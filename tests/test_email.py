"""
Tests du module tecpap.channels.email_sender (serveur SMTP simulé, aucun envoi réel).
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from tecpap.channels import email_sender as email_sender_module
from tecpap.channels.email_sender import EmailSender
from tecpap.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Les expéditeurs créés sans configuration explicite utilisent la configuration de test."""
    settings = Settings.for_tests(tmp_path)
    monkeypatch.setattr(email_sender_module, "load_settings", lambda: settings)
    return settings


class TestEmailSenderInit:
    """Tests d'initialisation de EmailSender."""

    def test_email_sender_creates_instance(self):
        """Test création d'instance EmailSender."""
        sender = EmailSender()
        assert sender is not None

    def test_smtp_server_configured(self):
        """Test configuration serveur SMTP."""
        sender = EmailSender()
        assert sender.smtp_server == "smtp.gmail.com"
        assert sender.smtp_port == 587

    def test_company_name_defined(self):
        """Test que le nom de l'entreprise est défini."""
        sender = EmailSender()
        assert sender.company_name == "TECPAP"


class TestEmailSending:
    """Tests d'envoi d'emails (mockés)."""

    @pytest.fixture
    def sender(self):
        """Instance EmailSender pour tests."""
        return EmailSender()

    def test_send_email_invalid_email_returns_false(self, sender):
        """Test que l'envoi échoue pour email invalide."""
        result = sender.send_email("invalid_email", "Test", "<html></html>")
        assert result is False

    def test_send_email_empty_email_returns_false(self, sender):
        """Test que l'envoi échoue pour email vide."""
        result = sender.send_email("", "Test", "<html></html>")
        assert result is False

    def test_send_email_none_email_returns_false(self, sender):
        """Test que l'envoi échoue pour email None."""
        result = sender.send_email(None, "Test", "<html></html>")
        assert result is False

    @patch('smtplib.SMTP')
    def test_send_email_with_mocked_smtp(self, mock_smtp, sender):
        """Test envoi email avec SMTP mocké."""
        sender.email = "test@example.com"
        sender.password = "test_password"

        # Configuration du serveur SMTP simulé
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = Mock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = Mock(return_value=False)

        result = sender.send_email(
            "client@test.com",
            "Test Subject",
            "<html><body>Test</body></html>"
        )

        # L'envoi réussit avec le serveur simulé
        assert result is True


class TestValidationEmail:
    """Tests d'emails de validation."""

    @pytest.fixture
    def sender(self):
        return EmailSender()

    @pytest.fixture
    def sample_order(self):
        """Commande exemple pour tests."""
        return {
            'id': 123,
            'client_nom': 'Ahmed Benali',
            'client_telephone': '+212612345678',
            'client_ville': 'Casablanca',
            'produit_type': 'Sachets fond plat',
            'quantite': 1000,
            'unite': 'kg',
            'prix_total': 5000.00,
            'statut': 'validé',
            'date_commande': '2025-01-15 10:30:00',
            'email_from': 'ahmed@test.com',
            'numero_commande': 'CMD-123'
        }

    def test_validation_email_no_email_returns_false(self, sender, sample_order):
        """Test que validation retourne False sans email."""
        sample_order['email_from'] = ''
        result = sender.send_validation_email(sample_order)
        assert result is False

    def test_validation_email_invalid_email_returns_false(self, sender, sample_order):
        """Test que validation retourne False pour email invalide."""
        sample_order['email_from'] = 'not_an_email'
        result = sender.send_validation_email(sample_order)
        assert result is False


class TestRejectionEmail:
    """Tests d'emails de rejet."""

    @pytest.fixture
    def sender(self):
        return EmailSender()

    @pytest.fixture
    def sample_order(self):
        """Commande exemple pour tests."""
        return {
            'id': 456,
            'client_nom': 'Fatima Zahra',
            'client_telephone': '+212698765432',
            'client_ville': 'Rabat',
            'produit_type': 'Sac fond carré',
            'quantite': 500,
            'unite': 'pièces',
            'prix_total': 2500.00,
            'statut': 'rejeté',
            'date_commande': '2025-01-16 14:00:00',
            'email_from': 'fatima@test.com',
            'numero_commande': 'CMD-456'
        }

    def test_rejection_email_no_email_returns_false(self, sender, sample_order):
        """Test que rejet retourne False sans email."""
        sample_order['email_from'] = ''
        result = sender.send_rejection_email(sample_order, "Informations incomplètes")
        assert result is False

    def test_rejection_email_invalid_email_returns_false(self, sender, sample_order):
        """Test que rejet retourne False pour email invalide."""
        sample_order['email_from'] = 'invalid'
        result = sender.send_rejection_email(sample_order, "Stock insuffisant")
        assert result is False


class TestEmailHTMLContent:
    """Tests du contenu HTML des emails."""

    def test_email_sender_has_validation_method(self):
        """Test que EmailSender a la méthode send_validation_email."""
        sender = EmailSender()
        assert hasattr(sender, 'send_validation_email')

    def test_email_sender_has_rejection_method(self):
        """Test que EmailSender a la méthode send_rejection_email."""
        sender = EmailSender()
        assert hasattr(sender, 'send_rejection_email')

    def test_email_sender_has_send_method(self):
        """Test que EmailSender a la méthode send_email."""
        sender = EmailSender()
        assert hasattr(sender, 'send_email')


class TestEmailConfiguration:
    """Tests de configuration email."""

    def test_smtp_port_is_587(self):
        """Test que le port SMTP est 587 (TLS)."""
        sender = EmailSender()
        assert sender.smtp_port == 587

    def test_smtp_server_is_gmail(self):
        """Test que le serveur SMTP est gmail."""
        sender = EmailSender()
        assert 'gmail' in sender.smtp_server.lower()


class TestEmailErrorHandling:
    """Tests de gestion d'erreurs email."""

    @pytest.fixture
    def sender_without_credentials(self):
        """Sender sans credentials."""
        sender = EmailSender()
        sender.email = None
        sender.password = None
        return sender

    def test_send_without_credentials_returns_false(self, sender_without_credentials):
        """Test envoi sans credentials retourne False."""
        result = sender_without_credentials.send_email(
            "test@example.com",
            "Test",
            "<html>Test</html>"
        )
        assert result is False

    def test_validation_without_credentials(self, sender_without_credentials):
        """Test validation sans credentials retourne False."""
        order = {
            'id': 1,
            'email_from': 'test@test.com',
            'client_nom': 'Test'
        }
        result = sender_without_credentials.send_validation_email(order)
        assert result is False

    def test_rejection_without_credentials(self, sender_without_credentials):
        """Test rejet sans credentials retourne False."""
        order = {
            'id': 1,
            'email_from': 'test@test.com',
            'client_nom': 'Test'
        }
        result = sender_without_credentials.send_rejection_email(order, "Raison")
        assert result is False


class TestEmailModuleImport:
    """Tests d'import du module email."""

    def test_import_email_sender_module(self):
        """Test import du module."""
        assert hasattr(email_sender_module, 'EmailSender')

    def test_email_sender_class_exists(self):
        """Test que la classe EmailSender existe."""
        assert EmailSender is not None


class TestEmailContent:
    """Contenu des emails envoyés (SMTP simulé)."""

    @pytest.fixture
    def configured_sender(self, tmp_path):
        return EmailSender(Settings.for_tests(tmp_path, gmail_email="commandes@example.com",
                                              gmail_app_password="abcd efgh ijkl mnop"))

    @pytest.fixture
    def smtp(self):
        with patch("tecpap.channels.email_sender.smtplib.SMTP") as smtp_class:
            server = MagicMock()
            smtp_class.return_value.__enter__.return_value = server
            yield smtp_class, server

    def test_validation_email_is_sent(self, configured_sender, smtp):
        smtp_class, server = smtp
        order = {"id": 5, "email_from": "client@example.com", "client_nom": "Snack Atlas",
                 "produit_type": "Sachets fond plat", "quantite": 500, "unite": "pièces"}

        assert configured_sender.send_validation_email(order) is True

        smtp_class.assert_called_once_with("smtp.gmail.com", 587, timeout=30)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("commandes@example.com", "abcdefghijklmnop")
        message = server.send_message.call_args.args[0]
        assert message["To"] == "client@example.com"
        assert "CMD-5" in message["Subject"]

    def test_client_values_are_escaped_in_html(self, configured_sender, smtp):
        _, server = smtp
        order = {"id": 1, "email_from": "client@example.com", "client_nom": "<script>alert(1)</script>"}

        configured_sender.send_order_received_email(order)

        message = server.send_message.call_args.args[0]
        html_part = [part for part in message.get_payload() if part.get_content_type() == "text/html"][0]
        html = html_part.get_payload(decode=True).decode("utf-8")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_rejection_email_contains_reason(self, configured_sender, smtp):
        _, server = smtp
        order = {"id": 2, "email_from": "client@example.com", "client_nom": "Atlas"}

        configured_sender.send_rejection_email(order, "Stock insuffisant")

        message = server.send_message.call_args.args[0]
        text_part = [part for part in message.get_payload() if part.get_content_type() == "text/plain"][0]
        assert "Stock insuffisant" in text_part.get_payload(decode=True).decode("utf-8")

    def test_subject_never_contains_a_line_break(self, configured_sender, smtp):
        _, server = smtp
        order = {"id": 3, "email_from": "client@example.com", "client_nom": "Atlas",
                 "numero_commande": "BC-1\r\nBcc: autre@example.com"}

        configured_sender.send_validation_email(order)

        message = server.send_message.call_args.args[0]
        assert "\n" not in message["Subject"] and "\r" not in message["Subject"]
        assert message["Bcc"] is None

    def test_smtp_failure_returns_false(self, configured_sender):
        import smtplib

        with patch("tecpap.channels.email_sender.smtplib.SMTP", side_effect=smtplib.SMTPException("refus")):
            assert configured_sender.send_email("client@example.com", "Sujet", "<p>x</p>") is False

    def test_smtp_settings_come_from_configuration(self, tmp_path):
        sender = EmailSender(Settings.for_tests(tmp_path, smtp_server="smtp.example.com", smtp_port=2525))
        assert (sender.smtp_server, sender.smtp_port) == ("smtp.example.com", 2525)


class TestEmailWording:
    """Texte des emails : mise en forme française, nom du client, promesses tenues, coordonnées."""

    @pytest.fixture
    def sent(self, tmp_path, monkeypatch):
        """Emails produits par un expéditeur configuré, sans envoi : (sujet, HTML, texte) par appel."""
        messages = []

        def record(self, to_email, subject, html_content, text_content=None):
            messages.append((subject, html_content, text_content))
            return True

        monkeypatch.setattr(EmailSender, "send_email", record)
        sender = EmailSender(Settings.for_tests(tmp_path, gmail_email="commandes@example.com",
                                                gmail_app_password="secret"))
        return sender, messages

    @staticmethod
    def _plain(text):
        return text.replace("\u202f", " ").replace("\xa0", " ")

    def _all_emails(self, sender, messages, order):
        sender.send_order_received_email(order)
        sender.send_validation_email(order)
        sender.send_rejection_email(order, "Produit hors catalogue")
        return messages

    @pytest.mark.parametrize("quantity, unit, expected", [
        (5000.0, "US", "Quantité : 5 000 pièces"),
        (20.0, "paquet", "Quantité : 20 paquets"),
        (1.0, "carton", "Quantité : 1 carton"),
        (2.5, "kg", "Quantité : 2,5 kg"),
        (1500, None, "Quantité : 1 500 pièces"),
        (0.0, "US", "Quantité : À préciser"),
        (None, "US", "Quantité : À préciser"),
    ])
    def test_quantity_and_unit_are_written_for_the_client(self, sent, quantity, unit, expected):
        sender, messages = sent
        order = {"id": 7, "email_from": "client@example.com", "client_nom": "Snack Atlas",
                 "produit_type": "Sachets fond plat", "quantite": quantity, "unite": unit}
        for _subject, html_body, text_body in self._all_emails(sender, messages, order):
            assert expected in self._plain(text_body)
            assert "5000.0" not in text_body and " US" not in text_body and "None" not in text_body
            assert expected.split(" : ")[1] in self._plain(html_body)

    def test_delivery_date_is_the_requested_one(self, sent):
        sender, messages = sent
        sender.send_validation_email({"id": 3, "email_from": "client@example.com", "client_nom": "Atlas",
                                      "quantite": 100, "date_livraison": "2026-11-15"})
        _subject, html_body, text_body = messages[0]
        assert "Livraison souhaitée : 15/11/2026" in text_body
        assert "Livraison souhaitée" in html_body and "15/11/2026" in html_body
        assert "estimée" not in html_body + text_body

    def test_missing_delivery_date(self, sent):
        sender, messages = sent
        sender.send_validation_email({"id": 3, "email_from": "client@example.com", "client_nom": "Atlas"})
        assert "Livraison souhaitée : Non précisée" in messages[0][2]

    def test_no_unkept_promise(self, sent):
        sender, messages = sent
        order = {"id": 4, "email_from": "client@example.com", "client_nom": "Atlas", "quantite": 100}
        for _subject, html_body, text_body in self._all_emails(sender, messages, order):
            content = (html_body + text_body).lower()
            for promise in ("24-48", "24h", "48h", "expédition", "lors de l'envoi", "en cours de préparation",
                            "bientôt prête", "plus brefs délais"):
                assert promise not in content

    @pytest.mark.parametrize("client_nom", ["Client WhatsApp +212612345678", "Client Inconnu", "achat@snack2.ma",
                                            "<achat@snack2.ma>", "", None])
    def test_placeholder_names_are_never_written(self, sent, client_nom):
        sender, messages = sent
        order = {"id": 8, "email_from": "achat@snack2.ma", "client_nom": client_nom, "quantite": 100}
        for _subject, html_body, text_body in self._all_emails(sender, messages, order):
            assert "\nBonjour,\n" in text_body
            assert "Bonjour," in html_body
            for placeholder in ("Client WhatsApp", "Client Inconnu", "Cher client", "Bonjour achat", "@snack2"):
                assert placeholder not in html_body + text_body

    def test_display_name_of_sender_address_is_kept(self, sent):
        sender, messages = sent
        sender.send_validation_email({"id": 9, "email_from": "achat@snack.ma",
                                      "client_nom": "Snack Atlas <achat@snack.ma>", "quantite": 100})
        _subject, html_body, text_body = messages[0]
        assert "Bonjour Snack Atlas," in text_body
        assert "Bonjour <strong>Snack Atlas</strong>," in html_body
        assert "achat@snack.ma" not in text_body

    def test_received_email_uses_extracted_fields(self, sent):
        sender, messages = sent
        sender.send_order_received_email({"id": 10, "email_from": "client@example.com",
                                          "entreprise_cliente": "Boulangerie Atlas",
                                          "type_produit": "Sac fond carré avec poignées plates",
                                          "quantite": 3000.0, "unite": "pièces"})
        text_body = self._plain(messages[0][2])
        assert "Bonjour Boulangerie Atlas," in text_body
        assert "Produit : Sac fond carré avec poignées plates" in text_body
        assert "Quantité : 3 000 pièces" in text_body
        assert "Référence : CMD-10" in text_body

    def test_footer_comes_from_company_info(self, sent):
        from tecpap.catalog import COMPANY_INFO

        sender, messages = sent
        order = {"id": 11, "email_from": "client@example.com", "client_nom": "Atlas", "quantite": 100}
        for _subject, html_body, text_body in self._all_emails(sender, messages, order):
            for key in ("address", "phone", "email", "website"):
                assert COMPANY_INFO[key] in text_body
                assert COMPANY_INFO[key] in html_body.replace("&#x27;", "'")
            assert "+212 5 22 86 56 83" not in html_body + text_body

    def test_html_declares_french(self, sent):
        sender, messages = sent
        order = {"id": 12, "email_from": "client@example.com", "client_nom": "Atlas"}
        for _subject, html_body, _text_body in self._all_emails(sender, messages, order):
            assert '<html lang="fr" dir="ltr">' in html_body
