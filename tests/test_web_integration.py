"""Tests de l'application web assemblée : services partagés, webhook, tableau de bord sécurisé.

- Un seul client des modèles de langue est construit par application et partagé par le
  webhook, le traitement des emails, la conversion des médias et les prévisions.
- Avec la connexion et la protection CSRF activées, chaque page du tableau de bord s'affiche
  pour un utilisateur connecté, et les actions de chaque type de page (valider, rejeter,
  modifier une commande, créer une sauvegarde…) aboutissent avec le jeton CSRF lu dans la page,
  comme le fait le script de ``base.html`` dans le navigateur.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import pytest

from tecpap.config import Settings, is_reloader_child_process
from tecpap.extraction.extractor import RULES_FALLBACK_ANOMALY
from tecpap.processing.order_processor import OrderProcessor
from tecpap.web import create_app
from tecpap.web.errors import GENERIC_ERROR_MESSAGE
from tecpap.web.scheduler import BackupScheduler, SchedulerConfigError
from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_PASSWORD_HASH, TEST_ADMIN_USERNAME
from tests.fakes import FakeLLMClient

TEMPLATES = Path(__file__).resolve().parent.parent / "tecpap" / "web" / "templates"

WHATSAPP_MESSAGE = {"From": "whatsapp:+212612345678", "Body": "salam, bghit 2000 sachet fond plat", "NumMedia": "0",
                    "MessageSid": "SM0123456789abcdef0123456789abcdef"}

CSRF_META = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def _csrf_from_page(html: str) -> str:
    """Jeton CSRF écrit dans la balise ``<meta name="csrf-token">`` de la page."""
    match = CSRF_META.search(html)
    assert match, "balise meta csrf-token absente"
    return match.group(1)


class _FormReader(HTMLParser):
    """Lit les champs d'un formulaire HTML comme ``new FormData(form)`` dans le navigateur.

    Pour une liste déroulante, la valeur est celle de l'option sélectionnée, sinon de la première.
    """

    def __init__(self, form_id: str):
        super().__init__()
        self.form_id = form_id
        self.inside = False
        self.fields: dict[str, str] = {}
        self._select: str | None = None
        self._select_first: str | None = None
        self._select_chosen: str | None = None
        self._textarea: str | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == self.form_id:
            self.inside = True
        if not self.inside:
            return
        name = attributes.get("name")
        if tag == "input" and name and attributes.get("type") not in ("submit", "button"):
            self.fields[name] = attributes.get("value") or ""
        elif tag == "select" and name:
            self._select, self._select_first, self._select_chosen = name, None, None
        elif tag == "option" and self._select:
            value = attributes.get("value", "")
            if self._select_first is None:
                self._select_first = value
            if "selected" in attributes:
                self._select_chosen = value
        elif tag == "textarea" and name:
            self._textarea = name
            self.fields[name] = ""

    def handle_data(self, data):
        if self._textarea:
            self.fields[self._textarea] += data

    def handle_endtag(self, tag):
        if tag == "form" and self.inside:
            self.inside = False
        elif tag == "select" and self._select:
            chosen = self._select_chosen if self._select_chosen is not None else self._select_first
            self.fields[self._select] = chosen or ""
            self._select = None
        elif tag == "textarea":
            self._textarea = None


def _form_fields(html: str, form_id: str) -> dict[str, str]:
    reader = _FormReader(form_id)
    reader.feed(html)
    return reader.fields


# ---------------------------------------------------------------------- services partagés

@pytest.fixture
def fake_llm():
    return FakeLLMClient()


@pytest.fixture
def llm_app(test_settings, fake_llm):
    """Application de test construite avec un client des modèles de langue simulé."""
    app = create_app(test_settings, llm=fake_llm)
    yield app
    app.extensions["tecpap"].close()


class TestSharedLLMClient:
    """Le client des modèles de langue de l'application est le même partout."""

    def test_services_share_the_injected_client(self, llm_app, fake_llm):
        services = llm_app.extensions["tecpap"]
        assert services.llm is fake_llm
        assert services.media.llm is fake_llm
        assert services.whatsapp.llm is fake_llm
        assert services.whatsapp.media is services.media

    def test_default_client_is_built_from_settings(self, flask_app):
        services = flask_app.extensions["tecpap"]
        assert services.llm.available is False
        assert services.whatsapp.llm is services.llm

    def test_webhook_uses_the_shared_client(self, llm_app, fake_llm):
        services = llm_app.extensions["tecpap"]
        assert services.extractor.llm is fake_llm
        assert services.extractor.router is services.router
        assistant = services.assistant()
        assert assistant.llm is fake_llm
        assert assistant.router is services.router

    def test_process_emails_passes_the_shared_client(self, llm_app, fake_llm, monkeypatch):
        received = {}

        class RecordingProcessor:
            def __init__(self, **kwargs):
                received.update(kwargs)

            def process_new_emails(self, max_emails, save_to_db):
                return []

        monkeypatch.setattr("tecpap.web.routes.api.OrderProcessor", RecordingProcessor)
        response = llm_app.test_client().post("/api/process-emails")
        assert response.status_code == 200
        assert received["llm"] is fake_llm

    @pytest.mark.parametrize("url", ["/clients/{id}", "/api/client/{id}/history"])
    def test_predictions_use_the_shared_client(self, llm_app, fake_llm, sample_order_data, url):
        services = llm_app.extensions["tecpap"]
        services.db.create_order(sample_order_data)
        client_id = services.db.get_all_orders()[0]["client_id"]
        target = "tecpap.web.routes.pages.AIPredictor" if url.startswith("/clients") else \
            "tecpap.web.routes.api.AIPredictor"
        with patch(target) as predictor_class:
            predictor_class.return_value.predict_client_behavior.return_value = None
            response = llm_app.test_client().get(url.format(id=client_id))
        assert response.status_code == 200
        assert predictor_class.call_args.kwargs["llm"] is fake_llm

    def test_order_processor_builds_its_extractor_with_the_client(self, test_settings, fake_llm):
        processor = OrderProcessor(settings=test_settings, llm=fake_llm)
        assert processor.extractor.llm is fake_llm
        assert processor.extractor.db is processor.db


class TestWhatsAppEndToEnd:
    """Message WhatsApp reçu par le webhook sans assistant, extrait par le modèle simulé, enregistré en base."""

    @pytest.fixture
    def one_shot_app(self, tmp_path, fake_llm):
        app = create_app(Settings.for_tests(tmp_path, chatbot_enabled=False), llm=fake_llm)
        yield app
        app.extensions["tecpap"].close()

    def test_order_is_extracted_and_saved(self, one_shot_app, fake_llm):
        order = {"est_bon_commande": True, "entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat",
                 "quantite": 2000, "unite": "pièces", "confiance": 85}
        fake_llm.queue("chat_json", order)

        response = one_shot_app.test_client().post("/webhook/whatsapp", data=WHATSAPP_MESSAGE)

        assert response.status_code == 200
        saved = one_shot_app.extensions["tecpap"].db.get_all_orders()
        assert len(saved) == 1
        assert f"CMD-{saved[0]['id']}" in response.get_data(as_text=True)
        assert saved[0]["client_nom"] == "Snack Atlas"
        assert saved[0]["quantite"] == 2000
        assert saved[0]["statut"] == "en_attente"
        assert saved[0]["source"] == "whatsapp"
        # Message court sans expression de renouvellement : un seul appel, celui de l'extraction.
        (extraction_call,) = fake_llm.calls_to("chat_json")
        # Le message du client est transmis au modèle d'extraction dans un bloc délimité.
        assert "bghit 2000 sachet" in extraction_call["user"]

    def test_message_without_order_creates_nothing(self, one_shot_app, fake_llm):
        fake_llm.queue("chat_json", {"est_bon_commande": False})
        response = one_shot_app.test_client().post("/webhook/whatsapp", data=dict(WHATSAPP_MESSAGE, Body="salam"))
        assert response.status_code == 200
        assert one_shot_app.extensions["tecpap"].db.get_all_orders() == []

    def test_model_failure_falls_back_to_rules(self, one_shot_app):
        # File de réponses vide : chaque appel renvoie None, comme le client réel en cas d'échec.
        response = one_shot_app.test_client().post("/webhook/whatsapp", data=WHATSAPP_MESSAGE)
        assert response.status_code == 200
        (saved,) = one_shot_app.extensions["tecpap"].db.get_all_orders()
        assert (saved["statut"], saved["quantite"], saved["produit_type"]) == ("en_attente", 2000, "Sachets fond plat")
        assert saved["modele_llm"] is None
        assert RULES_FALLBACK_ANOMALY in saved["anomalies_list"]


# ---------------------------------------------------------------------- planificateur

class TestSchedulerErrors:
    """Seules les erreurs de validation du planificateur sont renvoyées à l'utilisateur."""

    def test_validation_error_is_a_value_error(self):
        scheduler = BackupScheduler("orders.db", "backups")
        with pytest.raises(SchedulerConfigError) as excinfo:
            scheduler.configure(interval_hours=0)
        assert isinstance(excinfo.value, ValueError)
        assert excinfo.value.message == "L'intervalle doit être compris entre 1 et 24 heures"

    @pytest.mark.parametrize("value", ["abc", 1.5, True, float("inf"), [], "1e400"])
    def test_non_integer_values_are_refused(self, value):
        with pytest.raises(SchedulerConfigError, match="nombre entier"):
            BackupScheduler("orders.db", "backups").configure(keep_backups=value)

    def test_route_returns_the_french_message(self, flask_client):
        response = flask_client.post("/api/backup/scheduler/configure", json={"keep_backups": 0})
        assert response.status_code == 400
        assert response.get_json()["error"] == "Le nombre de sauvegardes conservées doit être au moins 1"

    def test_unexpected_value_error_stays_in_the_log(self, flask_client, services, monkeypatch, caplog):
        def broken(**kwargs):
            raise ValueError("chemin interne C:/donnees/backups")

        monkeypatch.setattr(services.backup_scheduler, "configure", broken)
        with caplog.at_level(logging.ERROR):
            response = flask_client.post("/api/backup/scheduler/configure", json={"interval_hours": 6})
        assert response.status_code == 500
        assert response.get_json()["error"] == GENERIC_ERROR_MESSAGE
        assert "C:/donnees" not in response.get_data(as_text=True)
        assert "C:/donnees" in caplog.text


class TestReloaderDetection:
    """Processus enfant du rechargeur de Werkzeug."""

    @pytest.mark.parametrize("env, expected", [
        ({"WERKZEUG_RUN_MAIN": "true"}, True),
        ({"WERKZEUG_RUN_MAIN": "false"}, False),
        ({}, False),
    ])
    def test_explicit_environment(self, env, expected):
        assert is_reloader_child_process(env) is expected

    def test_process_environment(self, monkeypatch):
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
        assert is_reloader_child_process() is True
        monkeypatch.delenv("WERKZEUG_RUN_MAIN")
        assert is_reloader_child_process() is False


# ---------------------------------------------------------------------- tableau de bord sécurisé

@pytest.fixture
def dashboard(tmp_path, sample_order_data):
    """Application avec connexion et protection CSRF, une commande en base et un utilisateur connecté.

    Le client de test n'ajoute aucun en-tête de lui-même : chaque requête qui modifie des données
    porte le jeton lu dans la page, comme dans le navigateur.
    """
    settings = Settings.for_tests(tmp_path, auth_enabled=True, csrf_enabled=True,
                                  admin_username=TEST_ADMIN_USERNAME, admin_password_hash=TEST_ADMIN_PASSWORD_HASH)
    app = create_app(settings)
    services = app.extensions["tecpap"]
    order_id = services.db.create_order(sample_order_data)
    client_id = services.db.get_order(order_id)["client_id"]
    with app.test_client() as client:
        login_page = client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
        response = client.post("/login", data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD,
                                               "csrf_token": token})
        assert response.status_code == 302
        yield {"app": app, "client": client, "services": services, "order_id": order_id, "client_id": client_id}
    services.close()


DASHBOARD_PAGES = ["/", "/orders", "/orders/{order_id}", "/process", "/analytics", "/clients",
                   "/clients/{client_id}", "/alerts", "/whatsapp", "/sage", "/backups"]


class TestSecureDashboard:
    """Pages et actions du tableau de bord avec connexion et protection CSRF."""

    @staticmethod
    def _page(dashboard, url: str) -> str:
        response = dashboard["client"].get(url.format(**dashboard))
        assert response.status_code == 200, url
        return response.get_data(as_text=True)

    def test_every_page_is_listed(self, dashboard):
        pages = {rule.rule for rule in dashboard["app"].url_map.iter_rules() if rule.endpoint.startswith("pages.")}
        pages.add("/backups")
        listed = {url.replace("{order_id}", "<int:order_id>").replace("{client_id}", "<int:client_id>")
                  for url in DASHBOARD_PAGES}
        assert pages == listed

    @pytest.mark.parametrize("url", DASHBOARD_PAGES)
    def test_page_renders_for_logged_in_user(self, dashboard, url):
        html = self._page(dashboard, url)
        with dashboard["client"].session_transaction() as session:
            assert _csrf_from_page(html) == session["csrf_token"]
        assert "window.fetch = function" in html
        assert 'action="/logout"' in html
        assert f'<span id="currentUser" class="font-medium text-slate-700">{TEST_ADMIN_USERNAME}</span>' in html

    def test_mutation_without_token_is_refused(self, dashboard):
        response = dashboard["client"].post(f"/api/orders/{dashboard['order_id']}/validate", json={})
        assert response.status_code == 403
        assert dashboard["services"].db.get_order(dashboard["order_id"])["statut"] == "en_attente"

    def test_validate_from_orders_page(self, dashboard):
        token = _csrf_from_page(self._page(dashboard, "/orders"))
        response = dashboard["client"].post(f"/api/orders/{dashboard['order_id']}/validate", json={},
                                            headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        order = dashboard["services"].db.get_order(dashboard["order_id"])
        assert order["statut"] == "validee"
        assert order["validated_by"] == TEST_ADMIN_USERNAME

    def test_reject_from_order_detail_page(self, dashboard):
        token = _csrf_from_page(self._page(dashboard, "/orders/{order_id}"))
        response = dashboard["client"].post(f"/api/orders/{dashboard['order_id']}/reject",
                                            json={"reason": "Stock insuffisant"}, headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        order = dashboard["services"].db.get_order(dashboard["order_id"])
        assert order["statut"] == "rejetee"
        assert order["motif_rejet"] == "Stock insuffisant"

    def test_edit_form_of_order_detail_page(self, dashboard):
        html = self._page(dashboard, "/orders/{order_id}")
        fields = _form_fields(html, "editForm")
        assert {"quantite", "grammage", "laize", "prix_unitaire", "date_livraison"} <= set(fields)
        # Valeurs saisies dans le formulaire : FormData les transmet toutes sous forme de texte.
        fields.update(quantite="6000", grammage="80", laize="", prix_unitaire="0.45", commercial="Équipe Nord")

        response = dashboard["client"].post(f"/api/orders/{dashboard['order_id']}/update", json=fields,
                                            headers={"X-CSRF-Token": _csrf_from_page(html)})

        assert response.status_code == 200, response.get_json()
        order = dashboard["services"].db.get_order(dashboard["order_id"])
        assert order["quantite"] == 6000
        assert order["grammage"] == 80
        assert order["laize"] is None
        assert order["prix_unitaire"] == 0.45
        assert order["commercial"] == "Équipe Nord"
        assert order["reste_a_livrer"] == 6000

    def test_generate_code_from_sage_page(self, dashboard):
        services = dashboard["services"]
        services.db.update_order(dashboard["order_id"], {"type_papier": "Kraft blanc", "grammage": 80, "laize": 1000})
        token = _csrf_from_page(self._page(dashboard, "/sage"))
        response = dashboard["client"].post(f"/api/orders/{dashboard['order_id']}/generate-code",
                                            headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        assert services.db.get_order(dashboard["order_id"])["code_article"] == response.get_json()["code"]

    def test_backup_actions_from_backups_page(self, dashboard):
        token = _csrf_from_page(self._page(dashboard, "/backups"))
        headers = {"X-CSRF-Token": token}
        client = dashboard["client"]

        created = client.post("/api/backup/create", json={}, headers=headers)
        assert created.status_code == 200
        filename = created.get_json()["filename"]
        assert (Path(dashboard["services"].settings.backup_dir) / filename).exists()

        configured = client.post("/api/backup/scheduler/configure", json={"interval_hours": "12"}, headers=headers)
        assert configured.status_code == 200
        assert configured.get_json()["status"]["interval_hours"] == 12

        deleted = client.delete(f"/api/backup/delete/{filename}", headers=headers)
        assert deleted.status_code == 200
        assert not (Path(dashboard["services"].settings.backup_dir) / filename).exists()

    def test_process_emails_from_process_page(self, dashboard):
        token = _csrf_from_page(self._page(dashboard, "/process"))
        response = dashboard["client"].post("/api/process-emails", headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        assert response.get_json()["orders_count"] == 0  # aucun identifiant Gmail dans la configuration de test

    def test_logout_form(self, dashboard):
        html = self._page(dashboard, "/")
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        response = dashboard["client"].post("/logout", data={"csrf_token": token})
        assert response.status_code == 302
        assert dashboard["client"].get("/orders").status_code == 302


class TestNotificationRendering:
    """Notifications des nouvelles commandes dans ``base.html``."""

    def test_untrusted_text_is_escaped(self):
        html = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "function escapeHtml(" in html
        for value in ("n.title", "n.message", "title", "message"):
            assert f"${{escapeHtml({value})}}" in html
        assert "${n.title}" not in html and "${n.message}" not in html

    def test_order_toast_does_not_clash_with_page_functions(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "function showOrderToast(" in base
        for page in TEMPLATES.glob("*.html"):
            assert "function showOrderToast(" not in page.read_text(encoding="utf-8") or page.name == "base.html"
