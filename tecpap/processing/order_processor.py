"""Traitement des emails de commande : lecture de la boîte, extraction, enregistrement.

Un passage (:meth:`OrderProcessor.process_new_emails`) examine les emails arrivés depuis le
passage précédent, du plus ancien au plus récent :

1. la boîte est lue par UID (identifiant stable d'un message dans un dossier IMAP) à partir de
   la position enregistrée en base (dernier UID examiné, voir
   :meth:`tecpap.db.database.DatabaseManager.get_mailbox_state`) : aucun email n'est laissé de
   côté, quel que soit le nombre de messages reçus entre deux passages. Au plus ``max_emails``
   emails sont examinés par passage ; les suivants le sont au passage suivant
   (:attr:`OrderProcessor.remaining_emails`) ;
2. un email déjà enregistré (même en-tête ``Message-ID``, ou, sans cet en-tête, même empreinte
   SHA-256 de l'expéditeur, de la date, du sujet et du corps) est ignoré avant tout appel au
   modèle de langue ;
3. les pièces jointes sont téléchargées dans un sous-dossier propre à l'email et leur texte
   extrait ;
4. l'extracteur (:class:`tecpap.extraction.extractor.DataExtractor`) analyse le sujet, le corps
   et le texte des pièces jointes : contrôle des injections d'instructions, langue, modèle,
   exemples de commandes validées, règles de normalisation. Un email suspect est signalé par
   une anomalie et un événement de sécurité, jamais écarté ; sans modèle de langue, l'extraction
   par règles prend le relais ;
5. un bon de commande reconnu est enregistré « en attente » de validation, avec la traçabilité
   de l'extraction (``message_sid`` : identifiant de l'email, ``message_brut``, langue, modèle,
   anomalies, champs manquants) ;
6. l'accusé de réception envoyé au client ne reprend que des valeurs contrôlées par TECPAP
   (référence « CMD-<id> », type de produit du catalogue, quantité et unité reconnue), jamais le
   texte libre du message. Il n'est pas envoyé pour une commande déjà enregistrée, pour un
   contenu suspect (:data:`~tecpap.extraction.extractor.INJECTION_ANOMALY`) ni pour un email dont
   le contrôle DMARC (authentification du domaine de l'expéditeur) a échoué : l'adresse
   d'expédition peut alors être usurpée.

Un seul passage à la fois dans le processus : un appel pendant un passage en cours lève
:class:`ProcessingAlreadyRunning`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from tecpap.catalog import PRODUCT_TYPES
from tecpap.channels.email_receiver import GmailReceiver, MailboxBatch
from tecpap.channels.email_sender import EmailSender
from tecpap.config import Settings, load_settings
from tecpap.db.database import DatabaseManager
from tecpap.extraction.extractor import INJECTION_ANOMALY, DataExtractor
from tecpap.extraction.rules import CANONICAL_UNITS
from tecpap.llm.client import LLMClient

logger = logging.getLogger(__name__)

__all__ = [
    "MAILBOX_FOLDER",
    "OrderProcessor",
    "ProcessingAlreadyRunning",
    "email_fingerprint",
]

MAILBOX_FOLDER = "INBOX"
"""Dossier IMAP lu."""

_RUN_LOCK = threading.Lock()


class ProcessingAlreadyRunning(RuntimeError):
    """Un traitement des emails est déjà en cours dans ce processus."""

    def __init__(self, message: str = "Un traitement des emails est déjà en cours"):
        super().__init__(message)


def email_fingerprint(email_data: dict[str, Any]) -> str:
    """Identifiant d'un email sans en-tête ``Message-ID``.

    C'est l'empreinte SHA-256 de l'expéditeur, de la date, du sujet et du corps : le même email
    relu donne la même empreinte.
    """
    parts = (email_data.get(name) or "" for name in ("from", "date", "subject", "body"))
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:40]}"


def _message_key(email_data: dict[str, Any]) -> str:
    return email_data.get("message_id") or email_fingerprint(email_data)


class OrderProcessor:
    """Orchestration du traitement des emails de commande.

    Chaque dépendance peut être fournie (tests, réutilisation) ; à défaut elle est construite
    à partir de la configuration. La base ouverte ici est une connexion distincte de celle de
    l'application web, fermée à la fin du traitement.

    Args:
        llm: client des modèles de langue de l'extracteur construit par défaut (celui de
            l'application web, par exemple) ; ignoré quand ``extractor`` est fourni.

    Attributes:
        processed_orders: bons de commande détectés lors du dernier passage.
        remaining_emails: nombre d'emails laissés pour le passage suivant (limite ``max_emails``).
        failed_emails: nombre d'emails du dernier passage dont le traitement a échoué (erreur
            inattendue) : ils sont journalisés puis ignorés, pour qu'un message qui provoque une
            erreur ne bloque pas les suivants. Une erreur de la base (``sqlite3.Error``) arrête le
            passage sans avancer la position de lecture : l'email est relu au passage suivant.
        mailbox_unreachable: vrai quand la connexion à la boîte ou sa lecture a échoué lors du
            dernier passage (aucun email n'a alors été examiné).
    """

    def __init__(self, settings: Settings | None = None, gmail: GmailReceiver | None = None,
                 db: DatabaseManager | None = None, extractor: DataExtractor | None = None,
                 email_sender: EmailSender | None = None, llm: LLMClient | None = None):
        self.settings = settings or load_settings()
        self.gmail = gmail or GmailReceiver(self.settings)
        self.db = db or DatabaseManager(self.settings.database_path)
        self.extractor = extractor or DataExtractor(db_manager=self.db, settings=self.settings, llm=llm)
        self.email_sender = email_sender or EmailSender(self.settings)
        self.attachments_dir = self.settings.attachments_dir
        self.processed_orders: list[dict[str, Any]] = []
        self.remaining_emails = 0
        self.failed_emails = 0
        self.mailbox_unreachable = False

    def process_new_emails(self, max_emails: int = 10, save_to_db: bool = True) -> list[dict[str, Any]]:
        """Examine les emails arrivés depuis le passage précédent et renvoie les bons de commande détectés.

        Args:
            max_emails: nombre maximal d'emails examinés lors de ce passage ; les suivants le
                sont au passage suivant (:attr:`remaining_emails`). Au premier passage, ce sont
                les ``max_emails`` emails les plus récents de la boîte.
            save_to_db: enregistre les commandes, envoie l'accusé de réception et fait avancer la
                position de lecture ; sans enregistrement, la position ne change pas.

        Raises:
            ProcessingAlreadyRunning: un autre passage est en cours dans ce processus.
        """
        if not _RUN_LOCK.acquire(blocking=False):
            logger.warning("Traitement des emails déjà en cours : nouvelle demande refusée")
            raise ProcessingAlreadyRunning()
        try:
            return self._run(max_emails, save_to_db)
        finally:
            _RUN_LOCK.release()

    def _run(self, max_emails: int, save_to_db: bool) -> list[dict[str, Any]]:
        logger.info("Traitement des bons de commande reçus par email")
        self.remaining_emails = 0
        self.failed_emails = 0
        self.mailbox_unreachable = False

        if not self.gmail.connect():
            logger.error("Connexion à la boîte Gmail impossible")
            self.mailbox_unreachable = True
            return []

        # La base sert aussi à l'historique des clients : elle est ouverte avant l'extraction.
        self.db.connect()
        self.db.init_database()
        self.extractor.set_database(self.db)
        mailbox = self.gmail.mailbox_key(MAILBOX_FOLDER)

        try:
            batch: MailboxBatch | None = self.gmail.get_unprocessed_emails(
                self.db.get_mailbox_state(mailbox), folder=MAILBOX_FOLDER, limit=max_emails)
            if batch is None:
                logger.error("Lecture de la boîte Gmail impossible")
                self.mailbox_unreachable = True
                return []
            self.remaining_emails = batch.remaining
            if not batch.emails:
                logger.info("Aucun email à traiter")
            else:
                logger.info("%d email(s) à analyser", len(batch.emails))

            orders = []
            for index, email_data in enumerate(batch.emails, 1):
                order = self._process_one_safely(index, len(batch.emails), email_data, save_to_db)
                if order:
                    orders.append(order)
                if save_to_db and email_data.get("uid") is not None:
                    self.db.set_mailbox_state(mailbox, batch.uidvalidity, int(email_data["uid"]))
            if save_to_db and batch.last_uid is not None:
                # Les emails illisibles du lot ne sont pas relus à chaque passage.
                self.db.set_mailbox_state(mailbox, batch.uidvalidity, batch.last_uid)

            self.processed_orders = orders
            return orders
        finally:
            self.gmail.disconnect()
            if save_to_db:
                self.db.disconnect()

    def _process_one_safely(self, index: int, total: int, email_data: dict[str, Any],
                            save_to_db: bool) -> dict[str, Any] | None:
        """Traite un email ; une erreur inattendue est journalisée et ne bloque pas les emails suivants."""
        try:
            return self._process_one(index, total, email_data, save_to_db)
        except sqlite3.Error:
            raise
        except Exception:
            self.failed_emails += 1
            logger.exception("Email %d/%d (UID %s) : traitement impossible, email ignoré",
                             index, total, email_data.get("uid"))
            return None

    def _process_one(self, index: int, total: int, email_data: dict[str, Any],
                     save_to_db: bool) -> dict[str, Any] | None:
        logger.info("Email %d/%d : %s", index, total, (email_data.get("subject") or "")[:50])

        # Vérification avant tout appel au modèle de langue : un email déjà traité ne coûte rien.
        if self.db.is_message_processed(_message_key(email_data)):
            logger.info("Email déjà traité, ignoré")
            return None

        order = self.process_single_email(email_data)
        if not order or not order.get("est_bon_commande"):
            logger.info("Pas un bon de commande")
            return None

        logger.info("Bon de commande détecté : %s", order.get("numero_commande") or "sans numéro")
        if save_to_db:
            order["id"], created = self.db.save_order(order)
            if not created:
                logger.info("Commande %s déjà enregistrée : aucun accusé de réception", order["id"])
            elif self._should_acknowledge(order, email_data):
                self.email_sender.send_order_received_email(self.acknowledgement(order))
        return order

    @staticmethod
    def _should_acknowledge(order: dict[str, Any], email_data: dict[str, Any]) -> bool:
        """Vrai quand l'accusé de réception automatique peut être envoyé à l'expéditeur."""
        if "@" not in str(order.get("email_from") or ""):
            return False
        if INJECTION_ANOMALY in (order.get("anomalies") or []):
            logger.warning("Contenu suspect : aucun accusé de réception automatique (commande %s)", order.get("id"))
            return False
        if email_data.get("dmarc") == "fail":
            logger.warning("Contrôle DMARC en échec : aucun accusé de réception automatique (commande %s)",
                           order.get("id"))
            return False
        return True

    @staticmethod
    def acknowledgement(order: dict[str, Any]) -> dict[str, Any]:
        """Données de l'accusé de réception : seulement des valeurs contrôlées par TECPAP.

        La référence est « CMD-<id> » (le numéro écrit par l'expéditeur n'est pas repris), le
        produit est un type du catalogue, l'unité une unité reconnue par les règles
        (:data:`~tecpap.extraction.rules.CANONICAL_UNITS`) ; la
        désignation, le nom du client et tout autre texte libre du message sont omis.
        """
        product = order.get("type_produit") if order.get("type_produit") in PRODUCT_TYPES else None
        unit = order.get("unite") if order.get("unite") in CANONICAL_UNITS else None
        return {
            "id": order.get("id"),
            "email_from": order.get("email_from"),
            "numero_commande": None,
            "type_produit": product,
            "produit_type": product,
            "nature_produit": None,
            "quantite": order.get("quantite"),
            "unite": unit,
            "date_livraison": order.get("date_livraison"),
        }

    def _attachments_dir_for(self, email_data: dict[str, Any]) -> str:
        """Sous-dossier des pièces jointes d'un email : deux emails n'écrasent jamais leurs fichiers."""
        digest = hashlib.sha256(_message_key(email_data).encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.attachments_dir, digest)

    def process_single_email(self, email_data: dict[str, Any]) -> dict[str, Any] | None:
        """Extrait la commande d'un email (corps et pièces jointes) et ajoute ses métadonnées.

        Le texte analysé (sujet, corps et texte des pièces jointes) est conservé dans
        ``message_brut`` ; l'identifiant ``Message-ID`` de l'email (à défaut son empreinte, voir
        :func:`email_fingerprint`) devient ``message_sid``.
        """
        attachment_texts: dict[str, str] = {}

        if email_data.get("attachments"):
            logger.info("%d pièce(s) jointe(s)", len(email_data["attachments"]))
            downloaded = self.gmail.download_attachments(email_data["id"],
                                                         save_dir=self._attachments_dir_for(email_data))
            if downloaded:
                attachment_texts = self.extractor.process_attachments(downloaded)

        order_data = self.extractor.extract_from_email(email_data, attachment_texts)
        if order_data:
            order_data["email_id"] = email_data.get("id")
            order_data["email_subject"] = email_data.get("subject")
            order_data["email_from"] = email_data.get("from")
            order_data["message_sid"] = _message_key(email_data)
            order_data["source"] = "email"
            order_data["processed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return order_data
