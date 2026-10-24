"""Authentification du tableau de bord et protection CSRF.

Connexion
    Un seul compte, celui de l'administrateur : identifiant ``ADMIN_USERNAME`` et empreinte
    (hachage) du mot de passe ``ADMIN_PASSWORD_HASH``, vérifiée par
    :func:`werkzeug.security.check_password_hash`. Le mot de passe n'est jamais stocké en clair et
    il n'existe aucun mot de passe par défaut : sans empreinte configurée, la connexion est
    impossible (un avertissement est écrit dans le journal au démarrage).

    Toutes les routes exigent une session ouverte, sauf la page de connexion, la sonde de
    disponibilité ``/healthz``, les fichiers statiques et le webhook Twilio (protégé par la
    signature des requêtes Twilio). Sans session, une page HTML redirige vers
    ``/login?next=…`` et l'API répond 401 en JSON. Une session dure au plus
    :data:`SESSION_LIFETIME` après la connexion, même si elle est utilisée sans interruption.

    Après ``LOGIN_MAX_ATTEMPTS`` échecs depuis une même adresse IP en ``LOGIN_LOCKOUT_MINUTES``
    minutes, les tentatives de cette adresse sont refusées pendant ``LOGIN_LOCKOUT_MINUTES``
    minutes (:class:`LoginRateLimiter`).

Protection CSRF
    La falsification de requête intersite (CSRF, Cross-Site Request Forgery) consiste à faire
    envoyer, par le navigateur d'un utilisateur connecté, une requête qu'il n'a pas voulue depuis
    un autre site. Chaque session reçoit un jeton aléatoire ; toute requête qui modifie des
    données (POST, PUT, PATCH, DELETE) doit le renvoyer, dans l'en-tête ``X-CSRF-Token`` (ajouté
    aux appels ``fetch`` par le script de ``base.html``) ou dans le champ de formulaire
    ``csrf_token``. Le webhook Twilio en est dispensé : il n'est pas appelé par un navigateur.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, current_app, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash

from tecpap.config import Settings
from tecpap.web.errors import error_page, is_api_request

logger = logging.getLogger(__name__)

SESSION_LIFETIME = timedelta(hours=12)
"""Durée maximale d'une session après la connexion."""

SESSION_USER_KEY = "user"
SESSION_LOGIN_AT_KEY = "login_at"
SESSION_CSRF_KEY = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
PUBLIC_ENDPOINTS = frozenset({"auth.login", "api.healthz", "static"})
"""Routes accessibles sans connexion."""
PUBLIC_BLUEPRINTS = frozenset({"webhooks"})
"""Blueprints accessibles sans connexion (webhook Twilio, protégé par sa signature)."""
CSRF_EXEMPT_BLUEPRINTS = frozenset({"webhooks"})

LIMITER_EXTENSION_KEY = "tecpap_login_limiter"
_FORM_MIMETYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data"})
_MAX_NEXT_URL_LENGTH = 2048

AUTH_REQUIRED_MESSAGE = "Authentification requise : connectez-vous au tableau de bord."
CSRF_ERROR_MESSAGE = "Jeton de sécurité (CSRF) absent ou invalide : rechargez la page puis recommencez."


# ---------------------------------------------------------------------- limitation des échecs

class LoginRateLimiter:
    """Compte les échecs de connexion par clé (adresse IP du client) et bloque les clés trop insistantes.

    Après ``max_attempts`` échecs survenus en moins de ``lockout_seconds`` secondes, la clé est
    bloquée pendant ``lockout_seconds`` secondes ; une connexion réussie remet son compteur à zéro.
    Les compteurs sont gardés en mémoire : ils sont propres à chaque processus et perdus au
    redémarrage. Les méthodes peuvent être appelées depuis plusieurs fils d'exécution.

    Args:
        max_attempts: nombre d'échecs qui déclenche le blocage.
        lockout_seconds: durée du blocage, et période sur laquelle les échecs sont comptés.
        clock: horloge en secondes (``time.monotonic`` par défaut, remplaçable dans les tests).
    """

    # Au-delà de ce nombre de clés suivies, les entrées expirées sont supprimées.
    PURGE_THRESHOLD = 1000

    def __init__(self, max_attempts: int, lockout_seconds: float, clock: Callable[[], float] = time.monotonic):
        if max_attempts < 1 or lockout_seconds <= 0:
            raise ValueError("Le nombre d'échecs et la durée du blocage doivent être strictement positifs")
        self.max_attempts = max_attempts
        self.lockout_seconds = float(lockout_seconds)
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float:
        """Secondes de blocage restantes pour ``key`` (0 quand une tentative est permise)."""
        with self._lock:
            now = self._clock()
            until = self._locked_until.get(key)
            if until is None:
                return 0.0
            if until > now:
                return until - now
            del self._locked_until[key]
            return 0.0

    def register_failure(self, key: str) -> float:
        """Compte un échec ; renvoie la durée du blocage s'il vient d'être déclenché, sinon 0."""
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            recent = [moment for moment in self._failures.get(key, []) if now - moment < self.lockout_seconds]
            recent.append(now)
            if len(recent) >= self.max_attempts:
                self._failures.pop(key, None)
                self._locked_until[key] = now + self.lockout_seconds
                return self.lockout_seconds
            self._failures[key] = recent
            return 0.0

    def reset(self, key: str) -> None:
        """Oublie les échecs et le blocage de ``key`` (après une connexion réussie)."""
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def _purge_expired(self, now: float) -> None:
        if len(self._failures) + len(self._locked_until) < self.PURGE_THRESHOLD:
            return
        self._locked_until = {key: until for key, until in self._locked_until.items() if until > now}
        self._failures = {
            key: moments for key, moments in self._failures.items()
            if any(now - moment < self.lockout_seconds for moment in moments)
        }


def get_login_limiter() -> LoginRateLimiter:
    """Limiteur des échecs de connexion de l'application Flask courante."""
    return current_app.extensions[LIMITER_EXTENSION_KEY]


def client_ip() -> str:
    """Adresse IP du client (celle transmise par le proxy inverse si TRUST_PROXY_HEADERS est activé)."""
    return request.remote_addr or "inconnue"


# ---------------------------------------------------------------------- identifiants et session

def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    """Vrai si ``username`` et ``password`` sont ceux de l'administrateur.

    L'identifiant est comparé en temps constant (:func:`hmac.compare_digest`) et le mot de passe est
    toujours vérifié, même quand l'identifiant est faux : la durée de la réponse ne révèle pas
    lequel des deux est erroné. Sans empreinte configurée, la réponse est toujours ``False``.
    """
    expected_hash = settings.admin_password_hash
    if not expected_hash:
        return False
    username_ok = hmac.compare_digest(username.encode("utf-8"), settings.admin_username.encode("utf-8"))
    try:
        password_ok = check_password_hash(expected_hash, password)
    except ValueError:
        logger.error("ADMIN_PASSWORD_HASH n'est pas une empreinte valide : régénérez-la avec scripts/hash_password.py")
        return False
    return username_ok and password_ok


def current_user() -> str | None:
    """Nom de l'utilisateur connecté, ou ``None``."""
    user = session.get(SESSION_USER_KEY)
    return user if isinstance(user, str) and user else None


def login_user(username: str) -> None:
    """Ouvre une session pour ``username``.

    Le contenu de la session précédente est effacé (y compris son jeton CSRF) : un identifiant de
    session obtenu avant la connexion ne peut pas servir après.
    """
    session.clear()
    session.permanent = True
    session[SESSION_USER_KEY] = username
    session[SESSION_LOGIN_AT_KEY] = time.time()
    session[SESSION_CSRF_KEY] = secrets.token_urlsafe(32)


def logout_user() -> None:
    """Ferme la session courante."""
    session.clear()


def session_expired() -> bool:
    """Vrai quand la connexion date de plus de :data:`SESSION_LIFETIME`."""
    login_at = session.get(SESSION_LOGIN_AT_KEY)
    if not isinstance(login_at, (int, float)) or isinstance(login_at, bool):
        return True
    return time.time() - login_at > SESSION_LIFETIME.total_seconds()


def safe_next_url(target: str | None) -> str | None:
    """Renvoie ``target`` si c'est une adresse relative de l'application, sinon ``None``.

    Seuls les chemins qui commencent par une seule barre oblique sont acceptés : une adresse
    absolue (``https://…``), sans protocole (``//site``), avec une barre oblique inversée ou un
    caractère de contrôle (que les navigateurs suppriment ou réinterprètent) permettrait de
    rediriger l'utilisateur vers un autre site après la connexion (redirection ouverte).
    """
    if not target or len(target) > _MAX_NEXT_URL_LENGTH:
        return None
    if "\\" in target or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in target):
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


def _current_relative_url() -> str:
    return request.full_path if request.query_string else request.path


# ---------------------------------------------------------------------- jeton CSRF

def csrf_token() -> str:
    """Jeton CSRF de la session (créé à la première demande)."""
    token = session.get(SESSION_CSRF_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_CSRF_KEY] = token
    return token


def is_valid_csrf_token(provided: str | None) -> bool:
    """Vrai si ``provided`` est le jeton CSRF de la session (comparaison en temps constant)."""
    expected = session.get(SESSION_CSRF_KEY)
    if not provided or not isinstance(expected, str) or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _submitted_csrf_token() -> str | None:
    token = request.headers.get(CSRF_HEADER)
    if not token and request.mimetype in _FORM_MIMETYPES:
        token = request.form.get(CSRF_FORM_FIELD)
    return token


# ---------------------------------------------------------------------- installation

def _log_startup_warnings(settings: Settings) -> None:
    if settings.auth_enabled and not settings.admin_password_hash:
        logger.warning(
            "Authentification activée mais ADMIN_PASSWORD_HASH n'est pas défini : aucune connexion au "
            "tableau de bord n'est possible. Générez une empreinte avec « python scripts/hash_password.py » "
            "puis renseignez ADMIN_PASSWORD_HASH dans le fichier .env."
        )
    elif settings.auth_enabled and settings.admin_password_hash.count("$") < 2:
        logger.warning(
            "ADMIN_PASSWORD_HASH ne ressemble pas à une empreinte produite par scripts/hash_password.py : "
            "la connexion échouera."
        )
    if settings.is_testing:
        return
    if not settings.auth_enabled:
        logger.warning("Authentification désactivée (AUTH_ENABLED=false) : le tableau de bord est accessible "
                       "sans connexion.")
    if not settings.csrf_enabled:
        logger.warning("Protection CSRF désactivée (CSRF_ENABLED=false).")


def init_auth(app: Flask, settings: Settings) -> None:
    """Configure le cookie de session et installe le contrôle d'accès et la protection CSRF.

    Le cookie de session n'est pas lisible par JavaScript (HttpOnly), n'est pas envoyé par les
    requêtes intersites qui modifient des données (SameSite=Lax) et n'est envoyé qu'en HTTPS
    quand ``SESSION_COOKIE_SECURE`` est actif.
    """
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(settings.session_cookie_secure),
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
    )
    app.extensions[LIMITER_EXTENSION_KEY] = LoginRateLimiter(
        max_attempts=settings.login_max_attempts,
        lockout_seconds=settings.login_lockout_minutes * 60,
    )
    _log_startup_warnings(settings)

    @app.before_request
    def require_login():
        """Refuse les requêtes sans session ouverte (sauf routes publiques)."""
        if not settings.auth_enabled:
            return None
        if request.endpoint in PUBLIC_ENDPOINTS or request.blueprint in PUBLIC_BLUEPRINTS:
            return None
        if current_user():
            if not session_expired():
                return None
            logger.info("Session de %s expirée", current_user())
            logout_user()
        if is_api_request():
            return jsonify({"success": False, "error": AUTH_REQUIRED_MESSAGE, "login_url": url_for("auth.login")}), 401
        return redirect(url_for("auth.login", next=_current_relative_url()))

    @app.before_request
    def protect_against_csrf():
        """Refuse les requêtes qui modifient des données sans le jeton CSRF de la session."""
        if not settings.csrf_enabled or request.method in SAFE_METHODS:
            return None
        if request.blueprint in CSRF_EXEMPT_BLUEPRINTS:
            return None
        if is_valid_csrf_token(_submitted_csrf_token()):
            return None
        logger.warning("Requête %s %s refusée : jeton CSRF absent ou invalide (adresse %s)",
                       request.method, request.path, client_ip())
        if is_api_request():
            return jsonify({"success": False, "error": CSRF_ERROR_MESSAGE}), 403
        return error_page("Requête refusée", CSRF_ERROR_MESSAGE, 403)

    @app.context_processor
    def inject_auth_context() -> dict[str, object]:
        return {"csrf_token": csrf_token, "current_user": current_user(), "auth_enabled": settings.auth_enabled}
