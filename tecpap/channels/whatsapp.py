"""Canal WhatsApp par l'API Twilio : lecture des messages reçus, téléchargement des médias,
conversion des médias en texte, envoi de réponses.

Traitement par type de média (voir :mod:`tecpap.processing.multimodal`) :
- texte : transmis tel quel à l'extraction ;
- audio (notes vocales) : transcription ;
- image : lecture du texte par le modèle de vision ;
- PDF : extraction du texte avec pypdf.

Les médias ne sont téléchargés que depuis les serveurs de Twilio : l'adresse du média vient de
la requête reçue et le téléchargement transmet les identifiants Twilio, il ne doit donc jamais
viser un autre hôte. Un média téléchargé est supprimé dès que son texte a été lu : seul ce texte
est conservé (avec le message), pas les notes vocales, photos ni documents des clients.

Les numéros des clients n'apparaissent jamais en entier dans le journal
(:func:`tecpap.security.redaction.mask_sender`).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client as TwilioClient

from tecpap.config import Settings, load_settings
from tecpap.llm.client import LLMClient
from tecpap.processing.multimodal import (
    KIND_AUDIO,
    KIND_IMAGE,
    KIND_PDF,
    KIND_TEXT,
    KIND_UNKNOWN,
    MediaProcessor,
    MediaText,
    base_mime,
    extension_for_mime,
)
from tecpap.security.redaction import mask_sender

logger = logging.getLogger(__name__)

# Hôtes autorisés pour le téléchargement des médias (l'hôte exact ou l'un de ses sous-domaines).
ALLOWED_MEDIA_HOSTS = ("twilio.com",)

AUDIO_MARKERS = ("audio", "ogg", "opus", "mpeg", "mp3", "wav", "m4a", "voice")

# Type de message (champ ``type`` du résultat) selon le type de média détecté dans le fichier.
MESSAGE_TYPES = {
    KIND_IMAGE: "image",
    KIND_AUDIO: "audio",
    KIND_PDF: "document",
    KIND_TEXT: "document",
}

# Préfixe du nom de fichier enregistré, selon le type de message.
FILE_PREFIXES = {"image": "image", "audio": "audio", "document": "doc"}

DOWNLOAD_TIMEOUT_SECONDS = 30
VERIFY_TIMEOUT_SECONDS = 5


def is_allowed_media_url(url: str | None) -> bool:
    """Vrai si ``url`` est une adresse HTTPS d'un serveur Twilio."""
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_MEDIA_HOSTS)


def declared_message_type(media_type: str) -> str:
    """Type de message annoncé par le type MIME du média : image, audio, document ou file."""
    media_type = media_type.lower()
    if "image" in media_type:
        return "image"
    if any(marker in media_type for marker in AUDIO_MARKERS):
        return "audio"
    if "pdf" in media_type or "document" in media_type or media_type.startswith("text/"):
        return "document"
    return "file"


def _timestamp() -> str:
    """Horodatage UTC des noms de fichiers des médias."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def remove_media_file(filepath: str) -> bool:
    """Supprime un média téléchargé ; renvoie ``True`` si le fichier a été supprimé."""
    try:
        os.remove(filepath)
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Suppression du média %s impossible", os.path.basename(filepath), exc_info=True)
        return False
    logger.debug("Média supprimé après lecture : %s", os.path.basename(filepath))
    return True


class WhatsAppReceiver:
    """Client WhatsApp (Twilio) avec conversion en texte des médias reçus.

    Args:
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
        llm: client des modèles de langue (transcription, vision) ; par défaut construit depuis
            la configuration. Sans clé d'API, seuls les PDF sont lus.
        media: convertisseur des médias en texte ; par défaut construit avec ``llm`` et
            ``settings``.
    """

    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None,
                 media: MediaProcessor | None = None):
        self.settings = settings or load_settings()
        self.account_sid = self.settings.twilio_account_sid
        self.auth_token = self.settings.twilio_auth_token
        self.llm = llm if llm is not None else LLMClient.from_settings(self.settings)
        self.media = media if media is not None else MediaProcessor(self.llm, self.settings)
        self.twilio_client: TwilioClient | None = None
        self.media_dir = self.settings.whatsapp_media_dir

    # ------------------------------------------------------------------ Twilio

    def connect(self) -> bool:
        """Construit le client Twilio. Renvoie ``False`` si les identifiants manquent."""
        if not self.account_sid or not self.auth_token:
            logger.warning("Identifiants Twilio absents (TWILIO_ACCOUNT_SID et TWILIO_AUTH_TOKEN)")
            return False
        try:
            self.twilio_client = TwilioClient(self.account_sid, self.auth_token)
            logger.info("Client Twilio prêt")
            return True
        except Exception:
            logger.exception("Création du client Twilio impossible")
            return False

    def verify_credentials(self) -> bool:
        """Vrai si Twilio accepte les identifiants (lecture du compte, avec un délai de quelques secondes)."""
        if not self.account_sid or not self.auth_token:
            return False
        try:
            client = TwilioClient(self.account_sid, self.auth_token,
                                  http_client=TwilioHttpClient(timeout=VERIFY_TIMEOUT_SECONDS))
            client.api.accounts(self.account_sid).fetch()
            return True
        except Exception:
            logger.warning("Identifiants Twilio refusés ou service injoignable", exc_info=True)
            return False

    def send_reply(self, to_number: str, message: str) -> str:
        """Envoie un message WhatsApp et renvoie son identifiant Twilio (SID).

        Lève une exception si l'envoi échoue, pour que l'appelant sache que le client n'a pas
        été prévenu.
        """
        if not self.twilio_client and not self.connect():
            raise RuntimeError("Client Twilio non configuré : envoi WhatsApp impossible")

        clean_number = to_number.replace("whatsapp:", "").strip()
        if not clean_number.startswith("+"):
            clean_number = "+" + clean_number
        to_whatsapp = f"whatsapp:{clean_number}"

        try:
            msg = self.twilio_client.messages.create(
                body=message,
                from_=self.settings.twilio_whatsapp_number,
                to=to_whatsapp,
            )
        except Exception:
            logger.exception("Échec de l'envoi WhatsApp à %s", mask_sender(to_whatsapp))
            raise
        logger.info("Message WhatsApp envoyé à %s (SID %s)", mask_sender(to_whatsapp), msg.sid)
        return msg.sid

    # ------------------------------------------------------------------ messages reçus

    def process_incoming_message(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Analyse un message reçu par le webhook Twilio et en extrait le contenu textuel.

        Args:
            message_data: champs du formulaire envoyé par Twilio (``From``, ``Body``,
                ``MessageSid``, ``NumMedia``, ``MediaUrl0``, ``MediaContentType0``…).

        Returns:
            Un dictionnaire avec le type de message (``text``, ``image``, ``audio``,
            ``document``, ``file`` ou ``unknown``), l'expéditeur, l'identifiant Twilio du
            message (``message_sid``), le type MIME du média (``media_type``), le texte, la
            transcription audio, le texte extrait d'un document ou d'une image et, en cas
            d'échec de la lecture du média, sa cause (``media_error``).
        """
        result: dict[str, Any] = {
            "type": "unknown",
            "from": message_data.get("From", "").replace("whatsapp:", ""),
            "to": message_data.get("To", "").replace("whatsapp:", ""),
            "message_sid": message_data.get("MessageSid") or message_data.get("SmsMessageSid") or None,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "content": None,
            "media_url": None,
            "media_type": None,
            "media_error": None,
            "transcription": None,
            "extracted_text": None,
        }

        body = message_data.get("Body", "").strip()
        if body:
            result["type"] = "text"
            result["content"] = body

        try:
            num_media = int(message_data.get("NumMedia", 0) or 0)
        except (TypeError, ValueError):
            num_media = 0

        if num_media > 0:
            media_url = message_data.get("MediaUrl0")
            media_type = base_mime(message_data.get("MediaContentType0"))
            result["media_url"] = media_url
            result["media_type"] = media_type or None
            result["type"] = declared_message_type(media_type)
            logger.info("Média reçu de type %s", media_type or "inconnu")

            media_text = self.process_media(media_url, media_type)
            self._apply_media_text(result, media_text)

        return result

    def process_media(self, media_url: str | None, media_type: str) -> MediaText:
        """Télécharge un média Twilio, le convertit en texte, puis supprime le fichier téléchargé.

        Le téléchargement est évité quand le média ne pourrait pas être lu : type non pris en
        charge, ou image et audio sans clé d'API pour les modèles de vision et de transcription.
        """
        message_type = declared_message_type(media_type)
        if message_type == "file":
            return MediaText(KIND_UNKNOWN, None, "type de média non pris en charge")
        if message_type in ("image", "audio") and not self.media.llm_available:
            logger.warning("Média %s non lu : clé OPENAI_API_KEY absente", message_type)
            kind = KIND_IMAGE if message_type == "image" else KIND_AUDIO
            return MediaText(kind, None, "lecture du média indisponible : clé OPENAI_API_KEY absente")

        filename = f"{FILE_PREFIXES[message_type]}_{_timestamp()}{extension_for_mime(media_type)}"
        filepath = self.download_media(media_url, filename)
        if not filepath:
            return MediaText(KIND_UNKNOWN, None, "téléchargement du média impossible")
        try:
            return self.media.process_file(filepath, declared_mime=media_type)
        finally:
            remove_media_file(filepath)

    @staticmethod
    def _apply_media_text(result: dict[str, Any], media_text: MediaText) -> None:
        """Range le texte d'un média dans le résultat du message, selon le type détecté."""
        if media_text.kind in MESSAGE_TYPES:
            result["type"] = MESSAGE_TYPES[media_text.kind]
        if media_text.error:
            result["media_error"] = media_text.error
            logger.info("Média non lu : %s", media_text.error)
            return

        if result["type"] == "audio":
            result["transcription"] = media_text.text
            result["content"] = media_text.text
        else:
            result["extracted_text"] = media_text.text

    def format_for_extraction(self, message_result: dict[str, Any]) -> dict[str, Any]:
        """Présente un message WhatsApp sous la forme d'un email (sujet, expéditeur, corps).

        L'extracteur de commandes traite ainsi les deux canaux de la même façon.
        """
        parts = []
        if message_result.get("content"):
            parts.append(f"Message: {message_result['content']}")
        if message_result.get("transcription"):
            parts.append(f"Audio transcription: {message_result['transcription']}")
        if message_result.get("extracted_text"):
            parts.append(f"Document/Image content: {message_result['extracted_text']}")

        return {
            "subject": f"WhatsApp - {message_result['from']}",
            "from": message_result["from"],
            "date": message_result["timestamp"],
            "body": "\n\n".join(parts),
            "source": "whatsapp",
        }

    # ------------------------------------------------------------------ médias

    def download_media(self, media_url: str | None, filename: str | None = None) -> str | None:
        """Télécharge un média Twilio dans ``Settings.whatsapp_media_dir``.

        Le téléchargement est refusé pour une adresse qui n'appartient pas à Twilio et pour un
        fichier plus gros que ``Settings.max_media_bytes``.

        Returns:
            Le chemin du fichier enregistré, ou ``None``.
        """
        if not is_allowed_media_url(media_url):
            logger.warning("Téléchargement refusé : adresse de média hors Twilio (%s)", media_url)
            return None
        try:
            response = requests.get(media_url, auth=(self.account_sid, self.auth_token),
                                    timeout=DOWNLOAD_TIMEOUT_SECONDS)
        except requests.RequestException:
            logger.exception("Téléchargement du média impossible")
            return None

        if response.status_code != 200:
            logger.error("Téléchargement du média en échec (code HTTP %s)", response.status_code)
            return None
        if len(response.content) > self.settings.max_media_bytes:
            logger.warning("Média ignoré : %d octets, limite %d", len(response.content), self.settings.max_media_bytes)
            return None

        if not filename:
            filename = f"media_{_timestamp()}.bin"
        os.makedirs(self.media_dir, exist_ok=True)
        filepath = os.path.join(self.media_dir, os.path.basename(filename))
        with open(filepath, "wb") as f:
            f.write(response.content)
        logger.info("Média enregistré : %s", filepath)
        return filepath


WhatsAppClient = WhatsAppReceiver
"""Nom générique du client WhatsApp (même classe que :class:`WhatsAppReceiver`)."""
