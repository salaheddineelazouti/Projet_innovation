"""Normalisation et découpage en mots des textes de la recherche documentaire.

Les questions des clients mélangent le français, l'arabe standard et la darija marocaine, écrite
en lettres arabes ou en lettres latines avec des chiffres à la place de certaines lettres
arabes (« arabizi » : 3 pour ع, 7 pour ح, 9 pour ق…). Pour qu'une même idée écrite de plusieurs
façons donne les mêmes mots, chaque texte passe par :func:`normalize_text` puis
:func:`tokenize` :

1. normalisation Unicode NFKC (formes de compatibilité : ligatures, chiffres et lettres pleine
   largeur, formes de présentation de l'arabe), passage en minuscules ;
2. suppression des signes diacritiques : accents du français (« écru » → « ecru »), voyelles
   courtes de l'arabe (harakat, tanwin, chadda, soukoun) et tatwil (trait d'allongement « ـ ») ;
3. unification des variantes de lettres arabes : alif avec hamza ou madda (أ إ آ ٱ) → ا,
   alif maqsoura ى → ي, ta marbouta ة → ه, waw et ya avec hamza (ؤ ئ) → و ي, lettres persanes
   ی ک → ي ك, chiffres arabo-indiens (٠-٩, ۰-۹) → chiffres 0-9 ;
4. découpage en mots (suites de lettres et de chiffres, avec le trait d'union et l'apostrophe
   comme séparateurs) ;
5. réécriture des mots en arabizi : dans un mot qui mêle lettres latines et chiffres 2, 3, 5,
   7, 8 ou 9 isolés, chaque chiffre est remplacé par la ou les lettres latines les plus
   courantes (« b7al » → « bhal », « l3ada » → « laada », « 9ahwi » → « qahwi »). Les codes
   articles (« KB100L28 »), les quantités (« 80g », « 5000 ») et les unités de deux
   caractères (« m2 ») ne sont pas touchés ;
6. réduction des lettres répétées trois fois ou plus (« salaaam » → « salam ») ;
7. retrait des mots vides (mots grammaticaux et formules de politesse qui ne portent pas le
   sujet de la question : « le », « est-ce », « wach », « عافاك »…) et des lettres isolées ;
8. racinisation légère (« light stemming » : retrait des terminaisons les plus fréquentes,
   sans dictionnaire) : pluriel, e finals et terminaisons verbales -er et -ez pour les mots en
   lettres latines (« poignées » → « poign », « commander » → « command », « bags » →
   « bag ») ; pour l'arabe, retrait du waw initial, de l'article et des préfixes ``ب``,
   ``ك``, ``ف`` et ``ل`` qui s'y attachent, puis d'un suffixe fréquent, selon la méthode
   « light10 » de Larkey (« الطلبية » → « طلب »).

La même chaîne de traitement est appliquée aux documents et aux questions : une
réécriture imparfaite (un « 5kg » lu comme de l'arabizi, par exemple) reste donc sans effet
sur la correspondance entre une question et un document.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# ---------------------------------------------------------------- lettres et chiffres arabes

_ARABIC_TATWEEL = "\u0640"  # tatwil (trait d'allongement)

_ARABIC_LETTER_MAP = str.maketrans({
    "\u0623": "\u0627",  # أ alif hamza au-dessus → ا
    "\u0625": "\u0627",  # إ alif hamza au-dessous → ا
    "\u0622": "\u0627",  # آ alif madda → ا
    "\u0671": "\u0627",  # ٱ alif wasla → ا
    "\u0649": "\u064a",  # ى alif maqsoura → ya ي
    "\u06cc": "\u064a",  # ی ya persan → ya ي
    "\u0629": "\u0647",  # ة ta marbouta → ha ه
    "\u0624": "\u0648",  # ؤ waw hamza → waw و
    "\u0626": "\u064a",  # ئ ya hamza → ya ي
    "\u06a9": "\u0643",  # ک kaf persan → kaf ك
    _ARABIC_TATWEEL: None,
    **{chr(0x0660 + digit): str(digit) for digit in range(10)},  # chiffres arabo-indiens
    **{chr(0x06F0 + digit): str(digit) for digit in range(10)},  # chiffres arabo-indiens étendus
})

_WORD_PATTERN = re.compile(r"[^\W_]+")
_REPEATED_LETTERS = re.compile(r"([^\W\d_])\1{2,}")

# ---------------------------------------------------------------- arabizi

ARABIZI_DIGITS: dict[str, str] = {"2": "a", "3": "a", "5": "kh", "7": "h", "8": "gh", "9": "q"}
"""Chiffres de l'arabizi et lettres latines qui les remplacent."""

_ARABIZI_WORD = re.compile(r"(?=.*[a-z])(?=.*[235789])[a-z235789]+")
_CONSECUTIVE_DIGITS = re.compile(r"\d\d")

# ---------------------------------------------------------------- mots vides

_FRENCH_STOP_WORDS = """
a à au aux ce ceci cela ces cet cette chez comme comment d dans de des donc dont du elle elles en
entre est et etc il ils j je l la le les leur leurs lui m ma mais me mes moi mon n ne ni nos notre nous
on ou où par pas peu peut peuvent pour pourquoi qu que quel quelle quelles quels qui quoi s sa se
ses si son sont sur t ta te tes toi ton tu un une vos votre vous y
ai as avez avons ont suis es etes sommes etre être avoir fait faire faites puis puisse
peux pouvez pourriez pourrais pouvons voudrais voudrions souhaite souhaitons souhaiterais aimerais
savoir dire indiquer connaitre connaître merci bonjour bonsoir salut svp stp plait plaît cordialement
madame monsieur messieurs svp oui non
"""

_ENGLISH_STOP_WORDS = """
a an and are as at be by can could do does for from has have hello hi how i if in is it its me my
of on or our please thanks thank the this that to us we what when where which who why will with
would you your yours
"""

_DARIJA_LATIN_STOP_WORDS = """
wach wash wesh chno chnou chnu achno ach ana nta nti howa hiya hna ntoma homa had hadi hada hadak
hadik dik dak dyal dial dyali dialy dyalek dyalk dyalkom dialkom dyalna o w wla walla ola ila bach li
lli ma mashi machi chi rah raha 3afak aafak afak salam slm labas lah allah ykhlik khoya khti
"""

_ARABIC_STOP_WORDS = """
في من على الى إلى عن مع هل ما ماذا هو هي هم هذا هذه ذلك تلك التي الذي او أو و ثم كل كان يكون
انا أنا انت أنت نحن انتم أنتم لي لك له لها لكم لنا عند عندكم عندك
السلام عليكم شكرا عافاك من فضلك واش شنو اش اشنو ديال ديالي ديالك ديالكم ديالنا هاد هادي هادا
"""


def _normalized_word_set(raw: str) -> frozenset[str]:
    """Normalise une liste de mots vides comme les textes (accents, lettres arabes, arabizi)."""
    words: set[str] = set()
    for word in raw.split():
        words.update(_raw_tokens(normalize_text(word)))
    return frozenset(words)


# ---------------------------------------------------------------- normalisation


def strip_diacritics(text: str) -> str:
    """Retire les signes diacritiques combinants (accents, voyelles courtes de l'arabe).

    La décomposition canonique (NFD) sépare chaque lettre de ses signes ; les signes
    (catégorie Unicode « Mn ») sont retirés, puis le texte est recomposé (NFC). Pour l'arabe,
    la décomposition sépare aussi la hamza et la madda de l'alif, du waw et du ya.
    """
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return unicodedata.normalize("NFC", kept)


def normalize_text(text: str) -> str:
    """Normalise un texte pour la comparaison : NFKC, minuscules, sans diacritiques.

    Les variantes de lettres arabes sont unifiées et les chiffres arabo-indiens remplacés par
    les chiffres 0-9. La ponctuation et les espaces sont conservés : le découpage en mots est
    fait par :func:`tokenize`.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = strip_diacritics(normalized)
    return normalized.translate(_ARABIC_LETTER_MAP)


def rewrite_arabizi(word: str) -> str:
    """Remplace les chiffres d'un mot écrit en arabizi par des lettres latines.

    Un mot est lu comme de l'arabizi s'il a au moins trois caractères, ne contient que des
    lettres latines minuscules et des chiffres 2, 3, 5, 7, 8, 9, avec au moins une lettre et au
    moins un de ces chiffres, et jamais deux chiffres de suite. Les autres mots (« m2 », « a4 »,
    « kb100l28 ») sont renvoyés tels quels.
    """
    if len(word) < 3 or not _ARABIZI_WORD.fullmatch(word) or _CONSECUTIVE_DIGITS.search(word):
        return word
    return "".join(ARABIZI_DIGITS.get(char, char) for char in word)


def _raw_tokens(normalized: str) -> list[str]:
    """Mots d'un texte déjà normalisé, arabizi réécrit et lettres répétées réduites."""
    tokens = []
    for word in _WORD_PATTERN.findall(normalized):
        word = _REPEATED_LETTERS.sub(r"\1", rewrite_arabizi(word))
        tokens.append(word)
    return tokens


# ---------------------------------------------------------------- racinisation légère

_ARABIC_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
_ARABIC_SUFFIXES = ("ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي")
_ARABIC_LETTERS = re.compile(r"[ء-ي]")
_LATIN_PLURAL_ENDINGS = ("s", "x")
_LATIN_VERB_ENDINGS = ("er", "ez")


def _stem_arabic(word: str) -> str:
    """Racinisation « light10 » de Larkey : préfixes (article, و) puis un suffixe fréquent."""
    if len(word) >= 4 and word.startswith("و"):
        word = word[1:]
    for prefix in _ARABIC_PREFIXES:
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            word = word[len(prefix):]
            break
    for suffix in _ARABIC_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


def _stem_latin(word: str) -> str:
    """Retire le pluriel (s, x), les e finals, puis les terminaisons verbales -er et -ez.

    « poignée » et « poignées » → « poign », « torsadé » et « torsadées » → « torsad »,
    « commander » et « commande » → « command », « proposez » → « propos ». Chaque règle ne
    s'applique qu'aux mots assez longs pour garder une racine.
    """
    if len(word) > 3 and word.endswith(_LATIN_PLURAL_ENDINGS):
        word = word[:-1]
    while len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 5 and word.endswith(_LATIN_VERB_ENDINGS):
        word = word[:-2]
    return word


def light_stem(word: str) -> str:
    """Racine approchée d'un mot normalisé (arabe ou latin) ; les nombres restent inchangés."""
    if word.isdigit():
        return word
    if _ARABIC_LETTERS.search(word):
        return _stem_arabic(word)
    return _stem_latin(word)


# ---------------------------------------------------------------- découpage en mots

STOP_WORDS: frozenset[str] = _normalized_word_set(
    _FRENCH_STOP_WORDS + _ENGLISH_STOP_WORDS + _DARIJA_LATIN_STOP_WORDS + _ARABIC_STOP_WORDS
)
"""Mots vides en français, anglais, darija (lettres latines et arabes) et arabe, normalisés."""


def _is_single_letter(token: str) -> bool:
    return len(token) == 1 and not token.isdigit()


def tokenize(text: str, *, stem: bool = True, remove_stop_words: bool = True) -> list[str]:
    """Découpe un texte en mots normalisés, dans l'ordre du texte.

    Args:
        text: texte quelconque (français, arabe, darija, anglais, mélange).
        stem: applique la racinisation légère (:func:`light_stem`).
        remove_stop_words: retire les mots vides (:data:`STOP_WORDS`).

    Returns:
        La liste des mots, éventuellement vide.
    """
    tokens = _raw_tokens(normalize_text(text))
    if remove_stop_words:
        tokens = [token for token in tokens if token not in STOP_WORDS and not _is_single_letter(token)]
    if stem:
        tokens = [light_stem(token) for token in tokens]
    return [token for token in tokens if token]


def join_fields(parts: Iterable[str | None]) -> str:
    """Assemble des champs de texte non vides, un par ligne."""
    return "\n".join(part.strip() for part in parts if part and part.strip())


__all__ = [
    "ARABIZI_DIGITS",
    "STOP_WORDS",
    "join_fields",
    "light_stem",
    "normalize_text",
    "rewrite_arabizi",
    "strip_diacritics",
    "tokenize",
]
