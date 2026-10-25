"""Protection des fichiers tableur exportés et fichiers d'export temporaires.

Injection de formules (« CSV injection » dans la terminologie de l'OWASP, organisation de
référence en sécurité des applications web) : un tableur comme Excel interprète comme une
formule une cellule de texte qui commence par ``=``, ``+``, ``-`` ou ``@`` (et, après une
tabulation ou un retour chariot de tête, la suite du texte). Les noms de clients, désignations
et numéros de commande exportés viennent de messages externes : une valeur comme
``=HYPERLINK("http://…"&A2)`` deviendrait une formule active dans le fichier ouvert par l'équipe
commerciale ou importé dans SAGE X3. Les cellules numériques (entiers, réels) ne sont jamais
modifiées : elles restent des nombres.

- Classeur Excel (.xlsx) : la cellule est écrite comme texte (type ``s``) avec l'indicateur
  « quotePrefix » du format de cellule ; la valeur est gardée telle quelle et Excel ne l'évalue
  ni à l'ouverture ni après une modification de la cellule.
- Fichier CSV : le format ne porte pas de type de cellule ; une apostrophe est ajoutée devant
  la valeur, ce qui la fait lire comme du texte.

Chaque export est écrit dans son propre fichier temporaire (nom unique) : deux téléchargements
simultanés n'écrivent jamais dans le même fichier.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pandas as pd

FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
"""Premiers caractères qui font interpréter une cellule de texte comme une formule."""

CSV_TEXT_PREFIX = "'"
"""Caractère ajouté devant une valeur CSV dangereuse pour qu'elle soit lue comme du texte."""


def is_formula_like(value: Any) -> bool:
    """Vrai pour un texte qu'un tableur interpréterait comme une formule (premier caractère dangereux)."""
    return isinstance(value, str) and value.startswith(FORMULA_TRIGGERS)


def neutralize_csv_value(value: Any) -> Any:
    """Valeur CSV sans risque : apostrophe ajoutée devant un texte dangereux, autre valeur inchangée."""
    return CSV_TEXT_PREFIX + value if is_formula_like(value) else value


def neutralize_csv_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Copie du tableau où chaque texte dangereux est précédé d'une apostrophe.

    Seules les colonnes de type objet (textes, valeurs mélangées) sont parcourues ; les colonnes
    numériques sont gardées telles quelles.
    """
    safe = frame.copy()
    for column in safe.columns:
        if safe[column].dtype == object:
            safe[column] = safe[column].map(neutralize_csv_value)
    return safe


def protect_text_cells(worksheet: Any) -> int:
    """Écrit comme texte chaque cellule d'une feuille openpyxl dont le texte ressemble à une formule.

    Returns:
        Le nombre de cellules protégées.
    """
    protected = 0
    for row in worksheet.iter_rows():
        for cell in row:
            if is_formula_like(cell.value):
                cell.data_type = "s"
                cell.quotePrefix = True
                protected += 1
    return protected


def unique_export_path(directory: str, default_name: str) -> str:
    """Crée un fichier vide au nom unique dans ``directory`` et renvoie son chemin.

    Le nom reprend celui de ``default_name`` (« commandes_sage.xlsx » donne
    « commandes_sage_<suffixe aléatoire>.xlsx ») ; le dossier est créé s'il n'existe pas.
    """
    os.makedirs(directory, exist_ok=True)
    stem, extension = os.path.splitext(default_name)
    handle, path = tempfile.mkstemp(prefix=f"{stem}_", suffix=extension, dir=directory)
    os.close(handle)
    return path
