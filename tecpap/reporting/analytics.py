"""Statistiques, alertes, historique client, prévisions simples et exports (Excel, CSV, PDF).

Les alertes et les prévisions reposent sur des règles fixes calculées en SQL (seuils de
quantité, ancienneté des commandes en attente, confiance de l'extraction) ; seule la
classification de priorité d'un email fait appel à un modèle de langue, et elle a une valeur
par défaut quand aucune clé d'API n'est configurée.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from tecpap.config import Settings, load_settings
from tecpap.extraction.prompts import PRIORITY_SYSTEM_PROMPT, build_priority_prompt
from tecpap.llm.client import LLMClient

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


class ClientHistory:
    """Historique et habitudes de commande d'un client."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def get_client_preferences(self, client_name: str) -> dict[str, Any] | None:
        """Résume les commandes d'un client : produits préférés, quantité moyenne, fréquence, total."""
        cursor = self.db.connection.cursor()
        cursor.execute("""
            SELECT c.*, p.type as produit_type
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE cl.nom LIKE ?
            ORDER BY c.created_at DESC
        """, (f"%{client_name}%",))
        orders = [dict(row) for row in cursor.fetchall()]

        if not orders:
            return None

        return {
            "client_name": client_name,
            "total_orders": len(orders),
            "first_order": orders[-1]["created_at"],
            "last_order": orders[0]["created_at"],
            "favorite_products": self._get_favorite_products(orders),
            "average_quantity": self._get_average_quantity(orders),
            "typical_order": self._get_typical_order(orders),
            "order_frequency_days": self._calculate_order_frequency(orders),
            "total_spent": sum(o.get("prix_total") or 0 for o in orders),
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

    def suggest_reorder(self, client_name: str, partial_order_data: dict[str, Any]) -> dict[str, Any]:
        """Complète une commande partielle avec la commande type du client."""
        preferences = self.get_client_preferences(client_name)
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
    """Statistiques du tableau de bord."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def get_dashboard_stats(self) -> dict[str, Any]:
        """Calcule les indicateurs du tableau de bord (volumes, chiffre d'affaires, tendances)."""
        cursor = self.db.connection.cursor()
        stats: dict[str, Any] = {}

        counts = {
            "total_orders": "SELECT COUNT(*) FROM commandes",
            "pending_orders": "SELECT COUNT(*) FROM commandes WHERE statut = 'en_attente'",
            "validated_orders": "SELECT COUNT(*) FROM commandes WHERE statut = 'validee'",
            "rejected_orders": "SELECT COUNT(*) FROM commandes WHERE statut = 'rejetee'",
            "total_clients": "SELECT COUNT(*) FROM clients",
        }
        for key, query in counts.items():
            cursor.execute(query)
            stats[key] = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(prix_total), AVG(prix_total) FROM commandes WHERE prix_total IS NOT NULL")
        result = cursor.fetchone()
        stats["total_revenue"] = result[0] or 0
        stats["avg_order_value"] = result[1] or 0

        cursor.execute("SELECT SUM(quantite), AVG(quantite) FROM commandes WHERE quantite IS NOT NULL")
        result = cursor.fetchone()
        stats["total_quantity"] = result[0] or 0
        stats["avg_quantity"] = result[1] or 0

        cursor.execute("""
            SELECT p.type, COUNT(*) as count, SUM(c.quantite) as total_qty
            FROM commandes c
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE p.type IS NOT NULL
            GROUP BY p.type
            ORDER BY count DESC
        """)
        stats["orders_by_product"] = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT statut, COUNT(*) as count FROM commandes GROUP BY statut")
        stats["orders_by_status"] = {row["statut"]: row["count"] for row in cursor.fetchall()}

        cursor.execute("""
            SELECT cl.nom, COUNT(*) as order_count, SUM(c.prix_total) as total_spent
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            GROUP BY cl.id
            ORDER BY order_count DESC
            LIMIT 10
        """)
        stats["top_clients"] = [dict(row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM commandes
            WHERE created_at >= datetime('now', '-30 days')
            GROUP BY DATE(created_at)
            ORDER BY date
        """)
        stats["daily_orders"] = [dict(row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT strftime('%Y-%m', created_at) as month,
                   COUNT(*) as count,
                   SUM(prix_total) as revenue
            FROM commandes
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


_SAGE_EXPORT_QUERY = """
    SELECT
        c.numero_commande as "Numéro commande",
        c.ligne_commande as "Ligne commande",
        c.site_vente as "Site de vente",
        c.code_client as "Code client",
        cl.nom as "Raison sociale",
        c.code_article as "Code article",
        c.nature_produit as "Désignation",
        c.date_commande as "Date commande",
        c.date_livraison as "Date livraison prévue",
        c.quantite as "Qtée commandée US",
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


def _filter_clause(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
    """Construit la clause WHERE paramétrée des exports (statut, client, période)."""
    if not filters:
        return "", []
    conditions = []
    params: list[Any] = []
    if filters.get("status"):
        conditions.append("c.statut = ?")
        params.append(filters["status"])
    if filters.get("client_id"):
        conditions.append("c.client_id = ?")
        params.append(filters["client_id"])
    if filters.get("date_from"):
        conditions.append("c.created_at >= ?")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        conditions.append("c.created_at <= ?")
        params.append(filters["date_to"])
    if not conditions:
        return "", []
    return " WHERE " + " AND ".join(conditions), params


class ReportGenerator:
    """Exports des commandes (Excel standard, Excel SAGE X3, CSV) et rapport PDF simple.

    Args:
        db: base des commandes.
        settings: configuration ; les fichiers sont écrits par défaut dans ``Settings.export_dir``.
    """

    def __init__(self, db: DatabaseManager, settings: Settings | None = None):
        self.db = db
        self.settings = settings or load_settings()
        self.export_dir = self.settings.export_dir

    def _path(self, filepath: str | None, default_name: str) -> str:
        path = filepath or os.path.join(self.export_dir, default_name)
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        return path

    def _query_dataframe(self, query: str, filters: dict[str, Any] | None) -> pd.DataFrame:
        where, params = _filter_clause(filters)
        cursor = self.db.connection.cursor()
        cursor.execute(query + where + " ORDER BY c.created_at DESC", params)
        columns = [description[0] for description in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)

    def export_to_excel_sage(self, filepath: str | None = None, filters: dict[str, Any] | None = None) -> str:
        """Exporte les commandes au format d'import SAGE X3 (en-têtes de colonnes de l'ERP)."""
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        path = self._path(filepath, "commandes_sage.xlsx")
        df = self._query_dataframe(_SAGE_EXPORT_QUERY, filters)

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Commandes SAGE X3")
            worksheet = writer.sheets["Commandes SAGE X3"]

            header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
            header_font = Font(color="FFFFFF", bold=True, size=10)
            thin = Side(style="thin")
            thin_border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for col_num in range(1, len(df.columns) + 1):
                cell = worksheet.cell(row=1, column=col_num)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", wrap_text=True)
                cell.border = thin_border

            for column in worksheet.columns:
                max_length = max((len(str(cell.value)) for cell in column if cell.value is not None), default=0)
                worksheet.column_dimensions[column[0].column_letter].width = min(max_length + 2, 30)

        return path

    def export_to_excel(self, filepath: str | None = None, filters: dict[str, Any] | None = None) -> str:
        """Exporte les commandes dans un classeur Excel (colonnes de la base)."""
        path = self._path(filepath, "commandes.xlsx")
        self._query_dataframe(_STANDARD_EXPORT_QUERY, filters).to_excel(path, index=False, sheet_name="Commandes")
        return path

    def export_to_csv(self, filepath: str | None = None, filters: dict[str, Any] | None = None) -> str:
        """Exporte les commandes en CSV (mêmes colonnes que l'export Excel, encodage UTF-8 avec BOM)."""
        path = self._path(filepath, "commandes.csv")
        self._query_dataframe(_STANDARD_EXPORT_QUERY, filters).to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def generate_pdf_report(self, filepath: str | None = None, period: str = "month") -> str:
        """Génère un rapport PDF court : indicateurs principaux et meilleurs clients."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        path = self._path(filepath, "rapport.pdf")
        doc = SimpleDocTemplate(path, pagesize=A4)
        styles = getSampleStyleSheet()
        elements: list[Any] = []

        title_style = ParagraphStyle("CustomTitle", parent=styles["Heading1"], fontSize=24, spaceAfter=30)
        elements.append(Paragraph("Rapport des Commandes", title_style))
        elements.append(Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
        elements.append(Spacer(1, 20))

        stats = Analytics(self.db).get_dashboard_stats()
        summary_data = [
            ["Métrique", "Valeur"],
            ["Total Commandes", str(stats["total_orders"])],
            ["Commandes Validées", str(stats["validated_orders"])],
            ["Commandes En Attente", str(stats["pending_orders"])],
            ["Commandes Rejetées", str(stats["rejected_orders"])],
            ["Total Clients", str(stats["total_clients"])],
            ["Chiffre d'Affaires Total", f"{stats['total_revenue']:.2f} MAD"],
            ["Valeur Moyenne Commande", f"{stats['avg_order_value']:.2f} MAD"],
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
            client_data = [["Client", "Commandes", "Total Dépensé"]]
            for client in stats["top_clients"][:5]:
                client_data.append([
                    client["nom"] or "N/A",
                    str(client["order_count"]),
                    f"{client['total_spent'] or 0:.2f} MAD",
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

        doc.build(elements)
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

    def predict_client_behavior(self, client_name: str) -> dict[str, Any] | None:
        """Estime la prochaine commande d'un client à partir de son historique (règles fixes)."""
        preferences = ClientHistory(self.db).get_client_preferences(client_name)
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
            "client": client_name,
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
