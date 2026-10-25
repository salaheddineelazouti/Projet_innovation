"""Tests du stockage des conversations WhatsApp (tecpap.chatbot.conversation)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from tecpap.chatbot.conversation import (
    DRAFT_TTL_HOURS,
    MAX_BODY_CHARS,
    PREVIEW_CHARS,
    STATES,
    Conversation,
    ConversationStore,
    conversation_key,
    ensure_schema,
)

START = datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc)


class Clock:
    """Horloge réglable pour les tests."""

    def __init__(self, moment: datetime = START):
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "conversations.db", check_same_thread=False)
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(conn, clock):
    return ConversationStore(conn, clock=clock)


def _draft(store: ConversationStore, **order) -> dict:
    return {"order": order, "asked": None, "updated_at": store.timestamp()}


class TestSchema:
    def test_ensure_schema_is_idempotent(self, conn):
        ensure_schema(conn)
        ensure_schema(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"conversations", "chat_messages"} <= tables

    def test_columns(self, conn):
        ensure_schema(conn)
        conversation_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        message_columns = {row[1] for row in conn.execute("PRAGMA table_info(chat_messages)")}
        assert conversation_columns == {"id", "phone", "state", "language", "draft_json", "last_order_id",
                                        "handoff", "created_at", "updated_at"}
        assert message_columns == {"id", "conversation_id", "direction", "body", "intent", "message_sid",
                                   "blocked", "created_at"}

    def test_store_creates_the_schema(self, conn):
        ConversationStore(conn)
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0

    def test_existing_data_is_kept(self, conn, store):
        store.get_or_create("+212600000001")
        ensure_schema(conn)
        assert store.find("+212600000001") is not None

    def test_invalid_ttl(self, conn):
        with pytest.raises(ValueError):
            ConversationStore(conn, draft_ttl_hours=0)

    def test_works_with_sqlite_row_factory(self, conn, clock):
        conn.row_factory = sqlite3.Row
        store = ConversationStore(conn, clock=clock)
        conversation = store.get_or_create("+212600000001")
        store.add_message(conversation.id, "in", "bonjour")
        assert store.recent_messages(conversation.id)[0]["body"] == "bonjour"
        assert store.list_conversations()[0]["phone"] == "+212600000001"


class TestConversationKey:
    @pytest.mark.parametrize("raw", ["whatsapp:+212612345678", "+212612345678", "0612345678",
                                     "+212 6 12 34 56 78", " whatsapp:+212612345678 ", "00212612345678"])
    def test_moroccan_forms_share_one_key(self, raw):
        assert conversation_key(raw) == "+212612345678"

    def test_foreign_number(self):
        assert conversation_key("whatsapp:+14155238886") == "+14155238886"

    def test_unrecognised_identifier_is_kept(self):
        assert conversation_key("whatsapp:Test User") == "testuser"

    @pytest.mark.parametrize("raw", [None, "", "   ", "whatsapp:", 12])
    def test_empty_identifier_raises(self, raw):
        with pytest.raises(ValueError):
            conversation_key(raw)


class TestConversations:
    def test_get_or_create_creates_an_idle_conversation(self, store):
        conversation = store.get_or_create("whatsapp:+212600000001")
        assert conversation.phone == "+212600000001"
        assert conversation.state == "idle"
        assert conversation.language is None
        assert conversation.draft is None
        assert conversation.handoff is False
        assert conversation.created_at == "2026-10-25 09:00:00"

    def test_get_or_create_returns_the_same_conversation(self, store):
        first = store.get_or_create("whatsapp:+212600000001")
        second = store.get_or_create("0600000001")
        assert first.id == second.id

    def test_save_and_reload(self, store, clock):
        conversation = store.get_or_create("+212600000001")
        conversation.state = "awaiting_confirmation"
        conversation.language = "darija"
        conversation.draft = _draft(store, type_produit="Sachets fond plat", quantite=3000, entreprise_cliente="Café é")
        conversation.last_order_id = 7
        conversation.handoff = True
        clock.advance(minutes=5)
        store.save(conversation)
        reloaded = store.get(conversation.id)
        assert reloaded == conversation
        assert reloaded.updated_at == "2026-10-25 09:05:00"
        assert reloaded.draft["order"]["entreprise_cliente"] == "Café é"

    def test_save_rejects_unknown_state(self, store):
        conversation = store.get_or_create("+212600000001")
        conversation.state = "hacked"
        with pytest.raises(ValueError):
            store.save(conversation)

    def test_unknown_state_in_table_is_read_as_idle(self, conn, store):
        conversation = store.get_or_create("+212600000001")
        conn.execute("UPDATE conversations SET state = 'xyz' WHERE id = ?", (conversation.id,))
        assert store.get(conversation.id).state == "idle"

    def test_unreadable_draft_is_ignored(self, conn, store):
        conversation = store.get_or_create("+212600000001")
        conn.execute("UPDATE conversations SET draft_json = '{not json' WHERE id = ?", (conversation.id,))
        assert store.get(conversation.id).draft is None
        conn.execute("UPDATE conversations SET draft_json = '[1, 2]' WHERE id = ?", (conversation.id,))
        assert store.get(conversation.id).draft is None

    def test_find_and_get_missing(self, store):
        assert store.find("+212600000009") is None
        assert store.get(12345) is None

    def test_to_dict(self, store):
        data = store.get_or_create("+212600000001").to_dict()
        assert set(data) == {"id", "phone", "state", "language", "draft", "last_order_id", "handoff",
                             "created_at", "updated_at"}

    def test_states(self):
        assert STATES == ("idle", "collecting", "awaiting_confirmation", "handoff")


class TestDraftExpiry:
    def test_recent_draft_is_kept(self, store, clock):
        conversation = store.get_or_create("+212600000001")
        conversation.state, conversation.draft = "collecting", _draft(store, quantite=10)
        store.save(conversation)
        clock.advance(hours=DRAFT_TTL_HOURS - 1)
        reloaded = store.get_or_create("+212600000001")
        assert reloaded.draft is not None and reloaded.state == "collecting"
        assert reloaded.draft_expired is False

    def test_old_draft_is_dropped(self, store, clock):
        conversation = store.get_or_create("+212600000001")
        conversation.state, conversation.draft = "awaiting_confirmation", _draft(store, quantite=10)
        store.save(conversation)
        clock.advance(hours=DRAFT_TTL_HOURS, seconds=1)
        reloaded = store.get_or_create("+212600000001")
        assert reloaded.draft is None and reloaded.state == "idle" and reloaded.draft_expired is True
        assert store.get(conversation.id).draft is None

    def test_draft_age_comes_from_the_draft_not_the_conversation(self, store, clock):
        conversation = store.get_or_create("+212600000001")
        conversation.state, conversation.draft = "collecting", _draft(store, quantite=10)
        store.save(conversation)
        clock.advance(hours=20)
        store.save(conversation)
        clock.advance(hours=5)
        assert store.get_or_create("+212600000001").draft is None

    def test_draft_without_date_is_dropped(self, store):
        conversation = store.get_or_create("+212600000001")
        conversation.state, conversation.draft = "collecting", {"order": {}}
        store.save(conversation)
        assert store.get_or_create("+212600000001").draft is None

    @pytest.mark.parametrize("stamp", ["hier", "2026-13-45 99:99:99", 12345, None])
    def test_draft_with_an_unreadable_date_is_dropped(self, store, stamp):
        conversation = store.get_or_create("+212600000001")
        conversation.state, conversation.draft = "collecting", {"order": {}, "updated_at": stamp}
        store.save(conversation)
        assert store.get_or_create("+212600000001").draft is None

    def test_handoff_state_is_kept_when_the_draft_expires(self, store, clock):
        conversation = store.get_or_create("+212600000001")
        conversation.state, conversation.handoff, conversation.draft = "handoff", True, _draft(store, quantite=1)
        store.save(conversation)
        clock.advance(hours=30)
        reloaded = store.get_or_create("+212600000001")
        assert reloaded.state == "handoff" and reloaded.handoff and reloaded.draft is None

    def test_reset_after(self, store, clock):
        old = store.get_or_create("+212600000001")
        old.state, old.draft = "collecting", _draft(store, quantite=1)
        store.save(old)
        clock.advance(hours=25)
        recent = store.get_or_create("+212600000002")
        recent.state, recent.draft = "awaiting_confirmation", _draft(store, quantite=2)
        store.save(recent)
        assert store.reset_after() == 1
        assert store.get(old.id).draft is None and store.get(old.id).state == "idle"
        assert store.get(recent.id).draft is not None
        assert store.reset_after(hours=0) == 0
        clock.advance(seconds=1)
        assert store.reset_after(hours=0) == 1
        assert store.reset_after() == 0

    def test_custom_ttl(self, conn, clock):
        store = ConversationStore(conn, clock=clock, draft_ttl_hours=1)
        conversation = store.get_or_create("+212600000001")
        conversation.state, conversation.draft = "collecting", _draft(store, quantite=1)
        store.save(conversation)
        clock.advance(hours=2)
        assert store.get_or_create("+212600000001").draft is None


class TestReset:
    def test_reset_returns_to_idle(self, store):
        conversation = store.get_or_create("whatsapp:+212600000001")
        conversation.state, conversation.handoff, conversation.draft = "handoff", True, _draft(store)
        store.save(conversation)
        assert store.reset("0600000001") is True
        reloaded = store.get(conversation.id)
        assert (reloaded.state, reloaded.handoff, reloaded.draft) == ("idle", False, None)

    def test_reset_unknown_phone(self, store):
        assert store.reset("+212600000077") is False


class TestMessages:
    def test_add_and_read_messages(self, store, clock):
        conversation = store.get_or_create("+212600000001")
        first = store.add_message(conversation.id, "in", "bonjour", message_sid="SM1")
        clock.advance(seconds=1)
        second = store.add_message(conversation.id, "out", "réponse", intent="greeting")
        assert first < second
        messages = store.recent_messages(conversation.id)
        assert [message["body"] for message in messages] == ["bonjour", "réponse"]
        assert messages[0]["message_sid"] == "SM1" and messages[0]["blocked"] is False
        assert messages[1]["intent"] == "greeting" and messages[1]["direction"] == "out"

    def test_recent_messages_limit_keeps_the_latest(self, store):
        conversation = store.get_or_create("+212600000001")
        for index in range(15):
            store.add_message(conversation.id, "in", f"m{index}")
        assert [message["body"] for message in store.recent_messages(conversation.id, 3)] == ["m12", "m13", "m14"]
        assert len(store.recent_messages(conversation.id, -5)) == 1
        assert len(store.recent_messages(conversation.id, "abc")) == 10

    def test_duplicate_message_sid(self, store):
        conversation = store.get_or_create("+212600000001")
        assert store.add_message(conversation.id, "in", "a", message_sid="SM42") is not None
        assert store.add_message(conversation.id, "in", "b", message_sid="SM42") is None
        assert store.is_duplicate("SM42") is True
        assert store.is_duplicate(" SM42 ") is True
        assert len(store.recent_messages(conversation.id)) == 1

    def test_store_still_works_after_a_duplicate(self, store):
        conversation = store.get_or_create("+212600000001")
        store.add_message(conversation.id, "in", "a", message_sid="SM1")
        store.add_message(conversation.id, "in", "b", message_sid="SM1")
        assert store.add_message(conversation.id, "out", "c") is not None
        conversation.state = "collecting"
        store.save(conversation)
        assert store.get(conversation.id).state == "collecting"

    @pytest.mark.parametrize("sid", [None, "", "   "])
    def test_messages_without_sid_are_never_duplicates(self, store, sid):
        conversation = store.get_or_create("+212600000001")
        assert store.add_message(conversation.id, "in", "a", message_sid=sid) is not None
        assert store.add_message(conversation.id, "in", "a", message_sid=sid) is not None
        assert store.is_duplicate(sid) is False

    def test_invalid_direction(self, store):
        conversation = store.get_or_create("+212600000001")
        with pytest.raises(ValueError):
            store.add_message(conversation.id, "sideways", "x")

    def test_long_body_is_truncated_and_none_is_empty(self, store):
        conversation = store.get_or_create("+212600000001")
        store.add_message(conversation.id, "in", "x" * (MAX_BODY_CHARS + 100))
        store.add_message(conversation.id, "out", None)
        bodies = [message["body"] for message in store.recent_messages(conversation.id)]
        assert len(bodies[0]) == MAX_BODY_CHARS and bodies[1] == ""

    def test_update_message(self, store):
        conversation = store.get_or_create("+212600000001")
        message_id = store.add_message(conversation.id, "in", "x")
        store.update_message(message_id, intent="blocked", blocked=True)
        store.update_message(message_id)
        message = store.recent_messages(conversation.id)[0]
        assert message["intent"] == "blocked" and message["blocked"] is True

    def test_count_recent_inbound(self, store, clock):
        conversation = store.get_or_create("whatsapp:+212600000001")
        other = store.get_or_create("+212600000002")
        store.add_message(conversation.id, "in", "old")
        clock.advance(seconds=61)
        store.add_message(conversation.id, "in", "a")
        store.add_message(conversation.id, "in", "b")
        store.add_message(conversation.id, "out", "reply")
        store.add_message(other.id, "in", "other")
        assert store.count_recent_inbound("0600000001", 60) == 2
        assert store.count_recent_inbound("+212600000001", 3600) == 3
        assert store.count_recent_inbound("+212600000003", 60) == 0

    def test_concurrent_writes(self, store):
        conversation = store.get_or_create("+212600000001")
        results = []

        def write(index: int) -> None:
            results.append(store.add_message(conversation.id, "in", f"m{index}", message_sid=f"SM{index % 5}"))

        threads = [threading.Thread(target=write, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(result is not None for result in results) == 5
        assert len(store.recent_messages(conversation.id, 100)) == 5


class TestDashboard:
    def test_list_conversations(self, store, clock):
        first = store.get_or_create("+212600000001")
        store.add_message(first.id, "in", "  bonjour\n  tout le monde ")
        store.add_message(first.id, "out", "x" * 300, blocked=True)
        clock.advance(minutes=1)
        second = store.get_or_create("+212600000002")
        second.state, second.handoff, second.draft = "handoff", True, _draft(store)
        store.save(second)
        rows = store.list_conversations()
        assert [row["phone"] for row in rows] == ["+212600000002", "+212600000001"]
        assert rows[0]["handoff"] is True and rows[0]["has_draft"] is True
        assert rows[0]["message_count"] == 0 and rows[0]["last_message"] == ""
        assert rows[1]["message_count"] == 2 and rows[1]["blocked_count"] == 1
        assert rows[1]["last_direction"] == "out"
        assert len(rows[1]["last_message"]) == PREVIEW_CHARS and rows[1]["last_message"].endswith("…")
        assert "draft_json" not in rows[0]

    def test_list_conversations_handoff_only_and_limit(self, store):
        for index in range(5):
            conversation = store.get_or_create(f"+21260000000{index}")
            conversation.handoff = index % 2 == 0
            store.save(conversation)
        assert len(store.list_conversations(handoff_only=True)) == 3
        assert len(store.list_conversations(limit=2)) == 2
        assert len(store.list_conversations(limit=0)) == 1

    def test_preview_of_short_message(self, store):
        conversation = store.get_or_create("+212600000001")
        store.add_message(conversation.id, "in", "  bonjour\n  tout le monde ")
        assert store.list_conversations()[0]["last_message"] == "bonjour tout le monde"

    def test_get_thread(self, store):
        conversation = store.get_or_create("+212600000001")
        for index in range(3):
            store.add_message(conversation.id, "in" if index % 2 == 0 else "out", f"m{index}")
        thread = store.get_thread(conversation.id, limit=2)
        assert thread["conversation"]["phone"] == "+212600000001"
        assert [message["body"] for message in thread["messages"]] == ["m1", "m2"]

    def test_get_thread_missing(self, store):
        assert store.get_thread(999) is None


def test_conversation_equality_ignores_the_expiry_flag():
    first = Conversation(id=1, phone="+212600000001")
    second = Conversation(id=1, phone="+212600000001", draft_expired=True)
    assert first == second
