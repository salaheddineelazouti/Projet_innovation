# Sécurité de TECPAP

Ce document décrit les menaces prises en compte, les défenses mises en place avec le fichier qui les réalise, les limites connues et la conduite à tenir quand la page « Sécurité » du tableau de bord signale un événement. La procédure de signalement d'une vulnérabilité est dans [SECURITY.md](../SECURITY.md). Le choix des modèles de langue est décrit dans [choix_des_modeles.md](choix_des_modeles.md), l'assistant WhatsApp dans [assistant_whatsapp.md](assistant_whatsapp.md) et la base de connaissances dans [rag.md](rag.md).

## 1. Principe directeur

TECPAP lit des messages écrits par des inconnus (WhatsApp, email, pièces jointes, notes vocales) avec un modèle de langue (LLM, Large Language Model). Un tel modèle ne distingue pas de façon fiable une consigne de son concepteur d'un texte qui prétend en être une. Le système est donc construit pour qu'une consigne malveillante, même obéie par le modèle, ne puisse rien faire de grave :

1. **L'autorisation est vérifiée dans le code, jamais confiée au modèle.** Les outils de l'assistant sont liés au numéro de l'expéditeur au moment de leur construction ; le modèle n'a aucun accès libre à la base de données.
2. **Une commande n'est jamais définitive sans l'équipe commerciale.** Toute commande est enregistrée au statut « en attente » ; seule l'équipe commerciale la valide depuis le tableau de bord.
3. **Le détecteur d'injection d'instructions (prompt injection) est une défense en profondeur.** Il signale et journalise ; il ne remplace aucune des deux protections précédentes (voir la section 6).
4. **Les sorties du modèle sont contrôlées avant envoi.** Un filtre de sortie bloque les fuites de consignes système (system prompt), de secrets et de coordonnées d'autres personnes.

## 2. Acteurs et biens à protéger

### 2.1 Acteurs

| Acteur | Capacité supposée | Entrée utilisée |
|---|---|---|
| Expéditeur WhatsApp inconnu | Envoyer du texte, des notes vocales, des images, des PDF | Webhook WhatsApp |
| Expéditeur d'email | Écrire n'importe quel texte, joindre des fichiers, falsifier l'adresse d'expéditeur | Boîte IMAP |
| Attaquant sur le webhook public | Appeler l'adresse du webhook sans passer par Twilio | Requêtes HTTP |
| Document malveillant | Contenir un texte caché ou une consigne destinée au modèle | PDF, image, pièce jointe |
| Visiteur du tableau de bord | Tenter de se connecter, ou de forcer une action d'un administrateur connecté | Interface web |

### 2.2 Biens

| Bien | Pourquoi le protéger |
|---|---|
| Données des autres clients | Commandes, numéros de téléphone, adresses email, historique |
| Consignes système | Texte des instructions données au modèle |
| Clés et secrets | Clé d'API du modèle de langue, jeton Twilio, mot de passe d'application Gmail, clé de session |
| Commandes | Une commande falsifiée ou un prix modifié a un coût commercial |
| Tableau de bord | Accès de l'équipe commerciale, qui valide les commandes |
| Disponibilité et coûts | Les appels aux modèles de langue sont payants |

## 3. Correspondance avec le Top 10 OWASP des applications à base de modèles de langue

L'OWASP (Open Worldwide Application Security Project) publie une liste des dix risques principaux des applications à base de modèles de langue. La numérotation ci-dessous est celle de l'édition 2025.

| Risque | Menace pour TECPAP | Défense | Fichier |
|---|---|---|---|
| LLM01 Injection d'instructions | Un message ou un document ordonne au modèle d'ignorer ses règles | Détecteur à signaux pondérés | `tecpap/security/guardrails.py`, `tecpap/security/signals.py`, `tecpap/security/text.py` |
| LLM01 (suite) | Le texte hostile est pris pour une consigne | Contenu externe délimité par des balises et rappelé comme donnée | `tecpap/security/guardrails.py` (`wrap_untrusted`) |
| LLM01 (suite) | Une consigne obéie fait agir le modèle | Autorisation dans le code : outils liés à l'expéditeur | `tecpap/chatbot/tools.py` |
| LLM02 Divulgation d'informations sensibles | Le modèle cite le numéro ou l'email d'un autre client | Filtre de sortie : masquage des coordonnées non autorisées | `tecpap/security/guardrails.py` (`OutputFilter`), `tecpap/security/redaction.py` |
| LLM02 (suite) | Un secret apparaît dans une réponse | Filtre de sortie : clés d'API, jetons, noms de variables d'environnement, chemins internes | `tecpap/security/guardrails.py` |
| LLM03 Chaîne d'approvisionnement | Une dépendance vulnérable | Versions exactes dans `requirements.txt` ; analyse des vulnérabilités connues (pip-audit) et du code (bandit) en intégration continue | `requirements.txt`, `.github/workflows/ci.yml` |
| LLM04 Empoisonnement des données | Une commande ou un document trompeur oriente le modèle | Exemples tirés des seules commandes validées par l'équipe, anonymisés et sans prix ; base de connaissances écrite par l'équipe | `tecpap/rag/retriever.py` (`OrderExampleRetriever`), `knowledge/` |
| LLM05 Mauvais traitement des sorties | La sortie du modèle est exécutée ou affichée sans contrôle | Sortie JSON validée par un schéma ; requêtes SQL paramétrées ; échappement automatique des gabarits Jinja ; messages à données construits par des modèles de texte fixes | `tecpap/extraction/schema.py`, `tecpap/chatbot/messages.py`, `tecpap/web/templates/` |
| LLM06 Autonomie excessive | Le modèle enregistre ou modifie une commande seul | Outils en lecture seule ; l'enregistrement d'une commande est fait par l'automate après l'accord explicite du client, au statut « en attente » | `tecpap/chatbot/tools.py`, `tecpap/chatbot/bot.py` |
| LLM07 Fuite des consignes système | Un message demande de recopier, traduire ou résumer les consignes | Jeton témoin aléatoire par message ; comparaison avec les textes protégés | `tecpap/security/guardrails.py` (`new_canary`, `OutputFilter`) |
| LLM08 Faiblesses des vecteurs et des plongements | Un extrait de la base de connaissances contient une consigne | Extraits délimités et neutralisés comme tout contenu externe ; base écrite par l'équipe | `tecpap/rag/answer.py` |
| LLM09 Désinformation | Le modèle invente un prix ou un délai | Questions commerciales renvoyées à l'équipe sans appel au modèle ; contrôle que chaque montant, durée ou pourcentage cité figure dans les extraits | `tecpap/rag/answer.py` |
| LLM10 Consommation non bornée | Un expéditeur épuise le quota payant | Limite de messages par minute, taille maximale des requêtes et des médias, tours d'outils bornés, délai maximal des appels | `tecpap/chatbot/bot.py`, `tecpap/web/__init__.py`, `tecpap/processing/multimodal.py`, `tecpap/llm/client.py` |

## 4. Correspondance avec les risques classiques des applications web

La référence est le Top 10 OWASP des applications web, édition 2021.

| Risque | Menace pour TECPAP | Défense | Fichier |
|---|---|---|---|
| A01 Contrôle d'accès défaillant | Accès au tableau de bord sans connexion | Toutes les routes exigent une session, sauf la connexion, la sonde `/healthz`, les fichiers statiques et le webhook | `tecpap/web/auth.py` |
| A01 (suite) | Lecture d'un fichier hors du dossier des sauvegardes | Nom de fichier restreint à un jeu de caractères, chemin résolu et contrôlé | `tecpap/web/routes/backups.py`, `tecpap/db/backup.py` |
| A02 Défaillances cryptographiques | Mot de passe d'administrateur lisible | Empreinte du mot de passe (werkzeug), jamais le mot de passe ; cookie de session signé ; HSTS en HTTPS | `scripts/hash_password.py`, `tecpap/web/auth.py`, `tecpap/web/security_headers.py` |
| A03 Injection | Injection SQL | Requêtes paramétrées ; le modèle n'écrit aucune requête | `tecpap/chatbot/tools.py`, `tecpap/db/database.py` |
| A03 (suite) | Injection de formules dans un tableur | Neutralisation dans les exports (section 5.8) | `tecpap/reporting/spreadsheet.py` |
| A03 (suite) | Injection d'en-tête d'email | Sujet réduit à une seule ligne ; accusé de réception construit avec des valeurs contrôlées | `tecpap/channels/email_sender.py`, `tecpap/processing/order_processor.py` |
| A03 (suite) | Injection de code HTML (XSS) | Échappement automatique de Jinja ; fonction d'échappement côté navigateur ; politique de sécurité du contenu | `tecpap/web/templates/`, `tecpap/web/security_headers.py` |
| A04 Conception non sécurisée | Une seule couche de défense | Plusieurs couches indépendantes (section 1) | Ensemble du dépôt |
| A05 Mauvaise configuration | Clé de session faible ou débogage en production | Production refusée sans clé de 32 caractères au moins, avec la valeur d'exemple, ou en mode débogage | `tecpap/config.py` |
| A05 (suite) | Conteneur trop privilégié | Conteneur exécuté par un utilisateur sans privilèges | `Dockerfile` |
| A06 Composants vulnérables | Dépendance connue comme vulnérable | pip-audit et bandit dans l'intégration continue | `.github/workflows/ci.yml` |
| A07 Authentification défaillante | Essais de mots de passe en série | Blocage par adresse IP, ralentissement par identifiant, session de 12 heures au plus, comparaison en temps constant | `tecpap/web/auth.py`, `tecpap/web/routes/auth_routes.py` |
| A07 (suite) | Requête forgée depuis un autre site (CSRF) | Jeton par session sur toute requête qui modifie des données ; cookie `SameSite=Lax` | `tecpap/web/auth.py` |
| A08 Défaillances d'intégrité | Appel du webhook par un tiers | Signature Twilio vérifiée avant tout traitement | `tecpap/web/routes/webhooks.py` |
| A09 Journalisation insuffisante | Attaque non détectée | Table des événements de sécurité et page dédiée ; journaux sans contenu de message | `tecpap/security/events.py`, `tecpap/web/routes/assistant.py` |
| A10 Falsification de requête côté serveur (SSRF) | Le serveur est poussé à appeler une adresse interne | Un média n'est téléchargé que s'il est servi en HTTPS par un hôte `twilio.com` ou l'un de ses sous-domaines | `tecpap/channels/whatsapp.py` |

## 5. Défenses, une par une

### 5.1 Autorisation dans le code

Fichier : `tecpap/chatbot/tools.py`.

- `AssistantTools` est construit pour un expéditeur ; son numéro est fixé à la construction. Aucune méthode ne prend un numéro, un client ou un identifiant interne en paramètre, et les schémas d'outils transmis au modèle n'en déclarent pas. Un argument inattendu (par exemple `phone`) est ignoré.
- Chaque requête SQL est paramétrée et filtre elle-même sur le numéro de l'expéditeur (colonne `whatsapp_from`).
- Le numéro enregistré pour un client ne donne accès à aucune commande : un expéditeur qui écrit au nom d'une entreprise ne voit pas les commandes de cette entreprise reçues par email ou depuis un autre numéro.
- Les résultats sont réduits au minimum : référence, date, produit, quantité, unité, statut. Jamais de prix, jamais de coordonnées.
- Les outils proposés au modèle sont `my_recent_orders`, `my_order_status`, `my_last_validated_order`, `search_catalog` et `request_human` : de la lecture et la demande d'un conseiller. L'enregistrement d'une commande est fait par l'automate de conversation (`tecpap/chatbot/bot.py`) après l'accord explicite du client.
- Quand le modèle demande un outil de données, la réponse est construite à partir du résultat de l'outil, qui n'est pas renvoyé au modèle ; seules les erreurs lui sont renvoyées. Le nombre de tours d'outils est borné (3 tours, 3 outils par tour).
- Pour un renouvellement, la dernière commande validée est cherchée par l'adresse email ou le numéro WhatsApp de l'expéditeur, jamais par le nom de client cité dans le message (`tecpap/extraction/extractor.py`).

Conséquence : même si une injection convainc le modèle de demander « les commandes de tous les clients », aucun outil ne sait les lire.

### 5.2 Inspection des entrées

Fichiers : `tecpap/security/guardrails.py` (`inspect_input`), `tecpap/security/signals.py`, `tecpap/security/text.py`.

1. Le texte est normalisé : forme Unicode NFKC, caractères invisibles retirés, homoglyphes (lettres d'un autre alphabet d'apparence identique), accents, signes diacritiques arabes, lettres espacées, écriture leet (chiffres à la place de lettres), abréviations de messagerie.
2. Le texte est comparé à des signaux pondérés en français, anglais, arabe standard et darija (lettres arabes et lettres latines). Chaque signal a un poids ; les tournures sans usage légitime dans une commande atteignent seules le seuil, les indices ambigus ne déclenchent une alerte qu'associés à d'autres.
3. Le score est la somme des poids des signaux et des associations de signaux, plafonnée à 100. Le message est suspect à partir du seuil `SUSPICION_THRESHOLD`, fixé à 50.
4. Les contenus encodés (base64, hexadécimal, encodage d'URL, séquences d'échappement), le texte caché en caractères d'étiquette Unicode, le texte affiché à l'envers par une marque de direction et le texte cité comme ROT13 sont décodés et analysés à leur tour, sur deux niveaux au plus.
5. Chaque signal ajoute une raison en français avec l'extrait qui l'a déclenché ; ces raisons sont enregistrées dans le journal de sécurité.

Réaction selon le canal :

| Canal | Réaction à un contenu suspect | Fichier |
|---|---|---|
| Assistant WhatsApp | Événement de sécurité, réponse fixe, aucun appel au modèle ni aux outils | `tecpap/chatbot/bot.py` |
| Extraction (WhatsApp et email) | Anomalie ajoutée à la commande, événement de sécurité ; la commande n'est jamais écartée | `tecpap/extraction/extractor.py` |
| Email | Aucun accusé de réception automatique | `tecpap/processing/order_processor.py` |

Un email n'est jamais bloqué : une commande légitime ne doit pas être perdue à cause d'une fausse alerte. L'équipe commerciale voit l'anomalie « Contenu suspect : tentative possible d'injection d'instructions » et décide.

Les textes analysés comprennent le corps du message, la transcription d'une note vocale et le texte lu dans une image ou un PDF : une consigne cachée dans un document ou dite dans un message vocal est inspectée comme un message écrit.

### 5.3 Délimitation du contenu externe

Fichier : `tecpap/security/guardrails.py` (`wrap_untrusted`, `neutralize_tags`, `UNTRUSTED_CONTENT_RULE`).

Le message d'un client, le contenu d'un email et chaque extrait de la base de connaissances sont placés entre des balises (`<message_client>`, `<extrait>`…). Les consignes système rappellent que ce contenu est une donnée à analyser, jamais une instruction. Toute balise de même nom présente dans le contenu est remplacée par des crochets, y compris ses variantes : casse, espaces, caractères invisibles, chevrons pleine largeur, entités HTML, homoglyphes. Un expéditeur ne peut donc pas « fermer » la zone de données pour écrire une consigne.

Cette mesure réduit le risque sans l'annuler : un modèle peut encore se laisser convaincre par un texte placé dans une zone délimitée. C'est la raison de l'autorisation dans le code.

### 5.4 Jeton témoin et filtre de sortie

Fichier : `tecpap/security/guardrails.py` (`new_canary`, `canary_instruction`, `OutputFilter`).

- Un jeton témoin (canary token, nommé « canari » dans le code) est un code aléatoire de la forme `CNR-` suivi de 12 caractères hexadécimaux. Il est placé dans les consignes système avec l'ordre de ne jamais le recopier, et renouvelé à chaque message. S'il apparaît dans une réponse, même entrecoupé d'espaces ou de caractères invisibles, les consignes ont fuité.
- Le filtre de sortie bloque aussi une réponse qui reprend une suite de 8 mots identiques à un texte protégé (toutes les consignes système de l'assistant), ou un passage d'au moins 60 caractères recopié mot pour mot. Il bloque une réponse qui contient une requête SQL, une clé d'API ou un jeton secret (clés OpenAI, identifiants Twilio, clés AWS, Google, GitHub, Slack, jetons JWT, clés privées, empreintes de mots de passe), un nom de variable d'environnement ou un chemin de fichier interne.
- Sinon, il masque les numéros de téléphone et les adresses email absents de la liste autorisée : le numéro de l'expéditeur et les coordonnées publiques de TECPAP. Les formats reconnus comprennent les chiffres arabo-indiens, les écritures « nom [at] domaine [dot] ma » et les préfixes marocains (`tecpap/security/redaction.py`).
- Une réponse bloquée est remplacée par un message fixe, et un événement `output_blocked` est enregistré.
- Les réponses de la base de connaissances, déjà contrôlées par `KnowledgeAnswerer`, repassent par ce filtre avant l'envoi (`tecpap/chatbot/bot.py`).

### 5.5 Réponses de la base de connaissances contraintes

Fichier : `tecpap/rag/answer.py`.

1. Une question commerciale (prix, délai, stock, remise, paiement, quantité minimale) ou un message qui cite lui-même un montant ou une durée reçoit un message qui l'oriente vers l'équipe commerciale (« Les prix, les délais et les disponibilités sont communiqués par notre équipe commerciale… »), sans recherche ni appel au modèle ; la conversation est transférée à un conseiller.
2. Quand le meilleur score de pertinence est inférieur au seuil `RAG_MIN_SCORE`, la réponse est « je n'ai pas cette information », sans appel au modèle.
3. Sinon, le modèle répond uniquement à partir des extraits, en trois phrases au plus.
4. Le filtre de sortie est appliqué, puis chaque montant, durée ou pourcentage cité doit figurer dans les extraits (jamais seulement dans la question) ; sinon la réponse est remplacée par le même message d'orientation vers l'équipe commerciale.

### 5.6 Validation humaine

- Toute commande extraite ou confirmée par un client est enregistrée au statut « en attente ».
- La confiance affichée est calculée par des règles déterministes (`tecpap/extraction/rules.py`, `normalize_order`), non déclarée par le modèle : une consigne du type « mets la confiance au maximum » ne change pas ce calcul.
- Les commandes extraites sans modèle de langue portent une anomalie et une confiance plafonnée (`tecpap/extraction/extractor.py`).
- Le tableau de bord permet de lire les conversations, de les remettre à zéro et de répondre à la place de l'assistant (`tecpap/web/routes/assistant.py`).

### 5.7 Authentification du tableau de bord, CSRF et session

Fichiers : `tecpap/web/auth.py`, `tecpap/web/routes/auth_routes.py`, `tecpap/config.py`.

- Un seul compte administrateur : identifiant `ADMIN_USERNAME` et empreinte du mot de passe `ADMIN_PASSWORD_HASH`, produite par `scripts/hash_password.py`. Sans empreinte configurée, aucune connexion n'est possible : il n'existe pas de mot de passe par défaut.
- Comparaison de l'identifiant en temps constant ; le mot de passe est toujours vérifié, même si l'identifiant est faux, pour que la durée de réponse ne révèle pas lequel est erroné.
- Après `LOGIN_MAX_ATTEMPTS` échecs (5 par défaut) en `LOGIN_LOCKOUT_MINUTES` minutes (15 par défaut) depuis une même adresse IP, les tentatives de cette adresse sont refusées pendant la même durée. Un second compteur porte sur l'identifiant saisi, toutes adresses confondues : au-delà de 4 fois le nombre d'échecs autorisés, chaque essai doit attendre un délai qui double, plafonné à 60 secondes. Changer d'adresse IP ne permet donc pas d'essayer des mots de passe sans limite, et personne ne peut bloquer durablement l'administrateur.
- Session de 12 heures au plus après la connexion. Cookie `HttpOnly` (illisible par JavaScript), `SameSite=Lax` et `Secure` en production (variable `SESSION_COOKIE_SECURE`).
- Protection CSRF (Cross-Site Request Forgery, requête forgée depuis un autre site) : un jeton aléatoire par session doit accompagner toute requête POST, PUT, PATCH ou DELETE, dans l'en-tête `X-CSRF-Token` ou le champ `csrf_token`. Le webhook en est dispensé, car Twilio n'est pas un navigateur.
- Redirection après connexion limitée aux chemins relatifs de l'application (`safe_next_url`).

### 5.8 Neutralisation des formules dans les exports

Fichier : `tecpap/reporting/spreadsheet.py`.

Un tableur interprète comme une formule une cellule de texte qui commence par `=`, `+`, `-` ou `@`. Les noms de clients et les désignations exportés viennent de messages externes. Dans un classeur Excel, ces cellules sont écrites comme du texte (indicateur « quotePrefix ») ; dans un fichier CSV, une apostrophe est ajoutée devant la valeur. Les cellules numériques ne sont pas modifiées. Les exports sont écrits dans des fichiers temporaires de nom unique, lus en mémoire puis supprimés (`tecpap/web/routes/exports.py`).

### 5.9 Signature Twilio

Fichier : `tecpap/web/routes/webhooks.py`.

Chaque requête doit porter l'en-tête `X-Twilio-Signature`, calculé par Twilio avec le jeton d'authentification du compte sur l'adresse appelée et les paramètres. Sans signature valide, la requête est refusée (403) avant tout traitement : personne d'autre que Twilio ne peut créer de commande par ce webhook. Si la vérification est activée sans `TWILIO_AUTH_TOKEN`, le webhook répond 503 plutôt que d'accepter tout le monde. Derrière un proxy ou un tunnel, l'adresse signée est reconstruite à partir de `PUBLIC_BASE_URL`. Le refus est enregistré comme événement `invalid_signature` (numéro masqué), au plus un par minute et par adresse IP.

Le numéro de l'expéditeur est celui que Twilio a signé : il n'est pas simplement déclaré par l'appelant.

### 5.10 Limites de taille et de débit

| Limite | Valeur | Fichier |
|---|---|---|
| Corps d'une requête HTTP | 1 Mo, réponse 413 au-delà | `tecpap/web/__init__.py` |
| Messages WhatsApp par expéditeur | `WHATSAPP_RATE_LIMIT_PER_MINUTE`, 10 par défaut | `tecpap/chatbot/bot.py`, `tecpap/web/routes/webhooks.py` |
| Média traité | `MAX_MEDIA_BYTES`, 16 Mo par défaut | `tecpap/processing/multimodal.py` |
| Pages de PDF lues | 100 | `tecpap/processing/multimodal.py` |
| Tours d'outils de l'assistant | 3 | `tecpap/chatbot/bot.py` |
| Longueur d'une réponse rédigée par le modèle | 600 caractères | `tecpap/chatbot/bot.py` |
| Appels au modèle de langue | 2 essais au plus, délai `LLM_TIMEOUT` | `tecpap/llm/client.py` |
| Événements de sécurité enregistrés pour une même adresse | 1 par minute | `tecpap/security/events.py` |

La limite de débit est vérifiée avant le téléchargement du média : un expéditeur au-delà de la limite ne déclenche ni transcription ni lecture d'image, qui sont payantes.

### 5.11 File durable des messages reçus

Fichier : `tecpap/chatbot/inbox.py`.

Twilio considère un message comme livré dès que le webhook répond 200. Chaque message signé est donc enregistré dans la table `whatsapp_inbox` avant la réponse, puis traité. Un arrêt du conteneur pendant le traitement ne perd aucun message : il est repris au démarrage et par la maintenance périodique, après au plus 3 prises en charge. L'identifiant Twilio du message est unique : une seconde livraison du même message n'est ni enregistrée ni traitée.

### 5.12 En-têtes de sécurité

Fichier : `tecpap/web/security_headers.py`.

| En-tête | Effet |
|---|---|
| `X-Content-Type-Options: nosniff` | Le navigateur respecte le type annoncé d'un fichier |
| `X-Frame-Options: DENY` et `frame-ancestors 'none'` | Le tableau de bord ne s'affiche pas dans le cadre d'un autre site |
| `Referrer-Policy: same-origin` | L'adresse des pages n'est pas transmise aux sites externes |
| `Content-Security-Policy` | Seules les origines listées peuvent fournir scripts, styles, polices et images |
| `Strict-Transport-Security` | HTTPS imposé pendant un an, envoyé seulement sur une réponse servie en HTTPS |

Limite : les gabarits contiennent des scripts en ligne, la politique autorise donc `'unsafe-inline'` pour les scripts. Elle ne protège pas contre une injection de code HTML ; c'est l'échappement automatique de Jinja qui l'empêche.

### 5.13 Page « Sécurité » et masquage dans les journaux

Fichiers : `tecpap/security/events.py`, `tecpap/security/redaction.py`, `tecpap/web/routes/assistant.py`, `tecpap/web/templates/security.html`.

- Quatre natures d'événements : `prompt_injection`, `output_blocked`, `rate_limited`, `invalid_signature`. La page `/security` affiche les plus récents, filtrables par nature, et le décompte sur sept jours.
- L'expéditeur est enregistré masqué (« +2126•••••678 »). Le détail est nettoyé, les numéros et emails qu'il contient sont masqués, puis il est tronqué à 500 caractères. Les événements sont conservés 180 jours, dans la limite de 100 000 lignes.
- Les journaux du serveur affichent les numéros masqués (`mask_sender`). Le client des modèles de langue n'écrit jamais le contenu des messages dans le journal, seulement l'opération, le modèle et le type d'erreur (`tecpap/llm/client.py`). Les bibliothèques Twilio et OpenAI sont limitées au niveau WARNING (`tecpap/logging_config.py`).
- Une erreur n'est jamais renvoyée au navigateur avec son détail : réponse générique, détail dans le journal (`tecpap/web/errors.py`).

## 6. Limites connues

### 6.1 Le détecteur d'injection est une défense en profondeur

Un détecteur à signaux ne reconnaît que les formulations prévues par ses auteurs. Un attaquant qui connaît la méthode peut reformuler. C'est pourquoi la sécurité ne repose pas sur lui : les protections qui comptent sont l'autorisation dans le code (section 5.1), la validation humaine (section 5.6) et le filtre de sortie (section 5.4). Le détecteur sert à repérer les tentatives, à épargner un appel au modèle et à alerter l'équipe.

Mesures de l'évaluation hors ligne (`evaluation/results/synthese.md`, exécution du 2026-10-25). Le jeu de test est tenu à l'écart : il n'a jamais servi à écrire ni à ajuster les signaux (`evaluation/README.md`, section 9). Il compte 28 tentatives d'injection de familles variées et 106 messages sans injection, dont 17 pièges (messages légitimes qui contiennent un mot ou une tournure surveillés).

| Mesure | Jeu de test (tenu à l'écart) | Jeu de développement |
|---|---|---|
| Rappel (injections signalées) | 23 sur 28, soit 82,1 % | 39 sur 39, soit 100,0 % |
| Intervalle de confiance à 95 % du rappel | de 64,4 % à 92,1 % | de 91,0 % à 100,0 % |
| Précision (signalements justes) | 23 sur 23, soit 100,0 % | 39 sur 39, soit 100,0 % |
| Taux de faux positifs | 0 sur 106, soit 0,0 % | 0 sur 159, soit 0,0 % |
| Intervalle de confiance à 95 % du taux de faux positifs | de 0,0 % à 3,5 % | de 0,0 % à 2,4 % |
| Fausses alertes sur les pièges | 0 sur 17, soit 0,0 % | 0 sur 23, soit 0,0 % |
| Intervalle de confiance à 95 % des fausses alertes sur les pièges | de 0,0 % à 18,4 % | de 0,0 % à 14,3 % |

L'intervalle de confiance à 95 % est l'intervalle de score de Wilson : la plage de valeurs de la proportion réelle compatibles avec l'échantillon.

Lecture de ces chiffres :

- Le score parfait du jeu de développement ne mesure pas la qualité réelle : ce jeu a servi à régler le détecteur.
- Sur des messages que le détecteur n'avait jamais vus, 5 tentatives sur 28 n'ont pas été signalées. Avec 28 tentatives, le rappel réel peut aller de 64,4 % à 92,1 % : plus d'un tiers des tentatives pourrait passer.
- Zéro fausse alerte sur 106 messages n'exclut pas un taux réel allant jusqu'à 3,5 %. L'échantillon de pièges est petit : la borne haute des fausses alertes sur les pièges est de 18,4 %.
- Les messages du jeu de test sont fictifs et écrits pour l'évaluation, non par des attaquants extérieurs. Ces chiffres ne prédisent pas le taux de détection face à un attaquant qui adapte son message.
- L'évaluation hors ligne mesure le détecteur, pas le comportement du modèle de langue face à une injection. Elle ne mesure pas non plus le filtre de sortie.

Conséquence d'une injection non détectée : le message atteint le modèle. Il peut faire dire à l'assistant une phrase hors sujet ou fausser les champs d'une commande, mais il ne peut lire aucune donnée d'un autre client (outils liés à l'expéditeur), ne peut valider aucune commande (validation humaine) et ne peut pas faire sortir un secret ou un numéro tiers sans que le filtre de sortie le masque ou le bloque.

### 6.2 Autres limites

| Limite | Explication |
|---|---|
| Adresse d'expéditeur d'un email | L'adresse peut être falsifiée. Un résultat DMARC en échec empêche l'accusé de réception automatique (`tecpap/channels/email_receiver.py`, `tecpap/processing/order_processor.py`), mais la commande est enregistrée et vérifiée par l'équipe |
| Renouvellement par email | L'historique repris est celui de l'adresse indiquée comme expéditeur ; seuls les champs manquants sont complétés et la commande reste « en attente » |
| Filtre de sortie | Il reconnaît des motifs (formats de clés, noms de variables, suites de mots). Un secret au format inconnu ou une information paraphrasée ne serait pas arrêté |
| Compteurs de connexion | Gardés en mémoire du processus, perdus au redémarrage. Le conteneur lance un seul processus gunicorn pour cette raison (`Dockerfile`) |
| Compte unique | Un seul administrateur ; pas de rôles distincts ni de double authentification |
| Politique de sécurité du contenu | Elle autorise les scripts en ligne (section 5.12) |
| Services tiers | Le texte des messages est envoyé à l'API du modèle de langue, selon les conditions de son fournisseur |
| Pièces jointes | Le texte extrait d'un PDF ou lu dans une image est traité comme un message externe, mais le fichier n'est pas analysé à la recherche de logiciels malveillants |
| Base SQLite | Fichier local non chiffré ; la protection repose sur les droits du système de fichiers et sur les sauvegardes |

## 7. Conduite à tenir quand la page « Sécurité » affiche des événements

La page est accessible à l'adresse `/security`. Elle se consulte chaque jour ouvré et à chaque alerte de l'équipe.

### 7.1 Par nature d'événement

| Événement | Signification | Actions |
|---|---|---|
| `prompt_injection` | Un message ou un document a atteint le seuil de suspicion | 1. Lire les raisons et les extraits dans la colonne « Détails ». 2. Ouvrir la conversation concernée et lire le message. 3. Si une commande porte l'anomalie « Contenu suspect », la vérifier champ par champ avant de la valider ou de la rejeter. 4. En cas de fausse alerte sur un client légitime, noter le message pour l'amélioration du détecteur |
| `prompt_injection`, répété | Plusieurs tentatives depuis le même expéditeur masqué | Transférer la conversation à un conseiller, ne valider aucune commande de cet expéditeur sans vérification par téléphone, décider hors de l'application du blocage du numéro (elle n'a pas de liste de numéros bloqués) |
| `output_blocked` | Une réponse du modèle a été bloquée par le filtre de sortie | Lire la raison. Pour « canari » ou « texte protégé » : une tentative d'extraction des consignes est allée jusqu'au modèle ; chercher l'événement `prompt_injection` voisin. Pour « secret » ou « variable d'environnement » : vérifier qu'aucune clé n'a été exposée, et la renouveler par précaution en cas de doute |
| `rate_limited` | Un expéditeur dépasse la limite de messages par minute | Distinguer un client qui renvoie plusieurs messages d'un abus. En cas d'abus, vérifier la consommation facturée par le fournisseur du modèle de langue |
| `invalid_signature` | Une requête a appelé le webhook sans signature valide | Événement isolé : un robot a trouvé l'adresse, aucune action. Si les messages légitimes cessent d'arriver et que les événements se multiplient : vérifier `TWILIO_AUTH_TOKEN` et `PUBLIC_BASE_URL` ; l'adresse vérifiée est écrite dans le journal du serveur |

Un résultat DMARC en échec sur un email n'apparaît pas sur la page « Sécurité » : il est écrit dans le journal du serveur, avec la mention qu'aucun accusé de réception n'a été envoyé.

### 7.2 Si un secret est peut-être exposé

1. Renouveler la clé concernée chez le fournisseur : `OPENAI_API_KEY`, `TWILIO_AUTH_TOKEN`, mot de passe d'application Gmail.
2. Remplacer `SECRET_KEY` : toutes les sessions du tableau de bord sont alors fermées.
3. Régénérer `ADMIN_PASSWORD_HASH` avec `scripts/hash_password.py` si le mot de passe de l'administrateur est en cause.
4. Redémarrer l'application et lire les journaux au démarrage (avertissements de configuration).
5. Si la base est altérée, restaurer une sauvegarde depuis la page « Sauvegardes ».
6. Signaler la vulnérabilité à l'auteur du projet selon [SECURITY.md](../SECURITY.md).

Les événements concernés restent consultables pendant 180 jours.

## 8. Tests

Les défenses sont exercées par des tests automatisés, notamment `tests/test_guardrails.py`, `tests/test_chatbot_security.py`, `tests/test_chatbot_tools.py`, `tests/test_auth.py`, `tests/test_csrf.py` et `tests/test_dashboard_security.py`. L'évaluation du détecteur est décrite dans [evaluation/README.md](../evaluation/README.md).
