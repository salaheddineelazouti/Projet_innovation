"""Tests du menu latéral, du rendu des pages de l'assistant et des libellés communs aux gabarits."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from tecpap.chatbot.conversation import ConversationStore
from tecpap.security.events import KIND_PROMPT_INJECTION, log_security_event
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
            'href="/whatsapp"', ">Assistant WhatsApp<", 'href="/conversations"', 'href="/knowledge"', ">Analytics<",
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
