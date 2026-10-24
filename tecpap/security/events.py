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
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
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
        ensure_schema(conn)
        cursor = conn.execute(
            f"INSERT INTO {TABLE_NAME} (created_at, channel, sender, kind, score, details) VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("Impossible d'enregistrer l'événement de sécurité %s", row[3])
        return None
    logger.info("Événement de sécurité enregistré : %s (canal %s, score %d)", row[3], row[1], row[4])
    return cursor.lastrowid


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
