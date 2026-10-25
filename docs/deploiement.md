# Déploiement en production

Ce guide décrit la mise en service de TECPAP sur un serveur : image Docker, réglages obligatoires,
HTTPS, configuration de WhatsApp (Twilio) et de la boîte Gmail, indexation de la base de
connaissances, sauvegardes, journaux, changement de version et dépannage.

Documents liés : [README](../README.md) (installation en développement), [sécurité](securite.md)
(mesures de protection et leurs raisons), [assistant WhatsApp](assistant_whatsapp.md),
[base de connaissances et recherche](rag.md), [API](api.md), [contribution](../CONTRIBUTING.md).

Sommaire :

1. [Prérequis](#1-prérequis)
2. [Liste de contrôle avant la mise en production](#2-liste-de-contrôle-avant-la-mise-en-production)
3. [Fichier d'environnement](#3-fichier-denvironnement)
4. [Image Docker : construction et lancement](#4-image-docker--construction-et-lancement)
5. [Proxy inverse et HTTPS](#5-proxy-inverse-et-https)
6. [WhatsApp avec Twilio](#6-whatsapp-avec-twilio)
7. [Boîte Gmail](#7-boîte-gmail)
8. [Base de connaissances de l'assistant](#8-base-de-connaissances-de-lassistant)
9. [Sauvegardes et restauration](#9-sauvegardes-et-restauration)
10. [Journaux et surveillance](#10-journaux-et-surveillance)
11. [Changement de version](#11-changement-de-version)
12. [Dépannage](#12-dépannage)

---

## 1. Prérequis

| Élément | Détail |
|---|---|
| Serveur | Linux avec Docker (l'image est construite sur `python:3.11-slim`) |
| Nom de domaine | Un nom public (par exemple `tecpap.example.org`) qui pointe vers le serveur |
| Certificat HTTPS | Obtenu par le proxy inverse (section 5), par exemple avec Let's Encrypt |
| Compte OpenAI | Clé d'API pour l'extraction, la transcription des notes vocales et la lecture des images |
| Compte Twilio | Numéro ou bac à sable WhatsApp, identifiant du compte et jeton d'authentification |
| Boîte Gmail | Adresse dédiée aux commandes, avec la validation en deux étapes et un mot de passe d'application |
| Postes de l'équipe commerciale | Accès à Internet : le tableau de bord charge ses feuilles de style, ses graphiques et ses polices depuis des réseaux de diffusion de contenu (CDN) |

Un proxy inverse est un serveur placé devant l'application : il reçoit les connexions HTTPS des
navigateurs et de Twilio, puis les transmet à l'application en HTTP sur le serveur lui-même.

Sans clé OpenAI, l'application démarre : l'extraction se fait alors par des règles fixes, chaque
commande arrive au tableau de bord avec tous ses champs à vérifier, et la transcription des notes
vocales ainsi que la lecture des images sont indisponibles. Un service dont la clé ou le jeton est
vide reste désactivé.

## 2. Liste de contrôle avant la mise en production

Chaque ligne se vérifie dans le fichier `.env` (section 3). Les réglages sont lus et contrôlés par
`tecpap/config.py` au démarrage ; une valeur refusée arrête l'application avec un message en français.

| Variable | Valeur en production | Raison |
|---|---|---|
| `APP_ENV` | `production` (déjà fixé par l'image) | Active les contrôles stricts décrits ci-dessous |
| `SECRET_KEY` | Au moins 32 caractères, tirés au hasard | Clé de signature des sessions : une clé connue permettrait de fabriquer une session ouverte |
| `FLASK_DEBUG` | `false` | Le mode débogage est refusé en production |
| `AUTH_ENABLED` | `true` | Connexion obligatoire au tableau de bord |
| `ADMIN_PASSWORD_HASH` | Empreinte produite par `scripts/hash_password.py` | Sans empreinte, aucune connexion n'est possible |
| `CSRF_ENABLED` | `true` | Protection contre la falsification de requête intersite (CSRF, Cross-Site Request Forgery) |
| `SESSION_COOKIE_SECURE` | Vide ou `true` | Le cookie de session n'est envoyé qu'en HTTPS (vide : activé en production) |
| `TRUST_PROXY_HEADERS` | `true` derrière un proxy inverse | Adresse du client et protocole sont lus dans les en-têtes `X-Forwarded-*` |
| `PUBLIC_BASE_URL` | `https://` suivi du nom de domaine, sans `/` final | Adresse que Twilio appelle, nécessaire à la vérification de la signature |
| `TWILIO_VALIDATE_SIGNATURE` | `true` | Seul Twilio peut créer une commande par le webhook |
| `TWILIO_AUTH_TOKEN` | Jeton du compte Twilio | Sert à vérifier la signature et à télécharger les médias |

Règles appliquées au démarrage en production :

- `SECRET_KEY` absente, trop courte (moins de 32 caractères) ou égale à une valeur d'exemple de
  `.env.example` : l'application refuse de démarrer. Une valeur d'exemple est une valeur qui contient
  « remplacer », qui n'est faite que de « x » ou qui reprend un des exemples du modèle (identifiant
  Twilio, adresse publique, adresse email).
- `FLASK_DEBUG=true` : l'application refuse de démarrer.
- Une clé d'API, un jeton, un mot de passe ou une adresse publique égale à la valeur d'exemple est
  ignorée avec un avertissement : le service concerné reste désactivé.
- `TWILIO_VALIDATE_SIGNATURE=true` sans `TWILIO_AUTH_TOKEN` : le webhook refuse tous les messages
  (réponse 503) et le journal signale l'erreur au démarrage.
- `TWILIO_VALIDATE_SIGNATURE=false` : le journal signale que n'importe qui peut appeler le webhook.
  Ce réglage ne sert qu'aux essais locaux.
- `TRUST_PROXY_HEADERS` absent derrière un proxy : le journal avertit que toutes les requêtes
  semblent venir du proxy, et que le blocage après `LOGIN_MAX_ATTEMPTS` échecs de connexion
  s'applique alors à tous les utilisateurs à la fois.

### Générer la clé de session

Sur un poste qui dispose de Python :

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

ou, avec OpenSSL :

```bash
openssl rand -hex 32
```

Les deux commandes donnent 64 caractères hexadécimaux. La clé doit rester la même d'un redémarrage
à l'autre : la changer déconnecte tous les utilisateurs.

### Produire l'empreinte du mot de passe administrateur

```bash
python scripts/hash_password.py
```

Le script demande le mot de passe deux fois, sans l'afficher (12 caractères au minimum), puis
affiche la ligne à copier dans `.env` :

```
ADMIN_PASSWORD_HASH='scrypt:...'
```

Sans Python sur le serveur, la même commande s'exécute dans l'image (section 4) :

```bash
docker run --rm -it tecpap-orders python scripts/hash_password.py
```

L'empreinte contient des caractères `$`. Deux règles en découlent :

- dans un fichier lu par `docker run --env-file`, la valeur est transmise telle quelle, sans
  interprétation des `$` : les apostrophes sont inutiles et la ligne peut s'écrire
  `ADMIN_PASSWORD_HASH=scrypt:...`. Les garder est sans effet, car l'application retire une paire
  d'apostrophes ou de guillemets qui entoure toute la valeur ;
- ne jamais passer l'empreinte avec `-e ADMIN_PASSWORD_HASH=...` dans un terminal sans l'entourer
  d'apostrophes : le terminal remplacerait les `$` et l'empreinte serait tronquée. Au démarrage,
  l'application avertit quand la valeur ne ressemble pas à une empreinte.

Le mot de passe lui-même n'est enregistré nulle part.

## 3. Fichier d'environnement

Le fichier `.env` rassemble les réglages et les secrets. Il se crée à partir du modèle commenté :

```bash
cp .env.example .env
```

`.env` est exclu du dépôt Git (`.gitignore`) et de l'image Docker (`.dockerignore`). Il ne doit être
lisible que par l'administrateur du serveur :

```bash
chmod 600 .env
```

Contenu minimal d'un fichier de production (les valeurs entre chevrons sont à remplacer ; les
variables non citées gardent la valeur par défaut indiquée dans `.env.example`) :

```
SECRET_KEY=<64 caractères produits par la commande de la section 2>
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<empreinte produite par scripts/hash_password.py>
TRUST_PROXY_HEADERS=true
PUBLIC_BASE_URL=https://tecpap.example.org
OPENAI_API_KEY=<clé d'API OpenAI>
GMAIL_EMAIL=<adresse de la boîte des commandes>
GMAIL_APP_PASSWORD=<mot de passe d'application Gmail>
TWILIO_ACCOUNT_SID=<identifiant du compte Twilio, commence par AC>
TWILIO_AUTH_TOKEN=<jeton d'authentification Twilio>
TWILIO_WHATSAPP_NUMBER=whatsapp:<numéro d'envoi au format international>
```

Ne pas définir `DATABASE_PATH`, `BACKUP_DIR`, `EXPORT_DIR`, `ATTACHMENTS_DIR` ni
`WHATSAPP_MEDIA_DIR` dans ce fichier : l'image les fixe sous `/data`, dossier accessible en écriture
à l'utilisateur du conteneur. Les variables passées par `--env-file` ou `-e` remplacent celles de
l'image ; un chemin relatif comme `orders.db` placerait la base dans `/app`, où l'application ne
peut pas écrire. Les lignes correspondantes de `.env.example` sont commentées pour cette raison.

Le fichier `.env.example` décrit chaque variable : rôle, valeur par défaut et valeurs acceptées.

Vérification de la configuration avant le lancement (l'image n'est pas démarrée, seul le contrôle
de `tecpap/config.py` s'exécute) :

```bash
docker run --rm --env-file .env tecpap-orders python -c "from tecpap.config import load_settings; load_settings(); print('Configuration valide')"
```

## 4. Image Docker : construction et lancement

### Construction

```bash
docker build -t tecpap-orders .
```

Les dépendances sont installées aux versions exactes de `requirements.txt`, celles qui ont passé les
tests. L'image contient le code, la base de connaissances (`knowledge/`) et les scripts (`scripts/`) ;
`.dockerignore` en exclut les secrets, les données, les tests, le jeu d'évaluation et les dépendances
de développement.

### Contenu de l'image

| Élément | Valeur |
|---|---|
| Image de base | `python:3.11-slim` |
| Dossier de l'application | `/app` (en lecture seule pour l'utilisateur du conteneur) |
| Utilisateur | `tecpap` (identifiant 10001), sans privilèges, propriétaire de `/data` |
| Port | 5000 |
| `APP_ENV` | `production` |
| Serveur | gunicorn, `app:app` |

### Dossiers de données

| Chemin dans le conteneur | Contenu | Volume conseillé |
|---|---|---|
| `/data/db` | Base SQLite `orders.db` et ses fichiers de journal | `tecpap-db` |
| `/data/backups` | Sauvegardes de la base et exports JSON | `tecpap-backups` |
| `/data/media/attachments` | Pièces jointes des emails | `tecpap-media` |
| `/data/media/whatsapp` | Médias reçus par WhatsApp (notes vocales, images, PDF) | `tecpap-media` |
| `/data/exports` | Exports Excel, CSV et PDF | aucun (fichiers recréés à la demande) |

L'image déclare `/data/db`, `/data/backups` et `/data/media` comme volumes. Sans `-v` explicite,
Docker crée des volumes anonymes qu'une autre commande `docker run` ne retrouve pas : les données
semblent alors perdues. Toujours nommer les volumes.

Avec un dossier de l'hôte plutôt qu'un volume nommé (`-v /srv/tecpap/db:/data/db`), le dossier doit
appartenir à l'utilisateur 10001 :

```bash
sudo chown -R 10001:10001 /srv/tecpap
```

### Lancement

```bash
docker run -d --name tecpap --restart unless-stopped \
  --env-file .env \
  --stop-timeout 130 \
  -p 127.0.0.1:5000:5000 \
  -v tecpap-db:/data/db \
  -v tecpap-backups:/data/backups \
  -v tecpap-media:/data/media \
  --log-opt max-size=10m --log-opt max-file=5 \
  tecpap-orders
```

| Option | Rôle |
|---|---|
| `--restart unless-stopped` | Relance le conteneur après un redémarrage du serveur ou un plantage |
| `--env-file .env` | Transmet les réglages et les secrets |
| `--stop-timeout 130` | Laisse 130 secondes à l'arrêt, plus que le délai de grâce de gunicorn (120 secondes) |
| `-p 127.0.0.1:5000:5000` | Publie le port uniquement pour le proxy inverse du serveur |
| `-v ...` | Volumes des données (tableau ci-dessus) |
| `--log-opt ...` | Limite la taille des journaux conservés par Docker |

Sans proxy inverse (essai sur un poste), `-p 5000:5000` rend le tableau de bord accessible à
l'adresse `http://localhost:5000`. Le cookie de session n'étant alors pas envoyé en HTTP, la
connexion échoue tant que `SESSION_COOKIE_SECURE=false` n'est pas défini dans `.env`.

### Réglages de gunicorn et raison d'un seul processus

La commande de l'image est :

```
gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 4 --timeout 120 --graceful-timeout 120 --access-logfile - app:app
```

| Option | Valeur | Effet |
|---|---|---|
| `--workers` | 1 | Un seul processus |
| `--threads` | 4 | Quatre requêtes servies en parallèle dans ce processus |
| `--timeout` | 120 | Un fil bloqué plus de 120 secondes est relancé |
| `--graceful-timeout` | 120 | À l'arrêt, les tâches en cours disposent de 120 secondes pour finir |
| `--access-logfile -` | sortie standard | Une ligne par requête dans le journal du conteneur |

Il ne faut pas augmenter `--workers`. L'application garde en mémoire du processus :

- le planificateur des sauvegardes : plusieurs processus lanceraient chacun leurs sauvegardes ;
- l'exécuteur des tâches de fond (traitement différé des messages WhatsApp) et la maintenance
  périodique, qui reprend les messages en attente ;
- les verrous par expéditeur et la liste des messages en cours de traitement : sans eux, un même
  message WhatsApp pourrait recevoir deux réponses ;
- les compteurs d'échecs de connexion : répartis sur plusieurs processus, ils laisseraient contourner
  le blocage après plusieurs échecs.

Chaque fil d'exécution ouvre sa propre connexion SQLite, en mode WAL (journal d'écriture anticipée :
les lectures se font en parallèle, les écritures l'une après l'autre). La parallélisation se fait
donc avec `--threads`, non avec `--workers`.

Twilio attend la réponse du webhook au plus 15 secondes. Avec `WHATSAPP_ASYNC_REPLY=true` (valeur par
défaut), le webhook enregistre le message dans une file durable, répond aussitôt, puis le traitement
a lieu en tâche de fond et la réponse part par l'API de Twilio. Un message dont le traitement est
interrompu par un arrêt est repris au démarrage suivant.

### Sonde de disponibilité

L'image contient une sonde (`HEALTHCHECK`) : toutes les 30 secondes, elle appelle
`http://127.0.0.1:5000/healthz`, avec un délai de 5 secondes, 20 secondes de grâce au démarrage et
3 échecs consécutifs avant de déclarer le conteneur défaillant. Cette adresse est accessible sans
connexion et lit la base : elle répond 200 quand la base est lisible, 503 sinon.

```bash
curl http://127.0.0.1:5000/healthz
docker inspect --format '{{.State.Health.Status}}' tecpap
```

La première commande renvoie `{"status": "ok", "version": "3.0.0"}`, la seconde `healthy` une fois
l'application démarrée.

## 5. Proxy inverse et HTTPS

Le tableau de bord manipule des données de clients et des mots de passe : il ne doit être joint qu'en
HTTPS. Le webhook de Twilio exige aussi une adresse HTTPS publique.

Exigences pour le proxy inverse :

- terminer HTTPS (certificat valide) et transmettre à `127.0.0.1:5000` ;
- écrire les en-têtes `X-Forwarded-For`, `X-Forwarded-Proto` et `X-Forwarded-Host` en remplaçant ceux
  de la requête reçue : l'application, avec `TRUST_PROXY_HEADERS=true`, fait confiance à un seul
  proxy et lit la dernière valeur de chaque en-tête ;
- laisser passer le chemin `/webhook/whatsapp` sans authentification du proxy : il est protégé par la
  signature de Twilio ;
- accepter un délai de réponse d'au moins 130 secondes.

Exemple avec nginx (certificat obtenu au préalable, par exemple avec Certbot) :

```nginx
server {
    listen 80;
    server_name tecpap.example.org;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name tecpap.example.org;

    ssl_certificate     /etc/letsencrypt/live/tecpap.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tecpap.example.org/privkey.pem;

    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 130s;
    }
}
```

L'application limite le corps d'une requête à 1 Mo : Twilio transmet les médias par adresse, jamais
dans la requête.

Exemple avec Caddy, qui obtient et renouvelle le certificat seul et écrit les en-têtes
`X-Forwarded-*` :

```
tecpap.example.org {
    reverse_proxy 127.0.0.1:5000
}
```

Après la mise en place :

- renseigner `TRUST_PROXY_HEADERS=true` et `PUBLIC_BASE_URL=https://tecpap.example.org` dans `.env`,
  puis recréer le conteneur (section 11) ;
- ouvrir le pare-feu du serveur pour les ports 80 et 443 seulement : le port 5000 reste local ;
- vérifier que la page de connexion s'affiche en HTTPS et que la réponse contient l'en-tête
  `Strict-Transport-Security` (envoyé uniquement sur les réponses servies en HTTPS).

## 6. WhatsApp avec Twilio

L'application reçoit les messages WhatsApp par un webhook (adresse appelée par Twilio à chaque
message) et répond par l'API REST de Twilio.

### Réglages dans `.env`

| Variable | Valeur |
|---|---|
| `TWILIO_ACCOUNT_SID` | Identifiant du compte (commence par `AC`) |
| `TWILIO_AUTH_TOKEN` | Jeton d'authentification du compte |
| `TWILIO_WHATSAPP_NUMBER` | `whatsapp:` suivi du numéro d'envoi au format international (défaut : numéro du bac à sable) |
| `PUBLIC_BASE_URL` | Adresse publique HTTPS de l'application, sans `/` final |
| `TWILIO_VALIDATE_SIGNATURE` | `true` |
| `WHATSAPP_ASYNC_REPLY` | `true` (défaut) |
| `WHATSAPP_RATE_LIMIT_PER_MINUTE` | Nombre maximal de messages traités par minute et par numéro (défaut : 10) |

L'adresse du webhook est `PUBLIC_BASE_URL` suivie de `/webhook/whatsapp`, par exemple
`https://tecpap.example.org/webhook/whatsapp`. La page « WhatsApp » du tableau de bord l'affiche dès
que `PUBLIC_BASE_URL` est renseignée.

### Bac à sable Twilio (essais)

1. Dans la console Twilio, ouvrir la section WhatsApp du bac à sable (Messaging, puis Try it out).
2. Renseigner l'adresse du webhook dans le champ « When a message comes in », avec la méthode `POST`.
3. Garder `TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886`, numéro du bac à sable.
4. Depuis chaque téléphone de test, envoyer au numéro du bac à sable le message `join` suivi du mot
   proposé par la console. Sans cette étape, Twilio ne délivre pas les messages.

Les libellés de la console Twilio peuvent évoluer ; la page « WhatsApp » du tableau de bord rappelle
l'adresse à déclarer.

### Numéro WhatsApp de production

1. Faire valider auprès de Twilio et de Meta un expéditeur WhatsApp (numéro dédié et profil de
   l'entreprise). Cette démarche est menée dans la console Twilio ; sa durée dépend de la validation.
2. Renseigner `TWILIO_WHATSAPP_NUMBER=whatsapp:<numéro de l'expéditeur>` dans `.env`.
3. Déclarer l'adresse du webhook dans la configuration de cet expéditeur (champ qui reçoit les
   messages entrants), avec la méthode `POST`.
4. Recréer le conteneur (section 11), puis envoyer un message de test.

WhatsApp n'autorise les messages libres que pendant une fenêtre de 24 heures après le dernier message
du client ; au-delà, seuls des modèles de message approuvés sont acceptés. Une notification de
validation ou de rejet envoyée plus tard peut donc être refusée par WhatsApp : le détail est dans le
journal du serveur.

### Signature des requêtes

Twilio signe chaque requête avec le jeton d'authentification du compte (en-tête `X-Twilio-Signature`).
L'application recalcule la signature à partir de l'adresse que Twilio a appelée, c'est-à-dire
`PUBLIC_BASE_URL` suivi du chemin et des paramètres, et des champs du formulaire. Une requête sans
signature valide est refusée (403) et enregistrée comme événement de sécurité (nature
`invalid_signature`) sur la page « Sécurité » du tableau de bord. Pour que la signature concorde :

- `PUBLIC_BASE_URL` doit être exactement l'adresse déclarée chez Twilio (même protocole, même nom de
  domaine, pas de `/` final) ;
- `TWILIO_AUTH_TOKEN` doit être le jeton du compte qui envoie les requêtes ;
- le proxy inverse doit transmettre le chemin et les paramètres sans les modifier.

### Vérification

1. Envoyer « bonjour » depuis un téléphone enregistré : l'assistant répond.
2. Ouvrir la page « Conversations » du tableau de bord : la conversation apparaît.
3. En cas de silence, consulter `docker logs tecpap` et la page « Sécurité » (section 12).

## 7. Boîte Gmail

L'application lit les emails par IMAP (`imap.gmail.com`, port 993) et envoie les notifications par
SMTP (`smtp.gmail.com`, port 587, STARTTLS).

1. Activer la validation en deux étapes sur le compte Google de la boîte des commandes.
2. Vérifier que l'accès IMAP est activé dans les paramètres Gmail.
3. Créer un mot de passe d'application dans les paramètres de sécurité du compte Google : c'est un
   code de 16 caractères propre à l'application, distinct du mot de passe du compte, que l'on peut
   révoquer sans le changer.
4. Renseigner `GMAIL_EMAIL` (adresse de la boîte) et `GMAIL_APP_PASSWORD` (ce code) dans `.env`, puis
   recréer le conteneur.

Le traitement des emails se lance à la demande :

- depuis la page « Emails » du tableau de bord, bouton « Démarrer le traitement » (10 emails au plus
  par passage) ;
- depuis la ligne de commande, par exemple pour une tâche planifiée :

```bash
docker exec tecpap python scripts/process_emails.py --max 20
```

Le script lit les emails arrivés depuis le passage précédent, du plus ancien au plus récent. Ne pas
lancer le script pendant un traitement lancé depuis le tableau de bord : le verrou qui évite deux
passages simultanés ne vaut que dans un même processus.

Exemple de tâche planifiée `cron` du serveur, toutes les 15 minutes :

```
*/15 * * * * docker exec tecpap python scripts/process_emails.py --max 20
```

Le mot de passe d'application et l'adresse ne doivent figurer que dans `.env`.

## 8. Base de connaissances de l'assistant

L'assistant répond aux questions des clients à partir des documents Markdown du dossier
`knowledge/` (génération augmentée par la recherche, voir [rag.md](rag.md)). Ces documents sont
découpés en extraits, dont l'index (texte et vecteurs de recherche) est stocké dans la table
`rag_chunks` de la base SQLite.

### Indexation dans le conteneur

- Au démarrage, quand `RAG_ENABLED=true` (défaut) et que le dossier `KNOWLEDGE_DIR` existe,
  l'application actualise l'index. L'opération est répétable sans risque : seuls les extraits
  ajoutés ou modifiés sont recalculés. Le journal contient une ligne
  « Base de connaissances indexée : N extraits indexés (… ajoutés, … mis à jour, … supprimés, … inchangés) ».
- La page « Base de connaissances » du tableau de bord affiche les documents indexés, permet de
  tester une recherche et porte le bouton « Réindexer ». La case « force » recalcule tous les
  vecteurs.
- Depuis la ligne de commande :

```bash
docker exec tecpap python scripts/build_knowledge_index.py
docker exec tecpap python scripts/build_knowledge_index.py --force
```

Le script affiche le bilan : extraits ajoutés, mis à jour, supprimés et inchangés. Ses autres options
(`--local`, `--db`, `--knowledge-dir`) sont décrites dans l'en-tête du script ; l'option `--evaluate`
demande un fichier de questions annotées qui n'est pas dans l'image.

### Modifier les documents

Dans l'image, `knowledge/` est une copie faite à la construction, dans `/app/knowledge` : ce n'est
pas un volume. Une modification faite à l'intérieur du conteneur est perdue à la prochaine
recréation. Deux méthodes conservent les modifications :

| Méthode | Procédure |
|---|---|
| Reconstruire l'image | Modifier les fichiers dans le dépôt, puis `docker build -t tecpap-orders .` et recréer le conteneur (section 11). L'index est actualisé au démarrage |
| Monter le dossier de l'hôte | Ajouter `-v "$PWD/knowledge:/app/knowledge:ro"` à la commande `docker run`. Après chaque modification, cliquer sur « Réindexer », lancer `docker exec tecpap python scripts/build_knowledge_index.py` ou redémarrer le conteneur (`docker restart tecpap`) |

La rédaction des documents est décrite dans `knowledge/_guide_redaction.md`.

### Calcul des vecteurs

`RAG_EMBEDDER=local` (défaut) calcule les vecteurs sur le serveur : hors ligne, gratuit, identique à
chaque calcul. `RAG_EMBEDDER=openai` utilise le modèle `EMBEDDING_MODEL` de l'API OpenAI et nécessite
la clé `OPENAI_API_KEY`. Un changement de valeur recalcule les vecteurs de l'index au démarrage
suivant.

## 9. Sauvegardes et restauration

### Ce qui est sauvegardé

| Donnée | Sauvegardée par | Remarque |
|---|---|---|
| Base `orders.db` (commandes, clients, conversations, événements de sécurité, index de la base de connaissances) | Sauvegardes de la base | Copie cohérente même pendant que l'application écrit |
| Médias (`/data/media`) | Copie du volume `tecpap-media` (ci-dessous) | Pièces jointes des emails, notes vocales, images, PDF |
| Réglages et secrets | Copie du fichier `.env`, hors du serveur | Les secrets ne sont pas dans les sauvegardes |

### Sauvegardes automatiques

Un planificateur intégré à l'application crée une sauvegarde compressée (gzip) toutes les
`BACKUP_INTERVAL_HOURS` heures (défaut : 6) et ne garde que les `BACKUP_KEEP` plus récentes (défaut :
20). Les sauvegardes ont lieu aux heures rondes multiples de l'intervalle (0 h, 6 h, 12 h et 18 h pour
6 heures), à l'heure du serveur du conteneur, qui est l'heure UTC sauf réglage contraire. Aucune
sauvegarde n'est faite immédiatement au démarrage.

Les fichiers du dossier `/data/backups` sont nommés :

| Fichier | Contenu |
|---|---|
| `backup_AAAAMMJJ_HHMMSS_mmmmmm.db.gz` | Sauvegarde de la base (heure UTC) |
| `backup_AAAAMMJJ_HHMMSS_mmmmmm_pre_restore.db.gz` | Copie de la base faite juste avant une restauration |
| `export_*.json` | Export complet de la base en JSON (trois conservés) |

Les copies faites avant une restauration comptent dans la limite `BACKUP_KEEP` comme les autres
sauvegardes.

### Page « Sauvegardes » du tableau de bord

La page permet de créer une sauvegarde, de télécharger ou de supprimer un fichier, de restaurer la base,
d'exporter la base en JSON (envoyé en téléchargement, sans copie conservée sur le serveur) et de
régler l'intervalle (1 à 24 heures) et le nombre de sauvegardes conservées. Ce réglage vaut jusqu'au
redémarrage : au démarrage, `BACKUP_INTERVAL_HOURS` et `BACKUP_KEEP` reprennent effet.

### Ligne de commande

```bash
docker exec tecpap python scripts/backup.py backup
docker exec tecpap python scripts/backup.py backup --no-compress
docker exec tecpap python scripts/backup.py list
docker exec tecpap python scripts/backup.py stats
docker exec tecpap python scripts/backup.py clean 20
docker exec tecpap python scripts/backup.py export
```

`clean N` ne garde que les N sauvegardes les plus récentes (10 par défaut quand N est omis, contre 20
pour le planificateur) ; au-delà de 5 suppressions, une confirmation est demandée, que `--yes` évite.
Elle supprime aussi les exports JSON les plus anciens.

### Copie hors du serveur

Les sauvegardes stockées sur le serveur ne protègent pas d'une perte du serveur. Copier
régulièrement `/data/backups` ailleurs, sur un support chiffré :

```bash
docker cp tecpap:/data/backups/. ./copie-sauvegardes/
```

Copie du volume des médias :

```bash
docker run --rm -v tecpap-media:/data/media -v "$PWD":/sauvegarde python:3.11-slim \
  tar czf /sauvegarde/media.tar.gz -C /data media
```

Une sauvegarde et un export JSON contiennent les mêmes données personnelles que la base (noms, numéros
de téléphone, adresses email) : leur accès est restreint comme celui de la base.

### Restauration

Depuis le tableau de bord (application en marche) : page « Sauvegardes », bouton de restauration du
fichier choisi. Dans l'ordre, l'application vérifie que la sauvegarde est lisible, met son schéma à
niveau (une sauvegarde faite par une version plus ancienne est acceptée), copie la base actuelle dans
un fichier `..._pre_restore.db.gz` pour pouvoir revenir en arrière, ferme les connexions ouvertes puis
remplace la base.

Depuis la ligne de commande, application arrêtée :

```bash
docker stop tecpap
docker run --rm -it --env-file .env \
  -v tecpap-db:/data/db -v tecpap-backups:/data/backups \
  tecpap-orders python scripts/backup.py restore
docker start tecpap
```

Le script liste les sauvegardes, demande le numéro de celle à restaurer, puis une confirmation
(« oui »). L'index de la base de connaissances fait partie de la base : il est actualisé au démarrage.

Pour restaurer sur un autre serveur : construire ou charger l'image, copier les fichiers de
sauvegarde dans le volume `tecpap-backups`, restaurer la base par les commandes ci-dessus, puis
remettre les médias en place :

```bash
docker run --rm -v tecpap-backups:/data/backups -v "$PWD/copie-sauvegardes":/source:ro \n  tecpap-orders cp -r /source/. /data/backups/
docker run --rm -v tecpap-media:/data/media -v "$PWD":/sauvegarde:ro \n  tecpap-orders tar xzf /sauvegarde/media.tar.gz -C /data
```

## 10. Journaux et surveillance

L'application écrit ses journaux sur la sortie d'erreur et gunicorn son journal d'accès sur la sortie
standard : `docker logs` affiche les deux.

```bash
docker logs -f --tail 100 tecpap
```

Chaque ligne a la forme `AAAA-MM-JJ HH:MM:SS NIVEAU [fil d'exécution] module : message`. Le fil
d'exécution distingue les requêtes des tâches de fond (traitement différé des messages WhatsApp,
sauvegardes).

| Réglage | Effet |
|---|---|
| `LOG_LEVEL=INFO` (défaut) | Fonctionnement normal : démarrage, sauvegardes, messages reçus (numéro masqué) |
| `LOG_LEVEL=WARNING` | Seulement les avertissements et les erreurs |
| `LOG_LEVEL=DEBUG` | Détail pour un diagnostic, à n'utiliser que le temps nécessaire |

Les journaux des bibliothèques Twilio et OpenAI sont limités au niveau WARNING, sauf avec `DEBUG` : à
ce niveau, une ligne d'appel de l'API Twilio contient l'identifiant du compte.

Autres sources de suivi :

- page « Sécurité » du tableau de bord : signatures Twilio refusées, messages suspects, dépassements de
  la limite de débit. Les événements sont conservés 180 jours (100 000 au plus) et les numéros y sont
  masqués ;
- page « Conversations » : conversations transférées à un conseiller ;
- état du conteneur : `docker ps` (colonne STATUS : `healthy` ou `unhealthy`) ;
- taille des journaux : sans l'option `--log-opt` de la section 4, Docker n'en limite pas la taille.

## 11. Changement de version

Avant chaque changement de version, créer une sauvegarde :

```bash
docker exec tecpap python scripts/backup.py backup
```

### Depuis le code source

```bash
git pull
docker build -t tecpap-orders .
docker stop tecpap
docker rm tecpap
```

puis relancer la commande `docker run` de la section 4. Les volumes conservent les données ; au
démarrage, l'application ajoute à la base les colonnes, index et tables qui manquent.

### Depuis l'image publiée par GitHub Actions

Une étiquette Git de la forme `v*` déclenche le workflow « Publication » : tests, contrôle des
vulnérabilités des dépendances, construction de l'image, démarrage et sonde de `/healthz`, puis
publication de l'image comme artefact du workflow (conservé 30 jours).

```bash
git tag v3.0.0
git push origin v3.0.0
```

Aucun registre d'images n'est configuré. Télécharger l'artefact `tecpap-orders-v3.0.0` depuis l'onglet
Actions de GitHub, le décompresser pour obtenir `tecpap-orders.tar.gz`, puis :

```bash
docker load -i tecpap-orders.tar.gz
```

L'image porte alors l'étiquette `tecpap-orders:v3.0.0` : l'utiliser à la place de `tecpap-orders` dans
la commande `docker run`.

### Retour en arrière

Relancer le conteneur avec l'image précédente. Si la version installée a modifié la base de manière
indésirable, restaurer la sauvegarde faite avant le changement de version (section 9).

### Modifier un réglage

Une variable de `.env` ne s'applique qu'à la création du conteneur : après la modification, exécuter
`docker stop tecpap`, `docker rm tecpap`, puis la commande `docker run` de la section 4.

## 12. Dépannage

Messages et avertissements cités : ceux écrits par l'application dans le journal (`docker logs tecpap`).

| Symptôme | Cause probable | Action |
|---|---|---|
| Le conteneur s'arrête au démarrage avec « SECRET_KEY est obligatoire en production » | `SECRET_KEY` absente ou vide | Générer une clé (section 2) et la renseigner dans `.env` |
| « SECRET_KEY est trop courte » | Moins de 32 caractères | Générer une clé de 64 caractères |
| « SECRET_KEY contient la valeur d'exemple » | Valeur de `.env.example` conservée | Remplacer par une clé générée |
| « FLASK_DEBUG=true est refusé en production » | Mode débogage activé | Mettre `FLASK_DEBUG=false` |
| « Base de données ... impossible à ouvrir » | `DATABASE_PATH` définie dans `.env`, ou dossier de l'hôte non accessible | Retirer la variable ; pour un dossier de l'hôte, `chown -R 10001:10001` |
| Données perdues après `docker rm` | Volumes non nommés | Relancer avec les trois `-v` de la section 4 ; restaurer une sauvegarde |
| « ADMIN_PASSWORD_HASH n'est pas défini » | Empreinte absente | La produire (section 2) et la renseigner |
| « ADMIN_PASSWORD_HASH ne ressemble pas à une empreinte » | Empreinte tronquée par le terminal (`$` remplacés) | Écrire l'empreinte dans `.env`, ne pas la passer avec `-e` sans apostrophes |
| Le mot de passe est accepté puis la page de connexion revient | Cookie de session sécurisé reçu en HTTP | Accéder en HTTPS ; pour un essai en HTTP, `SESSION_COOKIE_SECURE=false` |
| Tous les utilisateurs sont bloqués après quelques échecs | `TRUST_PROXY_HEADERS` absent derrière un proxy | Mettre `TRUST_PROXY_HEADERS=true` et publier le port sur `127.0.0.1` |
| Le webhook répond 403 | Signature invalide | Comparer `PUBLIC_BASE_URL` à l'adresse déclarée chez Twilio ; vérifier `TWILIO_AUTH_TOKEN` ; consulter la page « Sécurité » |
| Le webhook répond 503 | `TWILIO_AUTH_TOKEN` absent avec la signature activée | Renseigner le jeton |
| Aucun message WhatsApp reçu | Webhook non déclaré, ou téléphone absent du bac à sable | Vérifier l'adresse chez Twilio et le message `join` |
| Messages reçus mais aucune réponse | Identifiants Twilio absents ou envoi refusé | Consulter `docker logs tecpap` ; vérifier `TWILIO_ACCOUNT_SID` et `TWILIO_AUTH_TOKEN` |
| « La boîte Gmail n'a pas pu être lue » | Identifiants refusés ou IMAP désactivé | Recréer le mot de passe d'application ; activer IMAP |
| Aucune commande extraite d'un email | Clé OpenAI absente ou refusée | Vérifier `OPENAI_API_KEY` ; sans clé, l'extraction par règles marque les champs à vérifier |
| Le conteneur est `unhealthy` | `/healthz` répond 503 : base illisible | Consulter le journal ; vérifier les droits sur `/data/db` et l'espace disque |
| Messages interrompus à l'arrêt | Arrêt trop rapide du conteneur | Utiliser `--stop-timeout 130` ; les messages sont repris au démarrage |
| « sauvegardes automatiques ... non démarrées » | `FLASK_DEBUG=true` | Mettre `FLASK_DEBUG=false` |
| Les documents modifiés ne changent pas les réponses | `knowledge/` figé dans l'image, ou index non actualisé | Reconstruire l'image ou monter le dossier, puis « Réindexer » (section 8) |
| Tableau de bord sans style ni graphiques | Poste sans accès aux CDN autorisés par la politique de sécurité | Autoriser l'accès à `cdn.tailwindcss.com`, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `fonts.googleapis.com` et `fonts.gstatic.com` |
| Réponse 413 | Corps de requête de plus de 1 Mo | Vérifier ce qui appelle l'application (le webhook de Twilio n'envoie pas de fichier) |
