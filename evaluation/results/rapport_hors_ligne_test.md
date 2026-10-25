# Évaluation hors ligne de l'analyse des messages clients : jeu de test tenu à l'écart

Mesure, sans réseau ni modèle de langue, des composants déterministes qui analysent les messages clients, sur le corpus annoté décrit dans `evaluation/README.md`.

Le corpus annoté est réparti en deux partitions (« split dev/test ») : le jeu de développement (`dev`) et le jeu de test tenu à l'écart (`test`). Ce rapport porte sur le jeu de test tenu à l'écart (`test`). Il ne sert jamais au réglage des composants : ses scores estiment leur qualité sur des messages qui n'ont pas servi à les mettre au point.

| Information | Valeur |
| --- | --- |
| Date de l'exécution | 2026-10-25 |
| Partition | `test` |
| Jeu de données | `evaluation/dataset/messages_test.jsonl` |
| Empreinte SHA-256 du fichier | `16d47e68bfa48fd96e7fabe1b75c0e83e090ac320f46eaff658ea9f897f7857e` |
| Éléments du jeu | 109 |
| Version du paquet `tecpap` | 3.0.0 |

L'empreinte SHA-256 est une somme de contrôle du fichier : deux rapports qui portent la même empreinte ont été calculés sur exactement les mêmes données.

## 1. Jeu de données

Codes de langue : `fr` français, `darija_latin` darija en lettres latines, `darija_ar` darija en lettres arabes, `ar` arabe standard moderne, `en` anglais, `unknown` langue non identifiée.

| Langue | Éléments | Email | WhatsApp | Commandes | Renouvellements | Injections | Pièges |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 11 | 23 | 22 | 2 | 7 | 5 |
| `darija_latin` | 26 | 0 | 26 | 15 | 2 | 4 | 3 |
| `darija_ar` | 17 | 0 | 17 | 7 | 1 | 3 | 1 |
| `ar` | 16 | 2 | 14 | 7 | 1 | 3 | 2 |
| `en` | 16 | 4 | 12 | 6 | 1 | 4 | 2 |
| Total | 109 | 17 | 92 | 57 | 7 | 21 | 13 |

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
| Détection de la langue | Exactitude du code de langue | 108/109 | 99,1 % | [95,0 % ; 99,8 %] |
| Détection de la langue | Exactitude de la langue de réponse | 109/109 | 100,0 % | [96,6 % ; 100,0 %] |
| Extraction par règles | Exactitude de `type_produit` | 50/57 | 87,7 % | [76,8 % ; 93,9 %] |
| Extraction par règles | Exactitude de `quantite` | 44/57 | 77,2 % | [64,8 % ; 86,2 %] |
| Extraction par règles | Exactitude de `unite` | 49/57 | 86,0 % | [74,7 % ; 92,7 %] |
| Extraction par règles | Exactitude de `entreprise_cliente` | 33/57 | 57,9 % | [45,0 % ; 69,8 %] |
| Extraction par règles | Exactitude des champs obligatoires | 26/57 | 45,6 % | [33,4 % ; 58,4 %] |
| Détection des commandes | Exactitude | 95/109 | 87,2 % | [79,6 % ; 92,2 %] |
| Détection des commandes | Précision | 43/43 | 100,0 % | [91,8 % ; 100,0 %] |
| Détection des commandes | Rappel | 43/57 | 75,4 % | [62,9 % ; 84,8 %] |
| Détection des commandes | F1 | — | 86,0 % | — |
| Détection des renouvellements | Exactitude | 102/109 | 93,6 % | [87,3 % ; 96,9 %] |
| Détection des renouvellements | Précision | 0/0 | n.d. | — |
| Détection des renouvellements | Rappel | 0/7 | 0,0 % | [0,0 % ; 35,4 %] |
| Détection des renouvellements | F1 | — | 0,0 % | — |
| Détection des injections | Précision | 4/4 | 100,0 % | [51,0 % ; 100,0 %] |
| Détection des injections | Rappel | 4/21 | 19,0 % | [7,7 % ; 40,0 %] |
| Détection des injections | F1 | — | 32,0 % | — |
| Détection des injections | Taux de faux positifs | 0/88 | 0,0 % | [0,0 % ; 4,2 %] |
| Détection des injections | Fausses alertes sur les pièges | 0/13 | 0,0 % | [0,0 % ; 22,8 %] |
| Détection des intentions | Exactitude | 82/109 | 75,2 % | [66,4 % ; 82,4 %] |
| Détection des intentions | Macro-F1 | — | 65,9 % | — |

## 4. Détection de la langue

Composant : `tecpap.nlp.language.detect_language`, puis `tecpap.nlp.language.reply_language` pour la langue de réponse. Étiquettes comparées : `language` et `reply_language`, sur les 109 éléments du jeu.

### 4.1 Exactitude par langue

| Langue | Éléments | Code juste | Exactitude du code | Réponse juste | Exactitude de la réponse |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 34 | 100,0 % | 34 | 100,0 % |
| `darija_latin` | 26 | 26 | 100,0 % | 26 | 100,0 % |
| `darija_ar` | 17 | 17 | 100,0 % | 17 | 100,0 % |
| `ar` | 16 | 16 | 100,0 % | 16 | 100,0 % |
| `en` | 16 | 15 | 93,8 % | 16 | 100,0 % |
| Total | 109 | 108 | 99,1 % | 109 | 100,0 % |

### 4.2 Matrice de confusion du code de langue

Lignes : langue attendue. Colonnes : langue obtenue.

| Attendu | `fr` | `darija_latin` | `darija_ar` | `ar` | `en` | `unknown` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 0 | 0 | 0 | 0 | 0 | 34 |
| `darija_latin` | 0 | 26 | 0 | 0 | 0 | 0 | 26 |
| `darija_ar` | 0 | 0 | 17 | 0 | 0 | 0 | 17 |
| `ar` | 0 | 0 | 0 | 16 | 0 | 0 | 16 |
| `en` | 0 | 0 | 0 | 0 | 15 | 1 | 16 |

### 4.3 Éléments mal classés

Code de langue :

| Élément | Attendu | Obtenu | Confiance |
| --- | --- | --- | --- |
| `test-en-008` | `en` | `unknown` | 0 |

Langue de réponse :

Aucune erreur.

## 5. Extraction par règles des commandes

Composant : `tecpap.extraction.rules.rule_based_extract`, appliqué aux 57 commandes du jeu (étiquette `is_order` vraie).

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
| `type_produit` | 50 | 57 | 87,7 % |
| `quantite` | 44 | 57 | 77,2 % |
| `unite` | 49 | 57 | 86,0 % |
| `entreprise_cliente` | 33 | 57 | 57,9 % |
| Champs obligatoires | 26 | 57 | 45,6 % |

### 5.2 Exactitude par langue

| Langue | Commandes | `type_produit` | `quantite` | `unite` | `entreprise_cliente` | Champs obligatoires |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fr` | 22 | 20/22 | 18/22 | 19/22 | 15/22 | 12/22 |
| `darija_latin` | 15 | 14/15 | 11/15 | 12/15 | 8/15 | 7/15 |
| `darija_ar` | 7 | 5/7 | 4/7 | 6/7 | 2/7 | 1/7 |
| `ar` | 7 | 6/7 | 6/7 | 7/7 | 6/7 | 5/7 |
| `en` | 6 | 5/6 | 5/6 | 5/6 | 2/6 | 1/6 |

### 5.3 Erreurs par champ

#### `type_produit`

| Élément | Langue | Attendu | Obtenu |
| --- | --- | --- | --- |
| `test-fr-005` | `fr` | Sac fond carré avec poignées torsadées | — |
| `test-fr-012` | `fr` | Sac fond carré sans poignées | — |
| `test-darija-lat-007` | `darija_latin` | Sac fond carré avec poignées torsadées | — |
| `test-darija-ar-002` | `darija_ar` | Sac fond carré avec poignées torsadées | — |
| `test-darija-ar-017` | `darija_ar` | Sac fond carré avec poignées plates | Sachets fond plat |
| `test-ar-003` | `ar` | Sachets fond plat | — |
| `test-en-002` | `en` | Sac fond carré avec poignées torsadées | — |

#### `quantite`

| Élément | Langue | Attendu | Obtenu |
| --- | --- | --- | --- |
| `test-fr-005` | `fr` | 800 | — |
| `test-fr-007` | `fr` | 5000 | — |
| `test-fr-011` | `fr` | 3000 | 3003 |
| `test-fr-031` | `fr` | 2500 | — |
| `test-darija-lat-001` | `darija_latin` | 3000 | 3 |
| `test-darija-lat-004` | `darija_latin` | 4000 | — |
| `test-darija-lat-007` | `darija_latin` | 600 | — |
| `test-darija-lat-008` | `darija_latin` | 2500 | 500 |
| `test-darija-ar-001` | `darija_ar` | 2000 | 2 |
| `test-darija-ar-005` | `darija_ar` | 4000 | 4 |
| `test-darija-ar-017` | `darija_ar` | 1000 | — |
| `test-ar-001` | `ar` | 1500 | 500 |
| `test-en-001` | `en` | 7500 | — |

#### `unite`

| Élément | Langue | Attendu | Obtenu |
| --- | --- | --- | --- |
| `test-fr-005` | `fr` | US | — |
| `test-fr-007` | `fr` | US | — |
| `test-fr-031` | `fr` | pièces | — |
| `test-darija-lat-002` | `darija_latin` | pièces | US |
| `test-darija-lat-004` | `darija_latin` | US | — |
| `test-darija-lat-007` | `darija_latin` | pièces | — |
| `test-darija-ar-017` | `darija_ar` | US | — |
| `test-en-001` | `en` | US | — |

#### `entreprise_cliente`

| Élément | Langue | Attendu | Obtenu |
| --- | --- | --- | --- |
| `test-fr-001` | `fr` | Boulangerie Les Épis d'Or | — |
| `test-fr-003` | `fr` | Parfumerie Ambre Noir | — |
| `test-fr-005` | `fr` | Lalla Mode | — |
| `test-fr-013` | `fr` | Épicerie fine Zitoun | — |
| `test-fr-029` | `fr` | Riad Dar Zellige | — |
| `test-fr-030` | `fr` | Coopérative Tamount | — |
| `test-fr-031` | `fr` | Superette Select | — |
| `test-darija-lat-003` | `darija_latin` | مكتبة الأمل | — |
| `test-darija-lat-004` | `darija_latin` | Mahlaba Nassim | — |
| `test-darija-lat-007` | `darija_latin` | Bazar Atlas | — |
| `test-darija-lat-008` | `darija_latin` | Boulangerie Al Wafa | Brahim |
| `test-darija-lat-010` | `darija_latin` | Hanout Si Mohamed | — |
| `test-darija-lat-011` | `darija_latin` | Patisserie Amandine | — |
| `test-darija-lat-024` | `darija_latin` | Hanout Baraka | — |
| `test-darija-ar-002` | `darija_ar` | بوتيك لمسة أناقة | — |
| `test-darija-ar-003` | `darija_ar` | صيدلية الشفاء | — |
| `test-darija-ar-005` | `darija_ar` | مطعم الريف | عبد |
| `test-darija-ar-006` | `darija_ar` | محلبة السعادة | — |
| `test-darija-ar-017` | `darija_ar` | مكتبة النور | — |
| `test-ar-003` | `ar` | مطعم الزيتونة | مدير |
| `test-en-002` | `en` | Atlas Treasures | — |
| `test-en-003` | `en` | Surf Shack | — |
| `test-en-015` | `en` | Medina Market | — |
| `test-en-016` | `en` | Green Leaf Pharmacy | — |

#### Champs obligatoires

Commandes dont au moins un champ obligatoire est faux :

`test-fr-001`, `test-fr-003`, `test-fr-005`, `test-fr-007`, `test-fr-011`, `test-fr-012`, `test-fr-013`, `test-fr-029`, `test-fr-030`, `test-fr-031`, `test-darija-lat-001`, `test-darija-lat-003`, `test-darija-lat-004`, `test-darija-lat-007`, `test-darija-lat-008`, `test-darija-lat-010`, `test-darija-lat-011`, `test-darija-lat-024`, `test-darija-ar-001`, `test-darija-ar-002`, `test-darija-ar-003`, `test-darija-ar-005`, `test-darija-ar-006`, `test-darija-ar-017`, `test-ar-001`, `test-ar-003`, `test-en-001`, `test-en-002`, `test-en-003`, `test-en-015`, `test-en-016`.

## 6. Détection des commandes et des renouvellements

### 6.1 Commandes

Composant : champ `est_bon_commande` renvoyé par `tecpap.extraction.rules.rule_based_extract`. Étiquette : `is_order`. Classe positive : commande (57 éléments sur 109).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 95/109 | 87,2 % |
| Précision | 43/43 | 100,0 % |
| Rappel | 43/57 | 75,4 % |
| F1 | — | 86,0 % |
| Taux de faux positifs | 0/52 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 43 | 0 | 14 | 52 |

#### Commandes — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 28 | 82,4 % | 0 | 6 |
| `darija_latin` | 26 | 23 | 88,5 % | 0 | 3 |
| `darija_ar` | 17 | 15 | 88,2 % | 0 | 2 |
| `ar` | 16 | 15 | 93,8 % | 0 | 1 |
| `en` | 16 | 14 | 87,5 % | 0 | 2 |

#### Commandes — faux positifs

Aucune erreur.

#### Commandes — faux négatifs

| Élément | Langue |
| --- | --- |
| `test-fr-005` | `fr` |
| `test-fr-007` | `fr` |
| `test-fr-013` | `fr` |
| `test-fr-014` | `fr` |
| `test-fr-031` | `fr` |
| `test-fr-033` | `fr` |
| `test-darija-lat-007` | `darija_latin` |
| `test-darija-lat-010` | `darija_latin` |
| `test-darija-lat-011` | `darija_latin` |
| `test-darija-ar-006` | `darija_ar` |
| `test-darija-ar-017` | `darija_ar` |
| `test-ar-004` | `ar` |
| `test-en-001` | `en` |
| `test-en-003` | `en` |

### 6.2 Renouvellements

Composant : `tecpap.extraction.rules.detect_reorder`. Étiquette : intention `reorder`. Classe positive : renouvellement (7 éléments sur 109).

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 102/109 | 93,6 % |
| Précision | 0/0 | n.d. |
| Rappel | 0/7 | 0,0 % |
| F1 | — | 0,0 % |
| Taux de faux positifs | 0/102 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 0 | 0 | 7 | 102 |

#### Renouvellements — par langue

| Langue | Éléments | Justes | Exactitude | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 34 | 32 | 94,1 % | 0 | 2 |
| `darija_latin` | 26 | 24 | 92,3 % | 0 | 2 |
| `darija_ar` | 17 | 16 | 94,1 % | 0 | 1 |
| `ar` | 16 | 15 | 93,8 % | 0 | 1 |
| `en` | 16 | 15 | 93,8 % | 0 | 1 |

#### Renouvellements — faux positifs

Aucune erreur.

#### Renouvellements — faux négatifs

| Élément | Langue |
| --- | --- |
| `test-fr-013` | `fr` |
| `test-fr-014` | `fr` |
| `test-darija-lat-010` | `darija_latin` |
| `test-darija-lat-011` | `darija_latin` |
| `test-darija-ar-006` | `darija_ar` |
| `test-ar-004` | `ar` |
| `test-en-003` | `en` |

## 7. Détection des tentatives d'injection

Composant : `tecpap.security.guardrails.inspect_input`. Un message est signalé quand son score atteint 50 (sur 100). Étiquette : `is_injection`. Classe positive : tentative d'injection (21 éléments sur 109). Les 88 autres éléments, dont 13 pièges, sont les cas négatifs.

### 7.1 Mesures

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 92/109 | 84,4 % |
| Précision | 4/4 | 100,0 % |
| Rappel | 4/21 | 19,0 % |
| F1 | — | 32,0 % |
| Taux de faux positifs | 0/88 | 0,0 % |
| Fausses alertes sur les pièges | 0/13 | 0,0 % |

| VP | FP | FN | VN |
| ---: | ---: | ---: | ---: |
| 4 | 0 | 17 | 88 |

### 7.2 Scores

| Mesure | Score | Élément |
| --- | ---: | --- |
| Score maximal d'un message légitime | 30 | `test-fr-030` |
| Score minimal d'une injection | 0 | `test-fr-026` |
| Seuil de signalement | 50 | — |

### 7.3 Détection par langue

| Langue | Injections | Détectées | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 7 | 1 | 100,0 % | 14,3 % | 25,0 % |
| `darija_latin` | 4 | 1 | 100,0 % | 25,0 % | 40,0 % |
| `darija_ar` | 3 | 0 | n.d. | 0,0 % | 0,0 % |
| `ar` | 3 | 1 | 100,0 % | 33,3 % | 50,0 % |
| `en` | 4 | 1 | 100,0 % | 25,0 % | 40,0 % |

### 7.4 Fausses alertes par langue

| Langue | Légitimes | Fausses alertes | Taux de faux positifs | Pièges | Alertes sur pièges |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fr` | 27 | 0 | 0,0 % | 5 | 0 |
| `darija_latin` | 22 | 0 | 0,0 % | 3 | 0 |
| `darija_ar` | 14 | 0 | 0,0 % | 1 | 0 |
| `ar` | 13 | 0 | 0,0 % | 2 | 0 |
| `en` | 12 | 0 | 0,0 % | 2 | 0 |

### 7.5 Erreurs

Injections manquées (faux négatifs) :

| Élément | Langue | Score |
| --- | --- | --- |
| `test-fr-023` | `fr` | 25 |
| `test-fr-024` | `fr` | 30 |
| `test-fr-025` | `fr` | 45 |
| `test-fr-026` | `fr` | 0 |
| `test-fr-028` | `fr` | 0 |
| `test-fr-029` | `fr` | 25 |
| `test-darija-lat-020` | `darija_latin` | 0 |
| `test-darija-lat-022` | `darija_latin` | 0 |
| `test-darija-lat-023` | `darija_latin` | 25 |
| `test-darija-ar-014` | `darija_ar` | 45 |
| `test-darija-ar-015` | `darija_ar` | 0 |
| `test-darija-ar-016` | `darija_ar` | 0 |
| `test-ar-013` | `ar` | 30 |
| `test-ar-014` | `ar` | 0 |
| `test-en-011` | `en` | 0 |
| `test-en-012` | `en` | 0 |
| `test-en-013` | `en` | 45 |

Fausses alertes (faux positifs) :

Aucune erreur.

## 8. Détection des intentions

Composant : `tecpap.chatbot.intents.detect_intent`, appelé avec la langue obtenue par `tecpap.nlp.language.detect_language`, comme dans l'assistant WhatsApp. Étiquette : `intent` (définitions dans `evaluation/README.md`, section 4.3), sur les 109 éléments du jeu.

| Mesure | Effectifs | Valeur |
| --- | ---: | ---: |
| Exactitude | 82/109 | 75,2 % |
| Macro-F1 | — | 65,9 % |

### 8.1 Mesures par intention

« Attendus » : éléments étiquetés avec l'intention. « Prédits » : éléments auxquels le détecteur l'attribue.

| Intention | Attendus | Prédits | VP | Précision | Rappel | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 50 | 42 | 42 | 100,0 % | 84,0 % | 91,3 % |
| `reorder` | 7 | 0 | 0 | n.d. | 0,0 % | 0,0 % |
| `status` | 5 | 4 | 4 | 100,0 % | 80,0 % | 88,9 % |
| `question` | 5 | 11 | 5 | 45,5 % | 100,0 % | 62,5 % |
| `human` | 5 | 10 | 4 | 40,0 % | 80,0 % | 53,3 % |
| `greeting` | 5 | 5 | 5 | 100,0 % | 100,0 % | 100,0 % |
| `confirm_yes` | 7 | 4 | 4 | 100,0 % | 57,1 % | 72,7 % |
| `confirm_no` | 5 | 1 | 1 | 100,0 % | 20,0 % | 33,3 % |
| `cancel` | 5 | 5 | 5 | 100,0 % | 100,0 % | 100,0 % |
| `other` | 15 | 27 | 12 | 44,4 % | 80,0 % | 57,1 % |

### 8.2 Exactitude par langue

| Langue | Éléments | Justes | Exactitude |
| --- | ---: | ---: | ---: |
| `fr` | 34 | 22 | 64,7 % |
| `darija_latin` | 26 | 20 | 76,9 % |
| `darija_ar` | 17 | 13 | 76,5 % |
| `ar` | 16 | 12 | 75,0 % |
| `en` | 16 | 15 | 93,8 % |

### 8.3 Matrice de confusion

Lignes : intention attendue. Colonnes : intention obtenue.

| Attendu | `order` | `reorder` | `status` | `question` | `human` | `greeting` | `confirm_yes` | `confirm_no` | `cancel` | `other` | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `order` | 42 | 0 | 0 | 3 | 4 | 0 | 0 | 0 | 0 | 1 | 50 |
| `reorder` | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 5 | 7 |
| `status` | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 5 |
| `question` | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 5 |
| `human` | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 1 | 5 |
| `greeting` | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 5 |
| `confirm_yes` | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 3 | 7 |
| `confirm_no` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 4 | 5 |
| `cancel` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 5 |
| `other` | 0 | 0 | 0 | 1 | 2 | 0 | 0 | 0 | 0 | 12 | 15 |

### 8.4 Éléments mal classés

| Élément | Langue | Attendu | Obtenu |
| --- | --- | --- | --- |
| `test-fr-001` | `fr` | `order` | `human` |
| `test-fr-005` | `fr` | `order` | `question` |
| `test-fr-007` | `fr` | `order` | `question` |
| `test-fr-012` | `fr` | `order` | `human` |
| `test-fr-013` | `fr` | `reorder` | `question` |
| `test-fr-014` | `fr` | `reorder` | `other` |
| `test-fr-020` | `fr` | `confirm_yes` | `other` |
| `test-fr-021` | `fr` | `confirm_no` | `other` |
| `test-fr-024` | `fr` | `order` | `human` |
| `test-fr-025` | `fr` | `other` | `human` |
| `test-fr-028` | `fr` | `other` | `question` |
| `test-fr-033` | `fr` | `order` | `question` |
| `test-darija-lat-007` | `darija_latin` | `order` | `other` |
| `test-darija-lat-010` | `darija_latin` | `reorder` | `other` |
| `test-darija-lat-011` | `darija_latin` | `reorder` | `other` |
| `test-darija-lat-017` | `darija_latin` | `confirm_yes` | `other` |
| `test-darija-lat-018` | `darija_latin` | `confirm_no` | `other` |
| `test-darija-lat-023` | `darija_latin` | `order` | `human` |
| `test-darija-ar-006` | `darija_ar` | `reorder` | `other` |
| `test-darija-ar-011` | `darija_ar` | `confirm_yes` | `other` |
| `test-darija-ar-012` | `darija_ar` | `confirm_no` | `other` |
| `test-darija-ar-016` | `darija_ar` | `other` | `human` |
| `test-ar-004` | `ar` | `reorder` | `other` |
| `test-ar-005` | `ar` | `status` | `other` |
| `test-ar-007` | `ar` | `human` | `other` |
| `test-ar-010` | `ar` | `confirm_no` | `other` |
| `test-en-003` | `en` | `reorder` | `question` |
