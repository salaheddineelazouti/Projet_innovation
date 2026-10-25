"""Repérage et masquage des numéros de téléphone et des adresses email.

Ces fonctions protègent les coordonnées personnelles (OWASP LLM02, divulgation d'informations
sensibles) à trois endroits : les réponses générées par le modèle de langue avant leur envoi
(voir :class:`tecpap.security.guardrails.OutputFilter`), le journal des événements de sécurité
(l'expéditeur y est enregistré masqué) et les exemples de commandes réutilisés dans les
instructions envoyées au modèle.

Méthode pour les numéros de téléphone :

1. Les chiffres arabo-indiens (٠-٩), persans (۰-۹) et pleine largeur (０-９) sont lus comme
   des chiffres occidentaux, caractère par caractère, ce qui conserve les positions du texte.
2. Le texte est découpé en groupes de chiffres séparés par une ou deux espaces, un point ou un
   tiret ; un groupe entre parenthèses (« (0) », « (415) », « (+212) ») peut être accolé à ses
   voisins. Une suite qui contient un autre séparateur (« / », « , », lettre) est coupée.
3. Dans chaque suite, la plus longue sous-suite de groupes qui forme un numéro valide est
   retenue, ce qui sépare un numéro d'une quantité écrite juste avant ou juste après.
4. Un numéro est valide quand il correspond à un format reconnu :

   - format international : « + » ou « 00 » suivi de 8 à 15 chiffres (norme E.164,
     plan de numérotation international : indicatif du pays puis numéro national) ;
     un numéro marocain (indicatif 212) a exactement 9 chiffres après l'indicatif, et le
     préfixe interurbain « (0) » ou « 0 » écrit après l'indicatif est retiré ;
   - format national marocain : 10 chiffres commençant par 0 (06…, 07…, 05…) ;
   - indicatif marocain sans « + » : 212 suivi de 9 chiffres commençant par 5 à 8.

   Les dates (« 15/11/2026 »), quantités (« 10 000 »), prix (« 7 600,00 ») et références
   (« BC-2026-0417 ») ne forment aucun de ces formats et restent intacts.

La forme canonique d'un numéro (:func:`canonical_phone`) est sa forme E.164 : « +212612345678 »
pour « 06 12 34 56 78 », « whatsapp:+212612345678 » ou « +212 (0)6 12 34 56 78 ». Les listes
de numéros autorisés sont comparées sous cette forme.

Les adresses email sont repérées par une expression régulière classique (partie locale, « @ »,
nom de domaine avec au moins un point) et par la forme écrite pour échapper aux robots
(« nom [at] domaine [dot] ma »). Leur forme canonique est en minuscules.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

PHONE_PLACEHOLDER = "[numéro masqué]"
"""Texte qui remplace un numéro de téléphone masqué."""

EMAIL_PLACEHOLDER = "[email masqué]"
"""Texte qui remplace une adresse email masquée."""

MASK_CHAR = "•"
"""Caractère qui remplace les chiffres ou les lettres cachés par :func:`mask_phone` et :func:`mask_email`."""

_DIGIT_TRANSLATION = str.maketrans(
    {
        **{chr(0x0660 + value): str(value) for value in range(10)},  # chiffres arabo-indiens
        **{chr(0x06F0 + value): str(value) for value in range(10)},  # chiffres persans
        **{chr(0xFF10 + value): str(value) for value in range(10)},  # chiffres pleine largeur
    }
)

_CHANNEL_PREFIX = re.compile(r"^\s*(?:whatsapp|tel|tél|sms|mailto)\s*:\s*", re.IGNORECASE)

# Suite candidate : caractères qui peuvent composer un numéro écrit avec séparateurs. Le motif
# n'a pas de quantificateur imbriqué : son coût reste linéaire en la longueur du texte.
_PHONE_SPAN = re.compile(r"(?<![\d@+.,_])(?:\(\s?)?\+?\d[\d \u00a0\u202f.\-()+]*")

# Groupe de chiffres, éventuellement précédé de « + » ou placé entre parenthèses.
_PHONE_GROUP = re.compile(r"\(\s?\+?\d{1,4}\s?\)|\+?\d+")

# Séparateur accepté entre deux groupes : une ou deux espaces, ou un point / tiret entouré
# d'au plus une espace de chaque côté.
_VALID_GAP = re.compile(r"[ \u00a0\u202f]{1,2}|[ \u00a0\u202f]?[.\-][ \u00a0\u202f]?")

_TRUNK_PREFIX = re.compile(r"\(\s?0\s?\)")

_MAX_GROUPS = 15
_MOROCCO_CODE = "212"
_MOROCCAN_NATIONAL = re.compile(r"0[1-9]\d{8}")
_MOROCCAN_WITHOUT_PLUS = re.compile(r"212[5-8]\d{8}")

_EMAIL = re.compile(
    r"(?<![\w.+\-])[\w][\w.%+\-]{0,63}@(?:[^\W_](?:[\w\-]{0,61}[^\W_])?\.)+[^\W\d_]{2,24}(?![\w\-])"
)
# Forme écrite pour échapper aux robots : « nom [at] domaine [dot] ma », « nom (arobase) … ».
_OBFUSCATED_EMAIL = re.compile(
    r"(?<![\w.])(?P<local>[\w][\w.\-]{0,63})\s?[\[(]\s?(?:at|arobase|@)\s?[\])]\s?"
    r"(?P<domain>[\w\-]+(?:\s?[\[(]\s?(?:dot|point|\.)\s?[\])]\s?[\w\-]+)+)",
    re.IGNORECASE,
)
_OBFUSCATED_DOT = re.compile(r"\s?[\[(]\s?(?:dot|point|\.)\s?[\])]\s?", re.IGNORECASE)


@dataclass(frozen=True)
class SensitiveMatch:
    """Coordonnée repérée dans un texte.

    Attributes:
        start: position du premier caractère dans le texte d'origine.
        end: position qui suit le dernier caractère.
        value: texte d'origine (« 06 12 34 56 78 »).
        normalized: forme canonique (« +212612345678 », adresse en minuscules).
    """

    start: int
    end: int
    value: str
    normalized: str


@dataclass(frozen=True)
class RedactionResult:
    """Texte après masquage, avec le nombre de coordonnées masquées."""

    text: str
    phones: int = 0
    emails: int = 0

    @property
    def total(self) -> int:
        """Nombre total de coordonnées masquées."""
        return self.phones + self.emails


# ---------------------------------------------------------------- numéros de téléphone


def _to_ascii_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def canonical_phone(raw: str | None) -> str | None:
    """Forme E.164 d'un numéro (« +212612345678 »), ou ``None`` si ce n'est pas un numéro valide.

    Accepte les préfixes de canal (« whatsapp: », « tel: »), les séparateurs (espaces, points,
    tirets, parenthèses) et les formats décrits dans la documentation du module.
    """
    if not raw:
        return None
    text = _CHANNEL_PREFIX.sub("", unicodedata.normalize("NFKC", str(raw)))
    text = _to_ascii_digits(text).strip()
    international = text.lstrip("( ").startswith("+")
    text = _TRUNK_PREFIX.sub("", text)
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if digits.startswith("00"):
        international = True
        digits = digits[2:]
    if international:
        return _international_phone(digits)
    if _MOROCCAN_NATIONAL.fullmatch(digits):
        return f"+{_MOROCCO_CODE}{digits[1:]}"
    if _MOROCCAN_WITHOUT_PLUS.fullmatch(digits):
        return f"+{digits}"
    return None


def _international_phone(digits: str) -> str | None:
    # « +212 06 12 34 56 78 » : le 0 national écrit après l'indicatif est retiré.
    if digits.startswith(f"{_MOROCCO_CODE}0") and len(digits) == 13:
        digits = _MOROCCO_CODE + digits[4:]
    if not 8 <= len(digits) <= 15 or digits[0] == "0":
        return None
    if digits.startswith(_MOROCCO_CODE) and len(digits) != 12:
        return None
    if digits[0] == "1" and len(digits) != 11:  # plan de numérotation nord-américain
        return None
    return f"+{digits}"


def _segments(probe: str, groups: list[tuple[int, int]]) -> Iterator[list[tuple[int, int]]]:
    """Coupe une suite de groupes là où le séparateur n'est pas celui d'un numéro."""
    segment: list[tuple[int, int]] = []
    for start, end in groups:
        if segment:
            previous_start, previous_end = segment[-1]
            gap = probe[previous_end:start]
            parenthesized = probe[previous_start] == "(" or probe[start] == "("
            valid = _VALID_GAP.fullmatch(gap) is not None or (gap == "" and parenthesized)
            if not valid or probe[start] == "+" or probe[start:end].startswith("(+"):
                yield segment
                segment = []
        segment.append((start, end))
    if segment:
        yield segment


def _valid_ranges(probe: str, segment: list[tuple[int, int]]) -> Iterator[tuple[int, int, str]]:
    """Plus longues sous-suites de groupes qui forment un numéro valide, de gauche à droite."""
    index = 0
    while index < len(segment):
        found = None
        for stop in range(min(len(segment), index + _MAX_GROUPS), index, -1):
            start, end = segment[index][0], segment[stop - 1][1]
            if probe[end:end + 1] == "@":  # partie locale d'une adresse email
                continue
            canonical = canonical_phone(probe[start:end])
            if canonical:
                found = (start, end, canonical, stop)
                break
        if found is None:
            index += 1
            continue
        start, end, canonical, stop = found
        yield start, end, canonical
        index = stop


def find_phone_numbers(text: str | None) -> list[SensitiveMatch]:
    """Numéros de téléphone présents dans ``text``, dans l'ordre du texte."""
    if not text:
        return []
    probe = _to_ascii_digits(text)
    matches = []
    for span in _PHONE_SPAN.finditer(probe):
        offset = span.start()
        groups = [(offset + match.start(), offset + match.end()) for match in _PHONE_GROUP.finditer(span.group())]
        for segment in _segments(probe, groups):
            for start, end, canonical in _valid_ranges(probe, segment):
                matches.append(SensitiveMatch(start, end, text[start:end], canonical))
    return matches


def normalize_phone_set(phones: Iterable[str | None] | None) -> frozenset[str]:
    """Formes canoniques des numéros donnés ; les valeurs qui ne sont pas des numéros sont ignorées."""
    return frozenset(filter(None, (canonical_phone(phone) for phone in phones or ())))


# ---------------------------------------------------------------- adresses email


def normalize_email(raw: str | None) -> str:
    """Forme canonique d'une adresse : sans préfixe « mailto: », sans espaces, en minuscules."""
    if not raw:
        return ""
    text = _CHANNEL_PREFIX.sub("", unicodedata.normalize("NFKC", str(raw))).strip()
    obfuscated = _OBFUSCATED_EMAIL.fullmatch(text)
    if obfuscated:
        domain = _OBFUSCATED_DOT.sub(".", obfuscated.group("domain"))
        text = f"{obfuscated.group('local')}@{domain}"
    return text.casefold()


def find_emails(text: str | None) -> list[SensitiveMatch]:
    """Adresses email présentes dans ``text`` (forme classique ou écrite « [at] … [dot] »)."""
    if not text:
        return []
    matches = [
        SensitiveMatch(match.start(), match.end(), match.group(), normalize_email(match.group()))
        for match in _EMAIL.finditer(text)
    ]
    for match in _OBFUSCATED_EMAIL.finditer(text):
        if not any(match.start() < other.end and other.start < match.end() for other in matches):
            matches.append(SensitiveMatch(match.start(), match.end(), match.group(), normalize_email(match.group())))
    return sorted(matches, key=lambda item: item.start)


def normalize_email_set(emails: Iterable[str | None] | None) -> frozenset[str]:
    """Formes canoniques des adresses données (les valeurs vides sont ignorées)."""
    return frozenset(filter(None, (normalize_email(email) for email in emails or ())))


# ---------------------------------------------------------------- masquage


def _replace(text: str, matches: Iterable[SensitiveMatch], placeholder: str) -> str:
    pieces, position = [], 0
    for match in sorted(matches, key=lambda item: item.start):
        if match.start < position:
            continue
        pieces.append(text[position:match.start])
        pieces.append(placeholder)
        position = match.end
    pieces.append(text[position:])
    return "".join(pieces)


def redact_phones(text: str | None, allowed: Iterable[str] | None = (),
                  placeholder: str = PHONE_PLACEHOLDER) -> str:
    """Remplace par ``placeholder`` les numéros de ``text`` absents de ``allowed``.

    ``allowed`` contient des numéros sous n'importe quelle forme (« 0612345678 »,
    « whatsapp:+212612345678 ») : ils sont comparés sous leur forme canonique.
    """
    if not text:
        return text or ""
    permitted = normalize_phone_set(allowed)
    matches = [match for match in find_phone_numbers(text) if match.normalized not in permitted]
    return _replace(text, matches, placeholder)


def redact_emails(text: str | None, allowed: Iterable[str] | None = (),
                  placeholder: str = EMAIL_PLACEHOLDER) -> str:
    """Remplace par ``placeholder`` les adresses de ``text`` absentes de ``allowed`` (casse ignorée)."""
    if not text:
        return text or ""
    permitted = normalize_email_set(allowed)
    matches = [match for match in find_emails(text) if match.normalized not in permitted]
    return _replace(text, matches, placeholder)


def redact_contacts(text: str | None, *, allowed_phones: Iterable[str] | None = (),
                    allowed_emails: Iterable[str] | None = (), phone_placeholder: str = PHONE_PLACEHOLDER,
                    email_placeholder: str = EMAIL_PLACEHOLDER) -> RedactionResult:
    """Masque les adresses email puis les numéros de téléphone non autorisés.

    Les adresses sont traitées en premier : les chiffres d'une partie locale
    (« 0612345678@exemple.ma ») ne sont pas pris pour un numéro.
    """
    if not text:
        return RedactionResult(text or "")
    permitted_emails = normalize_email_set(allowed_emails)
    email_matches = [match for match in find_emails(text) if match.normalized not in permitted_emails]
    text = _replace(text, email_matches, email_placeholder)
    permitted_phones = normalize_phone_set(allowed_phones)
    phone_matches = [match for match in find_phone_numbers(text) if match.normalized not in permitted_phones]
    text = _replace(text, phone_matches, phone_placeholder)
    return RedactionResult(text, phones=len(phone_matches), emails=len(email_matches))


def mask_phone(raw: str | None) -> str:
    """Numéro partiellement masqué pour les journaux : ``"+212612345678"`` → ``"+2126•••••678"``.

    L'indicatif, le premier chiffre du numéro national et les trois derniers chiffres restent
    lisibles. Une valeur qui n'est pas un numéro reconnu est masquée selon sa longueur.
    """
    if not raw:
        return ""
    value = canonical_phone(raw)
    if value is None:
        cleaned = _to_ascii_digits(_CHANNEL_PREFIX.sub("", str(raw))).strip()
        digits = re.sub(r"\D", "", cleaned)
        if not digits:
            return MASK_CHAR * 3
        value = f"+{digits}" if cleaned.startswith("+") else digits
    if len(value) >= 11:
        keep_start, keep_end = 5, 3
    elif len(value) >= 7:
        keep_start, keep_end = 2, 2
    else:
        keep_start, keep_end = 0, min(2, len(value) - 1)
    hidden = len(value) - keep_start - keep_end
    return value[:keep_start] + MASK_CHAR * hidden + (value[-keep_end:] if keep_end else "")


def mask_email(raw: str | None) -> str:
    """Adresse partiellement masquée : ``"h.bennani@exemple.ma"`` → ``"h•••@exemple.ma"``.

    Seul le premier caractère de la partie locale reste lisible ; le nombre de caractères
    masqués est fixe, pour ne pas révéler la longueur de l'adresse.
    """
    if not raw:
        return ""
    local, _, domain = normalize_email(raw).partition("@")
    if not domain:
        return mask_identifier(raw)
    return f"{local[:1]}{MASK_CHAR * 3}@{domain}"


def mask_identifier(raw: str | None) -> str:
    """Identifiant quelconque masqué : les deux premiers caractères restent lisibles."""
    if not raw:
        return ""
    value = str(raw).strip()
    return f"{value[:2]}{MASK_CHAR * 3}" if len(value) > 3 else MASK_CHAR * 3


def mask_sender(raw: str | None) -> str:
    """Expéditeur masqué pour le journal : numéro (:func:`mask_phone`), email (:func:`mask_email`) ou autre."""
    if not raw:
        return ""
    value = str(raw).strip()
    if _EMAIL.fullmatch(_CHANNEL_PREFIX.sub("", value)):
        return mask_email(value)
    if len(re.sub(r"\D", "", _to_ascii_digits(value))) >= 6:
        return mask_phone(value)
    return mask_identifier(value)
