"""Outils de l'assistant WhatsApp : seul accès de l'assistant aux données, limité à l'expéditeur.

L'autorisation est appliquée dans le code, jamais confiée au modèle de langue :

- :class:`AssistantTools` est construit pour un expéditeur ; son numéro est fixé à la
  construction et aucune méthode ne prend de numéro, de client ou d'identifiant interne en
  paramètre. Les schémas d'outils transmis au modèle (:meth:`AssistantTools.tool_specs`) n'en
  déclarent pas non plus ; un argument inattendu (« phone », « client »…) est ignoré ;
- chaque requête SQL est paramétrée et filtre elle-même sur l'expéditeur (voir ci-dessous) ;
- les résultats sont réduits au minimum : référence (``numero_commande``, ou « CMD-<id> »),
  date, produit, quantité, unité et statut ; jamais de prix, jamais de coordonnées ni de
  commande d'un autre client ;
- le modèle n'a accès qu'à des outils de lecture et à la demande d'un conseiller : une commande
  n'est enregistrée que par :meth:`AssistantTools.submit_order`, appelée par l'automate de
  conversation après l'accord explicite du client, toujours au statut « en_attente ».

Commandes visibles par un expéditeur :

1. les commandes envoyées depuis son numéro (colonne ``whatsapp_from``) ;
2. les commandes d'un client dont le téléphone est ce numéro, à condition que le lien entre le
   numéro et le client ait été confirmé par l'équipe commerciale : au moins une commande
   WhatsApp de ce numéro pour ce client a été validée. Le téléphone d'un expéditeur n'est
   rattaché à un client existant qu'à la validation d'une de ses commandes
   (:meth:`tecpap.db.database.DatabaseManager.attach_phone_to_client`, appelée par l'API de
   validation) ; la condition protège en plus les téléphones enregistrés autrement (saisie,
   bases plus anciennes). Sans ces deux règles, un expéditeur pourrait passer commande au nom
   d'une entreprise, puis consulter les commandes de cette entreprise reçues par email.

Les numéros sont comparés sous une forme normalisée des deux côtés : préfixe « whatsapp: »,
espaces, tirets, points, parenthèses et préfixe interurbain « (0) » retirés dans la colonne, et
variantes du numéro de l'expéditeur (« +212… », « 212… », « 00212… », « 06… ») en paramètres.

Les erreurs d'un outil sont journalisées et remplacées par un message générique : aucune trace
d'exécution n'est renvoyée au modèle ni au client.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tecpap.catalog import PRODUCTS, REQUIRED_ORDER_FIELDS
from tecpap.db.database import is_generic_client_name
from tecpap.extraction.rules import ORDER_FIELD_KINDS, fold, normalize_phone, normalize_product_type

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_RECENT_ORDERS",
    "ORDER_METADATA_FIELDS",
    "TOOL_NAMES",
    "VISIBLE_STATUSES",
    "AssistantTools",
    "ToolResult",
    "order_reference",
    "phone_variants",
]

TOOL_NAMES: tuple[str, ...] = (
    "my_recent_orders", "my_order_status", "my_last_validated_order", "search_catalog", "request_human",
)
"""Outils proposés au modèle de langue (lecture seule, et demande d'un conseiller)."""

MAX_RECENT_ORDERS = 10
"""Nombre maximal de commandes renvoyées par :meth:`AssistantTools.my_recent_orders`."""

VISIBLE_STATUSES: tuple[str, ...] = ("en_attente", "validee", "rejetee")
"""Statuts des commandes montrées au client (une commande supprimée ne l'est jamais)."""

MAX_REFERENCE_CHARS = 40
MAX_QUERY_CHARS = 300
MAX_PRODUCT_CHARS = 80
MAX_MESSAGE_SID_CHARS = 200

ORDER_METADATA_FIELDS: tuple[str, ...] = (
    "message_brut", "langue", "modele_llm", "routage_modele", "anomalies", "champs_manquants",
)
"""Informations de traçabilité transmises avec une commande.

Ce sont les colonnes de :data:`tecpap.db.database.ORDER_AUDIT_FIELDS`, hors ``message_sid``,
fixé par :meth:`AssistantTools.submit_order`.
"""

_SUBMITTABLE_FIELDS = frozenset(name for name in ORDER_FIELD_KINDS if name != "est_bon_commande") \
    | frozenset(ORDER_METADATA_FIELDS)
_LIST_METADATA_FIELDS = frozenset({"anomalies", "champs_manquants"})
_REORDER_FIELDS = ("nature_produit", "quantite", "unite", "type_papier", "grammage", "laize", "format_sac",
                   "type_sac", "impression_client", "code_article")
_REFERENCE_ID_RE = re.compile(r"(?:CMD-?)?(\d{1,12})")
_REFERENCE_CHARS_RE = re.compile(r"[^\w/\-]")
_CLIENT_LIMIT = 50


@dataclass(frozen=True)
class ToolResult:
    """Résultat de :meth:`AssistantTools.execute`.

    Attributes:
        name: nom de l'outil demandé.
        ok: faux quand l'outil est inconnu, que ses arguments sont invalides ou qu'il a échoué.
        data: résultat minimal (sérialisable en JSON).
        error: message d'erreur générique en français, sans détail technique.
        answer: réponse de la base de connaissances pour ``search_catalog`` (objet
            :class:`tecpap.rag.answer.Answer`), sinon ``None``.
    """

    name: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    answer: Any = None


def phone_variants(phone: str) -> tuple[str, ...]:
    """Écritures d'un numéro E.164 comparées aux colonnes normalisées (« +212… », « 212… », « 00212… », « 0… »)."""
    digits = phone.lstrip("+")
    variants = [f"+{digits}", digits, f"00{digits}"]
    if digits.startswith("212") and len(digits) == 12:
        variants.append("0" + digits[3:])
    return tuple(variants)


def _normalized_phone_sql(column: str) -> str:
    """Expression SQL qui retire d'une colonne de téléphone le préfixe « whatsapp: » et les séparateurs.

    Les espaces sont retirées en premier, pour que « WhatsApp : +212… » perde aussi son préfixe.
    """
    expression = f"LOWER(TRIM(COALESCE({column}, '')))"
    for removed in (" ", "whatsapp:", "(0)", "-", ".", "(", ")"):
        expression = f"REPLACE({expression}, '{removed}', '')"
    return expression


def order_reference(numero_commande: Any, order_id: Any) -> str:
    """Référence d'une commande montrée au client : son numéro, sinon « CMD-<id> »."""
    if isinstance(numero_commande, str) and numero_commande.strip():
        return numero_commande.strip()
    return f"CMD-{order_id}"


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else float(value)


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    names = [column[0] for column in cursor.description or ()]
    return [dict(zip(names, tuple(row), strict=True)) for row in cursor.fetchall()]


class AssistantTools:
    """Accès aux données de l'assistant pour un expéditeur donné.

    Args:
        conn: connexion SQLite de la base des commandes.
        db_manager: :class:`tecpap.db.database.DatabaseManager` (création des commandes).
        sender_phone: numéro de l'expéditeur, tel que reçu de Twilio (« whatsapp:+2126… ») ;
            il est fixé pour toute la vie de l'objet.
        knowledge_answerer: :class:`tecpap.rag.answer.KnowledgeAnswerer` qui répond aux questions
            sur les produits ; sans lui, :meth:`search_catalog` cherche dans le catalogue.
        language: langue de réponse du client (``fr``, ``darija``, ``ar``).

    Raises:
        ValueError: le numéro de l'expéditeur n'est pas un numéro de téléphone reconnu.
    """

    def __init__(self, conn: sqlite3.Connection, db_manager: Any, sender_phone: str,
                 knowledge_answerer: Any = None, *, language: str = "fr"):
        phone = normalize_phone(sender_phone) if isinstance(sender_phone, str) else None
        if phone is None:
            raise ValueError("numéro d'expéditeur non reconnu")
        self.conn = conn
        self.db_manager = db_manager
        self.knowledge_answerer = knowledge_answerer
        self.language = language
        self._phone = phone
        self._sender = sender_phone.strip()
        self._variants = phone_variants(phone)
        self._handlers: dict[str, Callable[[Mapping[str, Any]], ToolResult]] = {
            "my_recent_orders": self._run_recent_orders,
            "my_order_status": self._run_order_status,
            "my_last_validated_order": self._run_last_validated_order,
            "search_catalog": self._run_search_catalog,
            "request_human": self._run_request_human,
        }

    def __repr__(self) -> str:
        return f"AssistantTools(expéditeur={self._phone[:5]}…)"

    @property
    def phone(self) -> str:
        """Numéro de l'expéditeur au format international (« +2126… »)."""
        return self._phone

    # ------------------------------------------------------------------ portée des requêtes

    def _scope(self, alias: str = "c") -> tuple[str, list[str]]:
        """Clause SQL qui limite les commandes ``alias`` à l'expéditeur, et ses paramètres."""
        marks = ", ".join("?" for _ in self._variants)
        clause = (
            f"({_normalized_phone_sql(f'{alias}.whatsapp_from')} IN ({marks}) "
            f"OR {alias}.client_id IN ("
            f"SELECT cl.id FROM clients cl WHERE {_normalized_phone_sql('cl.telephone')} IN ({marks}) "
            f"AND EXISTS (SELECT 1 FROM commandes v WHERE v.client_id = cl.id AND v.statut = 'validee' "
            f"AND {_normalized_phone_sql('v.whatsapp_from')} IN ({marks}))))"
        )
        return clause, [*self._variants, *self._variants, *self._variants]

    def _orders(self, condition: str = "", parameters: Sequence[Any] = (), *, order_by: str = "c.created_at DESC",
                limit: int = MAX_RECENT_ORDERS) -> list[dict[str, Any]]:
        """Commandes visibles par l'expéditeur, avec le produit et le nom du client."""
        scope, scope_parameters = self._scope()
        statuses = ", ".join("?" for _ in VISIBLE_STATUSES)
        extra = f" AND ({condition})" if condition else ""
        # Les fragments SQL sont des constantes du module ; toutes les valeurs passent par des paramètres « ? ».
        query = (
            "SELECT c.id, c.numero_commande, c.created_at, c.date_commande, c.validated_at, c.statut, "
            "c.quantite, c.unite, c.nature_produit, c.type_papier, c.grammage, c.laize, c.format_sac, c.type_sac, "
            "c.impression_client, c.code_article, p.type AS produit_type, cl.nom AS client_nom "
            "FROM commandes c LEFT JOIN produits p ON p.id = c.produit_id LEFT JOIN clients cl ON cl.id = c.client_id "
            f"WHERE c.statut IN ({statuses}) AND {scope}{extra} ORDER BY {order_by}, c.id DESC LIMIT ?"  # nosec B608
        )
        cursor = self.conn.execute(query, (*VISIBLE_STATUSES, *scope_parameters, *parameters, int(limit)))
        return _rows(cursor)

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        """Résultat minimal montré au client : référence, date, produit, quantité, unité, statut."""
        product = row.get("produit_type") or row.get("nature_produit")
        if isinstance(product, str):
            product = " ".join(product.split())[:MAX_PRODUCT_CHARS] or None
        date = row.get("date_commande") or row.get("created_at")
        return {
            "reference": order_reference(row.get("numero_commande"), row.get("id")),
            "date": date[:10] if isinstance(date, str) else None,
            "product": product,
            "quantity": _number(row.get("quantite")),
            "unit": row.get("unite") or "US",
            "status": row.get("statut"),
        }

    # ------------------------------------------------------------------ outils de lecture

    def my_recent_orders(self, limit: int = 5) -> list[dict[str, Any]]:
        """Dernières commandes de l'expéditeur, au plus :data:`MAX_RECENT_ORDERS`, la plus récente d'abord."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            limit = 5
        limit = max(1, min(MAX_RECENT_ORDERS, limit))
        return [self._public(row) for row in self._orders(limit=limit)]

    def my_order_status(self, reference: str) -> dict[str, Any] | None:
        """Commande de l'expéditeur désignée par son numéro ou par « CMD-<id> », ou ``None``.

        La réponse est la même pour une référence inconnue et pour la commande d'un autre
        client : rien ne permet de savoir si une référence existe ailleurs.
        """
        if not isinstance(reference, str):
            return None
        wanted = _REFERENCE_CHARS_RE.sub("", reference.strip().upper())[:MAX_REFERENCE_CHARS]
        if not wanted:
            return None
        match = _REFERENCE_ID_RE.fullmatch(wanted)
        order_id = int(match.group(1)) if match else -1
        rows = self._orders(
            "UPPER(REPLACE(TRIM(COALESCE(c.numero_commande, '')), ' ', '')) = ? OR c.id = ?",
            (wanted, order_id), limit=1,
        )
        return self._public(rows[0]) if rows else None

    def my_last_validated_order(self) -> dict[str, Any] | None:
        """Dernière commande validée de l'expéditeur, ou ``None``."""
        rows = self._validated_rows(limit=1)
        return self._public(rows[0]) if rows else None

    def _validated_rows(self, limit: int) -> list[dict[str, Any]]:
        return self._orders("c.statut = 'validee'", order_by="COALESCE(c.validated_at, c.created_at) DESC",
                            limit=limit)

    def last_validated_order_draft(self, client_name: str | None = None) -> dict[str, Any] | None:
        """Champs de la dernière commande validée de l'expéditeur, pour la renouveler (sans prix).

        Args:
            client_name: nom de client cité dans le message ; la commande renouvelée est alors
                la dernière commande validée de l'expéditeur pour ce client (comparaison sans
                casse ni accents). Sans commande pour ce client, le résultat est ``None``.

        Returns:
            Les champs de commande (``type_produit``, ``quantite``, ``unite``, ``entreprise_cliente``,
            caractéristiques du sac) et ``reference``, ou ``None``.
        """
        rows = self._validated_rows(limit=_CLIENT_LIMIT)
        if client_name:
            wanted = fold(client_name).strip()
            rows = [row for row in rows if row.get("client_nom") and fold(row["client_nom"]).strip() == wanted]
        if not rows:
            return None
        row = rows[0]
        draft: dict[str, Any] = {name: row[name] for name in _REORDER_FIELDS if row.get(name) not in (None, "")}
        if row.get("produit_type"):
            draft["type_produit"] = row["produit_type"]
        if row.get("client_nom") and not is_generic_client_name(row["client_nom"]):
            draft["entreprise_cliente"] = row["client_nom"]
        if "quantite" in draft:
            draft["quantite"] = _number(draft["quantite"])
        draft["reference"] = order_reference(row.get("numero_commande"), row.get("id"))
        return draft

    def known_client_name(self) -> str | None:
        """Nom du client de la dernière commande validée de l'expéditeur (nom provisoire exclu), ou ``None``."""
        for row in self._validated_rows(limit=_CLIENT_LIMIT):
            name = row.get("client_nom")
            if name and not is_generic_client_name(name):
                return name
        return None

    def search_catalog(self, query: str) -> dict[str, Any]:
        """Réponse à une question sur les produits.

        Avec une base de connaissances : ``{"type": "answer", "text", "grounded", "sources"}``.
        Sans elle : ``{"type": "catalog", "products": [...]}``, les produits du catalogue cités dans
        la question, ou les quatre produits si aucun n'est cité.
        """
        return self._search(query)[0]

    def _search(self, query: str) -> tuple[dict[str, Any], Any]:
        query = " ".join(str(query or "").split())[:MAX_QUERY_CHARS]
        if self.knowledge_answerer is not None:
            answer = self.knowledge_answerer.answer(query, self.language, allowed_phones=(self._phone,))
            return ({"type": "answer", "text": answer.text, "grounded": answer.grounded,
                     "sources": list(answer.sources)}, answer)
        cited = normalize_product_type(query) if query else None
        products = [
            {"number": index, "product": product["type"], "usages": list(product["usages"])}
            for index, product in enumerate(PRODUCTS, start=1)
            if cited is None or product["type"] == cited
        ]
        return {"type": "catalog", "products": products}, None

    def request_human(self) -> dict[str, Any]:
        """Demande d'un conseiller : l'automate de conversation transfère la conversation à l'équipe commerciale."""
        logger.info("Conseiller demandé par l'expéditeur %s…", self._phone[:5])
        return {"handoff": True}

    # ------------------------------------------------------------------ enregistrement d'une commande

    def _existing_order(self, column: str, value: Any) -> tuple[int, bool] | None:
        """Commande déjà enregistrée avec cette valeur de ``column`` : (identifiant, visible par l'expéditeur)."""
        row = self.conn.execute(f"SELECT id FROM commandes WHERE {column} = ?", (value,)).fetchone()  # nosec B608
        if row is None:
            return None
        order_id = int(row[0])
        scope, parameters = self._scope()
        visible = self.conn.execute(
            f"SELECT 1 FROM commandes c WHERE c.id = ? AND {scope}", (order_id, *parameters),  # nosec B608
        ).fetchone()
        return order_id, visible is not None

    def submit_order(self, draft: Mapping[str, Any], *, message_sid: str | None = None) -> int:
        """Enregistre la commande confirmée par le client, au statut « en_attente », et renvoie son identifiant.

        Seuls les champs de commande et les informations de :data:`ORDER_METADATA_FIELDS` sont
        repris ; le canal, le numéro de l'expéditeur et le statut sont fixés ici. Un numéro de
        commande déjà attribué à une commande d'un autre expéditeur est retiré (et noté dans les
        informations supplémentaires) : la commande est créée, jamais rattachée à celle d'un autre.

        Args:
            draft: commande normalisée (``entreprise_cliente``, ``type_produit``, ``quantite``…).
            message_sid: identifiant Twilio du message de confirmation, enregistré dans
                ``commandes.message_sid`` ; un même identifiant ne crée qu'une commande.

        Raises:
            ValueError: un champ obligatoire manque.
        """
        # Une liste de traçabilité vide (aucune anomalie) est gardée : elle est enregistrée « [] ».
        order = {name: value for name, value in draft.items() if name in _SUBMITTABLE_FIELDS
                 and (value not in (None, "", [], {}) or (name in _LIST_METADATA_FIELDS and value == []))}
        missing = [name for name in REQUIRED_ORDER_FIELDS if name not in order]
        if missing:
            raise ValueError(f"commande incomplète, champs manquants : {', '.join(missing)}")

        if message_sid:
            sid = str(message_sid).strip()[:MAX_MESSAGE_SID_CHARS]
            identifiers = {"message_sid": sid, "email_id": f"whatsapp_chat_{sid[:64]}"}
            for column, value in identifiers.items():
                existing = self._existing_order(column, value)
                if existing is not None and existing[1]:
                    logger.info("Commande déjà enregistrée pour ce message (id %s)", existing[0])
                    return existing[0]
                if existing is None:
                    order[column] = value

        numero = order.get("numero_commande")
        if numero:
            existing = self._existing_order("numero_commande", numero)
            if existing is not None and existing[1]:
                logger.info("Commande %s déjà enregistrée pour cet expéditeur (id %s)", numero, existing[0])
                return existing[0]
            if existing is not None:
                del order["numero_commande"]
                note = f"Référence indiquée par le client : {numero} (déjà attribuée à une autre commande)."
                previous = order.get("informations_supplementaires")
                order["informations_supplementaires"] = f"{previous}\n{note}" if previous else note

        order.update({"source": "whatsapp", "whatsapp_from": self._sender})
        order_id = self.db_manager.create_order(order)
        logger.info("Commande WhatsApp confirmée par le client enregistrée (id %s)", order_id)
        return int(order_id)

    # ------------------------------------------------------------------ outils du modèle de langue

    @staticmethod
    def tool_specs() -> list[dict[str, Any]]:
        """Schémas des outils au format de l'API OpenAI (appel de fonctions).

        Aucun schéma ne déclare de numéro de téléphone, de client ni d'identifiant interne :
        l'expéditeur est fixé dans le code.
        """
        def spec(name: str, description: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
            return {"type": "function", "function": {
                "name": name, "description": description,
                "parameters": {"type": "object", "properties": properties or {}, "additionalProperties": False},
            }}

        return [
            spec("my_recent_orders", "Liste les dernières commandes du client qui écrit (référence, date, produit, "
                 "quantité, statut).",
                 {"limit": {"type": "integer", "minimum": 1, "maximum": MAX_RECENT_ORDERS,
                            "description": "Nombre de commandes à lister."}}),
            spec("my_order_status", "Donne le statut d'une commande du client qui écrit, d'après sa référence.",
                 {"reference": {"type": "string", "maxLength": MAX_REFERENCE_CHARS,
                                "description": "Référence de la commande citée par le client."}}),
            spec("my_last_validated_order", "Donne la dernière commande validée du client qui écrit."),
            spec("search_catalog", "Cherche la réponse à une question sur les produits TECPAP dans la base "
                 "de connaissances.",
                 {"query": {"type": "string", "maxLength": MAX_QUERY_CHARS,
                            "description": "Question du client sur les produits."}}),
            spec("request_human", "Transfère la conversation à un membre de l'équipe commerciale."),
        ]

    def execute(self, name: str, arguments: Any) -> ToolResult:
        """Exécute un outil demandé par le modèle ; ne lève jamais d'exception.

        Un outil inconnu, des arguments invalides ou une erreur pendant l'exécution donnent un
        résultat ``ok=False`` avec un message générique ; le détail de l'erreur est journalisé.
        """
        handler = self._handlers.get(name) if isinstance(name, str) else None
        if handler is None:
            logger.warning("Outil inconnu demandé par le modèle : %r", str(name)[:60])
            return ToolResult(str(name)[:60], False, error="Outil inconnu.")
        args = dict(arguments) if isinstance(arguments, Mapping) else {}
        ignored = sorted(str(key)[:30] for key in args if key not in self._expected_arguments(name))
        if ignored:
            logger.warning("Arguments ignorés pour l'outil %s : %s", name, ", ".join(ignored))
        try:
            return handler(args)
        except Exception:
            logger.exception("Erreur pendant l'exécution de l'outil %s", name)
            return ToolResult(name, False, error="L'outil n'a pas pu répondre.")

    @staticmethod
    def _expected_arguments(name: str) -> Iterable[str]:
        return {"my_recent_orders": ("limit",), "my_order_status": ("reference",),
                "search_catalog": ("query",)}.get(name, ())

    def _run_recent_orders(self, args: Mapping[str, Any]) -> ToolResult:
        limit = args.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            limit = 5
        return ToolResult("my_recent_orders", True, {"orders": self.my_recent_orders(int(limit))})

    def _run_order_status(self, args: Mapping[str, Any]) -> ToolResult:
        reference = args.get("reference")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 4 * MAX_REFERENCE_CHARS:
            return ToolResult("my_order_status", False, error="Référence de commande invalide.")
        data = {"reference": reference.strip()[:MAX_REFERENCE_CHARS], "order": self.my_order_status(reference)}
        return ToolResult("my_order_status", True, data)

    def _run_last_validated_order(self, args: Mapping[str, Any]) -> ToolResult:
        return ToolResult("my_last_validated_order", True, {"order": self.my_last_validated_order()})

    def _run_search_catalog(self, args: Mapping[str, Any]) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult("search_catalog", False, error="Question vide.")
        data, answer = self._search(query)
        return ToolResult("search_catalog", True, data, answer=answer)

    def _run_request_human(self, args: Mapping[str, Any]) -> ToolResult:
        return ToolResult("request_human", True, self.request_human())
