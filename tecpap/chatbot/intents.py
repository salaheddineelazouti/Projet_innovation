"""Détection déterministe de l'intention d'un message WhatsApp, sans modèle de langue.

L'intention est reconnue par mots-clés et expressions, en français, en anglais, en arabe
standard et en darija (lettres latines et arabes), avec le vocabulaire du jeu d'évaluation
``evaluation/dataset/messages.jsonl``. Le texte est d'abord ramené à sa forme de comparaison
(:func:`tecpap.extraction.rules.fold` : minuscules, sans accents ni signes diacritiques arabes,
chiffres arabo-indiens convertis), puis découpé en mots.

Intentions, testées dans cet ordre (la première qui s'applique l'emporte) :

1. ``other`` pour une tentative d'injection d'instructions (texte rédigé pour détourner le
   modèle de langue de sa tâche, repéré par :func:`tecpap.security.guardrails.inspect_input`)
   qui ne contient pas de commande ; une injection placée dans une commande valide donne
   ``order`` ou ``reorder`` ;
2. ``cancel`` : annulation (« annuler », « nlghi », « إلغاء ») ;
3. ``human`` : demande d'un interlocuteur (« conseiller », « nhder m3a chi insan », « مستشار ») ;
4. ``confirm_yes`` / ``confirm_no`` : réponse courte (au plus :data:`MAX_SHORT_WORDS` mots) faite
   uniquement de mots d'accord ou de refus et de formules de politesse (« oui », « ok c'est bon »,
   « wakha », « نعم » ; « non merci », « la, machi hakka », « لا ») ; un refus l'emporte sur un
   accord (« لا أؤكد ») ; un message qui contient un nombre (une correction probable) ou un point
   d'interrogation (« ah bon ? », « wakha ? ») n'est pas une simple réponse ;
5. ``greeting`` : salutation seule ou demande du menu (« bonjour », « salam labas 3likom ») ;
6. ``reorder`` : expression de renouvellement du catalogue (:func:`tecpap.extraction.rules.detect_reorder`) ;
7. ``status`` : suivi d'une commande (« où en est », « fin wslat », « حالة طلبي ») ;
8. ``order`` : bon de commande selon :func:`tecpap.extraction.rules.rule_based_extract`
   (quantité, ou produit avec un verbe de commande), simple demande de commander
   (« je veux commander », « bghit ncommandi »), ou produit du catalogue cité sans tournure
   interrogative (« des sachets fond plat ») ;
9. ``question`` : tournure interrogative (« ? », « wach », « هل », « est-ce que ») ou demande de
   renseignement, portant sur les produits, les prix, les délais ou leurs caractéristiques ; une
   demande de renseignement sans quantité reste une question même si elle cite un produit
   (« je voudrais des renseignements sur vos sachets ») ;
10. ``other`` : tout le reste.

Le mot « la » (« non » en darija, article en français) n'est un refus qu'en tête d'un message
court fait de mots de réponse (« la », « la, machi hakka », « la merci ») ; « là » accentué ne
l'est jamais.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

from tecpap.extraction.rules import fold, normalize_product_type, rule_based_extract
from tecpap.security.guardrails import inspect_input

logger = logging.getLogger(__name__)

__all__ = [
    "INTENTS",
    "MAX_SHORT_WORDS",
    "IntentResult",
    "detect_intent",
    "is_no",
    "is_yes",
]

INTENTS: tuple[str, ...] = (
    "confirm_yes", "confirm_no", "cancel", "human", "status", "reorder", "greeting", "question", "order", "other",
)
"""Intentions reconnues (mêmes valeurs que le champ ``intent`` du jeu d'évaluation)."""

MAX_SHORT_WORDS = 8
"""Nombre maximal de mots d'une réponse courte (accord, refus, salutation)."""

MAX_ANALYSED_CHARS = 5000
"""Longueur maximale de texte analysée."""


@dataclass(frozen=True)
class IntentResult:
    """Intention d'un message.

    Attributes:
        intent: une des valeurs de :data:`INTENTS`.
        matched: mot ou expression qui a décidé l'intention (pour le journal et les tests), ou
            ``None`` pour ``other``.
    """

    intent: str
    matched: str | None = None


# ---------------------------------------------------------------- lexiques

def _folded(words: Iterable[str]) -> frozenset[str]:
    return frozenset(fold(word) for word in words)


def _phrase_pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    """Expression régulière qui reconnaît une des expressions (comparées sous forme repliée), mot entier."""
    bodies = sorted({r"\s+".join(re.escape(part) for part in fold(phrase).split()) for phrase in phrases},
                    key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(bodies) + r")(?!\w)")


_TOKEN_RE = re.compile(r"[^\W_]+")
_DIGIT_RE = re.compile(r"\d")

_YES_WORDS = _folded((
    "oui", "ouais", "ouai", "ouii", "wi", "ok", "okay", "oki", "okey", "dac", "dacc", "parfait", "exact",
    "exactement", "confirme", "confirmé", "confirmée", "confirmer", "confirmons", "valide", "validé", "valider",
    "validez", "yes", "yep", "yeah", "yup", "sure", "correct", "wakha", "wakhka", "waxa", "wah", "iyeh",
    "iyah", "ayeh", "eyeh", "ah", "mzyan", "mzyane", "mezyan", "mzian", "safi", "tamam", "nichan",
    "نعم", "اه", "آه", "ايه", "إيه", "اييه", "واخا", "مزيان", "صافي", "اكيد", "أكيد", "موافق", "اوكي", "تمام",
    "أجل", "بالتأكيد", "أؤكد", "اؤكد", "مؤكد", "نأكد",
))
_YES_PHRASES = _phrase_pattern((
    "c'est bon", "c est bon", "ça marche", "ca marche", "d'accord", "d accord", "daccord", "dacord", "je confirme",
    "c'est ok", "c'est parfait",
    "vas-y", "allez-y", "bien sûr", "pas de problème", "sans problème", "go ahead", "sounds good",
    "that's right", "looks good", "all good", "kolchi mzyan", "hakka mzyan", "hakda mzyan", "ewa safi",
    "iwa safi", "يمكنك الإرسال",
))
_NO_WORDS = _folded((
    "non", "no", "nope", "nan", "machi", "ماشي", "لا", "لالا", "khata", "ghalat", "غلط", "خطأ", "incorrect",
    "faux", "wrong", "not",
))
_NO_PHRASES = _phrase_pattern((
    "pas d'accord", "c'est pas ça", "ce n'est pas ça", "c'est pas ca", "pas correct", "pas bon", "c'est faux",
    "machi hakka", "machi hakda", "machi hadi", "machi hadchi", "ماشي هادشي", "ماشي هكا", "لا أؤكد",
    "not correct", "that's wrong", "ma bghitch",
))
# Mots qui accompagnent une réponse courte sans en changer le sens.
_REPLY_FILLERS = _folded((
    "merci", "mercii", "beaucoup", "bien", "bon", "c", "est", "ça", "ca", "tout", "ce", "cela", "va", "pour",
    "moi", "nous", "svp", "stp", "please", "pls", "thanks", "thank", "you", "it", "is", "that", "s", "fine",
    "chokran", "choukran", "shukran", "chkran", "3afak", "afak", "baraka", "lahoufik", "llah", "allah", "ykhalik",
    "sift", "siftha", "siftoha", "sifto", "siftohom", "sir", "hakka", "hakda", "hadchi", "hadi", "dak", "dakchi",
    "kolchi", "a", "sidi", "khoya", "khouya", "lalla", "madame", "monsieur",
    "شكرا", "عافاك", "بارك", "الله", "فيك", "هادشي", "هكا", "هذا", "هذه", "الطلب", "الطلبية", "صيفط", "صيفطها",
    "ارسل", "أرسل", "من", "فضلك", "جزيلا",
))
_GREETING_WORDS = _folded((
    "bonjour", "bonsoir", "salut", "slt", "coucou", "hello", "hi", "hey", "salam", "slm", "salamo", "salamou",
    "salamu", "assalam", "assalamou", "assalamu", "alaykoum", "alaikoum", "alikoum", "3alaykoum", "3likom",
    "3likoum", "aleykoum", "alaykum", "ahlan", "marhba", "marhaba", "sba7", "sbah", "lkhir", "lkher", "msa",
    "good", "morning", "evening", "afternoon", "labas", "cv", "kidayr", "kidayra", "kidayrin", "dayr", "dayra",
    "kif", "bikhir", "hamdoulah", "hamdoullah", "lhamdoulah", "lhamdoulillah", "lah", "there", "all", "everyone",
    "le", "monde", "messieurs", "dames", "cher", "chere", "chers", "madame", "monsieur", "mme", "mr", "a",
    "khoya", "khouya", "sahbi", "sidi", "lalla",
    "السلام", "سلام", "عليكم", "مرحبا", "اهلا", "أهلا", "صباح", "مساء", "الخير", "لاباس",
    "الحمد", "لله", "بخير", "كيداير", "كيف", "داير", "دايرين", "حالك", "اخويا", "خويا",
))
_MENU_WORDS = _folded((
    "menu", "aide", "help", "start", "commencer", "options", "3awni", "مساعدة", "القائمة", "قائمة",
))

_CANCEL_PATTERN = _phrase_pattern((
    "annuler", "annule", "annulez", "annulation", "annulons", "cancel", "cancelled", "canceled", "cancellation",
    "nlghi", "lghi", "nlghiw", "lghiw", "lghih", "lghiha", "nlghiha", "nlghiwha", "battel", "battelha",
    "nbattel", "laisse tomber", "laissez tomber", "abandonner", "لغي", "نلغي", "الغي", "ألغي", "إلغاء", "الغاء",
    "تلغي", "نلغيو", "بطل", "نبطل",
))
_HUMAN_PATTERN = _phrase_pattern((
    "conseiller", "conseillère", "commercial", "commerciaux", "un humain", "une personne", "une vraie personne",
    "quelqu'un", "parler à", "parler a", "parler avec", "être rappelé", "etre rappele", "me rappeler",
    "rappelez-moi", "rappelez moi", "appelez-moi", "appelez moi", "service client", "opérateur", "agent",
    "nhder m3a", "nhdar m3a", "nhdr m3a", "nahder m3a", "nhder", "nhdar", "nkellem", "ntkellem", "nkallem",
    "insan", "machi robot", "bnadem", "3ayet lia", "3ayto lia", "3iyet lia", "human", "real person",
    "speak to", "talk to", "call me", "representative", "salesperson", "sales team",
    "نهضر", "نهدر", "نتكلم", "نكلم", "عيط ليا", "عيطو ليا", "اتصل بي", "اتصلوا بي", "إنسان", "انسان",
    "مستشار", "موظف", "خدمة العملاء", "بنادم",
))
_STATUS_PATTERN = _phrase_pattern((
    "où en est", "ou en est", "où en sont", "suivi de", "suivre ma commande", "suivre mes commandes", "statut",
    "status", "état de ma commande", "état de la commande", "état de notre commande", "état de mes commandes",
    "pas encore reçu", "toujours pas reçu", "jamais reçu", "mes commandes", "nos commandes",
    "update on", "any update", "tracking", "track my", "where is my order", "my orders",
    "fin wslat", "fin wsalt", "fin wslet", "fin wselt", "fin wsl", "mazal ma jat", "mazal ma jatch",
    "mazal ma wslat", "mazal ma wsalt", "mazal ma wslatni", "mazal ma wsltni", "mazal ma wslatnich",
    "ma wslatni", "ma wslatnich", "mazal ma jatni", "mazal ma jani", "commandes dyali",
    "les commandes dyali", "فين وصلات", "فين وصلت", "مازال ما", "حالة طلب", "حالة الطلب", "حالة طلبي",
    "حالة طلبيتي", "أين طلبي", "اين طلبي", "تتبع", "ما وصلنيش", "ما وصلاتنيش", "طلباتي",
))
_ORDER_VERB_PATTERN = _phrase_pattern((
    "commander", "passer une commande", "passer commande", "faire une commande", "nouvelle commande",
    "je veux commander", "ncommandi", "ndir commande", "ndir chi commande", "nsift commande", "place an order",
    "to order", "new order", "make an order", "نطلب", "أطلب", "اطلب", "طلب جديد", "طلبية جديدة", "نكوموندي",
))
_QUESTION_START = _phrase_pattern((
    "est-ce", "est ce", "quel", "quels", "quelle", "quelles", "combien", "comment", "pourquoi", "quand",
    "avez-vous", "avez vous", "vous avez", "proposez-vous", "proposez vous", "faites-vous", "faites vous",
    "vendez-vous", "vendez vous", "existe-t-il", "y a-t-il", "peut-on", "wach", "wash", "wech", "ch7al",
    "chhal", "chal", "kifach", "kifash", "3lach", "imta", "chno", "achno", "what", "how", "which", "when",
    "where", "why", "do you", "does", "is there", "are there", "can i", "can you", "could you",
    "واش", "شحال", "علاش", "كيفاش", "امتى", "متى", "هل", "ما هي", "ما هو", "ماذا", "كم", "أين", "كيف",
    "اشنو", "شنو", "اش",
))
_INFORMATION_PATTERN = _phrase_pattern((
    "je voudrais savoir", "je veux savoir", "j'aimerais savoir", "renseignement", "renseignements",
    "information", "informations", "parlez-moi", "parlez moi", "parle-moi", "parle moi", "dites-moi",
    "dites moi", "tell me about", "bghit n3ref", "bghit nswel", "bghit nsowel", "i would like to know",
    "i want to know", "أريد معرفة", "بغيت نعرف", "بغيت نسول", "استفسار",
))
_TOPIC_WORDS = _folded((
    "sac", "sacs", "sachet", "sachets", "emballage", "emballages", "kraft", "papier", "poignée", "poignées",
    "poignee", "poignees", "anse", "anses", "produit", "produits", "catalogue", "bag", "bags", "paper", "handle",
    "handles", "packaging", "product", "products", "sachiyat", "sachia", "sachiya", "sachiat", "grammage",
    "grammages", "laize", "impression", "imprimer", "logo", "couleur", "couleurs", "taille", "tailles",
    "dimension", "dimensions", "format", "formats", "fermeture", "gramme", "grammes", "colle", "size", "sizes",
    "color", "colors", "colour", "print", "printing", "prix", "tarif", "tarifs", "délai", "délais", "stock",
    "minimum", "price", "prices", "cost", "taman", "tamane", "thaman", "livrez", "livrer", "deliver",
    "commande", "commandes", "commander", "order", "ordering",
    "ساك", "ساشي", "ساشيات", "كيس", "أكياس", "اكياس", "كاغيط", "الكاغيط", "ورق", "الورق", "منتج", "منتجات",
    "طباعة", "لون", "ألوان", "حجم", "مقاس", "سعر", "ثمن", "مدة", "مقبض", "مقابض", "الساك", "طلب", "الطلب",
))
_LONE_NO = re.compile(r"^\W*la(?:\W*$|\s*[,.;!؟?،]|\s+(?:la|machi|merci|chokran|choukran|shukran|lla|no|non)\b)")


# ---------------------------------------------------------------- règles

def _tokens(folded: str) -> list[str]:
    return _TOKEN_RE.findall(folded)


def is_yes(text: str | None) -> bool:
    """Vrai pour une réponse courte d'accord (« oui », « wakha », « نعم », « ok c'est bon »)."""
    return detect_intent(text, suspicious=False).intent == "confirm_yes"


def is_no(text: str | None) -> bool:
    """Vrai pour une réponse courte de refus (« non », « la », « لا », « non merci »)."""
    return detect_intent(text, suspicious=False).intent == "confirm_no"


def _short_reply(raw: str, folded: str, language: str | None) -> IntentResult | None:
    """Accord ou refus exprimé par une réponse courte, ou ``None``."""
    if _DIGIT_RE.search(folded) or "?" in folded or "؟" in folded:
        return None
    tokens = _tokens(folded)
    if not tokens or len(tokens) > MAX_SHORT_WORDS:
        return None
    # Les refus sont retirés avant les accords : « pas d'accord » contient « d'accord ».
    without_no = _NO_PHRASES.sub(" ", folded)
    no_phrase = without_no != folded
    remaining = _YES_PHRASES.sub(" ", without_no)
    yes_phrase = remaining != without_no
    words = _tokens(remaining)
    lone_no = bool(_LONE_NO.match(folded)) and "là" not in raw.lower()
    no_words = [word for word in words if word in _NO_WORDS]
    yes_words = [word for word in words if word in _YES_WORDS]
    if language in ("fr", "en") and yes_words == ["ah"] and not yes_phrase:
        yes_words = []
    unknown = [word for word in words
               if word not in _NO_WORDS and word not in _YES_WORDS and word not in _REPLY_FILLERS
               and not (word == "la" and lone_no)]
    if unknown:
        return None
    if no_phrase or no_words or lone_no:
        return IntentResult("confirm_no", (no_words or ["la"])[0] if not no_phrase else "refus")
    if yes_phrase or yes_words:
        return IntentResult("confirm_yes", yes_words[0] if yes_words else "accord")
    return None


def _greeting(folded: str) -> IntentResult | None:
    tokens = _tokens(folded)
    if not tokens or len(tokens) > MAX_SHORT_WORDS or _DIGIT_RE.search(folded.replace("3", "")):
        return None
    menu = [token for token in tokens if token in _MENU_WORDS]
    if menu and all(token in _MENU_WORDS or token in _GREETING_WORDS or token in _REPLY_FILLERS for token in tokens):
        return IntentResult("greeting", menu[0])
    if all(token in _GREETING_WORDS for token in tokens):
        return IntentResult("greeting", tokens[0])
    return None


def _question(folded: str, text: str) -> IntentResult | None:
    interrogative = "?" in folded or "؟" in folded or _QUESTION_START.search(folded) is not None
    information = _INFORMATION_PATTERN.search(folded)
    if not interrogative and information is None:
        return None
    topic = next((token for token in _tokens(folded) if token in _TOPIC_WORDS), None)
    if topic is None and normalize_product_type(text) is not None:
        topic = "produit"
    if topic is None:
        return None
    return IntentResult("question", topic)


def _search(pattern: re.Pattern[str], folded: str) -> str | None:
    match = pattern.search(folded)
    return match.group(0) if match else None


def detect_intent(text: str | None, language: str | None = None, *,
                  suspicious: bool | None = None) -> IntentResult:
    """Intention d'un message, selon les règles de la documentation du module.

    Args:
        text: message du client (texte, transcription ou texte lu dans un média).
        language: code de langue détecté (``fr``, ``darija_latin``…), facultatif ; il lève
            l'ambiguïté de l'interjection « ah », qui n'est un accord qu'en darija.
        suspicious: résultat déjà connu du contrôle des injections ; ``None`` le calcule avec
            :func:`tecpap.security.guardrails.inspect_input`.

    Returns:
        L'intention et le mot ou l'expression qui l'a décidée.
    """
    if not isinstance(text, str) or not text.strip():
        return IntentResult("other")
    text = text[:MAX_ANALYSED_CHARS]
    folded = fold(text)
    extraction = rule_based_extract(text)

    if suspicious is None:
        suspicious = inspect_input(text).is_suspicious
    if suspicious:
        if extraction["is_reorder"]:
            return IntentResult("reorder", "renouvellement")
        if extraction["est_bon_commande"]:
            return IntentResult("order", "commande")
        return IntentResult("other", "injection")

    for intent, pattern in (("cancel", _CANCEL_PATTERN), ("human", _HUMAN_PATTERN)):
        matched = _search(pattern, folded)
        if matched:
            return IntentResult(intent, matched)

    for rule in (lambda: _short_reply(text, folded, language), lambda: _greeting(folded)):
        result = rule()
        if result is not None:
            return result

    if extraction["is_reorder"]:
        return IntentResult("reorder", "renouvellement")
    matched = _search(_STATUS_PATTERN, folded)
    if matched:
        return IntentResult("status", matched)
    if extraction["est_bon_commande"]:
        if "quantite" not in extraction and _INFORMATION_PATTERN.search(folded):
            # « je voudrais des renseignements sur vos sachets » : demande d'information, sans quantité.
            return IntentResult("question", _search(_INFORMATION_PATTERN, folded))
        return IntentResult("order", "quantité" if "quantite" in extraction else "produit")
    interrogative = _QUESTION_START.search(folded) is not None or "?" in folded or "؟" in folded
    matched = _search(_ORDER_VERB_PATTERN, folded)
    if matched and not interrogative:
        return IntentResult("order", matched)
    if not interrogative and _INFORMATION_PATTERN.search(folded) is None and normalize_product_type(text):
        # Un produit cité sans question ni demande de renseignement (« des sachets fond plat »,
        # « commande de sacs premium ») commence une commande : les champs manquants seront demandés.
        return IntentResult("order", "produit")
    result = _question(folded, text)
    if result is not None:
        return result
    return IntentResult("other")
