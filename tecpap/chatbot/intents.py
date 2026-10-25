"""Détection déterministe de l'intention d'un message WhatsApp, sans modèle de langue.

L'intention est reconnue par mots-clés, familles d'expressions et formes verbales, en français, en
anglais, en arabe standard et en darija (lettres latines et arabes). Les valeurs d'intention sont
celles du champ ``intent`` de ``evaluation/dataset/messages.jsonl``. Seule la partie du message écrite
par l'expéditeur est analysée (:func:`tecpap.nlp.language.message_body` retire les citations, le
message d'origine et la signature d'un email) ; quand cette partie ne contient aucune demande et que
le texte transféré contient une commande, la commande est retenue. Le texte est ensuite ramené à sa
forme de comparaison (:func:`tecpap.extraction.rules.fold` : minuscules, sans accents ni signes
diacritiques arabes, chiffres arabo-indiens convertis ; une lettre répétée plus de deux fois est
réduite à une seule : « ouiii », « wakhaaa »), puis découpé en mots. Dans les expressions, une
espace, une apostrophe ou un trait d'union se correspondent (« vas-y », « vas y » ; « c'est bon »,
« cest bon »).

Priorité entre les intentions : les règles sont appliquées dans cet ordre et la première qui
s'applique l'emporte. Les intentions qui déclenchent une action de l'assistant sans confirmation du
client (annulation, mise en relation avec l'équipe commerciale) passent avant les réponses courtes ;
une négation placée juste avant l'expression (« n'annulez pas », « ne m'appelez pas », « don't
cancel ») retire à l'expression son sens.

1. ``other`` pour une tentative d'injection d'instructions (texte rédigé pour détourner le
   modèle de langue de sa tâche, repéré par :func:`tecpap.security.guardrails.inspect_input`)
   qui ne contient pas de commande ; une injection placée dans une commande valide donne
   ``order`` ou ``reorder`` ;
2. ``cancel`` : verbe d'annulation reconnu par son radical (« annuler », « annulez », « cancel »,
   « nlghi », « nannuli », « battlo », « لغيها », « إلغاء », « بطلو »), « renoncer », « ne pas
   donner suite », « laisse tomber », ordre d'arrêt portant sur la commande (« arrêtez la
   commande », « stoppez la fabrication de ma commande », « وقف الطلبية »), ou, dans un message
   qui ne cite ni produit ni quantité, renoncement (verbe de volonté ou de besoin employé avec
   « ne … plus » : « je n'en veux plus », « on n'en a plus besoin », « no longer need », « ma b9itch
   baghi », « ma bghitch had lcommande », « لم أعد بحاجة ») et retrait de la commande (« supprimez
   ma commande », « forget the order », « nsa la commande », « بطل الكوموند ») ; la formule
   « annule et remplace » d'un bon de commande rectificatif n'est pas une annulation ;
3. ``human`` : demande explicite d'un interlocuteur : rappel (« rappelez-moi », « qu'on me
   rappelle », « call me », « 3ayet lia », « اتصلوا بي », « un commercial peut me contacter »,
   « veuillez me contacter »), refus du robot (« machi robot », « je ne veux pas parler à un
   robot »), verbe de parole suivi d'une personne (« parler à un conseiller », « nhder m3a chi
   wa7ed », « أتحدث مع موظف »), personne indéterminée suivie du verbe de parole ou de contact
   (« y a-t-il quelqu'un pour me parler », « wach kayn chi wa7ed nhder m3ah », « chi wa7ed
   ykellemni »), souhait suivi d'une personne (« je veux un conseiller », « bghit insan », « puis-je
   avoir un interlocuteur »), mise en relation (« passez-moi un commercial », « please connect me
   with a sales representative », « sifto lia chi commercial »), ou message court fait d'un nom de
   personne (« conseiller », « مستشار »). Un nom seul dans une phrase (« service commercial » dans
   une signature) ne suffit pas, et « nhder m3ak » (parler avec l'assistant) ne désigne personne.
   « Me rappeler le prix » demande un rappel d'information, pas un appel. Une commande complète
   (quantité et produit) ou un renouvellement l'emporte sur une demande de rappel : la commande
   est enregistrée et l'équipe commerciale, qui valide chaque commande, rappelle le client ;
4. ``confirm_yes`` / ``confirm_no`` : réponse courte (au plus :data:`MAX_REPLY_WORDS` mots, une
   salutation d'ouverture comme « bonjour, » ou « السلام عليكم، » étant retirée) dont chaque
   mot est un mot ou une expression d'accord ou de refus, un verbe d'envoi ou de lancement, le
   nom de la commande, une formule de politesse ou de clôture, ou un mot grammatical (pronom,
   article, préposition, auxiliaire, verbe de permission) : « oui », « ok c'est bon », « parfait,
   je valide », « oui, vous pouvez envoyer », « wakha », « ewa sir 3la llah », « تمام », « no
   problem » ; « non merci », « la, machi hakka », « no thanks », « لا ». Un mot de contenu
   inconnu (nom, adjectif) empêche cette lecture, car il peut porter une modification (« oui
   mais en blanc », « oui envoyez le camion »). Précisions :

   - un verbe d'envoi seul vaut accord (« envoyez », « sift », « send it », « صيفط ») ; « noté »
     et « noted » reconnaissent la réception du message sans valoir accord ;
   - le refus est reconnu par les mots de refus, de report et d'attente (« non », « pas encore »,
     « attendez », « stop », « mazal », « wait », « un instant ») et par la négation de chaque
     langue : « ne … pas » (« ne l'envoyez pas », « ce n'est pas correct »), « don't », « ma … ch »
     de la darija (« matsiftch », « ma tsiftohach », « ما تصيفطش », voir
     :func:`tecpap.nlp.language.is_darija_negation`), « ليس » et « لا » suivi d'un verbe
     (« لا ترسلوا ») ;
   - les émojis d'approbation (👍, 👌, ✅) et de refus (👎, ❌) comptent comme des mots ;
   - un refus l'emporte sur un accord (« لا أؤكد ») ; les expressions qui contiennent une négation
     sans être des refus (« pas de problème », « machi mochkil », « لا بأس ») sont reconnues
     avant les mots de refus ;
   - un message qui contient un nombre (une correction probable) ou un point d'interrogation
     (« ah bon ? », « wakha ? ») n'est pas une simple réponse ; un chiffre collé à des lettres est
     une lettre d'arabizi (« 3afak », « la2 ») et non un nombre ;
   - un mot de contraste (« mais », « but », « ولكن ») annonce une réserve : « oui mais … » n'est
     pas un accord, alors que « merci mais non » reste un refus ;
   - une négation qui porte sur l'état attendu d'une commande (« mazal ma wslatnich », « n'est pas
     arrivée ») est un suivi (règle 7), pas un refus ;
   - un report poli de la décision est un refus, même dans une phrase longue (« je préfère
     attendre », « let me think », « je vais d'abord vérifier avec mon associé », « avant de
     valider », « خليني نفكر ») ;
5. ``greeting`` : salutation seule, éventuellement suivie d'une question de politesse, d'un
   vœu ou d'un mot grammatical, ou demande du menu (« bonjour », « salam labas 3likom », « salut,
   ça va ? », « salut, bonne journée », « kidayr khoya labas ? 3afak »). Un vœu de clôture sans
   salutation (« bonne journée ») n'en est pas une, et « merci pour votre aide » n'est pas une
   demande d'aide ;
6. ``reorder`` : expression de renouvellement du catalogue (:func:`tecpap.extraction.rules.detect_reorder`) ;
7. ``status`` : suivi d'une commande. Une question de suivi explicite l'emporte sur une commande
   citée dans le même message : la quantité et le produit d'une question de suivi décrivent la
   commande déjà passée (« où en est notre commande de 10 000 sachets ? »). Le suivi est reconnu :

   - par une expression (« où en est », « fin wslat », « حالة طلبي ») ou par un état attendu et
     pas encore atteint (« toujours pas reçu », « mazal ma wslat », « لم أستلم ») ;
   - par une référence de commande (« CMD-2026-0912 », « n° 4512 », « la commande 1203 »,
     « رقم 45 »), un possessif (« ma commande », « my order », « my package », « للكوموند
     ديالي », « طلبيتي ») ou une subordonnée (« les sacs que j'ai commandés », « الأكياس التي
     طلبتها »), accompagnés d'une question, directe ou indirecte (« je voudrais savoir si … »),
     courte (les formules de politesse ne comptent pas) ou portant sur un état, un moment ou une
     demande de nouvelles (« est partie », « shipped », « wajda », « خرجات », « quand », « des
     nouvelles ») ; une référence seule dans un message court suffit. Un chiffre seul après « n° »
     (« n°4 ») est le choix d'un menu numéroté, pas une référence, et le nombre qui suit
     « commande » n'en est pas une quand il est la quantité commandée (« commande 5000 sachets ») ;
   - dans la même proposition que la désignation de la commande, par une demande polie sans
     question (« date de livraison de ma commande svp », sauf si le message cite une quantité :
     c'est alors un bon de commande), par un état nié (« ma commande n'est pas arrivée », « my
     order hasn't arrived », « ma wslatch »), par une attente prolongée (« j'attends ma commande »,
     « still waiting for my order », « كنتسنى الكوموند ») ou par un retard (« ma commande est en
     retard », « تأخرت طلبيتي ») ;
   - par un nom défini sans possessif (« la commande », « lcommande », « الطلبية ») dans une
     question avec un verbe d'état conjugué (« imta ghatwsel lcommande? », « هل تم شحن
     الطلبية؟ »), sans quantité, sans tournure hypothétique (« si je commande lundi… ») ni
     question de prix ou de délai, qui portent sur une commande future.

   Les verbes d'état sont reconnus par leur radical et leurs préfixes de temps et de personne
   (darija « gha- », « ka- » ; « wslat », « ghatwsel », « khrjat », « tsiftat », « غاتوصل »,
   « تصيفطات » ; arabe « سيصل », « أستلم ») ; une demande de modification (« modifier ma commande
   CMD-12 ») ou de passer commande (« passer ma commande ») n'est pas un suivi ;
8. ``order`` :

   - bon de commande selon :func:`tecpap.extraction.rules.rule_based_extract` (quantité, ou
     produit avec un verbe de commande), sauf une demande de renseignement sans quantité
     (« je voudrais des renseignements sur vos sachets ») et une question de prix, de délai ou
     de disponibilité sans verbe d'achat (« combien coûtent 5000 sachets ? », « peut-on commander
     moins de 1000 pièces ? », « pouvez-vous m'envoyer un devis pour 5000 sachets ? ») ;
   - verbe d'achat à la première personne ou à l'impératif, avec ses pronoms compléments
     (« je prends », « il nous faut », « envoyez-m'en », « bghit », « sifto lia », « نريد »,
     « I need », « I just want ») suivi d'un produit, d'un nom de sac ou d'une quantité, même si
     le message pose aussi une question (« Vous faites les sacs à poignées plates ? Si oui je vous
     en prends 5 000 ») : la commande l'emporte sur la question, que l'équipe commerciale traite
     en validant la commande. Un souhait suivi d'un verbe de communication (« je souhaite vous
     proposer nos services », « je voudrais parler de … ») n'est pas un achat ;
   - demande de fourniture à la forme polie (« pouvez-vous me passer 2000 sacs ? », « merci de
     nous livrer 500 sachets »), sauf si le message demande un prix, un délai ou une disponibilité ;
   - simple demande de commander sans tournure interrogative (« je veux commander »,
     « bghit ncommandi »), ou produit du catalogue cité sans tournure interrogative
     (« des sachets fond plat ») ;
9. ``question`` : tournure interrogative (« ? », « wach », « هل », « est-ce que », « où est ») ou
   demande de renseignement (« renseignements », « infos », « bghit n3ref », « envoyez-moi vos
   tarifs ») portant sur les produits, les prix, les délais, la livraison ou leurs
   caractéristiques ; le sujet est cherché sans marque de pluriel (« colours ») ni affixes arabes
   (« المقاسات »), et un verbe d'envoi au présent d'habitude de la darija (« wach katsifto l
   Agadir? », « wach kat-livriw l Marrakech? ») pose une question de livraison. Une demande de
   coordonnées (« les numéros des autres clients », « contact details », « أرقام الهواتف ») ne
   porte pas sur les produits : elle donne ``other`` ;
10. ``confirm_no`` quand aucune règle précédente ne s'applique et que le message ne contient ni
    nombre, ni question, ni élément de commande (« non, c'est pour la Boulangerie Nour » rectifie le
    client de la commande) :

    - souhait de modifier la commande en cours sans en citer la valeur voulue (« je préfère
      modifier la quantité », « I want to change something »), sauf si le message commence par
      un accord ou désigne une commande déjà passée ;
    - refus placé en tête d'un message court et suivi d'une explication (« non, ce n'est pas la
      bonne quantité », « no, that's not what I ordered », « erreur, ce n'est pas le bon
      produit ») : le premier mot est un mot de refus, une expression de refus qui n'exprime pas
      une volonté (« je ne veux pas un appel » n'en est pas une) ou une copule niée (« ce n'est
      pas », « that's not »), après des hésitations ou des adverbes d'atténuation (« hmm non »,
      « plutôt non ») ;
11. ``other`` : tout le reste.

Les accords et les refus sont traités de façon asymétrique : un refus suivi de mots inconnus
reste un refus (règle 10), alors qu'un accord suivi d'un mot de contenu inconnu ne l'est pas. Un
faux accord ferait enregistrer une commande ; un faux refus ne fait que redemander au client la
modification voulue.

Le mot « la » (« non » en darija, article en français) n'est un refus qu'en tête d'un message,
seul, suivi d'une ponctuation ou d'un mot de darija qui accompagne un refus (« la », « la, machi
hakka », « la 3afak », « la mabghitch ») ; « là » accentué ne l'est jamais, et la langue détectée
en darija en lettres latines suffit à lever le doute en tête de message (« la khass nbeddel chi
haja »). « ماشي » seul est un refus (« pas », « ce n'est pas ») ; suivi de « مشكل » il forme « pas
de problème », un accord.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tecpap.extraction.rules import fold, normalize_product_type, rule_based_extract
from tecpap.nlp.language import is_darija_negation, message_body
from tecpap.security.guardrails import inspect_input

logger = logging.getLogger(__name__)

__all__ = [
    "INTENTS",
    "MAX_REPLY_WORDS",
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
"""Nombre maximal de mots d'une réponse courte (accord, refus, salutation, demande d'un interlocuteur)."""

MAX_REPLY_WORDS = 12
"""Nombre maximal de mots d'une réponse d'accord ou de refus : chaque mot étant un mot d'accord, de refus ou un
mot grammatical, une réponse plus longue que :data:`MAX_SHORT_WORDS` (« tout est correct, vous pouvez lancer la
commande, merci beaucoup ») reste une simple réponse."""

MAX_ANALYSED_CHARS = 5000
"""Longueur maximale de texte analysée."""

SHORT_FOLLOW_UP_WORDS = 5
"""Nombre maximal de mots d'un suivi réduit à une référence ou à « ma commande ? »."""

CONTACT_WINDOW_WORDS = 6
"""Nombre maximal de mots entre un verbe de parole et la personne demandée (« parler à un conseiller »)."""

REFUSAL_OPENING_MAX_WORDS = 12
"""Nombre maximal de mots d'un refus suivi d'une explication (« non, ce n'est pas la bonne quantité »)."""


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


# ---------------------------------------------------------------- outils de construction

def _folded(words: Iterable[str]) -> frozenset[str]:
    return frozenset(fold(word) for word in words)


_SPACE_SEPARATOR = r"[\s'\-]+"
_JOINED_SEPARATOR = r"[\s'\-]*"


def _phrase_body(phrase: str) -> str:
    """Corps d'expression régulière d'une expression repliée.

    Une espace devient une suite d'au moins un séparateur (espace, apostrophe, trait d'union) ;
    une apostrophe ou un trait d'union devient une suite éventuellement vide de séparateurs.
    """
    words = fold(phrase).split()
    parts = []
    for word in words:
        pieces = re.split(r"['\-]", word)
        parts.append(_JOINED_SEPARATOR.join(re.escape(piece) for piece in pieces if piece))
    return _SPACE_SEPARATOR.join(parts)


def _alternation(phrases: Iterable[str]) -> str:
    bodies = sorted({_phrase_body(phrase) for phrase in phrases}, key=len, reverse=True)
    return "(?:" + "|".join(bodies) + ")"


def _phrase_pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    """Expression régulière qui reconnaît une des expressions (comparées sous forme repliée), mot entier."""
    return re.compile(r"(?<!\w)" + _alternation(phrases) + r"(?!\w)")


def _sequence_pattern(first: Iterable[str], second: Iterable[str], gap: int) -> re.Pattern[str]:
    """Expression de ``first`` suivie, à au plus ``gap`` mots d'écart, d'une expression de ``second``."""
    return re.compile(
        r"(?<!\w)" + _alternation(first) + r"(?:\W+[^\W_]+){0," + str(gap) + r"}?\W+" + _alternation(second)
        + r"(?!\w)"
    )


_TOKEN_RE = re.compile(r"[^\W_]+")
# Séparateur de propositions : fin de phrase, point-virgule, saut de ligne.
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;\n؟]+")
# Lettre répétée plus de deux fois (« ouiii », « wakhaaa »), réduite à une seule.
_REPEATED_LETTER_RE = re.compile(r"([^\W\d_])\1{2,}")
# Nombre : au moins deux chiffres consécutifs, ou un chiffre isolé. Un chiffre seul collé à des
# lettres est une lettre d'arabizi (« 3afak », « m3a », « la2 »).
_NUMBER_RE = re.compile(r"\d{2,}|(?<![^\W\d_])\d(?![^\W\d_])")
_QUESTION_MARKS = ("?", "؟")

# ---------------------------------------------------------------- accord et refus

_YES_WORDS = _folded((
    # français
    "oui", "ouais", "ouai", "ouii", "wi", "ok", "okay", "oki", "okey", "dac", "dacc", "parfait", "parfaitement",
    "exact", "exactement", "confirme", "confirmé", "confirmée", "confirmer", "confirmons", "confirmez", "valide",
    "validé", "validée", "valider", "validez", "validons", "accepte", "accepté", "acceptons", "approuve", "approuvé",
    "super", "nickel", "impeccable", "génial", "top", "absolument", "certainement", "évidemment", "volontiers",
    "banco", "entendu", "ouaip", "affirmatif", "voilà", "cbon", "cbn", "dakor",
    # anglais
    "yes", "yep", "yeah", "yup", "sure", "correct", "fine", "perfect", "great", "agreed", "alright",
    "definitely", "certainly", "confirmed", "approved", "confirm", "approve", "accept", "accepted", "agree",
    "affirmative", "roger", "absolutely", "totally", "indeed",
    # darija en lettres latines
    "wakha", "wakhka", "wakhkha", "waxa", "wah", "iyeh", "iyah", "ayeh", "eyeh", "ah", "ewa", "iwa", "ewah",
    "iwah", "aywa", "aiwa", "mzyan", "mzyane", "mezyan", "mzian", "mzyana", "safi", "tamam", "temam", "nichan",
    "na3am", "n3am", "yallah", "yalah", "yala",
    # darija en lettres arabes et arabe standard
    "نعم", "اه", "آه", "ايه", "إيه", "اييه", "ايوا", "إيوا", "إوا", "اوا", "ايوه", "أيوه", "واخا", "مزيان", "مزيانة",
    "صافي",
    "اكيد", "أكيد", "موافق", "موافقة", "أوافق", "نوافق", "اوكي", "تمام", "أجل", "بلى", "حسنا", "طيب", "صحيح",
    "ممتاز", "بالتأكيد", "أؤكد", "اؤكد", "نؤكد", "مؤكد", "نأكد", "نيشان", "يالاه", "يلاه", "يلا",
))
_YES_PHRASES = _phrase_pattern((
    # français
    "c'est bon", "ça marche", "ça roule", "d'accord", "dacord", "d'acc", "je confirme", "on confirme",
    "nous confirmons", "je valide", "on valide", "nous validons", "c'est validé", "c'est confirmé", "j'accepte",
    "on accepte", "j'approuve", "c'est ok", "c'est parfait", "c'est correct", "c'est juste", "c'est ça",
    "tout à fait", "bon pour accord", "c bon", "c ok", "on est bon", "on est ok", "on est d'accord",
    "nous sommes d'accord",
    "c'est bien ça", "c'est bien cela", "c'est cela", "c'est exactement ça", "pas mal",
    "c'est tout bon", "tout bon", "tout est bon", "tout est correct", "tout est ok", "ça me va", "ça nous va",
    "ça me convient", "ça nous convient", "ça convient", "très bien", "vas-y", "allez-y", "bien sûr",
    "avec plaisir", "pas de problème", "pas de problèmes", "pas de souci", "pas de soucis", "aucun problème",
    "aucun souci", "sans problème", "sans souci",
    # anglais
    "go ahead", "go for it", "please proceed", "sounds good", "that's right", "that's correct", "that works",
    "that is right", "that is correct", "that is fine", "it's right", "it's correct", "it's fine", "it's good",
    "it's perfect", "it's ok", "this is right", "this is correct", "this is fine", "that's fine", "that's good",
    "that's perfect", "works for me", "fine by me", "fine with me", "ok with me",
    "ok for me", "okay with me", "looks good", "all good", "all right", "of course", "no problem", "no problems",
    "no worries", "fair enough", "sounds great", "looks great", "looks fine",
    # darija en lettres latines
    "kolchi mzyan", "hakka mzyan", "hakda mzyan", "ewa safi", "iwa safi", "sir 3la llah", "sir 3la lah",
    "sir 3la allah", "sir 3lallah", "siri 3la llah", "siro 3la llah", "machi mochkil", "machi mouchkil",
    "machi mochkila", "machi mushkil", "ma kayn mochkil", "ma kayn mouchkil", "makayn mochkil",
    "makayn mouchkil", "makaynch mochkil", "ma kaynch mochkil", "hadchi howa", "hada howa", "hadak howa", "dak howa",
    "hadik hiya", "dik hiya", "howa hadak", "hiya hadik",
    "hadi hiya",
    # darija en lettres arabes et arabe standard
    "سير على الله", "سيري على الله", "سيرو على الله", "على بركة الله", "ماشي مشكل", "ماشي مشكيل", "ما كاين مشكل",
    "ماكاين مشكل", "ماكاينش مشكل", "ما كاينش مشكل", "لا مشكلة", "لا مشكل", "لا مانع", "لا بأس", "كلشي مزيان",
    "هادشي هو", "هو هاداك", "هي هاديك",
    "بكل سرور", "يمكنك الإرسال", "كل شيء صحيح", "كل شيء تمام", "كل شيء مزيان", "كل شيء جيد",
))
# Graphies variables de mots d'accord de la darija en lettres latines : « wakha », « wakkha », « waxxa »,
# « wa5a » ; « mzyan », « mezyan », « mzian » ; « safi », « safy » ; « tamam », « tmam » ; « iyeh »,
# « eyeh » ; « ewa », « iwa », « aywa ».
_YES_SPELLING_RE = re.compile(
    r"^(?:wa(?:k+h*|x+|5+)+a+h?|me?z[iy]+e?a?n[ae]?|saf[iy]+|t[ae]?ma+m|[iea][yi][ea]h|(?:e|i|ay|ai)w+a+h?)$"
)
_NO_WORDS = _folded((
    "non", "no", "nope", "nan", "nah", "never", "jamais", "incorrect", "faux", "erreur", "wrong", "not", "pas",
    "refuse", "refusé", "machi", "mashi", "khata", "ghalat", "ghalta", "lla", "laa", "la2",
    "ماشي", "لا", "لالا", "كلا", "غلط", "غالط", "خطأ", "أرفض", "ارفض", "نرفض", "مرفوض", "ليس", "ليست", "لست",
    "لسنا",
    # demande d'attendre ou d'arrêter : la décision est suspendue (« attendez », « wait », « stop », « tsenna »,
    # « انتظر »)
    "attendez", "attends", "patientez", "wait", "hold", "stop", "stoppez", "arretez", "arrete", "halte", "tsenna",
    "tsennaw", "tsena", "stanna", "stennaw", "tsanna", "tsannaw", "تسنى", "تسناو", "انتظر", "انتظروا", "انتظرو",
    "توقف", "توقفوا", "قف", "انتظار", "الانتظار", "lala", "lalala", "nn",
    # « pas encore » en darija et en arabe (« la, mazal », « مازال »)
    "mazal", "mazala", "ba9i", "مازال", "مزال", "باقي",
))
_NO_PHRASE_LIST = (
    # français
    "pas d'accord", "c'est pas ça", "ce n'est pas ça", "pas correct", "pas bon", "c'est faux", "pas du tout",
    "absolument pas", "surtout pas", "pas maintenant", "pas pour le moment", "pas pour l'instant", "je refuse",
    "je ne confirme pas", "je ne valide pas", "ne confirmez pas", "ne validez pas", "c'est une erreur",
    "il y a une erreur", "je ne veux pas", "je ne souhaite pas", "nous ne souhaitons pas", "plus tard",
    "pas encore", "pas comme ça", "je me suis trompé", "je me suis trompée", "on s'est trompé",
    "nous nous sommes trompés", "c'est pas bon", "c'est pas correct", "c'est pas le bon", "c'est pas la bonne",
    "un instant", "un moment", "une seconde", "une minute", "quelques instants",
    # anglais
    "not correct", "not right", "that's wrong", "not now", "not yet", "do not confirm", "don't confirm",
    "i don't confirm", "i don't want", "we don't want", "later", "do not", "don't", "hold on", "i made a mistake",
    "we made a mistake", "my mistake", "i changed my mind", "we changed our mind", "i don't agree",
    "one moment", "one second", "just a moment", "a moment", "a minute", "a sec", "give me a moment",
    # darija en lettres latines
    "machi hakka", "machi hakda", "machi hadi", "machi hadchi", "machi daba", "mashi daba", "ma bghitch",
    "mabghitch", "ma bghinach", "mabghinach", "mn b3d", "men ba3d", "mn ba3d", "mnb3d", "d9i9a", "dqiqa",
    # darija en lettres arabes et arabe standard
    "ماشي هادشي", "ماشي هكا", "ماشي هادي", "ماشي دابا", "ما بغيتش", "مابغيتش", "ماشي صحيح", "غير صحيح",
    "ليس صحيحا", "ليس كذلك", "لا أؤكد", "لا أوافق", "لا نوافق", "لا أريد", "لا نريد", "لا أرغب", "لا نرغب",
    "من بعد", "لاحقا", "ليس الآن", "لحظة", "لحظه", "دقيقة", "دقيقه",
)
_NO_PHRASES = _phrase_pattern(_NO_PHRASE_LIST)
# Refus de volonté (« je ne veux pas », « ma bghitch ») : ils refusent une chose précise (« je ne veux pas
# d'appel »), et ne valent refus de la commande que dans une réponse courte.
_VOLITIONAL_REFUSALS = frozenset((
    "je ne veux pas", "je ne souhaite pas", "nous ne souhaitons pas", "i don't want", "we don't want", "ma bghitch",
    "mabghitch", "ma bghinach", "mabghinach", "ما بغيتش", "مابغيتش", "لا أريد", "لا نريد",
    "لا أرغب", "لا نرغب",
))
_NO_OPENING_PHRASES = _phrase_pattern(tuple(
    phrase for phrase in _NO_PHRASE_LIST if phrase not in _VOLITIONAL_REFUSALS))
# Report de la décision, refus poli même dans une phrase longue (« je préfère attendre encore un peu
# avant de valider », « let me think about it », « خليني نفكر »).
_DEFERRAL_PHRASES = _phrase_pattern((
    "je préfère attendre", "on préfère attendre", "nous préférons attendre", "je vais réfléchir",
    "je vais y réfléchir", "laissez-moi réfléchir", "je dois réfléchir", "on va réfléchir", "je reviens vers vous",
    "je reviendrai vers vous", "je vous recontacte", "je vous tiens au courant", "pas tout de suite",
    "let me think", "i'll think about it", "i will think about it", "i need to think", "maybe later",
    "not right now", "i'll get back to you", "i will get back to you", "i prefer to wait", "i'd rather wait",
    "khallini nfekker", "khalini nfekker", "khallini nfakker", "khalini nfakar", "ghadi nfekker", "ghanfekker",
    "خليني نفكر", "غادي نفكر", "سأفكر", "سنفكر", "في وقت لاحق",
))
# Auxiliaire de projet ou d'obligation suivi d'un verbe de vérification, de consultation ou de réflexion
# (« je vais d'abord vérifier avec mon associé », « I need to check first », « khalini nchouf »), ou
# réserve annoncée avant la décision (« avant de valider », « before confirming »).
_DEFERRAL_VERB_RE = re.compile(
    r"(?<!\w)(?:je|j|on|nous)[\s']*(?:vais|va|allons|dois|doit|devons|prefere|preferons)"
    r"\s+(?:d[\s']*abord\s+|encore\s+|juste\s+|quand\s+meme\s+)?(?:y\s+|en\s+|le\s+|la\s+|lui\s+)?"
    r"(?:reflechir|verifier|consulter|voir|checker|regarder|demander|attendre|discuter|prendre\s+le\s+temps)"
    r"(?!\w)"
    r"|(?<!\w)(?:je|j|on|nous)[\s']*(?:vais|va|allons|dois|doit|devons|prefere|preferons)\s+"
    r"(?:d[\s']*abord\s+)?(?:en|y)\s+parler(?!\w)"
    r"|(?<!\w)avant\s+de\s+(?:valider|confirmer|commander|envoyer|lancer)(?!\w)"
    r"|(?<!\w)(?:i|we)(?:'ll|\s*will|\s+need\s+to|\s+have\s+to|\s+must|\s+should|\s+am\s+going\s+to"
    r"|\s+are\s+going\s+to)\s+(?:first\s+|just\s+|still\s+)?"
    r"(?:check|verify|think|consult|ask|discuss|talk|confirm\s+with|see|look|get\s+back|speak)(?!\w)"
    r"|(?<!\w)let\s+me\s+(?:check|think|discuss|ask|verify|see|talk|consult|first)(?!\w)"
    r"|(?<!\w)before\s+(?:confirming|validating|ordering|proceeding|sending)(?!\w)"
    r"|(?<!\w)(?:khal+ini|khal+ina|khassni|khasni|ghadi|gha)\s*n?"
    r"(?:fe[kq]+er|fakar|fakker|chouf|shouf|chawer|sewel|sowel|tsenna|stanna)(?!\w)"
    r"|(?<!\w)س[اني](?:راجع|تحقق|سال|تشاور|ناقش|فكر)(?!\w)"
    r"|(?<!\w)(?:خليني|دعني|خلينا)\s+(?:نشوف|نفكر|نتاكد|نشاور|افكر|اراجع|اتاكد|اشاور)(?!\w)"
)
# Mots qui accompagnent une réponse courte sans en changer le sens : politesse, adresse,
# démonstratifs, mots grammaticaux (pronoms, articles, prépositions, auxiliaires et verbes de
# permission comme « pouvez » ou « can »). Ces mots appartiennent à des classes fermées : ils ne
# portent aucune information de commande, alors qu'un nom ou un adjectif inconnu (« oui envoyez le
# camion ») peut porter une modification et empêche la lecture comme simple réponse.
_REPLY_FILLERS = _folded((
    # français
    "merci", "mercii", "beaucoup", "bien", "bon", "c", "est", "ça", "ca", "tout", "ce", "cela", "va", "pour",
    "moi", "nous", "svp", "stp", "très", "tres", "alors", "donc", "aussi", "ainsi", "je", "j", "on", "vous",
    "il", "y", "en", "le", "les", "l", "de", "du", "des", "et", "mon", "notre", "votre", "suis", "sommes",
    "peux", "pouvez", "pouvons", "peut", "vas", "go", "procédez", "encore", "d", "abord", "tous", "toutes",
    "noté", "notée", "noted", "bien",
    # anglais
    "please", "pls", "thanks", "thank", "you", "it", "is", "that", "s", "i", "we", "this", "the", "with", "and",
    "can", "may", "now", "so", "just", "am", "are", "be", "proceed", "ahead", "yet", "everything", "all", "a",
    "me", "my",
    # darija en lettres latines
    "chokran", "choukran", "shukran", "chkran", "3afak", "afak", "baraka", "lahoufik", "llah", "allah", "ykhalik",
    "sir", "hakka", "hakda", "hadchi", "hadi", "dak", "dakchi", "kolchi", "a", "sidi", "khoya", "khouya", "lalla",
    "madame", "monsieur", "lah", "bzaf", "bezzaf", "bezaf", "bzzaf", "ana", "7na", "hna", "rah", "raha", "ghir",
    "t9der", "t9dro", "tqder", "tqdro", "momkin", "mumkin", "chwiya", "chwia", "chwya", "chwiyt", "mn", "men",
    "smhli", "smahli", "sm7li", "smah",
    # darija en lettres arabes et arabe standard
    "بزاف", "جدا", "شكرا", "عافاك", "عافاكم", "بارك", "الله", "فيك", "هادشي", "هكا", "هذا", "هذه", "الطلب",
    "الطلبية", "من", "فضلك", "فضلكم", "جزيلا", "خويا", "اخويا", "أخي", "سيدي", "انا", "أنا", "نحن", "يمكنك",
    "يمكنكم", "تقدر", "تقدرو", "تقدروا", "ممكن", "على", "عليه", "عليها", "ذلك", "شوية", "شوي", "قليلا", "قليل",
    "تفضلوا", "تفضل", "تفضلو", "لحظة", "لحظه", "كل", "شيء", "شي", "هاكا", "هاكدا", "هكذا", "هكدا", "تماما", "كليا",
    "بالكامل", "فعلا",
    "حقا", "ارجو", "نرجو", "رجاء", "الرجاء", "يرجى", "سمحتم", "لو",
))
# Mots de contraste : « oui mais … » annonce une réserve, « merci mais non » confirme un refus.
_CONTRAST_WORDS = _folded(("mais", "but", "walakin", "walaken", "sauf", "except", "ولكن", "لكن"))
# Nom de la commande, objet d'un accord (« je valide la commande », « confirm the order »).
_REPLY_OBJECTS = _phrase_pattern((
    "la commande", "ma commande", "notre commande", "cette commande", "le récapitulatif", "la commande dyali",
    "had la commande", "l commande", "lcommande", "the order", "my order", "this order", "our order",
    "الكوموند", "الطلبية ديالي",
    # compléments de temps, de manière, de point de vue et de souhait qui ne changent pas le sens de la réponse
    "pour le moment", "pour l'instant", "en ce moment", "for now", "right now", "daba", "حاليا", "دابا",
    "في الوقت الحالي", "في ذلك", "inchallah", "nchallah", "inshallah", "in cha allah", "إن شاء الله",
    "comme ça", "tel quel", "telle quelle", "as is", "like that", "de mon côté", "de notre côté", "pour ma part",
    "pour notre part", "on my side", "on our side", "on my end", "s'il vous plait", "s'il te plait",
    "un peu", "a bit", "a little", "for the moment", "for the time being", "to me", "for me", "with me", "for us",
    "to us", "with us", "pour nous",
    # remerciements, vœux et formules de clôture qui suivent une réponse
    "merci beaucoup", "merci bien", "merci d'avance", "mille mercis", "bonne journee", "bonne soiree",
    "bonne continuation", "bonne fin de journee", "bonne semaine", "a bientot", "a demain", "cordialement",
    "thanks a lot", "thank you very much", "have a nice day", "have a good day", "see you", "talk soon",
    "chokran bzaf", "bslama", "الله يعطيك الصحة", "شكرا جزيلا", "شكرا لكم", "مع السلامة",
    *(f"{thanks} {preposition} {possessive} {noun}"
      for thanks in ("merci", "mercii")
      for preposition in ("pour",)
      for possessive in ("votre", "la", "ta", "ton", "vos")
      for noun in ("reactivite", "rapidite", "aide", "retour", "reponse", "patience", "comprehension", "gentillesse",
                   "service", "travail", "disponibilite", "efficacite", "prompte reponse")),
    "for your help", "for your quick reply", "for your quick response", "for your patience", "for the quick reply",
    "for your time", "for the quick response", "y aller", "on y va", "go on", "go on then", "that is what i need",
    "that's what i need", "that is what i want", "that's what i want", "what i need", "what i want",
    "ce dont j'ai besoin", "ce dont nous avons besoin",
    # « c'est ce que je voulais »
    "ce que je voulais", "ce que je souhaitais", "ce que j'attendais", "ce qu'il me fallait", "ce qu'il nous fallait",
    "ce que nous voulions", "what i wanted", "what i needed", "what we wanted", "what we needed", "exactly",
))
# Verbes d'envoi conjugués, avec leurs pronoms suffixes : « sifto », « siftoha », « صيفطوها ليا »,
# « envoyez-la », « أرسلوا », « الإرسال ». Envoyés seuls ou avec des mots grammaticaux en réponse à
# un récapitulatif (« vous pouvez envoyer », « sift », « send it »), ils valent accord ; niés
# (« ne l'envoyez pas », « matsiftch », « لا ترسلوا »), ils valent refus.
_DISPATCH_VERB_RE = re.compile(
    r"^(?:(?:ka|ki|gha)?[nty]?(?:sift|sifet|sayft|sayeft)[a-z]*|[nty]?jib[a-z]*|(?:كا|كي|غا)?[نتي]?[صس]يفط[^\W\d_]*"
    r"|[نتي]?جيب[^\W\d_]*"
    r"|[انتي]?(?:رسل|بعث)[^\W\d_]*|(?:ب)?(?:ال)?ارسال|envoy[a-z]*|expedi[a-z]*|livr(?:ez|er|e)|lanc(?:ez|er|e)"
    r"|send[a-z]*|ship|place|submit|process|launch|lancez|demarrez|commencez|traitez|enregistrez)$"
)
_BENEFACTIVE_PRONOUNS = _folded(("lia", "liya", "lya", "lina", "ليا", "لينا", "لي", "لنا", "me", "nous", "us"))
_YES_EMOJIS = ("\U0001f44d", "\U0001f44c", "✅", "✔", "☑", "\U0001f197", "\U0001f64c")
_NO_EMOJIS = ("\U0001f44e", "❌", "✖", "⛔", "\U0001f6ab", "\U0001f645")
# Négation française « ne … pas » (« ne l'envoyez pas », « ce n'est pas ça »), avec au plus trois
# mots entre les deux particules.
_FRENCH_NEGATION_RE = re.compile(r"(?<!\w)n(?:e\s+|')(?:[^\W\d_]+[\s']+){0,3}?(?:pas|plus|jamais|point)(?!\w)")
# Mots qui, placés après « la » en tête de message, montrent que « la » est le « non » de la darija et
# pas l'article français : « la la », « la 3afak », « la a khoya », « la merci ». Les mots grammaticaux
# français en sont exclus : « la c'est bon » peut être « là, c'est bon » écrit sans accent.
_DARIJA_AFTER_LA = _folded((
    "la", "lla", "no", "non", "merci", "machi", "mashi", "ma", "3afak", "afak", "a", "khoya", "khouya", "sidi",
    "lalla", "chokran", "choukran", "shukran", "chkran", "baraka", "ghalat", "ghalta", "khata", "mazal", "daba",
    "men", "mn", "ba3d", "tsenna", "stanna", "bzaf", "chwiya", "walou",
))
# Mots de refus qui peuvent ouvrir une réponse suivie d'une explication (« non, ce n'est pas la bonne
# quantité ») ; « la » de la darija est reconnu à part (:func:`_is_lone_la`).
_REFUSAL_OPENERS = _NO_WORDS
# Hésitations et adverbes d'atténuation qui précèdent un refus (« hmm non », « plutôt non », « sorry, no »).
_HEDGE_WORDS = _folded((
    "hmm", "hum", "humm", "mmm", "euh", "heu", "ben", "bah", "bon", "alors", "well", "uh", "um", "eh", "oh",
    "plutôt", "finalement", "franchement", "honnêtement", "actually", "honestly", "sorry", "désolé", "désolée",
    "pardon", "mais", "but", "walakin", "ولكن", "لكن",
))
# Copule niée en tête de message : « ce n'est pas ça », « c'est pas le bon », « that's not what I ordered »,
# « ليس هذا ».
_NEGATIVE_COPULA_RE = re.compile(
    r"^\W*(?:(?:ce|c|ca|cela)[\s']*(?:n[\s']*)?(?:est|etait)\s+pas"
    r"|(?:that|this|it)(?:[\s']*s|\s+is|\s+was)?[\s']*(?:not|isn[\s']?t|wasn[\s']?t)"
    r"|ليس|ليست|لست)(?!\w)"
)
# Champs d'une extraction par règles qui montrent qu'un message apporte un élément de commande.
_ORDER_FIELDS = ("entreprise_cliente", "type_produit", "quantite", "unite", "date_livraison")
# Particule de négation écrite à part (« ma tsiftohach », « ما تصيفطش »).
_NEGATION_PARTICLES = _folded(("ma", "ما"))
# Mots en « ma … ch » qui ne sont pas des refus : « ma3lich » (ce n'est rien, pas grave).
_NOT_NEGATIONS = _folded(("ma3lich", "ma3lish", "m3lich", "maalich", "معليش", "ماعليش"))

# ---------------------------------------------------------------- salutations

_GREETING_WORDS = _folded((
    "bonjour", "bonsoir", "rebonjour", "re", "salut", "slt", "bjr", "bsr", "cc", "coucou", "hello", "hi", "hey",
    "greetings", "salam", "slm", "salamo", "salamou", "salamu", "assalam", "assalamou", "assalamu", "alaykoum",
    "mrhba",
    "mrehba", "mar7ba", "mer7ba", "merhba",
    "alaikoum", "alikoum", "alikom", "3alikom", "3alaykoum", "3alaykom", "alaykom", "3likom", "3likoum",
    "aleykoum", "alaykum", "wa3alaykoum", "ahlan", "marhba", "marhaba", "sba7", "sbah", "lkhir", "lkher", "msa",
    "good", "morning", "evening", "afternoon", "day", "labas", "cv", "kidayr", "kidayra", "kidayrin", "dayr",
    "dayra", "kif", "bikhir", "hamdoulah", "hamdoullah", "lhamdoulah", "lhamdoulillah", "lah", "there", "all",
    "everyone", "tous", "toutes", "tout", "le", "monde", "messieurs", "dames", "cher", "chere", "chers", "madame",
    "monsieur", "mme", "mr", "a", "khoya", "khouya", "sahbi", "sidi", "lalla", "team", "guys", "folks", "sir",
    "madam", "equipe", "tecpap", "wa", "sahlan", "toute", "l", "rahmat", "rahmatu", "rahmatullah", "rahma",
    "barakatuh",
    "barakatou", "allah",
    "السلام", "سلام", "عليكم", "وعليكم", "مرحبا", "اهلا", "أهلا", "سهلا", "صباح", "مساء", "الخير", "النور",
    "لاباس", "الحمد", "لله", "بخير", "كيداير", "كيف", "داير", "دايرين", "حالك", "اخويا", "خويا", "ورحمة", "ورحمه",
    "وبركاته", "الله", "وسهلا", "تحية", "تحيه", "طيبة", "طيبه", "فريق", "بيك", "بكم", "بك", "بيكم", "صباح",
    "bik", "bikom", "bikoum", "biki", "to", "you", "your",
))
# Voeux qui prolongent une salutation d'ouverture (« salut, bonne journée », « hello and have a nice day »).
_GREETING_WISHES = _folded((
    "bonne", "bon", "journee", "soiree", "semaine", "matinee", "week", "end", "weekend", "have", "nice", "day",
    "great", "lovely", "wonderful", "happy", "and", "et", "j'espere", "hope", "doing", "well", "ca", "va",
))
# Mots qui ouvrent une salutation : un message qui commence par l'un d'eux et ne contient ensuite que des
# mots de salutation, des vœux ou des mots grammaticaux est une salutation.
_GREETING_OPENERS = _folded((
    "bonjour", "bonsoir", "rebonjour", "salut", "slt", "bjr", "bsr", "cc", "coucou", "hello", "hi", "hey",
    "greetings", "good", "salam", "slm", "salamo", "salamou", "salamu", "assalam", "assalamou", "assalamu",
    "ahlan", "marhba", "marhaba", "mrhba", "mrehba", "mar7ba", "mer7ba", "merhba", "sba7", "sbah", "msa",
    "السلام", "سلام", "مرحبا", "اهلا", "أهلا", "صباح",
    "مساء", "تحية",
))
# Mots de salutation qui peuvent aussi appartenir à une réponse (« tout bon », « bon pour accord ») : ils ne sont pas
# retirés avec la salutation d'ouverture.
_AMBIGUOUS_GREETING_WORDS = _folded((
    "tout", "tous", "toutes", "toute", "le", "monde", "a", "l", "bon", "re", "good", "all", "to", "you", "your",
    "mr", "mme", "cher", "chere", "chers",
))
_THANKS_WORDS = _folded(("merci", "mercii", "thanks", "thank", "chokran", "choukran", "shukran", "شكرا"))
_MENU_WORDS = _folded((
    "menu", "aide", "help", "start", "commencer", "options", "3awni", "مساعدة", "القائمة", "قائمة",
))
# Questions de politesse qui accompagnent une salutation (« ça va ? », « how are you? », « كيف حالك؟ »).
_WELL_BEING_PATTERN = _phrase_pattern((
    "ça va", "comment ça va", "comment allez-vous", "comment vas-tu", "vous allez bien", "tu vas bien",
    "j'espère que vous allez bien", "how are you", "how are you doing", "how's it going", "hope you are well",
    "labas 3lik", "labas 3likom", "labas 3lih", "kidayr", "kidayra", "kidayrin", "kif dayr", "kif dayra",
    "kif dayrin", "kif lhal", "كيف حالك", "كيف حالكم", "كيف الحال", "لاباس عليك", "لاباس عليكم", "كيداير",
    "كيف داير", "كيف دايرين", "أتمنى أن تكونوا بخير",
))

# ---------------------------------------------------------------- annulation

_CANCEL_PATTERN = _phrase_pattern((
    "call off", "laisse tomber", "laissez tomber", "abandonner", "abandonnons", "never mind", "forget it",
    "sans suite", "withdraw", "withdrawn", "scrap",
))
# Verbe « annuler » de la darija écrit à la française (« nannuli », « tannuli »), de la racine b-t-l
# (« battel », « nbattel », « battlo », « battliha », « بطلو », « نبطل ») ou de la racine « renoncer »
# (« je renonce à ma commande »). « بطل » seul (héros) n'est pas un verbe : il faut une marque de
# personne ou un pluriel.
_CANCEL_VERB_RE = re.compile(
    r"(?<![^\W\d_])(?:ka|ki)?[nty]{1,2}annul[^\W\d_]*"
    r"|(?<![^\W\d_])[nty]?b[ae]t+[ae]?l(?:o|ou|i|iw|ha|hom|ni|li|lia|lina)?(?![^\W\d_])"
    r"|(?<![^\W\d_])(?:ن|ت|ي)?بطل(?:و|وا|ي|ها|هم)(?!\w)|(?<!\w)نبطل(?!\w)"
    r"|(?<![^\W\d_])renonc[^\W\d_]*"
    r"|(?<!\w)n(?:e\s+|')\s*(?:\w+\s+)?(?:pas\s+|plus\s+)?donn\w*\s+(?:pas\s+|plus\s+)?suite(?!\w)"
)
# Verbe de retrait ou d'oubli suivi du nom de la commande : « supprimez ma commande », « forget the
# order », « nsa la commande ». Il faut le nom de la commande à moins de trois mots du verbe, et le
# message ne doit citer ni produit ni quantité (« nsa dik la commande, bghit 2000 sachet » remplace
# la commande par une autre).
_WITHDRAW_ORDER_PATTERN = _sequence_pattern(
    ("supprimer", "supprimez", "supprime", "retirer", "retirez", "retire", "effacer", "effacez", "oublier",
     "oubliez", "oublie", "ignorer", "ignorez", "ignore", "forget", "delete", "remove", "drop", "disregard",
     "nsa", "nsaw", "nsiw", "nsi", "7iyed", "7eyed", "7yed", "msa7", "ms7", "mse7", "امسح", "امسحوا", "احذف",
     "احذفوا", "انسى", "انسوا", "نسا", "نساو", "حيد", "حيدو", "بطل", "بطلي"),
    ("commande", "commandes", "order", "orders", "lcommande", "الطلبية", "الطلب", "الكوموند", "كوموند",
     "كومند", "الكومند", "الطلبيه"),
    3,
)
# Formes fléchies des verbes d'annulation, reconnues par leur radical :
# - français « annul- » (annuler, annulez, annulée, annulation) et anglais « cancel- » ;
# - darija « lgh- » (racine arabe l-gh-y) avec un préfixe de personne éventuel : lghi, nlghi,
#   tlghiw, lghiha ;
# - arabe « لغ » suivi de ي, ا, و ou ى, avec un préfixe de personne ou de participe éventuel :
#   ألغي, ألغوا, إلغاء, نلغي, تلغيو, ملغاة. « مبلغ » (montant) et « لغة » (langue) ne correspondent pas.
_CANCEL_STEM_RE = re.compile(
    r"(?<![^\W\d_])(?:annul|cancel)[^\W\d_]*"
    r"|(?<![^\W\d_])[ntym]?lgh[iaey][^\W\d_]*"
    r"|(?<![^\W\d_])[وف]?س?[انتيم]?لغ[ياوى][^\W\d_]*"
    r"|(?<![^\W\d_])[وف]?[انتيم]?لغ(?![^\W\d_])"
    r"|(?<![^\W\d_])[وف]?بالغ[ياوى][^\W\d_]*"
)
# « Ce bon annule et remplace le précédent » : rectification d'une commande, pas une annulation.
_REPLACES_PATTERN = _phrase_pattern(("annule et remplace", "annulé et remplacé", "annule et remplacé"))
_STOP_ORDER_PATTERN = _sequence_pattern(
    ("arrêtez", "arrêter", "arrête", "stoppez", "stopper", "stop", "stoppe", "wqef", "w9ef", "wa9ef", "وقف", "وقفو",
     "حبس", "حبسو", "أوقفوا", "اوقفوا"),
    ("commande", "commandes", "order", "ordre", "lcommande", "الطلبية", "الطلب", "الكوموند", "كوموند"),
    2,
)
# Arrêt d'une étape portant sur la commande : « stoppez la fabrication de ma commande », « stop the
# delivery of my order ».
_STOP_STEP_PATTERN = re.compile(
    r"(?<!\w)(?:arretez|arrete|arreter|stoppez|stoppe|stopper|stop)\s+(?:la\s+|le\s+|l'|the\s+|our\s+|my\s+)?"
    r"(?:fabrication|production|preparation|livraison|expedition|traitement|processing|delivery|shipping|shipment)"
    r"\s+(?:de\s+|d'|of\s+)(?:ma\s+|la\s+|notre\s+|cette\s+|mon\s+|my\s+|our\s+|the\s+|this\s+)?"
    r"(?:commande|order)(?!\w)"
)
# Renoncement : verbe de volonté ou de besoin nié avec l'idée de « ne plus » :
# - français « ne … plus » avec vouloir (« je n'en veux plus », « on ne la veut plus ») ou avec
#   « avoir besoin » (« nous n'en avons plus besoin »), et « plus besoin » en tête de réponse ;
# - anglais « no longer » suivi de « need » ou « want », « don't need … anymore » ;
# - darija « ma b9itch » (je ne … plus) suivi de « baghi » (vouloir), « m7taj » (avoir besoin) ou
#   « khass » (falloir), en lettres latines et arabes (« ما بقيتش باغي ») ;
# - arabe « لم أعد » / « لم نعد » suivi de « بحاجة », « أحتاج », « أريد » ou « أرغب ».
_RENOUNCE_RE = re.compile(
    r"(?<!\w)n(?:e\s+|')(?:en\s+|la\s+|le\s+|les\s+|l')?(?:veux|veut|voulons|voulez|souhaite|souhaitons)\s+plus(?!\w)"
    r"|(?<!\w)n(?:e\s+|')(?:en\s+)?(?:ai|a|avons|avez)\s+plus\s+besoin(?!\w)"
    r"|^\W*plus\s+besoin(?!\w)"
    r"|(?<!\w)no\s+longer\s+(?:need|want|required|needed|wanted)(?!\w)"
    r"|(?<!\w)(?:je|nous|on)\s+ne\s+(?:veux|voulons|souhaite|souhaitons)\s+pas\s+(?:de\s+)?(?:cette|ma|notre|la)\s+commande(?!\w)"
    r"|(?<!\w)(?:i|we)\s+(?:don't|do\s+not)\s+want\s+(?:this|the|my|our)\s+order(?!\w)"
    r"|(?<!\w)ma\s*(?:bghitch|bghinach)\s+(?:had\s+|dik\s+|hadik\s+)?(?:l|la\s+)?commande(?!\w)"
    r"|(?<!\w)(?:ما\s*بغيتش|ما\s*بغيناش)\s+(?:هاد\s+)?(?:ال)?(?:كوموند|طلبية|طلب)(?!\w)"
    r"|(?<!\w)لا\s+(?:اريد|نريد)\s+(?:هذا\s+)?(?:الطلب|الطلبية)(?!\w)"
    r"|(?<!\w)(?:don't|do\s+not|dont)\s+(?:need|want)\b[^.?!\n]{0,30}?\b(?:anymore|any\s+more)(?!\w)"
    r"|(?<!\w)ma\s*(?:b9|bq)i(?:t|na|ti|tou|tiw)(?:ch|sh)\s+(?:bagh|bghi|m7taj|mehtaj|ma7taj|khass)[^\W_]*"
    r"|(?<!\w)ما\s*بقي(?:ت|نا|تي|تو)?ش\s+(?:باغ|بغي|محتاج|خاص)[^\W\d_]*"
    r"|(?<!\w)لم\s+(?:اعد|نعد|يعد|تعد)\s+(?:بحاج|في\s+حاج|احتاج|نحتاج|اريد|نريد|ارغب|نرغب)[^\W\d_]*"
)

# ---------------------------------------------------------------- interlocuteur humain

# Demande de rappel, reconnue par la forme du verbe :
# - français : pronom complément puis infinitif ou deuxième personne (« me rappeler », « m'appeler »,
#   « nous recontacter »), sauf « rappeler » au sens de « remettre en mémoire » (« me rappeler le
#   prix ») ; impératif suivi du pronom (« rappelez-moi », « contactez-nous ») ; verbe de permission
#   suivi de « me contacter » ou « me joindre » (« un commercial peut me contacter ») ; « je
#   m'appelle » (je me nomme) ne correspond pas ;
# - darija : verbe « 3ayet » (appeler) conjugué, suivi de « lia » / « lina » (« t3ayet lia »,
#   « 3ayto lina ») ; en lettres arabes « عيط » conjugué suivi de « ليا » / « لينا » ;
# - arabe : verbe de la racine t-s-l (« اتصلوا », « تتصل ») suivi de « بي » / « بنا ».
_CALLBACK_PATTERN = re.compile(
    r"(?<!\w)(?:me|m|nous)[\s'\-]*(?:r?appel(?:er|ez|iez)|recontact(?:er|ez)|telephon(?:er|ez))(?!\w)"
    r"(?!\s+(?:le|la|les|l|vos|votre|ce|cette|ces|combien|quel|quelle|quels|quelles|comment|si|que|qu)\b)"
    r"|(?<!\w)(?:r?appel(?:ez|le)|recontactez|contactez|telephonez)[\s'\-]*(?:moi|nous)(?!\w)"
    r"(?!\s+(?:combien|quel|quelle|quels|quelles|comment|si|que|qu)\b)"
    r"(?!\s+(?:le|la|les|l|vos|votre|ce|cette|ces)\s*(?:prix|quantit|montant|total|numero|ref|detail|delai|date|"
    r"adresse|nom|nombre|tarif|produit|commande|devis))"
    r"|(?<!\w)(?:veuillez|merci\s+de|priere\s+de|prie\s+de)\s+(?:bien\s+)?(?:me|nous)[\s']*"
    r"(?:contacter|r?appeler|recontacter|joindre|telephoner)(?!\w)"
    r"(?!\s+(?:par\s+(?:mail|email|e-mail|ecrit|sms|message|courrier)|sur\s+whatsapp|ici))"
    r"|(?<!\w)(?:pouvez|pourriez|peut|pourrait|peuvent|pourraient)[\s\-]*(?:vous\s+|il\s+|elle\s+|on\s+)?"
    r"(?:me|m|nous)[\s']*(?:contacter|joindre|r?appeler|recontacter|telephoner)(?!\w)"
    r"(?!\s+(?:le|la|les|l|vos|votre|ce|cette|ces|combien|quel|quelle|quels|quelles|comment|si|que|qu)\b)"
    r"|(?<!\w)qu(?:e\s+|')(?:\w+[\s']+){0,3}?(?:me|m|nous)[\s']*"
    r"(?:r?appel+(?:e|ent|iez)|(?:re)?contact(?:e|ent|iez)|joign(?:e|ent|iez)|telephon(?:e|ent|iez))(?!\w)"
    r"|(?<!\w)[nty]?3a?y+e?t(?:o|ou|i|u)?[\s'\-]*(?:lia|liya|lya|li|lina)(?!\w)"
    r"|(?<!\w)[نتي]?عيط(?:و|ي)?\s*(?:ليا|لي|لينا)(?!\w)"
    r"|(?<!\w)[^\W\d_]{0,3}تصل[^\W\d_]{0,3}\s+(?:بي|بيا|بنا|بينا)(?!\w)"
)
_HUMAN_REQUEST_PATTERN = _phrase_pattern((
    # rappel
    "être rappelé", "être rappelée", "être recontacté", "être contacté", "faites-moi rappeler", "call me",
    "call us", "give me a call", "give us a call", "phone me", "ring me", "contact me", "reach me",
    "3ayetli", "3ayetlia", "3aytoli", "3aytolia", "tasl bia", "ttasl bia", "tasslo bia", "ttasslo bia",
    "عيطلي", "عيطليا", "عيطوليا", "تواصلوا معي",
    # mise en relation
    "passez-moi", "mettez-moi en relation", "mise en relation", "mettre en relation",
    # refus du robot
    "pas un robot", "pas avec un robot", "pas à un robot", "pas une machine", "not a bot", "not a robot",
    "machi robot", "machi m3a robot", "machi m3a robo", "machi bot", "machi robo",
    "ماشي روبو", "ماشي مع الروبو", "ماشي الروبو", "ماشي روبوت", "ليس روبوت", "ليس مع روبوت",
    # personne explicitement humaine
    "un humain", "un être humain", "une vraie personne", "une personne réelle", "un vrai conseiller",
    "a real person", "a human", "a live agent", "human agent", "شخص حقيقي",
    # rappel (anglais)
    "a call from", "a call back", "call back", "callback", "ring back", "phone back", "get back to me",
))
# Formules de politesse d'une signature, qui ne demandent pas d'interlocuteur.
_COURTESY_CONTACT_PATTERN = _phrase_pattern((
    "n'hésitez pas à me contacter", "n'hésitez pas à nous contacter", "n'hésitez pas à me joindre",
    "n'hésitez pas à nous joindre", "n'hésitez pas à me rappeler", "n'hésitez pas à nous rappeler",
    "feel free to contact me", "feel free to contact us", "do not hesitate to contact me",
    "do not hesitate to contact us", "don't hesitate to contact me", "don't hesitate to contact us",
))
_SPEECH_VERBS = (
    "parler", "discuter", "échanger", "contacter", "joindre", "appeler", "talk", "speak", "chat", "reach", "n3ayet",
    "n3ayt", "3ayet", "nconsulti",
    "nhder", "nhdar", "nhdr", "nahder", "nhadar", "nhdro", "hder", "nkellem", "ntkellem", "nkallem", "ntkallem",
    "ntwasel", "ntwassel",
    "نهضر", "نهدر", "هضر", "نتكلم", "نكلم", "تكلم", "أتكلم", "اتكلم", "التكلم", "أتحدث", "اتحدث", "التحدث", "نتحدث",
    "أتواصل", "اتواصل", "التواصل", "نتواصل",
)
_WANT_VERBS = (
    "je veux", "je voudrais", "je souhaite", "nous voulons", "nous souhaitons", "j'aimerais", "i want", "i need",
    "i'd like", "we want", "we need", "bghit", "bghina", "baghi", "khassni", "بغيت", "بغينا", "باغي", "خاصني",
    "أريد", "اريد", "نريد", "أحتاج", "نحتاج",
    "puis-je avoir", "pourrais-je avoir", "je voudrais avoir", "je veux avoir", "je peux avoir", "can i have",
    "can i get", "could i have", "could i get", "may i have", "i would like to have", "i'd like to have",
    "bghit chi", "بغيت شي", "بغينا شي",
)
_PERSONS = (
    "conseiller", "conseillère", "conseillers", "commercial", "commerciale", "commerciaux", "responsable",
    "vendeur", "vendeuse", "humain", "personne", "quelqu'un", "agent", "opérateur", "opératrice",
    "interlocuteur", "service client", "service commercial", "équipe commerciale", "gérant", "directeur",
    "someone", "somebody", "person", "human", "representative", "rep", "salesperson", "sales team",
    "sales rep", "customer service", "operator", "manager",
    "chi wa7ed", "chi wahed", "chi had", "chi 7ad", "chi 7ed", "wa7ed mn", "m3a wa7ed", "insan", "bnadem",
    "lcommercial", "mas2oul", "mas2oula", "lmas2oul", "modir", "lmodir",
    "شي واحد", "شي حد", "واحد من", "مع واحد", "إنسان", "انسان", "بنادم", "موظف", "مستشار", "مسؤول", "المسؤول",
    "الكوميرسيال", "كوميرسيال", "الكومرسيال", "المدير", "شخص", "أحد", "خدمة العملاء", "فريق المبيعات", "البائع",
    "مندوب", "المندوب", "مندوب مبيعات", "ممثل", "ممثلي", "ممثل المبيعات", "قسم المبيعات", "فريقكم", "الفريق",
)
_HUMAN_CONTACT_PATTERN = _sequence_pattern(_SPEECH_VERBS, _PERSONS, CONTACT_WINDOW_WORDS)
# Verbe de mise en relation, avec le pronom de la personne qui demande, suivi de la personne souhaitée :
# « pouvez-vous me passer un commercial », « mettez-moi en contact avec le service commercial »,
# « please connect me with a sales representative », « sifto lia chi commercial ».
_HUMAN_CONNECT_PATTERN = _sequence_pattern(
    ("me passer", "nous passer", "passez-moi", "passez-nous", "me transférer", "nous transférer", "transférez-moi",
     "transférez-nous", "me mettre en contact", "nous mettre en contact", "mettez-moi en contact",
     "mettez-nous en contact", "me mettre en relation", "nous mettre en relation", "mettez-moi en relation",
     "mettez-nous en relation", "me rediriger", "nous rediriger", "m'orienter", "m'orientez", "me connecter",
     "envoyez-moi", "donnez-moi", "me donner", "me trouver", "trouvez-moi",
     "connect me", "connect us", "put me through", "put us through", "put me in touch", "put us in touch",
     "transfer me", "transfer us", "refer me", "redirect me", "forward me", "give me", "get me", "send me",
     "3tini", "a3tini", "3tina", "sift lia", "sifto lia", "sift lina", "sifto lina", "siftli", "siftolia",
     "jib lia", "jibo lia", "jibli", "عطيني", "عطينا", "صيفط ليا", "صيفطو ليا", "صيفط لينا", "صيفطو لينا",
     "جيب ليا", "جيبو ليا", "ربطني", "ربطوني"),
    _PERSONS,
    3,
)
# Refus de parler à un robot, quelle que soit la tournure : « je ne veux pas parler à un robot »,
# « no bots please », « ماشي مع الروبو ».
_ROBOT_REFUSAL_RE = re.compile(
    r"(?<!\w)(?:n(?:e\s+|')\s*(?:\w+\s+){0,3}?pas|pas|sans|plus\s+de|no|not|don't|dont|do\s+not|never|machi|mashi"
    r"|ماشي|ليس|لست|بدون|بلا)\W+(?:\w+\W+){0,6}?(?:robots?|bots?|chatbots?|روبو|روبوت|الروبو|الروبوت|بوت)(?!\w)"
)
# Une personne indéterminée est priée de parler à l'expéditeur, de lui répondre ou de l'aider : « y a-t-il
# quelqu'un pour me parler », « someone to help me », « quelqu'un pour nous répondre ».
_SOMEONE_FOR_ME_RE = re.compile(
    r"(?<!\w)(?:quelqu[\s']*un|someone|somebody)\W+(?:\w+\W+){0,5}?"
    r"(?:(?:me|nous|m'|moi)[\s']*(?:parler|repondre|aider|appeler|rappeler|contacter|joindre|renseigner|conseiller)"
    r"|(?:to\s+)?(?:help|talk\s+to|speak\s+to|speak\s+with|talk\s+with|answer|assist|call|contact|reach)\s+(?:me|us))"
    r"(?!\w)"
)
# Une personne indéterminée est nommée avant le verbe de parole : « wach kayn chi wa7ed nhder m3ah »,
# « هل يوجد شخص يمكنني التحدث إليه ».
_SOMEONE_SPEAKS_RE = _sequence_pattern(
    ("chi wa7ed", "chi wahed", "chi had", "chi 7ad", "شي واحد", "شي حد", "شخص", "احد", "quelqu'un", "someone",
     "somebody"),
    _SPEECH_VERBS,
    4,
)
# Une personne indéterminée est priée de contacter l'expéditeur : « chi wa7ed ykellemni », « شي واحد يتصل
# بيا », « someone to reach me ». Le verbe de contact porte la marque de la première personne, collée
# (« -ni », « -na ») ou séparée (« lia », « بيا »).
_CONTACT_VERB = (
    r"(?:[ty]?(?:k[ae]ll?[ae]?m|3ayet|3ayt|contact|ttasl|tasl|rasl|jaweb|jawb|3awn|rje3|sma3|hd[ae]r)\w*"
    r"|[يت](?:تصل|عيط|تواصل|كلم|جاوب|هضر|رد)\w*)"
)
_SOMEONE_REACHES_ME_RE = re.compile(
    r"(?<!\w)(?:chi\s+(?:wa7ed|wahed|had|7ad|7ed)|quelqu[\s']*un|someone|somebody|شي\s+(?:واحد|حد)|احد)"
    r"\W+(?:\w+\W+){0,3}?(?:" + _CONTACT_VERB + r"(?:ni|na)|" + _CONTACT_VERB
    + r"\W+(?:me|us|lia|liya|lina|bia|biya|bina|ليا|لينا|بيا|معايا|معانا|بي|بنا))(?!\w)"
)
_HUMAN_WANT_PATTERN = _sequence_pattern(
    _WANT_VERBS,
    ("conseiller", "conseillère", "commercial", "commerciale", "humain", "agent", "opérateur", "human",
     "representative", "salesperson", "insan", "bnadem", "lcommercial", "إنسان", "انسان", "بنادم", "موظف",
     "مستشار", "الكوميرسيال", "كوميرسيال", "interlocuteur", "responsable", "quelqu'un", "someone", "somebody",
     "chi wa7ed", "chi wahed", "chi had", "شي واحد", "شي حد", "مندوب", "ممثل", "مسؤول", "المسؤول", "مدير", "المدير",
     "modir", "lmodir", "mas2oul", "lmas2oul", "manager", "واحد من الفريق", "واحد من المبيعات", "واحد من الشركة",
     "واحد من الموظفين", "واحد من الكوميرسيال", "واحد من فريق",
     "واحد من قسم"),
    3,
)
# Noms qui, seuls dans un message court, demandent un interlocuteur (« conseiller svp », « مستشار »).
_STANDALONE_PERSONS = _folded((
    "conseiller", "conseillère", "commercial", "commerciale", "humain", "agent", "opérateur", "human",
    "representative", "insan", "bnadem", "مستشار", "موظف", "إنسان", "انسان", "بنادم", "الكوميرسيال",
))
_STANDALONE_FILLERS = _folded((
    "un", "une", "le", "la", "l", "svp", "stp", "please", "pls", "a", "an", "merci", "3afak", "afak", "عافاك",
    "من", "فضلك", "شي", "chi", "bghit", "بغيت", "je", "veux", "voudrais", "i", "want", "need",
))

# ---------------------------------------------------------------- suivi

_STATUS_PATTERN = _phrase_pattern((
    "où en est", "où en sont", "suivi de", "suivre ma commande", "suivre mes commandes", "statut",
    "status", "état de ma commande", "état de la commande", "état de notre commande", "état de mes commandes",
    "pas encore reçu", "toujours pas reçu", "jamais reçu", "mes commandes", "nos commandes",
    "update on", "any update", "tracking", "track my", "where is my order", "my orders",
    "fin wslat", "fin wsalt", "fin wslet", "fin wselt", "fin wsl", "mazal ma jat", "mazal ma jatch",
    "mazal ma wslat", "mazal ma wsalt", "mazal ma wslatni", "mazal ma wsltni", "mazal ma wslatnich",
    "ma wslatni", "ma wslatnich", "mazal ma jatni", "mazal ma jani", "commandes dyali",
    "les commandes dyali", "فين وصلات", "فين وصلت", "مازال ما", "تتبع", "ما وصلنيش", "ما وصلاتنيش", "طلباتي",
))
# « حالة » (état) ou « أين » / « فين » (où) suivis d'un nom de commande, quels que soient l'article et
# le suffixe : « حالة طلبي », « حالة الطلبية », « أين طلبيتنا », « فين الكوموند ».
_ARABIC_STATUS_RE = re.compile(
    r"(?<!\w)(?:حالة|وضعية|وضع|اين|فين)\s+(?:ال)?(?:طلب|كوموند|كومند|سلعة|بضاعة)[^\W\d_]*"
)
# Référence de commande : code (« CMD-2026-0912 », « BC-889 », « PO 7781 ») ou numéro d'au moins deux
# chiffres annoncé par « n° », « # » ou « رقم ». Un chiffre seul après « n° » (« n°4 », « رقم 3 ») est
# le choix d'un menu numéroté, pas une référence.
_ORDER_REFERENCE_RE = re.compile(
    r"(?<![\w-])(?:cmd|bc|cde|po|bl|ref)[-_/ ]?\d[\d/-]*(?![^\W\d_])"
    r"|(?:n°|nº|no\.|numero|num\.?|#|raqm|ra9m|رقم|نمرة)\s*:?\s*\d{2,}(?![^\W\d_])"
)
# Nom de la commande suivi de son numéro (« la commande 1203 », « order 4512 », « الطلبية 77 ») ; le
# numéro n'est une référence que s'il n'est pas la quantité commandée (« commande 5000 sachets »).
_ORDER_NUMBER_RE = re.compile(
    r"(?<!\w)(?:commande|commandes|order|lcommande|طلبية|الطلبية|طلبيه|الطلبيه|طلب|الطلب|كوموند|الكوموند)"
    r"\s*(?:n°|nº|no\.?|numero|num\.?|#|رقم)?\s*:?\s*(\d{2,})(?![^\W\d_])"
)
# Noms qui désignent une commande passée ou son envoi, avec les déterminants possessifs (« ma commande »,
# « notre colis », « my package », « commande dyali »). Les genres ne sont pas vérifiés : « mon commande »
# est reconnu aussi.
_ORDER_NOUNS_FR = ("commande", "commandes", "cmd", "cde", "colis", "livraison", "livraisons", "marchandise", "envoi",
                   "expédition", "sacs", "sachets", "emballages", "cartons")
_ORDER_NOUNS_EN = ("order", "orders", "delivery", "deliveries", "parcel", "parcels", "package", "packages",
                   "shipment", "shipments", "goods", "cargo", "bags", "sachets", "packaging")
_ORDER_NOUNS_DARIJA = ("commande", "lcommande", "la commande", "colis", "l colis", "lcolis", "marchandise",
                       "lmarchandise", "l marchandise")
_MY_ORDER_PATTERN = _phrase_pattern((
    *(f"{possessive} {noun}" for possessive in ("ma", "mon", "mes", "notre", "nos") for noun in _ORDER_NOUNS_FR),
    *(f"{possessive} {noun}" for possessive in ("my", "our") for noun in _ORDER_NOUNS_EN),
    *(f"{noun} {possessive}" for noun in _ORDER_NOUNS_DARIJA for possessive in ("dyali", "dyalna", "diali", "dialna")),
    "طلبي", "طلبيتي", "طلبيتنا", "طلبنا", "الطلبية ديالي", "الطلبية ديالنا", "الطلب ديالي", "الكوموند ديالي",
    "الكوموند ديالنا", "كوموند ديالي",
))
# Possessif en lettres arabes, quel que soit le proclitique du nom (« الكوموند ديالي », « للكوموند
# ديالي », « فالطلبية ديالنا », « بطلبيتي »).
_ARABIC_MY_ORDER_RE = re.compile(
    r"(?<!\w)[وفبل]?(?:ال|لل)?(?:كوموند|كومند|طلبية|طلب|سلعة)\s+ديال(?:ي|نا)(?!\w)"
    r"|(?<!\w)[وفبل]?(?:طلبي|طلبيتي|طلبيتنا|طلبنا|طلباتي|طلبياتي)(?!\w)"
)
# Commande désignée par une subordonnée (« les sacs que j'ai commandés », « the bags I ordered »,
# « الأكياس التي طلبتها »).
_PAST_ORDER_PATTERN = _phrase_pattern((
    "que j'ai commandé", "que j'ai commandée", "que j'ai commandés", "que j'ai commandées", "que nous avons commandé",
    "que nous avons commandée", "que nous avons commandés", "que nous avons commandées", "que j'ai passée",
    "que nous avons passée", "i ordered", "we ordered", "i placed", "we placed", "i bought", "li tlabt", "li tlabna",
    "li commandit", "li commandina",
))
_ARABIC_PAST_ORDER_RE = re.compile(r"(?<!\w)(?:اللي|التي|الذي|لي)\s+طلب[^\W\d_]*")
# Question indirecte (« je voudrais savoir si ma commande est partie », « bghit n3ref wach … »).
_INDIRECT_QUESTION_PATTERN = _phrase_pattern((
    "savoir si", "savoir où", "savoir quand", "savoir comment", "know if", "know whether", "know when",
    "know where", "n3ref wach", "n3ref fin", "n3ref imta", "معرفة هل", "معرفة متى", "معرفة أين",
    "نعرف واش", "نعرف فين", "نعرف امتى",
))
# « fin » (où) en tête de message est l'interrogatif de la darija (« fin hiya lcommande dyali »), pas
# le nom français.
_DARIJA_WHERE_START_RE = re.compile(r"^\W*(?:fin|فين)(?!\w)")
# Nom défini d'une commande déjà passée, sans possessif (« la commande », « lcommande », « الكوموند »).
_DEFINITE_ORDER_PATTERN = _phrase_pattern((
    *(f"{article} {noun}" for article in ("la", "le", "les", "cette", "ce", "ces") for noun in _ORDER_NOUNS_FR),
    *(f"{article} {noun}" for article in ("the", "this", "that") for noun in _ORDER_NOUNS_EN),
    "lcommande", "l commande", "had lcommande", "had la commande", "lcolis", "l colis", "الطلبية", "الطلب",
    "الكوموند", "السلعة", "البضاعة", "الشحنة", "هاد الكوموند",
))
_STATE_PARTICIPLES = ("livré", "expédié", "envoyé", "arrivé", "reçu", "parti", "sorti", "prêt", "traité", "prévu",
                      "programmé", "retardé", "bloqué", "perdu")
_STATE_WORDS = (
    # participes passés accordés en genre et en nombre (« arrivés », « livrées »)
    *(f"{participle}{ending}" for participle in _STATE_PARTICIPLES for ending in ("", "e", "s", "es")),
    "livrée", "livré", "livrées", "livrés", "expédiée", "expédié", "expédiées", "envoyée", "envoyé", "partie",
    "parti", "partis", "sortie", "prête", "prêt", "prêtes", "arrivée", "arrivé", "arrive", "arriver", "arrivera",
    "reçue", "reçu", "recevoir", "réception", "en route", "en cours", "en préparation", "traitée", "prévue",
    "prévu", "programmée",
    "shipped", "delivered", "dispatched", "sent", "ready", "arrived", "arrive", "arriving", "on its way",
    "on the way", "in transit", "received", "processed", "scheduled", "expected", "out for delivery",
    "mchat", "تم شحن", "تم إرسال", "تم توصيل", "تم تسليم", "تم تجهيز", "مشات", "أرسلت",
    # retard
    "en retard", "retard", "retardée", "retardé", "bloquée", "bloqué", "perdue", "perdu", "late", "delayed", "delay",
    "stuck", "lost", "left", "leave", "leaves", "t3atlat", "t3tlat", "t2akhrat", "تأخر", "تأخرت", "متأخرة", "متأخر",
    "ضاعت",
)
# Formes conjuguées des verbes d'arrivée, d'envoi et de réception, reconnues par leur radical :
# - darija en lettres latines : préfixe de futur ou de présent éventuel (gha-, ka-, ki-, ta-), préfixe
#   de personne, puis radical « w(e)s(e)l » (arriver : « wslat », « twsel », « ghatwsel »), « ji » /
#   « jat » (venir : « tji », « ghatji », « jatni »), « khr(e)j » (sortir : « khrjat »), passif
#   « tsift- » / « tsayft- » (être envoyé : « tsiftat »), « wjd- » / « wajd- » (être prêt) ;
# - darija en lettres arabes : mêmes préfixes et radicaux (« وصلات », « غاتوصل », « تجي », « جاتني »,
#   « خرجات », « تصيفطات », « واجدة ») ;
# - arabe standard : « تصل », « يصل », « ستصل », « سيصل », « شحنت », « جاهزة » et le verbe recevoir
#   (« أستلم », « استلمت », « استلام »).
# « توصيل » (livraison, nom) et « يتصل » (appeler) ne correspondent pas.
_STATE_VERB_BODY = (
    r"(?:gha|ka|ki|ta)?[tyn]?w[ae]?s[ae]?l(?:at|et|t|ti|o|ou|na|ni|atni|atna|tni|tna)?(?:ch|sh)?"
    r"|(?:gha|ka|ki|ta)?[ty]ji(?:w|ou)?|jat(?:ni|na|kom|ch|nich|nach)?"
    r"|(?:gha|ka|ki|ta)?[ty]?khr[ae]?j(?:at|et|o|ou|t)?"
    r"|ts(?:i|ay|e)y?ft(?:at|et)|sift{1,2}iw(?:ha|hom)?|wjd(?:at|et)|wajd(?:a|in)?|wajed"
    r"|(?:غا|كا|كي|ك)?[تين]?وصل[^\W\d_]*|(?:غا|كا|كي|ك)?[تي]جي|جات[^\W\d_]*"
    r"|(?:غا|كا|كي|ك)?[تي]?خرج[^\W\d_]*|تصيفط[^\W\d_]*|صيفطتي?و(?:ها|هم)?|واجد[^\W\d_]*|وجدات"
    r"|س?[تي]صل(?:ني|نا|ك|كم|ه|ها|هم)?|شحنت|[انتي]?ستلم[^\W\d_]*|استلام|جاهز[^\W\d_]*"
)
_STATE_VERB_RE = re.compile(r"(?<!\w)(?:(?:ma|ما)\s*)?(?:" + _STATE_VERB_BODY + r")(?:ch|sh)?(?!\w)")
_STATE_WORD_PATTERN = _phrase_pattern(_STATE_WORDS)
# Verbe d'état ou question de lieu et de temps portant sur une commande désignée (« est partie »,
# « où est », « c'est pour quand »).
_STATE_PATTERN = _phrase_pattern((*_STATE_WORDS, "où est", "où sont", "where is", "where's", "fin", "فين", "أين",
                                  "quand", "when", "imta", "fo9ach", "امتى", "إمتى", "متى", "فوقاش",
                                  # demande de nouvelles ou de vérification portant sur la commande
                                  "nouvelles", "nouvelle", "news", "update", "check", "checking", "look into",
                                  "vérifier", "vérifiez", "regarder", "regardez", "chouf", "choufo", "شوف", "تحقق",
                                  # date, ou événement survenu, de la livraison
                                  "date de livraison", "date d'expédition", "delivery date", "eta",
                                  "délai de livraison",
                                  "what's happening", "what is happening", "what happened", "what's going on",
                                  "what is going on", "qu'est-ce qui se passe", "que se passe-t-il", "chno wqe3",
                                  "chno wa9e3", "شنو وقع", "ماذا حدث", "ماذا جرى", "تاريخ التسليم", "موعد التسليم",
                                  "موعد"))
# Absence de nouvelles ou attente prolongée au sujet de la commande désignée (« je suis sans nouvelles de ma
# commande », « j'attends ma commande depuis dix jours », « still waiting for my order »,
# « كنتسنى الكوموند »).
_WAITING_PATTERN = _phrase_pattern((
    "sans nouvelles", "sans nouvelle", "pas de nouvelles", "aucune nouvelle", "aucune nouvelles", "no news",
    "no update", "no updates", "haven't heard", "have not heard", "j'attends", "nous attendons", "on attend",
    "toujours en attente", "still waiting", "waiting for", "been waiting", "kantsenna", "kanstanna", "kantsenaw",
    "kanstannaw", "كنتسنى", "كنتسناو", "كنستنى", "في انتظار", "ننتظر", "منتظر", "أنتظر", "ما عندي خبر",
    "ما وصلني خبر", "en retard", "retard", "retardée", "retardé", "delayed", "is late", "was late", "تأخرت",
    "تأخر", "متأخرة", "متأخر",
))
# Mots de politesse, qui ne comptent pas dans la longueur d'un message de suivi (« et la commande 3341 ? merci
# beaucoup »).
_POLITE_WORDS = _folded((
    "merci", "mercii", "beaucoup", "svp", "stp", "please", "pls", "thanks", "thank", "you", "3afak", "afak",
    "chokran",
    "choukran", "شكرا", "عافاك", "فضلك", "فضلكم", "من", "bonjour", "bonsoir", "salam", "salut", "hello", "hi",
))
# Formules de demande polie, qui valent une question dans un message de suivi (« date de livraison de ma
# commande svp »).
_REQUEST_PATTERN = _phrase_pattern((
    "svp", "stp", "s'il vous plait", "s'il te plait", "please", "pls", "pouvez-vous", "pourriez-vous",
    "pouvez vous", "pourriez vous", "peux-tu", "dites-moi", "dis-moi", "indiquez-moi", "tell me", "let me know",
    "could you", "can you", "would you", "عافاك", "من فضلك", "من فضلكم", "لو سمحتم", "3afak",
))
# État attendu et pas encore atteint : « pas encore livrée », « still not delivered », « mazal ma wslat »,
# « لم أستلم ».
_NOT_YET_RE = re.compile(
    r"(?<!\w)" + _alternation(("toujours pas", "pas encore", "toujours rien", "still not", "still haven't",
                               "not yet", "mazal", "مازال", "ما زال", "لم"))
    + r"(?:\W+[^\W_]+){0,3}?\W+(?:" + _alternation(_STATE_WORDS) + r"|" + _STATE_VERB_BODY + r")(?!\w)"
)
# État attendu et nié, dans un message qui désigne la commande : « elle n'est pas arrivée », « hasn't
# arrived », « never delivered », « ma wslatch » (elle n'est pas arrivée), « ما وصلاتش ».
_NEGATED_STATE_RE = re.compile(
    r"(?<!\w)(?:n(?:e\s+|')\s*(?:\w+\s+){0,3}?(?:pas|plus|jamais)|pas|jamais"
    r"|(?:has|have|had|is|are|was|were|did|does|do)(?:n[\s']?t|\s+not)|never)"
    r"(?:\W+[^\W_]+){0,3}?\W+(?:" + _alternation(_STATE_WORDS) + r"|come|came|show|showed)(?!\w)"
    r"|(?<!\w)(?:ma|ما)\s*(?:" + _STATE_VERB_BODY + r")(?!\w)"
)
_CHANGE_PATTERN = _phrase_pattern((
    "modifier", "modifiez", "changer", "changez", "rectifier", "corriger", "ajouter", "ajoutez", "rajouter",
    "rajoutez", "remplacer", "enlever", "enlevez", "retirer", "retirez", "augmenter", "augmentez", "diminuer",
    "diminuez", "réduire", "réduisez",
    "modify", "change", "add", "remove", "increase", "reduce", "update",
    "bddel", "bdel", "beddel", "zid", "zido", "n9es", "na9es",
    "عدل", "بدل", "بدلو", "زيد", "زيدو", "نقص", "تعديل", "تغيير", "إضافة", "أضف", "أضيفوا",
))
# Verbes de modification de la commande en cours (« je préfère modifier la quantité », « I want to change
# something »), sans « update » ni « ajouter » qui servent aussi à demander des nouvelles ou à compléter.
_MODIFY_PATTERN = re.compile(
    r"(?<!\w)(?:modifier|modifiez|modification|changer|changez|rectifier|rectifiez|corriger|corrigez|modify|amend"
    r"|[nty]?be?dd?el(?:o|ou|i|iw|ha)?|بدل|بدلو|عدل|تعديل|تغيير)(?!\w)"
    r"|(?<!\w)(?:to|please|let\s+me|can\s+i|could\s+i)\s+(?:change|correct)(?!\w)"
)
# Tournure hypothétique (« si je commande lundi, ça arrive quand ? ») : question sur les délais.
_HYPOTHETICAL_PATTERN = _phrase_pattern(("si je", "si on", "si nous", "if i", "if we", "ila", "إذا", "اذا", "لو"))
# Demande de passer une commande, qui désigne une commande future (« quand puis-je passer ma commande ? »).
_PLACE_ORDER_PATTERN = _phrase_pattern((
    "passer ma commande", "passer notre commande", "passer la commande", "faire ma commande",
    "faire notre commande", "place my order", "place our order", "place the order",
))

# ---------------------------------------------------------------- commande

_ORDER_VERB_PATTERN = _phrase_pattern((
    "commander", "passer une commande", "passer commande", "faire une commande", "nouvelle commande",
    "je veux commander", "ncommandi", "ndir commande", "ndir chi commande", "nsift commande", "place an order",
    "to order", "new order", "make an order", "نطلب", "أطلب", "اطلب", "طلب جديد", "طلبية جديدة", "نكوموندي",
))
# Verbes d'achat à la première personne ou à l'impératif, qui engagent une commande.
_PURCHASE_PATTERN = _phrase_pattern((
    # français
    "je prends", "j'en prends", "je vous en prends", "nous prenons", "on prend", "on en prend", "je commande",
    "j'en veux", "j'en voudrais", "j'en souhaite", "nous en voulons", "nous en voudrions", "on en veut",
    "on en voudrait", "il m'en faut", "il nous en faut", "j'en ai besoin", "nous en avons besoin",
    "j'en aurais besoin", "j'en prendrai", "je vous en commande",
    "nous commandons", "on commande", "je vous commande", "envoyez-moi", "envoyez-nous", "livrez-moi",
    "livrez-nous", "mettez-moi", "préparez-moi", "préparez-nous", "il me faut", "il nous faut", "il me faudrait",
    "il nous faudrait", "j'ai besoin", "nous avons besoin", "on a besoin", "on aurait besoin", "j'aurais besoin",
    "nous aurions besoin", "je veux", "je voudrais", "nous voulons", "nous voudrions", "on voudrait",
    "je souhaite", "nous souhaitons", "je souhaiterais", "nous souhaiterions", "j'aimerais", "nous aimerions",
    "je vais prendre",
    # anglais
    "i want", "i need", "we need", "we want", "i'd like", "we'd like", "i would like", "we would like",
    "please send", "send me", "send us", "i'll take", "we'll take", "i will take", "we will take", "we require",
    "please supply", "kindly supply", "kindly send",
    # darija en lettres latines
    "bghit", "bghina", "baghi", "bagha", "baghin", "khassni", "khasni", "khessni", "khassna", "khasna", "3tini",
    "3tina", "a3tini", "sift lia", "sift liya", "siftli", "siftlia", "sifto lia", "sifto liya", "sifto lina",
    "siftolina", "siftlina", "jibli", "jib lia", "jibo lia", "dirli", "dir lia", "diro lia", "nakhod",
    # darija en lettres arabes et arabe standard
    "بغيت", "بغينا", "باغي", "خاصني", "خصني", "خاصنا", "خصنا", "عطيني", "عطينا", "صيفط ليا", "صيفطو ليا",
    "صيفطلي", "صيفطليا", "صيفطو لينا", "صيفطلينا", "جيب ليا", "جيبلي", "ديرلي", "دير ليا", "أريد", "اريد", "نريد",
    "نود", "أود", "نرغب", "أرغب", "نحتاج", "أحتاج", "احتاج", "أرسلوا", "ارسلوا", "زودونا", "تزويدنا",
))
# Souhait ou besoin avec un adverbe intercalé (« i just want 3000 bags », « je veux juste 500 sachets »).
_PURCHASE_ADVERB_RE = re.compile(
    r"(?<!\w)(?:i|we)\s+(?:just|only|also|really|still|now)\s+(?:want|need|require)(?!\w)"
    r"|(?<!\w)(?:je|nous|on)\s+(?:veux|voulons|voudrais|voudrions|souhaite|souhaitons|prends|prenons)\s+"
    r"(?:juste|seulement|aussi|finalement|plutot|quand\s+meme)(?!\w)"
)
# Demande de fourniture à la forme polie ou à l'infinitif, qui vaut commande avec une quantité ou un produit :
# « pouvez-vous me passer 2000 sacs ? », « merci de nous livrer 500 sachets ».
_DELIVERY_REQUEST_PATTERN = _phrase_pattern((
    "me livrer", "nous livrer", "me fournir", "nous fournir", "me préparer", "nous préparer", "me passer",
    "nous passer", "passez-moi", "passez-nous", "me mettre", "nous mettre", "mettez-moi", "mettez-nous",
    "me réserver", "nous réserver", "m'envoyer", "nous envoyer", "me faire parvenir", "nous faire parvenir",
    "supply us", "supply me", "provide us", "provide me", "deliver us", "deliver me",
))
# Souhait suivi d'un verbe de communication (« je souhaite vous proposer nos services », « je voudrais vous
# remercier ») : ce n'est pas un souhait d'achat.
_NOT_PURCHASE_RE = re.compile(
    r"(?<!\w)(?:je\s+(?:souhaite|voudrais|souhaiterais|aimerais)|nous\s+(?:souhaitons|voudrions|aimerions)|"
    r"j'aimerais|we\s+would\s+like\s+to|i\s+would\s+like\s+to|i'd\s+like\s+to|we'd\s+like\s+to)\s+(?:vous\s+)?"
    r"(?:proposer|presenter|informer|remercier|feliciter|signaler|dire|poser|contacter|parler|discuter|echanger|"
    r"offer|inform|present|propose|thank|introduce|tell|let|talk|speak|discuss)(?!\w)"
)
# Impératif d'un verbe de livraison suivi d'un pronom complément (« envoyez-m'en », « livrez-nous-en »,
# « mettez-m'en », « réservez-nous »).
_IMPERATIVE_PURCHASE_RE = re.compile(
    r"(?<!\w)(?:envoy|livr|mett|prepar|expedi|fourniss|reserv|gard)ez[\s'\-]*"
    r"(?:m[\s']*en|moi|nous(?:[\s'\-]*en)?|en)(?!\w)"
)
# Noms de sac sans précision du modèle (« bghit sacs », « I need bags »).
_GENERIC_PRODUCT_WORDS = _folded((
    "sac", "sacs", "sachet", "sachets", "sachiyat", "sachiat", "sachia", "sachiya", "bag", "bags", "emballage",
    "emballages", "ساك", "ساكات", "الساك", "الساكات", "صاك", "الصاك", "ساشي", "ساشيات", "الساشي", "الساشيات",
    "كيس", "أكياس", "اكياس", "الأكياس", "الاكياس", "كياس",
))
# Question de prix, de délai ou de disponibilité : une quantité y est indicative, pas commandée.
_QUOTE_QUESTION_PATTERN = _phrase_pattern((
    "combien", "quel prix", "quel est le prix", "quels sont les prix", "quel tarif", "prix", "tarif", "tarifs",
    "coût", "coûte", "coûtent", "délai", "délais", "avez-vous", "vous avez", "en stock", "disponible",
    "how much", "price", "prices", "cost", "quote", "how long", "do you have", "in stock", "available",
    "un devis", "des devis", "demande de devis", "devis pour", "devis de", "proforma", "pro forma", "quotation",
    "moins de", "plus de", "au moins", "au minimum", "less than",
    "more than", "at least", "fewer than",
    "اقل من", "أقل من", "اكثر من", "أكثر من", "على الاقل", "على الأقل",
    "ch7al", "chhal", "chal", "bch7al", "bchhal", "taman", "thaman", "wach kayn", "wach 3endkom", "wach 3ndkom",
    "شحال", "بشحال", "بكم", "كم", "ثمن", "سعر", "تمن", "واش كاين", "واش عندكم", "هل لديكم", "هل يوجد", "متوفر",
))

# ---------------------------------------------------------------- question

_QUESTION_START = _phrase_pattern((
    "est-ce", "quel", "quels", "quelle", "quelles", "combien", "comment", "pourquoi", "quand",
    "avez-vous", "vous avez", "proposez-vous", "faites-vous", "vendez-vous", "existe-t-il", "y a-t-il",
    "peut-on", "wach", "wash", "wech", "ch7al", "chhal", "chal", "kifach", "kifash", "3lach", "imta", "chno",
    "achno", "what", "how", "which", "when", "where", "why", "do you", "does", "is there", "are there", "can i",
    "can you", "could you",
    "واش", "شحال", "علاش", "كيفاش", "امتى", "متى", "هل", "ما هي", "ما هو", "ماذا", "كم", "أين", "كيف", "اشنو",
    "شنو", "اش", "وهل", "وما", "وكم", "وأين", "ومتى", "وكيف", "وواش", "وشحال", "où est", "où sont", "où en est",
    "où en sont", "où se trouve", "est où", "puis-je", "pourrais-je", "est-il possible", "peut-on", "can we", "may i",
))
_INFORMATION_PHRASES = _phrase_pattern((
    "voir vos produits", "voir le catalogue", "voir votre catalogue", "decouvrir vos", "see your products",
    "je voudrais savoir", "je veux savoir", "j'aimerais savoir", "renseignement", "renseignements",
    "information", "informations", "info", "infos", "plus de détails", "des précisions", "documentation",
    "fiche technique", "parlez-moi", "parle-moi", "dites-moi", "tell me about", "more details",
    "bghit n3ref", "bghit nswel", "bghit nsowel", "ma3loumat", "i would like to know", "i want to know",
    "أريد معرفة", "بغيت نعرف", "بغيت نسول", "استفسار", "معلومات", "تفاصيل",
))
# Demande de documents ou de prix à l'impératif (« envoyez-moi vos tarifs », « send me your price list »).
_INFORMATION_PATTERN = re.compile(
    _INFORMATION_PHRASES.pattern
    + r"|(?<!\w)(?:envoy|donn|communiqu|transmet|adress|pass)\w*[\s'\-]*(?:moi|nous|m'|me|us)?\s*(?:\w+\s+){0,2}"
    r"(?:tarifs?|prix|catalogue|brochure|documentation|devis)(?!\w)"
    r"|(?<!\w)(?:send|share|give|forward)\s+(?:me|us)\s+(?:\w+\s+){0,2}"
    r"(?:prices?|price\s+list|catalog(?:ue)?|brochure|quote|quotation|pricing)(?!\w)"
)
# Demande de coordonnées ou démarche administrative (adresse, facturation) : ne porte pas sur les
# produits (``other``, traité par l'équipe commerciale).
_CONTACT_DATA_PATTERN = _phrase_pattern((
    "adresse", "adresses", "address", "facturation", "facture", "factures", "invoice", "billing",
    "base de données", "database", "mot de passe", "password", "l3onwan", "lfacture", "العنوان", "عنوان",
    "الفاتورة", "فاتورة",
    "numéros", "numéro de téléphone", "numéros de téléphone", "numéro whatsapp", "numéros whatsapp",
    "téléphones", "coordonnées", "contacts", "adresses email", "adresses mail", "emails", "liste des clients",
    "liste de clients", "liste de vos clients", "fichier client", "fichier clients", "noms des clients",
    "phone numbers", "phone number", "contact details", "email addresses", "customer list", "client list",
    "numbers", "whatsapp numbers", "list of clients", "list of your clients", "list of customers", "your clients",
    "your customers", "other clients", "other customers", "vos clients", "autres clients", "leurs numéros",
    "nmar", "les numéros", "numerowat", "ar9am", "أرقام", "الأرقام", "أرقام الهواتف", "الهواتف", "هواتف",
    "العناوين", "لائحة الزبناء", "لائحة الكليان", "قائمة العملاء", "قائمة الزبناء", "معلومات الزبناء",
    "معلومات العملاء", "بيانات العملاء",
))
_TOPIC_WORDS = _folded((
    "sac", "sacs", "sachet", "sachets", "emballage", "emballages", "kraft", "papier", "poignée", "poignées",
    "anse", "anses", "produit", "produits", "catalogue", "bag", "bags", "paper", "handle", "handles",
    "packaging", "product", "products", "sachiyat", "sachia", "sachiya", "sachiat", "grammage", "grammages",
    "laize", "impression", "imprimer", "logo", "couleur", "couleurs", "taille", "tailles", "dimension",
    "dimensions", "format", "formats", "fermeture", "gramme", "grammes", "colle", "size", "sizes", "color",
    "colors", "colour", "print", "printing", "prix", "tarif", "tarifs", "délai", "délais", "stock", "minimum",
    "price", "prices", "cost", "taman", "tamane", "thaman", "livrez", "livrer", "livraison", "deliver",
    "delivery", "ship", "shipping", "échantillon", "échantillons", "sample", "samples", "disponible", "available",
    "lwan", "alwan", "tba3a", "tab3a", "tba3", "ntba3", "ntbaa", "ktbe3", "ktb3o",
    "devis", "quote", "quotation", "unité", "unités", "unit", "units", "pièce", "pièces", "piece", "pieces",
    "vente", "vendre", "vendez", "vend", "sell", "sells", "selling", "épaisseur", "thickness", "matière",
    "matériau", "matériaux", "material", "materials", "résistant", "résistants", "recyclé", "recyclable",
    "recycled", "weight", "weights", "poids", "kg", "kilo", "palette", "palettes", "abroad", "export",
    "kmya", "kamiya", "l9yas", "9yas", "qias", "lead", "turnaround", "timeline", "production", "manufacturing",
    "fabrication", "fabriquer", "fabriqués", "produce", "produire", "remise", "remises", "réduction",
    "rabais", "promo", "promotion", "discount", "discounts", "offre", "tkhfid", "takhfid", "تخفيض", "تخفيضات",
    "mo9ayssat", "m9ayssat", "9yassat", "mqasat", "maqasat",
    "commande", "commandes", "commander", "order", "ordering",
    "ساك", "ساشي", "ساشيات", "كيس", "أكياس", "اكياس", "كاغيط", "ورق", "منتج", "منتجات", "طباعة", "لون", "ألوان",
    "حجم", "مقاس", "سعر", "ثمن", "أسعار", "تمن", "مدة", "مقبض", "مقابض", "طلب", "توصيل", "عينة", "متوفر",
    "لوگو", "لوكو", "لوغو", "لوجو", "شعار", "نطبع", "نطبعو", "تطبعو", "كتطبعو", "كنطبع", "تطبع", "يطبع", "طباعه",
    "كوموند", "كومند", "كمية", "ادنى", "اقل", "عينة", "عروض", "عرض", "وزن", "مواد", "مادة", "ورقي", "تسليم",
    "شحن", "تصدير", "اللون", "شراء", "بيع", "تبيعون", "تبيعو",
))
# Verbe d'envoi au présent d'habitude de la darija (préfixe « ka- » / « ki- », « ك- ») : « katsifto »,
# « kaysifto », « كتصيفطو », « كيوصلو » (livrez-vous, livrent-ils).
_HABITUAL_DISPATCH_RE = re.compile(
    r"^(?:ka|ki)[nty](?:sift|sayft|wsl|wassl|wsel)[a-z]*$|^(?:كا|كي|ك)[نتي](?:صيفط|سيفط|وصل)[^\W\d_]*$"
)
# Proclitiques arabes retirés avant la comparaison aux sujets (« الساك », « بالأكياس »), puis marques
# du pluriel et du féminin (« مقاسات », « متوفرة »), en gardant un radical d'au moins trois lettres.
_ARABIC_ARTICLES = ("وال", "بال", "فال", "لل", "ال")
_ARABIC_SUFFIXES = ("ات", "ة", "ين", "ون")
_MIN_ARABIC_STEM = 3


# ---------------------------------------------------------------- règles

def _tokens(folded: str) -> list[str]:
    return _TOKEN_RE.findall(folded)


# Négation placée juste avant une expression, dans la même proposition (une virgule ou un point rompt la
# chaîne) : « ne m'appelez pas », « n'annulez surtout pas », « don't cancel », « pas besoin de me rappeler ».
_NEGATION_BEFORE_RE = re.compile(
    r"(?:(?<!\w)(?:ne\s+|n')|(?<!\w)(?:pas|plus|jamais|sans|never|no|not|cannot|لا|machi|mashi|[^\W_]+n't)['\s]+)"
    r"(?:[^\W_]+['\s]+){0,5}$"
)
# Expression qui contient elle-même sa négation (« pas un robot ») : la garde ne s'applique pas.
_SELF_NEGATED_RE = re.compile(r"^(?:pas|machi|mashi|ماشي|ليس|not|no)(?!\w)")


def _negated_before(folded: str, start: int) -> bool:
    """Vrai quand une négation précède, dans la même proposition, la position ``start``."""
    return _NEGATION_BEFORE_RE.search(folded[max(0, start - 60):start]) is not None


def _affirmed(pattern: re.Pattern[str], folded: str) -> str | None:
    """Première correspondance de ``pattern`` qui n'est pas niée (« annulez » oui, « n'annulez pas » non)."""
    for match in pattern.finditer(folded):
        text = match.group(0)
        if _SELF_NEGATED_RE.match(text):
            return text
        before = _tokens(folded[:match.start()])
        words = _tokens(text)
        darija_negated = bool(words) and is_darija_negation(words[0], before[-1] if before else "")
        if not darija_negated and not _negated_before(folded, match.start()):
            return text
    return None


def _search(pattern: re.Pattern[str], folded: str) -> str | None:
    match = pattern.search(folded)
    return match.group(0) if match else None


def _is_interrogative(folded: str) -> bool:
    return any(mark in folded for mark in _QUESTION_MARKS) or _QUESTION_START.search(folded) is not None


def is_yes(text: str | None) -> bool:
    """Vrai pour une réponse courte d'accord (« oui », « wakha », « نعم », « ok c'est bon »)."""
    return detect_intent(text, suspicious=False).intent == "confirm_yes"


def is_no(text: str | None) -> bool:
    """Vrai pour une réponse courte de refus (« non », « la », « لا », « non merci »)."""
    return detect_intent(text, suspicious=False).intent == "confirm_no"


def _is_lone_la(raw: str, folded: str, tokens: list[str]) -> bool:
    """Vrai quand « la » ouvre le message comme le « non » de la darija, et non comme l'article français.

    « la » doit être seul, suivi d'une ponctuation, ou suivi d'un mot de darija qui accompagne un
    refus (« la 3afak », « la a khoya », « la mabghitch ») ; « là » accentué n'est jamais un refus.
    """
    if not tokens or tokens[0] != "la" or "là" in raw.lower():
        return False
    if len(tokens) == 1 or re.match(r"^\W*la\s*[,.;!؟?،]", folded):
        return True
    following = tokens[1]
    return following in _DARIJA_AFTER_LA or is_darija_negation(following)


def _is_negated(word: str, previous: str) -> bool:
    """Verbe nié par la négation darija « ma … ch » (« matsiftch », « ma tsiftohach », « ما تصيفطش »)."""
    return word not in _NOT_NEGATIONS and is_darija_negation(word, previous)


def _short_reply(raw: str, folded: str, language: str | None) -> IntentResult | None:
    """Accord ou refus exprimé par une réponse courte, ou ``None``.

    Chaque mot doit être un mot d'accord, de refus, d'envoi ou un mot grammatical ; un mot de
    contenu inconnu laisse le message aux règles suivantes. Un mot de contraste (« mais ») sans
    refus annonce une réserve et écarte aussi la lecture comme simple accord.
    """
    if _NUMBER_RE.search(folded) or any(mark in folded for mark in _QUESTION_MARKS):
        return None
    if _NOT_YET_RE.search(folded) or _NEGATED_STATE_RE.search(folded):
        # « mazal ma wslatnich » : la négation porte sur un état attendu (suivi de commande), pas sur un refus.
        return None
    tokens = _tokens(folded)
    yes_emoji = any(emoji in raw for emoji in _YES_EMOJIS)
    no_emoji = any(emoji in raw for emoji in _NO_EMOJIS)
    if len(tokens) > MAX_REPLY_WORDS or not (tokens or yes_emoji or no_emoji):
        return None
    # Les refus sont retirés avant les accords : « pas d'accord » contient « d'accord » ; les
    # accords le sont avant le comptage des mots : « no problem » contient « no ». La négation
    # « ne … pas » est retirée la première, entière : « ce n'est pas correct ».
    without_no = _NO_PHRASES.sub(" ", _FRENCH_NEGATION_RE.sub(" ", folded))
    no_phrase = without_no != folded
    without_yes = _YES_PHRASES.sub(" ", without_no)
    yes_phrase = without_yes != without_no
    words = _tokens(_REPLY_OBJECTS.sub(" ", without_yes))
    contrast = [word for word in words if word in _CONTRAST_WORDS]
    words = [word for word in words if word not in _CONTRAST_WORDS]
    lone_no = _is_lone_la(raw, folded, tokens)
    no_words: list[str] = []
    yes_words: list[str] = []
    dispatch: list[str] = []
    for index, word in enumerate(words):
        previous = words[index - 1] if index else ""
        following = words[index + 1] if index + 1 < len(words) else ""
        if word in _NO_WORDS or _is_negated(word, previous):
            no_words.append(word)
        elif word in _NEGATION_PARTICLES and _is_negated(following, word):
            continue
        elif word in _YES_WORDS or _YES_SPELLING_RE.match(word):
            yes_words.append(word)
        elif _DISPATCH_VERB_RE.match(word):
            dispatch.append(word)
        elif not (word in _REPLY_FILLERS or word in _BENEFACTIVE_PRONOUNS or word in _GREETING_OPENERS
                  or (word == "la" and (lone_no or index))):
            return None
    if language in ("fr", "en") and yes_words == ["ah"] and not yes_phrase:
        yes_words = []
    if contrast and not (no_phrase or no_words or lone_no or no_emoji):
        # « oui mais … » annonce une réserve : ce n'est pas un accord sans condition.
        return None
    if no_phrase or no_words or lone_no or no_emoji:
        if no_phrase:
            return IntentResult("confirm_no", "refus")
        return IntentResult("confirm_no", no_words[0] if no_words else "la" if lone_no else "émoji")
    if yes_words or yes_phrase or yes_emoji or dispatch:
        matched = yes_words[0] if yes_words else "accord" if yes_phrase else "émoji" if yes_emoji else dispatch[0]
        return IntentResult("confirm_yes", matched)
    return None


def _refusal_opening(raw: str, folded: str, extraction: Mapping[str, Any], language: str | None) -> IntentResult | None:
    """Refus placé en tête d'une réponse courte et suivi d'une explication.

    « non, ce n'est pas la bonne quantité », « erreur, ce n'est pas le bon produit », « no, that's
    not what I ordered », « لا، ليس هذا » : le premier mot est un mot de refus, une expression de
    refus qui n'exprime pas une volonté, une copule niée (« ce n'est pas », « that's not ») ou le
    « la » de la darija (reconnu à sa position, ou à la langue détectée quand elle est la darija en
    lettres latines), après d'éventuelles hésitations (« hmm non », « plutôt non »). Le reste du
    message n'exprime aucune autre intention (cette règle passe après toutes les autres). Le message
    ne doit contenir ni nombre, ni question, ni élément de commande (client, produit, quantité) :
    « non, c'est pour la Boulangerie Nour » rectifie la commande en cours. Un message qui commence par
    une expression d'accord (« no problem », « لا بأس ») n'est pas un refus.

    Les accords ne reçoivent pas cette extension : un faux accord enregistrerait une commande,
    alors qu'un faux refus ne fait que redemander la modification voulue.
    """
    if _NUMBER_RE.search(folded) or any(mark in folded for mark in _QUESTION_MARKS):
        return None
    tokens = _tokens(folded)
    if not tokens or len(tokens) > REFUSAL_OPENING_MAX_WORDS:
        return None
    if any(name in extraction for name in _ORDER_FIELDS):
        return None
    start = re.match(r"\W*", folded).end()
    if _YES_PHRASES.match(folded, start):
        return None
    while len(tokens) > 1 and tokens[0] in _HEDGE_WORDS:
        tokens = tokens[1:]
        start = folded.index(tokens[0], start)
    darija_la = tokens[0] == "la" and language == "darija_latin"
    if (tokens[0] in _REFUSAL_OPENERS or darija_la or _is_lone_la(raw, folded, tokens)
            or _NO_OPENING_PHRASES.match(folded, start) or _NEGATIVE_COPULA_RE.match(folded[start:])):
        return IntentResult("confirm_no", tokens[0])
    return None


def _change_request(folded: str, extraction: Mapping[str, Any]) -> IntentResult | None:
    """Souhait de modifier la commande en cours, sans la valeur voulue (« je préfère modifier la quantité »).

    Le message ne doit ni citer un nombre, une question ou un élément de commande, ni désigner une commande
    déjà passée (« modifier ma commande CMD-12 » : demande traitée par l'équipe commerciale).
    """
    if _NUMBER_RE.search(folded) or any(mark in folded for mark in _QUESTION_MARKS):
        return None
    if any(name in extraction for name in _ORDER_FIELDS):
        return None
    if _MY_ORDER_PATTERN.search(folded) or _ORDER_REFERENCE_RE.search(folded) or _DEFINITE_ORDER_PATTERN.search(folded):
        return None
    tokens = _tokens(folded)
    if tokens and (tokens[0] in _YES_WORDS or _YES_PHRASES.match(folded, re.match(r"\W*", folded).end())):
        return None
    matched = _search(_MODIFY_PATTERN, folded)
    return IntentResult("confirm_no", matched) if matched else None


def _deferral(folded: str, extraction: Mapping[str, Any]) -> IntentResult | None:
    """Report de la décision (refus poli), dans un message sans question ni élément de commande."""
    if any(mark in folded for mark in _QUESTION_MARKS) or "quantite" in extraction or "type_produit" in extraction:
        return None
    matched = _search(_DEFERRAL_PHRASES, folded) or _search(_DEFERRAL_VERB_RE, folded)
    return IntentResult("confirm_no", matched) if matched else None


def _without_opening_greeting(folded: str) -> str:
    """Texte sans la salutation d'ouverture (« bonjour, », « salam khoya, ») qui précède la réponse.

    La salutation commence par un mot de salutation et ne contient ensuite que des mots de salutation. Le texte
    est rendu tel quel quand rien d'autre qu'une salutation ne le compose.
    """
    end = None
    for index, match in enumerate(_TOKEN_RE.finditer(folded)):
        word = match.group(0)
        if word not in (_GREETING_OPENERS if index == 0 else _GREETING_WORDS - _AMBIGUOUS_GREETING_WORDS):
            break
        end = match.end()
    if end is None:
        return folded
    rest = folded[end:]
    return rest if _TOKEN_RE.search(rest) else folded


def _greeting(folded: str) -> IntentResult | None:
    """Salutation seule, éventuellement suivie d'une question de politesse (« salut, ça va ? »), ou menu."""
    tokens = _tokens(folded)
    if not tokens or len(tokens) > MAX_SHORT_WORDS or _NUMBER_RE.search(folded):
        return None
    menu = [token for token in tokens if token in _MENU_WORDS]
    thanks = any(token in _THANKS_WORDS for token in tokens)
    if menu and not thanks and all(
            token in _MENU_WORDS or token in _GREETING_WORDS or token in _REPLY_FILLERS for token in tokens):
        return IntentResult("greeting", menu[0])
    well_being = _search(_WELL_BEING_PATTERN, folded)
    remaining = _tokens(_WELL_BEING_PATTERN.sub(" ", folded)) if well_being else tokens
    if all(token in _GREETING_WORDS for token in remaining):
        return IntentResult("greeting", remaining[0] if remaining else well_being)
    # Une salutation ou une question de politesse suivie de vœux ou de mots grammaticaux (« salut, bonne journée »,
    # « kidayr khoya labas ? 3afak ») reste une salutation ; des mots grammaticaux seuls n'en sont pas une.
    if (well_being or any(token in _GREETING_OPENERS for token in remaining)) and all(
            token in _GREETING_WORDS or token in _GREETING_WISHES or token in _REPLY_FILLERS for token in remaining):
        return IntentResult("greeting", remaining[0] if remaining else well_being)
    return None


def _cancel(folded: str, extraction: Mapping[str, Any]) -> IntentResult | None:
    """Annulation : verbe d'annulation, ordre d'arrêt, renoncement ou retrait de la commande.

    Le renoncement (« je n'en veux plus ») et le retrait (« oubliez la commande ») ne comptent que
    dans un message qui ne cite ni produit ni quantité : sinon la commande est remplacée par une autre.
    """
    without_replacement = _REPLACES_PATTERN.sub(" ", folded)
    matched = (_affirmed(_CANCEL_STEM_RE, without_replacement) or _affirmed(_CANCEL_VERB_RE, without_replacement)
               or _affirmed(_CANCEL_PATTERN, without_replacement)
               or _affirmed(_STOP_ORDER_PATTERN, without_replacement)
               or _affirmed(_STOP_STEP_PATTERN, without_replacement))
    if matched:
        return IntentResult("cancel", matched)
    if "quantite" in extraction or "type_produit" in extraction:
        return None
    unwanted = _search(_RENOUNCE_RE, folded) or _search(_WITHDRAW_ORDER_PATTERN, folded)
    return IntentResult("cancel", unwanted) if unwanted else None


def _human(folded: str) -> IntentResult | None:
    """Demande explicite d'un interlocuteur humain (rappel, refus du robot, personne demandée)."""
    folded = _COURTESY_CONTACT_PATTERN.sub(" ", folded)
    for pattern in (_HUMAN_REQUEST_PATTERN, _CALLBACK_PATTERN, _HUMAN_CONTACT_PATTERN, _HUMAN_WANT_PATTERN,
                    _HUMAN_CONNECT_PATTERN, _SOMEONE_REACHES_ME_RE, _SOMEONE_FOR_ME_RE, _SOMEONE_SPEAKS_RE):
        matched = _affirmed(pattern, folded)
        if matched:
            return IntentResult("human", matched)
    matched = _search(_ROBOT_REFUSAL_RE, folded)
    if matched:
        return IntentResult("human", matched)
    tokens = _tokens(folded)
    persons = [token for token in tokens if token in _STANDALONE_PERSONS]
    if persons and len(tokens) <= MAX_SHORT_WORDS and all(
            token in _STANDALONE_PERSONS or token in _STANDALONE_FILLERS for token in tokens):
        return IntentResult("human", persons[0])
    return None


def _has_order_content(folded: str, extraction: Mapping[str, Any]) -> bool:
    """Vrai pour une commande complète (quantité et produit) ou un renouvellement."""
    if extraction.get("is_reorder"):
        return True
    return "quantite" in extraction and ("type_produit" in extraction or _generic_product(folded) is not None)


def _generic_product(folded: str) -> str | None:
    return next((token for token in _tokens(folded) if token in _GENERIC_PRODUCT_WORDS), None)


def _order_number(folded: str, extraction: Mapping[str, Any]) -> str | None:
    """Nom de commande suivi d'un numéro (« la commande 1203 »), quand ce numéro n'est pas la quantité."""
    quantity = extraction.get("quantite")
    for match in _ORDER_NUMBER_RE.finditer(folded):
        if not isinstance(quantity, (int, float)) or float(quantity) != float(match.group(1)):
            return match.group(0)
    return None


def _follow_up(folded: str, extraction: Mapping[str, Any]) -> IntentResult | None:
    """Suivi d'une commande désignée par sa référence, par un possessif ou par un nom défini.

    - Référence (« CMD-12 », « la commande 1203 ») ou possessif (« ma commande ») : la désignation
      doit être accompagnée d'une question courte (« et ma commande ? »), d'une question avec un
      verbe d'état ou de temps (« la CMD-12 est partie ? », « c'est pour quand ma commande ? »), ou
      former à elle seule un message court sans quantité (« CMD-2026-0912 »).
    - Nom défini sans possessif (« la commande », « lcommande », « الطلبية ») : il faut une question,
      un verbe d'état conjugué (« imta ghatwsel lcommande? », « هل تم شحن الطلبية؟ ») et aucune
      quantité, ni tournure hypothétique ou question de prix et de délai, qui portent sur une
      commande future (« si je commande lundi, la commande arrive quand ? »).

    Une demande de modification (« modifier ma commande ») ou de passer commande (« passer ma
    commande ») n'est pas un suivi.
    """
    if _CHANGE_PATTERN.search(folded) or _PLACE_ORDER_PATTERN.search(folded):
        return None
    reference = _search(_ORDER_REFERENCE_RE, folded) or _order_number(folded, extraction)
    designation = (reference or _search(_MY_ORDER_PATTERN, folded) or _search(_ARABIC_MY_ORDER_RE, folded)
                   or _search(_PAST_ORDER_PATTERN, folded) or _search(_ARABIC_PAST_ORDER_RE, folded))
    asks = (_is_interrogative(folded) or _INDIRECT_QUESTION_PATTERN.search(folded) is not None
            or _DARIJA_WHERE_START_RE.match(folded) is not None)
    polite = _REQUEST_PATTERN.search(folded) is not None
    if designation is not None:
        short = sum(token not in _POLITE_WORDS for token in _tokens(folded)) <= SHORT_FOLLOW_UP_WORDS
        reference_only = short and reference is not None and "quantite" not in extraction
        state = _STATE_PATTERN.search(folded) or _STATE_VERB_RE.search(folded)
        if reference_only or ((asks or polite) and short) or (asks and state):
            return IntentResult("status", designation)
        # Demande polie sans question (« date de livraison de ma commande svp »), état nié ou attente
        # prolongée (« ma commande n'est pas arrivée », « I'm still waiting for my order ») : l'état doit
        # se trouver dans la même proposition que la désignation, sinon une formule de politesse ou
        # une phrase de bas de message (« if you have not received it… ») déciderait seule.
        clauses = [clause for clause in _CLAUSE_SPLIT_RE.split(folded) if designation in clause]
        if polite and "quantite" not in extraction and any(
                _STATE_PATTERN.search(clause) or _STATE_VERB_RE.search(clause) for clause in clauses):
            return IntentResult("status", designation)
        if any(_NEGATED_STATE_RE.search(clause) or _WAITING_PATTERN.search(clause) for clause in clauses):
            return IntentResult("status", designation)
        return None
    definite = _DEFINITE_ORDER_PATTERN.search(folded)
    if definite is None:
        return None
    if "quantite" not in extraction:
        for clause in _CLAUSE_SPLIT_RE.split(folded):
            waiting = _NEGATED_STATE_RE.search(clause) or _WAITING_PATTERN.search(clause)
            if waiting and _DEFINITE_ORDER_PATTERN.search(clause):
                return IntentResult("status", waiting.group(0))
    if (not asks or "quantite" in extraction or _HYPOTHETICAL_PATTERN.search(folded)
            or _QUOTE_QUESTION_PATTERN.search(folded)):
        return None
    state = _search(_STATE_VERB_RE, folded) or _search(_STATE_WORD_PATTERN, folded)
    return IntentResult("status", state) if state else None


def _order(folded: str, text: str, extraction: Mapping[str, Any]) -> IntentResult | None:
    """Commande : extraction par règles, verbe d'achat, demande de commander ou produit cité."""
    interrogative = _is_interrogative(folded)
    information = _INFORMATION_PATTERN.search(folded) is not None
    purchase = None if _NOT_PURCHASE_RE.search(folded) else (
        _search(_PURCHASE_PATTERN, folded) or _search(_PURCHASE_ADVERB_RE, folded)
        or _search(_IMPERATIVE_PURCHASE_RE, folded))
    if purchase is None and not (interrogative and _QUOTE_QUESTION_PATTERN.search(folded)):
        # Demande de livraison à la forme polie (« pouvez-vous me passer 2000 sacs ? ») : elle vaut commande,
        # sauf si la phrase demande un prix, un délai ou une disponibilité.
        purchase = _search(_DELIVERY_REQUEST_PATTERN, folded)
    product = extraction.get("type_produit") or normalize_product_type(text)
    if extraction["est_bon_commande"]:
        if "quantite" not in extraction and information:
            # « je voudrais des renseignements sur vos sachets » : demande d'information, sans quantité.
            return None
        if interrogative and purchase is None and _QUOTE_QUESTION_PATTERN.search(folded):
            # « combien coûtent 5000 sachets ? » : la quantité sert au calcul du prix.
            return None
        return IntentResult("order", "quantité" if "quantite" in extraction else "produit")
    if purchase and not information and (product or "quantite" in extraction or _generic_product(folded)):
        return IntentResult("order", purchase)
    matched = _search(_ORDER_VERB_PATTERN, folded)
    if matched and not interrogative:
        return IntentResult("order", matched)
    if not interrogative and not information and product:
        # Un produit cité sans question ni demande de renseignement (« des sachets fond plat »,
        # « commande de sacs premium ») commence une commande : les champs manquants seront demandés.
        return IntentResult("order", "produit")
    return None


def _arabic_forms(token: str) -> set[str]:
    """Formes d'un mot arabe sans proclitique (« الساك », « بالأكياس ») ni marque du féminin ou du
    pluriel (« المقاسات » → « مقاس », « المتوفرة » → « متوفر »)."""
    forms = {token}
    forms.update(token[len(article):] for article in _ARABIC_ARTICLES if token.startswith(article))
    forms.update(form[:-len(suffix)] for form in tuple(forms) for suffix in _ARABIC_SUFFIXES
                 if form.endswith(suffix) and len(form) - len(suffix) >= _MIN_ARABIC_STEM)
    # pluriel régulier en « ـات » : « عينات » est le pluriel de « عينة »
    forms.update(form[:-2] + "ة" for form in tuple(forms) if form.endswith("ات") and len(form) - 2 >= _MIN_ARABIC_STEM)
    return forms


def _topic(folded: str, text: str) -> str | None:
    """Sujet d'une question : produit, caractéristique, prix, délai ou livraison.

    Un mot est cherché tel quel, sans sa marque de pluriel française ou anglaise (« colours »), sans
    ses affixes arabes (:func:`_arabic_forms`) ; un verbe d'envoi (« katsifto », « expédiez ») pose une
    question de livraison quand il est au présent d'habitude de la darija (« katsifto », « كتصيفطو » :
    « livrez-vous ? »), qui exprime une pratique générale et non un envoi particulier.
    """
    for token in _tokens(folded):
        if token in _TOPIC_WORDS or (token.endswith(("s", "x")) and token[:-1] in _TOPIC_WORDS):
            return token
        if token.startswith(("livr", "exped")):
            return token
        if not token.isascii() and _arabic_forms(token) & _TOPIC_WORDS:
            return token
        if _HABITUAL_DISPATCH_RE.match(token):
            return token
    return "produit" if normalize_product_type(text) is not None else None


def _question(folded: str, text: str) -> IntentResult | None:
    if not _is_interrogative(folded) and _INFORMATION_PATTERN.search(folded) is None:
        return None
    if _CONTACT_DATA_PATTERN.search(folded):
        return None
    if _CHANGE_PATTERN.search(folded) and (_MY_ORDER_PATTERN.search(folded) or _ORDER_REFERENCE_RE.search(folded)):
        # « je voudrais modifier ma commande ? » : demande de modification, pas une question sur les produits.
        return None
    topic = _topic(folded, text)
    return IntentResult("question", topic) if topic is not None else None


def _classify(text: str, language: str | None, suspicious: bool) -> IntentResult:
    """Intention d'un texte (partie écrite par l'expéditeur ou message complet)."""
    folded = _REPEATED_LETTER_RE.sub(r"\1", fold(text))
    extraction = rule_based_extract(text)
    if suspicious:
        if extraction["is_reorder"]:
            return IntentResult("reorder", "renouvellement")
        if extraction["est_bon_commande"]:
            return IntentResult("order", "commande")
        return IntentResult("other", "injection")

    result = _cancel(folded, extraction)
    if result is not None:
        return result
    result = _human(folded)
    if result is not None and not _has_order_content(folded, extraction):
        return result
    reply = _without_opening_greeting(folded)
    for rule in (lambda: _short_reply(text, reply, language), lambda: _deferral(reply, extraction),
                 lambda: _greeting(folded)):
        result = rule()
        if result is not None:
            return result
    if extraction["is_reorder"]:
        return IntentResult("reorder", "renouvellement")
    matched = (_search(_STATUS_PATTERN, folded) or _search(_ARABIC_STATUS_RE, folded)
               or _search(_NOT_YET_RE, folded))
    if matched:
        return IntentResult("status", matched)
    for rule in (lambda: _follow_up(folded, extraction), lambda: _order(folded, text, extraction),
                 lambda: _question(folded, text), lambda: _change_request(reply, extraction),
                 lambda: _refusal_opening(text, reply, extraction, language)):
        result = rule()
        if result is not None:
            return result
    return IntentResult("other")


def detect_intent(text: str | None, language: str | None = None, *,
                  suspicious: bool | None = None) -> IntentResult:
    """Intention d'un message, selon les règles et la priorité de la documentation du module.

    Args:
        text: message du client (texte, transcription ou texte lu dans un média).
        language: code de langue détecté (``fr``, ``darija_latin``…), facultatif ; il lève
            l'ambiguïté de l'interjection « ah », qui n'est un accord qu'en darija.
        suspicious: résultat déjà connu du contrôle des injections ; ``None`` le calcule avec
            :func:`tecpap.security.guardrails.inspect_input` sur le message complet.

    Returns:
        L'intention et le mot ou l'expression qui l'a décidée.
    """
    if not isinstance(text, str) or not text.strip():
        return IntentResult("other")
    text = text[:MAX_ANALYSED_CHARS]
    if suspicious is None:
        suspicious = inspect_input(text).is_suspicious
    body = message_body(text)
    result = _classify(body, language, suspicious)
    if result.intent in ("other", "greeting") and fold(body).split() != fold(text).split():
        # Partie écrite sans demande (« Bonjour, ci-dessous notre commande ») : une commande
        # placée dans le message transféré est retenue.
        forwarded = _classify(text, language, suspicious)
        if forwarded.intent in ("order", "reorder"):
            return forwarded
    logger.debug("Intention détectée : %s (%s)", result.intent, result.matched)
    return result
