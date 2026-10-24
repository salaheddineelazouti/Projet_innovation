"""Codes articles TECPAP au format SAGE X3.

Format d'un code : ``[TYPE][GRAMMAGE]L[LAIZE][FOURNISSEUR]``, où
- TYPE est ``KB`` (kraft blanchi) ou ``KE`` (kraft écru, aussi appelé naturel) ;
- GRAMMAGE est le grammage du papier en g/m² ;
- LAIZE est la laize (largeur de la bobine de papier) en cm ;
- FOURNISSEUR est un code à trois lettres facultatif.

Exemple : ``KB100L28MON`` = kraft blanchi 100 g/m², laize 28 cm, papier Mondi.

Les tables de correspondance (types de papier, fournisseurs, grammages et laizes standards)
viennent de :mod:`tecpap.catalog`.
"""

from __future__ import annotations

import re
from typing import Any

from tecpap.catalog import (
    PAPER_TYPE_NAMES,
    PAPER_TYPES,
    PRODUCTS,
    STANDARD_GRAMMAGES,
    STANDARD_LAIZES,
    SUPPLIERS,
)

__all__ = [
    "PAPER_TYPES",
    "SAC_TYPES",
    "STANDARD_GRAMMAGES",
    "STANDARD_LAIZES",
    "SUPPLIERS",
    "generate_article_code",
    "get_all_standard_codes",
    "parse_article_code",
    "suggest_article_code_from_description",
]

SAC_TYPES: dict[str, dict[str, str]] = {
    product["type"]: {
        "code": product["code"],
        "description": f"{product['description']} - {', '.join(product['usages'])}",
    }
    for product in PRODUCTS
}
"""Familles de sacs : nom -> code produit et description."""

DEFAULT_PAPER_CODE = "KE"
DEFAULT_GRAMMAGE = "80"

_GRAMMAGE_PATTERNS = (
    r"(\d+)\s*g(?:\/m2|ram|r)?",
    r"(\d+)\s*gr",
    r"grammage\s*[\:\=]?\s*(\d+)",
)

_LAIZE_PATTERNS = (
    r"laize?\s*[\:\=]?\s*(\d+)",
    r"l(\d+)",
    r"largeur\s*[\:\=]?\s*(\d+)",
)


def generate_article_code(paper_type: str | None = None, grammage: Any = None, laize: Any = None,
                          supplier: str | None = None) -> str:
    """Construit un code article TECPAP.

    Args:
        paper_type: type de papier en texte libre (« kraft blanchi », « kraft écru »…) ;
            kraft écru par défaut.
        grammage: grammage en g/m² ; 80 par défaut.
        laize: laize en cm ; omise si inconnue.
        supplier: fournisseur (« Mondi », « Nordic »…) ; omis si inconnu.

    Returns:
        Le code article, par exemple ``KB100L28MON``.
    """
    type_code = DEFAULT_PAPER_CODE
    if paper_type:
        paper_type_lower = paper_type.lower()
        for key, code in PAPER_TYPES.items():
            if key in paper_type_lower:
                type_code = code
                break

    gram = str(grammage) if grammage else DEFAULT_GRAMMAGE
    laize_str = f"L{laize}" if laize else ""

    supplier_code = ""
    if supplier:
        supplier_lower = supplier.lower()
        for key, code in SUPPLIERS.items():
            if key in supplier_lower:
                supplier_code = code
                break

    return f"{type_code}{gram}{laize_str}{supplier_code}"


def parse_article_code(code: str | None) -> dict[str, Any] | None:
    """Décompose un code article en type de papier, grammage, laize et fournisseur."""
    if not code:
        return None

    result: dict[str, Any] = {
        "paper_type": None,
        "grammage": None,
        "laize": None,
        "supplier": None,
        "raw_code": code,
    }

    for prefix in ("KB", "KE"):
        if code.startswith(prefix):
            result["paper_type"] = PAPER_TYPE_NAMES[prefix]
            code = code[len(prefix):]
            break

    grammage_match = re.match(r"(\d+)", code)
    if grammage_match:
        result["grammage"] = int(grammage_match.group(1))
        code = code[len(grammage_match.group(1)):]

    laize_match = re.match(r"L(\d+)", code)
    if laize_match:
        result["laize"] = int(laize_match.group(1))
        code = code[len(laize_match.group(0)):]

    if code:
        for name, short_code in SUPPLIERS.items():
            if code == short_code:
                result["supplier"] = name.capitalize()
                break
        if not result["supplier"]:
            result["supplier"] = code

    return result


def _first_number(patterns: tuple[str, ...], text: str) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def suggest_article_code_from_description(description: str | None) -> str | None:
    """Propose un code article à partir d'une description libre (« kraft blanchi 100g laize 28 »)."""
    if not description:
        return None

    description_lower = description.lower()
    paper_type = next((key for key in PAPER_TYPES if key in description_lower), None)
    grammage = _first_number(_GRAMMAGE_PATTERNS, description_lower)
    laize = _first_number(_LAIZE_PATTERNS, description_lower)
    supplier = next((key for key in SUPPLIERS if key in description_lower), None)

    return generate_article_code(paper_type, grammage, laize, supplier)


def get_all_standard_codes() -> list[dict[str, Any]]:
    """Liste les codes articles standards (chaque type de papier × grammage × laize standards)."""
    codes = []
    for paper_code in PAPER_TYPES.values():
        type_name = PAPER_TYPE_NAMES[paper_code]
        for grammage in STANDARD_GRAMMAGES:
            for laize in STANDARD_LAIZES:
                codes.append({
                    "code": f"{paper_code}{grammage}L{laize}",
                    "description": f"{type_name} {grammage}g/m² Laize {laize}cm",
                    "paper_type": type_name,
                    "grammage": grammage,
                    "laize": laize,
                })
    return codes
