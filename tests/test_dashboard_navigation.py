"""Tests du gabarit commun (menu, en-tête, pied de page), des pages du tableau de bord et des libellés des gabarits."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import render_template_string

from tecpap import __version__
from tecpap.chatbot.conversation import ConversationStore
from tecpap.config import Settings
from tecpap.security.events import KIND_PROMPT_INJECTION, log_security_event
from tecpap.web import create_app
from tecpap.web.routes import assistant

PHONE = "+212612345678"


@pytest.fixture
def dashboard_services(secure_app):
    return secure_app.extensions["tecpap"]


def _conversation(services, phone: str, *, handoff: bool):
    store = ConversationStore(services.db.connection, clock=lambda: datetime.now(timezone.utc))
    conversation = store.get_or_create(phone)
    store.add_message(conversation.id, "in", "Bonjour", intent="greeting")
    conversation.handoff = handoff
    conversation.state = "handoff" if handoff else "idle"
    store.save(conversation)
    return conversation


@pytest.fixture
def pages(dashboard_services):
    """Adresses des pages de l'assistant, avec une conversation transférée et un événement de sécurité."""
    conversation = _conversation(dashboard_services, PHONE, handoff=True)
    log_security_event(dashboard_services.db.connection, channel="whatsapp", sender=PHONE,
                       kind=KIND_PROMPT_INJECTION, score=80, details="test")
    return ["/conversations", "/conversations?handoff=1", f"/conversations/{conversation.id}", "/knowledge",
            "/knowledge?q=sachet", "/security", f"/security?kind={KIND_PROMPT_INJECTION}"]


def _badge(html: str) -> str | None:
    match = re.search(r'id="handoffBadge"[^>]*>(\d+)<', html)
    return match.group(1) if match else None


class TestNavigation:
    """Entrées du menu latéral."""

    def test_entries_and_their_sections(self, logged_in_client):
        html = logged_in_client.get("/").get_data(as_text=True)
        nav = re.search(r"<nav class=\"flex-1.*?</nav>", html, flags=re.DOTALL).group(0)
        order = [nav.index(marker) for marker in (
            'href="/whatsapp"', ">Assistant WhatsApp<", 'href="/conversations"', 'href="/knowledge"', ">Suivi<",
            'href="/backups"', 'href="/security"',
        )]
        assert order == sorted(order)
        assert ">Conversations<" in nav and ">Base de connaissances<" in nav and ">Sécurité<" in nav

    @pytest.mark.parametrize("url, href", [
        ("/conversations", "/conversations"), ("/knowledge", "/knowledge"), ("/security", "/security"),
    ])
    def test_active_entry(self, logged_in_client, url, href):
        html = logged_in_client.get(url).get_data(as_text=True)
        assert f'<a href="{href}" class="nav-item active">' in html
        assert html.count("nav-item active") == 1

    def test_detail_page_keeps_the_entry_active(self, logged_in_client, dashboard_services):
        conversation = _conversation(dashboard_services, PHONE, handoff=False)
        html = logged_in_client.get(f"/conversations/{conversation.id}").get_data(as_text=True)
        assert '<a href="/conversations" class="nav-item active">' in html

    def test_badge_counts_handoff_conversations(self, logged_in_client, dashboard_services):
        assert _badge(logged_in_client.get("/").get_data(as_text=True)) is None
        _conversation(dashboard_services, PHONE, handoff=True)
        _conversation(dashboard_services, "+212698765432", handoff=True)
        _conversation(dashboard_services, "+212600000001", handoff=False)
        for url in ("/", "/orders", "/security"):
            assert _badge(logged_in_client.get(url).get_data(as_text=True)) == "2"

    def test_badge_disappears_after_reset(self, logged_in_client, dashboard_services):
        conversation = _conversation(dashboard_services, PHONE, handoff=True)
        logged_in_client.post(f"/conversations/{conversation.id}/reset")
        assert _badge(logged_in_client.get("/").get_data(as_text=True)) is None

    def test_pages_render_without_the_conversation_tables(self, logged_in_client, dashboard_services):
        connection = dashboard_services.db.connection
        connection.execute("DROP TABLE chat_messages")
        connection.execute("DROP TABLE conversations")
        connection.commit()
        response = logged_in_client.get("/")
        assert response.status_code == 200
        assert _badge(response.get_data(as_text=True)) is None


class TestAssistantPages:
    """Rendu des pages avec connexion et protection CSRF."""

    def test_every_page_renders_for_a_logged_in_user(self, logged_in_client, pages):
        with logged_in_client.session_transaction() as session:
            token = session["csrf_token"]
        for url in pages:
            response = logged_in_client.get(url)
            assert response.status_code == 200, url
            html = response.get_data(as_text=True)
            assert f'<meta name="csrf-token" content="{token}">' in html, url
            assert 'action="/logout"' in html, url

    def test_every_post_form_carries_the_session_token(self, logged_in_client, pages):
        with logged_in_client.session_transaction() as session:
            token = session["csrf_token"]
        forms = 0
        for url in pages:
            html = logged_in_client.get(url).get_data(as_text=True)
            for form in re.findall(r'<form method="post".*?</form>', html, flags=re.DOTALL):
                assert f'name="csrf_token" value="{token}"' in form, url
                forms += 1
        assert forms >= 4

    def test_pages_require_login(self, secure_client, pages):
        for url in pages:
            response = secure_client.get(url)
            assert response.status_code == 302, url
            assert "/login?next=" in response.headers["Location"], url

    @pytest.mark.parametrize("url", ["/conversations/1/reset", "/conversations/1/reply", "/knowledge/reindex"])
    def test_actions_require_login(self, secure_client, pages, url):
        response = secure_client.post(url, data={"message": "Bonjour"})
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    @pytest.mark.parametrize("url", ["/conversations/1/reset", "/conversations/1/reply", "/knowledge/reindex"])
    def test_actions_require_the_csrf_token(self, logged_in_client, pages, url):
        logged_in_client.environ_base.pop("HTTP_X_CSRF_TOKEN")
        assert logged_in_client.post(url, data={"message": "Bonjour"}).status_code == 403


class TestLabels:
    """Libellés et filtre de date fournis aux gabarits."""

    @pytest.mark.parametrize("value, expected", [
        ("2026-10-25 14:03:05", "25/10/2026 14:03"),
        ("2026-10-25 14:03:05.123456", "25/10/2026 14:03"),
        (None, "—"),
        ("", "—"),
        ("hier", "hier"),
    ])
    def test_timestamp_filter(self, value, expected):
        assert assistant.format_timestamp(value) == expected

    @pytest.mark.parametrize("code, label, short", [
        ("fr", "Français", "FR"), ("darija_latin", "Darija (lettres latines)", "Darija"),
        ("darija_ar", "Darija (lettres arabes)", "Darija"), ("ar", "Arabe standard", "AR"),
        ("darija", "Darija", "Darija"), ("unknown", "Indéterminée", "Indéterminée"), ("xx", "xx", "xx"),
    ])
    def test_language_labels(self, code, label, short):
        assert assistant.language_label(code) == label
        assert assistant.language_label(code, short=True) == short

    def test_missing_language(self):
        assert assistant.language_label(None) == "Non détectée"

    def test_every_conversation_state_has_a_label(self):
        from tecpap.chatbot.conversation import STATES

        assert set(STATES) <= set(assistant.STATE_LABELS)

    def test_every_intent_has_a_label(self):
        from tecpap.chatbot.intents import INTENTS

        assert set(INTENTS) <= set(assistant.INTENT_LABELS)
        assert assistant.intent_label(assistant.AGENT_INTENT) == "Équipe commerciale"
        assert assistant.intent_label("intention_inconnue") == "intention_inconnue"
        assert assistant.intent_label(None) == ""

    def test_every_required_field_has_a_label(self):
        from tecpap.catalog import REQUIRED_ORDER_FIELDS

        for name in REQUIRED_ORDER_FIELDS:
            assert assistant.field_label(name) != name

    def test_event_and_channel_labels(self):
        assert assistant.event_kind_label(KIND_PROMPT_INJECTION) == "Tentative d'injection d'instructions"
        assert assistant.event_kind_label("autre") == "autre"
        assert assistant.channel_label("whatsapp") == "WhatsApp"
        assert assistant.channel_label("fax") == "fax"

    @pytest.mark.parametrize("raw, expected", [
        ("whatsapp:+212612345678", PHONE), ("06 12 34 56 78", PHONE), ("+212 (0)6 12 34 56 78", PHONE),
        ("", None), (None, None),
    ])
    def test_phone_key(self, raw, expected):
        assert assistant.phone_key(raw) == expected


# ---------------------------------------------------------------------- gabarit commun et pages du tableau de bord

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "tecpap" / "web" / "templates"
DASHBOARD_TEMPLATES = ("base.html", "index.html", "orders.html", "order_detail.html", "clients.html",
                       "client_detail.html", "alerts.html", "process.html", "whatsapp.html", "_flash_messages.html")


class TestLayout:
    """En-tête, menus et pied de page de base.html."""

    def test_footer_shows_the_package_version_and_the_current_year(self, logged_in_client):
        html = logged_in_client.get("/").get_data(as_text=True)
        footer = re.search(r"<footer.*?</footer>", html, flags=re.DOTALL).group(0)
        assert f'id="appVersion">v{__version__}<' in footer
        assert f"© {datetime.now(timezone.utc).year} " in footer or f"© {datetime.now().year} " in footer
        assert "v2.0" not in footer and "Opérationnel" not in footer

    def test_chart_library_is_pinned_and_loaded_once(self, logged_in_client):
        for url in ("/", "/analytics"):
            html = logged_in_client.get(url).get_data(as_text=True)
            sources = re.findall(r'<script src="([^"]*chart[^"]*)"', html)
            assert sources == ["https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js"], url

    def test_header_search_targets_the_order_list(self, logged_in_client):
        html = logged_in_client.get("/clients").get_data(as_text=True)
        form = re.search(r'<form method="get" action="/orders" role="search".*?</form>', html, flags=re.DOTALL)
        assert form
        assert 'name="q"' in form.group(0) and '<label for="headerSearchInput"' in form.group(0)

    def test_menus_can_be_opened_from_the_keyboard(self, logged_in_client):
        html = logged_in_client.get("/orders").get_data(as_text=True)
        for menu in ("actionsMenu", "exportMenu"):
            button = re.search(rf'<button[^>]*aria-controls="{menu}"[^>]*>', html).group(0)
            assert 'type="button"' in button and "data-menu-toggle" in button
            assert 'aria-haspopup="true"' in button and 'aria-expanded="false"' in button
        assert ".dropdown.open .dropdown-menu" in html
        assert "group-hover:visible" not in html

    def test_icon_buttons_have_a_name(self, logged_in_client):
        html = logged_in_client.get("/").get_data(as_text=True)
        assert re.search(r'<button[^>]*id="sidebarToggle"[^>]*aria-label="Réduire le menu"', html)
        assert re.search(r'<button[^>]*id="notificationButton"[^>]*aria-label="Notifications"', html)

    def test_section_titles_are_in_french(self, logged_in_client):
        html = logged_in_client.get("/").get_data(as_text=True)
        assert ">Analytics<" not in html and "Top Clients" not in html
        assert "Meilleurs clients" in html

    @pytest.mark.parametrize("name", DASHBOARD_TEMPLATES)
    def test_templates_do_not_disable_escaping(self, name):
        assert "|safe" not in (TEMPLATES_DIR / name).read_text(encoding="utf-8").replace(" ", "")

    def test_email_card_links_to_email_orders(self, logged_in_client):
        html = logged_in_client.get("/").get_data(as_text=True)
        link = re.search(r'<a href="([^"]*)"[^>]*id="emailOrdersLink"', html).group(1)
        assert link == "/orders?source=email"
        assert logged_in_client.get(link).status_code == 200


class TestTemplateFilters:
    """Filtres Jinja de mise en forme et variables du pied de page."""

    def _render(self, app, source: str, **values) -> str:
        # render_template_string applique les processeurs de contexte (version et année du pied de page).
        with app.test_request_context("/"):
            return render_template_string(source, **values)

    def test_number_filters(self, secure_app):
        assert self._render(secure_app, "{{ 10000|format_quantity }}") == "10 000"
        assert self._render(secure_app, "{{ 2.5|format_quantity('kg') }}") == "2,5 kg"
        assert self._render(secure_app, "{{ 1234.5|format_amount }}") == "1 234,50 MAD"
        assert self._render(secure_app, "{{ 12|format_amount('EUR') }}") == "12,00 EUR"
        assert self._render(secure_app, "{{ none|format_amount(none) }}") == "—"

    def test_dates_use_the_configured_time_zone(self, secure_app, tmp_path):
        assert self._render(secure_app, "{{ '2025-07-15 14:03:00'|format_datetime }}") == "15/07/2025 15:03"
        assert self._render(secure_app, "{{ '2025-07-15 23:30:00'|format_date }}") == "16/07/2025"
        utc_app = create_app(Settings.for_tests(tmp_path / "utc", timezone="UTC"))
        try:
            assert self._render(utc_app, "{{ '2025-07-15 14:03:00'|format_datetime }}") == "15/07/2025 14:03"
        finally:
            utc_app.extensions["tecpap"].close()

    def test_order_reference_filter(self, secure_app):
        assert self._render(secure_app, "{{ order|order_reference }}", order={"id": 7}) == "CMD-7"
        assert self._render(secure_app, "{{ order|order_reference }}",
                            order={"id": 7, "numero_commande": "BC-7"}) == "BC-7"

    def test_footer_values(self, secure_app):
        assert self._render(secure_app, "{{ app_version }}") == __version__
        assert int(self._render(secure_app, "{{ current_year }}")) >= 2025


class TestStatusPages:
    """Pages Emails et WhatsApp : l'état affiché vient de la configuration."""

    def test_email_page_without_credentials(self, logged_in_client):
        html = logged_in_client.get("/process").get_data(as_text=True)
        assert "Non configurés" in re.search(r'id="gmailStatus".*?</div>', html, flags=re.DOTALL).group(0)
        assert "Clé absente" in re.search(r'id="llmStatus".*?</div>', html, flags=re.DOTALL).group(0)
        assert 'id="gmailWarning"' in html
        assert "Connecté" not in html.split("<main", 1)[1]

    def test_email_page_with_credentials(self, tmp_path):
        settings = Settings.for_tests(tmp_path, gmail_email="commandes@tecpap.ma", gmail_app_password="abcd")
        app = create_app(settings)
        try:
            html = app.test_client().get("/process").get_data(as_text=True)
        finally:
            app.extensions["tecpap"].close()
        assert "Configurés" in re.search(r'id="gmailStatus".*?</div>', html, flags=re.DOTALL).group(0)
        assert 'id="gmailWarning"' not in html

    def test_email_page_has_no_simulated_steps(self, logged_in_client):
        html = logged_in_client.get("/process").get_data(as_text=True)
        for text in ("Connexion Gmail établie", "Authentification réussie", "simulateProgress",
                     "Traitement terminé avec succès", "Détection de relances", "Scan emails"):
            assert text not in html, text
        assert "Détection des renouvellements de commande" in html

    def test_whatsapp_page_without_credentials(self, logged_in_client):
        html = logged_in_client.get("/whatsapp").get_data(as_text=True)
        assert "Non configuré" in re.search(r'id="statusIndicator".*?</span>\s*Non configuré\s*</span>', html,
                                            flags=re.DOTALL).group(0)
        assert 'id="whatsappNumber">+14155238886<' in html
        assert "checkStatus();" not in html
        assert 'href="/orders?source=whatsapp"' in html
        assert "Webhook URL" not in html and "Adresse du webhook" in html

    def test_whatsapp_page_does_not_claim_a_verified_connection(self, tmp_path):
        settings = Settings.for_tests(tmp_path, twilio_account_sid="AC" + "0" * 32, twilio_auth_token="jeton")
        app = create_app(settings)
        try:
            html = app.test_client().get("/whatsapp").get_data(as_text=True)
        finally:
            app.extensions["tecpap"].close()
        assert "if (data.verified)" in html and "Identifiants configurés" in html
        assert "checkStatus();" in html


class TestAlertsPage:
    """Compteurs et titres des alertes calculées par AlertSystem."""

    def test_counts_titles_and_links(self, logged_in_client, dashboard_services):
        db = dashboard_services.db
        urgent = db.create_order({"entreprise_cliente": "Snack Atlas", "email_id": "mail-a1",
                                  "email_subject": "Commande urgent", "confiance": 90})
        doubtful = db.create_order({"entreprise_cliente": "Café Rif", "email_id": "mail-a2", "confiance": 10})
        html = logged_in_client.get("/alerts").get_data(as_text=True)
        assert re.search(r'id="criticalCount">(\d+)<', html).group(1) == "1"
        assert re.search(r'id="warningCount">(\d+)<', html).group(1) == "1"
        assert re.search(r'id="infoCount">(\d+)<', html).group(1) == "0"
        assert "Commande urgente" in html and "Extraction peu fiable" in html
        assert f'href="/orders/{urgent}"' in html and f'href="/orders/{doubtful}"' in html
        assert "fa-times" not in re.search(r'id="alertList".*', html, flags=re.DOTALL).group(0).split("</main>")[0]


class TestClientPages:
    """Fiche et liste des clients."""

    def test_client_page_shows_the_estimate(self, logged_in_client, dashboard_services):
        db = dashboard_services.db
        first = db.create_order({"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat",
                                 "quantite": 1000, "email_id": "mail-p1"})
        second = db.create_order({"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat",
                                  "quantite": 3000, "email_id": "mail-p2"})
        db.connection.execute("UPDATE commandes SET created_at = '2025-06-01 10:00:00' WHERE id = ?", (first,))
        db.connection.execute("UPDATE commandes SET created_at = '2025-06-11 10:00:00' WHERE id = ?", (second,))
        db.connection.commit()
        client_id = db.get_order(first)["client_id"]
        html = logged_in_client.get(f"/clients/{client_id}").get_data(as_text=True)
        assert "Entreprise non spécifiée" not in html
        assert re.search(r'id="predictedDate">\s*vers le 21/06/2025\s*<', html)
        assert re.search(r'id="predictedQuantity">\s*2 000\s*<', html)
        assert "Sachets fond plat" in re.search(r'id="clientPrediction".*?</div>\s*</div>', html,
                                                flags=re.DOTALL).group(0)

    def test_client_without_history(self, logged_in_client, dashboard_services):
        cursor = dashboard_services.db.connection.execute("INSERT INTO clients (nom) VALUES ('Client Sans Commande')")
        dashboard_services.db.connection.commit()
        html = logged_in_client.get(f"/clients/{cursor.lastrowid}").get_data(as_text=True)
        assert "pas d'estimation possible" in html

    def test_client_list_has_no_empty_company_line(self, logged_in_client, dashboard_services):
        dashboard_services.db.create_order({"entreprise_cliente": "Snack Atlas", "email_id": "mail-p3"})
        html = logged_in_client.get("/clients").get_data(as_text=True)
        assert '<p class="text-xs text-slate-500"></p>' not in html
        assert 'aria-label="Rechercher un client par nom, email ou téléphone"' in html
