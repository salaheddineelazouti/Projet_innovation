"""Tests de la protection CSRF (falsification de requête intersite) du tableau de bord."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from tecpap.config import Settings
from tecpap.web import create_app

TEMPLATES = Path(__file__).resolve().parent.parent / "tecpap" / "web" / "templates"


@pytest.fixture
def csrf_app(tmp_path):
    """Application avec protection CSRF, sans connexion obligatoire (pour isoler le contrôle du jeton)."""
    app = create_app(Settings.for_tests(tmp_path, csrf_enabled=True))
    yield app
    app.extensions["tecpap"].close()


@pytest.fixture
def client(csrf_app):
    with csrf_app.test_client() as client:
        yield client


@pytest.fixture
def db(csrf_app):
    return csrf_app.extensions["tecpap"].db


@pytest.fixture
def order_id(db, sample_order_data):
    return db.create_order(sample_order_data)


class TestUnsafeMethods:
    """Les requêtes qui modifient des données exigent le jeton de la session."""

    def test_missing_token(self, client, db, order_id):
        response = client.post(f"/api/orders/{order_id}/validate", json={})
        assert response.status_code == 403
        data = response.get_json()
        assert data["success"] is False
        assert "CSRF" in data["error"]
        assert db.get_order(order_id)["statut"] == "en_attente"

    def test_invalid_token(self, client, db, order_id, csrf_token_for):
        csrf_token_for(client)
        response = client.post(f"/api/orders/{order_id}/validate", json={}, headers={"X-CSRF-Token": "jeton-invente"})
        assert response.status_code == 403
        assert db.get_order(order_id)["statut"] == "en_attente"

    def test_token_without_session(self, client, order_id):
        response = client.post(f"/api/orders/{order_id}/validate", json={}, headers={"X-CSRF-Token": "x" * 43})
        assert response.status_code == 403

    def test_token_of_another_session(self, csrf_app, client, order_id, csrf_token_for):
        other_token = csrf_token_for(csrf_app.test_client())
        csrf_token_for(client)
        response = client.post(f"/api/orders/{order_id}/validate", json={}, headers={"X-CSRF-Token": other_token})
        assert response.status_code == 403

    def test_valid_token_in_header(self, client, db, order_id, csrf_token_for):
        token = csrf_token_for(client)
        response = client.post(f"/api/orders/{order_id}/validate", json={}, headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        assert db.get_order(order_id)["statut"] == "validee"

    def test_valid_token_in_form_field(self, client, csrf_token_for):
        token = csrf_token_for(client)
        response = client.post("/logout", data={"csrf_token": token})
        assert response.status_code == 302

    def test_form_field_ignored_for_json_body(self, client, order_id, csrf_token_for):
        token = csrf_token_for(client)
        response = client.post(f"/api/orders/{order_id}/validate", json={"csrf_token": token})
        assert response.status_code == 403

    @pytest.mark.parametrize("method, url", [
        ("put", "/api/orders/1/sage-fields"), ("delete", "/api/backup/delete/backup_20250101_000000.db"),
        ("post", "/api/backup/create"), ("post", "/api/process-emails"), ("patch", "/api/orders/1/update"),
    ])
    def test_every_unsafe_method_is_checked(self, client, method, url):
        assert getattr(client, method)(url, json={}).status_code == 403

    def test_html_form_without_token_gets_error_page(self, client):
        response = client.post("/logout")
        assert response.status_code == 403
        assert "text/html" in response.content_type
        assert "Requête refusée" in response.get_data(as_text=True)

    def test_refusal_is_logged(self, client, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.web.auth"):
            client.post("/api/process-emails")
        assert "jeton CSRF absent ou invalide" in caplog.text

    def test_safe_methods_need_no_token(self, client):
        assert client.get("/api/stats").status_code == 200
        assert client.head("/api/stats").status_code == 200
        assert client.get("/").status_code == 200


class TestExemptions:
    """Webhook Twilio et désactivation."""

    def test_webhook_is_exempt(self, client):
        response = client.post("/webhook/whatsapp", data={"From": "whatsapp:+212600000000", "Body": "salam"})
        assert response.status_code == 200

    def test_disabled_protection(self, flask_client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        assert flask_client.post(f"/api/orders/{order_id}/validate", json={}).status_code == 200


class TestLoginForm:
    """Le formulaire de connexion porte le jeton dans un champ caché."""

    def test_login_without_token_refused(self, secure_client):
        response = secure_client.post("/login", data={"username": "admin", "password": "x"})
        assert response.status_code == 403

    def test_login_page_contains_session_token(self, secure_client):
        html = secure_client.get("/login").get_data(as_text=True)
        with secure_client.session_transaction() as session:
            token = session["csrf_token"]
        assert f'name="csrf_token" value="{token}"' in html

    def test_logged_in_requests(self, logged_in_client, secure_app, sample_order_data):
        order_id = secure_app.extensions["tecpap"].db.create_order(sample_order_data)
        response = logged_in_client.post(f"/api/orders/{order_id}/update", json={"quantite": 10})
        assert response.status_code == 200


class TestPages:
    """Le jeton est transmis aux pages et ajouté aux appels fetch."""

    def test_meta_tag_holds_session_token(self, client):
        html = client.get("/").get_data(as_text=True)
        with client.session_transaction() as session:
            token = session["csrf_token"]
        assert f'<meta name="csrf-token" content="{token}">' in html

    def test_token_is_stable_within_a_session(self, client):
        first = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/").get_data(as_text=True)).group(1)
        second = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/orders").get_data(as_text=True)).group(1)
        assert first == second

    def test_fetch_wrapper_is_installed_before_page_scripts(self, client):
        html = client.get("/").get_data(as_text=True)
        wrapper = html.index("headers.set('X-CSRF-Token', csrfToken)")
        assert wrapper < html.index("function checkNewOrders")
        assert wrapper < html.index("cdn.tailwindcss.com")

    def test_every_template_mutation_goes_through_fetch_or_carries_the_token(self):
        """Aucune requête qui modifie des données n'échappe au jeton.

        Les gabarits envoient leurs modifications par ``fetch`` (en-tête ajouté par base.html) ;
        un formulaire envoyé en POST doit contenir le champ caché ``csrf_token``.
        """
        for template in TEMPLATES.glob("*.html"):
            content = template.read_text(encoding="utf-8")
            assert "XMLHttpRequest" not in content, template.name
            assert "$.ajax" not in content and "sendBeacon" not in content, template.name
            for form in re.findall(r"<form\b[^>]*>.*?</form>", content, flags=re.DOTALL | re.IGNORECASE):
                opening = form[: form.index(">") + 1].lower()
                if 'method="post"' in opening:
                    assert 'name="csrf_token"' in form, f"{template.name} : formulaire POST sans jeton"
                else:
                    assert "method=" not in opening or 'method="get"' in opening, template.name
