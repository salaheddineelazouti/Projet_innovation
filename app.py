"""Point d'entrée de l'application web TECPAP (tableau de bord de validation des commandes).

- Développement : ``python app.py`` lance le serveur de Flask sur ``FLASK_HOST:FLASK_PORT``
  (127.0.0.1:5000 par défaut).
- Production : ``gunicorn app:app`` (voir le Dockerfile).
"""

from __future__ import annotations

import sys

from tecpap.web import create_app

app = create_app()


def _use_utf8_console() -> None:
    """Force l'UTF-8 sur la console (Windows affiche sinon mal l'arabe et les accents)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    _use_utf8_console()
    settings = app.extensions["tecpap"].settings
    app.run(host=settings.host, port=settings.port, debug=settings.debug)
