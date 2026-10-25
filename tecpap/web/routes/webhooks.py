"""Webhook WhatsApp : réception des messages envoyés par Twilio.

Chaque requête doit porter l'en-tête ``X-Twilio-Signature`` : Twilio signe l'URL appelée et
les paramètres du message avec le jeton d'authentification du compte (HMAC-SHA1, code
d'authentification de message à clé). Une requête sans signature valide est refusée (403) sans
être traitée : personne d'autre que Twilio ne peut ainsi créer de commande par ce webhook. Le refus
est enregistré comme événement de sécurité (nature ``invalid_signature``, numéro masqué), visible
sur la page Sécurité du tableau de bord.

Traitement d'un message signé, dans cet ordre :

1. déduplication avant tout traitement : un message dont l'identifiant Twilio (``MessageSid``)
   est déjà enregistré (messages des conversations, ou commande créée par ce message) ou en
   cours de traitement ne reçoit aucune réponse : Twilio peut livrer deux fois le même message ;
2. lecture du message (:meth:`tecpap.channels.whatsapp.WhatsAppReceiver.process_incoming_message`) :
   le texte analysé réunit le corps du message, la transcription d'une note vocale et le texte
   lu dans une image ou un PDF ; un média illisible sans autre texte reçoit le message
   « media_failed » ;
3. avec l'assistant activé (``CHATBOT_ENABLED``), le message est confié à
   :class:`tecpap.chatbot.bot.WhatsAppAssistant` (conversation, commande confirmée par le client,
   suivi, questions) ; sinon, traitement en une fois : extraction
   (:meth:`tecpap.extraction.extractor.DataExtractor.extract_text`), commande enregistrée « en
   attente » de validation quand c'est un bon de commande, accusé de réception rédigé à partir
   d'un modèle fixe ;
4. réponse : avec ``WHATSAPP_ASYNC_REPLY``, le webhook répond aussitôt par un TwiML vide (format
   XML de Twilio, ici sans message), le traitement a lieu en tâche de fond
   (:class:`tecpap.web.worker.BackgroundWorker`) et la réponse part par l'API REST de Twilio ;
   sinon, la réponse est renvoyée dans le TwiML de la requête.

Une erreur de traitement donne un message générique au client : le détail reste dans le journal.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, request
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from tecpap.chatbot.messages import render
from tecpap.config import Settings
from tecpap.extraction.rules import normalize_phone
from tecpap.nlp.language import detect_language, reply_language
from tecpap.security.events import KIND_INVALID_SIGNATURE, log_security_event
from tecpap.security.redaction import mask_sender
from tecpap.web.services import Services, get_services

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)

SIGNATURE_HEADER = "X-Twilio-Signature"

NOT_AN_ORDER_REPLY = (
    "Message reçu. Si vous souhaitez passer une commande, veuillez préciser les détails "
    "(client, produit, quantité)."
)
NOT_AN_ORDER_REPLIES: dict[str, str] = {
    "fr": NOT_AN_ORDER_REPLY,
    "darija": "Wsalni l'message. Ila bghiti tsift commande, kteb les détails (client, produit, quantité).",
    "ar": "تم استلام رسالتك. إذا أردت تقديم طلب، يرجى ذكر التفاصيل (الزبون، المنتج، الكمية).",
}
"""Réponse à un message qui n'est pas un bon de commande (traitement sans assistant), par langue."""

PROCESSING_ERROR_REPLY = (
    "Votre message n'a pas pu être traité. Veuillez réessayer dans quelques minutes "
    "ou contacter notre service commercial."
)


def _twiml_reply(message: str) -> tuple[str, int, dict[str, str]]:
    twiml = MessagingResponse()
    twiml.message(message)
    return str(twiml), 200, {"Content-Type": "application/xml"}


def _empty_twiml() -> tuple[str, int, dict[str, str]]:
    """Réponse TwiML sans message : Twilio n'envoie rien au client."""
    return str(MessagingResponse()), 200, {"Content-Type": "application/xml"}


def signed_url(settings: Settings) -> str:
    """URL que Twilio a signée.

    Derrière un tunnel ou un proxy inverse, l'URL reçue par l'application (``http://127.0.0.1:5000/…``)
    diffère de celle que Twilio a appelée : on reconstruit alors celle-ci à partir de
    ``PUBLIC_BASE_URL`` suivie du chemin et des paramètres de la requête.
    """
    if settings.public_base_url:
        path = request.full_path if request.query_string else request.path
        return settings.public_base_url.rstrip("/") + path
    return request.url


def check_twilio_signature(settings: Settings) -> Response | None:
    """Vérifie la signature Twilio de la requête courante.

    Returns:
        ``None`` si la requête peut être traitée, sinon la réponse de refus : 403 pour une
        signature absente ou invalide, 503 quand la vérification est activée sans jeton
        d'authentification Twilio configuré.
    """
    if not settings.twilio_validate_signature:
        return None
    if not settings.twilio_auth_token:
        logger.error("Webhook WhatsApp : TWILIO_VALIDATE_SIGNATURE est activé mais TWILIO_AUTH_TOKEN n'est pas "
                     "défini, la signature ne peut pas être vérifiée ; message refusé")
        return Response("Service indisponible", status=503, mimetype="text/plain")

    signature = request.headers.get(SIGNATURE_HEADER, "")
    url = signed_url(settings)
    if not signature or not RequestValidator(settings.twilio_auth_token).validate(url, request.form, signature):
        logger.warning("Webhook WhatsApp : requête refusée, signature Twilio %s (URL vérifiée %s, adresse %s)",
                       "invalide" if signature else "absente", url, request.remote_addr)
        return Response("Signature invalide", status=403, mimetype="text/plain")
    return None


def log_invalid_signature(services: Services) -> None:
    """Enregistre le refus d'une requête mal signée comme événement de sécurité.

    Le numéro ``From`` de la requête n'est pas authentifié : il est enregistré masqué, comme
    tout expéditeur d'événement. Une erreur d'écriture est journalisée sans changer la réponse.
    """
    state = "invalide" if request.headers.get(SIGNATURE_HEADER) else "absente"
    try:
        log_security_event(services.db.connection, channel="whatsapp", sender=request.form.get("From"),
                           kind=KIND_INVALID_SIGNATURE,
                           details=f"Signature Twilio {state} (adresse {request.remote_addr or 'inconnue'})")
    except Exception:
        logger.warning("Événement de sécurité non enregistré pour une signature Twilio %s", state, exc_info=True)


def log_signature_settings(settings: Settings) -> None:
    """Signale au démarrage une configuration du webhook qui refuse ou accepte tous les messages."""
    if settings.twilio_validate_signature and not settings.twilio_auth_token:
        logger.error("TWILIO_VALIDATE_SIGNATURE est activé sans TWILIO_AUTH_TOKEN : le webhook WhatsApp "
                     "refusera tous les messages (503)")
    elif not settings.twilio_validate_signature and not settings.is_testing:
        logger.warning("Vérification de la signature Twilio désactivée (TWILIO_VALIDATE_SIGNATURE=false) : "
                       "n'importe qui peut appeler le webhook WhatsApp")


# ---------------------------------------------------------------------- lecture du message

def message_sid_of(form: Mapping[str, Any]) -> str | None:
    """Identifiant Twilio du message (``MessageSid``, à défaut ``SmsMessageSid``), ou ``None``."""
    sid = str(form.get("MessageSid") or form.get("SmsMessageSid") or "").strip()
    return sid or None


def message_text(form: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    """Texte analysé : corps du message, transcription de la note vocale et texte lu dans le média."""
    parts: list[str] = []
    for value in (form.get("Body"), result.get("transcription"), result.get("extracted_text")):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "\n\n".join(parts)


def _media_kind(form: Mapping[str, Any], result: Mapping[str, Any]) -> str | None:
    """Nature du média joint (« audio », « image », « document »…), ou ``None`` sans média."""
    try:
        has_media = int(form.get("NumMedia") or 0) > 0
    except (TypeError, ValueError):
        has_media = False
    return str(result.get("type") or "media") if has_media else None


# ---------------------------------------------------------------------- traitement

def handle_incoming_message(services: Services, form: Mapping[str, Any]) -> str | None:
    """Traite un message WhatsApp signé et renvoie la réponse à envoyer, ou ``None`` pour ne rien envoyer.

    Le message n'est pas dédupliqué ici : :func:`whatsapp_webhook` l'a déjà fait.
    """
    sender = str(form.get("From") or "").strip()
    sid = message_sid_of(form)
    result = services.whatsapp.process_incoming_message(dict(form))
    text = message_text(form, result)
    media_kind = _media_kind(form, result)
    if result.get("media_error"):
        logger.info("Média WhatsApp illisible (%s)", result["media_error"])

    if services.settings.chatbot_enabled:
        with services.sender_lock(sender):
            reply = services.assistant().handle(sender, text, message_sid=sid, media_kind=media_kind)
        logger.info("Réponse de l'assistant WhatsApp : %s%s", reply.intent, " (bloquée)" if reply.blocked else "")
        return None if reply.silent else reply.text
    with services.sender_lock(sender):
        return _one_shot_reply(services, sender, sid, text, media_kind)


def _one_shot_reply(services: Services, sender: str, sid: str | None, text: str,
                    media_kind: str | None) -> str | None:
    """Traitement sans assistant : extraction, commande enregistrée si c'en est une, accusé de réception."""
    store = services.conversation_store()
    conversation = store.get_or_create(sender)
    body = f"[{media_kind}] {text}".strip() if media_kind else text
    inbound = store.add_message(conversation.id, "in", body, message_sid=sid)
    if inbound is None:
        return None
    if text.strip():
        conversation.language = reply_language(detect_language(text))
    language = conversation.language or "fr"

    order_id = None
    if not text.strip() and media_kind:
        intent, reply = "media_failed", render("media_failed", language)
    elif not text.strip():
        intent, reply = "other", NOT_AN_ORDER_REPLIES.get(language, NOT_AN_ORDER_REPLY)
    else:
        order = services.extractor.extract_text(text, sender=sender, channel="whatsapp")
        if order and order.get("est_bon_commande"):
            order_id = _save_whatsapp_order(services, order, sender, sid)
            intent = "order"
            reply = render("submitted", language, reference=order.get("numero_commande") or f"CMD-{order_id}")
        else:
            logger.info("Message WhatsApp sans bon de commande")
            intent, reply = "other", NOT_AN_ORDER_REPLIES.get(language, NOT_AN_ORDER_REPLY)

    store.update_message(inbound, intent=intent)
    store.add_message(conversation.id, "out", reply, intent=intent)
    if order_id is not None:
        conversation.last_order_id = order_id
    store.save(conversation)
    return reply


def _save_whatsapp_order(services: Services, order: dict[str, Any], sender: str, sid: str | None) -> int:
    """Enregistre une commande WhatsApp « en attente » ; sans nom de client, le numéro sert de nom provisoire."""
    phone = sender.replace("whatsapp:", "").strip()
    order.update({
        "email_id": f"whatsapp_{sid}" if sid else f"whatsapp_{datetime.now().isoformat()}",
        "email_subject": f"WhatsApp - {sender}",
        "email_from": sender,
        "source": "whatsapp",
        "whatsapp_from": sender,
        "message_sid": sid,
    })
    if not order.get("entreprise_cliente"):
        order["entreprise_cliente"] = f"Client WhatsApp {phone}"
    order_id = services.db.create_order(order)
    logger.info("Commande WhatsApp enregistrée (id %s)", order_id)
    return order_id


def process_and_send(services: Services, form: Mapping[str, Any]) -> None:
    """Tâche de fond : traite le message puis envoie la réponse par l'API REST de Twilio.

    Une erreur de traitement donne le message générique ; un échec d'envoi est consigné dans le journal.
    """
    sid = message_sid_of(form)
    try:
        services.db.connect()
        try:
            reply = handle_incoming_message(services, form)
        except Exception:
            logger.exception("Traitement différé d'un message WhatsApp impossible")
            reply = PROCESSING_ERROR_REPLY
        if reply:
            try:
                services.whatsapp.send_reply(str(form.get("From") or ""), reply)
            except Exception:
                logger.exception("Réponse WhatsApp différée non envoyée à %s", mask_sender(form.get("From")))
    finally:
        services.release_message(sid)


@bp.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Reçoit un message WhatsApp ; répond en TwiML, tout de suite (réponse différée) ou après traitement."""
    services = get_services()
    rejection = check_twilio_signature(services.settings)
    if rejection is not None:
        if rejection.status_code == 403:
            log_invalid_signature(services)
        return rejection

    sid: str | None = None
    claimed = handed_off = False
    try:
        form = request.form.to_dict()
        sid = message_sid_of(form)
        sender = form.get("From", "")
        logger.info("Message WhatsApp reçu de %s (%s média(s))", mask_sender(sender), form.get("NumMedia", 0))
        if normalize_phone(sender) is None:
            logger.warning("Message WhatsApp ignoré : expéditeur non reconnu")
            return _empty_twiml()
        if services.is_duplicate_message(sid) or not services.claim_message(sid):
            logger.info("Message WhatsApp déjà reçu : ignoré")
            return _empty_twiml()
        claimed = True

        if services.settings.whatsapp_async_reply:
            if services.worker.submit(process_and_send, services, form) is not None:
                handed_off = True
                return _empty_twiml()
            logger.warning("Message WhatsApp traité sans délai : tâches de fond indisponibles")
        reply = handle_incoming_message(services, form)
        return _twiml_reply(reply) if reply else _empty_twiml()
    except Exception:
        # Le client reçoit un message générique : le détail de l'erreur reste dans le journal.
        logger.exception("Erreur du webhook WhatsApp")
        return _twiml_reply(PROCESSING_ERROR_REPLY)
    finally:
        if claimed and not handed_off:
            services.release_message(sid)
