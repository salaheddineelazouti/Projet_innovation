# 🚀 OrderFlow - Système d'Automatisation des Bons de Commande

## 📋 Description du Projet

Système intelligent d'automatisation de la saisie des bons de commande utilisant l'IA (OpenAI GPT-4o) pour extraire et valider les informations depuis les emails et messages WhatsApp.

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
├── app.py                  # Application Flask principale
├── gmail_receiver.py       # Réception emails via IMAP
├── whatsapp_receiver.py    # Intégration WhatsApp/Twilio + Whisper
├── data_extractor.py       # Extraction IA (OpenAI GPT-4o)
├── database.py             # Gestion base de données SQLite
├── process_orders.py       # Orchestration du traitement emails
├── analytics.py            # Statistiques & rapports
├── email_sender.py         # Envoi emails HTML (validation/rejet)
├── backup_database.py      # Système de sauvegarde hybride
├── orders.db               # Base de données SQLite
├── .env                    # Variables d'environnement (secrets)
├── requirements.txt        # Dépendances Python
├── ngrok.exe               # Tunnel pour webhook WhatsApp
│
├── templates/              # Templates HTML (Jinja2 + TailwindCSS)
│   ├── base.html           # Template de base avec notifications
│   ├── index.html          # Dashboard avec stats par canal
│   ├── orders.html         # Liste des commandes
│   ├── order_detail.html   # Détail & validation
│   ├── clients.html        # Gestion clients avec recherche
│   ├── client_detail.html  # Détail client avec historique
│   ├── analytics.html      # Tableau de bord avancé
│   ├── alerts.html         # Système d'alertes
│   ├── backups.html        # Gestion des sauvegardes
│   ├── whatsapp.html       # Stats WhatsApp
│   └── process.html        # Traitement emails avec progress bar
│
├── backups/                # Dossier des sauvegardes
│   ├── backup_*.db.gz      # Sauvegardes compressées
│   └── backup_history.json # Historique
│
├── whatsapp_media/         # Médias WhatsApp téléchargés
├── attachments/            # Pièces jointes emails
└── exports/                # Fichiers exportés
```

---

## 🔧 Configuration

### Variables d'Environnement (`.env`)

```env
# Gmail Configuration
GMAIL_EMAIL=votre-email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

# OpenAI Configuration
OPENAI_API_KEY=sk-proj-xxxxx

# Twilio WhatsApp
TWILIO_ACCOUNT_SID=ACxxxxx
TWILIO_AUTH_TOKEN=xxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
NGROK_URL=https://xxxxx.ngrok-free.dev
```

### Dépendances (`requirements.txt`)

```
python-dotenv==1.0.0
openai==1.6.1
PyPDF2==3.0.1
Pillow==10.1.0
flask==3.0.0
pandas==2.1.4
openpyxl==3.1.2
reportlab==4.0.8
matplotlib==3.8.2
twilio==8.10.0
requests==2.31.0
```

---

## 🚀 Installation & Démarrage

### 1. Installation des dépendances

```bash
pip install -r requirements.txt
```

### 2. Configuration

1. Créer le fichier `.env` avec vos credentials
2. Activer l'accès IMAP sur Gmail
3. Générer un mot de passe d'application Gmail
4. Créer un compte Twilio pour WhatsApp

### 3. Lancer l'application

```bash
python app.py
```

L'application sera disponible sur: **http://localhost:5000**

### 4. Configurer WhatsApp

```bash
# Démarrer le tunnel ngrok
.\ngrok.exe http 5000

# Configurer dans Twilio Console > Messaging > WhatsApp Sandbox:
# - When a message comes in: https://xxxxx.ngrok-free.dev/webhook/whatsapp
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
python backup_database.py backup

# Lister les sauvegardes
python backup_database.py list

# Restaurer une sauvegarde (interactif)
python backup_database.py restore

# Nettoyer anciennes sauvegardes (garder 10)
python backup_database.py clean 10

# Statistiques de la base
python backup_database.py stats

# Exporter en JSON
python backup_database.py export
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

## 📦 Produits Supportés

L'entreprise fabrique 4 types de produits d'emballage:

| Type | Description |
|------|-------------|
| Sachets fond plat | Pour sandwichs, tacos, viennoiseries |
| Sac fond carré sans poignées | Emballage standard |
| Sac fond carré avec poignées plates | Sacs shopping |
| Sac fond carré avec poignées torsadées | Sacs premium |

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

## 🔐 Sécurité

- Credentials stockés dans `.env` (gitignored)
- Mots de passe d'application Gmail (pas le mot de passe principal)
- Authentification Twilio pour les médias
- Validation côté serveur des données
- Timeout OpenAI configurable (120s)

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
- Whisper est configuré avec `language="ar"` et prompt Darija
- Les fichiers audio sont téléchargés localement avant transcription

### Nom client incorrect
- Vérifier que le message contient le nom (patterns supportés)
- Le numéro de téléphone seul → "Client WhatsApp +XXX"

---

## 📝 Changelog

### v2.0.0 (28/12/2024)
- ✅ Dashboard redesigné avec stats par canal
- ✅ Notifications temps réel avec toast et son
- ✅ Support complet Darija marocain (Whisper + GPT-4)
- ✅ Extraction intelligente noms clients
- ✅ Gestion multi-clients par téléphone
- ✅ Notifications WhatsApp validation/rejet
- ✅ Progress bar traitement emails
- ✅ Correction affichage "Il y a X min"
- ✅ WAL checkpoint pour sync base de données

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

*Documentation mise à jour le 28/12/202