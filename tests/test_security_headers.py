"""Tests des en-têtes HTTP de sécurité et de la politique de sécurité du contenu (CSP)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tecpap.web.security_headers import CONTENT_SECURITY_POLICY, CSP_DIRECTIVES, SECURITY_HEADERS

TEMPLATES = Path(__file__).resolve().parent.parent / "tecpap" / "web" / "templates"

# Feuilles de style externes : polices qu'elles chargent elles-mêmes (font-src).
FONT_HOSTS_OF_STYLESHEETS = {
    "https://cdnjs.cloudflare.com": "https://cdnjs.cloudflare.com",  # Font Awesome : webfonts/ sur le même CDN
    "https://fonts.googleapis.com": "https://fonts.gstatic.com",      # Google Fonts : fichiers sur gstatic
}


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _source_allows(source: str, url: str) -> bool:
    """Vrai quand la source d'une directive CSP autorise l'adresse ``url``.

    Une source sans chemin désigne toute l'origine ; une source dont le chemin se termine par « / »,
    tout ce qui commence par ce chemin ; sinon, ce seul fichier.
    """
    if not source.startswith("https://"):
        return False
    parts = urlsplit(source)
    if not parts.path:
        return _origin(url) == _origin(source)
    if parts.path.endswith("/"):
        return url.startswith(source)
    return url == source


def _allowed(url: str, sources: tuple[str, ...]) -> bool:
    return any(_source_allows(source, url) for source in sources)


def _template_resources() -> tuple[set[str], set[str]]:
    """Adresses des scripts et des feuilles de style externes chargés par les gabarits."""
    scripts, styles = set(), set()
    for template in TEMPLATES.glob("*.html"):
        content = template.read_text(encoding="utf-8")
        scripts |= set(re.findall(r'<script[^>]+src="(https?://[^"]+)"', content))
        for tag in re.findall(r"<link\b[^>]*>", content):
            href = re.search(r'href="(https?://[^"]+)"', tag)
            if href and "stylesheet" in tag:
                styles.add(href.group(1))
    return scripts, styles


@pytest.fixture
def pages(logged_in_client, secure_app, sample_order_data, sample_whatsapp_order):
    """Adresses de toutes les pages HTML, avec une commande et un client existants."""
    db = secure_app.extensions["tecpap"].db
    order_id = db.create_order(sample_order_data)
    db.create_order(sample_whatsapp_order)
    client_id = db.get_order(order_id)["client_id"]
    conversation_id = secure_app.extensions["tecpap"].conversation_store().get_or_create("+212612345678").id
    return [
        "/", "/orders", "/orders?status=en_attente", f"/orders/{order_id}", "/process", "/analytics", "/clients",
        f"/clients/{client_id}", "/alerts", "/whatsapp", "/sage", "/backups",
        "/conversations", f"/conversations/{conversation_id}", "/knowledge", "/security",
    ]


class TestHeaders:
    """En-têtes présents sur toutes les réponses."""

    def _assert_headers(self, response):
        for name, value in SECURITY_HEADERS.items():
            assert response.headers.get(name) == value, name

    def test_header_values(self):
        assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"
        assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
        assert SECURITY_HEADERS["Referrer-Policy"] == "same-origin"

    def test_every_page_renders_with_headers(self, logged_in_client, pages):
        for url in pages:
            response = logged_in_client.get(url)
            assert response.status_code == 200, url
            assert "text/html" in response.content_type
            self._assert_headers(response)

    def test_every_page_route_is_covered(self, secure_app, pages):
        """Toute page HTML de l'application figure dans la liste rendue ci-dessus."""
        covered = {re.sub(r"\d+", "<id>", url.split("?")[0]) for url in pages}
        for rule in secure_app.url_map.iter_rules():
            if "GET" not in rule.methods or rule.endpoint in ("static", "auth.login", "api.healthz"):
                continue
            if rule.rule.startswith(("/api/", "/export/", "/webhook/")):
                continue
            assert re.sub(r"<[^>]+>", "<id>", rule.rule) in covered, rule.rule

    def test_login_and_error_pages(self, secure_client):
        self._assert_headers(secure_client.get("/login"))
        self._assert_headers(secure_client.post("/login", data={}))  # 403 : jeton CSRF absent

    @pytest.mark.parametrize("url", ["/api/stats", "/healthz", "/static/images/tecpap-logo.png", "/page-inexistante"])
    def test_other_responses(self, flask_client, url):
        self._assert_headers(flask_client.get(url))

    def test_webhook_and_downloads(self, flask_client):
        self._assert_headers(flask_client.post("/webhook/whatsapp", data={"From": "whatsapp:+212600000000"}))
        self._assert_headers(flask_client.get("/export/csv"))

    def test_redirect(self, secure_client):
        self._assert_headers(secure_client.get("/orders"))


class TestContentSecurityPolicy:
    """La CSP autorise exactement les ressources des gabarits."""

    def test_external_scripts_are_allowed(self):
        scripts, _ = _template_resources()
        assert scripts, "aucun script externe trouvé dans les gabarits"
        for url in scripts:
            assert _allowed(url, CSP_DIRECTIVES["script-src"]), url

    def test_external_stylesheets_and_their_fonts_are_allowed(self):
        _, styles = _template_resources()
        assert styles
        for url in styles:
            assert _allowed(url, CSP_DIRECTIVES["style-src"]), url
            assert FONT_HOSTS_OF_STYLESHEETS[_origin(url)] in CSP_DIRECTIVES["font-src"]

    def test_no_unused_cdn(self):
        """Aucune source externe n'est autorisée sans être utilisée par un gabarit."""
        scripts, styles = _template_resources()
        used = scripts | styles | {FONT_HOSTS_OF_STYLESHEETS[_origin(url)] for url in styles}
        allowed = {source for sources in CSP_DIRECTIVES.values() for source in sources if source.startswith("https://")}
        for source in allowed:
            assert any(_source_allows(source, url) or _source_allows(source, url + "/") for url in used), source

    def test_every_external_url_in_templates_is_covered(self):
        allowed = tuple(source for sources in CSP_DIRECTIVES.values() for source in sources)
        for template in TEMPLATES.glob("*.html"):
            for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', template.read_text(encoding="utf-8")):
                assert _allowed(url, allowed), f"{template.name} : {url}"

    def test_jsdelivr_is_limited_to_the_chart_library(self):
        """Le CDN jsdelivr sert n'importe quel paquet npm : seul le dossier de Chart.js est autorisé."""
        sources = CSP_DIRECTIVES["script-src"]
        assert "https://cdn.jsdelivr.net" not in sources
        assert _allowed("https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js", sources)
        assert not _allowed("https://cdn.jsdelivr.net/npm/lodash@4.17.21/lodash.min.js", sources)
        assert not _allowed("https://cdn.jsdelivr.net/npm/chart.js@4.4.1-evil/x.js", sources)

    def test_inline_code_is_allowed(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "<script>" in base and "<style>" in base and "onclick=" in base
        assert "'unsafe-inline'" in CSP_DIRECTIVES["script-src"]
        assert "'unsafe-inline'" in CSP_DIRECTIVES["style-src"]

    def test_embedded_notification_sound_is_allowed(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert 'src="data:audio/' in base
        assert "data:" in CSP_DIRECTIVES["media-src"]

    def test_restrictive_directives(self):
        assert CSP_DIRECTIVES["default-src"] == ("'self'",)
        assert CSP_DIRECTIVES["connect-src"] == ("'self'",)
        assert CSP_DIRECTIVES["object-src"] == ("'none'",)
        assert CSP_DIRECTIVES["frame-ancestors"] == ("'none'",)
        assert CSP_DIRECTIVES["form-action"] == ("'self'",)
        assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY

    def test_policy_string(self):
        assert CONTENT_SECURITY_POLICY.startswith("default-src 'self'; script-src 'self' 'unsafe-inline' ")
        assert CONTENT_SECURITY_POLICY.count(";") == len(CSP_DIRECTIVES) - 1
