"""API JSON du tableau de bord : traitement des emails, validation et rejet des commandes,
codes articles, statistiques, notifications et état du service.

La validation ou le rejet d'une commande prévient le client par email et, pour une commande
reçue par WhatsApp, par un message WhatsApp.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, request

from tecpap import __version__
from tecpap.extraction.article_codes import generate_article_code, suggest_article_code_from_description
from tecpap.processing.order_processor import OrderProcessor
from tecpap.reporting.analytics import AIPredictor, AlertSystem, Analytics, ClientHistory
from tecpap.web.services import Services, get_services

logger = logging.getLogger(__name__)

bp = Blueprint("api", __name__)

# Champs SAGE X3 modifiables par la route /api/orders/<id>/sage-fields.
SAGE_FIELDS = (
    "code_article", "code_client", "site_vente", "ligne_commande",
    "quantite_livree", "reste_a_livrer", "quantite_facturee",
    "type_sac", "format_sac", "type_papier", "grammage", "laize",
    "impression_client", "commercial",
)


def _json_body() -> dict[str, Any]:
    """Corps JSON de la requête, ou dictionnaire vide s'il est absent ou invalide."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------- état du service

@bp.route("/healthz")
def healthz():
    """Sonde de disponibilité (utilisée par le conteneur Docker)."""
    return jsonify({"status": "ok", "version": __version__})


# ---------------------------------------------------------------------- notifications client

def _order_whatsapp_number(order: dict[str, Any]) -> str | None:
    """Numéro WhatsApp du client d'une commande (téléphone, expéditeur, ou nom provisoire « Client WhatsApp +212… »)."""
    phone = order.get("client_telephone") or order.get("email_from") or ""
    client_name = order.get("client_nom") or ""
    if not phone and client_name.startswith("Client WhatsApp"):
        match = re.search(r"\+\d+", client_name)
        if match:
            phone = match.group()
    if not phone:
        return None
    return "+" + phone.replace("+", "").replace(" ", "").replace("whatsapp:", "")


def _send_whatsapp(services: Services, order: dict[str, Any], message: str) -> bool:
    """Envoie un message WhatsApp au client d'une commande reçue par WhatsApp."""
    if order.get("source") != "whatsapp":
        return False
    number = _order_whatsapp_number(order)
    if not number:
        return False
    try:
        if services.whatsapp.send_reply(number, message):
            logger.info("Notification WhatsApp envoyée pour la commande %s", order.get("id"))
            return True
    except Exception:
        logger.warning("Notification WhatsApp impossible pour la commande %s", order.get("id"), exc_info=True)
    return False


def _validation_message(order: dict[str, Any], order_id: int) -> str:
    return f"""✅ *Commande Validée!*

📋 *Détails:*
• Client: {order.get('client_nom', 'N/A')}
• Produit: {order.get('produit_type', order.get('nature_produit', 'N/A'))}
• Quantité: {order.get('quantite', 'N/A')} {order.get('unite', '')}
• N° Commande: {order.get('numero_commande') or f"CMD-{order_id}"}

✨ Merci pour votre confiance!
📞 Pour toute question, contactez-nous."""


def _rejection_message(order: dict[str, Any], reason: str) -> str:
    return f"""❌ *Commande Non Validée*

📋 *Détails:*
• Client: {order.get('client_nom', 'N/A')}
• Produit: {order.get('produit_type', order.get('nature_produit', 'N/A'))}
{f'• Raison: {reason}' if reason else ''}

📞 Veuillez nous contacter pour plus d'informations."""


# ---------------------------------------------------------------------- commandes

@bp.route("/api/process-emails", methods=["POST"])
def api_process_emails():
    """Lit les emails récents et enregistre les bons de commande détectés."""
    services = get_services()
    try:
        processor = OrderProcessor(settings=services.settings, email_sender=services.email_sender)
        orders = processor.process_new_emails(max_emails=10, save_to_db=True)
        return jsonify({
            "success": True,
            "message": f"{len(orders)} bon(s) de commande détecté(s)",
            "orders_count": len(orders),
        })
    except Exception as exc:
        logger.exception("Échec du traitement des emails")
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/orders/<int:order_id>/validate", methods=["POST"])
def api_validate_order(order_id: int):
    """Valide une commande et prévient le client (email, et WhatsApp si la commande en vient)."""
    services = get_services()
    validated_by = _json_body().get("validated_by", "Commercial")
    services.db.update_order_status(order_id, "validee", validated_by)

    order = services.db.get_order(order_id)
    email_sent = False
    whatsapp_sent = False
    if order:
        try:
            email_sent = services.email_sender.send_validation_email(order)
        except Exception:
            logger.warning("Email de validation impossible pour la commande %s", order_id, exc_info=True)
        whatsapp_sent = _send_whatsapp(services, order, _validation_message(order, order_id))

    return jsonify({
        "success": True,
        "message": "Commande validée",
        "whatsapp_sent": whatsapp_sent,
        "email_sent": email_sent,
    })


@bp.route("/api/orders/<int:order_id>/reject", methods=["POST"])
def api_reject_order(order_id: int):
    """Rejette une commande, enregistre le motif et prévient le client."""
    services = get_services()
    reason = _json_body().get("reason", "")

    order = services.db.get_order(order_id)
    services.db.update_order_status(order_id, "rejetee")
    if reason:
        services.db.update_order(order_id, {"motif_rejet": reason})

    email_sent = False
    whatsapp_sent = False
    if order:
        try:
            email_sent = services.email_sender.send_rejection_email(order, reason)
        except Exception:
            logger.warning("Email de rejet impossible pour la commande %s", order_id, exc_info=True)
        whatsapp_sent = _send_whatsapp(services, order, _rejection_message(order, reason))

    return jsonify({
        "success": True,
        "message": "Commande rejetée",
        "whatsapp_sent": whatsapp_sent,
        "email_sent": email_sent,
    })


@bp.route("/api/orders/<int:order_id>/update", methods=["POST"])
def api_update_order(order_id: int):
    """Modifie les champs d'une commande ; le reste à livrer est recalculé si une quantité change."""
    db = get_services().db
    updates = _json_body()
    updates.pop("id", None)
    updates.pop("created_at", None)
    if not updates:
        return jsonify({"success": False, "error": "Aucun champ à modifier"}), 400

    if "quantite" in updates or "quantite_livree" in updates:
        order = db.get_order(order_id)
        if not order:
            return jsonify({"success": False, "error": "Commande introuvable"}), 404
        quantite = updates.get("quantite", order.get("quantite", 0)) or 0
        quantite_livree = updates.get("quantite_livree", order.get("quantite_livree", 0)) or 0
        updates["reste_a_livrer"] = quantite - quantite_livree

    db.update_order(order_id, updates)
    return jsonify({"success": True, "message": "Commande mise à jour"})


@bp.route("/api/stats")
def api_stats():
    """Compteurs de commandes par statut et par canal."""
    return jsonify(get_services().db.get_stats())


@bp.route("/api/orders")
def api_orders():
    """Toutes les commandes au format JSON."""
    return jsonify(get_services().db.get_all_orders())


# ---------------------------------------------------------------------- codes articles

def _code_for_order(order: dict[str, Any]) -> str | None:
    """Code article d'une commande : depuis papier, grammage et laize, sinon depuis la désignation."""
    code = None
    if order.get("type_papier") or order.get("grammage") or order.get("laize"):
        code = generate_article_code(
            paper_type=order.get("type_papier"),
            grammage=order.get("grammage"),
            laize=order.get("laize"),
        )
    if not code or len(code) <= 2:
        description = f"{order.get('nature_produit', '')} {order.get('produit_type', '')}"
        code = suggest_article_code_from_description(description)
    return code if code and len(code) > 2 else None


@bp.route("/api/article-code/generate", methods=["POST"])
def api_generate_article_code():
    """Construit un code article à partir du type de papier, du grammage, de la laize et du fournisseur."""
    try:
        data = _json_body()
        code = generate_article_code(
            paper_type=data.get("paper_type"),
            grammage=data.get("grammage"),
            laize=data.get("laize"),
            supplier=data.get("supplier"),
        )
        return jsonify({
            "success": True,
            "code": code,
            "paper_type": data.get("paper_type"),
            "grammage": data.get("grammage"),
            "laize": data.get("laize"),
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/article-code/suggest", methods=["POST"])
def api_suggest_article_code():
    """Propose un code article à partir d'une description libre."""
    try:
        description = _json_body().get("description", "")
        code = suggest_article_code_from_description(description)
        return jsonify({"success": True, "code": code, "description": description})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/orders/<int:order_id>/generate-code", methods=["POST"])
def api_order_generate_code(order_id: int):
    """Calcule et enregistre le code article d'une commande."""
    db = get_services().db
    try:
        order = db.get_order(order_id)
        if not order:
            return jsonify({"success": False, "error": "Order not found"}), 404

        code = _code_for_order(order)
        if code:
            db.update_order(order_id, {"code_article": code})
            return jsonify({"success": True, "code": code, "message": f"Code article {code} assigné"})
        return jsonify({"success": False, "error": "Impossible de générer un code, données insuffisantes"}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/orders/generate-all-codes", methods=["POST"])
def api_generate_all_codes():
    """Calcule le code article de toutes les commandes qui n'en ont pas."""
    db = get_services().db
    try:
        cursor = db.connection.cursor()
        cursor.execute("""
            SELECT c.id, c.nature_produit, p.type as produit_type, c.type_papier, c.grammage, c.laize
            FROM commandes c
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE c.code_article IS NULL OR c.code_article = ''
        """)
        orders_without_code = [dict(row) for row in cursor.fetchall()]

        generated = 0
        failed = 0
        results = []
        for order in orders_without_code:
            code = _code_for_order(order)
            if code:
                db.update_order(order["id"], {"code_article": code})
                generated += 1
                results.append({"id": order["id"], "code": code, "status": "success"})
            else:
                failed += 1
                results.append({"id": order["id"], "code": None, "status": "failed"})

        return jsonify({
            "success": True,
            "generated": generated,
            "failed": failed,
            "total_processed": len(orders_without_code),
            "results": results[:50],
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/orders/sage-stats")
def api_sage_stats():
    """Complétude des champs SAGE X3 (codes articles, grammage, laize) et codes les plus fréquents."""
    db = get_services().db
    try:
        cursor = db.connection.cursor()

        def count(query: str) -> int:
            cursor.execute(query)
            return cursor.fetchone()[0]

        total = count("SELECT COUNT(*) FROM commandes")
        with_code = count("SELECT COUNT(*) FROM commandes WHERE code_article IS NOT NULL AND code_article != ''")
        validated = count("SELECT COUNT(*) FROM commandes WHERE statut = 'validee'")
        complete_sage = count("""
            SELECT COUNT(*) FROM commandes
            WHERE code_article IS NOT NULL
            AND grammage IS NOT NULL
            AND laize IS NOT NULL
        """)
        cursor.execute("""
            SELECT code_article, COUNT(*) as count
            FROM commandes
            WHERE code_article IS NOT NULL AND code_article != ''
            GROUP BY code_article
            ORDER BY count DESC
            LIMIT 10
        """)
        top_codes = [dict(row) for row in cursor.fetchall()]

        return jsonify({
            "success": True,
            "stats": {
                "total_orders": total,
                "with_code_article": with_code,
                "without_code_article": total - with_code,
                "validated": validated,
                "complete_sage_fields": complete_sage,
                "completion_rate": round(with_code / total * 100, 1) if total > 0 else 0,
            },
            "top_codes": top_codes,
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/orders/<int:order_id>/sage-fields", methods=["PUT"])
def api_update_sage_fields(order_id: int):
    """Modifie uniquement les champs SAGE X3 d'une commande (liste fermée :data:`SAGE_FIELDS`)."""
    db = get_services().db
    try:
        data = _json_body()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        sage_data = {key: value for key, value in data.items() if key in SAGE_FIELDS}
        if not sage_data:
            return jsonify({"success": False, "error": "No valid SAGE fields provided"}), 400

        if "quantite_livree" in sage_data:
            order = db.get_order(order_id)
            if order:
                quantite = order.get("quantite", 0) or 0
                livree = sage_data.get("quantite_livree", 0) or 0
                sage_data["reste_a_livrer"] = quantite - livree

        db.update_order(order_id, sage_data)
        return jsonify({
            "success": True,
            "message": "Champs SAGE X3 mis à jour",
            "updated_fields": list(sage_data.keys()),
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------- statistiques et alertes

@bp.route("/api/analytics")
def api_analytics():
    """Indicateurs détaillés du tableau de bord."""
    return jsonify(Analytics(get_services().db).get_dashboard_stats())


@bp.route("/api/alerts")
def api_alerts():
    """Alertes en cours."""
    return jsonify(AlertSystem(get_services().db).check_alerts())


@bp.route("/api/client/<int:client_id>/history")
def api_client_history(client_id: int):
    """Habitudes de commande et estimation de la prochaine commande d'un client."""
    services = get_services()
    cursor = services.db.connection.cursor()
    cursor.execute("SELECT nom FROM clients WHERE id = ?", (client_id,))
    client = cursor.fetchone()
    if not client:
        return jsonify({"error": "Client not found"}), 404

    preferences = ClientHistory(services.db).get_client_preferences(client["nom"])
    prediction = AIPredictor(services.db, settings=services.settings).predict_client_behavior(client["nom"])
    return jsonify({"preferences": preferences, "prediction": prediction})


@bp.route("/api/notifications/check")
def check_notifications():
    """Nouvelles commandes depuis ``last_id`` (interrogé régulièrement par le navigateur).

    Sans ``last_id`` (ou avec 0), renvoie seulement l'identifiant de la dernière commande,
    qui sert de point de départ.
    """
    db = get_services().db
    try:
        cursor = db.connection.cursor()
        try:
            last_id = int(request.args.get("last_id", "0"))
        except ValueError:
            last_id = 0

        if last_id <= 0:
            cursor.execute("SELECT MAX(id) as max_id FROM commandes")
            row = cursor.fetchone()
            return jsonify({"new_orders": [], "last_id": row["max_id"] if row and row["max_id"] else 0})

        cursor.execute("""
            SELECT c.id, c.created_at, c.source, c.email_subject,
                   cl.nom as client, p.type as produit, c.nature_produit
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE c.id > ?
            ORDER BY c.id DESC
            LIMIT 10
        """, (last_id,))

        new_orders = []
        max_seen_id = last_id
        for row in cursor.fetchall():
            order = dict(row)
            max_seen_id = max(max_seen_id, order["id"])
            source = order.get("source", "email")
            if not source and order.get("email_subject"):
                source = "whatsapp" if "whatsapp" in order["email_subject"].lower() else "email"
            order["source"] = source
            if not order.get("produit"):
                order["produit"] = order.get("nature_produit", "Produit")
            order["timestamp"] = order.get("created_at") or datetime.now().isoformat()
            new_orders.append(order)

        return jsonify({"new_orders": new_orders, "last_id": max_seen_id})
    except Exception as exc:
        return jsonify({"new_orders": [], "error": str(exc), "last_id": 0})


@bp.route("/api/whatsapp/status")
def whatsapp_status():
    """Indique si le client Twilio est configuré (identifiant de compte tronqué)."""
    whatsapp = get_services().whatsapp
    try:
        connected = whatsapp.connect()
        return jsonify({
            "connected": connected,
            "account_sid": whatsapp.account_sid[:10] + "..." if whatsapp.account_sid else None,
        })
    except Exception as exc:
        return jsonify({"connected": False, "error": str(exc)})
