"""
WhatsApp Receiver Module
Uses Twilio API to receive WhatsApp messages (text, images, files, audio).
Includes audio transcription with OpenAI Whisper (supports Darija/Moroccan Arabic).
"""

import os
import sys
import requests
import base64
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from twilio.rest import Client as TwilioClient

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()


class WhatsAppReceiver:
    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.twilio_client = None
        self.media_dir = "whatsapp_media"
        
        # Create media directory
        os.makedirs(self.media_dir, exist_ok=True)
    
    def connect(self):
        """Initialize Twilio client."""
        try:
            self.twilio_client = TwilioClient(self.account_sid, self.auth_token)
            print("✅ Connexion Twilio réussie")
            return True
        except Exception as e:
            print(f"❌ Erreur connexion Twilio: {e}")
            return False
    
    def process_incoming_message(self, message_data):
        """
        Process incoming WhatsApp message from webhook.
        message_data comes from Twilio webhook POST request.
        """
        result = {
            "type": "unknown",
            "from": message_data.get("From", "").replace("whatsapp:", ""),
            "to": message_data.get("To", "").replace("whatsapp:", ""),
            "timestamp": datetime.now().isoformat(),
            "content": None,
            "media_url": None,
            "transcription": None,
            "extracted_text": None
        }
        
        # Get message body (text)
        body = message_data.get("Body", "").strip()
        if body:
            result["type"] = "text"
            result["content"] = body
            print(f"   📝 Message texte: {body[:100]}...")
        
        # Check for media (images, audio, files)
        num_media = int(message_data.get("NumMedia", 0))
        
        if num_media > 0:
            media_url = message_data.get("MediaUrl0")
            media_type = message_data.get("MediaContentType0", "")
            
            result["media_url"] = media_url
            
            # Log media type for debugging
            print(f"   📦 Type média: {media_type}")
            
            if "image" in media_type.lower():
                result["type"] = "image"
                print(f"   🖼️ Image reçue: {media_type}")
                result["extracted_text"] = self.extract_text_from_image(media_url)
                
            elif any(x in media_type.lower() for x in ["audio", "ogg", "opus", "mpeg", "mp3", "wav", "m4a", "voice"]):
                result["type"] = "audio"
                print(f"   🎤 Audio reçu: {media_type}")
                result["transcription"] = self.transcribe_audio(media_url, media_type)
                result["content"] = result["transcription"]
                
            elif "pdf" in media_type.lower() or "document" in media_type.lower():
                result["type"] = "document"
                print(f"   📄 Document reçu: {media_type}")
                result["extracted_text"] = self.extract_text_from_document(media_url, media_type)
            
            else:
                result["type"] = "file"
                print(f"   📎 Fichier reçu: {media_type}")
        
        return result
    
    def download_media(self, media_url, filename=None):
        """Download media from Twilio URL."""
        try:
            # Twilio requires authentication to download media
            response = requests.get(
                media_url,
                auth=(self.account_sid, self.auth_token)
            )
            
            if response.status_code == 200:
                if not filename:
                    # Generate filename from URL
                    ext = media_url.split(".")[-1][:4] if "." in media_url else "bin"
                    filename = f"media_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
                
                filepath = os.path.join(self.media_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(response.content)
                
                print(f"   📥 Téléchargé: {filename}")
                return filepath
            else:
                print(f"   ❌ Erreur téléchargement: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"   ❌ Erreur téléchargement média: {e}")
            return None
    
    def transcribe_audio(self, audio_url, media_type="audio/ogg"):
        """
        Transcribe audio using OpenAI Whisper.
        Supports Darija (Moroccan Arabic) and other languages.
        """
        try:
            # Determine file extension from media type
            ext_map = {
                "audio/ogg": "ogg",
                "audio/mpeg": "mp3",
                "audio/mp4": "m4a",
                "audio/wav": "wav",
                "audio/x-wav": "wav",
                "audio/webm": "webm",
                "audio/aac": "aac",
            }
            ext = "ogg"  # default
            for key, value in ext_map.items():
                if key in media_type.lower():
                    ext = value
                    break
            
            filename = f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
            
            # Download audio file
            print(f"   📥 Téléchargement audio: {filename}")
            filepath = self.download_media(audio_url, filename)
            
            if not filepath:
                print("   ❌ Échec téléchargement audio")
                return None
            
            # Check file size
            file_size = os.path.getsize(filepath)
            print(f"   📊 Taille fichier: {file_size / 1024:.1f} KB")
            
            if file_size < 100:
                print("   ❌ Fichier audio trop petit")
                return None
            
            print("   🎯 Transcription avec Whisper (Darija/Arabe supporté)...")
            
            with open(filepath, "rb") as audio_file:
                # Whisper automatically detects language including Arabic dialects
                transcription = self.openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
            
            if transcription:
                print(f"   ✅ Transcription: {transcription[:100]}...")
            else:
                print("   ⚠️ Transcription vide")
                
            return transcription
            
        except Exception as e:
            print(f"   ❌ Erreur transcription: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def extract_text_from_image(self, image_url):
        """Extract text from image using OpenAI Vision."""
        try:
            # Download image
            filepath = self.download_media(image_url, f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
            
            if not filepath:
                return None
            
            print("   🔍 Extraction texte image avec Vision...")
            
            with open(filepath, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Extrais tout le texte visible dans cette image. Retourne uniquement le texte extrait."
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}
                            }
                        ]
                    }
                ],
                max_tokens=2000
            )
            
            text = response.choices[0].message.content.strip()
            print(f"   ✅ Texte extrait: {text[:100]}...")
            return text
            
        except Exception as e:
            print(f"   ❌ Erreur extraction image: {e}")
            return None
    
    def extract_text_from_document(self, doc_url, media_type):
        """Extract text from PDF or document."""
        try:
            ext = "pdf" if "pdf" in media_type else "doc"
            filepath = self.download_media(doc_url, f"doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}")
            
            if not filepath:
                return None
            
            if ext == "pdf":
                import PyPDF2
                print("   📄 Extraction texte PDF...")
                
                with open(filepath, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    text = ""
                    for page in reader.pages:
                        text += page.extract_text() + "\n"
                
                print(f"   ✅ Texte PDF extrait: {text[:100]}...")
                return text.strip()
            
            return None
            
        except Exception as e:
            print(f"   ❌ Erreur extraction document: {e}")
            return None
    
    def format_for_extraction(self, message_result):
        """Format WhatsApp message data for order extraction."""
        # Create email-like structure for compatibility with existing extractor
        formatted = {
            "subject": f"WhatsApp - {message_result['from']}",
            "from": message_result["from"],
            "date": message_result["timestamp"],
            "body": "",
            "source": "whatsapp"
        }
        
        # Build body from all available content
        parts = []
        
        if message_result.get("content"):
            parts.append(f"Message: {message_result['content']}")
        
        if message_result.get("transcription"):
            parts.append(f"Audio transcription: {message_result['transcription']}")
        
        if message_result.get("extracted_text"):
            parts.append(f"Document/Image content: {message_result['extracted_text']}")
        
        formatted["body"] = "\n\n".join(parts)
        
        return formatted
    
    def send_reply(self, to_number, message):
        """Send a WhatsApp reply."""
        try:
            if not self.twilio_client:
                self.connect()
            
            # Get your Twilio WhatsApp number from environment or use sandbox
            from_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
            
            msg = self.twilio_client.messages.create(
                body=message,
                from_=from_number,
                to=f"whatsapp:{to_number}"
            )
            
            print(f"   📤 Réponse envoyée: {message[:50]}...")
            return msg.sid
            
        except Exception as e:
            print(f"   ❌ Erreur envoi réponse: {e}")
            return None


def test_whatsapp():
    """Test WhatsApp connection."""
    print("=" * 50)
    print("🧪 Test WhatsApp Receiver")
    print("=" * 50)
    
    receiver = WhatsAppReceiver()
    
    if receiver.connect():
        print("✅ Module WhatsApp prêt")
        print(f"   Account SID: {receiver.account_sid[:10]}...")
        print(f"   Media dir: {receiver.media_dir}")
    else:
        print("❌ Échec connexion")


if __name__ == "__main__":
    test_whatsapp()
