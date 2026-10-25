"""Journal des événements de sécurité (table SQLite ``security_events``).

Chaque événement enregistre le canal (« whatsapp », « email », « web »…), l'expéditeur masqué,
la nature de l'événement, un score et un court détail. Il alimente la page « Sécurité » du
tableau de bord et permet à l'équipe commerciale de repérer les tentatives répétées.

Protection des données enregistrées :

- l'expéditeur est stocké masqué (:func:`tecpap.security.redaction.mask_sender` : « +2126•••••678 »,
  « h•••@exemple.ma ») ; le journal permet de compter les tentatives sans conserver le numéro ;
- le détail est nettoyé (caractères de contrôle et invisibles retirés), les numéros de
  téléphone et adresses email qu'il contient sont masqués, puis il est tronqué à
  :data:`DETAILS_MAX_CHARS` caractères ;
- les dates sont en temps universel coordonné (UTC), au format ``AAAA-MM-JJ HH:MM:SS`` de
  ``CURRENT_TIMESTAMP`` dans SQLite.

Une erreur SQLite pendant l'enregistrement est journalisée et ne remonte pas : la réponse au
client ne doit pas échouer parce que le journal de sécurité est indisponible.

Volume du journal :

- :class:`EventThrottle` limite le nombre d'événements enregistrés pour une même source
  (adresse IP d'une requête mal signée, par exemple) et au total : une requête anonyme répétée
  en boucle n'écrit pas une ligne par requête ;
- :func:`purge_security_events` supprime les événements plus anciens que
  :data:`RETENTION_DAYS` jours et ne garde au plus que :data:`MAX_STORED_EVENTS` lignes.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from tecpap.security.redaction import mask_sender, redact_contacts
from tecpap.security.text import sanitize_text

logger = logging.getLogger(__name__)

TABLE_NAME = "security_events"

DETAILS_MAX_CHARS = 500
"""Longueur maximale du détail enregistré."""

LABEL_MAX_CHARS = 50
"""Longueur maximale du canal et de la nature de l'événement."""

MAX_LIMIT = 1000
"""Nombre maximal d'événements renvoyés par :func:`recent_security_events`."""

RETENTION_DAYS = 180
"""Durée de conservation des événements, en jours (:func:`purge_security_events`)."""

MAX_STORED_EVENTS = 100_000
"""Nombre maximal d'événements conservés : au-delà, les plus anciens sont supprimés."""

KIND_PROMPT_INJECTION = "prompt_injection"
"""Message reçu jugé suspect par :func:`tecpap.security.guardrails.inspect_input`."""

KIND_OUTPUT_BLOCKED = "output_blocked"
"""Réponse générée bloquée par :class:`tecpap.security.guardrails.OutputFilter`."""

KIND_RATE_LIMITED = "rate_limited"
"""Expéditeur qui dépasse le nombre de messages autorisé par minute."""

KIND_INVALID_SIGNATURE = "invalid_signature"
"""Requête de webhook dont la signature Twilio est invalide."""

_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        channel TEXT NOT NULL,
        sender TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        score INTEGER NOT NULL DEFAULT 0,
        details TEXT NOT NULL DEFAULT ''
    )""",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_created_at ON {TABLE_NAME} (created_at)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_kind ON {TABLE_NAME} (kind)",
)
_COLUMNS = ("id", "created_at", "channel", "sender", "kind", "score", "details")
_INSERT = f"INSERT INTO {TABLE_NAME} (created_at, channel, sender, kind, score, details) VALUES (?, ?, ?, ?, ?, ?)"
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée la table ``security_events`` et ses index s'ils n'existent pas (appel idempotent)."""
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.commit()


def _timestamp(moment: datetime | None) -> str:
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime(_TIMESTAMP_FORMAT)


def _label(value: object, default: str) -> str:
    text = sanitize_text(str(value) if value is not None else "", max_chars=None).replace("\n", " ")
    return text[:LABEL_MAX_CHARS] or default


def _details(details: str | Iterable[str] | None) -> str:
    if details is None:
        return ""
    if not isinstance(details, str):
        details = " ; ".join(str(item) for item in details)
    text = sanitize_text(details, max_chars=None)
    text = redact_contacts(text).text
    if len(text) > DETAILS_MAX_CHARS:
        text = text[:DETAILS_MAX_CHARS - 1].rstrip() + "…"
    return text


def _score(score: object) -> int:
    try:
        value = int(score)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, value))


def log_security_event(conn: sqlite3.Connection, *, channel: str, sender: str | None, kind: str,
                       score: int = 0, details: str | Iterable[str] | None = "",
                       created_at: datetime | None = None) -> int | None:
    """Enregistre un événement de sécurité ; renvoie son identifiant, ou ``None`` en cas d'erreur.

    Args:
        conn: connexion SQLite (la table est créée si besoin).
        channel: canal de réception (« whatsapp », « email », « web »).
        sender: numéro, adresse ou identifiant de l'expéditeur ; il est enregistré masqué.
        kind: nature de l'événement (voir les constantes ``KIND_*``).
        score: score de suspicion, ramené entre 0 et 100.
        details: détail lisible (chaîne, ou liste de raisons jointes par « ; »).
        created_at: date de l'événement (par défaut, l'heure courante) ; une date sans fuseau
            horaire est considérée comme exprimée en UTC.
    """
    row = (
        _timestamp(created_at),
        _label(channel, "inconnu"),
        mask_sender(sender),
        _label(kind, "inconnu"),
        _score(score),
        _details(details),
    )
    try:
        try:
            cursor = conn.execute(_INSERT, row)
        except sqlite3.OperationalError as exc:
            # Table absente (base antérieure au journal de sécurité, base de test vierge) : elle est
            # créée puis l'insertion est refaite ; le schéma n'est pas revérifié à chaque événement.
            if "no such table" not in str(exc):
                raise
            ensure_schema(conn)
            cursor = conn.execute(_INSERT, row)
        conn.commit()
    except sqlite3.Error:
        logger.exception("Impossible d'enregistrer l'événement de sécurité %s", row[3])
        return None
    logger.info("Événement de sécurité enregistré : %s (canal %s, score %d)", row[3], row[1], row[4])
    return cursor.lastrowid


def purge_security_events(conn: sqlite3.Connection, *, older_than_days: float = RETENTION_DAYS,
                          max_rows: int = MAX_STORED_EVENTS, now: datetime | None = None) -> int:
    """Supprime les événements de plus de ``older_than_days`` jours, puis les plus anciens au-delà de ``max_rows``.

    Args:
        conn: connexion SQLite.
        older_than_days: âge maximal d'un événement conservé, en jours.
        max_rows: nombre maximal d'événements conservés.
        now: heure de référence (par défaut, l'heure courante) ; sans fuseau horaire, elle est en UTC.

    Returns:
        Le nombre d'événements supprimés (0 si la table n'existe pas ou en cas d'erreur, journalisée).
    """
    limit = _timestamp((now or datetime.now(timezone.utc)) - timedelta(days=max(0.0, float(older_than_days))))
    try:
        deleted = conn.execute(f"DELETE FROM {TABLE_NAME} WHERE created_at < ?", (limit,)).rowcount  # nosec B608
        deleted += conn.execute(
            f"DELETE FROM {TABLE_NAME} WHERE id NOT IN "  # nosec B608
            f"(SELECT id FROM {TABLE_NAME} ORDER BY id DESC LIMIT ?)",
            (max(0, int(max_rows)),),
        ).rowcount
        conn.commit()
    except sqlite3.OperationalError:
        logger.debug("Table %s absente : aucun événement à supprimer", TABLE_NAME)
        return 0
    except sqlite3.Error:
        logger.exception("Purge du journal des événements de sécurité impossible")
        return 0
    if deleted:
        logger.info("%d ancien(s) événement(s) de sécurité supprimé(s)", deleted)
    return deleted


@dataclass(frozen=True)
class ThrottleDecision:
    """Décision de :meth:`EventThrottle.check`.

    Attributes:
        record: vrai quand l'événement doit être enregistré.
        suppressed: nombre d'événements de la même source écartés depuis le dernier enregistré
            (à mentionner dans le détail de celui-ci).
    """

    record: bool
    suppressed: int = 0


class EventThrottle:
    """Limite le nombre d'événements enregistrés, par source et au total.

    Une source (adresse IP, par exemple) obtient au plus un événement enregistré par fenêtre de
    ``window_seconds`` secondes, et toutes sources confondues au plus ``max_per_window`` : une
    rafale de requêtes depuis des adresses toujours nouvelles reste bornée. Les événements écartés
    sont comptés et annoncés par le suivant de la même source. Les méthodes peuvent être appelées
    depuis plusieurs fils d'exécution.

    Args:
        window_seconds: durée de la fenêtre, en secondes.
        max_per_window: nombre maximal d'événements enregistrés par fenêtre, toutes sources confondues.
        clock: horloge en secondes (``time.monotonic`` par défaut, remplaçable dans les tests).
    """

    # Au-delà de ce nombre de sources suivies, celles dont la fenêtre est terminée sont oubliées.
    PURGE_THRESHOLD = 1000

    def __init__(self, window_seconds: float = 60.0, max_per_window: int = 30,
                 clock: Callable[[], float] = time.monotonic):
        if window_seconds <= 0 or max_per_window < 1:
            raise ValueError("La fenêtre et le nombre d'événements doivent être strictement positifs")
        self.window_seconds = float(window_seconds)
        self.max_per_window = max_per_window
        self._clock = clock
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}
        self._recorded: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, source: str) -> ThrottleDecision:
        """Indique si l'événement de ``source`` doit être enregistré maintenant."""
        with self._lock:
            now = self._clock()
            self._forget(now)
            last = self._last.get(source)
            if (last is not None and now - last < self.window_seconds) or len(self._recorded) >= self.max_per_window:
                self._suppressed[source] = self._suppressed.get(source, 0) + 1
                return ThrottleDecision(record=False)
            self._last[source] = now
            self._recorded.append(now)
            return ThrottleDecision(record=True, suppressed=self._suppressed.pop(source, 0))

    def _forget(self, now: float) -> None:
        while self._recorded and now - self._recorded[0] >= self.window_seconds:
            self._recorded.popleft()
        if len(self._last) + len(self._suppressed) < self.PURGE_THRESHOLD:
            return
        self._last = {source: moment for source, moment in self._last.items() if now - moment < self.window_seconds}
        # Les comptes des sources oubliées sont abandonnés : le journal n'a besoin que d'un ordre de grandeur.
        self._suppressed = {source: count for source, count in self._suppressed.items() if source in self._last}


def recent_security_events(conn: sqlite3.Connection, limit: int = 50, *, kind: str | None = None,
                           channel: str | None = None) -> list[dict[str, Any]]:
    """Derniers événements, du plus récent au plus ancien, sous forme de dictionnaires.

    Chaque dictionnaire a les clés ``id``, ``created_at``, ``channel``, ``sender``, ``kind``,
    ``score`` et ``details``. ``limit`` est ramené entre 1 et :data:`MAX_LIMIT`. La liste est
    vide si la table n'existe pas encore.
    """
    limit = max(1, min(MAX_LIMIT, int(limit)))
    clauses, parameters = [], []
    if kind is not None:
        clauses.append("kind = ?")
        parameters.append(kind)
    if channel is not None:
        clauses.append("channel = ?")
        parameters.append(channel)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT {', '.join(_COLUMNS)} FROM {TABLE_NAME}{where} ORDER BY id DESC LIMIT ?"  # nosec B608
    try:
        rows = conn.execute(query, (*parameters, limit)).fetchall()
    except sqlite3.OperationalError:
        logger.debug("Table %s absente : aucun événement de sécurité", TABLE_NAME)
        return []
    return [dict(zip(_COLUMNS, tuple(row), strict=True)) for row in rows]


def count_security_events(conn: sqlite3.Connection, *, kind: str | None = None,
                          since: datetime | None = None) -> int:
    """Nombre d'événements, éventuellement limités à une nature et à une date de début.

    Une date ``since`` sans fuseau horaire est considérée comme exprimée en UTC.
    """
    clauses, parameters = [], []
    if kind is not None:
        clauses.append("kind = ?")
        parameters.append(kind)
    if since is not None:
        clauses.append("created_at >= ?")
        parameters.append(_timestamp(since))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}{where}", parameters).fetchone()  # nosec B608
    except sqlite3.OperationalError:
        return 0
    return int(row[0])
