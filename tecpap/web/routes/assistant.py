"""Pages du tableau de bord consacrées à l'assistant WhatsApp : conversations, base de connaissances, sécurité.

Conversations (``/conversations``)
    Liste des conversations WhatsApp : d'abord celles transférées à l'équipe commerciale
    (« à reprendre »), puis les plus récemment actives. La fiche d'une conversation montre ses
    messages (reçus du client, envoyés par l'assistant, envoyés par l'équipe commerciale), avec
    l'intention reconnue, l'indicateur de blocage par les contrôles de sécurité et la date, la
    commande en cours de saisie et les commandes du numéro. Deux actions :

    - « Terminer la prise en charge » remet la conversation à l'état ``idle``, sans transfert ni
      commande en cours (:meth:`tecpap.chatbot.conversation.ConversationStore.reset`) :
      l'assistant répond de nouveau au client. La remise à zéro attend la fin du traitement d'un
      message du même expéditeur (:meth:`tecpap.web.services.Services.sender_lock`), pour que
      l'assistant n'écrase pas l'état remis à zéro ;
    - la réponse de l'équipe commerciale est envoyée par WhatsApp
      (:meth:`tecpap.channels.whatsapp.WhatsAppReceiver.send_reply`), puis enregistrée comme
      message envoyé d'intention :data:`AGENT_INTENT`. La conversation reste transférée :
      l'assistant n'intervient pas tant que la prise en charge n'est pas terminée.

    La liste signale aussi les messages reçus encore en attente de traitement ou en échec dans la
    file durable des messages WhatsApp (:mod:`tecpap.chatbot.inbox`).

Base de connaissances (``/knowledge``)
    Documents et sections indexés (table ``rag_chunks``), calcul des plongements (vecteurs
    numériques qui représentent les textes), état de l'index par rapport au dossier des
    documents, réindexation et recherche de test. La recherche de test montre les composantes
    du score de chaque extrait et la décision de :class:`tecpap.rag.answer.KnowledgeAnswerer`
    (répondre, transmettre à l'équipe commerciale, ou répondre « je n'ai pas cette
    information »), calculée sans appel au modèle de langue.

Sécurité (``/security``)
    Derniers événements de sécurité (:mod:`tecpap.security.events`), filtrables par nature, et
    nombre d'événements de chaque nature sur les :data:`SECURITY_WINDOW_DAYS` derniers jours.

Les actions (POST) sont des formulaires HTML qui portent le jeton CSRF dans un champ caché ; le
contrôle d'accès et la vérification du jeton s'appliquent à toute l'application
(:mod:`tecpap.web.auth`). Les textes venus des clients ou du modèle de langue sont échappés par
Jinja à l'affichage.

Le blueprint fournit aussi aux gabarits les libellés en français des états, langues,
intentions, champs et natures d'événements, le nombre de conversations à reprendre (pastille du
menu latéral) et le filtre ``timestamp`` (« AAAA-MM-JJ HH:MM:SS » → « JJ/MM/AAAA HH:MM »). Les
dates sont affichées en temps universel coordonné (UTC), comme elles sont enregistrées.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from tecpap.chatbot.conversation import MAX_LIMIT as CONVERSATION_MAX_LIMIT
from tecpap.chatbot.conversation import conversation_key
from tecpap.nlp.language import detect_language, reply_language
from tecpap.rag.answer import (
    MAX_QUESTION_CHARS,
    REASON_ANSWERED,
    REASON_BLOCKED,
    REASON_COMMERCIAL,
    REASON_EMPTY,
    REASON_ERROR,
    REASON_EXCERPT,
    REASON_NO_MATCH,
    Answer,
    KnowledgeAnswerer,
)
from tecpap.rag.documents import load_chunks
from tecpap.rag.retriever import KnowledgeBase, SearchHit, best_score
from tecpap.rag.store import StoredChunk
from tecpap.security.events import (
    KIND_INVALID_SIGNATURE,
    KIND_OUTPUT_BLOCKED,
    KIND_PROMPT_INJECTION,
    KIND_RATE_LIMITED,
    LABEL_MAX_CHARS,
    count_security_events,
    recent_security_events,
)
from tecpap.security.events import MAX_LIMIT as SECURITY_MAX_LIMIT
from tecpap.security.text import sanitize_text
from tecpap.web.auth import current_user
from tecpap.web.services import Services, get_services

logger = logging.getLogger(__name__)

bp = Blueprint("assistant", __name__)

AGENT_INTENT = "agent"
"""Intention enregistrée pour un message envoyé par l'équipe commerciale depuis le tableau de bord."""

AGENT_REPLY_MAX_CHARS = 1600
"""Longueur maximale d'une réponse de l'équipe commerciale (limite d'un message WhatsApp envoyé par Twilio)."""

CONVERSATION_LIST_LIMIT = 100
"""Nombre maximal de conversations non transférées affichées (les plus récentes)."""

THREAD_MESSAGE_LIMIT = 200
"""Nombre maximal de messages affichés dans la fiche d'une conversation (les plus récents)."""

PHONE_ORDERS_PREVIEW = 10
"""Nombre de commandes du numéro affichées dans la fiche d'une conversation."""

SENDER_LOCK_TIMEOUT_SECONDS = 30.0
"""Attente maximale de la fin du traitement d'un message avant la remise à zéro d'une conversation."""

SECURITY_EVENT_LIMIT = 200
"""Nombre maximal d'événements de sécurité affichés."""

SECURITY_WINDOW_DAYS = 7
"""Période, en jours, du décompte des événements de sécurité par nature."""

LAST_INDEX_KEY = "tecpap_last_knowledge_index"
"""Clé de ``app.extensions`` qui garde le bilan de la dernière réindexation lancée depuis le tableau de bord."""

SEND_FAILED_MESSAGE = (
    "Message non envoyé : l'envoi WhatsApp a échoué (service Twilio non configuré ou indisponible). "
    "Le détail est consigné dans le journal du serveur ; le texte saisi est conservé ci-dessous."
)
EMPTY_REPLY_MESSAGE = "Le message est vide : saisissez le texte à envoyer au client."
BUSY_CONVERSATION_MESSAGE = (
    "Un message de ce client est en cours de traitement par l'assistant : réessayez dans quelques instants."
)

STATE_LABELS: Mapping[str, str] = {
    "idle": "Aucune commande en cours",
    "collecting": "Commande en cours de saisie",
    "awaiting_confirmation": "Attente de la confirmation du client",
    "handoff": "Transférée à l'équipe commerciale",
}
"""Libellés des états d'une conversation (:data:`tecpap.chatbot.conversation.STATES`)."""

LANGUAGE_LABELS: Mapping[str, str] = {
    "fr": "Français",
    "en": "Anglais",
    "ar": "Arabe standard",
    "darija": "Darija",
    "darija_ar": "Darija (lettres arabes)",
    "darija_latin": "Darija (lettres latines)",
    "unknown": "Indéterminée",
}
"""Libellés des codes de langue : langue de réponse d'une conversation et langue détectée d'une commande."""

LANGUAGE_SHORT_LABELS: Mapping[str, str] = {
    "fr": "FR", "en": "EN", "ar": "AR", "darija": "Darija", "darija_ar": "Darija", "darija_latin": "Darija",
    "unknown": "Indéterminée",
}
"""Libellés courts des codes de langue (pastilles de la liste des commandes)."""

INTENT_LABELS: Mapping[str, str] = {
    "confirm_yes": "Accord",
    "confirm_no": "Refus",
    "cancel": "Annulation",
    "human": "Demande d'un conseiller",
    "status": "Suivi de commande",
    "reorder": "Renouvellement",
    "greeting": "Salutation",
    "question": "Question",
    "order": "Commande",
    "other": "Autre",
    AGENT_INTENT: "Équipe commerciale",
    "handoff": "Conversation transférée",
    "blocked": "Bloqué par les contrôles de sécurité",
    "duplicate": "Message reçu en double",
    "rate_limited": "Limite de messages dépassée",
    "media_failed": "Média illisible",
    "invalid_sender": "Expéditeur non reconnu",
    "error": "Erreur de traitement",
    "order_validated": "Avis de validation",
    "order_rejected": "Avis de refus",
}
"""Libellés des intentions et des natures de traitement enregistrées avec les messages."""

FIELD_LABELS: Mapping[str, str] = {
    "entreprise_cliente": "Client",
    "type_produit": "Type de produit",
    "nature_produit": "Désignation",
    "quantite": "Quantité",
    "unite": "Unité",
    "date_commande": "Date de commande",
    "date_livraison": "Date de livraison",
    "numero_commande": "Numéro de commande",
    "code_article": "Code article",
    "type_sac": "Type de sac",
    "format_sac": "Format",
    "type_papier": "Type de papier",
    "grammage": "Grammage",
    "laize": "Laize",
    "impression_client": "Impression",
    "prix_unitaire": "Prix unitaire",
    "prix_total": "Prix total",
    "devise": "Devise",
    "commercial": "Commercial",
    "informations_supplementaires": "Informations supplémentaires",
}
"""Libellés des champs d'une commande (champs manquants, commande en cours de saisie)."""

EVENT_KIND_LABELS: Mapping[str, str] = {
    KIND_PROMPT_INJECTION: "Tentative d'injection d'instructions",
    KIND_OUTPUT_BLOCKED: "Réponse bloquée par le filtre de sortie",
    KIND_RATE_LIMITED: "Limite de messages par minute dépassée",
    KIND_INVALID_SIGNATURE: "Signature Twilio invalide",
}
"""Libellés des natures d'événements de sécurité connues, dans l'ordre d'affichage."""

CHANNEL_LABELS: Mapping[str, str] = {"whatsapp": "WhatsApp", "email": "Email", "web": "Tableau de bord"}
"""Libellés des canaux de réception."""

ANSWER_DECISIONS: Mapping[str, tuple[bool, str]] = {
    REASON_ANSWERED: (True, "L'assistant répond à partir des extraits dont le score atteint le seuil."),
    REASON_EXCERPT: (True, "L'assistant répond à partir des extraits dont le score atteint le seuil."),
    REASON_COMMERCIAL: (False, "Question commerciale (prix, délai, stock, remise, paiement ou quantité "
                               "minimale) : l'assistant ne cherche pas dans la base et transmet la question "
                               "à l'équipe commerciale."),
    REASON_NO_MATCH: (False, "Aucun extrait n'atteint le seuil : l'assistant répond qu'il n'a pas cette "
                             "information et propose un conseiller."),
    REASON_EMPTY: (False, "Question vide après nettoyage du texte."),
    REASON_BLOCKED: (False, "La réponse serait bloquée par le filtre de sortie (coordonnées ou secret dans "
                            "l'extrait cité)."),
    REASON_ERROR: (False, "La recherche dans la base de connaissances a échoué (détail dans le journal du "
                          "serveur)."),
}
"""Décision de l'assistant (répond ou non) et explication, pour chaque motif de :class:`tecpap.rag.answer.Answer`."""

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_HANDOFF_COUNT_QUERY = "SELECT COUNT(*) FROM conversations WHERE handoff = 1"
_PHONE_CLIENTS_QUERY = (
    "SELECT id, nom, telephone FROM clients WHERE COALESCE(TRIM(telephone), '') != '' ORDER BY nom"
)
_PHONE_ORDERS_QUERY = """
    SELECT c.id, c.numero_commande, c.statut, c.created_at, c.quantite, c.unite, c.source, c.whatsapp_from,
           cl.nom AS client_nom, cl.telephone AS client_telephone, p.type AS produit_type
    FROM commandes c
    LEFT JOIN clients cl ON c.client_id = cl.id
    LEFT JOIN produits p ON c.produit_id = p.id
    WHERE COALESCE(TRIM(c.whatsapp_from), '') != '' OR COALESCE(TRIM(cl.telephone), '') != ''
    ORDER BY c.created_at DESC, c.id DESC
"""


# ---------------------------------------------------------------------- libellés pour les gabarits

def state_label(state: str | None) -> str:
    """Libellé d'un état de conversation ; l'état brut s'il est inconnu."""
    return STATE_LABELS.get(state or "", state or "Inconnu")


def language_label(code: str | None, short: bool = False) -> str:
    """Libellé d'un code de langue (« darija_latin » → « Darija (lettres latines) »), ou « Non détectée »."""
    if not code:
        return "Non détectée"
    labels = LANGUAGE_SHORT_LABELS if short else LANGUAGE_LABELS
    return labels.get(code, code)


def intent_label(intent: str | None) -> str:
    """Libellé d'une intention de message ; l'intention brute si elle est inconnue."""
    if not intent:
        return ""
    return INTENT_LABELS.get(intent, intent)


def field_label(name: str | None) -> str:
    """Libellé d'un champ de commande (« entreprise_cliente » → « Client »)."""
    return FIELD_LABELS.get(name or "", name or "")


def event_kind_label(kind: str | None) -> str:
    """Libellé d'une nature d'événement de sécurité ; la nature brute si elle est inconnue."""
    return EVENT_KIND_LABELS.get(kind or "", kind or "")


def channel_label(channel: str | None) -> str:
    """Libellé d'un canal de réception (« whatsapp » → « WhatsApp »)."""
    return CHANNEL_LABELS.get(channel or "", channel or "")


def handoff_conversation_count() -> int:
    """Nombre de conversations transférées à l'équipe commerciale (0 si la base est indisponible).

    Sert à la pastille du menu latéral : une erreur de lecture ne doit pas empêcher l'affichage
    d'une page.
    """
    try:
        connection = get_services().db.connection
        if connection is None:
            return 0
        row = connection.execute(_HANDOFF_COUNT_QUERY).fetchone()
    except (sqlite3.Error, RuntimeError, KeyError):
        logger.debug("Nombre de conversations à reprendre illisible", exc_info=True)
        return 0
    return int(row[0]) if row else 0


@bp.app_context_processor
def inject_assistant_labels() -> dict[str, Any]:
    """Fonctions de libellés et nombre de conversations à reprendre, pour tous les gabarits."""
    return {
        "handoff_conversation_count": handoff_conversation_count,
        "state_label": state_label,
        "language_label": language_label,
        "intent_label": intent_label,
        "field_label": field_label,
        "event_kind_label": event_kind_label,
        "channel_label": channel_label,
    }


@bp.app_template_filter("timestamp")
def format_timestamp(value: Any) -> str:
    """Date « AAAA-MM-JJ HH:MM:SS » affichée « JJ/MM/AAAA HH:MM » ; une autre valeur est affichée telle quelle."""
    if not value:
        return "—"
    text = str(value)
    try:
        moment = datetime.strptime(text[:19], _TIMESTAMP_FORMAT)
    except ValueError:
        return text
    return moment.strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------- numéros et commandes

def phone_key(raw: str | None) -> str | None:
    """Clé de comparaison d'un numéro (celle des conversations : « 06 12… » → « +2126… »), ou ``None``."""
    if not raw:
        return None
    try:
        return conversation_key(str(raw))
    except ValueError:
        return None


def clients_by_phone(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Clients qui ont un téléphone, regroupés par clé de numéro (:func:`phone_key`)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(_PHONE_CLIENTS_QUERY).fetchall():
        key = phone_key(row[2])
        if key:
            grouped.setdefault(key, []).append({"id": int(row[0]), "nom": row[1]})
    return grouped


def orders_for_phone(connection: sqlite3.Connection, phone: str) -> list[dict[str, Any]]:
    """Commandes d'un numéro, des plus récentes aux plus anciennes.

    Une commande est retenue quand elle a été envoyée depuis ce numéro (colonne
    ``whatsapp_from``) ou que son client a ce numéro pour téléphone. Les numéros sont comparés
    sous leur forme internationale, quelle que soit leur écriture en base.
    """
    target = phone_key(phone)
    if target is None:
        return []
    cursor = connection.execute(_PHONE_ORDERS_QUERY)
    names = [column[0] for column in cursor.description]
    orders = []
    for row in cursor.fetchall():
        order = dict(zip(names, tuple(row), strict=True))
        if target in (phone_key(order["whatsapp_from"]), phone_key(order["client_telephone"])):
            orders.append(order)
    return orders


# ---------------------------------------------------------------------- conversations

@bp.route("/conversations")
def conversations():
    """Liste des conversations : transférées d'abord, puis les plus récentes (``?handoff=1`` : transférées seules)."""
    services = get_services()
    store = services.conversation_store()
    handoff_only = request.args.get("handoff") == "1"
    waiting = store.list_conversations(CONVERSATION_MAX_LIMIT, handoff_only=True)
    rows = list(waiting)
    if not handoff_only:
        rows += [row for row in store.list_conversations(CONVERSATION_LIST_LIMIT) if not row["handoff"]]
    clients = clients_by_phone(services.db.connection)
    for row in rows:
        row["clients"] = clients.get(row["phone"], [])
    return render_template(
        "conversations.html",
        conversations=rows,
        handoff_only=handoff_only,
        handoff_total=len(waiting),
        list_limit=CONVERSATION_LIST_LIMIT,
        inbox_counts=_inbox_counts(services),
    )


def _inbox_counts(services: Services) -> dict[str, int] | None:
    """Nombre de messages de la file durable par statut, ou ``None`` si la file est illisible."""
    try:
        return services.inbox().counts()
    except (sqlite3.Error, RuntimeError):
        logger.warning("File des messages WhatsApp reçus illisible", exc_info=True)
        return None


def _draft_fields(draft: Any) -> list[tuple[str, Any]]:
    """Champs renseignés de la commande en cours de saisie, avec leur libellé."""
    if not isinstance(draft, Mapping) or not isinstance(draft.get("order"), Mapping):
        return []
    return [(field_label(name), value) for name, value in draft["order"].items()
            if value not in (None, "", [], {})]


def _render_thread(conversation_id: int, *, reply_text: str = "", reply_error: str | None = None,
                   status: int = 200):
    """Fiche d'une conversation ; redirige vers la liste si elle n'existe pas."""
    services = get_services()
    thread = services.conversation_store().get_thread(conversation_id, THREAD_MESSAGE_LIMIT)
    if thread is None:
        return redirect(url_for(".conversations"))
    conversation = thread["conversation"]
    connection = services.db.connection
    orders = orders_for_phone(connection, conversation["phone"])
    draft = conversation.get("draft")
    settings = services.settings
    return render_template(
        "conversation_detail.html",
        conversation=conversation,
        messages=thread["messages"],
        message_limit=THREAD_MESSAGE_LIMIT,
        draft_fields=_draft_fields(draft),
        draft_asked=draft.get("asked") if isinstance(draft, Mapping) else None,
        clients=clients_by_phone(connection).get(conversation["phone"], []),
        orders=orders[:PHONE_ORDERS_PREVIEW],
        order_count=len(orders),
        reply_text=reply_text,
        reply_error=reply_error,
        reply_max_chars=AGENT_REPLY_MAX_CHARS,
        whatsapp_configured=bool(settings.twilio_account_sid and settings.twilio_auth_token),
    ), status


@bp.route("/conversations/<int:conversation_id>")
def conversation_detail(conversation_id: int):
    """Fiche d'une conversation : messages, commande en cours, commandes du numéro, actions de l'équipe."""
    return _render_thread(conversation_id)


def _conversation_or_404(services: Services, conversation_id: int):
    conversation = services.conversation_store().get(conversation_id)
    if conversation is None:
        abort(404)
    return conversation


@bp.route("/conversations/<int:conversation_id>/reset", methods=["POST"])
def reset_conversation(conversation_id: int):
    """Termine la prise en charge : état ``idle``, sans transfert ni commande en cours."""
    services = get_services()
    conversation = _conversation_or_404(services, conversation_id)
    lock = services.sender_lock(conversation.phone)
    if not lock.acquire(timeout=SENDER_LOCK_TIMEOUT_SECONDS):
        flash(BUSY_CONVERSATION_MESSAGE, "error")
        return redirect(url_for(".conversation_detail", conversation_id=conversation_id))
    try:
        services.conversation_store().reset(conversation.phone)
    finally:
        lock.release()
    logger.info("Prise en charge de la conversation %s terminée par %s", conversation_id,
                current_user() or "l'équipe commerciale")
    flash("Prise en charge terminée : l'assistant répond de nouveau à ce client.", "success")
    return redirect(url_for(".conversation_detail", conversation_id=conversation_id))


def _clean_reply(raw: str | None) -> str:
    """Texte saisi par l'équipe commerciale : fins de ligne unifiées, espaces de début et de fin retirées."""
    return (raw or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()


@bp.route("/conversations/<int:conversation_id>/reply", methods=["POST"])
def reply_to_conversation(conversation_id: int):
    """Envoie au client, par WhatsApp, un message de l'équipe commerciale et l'enregistre dans la conversation."""
    services = get_services()
    conversation = _conversation_or_404(services, conversation_id)
    text = _clean_reply(request.form.get("message"))
    if not text:
        return _render_thread(conversation_id, reply_error=EMPTY_REPLY_MESSAGE, status=400)
    if len(text) > AGENT_REPLY_MAX_CHARS:
        error = (f"Le message compte {len(text)} caractères : un message WhatsApp en compte au plus "
                 f"{AGENT_REPLY_MAX_CHARS}.")
        return _render_thread(conversation_id, reply_text=text, reply_error=error, status=400)
    try:
        sent = services.whatsapp.send_reply(conversation.phone, text)
    except Exception:
        logger.warning("Réponse de l'équipe commerciale non envoyée (conversation %s)", conversation_id,
                       exc_info=True)
        sent = None
    if not sent:
        return _render_thread(conversation_id, reply_text=text, reply_error=SEND_FAILED_MESSAGE, status=502)
    services.conversation_store().add_message(conversation.id, "out", text, intent=AGENT_INTENT)
    logger.info("Réponse de l'équipe commerciale envoyée (conversation %s, par %s)", conversation_id,
                current_user() or "l'équipe commerciale")
    flash("Message envoyé au client par WhatsApp.", "success")
    return redirect(url_for(".conversation_detail", conversation_id=conversation_id))


# ---------------------------------------------------------------------- base de connaissances

@dataclass(frozen=True)
class DocumentSummary:
    """Document indexé : identifiant, titre, fichier, nombre d'extraits et titres de ses sections."""

    doc_id: str
    title: str
    source_path: str
    chunks: int
    sections: tuple[str, ...]


@dataclass(frozen=True)
class IndexStatus:
    """État de l'index par rapport au dossier des documents.

    Attributes:
        readable: faux quand le dossier est absent ou illisible (les autres valeurs valent 0).
        changed: extraits du dossier absents de l'index ou modifiés depuis l'indexation.
        removed: extraits indexés qui n'existent plus dans le dossier.
        other_space: extraits indexés dans un autre espace vectoriel que celui du calcul configuré.
        problem: explication quand ``readable`` est faux.
    """

    readable: bool
    changed: int = 0
    removed: int = 0
    other_space: int = 0
    problem: str = ""

    @property
    def up_to_date(self) -> bool:
        """Vrai quand une réindexation ne modifierait pas l'index."""
        return self.readable and not (self.changed or self.removed or self.other_space)


@dataclass(frozen=True)
class SearchPreview:
    """Résultat de la recherche de test.

    Attributes:
        question: question après nettoyage (celle que l'assistant chercherait).
        language: langue de réponse déduite de la question (``fr``, ``darija``, ``ar``).
        hits: extraits trouvés, dans l'ordre de la fusion des classements.
        best: meilleur score de pertinence des extraits.
        answer: décision de :class:`tecpap.rag.answer.KnowledgeAnswerer` sans modèle de langue.
        failed: vrai quand la recherche a levé une exception.
    """

    question: str
    language: str
    hits: list[SearchHit] = field(default_factory=list)
    best: float = 0.0
    answer: Answer | None = None
    failed: bool = False

    @property
    def would_answer(self) -> bool:
        """Vrai quand l'assistant répondrait à partir de la base de connaissances."""
        return self.answer is not None and ANSWER_DECISIONS.get(self.answer.reason, (False, ""))[0]

    @property
    def explanation(self) -> str:
        """Explication de la décision, en français."""
        if self.answer is None:
            return ANSWER_DECISIONS[REASON_ERROR][1]
        return ANSWER_DECISIONS.get(self.answer.reason, (False, self.answer.reason))[1]


def _summaries(kb: KnowledgeBase, stored: list[StoredChunk]) -> list[DocumentSummary]:
    sections: dict[str, list[str]] = {}
    for item in stored:
        names = sections.setdefault(item.chunk.doc_id, [])
        if item.chunk.section not in names:
            names.append(item.chunk.section)
    return [DocumentSummary(document.doc_id, document.title, document.source_path, document.chunks,
                            tuple(sections.get(document.doc_id, ())))
            for document in kb.documents()]


def _spaces(stored: list[StoredChunk]) -> dict[str, int]:
    """Nombre d'extraits indexés par espace vectoriel."""
    counts: dict[str, int] = {}
    for item in stored:
        counts[item.embedder] = counts.get(item.embedder, 0) + 1
    return counts


def index_status(knowledge_dir: str | Path, stored: list[StoredChunk], embedder_name: str) -> IndexStatus:
    """Compare l'index aux documents du dossier sans rien modifier (voir :class:`IndexStatus`)."""
    directory = Path(knowledge_dir)
    if not directory.is_dir():
        return IndexStatus(False, problem="dossier de la base de connaissances introuvable")
    try:
        chunks = load_chunks(directory)
    except (OSError, ValueError):
        logger.warning("Lecture du dossier de la base de connaissances impossible", exc_info=True)
        return IndexStatus(False, problem="lecture du dossier de la base de connaissances impossible")
    on_disk = {(chunk.doc_id, chunk.chunk_index): chunk.content_hash for chunk in chunks}
    indexed = {(item.chunk.doc_id, item.chunk.chunk_index): item.chunk.content_hash for item in stored}
    return IndexStatus(
        True,
        changed=sum(1 for key, digest in on_disk.items() if indexed.get(key) != digest),
        removed=len(indexed.keys() - on_disk.keys()),
        other_space=sum(1 for item in stored if item.embedder != embedder_name),
    )


def search_preview(services: Services, kb: KnowledgeBase, query: str) -> SearchPreview:
    """Recherche de test : extraits trouvés et décision de l'assistant, sans appel au modèle de langue.

    La question est nettoyée comme celle d'un client (:func:`tecpap.security.text.sanitize_text`)
    et la décision est celle de :class:`tecpap.rag.answer.KnowledgeAnswerer` construit sans
    client de modèle de langue : mêmes seuil, nombre d'extraits et refus des questions commerciales.
    """
    question = sanitize_text(query, max_chars=MAX_QUESTION_CHARS).replace("\n", " ").strip()
    language = reply_language(detect_language(question))
    try:
        hits = kb.search(question, k=services.settings.rag_top_k)
    except Exception:
        logger.exception("Recherche de test dans la base de connaissances en échec")
        return SearchPreview(question, language, failed=True)
    answer = KnowledgeAnswerer(kb, None, services.router, services.settings).answer(question, language)
    return SearchPreview(question, language, hits, best_score(hits), answer)


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)


@bp.route("/knowledge")
def knowledge():
    """Base de connaissances : documents indexés, état de l'index, réindexation et recherche de test (``?q=``)."""
    services = get_services()
    settings = services.settings
    kb = services.knowledge_base()
    stored = kb.store.all()
    query = (request.args.get("q") or "").strip()[:MAX_QUESTION_CHARS]
    return render_template(
        "knowledge.html",
        documents=_summaries(kb, stored),
        chunk_count=kb.chunk_count(),
        spaces=_spaces(stored),
        embedder_name=services.embedder.name,
        embedder_setting=settings.rag_embedder,
        status=index_status(settings.knowledge_dir, stored, services.embedder.name),
        last_index=current_app.extensions.get(LAST_INDEX_KEY),
        query=query,
        search=search_preview(services, kb, query) if query else None,
        min_score=settings.rag_min_score,
        top_k=settings.rag_top_k,
        rag_enabled=settings.rag_enabled,
        chatbot_enabled=settings.chatbot_enabled,
        knowledge_dir=settings.knowledge_dir,
    )


@bp.route("/knowledge/reindex", methods=["POST"])
def reindex_knowledge():
    """Met l'index à jour (``force=1`` : recalcule tous les vecteurs) et garde le bilan pour la page."""
    services = get_services()
    force = request.form.get("force") == "1"
    if not Path(services.settings.knowledge_dir).is_dir():
        flash("Réindexation impossible : le dossier de la base de connaissances est introuvable.", "error")
        return redirect(url_for(".knowledge"))
    stats = services.index_knowledge_base(force=force)
    if stats is None:
        flash("La réindexation a échoué. Le détail est consigné dans le journal du serveur.", "error")
        return redirect(url_for(".knowledge"))
    current_app.extensions[LAST_INDEX_KEY] = {
        "stats": stats, "at": _timestamp_now(), "user": current_user(), "force": force,
    }
    logger.info("Base de connaissances réindexée depuis le tableau de bord par %s : %s",
                current_user() or "l'équipe commerciale", stats)
    flash(f"Réindexation terminée : {stats}.", "success")
    return redirect(url_for(".knowledge"))


# ---------------------------------------------------------------------- sécurité

@bp.route("/security")
def security():
    """Événements de sécurité récents (``?kind=`` : une seule nature) et décompte par nature sur sept jours."""
    services = get_services()
    connection = services.db.connection
    kind = (request.args.get("kind") or "").strip()[:LABEL_MAX_CHARS] or None
    events = recent_security_events(connection, SECURITY_EVENT_LIMIT, kind=kind)
    seen = {event["kind"] for event in recent_security_events(connection, SECURITY_MAX_LIMIT)}
    if kind:
        seen.add(kind)
    kinds = list(EVENT_KIND_LABELS) + sorted(seen - set(EVENT_KIND_LABELS))
    since = datetime.now(timezone.utc) - timedelta(days=SECURITY_WINDOW_DAYS)
    counts = [{"kind": name, "count": count_security_events(connection, kind=name, since=since)} for name in kinds]
    return render_template(
        "security.html",
        events=events,
        current_kind=kind,
        counts=counts,
        week_total=count_security_events(connection, since=since),
        window_days=SECURITY_WINDOW_DAYS,
        event_limit=SECURITY_EVENT_LIMIT,
    )
