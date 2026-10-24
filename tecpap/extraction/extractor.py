"""Extraction des commandes à partir des messages (emails et WhatsApp) avec un modèle de langue.

Étapes :
1. le texte du message et celui des pièces jointes sont réunis ; les pièces jointes et les
   médias sont convertis en texte par :class:`~tecpap.processing.multimodal.MediaProcessor` ;
2. un modèle léger (``Settings.model_light``) détecte une demande de renouvellement
   (« comme d'habitude », « kif dima ») et le nom du client cité dans le message ;
3. pour un renouvellement, la dernière commande du client est cherchée en base par des
   requêtes SQL fixes et paramétrées : le modèle ne reçoit jamais d'accès libre à la base ;
4. le modèle d'extraction (``Settings.model_extraction``, température basse) renvoie la
   commande au format JSON ; les champs manquants d'un renouvellement sont complétés depuis
   l'historique ;
5. un code article SAGE X3 est calculé par des règles fixes s'il n'a pas été trouvé.

Le message est transmis au modèle dans un bloc délimité, présenté comme des données et jamais
comme des instructions (voir :mod:`tecpap.extraction.prompts`). La confiance indiquée est celle
déclarée par le modèle ; la commande extraite reste « en attente » jusqu'à la validation par
l'équipe commerciale.
"""

from __future__ import annotations

import logging
import os
import unicodedata
from typing import Any

from tecpap.catalog import ALL_REORDER_PATTERNS, COMPANY_INFO, PRODUCT_TYPES
from tecpap.config import Settings, load_settings
from tecpap.extraction.article_codes import generate_article_code, suggest_article_code_from_description
from tecpap.extraction.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    REORDER_SYSTEM_PROMPT,
    build_extraction_prompt,
    build_reorder_prompt,
)
from tecpap.llm.client import LLMClient
from tecpap.processing.multimodal import MediaProcessor

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

# Score minimal de recouvrement des mots pour rapprocher un nom cité d'un client connu.
CLIENT_MATCH_THRESHOLD = 0.3

TRUE_WORDS = frozenset({"true", "oui", "yes", "1"})


def _as_bool(value: Any) -> bool:
    """Interprète un booléen renvoyé par le modèle (``true``, ``"true"``, ``"oui"``…)."""
    if isinstance(value, bool):
        return value
    return value is not None and str(value).strip().lower() in TRUE_WORDS


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


def build_message_content(email_data: dict[str, Any], attachment_texts: dict[str, str] | None = None) -> str:
    """Réunit l'en-tête, le corps du message et le texte des pièces jointes en un seul texte."""
    content = f"""
SUJET: {email_data.get('subject', '')}
DE: {email_data.get('from', '')}
DATE: {email_data.get('date', '')}

CONTENU:
{email_data.get('body', '')}
"""
    if attachment_texts:
        content += "\n\nCONTENU DES PIÈCES JOINTES:\n"
        for filename, text in attachment_texts.items():
            content += f"\n--- {filename} ---\n{text}\n"
    return content


class DataExtractor:
    """Extraction des données de commande avec un modèle de langue.

    Args:
        db_manager: base des commandes, utilisée pour retrouver l'historique d'un client.
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
        llm: client des modèles de langue ; par défaut construit depuis la configuration
            (:meth:`LLMClient.from_settings`). Sans clé d'API, l'extraction renvoie ``None``.
    """

    def __init__(self, db_manager: Any = None, settings: Settings | None = None, llm: LLMClient | None = None):
        self.settings = settings or load_settings()
        self.llm = llm if llm is not None else LLMClient.from_settings(self.settings)
        self.media = MediaProcessor(self.llm, self.settings)
        self.model = self.settings.model_extraction
        self.db = db_manager

    def set_database(self, db_manager: Any) -> None:
        """Définit la base utilisée pour l'historique des clients."""
        self.db = db_manager

    # ------------------------------------------------------------------ renouvellement

    def detect_reorder_intent(self, email_content: str) -> dict[str, Any]:
        """Demande au modèle léger si le message est un renouvellement et quel client il cite.

        Returns:
            ``is_reorder`` (booléen), ``client_name`` (texte non vide ou ``None``),
            ``confidence`` et, quand le modèle les fournit, les expressions détectées
            (``reorder_indicators``).
        """
        default: dict[str, Any] = {"is_reorder": False, "client_name": None, "confidence": 0}
        if not self.llm.available:
            logger.warning("Détection de renouvellement impossible : clé OPENAI_API_KEY absente")
            return default

        result = self.llm.chat_json(
            model=self.settings.model_light,
            system=REORDER_SYSTEM_PROMPT,
            user=build_reorder_prompt(email_content),
            temperature=self.settings.llm_temperature,
            max_tokens=300,
        )
        if result is None:
            logger.warning("Détection de renouvellement en échec : réponse du modèle absente ou illisible")
            return default

        # La réponse du modèle est contrôlée avant usage : booléen attendu, nom de client non vide.
        client_name = result.get("client_name")
        result["is_reorder"] = _as_bool(result.get("is_reorder"))
        result["client_name"] = client_name.strip() if isinstance(client_name, str) and client_name.strip() else None
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
        """Complète les champs vides de la commande avec ceux de la dernière commande du client.

        Seuls les champs absents de l'extraction sont complétés ; la confiance déclarée par le
        modèle est conservée telle quelle.
        """
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

        return extracted_data

    # ------------------------------------------------------------------ extraction

    def extract_from_email(self, email_data: dict[str, Any],
                           attachment_texts: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Extrait une commande d'un message (email, ou message WhatsApp présenté comme un email).

        Args:
            email_data: ``subject``, ``from``, ``date`` et ``body`` du message.
            attachment_texts: texte de chaque pièce jointe, par nom de fichier.

        Returns:
            Les champs de la commande (dont ``est_bon_commande``), ou ``None`` en cas d'échec
            ou sans clé d'API.
        """
        if not self.llm.available:
            logger.warning("Extraction impossible : clé OPENAI_API_KEY absente")
            return None

        email_content = build_message_content(email_data, attachment_texts)

        reorder_info = self.detect_reorder_intent(email_content)
        if reorder_info.get("is_reorder") and reorder_info.get("client_name"):
            reordered = self._extract_reorder(email_content, reorder_info)
            if reordered:
                return reordered

        return self._extract_with_llm(email_content)

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
        extracted = self._extract_with_llm(email_content)
        if not extracted:
            return None

        # Le nom exact enregistré en base remplace le nom approximatif cité dans le message.
        extracted["entreprise_cliente"] = last_order.get("client_nom") or client_name
        extracted = self.fill_from_history(extracted, last_order)
        extracted["est_bon_commande"] = True
        extracted["is_reorder"] = True
        return extracted

    def _extract_with_llm(self, content: str) -> dict[str, Any] | None:
        """Envoie le contenu au modèle d'extraction et renvoie la commande décodée du JSON."""
        extracted_data = self.llm.chat_json(
            model=self.model,
            system=EXTRACTION_SYSTEM_PROMPT,
            user=build_extraction_prompt(content),
            temperature=self.settings.llm_temperature,
            max_tokens=1000,
        )
        if extracted_data is None:
            logger.error("Extraction en échec : réponse du modèle absente ou illisible")
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
        return self.media.pdf_to_text(pdf_path).text or ""

    def extract_text_from_image(self, image_path: str) -> str:
        """Lit le texte d'une image avec le modèle de vision, ou renvoie une chaîne vide."""
        return self.media.image_to_text(image_path).text or ""

    def process_attachment(self, filepath: str) -> str:
        """Extrait le texte d'une pièce jointe (PDF, image, audio, texte), ou renvoie une chaîne vide.

        Le type est déterminé d'après le contenu du fichier quand c'est possible (voir
        :class:`~tecpap.processing.multimodal.MediaProcessor`).
        """
        result = self.media.process_file(filepath)
        if result.error:
            logger.info("Pièce jointe %s ignorée : %s", os.path.basename(filepath), result.error)
            return ""
        return result.text or ""

    def process_attachments(self, filepaths: list[str]) -> dict[str, str]:
        """Extrait le texte de plusieurs pièces jointes ; renvoie le texte non vide par nom de fichier."""
        results = {}
        for filepath in filepaths:
            text = self.process_attachment(filepath)
            if text:
                results[os.path.basename(filepath)] = text
        return results
