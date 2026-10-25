"""Modèles de réponse de l'assistant WhatsApp, en français, en darija et en arabe standard.

Toutes les réponses qui contiennent des données (récapitulatif de commande, liste de statuts,
confirmation d'enregistrement) sont produites ici, à partir de modèles fixes remplis par le
code : le modèle de langue ne rédige jamais une donnée envoyée au client. Seules les réponses
aux questions sur les produits (base de connaissances) et quelques réponses de conversation
viennent du modèle, après contrôle par le filtre de sortie.

Langues de réponse (voir :func:`tecpap.nlp.language.reply_language`) :

- ``fr`` : français ;
- ``darija`` : arabe marocain écrit en lettres latines (arabizi : les chiffres 3, 7 et 9
  remplacent des lettres arabes), avec les mots français usuels du commerce (« commande »,
  « produit ») comme les clients les écrivent ;
- ``ar`` : arabe standard moderne.

Une langue inconnue (``en``, ``unknown``…) donne le français. :func:`render` remplit un modèle ;
les autres fonctions mettent en forme les valeurs (quantité, date, produit, statut) dans la
langue demandée.

Les nombres suivent :mod:`tecpap.formatting` : espace fine insécable (U+202F) entre les milliers
et virgule décimale (« 10 000 », « 2,5 »), espace insécable (U+00A0) entre la quantité et son
unité. Avec une espace ordinaire, l'algorithme bidirectionnel Unicode afficherait « 10 000 »
sous la forme « 000 10 » dans une réponse en arabe.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from tecpap.catalog import COMPANY_INFO, PRODUCTS
from tecpap.formatting import MISSING_VALUE, UNIT_SEPARATOR, format_number

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "STATUS_LABELS",
    "TEMPLATE_KEYS",
    "format_date",
    "format_quantity",
    "normalize_reply_language",
    "order_lines",
    "product_label",
    "product_menu",
    "reason_line",
    "render",
    "status_label",
    "status_lines",
    "unit_label",
]

LANGUAGES: tuple[str, ...] = ("fr", "darija", "ar")
"""Langues dans lesquelles l'assistant répond."""

DEFAULT_LANGUAGE = "fr"
"""Langue de réponse quand la langue du client est inconnue ou n'a pas de modèles."""

_LANGUAGE_ALIASES = MappingProxyType({
    "fr": "fr", "darija": "darija", "darija_latin": "darija", "darija_ar": "darija", "ar": "ar",
})

_TEMPLATES: dict[str, dict[str, str]] = {
    "fr": {
        "greeting": "Bonjour ! Je suis l'assistant WhatsApp de TECPAP.\n{help}",
        "help": (
            "Je peux vous aider à :\n"
            "- passer une commande (produit et quantité) ;\n"
            "- renouveler votre commande habituelle (« comme d'habitude ») ;\n"
            "- suivre vos commandes ;\n"
            "- répondre à vos questions sur nos sacs.\n"
            "Écrivez « conseiller » pour parler à notre équipe commerciale."
        ),
        "ask_client": "Pour quel commerce ou quelle entreprise est cette commande ? Indiquez son nom.",
        "ask_product": "Quel produit souhaitez-vous ? Répondez par le numéro :\n{products}",
        "ask_quantity": "Quelle quantité souhaitez-vous (nombre de pièces) ? Par exemple : 5000.",
        "quantity_invalid": "Je n'ai pas compris la quantité. Indiquez un nombre de pièces, par exemple 5000.",
        "summary": (
            "{intro}Récapitulatif de votre commande :\n{lines}\n"
            "Confirmez-vous ? Répondez OUI pour valider, NON pour modifier, ou ANNULER."
        ),
        "reorder_intro": "Voici votre dernière commande validée, prête à être renouvelée.\n",
        "confirm_hint": "Répondez OUI pour valider la commande, NON pour la modifier, ou ANNULER pour l'abandonner.",
        "ask_change": (
            "D'accord. Que souhaitez-vous modifier : le produit, la quantité ou le client ? "
            "Vous pouvez aussi écrire directement la correction, par exemple « 5000 »."
        ),
        "submitted": (
            "✅ Merci ! Votre commande {reference} est enregistrée. Elle est en attente de validation "
            "par notre équipe commerciale."
        ),
        "cancelled": "Commande annulée. Écrivez-moi pour toute autre commande.",
        "cancel_forwarded": (
            "Votre demande d'annulation est transmise à notre équipe commerciale, qui vous la confirmera ici."
        ),
        "status_list": "Vos dernières commandes :\n{lines}",
        "status_none": "Je ne trouve aucune commande associée à votre numéro.",
        "status_not_found": "Je ne trouve pas la commande {reference} parmi les commandes de votre numéro.",
        "handoff": "Entendu. Un membre de notre équipe commerciale va vous répondre ici dès que possible.",
        "handoff_ack": "Message bien reçu. Notre équipe commerciale vous répond ici rapidement.",
        "blocked": (
            "Je ne peux pas traiter cette demande. Je peux vous aider à passer une commande, à suivre vos "
            "commandes ou à répondre à vos questions sur nos produits."
        ),
        "no_answer": "Je n'ai pas cette information. Écrivez « conseiller » pour qu'un membre de notre équipe "
                     "vous réponde.",
        "sales_will_answer": (
            "Les prix, les délais et les disponibilités sont communiqués par notre équipe commerciale. "
            "Un conseiller vous répond ici rapidement."
        ),
        "rate_limited": (
            "Vous avez envoyé beaucoup de messages en peu de temps. Merci de patienter une minute avant de réécrire."
        ),
        "media_failed": "Je n'ai pas pu lire votre fichier. Pouvez-vous écrire votre demande en texte ?",
        "error": "Un incident technique m'empêche de répondre. Réessayez dans quelques minutes ou écrivez "
                 "« conseiller ».",
        "reorder_none": "Je ne trouve pas de commande validée à renouveler pour votre numéro.",
        "draft_expired": "Votre commande en cours n'a pas été confirmée dans les 24 heures : elle a été abandonnée.",
        "excerpt_answer": "D'après notre documentation : « {excerpt} »",
        "catalog_list": "Nos produits :\n{products}",
        "order_validated": (
            "Bonne nouvelle : votre commande {reference} est validée par notre équipe commerciale.\n{lines}\n"
            "Merci pour votre confiance."
        ),
        "order_rejected": (
            "Votre commande {reference} n'a pas pu être validée par notre équipe commerciale.{reason}\n"
            "Écrivez « conseiller » pour en parler avec nous."
        ),
    },
    "darija": {
        "greeting": "Salam! Ana l'assistant WhatsApp dyal TECPAP.\n{help}",
        "help": (
            "N9der n3awnek:\n"
            "- tsift commande (produit w quantité)\n"
            "- t3awed nafs la commande (\"kif dima\")\n"
            "- tchouf fin wslat commande dyalek\n"
            "- tswel 3la les sacs dyalna\n"
            "Kteb \"conseiller\" bach tehder m3a l'équipe commerciale."
        ),
        "ask_client": "Chno smiyt l'commerce wla la société li 3endha had la commande?",
        "ask_product": "Ina produit bghiti? Kteb ra9m:\n{products}",
        "ask_quantity": "Ch7al bghiti (3adad l7bat)? Matalan: 5000.",
        "quantity_invalid": "Ma fhemtch la quantité. Kteb ra9m dyal l7bat, matalan 5000.",
        "summary": (
            "{intro}Hak l'récap dyal la commande:\n{lines}\n"
            "Nconfirmiw? Kteb \"wakha\" bach tconfirmi, \"la\" bach tbeddel, wla \"annuler\"."
        ),
        "reorder_intro": "Hadi akhir commande mvalidya dyalek, wajda bach t3awedha.\n",
        "confirm_hint": "Kteb \"wakha\" bach tconfirmi la commande, \"la\" bach tbeddel chi 7aja, wla \"annuler\".",
        "ask_change": (
            "Wakha. Chno bghiti tbeddel: le produit, la quantité wla le client? "
            "T9der tkteb dakchi jdid direct, matalan \"5000\"."
        ),
        "submitted": (
            "✅ Chokran! La commande {reference} tsejlat. Daba katsna l'validation dyal l'équipe commerciale."
        ),
        "cancelled": "Safi, la commande t'annulat. Ila bghiti chi commande jdida, ktebli.",
        "cancel_forwarded": "Talab l'annulation wsel l'équipe commerciale, ghadi yconfirmiwh lik hna.",
        "status_list": "Akhir les commandes dyalek:\n{lines}",
        "status_none": "Ma l9it 7ta commande b had nnemra.",
        "status_not_found": "Ma l9itch la commande {reference} f les commandes dyal had nnemra.",
        "handoff": "Wakha. Chi wa7ed mn l'équipe commerciale ghadi yjawbek hna f a9rab wa9t.",
        "handoff_ack": "Wsalni l'message dyalek. L'équipe commerciale ghadi tjawbek hna 9rib.",
        "blocked": (
            "Ma n9derch ndir had talab. N9der n3awnek f commande, f suivi dyal les commandes dyalek "
            "wla f su2al 3la les produits dyalna."
        ),
        "no_answer": "Ma3endich had l'information. Kteb \"conseiller\" w chi wa7ed mn l'équipe ghadi yjawbek.",
        "sales_will_answer": (
            "Les prix, les délais w l'disponibilité kay3tihom l'équipe commerciale. Chi conseiller ghadi "
            "yjawbek hna 9rib."
        ),
        "rate_limited": "Sifti bzzaf dyal les messages f wa9t 9sir. Tsenna chi d9i9a 3afak 3ad 3awed kteb.",
        "media_failed": "Ma 9dertch n9ra l'fichier. Momkin tkteb lia talab dyalek?",
        "error": "Kayn chi mochkil techni. 3awed men ba3d chwiya, wla kteb \"conseiller\".",
        "reorder_none": "Ma l9it 7ta commande mvalidya 9bel b had nnemra bach n3awdoha.",
        "draft_expired": "La commande li kanti katdir ma tconfirmatch f 24 sa3a, donc tlghat.",
        "excerpt_answer": "Hada li kayn f la documentation dyalna: « {excerpt} »",
        "catalog_list": "Les produits dyalna:\n{products}",
        "order_validated": (
            "Khbar mzyan: la commande {reference} tvalidat 3end l'équipe commerciale.\n{lines}\n"
            "Chokran 3la thi9a dyalek."
        ),
        "order_rejected": (
            "La commande {reference} ma tvalidatch 3end l'équipe commerciale.{reason}\n"
            "Kteb \"conseiller\" bach tehder m3ana."
        ),
    },
    "ar": {
        "greeting": "مرحبا! أنا المساعد الآلي لشركة TECPAP على واتساب.\n{help}",
        "help": (
            "يمكنني مساعدتك في:\n"
            "- تقديم طلب (المنتج والكمية)\n"
            "- تجديد طلبك المعتاد («كالعادة»)\n"
            "- تتبع طلباتك\n"
            "- الإجابة عن أسئلتك حول أكياسنا\n"
            "اكتب «مستشار» للتحدث مع فريقنا التجاري."
        ),
        "ask_client": "لأي محل أو شركة هذا الطلب؟ اكتب الاسم من فضلك.",
        "ask_product": "ما المنتج الذي تريده؟ أجب برقم:\n{products}",
        "ask_quantity": "ما الكمية التي تريدها (عدد القطع)؟ مثلا: 5000.",
        "quantity_invalid": "لم أفهم الكمية. اكتب عدد القطع، مثلا 5000.",
        "summary": (
            "{intro}ملخص طلبك:\n{lines}\n"
            "هل تؤكد الطلب؟ أجب بـ«نعم» للتأكيد، أو «لا» للتعديل، أو «إلغاء»."
        ),
        "reorder_intro": "هذا آخر طلب مؤكد لك، وهو جاهز للتجديد.\n",
        "confirm_hint": "أجب بـ«نعم» لتأكيد الطلب، أو «لا» لتعديله، أو «إلغاء» للتخلي عنه.",
        "ask_change": (
            "حسنا، ماذا تريد أن تعدل: المنتج أم الكمية أم الزبون؟ يمكنك أيضا كتابة التصحيح مباشرة، مثلا «5000»."
        ),
        "submitted": "✅ شكرا! تم تسجيل طلبك {reference}، وهو الآن في انتظار مراجعة فريقنا التجاري.",
        "cancelled": "تم إلغاء الطلب. راسلنا متى أردت تقديم طلب جديد.",
        "cancel_forwarded": "تم تحويل طلب الإلغاء إلى فريقنا التجاري، وسيؤكده لك هنا.",
        "status_list": "آخر طلباتك:\n{lines}",
        "status_none": "لم أجد أي طلب مرتبط برقمك.",
        "status_not_found": "لم أجد الطلب {reference} ضمن طلبات رقمك.",
        "handoff": "حسنا، سيرد عليك أحد أعضاء فريقنا التجاري هنا في أقرب وقت.",
        "handoff_ack": "تم استلام رسالتك، وسيرد عليك فريقنا التجاري هنا قريبا.",
        "blocked": (
            "لا يمكنني معالجة هذا الطلب. يمكنني مساعدتك في تقديم طلب أو تتبع طلباتك أو الإجابة عن أسئلتك حول منتجاتنا."
        ),
        "no_answer": "لا تتوفر لدي هذه المعلومة. اكتب «مستشار» ليرد عليك أحد أعضاء فريقنا.",
        "sales_will_answer": "الأسعار والآجال والتوفر يحددها فريقنا التجاري، وسيرد عليك أحد المستشارين هنا قريبا.",
        "rate_limited": "أرسلت رسائل كثيرة في وقت قصير. يرجى الانتظار دقيقة قبل المراسلة من جديد.",
        "media_failed": "تعذرت علي قراءة الملف. هل يمكنك كتابة طلبك نصا؟",
        "error": "حدث خلل تقني. أعد المحاولة بعد دقائق أو اكتب «مستشار».",
        "reorder_none": "لم أجد طلبا مؤكدا سابقا لرقمك يمكن تجديده.",
        "draft_expired": "لم يتم تأكيد طلبك الجاري خلال 24 ساعة، لذلك تم التخلي عنه.",
        "excerpt_answer": "حسب وثائقنا: «{excerpt}»",
        "catalog_list": "منتجاتنا:\n{products}",
        "order_validated": "خبر سار: أكد فريقنا التجاري طلبك {reference}.\n{lines}\nشكرا على ثقتك.",
        "order_rejected": "تعذر على فريقنا التجاري تأكيد طلبك {reference}.{reason}\nاكتب «مستشار» للتواصل معنا.",
    },
}

TEMPLATE_KEYS: frozenset[str] = frozenset(_TEMPLATES[DEFAULT_LANGUAGE])
"""Clés des modèles de réponse ; chaque clé existe dans les trois langues."""

# Libellés des lignes d'un récapitulatif de commande.
_ORDER_LABELS: dict[str, dict[str, str]] = {
    "fr": {"client": "Client", "product": "Produit", "quantity": "Quantité", "delivery": "Livraison souhaitée",
           "reference": "Référence"},
    "darija": {"client": "Client", "product": "Produit", "quantity": "Quantité", "delivery": "Livraison",
               "reference": "Référence"},
    "ar": {"client": "الزبون", "product": "المنتج", "quantity": "الكمية", "delivery": "تاريخ التسليم المطلوب",
           "reference": "المرجع"},
}

# Ligne d'une liste de commandes (réponse au suivi) ; U+00A0 : espace insécable entre la quantité et l'unité.
_STATUS_LINE: dict[str, str] = {
    "fr": "- {reference} ({date}) : {quantity}\u00a0{unit}, {product} — {status}",
    "darija": "- {reference} ({date}): {quantity}\u00a0{unit}, {product} — {status}",
    "ar": "- {reference} ({date}): {quantity}\u00a0{unit}، {product} — {status}",
}

STATUS_LABELS: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "fr": MappingProxyType({"en_attente": "en attente de validation", "validee": "validée", "rejetee": "refusée"}),
    "darija": MappingProxyType({"en_attente": "katsna l'validation", "validee": "tvalidat", "rejetee": "trefusat"}),
    "ar": MappingProxyType({"en_attente": "قيد المراجعة", "validee": "مؤكدة", "rejetee": "مرفوضة"}),
})
"""Libellé de chaque statut de commande montré au client, par langue."""

# Nom de chaque produit du catalogue (code produit -> libellé) et précisions du menu numéroté.
_PRODUCT_LABELS: dict[str, dict[str, str]] = {
    "fr": {product["code"]: product["type"] for product in PRODUCTS},
    "darija": {"SFP": "Sachets fond plat", "SFCSP": "Sac fond carré bla yddin", "SFCPP": "Sac b yddin plates",
               "SFCPT": "Sac b yddin mfetlin"},
    "ar": {"SFP": "أكياس صغيرة بقاع مسطح", "SFCSP": "كيس بقاع مربع بدون مقابض", "SFCPP": "كيس بقاع مربع بمقابض مسطحة",
           "SFCPT": "كيس بقاع مربع بمقابض مفتولة"},
}
_PRODUCT_MENU_DETAILS: dict[str, dict[str, str]] = {
    "fr": {"SFP": " (sandwichs, tacos, viennoiseries)", "SFCPT": " (sacs premium)"},
    "darija": {"SFP": " (sachiyat dyal sandwich, tacos, viennoiseries)", "SFCPT": " (sacs premium)"},
    "ar": {"SFP": " (ساشي للسندويتشات والتاكوس والمعجنات)", "SFCPT": " (أكياس فاخرة)"},
}

_UNIT_LABELS: dict[str, dict[str, str]] = {
    "fr": {"US": "pièces", "pièces": "pièces", "kg": "kg", "carton": "cartons", "paquet": "paquets"},
    "darija": {"US": "7ba", "pièces": "7ba", "kg": "kilo", "carton": "carton", "paquet": "paquet"},
    "ar": {"US": "قطعة", "pièces": "قطعة", "kg": "كلغ", "carton": "كرتون", "paquet": "علبة"},
}
# Singulier français, employé pour une quantité inférieure à 2 (« 1 carton », « 1,5 carton »).
# La darija et l'arabe gardent la même forme après un nombre écrit en chiffres.
_UNIT_SINGULAR_LABELS: dict[str, dict[str, str]] = {
    "fr": {"US": "pièce", "pièces": "pièce", "carton": "carton", "paquet": "paquet"},
}

_MISSING_VALUE = {"fr": MISSING_VALUE, "darija": MISSING_VALUE, "ar": MISSING_VALUE}

# Libellé du motif d'un refus de commande (notification « order_rejected »).
_REASON_LABELS = {"fr": "Motif : ", "darija": "Sabab: ", "ar": "السبب: "}
MAX_REASON_CHARS = 300


def normalize_reply_language(language: str | None) -> str:
    """Langue de réponse disponible : ``fr``, ``darija`` ou ``ar``.

    Les codes de :func:`tecpap.nlp.language.detect_language` sont acceptés (``darija_latin`` et
    ``darija_ar`` donnent ``darija``) ; toute autre valeur donne le français.
    """
    if not isinstance(language, str):
        return DEFAULT_LANGUAGE
    return _LANGUAGE_ALIASES.get(language.strip().lower(), DEFAULT_LANGUAGE)


def product_menu(language: str | None) -> str:
    """Liste numérotée des quatre produits du catalogue (le client répond par le numéro)."""
    lang = normalize_reply_language(language)
    details = _PRODUCT_MENU_DETAILS[lang]
    return "\n".join(
        f"{index}. {_PRODUCT_LABELS[lang][product['code']]}{details.get(product['code'], '')}"
        for index, product in enumerate(PRODUCTS, start=1)
    )


def _defaults(language: str) -> dict[str, str]:
    return {
        "help": _TEMPLATES[language]["help"],
        "products": product_menu(language),
        "intro": "",
        "company_phone": COMPANY_INFO["phone"],
        "company_email": COMPANY_INFO["email"],
    }


def render(key: str, language: str | None, **values: Any) -> str:
    """Remplit le modèle ``key`` dans la langue demandée.

    Les valeurs ``help`` (texte d'aide), ``products`` (menu numéroté des produits), ``intro``
    (vide), ``company_phone`` et ``company_email`` sont fournies par défaut ; ``values`` les
    remplace. Les valeurs sont insérées telles quelles : un texte du client qui contient des
    accolades n'est pas interprété.

    Raises:
        KeyError: ``key`` n'est pas un modèle connu, ou une valeur attendue par le modèle manque.
    """
    if key not in TEMPLATE_KEYS:
        raise KeyError(f"modèle de réponse inconnu : {key!r}")
    lang = normalize_reply_language(language)
    return _TEMPLATES[lang][key].format_map({**_defaults(lang), **values})


def format_quantity(value: Any, language: str | None = None) -> str:
    """Quantité écrite avec une espace fine insécable entre les milliers (« 10 000 ») et une virgule décimale (« 2,5 »).

    Voir :func:`tecpap.formatting.format_number` (deux décimales au plus) ; une valeur absente
    donne « — » et un texte qui n'est pas un nombre est renvoyé tel quel.
    """
    lang = normalize_reply_language(language)
    if isinstance(value, bool):
        return str(value)
    return format_number(value, decimals=2, language=lang)


def format_date(value: Any, language: str | None = None) -> str:
    """Date ISO (« 2026-11-15 », éventuellement suivie d'une heure) écrite « 15/11/2026 »."""
    if not isinstance(value, str) or len(value) < 10:
        return _MISSING_VALUE[normalize_reply_language(language)] if not value else str(value)
    year, month, day = value[:4], value[5:7], value[8:10]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return value
    return f"{day}/{month}/{year}"


def product_label(product_type: str | None, language: str | None) -> str:
    """Nom d'un produit du catalogue dans la langue demandée ; un autre texte est renvoyé tel quel."""
    lang = normalize_reply_language(language)
    if not product_type:
        return _MISSING_VALUE[lang]
    for product in PRODUCTS:
        if product["type"] == product_type:
            return _PRODUCT_LABELS[lang][product["code"]]
    return product_type


def unit_label(unit: str | None, language: str | None, quantity: Any = None) -> str:
    """Unité d'une quantité montrée au client (« US », unité de stock de SAGE X3, devient « pièces »).

    En français, une quantité numérique inférieure à 2 prend le singulier (« 1 carton ») ; sans
    quantité, le libellé est au pluriel.
    """
    lang = normalize_reply_language(language)
    key = unit or "US"
    if _is_singular(quantity) and key in _UNIT_SINGULAR_LABELS.get(lang, {}):
        return _UNIT_SINGULAR_LABELS[lang][key]
    return _UNIT_LABELS[lang].get(key, key)


def _is_singular(quantity: Any) -> bool:
    """Vrai pour une quantité numérique dont la valeur absolue est inférieure à 2 (règle du français)."""
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        return False
    return abs(quantity) < 2


def status_label(status: str | None, language: str | None) -> str:
    """Libellé d'un statut de commande (``en_attente``, ``validee``, ``rejetee``) dans la langue demandée."""
    lang = normalize_reply_language(language)
    if not status:
        return _MISSING_VALUE[lang]
    return STATUS_LABELS[lang].get(status, status)


def order_lines(order: Mapping[str, Any], language: str | None, *, omit_unknown_client: bool = False) -> str:
    """Lignes du récapitulatif d'une commande : client, produit, quantité, puis livraison et référence si connues.

    Avec ``omit_unknown_client``, la ligne du client est omise quand son nom est absent, au lieu
    d'afficher une valeur à préciser (notification d'une décision : le client ne doit pas lire
    un nom provisoire).
    """
    lang = normalize_reply_language(language)
    labels = _ORDER_LABELS[lang]
    client_line = f"- {labels['client']} : {order.get('entreprise_cliente') or _MISSING_VALUE[lang]}"
    lines = [] if omit_unknown_client and not order.get("entreprise_cliente") else [client_line]
    lines += [
        f"- {labels['product']} : {product_label(order.get('type_produit'), lang)}",
        f"- {labels['quantity']} : {format_quantity(order.get('quantite'), lang)}{UNIT_SEPARATOR}"
        f"{unit_label(order.get('unite'), lang, order.get('quantite'))}",
    ]
    if order.get("date_livraison"):
        lines.append(f"- {labels['delivery']} : {format_date(order['date_livraison'], lang)}")
    if order.get("numero_commande"):
        lines.append(f"- {labels['reference']} : {order['numero_commande']}")
    if lang != "fr":
        lines = [line.replace(" : ", ": ", 1) for line in lines]
    return "\n".join(lines)


def reason_line(reason: str | None, language: str | None) -> str:
    """Motif d'un refus précédé d'un retour à la ligne (« Motif : … »), ou chaîne vide sans motif.

    Sert de valeur ``reason`` au modèle ``order_rejected``.
    """
    text = " ".join(str(reason or "").split())[:MAX_REASON_CHARS]
    if not text:
        return ""
    return f"\n{_REASON_LABELS[normalize_reply_language(language)]}{text}"


def status_lines(orders: Iterable[Mapping[str, Any]], language: str | None) -> str:
    """Une ligne par commande : référence, date, quantité, produit et statut."""
    lang = normalize_reply_language(language)
    template = _STATUS_LINE[lang]
    return "\n".join(
        template.format(
            reference=order.get("reference") or _MISSING_VALUE[lang],
            date=format_date(order.get("date"), lang),
            quantity=format_quantity(order.get("quantity"), lang),
            unit=unit_label(order.get("unit"), lang, order.get("quantity")),
            product=product_label(order.get("product"), lang),
            status=status_label(order.get("status"), lang),
        )
        for order in orders
    )
