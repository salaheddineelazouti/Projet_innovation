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
- ``ar`` : arabe en caractères arabes (arabe standard ou darija).
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
            "fr": ["sachet", "sachets", "fond plat", "sachet kraft", "sachet sandwich"],
            "darija": ["sachet", "sachiyat", "sachet dyal sandwich", "sachet tacos"],
            "ar": ["ساشي", "ساشة", "أكياس صغيرة", "كيس مسطح"],
        },
    },
    {
        "id": 2,
        "type": "Sac fond carré sans poignées",
        "code": "SFCSP",
        "description": "Sacs fond carré sans poignées",
        "usages": ["emballage standard"],
        "keywords": {
            "fr": ["sans poignée", "sans poignées", "fond carré"],
            "darija": ["bla yddin", "bla poignée", "sac bla poignée"],
            "ar": ["بدون مقبض", "بدون يد"],
        },
    },
    {
        "id": 3,
        "type": "Sac fond carré avec poignées plates",
        "code": "SFCPP",
        "description": "Sacs fond carré avec poignées plates",
        "usages": ["sacs shopping"],
        "keywords": {
            "fr": ["poignée plate", "poignées plates", "sac shopping"],
            "darija": ["poignée plate", "yddin plates"],
            "ar": ["مقبض مسطح", "يد مسطحة"],
        },
    },
    {
        "id": 4,
        "type": "Sac fond carré avec poignées torsadées",
        "code": "SFCPT",
        "description": "Sacs fond carré avec poignées torsadées",
        "usages": ["sacs premium"],
        "keywords": {
            "fr": ["poignée torsadée", "poignées torsadées", "torsadé", "cordelette", "premium"],
            "darija": ["poignée torsadée", "yddin mfetlin"],
            "ar": ["مقبض مفتول", "يد مفتولة"],
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
    ),
    "ar": (
        "كيف ديما",
        "بحال ديما",
        "كالعادة",
        "نفس الطلبية",
        "نفس الطلب",
        "بحال المرة الفايتة",
    ),
    "en": (
        "same as usual",
        "same as before",
        "as usual",
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
    """Renvoie, pour une langue (``fr``, ``darija`` ou ``ar``), les mots-clés de chaque produit."""
    return {product["type"]: list(product["keywords"].get(language, [])) for product in PRODUCTS}
