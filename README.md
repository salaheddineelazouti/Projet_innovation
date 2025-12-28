# 🚀 Système d'Automatisation des Bons de Commande

## 📋 Description du Projet

Système intelligent d'automatisation de la saisie des bons de commande utilisant l'IA (OpenAI GPT-4o) pour extraire et valider les informations depuis les emails et messages WhatsApp.

### 🎯 Objectifs
- Automatiser la réception et l'extraction des commandes depuis **Email** et **WhatsApp**
- Utiliser l'IA pour extraire les données structurées (client, produit, quantité, prix...)
- Détecter automatiquement les **relances/renouvellements** de commandes
- Fournir une interface web pour la validation par l'équipe commerciale
- Envoyer des confirmations automatiques aux clients

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
│  - Vision pour images                   │
│  - Whisper pour audio (Darija)          │
│  - Détection relances automatique       │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│           BASE DE DONNÉES               │
│  - SQLite (orders.db)                   │
│  - Clients, Produits, Commandes         │
│  - Historique pour auto-remplissage     │
└────────────────────┬────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────┐
│         INTERFACE WEB (Flask)           │
│  - Dashboard & Analytics                │
│  - Validation/Rejet des commandes       │
│  - Gestion clients & alertes            │
│  - Export Excel/PDF/CSV                 │
└─────────────────────────────────────────┘
```

---

## 📁 Structure des Fichiers

```
Projet_innovation/
├── app.py                  # Application Flask principale
├── gmail_receiver.py       # Réception emails via IMAP
├── whatsapp_receiver.py    # Intégration WhatsApp/Twilio
├── data_extractor.py       # Extraction IA (OpenAI)
├── database.py             # Gestion base de données SQLite
├── process_orders.py       # Orchestration du traitement
├── analytics.py            # Statistiques & rapports
├── orders.db               # Base de données SQLite
├── .env                    # Variables d'environnement (secrets)
├── requirements.txt        # Dépendances Python
├── ngrok.exe               # Tunnel pour webhook WhatsApp
│
├── templates/              # Templates HTML (Jinja2)
│   ├── base.html           # Template de base
│   ├── index.html          # Dashboard
│   ├── orders.html         # Liste des commandes
│   ├── order_detail.html   # Détail & validation
│   ├── clients.html        # Gestion clients
│   ├── client_detail.html  # Détail client
│   ├── analytics.html      # Tableau de bord avancé
│   ├── alerts.html         # Système d'alertes
│   ├── whatsapp.html       # Configuration WhatsApp
│   └── process.html        # Traitement emails
│
├── whatsapp_media/         # Médias WhatsApp téléchargés
├── attachments/            # Pièces jointes emails
└── reports/                # Rapports générés
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

### 4. Configurer WhatsApp (optionnel)

```bash
# Démarrer le tunnel ngrok
.\ngrok.exe http 5000

# Copier l'URL ngrok dans Twilio Console > WhatsApp Sandbox
# Webhook: https://xxxxx.ngrok-free.dev/webhook/whatsapp
```

---

## 📱 Fonctionnalités

### 1. Extraction Email
- Connexion IMAP à Gmail
- Récupération des emails récents
- Extraction du texte des pièces jointes (PDF, images)
- Analyse IA pour détecter les bons de commande

### 2. Extraction WhatsApp
- Réception via webhook Twilio
- Support des messages:
  - **Texte** - Extraction directe
  - **Images** - OCR avec GPT-4o Vision
  - **Audio** - Transcription Whisper (Darija/Arabe supporté)
  - **Documents PDF** - Extraction PyPDF2

### 3. Détection de Relances
Le système détecte automatiquement les expressions comme:
- "kif dima", "b7al dima", "comme d'habitude"
- "même commande", "relancer", "renouveler"
- "comme toujours", "pareil", "comme avant"

Et remplit automatiquement les détails depuis l'historique client.

### 4. Interface Web

| Route | Description |
|-------|-------------|
| `/` | Dashboard principal |
| `/orders` | Liste des commandes |
| `/orders/<id>` | Détail & validation |
| `/clients` | Gestion des clients |
| `/analytics` | Statistiques avancées |
| `/alerts` | Système d'alertes |
| `/whatsapp` | Configuration WhatsApp |
| `/process` | Traitement des emails |

### 5. API REST

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/api/process-emails` | POST | Traiter les emails |
| `/api/orders/<id>/validate` | POST | Valider une commande |
| `/api/orders/<id>/reject` | POST | Rejeter une commande |
| `/api/orders/<id>/update` | POST | Modifier une commande |
| `/api/stats` | GET | Statistiques |
| `/api/whatsapp/status` | GET | Statut WhatsApp |
| `/webhook/whatsapp` | POST | Webhook Twilio |

### 6. Exports

- **Excel** - `/export/excel`
- **PDF** - `/export/pdf`
- **CSV** - `/export/csv`

---

## 📦 Produits Supportés

L'entreprise fabrique 4 types de produits d'emballage:

1. **Sachets fond plat**
2. **Sac fond carré sans poignées**
3. **Sac fond carré avec poignées plates**
4. **Sac fond carré avec poignées torsadées**

---

## 🗄️ Base de Données

### Tables

**`clients`**
- id, nom, email, telephone, adresse, created_at

**`produits`**
- id, type, description

**`commandes`**
- id, numero_commande, client_id, produit_id
- nature_produit, quantite, unite
- prix_unitaire, prix_total, devise
- date_livraison, email_id, email_subject, email_from
- confiance, statut, validated_by, validated_at
- created_at

**`logs`**
- id, action, details, created_at

---

## 🔄 Flux de Traitement

```
1. EMAIL/WHATSAPP REÇU
        │
        ▼
2. DÉTECTION RELANCE ?
   ├── OUI → Recherche historique client
   │         Auto-remplissage des champs
   │         Confiance boostée à 85%
   │
   └── NON → Extraction standard OpenAI
             Confiance calculée par l'IA
        │
        ▼
3. ENREGISTREMENT BASE DE DONNÉES
   Statut: "en_attente"
        │
        ▼
4. VALIDATION COMMERCIALE (Interface web)
   ├── VALIDER → Statut: "validee"
   │             Notification WhatsApp ✅
   │
   └── REJETER → Statut: "rejetee"
                 Notification WhatsApp ❌
```

---

## 📊 Statistiques & Analytics

- Nombre de commandes par statut
- Volume total des commandes
- Clients les plus actifs
- Produits les plus commandés
- Taux de validation
- Alertes automatiques (anomalies, retards...)

---

## 🔐 Sécurité

- Credentials stockés dans `.env` (gitignored)
- Mots de passe d'application Gmail (pas le mot de passe principal)
- Authentification Twilio pour les médias
- Validation côté serveur des données

---

## 🛠️ Technologies Utilisées

| Technologie | Usage |
|-------------|-------|
| **Python 3.x** | Langage principal |
| **Flask** | Framework web |
| **OpenAI GPT-4o** | Extraction IA |
| **OpenAI Whisper** | Transcription audio |
| **Twilio** | WhatsApp API |
| **SQLite** | Base de données |
| **TailwindCSS** | Styling UI |
| **Font Awesome** | Icônes |
| **Jinja2** | Templates |

---

## 📞 Support

Pour toute question ou problème:
- Vérifier les logs dans la console Flask
- Consulter la page `/whatsapp` pour le statut
- Tester avec `/api/whatsapp/status`

---

## 📝 Changelog

### v1.0.0
- ✅ Extraction emails Gmail
- ✅ Interface web de validation
- ✅ Base de données SQLite
- ✅ Analytics & exports

### v1.1.0
- ✅ Intégration WhatsApp/Twilio
- ✅ Support audio (Darija/Arabe)
- ✅ Notifications validation/rejet
- ✅ Détection automatique des relances

---

## 👥 Auteurs

Projet développé dans le cadre d'un projet d'innovation.

---

*Documentation générée le 27/12/2024*
