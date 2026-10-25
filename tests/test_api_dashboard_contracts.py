"""Contrats entre l'API et les pages du tableau de bord : traitement des emails, motif de rejet,
nom du client dans les notifications, état de Twilio.

Aucun appel réseau : le processeur d'emails et le client Twilio sont simulés.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tecpap.channels.whatsapp import WhatsAppReceiver
from tecpap.chatbot.messages import order_lines
from tecpap.processing.order_processor import OrderProcessor
from tecpap.web.routes.api import MAILBOX_UNREACHABLE_MESSAGE, _summary_fields


class FakeProcessor:
    """Remplace OrderProcessor : résultat du passage fixé par le test."""

    def __init__(self, *, orders=(), unreachable=False, remaining=0, failed=0, **kwargs):
        self.orders = list(orders)
        self.mailbox_unreachable = unreachable
        self.remaining_emails = remaining
        self.failed_emails = failed

    def process_new_emails(self, max_emails=10, save_to_db=True):
        return self.orders


def use_processor(monkeypatch, **result):
    monkeypatch.setattr("tecpap.web.routes.api.OrderProcessor", lambda **kwargs: FakeProcessor(**result))


class TestProcessEmailsResult:
    def test_unreachable_mailbox_is_an_error(self, flask_client, monkeypatch):
        use_processor(monkeypatch, unreachable=True)
        response = flask_client.post("/api/process-emails")
        assert response.status_code == 502
        assert response.get_json() == {"success": False, "error": MAILBOX_UNREACHABLE_MESSAGE}

    def test_remaining_and_failed_emails_are_reported(self, flask_client, monkeypatch):
        use_processor(monkeypatch, orders=[{"id": 1}], remaining=7, failed=2)
        data = flask_client.post("/api/process-emails").get_json()
        assert data["success"] is True and data["orders_count"] == 1
        assert (data["remaining_emails"], data["failed_emails"]) == (7, 2)
        assert "7 email(s) restent" in data["message"] and "2 email(s) n'ont pas pu" in data["message"]

    def test_clean_pass_has_a_short_message(self, flask_client, monkeypatch):
        use_processor(monkeypatch)
        data = flask_client.post("/api/process-emails").get_json()
        assert data["message"] == "0 bon(s) de commande détecté(s)"

    def test_processor_flags_an_unreachable_mailbox(self, test_settings):
        gmail = MagicMock()
        gmail.connect.return_value = False
        processor = OrderProcessor(settings=test_settings, gmail=gmail, db=MagicMock(), extractor=MagicMock(),
                                   email_sender=MagicMock())
        assert processor.process_new_emails() == []
        assert processor.mailbox_unreachable is True


class TestRejectionReason:
    @pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}])
    def test_empty_reason_is_refused_and_the_order_stays_pending(self, flask_client, services, sample_order_data, body):
        order_id = services.db.create_order(sample_order_data)
        response = flask_client.post(f"/api/orders/{order_id}/reject", json=body)
        assert response.status_code == 400
        assert "motif" in response.get_json()["error"]
        assert services.db.get_order(order_id)["statut"] == "en_attente"


class TestClientNameInNotifications:
    @pytest.mark.parametrize("name", ["Client WhatsApp 1234", "Client Inconnu", "achat@snack.ma", None])
    def test_provisional_name_is_not_written_to_the_client(self, name):
        fields = _summary_fields({"client_nom": name, "type_produit": "sac_fond_plat", "quantite": 100})
        assert fields["entreprise_cliente"] is None
        text = order_lines(fields, "fr", omit_unknown_client=True)
        assert "Client" not in text.splitlines()[0]
        assert len(text.splitlines()) == 2

    def test_known_name_is_kept(self):
        fields = _summary_fields({"client_nom": "Snack Atlas", "quantite": 100})
        assert order_lines(fields, "fr", omit_unknown_client=True).splitlines()[0].endswith("Snack Atlas")

    def test_summary_without_the_option_keeps_a_client_line(self):
        assert order_lines({"quantite": 5}, "fr").splitlines()[0].startswith("- Client")


class TestWhatsAppStatus:
    def test_not_configured(self, flask_client):
        data = flask_client.get("/api/whatsapp/status").get_json()
        assert data["connected"] is False and data["verified"] is False
        assert data["phone_number"] and not data["phone_number"].startswith("whatsapp:")

    def test_credentials_accepted_by_twilio(self, flask_client, services, monkeypatch):
        monkeypatch.setattr(services.whatsapp, "connect", lambda: True)
        monkeypatch.setattr(services.whatsapp, "verify_credentials", lambda: True)
        assert flask_client.get("/api/whatsapp/status").get_json()["verified"] is True

    def test_configured_but_not_verified(self, flask_client, services, monkeypatch):
        monkeypatch.setattr(services.whatsapp, "connect", lambda: True)
        monkeypatch.setattr(services.whatsapp, "verify_credentials", lambda: False)
        data = flask_client.get("/api/whatsapp/status").get_json()
        assert data["connected"] is True and data["verified"] is False


class TestVerifyCredentials:
    def _receiver(self, test_settings, **overrides):
        settings = test_settings
        receiver = WhatsAppReceiver(settings=settings, llm=MagicMock(), media=MagicMock())
        receiver.account_sid = overrides.get("sid", "ACtest")
        receiver.auth_token = overrides.get("token", "jeton")
        return receiver

    def test_without_credentials_nothing_is_called(self, test_settings):
        receiver = self._receiver(test_settings, sid=None, token=None)
        with patch("tecpap.channels.whatsapp.TwilioClient") as twilio:
            assert receiver.verify_credentials() is False
        twilio.assert_not_called()

    def test_accepted_account(self, test_settings):
        receiver = self._receiver(test_settings)
        with patch("tecpap.channels.whatsapp.TwilioClient") as twilio:
            assert receiver.verify_credentials() is True
        twilio.return_value.api.accounts.assert_called_once_with("ACtest")

    def test_refused_account_is_logged_without_raising(self, test_settings, caplog):
        receiver = self._receiver(test_settings)
        with patch("tecpap.channels.whatsapp.TwilioClient") as twilio:
            twilio.return_value.api.accounts.return_value.fetch.side_effect = RuntimeError("401")
            assert receiver.verify_credentials() is False
        assert "Identifiants Twilio refusés" in caplog.text
