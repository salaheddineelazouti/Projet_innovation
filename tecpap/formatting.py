"""Mise en forme française des nombres, des montants et des dates affichés aux utilisateurs.

Ces fonctions servent au tableau de bord (filtres Jinja enregistrés par
:mod:`tecpap.web.routes.pages`), aux réponses de l'assistant WhatsApp et aux notifications.

Conventions (norme française de l'Imprimerie nationale) :

- séparateur des milliers : espace fine insécable (U+202F) : « 10 000 » ;
- séparateur décimal : virgule : « 2,5 » ; un nombre entier s'écrit sans « ,0 » ;
- espace insécable (U+00A0) entre un nombre et son unité ou sa devise, pour qu'un retour à la
  ligne ne les sépare jamais ;
- dates au format jour/mois/année (« 25/10/2026 14:03 »), converties de l'UTC (temps universel
  coordonné, référence des horodatages de la base SQLite) vers le fuseau horaire de l'entreprise
  (``Africa/Casablanca`` par défaut).

Texte arabe : l'arabe s'écrit de droite à gauche. L'algorithme bidirectionnel Unicode (UAX #9),
qui ordonne les caractères à l'affichage, coupe un nombre en deux segments séparés quand ses
groupes de chiffres sont séparés par une espace ordinaire (classe « WS ») : « 10 000 » s'affiche
alors « 000 10 ». L'espace fine insécable et la virgule appartiennent à la classe « CS »
(séparateur numérique commun) : entourées de chiffres, elles restent dans le nombre, qui s'affiche
d'un seul bloc dans une phrase arabe comme dans une phrase française. Le même format sert donc aux
trois langues de réponse (français, darija, arabe).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone, tzinfo
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

__all__ = [
    "DECIMAL_SEPARATOR",
    "DEFAULT_TIMEZONE",
    "MISSING_VALUE",
    "THOUSANDS_SEPARATOR",
    "UNIT_SEPARATOR",
    "format_amount",
    "format_date",
    "format_datetime",
    "format_number",
    "format_quantity",
    "parse_timestamp",
    "resolve_timezone",
]

THOUSANDS_SEPARATOR = " "
"""Espace fine insécable (U+202F) entre les groupes de trois chiffres."""

DECIMAL_SEPARATOR = ","
"""Virgule décimale."""

UNIT_SEPARATOR = " "
"""Espace insécable (U+00A0) entre un nombre et son unité ou sa devise."""

MISSING_VALUE = "—"
"""Texte affiché à la place d'une valeur absente."""

DEFAULT_TIMEZONE = "Africa/Casablanca"
"""Fuseau horaire de l'entreprise (Casablanca), utilisé quand aucun autre n'est configuré."""

# Séparateurs (milliers, décimales) par langue. L'entrée « ar » est identique aux autres : les deux
# caractères sont de classe bidirectionnelle « CS » et s'affichent correctement de droite à gauche.
_SEPARATORS: dict[str, tuple[str, str]] = {
    "fr": (THOUSANDS_SEPARATOR, DECIMAL_SEPARATOR),
    "darija": (THOUSANDS_SEPARATOR, DECIMAL_SEPARATOR),
    "ar": (THOUSANDS_SEPARATOR, DECIMAL_SEPARATOR),
}
_LANGUAGE_ALIASES = {"darija_latin": "darija", "darija_ar": "darija"}

# Un nombre dont l'exposant décimal dépasse ce seuil (plus de 10^100) n'est pas une quantité ni un montant
# réaliste : il est écrit tel quel plutôt que mis en forme, ce qui évite aussi de produire des milliers de chiffres.
_MAX_EXPONENT = 100
# Contexte de calcul décimal assez précis pour arrondir tout nombre de moins de _MAX_EXPONENT chiffres.
_ROUNDING_CONTEXT = Context(prec=_MAX_EXPONENT + 20)

_DATE_FORMAT = "%d/%m/%Y"
_DATETIME_FORMAT = "%d/%m/%Y %H:%M"


def _separators(language: str | None) -> tuple[str, str]:
    """Séparateurs des milliers et des décimales de la langue (français pour une langue inconnue)."""
    code = (language or "fr").strip().lower() if isinstance(language, str) else "fr"
    return _SEPARATORS.get(_LANGUAGE_ALIASES.get(code, code), _SEPARATORS["fr"])


def _to_decimal(value: Any) -> Decimal | None:
    """Valeur numérique en :class:`~decimal.Decimal`, ou ``None`` si elle n'est pas un nombre fini.

    Les chaînes acceptées sont celles qu'écrit Python ou SQLite (« 10000 », « 2.5 », « 1e3 »),
    ainsi que la virgule décimale française (« 2,5 ») ; une chaîne qui contient des séparateurs de
    milliers ou du texte (« 10 000 », « 5000 pièces ») n'est pas interprétée.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        number = Decimal(repr(value))
    elif isinstance(value, str):
        text = value.strip()
        if text.count(",") == 1 and "." not in text:
            text = text.replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    return number if number.is_finite() else None


def _group_thousands(digits: str, separator: str) -> str:
    """Insère ``separator`` toutes les trois positions en partant de la droite (« 1234567 » → « 1 234 567 »)."""
    head = len(digits) % 3 or 3
    groups = [digits[:head]] + [digits[index:index + 3] for index in range(head, len(digits), 3)]
    return separator.join(groups)


def format_number(value: Any, *, decimals: int | None = 2, min_decimals: int = 0, language: str | None = "fr") -> str:
    """Nombre écrit à la française : « 10 000 », « 2,5 », « 1 234,50 ».

    Args:
        value: entier, réel, :class:`~decimal.Decimal` ou chaîne numérique (« 2.5 », « 2,5 »).
        decimals: nombre maximal de décimales (arrondi au plus proche, une moitié vers le haut :
            0,125 donne « 0,13 ») ; ``None`` garde toutes les décimales de la valeur.
        min_decimals: nombre minimal de décimales écrites (2 pour un montant : « 12,00 »).
        language: langue d'affichage (``fr``, ``darija``, ``ar``) ; une langue inconnue donne le français.

    Returns:
        Le nombre mis en forme, :data:`MISSING_VALUE` pour ``None`` ou une chaîne vide, et la
        valeur d'origine convertie en texte quand elle n'est pas un nombre ou qu'elle dépasse
        10^100 en valeur absolue.
    """
    number = _to_decimal(value)
    if number is None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return MISSING_VALUE
        return str(value)
    if number.adjusted() > _MAX_EXPONENT or (decimals is None and number.adjusted() < -_MAX_EXPONENT):
        return str(value)
    if decimals is not None:
        number = number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP, context=_ROUNDING_CONTEXT)
    sign = "-" if number < 0 else ""
    text = f"{abs(number):f}"
    integer_part, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    if len(fraction) < min_decimals:
        fraction = fraction.ljust(min_decimals, "0")
    thousands, decimal_separator = _separators(language)
    result = sign + _group_thousands(integer_part, thousands)
    return f"{result}{decimal_separator}{fraction}" if fraction else result


def format_quantity(value: Any, unit: str | None = None, language: str | None = "fr", *, decimals: int = 2) -> str:
    """Quantité écrite à la française, suivie de son unité : « 10 000 », « 2,5 kg », « 5 000 pièces ».

    Un nombre entier s'écrit sans décimale (« 5000.0 » donne « 5 000 »). L'unité est ajoutée telle
    qu'elle est donnée, après une espace insécable ; une valeur absente donne :data:`MISSING_VALUE`
    sans unité.

    Args:
        value: quantité (entier, réel, :class:`~decimal.Decimal` ou chaîne numérique).
        unit: libellé de l'unité à écrire après le nombre (aucune unité si vide).
        language: langue d'affichage (``fr``, ``darija``, ``ar``).
        decimals: nombre maximal de décimales.
    """
    text = format_number(value, decimals=decimals, language=language)
    label = unit.strip() if isinstance(unit, str) else ""
    if text == MISSING_VALUE or not label:
        return text
    return f"{text}{UNIT_SEPARATOR}{label}"


def format_amount(value: Any, currency: str | None = "MAD", *, language: str | None = "fr") -> str:
    """Montant avec deux décimales, suivi de sa devise : « 1 234,50 MAD ».

    Args:
        value: montant (entier, réel, :class:`~decimal.Decimal` ou chaîne numérique).
        currency: code de la devise écrit après le montant (``MAD`` par défaut) ; aucune devise si vide.
        language: langue d'affichage (``fr``, ``darija``, ``ar``).

    Returns:
        Le montant mis en forme, ou :data:`MISSING_VALUE` (sans devise) pour une valeur absente.
    """
    text = format_number(value, decimals=2, min_decimals=2, language=language)
    label = currency.strip() if isinstance(currency, str) else ""
    if _to_decimal(value) is None or not label:
        return text
    return f"{text}{UNIT_SEPARATOR}{label}"


@lru_cache(maxsize=16)
def resolve_timezone(timezone_name: str | None = DEFAULT_TIMEZONE) -> tzinfo:
    """Fuseau horaire nommé (base IANA), ou UTC quand ses données sont introuvables.

    Sous Windows, Python ne trouve les fuseaux horaires que si le paquet ``tzdata`` est installé ;
    sans lui, l'affichage reste en UTC et un avertissement est journalisé une seule fois par nom.
    """
    name = (timezone_name or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.warning(
            "Fuseau horaire %r introuvable (paquet tzdata absent ou nom inconnu) : les dates sont affichées en UTC.",
            name,
        )
        return timezone.utc


def parse_timestamp(value: Any) -> datetime | date | None:
    """Horodatage lu en base : :class:`~datetime.datetime` en UTC, :class:`~datetime.date` ou ``None``.

    Formats acceptés : ISO 8601 (« 2026-10-25T14:03:00 », avec ou sans microsecondes, « Z » ou
    décalage horaire), format SQLite (« 2026-10-25 14:03:00 ») et date seule (« 2026-10-25 »).
    Un horodatage sans fuseau horaire est considéré comme exprimé en UTC (``CURRENT_TIMESTAMP``
    de SQLite) ; une date seule reste une date, sans conversion de fuseau horaire.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            if len(text) == 10:
                return date.fromisoformat(text)
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_datetime(value: Any, timezone_name: str | None = DEFAULT_TIMEZONE, with_time: bool = True) -> str:
    """Horodatage UTC écrit dans le fuseau horaire de l'entreprise : « 25/10/2026 15:03 ».

    Args:
        value: chaîne ISO 8601 ou SQLite, :class:`~datetime.datetime` (sans fuseau horaire : UTC)
            ou :class:`~datetime.date`.
        timezone_name: nom IANA du fuseau horaire d'affichage (``Africa/Casablanca`` par défaut).
        with_time: ``False`` n'écrit que la date (« 25/10/2026 »).

    Returns:
        La date mise en forme, :data:`MISSING_VALUE` pour une valeur absente, et la valeur
        d'origine convertie en texte quand elle n'est pas une date reconnue. Une date seule
        (sans heure) s'écrit sans heure ni conversion.
    """
    moment = parse_timestamp(value)
    if moment is None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return MISSING_VALUE
        return str(value)
    if not isinstance(moment, datetime):
        return moment.strftime(_DATE_FORMAT)
    local = moment.astimezone(resolve_timezone(timezone_name))
    return local.strftime(_DATETIME_FORMAT if with_time else _DATE_FORMAT)


def format_date(value: Any, timezone_name: str | None = DEFAULT_TIMEZONE) -> str:
    """Date seule (« 25/10/2026 ») d'un horodatage UTC, dans le fuseau horaire de l'entreprise."""
    return format_datetime(value, timezone_name, with_time=False)
