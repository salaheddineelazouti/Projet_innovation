# Évaluation hors ligne de l'analyse des messages clients

Mesure, sans réseau ni modèle de langue, des composants déterministes qui analysent les messages clients, sur le jeu annoté décrit dans `evaluation/README.md`.

| Information | Valeur |
| --- | --- |
| Date de l'exécution | 2026-10-25 |
| Jeu de données | `evaluation/dataset/messages.jsonl` |
| Empreinte SHA-256 du fichier | `09c7adc05b0fac57c73b3d467d7b808e28d4f128b49ba529eb8a8c63c6aedf5e` |
| Éléments du jeu | 89 |
| Version du paquet `tecpap` | 3.0.0 |

L'empreinte SHA-256 est une somme de contrôle du fichier : deux rapports qui portent la même empreinte ont été calculés sur exactement les mêmes données.

## 1. Jeu de données

Codes de langue : `fr` français, `darija_latin` darija en lettres latines, `darija_ar` darija en lettres arabes, `ar` arabe standard moderne, `en` anglais, `unknown` langue non identifiée.

| Langue | Éléments | Email | WhatsApp | Commandes | Renouvellements | Injections | Pièges |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 16 | 18 | 17 | 2 | 8 | 6 |
| `darija_latin` | 20 | 0 | 20 | 9 | 2 | 3 | 1 |
| `darija_ar` | 13 | 0 | 13 | 5 | 1 | 1 | 0 |
| `ar` | 10 | 3 | 7 | 5 | 1 | 2 | 1 |
| `en` | 12 | 5 | 7 | 5 | 1 | 4 | 2 |
| Total | 89 | 24 | 65 | 41 | 7 | 18 | 10 |

« Commandes » : éléments dont l'étiquette `is_order` est vraie, renouvellements compris. « Renouvellements » : intention `reorder`. « Injections » : tentatives d'injection d'instructions (`is_injection` vrai). « Pièges » : messages légitimes qui emploient les mots surveillés par la détection d'injection (catégorie `piege_faux_positif`).

## 2. Mesures employées

| Mesure | Définition |
| --- | --- |
| **Exactitude** | Part des éléments dont la valeur obtenue est égale à l'étiquette attendue. |
| **Intervalle de confiance à 95 %** | Plage de valeurs de la proportion réelle compatibles avec l'échantillon (méthode de Wilson). Plus l'effectif est petit, plus la plage est large. |
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
| Détection de la langue | Exactitude du code de langue | 89/89 | 100,0 % | [95,9 % ; 100,0 %] |
| Détection de la langue | Exactitude de la langue de réponse | 89/89 | 100,0 % | [95,9 % ; 100,0 %] |
| Extraction par règles | Exactitude de `type_produit` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] |
| Extraction par règles | Exactitude de `quantite` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] |
| Extraction par règles | Exactitude de `unite` | 39/41 | 95,1 % | [83,9 % ; 98,7 %] |
| Extraction par règles | Exactitude de `entreprise_cliente` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] |
| Extraction par règles | Exactitude des champs obligatoires | 41/41 | 100,0 % | [91,4 % ; 100,0 %] |
| Détection des commandes | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] |
| Détection des commandes | Précision | 41/41 | 100,0 % | [91,4 % ; 100,0 %] |
| Détection des commandes | Rappel | 41/41 | 100,0 % | [91,4 % ; 100,0 %] |
| Détection des commandes | F1 | — | 100,0 % | — |
| Détection des renouvellements | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] |
| Détection des renouvellements | Précision | 7/7 | 100,0 % | [64,6 % ; 100,0 %] |
| Détection des renouvellements | Rappel | 7/7 | 100,0 % | [64,6 % ; 100,0 %] |
| Détection des renouvellements | F1 | — | 100,0 % | — |
| Détection des injections | Précision | 18/18 | 100,0 % | [82,4 % ; 100,0 %] |
| Détection des injections | Rappel | 18/18 | 100,0 % | [82,4 % ; 100,0 %] |
| Détection des injections | F1 | — | 100,0 % | — |
| Détection des injections | Taux de faux positifs | 0/71 | 0,0 % | [0,0 % ; 5,1 %] |
| Détection des injections | Fausses alertes sur les pièges | 0/10 | 0,0 % | [0,0 % ; 27,8 %] |
| Détection des intentions | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] |
| Détection des intentions | Macro-F1 | — | 100,0 % | — |

## 4. Détection de la langue

Composant : `tecpap.nlp.language.detect_language`, puis `tecpap.nlp.language.reply_language` pour la langue de réponse. Étiquettes comparées : `language` et `reply_language`, sur les 89 éléments du jeu.

### 4.1 Exactitude par langue

| Langue | Éléments | Code juste | Exactitude du code | Réponse juste | Exactitude de la réponse |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 34 | 100,0 % | 34 | 100,0 % |
| `darija_latin` | 20 | 20 | 100,0 % | 20 | 100,0 % |
| `darija_ar` | 13 | 13 | 100,0 % | 13 | 100,0 % |
| `ar` | 10 | 10 | 100,0 % | 10 | 100,0 % |
| `en` | 12 | 12 | 100,0 % | 12 | 100,0 % |
| Total | 89 | 89 | 100,0 % | 89 | 100,0 % |

### 4.2 Matrice de confusion du code de langue

Lignes : langue attendue. Colonnes : langue obtenue.

| Attendu | `fr` | `darija_latin` | `darija_ar` | `ar` | `en` | `unknown` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 0 | 0 | 0 | 0 | 0 | 34 |
| `darija_latin` | 0 | 20 | 0 | 0 | 0 | 0 | 20 |
| `darija_ar` | 0 | 0 | 13 | 0 | 0 | 0 | 13 |
| `ar` | 0 | 0 | 0 | 10 | 0 | 0 | 10 |
| `en` | 0 | 0 | 0 | 0 | 12 | 0 | 12 |

### 4.3 Éléments mal classés

Code de langue :

Aucune erreur.

Langue de réponse :

Aucune erreur.

## 5. Extraction par règles des commandes

Composant : `tecpap.extraction.rules.rule_based_extract`, appliqué aux 41 commandes du jeu (étiquette `is_order` vraie).

| Champ | Comparaison |
| --- | --- |
| `type_produit` | Égalité exacte du nom du produit du catalogue. |
| `quantite` | Égalité numérique (10000 et 10000,0 sont égales). |
| `unite` | Égalité exacte. L'étiquette vaut `US` dès qu'une quantité est donnée (`evaluation/README.md`, section 4.7). |
| `entreprise_cliente` | Égalité sans tenir compte de la casse, des accents, des signes diacritiques ni des espaces répétées. |
| Champs obligatoires | Les champs obligatoires d'une commande (`entreprise_cliente`, `type_produit`, `quantite`) sont tous justes. |

Une valeur absente est juste quand l'étiquette vaut `null`.

### 5.1 Exactitude par champ

| Champ | Justes | Total | Exactitude |
| --- | ---: | ---: | ---: |
| `type_produit` | 41 | 41 | 100,0 % |
| `quantite` | 41 | 41 | 100,0 % |
| `unite` | 39 | 41 | 95,1 % |
| `entreprise_cliente` | 41 | 41 | 100,0 % |
| Champs obligatoires | 41 | 41 | 100,0 % |

### 5.2 Exactitude par langue

| Langue | Commandes | `type_produit` | `quantite` | `unite` | `entreprise_cliente` | Champs obligatoires |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 17 | 17/17 | 17/17 | 15/17 | 17/17 | 17/17 |
| `darija_latin` | 9 | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 |
| `darija_ar` | 5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| `ar` | 5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| `en` | 5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |

### 5.3 Erreurs par champ

#### `type_produit`

Aucune erreur.

#### `quantite`

Aucune erreur.

#### `unite`

| Élément | Langue | Attendu | Obtenu |
| --- | --- | --- | --- |
| `fr-001` | `fr` | US | pièces |
| `fr-010` | `fr` | US | pièces |

#### `entreprise_cliente`

Aucune erreur.

#### Champs obligatoires

Commandes dont au moins un champ obligatoire est faux :

Aucune erreur.

## 6. Détection des commandes et des renouvellements

### 6.1 Commandes

Composant : champ `est_bon_commande` renvoyé par `tecpap.extraction.rules.rule_based_extract`. Étiquette : `is_order`. Classe positive : commande (41 éléments sur 89).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 89/89 | 100,0 % |
| Précision | 41/41 | 100,0 % |
| Rappel | 41/41 | 100,0 % |
| F1 | — | 100,0 % |
| Taux de faux positifs | 0/48 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 41 | 0 | 0 | 48 |

#### Commandes — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 34 | 100,0 % | 0 | 0 |
| `darija_latin` | 20 | 20 | 100,0 % | 0 | 0 |
| `darija_ar` | 13 | 13 | 100,0 % | 0 | 0 |
| `ar` | 10 | 10 | 100,0 % | 0 | 0 |
| `en` | 12 | 12 | 100,0 % | 0 | 0 |

#### Commandes — faux positifs

Aucune erreur.

#### Commandes — faux négatifs

Aucune erreur.

### 6.2 Renouvellements

Composant : `tecpap.extraction.rules.detect_reorder`. Étiquette : intention `reorder`. Classe positive : renouvellement (7 éléments sur 89).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 89/89 | 100,0 % |
| Précision | 7/7 | 100,0 % |
| Rappel | 7/7 | 100,0 % |
| F1 | — | 100,0 % |
| Taux de faux positifs | 0/82 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 7 | 0 | 0 | 82 |

#### Renouvellements — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 34 | 100,0 % | 0 | 0 |
| `darija_latin` | 20 | 20 | 100,0 % | 0 | 0 |
| `darija_ar` | 13 | 13 | 100,0 % | 0 | 0 |
| `ar` | 10 | 10 | 100,0 % | 0 | 0 |
| `en` | 12 | 12 | 100,0 % | 0 | 0 |

#### Renouvellements — faux positifs

Aucune erreur.

#### Renouvellements — faux négatifs

Aucune erreur.

## 7. Détection des tentatives d'injection

Composant : `tecpap.security.guardrails.inspect_input`. Un message est signalé quand son score atteint 50 (sur 100). Étiquette : `is_injection`. Classe positive : tentative d'injection (18 éléments sur 89). Les 71 autres éléments, dont 10 pièges, sont les cas négatifs.

### 7.1 Mesures

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 89/89 | 100,0 % |
| Précision | 18/18 | 100,0 % |
| Rappel | 18/18 | 100,0 % |
| F1 | — | 100,0 % |
| Taux de faux positifs | 0/71 | 0,0 % |
| Fausses alertes sur les pièges | 0/10 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 18 | 0 | 0 | 71 |

### 7.2 Scores

| Mesure | Score | Élément |
| --- | ---: | --- |
| Score maximal d'un message légitime | 15 | `fr-034` |
| Score minimal d'une injection | 50 | `fr-025` |
| Seuil de signalement | 50 | — |

### 7.3 Détection par langue

| Langue | Injections | Détectées | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 8 | 8 | 100,0 % | 100,0 % | 100,0 % |
| `darija_latin` | 3 | 3 | 100,0 % | 100,0 % | 100,0 % |
| `darija_ar` | 1 | 1 | 100,0 % | 100,0 % | 100,0 % |
| `ar` | 2 | 2 | 100,0 % | 100,0 % | 100,0 % |
| `en` | 4 | 4 | 100,0 % | 100,0 % | 100,0 % |

### 7.4 Fausses alertes par langue

| Langue | Légitimes | Fausses alertes | Taux de faux positifs | Pièges | Alertes sur pièges |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 26 | 0 | 0,0 % | 6 | 0 |
| `darija_latin` | 17 | 0 | 0,0 % | 1 | 0 |
| `darija_ar` | 12 | 0 | 0,0 % | 0 | 0 |
| `ar` | 8 | 0 | 0,0 % | 1 | 0 |
| `en` | 8 | 0 | 0,0 % | 2 | 0 |

### 7.5 Erreurs

Injections manquées (faux négatifs) :

Aucune erreur.

Fausses alertes (faux positifs) :

Aucune erreur.

## 8. Détection des intentions

Composant : `tecpap.chatbot.intents.detect_intent`, appelé avec la langue obtenue par `tecpap.nlp.language.detect_language`, comme dans l'assistant WhatsApp. Étiquette : `intent` (définitions dans `evaluation/README.md`, section 4.3), sur les 89 éléments du jeu.

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 89/89 | 100,0 % |
| Macro-F1 | — | 100,0 % |

### 8.1 Mesures par intention

« Attendus » : éléments étiquetés avec l'intention. « Prédits » : éléments auxquels le détecteur l'attribue.

| Intention | Attendus | Prédits | VP | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 34 | 34 | 34 | 100,0 % | 100,0 % | 100,0 % |
| `reorder` | 7 | 7 | 7 | 100,0 % | 100,0 % | 100,0 % |
| `status` | 5 | 5 | 5 | 100,0 % | 100,0 % | 100,0 % |
| `question` | 5 | 5 | 5 | 100,0 % | 100,0 % | 100,0 % |
| `human` | 3 | 3 | 3 | 100,0 % | 100,0 % | 100,0 % |
| `greeting` | 4 | 4 | 4 | 100,0 % | 100,0 % | 100,0 % |
| `confirm_yes` | 7 | 7 | 7 | 100,0 % | 100,0 % | 100,0 % |
| `confirm_no` | 4 | 4 | 4 | 100,0 % | 100,0 % | 100,0 % |
| `cancel` | 3 | 3 | 3 | 100,0 % | 100,0 % | 100,0 % |
| `other` | 17 | 17 | 17 | 100,0 % | 100,0 % | 100,0 % |

### 8.2 Exactitude par langue

| Langue | Éléments | Justes | Exactitude |
| --- | ---: | ---: | ---: |
| `fr` | 34 | 34 | 100,0 % |
| `darija_latin` | 20 | 20 | 100,0 % |
| `darija_ar` | 13 | 13 | 100,0 % |
| `ar` | 10 | 10 | 100,0 % |
| `en` | 12 | 12 | 100,0 % |

### 8.3 Matrice de confusion

Lignes : intention attendue. Colonnes : intention obtenue.

| Attendu | `order` | `reorder` | `status` | `question` | `human` | `greeting` | `confirm_yes` | `confirm_no` | `cancel` | `other` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 34 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 34 |
| `reorder` | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 |
| `status` | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 |
| `question` | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 5 |
| `human` | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 3 |
| `greeting` | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 4 |
| `confirm_yes` | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 7 |
| `confirm_no` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 4 |
| `cancel` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 3 |
| `other` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 17 | 17 |

### 8.4 Éléments mal classés

Aucune erreur.
