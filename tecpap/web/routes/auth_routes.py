"""Connexion et déconnexion de l'administrateur du tableau de bord (voir :mod:`tecpap.web.auth`)."""

from __future__ import annotations

import logging
import math

from flask import Blueprint, redirect, render_template, request, url_for

from tecpap.web.auth import (
    client_ip,
    current_user,
    get_login_limiter,
    login_user,
    logout_user,
    safe_next_url,
    session_expired,
    verify_credentials,
)
from tecpap.web.services import get_services

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

INVALID_CREDENTIALS_MESSAGE = "Identifiant ou mot de passe incorrect."
_MAX_USERNAME_LENGTH = 150


def _lockout_message(seconds: float) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return f"Trop de tentatives de connexion. Réessayez dans {minutes} minute{'s' if minutes > 1 else ''}."


def _render_login(next_url: str | None, *, error: str | None = None, username: str = "", status: int = 200,
                  retry_after: float | None = None):
    settings = get_services().settings
    body = render_template(
        "login.html",
        next_url=next_url or "",
        error=error,
        username=username,
        password_configured=bool(settings.admin_password_hash),
    )
    headers = {"Retry-After": str(math.ceil(retry_after))} if retry_after else {}
    return body, status, headers


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Page de connexion ; en POST, vérifie l'identifiant et le mot de passe."""
    settings = get_services().settings
    next_url = safe_next_url(request.values.get("next"))
    home = url_for("pages.index")
    if not settings.auth_enabled:
        return redirect(next_url or home)
    # GET et HEAD affichent la page ; seul un POST est une tentative de connexion.
    if request.method != "POST":
        if current_user() and not session_expired():
            return redirect(next_url or home)
        return _render_login(next_url)

    limiter = get_login_limiter()
    ip = client_ip()
    username = request.form.get("username", "").strip()[:_MAX_USERNAME_LENGTH]
    password = request.form.get("password", "")

    retry_after = limiter.retry_after(ip)
    if retry_after:
        logger.warning("Tentative de connexion refusée depuis %s : adresse bloquée après trop d'échecs", ip)
        return _render_login(next_url, error=_lockout_message(retry_after), username=username, status=429,
                             retry_after=retry_after)

    if verify_credentials(settings, username, password):
        limiter.reset(ip)
        login_user(settings.admin_username)
        logger.info("Connexion de %s depuis %s", settings.admin_username, ip)
        return redirect(next_url or home)

    lockout = limiter.register_failure(ip)
    if lockout:
        logger.warning("Trop d'échecs de connexion depuis %s : tentatives bloquées pendant %d min",
                       ip, settings.login_lockout_minutes)
        return _render_login(next_url, error=_lockout_message(lockout), username=username, status=429,
                             retry_after=lockout)
    logger.warning("Échec de connexion depuis %s", ip)
    return _render_login(next_url, error=INVALID_CREDENTIALS_MESSAGE, username=username, status=401)


@bp.route("/logout", methods=["POST"])
def logout():
    """Ferme la session et revient à la page de connexion."""
    user = current_user()
    logout_user()
    if user:
        logger.info("Déconnexion de %s", user)
    if not get_services().settings.auth_enabled:
        return redirect(url_for("pages.index"))
    return redirect(url_for("auth.login"))
