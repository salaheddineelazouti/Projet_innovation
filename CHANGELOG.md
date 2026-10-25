# Journal des modifications

Toutes les évolutions notables de TECPAP sont consignées dans ce fichier.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le projet applique le
[versionnage sémantique](https://semver.org/lang/fr/) : le premier nombre change quand la
compatibilité est rompue, le deuxième quand une fonctionnalité est ajoutée, le troisième pour un
changement qui n'ajoute rien.

Documents liés : [README](README.md), [déploiement](docs/deploiement.md),
[sécurité](docs/securite.md), [contribution](CONTRIBUTING.md).

## [Non publié]

## [3.0.0] - 2026-10-25

Cette version fait passer TECPAP d'un outil d'extraction de commandes à un système en quatre couches :
canaux d'entrée (email et WhatsApp), traitement multimodal, extraction par modèle de langue (LLM, Large
Language Model) encadrée par des règles déterministes, puis validation par l'équipe commerciale. Un
modèle de langue n'est jamais la seule source de vérité : les règles, les autorisations vérifiées dans
le code et la validation humaine se complètent.

### Ajouté

Assistant WhatsApp

- Assistant conversationnel sur WhatsApp, fondé sur une machine à états : il collecte les champs
  manquants d'une commande, envoie un récapitulatif et n'enregistre la commande qu'après l'accord
  explicite du client. La commande est enregistrée « en attente » de validation par l'équipe
  commerciale.
- Intentions reconnues par des règles, sans modèle de langue : commande, renouvellement, suivi,
  question, demande de conseiller, salutation, confirmation, refus et annulation, en français, en
  anglais, en arabe standard et en darija (arabe marocain écrit en lettres arabes ou en lettres
  latines avec des chiffres, dit « arabizi »).
- Réponses en français, en darija et en arabe standard, dans la langue du client.
- Suivi des commandes envoyées depuis le numéro de l'expéditeur, annulation d'une commande en cours
  de saisie et transfert à un conseiller (handoff) : l'assistant n'accuse plus que réception jusqu'à la
  fin du transfert, décidée depuis le tableau de bord.
- Outils de l'assistant liés au numéro de l'expéditeur : requêtes SQL paramétrées qui filtrent sur ce
  numéro, résultats réduits au minimum (référence, date, produit, quantité, unité, statut), sans prix
  ni coordonnées. Le modèle de langue n'a jamais d'accès libre à la base.
- Limite de messages par minute et par numéro (`WHATSAPP_RATE_LIMIT_PER_MINUTE`), appliquée avant
  le téléchargement des médias payants (transcription, lecture d'image).
- File durable des messages reçus : un message accepté n'est jamais perdu, même si le processus
  s'arrête pendant son traitement ; il est repris au démarrage suivant et par une maintenance
  périodique. Un message livré deux fois par Twilio n'est traité qu'une fois (déduplication par
  `MessageSid`).
- Réponse différée : le webhook répond aussitôt à Twilio, le traitement a lieu dans des tâches de fond
  en nombre borné et la réponse part par l'API REST de Twilio (`WHATSAPP_ASYNC_REPLY`).
- Messages de validation et de rejet envoyés au client dans la langue de la conversation.

Extraction des commandes

- Détection de la langue et de l'écriture d'un message (français, anglais, arabe standard, darija en
  lettres arabes, darija en lettres latines), par mots marqueurs, sans modèle de langue, y compris sur
  des messages très courts ou qui mêlent darija et français.
- Choix du modèle de langue selon la tâche et la langue : le modèle principal traite l'extraction et
  les langues peu dotées (darija, arabe), le modèle léger les tâches courtes en français et en anglais.
  La raison du choix est conservée avec la commande pour l'audit.
- Validation de la réponse du modèle par un schéma, puis normalisation par des règles déterministes :
  quantités écrites en lettres ou en chiffres arabo-indiens, dates, unités, devises, numéros de
  téléphone, types de produit.
- Anomalies et champs obligatoires manquants listés pour chaque commande, et confiance calculée par
  les règles (mélange de la confiance du modèle et de la complétude de la commande, avec une pénalité
  par anomalie).
- Extraction par règles quand le modèle de langue est indisponible ou que la clé d'API est absente :
  la commande arrive au tableau de bord avec tous ses champs à vérifier.
- Détection des renouvellements (« comme d'habitude », « kif dima ») : les règles décident d'abord, le
  modèle léger n'est consulté que pour un texte d'au moins 60 caractères dans lequel les règles ne
  trouvent rien. Les champs manquants sont repris dans la dernière commande validée du même
  expéditeur, jamais dans celle d'un client cité dans le message.
- Exemples de commandes validées, anonymisés et sans prix, ajoutés aux consignes d'extraction.
- Colonnes d'audit sur chaque commande : identifiant du message, message d'origine, langue, modèle,
  raison du choix du modèle, anomalies et champs manquants.
- Client unique pour les appels aux modèles de langue : sortie JSON, appels d'outils, transcription
  Whisper, lecture d'image, vecteurs de recherche, une seconde tentative en cas d'échec, aucune exception
  remontée.
- Traitement multimodal : type du média reconnu d'après son contenu, limites de taille
  (`MAX_MEDIA_BYTES`), texte des PDF extrait avec pypdf, images lues par un modèle de vision, notes
  vocales transcrites par Whisper avec détection automatique de la langue.

Base de connaissances et recherche

- Base de connaissances de cinq documents Markdown (produits, papiers et codes articles, comment
  commander, contact, questions fréquentes) et un guide de rédaction pour l'équipe commerciale.
- Réponses aux questions des clients par génération augmentée par la recherche (RAG,
  Retrieval-Augmented Generation) : recherche hybride qui fusionne un classement par mots-clés (Okapi
  BM25) et un classement par similarité de vecteurs, par fusion par rangs réciproques (RRF, Reciprocal
  Rank Fusion).
- Calcul des vecteurs en local (hors ligne, gratuit, déterministe) ou par le modèle d'embeddings
  d'OpenAI (`RAG_EMBEDDER`).
- Seuil de pertinence (`RAG_MIN_SCORE`) en dessous duquel l'assistant répond qu'il n'a pas
  l'information, sans appeler le modèle de langue.
- Questions de prix, de délai, de stock, de remise, de paiement ou de quantité minimale transmises à
  l'équipe commerciale, sans recherche ni modèle : ces informations ne figurent pas dans la base.
- Indexation incrémentale au démarrage, depuis le tableau de bord ou par
  `scripts/build_knowledge_index.py` ; mesure de la séparation des questions annotées avec
  `--evaluate`.

Garde-fous de sécurité

- Détection des tentatives d'injection d'instructions (prompt injection) par signaux pondérés en
  français, anglais, arabe et darija, avec décodage des contenus encodés (base64, hexadécimal, encodage
  d'URL), du texte masqué par des caractères invisibles ou inversé, et des variantes obfusquées
  (homoglyphes, lettres espacées, accents et signes diacritiques).
- Délimitation du contenu externe (messages, emails, pièces jointes, extraits de la base) présenté au
  modèle comme une donnée, jamais comme une instruction.
- Jeton témoin (canary token) dans les consignes système et filtre de sortie : une réponse qui reprend
  le jeton, divulgue les consignes, contient un secret ou cite un montant absent des extraits est
  écartée.
- Masquage des numéros de téléphone et des adresses email dans les journaux et dans les événements de
  sécurité.
- Journal des événements de sécurité (signature refusée, message suspect, limite de débit dépassée),
  conservé 180 jours.

Tableau de bord

- Page « Conversations » : fil de chaque conversation, file des transferts à un conseiller, réponse
  de l'équipe commerciale depuis le tableau de bord, fin du transfert.
- Page « Base de connaissances » : documents indexés, état de l'index, réindexation, recherche d'essai
  avec le seuil de pertinence.
- Page « Sécurité » : événements filtrables.
- Page de la commande : carte d'audit de l'extraction (langue, modèle, anomalies, champs manquants) et
  message d'origine.
- Connexion de l'équipe commerciale (voir « Sécurité »).
- Export vers SAGE X3 (classeur Excel), gestion des codes articles et rapport PDF détaillé.

Évaluation

- Corpus de messages clients fictifs annotés (français, anglais, arabe, darija en lettres arabes et
  latines) : jeu de développement et jeu de test tenu à l'écart, avec des tentatives d'injection et des
  messages légitimes qui emploient les mêmes mots (pièges à fausses alertes).
- `evaluation/run_eval.py` : évaluation hors ligne des composants déterministes (langue, intentions,
  extraction par règles, détection des commandes et des renouvellements, détection des injections),
  avec rapports Markdown et JSON, intervalles de confiance à 95 % (intervalle de score de Wilson) et
  synthèse qui compare les deux jeux. L'option `--online` mesure aussi l'extraction par le modèle de
  langue.

Exploitation

- Image Docker de production : gunicorn avec un seul processus et quatre fils d'exécution,
  utilisateur sans privilèges, données sous `/data` (volumes), sonde de disponibilité `/healthz`.
- Configuration centralisée dans `tecpap/config.py` et documentée dans `.env.example` ; toute valeur
  invalide arrête le démarrage avec un message en français.
- Intégration continue : analyse statique (ruff), tests sur Python 3.11, 3.12 et 3.13 avec mesure de
  couverture, analyse du code (bandit), contrôle des vulnérabilités des dépendances (pip-audit),
  construction de l'image Docker démarrée et sondée.
- Workflow de publication déclenché par une étiquette `v*` : tests, contrôle des dépendances, image
  Docker publiée comme artefact. Dependabot propose chaque semaine la dernière version des actions GitHub.
- Dépendances figées aux versions exactes (`requirements.txt`, `requirements-dev.txt`).
- Scripts : `scripts/backup.py` (sauvegarde, liste, restauration, nettoyage, export JSON),
  `scripts/process_emails.py` (traitement des emails), `scripts/hash_password.py` (empreinte du mot de
  passe administrateur), `scripts/build_knowledge_index.py` (indexation).
- Sauvegardes par l'API de sauvegarde de SQLite (copie cohérente pendant que l'application écrit),
  copie de la base avant chaque restauration, schéma d'une sauvegarde ancienne mis au niveau de la
  version courante à la restauration.

### Modifié

- Le code est organisé en paquet Python `tecpap` (base de données, canaux, extraction, assistant,
  recherche, sécurité, rapports, application web) et le catalogue des produits est regroupé dans
  `tecpap/catalog.py`.
- La confiance affichée au tableau de bord est calculée par les règles et non plus reprise telle quelle
  du modèle de langue.
- L'application web est créée par une fabrique (`create_app`) avec des blueprints Flask et un
  conteneur de services ; `app.py` est le point d'entrée.
- Une connexion SQLite par fil d'exécution, en mode WAL.
- Les dates sont enregistrées en UTC et affichées dans le fuseau `APP_TIMEZONE` (Africa/Casablanca par
  défaut).
- Le numéro WhatsApp d'un expéditeur n'est rattaché à un client existant que lorsque l'équipe
  commerciale valide la commande.
- La transcription des notes vocales détecte la langue automatiquement ; l'indice de vocabulaire
  transmis à Whisper ne contient aucun nom propre.
- Les messages sont journalisés avec le module standard `logging` (niveau `LOG_LEVEL`), avec le nom du
  fil d'exécution ; les bibliothèques Twilio et OpenAI sont limitées au niveau WARNING.
- Python 3.11 est la version minimale.

### Sécurité

- Connexion obligatoire au tableau de bord : mot de passe conservé sous forme d'empreinte (scrypt),
  blocage après des échecs répétés, sessions de 12 heures, cookie de session inaccessible à JavaScript
  et envoyé en HTTPS seulement en production. Les adresses `/api` répondent 401 sans session.
- Protection contre la falsification de requête intersite (CSRF) : toute requête qui modifie des
  données porte le jeton de la session.
- Vérification de la signature Twilio sur le webhook WhatsApp ; une requête mal signée est refusée (403)
  et consignée.
- En production, `SECRET_KEY` doit compter au moins 32 caractères et ne pas être la valeur d'exemple ;
  le mode débogage est refusé. Les valeurs d'exemple de `.env.example` laissées en place sont ignorées.
- Listes blanches des colonnes modifiables d'une commande et de ses statuts : aucun nom de colonne
  n'est pris dans une requête.
- En-têtes de sécurité (politique de sécurité du contenu, `nosniff`, interdiction d'affichage dans un
  cadre, `Strict-Transport-Security` en HTTPS), messages d'erreur génériques, noms de fichiers de
  sauvegarde strictement contrôlés, contenu des notifications échappé.
- Téléchargement des médias limité aux hôtes de Twilio ; corps des requêtes limité à 1 Mo.
- Export JSON de la base envoyé sans copie conservée sur le serveur.
- Conteneur exécuté par un utilisateur sans privilèges, sans droit d'écriture dans le dossier de
  l'application.
- Actions GitHub désignées par l'empreinte de leur commit ; les tests n'utilisent aucun secret.

### Supprimé

- Prise en charge de Python 3.10.
- Scripts ponctuels qui contenaient des identifiants ou des données personnelles de clients, et fichiers
  de données de clients retirés du suivi Git (bases SQLite, export JSON), exclus par `.gitignore`.
- Workflow GitHub Actions de sauvegarde quotidienne, qui n'avait aucune base à sauvegarder dans le
  dépôt.

## [2.1.0] - 2025-12-28

### Ajouté

- Suite de tests automatisés : tests unitaires de tous les modules, tests d'intégration de bout en bout
  et tests de validation des fichiers de workflows.
- Intégration continue et publication avec GitHub Actions : analyse statique, tests, contrôles de
  sécurité et construction.
- Sauvegarde automatique quotidienne et déploiement vers un environnement de préproduction et de
  production, par workflows.
- Mesure de la couverture des tests.

### Modifié

- Lecture des PDF avec `pypdf`, bibliothèque maintenue, à la place de PyPDF2.

## [2.0.0] - 2025-12-28

### Ajouté

- Tableau de bord redessiné, avec des statistiques par canal (email et WhatsApp).
- Notifications en temps réel dans le navigateur, avec message contextuel et son.
- Prise en charge complète de la darija marocaine : transcription des notes vocales (Whisper) et
  extraction par modèle de langue.
- Extraction intelligente du nom du client et gestion de plusieurs clients derrière un même numéro de
  téléphone.
- Notifications WhatsApp de validation et de rejet des commandes.
- Notifications par email au format HTML pour la validation et le rejet.
- Barre de progression du traitement des emails.
- Sauvegarde hybride de la base (automatique et manuelle), page de gestion des sauvegardes et
  planificateur automatique (toutes les 6 heures).
- Recherche et filtres sur la page des clients.

## [1.1.0] - 2025-12-23

### Ajouté

- Intégration de WhatsApp avec Twilio.
- Prise en charge des notes vocales en darija et en arabe.
- Notifications de validation et de rejet.
- Détection automatique des renouvellements de commande.

## [1.0.0] - 2025-12-23

### Ajouté

- Extraction des commandes depuis les emails d'une boîte Gmail.
- Interface web de validation des commandes.
- Base de données SQLite.
- Statistiques et exports.
