"""Tests des pages et de l'API de l'application web (client de test Flask, base temporaire)."""

from __future__ import annotations

from pathlib import Path

import pytest


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

    def test_validate_order_endpoint_exists(self, client):
        response = client.post("/api/orders/9999/validate", json={})
        assert response.status_code == 200

    def test_reject_order_endpoint_exists(self, client):
        response = client.post("/api/orders/9999/reject", json={"reason": "test"})
        assert response.status_code == 200

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

    def test_sage_fields_only_accepts_sage_columns(self, client, services, sample_order_data):
        order_id = services.db.create_order(sample_order_data)
        data = client.put(f"/api/orders/{order_id}/sage-fields", json={"grammage": 80, "statut": "validee"}).get_json()
        assert data["updated_fields"] == ["grammage"]
        assert services.db.get_order(order_id)["statut"] == "en_attente"


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
