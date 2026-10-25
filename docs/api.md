# Interface HTTP de TECPAP

Ce document décrit les 54 routes HTTP de l'application web TECPAP (version 3.0.0), regroupées par blueprint (ensemble de routes enregistré sur l'application Flask). La liste est celle que construit `create_app` (`app.url_map`). L'organisation du code est décrite dans [architecture.md](architecture.md), la sécurité du tableau de bord dans [securite.md](securite.md) et le déploiement dans [deploiement.md](deploiement.md).

Les routes servent le tableau de bord de l'équipe commerciale (pages HTML et appels JSON du navigateur) et reçoivent les messages WhatsApp envoyés par Twilio. Il n'existe pas d'API publique destinée à des programmes tiers : toutes les routes exigent une session ouverte, sauf la connexion, la sonde de disponibilité, les fichiers statiques et le webhook Twilio.

## 1. Conventions

### 1.1 Authentification

| Mode | Routes | Fonctionnement |
|---|---|---|
| Session | Toutes les routes, sauf celles des deux lignes suivantes | Cookie de session signé (`HttpOnly`, `SameSite=Lax`, `Secure` en production), valable 12 heures après la connexion |
| Publique | `GET` et `POST /login`, `GET /healthz`, `GET /static/<chemin>` | Aucune session requise |
| Signature Twilio | `POST /webhook/whatsapp` | En-tête `X-Twilio-Signature` vérifié avant tout traitement |

Avec `AUTH_ENABLED=false`, toutes les routes sont accessibles sans connexion (usage de développement). Il n'existe qu'un compte, celui de l'administrateur (`ADMIN_USERNAME`, empreinte du mot de passe dans `ADMIN_PASSWORD_HASH`).

Une requête sans session valide reçoit :

- sous `/api/` : la réponse 401 `{"success": false, "error": "Authentification requise : connectez-vous au tableau de bord.", "login_url": "/login"}` ;
- ailleurs : la redirection 302 vers `/login?next=<chemin relatif demandé>`. Cette règle s'applique aussi à une adresse inconnue ou à une méthode non prévue.

### 1.2 Protection CSRF

La falsification de requête intersite (CSRF, *Cross-Site Request Forgery*) consiste à faire envoyer par le navigateur d'un utilisateur connecté une requête qu'il n'a pas voulue. Toute requête `POST`, `PUT`, `PATCH` ou `DELETE` doit donc renvoyer le jeton de la session :

- dans l'en-tête `X-CSRF-Token` (ajouté aux appels `fetch` par le script de `base.html`) ;
- ou, pour un formulaire HTML, dans le champ `csrf_token` (type `application/x-www-form-urlencoded` ou `multipart/form-data`).

Le webhook Twilio en est dispensé : il n'est pas appelé par un navigateur. Les requêtes `GET` ne modifient jamais de données et n'exigent pas de jeton.

Sans jeton valide, la réponse est 403 : `{"success": false, "error": "Jeton de sécurité (CSRF) absent ou invalide : rechargez la page puis recommencez."}` sous `/api/`, une page HTML « Requête refusée » ailleurs. `CSRF_ENABLED=false` désactive ce contrôle (usage de développement).

### 1.3 Ordre des contrôles

Pour chaque requête, l'application vérifie dans cet ordre : la connexion à la base, la session, le jeton CSRF, puis exécute la route. Le contrôle de session précède donc celui du jeton, et une route inexistante n'est jamais révélée à un visiteur anonyme.

### 1.4 Formats

| Élément | Règle |
|---|---|
| Corps JSON | Lu avec tolérance : un corps absent, mal formé ou qui n'est pas un objet est traité comme un objet vide |
| Taille du corps | 1 Mo au plus (`MAX_REQUEST_BYTES`) ; au-delà, réponse 413 avant toute lecture |
| Réponse d'erreur JSON | `{"success": false, "error": "<message en français>"}`, plus des champs propres à la route (`status`, `rejected_fields`) |
| Réponse de succès JSON | `{"success": true, ...}` pour les routes d'action ; les routes de lecture renvoient directement les données |
| Texte d'une exception | Jamais renvoyé : l'exception est consignée dans le journal et la réponse porte un message générique (erreur 500) |
| Identifiants dans le chemin | Entiers (`<int:...>`) ; un chemin non entier donne 404 |
| En-têtes de sécurité | Ajoutés à toutes les réponses (`Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, et `Strict-Transport-Security` en HTTPS) |

### 1.5 Erreurs communes à toutes les routes

| Code | Cas | Réponse |
|---|---|---|
| 400 | Requête invalide (erreur HTTP non traitée par la route) | JSON sous `/api/`, page HTML ailleurs |
| 401 | Session absente, sous `/api/` | JSON avec `login_url` |
| 403 | Jeton CSRF absent ou invalide | JSON sous `/api/`, page HTML ailleurs |
| 404 | Adresse inexistante (session ouverte) | JSON sous `/api/`, page HTML ailleurs |
| 405 | Méthode non autorisée (session ouverte) | JSON sous `/api/`, page HTML ailleurs ; en-tête `Allow` conservé |
| 413 | Corps de plus de 1 Mo | Page HTML |
| 500 | Erreur interne | Message générique, détail dans le journal |

## 2. Récapitulatif des routes

| Méthode | Chemin | Blueprint | Authentification | CSRF |
|---|---|---|---|---|
| GET, POST | `/login` | `auth` | Publique | Oui, pour le `POST` |
| POST | `/logout` | `auth` | Session | Oui |
| GET | `/` | `pages` | Session | Non |
| GET | `/orders` | `pages` | Session | Non |
| GET | `/orders/<int:order_id>` | `pages` | Session | Non |
| GET | `/process` | `pages` | Session | Non |
| GET | `/analytics` | `pages` | Session | Non |
| GET | `/clients` | `pages` | Session | Non |
| GET | `/clients/<int:client_id>` | `pages` | Session | Non |
| GET | `/alerts` | `pages` | Session | Non |
| GET | `/whatsapp` | `pages` | Session | Non |
| GET | `/sage` | `pages` | Session | Non |
| GET | `/conversations` | `assistant` | Session | Non |
| GET | `/conversations/<int:conversation_id>` | `assistant` | Session | Non |
| POST | `/conversations/<int:conversation_id>/reset` | `assistant` | Session | Oui |
| POST | `/conversations/<int:conversation_id>/reply` | `assistant` | Session | Oui |
| GET | `/knowledge` | `assistant` | Session | Non |
| POST | `/knowledge/reindex` | `assistant` | Session | Oui |
| GET | `/security` | `assistant` | Session | Non |
| GET | `/healthz` | `api` | Publique | Non |
| POST | `/api/process-emails` | `api` | Session | Oui |
| POST | `/api/orders/<int:order_id>/validate` | `api` | Session | Oui |
| POST | `/api/orders/<int:order_id>/reject` | `api` | Session | Oui |
| POST | `/api/orders/<int:order_id>/notify` | `api` | Session | Oui |
| POST | `/api/orders/<int:order_id>/update` | `api` | Session | Oui |
| PUT | `/api/orders/<int:order_id>/sage-fields` | `api` | Session | Oui |
| POST | `/api/orders/<int:order_id>/generate-code` | `api` | Session | Oui |
| POST | `/api/orders/generate-all-codes` | `api` | Session | Oui |
| GET | `/api/orders` | `api` | Session | Non |
| GET | `/api/orders/sage-stats` | `api` | Session | Non |
| POST | `/api/article-code/generate` | `api` | Session | Oui |
| POST | `/api/article-code/suggest` | `api` | Session | Oui |
| GET | `/api/stats` | `api` | Session | Non |
| GET | `/api/analytics` | `api` | Session | Non |
| GET | `/api/alerts` | `api` | Session | Non |
| GET | `/api/client/<int:client_id>/history` | `api` | Session | Non |
| GET | `/api/notifications/check` | `api` | Session | Non |
| GET | `/api/whatsapp/status` | `api` | Session | Non |
| POST | `/webhook/whatsapp` | `webhooks` | Signature Twilio | Non (dispensé) |
| GET | `/backups` | `backups` | Session | Non |
| POST | `/api/backup/create` | `backups` | Session | Oui |
| GET | `/api/backup/list` | `backups` | Session | Non |
| GET | `/api/backup/download/<filename>` | `backups` | Session | Non |
| POST | `/api/backup/download-latest` | `backups` | Session | Oui |
| POST | `/api/backup/restore/<filename>` | `backups` | Session | Oui |
| DELETE | `/api/backup/delete/<filename>` | `backups` | Session | Oui |
| POST | `/api/backup/export-json` | `backups` | Session | Oui |
| GET | `/api/backup/scheduler/status` | `backups` | Session | Non |
| POST | `/api/backup/scheduler/configure` | `backups` | Session | Oui |
| GET | `/export/excel` | `exports` | Session | Non |
| GET | `/export/excel/sage` | `exports` | Session | Non |
| GET | `/export/csv` | `exports` | Session | Non |
| GET | `/export/pdf` | `exports` | Session | Non |
| GET | `/static/<path:filename>` | `static` (Flask) | Publique | Non |

Les méthodes `HEAD` et `OPTIONS` sont ajoutées automatiquement par Flask et ne sont pas listées.

## 3. Blueprint `auth` : connexion et déconnexion

### `GET /login`

| Élément | Valeur |
|---|---|
| Authentification | Publique |
| Paramètre de requête | `next` (facultatif) : chemin relatif où revenir après la connexion. Une adresse absolue, sans protocole (`//site`), avec barre oblique inversée ou caractère de contrôle est ignorée (protection contre la redirection ouverte) |
| 200 | Page HTML de connexion |
| 302 | Vers `next` (ou `/`) si une session valide existe déjà, ou si `AUTH_ENABLED=false` |

### `POST /login`

| Élément | Valeur |
|---|---|
| Authentification | Publique |
| CSRF | Oui : champ `csrf_token` du formulaire (le jeton est créé à l'affichage de la page de connexion) |
| Corps | Formulaire : `username` (150 caractères au plus), `password`, `next` (facultatif) |
| 302 | Connexion réussie : vers `next` ou `/`. La session précédente est effacée et un jeton CSRF est créé |
| 401 | Page de connexion : « Identifiant ou mot de passe incorrect. » |
| 429 | Page de connexion avec l'en-tête `Retry-After` (secondes). Deux limites : après `LOGIN_MAX_ATTEMPTS` échecs depuis une même adresse IP, l'adresse est bloquée `LOGIN_LOCKOUT_MINUTES` minutes ; au-delà de 4 fois `LOGIN_MAX_ATTEMPTS` échecs sur un même identifiant (toutes adresses confondues), chaque essai doit attendre un délai qui double à chaque échec, de 60 secondes au plus |
| 403 | Jeton CSRF absent ou invalide |
| 413 | Corps de plus de 1 Mo |

L'identifiant est comparé en temps constant et le mot de passe est toujours vérifié, pour que la durée de la réponse ne révèle pas lequel des deux est faux. Sans empreinte de mot de passe configurée, aucune connexion n'est possible.

### `POST /logout`

| Élément | Valeur |
|---|---|
| Authentification | Session |
| CSRF | Oui |
| Corps | Aucun |
| 302 | Vers `/login` (vers `/` si `AUTH_ENABLED=false`) ; la session est effacée |

## 4. Blueprint `pages` : pages du tableau de bord

Toutes ces routes sont des `GET` qui renvoient une page HTML (200), exigent une session et n'exigent pas de jeton CSRF. Les erreurs communes (section 1.5) s'appliquent.

| Chemin | Contenu | Paramètres de requête | Cas particuliers |
|---|---|---|---|
| `/` | Indicateurs, cinq dernières commandes, meilleurs clients et produits, tendance sur 7 jours | Aucun | |
| `/orders` | Liste des commandes | `status` (par exemple `en_attente`, `validee` ou `rejetee`), `source` (`whatsapp` ou `email`, autre valeur ignorée), `q` (100 caractères au plus : référence « CMD-42 » ou « #42 », numéro de commande, nom ou email du client, numéro de téléphone), `conversation` (identifiant d'une conversation : commandes de ce numéro) | |
| `/orders/<order_id>` | Fiche de la commande, avec contrôle de l'extraction, modification, validation et rejet | Aucun | 302 vers `/orders` si la commande n'existe pas |
| `/process` | Lancement du traitement des emails, état de la configuration Gmail et du modèle de langue | Aucun | Aucune connexion à la boîte n'est tentée à l'affichage |
| `/analytics` | Statistiques détaillées et alertes | Aucun | |
| `/clients` | Liste des clients | Aucun | |
| `/clients/<client_id>` | Fiche client, historique, estimation de la prochaine commande | Aucun | 302 vers `/clients` si le client n'existe pas |
| `/alerts` | Alertes en cours | Aucun | |
| `/whatsapp` | Commandes WhatsApp, statistiques du canal, adresse du webhook | Aucun | |
| `/sage` | Préparation de l'export SAGE X3 | Aucun | |

## 5. Blueprint `assistant` : conversations, base de connaissances, sécurité

### `GET /conversations`

Liste des conversations WhatsApp : d'abord celles transférées à l'équipe commerciale, puis les plus récentes (100 au plus). Signale les messages reçus encore en attente ou en échec dans la file durable.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| Paramètre de requête | `handoff=1` : conversations transférées seulement |
| 200 | Page HTML |

### `GET /conversations/<conversation_id>`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| 200 | Fiche : 200 derniers messages au plus, commande en cours de saisie, commandes du numéro, actions de l'équipe |
| 302 | Vers `/conversations` si la conversation n'existe pas |

### `POST /conversations/<conversation_id>/reset`

Termine la prise en charge : l'état redevient `idle`, sans transfert ni commande en cours, et l'assistant reprend les réponses au client.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui (champ `csrf_token`) |
| Corps | Aucun |
| 302 | Vers la fiche, avec un message de confirmation. Si un message du même expéditeur est en cours de traitement au-delà de 30 secondes, la remise à zéro n'a pas lieu et un message d'erreur invite à réessayer |
| 404 | Page HTML : conversation inexistante |

### `POST /conversations/<conversation_id>/reply`

Envoie par WhatsApp un message de l'équipe commerciale et l'enregistre dans la conversation (intention `agent`). La conversation reste transférée.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui (champ `csrf_token`) |
| Corps | Formulaire : `message` (texte, 1 600 caractères au plus) |
| 302 | Vers la fiche, avec un message de confirmation |
| 400 | Fiche réaffichée avec une erreur : message vide, ou message trop long (le texte saisi est conservé) |
| 502 | Fiche réaffichée avec une erreur : envoi WhatsApp impossible (Twilio non configuré ou indisponible) |
| 404 | Conversation inexistante |

### `GET /knowledge`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| Paramètre de requête | `q` (facultatif, 1 000 caractères au plus) : question de test, qui affiche les extraits trouvés, leurs scores et la décision de réponse |
| 200 | Documents indexés, état de l'index par rapport au dossier des documents, recherche de test |

### `POST /knowledge/reindex`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui (champ `csrf_token`) |
| Corps | Formulaire : `force=1` (facultatif) pour recalculer tous les vecteurs ; sinon seuls les extraits ajoutés ou modifiés sont traités |
| 302 | Toujours vers `/knowledge`, avec un message de succès ou d'erreur (dossier introuvable, échec de l'indexation) |

### `GET /security`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| Paramètre de requête | `kind` (facultatif) : une seule nature d'événement (`prompt_injection`, `output_blocked`, `rate_limited`, `invalid_signature`) |
| 200 | 200 derniers événements de sécurité au plus, et décompte par nature sur les 7 derniers jours |

## 6. Blueprint `api` : API JSON du tableau de bord

Sauf `GET /healthz`, ces routes exigent une session ; les routes qui modifient des données exigent le jeton CSRF dans l'en-tête `X-CSRF-Token`. Les corps sont en JSON (`Content-Type: application/json`).

### 6.1 État du service

#### `GET /healthz`

Sonde de disponibilité utilisée par le conteneur Docker. La base est lue sur la connexion du fil courant.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Publique, non |
| 200 | `{"status": "ok", "version": "3.0.0"}` |
| 503 | `{"status": "error", "reason": "database", "version": "3.0.0"}` : base impossible à ouvrir ou illisible |

#### `GET /api/whatsapp/status`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| 200 | `{"connected": bool, "verified": bool, "phone_number": str ou null, "account_sid": str tronqué ou null}`. `connected` : identifiants Twilio renseignés ; `verified` : acceptés par Twilio |
| Échec | La réponse reste 200 : `{"connected": false, "error": "<message générique>"}` |

### 6.2 Traitement des emails

#### `POST /api/process-emails`

Lit les 10 emails les plus anciens non examinés de la boîte Gmail et enregistre les bons de commande détectés. Un seul traitement à la fois dans le processus.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Aucun |
| 200 | `{"success": true, "message": str, "orders_count": int, "remaining_emails": int, "failed_emails": int}` |
| 409 | Un traitement est déjà en cours |
| 502 | Boîte Gmail injoignable (identifiants manquants ou refusés) |
| 500 | Échec du traitement, message générique |

### 6.3 Validation, rejet et modification des commandes

Seule une commande « en attente » peut être validée ou rejetée : le changement de statut est une seule requête SQL conditionnelle (`WHERE statut = 'en_attente'`). De deux décisions simultanées sur la même commande, une seule aboutit ; l'autre reçoit l'erreur 409 et aucun second message n'est envoyé au client.

La validation et le rejet préviennent le client par email et, si la commande vient de WhatsApp, par un message WhatsApp rédigé à partir d'un modèle fixe, dans la langue de sa conversation. Le résultat est inscrit dans la table `logs` avec l'utilisateur connecté. Les réponses de succès de `validate`, `reject` et `notify` portent les mêmes champs de notification :

```json
{
  "success": true,
  "message": "Commande validée",
  "email_sent": true,
  "whatsapp_sent": false,
  "notifications": {"email": "sent", "whatsapp": "not_applicable"},
  "warning": null
}
```

| Champ | Valeurs |
|---|---|
| `notifications.email`, `notifications.whatsapp` | `sent` (envoyé), `failed` (échec), `not_applicable` (pas d'adresse email, ou commande non reçue par WhatsApp) |
| `warning` | Texte en français quand le client n'a pas été prévenu, sinon `null` |

L'échec d'une notification ne défait pas la décision : la commande reste validée ou rejetée.

#### `POST /api/orders/<order_id>/validate`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Facultatif : `{"validated_by": "<nom>"}` (100 caractères au plus). Sans ce champ, l'auteur est l'utilisateur connecté |
| 200 | Voir l'exemple ci-dessus, `message` : « Commande validée » |
| 404 | `{"success": false, "error": "Commande introuvable"}` |
| 409 | La commande n'est plus en attente : `{"success": false, "error": "...", "status": "<statut actuel>"}` |

La validation ne rattache jamais le numéro WhatsApp de l'expéditeur à la fiche du client : valider le contenu d'une commande ne prouve pas l'identité de celui qui l'a envoyée.

#### `POST /api/orders/<order_id>/reject`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Obligatoire : `{"reason": "<motif>"}` ; le motif est communiqué tel quel au client |
| 200 | `message` : « Commande rejetée », plus les champs de notification |
| 400 | Motif absent ou vide |
| 404 | Commande introuvable |
| 409 | La commande n'est plus en attente (champ `status`) |

#### `POST /api/orders/<order_id>/notify`

Renvoie au client la notification de la décision (validation, ou rejet avec son motif enregistré).

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Aucun |
| 200 | `message` : « Notification renvoyée », plus les champs de notification |
| 404 | Commande introuvable |
| 409 | Commande en attente ou supprimée : aucune décision à annoncer (champ `status`) |

#### `POST /api/orders/<order_id>/update`

Modifie les champs d'une commande. Le reste à livrer est recalculé si une quantité change.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Objet JSON dont les clés sont des champs de `ORDER_UPDATABLE_FIELDS` : `numero_commande`, `ligne_commande`, `site_vente`, `code_client`, `produit_id`, `code_article`, `nature_produit`, `quantite`, `unite`, `quantite_livree`, `reste_a_livrer`, `quantite_facturee`, `prix_unitaire`, `prix_total`, `devise`, `date_commande`, `date_livraison`, `commercial`, `type_sac`, `format_sac`, `type_papier`, `grammage`, `laize`, `impression_client`, `informations_supplementaires`, `motif_rejet`. Une valeur numérique peut être une chaîne (formulaire HTML) ; une chaîne vide efface la valeur |
| 200 | `{"success": true, "message": "<confirmation>"}` |
| 400 | Corps vide (« Aucun champ à modifier »), ou champ non modifiable ou valeur numérique invalide : `{"success": false, "error": "...", "rejected_fields": [...]}` |
| 404 | Commande introuvable |
| 409 | Commande validée, rejetée ou supprimée dont on change un champ annoncé au client (produit, quantité, unité, prix, devise, dates, numéro) : `rejected_fields` et `status` ; une valeur identique à l'actuelle est acceptée |
| 500 | Échec de l'enregistrement, message générique |

Le statut, le client, la confiance, la source, les traces de réception et la traçabilité de l'extraction ne sont pas modifiables par cette route.

#### `PUT /api/orders/<order_id>/sage-fields`

Modifie uniquement les champs SAGE X3 d'une commande. Une requête qui contient un autre champ est refusée en entier.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Objet JSON limité à : `code_article`, `code_client`, `site_vente`, `ligne_commande`, `quantite_livree`, `reste_a_livrer`, `quantite_facturee`, `type_sac`, `format_sac`, `type_papier`, `grammage`, `laize`, `impression_client`, `commercial` |
| 200 | `{"success": true, "message": "<confirmation>", "updated_fields": [...]}` ; le reste à livrer est recalculé si `quantite_livree` change |
| 400 | Corps vide (« Aucune donnée fournie »), champ hors liste (`rejected_fields`), ou valeur numérique invalide |
| 404 | Commande introuvable |
| 500 | Échec de l'enregistrement, message générique |

### 6.4 Codes articles

#### `POST /api/article-code/generate`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | `{"paper_type": str, "grammage": nombre, "laize": nombre, "supplier": str}` (tous facultatifs) |
| 200 | `{"success": true, "code": str ou null, "paper_type": ..., "grammage": ..., "laize": ...}`. Exemple de code : `KB100L28MON` (kraft blanchi, 100 g/m², laize 28 cm, papier Mondi) |
| 500 | Message générique |

#### `POST /api/article-code/suggest`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | `{"description": "<texte libre>"}` |
| 200 | `{"success": true, "code": str ou null, "description": str}` |
| 500 | Message générique |

#### `POST /api/orders/<order_id>/generate-code`

Calcule et enregistre le code article d'une commande, d'après son papier, son grammage et sa laize, à défaut d'après sa désignation.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Aucun |
| 200 | `{"success": true, "code": str, "message": "Code article <code> assigné"}` |
| 400 | « Impossible de générer un code, données insuffisantes » |
| 404 | Commande introuvable |
| 500 | Message générique |

#### `POST /api/orders/generate-all-codes`

Calcule le code article de toutes les commandes qui n'en ont pas.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Aucun |
| 200 | `{"success": true, "generated": int, "failed": int, "total_processed": int, "results": [{"id": int, "code": str ou null, "status": "success" ou "failed"}]}` ; `results` contient 50 entrées au plus |
| 500 | Message générique |

### 6.5 Lecture des données

Toutes ces routes sont des `GET` (session, sans jeton CSRF).

| Chemin | Paramètres | 200 | Erreurs |
|---|---|---|---|
| `/api/stats` | Aucun | Objet de compteurs : `total_orders`, `pending_orders`, `validated_orders`, `total_clients`, et par canal `email_total`, `email_pending`, `email_validated`, `whatsapp_total`, `whatsapp_pending`, `whatsapp_validated` | |
| `/api/orders` | Aucun | Liste de toutes les commandes, des plus récentes aux plus anciennes : colonnes de `commandes`, plus `client_nom`, `produit_type`, `anomalies_list`, `champs_manquants_list` | |
| `/api/orders/sage-stats` | Aucun | `{"success": true, "stats": {"total_orders", "with_code_article", "without_code_article", "validated", "complete_sage_fields", "completion_rate"}, "top_codes": [{"code_article", "count"}]}` (10 codes au plus) | 500 message générique |
| `/api/analytics` | Aucun | Indicateurs du tableau de bord : `total_orders`, `pending_orders`, `validated_orders`, `rejected_orders`, `orders_this_month`, `total_clients`, `total_quantity`, `total_revenue`, `avg_order_value`, `avg_quantity`, `orders_by_status`, `orders_by_product`, `daily_orders`, `monthly_trends`, `top_clients` | |
| `/api/alerts` | Aucun | Liste d'alertes `{"type", "severity", "message", "order_id"}` | |
| `/api/client/<client_id>/history` | Aucun | `{"preferences": {...}, "prediction": {...}}` : habitudes de commande et estimation de la prochaine commande | 404 « Client introuvable » |
| `/api/notifications/check` | `last_id` (entier) | `{"new_orders": [...], "last_id": int}` : au plus 10 commandes d'identifiant supérieur à `last_id`, avec `id`, `created_at`, `source`, `email_subject`, `client`, `produit`, `nature_produit`, `timestamp`. Sans `last_id` (absent, nul ou invalide), `new_orders` est vide et `last_id` est celui de la dernière commande : point de départ de l'interrogation | En cas d'échec, la réponse reste 200 : `{"new_orders": [], "error": "...", "last_id": 0}` |

Le navigateur interroge `/api/notifications/check` toutes les 3 secondes pour signaler l'arrivée d'une commande (alerte sonore et visuelle).

## 7. Blueprint `webhooks` : webhook WhatsApp

### `POST /webhook/whatsapp`

Reçoit les messages WhatsApp envoyés par Twilio. L'adresse à déclarer chez Twilio est `PUBLIC_BASE_URL` suivie de `/webhook/whatsapp`.

| Élément | Valeur |
|---|---|
| Authentification | Signature Twilio : en-tête `X-Twilio-Signature` (HMAC-SHA1, code d'authentification de message à clé, de l'URL appelée et des paramètres) vérifié avec `TWILIO_AUTH_TOKEN`. L'URL signée est `PUBLIC_BASE_URL` suivie du chemin quand cette variable est définie, sinon l'URL reçue. La vérification peut être désactivée par `TWILIO_VALIDATE_SIGNATURE=false` (déconseillé : n'importe qui pourrait appeler le webhook) |
| CSRF | Non : le webhook n'est pas appelé par un navigateur |
| Corps | Formulaire envoyé par Twilio (`application/x-www-form-urlencoded`) : `From` (« whatsapp:+212… »), `Body`, `MessageSid` (à défaut `SmsMessageSid`), `NumMedia`, `MediaUrl0`, `MediaContentType0`, `To` |
| 200, `application/xml` | Réponse TwiML (format XML de Twilio). Vide (`<Response />`) dans ces cas : traitement différé (`WHATSAPP_ASYNC_REPLY`, actif par défaut), expéditeur non reconnu, message déjà reçu (doublon de livraison), assistant qui ne répond pas. Avec un message (`<Message>`) quand la réponse est renvoyée directement, ou après une erreur de traitement (message d'erreur générique dans la langue du client) |
| 403, `text/plain` | « Signature invalide » : signature absente ou invalide. L'événement de sécurité `invalid_signature` est enregistré (numéro masqué, une fois par minute et par adresse IP) |
| 503, `text/plain` | « Service indisponible » : vérification activée sans `TWILIO_AUTH_TOKEN` |
| 413 | Corps de plus de 1 Mo, refusé avant toute lecture |

Déroulement d'un message signé, dans cet ordre :

1. déduplication par identifiant Twilio (`MessageSid`) : un message déjà reçu ne reçoit aucune réponse ;
2. enregistrement dans la file durable `whatsapp_inbox`, avant la réponse à Twilio : un message accepté n'est jamais perdu ;
3. traitement : en différé par une tâche de fond (une à la fois par expéditeur) avec réponse par l'API REST de Twilio, ou immédiat avec réponse dans le TwiML ;
4. contrôle de la limite de débit (`WHATSAPP_RATE_LIMIT_PER_MINUTE`) avant le téléchargement du média.

Le parcours complet est décrit dans [architecture.md](architecture.md), section 4, et le comportement de l'assistant dans [assistant_whatsapp.md](assistant_whatsapp.md).

Une requête `GET` ou toute autre méthode sur cette adresse sans session est redirigée vers `/login` ; avec une session, elle reçoit l'erreur 405.

## 8. Blueprint `backups` : sauvegardes

Un nom de fichier reçu dans l'URL n'est accepté que s'il ne contient que des lettres non accentuées, des chiffres, `_`, `.` et `-`, et s'il désigne un fichier situé directement dans le dossier des sauvegardes (liens symboliques résolus). Une action qui crée un fichier est une requête `POST`, soumise au jeton CSRF ; les requêtes `GET` ne font que lire.

### `GET /backups`

Page de gestion des sauvegardes. Authentification : session. Réponse : 200, page HTML (liste, statistiques de la base, état du planificateur).

### `POST /api/backup/create`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Facultatif : `{"compress": bool}` (vrai par défaut : fichier `.db.gz`) |
| 200 | `{"success": true, "message": "Sauvegarde créée avec succès", "path": str, "filename": str}` |
| 500 | `{"success": false, "message": "Erreur lors de la création"}`, ou message générique |

### `GET /api/backup/list`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| 200 | `{"success": true, "backups": [...], "db_stats": {"size", "tables"}, "scheduler": {...}}` |
| 500 | Message générique |

### `GET /api/backup/download/<filename>`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| 200 | Fichier de la sauvegarde, en pièce jointe |
| 400 | « Nom de fichier de sauvegarde invalide » |
| 404 | « Sauvegarde introuvable » |
| 500 | Message générique |

### `POST /api/backup/download-latest`

Crée une sauvegarde compressée et renvoie l'adresse de son téléchargement.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Aucun |
| 200 | `{"success": true, "message": "Sauvegarde créée", "filename": str, "download_url": "/api/backup/download/<nom>"}` ; le navigateur télécharge ensuite le fichier par `GET` |
| 500 | Échec de la création |

### `POST /api/backup/restore/<filename>`

Restaure la base depuis une sauvegarde. Le schéma de la sauvegarde est adapté à la version courante dans un fichier temporaire ; la base actuelle est copiée dans un fichier `…_pre_restore.db.gz` avant d'être remplacée, puis les connexions sont rouvertes.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Aucun |
| 200 | `{"success": true, "message": "Base de données restaurée depuis <nom>"}` |
| 400 | Nom de fichier invalide |
| 404 | Sauvegarde introuvable |
| 500 | `{"success": false, "message": "Erreur lors de la restauration"}`, ou message générique ; la base actuelle n'est pas modifiée si la sauvegarde est illisible |

### `DELETE /api/backup/delete/<filename>`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui (en-tête `X-CSRF-Token`) |
| Corps | Aucun |
| 200 | `{"success": true, "message": "Sauvegarde <nom> supprimée"}` |
| 400 | Nom de fichier invalide |
| 404 | Sauvegarde introuvable |
| 500 | Message générique |

### `POST /api/backup/export-json`

Exporte toute la base en JSON. Le fichier est construit dans un dossier temporaire supprimé aussitôt et envoyé depuis la mémoire : aucune copie en clair des données personnelles ne reste sur le serveur.

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | Aucun |
| 200 | Fichier `export_AAAAMMJJ_HHMMSS.json` en pièce jointe (`application/json`) |
| 500 | « Échec de l'export JSON », ou message générique |

### `GET /api/backup/scheduler/status`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, non |
| 200 | `{"running": bool, "interval_hours": int, "last_backup": str ou null, "next_backup": str ou null, "keep_backups": int}` |

### `POST /api/backup/scheduler/configure`

| Élément | Valeur |
|---|---|
| Authentification, CSRF | Session, oui |
| Corps | `{"interval_hours": int, "keep_backups": int}`, chacun facultatif. `interval_hours` : de 1 à 24. `keep_backups` : 1 au moins. Les nombres écrits en chaîne (« 12 ») sont acceptés, pas les décimaux ni les booléens |
| 200 | `{"success": true, "message": "<confirmation>", "status": {...}}` (même forme que `scheduler/status`) |
| 400 | Message de validation en français (« L'intervalle doit être compris entre 1 et 24 heures », par exemple) |
| 500 | Message générique |

Le réglage porte sur le processus en cours : il n'est pas écrit dans `.env` et revient aux valeurs `BACKUP_INTERVAL_HOURS` et `BACKUP_KEEP` au redémarrage.

## 9. Blueprint `exports` : téléchargement des exports

Quatre `GET` (session, sans jeton CSRF). Chaque export est écrit dans un fichier temporaire du dossier des exports, lu en mémoire puis supprimé avant l'envoi : deux téléchargements simultanés ne partagent jamais un fichier, et aucun export ne reste sur le disque. Les cellules de texte sont protégées contre l'injection de formules des tableurs.

| Chemin | Contenu | Fichier envoyé | Filtres |
|---|---|---|---|
| `/export/excel` | Classeur Excel des commandes, colonnes de la base | `commandes.xlsx` | `status`, `date_from`, `date_to` |
| `/export/excel/sage` | Classeur au format d'import SAGE X3 : commandes validées. Une seconde feuille, « Unités à convertir », liste les commandes dont la quantité n'est pas en unités de stock | `commandes_sage_x3.xlsx` | `date_from`, `date_to` ; `status` ne peut valoir que `validee` |
| `/export/csv` | Fichier CSV des commandes, UTF-8 avec marque d'ordre des octets (BOM), mêmes colonnes que l'export Excel | `commandes.csv` | `status`, `date_from`, `date_to` |
| `/export/pdf` | Rapport PDF d'aide à la décision de trois pages | `rapport_commandes.pdf` | Aucun |

Filtres (paramètres de requête) :

| Paramètre | Valeurs |
|---|---|
| `status` | Un statut de commande : `en_attente`, `validee`, `rejetee`, `supprimee`. Sans statut, toutes les commandes sauf les supprimées |
| `date_from`, `date_to` | Jours « AAAA-MM-JJ » inclus, dans le fuseau horaire de l'entreprise |

| Réponse | Cas |
|---|---|
| 200 | Fichier en pièce jointe |
| 400 | Page HTML « Export impossible » qui explique le refus : statut inconnu, date mal écrite, période inversée, ou statut autre que `validee` pour l'export SAGE X3. Un filtre n'est jamais ignoré en silence |

## 10. Fichiers statiques

### `GET /static/<chemin>`

Route fournie par Flask pour les fichiers du dossier `tecpap/web/static`. Publique, sans jeton CSRF. Réponse 200 avec le fichier, ou 404 s'il n'existe pas.

## 11. Exemple d'appel

Validation d'une commande depuis un script, avec la session et le jeton d'un administrateur connecté (le jeton se lit dans la balise `<meta name="csrf-token">` des pages) :

```bash
curl -X POST "https://<adresse-du-serveur>/api/orders/42/validate" \
  -H "Cookie: session=<cookie de session>" \
  -H "X-CSRF-Token: <jeton CSRF>" \
  -H "Content-Type: application/json" \
  -d '{"validated_by": "Équipe commerciale"}'
```

Réponse attendue (200) : voir la section 6.3. Une seconde exécution de la même commande renvoie 409, sans second message au client.
