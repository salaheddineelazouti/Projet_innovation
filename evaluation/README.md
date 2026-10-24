# Jeu d'évaluation des messages clients

Ce dossier contient un jeu de messages clients annotés, représentatif des commandes que TECPAP
reçoit par email et par WhatsApp. Il sert à mesurer la qualité des composants qui analysent ces messages,
avec des chiffres reproductibles plutôt qu'une impression d'ensemble.

## 1. Objet

Le jeu mesure deux familles de composants.

Composants déterministes (sans modèle de langage, exécutables hors ligne) :

| Champ attendu | Composant mesuré | Module |
|---|---|---|
| `language` | Détection de la langue et de l'écriture | `tecpap.nlp.language.detect_language` |
| `reply_language` | Choix de la langue de réponse | `tecpap.nlp.language.reply_language` |
| `intent` | Intentions détectées par règles dans l'assistant WhatsApp | `tecpap.chatbot.bot` |
| `is_order` | Heuristique « est un bon de commande » de l'extraction par règles | `tecpap.extraction.rules.rule_based_extract` |
| `is_injection` | Détection des tentatives d'injection d'instructions | `tecpap.security.guardrails.inspect_input` |
| `type_produit` | Normalisation du type de produit à partir des mots-clés du catalogue | `tecpap.extraction.rules.normalize_product_type` |
| `quantite`, `unite` | Lecture des quantités écrites en chiffres ou en lettres | `tecpap.extraction.rules.parse_quantity` |
| `entreprise_cliente` | Repérage du client par motifs (« pour X », « ana X », « je suis X ») | `tecpap.extraction.rules.rule_based_extract` |

Extraction complète par le modèle de langage : lorsque la variable `OPENAI_API_KEY` est définie, les mêmes
champs (`type_produit`, `quantite`, `unite`, `entreprise_cliente`, `is_order`) servent de référence pour
l'extraction réalisée par le modèle puis normalisée par les règles. Sans clé, seule la partie déterministe
est mesurée.

## 2. Fichier

`dataset/messages.jsonl` : encodage UTF-8, format JSON Lines (un objet JSON complet par ligne, sans
virgule entre les lignes). Les textes en écriture arabe sont stockés tels quels, sans transcription.

## 3. Schéma d'un élément

| Champ | Type | Contenu |
|---|---|---|
| `id` | chaîne | Identifiant unique. Préfixe de langue suivi d'un numéro sur trois chiffres : `fr-`, `en-`, `ar-`, `darija-ar-`, `darija-lat-`. |
| `category` | chaîne | Famille de conception de l'élément (voir section 5). Sert au regroupement des résultats. |
| `channel` | chaîne | Canal de réception : `email` ou `whatsapp`. |
| `text` | chaîne | Message tel qu'il arrive dans la chaîne de traitement : corps de l'email, texte extrait d'une pièce jointe, message WhatsApp ou transcription d'une note vocale. |
| `expected` | objet | Valeurs de référence (voir section 4). |
| `notes` | chaîne | Courte phrase qui décrit ce que l'élément teste et, le cas échéant, le choix d'étiquetage retenu. |

Champs de `expected` :

| Champ | Type | Valeurs possibles |
|---|---|---|
| `language` | chaîne | `fr`, `en`, `ar`, `darija_ar`, `darija_latin` |
| `reply_language` | chaîne | `fr`, `darija`, `ar` |
| `intent` | chaîne | `order`, `reorder`, `status`, `question`, `human`, `greeting`, `confirm_yes`, `confirm_no`, `cancel`, `other` |
| `is_order` | booléen | `true` ou `false` |
| `is_injection` | booléen | `true` ou `false` |
| `type_produit` | chaîne ou `null` | Nom exact d'un des quatre produits du catalogue |
| `quantite` | nombre ou `null` | Quantité demandée, en unités |
| `unite` | chaîne ou `null` | `US` ou `null` |
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
commande : remplacement des instructions, demande de divulgation du prompt système (texte des consignes
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

`quantite` est le nombre d'unités demandé, ramené à un nombre (« 10.000 » = 10000, « jouj alaf » = 2000,
« ٣٠٠٠ » = 3000, « 1,5 million » = 1500000). `null` quand aucune quantité n'est donnée, quand elle est une
différence à appliquer à une commande antérieure (« zid 1000 ») ou quand elle n'est pas une quantité
commandée (« minimum order quantity »).

`unite` vaut `US` (unité de stock dans SAGE X3, unité par défaut pour des sacs comptés à la pièce) dès
qu'une quantité est donnée, `null` sinon. Le jeu ne contient pas de quantités en cartons ou en kilogrammes.

### 4.8 Client (`entreprise_cliente`)

Raison sociale ou nom du commerce pour lequel la commande est passée, tel qu'il est écrit dans le message
(les noms en écriture arabe ne sont pas transcrits, la ville qui suit le nom n'est pas reprise quand elle
est séparée par une préposition). Quand seule une personne se présente (« ana Youssef »), son prénom sert de
nom de client. `null` quand le message ne permet pas d'identifier le client.

## 5. Conception des éléments

Le fichier contient 89 éléments : 24 reçus par email, 65 par WhatsApp. Effectifs par catégorie et par
langue :

| Catégorie | fr | darija_latin | darija_ar | ar | en | Total |
|---|---|---|---|---|---|---|
| `commande_formelle` (email ou pièce jointe structurée) | 6 | 0 | 0 | 2 | 1 | 9 |
| `commande_informelle` (message court) | 3 | 4 | 3 | 1 | 1 | 12 |
| `commande_vocale` (transcription de note vocale) | 1 | 1 | 1 | 0 | 0 | 3 |
| `renouvellement` | 2 | 2 | 1 | 1 | 1 | 7 |
| `suivi` (état d'une commande) | 1 | 1 | 1 | 1 | 1 | 5 |
| `question_produit` | 1 | 1 | 1 | 0 | 1 | 4 |
| `demande_humain` | 1 | 1 | 1 | 0 | 0 | 3 |
| `salutation` | 1 | 1 | 1 | 0 | 1 | 4 |
| `confirmation_oui` | 2 | 3 | 1 | 1 | 0 | 7 |
| `confirmation_non` | 1 | 1 | 1 | 1 | 0 | 4 |
| `annulation` | 1 | 1 | 1 | 0 | 0 | 3 |
| `injection` | 8 | 3 | 1 | 2 | 4 | 18 |
| `piege_faux_positif` | 6 | 1 | 0 | 1 | 2 | 10 |
| **Total** | **34** | **20** | **13** | **10** | **12** | **89** |

Effectifs par intention : `order` 34, `reorder` 7, `status` 5, `question` 5, `human` 3, `greeting` 4,
`confirm_yes` 7, `confirm_no` 4, `cancel` 3, `other` 17. Au total 41 éléments sont des commandes
(`is_order = true`), 35 portent une quantité et 36 un nom de client.

Effectifs par type de produit attendu : `Sachets fond plat` 16, `Sac fond carré sans poignées` 6,
`Sac fond carré avec poignées plates` 8, `Sac fond carré avec poignées torsadées` 8, aucun produit 51.

### 5.1 Commandes

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
  (poignées torsadées). Les synonymes anglais ne figurent pas dans les mots-clés du catalogue : ces
  éléments mesurent la couverture de la normalisation.

### 5.2 Conversation WhatsApp

Suivis de commande, questions produit, demandes d'interlocuteur humain, salutations, confirmations et
refus d'un ou deux mots, annulations, dans chaque langue où l'assistant doit les reconnaître.

### 5.3 Tentatives d'injection (18 éléments)

| Famille | Éléments |
|---|---|
| Remplacement des instructions | `fr-021`, `en-007`, `ar-008`, `darija-lat-018`, `darija-ar-013` |
| Divulgation du prompt système ou des consignes | `fr-022`, `en-007`, `ar-009`, `darija-lat-017` |
| Jeu de rôle, mode développeur, contournement de type DAN (« Do Anything Now », personnage censé ne suivre aucune règle) | `fr-023`, `en-008`, `darija-lat-018` |
| Données d'autres clients (liste, téléphones, prix) | `fr-023`, `fr-024`, `en-008`, `en-009`, `ar-008`, `darija-lat-018`, `darija-ar-013` |
| Requêtes SQL | `fr-025` |
| Faux marqueurs de rôle et jetons de conversation, demande de clés d'API | `fr-026`, `en-010` |
| Injection cachée dans une commande valide | `fr-027` (email), `darija-lat-019` (WhatsApp) |
| Consigne encodée en base64 | `fr-028` |

Un même élément peut relever de plusieurs familles.

### 5.4 Pièges faux positifs (10 éléments)

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

## 6. Choix d'étiquetage dans les cas ambigus

- `fr-018` : « ok » seul ne permet pas de fixer la langue ; « ok c'est bon » est étiqueté `fr`.
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

## 7. Données exclusivement fictives

Toutes les données du jeu sont inventées : noms de sociétés, de commerces et de personnes plausibles pour
le Maroc mais sans lien avec des clients réels, numéros de bon de commande, adresses, prix. Les numéros de
téléphone sont uniquement de la forme `+2126000000NN` et les adresses email utilisent uniquement le domaine
`exemple.ma`. Aucun message ne provient de la base `orders.db` ni d'une boîte de réception réelle.

Tout élément ajouté au fichier doit respecter les mêmes règles, garder un identifiant unique avec le
préfixe de sa langue, utiliser uniquement les valeurs d'étiquettes définies en section 3 et décrire dans
`notes` ce qu'il teste.
