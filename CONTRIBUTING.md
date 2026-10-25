# Guide de contribution

Ce guide s'adresse aux personnes qui modifient le code, la base de connaissances ou le jeu
d'évaluation de TECPAP. Il décrit l'installation du poste de développement, les vérifications à
passer avant une demande de fusion, les conventions du projet et le protocole d'évaluation.

Documents liés : [README](README.md) (présentation du projet), [architecture](docs/architecture.md),
[déploiement](docs/deploiement.md), [sécurité](docs/securite.md), [journal des
modifications](CHANGELOG.md), [évaluation](evaluation/README.md), [guide de rédaction de la base de
connaissances](knowledge/_guide_redaction.md).

Une faille de sécurité ne se signale pas dans un ticket public : suivre [SECURITY.md](SECURITY.md).

## 1. Poste de développement

Prérequis : Python 3.11 ou plus récent (l'intégration continue teste les versions 3.11, 3.12 et
3.13) et Git.

```bash
python -m venv .venv
source .venv/bin/activate          # Git Bash sous Windows : source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
```

Sous PowerShell, l'activation se fait avec `.venv\Scripts\Activate.ps1`.

`requirements-dev.txt` contient les dépendances d'exécution et les outils de développement (pytest,
ruff, bandit, pip-audit) à leurs versions exactes : une seule installation suffit, et chaque poste
ainsi que l'intégration continue utilisent les mêmes versions.

Lancement de l'application :

```bash
python scripts/hash_password.py     # produit la ligne ADMIN_PASSWORD_HASH à copier dans .env
python app.py                       # http://127.0.0.1:5000
```

En développement, `SECRET_KEY` est facultative (une clé aléatoire est tirée à chaque démarrage). Les
clés d'API et les identifiants des services externes (OpenAI, Twilio, Gmail) restent vides tant qu'on
n'en a pas besoin : un service dont la clé est vide est désactivé, et l'extraction se fait alors par des
règles fixes. Le fichier `.env` est exclu du dépôt ; ne jamais le publier.

### Modifier les dépendances

Les versions acceptées sont déclarées dans `pyproject.toml`. Les versions exactes sont figées dans
`requirements.txt` (exécution, installé dans l'image Docker) puis dans `requirements-dev.txt`
(développement, qui reprend les versions du premier). La commande qui produit chaque fichier est
écrite en tête de ce fichier (outil `uv`). Pour changer une version : modifier `pyproject.toml`,
relancer les deux commandes dans cet ordre, puis la suite de tests et le contrôle `pip-audit`
(section 2).

## 2. Vérifications avant une demande de fusion

| Vérification | Commande | Bloque la fusion |
|---|---|---|
| Analyse statique | `ruff check .` | Oui |
| Tests | `pytest -q` | Oui |
| Couverture | `pytest --cov=tecpap --cov-report=term-missing` | Non (rapport) |
| Analyse du code | `bandit -r tecpap app.py scripts --severity-level medium` | Non (rapport examiné) |
| Vulnérabilités des dépendances | `pip-audit -r requirements.txt --no-deps --disable-pip` | Oui |

### Tests

```bash
pytest -q                          # toute la suite
pytest tests/test_rules.py -q      # un fichier
pytest -k "reorder" -q             # tests dont le nom contient « reorder »
```

La suite complète compte 7 042 tests ; la dernière exécution complète, sous Python 3.11 sur Windows,
les a tous réussis en 6 min 14 s. La durée vient du nombre de tests : le plus long dure quelques
secondes, et aucun marqueur ne permet d'écourter la suite (`--strict-markers` refuse tout marqueur non
déclaré). Un test qui dépasse 120 secondes affiche la pile d'appels de tous les fils d'exécution
(`faulthandler_timeout`), pour trouver la cause d'un blocage.

Règles pour écrire un test :

- **Hors ligne.** Aucun test n'appelle le réseau. Les modèles de langue sont remplacés par
  `FakeLLMClient` (`tests/fakes.py`) : chaque méthode renvoie la réponse suivante de sa file
  (`queue("chat_json", {...})`), `calls_to("chat_json")` donne les arguments reçus, et
  `unavailable_llm()` imite l'absence de clé d'API. Les services Twilio et la boîte Gmail sont
  remplacés de la même façon.
- **Configuration isolée.** `Settings.for_tests(tmp_path)` place tous les chemins dans le dossier
  temporaire du test, désactive la connexion, la protection CSRF, la vérification de la signature
  Twilio et le planificateur, et ne lit ni `.env` ni l'environnement du poste. Les fixtures de
  `tests/conftest.py` (`flask_app`, `flask_client`, `secure_client`, `logged_in_client`, `temp_db`…)
  s'appuient dessus.
- **Aucun secret.** Un test n'embarque ni clé d'API, ni jeton, ni mot de passe réel. Les mots de passe
  de test sont des valeurs fictives.
- **Données fictives.** Noms, numéros de téléphone et adresses email des tests sont inventés : jamais
  de donnée d'un client réel, dans les tests comme dans les jeux de données et les documents.
- Un comportement ajouté ou changé s'accompagne d'un test ; la réparation d'un défaut, d'un test qui
  échouait avant elle.
- Les tests ne mesurent rien sur le fichier du jeu de test de l'évaluation (section 5).

### Analyse statique

`ruff check .` applique les règles E, F, W, I, B et UP (style PEP 8, erreurs probables, ordre des
imports, syntaxe moderne), avec des lignes de 120 caractères au plus et Python 3.11 comme cible. La
version de ruff est figée. `ruff check . --fix` réécrit automatiquement l'ordre des imports et les
écritures obsolètes ; relire le résultat.

## 3. Conventions du projet

### Langue

- Commentaires, docstrings (descriptions des modules, classes et fonctions), messages de journal,
  messages d'erreur et textes de l'interface sont écrits en **français**, avec les accents.
- Les noms de fonctions, de classes et de variables sont en anglais. Les noms de champs métier et de
  colonnes restent en français, comme dans la base : `numero_commande`, `entreprise_cliente`,
  `date_livraison`.
- Le vocabulaire est celui du projet : « modèle de langue » (LLM, Large Language Model, à la première
  occurrence), « assistant WhatsApp », « équipe commerciale », « tableau de bord », « base de
  connaissances », « injection d'instructions », « consignes système », « jeton témoin », « filtre de
  sortie », « renouvellement », « transfert à un conseiller ». On évite les anglicismes que le français
  remplace sans perte (« analyseur » plutôt que « parseur »).
- Un terme technique est défini à sa première apparition dans un document ou un module.

### Structure

Le paquet `tecpap` est découpé par responsabilité : `channels` (email, WhatsApp), `processing`
(médias, traitement des emails), `extraction` (modèle de langue, schéma, règles), `chatbot`
(assistant), `rag` (recherche), `security` (garde-fous), `nlp` (langue), `llm` (client et choix du
modèle), `db`, `reporting` et `web`. Les constantes métier (produits, papiers, expressions de
renouvellement, mots-clés par langue) sont dans `tecpap/catalog.py`, une seule fois.

### Journalisation

- Chaque module déclare `logger = logging.getLogger(__name__)` ; seuls les points d'entrée
  (`app.py`, `scripts/`) appellent `configure_logging`. Pas de `print` dans le paquet.
- Le message utilise le formatage différé (`logger.info("%d extraits", n)`) et le niveau adapté ;
  `logger.exception` journalise l'erreur avec sa pile d'appels.
- Un numéro de téléphone, une adresse email ou un texte de client ne sont pas journalisés tels quels :
  `tecpap.security.redaction.mask_sender` masque l'expéditeur.
- Le client reçoit un message d'erreur générique ; le détail reste dans le journal du serveur.

### Configuration

Toute la configuration est lue une fois par `tecpap/config.py` (classe `Settings`). Aucun autre module
ne lit `os.environ`. Pour ajouter un réglage :

1. ajouter le champ à `Settings`, avec sa valeur par défaut et ses contrôles ;
2. l'inscrire dans la table `_ENV_SPEC` de `tecpap/config.py` ;
3. le documenter dans `.env.example` (rôle, valeur par défaut entre parenthèses, valeurs acceptées) ;
4. ajouter un test dans `tests/test_config.py` ;
5. le citer dans [docs/deploiement.md](docs/deploiement.md) s'il concerne l'exploitation.

### Sécurité et données

- **Aucun secret dans le dépôt** : clés, jetons, mots de passe et fichier `.env` n'y figurent jamais,
  pas plus que les bases SQLite, les sauvegardes, les exports et les médias reçus (`.gitignore`
  les exclut ; ne pas contourner par `git add -f`).
- **L'autorisation est appliquée dans le code, jamais confiée au modèle de langue.** Les outils de
  l'assistant sont liés au numéro de l'expéditeur, le modèle n'a pas d'accès libre à la base, et une
  requête SQL est toujours paramétrée. Un nom de colonne ou de statut n'est jamais pris dans une
  requête : on passe par les listes blanches.
- Tout contenu externe (message, email, pièce jointe, extrait de la base) est présenté au modèle dans
  un bloc délimité (`tecpap.security.guardrails.wrap_untrusted`) et traité comme une donnée.
- Tout texte généré destiné à un client passe par le filtre de sortie avant l'envoi.
- Une commande légitime n'est jamais bloquée par un contrôle de sécurité : un contenu suspect est
  signalé à l'équipe commerciale, qui décide.

### Rédaction de la documentation

- Utiliser le nom standard des méthodes (Okapi BM25, fusion par rangs réciproques, intervalle de
  score de Wilson…) ; ne pas créer de terme maison.
- Les chiffres cités viennent d'une sortie exécutée (tests, évaluation, mesure), jamais de la mémoire.
  Après une modification qui change une sortie, la ré-exécuter puis relire les textes qui la citent.
- Dans un tableau : phrases courtes, une information par case, un style homogène.
- Une liste numérotée suit l'ordre d'exécution, et les renvois suivent la même numérotation.
- Un document décrit son sujet, pas sa propre rédaction : aucune formule qui évoque les étapes
  d'écriture, les consignes reçues ou une fonctionnalité future.
- Le résultat de l'évaluation hors ligne mesure la couche déterministe (règles, langue, intentions,
  détecteur d'injection), pas le modèle de langue : toujours le préciser.

## 4. Ajouter un document à la base de connaissances

Le guide complet, destiné à l'équipe commerciale, est dans
[`knowledge/_guide_redaction.md`](knowledge/_guide_redaction.md). En résumé :

1. Créer un fichier `knowledge/<sujet>.md` encodé en UTF-8. Un nom qui commence par `_` n'est pas
   indexé.
2. Commencer par un en-tête YAML (`title`, `categorie`, `langue`, `mots_cles`). Les mots-clés
   reprennent les mots des clients en français, en darija (lettres latines et lettres arabes) et en
   arabe.
3. Écrire un titre `#` puis des sections `##` : une section traite un seul sujet, se comprend seule et
   compte moins de 900 caractères environ, car elle est l'unité de recherche.
4. N'écrire que des faits approuvés par TECPAP : ni prix, ni délai, ni quantité minimale, ni condition
   de paiement tant que la direction ne les a pas validés. Aucune donnée de client, aucun téléphone ni
   email personnel, aucune phrase adressée à l'assistant. Toute phrase peut être citée à un client.
5. Actualiser l'index de recherche :

```bash
python scripts/build_knowledge_index.py          # extraits modifiés seulement
python scripts/build_knowledge_index.py --force  # recalcul de tous les vecteurs
```

   ou, depuis le tableau de bord, bouton « Réindexer » de la page « Base de connaissances ». Dans le
   conteneur Docker, voir [docs/deploiement.md](docs/deploiement.md#8-base-de-connaissances-de-lassistant).
6. Vérifier la recherche et le seuil de pertinence. Ajouter des questions annotées à
   `tests/data/rag_queries.jsonl` (`id`, `language`, `query`, `answerable`, `category`,
   `expected_docs`), puis :

```bash
python scripts/build_knowledge_index.py --local --evaluate tests/data/rag_queries.jsonl
```

   Le bilan donne la séparation entre les questions auxquelles la base répond et les autres. Il choisit
   un seuil sur une moitié des questions et le mesure sur l'autre : ne pas ajuster `RAG_MIN_SCORE` sur
   cette seconde moitié.
7. Lancer `pytest tests/test_rag_documents.py tests/test_rag_retriever.py tests/test_rag_answer.py
   tests/test_dashboard_knowledge.py -q`.

## 5. Protocole d'évaluation

Le détail complet est dans [evaluation/README.md](evaluation/README.md). Les règles qui s'imposent à
toute contribution :

| Jeu | Fichiers | Usage |
|---|---|---|
| Développement (198 éléments) | `evaluation/dataset/messages.jsonl` et `evaluation/dataset/messages_dev_complement.jsonl` | Écrire et ajuster les règles, les mots-clés, les seuils et les consignes |
| Test tenu à l'écart | `evaluation/dataset/messages_test.jsonl` | Mesurer la généralisation, jamais ajuster |

Règles :

1. **Ne jamais ajuster un composant à partir du jeu de test** : ni règle, ni mot-clé, ni seuil, ni
   consigne au modèle, ni exemple d'extraction ne s'écrit à partir des textes de
   `messages_test.jsonl` ni de la liste de ses erreurs. Ne pas ouvrir ce fichier pour s'en inspirer.
   Le rapport du jeu de test ne contient d'ailleurs que des chiffres agrégés.
2. **Travailler sur le jeu de développement** : `python evaluation/run_eval.py --split dev` donne les
   mesures et la liste des éléments mal classés.
3. **Publier les deux résultats tels quels**, côte à côte : `python evaluation/run_eval.py` évalue
   les deux jeux et écrit `evaluation/results/synthese.md`, avec les intervalles de confiance à 95 %
   (intervalle de score de Wilson). Un score parfait sur quelques dizaines d'éléments ne prouve pas
   l'absence d'erreur.
4. **Si le jeu de test a servi à modifier un composant**, il ne mesure plus la généralisation : ses
   éléments rejoignent le jeu de développement, et un autre jeu tenu à l'écart doit être rédigé selon
   la section 9.3 de `evaluation/README.md` (sans consulter le jeu de développement, le code, les tests
   ni les rapports ; étiquettes fixées avant toute exécution ; autre préfixe d'identifiant et autre
   plage de numéros de téléphone).
5. **Relancer l'évaluation avant de citer un chiffre**, après toute modification d'un composant mesuré
   ou d'un fichier du jeu, puis vérifier que les empreintes SHA-256 du rapport sont celles des fichiers.
6. **Dire ce qui est mesuré** : l'évaluation hors ligne mesure la couche déterministe (langue,
   intentions, extraction par règles, détection des injections), pas le modèle de langue. Le mode
   `--online` (extraction complète, clé `OPENAI_API_KEY` nécessaire, coût et résultats variables)
   n'est exécuté ni par l'intégration continue ni par défaut.
7. **Données fictives** : le jeu ne contient que des messages inventés.

## 6. Branches, commits et demandes de fusion

### Branches

La branche `main` reçoit les versions publiées. Le travail se fait sur une branche dédiée, nommée
`feature/<sujet>` (par exemple `feature/rag-chatbot`) : l'intégration continue s'exécute à chaque envoi
sur `main`, `develop` et `feature/**`, et sur les demandes de fusion vers `main` et `develop`.

### Commits

- Un commit regroupe un changement cohérent et laisse la suite de tests passer.
- Le titre est une phrase courte à l'impératif, comme dans l'historique du dépôt
  (« Add the knowledge base used by the WhatsApp assistant »), de 72 caractères au plus.
- Le corps explique ce que fait le changement et pourquoi, par thèmes si nécessaire.
- Aucun secret ni donnée de client dans un commit. Un secret publié par erreur est considéré comme
  compromis : le révoquer, puis demander la réécriture de l'historique.

### Demande de fusion

La description indique : l'objet du changement, la raison, les tests ajoutés, l'évaluation relancée si
un composant mesuré a changé, et les points à relire avec attention. Liste de contrôle :

- [ ] `ruff check .` ne signale rien
- [ ] `pytest -q` réussit
- [ ] aucun secret, aucune donnée de client, aucun fichier de données ajouté
- [ ] `.env.example` et la documentation décrivent tout réglage ajouté
- [ ] `CHANGELOG.md` décrit le changement sous « Non publié » (voir la section suivante)
- [ ] le jeu de test de l'évaluation n'a pas servi à régler un composant

### Journal des modifications et versions

Les changements visibles de l'extérieur s'ajoutent à `CHANGELOG.md` (format Keep a Changelog) sous un
titre « Non publié », rangés dans « Ajouté », « Modifié », « Sécurité » ou « Supprimé ». On y décrit
les fonctionnalités, pas la manière dont elles ont été écrites.

Pour publier une version : numéro identique dans `tecpap/__init__.py` et `pyproject.toml`, entrée
datée dans `CHANGELOG.md`, puis étiquette Git `v<version>` (voir [docs/deploiement.md](docs/deploiement.md#11-changement-de-version)).

## 7. Intégration continue

Fichier `.github/workflows/ci.yml`, déclenché à chaque envoi sur `main`, `develop` et `feature/**`, à
chaque demande de fusion vers `main` et `develop`, et à la demande.

| Travail | Contenu | Dépend de | Bloque |
|---|---|---|---|
| Analyse statique | `ruff check .` | aucun | Oui |
| Tests | `pytest --cov=tecpap` sur Python 3.11, 3.12 et 3.13 ; rapport de couverture publié pour 3.11 | Analyse statique | Oui |
| Contrôles de sécurité | `bandit` (rapport publié, sans blocage) puis `pip-audit` sur `requirements.txt` (bloquant) | Analyse statique | `pip-audit` seulement |
| Construction | Création de l'application, `docker build`, démarrage de l'image et sonde de `/healthz` (`.github/scripts/smoke_image.sh`) | Tests, contrôles de sécurité | Oui |

Le fichier `.github/workflows/deploy.yml` (« Publication ») se déclenche sur une étiquette `v*` ou à la
demande : tests et `pip-audit`, construction de l'image, démarrage et sonde, puis publication de l'image
comme artefact du workflow (conservé 30 jours). Aucun registre d'images n'est configuré.

Les tests de l'intégration continue n'utilisent aucun secret : tous les services externes sont
simulés. Les actions GitHub sont désignées par l'empreinte de leur commit, et Dependabot propose
chaque semaine leur dernière version.
