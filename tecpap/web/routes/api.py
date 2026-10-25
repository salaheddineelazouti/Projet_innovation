"""API JSON du tableau de bord : traitement des emails, validation et rejet des commandes,
codes articles, statistiques, notifications et état du service.

La validation ou le rejet d'une commande prévient le client par email et, pour une commande
reçue par WhatsApp, par un message WhatsApp rédigé à partir des modèles ``order_validated`` et
``order_rejected`` (:mod:`tecpap.chatbot.messages`), dans la langue de la conversation du
client ; ce message est enregistré dans la conversation. La validation d'une commande WhatsApp
vaut vérification du lien entre le numéro de l'expéditeur et le client : le numéro est alors
enregistré comme téléphone d'un client qui n'en avait pas
(:meth:`tecpap.db.database.DatabaseManager.attach_phone_to_client`).

Les champs modifiables d'une commande sont limités à
:data:`tecpap.db.database.ORDER_UPDATABLE_FIELDS` : une requête qui contient un autre champ, ou
une valeur numérique invalide, est refusée (400) avec la liste des champs refusés
(``rejected_fields``). En cas d'erreur inattendue, l'exception est consignée dans le journal et la
réponse ne contient qu'un message générique.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, request

from tecpap import __version__
from tecpap.chatbot.messages import DEFAULT_LANGUAGE, order_lines, reason_line, render
from tecpap.db.database import OrderUpdateError, clean_order_updates
from tecpap.extraction.article_codes import generate_article_code, suggest_article_code_from_description
from tecpap.extraction.rules import normalize_phone
from tecpap.processing.order_processor import OrderProcessor
from tecpap.reporting.analytics import AIPredictor, AlertSystem, Analytics, ClientHistory
from tecpap.web.auth import current_user
from tecpap.web.errors import GENERIC_ERROR_MESSAGE, json_error, server_error
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


# Longueur maximale du nom de l'auteur d'une validation.
_MAX_VALIDATOR_LENGTH = 100


def _json_body() -> dict[str, Any]:
    """Corps JSON de la requête, ou dictionnaire vide s'il est absent ou invalide."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _optional_text(value: Any, max_length: int | None = None) -> str | None:
    """Chaîne non vide (sans espaces en bordure), sinon ``None`` ; tronquée à ``max_length`` caractères."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    return text[:max_length] if max_length else text


def _rejected_fields(exc: OrderUpdateError):
    """Réponse 400 d'une modification refusée, avec la liste des champs en cause."""
    return json_error(exc.message, 400, rejected_fields=list(exc.fields))


# ---------------------------------------------------------------------- état du service

@bp.route("/healthz")
def healthz():
    """Sonde de disponibilité (utilisée par le conteneur Docker)."""
    return jsonify({"status": "ok", "version": __version__})


# ---------------------------------------------------------------------- notifications client

def _order_whatsapp_number(order: dict[str, Any]) -> str | None:
    """Numéro WhatsApp à prévenir pour une commande, au format international (« +2126… »).

    Dans l'ordre : l'expéditeur du message (``whatsapp_from``), le téléphone du client, puis le
    numéro d'un nom de client provisoire (« Client WhatsApp +212… »).
    """
    for candidate in (order.get("whatsapp_from"), order.get("client_telephone"), order.get("email_from")):
        phone = normalize_phone(candidate) if candidate else None
        if phone:
            return phone
    client_name = order.get("client_nom") or ""
    if client_name.startswith("Client WhatsApp"):
        match = re.search(r"\+\d+", client_name)
        if match:
            return normalize_phone(match.group())
    return None


def _notification_language(services: Services, number: str) -> tuple[Any, str]:
    """Conversation WhatsApp du numéro (ou ``None``) et langue de la notification (français sans conversation)."""
    try:
        conversation = services.conversation_store().find(number)
    except Exception:
        logger.warning("Conversation WhatsApp illisible pour la notification", exc_info=True)
        return None, DEFAULT_LANGUAGE
    if conversation is None:
        return None, DEFAULT_LANGUAGE
    return conversation, conversation.language or DEFAULT_LANGUAGE


def _summary_fields(order: dict[str, Any]) -> dict[str, Any]:
    """Champs d'une commande lue en base, sous les noms attendus par :func:`order_lines`."""
    return {
        "entreprise_cliente": order.get("client_nom"),
        "type_produit": order.get("produit_type") or order.get("nature_produit"),
        "quantite": order.get("quantite"),
        "unite": order.get("unite"),
        "date_livraison": order.get("date_livraison"),
    }


def _send_whatsapp_notification(services: Services, order: dict[str, Any], template: str, *,
                                reason: str | None = None) -> bool:
    """Prévient par WhatsApp le client d'une commande reçue par WhatsApp (validation ou refus).

    Le message est rédigé à partir du modèle ``template`` (``order_validated`` ou
    ``order_rejected``), dans la langue de la conversation WhatsApp du numéro (français sans
    conversation), puis enregistré comme message envoyé de cette conversation.
    """
    if order.get("source") != "whatsapp":
        return False
    number = _order_whatsapp_number(order)
    if not number:
        return False
    conversation, language = _notification_language(services, number)
    reference = order.get("numero_commande") or f"CMD-{order.get('id')}"
    if template == "order_validated":
        message = render(template, language, reference=reference, lines=order_lines(_summary_fields(order), language))
    else:
        message = render(template, language, reference=reference, reason=reason_line(reason, language))
    try:
        if not services.whatsapp.send_reply(number, message):
            return False
    except Exception:
        logger.warning("Notification WhatsApp impossible pour la commande %s", order.get("id"), exc_info=True)
        return False
    logger.info("Notification WhatsApp envoyée pour la commande %s", order.get("id"))
    if conversation is not None:
        try:
            services.conversation_store().add_message(conversation.id, "out", message, intent=template)
        except Exception:
            logger.warning("Notification non enregistrée dans la conversation %s", conversation.id, exc_info=True)
    return True


# ---------------------------------------------------------------------- commandes

@bp.route("/api/process-emails", methods=["POST"])
def api_process_emails():
    """Lit les emails récents et enregistre les bons de commande détectés."""
    services = get_services()
    try:
        processor = OrderProcessor(settings=services.settings, email_sender=services.email_sender, llm=services.llm)
        orders = processor.process_new_emails(max_emails=10, save_to_db=True)
        return jsonify({
            "success": True,
            "message": f"{len(orders)} bon(s) de commande détecté(s)",
            "orders_count": len(orders),
        })
    except Exception:
        logger.exception("Échec du traitement des emails")
        return json_error("Le traitement des emails a échoué. Le détail est consigné dans le journal du serveur.", 500)


@bp.route("/api/orders/<int:order_id>/validate", methods=["POST"])
def api_validate_order(order_id: int):
    """Valide une commande et prévient le client (email, et WhatsApp si la commande en vient).

    L'auteur enregistré est ``validated_by`` s'il est fourni, sinon l'utilisateur connecté.
    Pour une commande WhatsApp dont le client n'a pas encore de téléphone, le numéro de
    l'expéditeur lui est rattaché. Une commande inexistante donne une erreur 404.
    """
    services = get_services()
    order = services.db.get_order(order_id)
    if not order:
        return json_error("Commande introuvable", 404)
    validated_by = (_optional_text(_json_body().get("validated_by"), _MAX_VALIDATOR_LENGTH)
                    or current_user() or "Commercial")
    services.db.update_order_status(order_id, "validee", validated_by)
    if order.get("source") == "whatsapp" and order.get("whatsapp_from") and not order.get("client_telephone"):
        services.db.attach_phone_to_client(order.get("client_id"), order["whatsapp_from"])

    # Commande relue après la validation : la notification contient le statut et la date enregistrés.
    order = services.db.get_order(order_id)
    email_sent = False
    try:
        email_sent = services.email_sender.send_validation_email(order)
    except Exception:
        logger.warning("Email de validation impossible pour la commande %s", order_id, exc_info=True)
    whatsapp_sent = _send_whatsapp_notification(services, order, "order_validated")

    return jsonify({
        "success": True,
        "message": "Commande validée",
        "whatsapp_sent": whatsapp_sent,
        "email_sent": email_sent,
    })


@bp.route("/api/orders/<int:order_id>/reject", methods=["POST"])
def api_reject_order(order_id: int):
    """Rejette une commande, enregistre le motif et prévient le client.

    Une commande inexistante donne une erreur 404.
    """
    services = get_services()
    order = services.db.get_order(order_id)
    if not order:
        return json_error("Commande introuvable", 404)
    reason = _optional_text(_json_body().get("reason")) or ""

    services.db.update_order_status(order_id, "rejetee")
    if reason:
        services.db.update_order(order_id, {"motif_rejet": reason})

    email_sent = False
    try:
        email_sent = services.email_sender.send_rejection_email(order, reason)
    except Exception:
        logger.warning("Email de rejet impossible pour la commande %s", order_id, exc_info=True)
    whatsapp_sent = _send_whatsapp_notification(services, order, "order_rejected", reason=reason)

    return jsonify({
        "success": True,
        "message": "Commande rejetée",
        "whatsapp_sent": whatsapp_sent,
        "email_sent": email_sent,
    })


@bp.route("/api/orders/<int:order_id>/update", methods=["POST"])
def api_update_order(order_id: int):
    """Modifie les champs d'une commande ; le reste à livrer est recalculé si une quantité change.

    Seuls les champs de :data:`~tecpap.db.database.ORDER_UPDATABLE_FIELDS` sont acceptés ; les
    valeurs des champs numériques peuvent être des chaînes (formulaire HTML), une chaîne vide
    efface la valeur.
    """
    db = get_services().db
    body = _json_body()
    if not body:
        return json_error("Aucun champ à modifier", 400)
    try:
        updates = clean_order_updates(body)
    except OrderUpdateError as exc:
        return _rejected_fields(exc)

    order = db.get_order(order_id)
    if not order:
        return json_error("Commande introuvable", 404)
    if "quantite" in updates or "quantite_livree" in updates:
        quantite = updates.get("quantite", order.get("quantite")) or 0
        quantite_livree = updates.get("quantite_livree", order.get("quantite_livree")) or 0
        updates["reste_a_livrer"] = quantite - quantite_livree

    try:
        db.update_order(order_id, updates)
    except Exception:
        logger.exception("Échec de la modification de la commande %s", order_id)
        return server_error()
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
    except Exception:
        logger.exception("Échec de la construction d'un code article")
        return server_error()


@bp.route("/api/article-code/suggest", methods=["POST"])
def api_suggest_article_code():
    """Propose un code article à partir d'une description libre."""
    try:
        description = _json_body().get("description", "")
        code = suggest_article_code_from_description(description)
        return jsonify({"success": True, "code": code, "description": description})
    except Exception:
        logger.exception("Échec de la proposition d'un code article")
        return server_error()


@bp.route("/api/orders/<int:order_id>/generate-code", methods=["POST"])
def api_order_generate_code(order_id: int):
    """Calcule et enregistre le code article d'une commande."""
    db = get_services().db
    try:
        order = db.get_order(order_id)
        if not order:
            return json_error("Commande introuvable", 404)

        code = _code_for_order(order)
        if code:
            db.update_order(order_id, {"code_article": code})
            return jsonify({"success": True, "code": code, "message": f"Code article {code} assigné"})
        return json_error("Impossible de générer un code, données insuffisantes", 400)
    except Exception:
        logger.exception("Échec de la génération du code article de la commande %s", order_id)
        return server_error()


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
    except Exception:
        logger.exception("Échec de la génération des codes articles")
        return server_error()


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
    except Exception:
        logger.exception("Échec du calcul des statistiques SAGE X3")
        return server_error()


@bp.route("/api/orders/<int:order_id>/sage-fields", methods=["PUT"])
def api_update_sage_fields(order_id: int):
    """Modifie uniquement les champs SAGE X3 d'une commande (liste fermée :data:`SAGE_FIELDS`).

    Une requête qui contient un autre champ est refusée entièrement (400, ``rejected_fields``).
    """
    db = get_services().db
    data = _json_body()
    if not data:
        return json_error("Aucune donnée fournie", 400)
    rejected = sorted(str(key) for key in data if key not in SAGE_FIELDS)
    if rejected:
        return json_error("Champs non modifiables par cette route : " + ", ".join(rejected), 400,
                          rejected_fields=rejected)
    try:
        sage_data = clean_order_updates(data)
    except OrderUpdateError as exc:
        return _rejected_fields(exc)

    try:
        order = db.get_order(order_id)
        if not order:
            return json_error("Commande introuvable", 404)
        if "quantite_livree" in sage_data:
            quantite = order.get("quantite") or 0
            livree = sage_data.get("quantite_livree") or 0
            sage_data["reste_a_livrer"] = quantite - livree

        db.update_order(order_id, sage_data)
        return jsonify({
            "success": True,
            "message": "Champs SAGE X3 mis à jour",
            "updated_fields": list(sage_data.keys()),
        })
    except Exception:
        logger.exception("Échec de la modification des champs SAGE X3 de la commande %s", order_id)
        return server_error()


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
        return json_error("Client introuvable", 404)

    preferences = ClientHistory(services.db).get_client_preferences(client["nom"])
    predictor = AIPredictor(services.db, settings=services.settings, llm=services.llm)
    prediction = predictor.predict_client_behavior(client["nom"])
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
    except Exception:
        logger.exception("Échec de la recherche des nouvelles commandes")
        return jsonify({"new_orders": [], "error": GENERIC_ERROR_MESSAGE, "last_id": 0})


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
    except Exception:
        logger.exception("Échec de la vérification de la connexion Twilio")
        return jsonify({"connected": False, "error": GENERIC_ERROR_MESSAGE})
