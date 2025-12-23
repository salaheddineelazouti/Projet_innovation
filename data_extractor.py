"""
Data Extractor Module
Uses OpenAI API to extract purchase order data from emails and attachments.
Includes smart reorder detection using client history.
"""

import os
import sys
import base64
import json
from dotenv import load_dotenv
from openai import OpenAI
import PyPDF2
from PIL import Image
import io

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

# Product types for the company
PRODUCT_TYPES = [
    "Sachets fond plat",
    "Sac fond carré sans poignées",
    "Sac fond carré avec poignées plates",
    "Sac fond carré avec poignées torsadées"
]

# Reorder patterns in different languages (French, Arabic transliteration, etc.)
REORDER_PATTERNS = [
    "kif dima", "comme d'habitude", "comme toujours", "same as usual", 
    "same as before", "même commande", "relancer", "renouveler",
    "la même chose", "pareil", "habituelle", "comme la dernière fois",
    "bhal dima", "comme avant", "réapprovisionnement"
]

class DataExtractor:
    def __init__(self, db_manager=None):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = "gpt-4o"
        self.db = db_manager
    
    def set_database(self, db_manager):
        """Set database manager for client history lookups."""
        self.db = db_manager
    
    def detect_reorder_intent(self, email_content):
        """Use OpenAI to detect if email is a reorder request and identify client."""
        prompt = f"""Analyse cet email et détermine:
1. Est-ce une demande de RENOUVELLEMENT/RELANCE de commande habituelle? 
   Expressions à détecter: "comme d'habitude", "kif dima", "b7al dima", "même commande", "comme toujours", etc.

2. Quel est le nom de l'entreprise cliente MENTIONNÉE DANS LE CORPS de l'email?
   IMPORTANT: Cherche le nom du CLIENT dans le CONTENU du message, PAS dans la signature de l'expéditeur.
   Exemple: si le message dit "commande chhiwat fes" ou "pour société X", le client est "chhiwat fes" ou "société X".

Email:
{email_content[:1500]}

Réponds en JSON:
{{
    "is_reorder": true/false,
    "reorder_indicators": ["liste des expressions détectées"],
    "client_name": "nom de l'entreprise cliente DANS LE CONTENU (pas l'expéditeur)",
    "confidence": 0-100
}}

JSON uniquement:"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300
            )
            
            result = response.choices[0].message.content.strip()
            if result.startswith("```"):
                result = result.split("```")[1].replace("json", "").strip()
            
            return json.loads(result)
        except Exception as e:
            print(f"   ⚠️ Erreur détection reorder: {e}")
            return {"is_reorder": False, "client_name": None, "confidence": 0}
    
    def normalize_client_name(self, name):
        """Normalize client name for fuzzy matching."""
        if not name:
            return ""
        # Remove accents and special chars, lowercase
        import unicodedata
        normalized = unicodedata.normalize('NFD', name.lower())
        normalized = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
        # Remove common words and punctuation
        normalized = normalized.replace("'", "").replace("-", " ").replace(".", "")
        return normalized.strip()
    
    def find_matching_client(self, search_name):
        """Find best matching client using fuzzy matching."""
        if not self.db or not self.db.connection:
            return None
        
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT id, nom FROM clients")
        clients = cursor.fetchall()
        
        search_normalized = self.normalize_client_name(search_name)
        search_words = set(search_normalized.split())
        
        best_match = None
        best_score = 0
        
        for client in clients:
            client_normalized = self.normalize_client_name(client['nom'])
            client_words = set(client_normalized.split())
            
            # Check word overlap
            common_words = search_words & client_words
            if common_words:
                score = len(common_words) / max(len(search_words), len(client_words))
                
                # Boost score if main word matches
                if any(w in client_normalized for w in search_words if len(w) > 3):
                    score += 0.3
                
                if score > best_score:
                    best_score = score
                    best_match = client
        
        if best_match and best_score > 0.3:
            print(f"   🔍 Client trouvé: '{best_match['nom']}' (score: {best_score:.2f})")
            return best_match['nom']
        
        return None
    
    def get_client_last_order(self, client_name):
        """Get the last validated order for a client from database."""
        if not self.db or not self.db.connection:
            return None
        
        cursor = self.db.connection.cursor()
        
        # Try fuzzy matching first
        matched_name = self.find_matching_client(client_name)
        if matched_name:
            client_name = matched_name
        
        # Find client and their last validated order
        cursor.execute("""
            SELECT c.*, p.type as produit_type, cl.nom as client_nom
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE cl.nom LIKE ?
            AND c.statut = 'validee'
            ORDER BY c.validated_at DESC, c.created_at DESC
            LIMIT 1
        """, (f"%{client_name}%",))
        
        result = cursor.fetchone()
        if result:
            return dict(result)
        
        # If no validated order, get the most recent order
        cursor.execute("""
            SELECT c.*, p.type as produit_type, cl.nom as client_nom
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE cl.nom LIKE ?
            ORDER BY c.created_at DESC
            LIMIT 1
        """, (f"%{client_name}%",))
        
        result = cursor.fetchone()
        return dict(result) if result else None
    
    def fill_from_history(self, extracted_data, last_order):
        """Fill missing fields from client's last order."""
        if not last_order:
            return extracted_data
        
        # Fields to potentially fill from history
        field_mapping = {
            'type_produit': 'produit_type',
            'nature_produit': 'nature_produit',
            'quantite': 'quantite',
            'unite': 'unite',
            'prix_unitaire': 'prix_unitaire',
            'prix_total': 'prix_total',
            'devise': 'devise'
        }
        
        filled_fields = []
        for new_field, old_field in field_mapping.items():
            if not extracted_data.get(new_field) and last_order.get(old_field):
                extracted_data[new_field] = last_order[old_field]
                filled_fields.append(new_field)
        
        if filled_fields:
            extracted_data['filled_from_history'] = True
            extracted_data['history_fields'] = filled_fields
            extracted_data['history_source_order'] = last_order.get('numero_commande') or f"ID-{last_order.get('id')}"
            # Add note about auto-fill
            info = extracted_data.get('informations_supplementaires', '') or ''
            extracted_data['informations_supplementaires'] = f"[AUTO-REMPLI depuis historique: {', '.join(filled_fields)}] {info}"
            # Boost confidence since we used historical data
            if extracted_data.get('confiance', 0) < 85:
                extracted_data['confiance'] = 85
        
        return extracted_data
    
    def extract_from_email(self, email_data, attachment_texts=None):
        """Extract purchase order data from email content and attachments."""
        
        # Build context from email
        email_content = f"""
SUJET: {email_data.get('subject', '')}
DE: {email_data.get('from', '')}
DATE: {email_data.get('date', '')}

CONTENU:
{email_data.get('body', '')}
"""
        
        # Add attachment content if available
        if attachment_texts:
            email_content += "\n\nCONTENU DES PIÈCES JOINTES:\n"
            for filename, text in attachment_texts.items():
                email_content += f"\n--- {filename} ---\n{text}\n"
        
        # Step 1: Detect if this is a reorder request
        reorder_info = self.detect_reorder_intent(email_content)
        
        if reorder_info.get('is_reorder') and reorder_info.get('client_name'):
            print(f"   🔄 RELANCE détectée pour: {reorder_info['client_name']}")
            print(f"   📝 Indicateurs: {reorder_info.get('reorder_indicators', [])}")
            
            # Step 2: Get client's last order from history
            last_order = self.get_client_last_order(reorder_info['client_name'])
            
            if last_order:
                print(f"   📦 Dernière commande trouvée: {last_order.get('numero_commande') or 'ID-' + str(last_order.get('id'))}")
                print(f"      - Produit: {last_order.get('produit_type')}")
                print(f"      - Quantité: {last_order.get('quantite')} {last_order.get('unite', '')}")
                
                # Step 3: Extract basic data then fill from history
                extracted = self._extract_with_openai(email_content)
                
                if extracted:
                    extracted['entreprise_cliente'] = reorder_info['client_name']
                    extracted = self.fill_from_history(extracted, last_order)
                    # Force as valid order since we have history
                    extracted['est_bon_commande'] = True
                    extracted['is_reorder'] = True
                    print(f"   ✅ Commande auto-remplie depuis l'historique!")
                    return extracted
            else:
                print(f"   ⚠️ Pas d'historique trouvé pour {reorder_info['client_name']}")
        
        # Standard extraction
        return self._extract_with_openai(email_content)
    
    def _extract_with_openai(self, content):
        """Use OpenAI to extract structured data from content."""
        
        prompt = f"""Tu es un assistant spécialisé dans l'extraction de données de bons de commande.

L'entreprise fabrique 4 types de produits d'emballage:
1. Sachets fond plat
2. Sac fond carré sans poignées
3. Sac fond carré avec poignées plates
4. Sac fond carré avec poignées torsadées

Analyse le contenu suivant et extrais les informations du bon de commande.
Retourne les données au format JSON avec les champs suivants:
- numero_commande: string (numéro du bon de commande)
- entreprise_cliente: string (nom de l'entreprise qui passe la commande)
- type_produit: string (un des 4 types listés ci-dessus, ou null si non identifiable)
- nature_produit: string (détails spécifiques du produit)
- quantite: number (quantité commandée)
- unite: string (unité de mesure: pièces, kg, etc.)
- date_commande: string (date du bon de commande)
- date_livraison: string (date de livraison souhaitée, si mentionnée)
- prix_unitaire: number (prix unitaire si mentionné)
- prix_total: number (prix total si mentionné)
- devise: string (EUR, MAD, USD, etc.)
- informations_supplementaires: string (autres informations pertinentes)
- confiance: number (niveau de confiance de 0 à 100)
- est_bon_commande: boolean (true si c'est bien un bon de commande)

Si une information n'est pas trouvée, utilise null.

CONTENU À ANALYSER:
{content}

Réponds UNIQUEMENT avec le JSON, sans texte additionnel."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Tu es un expert en extraction de données de documents commerciaux. Tu réponds uniquement en JSON valide."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1000
            )
            
            result = response.choices[0].message.content.strip()
            
            # Clean JSON if wrapped in markdown
            if result.startswith("```"):
                result = result.split("```")[1]
                if result.startswith("json"):
                    result = result[4:]
                result = result.strip()
            
            import json
            return json.loads(result)
            
        except Exception as e:
            print(f"❌ Erreur OpenAI: {e}")
            return None
    
    def extract_text_from_pdf(self, pdf_path):
        """Extract text content from a PDF file."""
        try:
            text = ""
            with open(pdf_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            return text.strip()
        except Exception as e:
            print(f"❌ Erreur extraction PDF: {e}")
            return ""
    
    def extract_text_from_image(self, image_path):
        """Use OpenAI Vision to extract text from an image."""
        try:
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            
            # Determine image type
            ext = os.path.splitext(image_path)[1].lower()
            mime_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp"
            }.get(ext, "image/jpeg")
            
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Extrais tout le texte visible dans cette image. Retourne uniquement le texte extrait, sans commentaire."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_data}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=2000
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"❌ Erreur extraction image: {e}")
            return ""
    
    def process_attachment(self, filepath):
        """Process an attachment and extract text based on file type."""
        ext = os.path.splitext(filepath)[1].lower()
        
        if ext == ".pdf":
            print(f"   📄 Extraction PDF: {os.path.basename(filepath)}")
            return self.extract_text_from_pdf(filepath)
        
        elif ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
            print(f"   🖼️ Extraction image: {os.path.basename(filepath)}")
            return self.extract_text_from_image(filepath)
        
        elif ext in [".txt", ".csv"]:
            print(f"   📝 Lecture fichier texte: {os.path.basename(filepath)}")
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return f.read()
            except:
                return ""
        
        else:
            print(f"   ⚠️ Type de fichier non supporté: {ext}")
            return ""
    
    def process_attachments(self, filepaths):
        """Process multiple attachments and return extracted text."""
        results = {}
        for filepath in filepaths:
            filename = os.path.basename(filepath)
            text = self.process_attachment(filepath)
            if text:
                results[filename] = text
        return results


def test_extractor():
    """Test the data extractor with sample data."""
    print("=" * 50)
    print("🧪 Test de l'extracteur de données")
    print("=" * 50)
    
    extractor = DataExtractor()
    
    # Test with sample email data
    sample_email = {
        "subject": "Commande N° BC-2024-0156 - Sachets kraft",
        "from": "achats@entreprise-client.ma",
        "date": "2024-12-23",
        "body": """Bonjour,

Veuillez trouver ci-dessous notre bon de commande:

Numéro de commande: BC-2024-0156
Entreprise: SARL Les Délices du Maroc
Date: 23/12/2024

Produit: Sachets fond plat kraft naturel
Dimensions: 15x25 cm
Quantité: 10 000 pièces
Prix unitaire: 0.15 MAD
Total: 1 500 MAD HT

Livraison souhaitée: 15/01/2025

Cordialement,
Service Achats
"""
    }
    
    print("\n📧 Email de test:")
    print(f"   Sujet: {sample_email['subject']}")
    print(f"   De: {sample_email['from']}")
    
    print("\n🔍 Extraction en cours...")
    result = extractor.extract_from_email(sample_email)
    
    if result:
        print("\n✅ Données extraites:")
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("❌ Échec de l'extraction")
    
    print("\n" + "=" * 50)


if __name__ == "__main__":
    test_extractor()
