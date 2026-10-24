---
title: Guide de rédaction de la base de connaissances
categorie: guide
langue: fr
---

# Guide de rédaction de la base de connaissances

Ce guide s'adresse à l'équipe commerciale de TECPAP. Il explique comment ajouter ou modifier les documents du dossier `knowledge/`. Son nom commence par « _ » : il n'est donc pas indexé et l'assistant WhatsApp ne le lit jamais.

## À quoi sert la base de connaissances

L'assistant WhatsApp de TECPAP répond aux questions des clients sur les produits par génération augmentée par recherche (RAG, de l'anglais retrieval-augmented generation) :

1. l'assistant cherche, dans les documents de ce dossier, les sections les plus proches de la question du client ;
2. il rédige sa réponse uniquement à partir de ces sections ;
3. si aucune section ne correspond assez à la question, il ne répond pas lui-même et la question est transmise à l'équipe commerciale.

Conséquence : **toute phrase écrite ici peut être citée à n'importe quel client.** Une information absente de ces documents n'est jamais donnée par l'assistant.

## Règle de confidentialité

Ne jamais écrire dans la base de connaissances :

- les prix négociés avec un client, les remises ou les conditions particulières d'un client ;
- les listes de clients, les noms de clients ou l'historique de leurs commandes ;
- les numéros de téléphone ou les adresses email de clients ou d'employés (seules les coordonnées publiques de TECPAP, dans `contact.md`, y figurent) ;
- les mots de passe, clés d'accès, requêtes SQL, noms de variables de configuration ou toute autre information technique interne (une réponse qui en contient est bloquée par le filtre de sortie de l'assistant).

Les données d'un client (ses commandes, leur statut) sont consultées par l'assistant au moyen de fonctions dédiées, limitées au numéro de téléphone de l'expéditeur. Elles n'ont pas leur place dans ces documents.

## Règle d'exactitude

N'écrire que des informations vérifiées et approuvées par TECPAP. En particulier, ne pas ajouter de prix, de délai de livraison, de quantité minimale de commande, de condition de paiement, de certification ou d'horaire d'ouverture tant que l'information n'est pas validée par la direction.

Une question sans réponse dans la base n'est pas un problème : l'assistant la transmet à l'équipe commerciale. Une réponse fausse, elle, est envoyée telle quelle au client.

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
3. Reconstruire l'index de recherche depuis le dossier du projet :

```bash
python scripts/build_knowledge_index.py
```

La reconstruction ne recalcule que les sections modifiées et retire de l'index les documents supprimés. Les modifications sont prises en compte par l'assistant après la reconstruction de l'index.

Pour supprimer un document, supprimer le fichier puis reconstruire l'index avec la même commande.

## Liste de contrôle avant enregistrement

- Aucune donnée confidentielle (prix négocié, client, téléphone, email personnel).
- Aucune information non vérifiée (prix, délai, quantité minimale, paiement, certification, horaire).
- Aucune phrase adressée à l'assistant.
- Chaque section `##` traite un seul sujet, se comprend seule et fait moins de 900 caractères environ.
- Les mots-clés contiennent les synonymes en darija (lettres latines et arabes) et en arabe.
- L'index est reconstruit avec `python scripts/build_knowledge_index.py`.
