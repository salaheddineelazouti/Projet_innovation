"""API JSON du tableau de bord : traitement des emails, validation et rejet des commandes,
codes articles, statistiques, notifications et état du service.

Validation et rejet
    Seule une commande « en attente » peut être validée ou rejetée : le changement de statut est
    une seule requête SQL conditionnelle (``WHERE statut = 'en_attente'``). Quand deux
    commerciaux agissent sur la même commande, ou qu'une page n'est plus à jour, la seconde
    action reçoit une erreur 409 avec le statut actuel, et le client ne reçoit aucun second
    message. La validation ne rattache jamais le numéro WhatsApp de l'expéditeur au client :
    valider le contenu d'une commande ne prouve pas l'identité de celui qui l'a envoyée.
    L'utilisateur connecté est inscrit dans la trace de la table ``logs`` (colonne ``user_id``),
    pour la validation, le rejet et chaque modification d'une commande.

Notifications
    La validation ou le rejet prévient le client par email et, pour une commande reçue par
    WhatsApp, par un message WhatsApp rédigé à partir des modèles ``order_validated`` et
    ``order_rejected`` (:mod:`tecpap.chatbot.messages`), dans la langue de la conversation du
    client ; ce message est enregistré dans la conversation. La réponse indique, pour chaque
    canal, ``sent`` (envoyé), ``failed`` (échec) ou ``not_applicable`` (pas d'adresse ou de
    numéro), avec un avertissement en français (``warning``) quand le client n'a pas été prévenu ;
    le résultat est aussi inscrit dans la table ``logs``. La route ``/api/orders/<id>/notify``
    renvoie la notification d'une commande validée ou rejetée.

Modifications
    Les champs modifiables d'une commande sont limités à
    :data:`tecpap.db.database.ORDER_UPDATABLE_FIELDS` : une requête qui contient un autre champ, ou
    une valeur numérique invalide, est refusée (400) avec la liste des champs refusés
    (``rejected_fields``). Sur une commande validée ou rejetée, les champs annoncés au client
    (:data:`DECIDED_ORDER_LOCKED_FIELDS` : produit, quantité, prix, dates) ne peuvent plus changer
    (409) ; les champs SAGE X3 et de livraison restent modifiables.

En cas d'erreur inattendue, l'exception est consignée dans le journal et la réponse ne contient
qu'un message générique.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, request

from tecpap import __version__
from tecpap.chatbot.messages import DEFAULT_LANGUAGE, order_lines, reason_line, render
from tecpap.db.database import OrderUpdateError, clean_order_updates, is_generic_client_name
from tecpap.extraction.article_codes import generate_article_code, suggest_article_code_from_description
from tecpap.extraction.rules import normalize_phone
from tecpap.processing import order_processor as order_processor_module
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


DECIDED_ORDER_LOCKED_FIELDS: tuple[str, ...] = (
    "numero_commande", "produit_id", "nature_produit", "quantite", "unite",
    "prix_unitaire", "prix_total", "devise", "date_commande", "date_livraison",
)
"""Champs d'une commande validée ou rejetée qui ne peuvent plus changer : ceux annoncés au client."""

STATUS_LABELS: dict[str, str] = {
    "en_attente": "en attente", "validee": "validée", "rejetee": "rejetée", "supprimee": "supprimée",
}
"""Libellés des statuts dans les messages d'erreur."""

MAILBOX_UNREACHABLE_MESSAGE = ("La boîte Gmail n'a pas pu être lue : vérifiez les identifiants Gmail "
                               "et le journal du serveur.")
PROCESSING_RUNNING_MESSAGE = "Un traitement des emails est déjà en cours : attendez qu'il se termine puis réessayez."

NOTIFICATION_SENT = "sent"
NOTIFICATION_FAILED = "failed"
NOTIFICATION_NOT_APPLICABLE = "not_applicable"

_EMAIL_ADDRESS_RE = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")

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
    """Sonde de disponibilité (utilisée par le conteneur Docker).

    La base est lue (table ``sqlite_master``) sur la connexion du fil courant : une base
    impossible à ouvrir ou illisible donne 503, avec la cause ``database``.
    """
    try:
        connection = get_services().db.connection
        if connection is None:
            raise sqlite3.OperationalError("connexion à la base impossible")
        connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        logger.warning("Sonde de disponibilité : base de données indisponible", exc_info=True)
        return jsonify({"status": "error", "reason": "database", "version": __version__}), 503
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
    client_name = order.get("client_nom")
    if not isinstance(client_name, str) or "@" in client_name or is_generic_client_name(client_name):
        client_name = None
    return {
        "entreprise_cliente": client_name,
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
    number = _whatsapp_recipient(order)
    if not number:
        return False
    conversation, language = _notification_language(services, number)
    reference = order.get("numero_commande") or f"CMD-{order.get('id')}"
    if template == "order_validated":
        lines = order_lines(_summary_fields(order), language, omit_unknown_client=True)
        message = render(template, language, reference=reference, lines=lines)
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


def _whatsapp_recipient(order: dict[str, Any]) -> str | None:
    """Numéro à prévenir par WhatsApp : seulement pour une commande reçue par WhatsApp."""
    return _order_whatsapp_number(order) if order.get("source") == "whatsapp" else None


def _has_email_address(order: dict[str, Any]) -> bool:
    return bool(_EMAIL_ADDRESS_RE.fullmatch(str(order.get("email_from") or "").strip()))


def _channel_state(applicable: bool, sent: bool) -> str:
    if not applicable:
        return NOTIFICATION_NOT_APPLICABLE
    return NOTIFICATION_SENT if sent else NOTIFICATION_FAILED


def _notification_warning(states: dict[str, str]) -> str | None:
    """Avertissement à afficher quand le client n'a pas été prévenu, sinon ``None``."""
    failed = [label for channel, label in (("email", "email"), ("whatsapp", "WhatsApp"))
              if states[channel] == NOTIFICATION_FAILED]
    if failed:
        return (f"Le client n'a pas été prévenu par {' ni par '.join(failed)} : l'envoi a échoué (détail dans le "
                "journal du serveur). Prévenez-le autrement ou renvoyez la notification.")
    if all(state == NOTIFICATION_NOT_APPLICABLE for state in states.values()):
        return "Le client n'a pas été prévenu : la commande n'a ni adresse email ni numéro WhatsApp."
    return None


def _record_notification(services: Services, order_id: int, template: str, states: dict[str, str]) -> None:
    """Inscrit le résultat d'une notification dans la table ``logs`` (avec l'utilisateur connecté)."""
    details = f"Notification {template}: email={states['email']}, whatsapp={states['whatsapp']}"
    try:
        connection = services.db.connection
        connection.execute(
            "INSERT INTO logs (action, table_name, record_id, details, user_id) "
            "VALUES ('NOTIFY', 'commandes', ?, ?, ?)",
            (order_id, details, current_user()),
        )
        connection.commit()
    except sqlite3.Error:
        logger.warning("Résultat de la notification de la commande %s non enregistré", order_id, exc_info=True)


def _notify_client(services: Services, order: dict[str, Any], template: str, reason: str = "") -> dict[str, Any]:
    """Prévient le client d'une décision (email et WhatsApp) ; renvoie les champs de la réponse JSON."""
    order_id = order.get("id")
    email_sent = False
    email_applicable = _has_email_address(order)
    if email_applicable:
        try:
            if template == "order_validated":
                email_sent = services.email_sender.send_validation_email(order)
            else:
                email_sent = services.email_sender.send_rejection_email(order, reason)
        except Exception:
            logger.warning("Email de notification impossible pour la commande %s", order_id, exc_info=True)
    whatsapp_applicable = _whatsapp_recipient(order) is not None
    whatsapp_sent = (_send_whatsapp_notification(services, order, template, reason=reason)
                     if whatsapp_applicable else False)
    states = {"email": _channel_state(email_applicable, bool(email_sent)),
              "whatsapp": _channel_state(whatsapp_applicable, whatsapp_sent)}
    if NOTIFICATION_FAILED in states.values():
        logger.warning("Notification incomplète pour la commande %s : %s", order_id, states)
    _record_notification(services, order_id, template, states)
    return {"email_sent": bool(email_sent), "whatsapp_sent": whatsapp_sent, "notifications": states,
            "warning": _notification_warning(states)}


def _already_decided(order: dict[str, Any] | None):
    """Réponse 409 pour une commande qui n'est plus en attente (statut actuel dans ``status``)."""
    status = (order or {}).get("statut") or ""
    label = STATUS_LABELS.get(status, status or "inconnu")
    return json_error(f"Commande déjà traitée (statut : {label}) : rechargez la page pour voir son état actuel.",
                      409, status=status)


def _decide(services: Services, order_id: int, status: str, *, validated_by: str | None = None,
            reason: str = "") -> bool:
    """Fait passer une commande en attente au statut ``status`` ; faux si elle n'est plus en attente.

    Le changement est une seule requête conditionnelle : de deux décisions simultanées, une
    seule aboutit. Le motif d'un rejet est écrit dans la même requête. La date et l'auteur de la
    validation, et la trace de la table ``logs``, sont ensuite enregistrés par
    :meth:`tecpap.db.database.DatabaseManager.update_order_status`.
    """
    connection = services.db.connection
    if status == "rejetee":
        cursor = connection.execute(
            "UPDATE commandes SET statut = 'rejetee', motif_rejet = ? WHERE id = ? AND statut = 'en_attente'",
            (reason or None, order_id),
        )
    else:
        cursor = connection.execute(
            "UPDATE commandes SET statut = 'validee' WHERE id = ? AND statut = 'en_attente'", (order_id,),
        )
    connection.commit()
    if cursor.rowcount != 1:
        return False
    services.db.update_order_status(order_id, status, validated_by)
    _attribute_last_log(services, order_id, current_user() or validated_by)
    return True


def _attribute_last_log(services: Services, order_id: int, user: str | None) -> None:
    """Inscrit ``user`` sur la dernière ligne de ``logs`` de la commande qui n'a pas encore d'auteur.

    La couche base de données écrit la trace d'une modification sans connaître l'utilisateur
    connecté ; la route la complète aussitôt après la modification.
    """
    if not user:
        return
    try:
        connection = services.db.connection
        connection.execute(
            "UPDATE logs SET user_id = ? WHERE id = (SELECT MAX(id) FROM logs WHERE table_name = 'commandes' "
            "AND record_id = ? AND action = 'UPDATE' AND user_id IS NULL)",
            (user, order_id),
        )
        connection.commit()
    except sqlite3.Error:
        logger.warning("Auteur de la modification de la commande %s non enregistré", order_id, exc_info=True)


# ---------------------------------------------------------------------- commandes

def _is_processing_already_running(error: Exception) -> bool:
    """Vrai pour l'erreur « traitement des emails déjà en cours » du processeur de commandes."""
    running_error = getattr(order_processor_module, "ProcessingAlreadyRunning", None)
    return isinstance(running_error, type) and isinstance(error, running_error)


@bp.route("/api/process-emails", methods=["POST"])
def api_process_emails():
    """Lit les emails récents et enregistre les bons de commande détectés.

    Un seul traitement à la fois : pendant un traitement en cours, la requête reçoit 409.
    """
    services = get_services()
    try:
        processor = OrderProcessor(settings=services.settings, email_sender=services.email_sender, llm=services.llm)
        orders = processor.process_new_emails(max_emails=10, save_to_db=True)
        if processor.mailbox_unreachable:
            return json_error(MAILBOX_UNREACHABLE_MESSAGE, 502)
        message = f"{len(orders)} bon(s) de commande détecté(s)"
        if processor.remaining_emails:
            message += f" ; {processor.remaining_emails} email(s) restent à traiter au prochain passage"
        if processor.failed_emails:
            message += f" ; {processor.failed_emails} email(s) n'ont pas pu être traités (voir le journal)"
        return jsonify({
            "success": True,
            "message": message,
            "orders_count": len(orders),
            "remaining_emails": processor.remaining_emails,
            "failed_emails": processor.failed_emails,
        })
    except Exception as exc:
        if _is_processing_already_running(exc):
            logger.info("Traitement des emails refusé : un traitement est déjà en cours")
            return json_error(PROCESSING_RUNNING_MESSAGE, 409)
        logger.exception("Échec du traitement des emails")
        return json_error("Le traitement des emails a échoué. Le détail est consigné dans le journal du serveur.", 500)


@bp.route("/api/orders/<int:order_id>/validate", methods=["POST"])
def api_validate_order(order_id: int):
    """Valide une commande en attente et prévient le client (email, et WhatsApp si la commande en vient).

    L'auteur enregistré est ``validated_by`` s'il est fourni, sinon l'utilisateur connecté. Une
    commande inexistante donne 404, une commande qui n'est plus en attente 409 (aucun message
    n'est alors envoyé).
    """
    services = get_services()
    order = services.db.get_order(order_id)
    if not order:
        return json_error("Commande introuvable", 404)
    validated_by = (_optional_text(_json_body().get("validated_by"), _MAX_VALIDATOR_LENGTH)
                    or current_user() or "Commercial")
    if not _decide(services, order_id, "validee", validated_by=validated_by):
        return _already_decided(services.db.get_order(order_id))

    # Commande relue après la validation : la notification contient le statut et la date enregistrés.
    order = services.db.get_order(order_id)
    return jsonify({"success": True, "message": "Commande validée",
                    **_notify_client(services, order, "order_validated")})


@bp.route("/api/orders/<int:order_id>/reject", methods=["POST"])
def api_reject_order(order_id: int):
    """Rejette une commande en attente, enregistre le motif et prévient le client.

    Une commande inexistante donne 404, un motif absent 400 (le motif est envoyé tel quel au
    client), une commande qui n'est plus en attente 409 (aucun message n'est alors envoyé).
    """
    services = get_services()
    order = services.db.get_order(order_id)
    if not order:
        return json_error("Commande introuvable", 404)
    reason = _optional_text(_json_body().get("reason")) or ""
    if not reason:
        return json_error("Indiquez un motif de rejet : il est communiqué au client.", 400)
    if not _decide(services, order_id, "rejetee", reason=reason):
        return _already_decided(services.db.get_order(order_id))

    order = services.db.get_order(order_id)
    return jsonify({"success": True, "message": "Commande rejetée",
                    **_notify_client(services, order, "order_rejected", reason)})


@bp.route("/api/orders/<int:order_id>/notify", methods=["POST"])
def api_notify_order(order_id: int):
    """Renvoie au client la notification de la décision (validation, ou rejet avec son motif).

    Une commande en attente ou supprimée donne 409 : il n'y a pas de décision à annoncer.
    """
    services = get_services()
    order = services.db.get_order(order_id)
    if not order:
        return json_error("Commande introuvable", 404)
    status = order.get("statut")
    if status == "validee":
        template = "order_validated"
    elif status == "rejetee":
        template = "order_rejected"
    else:
        label = STATUS_LABELS.get(status or "", status or "inconnu")
        return json_error(f"Aucune décision à annoncer pour une commande {label}.", 409, status=status)
    result = _notify_client(services, order, template, order.get("motif_rejet") or "")
    return jsonify({"success": True, "message": "Notification renvoyée", **result})


@bp.route("/api/orders/<int:order_id>/update", methods=["POST"])
def api_update_order(order_id: int):
    """Modifie les champs d'une commande ; le reste à livrer est recalculé si une quantité change.

    Seuls les champs de :data:`~tecpap.db.database.ORDER_UPDATABLE_FIELDS` sont acceptés ; les
    valeurs des champs numériques peuvent être des chaînes (formulaire HTML), une chaîne vide
    efface la valeur. Sur une commande validée, rejetée ou supprimée, un changement de
    :data:`DECIDED_ORDER_LOCKED_FIELDS` est refusé (409, ``rejected_fields``) : le client a été
    prévenu de ces valeurs ; une valeur identique à l'actuelle est acceptée. L'utilisateur connecté
    est inscrit dans la trace de la table ``logs``.
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
    if order.get("statut") != "en_attente":
        locked = sorted(name for name in DECIDED_ORDER_LOCKED_FIELDS
                        if name in updates and updates[name] != order.get(name))
        if locked:
            label = STATUS_LABELS.get(order.get("statut") or "", order.get("statut") or "")
            return json_error(f"Commande {label} : produit, quantité, prix et dates ne sont plus modifiables. "
                              "Champs refusés : " + ", ".join(locked), 409, rejected_fields=locked,
                              status=order.get("statut"))
    if "quantite" in updates or "quantite_livree" in updates:
        quantite = updates.get("quantite", order.get("quantite")) or 0
        quantite_livree = updates.get("quantite_livree", order.get("quantite_livree")) or 0
        updates["reste_a_livrer"] = quantite - quantite_livree

    try:
        db.update_order(order_id, updates)
    except Exception:
        logger.exception("Échec de la modification de la commande %s", order_id)
        return server_error()
    _attribute_last_log(get_services(), order_id, current_user())
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
        _attribute_last_log(get_services(), order_id, current_user())
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
    services = get_services()
    return jsonify(Analytics(services.db, getattr(services.settings, "timezone", None)).get_dashboard_stats())


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

    preferences = ClientHistory(services.db).get_client_preferences(client_id=client_id)
    predictor = AIPredictor(services.db, settings=services.settings, llm=services.llm)
    prediction = predictor.predict_client_behavior(client_id=client_id)
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
    """État du client Twilio : identifiants renseignés (``connected``), acceptés par Twilio (``verified``).

    L'identifiant de compte est tronqué et le numéro d'envoi est celui de la configuration.
    """
    whatsapp = get_services().whatsapp
    try:
        connected = whatsapp.connect()
        sender = (get_services().settings.twilio_whatsapp_number or "").removeprefix("whatsapp:")
        return jsonify({
            "connected": connected,
            "verified": bool(connected and whatsapp.verify_credentials()),
            "phone_number": sender or None,
            "account_sid": whatsapp.account_sid[:10] + "..." if whatsapp.account_sid else None,
        })
    except Exception:
        logger.exception("Échec de la vérification de la connexion Twilio")
        return jsonify({"connected": False, "error": GENERIC_ERROR_MESSAGE})
