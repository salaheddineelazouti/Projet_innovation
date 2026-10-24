"""Pages HTML du tableau de bord : accueil, commandes, clients, statistiques, alertes, WhatsApp, SAGE X3."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, url_for

from tecpap.reporting.analytics import AIPredictor, AlertSystem, Analytics, ClientHistory
from tecpap.web.services import get_services

bp = Blueprint("pages", __name__)

TREND_DAYS = 7


@bp.route("/")
def index():
    """Tableau de bord : indicateurs, dernières commandes, meilleurs clients et produits, tendance sur 7 jours."""
    db = get_services().db
    stats = db.get_stats()
    recent_orders = db.get_all_orders()[:5]
    top_clients = db.get_top_clients(5)
    top_products = db.get_top_products(5)

    trend = {item["date"]: item["count"] for item in db.get_orders_trend(TREND_DAYS)}
    today = datetime.now().date()
    labels, data = [], []
    for days_ago in range(TREND_DAYS - 1, -1, -1):
        day = today - timedelta(days=days_ago)
        labels.append(day.strftime("%d/%m"))
        data.append(trend.get(day.strftime("%Y-%m-%d"), 0))

    return render_template(
        "index.html",
        stats=stats,
        recent_orders=recent_orders,
        top_clients=top_clients,
        top_products=top_products,
        trend_labels=json.dumps(labels),
        trend_data=json.dumps(data),
    )


@bp.route("/orders")
def orders_list():
    """Liste des commandes, filtrable par statut (``?status=en_attente``)."""
    status_filter = request.args.get("status", None)
    db = get_services().db
    orders = db.get_all_orders(status=status_filter) if status_filter else db.get_all_orders()
    return render_template("orders.html", orders=orders, current_filter=status_filter)


@bp.route("/orders/<int:order_id>")
def order_detail(order_id: int):
    """Fiche d'une commande, pour la vérification et la validation."""
    db = get_services().db
    order = db.get_order(order_id)
    if not order:
        return redirect(url_for("pages.orders_list"))
    return render_template("order_detail.html", order=order, products=db.get_all_products())


@bp.route("/process")
def process_page():
    """Page de lancement du traitement des emails."""
    return render_template("process.html")


@bp.route("/analytics")
def analytics_page():
    """Statistiques détaillées et alertes."""
    db = get_services().db
    stats = Analytics(db).get_dashboard_stats()
    alerts = AlertSystem(db).check_alerts()
    return render_template("analytics.html", stats=stats, alerts=alerts)


@bp.route("/clients")
def clients_page():
    """Liste des clients avec leurs habitudes de commande."""
    db = get_services().db
    clients = db.get_all_clients()
    history = ClientHistory(db)
    for client in clients:
        client["preferences"] = history.get_client_preferences(client["nom"])
    return render_template("clients.html", clients=clients)


@bp.route("/clients/<int:client_id>")
def client_detail(client_id: int):
    """Fiche client : historique des commandes et estimation de la prochaine commande."""
    services = get_services()
    cursor = services.db.connection.cursor()
    cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    if not row:
        return redirect(url_for("pages.clients_page"))

    client = dict(row)
    client["preferences"] = ClientHistory(services.db).get_client_preferences(client["nom"])

    cursor.execute("""
        SELECT c.*, p.type as produit_type
        FROM commandes c
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE c.client_id = ?
        ORDER BY c.created_at DESC
    """, (client_id,))
    orders = [dict(order) for order in cursor.fetchall()]

    predictor = AIPredictor(services.db, settings=services.settings, llm=services.llm)
    prediction = predictor.predict_client_behavior(client["nom"])
    return render_template("client_detail.html", client=client, orders=orders, prediction=prediction)


@bp.route("/alerts")
def alerts_page():
    """Alertes : commandes urgentes, volumineuses, anciennes ou douteuses."""
    alerts = AlertSystem(get_services().db).check_alerts()
    return render_template("alerts.html", alerts=alerts)


def _count(cursor, query: str) -> int:
    cursor.execute(query)
    return cursor.fetchone()[0]


@bp.route("/whatsapp")
def whatsapp_page():
    """Commandes reçues par WhatsApp, statistiques du canal et adresse du webhook Twilio."""
    services = get_services()
    cursor = services.db.connection.cursor()

    cursor.execute("""
        SELECT c.*, cl.nom as client_nom, cl.telephone, p.type as produit_type
        FROM commandes c
        LEFT JOIN clients cl ON c.client_id = cl.id
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE c.source = 'whatsapp' OR c.email_subject LIKE 'WhatsApp%'
        ORDER BY c.created_at DESC
        LIMIT 20
    """)
    whatsapp_orders = [dict(row) for row in cursor.fetchall()]

    total = _count(cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp'")
    validated = _count(cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'validee'")
    whatsapp_stats = {
        "total": total,
        "pending": _count(cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'en_attente'"),
        "validated": validated,
        "today": _count(
            cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND DATE(created_at) = DATE('now')"
        ),
        "this_week": _count(
            cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND created_at >= DATE('now', '-7 days')"
        ),
        "this_month": _count(
            cursor,
            "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND created_at >= DATE('now', 'start of month')",
        ),
        "validation_rate": round((validated / total * 100) if total > 0 else 0),
    }

    cursor.execute("""
        SELECT cl.id, cl.nom, COUNT(c.id) as total_orders
        FROM clients cl
        JOIN commandes c ON cl.id = c.client_id
        WHERE c.source = 'whatsapp'
        GROUP BY cl.id
        ORDER BY total_orders DESC
        LIMIT 5
    """)
    whatsapp_clients = [dict(row) for row in cursor.fetchall()]

    base_url = services.settings.public_base_url
    webhook_url = f"{base_url.rstrip('/')}/webhook/whatsapp" if base_url else None

    return render_template(
        "whatsapp.html",
        whatsapp_orders=whatsapp_orders,
        whatsapp_stats=whatsapp_stats,
        whatsapp_clients=whatsapp_clients,
        webhook_url=webhook_url,
    )


@bp.route("/sage")
def sage_export_page():
    """Préparation de l'export SAGE X3 : aperçu des commandes et part des codes articles renseignés."""
    db = get_services().db
    stats = db.get_stats()
    orders = db.get_all_orders()
    cursor = db.connection.cursor()
    stats["with_code_article"] = _count(
        cursor, "SELECT COUNT(*) FROM commandes WHERE code_article IS NOT NULL AND code_article != ''"
    )
    return render_template("sage_export.html", stats=stats, orders=orders)
