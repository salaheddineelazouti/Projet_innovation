---
title: Guide de rédaction de la base de connaissances
categorie: guide
langue: fr
---

# Guide de rédaction de la base de connaissances

Ce guide s'adresse à l'équipe commerciale de TECPAP. Il explique comment ajouter ou modifier les documents du dossier `knowledge/`. Son nom commence par « _ » : il n'est donc pas indexé et l'assistant WhatsApp ne le lit jamais.

## À quoi sert la base de connaissances

L'assistant WhatsApp de TECPAP répond aux questions des clients sur les produits par génération augmentée par la recherche (RAG, Retrieval-Augmented Generation) :

1. une question de prix, de délai, de stock, de remise, de paiement ou de quantité minimale n'est pas cherchée dans ces documents : elle est transmise à l'équipe commerciale ;
2. pour les autres questions, l'assistant cherche, dans les documents de ce dossier, les sections les plus proches de la question du client ;
3. il rédige sa réponse uniquement à partir de ces sections, et sa réponse est écartée si elle cite un montant, une durée ou un pourcentage absent de ces sections ;
4. si aucune section ne correspond assez à la question, l'assistant répond qu'il n'a pas cette information et invite le client à écrire « conseiller ». Dans ce cas, la conversation n'est pas transférée automatiquement à l'équipe commerciale : la question reste visible dans l'historique de la conversation, sur la page « Conversations » du tableau de bord, mais rien ne la signale à l'équipe.

Conséquence : **toute phrase écrite ici peut être citée à n'importe quel client.** Pour répondre à une question sur les produits, l'assistant n'utilise que ces documents : une information qui n'y figure pas n'est pas donnée dans ce cadre.

## Règle de confidentialité

Ne jamais écrire dans la base de connaissances :

- les prix négociés avec un client, les remises ou les conditions particulières d'un client ;
- les listes de clients, les noms de clients ou l'historique de leurs commandes ;
- les numéros de téléphone ou les adresses email de clients ou d'employés (seules les coordonnées publiques de TECPAP, dans `contact.md`, y figurent) ;
- les mots de passe, clés d'accès, requêtes SQL, noms de variables de configuration ou toute autre information technique interne (une réponse qui en contient est bloquée par le filtre de sortie de l'assistant).

Les données d'un client (ses commandes, leur statut) sont consultées par l'assistant au moyen de fonctions dédiées, limitées au numéro de téléphone de l'expéditeur. Elles n'ont pas leur place dans ces documents.

## Règle d'exactitude

N'écrire que des informations vérifiées et approuvées par TECPAP. En particulier, ne pas ajouter de prix, de délai de livraison, de quantité minimale de commande, de condition de paiement, de certification ou d'horaire d'ouverture tant que l'information n'est pas validée par la direction.

Une question sans réponse dans la base reçoit la réponse « je n'ai pas cette information » avec l'invitation à écrire « conseiller ». Quand une même question revient souvent sans réponse, il vaut mieux compléter la base avec une information validée. Une réponse fausse, elle, est envoyée telle quelle au client.

## Écrire des faits, jamais des consignes

Le contenu des documents est traité comme une donnée, pas comme une consigne pour l'assistant. Écrire des phrases qui décrivent les produits et les procédures, par exemple « Les sachets fond plat servent à emballer les sandwichs ».

Ne pas écrire de phrases adressées à l'assistant, comme « l'assistant doit toujours répondre que… » ou « ignorer les règles précédentes… » : elles ne modifient pas son comportement, puisque les documents lui sont présentés comme des données, et elles risquent d'être citées telles quelles à un client.

## Format d'un document

Chaque document est un fichier Markdown (extension `.md`) placé dans le dossier `knowledge/`. Il commence par un en-tête YAML (front matter), encadré par deux lignes `---` :

```yaml
---
title: Produits TECPAP
categorie: produits
langue: fr
mots_cles:
  - sachet
  - sachiyat
  - ساشي
---
```

- `title` : titre du document ;
- `categorie` : thème (produits, papiers, commandes, contact, faq…) ;
- `langue` : langue de rédaction, `fr` ;
- `mots_cles` : liste de mots que les clients emploient pour ce sujet, en français, en darija en lettres latines (par exemple « sachiyat », « kif dima ») et en lettres arabes (par exemple « ساشي », « كالعادة »). Ces mots aident à retrouver le document quand la question est écrite en darija ou en arabe.

Après l'en-tête, le document contient un titre `#` puis des sections `##`.

## Rédiger une section

Chaque section `##` est l'unité de recherche : l'assistant reçoit des sections isolées, sans le reste du document. Une bonne section :

- traite un seul sujet (un produit, une définition, une procédure) ;
- se comprend seule : répéter le nom du produit ou du sujet dans le texte, ne pas écrire « ce produit » ou « voir plus haut » ;
- reste courte, moins de 900 caractères environ ; une section plus longue est coupée en plusieurs morceaux d'environ 900 caractères, qui se lisent moins bien ;
- définit les termes techniques à leur première apparition, par exemple « le grammage (masse d'un mètre carré de papier, en g/m²) » ;
- peut se terminer par une ligne « Autres formulations : … » avec la même question en darija et en arabe.

## Ajouter ou modifier un document

1. Créer ou modifier le fichier `.md` dans le dossier `knowledge/`, encodé en UTF-8.
2. Vérifier l'en-tête, les sections `##` et les règles de confidentialité et d'exactitude ci-dessus.
3. Mettre l'index de recherche à jour. La procédure dépend du mode de déploiement.

L'index est stocké dans la base de données (table `rag_chunks`). Il est mis à jour au démarrage de l'application et à la demande. La mise à jour ne recalcule que les sections modifiées et retire de l'index les documents supprimés. L'assistant utilise les textes modifiés une fois l'index à jour.

### Poste de développement

Le dossier `knowledge/` du dépôt est lu directement. Deux façons de mettre l'index à jour :

- depuis le dossier du projet : `python scripts/build_knowledge_index.py` (l'option `--force` recalcule tous les vecteurs) ;
- depuis le tableau de bord, page « Base de connaissances », bouton « Réindexer ».

### Déploiement avec Docker

Dans l'image Docker, le dossier `knowledge/` est copié dans `/app` au moment de la construction de l'image. Ce n'est pas un volume : modifier un fichier sur le serveur hôte ne change pas ce que lit le conteneur, et une modification faite dans le conteneur est perdue quand il est recréé. Deux procédures sont possibles :

- reconstruire l'image avec les documents modifiés, puis recréer le conteneur ; l'index est mis à jour au démarrage ;
- monter le dossier `knowledge/` de l'hôte sur `/app/knowledge` du conteneur (montage de dossier, option `-v` de `docker run`), puis cliquer sur « Réindexer » dans la page « Base de connaissances », ou redémarrer le conteneur. Le dossier monté doit être lisible par l'utilisateur du conteneur.

Dans les deux cas, la page « Base de connaissances » indique si l'index est à jour par rapport au dossier lu par l'application. La commande `python scripts/build_knowledge_index.py` lancée sur l'hôte met à jour la base de données de l'hôte, pas celle du conteneur.

Pour supprimer un document, supprimer le fichier puis mettre l'index à jour de la même façon.

## Liste de contrôle avant enregistrement

- Aucune donnée confidentielle (prix négocié, client, téléphone, email personnel).
- Aucune information non vérifiée (prix, délai, quantité minimale, paiement, certification, horaire).
- Aucune phrase adressée à l'assistant.
- Chaque section `##` traite un seul sujet, se comprend seule et fait moins de 900 caractères environ.
- Les mots-clés contiennent les synonymes en darija (lettres latines et arabes) et en arabe.
- L'index est mis à jour (bouton « Réindexer », redémarrage ou `python scripts/build_knowledge_index.py` selon le déploiement).
