"""Tests du traitement des emails de commande (tecpap.processing.order_processor).

La boîte Gmail et l'envoi d'emails sont simulés, le modèle de langue est remplacé par
``FakeLLMClient`` ; la base est une base SQLite temporaire.
"""

from unittest.mock import Mock

import pytest

from tecpap.db.database import DatabaseManager
from tecpap.extraction.extractor import DataExtractor
from tecpap.extraction.prompts import CONTENT_TAG
from tecpap.processing.order_processor import OrderProcessor
from tests.fakes import FakeLLMClient

NOT_A_REORDER = {"is_reorder": False, "client_name": None, "confidence": 90}
ORDER = {"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 4000,
         "unite": "pièces", "est_bon_commande": True, "confiance": 80}


def _email(email_id="mail-1", attachments=None):
    return {"id": email_id, "subject": "Bon de commande", "from": "achats@snack-atlas.ma",
            "date": "2025-01-10", "body": "Bonjour, voici notre commande.", "attachments": attachments or []}


def _gmail(emails, downloaded=None):
    gmail = Mock()
    gmail.connect.return_value = True
    gmail.get_recent_emails.return_value = emails
    gmail.download_attachments.return_value = downloaded or []
    return gmail


def _pdf(path, text):
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path))
    pdf.drawString(100, 750, text)
    pdf.save()
    return str(path)


@pytest.fixture
def make_processor(test_settings):
    """Construit un ``OrderProcessor`` sur une base temporaire avec un modèle simulé."""
    def build(gmail, llm):
        db = DatabaseManager(test_settings.database_path)
        extractor = DataExtractor(db_manager=db, settings=test_settings, llm=llm)
        sender = Mock()
        processor = OrderProcessor(settings=test_settings, gmail=gmail, db=db, extractor=extractor,
                                   email_sender=sender)
        return processor, sender
    return build


def _saved_orders(settings):
    db = DatabaseManager(settings.database_path)
    db.connect()
    try:
        return db.get_all_orders()
    finally:
        db.disconnect()


class TestOrderProcessor:
    """Lecture de la boîte, extraction et enregistrement."""

    def test_order_with_pdf_attachment_is_saved(self, make_processor, test_settings, tmp_path):
        pdf_path = _pdf(tmp_path / "bon.pdf", "Bon de commande 4000 sachets fond plat")
        gmail = _gmail([_email(attachments=["bon.pdf"])], downloaded=[pdf_path])
        llm = FakeLLMClient(chat_json=[dict(ORDER), NOT_A_REORDER])
        processor, sender = make_processor(gmail, llm)

        orders = processor.process_new_emails(max_emails=5)

        assert len(orders) == 1
        assert orders[0]["email_id"] == "mail-1"
        prompt = llm.calls_to("chat_json")[0]["user"]
        block = prompt.split(f"<{CONTENT_TAG}>", 1)[1].split(f"</{CONTENT_TAG}>", 1)[0]
        assert "4000 sachets" in block
        saved = _saved_orders(test_settings)
        assert len(saved) == 1
        assert saved[0]["statut"] == "en_attente"
        sender.send_order_received_email.assert_called_once()
        gmail.disconnect.assert_called_once()

    def test_already_processed_email_costs_no_model_call(self, make_processor, test_settings):
        db = DatabaseManager(test_settings.database_path)
        db.connect()
        db.init_database()
        db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 10, "email_id": "mail-1"})
        db.disconnect()
        llm = FakeLLMClient(chat_json=[dict(ORDER), NOT_A_REORDER])
        processor, _sender = make_processor(_gmail([_email()]), llm)

        assert processor.process_new_emails() == []
        assert llm.calls == []

    def test_message_that_is_not_an_order(self, make_processor, test_settings):
        llm = FakeLLMClient(chat_json=[{"est_bon_commande": False}, NOT_A_REORDER])
        processor, sender = make_processor(_gmail([_email()]), llm)

        assert processor.process_new_emails() == []
        assert _saved_orders(test_settings) == []
        sender.send_order_received_email.assert_not_called()

    def test_without_api_key_an_email_without_order_is_not_saved(self, make_processor, test_settings):
        llm = FakeLLMClient(available=False)
        processor, _sender = make_processor(_gmail([_email()]), llm)

        assert processor.process_new_emails() == []
        assert _saved_orders(test_settings) == []

    def test_mailbox_unreachable(self, make_processor):
        gmail = Mock()
        gmail.connect.return_value = False
        processor, _sender = make_processor(gmail, FakeLLMClient())
        assert processor.process_new_emails() == []
        gmail.get_recent_emails.assert_not_called()
