"""Tests de l'authentification du tableau de bord (tecpap.web.auth et routes /login, /logout)."""

from __future__ import annotations

import importlib.util
import logging
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from werkzeug.security import check_password_hash

from tecpap.config import Settings
from tecpap.web import create_app
from tecpap.web.auth import (
    LIMITER_EXTENSION_KEY,
    SESSION_LIFETIME,
    LoginRateLimiter,
    safe_next_url,
    verify_credentials,
)
from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_PASSWORD_HASH

ROOT = Path(__file__).resolve().parent.parent


class FakeClock:
    """Horloge réglable à la main pour le limiteur des échecs de connexion."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(secure_app, secure_settings) -> FakeClock:
    """Remplace le limiteur de l'application par un limiteur piloté par une horloge réglable."""
    fake = FakeClock()
    secure_app.extensions[LIMITER_EXTENSION_KEY] = LoginRateLimiter(
        secure_settings.login_max_attempts, secure_settings.login_lockout_minutes * 60, clock=fake
    )
    return fake


class TestAccessControl:
    """Routes protégées et routes publiques."""

    @pytest.mark.parametrize("url", ["/", "/orders", "/clients", "/analytics", "/backups", "/export/csv"])
    def test_html_page_redirects_to_login(self, secure_client, url):
        response = secure_client.get(url)
        assert response.status_code == 302
        location = urlsplit(response.headers["Location"])
        assert location.path == "/login"
        assert parse_qs(location.query)["next"] == [url]

    def test_query_string_is_kept_in_next(self, secure_client):
        response = secure_client.get("/orders?status=en_attente")
        next_url = parse_qs(urlsplit(response.headers["Location"]).query)["next"][0]
        assert next_url == "/orders?status=en_attente"

    @pytest.mark.parametrize("method, url", [
        ("get", "/api/stats"), ("get", "/api/orders"), ("post", "/api/orders/1/validate"),
        ("put", "/api/orders/1/sage-fields"), ("delete", "/api/backup/delete/backup.db"),
    ])
    def test_api_returns_401_json(self, secure_client, method, url):
        response = getattr(secure_client, method)(url, json={})
        assert response.status_code == 401
        data = response.get_json()
        assert data["success"] is False
        assert data["login_url"] == "/login"
        assert "connectez-vous" in data["error"]

    def test_unknown_url_requires_login(self, secure_client):
        assert secure_client.get("/page-inexistante").status_code == 302
        assert secure_client.get("/api/inexistante").status_code == 401

    def test_public_routes(self, secure_client):
        assert secure_client.get("/healthz").status_code == 200
        assert secure_client.get("/login").status_code == 200
        assert secure_client.get("/static/images/tecpap-logo.png").status_code == 200

    def test_webhook_does_not_require_login(self, secure_client):
        response = secure_client.post("/webhook/whatsapp", data={"From": "whatsapp:+212600000000", "Body": "salam"})
        assert response.status_code == 200
        assert response.content_type.startswith("application/xml")

    def test_everything_open_when_auth_disabled(self, flask_client):
        assert flask_client.get("/").status_code == 200
        assert flask_client.get("/api/stats").status_code == 200

    def test_login_page_redirects_when_auth_disabled(self, flask_client):
        response = flask_client.get("/login?next=/orders")
        assert response.status_code == 302
        assert response.headers["Location"] == "/orders"


class TestLogin:
    """Formulaire de connexion."""

    def test_login_page(self, secure_client):
        html = secure_client.get("/login").get_data(as_text=True)
        assert 'name="username"' in html
        assert 'type="password"' in html
        assert 'name="csrf_token"' in html
        assert 'autocomplete="current-password"' in html

    def test_head_request_is_not_a_login_attempt(self, secure_client, login, clock, secure_settings):
        for _ in range(secure_settings.login_max_attempts + 1):
            assert secure_client.head("/login").status_code == 200
        assert login().status_code == 302

    def test_login_success(self, secure_client, login):
        response = login()
        assert response.status_code == 302
        assert response.headers["Location"] == "/"
        with secure_client.session_transaction() as session:
            assert session["user"] == "admin"
            assert session.permanent is True
        assert secure_client.get("/").status_code == 200

    def test_login_redirects_to_next(self, secure_client, login):
        response = login(next_url="/orders?status=validee")
        assert response.headers["Location"] == "/orders?status=validee"

    def test_logged_in_user_visiting_login_page(self, secure_client, login):
        login()
        response = secure_client.get("/login?next=/clients")
        assert response.status_code == 302
        assert response.headers["Location"] == "/clients"

    @pytest.mark.parametrize("username, password", [
        ("admin", "mauvais-mot-de-passe"), ("autre", TEST_ADMIN_PASSWORD), ("", ""), ("ADMIN", TEST_ADMIN_PASSWORD),
    ])
    def test_login_failure(self, secure_client, login, username, password):
        response = login(username=username, password=password)
        assert response.status_code == 401
        assert "Identifiant ou mot de passe incorrect" in response.get_data(as_text=True)
        with secure_client.session_transaction() as session:
            assert "user" not in session
        assert secure_client.get("/").status_code == 302

    def test_username_is_kept_after_failure(self, login):
        html = login(password="faux").get_data(as_text=True)
        assert 'value="admin"' in html

    def test_failure_is_logged_without_password(self, login, caplog):
        saisie = "-".join(["mot", "secret", "saisi"])
        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.auth_routes"):
            login(password=saisie)
        assert "Échec de connexion" in caplog.text
        assert saisie not in caplog.text

    def test_session_is_renewed_at_login(self, secure_client, login, csrf_token_for):
        before = csrf_token_for(secure_client)
        with secure_client.session_transaction() as session:
            session["valeur_avant_connexion"] = "x"
        login()
        with secure_client.session_transaction() as session:
            assert "valeur_avant_connexion" not in session
            assert session["csrf_token"] != before

    def test_connected_user_shown_with_logout_button(self, logged_in_client):
        html = logged_in_client.get("/").get_data(as_text=True)
        assert 'id="currentUser"' in html
        assert ">admin<" in html
        assert 'action="/logout"' in html
        assert 'id="logoutForm"' in html

    def test_no_logout_button_when_auth_disabled(self, flask_client):
        html = flask_client.get("/").get_data(as_text=True)
        assert 'id="logoutForm"' not in html


class TestOpenRedirect:
    """Seules les adresses relatives de l'application sont acceptées après la connexion."""

    @pytest.mark.parametrize("target", [
        "https://exemple-malveillant.org/", "http://exemple-malveillant.org", "//exemple-malveillant.org",
        "/\\exemple-malveillant.org", "\\\\exemple-malveillant.org", "/\t/exemple-malveillant.org",
        "javascript:alert(1)", "exemple-malveillant.org", "", "/" + "a" * 3000,
    ])
    def test_unsafe_next_refused(self, login, target):
        response = login(next_url=target)
        assert response.status_code == 302
        assert response.headers["Location"] == "/"

    @pytest.mark.parametrize("target", ["https://exemple-malveillant.org", "//exemple-malveillant.org", "/\\x", None,
                                        "/\x00/x", "relatif"])
    def test_safe_next_url_refuses(self, target):
        assert safe_next_url(target) is None

    @pytest.mark.parametrize("target", ["/", "/orders", "/orders?status=en_attente", "/orders/12#notes"])
    def test_safe_next_url_accepts(self, target):
        assert safe_next_url(target) == target


class TestLockout:
    """Limitation des échecs de connexion par adresse IP."""

    def test_lockout_after_max_attempts(self, secure_client, login, clock, secure_settings):
        for _ in range(secure_settings.login_max_attempts - 1):
            assert login(password="faux").status_code == 401
        response = login(password="faux")
        assert response.status_code == 429
        assert "Trop de tentatives" in response.get_data(as_text=True)
        assert int(response.headers["Retry-After"]) == secure_settings.login_lockout_minutes * 60

        # Même le bon mot de passe est refusé pendant le blocage.
        assert login().status_code == 429
        with secure_client.session_transaction() as session:
            assert "user" not in session

    def test_lockout_expires(self, login, clock, secure_settings):
        for _ in range(secure_settings.login_max_attempts):
            login(password="faux")
        clock.now += secure_settings.login_lockout_minutes * 60 + 1
        assert login().status_code == 302

    def test_lockout_is_per_ip(self, login, clock, secure_settings):
        for _ in range(secure_settings.login_max_attempts):
            login(password="faux", REMOTE_ADDR="10.0.0.1")
        assert login(REMOTE_ADDR="10.0.0.1").status_code == 429
        assert login(REMOTE_ADDR="10.0.0.2").status_code == 302

    def test_success_resets_counter(self, secure_client, login, clock, secure_settings, csrf_token_for):
        for _ in range(secure_settings.login_max_attempts - 1):
            login(password="faux")
        assert login().status_code == 302
        secure_client.post("/logout", data={"csrf_token": csrf_token_for(secure_client)})
        for _ in range(secure_settings.login_max_attempts - 1):
            assert login(password="faux").status_code == 401

    def test_old_failures_are_forgotten(self, login, clock, secure_settings):
        for _ in range(secure_settings.login_max_attempts - 1):
            login(password="faux")
        clock.now += secure_settings.login_lockout_minutes * 60
        assert login(password="faux").status_code == 401

    def test_lockout_is_logged(self, login, clock, secure_settings, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.auth_routes"):
            for _ in range(secure_settings.login_max_attempts):
                login(password="faux")
        assert "Trop d'échecs de connexion" in caplog.text


class TestLoginRateLimiter:
    """Limiteur des échecs de connexion, sans application."""

    def test_blocks_after_max_attempts(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(3, 60, clock=clock)
        assert limiter.register_failure("ip") == 0
        assert limiter.register_failure("ip") == 0
        assert limiter.register_failure("ip") == 60
        assert limiter.retry_after("ip") == 60
        clock.now += 59
        assert limiter.retry_after("ip") == pytest.approx(1)
        clock.now += 1
        assert limiter.retry_after("ip") == 0

    def test_reset(self):
        limiter = LoginRateLimiter(2, 60, clock=FakeClock())
        limiter.register_failure("ip")
        limiter.register_failure("ip")
        limiter.reset("ip")
        assert limiter.retry_after("ip") == 0
        assert limiter.register_failure("ip") == 0

    def test_purge_of_expired_entries(self):
        clock = FakeClock()
        limiter = LoginRateLimiter(5, 60, clock=clock)
        for index in range(LoginRateLimiter.PURGE_THRESHOLD):
            limiter.register_failure(f"10.0.{index // 256}.{index % 256}")
        clock.now += 61
        limiter.register_failure("autre")
        assert set(limiter._failures) == {"autre"}

    def test_invalid_parameters(self):
        with pytest.raises(ValueError):
            LoginRateLimiter(0, 60)


class TestLogout:
    """Déconnexion."""

    def test_logout(self, logged_in_client):
        response = logged_in_client.post("/logout")
        assert response.status_code == 302
        assert response.headers["Location"] == "/login"
        with logged_in_client.session_transaction() as session:
            assert "user" not in session
        assert logged_in_client.get("/").status_code == 302

    def test_logout_requires_post(self, logged_in_client):
        assert logged_in_client.get("/logout").status_code == 405

    def test_logout_requires_csrf_token(self, secure_client, login):
        login()
        assert secure_client.post("/logout").status_code == 403
        assert secure_client.get("/").status_code == 200


class TestSession:
    """Durée et cookie de session."""

    def test_session_expires_after_lifetime(self, logged_in_client):
        with logged_in_client.session_transaction() as session:
            session["login_at"] = time.time() - SESSION_LIFETIME.total_seconds() - 1
        assert logged_in_client.get("/").status_code == 302
        assert logged_in_client.get("/api/stats").status_code == 401

    def test_session_without_login_time_is_refused(self, secure_client):
        with secure_client.session_transaction() as session:
            session["user"] = "admin"
        assert secure_client.get("/").status_code == 302

    def test_cookie_settings(self, secure_app):
        assert secure_app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert secure_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert secure_app.config["SESSION_COOKIE_SECURE"] is False
        assert secure_app.config["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=12)

    def test_cookie_flags_in_response(self, secure_client, login):
        cookie = login().headers["Set-Cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie
        assert "Secure" not in cookie

    def test_secure_cookie(self, tmp_path):
        settings = Settings.for_tests(tmp_path, session_cookie_secure=True)
        app = create_app(settings)
        try:
            assert app.config["SESSION_COOKIE_SECURE"] is True
        finally:
            app.extensions["tecpap"].close()


class TestCredentials:
    """Vérification de l'identifiant et du mot de passe."""

    def test_valid(self, secure_settings):
        assert verify_credentials(secure_settings, "admin", TEST_ADMIN_PASSWORD) is True

    def test_password_checked_even_when_username_is_wrong(self, secure_settings, monkeypatch):
        calls = []
        monkeypatch.setattr("tecpap.web.auth.check_password_hash", lambda *args: calls.append(args) or True)
        assert verify_credentials(secure_settings, "intrus", TEST_ADMIN_PASSWORD) is False
        assert len(calls) == 1

    def test_no_hash_means_no_login(self, tmp_path):
        settings = Settings.for_tests(tmp_path, auth_enabled=True)
        assert verify_credentials(settings, "admin", "") is False
        assert verify_credentials(settings, "admin", "admin") is False

    def test_invalid_hash_is_refused_and_logged(self, tmp_path, caplog):
        settings = Settings.for_tests(tmp_path, auth_enabled=True, admin_password_hash="methode$sel$valeur")
        with caplog.at_level(logging.ERROR, logger="tecpap.web.auth"):
            assert verify_credentials(settings, "admin", "x") is False
        assert "ADMIN_PASSWORD_HASH" in caplog.text


class TestMissingPasswordHash:
    """Authentification activée sans empreinte de mot de passe."""

    @pytest.fixture
    def settings(self, tmp_path):
        return Settings.for_tests(tmp_path, auth_enabled=True, csrf_enabled=True)

    def test_startup_warning(self, settings, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.web.auth"):
            app = create_app(settings)
        app.extensions["tecpap"].close()
        assert "ADMIN_PASSWORD_HASH n'est pas défini" in caplog.text
        assert "hash_password.py" in caplog.text

    def test_no_warning_when_hash_configured(self, secure_settings, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.web.auth"):
            app = create_app(secure_settings)
        app.extensions["tecpap"].close()
        assert "ADMIN_PASSWORD_HASH" not in caplog.text

    def test_login_impossible(self, settings, csrf_token_for):
        app = create_app(settings)
        try:
            client = app.test_client()
            html = client.get("/login").get_data(as_text=True)
            assert "La connexion est impossible" in html
            response = client.post("/login", data={"username": "admin", "password": "",
                                                    "csrf_token": csrf_token_for(client)})
            assert response.status_code == 401
            assert client.get("/").status_code == 302
        finally:
            app.extensions["tecpap"].close()


class TestValidatedBy:
    """Auteur enregistré lors de la validation d'une commande."""

    @pytest.fixture
    def order_id(self, secure_app, sample_order_data):
        return secure_app.extensions["tecpap"].db.create_order(sample_order_data)

    def test_defaults_to_connected_user(self, logged_in_client, secure_app, order_id):
        response = logged_in_client.post(f"/api/orders/{order_id}/validate", json={})
        assert response.status_code == 200
        assert secure_app.extensions["tecpap"].db.get_order(order_id)["validated_by"] == "admin"

    def test_explicit_author_is_kept(self, logged_in_client, secure_app, order_id):
        logged_in_client.post(f"/api/orders/{order_id}/validate", json={"validated_by": "Équipe export"})
        assert secure_app.extensions["tecpap"].db.get_order(order_id)["validated_by"] == "Équipe export"

    def test_without_session_defaults_to_commercial(self, flask_client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        flask_client.post(f"/api/orders/{order_id}/validate", json={"validated_by": ["liste"]})
        assert services.db.get_order(order_id)["validated_by"] == "Commercial"

    def test_templates_let_the_server_choose_the_author(self):
        for name in ("orders.html", "order_detail.html"):
            content = (ROOT / "tecpap" / "web" / "templates" / name).read_text(encoding="utf-8")
            assert "validated_by: 'Commercial'" not in content


def _load_hash_password_script():
    spec = importlib.util.spec_from_file_location("hash_password", ROOT / "scripts" / "hash_password.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestHashPasswordScript:
    """Script scripts/hash_password.py."""

    def test_prints_a_valid_hash(self, monkeypatch, capsys):
        script = _load_hash_password_script()
        monkeypatch.setattr(script.getpass, "getpass", lambda prompt="": "un-mot-de-passe-solide")
        assert script.main() == 0
        line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("ADMIN_PASSWORD_HASH="))
        password_hash = line.split("=", 1)[1].strip("'")
        assert check_password_hash(password_hash, "un-mot-de-passe-solide")
        assert "un-mot-de-passe-solide" not in line

    def test_confirmation_mismatch(self, monkeypatch, capsys):
        script = _load_hash_password_script()
        answers = iter(["un-mot-de-passe-solide", "une-autre-saisie"])
        monkeypatch.setattr(script.getpass, "getpass", lambda prompt="": next(answers))
        assert script.main() == 1
        assert "ADMIN_PASSWORD_HASH" not in capsys.readouterr().out

    def test_short_password_refused(self, monkeypatch):
        script = _load_hash_password_script()
        monkeypatch.setattr(script.getpass, "getpass", lambda prompt="": "court")
        assert script.main() == 1


def test_test_hash_matches_test_password():
    assert check_password_hash(TEST_ADMIN_PASSWORD_HASH, TEST_ADMIN_PASSWORD)
