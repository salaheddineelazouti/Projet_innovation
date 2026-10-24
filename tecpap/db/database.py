"""Accès à la base SQLite : clients, produits, commandes et journal des actions.

La table ``commandes`` suit le format des commandes SAGE X3 (numéro, ligne, site de vente,
code client, code article, quantités livrées et facturées…) pour que l'export vers l'ERP soit
direct. Les noms de colonnes sont en français, comme dans l'ERP.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from tecpap.catalog import PRODUCTS
from tecpap.config import load_settings

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

_ORDER_COLUMNS = (
    "numero_commande", "ligne_commande", "site_vente", "client_id", "code_client",
    "produit_id", "code_article", "nature_produit",
    "quantite", "unite", "quantite_livree", "reste_a_livrer", "quantite_facturee",
    "prix_unitaire", "prix_total", "devise",
    "date_commande", "date_livraison", "commercial",
    "type_sac", "format_sac", "type_papier", "grammage", "laize", "impression_client",
    "informations_supplementaires", "confiance", "source",
    "email_id", "email_subject", "email_from", "whatsapp_from",
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
le statut, la confiance de l'extraction, la source, les traces de réception (email, WhatsApp) et
les dates de création ou de validation n'en font pas partie : elles ne changent que par les
méthodes dédiées (:meth:`DatabaseManager.update_order_status`, par exemple).
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


class DatabaseManager:
    """Gestionnaire de la base SQLite des commandes.

    Une seule connexion est ouverte par instance ; elle est partagée entre les fils d'exécution
    du serveur web (``check_same_thread=False``) et utilise le mode WAL (journal à écriture
    anticipée), qui laisse les lectures se poursuivre pendant une écriture.

    Args:
        db_file: chemin du fichier SQLite. Par défaut, ``Settings.database_path``.
    """

    def __init__(self, db_file: str | None = None):
        self.db_file = db_file if db_file is not None else load_settings().database_path
        self.connection: sqlite3.Connection | None = None
        self._initialized = False

    # ------------------------------------------------------------------ connexion

    def connect(self) -> bool:
        """Ouvre la connexion si elle ne l'est pas déjà. Renvoie ``False`` en cas d'échec."""
        if self.connection:
            return True
        try:
            if self.db_file != ":memory:":
                Path(self.db_file).parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.db_file, check_same_thread=False, timeout=30.0)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA busy_timeout=30000")
            logger.info("Connexion à la base de données %s", self.db_file)
            return True
        except (sqlite3.Error, OSError):
            logger.exception("Échec de la connexion à la base de données %s", self.db_file)
            self.connection = None
            return False

    def disconnect(self) -> None:
        """Ferme la connexion si elle est ouverte."""
        if not self.connection:
            return
        try:
            self.connection.close()
            logger.info("Déconnexion de la base de données %s", self.db_file)
        except sqlite3.Error:
            logger.warning("Erreur à la fermeture de la base de données %s", self.db_file, exc_info=True)
        finally:
            self.connection = None

    def init_database(self) -> None:
        """Crée les tables manquantes, ajoute les colonnes absentes et insère le catalogue produits."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor()
        cursor.execute(_CREATE_CLIENTS)
        cursor.execute(_CREATE_PRODUITS)
        cursor.execute(_CREATE_COMMANDES)
        self._add_missing_columns(cursor)
        cursor.execute(_CREATE_LOGS)

        for product in PRODUCT_CATALOG:
            cursor.execute(
                "INSERT OR IGNORE INTO produits (id, type, description) VALUES (?, ?, ?)",
                (product["id"], product["type"], product["description"]),
            )

        self.connection.commit()
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

    def _log_action(self, action: str, table_name: str | None, record_id: int | None, details: str) -> None:
        """Ajoute une ligne à la table ``logs`` (trace des créations et modifications)."""
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO logs (action, table_name, record_id, details) VALUES (?, ?, ?, ?)",
            (action, table_name, record_id, details),
        )
        self.connection.commit()

    # ------------------------------------------------------------------ clients

    def get_or_create_client(self, nom: str | None, email: str | None = None,
                             telephone: str | None = None) -> dict[str, Any]:
        """Renvoie le client correspondant ou le crée.

        Règles :
        - un nom réel (pas un nom provisoire « Client WhatsApp … ») est cherché par nom, sans
          tenir compte de la casse ; le téléphone est complété s'il manquait ;
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
                if telephone and not client["telephone"]:
                    cursor.execute("UPDATE clients SET telephone = ? WHERE id = ?", (telephone, client["id"]))
                    self.connection.commit()
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
        """Vrai si une commande a déjà été créée à partir de cet email."""
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

    def create_order(self, order_data: dict[str, Any]) -> int:
        """Enregistre une commande extraite et renvoie son identifiant.

        Une commande déjà connue (même ``numero_commande`` ou même ``email_id``) n'est pas
        dupliquée : l'identifiant existant est renvoyé. Le client est retrouvé ou créé, le
        produit rattaché d'après ``type_produit`` et le reste à livrer calculé.
        """
        cursor = self.connection.cursor()

        numero = order_data.get("numero_commande")
        if numero:
            cursor.execute("SELECT id FROM commandes WHERE numero_commande = ?", (numero,))
            existing = cursor.fetchone()
            if existing:
                logger.info("Commande %s déjà enregistrée (id %s)", numero, existing[0])
                return existing[0]

        email_id = order_data.get("email_id")
        if email_id:
            cursor.execute("SELECT id FROM commandes WHERE email_id = ?", (email_id,))
            existing = cursor.fetchone()
            if existing:
                logger.info("Message déjà traité (commande id %s)", existing[0])
                return existing[0]

        client = self._client_for_order(order_data)

        product = None
        if order_data.get("type_produit"):
            product = self.get_product_by_type(order_data["type_produit"])

        quantite = order_data.get("quantite") or 0
        quantite_livree = order_data.get("quantite_livree") or 0
        # Code client au format SAGE X3 : « CL » suivi de l'identifiant sur 5 chiffres.
        code_client = order_data.get("code_client") or f"CL{str(client['id']).zfill(5)}"

        values = (
            order_data.get("numero_commande"),
            order_data.get("ligne_commande", 1),
            order_data.get("site_vente", "SXP"),
            client["id"],
            code_client,
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
            order_data.get("informations_supplementaires"),
            order_data.get("confiance"),
            order_data.get("source", "email"),
            order_data.get("email_id"),
            order_data.get("email_subject"),
            order_data.get("email_from"),
            order_data.get("whatsapp_from"),
        )
        # Colonnes issues de la constante _ORDER_COLUMNS ; les valeurs passent par des paramètres « ? ».
        placeholders = ", ".join("?" for _ in _ORDER_COLUMNS)
        cursor.execute(
            f"INSERT INTO commandes ({', '.join(_ORDER_COLUMNS)}) VALUES ({placeholders})",  # nosec B608
            values,
        )
        self.connection.commit()
        order_id = cursor.lastrowid

        try:
            self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            logger.debug("Point de contrôle WAL impossible", exc_info=True)

        self._log_action("CREATE", "commandes", order_id, f"Created order: {order_data.get('numero_commande')}")
        logger.info("Commande enregistrée (id %s)", order_id)
        return order_id

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
        """Renvoie une commande avec le nom, l'email et le téléphone du client et le nom du produit."""
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
        return dict(order) if order else None

    def get_all_orders(self, status: str | None = None) -> list[dict[str, Any]]:
        """Renvoie les commandes, des plus récentes aux plus anciennes, éventuellement filtrées par statut."""
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
        return [dict(row) for row in cursor.fetchall()]

    def get_pending_orders(self) -> list[dict[str, Any]]:
        """Renvoie les commandes en attente de validation."""
        return self.get_all_orders(status="en_attente")

    def update_order_status(self, order_id: int, status: str, validated_by: str | None = None) -> None:
        """Change le statut d'une commande ; une validation enregistre aussi la date et l'auteur.

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
                (status, datetime.now().isoformat(), validated_by, order_id),
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

    def get_orders_trend(self, days: int = 7) -> list[dict[str, Any]]:
        """Renvoie le nombre de commandes par jour sur les ``days`` derniers jours."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM commandes
            WHERE created_at >= DATE('now', ?)
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (f"-{days} days",))
        return [dict(row) for row in cursor.fetchall()]

    def get_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Renvoie les dernières lignes du journal des actions."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]
