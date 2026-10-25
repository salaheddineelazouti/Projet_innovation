"""Extraction des commandes à partir des messages (emails et WhatsApp) avec un modèle de langue.

Le modèle de langue n'est jamais la seule source de vérité : sa réponse est validée par le schéma
(:mod:`tecpap.extraction.schema`), contrôlée et complétée par des règles fixes
(:mod:`tecpap.extraction.rules`), et chaque commande reste « en attente » jusqu'à la validation
par l'équipe commerciale.

Étapes (:meth:`DataExtractor.extract_text`, :meth:`DataExtractor.extract_from_email`) :

1. le texte est nettoyé (:func:`tecpap.security.guardrails.sanitize_text` : forme NFKC,
   caractères de contrôle et invisibles retirés) ; pour un email, le sujet, le corps et le texte
   des pièces jointes sont d'abord réunis, les pièces jointes et les médias étant convertis en
   texte par :class:`~tecpap.processing.multimodal.MediaProcessor` ;
2. tout le contenu est contrôlé par :func:`tecpap.security.guardrails.inspect_input` : un contenu
   qui ressemble à une tentative d'injection d'instructions (texte rédigé pour détourner le
   modèle de sa tâche) est signalé par une anomalie et un événement de sécurité, mais la
   commande n'est jamais écartée : l'équipe commerciale en décide ;
3. la langue est détectée (:func:`tecpap.nlp.language.detect_language`) et le modèle choisi par
   :class:`tecpap.llm.router.ModelRouter` (tâches ``extraction`` et ``reorder_detection``) ;
4. l'instruction d'extraction (:mod:`tecpap.extraction.prompts`) contient le message dans un
   bloc délimité et jusqu'à trois commandes validées proches, anonymisées et sans prix
   (:class:`tecpap.rag.retriever.OrderExampleRetriever`) ; la réponse JSON du modèle passe par
   :func:`tecpap.extraction.schema.parse_llm_order` ;
5. renouvellement (« comme d'habitude », « kif dima ») : les règles
   (:func:`tecpap.extraction.rules.detect_reorder`) décident d'abord ; le modèle léger n'est
   consulté que si elles ne trouvent rien dans un texte assez long pour être ambigu
   (:data:`REORDER_MODEL_MIN_CHARS`) ;
6. pour un renouvellement, la dernière commande validée envoyée par le même expéditeur (même
   adresse email, ou même numéro WhatsApp) est cherchée en base par des requêtes SQL fixes et
   paramétrées (le modèle ne reçoit jamais d'accès libre à la base), et seuls les champs
   manquants sont complétés depuis cette commande (voir :meth:`DataExtractor.fill_from_history`).
   Le nom d'un client cité dans le message ne donne jamais accès à l'historique de ce client :
   sans commande validée de l'expéditeur, l'anomalie :data:`REORDER_WITHOUT_HISTORY_ANOMALY`
   demande à l'équipe commerciale de compléter la commande ;
7. un code article SAGE X3 est calculé par des règles fixes s'il n'a pas été trouvé ;
8. :func:`tecpap.extraction.rules.normalize_order` normalise les champs, rédige les anomalies,
   liste les champs obligatoires manquants et calcule la confiance ; chaque anomalie du
   traitement (contenu suspect, extraction par règles, renouvellement sans historique) retire à
   son tour :data:`~tecpap.extraction.rules.ANOMALY_PENALTY` points.

Sans modèle de langue (clé d'API absente) ou quand l'appel échoue, l'extraction par règles
(:func:`tecpap.extraction.rules.rule_based_extract`) prend le relais avec l'anomalie
:data:`RULES_FALLBACK_ANOMALY` : la commande arrive quand même au tableau de bord, où l'équipe
commerciale vérifie tous les champs ; sa confiance ne dépasse jamais :data:`RULES_MAX_CONFIDENCE`.

Le résultat contient, en plus des champs de la commande : ``langue`` (code de
:func:`~tecpap.nlp.language.detect_language`), ``modele_llm`` (``None`` pour l'extraction par
règles), ``routage_modele`` (raison du choix du modèle), ``anomalies`` et ``champs_manquants``
(listes), ``confiance`` (calculée par les règles), ``is_reorder`` et ``message_brut`` (le texte
analysé, au plus :data:`MESSAGE_MAX_CHARS` caractères).
"""

from __future__ import annotations

import logging
import os
import threading
import unicodedata
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from email.utils import parseaddr
from typing import Any

from tecpap.catalog import ALL_REORDER_PATTERNS, COMPANY_INFO, PRODUCT_TYPES
from tecpap.config import Settings, load_settings
from tecpap.extraction.article_codes import generate_article_code, suggest_article_code_from_description
from tecpap.extraction.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    MAX_EXAMPLES,
    REORDER_SYSTEM_PROMPT,
    build_extraction_prompt,
    build_reorder_prompt,
)
from tecpap.extraction.rules import (
    ANOMALY_PENALTY,
    detect_reorder,
    extract_client_name,
    normalize_order,
    normalize_phone,
    normalize_product_type,
    rule_based_extract,
)
from tecpap.extraction.schema import parse_llm_order
from tecpap.formatting import resolve_timezone
from tecpap.llm.client import LLMClient
from tecpap.llm.router import ModelRouter
from tecpap.nlp.language import detect_language
from tecpap.processing.multimodal import MediaProcessor
from tecpap.rag.retriever import OrderExampleRetriever, build_embedder
from tecpap.security.events import KIND_PROMPT_INJECTION, log_security_event
from tecpap.security.guardrails import inspect_input, sanitize_text

logger = logging.getLogger(__name__)

__all__ = [
    "COMPANY_INFO",
    "INJECTION_ANOMALY",
    "MESSAGE_MAX_CHARS",
    "PROCESSING_ANOMALIES",
    "PRODUCT_TYPES",
    "REORDER_PATTERNS",
    "REORDER_WITHOUT_HISTORY_ANOMALY",
    "RULES_FALLBACK_ANOMALY",
    "RULES_MAX_CONFIDENCE",
    "DataExtractor",
    "auto_generate_article_code",
    "build_message_content",
    "sender_identity",
]

REORDER_PATTERNS: list[str] = list(ALL_REORDER_PATTERNS)
"""Expressions de renouvellement, toutes langues confondues (voir ``tecpap.catalog``)."""

MESSAGE_MAX_CHARS = 20_000
"""Longueur maximale du texte analysé (et conservé dans ``message_brut``)."""

REORDER_MODEL_MIN_CHARS = 60
"""Longueur à partir de laquelle un texte sans expression de renouvellement connue est soumis au
modèle léger : un message plus court qui n'en contient aucune n'est pas ambigu."""

EXTRACTION_MAX_TOKENS = 1000
REORDER_MAX_TOKENS = 300

INJECTION_ANOMALY = "Contenu suspect : tentative possible d'injection d'instructions"
"""Anomalie d'un contenu signalé par :func:`tecpap.security.guardrails.inspect_input`."""

RULES_FALLBACK_ANOMALY = "Extraction par règles (modèle de langue indisponible) : vérifier tous les champs"
"""Anomalie d'une commande extraite sans modèle de langue."""

PROCESSING_ANOMALIES: frozenset[str] = frozenset({INJECTION_ANOMALY, RULES_FALLBACK_ANOMALY})
"""Anomalies qui décrivent le traitement du message plutôt que les valeurs de la commande."""

RULES_ROUTING_REASON = "Extraction par règles : aucun modèle de langue disponible pour ce message."
"""Raison enregistrée dans ``routage_modele`` pour une extraction par règles."""

RULES_MAX_CONFIDENCE = 50
"""Confiance maximale d'une commande extraite par règles seules.

Le calcul de :func:`~tecpap.extraction.rules.normalize_order` mesure seulement que les champs
obligatoires sont remplis ; sans modèle de langue, rien ne recoupe leurs valeurs (le contrôle par
les règles relirait le même texte avec les mêmes règles). La confiance reste donc sous le seuil
des alertes de confiance faible du tableau de bord (60 %) et sous celle de toute extraction par
le modèle, quelle que soit la complétude.
"""

REORDER_WITHOUT_HISTORY_ANOMALY = (
    "Renouvellement demandé, mais aucune commande validée de cet expéditeur à reprendre : "
    "commande à compléter par l'équipe commerciale."
)
"""Anomalie d'un renouvellement que l'historique de l'expéditeur ne permet pas de compléter."""

# Champs complétés depuis la dernière commande validée de l'expéditeur lors d'un renouvellement :
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

PRODUCT_HISTORY_FIELDS = frozenset({"nature_produit", "prix_unitaire", "prix_total"})
"""Champs de l'historique propres au produit commandé : ils ne sont pas repris pour un autre type de produit."""

SENDER_HISTORY_LIMIT = 50
"""Nombre maximal de commandes validées de l'expéditeur lues pour un renouvellement."""

# Score minimal de recouvrement des mots pour rapprocher un nom cité d'un client connu.
CLIENT_MATCH_THRESHOLD = 0.3

TRUE_WORDS = frozenset({"true", "oui", "yes", "1"})

CHANNELS = ("email", "whatsapp")
"""Canaux d'origine d'un message, enregistrés dans le journal des événements de sécurité."""


def company_today() -> date:
    """Date du jour dans le fuseau horaire de l'entreprise (Casablanca), quel que soit celui du serveur."""
    return datetime.now(timezone.utc).astimezone(resolve_timezone()).date()


def _as_bool(value: Any) -> bool:
    """Interprète un booléen renvoyé par le modèle (``true``, ``"true"``, ``"oui"``…)."""
    if isinstance(value, bool):
        return value
    return value is not None and str(value).strip().lower() in TRUE_WORDS


def sender_identity(sender: str | None, channel: str) -> str | None:
    """Forme comparable d'un expéditeur, ou ``None`` quand elle est illisible.

    Canal email : adresse de l'en-tête ``From`` sans le nom affiché, en minuscules
    (« Achats <Achats@Atlas.ma> » → « achats@atlas.ma »). Canal WhatsApp : numéro au format
    international (:func:`tecpap.extraction.rules.normalize_phone`).
    """
    if not isinstance(sender, str) or not sender.strip():
        return None
    if channel == "email":
        address = parseaddr(sender)[1].strip().lower()
        return address if "@" in address else None
    return normalize_phone(sender)


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
    """Extraction des données de commande (étapes décrites dans la documentation du module).

    Un même extracteur peut servir plusieurs fils d'exécution : la base est lue par la connexion
    du fil courant (:attr:`tecpap.db.database.DatabaseManager.connection`).

    Args:
        db_manager: base des commandes : historique des clients, exemples de commandes validées
            et journal des événements de sécurité. Sans base, ces trois étapes sont omises.
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
        llm: client des modèles de langue ; par défaut construit depuis la configuration
            (:meth:`LLMClient.from_settings`). Sans clé d'API, l'extraction se fait par règles.
        router: choix des modèles ; par défaut construit depuis ``settings``.
        embedder: calcul des plongements des exemples ; par défaut celui de la configuration
            (:func:`tecpap.rag.retriever.build_embedder`).
        clock: date du jour (dates sans année, dates de livraison passées) ; par défaut la date du
            jour à Casablanca (:func:`company_today`).
    """

    def __init__(self, db_manager: Any = None, settings: Settings | None = None, llm: LLMClient | None = None,
                 *, router: ModelRouter | None = None, embedder: Any = None,
                 clock: Callable[[], date] | None = None):
        self.settings = settings or load_settings()
        self.llm = llm if llm is not None else LLMClient.from_settings(self.settings)
        self.media = MediaProcessor(self.llm, self.settings)
        self.model = self.settings.model_extraction
        self.router = router if router is not None else ModelRouter(self.settings)
        self.db = db_manager
        self._embedder = embedder
        self._clock = clock or company_today
        self._local = threading.local()

    def set_database(self, db_manager: Any) -> None:
        """Définit la base utilisée pour l'historique des clients."""
        self.db = db_manager

    def _connection(self) -> Any:
        """Connexion du fil courant à la base, ou ``None``."""
        if not self.db:
            return None
        try:
            return self.db.connection
        except Exception:
            logger.warning("Base des commandes inaccessible", exc_info=True)
            return None

    # ------------------------------------------------------------------ renouvellement

    def detect_reorder_intent(self, email_content: str, language: str | None = None) -> dict[str, Any]:
        """Demande au modèle léger si le message est un renouvellement et quel client il cite.

        Args:
            email_content: texte du message.
            language: code de langue du message ; détecté depuis le texte quand il est absent.

        Returns:
            ``is_reorder`` (booléen), ``client_name`` (texte non vide ou ``None``),
            ``confidence`` et, quand le modèle les fournit, les expressions détectées
            (``reorder_indicators``). ``model_reason`` donne la raison du choix du modèle.
        """
        default: dict[str, Any] = {"is_reorder": False, "client_name": None, "confidence": 0}
        if not self.llm.available:
            logger.warning("Détection de renouvellement impossible : clé OPENAI_API_KEY absente")
            return default

        code = language or detect_language(email_content).code
        choice = self.router.route("reorder_detection", code)
        result = self.llm.chat_json(
            model=choice.model,
            system=REORDER_SYSTEM_PROMPT,
            user=build_reorder_prompt(email_content),
            temperature=choice.temperature,
            max_tokens=REORDER_MAX_TOKENS,
        )
        if not isinstance(result, dict):
            logger.warning("Détection de renouvellement en échec : réponse du modèle absente ou illisible")
            return default

        # La réponse du modèle est contrôlée avant usage : booléen attendu, nom de client non vide.
        client_name = result.get("client_name")
        result["is_reorder"] = _as_bool(result.get("is_reorder"))
        result["client_name"] = client_name.strip() if isinstance(client_name, str) and client_name.strip() else None
        result["model_reason"] = choice.reason
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
        quand un mot de plus de trois lettres du nom cherché figure dans le nom du client. Ce
        rapprochement n'attribue jamais une commande ni ne donne accès à un historique : il sert
        seulement à signaler un client proche à l'équipe commerciale.
        """
        connection = self._connection()
        if connection is None:
            return None

        cursor = connection.cursor()
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

    def _sender_validated_orders(self, sender: str | None, channel: str) -> list[dict[str, Any]]:
        """Commandes validées envoyées par ``sender`` (même adresse email ou même numéro), la plus récente d'abord.

        La requête SQL est fixe et paramétrée ; elle présélectionne les lignes, puis l'expéditeur
        de chacune est comparé exactement à ``sender`` (adresse sans nom affiché ni casse, ou
        numéro au format international).
        """
        connection = self._connection()
        identity = sender_identity(sender, channel)
        if connection is None or identity is None:
            return []
        if channel == "email":
            column, needle, searched = "email_from", identity, "LOWER(COALESCE(c.email_from, ''))"
        else:
            # Les 9 derniers chiffres du numéro, cherchés dans la colonne sans séparateurs.
            column, needle = "whatsapp_from", identity[-9:]
            searched = "REPLACE(REPLACE(REPLACE(COALESCE(c.whatsapp_from, ''), ' ', ''), '-', ''), '.', '')"
        # Fragments SQL choisis parmi des constantes ; les valeurs passent par des paramètres « ? ».
        rows = connection.execute(
            "SELECT c.*, p.type AS produit_type, cl.nom AS client_nom FROM commandes c "
            "LEFT JOIN clients cl ON c.client_id = cl.id LEFT JOIN produits p ON c.produit_id = p.id "
            f"WHERE c.statut = 'validee' AND INSTR({searched}, ?) > 0 "  # nosec B608
            "ORDER BY COALESCE(c.validated_at, c.created_at) DESC, c.id DESC LIMIT ?",
            (needle, SENDER_HISTORY_LIMIT),
        ).fetchall()
        return [dict(row) for row in rows if sender_identity(row[column], channel) == identity]

    def get_sender_last_order(self, sender: str | None, channel: str,
                              client_name: str | None = None) -> dict[str, Any] | None:
        """Dernière commande validée envoyée par ``sender``, pour compléter un renouvellement.

        Seules les commandes validées par l'équipe commerciale et envoyées depuis la même
        adresse email (canal email) ou le même numéro (canal WhatsApp) sont lues : un message
        qui cite le nom d'un autre client n'obtient jamais l'historique de ce client.

        Args:
            sender: expéditeur du message (en-tête ``From`` d'un email, numéro WhatsApp).
            channel: « email » ou « whatsapp ».
            client_name: client cité dans le message ; la commande retenue est alors la dernière
                de l'expéditeur pour ce client (même nom sans casse ni accents, ou nom cité dont
                tous les mots figurent dans le nom d'un seul des clients de l'expéditeur).

        Returns:
            La commande (colonnes de ``commandes``, ``produit_type`` et ``client_nom``), ou ``None``.
        """
        rows = self._sender_validated_orders(sender, channel)
        if not rows or not client_name:
            return rows[0] if rows else None
        wanted = self._name_words(client_name)
        exact = [row for row in rows if self._name_words(row.get("client_nom")) == wanted]
        if exact:
            return exact[0]
        partial = [row for row in rows if wanted and wanted <= self._name_words(row.get("client_nom"))]
        return partial[0] if len({row.get("client_id") for row in partial}) == 1 else None

    def _name_words(self, name: str | None) -> frozenset[str]:
        return frozenset(self.normalize_client_name(name).split())

    def fill_from_history(self, extracted_data: dict[str, Any], last_order: dict[str, Any] | None) -> dict[str, Any]:
        """Complète les champs vides de la commande avec ceux de la dernière commande du client.

        Seuls les champs absents de l'extraction sont complétés, et la confiance déclarée par le
        modèle est conservée telle quelle. Deux règles évitent de reprendre une valeur qui ne
        vaut plus :

        - la désignation et les prix (:data:`PRODUCT_HISTORY_FIELDS`) ne sont repris que si le
          type de produit extrait est absent ou identique à celui de la commande précédente ;
        - le prix total n'est repris qu'avec la quantité : pour une autre quantité, il reste vide.
        """
        if not last_order:
            return extracted_data

        new_type = extracted_data.get("type_produit")
        old_type = last_order.get("produit_type")
        same_product = not new_type or not old_type or (
            (normalize_product_type(str(new_type)) or new_type) == (normalize_product_type(str(old_type)) or old_type))
        quantity_from_history = not extracted_data.get("quantite")

        filled_fields = []
        for new_field, old_field in HISTORY_FIELDS.items():
            if extracted_data.get(new_field) or not last_order.get(old_field):
                continue
            if new_field in PRODUCT_HISTORY_FIELDS and not same_product:
                continue
            if new_field == "prix_total" and not quantity_from_history:
                continue
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

    def _reorder(self, text: str, language: str, data: Mapping[str, Any], reasons: list[str],
                 *, use_model: bool) -> tuple[bool, str | None]:
        """Renouvellement et client cité : règles d'abord, modèle léger pour un texte long et ambigu."""
        if detect_reorder(text):
            return True, data.get("entreprise_cliente") or extract_client_name(text)
        if not use_model or len(text) < REORDER_MODEL_MIN_CHARS:
            return False, None
        info = self.detect_reorder_intent(text, language)
        if info.get("model_reason"):
            reasons.append(info["model_reason"])
        if not info.get("is_reorder"):
            return False, None
        logger.info("Renouvellement détecté par le modèle (indices : %s)", info.get("reorder_indicators", []))
        return True, info.get("client_name") or data.get("entreprise_cliente")

    def _fill_reorder(self, data: dict[str, Any], client_name: str | None, *, sender: str | None, channel: str,
                      anomalies: list[str]) -> dict[str, Any]:
        """Complète un renouvellement depuis la dernière commande validée de l'expéditeur (requêtes SQL fixes).

        Sans commande validée de l'expéditeur (pour le client cité, le cas échéant), aucun champ
        n'est complété et l'anomalie :data:`REORDER_WITHOUT_HISTORY_ANOMALY` demande à l'équipe
        commerciale de compléter la commande ; un client connu au nom proche du nom cité y est
        mentionné.
        """
        last_order = self.get_sender_last_order(sender, channel, client_name)
        if not last_order:
            logger.info("Renouvellement sans commande validée de l'expéditeur : aucun champ complété")
            anomalies.append(self._reorder_anomaly(client_name))
            return data
        logger.info("Dernière commande validée de l'expéditeur retenue : %s",
                    last_order.get("numero_commande") or f"ID-{last_order.get('id')}")
        # Commande validée de l'expéditeur : le nom enregistré remplace le nom écrit dans le message.
        if last_order.get("client_nom"):
            data["entreprise_cliente"] = last_order["client_nom"]
        data = self.fill_from_history(data, last_order)
        data["est_bon_commande"] = True
        return data

    def _reorder_anomaly(self, client_name: str | None) -> str:
        """Anomalie d'un renouvellement sans historique, avec le client connu le plus proche du nom cité."""
        close = self.find_matching_client(client_name) if client_name else None
        if close:
            return f"{REORDER_WITHOUT_HISTORY_ANOMALY} Client connu le plus proche du nom cité : « {close} »."
        return REORDER_WITHOUT_HISTORY_ANOMALY

    # ------------------------------------------------------------------ exemples et sécurité

    def _example_retriever(self, connection: Any) -> OrderExampleRetriever:
        """Recherche d'exemples du fil courant (son cache de vecteurs est propre à la connexion)."""
        cached = getattr(self._local, "examples", None)
        if cached is None or cached.conn is not connection:
            if self._embedder is None:
                self._embedder = build_embedder(self.settings, self.llm)
            cached = OrderExampleRetriever(connection, self._embedder)
            self._local.examples = cached
        return cached

    def _examples(self, text: str) -> list[Any]:
        """Commandes validées proches du message, anonymisées ; liste vide sans base ou en cas d'erreur."""
        connection = self._connection()
        if connection is None:
            return []
        try:
            return self._example_retriever(connection).similar_validated_orders(text, k=MAX_EXAMPLES)
        except Exception:
            logger.warning("Exemples de commandes validées indisponibles", exc_info=True)
            return []

    def _log_injection(self, channel: str, sender: str | None, score: int, reasons: list[str]) -> None:
        connection = self._connection()
        if connection is None:
            logger.warning("Contenu suspect (canal %s, score %d) : aucune base pour l'événement de sécurité",
                           channel, score)
            return
        log_security_event(connection, channel=channel, sender=sender, kind=KIND_PROMPT_INJECTION,
                           score=score, details=reasons)

    # ------------------------------------------------------------------ extraction

    def extract_from_email(self, email_data: dict[str, Any],
                           attachment_texts: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Extrait une commande d'un email : sujet, expéditeur, date, corps et texte des pièces jointes.

        Args:
            email_data: ``subject``, ``from``, ``date`` et ``body`` du message ; ``source``
                (« whatsapp ») désigne le canal quand il ne s'agit pas d'un email.
            attachment_texts: texte de chaque pièce jointe, par nom de fichier.

        Returns:
            Les champs de la commande (dont ``est_bon_commande``) et la traçabilité de
            l'extraction (voir la documentation du module).
        """
        channel = email_data.get("source") if email_data.get("source") in CHANNELS else "email"
        content = build_message_content(email_data, attachment_texts)
        return self._extract(content, channel=channel, sender=email_data.get("from"), use_history=True)

    def extract_text(self, text: str, *, sender: str | None = None, channel: str = "whatsapp") -> dict[str, Any] | None:
        """Extrait une commande d'un texte (message WhatsApp, transcription d'une note vocale…).

        Args:
            text: texte du message, transcription ou texte lu dans un média.
            sender: expéditeur (numéro ou adresse), pour le journal des événements de sécurité.
            channel: canal d'origine (« whatsapp » ou « email »).

        Returns:
            La commande extraite (voir la documentation du module), ou ``None`` pour un texte vide.
        """
        if not isinstance(text, str) or not text.strip():
            return None
        return self._extract(text, channel=channel, sender=sender, use_history=True)

    def assistant_extractor(self) -> Callable[[str, str], dict[str, Any] | None]:
        """Fonction ``extract(text, sender)`` attendue par :class:`tecpap.chatbot.bot.WhatsAppAssistant`.

        L'historique n'y est pas consulté, et le renouvellement n'y est lu que par les règles
        (aucun appel au modèle de détection) : l'assistant renouvelle une commande avec ses
        propres outils, limités aux commandes de l'expéditeur
        (:meth:`tecpap.chatbot.tools.AssistantTools.last_validated_order_draft`), et montre la
        commande renouvelée au client avant de l'enregistrer.
        """
        def extract(text: str, sender: str) -> dict[str, Any] | None:
            if not isinstance(text, str) or not text.strip():
                return None
            return self._extract(text, channel="whatsapp", sender=sender, use_history=False)

        return extract

    def _extract(self, content: str, *, channel: str, sender: str | None, use_history: bool) -> dict[str, Any]:
        """Chaîne d'extraction complète ; ne lève pas d'exception pour une réponse du modèle invalide."""
        text = sanitize_text(content, max_chars=None)
        check = inspect_input(text, max_chars=None)
        text = text[:MESSAGE_MAX_CHARS]
        anomalies: list[str] = []
        if check.is_suspicious:
            logger.warning("Contenu suspect reçu par %s (score %d) : commande signalée à l'équipe commerciale",
                           channel, check.score)
            anomalies.append(INJECTION_ANOMALY)
            self._log_injection(channel, sender, check.score, check.reasons)

        language = detect_language(text).code
        if not self.llm.available:
            logger.warning("Modèle de langue indisponible (clé OPENAI_API_KEY absente) : extraction par règles")
            return self._rules_extraction(text, language, anomalies, use_history, sender=sender, channel=channel)

        choice = self.router.route("extraction", language)
        raw = self.llm.chat_json(
            model=choice.model,
            system=EXTRACTION_SYSTEM_PROMPT,
            user=build_extraction_prompt(text, self._examples(text)),
            temperature=choice.temperature,
            max_tokens=EXTRACTION_MAX_TOKENS,
        )
        if not isinstance(raw, dict):
            logger.error("Extraction en échec : réponse du modèle absente ou illisible ; extraction par règles")
            return self._rules_extraction(text, language, anomalies, use_history, sender=sender, channel=channel)

        data = parse_llm_order(raw)
        reasons = [choice.reason]
        # Sans historique (assistant WhatsApp), le renouvellement n'est lu que par les règles :
        # l'assistant le traite avec ses propres outils, et un appel au modèle serait inutile.
        is_reorder, client_name = self._reorder(text, language, data, reasons, use_model=use_history)
        data["is_reorder"] = is_reorder
        if is_reorder and use_history:
            data = self._fill_reorder(data, client_name, sender=sender, channel=channel, anomalies=anomalies)
        return self._finish(data, text, language, choice.model, " ; ".join(reasons), anomalies)

    def _rules_extraction(self, text: str, language: str, anomalies: list[str], use_history: bool, *,
                          sender: str | None, channel: str) -> dict[str, Any]:
        """Extraction de secours par règles, signalée par :data:`RULES_FALLBACK_ANOMALY`."""
        data = rule_based_extract(text, today=self._clock())
        anomalies.append(RULES_FALLBACK_ANOMALY)
        if data.get("is_reorder") and use_history:
            data = self._fill_reorder(data, data.get("entreprise_cliente"), sender=sender, channel=channel,
                                      anomalies=anomalies)
        return self._finish(data, text, language, None, RULES_ROUTING_REASON, anomalies)

    def _finish(self, data: dict[str, Any], text: str, language: str, model: str | None, reason: str,
                anomalies: list[str]) -> dict[str, Any]:
        """Code article, normalisation par les règles et traçabilité de l'extraction."""
        if not data.get("code_article"):
            auto_code = auto_generate_article_code(data)
            if auto_code:
                data["code_article"] = auto_code
                logger.info("Code article calculé : %s", auto_code)

        result = normalize_order(data, source_text=text, today=self._clock())
        order = result.data
        order.setdefault("est_bon_commande", False)
        order["is_reorder"] = bool(data.get("is_reorder"))
        # Les anomalies du traitement (contenu suspect, extraction par règles, renouvellement sans
        # historique) retirent les mêmes points de confiance que les anomalies des valeurs.
        confidence = max(0, result.confidence - ANOMALY_PENALTY * len(anomalies))
        if model is None:
            confidence = min(confidence, RULES_MAX_CONFIDENCE)
        order.update({
            "confiance": confidence,
            "langue": language,
            "modele_llm": model,
            "routage_modele": reason,
            "anomalies": [*anomalies, *(item for item in result.anomalies if item not in anomalies)],
            "champs_manquants": list(result.missing_fields),
            "message_brut": text,
        })
        logger.info("Commande extraite (langue %s, modèle %s) : %d anomalie(s), champs manquants %s, confiance %d",
                    language, model or "règles", len(order["anomalies"]), order["champs_manquants"], confidence)
        return order

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
