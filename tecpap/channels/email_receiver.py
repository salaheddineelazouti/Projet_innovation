"""Réception des emails de commande par IMAP (boîte Gmail).

La connexion utilise un mot de passe d'application Gmail (``GMAIL_APP_PASSWORD``), jamais le
mot de passe du compte. Les pièces jointes et les images intégrées au message sont
téléchargées pour l'extraction de texte (PDF, images, fichiers texte).

Les messages sont lus par UID (identifiant unique d'un message dans un dossier IMAP, valable
tant que la valeur UIDVALIDITY du dossier ne change pas) : :meth:`GmailReceiver.get_unprocessed_emails`
renvoie les messages arrivés après le dernier UID examiné, par lots, sans en laisser de côté quel
que soit le nombre de messages reçus entre deux passages.
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import re
from dataclasses import dataclass, field
from email.header import decode_header
from email.message import Message
from typing import Any

from tecpap.config import Settings, load_settings
from tecpap.db.database import MailboxState
from tecpap.security.redaction import mask_sender

logger = logging.getLogger(__name__)


def _decode_header_value(value: str | None) -> str:
    """Décode un en-tête MIME (« =?utf-8?B?…?= ») en texte."""
    if not value:
        return ""
    decoded, encoding = decode_header(value)[0]
    if isinstance(decoded, bytes):
        return decoded.decode(encoding or "utf-8", errors="replace")
    return decoded


MESSAGE_ID_MAX_CHARS = 200
"""Longueur maximale conservée de l'en-tête ``Message-ID``."""


def _message_id(msg: Message) -> str | None:
    """Identifiant unique de l'email (en-tête ``Message-ID``, espaces retirés), ou ``None``."""
    value = " ".join(str(msg.get("Message-ID") or "").split())
    return value[:MESSAGE_ID_MAX_CHARS] or None


_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY\s+(\d+)", re.IGNORECASE)
_DMARC_RE = re.compile(r"\bdmarc\s*=\s*([a-z]+)", re.IGNORECASE)
_EMAIL_ID_RE = re.compile(r"^\s*(?:(\d+):)?(\d+)\s*$")


@dataclass(frozen=True)
class MailboxBatch:
    """Lot d'emails à examiner lors d'un passage, du plus ancien au plus récent.

    Attributes:
        uidvalidity: valeur UIDVALIDITY du dossier au moment de la lecture.
        emails: emails lus (voir :meth:`GmailReceiver._fetch_email`).
        remaining: nombre d'emails non traités laissés pour le passage suivant.
        first_run: vrai quand aucune position de lecture n'était valable (premier passage, ou
            UIDVALIDITY du dossier modifiée).
        last_uid: plus grand UID du lot (emails illisibles compris), ou ``None`` pour un lot vide.
    """

    uidvalidity: int
    emails: list[dict[str, Any]] = field(default_factory=list)
    remaining: int = 0
    first_run: bool = False
    last_uid: int | None = None


def _first_int(data: Any) -> int | None:
    """Premier entier lu dans une réponse IMAP (liste d'octets), ou ``None``."""
    for item in data or ():
        if isinstance(item, bytes) and item.strip().isdigit():
            return int(item)
    return None


def _message_from_response(msg_data: Any) -> Message | None:
    """Message contenu dans la réponse d'une commande FETCH, ou ``None``."""
    for response_part in msg_data or ():
        if isinstance(response_part, tuple) and len(response_part) > 1 and isinstance(response_part[1], bytes):
            return email.message_from_bytes(response_part[1])
    return None


def _dmarc_result(msg: Message) -> str | None:
    """Résultat DMARC (« pass », « fail », « none »…) du premier en-tête ``Authentication-Results``.

    Le serveur de réception (Gmail) place son propre en-tête en tête du message ; les en-têtes
    plus bas peuvent avoir été écrits par l'expéditeur et ne sont pas lus.
    """
    header = msg.get("Authentication-Results")
    match = _DMARC_RE.search(str(header)) if header else None
    return match.group(1).lower() if match else None


def parse_email_id(email_id: str | bytes | int) -> tuple[int | None, int] | None:
    """Décompose l'identifiant d'un email (« UIDVALIDITY:UID », ou UID seul) ; ``None`` s'il est illisible."""
    text = email_id.decode() if isinstance(email_id, bytes) else str(email_id)
    match = _EMAIL_ID_RE.match(text)
    if not match:
        return None
    return (int(match.group(1)) if match.group(1) else None), int(match.group(2))


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

    Les messages sont désignés par leur UID (identifiant unique et stable d'un message dans un
    dossier IMAP), jamais par leur numéro de séquence, qui change dès qu'un message quitte le
    dossier (archivage, suppression). Un UID n'a de sens qu'avec la valeur UIDVALIDITY du
    dossier : l'identifiant d'un email (``id``) est donc « UIDVALIDITY:UID ».

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
            logger.info("Connexion IMAP établie pour %s", mask_sender(self.email_address))
            return True
        except imaplib.IMAP4.error:
            logger.exception("Connexion IMAP refusée pour %s", mask_sender(self.email_address))
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

    def mailbox_key(self, folder: str = "INBOX") -> str:
        """Nom sous lequel la position de lecture du dossier est enregistrée : compte et dossier."""
        return f"{(self.email_address or '').strip().lower()}/{folder}"

    # ------------------------------------------------------------------ lecture par UID

    def _select(self, folder: str) -> int | None:
        """Sélectionne le dossier ; renvoie sa valeur UIDVALIDITY (0 si le serveur ne la donne pas), ou ``None``."""
        status, _data = self.connection.select(folder)
        if status != "OK":
            logger.error("Sélection du dossier IMAP %s impossible (statut %s)", folder, status)
            return None
        value = _first_int(self.connection.response("UIDVALIDITY")[1])
        if value is None:
            status, data = self.connection.status(folder, "(UIDVALIDITY)")
            match = _UIDVALIDITY_RE.search(b" ".join(item for item in data or () if isinstance(item, bytes))) \
                if status == "OK" else None
            value = int(match.group(1)) if match else None
        if value is None:
            logger.warning("UIDVALIDITY du dossier %s inconnue : valeur 0 retenue", folder)
        return value or 0

    def _search_uids(self, criteria: str = "ALL") -> list[int] | None:
        """UID des messages du dossier sélectionné qui répondent à ``criteria``, par ordre croissant."""
        status, data = self.connection.uid("SEARCH", None, criteria)
        if status != "OK":
            logger.error("Recherche IMAP en échec (statut %s)", status)
            return None
        raw = data[0] if data else b""
        return sorted({int(item) for item in (raw or b"").split() if item.isdigit()})

    def _fetch_uids(self, uids: list[int], uidvalidity: int) -> list[dict[str, Any]]:
        return [data for data in (self._fetch_email(uid, uidvalidity) for uid in uids) if data]

    def get_recent_emails(self, folder: str = "INBOX", count: int = 5) -> list[dict[str, Any]]:
        """Renvoie les ``count`` derniers emails du dossier (plus grands UID), du plus récent au plus ancien."""
        if not self.connection:
            logger.error("Connexion IMAP absente : appelez connect() d'abord")
            return []
        try:
            uidvalidity = self._select(folder)
            uids = self._search_uids() if uidvalidity is not None else None
            if not uids:
                if uids is not None:
                    logger.info("Aucun email dans le dossier %s", folder)
                return []
            return self._fetch_uids(uids[-count:][::-1], uidvalidity)
        except (imaplib.IMAP4.error, OSError):
            logger.exception("Lecture des emails récents impossible")
            return []

    def get_unprocessed_emails(self, state: MailboxState | None, *, folder: str = "INBOX",
                               limit: int = 10) -> MailboxBatch | None:
        """Emails arrivés après la position de lecture ``state``, du plus ancien au plus récent.

        Args:
            state: position enregistrée (dernier UID examiné et UIDVALIDITY), ou ``None``.
            folder: dossier IMAP lu.
            limit: nombre maximal d'emails renvoyés ; les suivants restent pour le passage suivant
                (:attr:`MailboxBatch.remaining`).

        Sans position valable (premier passage, ou UIDVALIDITY changée : les UID enregistrés ne
        désignent plus les mêmes messages), seuls les ``limit`` emails les plus récents sont renvoyés : les messages
        plus anciens de la boîte ne sont pas traités comme des commandes nouvelles.

        Returns:
            Le lot à examiner, ou ``None`` si la boîte n'a pas pu être lue.
        """
        if not self.connection:
            logger.error("Connexion IMAP absente : appelez connect() d'abord")
            return None
        limit = max(1, int(limit))
        try:
            uidvalidity = self._select(folder)
            uids = self._search_uids() if uidvalidity is not None else None
            if uids is None:
                return None
            first_run = state is None or state.uidvalidity != uidvalidity
            if first_run:
                if state is not None:
                    logger.warning("UIDVALIDITY du dossier %s modifiée : lecture reprise sur les %d derniers emails",
                                   folder, limit)
                pending = uids[-limit:]
            else:
                pending = [uid for uid in uids if uid > state.last_uid]
            batch = pending[:limit]
            remaining = len(pending) - len(batch)
            if remaining:
                logger.info("%d email(s) restent à examiner au prochain passage", remaining)
            return MailboxBatch(uidvalidity, self._fetch_uids(batch, uidvalidity), remaining, first_run,
                                last_uid=batch[-1] if batch else None)
        except (imaplib.IMAP4.error, OSError):
            logger.exception("Lecture des emails non traités impossible")
            return None

    def _fetch_email(self, uid: int, uidvalidity: int) -> dict[str, Any] | None:
        """Télécharge un email par son UID et renvoie son sujet, expéditeur, date, corps et pièces jointes.

        ``id`` vaut « UIDVALIDITY:UID » (identifiant stable dans la boîte) et ``uid`` l'UID seul ;
        ``message_id`` est l'identifiant unique attribué par la messagerie d'envoi (en-tête
        ``Message-ID``), ou ``None`` s'il est absent ; ``dmarc`` est le résultat du contrôle DMARC
        (authentification du domaine de l'expéditeur) noté par le serveur de réception, ou ``None``.
        """
        try:
            status, msg_data = self.connection.uid("FETCH", str(uid), "(RFC822)")
            if status != "OK":
                return None
            msg = _message_from_response(msg_data)
            if msg is None:
                return None
            return {
                "id": f"{uidvalidity}:{uid}",
                "uid": int(uid),
                "message_id": _message_id(msg),
                "subject": _decode_header_value(msg["Subject"]),
                "from": _decode_header_value(msg.get("From", "")),
                "date": msg.get("Date", ""),
                "dmarc": _dmarc_result(msg),
                "body": self._get_email_body(msg),
                "attachments": self._get_attachments_info(msg),
            }
        except (imaplib.IMAP4.error, OSError):
            # Connexion interrompue : le lot entier sera relu au passage suivant.
            raise
        except Exception:
            # Un email mal formé ne doit pas interrompre la lecture des suivants.
            logger.exception("Lecture de l'email d'UID %s impossible", uid)
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

    def download_attachments(self, email_id: str | bytes, save_dir: str | None = None,
                             folder: str = "INBOX") -> list[str]:
        """Enregistre les pièces jointes (et images intégrées) d'un email dans ``save_dir``.

        Args:
            email_id: identifiant de l'email (« UIDVALIDITY:UID », ou UID seul).
            save_dir: dossier de destination ; par défaut ``Settings.attachments_dir``.
            folder: dossier IMAP de l'email.

        Returns:
            Les chemins des fichiers enregistrés ; aucun quand la valeur UIDVALIDITY du dossier a changé
            depuis la lecture de l'email : l'UID désignerait alors un autre message.
        """
        if not self.connection:
            logger.error("Connexion IMAP absente : appelez connect() d'abord")
            return []
        parsed = parse_email_id(email_id)
        if parsed is None:
            logger.error("Identifiant d'email illisible : %r", str(email_id)[:40])
            return []
        expected_validity, uid = parsed

        save_dir = save_dir or self.settings.attachments_dir
        os.makedirs(save_dir, exist_ok=True)
        downloaded = []
        image_counter = 0

        try:
            uidvalidity = self._select(folder)
            if uidvalidity is None:
                return []
            if expected_validity is not None and uidvalidity != expected_validity:
                logger.warning("UIDVALIDITY du dossier %s modifiée depuis la lecture de l'email : "
                               "pièces jointes non téléchargées", folder)
                return []
            status, msg_data = self.connection.uid("FETCH", str(uid), "(RFC822)")
            if status != "OK":
                return []
            msg = _message_from_response(msg_data)
            if msg is None or not msg.is_multipart():
                return []
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

    def check_for_new_emails(self, since_date: str | None = None, folder: str = "INBOX") -> list[dict[str, Any]]:
        """Renvoie les emails non lus du dossier, du plus ancien au plus récent."""
        if not self.connection:
            logger.error("Connexion IMAP absente : appelez connect() d'abord")
            return []
        try:
            uidvalidity = self._select(folder)
            uids = self._search_uids("UNSEEN") if uidvalidity is not None else None
            if not uids:
                if uids is not None:
                    logger.info("Aucun email non lu")
                return []
            logger.info("%d email(s) non lu(s)", len(uids))
            return self._fetch_uids(uids, uidvalidity)
        except (imaplib.IMAP4.error, OSError):
            logger.exception("Lecture des emails non lus impossible")
            return []
