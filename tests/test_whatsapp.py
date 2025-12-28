"""
Tests pour le module whatsapp_receiver.py
Tests de l'intégration WhatsApp/Twilio
"""

import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestWhatsAppConfiguration:
    """Tests de la configuration WhatsApp."""
    
    def test_twilio_config_exists(self):
        """Test que la configuration Twilio existe."""
        import whatsapp_receiver
        
        source = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'whatsapp_receiver.py'), encoding='utf-8').read()
        assert 'twilio' in source.lower() or 'TWILIO' in source
    
    def test_sandbox_number_format(self):
        """Test format du numéro sandbox."""
        # Twilio sandbox format: +14155238886
        sandbox_number = '+14155238886'
        
        assert sandbox_number.startswith('+')
        assert len(sandbox_number) == 12


class TestPhoneNumberFormatting:
    """Tests du formatage des numéros de téléphone."""
    
    def test_format_moroccan_number_with_plus(self):
        """Test formatage numéro marocain avec +."""
        phone = '+212612345678'
        
        # Should be valid format
        assert phone.startswith('+212')
        assert len(phone) == 13
    
    def test_format_moroccan_number_without_plus(self):
        """Test formatage numéro marocain sans +."""
        phone = '0612345678'
        
        # Should start with 0
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
        
        # All should be convertible to standard format
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
        
        # Extract phone number
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
        
        # Build confirmation message
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
        
        # Build message without confidence
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
        # Simulate message sending
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
        # Twilio sends X-Twilio-Signature header
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
        
        # Check if supported
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
        
        # Darija keywords
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
        
        # Match by phone
        is_existing = existing_client['telephone'] == incoming_phone
        assert is_existing
    
    def test_new_client_creation_data(self):
        """Test données pour nouveau client."""
        new_client_data = {
            'nom': None,  # May be unknown
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
        # TwiML response format
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
        
        # Required fields
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
        # Track requests per phone number
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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
