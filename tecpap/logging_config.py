"""Configuration de la journalisation (module standard ``logging``).

Les modules du paquet écrivent leurs messages avec ``logging.getLogger(__name__)`` ;
seuls les points d'entrée (application web, scripts) appellent :func:`configure_logging`
pour choisir le niveau et le format de sortie.

Chaque ligne indique le fil d'exécution qui l'a écrite (``%(threadName)s``) : les messages des
tâches de fond (traitement différé des messages WhatsApp, sauvegardes) se distinguent ainsi de
ceux des requêtes.

Les bibliothèques des services externes (:data:`QUIET_LOGGERS`) écrivent au niveau INFO une
ligne par appel, avec l'adresse appelée et ses en-têtes : celle de l'API Twilio contient
l'identifiant du compte. Elles sont limitées au niveau WARNING, sauf avec ``LOG_LEVEL=DEBUG``.
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s : %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

QUIET_LOGGERS: tuple[str, ...] = ("twilio.http_client", "httpx", "httpx2", "httpcore", "httpcore2", "openai")
"""Journaux des bibliothèques Twilio, OpenAI et de leurs clients HTTP."""

# Attribut posé sur le gestionnaire installé par ce module, pour ne jamais l'ajouter deux fois.
_HANDLER_MARKER = "_tecpap_handler"


def _numeric_level(level: str | int) -> int:
    if isinstance(level, str):
        resolved = logging.getLevelName(level.upper())
        return resolved if isinstance(resolved, int) else logging.INFO
    return level


def configure_logging(level: str | int = "INFO") -> None:
    """Installe un gestionnaire vers la sortie d'erreur et règle le niveau du journal racine.

    La fonction peut être appelée plusieurs fois (par exemple une fois par application créée
    dans les tests) : le gestionnaire n'est installé qu'une seule fois, seuls les niveaux changent.
    Les journaux de :data:`QUIET_LOGGERS` reçoivent au moins le niveau WARNING, sauf au niveau DEBUG.

    Args:
        level: nom du niveau (« DEBUG », « INFO », « WARNING »…) ou valeur numérique.
    """
    numeric_level = _numeric_level(level)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    quiet_level = numeric_level if numeric_level <= logging.DEBUG else max(numeric_level, logging.WARNING)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(quiet_level)
    if any(getattr(handler, _HANDLER_MARKER, False) for handler in root.handlers):
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)
