"""Signaux pondérés de la détection d'injection d'instructions.

Une injection d'instructions (« prompt injection », OWASP LLM01) est un texte rédigé pour
détourner le modèle de langue de sa tâche : remplacer ses instructions, lui faire divulguer
son prompt système (texte des consignes qui lui sont données), lui faire jouer un rôle sans
règles ou lui faire livrer des données d'autres clients. Elle peut être directe (message
WhatsApp) ou indirecte (texte caché dans un email, une pièce jointe ou une transcription).

Chaque :class:`Signal` regroupe des motifs en français, anglais, arabe standard, darija en
lettres latines (arabizi) et darija en lettres arabes, écrits sous la forme normalisée de
:mod:`tecpap.security.text` (minuscules, sans accents ni signes diacritiques). Un signal
compte une seule fois, avec son poids, quel que soit le nombre de ses motifs trouvés.

Principe de pondération : les tournures sans usage légitime dans une commande (« ignore
previous instructions », « <|im_start|> », requête SQL) atteignent seules le seuil de
suspicion (50) ; les indices ambigus (« autres clients », « base de données », « si tu es une
IA ») ont un poids faible et ne déclenchent une alerte qu'associés à d'autres signaux.
Les motifs de remplacement ou de divulgation d'instructions exigent un objet de type
« instructions / règles / consignes / prompt » et excluent les compléments métier (« instructions
de livraison », « consignes d'impression ») et les possessifs du client (« mes instructions »,
« my previous instructions ») : « ignorer ma commande précédente » ou « oubliez le sachet »
ne déclenchent rien.

Vues (voir :func:`tecpap.security.text.build_views`) : ``words`` (mots séparés par une espace,
cherchés aussi dans la vue ``leet``), ``lowered`` (ponctuation et retours à la ligne conservés)
et ``raw`` (casse conservée). Les expressions ``compact`` (sans espace) sont cherchées à
l'intérieur des mots, pour les textes écrits sans espace (« ignorepreviousinstructions ») ou
lettre par lettre.

Poids des signaux :

- 60 : ``instruction_override`` (remplacement des instructions), ``chat_template_token``
  (jetons de format de conversation) ;
- 55 : ``prompt_disclosure`` (divulgation du prompt système) ;
- 50 : ``json_role_marker``, ``sql_statement`` ;
- 45 : ``customer_data_request``, ``privileged_role``, ``jailbreak_mode``, ``delimiter_tag`` ;
- 40 : ``system_prompt_mention``, ``instructions_question``, ``system_role_marker``,
  ``instruction_heading``, ``environment_variable``, ``hidden_unicode_tags`` ;
- 35 : ``no_rules``, ``previous_text_override`` ;
- 30 : ``role_play``, ``assistant_role_marker``, ``admin_claim``, ``own_instructions_reference``,
  ``new_instructions``, ``other_customers_mention``, ``secret_mention``, ``forced_validation``,
  ``dan_acronym``, ``encoded_payload`` ;
- 25 : ``addressed_to_ai``, ``all_customers_mention``, ``zero_price``, ``decode_and_apply`` ;
- 20 : ``user_role_marker``, ``mixed_scripts`` ;
- 15 : ``database_mention``, ``encoding_mention``, ``invisible_characters`` ;
- 10 : ``spaced_letters``.

Les signaux ``encoded_payload``, ``hidden_unicode_tags``, ``mixed_scripts``,
``invisible_characters`` et ``spaced_letters`` sont calculés par
:mod:`tecpap.security.guardrails` (décodage, analyse des caractères) ; leurs poids sont définis ici.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tecpap.security.text import fold_characters

WORDS = "words"
LOWERED = "lowered"
RAW = "raw"


@dataclass(frozen=True)
class Signal:
    """Indice d'injection : nom stable, poids, explication en français et motifs.

    Attributes:
        name: identifiant du signal (enregistré dans le journal de sécurité).
        weight: points ajoutés au score quand au moins un motif est trouvé.
        reason: explication en français, reprise dans ``InputCheck.reasons``.
        patterns: couples (vue, expression régulière compilée).
        compact: expressions sans espace cherchées à l'intérieur des mots des vues ``words`` et ``leet``.
    """

    name: str
    weight: int
    reason: str
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()
    compact: tuple[str, ...] = ()


def _signal(name: str, weight: int, reason: str, *, words: tuple[str, ...] = (),
            lowered: tuple[str, ...] = (), raw: tuple[str, ...] = (), compact: tuple[str, ...] = ()) -> Signal:
    patterns = (
        [(WORDS, re.compile(fold_characters(source))) for source in words]
        + [(LOWERED, re.compile(fold_characters(source))) for source in lowered]
        + [(RAW, re.compile(source)) for source in raw]
    )
    return Signal(name, weight, reason, tuple(patterns), tuple(fold_characters(item) for item in compact))


# ---------------------------------------------------------------- fragments réutilisés

# Compléments métier : « instructions de livraison », « consignes d'impression »…
_FR_BUSINESS = (
    r"(?:livraisons?|expedition|transport|facturation|factures?|paiement|reglement|commandes?|bon|bons|"
    r"emballage|conditionnement|impression|fabrication|production|pliage|collage|montage|stockage|"
    r"utilisation|marquage|etiquetage|retour|chargement|dechargement|chauffeur|livreur|transporteur|"
    r"depot|quai|reception|maquette|logo|bat|devis|enlevement|palettes?|cartons?)"
)
_FR_NOT_BUSINESS = (
    rf"(?!(?: (?:de|d|du|des|pour|sur|a|au|aux|concernant|relatives?|liees?)"
    rf"(?: (?:la|le|l|les|votre|notre|vos|nos|ma|mon|mes|cette|ce))?)? {_FR_BUSINESS}\b)"
)
_EN_NOT_QUALIFIED = r"(?! (?:for|on|about|regarding|concerning|re|related to|of|from|in|to) )"
_EN_BUSINESS = (
    r"(?:delivery|deliveries|shipping|shipment|packaging|printing|payment|invoicing|invoice|order|orders|"
    r"pickup|returns?)"
)
_AR_NOT_BUSINESS = (
    r"(?! (?:ال)?(?:توصيل|تسليم|شحن|طلب|طلبيه|طلبيات|دفع|اداء|طباعه|تغليف|استعمال|استخدام|تخزين|فاتوره|فواتير)"
    r"| (?:الخاصه|المتعلقه) ب| ديال (?:ال)?(?:توصيل|ليفريزون|كوموند|طباعه))"
)
_DL_NOT_BUSINESS = (
    r"(?! (?:dyal|d) (?:livraison|tawsil|ttawsil|l?commande|lkomand|lkoumond|tba3a|ttba3a|impression|lkhlas|"
    r"lkhalas|facture))"
)

_FR_OVERRIDE_VERB = (
    r"(?:ignor(?:e|es|ez|er|ons)|oubli(?:e|es|ez|er|ons)|neglig(?:e|es|ez|er)|laiss(?:e|ez|er) tomber|"
    r"fai(?:s|tes) abstraction (?:de|des|d)|ne (?:tiens|tenez|tenir) (?:pas|plus) compte (?:de|des|d)|"
    r"pass(?:e|ez|er) outre(?: a| aux)?|outrepass(?:e|es|ez|er)|contourn(?:e|es|ez|er)|desactiv(?:e|es|ez|er)|"
    r"enfrein(?:s|t|dre)|transgress(?:e|ez|er)|ne (?:suis|suivez|respecte|respectez) (?:pas|plus))"
)
_FR_RULES_DET = r"(?:(?:toutes|tous|tout) )?(?:(?:les|tes|vos|ces|ton|ta|votre|l|le|la|cette|ce) )?"
_FR_RULES_NOUN = r"(?:instructions?|consignes?|regles?|directives?|prompts?|programmation)"
_FR_RULES_QUALIFIER = (
    r"(?: (?:precedentes?|precedents?|anterieures?|ci dessus|plus haut|initiales?|d origine|originales?|"
    r"systemes?|du systeme|internes?|cachees?|secretes?|de base|actuelles?|recues|de securite|"
    r"qu on t a donnees|que tu as recues))*"
)

_EN_OVERRIDE_VERB = (
    r"(?:ignore|ignoring|disregard|disregarding|forget|forgetting|skip|bypass|override|overrule|neglect|"
    r"discard|abandon|drop|do not follow|don t follow|dont follow|stop following|never mind|nevermind|"
    r"throw away|set aside|put aside)"
)
_EN_DET = r"(?:(?:all|any|every|each) )?(?:of )?(?:(?:the|your|these|those|this|its) )?"
_EN_ADJ = (
    r"(?:(?:previous|prior|above|earlier|preceding|foregoing|initial|original|old|existing|current|given|"
    r"system|developer|default|safety|security|hidden|secret|internal|preset) )*"
)
_EN_RULES_NOUN = (
    r"(?:instructions?|prompts?|rules|directives?|guidelines|guardrails|constraints|programming|"
    r"system (?:message|prompt)|context|policies|safeguards)"
)

_AR_OVERRIDE_VERB = (
    r"(?:و|ف)?(?:تجاهل|تجاهلي|تجاهلوا|اهمل|اهملي|انس|انسي|انسى|انسا|تناس|تناسي|تناسى|لا تتبع|"
    r"لا تلتزم ب|لا تلتزم|لا تطبق|لا تعمل ب|توقف عن اتباع|توقف عن تطبيق|تخل عن|تخلى عن|اترك|الغ|الغي)"
)
_AR_RULES = (
    r"(?:(?:كل|جميع|كافه|كامل) )?(?:ال)?(?:تعليمات|اوامر|قواعد|توجيهات|ارشادات|قيود|ضوابط|قوانين|"
    r"برومبت|موجه)(?:ك|كم)?\b"
)
_DL_FORGET = r"(?:nsa|nsay|nsaw|nsa3|ensa|insa|tnsa|nssa|nsi|ensay|ignori|skipi|skippi)"
_DL_RULES = (
    r"(?:(?:ga3|kolchi|kamlin|kaml|kamel) )?(?:(?:les|l|el|dok|dik|hadok|had|hadou) )?"
    r"(?:regles|regle|reglat|instructions?|consignes?|prompt|lprompt|qawa3id|9awa3id|lqawa3id|l9awa3id|"
    r"ta3limat|t3limat|tta3limat|lta3limat|qawanin|9awanin|lqawanin)"
)

_FR_DISCLOSE_VERB = (
    r"(?:montr(?:e|es|ez|er)|affich(?:e|es|ez|er)|donn(?:e|es|ez|er)|revel(?:e|es|ez|er)|"
    r"ecri(?:s|t|vez|re)|repet(?:e|es|ez|er)|recopi(?:e|es|ez|er)|copi(?:e|es|ez|er)|imprim(?:e|es|ez|er)|"
    r"di(?:s|tes|re)|envoi(?:e|es)|envoy(?:ez|er)|partag(?:e|es|ez|er)|list(?:e|es|ez|er)|cit(?:e|es|ez|er)|"
    r"communiqu(?:e|es|ez|er)|divulgu(?:e|es|ez|er)|expos(?:e|es|ez|er)|li(?:s|sez|re)|recit(?:e|es|ez|er)|"
    r"transmet(?:s|tez|tre)|coll(?:e|es|ez|er)|sor(?:s|tez|tir)|crach(?:e|ez|er)|balanc(?:e|ez|er)|"
    r"resum(?:e|es|ez|er)|tradui(?:s|sez|re)|explique(?:z|r)?)"
)
_FR_PRONOUN = r"(?: (?:moi|nous|m|nous en))?(?: (?:en entier|mot pour mot|integralement|exactement|tout|toutes|tous))?"
_FR_DISCLOSE_SYSTEM = (
    r"(?:(?:le|ton|votre|ta|la|les|tes|vos|l) )?(?:prompt|pre prompt|preprompt|message systeme|message system)"
    r"|(?:(?:les|tes|vos|la|ta|votre|ton|le|l) )?(?:instructions?|consignes?|regles?|directives?|configuration|"
    r"programmation) (?:systeme|system|du systeme|cachees?|secretes?|initiales?|internes?|d origine|de depart|"
    r"completes?|exactes?|confidentielles?|qu on t a donnees|que tu as recues|que tu as eues|de ton createur|"
    r"de tes createurs|de tes developpeurs)"
)
# « montre-moi le contenu de ton prompt », « show me the text of your instructions ».
_FR_DISCLOSE_CONTENT_OF = (
    r"(?: (?:le|la|une) (?:contenu|texte|copie|liste|totalite|version complete|integralite) (?:de|des|du|d))?"
)
_EN_DISCLOSE_CONTENT_OF = (
    r"(?: (?:the|a) (?:(?:full|entire|complete|exact) )?(?:content|contents|text|copy|wording|list) of)?"
)
_FR_DISCLOSE_YOURS = (
    r"(?:ton|ta|tes) (?:prompt|instructions?|consignes?|regles?|directives?|configuration|programmation|parametres)"
)

_EN_DISCLOSE_VERB = (
    r"(?:show|print|reveal|display|repeat|output|give|tell|write|leak|dump|share|list|recite|paste|echo|"
    r"return|spell out|type out|disclose|expose|send|provide|copy|translate|summari[sz]e|read|state|quote|"
    r"reproduce)"
)
_EN_DISCLOSE_SYSTEM = (
    r"(?:(?:the|your|its|this) )?(?:(?:full|entire|complete|exact|initial|original|hidden|secret|internal|"
    r"developer|system|first|whole|real) )*(?:system prompt|system message|system instructions|prompt|"
    r"pre ?prompt|initial instructions|hidden instructions|secret instructions|internal instructions|"
    r"original instructions|developer (?:message|instructions)|instructions (?:above|you were given|you received|"
    r"you got)|rules (?:above|you were given)|text above|words above|previous text|everything above|guardrails)"
)
_EN_DISCLOSE_YOURS = (
    r"your (?:(?:full|entire|complete|exact|initial|original|hidden|secret|internal|real|actual|underlying) )*"
    rf"(?:instructions|rules|guidelines|directives|programming|prompt|guardrails|restrictions){_EN_NOT_QUALIFIED}"
)

_AR_DISCLOSE_VERB = (
    r"(?:و|ف)?(?:اكتب|اكتبي|اعرض|اعرضي|اظهر|اظهري|اعطني|اعطيني|اعطنا|ارني|اريني|اطبع|انسخ|كرر|اعد كتابه|"
    r"عطيني|عطينا|وريني|ورينا|بين|بين لي|قل لي|قول ليا|قولي|اخبرني|خبرني|شارك|ارسل|صيفط|صيفطلي|اذكر|اكشف|"
    r"اكشف عن|افصح عن|ترجم)(?: (?:لي|ليا|لينا|لنا))?(?: (?:كامل|كل|جميع|نص))?"
)
_AR_SYSTEM_PROMPT = (
    r"(?:موجه النظام|رساله النظام|تعليمات النظام|اوامر النظام|قواعد النظام|برومبت النظام|البرومبت|"
    r"التعليمات المخفيه|القواعد المخفيه|التعليمات السريه|التعليمات الداخليه|التعليمات الاصليه)"
)
_AR_YOURS = r"(?:تعليماتك|اوامرك|قواعدك|ارشاداتك|توجيهاتك|برمجتك|(?:ال)?(?:تعليمات|قواعد|اوامر|قوانين|برومبت) ديالك)"

_DL_DISCLOSE_VERB = (
    r"(?:warini|wrini|werini|warrini|orini|warina|wrina|warili|3tini|a3tini|3tina|atini|kteb|ktebli|ktbli|"
    r"gol|goul|gouli|goli|9ol|9oul|9oli|golia|goulia|sift|siftli|siftlia|sifet|chareg|charek|bayen|biyen|"
    r"beyen|3awd|3awed|n9el|nqel|affichi|tl3|tle3)(?: (?:lia|lina|li|liya|lya))?"
)
_DL_RULES_OBJECT = (
    r"(?: (?:ga3|kamlin|kolchi|kaml|kamel))? (?:(?:les|l|el|dok|dik|had|hadok|hadou) )?"
    r"(?:instructions?|regles?|reglat|consignes?|prompt|lprompt|ta3limat|t3limat|tta3limat|lta3limat|"
    r"qawa3id|9awa3id|lqawa3id|l9awa3id|qawanin|9awanin|lqawanin)"
)

_FR_CUSTOMERS = r"(?:clients?|clientes?|utilisateurs|abonnes)"
_FR_NO_NEGATION = r"(?<!aucune )(?<!aucun )(?<!pas de )(?<!pas d )(?<!jamais de )(?<!jamais d )(?<!ni )"
_EN_CUSTOMERS = r"(?:customers?|clients?|users|buyers)"
_AR_CUSTOMERS = r"(?:ال)?(?:عملاء|زبناء|زبائن|زباين|كليان|مستخدمين)"
_DL_CUSTOMERS = r"(?:(?:les|l|el|lel) )?(?:clients?|klyan|lklyan|kliyan)"
_DL_OTHERS = (
    r"(?:lokhrin|lokhra|lakhrin|lakhrine|okhrin|ukhrin|akhrin|lokhrine|lo5rin|lou5rin|l5rin|lokhrien|"
    r"lokhrinin)"
)

_ADMIN_ROLE = (
    r"(?:admin|administrateur|administrator|admine|root|superuser|super utilisateur|super admin|superadmin|"
    r"developpeur|developer|sysadmin|moderateur|moderator|ادمين|مسؤول النظام|مدير النظام|المطور|مطور|الادمين)"
)


SQL_PATTERNS: tuple[str, ...] = (
    r"\bselect\s+(?:distinct\s+)?(?:\*|[\w.]+(?:\s*,\s*[\w.]+)+|[\w.]+\.\*)\s+from\s+[\w.\[\]\"`]+",
    r"\bselect\s+[\w.*]+\s+from\s+[\w.]+\s*(?:;|\bwhere\b|\bjoin\b|\blimit\b|\border\s+by\b|\bgroup\s+by\b)",
    r"\bunion\s+(?:all\s+)?select\b",
    r"\b(?:drop|truncate)\s+(?:table|database|schema)\b",
    r"\b(?:alter|create)\s+table\b",
    r"\bdelete\s+from\s+[\w.]+\s*(?:;|\bwhere\b|$)",
    r"\binsert\s+into\s+[\w.]+\s*(?:\(|\bvalues\b)",
    r"\bupdate\s+[\w.]+\s+set\s+[\w.]+\s*=",
    r"'\s*or\s+'?\d+'?\s*=\s*'?\d+",
    r"\bor\s+1\s*=\s*1\b",
    r"\bsqlite_master\b|\bpragma\s+table_info\b|\binformation_schema\b",
)
"""Motifs de requêtes SQL (vue ``lowered``), partagés par la détection en entrée et le filtre de sortie."""


SIGNALS: tuple[Signal, ...] = (
    _signal(
        "instruction_override", 60,
        "Consigne demandant d'ignorer ou d'oublier les instructions du système",
        words=(
            # « ignore les instructions précédentes », « oublie tes règles ». Le groupe des
            # qualificatifs est rendu atomique (anticipation + référence arrière) pour que
            # l'exclusion des compléments métier ne puisse pas être contournée par retour arrière.
            rf"\b{_FR_OVERRIDE_VERB} {_FR_RULES_DET}{_FR_RULES_NOUN}\b"
            rf"(?=(?P<q>{_FR_RULES_QUALIFIER}))(?P=q){_FR_NOT_BUSINESS}",
            r"\b(?:oubli(?:e|es|ez|er)|ignor(?:e|es|ez|er)) tout ce (?:qu on t a|qu on vous a|que tu as|qu il t a|"
            r"que l on t a|qu ils t ont) (?:dit|dits|donne|appris|demande|ecrit|recu|transmis|programme)",
            # « annule tes instructions », « remplacez vos consignes » : verbes de modification
            # suivis d'un possessif qui désigne l'assistant.
            r"\b(?:annul|supprim|effac|remplac|modifi|chang|reinitialis)\w* (?:(?:toutes|tous) )?(?:tes|ton|ta|vos|"
            r"votre) "
            rf"{_FR_RULES_NOUN}\b(?=(?P<q>{_FR_RULES_QUALIFIER}))(?P=q){_FR_NOT_BUSINESS}",
            rf"\b{_EN_OVERRIDE_VERB} {_EN_DET}{_EN_ADJ}{_EN_RULES_NOUN}\b{_EN_NOT_QUALIFIED}",
            r"\b(?:reset|clear|wipe|erase|delete|remove|replace|change|update|modify) (?:all )?your "
            r"(?:(?:previous|current|existing|system|original) )*(?:instructions|rules|prompt|guidelines|programming|"
            rf"directives)\b{_EN_NOT_QUALIFIED}",
            r"\b(?:forget|ignore|disregard) (?:all |everything |anything )?(?:that |what )?you "
            r"(?:were|have been|ve been|got) (?:told|given|instructed|programmed|taught)",
            r"\b(?:ignora|ignore|olvida|olvide|omite) (?:todas )?(?:(?:las|tus|sus) )?(?:instrucciones|reglas|"
            r"indicaciones)\b",
            rf"(?<!\w){_AR_OVERRIDE_VERB} {_AR_RULES}{_AR_NOT_BUSINESS}",
            r"(?<!\w)(?:و|ف)?(?:تجاهل|انس|انسى|انسا|تناس|اهمل) (?:كل |جميع )?(?:ما|الذي|اللي) "
            r"(?:قيل لك|قالوه لك|طلب منك|اعطي لك|تعلمته|برمجت عليه)",
            r"(?<!\w)(?:و|ف)?(?:نسا|نسى|انسا|انسى|تنسا|تنسى|نساي|تجاهل) (?:(?:كاع|كلشي|كل|جميع|كامل) )?"
            r"(?:(?:داكشي|هادشي|الشي|شي|ما|ديك|داك) )?(?:لي|اللي|الي|الذي) (?:قالو|قالوا|قال|كالو|كال|قالولك|كالولك|"
            r"قريتي|تعلمتي|عطاوك|عطاوهولك|برمجوك)",
            r"(?<!\w)(?:و|ف)?(?:نسا|نسى|انسا|انسى|تنسا|تنسى|نساي|ما تتبعش|ماتتبعش) (?:(?:كاع|كلشي|كل|جميع) )?"
            rf"(?:ال)?(?:قواعد|تعليمات|اوامر|قوانين|برومبت)(?: (?:ديالك|ديالكم))?\b{_AR_NOT_BUSINESS}",
            rf"\b{_DL_FORGET} {_DL_RULES}\b{_DL_NOT_BUSINESS}",
            r"\b(?:nsa|nsay|ensa|insa|tnsa|nssa|ignori) (?:(?:ga3|kolchi|kamel) )?(?:dakchi|dak chi|chi|kolchi) "
            r"(?:li|lli|elli) (?:galo|gal|9alo|9al|galou|9alou|kalou|galoulek|9aloulek|galolik|9alolik|t3elmti|"
            r"ta3lamti|3tawk|3tawek|brmjok)",
        ),
        compact=(
            "ignorepreviousinstructions", "ignoreallpreviousinstructions", "ignoreallinstructions",
            "ignoreyourinstructions", "ignoretheinstructions", "ignorepriorinstructions",
            "disregardpreviousinstructions", "disregardallpreviousinstructions", "disregardyourinstructions",
            "forgetpreviousinstructions", "forgetallpreviousinstructions", "forgetyourinstructions",
            "forgetyourrules", "ignoreyourrules", "ignorelesinstructions", "ignoreztouteslesinstructions",
            "ignoretouteslesinstructions", "ignoretesinstructions", "ignorezvosinstructions",
            "oublietesinstructions", "oublietesregles", "oubliezvosinstructions", "oubliezvosregles",
            "oublietesconsignes", "تجاهلالتعليمات", "تجاهلجميعالتعليمات", "تجاهلكلالتعليمات",
            "nsalesregles", "nsaga3lesregles",
        ),
    ),
    _signal(
        "previous_text_override", 35,
        "Consigne demandant d'ignorer tout le texte qui précède",
        words=(
            r"\b(?:oubli(?:e|ez)|ignor(?:e|ez)) (?:tout )?(?:ce qui (?:precede|est au dessus|est ci dessus|a ete dit)|"
            r"tout ce qui precede|le texte (?:ci dessus|precedent|au dessus))",
            r"\b(?:ignore|disregard|forget) (?:all|everything|anything) (?:above|before|previously|so far|until now|"
            r"up to now)\b",
            r"\b(?:ignore|disregard|forget) (?:the|what is|what was|what s) (?:above|preceding|previous text|"
            r"written above|said above)\b",
            r"(?<!\w)(?:و|ف)?(?:تجاهل|انس|انسى|تناس|اهمل) (?:كل )?ما (?:سبق|تقدم|كتب اعلاه|ورد اعلاه)",
        ),
    ),
    _signal(
        "prompt_disclosure", 55,
        "Demande de divulgation du prompt système (instructions données au modèle)",
        words=(
            rf"\b{_FR_DISCLOSE_VERB}{_FR_PRONOUN}{_FR_DISCLOSE_CONTENT_OF} (?:{_FR_DISCLOSE_SYSTEM}|"
            rf"{_FR_DISCLOSE_YOURS})\b",
            r"\bquel(?:le)?s? (?:sont|est|etaient|etait) (?:tes|ton|ta) (?:instructions?|consignes?|regles?|"
            r"directives?|prompt|programmation)\b",
            r"\bquel(?:le)?s? (?:sont|est) (?:(?:les|vos|votre|le) )?(?:instructions?|consignes?|regles?|prompt) "
            r"(?:systeme|cachees?|secretes?|initiales?|internes?|qu on (?:t|vous) a donnees)",
            rf"\b{_EN_DISCLOSE_VERB}(?: (?:me|us|back))*{_EN_DISCLOSE_CONTENT_OF} "
            rf"(?:{_EN_DISCLOSE_SYSTEM}|{_EN_DISCLOSE_YOURS})",
            r"\bwhat (?:are|were|is|was) (?:your|the) (?:(?:system|initial|original|hidden|secret|exact|full|"
            r"internal) )+"
            r"(?:instructions|rules|prompt|guidelines|directives|message)\b",
            rf"(?<!\w){_AR_DISCLOSE_VERB} (?:{_AR_SYSTEM_PROMPT}|{_AR_YOURS})",
            r"(?<!\w)(?:ما هي|ماهي|ما|شنو هي|اشنو هي|شنو|اشنو|شنوما|اش|واش) (?:هي )?(?:ال)?(?:تعليمات|اوامر|قواعد|"
            r"توجيهات|ارشادات)(?:ك)? (?:التي|الذي|اللي|لي|الي) (?:اعطيت|اعطوك|عطاوك|تلقيت|تلقيتها|قالو|كالو|زودت|"
            r"زودوك|اعطاها|عطاوهالك)",
            r"(?<!\w)(?:ما هي|ماهي|شنو هي|اشنو هي|شنو|اشنو) (?:تعليماتك|اوامرك|قواعدك|ارشاداتك|توجيهاتك)\b",
            rf"\b{_DL_DISCLOSE_VERB}{_DL_RULES_OBJECT}\b{_DL_NOT_BUSINESS}",
        ),
        compact=(
            "printyoursystemprompt", "showmeyourprompt", "showyourprompt", "revealyourprompt",
            "revealyoursystemprompt", "showmeyoursystemprompt", "montretonprompt", "montremoitonprompt",
            "affichetonprompt", "donnemoitonprompt", "warinilesinstructions",
        ),
    ),
    _signal(
        "system_prompt_mention", 40,
        "Mention du prompt système ou d'instructions cachées",
        words=(
            r"\b(?:prompt (?:systeme|system|initial|cache|secret)|system prompt|systemprompt|system message|"
            r"message systeme|initial prompt|hidden prompt|developer message|pre prompt|preprompt|"
            r"meta prompt|metaprompt)\b",
            r"\b(?:instructions?|consignes?|regles?) (?:systeme|cachees?|secretes?|confidentielles?|"
            r"internes de l assistant)\b",
            r"\b(?:hidden|secret|confidential) (?:instructions|rules|prompt|guidelines)\b",
            r"\byour (?:prompt|system prompt)\b",
            rf"(?<!\w){_AR_SYSTEM_PROMPT}\b",
        ),
        compact=("systemprompt", "promptsysteme", "موجهالنظام"),
    ),
    _signal(
        "instructions_question", 40,
        "Question sur les instructions reçues par l'assistant",
        words=(
            rf"\bwhat (?:are|were) your (?:instructions|rules|guidelines|directives){_EN_NOT_QUALIFIED}",
            r"\bwhat (?:were|have) you (?:been )?(?:told|instructed|programmed)\b",
            r"\bqu est ce qu on (?:t|vous) a (?:dit|demande|ordonne|programme|donne comme (?:instructions|consignes|"
            r"regles))\b",
            r"\bce qu on t a (?:dit|demande) de (?:faire|dire|repondre|cacher|taire|ne pas dire|ne pas reveler)\b",
            r"\bc est quoi (?:ton|ta|tes) (?:prompt|instructions?|consignes?|regles?|directives?|programmation)\b",
            r"\b(?:chno|achno|ach|chnou|achnou|chnahoma|achnahoma|chnahouma|chnoma|ach hiya|chno hiya) "
            r"(?:(?:homa|houma|hiya|hia|hya|howa|houwa|hwa) )?(?:(?:les|l) )?(?:instructions|regles|consignes|"
            r"ta3limat|qawa3id|9awa3id|prompt|lprompt|qawanin)\b",
            r"(?<!\w)(?:شنو|اشنو|شنوما|اش|واش) (?:هوما |هي |هو )?(?:ال)?(?:تعليمات|قواعد|اوامر|قوانين|برومبت)\b",
        ),
    ),
    _signal(
        "own_instructions_reference", 30,
        "Référence aux instructions propres de l'assistant",
        words=(
            r"\b(?:tes|ton|ta) (?:instructions?|consignes?|regles?|directives?|prompt|programmation|createurs?|"
            r"developpeurs?)\b",
            r"\b(?:(?:les|l|el) )?(?:instructions|regles|consignes|prompt|lprompt|ta3limat|lta3limat|qawa3id|"
            r"lqawa3id|9awa3id|l9awa3id|qawanin|9awanin)(?: (?:kamlin|kolhom|ga3))? (?:dyal(?:k|ek|kom|kum)|dyalek)\b",
            r"\b(?:chno|achno|ach|chnou|achnou) (?:katgoul|kaygoul|kaygoulo|kaygolo|katgol|kaygol|galt|galo|gal|9al|"
            r"9alo|galou|9alou|kat9oul|kay9oul|kay9olo|t9al|tgal|3tawk|3tawek|kaytlbo|katlab) (?:lik|lek|lk|lak)\b",
            rf"(?<!\w){_AR_YOURS}\b",
            r"(?<!\w)(?:ال)?(?:تعليمات|قواعد|اوامر|برومبت|توجيهات|موجه) (?:الخاصه بك|الخاص بك|التي لديك|الذي لديك|"
            r"اللي عندك|لي عندك)\b",
        ),
    ),
    _signal(
        "role_play", 30,
        "Tentative de changement de rôle (jeu de rôle)",
        words=(
            r"\b(?:tu es|t es|vous etes) (?:maintenant|desormais|a present|dorenavant|a partir de maintenant|"
            r"devenue?)\b"
            r"(?! (?:(?:notre|nos|mon|ma|mes|le|un|l|leur) )?(?:principal |nouveau |seul |unique )?"
            r"(?:fournisseurs?|partenaires?|prestataires?|clients?|distributeurs?|livreurs?|fabricants?)\b)"
            r"(?! (?:notre|nos|mon|ma|mes)\b)",
            r"\b(?:a partir de maintenant|des maintenant|desormais|dorenavant|a partir d aujourd hui) "
            r"(?:tu es|tu seras|tu vas etre|tu agis|tu joues|tu deviens|tu n as|tu ne)\b",
            r"\b(?:jou(?:e|ez|er)) (?:le role|un role|le personnage)\b",
            r"\b(?:fai(?:s|tes|re)) (?:semblant|comme si tu)\b",
            r"\b(?:incarn(?:e|ez)|comporte toi|comportez vous|agis|agissez|repond(?:s|ez)) (?:comme|en tant qu|"
            r"en tant que|tel)"
            r"(?: (?:un|une|l))? (?:ia|intelligence artificielle|assistant|bot|robot|chatbot|modele|ai|llm|gpt|"
            r"admin|administrateur|developpeur|hacker|pirate|personnage|libre|sans)\b",
            r"\btu (?:deviens|vas devenir|es devenu)\b",
            r"\bimagine que tu (?:es|n as|n es)\b",
            r"\b(?:nouveau|nouvelle) (?:role|personnage|personnalite|identite)\b",
            r"\byou are now\b(?! (?:(?:our|my|the|a|an) )?(?:(?:main|preferred|official|new|exclusive|sole|primary) )?"
            r"(?:supplier|partner|vendor|provider|distributor|customer|manufacturer)s?\b)",
            r"\bfrom now on,? you (?:are|act as|will act|will be|play|have no|re)\b",
            r"\b(?:act|behave|respond|answer) as (?:if you were |though you were )?(?:(?:a|an|the) )?(?:ai|assistant|"
            r"bot|chatbot|model|llm|gpt|admin|administrator|developer|hacker|dan|unrestricted|unfiltered|evil|"
            r"jailbroken|different|new)\b",
            r"\b(?:act|behave) as (?:if|though|my|a|an|the)\b",
            r"\bpretend (?:to be|you are|you re|that you|you have)\b",
            r"\b(?:role ?play|roleplay)\b",
            r"\bplay the (?:role|part) of\b",
            r"\bimagine (?:you are|you re|that you are)\b",
            r"\bswitch to (?:(?:a|an) )?(?:(?:new|different) )?(?:mode|persona|personality|role)\b",
            r"\b(?:new|different) (?:persona|personality|identity)\b",
            r"(?<!\w)(?:انت|انتي|انتم) (?:الان|الحين|من الان|اصبحت)\b(?! (?:مورد|موردنا|المورد|شريك|شريكنا))",
            r"(?<!\w)من الان (?:فصاعدا )?(?:انت|ستكون|ستصبح|تصرف)\b",
            r"(?<!\w)(?:تظاهر|تقمص|العب دور|مثل دور|تصرف ك|تصرف مثل|تخيل انك)",
            r"(?<!\w)(?:دابا|دبا) (?:نتا|نتي|انت|راك)\b(?! (?:مورد|الفورنيسور|فورنيسور))",
            r"(?<!\w)(?:نتا|نتي|انت) (?:دابا|دبا)\b",
            r"(?<!\w)(?:دير|ديري) راسك\b",
            r"(?<!\w)تخيل راسك\b",
            r"\b(?:daba|db|dba) (?:nta|nti|enta|anta|rak)\b",
            r"\b(?:nta|nti|enta|anta) (?:daba|db|dba)\b",
            r"\b(?:welliti|wliti|weliti|walliti)\b",
            r"\b(?:dir|diri) (?:rasek|rask|rassek|rasak) (?:bhal|b7al|ki|kif)\b",
            r"\btkhayel (?:rasek|rask|bli nta)\b",
        ),
    ),
    _signal(
        "privileged_role", 45,
        "Attribution d'un rôle privilégié à l'assistant (administrateur, développeur)",
        words=(
            r"\b(?:tu es|t es|vous etes|you are|you re|u r|nta|nti|enta|anta|راك|انت|نتا|نتي)"
            r"(?: (?:maintenant|desormais|now|daba|db|الان|دابا))?(?: (?:un|une|l|le|the|an|a|chi))? "
            rf"{_ADMIN_ROLE}\b",
            rf"\b(?:welliti|wliti|weliti|walliti|وليتي|وليت) (?:(?:l|el) )?{_ADMIN_ROLE}\b",
            rf"\b(?:passe|passez|switch|bascule|basculez) (?:en|to|au) (?:mode )?{_ADMIN_ROLE}\b",
        ),
    ),
    _signal(
        "admin_claim", 30,
        "Revendication de droits d'administrateur",
        words=(
            r"\b(?:l utilisateur|cet utilisateur|the user|this user|user|je suis|i am|i m|ana|انا|المستخدم)"
            r"(?: (?:est|is))?(?: (?:un|une|l|le|the|an|a))? (?:admin|administrateur|administrator|root|superuser|"
            r"developpeur|developer|sysadmin|ادمين|مسؤول النظام|المطور)\b",
            r"\b(?:droits?|acces|privileges?|permissions?) (?:d )?(?:admin|administrateur|root|complets?|illimites?)\b",
            r"\b(?:admin|administrator|root|full|unlimited) (?:access|rights|privileges|permissions)\b",
        ),
    ),
    _signal(
        "jailbreak_mode", 45,
        "Mode de contournement des règles (mode développeur, jailbreak, DAN)",
        words=(
            r"\bmode (?:developpeur|dev|debug|debogage|maintenance|admin|administrateur|dieu|sans restriction|"
            r"sans filtre|jailbreak|root|super utilisateur|dan)\b",
            r"\b(?:developer|dev|debug|maintenance|admin|god|jailbreak|unrestricted|unfiltered|sudo|root|dan|"
            r"evil) mode\b",
            r"\bjailbreak(?:ed|ing|e|er)?\b",
            r"\bdo anything now\b",
            r"\b(?:tu es|t es|vous etes|you are|you re|nta|nti|انت|نتا|نتي) (?:(?:maintenant|desormais|now|"
            r"daba) )?dan\b",
            r"(?<!\w)وضع (?:المطور|الصيانه|المسؤول|الادمين|التصحيح|غير مقيد)",
            r"\bmode (?:dyal )?(?:developpeur|admin)\b",
        ),
        compact=(
            "developermode", "modedeveloppeur", "doanythingnow", "jailbreak", "maintenancemode", "debugmode",
            "godmode",
        ),
    ),
    _signal(
        "dan_acronym", 30,
        "Acronyme DAN (« Do Anything Now », personnage censé ne suivre aucune règle)",
        raw=(r"\bDAN\b",),
    ),
    _signal(
        "no_rules", 35,
        "Demande de fonctionner sans règles ni restrictions",
        words=(
            r"\b(?:tu n as|vous n avez|n a|n ont|il n y a) (?:(?:plus|aucune|pas de|aucun) )+(?:regles?|restrictions?|"
            r"limites?|filtres?|censure|interdits?)\b",
            r"\b(?:libere|libre) de (?:toute|toutes|tes|vos) (?:regles?|restrictions?|contraintes?)\b",
            r"\bsans (?:(?:aucune|plus de|la moindre) )?(?:restrictions?|limites?|filtres?|censure|regles?|"
            r"garde fous?)\b"
            r"(?! (?:de|d|sur|horaires?|quantite|date|delai|couleurs?|taille|minimum|volume)\b)",
            r"\b(?:no|without|free of|free from|zero) (?:any )?(?:rules|restrictions|limits|limitations|filters|"
            r"censorship|guidelines|guardrails|boundaries)\b(?! (?:on|for|regarding) )",
            r"\b(?:has|have) no (?:rules|restrictions|limits|filters|guidelines)\b(?! (?:on|for|regarding) )",
            r"\bignore (?:(?:your|all|the) )?(?:safety|content|ethical|moral) (?:rules|guidelines|policies|filters|"
            r"settings)\b",
            r"\b(?:uncensored|unfiltered|unrestricted)\b",
            r"(?<!\w)(?:بدون|بلا|دون) (?:اي )?(?:قيود|قواعد|حدود|رقابه|فلتر|ضوابط)\b",
            r"\bbla (?:(?:ga3|ma) )?(?:(?:les|l) )?(?:regles|qawa3id|9awa3id|restrictions|limites|qanoun)\b",
        ),
    ),
    _signal(
        "chat_template_token", 60,
        "Jeton de format de conversation (« <|im_start|> », « [INST] »…)",
        lowered=(
            r"<\|\s*(?:im_start|im_end|im_sep|system|user|assistant|endoftext|begin_of_text|end_of_text|"
            r"start_header_id|end_header_id|eot_id|eom_id|fim_[a-z]+|tool|pad)\s*\|>",
            r"\[/?inst\]",
            r"<<\s*/?\s*sys\s*>>",
            r"<\s*/?\s*(?:start_of_turn|end_of_turn)\s*>",
            r"\[/?(?:system_prompt|sys)\]",
        ),
    ),
    _signal(
        "system_role_marker", 40,
        "Faux marqueur de rôle système (« system: »)",
        lowered=(
            r"(?m)(?:^|[.!?;]\s+)[ \t>*#\-\[(]*(?:\*\*|__)?(?:system|developer|sys)(?:\*\*|__)?\s*[\])]?\s*:",
            r"\brole\s*[:=]\s*(?:system|developer)\b",
        ),
    ),
    _signal(
        "assistant_role_marker", 30,
        "Faux marqueur de rôle assistant (« assistant: »)",
        lowered=(
            r"(?m)^[ \t>*#\-\[(]*(?:\*\*|__)?(?:assistant|ai|ia|bot|chatbot|gpt|chatgpt|model|modele|llm)(?:\*\*|__)?"
            r"\s*[\])]?\s*:",
        ),
    ),
    _signal(
        "user_role_marker", 20,
        "Faux marqueur de rôle utilisateur (« user: »)",
        lowered=(r"(?m)^[ \t>*#\-\[(]*(?:\*\*|__)?(?:user|utilisateur|human|humain)(?:\*\*|__)?\s*[\])]?\s*:",),
    ),
    _signal(
        "json_role_marker", 50,
        "Faux message structuré avec un rôle (« \"role\": \"system\" »)",
        lowered=(
            r"[\"'`]role[\"'`]\s*:\s*[\"'`](?:system|developer|assistant|admin|tool|function)[\"'`]",
            r"[\"'`](?:system|system_prompt|systemprompt|instructions|developer)[\"'`]\s*:",
        ),
    ),
    _signal(
        "instruction_heading", 40,
        "Titre ou bloc de code présenté comme des instructions (« ### instruction »)",
        lowered=(
            r"(?m)^\s*#{1,6}\s*(?:system|instructions?|new instructions|nouvelles instructions|nouvelles consignes|"
            r"prompt|consignes|rules|admin|developer|override|jailbreak)\b(?!\s+(?:de|d|du|des|pour|for|on|of)\b)",
            r"```\s*(?:system|instructions?|prompt|admin|developer|sys)\b",
        ),
    ),
    _signal(
        "delimiter_tag", 45,
        "Balise de délimitation ou de rôle insérée dans le message",
        lowered=(
            r"<\s*/?\s*(?:system|sys|assistant|user|instructions?|prompt|admin|developer|tool|function|"
            r"contenu_a_analyser|contenu a analyser|message_client|message client|extrait|context|contexte|human)"
            r"\s*(?:\s[^<>\n]{0,100})?>",
            r"\[\s*/?\s*(?:system|sys|assistant|admin|developer|instructions?)\s*\]",
        ),
    ),
    _signal(
        "addressed_to_ai", 25,
        "Message adressé au système automatique qui lit le texte",
        words=(
            r"\b(?:note|message|consigne|instruction|info|information|remarque|avis|mot|precision|important|"
            r"attention)s? "
            r"(?:pour|a|au|aux|a l attention de|a l intention de|destinee? a|adressee? a) (?:(?:l|la|le|les|cet|"
            r"cette|ce) )?"
            r"(?:assistants?|ia|intelligence artificielle|robots?|bots?|chatbots?|modeles?(?: de langage)?|"
            r"systemes? automatiques?|agents?(?: automatiques?)?|programmes?|lecteurs? automatiques?|"
            r"outils? automatiques?|ai|llm|gpt|chatgpt|automate)\b",
            r"\b(?:si tu es|si vous etes) (?:(?:une|un|l|le) )?(?:ia|intelligence artificielle|"
            r"assistant(?: virtuel| automatique)?|robot|bot|chatbot|modele de langage|llm|ai|gpt|chatgpt)\b",
            r"\b(?:qui lit|qui lis|qui lira|qui analyse|qui traite) (?:ce|cet|cette) (?:message|email|mail|document|"
            r"texte|fichier)\b",
            r"\b(?:cher|chere) (?:assistant|ia|robot|bot|chatbot|modele)\b",
            r"\b(?:note|message|instruction|attention|important|notice|memo)s? (?:to|for) (?:(?:the|any|all) )?"
            r"(?:ai|assistant|bot|chatbot|model|llm|language model|automated (?:system|reader|assistant|agent)|"
            r"automatic (?:reader|system)|agent|gpt|chatgpt)s?\b",
            r"\bif you are (?:(?:an|a|the) )?(?:ai|assistant|bot|chatbot|language model|llm|gpt|automated system)\b",
            r"\b(?:to|for) (?:(?:the|any) )?(?:ai|llm|language model|automated system) (?:reading|processing|parsing|"
            r"analy[sz]ing) (?:this|these)\b",
            r"\bdear (?:ai|assistant|bot|chatbot|model|gpt)\b",
            r"(?<!\w)(?:ملاحظه|رساله|تنبيه|تعليمات|ملاحظات) (?:الى|ل|لل)? ?(?:ال)?(?:مساعد|ذكاء الاصطناعي|روبوت|بوت|"
            r"نموذج|النظام الالي|المساعد الالي)",
            r"(?<!\w)اذا كنت (?:ذكاء اصطناعي|مساعدا?|روبوتا?|نموذجا?|بوت)",
            r"\b(?:ila kunti|ila konti|ila kenti|ila nta) (?:robot|bot|ia|ai|chatbot|machine)\b",
        ),
    ),
    _signal(
        "new_instructions", 30,
        "Annonce de nouvelles instructions pour l'assistant",
        words=(
            r"\b(?:nouvelles?|vraies?|veritables?) (?:instructions?|consignes?|regles?|"
            rf"directives?)\b{_FR_NOT_BUSINESS}",
            r"\btes (?:(?:nouvelles|vraies|seules) )?(?:instructions?|consignes?|regles?|missions?|taches?|roles?) "
            r"(?:sont|est|devient|deviennent)\b",
            r"\b(?:new|updated|real|actual|true|revised|override|overriding) (?:instructions?|rules|directives?|"
            r"system prompt|prompt|guidelines)\b(?!(?: (?:for|on|about|regarding|concerning|of|"
            rf"re))? {_EN_BUSINESS}\b)",
            r"\byour (?:(?:new|real|actual|true) )?(?:instructions|rules|task|role|purpose|goal|job|mission) "
            r"(?:is|are|has changed|have changed)\b",
            r"(?<!\w)(?:تعليمات|اوامر|قواعد) (?:جديده|الجديده)\b(?! (?:ل|لل|ب|بخصوص|حول)?(?:ال)?(?:توصيل|تسليم|شحن))",
            r"(?<!\w)مهمتك (?:الجديده )?(?:هي|الان)\b",
            r"\b(?:instructions|regles|consignes|ta3limat|qawa3id) (?:jdad|jdida)\b",
            r"\bdaba (?:l|el)?(?:mohima|mouhima|khedma|mission) dyalk\b",
        ),
    ),
    _signal(
        "customer_data_request", 45,
        "Demande de données d'autres clients (liste, téléphones, prix)",
        words=(
            r"\b(?:liste|listing|fichier|export|extraction|base|repertoire|annuaire|ensemble)"
            r"(?: (?:complete|entiere|integrale))?"
            rf" (?:(?:de|des|d) )?(?:(?:tous|toutes) )?(?:(?:les|vos|tes) )?{_FR_CUSTOMERS}\b",
            # La négation (« aucune information sur les autres clients ») décrit la règle de
            # confidentialité et n'est pas une demande.
            rf"{_FR_NO_NEGATION}\b(?:numeros?|telephones?|tel|coordonnees|contacts?|adresses?|e ?mails?|noms|prix|"
            r"tarifs|commandes|donnees|informations?|infos?|chiffres|factures)\b(?: de (?:telephone|tel))?"
            r"(?: (?!(?:ni|et|ou|mais|pas|aucun|aucune|jamais)\b)\w+){0,4} "
            rf"(?:(?:de|des|d|aux) )?(?:(?:tous|toutes) )?(?:(?:les|vos|tes) )?autres {_FR_CUSTOMERS}\b",
            r"\b(?:numeros?|telephones?|coordonnees|adresses?|e ?mails?|noms|prix|tarifs|commandes|donnees)\b"
            rf"(?: de (?:telephone|tel))? (?:de|des|d) (?:tous|toutes) (?:(?:les|vos|tes) )?{_FR_CUSTOMERS}\b",
            # « les numéros WhatsApp des clients », « les coordonnées des clients »
            r"\b(?:telephones?|numeros?|coordonnees|contacts?|adresses?|e ?mails?)\b(?: \w+){0,2} (?:de|des|d) "
            rf"{_FR_CUSTOMERS}\b",
            r"\b(?:list|show|give|send|export|dump|tell|share|print|display|provide)(?: (?:me|us))?(?: (?:a|the))?"
            r"(?: (?:full|complete))?(?: list of)? (?:all|every|each) (?:of )?(?:(?:your|the) )?"
            rf"{_EN_CUSTOMERS}\b",
            r"\b(?:phone numbers?|telephone numbers?|numbers|contacts?|contact details|e ?mails?|addresses|names|"
            r"prices|pricing|rates|orders|data|details|information|info)\b(?: \w+){0,6} (?:all|other|every) "
            rf"(?:of )?(?:(?:your|the|their) )?{_EN_CUSTOMERS}\b",
            rf"\b{_EN_CUSTOMERS} (?:in|from|of) (?:your|the) (?:database|db|system|crm|records)\b",
            r"\bcustomer (?:list|database|data|records|details|phone numbers|contacts)\b",
            r"(?<!\w)(?:ارقام|ارقام هواتف|هواتف|نمر|النمر|النمرات|نمرات|عناوين|اسماء|اسعار|بيانات|معلومات|طلبات|"
            rf"ايميلات)(?: \S+){{0,3}} (?:جميع|كل|كافه|باقي|بقيه|كاع) {_AR_CUSTOMERS}",
            rf"(?<!\w){_AR_CUSTOMERS} (?:ال)?(?:اخرين|لوخرين|الاخرين|لخرين|اخرون)\b",
            r"(?<!\w)(?:ارقام|هواتف|نمر|النمرات|النمر|نمرات|عناوين|اسماء|اسعار|بيانات|معلومات)(?: (?:ديال|تاع))? "
            rf"{_AR_CUSTOMERS}\b",
            r"(?<!\w)(?:قائمه|لائحه|ليسته|اللائحه|القائمه|الليسته)(?: (?:ديال|تاع))? (?:(?:جميع|كل|كاع) )?"
            rf"{_AR_CUSTOMERS}\b",
            rf"\b{_DL_CUSTOMERS} {_DL_OTHERS}\b",
            r"\b(?:numeros?|nmra|nmrat|nemra|nemrat|nmrouat|telephones?|tilifonat|tel|adresses?|smiyat|asami|prix|"
            rf"commandes)\b(?: \w+){{0,2}} dyal (?:(?:ga3|kamlin) )?{_DL_CUSTOMERS}\b",
            rf"\b(?:(?:la|l) )?(?:liste|lista|listat)(?: kamla)? dyal (?:(?:ga3|kamlin|kolchi) )?{_DL_CUSTOMERS}\b",
        ),
    ),
    _signal(
        "other_customers_mention", 30,
        "Mention des autres clients",
        words=(
            rf"\b(?:les|vos|tes|aux|des|d|leurs) autres {_FR_CUSTOMERS}\b",
            rf"\b(?:les |des )?{_FR_CUSTOMERS} qui ont (?:commande|achete|passe commande|passe une commande)\b",
            rf"\bother {_EN_CUSTOMERS}\b",
            rf"\b{_EN_CUSTOMERS} who (?:ordered|bought|purchased)\b",
            rf"(?<!\w)(?:باقي|بقيه) {_AR_CUSTOMERS}\b",
            rf"(?<!\w){_AR_CUSTOMERS} (?:الذين|اللي|لي) (?:طلبوا|طلبو|شراو|اشتروا)\b",
        ),
    ),
    _signal(
        "all_customers_mention", 25,
        "Mention de l'ensemble des clients",
        words=(
            rf"\b(?:tous|toutes) (?:les|vos|tes) {_FR_CUSTOMERS}\b",
            rf"\ball (?:(?:your|the) )?{_EN_CUSTOMERS}\b",
            rf"(?<!\w)(?:جميع|كل|كافه|كاع) {_AR_CUSTOMERS}\b",
            rf"\bga3 {_DL_CUSTOMERS}\b",
        ),
    ),
    _signal(
        "database_mention", 15,
        "Mention de la base de données",
        words=(r"\b(?:base de donnees|bdd|database|db|sqlite)\b", r"(?<!\w)قاعده (?:ال)?بيانات\b"),
    ),
    _signal("sql_statement", 50, "Requête SQL", lowered=SQL_PATTERNS),
    _signal(
        "environment_variable", 40,
        "Nom de variable d'environnement ou de clé de configuration",
        lowered=(
            r"\b(?:openai_api_key|openai_[a-z_]*key|twilio_[a-z_]+|gmail_[a-z_]+|smtp_[a-z_]*pass[a-z_]*|secret_key|"
            r"admin_password(?:_hash)?|admin_username|flask_secret_key|database_path|api_key|auth_token|"
            r"access_token)\b",
            r"(?<![\w.])\.env\b",
        ),
    ),
    _signal(
        "secret_mention", 30,
        "Mention de clés d'API, de mots de passe ou d'identifiants",
        words=(
            r"\b(?:api key|api keys|apikey|cles? api|cles? d api|clef api|clef d api|access token|auth token|"
            r"bearer token|"
            r"jeton d acces|jeton api|jeton d authentification|secret key|cle secrete|cle privee|private key|"
            r"mot de passe (?:admin|administrateur|du serveur|de la base|du systeme|de l application|root)|"
            r"admin password|root password|password hash|hash du mot de passe|credentials|identifiants (?:de connexion|"
            r"admin|administrateur|du serveur|de la base)|variables? d environnement|environment variables?|env vars?|"
            r"fichier env|dot env|token twilio|twilio token|cle openai|openai key|token openai)\b",
            r"(?<!\w)(?:مفتاح (?:ال)?api|كلمه (?:السر|المرور) (?:ديال |الخاصه ب)?(?:ال)?(?:ادمين|مسؤول|نظام|سيرفر))",
        ),
    ),
    _signal(
        "forced_validation", 30,
        "Consigne imposant la validation ou le statut d'une commande",
        words=(
            r"\b(?:marqu(?:e|ez|er)|mets|mettez|mettre|pass(?:e|ez|er)|chang(?:e|ez|er)|consider(?:e|ez|er)|"
            r"enregistr(?:e|ez|er)|indiqu(?:e|ez|er))(?: (?:cette|la|ma|les|mes|toutes les|toutes mes|ces|nos|notre))?"
            r" commandes? (?:(?:comme|en|au statut|a l etat|avec le statut) )?(?:validees?|confirmees?|payees?|"
            r"approuvees?|livrees?|reglees?|gratuites?)\b",
            r"\b(?:valid(?:e|ez|er)|confirm(?:e|ez|er)|approuv(?:e|ez|er)|accept(?:e|ez|er))"
            r"(?: (?:automatiquement|directement|sans verification|sans controle|sans validation))? "
            r"(?:toutes|tous) (?:les|mes|nos|ces) commandes\b",
            r"\bmark (?:(?:this|the|my|all|these|every) )?(?:orders?|commands?|requests?) as (?:validated|approved|"
            r"confirmed|paid|delivered|free)\b",
            r"\b(?:approve|validate|confirm|accept) (?:all|every) (?:(?:my|the|pending) )?orders?\b",
            r"\bskip (?:the )?(?:(?:human|manual) )?(?:validation|verification|review|approval)\b",
            r"(?<!\w)(?:اعتبر|سجل|ضع) (?:(?:هذا|هاد) )?(?:الطلب|الطلبيه|الكوموند) (?:ك|بحال )?(?:موكد|مقبول|مدفوع|"
            r"مصادق عليه)",
        ),
    ),
    _signal(
        "zero_price", 25,
        "Consigne imposant un prix nul",
        lowered=(
            r"(?<!\w)(?:prix|tarif|montant|cout|price|cost|amount|سعر|ثمن|بسعر|بثمن|taman|tamane|thaman)"
            r"(?:\s+(?:unitaire|total|unit))?\s*(?:de|a|=|:|egal a|of|to|at|ب|ديال)?\s*(?:0|zero|صفر)"
            r"(?:[.,]0+)?(?:\s*(?:mad|dh|dhs|dirhams?|eur|euros?|usd|درهم))?(?![.,]?\d)",
        ),
    ),
    _signal(
        "decode_and_apply", 25,
        "Demande de décoder un contenu puis d'appliquer ce qu'il contient",
        words=(
            r"\b(?:decod(?:e|es|ez|er)|dechiffr(?:e|es|ez|er)|decrypt(?:e|es|ez|er)|tradui(?:s|sez|re)|convert(?:is|"
            r"issez|ir))\b"
            r"(?: \w+){0,12} (?:(?:puis|et|ensuite) )?(?:appliqu(?:e|ez|er)|execut(?:e|ez|er)|sui(?:s|vez|vre)|"
            r"obei(?:s|ssez|r)|fai(?:s|tes) ce qu|realis(?:e|ez|er)|lanc(?:e|ez|er))\b",
            r"\b(?:decode|decrypt|deobfuscate|translate|convert|unpack)\b(?: \w+){0,12} (?:(?:then|and) )?(?:apply|"
            r"execute|follow|obey|run|perform|do what|carry out|act on)\b",
            r"(?<!\w)(?:فك|فكك|ترجم|حول)\b(?: \S+){0,10} (?:ثم |و)?(?:طبق|نفذ|اتبع|اعمل ب)",
        ),
    ),
    _signal(
        "encoding_mention", 15,
        "Mention d'un encodage (base64, ROT13…)",
        words=(r"\b(?:base ?64|b64|rot ?13|base ?32|uuencode|encoded in|encode en base|en hexadecimal|in hex)\b",),
    ),
)
"""Signaux cherchés par motifs, dans l'ordre d'affichage des raisons."""

ENCODED_PAYLOAD = Signal(
    "encoded_payload", 30,
    "Contenu encodé (base64, hexadécimal, encodage d'URL) qui se décode en texte lisible",
)
HIDDEN_UNICODE_TAGS = Signal("hidden_unicode_tags", 40, "Texte invisible écrit en caractères d'étiquette Unicode")
MIXED_SCRIPTS = Signal(
    "mixed_scripts", 20,
    "Mot qui mélange l'alphabet latin et un autre alphabet (homoglyphes : lettres d'apparence identique)",
)
INVISIBLE_CHARACTERS = Signal("invisible_characters", 15, "Caractères invisibles insérés à l'intérieur de mots")
SPACED_LETTERS = Signal("spaced_letters", 10, "Mots écrits lettre par lettre (lettres séparées une à une)")
