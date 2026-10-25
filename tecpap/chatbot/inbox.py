"""File durable des messages WhatsApp reçus (table SQLite ``whatsapp_inbox``).

Twilio considère un message comme livré dès que le webhook lui répond 200 : il ne le renvoie
plus. Le webhook enregistre donc chaque message signé dans cette table avant de répondre, et le
traitement (assistant, extraction, réponse au client) part de la ligne enregistrée. Un message
dont le traitement a été interrompu (arrêt du conteneur, plantage du processus) reste en base :
il est repris au démarrage suivant et par la reprise périodique
(:func:`tecpap.web.routes.webhooks.resume_pending_messages`).

Statuts d'un message (:data:`STATUSES`) :

- ``pending`` : reçu, pas encore pris en charge (file d'attente pleine, ou reprise après un arrêt) ;
- ``processing`` : pris en charge par un fil d'exécution (:meth:`WhatsAppInbox.claim`, atomique :
  deux fils ne traitent jamais le même message) ;
- ``done`` : traité ;
- ``failed`` : traitement en erreur, ou abandonné après :data:`MAX_ATTEMPTS` prises en charge
  interrompues (le détail est dans ``last_error`` et dans le journal du serveur).

La table garde aussi la déduplication par identifiant Twilio : ``message_sid`` est unique, une
seconde livraison du même message n'est pas enregistrée (:meth:`WhatsAppInbox.add` renvoie
``None``). Les messages traités sont supprimés au bout de :data:`DONE_RETENTION_DAYS` jours
(:meth:`WhatsAppInbox.purge`) : le texte reste dans l'historique des conversations.

Les dates sont en temps universel coordonné (UTC), au format ``AAAA-MM-JJ HH:MM:SS``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DONE_RETENTION_DAYS",
    "FAILED_RETENTION_DAYS",
    "MAX_ATTEMPTS",
    "STATUSES",
    "InboxEntry",
    "WhatsAppInbox",
    "ensure_schema",
]

TABLE_NAME = "whatsapp_inbox"

STATUSES: tuple[str, ...] = ("pending", "processing", "done", "failed")
"""Statuts possibles d'un message de la file."""

MAX_ATTEMPTS = 3
"""Nombre maximal de prises en charge d'un message avant son abandon (statut ``failed``)."""

DONE_RETENTION_DAYS = 7
"""Durée de conservation d'un message traité, en jours."""

FAILED_RETENTION_DAYS = 30
"""Durée de conservation d'un message en échec, en jours (le temps d'examiner l'erreur)."""

MAX_ERROR_CHARS = 500
"""Longueur maximale du détail d'erreur enregistré."""

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_SCHEMA = (
    f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_sid TEXT UNIQUE,
        sender TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'processing', 'done', 'failed')),
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        received_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        processed_at TEXT
    )""",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_status ON {TABLE_NAME} (status, id)",
)
_COLUMNS = ("id", "message_sid", "sender", "payload_json", "status", "attempts", "last_error", "received_at",
            "updated_at", "processed_at")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée la table ``whatsapp_inbox`` et son index s'ils n'existent pas (appel idempotent)."""
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.commit()


@dataclass(frozen=True)
class InboxEntry:
    """Message de la file.

    Attributes:
        id: identifiant de la ligne.
        message_sid: identifiant Twilio du message, ou ``None``.
        sender: expéditeur tel que reçu de Twilio (« whatsapp:+2126… »).
        payload: paramètres du webhook signés par Twilio (corps, médias…).
        status: une des valeurs de :data:`STATUSES`.
        attempts: nombre de prises en charge.
        last_error: détail de la dernière erreur, ou ``None``.
        received_at, updated_at, processed_at: dates de réception, de dernière modification et de fin (UTC).
    """

    id: int
    message_sid: str | None
    sender: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    attempts: int = 0
    last_error: str | None = None
    received_at: str = ""
    updated_at: str = ""
    processed_at: str | None = None


def _entry(row: Mapping[str, Any]) -> InboxEntry:
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        logger.warning("Message %s de la file WhatsApp illisible", row["id"])
        payload = {}
    return InboxEntry(
        id=int(row["id"]), message_sid=row["message_sid"], sender=row["sender"],
        payload=payload if isinstance(payload, dict) else {}, status=row["status"], attempts=int(row["attempts"]),
        last_error=row["last_error"], received_at=row["received_at"], updated_at=row["updated_at"],
        processed_at=row["processed_at"],
    )


def _clean_sid(value: Any) -> str | None:
    sid = str(value or "").strip()[:64]
    return sid or None


class WhatsAppInbox:
    """Lecture et écriture de la file des messages WhatsApp reçus.

    Args:
        conn: connexion SQLite ; la table est créée si besoin.
        clock: fonction qui renvoie l'heure courante (par défaut, l'heure UTC du système).

    Les opérations sont protégées par un verrou ; la prise en charge (:meth:`claim`) est en
    outre atomique dans la base, quel que soit le nombre de connexions.
    """

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None):
        self.conn = conn
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        with self._lock:
            ensure_schema(conn)

    # ------------------------------------------------------------------ horloge

    def _now(self) -> datetime:
        moment = self._clock()
        return moment.astimezone(timezone.utc) if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)

    def _timestamp(self, delta: timedelta = timedelta(0)) -> str:
        return (self._now() - delta).strftime(_TIMESTAMP_FORMAT)

    # ------------------------------------------------------------------ écriture

    def add(self, payload: Mapping[str, Any]) -> InboxEntry | None:
        """Enregistre un message reçu (statut ``pending``) ; ``None`` si son ``MessageSid`` est déjà connu."""
        sid = _clean_sid(payload.get("MessageSid") or payload.get("SmsMessageSid"))
        sender = str(payload.get("From") or "").strip()
        body = json.dumps({str(key): value for key, value in payload.items()}, ensure_ascii=False, default=str)
        now = self._timestamp()
        with self._lock:
            try:
                cursor = self.conn.execute(
                    f"INSERT INTO {TABLE_NAME} (message_sid, sender, payload_json, status, attempts, received_at, "  # nosec B608
                    "updated_at) VALUES (?, ?, ?, 'pending', 0, ?, ?)",
                    (sid, sender, body, now, now),
                )
            except sqlite3.IntegrityError:
                self.conn.commit()
                logger.info("Message WhatsApp %s déjà dans la file : ignoré", sid)
                return None
            self.conn.commit()
            entry_id = cursor.lastrowid
        return InboxEntry(id=int(entry_id), message_sid=sid, sender=sender, payload=dict(payload),
                          received_at=now, updated_at=now)

    def claim(self, entry_id: int) -> InboxEntry | None:
        """Prend en charge un message ``pending`` (statut ``processing``) ; ``None`` s'il ne l'est plus."""
        with self._lock:
            cursor = self.conn.execute(
                f"UPDATE {TABLE_NAME} SET status = 'processing', attempts = attempts + 1, updated_at = ? "  # nosec B608
                "WHERE id = ? AND status = 'pending'",
                (self._timestamp(), int(entry_id)),
            )
            self.conn.commit()
            if cursor.rowcount != 1:
                return None
            return self.get(entry_id)

    def mark_done(self, entry_id: int, note: str | None = None) -> None:
        """Marque un message traité ; ``note`` garde un incident sans gravité (réponse non envoyée…)."""
        self._finish(entry_id, "done", note)

    def mark_failed(self, entry_id: int, error: str) -> None:
        """Marque un message en échec, avec le détail de l'erreur."""
        self._finish(entry_id, "failed", error)

    def _finish(self, entry_id: int, status: str, detail: str | None) -> None:
        now = self._timestamp()
        detail_text = str(detail)[:MAX_ERROR_CHARS] if detail else None
        with self._lock:
            self.conn.execute(
                f"UPDATE {TABLE_NAME} SET status = ?, last_error = ?, updated_at = ?, processed_at = ? "  # nosec B608
                "WHERE id = ?",
                (status, detail_text, now, now, int(entry_id)),
            )
            self.conn.commit()

    def requeue_interrupted(self, older_than_seconds: float = 0.0) -> tuple[int, int]:
        """Remet en attente les messages restés ``processing`` (traitement interrompu).

        Seuls les messages pris en charge depuis plus de ``older_than_seconds`` secondes sont
        concernés ; ceux qui ont déjà été pris en charge :data:`MAX_ATTEMPTS` fois sont abandonnés
        (``failed``) : un message qui interrompt le processus à chaque essai ne bloque pas la file.

        Returns:
            Le nombre de messages remis en attente et le nombre de messages abandonnés.
        """
        limit = self._timestamp(timedelta(seconds=max(0.0, float(older_than_seconds))))
        now = self._timestamp()
        with self._lock:
            abandoned = self.conn.execute(
                f"UPDATE {TABLE_NAME} SET status = 'failed', updated_at = ?, processed_at = ?, "  # nosec B608
                "last_error = 'Traitement interrompu à chaque essai : message abandonné' "
                "WHERE status = 'processing' AND updated_at <= ? AND attempts >= ?",
                (now, now, limit, MAX_ATTEMPTS),
            ).rowcount
            requeued = self.conn.execute(
                f"UPDATE {TABLE_NAME} SET status = 'pending', updated_at = ? "  # nosec B608
                "WHERE status = 'processing' AND updated_at <= ?",
                (now, limit),
            ).rowcount
            self.conn.commit()
        if abandoned:
            logger.error("%d message(s) WhatsApp abandonné(s) après %d essais interrompus", abandoned, MAX_ATTEMPTS)
        if requeued:
            logger.warning("%d message(s) WhatsApp au traitement interrompu remis en attente", requeued)
        return requeued, abandoned

    def purge(self, *, done_older_than_days: float = DONE_RETENTION_DAYS,
              failed_older_than_days: float = FAILED_RETENTION_DAYS) -> int:
        """Supprime les messages traités et les messages en échec plus anciens que leur durée de conservation."""
        done_limit = self._timestamp(timedelta(days=max(0.0, float(done_older_than_days))))
        failed_limit = self._timestamp(timedelta(days=max(0.0, float(failed_older_than_days))))
        with self._lock:
            deleted = self.conn.execute(
                f"DELETE FROM {TABLE_NAME} WHERE (status = 'done' AND updated_at < ?) "  # nosec B608
                "OR (status = 'failed' AND updated_at < ?)",
                (done_limit, failed_limit),
            ).rowcount
            self.conn.commit()
        if deleted:
            logger.info("%d message(s) WhatsApp ancien(s) supprimé(s) de la file", deleted)
        return deleted

    # ------------------------------------------------------------------ lecture

    def _select(self, where: str, parameters: tuple[Any, ...], limit: int | None = None) -> list[InboxEntry]:
        query = f"SELECT {', '.join(_COLUMNS)} FROM {TABLE_NAME} WHERE {where} ORDER BY id"  # nosec B608
        if limit is not None:
            query += " LIMIT ?"
            parameters = (*parameters, max(1, int(limit)))
        with self._lock:
            cursor = self.conn.execute(query, parameters)
            names = [column[0] for column in cursor.description]
            rows = [dict(zip(names, tuple(row), strict=True)) for row in cursor.fetchall()]
        return [_entry(row) for row in rows]

    def get(self, entry_id: int) -> InboxEntry | None:
        """Message d'identifiant ``entry_id``, ou ``None``."""
        entries = self._select("id = ?", (int(entry_id),))
        return entries[0] if entries else None

    def pending(self, limit: int = 100) -> list[InboxEntry]:
        """Messages en attente, du plus ancien au plus récent."""
        return self._select("status = 'pending'", (), limit)

    def next_pending_for(self, sender: str) -> InboxEntry | None:
        """Plus ancien message en attente d'un expéditeur, ou ``None``."""
        entries = self._select("status = 'pending' AND sender = ?", (sender,), 1)
        return entries[0] if entries else None

    def counts(self) -> dict[str, int]:
        """Nombre de messages par statut (tous les statuts de :data:`STATUSES` sont présents)."""
        counts = dict.fromkeys(STATUSES, 0)
        with self._lock:
            rows = self.conn.execute(f"SELECT status, COUNT(*) FROM {TABLE_NAME} GROUP BY status").fetchall()  # nosec B608
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts
