"""Application web Flask : tableau de bord de validation des commandes par l'équipe commerciale.

:func:`create_app` construit l'application (fabrique d'application) : elle charge la
configuration, ouvre la base, enregistre les blueprints et démarre, hors tests, le
planificateur de sauvegardes.
"""

from __future__ import annotations

from flask import Flask

from tecpap.config import Settings, load_settings
from tecpap.logging_config import configure_logging
from tecpap.web.routes import api, backups, exports, pages, webhooks
from tecpap.web.scheduler import should_start_in_this_process
from tecpap.web.services import EXTENSION_KEY, Services, get_services

__all__ = ["create_app", "get_services"]

BLUEPRINTS = (pages.bp, api.bp, webhooks.bp, backups.bp, exports.bp)


def create_app(settings: Settings | None = None) -> Flask:
    """Crée l'application Flask.

    Args:
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
            Les tests passent ``Settings.for_tests(tmp_path)``.
    """
    settings = settings or load_settings()
    if not settings.is_testing:
        configure_logging(settings.log_level)

    app = Flask(__name__)
    app.config.update(SECRET_KEY=settings.secret_key, TESTING=settings.is_testing)

    services = Services.from_settings(settings)
    app.extensions[EXTENSION_KEY] = services

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

    @app.before_request
    def ensure_database_connection() -> None:
        """Rouvre la connexion à la base si elle a été fermée (après une restauration, par exemple)."""
        if not services.db.connection:
            services.db.connect()

    if (settings.backup_scheduler_enabled and not settings.is_testing
            and should_start_in_this_process(settings.debug)):
        services.backup_scheduler.start()

    return app
