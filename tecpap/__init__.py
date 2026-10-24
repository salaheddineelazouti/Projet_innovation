"""TECPAP : saisie automatique des commandes de sacs en papier kraft.

Les commandes arrivent par email (texte, pièces jointes PDF ou images) et par WhatsApp
(texte, notes vocales, images, PDF). Le paquet extrait chaque commande, l'enregistre en base
SQLite et la présente à l'équipe commerciale, qui la valide avant l'export vers l'ERP SAGE X3.
"""

__version__ = "3.0.0"
