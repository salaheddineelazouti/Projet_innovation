"""Tests des pages et de l'API de l'application web (client de test Flask, base temporaire)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest

from tecpap.db.database import ORDER_UPDATABLE_FIELDS
from tecpap.web.errors import GENERIC_ERROR_MESSAGE
from tecpap.web.routes.backups import resolve_backup_path

# Texte d'une exception simulée : il ne doit jamais apparaître dans une réponse.
SECRET_ERROR_TEXT = "C:/donnees/orders.db verrouillee par le processus 4242"


@pytest.fixture
def client(flask_client):
    return flask_client


class TestAppConfiguration:
    """Configuration de l'application."""

    def test_app_imports(self):
        """La fabrique d'application s'importe."""
        from tecpap.web import create_app

        assert callable(create_app)

    def test_app_has_routes(self, flask_app):
        """Les routes principales sont déclarées."""
        rules = [rule.rule for rule in flask_app.url_map.iter_rules()]
        assert "/" in rules
        assert "/orders" in rules


class TestHomeRoute:
    """Page d'accueil."""

    def test_home_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_home_returns_html(self, client):
        response = client.get("/")
        assert "text/html" in response.content_type


class TestOrdersRoutes:
    """Pages et API des commandes."""

    def test_orders_page_returns_200(self, client):
        response = client.get("/orders")
        assert response.status_code == 200

    def test_orders_page_returns_html(self, client):
        response = client.get("/orders")
        assert "text/html" in response.content_type

    def test_orders_api_returns_json(self, client):
        response = client.get("/api/orders")
        assert response.status_code == 200
        assert "application/json" in response.content_type

    def test_orders_page_status_filter(self, client, services, sample_order_data):
        services.db.create_order(sample_order_data)
        response = client.get("/orders?status=en_attente")
        assert response.status_code == 200


class TestClientsPage:
    """Page des clients."""

    def test_clients_page_returns_200(self, client):
        response = client.get("/clients")
        assert response.status_code == 200

    def test_clients_page_returns_html(self, client):
        response = client.get("/clients")
        assert "text/html" in response.content_type


class TestAnalyticsRoutes:
    """Statistiques."""

    def test_analytics_page_returns_200(self, client):
        response = client.get("/analytics")
        assert response.status_code == 200

    def test_analytics_api_returns_json(self, client):
        response = client.get("/api/analytics")
        assert response.status_code == 200
        assert "application/json" in response.content_type


class TestStatsAPI:
    """API des statistiques."""

    def test_stats_api_returns_200(self, client):
        response = client.get("/api/stats")
        assert response.status_code == 200

    def test_stats_api_returns_json(self, client):
        response = client.get("/api/stats")
        assert "application/json" in response.content_type

    def test_stats_counts_orders(self, client, services, sample_order_data):
        services.db.create_order(sample_order_data)
        stats = client.get("/api/stats").get_json()
        assert stats["total_orders"] == 1
        assert stats["pending_orders"] == 1


class TestProcessRoutes:
    """Traitement des emails."""

    def test_process_page_returns_200(self, client):
        response = client.get("/process")
        assert response.status_code == 200


class TestAlertsRoutes:
    """Alertes."""

    def test_alerts_page_returns_200(self, client):
        response = client.get("/alerts")
        assert response.status_code == 200

    def test_alerts_api_returns_json(self, client):
        response = client.get("/api/alerts")
        assert response.status_code == 200
        assert "application/json" in response.content_type


class TestNotificationsAPI:
    """Notifications des nouvelles commandes."""

    def test_notifications_check_returns_json(self, client):
        response = client.get("/api/notifications/check")
        assert response.status_code == 200
        assert "application/json" in response.content_type

    def test_notifications_return_new_orders(self, client, services, sample_order_data):
        first_id = services.db.create_order(sample_order_data)
        second = dict(sample_order_data, email_id="test_email_002")
        services.db.create_order(second)

        data = client.get(f"/api/notifications/check?last_id={first_id}").get_json()

        assert len(data["new_orders"]) == 1
        assert data["last_id"] == first_id + 1


class TestBackupsRoute:
    """Page des sauvegardes."""

    def test_backups_page_returns_200(self, client):
        response = client.get("/backups")
        assert response.status_code == 200


class TestWhatsAppRoute:
    """Page WhatsApp."""

    def test_whatsapp_page_returns_200(self, client):
        response = client.get("/whatsapp")
        assert response.status_code == 200


class TestExportRoutes:
    """Exports de fichiers."""

    def test_export_csv_route_exists(self, client):
        response = client.get("/export/csv")
        assert response.status_code == 200
        assert "attachment" in response.headers["Content-Disposition"]

    def test_export_excel_route_exists(self, client):
        response = client.get("/export/excel")
        assert response.status_code == 200
        assert response.data[:2] == b"PK"  # un classeur .xlsx est une archive ZIP


class TestValidationAPI:
    """Validation et rejet des commandes."""

    @pytest.mark.parametrize("action, body", [("validate", {}), ("reject", {"reason": "test"})])
    def test_unknown_order_gives_404(self, client, services, action, body):
        response = client.post(f"/api/orders/9999/{action}", json=body)
        assert response.status_code == 404
        assert response.get_json() == {"success": False, "error": "Commande introuvable"}
        assert services.db.get_all_orders() == []

    @pytest.mark.parametrize("action, body, message", [
        ("validate", {}, "Commande validée"),
        ("reject", {"reason": "test"}, "Commande rejetée"),
    ])
    def test_existing_order_gives_200(self, client, services, sample_order_data, action, body, message):
        order_id = services.db.create_order(sample_order_data)
        response = client.post(f"/api/orders/{order_id}/{action}", json=body)
        assert response.status_code == 200
        assert response.get_json()["message"] == message

    def test_validate_order_updates_status(self, client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)

        data = client.post(f"/api/orders/{order_id}/validate", json={"validated_by": "Équipe"}).get_json()

        assert data["success"] is True
        assert data["email_sent"] is False  # aucun identifiant SMTP dans la configuration de test
        order = services.db.get_order(order_id)
        assert order["statut"] == "validee"
        assert order["validated_by"] == "Équipe"

    def test_reject_order_saves_reason(self, client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)

        client.post(f"/api/orders/{order_id}/reject", json={"reason": "Stock insuffisant"})

        order = services.db.get_order(order_id)
        assert order["statut"] == "rejetee"
        assert order["motif_rejet"] == "Stock insuffisant"

    def test_validate_whatsapp_order_sends_whatsapp(self, client, services, sample_whatsapp_order, monkeypatch):
        order_id = services.db.create_order(sample_whatsapp_order)
        sent = []
        monkeypatch.setattr(services.whatsapp, "send_reply", lambda number, message: sent.append(number) or "SM1")

        data = client.post(f"/api/orders/{order_id}/validate", json={}).get_json()

        assert data["whatsapp_sent"] is True
        assert sent == ["+212612345678"]

    def test_update_order_recomputes_remaining_quantity(self, client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)

        response = client.post(f"/api/orders/{order_id}/update", json={"quantite_livree": 2000})

        assert response.status_code == 200
        assert services.db.get_order(order_id)["reste_a_livrer"] == 3000

    def test_update_order_without_body_is_rejected(self, client):
        response = client.post("/api/orders/1/update", json={})
        assert response.status_code == 400


class TestArticleCodeAPI:
    """Codes articles."""

    def test_generate_article_code(self, client):
        data = client.post("/api/article-code/generate", json={
            "paper_type": "kraft blanchi", "grammage": 100, "laize": 28, "supplier": "mondi",
        }).get_json()
        assert data["code"] == "KB100L28MON"

    def test_suggest_article_code(self, client):
        data = client.post("/api/article-code/suggest", json={"description": "kraft blanchi 80g laize 25"}).get_json()
        assert data["code"] == "KB80L25"

    def test_generate_code_for_order(self, client, services, sample_order_data):
        order_id = services.db.create_order(dict(sample_order_data, nature_produit="sachet kraft blanchi 70g laize 30"))
        data = client.post(f"/api/orders/{order_id}/generate-code").get_json()
        assert data["success"] is True
        assert services.db.get_order(order_id)["code_article"] == data["code"]

    def test_generate_code_unknown_order(self, client):
        response = client.post("/api/orders/9999/generate-code")
        assert response.status_code == 404

    def test_sage_stats(self, client):
        data = client.get("/api/orders/sage-stats").get_json()
        assert data["success"] is True
        assert data["stats"]["total_orders"] == 0

    def test_sage_fields_rejects_other_columns(self, client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        response = client.put(f"/api/orders/{order_id}/sage-fields", json={"grammage": 80, "statut": "validee"})
        assert response.status_code == 400
        data = response.get_json()
        assert data["rejected_fields"] == ["statut"]
        assert "statut" in data["error"]
        order = services.db.get_order(order_id)
        assert order["statut"] == "en_attente"
        assert order["grammage"] is None

    def test_sage_fields_update(self, client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        data = client.put(f"/api/orders/{order_id}/sage-fields", json={"grammage": "80", "quantite_livree": 1000}).get_json()
        assert data["success"] is True
        assert set(data["updated_fields"]) == {"grammage", "quantite_livree", "reste_a_livrer"}
        order = services.db.get_order(order_id)
        assert (order["grammage"], order["reste_a_livrer"]) == (80, 4000)

    def test_sage_fields_invalid_number(self, client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        response = client.put(f"/api/orders/{order_id}/sage-fields", json={"laize": "vingt-huit"})
        assert response.status_code == 400
        assert response.get_json()["rejected_fields"] == ["laize"]

    def test_sage_fields_unknown_order(self, client):
        assert client.put("/api/orders/9999/sage-fields", json={"grammage": 80}).status_code == 404


class TestOrderUpdateWhitelist:
    """Seules les colonnes métier et SAGE X3 d'une commande sont modifiables par l'API."""

    @pytest.fixture
    def order_id(self, services, sample_order_data):
        return services.db.create_order(sample_order_data)

    @pytest.mark.parametrize("field", ["statut", "client_id", "id", "created_at", "validated_by", "email_from",
                                       "confiance", "source"])
    def test_non_editable_column_rejected(self, client, services, order_id, field):
        before = services.db.get_order(order_id)
        response = client.post(f"/api/orders/{order_id}/update", json={field: "x", "quantite": 1})
        assert response.status_code == 400
        assert response.get_json()["rejected_fields"] == [field]
        assert services.db.get_order(order_id) == before

    @pytest.mark.parametrize("key", [
        "statut = 'validee', quantite", "quantite = 0 --", "quantite; DROP TABLE commandes", "1=1) OR (quantite",
    ])
    def test_sql_looking_keys_rejected(self, client, services, order_id, key):
        response = client.post(f"/api/orders/{order_id}/update", json={key: 1})
        assert response.status_code == 400
        assert response.get_json()["rejected_fields"] == [key]
        order = services.db.get_order(order_id)
        assert (order["statut"], order["quantite"]) == ("en_attente", 5000)
        assert services.db.connection.execute("SELECT COUNT(*) FROM commandes").fetchone()[0] == 1

    @pytest.mark.parametrize("payload, field", [
        ({"quantite": "beaucoup"}, "quantite"), ({"grammage": "80.5"}, "grammage"), ({"laize": True}, "laize"),
        ({"prix_unitaire": [1]}, "prix_unitaire"), ({"commercial": {"nom": "x"}}, "commercial"),
        ({"quantite": "nan"}, "quantite"), ({"produit_id": "deux"}, "produit_id"),
    ])
    def test_invalid_values_rejected(self, client, services, order_id, payload, field):
        response = client.post(f"/api/orders/{order_id}/update", json=payload)
        assert response.status_code == 400
        assert response.get_json()["rejected_fields"] == [field]
        assert services.db.get_order(order_id)["quantite"] == 5000

    def test_form_values_are_converted(self, client, services, order_id):
        """Le formulaire de la fiche commande envoie des chaînes ; une chaîne vide efface la valeur."""
        response = client.post(f"/api/orders/{order_id}/update", json={
            "quantite": "6000", "grammage": "80", "laize": "28", "prix_unitaire": "0.45", "prix_total": "",
            "produit_id": "2", "code_article": "KB80L28", "date_livraison": "2025-02-01",
        })
        assert response.status_code == 200
        order = services.db.get_order(order_id)
        assert (order["quantite"], order["grammage"], order["laize"]) == (6000, 80, 28)
        assert order["prix_unitaire"] == 0.45
        assert order["prix_total"] is None
        assert order["produit_id"] == 2
        assert order["reste_a_livrer"] == 6000
        assert order["code_article"] == "KB80L28"

    def test_unknown_order(self, client):
        assert client.post("/api/orders/9999/update", json={"quantite": 10}).status_code == 404

    def test_order_detail_form_sends_only_editable_fields(self, client, services, order_id):
        html = client.get(f"/orders/{order_id}").get_data(as_text=True)
        form = re.search(r'<form id="editForm".*?</form>', html, flags=re.DOTALL).group(0)
        names = set(re.findall(r'name="([^"]+)"', form))
        assert names
        assert names <= set(ORDER_UPDATABLE_FIELDS)

    def test_order_detail_form_round_trip(self, client, services, order_id):
        """Les valeurs du formulaire, telles que le navigateur les envoie, sont acceptées."""
        html = client.get(f"/orders/{order_id}").get_data(as_text=True)
        form = re.search(r'<form id="editForm".*?</form>', html, flags=re.DOTALL).group(0)
        values = {name: value for name, value in re.findall(r'<input[^>]*name="([^"]+)" value="([^"]*)"', form)}
        values.update({name: "" for name in re.findall(r'<select name="([^"]+)"', form)})
        response = client.post(f"/api/orders/{order_id}/update", json=values)
        assert response.status_code == 200, response.get_json()


class TestProcessEmailsAPI:
    """Traitement des emails depuis le tableau de bord."""

    def test_process_emails_endpoint_exists(self, client):
        """Sans identifiants Gmail, aucun email n'est lu et aucune commande n'est créée."""
        response = client.post("/api/process-emails")
        assert response.status_code == 200
        assert response.get_json()["orders_count"] == 0


class TestStaticRoutes:
    """Fichiers statiques."""

    def test_static_route_exists(self, client):
        response = client.get("/static/images/tecpap-logo.png")
        assert response.status_code == 200
        assert response.content_type == "image/png"


class TestErrorHandling:
    """Cas d'erreur."""

    def test_404_for_nonexistent_route(self, client):
        response = client.get("/this-route-does-not-exist")
        assert response.status_code == 404

    def test_order_detail_nonexistent(self, client):
        response = client.get("/orders/999999")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/orders")

    def test_client_detail_nonexistent(self, client):
        response = client.get("/clients/999999")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/clients")


class TestAppImports:
    """Application et services."""

    def test_flask_app_exists(self, flask_app):
        assert flask_app is not None

    def test_database_manager_exists(self, services):
        assert services.db is not None
        assert services.db.connection is not None

    def test_templates_configured(self, flask_app):
        template_dir = Path(flask_app.root_path) / flask_app.template_folder
        assert (template_dir / "base.html").exists()

    def test_client_detail_without_openai_key(self, client, services, sample_order_data):
        """La fiche client s'affiche sans clé OpenAI (prévisions calculées par des règles fixes)."""
        order_id = services.db.create_order(sample_order_data)
        client_id = services.db.get_order(order_id)["client_id"]
        response = client.get(f"/clients/{client_id}")
        assert response.status_code == 200


class TestErrorMessages:
    """Le texte d'une exception n'est jamais renvoyé : message générique et trace dans le journal."""

    @staticmethod
    def _fail(*args, **kwargs):
        raise RuntimeError(SECRET_ERROR_TEXT)

    def _assert_generic(self, response, caplog, status=500):
        assert response.status_code == status
        body = response.get_data(as_text=True)
        assert SECRET_ERROR_TEXT not in body
        assert "RuntimeError" not in body
        assert SECRET_ERROR_TEXT in caplog.text

    def test_process_emails(self, client, monkeypatch, caplog):
        monkeypatch.setattr("tecpap.web.routes.api.OrderProcessor", self._fail)
        with caplog.at_level(logging.ERROR):
            response = client.post("/api/process-emails")
        self._assert_generic(response, caplog)
        assert response.get_json()["success"] is False

    @pytest.mark.parametrize("url", ["/api/article-code/generate", "/api/article-code/suggest"])
    def test_article_codes(self, client, monkeypatch, caplog, url):
        monkeypatch.setattr("tecpap.web.routes.api.generate_article_code", self._fail)
        monkeypatch.setattr("tecpap.web.routes.api.suggest_article_code_from_description", self._fail)
        with caplog.at_level(logging.ERROR):
            response = client.post(url, json={"description": "kraft", "paper_type": "kraft"})
        self._assert_generic(response, caplog)
        assert response.get_json()["error"] == GENERIC_ERROR_MESSAGE

    def test_generate_all_codes(self, client, services, monkeypatch, caplog, sample_order_data):
        services.db.create_order(sample_order_data)
        monkeypatch.setattr("tecpap.web.routes.api._code_for_order", self._fail)
        with caplog.at_level(logging.ERROR):
            response = client.post("/api/orders/generate-all-codes")
        self._assert_generic(response, caplog)

    def test_update_order(self, client, services, monkeypatch, caplog, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        monkeypatch.setattr(services.db, "update_order", self._fail)
        with caplog.at_level(logging.ERROR):
            response = client.post(f"/api/orders/{order_id}/update", json={"quantite": 10})
        self._assert_generic(response, caplog)

    def test_notifications(self, client, services, monkeypatch, caplog):
        broken_connection = type("ConnexionEnPanne", (), {"cursor": self._fail})()
        monkeypatch.setattr(services.db, "connection", broken_connection)
        with caplog.at_level(logging.ERROR):
            response = client.get("/api/notifications/check?last_id=3")
        self._assert_generic(response, caplog, status=200)

    def test_whatsapp_status(self, client, services, monkeypatch, caplog):
        monkeypatch.setattr(services.whatsapp, "connect", self._fail)
        with caplog.at_level(logging.ERROR):
            response = client.get("/api/whatsapp/status")
        self._assert_generic(response, caplog, status=200)
        assert response.get_json()["connected"] is False

    def test_backup_list(self, client, monkeypatch, caplog):
        monkeypatch.setattr("tecpap.web.routes.backups.list_backups", self._fail)
        with caplog.at_level(logging.ERROR):
            response = client.get("/api/backup/list")
        self._assert_generic(response, caplog)

    def test_uncaught_exception_in_page(self, flask_app, client, monkeypatch):
        # En mode test, Flask transmet les exceptions au client de test au lieu de répondre 500.
        flask_app.config["PROPAGATE_EXCEPTIONS"] = False
        monkeypatch.setattr("tecpap.web.routes.pages.render_template", self._fail)
        response = client.get("/process")
        assert response.status_code == 500
        assert SECRET_ERROR_TEXT not in response.get_data(as_text=True)
        assert "Erreur interne" in response.get_data(as_text=True)

    def test_unknown_api_route_returns_json(self, client):
        response = client.get("/api/route-inexistante")
        assert response.status_code == 404
        assert response.get_json()["success"] is False

    def test_wrong_method_keeps_allow_header(self, client):
        response = client.get("/api/process-emails")
        assert response.status_code == 405
        assert "POST" in response.headers["Allow"]

    def test_no_exception_text_in_route_code(self):
        """Aucune route ne renvoie ``str(exc)`` hormis les messages de validation."""
        routes = Path(__file__).resolve().parent.parent / "tecpap" / "web" / "routes"
        for module in routes.glob("*.py"):
            for line in module.read_text(encoding="utf-8").splitlines():
                if "str(exc)" in line:
                    assert "400" in line, f"{module.name} : {line.strip()}"


class TestBackupFilenames:
    """Noms de fichiers reçus par les routes des sauvegardes."""

    @pytest.fixture
    def backup_file(self, test_settings):
        os.makedirs(test_settings.backup_dir, exist_ok=True)
        path = Path(test_settings.backup_dir) / "backup_20250101_000000.db"
        path.write_bytes(b"SQLite format 3\x00")
        return path

    @pytest.mark.parametrize("name", [
        "..%5Corders.db", "%2E%2E", ".", "back%20up.db", "sauvegarde%C3%A9.db", "backup.db%00", "a%3Bb.db",
        "..backup.db",
    ])
    @pytest.mark.parametrize("method, prefix", [
        ("get", "/api/backup/download/"), ("post", "/api/backup/restore/"), ("delete", "/api/backup/delete/"),
    ])
    def test_invalid_names_rejected(self, client, method, prefix, name):
        response = getattr(client, method)(prefix + name)
        assert response.status_code == 400
        assert response.get_json()["error"] == "Nom de fichier de sauvegarde invalide"

    @pytest.mark.parametrize("method, prefix", [
        ("get", "/api/backup/download/"), ("post", "/api/backup/restore/"), ("delete", "/api/backup/delete/"),
    ])
    def test_missing_backup(self, client, method, prefix):
        response = getattr(client, method)(prefix + "backup_19990101_000000.db")
        assert response.status_code == 404

    def test_download_existing_backup(self, client, backup_file):
        response = client.get(f"/api/backup/download/{backup_file.name}")
        assert response.status_code == 200
        assert response.data.startswith(b"SQLite")

    def test_delete_existing_backup(self, client, backup_file):
        assert client.delete(f"/api/backup/delete/{backup_file.name}").status_code == 200
        assert not backup_file.exists()

    def test_database_outside_backup_dir_is_unreachable(self, client, test_settings):
        """La base elle-même (dossier parent) ne peut être ni téléchargée ni supprimée."""
        db_name = Path(test_settings.database_path).name
        assert client.get(f"/api/backup/download/..%2F{db_name}").status_code in (400, 404)
        assert client.delete(f"/api/backup/delete/..%5C{db_name}").status_code == 400
        assert Path(test_settings.database_path).exists()

    def test_resolve_backup_path(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        resolved = resolve_backup_path("backup_1.db.gz", str(backup_dir))
        assert Path(resolved) == (backup_dir / "backup_1.db.gz").resolve()
        for name in ("..", ".", "../orders.db", "..\\orders.db", "a/b.db", "", "nom avec espace.db"):
            assert resolve_backup_path(name, str(backup_dir)) is None

    def test_symlink_leaving_backup_dir_is_refused(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        outside = tmp_path / "secret.db"
        outside.write_text("secret", encoding="utf-8")
        link = backup_dir / "backup_lien.db"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            pytest.skip("création de lien symbolique impossible sur ce poste")
        assert resolve_backup_path("backup_lien.db", str(backup_dir)) is None
