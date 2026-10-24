"""Canal WhatsApp par l'API Twilio : lecture des messages reçus, téléchargement des médias,
transcription des notes vocales, extraction du texte des images et des PDF, envoi de réponses.

Traitement par type de média :
- texte : transmis tel quel à l'extraction ;
- audio (notes vocales) : transcription par le modèle Whisper d'OpenAI ;
- image : lecture du texte par le modèle de vision d'OpenAI ;
- PDF : extraction du texte avec pypdf.

Les médias ne sont téléchargés que depuis les serveurs de Twilio : l'adresse du média vient de
la requête reçue et le téléchargement transmet les identifiants Twilio, il ne doit donc jamais
viser un autre hôte.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import pypdf
import requests
from openai import OpenAI
from twilio.rest import Client as TwilioClient

from tecpap.config import Settings, load_settings

logger = logging.getLogger(__name__)

# Hôtes autorisés pour le téléchargement des médias (l'hôte exact ou l'un de ses sous-domaines).
ALLOWED_MEDIA_HOSTS = ("twilio.com",)

AUDIO_MARKERS = ("audio", "ogg", "opus", "mpeg", "mp3", "wav", "m4a", "voice")

AUDIO_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/aac": "aac",
}

# Vocabulaire transmis à Whisper pour orienter la transcription des commandes en darija,
# en arabe et en français (mots d'introduction du client, produits, quantités).
TRANSCRIPTION_PROMPT = (
    "Commande commerciale au Maroc. Introduction client: ana restaurant, ana snack, ana café, "
    "أنا ريستوران, أنا سناك. Termes: sachets, sac kraft, carton, papier, emballage, pièces, unités, "
    "commande, livraison, dirhams. Darija: bghit, khassni, 3tini, sachet, sac, carton, kraft, fond plat, "
    "poignées, sandwich, tacos. Noms: Salah Eddine, Mohamed, Ahmed, Hassan, Youssef."
)

IMAGE_TEXT_INSTRUCTION = "Extrais tout le texte visible dans cette image. Retourne uniquement le texte extrait."

# Taille en dessous de laquelle un fichier audio est considéré comme vide.
MIN_AUDIO_BYTES = 100


def is_allowed_media_url(url: str | None) -> bool:
    """Vrai si ``url`` est une adresse HTTPS d'un serveur Twilio."""
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_MEDIA_HOSTS)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class WhatsAppReceiver:
    """Client WhatsApp (Twilio) avec traitement multimodal des messages reçus.

    Args:
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
        openai_client: client OpenAI déjà construit (utile pour les tests). Par défaut, un
            client est créé seulement si ``OPENAI_API_KEY`` est défini.
    """

    def __init__(self, settings: Settings | None = None, openai_client: Any | None = None):
        self.settings = settings or load_settings()
        self.account_sid = self.settings.twilio_account_sid
        self.auth_token = self.settings.twilio_auth_token
        if openai_client is not None:
            self.openai_client = openai_client
        elif self.settings.openai_api_key:
            self.openai_client = OpenAI(api_key=self.settings.openai_api_key, timeout=self.settings.llm_timeout)
        else:
            self.openai_client = None
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
            logger.exception("Échec de l'envoi WhatsApp à %s", to_whatsapp)
            raise
        logger.info("Message WhatsApp envoyé à %s", to_whatsapp)
        return msg.sid

    # ------------------------------------------------------------------ messages reçus

    def process_incoming_message(self, message_data: dict[str, Any]) -> dict[str, Any]:
        """Analyse un message reçu par le webhook Twilio et en extrait le contenu textuel.

        Args:
            message_data: champs du formulaire envoyé par Twilio (``From``, ``Body``,
                ``NumMedia``, ``MediaUrl0``, ``MediaContentType0``…).

        Returns:
            Un dictionnaire avec le type de message, l'expéditeur, le texte, la transcription
            audio et le texte extrait d'un document ou d'une image.
        """
        result: dict[str, Any] = {
            "type": "unknown",
            "from": message_data.get("From", "").replace("whatsapp:", ""),
            "to": message_data.get("To", "").replace("whatsapp:", ""),
            "timestamp": datetime.now().isoformat(),
            "content": None,
            "media_url": None,
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
            media_type = message_data.get("MediaContentType0", "").lower()
            result["media_url"] = media_url
            logger.info("Média reçu de type %s", media_type)

            if "image" in media_type:
                result["type"] = "image"
                result["extracted_text"] = self.extract_text_from_image(media_url)
            elif any(marker in media_type for marker in AUDIO_MARKERS):
                result["type"] = "audio"
                result["transcription"] = self.transcribe_audio(media_url, media_type)
                result["content"] = result["transcription"]
            elif "pdf" in media_type or "document" in media_type:
                result["type"] = "document"
                result["extracted_text"] = self.extract_text_from_document(media_url, media_type)
            else:
                result["type"] = "file"

        return result

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

    def download_media(self, media_url: str, filename: str | None = None) -> str | None:
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
            response = requests.get(media_url, auth=(self.account_sid, self.auth_token), timeout=30)
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
            ext = media_url.split(".")[-1][:4] if "." in media_url else "bin"
            filename = f"media_{_timestamp()}.{ext}"
        os.makedirs(self.media_dir, exist_ok=True)
        filepath = os.path.join(self.media_dir, os.path.basename(filename))
        with open(filepath, "wb") as f:
            f.write(response.content)
        logger.info("Média enregistré : %s", filepath)
        return filepath

    def transcribe_audio(self, audio_url: str, media_type: str = "audio/ogg") -> str | None:
        """Transcrit une note vocale avec Whisper (français, arabe et darija).

        La langue transmise à Whisper est ``Settings.transcription_language`` ; sans valeur,
        Whisper détecte la langue lui-même.
        """
        if not self.openai_client:
            logger.warning("Transcription impossible : OPENAI_API_KEY absent")
            return None

        ext = next((value for key, value in AUDIO_EXTENSIONS.items() if key in media_type.lower()), "ogg")
        filepath = self.download_media(audio_url, f"audio_{_timestamp()}.{ext}")
        if not filepath:
            return None

        file_size = os.path.getsize(filepath)
        if file_size < MIN_AUDIO_BYTES:
            logger.warning("Fichier audio vide ou trop court (%d octets)", file_size)
            return None

        options: dict[str, Any] = {
            "model": self.settings.model_transcription,
            "response_format": "text",
            "prompt": TRANSCRIPTION_PROMPT,
        }
        if self.settings.transcription_language:
            options["language"] = self.settings.transcription_language

        try:
            with open(filepath, "rb") as audio_file:
                transcription = self.openai_client.audio.transcriptions.create(file=audio_file, **options)
        except Exception:
            logger.exception("Échec de la transcription audio")
            return None

        if not transcription:
            logger.warning("Transcription vide")
        return transcription

    def extract_text_from_image(self, image_url: str) -> str | None:
        """Lit le texte d'une image avec le modèle de vision (``Settings.model_vision``)."""
        if not self.openai_client:
            logger.warning("Lecture d'image impossible : OPENAI_API_KEY absent")
            return None

        filepath = self.download_media(image_url, f"image_{_timestamp()}.jpg")
        if not filepath:
            return None

        with open(filepath, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        try:
            response = self.openai_client.chat.completions.create(
                model=self.settings.model_vision,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": IMAGE_TEXT_INSTRUCTION},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
                    ],
                }],
                max_tokens=2000,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("Échec de la lecture du texte de l'image")
            return None

    def extract_text_from_document(self, doc_url: str, media_type: str) -> str | None:
        """Extrait le texte d'un PDF avec pypdf. Les autres documents ne sont pas lus."""
        ext = "pdf" if "pdf" in media_type else "doc"
        filepath = self.download_media(doc_url, f"doc_{_timestamp()}.{ext}")
        if not filepath or ext != "pdf":
            return None
        return extract_pdf_text(filepath)


def extract_pdf_text(filepath: str) -> str | None:
    """Renvoie le texte de toutes les pages d'un PDF, ou ``None`` si le fichier est illisible."""
    try:
        reader = pypdf.PdfReader(filepath)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        # Un PDF reçu d'un client peut être malformé de multiples façons : il est alors ignoré.
        logger.exception("Lecture du PDF impossible : %s", filepath)
        return None
    return text.strip()


WhatsAppClient = WhatsAppReceiver
"""Nom générique du client WhatsApp (même classe que :class:`WhatsAppReceiver`)."""
