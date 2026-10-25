# Évaluation hors ligne de l'analyse des messages clients : jeu de développement

Mesure, sans réseau ni modèle de langue, des composants déterministes qui analysent les messages clients, sur le corpus annoté décrit dans `evaluation/README.md`.

Le corpus annoté est réparti en deux partitions (« split dev/test ») : le jeu de développement (`dev`) et le jeu de test tenu à l'écart (`test`). Ce rapport porte sur le jeu de développement (`dev`). Ses erreurs servent à écrire et à ajuster les règles, les listes de mots-clés et les seuils : ses scores surestiment donc la qualité réelle des composants.

| Information | Valeur |
| --- | --- |
| Date de l'exécution | 2026-10-25 |
| Partition | `dev` |
| Fichiers du jeu | 2 |
| Éléments du jeu | 198 |
| Version du paquet `tecpap` | 3.0.0 |

| Fichier | Éléments | Empreinte SHA-256 |
| --- | ---: | --- |
| `evaluation/dataset/messages.jsonl` | 89 | `96fa89491ab4acded5276a5a68919a4ad53d0fc1d75c66758a4e8df2655f6e60` |
| `evaluation/dataset/messages_dev_complement.jsonl` | 109 | `1f05e48593d0e3c7265fdf18b980a7650bcb901bdd6d6ac58e96dc1b5766fcd1` |

L'empreinte SHA-256 est une somme de contrôle du fichier : deux rapports qui portent la même empreinte ont été calculés sur exactement les mêmes données.

## 1. Jeu de données

Codes de langue : `fr` français, `darija_latin` darija en lettres latines, `darija_ar` darija en lettres arabes, `ar` arabe standard moderne, `en` anglais, `unknown` langue non identifiée.

| Langue | Éléments | Email | WhatsApp | Commandes | Renouvellements | Injections | Pièges |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 68 | 27 | 41 | 39 | 4 | 15 | 11 |
| `darija_latin` | 46 | 0 | 46 | 24 | 4 | 7 | 4 |
| `darija_ar` | 30 | 0 | 30 | 12 | 2 | 4 | 1 |
| `ar` | 26 | 5 | 21 | 12 | 2 | 5 | 3 |
| `en` | 28 | 9 | 19 | 11 | 2 | 8 | 4 |
| Total | 198 | 41 | 157 | 98 | 14 | 39 | 23 |

Effectifs par fichier :

| Fichier | Éléments | Email | WhatsApp | Commandes | Renouvellements | Injections | Pièges |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `evaluation/dataset/messages.jsonl` | 89 | 24 | 65 | 41 | 7 | 18 | 10 |
| `evaluation/dataset/messages_dev_complement.jsonl` | 109 | 17 | 92 | 57 | 7 | 21 | 13 |
| Total | 198 | 41 | 157 | 98 | 14 | 39 | 23 |

« Commandes » : éléments dont l'étiquette `is_order` est vraie, renouvellements compris. « Renouvellements » : intention `reorder`. « Injections » : tentatives d'injection d'instructions (`is_injection` vrai). « Pièges » : messages légitimes qui emploient les mots surveillés par la détection d'injection (catégorie `piege_faux_positif`).

## 2. Mesures employées

| Mesure | Définition |
| --- | --- |
| **Exactitude** | Part des éléments dont la valeur obtenue est égale à l'étiquette attendue. |
| **Intervalle de confiance à 95 %** | Intervalle de score de Wilson : plage de valeurs de la proportion réelle compatibles avec l'échantillon. Pour k réussites sur n éléments, avec p = k / n et z = 1,96 (quantile à 97,5 % de la loi normale), il est centré sur (p + z² / (2n)) / (1 + z² / n) et sa demi-largeur vaut z √(p (1 − p) / n + z² / (4n²)) / (1 + z² / n). Il reste compris entre 0 % et 100 % et garde une largeur non nulle quand p vaut 0 % ou 100 %. Plus l'effectif est petit, plus la plage est large. |
| **Matrice de confusion** | Tableau croisé : une ligne par étiquette attendue, une colonne par valeur obtenue. Les bonnes réponses sont sur la diagonale. |
| **Classe positive** | Catégorie que le détecteur doit signaler (commande, tentative d'injection…). |
| **VP (vrai positif)** | Cas positif signalé. |
| **FP (faux positif)** | Cas négatif signalé à tort : fausse alerte. |
| **FN (faux négatif)** | Cas positif manqué. |
| **VN (vrai négatif)** | Cas négatif non signalé. |
| **Précision** | VP / (VP + FP) : part des signalements justes. |
| **Rappel** | VP / (VP + FN) : part des cas positifs signalés. |
| **F1** | 2 VP / (2 VP + FP + FN) : moyenne harmonique de la précision et du rappel. |
| **Taux de faux positifs** | FP / (FP + VN) : part des cas négatifs signalés à tort. |
| **Macro-F1** | Moyenne non pondérée des F1 des classes présentes dans le jeu, chaque classe étant tour à tour la classe positive. |

« Effectifs » : numérateur et dénominateur de la mesure. « n.d. » : mesure non définie (dénominateur nul). « — » : aucune valeur.

## 3. Synthèse

| Composant | Mesure | Effectifs | Valeur | Intervalle de confiance à 95 % |
| --- | --- | ---: | ---: | ---: |
| Détection de la langue | Exactitude du code de langue | 198/198 | 100,0 % | [98,1 % ; 100,0 %] |
| Détection de la langue | Exactitude de la langue de réponse | 198/198 | 100,0 % | [98,1 % ; 100,0 %] |
| Extraction par règles | Exactitude de `type_produit` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] |
| Extraction par règles | Exactitude de `quantite` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] |
| Extraction par règles | Exactitude de `unite` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] |
| Extraction par règles | Exactitude de `entreprise_cliente` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] |
| Extraction par règles | Exactitude des champs obligatoires | 98/98 | 100,0 % | [96,2 % ; 100,0 %] |
| Détection des commandes | Exactitude | 198/198 | 100,0 % | [98,1 % ; 100,0 %] |
| Détection des commandes | Précision | 98/98 | 100,0 % | [96,2 % ; 100,0 %] |
| Détection des commandes | Rappel | 98/98 | 100,0 % | [96,2 % ; 100,0 %] |
| Détection des commandes | F1 | — | 100,0 % | — |
| Détection des renouvellements | Exactitude | 198/198 | 100,0 % | [98,1 % ; 100,0 %] |
| Détection des renouvellements | Précision | 14/14 | 100,0 % | [78,5 % ; 100,0 %] |
| Détection des renouvellements | Rappel | 14/14 | 100,0 % | [78,5 % ; 100,0 %] |
| Détection des renouvellements | F1 | — | 100,0 % | — |
| Détection des injections | Précision | 39/39 | 100,0 % | [91,0 % ; 100,0 %] |
| Détection des injections | Rappel | 39/39 | 100,0 % | [91,0 % ; 100,0 %] |
| Détection des injections | F1 | — | 100,0 % | — |
| Détection des injections | Taux de faux positifs | 0/159 | 0,0 % | [0,0 % ; 2,4 %] |
| Détection des injections | Fausses alertes sur les pièges | 0/23 | 0,0 % | [0,0 % ; 14,3 %] |
| Détection des intentions | Exactitude | 198/198 | 100,0 % | [98,1 % ; 100,0 %] |
| Détection des intentions | Macro-F1 | — | 100,0 % | — |

## 4. Synthèse par fichier

Mêmes mesures que la synthèse, calculées sur les éléments de chaque fichier de la partition (fichiers du tableau de la section 1). Plus un fichier est petit, plus ses intervalles de confiance sont larges.

| Composant | Mesure | Effectifs (`messages.jsonl`) | Valeur (`messages.jsonl`) | Intervalle de confiance à 95 % (`messages.jsonl`) | Effectifs (`messages_dev_complement.jsonl`) | Valeur (`messages_dev_complement.jsonl`) | Intervalle de confiance à 95 % (`messages_dev_complement.jsonl`) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Détection de la langue | Exactitude du code de langue | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 109/109 | 100,0 % | [96,6 % ; 100,0 %] |
| Détection de la langue | Exactitude de la langue de réponse | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 109/109 | 100,0 % | [96,6 % ; 100,0 %] |
| Extraction par règles | Exactitude de `type_produit` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 57/57 | 100,0 % | [93,7 % ; 100,0 %] |
| Extraction par règles | Exactitude de `quantite` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 57/57 | 100,0 % | [93,7 % ; 100,0 %] |
| Extraction par règles | Exactitude de `unite` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 57/57 | 100,0 % | [93,7 % ; 100,0 %] |
| Extraction par règles | Exactitude de `entreprise_cliente` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 57/57 | 100,0 % | [93,7 % ; 100,0 %] |
| Extraction par règles | Exactitude des champs obligatoires | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 57/57 | 100,0 % | [93,7 % ; 100,0 %] |
| Détection des commandes | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 109/109 | 100,0 % | [96,6 % ; 100,0 %] |
| Détection des commandes | Précision | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 57/57 | 100,0 % | [93,7 % ; 100,0 %] |
| Détection des commandes | Rappel | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 57/57 | 100,0 % | [93,7 % ; 100,0 %] |
| Détection des commandes | F1 | — | 100,0 % | — | — | 100,0 % | — |
| Détection des renouvellements | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 109/109 | 100,0 % | [96,6 % ; 100,0 %] |
| Détection des renouvellements | Précision | 7/7 | 100,0 % | [64,6 % ; 100,0 %] | 7/7 | 100,0 % | [64,6 % ; 100,0 %] |
| Détection des renouvellements | Rappel | 7/7 | 100,0 % | [64,6 % ; 100,0 %] | 7/7 | 100,0 % | [64,6 % ; 100,0 %] |
| Détection des renouvellements | F1 | — | 100,0 % | — | — | 100,0 % | — |
| Détection des injections | Précision | 18/18 | 100,0 % | [82,4 % ; 100,0 %] | 21/21 | 100,0 % | [84,5 % ; 100,0 %] |
| Détection des injections | Rappel | 18/18 | 100,0 % | [82,4 % ; 100,0 %] | 21/21 | 100,0 % | [84,5 % ; 100,0 %] |
| Détection des injections | F1 | — | 100,0 % | — | — | 100,0 % | — |
| Détection des injections | Taux de faux positifs | 0/71 | 0,0 % | [0,0 % ; 5,1 %] | 0/88 | 0,0 % | [0,0 % ; 4,2 %] |
| Détection des injections | Fausses alertes sur les pièges | 0/10 | 0,0 % | [0,0 % ; 27,8 %] | 0/13 | 0,0 % | [0,0 % ; 22,8 %] |
| Détection des intentions | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 109/109 | 100,0 % | [96,6 % ; 100,0 %] |
| Détection des intentions | Macro-F1 | — | 100,0 % | — | — | 100,0 % | — |

## 5. Détection de la langue

Composant : `tecpap.nlp.language.detect_language`, puis `tecpap.nlp.language.reply_language` pour la langue de réponse. Étiquettes comparées : `language` et `reply_language`, sur les 198 éléments du jeu.

### 5.1 Exactitude par langue

| Langue | Éléments | Code juste | Exactitude du code | Réponse juste | Exactitude de la réponse |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 68 | 68 | 100,0 % | 68 | 100,0 % |
| `darija_latin` | 46 | 46 | 100,0 % | 46 | 100,0 % |
| `darija_ar` | 30 | 30 | 100,0 % | 30 | 100,0 % |
| `ar` | 26 | 26 | 100,0 % | 26 | 100,0 % |
| `en` | 28 | 28 | 100,0 % | 28 | 100,0 % |
| Total | 198 | 198 | 100,0 % | 198 | 100,0 % |

### 5.2 Matrice de confusion du code de langue

Lignes : langue attendue. Colonnes : langue obtenue.

| Attendu | `fr` | `darija_latin` | `darija_ar` | `ar` | `en` | `unknown` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 68 | 0 | 0 | 0 | 0 | 0 | 68 |
| `darija_latin` | 0 | 46 | 0 | 0 | 0 | 0 | 46 |
| `darija_ar` | 0 | 0 | 30 | 0 | 0 | 0 | 30 |
| `ar` | 0 | 0 | 0 | 26 | 0 | 0 | 26 |
| `en` | 0 | 0 | 0 | 0 | 28 | 0 | 28 |

### 5.3 Éléments mal classés

Code de langue :

Aucune erreur.

Langue de réponse :

Aucune erreur.

## 6. Extraction par règles des commandes

Composant : `tecpap.extraction.rules.rule_based_extract`, appliqué aux 98 commandes du jeu (étiquette `is_order` vraie).

| Champ | Comparaison |
| --- | --- |
| `type_produit` | Égalité exacte du nom du produit du catalogue. |
| `quantite` | Égalité numérique (10000 et 10000,0 sont égales). |
| `unite` | Égalité exacte avec l'unité écrite par le client : `pièces` pour des pièces ou des unités, `kg`, `carton` ou `paquet`, `US` pour une quantité sans unité (`evaluation/README.md`, section 4.7). |
| `entreprise_cliente` | Égalité sans tenir compte de la casse, des accents, des signes diacritiques ni des espaces répétées. |
| Champs obligatoires | Les champs obligatoires d'une commande (`entreprise_cliente`, `type_produit`, `quantite`) sont tous justes. |

Une valeur absente est juste quand l'étiquette vaut `null`.

### 6.1 Exactitude par champ

| Champ | Justes | Total | Exactitude |
| --- | ---: | ---: | ---: |
| `type_produit` | 98 | 98 | 100,0 % |
| `quantite` | 98 | 98 | 100,0 % |
| `unite` | 98 | 98 | 100,0 % |
| `entreprise_cliente` | 98 | 98 | 100,0 % |
| Champs obligatoires | 98 | 98 | 100,0 % |

### 6.2 Exactitude par langue

| Langue | Commandes | `type_produit` | `quantite` | `unite` | `entreprise_cliente` | Champs obligatoires |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 39 | 39/39 | 39/39 | 39/39 | 39/39 | 39/39 |
| `darija_latin` | 24 | 24/24 | 24/24 | 24/24 | 24/24 | 24/24 |
| `darija_ar` | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| `ar` | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| `en` | 11 | 11/11 | 11/11 | 11/11 | 11/11 | 11/11 |

### 6.3 Erreurs par champ

#### `type_produit`

Aucune erreur.

#### `quantite`

Aucune erreur.

#### `unite`

Aucune erreur.

#### `entreprise_cliente`

Aucune erreur.

#### Champs obligatoires

Commandes dont au moins un champ obligatoire est faux :

Aucune erreur.

## 7. Détection des commandes et des renouvellements

### 7.1 Commandes

Composant : champ `est_bon_commande` renvoyé par `tecpap.extraction.rules.rule_based_extract`. Étiquette : `is_order`. Classe positive : commande (98 éléments sur 198).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 198/198 | 100,0 % |
| Précision | 98/98 | 100,0 % |
| Rappel | 98/98 | 100,0 % |
| F1 | — | 100,0 % |
| Taux de faux positifs | 0/100 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 98 | 0 | 0 | 100 |

#### Commandes — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 68 | 68 | 100,0 % | 0 | 0 |
| `darija_latin` | 46 | 46 | 100,0 % | 0 | 0 |
| `darija_ar` | 30 | 30 | 100,0 % | 0 | 0 |
| `ar` | 26 | 26 | 100,0 % | 0 | 0 |
| `en` | 28 | 28 | 100,0 % | 0 | 0 |

#### Commandes — faux positifs

Aucune erreur.

#### Commandes — faux négatifs

Aucune erreur.

### 7.2 Renouvellements

Composant : `tecpap.extraction.rules.detect_reorder`. Étiquette : intention `reorder`. Classe positive : renouvellement (14 éléments sur 198).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 198/198 | 100,0 % |
| Précision | 14/14 | 100,0 % |
| Rappel | 14/14 | 100,0 % |
| F1 | — | 100,0 % |
| Taux de faux positifs | 0/184 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 14 | 0 | 0 | 184 |

#### Renouvellements — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 68 | 68 | 100,0 % | 0 | 0 |
| `darija_latin` | 46 | 46 | 100,0 % | 0 | 0 |
| `darija_ar` | 30 | 30 | 100,0 % | 0 | 0 |
| `ar` | 26 | 26 | 100,0 % | 0 | 0 |
| `en` | 28 | 28 | 100,0 % | 0 | 0 |

#### Renouvellements — faux positifs

Aucune erreur.

#### Renouvellements — faux négatifs

Aucune erreur.

## 8. Détection des tentatives d'injection

Composant : `tecpap.security.guardrails.inspect_input`. Un message est signalé quand son score atteint 50 (sur 100). Étiquette : `is_injection`. Classe positive : tentative d'injection (39 éléments sur 198). Les 159 autres éléments, dont 23 pièges, sont les cas négatifs.

### 8.1 Mesures

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 198/198 | 100,0 % |
| Précision | 39/39 | 100,0 % |
| Rappel | 39/39 | 100,0 % |
| F1 | — | 100,0 % |
| Taux de faux positifs | 0/159 | 0,0 % |
| Fausses alertes sur les pièges | 0/23 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 39 | 0 | 0 | 159 |

### 8.2 Scores

| Mesure | Score | Élément |
| --- | ---: | --- |
| Score maximal d'un message légitime | 30 | `dev2-fr-034` |
| Score minimal d'une injection | 50 | `fr-025` |
| Seuil de signalement | 50 | — |

### 8.3 Détection par langue

| Langue | Injections | Détectées | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 15 | 15 | 100,0 % | 100,0 % | 100,0 % |
| `darija_latin` | 7 | 7 | 100,0 % | 100,0 % | 100,0 % |
| `darija_ar` | 4 | 4 | 100,0 % | 100,0 % | 100,0 % |
| `ar` | 5 | 5 | 100,0 % | 100,0 % | 100,0 % |
| `en` | 8 | 8 | 100,0 % | 100,0 % | 100,0 % |

### 8.4 Fausses alertes par langue

| Langue | Légitimes | Fausses alertes | Taux de faux positifs | Pièges | Alertes sur pièges |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 53 | 0 | 0,0 % | 11 | 0 |
| `darija_latin` | 39 | 0 | 0,0 % | 4 | 0 |
| `darija_ar` | 26 | 0 | 0,0 % | 1 | 0 |
| `ar` | 21 | 0 | 0,0 % | 3 | 0 |
| `en` | 20 | 0 | 0,0 % | 4 | 0 |

### 8.5 Erreurs

Injections manquées (faux négatifs) :

Aucune erreur.

Fausses alertes (faux positifs) :

Aucune erreur.

## 9. Détection des intentions

Composant : `tecpap.chatbot.intents.detect_intent`, appelé avec la langue obtenue par `tecpap.nlp.language.detect_language`, comme dans l'assistant WhatsApp. Étiquette : `intent` (définitions dans `evaluation/README.md`, section 4.3), sur les 198 éléments du jeu.

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 198/198 | 100,0 % |
| Macro-F1 | — | 100,0 % |

### 9.1 Mesures par intention

« Attendus » : éléments étiquetés avec l'intention. « Prédits » : éléments auxquels le détecteur l'attribue.

| Intention | Attendus | Prédits | VP | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 84 | 84 | 84 | 100,0 % | 100,0 % | 100,0 % |
| `reorder` | 14 | 14 | 14 | 100,0 % | 100,0 % | 100,0 % |
| `status` | 10 | 10 | 10 | 100,0 % | 100,0 % | 100,0 % |
| `question` | 10 | 10 | 10 | 100,0 % | 100,0 % | 100,0 % |
| `human` | 8 | 8 | 8 | 100,0 % | 100,0 % | 100,0 % |
| `greeting` | 9 | 9 | 9 | 100,0 % | 100,0 % | 100,0 % |
| `confirm_yes` | 14 | 14 | 14 | 100,0 % | 100,0 % | 100,0 % |
| `confirm_no` | 9 | 9 | 9 | 100,0 % | 100,0 % | 100,0 % |
| `cancel` | 8 | 8 | 8 | 100,0 % | 100,0 % | 100,0 % |
| `other` | 32 | 32 | 32 | 100,0 % | 100,0 % | 100,0 % |

### 9.2 Exactitude par langue

| Langue | Éléments | Justes | Exactitude |
| --- | ---: | ---: | ---: |
| `fr` | 68 | 68 | 100,0 % |
| `darija_latin` | 46 | 46 | 100,0 % |
| `darija_ar` | 30 | 30 | 100,0 % |
| `ar` | 26 | 26 | 100,0 % |
| `en` | 28 | 28 | 100,0 % |

### 9.3 Matrice de confusion

Lignes : intention attendue. Colonnes : intention obtenue.

| Attendu | `order` | `reorder` | `status` | `question` | `human` | `greeting` | `confirm_yes` | `confirm_no` | `cancel` | `other` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 84 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 84 |
| `reorder` | 0 | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 14 |
| `status` | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 |
| `question` | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 10 |
| `human` | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 8 |
| `greeting` | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 9 |
| `confirm_yes` | 0 | 0 | 0 | 0 | 0 | 0 | 14 | 0 | 0 | 0 | 14 |
| `confirm_no` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | 9 |
| `cancel` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 8 |
| `other` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 32 | 32 |

### 9.4 Éléments mal classés

Aucune erreur.
