# Synthèse de l'évaluation hors ligne : jeu de développement et jeu de test

Le corpus annoté est réparti en deux partitions (« split dev/test ») : le jeu de développement (`dev`) et le jeu de test tenu à l'écart (`test`). Les erreurs du jeu de développement servent à écrire et à ajuster les règles, les listes de mots-clés et les seuils : ses scores surestiment donc la qualité réelle des composants. Le jeu de test ne sert jamais au réglage : ses scores estiment la qualité des composants sur des messages qui n'ont pas servi à les mettre au point (`evaluation/README.md`, section 9).

Les résultats par langue et les matrices de confusion de chaque partition sont dans `rapport_hors_ligne_dev.md` et `rapport_hors_ligne_test.md`. Les éléments mal classés ne sont listés que dans `rapport_hors_ligne_dev.md`.

## 1. Partitions évaluées

| Partition | Nom | Usage | Fichiers | Éléments | Commandes | Injections | Date de l'exécution |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `dev` | jeu de développement | Réglage des composants | 2 | 198 | 98 | 39 | 2026-10-25 |
| `test` | jeu de test tenu à l'écart | Mesure seule, jamais de réglage | 1 | 134 | 63 | 28 | 2026-10-25 |

| Partition | Fichier | Éléments | Empreinte SHA-256 |
| --- | --- | ---: | --- |
| `dev` | `evaluation/dataset/messages.jsonl` | 89 | `96fa89491ab4acded5276a5a68919a4ad53d0fc1d75c66758a4e8df2655f6e60` |
| `dev` | `evaluation/dataset/messages_dev_complement.jsonl` | 109 | `1f05e48593d0e3c7265fdf18b980a7650bcb901bdd6d6ac58e96dc1b5766fcd1` |
| `test` | `evaluation/dataset/messages_test.jsonl` | 134 | `a34eb5d0a335097bfbb6edec55a43bbb48f42658bbedd73bb28e24216d8ce6f7` |

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
| Détection de la langue | Exactitude du code de langue | 198/198 | 100,0 % | [98,1 % ; 100,0 %] | 133/134 | 99,3 % | [95,9 % ; 99,9 %] | −0,7 |
| Détection de la langue | Exactitude de la langue de réponse | 198/198 | 100,0 % | [98,1 % ; 100,0 %] | 133/134 | 99,3 % | [95,9 % ; 99,9 %] | −0,7 |
| Extraction par règles | Exactitude de `type_produit` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] | 62/63 | 98,4 % | [91,5 % ; 99,7 %] | −1,6 |
| Extraction par règles | Exactitude de `quantite` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] | 60/63 | 95,2 % | [86,9 % ; 98,4 %] | −4,8 |
| Extraction par règles | Exactitude de `unite` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] | 58/63 | 92,1 % | [82,7 % ; 96,6 %] | −7,9 |
| Extraction par règles | Exactitude de `entreprise_cliente` | 98/98 | 100,0 % | [96,2 % ; 100,0 %] | 52/63 | 82,5 % | [71,4 % ; 90,0 %] | −17,5 |
| Extraction par règles | Exactitude des champs obligatoires | 98/98 | 100,0 % | [96,2 % ; 100,0 %] | 48/63 | 76,2 % | [64,4 % ; 85,0 %] | −23,8 |
| Détection des commandes | Exactitude | 198/198 | 100,0 % | [98,1 % ; 100,0 %] | 129/134 | 96,3 % | [91,6 % ; 98,4 %] | −3,7 |
| Détection des commandes | Précision | 98/98 | 100,0 % | [96,2 % ; 100,0 %] | 58/58 | 100,0 % | [93,8 % ; 100,0 %] | 0,0 |
| Détection des commandes | Rappel | 98/98 | 100,0 % | [96,2 % ; 100,0 %] | 58/63 | 92,1 % | [82,7 % ; 96,6 %] | −7,9 |
| Détection des commandes | F1 | — | 100,0 % | — | — | 95,9 % | — | −4,1 |
| Détection des renouvellements | Exactitude | 198/198 | 100,0 % | [98,1 % ; 100,0 %] | 133/134 | 99,3 % | [95,9 % ; 99,9 %] | −0,7 |
| Détection des renouvellements | Précision | 14/14 | 100,0 % | [78,5 % ; 100,0 %] | 12/12 | 100,0 % | [75,8 % ; 100,0 %] | 0,0 |
| Détection des renouvellements | Rappel | 14/14 | 100,0 % | [78,5 % ; 100,0 %] | 12/13 | 92,3 % | [66,7 % ; 98,6 %] | −7,7 |
| Détection des renouvellements | F1 | — | 100,0 % | — | — | 96,0 % | — | −4,0 |
| Détection des injections | Précision | 39/39 | 100,0 % | [91,0 % ; 100,0 %] | 23/23 | 100,0 % | [85,7 % ; 100,0 %] | 0,0 |
| Détection des injections | Rappel | 39/39 | 100,0 % | [91,0 % ; 100,0 %] | 23/28 | 82,1 % | [64,4 % ; 92,1 %] | −17,9 |
| Détection des injections | F1 | — | 100,0 % | — | — | 90,2 % | — | −9,8 |
| Détection des injections | Taux de faux positifs | 0/159 | 0,0 % | [0,0 % ; 2,4 %] | 0/106 | 0,0 % | [0,0 % ; 3,5 %] | 0,0 |
| Détection des injections | Fausses alertes sur les pièges | 0/23 | 0,0 % | [0,0 % ; 14,3 %] | 0/17 | 0,0 % | [0,0 % ; 18,4 %] | 0,0 |
| Détection des intentions | Exactitude | 198/198 | 100,0 % | [98,1 % ; 100,0 %] | 124/134 | 92,5 % | [86,8 % ; 95,9 %] | −7,5 |
| Détection des intentions | Macro-F1 | — | 100,0 % | — | — | 91,9 % | — | −8,1 |

## 4. Écarts entre les partitions

Quand les intervalles de confiance des deux partitions ne se chevauchent pas, l'écart s'explique mal par le seul hasard de l'échantillonnage. Ce critère est prudent : deux intervalles qui se chevauchent n'excluent pas un écart réel, mais ne permettent pas de l'affirmer.

Mesures dont les intervalles sont disjoints :

- Extraction par règles — Exactitude de `entreprise_cliente` (−17,5 points)
- Extraction par règles — Exactitude des champs obligatoires (−23,8 points)
- Détection des intentions — Exactitude (−7,5 points)
