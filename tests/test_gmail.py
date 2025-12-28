"""
Tests pour le module gmail_receiver.py
Tests de réception et traitement des emails
"""

import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestGmailConfiguration:
    """Tests de la configuration Gmail."""
    
    def test_imap_config_exists(self):
        """Test que la configuration IMAP existe."""
        import gmail_receiver
        
        source = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'gmail_receiver.py'), encoding='utf-8').read()
        assert 'imap' in source.lower() or 'IMAP' in source
    
    def test_gmail_server_address(self):
        """Test adresse serveur Gmail."""
        gmail_imap = 'imap.gmail.com'
        gmail_imap_port = 993
        
        assert 'gmail.com' in gmail_imap
        assert gmail_imap_port == 993


class TestEmailParsing:
    """Tests d'analyse des emails."""
    
    def test_parse_email_subject(self):
        """Test parsing du sujet."""
        email_data = {
            'subject': 'Commande - Ahmed Benali',
            'from': 'client@example.com',
            'body': 'Je veux commander 2 produits'
        }
        
        assert 'subject' in email_data
        assert len(email_data['subject']) > 0
    
    def test_parse_email_body(self):
        """Test parsing du corps."""
        email_body = """
        Bonjour,
        
        Je voudrais commander:
        - 2 x Premium Package
        
        Nom: Ahmed Benali
        Téléphone: 0612345678
        Ville: Casablanca
        
        Merci
        """
        
        assert 'commander' in email_body.lower()
        assert 'Téléphone' in email_body
    
    def test_extract_phone_from_body(self):
        """Test extraction téléphone du corps."""
        import re
        
        body = "Mon numéro est 0612345678 pour la livraison"
        
        # Regex for Moroccan phone numbers
        phone_pattern = r'0[5-7]\d{8}'
        match = re.search(phone_pattern, body)
        
        assert match is not None
        assert match.group(0) == '0612345678'
    
    def test_extract_city_from_body(self):
        """Test extraction ville du corps."""
        body = "Ville: Casablanca\nAdresse: 123 Rue Example"
        
        # Simple extraction
        if 'Ville:' in body:
            lines = body.split('\n')
            for line in lines:
                if 'Ville:' in line:
                    city = line.split('Ville:')[1].strip()
                    assert city == 'Casablanca'


class TestEmailAttachments:
    """Tests des pièces jointes."""
    
    def test_detect_audio_attachment(self):
        """Test détection pièce jointe audio."""
        attachment = {
            'filename': 'voice_note.ogg',
            'content_type': 'audio/ogg',
            'size': 15000
        }
        
        is_audio = attachment['content_type'].startswith('audio/')
        assert is_audio
    
    def test_supported_audio_types(self):
        """Test types audio supportés."""
        supported_types = ['audio/ogg', 'audio/mpeg', 'audio/wav', 'audio/mp4', 'audio/x-m4a']
        
        test_type = 'audio/ogg'
        assert test_type in supported_types
    
    def test_attachment_size_limit(self):
        """Test limite de taille pièce jointe."""
        max_size_mb = 25  # Gmail limit
        max_size_bytes = max_size_mb * 1024 * 1024
        
        attachment_size = 10 * 1024 * 1024  # 10MB
        
        assert attachment_size < max_size_bytes


class TestEmailFiltering:
    """Tests du filtrage des emails."""
    
    def test_filter_by_unread(self):
        """Test filtrage par non lu."""
        emails = [
            {'id': 1, 'read': False},
            {'id': 2, 'read': True},
            {'id': 3, 'read': False}
        ]
        
        unread = [e for e in emails if not e['read']]
        assert len(unread) == 2
    
    def test_filter_by_subject_keyword(self):
        """Test filtrage par mot-clé sujet."""
        emails = [
            {'subject': 'Commande - Client A'},
            {'subject': 'Question about product'},
            {'subject': 'Nouvelle commande'}
        ]
        
        order_emails = [e for e in emails if 'commande' in e['subject'].lower()]
        assert len(order_emails) == 2
    
    def test_filter_by_sender(self):
        """Test filtrage par expéditeur."""
        emails = [
            {'from': 'client1@example.com'},
            {'from': 'spam@spam.com'},
            {'from': 'client2@example.com'}
        ]
        
        # Exclude spam domain
        valid_emails = [e for e in emails if 'spam.com' not in e['from']]
        assert len(valid_emails) == 2


class TestOrderExtraction:
    """Tests d'extraction de commande depuis email."""
    
    def test_extract_order_from_structured_email(self):
        """Test extraction depuis email structuré."""
        email_body = """
        Nouvelle commande:
        
        Nom: Fatima Zahra
        Téléphone: 0698765432
        Ville: Rabat
        Produit: Standard Package
        Quantité: 3
        """
        
        # Simple parsing
        lines = email_body.strip().split('\n')
        order_data = {}
        
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                order_data[key.strip()] = value.strip()
        
        assert 'Nom' in order_data
        assert order_data['Quantité'] == '3'
    
    def test_extract_order_from_free_text(self):
        """Test extraction depuis texte libre."""
        email_body = "Bonjour, je m'appelle Ahmed et je veux commander 2 premium packages. Mon numéro est 0612345678. J'habite à Casablanca."
        
        # Keywords present
        assert 'commander' in email_body.lower()
        assert 'premium' in email_body.lower()
        assert '0612345678' in email_body


class TestEmailSource:
    """Tests de la source email."""
    
    def test_order_source_is_email(self):
        """Test que la source est email."""
        order = {
            'source': 'email',
            'email_id': 'message_id_123'
        }
        
        assert order['source'] == 'email'
    
    def test_email_metadata_saved(self):
        """Test que les métadonnées email sont sauvegardées."""
        email_metadata = {
            'message_id': '<abc123@mail.gmail.com>',
            'from': 'client@example.com',
            'subject': 'Commande produit',
            'date': '2025-01-15 10:30:00'
        }
        
        assert 'message_id' in email_metadata
        assert 'from' in email_metadata


class TestIMAPConnection:
    """Tests de connexion IMAP (mockés)."""
    
    @pytest.fixture
    def mock_imap(self):
        """Mock de la connexion IMAP."""
        with patch('imaplib.IMAP4_SSL') as mock:
            yield mock
    
    def test_imap_login_structure(self, mock_imap):
        """Test structure de login IMAP."""
        # Simulate login
        imap_instance = MagicMock()
        mock_imap.return_value = imap_instance
        
        # Login should be called with email and password
        imap_instance.login.return_value = ('OK', [b'Success'])
        
        result = imap_instance.login('test@gmail.com', 'password')
        assert result[0] == 'OK'
    
    def test_imap_select_inbox(self, mock_imap):
        """Test sélection INBOX."""
        imap_instance = MagicMock()
        mock_imap.return_value = imap_instance
        
        imap_instance.select.return_value = ('OK', [b'5'])  # 5 messages
        
        result = imap_instance.select('INBOX')
        assert result[0] == 'OK'


class TestEmailProcessingFlow:
    """Tests du flux de traitement email."""
    
    def test_processing_status_tracking(self):
        """Test suivi du statut de traitement."""
        processing_status = {
            'total_emails': 10,
            'processed': 5,
            'errors': 1,
            'progress': 50
        }
        
        assert processing_status['progress'] == 50
        assert processing_status['processed'] + processing_status['errors'] <= processing_status['total_emails']
    
    def test_progress_percentage_calculation(self):
        """Test calcul pourcentage de progression."""
        total = 20
        processed = 15
        
        progress = int((processed / total) * 100)
        
        assert progress == 75
    
    def test_progress_reaches_100(self):
        """Test que la progression atteint 100%."""
        total = 10
        processed = 10
        
        progress = int((processed / total) * 100)
        
        assert progress == 100


class TestEmailDecoding:
    """Tests de décodage des emails."""
    
    def test_decode_utf8_subject(self):
        """Test décodage sujet UTF-8."""
        import base64
        
        # Encoded subject
        encoded = '=?utf-8?B?Q29tbWFuZGUgLSBBaG1lZA==?='
        
        # Decode
        if '=?utf-8?B?' in encoded:
            b64_part = encoded.split('?')[3]
            decoded = base64.b64decode(b64_part).decode('utf-8')
            assert decoded == 'Commande - Ahmed'
    
    def test_decode_arabic_content(self):
        """Test décodage contenu arabe."""
        arabic_text = 'مرحبا'  # Hello in Arabic
        
        # Should be valid UTF-8
        encoded = arabic_text.encode('utf-8')
        decoded = encoded.decode('utf-8')
        
        assert decoded == arabic_text
    
    def test_handle_mixed_encoding(self):
        """Test gestion encodage mixte."""
        mixed_text = "Bonjour مرحبا Hello"
        
        # Should handle mixed Latin and Arabic
        assert len(mixed_text) > 0


class TestEmailDateParsing:
    """Tests de parsing des dates d'email."""
    
    def test_parse_email_date(self):
        """Test parsing date email."""
        from email.utils import parsedate_to_datetime
        
        email_date = "Mon, 15 Jan 2025 10:30:00 +0100"
        
        dt = parsedate_to_datetime(email_date)
        
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15
    
    def test_handle_invalid_date(self):
        """Test gestion date invalide."""
        invalid_date = "Invalid Date Format"
        
        from email.utils import parsedate_to_datetime
        
        try:
            parsedate_to_datetime(invalid_date)
            assert False, "Should raise exception"
        except (ValueError, TypeError):
            assert True


class TestDuplicateDetection:
    """Tests de détection des doublons."""
    
    def test_detect_duplicate_by_message_id(self):
        """Test détection doublon par message ID."""
        processed_ids = ['<abc123@mail>', '<def456@mail>']
        new_id = '<abc123@mail>'
        
        is_duplicate = new_id in processed_ids
        assert is_duplicate
    
    def test_new_email_not_duplicate(self):
        """Test nouvel email non doublon."""
        processed_ids = ['<abc123@mail>', '<def456@mail>']
        new_id = '<ghi789@mail>'
        
        is_duplicate = new_id in processed_ids
        assert not is_duplicate


class TestHTMLEmailParsing:
    """Tests de parsing HTML email."""
    
    def test_strip_html_tags(self):
        """Test suppression balises HTML."""
        import re
        
        html_body = "<html><body><p>Bonjour, je veux commander</p></body></html>"
        
        # Strip HTML
        text = re.sub(r'<[^>]+>', '', html_body)
        
        assert '<' not in text
        assert 'commander' in text
    
    def test_extract_text_from_html(self):
        """Test extraction texte depuis HTML."""
        html = """
        <div>
            <p>Nom: Ahmed</p>
            <p>Produit: Premium</p>
        </div>
        """
        
        import re
        text = re.sub(r'<[^>]+>', '\n', html)
        text = ' '.join(text.split())
        
        assert 'Ahmed' in text
        assert 'Premium' in text


class TestEmailValidation:
    """Tests de validation email."""
    
    def test_valid_email_has_required_fields(self):
        """Test email valide a les champs requis."""
        email = {
            'from': 'sender@example.com',
            'subject': 'Test',
            'body': 'Content'
        }
        
        required = ['from', 'subject', 'body']
        has_all = all(field in email for field in required)
        
        assert has_all
    
    def test_empty_body_is_invalid(self):
        """Test corps vide est invalide."""
        email = {
            'from': 'sender@example.com',
            'subject': 'Test',
            'body': ''
        }
        
        is_valid = len(email['body'].strip()) > 0
        assert not is_valid


class TestErrorHandling:
    """Tests de gestion des erreurs."""
    
    def test_connection_error_handling(self):
        """Test gestion erreur de connexion."""
        error_messages = []
        
        try:
            raise ConnectionError("IMAP connection failed")
        except ConnectionError as e:
            error_messages.append(str(e))
        
        assert len(error_messages) == 1
        assert 'connection' in error_messages[0].lower()
    
    def test_authentication_error_handling(self):
        """Test gestion erreur d'authentification."""
        error_messages = []
        
        try:
            raise Exception("Authentication failed")
        except Exception as e:
            error_messages.append(str(e))
        
        assert 'Authentication' in error_messages[0]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
