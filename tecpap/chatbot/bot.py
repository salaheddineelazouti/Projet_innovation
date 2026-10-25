"""Assistant WhatsApp de TECPAP : automate de conversation déterministe, modèle de langue en appui.

Traitement d'un message (:meth:`WhatsAppAssistant.handle`), dans cet ordre :

1. doublon : un message dont l'identifiant Twilio (``MessageSid``) est déjà enregistré ne reçoit
   aucune réponse (Twilio peut livrer deux fois le même message) ;
2. limite de débit : au-delà de ``Settings.whatsapp_rate_limit_per_minute`` messages reçus d'un
   même numéro en 60 secondes, le premier message en trop reçoit un avertissement (événement de
   sécurité ``rate_limited``), les suivants aucune réponse ;
3. contrôle des injections (:func:`tecpap.security.guardrails.inspect_input`) : un message suspect
   est journalisé comme événement de sécurité et reçoit une réponse fixe, sans appel au modèle
   de langue ni aux outils ;
4. langue (:func:`tecpap.nlp.language.detect_language`) : la conversation garde la dernière
   langue claire ; une réponse courte ou ambiguë (« ok », « 5000 ») ne la change pas ;
5. intention déterministe (:func:`tecpap.chatbot.intents.detect_intent`), puis automate :

   - ``cancel`` abandonne la commande en cours ; sans commande en cours, la demande
     d'annulation d'une commande déjà envoyée est transférée à l'équipe commerciale ;
   - ``human`` transfère la conversation (état ``handoff``) : l'assistant ne fait plus qu'accuser
     réception jusqu'à la remise à zéro depuis le tableau de bord ;
   - ``collecting`` : la réponse est lue pour le champ demandé (numéro du produit dans le menu,
     quantité lue par :func:`tecpap.extraction.rules.parse_quantity`, nom du client), fusionnée
     avec ce que l'extraction trouve dans le message, puis le premier champ manquant est
     demandé, ou le récapitulatif est envoyé ;
   - ``awaiting_confirmation`` : seul un accord explicite (« oui », « wakha », « نعم ») enregistre
     la commande, toujours au statut « en_attente » (l'équipe commerciale la valide) ; un refus
     demande ce qu'il faut modifier ; une correction (« 8000 », « c'est pour Snack X ») est
     fusionnée et le récapitulatif renvoyé ;
   - ``idle`` : commande, renouvellement (dernière commande validée de l'expéditeur), suivi,
     question sur les produits (base de connaissances, voir :mod:`tecpap.rag.answer`) ;
     un message sans intention reconnue est confié au modèle de langue avec des outils limités à
     l'expéditeur (:class:`tecpap.chatbot.tools.AssistantTools`), au plus
     :data:`MAX_TOOL_ROUNDS` tours, ou reçoit le message d'aide sans modèle.

Les réponses qui contiennent des données (récapitulatif, statuts, confirmation) viennent des
modèles de :mod:`tecpap.chatbot.messages`. Quand le modèle demande un outil de données, la
réponse est construite à partir du résultat de l'outil et le résultat n'est pas renvoyé au
modèle ; seules les erreurs (outil inconnu, arguments invalides) lui sont renvoyées, pour qu'il
reformule sa demande. Tout texte rédigé par le modèle passe par
:class:`tecpap.security.guardrails.OutputFilter` : canari (code aléatoire placé dans l'instruction
système, renouvelé à chaque message, dont la présence dans une réponse prouve une fuite de cette
instruction), reprise des instructions système, secrets, coordonnées autres que celles de
l'expéditeur et de TECPAP ; une réponse bloquée est remplacée par un message fixe et journalisée.
Les réponses de la base de connaissances, déjà contrôlées par :class:`tecpap.rag.answer.KnowledgeAnswerer`,
repassent par ce filtre avant l'envoi.

Chaque message reçu et envoyé est enregistré (:class:`tecpap.chatbot.conversation.ConversationStore`).
L'extraction d'une commande est une dépendance ``extract(text, sender) -> dict | None`` ; sans
elle, ou quand elle échoue, l'extraction par règles (:func:`tecpap.extraction.rules.rule_based_extract`)
est utilisée. Chaque résultat passe par :func:`tecpap.extraction.rules.normalize_order`. La
traçabilité fournie par l'extraction (modèle de langue utilisé, raison de ce choix, anomalies du
traitement comme l'extraction par règles faute de modèle) est gardée avec la commande en cours et
enregistrée avec la commande confirmée.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from tecpap.catalog import COMPANY_INFO, PRODUCTS, REQUIRED_ORDER_FIELDS
from tecpap.chatbot.conversation import Conversation, ConversationStore, conversation_key
from tecpap.chatbot.intents import IntentResult, detect_intent
from tecpap.chatbot.messages import (
    DEFAULT_LANGUAGE,
    order_lines,
    product_label,
    render,
    status_lines,
)
from tecpap.chatbot.tools import AssistantTools, ToolResult
from tecpap.db.database import is_generic_client_name
from tecpap.extraction.extractor import PROCESSING_ANOMALIES
from tecpap.extraction.rules import (
    ANOMALY_PENALTY,
    ORDER_FIELD_KINDS,
    clean_text,
    extract_client_name,
    fold,
    normalize_order,
    normalize_phone,
    normalize_product_type,
    parse_quantity,
    rule_based_extract,
)
from tecpap.llm.router import ModelRouter
from tecpap.nlp.language import LanguageResult, detect_language, reply_language
from tecpap.rag.answer import Answer, KnowledgeAnswerer, answer_system_prompt, is_commercial_question
from tecpap.security.events import KIND_OUTPUT_BLOCKED, KIND_PROMPT_INJECTION, KIND_RATE_LIMITED, log_security_event
from tecpap.security.guardrails import (
    UNTRUSTED_CONTENT_RULE,
    OutputFilter,
    canary_instruction,
    inspect_input,
    new_canary,
    wrap_untrusted,
)
from tecpap.security.text import sanitize_text

logger = logging.getLogger(__name__)

__all__ = [
    "ASK_ORDER",
    "CHANNEL",
    "LANGUAGE_SWITCH_CONFIDENCE",
    "MAX_TOOL_ROUNDS",
    "BotReply",
    "Extractor",
    "WhatsAppAssistant",
    "routing_system_prompt",
]

Extractor = Callable[[str, str], Mapping[str, Any] | None]
"""Extraction d'une commande : ``extract(texte, expéditeur)`` renvoie les champs trouvés, ou ``None``."""

CHANNEL = "whatsapp"
"""Canal enregistré dans le journal des événements de sécurité."""

MAX_TOOL_ROUNDS = 3
"""Nombre maximal d'appels au modèle de langue avec outils pour un même message."""

MAX_TOOL_CALLS_PER_ROUND = 3
"""Nombre maximal d'appels d'outils exécutés par réponse du modèle ; les suivants sont refusés."""

LANGUAGE_SWITCH_CONFIDENCE = 0.5
"""Confiance minimale de la détection pour changer la langue d'une conversation déjà commencée."""

RATE_LIMIT_WINDOW_SECONDS = 60
"""Fenêtre de la limite de débit, en secondes."""

ASK_ORDER: tuple[str, ...] = ("type_produit", "quantite", "entreprise_cliente")
"""Ordre dans lequel les champs obligatoires manquants sont demandés."""

MAX_LLM_REPLY_CHARS = 600
MAX_CLIENT_NAME_CHARS = 80
MAX_CLIENT_NAME_WORDS = 8
MAX_DRAFT_MESSAGES = 10
MAX_DRAFT_MESSAGE_CHARS = 1000
ROUTING_MAX_TOKENS = 300

_ASK_TEMPLATES = {"type_produit": "ask_product", "quantite": "ask_quantity", "entreprise_cliente": "ask_client"}
# Traçabilité de l'extraction reprise de l'extracteur et enregistrée avec la commande confirmée.
_AUDIT_FIELDS = ("modele_llm", "routage_modele")
_DRAFT_FIELDS = frozenset(name for name in ORDER_FIELD_KINDS if name not in ("est_bon_commande", "confiance"))
_SIDE_INTENTS = frozenset({"status", "question", "greeting"})
_NOT_A_NAME_INTENTS = frozenset({"confirm_yes", "confirm_no", "greeting", "status", "question", "cancel", "human",
                                 "reorder"})
_LOCK_STRIPES = 64

_PRODUCT_CHOICE_RE = re.compile(
    r"^\W*(?:(?:n|no|num|numero|nr|ra9m|raqm|رقم|le|la|l|choix|option|produit|product)\W*)?([1-9])\W*$"
)
_REFERENCE_TOKEN_RE = re.compile(r"(?<![\w-])([A-Za-z]{1,8}[-_/]?\d[\w/-]{0,28})(?![\w-])")
_LABELLED_REFERENCE_RE = re.compile(
    r"(?i)(?:n°|n º|no\.|num(?:éro|ero)?|r[ée]f(?:[ée]rence)?\.?|commande|order|#|رقم|نمرة)\s*:?\s*(\d[\d/-]{1,20})"
)
_CMD_REFERENCE_RE = re.compile(r"(?i)^cmd-?\d+$")
_FIELD_REQUESTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("quantite", re.compile(r"(?<!\w)(?:quantite|quantites|qte|qty|quantity|ch7al|chhal|kammiya|lkammiya|"
                            r"الكمية|كمية|العدد)(?!\w)")),
    ("type_produit", re.compile(r"(?<!\w)(?:produit|produits|product|article|type|lproduit|المنتج|منتج|النوع)(?!\w)")),
    ("entreprise_cliente", re.compile(r"(?<!\w)(?:client|nom|societe|entreprise|magasin|commerce|smiya|smia|"
                                      r"customer|company|name|الزبون|الاسم|الشركة|المحل)(?!\w)")),
)
_CATALOG_QUESTION_RE = re.compile(
    r"(?<!\w)(?:produits|catalogue|gamme|proposez|products|catalog|range|المنتجات|منتجات|les produits)(?!\w)"
)


@dataclass(frozen=True)
class BotReply:
    """Réponse de l'assistant à un message.

    Attributes:
        text: texte à envoyer ; vide quand rien ne doit être envoyé (doublon, limite de débit dépassée).
        intent: intention retenue (voir :data:`tecpap.chatbot.intents.INTENTS`) ou nature du
            traitement (``blocked``, ``duplicate``, ``rate_limited``, ``handoff``, ``media_failed``,
            ``invalid_sender``, ``error``).
        order_id: identifiant de la commande enregistrée par ce message, sinon ``None``.
        blocked: vrai quand le message ou la réponse générée a été bloqué par un contrôle de sécurité.
        language: langue de la réponse (``fr``, ``darija``, ``ar``).
        state: état de la conversation après le message.
    """

    text: str
    intent: str
    order_id: int | None = None
    blocked: bool = False
    language: str = DEFAULT_LANGUAGE
    state: str = "idle"

    @property
    def silent(self) -> bool:
        """Vrai quand aucune réponse ne doit être envoyée."""
        return not self.text


def _valid_draft(draft: Any) -> bool:
    """Vrai pour un brouillon de commande lisible (champs de commande sous la clé ``order``)."""
    return isinstance(draft, dict) and isinstance(draft.get("order"), dict)


@dataclass
class _Turn:
    """Message en cours de traitement et son contexte."""

    conversation: Conversation
    sender: str
    phone: str
    text: str
    intent: IntentResult
    detected: LanguageResult
    message_sid: str | None
    tools: AssistantTools
    audit: dict[str, Any] = field(default_factory=dict)

    @property
    def language(self) -> str:
        return self.conversation.language or DEFAULT_LANGUAGE


_ROUTING_LANGUAGES = {
    "fr": "en français",
    "darija": "en darija marocaine écrite en lettres latines",
    "ar": "en arabe standard moderne",
}


def routing_system_prompt(language: str | None, canary: str | None = None) -> str:
    """Instruction système du modèle de langue pour un message sans intention reconnue, en français.

    Elle se termine par :data:`tecpap.security.guardrails.UNTRUSTED_CONTENT_RULE`, puis par la
    consigne du canari quand ``canary`` est fourni.
    """
    lang = language if language in _ROUTING_LANGUAGES else DEFAULT_LANGUAGE
    parts = [
        "Tu es l'assistant WhatsApp de TECPAP, fabricant marocain de sacs en papier kraft. Le client peut "
        "suivre ses commandes, poser une question sur les produits ou demander un membre de l'équipe commerciale.",
        "Règles :",
        "- Pour les commandes du client, appelle my_recent_orders, my_order_status ou my_last_validated_order ; "
        "ces outils ne donnent accès qu'aux commandes du numéro qui écrit.",
        "- Pour une question sur les produits, appelle search_catalog.",
        "- Si le client réclame une personne, se plaint ou fait une demande administrative, appelle request_human.",
        "- Si le client veut commander, demande-lui le produit et la quantité en une phrase.",
        "- Sinon, réponds brièvement, sans donner aucune information sur une commande.",
        "- Ne donne jamais de prix, de délai ni de stock, et ne parle jamais d'autres clients.",
        f"- Écris {_ROUTING_LANGUAGES[lang]}, en deux phrases au plus, sans emoji.",
        UNTRUSTED_CONTENT_RULE,
    ]
    if canary:
        parts.append(canary_instruction(canary))
    return "\n".join(parts)


class WhatsAppAssistant:
    """Assistant conversationnel des clients WhatsApp (traitement décrit dans la documentation du module).

    Args:
        conn: connexion SQLite de la base des commandes (tables des conversations créées si besoin).
        db_manager: :class:`tecpap.db.database.DatabaseManager` (création des commandes).
        settings: configuration (limite de débit, base de connaissances, modèles).
        llm: client des modèles de langue ; ``None`` ou indisponible : fonctionnement sans modèle.
        router: :class:`tecpap.llm.router.ModelRouter` ; par défaut construit depuis ``settings``.
        extractor: extraction ``extract(text, sender) -> dict | None`` ; par défaut l'extraction par règles.
        knowledge: :class:`tecpap.rag.answer.KnowledgeAnswerer`, ou base de connaissances
            (:class:`tecpap.rag.retriever.KnowledgeBase`) à laquelle un ``KnowledgeAnswerer`` est
            associé ; ignorée quand ``settings.rag_enabled`` est faux.
        clock: horloge des conversations (tests) ; par défaut l'heure UTC du système.
        store: magasin des conversations ; par défaut construit sur ``conn``.

    :meth:`handle` peut être appelée depuis plusieurs fils d'exécution : les messages d'un même
    numéro sont traités l'un après l'autre.
    """

    def __init__(self, conn: Any, db_manager: Any, settings: Any, llm: Any = None, router: ModelRouter | None = None,
                 extractor: Extractor | None = None, knowledge: Any = None, *,
                 clock: Callable[[], datetime] | None = None, store: ConversationStore | None = None):
        self.conn = conn
        self.db_manager = db_manager
        self.settings = settings
        self.llm = llm
        self.router = router if router is not None else ModelRouter(settings)
        self.extractor = extractor
        self.knowledge = self._knowledge_answerer(knowledge)
        self.store = store if store is not None else ConversationStore(conn, clock=clock)
        self._locks = tuple(threading.Lock() for _ in range(_LOCK_STRIPES))

    def _knowledge_answerer(self, knowledge: Any) -> Any:
        if knowledge is None or not getattr(self.settings, "rag_enabled", True):
            return None
        if hasattr(knowledge, "answer"):
            return knowledge
        if hasattr(knowledge, "search"):
            return KnowledgeAnswerer(knowledge, self.llm, self.router, self.settings)
        raise TypeError("knowledge doit être un KnowledgeAnswerer ou une base de connaissances")

    def _llm_ready(self) -> bool:
        return self.llm is not None and bool(getattr(self.llm, "available", False))

    # ------------------------------------------------------------------ point d'entrée

    def handle(self, sender: str, text: str | None, *, message_sid: str | None = None,
               media_kind: str | None = None) -> BotReply:
        """Traite un message reçu et renvoie la réponse à envoyer ; ne lève jamais d'exception.

        Args:
            sender: numéro de l'expéditeur tel que reçu de Twilio (« whatsapp:+2126… »).
            text: texte du message, ou texte lu dans le média joint (transcription, lecture d'image).
            message_sid: identifiant Twilio du message (déduplication).
            media_kind: nature du média joint (« audio », « image », « document »…), ou ``None`` ;
                avec un texte vide, le média n'a pas pu être lu.
        """
        if not isinstance(sender, str) or normalize_phone(sender) is None:
            logger.warning("Message WhatsApp ignoré : expéditeur non reconnu")
            return BotReply("", "invalid_sender")
        phone = conversation_key(sender)
        try:
            if self.store.is_duplicate(message_sid):
                logger.info("Message WhatsApp déjà traité : aucune réponse")
                return BotReply("", "duplicate")
            with self._locks[hash(phone) % _LOCK_STRIPES]:
                return self._handle_locked(sender, phone, text, message_sid, media_kind)
        except Exception:
            # Base de données indisponible, par exemple : le client reçoit le message d'erreur générique.
            logger.exception("Traitement d'un message WhatsApp impossible")
            return BotReply(render("error", DEFAULT_LANGUAGE), "error")

    def _handle_locked(self, sender: str, phone: str, text: str | None, message_sid: str | None,
                       media_kind: str | None) -> BotReply:
        conversation = self.store.get_or_create(phone)
        recent = self.store.count_recent_inbound(phone, RATE_LIMIT_WINDOW_SECONDS)
        inbound_id = self.store.add_message(conversation.id, "in", self._inbound_body(text, media_kind),
                                            message_sid=message_sid)
        if inbound_id is None:
            return BotReply("", "duplicate", language=conversation.language or DEFAULT_LANGUAGE,
                            state=conversation.state)
        limit = self.settings.whatsapp_rate_limit_per_minute
        if recent >= limit:
            reply = self._rate_limited(conversation, phone, recent, limit)
        else:
            try:
                reply = self._process(conversation, sender, phone, text, message_sid, media_kind)
            except Exception:
                logger.exception("Erreur pendant le traitement d'un message WhatsApp (conversation %s)",
                                 conversation.id)
                # Les modifications partielles de l'état sont abandonnées : l'état enregistré est relu.
                conversation = self.store.get(conversation.id) or conversation
                reply = self._reply(conversation, "error", intent="error")
        self.store.update_message(inbound_id, intent=reply.intent, blocked=reply.blocked)
        if reply.text:
            self.store.add_message(conversation.id, "out", reply.text, intent=reply.intent, blocked=reply.blocked)
        self.store.save(conversation)
        return replace(reply, state=conversation.state)

    @staticmethod
    def _inbound_body(text: str | None, media_kind: str | None) -> str:
        body = sanitize_text(text if isinstance(text, str) else "", max_chars=None)
        if media_kind:
            kind = re.sub(r"[^\w-]", "", str(media_kind))[:20] or "media"
            body = f"[{kind}] {body}".strip()
        return body

    def _rate_limited(self, conversation: Conversation, phone: str, recent: int, limit: int) -> BotReply:
        if recent > limit:
            return BotReply("", "rate_limited", language=conversation.language or DEFAULT_LANGUAGE)
        log_security_event(self.conn, channel=CHANNEL, sender=phone, kind=KIND_RATE_LIMITED,
                           details=f"Plus de {limit} messages en {RATE_LIMIT_WINDOW_SECONDS} secondes")
        return self._reply(conversation, "rate_limited", intent="rate_limited")

    # ------------------------------------------------------------------ traitement

    def _process(self, conversation: Conversation, sender: str, phone: str, text: str | None,
                 message_sid: str | None, media_kind: str | None) -> BotReply:
        raw = text if isinstance(text, str) else ""
        if not raw.strip():
            if media_kind:
                return self._reply(conversation, "media_failed", intent="media_failed")
            return self._reply(conversation, "greeting", intent="greeting")

        check = inspect_input(raw)
        if check.is_suspicious:
            log_security_event(self.conn, channel=CHANNEL, sender=phone, kind=KIND_PROMPT_INJECTION,
                               score=check.score, details=check.reasons)
            return self._reply(conversation, "blocked", intent="blocked", blocked=True)

        detected = detect_language(check.cleaned)
        self._update_language(conversation, detected)
        intent = detect_intent(check.cleaned, detected.code, suspicious=False)
        logger.info("Message WhatsApp (conversation %s) : intention %s, langue %s, état %s",
                    conversation.id, intent.intent, detected.code, conversation.state)

        if conversation.handoff or conversation.state == "handoff":
            conversation.state, conversation.handoff = "handoff", True
            return self._reply(conversation, "handoff_ack", intent="handoff")

        tools = AssistantTools(self.conn, self.db_manager, sender, self.knowledge, language=conversation.language
                               or DEFAULT_LANGUAGE)
        turn = _Turn(conversation, sender, phone, check.cleaned, intent, detected, message_sid, tools)
        reply = self._dispatch(turn)
        if conversation.draft_expired:
            reply = replace(reply, text=f"{render('draft_expired', turn.language)}\n\n{reply.text}".strip())
        return reply

    def _update_language(self, conversation: Conversation, detected: LanguageResult) -> None:
        """Garde la dernière langue claire : une détection peu sûre ne change pas une langue déjà retenue."""
        if detected.code == "unknown":
            return
        candidate = reply_language(detected)
        if conversation.language is None or (
                candidate != conversation.language and detected.confidence >= LANGUAGE_SWITCH_CONFIDENCE):
            conversation.language = candidate

    def _dispatch(self, turn: _Turn) -> BotReply:
        intent = turn.intent.intent
        if intent == "cancel":
            return self._cancel(turn)
        if intent == "human":
            return self._handoff(turn, "handoff", "human")
        state = turn.conversation.state
        if _valid_draft(turn.conversation.draft):
            if state == "awaiting_confirmation":
                return self._awaiting_confirmation(turn)
            if state == "collecting":
                return self._collecting(turn)
        turn.conversation.state, turn.conversation.draft = "idle", None
        return self._idle(turn)

    # ------------------------------------------------------------------ réponses

    def _reply(self, conversation: Conversation, key: str, *, intent: str, order_id: int | None = None,
               blocked: bool = False, **values: Any) -> BotReply:
        language = conversation.language or DEFAULT_LANGUAGE
        return BotReply(render(key, language, **values), intent, order_id, blocked, language)

    def _turn_reply(self, turn: _Turn, key: str, *, intent: str | None = None, **values: Any) -> BotReply:
        return self._reply(turn.conversation, key, intent=intent or turn.intent.intent, **values)

    @staticmethod
    def _combine(first: BotReply, second: BotReply) -> BotReply:
        return replace(first, text=f"{first.text}\n\n{second.text}", blocked=first.blocked or second.blocked)

    def _handoff(self, turn: _Turn, key: str, intent: str) -> BotReply:
        turn.tools.request_human()
        turn.conversation.state, turn.conversation.handoff = "handoff", True
        return self._turn_reply(turn, key, intent=intent)

    def _cancel(self, turn: _Turn) -> BotReply:
        conversation = turn.conversation
        if conversation.draft or conversation.state in ("collecting", "awaiting_confirmation"):
            conversation.state, conversation.draft = "idle", None
            return self._turn_reply(turn, "cancelled", intent="cancel")
        return self._handoff(turn, "cancel_forwarded", "cancel")

    # ------------------------------------------------------------------ état idle

    def _idle(self, turn: _Turn) -> BotReply:
        intent = turn.intent.intent
        if intent == "greeting":
            return self._turn_reply(turn, "greeting")
        if intent == "status":
            return self._status(turn)
        if intent == "reorder":
            return self._reorder(turn)
        if intent == "order":
            return self._start_order(turn)
        if intent == "question":
            return self._question(turn)
        if intent in ("confirm_yes", "confirm_no"):
            return self._turn_reply(turn, "help")
        return self._llm_route(turn)

    # ------------------------------------------------------------------ commande en cours

    def _new_draft(self, turn: _Turn) -> dict[str, Any]:
        return {"order": {}, "asked": None, "messages": [], "language_code": turn.detected.code,
                "reorder_of": None, "updated_at": self.store.timestamp()}

    def _remember(self, draft: dict[str, Any], text: str) -> None:
        messages = [*draft.get("messages", []), text[:MAX_DRAFT_MESSAGE_CHARS]]
        draft["messages"] = messages[-MAX_DRAFT_MESSAGES:]

    def _extract(self, turn: _Turn) -> dict[str, Any]:
        """Champs de commande lus dans le message : extracteur configuré, sinon règles, puis normalisation."""
        data: Any = None
        if self.extractor is not None:
            try:
                data = self.extractor(turn.text, turn.sender)
            except Exception:
                logger.exception("Extraction en échec : extraction par règles")
                data = None
        if not isinstance(data, Mapping) or not data:
            data = rule_based_extract(turn.text)
        else:
            self._remember_audit(turn, data)
        result = normalize_order(data, source_text=turn.text)
        fields = {name: value for name, value in result.data.items() if name in _DRAFT_FIELDS}
        if is_generic_client_name(fields.get("entreprise_cliente")):
            del fields["entreprise_cliente"]
        return fields

    @staticmethod
    def _remember_audit(turn: _Turn, data: Mapping[str, Any]) -> None:
        """Garde la traçabilité donnée par l'extracteur : modèle, raison du choix, anomalies du traitement."""
        audit = {name: data[name] for name in _AUDIT_FIELDS if name in data}
        anomalies = data.get("anomalies")
        if isinstance(anomalies, (list, tuple)):
            audit["anomalies"] = [item for item in anomalies if item in PROCESSING_ANOMALIES]
        turn.audit.update(audit)

    @staticmethod
    def _merged_audit(previous: Any, current: Mapping[str, Any]) -> dict[str, Any]:
        """Traçabilité de la commande en cours après un message de plus.

        Le modèle (et la raison de son choix) retenu est celui de la première extraction faite par
        un modèle de langue, en général celle du message qui a ouvert la commande ; les anomalies
        du traitement de tous les messages sont réunies.
        """
        merged = dict(previous) if isinstance(previous, Mapping) else {}
        if merged.get("modele_llm") is None and (current.get("modele_llm") is not None
                                                 or "routage_modele" not in merged):
            merged.update({name: current[name] for name in _AUDIT_FIELDS if name in current})
        anomalies = list(merged.get("anomalies") or [])
        anomalies += [item for item in current.get("anomalies") or [] if item not in anomalies]
        merged["anomalies"] = anomalies
        return merged

    def _start_order(self, turn: _Turn) -> BotReply:
        draft = self._new_draft(turn)
        draft["order"].update(self._extract(turn))
        self._remember(draft, turn.text)
        return self._advance(turn, draft)

    def _advance(self, turn: _Turn, draft: dict[str, Any], *, intro: str = "") -> BotReply:
        """Normalise la commande en cours, puis demande le premier champ manquant ou envoie le récapitulatif."""
        conversation = turn.conversation
        if "entreprise_cliente" not in draft["order"]:
            known = turn.tools.known_client_name()
            if known:
                draft["order"]["entreprise_cliente"] = known
        result = normalize_order(draft["order"])
        draft["order"] = {name: value for name, value in result.data.items() if name in _DRAFT_FIELDS}
        draft["updated_at"] = self.store.timestamp()
        if turn.audit:
            draft["audit"] = self._merged_audit(draft.get("audit"), turn.audit)
        conversation.draft = draft
        missing = [name for name in ASK_ORDER if name in result.missing_fields]
        intent = "reorder" if turn.intent.intent == "reorder" else "order"
        if missing:
            draft["asked"] = missing[0]
            conversation.state = "collecting"
            return self._turn_reply(turn, _ASK_TEMPLATES[missing[0]], intent=intent)
        draft["asked"] = None
        conversation.state = "awaiting_confirmation"
        return self._turn_reply(turn, "summary", intent=intent, lines=order_lines(draft["order"], turn.language),
                                intro=intro)

    def _pending_prompt(self, turn: _Turn, draft: dict[str, Any]) -> BotReply:
        """Question en attente : champ demandé, ou rappel de la confirmation."""
        if turn.conversation.state == "awaiting_confirmation":
            return self._turn_reply(turn, "confirm_hint")
        asked = draft.get("asked") or next(
            (name for name in ASK_ORDER if name not in draft.get("order", {})), "type_produit")
        return self._turn_reply(turn, _ASK_TEMPLATES.get(asked, "ask_product"))

    def _side_reply(self, turn: _Turn, draft: dict[str, Any]) -> BotReply:
        """Suivi, question ou salutation pendant une commande en cours, suivis de la question en attente."""
        intent = turn.intent.intent
        if intent == "greeting":
            return self._pending_prompt(turn, draft)
        first = self._status(turn) if intent == "status" else self._question(turn)
        if turn.conversation.state == "handoff":
            return first
        return self._combine(first, self._pending_prompt(turn, draft))

    def _collecting(self, turn: _Turn) -> BotReply:
        draft = turn.conversation.draft
        intent = turn.intent.intent
        if intent == "reorder":
            return self._reorder(turn)
        if intent in _SIDE_INTENTS:
            return self._side_reply(turn, draft)
        asked = draft.get("asked")
        updates = self._extract(turn)
        value = self._field_answer(asked, turn)
        if value is not None:
            updates[asked] = value
        elif "quantite" not in updates and "quantite" not in draft["order"] and len(turn.text.split()) <= 4:
            # Une quantité donnée avant d'être demandée (« 3000 » en réponse au menu des produits)
            # est gardée ; les nombres de 1 à 9 restent des choix du menu.
            quantity = self._field_answer("quantite", turn)
            if quantity is not None and quantity >= 10:
                updates["quantite"] = quantity
        if not updates:
            if asked == "quantite":
                return self._turn_reply(turn, "quantity_invalid")
            return self._pending_prompt(turn, draft)
        draft["order"].update(updates)
        self._remember(draft, turn.text)
        return self._advance(turn, draft)

    def _field_answer(self, field: str | None, turn: _Turn) -> Any:
        """Valeur du champ demandé lue dans une réponse courte, ou ``None``."""
        text = turn.text
        if field == "type_produit":
            match = _PRODUCT_CHOICE_RE.match(fold(text))
            if match and 1 <= int(match.group(1)) <= len(PRODUCTS):
                return PRODUCTS[int(match.group(1)) - 1]["type"]
            return normalize_product_type(text)
        if field == "quantite":
            quantity = parse_quantity(text)
            if quantity is None or quantity <= 0:
                return None
            return int(quantity) if float(quantity).is_integer() else quantity
        if field == "entreprise_cliente":
            return self._client_answer(turn)
        return None

    @staticmethod
    def _client_answer(turn: _Turn) -> str | None:
        """Nom de client donné en réponse à la question « pour quel client ? »."""
        if turn.intent.intent in _NOT_A_NAME_INTENTS:
            return None
        name = extract_client_name(turn.text) or turn.text
        name = clean_text(name, max_length=MAX_CLIENT_NAME_CHARS * 2)
        if not name:
            return None
        words = name.split()
        if (len(name) > MAX_CLIENT_NAME_CHARS or len(words) > MAX_CLIENT_NAME_WORDS
                or not re.search(r"[^\W\d_].*[^\W\d_]", name) or "tecpap" in fold(name)
                or is_generic_client_name(name)):
            return None
        return name.strip(" .,;:!")

    def _awaiting_confirmation(self, turn: _Turn) -> BotReply:
        draft = turn.conversation.draft
        intent = turn.intent.intent
        if intent == "confirm_yes":
            return self._submit(turn)
        if intent == "confirm_no":
            draft["asked"] = None
            draft["updated_at"] = self.store.timestamp()
            return self._turn_reply(turn, "ask_change")
        if intent == "reorder":
            return self._reorder(turn)
        if intent in _SIDE_INTENTS and intent != "greeting":
            return self._side_reply(turn, draft)
        updates = self._modifications(turn, draft)
        if updates:
            draft["order"].update(updates)
            self._remember(draft, turn.text)
            return self._advance(turn, draft)
        field = self._requested_field(turn.text)
        if field is not None:
            draft["asked"] = field
            draft["updated_at"] = self.store.timestamp()
            turn.conversation.state = "collecting"
            return self._turn_reply(turn, _ASK_TEMPLATES[field])
        if intent == "greeting":
            return self._turn_reply(turn, "summary", lines=order_lines(draft["order"], turn.language))
        return self._turn_reply(turn, "confirm_hint")

    def _modifications(self, turn: _Turn, draft: dict[str, Any]) -> dict[str, Any]:
        """Champs du message qui diffèrent de la commande en cours (une quantité seule est une correction)."""
        updates = self._extract(turn)
        if "quantite" not in updates and len(turn.text.split()) <= 4:
            quantity = self._field_answer("quantite", turn)
            if quantity is not None:
                updates["quantite"] = quantity
        current = draft.get("order", {})
        return {name: value for name, value in updates.items() if current.get(name) != value}

    @staticmethod
    def _requested_field(text: str) -> str | None:
        """Champ que le client veut modifier (« la quantité », « le client »), ou ``None``."""
        if len(text.split()) > 6:
            return None
        folded = fold(text)
        return next((name for name, pattern in _FIELD_REQUESTS if pattern.search(folded)), None)

    def _submit(self, turn: _Turn) -> BotReply:
        conversation = turn.conversation
        draft = conversation.draft
        result = normalize_order(draft["order"])
        if any(name in result.missing_fields for name in REQUIRED_ORDER_FIELDS):
            return self._advance(turn, draft)
        order = {name: value for name, value in result.data.items() if name in _DRAFT_FIELDS}
        audit = draft.get("audit") if isinstance(draft.get("audit"), dict) else {}
        processing = [item for item in audit.get("anomalies") or [] if item in PROCESSING_ANOMALIES]
        order.update({
            # Comme dans l'extracteur, chaque anomalie du traitement retire ANOMALY_PENALTY points.
            "confiance": max(0, result.confidence - ANOMALY_PENALTY * len(processing)),
            "message_brut": "\n".join(draft.get("messages", [])),
            "langue": draft.get("language_code") or turn.detected.code,
            "anomalies": [*processing, *(item for item in result.anomalies if item not in processing)],
            "champs_manquants": result.missing_fields,
            **{name: audit[name] for name in _AUDIT_FIELDS if name in audit},
        })
        try:
            order_id = turn.tools.submit_order(order, message_sid=turn.message_sid)
        except Exception:
            logger.exception("Enregistrement de la commande confirmée en échec (conversation %s)", conversation.id)
            return self._turn_reply(turn, "error", intent="error")
        # La commande existe : le brouillon est abandonné avant toute autre opération, pour qu'un
        # second accord ne crée pas une deuxième commande.
        conversation.state, conversation.draft, conversation.last_order_id = "idle", None, order_id
        reference = f"CMD-{order_id}"
        try:
            found = turn.tools.my_order_status(reference)
            reference = found["reference"] if found else reference
        except Exception:
            logger.exception("Référence de la commande %s illisible", order_id)
        return self._reply(conversation, "submitted", intent="confirm_yes", order_id=order_id, reference=reference)

    # ------------------------------------------------------------------ renouvellement, suivi, questions

    def _reorder(self, turn: _Turn) -> BotReply:
        updates = self._extract(turn)
        base = turn.tools.last_validated_order_draft(updates.get("entreprise_cliente"))
        draft = self._new_draft(turn)
        self._remember(draft, turn.text)
        if base is None:
            draft["order"].update(updates)
            notice = self._turn_reply(turn, "reorder_none")
            return self._combine(notice, self._advance(turn, draft))
        draft["reorder_of"] = base.pop("reference")
        draft["order"].update(base)
        draft["order"].update({name: value for name, value in updates.items() if name != "entreprise_cliente"})
        return self._advance(turn, draft, intro=render("reorder_intro", turn.language))

    @staticmethod
    def _reference_candidates(text: str) -> list[str]:
        """Références de commande citées : jetons lettres + chiffres (« BC-889 ») ou nombre après « n° »."""
        candidates = [match.group(1) for match in _REFERENCE_TOKEN_RE.finditer(text)
                      if sum(char.isdigit() for char in match.group(1)) >= 2
                      or _CMD_REFERENCE_RE.match(match.group(1))]
        candidates += [match.group(1) for match in _LABELLED_REFERENCE_RE.finditer(text)]
        return list(dict.fromkeys(candidates))

    def _status(self, turn: _Turn) -> BotReply:
        candidates = self._reference_candidates(turn.text)
        for reference in candidates:
            found = turn.tools.my_order_status(reference)
            if found is not None:
                return self._turn_reply(turn, "status_list", intent="status",
                                        lines=status_lines([found], turn.language))
        if candidates:
            return self._turn_reply(turn, "status_not_found", intent="status", reference=candidates[0])
        return self._recent_orders_reply(turn, turn.tools.my_recent_orders(5))

    def _recent_orders_reply(self, turn: _Turn, orders: list[dict[str, Any]]) -> BotReply:
        if not orders:
            return self._turn_reply(turn, "status_none", intent="status")
        return self._turn_reply(turn, "status_list", intent="status", lines=status_lines(orders, turn.language))

    def _question(self, turn: _Turn) -> BotReply:
        if is_commercial_question(turn.text):
            return self._handoff(turn, "sales_will_answer", "question")
        if self.knowledge is None:
            return self._catalog_reply(turn, turn.tools.search_catalog(turn.text))
        try:
            answer = self.knowledge.answer(turn.text, turn.language, allowed_phones=(turn.phone,))
        except Exception:
            logger.exception("Réponse de la base de connaissances en échec")
            return self._turn_reply(turn, "no_answer", intent="question")
        return self._answer_reply(turn, answer)

    def _answer_reply(self, turn: _Turn, answer: Answer) -> BotReply:
        """Réponse de la base de connaissances, contrôlée une seconde fois par le filtre de l'assistant.

        Le second contrôle ne dépend pas de l'objet ``knowledge`` fourni : une réponse qui reprend
        une instruction système, contient un secret ou cite des coordonnées non autorisées n'est
        jamais envoyée, quel que soit le composant qui l'a rédigée.
        """
        if answer.blocked_reasons:
            log_security_event(self.conn, channel=CHANNEL, sender=turn.phone, kind=KIND_OUTPUT_BLOCKED,
                               details=list(answer.blocked_reasons))
            return self._turn_reply(turn, "no_answer", intent="question", blocked=True)
        if answer.needs_sales_team:
            return self._handoff(turn, "sales_will_answer", "question")
        text = self._checked_text(turn, answer.text)
        if text is None:
            return self._turn_reply(turn, "no_answer", intent="question", blocked=True)
        return BotReply(text, "question", language=turn.language)

    def _catalog_reply(self, turn: _Turn, data: Mapping[str, Any]) -> BotReply:
        products = data.get("products") or []
        cited = len(products) == 1
        if not cited and not _CATALOG_QUESTION_RE.search(fold(turn.text)):
            return self._turn_reply(turn, "no_answer", intent="question")
        lines = "\n".join(f"{item['number']}. {product_label(item['product'], turn.language)}" for item in products)
        return self._turn_reply(turn, "catalog_list", intent="question", products=lines)

    # ------------------------------------------------------------------ modèle de langue avec outils

    def _llm_route(self, turn: _Turn) -> BotReply:
        """Message sans intention reconnue : modèle de langue avec outils limités à l'expéditeur."""
        if not self._llm_ready():
            return self._turn_reply(turn, "help", intent="other")
        canary = new_canary()
        system = routing_system_prompt(turn.language, canary)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": wrap_untrusted(turn.text, "message_client")},
        ]
        choice = self.router.route("intent", turn.detected.code)
        specs = AssistantTools.tool_specs()
        for _ in range(MAX_TOOL_ROUNDS):
            result = self.llm.chat(model=choice.model, messages=messages, temperature=choice.temperature,
                                   max_tokens=ROUTING_MAX_TOKENS, tools=specs, tool_choice="auto")
            if result is None:
                break
            calls = list(getattr(result, "tool_calls", None) or [])
            if not calls:
                content = (getattr(result, "content", None) or "").strip()
                if content:
                    return self._filtered_reply(turn, content, system, canary)
                break
            messages.append(self._assistant_message(result.content, calls))
            for position, call in enumerate(calls):
                if position >= MAX_TOOL_CALLS_PER_ROUND:
                    outcome = ToolResult(str(call.name)[:60], False, error="Trop d'outils demandés en une fois.")
                else:
                    outcome = turn.tools.execute(call.name, call.arguments)
                    reply = self._tool_reply(turn, outcome)
                    if reply is not None:
                        return reply
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps({"erreur": outcome.error or "Résultat inutilisable."},
                                                       ensure_ascii=False)})
        return self._turn_reply(turn, "help", intent="other")

    @staticmethod
    def _assistant_message(content: str | None, calls: list[Any]) -> dict[str, Any]:
        return {"role": "assistant", "content": content, "tool_calls": [
            {"id": call.id, "type": "function",
             "function": {"name": str(call.name),
                          "arguments": json.dumps(call.arguments or {}, ensure_ascii=False, default=str)}}
            for call in calls
        ]}

    def _tool_reply(self, turn: _Turn, outcome: ToolResult) -> BotReply | None:
        """Réponse construite à partir du résultat d'un outil, ou ``None`` pour un outil en erreur."""
        if not outcome.ok:
            return None
        data = outcome.data
        if outcome.name == "my_recent_orders":
            return self._recent_orders_reply(turn, data.get("orders") or [])
        if outcome.name == "my_order_status":
            if data.get("order"):
                return self._turn_reply(turn, "status_list", intent="status",
                                        lines=status_lines([data["order"]], turn.language))
            cited = next((reference for reference in self._reference_candidates(turn.text)
                          if fold(reference) == fold(str(data.get("reference", "")))), None)
            if cited:
                return self._turn_reply(turn, "status_not_found", intent="status", reference=cited)
            return self._recent_orders_reply(turn, turn.tools.my_recent_orders(5))
        if outcome.name == "my_last_validated_order":
            order = data.get("order")
            return self._recent_orders_reply(turn, [order] if order else [])
        if outcome.name == "search_catalog":
            if outcome.answer is not None:
                return self._answer_reply(turn, outcome.answer)
            return self._catalog_reply(turn, data)
        if outcome.name == "request_human":
            return self._handoff(turn, "handoff", "human")
        return None

    def _protected_texts(self, system: str | None = None) -> list[str]:
        """Textes qui ne doivent jamais apparaître dans une réponse : toutes les instructions système.

        ``system`` est l'instruction envoyée pour ce message (avec son canari), ajoutée en tête.
        """
        prompts = [*(routing_system_prompt(lang) for lang in _ROUTING_LANGUAGES),
                   *(answer_system_prompt(lang) for lang in _ROUTING_LANGUAGES)]
        return [system, *prompts] if system else prompts

    def _checked_text(self, turn: _Turn, text: str, *, system: str | None = None,
                      canary: str | None = None) -> str | None:
        """Texte à envoyer après le filtre de sortie, ou ``None`` s'il est bloqué (événement de sécurité journalisé).

        Seuls le numéro de l'expéditeur et les coordonnées publiques de TECPAP restent lisibles.
        """
        output_filter = OutputFilter(self._protected_texts(system), canary,
                                     allowed_phones={turn.phone, COMPANY_INFO["phone"]},
                                     allowed_emails={COMPANY_INFO["email"]})
        check = output_filter.check(text)
        if not check.allowed:
            log_security_event(self.conn, channel=CHANNEL, sender=turn.phone, kind=KIND_OUTPUT_BLOCKED,
                               details=check.reasons)
            return None
        return check.text

    def _filtered_reply(self, turn: _Turn, content: str, system: str, canary: str) -> BotReply:
        """Texte rédigé par le modèle, contrôlé par le filtre de sortie avant l'envoi."""
        text = self._checked_text(turn, content, system=system, canary=canary)
        if text is None:
            return self._turn_reply(turn, "no_answer", intent="other", blocked=True)
        if len(text) > MAX_LLM_REPLY_CHARS:
            text = text[:MAX_LLM_REPLY_CHARS - 1].rstrip() + "…"
        return BotReply(text, "other", language=turn.language)
