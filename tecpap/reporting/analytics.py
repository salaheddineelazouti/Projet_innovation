"""Statistiques, alertes, historique client, prévisions simples et exports (Excel, CSV, PDF).

Les alertes et les prévisions reposent sur des règles fixes calculées en SQL (seuils de
quantité, ancienneté des commandes en attente, confiance de l'extraction) ; seule la
classification de priorité d'un email fait appel à un modèle de langue, et elle a une valeur
par défaut quand aucune clé d'API n'est configurée.

Règles communes aux statistiques et aux exports :

- une commande supprimée (suppression logique, statut ``supprimee``) n'est comptée nulle part
  et n'est exportée que sur demande explicite de ce statut ;
- le chiffre d'affaires ne porte que sur les commandes validées ;
- l'export SAGE X3 ne contient que des commandes validées : seule une commande relue et validée
  par l'équipe commerciale entre dans l'ERP ;
- les horodatages de la base sont en UTC (temps universel coordonné, ``CURRENT_TIMESTAMP`` de
  SQLite) ; les périodes des filtres (« Date début », « Date fin ») et les dates écrites dans
  les exports sont exprimées dans le fuseau horaire de l'entreprise (réglage
  ``Settings.timezone``, Casablanca par défaut).
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pandas as pd

from tecpap.config import Settings, load_settings
from tecpap.db.database import ORDER_STATUSES
from tecpap.extraction.prompts import PRIORITY_SYSTEM_PROMPT, build_priority_prompt
from tecpap.formatting import DEFAULT_TIMEZONE, format_amount, parse_timestamp, resolve_timezone
from tecpap.llm.client import LLMClient
from tecpap.reporting.spreadsheet import neutralize_csv_frame, protect_text_cells, unique_export_path

if TYPE_CHECKING:
    from tecpap.db.database import DatabaseManager

logger = logging.getLogger(__name__)

# Seuils des alertes.
HIGH_QUANTITY_THRESHOLD = 10000
PENDING_MAX_HOURS = 24
NEW_CLIENT_LARGE_ORDER = 5000
LOW_CONFIDENCE_THRESHOLD = 60

# Segments de valeur client selon le total dépensé (MAD).
HIGH_VALUE_CLIENT = 10000
MEDIUM_VALUE_CLIENT = 1000

PRIORITY_LEVELS = ("high", "medium", "low")

VALIDATED_STATUS = "validee"
REJECTED_STATUS = "rejetee"
DELETED_STATUS = "supprimee"


def _not_deleted(alias: str = "") -> str:
    """Condition SQL qui écarte les commandes supprimées (``alias`` : préfixe de table, « c. »)."""
    return f"COALESCE({alias}statut, '') != '{DELETED_STATUS}'"


def _not_rejected_nor_deleted(alias: str = "") -> str:
    """Condition SQL qui écarte les commandes rejetées et les commandes supprimées."""
    return f"COALESCE({alias}statut, '') NOT IN ('{REJECTED_STATUS}', '{DELETED_STATUS}')"


def _settings_timezone(settings: Settings | None) -> str:
    """Fuseau horaire de l'entreprise lu dans la configuration (Casablanca par défaut)."""
    return getattr(settings, "timezone", None) or DEFAULT_TIMEZONE


def _local_today(now: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE) -> date:
    """Date du jour dans le fuseau horaire ``timezone_name`` (``now`` sans fuseau horaire : UTC)."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(resolve_timezone(timezone_name)).date()


def _utc_bound(day: date, timezone_name: str = DEFAULT_TIMEZONE) -> str:
    """Minuit du jour ``day`` dans le fuseau horaire ``timezone_name``, écrit en UTC au format de SQLite."""
    local_midnight = datetime.combine(day, time.min, tzinfo=resolve_timezone(timezone_name))
    return local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _local_timestamp(value: Any, timezone_name: str = DEFAULT_TIMEZONE) -> Any:
    """Horodatage UTC de la base écrit dans le fuseau horaire ``timezone_name`` (« AAAA-MM-JJ HH:MM:SS »).

    Une date seule est gardée telle quelle ; une valeur absente ou illisible est renvoyée inchangée.
    """
    moment = parse_timestamp(value)
    if isinstance(moment, datetime):
        return moment.astimezone(resolve_timezone(timezone_name)).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(moment, date):
        return moment.isoformat()
    return value


def _is_blank(value: Any) -> bool:
    """Vrai pour une valeur absente : ``None``, valeur manquante de pandas ou texte vide."""
    if isinstance(value, str):
        return not value.strip()
    return value is None or bool(pd.isna(value))


class ClientHistory:
    """Historique et habitudes de commande d'un client."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def get_client_preferences(self, client_name: str | None = None, *,
                               client_id: int | None = None) -> dict[str, Any] | None:
        """Résume les commandes d'un client : produits préférés, quantité moyenne, fréquence, total.

        Le client est désigné par son identifiant ``client_id`` ou, à défaut, par son nom exact
        (casse ignorée, même règle que :meth:`DatabaseManager.get_or_create_client`) : le nom
        « Snack » ne regroupe pas les commandes de « Snack Atlas ».

        Commandes prises en compte :

        - les commandes supprimées sont ignorées ;
        - nombre de commandes, première et dernière commande, fréquence : toutes les autres ;
        - produits préférés et quantité moyenne : commandes ni rejetées ni supprimées ;
        - total dépensé : commandes validées uniquement.
        """
        if client_id is not None:
            condition, params = "c.client_id = ?", (client_id,)
        elif client_name and client_name.strip():
            condition, params = "LOWER(cl.nom) = LOWER(?)", (client_name.strip(),)
        else:
            return None

        cursor = self.db.connection.cursor()
        cursor.execute(f"""
            SELECT c.*, cl.nom as client_nom, p.type as produit_type
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE {condition} AND {_not_deleted("c.")}
            ORDER BY c.created_at DESC
        """, params)
        orders = [dict(row) for row in cursor.fetchall()]

        if not orders:
            return None

        kept = [order for order in orders if order.get("statut") != REJECTED_STATUS]
        return {
            "client_name": orders[0].get("client_nom") or client_name,
            "total_orders": len(orders),
            "first_order": orders[-1]["created_at"],
            "last_order": orders[0]["created_at"],
            "favorite_products": self._get_favorite_products(kept),
            "average_quantity": self._get_average_quantity(kept),
            "typical_order": self._get_typical_order(orders),
            "order_frequency_days": self._calculate_order_frequency(orders),
            "total_spent": sum(o.get("prix_total") or 0 for o in orders if o.get("statut") == VALIDATED_STATUS),
        }

    def _get_favorite_products(self, orders: list[dict[str, Any]]) -> dict[str, int]:
        product_counts: dict[str, int] = defaultdict(int)
        for order in orders:
            if order.get("produit_type"):
                product_counts[order["produit_type"]] += 1
        return dict(sorted(product_counts.items(), key=lambda item: item[1], reverse=True))

    def _get_average_quantity(self, orders: list[dict[str, Any]]) -> float:
        quantities = [o.get("quantite") or 0 for o in orders if o.get("quantite")]
        return sum(quantities) / len(quantities) if quantities else 0

    def _get_typical_order(self, orders: list[dict[str, Any]]) -> dict[str, Any] | None:
        """La commande type est la plus récente commande validée."""
        for order in orders:
            if order.get("statut") == "validee":
                return {
                    "produit_type": order.get("produit_type"),
                    "nature_produit": order.get("nature_produit"),
                    "quantite": order.get("quantite"),
                    "unite": order.get("unite"),
                }
        return None

    def _calculate_order_frequency(self, orders: list[dict[str, Any]]) -> float | None:
        """Nombre moyen de jours entre deux commandes."""
        if len(orders) < 2:
            return None

        dates = []
        for order in orders:
            created_at = order.get("created_at")
            if not created_at:
                continue
            try:
                dates.append(datetime.fromisoformat(str(created_at).replace("Z", "")))
            except ValueError:
                continue

        if len(dates) < 2:
            return None

        dates.sort(reverse=True)
        gaps = [(dates[i] - dates[i + 1]).days for i in range(len(dates) - 1)]
        return sum(gaps) / len(gaps) if gaps else None

    def suggest_reorder(self, client_name: str | None, partial_order_data: dict[str, Any], *,
                        client_id: int | None = None) -> dict[str, Any]:
        """Complète une commande partielle avec la commande type du client (désigné comme dans
        :meth:`get_client_preferences`)."""
        preferences = self.get_client_preferences(client_name, client_id=client_id)
        if not preferences or not preferences.get("typical_order"):
            return partial_order_data

        typical = preferences["typical_order"]
        for order_field, typical_field in (("type_produit", "produit_type"), ("nature_produit", "nature_produit"),
                                           ("quantite", "quantite"), ("unite", "unite")):
            if not partial_order_data.get(order_field) and typical.get(typical_field):
                partial_order_data[order_field] = typical[typical_field]

        partial_order_data["suggestion_source"] = "client_history"
        return partial_order_data


class Analytics:
    """Statistiques du tableau de bord.

    Args:
        db: base des commandes.
        timezone_name: fuseau horaire de l'entreprise, qui fixe les limites du mois en cours
            (``Settings.timezone`` ; Casablanca par défaut).
    """

    def __init__(self, db: DatabaseManager, timezone_name: str | None = None):
        self.db = db
        self.timezone_name = timezone_name or DEFAULT_TIMEZONE

    def get_dashboard_stats(self, now: datetime | None = None) -> dict[str, Any]:
        """Calcule les indicateurs du tableau de bord (volumes, chiffre d'affaires, tendances).

        - Les commandes supprimées ne sont comptées dans aucun indicateur.
        - ``total_orders`` compte toutes les autres commandes, ``orders_this_month`` celles reçues
          depuis le premier jour du mois en cours (fuseau horaire de l'entreprise).
        - Le chiffre d'affaires (``total_revenue``, ``avg_order_value``, ``total_spent`` des
          meilleurs clients, ``revenue`` des tendances mensuelles) ne porte que sur les commandes
          validées ; ``validated_count`` donne le nombre de commandes validées de chaque client.
        - Les quantités (``total_quantity``, ``avg_quantity``, ``total_qty`` par produit) ne
          portent que sur les commandes ni rejetées ni supprimées.

        Args:
            now: instant qui fixe le mois en cours ; par défaut l'heure actuelle.
        """
        cursor = self.db.connection.cursor()
        stats: dict[str, Any] = {}
        active = _not_deleted()
        month_start = _utc_bound(_local_today(now, self.timezone_name).replace(day=1), self.timezone_name)

        counts: dict[str, tuple[str, tuple[Any, ...]]] = {
            "total_orders": (f"SELECT COUNT(*) FROM commandes WHERE {active}", ()),
            "orders_this_month": (
                f"SELECT COUNT(*) FROM commandes WHERE {active} AND datetime(created_at) >= ?", (month_start,)
            ),
            "pending_orders": ("SELECT COUNT(*) FROM commandes WHERE statut = 'en_attente'", ()),
            "validated_orders": ("SELECT COUNT(*) FROM commandes WHERE statut = 'validee'", ()),
            "rejected_orders": ("SELECT COUNT(*) FROM commandes WHERE statut = 'rejetee'", ()),
            "total_clients": ("SELECT COUNT(*) FROM clients", ()),
        }
        for key, (query, params) in counts.items():
            cursor.execute(query, params)
            stats[key] = cursor.fetchone()[0]

        cursor.execute(
            "SELECT SUM(prix_total), AVG(prix_total) FROM commandes WHERE prix_total IS NOT NULL AND statut = ?",
            (VALIDATED_STATUS,),
        )
        result = cursor.fetchone()
        stats["total_revenue"] = result[0] or 0
        stats["avg_order_value"] = result[1] or 0

        cursor.execute(
            f"SELECT SUM(quantite), AVG(quantite) FROM commandes "
            f"WHERE quantite IS NOT NULL AND {_not_rejected_nor_deleted()}"
        )
        result = cursor.fetchone()
        stats["total_quantity"] = result[0] or 0
        stats["avg_quantity"] = result[1] or 0

        cursor.execute(f"""
            SELECT p.type, COUNT(*) as count,
                   SUM(CASE WHEN {_not_rejected_nor_deleted("c.")} THEN c.quantite END) as total_qty
            FROM commandes c
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE p.type IS NOT NULL AND {_not_deleted("c.")}
            GROUP BY p.type
            ORDER BY count DESC
        """)
        stats["orders_by_product"] = [dict(row) for row in cursor.fetchall()]

        cursor.execute(f"SELECT statut, COUNT(*) as count FROM commandes WHERE {active} GROUP BY statut")
        stats["orders_by_status"] = {row["statut"]: row["count"] for row in cursor.fetchall()}

        cursor.execute(f"""
            SELECT cl.nom, COUNT(*) as order_count,
                   SUM(CASE WHEN c.statut = '{VALIDATED_STATUS}' THEN 1 ELSE 0 END) as validated_count,
                   SUM(CASE WHEN c.statut = '{VALIDATED_STATUS}' THEN c.prix_total END) as total_spent
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            WHERE {_not_deleted("c.")}
            GROUP BY cl.id
            ORDER BY order_count DESC
            LIMIT 10
        """)
        stats["top_clients"] = [dict(row) for row in cursor.fetchall()]

        cursor.execute(f"""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM commandes
            WHERE created_at >= datetime('now', '-30 days') AND {active}
            GROUP BY DATE(created_at)
            ORDER BY date
        """)
        stats["daily_orders"] = [dict(row) for row in cursor.fetchall()]

        cursor.execute(f"""
            SELECT strftime('%Y-%m', created_at) as month,
                   COUNT(*) as count,
                   SUM(CASE WHEN statut = '{VALIDATED_STATUS}' THEN prix_total END) as revenue
            FROM commandes
            WHERE {active}
            GROUP BY strftime('%Y-%m', created_at)
            ORDER BY month DESC
            LIMIT 12
        """)
        stats["monthly_trends"] = [dict(row) for row in cursor.fetchall()]

        return stats

    def get_orders_dataframe(self) -> pd.DataFrame:
        """Renvoie toutes les commandes (avec client et produit) dans un tableau pandas."""
        cursor = self.db.connection.cursor()
        cursor.execute("""
            SELECT c.*, cl.nom as client_nom, p.type as produit_type
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
        """)
        columns = [description[0] for description in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


class AlertSystem:
    """Alertes sur les commandes en attente : urgentes, volumineuses, anciennes ou douteuses."""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.alerts: list[dict[str, Any]] = []

    def check_alerts(self) -> list[dict[str, Any]]:
        """Évalue toutes les règles d'alerte et renvoie la liste des alertes."""
        self.alerts = []
        self._check_urgent_orders()
        self._check_high_quantity_orders()
        self._check_pending_orders_age()
        self._check_suspicious_orders()
        return self.alerts

    def _check_urgent_orders(self) -> None:
        cursor = self.db.connection.cursor()
        cursor.execute("""
            SELECT c.*, cl.nom as client_nom
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            WHERE (c.email_subject LIKE '%urgent%' OR c.informations_supplementaires LIKE '%urgent%')
            AND c.statut = 'en_attente'
        """)
        for row in cursor.fetchall():
            self.alerts.append({
                "type": "urgent",
                "severity": "high",
                "message": f"Commande urgente en attente: {row['numero_commande'] or 'N/A'} - {row['client_nom']}",
                "order_id": row["id"],
            })

    def _check_high_quantity_orders(self, threshold: int = HIGH_QUANTITY_THRESHOLD) -> None:
        cursor = self.db.connection.cursor()
        cursor.execute("""
            SELECT c.*, cl.nom as client_nom
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            WHERE c.quantite > ? AND c.statut = 'en_attente'
        """, (threshold,))
        for row in cursor.fetchall():
            self.alerts.append({
                "type": "high_quantity",
                "severity": "medium",
                "message": f"Commande avec grande quantité ({row['quantite']}): {row['client_nom']}",
                "order_id": row["id"],
            })

    def _check_pending_orders_age(self, max_hours: int = PENDING_MAX_HOURS) -> None:
        cursor = self.db.connection.cursor()
        cursor.execute("""
            SELECT c.*, cl.nom as client_nom,
                   (julianday('now') - julianday(c.created_at)) * 24 as hours_pending
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            WHERE c.statut = 'en_attente'
            AND (julianday('now') - julianday(c.created_at)) * 24 > ?
        """, (max_hours,))
        for row in cursor.fetchall():
            self.alerts.append({
                "type": "pending_too_long",
                "severity": "medium",
                "message": f"Commande en attente depuis {int(row['hours_pending'])}h: {row['client_nom']}",
                "order_id": row["id"],
            })

    def _check_suspicious_orders(self) -> None:
        """Nouveau client avec une grosse commande, ou extraction de faible confiance."""
        cursor = self.db.connection.cursor()
        cursor.execute("""
            SELECT c.*, cl.nom as client_nom,
                   (SELECT COUNT(*) FROM commandes WHERE client_id = c.client_id) as client_order_count
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            WHERE c.statut = 'en_attente'
            AND c.quantite > ?
            AND (SELECT COUNT(*) FROM commandes WHERE client_id = c.client_id) <= 1
        """, (NEW_CLIENT_LARGE_ORDER,))
        for row in cursor.fetchall():
            self.alerts.append({
                "type": "suspicious",
                "severity": "high",
                "message": f"⚠️ Nouveau client avec grande commande: {row['client_nom']} ({row['quantite']} unités)",
                "order_id": row["id"],
            })

        cursor.execute("""
            SELECT c.*, cl.nom as client_nom
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            WHERE c.confiance < ? AND c.statut = 'en_attente'
        """, (LOW_CONFIDENCE_THRESHOLD,))
        for row in cursor.fetchall():
            self.alerts.append({
                "type": "low_confidence",
                "severity": "medium",
                "message": f"Commande avec faible confiance ({row['confiance']}%): {row['client_nom']}",
                "order_id": row["id"],
            })


SAGE_SHEET_NAME = "Commandes SAGE X3"
"""Feuille au format d'import SAGE X3 (quantités en unités de stock)."""

CONVERSION_SHEET_NAME = "Unités à convertir"
"""Feuille des commandes validées dont la quantité n'est pas exprimée en unités de stock."""

STOCK_UNIT = "US"
"""Unité de stock de SAGE X3 (une pièce, un sac)."""

STOCK_UNIT_ALIASES = frozenset({"us", "pièces", "pièce", "pieces", "piece"})
"""Unités équivalentes à l'unité de stock (« pièces » est l'unité canonique de l'extraction)."""

CONVERSION_ACTION = "Convertir la quantité en unités de stock (US) avant l'import dans SAGE X3."

SAGE_STATUS_MESSAGE = (
    "L'export SAGE X3 ne contient que les commandes validées. "
    "Les commandes d'un autre statut s'exportent en CSV ou en Excel (colonnes de la base)."
)

# Colonnes de quantité du format SAGE X3, renommées dans la feuille des unités à convertir, où la
# quantité n'est pas encore exprimée en unités de stock.
_SAGE_QUANTITY_HEADERS: dict[str, str] = {
    "Qtée commandée US": "Qtée commandée",
    "Qté Livrée US": "Qté livrée",
    "Reste à livrer US": "Reste à livrer",
    "Qtée facturée US": "Qtée facturée",
}

_SAGE_EXPORT_QUERY = """
    SELECT
        COALESCE(NULLIF(TRIM(c.numero_commande), ''), 'CMD-' || c.id) as "Numéro commande",
        c.ligne_commande as "Ligne commande",
        c.site_vente as "Site de vente",
        c.code_client as "Code client",
        cl.nom as "Raison sociale",
        c.code_article as "Code article",
        c.nature_produit as "Désignation",
        c.date_commande as "Date commande",
        c.date_livraison as "Date livraison prévue",
        c.quantite as "Qtée commandée US",
        COALESCE(NULLIF(TRIM(c.unite), ''), 'US') as "Unité",
        c.quantite_livree as "Qté Livrée US",
        c.reste_a_livrer as "Reste à livrer US",
        c.quantite_facturee as "Qtée facturée US",
        c.commercial as "Commercial",
        c.type_sac as "Type SAC",
        c.format_sac as "Format LAR.PRE.LON",
        c.type_papier as "Type de papier",
        c.grammage as "Grammage",
        c.laize as "Laize",
        c.impression_client as "Impression client",
        c.prix_unitaire as "Prix unitaire",
        c.prix_total as "Prix total",
        c.devise as "Devise",
        c.statut as "Statut",
        c.confiance as "Confiance IA (%)",
        c.source as "Source",
        c.created_at as "Date création"
    FROM commandes c
    LEFT JOIN clients cl ON c.client_id = cl.id
    LEFT JOIN produits p ON c.produit_id = p.id
"""

_STANDARD_EXPORT_QUERY = """
    SELECT
        c.id,
        c.numero_commande,
        c.ligne_commande,
        c.site_vente,
        c.code_client,
        cl.nom as client,
        c.code_article,
        p.type as produit,
        c.nature_produit,
        c.quantite,
        c.unite,
        c.quantite_livree,
        c.reste_a_livrer,
        c.quantite_facturee,
        c.prix_unitaire,
        c.prix_total,
        c.devise,
        c.date_commande,
        c.date_livraison,
        c.commercial,
        c.type_sac,
        c.format_sac,
        c.type_papier,
        c.grammage,
        c.laize,
        c.impression_client,
        c.statut,
        c.confiance,
        c.source,
        c.created_at
    FROM commandes c
    LEFT JOIN clients cl ON c.client_id = cl.id
    LEFT JOIN produits p ON c.produit_id = p.id
"""

_ISO_DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class ExportFilterError(ValueError):
    """Filtre d'export refusé (statut inconnu ou non exportable, date mal écrite, période inversée).

    Le message est rédigé pour l'utilisateur : la route d'export l'affiche tel quel.
    """


def _shown(value: Any) -> str:
    """Valeur reçue, raccourcie, pour un message d'erreur."""
    text = str(value).strip()
    return text if len(text) <= 40 else text[:39] + "…"


def _parse_day(value: Any, label: str) -> date:
    """Jour « AAAA-MM-JJ » d'un champ de date du formulaire d'export."""
    text = str(value).strip()
    try:
        if not _ISO_DAY_RE.fullmatch(text):
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise ExportFilterError(f"{label} invalide : « {_shown(value)} » (format attendu : AAAA-MM-JJ).") from None


def _filter_clause(filters: Mapping[str, Any] | None,
                   timezone_name: str = DEFAULT_TIMEZONE) -> tuple[str, list[Any]]:
    """Construit la clause WHERE paramétrée des exports (statut, client, période).

    - ``status`` : un statut de :data:`ORDER_STATUSES` ; sans statut, toutes les commandes sauf
      les commandes supprimées.
    - ``date_from`` / ``date_to`` : jours « AAAA-MM-JJ » inclus, dans le fuseau horaire
      ``timezone_name`` ; ``date_to`` inclut toute la journée (commandes reçues jusqu'à 23:59:59).
    - ``client_id`` : identifiant du client.

    Raises:
        ExportFilterError: statut inconnu, date mal écrite ou date de début postérieure à la date de fin.
    """
    filters = filters or {}
    conditions: list[str] = []
    params: list[Any] = []

    status = filters.get("status")
    if status:
        if status not in ORDER_STATUSES:
            raise ExportFilterError(
                f"Statut inconnu : « {_shown(status)} ». Valeurs possibles : {', '.join(ORDER_STATUSES)}."
            )
        conditions.append("c.statut = ?")
        params.append(status)
    else:
        conditions.append(_not_deleted("c."))

    if filters.get("client_id"):
        conditions.append("c.client_id = ?")
        params.append(filters["client_id"])

    day_from = _parse_day(filters["date_from"], "Date de début") if filters.get("date_from") else None
    day_to = _parse_day(filters["date_to"], "Date de fin") if filters.get("date_to") else None
    if day_from and day_to and day_from > day_to:
        raise ExportFilterError(
            f"La date de début ({day_from.strftime('%d/%m/%Y')}) est postérieure "
            f"à la date de fin ({day_to.strftime('%d/%m/%Y')})."
        )
    # datetime() ramène toute écriture ISO (« T » ou espace entre la date et l'heure) au format
    # « AAAA-MM-JJ HH:MM:SS » comparé aux bornes, elles aussi en UTC.
    if day_from:
        conditions.append("datetime(c.created_at) >= ?")
        params.append(_utc_bound(day_from, timezone_name))
    if day_to:
        conditions.append("datetime(c.created_at) < ?")
        params.append(_utc_bound(day_to + timedelta(days=1), timezone_name))

    return " WHERE " + " AND ".join(conditions), params


def _sage_filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Filtres de l'export SAGE X3 : statut ``validee`` imposé, tout autre statut refusé."""
    sage_filters = dict(filters or {})
    status = sage_filters.get("status")
    if status and status != VALIDATED_STATUS:
        raise ExportFilterError(SAGE_STATUS_MESSAGE)
    sage_filters["status"] = VALIDATED_STATUS
    return sage_filters


def is_stock_unit(unit: Any) -> bool:
    """Vrai pour l'unité de stock de SAGE X3 ou une unité équivalente (pièces) ; vide vaut « US »."""
    return _is_blank(unit) or str(unit).strip().lower() in STOCK_UNIT_ALIASES


def _prepare_sage_frame(frame: pd.DataFrame, timezone_name: str = DEFAULT_TIMEZONE) -> pd.DataFrame:
    """Dates en heure de l'entreprise ; date de commande absente remplacée par le jour de réception."""
    frame = frame.copy()
    frame["Date création"] = frame["Date création"].map(lambda value: _local_timestamp(value, timezone_name))
    received_day = frame["Date création"].map(lambda value: value[:10] if isinstance(value, str) else None)
    missing = frame["Date commande"].map(_is_blank).astype(bool)
    frame["Date commande"] = frame["Date commande"].where(~missing, received_day)
    return frame


def _split_by_unit(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sépare les commandes en unités de stock de celles qu'il faut convertir avant l'import."""
    in_stock_unit = frame["Unité"].map(is_stock_unit).astype(bool)
    importable = frame[in_stock_unit].copy()
    importable["Unité"] = STOCK_UNIT
    to_convert = frame[~in_stock_unit].rename(columns=_SAGE_QUANTITY_HEADERS)
    to_convert["Action requise"] = CONVERSION_ACTION
    return importable, to_convert


def _style_sheet(worksheet: Any, column_count: int) -> None:
    """En-têtes colorés et largeur des colonnes adaptée au contenu (30 caractères au plus)."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin")
    thin_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col_num in range(1, column_count + 1):
        cell = worksheet.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = thin_border

    for column in worksheet.columns:
        max_length = max((len(str(cell.value)) for cell in column if cell.value is not None), default=0)
        worksheet.column_dimensions[column[0].column_letter].width = min(max_length + 2, 30)


def _write_sheet(writer: pd.ExcelWriter, frame: pd.DataFrame, sheet_name: str, *, styled: bool = False) -> None:
    """Écrit ``frame`` dans une feuille ; les textes qui ressemblent à une formule restent du texte."""
    frame.to_excel(writer, index=False, sheet_name=sheet_name)
    worksheet = writer.sheets[sheet_name]
    protected = protect_text_cells(worksheet)
    if protected:
        logger.warning("Export Excel : %d cellule(s) commençant comme une formule écrite(s) comme du texte "
                       "(feuille « %s »)", protected, sheet_name)
    if styled:
        _style_sheet(worksheet, len(frame.columns))


class ReportGenerator:
    """Exports des commandes (Excel standard, Excel SAGE X3, CSV) et rapport PDF simple.

    Sans chemin explicite, chaque export est écrit dans un fichier au nom unique du dossier
    ``Settings.export_dir`` : deux exports simultanés n'écrivent jamais dans le même fichier.
    L'appelant supprime ce fichier une fois envoyé (voir :mod:`tecpap.web.routes.exports`).
    Les périodes des filtres et les dates exportées suivent le fuseau horaire ``Settings.timezone``.

    Args:
        db: base des commandes.
        settings: configuration ; dossier des exports ``Settings.export_dir``.
    """

    def __init__(self, db: DatabaseManager, settings: Settings | None = None):
        self.db = db
        self.settings = settings or load_settings()
        self.export_dir = self.settings.export_dir
        self.timezone_name = _settings_timezone(self.settings)

    def new_export_path(self, default_name: str) -> str:
        """Fichier vide au nom unique dans le dossier des exports (« rapport.pdf » → « rapport_<suffixe>.pdf »)."""
        return unique_export_path(self.export_dir, default_name)

    @contextmanager
    def _export_target(self, filepath: str | None, default_name: str) -> Iterator[str]:
        """Chemin du fichier à écrire ; un fichier temporaire créé ici est supprimé en cas d'échec."""
        if filepath:
            directory = os.path.dirname(filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)
            yield filepath
            return
        path = self.new_export_path(default_name)
        try:
            yield path
        except BaseException:
            try:
                os.remove(path)
            except OSError:
                logger.warning("Fichier d'export temporaire impossible à supprimer : %s", path)
            raise

    def _query_dataframe(self, query: str, filters: Mapping[str, Any] | None) -> pd.DataFrame:
        where, params = _filter_clause(filters, self.timezone_name)
        cursor = self.db.connection.cursor()
        cursor.execute(query + where + " ORDER BY c.created_at DESC", params)
        columns = [description[0] for description in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)

    def _standard_dataframe(self, filters: Mapping[str, Any] | None) -> pd.DataFrame:
        frame = self._query_dataframe(_STANDARD_EXPORT_QUERY, filters)
        frame["created_at"] = frame["created_at"].map(lambda value: _local_timestamp(value, self.timezone_name))
        return frame

    def export_to_excel_sage(self, filepath: str | None = None, filters: Mapping[str, Any] | None = None) -> str:
        """Exporte les commandes validées au format d'import SAGE X3 (en-têtes de colonnes de l'ERP).

        - Seules les commandes validées sont exportées ; ``filters`` peut restreindre la période
          (``date_from``, ``date_to``) mais pas demander un autre statut.
        - Une commande sans numéro reçoit la référence donnée au client (« CMD-<id> ») ; sans date
          de commande, la date de réception (heure de l'entreprise) est utilisée.
        - La feuille d'import ne contient que des quantités en unités de stock (US). Les commandes
          en cartons, paquets, kg… sont placées dans une seconde feuille, « Unités à convertir »,
          pour être converties avant l'import : elles ne peuvent pas entrer dans l'ERP comme un
          nombre de pièces.

        Raises:
            ExportFilterError: statut autre que ``validee``, date mal écrite ou période inversée.
        """
        frame = _prepare_sage_frame(self._query_dataframe(_SAGE_EXPORT_QUERY, _sage_filters(filters)),
                                    self.timezone_name)
        importable, to_convert = _split_by_unit(frame)

        with self._export_target(filepath, "commandes_sage.xlsx") as path:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                _write_sheet(writer, importable, SAGE_SHEET_NAME, styled=True)
                if not to_convert.empty:
                    _write_sheet(writer, to_convert, CONVERSION_SHEET_NAME, styled=True)
        if not to_convert.empty:
            logger.warning(
                "Export SAGE X3 : %d commande(s) validée(s) hors unité de stock placée(s) dans la feuille « %s » : %s",
                len(to_convert), CONVERSION_SHEET_NAME, ", ".join(str(ref) for ref in to_convert["Numéro commande"]),
            )
        return path

    def export_to_excel(self, filepath: str | None = None, filters: Mapping[str, Any] | None = None) -> str:
        """Exporte les commandes dans un classeur Excel (colonnes de la base).

        Raises:
            ExportFilterError: statut inconnu, date mal écrite ou période inversée.
        """
        frame = self._standard_dataframe(filters)
        with self._export_target(filepath, "commandes.xlsx") as path:
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                _write_sheet(writer, frame, "Commandes")
        return path

    def export_to_csv(self, filepath: str | None = None, filters: Mapping[str, Any] | None = None) -> str:
        """Exporte les commandes en CSV (mêmes colonnes que l'export Excel, encodage UTF-8 avec BOM).

        Raises:
            ExportFilterError: statut inconnu, date mal écrite ou période inversée.
        """
        frame = neutralize_csv_frame(self._standard_dataframe(filters))
        with self._export_target(filepath, "commandes.csv") as path:
            frame.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def generate_pdf_report(self, filepath: str | None = None, period: str = "month") -> str:
        """Génère un rapport PDF court : indicateurs principaux et meilleurs clients.

        Le chiffre d'affaires ne compte que les commandes validées (voir :meth:`Analytics.get_dashboard_stats`).
        """
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        from tecpap.reporting.pdf_report import pdf_safe_text

        stats = Analytics(self.db, self.timezone_name).get_dashboard_stats()
        styles = getSampleStyleSheet()
        elements: list[Any] = []

        title_style = ParagraphStyle("CustomTitle", parent=styles["Heading1"], fontSize=24, spaceAfter=30)
        elements.append(Paragraph("Rapport des Commandes", title_style))
        elements.append(Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
        elements.append(Spacer(1, 20))

        summary_data = [
            ["Métrique", "Valeur"],
            ["Total Commandes", str(stats["total_orders"])],
            ["Commandes Validées", str(stats["validated_orders"])],
            ["Commandes En Attente", str(stats["pending_orders"])],
            ["Commandes Rejetées", str(stats["rejected_orders"])],
            ["Total Clients", str(stats["total_clients"])],
            ["Chiffre d'affaires validé", pdf_safe_text(format_amount(stats["total_revenue"]))],
            ["Valeur moyenne d'une commande validée", pdf_safe_text(format_amount(stats["avg_order_value"]))],
        ]
        table = Table(summary_data, colWidths=[8 * cm, 6 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#667eea")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f3f4f6")),
            ("GRID", (0, 0), (-1, -1), 1, colors.white),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 30))

        elements.append(Paragraph("Top Clients", styles["Heading2"]))
        if stats["top_clients"]:
            client_data = [["Client", "Commandes", "Chiffre d'affaires validé"]]
            for client in stats["top_clients"][:5]:
                client_data.append([
                    client["nom"] or "N/A",
                    str(client["order_count"]),
                    pdf_safe_text(format_amount(client["total_spent"] or 0)),
                ])
            client_table = Table(client_data, colWidths=[7 * cm, 4 * cm, 4 * cm])
            client_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#764ba2")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, colors.lightgrey),
            ]))
            elements.append(client_table)

        with self._export_target(filepath, "rapport.pdf") as path:
            SimpleDocTemplate(path, pagesize=A4).build(elements)
        return path


class AIPredictor:
    """Prévisions sur le comportement d'un client et classification de la priorité des emails.

    Sans clé d'API, la classification renvoie une priorité moyenne par défaut.

    Args:
        db: base des commandes.
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
        llm: client des modèles de langue ; par défaut construit depuis la configuration.
    """

    DEFAULT_PRIORITY = {"priority": "medium", "is_purchase_order": False, "is_urgent": False, "confidence": 50}

    def __init__(self, db: DatabaseManager, settings: Settings | None = None, llm: LLMClient | None = None):
        self.db = db
        self.settings = settings or load_settings()
        self.llm = llm if llm is not None else LLMClient.from_settings(self.settings)

    def predict_client_behavior(self, client_name: str | None = None, *,
                                client_id: int | None = None) -> dict[str, Any] | None:
        """Estime la prochaine commande d'un client à partir de son historique (règles fixes).

        Le client est désigné comme dans :meth:`ClientHistory.get_client_preferences` ; son
        segment de valeur (``client_value``) dépend du total de ses commandes validées.
        """
        preferences = ClientHistory(self.db).get_client_preferences(client_name, client_id=client_id)
        if not preferences:
            return None

        total_spent = preferences["total_spent"]
        if total_spent > HIGH_VALUE_CLIENT:
            client_value = "high"
        elif total_spent > MEDIUM_VALUE_CLIENT:
            client_value = "medium"
        else:
            client_value = "low"

        prediction = {
            "client": preferences["client_name"],
            "predicted_next_order_days": None,
            "predicted_quantity": preferences["average_quantity"],
            "likely_products": list(preferences["favorite_products"].keys())[:3],
            "client_value": client_value,
        }
        if preferences["order_frequency_days"]:
            prediction["predicted_next_order_days"] = int(preferences["order_frequency_days"])
        return prediction

    def classify_email_priority(self, email_subject: str, email_body: str) -> dict[str, Any]:
        """Demande au modèle léger la priorité d'un email ; valeur par défaut sans clé ou en cas d'erreur.

        La réponse est contrôlée : seules les clés de :attr:`DEFAULT_PRIORITY` sont gardées et
        une priorité inconnue est remplacée par ``"medium"``.
        """
        if not self.llm.available:
            return dict(self.DEFAULT_PRIORITY)

        result = self.llm.chat_json(
            model=self.settings.model_light,
            system=PRIORITY_SYSTEM_PROMPT,
            user=build_priority_prompt(email_subject or "", email_body or ""),
            temperature=self.settings.llm_temperature,
            max_tokens=200,
        )
        if result is None:
            logger.warning("Classification de priorité en échec : priorité moyenne par défaut")
            return dict(self.DEFAULT_PRIORITY)

        classification = dict(self.DEFAULT_PRIORITY)
        classification.update({key: result[key] for key in self.DEFAULT_PRIORITY if key in result})
        if classification["priority"] not in PRIORITY_LEVELS:
            classification["priority"] = self.DEFAULT_PRIORITY["priority"]
        return classification

    def detect_anomalies(self, order_data: dict[str, Any]) -> list[dict[str, str]]:
        """Signale une quantité ou un prix unitaire très éloignés de la moyenne des commandes."""
        anomalies = []
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT AVG(quantite), MAX(quantite) FROM commandes WHERE quantite IS NOT NULL")
        avg_qty, _max_qty = cursor.fetchone()

        if order_data.get("quantite") and avg_qty and order_data["quantite"] > avg_qty * 3:
            anomalies.append({
                "type": "quantity_anomaly",
                "message": f"Quantité ({order_data['quantite']}) très supérieure à la moyenne ({avg_qty:.0f})",
            })

        if order_data.get("prix_total") and order_data.get("quantite"):
            unit_price = order_data["prix_total"] / order_data["quantite"]
            cursor.execute("""
                SELECT AVG(prix_total / quantite)
                FROM commandes
                WHERE prix_total IS NOT NULL AND quantite > 0
            """)
            avg_unit = cursor.fetchone()[0]
            if avg_unit and (unit_price < avg_unit * 0.3 or unit_price > avg_unit * 3):
                anomalies.append({
                    "type": "price_anomaly",
                    "message": f"Prix unitaire ({unit_price:.2f}) inhabituel par rapport à la moyenne ({avg_unit:.2f})",
                })

        return anomalies
