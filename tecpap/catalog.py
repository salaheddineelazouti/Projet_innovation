"""Catalogue TECPAP : source unique des données métier fixes.

Ce module regroupe tout ce qui décrit l'offre et l'entreprise : les quatre familles de sacs,
les types de papier, les fournisseurs, les grammages et laizes standards, les coordonnées de
l'entreprise, les expressions de renouvellement de commande et les mots-clés par langue.
Le remplissage de la base, les instructions envoyées au modèle de langue, les règles de
normalisation et la base de connaissances s'appuient sur ces constantes, ce qui évite les
listes recopiées et divergentes.

Langues des mots-clés :
- ``fr`` : français ;
- ``darija`` : arabe marocain écrit en caractères latins (« arabizi », où les chiffres 3, 7 et 9
  remplacent des lettres arabes) ;
- ``ar`` : arabe en caractères arabes (arabe standard ou darija) ;
- ``en`` : anglais.
"""

from __future__ import annotations

from typing import Any

# Les quatre familles de produits. Les identifiants 1 à 4 sont aussi les clés primaires de la
# table ``produits`` : ils ne doivent jamais changer.
PRODUCTS: list[dict[str, Any]] = [
    {
        "id": 1,
        "type": "Sachets fond plat",
        "code": "SFP",
        "description": "Sachets à fond plat pour emballage",
        "usages": ["sandwichs", "tacos", "viennoiseries"],
        "keywords": {
            "fr": ["sachet", "sachets", "fond plat", "sachet kraft", "sachet sandwich", "pochette", "petit sac",
                   "sac à pain", "sac pain"],
            "darija": ["sachet", "sachiyat", "sachet dyal sandwich", "sachet tacos", "sachiat", "sachyat",
                       "sachiya", "sachia", "sachiyet", "sachet dyal lkhobz"],
            "ar": ["ساشي", "ساشة", "أكياس صغيرة", "كيس مسطح", "كيس صغير", "قاع مسطح", "قعر مسطح", "كيس الخبز",
                   "ساشيات"],
            "en": ["flat bottom bag", "flat bottom bags", "flat bottom sachet", "flat bottom", "sachet",
                   "sandwich bag", "small paper bag", "small bag", "bread bag"],
        },
    },
    {
        "id": 2,
        "type": "Sac fond carré sans poignées",
        "code": "SFCSP",
        "description": "Sacs fond carré sans poignées",
        "usages": ["emballage standard"],
        "keywords": {
            "fr": ["sans poignée", "sans poignées", "fond carré", "sans anse", "sans anses", "sac sos",
                   "pas de poignée", "pas de poignées", "pas d'anse", "pas d'anses"],
            "darija": ["bla yddin", "bla poignée", "sac bla poignée", "bla yeddin", "bla yedd", "bla poignées",
                       "bla anses", "bla mqabed", "ma fihch yddin"],
            "ar": ["بدون مقبض", "بدون يد", "بدون مقابض", "بلا مقبض", "بلا يد", "من غير مقبض", "من غير يد",
                   "بدون أيدي", "بلا أيدي", "قاع مربع", "قعر مربع"],
            "en": ["without handles", "without handle", "no handles", "handleless", "square bottom bag",
                   "square bottom", "sos bag", "no handle"],
        },
    },
    {
        "id": 3,
        "type": "Sac fond carré avec poignées plates",
        "code": "SFCPP",
        "description": "Sacs fond carré avec poignées plates",
        "usages": ["sacs shopping"],
        "keywords": {
            "fr": ["poignée plate", "poignées plates", "sac shopping", "anse plate", "anses plates"],
            "darija": ["poignée plate", "yddin plates", "yeddin plates", "yddin plat", "yddin msat7in"],
            "ar": ["مقبض مسطح", "يد مسطحة", "مقابض مسطحة", "أيدي مسطحة"],
            "en": ["flat handles", "flat handle", "flat paper handles", "shopping bag", "tape handles",
                   "flat tape handles"],
        },
    },
    {
        "id": 4,
        "type": "Sac fond carré avec poignées torsadées",
        "code": "SFCPT",
        "description": "Sacs fond carré avec poignées torsadées",
        "usages": ["sacs premium"],
        "keywords": {
            "fr": ["poignée torsadée", "poignées torsadées", "torsadé", "cordelette", "premium", "poignée ficelle",
                   "poignées ficelles", "ficelle", "corde", "cordon", "anse torsadée", "anses torsadées",
                   "luxe", "haut de gamme"],
            "darija": ["poignée torsadée", "yddin mfetlin", "yeddin mfetlin", "mfetlin", "mfetel", "mfetla",
                       "mfatel", "mfettel", "mfetlen", "mftel", "7bel", "l7bel", "hbel", "lhbel", "yddin dyal l7bel"],
            "ar": ["مقبض مفتول", "يد مفتولة", "مقابض مفتولة", "مفتول", "حبل", "أيدي مفتولة"],
            "en": ["twisted handles", "twisted handle", "twisted paper handles", "twisted", "cord handles",
                   "rope handles", "rope handle", "rope", "string handles", "cord", "paper cord", "luxury"],
        },
    },
]

PRODUCT_TYPES: list[str] = [product["type"] for product in PRODUCTS]
"""Noms des quatre familles de produits, dans l'ordre des identifiants."""

# Types de papier : expression rencontrée dans une commande -> code du papier.
# L'ordre compte : les expressions les plus précises sont testées en premier.
PAPER_TYPES: dict[str, str] = {
    "kraft blanchi": "KB",
    "kraft ecru": "KE",
    "kraft naturel": "KE",
    "kraft": "KE",
    "blanchi": "KB",
    "ecru": "KE",
}

PAPER_TYPE_NAMES: dict[str, str] = {"KB": "Kraft Blanchi", "KE": "Kraft Écru"}
"""Libellé de chaque code de papier."""

# Fournisseurs de papier : nom -> code à trois lettres utilisé dans les codes articles.
SUPPLIERS: dict[str, str] = {
    "mondi": "MON",
    "nordic": "NOR",
    "billerud": "BIL",
    "smurfit": "SMU",
    "default": "STD",
}

STANDARD_GRAMMAGES: list[int] = [40, 50, 60, 70, 80, 90, 100, 120, 140]
"""Grammages standards du papier, en g/m²."""

STANDARD_LAIZES: list[int] = [15, 18, 20, 22, 25, 28, 30, 32, 35, 40, 45, 50]
"""Laizes standards (largeur de la bobine de papier), en cm."""

COMPANY_INFO: dict[str, str] = {
    "name": "TECPAP",
    "description": "Fabrication de sacs en papier Kraft",
    "address": "Parc Industriel CFCIM Bouskoura, Lot n°85, Casablanca",
    "phone": "+212 (0)5 22 86 56 83",
    "email": "info@tecpap.net",
    "website": "www.tecpap.ma",
}

# Expressions qui signalent une demande de renouvellement (« la même commande que d'habitude »).
# Familles : comparaison avec l'habitude (« comme d'habitude », « kif l3ada », « كالعادة »),
# comparaison avec une commande passée (« comme la dernière fois », « b7al l'mra li fatet »,
# « same as last time »), « même commande » (« nafs la commande », « نفس الطلب »), verbes de
# répétition (« refaire la commande », « 3awd la commande », « reorder »). Les règles
# (:func:`tecpap.extraction.rules.detect_reorder`) reconnaissent aussi les combinaisons de ces familles
# (« comme le mois dernier », « b7al chhar li fat », « same order as last week »).
REORDER_PATTERNS: dict[str, tuple[str, ...]] = {
    "fr": (
        "comme d'habitude",
        "comme toujours",
        "même commande",
        "la même chose",
        "comme la dernière fois",
        "comme avant",
        "renouveler",
        "renouvellement",
        "relancer",
        "réapprovisionnement",
        "habituelle",
        "comme d'hab",
        "comme la fois dernière",
        "comme le mois dernier",
        "comme la semaine dernière",
        "comme la dernière commande",
        "comme la commande précédente",
        "comme précédemment",
        "la même que la dernière fois",
        "refaire la commande",
        "reprendre la même commande",
        "réassort",
    ),
    "darija": (
        "kif dima",
        "b7al dima",
        "bhal dima",
        "bhal l3ada",
        "b7al l3ada",
        "nafs la commande",
        "nfs l commande",
        "kima dima",
        "kif l3ada",
        "kima l3ada",
        "b7al l'mra li fatet",
        "b7al lmerra li fatet",
        "kif lmerra li fatet",
        "b7al chhar li fat",
        "nafs l commande",
        "nefs la commande",
        "3awd la commande",
        "3awdo lina la commande",
    ),
    "ar": (
        "كيف ديما",
        "بحال ديما",
        "كالعادة",
        "نفس الطلبية",
        "نفس الطلب",
        "بحال المرة الفايتة",
        "كيف العادة",
        "بحال العادة",
        "كيما ديما",
        "بحال المرة اللي فاتت",
        "بحال الشهر اللي فات",
        "نفس الكوموند",
        "مثل المرة السابقة",
        "مثل الطلبية السابقة",
        "تجديد الطلبية",
        "إعادة الطلبية",
        "عاود نفس الطلبية",
        "كالمعتاد",
        "كما جرت العادة",
        "كما هو معتاد",
    ),
    "en": (
        "same as usual",
        "same as before",
        "as usual",
        "same as last time",
        "same order as last time",
        "same order as last month",
        "same order",
        "like last time",
        "repeat our last order",
        "repeat the last order",
        "our usual order",
        "the usual order",
        "reorder",
        "as always",
    ),
}

ALL_REORDER_PATTERNS: list[str] = list(
    dict.fromkeys(pattern for patterns in REORDER_PATTERNS.values() for pattern in patterns)
)
"""Toutes les expressions de renouvellement, toutes langues confondues, sans doublon."""

REQUIRED_ORDER_FIELDS: tuple[str, ...] = ("entreprise_cliente", "type_produit", "quantite")
"""Champs sans lesquels une commande ne peut pas être saisie dans l'ERP."""


def get_product(product_id: int) -> dict[str, Any] | None:
    """Renvoie le produit d'identifiant ``product_id``, ou ``None`` s'il n'existe pas."""
    return next((product for product in PRODUCTS if product["id"] == product_id), None)


def get_product_by_type(product_type: str) -> dict[str, Any] | None:
    """Renvoie le produit dont le nom correspond exactement à ``product_type`` (sans tenir compte de la casse)."""
    wanted = product_type.strip().lower()
    return next((product for product in PRODUCTS if product["type"].lower() == wanted), None)


def get_product_by_code(code: str) -> dict[str, Any] | None:
    """Renvoie le produit de code ``code`` (par exemple « SFP »), ou ``None``."""
    wanted = code.strip().upper()
    return next((product for product in PRODUCTS if product["code"] == wanted), None)


def product_keywords(language: str) -> dict[str, list[str]]:
    """Renvoie, pour une langue (``fr``, ``darija``, ``ar`` ou ``en``), les mots-clés de chaque produit."""
    return {product["type"]: list(product["keywords"].get(language, [])) for product in PRODUCTS}
