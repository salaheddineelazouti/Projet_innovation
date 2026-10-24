"""Webhook WhatsApp : réception des messages envoyés par Twilio.

Chaque requête doit porter l'en-tête ``X-Twilio-Signature`` : Twilio signe l'URL appelée et
les paramètres du message avec le jeton d'authentification du compte (HMAC-SHA1, code
d'authentification de message à clé). Une requête sans signature valide est refusée (403) sans
être traitée : personne d'autre que Twilio ne peut ainsi créer de commande par ce webhook.

Chaque message (texte, note vocale, image, PDF) est ensuite converti en texte, passé à
l'extracteur de commandes et, s'il s'agit d'un bon de commande, enregistré « en attente » de
validation. La réponse au client est renvoyée en TwiML (format XML de Twilio), que Twilio
transmet sur WhatsApp.
"""

from __future__ import annotations

import logging

from flask import Blueprint, Response, request
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from tecpap.config import Settings
from tecpap.extraction.extractor import DataExtractor
from tecpap.web.services import get_services

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)

SIGNATURE_HEADER = "X-Twilio-Signature"

NOT_AN_ORDER_REPLY = (
    "Message reçu. Si vous souhaitez passer une commande, veuillez préciser les détails "
    "(client, produit, quantité)."
)
PROCESSING_ERROR_REPLY = (
    "Votre message n'a pas pu être traité. Veuillez réessayer dans quelques minutes "
    "ou contacter notre service commercial."
)


def _order_received_reply(order_data: dict, client_name: str) -> str:
    return f"""✅ Commande reçue !

📦 Client: {client_name}
📋 Produit: {order_data.get('type_produit', 'N/A')}
🔢 Quantité: {order_data.get('quantite', 'N/A')} {order_data.get('unite', '')}

Votre commande est en attente de validation."""


def _twiml_reply(message: str) -> tuple[str, int, dict[str, str]]:
    twiml = MessagingResponse()
    twiml.message(message)
    return str(twiml), 200, {"Content-Type": "application/xml"}


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


def log_signature_settings(settings: Settings) -> None:
    """Signale au démarrage une configuration du webhook qui refuse ou accepte tous les messages."""
    if settings.twilio_validate_signature and not settings.twilio_auth_token:
        logger.error("TWILIO_VALIDATE_SIGNATURE est activé sans TWILIO_AUTH_TOKEN : le webhook WhatsApp "
                     "refusera tous les messages (503)")
    elif not settings.twilio_validate_signature and not settings.is_testing:
        logger.warning("Vérification de la signature Twilio désactivée (TWILIO_VALIDATE_SIGNATURE=false) : "
                       "n'importe qui peut appeler le webhook WhatsApp")


@bp.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Traite un message WhatsApp entrant et répond en TwiML."""
    services = get_services()
    rejection = check_twilio_signature(services.settings)
    if rejection is not None:
        return rejection

    try:
        message_data = request.form.to_dict()
        from_number = message_data.get("From", "N/A")
        logger.info("Message WhatsApp reçu de %s (%s média(s))", from_number, message_data.get("NumMedia", 0))

        result = services.whatsapp.process_incoming_message(message_data)
        formatted = services.whatsapp.format_for_extraction(result)

        # Le client des modèles de langue est celui de l'application, partagé par toutes les requêtes.
        extractor = DataExtractor(db_manager=services.db, settings=services.settings, llm=services.llm)
        order_data = extractor.extract_from_email(formatted)

        if order_data and order_data.get("est_bon_commande"):
            order_data["email_id"] = f"whatsapp_{result['timestamp']}"
            order_data["email_subject"] = f"WhatsApp - {from_number}"
            order_data["email_from"] = from_number
            order_data["source"] = "whatsapp"
            order_data["whatsapp_from"] = from_number
            # Sans nom de client extrait, le numéro de téléphone sert de nom provisoire.
            if not order_data.get("entreprise_cliente"):
                order_data["entreprise_cliente"] = f"Client WhatsApp {from_number.replace('whatsapp:', '')}"

            order_id = services.db.create_order(order_data)
            logger.info("Commande WhatsApp enregistrée (id %s)", order_id)
            response_message = _order_received_reply(order_data, order_data.get("entreprise_cliente", from_number))
        else:
            logger.info("Message WhatsApp sans bon de commande")
            response_message = NOT_AN_ORDER_REPLY

        return _twiml_reply(response_message)

    except Exception:
        # Le client reçoit un message générique : le détail de l'erreur reste dans le journal.
        logger.exception("Erreur du webhook WhatsApp")
        return _twiml_reply(PROCESSING_ERROR_REPLY)
