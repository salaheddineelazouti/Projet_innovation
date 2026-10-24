"""Configuration de la journalisation (module standard ``logging``).

Les modules du paquet écrivent leurs messages avec ``logging.getLogger(__name__)`` ;
seuls les points d'entrée (application web, scripts) appellent :func:`configure_logging`
pour choisir le niveau et le format de sortie.
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s : %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Attribut posé sur le gestionnaire installé par ce module, pour ne jamais l'ajouter deux fois.
_HANDLER_MARKER = "_tecpap_handler"


def configure_logging(level: str | int = "INFO") -> None:
    """Installe un gestionnaire vers la sortie d'erreur et règle le niveau du journal racine.

    La fonction peut être appelée plusieurs fois (par exemple une fois par application créée
    dans les tests) : le gestionnaire n'est installé qu'une seule fois, seul le niveau change.

    Args:
        level: nom du niveau (« DEBUG », « INFO », « WARNING »…) ou valeur numérique.
    """
    if isinstance(level, str):
        resolved = logging.getLevelName(level.upper())
        numeric_level = resolved if isinstance(resolved, int) else logging.INFO
    else:
        numeric_level = level

    root = logging.getLogger()
    root.setLevel(numeric_level)
    if any(getattr(handler, _HANDLER_MARKER, False) for handler in root.handlers):
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
