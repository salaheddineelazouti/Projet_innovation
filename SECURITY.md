# Politique de sécurité

## Versions prises en charge

| Version | Prise en charge |
|---|---|
| 3.0.x | Oui |
| Versions antérieures à 3.0 | Non |

## Signaler une vulnérabilité

Ne publiez pas une vulnérabilité dans une issue, une discussion ou une demande de fusion publiques.

Le signalement se fait en privé auprès de l'auteur du projet, Salah Eddine EL AZOUTI, par le signalement privé de vulnérabilités de l'hébergeur du dépôt : onglet « Security » du dépôt, puis « Report a vulnerability ». Si cette option n'est pas visible, ouvrez une issue qui demande un contact privé, sans y décrire la vulnérabilité.

Un signalement utile contient :

- la version concernée (`tecpap/__init__.py`) ;
- la description du problème et de son effet : donnée exposée, action non autorisée, déni de service ;
- les étapes pour le reproduire, avec des données fictives ;
- si possible, une proposition de correction.

L'auteur accuse réception du signalement, l'étudie, puis informe son auteur de la suite donnée. Merci de laisser un délai raisonnable pour la correction avant toute divulgation publique.

## Ce qu'il ne faut pas faire

- Ne testez pas sur des données de production ni sur le compte WhatsApp ou la boîte email d'une entreprise réelle : utilisez une instance locale avec des données fictives.
- Ne tentez pas d'accéder aux données d'autres personnes, de les modifier ou de les supprimer.
- N'envoyez pas de volume de requêtes qui perturbe un service en fonctionnement.
- N'incluez dans un signalement ni clé d'API, ni mot de passe, ni donnée personnelle réelle.

## Périmètre

Sont concernés : le code de ce dépôt, sa configuration par défaut et son image Docker. Les services tiers (Twilio, OpenAI, Gmail) relèvent de leurs fournisseurs.

Les défenses en place, leurs limites connues (dont celles du détecteur d'injection d'instructions) et la conduite à tenir devant un événement de sécurité sont décrites dans [docs/securite.md](docs/securite.md).
