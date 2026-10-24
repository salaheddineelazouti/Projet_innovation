"""Traitement des emails de commande : lecture de la boîte, extraction, enregistrement.

Pour chaque email récent qui n'a pas encore été traité :
1. les pièces jointes sont téléchargées et leur texte extrait ;
2. l'extracteur produit les champs de la commande ;
3. un bon de commande reconnu est enregistré « en attente » de validation et le client
   reçoit un accusé de réception par email.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from tecpap.channels.email_receiver import GmailReceiver
from tecpap.channels.email_sender import EmailSender
from tecpap.config import Settings, load_settings
from tecpap.db.database import DatabaseManager
from tecpap.extraction.extractor import DataExtractor

logger = logging.getLogger(__name__)


class OrderProcessor:
    """Orchestration du traitement des emails de commande.

    Chaque dépendance peut être fournie (tests, réutilisation) ; à défaut elle est construite
    à partir de la configuration. La base ouverte ici est une connexion distincte de celle de
    l'application web, fermée à la fin du traitement.
    """

    def __init__(self, settings: Settings | None = None, gmail: GmailReceiver | None = None,
                 db: DatabaseManager | None = None, extractor: DataExtractor | None = None,
                 email_sender: EmailSender | None = None):
        self.settings = settings or load_settings()
        self.gmail = gmail or GmailReceiver(self.settings)
        self.db = db or DatabaseManager(self.settings.database_path)
        self.extractor = extractor or DataExtractor(db_manager=self.db, settings=self.settings)
        self.email_sender = email_sender or EmailSender(self.settings)
        self.attachments_dir = self.settings.attachments_dir
        self.processed_orders: list[dict[str, Any]] = []

    def process_new_emails(self, max_emails: int = 10, save_to_db: bool = True) -> list[dict[str, Any]]:
        """Traite les ``max_emails`` emails les plus récents et renvoie les bons de commande détectés.

        Args:
            max_emails: nombre d'emails récents examinés.
            save_to_db: enregistre les commandes et envoie l'accusé de réception.
        """
        logger.info("Traitement des bons de commande reçus par email")

        if not self.gmail.connect():
            logger.error("Connexion à la boîte Gmail impossible")
            return []

        # La base sert aussi à l'historique des clients : elle est ouverte avant l'extraction.
        self.db.connect()
        self.db.init_database()
        self.extractor.set_database(self.db)

        try:
            emails = self.gmail.get_recent_emails(count=max_emails)
            if not emails:
                logger.info("Aucun email à traiter")
                return []

            logger.info("%d email(s) à analyser", len(emails))
            orders = []
            for index, email_data in enumerate(emails, 1):
                order = self._process_one(index, len(emails), email_data, save_to_db)
                if order:
                    orders.append(order)

            self.processed_orders = orders
            return orders
        finally:
            self.gmail.disconnect()
            if save_to_db:
                self.db.disconnect()

    def _process_one(self, index: int, total: int, email_data: dict[str, Any],
                     save_to_db: bool) -> dict[str, Any] | None:
        logger.info("Email %d/%d : %s", index, total, (email_data.get("subject") or "")[:50])

        # Vérification avant tout appel au modèle de langue : un email déjà traité ne coûte rien.
        email_id = email_data.get("id")
        if email_id and self.db.is_email_processed(email_id):
            logger.info("Email déjà traité, ignoré")
            return None

        order = self.process_single_email(email_data)
        if not order or not order.get("est_bon_commande"):
            logger.info("Pas un bon de commande")
            return None

        logger.info("Bon de commande détecté : %s", order.get("numero_commande") or "sans numéro")
        if save_to_db:
            order["id"] = self.db.create_order(order)
            if order.get("email_from") and "@" in order.get("email_from", ""):
                self.email_sender.send_order_received_email(order)
        return order

    def process_single_email(self, email_data: dict[str, Any]) -> dict[str, Any] | None:
        """Extrait la commande d'un email (corps et pièces jointes) et ajoute ses métadonnées."""
        attachment_texts: dict[str, str] = {}

        if email_data.get("attachments"):
            logger.info("%d pièce(s) jointe(s)", len(email_data["attachments"]))
            downloaded = self.gmail.download_attachments(email_data["id"], save_dir=self.attachments_dir)
            if downloaded:
                attachment_texts = self.extractor.process_attachments(downloaded)

        order_data = self.extractor.extract_from_email(email_data, attachment_texts)
        if order_data:
            order_data["email_id"] = email_data.get("id")
            order_data["email_subject"] = email_data.get("subject")
            order_data["email_from"] = email_data.get("from")
            order_data["processed_at"] = datetime.now().isoformat()
        return order_data
