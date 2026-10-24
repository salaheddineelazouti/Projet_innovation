# 🌿 TECPAP - Système d'Automatisation des Bons de Commande

<p align="center">
  <strong>Fabrication de sacs en papier Kraft - 100% biodégradables, 100% recyclables</strong>
</p>

## 📋 Description du Projet

Système intelligent d'automatisation de la saisie des bons de commande pour **TECPAP**, utilisant l'IA (OpenAI GPT-4o) pour extraire et valider les informations depuis les emails et messages WhatsApp.

### 🏢 À propos de TECPAP
- **Localisation** : Parc Industriel CFCIM Bouskoura, Casablanca, Maroc
- **Activité** : Fabrication de sacs en papier Kraft
- **Capacité** : 900 000 000 sacs/an | 10 lignes de production | 7 000 m²
- **Contact** : +212 (0)5 22 86 56 83 | info@tecpap.net
- **Site web** : [www.tecpap.ma](https://www.tecpap.ma)

### 🎯 Objectifs
- Automatiser la réception et l'extraction des commandes depuis **Email** et **WhatsApp**
- Utiliser l'IA pour extraire les données structurées (client, produit, quantité, prix...)
- Détecter automatiquement les **relances/renouvellements** de commandes
- Supporter le **Darija marocain** (dialecte arabe) pour les commandes vocales
- Fournir une interface web moderne pour la validation par l'équipe commerciale
- Envoyer des confirmations automatiques aux clients via WhatsApp

---

## 🏗️ Architecture du Système

```
┌─────────────────┐     ┌─────────────────┐
│   Gmail IMAP    │     │  WhatsApp/Twilio│
│   (Emails)      │     │  (Messages)     │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│         DATA EXTRACTOR (OpenAI)         │
│  - GPT-4o pour extraction texte         │
│  - Vision pour images/PDF               │
│  - Whisper pour audio (Darija/Arabe)    │
│  - Détection relances automatique       │
│  - Extraction noms clients intelligente │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│           BASE DE DONNÉES               │
│  - SQLite avec WAL mode                 │
│  - Clients, Produits, Commandes         │
│  - Historique pour auto-remplissage     │
│  - Gestion multi-connexions             │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│         INTERFACE WEB (Flask)           │
│  - Dashboard avec stats par canal       │
│  - Notifications temps réel             │
│  - Validation/Rejet des commandes       │
│  - Export Excel/PDF/CSV                 │
└─────────────────────────────────────────┘
```

---

## 📁 Structure des Fichiers

```
Projet_innovation/
├── app.py                      # Point d'entrée : app = create_app() (python app.py ou gunicorn app:app)
├── tecpap/                     # Paquet de l'application
│   ├── config.py               # Configuration (Settings, load_settings)
│   ├── logging_config.py       # Journalisation
│   ├── catalog.py              # Catalogue : produits, papiers, grammages, laizes, expressions de renouvellement
│   ├── db/
│   │   ├── database.py         # Base SQLite (clients, produits, commandes, journal)
│   │   └── backup.py           # Sauvegarde, restauration, export JSON
│   ├── channels/
│   │   ├── email_receiver.py   # Réception des emails (IMAP)
│   │   ├── email_sender.py     # Emails HTML de réception, validation, rejet
│   │   └── whatsapp.py         # WhatsApp (Twilio), Whisper, vision, PDF
│   ├── processing/
│   │   └── order_processor.py  # Traitement des emails de commande
│   ├── extraction/
│   │   ├── extractor.py        # Extraction des commandes (OpenAI GPT-4o)
│   │   └── article_codes.py    # Codes articles SAGE X3
│   ├── reporting/
│   │   ├── analytics.py        # Statistiques, alertes, exports Excel/CSV
│   │   └── pdf_report.py       # Rapport PDF
│   └── web/
│       ├── __init__.py         # create_app() : fabrique de l'application Flask
│       ├── services.py         # Services partagés par les routes
│       ├── scheduler.py        # Planificateur des sauvegardes automatiques
│       ├── routes/             # Blueprints : pages, api, webhooks, backups, exports
│       ├── templates/          # Pages HTML (Jinja2 + TailwindCSS)
│       └── static/             # Logos
├── scripts/                    # Outils en ligne de commande
│   ├── process_emails.py       # Traiter les emails de commande
│   ├── backup.py               # Sauvegarder, restaurer, exporter la base
│   └── rename_generic_clients.py  # Renommer les clients au nom provisoire
├── tests/                      # Tests (pytest), sans réseau ni clé d'API
├── .github/workflows/
│   ├── ci.yml                  # Analyse statique, tests, sécurité, image Docker
│   └── deploy.yml              # Publication de l'image Docker sur étiquette v*
├── pyproject.toml              # Métadonnées du paquet, configuration de ruff et pytest
├── requirements.txt            # Dépendances d'exécution
├── requirements-dev.txt        # Dépendances de développement (tests, ruff)
├── .env.example                # Modèle du fichier .env (toutes les variables)
├── Dockerfile                  # Image de production (gunicorn)
├── backups/                    # Sauvegardes de la base
├── whatsapp_media/             # Médias WhatsApp téléchargés
├── attachments/                # Pièces jointes des emails
└── exports/                    # Fichiers exportés (Excel SAGE X3, CSV, PDF)
```

---

## 🔗 Intégration SAGE X3 (Préparation API)

Le système est préparé pour une future intégration avec l'API SAGE X3. Les données des commandes sont structurées pour être compatibles avec le format SAGE X3 :

### Champs compatibles SAGE X3

| Champ TECPAP | Champ SAGE X3 | Description |
|--------------|---------------|-------------|
| `numero_commande` | Numéro commande | Référence unique |
| `ligne_commande` | Ligne commande | Numéro de ligne (défaut: 1) |
| `site_vente` | Site de vente | Code site (défaut: SXP) |
| `code_client` | Code client | Format: CL00001 |
| `client_nom` | Raison sociale | Nom du client |
| `code_article` | Code article | Ex: KB100L28MON |
| `nature_produit` | Désignation | Description produit |
| `quantite` | Qtée commandée | Quantité commandée |
| `quantite_livree` | Qté Livrée | Quantité livrée |
| `reste_a_livrer` | Reste à livrer | Calculé automatiquement |
| `commercial` | Commercial | Nom commercial |
| `type_sac` | Type SAC | KRAFT, KRAFT BLANCHI, etc. |
| `format_sac` | Format LAR.PRE.LON | Dimensions du sac |
| `grammage` | Grammage | En g/m² |
| `laize` | Laize | En cm |

### Format des codes articles TECPAP

```
[TYPE][GRAMMAGE]L[LAIZE][FOURNISSEUR]

Exemples:
- KB100L28MON = Kraft Blanchi 100g/m² Laize 28cm MONDI
- KE80L25 = Kraft Écru 80g/m² Laize 25cm
```

### Export SAGE X3

```bash
# Via l'interface web
GET /export/excel/sage

# Génère un fichier Excel avec les colonnes SAGE X3
```

---

## 🔧 Configuration

### Variables d'Environnement (`.env`)

Toutes les variables, leurs valeurs par défaut et leur rôle sont décrites dans `.env.example`.
Les principales :

```env
GMAIL_EMAIL=votre-email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
OPENAI_API_KEY=sk-proj-xxxxx
TWILIO_ACCOUNT_SID=ACxxxxx
TWILIO_AUTH_TOKEN=xxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
PUBLIC_BASE_URL=https://xxxxx.ngrok-free.app
SECRET_KEY=valeur-longue-et-aleatoire   # obligatoire en production
```

### Dépendances

- `requirements.txt` : dépendances d'exécution (Flask, OpenAI, Twilio, pypdf, pandas, reportlab…) ;
- `requirements-dev.txt` : dépendances de développement (pytest, pytest-cov, ruff, PyYAML).

---

## 🚀 Installation & Démarrage

### 1. Installation des dépendances

```bash
pip install -r requirements-dev.txt   # développement (inclut requirements.txt)
pip install -r requirements.txt       # exécution seule
```

### 2. Configuration

1. Copier `.env.example` en `.env` et remplacer les valeurs d'exemple
2. Activer l'accès IMAP sur Gmail
3. Générer un mot de passe d'application Gmail
4. Créer un compte Twilio pour WhatsApp

### 3. Lancer l'application

```bash
python app.py
```

L'application sera disponible sur: **http://localhost:5000** (adresse et port : `FLASK_HOST`, `FLASK_PORT`).

En production (image Docker) :

```bash
docker build -t tecpap-orders .
docker run --env-file .env -p 5000:5000 -v tecpap-db:/data/db -v tecpap-backups:/data/backups tecpap-orders
```

### 4. Lancer les tests

```bash
# Exécuter tous les tests
pytest

# Avec couverture de code
pytest --cov=tecpap --cov-report=html

# Tests spécifiques
pytest tests/test_database.py -v
pytest tests/test_api.py -v
pytest tests/test_workflows.py -v

# Analyse statique
ruff check .
```

### 5. Configurer WhatsApp

```bash
# Démarrer le tunnel ngrok
.\ngrok.exe http 5000

# Reporter l'adresse publique du tunnel dans .env : PUBLIC_BASE_URL=https://xxxxx.ngrok-free.app
# Configurer dans Twilio Console > Messaging > WhatsApp Sandbox:
# - When a message comes in: https://xxxxx.ngrok-free.app/webhook/whatsapp
# - Status callback URL: (optionnel)

# Pour recevoir les messages, les utilisateurs doivent d'abord envoyer:
# "join <sandbox-keyword>" au numéro WhatsApp Twilio
```

---

## 📱 Fonctionnalités

### 1. Extraction Email
- Connexion IMAP sécurisée à Gmail
- Récupération intelligente des emails (évite les doublons)
- Extraction du texte des pièces jointes (PDF, images)
- Analyse IA pour détecter les bons de commande
- Progress bar temps réel pendant le traitement

### 2. Extraction WhatsApp
- Réception via webhook Twilio
- Support des messages:
  - **Texte** - Extraction directe avec patterns Darija
  - **Images** - OCR avec GPT-4o Vision
  - **Audio** - Transcription Whisper optimisée Darija/Arabe
  - **Documents PDF** - Extraction PyPDF2 + Vision
- Confirmation automatique au client

### 3. Support Darija Marocain 🇲🇦

Le système comprend le vocabulaire marocain:
- "bghit" / "بغيت" = je veux
- "khassni" / "خصني" = j'ai besoin de
- "3tini" / "عطيني" = donne-moi
- "sachet" / "ساشي" = sachets
- "ana restaurant X" = identification client

**Prompt Whisper optimisé** pour la transcription audio en Darija.

### 4. Extraction Intelligente des Noms de Clients

Le système détecte le nom du client depuis plusieurs patterns:
- "Commande pour [CLIENT]" → Ecole Mohamadia des Ingénieurs
- "ana [nom]" / "أنا [nom]" → Restaurant Salah Eddine
- "de la part de [nom]" → Café Central

**Important**: Le numéro de téléphone n'est pas utilisé comme identifiant unique - un même numéro peut commander pour différentes entreprises.

### 5. Détection de Relances Automatique

Le système détecte les expressions comme:
- "kif dima", "b7al dima", "comme d'habitude"
- "même commande", "relancer", "renouveler"
- "comme toujours", "pareil", "comme la dernière fois"

Et remplit automatiquement depuis l'historique:
- Produit commandé précédemment
- Quantité habituelle
- Prix négocié

### 6. Notifications WhatsApp

Lors de la **validation** d'une commande:
```
✅ Commande Validée!

Votre commande a été validée avec succès.
📦 Produit: Sachets fond plat
🔢 Quantité: 5000 pièces

Merci pour votre confiance!
```

Lors du **rejet** d'une commande:
```
❌ Commande Non Validée

Votre commande n'a pas pu être validée.
Raison: [motif de rejet]

Veuillez nous contacter pour plus d'informations.
```

### 7. Interface Web Moderne

| Route | Description |
|-------|-------------|
| `/` | Dashboard avec stats Email/WhatsApp, graphique tendances |
| `/orders` | Liste des commandes avec filtres |
| `/orders/<id>` | Détail, modification & validation |
| `/clients` | Gestion des clients avec recherche et filtres |
| `/clients/<id>` | Détail client avec historique commandes |
| `/analytics` | Statistiques avancées |
| `/alerts` | Système d'alertes |
| `/whatsapp` | Stats et KPIs WhatsApp |
| `/process` | Traitement des emails avec progress bar |
| `/backups` | Gestion des sauvegardes de base de données |

### 8. Dashboard

- **Stats par canal**: Commandes Email vs WhatsApp
- **Graphique tendances**: Évolution sur 30 jours
- **Top clients**: Les plus actifs
- **Top produits**: Les plus commandés
- **Notifications temps réel**: Toast + son

### 9. API REST

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/api/process-emails` | POST | Traiter les emails |
| `/api/orders/<id>/validate` | POST | Valider une commande |
| `/api/orders/<id>/reject` | POST | Rejeter une commande |
| `/api/orders/<id>/update` | POST | Modifier une commande |
| `/api/stats` | GET | Statistiques globales |
| `/api/notifications/check` | GET | Polling nouvelles commandes |
| `/webhook/whatsapp` | POST | Webhook Twilio |

### 10. Exports

- **Excel** - `/export/excel` - Toutes les commandes
- **PDF** - `/export/pdf` - Rapport formaté
- **CSV** - `/export/csv` - Données brutes

### 11. Système de Sauvegarde Hybride 💾

Le système implémente une stratégie de sauvegarde **hybride** optimale pour protéger vos données :

#### Sauvegarde Automatique (Backend)
- **Intervalle** : Toutes les 6 heures (configurable)
- **Rétention** : 20 dernières sauvegardes conservées
- **Compression** : Fichiers `.db.gz` pour économiser l'espace
- **Sécurité SQLite** : Utilise l'API `sqlite3.backup()` (compatible mode WAL)

#### Sauvegarde Manuelle (Frontend)
Interface accessible via **Sidebar → Système → Sauvegardes** (`/backups`)

| Action | Description |
|--------|-------------|
| **Nouvelle sauvegarde** | Créer une sauvegarde immédiate |
| **Télécharger backup** | Télécharger une copie fraîche sur votre PC |
| **Restaurer** | Restaurer depuis une sauvegarde (backup pré-restauration auto) |
| **Exporter JSON** | Export complet de toutes les données en JSON |
| **Supprimer** | Supprimer une sauvegarde obsolète |

#### API Backup

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/api/backup/create` | POST | Créer une sauvegarde |
| `/api/backup/list` | GET | Lister les sauvegardes |
| `/api/backup/download/<filename>` | GET | Télécharger une sauvegarde |
| `/api/backup/download-latest` | GET | Créer et télécharger immédiatement |
| `/api/backup/restore/<filename>` | POST | Restaurer une sauvegarde |
| `/api/backup/delete/<filename>` | DELETE | Supprimer une sauvegarde |
| `/api/backup/export-json` | GET | Exporter en JSON |

#### Ligne de Commande

```bash
# Créer une sauvegarde
python scripts/backup.py backup

# Lister les sauvegardes
python scripts/backup.py list

# Restaurer une sauvegarde (interactif)
python scripts/backup.py restore

# Nettoyer anciennes sauvegardes (garder 10)
python scripts/backup.py clean 10

# Statistiques de la base
python scripts/backup.py stats

# Exporter en JSON
python scripts/backup.py export
```

#### Fichiers de Sauvegarde

```
backups/
├── backup_20251228_032504.db.gz    # Sauvegarde compressée
├── backup_20251228_090000.db.gz    # Sauvegarde auto 6h
├── pre_restore_20251228_120000.db  # Backup avant restauration
├── export_20251228_150000.json     # Export JSON
└── backup_history.json             # Historique des sauvegardes
```

### 12. Notifications Email Professionnelles 📧

Le système envoie des emails HTML professionnels lors de la validation/rejet des commandes :

#### Email de Validation (Vert)
- Design moderne avec header dégradé vert
- Récapitulatif de la commande
- Timeline de suivi (Validée → Préparation → Expédition)
- Footer professionnel

#### Email de Rejet (Neutre)
- Design sobre avec header gris
- Détails de la demande
- Motif de la décision
- Conseils pour procéder

#### Configuration SMTP

```env
# Dans .env
GMAIL_EMAIL=votre-email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

---

## 📦 Produits TECPAP

TECPAP fabrique 4 types de sacs en papier Kraft:

| Type | Description | Utilisation |
|------|-------------|-------------|
| **Sachets fond plat** | Sacs simples à fond plat | Sandwichs, viennoiseries, snacks |
| **Sac fond carré sans poignées** | Emballage standard SOS | Boulangeries, pharmacies |
| **Sac fond carré avec poignées plates** | Sacs shopping pratiques | Boutiques, magasins |
| **Sac fond carré avec poignées torsadées** | Sacs premium élégants | Luxe, cadeaux, mode |

### Caractéristiques
- ✅ **100% biodégradables**
- ✅ **100% recyclables**
- 🎨 **Impression jusqu'à 6 couleurs**
- 👨‍🎨 **Équipe de créatifs dédiée** pour maquettes personnalisées

---

## 🗄️ Base de Données

### Configuration SQLite
- **Mode WAL** pour accès concurrent
- **Busy timeout** 30 secondes
- **check_same_thread=False** pour Flask

### Tables

**`clients`**
```sql
- id INTEGER PRIMARY KEY
- nom TEXT NOT NULL
- email TEXT
- telephone TEXT
- adresse TEXT
- created_at TIMESTAMP
```

**`produits`**
```sql
- id INTEGER PRIMARY KEY
- type TEXT NOT NULL
- description TEXT
```

**`commandes`**
```sql
- id INTEGER PRIMARY KEY
- numero_commande TEXT
- client_id INTEGER (FK)
- produit_id INTEGER (FK)
- nature_produit TEXT
- quantite REAL
- unite TEXT
- prix_unitaire REAL
- prix_total REAL
- devise TEXT DEFAULT 'MAD'
- date_commande TEXT
- date_livraison TEXT
- email_id TEXT UNIQUE
- email_subject TEXT
- email_from TEXT
- whatsapp_from TEXT
- source TEXT DEFAULT 'email'
- confiance INTEGER
- statut TEXT DEFAULT 'en_attente'
- validated_by TEXT
- validated_at TIMESTAMP
- rejection_reason TEXT
- created_at TIMESTAMP
```

---

## 🔄 Flux de Traitement

```
1. EMAIL/WHATSAPP REÇU
        │
        ▼
2. VÉRIFICATION DOUBLON
   Email déjà traité? → Skip
        │
        ▼
3. DÉTECTION RELANCE ?
   ├── OUI → Recherche historique client
   │         Auto-remplissage des champs
   │         Nom client exact depuis BDD
   │         Confiance boostée à 85%
   │
   └── NON → Extraction standard OpenAI
             Détection nom client dans message
             Confiance calculée par l'IA
        │
        ▼
4. ENREGISTREMENT BASE DE DONNÉES
   - Création/récupération client
   - Statut: "en_attente"
   - Notification temps réel UI
        │
        ▼
5. VALIDATION COMMERCIALE (Interface web)
   ├── VALIDER → Statut: "validee"
   │             Notification WhatsApp ✅
   │
   └── REJETER → Statut: "rejetee"
                 Motif enregistré
                 Notification WhatsApp ❌
```

---

## 📊 Statistiques & Analytics

- **Par statut**: En attente, Validées, Rejetées
- **Par canal**: Email vs WhatsApp
- **Par période**: Aujourd'hui, semaine, mois
- **Top clients**: Volume et fréquence
- **Top produits**: Les plus commandés
- **Taux de validation**: Ratio validées/total
- **Graphique tendances**: Chart.js

---

## 🧪 Tests

Le projet dispose d'une suite de 455 tests, exécutés sans réseau ni clé d'API : OpenAI, Twilio,
IMAP et SMTP sont simulés, et chaque test utilise une base et des dossiers temporaires.

### Structure des Tests

| Fichier | Description |
|---------|-------------|
| `test_config.py` | Configuration : valeurs par défaut, conversion des variables, règles de production |
| `test_catalog.py` | Catalogue produits, expressions de renouvellement, codes articles |
| `test_app_factory.py` | Fabrique d'application, routes, exports, webhook WhatsApp, planificateur |
| `test_database.py` | Opérations sur la base, migration du schéma |
| `test_data_extractor.py` | Extraction avec un modèle simulé, historique client, pièces jointes |
| `test_backup.py` | Sauvegarde, restauration, nettoyage, export JSON |
| `test_email.py` | Envoi des emails (SMTP simulé) |
| `test_api.py` | Pages et API du tableau de bord |
| `test_whatsapp.py` | Messages WhatsApp, médias, transcription, envoi |
| `test_gmail.py` | Réception des emails (IMAP simulé) |
| `test_integration.py` | Flux complets sur la base |
| `test_workflows.py` | Workflows GitHub Actions |

### Exécution des Tests

```bash
# Tous les tests
pytest

# Avec verbose
pytest -v

# Tests spécifiques par fichier
pytest tests/test_database.py -v

# Tests par marqueur
pytest -m "not slow"

# Avec couverture
pytest --cov=tecpap --cov-report=html --cov-report=term-missing

# Rapport HTML dans htmlcov/index.html
```

### Fixtures Partagées

Les fixtures pytest dans `conftest.py` fournissent :
- `test_settings` : configuration de test (`Settings.for_tests(tmp_path)`)
- `temp_db` : base de données temporaire initialisée
- `flask_app`, `flask_client`, `services` : application construite par `create_app(test_settings)`
- `sample_order_data`, `sample_whatsapp_order` : données de commande de test

---

## 🔄 CI/CD - GitHub Actions

### Workflows Configurés

#### 1. Intégration continue (`ci.yml`)

Déclenché sur push vers `main`, `develop` et `feature/**`, et sur les pull requests vers `main` et `develop`.

| Job | Description | Outils |
|-----|-------------|--------|
| **lint** | Analyse statique, bloquante | ruff |
| **tests** | Tests et couverture (Python 3.10, 3.11, 3.12) | pytest, pytest-cov |
| **security** | Rapport de sécurité, non bloquant | bandit, pip-audit |
| **build** | Création de l'application et de l'image Docker | docker |

#### 2. Publication (`deploy.yml`)

- **Déclencheur** : étiquette `v*` (ex : v3.0.0)
- **Jobs** : tests → construction de l'image Docker → image publiée comme artefact du workflow
- Aucune cible de déploiement n'est configurée : l'image est chargée sur le serveur avec `docker load`

### Statut des Tests

```
455 tests réussis
Couverture du paquet tecpap : 78 %
```

---

## 🔐 Sécurité

- Credentials stockés dans `.env` (gitignored)
- Mots de passe d'application Gmail (pas le mot de passe principal)
- Authentification Twilio pour les médias
- Validation côté serveur des données
- Délai maximal des appels OpenAI configurable (`LLM_TIMEOUT`, 60 s par défaut)

---

## 🛠️ Technologies Utilisées

| Technologie | Usage |
|-------------|-------|
| **Python 3.11+** | Langage principal |
| **Flask 3.x** | Framework web |
| **OpenAI GPT-4o** | Extraction IA texte/vision |
| **OpenAI Whisper** | Transcription audio Darija |
| **Twilio** | WhatsApp API |
| **SQLite** | Base de données (WAL mode) |
| **pypdf** | Extraction PDF |
| **pytest** | Framework de tests |
| **GitHub Actions** | CI/CD pipelines |
| **TailwindCSS** | Styling UI moderne |
| **Chart.js** | Graphiques |
| **Font Awesome** | Icônes |
| **Jinja2** | Templates HTML |

---

## 🐛 Résolution de Problèmes

### WhatsApp ne reçoit pas les notifications
1. Vérifier que le client a rejoint le sandbox Twilio
2. Vérifier le format du numéro (whatsapp:+212...)
3. Consulter les logs Twilio

### Emails traités en double
- Le système utilise `email_id` unique
- WAL checkpoint force la synchronisation

### Transcription audio incorrecte
- La langue est détectée par Whisper ; `TRANSCRIPTION_LANGUAGE` (par exemple `ar`) l'impose
- Un vocabulaire darija, arabe et français est transmis à Whisper
- Les fichiers audio sont téléchargés localement avant transcription

### Nom client incorrect
- Vérifier que le message contient le nom (patterns supportés)
- Le numéro de téléphone seul → "Client WhatsApp +XXX"

---

## 📝 Changelog

### v2.1.0 (29/12/2024)
- ✅ Suite de tests complète (257 tests)
- ✅ Tests unitaires pour tous les modules
- ✅ Tests d'intégration end-to-end
- ✅ GitHub Actions CI/CD (lint, test, security, build)
- ✅ Workflow de sauvegarde automatique quotidienne
- ✅ Workflow de déploiement staging/production
- ✅ Migration PyPDF2 → pypdf (version moderne)
- ✅ Tests de validation des workflows YAML
- ✅ Couverture de code > 80%
- ✅ 0 warnings dans les tests

### v2.0.0 (28/12/2024)
- ✅ Dashboard redesigné avec stats par canal
- ✅ Notifications temps réel avec toast et son
- ✅ Support complet Darija marocain (Whisper + GPT-4)
- ✅ Extraction intelligente noms clients
- ✅ Gestion multi-clients par téléphone
- ✅ Notifications WhatsApp validation/rejet
- ✅ Notifications Email HTML professionnelles
- ✅ Progress bar traitement emails
- ✅ Correction affichage "Il y a X min"
- ✅ WAL checkpoint pour sync base de données
- ✅ Système de sauvegarde hybride (auto + manuel)
- ✅ Page de gestion des sauvegardes
- ✅ Planificateur automatique (toutes les 6h)
- ✅ Recherche et filtres sur page clients

### v1.1.0
- ✅ Intégration WhatsApp/Twilio
- ✅ Support audio (Darija/Arabe)
- ✅ Notifications validation/rejet
- ✅ Détection automatique des relances

### v1.0.0
- ✅ Extraction emails Gmail
- ✅ Interface web de validation
- ✅ Base de données SQLite
- ✅ Analytics & exports

---

## 👥 Auteurs

Projet développé dans le cadre d'un projet d'innovation.
projet scientifique
**Réalisé par**:equipe de projet scientifique

---

*Documentation mise à jour le 29/12/2024*