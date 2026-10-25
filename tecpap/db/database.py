"""Accès à la base SQLite : clients, produits, commandes et journal des actions.

La table ``commandes`` suit le format des commandes SAGE X3 (numéro, ligne, site de vente,
code client, code article, quantités livrées et facturées…) pour que l'export vers l'ERP soit
direct. Les noms de colonnes sont en français, comme dans l'ERP.

Chaque commande garde aussi la trace de son extraction, pour l'audit et la validation par
l'équipe commerciale (:data:`ORDER_AUDIT_FIELDS`) : identifiant du message reçu
(``message_sid`` : identifiant Twilio d'un message WhatsApp ou identifiant d'un email), texte
analysé (``message_brut``), langue détectée, modèle de langue utilisé et raison de ce choix,
anomalies et champs obligatoires manquants (listes enregistrées en JSON). Ces colonnes ne sont
jamais modifiables depuis le tableau de bord.

Dédoublonnage : une commande n'est rattachée à une commande existante que par l'identifiant du
message reçu (``message_sid``, à défaut ``email_id``), jamais par son numéro de commande ni par
son client. Un numéro de commande déjà attribué, ou un code client indiqué dans le message qui
diffère de celui du client, est noté dans les anomalies pour l'équipe commerciale (voir
:meth:`DatabaseManager.save_order`).

Horodatages : toutes les dates et heures écrites dans la base sont en UTC, au format
« AAAA-MM-JJ HH:MM:SS » de ``CURRENT_TIMESTAMP`` de SQLite (:data:`SQLITE_TIMESTAMP_FORMAT`) ;
la conversion en heure de Casablanca se fait à l'affichage.

Fils d'exécution : une connexion SQLite n'est jamais partagée entre deux fils. Chaque fil
(requête du serveur web, tâche de fond) obtient sa propre connexion, ouverte à la première
utilisation (voir :class:`DatabaseManager`).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import weakref
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tecpap.catalog import PRODUCTS
from tecpap.config import load_settings
from tecpap.formatting import parse_timestamp, resolve_timezone

logger = logging.getLogger(__name__)

DATABASE_FILE = "orders.db"
"""Nom de fichier par défaut de la base ; le chemin effectif vient de ``Settings.database_path``."""

PRODUCT_CATALOG: list[dict[str, Any]] = [
    {"id": product["id"], "type": product["type"], "description": product["description"]}
    for product in PRODUCTS
]
"""Lignes insérées dans la table ``produits`` à l'initialisation (construites depuis tecpap.catalog)."""

# Préfixes des noms de clients provisoires, attribués quand le nom réel est inconnu.
GENERIC_CLIENT_PREFIXES = ("Client WhatsApp", "Client Inconnu")

# Colonnes ajoutées aux bases créées par des versions antérieures du schéma.
_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source", "TEXT DEFAULT 'email'"),
    ("whatsapp_from", "TEXT"),
    ("motif_rejet", "TEXT"),
    ("ligne_commande", "INTEGER DEFAULT 1"),
    ("site_vente", "TEXT DEFAULT 'SXP'"),
    ("code_client", "TEXT"),
    ("code_article", "TEXT"),
    ("quantite_livree", "REAL DEFAULT 0"),
    ("reste_a_livrer", "REAL"),
    ("quantite_facturee", "REAL DEFAULT 0"),
    ("commercial", "TEXT DEFAULT 'DIVERS'"),
    ("type_sac", "TEXT"),
    ("format_sac", "TEXT"),
    ("type_papier", "TEXT"),
    ("grammage", "INTEGER"),
    ("laize", "INTEGER"),
    ("impression_client", "TEXT"),
    ("updated_at", "TIMESTAMP"),
    ("message_sid", "TEXT"),
    ("message_brut", "TEXT"),
    ("langue", "TEXT"),
    ("modele_llm", "TEXT"),
    ("routage_modele", "TEXT"),
    ("anomalies", "TEXT"),
    ("champs_manquants", "TEXT"),
)

ORDER_AUDIT_FIELDS: tuple[str, ...] = (
    "message_sid", "message_brut", "langue", "modele_llm", "routage_modele", "anomalies", "champs_manquants",
)
"""Colonnes de traçabilité de l'extraction, écrites à la création de la commande et jamais modifiées.

``anomalies`` et ``champs_manquants`` sont des listes de textes enregistrées en JSON ;
:meth:`DatabaseManager.get_order` et :meth:`DatabaseManager.get_all_orders` les renvoient aussi
décodées (``anomalies_list``, ``champs_manquants_list``).
"""

MESSAGE_BRUT_MAX_CHARS = 20_000
"""Longueur maximale du texte analysé conservé avec une commande (colonne ``message_brut``)."""

SQLITE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
"""Format des horodatages de la base (celui de ``CURRENT_TIMESTAMP`` de SQLite), toujours en UTC."""

_MESSAGE_SID_MAX_CHARS = 200
_REFERENCE_MAX_CHARS = 100
_CLIENT_CODE_PREFIX = "CL"
_CREATE_MESSAGE_SID_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_commandes_message_sid ON commandes(message_sid) "
    "WHERE message_sid IS NOT NULL"
)

_CREATE_CLIENTS = """
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        email TEXT,
        telephone TEXT,
        adresse TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

_CREATE_PRODUITS = """
    CREATE TABLE IF NOT EXISTS produits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL UNIQUE,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

_CREATE_COMMANDES = """
    CREATE TABLE IF NOT EXISTS commandes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_commande TEXT UNIQUE,
        ligne_commande INTEGER DEFAULT 1,
        site_vente TEXT DEFAULT 'SXP',
        client_id INTEGER,
        code_client TEXT,
        produit_id INTEGER,
        code_article TEXT,
        nature_produit TEXT,
        quantite REAL,
        unite TEXT DEFAULT 'US',
        quantite_livree REAL DEFAULT 0,
        reste_a_livrer REAL,
        quantite_facturee REAL DEFAULT 0,
        prix_unitaire REAL,
        prix_total REAL,
        devise TEXT DEFAULT 'MAD',
        date_commande DATE,
        date_livraison DATE,
        commercial TEXT DEFAULT 'DIVERS',
        type_sac TEXT,
        format_sac TEXT,
        type_papier TEXT,
        grammage INTEGER,
        laize INTEGER,
        impression_client TEXT,
        informations_supplementaires TEXT,
        confiance INTEGER,
        statut TEXT DEFAULT 'en_attente',
        source TEXT DEFAULT 'email',
        email_id TEXT,
        email_subject TEXT,
        email_from TEXT,
        whatsapp_from TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        validated_at TIMESTAMP,
        validated_by TEXT,
        motif_rejet TEXT,
        updated_at TIMESTAMP,
        message_sid TEXT,
        message_brut TEXT,
        langue TEXT,
        modele_llm TEXT,
        routage_modele TEXT,
        anomalies TEXT,
        champs_manquants TEXT,
        FOREIGN KEY (client_id) REFERENCES clients(id),
        FOREIGN KEY (produit_id) REFERENCES produits(id)
    )
"""

_CREATE_LOGS = """
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        table_name TEXT,
        record_id INTEGER,
        details TEXT,
        user_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

# Position de lecture de chaque boîte IMAP : dernier UID examiné pour une valeur de UIDVALIDITY.
_CREATE_MAILBOX_STATE = """
    CREATE TABLE IF NOT EXISTS email_mailbox_state (
        mailbox TEXT PRIMARY KEY,
        uidvalidity INTEGER NOT NULL,
        last_uid INTEGER NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

_ORDER_COLUMNS = (
    "numero_commande", "ligne_commande", "site_vente", "client_id", "code_client",
    "produit_id", "code_article", "nature_produit",
    "quantite", "unite", "quantite_livree", "reste_a_livrer", "quantite_facturee",
    "prix_unitaire", "prix_total", "devise",
    "date_commande", "date_livraison", "commercial",
    "type_sac", "format_sac", "type_papier", "grammage", "laize", "impression_client",
    "informations_supplementaires", "confiance", "source",
    "email_id", "email_subject", "email_from", "whatsapp_from",
    *ORDER_AUDIT_FIELDS,
)

ORDER_STATUSES: tuple[str, ...] = ("en_attente", "validee", "rejetee", "supprimee")
"""Statuts possibles d'une commande (colonne ``statut``)."""

ORDER_UPDATABLE_FIELDS: tuple[str, ...] = (
    "numero_commande", "ligne_commande", "site_vente", "code_client",
    "produit_id", "code_article", "nature_produit",
    "quantite", "unite", "quantite_livree", "reste_a_livrer", "quantite_facturee",
    "prix_unitaire", "prix_total", "devise",
    "date_commande", "date_livraison", "commercial",
    "type_sac", "format_sac", "type_papier", "grammage", "laize", "impression_client",
    "informations_supplementaires", "motif_rejet",
)
"""Colonnes de ``commandes`` modifiables par :meth:`DatabaseManager.update_order`.

Ce sont les données métier et SAGE X3 qu'un commercial peut corriger. L'identifiant, le client,
le statut, la confiance de l'extraction, la source, les traces de réception (email, WhatsApp),
la traçabilité de l'extraction (:data:`ORDER_AUDIT_FIELDS`) et les dates de création ou de
validation n'en font pas partie : elles ne changent que par les méthodes dédiées
(:meth:`DatabaseManager.update_order_status`, par exemple).
"""

_UPDATABLE_FIELD_SET = frozenset(ORDER_UPDATABLE_FIELDS)
# Colonnes numériques modifiables : INTEGER (valeur entière) et REAL (valeur décimale).
_INTEGER_FIELDS = frozenset({"ligne_commande", "produit_id", "grammage", "laize"})
_REAL_FIELDS = frozenset({
    "quantite", "quantite_livree", "reste_a_livrer", "quantite_facturee", "prix_unitaire", "prix_total",
})
# Plus grand entier qu'une colonne INTEGER de SQLite peut stocker (entier signé sur 64 bits).
_SQLITE_MAX_INTEGER = 2**63 - 1


class OrderUpdateError(ValueError):
    """Modification de commande refusée : champ non modifiable ou valeur invalide.

    Attributes:
        message: explication en français, destinée à l'utilisateur du tableau de bord.
        fields: noms des champs refusés, triés par ordre alphabétique.
    """

    def __init__(self, message: str, fields: Iterable[str]):
        super().__init__(message)
        self.message = message
        self.fields: tuple[str, ...] = tuple(fields)


def _to_number(value: Any, *, integer: bool) -> int | float | None:
    """Convertit une valeur numérique ; une chaîne (champ de formulaire HTML) est acceptée.

    Raises:
        TypeError, ValueError: la valeur n'est pas un nombre fini (ou pas un entier si ``integer``).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("booléen refusé")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        number: int | float = float(text)
    elif isinstance(value, (int, float)):
        number = value
    else:
        raise TypeError(f"type {type(value).__name__} refusé")
    if not math.isfinite(number):
        raise ValueError("nombre infini ou indéfini")
    if not integer:
        return float(number)
    if isinstance(number, float) and not number.is_integer():
        raise ValueError("nombre entier attendu")
    if abs(int(number)) > _SQLITE_MAX_INTEGER:
        raise ValueError("entier trop grand")
    return int(number)


def _to_text(value: Any) -> str | None:
    """Accepte une chaîne, un nombre (converti en texte) ou ``None``."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise TypeError(f"type {type(value).__name__} refusé")


def _expected_type(column: str) -> str:
    if column in _INTEGER_FIELDS:
        return "nombre entier attendu"
    if column in _REAL_FIELDS:
        return "nombre attendu"
    return "texte attendu"


def clean_order_updates(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Vérifie et convertit les modifications d'une commande avant leur écriture.

    - Seules les colonnes de :data:`ORDER_UPDATABLE_FIELDS` sont acceptées.
    - Une colonne numérique reçoit un nombre, une chaîne qui représente un nombre (valeur d'un
      champ de formulaire HTML) ou ``None`` ; une chaîne vide devient ``None``. Les colonnes
      INTEGER (``grammage``, ``laize``…) n'acceptent que des valeurs entières.
    - Une colonne texte reçoit une chaîne, un nombre (converti en texte) ou ``None``.

    Returns:
        Les modifications converties, dans l'ordre de :data:`ORDER_UPDATABLE_FIELDS`.

    Raises:
        OrderUpdateError: champ non modifiable ou valeur invalide ; ``fields`` liste les champs refusés.
    """
    unknown = sorted(str(key) for key in updates if key not in _UPDATABLE_FIELD_SET)
    if unknown:
        raise OrderUpdateError("Champs non modifiables : " + ", ".join(unknown), unknown)

    cleaned: dict[str, Any] = {}
    invalid: list[str] = []
    for column in ORDER_UPDATABLE_FIELDS:
        if column not in updates:
            continue
        value = updates[column]
        try:
            if column in _INTEGER_FIELDS or column in _REAL_FIELDS:
                cleaned[column] = _to_number(value, integer=column in _INTEGER_FIELDS)
            else:
                cleaned[column] = _to_text(value)
        except (TypeError, ValueError):
            invalid.append(column)
    if invalid:
        invalid.sort()
        details = ", ".join(f"{column} ({_expected_type(column)})" for column in invalid)
        raise OrderUpdateError(f"Valeurs invalides : {details}", invalid)
    return cleaned


def is_generic_client_name(name: str | None) -> bool:
    """Vrai pour un nom de client provisoire (« Client WhatsApp +212… », « Client Inconnu »)."""
    return bool(name) and name.startswith(GENERIC_CLIENT_PREFIXES)


def encode_text_list(value: Any) -> str | None:
    """Liste de textes (anomalies, champs manquants) au format JSON enregistré en base.

    ``None`` reste ``None`` ; une chaîne qui contient déjà une liste JSON est reprise, une autre
    chaîne devient une liste d'un élément. Les caractères non ASCII (accents, arabe) sont écrits
    tels quels.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = [value]
        items = decoded if isinstance(decoded, list) else [value]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        items = list(value)
    else:
        items = [value]
    return json.dumps([str(item) for item in items if item is not None and str(item) != ""], ensure_ascii=False)


def decode_text_list(raw: Any) -> list[str]:
    """Liste de textes lue dans une colonne JSON.

    Un texte qui n'est pas une liste JSON donne une liste d'un élément ; une valeur vide, une liste vide.
    """
    if raw is None or raw == "":
        return []
    try:
        decoded = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return [str(raw)]
    if isinstance(decoded, list):
        return [str(item) for item in decoded if item is not None]
    return [str(raw)]


def _with_decoded_lists(order: dict[str, Any]) -> dict[str, Any]:
    """Ajoute à une commande lue en base ses listes décodées (``anomalies_list``, ``champs_manquants_list``)."""
    order["anomalies_list"] = decode_text_list(order.get("anomalies"))
    order["champs_manquants_list"] = decode_text_list(order.get("champs_manquants"))
    return order


def _clean_message_sid(value: Any) -> str | None:
    """Identifiant de message nettoyé (espaces retirés, longueur limitée), ou ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:_MESSAGE_SID_MAX_CHARS] or None


def _clean_reference(value: Any) -> str | None:
    """Numéro de commande ou code client indiqué dans un message : texte sur une ligne, ou ``None``."""
    if value is None or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    return text[:_REFERENCE_MAX_CHARS] or None


def default_client_code(client_id: int) -> str:
    """Code client SAGE X3 attribué par défaut : « CL » suivi de l'identifiant sur 5 chiffres."""
    return f"{_CLIENT_CODE_PREFIX}{int(client_id):05d}"


def utc_timestamp(moment: datetime | None = None) -> str:
    """Horodatage UTC au format de la base (:data:`SQLITE_TIMESTAMP_FORMAT`).

    Args:
        moment: instant à écrire ; sans fuseau horaire, il est lu comme une heure locale du
            serveur. Par défaut, l'instant présent.
    """
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime(SQLITE_TIMESTAMP_FORMAT)


class _OrderReview:
    """Contrôles d'identification d'une commande avant son insertion : numéro de commande et code client.

    Attributes:
        client_code: code client SAGE X3 enregistré (toujours celui de la base).
        anomalies: anomalies ajoutées, en français, pour l'équipe commerciale.
        notes: phrases ajoutées aux informations supplémentaires de la commande.
    """

    def __init__(self) -> None:
        self.client_code: str | None = None
        self.anomalies: list[str] = []
        self.notes: list[str] = []

    def check_client_code(self, stated: Any, expected: str) -> None:
        """Retient le code ``expected`` ; un code ``stated`` différent est noté et signalé."""
        self.client_code = expected
        code = _clean_reference(stated)
        if code is None or code.upper() == expected.upper():
            return
        self.notes.append(f"Code client indiqué dans le message : {code}.")
        self.anomalies.append(f"Code client indiqué « {code} » différent du code du client ({expected}) : "
                              "à vérifier.")

    def check_order_number(self, cursor: sqlite3.Cursor, stated: Any) -> str | None:
        """Numéro de commande à enregistrer : ``None`` s'il est déjà attribué à une autre commande."""
        numero = _clean_reference(stated)
        if numero is None:
            return None
        row = cursor.execute("SELECT id FROM commandes WHERE numero_commande = ?", (numero,)).fetchone()
        if row is None:
            return numero
        logger.warning("Numéro de commande déjà attribué à la commande %s : commande créée sans ce numéro", row[0])
        self.notes.append(f"Numéro de commande indiqué dans le message : {numero} (déjà attribué à une autre "
                          "commande).")
        self.anomalies.append(f"Numéro de commande « {numero} » déjà utilisé par la commande CMD-{row[0]} : "
                              "doublon possible, à vérifier.")
        return None

    def informations(self, previous: Any) -> Any:
        """Informations supplémentaires de la commande, suivies des notes des contrôles."""
        if not self.notes:
            return previous
        notes = " ".join(self.notes)
        return f"{previous}\n{notes}" if previous else notes

    def confidence(self, value: Any) -> Any:
        """Confiance diminuée de :data:`~tecpap.extraction.rules.ANOMALY_PENALTY` points par anomalie ajoutée."""
        if not self.anomalies or isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        # Import différé : tecpap.extraction.rules importe ce module.
        from tecpap.extraction.rules import ANOMALY_PENALTY

        return max(0, int(value) - ANOMALY_PENALTY * len(self.anomalies))

    def encoded_anomalies(self, existing: Any) -> str | None:
        """Anomalies de la commande (JSON) complétées par celles des contrôles."""
        if not self.anomalies:
            return encode_text_list(existing)
        items = decode_text_list(encode_text_list(existing))
        return encode_text_list([*items, *(item for item in self.anomalies if item not in items)])


@dataclass(frozen=True)
class MailboxState:
    """Position de lecture d'une boîte IMAP.

    Attributes:
        uidvalidity: valeur UIDVALIDITY de la boîte (elle change quand les UID
            enregistrés ne désignent plus les mêmes messages).
        last_uid: plus grand UID déjà examiné pour cette valeur de UIDVALIDITY.
    """

    uidvalidity: int
    last_uid: int


class _ThreadState(threading.local):
    """Connexion du fil d'exécution courant.

    Attributes:
        conn: connexion ouverte par ce fil, ou ``None``.
        generation: génération des connexions à laquelle ``conn`` appartient ; une fermeture
            générale (:meth:`DatabaseManager.close_all`) change de génération.
        detached: vrai après :meth:`DatabaseManager.disconnect` dans ce fil : aucune connexion
            n'est rouverte automatiquement avant le prochain :meth:`DatabaseManager.connect`.
    """

    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None
        self.generation = -1
        self.detached = False


_MANAGERS: weakref.WeakSet[DatabaseManager] = weakref.WeakSet()
_MANAGERS_LOCK = threading.Lock()


def _same_database(first: str, second: str) -> bool:
    if first == ":memory:" or second == ":memory:":
        return False
    try:
        return Path(first).resolve() == Path(second).resolve()
    except OSError:
        return first == second


def close_connections_to(db_file: str) -> int:
    """Ferme toutes les connexions de tous les gestionnaires ouverts sur la base ``db_file``.

    Utilisé avant une restauration : aucune connexion ne garde une lecture en cours sur
    l'ancienne base. Les gestionnaires rouvrent leurs connexions au prochain
    :meth:`DatabaseManager.connect`.

    Returns:
        Le nombre de connexions fermées.
    """
    with _MANAGERS_LOCK:
        managers = [manager for manager in _MANAGERS if _same_database(manager.db_file, db_file)]
    return sum(manager.close_all() for manager in managers)


class DatabaseManager:
    """Gestionnaire de la base SQLite des commandes.

    Une connexion SQLite n'est jamais partagée entre fils d'exécution : chaque fil (requête du
    serveur web, tâche de fond) obtient la sienne par la propriété :attr:`connection`, ouverte
    à la première utilisation avec les mêmes réglages : mode WAL (journal à écriture anticipée,
    qui laisse les lectures se poursuivre pendant une écriture) et attente de 30 secondes quand
    la base est verrouillée par une autre connexion. L'ouverture automatique n'a lieu qu'une
    fois le gestionnaire ouvert par :meth:`connect` (ou :meth:`init_database`).

    - :meth:`connect` ouvre la connexion du fil courant ;
    - :meth:`disconnect` ferme la connexion du fil courant ;
    - :meth:`close_all` ferme les connexions de tous les fils (arrêt de l'application,
      restauration d'une sauvegarde) ; un :meth:`connect` ultérieur rouvre le gestionnaire.

    Les connexions des fils terminés sont fermées à l'ouverture suivante. Une base en mémoire
    (``":memory:"``) n'existe que dans sa connexion : celle-ci est alors partagée par tous les fils.

    Args:
        db_file: chemin du fichier SQLite. Par défaut, ``Settings.database_path``.
    """

    def __init__(self, db_file: str | None = None):
        self.db_file = db_file if db_file is not None else load_settings().database_path
        self._local = _ThreadState()
        self._lock = threading.RLock()
        # Connexions ouvertes : (fil propriétaire, connexion) ; fil None pour une base en mémoire.
        self._connections: list[tuple[weakref.ref[threading.Thread] | None, sqlite3.Connection]] = []
        self._generation = 0
        self._open = False
        self._initialized = False
        with _MANAGERS_LOCK:
            _MANAGERS.add(self)

    # ------------------------------------------------------------------ connexion

    @property
    def connection(self) -> sqlite3.Connection | None:
        """Connexion du fil courant, ouverte à la première utilisation ; ``None`` si le gestionnaire est fermé."""
        state = self._current_state()
        if state.conn is not None:
            return state.conn
        if state.detached or not self._open:
            return None
        return self._open_connection(state)

    @connection.setter
    def connection(self, value: sqlite3.Connection | None) -> None:
        """Remplace la connexion du fil courant (sans fermer la précédente) ; ``None`` la détache."""
        state = self._current_state()
        state.conn = value
        state.detached = value is None

    def _current_state(self) -> _ThreadState:
        state = self._local
        if state.generation != self._generation:
            state.conn, state.detached, state.generation = None, False, self._generation
        return state

    def _new_connection(self) -> sqlite3.Connection:
        if self.db_file != ":memory:":
            Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False permet seulement à close_all() de fermer la connexion d'un autre
        # fil ; chaque connexion n'est utilisée que par le fil qui l'a ouverte.
        connection = sqlite3.connect(self.db_file, check_same_thread=False, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _open_connection(self, state: _ThreadState) -> sqlite3.Connection | None:
        """Ouvre la connexion du fil courant (ou reprend la connexion partagée d'une base en mémoire)."""
        with self._lock:
            self._close_dead_threads()
            if self.db_file == ":memory:" and self._connections:
                connection = self._connections[0][1]
            else:
                try:
                    connection = self._new_connection()
                except (sqlite3.Error, OSError):
                    logger.exception("Échec de la connexion à la base de données %s", self.db_file)
                    return None
                owner = None if self.db_file == ":memory:" else weakref.ref(threading.current_thread())
                self._connections.append((owner, connection))
                logger.debug("Connexion à la base de données %s (fil %s)", self.db_file,
                             threading.current_thread().name)
            state.conn, state.detached, state.generation = connection, False, self._generation
            return connection

    def _close_dead_threads(self) -> None:
        """Ferme les connexions des fils terminés (appelée sous verrou)."""
        alive = []
        for owner, connection in self._connections:
            thread = owner() if owner is not None else None
            if owner is not None and (thread is None or not thread.is_alive()):
                try:
                    connection.close()
                except sqlite3.Error:
                    logger.debug("Fermeture de la connexion d'un fil terminé impossible", exc_info=True)
            else:
                alive.append((owner, connection))
        self._connections = alive

    def connect(self) -> bool:
        """Ouvre la connexion du fil courant si elle ne l'est pas déjà. Renvoie ``False`` en cas d'échec."""
        state = self._current_state()
        state.detached = False
        if state.conn is None and self._open_connection(state) is None:
            return False
        if not self._open:
            logger.info("Connexion à la base de données %s", self.db_file)
        self._open = True
        return True

    def disconnect(self) -> None:
        """Ferme la connexion du fil courant ; les autres fils gardent la leur.

        Pour une base en mémoire, la connexion partagée est fermée pour tous les fils.
        """
        if self.db_file == ":memory:":
            self.close_all()
            self._current_state().detached = True
            return
        state = self._current_state()
        connection, state.conn, state.detached = state.conn, None, True
        if connection is None:
            return
        with self._lock:
            self._connections = [entry for entry in self._connections if entry[1] is not connection]
        try:
            connection.close()
            logger.info("Déconnexion de la base de données %s", self.db_file)
        except sqlite3.Error:
            logger.warning("Erreur à la fermeture de la base de données %s", self.db_file, exc_info=True)

    def close_all(self) -> int:
        """Ferme les connexions de tous les fils ; le gestionnaire reste fermé jusqu'au prochain :meth:`connect`.

        Une requête en cours dans un autre fil échoue : à réserver à l'arrêt de l'application et
        à la restauration d'une sauvegarde.

        Returns:
            Le nombre de connexions fermées.
        """
        with self._lock:
            connections = [connection for _, connection in self._connections]
            self._connections = []
            self._generation += 1
            self._open = False
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                logger.warning("Erreur à la fermeture d'une connexion à %s", self.db_file, exc_info=True)
        if connections:
            logger.info("Connexions à la base de données %s fermées (%d)", self.db_file, len(connections))
        return len(connections)

    def open_connection_count(self) -> int:
        """Nombre de connexions ouvertes, tous fils confondus."""
        with self._lock:
            return len(self._connections)

    def init_database(self) -> None:
        """Crée les tables manquantes, ajoute les colonnes absentes et insère le catalogue produits.

        Crée aussi l'index d'unicité de ``commandes.message_sid``, la table des positions de
        lecture des boîtes IMAP et les tables du journal de sécurité, de l'index de la base de
        connaissances et des conversations WhatsApp. Les dates de validation écrites en heure
        locale du serveur par les versions antérieures (« AAAA-MM-JJTHH:MM:SS.ffffff ») sont
        converties en UTC au format de la base.
        """
        # Imports différés : ces modules dépendent indirectement de celui-ci.
        from tecpap.chatbot.conversation import ensure_schema as ensure_conversation_schema
        from tecpap.rag.store import ensure_schema as ensure_rag_schema
        from tecpap.security.events import ensure_schema as ensure_security_schema

        if not self.connection:
            self.connect()
        connection = self.connection

        cursor = connection.cursor()
        cursor.execute(_CREATE_CLIENTS)
        cursor.execute(_CREATE_PRODUITS)
        cursor.execute(_CREATE_COMMANDES)
        self._add_missing_columns(cursor)
        cursor.execute(_CREATE_MESSAGE_SID_INDEX)
        cursor.execute(_CREATE_LOGS)
        cursor.execute(_CREATE_MAILBOX_STATE)
        self._convert_local_validation_dates(cursor)

        for product in PRODUCT_CATALOG:
            cursor.execute(
                "INSERT OR IGNORE INTO produits (id, type, description) VALUES (?, ?, ?)",
                (product["id"], product["type"], product["description"]),
            )

        connection.commit()
        ensure_security_schema(connection)
        ensure_rag_schema(connection)
        ensure_conversation_schema(connection)
        self._initialized = True
        logger.info("Base de données initialisée")
        self._log_action("INIT", "database", None, "Database initialized")

    def _add_missing_columns(self, cursor: sqlite3.Cursor) -> None:
        """Ajoute à ``commandes`` les colonnes des versions récentes du schéma (bases existantes)."""
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(commandes)").fetchall()}
        for column, definition in _MIGRATION_COLUMNS:
            if column not in existing:
                # Noms et définitions proviennent de la constante _MIGRATION_COLUMNS, jamais d'une saisie.
                cursor.execute(f"ALTER TABLE commandes ADD COLUMN {column} {definition}")
        self.connection.commit()

    @staticmethod
    def _convert_local_validation_dates(cursor: sqlite3.Cursor) -> None:
        """Convertit en UTC les dates de validation écrites en heure locale par ``datetime.now().isoformat()``.

        Ces valeurs se reconnaissent au séparateur « T » ; une valeur sans fuseau horaire est lue
        comme une heure locale du serveur. Une valeur illisible est laissée telle quelle.
        """
        rows = cursor.execute(
            "SELECT id, validated_at FROM commandes WHERE validated_at LIKE '____-__-__T%'").fetchall()
        converted = 0
        for order_id, value in rows:
            try:
                moment = datetime.fromisoformat(str(value))
            except ValueError:
                continue
            cursor.execute("UPDATE commandes SET validated_at = ? WHERE id = ?", (utc_timestamp(moment), order_id))
            converted += 1
        if converted:
            logger.info("%d date(s) de validation converties en UTC", converted)

    def _log_action(self, action: str, table_name: str | None, record_id: int | None, details: str) -> None:
        """Ajoute une ligne à la table ``logs`` (trace des créations et modifications)."""
        connection = self.connection
        connection.execute(
            "INSERT INTO logs (action, table_name, record_id, details) VALUES (?, ?, ?, ?)",
            (action, table_name, record_id, details),
        )
        connection.commit()

    # ------------------------------------------------------------------ clients

    def get_or_create_client(self, nom: str | None, email: str | None = None,
                             telephone: str | None = None) -> dict[str, Any]:
        """Renvoie le client correspondant ou le crée.

        Règles :
        - un nom réel (pas un nom provisoire « Client WhatsApp … ») est cherché par nom, sans
          tenir compte de la casse ;
        - le téléphone d'un message n'est jamais rattaché à un client existant trouvé par son
          nom : n'importe qui peut écrire « ana <nom d'un client> » depuis un numéro inconnu. Le
          rattachement a lieu à la validation de la commande par l'équipe commerciale
          (:meth:`attach_phone_to_client`) ; un client créé ici reçoit en revanche le téléphone ;
        - le téléphone n'identifie pas un client : une même personne peut commander pour
          plusieurs entreprises ;
        - pour un nom provisoire, le client est cherché par téléphone puis par email.
        """
        cursor = self.connection.cursor()

        if not nom or not nom.strip():
            nom = email or telephone or "Client Inconnu"

        if not is_generic_client_name(nom):
            cursor.execute("SELECT * FROM clients WHERE LOWER(nom) = LOWER(?)", (nom,))
            client = cursor.fetchone()
            if client:
                return dict(client)
            return self._insert_client(cursor, nom, email, telephone)

        if telephone:
            cursor.execute("SELECT * FROM clients WHERE telephone = ?", (telephone,))
            client = cursor.fetchone()
            if client:
                return dict(client)

        if email:
            cursor.execute("SELECT * FROM clients WHERE email = ?", (email,))
            client = cursor.fetchone()
            if client:
                return dict(client)

        return self._insert_client(cursor, nom, email, telephone)

    def _insert_client(self, cursor: sqlite3.Cursor, nom: str, email: str | None,
                       telephone: str | None) -> dict[str, Any]:
        cursor.execute("INSERT INTO clients (nom, email, telephone) VALUES (?, ?, ?)", (nom, email, telephone))
        self.connection.commit()
        client_id = cursor.lastrowid
        self._log_action("CREATE", "clients", client_id, f"Created client: {nom}")
        logger.info("Client créé : %s (id %s)", nom, client_id)
        return {"id": client_id, "nom": nom, "email": email, "telephone": telephone}

    def attach_phone_to_client(self, client_id: int, phone: str | None) -> bool:
        """Enregistre le téléphone d'un client qui n'en a pas encore.

        Appelée quand l'équipe commerciale valide une commande WhatsApp : la validation vaut
        vérification du lien entre le numéro de l'expéditeur et le client. Un téléphone déjà
        enregistré n'est jamais remplacé.

        Args:
            client_id: identifiant du client.
            phone: numéro de l'expéditeur (« whatsapp: » et espaces retirés).

        Returns:
            ``True`` si le téléphone a été enregistré.
        """
        number = (phone or "").replace("whatsapp:", "").strip()
        if not number or client_id is None:
            return False
        connection = self.connection
        cursor = connection.execute(
            "UPDATE clients SET telephone = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND (telephone IS NULL OR TRIM(telephone) = '')",
            (number, int(client_id)),
        )
        connection.commit()
        if cursor.rowcount != 1:
            return False
        self._log_action("UPDATE", "clients", int(client_id), "Phone attached after order validation")
        logger.info("Téléphone rattaché au client %s après validation", client_id)
        return True

    def get_all_clients(self) -> list[dict[str, Any]]:
        """Renvoie tous les clients avec leur nombre de commandes et la date de la dernière."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT c.*,
                   COUNT(cmd.id) as total_orders,
                   MAX(cmd.created_at) as last_order_date
            FROM clients c
            LEFT JOIN commandes cmd ON c.id = cmd.client_id
            GROUP BY c.id
            ORDER BY c.nom
        """)
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------ produits

    def get_product_by_type(self, type_name: str) -> dict[str, Any] | None:
        """Renvoie le premier produit dont le nom contient ``type_name``."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM produits WHERE type LIKE ?", (f"%{type_name}%",))
        product = cursor.fetchone()
        return dict(product) if product else None

    def get_all_products(self) -> list[dict[str, Any]]:
        """Renvoie tous les produits du catalogue."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM produits")
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------ commandes

    def is_email_processed(self, email_id: str | None) -> bool:
        """Vrai si une commande porte cet identifiant d'email dans la boîte (« UIDVALIDITY:UID »).

        Cet identifiant désigne un message de la boîte IMAP, pas son contenu : pour savoir si un
        email a déjà été traité, :meth:`is_message_processed` (en-tête ``Message-ID``) fait foi.
        """
        if not email_id or not self.connection:
            return False
        cursor = self.connection.cursor()
        try:
            # Point de contrôle du journal WAL : rend visibles les écritures des autres connexions.
            cursor.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            logger.debug("Point de contrôle WAL impossible", exc_info=True)
        cursor.execute("SELECT id FROM commandes WHERE email_id = ?", (email_id,))
        return cursor.fetchone() is not None

    def find_order_by_message_sid(self, message_sid: str | None) -> int | None:
        """Identifiant de la commande créée à partir du message ``message_sid``, ou ``None``."""
        sid = _clean_message_sid(message_sid)
        connection = self.connection
        if sid is None or connection is None:
            return None
        row = connection.execute("SELECT id FROM commandes WHERE message_sid = ?", (sid,)).fetchone()
        return int(row[0]) if row else None

    def is_message_processed(self, message_sid: str | None) -> bool:
        """Vrai si une commande a déjà été créée à partir du message ``message_sid`` (WhatsApp ou email)."""
        return self.find_order_by_message_sid(message_sid) is not None

    # ------------------------------------------------------------------ position de lecture des boîtes IMAP

    def get_mailbox_state(self, mailbox: str) -> MailboxState | None:
        """Position de lecture enregistrée pour la boîte ``mailbox`` (compte et dossier), ou ``None``."""
        connection = self.connection
        connection.execute(_CREATE_MAILBOX_STATE)
        row = connection.execute("SELECT uidvalidity, last_uid FROM email_mailbox_state WHERE mailbox = ?",
                                 (mailbox,)).fetchone()
        return MailboxState(int(row[0]), int(row[1])) if row else None

    def set_mailbox_state(self, mailbox: str, uidvalidity: int, last_uid: int) -> None:
        """Enregistre le dernier UID examiné de la boîte ``mailbox``.

        Pour une même valeur de UIDVALIDITY, la position n'avance que vers des UID plus grands ;
        une autre valeur de UIDVALIDITY remplace la position.
        """
        connection = self.connection
        connection.execute(_CREATE_MAILBOX_STATE)
        connection.execute(
            "INSERT INTO email_mailbox_state (mailbox, uidvalidity, last_uid, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(mailbox) DO UPDATE SET "
            "last_uid = CASE WHEN uidvalidity = excluded.uidvalidity THEN MAX(last_uid, excluded.last_uid) "
            "ELSE excluded.last_uid END, "
            "uidvalidity = excluded.uidvalidity, updated_at = CURRENT_TIMESTAMP",
            (mailbox, int(uidvalidity), int(last_uid)),
        )
        connection.commit()

    def _existing_order_id(self, cursor: sqlite3.Cursor, order_data: Mapping[str, Any]) -> int | None:
        """Commande déjà enregistrée pour ce message : même ``message_sid``, à défaut même ``email_id``.

        Le numéro de commande n'identifie pas un message : deux clients peuvent utiliser le même,
        et un client peut réutiliser le sien. Quand ``message_sid`` est connu, ``email_id`` n'est pas
        comparé non plus : un ancien numéro de message IMAP peut avoir été réattribué.
        """
        message_sid = _clean_message_sid(order_data.get("message_sid"))
        column, value = ("message_sid", message_sid) if message_sid else ("email_id", order_data.get("email_id"))
        if not value:
            return None
        # Nom de colonne issu des deux constantes ci-dessus ; la valeur passe par un paramètre « ? ».
        cursor.execute(f"SELECT id FROM commandes WHERE {column} = ?", (value,))  # nosec B608
        existing = cursor.fetchone()
        if existing is None:
            return None
        logger.info("Message %s déjà traité (commande id %s)", value, existing[0])
        return int(existing[0])

    def create_order(self, order_data: Mapping[str, Any]) -> int:
        """Enregistre une commande extraite et renvoie son identifiant (voir :meth:`save_order`)."""
        return self.save_order(order_data)[0]

    def save_order(self, order_data: Mapping[str, Any]) -> tuple[int, bool]:
        """Enregistre une commande extraite ; renvoie son identifiant et ``True`` si elle vient d'être créée.

        Règles :

        - un message déjà enregistré (même ``message_sid``, à défaut même ``email_id``) n'est pas
          dupliqué : l'identifiant de la commande existante est renvoyé avec ``False`` ;
        - un numéro de commande (``numero_commande``) déjà attribué à une autre commande n'est
          jamais une raison de rattacher la commande à celle-ci : la commande est créée sans ce
          numéro, qui est noté dans les informations supplémentaires, et une anomalie signale
          un doublon possible ;
        - le code client SAGE X3 vient toujours de la base : celui de la dernière commande
          validée du client, à défaut « CL » suivi de l'identifiant du client sur 5 chiffres. Un
          code indiqué dans le message qui en diffère est noté et signalé par une anomalie ;
        - chaque anomalie ajoutée ici retire :data:`~tecpap.extraction.rules.ANOMALY_PENALTY`
          points à la confiance.

        Le client est retrouvé ou créé, le produit rattaché d'après ``type_produit`` et le reste
        à livrer calculé. Les champs de traçabilité (:data:`ORDER_AUDIT_FIELDS`) sont
        enregistrés : listes ``anomalies`` et ``champs_manquants`` en JSON, ``message_brut``
        limité à :data:`MESSAGE_BRUT_MAX_CHARS` caractères.
        """
        connection = self.connection
        cursor = connection.cursor()

        existing = self._existing_order_id(cursor, order_data)
        if existing is not None:
            return existing, False

        client = self._client_for_order(order_data)
        product = None
        if order_data.get("type_produit"):
            product = self.get_product_by_type(order_data["type_produit"])

        review = _OrderReview()
        review.check_client_code(order_data.get("code_client"), self._client_code(cursor, client["id"]))
        numero = review.check_order_number(cursor, order_data.get("numero_commande"))

        for attempt in range(2):
            try:
                cursor.execute(
                    # Colonnes issues de la constante _ORDER_COLUMNS ; les valeurs passent par des paramètres « ? ».
                    f"INSERT INTO commandes ({', '.join(_ORDER_COLUMNS)}) "  # nosec B608
                    f"VALUES ({', '.join('?' for _ in _ORDER_COLUMNS)})",
                    self._order_values(order_data, client, product, numero, review),
                )
                break
            except sqlite3.IntegrityError:
                # Même message ou même numéro de commande enregistré entre-temps par un autre fil.
                connection.rollback()
                existing = self._existing_order_id(cursor, order_data)
                if existing is not None:
                    return existing, False
                if attempt or numero is None:
                    raise
                numero = review.check_order_number(cursor, numero)
        connection.commit()
        order_id = cursor.lastrowid

        try:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            logger.debug("Point de contrôle WAL impossible", exc_info=True)

        self._log_action("CREATE", "commandes", order_id, f"Created order: {numero}")
        if review.anomalies:
            logger.warning("Commande %s enregistrée avec %d anomalie(s) d'identification à vérifier",
                           order_id, len(review.anomalies))
        logger.info("Commande enregistrée (id %s)", order_id)
        return order_id, True

    @staticmethod
    def _client_code(cursor: sqlite3.Cursor, client_id: int) -> str:
        """Code SAGE X3 du client : celui de sa dernière commande validée, à défaut :func:`default_client_code`."""
        row = cursor.execute(
            "SELECT code_client FROM commandes WHERE client_id = ? AND statut = 'validee' "
            "AND code_client IS NOT NULL AND TRIM(code_client) != '' "
            "ORDER BY validated_at DESC, id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
        return str(row[0]).strip() if row else default_client_code(client_id)

    @staticmethod
    def _order_values(order_data: Mapping[str, Any], client: Mapping[str, Any], product: Mapping[str, Any] | None,
                      numero: str | None, review: _OrderReview) -> tuple[Any, ...]:
        """Valeurs de la ligne ``commandes``, dans l'ordre de :data:`_ORDER_COLUMNS`."""
        quantite = order_data.get("quantite") or 0
        quantite_livree = order_data.get("quantite_livree") or 0
        message_brut = order_data.get("message_brut")
        if message_brut is not None:
            message_brut = str(message_brut)[:MESSAGE_BRUT_MAX_CHARS]
        return (
            numero,
            order_data.get("ligne_commande", 1),
            order_data.get("site_vente", "SXP"),
            client["id"],
            review.client_code,
            product["id"] if product else None,
            order_data.get("code_article"),
            order_data.get("nature_produit"),
            quantite,
            order_data.get("unite", "US"),
            quantite_livree,
            quantite - quantite_livree,
            order_data.get("quantite_facturee", 0),
            order_data.get("prix_unitaire"),
            order_data.get("prix_total"),
            order_data.get("devise", "MAD"),
            order_data.get("date_commande"),
            order_data.get("date_livraison"),
            order_data.get("commercial", "DIVERS"),
            order_data.get("type_sac"),
            order_data.get("format_sac"),
            order_data.get("type_papier"),
            order_data.get("grammage"),
            order_data.get("laize"),
            order_data.get("impression_client"),
            review.informations(order_data.get("informations_supplementaires")),
            review.confidence(order_data.get("confiance")),
            order_data.get("source", "email"),
            order_data.get("email_id"),
            order_data.get("email_subject"),
            order_data.get("email_from"),
            order_data.get("whatsapp_from"),
            _clean_message_sid(order_data.get("message_sid")),
            message_brut,
            order_data.get("langue"),
            order_data.get("modele_llm"),
            order_data.get("routage_modele"),
            review.encoded_anomalies(order_data.get("anomalies")),
            encode_text_list(order_data.get("champs_manquants")),
        )

    def _client_for_order(self, order_data: dict[str, Any]) -> dict[str, Any]:
        """Retrouve ou crée le client d'une commande (nom extrait, sinon nom provisoire)."""
        client_name = order_data.get("entreprise_cliente")
        client_email = order_data.get("email_from") or order_data.get("whatsapp_from")

        client_phone = None
        if order_data.get("whatsapp_from"):
            client_phone = order_data["whatsapp_from"].replace("whatsapp:", "")

        if not client_name or not client_name.strip():
            if client_phone:
                client_name = f"Client WhatsApp {client_phone}"
            else:
                client_name = client_email or "Client Inconnu"

        return self.get_or_create_client(client_name, client_email, client_phone)

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        """Renvoie une commande avec le nom, l'email et le téléphone du client et le nom du produit.

        Les listes ``anomalies`` et ``champs_manquants`` sont aussi renvoyées décodées
        (``anomalies_list``, ``champs_manquants_list``).
        """
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT c.*, cl.nom as client_nom, cl.email as client_email, cl.telephone as client_telephone,
                   p.type as produit_type
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE c.id = ?
        """, (order_id,))
        order = cursor.fetchone()
        return _with_decoded_lists(dict(order)) if order else None

    def get_all_orders(self, status: str | None = None) -> list[dict[str, Any]]:
        """Renvoie les commandes, des plus récentes aux plus anciennes, éventuellement filtrées par statut.

        Comme pour :meth:`get_order`, les listes décodées ``anomalies_list`` et
        ``champs_manquants_list`` sont ajoutées.
        """
        cursor = self.connection.cursor()
        query = """
            SELECT c.*, cl.nom as client_nom, p.type as produit_type
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
        """
        if status:
            cursor.execute(query + " WHERE c.statut = ? ORDER BY c.created_at DESC", (status,))
        else:
            cursor.execute(query + " ORDER BY c.created_at DESC")
        return [_with_decoded_lists(dict(row)) for row in cursor.fetchall()]

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """Renvoie les commandes en attente de validation."""
        return self.get_all_orders(status="en_attente")

    def update_order_status(self, order_id: int, status: str, validated_by: str | None = None) -> None:
        """Change le statut d'une commande ; une validation enregistre aussi la date (UTC) et l'auteur.

        Raises:
            ValueError: ``status`` ne fait pas partie de :data:`ORDER_STATUSES`.
        """
        if status not in ORDER_STATUSES:
            raise ValueError(
                f"Statut de commande inconnu : {status!r} (statuts possibles : {', '.join(ORDER_STATUSES)})"
            )
        cursor = self.connection.cursor()
        if status == "validee":
            cursor.execute(
                "UPDATE commandes SET statut = ?, validated_at = ?, validated_by = ? WHERE id = ?",
                (status, utc_timestamp(), validated_by, order_id),
            )
        else:
            cursor.execute("UPDATE commandes SET statut = ? WHERE id = ?", (status, order_id))
        self.connection.commit()
        self._log_action("UPDATE", "commandes", order_id, f"Status changed to: {status}")
        logger.info("Statut de la commande %s : %s", order_id, status)

    def update_order(self, order_id: int, updates: Mapping[str, Any]) -> None:
        """Met à jour des champs d'une commande.

        Seules les colonnes de :data:`ORDER_UPDATABLE_FIELDS` sont modifiables ; les valeurs sont
        vérifiées et converties par :func:`clean_order_updates`. Sans modification, rien n'est écrit.

        Raises:
            OrderUpdateError: (sous-classe de ``ValueError``) champ non modifiable ou valeur invalide.
        """
        cleaned = clean_order_updates(updates)
        if not cleaned:
            return
        columns = [column for column in ORDER_UPDATABLE_FIELDS if column in cleaned]
        # Les noms de colonnes viennent de la constante ORDER_UPDATABLE_FIELDS, jamais de la requête ;
        # les valeurs passent par des paramètres « ? ».
        set_clause = ", ".join(f"{column} = ?" for column in columns)
        values = [*(cleaned[column] for column in columns), order_id]
        cursor = self.connection.cursor()
        cursor.execute(
            f"UPDATE commandes SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",  # nosec B608
            values,
        )
        self.connection.commit()
        self._log_action("UPDATE", "commandes", order_id, f"Updated fields: {columns}")

    def delete_order(self, order_id: int) -> None:
        """Suppression logique : la commande passe au statut « supprimee »."""
        cursor = self.connection.cursor()
        cursor.execute("UPDATE commandes SET statut = 'supprimee' WHERE id = ?", (order_id,))
        self.connection.commit()
        self._log_action("DELETE", "commandes", order_id, "Order marked as deleted")

    # ------------------------------------------------------------------ statistiques

    def get_stats(self) -> dict[str, int]:
        """Compte les commandes par statut et par canal, et les clients."""
        cursor = self.connection.cursor()
        queries = {
            "total_orders": "SELECT COUNT(*) FROM commandes",
            "pending_orders": "SELECT COUNT(*) FROM commandes WHERE statut = 'en_attente'",
            "validated_orders": "SELECT COUNT(*) FROM commandes WHERE statut = 'validee'",
            "total_clients": "SELECT COUNT(*) FROM clients",
            "whatsapp_total": "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp'",
            "whatsapp_pending": "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'en_attente'",
            "whatsapp_validated": "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'validee'",
            "email_total": "SELECT COUNT(*) FROM commandes WHERE source = 'email' OR source IS NULL",
            "email_pending": (
                "SELECT COUNT(*) FROM commandes WHERE (source = 'email' OR source IS NULL) AND statut = 'en_attente'"
            ),
            "email_validated": (
                "SELECT COUNT(*) FROM commandes WHERE (source = 'email' OR source IS NULL) AND statut = 'validee'"
            ),
        }
        stats = {}
        for key, query in queries.items():
            cursor.execute(query)
            stats[key] = cursor.fetchone()[0]
        return stats

    def get_top_clients(self, limit: int = 5) -> list[dict[str, Any]]:
        """Renvoie les clients qui ont passé le plus de commandes."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT cl.id, cl.nom, COUNT(c.id) as total_orders, SUM(c.quantite) as total_quantity
            FROM clients cl
            LEFT JOIN commandes c ON cl.id = c.client_id
            WHERE cl.nom IS NOT NULL AND cl.nom != ''
            GROUP BY cl.id
            HAVING total_orders > 0
            ORDER BY total_orders DESC, total_quantity DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_top_products(self, limit: int = 5) -> list[dict[str, Any]]:
        """Renvoie les produits les plus commandés."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT p.id, p.type, COUNT(c.id) as order_count
            FROM produits p
            LEFT JOIN commandes c ON p.id = c.produit_id
            WHERE p.type IS NOT NULL AND p.type != ''
            GROUP BY p.id
            HAVING order_count > 0
            ORDER BY order_count DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_orders_trend(self, days: int = 7, timezone_name: str | None = None) -> list[dict[str, Any]]:
        """Renvoie le nombre de commandes reçues par jour sur les ``days`` derniers jours.

        Les dates d'enregistrement de la base sont en UTC ; les jours sont ceux du fuseau horaire
        de l'entreprise (``Africa/Casablanca`` par défaut) : une commande reçue à 00 h 30 à
        Casablanca compte pour ce jour-là, pas pour la veille. Le résultat, trié par date
        croissante, ne contient pas les jours sans commande.

        Args:
            days: nombre de jours en arrière, aujourd'hui compris dans le calcul.
            timezone_name: nom IANA du fuseau horaire des jours ; par défaut celui de l'entreprise.
        """
        zone = resolve_timezone(timezone_name)
        first_day = datetime.now(timezone.utc).astimezone(zone).date() - timedelta(days=days)
        # Marge d'un jour de chaque côté du fuseau horaire, affinée ensuite par la conversion.
        cursor = self.connection.cursor()
        cursor.execute("SELECT created_at FROM commandes WHERE created_at >= ?",
                       ((first_day - timedelta(days=1)).isoformat(),))
        counts: dict[str, int] = {}
        for (created_at,) in cursor.fetchall():
            moment = parse_timestamp(created_at)
            if not isinstance(moment, datetime):
                continue
            day = moment.astimezone(zone).date()
            if day >= first_day:
                counts[day.isoformat()] = counts.get(day.isoformat(), 0) + 1
        return [{"date": day, "count": counts[day]} for day in sorted(counts)]

    def get_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Renvoie les dernières lignes du journal des actions."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


def migrate_schema(db_file: str) -> None:
    """Met à jour le schéma d'un fichier de base : colonnes, index et tables absents sont ajoutés.

    Les données existantes ne sont pas modifiées, à part la conversion en UTC des anciennes dates
    de validation (voir :meth:`DatabaseManager.init_database`). Sert à rendre utilisable par la
    version courante une sauvegarde faite par une version plus ancienne.

    Raises:
        sqlite3.Error: le fichier n'est pas une base SQLite lisible, ou son schéma est incompatible.
    """
    manager = DatabaseManager(db_file)
    try:
        if not manager.connect():
            raise sqlite3.DatabaseError(f"base illisible : {Path(db_file).name}")
        manager.init_database()
    finally:
        manager.close_all()
