"""Webhook WhatsApp : réception des messages envoyés par Twilio.

Chaque message (texte, note vocale, image, PDF) est converti en texte, passé à l'extracteur
de commandes et, s'il s'agit d'un bon de commande, enregistré « en attente » de validation.
La réponse au client est renvoyée en TwiML (format XML de Twilio), que Twilio transmet sur
WhatsApp.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from twilio.twiml.messaging_response import MessagingResponse

from tecpap.extraction.extractor import DataExtractor
from tecpap.web.services import get_services

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)

NOT_AN_ORDER_REPLY = (
    "Message reçu. Si vous souhaitez passer une commande, veuillez préciser les détails "
    "(client, produit, quantité)."
)


def _order_received_reply(order_data: dict, client_name: str) -> str:
    return f"""✅ Commande reçue !

📦 Client: {client_name}
📋 Produit: {order_data.get('type_produit', 'N/A')}
🔢 Quantité: {order_data.get('quantite', 'N/A')} {order_data.get('unite', '')}

Votre commande est en attente de validation."""


@bp.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Traite un message WhatsApp entrant et répond en TwiML."""
    services = get_services()
    try:
        message_data = request.form.to_dict()
        from_number = message_data.get("From", "N/A")
        logger.info("Message WhatsApp reçu de %s (%s média(s))", from_number, message_data.get("NumMedia", 0))

        result = services.whatsapp.process_incoming_message(message_data)
        formatted = services.whatsapp.format_for_extraction(result)

        extractor = DataExtractor(db_manager=services.db, settings=services.settings)
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

        twiml = MessagingResponse()
        twiml.message(response_message)
        return str(twiml), 200, {"Content-Type": "application/xml"}

    except Exception as exc:
        logger.exception("Erreur du webhook WhatsApp")
        return jsonify({"error": str(exc)}), 500
