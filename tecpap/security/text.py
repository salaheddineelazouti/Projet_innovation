"""Nettoyage des textes reçus et formes normalisées utilisées pour la recherche de motifs.

Deux usages distincts :

1. :func:`sanitize_text` produit le texte transmis au modèle de langue et enregistré : forme de
   normalisation Unicode NFKC (les caractères de compatibilité, comme les lettres pleine
   largeur « ｉｇｎｏｒｅ » ou les lettres mathématiques « 𝐢𝐠𝐧𝐨𝐫𝐞 », deviennent les lettres
   ordinaires), suppression des caractères de contrôle et des caractères invisibles (espaces de
   largeur nulle, marques de direction, caractères d'étiquette Unicode), espaces regroupées,
   longueur bornée. Le sens du message est conservé.

2. :func:`build_views` produit des copies du texte qui ne servent qu'à la comparaison avec les
   motifs de détection et ne sont jamais transmises ni enregistrées :

   - ``raw`` : texte nettoyé, casse conservée, homoglyphes remplacés (lettres cyrilliques ou
     grecques d'apparence identique à une lettre latine, comme « о » cyrillique pour « o ») ;
   - ``lowered`` : en minuscules, entités HTML décodées (« &lt; » → « < »), accents latins
     retirés (« précédentes » → « precedentes »), signes diacritiques arabes (voyelles brèves,
     chadda) et tatweel (allongement « ـ ») retirés, variantes de lettres arabes unifiées
     (« أ إ آ » → « ا », « ة » → « ه », « ى » → « ي »), chiffres arabo-indiens convertis ;
     ponctuation et retours à la ligne conservés (marqueurs de rôle, requêtes SQL) ;
   - ``words`` : ``lowered`` réduit aux lettres et chiffres séparés par une espace, après
     regroupement des lettres espacées une à une (« i g n o r e » → « ignore ») ; dans les
     mots qui mélangent lettres et chiffres, 0 devient « o » et 1 devient « i ». Les autres
     chiffres sont conservés : en arabizi (darija écrite en lettres latines), 3, 7 et 9
     remplacent des lettres arabes (« 3tini », « b7al », « t9dro ») ;
   - ``leet`` : comme ``words`` avec la table complète du leetspeak (écriture qui remplace des
     lettres par des chiffres ou des symboles : 3 → e, 4 → a, 5 → s, 7 → t, @ → a, $ → s,
     ! → i, | → l), appliquée aux seuls mots qui mélangent lettres et chiffres ou symboles ;
     les nombres (quantités, dates) ne sont jamais modifiés.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 4000
"""Longueur maximale par défaut d'un texte nettoyé par :func:`sanitize_text`."""

# Catégories Unicode supprimées : contrôle (Cc), format invisible (Cf : espaces de largeur
# nulle, marques de direction, caractères d'étiquette), demi-codets isolés (Cs), usage privé (Co).
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})
_KEPT_CONTROLS = frozenset({"\n", "\t"})
_LINE_SEPARATORS = str.maketrans({"\u2028": "\n", "\u2029": "\n", "\x85": "\n", "\r": "\n"})

# Homoglyphes : lettres cyrilliques, grecques et petites capitales latines d'apparence
# identique à une lettre latine. La casse est conservée.
CONFUSABLES: dict[str, str] = {
    # cyrillique minuscule
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
    "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w", "ɡ": "g",
    # cyrillique majuscule
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J", "Ѕ": "S", "Ԁ": "D",
    # grec
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "ϲ": "c", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # petites capitales latines
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ғ": "f", "ɢ": "g", "ʜ": "h", "ɪ": "i",
    "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ʀ": "r", "ꜱ": "s",
    "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
}
_CONFUSABLE_TRANSLATION = str.maketrans(CONFUSABLES)
_CONFUSABLE_SCRIPT_LETTER = re.compile(r"[\u0370-\u03ff\u0400-\u052f]")

# Lettres sans décomposition Unicode, et variantes de lettres arabes unifiées.
_LETTER_FOLDING = str.maketrans({
    "ł": "l", "ø": "o", "đ": "d", "ı": "i", "œ": "oe", "æ": "ae", "ŀ": "l", "ħ": "h",
    "ى": "ي", "ی": "ي", "ة": "ه", "ک": "ك", "ڭ": "ك", "ݣ": "ك", "گ": "ك", "ڤ": "ف", "ڨ": "ق",
    "ٱ": "ا", "ـ": "",
    **{chr(0x0660 + value): str(value) for value in range(10)},
    **{chr(0x06F0 + value): str(value) for value in range(10)},
})

_BASIC_LEET = str.maketrans({"0": "o", "1": "i"})
_FULL_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "|": "l"}
)
_LEET_SYMBOLS = frozenset("@$!|")

_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_SPACE_AROUND_NEWLINE = re.compile(r" ?\n ?")
_BLANK_LINES = re.compile(r"\n{3,}")
_NON_WORD = re.compile(r"[\W_]+")
_HAS_LETTER = re.compile(r"[^\W\d_]")
# Suite d'au moins trois caractères isolés séparés chacun par un seul séparateur : « i g n o r e »,
# « i.g.n.o.r.e », « i-g-n-o-r-e », « D.A.N ». Deux espaces consécutives séparent deux mots espacés.
_SPACED_RUN = re.compile(r"(?<![^\W_])(?:[^\W_][ .\-_*·|,+]){2,}[^\W_](?![^\W_])")
_SPACED_SEPARATORS = re.compile(r"[ .\-_*·|,+]")


def strip_invisible(text: str) -> str:
    """Supprime les caractères de contrôle et les caractères invisibles ; garde ``\\n`` et ``\\t``."""
    text = text.replace("\r\n", "\n").translate(_LINE_SEPARATORS)
    return "".join(
        char for char in text
        if char in _KEPT_CONTROLS or unicodedata.category(char) not in _REMOVED_CATEGORIES
    )


def _collapse_whitespace(text: str) -> str:
    """Espaces et tabulations regroupées, au plus une ligne vide consécutive, bords retirés."""
    text = _HORIZONTAL_SPACE.sub(" ", text)
    text = _SPACE_AROUND_NEWLINE.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def sanitize_text(text: str | None, max_chars: int | None = DEFAULT_MAX_CHARS) -> str:
    """Texte nettoyé : NFKC, sans caractère de contrôle ni invisible, espaces regroupées, tronqué.

    Les retours à la ligne sont conservés (au plus une ligne vide consécutive), les espaces et
    tabulations consécutives deviennent une seule espace.

    Args:
        text: texte reçu (``None`` donne une chaîne vide).
        max_chars: longueur maximale du résultat ; ``None`` pour ne pas tronquer.

    Raises:
        ValueError: si ``max_chars`` est négatif ou nul.
    """
    if max_chars is not None and max_chars <= 0:
        raise ValueError("max_chars doit être strictement positif")
    if not text:
        return ""
    text = _collapse_whitespace(strip_invisible(unicodedata.normalize("NFKC", str(text))))
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def replace_confusables(text: str) -> str:
    """Remplace les homoglyphes cyrilliques, grecs et les petites capitales par la lettre latine."""
    return text.translate(_CONFUSABLE_TRANSLATION)


def fold_characters(text: str) -> str:
    """Retire accents et signes diacritiques, unifie les lettres arabes ; la casse est conservée.

    Appliquée aux motifs de détection comme aux textes, elle garantit que les deux sont
    comparés sous la même forme. Les caractères de la syntaxe des expressions régulières ne
    sont pas modifiés.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return stripped.translate(_LETTER_FOLDING)


def lower_view(text: str) -> str:
    """Vue ``lowered`` d'un texte (voir la documentation du module)."""
    text = sanitize_text(html.unescape(text), max_chars=None)
    return fold_characters(replace_confusables(text).casefold())


def _join_spaced_letters(text: str) -> tuple[str, int]:
    """Regroupe les lettres espacées une à une ; renvoie aussi le nombre de suites d'au moins six lettres."""
    long_runs = 0

    def join(match: re.Match[str]) -> str:
        nonlocal long_runs
        letters = _SPACED_SEPARATORS.sub("", match.group())
        if len(letters) >= 6:
            long_runs += 1
        return letters

    return _SPACED_RUN.sub(join, text), long_runs


def _words(text: str, leet_table: dict[int, str]) -> str:
    tokens = []
    for token in text.split():
        if _HAS_LETTER.search(token) and any(char.isdigit() or char in _LEET_SYMBOLS for char in token):
            token = token.translate(leet_table)
        tokens.append(token)
    return _NON_WORD.sub(" ", " ".join(tokens)).strip()


@dataclass(frozen=True)
class MatchingViews:
    """Copies d'un texte destinées uniquement à la recherche de motifs (voir le module).

    ``raw_joined`` et ``lowered_joined`` sont les vues ``raw`` et ``lowered`` après
    regroupement des lettres espacées (« S E L E C T » → « SELECT ») ; ``spaced_runs`` compte
    les suites regroupées d'au moins six lettres.
    """

    raw: str
    lowered: str
    words: str
    leet: str
    raw_joined: str = ""
    lowered_joined: str = ""
    spaced_runs: int = 0


def build_views(text: str | None) -> MatchingViews:
    """Construit les vues de comparaison de ``text``."""
    # Les lettres espacées sont regroupées avant le regroupement des espaces : deux espaces
    # consécutives séparent alors deux mots écrits lettre par lettre (« i g n o r e  a l l »).
    base = replace_confusables(strip_invisible(unicodedata.normalize("NFKC", html.unescape(text or ""))))
    joined, spaced_runs = _join_spaced_letters(base)
    raw, raw_joined = _collapse_whitespace(base), _collapse_whitespace(joined)
    lowered, lowered_joined = fold_characters(raw.casefold()), fold_characters(raw_joined.casefold())
    return MatchingViews(
        raw=raw,
        lowered=lowered,
        words=_words(lowered_joined, _BASIC_LEET),
        leet=_words(lowered_joined, _FULL_LEET),
        raw_joined=raw_joined,
        lowered_joined=lowered_joined,
        spaced_runs=spaced_runs,
    )


def normalize_for_matching(text: str | None) -> str:
    """Forme de comparaison d'un texte : vue ``words`` (minuscules, sans accents ni ponctuation)."""
    return build_views(text).words


def decode_tag_characters(text: str) -> str:
    """Texte ASCII caché dans des caractères d'étiquette Unicode (U+E0020 à U+E007E)."""
    return "".join(chr(ord(char) - 0xE0000) for char in text if 0xE0020 <= ord(char) <= 0xE007E)


def has_mixed_script_word(text: str) -> bool:
    """Vrai si un mot mélange des lettres latines et des homoglyphes cyrilliques ou grecs.

    Seules les lettres de :data:`CONFUSABLES` comptent : « 70 μm » (micromètre, lettre grecque
    mu, sans équivalent latin d'apparence identique) n'est pas signalé.
    """
    for word in re.findall(r"[^\W\d_]+", text):
        if re.search(r"[A-Za-z]", word) and any(
            char in CONFUSABLES and _CONFUSABLE_SCRIPT_LETTER.match(char) for char in word
        ):
            return True
    return False


_INVISIBLE_INSIDE_WORD = re.compile(
    r"[A-Za-z][\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]+[A-Za-z]"
)


def count_invisible_inside_words(text: str) -> int:
    """Nombre de caractères invisibles placés entre deux lettres latines (« ig\\u200bnore »).

    Le trait d'union conditionnel (U+00AD), fréquent dans les textes issus de pages HTML,
    n'est pas compté.
    """
    return len(_INVISIBLE_INSIDE_WORD.findall(text))
