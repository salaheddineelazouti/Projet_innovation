"""Réponses d'erreur de l'application web.

Le texte d'une exception n'est jamais renvoyé au navigateur : il peut contenir des chemins de
fichiers, des requêtes SQL ou des extraits de configuration. Les routes consignent l'exception
dans le journal (``logger.exception``) et répondent par un message générique ; seuls les messages
de validation écrits pour l'utilisateur (champ refusé, valeur invalide…) sont renvoyés tels quels.

Les erreurs HTTP non traitées par les routes (page introuvable, méthode non autorisée, erreur
interne) reçoivent une réponse JSON sous ``/api/`` et une page HTML ailleurs.
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

GENERIC_ERROR_MESSAGE = "Une erreur interne est survenue. Le détail est consigné dans le journal du serveur."

# Titre et message des pages d'erreur, par code HTTP.
_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("Requête invalide", "La requête envoyée au serveur est invalide."),
    404: ("Page introuvable", "La page demandée n'existe pas."),
    405: ("Méthode non autorisée", "Cette adresse n'accepte pas ce type de requête."),
    413: ("Requête trop volumineuse", "Les données envoyées dépassent la taille autorisée."),
    500: ("Erreur interne", GENERIC_ERROR_MESSAGE),
}


def is_api_request() -> bool:
    """Vrai pour une requête adressée à l'API JSON (chemin ``/api`` ou ``/api/…``)."""
    return request.path == "/api" or request.path.startswith("/api/")


def json_error(message: str, status: int, **extra: object) -> tuple[Response, int]:
    """Réponse JSON d'erreur ``{"success": false, "error": message, ...}`` avec le code ``status``."""
    return jsonify({"success": False, "error": message, **extra}), status


def server_error() -> tuple[Response, int]:
    """Réponse JSON 500 au message générique (l'exception est consignée par l'appelant)."""
    return json_error(GENERIC_ERROR_MESSAGE, 500)


def error_page(title: str, message: str, status: int) -> tuple[str, int]:
    """Page HTML d'erreur autonome (sans menu latéral), avec le code ``status``."""
    return render_template("error.html", title=title, message=message, status=status), status


def _handle_http_error(error: HTTPException):
    status = error.code or 500
    title, message = _HTTP_ERRORS.get(status, ("Erreur", GENERIC_ERROR_MESSAGE))
    body, status = json_error(message, status) if is_api_request() else error_page(title, message, status)
    # Conserve les en-têtes propres à l'erreur, par exemple « Allow » pour une réponse 405.
    headers = {name: value for name, value in error.get_headers() if name.lower() != "content-type"}
    return body, status, headers


def register_error_handlers(app: Flask) -> None:
    """Installe les réponses en français des erreurs HTTP courantes."""
    for status in _HTTP_ERRORS:
        app.register_error_handler(status, _handle_http_error)
