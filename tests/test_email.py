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

    def test_smtp_failure_returns_false(self, configured_sender):
        import smtplib

        with patch("tecpap.channels.email_sender.smtplib.SMTP", side_effect=smtplib.SMTPException("refus")):
            assert configured_sender.send_email("client@example.com", "Sujet", "<p>x</p>") is False

    def test_smtp_settings_come_from_configuration(self, tmp_path):
        sender = EmailSender(Settings.for_tests(tmp_path, smtp_server="smtp.example.com", smtp_port=2525))
        assert (sender.smtp_server, sender.smtp_port) == ("smtp.example.com", 2525)
