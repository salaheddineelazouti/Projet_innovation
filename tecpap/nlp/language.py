"""Détection de la langue et de l'écriture d'un message client.

Les clients de TECPAP écrivent en français, en anglais, en arabe standard moderne et en darija
(arabe dialectal marocain). La darija s'écrit en lettres arabes ou en lettres latines ; dans ce
second cas on parle d'arabizi : écriture de l'arabe avec l'alphabet latin, où des chiffres
remplacent les lettres arabes sans équivalent latin (3 = ع, 7 = ح, 9 = ق, 2 = ء, 5 = خ, 8 = غ).
Les messages sont souvent très courts (« ok », « wakha », « نعم »), dictés (transcription d'une
note vocale, sans ponctuation) ou mêlent darija et français dans la même phrase : c'est
l'alternance codique (« code switching » : passage d'une langue à l'autre à l'intérieur d'un même
message), la norme chez ces clients.

Les méthodes statistiques usuelles d'identification de la langue, comme les profils de n-grammes
de caractères (Cavnar et Trenkle, 1994 : fréquences des suites de n caractères consécutifs),
demandent un corpus d'entraînement par langue, deviennent peu fiables sur quelques mots et ne
séparent pas la darija de l'arabe standard, qui partagent le même alphabet. Ce module applique
une méthode par mots marqueurs, variante de la méthode des mots outils (Grefenstette, 1995 : les
mots outils, c'est-à-dire articles, prépositions, pronoms et conjonctions, suffisent à
reconnaître une langue). Elle est déterministe, rapide et n'appelle aucun modèle de langue.

1. Normalisation : forme Unicode NFKC (les variantes de compatibilité, comme les lettres pleine
   largeur ou les formes de présentation de l'arabe, sont remplacées par le caractère de base),
   suppression des caractères de mise en forme invisibles (espace sans chasse, marques de
   direction), suppression du tashkil (voyelles brèves et signes diacritiques de l'arabe) et du
   tatweel (caractère d'allongement), forme unique pour l'alif, le ya, le ta marbuta et les
   lettres marocaines (ڭ → ك), chiffres arabo-indiens (٣) convertis en chiffres occidentaux,
   lettre répétée plus de deux fois réduite à une seule (« wakhaaa » → « wakha »). Les mots sont
   comparés aux lexiques en minuscules.
2. Nettoyage des emails : lignes citées (« > »), en-têtes de réponse (« Le … a écrit : »),
   message d'origine sous un séparateur (« -----Original Message----- », bloc « De : … Envoyé : »),
   signature placée après le séparateur « -- » et mentions automatiques (« Envoyé de mon
   iPhone »). Ce nettoyage est aussi offert seul par :func:`message_body`, pour les composants
   qui analysent la partie écrite par l'expéditeur (détection d'intention). Les adresses email,
   les adresses web et les suites de plus de :data:`MAX_TOKEN_CHARS` caractères sans espace
   (contenu encodé, identifiants) sont ignorées.
3. Écriture : part des lettres arabes parmi les lettres arabes et latines (voir :data:`SCRIPTS`).
4. Indices : chaque mot reconnu ajoute son poids au score d'une langue, 1 pour un marqueur sans
   ambiguïté, 0,5 pour un mot qui existe aussi ailleurs (« ana » est aussi un prénom) :

   - darija en lettres latines : lexique :data:`DARIJA_LATIN_MARKERS`, mots arabizi (chiffre
     2, 3, 5, 7, 8 ou 9 dans un mot : « 3afak », « ch7al », à l'exclusion des quantités avec
     unité comme « 80g » et des références comme « BC-2026 » ; un chiffre placé seulement en fin
     de mot, « lbar7 », compte 0,5 car la même forme sert aux références comme « ref3 »),
     négation « ma … ch » (« mabghitch », « ma jatch »), préfixes verbaux du futur « gha- »
     (« ghansift », 1) et du présent « ka- » (« katsifto », 0,5, car quelques mots empruntés
     commencent ainsi, comme « kanban ») suivis d'un préfixe de personne n, t ou y, et réponse
     « la » (non) envoyée seule, qui ne forme pas un message à elle seule en français. Un
     marqueur écrit avec une majuscule au milieu d'une phrase compte pour moitié (:data:`PROPER_NOUN_FACTOR`) : c'est
     probablement un nom propre (« Snack Daba », « Boutique Lina ») ;
   - darija en lettres arabes : lexique :data:`DARIJA_ARABIC_MARKERS` (« بغيت », « ديال »,
     « واش »…), négation « ما … ش » (« ما بقيتش »), futur « غا » suivi d'un préfixe de personne
     (« غانصيفط », « غاتوصل ») ;
   - arabe standard : :data:`MSA_MARKERS`, mots que la darija n'emploie pas (« أريد », « هل »,
     « الذي », « سوف »…) ; les mots communs aux deux (« هذا », « من ») ne comptent pas ;
   - français : :data:`FRENCH_MARKERS` (mots outils, formules de politesse, réponses courtes
     comme « parfait », et à 0,5 les noms du métier, les jours, les mois, les repères de temps
     et les nombres écrits en toutes lettres : « demain », « jeudi », « cinq mille »), élisions
     (« c' », « j' », 0,5), terminaison verbale en « -ez » (« envoyez », « validez », 0,5) et
     mots accentués (0,5) ;
   - anglais : :data:`ENGLISH_MARKERS` (mots outils, réponses courtes comme « great », et à 0,5
     les noms et verbes du métier en orthographe anglaise : « bags », « order », « shipped »,
     ainsi que les jours, les mois et les nombres écrits en toutes lettres) et contractions
     (« don't », « I'm », 0,5).

   En écriture arabe, un mot absent des lexiques est aussi cherché sans son proclitique
   (préfixe collé au mot) : particule « و », « ف », « ب », « ل », « ك » devant tout mot,
   article « ال » devant un nom de :data:`DARIJA_ARABIC_NOUNS` seulement (« الكوموند » →
   « كوموند », alors que « الفين », deux mille, ne contient pas « فين », où).
5. Décision, dans cet ordre :

   a. aucune lettre latine ni arabe : ``unknown`` ;
   b. darija : retenue quand ses indices atteignent :data:`CLEAR_DARIJA_EVIDENCE` (deux marqueurs
      sûrs) ou quand, multipliés par :data:`DARIJA_WEIGHT`, ils égalent au moins le meilleur
      score du français, de l'anglais et de l'arabe standard. Un mot de darija n'apparaît
      presque jamais par hasard dans un texte français, alors que « la » ou « de » apparaissent
      dans toute phrase française : un marqueur clair de darija l'emporte donc sur quelques
      mots outils français (« bonjour bghit 3000 sachet pour mon snack » est de la darija).
      L'écriture où les indices de darija sont les plus forts donne ``darija_ar`` ou
      ``darija_latin`` (à égalité, l'écriture majoritaire) ;
   c. lettres arabes majoritaires : ``ar`` ; un texte en lettres arabes sans marqueur de darija
      est lu comme de l'arabe standard, même sans mot propre à l'arabe standard ;
   d. sinon, le meilleur score entre français et anglais (français en cas d'égalité) ;
   e. sans aucun indice : ``ar`` s'il reste des lettres arabes, ``unknown`` sinon (« ok »).

6. Confiance : part du score retenu dans la somme de ce score et du meilleur score concurrent,
   multipliée par ``1 - exp(-score / EVIDENCE_SCALE)``, qui tend vers 1 quand les indices
   s'accumulent : un mot isolé donne une confiance moyenne, un email complet une confiance
   proche de 1. Le résultat ``unknown`` a une confiance nulle.

Le dictionnaire ``scores`` du résultat contient le score de chacune des cinq langues (scores de
darija déjà multipliés par :data:`DARIJA_WEIGHT`), ce qui permet de relire et d'expliquer chaque
décision.

Limites : un nom propre formé de mots de darija et écrit en minuscules ou en début de phrase
compte comme de la darija ; une graphie de darija absente des lexiques n'est reconnue que par ses
chiffres arabizi ; un message sans aucun mot outil (« Tacos Bab Rbat 3000 ») reste ``unknown``.
Le résultat oriente la langue de réponse et le choix du modèle : il ne remplace pas la relecture
humaine.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NamedTuple

logger = logging.getLogger(__name__)

LANGUAGE_CODES: tuple[str, ...] = ("fr", "en", "ar", "darija_ar", "darija_latin", "unknown")
"""Codes de langue possibles : français, anglais, arabe standard, darija en lettres arabes,
darija en lettres latines, langue non identifiée."""

SCORED_LANGUAGES: tuple[str, ...] = ("fr", "en", "ar", "darija_ar", "darija_latin")
"""Langues qui reçoivent un score (clés du dictionnaire ``LanguageResult.scores``)."""

DARIJA_CODES: frozenset[str] = frozenset({"darija_ar", "darija_latin"})
"""Codes qui désignent la darija, quelle que soit l'écriture."""

SCRIPTS: tuple[str, ...] = ("latin", "arabic", "mixed", "none")
"""Écritures possibles : latine, arabe, mélange des deux (chacune couvre au moins
:data:`MIXED_SCRIPT_MIN_SHARE` des lettres), aucune lettre latine ni arabe."""

REPLY_LANGUAGES: tuple[str, ...] = ("fr", "darija", "ar")
"""Langues dans lesquelles l'assistant répond."""

DARIJA_WEIGHT = 3.0
"""Multiplicateur des indices de darija face aux mots outils des autres langues."""

CLEAR_DARIJA_EVIDENCE = 2.0
"""Total d'indices de darija qui suffit à retenir la darija (deux marqueurs sûrs)."""

ARABIC_DEFAULT_SUPPORT = 1.0
"""Score minimal attribué à l'arabe standard quand les lettres arabes sont majoritaires."""

EVIDENCE_SCALE = 2.0
"""Échelle de la saturation de la confiance : ``1 - exp(-score / EVIDENCE_SCALE)``."""

MIXED_SCRIPT_MIN_SHARE = 0.2
"""Part minimale des lettres de chaque écriture pour qu'un texte soit d'écriture « mixed »."""

MAX_ANALYSED_CHARS = 5000
"""Nombre de caractères analysés : le début d'un message suffit à en reconnaître la langue."""

MAX_TOKEN_CHARS = 30
"""Longueur au-delà de laquelle une suite sans espace est ignorée (contenu encodé, identifiant)."""

STRONG = 1.0
"""Poids d'un marqueur sans ambiguïté."""

WEAK = 0.5
"""Poids d'un mot qui existe aussi dans une autre langue ou comme nom propre."""


@dataclass(frozen=True)
class LanguageResult:
    """Langue détectée d'un message.

    Attributes:
        code: une des valeurs de :data:`LANGUAGE_CODES`.
        script: une des valeurs de :data:`SCRIPTS`.
        confidence: confiance entre 0 et 1 (0 pour ``unknown``).
        scores: score de chaque langue de :data:`SCORED_LANGUAGES`, pour expliquer la décision.
    """

    code: str
    script: str
    confidence: float
    scores: dict[str, float] = field(default_factory=dict, hash=False)

    @property
    def is_darija(self) -> bool:
        """Vrai pour la darija, en lettres arabes ou latines."""
        return self.code in DARIJA_CODES


# --------------------------------------------------------------------------- normalisation

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", "´": "'", "′": "'"})


def _code_point_class(*ranges: tuple[int, int]) -> str:
    """Classe de caractères d'expression régulière couvrant des intervalles de points de code."""
    return "[" + "".join(f"{re.escape(chr(first))}-{re.escape(chr(last))}" for first, last in ranges) + "]"


# Caractères de mise en forme invisibles : trait d'union conditionnel, marque de lettre arabe,
# espaces sans chasse, marques et enchâssements de direction, marque d'ordre des octets.
_FORMAT_CHARACTERS = re.compile(_code_point_class(
    (0x00AD, 0x00AD), (0x061C, 0x061C), (0x180E, 0x180E), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x2064), (0x2066, 0x2069), (0xFEFF, 0xFEFF),
))

# Tashkil (voyelles brèves, tanwin, shadda, sukun, alif suscrit, signes coraniques) et tatweel.
_ARABIC_DIACRITICS = re.compile(_code_point_class(
    (0x0610, 0x061A), (0x064B, 0x065F), (0x0670, 0x0670), (0x06D6, 0x06DC), (0x06DF, 0x06E8),
    (0x06EA, 0x06ED), (0x0640, 0x0640),
))

_ARABIC_LETTER_FORMS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ی": "ي", "ئ": "ي",
    "ة": "ه", "ۀ": "ه",
    "ؤ": "و",
    "ڭ": "ك", "گ": "ك", "ک": "ك",
    "ڤ": "ف", "پ": "ب", "ژ": "ز",
    **{chr(0x0660 + digit): str(digit) for digit in range(10)},
    **{chr(0x06F0 + digit): str(digit) for digit in range(10)},
})

_REPEATED_LETTER = re.compile(r"([^\W\d_])\1{2,}")


def _normalize_arabic(text: str) -> str:
    """Retire le tashkil et le tatweel, puis unifie les variantes de lettres arabes."""
    return _ARABIC_DIACRITICS.sub("", text).translate(_ARABIC_LETTER_FORMS)


def _normalize(text: str) -> str:
    """Forme Unicode NFKC, sans caractère invisible, apostrophes et lettres arabes unifiées.

    La casse est conservée : elle sert à repérer les noms propres (voir :func:`_tokens`).
    """
    text = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES)
    text = _FORMAT_CHARACTERS.sub("", text)
    return _normalize_arabic(text)


def _squeeze_repeated_letters(text: str) -> str:
    """Réduit à une seule une lettre répétée plus de deux fois (« wakhaaa » → « wakha »)."""
    return _REPEATED_LETTER.sub(r"\1", text)


def _normalize_word(word: str) -> str:
    """Forme normalisée d'un mot de lexique, identique à celle des mots analysés."""
    return _squeeze_repeated_letters(_normalize(word)).casefold()


# --------------------------------------------------------------------------- nettoyage des emails

_LINES = re.MULTILINE | re.IGNORECASE
_QUOTED_LINE = re.compile(r"^[ \t]*>.*$", _LINES)
_REPLY_HEADER = re.compile(
    r"^[ \t]*(?:le|on)\b[^\n]{0,200}(?:\n[^\n]{0,200})?\b(?:a écrit|wrote)[ \t]*:[ \t]*$", _LINES
)
_MOBILE_SIGNATURE = re.compile(
    r"^[ \t]*(?:sent from my|envoyé de mon|envoyé depuis mon|get outlook for|obtenir outlook pour)\b.*$", _LINES
)
_PREVIOUS_MESSAGE_SEPARATORS = (
    re.compile(
        r"^[ \t]*-{2,}[ \t]*(?:original message|message d'origine|forwarded message|message transféré)",
        _LINES,
    ),
    re.compile(r"^[ \t]*(?:de|from)[ \t]*:.*\n(?:.*\n){0,3}?[ \t]*(?:envoyé|sent)[ \t]*:", _LINES),
    re.compile(r"^--[ \t]*$", _LINES),
)
_EMAIL_ADDRESS = re.compile(r"\S+@\S+")
_WEB_ADDRESS = re.compile(r"(?:https?://|www\.)\S+")
_LONG_TOKEN = re.compile(rf"\S{{{MAX_TOKEN_CHARS + 1},}}")
_LETTER = re.compile(r"[^\W\d_]")


def _previous_message_start(text: str) -> int:
    """Position du premier séparateur de message d'origine ou de signature (fin du texte sinon)."""
    positions = [match.start() for pattern in _PREVIOUS_MESSAGE_SEPARATORS if (match := pattern.search(text))]
    return min(positions, default=len(text))


def _strip_email_noise(text: str) -> str:
    """Retire citations, en-têtes de réponse, message d'origine et signature d'un email.

    Le texte d'origine est conservé quand le nettoyage ne laisse aucune lettre (message réduit
    à un transfert, par exemple).
    """
    body = text
    cut = _previous_message_start(body)
    if _LETTER.search(body[:cut]):
        body = body[:cut]
    for pattern in (_QUOTED_LINE, _REPLY_HEADER, _MOBILE_SIGNATURE):
        body = pattern.sub(" ", body)
    return body if _LETTER.search(body) else text


def _strip_non_words(text: str) -> str:
    """Remplace adresses email, adresses web et longues suites sans espace par une espace."""
    for pattern in (_EMAIL_ADDRESS, _WEB_ADDRESS, _LONG_TOKEN):
        text = pattern.sub(" ", text)
    return text


def message_body(text: str) -> str:
    """Partie d'un message écrite par son expéditeur (nettoyage de l'étape 2 de la détection).

    Les lignes citées, les en-têtes de réponse, le message d'origine placé sous un séparateur, la
    signature et les mentions automatiques sont retirés ; les apostrophes typographiques sont
    unifiées. Un message réduit à un transfert garde tout son texte. Le reste du texte n'est pas
    modifié (casse, accents et chiffres conservés), ce qui permet d'y appliquer l'extraction.

    Raises:
        TypeError: ``text`` n'est pas une chaîne.
    """
    if not isinstance(text, str):
        raise TypeError(f"message_body attend une chaîne de caractères, reçu {type(text).__name__}")
    return _strip_email_noise(text.translate(_APOSTROPHES))


# --------------------------------------------------------------------------- lexiques


def _lexicon(strong: Iterable[str], weak: Iterable[str] = ()) -> Mapping[str, float]:
    """Construit un lexique pondéré, normalisé comme les textes analysés."""
    weights: dict[str, float] = {}
    for weight, words in ((WEAK, weak), (STRONG, strong)):
        for word in words:
            weights[_normalize_word(word)] = weight
    return MappingProxyType(weights)


DARIJA_LATIN_MARKERS: Mapping[str, float] = _lexicon(
    strong=(
        # pronoms
        "nta", "nti", "ntia", "ntaya", "ntiya", "ntouma", "ntoma", "ntuma", "7na", "hna",
        "howa", "huwa", "homa", "houma",
        # possessif « dyal » (de, à)
        "dyal", "dial", "dyali", "diali", "dyalk", "dialk", "dyalek", "dialek", "dyalkom", "dialkom",
        "dyalkoum", "dyalna", "dialna", "dyalo", "dyalou", "dialo", "dyalha", "dialha", "dyalhom", "dialhom",
        # interrogatifs
        "wach", "achno", "achnou", "chno", "chnou", "chnu", "ch7al", "chhal", "bch7al", "bchhal", "kifach", "kifash",
        "kifech", "fen", "mnin", "imta", "fo9ach", "fou9ach", "foqach", "fach", "3lach", "3lash", "3lache", "3lech",
        "chkoun", "chkon", "chkun", "achmen", "chmen", "chnahiya", "chnahowa",
        # adverbes et repères de temps
        "daba", "dba", "bzaf", "bezaf", "bzzaf", "bezzaf", "ghir", "mazal", "mazala", "ba9i", "lyoum",
        "lyouma", "lyoma", "lbare7", "lbar7", "lbareh", "ghda", "ghedda", "ghedwa", "dima", "chwiya", "chwia",
        "chwya", "hakka", "hakda", "hnaya", "temma", "nhar", "simana", "lmerra", "merra", "lkhmis",
        # politesse, salutations, accord
        "3afak", "afak", "3fak", "3afakom", "3afakoum", "ykhlik", "chokran", "choukran", "chukran", "shokran",
        "shukran", "lahoufik", "3likom", "alikom", "3alikom", "3alaykom", "labas", "bikhir", "hamdollah",
        "hamdoulah", "hamdoullah", "lhamdulillah", "hamdulillah", "nchallah", "wakha", "waxa", "wakhkha",
        "iyeh", "iyah", "eyeh", "ewa", "iwa", "khoya", "khouya", "khti", "sahbi", "bslama", "beslama", "bsslama",
        "marhba", "kidayr", "kidayra", "kidayrin", "kidayer", "kirak", "kiraki", "smhli", "smahli", "smah",
        "tbarkellah", "tbarkallah", "tbarkalah", "momkin", "mumkin", "momkine", "mumkine", "yemken", "imken",
        # négation
        "machi", "mashi", "makaynch", "makaynach", "walou", "walo", "wlou",
        # verbes fréquents (vouloir, falloir, donner, envoyer, faire, y avoir, avoir, dire, parler…)
        "bghit", "bghiti", "bghina", "bghitou", "bgha", "bghat", "bghaw", "nbghi", "kanbghi", "kanbghiw",
        "baghi", "baghya", "baghin", "baghyin",
        "khassni", "khasni", "khesni", "khessni", "khassna", "khasna", "khassek",
        "3tini", "3tina", "a3tini", "3tih", "3tiha", "3ti",
        "sift", "sifet", "siftli", "siftlia", "siftlina", "sifto", "siftoha", "siftohom", "tsiftoha",
        "tsiftohom", "tsifto", "tsift", "nsift", "nsiftlik", "sayft", "sayeft", "sayfet",
        "wsel", "wslat", "wsalt", "wselt", "wslt", "wslti",
        "jibli", "jibo", "dirli", "dert", "derti", "derna",
        "kayn", "kayna", "kayen", "kaynin", "kaina",
        "3ndi", "3endi", "3andi", "3ndna", "3endna", "3andna", "3ndkom", "3endkom", "3andkom", "3ndek", "3endek",
        "galt", "galo", "galik", "golia", "goulia", "gouli", "goli",
        "nhder", "nhdar", "nhdro", "hder", "nchouf", "chouf", "choufo", "chof",
        "ghadi", "ghadin", "ghadya", "n9der", "t9der", "t9dro", "tqder", "nqder",
        "mchit", "mcha", "jat", "jit", "nsa", "nsit", "nsaw", "ndir", "tdir", "ydir", "dir", "dirou", "zid", "zidni",
        "zido",
        "rah", "raha", "rak", "rakom", "warini", "werini", "nlghi", "lghi",
        "tsenna", "tsena", "tsna", "nsenna", "stanna", "nstanna", "tstanna", "kantsenna", "kanstanna",
        "nchri", "nchriw", "chrit", "chrina", "nkhelles", "nkhles", "khlest", "khelest", "tkhelso", "khod", "khoud",
        "nakhod", "nakhdo", "diri", "diro", "dirha", "battel", "nbattel", "battelha", "battlo", "battliha",
        "wajda", "wajed", "wajdin", "ghalat", "ghalta", "khata",
        # nombres
        "wahed", "wa7ed", "wa7d", "jouj", "juj", "zouj", "tlata", "tlat", "tleta", "rb3a", "reb3a", "rab3a",
        "khamsa", "khmsa", "stta", "setta", "sb3a", "seb3a", "tmnya", "tmenya", "tmanya", "tes3oud", "ts3ud",
        "3achra", "3chra", "alf", "alaf", "alfayn", "alfin", "mya", "miya", "myat",
        # démonstratifs, prépositions, conjonctions
        "hadchi", "hadshi", "dakchi", "dakshi", "hadak", "hadik", "hada", "hado", "hadou", "dik", "dak",
        "liya", "lik", "lih", "lihom", "likom", "m3a", "m3ak", "m3akom", "m3ana", "m3ah", "3la", "3lih", "3lik",
        "bach", "7it", "7ta", "wla", "wlla", "walakin", "walakine", "walaken",
        "ga3", "kamlin", "kolchi", "koulchi", "kulchi", "lokhrin", "lokhra", "lakhor",
        # adjectifs et noms courants (dont le vocabulaire des produits du catalogue)
        "mzyan", "mzyana", "mezyan", "mzian", "zwin", "zwina", "jdid", "jdida", "taman", "tamane", "flous",
        "lflous", "insan", "khdma", "drari", "hanout", "7anout", "l7anout", "lmahal", "yddin", "ydin",
        "yeddin", "mfetlin", "mfatlin", "sachiyat", "sachiat",
    ),
    weak=(
        "ana", "salam", "slm", "slam", "salamo", "inchallah", "inshallah", "safi", "li", "chi", "kif", "bla",
        "jib", "kain", "rani", "hadi", "lia", "lina", "mahal", "hiya", "ila", "brit", "lli", "yallah", "yalah",
        "ahlan", "wah",
    ),
)
"""Darija en lettres latines : mots outils, verbes et noms fréquents, avec leurs graphies courantes."""

# Noms du métier empruntés au français, que la darija écrit avec son article « l » attaché
# (« lcommande », « lsachet », « lprix ») : le français écrit « la commande » ou « l'emballage ».
_DARIJA_LATIN_LOANWORDS: tuple[str, ...] = (
    "commande", "commandes", "sac", "sacs", "sachet", "sachets", "prix", "facture", "livraison", "logo", "carton",
    "cartons", "kraft", "client", "stock", "papier", "couleur", "model", "modele", "taille", "quantite", "adresse",
    "numero", "camion", "produit", "produits", "poignee", "poignees", "catalogue", "minimum",
)
_LATIN_ARTICLE_LOANWORD = re.compile(r"^l(?:" + "|".join(_DARIJA_LATIN_LOANWORDS) + r")$")

# Mots empruntés au français, propres à la darija (l'arabe standard dit طلبية، زبون، كيس، أسبوع، ورق).
_DARIJA_ARABIC_LOANWORDS: tuple[str, ...] = (
    "كوموند", "كومند", "كوماند", "كليان", "ساك", "ساكات", "ساشي", "ساشيات", "سيمانة", "كاغيط",
    # noms français écrits avec l'article « la » collé (« la livraison », « la commande », « la facture »)
    "لافريزون", "ليفريزون", "لاكوموند", "لاكومند", "لافاكتور", "لافاكتير",
)

DARIJA_ARABIC_MARKERS: Mapping[str, float] = _lexicon(
    strong=(
        "بغيت", "بغيتي", "بغينا", "بغيتو", "بغا", "بغات", "بغاو", "نبغي", "كنبغي", "كنبغيو", "كتبغي", "كيبغي",
        "خاصني", "خصني", "خاصنا", "خصنا", "خاصك", "خصك", "خاصو", "خصو", "خاصكم",
        "ديال", "ديالي", "ديالك", "ديالنا", "ديالكم", "ديالو", "ديالها", "ديالهم",
        "واش", "شحال", "بشحال", "شنو", "اشنو", "آش", "علاش", "كيفاش", "فين", "منين", "امتى", "فوقاش",
        "دابا", "دبا", "بزاف", "مازال", "مزال", "ديما", "شوية", "هكا", "هاكا", "هكاكا", "دغيا",
        "عافاك", "عفاك", "عافاكم", "لاباس", "واخا", "صافي", "مزيان", "مزيانة", "زوين", "زوينة",
        "ماشي", "والو",
        "راه", "راني", "راك", "راها", "راهم",
        "عطيني", "عطينا", "عطيه", "صيفط", "صيفطو", "صيفطي", "صيفطلي", "صيفطليا", "صيفطلينا", "نصيفط",
        "تصيفط", "تصيفطو", "سيفط", "سيفطلي", "جيبلي", "نهضر", "هضر", "نشوف", "شوف",
        "غادي", "غادية", "غادين",
        "كاين", "كاينة", "كاينين", "كاينش", "ماكاينش", "مكاينش",
        "هادشي", "داكشي", "هاد", "هادي", "هادا", "هادو", "داك", "هداك", "هديك",
        "كاع", "كلشي", "كولشي", "لوخرين", "لوخرا", "بحال", "كيما",
        "ليا", "لينا", "ليك", "ليكم", "جوج", "تلاتة", "اللي", "حيت", "باش", "البارح", "لبارح", "إيوا",
        "معاك", "معاكم", "معايا", "معاه", "معاها", "معاهم", "معانا", "شكون", "فاش", "بسلامة", "مرسي", "خويا",
        "اخويا", "كنتسنى", "كنتسناو", "نتسنى", "تسنى", "مكاين", "ماكاين", "زربة", "نشري", "نشريو", "شريت", "كنشري",
        *_DARIJA_ARABIC_LOANWORDS,
    ),
    # « جيب » (apporte) et « ديك » (ce, cette) sont aussi des noms en arabe standard (poche, coq).
    # « سير » (va, impératif) est aussi un nom en arabe standard (marche, déroulement).
    weak=("إيه", "شي", "ليه", "جيب", "ديك", "سير", "سيري", "سيرو"),
)
"""Darija en lettres arabes : mots absents de l'arabe standard ou de sens différent."""

DARIJA_ARABIC_NOUNS: frozenset[str] = frozenset(_normalize_word(word) for word in _DARIJA_ARABIC_LOANWORDS)
"""Noms de darija qui prennent l'article « ال » (« الكوموند »), sous leur forme normalisée."""

MSA_MARKERS: Mapping[str, float] = _lexicon(
    strong=(
        "أريد", "نريد", "يريد", "تريد", "أحتاج", "نحتاج", "يحتاج", "هل", "الذي", "التي", "الذين", "اللذين",
        "اللتين", "اللواتي", "اللاتي", "سوف", "لدينا", "لدي", "لديكم", "لديك", "لديه", "نرجو", "أرجو", "يرجى",
        "الرجاء", "رجاء", "فضلكم", "نود", "أود", "نرغب", "أرغب", "يرغب", "ليس", "ليست", "لسنا", "لم", "لن",
        "أين", "متى", "ماذا", "لماذا", "كم", "جميع", "السادة", "المحترمون", "المحترمين", "المحترم",
        "المحترمة", "تحية", "تحياتي", "تحياتنا", "وبعد", "نعم", "أؤكد", "نؤكد", "يمكنكم", "يمكنني", "يمكننا",
        "أعطني", "أعطونا", "الآن", "جدا", "أيضا", "فقط", "ذلك", "تلك", "هؤلاء", "بخصوص", "بشأن", "عندما",
        "حيث", "نحن", "إذا", "نشكركم", "حضرتكم", "سيادتكم", "التالي", "التالية", "أستطيع", "نستطيع", "تستطيع",
        "يستطيع", "تستطيعون", "هناك", "هنالك", "سيتم", "يتم", "منذ", "أمس", "إلينا", "إليكم", "لدى", "ننتظر",
        "أنتظر", "بحاجة",
    ),
    weak=("إلى", "أن", "في", "أو", "هذه", "يمكن", "طيبة"),
)
"""Arabe standard moderne : mots que la darija remplace par d'autres (أريد / بغيت, هل / واش…)."""

FRENCH_MARKERS: Mapping[str, float] = _lexicon(
    strong=(
        "le", "les", "de", "des", "du", "un", "une", "et", "est", "sont", "pour", "par", "avec", "sans",
        "dans", "sur", "sous", "chez", "vers", "entre", "au", "aux", "en", "à", "ce", "cet", "cette", "ces",
        "ça", "cela", "je", "tu", "il", "elle", "nous", "vous", "ils", "elles", "te", "se", "moi", "toi", "lui",
        "mon", "mes", "ton", "tes", "sa", "ses", "notre", "nos", "votre", "vos", "leur", "leurs",
        "qui", "que", "quoi", "quel", "quelle", "quels", "quelles", "ne", "pas", "très", "tres", "bien",
        "mais", "donc", "ou", "où", "aussi", "encore", "déjà", "tout", "tous", "toute", "toutes", "même",
        "meme", "comme", "quand", "comment", "combien", "pourquoi", "depuis", "avant", "après", "afin",
        "selon", "chaque", "autre", "autres", "alors", "voici", "voilà", "être", "avoir", "avons", "avez",
        "ont", "suis", "sommes", "êtes", "été", "était", "fait", "faire", "peut", "pouvez", "pourriez",
        "pourrait", "voudrais", "voulons", "voulez", "veux", "veut", "souhaite", "souhaitons", "veuillez",
        "faut", "faudrait", "besoin", "bon", "bonne", "merci", "bonjour", "bonsoir", "salut", "oui", "non",
        "cordialement", "madame", "monsieur", "mesdames", "messieurs", "euh",
        # réponses courtes
        "parfait", "parfaite", "parfaitement", "exactement", "absolument", "effectivement", "volontiers", "désolé",
        "désolée",
        # abréviations de messagerie
        "slt", "svp", "stp", "bjr", "cdt", "cv", "dsl", "mrc",
    ),
    # Noms du métier en orthographe française : la darija les emploie aussi (« bghit ndir commande »),
    # d'où un poids faible. Les repères de temps (jours, mois, « demain »), les nombres écrits en toutes lettres
    # et les noms de quantité reconnaissent un message dicté ou télégraphique sans mot outil (« cinq mille
    # sachets jeudi »). Les mots de la darija qui s'en rapprochent sont écrits autrement (« ghda », « tlata »).
    weak=("la", "ma", "ta", "y", "plus", "car", "ai", "commande", "commandes", "livraison", "statut", "facture",
          "devis", "tarif", "tarifs", "demain", "hier", "aujourd", "semaine", "semaines", "mois", "jour", "jours",
          "heure", "heures", "prochain", "prochaine", "dernier", "dernière", "lundi", "mardi", "mercredi", "jeudi",
          "vendredi", "samedi", "dimanche", "janvier", "février", "mars", "avril", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre", "deux", "trois", "quatre", "cinq", "sept", "huit", "neuf",
          "dix", "cent", "cents", "mille", "quantité", "quantite"),
)
"""Français : mots outils, auxiliaires et formules de politesse (sans les mots partagés avec
l'anglais comme « on » ou « me »)."""

ENGLISH_MARKERS: Mapping[str, float] = _lexicon(
    strong=(
        "the", "and", "is", "are", "was", "were", "be", "been", "being", "am", "to", "of", "for", "with",
        "without", "from", "by", "at", "in", "into", "about", "this", "that", "these", "those", "there",
        "here", "it", "its", "you", "your", "we", "our", "they", "their", "them", "he", "she", "his", "her",
        "my", "what", "which", "who", "when", "where", "why", "how", "can", "could", "would", "should",
        "will", "shall", "do", "does", "did", "have", "has", "not", "no", "yes", "please", "pls", "plz",
        "hello", "hi", "hey", "thanks", "thank", "thx", "regards", "dear", "sincerely", "kindly", "any",
        "all", "some", "if", "so", "but", "like", "want", "need", "same", "usual", "now", "then", "only",
        "also", "just", "more", "much", "many", "again", "today", "tomorrow", "next", "send", "give", "tell",
        "get", "got", "let", "know", "as", "sir", "madam", "best", "morning", "afternoon", "evening",
        # réponses courtes
        "great", "perfect", "sorry", "wrong", "ahead", "sure", "noted", "cheers", "awesome", "good", "sounds",
        "yeah", "yep", "nope",
    ),
    # Noms et verbes du métier, en orthographe anglaise ; « fine » est aussi un adjectif français.
    # Les repères de temps et les nombres écrits en toutes lettres reconnaissent un message télégraphique.
    weak=("i", "an", "or", "had", "bag", "bags", "handle", "handles", "order", "orders", "ordered", "quote",
          "receipt", "purchase", "deliver", "delivered", "shipped", "received", "confirm", "confirmed", "fine",
          "regarding", "invoice", "payment", "delivery", "price", "prices", "yesterday", "week", "weeks", "month",
          "months", "day", "days", "hour", "hours", "monday", "tuesday", "wednesday", "thursday", "friday",
          "saturday", "sunday", "january", "february", "april", "june", "july", "september", "october",
          "november", "december", "one", "two", "three", "four", "five", "seven", "eight", "nine", "ten",
          "hundred", "thousand", "quantity", "pallet", "pallets"),
)
"""Anglais : mots outils, auxiliaires et formules de politesse (sans « us », code d'unité, ni
les mots partagés avec le français)."""

STANDALONE_DARIJA_ANSWERS: frozenset[str] = frozenset({"la", "lla"})
"""Réponses de darija qui forment un message à elles seules (« la » : non)."""

# Proclitiques arabes (préfixes collés au mot). L'article « al », seul ou précédé d'une
# particule, ne s'attache qu'aux noms : « الفين » (deux mille) ne contient pas « فين » (où).
_ARTICLE_PREFIXES: tuple[str, ...] = ("وال", "بال", "فال", "كال", "لل", "ال")
# Particules « wa » (et), « fa » (puis), « bi » (avec), « li » (pour), « ka » (comme).
_PARTICLE_PREFIXES: tuple[str, ...] = ("و", "ف", "ب", "ل", "ك")
_MIN_STEM_CHARS = 3

_SENTENCE_BOUNDARY = re.compile(r"[.!?:;\n]")
_FRENCH_ELISION = re.compile(r"(?<![^\W\d_])(?:c|d|j|l|m|n|s|t|qu|jusqu|lorsqu|puisqu)'(?=[^\W\d_])")
_ENGLISH_CONTRACTION = re.compile(r"(?<=[a-z])'(?:s|m|re|ll|ve|d|t)(?![^\W\d_])")
_FRENCH_ACCENT = re.compile("[àâäéèêëîïôöùûüçœæ]")
# Deuxième personne du pluriel du présent et de l'impératif (« envoyez », « validez », « annulez »),
# terminaison propre au français parmi les langues des clients.
_FRENCH_VERB_FORM = re.compile(r"^[a-z]{3,}ez$")
_TOKEN = re.compile(r"[^\W_]+")
_ARABIC_LETTER = re.compile(_code_point_class(
    (0x0620, 0x064A), (0x066E, 0x06D3), (0x06D5, 0x06D5), (0x06FA, 0x06FF), (0x0750, 0x077F),
    (0x08A0, 0x08FF),
))

AFFIX_WEIGHT = 0.5
"""Poids d'une élision française, d'une contraction anglaise ou d'un mot accentué."""

PROPER_NOUN_FACTOR = 0.5
"""Facteur appliqué à un marqueur de darija écrit avec une majuscule au milieu d'une phrase,
probablement un nom propre (« Snack Daba », « Boutique Lina »)."""

# Arabizi : poids d'un chiffre utilisé comme lettre. 3 (ع), 7 (ح) et 9 (ق) sont d'usage
# constant ; 2 (ء), 5 (خ) et 8 (غ) sont plus rares et plus souvent de vrais nombres.
_ARABIZI_DIGIT_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {"3": STRONG, "7": STRONG, "9": STRONG, "2": WEAK, "5": WEAK, "8": WEAK}
)
_ARABIZI_MIN_CHARS = 3
_ARABIZI_MAX_CHARS = 15
_ARABIZI_MAX_DIGITS = 2
_CONSECUTIVE_DIGITS = re.compile(r"\d\d")
# Nombre suivi d'une unité, d'un ordinal ou d'un mot de quantité : « 80g », « 3eme », « 2pcs ».
_NUMBER_WITH_UNIT = re.compile(
    r"^\d+(?:g|gr|grs|kg|mg|t|cm|mm|m|ml|cl|l|k|x|h|mn|min|s|j|u|p|pc|pcs|pce|pces|piece|pieces|unite|"
    r"unites|dh|dhs|mad|eur|euro|euros|usd|ht|ttc|er|re|e|eme|ieme|em|th|st|nd|rd|an|ans|mois|jour|jours|fois)$"
)
_NOT_ARABIZI: frozenset[str] = frozenset({"mp3", "b2b", "b2c", "p2p", "h2o", "co2"})

# Négation darija « ma … ch » : mots anglais ou français en « ch »/« sh » qui peuvent suivre « ma ».
_NOT_NEGATED: frozenset[str] = frozenset({
    "sandwich", "match", "lunch", "brunch", "flash", "cash", "touch", "coach", "sketch", "speech", "watch",
    "batch", "patch", "switch", "crash", "wash", "wach", "fish", "macintosh", "mackintosh",
})
_LATIN_NEGATION_MIN_CHARS = 6
_ARABIC_NEGATION_MIN_CHARS = 5

# Préfixes verbaux de la darija : futur « gha- » (« ghansift », j'enverrai) et présent « ka- »
# (« katsifto », vous envoyez), suivis du préfixe de personne n (je, nous), t (tu, elle, vous) ou
# y (il, ils) et d'un radical d'au moins trois lettres. Le futur ne forme presque aucun mot
# français ou anglais ; le présent en forme quelques-uns (« katana », « kanban »), exclus, et ne
# compte que pour :data:`WEAK`.
_LATIN_VERB_PREFIXES: Mapping[str, float] = MappingProxyType({"gha": STRONG, "ka": WEAK})
_LATIN_VERB_FORM = re.compile(r"^(gha|ka)[nty][a-z]{3,}$")
_NOT_LATIN_VERB_FORMS: frozenset[str] = frozenset({
    "katana", "katanas", "kayaks", "kayaking", "kanban", "kangourou", "kangourous", "kangaroo", "kangaroos",
    "kantien", "kantienne", "kantian", "katherine", "katrina", "kaynes", "ghanaian", "ghanaians", "ghaneen",
    "ghaneenne",
})
# Futur de la darija en lettres arabes : « غا » suivi du préfixe de personne (« غانصيفط », « غاتوصل »,
# « غايجيب ») ; « غاية » (but) et ses formes (« غايتنا ») ne sont pas des verbes.
_ARABIC_FUTURE_PREFIXES: tuple[str, ...] = ("غان", "غات", "غاي")
_NOT_ARABIC_FUTURE_STARTS: tuple[str, ...] = ("غايت", "غايه", "غايات")
_VERB_STEM_MIN_CHARS = 3


# --------------------------------------------------------------------------- indices


class _Token(NamedTuple):
    """Mot du texte en minuscules, avec l'indication « nom propre probable »."""

    text: str
    proper: bool


class _Evidence(NamedTuple):
    """Indices bruts (avant multiplication de la darija) par langue."""

    darija_latin: float = 0.0
    darija_arabic: float = 0.0
    msa: float = 0.0
    french: float = 0.0
    english: float = 0.0


def _arabizi_weight(token: str) -> float:
    """Poids arabizi d'un mot latin : chiffre-lettre à l'intérieur d'un mot (« 3afak », « ch7al »).

    Un chiffre placé seulement en fin de mot (« lbar7 ») ne compte que pour :data:`WEAK` : la
    même forme sert aux références (« ref3 », « lot5 »).
    """
    if not (_ARABIZI_MIN_CHARS <= len(token) <= _ARABIZI_MAX_CHARS) or not token.isascii():
        return 0.0
    digits = [char for char in token if char.isdigit()]
    letters = len(token) - len(digits)
    if not digits or len(digits) > _ARABIZI_MAX_DIGITS or letters < 2 or not token.isalnum():
        return 0.0
    if any(digit not in _ARABIZI_DIGIT_WEIGHTS for digit in digits):
        return 0.0
    if _CONSECUTIVE_DIGITS.search(token) or _NUMBER_WITH_UNIT.match(token) or token in _NOT_ARABIZI:
        return 0.0
    weight = max(_ARABIZI_DIGIT_WEIGHTS[digit] for digit in digits)
    final_digit_only = token[-1].isdigit() and not any(char.isdigit() for char in token[:-1])
    return min(weight, WEAK) if final_digit_only else weight


def _strip_accents(word: str) -> str:
    """Mot sans ses signes diacritiques (« quantité » → « quantite »)."""
    return "".join(char for char in unicodedata.normalize("NFD", word) if unicodedata.category(char) != "Mn")


def _latin_verb_prefix_weight(token: str) -> float:
    """Poids d'un verbe de darija reconnu par son préfixe de futur ou de présent (« ghansift », « kaydir »)."""
    match = _LATIN_VERB_FORM.match(token)
    if match is None or token in _NOT_LATIN_VERB_FORMS:
        return 0.0
    return _LATIN_VERB_PREFIXES[match.group(1)]


def _is_arabic_future(token: str) -> bool:
    """Futur de la darija en lettres arabes : « غانصيفط », « غاتوصل »."""
    return (token.startswith(_ARABIC_FUTURE_PREFIXES) and not token.startswith(_NOT_ARABIC_FUTURE_STARTS)
            and len(token) - len(_ARABIC_FUTURE_PREFIXES[0]) >= _VERB_STEM_MIN_CHARS)


def _is_latin_negation(token: str, previous: str) -> bool:
    """Négation darija « ma … ch » : « mabghitch », « makaynch », ou « ma » suivi de « jatch »."""
    if not token.endswith(("ch", "sh")) or token in _NOT_NEGATED or not token.isascii():
        return False
    if previous == "ma":
        return len(token) >= 3
    return token.startswith("ma") and len(token) >= _LATIN_NEGATION_MIN_CHARS


def _is_arabic_negation(token: str, previous: str) -> bool:
    """Négation darija « ما … ش » : « مابغيتش », ou « ما » suivi de « بقيتش »."""
    if not token.endswith("ش"):
        return False
    if previous == "ما":
        return len(token) >= 3
    return token.startswith("ما") and len(token) >= _ARABIC_NEGATION_MIN_CHARS


def is_darija_negation(word: str, previous: str = "") -> bool:
    """Vrai pour un mot nié par la négation darija « ma … ch » ou « ما … ش ».

    La négation entoure le verbe (« mabghitch », « matsiftch », « مابغيتش ») ou suit la particule
    « ma » / « ما » écrite à part (« ma tsiftohach », « ما تصيفطش ») ; ``previous`` est le mot qui
    précède ``word``. Les mots sont comparés sous la forme normalisée de l'étape 1 de la détection.
    """
    word, previous = _normalize_word(word), _normalize_word(previous)
    if _ARABIC_LETTER.search(word):
        return _is_arabic_negation(word, previous)
    return _is_latin_negation(word, previous)


def _stem_weight(token: str, lexicon: Mapping[str, float], nouns: frozenset[str] = frozenset()) -> float:
    """Poids du mot arabe débarrassé d'un proclitique : article devant un nom, particule devant tout mot."""
    for prefix in _ARTICLE_PREFIXES:
        stem = token[len(prefix):]
        if token.startswith(prefix) and stem in nouns and stem in lexicon:
            return lexicon[stem]
    for prefix in _PARTICLE_PREFIXES:
        stem = token[len(prefix):]
        if token.startswith(prefix) and len(stem) >= _MIN_STEM_CHARS and stem in lexicon:
            return lexicon[stem]
    return 0.0


def _arabic_token_weights(token: str) -> tuple[float, float]:
    """Poids darija et arabe standard d'un mot arabe.

    Un mot présent tel quel dans un lexique n'est pas décomposé : « لديك » (tu as, arabe
    standard) ne doit pas être lu comme « ل » + « ديك ».
    """
    darija = DARIJA_ARABIC_MARKERS.get(token, 0.0)
    msa = MSA_MARKERS.get(token, 0.0)
    if darija or msa:
        return darija, msa
    if _is_arabic_future(token):
        return STRONG, 0.0
    return _stem_weight(token, DARIJA_ARABIC_MARKERS, DARIJA_ARABIC_NOUNS), _stem_weight(token, MSA_MARKERS)


def _latin_darija_weight(token: _Token, previous: str) -> float:
    """Poids darija d'un mot latin (lexique, arabizi, préfixe verbal, négation), réduit pour un nom propre probable."""
    weight = max(
        DARIJA_LATIN_MARKERS.get(token.text, 0.0),
        _arabizi_weight(token.text),
        _latin_verb_prefix_weight(token.text),
        STRONG if _is_latin_negation(token.text, previous) else 0.0,
        STRONG if _LATIN_ARTICLE_LOANWORD.match(_strip_accents(token.text)) else 0.0,
    )
    return weight * PROPER_NOUN_FACTOR if token.proper else weight


def _latin_evidence(tokens: list[_Token], lowered_text: str) -> tuple[float, float, float]:
    """Indices de darija latine, de français et d'anglais apportés par les mots latins."""
    darija = french = english = 0.0
    previous = ""
    for token in tokens:
        darija += _latin_darija_weight(token, previous)
        french += FRENCH_MARKERS.get(token.text, 0.0)
        english += ENGLISH_MARKERS.get(token.text, 0.0)
        if _FRENCH_ACCENT.search(token.text):
            french += AFFIX_WEIGHT
        elif _FRENCH_VERB_FORM.match(token.text) and not token.proper and token.text not in FRENCH_MARKERS:
            french += AFFIX_WEIGHT
        previous = token.text
    french += AFFIX_WEIGHT * len(_FRENCH_ELISION.findall(lowered_text))
    english += AFFIX_WEIGHT * len(_ENGLISH_CONTRACTION.findall(lowered_text))
    return darija, french, english


def _arabic_evidence(tokens: list[str]) -> tuple[float, float]:
    """Indices de darija en lettres arabes et d'arabe standard apportés par les mots arabes."""
    darija = msa = 0.0
    previous = ""
    for token in tokens:
        token_darija, token_msa = _arabic_token_weights(token)
        darija += max(token_darija, STRONG if _is_arabic_negation(token, previous) else 0.0)
        msa += token_msa
        previous = token
    return darija, msa


def _tokens(text: str) -> list[_Token]:
    """Découpe le texte en mots (suites de lettres et de chiffres) mis en minuscules.

    Un mot qui commence par une majuscule sans être entièrement en majuscules, et qui ne
    commence pas une phrase, est marqué comme nom propre probable.
    """
    spaced = text.replace("'", " ")
    tokens: list[_Token] = []
    previous_end: int | None = None
    for match in _TOKEN.finditer(spaced):
        word = match.group()
        starts_sentence = previous_end is None or bool(_SENTENCE_BOUNDARY.search(spaced, previous_end, match.start()))
        proper = not starts_sentence and word[0].isupper() and not word.isupper()
        tokens.append(_Token(word.casefold(), proper))
        previous_end = match.end()
    return tokens


def _is_standalone_answer(latin_tokens: list[_Token], arabic_tokens: list[str]) -> bool:
    """Vrai pour un message réduit à une réponse de darija comme « la » (non)."""
    return bool(latin_tokens) and not arabic_tokens and all(
        token.text in STANDALONE_DARIJA_ANSWERS for token in latin_tokens
    )


def _collect_evidence(text: str) -> tuple[_Evidence, int, int]:
    """Indices par langue, nombre de lettres arabes et nombre de lettres latines du texte."""
    tokens = _tokens(text)
    arabic_tokens = [token.text for token in tokens if _ARABIC_LETTER.search(token.text)]
    latin_tokens = [token for token in tokens if not _ARABIC_LETTER.search(token.text) and _LETTER.search(token.text)]
    darija_latin, french, english = _latin_evidence(latin_tokens, text.casefold())
    if _is_standalone_answer(latin_tokens, arabic_tokens):
        darija_latin += STRONG
    darija_arabic, msa = _arabic_evidence(arabic_tokens)
    arabic_letters = sum(len(_ARABIC_LETTER.findall(token)) for token in arabic_tokens)
    latin_letters = sum(1 for token in tokens for char in token.text if _is_latin_letter(char))
    evidence = _Evidence(darija_latin, darija_arabic, msa, french, english)
    return evidence, arabic_letters, latin_letters


def _is_latin_letter(char: str) -> bool:
    """Vrai pour une lettre de l'alphabet latin (avec ou sans diacritique)."""
    code = ord(char)
    return char.isalpha() and (code < 0x0250 or 0x1E00 <= code <= 0x1EFF)


# --------------------------------------------------------------------------- décision


def _script(arabic_letters: int, latin_letters: int) -> str:
    """Écriture du texte d'après la part des lettres arabes."""
    total = arabic_letters + latin_letters
    if total == 0:
        return "none"
    share = arabic_letters / total
    if share >= 1 - MIXED_SCRIPT_MIN_SHARE:
        return "arabic"
    if share <= MIXED_SCRIPT_MIN_SHARE:
        return "latin"
    return "mixed"


def _scores(evidence: _Evidence) -> dict[str, float]:
    """Scores publiés dans le résultat (darija multipliée par :data:`DARIJA_WEIGHT`)."""
    return {
        "fr": round(evidence.french, 3),
        "en": round(evidence.english, 3),
        "ar": round(evidence.msa, 3),
        "darija_ar": round(DARIJA_WEIGHT * evidence.darija_arabic, 3),
        "darija_latin": round(DARIJA_WEIGHT * evidence.darija_latin, 3),
    }


def _confidence(support: float, competitor: float) -> float:
    """Confiance : part du score retenu face au meilleur concurrent, saturée par la quantité d'indices."""
    if support <= 0:
        return 0.0
    share = support / (support + max(competitor, 0.0))
    return round(share * (1.0 - math.exp(-support / EVIDENCE_SCALE)), 3)


def _decide(evidence: _Evidence, arabic_letters: int, latin_letters: int) -> tuple[str, float]:
    """Applique les règles de décision du module : code de langue et confiance."""
    darija = evidence.darija_latin + evidence.darija_arabic
    rivals = max(evidence.french, evidence.english, evidence.msa)
    arabic_majority = arabic_letters >= latin_letters
    if darija > 0 and (darija >= CLEAR_DARIJA_EVIDENCE or DARIJA_WEIGHT * darija >= rivals):
        if evidence.darija_arabic != evidence.darija_latin:
            code = "darija_ar" if evidence.darija_arabic > evidence.darija_latin else "darija_latin"
        else:
            code = "darija_ar" if arabic_majority else "darija_latin"
        return code, _confidence(DARIJA_WEIGHT * darija, rivals)
    weighted_darija = DARIJA_WEIGHT * darija
    if arabic_letters and arabic_majority:
        support = max(evidence.msa, ARABIC_DEFAULT_SUPPORT)
        return "ar", _confidence(support, max(evidence.french, evidence.english, weighted_darija))
    if evidence.french > 0 or evidence.english > 0:
        if evidence.french >= evidence.english:
            return "fr", _confidence(evidence.french, max(evidence.english, weighted_darija, evidence.msa))
        return "en", _confidence(evidence.english, max(evidence.french, weighted_darija, evidence.msa))
    if arabic_letters:
        support = max(evidence.msa, ARABIC_DEFAULT_SUPPORT)
        return "ar", _confidence(support, weighted_darija)
    return "unknown", 0.0


def _prepare(text: str) -> str:
    """Texte normalisé, débarrassé du bruit des emails, limité à :data:`MAX_ANALYSED_CHARS`."""
    normalized = _normalize(text[: 2 * MAX_ANALYSED_CHARS])
    cleaned = _strip_non_words(_strip_email_noise(normalized))
    return _squeeze_repeated_letters(cleaned)[:MAX_ANALYSED_CHARS]


def detect_language(text: str | None) -> LanguageResult:
    """Détecte la langue et l'écriture d'un message (méthode décrite dans la documentation du module).

    Args:
        text: message à analyser (email, message WhatsApp, transcription d'une note vocale).
            ``None`` et une chaîne vide donnent ``unknown``.

    Returns:
        Le code de langue, l'écriture, la confiance et le score de chaque langue.

    Raises:
        TypeError: ``text`` n'est ni une chaîne ni ``None``.
    """
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise TypeError(f"detect_language attend une chaîne de caractères, reçu {type(text).__name__}")
    evidence, arabic_letters, latin_letters = _collect_evidence(_prepare(text))
    script = _script(arabic_letters, latin_letters)
    if script == "none":
        code, confidence = "unknown", 0.0
    else:
        code, confidence = _decide(evidence, arabic_letters, latin_letters)
    logger.debug("Langue détectée : %s (écriture %s, confiance %.2f)", code, script, confidence)
    return LanguageResult(code=code, script=script, confidence=confidence, scores=_scores(evidence))


_REPLY_LANGUAGE_BY_CODE: Mapping[str, str] = MappingProxyType(
    {"darija_latin": "darija", "darija_ar": "darija", "ar": "ar"}
)


def reply_language(result: LanguageResult | str | None) -> str:
    """Langue de réponse de l'assistant pour une langue détectée.

    ``darija_latin`` et ``darija_ar`` donnent ``darija``, ``ar`` donne ``ar`` ; le français,
    l'anglais, une langue non identifiée et toute autre valeur donnent ``fr`` (l'assistant ne
    répond qu'en français, en darija et en arabe standard).

    Args:
        result: résultat de :func:`detect_language`, ou directement son code de langue.
    """
    code = result.code if isinstance(result, LanguageResult) else result
    if not isinstance(code, str):
        return "fr"
    return _REPLY_LANGUAGE_BY_CODE.get(code.strip().lower(), "fr")
