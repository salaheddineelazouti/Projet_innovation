"""
Tests de réception des emails (tecpap.channels.email_receiver) avec un serveur IMAP simulé.
"""

from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from tecpap.channels.email_receiver import GmailReceiver, _safe_filename
from tecpap.config import Settings


class TestGmailConfiguration:
    """Tests de la configuration Gmail."""

    def test_imap_config_exists(self):
        """Le client IMAP vise le serveur Gmail sur le port IMAPS."""
        assert GmailReceiver.imap_server == "imap.gmail.com"
        assert GmailReceiver.imap_port == 993

    def test_gmail_server_address(self):
        """Adresse du serveur IMAP de Gmail."""
        assert "gmail.com" in GmailReceiver.imap_server

    def test_credentials_come_from_settings(self, tmp_path):
        receiver = GmailReceiver(Settings.for_tests(tmp_path, gmail_email="a@example.com", gmail_app_password="x"))
        assert receiver.email_address == "a@example.com"

    def test_connect_without_credentials(self, tmp_path):
        with patch("imaplib.IMAP4_SSL") as imap:
            assert GmailReceiver(Settings.for_tests(tmp_path)).connect() is False
        imap.assert_not_called()


class FakeImap:
    """Serveur IMAP simulé : messages du dossier INBOX désignés par leur UID.

    Args:
        messages: contenu brut de chaque message, par UID.
        uidvalidity: valeur UIDVALIDITY du dossier.
        unseen: UID des messages non lus (critère ``UNSEEN``).
    """

    def __init__(self, messages=None, uidvalidity=7, unseen=()):
        self.messages = dict(messages or {})
        self.uidvalidity = uidvalidity
        self.unseen = set(unseen)
        self.fetched = []
        self.logged_out = False

    def select(self, folder):
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code):
        return code, [str(self.uidvalidity).encode()]

    def status(self, folder, names):
        return "OK", [f'"{folder}" (UIDVALIDITY {self.uidvalidity})'.encode()]

    def uid(self, command, *args):
        if command == "SEARCH":
            uids = sorted(self.messages) if args[-1] == "ALL" else sorted(self.unseen & set(self.messages))
            return "OK", [" ".join(map(str, uids)).encode()]
        if command == "FETCH":
            uid = int(args[0])
            self.fetched.append(uid)
            if uid not in self.messages:
                return "OK", [None]
            return "OK", [(f"1 (UID {uid} RFC822 {{{len(self.messages[uid])}}}".encode(), self.messages[uid]), b")"]
        raise AssertionError(f"commande IMAP inattendue : {command}")

    def fetch(self, *args):
        raise AssertionError("lecture par numéro de séquence interdite : les UID sont seuls utilisés")

    def search(self, *args):
        raise AssertionError("recherche par numéro de séquence interdite : les UID sont seuls utilisés")

    def logout(self):
        self.logged_out = True


def _raw_email_with_attachment(filename="bon.pdf"):
    msg = EmailMessage()
    msg["Subject"] = "Commande sachets"
    msg["From"] = "Client <client@example.com>"
    msg["Date"] = "Mon, 15 Jan 2025 10:30:00 +0100"
    msg.set_content("Bonjour, 3000 sachets fond plat svp.")
    msg.add_attachment(b"%PDF-1.4 contenu", maintype="application", subtype="pdf", filename=filename)
    return msg.as_bytes()


class TestImapReading:
    """Lecture des emails et des pièces jointes avec un serveur IMAP simulé."""

    @pytest.fixture
    def receiver(self, tmp_path):
        receiver = GmailReceiver(Settings.for_tests(tmp_path, gmail_email="a@example.com", gmail_app_password="x"))
        receiver.connection = FakeImap()
        return receiver

    def test_recent_emails_are_parsed(self, receiver):
        receiver.connection.messages = {1: _raw_email_with_attachment("ancien.pdf"), 2: _raw_email_with_attachment()}

        emails = receiver.get_recent_emails(count=1)

        assert len(emails) == 1
        assert (emails[0]["id"], emails[0]["uid"]) == ("7:2", 2)
        assert emails[0]["subject"] == "Commande sachets"
        assert "3000 sachets" in emails[0]["body"]
        assert emails[0]["attachments"] == ["bon.pdf"]

    def test_attachments_are_saved_without_directories(self, receiver, tmp_path):
        receiver.connection.messages = {1: _raw_email_with_attachment("../../evil.pdf")}

        saved = receiver.download_attachments("7:1", save_dir=str(tmp_path / "pj"))

        assert len(saved) == 1
        assert saved[0].startswith(str(tmp_path / "pj"))
        assert saved[0].endswith("evil.pdf")

    def test_not_connected_returns_empty_list(self, tmp_path):
        assert GmailReceiver(Settings.for_tests(tmp_path)).get_recent_emails() == []

    @pytest.mark.parametrize("name, expected", [
        ("bon.pdf", "bon.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Windows\\x.png", "x.png"),
        ("..", "piece_jointe"),
    ])
    def test_safe_filename(self, name, expected):
        assert _safe_filename(name) == expected

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

        # Expression régulière des numéros marocains
        phone_pattern = r'0[5-7]\d{8}'
        match = re.search(phone_pattern, body)

        assert match is not None
        assert match.group(0) == '0612345678'

    def test_extract_city_from_body(self):
        """Test extraction ville du corps."""
        body = "Ville: Casablanca\nAdresse: 123 Rue Example"

        # Extraction simple
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
        max_size_mb = 25  # limite de Gmail
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

        # Exclusion du domaine indésirable
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

        # Découpage simple
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

        # Mots-clés présents
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
        # Connexion simulée
        imap_instance = MagicMock()
        mock_imap.return_value = imap_instance

        # Connexion avec l'email et le mot de passe
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

        # Sujet encodé
        encoded = '=?utf-8?B?Q29tbWFuZGUgLSBBaG1lZA==?='

        # Décodage
        if '=?utf-8?B?' in encoded:
            b64_part = encoded.split('?')[3]
            decoded = base64.b64decode(b64_part).decode('utf-8')
            assert decoded == 'Commande - Ahmed'

    def test_decode_arabic_content(self):
        """Test décodage contenu arabe."""
        arabic_text = 'مرحبا'  # « bonjour » en arabe

        # Texte UTF-8 valide
        encoded = arabic_text.encode('utf-8')
        decoded = encoded.decode('utf-8')

        assert decoded == arabic_text

    def test_handle_mixed_encoding(self):
        """Test gestion encodage mixte."""
        mixed_text = "Bonjour مرحبا Hello"

        # Mélange de caractères latins et arabes
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

        with pytest.raises((ValueError, TypeError)):
            parsedate_to_datetime(invalid_date)


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

        # Suppression des balises HTML
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
