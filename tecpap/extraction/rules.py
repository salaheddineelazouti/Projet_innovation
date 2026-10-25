"""Règles déterministes : lecture, normalisation et contrôle des commandes extraites.

Le modèle de langue n'est jamais la seule source de vérité. Chaque commande qu'il renvoie passe
par les règles de ce module, qui s'exécutent sans réseau ni modèle et donnent toujours le même
résultat pour le même texte. Elles servent aussi d'extraction de secours quand le modèle n'est
pas disponible (:func:`rule_based_extract`).

Méthodes
--------
Normalisation du texte
    Forme Unicode NFKC (caractères de compatibilité ramenés à leur forme usuelle, espaces
    insécables comprises), chiffres arabo-indiens (٠ à ٩) remplacés par les chiffres 0 à 9,
    apostrophes et tirets unifiés, trait d'allongement arabe (tatweel) retiré
    (:func:`prepare_text`). Pour les comparaisons seulement, le texte est ensuite décomposé
    (forme NFKD), privé de ses signes diacritiques (accents latins, voyelles brèves et hamza de
    l'écriture arabe), ses variantes de lettres arabes unifiées (ى et ی écrits ي) et mis en
    minuscules : c'est la forme de comparaison de :func:`fold`. Les valeurs renvoyées (nom du
    client) gardent l'écriture d'origine.
Racinisation légère
    Réduction d'un mot à une forme commune par suppression des préfixes et terminaisons les
    plus fréquents, sans analyse grammaticale (« light stemming ») : marque du pluriel en
    français et en anglais (s, x, e final), article « ال » et prépositions collées (ب, ل, و)
    ainsi que les terminaisons « ات », « ين » et « ة » en arabe. La reconnaissance des mots-clés
    du catalogue devient insensible au singulier, au pluriel et au genre.
Lecture des nombres
    Chiffres avec séparateur des milliers (espace, point, virgule, apostrophe) ou suffixe « k »,
    nombres en lettres en français, en anglais, en darija (« jouj alaf », « tlata d alaf »,
    « khamsmya ») et en arabe (« ثلاث آلاف », « تلاتة د الاف »), combinaisons de chiffres et de
    lettres (« 2 mille 500 », « 1,5 million »). Le mot « wahed » / « واحد » placé devant un nombre
    est un article (« un ») et non la quantité 1.
Choix de la quantité dans un message
    Les adresses email, adresses web, téléphones, dates, dimensions et heures sont d'abord
    masqués. Chaque nombre restant reçoit un score selon son contexte : nom de produit ou unité
    qui suit (+3), libellé « quantité » qui précède (+3), formule « au lieu de » qui suit (+3),
    reprise après « non » ou « pardon » (+3), verbe de commande qui précède (+1), nombre qui suit la
    description d'un article sans verbe ni unité (« sachets fond plat : 5000 », +1). Un nombre suivi
    d'une devise ou d'une unité de mesure (g, cm, jours…), précédé d'un libellé de prix ou de
    référence, ou présenté comme un ajout (« zid 1000 ») est écarté. Le meilleur score l'emporte ;
    sans aucun indice favorable, aucune quantité n'est retenue. Un tableau dont les colonnes sont
    séparées par « | » est lu d'après son en-tête (colonne « Qté »). L'unité est celle que le client a
    écrite après le nombre (« pièces », « carton », « kg », « paquet ») ; « US » (unité de stock) quand
    la quantité n'a pas d'unité.
Motifs de texte
    Expressions régulières pour les dates, les téléphones, les références de commande et le nom
    du client : libellé « Client : », présentation (« ana X mn Y », « c'est X du Y »,
    « Amina from X », « أنا X من Y »), « de la part de X », « pour le compte de X », « smitou X »,
    « pour X », « l X », signature de l'email. Chaque motif a une priorité ; le motif le plus sûr
    l'emporte, et un commerce nommé l'emporte sur le prénom de la personne qui écrit.
Contrôles métier
    :func:`normalize_order` applique tous les normalisateurs, rédige les anomalies destinées à
    l'équipe commerciale et calcule une confiance déterministe.

Principe commun : en cas de doute, la valeur est ``None``. Aucune règle n'invente une valeur
absente du texte.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from tecpap.catalog import (
    ALL_REORDER_PATTERNS,
    PAPER_TYPE_NAMES,
    PAPER_TYPES,
    PRODUCTS,
    REQUIRED_ORDER_FIELDS,
    STANDARD_GRAMMAGES,
    STANDARD_LAIZES,
)
from tecpap.db.database import GENERIC_CLIENT_PREFIXES

logger = logging.getLogger(__name__)

__all__ = [
    "CANONICAL_UNITS",
    "CURRENCY_CODES",
    "LONG_TEXT_MAX",
    "MAX_PLAUSIBLE_QUANTITY",
    "NAME_MAX",
    "ORDER_FIELD_KINDS",
    "SHORT_TEXT_MAX",
    "NormalizationResult",
    "clean_text",
    "detect_reorder",
    "extract_client_name",
    "fold",
    "normalize_currency",
    "normalize_date",
    "normalize_order",
    "normalize_phone",
    "normalize_product_type",
    "normalize_unit",
    "parse_quantity",
    "prepare_text",
    "rule_based_extract",
    "to_bool",
]

MAX_PLAUSIBLE_QUANTITY = 5_000_000
"""Quantité au-delà de laquelle une commande est signalée comme invraisemblable."""

PRICE_TOLERANCE = 0.02
"""Écart relatif toléré entre le prix total et la quantité multipliée par le prix unitaire."""

ANOMALY_PENALTY = 10
"""Points de confiance retirés pour chaque anomalie."""

CONFIDENCE_MARGIN = 10
"""La confiance ne dépasse jamais le score de complétude augmenté de cette marge."""

MAX_TEXT_CHARS = 20_000
"""Longueur maximale de message analysée par les règles (le reste est ignoré)."""

MAX_VALUE_CHARS = 200
"""Longueur maximale d'une valeur isolée (quantité, date) ; au-delà, la valeur est illisible."""

SHORT_TEXT_MAX = 100
"""Longueur maximale d'un identifiant ou d'un champ court (unité, devise, référence)."""

NAME_MAX = 200
"""Longueur maximale d'un nom (client, produit, commercial)."""

LONG_TEXT_MAX = 2000
"""Longueur maximale d'un texte libre (désignation, informations supplémentaires)."""

ORDER_FIELD_KINDS: dict[str, str] = {
    "numero_commande": "identifier",
    "ligne_commande": "integer",
    "site_vente": "identifier",
    "code_client": "identifier",
    "entreprise_cliente": "name",
    "code_article": "identifier",
    "type_produit": "name",
    "nature_produit": "text",
    "quantite": "number",
    "unite": "short",
    "date_commande": "date",
    "date_livraison": "date",
    "commercial": "name",
    "type_sac": "short",
    "format_sac": "short",
    "type_papier": "short",
    "grammage": "measure",
    "laize": "measure",
    "impression_client": "text",
    "prix_unitaire": "number",
    "prix_total": "number",
    "devise": "short",
    "informations_supplementaires": "text",
    "confiance": "number",
    "est_bon_commande": "boolean",
}
"""Champs d'une commande extraite et nature de chacun.

Natures : ``identifier`` (référence courte), ``short`` (texte court), ``name`` (nom), ``text``
(texte libre), ``integer`` (entier), ``number`` (nombre décimal), ``measure`` (grammage ou laize,
entier quand la valeur est ronde), ``date`` (date écrite librement) et ``boolean``.
"""

_TEXT_LIMITS = {"identifier": SHORT_TEXT_MAX, "short": SHORT_TEXT_MAX, "name": NAME_MAX,
                "text": LONG_TEXT_MAX, "date": SHORT_TEXT_MAX}


# ---------------------------------------------------------------- préparation du texte

_CHAR_TRANSLATION = str.maketrans({
    **{chr(0x0660 + digit): str(digit) for digit in range(10)},  # chiffres arabo-indiens
    **{chr(0x06F0 + digit): str(digit) for digit in range(10)},  # variante persane et ourdoue
    "\u066b": ".",  # séparateur décimal arabe
    "\u066c": ",",  # séparateur des milliers arabe
    "\u0640": "",  # tatweel : trait d'allongement d'une lettre arabe, sans valeur
    "\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u00b4": "'", "`": "'",  # apostrophes
    "\u2212": "-", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",  # tirets
})
# Variantes de lettres arabes unifiées pour la comparaison seulement.
_FOLD_TRANSLATION = str.maketrans({
    "\u0649": "\u064a",  # alif maqsura écrit comme un ya
    "\u06cc": "\u064a",  # ya persan
    "\u06a9": "\u0643",  # kaf persan
})

_WORD_RE = re.compile(r"[^\W_]+")
_ARABIC_LETTER_RE = re.compile("[\u0600-\u06ff]")


def prepare_text(text: str) -> str:
    """Forme NFKC, chiffres latins, apostrophes et tirets unifiés ; casse et accents conservés."""
    return unicodedata.normalize("NFKC", text).translate(_CHAR_TRANSLATION)


def fold(text: str) -> str:
    """Forme de comparaison : texte préparé, sans signes diacritiques, en minuscules.

    >>> fold("Poignées TORSADÉES")
    'poignees torsadees'
    """
    decomposed = unicodedata.normalize("NFKD", prepare_text(text).translate(_FOLD_TRANSLATION))
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn").casefold()


def _folded_set(words: Iterable[str]) -> frozenset[str]:
    return frozenset(fold(word) for word in words)


def _has_arabic(text: str) -> bool:
    return bool(_ARABIC_LETTER_RE.search(text))


# ---------------------------------------------------------------- racinisation légère

# Préfixes arabes et longueur minimale du reste du mot pour qu'ils soient retirés.
_ARABIC_PREFIXES: tuple[tuple[str, int], ...] = (
    ("وال", 2), ("بال", 2), ("فال", 2), ("كال", 2), ("لل", 2), ("ال", 2),
    ("ب", 2), ("ل", 3), ("و", 4), ("ف", 4),
)
_ARABIC_SUFFIXES: tuple[str, ...] = ("ات", "ين", "ة")


def _arabic_stem(word: str) -> str:
    for prefix, minimum in _ARABIC_PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= minimum:
            word = word[len(prefix):]
            break
    for suffix in _ARABIC_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


def _latin_stem(word: str) -> str:
    if len(word) > 3 and word[-1] in "sx":
        word = word[:-1]
    while len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


# Pluriels internes (« brisés ») de l'arabe : la racinisation légère ne les ramène pas au singulier.
_ARABIC_BROKEN_PLURALS = {fold(plural): fold(singular) for plural, singular in {
    "أيدي": "يد", "أيادي": "يد", "ايد": "يد", "مقابض": "مقبض", "حبال": "حبل", "أكياس": "كيس", "أكياش": "كيس",
    "شكاير": "شكارة", "كراتين": "كرتون", "كراطن": "كرتون",
}.items()}


def _stem(word: str) -> str:
    """Racine légère d'un mot déjà en forme de comparaison (voir le docstring du module)."""
    if _has_arabic(word):
        stem = _arabic_stem(word)
        return _ARABIC_BROKEN_PLURALS.get(stem, stem)
    return _latin_stem(word)


def _stems(text: str) -> list[str]:
    return [_stem(word) for word in _WORD_RE.findall(fold(text))]


# ---------------------------------------------------------------- conversions élémentaires

_TRUE_WORDS = frozenset({"true", "vrai", "oui", "yes", "y", "1"})
_FALSE_WORDS = frozenset({"false", "faux", "non", "no", "n", "0"})

# Valeurs textuelles qui signifient « aucune valeur » dans une réponse du modèle.
_NULL_TEXTS = _folded_set((
    "", "null", "none", "nil", "n/a", "na", "nan", "-", "--", "?", "inconnu", "inconnue", "non précisé",
    "non précisée", "non spécifié", "non spécifiée", "non communiqué", "non renseigné", "unknown",
    "not specified", "aucun", "aucune", "vide",
))

_CONTROL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def to_bool(value: Any) -> bool | None:
    """Interprète un booléen : ``True``, ``1``, « true », « oui », « yes »… ; ``None`` si illisible."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        folded = fold(value).strip()
        if folded in _TRUE_WORDS:
            return True
        if folded in _FALSE_WORDS:
            return False
    return None


def _plain_number_text(value: int | float | Decimal) -> str | None:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Decimal) and not value.is_finite():
        return None
    # Au-delà de 10^15, l'écriture entière n'a plus de sens (et serait démesurée) : notation d'origine.
    if isinstance(value, (float, Decimal)) and abs(value) < 1e15 and value == int(value):
        return str(int(value))
    return str(value)


def clean_text(value: Any, *, max_length: int = LONG_TEXT_MAX, multiline: bool = False) -> str | None:
    """Texte nettoyé d'une valeur quelconque, ou ``None``.

    Les caractères de contrôle et les caractères invisibles sont retirés, les espaces
    regroupés (les sauts de ligne sont gardés si ``multiline``), le texte est tronqué à
    ``max_length`` caractères. Un nombre devient son écriture décimale, une liste la suite de
    ses éléments séparés par « ; ». Un booléen, un dictionnaire, un texte vide ou une valeur
    comme « null », « N/A » ou « inconnu » donnent ``None``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        parts = (clean_text(item, max_length=max_length) for item in value if not isinstance(item, (list, tuple)))
        value = "; ".join(part for part in parts if part)
    elif isinstance(value, (int, float, Decimal)):
        value = _plain_number_text(value)
    if not isinstance(value, str):
        return None
    # Une valeur démesurée est d'abord raccourcie : le nettoyage reste rapide.
    text = _CONTROL_RE.sub("", value[: max_length * 4])
    if multiline:
        lines = (" ".join(line.split()) for line in text.splitlines())
        text = "\n".join(line for line in lines if line)
    else:
        text = " ".join(text.split())
    if fold(text) in _NULL_TEXTS:
        return None
    return text[:max_length].rstrip() or None


def _integral(value: float) -> int | float:
    """Entier quand la valeur est ronde, sinon la valeur décimale inchangée."""
    return int(value) if float(value).is_integer() else value


# ---------------------------------------------------------------- découpage en jetons

@dataclass(frozen=True)
class _Token:
    """Jeton d'un texte en forme de comparaison : nombre, mot, ponctuation ou saut de ligne."""

    text: str
    kind: str
    start: int
    end: int
    value: float | None = None
    suffix: str = ""
    leading_zero: bool = False


# Nombre écrit en chiffres : groupes de trois chiffres séparés par une espace, un point, une
# virgule ou une apostrophe (le même séparateur dans tout le nombre), partie décimale facultative
# et lettres collées (« 10k », « 80g », « 1500pcs »).
_TOKEN_RE = re.compile(
    r"(?P<int>[1-9]\d{0,2}(?:(?P<sep>[ .,'])\d{3}(?!\d))(?:(?P=sep)\d{3}(?!\d))*|\d+)"
    r"(?P<dec>[.,]\d+(?!\d))?"
    r"(?P<suffix>[^\W\d_]*)"
    r"|(?P<word>[^\W_]+)"
    r"|(?P<nl>\n)"
    r"|(?P<punct>[^\w\s])"
)

_MASK_CHAR = "¤"
# Ponctuation qui termine la fenêtre de contexte d'un nombre.
_BOUNDARY_PUNCT = frozenset(".,;!?|()[]{}\"«»؟،؛" + _MASK_CHAR)

_MULTIPLIER_SUFFIXES = frozenset({"k"})
# Unités de mesure, devises et marques d'ordinal : un nombre ainsi suivi n'est pas une quantité.
_MEASURE_SUFFIXES = frozenset({
    "g", "gr", "grs", "gsm", "cm", "mm", "m", "dh", "dhs", "mad", "eur", "usd", "h", "j", "min",
    "er", "ere", "eme", "e", "st", "nd", "rd", "th", "x",
})
_COUNT_SUFFIXES = frozenset({"pcs", "pc", "pce", "pces", "u", "kg", "kgs"})


def _number_value(int_part: str, separator: str | None, decimal: str | None) -> float:
    digits = int_part.replace(separator, "") if separator else int_part
    if decimal:
        digits += "." + decimal[1:]
    return float(digits)


def _tokenize(folded: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN_RE.finditer(folded):
        start, end = match.span()
        if match.group("int") is not None:
            suffix = match.group("suffix") or ""
            if suffix and not _allowed_suffix(suffix):
                tokens.append(_Token(match.group(), "word", start, end))
                continue
            int_part = match.group("int")
            value = _number_value(int_part, match.group("sep"), match.group("dec"))
            tokens.append(_Token(match.group(), "num", start, end, value, suffix,
                                 leading_zero=len(int_part) > 1 and int_part.startswith("0")))
        elif match.group("word") is not None:
            tokens.append(_Token(match.group(), "word", start, end))
        elif match.group("nl") is not None:
            tokens.append(_Token("\n", "nl", start, end))
        else:
            tokens.append(_Token(match.group(), "punct", start, end))
    return tokens


def _allowed_suffix(suffix: str) -> bool:
    return (suffix in _MULTIPLIER_SUFFIXES or suffix in _MEASURE_SUFFIXES or suffix in _COUNT_SUFFIXES
            or _stem(suffix) in _COUNT_STEMS)


def _glued(left: _Token, right: _Token) -> bool:
    return left.end == right.start


# ---------------------------------------------------------------- masquage

# La condition « non précédé d'un caractère d'adresse » évite de réessayer le motif à chaque
# position d'un long mot (temps de calcul linéaire et non quadratique).
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")
_PHONE_RE = re.compile(
    r"(?:\+|(?<!\d)00)212[ .\-]?(?:\(0\)[ .\-]?)?0?[5-8](?:[ .\-]?\d){8}(?!\d)"
    r"|\+(?!212)[1-9]\d{0,2}(?:[ .\-]?\d){6,12}(?!\d)"
    r"|(?<!\d)0[5-8](?:[ .\-]?\d{2}){4}(?!\d)"
)
_DIMENSION_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?\s*[x×*]\s*\d+(?:[.,]\d+)?(?:\s*[x×*]\s*\d+(?:[.,]\d+)?)*")
_TIME_RE = re.compile(r"(?<![\w.,:])\d{1,2} ?[h:] ?\d{2}(?![\d:])")


def _mask(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda match: _MASK_CHAR * len(match.group()), text)


def _mask_spans(text: str, spans: Iterable[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = _MASK_CHAR * (end - start)
    return "".join(chars)


# Lettre arabe collée à un chiffre (« و500 », « 3000كيس ») : une espace les sépare avant le découpage.
_ARABIC_DIGIT_BOUNDARY_RE = re.compile("(?<=[\u0600-\u06ff])(?=[0-9])|(?<=[0-9])(?=[\u0600-\u06ff])")


def _masked_tokens(text: str) -> list[_Token]:
    """Jetons d'un texte où les emails, adresses web, téléphones, dates, dimensions et heures sont masqués."""
    folded = fold(text)
    for pattern in (_EMAIL_RE, _URL_RE, _PHONE_RE):
        folded = _mask(folded, pattern)
    folded = _mask_spans(folded, (match.span() for _, match in _date_matches(folded)))
    for pattern in (_DIMENSION_RE, _TIME_RE):
        folded = _mask(folded, pattern)
    return _tokenize(_ARABIC_DIGIT_BOUNDARY_RE.sub(" ", folded))


# ---------------------------------------------------------------- nombres en lettres

def _folded_mapping(entries: Mapping[str, float]) -> dict[str, float]:
    return {fold(word): value for word, value in entries.items()}


_SMALL_NUMBERS = _folded_mapping({
    # français
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
    # anglais
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    # darija en lettres latines (avec les formes courtes employées devant « alaf »)
    "wahed": 1, "wa7ed": 1, "wahd": 1, "wa7d": 1,
    "jouj": 2, "jooj": 2, "jouje": 2, "zouj": 2, "zoj": 2,
    "tlata": 3, "tlat": 3, "tlate": 3, "tleta": 3, "tlt": 3,
    "rb3a": 4, "reb3a": 4, "rab3a": 4, "arb3a": 4, "rb3": 4, "reb3": 4, "rb3t": 4, "reb3at": 4,
    "khamsa": 5, "khmsa": 5, "khams": 5, "khamst": 5, "5amsa": 5,
    "stta": 6, "setta": 6, "sitta": 6, "stt": 6, "sett": 6,
    "sb3a": 7, "seb3a": 7, "sab3a": 7, "sb3": 7, "seb3": 7, "sb3t": 7,
    "tmenya": 8, "tmanya": 8, "tmnya": 8, "tmania": 8, "tmn": 8, "tmen": 8, "tmnt": 8,
    "tes3oud": 9, "tes3od": 9, "ts3oud": 9, "tse3d": 9, "tes3a": 9, "ts3a": 9, "tes3": 9, "ts3": 9,
    "3achra": 10, "3ashra": 10, "3chra": 10, "3chr": 10, "3achr": 10, "3achrat": 10,
    "3echrin": 20, "3chrin": 20, "3ichrin": 20, "tlatin": 30, "rb3in": 40, "rab3in": 40,
    "khamsin": 50, "sttin": 60, "settin": 60, "sb3in": 70, "seb3in": 70, "tmanin": 80, "tmenin": 80,
    "ts3in": 90, "tes3in": 90,
    # darija : nombres de 11 à 19, formés sur le suffixe « -tach » / « -طاش »
    "hdach": 11, "7dach": 11, "hdache": 11, "7dache": 11, "tna3ch": 12, "tnach": 12, "tna3che": 12,
    "tlattach": 13, "tlettach": 13, "tletach": 13, "tlatach": 13, "rb3tach": 14, "rba3tach": 14, "arba3tach": 14,
    "khmstach": 15, "khamstach": 15, "khemstach": 15, "5mstach": 15, "sttach": 16, "settach": 16, "sotach": 16,
    "sb3tach": 17, "sba3tach": 17, "tmntach": 18, "tmentach": 18, "tmantach": 18, "ts3tach": 19, "tsa3tach": 19,
    "tes3tach": 19,
    # arabe standard et darija en écriture arabe
    "واحد": 1, "أحد": 1, "إحدى": 1, "اثنان": 2, "اثنين": 2, "اثنا": 2, "اثني": 2, "جوج": 2, "زوج": 2,
    "ثلاثة": 3, "ثلاث": 3, "تلاتة": 3, "تلات": 3, "تلت": 3, "تلاتا": 3,
    "أربعة": 4, "أربع": 4, "ربعة": 4, "خمسة": 5, "خمس": 5, "ستة": 6, "ست": 6, "سبعة": 7, "سبع": 7,
    "ثمانية": 8, "ثمان": 8, "تمنية": 8, "تمنيا": 8, "تسعة": 9, "تسع": 9, "تسعود": 9, "عشرة": 10, "عشر": 10,
    "حداش": 11, "طناش": 12, "تلطاش": 13, "ربعطاش": 14, "خمسطاش": 15, "سطاش": 16, "سبعطاش": 17,
    "تمنطاش": 18, "تسعطاش": 19,
    "عشرين": 20, "عشرون": 20, "ثلاثين": 30, "ثلاثون": 30, "تلاتين": 30, "أربعين": 40, "أربعون": 40,
    "ربعين": 40, "خمسين": 50, "خمسون": 50, "ستين": 60, "ستون": 60, "سبعين": 70, "سبعون": 70,
    "ثمانين": 80, "ثمانون": 80, "تمانين": 80, "تسعين": 90, "تسعون": 90,
})
_HUNDRED_WORDS = _folded_set(("cent", "cents", "hundred", "hundreds", "mya", "miya", "mia", "myat",
                              "مية", "مائة", "مئة", "ميا"))
_HUNDREDS = _folded_mapping({
    "myatayn": 200, "mitin": 200, "miteen": 200, "myatin": 200, "mitayn": 200, "mitine": 200, "miyatayn": 200,
    "ميتين": 200, "مئتين": 200, "مائتين": 200, "مائتان": 200, "مئتان": 200, "مئتا": 200, "مائتا": 200,
    "مئتي": 200, "مائتي": 200,
})
# Centaines composées en un seul mot : chiffre de 3 à 9 suivi du mot « cent » (« khamsmya » = 500,
# « خمسمائة » = 500, « تلتمية » = 300). Racines des chiffres employées devant le suffixe.
_HUNDRED_SUFFIXES = tuple(sorted(_folded_set(("mya", "miya", "mia", "مائة", "مية", "مئة", "ميا")), key=len,
                                 reverse=True))
_HUNDRED_PREFIXES = _folded_mapping({
    "tlat": 3, "tlata": 3, "tlet": 3, "tleta": 3, "rb3": 4, "rob3": 4, "reb3": 4, "rba3": 4, "rb3a": 4, "rab3a": 4,
    "khams": 5, "khamsa": 5, "khms": 5, "khems": 5, "5ams": 5, "stt": 6, "sett": 6, "sit": 6,
    "sb3": 7, "seb3": 7, "sba3": 7, "sb3a": 7, "tmn": 8, "tmen": 8, "tman": 8, "tmnt": 8, "ts3": 9, "tes3": 9,
    "tsa3": 9, "ts3a": 9,
    "ثلاث": 3, "ثلاثة": 3, "تلت": 3, "تلات": 3, "أربع": 4, "ربع": 4, "خمس": 5, "ست": 6, "سبع": 7, "ثمان": 8,
    "ثماني": 8, "تمن": 8, "تسع": 9,
})
_THOUSAND_WORDS = _folded_set(("mille", "milles", "thousand", "thousands", "alf", "alef", "alaf", "alaaf",
                               "dalaf", "dalaaf", "dlalaf", "ألف", "آلاف", "ألاف", "ألفا", "دالاف", "دلاف",
                               "الآلاف", "الألاف"))
# Pluriels qui ne valent un nombre qu'après un autre nombre (« tlat alaf » = 3000, mais « alaf »
# seul signifie « des milliers ») ; seuls, ils sont trop vagues pour donner une quantité. En
# darija, « d » (de) relie souvent le chiffre au pluriel : « tlata d alaf », « جوج دالاف ».
_PLURAL_SCALE_WORDS = _folded_set(("alaf", "alaaf", "dalaf", "dalaaf", "dlalaf", "آلاف", "ألاف", "دالاف", "دلاف",
                                   "الآلاف", "الألاف", "thousands", "millions", "ملايين", "hundreds", "cents"))
# Mots d'une quantité approximative ou fractionnaire (« un et demi », « quelques milliers »).
_VAGUE_QUANTITY_WORDS = _folded_set(("demi", "demie", "moitié", "quart", "quelques", "plusieurs", "dizaines",
                                     "centaines", "milliers", "half", "quarter", "several", "few", "dozens",
                                     "نص", "نصف"))
# « Et demi » après un millier, un million ou une centaine ajoute la moitié de cette unité
# (« alf w nss » = 1500, « jouj alaf w nss » = 2500, « deux millions et demi » = 2 500 000).
_HALF_WORDS = _folded_set(("demi", "demie", "half", "nss", "nos", "nouss", "nus", "نص", "نصف"))
# Duels : un seul mot pour « deux mille » ou « deux millions » ; valeur et ordre de grandeur.
_DUAL_WORDS: dict[str, tuple[float, float]] = {fold(word): value for word, value in {
    "alfayn": (2000, 1000), "alfin": (2000, 1000), "alfine": (2000, 1000), "alfeen": (2000, 1000),
    "alfain": (2000, 1000), "ألفين": (2000, 1000), "ألفان": (2000, 1000), "ألفي": (2000, 1000),
    "mlyounayn": (2_000_000, 1_000_000), "مليونين": (2_000_000, 1_000_000), "مليونان": (2_000_000, 1_000_000),
}.items()}
_MILLION_WORDS = _folded_set(("million", "millions", "mlyoun", "melyoun", "mliyon", "malyoun", "مليون", "ملايين",
                              "مليونا"))
# Noms collectifs d'une quantité approximative, au singulier après un article (« une centaine de
# sachets » = 100, « un millier » = 1000) : une quantité approximative donne le nombre cité.
_COLLECTIVE_NUMBERS = _folded_mapping({
    "dizaine": 10, "douzaine": 12, "quinzaine": 15, "vingtaine": 20, "trentaine": 30, "quarantaine": 40,
    "cinquantaine": 50, "soixantaine": 60, "centaine": 100, "millier": 1000, "dozen": 12,
})
# Mots « un » placés devant un nombre : article, et non la quantité 1 (« wa7ed 2000 sachiyat »).
_ARTICLES = _folded_set(("wahed", "wa7ed", "wahd", "wa7d", "واحد", "un", "une", "one"))
_CONNECTORS = frozenset({"et", "w", "o", "and", "و", "-"})
# Particule « d » (de) entre un chiffre et le mot « alaf » en darija : « tlata d alaf », « تلاتة د الاف ».
_DARIJA_OF = frozenset({"d", "د"})
# Prépositions et conjonctions collées devant un nombre en écriture arabe : « و » (et), « ب » (avec,
# par : « تزويدنا بألف »), et leur combinaison « وب ».
_ARABIC_NUMBER_CLITICS = ("وب", "و", "ب")
_ARABIC_TEN_WORDS = _folded_set(("عشر", "عشرة"))


def _compound_hundred(text: str) -> float | None:
    """Centaine écrite en un seul mot (« khamsmya », « خمسمائة »), ou ``None``."""
    for suffix in _HUNDRED_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            digit = _HUNDRED_PREFIXES.get(text[: -len(suffix)].rstrip())
            if digit is not None:
                return digit * 100
    return None


def _number_word(text: str) -> tuple[str, float] | None:
    """Nature (unité, centaine, millier…) et valeur d'un nombre écrit en lettres (forme de comparaison), ou ``None``."""
    if text in _SMALL_NUMBERS:
        return "small", _SMALL_NUMBERS[text]
    if text in _HUNDRED_WORDS:
        return "hundred", 100
    if text in _HUNDREDS:
        return "hundreds", _HUNDREDS[text]
    if text in _THOUSAND_WORDS:
        return "thousand", 1000
    if text in _DUAL_WORDS:
        return ("thousands" if _DUAL_WORDS[text][1] == 1000 else "millions"), _DUAL_WORDS[text][0]
    if text in _MILLION_WORDS:
        return "million", 1_000_000
    if text in _COLLECTIVE_NUMBERS:
        return "collective", _COLLECTIVE_NUMBERS[text]
    if text in _HALF_WORDS:
        return "half", 0.5
    compound = _compound_hundred(text)
    if compound is not None:
        return "hundreds", compound
    return None


def _word_number(token: _Token) -> tuple[str, float, bool] | None:
    """Nombre écrit en lettres : (nature, valeur, conjonction « و » collée) ou ``None``.

    Un mot de l'écriture arabe peut porter une conjonction ou une préposition collée
    (« وخمسمائة », « بألف ») : elle est retirée avant la lecture.
    """
    if token.kind != "word":
        return None
    entry = _number_word(token.text)
    if entry is not None:
        return entry[0], entry[1], False
    for clitic in _ARABIC_NUMBER_CLITICS:
        if token.text.startswith(clitic) and len(token.text) > len(clitic) + 1:
            entry = _number_word(token.text[len(clitic):])
            if entry is not None:
                return entry[0], entry[1], clitic.startswith("و")
    return None


def _starts_number(token: _Token) -> bool:
    return token.kind == "num" or _word_number(token) is not None


@dataclass
class _NumberState:
    """Accumulateur de la lecture d'un nombre : ``total`` (milliers et millions) + ``current``."""

    total: float = 0.0
    current: float = 0.0
    count: int = 0
    digits: bool = False
    after_multiplier: bool = False
    connector: bool = False
    last_small: float | None = None
    last_word: str = ""
    scale: float = math.inf
    last_unit: float | None = None
    closed: bool = False

    @property
    def value(self) -> float:
        return self.total + self.current

    def add(self, kind: str, value: float, glued_connector: bool, word: str) -> bool:
        """Ajoute un mot-nombre ; renvoie ``False`` quand le mot commence un autre nombre."""
        connector = self.connector or glued_connector
        if self.closed or (self.count == 0 and word in _PLURAL_SCALE_WORDS):
            return False
        if kind == "small":
            if not self._add_small(value, connector, word):
                return False
        elif kind == "hundred":
            if self.current >= 100:  # « 2000 cent » : deux nombres distincts
                return False
            self.current = (self.current or 1) * 100
            self.after_multiplier, self.last_small, self.last_unit = True, None, 100
        elif kind == "hundreds":
            if self.current:
                return False
            self.current, self.after_multiplier, self.last_small, self.last_unit = value, True, None, 100
        elif kind == "collective":
            if self.count:
                return False
            self.current, self.closed = value, True
        elif kind == "half":
            if not self._add_half(connector):
                return False
        elif not self._add_scale(kind, value):
            return False
        self.count += 1
        self.connector = False
        self.last_word = word
        return True

    def _add_small(self, value: float, connector: bool, word: str) -> bool:
        if self.digits and not self.after_multiplier:
            return False
        if value == 20 and self.last_word == "quatre":
            self.current += 76  # « quatre-vingt » = 80
            self.last_small = 80
            return True
        if (word in _ARABIC_TEN_WORDS and not connector and self.last_small is not None
                and 1 <= self.last_small <= 9 and not self.after_multiplier):
            # Arabe : le chiffre précède la dizaine (« ثلاثة عشر » = 13, « أحد عشر ألف » = 11 000).
            self.current += value
            self.last_small = self.last_small + value
            return True
        follows_ten = (self.last_small is not None and self.last_small >= 10 and self.last_small % 10 == 0
                       and value < self.last_small)
        if self.count and not (connector or self.after_multiplier or follows_ten):
            return False
        self.current += value
        self.last_small = value
        self.after_multiplier = False
        return True

    def _add_half(self, connector: bool) -> bool:
        """« et demi » juste après un millier, un million ou une centaine : moitié de cette unité."""
        if not (connector and self.after_multiplier and self.last_unit):
            return False
        if self.last_unit >= 1000:
            self.total += self.last_unit / 2
        else:
            self.current += self.last_unit / 2
        self.closed = True
        return True

    def _add_scale(self, kind: str, value: float) -> bool:
        scale = 1_000_000 if kind in ("million", "millions") else 1000
        if scale >= self.scale or self.current >= 1000:  # « 2000 mille » : deux nombres distincts
            return False
        if kind in ("thousands", "millions"):
            if self.current:
                return False
            self.total += value
        else:
            self.total += (self.current or 1) * scale
        self.current, self.scale, self.after_multiplier, self.last_small = 0.0, scale, True, None
        self.last_unit = scale
        return True


@dataclass(frozen=True)
class _NumberRun:
    """Nombre lu dans une suite de jetons ``tokens[start:end]``."""

    value: float
    start: int
    end: int
    digits: bool
    lone_one: bool


def _is_connector(tokens: Sequence[_Token], position: int) -> bool:
    token = tokens[position]
    if token.text not in _CONNECTORS or position + 1 >= len(tokens):
        return False
    following = tokens[position + 1]
    if _word_number(following) is None:
        return False
    if token.text == "-":
        return _glued(tokens[position - 1], token) and _glued(token, following)
    return True


def _of_particle_end(tokens: Sequence[_Token], position: int) -> int | None:
    """Fin de la particule darija « d » (ou « d' ») suivie de « alaf » / « alf » : « tlata d alaf »."""
    if tokens[position].text not in _DARIJA_OF:
        return None
    following = position + 1
    if following < len(tokens) and tokens[following].text == "'" and _glued(tokens[position], tokens[following]):
        following += 1
    if following < len(tokens) and tokens[following].text in _THOUSAND_WORDS:
        return following
    return None


def _repeats_start(tokens: Sequence[_Token], start: int, position: int) -> bool:
    """Vrai quand les mots qui suivent répètent le nombre déjà lu (« trois mille trois mille sachets »).

    Une note vocale dictée reprend souvent la quantité : la répétition commence un autre nombre
    au lieu d'ajouter des unités (« trois mille trois » ne vaut pas 3003 ici).
    """
    size = position - start
    if size < 2 or position + size > len(tokens):
        return False
    return all(tokens[start + offset].text == tokens[position + offset].text for offset in range(size))


def _digits_after_scale(tokens: Sequence[_Token], position: int, state: _NumberState) -> int | None:
    """Fin d'un nombre en chiffres qui complète un millier ou un million écrit en lettres, ou ``None``.

    Écriture mixte courante : « 2 mille 500 » (2500), « alf w 200 » (1200), « 3 alaf w 500 »
    (3500), « ألف و 500 » (1500). Le nombre en chiffres doit être inférieur à l'unité qu'il
    complète et au moins égal à 10, pour ne pas lier deux nombres distincts (« mille 2 cartons »).
    """
    if not (state.after_multiplier and state.last_unit and state.last_unit >= 1000) or state.closed:
        return None
    index = position
    if tokens[index].text in _CONNECTORS and tokens[index].text != "-":
        index += 1
    if index >= len(tokens):
        return None
    token = tokens[index]
    if token.kind != "num" or token.suffix or token.leading_zero or token.value is None:
        return None
    if not (10 <= token.value < state.last_unit and token.value.is_integer()):
        return None
    return index + 1


def _read_number(tokens: Sequence[_Token], index: int) -> _NumberRun | None:
    """Lit le nombre (chiffres ou lettres) qui commence au jeton ``index``, ou renvoie ``None``."""
    start = index
    if tokens[index].text in _ARTICLES and index + 1 < len(tokens) and _starts_number(tokens[index + 1]):
        start = index + 1
    state = _NumberState()
    position = start
    first = tokens[start]
    if first.kind == "num":
        state.current, state.digits, state.count = first.value or 0.0, True, 1
        position += 1
        if first.suffix == "k":
            return _NumberRun(state.current * 1000, start, position, digits=True, lone_one=False)
    while position < len(tokens):
        token = tokens[position]
        if state.after_multiplier and _repeats_start(tokens, start, position):
            break
        if state.count and _is_connector(tokens, position):
            state.connector = True
            position += 1
            continue
        particle_end = _of_particle_end(tokens, position) if state.count and not state.total else None
        if particle_end is not None:
            position = particle_end
            continue
        digits_end = _digits_after_scale(tokens, position, state)
        if digits_end is not None:
            state.current += tokens[digits_end - 1].value or 0.0
            state.count += 1
            state.closed, state.after_multiplier = True, False
            position = digits_end
            break
        if state.digits and state.count == 1 and token.text == "k":
            entry: tuple[str, float, bool] | None = ("thousand", 1000, False)
        else:
            entry = _word_number(token)
        if entry is None or not state.add(entry[0], entry[1], entry[2], token.text):
            break
        position += 1
    if state.count == 0:
        return None
    lone_one = not state.digits and state.count == 1 and state.value == 1
    return _NumberRun(state.value, start, position, state.digits, lone_one)


def _number_runs(tokens: Sequence[_Token]) -> Iterator[_NumberRun]:
    index = 0
    while index < len(tokens):
        run = _read_number(tokens, index) if _starts_number(tokens[index]) else None
        if run is None:
            index += 1
            continue
        yield run
        index = run.end


def _is_negative(tokens: Sequence[_Token], run: _NumberRun) -> bool:
    """Vrai quand un signe moins précède le nombre (et non un tiret de référence « PO-12 »)."""
    if run.start == 0:
        return False
    sign = tokens[run.start - 1]
    if sign.text != "-" or not _glued(sign, tokens[run.start]):
        return False
    if run.start == 1:
        return True
    before = tokens[run.start - 2]
    return not _glued(before, sign) or before.kind == "punct"


_SCIENTIFIC_RE = re.compile(r"[+-]?\d+(?:\.\d+)?[eE][+-]?\d+")
_ORDINAL_SUFFIXES = frozenset({"er", "ere", "eme", "e", "st", "nd", "rd", "th"})


def _is_hyphenated(tokens: Sequence[_Token], run: _NumberRun) -> bool:
    """Nombre relié par un tiret à un mot : référence (« PO-5521 ») ou mot composé (« mille-feuilles »)."""
    first, last = tokens[run.start], tokens[run.end - 1]
    if run.start >= 2:
        dash, before = tokens[run.start - 1], tokens[run.start - 2]
        if dash.text == "-" and _glued(dash, first) and _glued(before, dash) and before.kind == "word":
            return True
    if run.end + 1 < len(tokens):
        dash, after = tokens[run.end], tokens[run.end + 1]
        if dash.text == "-" and _glued(last, dash) and _glued(dash, after) and after.kind == "word":
            return True
    return False


def parse_quantity(value: Any) -> float | None:
    """Lit une quantité (ou tout nombre d'un champ) écrite en chiffres ou en lettres.

    Formats acceptés : « 10 000 », « 10.000 », « 10,000 », « 10k », « 2 mille », « 1,5 million »,
    « 3000 pcs », « Quantité : 10 000 unités », chiffres arabo-indiens (« ٣٠٠٠ »), darija
    (« jouj alaf » = 2000, « tlat alaf » = 3000), arabe (« ألفين », « خمسة آلاف », « مية »),
    nombres en lettres français et anglais. Un signe moins initial donne une valeur négative
    (la quantité est alors signalée par :func:`normalize_order`).

    Returns:
        La valeur, ou ``None`` quand aucun nombre n'est lisible, quand la valeur contient
        plusieurs nombres différents (« 2000 à 3000 », date, dimensions) car le choix serait
        arbitraire, quand la quantité est approximative (« quelques milliers », « un et demi »,
        « alaf » seul), ou quand le nombre fait partie d'une référence (« PO-5521 »), d'un mot
        composé (« mille-feuilles ») ou d'un ordinal (« 1er »).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = float(value)
        except (OverflowError, ValueError, InvalidOperation):
            return None
        return number if math.isfinite(number) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_VALUE_CHARS:
        return None
    if _SCIENTIFIC_RE.fullmatch(text):
        number = float(text)
        return number if math.isfinite(number) else None
    tokens = _masked_tokens(text)
    runs = list(_number_runs(tokens))
    inside = {position for run in runs for position in range(run.start, run.end)}
    if any(token.text in _VAGUE_QUANTITY_WORDS and index not in inside for index, token in enumerate(tokens)):
        return None
    if any(_is_hyphenated(tokens, run) or tokens[run.end - 1].suffix in _ORDINAL_SUFFIXES for run in runs):
        return None
    if any(not run.lone_one for run in runs):
        runs = [run for run in runs if not run.lone_one]
    values = {-run.value if _is_negative(tokens, run) else run.value for run in runs}
    return values.pop() if len(values) == 1 else None


# ---------------------------------------------------------------- types de produit

# Ordre de priorité quand plusieurs produits sont cités : le plus spécifique l'emporte.
_PRODUCT_PRECEDENCE = ("SFCPT", "SFCPP", "SFCSP", "SFP")
# Mots-clés du sac sans poignées qui ne disent rien des poignées : ils ne suffisent pas quand le
# texte parle de poignées (« sac fond carré avec poignées » sans préciser lesquelles).
_GENERIC_SQUARE_BOTTOM = ("fond carré", "square bottom", "square bottom bag", "قاع مربع", "قعر مربع")
# Mots-clés d'usage, de gamme ou de taille (« premium », « sac shopping », « petit sac ») : ils ne
# décrivent pas la construction du sac et cèdent devant un mot-clé de construction (« sachets
# premium » reste un sachet, « sac shopping à poignées torsadées » un sac à poignées torsadées).
_USAGE_KEYWORDS = ("premium", "luxe", "luxury", "haut de gamme", "sac shopping", "shopping bag", "petit sac",
                   "small bag", "small paper bag", "sac à pain", "sac pain", "bread bag", "كيس صغير", "أكياس صغيرة",
                   "كيس الخبز")
_HANDLE_STEMS = frozenset(_stem(fold(word)) for word in (
    "poignée", "poignées", "anse", "anses", "handle", "handles", "yddin", "yeddin", "yedd", "mqabed", "lyddin",
    "lyeddin", "lmqabed", "مقبض", "مقابض", "يد", "أيدي",
))
# Négation placée devant les poignées (« sacs qui n'ont pas de poignées », « li ma fihomch lyddin »,
# « ما فيهش اليدين », « bags with no handles ») : le sac sans poignées. Entre la négation et le mot
# « poignée », seuls des mots de liaison ou les verbes « avoir » / « fih » (il y a) sont admis.
_HANDLE_NEGATIONS = frozenset(_stem(fold(word)) for word in (
    "sans", "pas", "aucun", "aucune", "without", "no", "not", "bla", "bila", "ma", "machi", "mafihch",
    "mafihomch", "mafihach", "بلا", "بدون", "ما", "ماشي", "غير", "ليس", "مافيهش", "مافيهمش", "مافيهاش",
))
_HANDLE_PRIVATIVES = frozenset({_stem("free")})
_NEGATION_GAP_WORDS = frozenset(_stem(fold(word)) for word in (
    "de", "d", "des", "les", "la", "le", "of", "any", "the", "a", "have", "has", "had", "with", "avoir", "ont",
    "a", "y", "b", "fih", "fiha", "fihom", "fihoum", "fihch", "fihach", "fihomch", "fihoumch", "3ndo", "3ndha",
    "3endhom", "فيه", "فيها", "فيهم", "فيهش", "فيهاش", "فيهمش", "عندو", "عندها", "عندهم", "ب", "مع",
))


def _negated_handle(stems: Sequence[str]) -> bool:
    """Vrai quand une négation précède le mot « poignée » (au plus trois mots de liaison entre les deux)."""
    for index, stem in enumerate(stems):
        if stem not in _HANDLE_STEMS:
            continue
        position = index - 1
        while position >= 0 and index - position <= 4 and stems[position] in _NEGATION_GAP_WORDS:
            position -= 1
        if position >= 0 and index - position <= 4 and stems[position] in _HANDLE_NEGATIONS:
            return True
        if index + 1 < len(stems) and stems[index + 1] in _HANDLE_PRIVATIVES:  # « handle-free »
            return True
    return False
# Mots qui peuvent s'intercaler dans un mot-clé sans en changer le sens : articles, prépositions,
# hésitations d'une note vocale, matière ou couleur du papier (« sans les poignées », « poignées en
# papier torsadées », « كيس ورقي صغير »). Au plus deux mots intercalés.
_KEYWORD_FILLERS = frozenset(_stem(fold(word)) for word in (
    "le", "la", "les", "l", "un", "une", "des", "de", "du", "d", "en", "à", "the", "a", "an", "any", "of", "là",
    "euh", "heu", "dyal", "dial", "ديال", "اه", "ذي", "ذات", "kraft", "papier", "paper", "ورقي", "ورق", "كرافت",
    "brun", "marron", "blanc", "blanche", "white", "brown", "naturel",
))
_MAX_KEYWORD_GAP = 2
# Usage alimentaire d'un sac sans mot-clé de construction (« sacs pour les croissants », « أكياس
# للخبز ») : le catalogue réserve cet usage aux sachets fond plat (sandwichs, tacos, viennoiseries).
_BAG_STEMS = frozenset(_stem(fold(word)) for word in (
    "sac", "sacs", "bag", "bags", "sachet", "pochon", "emballage", "emballages", "كيس", "أكياس", "ساك", "صاك",
    "ساكات", "شكارة", "chkara",
))
_FOOD_USAGE_STEMS = frozenset(_stem(fold(word)) for word in (
    "sandwich", "sandwichs", "sandwiches", "tacos", "viennoiserie", "viennoiseries", "croissant", "croissants",
    "pain", "pains", "baguette", "baguettes", "khobz", "lkhobz", "msemen", "harcha", "beignet", "beignets",
    "frites", "fries", "gâteau", "gâteaux", "biscuit", "biscuits", "bonbon", "bonbons", "confiserie", "crêpe",
    "crêpes", "panini", "shawarma", "chawarma", "bread", "pastries", "pastry", "cookies", "سندويتش", "سندويش",
    "ساندويتش", "طاكوس", "خبز", "حلويات", "حلوى", "كرواسون", "مسمن", "حرشة", "فطائر", "بريوش", "شاورما",
    # darija en lettres latines, avec ou sans l'article collé « l »
    "7lwa", "l7lwa", "7elwa", "l7elwa", "7alwa", "l7alwa", "gato", "lgato", "frit", "lfrit", "croissa",
))


@dataclass(frozen=True)
class _Keyword:
    stems: tuple[str, ...]
    generic: bool
    usage: bool = False


def _build_keyword_index() -> dict[str, tuple[_Keyword, ...]]:
    generic = {tuple(_stems(keyword)) for keyword in _GENERIC_SQUARE_BOTTOM}
    usage = {tuple(_stems(keyword)) for keyword in _USAGE_KEYWORDS}
    index: dict[str, tuple[_Keyword, ...]] = {}
    for product in PRODUCTS:
        keywords = []
        for words in product["keywords"].values():
            for keyword in words:
                stems = tuple(_stems(keyword))
                if stems:
                    keywords.append(_Keyword(stems, stems in generic, stems in usage))
        index[product["code"]] = tuple(keywords)
    return index


_KEYWORDS = _build_keyword_index()
_KEYWORD_STEMS = frozenset(stem for keywords in _KEYWORDS.values() for keyword in keywords for stem in keyword.stems)
_PRODUCT_BY_CODE = {product["code"]: product["type"] for product in PRODUCTS}
_PRODUCT_BY_STEMS = {tuple(_stems(product["type"])): product["type"] for product in PRODUCTS}


def _stem_positions(stems: Sequence[str]) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    for index, stem in enumerate(stems):
        positions.setdefault(stem, []).append(index)
    return positions


def _contains(stems: Sequence[str], positions: Mapping[str, list[int]], keyword: Sequence[str]) -> bool:
    """Vrai quand les mots du mot-clé se suivent dans le texte, séparés au plus par deux mots de liaison."""
    for index in positions.get(keyword[0], ()):
        position = index
        for word in keyword[1:]:
            position += 1
            skipped = 0
            while (position < len(stems) and stems[position] != word and stems[position] in _KEYWORD_FILLERS
                   and skipped < _MAX_KEYWORD_GAP):
                position += 1
                skipped += 1
            if position >= len(stems) or stems[position] != word:
                break
        else:
            return True
    return False


# Aliments dont le nom désigne aussi le commerce qui les vend (« Pâtisserie Nour ») : ils ne comptent
# comme usage que écrits en minuscules (« des sacs pour mes pâtisseries »).
_LOWERCASE_FOOD_STEMS = frozenset(_stem(fold(word)) for word in ("pâtisserie", "pâtisseries", "patisserie"))


def _usage_product(stems: Sequence[str], lowercase_stems: Iterable[str] = ()) -> str | None:
    """Produit déduit de l'usage quand aucun mot-clé ne désigne la construction du sac."""
    present = set(stems)
    food = present & _FOOD_USAGE_STEMS or set(lowercase_stems) & _LOWERCASE_FOOD_STEMS
    if present & _BAG_STEMS and food and not present & _HANDLE_STEMS:
        return _PRODUCT_BY_CODE["SFP"]
    return None


def _lowercase_stems(text: str) -> list[str]:
    """Racines des mots écrits sans majuscule initiale (tous les mots d'une écriture sans casse)."""
    return [_stem(fold(word)) for word in _WORD_RE.findall(prepare_text(text)) if not word[0].isupper()]


_SUN_LETTERS = "tdrzsn"


def _without_darija_article(stem: str) -> str:
    """Mot darija précédé de l'article collé « l » (« lyddin » → « yddin », « lmsat7in » → « msat7in »).

    Devant une « lettre solaire » (t, d, r, z, s, n), le « l » de l'article s'assimile à la lettre
    suivante, qui se double : « ssandwich » = « l » + « sandwich », « ttacos » = « l » + « tacos ».
    """
    if stem in _KEYWORD_STEMS or len(stem) < 4 or _has_arabic(stem):
        return stem
    known = _KEYWORD_STEMS | _HANDLE_STEMS | _FOOD_USAGE_STEMS | _BAG_STEMS
    if stem.startswith("l") and stem[1:] in known:
        return stem[1:]
    if stem[0] == stem[1] and stem[0] in _SUN_LETTERS and stem[1:] in known:
        return stem[1:]
    return stem


def normalize_product_type(text: Any) -> str | None:
    """Nom exact du produit du catalogue désigné par ``text``, ou ``None``.

    Le texte peut être un nom de produit (« Sachets fond plat »), un code (« SFCPT ») ou un
    message entier. Les mots-clés du catalogue sont cherchés en forme de comparaison
    (:func:`fold`) et par racine légère, donc sans tenir compte des accents, de la casse, du
    singulier ou du pluriel (pluriels internes de l'arabe compris : « أيدي » pour « يد »). Deux
    mots de liaison au plus peuvent s'intercaler dans un mot-clé (« sans les poignées »). Un code
    de produit écrit en majuscules dans le message (« SFCPT ») désigne son produit. Quand
    plusieurs produits sont cités, le plus spécifique l'emporte : poignées torsadées, puis poignées
    plates, puis sans poignées, puis fond plat. Les mots-clés d'usage ou de gamme (« premium »,
    « sac shopping », « petit sac ») cèdent devant un mot-clé de construction. « Fond carré » seul
    ne désigne le sac sans poignées que si le texte ne parle pas de poignées. Une négation devant
    les poignées (« qui n'ont pas de poignées », « li ma fihomch lyddin ») désigne le sac sans
    poignées. Sans aucun mot-clé,
    un sac destiné à un usage alimentaire (« sacs pour les croissants ») désigne les sachets fond
    plat, sauf si le texte parle de poignées.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    text = text[:MAX_TEXT_CHARS]
    code = text.strip().upper()
    if code in _PRODUCT_BY_CODE:
        return _PRODUCT_BY_CODE[code]
    listed = {_PRODUCT_BY_CODE[word] for word in _WORD_RE.findall(text) if word.isupper() and word in _PRODUCT_BY_CODE}
    if len(listed) == 1:
        return listed.pop()
    stems = [_without_darija_article(stem) for stem in _stems(text)]
    exact = _PRODUCT_BY_STEMS.get(tuple(stems))
    if exact:
        return exact
    positions = _stem_positions(stems)
    usage_choice: str | None = None
    for product_code in _PRODUCT_PRECEDENCE:
        matched = [keyword for keyword in _KEYWORDS.get(product_code, ())
                   if _contains(stems, positions, keyword.stems)]
        if product_code == "SFCSP" and _negated_handle(stems):
            matched.append(_Keyword(("sans", "poigne"), generic=False))
        if not matched:
            continue
        if all(keyword.usage for keyword in matched):
            usage_choice = usage_choice or product_code
            continue
        if all(keyword.generic or keyword.usage for keyword in matched):
            if usage_choice:
                return _PRODUCT_BY_CODE[usage_choice]
            if _HANDLE_STEMS.intersection(stems):
                return None
        return _PRODUCT_BY_CODE[product_code]
    if usage_choice:
        return _PRODUCT_BY_CODE[usage_choice]
    return _usage_product(stems, _lowercase_stems(text))


# ---------------------------------------------------------------- unités et devises

# Darija en lettres latines : « 7 » note la lettre ح (« 7ba » = حبة, pièce), « k » ou « c » la lettre ك.
_UNIT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pièces": ("pièce", "pièces", "piece", "pieces", "pcs", "pc", "pce", "pces", "unité", "unités", "u",
               "unit", "units", "hba", "hbat", "7ba", "7bat", "7abba", "7abbat", "7aba", "7abat", "habba", "habbat",
               "حبة", "حبات", "قطعة", "قطع", "وحدة", "وحدات"),
    "kg": ("kg", "kgs", "kilo", "kilos", "kilogramme", "kilogrammes", "kilogram", "kilograms", "كيلو", "كلغ",
           "كغ", "كيلوغرام", "كيلوغرامات", "كيلوات"),
    "carton": ("carton", "cartons", "ctn", "ctns", "cartonat", "kartona", "kartonat", "kartouna", "cartona",
               "كرتون", "كارطون", "قرطون", "كراطن", "كرتونة", "كرطونة", "كارطونة", "كرتونات", "كرطونات",
               "كارطونات", "كراتين"),
    "paquet": ("paquet", "paquets", "pqt", "pack", "packs", "paket", "pakiya", "bakiya", "pakiyat", "bakiyat",
               "باكية", "باكيات", "باكو", "بواكي", "رزمة", "رزم"),
    "US": ("us",),
}
_UNIT_LOOKUP = {fold(word): unit for unit, words in _UNIT_SYNONYMS.items() for word in words}
CANONICAL_UNITS = frozenset(_UNIT_SYNONYMS)

_CURRENCY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "MAD": ("mad", "dh", "dhs", "dirham", "dirhams", "drh", "درهم", "دراهم", "د.م", "dhm"),
    "EUR": ("eur", "euro", "euros", "€"),
    "USD": ("usd", "$", "us$", "dollar", "dollars"),
}
_CURRENCY_LOOKUP = {fold(word): code for code, words in _CURRENCY_SYNONYMS.items() for word in words}
_CURRENCY_TOKEN_RE = re.compile(r"[^\W_]+|[€$]")
CURRENCY_CODES = frozenset(_CURRENCY_SYNONYMS)
"""Codes de devise produits par :func:`normalize_currency` pour une devise reconnue."""


def normalize_unit(value: Any) -> str | None:
    """Unité canonique : « pièces », « kg », « carton », « paquet » ou « US ».

    « pièce », « pcs », « unités », « u », « hba », « قطعة », « وحدة »… donnent « pièces » ;
    « US » (unité de stock de SAGE X3) est gardé tel quel. Une unité inconnue est renvoyée
    telle qu'écrite, sans espaces autour ; une valeur vide ou non textuelle donne ``None``.
    """
    text = clean_text(value, max_length=SHORT_TEXT_MAX) if isinstance(value, str) else None
    if text is None:
        return None
    key = fold(text).strip(" .").replace("(s)", "s")
    found = _UNIT_LOOKUP.get(key) or _UNIT_LOOKUP.get(key.replace(".", "")) or _darija_article_unit(key)
    return found or text


_LATIN_UNIT_PREFIXES = ("dl", "l")
_ARABIC_UNIT_PREFIXES = ("دال", "بال", "فال", "ال", "د", "ب")


def _darija_article_unit(key: str) -> str | None:
    """Unité précédée d'un article ou d'une préposition collés (« lkartonat » = les cartons, « dlkartonat »
    = des cartons, « دالكراطن », « بالكرطونة »)."""
    prefixes = _ARABIC_UNIT_PREFIXES if _has_arabic(key) else _LATIN_UNIT_PREFIXES
    for prefix in prefixes:
        rest = key[len(prefix):]
        if key.startswith(prefix) and len(rest) >= 3 and rest in _UNIT_LOOKUP:
            return _UNIT_LOOKUP[rest]
    return None


def normalize_currency(value: Any) -> str | None:
    """Code de devise : dh, dhs, dirham, درهم → MAD ; €, euro → EUR ; $, dollar → USD.

    La première devise reconnue dans le texte l'emporte (« MAD HT » → MAD). Un code de trois
    lettres inconnu est mis en majuscules (« gbp » → GBP) ; un autre texte est renvoyé tel
    qu'écrit. Une valeur vide ou non textuelle donne ``None``.
    """
    text = clean_text(value, max_length=SHORT_TEXT_MAX) if isinstance(value, str) else None
    if text is None:
        return None
    folded = fold(text)
    if folded in _CURRENCY_LOOKUP:
        return _CURRENCY_LOOKUP[folded]
    for word in _CURRENCY_TOKEN_RE.findall(folded):
        if word in _CURRENCY_LOOKUP:
            return _CURRENCY_LOOKUP[word]
    if re.fullmatch(r"[A-Za-z]{3}", text):
        return text.upper()
    return text


# ---------------------------------------------------------------- dates

_MONTHS: dict[str, int] = {fold(name): number for name, number in {
    "janvier": 1, "janv": 1, "jan": 1, "february": 2, "février": 2, "févr": 2, "fév": 2, "feb": 2,
    "mars": 3, "mar": 3, "march": 3, "avril": 4, "avr": 4, "april": 4, "apr": 4, "mai": 5, "may": 5,
    "juin": 6, "june": 6, "jun": 6, "juillet": 7, "juil": 7, "july": 7, "jul": 7, "août": 8, "aout": 8,
    "august": 8, "aug": 8, "septembre": 9, "sept": 9, "sep": 9, "september": 9, "octobre": 10, "oct": 10,
    "october": 10, "novembre": 11, "nov": 11, "november": 11, "décembre": 12, "déc": 12, "dec": 12,
    "december": 12, "january": 1,
    # arabe : noms du Maghreb et noms du Moyen-Orient
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "ماي": 5, "مايو": 5, "يونيو": 6, "يونيه": 6,
    "يوليوز": 7, "يوليو": 7, "غشت": 8, "أغسطس": 8, "شتنبر": 9, "سبتمبر": 9, "أكتوبر": 10, "نونبر": 11,
    "نوفمبر": 11, "دجنبر": 12, "ديسمبر": 12,
}.items()}
_MONTH_PATTERN = "|".join(sorted((re.escape(name) for name in _MONTHS), key=len, reverse=True))
_WEEKDAYS = _folded_set((
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت",
    "الأحد", "letnin", "ttlat", "larb3", "lkhmis", "jem3a", "sebt", "l7ed",
))

# Motifs dans l'ordre d'application : une zone reconnue par un motif est retirée avant le suivant.
_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ymd", re.compile(r"(?<!\d)(\d{4})([-/.])(\d{1,2})\2(\d{1,2})(?!\d)")),
    ("dmy", re.compile(r"(?<![\d.,/-])(\d{1,2})([-/.])(\d{1,2})\2(\d{4}|\d{2})(?!\d)")),
    ("d_month_y", re.compile(
        rf"(?<!\d)(\d{{1,2}})(?:er|eme)?\s*-?\s*({_MONTH_PATTERN})\.?(?:\s*-?\s*(\d{{4}}))?(?!\w)")),
    ("month_d_y", re.compile(
        rf"(?<!\w)({_MONTH_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{{4}}))?(?!\w)")),
    ("dm", re.compile(r"(?<![\d.,/-])(\d{1,2})/(\d{1,2})(?![\d/])")),
)


def _date_matches(folded: str) -> Iterator[tuple[str, re.Match[str]]]:
    """Zones de date d'un texte en forme de comparaison, sans recouvrement, dans l'ordre de priorité des motifs."""
    work = folded
    for kind, pattern in _DATE_PATTERNS:
        matches = list(pattern.finditer(work))
        for match in matches:
            yield kind, match
        if matches:
            work = _mask_spans(work, (match.span() for match in matches)).replace(_MASK_CHAR, "#")


def _full_year(year: int, today: date) -> int:
    """Année sur quatre chiffres : « 25 » donne 2025, « 99 » donne 1999.

    Une année à deux chiffres est placée dans le siècle en cours, sauf si elle dépasse de plus
    de 20 ans l'année de ``today`` : elle appartient alors au siècle précédent.
    """
    if year >= 100:
        return year
    full = 2000 + year
    return full - 100 if full > today.year + 20 else full


def _safe_date(year: int, month: int, day: int) -> date | None:
    if not 1900 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _closest_date(month: int, day: int, today: date) -> date | None:
    """Date sans année : l'occurrence la plus proche de ``today`` (l'année suivante en cas d'égalité)."""
    candidates = [found for year in (today.year - 1, today.year, today.year + 1)
                  if (found := _safe_date(year, month, day)) is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda found: (abs((found - today).days), found < today))


def _dmy(day: int, month: int, year: int | None, today: date) -> date | None:
    if month > 12 >= day:  # format mois/jour (américain) sans ambiguïté
        day, month = month, day
    if year is None:
        return _closest_date(month, day, today)
    return _safe_date(_full_year(year, today), month, day)


def _date_from_match(kind: str, match: re.Match[str], today: date) -> date | None:
    groups = match.groups()
    if kind == "ymd":
        return _safe_date(int(groups[0]), int(groups[2]), int(groups[3]))
    if kind == "dmy":
        return _dmy(int(groups[0]), int(groups[2]), int(groups[3]), today)
    if kind == "d_month_y":
        return _dmy(int(groups[0]), _MONTHS[groups[1]], int(groups[2]) if groups[2] else None, today)
    if kind == "month_d_y":
        return _dmy(int(groups[1]), _MONTHS[groups[0]], int(groups[2]) if groups[2] else None, today)
    return _dmy(int(groups[0]), int(groups[1]), None, today)


def _find_dates(text: str, today: date) -> list[date]:
    """Dates valides d'un texte, dans l'ordre où elles apparaissent."""
    found = [(match.start(), _date_from_match(kind, match, today)) for kind, match in _date_matches(fold(text))]
    return [value for _, value in sorted(found, key=lambda item: item[0]) if value is not None]


def normalize_date(value: Any, today: date | None = None) -> str | None:
    """Date au format ISO (AAAA-MM-JJ), ou ``None``.

    Formats : jj/mm/aaaa, jj-mm-aa, jj.mm.aaaa, aaaa-mm-jj, « 15 janvier 2025 », « 1er mars »,
    « December 20, 2026 », « 20 ديسمبر 2026 ». Une année sur deux chiffres est complétée
    (« 25 » → 2025). Sans année, la date retenue est l'occurrence la plus proche de ``today``
    (date du jour par défaut). Une date impossible (31/02/2025), une indication vague
    (« fin janvier », « semaine prochaine ») ou plusieurs dates différentes donnent ``None``.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_VALUE_CHARS:
        return None
    dates = set(_find_dates(text, today or date.today()))
    return dates.pop().isoformat() if len(dates) == 1 else None


# ---------------------------------------------------------------- téléphones

_PHONE_LABEL_RE = re.compile(r"^\s*(?:whatsapp|tel|tél|tél\.|phone|mobile|gsm|fax)\s*:?\s*", re.IGNORECASE)
_PHONE_CHARS_RE = re.compile(r"[\d+\s().\-/]+")


def normalize_phone(raw: Any) -> str | None:
    """Numéro de téléphone au format international E.164 (« +2126… »), ou ``None``.

    Formats marocains acceptés : « whatsapp:+212612345678 », « +212 6 12 34 56 78 »,
    « +212 (0)6 12 34 56 78 », « 00212612345678 », « 212612345678 », « 0612345678 »,
    « 06 12 34 56 78 », « 06.12.34.56.78 », « 06-12-34-56-78 », fixes en 05 et 08. Un numéro
    marocain compte neuf chiffres après l'indicatif 212 et commence par 5, 6, 7 ou 8. Un
    numéro étranger précédé de « + » ou « 00 » est gardé s'il compte de 8 à 15 chiffres.
    """
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str):
        return None
    text = _PHONE_LABEL_RE.sub("", prepare_text(raw).strip()).replace("(0)", "")
    if not text or not _PHONE_CHARS_RE.fullmatch(text) or "+" in text[1:]:
        return None
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        international = digits
    elif digits.startswith("00"):
        international = digits[2:]
    elif digits.startswith("212") and len(digits) in (12, 13):
        international = digits
    elif digits.startswith("0") and len(digits) == 10:
        international = "212" + digits[1:]
    else:
        return None
    if international.startswith("212"):
        national = international[3:]
        if national.startswith("0"):
            national = national[1:]
        if len(national) != 9 or national[0] not in "5678":
            return None
        return "+212" + national
    if not 8 <= len(international) <= 15 or international.startswith("0"):
        return None
    return "+" + international


# ---------------------------------------------------------------- renouvellement

def _pattern_regex(pattern: str) -> str:
    parts = [part for part in re.split(r"[\s']+", fold(pattern)) if part]
    body = r"[\s']+".join(re.escape(part) for part in parts)
    if _has_arabic(pattern):
        return rf"(?<!\w)[وف]?{body}"
    return rf"(?<!\w){body}(?!\w)"


def _alternatives(phrases: Iterable[str]) -> str:
    """Expression régulière qui reconnaît l'une des expressions (forme de comparaison).

    Entre deux mots, l'espace et l'apostrophe sont facultatifs : « l'mra », « l mra » et « lmra »
    sont trois écritures du même groupe darija (article collé ou séparé).
    """
    bodies = {r"[\s']*".join(re.escape(part) for part in re.split(r"[\s']+", fold(phrase)) if part)
              for phrase in phrases}
    return "(?:" + "|".join(sorted(bodies, key=len, reverse=True)) + ")"


def _sequence(*groups: Iterable[str]) -> str:
    """Suite de groupes d'expressions séparés par des espaces (ou des apostrophes)."""
    return r"[\s']+".join(_alternatives(group) for group in groups)


# Familles d'expressions de renouvellement. Chaque famille combine un mot de comparaison (« comme »,
# « kif », « بحال », « same as ») ou un verbe de répétition (« refaire », « 3awd », « عاود »,
# « repeat ») avec une référence à l'habitude ou à une commande passée (« la dernière fois »,
# « l'mra li fatet », « الشهر الماضي », « last month »). Une autre famille qualifie la commande
# d'habituelle (« notre commande habituelle », « ttalab dyal dima », « طلبيتنا المعتادة »,
# « our usual order »).
# « La même commande que », « même livraison que », « les mêmes quantités que » : article, « même », nom, « que ».
_FR_SAME_NOUNS = ("commande", "chose", "livraison", "envoi", "quantite", "quantites", "produit", "produits",
                  "article", "articles", "marchandise", "reference", "references")
_FR_COMPARE = (
    "comme", "comme pour", "comme lors de", "pareil que", "pareille que", "identique a", "idem que",
    "la meme que", "le meme que", "exactement comme", "tout comme",
    *(f"{article}{noun} que" for article in ("la meme ", "le meme ", "les memes ", "meme ", "memes ")
      for noun in _FR_SAME_NOUNS),
    # Devant « avant » ou « à », « que » s'élide : « même chose qu'avant ».
    "meme chose qu", "la meme chose qu", "pareil qu", "pareille qu", "idem qu", "la meme commande qu",
    "meme commande qu", "meme livraison qu", "la meme livraison qu",
)
_FR_PAST = (
    "d'habitude", "d'hab", "dhab", "d'ordinaire", "l'habitude", "precedemment", "d'avant", "a l'accoutumee",
    "de coutume", "avant", "a l'habitude", "habituellement", "a la derniere fois", "la derniere fois",
    "la fois derniere", "la fois passee", "la fois precedente", "l'autre fois",
    "la derniere", "la precedente", "la derniere commande", "la commande precedente", "notre derniere commande",
    "ma derniere commande", "la derniere livraison", "notre derniere livraison", "la livraison precedente",
    "le dernier envoi", "l'envoi precedent", "le mois dernier", "le mois passe", "le mois precedent",
    "la semaine derniere", "la semaine passee", "l'an dernier", "l'annee derniere", "l'annee passee",
)
_FR_REPEAT_VERBS = ("refaire", "refaites", "refais", "refaisons", "renouveler", "renouvelez", "renouvelle",
                    "renouvelons", "reconduire", "reconduisez", "repeter", "repetez", "reproduire", "reproduisez",
                    "reprendre", "reprenez", "reprenons", "repasser", "repassez", "repassons")
# « Celle de la commande précédente », « celle du mois dernier » : démonstratif qui reprend « quantité ».
_FR_DEMONSTRATIVES = ("celle", "celui", "ceux", "celles")
_FR_LINKS = ("de", "du", "des", "d")
# « Ce que nous avions commandé » : la commande déjà passée, reprise par un verbe de répétition.
_FR_ALREADY_ORDERED = ("ce que nous avons commande", "ce que nous avions commande", "ce que nous avons deja commande",
                       "ce que j'ai commande", "ce que j'avais commande", "ce qu'on a commande",
                       "ce qu'on avait commande", "ce que je commande d'habitude", "ce que nous commandons d'habitude",
                       "ce que nous avons pris", "ce que j'ai pris", "ce qu'on avait pris")
_FR_PRONOUNS = ("nous", "me", "moi", "m", "vous", "lui", "exactement", "svp")
_FR_REPEAT_OBJECTS = ("la commande", "notre commande", "ma commande", "la meme commande", "la meme chose",
                      "exactement la commande", "exactement la meme commande", "exactement la meme chose",
                      "la derniere commande", "notre derniere commande", "ma derniere commande",
                      "la commande precedente", "nos commandes", "la meme", "commande", "le meme envoi",
                      "notre dernier envoi", "le dernier envoi", "l'envoi precedent", "la derniere livraison",
                      "notre derniere livraison", "la livraison precedente")
# « l'envoi d'octobre », « la livraison du mois dernier » : commande, envoi ou livraison datés du passé.
_FR_DATED_DELIVERIES = ("la commande", "notre commande", "l'envoi", "notre envoi", "la livraison", "notre livraison")
_FR_PAST_DATES = (
    "du mois dernier", "du mois passe", "de la semaine derniere", "de la semaine passee",
    *(f"de {month}" for month in _MONTHS if len(month) > 3 and not _has_arabic(month)),
    *(f"d'{month}" for month in _MONTHS if len(month) > 3 and month[0] in "aeiou"),
)
_FR_USUAL_NOUNS = ("la commande", "notre commande", "ma commande", "nos commandes", "la livraison",
                   "notre livraison")
_FR_USUAL = ("habituelle", "habituelles", "reguliere", "regulieres")

_DARIJA_COMPARE = ("kif", "kima", "b7al", "bhal", "bhel", "b7el", "f7al", "fhal")
_DARIJA_TIMES = ("mra", "merra", "marra", "khatra", "khetra", "commande", "la commande", "komanda", "talab",
                 "ttalab", "chhar", "chher", "ch'har", "simana", "ssimana", "3am")
_DARIJA_PAST_QUALIFIERS = ("li fatet", "lli fatet", "li fat", "lli fat", "lfayta", "lfayt", "fayta", "li dazt",
                           "li daz", "lakhra", "lekhra", "l'akhira", "dyal qbel", "dyal 9bel", "li qbel", "li 9bel")
_DARIJA_HABIT = ("dima", "dimma", "dayman", "dayma", "daymen", "l3ada", "3ada", "l'3ada", "kol mra", "koll mra",
                 "kolla mra", "kol merra", "kol marra")
_DARIJA_ARTICLES = ("l", "el", "dik")
_DARIJA_SAME = ("nafs", "nfs", "nefs", "nefss", "nafss")
_DARIJA_SAME_OBJECTS = ("la commande", "l commande", "commande", "komanda", "l komanda", "talab", "ttalab", "tlab",
                        "l7aja", "7aja", "chi", "sel3a", "ssel3a")
_DARIJA_REPEAT = ("3awd", "3awed", "3awdo", "3awdou", "3awdi", "n3awdo", "n3awed", "3iwd", "jedded", "jeddedo",
                  "jeddedou", "njeddedo", "jdded", "jddedo")
_DARIJA_TO_US = ("lia", "lina", "li", "lya", "lna", "liya")
_DARIJA_OF_WORDS = ("dyal", "dial", "d")
_DARIJA_OF_US = ("dyalna", "dialna", "dyali", "diali")

_AR_COMPARE = ("كيف", "كيما", "بحال", "مثل", "كما", "زي")
_AR_TIMES = ("المرة", "المره", "الخطرة", "الطلبية", "الطلب", "الكوموند", "الكومند", "الشهر", "السيمانة",
             "الأسبوع", "العام", "السنة")
_AR_PAST_QUALIFIERS = ("الفايتة", "الفايت", "اللي فاتت", "اللي فات", "الي فاتت", "الي فات", "اللي دازت",
                       "الماضية", "الماضي", "السابقة", "السابق", "الأخيرة", "الأخير")
_AR_HABIT = ("ديما", "العادة", "عادة", "عادتنا", "عادتي", "المعتاد", "المعتادة")
# Nom de la commande, avec l'article ou un possessif collé (« طلبيتنا », notre commande).
_AR_ORDER_NOUNS = ("الطلبية", "الطلب", "الكوموند", "الكومند", "طلبية", "طلب", "طلبيتنا", "طلبيتي", "طلبنا", "طلبي",
                   "طلبياتنا", "الطلبيات", "طلباتنا")
_AR_SAME_OBJECTS = (*_AR_ORDER_NOUNS, "السلعة", "الشي", "الحاجة")
_AR_REPEAT = ("عاود", "عاودو", "عاودوا", "عاودي", "نعاودو", "نعاود", "أعد", "أعيدوا", "أعيدو", "إعادة",
              "تجديد", "جدد", "جددوا", "جددو", "نجدد", "كرر", "كرروا", "تكرار")
_AR_TO_US = ("ليا", "لينا", "لنا", "لي")
# Mêmes quantités ou mêmes produits qu'une commande passée (« نفس الكمية ديال الشهر الماضي »).
_AR_SAME_AMOUNTS = ("الكمية", "الكميات", "السلعة", "المنتوج", "المنتج", "المنتجات", "الأكياس", "الساشيات")
_AR_PAST_LINKS = ("ديال", "د", "بحال", "كيف", "مثل", "كما في", "ف", "في")
_DARIJA_SAME_AMOUNTS = ("lkmiya", "lkemmiya", "lkamiya", "l9adr", "lqadr", "sel3a", "ssel3a", "sachiyat")
_DARIJA_PAST_LINKS = ("dyal", "dial", "d", "kif", "b7al", "bhal", "f")
_AR_USUAL = ("المعتادة", "المعتاد", "العادية", "العادي", "ديال ديما", "ديال العادة", "ديالنا ديال ديما")

_EN_COMPARE = ("same as", "the same as", "same order as", "the same order as", "same quantities as",
               "the same quantities as", "same quantity as", "the same quantity as", "same items as",
               "the same items as", "like", "just like", "exactly like", "as")
_EN_PAST = ("usual", "last time", "the last time", "the previous time", "last month", "last week", "last year",
            "last order", "the last order", "our last order", "my last order", "the previous order",
            "previous order", "our previous order", "previously", "the previous one", "the last one",
            "last delivery", "the last delivery", "our last delivery", "before", "earlier", "always")
# « repeat last month's order » : le complément de temps possessif remplace le qualificatif.
_EN_PAST_POSSESSIVES = ("last month's", "last week's", "last year's", "last time's")
_EN_LINKS = ("in", "from", "on", "with", "for")
# « the same as my previous order », « like the last one » : déterminant, qualificatif du passé, nom.
_EN_PAST_DETERMINERS = ("our", "my", "your", "the", "that", "this", "last")
_EN_PAST_QUALIFIERS = ("last", "previous", "prior", "latest", "earlier", "usual", "regular")
_EN_PAST_NOUNS = ("order", "delivery", "purchase", "shipment", "time", "one", "batch", "month", "week", "year")
_EN_REPEAT = ("repeat", "renew", "redo", "duplicate", "re-order", "reorder")
_EN_DETERMINERS = ("our", "my", "the", "that", "a")
_EN_QUALIFIERS = ("last", "previous", "usual", "same", "latest", "regular", "standing", "most recent")
_EN_ORDER_NOUNS = ("order", "purchase")

_REORDER_GRAMMAR = (
    rf"(?<!\w){_sequence(_FR_COMPARE, _FR_PAST)}(?!\w)",
    rf"(?<!\w){_alternatives(_FR_COMPARE)}[\s']+{_alternatives(_FR_DEMONSTRATIVES)}[\s']+"
    rf"(?:{_alternatives(_FR_PAST_DATES)}|{_alternatives(_FR_LINKS)}[\s']*{_alternatives(_FR_PAST)})(?!\w)",
    rf"(?<!\w){_alternatives(_FR_REPEAT_VERBS)}(?:[\s'-]+{_alternatives(_FR_PRONOUNS)})*[\s']+"
    rf"{_alternatives(_FR_ALREADY_ORDERED)}(?!\w)",
    rf"(?<!\w){_alternatives(_FR_REPEAT_VERBS)}(?:[\s'-]+{_alternatives(_FR_PRONOUNS)})*[\s']+"
    rf"{_alternatives(_FR_REPEAT_OBJECTS)}(?!\w)",
    rf"(?<!\w){_alternatives(_FR_REPEAT_VERBS)}(?:[\s'-]+{_alternatives(_FR_PRONOUNS)})*[\s']+"
    rf"{_sequence(_FR_DATED_DELIVERIES, _FR_PAST_DATES)}(?!\w)",
    rf"(?<!\w){_sequence(_FR_USUAL_NOUNS, _FR_USUAL)}(?!\w)",
    rf"(?<!\w){_alternatives(_DARIJA_COMPARE)}[\s']+(?:{_alternatives(_DARIJA_HABIT)}|"
    rf"(?:{_alternatives(_DARIJA_ARTICLES)}[\s']*){{0,2}}{_alternatives(_DARIJA_TIMES)}[\s']+"
    rf"{_alternatives(_DARIJA_PAST_QUALIFIERS)})(?!\w)",
    rf"(?<!\w){_sequence(_DARIJA_SAME, _DARIJA_SAME_OBJECTS)}(?!\w)",
    rf"(?<!\w){_alternatives(_DARIJA_REPEAT)}(?:[\s']*{_alternatives(_DARIJA_TO_US)})?[\s']+"
    rf"(?:{_alternatives(_DARIJA_SAME)}[\s']+)?{_alternatives(_DARIJA_SAME_OBJECTS)}(?!\w)",
    rf"(?<!\w){_alternatives(_DARIJA_SAME_OBJECTS)}[\s']+(?:{_alternatives(_DARIJA_OF_US)}[\s']+)?"
    rf"{_alternatives(_DARIJA_OF_WORDS)}[\s']+{_alternatives(_DARIJA_HABIT)}(?!\w)",
    rf"(?<!\w)[وف]?{_alternatives(_AR_COMPARE)}\s+(?:{_alternatives(_AR_HABIT)}|"
    rf"{_sequence(_AR_TIMES, _AR_PAST_QUALIFIERS)})(?!\w)",
    rf"(?<!\w)[وف]?ك{_sequence(_AR_TIMES, _AR_PAST_QUALIFIERS)}(?!\w)",
    rf"(?<!\w)[وف]?نفس\s+{_alternatives(_AR_SAME_OBJECTS)}(?!\w)",
    rf"(?<!\w)[وف]?{_alternatives(_AR_REPEAT)}(?:\s+{_alternatives(_AR_TO_US)})?\s+(?:نفس\s+)?"
    rf"{_alternatives(_AR_SAME_OBJECTS)}(?!\w)",
    rf"(?<!\w)[وف]?{_sequence(_AR_ORDER_NOUNS, _AR_USUAL)}(?!\w)",
    rf"(?<!\w)[وفب]?نفس\s+{_alternatives(_AR_SAME_AMOUNTS)}\s+(?:{_alternatives(_AR_PAST_LINKS)}\s+)?"
    rf"{_sequence(_AR_TIMES, _AR_PAST_QUALIFIERS)}(?!\w)",
    rf"(?<!\w){_alternatives(_DARIJA_SAME)}[\s']+{_alternatives(_DARIJA_SAME_AMOUNTS)}[\s']+"
    rf"(?:{_alternatives(_DARIJA_PAST_LINKS)}[\s']+)?(?:{_alternatives(_DARIJA_ARTICLES)}[\s']*){{0,2}}"
    rf"{_alternatives(_DARIJA_TIMES)}[\s']+{_alternatives(_DARIJA_PAST_QUALIFIERS)}(?!\w)",
    rf"(?<!\w){_sequence(_EN_COMPARE, _EN_PAST)}(?!\w)",
    rf"(?<!\w){_alternatives(_EN_COMPARE)}[\s']+{_alternatives(_EN_LINKS)}[\s']+{_alternatives(_EN_PAST)}(?!\w)",
    rf"(?<!\w){_alternatives(_EN_COMPARE)}[\s']+(?:{_alternatives(_EN_PAST_DETERMINERS)}[\s']+)?"
    rf"{_alternatives(_EN_PAST_QUALIFIERS)}[\s']+{_alternatives(_EN_PAST_NOUNS)}(?!\w)",
    rf"(?<!\w){_alternatives(_EN_REPEAT)}(?:[\s']+{_alternatives(_EN_DETERMINERS)})?"
    rf"(?:[\s']+{_alternatives((*_EN_QUALIFIERS, *_EN_PAST_POSSESSIVES))})?[\s']+"
    rf"{_alternatives(_EN_ORDER_NOUNS)}(?!\w)",
    r"(?<!\w)(?:the|our|my)[\s']+usual(?=\s*(?:$|[.!,;]|please|pls|order|thanks))",
    r"(?<!\w)(?:usual|regular|standing)[\s']+order(?!\w)",
    r"(?<!\w)same[\s']+again(?!\w)",
)

_REORDER_RE = re.compile("|".join([*(_pattern_regex(pattern) for pattern in ALL_REORDER_PATTERNS),
                                   *_REORDER_GRAMMAR]))


def detect_reorder(text: Any) -> bool:
    """Vrai quand le texte demande de renouveler une commande (« comme d'habitude », « kif dima »).

    Deux sources : les expressions de ``catalog.REORDER_PATTERNS`` et leurs combinaisons
    régulières, par famille et par langue : mot de comparaison suivi d'une référence à
    l'habitude ou à une commande passée (« comme le mois dernier », « même livraison que la
    semaine dernière », « b7al l'mra li fatet », « بحال الشهر اللي فات », « same as last week »),
    « même commande » (« nafs la commande », « نفس الكوموند »), verbe de répétition suivi de la
    commande (« refaire la commande », « 3awdo lina la commande », « عاود نفس الطلبية »,
    « repeat last month's order »). Un verbe qui peut aussi désigner le renvoi d'une livraison
    perdue (« renvoyer ») n'est pas une famille : le message reste ambigu. La comparaison
    ignore la casse, les accents, le type d'apostrophe et le nombre d'espaces. En écriture arabe,
    la conjonction collée « و » ou « ف » est acceptée devant l'expression. « b7al » seul signifie
    « environ » ou « comme » et ne suffit pas.
    """
    if not isinstance(text, str) or not text:
        return False
    return _REORDER_RE.search(fold(text[:MAX_TEXT_CHARS])) is not None


# ---------------------------------------------------------------- quantité dans un message

_COUNT_STEMS = frozenset(_stem(fold(word)) for word in (
    "sachet", "sac", "sachiyat", "sachiat", "sachyat", "sachiya", "sachia", "bag", "sack", "poche", "pochette",
    "emballage", "pièce", "pcs", "pc", "pce", "u", "unité", "unit", "hba", "carton", "paquet", "pack", "kg",
    "kilo", "ساشي", "ساشة", "ساك", "صاك", "كيس", "أكياس", "قطعة", "حبة", "وحدة", "كرتون", "باكية",
    *(word for words in _UNIT_SYNONYMS.values() for word in words if word != "us"),
))
_NON_COUNT_AFTER = _folded_set((
    "mad", "dh", "dhs", "dirham", "dirhams", "drh", "درهم", "دراهم", "eur", "euro", "euros", "€", "usd", "$",
    "dollar", "dollars", "%", "g", "gr", "grs", "gramme", "grammes", "gram", "grams", "gsm", "غرام", "غ", "cm",
    "mm", "m", "mètre", "mètres", "jour", "jours", "j", "mois", "semaine", "semaines", "an", "ans", "année",
    "années", "h", "heure", "heures", "min", "minute", "minutes", "couleur", "couleurs", "lot", "lots",
    "fois", "face", "faces", "côté", "côtés", "day", "days", "week", "weeks", "month", "months", "year",
    "years", "color", "colors", "colour", "colours", "side", "sides", "times", "يوم", "أيام", "ساعة",
    "ساعات", "سنة", "شهر", "أشهر", "rue", "avenue", "av", "bd", "boulevard", "route", "étage", "°",
    # durées en darija (lettres latines et arabes) : « 3 iyam » (trois jours), « jouj simanat »
    "iyam", "ayam", "ayyam", "iyyam", "yam", "nhar", "nhara", "simana", "simanat", "sa3a", "sa3at", "chhar",
    "chhor", "chhour", "chher", "3am", "snin", "d9i9a", "dqiqa", "lyam", "ليام", "نهار", "سيمانة", "سيمانات",
    "أسبوع", "أسابيع", "دقيقة", "دقائق", "شهور", "سنين", "سنوات", "hours",
))

_LABEL_BEFORE_RE = re.compile(
    r"(?<!\S)(?:quantite|quantites|qte|qty|quantity|quantities|nombre|nbre|nbr|kmiya|lkmiya|الكمية|كمية|عدد)(?!\S)"
)
_PRICE_WORDS = _folded_set(("prix", "pu", "tarif", "montant", "total", "coût", "price", "cost", "amount", "السعر",
                             "سعر", "ثمن", "taman", "thaman"))
_PACKAGING_UNITS = frozenset({"carton", "paquet", "kg"})
_REFERENCE_BEFORE_RE = re.compile(
    r"(?<!\S)(?:n|no|num|numero|ref|reference|po|bc|bl|facture|invoice|رقم|laize|laise|grammage|format|"
    r"taille|dimension|dimensions|quai|tel|telephone|fax|code|cl)(?: [°#:])?$"
)
_DELTA_BEFORE_RE = re.compile(
    r"(?<!\S)(?:zid|zido|zidou|zidouna|ajouter|ajoutez|ajoute|rajouter|rajoutez|add|plus|زيد|زيدو|زيدوا|زيدني|"
    r"زيدلي)(?: \S+)?$"
)
_INSTEAD_BEFORE_RE = re.compile(r"(?:au lieu de|lieu d '|instead of|بدل|عوض|f blast|blast)$")
_INSTEAD_AFTER_RE = re.compile(r"^(?:au lieu|instead of|بدل|عوض|f blast|blast)(?!\S)")
# Négation juste avant le nombre : « machi 3000 » (pas 3000), « not 3000 », « ماشي 3000 ».
# Reprise d'une quantité dictée ou écrite : le nombre qui suit « non », « plutôt », « pardon », « en
# fait », « I mean », « لا » ou « la la » remplace celui qui précède (« 3000 sacs, euh non, 3500 sacs »).
_CORRECTION_BEFORE_RE = re.compile(
    r"(?<!\S)(?:non|plutot|pardon|enfin|en fait|finalement|oups|je veux dire|je voulais dire|correction|no|"
    r"sorry|i mean|rather|actually|make it|in fact|la la|smeh lia|smhli|ghalt|khelliha|khalliha|dirha|"
    r"سمح ليا|عفوا|لا|بل|خليها|في الحقيقة)(?: (?:euh|heu|uh|اه))?$"
)
_NEGATION_BEFORE_RE = re.compile(r"(?<!\S)(?:machi|mashi|machy|ماشي|مشي|ليس|pas|not)(?: (?:l|la|le|les|the))?$")
# Nombre distributif ou contenu d'un conditionnement : « كل ١٠٠ كيس » (par 100 sacs), « chaque 50 »,
# « en paquets de 100 », « cartons de 500 », « packs of 50 », « باكية فيها 100 ».
_DISTRIBUTIVE_BEFORE_RE = re.compile(r"(?<!\S)(?:par|per|each|every|chaque|كل|lkol|kol|koll|kul)$")
_CONTAINER_WORDS = _folded_set(("lot", "lots", "boite", "boites", "boîte", "boîtes", "box", "boxes", "bundle",
                                "bundles", "ballot", "ballots", "rouleau", "rouleaux", "pack", "packs", "paquet",
                                "paquets", "carton", "cartons", "sachet", "sachets"))
_CONTAINER_LINKS = _folded_set(("de", "d", "of", "dyal", "dial", "ديال", "فيه", "فيها", "fih", "fiha", "b", "à"))
# Quantité minimale demandée dans une question (« le minimum de commande », « minimum order »,
# « الحد الأدنى للطلب ») : ce n'est pas une quantité commandée.
_MINIMUM_RE = re.compile(r"(?<!\S)(?:moq|minimum|min|الحد الادني|الادني|ادني)(?!\S)")
_MINIMUM_CONTEXT_RE = re.compile(r"(?<!\S)(?:commande|commander|order|ordering|achat|طلب|للطلب|الطلب|moq)(?!\S)")
# Adverbes d'approximation : la quantité approximative donne le nombre cité (« environ 800 »,
# « b7al 4000 », « about two thousand », « حوالي 500 »).
_APPROXIMATION_BEFORE_RE = re.compile(
    r"(?<!\S)(?:environ|approximativement|a peu pres|grosso modo|about|around|approximately|roughly|some|"
    r"b7al|bhal|ta9riban|taqriban|chi|تقريبا|حوالي|حوالى|نحو|قرابة|شي)$"
)
# Fourchette de quantités : « entre 2000 et 3000 », « de 2000 à 3000 », « 2000 à 3000 », « 2000-3000 »,
# « between 2,000 and 3,000 », « من 2000 حتى 3000 » (bornes lues séparément).
_RANGE_LINKS = _folded_set(("a", "à", "au", "-", "et", "to", "and", "ou", "or", "l", "hta", "7ta", "حتى", "إلى",
                            "الى", "ل", "w", "و"))
_RANGE_OPENERS = _folded_set(("entre", "between", "de", "from", "mn", "men", "بين", "من", "ما بين"))
_ORDER_INTENT_RE = re.compile(
    r"(?<!\w)(?:"
    r"je voudrais|nous voudrions|on voudrait|je veux|nous voulons|on veut|souhaite commander|"
    r"souhaitons commander|commander|commandons|(?:je|on) commande|passer (?:une )?commande|bon de commande|"
    r"il (?:(?:me|nous|m ?'|en) ?){1,2}(?:faut|faudrait|faudra)|"
    r"(?:j ?' ?(?:en )?(?:ai|aurai)|nous (?:en )?(?:avons|aurons)|on (?:en )?(?:a|aura)) besoin|besoin de|"
    r"livrer|livrez|preparer|preparez|envoyer|envoyez|enregistrer|fournir|fournissez|expedier|expediez|"
    r"reserver|reservez|je prends|nous prenons|on prend|on part sur|confirmons (?:une|la|notre) commande|"
    r"je (?:vous )?confirme (?:la |ma |notre |une )?commande|mettre|"
    # Acceptation d'un devis reçu : le devis devient un bon de commande.
    r"(?:je (?:vous )?confirme|nous confirmons|j ?' ?accepte|nous acceptons) (?:le |notre |votre |ce )?devis|"
    r"bon pour accord|(?:i|we) (?:confirm|accept) (?:the|your|our) (?:quote|quotation|offer)|"
    # Pronoms compléments entre le sujet et le verbe : « je vous en prends », « j'en veux », « on en prend ».
    r"(?:je|j ?'|nous|on) ?(?:(?:vous|te|t ?'|en|y|les|le|la|l ?'|lui|leur|me|m ?') ?){1,3}"
    r"(?:prends|prendrai|prendrais|prenons|prendrons|prend|veux|voudrais|voulons|voudrions|veut|voudrait|"
    r"commande|commanderai|commanderais|commandons)|"
    r"(?:vais|va|allons|vas) (?:(?:vous|en) )?(?:prendre|commander)|"
    # « bghit » suivi d'un autre verbe (« bghit n3ref », je veux savoir) n'est pas une commande,
    # sauf les verbes d'achat (« bghit nakhod », « bghit nchri »).
    r"(?:bghit|bghina|bgheet|bgit)(?!\s+n(?!akhod|akhd|chri|commandi)[^\W_])|"
    r"khassni|khasni|khessni|khassna|khasna|5assni|5asni|5assna|3tini|3tina|3tiwni|3tiwna|sift|siftli|"
    r"siftlia|siftlina|sifto|siftou|tsiftoli|jib|jibli|jibo|jibou|jiboli|nakhod|nakhdo|nchri|nchriw|"
    r"dir lia|dir lina|diro lia|diro lina|dirou lina|dirli|dirlina|wajjad lia|wajjdo lia|wajdo lina|"
    r"sejjel|sajjal|sejlo|sajlo|"
    r"would like|want to order|wish to order|to order|please send|send|please deliver|deliver|we need|"
    r"(?:can|could|may) (?:i|we) (?:get|have|order)|"
    r"(?:je|on|nous) (?:peux|peut|pouvons|pourrais|pourrait|pourrions) (?:avoir|recevoir|commander)|"
    r"i need|i want|we want|place an order|order ?:|please process|please supply|supply|we require|"
    r"(?:i|we) ?' ?(?:d|ll) (?:like|need|want|take)|(?:i|we) will (?:need|take)|"
    r"(?:nbghi|nbghiw)(?!\s+n(?!akhod|akhd|chri|commandi)[^\W_])|"
    r"(?:بغيت|بغينا|نبغي|نبغيو|نبغو)(?!\s+ن(?!اخد|شري)\w)|خاصني|خصني|خاصنا|خصنا|عطيني|عطينا|عطيونا|صيفط|صيفطو|صيفطلي|"
    r"صيفطولي|صيفطو لينا|نطلب|نرغب|نود|نريد|اريد|ابغي|جيب|جيبو|تزويدنا|زودونا|زودنا|الرجاء ارسال|"
    r"يرجي ارسال|الرجاء تزويد|يرجي تزويد|ديرو ليا|دير ليا|ديرلي|وجد ليا|وجدو ليا|حضر ليا|حضرو ليا|سجل ليا|"
    r"سجلو|نحتاج|نحتاجو|محتاجين|محتاج|بحاجة الي"
    r")(?!\w)"
)


@dataclass(frozen=True)
class _QuantityCandidate:
    value: float
    score: int
    position: int
    unit: str | None
    start: int = 0
    end: int = 0
    corrected: bool = False


def _window_before(tokens: Sequence[_Token], index: int, size: int) -> list[_Token]:
    window: list[_Token] = []
    position = index - 1
    while position >= 0 and len(window) < size:
        token = tokens[position]
        if token.kind == "nl" or (token.kind == "punct" and token.text in _BOUNDARY_PUNCT):
            break
        window.append(token)
        position -= 1
    window.reverse()
    return window


def _words_before(tokens: Sequence[_Token], index: int, size: int) -> list[_Token]:
    """Mots qui précèdent ``tokens[index]`` dans la même phrase, virgules et tirets ignorés."""
    window: list[_Token] = []
    position = index - 1
    while position >= 0 and len(window) < size:
        token = tokens[position]
        if token.kind == "nl" or (token.kind == "punct" and token.text not in ",-"):
            break
        if token.kind != "punct":
            window.append(token)
        position -= 1
    window.reverse()
    return window


def _window_after(tokens: Sequence[_Token], index: int, size: int, *, stop_at_number: bool) -> list[_Token]:
    window: list[_Token] = []
    position = index
    while position < len(tokens) and len(window) < size:
        token = tokens[position]
        if token.kind == "nl" or (token.kind == "punct" and token.text in _BOUNDARY_PUNCT):
            break
        if stop_at_number and (token.kind == "num" or _word_number(token) is not None):
            break
        window.append(token)
        position += 1
    return window


def _joined(tokens: Iterable[_Token]) -> str:
    return " ".join(token.text for token in tokens)


def _is_reference(tokens: Sequence[_Token], run: _NumberRun) -> bool:
    """Vrai pour un nombre collé à une référence, une date ou un mot composé (« PO-5521 », « 2026/118 »)."""
    first, last = tokens[run.start], tokens[run.end - 1]
    if run.start > 0:
        previous = tokens[run.start - 1]
        if _glued(previous, first) and previous.kind == "punct" and previous.text in "-/+#°.,'_":
            return True
    if run.end < len(tokens):
        following = tokens[run.end]
        if _glued(last, following) and following.text in "/-.,":
            after = tokens[run.end + 1] if run.end + 1 < len(tokens) else None
            if after is not None and _glued(following, after) and after.kind in ("num", "word"):
                return True
    return False


def _follows_price_label(before: Sequence[_Token]) -> bool:
    """Vrai quand le nombre suit un libellé de prix, éventuellement séparé par « : », « de », « unitaire »…"""
    for token in reversed(before):
        if token.text in _PRICE_FILLERS:
            continue
        return token.text in _PRICE_WORDS
    return False


def _is_date_part(before: Sequence[_Token], after: Sequence[_Token], value: float) -> bool:
    neighbours = [token.text for token in before[-1:]] + [token.text for token in after[:1]]
    if any(word in _MONTHS or word in _WEEKDAYS for word in neighbours) and value <= 31:
        return True
    year_like = value.is_integer() and 1900 <= value <= 2100
    return year_like and any(token.text in _MONTHS for token in before[-3:])


def _count_noun(after: Sequence[_Token], suffix: str) -> str | None:
    if suffix and (suffix in _COUNT_SUFFIXES or _stem(suffix) in _COUNT_STEMS):
        return suffix
    for token in after:
        if token.kind == "word" and (_stem(token.text) in _COUNT_STEMS or _darija_article_unit(token.text)):
            return token.text
    return None


_LINK_WORDS = _folded_set(("de", "d", "des", "du", "of", "dyal", "dial", "ديال", "د", "من"))


# Nombre placé après la description de l'article, sans verbe de commande ni unité (« sachets fond
# plat : 5000 », « sac bla yddin 3000 3afak ») : la quantité suit le produit. Mots qui, juste devant
# le nombre, en font autre chose qu'une quantité : prix (« à 45 »), jour du mois (« pour le 15 »),
# contenu d'un conditionnement (« de 500 »), numéro de commande ou de devis.
_NOT_QUANTITY_LINKS = _folded_set((
    "a", "à", "at", "pour", "for", "de", "d", "of", "par", "per", "en", "in", "sur", "du", "des", "au", "aux",
    "avec", "with", "le", "la", "les", "l", "the", "avant", "apres", "before", "after", "by", "jusqu", "vers",
    "f", "fi", "b", "dyal", "dial", "ديال", "ب", "ل", "في", "من", "ف", "commande", "order", "cmd", "devis",
    "ou", "or", "et", "and", "w", "و", "entre", "between", "from", "to", "mn", "men",
))
_ITEM_SEPARATORS = frozenset(",-:=×*" + _MASK_CHAR)
_ITEM_WINDOW = 8
# Un article cité parmi les trois mots qui précèdent le nombre suffit ; un article plus éloigné (« des sacs
# avec cordelette pour mon concept store, 150 ») exige que le nombre termine la phrase, éventuellement
# suivi d'une formule de politesse.
_ITEM_NEAR_WORDS = 3
_CLOSING_FILLERS = _folded_set((
    "svp", "stp", "merci", "please", "pls", "thanks", "s", "il", "vous", "plait", "3afak", "afak", "عافاك", "من",
    "فضلكم", "فضلك", "pcs", "'",
))
# Racines trop courantes pour dire qu'un nombre qui suit décrit un article (« sans frais », « fond »).
_GENERIC_ITEM_STEMS = frozenset({_stem(fold(word)) for word in (
    "sans", "avec", "fond", "pas", "bla", "without", "with", "no", "ma", "sos", "de", "d", "des",
)})
_ITEM_STEMS = frozenset(
    stem for stem in _COUNT_STEMS | _HANDLE_STEMS | _FOOD_USAGE_STEMS | _KEYWORD_STEMS
    if len(stem) >= 3 and stem not in _GENERIC_ITEM_STEMS and stem not in _KEYWORD_FILLERS
    and stem not in {_stem(word) for word in _NOT_QUANTITY_LINKS}
)


def _follows_item(tokens: Sequence[_Token], run: _NumberRun) -> bool:
    """Vrai quand le nombre suit, dans la même phrase, la description d'un article (sac, sachet, poignées…).

    Le nombre doit être entier et ne pas suivre une préposition ou un mot de référence
    (:data:`_NOT_QUANTITY_LINKS`) : « sachets fond plat : 5000 » donne une quantité, « sacs à 45 » un prix.
    """
    if not run.value.is_integer():
        return False
    words: list[_Token] = []
    position = run.start - 1
    while position >= 0 and len(words) < _ITEM_WINDOW:
        token = tokens[position]
        if token.kind == "nl" or (token.kind == "punct" and token.text not in _ITEM_SEPARATORS):
            break
        if token.kind != "punct":
            words.append(token)
        position -= 1
    if not words or words[0].text in _NOT_QUANTITY_LINKS or any(word.text in _PRICE_WORDS for word in words):
        return False
    is_item = [word.kind == "word" and _stem(word.text) in _ITEM_STEMS for word in words]
    if any(is_item[:_ITEM_NEAR_WORDS]):
        return True
    rest = _window_after(tokens, run.end, 6, stop_at_number=False)
    return any(is_item) and all(token.text in _CLOSING_FILLERS for token in rest)


def _first_content(after: Sequence[_Token]) -> _Token | None:
    """Premier jeton après le nombre, sans la préposition « de » / « dyal » (« 3 de jours » → « jours »)."""
    for token in after[:2]:
        if token.text not in _LINK_WORDS and token.text != "'":
            return token
    return None


def _is_container_content(before: Sequence[_Token]) -> bool:
    """Vrai pour le contenu d'un conditionnement (« cartons de 500 », « par paquets de 100 »)."""
    if len(before) < 2 or before[-1].text not in _CONTAINER_LINKS:
        return False
    container = before[-2].text
    return container in _CONTAINER_WORDS or normalize_unit(container) in ("carton", "paquet")


def _is_minimum_question(tokens: Sequence[_Token], run: _NumberRun) -> bool:
    """Nombre cité avec « minimum de commande », « minimum order », « الحد الأدنى للطلب »."""
    context = _joined(_window_before(tokens, run.start, 6))
    return bool(_MINIMUM_RE.search(context)) and bool(_MINIMUM_CONTEXT_RE.search(context))


def _parenthesised_unit(tokens: Sequence[_Token], index: int) -> str | None:
    """Unité entre parenthèses juste après la quantité ou son nom (« 4000 sachets (pièces) », « 500 (cartons) »)."""
    for position in range(index, min(index + 3, len(tokens) - 2)):
        token = tokens[position]
        if token.kind == "nl":
            break
        if token.text == "(" and tokens[position + 2].text == ")":
            unit = normalize_unit(tokens[position + 1].text)
            return unit if unit in CANONICAL_UNITS and unit != "US" else None
    return None


def _score_run(tokens: Sequence[_Token], run: _NumberRun) -> _QuantityCandidate | None:
    """Score d'un nombre comme quantité commandée (voir le docstring du module), ou ``None`` s'il est écarté."""
    first, last = tokens[run.start], tokens[run.end - 1]
    if run.value <= 0 or run.value > 1e9 or first.leading_zero:
        return None
    if _is_reference(tokens, run) or _is_hyphenated(tokens, run):
        return None
    before = _window_before(tokens, run.start, 4)
    after = _window_after(tokens, run.end, 6, stop_at_number=True)
    # « un » / « wa7ed » seul est un article, sauf devant une unité de conditionnement (« wa7ed carton »).
    if run.lone_one and not (after and normalize_unit(after[0].text) in _PACKAGING_UNITS):
        return None
    suffix = last.suffix if last.kind == "num" else ""
    following = _first_content(after)
    if suffix in _MEASURE_SUFFIXES or (following is not None and following.text in _NON_COUNT_AFTER):
        return None
    recent = _joined(before[-3:])
    excluding = (_REFERENCE_BEFORE_RE, _DELTA_BEFORE_RE, _INSTEAD_BEFORE_RE, _NEGATION_BEFORE_RE,
                 _DISTRIBUTIVE_BEFORE_RE)
    if _follows_price_label(before) or any(pattern.search(recent) for pattern in excluding):
        return None
    if _is_container_content(before) or _is_minimum_question(tokens, run):
        return None
    if _is_date_part(before, after, run.value):
        return None
    score = 0
    noun = _count_noun(after, suffix)
    unit = None
    if noun:
        score += 3
        canonical = normalize_unit(noun)
        unit = canonical if canonical in CANONICAL_UNITS else None
    unit = unit or _parenthesised_unit(tokens, run.end)
    if _LABEL_BEFORE_RE.search(_joined(before)):
        score += 3
    if _INSTEAD_AFTER_RE.search(_joined(_window_after(tokens, run.end, 4, stop_at_number=False))):
        score += 3
    corrected = bool(_CORRECTION_BEFORE_RE.search(_joined(_words_before(tokens, run.start, 3))))
    if corrected:
        score += 3
    if _ORDER_INTENT_RE.search(_joined(_window_before(tokens, run.start, 6))):
        score += 1
    if _APPROXIMATION_BEFORE_RE.search(recent):
        score += 1
    if score == 0 and _follows_item(tokens, run):
        score = 1
    if score == 0:
        return None
    return _QuantityCandidate(run.value, score, first.start, unit, run.start, run.end, corrected)


def _range_partner(tokens: Sequence[_Token], runs: Sequence[_NumberRun], index: int) -> _NumberRun | None:
    """Autre borne d'une fourchette dont ``runs[index]`` fait partie (« entre 2000 et 3000 »), ou ``None``.

    Deux nombres reliés par « à », « - », « to », « حتى »… forment une fourchette ; « et », « and »,
    « ou » et « و » ne la forment qu'après « entre », « between » ou « بين », car ils relient aussi
    deux quantités distinctes. La borne basse doit être inférieure à la borne haute.
    """
    run = runs[index]
    neighbours = (runs[index - 1] if index else None, runs[index + 1] if index + 1 < len(runs) else None)
    for other in neighbours:
        if other is None:
            continue
        low, high = (other, run) if other.start < run.start else (run, other)
        between = tokens[low.end:high.start]
        if len(between) != 1 or between[0].text not in _RANGE_LINKS:
            continue
        link = between[0].text
        opener = tokens[low.start - 1].text if low.start else ""
        if link in _COORDINATING_LINKS and opener not in _RANGE_OPENERS:
            continue
        if link == "-" and not (_glued(tokens[low.end - 1], between[0]) or opener in _RANGE_OPENERS):
            continue
        if low.value < high.value:
            return other
    return None


_COORDINATING_LINKS = _folded_set(("et", "and", "w", "و", "ou", "or", "l", "ل"))


def _best_candidate(tokens: Sequence[_Token]) -> tuple[_QuantityCandidate, _NumberRun | None] | None:
    """Meilleure quantité du message et, quand elle fait partie d'une fourchette, l'autre borne."""
    runs = list(_number_runs(tokens))
    scored = [(index, candidate) for index, run in enumerate(runs) if (candidate := _score_run(tokens, run))]
    if not scored:
        return None
    # Une reprise (« pardon 2500 ») remplace la quantité qui précède, quel que soit le score de celle-ci.
    corrections = [item for item in scored if item[1].corrected]
    index, best = (corrections[-1] if corrections
                   else max(scored, key=lambda item: (item[1].score, -item[1].position)))
    return best, _range_partner(tokens, runs, index)


def _quantity_from_tokens(tokens: Sequence[_Token]) -> _QuantityCandidate | None:
    """Quantité commandée la plus probable ; ``None`` quand le client donne une fourchette (à confirmer)."""
    found = _best_candidate(tokens)
    if found is None or found[1] is not None:
        return None
    return found[0]


def _quantity_range(tokens: Sequence[_Token]) -> tuple[float, float] | None:
    """Bornes de la fourchette de quantités du message (« entre 2000 et 3000 sacs »), ou ``None``."""
    found = _best_candidate(tokens)
    if found is None or found[1] is None:
        return None
    low, high = sorted((found[0].value, found[1].value))
    return low, high


# Tableau à colonnes séparées par « | » (texte extrait d'un bon de commande PDF) : en-têtes reconnus.
_TABLE_HEADERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("quantite", re.compile(r"^(?:qte|qty|quantite|quantites|quantity|nbre|nombre|الكمية|كمية)\b")),
    ("prix_total", re.compile(r"^(?:prix total|total|montant|amount|المبلغ|المجموع)\b")),
    ("prix_unitaire", re.compile(r"^(?:pu|p\.u|prix unitaire|prix|unit price|price|السعر|سعر الوحدة)\b")),
    ("unite", re.compile(r"^(?:unite|unit|u)$")),
)


def _table_cells(line: str) -> list[str]:
    if "|" not in line:
        return []
    cells = [cell.strip() for cell in line.split("|")]
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return cells


def _table_columns(cells: Sequence[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, cell in enumerate(cells):
        folded = fold(cell).strip()
        for name, pattern in _TABLE_HEADERS:
            if name not in columns and pattern.search(folded):
                columns[name] = index
                break
    return columns


def _table_values(prepared: str) -> dict[str, Any]:
    """Quantité, prix et unité de la première ligne d'un tableau dont l'en-tête a une colonne quantité."""
    lines = prepared.splitlines()
    for index, line in enumerate(lines):
        columns = _table_columns(_table_cells(line))
        if "quantite" not in columns:
            continue
        for row_line in lines[index + 1:]:
            row = _table_cells(row_line)
            if not row:
                if row_line.strip():
                    break
                continue
            if all(set(cell) <= set("-:= ") for cell in row):
                continue
            cell = row[columns["quantite"]] if columns["quantite"] < len(row) else None
            quantity = parse_quantity(cell)
            if quantity is not None and quantity > 0:
                return _table_row_values(row, columns, quantity)
    return {}


def _table_row_values(row: Sequence[str], columns: Mapping[str, int], quantity: float) -> dict[str, Any]:
    def cell(name: str) -> str | None:
        position = columns.get(name)
        return row[position] if position is not None and position < len(row) else None

    values: dict[str, Any] = {"quantite": quantity}
    for name in ("prix_unitaire", "prix_total"):
        number = parse_quantity(cell(name))
        if number is not None:
            values[name] = number
    unit = normalize_unit(cell("unite"))
    if unit:
        values["unite"] = unit
    return values


# ---------------------------------------------------------------- message cité ou transféré

# En-tête d'un message cité dans une réponse : la partie qui suit est un message antérieur, souvent
# écrit par TECPAP (signature du service commercial), et non la demande du client.
_REPLY_HEADER_RE = re.compile(
    r"(?im)^[ \t]*(?:-{2,}[ \t]*(?:message d'origine|message original|original message|الرسالة الأصلية|"
    r"الرسالة الاصلية)[ \t]*-{2,}|(?:le|on)\b[^\n]{0,150}\b(?:a écrit|a ecrit|wrote)[ \t]*:|"
    r"(?:de|from)[ \t]*:[^\n]*\n[ \t]*(?:envoyé|envoye|sent)[ \t]*:)"
)
# En-tête d'un message transféré : la demande transférée fait partie du message, sauf quand
# l'expéditeur a écrit sa propre demande au-dessus.
_FORWARD_HEADER_RE = re.compile(
    r"(?im)^[ \t]*-{2,}[ \t]*(?:message transféré|message transfere|message transmis|forwarded message|"
    r"رسالة محولة)[ \t]*-{2,}"
)
_QUOTED_LINE_RE = re.compile(r"(?m)^[ \t]*>.*(?:\n|$)")
_MIN_MAIN_WORDS = 4


def _main_message(text: str) -> str:
    """Partie du message écrite par l'expéditeur, sans le message cité dans une réponse.

    Le texte qui suit un en-tête de réponse (« -----Message d'origine----- », « Le … a écrit : »,
    « On … wrote: », « De : … Envoyé : ») et les lignes citées commençant par « > » sont
    retirés ; un message transféré n'est retiré que si l'expéditeur a écrit au moins quelques mots
    au-dessus. Quand il ne reste presque rien, le texte complet est renvoyé.
    """
    main = text
    for pattern in (_REPLY_HEADER_RE, _FORWARD_HEADER_RE):
        match = pattern.search(main)
        if match and len(_WORD_RE.findall(main[:match.start()])) >= _MIN_MAIN_WORDS:
            main = main[:match.start()]
    main = _QUOTED_LINE_RE.sub("", main)
    return main if len(_WORD_RE.findall(main)) >= _MIN_MAIN_WORDS or main.strip() == text.strip() else text


# ---------------------------------------------------------------- nom du client

@dataclass(frozen=True)
class _ClientCandidate:
    name: str
    priority: int
    position: int


_PRIORITY_LABEL = 100
_PRIORITY_AFFILIATION = 90
_PRIORITY_BEHALF = 88
_PRIORITY_NAMED = 85
_PRIORITY_COMPANY = 80
_PRIORITY_BUSINESS_INTRO = 75
_PRIORITY_APPOSITION = 72
_PRIORITY_RECIPIENT = 70
_PRIORITY_SIGNATURE_BUSINESS = 60
_PRIORITY_BUSINESS_MENTION = 50
_PRIORITY_SIGNATURE = 40
_PRIORITY_PERSON = 30

_MAX_NAME_WORDS = 6

_BUSINESS_WORDS = _folded_set((
    "boulangerie", "pâtisserie", "chocolaterie", "confiserie", "épicerie", "supérette", "supermarché",
    "hypermarché", "boutique", "magasin", "café", "cafétéria", "coffee", "snack", "restaurant", "resto",
    "pizzeria", "burger", "grill", "traiteur", "fromagerie", "glacier", "sandwicherie", "crêperie", "tacos",
    "hanout", "7anout", "librairie", "papeterie", "pharmacie", "parapharmacie", "hôtel", "laiterie", "mahlaba",
    "boucherie", "poissonnerie", "bakery", "shop", "store", "market", "kitchen", "emballages", "emballage",
    "distribution", "industries", "industrie", "trading", "groupe", "group", "société", "sté", "ets",
    "établissements", "douceurs", "délices", "cuisine", "food", "foods", "maison", "atelier", "coopérative",
    "imprimerie", "quincaillerie", "droguerie", "bazar", "bistro", "brasserie", "rôtisserie", "crèmerie",
    "torréfaction", "sweets", "pastry", "deli", "superette", "minimarket", "supermarket", "grocery", "pharmacy",
    "hotel", "kiosque", "parfumerie", "bijouterie", "optique", "pressing", "salon", "cosmétiques", "cosmetics",
    "fleuriste", "florist", "biscuiterie", "chocolatier", "grossiste", "wholesale", "dépôt", "agence", "clinique",
    "cabinet", "riad", "kissaria", "qissaria", "souk", "mahal", "dukkan", "fast-food", "snack-bar", "drugstore",
    "concept-store", "crafts", "creations", "créations", "bistrot", "buvette", "cantine", "auberge", "fruiterie",
    "herboristerie", "torréfacteur", "maroquinerie", "caviste", "shack", "bar", "diner", "lounge",
    "eatery", "canteen", "bakehouse", "patisserie", "studio", "spa", "inn", "lodge", "hostel", "guesthouse",
    "gallery", "ferran", "farran", "kiosk", "charcuterie", "primeur", "association", "école", "fondation",
    "complexe", "résidence", "domaine", "ferme", "fournil",
    # darija en lettres latines
    "ma7alba", "mahalba", "m7alba", "mat3am", "mat3em", "9ahwa", "qahwa", "sidaliya", "saydaliya", "mkhbaza",
    "makhbaza", "7lawiyat", "7alwiyat",
    # écriture arabe
    "شركة", "مؤسسة", "مكتبة", "مطعم", "مقهى", "قهوة", "كافي", "سناك", "محل", "متجر", "حانوت", "دكان", "بوتيك",
    "حلويات", "محلبة", "مخبزة", "مخبز", "فرن", "صيدلية", "فندق", "ريستوران", "مجموعة", "معمل", "مصنع",
    "سوبرماركت", "مركز", "مقاولة", "تعاونية", "مطبعة", "بيتزيريا", "ممون", "سوق", "قيسارية", "كشك", "صالون",
    "عطارة", "مجزرة", "ملبنة", "رياض", "مقشدة", "بازار", "سوبيريت", "محمصة", "عشابة", "مصبنة", "ملحمة", "جمعية",
    "مدرسة", "عيادة", "وكالة", "مكتب", "مركب", "نادي", "كافيتيريا", "ماركت",
))
_LEGAL_FORMS = _folded_set(("sarl", "sarlau", "sa", "sas", "sasu", "snc", "suarl", "eurl", "llc", "ltd", "inc",
                            "gmbh", "ش.م.م"))
# Noms communs qui annoncent un nom propre sans en faire partie (« le magasin Mode Sabrine »).
_GENERIC_NOUNS = _folded_set(("magasin", "local", "store", "shop", "commerce", "enseigne", "société", "sté",
                              "entreprise", "company", "client", "cliente", "mahal"))
_DETERMINERS = _folded_set(("la", "le", "les", "l", "the", "notre", "nos", "our", "my", "mon", "ma", "mes",
                            "votre", "vos", "un", "une", "a", "an"))
_NAME_LINKS = _folded_set(("de", "du", "des", "d", "la", "le", "les", "el", "al", "il", "ben", "bent", "bin", "ibn",
                           "ait", "&", "and", "et", "of", "y", "di", "da", "-"))
_AFFILIATION_WORDS = _folded_set(("mn", "men", "min", "dyal", "dial", "d", "de", "du", "des", "from", "of",
                                  "chez", "at"))
_NAME_STOP_WORDS = _folded_set((
    "bonjour", "bonsoir", "salut", "salam", "slm", "merci", "cordialement", "svp", "stp", "thanks", "thank",
    "please", "pls", "regards", "hello", "hi", "hey", "dear", "monsieur", "madame", "mademoiselle", "messieurs",
    "mr", "mme", "mlle", "objet", "re", "ok", "okay", "oui", "non", "yes", "no", "je", "j", "nous", "vous", "il",
    "elle", "on", "we", "i", "you", "they", "ce", "cet", "cette", "ces", "pour", "for", "avec", "with", "sans",
    "without", "mais", "but", "commande", "order", "livraison", "delivery", "date", "quantité", "prix",
    "total", "bon", "next", "prochain", "prochaine", "demain", "tomorrow", "today", "inchallah", "ramadan",
    "aïd", "noël", "urgent", "important", "attention", "note", "ps", "nb", "cc", "tél", "fax", "email", "mail",
    "adresse", "address", "bghit", "bghina", "khassni", "3afak", "afak", "wach", "chokran", "choukran", "safi",
    "wakha", "khoya", "labas", "tecpap", "sachet", "sachets", "sac", "sacs", "kraft",
)) | _WEEKDAYS | frozenset(_MONTHS)
_JOB_TITLE_WORDS = _folded_set((
    "directeur", "directrice", "responsable", "service", "gérant", "gérante", "manager", "acheteur",
    "acheteuse", "assistant", "assistante", "comptable", "chef", "département", "purchasing", "buyer", "head",
    "ceo", "dg", "pdg", "commercial", "commerciale", "achats", "achat", "direction", "sales", "procurement",
    "owner", "propriétaire", "fondateur", "fondatrice",
))
_CITIES = _folded_set((
    "casablanca", "casa", "rabat", "rbat", "salé", "fès", "fes", "fez", "marrakech", "marrakesh", "tanger",
    "tangier", "tanja", "agadir", "meknès", "meknes", "oujda", "kénitra", "kenitra", "tétouan", "tetouan",
    "safi", "mohammedia", "el jadida", "jadida", "béni mellal", "beni mellal", "nador", "taza", "settat",
    "larache", "khouribga", "berrechid", "essaouira", "ifrane", "bouskoura", "témara", "temara", "laâyoune",
    "laayoune", "dakhla", "errachidia", "ouarzazate", "guelmim", "tiznit", "taroudant", "khemisset",
    "sidi kacem", "berkane", "inezgane", "azrou", "chefchaouen", "al hoceima", "zagora", "tinghir", "midelt",
    "khenifra", "azilal", "taourirt", "guercif", "ouazzane", "asilah", "martil", "fnideq", "mdiq", "skhirat",
    "bouznika", "azemmour", "sidi ifni", "tan-tan", "tantan", "smara", "boujdour", "taghazout", "mirleft", "erfoud",
    "rissani", "merzouga", "sefrou", "taounate", "saidia", "youssoufia", "benguerir", "ben guerir", "tiflet",
    "sidi slimane", "sidi bennour", "oued zem", "ait melloul", "biougra", "imouzzer", "moulay idriss",
))
_CITY_WORDS = frozenset(word for city in _CITIES for word in city.split())
_ARABIC_CITIES = _folded_set((
    "تطوان", "طنجة", "الدار", "البيضاء", "كازا", "الرباط", "رباط", "فاس", "مراكش", "أكادير", "اكادير", "مكناس",
    "وجدة", "القنيطرة", "قنيطرة", "سلا", "تمارة", "المحمدية", "محمدية", "الجديدة", "جديدة", "آسفي", "اسفي",
    "الناظور", "ناظور", "تازة", "سطات", "العرائش", "خريبكة", "برشيد", "الصويرة", "إفران", "افران", "العيون",
    "الداخلة", "الراشيدية", "ورزازات", "كلميم", "تزنيت", "تارودانت", "الخميسات", "بوسكورة", "زاكورة", "تنغير",
    "ميدلت", "خنيفرة", "أزيلال", "تاوريرت", "جرسيف", "بركان", "وزان", "شفشاون", "الحسيمة", "أصيلة", "مرتيل",
    "الفنيدق", "المضيق", "الصخيرات", "بوزنيقة", "أزمور", "آزمور", "طانطان", "السمارة", "بوجدور", "تغازوت", "أرفود",
    "الريصاني", "مرزوكة", "صفرو", "تاونات", "السعيدية", "اليوسفية", "بنجرير", "تيفلت", "إنزكان", "انزكان", "أزرو",
    "خميسات", "الرشيدية", "تيزنيت",
))

_NAME_TOKEN_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*|&|\S")
_ELIDED_NAME_RE = re.compile(r"^[dDlL]'(\w)")

_LABEL_LINE_RE = re.compile(
    r"(?im)^[ \t>*-]*(?:client|cliente|nom du client|nom client|raison sociale|soci[ée]t[ée]|entreprise|"
    r"customer|company|العميل|الزبون|الشركة|اسم الزبون|اسم العميل)[ \t]*:[ \t]*(?P<value>[^\n|]+)"
)
# Paire clé-valeur au milieu d'une ligne : export JSON (« "client": "Superette Select" »), ligne
# de bon de commande à champs séparés par « | » (« Qty: 6000 pcs | Customer: Green Leaf »).
_KEY_VALUE_RE = re.compile(
    r"""(?:^|(?<=[{,;|(\[]))[ \t]*["']?(?P<key>[^\W\d_][^"':=\n|{},;]{0,30}?)["']?[ \t]*[:=][ \t]*"""
    r"""(?:"(?P<quoted>[^"\n]*)"|'(?P<single>[^'\n]*)'|(?P<value>(?:[^,;|}\n]|,(?=\d{3}(?!\d)))+))""",
    re.MULTILINE,
)
_CLIENT_KEYS = _folded_set(("client", "cliente", "nom du client", "nom client", "nom_client", "raison sociale",
                            "société", "entreprise", "customer", "customer name", "customer_name", "company",
                            "company name", "magasin", "enseigne", "العميل", "الزبون", "الشركة", "اسم الزبون",
                            "اسم العميل"))
_QUANTITY_KEYS = _folded_set(("quantité", "quantités", "quantite", "qte", "qté", "qty", "quantity", "quantities",
                              "nombre", "nbre", "الكمية", "كمية", "kmiya", "lkmiya"))
_UNIT_KEYS = _folded_set(("unité", "unite", "unités", "unit", "units", "uom", "الوحدة", "وحدة"))
_INTRO_RE = re.compile(
    r"(?i)(?<![\w'])(?:ana|je suis|nous sommes|c'est|c est|c|ici|this is|i am|i'm|we are|it's|it is|m3ak|m3akom|"
    r"m3akoum|m3akum)\s+"
)
# Fonction de la personne dans le commerce : « manager of Blue Door Café », « gérant de la Brasserie
# du Port », « propriétaire du Snack Atlas », « moul Snack Najma » (darija : le patron de).
_ROLE_RE = re.compile(
    r"(?i)(?<![\w'])(?:"
    r"(?:manager|owner|co-owner|gérant|gérante|gerant|gerante|propriétaire|proprietaire|directeur|directrice|"
    r"responsable|patron|patronne|fondateur|fondatrice|founder)\s+(?:of|at|de la|de l'|du|des|de|d'|chez|dyal|dial)\s*"
    r"|(?:moul|mol|mmoulat|moulat)\s+(?:(?:dyal|dial)\s*)?"
    r")"
)
# « Karim here from Sunset Grill », « Fatima hna mn Snack Lmima » : la personne puis son commerce.
_HERE_FROM_RE = re.compile(r"(?i)(?<![\w'])(?:here|hna)\s*,?\s+(?:from|at|mn|men|dyal|dial)\s+")
_RECIPIENT_RE = re.compile(
    r"(?i)(?<![\w'])(?P<word>pour|for|l|to|chez|dyal|dial|à|a(?=\s+(?:la|le)\s|\s+l'))\s+"
)
# Commande passée au nom d'un client : « de la part de », « pour le compte de », « au nom de »,
# « on behalf of », darija « mn jiht » (de la part de).
_BEHALF_RE = re.compile(
    r"(?i)(?<![\w'])(?:de la part|pour le compte|au nom)\s+(?:de|du|des|d')\s*|"
    r"(?<![\w'])(?:on behalf of|mn jiht|men jiht|mn jihat|men jihat|mn 3and|men 3and)\s+"
)
_COMPANY_RE = re.compile(r"(?i)(?<![\w'])(?:soci[ée]t[ée]|st[ée]|ets|[ée]tablissements|entreprise|company)\s+")
_NAMED_RE = re.compile(
    r"(?i)(?<![\w'])(?:smitou|smito|smitha|smiytou|smiyto|smiytha|qui s'appelle|qui se nomme|nomm[ée]e?|"
    r"appel[ée]e?|named|called)\s+"
)
_CODE_VALUE_RE = re.compile(r"[A-Za-z]{0,4}[-_]?\d[\w-]*")
_CLOSING_RE = re.compile(
    r"(?:bien |tres |)cordialement|(?:sinceres |meilleures |)salutations|bien a vous|"
    r"merci(?: beaucoup| d'avance| par avance| bien)?|bonne (?:journee|soiree|reception)|"
    r"(?:best |kind |warm |)regards|best|thanks(?: in advance| a lot)?|thank you|cheers|(?:yours |)sincerely|"
    r"مع (?:خالص )?التحية|تحياتي|تحياتنا|شكرا(?: لكم)?|وشكرا|مع الشكر"
)
_CONTACT_RE = re.compile(r"@|https?://|www\.|\d{6,}|(?:\d[\s.]?){8,}")
_PART_SPLIT_RE = re.compile(r"\s+[-|•]\s+|,\s*")

_AR_TOKEN_RE = re.compile(r"[^\W_]+|\n|[^\w\s]")
# Présentation : « أنا » (je), « نحن » / « حنا » (nous), « معكم » (vous êtes avec : formule d'appel).
_AR_INTRO = _folded_set(("أنا", "انا", "نحن", "حنا", "معكم", "معك", "معاك", "معاكم"))
_AR_FROM = _folded_set(("من", "ديال"))
_AR_NAMED = _folded_set(("سميتو", "سميته", "سميتها", "اسمه", "اسمها", "اسمو", "سميت"))
_AR_POSSESSIVE_WORDS = _folded_set(("ديالي", "ديالنا", "ديالو", "ديالها", "ديالهم", "ديالكم", "تاعي", "تاعنا"))
_AR_POSSESSIVE_SUFFIXES = ("نا", "ها", "كم", "هم", "ي", "ه")
_AR_STOP_WORDS = _folded_set((
    "اه", "آه", "ايه", "إيه", "ايوا", "و", "ويلا", "اذا", "إذا", "الا", "إلا", "عافاك", "عفاك", "شكرا", "مع", "في",
    "ف", "قبل", "بعد", "باش", "حتى", "ديال", "ديالي", "ديالنا", "ليا", "لينا", "بغيت", "بغينا", "خاصني", "خصني",
    "خاصنا", "نطلب", "نرغب", "نريد", "أريد", "نود", "نحتاج", "نحتاجو", "نبغي", "نبغيو", "محتاج", "محتاجين",
    "ابغي", "يطلب", "تطلب", "يريد", "تريد", "يحتاج", "تحتاج", "يبغي", "تبغي", "كيبغي", "كيطلب",
    "هذا", "هاد", "هادي", "اليوم", "غدا", "غدوة", "نهار", "من", "على", "عند", "إلى",
    "الى", "هو", "هي", "أنا", "انا", "نحن", "عليكم", "عليك", "مرحبا", "صيفط", "عطيني", "الله", "إن", "ان",
    "شاء", "إنشاء", "غادي", "غادا", "نقول", "ليك", "ليكم", "كاين", "كاينة", "اللي", "الي", "هادشي", "داكشي",
    "دابا", "بزاف", "شوية", "كذلك", "أيضا", "يرجى", "الرجاء", "او", "أو", "ثم", "لكن", "ولكن", "بلا", "بدون",
    "نرجو", "منكم", "عن", "طرف",
))
_AR_BEHALF = (("نيابة", "عن"), ("من", "طرف"), ("باسم",), ("لحساب",), ("لفائدة",), ("لصالح",))
_AR_OF = fold("ديال")


def _is_name_word(word: str) -> bool:
    """Mot qui peut faire partie d'un nom propre : initiale majuscule ou « d'X » / « l'X »."""
    if not word or not word[0].isalpha():
        return False
    if word[0].isupper():
        return True
    elided = _ELIDED_NAME_RE.match(word)
    return bool(elided and elided.group(1).isupper())


def _bare(word: str) -> str:
    """Mot sans article élidé en minuscule (« l'épicerie » → « épicerie »)."""
    return word[2:] if _ELIDED_NAME_RE.match(word) and word[0].islower() else word


def _accept_name_word(word: str, *, first: bool) -> bool:
    folded = fold(word)
    if folded in _NAME_STOP_WORDS:
        return False
    return _is_name_word(word) or (first and fold(_bare(word)) in _BUSINESS_WORDS)


def _is_name_link(tokens: Sequence[re.Match[str]], index: int) -> bool:
    """Particule ou « & » entre deux mots d'un nom (« Douceurs de Fès », « Wrap & Go », « Café de la Poste »).

    Une suite de particules (« de la », « de l' ») est admise devant le mot à majuscule.
    """
    if fold(tokens[index].group()) not in _NAME_LINKS or index + 1 >= len(tokens):
        return False
    following_index = index + 1
    while (following_index + 1 < len(tokens) and following_index - index < 2
           and fold(tokens[following_index].group()) in _NAME_LINKS
           and fold(tokens[following_index].group()) not in _NAME_STOP_WORDS):
        following_index += 1
    following = tokens[following_index].group()
    if not _is_name_word(following) or fold(following) in _NAME_STOP_WORDS:
        return False
    if tokens[index].group() == "-":
        return tokens[index - 1].end() == tokens[index].start() and tokens[index].end() == tokens[index + 1].start()
    return True


_BRAND_ARTICLES = _folded_set(("the", "le", "la", "les"))


def _starts_brand_name(tokens: Sequence[re.Match[str]], index: int) -> bool:
    """Vrai quand le premier mot est un article écrit avec une majuscule devant un mot à majuscule.

    « The Argan Tree Shop » et « Le Fournil » sont des enseignes dont l'article fait partie ; « la
    boulangerie Nour » et « the Green Leaf » sont des phrases où l'article ne fait pas partie du nom.
    """
    if index != 0 or index + 1 >= len(tokens):
        return False
    word = tokens[index].group()
    following = tokens[index + 1].group()
    return (fold(word) in _BRAND_ARTICLES and word[0].isupper() and word[1:].islower()
            and following[0].isupper() and fold(following) not in _NAME_STOP_WORDS)


def _skip_preamble(tokens: Sequence[re.Match[str]], *, generic: bool) -> int:
    """Indice du premier mot après les déterminants (et les noms communs génériques en minuscules)."""
    index = 0
    while index < len(tokens) and index < 3:
        word = tokens[index].group()
        folded = fold(word)
        if _starts_brand_name(tokens, index):
            break
        if folded in _DETERMINERS or (generic and folded in _GENERIC_NOUNS and not word[0].isupper()):
            index += 1
            continue
        break
    return index


_CITY_PREPOSITIONS = frozenset({"de", "du", "a", "au", "in", "at", "f", "fi", "b", "en"})


def _strip_trailing_city(name: str) -> str:
    """Retire la ville introduite par une préposition (« Boulangerie Atlas de Fès » → « Boulangerie Atlas »)."""
    words = name.split()
    for index in range(len(words) - 1, 1, -1):
        if fold(words[index]) in _CITY_PREPOSITIONS and fold(" ".join(words[index + 1:])) in _CITIES:
            return " ".join(words[:index])
    return name


def _qualifier_end(tokens: Sequence[re.Match[str]], index: int) -> int | None:
    """Fin d'un qualificatif en minuscules entre le mot de commerce et le nom (« Épicerie fine Zitoun »).

    Un ou deux mots en minuscules (« fine », « artisanale », « orientale ») sont gardés quand un
    mot à majuscule les suit ; sinon le nom s'arrête au mot de commerce.
    """
    end = index
    while end < len(tokens) and end - index < 2:
        word = tokens[end].group()
        folded = fold(word)
        if not word.isalpha() or _is_name_word(word) or folded in _NAME_STOP_WORDS or folded in _NAME_LINKS \
                or folded in _DETERMINERS or folded in _CITY_WORDS:
            break
        end += 1
    if end == index or end >= len(tokens):
        return None
    following = tokens[end].group()
    if _is_name_word(following) and fold(following) not in _NAME_STOP_WORDS:
        return end
    return None


def _capture_name(segment: str, *, skip_generic: bool = True) -> str | None:
    """Nom propre au début de ``segment`` : mots à majuscule et particules, au plus six mots.

    Après un mot de commerce, un qualificatif en minuscules suivi d'un nom propre est gardé
    (« Épicerie fine Zitoun »).
    """
    tokens = list(_NAME_TOKEN_RE.finditer(segment))
    index = _skip_preamble(tokens, generic=skip_generic)
    chosen: list[re.Match[str]] = []
    while index < len(tokens) and len(chosen) < _MAX_NAME_WORDS:
        word = tokens[index].group()
        if _accept_name_word(word, first=not chosen) or (chosen and _is_name_link(tokens, index)):
            chosen.append(tokens[index])
            index += 1
            continue
        qualifier = _qualifier_end(tokens, index) if chosen and fold(_bare(chosen[-1].group())) in _BUSINESS_WORDS \
            else None
        if qualifier is not None:
            chosen.extend(tokens[index:qualifier])
            index = qualifier
            continue
        break
    while chosen and fold(chosen[-1].group()) in _NAME_LINKS:
        chosen.pop()
    if not chosen:
        return None
    start = chosen[0].start()
    # Article élidé en minuscule (« l'Hôtel Atlas », « l'épicerie Nour ») : il ne fait pas partie du nom ;
    # en majuscule (« L'Atelier du Pain »), il appartient à l'enseigne.
    if _bare(chosen[0].group()) != chosen[0].group():
        start += 2
    return _strip_trailing_city(" ".join(segment[start:chosen[-1].end()].split()))


def _valid_client_name(name: str | None) -> bool:
    """Écarte les noms vides, génériques, réduits à une ville ou désignant TECPAP lui-même."""
    if not name or not 2 <= len(name) <= 120:
        return False
    folded = fold(name)
    words = _WORD_RE.findall(folded)
    if not words or "tecpap" in folded:
        return False
    if all(word in _NAME_STOP_WORDS or word in _NAME_LINKS or word in _DETERMINERS for word in words):
        return False
    if folded in _CITIES or all(word in _CITY_WORDS or word in _ARABIC_CITIES for word in words):
        return False
    return not (len(words) == 1 and (words[0] in _BUSINESS_WORDS or words[0] in _GENERIC_NOUNS))


def _tidy_name(name: str) -> str:
    return " ".join(name.split()).strip(" ,.;:-'\"")


def _candidate(name: str | None, priority: int, position: int) -> list[_ClientCandidate]:
    if name and _valid_client_name(_tidy_name(name)):
        return [_ClientCandidate(_tidy_name(name), priority, position)]
    return []


def _line_rest(text: str, start: int, limit: int = 150) -> str:
    end = text.find("\n", start)
    return text[start:end if end != -1 else len(text)][:limit]


def _key_values(text: str, keys: frozenset[str]) -> Iterator[tuple[int, str]]:
    """Valeurs des paires « clé : valeur » dont la clé appartient à ``keys`` (JSON, champs séparés par « | »)."""
    for match in _KEY_VALUE_RE.finditer(text):
        if " ".join(fold(match.group("key")).replace("_", " ").split()) not in keys:
            continue
        value = next((group for group in match.group("quoted", "single", "value") if group is not None), "")
        if value.strip():
            yield match.start(), value


def _label_candidates(text: str) -> list[_ClientCandidate]:
    """Libellé « Client : X » (bon de commande structuré, export JSON) ; un code client (« CL00231 ») est écarté."""
    candidates = []
    labelled = [(match.start(), match.group("value")) for match in _LABEL_LINE_RE.finditer(text)]
    for position, raw in [*labelled, *_key_values(text, _CLIENT_KEYS)]:
        value = _tidy_name(raw)
        if value and not _CODE_VALUE_RE.fullmatch(value):
            candidates += _candidate(value[:NAME_MAX], _PRIORITY_LABEL, position)
    return candidates


def _person_end(tokens: Sequence[re.Match[str]], index: int) -> int:
    end = index
    while end < len(tokens) and end - index < 3:
        word = tokens[end].group()
        if not _is_name_word(word) or fold(word) in _NAME_STOP_WORDS or fold(word) in _BUSINESS_WORDS:
            break
        end += 1
    return end


def _intro_candidates(text: str) -> list[_ClientCandidate]:
    """« ana X mn Y », « c'est X du Y », « this is X from Y » : Y est l'entreprise, à défaut X la personne."""
    candidates: list[_ClientCandidate] = []
    for match in _INTRO_RE.finditer(text):
        segment = _line_rest(text, match.end())
        tokens = list(_NAME_TOKEN_RE.finditer(segment))
        index = _skip_preamble(tokens, generic=False)
        if index >= len(tokens):
            continue
        first = fold(_bare(tokens[index].group()))
        if first in _AFFILIATION_WORDS:
            candidates += _candidate(_capture_name(segment[tokens[index].end():]), _PRIORITY_AFFILIATION,
                                     match.start())
            continue
        if first in _BUSINESS_WORDS:
            candidates += _candidate(_capture_name(segment[tokens[index].start():], skip_generic=False),
                                     _PRIORITY_BUSINESS_INTRO, match.start())
            continue
        end = _person_end(tokens, index)
        if end == index:
            continue
        if end < len(tokens) and tokens[end].group()[0].isupper() and fold(tokens[end].group()) in _BUSINESS_WORDS:
            business = segment[tokens[index].start():tokens[end].end()]  # « Ocean View Hotel »
            candidates += _candidate(business, _PRIORITY_BUSINESS_INTRO, match.start())
            continue
        if end < len(tokens) and fold(tokens[end].group()) in _AFFILIATION_WORDS:
            affiliation = _candidate(_capture_name(segment[tokens[end].end():]), _PRIORITY_AFFILIATION,
                                     match.start())
            if affiliation:
                candidates += affiliation
                continue
        person = segment[tokens[index].start():tokens[end - 1].end()]
        candidates += _candidate(person, _PRIORITY_PERSON, match.start())
    return candidates


# Début de phrase, avec une éventuelle formule d'appel : la personne qui écrit peut se présenter par son
# prénom suivi de son commerce (« Amina from Olive & Co here », « Samir de la Pizzeria Roma »).
_SENTENCE_START_RE = re.compile(
    r"(?im)(?:^|(?<=[.!?;:]))[ 	]*(?:(?:hi|hello|hey|bonjour|bonsoir|salut|salam|slt|slm|"
    r"good (?:morning|afternoon|evening))[ 	,!.]*)?"
)
_SENTENCE_AFFILIATIONS = _folded_set(("from", "of", "de", "du", "chez", "mn", "men", "dyal", "dial"))
# « from », « mn », « chez » désignent sans ambiguïté le commerce ; « de », « du », « of » exigent un mot de commerce.
_EXPLICIT_AFFILIATIONS = _folded_set(("from", "mn", "men", "chez"))


def _sentence_affiliation_candidates(text: str) -> list[_ClientCandidate]:
    """« Amina from Olive & Co here », « Samir de la Pizzeria Roma » : prénom puis commerce, en début de phrase."""
    candidates: list[_ClientCandidate] = []
    for match in _SENTENCE_START_RE.finditer(text):
        segment = _line_rest(text, match.end())
        tokens = list(_NAME_TOKEN_RE.finditer(segment))
        end = _person_end(tokens, 0)
        if end == 0 or end >= len(tokens) or fold(tokens[end].group()) not in _SENTENCE_AFFILIATIONS:
            continue
        name = _capture_name(segment[tokens[end].end():])
        if name and (fold(tokens[end].group()) in _EXPLICIT_AFFILIATIONS or _has_business_word(name)):
            candidates += _candidate(name, _PRIORITY_BUSINESS_INTRO, match.start())
    return candidates


def _role_candidates(text: str) -> list[_ClientCandidate]:
    """« manager of X », « gérant de la X », « moul X » : le commerce dont la personne a la charge."""
    candidates: list[_ClientCandidate] = []
    for match in _ROLE_RE.finditer(text):
        candidates += _candidate(_capture_name(_line_rest(text, match.end()), skip_generic=False),
                                 _PRIORITY_AFFILIATION, match.start())
    return candidates


# « Order from X », « commande de la X », « طلبية من X » : la commande vient du client nommé.
_ORDER_FROM_RE = re.compile(
    r"(?i)(?<![\w'])(?:order|purchase order|commande|bon de commande)\s+(?:from|de la part de|de la|de l'|du|de|d')\s*"
)


def _order_from_candidates(text: str) -> list[_ClientCandidate]:
    """« Order from Sahara Tea House », « Commande de la Boulangerie Nour » (nom propre qui suit)."""
    candidates: list[_ClientCandidate] = []
    for match in _ORDER_FROM_RE.finditer(text):
        candidates += _candidate(_capture_name(_line_rest(text, match.end()), skip_generic=False),
                                 _PRIORITY_RECIPIENT, match.start())
    return candidates


def _here_from_candidates(text: str) -> list[_ClientCandidate]:
    """« Karim here from Sunset Grill », « Fatima hna mn Snack Lmima » : le commerce qui suit."""
    candidates: list[_ClientCandidate] = []
    for match in _HERE_FROM_RE.finditer(text):
        candidates += _candidate(_capture_name(_line_rest(text, match.end())), _PRIORITY_AFFILIATION, match.start())
    return candidates


def _recipient_candidates(text: str) -> list[_ClientCandidate]:
    """« pour X », « for X », « l X » (darija), « to X », « à la X » (avec un mot de commerce)."""
    candidates: list[_ClientCandidate] = []
    for match in _RECIPIENT_RE.finditer(text):
        name = _capture_name(_line_rest(text, match.end()))
        if name and fold(match.group("word")) in _BUSINESS_ONLY_LINKS and not _has_business_word(name):
            continue
        candidates += _candidate(name, _PRIORITY_RECIPIENT, match.start())
    return candidates


# Prépositions trop fréquentes pour introduire seules un client : le nom doit contenir un mot de
# commerce (« à la Fromagerie X », « sac dyal Bazar Nour »).
_BUSINESS_ONLY_LINKS = frozenset({"a", "dyal", "dial"})


def _behalf_candidates(text: str) -> list[_ClientCandidate]:
    """« de la part de X », « pour le compte de X », « au nom de X », « on behalf of X », « mn jiht X »."""
    candidates: list[_ClientCandidate] = []
    for match in _BEHALF_RE.finditer(text):
        candidates += _candidate(_capture_name(_line_rest(text, match.end())), _PRIORITY_BEHALF, match.start())
    return candidates


_APPOSITION_SEPARATORS_RE = re.compile(r"\s*(?:,|:|\s[–—-])\s+")


def _apposition_candidates(text: str) -> list[_ClientCandidate]:
    """Nom en apposition d'un type de commerce : « for my gift shop, Atlas Treasures », « une boutique de
    vêtements pour femmes, Lalla Mode ».

    Le type de commerce est un nom commun en minuscules placé dans les six mots qui précèdent la
    virgule ; le nom qui suit commence par une majuscule. Le mot de commerce n'est pas repris.
    """
    candidates: list[_ClientCandidate] = []
    for separator in _APPOSITION_SEPARATORS_RE.finditer(text):
        line_start = text.rfind("\n", 0, separator.start()) + 1
        before = _WORD_TOKEN_RE.findall(text[line_start:separator.start()])[-6:]
        if not before or not any(word.islower() and fold(word) in _BUSINESS_WORDS for word in before):
            continue
        if any(_is_name_word(word) and fold(word) not in _NAME_STOP_WORDS for word in before[-3:]):
            continue
        rest = _line_rest(text, separator.end())
        if rest[:1].isupper():
            candidates += _candidate(_capture_name(rest, skip_generic=False), _PRIORITY_APPOSITION,
                                     separator.start())
    return candidates


_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")
# Déterminants ou prépositions qui font d'un mot de commerce en minuscules le début d'un nom
# (« la boutique Dar Lhena », « chez pharmacie Al Amal »).
_MENTION_INTRODUCERS = _DETERMINERS | _folded_set(("chez", "at", "de", "du", "d", "au", "aux"))


def _business_mention_candidates(text: str) -> list[_ClientCandidate]:
    """Mot de commerce suivi d'un nom propre, n'importe où dans le message (« …, 600 7ba, Bazar Atlas »).

    Le mot de commerce doit porter une majuscule ou suivre un déterminant (« la boutique Dar
    Lhena ») : en minuscules après un nom de produit (« sachet tacos »), il désigne un usage.
    """
    candidates: list[_ClientCandidate] = []
    tokens = list(_NAME_TOKEN_RE.finditer(text))
    for index, token in enumerate(tokens):
        word = token.group()
        if fold(word) not in _BUSINESS_WORDS:
            continue
        introduced = index and fold(tokens[index - 1].group()) in _MENTION_INTRODUCERS
        if not (word[0].isupper() or introduced):
            continue
        name = _capture_name(_line_rest(text, token.start()), skip_generic=False)
        if name and len(name.split()) >= 2:
            candidates += _candidate(name, _PRIORITY_BUSINESS_MENTION, token.start())
    return candidates


def _company_candidates(text: str) -> list[_ClientCandidate]:
    """« société X », « Sté X » ; le mot « Société » écrit avec une majuscule fait partie du nom."""
    candidates: list[_ClientCandidate] = []
    for match in _COMPANY_RE.finditer(text):
        name = _capture_name(_line_rest(text, match.end()), skip_generic=False)
        if name and match.group()[0].isupper():
            name = f"{match.group().strip()} {name}"
        candidates += _candidate(name, _PRIORITY_COMPANY, match.start())
    return candidates


def _named_candidates(text: str) -> list[_ClientCandidate]:
    """« smitou X » (darija : il s'appelle X), « qui s'appelle X », « named X »."""
    candidates: list[_ClientCandidate] = []
    for match in _NAMED_RE.finditer(text):
        candidates += _candidate(_capture_name(_line_rest(text, match.end()), skip_generic=False),
                                 _PRIORITY_NAMED, match.start())
    return candidates


def _has_business_word(name: str) -> bool:
    words = _WORD_RE.findall(fold(name))
    return any(word in _BUSINESS_WORDS or word in _LEGAL_FORMS for word in words) or any(
        _arabic_business(word)[0] for word in words if _has_arabic(word)
    )


# ---- écriture arabe

@dataclass(frozen=True)
class _ArabicToken:
    original: str
    folded: str
    position: int


def _arabic_tokens(text: str) -> list[_ArabicToken]:
    return [_ArabicToken(match.group(), fold(match.group()), match.start()) for match in _AR_TOKEN_RE.finditer(text)]


def _strip_arabic_prefix(word: str, prefixes: Sequence[str]) -> tuple[str, int]:
    for prefix in prefixes:
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            return word[len(prefix):], len(prefix)
    return word, 0


def _arabic_business(word: str) -> tuple[bool, bool]:
    """(mot de commerce, avec possessif) pour un mot en forme de comparaison (« متجرنا » → (True, True))."""
    for candidate in (word, _strip_arabic_prefix(word, ("ال",))[0]):
        if candidate in _BUSINESS_WORDS:
            return True, False
        for suffix in _AR_POSSESSIVE_SUFFIXES:
            if candidate.endswith(suffix) and candidate[: -len(suffix)] in _BUSINESS_WORDS:
                return True, True
    return False, False


_ARABIC_CITY_PREFIXES = ("بال", "فال", "ب", "ف", "ل")
# Prépositions séparées qui introduisent la ville : « في مراكش », « ف مراكش », « من فاس ».
_ARABIC_CITY_PREPOSITIONS = _folded_set(("في", "ف", "ب", "من", "بمدينة", "مدينة"))


def _is_arabic_city(word: str) -> bool:
    return _city_with_preposition(word) or word in _ARABIC_CITIES


def _city_with_preposition(word: str) -> bool:
    """Ville précédée d'une préposition attachée (« فمراكش », « بالرباط », « لطنجة »)."""
    for prefix in _ARABIC_CITY_PREFIXES:
        stripped, removed = _strip_arabic_prefix(word, (prefix,))
        if removed and (stripped in _ARABIC_CITIES or "ال" + stripped in _ARABIC_CITIES):
            return True
    return False


def _without_trailing_arabic_city(tokens: Sequence[_ArabicToken]) -> list[_ArabicToken]:
    """Retire la ville finale introduite par une préposition (« صيدلية الشفاء فمراكش », « … في فاس »)."""
    words = list(tokens)
    if len(words) >= 2 and _city_with_preposition(words[-1].folded):
        return words[:-1]
    if len(words) >= 3 and words[-2].folded in _ARABIC_CITY_PREPOSITIONS and words[-1].folded in _ARABIC_CITIES:
        return words[:-2]
    return words


def _drop_letters(word: str, count: int) -> str:
    """Retire les ``count`` premières lettres d'un mot d'origine (et leurs signes diacritiques)."""
    index = 0
    while index < len(word) and count:
        index += 1
        while index < len(word) and unicodedata.category(word[index]) == "Mn":
            index += 1
        count -= 1
    return word[index:]


_AR_PEACE = fold("السلام")
_AR_GREETING_ANSWER = _folded_set(("عليكم", "عليك"))


def _is_greeting(tokens: Sequence[_ArabicToken], index: int) -> bool:
    """Vrai pour « السلام » suivi de « عليكم » (salutation) ; « السلام » seul peut être un nom de commerce."""
    return (tokens[index].folded == _AR_PEACE and index + 1 < len(tokens)
            and tokens[index + 1].folded in _AR_GREETING_ANSWER)


def _is_arabic_stop(folded: str) -> bool:
    """Mot d'arrêt arabe, avec ou sans conjonction collée (« ونريد » = « و » + « نريد »)."""
    return folded in _AR_STOP_WORDS or (folded[:1] in ("و", "ف") and folded[1:] in _AR_STOP_WORDS)


def _arabic_capture(tokens: Sequence[_ArabicToken], start: int, first: str | None = None) -> str | None:
    """Nom en écriture arabe à partir de ``tokens[start]`` : au plus quatre mots, jusqu'à un mot d'arrêt."""
    words: list[str] = []
    index = start
    while index < len(tokens) and len(words) < 4:
        token = tokens[index]
        if not _has_arabic(token.folded) or not token.folded.isalnum() or _is_arabic_stop(token.folded):
            break
        if words and _is_arabic_city(token.folded):
            break
        if _is_greeting(tokens, index) and (len(words) != 1 or index == start):
            break  # « السلام عليكم » : salutation, et non un nom comme « مطعم السلام »
        words.append(first if first is not None and index == start else token.original)
        index += 1
    return " ".join(words) or None


def _arabic_candidates(text: str) -> list[_ClientCandidate]:
    if not _has_arabic(text):
        return []
    tokens = _arabic_tokens(text)
    candidates: list[_ClientCandidate] = []
    for index, token in enumerate(tokens):
        if not _has_arabic(token.folded):
            continue
        behalf = _arabic_behalf_end(tokens, index)
        if behalf is not None:
            candidates += _candidate(_arabic_capture(tokens, behalf), _PRIORITY_BEHALF, token.position)
        elif token.folded in _AR_INTRO:
            if index == 0 or tokens[index - 1].folded not in _AR_INTRO:  # « أنا أنا » : hésitation
                candidates += _arabic_intro(tokens, index)
        elif token.folded in _AR_NAMED:
            candidates += _candidate(_arabic_capture(tokens, index + 1), _PRIORITY_NAMED, token.position)
        elif token.folded == _AR_OF:
            candidates += _arabic_named_business(tokens, index + 1, _PRIORITY_RECIPIENT, token.position)
        else:
            candidates += _arabic_recipient(tokens, index)
            candidates += _arabic_named_business(tokens, index, _PRIORITY_BUSINESS_MENTION, token.position)
    return candidates


def _arabic_behalf_end(tokens: Sequence[_ArabicToken], index: int) -> int | None:
    """Indice du nom qui suit « نيابة عن », « من طرف », « باسم » (au nom de, de la part de), ou ``None``."""
    for words in _AR_BEHALF:
        folded = [fold(word) for word in words]
        if all(index + offset < len(tokens) and tokens[index + offset].folded == word
               for offset, word in enumerate(folded)):
            return index + len(folded)
    return None


def _arabic_named_business(tokens: Sequence[_ArabicToken], index: int, priority: int,
                           position: int) -> list[_ClientCandidate]:
    """Commerce nommé : mot de commerce sans article suivi de son nom (« صيدلية الشفاء », « بوتيك نور »).

    Avec l'article (« الحلويات », « المحل »), le mot de commerce est un nom commun et non le début
    d'un nom de client.
    """
    if index >= len(tokens):
        return []
    token = tokens[index]
    if token.folded.startswith("ال") or not _arabic_business(token.folded)[0] or _arabic_business(token.folded)[1]:
        return []
    following = tokens[index + 1] if index + 1 < len(tokens) else None
    if following is None or not _has_arabic(following.folded) or following.folded in _AR_STOP_WORDS \
            or _is_arabic_city(following.folded) or following.folded in _AR_POSSESSIVE_WORDS:
        return []
    return _candidate(_arabic_capture(tokens, index), priority, position)


def _arabic_intro(tokens: Sequence[_ArabicToken], index: int) -> list[_ClientCandidate]:
    """« أنا من Y », « أنا X من Y » : Y est l'entreprise ; « أنا X » seul donne la personne."""
    position = tokens[index].position
    following = tokens[index + 1] if index + 1 < len(tokens) else None
    if following is None:
        return []
    if following.folded in _AR_FROM:
        return _candidate(_arabic_capture(tokens, index + 2), _PRIORITY_AFFILIATION, position)
    if _arabic_business(following.folded)[0]:
        return _candidate(_arabic_capture(tokens, index + 1), _PRIORITY_BUSINESS_INTRO, position)
    if not _has_arabic(following.folded) or following.folded in _AR_STOP_WORDS:
        return []
    # Prénom simple ou composé (« عبد الرحيم ») avant « من » : au plus trois mots.
    for offset in range(2, 5):
        after = tokens[index + offset] if index + offset < len(tokens) else None
        if after is None or not _has_arabic(after.folded) or after.folded in _AR_STOP_WORDS and \
                after.folded not in _AR_FROM:
            break
        if after.folded in _AR_FROM:
            found = _candidate(_arabic_capture(tokens, index + offset + 1), _PRIORITY_AFFILIATION, position)
            if found:
                return found
            break
    person = following.original
    if following.folded == fold("عبد") and index + 2 < len(tokens) and _has_arabic(tokens[index + 2].folded):
        person = f"{following.original} {tokens[index + 2].original}"
    return _candidate(person, _PRIORITY_PERSON, position)


def _arabic_recipient(tokens: Sequence[_ArabicToken], index: int) -> list[_ClientCandidate]:
    """« لسناك الزيتونة », « لمطعم الواحة » (pour le …) ; « لمتجرنا مكتبة القلم » (pour notre magasin …)."""
    token = tokens[index]
    base, removed = _strip_arabic_prefix(token.folded, ("لل", "ل"))
    if not removed:
        return []
    is_business, possessive = _arabic_business(base)
    if not is_business:
        return []
    following = index + 1
    while following < len(tokens) and tokens[following].folded in _AR_POSSESSIVE_WORDS:
        following += 1
        possessive = True
    if possessive:
        if following < len(tokens) and _arabic_business(tokens[following].folded)[0]:
            return _candidate(_arabic_capture(tokens, following), _PRIORITY_RECIPIENT, token.position)
        return []
    first = _drop_letters(token.original, removed)
    return _candidate(_arabic_capture(tokens, index, first=first), _PRIORITY_RECIPIENT, token.position)


# ---- signature

def _is_closing(line: str) -> bool:
    return bool(_CLOSING_RE.fullmatch(fold(line).strip(" ,.!;:")))


def _trailing_lines(lines: Sequence[tuple[int, str]]) -> list[tuple[int, str]]:
    """Dernières lignes courtes d'un message sans formule de politesse (signature probable)."""
    block: list[tuple[int, str]] = []
    for offset, line in reversed(lines[1:]):
        if len(line.split()) > _MAX_NAME_WORDS or line.endswith((":", "?", "؟")) or len(block) == 3:
            break
        block.insert(0, (offset, line))
    return block


def _supplier_side(line: str) -> bool:
    """Ligne qui désigne TECPAP (nom, adresse email ou site) : signature du fournisseur, pas du client."""
    return "tecpap" in fold(line)


def _latin_name_like(part: str) -> bool:
    tokens = list(_NAME_TOKEN_RE.finditer(part))
    if not tokens or len(tokens) > _MAX_NAME_WORDS + 2:
        return False
    qualifier_until = -1
    for index, token in enumerate(tokens):
        word = token.group()
        folded = fold(word)
        if index < qualifier_until:
            continue
        if folded in _NAME_STOP_WORDS and folded not in _LEGAL_FORMS:
            return False
        if _is_name_word(word) or folded in _BUSINESS_WORDS or folded in _LEGAL_FORMS \
                or (index and _is_name_link(tokens, index)):
            continue
        # Qualificatif en minuscules après le mot de commerce (« Épicerie fine Zitoun »).
        end = _qualifier_end(tokens, index) if index and fold(tokens[index - 1].group()) in _BUSINESS_WORDS \
            else None
        if end is None:
            return False
        qualifier_until = end
    return True


# Lieu en fin de signature : « Surf Shack in Taghazout », « Pâtisserie Nour à Ifrane », « Snack Atlas
# f Agadir ». Ces prépositions de lieu n'entrent jamais dans un nom de commerce, contrairement à
# « de » (« Douceurs de Fès »), qui n'est retiré que devant une ville connue.
_TRAILING_PLACE_RE = re.compile(r"\s+(?:in|at|à|a|f|fi)\s+[A-ZÀ-Ý][\w'-]*(?:\s+[A-ZÀ-Ý][\w'-]*)?$")


def _without_trailing_place(part: str) -> str:
    stripped = _TRAILING_PLACE_RE.sub("", part)
    return stripped if stripped.strip() else part


def _signature_part_candidates(part: str, position: int, after_closing: bool) -> list[_ClientCandidate]:
    part = _tidy_name(part)
    if not part or _CONTACT_RE.search(part):
        return []
    words = _WORD_RE.findall(fold(part))
    if any(word in _JOB_TITLE_WORDS for word in words):
        return []
    if _has_arabic(part):
        tokens = _arabic_tokens(part)
        if len(tokens) > _MAX_NAME_WORDS or not all(token.folded.isalnum() for token in tokens):
            return []
        if any(_is_arabic_stop(token.folded) for token in tokens):
            return []  # phrase (verbe, pronom, préposition) et non une signature
        tokens = _without_trailing_arabic_city(tokens)
        part = " ".join(token.original for token in tokens)
        if tokens and _arabic_business(_strip_arabic_prefix(tokens[0].folded, ("ال",))[0])[0]:
            return _candidate(part, _PRIORITY_SIGNATURE_BUSINESS, position)
        return _candidate(part, _PRIORITY_SIGNATURE, position) if after_closing and len(tokens) <= 3 else []
    part = _without_trailing_place(part)
    if not _latin_name_like(part):
        return []
    if _has_business_word(part):
        return _candidate(part, _PRIORITY_SIGNATURE_BUSINESS, position)
    return _candidate(part, _PRIORITY_SIGNATURE, position) if after_closing else []


_TRAILING_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\s+[–—-]\s+|\s*,\s+|\s*،\s*")


def _last_sentence(line: str) -> str | None:
    """Dernier segment d'une ligne : après un point, une virgule ou un tiret (« …, 600 7ba, Bazar Atlas »)."""
    segments = [segment for segment in _TRAILING_SPLIT_RE.split(line) if segment.strip()]
    return segments[-1] if len(segments) > 1 else None


def _signature_candidates(text: str) -> list[_ClientCandidate]:
    """Signature : lignes après la formule de politesse, dernières lignes courtes, dernière phrase."""
    lines: list[tuple[int, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        if raw_line.strip():
            lines.append((offset, raw_line.strip()))
        offset += len(raw_line)
    if not lines:
        return []
    closings = [index for index, (_, line) in enumerate(lines) if _is_closing(line)]
    if closings:
        block, after_closing = lines[closings[-1] + 1:closings[-1] + 5], True
    else:
        block, after_closing = _trailing_lines(lines), False
    if any(_supplier_side(line) for _, line in block):
        block = []
    candidates: list[_ClientCandidate] = []
    for position, line in block:
        for part in _PART_SPLIT_RE.split(line):
            candidates += _signature_part_candidates(part, position, after_closing)
    sentence = _last_sentence(lines[-1][1])
    if sentence:
        candidates += [candidate for candidate in _signature_part_candidates(sentence, lines[-1][0], False)
                       if candidate.priority == _PRIORITY_SIGNATURE_BUSINESS]
    return candidates


def extract_client_name(text: Any) -> str | None:
    """Nom du client (raison sociale ou commerce) cité dans un message, tel qu'il est écrit.

    Motifs, du plus sûr au moins sûr : libellé « Client : X » ; présentation avec entreprise
    (« ana Hamid mn Snack X », « c'est Karim du Burger X », « this is Laila from X »,
    « أنا سميرة من X ») ; « smitou X », « سميتو X » ; « société X » ; présentation d'un commerce
    (« c'est la Pâtisserie X ») ; destinataire (« pour X », « for X », « l X », « to X »,
    « à la Fromagerie X », « لمطعم X ») ; signature de l'email (ligne avec un mot de commerce ou
    une forme juridique comme SARL) ; nom seul après la formule de politesse ; enfin prénom
    d'une personne qui se présente (« ana Youssef »).

    Un nom réduit à une ville, à un mot générique ou à TECPAP n'est jamais renvoyé. La ville
    introduite par une préposition n'est pas reprise (« مكتبة القلم بتطوان » → « مكتبة القلم »).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    prepared = prepare_text(text[:MAX_TEXT_CHARS])
    main = _main_message(prepared)
    for part in dict.fromkeys((main, prepared)):
        candidates = _client_candidates(part)
        if candidates:
            return max(candidates, key=lambda candidate: (candidate.priority, -candidate.position)).name
    return None


def _client_candidates(text: str) -> list[_ClientCandidate]:
    return [
        *_label_candidates(text), *_intro_candidates(text), *_sentence_affiliation_candidates(text),
        *_here_from_candidates(text), *_role_candidates(text),
        *_order_from_candidates(text), *_behalf_candidates(text), *_named_candidates(text),
        *_company_candidates(text), *_apposition_candidates(text), *_recipient_candidates(text),
        *_arabic_candidates(text), *_business_mention_candidates(text), *_signature_candidates(text),
    ]


# ---------------------------------------------------------------- autres champs du message

_UNIT_PRICE_LABELS: tuple[tuple[str, ...], ...] = (
    ("prix", "unitaire"), ("unit", "price"), ("price", "per", "unit"), ("pu",), ("prix",), ("price",),
    ("سعر", "الوحدة"), ("السعر",), ("ثمن", "الوحدة"),
)
_TOTAL_PRICE_LABELS: tuple[tuple[str, ...], ...] = (
    ("montant", "total"), ("prix", "total"), ("total", "amount"), ("total",), ("montant",), ("amount",),
    ("المبلغ",), ("المجموع",),
)
# Mots qui peuvent séparer un libellé de prix de sa valeur (« prix unitaire HT : 2,10 »).
_PRICE_FILLERS = frozenset({":", "=", "de", "d", "of", "as", "quoted", "ht", "ttc", "unitaire", "is", "est",
                            "hors", "taxe", "taxes", "en", "-", "a", "par", "unite", "per", "unit"})
_GRAMMAGE_UNITS = _folded_set(("g", "gr", "grs", "gramme", "grammes", "gram", "grams", "gsm", "غرام", "غ"))
_ORDER_NUMBER_RES = (
    re.compile(r"(?i)\bbon\s+de\s+commande\s*(?:n[°o]\.?|num[ée]ro|#)?\s*:?\s*(?P<ref>[A-Z0-9][\w/.-]*\d[\w/-]*)"),
    re.compile(r"(?i)\bcommande\s*(?:n[°o]\.?(?![^\W\d_])|num[ée]ro|#)\s*:?\s*(?P<ref>[A-Z0-9][\w/.-]*\d[\w/-]*)"),
    re.compile(r"(?i)\b(?:purchase\s+)?order\s*(?:no\.?|number|#|n°)\s*:?\s*(?P<ref>[A-Z0-9][\w/.-]*\d[\w/-]*)"),
    re.compile(r"(?im)^\s*n[°o]\s*:\s*(?P<ref>[A-Z0-9][\w/.-]*\d[\w/-]*)"),
    re.compile(r"(?<![\w-])(?P<ref>(?:BC|PO|CMD)[-_/]?\d[\w/-]*)"),
)
_CLIENT_CODE_RE = re.compile(r"(?i)(?<![\w])(CL\d{3,})(?!\w)")
_FORMAT_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.,]\d)?)(?!\d)\s*[x×*]\s*(\d{1,3}(?:[.,]\d)?)(?!\d)"
    r"(?:\s*[x×*]\s*(\d{1,3}(?:[.,]\d)?)(?!\d))?"
)
_DELIVERY_LABEL_RE = re.compile(
    r"(?<!\w)(?:livraison|livrer|livre|livres|livree|livrees|livrable|livrables|delivery|deliver|delivered|"
    r"للتسليم|التسليم|التوصيل|تسليم)(?!\w)"
)
_ORDER_DATE_RE = re.compile(
    r"(?im)^[ \t]*(?:date\s+(?:de\s+)?(?:la\s+)?commande|date|order\s+date|تاريخ\s+الطلب)[ \t]*:[ \t]*(?P<value>[^\n]+)"
)


def _matches_label(tokens: Sequence[_Token], index: int, label: Sequence[str]) -> bool:
    return all(index + offset < len(tokens) and tokens[index + offset].text == word
               for offset, word in enumerate(label))


def _labelled_number(tokens: Sequence[_Token], labels: Sequence[Sequence[str]]) -> tuple[float, str | None] | None:
    """Premier nombre qui suit un libellé (« Prix unitaire : 0,45 MAD ») et la devise qui le suit."""
    first_words = {label[0] for label in labels}
    for index in range(len(tokens)):
        if tokens[index].text not in first_words:
            continue
        for label in labels:
            if not _matches_label(tokens, index, label):
                continue
            position = index + len(label)
            while position < len(tokens) and tokens[position].text in _PRICE_FILLERS:
                position += 1
            if position < len(tokens) and _starts_number(tokens[position]):
                run = _read_number(tokens, position)
                if run is not None and not run.lone_one:
                    following = tokens[run.end].text if run.end < len(tokens) else ""
                    suffix = tokens[run.end - 1].suffix
                    return run.value, normalize_currency(suffix or following) if (suffix or following) else None
    return None


def _grammage(tokens: Sequence[_Token]) -> int | None:
    for run in _number_runs(tokens):
        last = tokens[run.end - 1]
        following = tokens[run.end].text if run.end < len(tokens) else ""
        before = _joined(_window_before(tokens, run.start, 2))
        if (last.suffix in _GRAMMAGE_UNITS or following in _GRAMMAGE_UNITS or "grammage" in before) \
                and run.digits and 20 <= run.value <= 500 and run.value.is_integer():
            return int(run.value)
    return None


def _laize(tokens: Sequence[_Token]) -> int | None:
    for index, token in enumerate(tokens):
        if token.text in ("laize", "laise"):
            position = index + 1
            while position < len(tokens) and tokens[position].text in (":", "=", "de"):
                position += 1
            if position < len(tokens) and tokens[position].kind == "num":
                value = tokens[position].value or 0
                if 5 <= value <= 200 and value.is_integer():
                    return int(value)
    return None


def _paper_type(folded: str) -> str | None:
    # « kraft » seul désigne la matière, pas sa couleur : il ne suffit pas à choisir un papier.
    for expression, code in PAPER_TYPES.items():
        if expression != "kraft" and re.search(rf"(?<!\w){re.escape(fold(expression))}(?!\w)", folded):
            return PAPER_TYPE_NAMES[code]
    return None


def _order_number(prepared: str) -> str | None:
    for pattern in _ORDER_NUMBER_RES:
        match = pattern.search(prepared)
        if match:
            reference = match.group("ref").rstrip(".-/")
            if 3 <= len(reference) <= 40:
                return reference
    return None


def _delivery_date(text: str, today: date) -> str | None:
    folded = fold(text)
    for match in _DELIVERY_LABEL_RE.finditer(folded):
        dates = _find_dates(_line_rest(folded, match.end(), limit=80), today)
        if dates:
            return dates[0].isoformat()
    return None


def _order_date(prepared: str, today: date) -> str | None:
    for match in _ORDER_DATE_RE.finditer(prepared):
        found = normalize_date(match.group("value"), today)
        if found:
            return found
    return None


def _format_sac(prepared: str) -> str | None:
    match = _FORMAT_RE.search(prepared)
    if not match:
        return None
    return "x".join(part for part in match.groups() if part)


# ---------------------------------------------------------------- est-ce une commande ?

_QUESTION_RE = re.compile(
    r"[?؟]|^\W*(?:wach|wash|est-ce|est ce|quel|quels|quelle|quelles|combien|comment|pourquoi|quand|what|how|"
    r"which|when|where|why|do you|does|is there|are there|ch7al|chhal|imta|واش|شحال|علاش|كيفاش|امتى|متى|هل|"
    r"ما هي|ما هو|ماذا|كم)(?!\w)"
)
_CANCEL_RE = re.compile(
    r"(?<!\w)(?:annuler|annulez|annule|annulation|cancel|cancelled|cancellation|nlghi|nlghiw|lghi|lghiw|"
    r"لغي|نلغي|الغي|الغاء)(?!\w)"
)
_STATUS_RE = re.compile(
    r"(?<!\w)(?:ou en est|suivi de (?:ma|notre) commande|statut|status|update on|any update|tracking|"
    r"fin wslat|fin wsalt|mazal ma|فين وصلات|فين وصلت|حالة طلب)"
)


_SENTENCE_RE = re.compile(r"[^.!?؟\n]+[.!?؟]*")
# Question sur le prix : la quantité citée y est hypothétique (« combien pour 5000 sacs ? »).
_PRICE_QUESTION_RE = re.compile(
    r"(?<!\w)(?:prix|tarif|tarifs|combien|cout|couts|price|prices|cost|how much|ch7al|chhal|bch7al|"
    r"b ch7al|taman|tamane|بشحال|شحال|ثمن|السعر|سعر|بكم|كم)(?!\w)"
)


# Mot interrogatif sur la quantité ou le prix placé au milieu de la phrase, souvent sans point
# d'interrogation dans un message court (« bghit n3ref ch7al 2000 sachet », « c'est combien 500 sacs »).
_INTERROGATIVE_RE = re.compile(r"(?<!\w)(?:combien|ch7al|chhal|bch7al|how much|بشحال|شحال|بكم)(?!\w)")
# Demande de devis (« un devis pour 3000 sacs », « please quote for », « عرض سعر ») : la quantité
# citée sert au calcul du prix, ce n'est pas encore une commande. « Le devis » ou « votre devis »
# renvoie à un devis déjà reçu, souvent accepté : ce n'est pas une demande.
_QUOTE_REQUEST_RE = re.compile(
    r"(?<!\w)(?:(?:un|une|demande de|demander un|d ?' ?un|nouveau) devis|devis pour|cotation|proforma|pro forma|"
    r"offre de prix|(?:a|an|price) (?:quote|quotation)|quote for|quotation for|please quote|price list|"
    r"عرض سعر|عرض ثمن|عرض الاسعار)(?!\w)"
)
# Verbes d'envoi : « envoyez-nous un devis » demande un document, pas une marchandise.
_SEND_VERBS_RE = re.compile(
    r"(?<!\w)(?:please send|send|envoyer|envoyez|envoie|sift|siftli|siftlia|siftlina|sifto|siftou|صيفط|صيفطو|"
    r"صيفطلي|صيفطولي)(?!\w)"
)


def _is_question(sentence: str) -> bool:
    stripped = sentence.strip()
    return (stripped.endswith(("?", "؟")) or bool(_QUESTION_RE.match(stripped))
            or bool(_INTERROGATIVE_RE.search(stripped)))


def _declarative_quantity(folded: str) -> bool:
    """Vrai quand une phrase affirmative du message porte une quantité commandée.

    « Pouvez-vous traduire le devis ? Logo en vert sur les 4 000 sacs. » : la question porte
    sur un autre sujet, la commande est dans la phrase affirmative.
    """
    sentences = [match.group() for match in _SENTENCE_RE.finditer(folded)]
    if any(_is_question(sentence) and _PRICE_QUESTION_RE.search(sentence) for sentence in sentences):
        return False
    return any(not _is_question(sentence) and _quantity_from_tokens(_masked_tokens(sentence)) is not None
               for sentence in sentences)


# Accusé de réception d'une livraison (« bien reçu les 2000 sacs », « wslatna », « توصلنا ») : la
# quantité citée est celle d'une commande passée.
_RECEIPT_RE = re.compile(
    r"(?<!\w)(?:bien recu|bien recus|avons recu|ai recu|on a recu|received|we got|wslatna|wslatni|wslouna|"
    r"wsalna|tsallamna|tsalemna|توصلنا|توصلت|وصلتنا|وصلاتنا|وصلوني|وصلني|استلمنا|استلمت)"
    r"(?:\W+\w+){0,3}?\W+(?:sacs?|sachets?|commande|livraison|marchandise|colis|cartons?|bags?|order|delivery|"
    r"goods|parcel|sachiyat|sel3a|sl3a|lcommande|السلعة|بالسلعة|الطلبية|بالطلبية|الطلب|الاكياس|الساشي|الكوموند|"
    r"\d+)(?!\w)"
)


_ARRIVAL_RE = re.compile(
    r"(?<!\w)(?:bien (?:arrivee?s?|recue?s?|livree?s?)|(?:est|sont) arrivee?s?|(?:has|have) (?:arrived|been delivered)|"
    r"arrived safely|well received)(?!\w)"
)


def _looks_like_order(folded: str, *, is_reorder: bool, product: str | None, quantity: float | None) -> bool:
    """Heuristique « est un bon de commande » : renouvellement, ou quantité, ou produit avec verbe de commande.

    Une annulation, un suivi de commande, un accusé de réception sans verbe de commande ou une
    question sans verbe de commande ne sont pas des commandes, même s'ils citent un produit. Une
    question sur un autre sujet que le prix n'empêche pas la commande portée par une phrase
    affirmative du même message. Une demande de devis n'est une commande qu'avec un verbe de
    commande autre qu'un verbe d'envoi (« envoyez-nous un devis » demande un document).
    """
    if is_reorder:
        return True
    order_intent = bool(_ORDER_INTENT_RE.search(folded))
    has_content = quantity is not None or (product is not None and order_intent)
    if not has_content:
        return False
    if _CANCEL_RE.search(folded) or _STATUS_RE.search(folded):
        return False
    if not order_intent and (_RECEIPT_RE.search(folded) or _ARRIVAL_RE.search(folded)):
        return False
    if _QUOTE_REQUEST_RE.search(folded):
        return bool(_ORDER_INTENT_RE.search(_SEND_VERBS_RE.sub(" ", folded)))
    if order_intent or not (_QUESTION_RE.search(folded) or _INTERROGATIVE_RE.search(folded)):
        return True
    return quantity is not None and _declarative_quantity(folded)


# ---------------------------------------------------------------- extraction par règles

def rule_based_extract(text: Any, *, today: date | None = None) -> dict[str, Any]:
    """Extraction d'une commande sans modèle de langue (secours et contrôle croisé).

    Champs renvoyés quand ils sont trouvés : ``type_produit``, ``quantite``, ``unite`` (« US »
    par défaut quand une quantité est lue sans unité), ``entreprise_cliente``,
    ``numero_commande``, ``code_client``, ``prix_unitaire``, ``prix_total``, ``devise`` (MAD par
    défaut quand un prix est lu sans devise), ``date_commande``, ``date_livraison``,
    ``grammage``, ``laize``, ``format_sac``, ``type_papier``. ``est_bon_commande`` et
    ``is_reorder`` (renouvellement) sont toujours présents. Un champ introuvable est absent du
    dictionnaire : aucune valeur n'est inventée.
    """
    result: dict[str, Any] = {"est_bon_commande": False, "is_reorder": False}
    if not isinstance(text, str) or not text.strip():
        return result
    text = text[:MAX_TEXT_CHARS]
    today = today or date.today()
    prepared = prepare_text(text)
    # La demande du client est lue dans son propre texte, sans le message cité d'une réponse ; le
    # message complet ne sert qu'à défaut (réponse « je confirme » à une offre citée).
    main = _main_message(prepared)
    folded = fold(main)
    tokens = _masked_tokens(main)

    is_reorder = detect_reorder(main)
    product = normalize_product_type(main) or normalize_product_type(text)
    fields: dict[str, Any] = {"type_produit": product, "entreprise_cliente": extract_client_name(text)}
    fields.update(_quantity_fields(main, tokens) or _quantity_fields(prepared, _masked_tokens(text)))
    fields.update(_price_fields(tokens, fields))
    fields.update({
        "numero_commande": _order_number(prepared),
        "code_client": match.group(1).upper() if (match := _CLIENT_CODE_RE.search(prepared)) else None,
        "date_commande": _order_date(prepared, today),
        "date_livraison": _delivery_date(text, today),
        "grammage": _grammage(tokens),
        "laize": _laize(tokens),
        "format_sac": _format_sac(prepared),
        "type_papier": _paper_type(folded),
    })
    result.update({name: value for name, value in fields.items() if value is not None})
    result["is_reorder"] = is_reorder
    result["est_bon_commande"] = _looks_like_order(folded, is_reorder=is_reorder, product=product,
                                                   quantity=fields.get("quantite"))
    logger.debug("Extraction par règles : %s", sorted(result))
    return result


def _unit_after_first_number(raw: str) -> str | None:
    """Unité écrite après le premier nombre d'une valeur (« 2500 pcs », « 4000 sachets (pièces) »), ou ``None``."""
    tokens = _masked_tokens(raw)
    run = next(_number_runs(tokens), None)
    if run is None:
        return None
    last = tokens[run.end - 1]
    suffix = last.suffix if last.kind == "num" else ""
    noun = _count_noun(_window_after(tokens, run.end, 6, stop_at_number=True), suffix)
    unit = normalize_unit(noun) if noun else None
    return unit if unit in CANONICAL_UNITS else _parenthesised_unit(tokens, run.end)


def _keyed_quantity(prepared: str) -> dict[str, Any]:
    """Quantité et unité écrites en paires clé-valeur (« "quantite": 2500, "unite": "pcs" »)."""
    for _, raw in _key_values(prepared, _QUANTITY_KEYS):
        quantity = parse_quantity(raw)
        if quantity is None or quantity <= 0:
            continue
        values: dict[str, Any] = {"quantite": _integral(quantity)}
        unit = next((normalize_unit(value) for _, value in _key_values(prepared, _UNIT_KEYS)), None)
        if unit not in CANONICAL_UNITS:
            unit = _unit_after_first_number(raw)
        values["unite"] = unit or "US"
        return values
    return {}


def _quantity_fields(prepared: str, tokens: Sequence[_Token]) -> dict[str, Any]:
    table = _table_values(prepared)
    if table:
        table.setdefault("unite", "US")
        table["quantite"] = _integral(table["quantite"])
        return table
    keyed = _keyed_quantity(prepared)
    if keyed:
        return keyed
    candidate = _quantity_from_tokens(tokens)
    if candidate is None:
        return {}
    return {"quantite": _integral(candidate.value), "unite": candidate.unit or "US"}


def _price_fields(tokens: Sequence[_Token], known: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    currency = None
    for name, labels in (("prix_unitaire", _UNIT_PRICE_LABELS), ("prix_total", _TOTAL_PRICE_LABELS)):
        if known.get(name) is not None:
            continue
        found = _labelled_number(tokens, labels)
        if found is not None:
            values[name] = found[0]
            currency = currency or (found[1] if found[1] in CURRENCY_CODES else None)
    if values or known.get("prix_unitaire") is not None or known.get("prix_total") is not None:
        values["devise"] = currency or "MAD"
    return values


# ---------------------------------------------------------------- normalisation d'une commande

@dataclass
class NormalizationResult:
    """Résultat de :func:`normalize_order`.

    Attributes:
        data: commande normalisée ; les champs vides ou illisibles en sont retirés, les clés
            hors du schéma (``email_id``, ``source``…) sont conservées telles quelles.
            ``data["confiance"]`` garde la confiance déclarée par le modèle, ramenée sur 0–100.
        anomalies: phrases en français destinées à l'équipe commerciale.
        missing_fields: champs obligatoires (``REQUIRED_ORDER_FIELDS``) absents.
        confidence: confiance calculée par les règles, de 0 à 100.
        filled_fields: champs complétés à partir du texte du message (``source_text``).
    """

    data: dict[str, Any]
    anomalies: list[str]
    missing_fields: list[str]
    confidence: int
    filled_fields: list[str] = field(default_factory=list)


_FIELD_LABELS = {
    "quantite": "la quantité", "prix_unitaire": "le prix unitaire", "prix_total": "le prix total",
    "grammage": "le grammage", "laize": "la laize", "date_commande": "la date de commande",
    "date_livraison": "la date de livraison", "confiance": "la confiance",
}
_UPPERCASE_FIELDS = frozenset({"code_client", "code_article"})
_PLACEHOLDER_PREFIXES = tuple(fold(prefix) for prefix in GENERIC_CLIENT_PREFIXES)


def _format_number(value: float) -> str:
    """Nombre écrit à la française : « 10 000 », « 0,45 »."""
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _format_date(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{day}/{month}/{year}"


def _quote(value: Any) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= 60 else text[:57] + "..."


def _is_placeholder_client(name: Any) -> bool:
    return isinstance(name, str) and fold(name).strip().startswith(_PLACEHOLDER_PREFIXES)


def _model_confidence(value: Any) -> float | None:
    """Confiance déclarée par le modèle, sur 0–100 (une valeur entre 0 et 1 est une fraction)."""
    number = parse_quantity(value)
    if number is None:
        return None
    if 0 < number < 1:
        number *= 100
    return min(max(number, 0.0), 100.0)


def _normalize_value(name: str, kind: str, raw: Any, today: date, anomalies: list[str]) -> Any:
    """Valeur normalisée d'un champ ; une valeur présente mais illisible est signalée."""
    if kind == "boolean":
        return to_bool(raw)
    if kind in ("number", "measure", "integer"):
        if name == "confiance":
            return _model_confidence(raw)
        number = parse_quantity(raw)
        if number is None:
            if clean_text(raw) is not None and name in _FIELD_LABELS:
                anomalies.append(f"Valeur illisible pour {_FIELD_LABELS[name]} : « {_quote(raw)} ».")
            return None
        if kind == "integer":
            return int(number) if number.is_integer() and number >= 1 else None
        return _integral(number) if kind == "measure" or name == "quantite" else number
    if kind == "date":
        found = normalize_date(raw, today)
        if found is None and clean_text(raw) is not None:
            anomalies.append(f"Valeur illisible pour {_FIELD_LABELS[name]} : « {_quote(raw)} ».")
        return found
    text = clean_text(raw, max_length=_TEXT_LIMITS[kind], multiline=kind == "text")
    if text is None:
        return None
    if name == "unite":
        return normalize_unit(text)
    if name == "devise":
        return normalize_currency(text)
    if name in _UPPERCASE_FIELDS:
        return "".join(text.split()).upper()
    return text


def _normalize_fields(order: dict[str, Any], today: date, anomalies: list[str]) -> None:
    price_currency = next((currency for raw in (order.get("prix_unitaire"), order.get("prix_total"))
                           if isinstance(raw, str) and (currency := normalize_currency(raw)) in CURRENCY_CODES),
                          None)
    for name, kind in ORDER_FIELD_KINDS.items():
        if name not in order:
            continue
        value = _normalize_value(name, kind, order[name], today, anomalies)
        if value is None:
            del order[name]
        else:
            order[name] = value
    if "devise" not in order and price_currency:
        order["devise"] = price_currency
    _normalize_product(order, anomalies)
    _normalize_client(order, anomalies)


def _normalize_product(order: dict[str, Any], anomalies: list[str]) -> None:
    raw = order.get("type_produit")
    if raw is None:
        return
    canonical = normalize_product_type(raw)
    if canonical is not None:
        order["type_produit"] = canonical
        return
    del order["type_produit"]
    order.setdefault("nature_produit", raw)
    anomalies.append(f"Le type de produit « {_quote(raw)} » ne correspond à aucun produit du catalogue.")


def _normalize_client(order: dict[str, Any], anomalies: list[str]) -> None:
    name = order.get("entreprise_cliente")
    if name is not None and "tecpap" in fold(name):
        del order["entreprise_cliente"]
        anomalies.append("Le nom de client extrait désigne TECPAP, le fournisseur : client à identifier.")


def _same_number(first: Any, second: Any) -> bool:
    return isinstance(first, (int, float)) and isinstance(second, (int, float)) \
        and math.isclose(first, second, rel_tol=1e-9, abs_tol=1e-9)


def _apply_source_text(order: dict[str, Any], source_text: str, today: date, anomalies: list[str]) -> list[str]:
    """Complète les champs obligatoires vides depuis le message et signale les désaccords avec les règles."""
    found = rule_based_extract(source_text, today=today)
    from_history = set(order.get("history_fields") or ())
    filled: list[str] = []
    for name in REQUIRED_ORDER_FIELDS:
        rule_value = found.get(name)
        if rule_value is None:
            continue
        current = order.get(name)
        if current is None or (name == "entreprise_cliente" and _is_placeholder_client(current)):
            order[name] = rule_value
            filled.append(name)
            if name == "quantite" and "unite" not in order and found.get("unite"):
                order["unite"] = found["unite"]
        elif name in from_history or name == "entreprise_cliente":
            continue
        elif name == "quantite" and not _same_number(current, rule_value):
            anomalies.append(f"La quantité extraite ({_format_number(current)}) diffère de la quantité lue dans "
                             f"le message ({_format_number(rule_value)}).")
        elif name == "type_produit" and current != rule_value:
            anomalies.append(f"Le type de produit extrait (« {current} ») diffère de celui lu dans le message "
                             f"(« {rule_value} »).")
    if "est_bon_commande" not in order:
        order["est_bon_commande"] = found["est_bon_commande"]
        filled.append("est_bon_commande")
    return filled


def _quantity_anomalies(order: Mapping[str, Any]) -> list[str]:
    quantity = order.get("quantite")
    if quantity is None:
        return []
    if quantity <= 0:
        return [f"La quantité est nulle ou négative ({_format_number(quantity)})."]
    if quantity > MAX_PLAUSIBLE_QUANTITY:
        return [f"La quantité ({_format_number(quantity)}) dépasse {_format_number(MAX_PLAUSIBLE_QUANTITY)} : "
                "valeur à vérifier."]
    return []


def _price_anomalies(order: Mapping[str, Any]) -> list[str]:
    anomalies = []
    unit_price, total, quantity = order.get("prix_unitaire"), order.get("prix_total"), order.get("quantite")
    if unit_price is not None and unit_price < 0:
        anomalies.append(f"Le prix unitaire est négatif ({_format_number(unit_price)}).")
    if total is not None and total < 0:
        anomalies.append(f"Le prix total est négatif ({_format_number(total)}).")
    if None not in (unit_price, total, quantity) and quantity > 0 and unit_price > 0:
        expected = quantity * unit_price
        if abs(total - expected) > PRICE_TOLERANCE * expected:
            currency = f" {order['devise']}" if order.get("devise") else ""
            anomalies.append(f"Le prix total ({_format_number(total)}{currency}) ne correspond pas à la quantité "
                             f"multipliée par le prix unitaire ({_format_number(expected)}{currency}).")
    return anomalies


def _date_anomalies(order: Mapping[str, Any], today: date) -> list[str]:
    anomalies = []
    delivery, ordered = order.get("date_livraison"), order.get("date_commande")
    if delivery and ordered and delivery < ordered:
        anomalies.append(f"La date de livraison ({_format_date(delivery)}) précède la date de commande "
                         f"({_format_date(ordered)}).")
    if delivery and delivery < today.isoformat():
        anomalies.append(f"La date de livraison ({_format_date(delivery)}) est déjà passée.")
    return anomalies


def _standard_anomalies(order: Mapping[str, Any]) -> list[str]:
    anomalies = []
    grammage, laize = order.get("grammage"), order.get("laize")
    if grammage is not None and grammage not in STANDARD_GRAMMAGES:
        standards = ", ".join(map(str, STANDARD_GRAMMAGES))
        anomalies.append(f"Le grammage {_format_number(grammage)} g/m² ne fait pas partie des grammages "
                         f"standards ({standards}).")
    if laize is not None and laize not in STANDARD_LAIZES:
        standards = ", ".join(map(str, STANDARD_LAIZES))
        anomalies.append(f"La laize {_format_number(laize)} cm ne fait pas partie des laizes standards "
                         f"({standards}).")
    return anomalies


def _client_anomalies(order: Mapping[str, Any]) -> list[str]:
    name = order.get("entreprise_cliente")
    if _is_placeholder_client(name):
        return [f"Le client n'est pas identifié (nom provisoire « {_quote(name)} »)."]
    return []


def _missing_fields(order: Mapping[str, Any]) -> list[str]:
    missing = []
    for name in REQUIRED_ORDER_FIELDS:
        value = order.get(name)
        if value is None or value == "" or (name == "entreprise_cliente" and _is_placeholder_client(value)):
            missing.append(name)
    return missing


def _confidence(model: float | None, missing: Sequence[str], anomalies: Sequence[str]) -> int:
    """0,5 × modèle + 0,5 × complétude, −10 par anomalie, plafonnée à la complétude + 10, entre 0 et 100."""
    completeness = 100 * (len(REQUIRED_ORDER_FIELDS) - len(missing)) / len(REQUIRED_ORDER_FIELDS)
    score = completeness if model is None else 0.5 * model + 0.5 * completeness
    score -= ANOMALY_PENALTY * len(anomalies)
    score = min(score, completeness + CONFIDENCE_MARGIN)
    return math.floor(min(max(score, 0.0), 100.0) + 0.5)


def normalize_order(data: Any, *, source_text: str | None = None, today: date | None = None) -> NormalizationResult:
    """Normalise et contrôle une commande extraite (par le modèle de langue ou par les règles).

    Étapes :
    1. chaque champ du schéma est normalisé : nombres par :func:`parse_quantity`, unité,
       devise, dates ISO, type de produit du catalogue, textes nettoyés ; une valeur illisible
       est retirée et signalée ;
    2. avec ``source_text`` (texte du message), les champs obligatoires vides sont complétés
       par :func:`rule_based_extract`, et un désaccord entre la quantité ou le produit extraits
       et ceux lus par les règles est signalé (sauf pour un champ repris de l'historique) ;
    3. devise MAD par défaut quand un prix est présent sans devise ;
    4. anomalies : quantité nulle, négative ou supérieure à 5 000 000, prix négatif, prix total
       différent de quantité × prix unitaire de plus de 2 %, date de livraison antérieure à la
       commande ou passée, grammage ou laize hors des valeurs standards, produit hors catalogue,
       nom de client provisoire ;
    5. confiance : 0,5 × confiance du modèle + 0,5 × complétude (part des champs obligatoires
       présents), moins 10 points par anomalie, jamais plus que la complétude + 10, entre 0
       et 100 ; la complétude seule quand le modèle n'a pas donné de confiance.

    ``today`` (date du jour par défaut) sert aux dates sans année et au contrôle des dates
    passées. La fonction ne lève jamais d'exception sur une valeur invalide.
    """
    today = today or date.today()
    order = dict(data) if isinstance(data, Mapping) else {}
    anomalies: list[str] = []
    _normalize_fields(order, today, anomalies)
    filled = _apply_source_text(order, source_text, today, anomalies) if isinstance(source_text, str) else []
    if "devise" not in order and (order.get("prix_unitaire") is not None or order.get("prix_total") is not None):
        order["devise"] = "MAD"
    for check in (_quantity_anomalies, _price_anomalies, _standard_anomalies, _client_anomalies):
        anomalies += check(order)
    anomalies += _date_anomalies(order, today)
    missing = _missing_fields(order)
    confidence = _confidence(order.get("confiance"), missing, anomalies)
    logger.debug("Commande normalisée : %d anomalie(s), champs manquants %s, confiance %d",
                 len(anomalies), missing, confidence)
    return NormalizationResult(order, anomalies, missing, confidence, filled)
