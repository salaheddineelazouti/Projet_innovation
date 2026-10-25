"""Pages HTML du tableau de bord : accueil, commandes, clients, statistiques, alertes, WhatsApp, SAGE X3.

Les pages de l'assistant WhatsApp (conversations, base de connaissances, sécurité) sont dans
:mod:`tecpap.web.routes.assistant`.

Ce module fournit aussi à tous les gabarits :

- les filtres Jinja de mise en forme française (voir :mod:`tecpap.formatting`) :
  ``format_quantity`` (« 10 000 »), ``format_amount`` (« 1 234,50 MAD »), ``format_datetime``
  et ``format_date`` (horodatage UTC affiché dans le fuseau horaire de l'entreprise),
  ``order_reference`` (référence d'une commande telle que le client la reçoit : son numéro,
  sinon « CMD-<id> ») ;
- les variables ``app_version`` (version du paquet) et ``current_year`` (année en cours, pour le
  pied de page).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, redirect, render_template, request, url_for

from tecpap import __version__, formatting
from tecpap.chatbot.tools import order_reference
from tecpap.extraction.rules import CANONICAL_UNITS
from tecpap.reporting.analytics import AIPredictor, AlertSystem, Analytics, ClientHistory
from tecpap.web.routes.assistant import orders_for_phone
from tecpap.web.services import Services, get_services

logger = logging.getLogger(__name__)

bp = Blueprint("pages", __name__)

TREND_DAYS = 7

ORDER_SOURCES: Mapping[str, str] = {"whatsapp": "WhatsApp", "email": "Email"}
"""Canaux du filtre ``?source=`` de la liste des commandes, avec leur libellé."""

MAX_SEARCH_LENGTH = 100
"""Longueur maximale du texte de recherche de la liste des commandes."""

MIN_PHONE_SEARCH_DIGITS = 6
"""Nombre minimal de chiffres d'une recherche par numéro de téléphone."""

# Unités proposées dans le formulaire de modification d'une commande : les unités canoniques des
# règles de normalisation (tecpap.extraction.rules.CANONICAL_UNITS), dans cet ordre.
_UNIT_LABELS: dict[str, str] = {
    "US": "US (unité de stock SAGE X3)",
    "pièces": "Pièces",
    "kg": "Kilogrammes (kg)",
    "carton": "Cartons",
    "paquet": "Paquets",
}
UNIT_CHOICES: tuple[tuple[str, str], ...] = tuple(
    [(unit, label) for unit, label in _UNIT_LABELS.items() if unit in CANONICAL_UNITS]
    + [(unit, unit) for unit in sorted(CANONICAL_UNITS - set(_UNIT_LABELS))]
)
"""Couples (valeur enregistrée, libellé) de la liste « Unité » ; une autre valeur en base est ajoutée à la liste."""

BAG_TYPE_CHOICES: tuple[str, ...] = ("KRAFT", "KRAFT BLANCHI", "KRAFT ECRU", "PAPIER")
"""Valeurs proposées pour le type de sac ; une autre valeur en base est ajoutée à la liste."""

ALERT_LABELS: Mapping[str, str] = {
    "urgent": "Commande urgente",
    "high_quantity": "Grande quantité",
    "pending_too_long": "Commande en attente depuis longtemps",
    "suspicious": "Nouveau client avec une grande commande",
    "low_confidence": "Extraction peu fiable",
}
"""Titre de chaque type d'alerte de :class:`~tecpap.reporting.analytics.AlertSystem`."""

CLIENT_VALUE_LABELS: Mapping[str, str] = {"high": "Élevée", "medium": "Moyenne", "low": "Faible"}
"""Libellé de la valeur d'un client estimée par :class:`~tecpap.reporting.analytics.AIPredictor`."""

_REFERENCE_SEARCH_RE = re.compile(r"^(?:cmd\s*-?\s*|#)(\d+)$")
_ORDERS_URL_PARAMETERS = ("status", "source", "q", "conversation")


# ---------------------------------------------------------------------- filtres et variables des gabarits

def _display_timezone() -> str:
    """Fuseau horaire d'affichage des dates : réglage ``timezone`` de la configuration, sinon Casablanca."""
    try:
        settings = get_services().settings
    except (RuntimeError, KeyError):
        return formatting.DEFAULT_TIMEZONE
    return getattr(settings, "timezone", None) or formatting.DEFAULT_TIMEZONE


@bp.app_template_filter("format_quantity")
def format_quantity_filter(value: Any, unit: str | None = None) -> str:
    """Quantité à la française (« 10 000 », « 2,5 kg »), suivie de l'unité si elle est donnée."""
    return formatting.format_quantity(value, unit)


@bp.app_template_filter("format_amount")
def format_amount_filter(value: Any, currency: str | None = "MAD") -> str:
    """Montant avec deux décimales et sa devise (« 1 234,50 MAD »)."""
    return formatting.format_amount(value, currency or "MAD")


@bp.app_template_filter("format_datetime")
def format_datetime_filter(value: Any, with_time: bool = True) -> str:
    """Horodatage UTC de la base affiché dans le fuseau horaire de l'entreprise (« 25/10/2026 15:03 »)."""
    return formatting.format_datetime(value, _display_timezone(), with_time=with_time)


@bp.app_template_filter("format_date")
def format_date_filter(value: Any) -> str:
    """Date seule (« 25/10/2026 ») d'un horodatage UTC, dans le fuseau horaire de l'entreprise."""
    return formatting.format_datetime(value, _display_timezone(), with_time=False)


@bp.app_template_filter("order_reference")
def order_reference_filter(order: Mapping[str, Any]) -> str:
    """Référence d'une commande telle que le client la reçoit : son numéro, sinon « CMD-<id> »."""
    return order_reference(order.get("numero_commande"), order.get("id"))


@bp.app_context_processor
def inject_footer_values() -> dict[str, Any]:
    """Version du paquet et année en cours (dans le fuseau horaire de l'entreprise), pour le pied de page."""
    now = datetime.now(timezone.utc).astimezone(formatting.resolve_timezone(_display_timezone()))
    return {"app_version": __version__, "current_year": now.year}


# ---------------------------------------------------------------------- accueil

@bp.route("/")
def index():
    """Tableau de bord : indicateurs, dernières commandes, meilleurs clients et produits, tendance sur 7 jours."""
    db = get_services().db
    stats = db.get_stats()
    recent_orders = db.get_all_orders()[:5]
    top_clients = db.get_top_clients(5)
    top_products = db.get_top_products(5)

    trend = {item["date"]: item["count"] for item in db.get_orders_trend(TREND_DAYS)}
    today = datetime.now().date()
    labels, data = [], []
    for days_ago in range(TREND_DAYS - 1, -1, -1):
        day = today - timedelta(days=days_ago)
        labels.append(day.strftime("%d/%m"))
        data.append(trend.get(day.strftime("%Y-%m-%d"), 0))

    return render_template(
        "index.html",
        stats=stats,
        recent_orders=recent_orders,
        top_clients=top_clients,
        top_products=top_products,
        trend_labels=labels,
        trend_data=data,
    )


# ---------------------------------------------------------------------- liste des commandes

@bp.route("/orders")
def orders_list():
    """Liste des commandes, filtrable par statut, canal, texte recherché et numéro WhatsApp.

    - ``?status=en_attente`` (``validee``, ``rejetee``) : statut ;
    - ``?source=whatsapp`` ou ``?source=email`` : canal de réception ;
    - ``?q=…`` : référence (« CMD-42 », « #42 », numéro de commande), nom ou email du client,
      ou numéro de téléphone (comparé sous sa forme internationale) ;
    - ``?conversation=<id>`` : commandes du numéro de cette conversation, envoyées depuis ce
      numéro ou passées par un client dont c'est le téléphone. L'identifiant de la conversation
      figure dans l'adresse à la place du numéro, qui n'apparaît donc pas dans les journaux du
      serveur web.
    """
    status_filter = request.args.get("status") or None
    source_filter = request.args.get("source") if request.args.get("source") in ORDER_SOURCES else None
    search = " ".join((request.args.get("q") or "").split())[:MAX_SEARCH_LENGTH] or None
    services = get_services()
    db = services.db
    orders = db.get_all_orders(status=status_filter) if status_filter else db.get_all_orders()
    _add_client_contacts(db.connection, orders)
    conversation = _filter_conversation(services, request.args.get("conversation", type=int))
    if conversation is not None:
        kept = {order["id"] for order in orders_for_phone(db.connection, conversation.phone)}
        orders = [order for order in orders if order["id"] in kept]
    if source_filter:
        orders = [order for order in orders if order_source(order) == source_filter]
    if search:
        orders = [order for order in orders if order_matches(order, search)]

    filters = {"status": status_filter, "source": source_filter, "q": search,
               "conversation": conversation.id if conversation is not None else None}
    return render_template(
        "orders.html",
        orders=orders,
        current_filter=status_filter,
        current_source=source_filter,
        current_search=search,
        current_conversation=conversation,
        order_sources=ORDER_SOURCES,
        orders_url=lambda **changes: _orders_url(filters, **changes),
    )


def _orders_url(filters: Mapping[str, Any], **changes: Any) -> str:
    """Adresse de la liste des commandes avec les filtres actuels modifiés par ``changes``.

    Les paramètres sont écrits dans un ordre fixe (statut, canal, recherche, conversation) ; un
    filtre à ``None`` est retiré de l'adresse.
    """
    values = {**filters, **changes}
    return url_for("pages.orders_list", **{name: values.get(name) for name in _ORDERS_URL_PARAMETERS})


def order_source(order: Mapping[str, Any]) -> str:
    """Canal d'une commande : ``whatsapp`` (colonne ``source`` ou sujet « WhatsApp… »), sinon ``email``.

    La règle est celle de la page WhatsApp : les commandes enregistrées avant la colonne
    ``source`` sont reconnues à leur sujet.
    """
    if order.get("source") == "whatsapp":
        return "whatsapp"
    subject = order.get("email_subject")
    if isinstance(subject, str) and subject.lower().startswith("whatsapp"):
        return "whatsapp"
    return "email"


def _fold(text: str) -> str:
    """Texte sans accents, en minuscules, pour une recherche insensible aux accents et à la casse."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def order_matches(order: Mapping[str, Any], search: str) -> bool:
    """Vrai si la commande correspond au texte recherché.

    Une référence « CMD-42 » ou « #42 » désigne la commande d'identifiant 42 ; sinon le texte est
    cherché (sans tenir compte des accents ni de la casse) dans la référence, le nom et l'email du
    client et l'email de l'expéditeur. Un texte d'au moins six chiffres est aussi cherché dans les
    chiffres du numéro de l'expéditeur WhatsApp et du téléphone du client, sans le préfixe
    national « 0 » ni le préfixe international « 00 » (« 06 12 34 » trouve « +212 612 34… »).
    """
    term = _fold(search.strip())
    if not term:
        return True
    reference = _REFERENCE_SEARCH_RE.match(term)
    if reference:
        return str(order.get("id")) == reference.group(1)
    fields = (order_reference(order.get("numero_commande"), order.get("id")), order.get("client_nom"),
              order.get("client_email"), order.get("email_from"))
    if any(isinstance(value, str) and term in _fold(value) for value in fields):
        return True
    wanted = _phone_digits(search)
    if len(wanted) < MIN_PHONE_SEARCH_DIGITS:
        return False
    numbers = (_phone_digits(order.get("whatsapp_from")), _phone_digits(order.get("client_telephone")))
    return any(wanted in number for number in numbers)


def _phone_digits(value: Any) -> str:
    """Chiffres d'un numéro de téléphone, sans préfixe « 00 » ni « 0 » en tête."""
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if digits.startswith("00"):
        return digits[2:]
    return digits[1:] if digits.startswith("0") else digits


def _add_client_contacts(connection: sqlite3.Connection, orders: Iterable[dict[str, Any]]) -> None:
    """Ajoute l'email et le téléphone du client (``client_email``, ``client_telephone``) à chaque commande."""
    try:
        contacts = {row[0]: (row[1], row[2]) for row in connection.execute("SELECT id, email, telephone FROM clients")}
    except sqlite3.Error:
        logger.warning("Coordonnées des clients illisibles pour la liste des commandes", exc_info=True)
        return
    for order in orders:
        email, telephone = contacts.get(order.get("client_id"), (None, None))
        order.setdefault("client_email", email)
        order.setdefault("client_telephone", telephone)


def _filter_conversation(services: Services, conversation_id: int | None):
    """Conversation du filtre de la liste des commandes, ou ``None`` (filtre absent ou conversation inconnue)."""
    if conversation_id is None:
        return None
    return services.conversation_store().get(conversation_id)


def _conversation_id_for(services: Services, order: dict) -> int | None:
    """Identifiant de la conversation WhatsApp du numéro qui a envoyé la commande, s'il en existe une."""
    sender = order.get("whatsapp_from")
    if not sender:
        return None
    try:
        conversation = services.conversation_store().find(sender)
    except ValueError:
        return None
    except sqlite3.Error:
        logger.warning("Conversation WhatsApp de la commande %s illisible", order.get("id"), exc_info=True)
        return None
    return conversation.id if conversation is not None else None


# ---------------------------------------------------------------------- fiche d'une commande

def choices_with_current(choices: Iterable[tuple[str, str]], current: Any) -> list[tuple[str, str]]:
    """Options d'une liste déroulante, complétées par la valeur enregistrée quand elle n'y figure pas.

    Sans cette option, le navigateur sélectionnerait la première valeur de la liste et un
    enregistrement du formulaire remplacerait la valeur extraite.
    """
    options = list(choices)
    if isinstance(current, str) and current.strip() and current not in {value for value, _ in options}:
        options.append((current, current))
    return options


@bp.route("/orders/<int:order_id>")
def order_detail(order_id: int):
    """Fiche d'une commande, pour la vérification et la validation (avec le contrôle de l'extraction)."""
    services = get_services()
    db = services.db
    order = db.get_order(order_id)
    if not order:
        return redirect(url_for("pages.orders_list"))
    return render_template(
        "order_detail.html",
        order=order,
        products=db.get_all_products(),
        conversation_id=_conversation_id_for(services, order),
        unit_choices=choices_with_current(UNIT_CHOICES, order.get("unite")),
        bag_type_choices=choices_with_current(((value, value) for value in BAG_TYPE_CHOICES), order.get("type_sac")),
    )


# ---------------------------------------------------------------------- traitement des emails

@bp.route("/process")
def process_page():
    """Page de lancement du traitement des emails, avec l'état de la configuration Gmail et OpenAI.

    L'état indique si les identifiants sont renseignés dans la configuration ; aucune connexion
    n'est tentée à l'affichage de la page.
    """
    services = get_services()
    settings = services.settings
    return render_template(
        "process.html",
        gmail_configured=bool(settings.gmail_email and settings.gmail_app_password),
        llm_configured=bool(getattr(services.llm, "available", False)),
    )


# ---------------------------------------------------------------------- statistiques, clients, alertes

@bp.route("/analytics")
def analytics_page():
    """Statistiques détaillées et alertes."""
    db = get_services().db
    stats = Analytics(db, _display_timezone()).get_dashboard_stats()
    alerts = AlertSystem(db).check_alerts()
    return render_template("analytics.html", stats=stats, alerts=alerts)


@bp.route("/clients")
def clients_page():
    """Liste des clients avec leur nombre de commandes et la date de la dernière."""
    clients = get_services().db.get_all_clients()
    return render_template("clients.html", clients=clients)


def _next_order_date(last_order: Any, interval_days: int | None) -> date | None:
    """Date estimée de la prochaine commande : dernière commande + intervalle moyen entre deux commandes."""
    moment = formatting.parse_timestamp(last_order)
    if moment is None or not interval_days:
        return None
    if isinstance(moment, datetime):
        moment = moment.astimezone(formatting.resolve_timezone(_display_timezone())).date()
    return moment + timedelta(days=interval_days)


@bp.route("/clients/<int:client_id>")
def client_detail(client_id: int):
    """Fiche client : historique des commandes et estimation de la prochaine commande (règles fixes)."""
    services = get_services()
    cursor = services.db.connection.cursor()
    cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    row = cursor.fetchone()
    if not row:
        return redirect(url_for("pages.clients_page"))

    client = dict(row)
    client["preferences"] = ClientHistory(services.db).get_client_preferences(client_id=client_id)

    cursor.execute("""
        SELECT c.*, p.type as produit_type
        FROM commandes c
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE c.client_id = ?
        ORDER BY c.created_at DESC
    """, (client_id,))
    orders = [dict(order) for order in cursor.fetchall()]

    predictor = AIPredictor(services.db, settings=services.settings, llm=services.llm)
    prediction = predictor.predict_client_behavior(client_id=client_id)
    if prediction is not None:
        prediction["value_label"] = CLIENT_VALUE_LABELS.get(prediction.get("client_value"), "")
        prediction["next_order_date"] = _next_order_date(
            (client["preferences"] or {}).get("last_order"), prediction.get("predicted_next_order_days")
        )
    return render_template("client_detail.html", client=client, orders=orders, prediction=prediction)


@bp.route("/alerts")
def alerts_page():
    """Alertes : commandes urgentes, volumineuses, anciennes ou douteuses."""
    alerts = AlertSystem(get_services().db).check_alerts()
    for alert in alerts:
        alert["label"] = ALERT_LABELS.get(alert.get("type"), "Alerte")
    return render_template("alerts.html", alerts=alerts)


def _count(cursor, query: str) -> int:
    cursor.execute(query)
    return cursor.fetchone()[0]


# ---------------------------------------------------------------------- WhatsApp

@bp.route("/whatsapp")
def whatsapp_page():
    """Commandes reçues par WhatsApp, statistiques du canal, numéro d'envoi et adresse du webhook Twilio."""
    services = get_services()
    cursor = services.db.connection.cursor()

    cursor.execute("""
        SELECT c.*, cl.nom as client_nom, cl.telephone, p.type as produit_type
        FROM commandes c
        LEFT JOIN clients cl ON c.client_id = cl.id
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE c.source = 'whatsapp' OR c.email_subject LIKE 'WhatsApp%'
        ORDER BY c.created_at DESC
        LIMIT 20
    """)
    whatsapp_orders = [dict(row) for row in cursor.fetchall()]

    total = _count(cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp'")
    validated = _count(cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'validee'")
    whatsapp_stats = {
        "total": total,
        "pending": _count(cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'en_attente'"),
        "validated": validated,
        "today": _count(
            cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND DATE(created_at) = DATE('now')"
        ),
        "this_week": _count(
            cursor, "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND created_at >= DATE('now', '-7 days')"
        ),
        "this_month": _count(
            cursor,
            "SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND created_at >= DATE('now', 'start of month')",
        ),
        "validation_rate": round((validated / total * 100) if total > 0 else 0),
    }

    cursor.execute("""
        SELECT cl.id, cl.nom, COUNT(c.id) as total_orders
        FROM clients cl
        JOIN commandes c ON cl.id = c.client_id
        WHERE c.source = 'whatsapp'
        GROUP BY cl.id
        ORDER BY total_orders DESC
        LIMIT 5
    """)
    whatsapp_clients = [dict(row) for row in cursor.fetchall()]

    settings = services.settings
    base_url = settings.public_base_url
    webhook_url = f"{base_url.rstrip('/')}/webhook/whatsapp" if base_url else None
    sender_number = (settings.twilio_whatsapp_number or "").removeprefix("whatsapp:").strip() or None

    return render_template(
        "whatsapp.html",
        whatsapp_orders=whatsapp_orders,
        whatsapp_stats=whatsapp_stats,
        whatsapp_clients=whatsapp_clients,
        webhook_url=webhook_url,
        whatsapp_number=sender_number,
        twilio_configured=bool(settings.twilio_account_sid and settings.twilio_auth_token),
    )


# ---------------------------------------------------------------------- SAGE X3

@bp.route("/sage")
def sage_export_page():
    """Préparation de l'export SAGE X3 : aperçu des commandes et part des codes articles renseignés."""
    db = get_services().db
    stats = db.get_stats()
    orders = db.get_all_orders()
    cursor = db.connection.cursor()
    stats["with_code_article"] = _count(
        cursor, "SELECT COUNT(*) FROM commandes WHERE code_article IS NOT NULL AND code_article != ''"
    )
    return render_template("sage_export.html", stats=stats, orders=orders)
