"""Conversations WhatsApp : état de chaque expéditeur et historique des messages (SQLite).

Deux tables :

- ``conversations`` : une ligne par numéro d'expéditeur (forme internationale « +212… »), avec
  l'état de l'assistant (:data:`STATES`), la langue de réponse retenue, la commande en cours de
  saisie (brouillon, en JSON), la dernière commande enregistrée et l'indicateur de transfert à
  l'équipe commerciale (``handoff``) ;
- ``chat_messages`` : chaque message reçu (``in``) ou envoyé (``out``), avec l'intention
  reconnue, l'identifiant Twilio du message (``message_sid``, unique : un message livré deux
  fois par Twilio n'est traité qu'une fois) et l'indicateur ``blocked`` (message bloqué par les
  contrôles de sécurité).

États :

- ``idle`` : aucune commande en cours ;
- ``collecting`` : commande en cours, des champs obligatoires manquent ;
- ``awaiting_confirmation`` : récapitulatif envoyé, l'assistant attend l'accord explicite du client ;
- ``handoff`` : conversation transférée à l'équipe commerciale ; l'assistant ne fait plus
  qu'accuser réception jusqu'à la remise à zéro depuis le tableau de bord (:meth:`ConversationStore.reset`).

Un brouillon non confirmé expire au bout de :data:`DRAFT_TTL_HOURS` heures sans modification : il
est supprimé à la lecture suivante de la conversation (:meth:`ConversationStore.get_or_create`)
ou par :meth:`ConversationStore.reset_after`.

Les dates sont en temps universel coordonné (UTC), au format ``AAAA-MM-JJ HH:MM:SS``, ce qui
permet de les comparer comme des chaînes. Une horloge peut être fournie (tests).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from tecpap.extraction.rules import normalize_phone

logger = logging.getLogger(__name__)

__all__ = [
    "DIRECTIONS",
    "DRAFT_TTL_HOURS",
    "STATES",
    "Conversation",
    "ConversationStore",
    "conversation_key",
    "ensure_schema",
]

STATES: tuple[str, ...] = ("idle", "collecting", "awaiting_confirmation", "handoff")
"""États possibles d'une conversation."""

DIRECTIONS: tuple[str, ...] = ("in", "out")
"""Sens d'un message : reçu du client (``in``) ou envoyé par l'assistant (``out``)."""

DRAFT_TTL_HOURS = 24
"""Durée de vie, en heures, d'une commande en cours qui n'est ni modifiée ni confirmée."""

MAX_BODY_CHARS = 4000
"""Longueur maximale d'un message enregistré."""

MAX_LIMIT = 500
"""Nombre maximal de lignes renvoyées par les méthodes de consultation."""

PREVIEW_CHARS = 120
"""Longueur de l'aperçu du dernier message dans :meth:`ConversationStore.list_conversations`."""

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL DEFAULT 'idle',
        language TEXT,
        draft_json TEXT,
        last_order_id INTEGER,
        handoff INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id),
        direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
        body TEXT NOT NULL DEFAULT '',
        intent TEXT,
        message_sid TEXT UNIQUE,
        blocked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages (conversation_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations (updated_at)",
)

_CONVERSATION_COLUMNS = ("id", "phone", "state", "language", "draft_json", "last_order_id", "handoff",
                         "created_at", "updated_at")
_MESSAGE_COLUMNS = ("id", "conversation_id", "direction", "body", "intent", "message_sid", "blocked", "created_at")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée les tables ``conversations`` et ``chat_messages`` et leurs index s'ils n'existent pas (idempotent)."""
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.commit()


def conversation_key(phone: str | None) -> str:
    """Clé d'une conversation : numéro au format international (« whatsapp:+2126… » → « +2126… »).

    Un identifiant qui n'est pas un numéro reconnu est gardé sans le préfixe « whatsapp: » ni
    espaces, en minuscules.

    Raises:
        ValueError: l'identifiant est vide.
    """
    if not isinstance(phone, str) or not phone.strip():
        raise ValueError("numéro d'expéditeur vide")
    canonical = normalize_phone(phone)
    if canonical:
        return canonical
    raw = phone.strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw[len("whatsapp:"):]
    key = "".join(raw.split()).lower()[:64]
    if not key:
        raise ValueError("numéro d'expéditeur vide")
    return key


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc) if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _format(moment: datetime) -> str:
    return _utc(moment).strftime(_TIMESTAMP_FORMAT)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value[:19], _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class Conversation:
    """État d'une conversation, lu par :meth:`ConversationStore.get_or_create`.

    Attributes:
        id: identifiant de la ligne.
        phone: numéro de l'expéditeur (clé de la conversation).
        state: une des valeurs de :data:`STATES`.
        language: langue de réponse retenue (``fr``, ``darija``, ``ar``), ou ``None`` tant
            qu'aucune langue claire n'a été reconnue.
        draft: commande en cours (dictionnaire sérialisable en JSON), ou ``None``.
        last_order_id: identifiant de la dernière commande enregistrée par l'assistant.
        handoff: vrai quand la conversation est transférée à l'équipe commerciale.
        created_at, updated_at: dates de création et de dernière modification (UTC).
        draft_expired: vrai quand un brouillon expiré a été supprimé à la lecture (non enregistré).
    """

    id: int
    phone: str
    state: str = "idle"
    language: str | None = None
    draft: dict[str, Any] | None = None
    last_order_id: int | None = None
    handoff: bool = False
    created_at: str = ""
    updated_at: str = ""
    draft_expired: bool = field(default=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Représentation pour le tableau de bord (le brouillon est inclus tel quel)."""
        return {
            "id": self.id, "phone": self.phone, "state": self.state, "language": self.language,
            "draft": self.draft, "last_order_id": self.last_order_id, "handoff": self.handoff,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


def _decode_draft(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Brouillon de commande illisible : supprimé")
        return None
    return value if isinstance(value, dict) else None


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """Lignes d'un curseur sous forme de dictionnaires, quelle que soit la ``row_factory`` de la connexion."""
    names = [column[0] for column in cursor.description or ()]
    return [dict(zip(names, tuple(row), strict=True)) for row in cursor.fetchall()]


def _clamp(limit: Any, default: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(MAX_LIMIT, value))


class ConversationStore:
    """Lecture et écriture des conversations et des messages.

    Args:
        conn: connexion SQLite ; les tables sont créées si besoin.
        clock: fonction qui renvoie l'heure courante (par défaut, l'heure UTC du système).
        draft_ttl_hours: durée de vie d'un brouillon de commande, en heures.

    Toutes les opérations sur la connexion sont protégées par un verrou : le magasin peut être
    partagé par les fils d'exécution qui traitent les messages.
    """

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime] | None = None,
                 draft_ttl_hours: float = DRAFT_TTL_HOURS):
        if draft_ttl_hours <= 0:
            raise ValueError("draft_ttl_hours doit être strictement positif")
        self.conn = conn
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.draft_ttl = timedelta(hours=draft_ttl_hours)
        self._lock = threading.RLock()
        with self._lock:
            ensure_schema(conn)

    # ------------------------------------------------------------------ horloge

    def now(self) -> datetime:
        """Heure courante, en UTC."""
        return _utc(self._clock())

    def timestamp(self) -> str:
        """Heure courante au format enregistré (« AAAA-MM-JJ HH:MM:SS », UTC)."""
        return _format(self.now())

    # ------------------------------------------------------------------ conversations

    def _conversation(self, row: dict[str, Any]) -> Conversation:
        state = row["state"] if row["state"] in STATES else "idle"
        return Conversation(
            id=int(row["id"]), phone=row["phone"], state=state, language=row["language"],
            draft=_decode_draft(row["draft_json"]), last_order_id=row["last_order_id"],
            handoff=bool(row["handoff"]), created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def _select(self, where: str, parameters: tuple[Any, ...]) -> Conversation | None:
        cursor = self.conn.execute(
            f"SELECT {', '.join(_CONVERSATION_COLUMNS)} FROM conversations WHERE {where}", parameters,  # nosec B608
        )
        rows = _rows(cursor)
        return self._conversation(rows[0]) if rows else None

    def find(self, phone: str) -> Conversation | None:
        """Conversation d'un numéro, sans la créer ; ``None`` si elle n'existe pas."""
        key = conversation_key(phone)
        with self._lock:
            return self._select("phone = ?", (key,))

    def get(self, conversation_id: int) -> Conversation | None:
        """Conversation d'identifiant ``conversation_id``, ou ``None``."""
        with self._lock:
            return self._select("id = ?", (int(conversation_id),))

    def get_or_create(self, phone: str) -> Conversation:
        """Conversation d'un numéro, créée à l'état ``idle`` si besoin.

        Un brouillon plus ancien que la durée de vie configurée est supprimé : la conversation
        revient à ``idle`` (sauf transfert en cours) et ``draft_expired`` vaut vrai.
        """
        key = conversation_key(phone)
        with self._lock:
            conversation = self._select("phone = ?", (key,))
            if conversation is None:
                now = self.timestamp()
                self.conn.execute(
                    "INSERT OR IGNORE INTO conversations (phone, state, handoff, created_at, updated_at) "
                    "VALUES (?, 'idle', 0, ?, ?)",
                    (key, now, now),
                )
                self.conn.commit()
                conversation = self._select("phone = ?", (key,))
                logger.info("Conversation WhatsApp créée (id %s)", conversation.id)
            if self._is_expired(conversation.draft):
                self._drop_draft(conversation)
                conversation.draft_expired = True
                self.save(conversation)
                logger.info("Commande en cours expirée (conversation %s)", conversation.id)
            return conversation

    def _is_expired(self, draft: dict[str, Any] | None) -> bool:
        if not draft:
            return False
        updated = _parse(draft.get("updated_at"))
        return updated is None or self.now() - updated > self.draft_ttl

    @staticmethod
    def _drop_draft(conversation: Conversation) -> None:
        conversation.draft = None
        if conversation.state in ("collecting", "awaiting_confirmation"):
            conversation.state = "idle"

    def save(self, conversation: Conversation) -> None:
        """Enregistre l'état, la langue, le brouillon, la dernière commande et le transfert.

        Raises:
            ValueError: état inconnu.
        """
        if conversation.state not in STATES:
            raise ValueError(f"état de conversation inconnu : {conversation.state!r}")
        draft = json.dumps(conversation.draft, ensure_ascii=False) if conversation.draft else None
        now = self.timestamp()
        with self._lock:
            self.conn.execute(
                "UPDATE conversations SET state = ?, language = ?, draft_json = ?, last_order_id = ?, "
                "handoff = ?, updated_at = ? WHERE id = ?",
                (conversation.state, conversation.language, draft, conversation.last_order_id,
                 int(bool(conversation.handoff)), now, conversation.id),
            )
            self.conn.commit()
        conversation.updated_at = now

    def reset(self, phone: str) -> bool:
        """Remet une conversation à zéro (état ``idle``, sans transfert ni brouillon) ; faux si elle n'existe pas.

        Utilisé par l'équipe commerciale depuis le tableau de bord, après avoir répondu au client.
        """
        conversation = self.find(phone)
        if conversation is None:
            return False
        conversation.state = "idle"
        conversation.handoff = False
        conversation.draft = None
        self.save(conversation)
        logger.info("Conversation %s remise à zéro", conversation.id)
        return True

    def reset_after(self, hours: float | None = None) -> int:
        """Supprime les brouillons plus anciens que ``hours`` heures (durée de vie configurée par défaut).

        Returns:
            Le nombre de conversations dont le brouillon a été supprimé.
        """
        limit = timedelta(hours=hours) if hours is not None else self.draft_ttl
        now = self.now()
        dropped = 0
        with self._lock:
            cursor = self.conn.execute(
                f"SELECT {', '.join(_CONVERSATION_COLUMNS)} FROM conversations "  # nosec B608
                "WHERE draft_json IS NOT NULL"
            )
            for row in _rows(cursor):
                conversation = self._conversation(row)
                updated = _parse((conversation.draft or {}).get("updated_at"))
                if updated is not None and now - updated <= limit:
                    continue
                self._drop_draft(conversation)
                self.save(conversation)
                dropped += 1
        if dropped:
            logger.info("%d commande(s) en cours expirée(s)", dropped)
        return dropped

    # ------------------------------------------------------------------ messages

    def add_message(self, conversation_id: int, direction: str, body: str | None, *, intent: str | None = None,
                    message_sid: str | None = None, blocked: bool = False) -> int | None:
        """Enregistre un message ; renvoie son identifiant, ou ``None`` si ``message_sid`` est déjà connu.

        Raises:
            ValueError: sens inconnu.
        """
        if direction not in DIRECTIONS:
            raise ValueError(f"sens de message inconnu : {direction!r}")
        sid = message_sid.strip()[:64] if isinstance(message_sid, str) and message_sid.strip() else None
        text = (body or "")[:MAX_BODY_CHARS]
        with self._lock:
            try:
                cursor = self.conn.execute(
                    "INSERT INTO chat_messages (conversation_id, direction, body, intent, message_sid, blocked, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(conversation_id), direction, text, intent, sid, int(bool(blocked)), self.timestamp()),
                )
            except sqlite3.IntegrityError:
                # L'instruction refusée est annulée par SQLite ; la validation termine la transaction ouverte.
                self.conn.commit()
                logger.info("Message %s déjà enregistré : ignoré", sid)
                return None
            self.conn.commit()
            return cursor.lastrowid

    def update_message(self, message_id: int, *, intent: str | None = None, blocked: bool | None = None) -> None:
        """Complète un message enregistré : intention reconnue, indicateur de blocage."""
        assignments, parameters = [], []
        if intent is not None:
            assignments.append("intent = ?")
            parameters.append(intent)
        if blocked is not None:
            assignments.append("blocked = ?")
            parameters.append(int(bool(blocked)))
        if not assignments:
            return
        with self._lock:
            self.conn.execute(
                f"UPDATE chat_messages SET {', '.join(assignments)} WHERE id = ?",  # nosec B608
                (*parameters, int(message_id)),
            )
            self.conn.commit()

    def is_duplicate(self, message_sid: str | None) -> bool:
        """Vrai si un message de même identifiant Twilio est déjà enregistré."""
        if not isinstance(message_sid, str) or not message_sid.strip():
            return False
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM chat_messages WHERE message_sid = ?", (message_sid.strip()[:64],),
            ).fetchone()
        return row is not None

    def recent_messages(self, conversation_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Les ``limit`` derniers messages d'une conversation, du plus ancien au plus récent."""
        limit = _clamp(limit, 10)
        with self._lock:
            cursor = self.conn.execute(
                f"SELECT {', '.join(_MESSAGE_COLUMNS)} FROM chat_messages WHERE conversation_id = ? "  # nosec B608
                "ORDER BY id DESC LIMIT ?",
                (int(conversation_id), limit),
            )
            rows = _rows(cursor)
        for row in rows:
            row["blocked"] = bool(row["blocked"])
        return list(reversed(rows))

    def count_recent_inbound(self, phone: str, seconds: float) -> int:
        """Nombre de messages reçus d'un numéro pendant les ``seconds`` dernières secondes (limite de débit)."""
        since = _format(self.now() - timedelta(seconds=max(0.0, float(seconds))))
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM chat_messages m JOIN conversations c ON c.id = m.conversation_id "
                "WHERE c.phone = ? AND m.direction = 'in' AND m.created_at >= ?",
                (conversation_key(phone), since),
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------ tableau de bord

    def list_conversations(self, limit: int = 50, *, handoff_only: bool = False) -> list[dict[str, Any]]:
        """Conversations, des plus récemment actives aux plus anciennes, avec un aperçu du dernier message.

        Chaque dictionnaire contient les colonnes de ``conversations`` (``handoff`` en booléen,
        ``has_draft`` à la place du brouillon), ``message_count``, ``blocked_count``,
        ``last_message`` (aperçu de :data:`PREVIEW_CHARS` caractères), ``last_direction`` et
        ``last_message_at``.
        """
        limit = _clamp(limit, 50)
        where = "WHERE c.handoff = 1" if handoff_only else ""
        query = f"""
            SELECT c.id, c.phone, c.state, c.language, c.draft_json IS NOT NULL AS has_draft, c.last_order_id,
                   c.handoff, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM chat_messages m WHERE m.conversation_id = c.id) AS message_count,
                   (SELECT COUNT(*) FROM chat_messages m WHERE m.conversation_id = c.id AND m.blocked = 1)
                       AS blocked_count,
                   last.body AS last_message, last.direction AS last_direction, last.created_at AS last_message_at
            FROM conversations c
            LEFT JOIN chat_messages last ON last.id = (
                SELECT MAX(m.id) FROM chat_messages m WHERE m.conversation_id = c.id)
            {where}
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT ?
        """  # nosec B608
        with self._lock:
            rows = _rows(self.conn.execute(query, (limit,)))
        for row in rows:
            row["handoff"] = bool(row["handoff"])
            row["has_draft"] = bool(row["has_draft"])
            preview = " ".join((row["last_message"] or "").split())
            row["last_message"] = preview if len(preview) <= PREVIEW_CHARS else preview[:PREVIEW_CHARS - 1] + "…"
        return rows

    def get_thread(self, conversation_id: int, limit: int = 100) -> dict[str, Any] | None:
        """Conversation et ses ``limit`` derniers messages (du plus ancien au plus récent), ou ``None``."""
        conversation = self.get(conversation_id)
        if conversation is None:
            return None
        return {"conversation": conversation.to_dict(),
                "messages": self.recent_messages(conversation.id, _clamp(limit, 100))}
