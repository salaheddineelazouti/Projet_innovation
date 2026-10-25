# Synthèse de l'évaluation hors ligne : jeu de développement et jeu de test

Le corpus annoté est réparti en deux partitions (« split dev/test ») : le jeu de développement (`dev`) et le jeu de test tenu à l'écart (`test`). Les erreurs du jeu de développement servent à écrire et à ajuster les règles, les listes de mots-clés et les seuils : ses scores surestiment donc la qualité réelle des composants. Le jeu de test ne sert jamais au réglage : ses scores estiment la qualité des composants sur des messages qui n'ont pas servi à les mettre au point (`evaluation/README.md`, section 9).

Les résultats par langue, les matrices de confusion et les éléments mal classés de chaque partition sont dans `rapport_hors_ligne_dev.md` et `rapport_hors_ligne_test.md`.

## 1. Partitions évaluées

| Partition | Nom | Usage | Fichier | Éléments | Commandes | Injections | Date de l'exécution | Empreinte SHA-256 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `dev` | jeu de développement | Réglage des composants | `evaluation/dataset/messages.jsonl` | 89 | 41 | 18 | 2026-10-25 | `96fa89491ab4acded5276a5a68919a4ad53d0fc1d75c66758a4e8df2655f6e60` |
| `test` | jeu de test tenu à l'écart | Mesure seule, jamais de réglage | `evaluation/dataset/messages_test.jsonl` | 109 | 57 | 21 | 2026-10-25 | `16d47e68bfa48fd96e7fabe1b75c0e83e090ac320f46eaff658ea9f897f7857e` |

L'empreinte SHA-256 est une somme de contrôle du fichier : deux rapports qui portent la même empreinte ont été calculés sur exactement les mêmes données.

## 2. Mesures employées

| Mesure | Définition |
| --- | --- |
| **Exactitude** | Part des éléments dont la valeur obtenue est égale à l'étiquette attendue. |
| **Intervalle de confiance à 95 %** | Intervalle de score de Wilson : plage de valeurs de la proportion réelle compatibles avec l'échantillon. Pour k réussites sur n éléments, avec p = k / n et z = 1,96 (quantile à 97,5 % de la loi normale), il est centré sur (p + z² / (2n)) / (1 + z² / n) et sa demi-largeur vaut z √(p (1 − p) / n + z² / (4n²)) / (1 + z² / n). Il reste compris entre 0 % et 100 % et garde une largeur non nulle quand p vaut 0 % ou 100 %. Plus l'effectif est petit, plus la plage est large. |
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
| **Écart** | Valeur du jeu de test moins valeur du jeu de développement, en points de pourcentage. Pour le taux de faux positifs et les fausses alertes sur les pièges, un écart positif signale davantage d'erreurs ; pour les autres mesures, un écart négatif. « — » : écart non calculable (mesure non définie ou non disponible). |

« Effectifs » : numérateur et dénominateur de la mesure. « n.d. » : mesure non définie (dénominateur nul). « — » : aucune valeur. Le F1 et le macro-F1 ne sont pas des proportions d'éléments : ils n'ont ni effectifs ni intervalle de confiance.

## 3. Comparaison par mesure

| Composant | Mesure | Effectifs (dev) | Valeur (dev) | Intervalle de confiance à 95 % (dev) | Effectifs (test) | Valeur (test) | Intervalle de confiance à 95 % (test) | Écart (points) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Détection de la langue | Exactitude du code de langue | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 108/109 | 99,1 % | [95,0 % ; 99,8 %] | −0,9 |
| Détection de la langue | Exactitude de la langue de réponse | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 109/109 | 100,0 % | [96,6 % ; 100,0 %] | 0,0 |
| Extraction par règles | Exactitude de `type_produit` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 50/57 | 87,7 % | [76,8 % ; 93,9 %] | −12,3 |
| Extraction par règles | Exactitude de `quantite` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 44/57 | 77,2 % | [64,8 % ; 86,2 %] | −22,8 |
| Extraction par règles | Exactitude de `unite` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 49/57 | 86,0 % | [74,7 % ; 92,7 %] | −14,0 |
| Extraction par règles | Exactitude de `entreprise_cliente` | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 33/57 | 57,9 % | [45,0 % ; 69,8 %] | −42,1 |
| Extraction par règles | Exactitude des champs obligatoires | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 26/57 | 45,6 % | [33,4 % ; 58,4 %] | −54,4 |
| Détection des commandes | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 95/109 | 87,2 % | [79,6 % ; 92,2 %] | −12,8 |
| Détection des commandes | Précision | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 43/43 | 100,0 % | [91,8 % ; 100,0 %] | 0,0 |
| Détection des commandes | Rappel | 41/41 | 100,0 % | [91,4 % ; 100,0 %] | 43/57 | 75,4 % | [62,9 % ; 84,8 %] | −24,6 |
| Détection des commandes | F1 | — | 100,0 % | — | — | 86,0 % | — | −14,0 |
| Détection des renouvellements | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 102/109 | 93,6 % | [87,3 % ; 96,9 %] | −6,4 |
| Détection des renouvellements | Précision | 7/7 | 100,0 % | [64,6 % ; 100,0 %] | 0/0 | n.d. | — | — |
| Détection des renouvellements | Rappel | 7/7 | 100,0 % | [64,6 % ; 100,0 %] | 0/7 | 0,0 % | [0,0 % ; 35,4 %] | −100,0 |
| Détection des renouvellements | F1 | — | 100,0 % | — | — | 0,0 % | — | −100,0 |
| Détection des injections | Précision | 18/18 | 100,0 % | [82,4 % ; 100,0 %] | 4/4 | 100,0 % | [51,0 % ; 100,0 %] | 0,0 |
| Détection des injections | Rappel | 18/18 | 100,0 % | [82,4 % ; 100,0 %] | 4/21 | 19,0 % | [7,7 % ; 40,0 %] | −81,0 |
| Détection des injections | F1 | — | 100,0 % | — | — | 32,0 % | — | −68,0 |
| Détection des injections | Taux de faux positifs | 0/71 | 0,0 % | [0,0 % ; 5,1 %] | 0/88 | 0,0 % | [0,0 % ; 4,2 %] | 0,0 |
| Détection des injections | Fausses alertes sur les pièges | 0/10 | 0,0 % | [0,0 % ; 27,8 %] | 0/13 | 0,0 % | [0,0 % ; 22,8 %] | 0,0 |
| Détection des intentions | Exactitude | 89/89 | 100,0 % | [95,9 % ; 100,0 %] | 82/109 | 75,2 % | [66,4 % ; 82,4 %] | −24,8 |
| Détection des intentions | Macro-F1 | — | 100,0 % | — | — | 65,9 % | — | −34,1 |

## 4. Écarts entre les partitions

Quand les intervalles de confiance des deux partitions ne se chevauchent pas, l'écart s'explique mal par le seul hasard de l'échantillonnage. Ce critère est prudent : deux intervalles qui se chevauchent n'excluent pas un écart réel, mais ne permettent pas de l'affirmer.

Mesures dont les intervalles sont disjoints :

- Extraction par règles — Exactitude de `quantite` (−22,8 points)
- Extraction par règles — Exactitude de `entreprise_cliente` (−42,1 points)
- Extraction par règles — Exactitude des champs obligatoires (−54,4 points)
- Détection des commandes — Exactitude (−12,8 points)
- Détection des commandes — Rappel (−24,6 points)
- Détection des renouvellements — Rappel (−100,0 points)
- Détection des injections — Rappel (−81,0 points)
- Détection des intentions — Exactitude (−24,8 points)
