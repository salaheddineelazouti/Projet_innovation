"""Schéma d'une commande extraite par le modèle de langue (validation avec pydantic).

La réponse JSON du modèle n'est jamais utilisée telle quelle : :func:`parse_llm_order` la fait
passer par le modèle :class:`ExtractedOrder`, qui reprend exactement les champs demandés dans
l'instruction d'extraction (:func:`tecpap.extraction.prompts.build_extraction_prompt`).

La validation est tolérante, car le modèle écrit souvent les nombres comme dans le message :

- tous les champs sont facultatifs ; une clé inconnue est ignorée ;
- les nombres écrits en texte (« 10 000 », « 10.000 », « 2 mille », « 1,5 million », « ٣٠٠٠ »)
  sont lus par :func:`tecpap.extraction.rules.parse_quantity` ;
- les booléens acceptent ``true`` / ``false``, « oui » / « non », « yes » / « no », 1 / 0 ;
- les textes sont nettoyés (caractères de contrôle retirés, espaces regroupés, longueur
  limitée) et « null », « N/A » ou « inconnu » valent ``None`` ;
- une valeur invalide devient ``None`` au lieu de lever une exception : une réponse mal formée
  ne bloque jamais le traitement d'un message, la commande reste incomplète et sera complétée
  par l'équipe commerciale.

Le contrôle métier (anomalies, champs manquants, confiance) est fait ensuite par
:func:`tecpap.extraction.rules.normalize_order`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from tecpap.extraction.rules import (
    CURRENCY_CODES,
    LONG_TEXT_MAX,
    NAME_MAX,
    ORDER_FIELD_KINDS,
    SHORT_TEXT_MAX,
    clean_text,
    fold,
    normalize_currency,
    parse_quantity,
    to_bool,
)

logger = logging.getLogger(__name__)

__all__ = ["ORDER_FIELDS", "ExtractedOrder", "parse_llm_order"]


def _fields(*kinds: str) -> tuple[str, ...]:
    return tuple(name for name, kind in ORDER_FIELD_KINDS.items() if kind in kinds)


class ExtractedOrder(BaseModel):
    """Commande renvoyée par le modèle d'extraction, au format des champs SAGE X3.

    Chaque champ vaut ``None`` quand il est absent ou illisible. Les champs ``grammage`` et
    ``laize`` sont des entiers quand la valeur est ronde.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    numero_commande: str | None = None
    ligne_commande: int | None = None
    site_vente: str | None = None
    code_client: str | None = None
    entreprise_cliente: str | None = None
    code_article: str | None = None
    type_produit: str | None = None
    nature_produit: str | None = None
    quantite: float | None = None
    unite: str | None = None
    date_commande: str | None = None
    date_livraison: str | None = None
    commercial: str | None = None
    type_sac: str | None = None
    format_sac: str | None = None
    type_papier: str | None = None
    grammage: int | float | None = None
    laize: int | float | None = None
    impression_client: str | None = None
    prix_unitaire: float | None = None
    prix_total: float | None = None
    devise: str | None = None
    informations_supplementaires: str | None = None
    confiance: float | None = None
    est_bon_commande: bool | None = None

    @field_validator(*_fields("identifier", "short"), mode="before")
    @classmethod
    def _short_text(cls, value: Any) -> str | None:
        return clean_text(value, max_length=SHORT_TEXT_MAX)

    @field_validator(*_fields("name"), mode="before")
    @classmethod
    def _name(cls, value: Any) -> str | None:
        return clean_text(value, max_length=NAME_MAX)

    @field_validator(*(name for name in _fields("text") if name != "impression_client"), mode="before")
    @classmethod
    def _long_text(cls, value: Any) -> str | None:
        return clean_text(value, max_length=LONG_TEXT_MAX, multiline=True)

    @field_validator("impression_client", mode="before")
    @classmethod
    def _printing(cls, value: Any) -> str | None:
        # Le modèle répond parfois par un booléen (« impression demandée : oui »).
        if isinstance(value, bool):
            return "oui" if value else "non"
        return clean_text(value, max_length=LONG_TEXT_MAX, multiline=True)

    @field_validator(*_fields("date"), mode="before")
    @classmethod
    def _date(cls, value: Any) -> str | None:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return clean_text(value, max_length=SHORT_TEXT_MAX)

    @field_validator(*_fields("number"), mode="before")
    @classmethod
    def _number(cls, value: Any) -> float | None:
        return parse_quantity(value)

    @field_validator(*_fields("measure"), mode="before")
    @classmethod
    def _measure(cls, value: Any) -> int | float | None:
        number = parse_quantity(value)
        if number is None:
            return None
        return int(number) if number.is_integer() else number

    @field_validator(*_fields("integer"), mode="before")
    @classmethod
    def _integer(cls, value: Any) -> int | None:
        number = parse_quantity(value)
        return int(number) if number is not None and number.is_integer() else None

    @field_validator(*_fields("boolean"), mode="before")
    @classmethod
    def _boolean(cls, value: Any) -> bool | None:
        return to_bool(value)


ORDER_FIELDS: tuple[str, ...] = tuple(ExtractedOrder.model_fields)
"""Noms des champs d'une commande extraite, dans l'ordre de l'instruction d'extraction."""


def _canonical_key(key: str) -> str:
    """Clé ramenée à la forme du schéma : « Quantité » → « quantite », « date-livraison » → « date_livraison »."""
    return "_".join(fold(key).replace("-", " ").split())


def _canonical_fields(raw: Mapping[Any, Any]) -> dict[str, Any]:
    """Valeurs du dictionnaire brut rangées sous les noms du schéma ; une clé exacte l'emporte sur une variante."""
    values: dict[str, Any] = {}
    exact: set[str] = set()
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        name = _canonical_key(key)
        if name not in ORDER_FIELD_KINDS or name in exact:
            continue
        values[name] = value
        if key == name:
            exact.add(name)
    return values


def _currency_from_prices(values: Mapping[str, Any]) -> str | None:
    """Devise écrite dans un prix (« 2,10 DH », « 1.95 € ») quand le champ devise est vide."""
    for name in ("prix_unitaire", "prix_total"):
        raw = values.get(name)
        if isinstance(raw, str):
            currency = normalize_currency(raw)
            if currency in CURRENCY_CODES:
                return currency
    return None


def _validate_each_field(values: Mapping[str, Any]) -> ExtractedOrder:
    """Validation champ par champ : un champ refusé est ignoré sans faire échouer les autres."""
    accepted: dict[str, Any] = {}
    for name, value in values.items():
        try:
            accepted[name] = getattr(ExtractedOrder.model_validate({name: value}), name)
        except ValidationError:
            logger.debug("Champ %s ignoré : valeur invalide", name)
    return ExtractedOrder.model_construct(**accepted)


def parse_llm_order(raw: Any) -> dict[str, Any]:
    """Valide la réponse JSON du modèle d'extraction et renvoie les champs utilisables.

    Args:
        raw: objet JSON décodé renvoyé par le modèle. Toute autre valeur (liste, texte,
            ``None``) donne un dictionnaire vide.

    Returns:
        Les champs du schéma dont la valeur est lisible, valeurs converties (nombres,
        booléens, textes nettoyés). Les champs absents ou invalides ne figurent pas dans le
        dictionnaire : les valeurs par défaut de l'enregistrement en base (unité « US », site
        « SXP »…) s'appliquent alors. Les clés sont reconnues sans tenir compte de la casse ni
        des accents (« Quantité » → ``quantite``). Une devise écrite dans un prix
        (« 2,10 DH ») remplit le champ ``devise`` quand il est vide. La fonction ne lève jamais
        d'exception.
    """
    if not isinstance(raw, Mapping):
        return {}
    values = _canonical_fields(raw)
    if clean_text(values.get("devise"), max_length=SHORT_TEXT_MAX) is None:
        currency = _currency_from_prices(values)
        if currency:
            values["devise"] = currency
    try:
        order = ExtractedOrder.model_validate(values)
    except ValidationError:
        logger.warning("Réponse du modèle partiellement invalide : validation champ par champ")
        order = _validate_each_field(values)
    return order.model_dump(exclude_none=True)
