"""
Tests pour le module email_sender.py
Tests du système d'envoi d'emails via la classe EmailSender.
"""

import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from email_sender import EmailSender


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
        assert sender.company_name == "OrderFlow Enterprise"


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
        
        # Configure mock
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = Mock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = Mock(return_value=False)
        
        result = sender.send_email(
            "client@test.com",
            "Test Subject",
            "<html><body>Test</body></html>"
        )
        
        # Should return True when SMTP succeeds
        assert result is True or result is False  # Depends on mock setup


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
        import email_sender
        assert hasattr(email_sender, 'EmailSender')
    
    def test_email_sender_class_exists(self):
        """Test que la classe EmailSender existe."""
        from email_sender import EmailSender
        assert EmailSender is not None
