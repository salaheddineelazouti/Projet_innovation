# Base de connaissances et génération augmentée par la recherche (RAG)

Ce document décrit la base de connaissances de TECPAP et la recherche qui permet à l'assistant WhatsApp de répondre aux questions des clients sur les produits. La génération augmentée par la recherche (RAG, Retrieval-Augmented Generation) consiste à chercher d'abord, dans des documents de référence, les passages qui concernent la question, puis à faire rédiger la réponse à partir de ces seuls passages. Autres documents : [assistant WhatsApp](assistant_whatsapp.md), [architecture](architecture.md), [sécurité](securite.md), [choix des modèles](choix_des_modeles.md), [guide de rédaction](../knowledge/_guide_redaction.md).

Le code se trouve dans `tecpap/rag/` : `documents.py` (lecture et découpage), `text.py` (normalisation des mots), `bm25.py` (recherche par mots-clés), `embeddings.py` (vecteurs), `store.py` (table d'index), `retriever.py` (recherche et exemples de commandes), `answer.py` (réponse), `calibration.py` (réglage du seuil).

## 1. Vue d'ensemble

Pour une question sur les produits, l'assistant suit ces étapes dans cet ordre.

1. **Question commerciale ?** Une question de prix, de délai, de stock, de remise, de paiement ou de quantité minimale est transmise à l'équipe commerciale sans recherche : ces informations ne figurent pas dans la base.
2. **Recherche.** Deux classements des passages (par mots-clés et par vecteurs) sont fusionnés.
3. **Seuil.** Si le meilleur score de pertinence est inférieur au seuil, l'assistant répond qu'il n'a pas l'information.
4. **Rédaction.** Le modèle de langue rédige la réponse à partir des passages retenus.
5. **Contrôles.** Filtre de sortie, puis vérification des chiffres cités.

Le RAG sert deux autres usages :

- les **exemples de commandes validées** placés dans l'instruction d'extraction (section 6) ;
- la **page « Base de connaissances »** du tableau de bord (section 7).

## 2. Format de la base de connaissances

La base est un dossier de fichiers Markdown, `knowledge/` par défaut (variable `KNOWLEDGE_DIR`), rédigés par l'équipe commerciale. Les fichiers dont le nom, ou celui d'un dossier parent, commence par « _ » ne sont pas indexés : c'est le cas de `_guide_redaction.md`.

Un document commence par un **en-tête YAML** (front matter, métadonnées placées entre deux lignes `---` au début du fichier), puis un titre `#` et des sections `##`.

```yaml
---
title: Papiers et codes articles TECPAP
categorie: papiers
langue: fr
mots_cles:
  - kraft
  - grammage
  - laize
  - ورق
---
```

| Clé | Contenu |
|---|---|
| `title` | Titre du document (à défaut, premier titre `#`, puis nom du fichier). |
| `categorie` | Thème : produits, papiers, commandes, contact, faq… |
| `langue` | Langue de rédaction (`fr`). |
| `mots_cles` | Mots employés par les clients, en français, en darija (lettres latines et arabes) et en arabe. |

Chaque section `##` est l'unité de recherche. Le texte placé avant la première section forme une section qui porte le titre du document. Les mots-clés de l'en-tête sont indexés avec le titre du document, le titre de la section et le texte : ils permettent de retrouver un document à partir d'une question écrite en darija ou en arabe.

Documents indexés dans le dépôt :

| Fichier | Extraits |
|---|---|
| `commander.md` | 13 |
| `contact.md` | 6 |
| `papiers_et_codes_articles.md` | 8 |
| `produits.md` | 8 |
| `questions_frequentes.md` | 18 |
| **Total** | **53** |

Un extrait (chunk) est une section, ou un morceau de section quand elle est trop longue. Ces nombres sont ceux de la page « Base de connaissances » du tableau de bord après indexation. Le dossier contient en plus `_guide_redaction.md`, qui n'est pas indexé.

## 3. Indexation

L'indexation (`KnowledgeBase.index`) compare les extraits du dossier à ceux de la table SQLite `rag_chunks`, ne calcule que les extraits absents de l'index ou modifiés et supprime ceux des documents retirés. Elle a lieu au démarrage de l'application (si `RAG_ENABLED` est vrai), sur demande depuis le tableau de bord, ou par la commande `python scripts/build_knowledge_index.py`.

### 3.1 Découpage en extraits

| Paramètre | Valeur |
|---|---|
| Longueur maximale d'un extrait | 900 caractères |
| Chevauchement entre deux extraits d'une même section | environ 120 caractères |
| Coupe | de préférence entre deux paragraphes, sinon entre deux lignes, deux phrases ou deux mots |

Chaque extrait reçoit une empreinte SHA-256 (somme de contrôle) calculée sur le titre du document, le titre de section, les mots-clés et le texte. Un extrait dont l'empreinte n'a pas changé n'est pas recalculé.

### 3.2 Normalisation des mots

Documents et questions passent par la même chaîne (`tecpap/rag/text.py`), pour qu'une même idée écrite de plusieurs façons donne les mêmes mots :

1. normalisation Unicode et minuscules ;
2. suppression des accents et des signes de voyelles de l'arabe ;
3. unification des variantes de lettres arabes ;
4. découpage en mots ;
5. réécriture de l'arabizi (darija en lettres latines, où des chiffres remplacent des lettres arabes : « b7al » devient « bhal », « 9ahwi » devient « qahwi ») ;
6. réduction des lettres répétées trois fois ou plus ;
7. retrait des mots vides (mots grammaticaux et formules de politesse, en français, en anglais, en darija et en arabe) ;
8. racinisation légère (retrait des terminaisons les plus fréquentes, sans dictionnaire).

### 3.3 Recherche par mots-clés : Okapi BM25

Okapi BM25 est une fonction de score classique de la recherche documentaire. Pour une question q et un extrait d :

```
BM25(q, d) = somme, sur les mots t de q, de
             idf(t) × tf(t, d) × (k1 + 1) / (tf(t, d) + k1 × (1 − b + b × |d| / moyenne(|d|)))
```

- `tf(t, d)` : nombre d'occurrences du mot t dans l'extrait d ;
- `|d|` : nombre de mots de d, comparé à la longueur moyenne des extraits ;
- `idf(t) = ln(1 + (N − n(t) + 0,5) / (n(t) + 0,5))` : fréquence documentaire inverse, élevée pour un mot présent dans peu des N extraits (n(t) extraits le contiennent) ;
- `k1 = 1,2` règle la saturation (la dixième occurrence d'un mot compte bien moins que la première) ;
- `b = 0,75` règle la correction par la longueur de l'extrait.

Le texte indexé de chaque extrait réunit le titre du document, le titre de section, les mots-clés et le texte. L'implémentation est en Python pur (`bm25.py`).

### 3.4 Recherche par vecteurs

Un plongement vectoriel (embedding) représente un texte par un vecteur de nombres ; deux textes proches ont des vecteurs proches (similarité cosinus : cosinus de l'angle entre les vecteurs). Deux calculs sont disponibles, choisis par la variable `RAG_EMBEDDER`.

| Valeur | Calcul | Réseau |
|---|---|---|
| `local` (défaut) | Vecteurs de n-grammes de caractères par hachage | Non |
| `openai` | Modèle de plongements de l'API OpenAI (`EMBEDDING_MODEL`, `text-embedding-3-small` par défaut) | Oui |

**Calcul local** (`LocalHashEmbedder`). Chaque mot normalisé est entouré d'espaces puis découpé en n-grammes de caractères (suites de 3, 4 et 5 caractères consécutifs) ; le mot entier compte aussi. Chaque élément est envoyé vers l'une des 512 coordonnées du vecteur par hachage : l'empreinte BLAKE2b de l'élément donne la coordonnée et un signe, ce qui évite de stocker un vocabulaire et fait se compenser en moyenne les collisions. La valeur d'une coordonnée est la somme de `1 + log(fréquence)` des éléments qui y tombent, et le vecteur est ramené à une norme de 1. Deux textes qui partagent des morceaux de mots ont des vecteurs proches, y compris en arabe, en arabizi ou avec des fautes de frappe. Le calcul est déterministe et ne demande ni réseau ni modèle.

**Calcul OpenAI** (`OpenAIEmbedder`). Il utilise `LLMClient.embed`. Si l'API est indisponible (pas de clé, erreur réseau), il se replie sur le calcul local. Le réglage `openai` sans clé donne directement le calcul local.

Les vecteurs de deux calculs différents ne sont pas comparables. Chaque vecteur est donc enregistré avec le nom de son espace vectoriel (`local-hash-v1-512` ou `openai:<modèle>`), et une question est toujours plongée dans l'espace des vecteurs auxquels elle est comparée. Après un changement de `RAG_EMBEDDER`, l'indexation recalcule les vecteurs de l'index.

### 3.5 Fusion des classements : RRF

La fusion par rangs réciproques (RRF, Reciprocal Rank Fusion) combine le classement BM25 et le classement par vecteurs. Chaque extrait reçoit la somme, sur les classements où il figure, de `1 / (k + rang)`, avec k = 60 (valeur de l'article d'origine de Cormack et collègues, 2009) et le rang commençant à 1. La fusion n'utilise que les rangs : les scores des deux méthodes, d'échelles différentes, n'ont pas besoin d'être comparables. Les 4 premiers extraits de la fusion (`RAG_TOP_K`) sont renvoyés.

## 4. Score de pertinence et seuil de réponse

Le classement RRF ordonne les extraits mais ne dit pas s'ils répondent à la question. Pour décider de répondre, chaque extrait reçoit un **score de pertinence** entre 0 et 1, moyenne de deux mesures :

| Mesure | Définition |
|---|---|
| Couverture BM25 | Part du poids idf des mots de la question présents dans l'extrait. Elle exige les mots exacts et pénalise les notions absentes de la base (prix, délais, météo…). |
| Similarité lexicale | Similarité cosinus des vecteurs de n-grammes de caractères du calcul local. Elle tolère les variantes d'écriture et les mots de la même famille. |

Les deux mesures sont indépendantes du calcul de plongements configuré : le seuil garde le même sens avec `local` ou `openai`.

L'assistant répond à partir de la base quand le **meilleur score** des extraits trouvés atteint `RAG_MIN_SCORE`, dont la valeur par défaut est **0,23** (`tecpap/config.py`, `.env.example`). En dessous, il répond « je n'ai pas cette information » sans appeler le modèle. Le score n'est pas une probabilité : il sert seulement à séparer les questions auxquelles la base répond des autres.

### 4.1 Calibration

Le jeu `tests/data/rag_queries.jsonl` contient 127 questions annotées : 84 auxquelles la base répond (avec le ou les documents attendus) et 43 hors du champ de la base (prix, délais, météo, données d'autres clients, informations absentes, sujets sans rapport). Elles sont écrites en français (51), en darija en lettres latines (26), en darija en lettres arabes (14), en arabe standard (17) et en anglais (19).

La mesure se refait avec la base actuelle par la commande :

```bash
python scripts/build_knowledge_index.py --evaluate tests/data/rag_queries.jsonl --local --db calibration.db
```

L'option `--db` désigne un fichier de base temporaire : sans elle, le script écrit l'index dans la base de l'application, avec les vecteurs du calcul local.

Résultats obtenus avec les documents du dépôt, le calcul local et le seuil de 0,23 :

| Mesure | Valeur |
|---|---|
| Aire sous la courbe ROC (probabilité qu'une question à laquelle la base répond ait un meilleur score qu'une question hors champ) | 0,917 |
| Rappel au rang 1 (premier extrait venant d'un document attendu) | 97,6 % |
| Rappel au rang 4 | 100,0 % |
| Taux de vrais positifs au seuil 0,23 (questions auxquelles la base répond et qui passent le seuil) | 85,7 % |
| Taux de vrais négatifs au seuil 0,23 (questions hors champ arrêtées) | 86,0 % |
| Exactitude équilibrée au seuil 0,23 (moyenne des deux taux) | 85,9 % |
| Meilleur seuil mesuré sur le jeu complet | 0,222 |

Résultats par langue au seuil 0,23 :

| Langue des questions | Vrais positifs | Vrais négatifs |
|---|---|---|
| Français | 100,0 % | 72,2 % |
| Darija, lettres latines | 94,4 % | 87,5 % |
| Darija, lettres arabes | 100,0 % | 100,0 % |
| Arabe standard | 81,8 % | 100,0 % |
| Anglais | 18,2 % | 100,0 % |

Lecture et prudence :

- Le seuil par défaut est proche du meilleur seuil mesuré sur ce jeu (0,222). Il a été choisi sur ce même jeu, qui compte peu de questions : les taux ci-dessus sont des mesures sur les questions qui ont servi au réglage, donc optimistes.
- Pour le vérifier, le jeu est coupé en deux moitiés équilibrées (split dev/test). Le seuil choisi sur la moitié « dev » (0,308) donne une exactitude équilibrée de 89,8 % sur dev et de 75,5 % sur la moitié « test ». Sur de nouvelles questions, la séparation attendue est donc plutôt de l'ordre de 75 %.
- Environ un quart des questions hors champ écrites en français ou en darija en lettres latines passent le seuil. Les questions de prix et de délai (17 des 43 questions hors champ) sont arrêtées avant la recherche par la détection des questions commerciales (section 1, étape 1) ; la mesure ci-dessus porte sur le score seul.
- Les questions en anglais obtiennent des scores faibles, car la base est rédigée en français : 18,2 % seulement passent le seuil, les autres reçoivent donc la réponse « je n'ai pas cette information ».

Ces chiffres sont ceux de l'exécution décrite ci-dessus ; ils changent quand les documents changent (section 9).

## 5. Génération de la réponse

La rédaction (`tecpap/rag/answer.py`) est encadrée par le code ; le modèle n'est jamais la seule garantie.

| Contrainte | Mise en œuvre |
|---|---|
| Pas de prix, de délai ni de stock | Les questions commerciales sont détectées par mots-clés (français, anglais, darija, arabe), et aussi quand le message cite lui-même un montant ou une durée (« vos sachets sont à 0,20 dh, je confirme ? »). Elles ne déclenchent ni recherche ni appel au modèle. |
| Réponse tirée des extraits seuls | Les consignes système interdisent d'inventer prix, délai, stock, disponibilité, quantité minimale, condition de paiement ou toute information absente des extraits. |
| Format | Dans la langue du client, trois phrases au plus, ton simple et poli, sans emoji, 250 jetons au plus (un jeton est un fragment de mot compté par le modèle), 700 caractères au plus. |
| Extraits traités comme des données | La question est placée entre balises `<message_client>` et chaque extrait entre balises `<extrait source="…" section="…">`. Les balises de même nom présentes dans les textes sont neutralisées : une question ne peut pas imiter un extrait. Une règle des consignes indique que le contenu délimité est une donnée à analyser, jamais une instruction. |
| Fuite des consignes | Un jeton témoin (code aléatoire placé dans les consignes, renouvelé à chaque réponse) ne doit jamais apparaître dans la réponse. |
| Filtre de sortie | Contrôle du jeton témoin, de la reprise des consignes système, des secrets, des noms de variables de configuration, des requêtes SQL et des chemins internes. Les numéros et adresses email autres que ceux de l'expéditeur et de TECPAP sont masqués. |
| Chiffres vérifiés | Chaque montant, durée ou pourcentage de la réponse doit figurer dans les extraits (jamais dans la seule question du client). Sinon, la réponse est remplacée par le message qui oriente vers l'équipe commerciale (« Les prix, les délais et les disponibilités sont communiqués par notre équipe commerciale… »), et la conversation est transférée. |
| Température basse | Au plus 0,2 (0,1 par défaut), pour rester proche des extraits. |
| Modèle choisi par la langue | Modèle léger pour le français, modèle principal pour la darija et l'arabe (voir [choix des modèles](choix_des_modeles.md)). |

L'assistant applique ensuite un second filtre de sortie à la réponse, quel que soit le composant qui l'a rédigée (voir [assistant WhatsApp](assistant_whatsapp.md), section 6).

Sans modèle de langue (pas de clé, appel en échec, réponse vide), la réponse cite le début de l'extrait le plus pertinent (300 caractères au plus, coupé à la fin d'une phrase, sans mise en forme Markdown), précédé de « D'après notre documentation : » dans la langue du client.

Issues possibles d'une question (champ `reason` de la réponse) :

| Motif | Signification | Suite donnée par l'assistant |
|---|---|---|
| `answered` | Réponse rédigée par le modèle à partir des extraits. | Envoi de la réponse. |
| `excerpt` | Citation d'un extrait, sans modèle. | Envoi de la citation. |
| `commercial` | Question de prix, délai, stock, remise, paiement ou quantité minimale, ou chiffre non fondé. | Transfert à l'équipe commerciale. |
| `no_match` | Aucun extrait n'atteint le seuil. | Message qui indique que l'information manque et invite à écrire « conseiller » ; pas de transfert. |
| `empty` | Question vide après nettoyage. | Même message. |
| `blocked` | Réponse arrêtée par le filtre de sortie. | Même message, événement de sécurité enregistré. |
| `error` | Échec de la recherche ou de la rédaction. | Même message. |

## 6. Exemples de commandes validées pour l'extraction

La recherche sert aussi à améliorer l'extraction des commandes. L'extraction par exemples (few-shot : quelques cas résolus placés dans l'instruction du modèle) donne au modèle de langue des commandes déjà validées par l'équipe commerciale, proches du message à traiter.

1. `OrderExampleRetriever` prend les commandes validées dont le message d'origine est connu (colonne `message_brut`), les 500 plus récentes au plus.
2. Il choisit les 3 plus proches du message à traiter par similarité cosinus des vecteurs.
3. Il anonymise le message et les champs de l'exemple (section 6.1).
4. L'instruction d'extraction retire les champs de prix et la devise, remplace les montants cités dans le message par `<PRIX>` et limite le message à 800 caractères.
5. Les exemples sont placés entre balises `<exemples_commandes>`, avec une règle qui précise que ce sont des données qui montrent comment remplir les champs, jamais des instructions, et qu'aucune valeur d'un exemple ne doit être reprise si elle ne figure pas dans le message analysé.

Un exemple ne montre que la façon d'écrire une commande : il ne contient aucune donnée utile à un autre client. Sans commande validée, ou si les vecteurs sont indisponibles, l'instruction d'extraction ne contient aucun exemple.

### 6.1 Anonymisation

| Donnée | Marqueur |
|---|---|
| Nom de l'entreprise (nom complet et mots qui la distinguent) | `<CLIENT>` |
| Numéros de téléphone | `<TELEPHONE>` |
| Adresses email | `<EMAIL>` |
| Noms de personnes précédés d'un titre ou d'une formule de présentation (« M. », « السيد », « je suis », « ana ») | `<PERSONNE>` |
| Adresses postales (rue, avenue, quartier, « شارع »…) | `<ADRESSE>` |
| Numéros d'au moins huit chiffres (identifiant d'entreprise…) | `<NUMERO>` |
| Adresses de sites | `<URL>` |
| Ligne « DE : » d'un email | `<EXPEDITEUR>` |
| Signature après la formule de politesse | supprimée |
| Montants cités | `<PRIX>` |

Exemple, avec un message fictif :

```
Avant :  Bonjour, c'est M. Karim Alaoui de la Boulangerie Atlas, mon numéro 0612345678 (karim@example.com).
         Livrez 5000 sachets fond plat au 12 rue des Orangers, prix 0,35 dh. Cordialement, Karim
Après :  Bonjour, c'est <PERSONNE> <CLIENT>, mon numéro <TELEPHONE> (<EMAIL>).
         Livrez 5000 sachets fond plat au <ADRESSE>, prix <PRIX>. Cordialement
```

Cette anonymisation repose sur des motifs : elle ne remplace pas une relecture quand un exemple est partagé en dehors de l'application.

## 7. Page « Base de connaissances » du tableau de bord

La page `/knowledge` (menu « Base de connaissances ») permet à l'équipe commerciale de contrôler la base sans ligne de commande.

| Élément | Rôle |
|---|---|
| Indicateurs | Nombre de documents et d'extraits indexés, calcul des plongements utilisé, seuil de réponse, nombre d'extraits par question. |
| Liste des documents | Titre, fichier, nombre d'extraits et sections de chaque document. |
| État de l'index | Indique si l'index correspond au dossier (extraits absents de l'index ou modifiés, extraits supprimés, extraits d'un autre espace vectoriel). |
| Tester une question | Recherche de test : extraits trouvés avec les composantes de leur score (pertinence, couverture BM25, similarité lexicale, similarité dense, score BM25, score RRF) et décision de l'assistant. Aucun modèle de langue n'est appelé. |
| Réindexer | Met l'index à jour ; la case « Recalculer tous les vecteurs » force le recalcul complet. Le bilan (extraits ajoutés, mis à jour, supprimés, inchangés) reste affiché. |

La page signale aussi quand `RAG_ENABLED` ou `CHATBOT_ENABLED` est désactivé.

## 8. Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| `RAG_ENABLED` | vrai | Active la recherche et l'indexation au démarrage. |
| `KNOWLEDGE_DIR` | `knowledge` | Dossier des documents. |
| `RAG_TOP_K` | 4 | Nombre d'extraits renvoyés et transmis au modèle. |
| `RAG_MIN_SCORE` | 0,23 | Seuil de réponse. |
| `RAG_EMBEDDER` | `local` | Calcul des vecteurs : `local` ou `openai`. |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Modèle de plongements pour `openai`. |
| `LLM_TEMPERATURE` | 0,1 | Température des modèles (plafonnée à 0,2 pour une réponse). |

## 9. Ajouter ou modifier un document

La procédure de rédaction (en-tête, sections, règles de confidentialité et d'exactitude) est dans le [guide de rédaction](../knowledge/_guide_redaction.md). En résumé :

1. Créer ou modifier un fichier `.md` UTF-8 dans `knowledge/`, avec un en-tête, un titre `#` et des sections `##` qui traitent chacune un seul sujet, se comprennent seules et font moins de 900 caractères. Ne mettre ni prix, ni délai, ni quantité minimale, ni donnée d'un client, ni information non validée par la direction.
2. Mettre l'index à jour :
   - poste de développement : `python scripts/build_knowledge_index.py`, ou le bouton « Réindexer » de la page « Base de connaissances » ;
   - déploiement Docker : le dossier `knowledge/` est copié dans l'image et n'est pas un volume ; reconstruire l'image, ou monter le dossier `knowledge/` de l'hôte sur `/app/knowledge`, puis cliquer sur « Réindexer » ou redémarrer le conteneur (voir [déploiement](deploiement.md)).
3. Vérifier avec « Tester une question » que les questions attendues retrouvent la section ajoutée et que les questions hors champ restent sous le seuil.
4. Contrôler la calibration avec la commande de la section 4.1, puis les tests :

```bash
python -m pytest -q tests/test_rag_documents.py tests/test_rag_retriever.py tests/test_rag_answer.py tests/test_dashboard_knowledge.py
```

Le quatrième point compte : le score dépend des mots de tous les extraits. Ajouter des mots courants (par exemple « équipe commerciale », « conseiller », « disponibilité ») peut faire passer le seuil à des questions hors champ qui étaient arrêtées. Les tests de calibration vérifient une aire sous la courbe ROC d'au moins 0,88, une exactitude équilibrée d'au moins 0,82 et un seuil par défaut à moins de 0,05 du meilleur seuil mesuré.

## 10. Limites

- **Base rédigée en français seulement.** Les mots-clés de l'en-tête en darija et en arabe aident à retrouver les documents, mais le texte des extraits est en français. Une réponse sans modèle de langue cite l'extrait en français, précédé d'une phrase dans la langue du client. Une réponse rédigée par le modèle est écrite dans la langue du client.
- **Questions en anglais.** Elles passent rarement le seuil (18,2 % sur le jeu de calibration), car la recherche repose sur les mots et les morceaux de mots de documents en français.
- **Recherche lexicale.** Le calcul local de vecteurs compare des morceaux de mots, pas le sens : une question qui reformule sans reprendre les mots des documents peut ne pas atteindre le seuil. Le calcul OpenAI (`RAG_EMBEDDER=openai`) ajoute une comparaison de sens à la fusion des classements, mais le score comparé au seuil reste lexical.
- **Mesure sur un petit jeu.** 127 questions, un seuil choisi sur ces mêmes questions et une exactitude équilibrée de 75,5 % sur la moitié « test » : le seuil est un réglage raisonnable, pas une garantie.
- **Évaluation de la recherche seulement.** La calibration mesure la recherche (quelles questions passent le seuil, quel document est trouvé). Les réponses rédigées par le modèle de langue n'ont pas été mesurées.
- **Aucun historique.** Chaque question est cherchée seule : une question de suivi (« et en blanc ? ») sans les mots du sujet atteint rarement le seuil.
- **Question sans réponse.** Une question qui n'atteint pas le seuil reçoit « je n'ai pas cette information » ; la conversation n'est pas transférée automatiquement à l'équipe commerciale.
- **Pas de prix, de délai ni de stock.** Ces informations ne sont pas dans la base ; les questions correspondantes sont toujours transmises à l'équipe commerciale.
- **Contenu validé à la main.** La base ne vaut que par ce qu'elle contient : une information fausse dans un document est envoyée telle quelle aux clients. Les valeurs de contrôle des commandes (grammages et laizes courants) sont présentées comme telles, et non comme l'offre commerciale.
- **Sources non montrées au client.** Les chemins des documents utilisés sont conservés avec la réponse (champ `sources`) mais ne figurent pas dans le message WhatsApp.
