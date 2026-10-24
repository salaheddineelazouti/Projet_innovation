"""Réception des emails de commande par IMAP (boîte Gmail).

La connexion utilise un mot de passe d'application Gmail (``GMAIL_APP_PASSWORD``), jamais le
mot de passe du compte. Les pièces jointes et les images intégrées au message sont
téléchargées pour l'extraction de texte (PDF, images, fichiers texte).
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
from email.header import decode_header
from email.message import Message
from typing import Any

from tecpap.config import Settings, load_settings

logger = logging.getLogger(__name__)


def _decode_header_value(value: str | None) -> str:
    """Décode un en-tête MIME (« =?utf-8?B?…?= ») en texte."""
    if not value:
        return ""
    decoded, encoding = decode_header(value)[0]
    if isinstance(decoded, bytes):
        return decoded.decode(encoding or "utf-8", errors="replace")
    return decoded


def _safe_filename(filename: str) -> str:
    """Garde uniquement le nom de fichier, sans dossier : un nom de pièce jointe est une donnée externe."""
    name = os.path.basename(filename.replace("\\", "/")).strip()
    return name if name not in ("", ".", "..") else "piece_jointe"


def _is_downloadable_part(part: Message) -> tuple[bool, bool]:
    """Indique si une partie MIME est une pièce jointe et/ou une image intégrée."""
    content_type = part.get_content_type()
    content_disposition = str(part.get("Content-Disposition"))
    is_attachment = "attachment" in content_disposition
    is_inline_image = content_type.startswith("image/") and content_type != "image/signature"
    return is_attachment, is_inline_image


def _part_filename(part: Message) -> str | None:
    filename = part.get_filename()
    if not filename:
        return None
    if isinstance(filename, bytes):
        return filename.decode("utf-8", errors="replace")
    try:
        return _decode_header_value(filename)
    except (ValueError, LookupError):
        return filename


class GmailReceiver:
    """Client IMAP pour lire la boîte de réception des commandes.

    Args:
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
    """

    imap_server = "imap.gmail.com"
    imap_port = 993

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.email_address = self.settings.gmail_email
        self.app_password = self.settings.gmail_app_password
        self.connection: imaplib.IMAP4_SSL | None = None

    def connect(self) -> bool:
        """Ouvre la connexion IMAP. Renvoie ``False`` si les identifiants manquent ou sont refusés."""
        if not self.email_address or not self.app_password:
            logger.warning("Identifiants Gmail absents (GMAIL_EMAIL et GMAIL_APP_PASSWORD)")
            return False
        try:
            self.connection = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            self.connection.login(self.email_address, self.app_password)
            logger.info("Connexion IMAP établie pour %s", self.email_address)
            return True
        except imaplib.IMAP4.error:
            logger.exception("Connexion IMAP refusée pour %s", self.email_address)
            return False
        except OSError:
            logger.exception("Serveur IMAP injoignable")
            return False

    def disconnect(self) -> None:
        """Ferme la connexion IMAP."""
        if not self.connection:
            return
        try:
            self.connection.logout()
            logger.info("Déconnexion IMAP")
        except (imaplib.IMAP4.error, OSError):
            logger.debug("Erreur à la déconnexion IMAP", exc_info=True)

    def get_recent_emails(self, folder: str = "INBOX", count: int = 5) -> list[dict[str, Any]]:
        """Renvoie les ``count`` derniers emails du dossier, du plus récent au plus ancien."""
        if not self.connection:
            logger.error("Connexion IMAP absente : appelez connect() d'abord")
            return []
        try:
            self.connection.select(folder)
            status, messages = self.connection.search(None, "ALL")
            if status != "OK":
                logger.error("Recherche IMAP en échec (statut %s)", status)
                return []

            email_ids = messages[0].split()
            if not email_ids:
                logger.info("Aucun email dans le dossier %s", folder)
                return []

            recent_ids = email_ids[-count:][::-1]
            return [data for data in (self._fetch_email(email_id) for email_id in recent_ids) if data]
        except (imaplib.IMAP4.error, OSError):
            logger.exception("Lecture des emails récents impossible")
            return []

    def _fetch_email(self, email_id: bytes) -> dict[str, Any] | None:
        """Télécharge un email et renvoie son sujet, expéditeur, date, corps et pièces jointes."""
        try:
            status, msg_data = self.connection.fetch(email_id, "(RFC822)")
            if status != "OK":
                return None

            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    return {
                        "id": email_id.decode(),
                        "subject": _decode_header_value(msg["Subject"]),
                        "from": _decode_header_value(msg.get("From", "")),
                        "date": msg.get("Date", ""),
                        "body": self._get_email_body(msg),
                        "attachments": self._get_attachments_info(msg),
                    }
            return None
        except Exception:
            # Un email mal formé ne doit pas interrompre la lecture des suivants.
            logger.exception("Lecture de l'email %s impossible", email_id)
            return None

    def _get_email_body(self, msg: Message) -> str:
        """Renvoie le texte brut (text/plain) du message."""
        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition"))
                if part.get_content_type() == "text/plain" and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            return payload.decode()
                        except UnicodeDecodeError:
                            continue
            return ""
        payload = msg.get_payload(decode=True)
        if not payload:
            return ""
        try:
            return payload.decode()
        except UnicodeDecodeError:
            return ""

    def _get_attachments_info(self, msg: Message) -> list[str]:
        """Renvoie les noms des pièces jointes, images intégrées comprises."""
        attachments = []
        if not msg.is_multipart():
            return attachments
        for part in msg.walk():
            is_attachment, is_inline_image = _is_downloadable_part(part)
            if not (is_attachment or is_inline_image):
                continue
            filename = _part_filename(part)
            if not filename and is_inline_image:
                filename = f"inline_image.{part.get_content_type().split('/')[-1]}"
            if filename:
                attachments.append(filename)
        return attachments

    def download_attachments(self, email_id: str | bytes, save_dir: str | None = None) -> list[str]:
        """Enregistre les pièces jointes (et images intégrées) d'un email dans ``save_dir``.

        Args:
            email_id: identifiant IMAP de l'email.
            save_dir: dossier de destination ; par défaut ``Settings.attachments_dir``.

        Returns:
            Les chemins des fichiers enregistrés.
        """
        if not self.connection:
            logger.error("Connexion IMAP absente : appelez connect() d'abord")
            return []

        save_dir = save_dir or self.settings.attachments_dir
        os.makedirs(save_dir, exist_ok=True)
        downloaded = []
        image_counter = 0

        try:
            self.connection.select("INBOX")
            imap_id = email_id.encode() if isinstance(email_id, str) else email_id
            status, msg_data = self.connection.fetch(imap_id, "(RFC822)")
            if status != "OK":
                return []

            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue
                msg = email.message_from_bytes(response_part[1])
                if not msg.is_multipart():
                    continue
                for part in msg.walk():
                    is_attachment, is_inline_image = _is_downloadable_part(part)
                    if not (is_attachment or is_inline_image):
                        continue
                    filename = _part_filename(part)
                    if not filename and is_inline_image:
                        image_counter += 1
                        filename = f"inline_image_{image_counter}.{part.get_content_type().split('/')[-1]}"
                    if not filename:
                        continue
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    filepath = os.path.join(save_dir, _safe_filename(filename))
                    with open(filepath, "wb") as f:
                        f.write(payload)
                    downloaded.append(filepath)
                    logger.info("Pièce jointe enregistrée : %s", filepath)
            return downloaded
        except (imaplib.IMAP4.error, OSError):
            logger.exception("Téléchargement des pièces jointes impossible")
            return []

    def check_for_new_emails(self, since_date: str | None = None) -> list[dict[str, Any]]:
        """Renvoie les emails non lus de la boîte de réception."""
        if not self.connection:
            logger.error("Connexion IMAP absente : appelez connect() d'abord")
            return []
        try:
            self.connection.select("INBOX")
            status, messages = self.connection.search(None, "UNSEEN")
            if status != "OK":
                return []

            email_ids = messages[0].split()
            if not email_ids:
                logger.info("Aucun email non lu")
                return []

            logger.info("%d email(s) non lu(s)", len(email_ids))
            return [data for data in (self._fetch_email(email_id) for email_id in email_ids) if data]
        except (imaplib.IMAP4.error, OSError):
            logger.exception("Lecture des emails non lus impossible")
            return []
