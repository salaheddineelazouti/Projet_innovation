"""Tests de la fabrique d'application (tecpap.web.create_app), des blueprints et du webhook WhatsApp."""

import importlib
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from tecpap import __version__
from tecpap.config import Settings
from tecpap.web import create_app, get_services
from tecpap.web.scheduler import BackupScheduler, next_backup_time, should_start_in_this_process
from tecpap.web.services import Services

ROOT = Path(__file__).resolve().parent.parent

# Routes de l'application : (URL, méthodes HTTP).
EXPECTED_ROUTES = {
    ("/", "GET"), ("/orders", "GET"), ("/orders/<int:order_id>", "GET"), ("/process", "GET"),
    ("/analytics", "GET"), ("/clients", "GET"), ("/clients/<int:client_id>", "GET"), ("/alerts", "GET"),
    ("/whatsapp", "GET"), ("/sage", "GET"), ("/backups", "GET"), ("/healthz", "GET"),
    ("/api/process-emails", "POST"), ("/api/orders/<int:order_id>/validate", "POST"),
    ("/api/orders/<int:order_id>/reject", "POST"), ("/api/orders/<int:order_id>/update", "POST"),
    ("/api/stats", "GET"), ("/api/orders", "GET"), ("/api/article-code/generate", "POST"),
    ("/api/article-code/suggest", "POST"), ("/api/orders/<int:order_id>/generate-code", "POST"),
    ("/api/orders/generate-all-codes", "POST"), ("/api/orders/sage-stats", "GET"),
    ("/api/orders/<int:order_id>/sage-fields", "PUT"), ("/api/analytics", "GET"), ("/api/alerts", "GET"),
    ("/api/client/<int:client_id>/history", "GET"), ("/api/notifications/check", "GET"),
    ("/api/whatsapp/status", "GET"), ("/webhook/whatsapp", "POST"),
    ("/api/backup/create", "POST"), ("/api/backup/list", "GET"), ("/api/backup/download/<filename>", "GET"),
    ("/api/backup/download-latest", "GET"), ("/api/backup/restore/<filename>", "POST"),
    ("/api/backup/delete/<filename>", "DELETE"), ("/api/backup/export-json", "GET"),
    ("/api/backup/scheduler/status", "GET"), ("/api/backup/scheduler/configure", "POST"),
    ("/export/excel", "GET"), ("/export/excel/sage", "GET"), ("/export/csv", "GET"), ("/export/pdf", "GET"),
    ("/login", "GET"), ("/login", "POST"), ("/logout", "POST"),
}


class TestFactory:
    """Construction de l'application."""

    def test_create_app_returns_flask_app(self, flask_app, test_settings):
        assert isinstance(flask_app, Flask)
        assert flask_app.config["TESTING"] is True
        assert flask_app.config["SECRET_KEY"] == test_settings.secret_key

    def test_services_are_registered(self, flask_app, test_settings):
        services = flask_app.extensions["tecpap"]
        assert isinstance(services, Services)
        assert services.settings is test_settings
        with flask_app.app_context():
            assert get_services() is services

    def test_database_is_created_in_test_directory(self, flask_app, test_settings):
        assert Path(test_settings.database_path).exists()
        assert flask_app.extensions["tecpap"].db.db_file == test_settings.database_path

    def test_every_blueprint_registered(self, flask_app):
        assert set(flask_app.blueprints) == {"auth", "pages", "api", "webhooks", "backups", "exports"}

    def test_routes_and_methods(self, flask_app):
        routes = {
            (rule.rule, method)
            for rule in flask_app.url_map.iter_rules() if rule.endpoint != "static"
            for method in rule.methods - {"HEAD", "OPTIONS"}
        }
        assert routes == EXPECTED_ROUTES

    def test_two_apps_are_independent(self, tmp_path):
        first = create_app(Settings.for_tests(tmp_path / "a"))
        second = create_app(Settings.for_tests(tmp_path / "b"))
        try:
            first.extensions["tecpap"].db.create_order({"entreprise_cliente": "A", "email_id": "1"})
            assert first.test_client().get("/api/stats").get_json()["total_orders"] == 1
            assert second.test_client().get("/api/stats").get_json()["total_orders"] == 0
        finally:
            first.extensions["tecpap"].close()
            second.extensions["tecpap"].close()

    def test_proxy_headers_ignored_by_default(self, flask_app):
        assert not isinstance(flask_app.wsgi_app, ProxyFix)

    def test_proxy_fix_when_trusted(self, tmp_path):
        app = create_app(Settings.for_tests(tmp_path, trust_proxy_headers=True))
        try:
            assert isinstance(app.wsgi_app, ProxyFix)
            response = app.test_client().get("/healthz", headers={"X-Forwarded-For": "203.0.113.9"})
            assert response.status_code == 200
        finally:
            app.extensions["tecpap"].close()

    def test_scheduler_not_started_in_tests(self, flask_app):
        assert flask_app.extensions["tecpap"].backup_scheduler.running is False

    def test_scheduler_started_outside_tests(self, tmp_path):
        settings = Settings.for_tests(tmp_path, env="development", backup_scheduler_enabled=True)
        with patch.object(BackupScheduler, "start") as start:
            app = create_app(settings)
        start.assert_called_once()
        app.extensions["tecpap"].db.disconnect()

    def test_scheduler_disabled_by_settings(self, tmp_path):
        settings = Settings.for_tests(tmp_path, env="development", backup_scheduler_enabled=False)
        with patch.object(BackupScheduler, "start") as start:
            app = create_app(settings)
        start.assert_not_called()
        app.extensions["tecpap"].db.disconnect()

    def test_no_database_in_repository_root(self, flask_client):
        flask_client.get("/")
        assert not (ROOT / "orders.db").exists() or (ROOT / "orders.db").stat().st_mtime < _SESSION_START


_SESSION_START = datetime.now().timestamp()


class TestHealth:
    """Sonde de disponibilité."""

    def test_healthz(self, flask_client):
        response = flask_client.get("/healthz")
        assert response.status_code == 200
        assert response.get_json() == {"status": "ok", "version": __version__}


class TestExports:
    """Les exports renvoient des fichiers téléchargeables."""

    @pytest.fixture
    def with_orders(self, services, sample_order_data, sample_whatsapp_order):
        services.db.create_order(sample_order_data)
        services.db.create_order(sample_whatsapp_order)

    @pytest.mark.parametrize("url, filename, magic", [
        ("/export/excel", "commandes.xlsx", b"PK"),
        ("/export/excel/sage", "commandes_sage_x3.xlsx", b"PK"),
        ("/export/csv", "commandes.csv", b"\xef\xbb\xbf"),
        ("/export/pdf", "rapport_commandes.pdf", b"%PDF"),
    ])
    def test_export_returns_file(self, flask_client, with_orders, url, filename, magic):
        response = flask_client.get(url)
        assert response.status_code == 200
        assert filename in response.headers["Content-Disposition"]
        assert response.data.startswith(magic)

    def test_exports_are_written_in_export_dir(self, flask_client, test_settings, with_orders):
        flask_client.get("/export/csv")
        assert (Path(test_settings.export_dir) / "commandes.csv").exists()

    def test_csv_status_filter(self, flask_client, services, with_orders):
        order_id = services.db.get_all_orders()[0]["id"]
        services.db.update_order_status(order_id, "validee")
        content = flask_client.get("/export/csv?status=validee").data.decode("utf-8-sig")
        assert len(content.strip().splitlines()) == 2  # en-tête + une commande


class TestBackupRoutes:
    """Routes des sauvegardes sur les dossiers de la configuration de test."""

    def test_create_and_download_backup(self, flask_client, test_settings):
        data = flask_client.post("/api/backup/create", json={}).get_json()
        assert data["success"] is True
        assert Path(data["path"]).parent == Path(test_settings.backup_dir)

        response = flask_client.get(f"/api/backup/download/{data['filename']}")
        assert response.status_code == 200

    def test_restore_reconnects_database(self, flask_client, services, sample_order_data):
        filename = flask_client.post("/api/backup/create", json={"compress": True}).get_json()["filename"]
        services.db.create_order(sample_order_data)

        data = flask_client.post(f"/api/backup/restore/{filename}").get_json()

        assert data["success"] is True
        assert flask_client.get("/api/stats").get_json()["total_orders"] == 0

    def test_invalid_filename(self, flask_client):
        assert flask_client.delete("/api/backup/delete/..%5Corders.db").status_code == 400

    def test_scheduler_configuration(self, flask_client):
        data = flask_client.post("/api/backup/scheduler/configure",
                                 json={"interval_hours": 12, "keep_backups": 5}).get_json()
        assert data["status"]["interval_hours"] == 12
        assert data["status"]["keep_backups"] == 5

    def test_scheduler_configuration_rejects_zero(self, flask_client):
        response = flask_client.post("/api/backup/scheduler/configure", json={"interval_hours": 0})
        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        assert data["error"] == "L'intervalle doit être compris entre 1 et 24 heures"

    @pytest.mark.parametrize("payload, message", [
        ({"interval_hours": "six"}, "L'intervalle doit être un nombre entier d'heures"),
        ({"interval_hours": 2.5}, "L'intervalle doit être un nombre entier d'heures"),
        ({"keep_backups": [3]}, "Le nombre de sauvegardes conservées doit être un nombre entier"),
        ({"keep_backups": 0}, "Le nombre de sauvegardes conservées doit être au moins 1"),
    ])
    def test_scheduler_configuration_messages(self, flask_client, payload, message):
        response = flask_client.post("/api/backup/scheduler/configure", json=payload)
        assert response.status_code == 400
        assert response.get_json()["error"] == message

    def test_export_json(self, flask_client):
        response = flask_client.get("/api/backup/export-json")
        assert response.status_code == 200
        assert b'"tables"' in response.data


class TestWhatsAppPage:
    """Adresse du webhook affichée sur la page WhatsApp."""

    def test_hint_when_public_url_missing(self, flask_client):
        html = flask_client.get("/whatsapp").get_data(as_text=True)
        assert "PUBLIC_BASE_URL" in html
        assert "ngrok-free.dev" not in html

    def test_webhook_url_from_settings(self, tmp_path):
        app = create_app(Settings.for_tests(tmp_path, public_base_url="https://tecpap.example.org/"))
        try:
            html = app.test_client().get("/whatsapp").get_data(as_text=True)
        finally:
            app.extensions["tecpap"].close()
        assert "https://tecpap.example.org/webhook/whatsapp" in html


class TestWhatsAppWebhook:
    """Webhook Twilio avec un extracteur simulé."""

    def _post(self, client, body="bghit 2000 sachet"):
        return client.post("/webhook/whatsapp", data={"From": "whatsapp:+212612345678", "Body": body, "NumMedia": "0"})

    def test_order_is_saved_and_acknowledged(self, flask_client, services):
        order = {"est_bon_commande": True, "type_produit": "Sachets fond plat", "quantite": 2000, "unite": "pièces"}
        with patch("tecpap.web.routes.webhooks.DataExtractor") as extractor_class:
            extractor_class.return_value.extract_from_email.return_value = order
            response = self._post(flask_client)

        assert response.status_code == 200
        assert response.content_type.startswith("application/xml")
        assert "Commande reçue" in response.get_data(as_text=True)
        saved = services.db.get_all_orders()[0]
        assert saved["source"] == "whatsapp"
        assert saved["whatsapp_from"] == "whatsapp:+212612345678"
        assert saved["client_nom"] == "Client WhatsApp +212612345678"

    def test_message_without_order(self, flask_client, services):
        with patch("tecpap.web.routes.webhooks.DataExtractor") as extractor_class:
            extractor_class.return_value.extract_from_email.return_value = {"est_bon_commande": False}
            response = self._post(flask_client, "salam")
        assert "Si vous souhaitez passer une commande" in response.get_data(as_text=True)
        assert services.db.get_all_orders() == []

    def test_without_openai_key_no_order_is_created(self, flask_client, services):
        response = self._post(flask_client)
        assert response.status_code == 200
        assert services.db.get_all_orders() == []


class TestScheduler:
    """Planificateur de sauvegardes."""

    @pytest.mark.parametrize("now, expected", [
        (datetime(2025, 3, 10, 1, 30), datetime(2025, 3, 10, 6, 0)),
        (datetime(2025, 3, 10, 12, 0), datetime(2025, 3, 10, 18, 0)),
        (datetime(2025, 1, 31, 19, 45), datetime(2025, 2, 1, 0, 0)),
        (datetime(2024, 12, 31, 23, 59), datetime(2025, 1, 1, 0, 0)),
    ])
    def test_next_backup_time(self, now, expected):
        assert next_backup_time(now, 6) == expected

    def test_invalid_interval(self):
        with pytest.raises(ValueError):
            next_backup_time(datetime(2025, 1, 1), 0)

    def test_reloader_parent_does_not_start(self, monkeypatch):
        monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
        assert should_start_in_this_process(debug=False) is True
        assert should_start_in_this_process(debug=True) is False
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
        assert should_start_in_this_process(debug=True) is True

    def test_single_scheduler_per_database(self, tmp_path):
        first = BackupScheduler(str(tmp_path / "orders.db"), str(tmp_path / "backups"))
        second = BackupScheduler(str(tmp_path / "orders.db"), str(tmp_path / "backups"))
        try:
            assert first.start() is True
            assert second.start() is False
            assert first.start() is False
        finally:
            first.stop()
            second.stop()
        assert first.running is False

    def test_run_backup(self, tmp_path, temp_db):
        scheduler = BackupScheduler(temp_db.db_file, str(tmp_path / "backups"), keep_backups=1)
        assert scheduler.run_backup() is not None
        assert scheduler.get_status()["last_backup"] is not None


class TestEntryPoint:
    """Point d'entrée app.py (utilisé par gunicorn)."""

    def test_app_module_builds_app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_ENV", "test")
        for variable, name in [("DATABASE_PATH", "orders.db"), ("BACKUP_DIR", "backups"), ("EXPORT_DIR", "exports"),
                               ("ATTACHMENTS_DIR", "attachments"), ("WHATSAPP_MEDIA_DIR", "media")]:
            monkeypatch.setenv(variable, str(tmp_path / name))
        sys.modules.pop("app", None)
        try:
            module = importlib.import_module("app")
            assert isinstance(module.app, Flask)
            assert module.app.extensions["tecpap"].db.db_file == str(tmp_path / "orders.db")
            module.app.extensions["tecpap"].close()
        finally:
            sys.modules.pop("app", None)
