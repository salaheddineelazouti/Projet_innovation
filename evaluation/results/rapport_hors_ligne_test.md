# Évaluation hors ligne de l'analyse des messages clients : jeu de test tenu à l'écart

Mesure, sans réseau ni modèle de langue, des composants déterministes qui analysent les messages clients, sur le corpus annoté décrit dans `evaluation/README.md`.

Le corpus annoté est réparti en deux partitions (« split dev/test ») : le jeu de développement (`dev`) et le jeu de test tenu à l'écart (`test`). Ce rapport porte sur le jeu de test tenu à l'écart (`test`). Il ne sert jamais au réglage des composants : ses scores estiment leur qualité sur des messages qui n'ont pas servi à les mettre au point. Ce rapport ne donne que des effectifs et des mesures agrégés : il ne cite ni identifiant, ni texte, ni étiquette attendue d'un élément du jeu, pour que sa lecture ne puisse pas guider le réglage des composants (`evaluation/README.md`, section 8.4).

| Information | Valeur |
| --- | --- |
| Date de l'exécution | 2026-10-25 |
| Partition | `test` |
| Fichiers du jeu | 1 |
| Éléments du jeu | 134 |
| Version du paquet `tecpap` | 3.0.0 |

| Fichier | Éléments | Empreinte SHA-256 |
| --- | ---: | --- |
| `evaluation/dataset/messages_test.jsonl` | 134 | `a34eb5d0a335097bfbb6edec55a43bbb48f42658bbedd73bb28e24216d8ce6f7` |

L'empreinte SHA-256 est une somme de contrôle du fichier : deux rapports qui portent la même empreinte ont été calculés sur exactement les mêmes données.

## 1. Jeu de données

Codes de langue : `fr` français, `darija_latin` darija en lettres latines, `darija_ar` darija en lettres arabes, `ar` arabe standard moderne, `en` anglais, `unknown` langue non identifiée.

| Langue | Éléments | Email | WhatsApp | Commandes | Renouvellements | Injections | Pièges |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 40 | 12 | 28 | 22 | 4 | 8 | 5 |
| `darija_latin` | 34 | 0 | 34 | 15 | 4 | 6 | 4 |
| `darija_ar` | 19 | 0 | 19 | 9 | 2 | 4 | 3 |
| `ar` | 20 | 4 | 16 | 9 | 1 | 4 | 3 |
| `en` | 21 | 6 | 15 | 8 | 2 | 6 | 2 |
| Total | 134 | 22 | 112 | 63 | 13 | 28 | 17 |

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
| Détection de la langue | Exactitude du code de langue | 133/134 | 99,3 % | [95,9 % ; 99,9 %] |
| Détection de la langue | Exactitude de la langue de réponse | 133/134 | 99,3 % | [95,9 % ; 99,9 %] |
| Extraction par règles | Exactitude de `type_produit` | 62/63 | 98,4 % | [91,5 % ; 99,7 %] |
| Extraction par règles | Exactitude de `quantite` | 60/63 | 95,2 % | [86,9 % ; 98,4 %] |
| Extraction par règles | Exactitude de `unite` | 58/63 | 92,1 % | [82,7 % ; 96,6 %] |
| Extraction par règles | Exactitude de `entreprise_cliente` | 52/63 | 82,5 % | [71,4 % ; 90,0 %] |
| Extraction par règles | Exactitude des champs obligatoires | 48/63 | 76,2 % | [64,4 % ; 85,0 %] |
| Détection des commandes | Exactitude | 129/134 | 96,3 % | [91,6 % ; 98,4 %] |
| Détection des commandes | Précision | 58/58 | 100,0 % | [93,8 % ; 100,0 %] |
| Détection des commandes | Rappel | 58/63 | 92,1 % | [82,7 % ; 96,6 %] |
| Détection des commandes | F1 | — | 95,9 % | — |
| Détection des renouvellements | Exactitude | 133/134 | 99,3 % | [95,9 % ; 99,9 %] |
| Détection des renouvellements | Précision | 12/12 | 100,0 % | [75,8 % ; 100,0 %] |
| Détection des renouvellements | Rappel | 12/13 | 92,3 % | [66,7 % ; 98,6 %] |
| Détection des renouvellements | F1 | — | 96,0 % | — |
| Détection des injections | Précision | 23/23 | 100,0 % | [85,7 % ; 100,0 %] |
| Détection des injections | Rappel | 23/28 | 82,1 % | [64,4 % ; 92,1 %] |
| Détection des injections | F1 | — | 90,2 % | — |
| Détection des injections | Taux de faux positifs | 0/106 | 0,0 % | [0,0 % ; 3,5 %] |
| Détection des injections | Fausses alertes sur les pièges | 0/17 | 0,0 % | [0,0 % ; 18,4 %] |
| Détection des intentions | Exactitude | 124/134 | 92,5 % | [86,8 % ; 95,9 %] |
| Détection des intentions | Macro-F1 | — | 91,9 % | — |

## 4. Détection de la langue

Composant : `tecpap.nlp.language.detect_language`, puis `tecpap.nlp.language.reply_language` pour la langue de réponse. Étiquettes comparées : `language` et `reply_language`, sur les 134 éléments du jeu.

### 4.1 Exactitude par langue

| Langue | Éléments | Code juste | Exactitude du code | Réponse juste | Exactitude de la réponse |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 40 | 39 | 97,5 % | 39 | 97,5 % |
| `darija_latin` | 34 | 34 | 100,0 % | 34 | 100,0 % |
| `darija_ar` | 19 | 19 | 100,0 % | 19 | 100,0 % |
| `ar` | 20 | 20 | 100,0 % | 20 | 100,0 % |
| `en` | 21 | 21 | 100,0 % | 21 | 100,0 % |
| Total | 134 | 133 | 99,3 % | 133 | 99,3 % |

### 4.2 Matrice de confusion du code de langue

Lignes : langue attendue. Colonnes : langue obtenue.

| Attendu | `fr` | `darija_latin` | `darija_ar` | `ar` | `en` | `unknown` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 39 | 1 | 0 | 0 | 0 | 0 | 40 |
| `darija_latin` | 0 | 34 | 0 | 0 | 0 | 0 | 34 |
| `darija_ar` | 0 | 0 | 19 | 0 | 0 | 0 | 19 |
| `ar` | 0 | 0 | 0 | 20 | 0 | 0 | 20 |
| `en` | 0 | 0 | 0 | 0 | 21 | 0 | 21 |

## 5. Extraction par règles des commandes

Composant : `tecpap.extraction.rules.rule_based_extract`, appliqué aux 63 commandes du jeu (étiquette `is_order` vraie).

| Champ | Comparaison |
| --- | --- |
| `type_produit` | Égalité exacte du nom du produit du catalogue. |
| `quantite` | Égalité numérique (10000 et 10000,0 sont égales). |
| `unite` | Égalité exacte avec l'unité écrite par le client : `pièces` pour des pièces ou des unités, `kg`, `carton` ou `paquet`, `US` pour une quantité sans unité (`evaluation/README.md`, section 4.7). |
| `entreprise_cliente` | Égalité sans tenir compte de la casse, des accents, des signes diacritiques ni des espaces répétées. |
| Champs obligatoires | Les champs obligatoires d'une commande (`entreprise_cliente`, `type_produit`, `quantite`) sont tous justes. |

Une valeur absente est juste quand l'étiquette vaut `null`.

### 5.1 Exactitude par champ

| Champ | Justes | Total | Exactitude |
| --- | ---: | ---: | ---: |
| `type_produit` | 62 | 63 | 98,4 % |
| `quantite` | 60 | 63 | 95,2 % |
| `unite` | 58 | 63 | 92,1 % |
| `entreprise_cliente` | 52 | 63 | 82,5 % |
| Champs obligatoires | 48 | 63 | 76,2 % |

### 5.2 Exactitude par langue

| Langue | Commandes | `type_produit` | `quantite` | `unite` | `entreprise_cliente` | Champs obligatoires |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 22 | 22/22 | 20/22 | 20/22 | 17/22 | 15/22 |
| `darija_latin` | 15 | 14/15 | 15/15 | 15/15 | 14/15 | 13/15 |
| `darija_ar` | 9 | 9/9 | 9/9 | 9/9 | 7/9 | 7/9 |
| `ar` | 9 | 9/9 | 9/9 | 8/9 | 9/9 | 9/9 |
| `en` | 8 | 8/8 | 7/8 | 6/8 | 5/8 | 4/8 |

## 6. Détection des commandes et des renouvellements

### 6.1 Commandes

Composant : champ `est_bon_commande` renvoyé par `tecpap.extraction.rules.rule_based_extract`. Étiquette : `is_order`. Classe positive : commande (63 éléments sur 134).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 129/134 | 96,3 % |
| Précision | 58/58 | 100,0 % |
| Rappel | 58/63 | 92,1 % |
| F1 | — | 95,9 % |
| Taux de faux positifs | 0/71 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 58 | 0 | 5 | 71 |

#### Commandes — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 40 | 37 | 92,5 % | 0 | 3 |
| `darija_latin` | 34 | 33 | 97,1 % | 0 | 1 |
| `darija_ar` | 19 | 19 | 100,0 % | 0 | 0 |
| `ar` | 20 | 20 | 100,0 % | 0 | 0 |
| `en` | 21 | 20 | 95,2 % | 0 | 1 |

### 6.2 Renouvellements

Composant : `tecpap.extraction.rules.detect_reorder`. Étiquette : intention `reorder`. Classe positive : renouvellement (13 éléments sur 134).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 133/134 | 99,3 % |
| Précision | 12/12 | 100,0 % |
| Rappel | 12/13 | 92,3 % |
| F1 | — | 96,0 % |
| Taux de faux positifs | 0/121 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 12 | 0 | 1 | 121 |

#### Renouvellements — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 40 | 40 | 100,0 % | 0 | 0 |
| `darija_latin` | 34 | 33 | 97,1 % | 0 | 1 |
| `darija_ar` | 19 | 19 | 100,0 % | 0 | 0 |
| `ar` | 20 | 20 | 100,0 % | 0 | 0 |
| `en` | 21 | 21 | 100,0 % | 0 | 0 |

## 7. Détection des tentatives d'injection

Composant : `tecpap.security.guardrails.inspect_input`. Un message est signalé quand son score atteint 50 (sur 100). Étiquette : `is_injection`. Classe positive : tentative d'injection (28 éléments sur 134). Les 106 autres éléments, dont 17 pièges, sont les cas négatifs.

### 7.1 Mesures

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 129/134 | 96,3 % |
| Précision | 23/23 | 100,0 % |
| Rappel | 23/28 | 82,1 % |
| F1 | — | 90,2 % |
| Taux de faux positifs | 0/106 | 0,0 % |
| Fausses alertes sur les pièges | 0/17 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 23 | 0 | 5 | 106 |

### 7.2 Scores

| Mesure | Score |
| --- | ---: |
| Score maximal d'un message légitime | 30 |
| Score minimal d'une injection | 0 |
| Seuil de signalement | 50 |

### 7.3 Détection par langue

| Langue | Injections | Détectées | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 8 | 7 | 100,0 % | 87,5 % | 93,3 % |
| `darija_latin` | 6 | 5 | 100,0 % | 83,3 % | 90,9 % |
| `darija_ar` | 4 | 4 | 100,0 % | 100,0 % | 100,0 % |
| `ar` | 4 | 3 | 100,0 % | 75,0 % | 85,7 % |
| `en` | 6 | 4 | 100,0 % | 66,7 % | 80,0 % |

### 7.4 Fausses alertes par langue

| Langue | Légitimes | Fausses alertes | Taux de faux positifs | Pièges | Alertes sur pièges |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 32 | 0 | 0,0 % | 5 | 0 |
| `darija_latin` | 28 | 0 | 0,0 % | 4 | 0 |
| `darija_ar` | 15 | 0 | 0,0 % | 3 | 0 |
| `ar` | 16 | 0 | 0,0 % | 3 | 0 |
| `en` | 15 | 0 | 0,0 % | 2 | 0 |

## 8. Détection des intentions

Composant : `tecpap.chatbot.intents.detect_intent`, appelé avec la langue obtenue par `tecpap.nlp.language.detect_language`, comme dans l'assistant WhatsApp. Étiquette : `intent` (définitions dans `evaluation/README.md`, section 4.3), sur les 134 éléments du jeu.

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 124/134 | 92,5 % |
| Macro-F1 | — | 91,9 % |

### 8.1 Mesures par intention

« Attendus » : éléments étiquetés avec l'intention. « Prédits » : éléments auxquels le détecteur l'attribue.

| Intention | Attendus | Prédits | VP | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 50 | 48 | 47 | 97,9 % | 94,0 % | 95,9 % |
| `reorder` | 13 | 12 | 12 | 100,0 % | 92,3 % | 96,0 % |
| `status` | 7 | 8 | 7 | 87,5 % | 100,0 % | 93,3 % |
| `question` | 10 | 11 | 9 | 81,8 % | 90,0 % | 85,7 % |
| `human` | 5 | 4 | 4 | 100,0 % | 80,0 % | 88,9 % |
| `greeting` | 6 | 4 | 4 | 100,0 % | 66,7 % | 80,0 % |
| `confirm_yes` | 7 | 6 | 6 | 100,0 % | 85,7 % | 92,3 % |
| `confirm_no` | 6 | 6 | 6 | 100,0 % | 100,0 % | 100,0 % |
| `cancel` | 6 | 6 | 6 | 100,0 % | 100,0 % | 100,0 % |
| `other` | 24 | 29 | 23 | 79,3 % | 95,8 % | 86,8 % |

### 8.2 Exactitude par langue

| Langue | Éléments | Justes | Exactitude |
| --- | ---: | ---: | ---: |
| `fr` | 40 | 36 | 90,0 % |
| `darija_latin` | 34 | 32 | 94,1 % |
| `darija_ar` | 19 | 19 | 100,0 % |
| `ar` | 20 | 17 | 85,0 % |
| `en` | 21 | 20 | 95,2 % |

### 8.3 Matrice de confusion

Lignes : intention attendue. Colonnes : intention obtenue.

| Attendu | `order` | `reorder` | `status` | `question` | `human` | `greeting` | `confirm_yes` | `confirm_no` | `cancel` | `other` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 47 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 50 |
| `reorder` | 0 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 13 |
| `status` | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 |
| `question` | 1 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 10 |
| `human` | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 1 | 5 |
| `greeting` | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 2 | 6 |
| `confirm_yes` | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | 1 | 7 |
| `confirm_no` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | 6 |
| `cancel` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 6 |
| `other` | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 23 | 24 |
