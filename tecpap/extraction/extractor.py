"""Extraction des commandes à partir des messages (emails et WhatsApp) avec un modèle de langue.

Étapes :
1. le texte du message et celui des pièces jointes sont réunis ;
2. un modèle léger (``Settings.model_light``) détecte une demande de renouvellement
   (« comme d'habitude », « kif dima ») et le nom du client cité dans le message ;
3. pour un renouvellement, la dernière commande du client est cherchée en base par des
   requêtes SQL fixes et paramétrées : le modèle ne reçoit jamais d'accès libre à la base ;
4. le modèle d'extraction (``Settings.model_extraction``, température basse) renvoie la
   commande au format JSON ; les champs manquants d'un renouvellement sont complétés depuis
   l'historique ;
5. un code article SAGE X3 est calculé par des règles fixes s'il n'a pas été trouvé.

La commande extraite reste « en attente » jusqu'à la validation par l'équipe commerciale.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import unicodedata
from typing import Any

import pypdf
from openai import OpenAI

from tecpap.catalog import ALL_REORDER_PATTERNS, COMPANY_INFO, PRODUCT_TYPES, PRODUCTS
from tecpap.config import Settings, load_settings
from tecpap.extraction.article_codes import generate_article_code, suggest_article_code_from_description

logger = logging.getLogger(__name__)

__all__ = [
    "COMPANY_INFO",
    "PRODUCT_TYPES",
    "REORDER_PATTERNS",
    "DataExtractor",
    "auto_generate_article_code",
]

REORDER_PATTERNS: list[str] = list(ALL_REORDER_PATTERNS)
"""Expressions de renouvellement, toutes langues confondues (voir ``tecpap.catalog``)."""

IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

TEXT_EXTENSIONS = (".txt", ".csv")

# Champs complétés depuis la dernière commande du client lors d'un renouvellement :
# champ de la commande extraite -> colonne de la commande en base.
HISTORY_FIELDS = {
    "type_produit": "produit_type",
    "nature_produit": "nature_produit",
    "quantite": "quantite",
    "unite": "unite",
    "prix_unitaire": "prix_unitaire",
    "prix_total": "prix_total",
    "devise": "devise",
}

# Confiance minimale attribuée à une commande complétée depuis l'historique du client.
HISTORY_CONFIDENCE = 85

# Score minimal de recouvrement des mots pour rapprocher un nom cité d'un client connu.
CLIENT_MATCH_THRESHOLD = 0.3

EXTRACTION_SYSTEM_PROMPT = (
    "Tu es un expert en extraction de données de documents commerciaux au Maroc. Tu comprends le français, "
    "l'arabe standard et la darija marocaine. Tu réponds uniquement en JSON valide."
)

IMAGE_TEXT_INSTRUCTION = "Extrais tout le texte visible dans cette image. Retourne uniquement le texte extrait, sans commentaire."


def _product_lines() -> str:
    """Liste numérotée des produits du catalogue, insérée dans l'instruction d'extraction."""
    return "\n".join(
        f"{index}. {product['type']} - {', '.join(product['usages'])} (code: {product['code']})"
        for index, product in enumerate(PRODUCTS, start=1)
    )


def _strip_code_fence(text: str) -> str:
    """Retire un éventuel bloc de code Markdown (```json … ```) autour d'une réponse JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def auto_generate_article_code(extracted_data: dict[str, Any]) -> str | None:
    """Calcule un code article quand l'extraction n'en a pas trouvé.

    Le code est d'abord déduit de la désignation et du type de papier, puis, à défaut, des
    champs type de papier, grammage et laize.
    """
    if extracted_data.get("code_article"):
        return extracted_data["code_article"]

    description = (extracted_data.get("nature_produit") or "") + " " + (extracted_data.get("type_papier") or "")
    suggested = suggest_article_code_from_description(description)
    if suggested and len(suggested) > 2:
        return suggested

    code = generate_article_code(
        paper_type=extracted_data.get("type_papier"),
        grammage=extracted_data.get("grammage"),
        laize=extracted_data.get("laize"),
        supplier=None,
    )
    if code and len(code) > 2:
        return code
    return None


class DataExtractor:
    """Extraction des données de commande avec l'API OpenAI.

    Args:
        db_manager: base des commandes, utilisée pour retrouver l'historique d'un client.
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
        client: client OpenAI déjà construit (utile pour les tests). Par défaut, un client est
            créé seulement si ``OPENAI_API_KEY`` est défini ; sans client, l'extraction renvoie
            ``None``.
    """

    def __init__(self, db_manager: Any = None, settings: Settings | None = None, client: Any = None):
        self.settings = settings or load_settings()
        if client is not None:
            self.client = client
        elif self.settings.openai_api_key:
            self.client = OpenAI(api_key=self.settings.openai_api_key, timeout=self.settings.llm_timeout)
        else:
            self.client = None
        self.model = self.settings.model_extraction
        self.db = db_manager

    def set_database(self, db_manager: Any) -> None:
        """Définit la base utilisée pour l'historique des clients."""
        self.db = db_manager

    # ------------------------------------------------------------------ renouvellement

    def detect_reorder_intent(self, email_content: str) -> dict[str, Any]:
        """Demande au modèle léger si le message est un renouvellement et quel client il cite."""
        default = {"is_reorder": False, "client_name": None, "confidence": 0}
        if not self.client:
            logger.warning("Détection de renouvellement impossible : OPENAI_API_KEY absent")
            return default

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
                model=self.settings.model_light,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.settings.llm_temperature,
                max_tokens=300,
            )
            result = json.loads(_strip_code_fence(response.choices[0].message.content or ""))
        except Exception:
            logger.warning("Détection de renouvellement en échec", exc_info=True)
            return default
        if not isinstance(result, dict):
            return default
        return result

    @staticmethod
    def normalize_client_name(name: str | None) -> str:
        """Met un nom en minuscules, sans accents ni ponctuation, pour la comparaison approchée."""
        if not name:
            return ""
        normalized = unicodedata.normalize("NFD", name.lower())
        normalized = "".join(c for c in normalized if unicodedata.category(c) != "Mn")
        normalized = normalized.replace("'", "").replace("-", " ").replace(".", "")
        return normalized.strip()

    def find_matching_client(self, search_name: str) -> str | None:
        """Renvoie le nom du client connu le plus proche de ``search_name``, ou ``None``.

        Le score est la part de mots communs (rapportée au nom le plus long), augmentée de 0,3
        quand un mot de plus de trois lettres du nom cherché figure dans le nom du client.
        """
        if not self.db or not self.db.connection:
            return None

        cursor = self.db.connection.cursor()
        cursor.execute("SELECT id, nom FROM clients")
        clients = cursor.fetchall()

        search_normalized = self.normalize_client_name(search_name)
        search_words = set(search_normalized.split())

        best_match = None
        best_score = 0.0
        for client in clients:
            client_normalized = self.normalize_client_name(client["nom"])
            client_words = set(client_normalized.split())
            common_words = search_words & client_words
            if not common_words:
                continue
            score = len(common_words) / max(len(search_words), len(client_words))
            if any(word in client_normalized for word in search_words if len(word) > 3):
                score += 0.3
            if score > best_score:
                best_score = score
                best_match = client

        if best_match and best_score > CLIENT_MATCH_THRESHOLD:
            logger.info("Client rapproché : %s (score %.2f)", best_match["nom"], best_score)
            return best_match["nom"]
        return None

    def get_client_last_order(self, client_name: str) -> dict[str, Any] | None:
        """Renvoie la dernière commande validée du client, à défaut sa dernière commande."""
        if not self.db or not self.db.connection:
            return None

        matched_name = self.find_matching_client(client_name)
        if matched_name:
            client_name = matched_name

        cursor = self.db.connection.cursor()
        base_query = """
            SELECT c.*, p.type as produit_type, cl.nom as client_nom
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE cl.nom LIKE ?
        """
        cursor.execute(
            base_query + " AND c.statut = 'validee' ORDER BY c.validated_at DESC, c.created_at DESC LIMIT 1",
            (f"%{client_name}%",),
        )
        result = cursor.fetchone()
        if result:
            return dict(result)

        cursor.execute(base_query + " ORDER BY c.created_at DESC LIMIT 1", (f"%{client_name}%",))
        result = cursor.fetchone()
        return dict(result) if result else None

    def fill_from_history(self, extracted_data: dict[str, Any], last_order: dict[str, Any] | None) -> dict[str, Any]:
        """Complète les champs vides de la commande avec ceux de la dernière commande du client."""
        if not last_order:
            return extracted_data

        filled_fields = []
        for new_field, old_field in HISTORY_FIELDS.items():
            if not extracted_data.get(new_field) and last_order.get(old_field):
                extracted_data[new_field] = last_order[old_field]
                filled_fields.append(new_field)

        if filled_fields:
            extracted_data["filled_from_history"] = True
            extracted_data["history_fields"] = filled_fields
            extracted_data["history_source_order"] = (
                last_order.get("numero_commande") or f"ID-{last_order.get('id')}"
            )
            info = extracted_data.get("informations_supplementaires", "") or ""
            extracted_data["informations_supplementaires"] = (
                f"[AUTO-REMPLI depuis historique: {', '.join(filled_fields)}] {info}"
            )
            if (extracted_data.get("confiance") or 0) < HISTORY_CONFIDENCE:
                extracted_data["confiance"] = HISTORY_CONFIDENCE

        return extracted_data

    # ------------------------------------------------------------------ extraction

    def extract_from_email(self, email_data: dict[str, Any],
                           attachment_texts: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Extrait une commande d'un message (email, ou message WhatsApp présenté comme un email).

        Args:
            email_data: ``subject``, ``from``, ``date`` et ``body`` du message.
            attachment_texts: texte de chaque pièce jointe, par nom de fichier.

        Returns:
            Les champs de la commande (dont ``est_bon_commande``), ou ``None`` en cas d'échec.
        """
        email_content = f"""
SUJET: {email_data.get('subject', '')}
DE: {email_data.get('from', '')}
DATE: {email_data.get('date', '')}

CONTENU:
{email_data.get('body', '')}
"""
        if attachment_texts:
            email_content += "\n\nCONTENU DES PIÈCES JOINTES:\n"
            for filename, text in attachment_texts.items():
                email_content += f"\n--- {filename} ---\n{text}\n"

        reorder_info = self.detect_reorder_intent(email_content)
        if reorder_info.get("is_reorder") and reorder_info.get("client_name"):
            reordered = self._extract_reorder(email_content, reorder_info)
            if reordered:
                return reordered

        return self._extract_with_openai(email_content)

    def _extract_reorder(self, email_content: str, reorder_info: dict[str, Any]) -> dict[str, Any] | None:
        """Extraction d'un renouvellement : la commande est complétée depuis l'historique du client."""
        client_name = reorder_info["client_name"]
        logger.info("Renouvellement détecté pour %s (indices : %s)", client_name,
                    reorder_info.get("reorder_indicators", []))

        last_order = self.get_client_last_order(client_name)
        if not last_order:
            logger.info("Aucun historique pour %s : extraction standard", client_name)
            return None

        logger.info("Dernière commande retenue : %s",
                    last_order.get("numero_commande") or f"ID-{last_order.get('id')}")
        extracted = self._extract_with_openai(email_content)
        if not extracted:
            return None

        # Le nom exact enregistré en base remplace le nom approximatif cité dans le message.
        extracted["entreprise_cliente"] = last_order.get("client_nom") or client_name
        extracted = self.fill_from_history(extracted, last_order)
        extracted["est_bon_commande"] = True
        extracted["is_reorder"] = True
        return extracted

    def _build_extraction_prompt(self, content: str) -> str:
        return f"""Tu es un assistant spécialisé dans l'extraction de données de bons de commande pour TECPAP (fabrication de sacs en papier Kraft au Maroc).
Tu comprends le français, l'arabe et la darija marocaine (dialecte marocain).

IMPORTANT - Vocabulaire Darija/Arabe pour commandes:
- "bghit" / "بغيت" = je veux
- "khassni" / "خصني" = j'ai besoin de
- "3tini" / "عطيني" = donne-moi
- "sachet" / "ساشي" / "ساشة" = sachets
- "carton" / "كرتون" / "قرطون" = carton
- "kraft" = papier kraft
- "sandwich" / "سندويش" = sandwich
- "tacos" / "طاكوس" = tacos
- "pièces" / "قطعة" = pièces
- "ana" / "أنا" = je suis (introduction du client)
- "restaurant" / "ريستوران" = restaurant
- "snack" / "سناك" = snack
- "café" / "قهوة" = café

IMPORTANT - Identification du client:
Le NOM DU CLIENT est la personne/entreprise QUI PASSE ou POUR QUI la commande est faite.
Patterns à reconnaître:
- "Commande pour [CLIENT]" → entreprise_cliente = CLIENT
- "pour [CLIENT]" au début → entreprise_cliente = CLIENT
- "ana [nom]" / "أنا [nom]" → entreprise_cliente = nom
- "je suis [nom]" / "c'est [nom]" → entreprise_cliente = nom
- "de la part de [nom]" → entreprise_cliente = nom

L'entreprise fabrique {len(PRODUCTS)} types de produits d'emballage (sacs en papier Kraft):
{_product_lines()}

CODES ARTICLES TECPAP (format: TYPE+GRAMMAGE+LAIZE+PAPIER):
- KB = Kraft Blanchi
- KE = Kraft Écru/Naturel
- Format: KB100L28MON = Kraft Blanchi 100g Laize 28 MONDI

Analyse le contenu suivant et extrais les informations du bon de commande.
MÊME si le message est informel ou en darija, essaie d'identifier s'il s'agit d'une demande de commande.

Retourne les données au format JSON avec les champs suivants (compatible SAGE X3):
- numero_commande: string (numéro du bon de commande, peut être null)
- ligne_commande: number (ligne de commande, défaut 1)
- site_vente: string (site de vente, défaut "SXP")
- code_client: string (code client format CLxxxxx, peut être null)
- entreprise_cliente: string (NOM DU CLIENT/RAISON SOCIALE - TRÈS IMPORTANT!)
- code_article: string (code article TECPAP si détectable, ex: KB100L28MON)
- type_produit: string (un des {len(PRODUCTS)} types listés ci-dessus, déduis le type approprié)
- nature_produit: string (désignation complète du produit)
- quantite: number (quantité commandée)
- unite: string (unité de mesure: US, pièces, kg, etc. défaut US)
- date_commande: string (date du bon de commande format YYYY-MM-DD)
- date_livraison: string (date de livraison souhaitée format YYYY-MM-DD)
- commercial: string (nom du commercial si mentionné, défaut "DIVERS")
- type_sac: string (type de sac: KRAFT, PAPIER, etc.)
- format_sac: string (dimensions/format LAR.PRE.LON si mentionné)
- type_papier: string (type de papier: kraft blanchi, kraft écru, etc.)
- grammage: number (grammage du papier en g/m² si mentionné: 60, 70, 80, 100, etc.)
- laize: number (laize/largeur en cm si mentionné)
- impression_client: string (type d'impression si mentionné)
- prix_unitaire: number (prix unitaire si mentionné)
- prix_total: number (prix total si mentionné)
- devise: string (EUR, MAD, USD, etc. - défaut MAD au Maroc)
- informations_supplementaires: string (autres informations pertinentes)
- confiance: number (niveau de confiance de 0 à 100)
- est_bon_commande: boolean (true si c'est une demande de produits/commande, même informelle)

RÈGLE IMPORTANTE: Si quelqu'un demande des sachets, sacs, ou emballages avec une quantité, c'est une commande (est_bon_commande: true).

Si une information n'est pas trouvée, utilise null.

CONTENU À ANALYSER:
{content}

Réponds UNIQUEMENT avec le JSON, sans texte additionnel."""

    def _extract_with_openai(self, content: str) -> dict[str, Any] | None:
        """Envoie le contenu au modèle d'extraction et renvoie la commande décodée du JSON."""
        if not self.client:
            logger.warning("Extraction impossible : OPENAI_API_KEY absent")
            return None

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": self._build_extraction_prompt(content)},
                ],
                temperature=self.settings.llm_temperature,
                max_tokens=1000,
            )
            extracted_data = json.loads(_strip_code_fence(response.choices[0].message.content or ""))
        except json.JSONDecodeError:
            logger.error("Réponse du modèle illisible : JSON invalide")
            return None
        except Exception:
            logger.exception("Échec de l'appel au modèle d'extraction")
            return None

        if not isinstance(extracted_data, dict):
            logger.error("Réponse du modèle inattendue : objet JSON attendu")
            return None

        if not extracted_data.get("code_article"):
            auto_code = auto_generate_article_code(extracted_data)
            if auto_code:
                extracted_data["code_article"] = auto_code
                logger.info("Code article calculé : %s", auto_code)

        return extracted_data

    # ------------------------------------------------------------------ pièces jointes

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Renvoie le texte d'un PDF (pypdf), ou une chaîne vide si le fichier est illisible."""
        try:
            reader = pypdf.PdfReader(pdf_path)
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception:
            logger.exception("Lecture du PDF impossible : %s", pdf_path)
            return ""

    def extract_text_from_image(self, image_path: str) -> str:
        """Lit le texte d'une image avec le modèle de vision, ou renvoie une chaîne vide."""
        if not self.client:
            logger.warning("Lecture d'image impossible : OPENAI_API_KEY absent")
            return ""
        try:
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            mime_type = IMAGE_EXTENSIONS.get(os.path.splitext(image_path)[1].lower(), "image/jpeg")

            response = self.client.chat.completions.create(
                model=self.settings.model_vision,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": IMAGE_TEXT_INSTRUCTION},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
                    ],
                }],
                max_tokens=2000,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("Lecture du texte de l'image impossible : %s", image_path)
            return ""

    def process_attachment(self, filepath: str) -> str:
        """Extrait le texte d'une pièce jointe selon son extension (PDF, image, texte)."""
        ext = os.path.splitext(filepath)[1].lower()
        name = os.path.basename(filepath)

        if ext == ".pdf":
            logger.info("Extraction du PDF %s", name)
            return self.extract_text_from_pdf(filepath)
        if ext in IMAGE_EXTENSIONS:
            logger.info("Lecture de l'image %s", name)
            return self.extract_text_from_image(filepath)
        if ext in TEXT_EXTENSIONS:
            logger.info("Lecture du fichier texte %s", name)
            try:
                with open(filepath, encoding="utf-8") as f:
                    return f.read()
            except (OSError, UnicodeDecodeError):
                logger.warning("Fichier texte illisible : %s", name)
                return ""

        logger.info("Type de pièce jointe non pris en charge : %s", ext)
        return ""

    def process_attachments(self, filepaths: list[str]) -> dict[str, str]:
        """Extrait le texte de plusieurs pièces jointes ; renvoie le texte non vide par nom de fichier."""
        results = {}
        for filepath in filepaths:
            text = self.process_attachment(filepath)
            if text:
                results[os.path.basename(filepath)] = text
        return results
