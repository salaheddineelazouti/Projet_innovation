"""Application web Flask : tableau de bord de validation des commandes par l'équipe commerciale.

:func:`create_app` construit l'application (fabrique d'application) : elle charge la
configuration, ouvre la base, enregistre les blueprints, installe le contrôle d'accès, la
protection CSRF et les en-têtes de sécurité (voir :mod:`tecpap.web.auth` et
:mod:`tecpap.web.security_headers`) et démarre, hors tests, le planificateur de sauvegardes et
la maintenance périodique (reprise des messages WhatsApp en attente, purge des journaux).

Le corps d'une requête est limité à :data:`MAX_REQUEST_BYTES` octets (réponse 413 au-delà) :
aucune route ne reçoit de fichier (Twilio transmet les médias par adresse), et sans limite une
requête anonyme sur le webhook ou la page de connexion serait lue entièrement en mémoire.
"""

from __future__ import annotations

import atexit
import logging

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from tecpap.config import Settings, load_settings
from tecpap.llm.client import LLMClient
from tecpap.logging_config import configure_logging
from tecpap.web.auth import init_auth
from tecpap.web.errors import register_error_handlers
from tecpap.web.routes import api, assistant, auth_routes, backups, exports, pages, webhooks
from tecpap.web.scheduler import should_start_in_this_process
from tecpap.web.security_headers import init_security_headers
from tecpap.web.services import EXTENSION_KEY, Services, get_services

__all__ = ["MAX_REQUEST_BYTES", "create_app", "get_services"]

logger = logging.getLogger(__name__)

BLUEPRINTS = (auth_routes.bp, pages.bp, assistant.bp, api.bp, webhooks.bp, backups.bp, exports.bp)

MAX_REQUEST_BYTES = 1024 * 1024
"""Taille maximale du corps d'une requête (1 Mo)."""


def create_app(settings: Settings | None = None, *, llm: LLMClient | None = None) -> Flask:
    """Crée l'application Flask.

    Args:
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
            Les tests passent ``Settings.for_tests(tmp_path)``.
        llm: client des modèles de langue partagé par les services ; par défaut construit depuis
            la configuration (les tests y substituent une doublure sans accès réseau).
    """
    settings = settings or load_settings()
    if not settings.is_testing:
        configure_logging(settings.log_level)

    app = Flask(__name__)
    app.config.update(SECRET_KEY=settings.secret_key, TESTING=settings.is_testing,
                      MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES)
    if settings.trust_proxy_headers:
        # Derrière un proxy inverse : adresse du client, protocole (https) et nom d'hôte d'origine
        # sont lus dans les en-têtes X-Forwarded-For, X-Forwarded-Proto et X-Forwarded-Host
        # écrits par un seul proxy.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    services = Services.from_settings(settings, llm=llm)
    app.extensions[EXTENSION_KEY] = services

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

    @app.before_request
    def ensure_database_connection() -> None:
        """Rouvre la connexion à la base si elle a été fermée (après une restauration, par exemple)."""
        if not services.db.connection:
            services.db.connect()

    init_auth(app, settings)
    init_security_headers(app)
    register_error_handlers(app)
    webhooks.log_signature_settings(settings)
    if not settings.is_testing:
        # Arrêt du processus (SIGTERM de gunicorn ou de docker stop) : tâches de fond terminées puis
        # connexions à la base fermées. Un message dont le traitement est coupé reste dans la file
        # durable et est repris au démarrage suivant.
        atexit.register(services.close)
    _start_background_tasks(services, settings)
    return app


def _start_background_tasks(services: Services, settings: Settings) -> None:
    """Démarre, hors tests, le planificateur de sauvegardes et la maintenance périodique.

    En mode débogage, seul le processus enfant du rechargeur de Werkzeug les démarre ; un autre
    serveur (gunicorn) n'a pas de rechargeur : le mode débogage y empêche donc le démarrage, ce
    qui est signalé dans le journal (et refusé en production par la configuration).
    """
    if settings.is_testing:
        return
    if not should_start_in_this_process(settings.debug):
        if settings.backup_scheduler_enabled:
            logger.warning(
                "FLASK_DEBUG=true : sauvegardes automatiques et reprise des messages WhatsApp non démarrées dans ce "
                "processus. Avec « python app.py », elles tournent dans le processus enfant du rechargeur de "
                "Werkzeug ; sous un autre serveur (gunicorn), désactivez FLASK_DEBUG."
            )
        return
    if settings.backup_scheduler_enabled:
        services.backup_scheduler.start()
    services.start_maintenance(webhooks.resume_pending_messages)
