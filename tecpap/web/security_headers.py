"""En-têtes HTTP de sécurité ajoutés à toutes les réponses.

- ``X-Content-Type-Options: nosniff`` : le navigateur respecte le type annoncé d'un fichier
  (un fichier texte n'est jamais exécuté comme un script).
- ``X-Frame-Options: DENY`` et ``frame-ancestors 'none'`` : le tableau de bord ne peut pas être
  affiché dans un cadre d'un autre site (protection contre le détournement de clic).
- ``Referrer-Policy: same-origin`` : l'adresse des pages n'est pas transmise aux sites externes.
- ``Content-Security-Policy`` (politique de sécurité du contenu, CSP) : liste des origines d'où
  la page peut charger scripts, styles, polices et images. Elle autorise les CDN (réseaux de
  diffusion de contenu) utilisés par les gabarits, et ``'unsafe-inline'`` pour les scripts et
  styles écrits directement dans les gabarits ; toute autre origine est bloquée.
"""

from __future__ import annotations

from flask import Flask, Response

# CDN chargés par les gabarits (templates/*.html).
TAILWIND_CDN = "https://cdn.tailwindcss.com"        # feuilles de style Tailwind générées dans le navigateur
JSDELIVR_CDN = "https://cdn.jsdelivr.net"           # Chart.js (graphiques)
CDNJS_CDN = "https://cdnjs.cloudflare.com"          # Font Awesome (icônes) : feuille de style et polices
GOOGLE_FONTS_CSS = "https://fonts.googleapis.com"   # feuille de style de la police Inter
GOOGLE_FONTS_FILES = "https://fonts.gstatic.com"    # fichiers de la police Inter

CSP_DIRECTIVES: dict[str, tuple[str, ...]] = {
    "default-src": ("'self'",),
    "script-src": ("'self'", "'unsafe-inline'", TAILWIND_CDN, JSDELIVR_CDN),
    "style-src": ("'self'", "'unsafe-inline'", CDNJS_CDN, GOOGLE_FONTS_CSS),
    "font-src": ("'self'", "data:", CDNJS_CDN, GOOGLE_FONTS_FILES),
    "img-src": ("'self'", "data:"),
    "media-src": ("'self'", "data:"),
    "connect-src": ("'self'",),
    "object-src": ("'none'",),
    "base-uri": ("'self'",),
    "form-action": ("'self'",),
    "frame-ancestors": ("'none'",),
}
"""Directives de la politique de sécurité du contenu ; ``data:`` couvre le son de notification intégré."""

CONTENT_SECURITY_POLICY = "; ".join(f"{name} {' '.join(sources)}" for name, sources in CSP_DIRECTIVES.items())

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}


def init_security_headers(app: Flask) -> None:
    """Ajoute :data:`SECURITY_HEADERS` à chaque réponse (sans écraser un en-tête déjà posé par une route)."""

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
