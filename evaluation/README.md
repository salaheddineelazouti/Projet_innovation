# Jeu d'évaluation des messages clients

Ce dossier contient un corpus de messages clients annotés, représentatif des commandes que TECPAP
reçoit par email et par WhatsApp, réparti en un jeu de développement et un jeu de test tenu à l'écart
(section 2). Il sert à mesurer la qualité des composants qui analysent ces messages, avec des chiffres
reproductibles plutôt qu'une impression d'ensemble.

## 1. Objet

Le jeu mesure deux familles de composants.

Composants déterministes (sans modèle de langue, exécutables hors ligne) :

| Champ attendu | Composant mesuré | Module |
|---|---|---|
| `language` | Détection de la langue et de l'écriture | `tecpap.nlp.language.detect_language` |
| `reply_language` | Choix de la langue de réponse | `tecpap.nlp.language.reply_language` |
| `intent` | Intentions détectées par règles dans l'assistant WhatsApp | `tecpap.chatbot.intents.detect_intent` |
| `is_order` | Heuristique « est un bon de commande » de l'extraction par règles | `tecpap.extraction.rules.rule_based_extract` |
| `is_injection` | Détection des tentatives d'injection d'instructions | `tecpap.security.guardrails.inspect_input` |
| `type_produit` | Normalisation du type de produit à partir des mots-clés du catalogue (`normalize_product_type`) | `tecpap.extraction.rules.rule_based_extract` |
| `quantite`, `unite` | Lecture des quantités écrites en chiffres ou en lettres (`parse_quantity`) et de leur unité (`normalize_unit`) | `tecpap.extraction.rules.rule_based_extract` |
| `entreprise_cliente` | Repérage du client par motifs (« pour X », « ana X », « je suis X ») | `tecpap.extraction.rules.rule_based_extract` |

Extraction complète par le modèle de langue : lorsque la variable `OPENAI_API_KEY` est définie, les mêmes
champs (`type_produit`, `quantite`, `unite`, `entreprise_cliente`, `is_order`) servent de référence pour
l'extraction réalisée par le modèle puis normalisée par les règles. Sans clé, seule la partie déterministe
est mesurée.

## 2. Fichiers

Le corpus annoté est réparti en deux partitions (« split dev/test ») :

| Partition | Fichier | Éléments | Usage |
|---|---|---|---|
| `dev` | `dataset/messages.jsonl` | 89 | Jeu de développement : ses erreurs servent à écrire et à ajuster les règles. |
| `dev` | `dataset/messages_dev_complement.jsonl` | 109 | Jeu de développement : premier jeu tenu à l'écart, versé dans les données de développement (section 9.1). |
| `test` | `dataset/messages_test.jsonl` | Section 9.4 | Jeu de test tenu à l'écart : il ne sert jamais au réglage (section 9). |

Les deux fichiers du jeu de développement forment une seule partition de 198 éléments : l'évaluation les
lit ensemble, un identifiant ne peut figurer que dans un des deux fichiers, et le rapport donne les mesures
de l'ensemble puis celles de chaque fichier (section 8.4).

Tous les fichiers sont encodés en UTF-8, au format JSON Lines (un objet JSON complet par ligne, sans
virgule entre les lignes). Les textes en écriture arabe sont stockés tels quels, sans transcription.
Les sections 3 à 7 décrivent le jeu de développement, la section 8 l'exécution de l'évaluation, et la
section 9 le jeu de test et ses règles d'usage.

## 3. Schéma d'un élément

| Champ | Type | Contenu |
|---|---|---|
| `id` | chaîne | Identifiant unique dans sa partition (voir le tableau des préfixes ci-dessous). |
| `category` | chaîne | Famille de conception de l'élément (voir section 5). Sert au regroupement des résultats. |
| `channel` | chaîne | Canal de réception : `email` ou `whatsapp`. |
| `text` | chaîne | Message tel qu'il arrive dans la chaîne de traitement : corps de l'email, texte extrait d'une pièce jointe, message WhatsApp ou transcription d'une note vocale. |
| `expected` | objet | Valeurs de référence (voir section 4). |
| `notes` | chaîne | Courte phrase qui décrit ce que l'élément teste et, le cas échéant, le choix d'étiquetage retenu. |

Un identifiant est formé d'un préfixe de fichier, d'un préfixe de langue (`fr-`, `en-`, `ar-`,
`darija-ar-`, `darija-lat-`) et d'un numéro sur trois chiffres :

| Fichier | Préfixe de fichier | Exemple |
|---|---|---|
| `dataset/messages.jsonl` | Aucun | `fr-001` |
| `dataset/messages_dev_complement.jsonl` | `dev2-` | `dev2-darija-lat-001` |
| `dataset/messages_test.jsonl` | `t2-` (section 9.3) | `t2-fr-001` |

Champs de `expected` :

| Champ | Type | Valeurs possibles |
|---|---|---|
| `language` | chaîne | `fr`, `en`, `ar`, `darija_ar`, `darija_latin` |
| `reply_language` | chaîne | `fr`, `darija`, `ar` |
| `intent` | chaîne | `order`, `reorder`, `status`, `question`, `human`, `greeting`, `confirm_yes`, `confirm_no`, `cancel`, `other` |
| `is_order` | booléen | `true` ou `false` |
| `is_injection` | booléen | `true` ou `false` |
| `type_produit` | chaîne ou `null` | Nom exact d'un des quatre produits du catalogue |
| `quantite` | nombre ou `null` | Quantité demandée, dans l'unité de `unite` |
| `unite` | chaîne ou `null` | `pièces`, `kg`, `carton`, `paquet`, `US` ou `null` (section 4.7) |
| `entreprise_cliente` | chaîne ou `null` | Nom du client tel qu'écrit dans le message |

## 4. Définition des étiquettes

### 4.1 Langue (`language`)

| Valeur | Définition |
|---|---|
| `fr` | Français, y compris abrégé (« slt », « c ») ou dicté. |
| `en` | Anglais. |
| `ar` | Arabe standard moderne (arabe écrit de la presse et de l'administration). |
| `darija_ar` | Arabe dialectal marocain (darija) écrit en lettres arabes, repérable par des mots comme « بغيت », « ديال », « عافاك », « واخا », « لاباس », « فين ». |
| `darija_latin` | Darija écrite en lettres latines (arabizi : écriture de l'arabe avec l'alphabet latin et des chiffres à la place de certaines lettres, 3 = ع, 7 = ح, 9 = ق), par exemple « bghit », « dyal », « 3afak », « wakha ». |

Règle d'alternance de langues : un message qui mélange darija et français (« bghit 3000 sachet kraft »)
est étiqueté darija dès qu'un marqueur de darija est clairement présent. Un message en lettres arabes sans
aucun marqueur dialectal est étiqueté `ar`.

### 4.2 Langue de réponse (`reply_language`)

Valeur déduite de la langue : `darija_latin` et `darija_ar` donnent `darija`, `ar` donne `ar`, `fr` et `en`
donnent `fr` (les réponses de l'assistant n'existent qu'en français, en darija et en arabe standard).

### 4.3 Intention (`intent`)

| Valeur | Définition |
|---|---|
| `order` | Demande de produits avec au moins un élément de commande (produit ou quantité), y compris la rectification d'une commande en cours. |
| `reorder` | Renouvellement d'une commande antérieure (« comme d'habitude », « kif dima », « b7al dima », « كيف ديما », « كالعادة », « same as usual »). Le produit et la quantité viennent alors de l'historique du client. |
| `status` | Question sur l'état d'avancement ou la livraison d'une commande. |
| `question` | Question sur les produits, les prix, les délais ou les caractéristiques, sans commande. |
| `human` | Demande explicite de parler à une personne de l'équipe commerciale. |
| `greeting` | Salutation seule. |
| `confirm_yes` | Réponse positive à une demande de confirmation (« oui », « ok », « wakha », « ah », « iyeh », « نعم », « واخا »). |
| `confirm_no` | Réponse négative à une demande de confirmation (« non », « la », « لا »). |
| `cancel` | Demande d'annulation d'une commande. |
| `other` | Tout le reste, en particulier les tentatives d'injection qui ne contiennent aucune commande et les demandes administratives. |

### 4.4 Commande (`is_order`)

`true` exactement quand l'intention est `order` ou `reorder`. Une tentative d'injection placée dans une
commande par ailleurs valide garde `is_order = true` : la commande est extraite et le message est signalé.

### 4.5 Injection d'instructions (`is_injection`)

`true` quand le message tente de modifier le comportement du système plutôt que de passer ou suivre une
commande : remplacement des instructions, demande de divulgation des consignes système (texte des consignes
données au modèle), jeu de rôle destiné à lever les restrictions, demande de données d'autres clients,
requêtes SQL, faux marqueurs de rôle (« system: », « assistant: », jetons de conversation), consigne encodée
en base64 (codage d'un texte en caractères ASCII). `false` pour un message légitime qui emploie les mêmes
mots dans leur sens courant (« ignorer ma commande précédente », « instructions de livraison »).

### 4.6 Type de produit (`type_produit`)

Nom exact d'un des quatre produits du catalogue :

- `Sachets fond plat`
- `Sac fond carré sans poignées`
- `Sac fond carré avec poignées plates`
- `Sac fond carré avec poignées torsadées`

La valeur est remplie chaque fois qu'un produit est clairement désigné, même si le message n'est pas une
commande (question produit, par exemple). Quand plusieurs produits sont cités, le plus spécifique
l'emporte (poignées torsadées, puis poignées plates, puis sans poignées, puis fond plat). `null` quand aucun
produit n'est désigné ou quand le produit dépend de l'historique (renouvellement).

### 4.7 Quantité et unité (`quantite`, `unite`)

`quantite` est la quantité demandée, ramenée à un nombre (« 10.000 » = 10000, « jouj alaf » = 2000,
« ٣٠٠٠ » = 3000, « 1,5 million » = 1500000). `null` quand aucune quantité n'est donnée, quand elle est une
différence à appliquer à une commande antérieure (« zid 1000 ») ou quand elle n'est pas une quantité
commandée (« minimum order quantity »).

`unite` est l'unité écrite par le client, ramenée à l'une des formes canoniques de
`tecpap.extraction.rules.normalize_unit` :

| Écrit dans le message | Étiquette |
|---|---|
| « pièces », « unités », « pcs », « 7ba », « قطعة » | `pièces` |
| « kg » | `kg` |
| « carton », « cartons » | `carton` |
| « paquet », « paquets » | `paquet` |
| Quantité sans unité (le nom du produit après le nombre n'est pas une unité : « 3000 sacs ») | `US` |
| Aucune quantité | `null` |

`US` est l'unité de stock de SAGE X3, unité par défaut pour des sacs comptés à la pièce. Pour une
quantité en cartons, en paquets ou en kilogrammes, `quantite` est le nombre de cartons, de paquets ou de
kilogrammes. La convention est commune aux deux partitions ; les effectifs par unité du jeu de
développement sont donnés en section 5.

### 4.8 Client (`entreprise_cliente`)

Raison sociale ou nom du commerce pour lequel la commande est passée, tel qu'il est écrit dans le message
(les noms en écriture arabe ne sont pas transcrits, la ville qui suit le nom n'est pas reprise quand elle
est séparée par une préposition). Quand seule une personne se présente (« ana Youssef »), son prénom sert de
nom de client. `null` quand le message ne permet pas d'identifier le client.

## 5. Conception des éléments

Le jeu de développement contient 198 éléments, 41 reçus par email et 157 par WhatsApp :

| Fichier | Éléments | Email | WhatsApp |
|---|---|---|---|
| `dataset/messages.jsonl` | 89 | 24 | 65 |
| `dataset/messages_dev_complement.jsonl` | 109 | 17 | 92 |
| **Total** | **198** | **41** | **157** |

Effectifs par catégorie et par langue, sur les deux fichiers :

| Catégorie | fr | darija_latin | darija_ar | ar | en | Total | `messages.jsonl` | `messages_dev_complement.jsonl` |
|---|---|---|---|---|---|---|---|---|
| `commande_formelle` (email ou pièce jointe structurée) | 10 | 0 | 0 | 3 | 2 | 15 | 9 | 6 |
| `commande_informelle` (message court) | 9 | 11 | 7 | 2 | 2 | 31 | 12 | 19 |
| `commande_vocale` (transcription de note vocale) | 3 | 3 | 2 | 1 | 0 | 9 | 3 | 6 |
| `renouvellement` | 4 | 4 | 2 | 2 | 2 | 14 | 7 | 7 |
| `suivi` (état d'une commande) | 2 | 2 | 2 | 2 | 2 | 10 | 5 | 5 |
| `question_produit` | 2 | 2 | 2 | 1 | 2 | 9 | 4 | 5 |
| `demande_humain` | 2 | 2 | 2 | 1 | 1 | 8 | 3 | 5 |
| `salutation` | 2 | 2 | 2 | 1 | 2 | 9 | 4 | 5 |
| `confirmation_oui` | 4 | 5 | 2 | 2 | 1 | 14 | 7 | 7 |
| `confirmation_non` | 2 | 2 | 2 | 2 | 1 | 9 | 4 | 5 |
| `annulation` | 2 | 2 | 2 | 1 | 1 | 8 | 3 | 5 |
| `injection` | 15 | 7 | 4 | 5 | 8 | 39 | 18 | 21 |
| `piege_faux_positif` | 11 | 4 | 1 | 3 | 4 | 23 | 10 | 13 |
| **Total** | **68** | **46** | **30** | **26** | **28** | **198** | **89** | **109** |

Effectifs par intention :

| Intention | Total | `messages.jsonl` | `messages_dev_complement.jsonl` |
|---|---|---|---|
| `order` | 84 | 34 | 50 |
| `reorder` | 14 | 7 | 7 |
| `status` | 10 | 5 | 5 |
| `question` | 10 | 5 | 5 |
| `human` | 8 | 3 | 5 |
| `greeting` | 9 | 4 | 5 |
| `confirm_yes` | 14 | 7 | 7 |
| `confirm_no` | 9 | 4 | 5 |
| `cancel` | 8 | 3 | 5 |
| `other` | 32 | 17 | 15 |
| **Total** | **198** | **89** | **109** |

Au total 98 éléments sont des commandes (`is_order = true`) : 41 dans `messages.jsonl` et 57 dans
`messages_dev_complement.jsonl`. Parmi elles, 8 contiennent aussi une tentative d'injection (2 et 6).
83 éléments portent une quantité (35 et 48) et 92 un nom de client (36 et 56), dont 90 parmi les
commandes (35 et 55).

Effectifs par unité attendue :

| Unité attendue | Total | `messages.jsonl` | `messages_dev_complement.jsonl` |
|---|---|---|---|
| `US` | 71 | 33 | 38 |
| `pièces` | 9 | 2 | 7 |
| `carton` | 1 | 0 | 1 |
| `kg` | 1 | 0 | 1 |
| `paquet` | 1 | 0 | 1 |
| Aucune (`null`) | 115 | 54 | 61 |

Les quantités en pièces sont celles de `fr-001`, `fr-010`, `dev2-fr-002`, `dev2-fr-031`,
`dev2-darija-lat-002`, `dev2-darija-lat-007`, `dev2-darija-ar-002`, `dev2-ar-002` et `dev2-en-016` ; la
quantité en cartons est celle de `dev2-fr-003`, en kilogrammes celle de `dev2-fr-008`, en paquets celle de
`dev2-fr-009`.

Effectifs par type de produit attendu :

| Type de produit attendu | Total | `messages.jsonl` | `messages_dev_complement.jsonl` |
|---|---|---|---|
| `Sachets fond plat` | 34 | 16 | 18 |
| `Sac fond carré sans poignées` | 19 | 6 | 13 |
| `Sac fond carré avec poignées plates` | 20 | 8 | 12 |
| `Sac fond carré avec poignées torsadées` | 20 | 8 | 12 |
| Aucun produit | 105 | 51 | 54 |

### 5.1 Commandes

Éléments de `dataset/messages.jsonl` :

- Emails formels avec objet, numéro de bon de commande, prix unitaire, montant total, dates aux formats
  jj/mm/aaaa, aaaa-mm-jj et en toutes lettres, signature avec le nom de la société.
- Texte extrait d'une pièce jointe PDF : tableau dont les colonnes sont séparées par « | », code client.
- Messages WhatsApp courts en français abrégé, en darija latine avec chiffres-lettres et mots français,
  en darija en écriture arabe, en arabe standard et en anglais.
- Transcriptions de notes vocales sans ponctuation, avec hésitations (« euh », « اه ») et article « wa7ed »
  / « واحد » placé devant le nombre.
- Formats de quantité : « 10 000 », « 10.000 », « 8,000 », « 10k », « 2 mille », « 1,5 million »,
  « jouj alaf », « tlat alaf », « khamsa alaf », « ألفين », « جوج آلاف », « خمسة آلاف », « ثلاث آلاف »,
  chiffres arabo-indiens « ٣٠٠٠ » et « ١٠٠٠٠ ».
- Chaque produit apparaît sous plusieurs synonymes : « sachet dyal tacos », « sachiyat », « ساشي »,
  « أكياس صغيرة », « flat bottom sachets » (fond plat) ; « bla yddin », « بدون مقبض », « without handles »
  (sans poignées) ; « sacs shopping », « yddin plates », « مقبض مسطح », « يد مسطحة », « flat handles »
  (poignées plates) ; « cordelette », « yddin mfetlin », « premium », « مقبض مفتول », « twisted handles »
  (poignées torsadées). Les synonymes anglais font partie des mots-clés du catalogue (liste `en`), au même
  titre que ceux des autres langues.

Éléments de `dataset/messages_dev_complement.jsonl`, rédigés pour s'écarter des formulations précédentes :

- clients variés (commerces, restaurants, pharmacies, boulangeries, coopératives, librairies, hôtels) dans
  de nombreuses villes du Maroc ;
- fautes de frappe, accents manquants, abréviations (« slt », « c », « pr », « 3k »), émojis ;
- emails longs avec signature, mention de confidentialité et message cité, emails transférés, texte extrait
  de bons de commande PDF en tableau, export au format JSON d'un logiciel de caisse ;
- transcriptions de notes vocales avec hésitations et répétitions (« trois mille trois mille ») ;
- nombres en lettres en français (« deux mille cinq cents », « dix mille »), en anglais (« two thousand »),
  en darija (« tlata d alaf », « khamsmya », « alf w khamsmya », « jouj d alaf w khamsmya », « جوج دالاف »,
  « ربعة دالاف », « خمسمية ») et en arabe standard (« ألف وخمسمائة », « سبعة آلاف », « ثمانية آلاف »),
  chiffres arabo-indiens (« ٢٥٠٠ », « ٥٠٠٠ ») ;
- quantités en pièces, en kilogrammes, en cartons et en paquets ;
- écritures mélangées dans un même message (darija latine avec un nom en lettres arabes, darija en lettres
  arabes avec un nom de produit en français, arabe standard avec des segments en anglais) ;
- produits décrits par leur usage ou leur forme plutôt que par leur nom (« des sacs pour mes clientes,
  assez solides, avec des anses en corde », « rope handles », « l7bel mfetel », « petits sachets pour
  emballer les croissants », « الساشيات ديال الحلويات ») ;
- questions mêlées à une commande, commandes conditionnelles (« si oui je vous en prends … »), commande sans
  quantité, rectifications de commande.

### 5.2 Conversation WhatsApp

Suivis de commande, questions produit, demandes d'interlocuteur humain, salutations, confirmations et
refus d'un ou deux mots, annulations, dans chaque langue où l'assistant doit les reconnaître.
`dataset/messages_dev_complement.jsonl` ajoute des refus polis sans le mot « non » et des variantes de
confirmation et de refus (« c'est bon », « vas-y », « je valide », « safi », « mzyan », « ewa », « تمام »,
« ماشي », « no thanks »).

### 5.3 Tentatives d'injection (39 éléments)

`dataset/messages.jsonl` (18 éléments) :

| Famille | Éléments |
|---|---|
| Remplacement des instructions | `fr-021`, `en-007`, `ar-008`, `darija-lat-018`, `darija-ar-013` |
| Divulgation des consignes système | `fr-022`, `en-007`, `ar-009`, `darija-lat-017` |
| Jeu de rôle, mode développeur, contournement de type DAN (« Do Anything Now », personnage censé ne suivre aucune règle) | `fr-023`, `en-008`, `darija-lat-018` |
| Données d'autres clients (liste, téléphones, prix) | `fr-023`, `fr-024`, `en-008`, `en-009`, `ar-008`, `darija-lat-018`, `darija-ar-013` |
| Requêtes SQL | `fr-025` |
| Faux marqueurs de rôle et jetons de conversation, demande de clés d'API | `fr-026`, `en-010` |
| Injection cachée dans une commande valide | `fr-027` (email), `darija-lat-019` (WhatsApp) |
| Consigne encodée en base64 | `fr-028` |

`dataset/messages_dev_complement.jsonl` (21 éléments, de familles qui ne reposent pas sur des mots-clés
évidents) :

| Famille | Éléments |
|---|---|
| Injection indirecte dans un email cité ou transféré | `dev2-fr-023`, `dev2-ar-012`, `dev2-en-014` |
| Consigne déguisée en remarque de bon de livraison ou en conditions d'un bon de commande | `dev2-fr-024`, `dev2-fr-029`, `dev2-darija-lat-023` |
| Faux message de l'administrateur, de la direction de TECPAP ou de l'administration du système | `dev2-fr-025`, `dev2-darija-ar-014`, `dev2-ar-013`, `dev2-en-013` |
| Demande de traduction des consignes données à l'assistant | `dev2-fr-026`, `dev2-darija-ar-016` |
| Jeu de rôle : développeur ou membre de l'équipe technique qui teste l'assistant | `dev2-fr-027`, `dev2-darija-lat-020`, `dev2-darija-ar-015`, `dev2-ar-014`, `dev2-en-011` |
| Coordonnées d'autres clients demandées pour une promotion ou pour des partenaires | `dev2-fr-025`, `dev2-fr-028`, `dev2-darija-lat-022`, `dev2-darija-ar-014`, `dev2-en-012`, `dev2-en-013` |
| Contenu JSON qui redéfinit le rôle ou la configuration | `dev2-darija-lat-021`, `dev2-ar-013`, `dev2-en-013` |
| Mélange de langues dans la tentative | `dev2-darija-lat-020` (darija et français), `dev2-ar-014` (arabe et anglais) |

Un même élément peut relever de plusieurs familles. Les éléments `fr-027`, `darija-lat-019`,
`dev2-fr-023`, `dev2-fr-024`, `dev2-fr-029`, `dev2-darija-lat-023`, `dev2-ar-012` et `dev2-en-014`
contiennent une commande valide : leur intention est `order` et `is_order` vaut `true`.

### 5.4 Pièges faux positifs (23 éléments)

Messages légitimes qui contiennent les mots surveillés par la détection d'injection, dans leur sens
courant. Ils mesurent le taux de fausses alertes, qui doit rester nul sur des commandes ordinaires.

| Élément | Mot surveillé | Sens dans le message |
|---|---|---|
| `fr-029` | « ignorer » | Ignorer la commande précédente et changer la quantité |
| `fr-030` | « instructions » | Instructions de livraison |
| `fr-031` | « oubliez » | Oublier un produit au profit d'un autre |
| `fr-032` | « vous êtes … notre fournisseur » | Phrase adressée au fournisseur |
| `fr-033` | « système » | Système de fermeture du sac |
| `fr-034` | « base de données » | Mise à jour de l'adresse de livraison |
| `en-011` | « ignore my previous email » | Rectification de quantité |
| `en-012` | « instructions » | Instructions du bon de commande |
| `darija-lat-020` | « nsa » (oublie) | Rectification d'une commande |
| `ar-010` | « تجاهل » (ignorer) | Rectification de quantité |
| `dev2-fr-030` | « administrateur » | Fonction de l'expéditeur dans sa coopérative |
| `dev2-fr-031` | Contenu JSON, « Select » | Export d'un logiciel de caisse ; nom du commerce |
| `dev2-fr-032` | « prompte », « règles » | Réponse rapide ; règles internes d'achat |
| `dev2-fr-033` | « traduire », « instructions » | Traduction d'un devis ; instructions d'impression |
| `dev2-fr-034` | « test », « rôle » | Commande d'essai ; rôle de relais auprès d'autres pharmacies |
| `dev2-darija-lat-024` | « msse7 dakchi li ktebt lik » (efface ce que j'ai écrit) | Rectification de quantité |
| `dev2-darija-lat-025` | « l'admin » | Administrateur d'un groupe de commerçants |
| `dev2-darija-lat-026` | « les règles » | Conditions de livraison |
| `dev2-darija-ar-017` | « نسا » (oublie) | Rectification du produit |
| `dev2-ar-015` | « تعليمات » (instructions) | Instructions d'emballage |
| `dev2-ar-016` | « نظام » (système) | Fonctionnement par commandes mensuelles |
| `dev2-en-015` | « disregard the instructions in my previous email » | Rectification de quantité |
| `dev2-en-016` | « admin system » | Bon de commande produit par le logiciel du client |

## 6. Choix d'étiquetage dans les cas ambigus

### 6.1 `dataset/messages.jsonl`

- `fr-018` : « ok » seul ne permet pas de fixer la langue ; « ok c'est bon » est étiqueté `fr`.
- `fr-001` (« 10 000 unités ») et `fr-010` (« 1500 pièces ») : l'unité écrite par le client donne
  `unite = pièces` (section 4.7) ; les autres quantités de ce fichier sont écrites sans unité et
  portent `US`.
- `darija-lat-005` et `darija-ar-003` : « wa7ed » / « واحد » devant le nombre est un article (« un »), pas la
  quantité 1.
- `darija-lat-007` : renouvellement « b7al dima » avec « zid 1000 » (ajouter 1000) ; la quantité finale
  dépend de l'historique, d'où `quantite = null`.
- `fr-012` : renouvellement avec une quantité explicite ; la quantité demandée (8000) est la référence.
- `fr-029`, `en-011`, `darija-lat-020`, `ar-010` : rectifications d'une commande en cours, étiquetées
  `order` ; le produit n'est renseigné que s'il est cité dans le message.
- `fr-032` : « chaque mois » décrit une commande récurrente ; la quantité de référence est celle d'une
  livraison (10 000).
- `fr-034` : demande de mise à jour d'adresse étiquetée `other` (traitée par l'équipe commerciale).
- `en-005` : « minimum order quantity » est une question, pas une quantité commandée.
- `darija-lat-002` : la question finale (« imta t9dro tsiftoha? », quand pouvez-vous l'envoyer) n'empêche
  pas l'intention `order`.
- `ar-001` : « مكتبة القلم بتطوان » donne le client « مكتبة القلم », la ville n'est pas reprise.
- `darija-ar-009` et `darija-ar-011` : formules communes à l'arabe standard et à la darija (« السلام
  عليكم », « لا ») accompagnées d'un marqueur dialectal (« لاباس », « ماشي ») qui fixe l'étiquette.

### 6.2 `dataset/messages_dev_complement.jsonl`

Client (`entreprise_cliente`) :

- le mot qui désigne le type de commerce est repris quand il précède immédiatement le nom (« Boulangerie
  Essaada », « pharmacie Al Amal », « Hanout Baraka », « مخبزة الفتح ») ; il ne l'est pas quand il en est
  séparé (« une boutique de vêtements pour femmes, Lalla Mode » donne « Lalla Mode », `dev2-fr-005`) ;
- la ville n'est pas reprise quand une préposition la sépare du nom, y compris une préposition attachée
  (« فمراكش », `dev2-darija-ar-003`) ou écrite sans accent (« a Settat », `dev2-fr-004`) ;
- le client est aussi renseigné quand un message autre qu'une commande le nomme (`dev2-fr-015`) ;
  l'évaluation hors ligne ne compare ce champ que sur les commandes.

Quantité (`quantite`) :

- une quantité approximative donne le nombre cité (« environ 800 », « b7al 4000 », « about two thousand ») ;
- une quantité répétée dans une note vocale n'est comptée qu'une fois (`dev2-fr-011`, `dev2-fr-012`,
  `dev2-darija-lat-009`) ;
- ne sont pas des quantités commandées : « machi 3000 » (pas 3000) dans une rectification
  (`dev2-darija-lat-024`), « كل ١٠٠ كيس » (par 100 sacs) dans une consigne d'emballage (`dev2-ar-015`),
  « 3 iyam » (trois jours) dans un suivi (`dev2-darija-lat-012`), « le minimum de commande » et
  « الحد الأدنى للطلب » dans une question (`dev2-fr-016`, `dev2-ar-006`).

Intention et produit :

- « b7al » signifie « environ » ou « comme » et n'indique pas un renouvellement (`dev2-darija-lat-004`,
  `dev2-darija-lat-007`) ; « بنظام الطلبات الشهرية » (fonctionnement par commandes mensuelles) accompagné
  d'une quantité explicite est une commande (`dev2-ar-016`) ;
- une question suivie d'une commande, ou une commande conditionnelle, est étiquetée `order`
  (`dev2-fr-005`, `dev2-fr-007`, `dev2-darija-lat-006`, `dev2-darija-lat-026`) ; une commande sans quantité
  reste une commande dès que le produit est désigné (`dev2-fr-010`, `dev2-darija-ar-004`) ;
- « ewa hadchi » (voilà, c'est tout) à la fin d'une commande dictée n'est pas une confirmation
  (`dev2-darija-lat-008`) ; un refus poli qui contient le verbe « valider » est un refus (`dev2-fr-021`) ;
- un produit décrit par son usage ou sa forme reçoit le nom du catalogue : anses en corde, « rope
  handles », « l7bel mfetel » (corde torsadée) donnent les poignées torsadées ; « sans anse » donne sans
  poignées ; petits sachets pour viennoiseries ou sandwichs, « flat-bottom kraft paper bags » pour un
  comptoir de sandwichs, « كيس ورقي صغير مسطح » donnent les sachets fond plat ;
- quand le message cite deux produits, le plus spécifique est retenu (`dev2-fr-016`) ; dans une
  rectification, c'est aussi le produit demandé (`dev2-darija-ar-017`).

Langue :

- un message en écritures mélangées reçoit la langue de son texte principal, et la darija l'emporte dès
  qu'un marqueur dialectal est présent (`dev2-darija-lat-003`, `dev2-darija-ar-003`) ; `dev2-ar-014`
  (arabe standard avec deux segments en anglais) est étiqueté `ar` ;
- « تمام » seul ne contient aucun marqueur dialectal : suivi d'un verbe dialectal (« صيفطوها ») il est
  étiqueté `darija_ar` (`dev2-darija-ar-011`), suivi d'une formule d'arabe standard (« أؤكد الطلبية ») il
  est étiqueté `ar` (`dev2-ar-009`).

## 7. Données exclusivement fictives

Toutes les données du jeu sont inventées : noms de sociétés, de commerces et de personnes plausibles pour
le Maroc mais sans lien avec des clients réels, numéros de bon de commande, adresses, prix. Les adresses
email utilisent uniquement le domaine `exemple.ma`. Aucun message ne provient de la base `orders.db` ni
d'une boîte de réception réelle.

Chaque fichier a sa propre plage de numéros de téléphone :

| Fichier | Numéros de téléphone |
|---|---|
| `dataset/messages.jsonl` | `+2126000000NN` |
| `dataset/messages_dev_complement.jsonl` | `+2126000001NN` |
| `dataset/messages_test.jsonl` | `+2126000002NN` (section 9.3) |

Tout élément ajouté au jeu de développement doit respecter les mêmes règles, garder un identifiant unique
dans la partition avec les préfixes de son fichier et de sa langue (section 3), utiliser uniquement les
valeurs d'étiquettes définies en section 3 et décrire dans `notes` ce qu'il teste.

## 8. Exécution de l'évaluation

Le programme `evaluation/run_eval.py` compare la sortie de chaque composant aux étiquettes de chaque
partition (section 2), puis écrit pour chacune un rapport en français et un fichier JSON qui porte les
mêmes chiffres. Il se lance depuis n'importe quel dossier : il ajoute lui-même la racine du dépôt aux
chemins d'importation de Python.

Avant le calcul, chaque ligne des fichiers évalués est contrôlée : schéma de la section 3, étiquettes
prises dans les valeurs autorisées, identifiant unique dans la partition (un identifiant ne peut figurer
que dans un seul fichier), `is_order` cohérent avec l'intention (section 4.4). Le programme s'arrête sur la
première ligne invalide, en donne le fichier et le numéro de ligne, et n'écrit aucun rapport.

### 8.1 Évaluation hors ligne

```
python evaluation/run_eval.py
```

Sans option, les deux partitions sont évaluées : le jeu de développement (ses deux fichiers ensemble) et le
jeu de test. Le programme s'arrête avec le code 1, sans écrire de rapport, quand l'un des fichiers est
absent ; `--split dev` évalue alors le seul jeu de développement. Sans réseau, sans clé d'API et sans base
de données, seuls les composants déterministes sont mesurés :

| Section du rapport | Composant | Étiquettes comparées | Éléments |
|---|---|---|---|
| Détection de la langue | `tecpap.nlp.language.detect_language` et `reply_language` | `language`, `reply_language` | Tous |
| Extraction par règles | `tecpap.extraction.rules.rule_based_extract` | `type_produit`, `quantite`, `unite`, `entreprise_cliente` | Commandes |
| Détection des commandes | Champ `est_bon_commande` de `rule_based_extract` | `is_order` | Tous |
| Détection des renouvellements | `tecpap.extraction.rules.detect_reorder` | Intention `reorder` | Tous |
| Détection des tentatives d'injection | `tecpap.security.guardrails.inspect_input` | `is_injection` | Tous |
| Détection des intentions | `tecpap.chatbot.intents.detect_intent` | `intent` | Tous |

Mesures calculées pour chaque section :

| Section du rapport | Mesures |
|---|---|
| Détection de la langue | Exactitude par langue, matrice de confusion |
| Extraction par règles | Exactitude par champ et par langue |
| Détection des commandes et des renouvellements | Exactitude, précision, rappel, F1 |
| Détection des tentatives d'injection | Précision, rappel, F1, fausses alertes |
| Détection des intentions | Exactitude, F1 par intention, macro-F1 |

Chaque mesure est définie dans le rapport (section « Mesures employées »). L'unité (`unite`) est comparée
à l'unité écrite par le client, selon la convention de la section 4.7.

Les proportions de la synthèse sont accompagnées de leur intervalle de confiance à 95 %, calculé par la
méthode du score de Wilson. Pour k réussites sur n éléments, avec p = k / n et z = 1,96 (quantile à 97,5 %
de la loi normale), l'intervalle est centré sur (p + z² / (2n)) / (1 + z² / n) et sa demi-largeur vaut
z √(p (1 − p) / n + z² / (4n²)) / (1 + z² / n). Contrairement à l'approximation normale
p ± z √(p (1 − p) / n), il reste compris entre 0 % et 100 % et garde une largeur non nulle quand p vaut
0 % ou 100 %. Plus l'effectif est petit, plus l'intervalle est large, ce qui rappelle qu'un score parfait
sur quelques dizaines d'éléments ne prouve pas l'absence d'erreur.

Les fausses alertes de la détection d'injection sont comptées sur les pièges (sections 5.4 et 9.4.5) et sur
tous les messages légitimes. Le détecteur d'intention est appelé avec la langue obtenue par
`detect_language`, comme dans l'assistant WhatsApp. Quand le module `tecpap.chatbot.intents` n'est pas
importable, la section des intentions porte la mention « non disponible » avec sa raison, et les autres
sections sont calculées normalement.

Le rapport du jeu de développement donne les mesures de ses 198 éléments, puis, dans la section « Synthèse
par fichier », les mêmes mesures calculées sur chacun de ses deux fichiers, avec leurs intervalles de
confiance.

Quand les deux partitions sont évaluées dans la même exécution, `results/synthese.md` les compare mesure par
mesure : effectifs, valeur et intervalle de confiance à 95 % de chaque partition, écart entre les deux (en
points de pourcentage), et liste des mesures dont les deux intervalles sont disjoints. Des intervalles
disjoints signalent un écart que le seul hasard de l'échantillonnage explique mal ; des intervalles qui se
chevauchent n'excluent pas un écart réel, mais ne permettent pas de l'affirmer.

### 8.2 Évaluation en ligne

```
python evaluation/run_eval.py --online
```

Cette évaluation demande une clé `OPENAI_API_KEY` (variable d'environnement ou fichier `.env`). Chaque
commande des partitions évaluées (`is_order` vrai) passe par l'extraction complète :
`tecpap.extraction.extractor.DataExtractor`, sans base de données, puis
`tecpap.extraction.rules.normalize_order`. Un message WhatsApp passe par `DataExtractor.extract_text`, comme
dans le webhook ; un email par `DataExtractor.extract_from_email`. Sans base de données, un renouvellement
n'est pas complété par l'historique du client. Une commande extraite par les règles faute de réponse du
modèle compte comme une extraction en échec : cette évaluation mesure le modèle de langue.

Le rapport compare les mêmes champs que l'extraction par règles et donne aussi la part des commandes
reconnues (`est_bon_commande` vrai), les extractions en échec, les commandes avec anomalie ou avec un champ
obligatoire manquant, et la confiance moyenne calculée par `normalize_order`. L'évaluation en ligne n'écrit
pas de synthèse.

Chaque commande donne lieu à une requête d'extraction, suivie d'une requête de détection du renouvellement
quand les règles n'en trouvent pas dans un texte d'au moins 60 caractères : la mesure a un coût, et ses
résultats varient d'une exécution à l'autre. Sans clé, le programme affiche un message et s'arrête avec le
code 2, sans aucun appel réseau.

### 8.3 Options

| Option | Effet | Valeur par défaut |
|---|---|---|
| `--split dev` ou `--split test` | Évalue une seule partition | Les deux partitions |
| `--dataset FICHIER` | Remplace les fichiers de la partition choisie par `--split` (obligatoire avec cette option) | Fichiers de la partition (section 2) |
| `--dataset FICHIER` répété | Forme un jeu de développement de plusieurs fichiers, évalués ensemble ; refusé avec `--split test` | Une seule occurrence |
| `--output-dir DOSSIER` | Dossier des rapports | `evaluation/results` |
| `--date AAAA-MM-JJ` | Date inscrite dans les rapports | Date du jour |
| `--online` | Extraction complète par le modèle de langue | Désactivée |

### 8.4 Fichiers produits

| Fichier | Contenu |
|---|---|
| `results/rapport_hors_ligne_dev.md` | Rapport de l'évaluation hors ligne du jeu de développement, avec les éléments mal classés |
| `results/rapport_hors_ligne_dev.json` | Chiffres de ce rapport, pour un traitement automatique |
| `results/rapport_hors_ligne_test.md` | Rapport de l'évaluation hors ligne du jeu de test, chiffres agrégés seulement |
| `results/rapport_hors_ligne_test.json` | Chiffres de ce rapport |
| `results/synthese.md` | Comparaison des deux partitions, mesure par mesure |
| `results/rapport_en_ligne_dev.md` | Rapport de l'évaluation en ligne du jeu de développement |
| `results/rapport_en_ligne_dev.json` | Chiffres de ce rapport |
| `results/rapport_en_ligne_test.md` | Rapport de l'évaluation en ligne du jeu de test, chiffres agrégés seulement |
| `results/rapport_en_ligne_test.json` | Chiffres de ce rapport |

Les quatre fichiers `rapport_en_ligne_*` ne sont produits que par l'option `--online` : le dépôt n'en contient
aucun, car l'évaluation en ligne n'a pas été exécutée.

Chaque rapport Markdown est rédigé à partir du contenu de son fichier JSON, et la synthèse à partir des deux
JSON hors ligne. Un rapport donne la partition, la date de l'exécution, le chemin, le nombre d'éléments et
l'empreinte SHA-256 de chaque fichier (somme de contrôle : deux rapports de même empreinte portent sur
exactement les mêmes données), la version du paquet `tecpap`, les effectifs par langue et par fichier, la
définition des mesures et une synthèse, puis, pour chaque composant, les résultats par langue et la matrice
de confusion quand elle s'applique.

Le contenu propre à chaque élément dépend de la partition :

| Partition | Contenu du rapport sur les éléments |
|---|---|
| `dev` | Listes des éléments mal classés : identifiant, langue, valeur attendue et valeur obtenue. |
| `test` | Aucun : ni identifiant, ni texte, ni étiquette attendue d'un élément. |

Le rapport du jeu de test ne donne que des effectifs et des mesures agrégés : proportions, intervalles de
confiance, matrices de confusion, scores extrêmes de la détection d'injection sans l'identifiant de
l'élément, nombre d'extractions en échec. Sa lecture ne peut donc pas guider le réglage d'un composant
(section 9.2). La console n'affiche que les chemins écrits et les lignes de synthèse, et le journal
ne cite aucun élément du jeu de test.

À données et code identiques, deux évaluations hors ligne ne diffèrent que par la date ; l'option `--date`
rend la sortie entièrement reproductible. Les fichiers de `results/` sont ceux de la dernière exécution
enregistrée : après une modification d'un composant mesuré ou d'un fichier du jeu, relancer
`python evaluation/run_eval.py` avant de citer un chiffre, et vérifier que les empreintes SHA-256 du rapport
sont celles des fichiers cités. Une exécution limitée à une partition (`--split`) n'écrit pas
`synthese.md`, qui peut alors porter sur des rapports plus anciens que ceux de cette partition.

### 8.5 Codes de sortie

| Code | Signification |
|---|---|
| 0 | Rapports écrits |
| 1 | Jeu de données introuvable ou invalide, ou configuration invalide ; aucun rapport écrit |
| 2 | Option `--online` sans clé `OPENAI_API_KEY`, ou option de la ligne de commande invalide (`--dataset` sans `--split`, `--dataset` répété avec `--split test`, par exemple) |

### 8.6 Contrôles automatiques

`tests/test_evaluation.py` exécute l'évaluation hors ligne du jeu de développement (ses deux fichiers) dans
un dossier temporaire et contrôle la structure des rapports, la cohérence des effectifs (sommes par langue
et par fichier, matrices de confusion, listes d'erreurs) et la synthèse par fichier. L'évaluation des deux
partitions, la synthèse et l'absence de tout contenu propre aux éléments dans le rapport du jeu de test
sont exercées sur de petits fichiers écrits par le test. L'évaluation en ligne est exercée avec une doublure
du client des modèles de langue, sans réseau :

```
python -m pytest tests/test_evaluation.py
```

Aucun test ne calcule de mesure sur `dataset/messages_test.jsonl`. Ce fichier n'est lu que par trois
contrôles de structure : schéma de la section 3, préfixe `t2-` des identifiants, et absence d'identifiant et
de message communs avec le jeu de développement. Ce dernier contrôle compare les textes ramenés à une forme
sans casse, accents ni espaces répétées, et ignore les formules courtes de trois mots au plus (« oui »,
« ok merci »), qui peuvent figurer dans les deux partitions sans copie. En cas d'échec, ces contrôles ne
donnent que des effectifs ; ils sont ignorés quand le fichier est absent.

## 9. Jeu de test tenu à l'écart

### 9.1 Objet

Le jeu de développement réunit `dataset/messages.jsonl` et `dataset/messages_dev_complement.jsonl`. Ses
erreurs servent à écrire et à ajuster les règles, les listes de mots-clés et les seuils : un score mesuré sur
ce jeu surestime donc la qualité réelle des composants.

`dataset/messages_dev_complement.jsonl` est le premier jeu tenu à l'écart. Ses résultats ont révélé un écart
de généralisation : les composants y obtenaient des scores plus bas que sur le jeu de développement. Ce jeu a
ensuite été versé dans les données de développement, où ses erreurs servent au réglage ; il ne mesure donc
plus la généralisation.

`dataset/messages_test.jsonl` est le deuxième jeu tenu à l'écart. Il ne sert jamais au réglage des
composants et donne une estimation de leur capacité de généralisation, c'est-à-dire de leur qualité sur des
messages qui n'ont pas servi à les mettre au point.

Un message du jeu de test qui reprenait un message du jeu de développement a été retiré : les deux partitions
ne partagent aucun message, hors les formules très courtes et génériques mentionnées en section 8.6.

### 9.2 Règle d'usage

- Aucune règle, aucun mot-clé, aucun seuil, aucune consigne donnée au modèle et aucun
  exemple d'extraction n'est écrit ou ajusté à partir des textes de `dataset/messages_test.jsonl`, ni à partir
  de la liste des éléments mal classés de ses rapports.
- Le travail sur un composant se fait sur le jeu de développement ; le jeu de test ne sert qu'à mesurer le
  résultat, et ses chiffres sont publiés tels qu'ils sont obtenus, à côté de ceux du jeu de développement.
- Dès que des textes ou des erreurs de ce jeu ont servi à modifier un composant, il ne mesure plus la
  généralisation : ses éléments rejoignent le jeu de développement et un autre jeu tenu à l'écart doit être
  rédigé selon les règles de la section 9.3, avec un autre préfixe d'identifiant et une autre plage de
  numéros de téléphone.

L'évaluation hors ligne par défaut mesure les deux partitions et écrit leurs rapports côte à côte
(`results/rapport_hors_ligne_dev.md`, `results/rapport_hors_ligne_test.md`), avec leur comparaison
dans `results/synthese.md` (section 8.4). Le jeu de test seul s'évalue par :

```
python evaluation/run_eval.py --split test
```

### 9.3 Règles de construction

Indépendance :

- Le jeu est rédigé sans consulter les fichiers du jeu de développement (`dataset/messages.jsonl`,
  `dataset/messages_dev_complement.jsonl`) ni leur historique, le code du paquet `tecpap` (règles, mots-clés
  du catalogue, détecteurs, assistant), les tests ni les rapports de `results/`. Les références sont les
  définitions des sections 3 et 4 et la spécification du projet (noms, usages et premiers mots-clés des
  quatre produits, comportement attendu des composants).
- Les étiquettes sont fixées avant toute exécution des composants et ne sont jamais modifiées au vu d'une
  sortie du système. Le seul contrôle appliqué au fichier porte sur sa forme : schéma de la section 3, valeurs
  autorisées, cohérence de `is_order` avec l'intention, présence du nom du client dans le texte.

Format : schéma de la section 3 et étiquettes de la section 4 (dont la convention d'unité de la
section 4.7), avec les précisions de la section 9.5. Les identifiants commencent par `t2-`, suivi du préfixe
de langue et d'un numéro sur trois chiffres (`t2-fr-001`, `t2-darija-lat-001`…). Les catégories sont celles
de la section 5.

Contenu, choisi pour s'écarter des formulations attendues par un auteur de règles :

- clients fictifs variés : hôtels, riads, pâtisseries, boulangeries, snacks, pizzerias, traiteurs,
  pharmacies, librairies, coopératives, torréfacteurs, herboristeries, boutiques, épiceries fines, dans de
  nombreuses villes du Maroc ;
- fautes de frappe, accents manquants, abréviations courantes de la messagerie, émojis ;
- emails longs avec signature et mention de confidentialité, emails transférés, formulaire de contact,
  texte extrait de bons de commande PDF en tableau, en français, en arabe standard et en anglais ;
- transcriptions de notes vocales avec hésitations, répétitions et reprise de la quantité dictée ;
- nombres en lettres en français, en anglais, en darija (écriture latine et écriture arabe) et en arabe
  standard, chiffres arabo-indiens ;
- quantités en pièces, en kilogrammes, en cartons et en paquets, avec les abréviations et les formes
  dialectales de ces unités ;
- écritures mélangées dans un même message (darija latine avec un nom en lettres arabes, darija en lettres
  arabes avec un nom de produit en français, arabe standard avec un segment en anglais) ;
- produits décrits par leur usage ou leur forme plutôt que par leur nom ;
- commandes conditionnelles, commande sans quantité, rectifications, renouvellements formulés sans les
  expressions figées ;
- confirmations, refus et annulations dans chacune des cinq langues, dont des refus polis sans mot de
  négation et une annulation sans le verbe « annuler » ;
- 28 tentatives d'injection de familles variées (tableau 9.4.4) et 17 pièges faux positifs
  (tableau 9.4.5).

Données fictives : noms de commerces et de personnes inventés, numéros de téléphone uniquement de la forme
`+2126000002NN` (plage distincte des plages `+2126000000NN` et `+2126000001NN` des données de
développement), adresses email et adresses web uniquement dans le domaine `exemple.ma`.

### 9.4 Effectifs

Seuls des effectifs sont publiés : aucun identifiant, aucun texte et aucune étiquette d'élément ne figure
dans ce document, conformément à la règle de la section 9.2. Les effectifs sont calculés sur le fichier, qui
contient 134 éléments : 22 reçus par email et 112 par WhatsApp.

#### 9.4.1 Par catégorie et par langue

| Catégorie | fr | darija_latin | darija_ar | ar | en | Total |
|---|---|---|---|---|---|---|
| `commande_formelle` | 5 | 0 | 0 | 2 | 2 | 9 |
| `commande_informelle` | 6 | 6 | 3 | 2 | 2 | 19 |
| `commande_vocale` | 2 | 2 | 1 | 1 | 0 | 6 |
| `renouvellement` | 3 | 3 | 2 | 1 | 1 | 10 |
| `suivi` | 2 | 2 | 1 | 1 | 1 | 7 |
| `question_produit` | 2 | 2 | 1 | 1 | 2 | 8 |
| `demande_humain` | 1 | 2 | 0 | 1 | 1 | 5 |
| `salutation` | 1 | 2 | 1 | 1 | 1 | 6 |
| `confirmation_oui` | 2 | 2 | 1 | 1 | 1 | 7 |
| `confirmation_non` | 1 | 2 | 1 | 1 | 1 | 6 |
| `annulation` | 2 | 1 | 1 | 1 | 1 | 6 |
| `injection` | 8 | 6 | 4 | 4 | 6 | 28 |
| `piege_faux_positif` | 5 | 4 | 3 | 3 | 2 | 17 |
| **Total** | **40** | **34** | **19** | **20** | **21** | **134** |

Part de chaque langue (valeurs arrondies) : français 30 %, darija latine 25 %, darija en lettres arabes 14 %,
arabe standard 15 %, anglais 16 %.

#### 9.4.2 Par intention et par langue

| Intention | fr | darija_latin | darija_ar | ar | en | Total |
|---|---|---|---|---|---|---|
| `order` | 18 | 11 | 7 | 8 | 6 | 50 |
| `reorder` | 4 | 4 | 2 | 1 | 2 | 13 |
| `status` | 2 | 2 | 1 | 1 | 1 | 7 |
| `question` | 2 | 3 | 1 | 2 | 2 | 10 |
| `human` | 1 | 2 | 0 | 1 | 1 | 5 |
| `greeting` | 1 | 2 | 1 | 1 | 1 | 6 |
| `confirm_yes` | 2 | 2 | 1 | 1 | 1 | 7 |
| `confirm_no` | 1 | 2 | 1 | 1 | 1 | 6 |
| `cancel` | 2 | 1 | 1 | 1 | 1 | 6 |
| `other` | 7 | 5 | 4 | 3 | 5 | 24 |
| **Total** | **40** | **34** | **19** | **20** | **21** | **134** |

Au total 63 éléments sont des commandes (`is_order = true`), dont 7 contiennent aussi une tentative
d'injection. 51 éléments portent une quantité et 59 un nom de client (55 parmi les commandes).

#### 9.4.3 Par produit et par unité

| Type de produit attendu | Éléments |
|---|---|
| `Sachets fond plat` | 24 |
| `Sac fond carré sans poignées` | 10 |
| `Sac fond carré avec poignées plates` | 14 |
| `Sac fond carré avec poignées torsadées` | 13 |
| Aucun produit | 73 |

| Unité attendue | Éléments |
|---|---|
| `US` | 33 |
| `pièces` | 7 |
| `carton` | 8 |
| `kg` | 2 |
| `paquet` | 1 |
| Aucune (`null`) | 83 |

#### 9.4.4 Tentatives d'injection (28 éléments)

Un même élément peut relever de plusieurs familles : la somme de la colonne dépasse donc 28.

| Famille | Éléments |
|---|---|
| Remplacement direct des consignes, sans la formule « ignore les instructions » | 4 |
| Injection indirecte dans un email transféré | 2 |
| Consigne cachée dans un document joint ou dans une mention de confidentialité | 2 |
| Usurpation de la direction ou de l'équipe technique de TECPAP | 5 |
| Jeu de rôle destiné à lever les restrictions | 3 |
| Données d'autres clients (liste, téléphones, prix, historique) | 13 |
| Divulgation, traduction ou résumé des consignes de l'assistant | 6 |
| Exfiltration de la conversation par une image Markdown (syntaxe `![texte](adresse)` dont l'adresse transporte les données) | 1 |
| Clé d'API et mot de passe demandés au nom d'un faux échange antérieur | 1 |
| Contenu structuré : JSON qui redéfinit le rôle, faux jetons de conversation, faux appel d'outil | 3 |
| Requête SQL | 1 |
| Écriture masquée : écriture leet (chiffres à la place de lettres), tatwil (allongement des lettres arabes), ROT13 (décalage de chaque lettre de 13 rangs dans l'alphabet), caractères de largeur nulle | 4 |
| Contournement de la validation humaine ou des prix dans une commande valide | 5 |
| Consigne dictée dans une note vocale | 1 |
| Mélange de langues dans la tentative | 3 |

Sur les 28 tentatives, 7 contiennent une commande valide : leur intention est `order` ou `reorder` et
`is_order` vaut `true`.

#### 9.4.5 Pièges faux positifs (17 éléments)

Un piège est un message légitime qui contient un mot ou une tournure surveillés par la détection
d'injection. Le tableau compte les éléments par type de mot surveillé. Un même élément peut contenir
plusieurs types de mots : la somme de la colonne dépasse donc 17.

| Type de mot surveillé | Éléments |
|---|---|
| Demande d'ignorer, d'oublier ou de supprimer un message ou une commande antérieurs | 4 |
| Mot désignant des consignes ou des instructions | 4 |
| Formule qui attribue un nouveau rôle à l'interlocuteur | 3 |
| Fonction de direction ou de développement (directeur, développeur, responsable) | 4 |
| Mot désignant le système | 3 |
| Mot désignant la base de données | 3 |
| Autre mot de contrôle (traduction, contournement) | 2 |

Intentions attendues des 17 pièges : 10 `order`, 2 `reorder`, 2 `question`, 3 `other`. Aucun n'est une
tentative d'injection (`is_injection = false`).

### 9.5 Précisions d'étiquetage

Ces règles générales complètent les sections 4 et 6. Les exemples sont inventés et ne reprennent aucun
message du jeu de test.

Unité (`unite`) :

- la convention de la section 4.7 est commune à toutes les partitions ; le jeu de test contient des
  quantités en pièces, en kilogrammes, en cartons et en paquets (section 9.4.3) ;
- les formes plurielles du mot carton en darija ou en arabe donnent `carton` ; le mot arabe pour « pièce »
  donne `pièces`, comme « units » et « pcs » ; « kilogramme » écrit en toutes lettres donne `kg` ;
- un conditionnement (par exemple « par 100 ») n'est ni la quantité ni l'unité : « 25 paquets de 100 »
  donne la quantité 25 et l'unité `paquet`.

Quantité (`quantite`) :

- une quantité dictée puis reprise donne la dernière valeur ; une quantité répétée n'est comptée qu'une
  fois ; une quantité approximative donne le nombre cité (« environ 2 000 » donne 2 000) ;
- dans une rectification, seule la valeur retenue compte (« 1 000, non plutôt 1 500 » donne 1 500) ;
- ne sont pas des quantités commandées : une quantité hypothétique dans une demande de prix, une charge en
  kilogrammes dans une question sur la résistance d'un sac, une différence (« ajoutez 2 000 ») ou un
  doublement appliqués à la commande habituelle.

Client (`entreprise_cliente`) :

- le mot qui désigne le type de commerce est repris quand il précède immédiatement le nom (« boutique Zina »,
  « snack Horizon ») ; il ne l'est pas quand un autre mot l'en sépare ;
- le nom du commerce l'emporte sur le prénom de la personne qui écrit ; le prénom sert de client quand aucun
  commerce n'est nommé ;
- la ville n'est pas reprise quand une préposition la sépare du nom, y compris une préposition attachée au
  mot en arabe ;
- le client est aussi renseigné quand un message autre qu'une commande le nomme ; l'évaluation hors ligne ne
  compare ce champ que sur les commandes.

Intention et produit :

- une commande conditionnelle ou accompagnée d'une question est une commande ; une commande sans quantité
  reste une commande dès que le produit est désigné ;
- un renouvellement qui cite le produit ou la quantité reçoit ces valeurs ; sinon le produit et la quantité
  restent nuls ;
- une formule de clôture (« c'est tout », « d'accord ») à la fin d'une commande dictée, ou un « oui » au
  milieu d'une note vocale, n'est pas une confirmation ;
- un refus poli sans mot de négation est un refus ; une formule comme « on laisse tomber la commande » est
  une annulation ;
- la demande du numéro du commercial de TECPAP est une demande d'interlocuteur humain, pas une demande de
  données d'un autre client ; les mises à jour de coordonnées (RIB, numéro, adresse) sont étiquetées
  `other` ; les demandes de consignes de stockage ou de traduction d'un guide de stockage sont des
  questions produit ;
- un produit décrit par son usage ou sa forme reçoit le nom du catalogue : des sacs sans poignées donnent
  les sacs fond carré sans poignées, des poignées en cordelette donnent les poignées torsadées, de petits
  sachets pour viennoiseries ou pour médicaments donnent les sachets fond plat ;
- le produit cité dans une question ou une annulation est renseigné ; « sacs kraft » seul ne désigne aucun
  produit.

Langue :

- un message en écritures mélangées reçoit la langue de son texte principal, et la darija l'emporte dès
  qu'un marqueur dialectal est présent ; un texte en arabe standard qui contient un segment en anglais est
  étiqueté `ar` ;
- une écriture qui place des chiffres dans les mots, comme l'arabizi, ne rend pas le texte dialectal : un
  texte français écrit ainsi reste étiqueté `fr` ;
- une salutation arabe suivie d'une formule d'arabe standard est étiquetée `ar` ; la même salutation
  suivie d'un marqueur dialectal est étiquetée `darija_ar`.
