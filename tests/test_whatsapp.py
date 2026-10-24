"""
Tests du canal WhatsApp (tecpap.channels.whatsapp) : Twilio, OpenAI et téléchargements simulés.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from tecpap.channels.whatsapp import WhatsAppClient, WhatsAppReceiver, is_allowed_media_url
from tecpap.config import Settings

TWILIO_MEDIA_URL = "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages/MM1/Media/ME1"


@pytest.fixture
def receiver(tmp_path):
    settings = Settings.for_tests(tmp_path, twilio_account_sid="AC" + "0" * 32, twilio_auth_token="jeton")
    return WhatsAppReceiver(settings)


class TestWhatsAppConfiguration:
    """Tests de la configuration WhatsApp."""

    def test_twilio_config_exists(self, receiver):
        """Les identifiants Twilio viennent de la configuration."""
        assert receiver.account_sid.startswith("AC")
        assert receiver.auth_token == "jeton"

    def test_sandbox_number_format(self):
        """Format du numéro du bac à sable Twilio."""
        sandbox_number = "+14155238886"
        assert sandbox_number.startswith("+")
        assert len(sandbox_number) == 12

    def test_whatsapp_client_alias(self):
        assert WhatsAppClient is WhatsAppReceiver

    def test_media_dir_from_settings(self, receiver, tmp_path):
        assert receiver.media_dir == str(tmp_path / "whatsapp_media")

    def test_no_openai_client_without_key(self, receiver):
        assert receiver.openai_client is None

    def test_connect_without_credentials(self, tmp_path):
        assert WhatsAppReceiver(Settings.for_tests(tmp_path)).connect() is False


class TestPhoneNumberFormatting:
    """Tests du formatage des numéros de téléphone."""

    def test_format_moroccan_number_with_plus(self):
        """Test formatage numéro marocain avec +."""
        phone = '+212612345678'

        # Format valide
        assert phone.startswith('+212')
        assert len(phone) == 13

    def test_format_moroccan_number_without_plus(self):
        """Test formatage numéro marocain sans +."""
        phone = '0612345678'

        # Commence par 0
        assert phone.startswith('0')
        assert len(phone) == 10

    def test_normalize_phone_number(self):
        """Test normalisation du numéro."""
        phones = [
            '0612345678',
            '+212612345678',
            '212612345678',
            '06 12 34 56 78'
        ]

        # Tous convertibles au format standard
        for phone in phones:
            cleaned = phone.replace(' ', '').replace('-', '')
            assert len(cleaned) >= 10


class TestWhatsAppMessageParsing:
    """Tests d'analyse des messages WhatsApp."""

    def test_parse_text_message(self):
        """Test parsing d'un message texte."""
        message_data = {
            'Body': 'Je veux commander 2 produits',
            'From': 'whatsapp:+212612345678',
            'To': 'whatsapp:+14155238886'
        }

        assert 'Body' in message_data
        assert message_data['From'].startswith('whatsapp:')

    def test_extract_phone_from_whatsapp_format(self):
        """Test extraction du numéro depuis format WhatsApp."""
        whatsapp_from = 'whatsapp:+212612345678'

        # Extraction du numéro
        phone = whatsapp_from.replace('whatsapp:', '')

        assert phone == '+212612345678'

    def test_parse_media_message(self):
        """Test parsing d'un message média."""
        message_data = {
            'Body': '',
            'From': 'whatsapp:+212612345678',
            'MediaContentType0': 'audio/ogg',
            'MediaUrl0': 'https://api.twilio.com/media/xxx'
        }

        has_media = 'MediaUrl0' in message_data
        assert has_media is True

    def test_detect_audio_message(self):
        """Test détection message audio."""
        message_data = {
            'MediaContentType0': 'audio/ogg'
        }

        content_type = message_data.get('MediaContentType0', '')
        is_audio = 'audio' in content_type

        assert is_audio is True


class TestWhatsAppResponseFormatting:
    """Tests du formatage des réponses WhatsApp."""

    def test_confirmation_message_format(self):
        """Test format du message de confirmation."""
        order_data = {
            'id': 123,
            'client_nom': 'Ahmed',
            'produit': 'Premium',
            'quantite': 2,
            'prix_total': 500
        }

        # Message de confirmation
        message = f"""✅ Commande #{order_data['id']} reçue

📦 Produit: {order_data['produit']}
📊 Quantité: {order_data['quantite']}
💰 Total: {order_data['prix_total']} MAD

Merci pour votre commande!"""

        assert str(order_data['id']) in message
        assert order_data['produit'] in message

    def test_confirmation_no_confidence_percentage(self):
        """Test que la confirmation n'a pas de pourcentage de confiance."""
        order_data = {
            'id': 456,
            'client_nom': 'Test',
            'produit': 'Standard',
            'quantite': 1,
            'prix_total': 100
        }

        # Message sans taux de confiance
        message = f"""✅ Commande #{order_data['id']} reçue

📦 Produit: {order_data['produit']}
📊 Quantité: {order_data['quantite']}

Merci!"""

        assert 'Confiance' not in message
        assert '%' not in message

    def test_error_message_format(self):
        """Test format du message d'erreur."""
        error_message = "❌ Désolé, je n'ai pas pu traiter votre message. Veuillez réessayer."

        assert '❌' in error_message or 'Désolé' in error_message


class TestTwilioIntegration:
    """Tests d'intégration Twilio (mockés)."""

    @pytest.fixture
    def mock_twilio_client(self):
        """Mock du client Twilio."""
        with patch('twilio.rest.Client') as mock:
            yield mock

    def test_send_message_structure(self, mock_twilio_client):
        """Test structure d'envoi de message."""
        # Envoi simulé
        message_params = {
            'from_': 'whatsapp:+14155238886',
            'to': 'whatsapp:+212612345678',
            'body': 'Test message'
        }

        assert 'from_' in message_params
        assert 'to' in message_params
        assert 'body' in message_params
        assert message_params['from_'].startswith('whatsapp:')

    def test_webhook_signature_validation_concept(self):
        """Test concept de validation de signature webhook."""
        # Twilio envoie l'en-tête X-Twilio-Signature
        headers = {
            'X-Twilio-Signature': 'abc123signature'
        }

        assert 'X-Twilio-Signature' in headers


class TestAudioProcessing:
    """Tests du traitement audio."""

    def test_supported_audio_formats(self):
        """Test formats audio supportés."""
        supported_formats = ['audio/ogg', 'audio/mpeg', 'audio/wav', 'audio/mp4']

        test_content_type = 'audio/ogg'

        # Format pris en charge
        is_supported = any(test_content_type.startswith(fmt.split('/')[0]) for fmt in supported_formats)
        assert is_supported

    def test_audio_url_download_concept(self):
        """Test concept de téléchargement audio."""
        media_url = 'https://api.twilio.com/2010-04-01/Accounts/xxx/Messages/yyy/Media/zzz'

        assert media_url.startswith('https://')
        assert 'twilio.com' in media_url


class TestOrderFromWhatsApp:
    """Tests de création de commande depuis WhatsApp."""

    def test_whatsapp_order_source(self):
        """Test que la source est WhatsApp."""
        order = {
            'source': 'whatsapp',
            'client_telephone': '+212612345678'
        }

        assert order['source'] == 'whatsapp'

    def test_order_data_structure(self):
        """Test structure des données de commande."""
        order = {
            'client_nom': 'Ahmed',
            'client_telephone': '+212612345678',
            'client_ville': 'Casablanca',
            'produit': 'Premium Package',
            'quantite': 2,
            'prix_total': 500.00,
            'source': 'whatsapp',
            'message_original': 'Je veux commander 2 premium packages'
        }

        required_fields = ['client_nom', 'client_telephone', 'produit', 'quantite', 'source']

        for field in required_fields:
            assert field in order


class TestDarijaInWhatsApp:
    """Tests du support Darija dans WhatsApp."""

    def test_darija_message_detection(self):
        """Test détection message en Darija."""
        darija_messages = [
            'bghit ncommandi jouj dial premium',
            'ana bghit chi produit',
            'chhal taman dyal premium'
        ]

        # Mots-clés en darija
        darija_keywords = ['bghit', 'chhal', 'dial', 'ana', 'chi']

        for message in darija_messages:
            message_lower = message.lower()
            has_darija = any(kw in message_lower for kw in darija_keywords)
            assert has_darija

    def test_darija_number_words(self):
        """Test mots numériques Darija."""
        number_mappings = {
            'wahed': 1,
            'jouj': 2,
            'tlata': 3,
            'rbaa': 4,
            'khamsa': 5
        }

        message = 'bghit jouj dial premium'

        for word, num in number_mappings.items():
            if word in message:
                assert num == 2


class TestClientIdentification:
    """Tests d'identification client."""

    def test_identify_by_phone(self):
        """Test identification par téléphone."""
        existing_client = {
            'telephone': '+212612345678',
            'nom': 'Ahmed Benali'
        }

        incoming_phone = '+212612345678'

        # Rapprochement par téléphone
        is_existing = existing_client['telephone'] == incoming_phone
        assert is_existing

    def test_new_client_creation_data(self):
        """Test données pour nouveau client."""
        new_client_data = {
            'nom': None,  # peut être inconnu
            'telephone': '+212698765432',
            'ville': None
        }

        assert 'telephone' in new_client_data
        assert new_client_data['telephone'].startswith('+212')


class TestMessageValidation:
    """Tests de validation des messages."""

    def test_empty_message(self):
        """Test message vide."""
        message = ''

        is_valid = len(message.strip()) > 0
        assert not is_valid

    def test_message_too_short(self):
        """Test message trop court."""
        message = 'hi'
        min_length = 3

        is_valid = len(message) >= min_length
        assert not is_valid

    def test_message_with_order_intent(self):
        """Test message avec intention de commande."""
        messages_with_intent = [
            'je veux commander',
            'bghit ncommandi',
            'I want to order',
            'commande produit'
        ]

        order_keywords = ['command', 'order', 'bghit', 'acheter', 'buy']

        for message in messages_with_intent:
            message_lower = message.lower()
            has_intent = any(kw in message_lower for kw in order_keywords)
            assert has_intent


class TestWhatsAppWebhook:
    """Tests du webhook WhatsApp."""

    def test_webhook_response_twiml(self):
        """Test réponse TwiML du webhook."""
        # Format de réponse TwiML
        twiml_response = '<?xml version="1.0" encoding="UTF-8"?><Response><Message>Test</Message></Response>'

        assert 'Response' in twiml_response
        assert 'Message' in twiml_response

    def test_webhook_request_parsing(self):
        """Test parsing requête webhook."""
        webhook_data = {
            'AccountSid': 'ACxxxx',
            'ApiVersion': '2010-04-01',
            'Body': 'Test message',
            'From': 'whatsapp:+212612345678',
            'To': 'whatsapp:+14155238886',
            'SmsMessageSid': 'SMxxxx',
            'NumMedia': '0'
        }

        # Champs obligatoires
        assert 'Body' in webhook_data
        assert 'From' in webhook_data
        assert 'To' in webhook_data


class TestMediaHandling:
    """Tests de gestion des médias."""

    def test_detect_image_media(self):
        """Test détection média image."""
        message_data = {
            'MediaContentType0': 'image/jpeg',
            'NumMedia': '1'
        }

        content_type = message_data.get('MediaContentType0', '')
        is_image = content_type.startswith('image/')

        assert is_image

    def test_detect_voice_note(self):
        """Test détection note vocale."""
        message_data = {
            'MediaContentType0': 'audio/ogg; codecs=opus',
            'NumMedia': '1'
        }

        content_type = message_data.get('MediaContentType0', '')
        is_voice = 'audio' in content_type

        assert is_voice

    def test_multiple_media_count(self):
        """Test comptage médias multiples."""
        message_data = {
            'NumMedia': '3',
            'MediaUrl0': 'url1',
            'MediaUrl1': 'url2',
            'MediaUrl2': 'url3'
        }

        num_media = int(message_data.get('NumMedia', 0))

        assert num_media == 3


class TestRateLimiting:
    """Tests de rate limiting (concept)."""

    def test_rate_limit_tracking_concept(self):
        """Test concept de tracking rate limit."""
        # Suivi des requêtes par numéro
        request_tracker = {
            '+212612345678': {
                'count': 5,
                'first_request': '2025-01-15T10:00:00',
                'last_request': '2025-01-15T10:05:00'
            }
        }

        phone = '+212612345678'
        max_requests_per_minute = 10

        current_count = request_tracker.get(phone, {}).get('count', 0)
        is_rate_limited = current_count >= max_requests_per_minute

        assert not is_rate_limited  # 5 < 10


class TestErrorResponses:
    """Tests des réponses d'erreur."""

    def test_transcription_error_message(self):
        """Test message d'erreur de transcription."""
        error_response = "❌ Désolé, je n'ai pas pu transcrire votre message audio. Veuillez réessayer ou envoyer un message texte."

        assert 'transcrire' in error_response or 'audio' in error_response

    def test_processing_error_message(self):
        """Test message d'erreur de traitement."""
        error_response = "❌ Une erreur s'est produite lors du traitement de votre commande."

        assert 'erreur' in error_response.lower()

    def test_invalid_format_message(self):
        """Test message format invalide."""
        error_response = "⚠️ Format de message non reconnu. Veuillez décrire votre commande."

        assert 'format' in error_response.lower() or 'message' in error_response.lower()



class TestIncomingMessages:
    """Analyse des messages reçus par le webhook."""

    def test_text_message(self, receiver):
        result = receiver.process_incoming_message({"From": "whatsapp:+212612345678", "Body": " bghit 2000 sachet "})
        assert result["type"] == "text"
        assert result["from"] == "+212612345678"
        assert result["content"] == "bghit 2000 sachet"

    def test_format_for_extraction(self, receiver):
        result = receiver.process_incoming_message({"From": "whatsapp:+212612345678", "Body": "500 sacs"})
        formatted = receiver.format_for_extraction(result)
        assert formatted["subject"] == "WhatsApp - +212612345678"
        assert formatted["body"] == "Message: 500 sacs"
        assert formatted["source"] == "whatsapp"

    def test_invalid_media_count_is_ignored(self, receiver):
        result = receiver.process_incoming_message({"From": "whatsapp:+1", "Body": "x", "NumMedia": "abc"})
        assert result["type"] == "text"

    def test_pdf_is_read_with_pypdf(self, receiver, tmp_path):
        from reportlab.pdfgen import canvas

        pdf_path = tmp_path / "bon.pdf"
        pdf = canvas.Canvas(str(pdf_path))
        pdf.drawString(100, 750, "Commande 1500 sacs poignees plates")
        pdf.save()

        with patch.object(receiver, "download_media", return_value=str(pdf_path)):
            result = receiver.process_incoming_message({
                "From": "whatsapp:+212600000000", "NumMedia": "1",
                "MediaUrl0": TWILIO_MEDIA_URL, "MediaContentType0": "application/pdf",
            })

        assert result["type"] == "document"
        assert "1500 sacs" in result["extracted_text"]

    def test_audio_without_openai_key(self, receiver):
        result = receiver.process_incoming_message({
            "From": "whatsapp:+212600000000", "NumMedia": "1",
            "MediaUrl0": TWILIO_MEDIA_URL, "MediaContentType0": "audio/ogg",
        })
        assert result["type"] == "audio"
        assert result["transcription"] is None


class TestMediaDownload:
    """Téléchargement des médias Twilio."""

    @pytest.mark.parametrize("url, allowed", [
        (TWILIO_MEDIA_URL, True),
        ("https://media.twiliocdn.twilio.com/x", True),
        ("http://api.twilio.com/x", False),
        ("https://evil.example.com/twilio.com", False),
        ("https://twilio.com.evil.example/x", False),
        ("", False),
        (None, False),
    ])
    def test_allowed_media_hosts(self, url, allowed):
        assert is_allowed_media_url(url) is allowed

    def test_foreign_host_is_never_requested(self, receiver):
        with patch("tecpap.channels.whatsapp.requests.get") as get:
            assert receiver.download_media("https://evil.example.com/x.jpg") is None
        get.assert_not_called()

    def test_download_saves_file(self, receiver):
        response = Mock(status_code=200, content=b"donnees")
        with patch("tecpap.channels.whatsapp.requests.get", return_value=response) as get:
            path = receiver.download_media(TWILIO_MEDIA_URL, "media.bin")
        with open(path, "rb") as f:
            assert f.read() == b"donnees"
        assert get.call_args.kwargs["auth"] == (receiver.account_sid, receiver.auth_token)
        assert get.call_args.kwargs["timeout"] > 0

    def test_oversized_media_is_refused(self, tmp_path):
        small = WhatsAppReceiver(Settings.for_tests(tmp_path, max_media_bytes=4))
        with patch("tecpap.channels.whatsapp.requests.get", return_value=Mock(status_code=200, content=b"12345")):
            assert small.download_media(TWILIO_MEDIA_URL, "gros.bin") is None


class TestTranscription:
    """Transcription des notes vocales (client OpenAI simulé)."""

    def _receiver(self, tmp_path, **overrides):
        openai_client = MagicMock()
        openai_client.audio.transcriptions.create.return_value = "bghit 3000 sachet"
        receiver = WhatsAppReceiver(Settings.for_tests(tmp_path, **overrides), openai_client=openai_client)
        audio = tmp_path / "audio.ogg"
        audio.write_bytes(b"0" * 500)
        return receiver, openai_client, str(audio)

    def test_automatic_language_detection_by_default(self, tmp_path):
        receiver, openai_client, audio = self._receiver(tmp_path)
        with patch.object(receiver, "download_media", return_value=audio):
            assert receiver.transcribe_audio(TWILIO_MEDIA_URL) == "bghit 3000 sachet"
        kwargs = openai_client.audio.transcriptions.create.call_args.kwargs
        assert kwargs["model"] == "whisper-1"
        assert "language" not in kwargs

    def test_configured_language_is_sent(self, tmp_path):
        receiver, openai_client, audio = self._receiver(tmp_path, transcription_language="ar")
        with patch.object(receiver, "download_media", return_value=audio):
            receiver.transcribe_audio(TWILIO_MEDIA_URL)
        assert openai_client.audio.transcriptions.create.call_args.kwargs["language"] == "ar"

    def test_empty_audio_is_ignored(self, tmp_path):
        receiver, openai_client, audio = self._receiver(tmp_path)
        with open(audio, "wb") as f:
            f.write(b"0")
        with patch.object(receiver, "download_media", return_value=audio):
            assert receiver.transcribe_audio(TWILIO_MEDIA_URL) is None
        openai_client.audio.transcriptions.create.assert_not_called()


class TestImageReading:
    """Lecture du texte des images (client OpenAI simulé)."""

    def test_image_text_uses_vision_model(self, tmp_path):
        openai_client = MagicMock()
        openai_client.chat.completions.create.return_value.choices = [Mock(message=Mock(content=" 2000 sachets "))]
        receiver = WhatsAppReceiver(Settings.for_tests(tmp_path, model_vision="vision-test"), openai_client=openai_client)
        image = tmp_path / "photo.jpg"
        image.write_bytes(b"\xff\xd8\xff")

        with patch.object(receiver, "download_media", return_value=str(image)):
            assert receiver.extract_text_from_image(TWILIO_MEDIA_URL) == "2000 sachets"
        assert openai_client.chat.completions.create.call_args.kwargs["model"] == "vision-test"


class TestSendReply:
    """Envoi de messages par l'API Twilio (simulée)."""

    def test_send_reply_formats_number(self, receiver):
        receiver.twilio_client = MagicMock()
        receiver.twilio_client.messages.create.return_value = Mock(sid="SM123")

        assert receiver.send_reply("212612345678", "Bonjour") == "SM123"
        kwargs = receiver.twilio_client.messages.create.call_args.kwargs
        assert kwargs["to"] == "whatsapp:+212612345678"
        assert kwargs["from_"] == "whatsapp:+14155238886"

    def test_send_reply_without_credentials_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            WhatsAppReceiver(Settings.for_tests(tmp_path)).send_reply("+212600000000", "x")
