# Choix des modèles de langue

Ce document explique quel modèle de langue (LLM, Large Language Model) TECPAP appelle pour chaque tâche et chaque langue, comment changer ces choix, ce qui se passe quand aucun modèle n'est disponible et ce que l'évaluation mesure. Le routage est réalisé par `tecpap/llm/router.py` ; les appels à l'API passent tous par `tecpap/llm/client.py`, le seul module qui utilise le SDK OpenAI. Les garde-fous de sécurité autour des modèles sont décrits dans [securite.md](securite.md).

## 1. Principe

Chaque appel à un modèle passe par `ModelRouter.route(tâche, langue)`, qui renvoie trois informations :

- le **modèle** à appeler ;
- la **température**, paramètre d'échantillonnage : plus elle est basse, plus la réponse est stable et reproductible ;
- une **raison** en une phrase. Elle est conservée avec la commande (champ `routage_modele`) : on sait après coup quel modèle a lu chaque message et pourquoi.

Les noms de modèles ne sont pas écrits dans le code métier : ce sont des réglages de la configuration (`tecpap/config.py`), modifiables par variables d'environnement (section 7).

## 2. Pourquoi choisir le modèle selon la langue

Les clients de TECPAP écrivent en français, en darija (arabe marocain, en lettres arabes ou en lettres latines avec des chiffres, appelé « arabizi ») et en arabe standard. La darija et l'arabe sont des langues peu dotées pour les modèles de langue : peu de textes dans ces langues ont servi à les entraîner, et la darija s'écrit sans orthographe fixe. Un modèle léger les comprend en général moins bien que le français ou l'anglais.

Le routeur applique donc une règle simple :

- le **modèle principal** est utilisé pour l'extraction, quelle que soit la langue, car chaque champ extrait alimente la commande ;
- pour les tâches courtes (détection d'un renouvellement, classification de l'intention, réponse au client), le **modèle léger** suffit en français et en anglais ; le modèle principal est préféré en darija, en arabe et quand la langue n'est pas identifiée.

Cette règle est un choix de conception, justifié par ce qui précède. Le dépôt ne contient aucune mesure qui compare les deux modèles sur la darija : l'évaluation hors ligne ne fait pas appel au modèle de langue (section 10). Les modèles par défaut sont ceux de `.env.example` ; ils ne sont pas une recommandation d'achat et le document ne cite aucun tarif.

## 3. Tâches

| Tâche | Rôle | Module appelant |
|---|---|---|
| Extraction de commande | Lire un message et remplir les champs de la commande | `tecpap/extraction/extractor.py` |
| Détection de renouvellement | Reconnaître « comme d'habitude », « kif dima » quand les règles n'ont rien trouvé dans un texte d'au moins 60 caractères | `tecpap/extraction/extractor.py` |
| Classification de l'intention | Orienter un message de l'assistant WhatsApp que les règles ne reconnaissent pas, avec des outils limités à l'expéditeur | `tecpap/chatbot/bot.py` |
| Réponse au client | Rédiger une réponse à partir des extraits de la base de connaissances | `tecpap/rag/answer.py` |
| Lecture d'image | Lire le texte d'une photo ou d'un scan | `tecpap/processing/multimodal.py` |
| Transcription audio | Transcrire une note vocale | `tecpap/processing/multimodal.py` |
| Plongements vectoriels | Représenter un texte par un vecteur pour la recherche dans la base de connaissances | `tecpap/rag/embeddings.py` |

## 4. Table de routage

La table est celle que renvoie `ModelRouter.route` avec les valeurs par défaut de la configuration. Les codes de langue sont ceux de `tecpap/nlp/language.py` : `fr` français, `en` anglais, `ar` arabe standard, `darija_ar` darija en lettres arabes, `darija_latin` darija en lettres latines, `darija` code de la langue de réponse en darija (quelle que soit l'écriture), `unknown` langue non identifiée.

| Tâche | Langues | Réglage | Modèle par défaut | Température par défaut | Raison |
|---|---|---|---|---|---|
| Extraction de commande | toutes | `LLM_MODEL_EXTRACTION` | gpt-4o | 0,1 | Modèle principal pour toutes les langues, car chaque champ extrait alimente la commande |
| Détection de renouvellement | fr, en | `LLM_MODEL_LIGHT` | gpt-4o-mini | 0,1 | Langue bien dotée, le modèle léger suffit pour cette tâche courte |
| Détection de renouvellement | ar, darija_ar, darija_latin, darija | `LLM_MODEL_EXTRACTION` | gpt-4o | 0,1 | Langue peu dotée pour les modèles de langue, le modèle principal est préféré |
| Détection de renouvellement | unknown | `LLM_MODEL_EXTRACTION` | gpt-4o | 0,1 | Sans langue identifiée, le modèle principal limite les erreurs de compréhension |
| Classification de l'intention | fr, en | `LLM_MODEL_LIGHT` | gpt-4o-mini | 0,1 | Langue bien dotée, le modèle léger suffit pour cette tâche courte |
| Classification de l'intention | ar, darija_ar, darija_latin, darija | `LLM_MODEL_EXTRACTION` | gpt-4o | 0,1 | Langue peu dotée pour les modèles de langue, le modèle principal est préféré |
| Classification de l'intention | unknown | `LLM_MODEL_EXTRACTION` | gpt-4o | 0,1 | Sans langue identifiée, le modèle principal limite les erreurs de compréhension |
| Réponse au client | fr, en | `LLM_MODEL_LIGHT` | gpt-4o-mini | 0,1, plafonnée à 0,2 | Langue bien dotée, le modèle léger suffit pour cette tâche courte |
| Réponse au client | ar, darija_ar, darija_latin, darija | `LLM_MODEL_EXTRACTION` | gpt-4o | 0,1, plafonnée à 0,2 | Langue peu dotée pour les modèles de langue, le modèle principal est préféré |
| Réponse au client | unknown | `LLM_MODEL_EXTRACTION` | gpt-4o | 0,1, plafonnée à 0,2 | Sans langue identifiée, le modèle principal limite les erreurs de compréhension |
| Lecture d'image | toutes | `LLM_MODEL_VISION` | gpt-4o | 0,1 | Modèle de vision, capable de lire le texte d'une image |
| Transcription audio | toutes | `TRANSCRIPTION_MODEL` | whisper-1 | sans effet | Modèle de transcription, orienté par le vocabulaire métier en français, darija et arabe |
| Plongements vectoriels | toutes | `EMBEDDING_MODEL` | text-embedding-3-small | sans effet | Modèle unique, pour que tous les vecteurs de la base de connaissances restent comparables |

Notes :

- La température par défaut est la valeur de `LLM_TEMPERATURE` (0,1). La réponse au client est plafonnée à 0,2 (`ANSWER_MAX_TEMPERATURE`) pour qu'elle reste proche des extraits de la base de connaissances : si `LLM_TEMPERATURE` vaut 0,5, la réponse utilise 0,2 et les autres tâches 0,5.
- Les API de transcription et de plongements n'utilisent pas la température : la valeur renvoyée par le routeur est sans effet.
- Une langue absente ou hors de la liste ci-dessus est traitée comme `unknown`.
- Une langue non identifiée est traitée comme une langue peu dotée : le coût d'un appel au modèle principal est préféré au risque d'une erreur de compréhension.

## 5. Transcription des notes vocales

Réglages : `TRANSCRIPTION_MODEL` (par défaut `whisper-1`) et `TRANSCRIPTION_LANGUAGE`.

- **Langue** : par défaut, `TRANSCRIPTION_LANGUAGE` est vide et le modèle détecte la langue lui-même. C'est le choix adapté aux notes vocales qui mêlent darija, arabe et français dans une même phrase. On peut imposer une langue avec un code ISO 639-1 (`fr`, `ar`) ; une langue unique imposée peut dégrader la transcription d'une note qui en mêle plusieurs.
- **Vocabulaire** : le texte `TRANSCRIPTION_HINT` (`tecpap/extraction/prompts.py`) est transmis au modèle pour orienter la reconnaissance des termes métier : types de sacs, grammage, laize, expressions de darija (« bghit », « khassni », « kif dima ») et leur écriture arabe. Il ne contient aucun nom de personne, car un nom cité serait favorisé à tort dans toutes les transcriptions. Le même texte sert pour toutes les langues : la langue d'une note vocale n'est connue qu'après sa transcription.
- **Contrôles avant l'appel** : fichier d'au moins 100 octets, format audio accepté, taille inférieure à `MAX_MEDIA_BYTES` (16 Mo par défaut). La transcription n'est lancée qu'après le contrôle de la limite de messages par minute, parce qu'elle est payante.
- **Sécurité** : la transcription est inspectée comme un message écrit (voir [securite.md](securite.md), section 5.2).
- La transcription d'un mot en darija reste approximative : la note transcrite passe par l'extraction, puis par la validation de l'équipe commerciale.

## 6. Vision

Le modèle `LLM_MODEL_VISION` (par défaut gpt-4o) lit le texte des images reçues (bon de commande photographié, capture d'écran). Les formats acceptés sont JPEG, PNG, GIF et WebP, vérifiés d'après le contenu du fichier et non d'après son nom. Le texte lu est ensuite traité comme un message : inspection contre l'injection d'instructions, extraction, validation humaine. Un PDF n'utilise pas de modèle : son texte est extrait par pypdf (100 pages au plus), et un PDF scanné sans texte n'est pas lu.

## 7. Plongements vectoriels de la base de connaissances

Deux calculs sont disponibles (`RAG_EMBEDDER`, voir [rag.md](rag.md)) :

| Valeur | Fonctionnement | Quand l'utiliser |
|---|---|---|
| `local` (défaut) | N-grammes de caractères hachés, calculés sur le serveur, sans réseau ni modèle | Usage normal : gratuit, hors ligne, résultat identique à chaque calcul, adapté à l'arabizi et aux fautes de frappe |
| `openai` | Modèle `EMBEDDING_MODEL` de l'API OpenAI, avec repli sur le calcul local si l'API est indisponible | Quand on souhaite une recherche sémantique plus large et qu'une clé d'API est configurée |

Les vecteurs de deux calculateurs différents ne sont pas comparables. Le nom de l'espace vectoriel est enregistré avec chaque vecteur, et une question est toujours calculée dans l'espace des vecteurs auxquels elle est comparée. Après un changement de `RAG_EMBEDDER` ou de `EMBEDDING_MODEL`, il faut réindexer la base de connaissances : bouton de réindexation de la page « Base de connaissances » du tableau de bord (option de recalcul complet), qui signale le nombre d'extraits restés dans un autre espace.

## 8. Changer les modèles

Les modèles se changent par variables d'environnement, dans le fichier `.env` ou dans l'environnement du conteneur. La configuration est lue une seule fois au démarrage : redémarrer l'application après chaque changement.

| Variable | Effet | Valeur par défaut |
|---|---|---|
| `LLM_MODEL_EXTRACTION` | Modèle principal : extraction, et tâches courtes en darija, arabe ou langue non identifiée | gpt-4o |
| `LLM_MODEL_LIGHT` | Modèle léger : tâches courtes en français et en anglais | gpt-4o-mini |
| `LLM_MODEL_VISION` | Lecture du texte des images | gpt-4o |
| `TRANSCRIPTION_MODEL` | Transcription des notes vocales | whisper-1 |
| `TRANSCRIPTION_LANGUAGE` | Langue imposée à la transcription (code ISO 639-1) ; vide : détection automatique | vide |
| `EMBEDDING_MODEL` | Plongements, avec `RAG_EMBEDDER=openai` | text-embedding-3-small |
| `RAG_EMBEDDER` | `local` ou `openai` | local |
| `LLM_TEMPERATURE` | Température de toutes les tâches de texte | 0,1 |
| `LLM_TIMEOUT` | Délai maximal d'un appel, en secondes | 60 |
| `OPENAI_API_KEY` | Clé d'API ; sans clé, aucun modèle n'est appelé (section 9) | vide |

Conditions pour un modèle de remplacement : il doit être proposé par l'API OpenAI avec laquelle le client est écrit, accepter le format de sortie « objet JSON » (extraction), l'appel d'outils (assistant) et, pour la vision et la transcription, les entrées image et audio. Pour comparer deux modèles, lancer l'évaluation en ligne (section 11) avec chacun et comparer les rapports.

La température se règle avec prudence : une valeur élevée rend l'extraction moins stable d'un appel à l'autre.

## 9. Comportement sans modèle de langue

Quand `OPENAI_API_KEY` est absente, ou quand un appel échoue (délai dépassé, erreur du fournisseur, réponse illisible), le système continue à fonctionner avec ses règles déterministes. Le client des modèles ne lève jamais d'exception vers l'appelant : l'incident est journalisé sans le contenu du message. Les erreurs passagères donnent lieu à un seul nouvel essai après une courte pause.

| Fonction | Sans modèle |
|---|---|
| Extraction de commande | Extraction par règles (`rule_based_extract`). La commande reçoit l'anomalie « Extraction par règles (modèle de langue indisponible) : vérifier tous les champs », et sa confiance ne dépasse pas 50 |
| Détection de renouvellement | Les règles décident seules |
| Assistant WhatsApp | Les intentions reconnues par règles (commande, suivi, annulation, conseiller) fonctionnent ; un message sans intention reconnue reçoit le message d'aide |
| Réponse à une question produit | Citation brève de l'extrait le plus pertinent de la base de connaissances |
| Lecture d'image, transcription | Média non lu ; si le message n'a pas d'autre texte, le client reçoit le message « média illisible » |
| Recherche dans la base de connaissances | Calcul local des plongements (réglage par défaut) |

La commande arrive donc au tableau de bord, où l'équipe commerciale vérifie tous les champs : c'est la raison pour laquelle l'anomalie est signalée et la confiance plafonnée. Le champ `modele_llm` de la commande vaut alors vide.

## 10. Ce que l'évaluation hors ligne mesure et ne mesure pas

L'évaluation hors ligne (`evaluation/run_eval.py`, résultats dans `evaluation/results/`) ne fait aucun appel réseau.

**Elle mesure** la couche déterministe :

- la détection de la langue et de l'écriture ;
- la détection des intentions de l'assistant WhatsApp ;
- l'extraction par règles (type de produit, quantité, unité, nom du client) et la reconnaissance des bons de commande ;
- la détection des renouvellements par règles ;
- la détection des tentatives d'injection d'instructions.

**Elle ne mesure pas** :

- l'extraction par le modèle de langue, en particulier en darija ;
- la comparaison entre le modèle principal et le modèle léger ;
- la qualité de la transcription et de la lecture d'image ;
- la qualité des réponses de la base de connaissances rédigées par le modèle ;
- le comportement du modèle face à une injection d'instructions ;
- les coûts et les délais des appels.

Ses chiffres décrivent donc la qualité du repli par règles et des garde-fous, pas celle de l'extraction réalisée avec un modèle. Les valeurs sont dans `evaluation/results/synthese.md`, avec leur intervalle de confiance à 95 % (intervalle de score de Wilson) ; le jeu de test est tenu à l'écart du réglage (split dev/test, voir [evaluation/README.md](../evaluation/README.md), section 9).

## 11. Lancer l'évaluation en ligne

L'évaluation en ligne fait passer chaque commande du jeu de données par l'extraction complète : modèle de langue, puis règles de normalisation, sans base de données. Elle compare les mêmes champs que l'évaluation hors ligne. Une commande extraite par les règles faute de réponse du modèle compte comme une extraction en échec : cette évaluation mesure le modèle.

```
python evaluation/run_eval.py --online
python evaluation/run_eval.py --online --split dev
```

- Elle exige `OPENAI_API_KEY` (variable d'environnement ou fichier `.env`). Sans clé, le programme s'arrête avec le code de sortie 2 sans aucun appel réseau.
- Chaque commande donne lieu à une requête d'extraction, plus une requête de détection du renouvellement quand les règles n'en trouvent pas dans un texte d'au moins 60 caractères : l'exécution est payante et ses résultats varient d'un lancement à l'autre.
- `--split dev` limite l'exécution au jeu de développement. Pour garder la valeur du jeu de test, ne s'en servir que pour une mesure finale, jamais pour choisir ou régler un modèle.
- Les rapports sont écrits dans `evaluation/results/` (`rapport_en_ligne_dev.md` et `rapport_en_ligne_test.md`, avec leur fichier JSON). Aucun rapport en ligne n'est fourni avec le dépôt : l'évaluation en ligne n'a pas été exécutée.
- Pour changer de modèle, modifier la variable d'environnement correspondante (section 8), relancer l'évaluation et comparer les rapports.
