"""Sécurité de l'application web : taille des requêtes, journal des refus de signature, connexions, HSTS, sauvegardes.

Aucun appel réseau ; les requêtes passent par le client de test Flask.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import pytest

from tecpap.chatbot.messages import render
from tecpap.config import Settings
from tecpap.security.events import (
    KIND_INVALID_SIGNATURE,
    MAX_STORED_EVENTS,
    RETENTION_DAYS,
    EventThrottle,
    count_security_events,
    log_security_event,
    purge_security_events,
    recent_security_events,
)
from tecpap.web import MAX_REQUEST_BYTES, create_app
from tecpap.web.auth import (
    ACCOUNT_LIMITER_EXTENSION_KEY,
    ACCOUNT_MAX_DELAY_SECONDS,
    LoginDelayLimiter,
    _log_startup_warnings,
)
from tecpap.web.routes.webhooks import PROCESSING_ERROR_REPLY, processing_error_reply
from tecpap.web.scheduler import BackupScheduler, scheduled_backup_time
from tecpap.web.security_headers import HSTS_HEADER
from tecpap.web.services import Services
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_ADMIN_USERNAME

ROOT = Path(__file__).resolve().parent.parent
WEBHOOK = "/webhook/whatsapp"
AUTH_TOKEN = "jeton-twilio-de-test"
MESSAGE = {"From": "whatsapp:+212612345678", "To": "whatsapp:+14155238886", "Body": "bghit 2000 sachet",
           "NumMedia": "0", "MessageSid": "SM0123456789abcdef0123456789abcdef"}
TOO_LARGE = "x" * (MAX_REQUEST_BYTES + 1024)


class FakeClock:
    """Horloge réglable à la main."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def signed_app(tmp_path):
    """Application dont la signature Twilio est vérifiée."""
    app = create_app(Settings.for_tests(tmp_path, twilio_validate_signature=True, twilio_auth_token=AUTH_TOKEN))
    yield app
    app.extensions["tecpap"].close()


class TestRequestSizeLimit:
    """Un corps de requête plus grand que 1 Mo est refusé (413) avant d'être lu en entier."""

    def test_limit_is_configured(self, flask_app):
        assert flask_app.config["MAX_CONTENT_LENGTH"] == MAX_REQUEST_BYTES == 1024 * 1024

    def test_webhook_refuses_a_large_form(self, flask_client, services):
        response = flask_client.post(WEBHOOK, data=dict(MESSAGE, Body=TOO_LARGE))

        assert response.status_code == 413
        assert "Requête trop volumineuse" in response.get_data(as_text=True)
        assert services.inbox().counts()["pending"] == 0
        assert services.db.get_all_orders() == []

    def test_webhook_refuses_a_large_form_before_checking_the_signature(self, signed_app):
        client = signed_app.test_client()

        response = client.post(WEBHOOK, data=dict(MESSAGE, Body=TOO_LARGE))

        assert response.status_code == 413
        connection = signed_app.extensions["tecpap"].db.connection
        assert recent_security_events(connection, kind=KIND_INVALID_SIGNATURE) == []

    def test_login_refuses_a_large_form(self, secure_client):
        response = secure_client.post("/login", data={"username": "admin", "password": TOO_LARGE})
        assert response.status_code == 413

    def test_json_route_answers_in_json(self, flask_client):
        body = b'{"quantite": "' + TOO_LARGE.encode() + b'"}'
        response = flask_client.post("/api/orders/1/update", data=body, content_type="application/json")
        assert response.status_code == 413
        assert response.get_json()["success"] is False

    def test_message_just_under_the_limit_is_accepted(self, flask_client):
        response = flask_client.post(WEBHOOK, data=dict(MESSAGE, Body="x" * (MAX_REQUEST_BYTES // 2)))
        assert response.status_code == 200


class TestInvalidSignatureJournal:
    """Les refus de signature sont comptés par adresse IP au lieu d'écrire une ligne par requête."""

    @pytest.fixture
    def clock(self, signed_app):
        fake = FakeClock()
        signed_app.extensions["tecpap"].signature_event_throttle = EventThrottle(window_seconds=60, clock=fake)
        return fake

    @staticmethod
    def attack(app, times: int, address: str = "203.0.113.5") -> list[int]:
        client = app.test_client()
        return [client.post(WEBHOOK, data=MESSAGE, environ_base={"REMOTE_ADDR": address}).status_code
                for _ in range(times)]

    @staticmethod
    def events(app):
        return recent_security_events(app.extensions["tecpap"].db.connection, kind=KIND_INVALID_SIGNATURE)

    def test_repeated_refusals_from_one_address_make_one_event(self, signed_app, clock):
        assert self.attack(signed_app, 20) == [403] * 20
        assert len(self.events(signed_app)) == 1

    def test_next_event_counts_the_skipped_refusals(self, signed_app, clock):
        self.attack(signed_app, 5)
        clock.now += 61

        self.attack(signed_app, 1)

        newest = self.events(signed_app)[0]
        assert "4 refus précédent(s)" in newest["details"]
        assert len(self.events(signed_app)) == 2

    def test_each_address_is_recorded(self, signed_app, clock):
        for index in range(3):
            self.attack(signed_app, 4, address=f"203.0.113.{index}")
        assert len(self.events(signed_app)) == 3

    def test_total_is_bounded_when_every_request_has_a_new_address(self, signed_app):
        services = signed_app.extensions["tecpap"]
        services.signature_event_throttle = EventThrottle(window_seconds=60, max_per_window=3, clock=FakeClock())
        for index in range(30):
            self.attack(signed_app, 1, address=f"198.51.100.{index}")
        assert len(self.events(signed_app)) == 3

    def test_refusals_do_not_create_tables_or_write_per_request(self, signed_app, clock):
        connection = signed_app.extensions["tecpap"].db.connection
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        try:
            self.attack(signed_app, 30)
        finally:
            connection.set_trace_callback(None)
        assert not [statement for statement in statements if statement.lstrip().upper().startswith("CREATE")]
        assert len([statement for statement in statements if "INSERT INTO security_events" in statement]) == 1


class TestEventThrottle:
    """EventThrottle : une source, une fenêtre, un événement."""

    def test_one_event_per_source_and_window(self):
        clock = FakeClock()
        throttle = EventThrottle(window_seconds=60, clock=clock)
        assert throttle.check("a").record is True
        assert throttle.check("a").record is False
        assert throttle.check("b").record is True
        clock.now += 60
        decision = throttle.check("a")
        assert decision.record is True and decision.suppressed == 1

    def test_suppressed_count_is_reported_once(self):
        clock = FakeClock()
        throttle = EventThrottle(window_seconds=10, clock=clock)
        throttle.check("a")
        for _ in range(5):
            throttle.check("a")
        clock.now += 10
        assert throttle.check("a").suppressed == 5
        clock.now += 10
        assert throttle.check("a").suppressed == 0

    def test_total_per_window(self):
        clock = FakeClock()
        throttle = EventThrottle(window_seconds=60, max_per_window=2, clock=clock)
        assert [throttle.check(name).record for name in "abc"] == [True, True, False]
        clock.now += 61
        assert throttle.check("c").record is True

    def test_many_sources_are_forgotten(self):
        clock = FakeClock()
        throttle = EventThrottle(window_seconds=1, max_per_window=10**6, clock=clock)
        for index in range(EventThrottle.PURGE_THRESHOLD + 10):
            throttle.check(f"source-{index}")
        clock.now += 5
        throttle.check("nouvelle")
        assert len(throttle._last) < EventThrottle.PURGE_THRESHOLD

    @pytest.mark.parametrize("window, maximum", [(0, 1), (-1, 1), (10, 0)])
    def test_invalid_parameters(self, window, maximum):
        with pytest.raises(ValueError):
            EventThrottle(window_seconds=window, max_per_window=maximum)


class TestSecurityEventRetention:
    """Les événements anciens sont supprimés ; leur nombre est borné."""

    @pytest.fixture
    def connection(self):
        conn = sqlite3.connect(":memory:")
        yield conn
        conn.close()

    NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

    def add(self, connection, days_ago: float, kind: str = "prompt_injection") -> None:
        log_security_event(connection, channel="whatsapp", sender="+212612345678", kind=kind,
                           created_at=self.NOW - timedelta(days=days_ago))

    def test_old_events_are_deleted(self, connection):
        for days in (1, 30, RETENTION_DAYS - 1, RETENTION_DAYS + 1, 400):
            self.add(connection, days)

        deleted = purge_security_events(connection, now=self.NOW)

        assert deleted == 2
        assert count_security_events(connection) == 3

    def test_row_count_is_bounded(self, connection):
        for index in range(10):
            self.add(connection, index / 10)

        deleted = purge_security_events(connection, max_rows=4, now=self.NOW)

        assert deleted == 6
        assert [event["id"] for event in recent_security_events(connection, 10)] == [10, 9, 8, 7]

    def test_nothing_to_delete(self, connection):
        self.add(connection, 1)
        assert purge_security_events(connection, now=self.NOW) == 0

    def test_missing_table_is_not_an_error(self, connection):
        assert purge_security_events(connection, now=self.NOW) == 0

    def test_defaults(self):
        assert RETENTION_DAYS == 180 and MAX_STORED_EVENTS == 100_000

    def test_missing_table_is_created_by_the_first_event(self, connection):
        assert log_security_event(connection, channel="web", sender=None, kind="test") == 1
        assert count_security_events(connection) == 1

    def test_schema_is_not_rechecked_for_each_event(self, connection):
        self.add(connection, 0)
        statements: list[str] = []
        connection.set_trace_callback(statements.append)
        self.add(connection, 0)
        connection.set_trace_callback(None)
        assert not [statement for statement in statements if statement.lstrip().upper().startswith("CREATE")]


class TestLoginDelayLimiter:
    """Ralentissement des essais sur un même identifiant, toutes adresses IP confondues."""

    def make(self, free: int = 3, window: float = 900, maximum: float = 60, clock=None) -> LoginDelayLimiter:
        return LoginDelayLimiter(free, window, maximum, clock=clock or FakeClock())

    def test_free_attempts_have_no_delay(self):
        limiter = self.make(free=3)
        assert [limiter.register_failure("admin") for _ in range(3)] == [0.0, 0.0, 0.0]
        assert limiter.retry_after("admin") == 0.0

    def test_delay_doubles_then_is_capped(self):
        limiter = self.make(free=1, maximum=10)
        assert [limiter.register_failure("admin") for _ in range(6)] == [0.0, 2.0, 4.0, 8.0, 10.0, 10.0]

    def test_delay_is_waited_not_permanent(self):
        clock = FakeClock()
        limiter = self.make(free=1, clock=clock)
        limiter.register_failure("admin")
        limiter.register_failure("admin")
        assert limiter.retry_after("admin") == 2.0
        clock.now += 1.5
        assert limiter.retry_after("admin") == pytest.approx(0.5)
        clock.now += 1
        assert limiter.retry_after("admin") == 0.0

    def test_other_keys_are_not_affected(self):
        limiter = self.make(free=1)
        for _ in range(5):
            limiter.register_failure("admin")
        assert limiter.retry_after("autre") == 0.0

    def test_old_failures_are_forgotten(self):
        clock = FakeClock()
        limiter = self.make(free=2, window=100, clock=clock)
        limiter.register_failure("admin")
        limiter.register_failure("admin")
        clock.now += 101
        assert limiter.register_failure("admin") == 0.0

    def test_reset(self):
        limiter = self.make(free=1)
        limiter.register_failure("admin")
        limiter.register_failure("admin")
        limiter.reset("admin")
        assert limiter.retry_after("admin") == 0.0
        assert limiter.register_failure("admin") == 0.0

    def test_unused_keys_are_purged(self):
        clock = FakeClock()
        limiter = self.make(free=1, window=10, clock=clock)
        for index in range(LoginDelayLimiter.PURGE_THRESHOLD):
            limiter.register_failure(f"utilisateur-{index}")
        clock.now += 20
        limiter.register_failure("nouveau")
        assert len(limiter._failures) < LoginDelayLimiter.PURGE_THRESHOLD

    @pytest.mark.parametrize("free, window, maximum", [(-1, 10, 10), (1, 0, 10), (1, 10, 0)])
    def test_invalid_parameters(self, free, window, maximum):
        with pytest.raises(ValueError):
            LoginDelayLimiter(free, window, maximum)

    def test_maximum_delay_is_short(self):
        """Un tiers qui échoue volontairement ne peut pas bloquer l'administrateur plus d'une minute."""
        assert ACCOUNT_MAX_DELAY_SECONDS <= 60


class TestLoginWithRotatingAddresses:
    """Changer d'adresse IP ne permet pas d'essayer des mots de passe sans limite."""

    @pytest.fixture
    def secure_settings(self, tmp_path):
        return Settings.for_tests(tmp_path, auth_enabled=True, csrf_enabled=True, admin_username=TEST_ADMIN_USERNAME,
                                  admin_password_hash=TEST_ADMIN_PASSWORD_HASH, login_max_attempts=2)

    @pytest.fixture
    def clock(self, secure_app):
        fake = FakeClock()
        limiter = secure_app.extensions[ACCOUNT_LIMITER_EXTENSION_KEY]
        secure_app.extensions[ACCOUNT_LIMITER_EXTENSION_KEY] = LoginDelayLimiter(
            limiter.free_attempts, limiter.window_seconds, limiter.max_delay, clock=fake)
        return fake

    def fail_from_new_addresses(self, login, count: int, username: str = TEST_ADMIN_USERNAME,
                                network: int = 1) -> list[int]:
        return [login(username=username, password="faux", REMOTE_ADDR=f"10.{network}.0.{index}").status_code
                for index in range(count)]

    def test_free_attempts_per_account_are_a_multiple_of_the_ip_limit(self, secure_app):
        limiter = secure_app.extensions[ACCOUNT_LIMITER_EXTENSION_KEY]
        assert limiter.free_attempts == 2 * 4

    def test_delay_after_many_failures_from_different_addresses(self, login, clock):
        assert self.fail_from_new_addresses(login, 8) == [401] * 8

        response = login(password="faux", REMOTE_ADDR="10.2.0.1")

        assert response.status_code == 429
        assert "Trop d'échecs pour cet identifiant" in html.unescape(response.get_data(as_text=True))
        assert int(response.headers["Retry-After"]) == 2

    def test_correct_password_waits_too_but_not_for_long(self, secure_client, login, clock):
        self.fail_from_new_addresses(login, 9)

        waiting = login(REMOTE_ADDR="10.3.0.1")
        assert waiting.status_code == 429
        with secure_client.session_transaction() as session:
            assert "user" not in session

        clock.now += 3
        assert login(REMOTE_ADDR="10.3.0.2").status_code == 302

    def test_success_resets_the_account_counter(self, login, clock, secure_client, csrf_token_for):
        self.fail_from_new_addresses(login, 8)
        assert login(REMOTE_ADDR="10.4.0.1").status_code == 302
        secure_client.post("/logout", data={"csrf_token": csrf_token_for(secure_client)})
        assert self.fail_from_new_addresses(login, 8, network=9) == [401] * 8

    def test_another_username_is_not_slowed_down(self, login, clock):
        self.fail_from_new_addresses(login, 12)
        assert login(username="autre", password="faux", REMOTE_ADDR="10.5.0.1").status_code == 401

    def test_attempts_are_logged(self, login, clock, caplog):
        self.fail_from_new_addresses(login, 8)
        with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.auth_routes"):
            login(password="faux", REMOTE_ADDR="10.6.0.1")
        assert "trop d'échecs sur cet identifiant" in caplog.text
        assert "faux" not in caplog.text


class TestStartupWarnings:
    """Avertissements du démarrage liés aux connexions."""

    def production(self, tmp_path, **overrides) -> Settings:
        values = dict(env="production", secret_key="0123456789abcdef0123456789abcdef", auth_enabled=True,
                      admin_password_hash=TEST_ADMIN_PASSWORD_HASH)
        values.update(overrides)
        return Settings.for_tests(tmp_path, **values)

    def test_production_without_proxy_headers(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.web.auth"):
            _log_startup_warnings(self.production(tmp_path))
        assert "Production sans TRUST_PROXY_HEADERS" in caplog.text
        assert "127.0.0.1:5000:5000" in caplog.text

    def test_production_behind_a_proxy(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.web.auth"):
            _log_startup_warnings(self.production(tmp_path, trust_proxy_headers=True))
        assert "TRUST_PROXY_HEADERS" not in caplog.text

    def test_tests_and_development_are_not_warned(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.web.auth"):
            _log_startup_warnings(Settings.for_tests(tmp_path, auth_enabled=True,
                                                     admin_password_hash=TEST_ADMIN_PASSWORD_HASH))
            _log_startup_warnings(self.production(tmp_path, env="development"))
        assert "TRUST_PROXY_HEADERS" not in caplog.text

    @pytest.mark.parametrize("quote", ["'", '"'])
    def test_quoted_hash_is_flagged(self, tmp_path, caplog, quote):
        settings = Settings.for_tests(tmp_path, auth_enabled=True,
                                      admin_password_hash=f"{quote}{TEST_ADMIN_PASSWORD_HASH}{quote}")
        with caplog.at_level(logging.WARNING, logger="tecpap.web.auth"):
            _log_startup_warnings(settings)
        assert "ne ressemble pas à une empreinte" in caplog.text


class TestStrictTransportSecurity:
    """L'en-tête HSTS n'est envoyé que sur les réponses servies en HTTPS."""

    def test_https_response(self, secure_client):
        response = secure_client.get("/login", base_url="https://localhost")
        assert response.headers[HSTS_HEADER[0]] == HSTS_HEADER[1] == "max-age=31536000"

    def test_http_response(self, secure_client):
        assert "Strict-Transport-Security" not in secure_client.get("/login").headers

    def test_error_response_over_https(self, secure_client):
        response = secure_client.get("/page-inexistante", base_url="https://localhost")
        assert "Strict-Transport-Security" in response.headers

    def test_forwarded_protocol_is_honoured_only_behind_a_trusted_proxy(self, tmp_path):
        headers = {"X-Forwarded-Proto": "https"}
        behind = create_app(Settings.for_tests(tmp_path / "proxy", trust_proxy_headers=True))
        direct = create_app(Settings.for_tests(tmp_path / "direct", trust_proxy_headers=False))
        try:
            assert "Strict-Transport-Security" in behind.test_client().get("/healthz", headers=headers).headers
            assert "Strict-Transport-Security" not in direct.test_client().get("/healthz", headers=headers).headers
        finally:
            behind.extensions["tecpap"].close()
            direct.extensions["tecpap"].close()


class TestBackupActionsArePosts:
    """Une action qui crée un fichier est un POST protégé par le jeton CSRF."""

    @staticmethod
    def files(settings) -> list[str]:
        """Sauvegardes et exports du dossier (sans le fichier d'historique)."""
        folder = Path(settings.backup_dir)
        if not folder.is_dir():
            return []
        return sorted(path.name for path in folder.iterdir() if path.name != "backup_history.json")

    @pytest.mark.parametrize("url", ["/api/backup/download-latest", "/api/backup/export-json"])
    def test_get_is_refused_and_creates_nothing(self, flask_client, test_settings, url):
        before = self.files(test_settings)
        for _ in range(3):
            assert flask_client.get(url).status_code == 405
        assert self.files(test_settings) == before

    def test_download_latest_creates_one_backup_then_downloads_it_by_name(self, flask_client, test_settings):
        before = self.files(test_settings)

        created = flask_client.post("/api/backup/download-latest")

        assert created.status_code == 200
        body = created.get_json()
        assert body["success"] is True and body["filename"].startswith("backup_")
        assert len(self.files(test_settings)) == len(before) + 1
        download = flask_client.get(body["download_url"])
        assert download.status_code == 200
        assert body["filename"] in download.headers["Content-Disposition"]
        assert len(self.files(test_settings)) == len(before) + 1

    def test_export_json_is_sent_without_leaving_a_copy(self, flask_client, test_settings):
        response = flask_client.post("/api/backup/export-json")

        assert response.status_code == 200
        assert "attachment" in response.headers["Content-Disposition"]
        assert "tables" in json.loads(response.data)
        assert not [name for name in self.files(test_settings) if name.startswith("export_")]

    @pytest.mark.parametrize("url", ["/api/backup/download-latest", "/api/backup/export-json"])
    def test_cross_site_post_without_token_is_refused(self, secure_client, login, secure_settings, url):
        assert login().status_code == 302
        before = self.files(secure_settings)

        assert secure_client.post(url).status_code == 403
        assert self.files(secure_settings) == before

    @pytest.mark.parametrize("url", ["/api/backup/download-latest", "/api/backup/export-json"])
    def test_post_with_token_is_accepted(self, logged_in_client, url):
        assert logged_in_client.post(url).status_code == 200

    def test_page_uses_post_requests(self):
        page = (ROOT / "tecpap" / "web" / "templates" / "backups.html").read_text(encoding="utf-8")
        assert "window.location.href = '/api/backup/download-latest'" not in page
        assert "window.location.href = '/api/backup/export-json'" not in page
        assert re.search(r"fetch\('/api/backup/download-latest',\s*\{\s*method:\s*'POST'", page)
        assert re.search(r"fetch\('/api/backup/export-json',\s*\{\s*method:\s*'POST'", page)


class TestSchedulerTimes:
    """L'heure annoncée de la prochaine sauvegarde est celle à laquelle elle a lieu."""

    @pytest.mark.parametrize("now, interval, expected", [
        (datetime(2026, 10, 25, 10, 30), 1, datetime(2026, 10, 25, 11, 0)),
        (datetime(2026, 10, 25, 10, 58), 1, datetime(2026, 10, 25, 12, 0)),
        (datetime(2026, 10, 25, 11, 57), 6, datetime(2026, 10, 25, 18, 0)),
        (datetime(2026, 10, 25, 7, 0), 6, datetime(2026, 10, 25, 12, 0)),
        (datetime(2026, 10, 25, 23, 58), 6, datetime(2026, 10, 26, 6, 0)),
        (datetime(2026, 10, 25, 23, 10), 6, datetime(2026, 10, 26, 0, 0)),
    ])
    def test_scheduled_backup_time(self, now, interval, expected):
        assert scheduled_backup_time(now, interval) == expected

    def test_target_is_a_round_hour_at_least_the_minimum_gap_away(self):
        start = datetime(2026, 10, 25, 0, 0)
        for minutes in range(0, 24 * 60, 7):
            now = start + timedelta(minutes=minutes)
            for interval in (1, 3, 6, 12, 24):
                target = scheduled_backup_time(now, interval, 300)
                assert target.minute == target.second == 0 and target.hour % interval == 0
                assert (target - now).total_seconds() >= 300

    def test_reported_time_is_the_time_waited(self, tmp_path, monkeypatch):
        fixed = datetime(2026, 10, 25, 10, 58, 30)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed

        monkeypatch.setattr("tecpap.web.scheduler.datetime", FrozenDatetime)
        scheduler = BackupScheduler(str(tmp_path / "orders.db"), str(tmp_path / "backups"), interval_hours=1)
        waits: list[float] = []

        def fake_sleep(seconds: float) -> bool:
            waits.append(seconds)
            if len(waits) == 1:  # attente du démarrage
                return False
            scheduler.running = False  # attente de la sauvegarde : on s'arrête là
            return True

        monkeypatch.setattr(scheduler, "_sleep", fake_sleep)
        scheduler.running = True

        scheduler._run_scheduler()

        assert scheduler.next_backup == datetime(2026, 10, 25, 12, 0)
        assert waits[1] == (scheduler.next_backup - fixed).total_seconds() == 3690
        assert scheduler.get_status()["next_backup"] == "2026-10-25T12:00:00"


class TestBackgroundTasksStartup:
    """Démarrage du planificateur et de la maintenance périodique."""

    def build(self, tmp_path, **overrides):
        values = dict(env="development", backup_scheduler_enabled=True)
        values.update(overrides)
        return create_app(Settings.for_tests(tmp_path, **values))

    def test_both_start_when_not_debugging(self, tmp_path):
        with patch.object(BackupScheduler, "start") as scheduler, patch.object(Services, "start_maintenance") as maintenance:
            app = self.build(tmp_path)
        scheduler.assert_called_once()
        maintenance.assert_called_once()
        app.extensions["tecpap"].close()

    def test_debug_mode_without_reloader_warns_instead_of_silently_skipping(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
        with caplog.at_level(logging.WARNING, logger="tecpap.web"), \
                patch.object(BackupScheduler, "start") as scheduler, \
                patch.object(Services, "start_maintenance") as maintenance:
            app = self.build(tmp_path, debug=True)
        scheduler.assert_not_called()
        maintenance.assert_not_called()
        assert "FLASK_DEBUG=true" in caplog.text and "gunicorn" in caplog.text
        app.extensions["tecpap"].close()

    def test_debug_mode_in_the_reloader_child_starts_both(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
        with patch.object(BackupScheduler, "start") as scheduler, patch.object(Services, "start_maintenance") as maintenance:
            app = self.build(tmp_path, debug=True)
        scheduler.assert_called_once()
        maintenance.assert_called_once()
        app.extensions["tecpap"].close()

    def test_no_warning_when_the_scheduler_is_disabled(self, tmp_path, monkeypatch, caplog):
        monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
        with caplog.at_level(logging.WARNING, logger="tecpap.web"):
            app = self.build(tmp_path, debug=True, backup_scheduler_enabled=False)
        assert "FLASK_DEBUG=true" not in caplog.text
        app.extensions["tecpap"].close()

    def test_services_are_closed_at_process_exit(self, tmp_path):
        registered = []
        with patch("tecpap.web.atexit.register", side_effect=registered.append), \
                patch.object(Services, "start_maintenance"):
            app = self.build(tmp_path, backup_scheduler_enabled=False)
        services = app.extensions["tecpap"]
        assert [callback.__self__ for callback in registered] == [services]
        assert registered[0].__func__ is Services.close
        services.close()

    def test_nothing_is_registered_in_tests(self, tmp_path):
        registered = []
        with patch("tecpap.web.atexit.register", side_effect=registered.append):
            app = create_app(Settings.for_tests(tmp_path))
        assert registered == []
        app.extensions["tecpap"].close()


class TestProcessingErrorReply:
    """Le message d'erreur générique est rédigé dans la langue du client."""

    @pytest.mark.parametrize("body, language", [
        ("bghit 2000 sachet", "darija"),
        ("Bonjour, je voudrais 2000 sachets pour demain s'il vous plaît", "fr"),
        ("مرحبا، أريد ألفي كيس من فضلك", "ar"),
        ("", "fr"),
        (None, "fr"),
    ])
    def test_language_of_the_message(self, body, language):
        assert processing_error_reply({"Body": body}) == render("error", language)

    def test_missing_body(self):
        assert processing_error_reply({}) == render("error", "fr")

    def test_french_fallback_when_the_template_cannot_be_written(self, caplog):
        with patch("tecpap.web.routes.webhooks.detect_language", side_effect=RuntimeError("détection en panne")):
            with caplog.at_level(logging.WARNING, logger="tecpap.web.routes.webhooks"):
                reply = processing_error_reply({"Body": "bghit 2000 sachet"})
        assert reply == PROCESSING_ERROR_REPLY
        assert "Message d'erreur non rédigé" in caplog.text

    def test_webhook_error_answer_is_in_the_language_of_the_client(self, flask_client, services, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("panne")

        monkeypatch.setattr(type(services), "assistant", broken)
        response = flask_client.post(WEBHOOK, data=dict(MESSAGE, Body="مرحبا، أريد ألفي كيس من فضلك"))

        assert response.status_code == 200
        assert ElementTree.fromstring(response.get_data(as_text=True)).findtext("Message") == render("error", "ar")
