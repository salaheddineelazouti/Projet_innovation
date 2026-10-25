"""En-têtes HTTP de sécurité ajoutés à toutes les réponses.

- ``X-Content-Type-Options: nosniff`` : le navigateur respecte le type annoncé d'un fichier
  (un fichier texte n'est jamais exécuté comme un script).
- ``X-Frame-Options: DENY`` et ``frame-ancestors 'none'`` : le tableau de bord ne peut pas être
  affiché dans un cadre d'un autre site (protection contre le détournement de clic).
- ``Referrer-Policy: same-origin`` : l'adresse des pages n'est pas transmise aux sites externes.
- ``Content-Security-Policy`` (politique de sécurité du contenu, CSP) : liste des origines d'où
  la page peut charger scripts, styles, polices et images. Elle autorise les CDN (réseaux de
  diffusion de contenu) utilisés par les gabarits, et ``'unsafe-inline'`` pour les scripts et
  styles écrits directement dans les gabarits ; toute autre origine est bloquée. Le CDN jsdelivr
  sert n'importe quel paquet npm : il n'est autorisé que pour le dossier de la version de
  Chart.js employée (une source qui se termine par « / » désigne tout ce dont l'adresse
  commence par ce chemin). Tant que les gabarits contiennent des scripts en ligne, cette
  politique ne protège pas contre une injection de code HTML : c'est l'échappement automatique
  de Jinja qui l'empêche.
- ``Strict-Transport-Security`` (HSTS, :data:`HSTS_HEADER`) sur les réponses servies en HTTPS
  (directement, ou derrière un proxy inverse avec ``TRUST_PROXY_HEADERS``) : le navigateur
  n'utilise plus que HTTPS pour ce site pendant un an. L'en-tête n'est jamais envoyé en HTTP
  simple, où les navigateurs l'ignorent et où le tableau de bord doit rester accessible.
"""

from __future__ import annotations

from flask import Flask, Response, request

# CDN chargés par les gabarits (templates/*.html).
TAILWIND_CDN = "https://cdn.tailwindcss.com"        # feuilles de style Tailwind générées dans le navigateur
JSDELIVR_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/"  # Chart.js (graphiques), version fixée
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

HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000")
"""En-tête HSTS (un an) ajouté aux réponses servies en HTTPS."""


def init_security_headers(app: Flask) -> None:
    """Ajoute :data:`SECURITY_HEADERS` à chaque réponse, et :data:`HSTS_HEADER` en HTTPS.

    Un en-tête déjà posé par une route n'est pas écrasé. Le protocole est celui de la requête
    reçue ; derrière un proxy inverse, il n'est connu que si ``TRUST_PROXY_HEADERS`` est actif
    (en-tête ``X-Forwarded-Proto``).
    """

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.is_secure:
            response.headers.setdefault(*HSTS_HEADER)
        return response
