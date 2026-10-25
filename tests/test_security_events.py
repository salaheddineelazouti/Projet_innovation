"""Journal des événements de sécurité (tecpap.security.events)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tecpap.security.events import (
    DETAILS_MAX_CHARS,
    KIND_OUTPUT_BLOCKED,
    KIND_PROMPT_INJECTION,
    LABEL_MAX_CHARS,
    MAX_LIMIT,
    TABLE_NAME,
    count_security_events,
    ensure_schema,
    log_security_event,
    recent_security_events,
)


@pytest.fixture
def conn():
    """Base SQLite en mémoire, table créée."""
    connection = sqlite3.connect(":memory:")
    ensure_schema(connection)
    yield connection
    connection.close()


def _columns(connection: sqlite3.Connection) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({TABLE_NAME})")]


class TestSchema:
    """Création de la table."""

    def test_columns(self, conn):
        assert _columns(conn) == ["id", "created_at", "channel", "sender", "kind", "score", "details"]

    def test_ensure_schema_is_idempotent(self, conn):
        log_security_event(conn, channel="whatsapp", sender="+212612345678", kind=KIND_PROMPT_INJECTION)
        ensure_schema(conn)
        ensure_schema(conn)
        assert len(recent_security_events(conn)) == 1

    def test_works_next_to_the_application_tables(self, temp_db):
        ensure_schema(temp_db.connection)
        event_id = log_security_event(temp_db.connection, channel="email", sender="client@exemple.ma",
                                      kind=KIND_PROMPT_INJECTION, score=80)
        assert event_id == 1
        tables = {row[0] for row in temp_db.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"commandes", TABLE_NAME} <= tables

    def test_log_creates_the_table_when_missing(self):
        connection = sqlite3.connect(":memory:")
        assert log_security_event(connection, channel="whatsapp", sender=None, kind="test") == 1
        assert _columns(connection)


class TestLogSecurityEvent:
    """Enregistrement d'un événement."""

    def test_returns_identifiers_in_order(self, conn):
        first = log_security_event(conn, channel="whatsapp", sender="+212612345678", kind=KIND_PROMPT_INJECTION)
        second = log_security_event(conn, channel="whatsapp", sender="+212612345678", kind=KIND_OUTPUT_BLOCKED)
        assert (first, second) == (1, 2)

    @pytest.mark.parametrize("sender, expected", [
        ("whatsapp:+212612345678", "+2126•••••678"),
        ("+212612345678", "+2126•••••678"),
        ("h.bennani@exemple.ma", "h•••@exemple.ma"),
        (None, ""),
        ("", ""),
    ])
    def test_sender_is_stored_masked(self, conn, sender, expected):
        log_security_event(conn, channel="whatsapp", sender=sender, kind=KIND_PROMPT_INJECTION)
        [event] = recent_security_events(conn)
        assert event["sender"] == expected
        stored = conn.execute(f"SELECT sender FROM {TABLE_NAME}").fetchone()[0]
        assert "612345678" not in stored

    def test_details_are_truncated(self, conn):
        log_security_event(conn, channel="email", sender="a@exemple.ma", kind=KIND_PROMPT_INJECTION, details="x" * 2000)
        details = recent_security_events(conn)[0]["details"]
        assert len(details) == DETAILS_MAX_CHARS
        assert details.endswith("…")

    def test_details_of_exactly_the_maximum_length_are_kept(self, conn):
        log_security_event(conn, channel="email", sender=None, kind="test", details="y" * DETAILS_MAX_CHARS)
        assert recent_security_events(conn)[0]["details"] == "y" * DETAILS_MAX_CHARS

    def test_list_of_reasons_is_joined(self, conn):
        log_security_event(conn, channel="whatsapp", sender=None, kind="test", details=["raison A", "raison B"])
        assert recent_security_events(conn)[0]["details"] == "raison A ; raison B"

    def test_contacts_in_details_are_masked(self, conn):
        log_security_event(conn, channel="whatsapp", sender=None, kind="test",
                           details="numéros des autres clients : 0699887766, ali@exemple.ma")
        details = recent_security_events(conn)[0]["details"]
        assert "0699887766" not in details
        assert "ali@exemple.ma" not in details

    def test_control_characters_are_removed_from_details(self, conn):
        log_security_event(conn, channel="whatsapp", sender=None, kind="test", details="a\x00b\u200bc\x1b[31m")
        assert recent_security_events(conn)[0]["details"] == "abc[31m"

    def test_none_details(self, conn):
        log_security_event(conn, channel="whatsapp", sender=None, kind="test", details=None)
        assert recent_security_events(conn)[0]["details"] == ""

    @pytest.mark.parametrize("score, expected", [(150, 100), (-5, 0), (42, 42), ("70", 70), ("abc", 0), (None, 0)])
    def test_score_is_clamped(self, conn, score, expected):
        log_security_event(conn, channel="web", sender=None, kind="test", score=score)
        assert recent_security_events(conn)[0]["score"] == expected

    def test_channel_and_kind_are_cleaned(self, conn):
        log_security_event(conn, channel="  ", sender=None, kind="k" * 200)
        event = recent_security_events(conn)[0]
        assert event["channel"] == "inconnu"
        assert event["kind"] == "k" * LABEL_MAX_CHARS

    def test_values_are_stored_literally(self, conn):
        injection = "x'); DROP TABLE security_events; --"
        log_security_event(conn, channel=injection, sender=injection, kind=injection, details=injection)
        assert len(recent_security_events(conn)) == 1
        assert recent_security_events(conn)[0]["details"] == injection

    def test_aware_datetime_is_converted_to_utc(self, conn):
        moment = datetime(2026, 10, 24, 15, 30, 0, tzinfo=timezone(timedelta(hours=1)))
        log_security_event(conn, channel="web", sender=None, kind="test", created_at=moment)
        assert recent_security_events(conn)[0]["created_at"] == "2026-10-24 14:30:00"

    def test_naive_datetime_is_stored_as_is(self, conn):
        log_security_event(conn, channel="web", sender=None, kind="test", created_at=datetime(2026, 1, 2, 3, 4, 5))
        assert recent_security_events(conn)[0]["created_at"] == "2026-01-02 03:04:05"

    def test_default_timestamp_format(self, conn):
        log_security_event(conn, channel="web", sender=None, kind="test")
        created_at = recent_security_events(conn)[0]["created_at"]
        assert datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")

    def test_database_error_is_logged_and_returns_none(self, caplog):
        connection = sqlite3.connect(":memory:")
        connection.close()
        with caplog.at_level(logging.ERROR, logger="tecpap.security.events"):
            assert log_security_event(connection, channel="web", sender=None, kind="test") is None
        assert "Impossible d'enregistrer" in caplog.text


class TestRecentSecurityEvents:
    """Lecture des derniers événements."""

    def _fill(self, conn, count=5):
        for index in range(count):
            kind = KIND_PROMPT_INJECTION if index % 2 == 0 else KIND_OUTPUT_BLOCKED
            channel = "whatsapp" if index < 3 else "email"
            log_security_event(conn, channel=channel, sender=f"+21261234567{index}", kind=kind, score=index * 10,
                               details=f"événement {index}")

    def test_newest_first_as_dicts(self, conn):
        self._fill(conn)
        events = recent_security_events(conn)
        assert [event["details"] for event in events] == [f"événement {index}" for index in range(4, -1, -1)]
        assert set(events[0]) == {"id", "created_at", "channel", "sender", "kind", "score", "details"}
        assert all(isinstance(event, dict) for event in events)

    def test_limit(self, conn):
        self._fill(conn)
        assert len(recent_security_events(conn, limit=2)) == 2
        assert len(recent_security_events(conn, limit=0)) == 1
        assert len(recent_security_events(conn, limit=-3)) == 1

    def test_limit_upper_bound(self, conn):
        conn.executemany(
            f"INSERT INTO {TABLE_NAME} (created_at, channel, kind) VALUES ('2026-01-01 00:00:00', 'web', 'test')",
            [()] * (MAX_LIMIT + 5),
        )
        assert len(recent_security_events(conn, limit=MAX_LIMIT * 10)) == MAX_LIMIT

    def test_filters(self, conn):
        self._fill(conn)
        assert {event["kind"] for event in recent_security_events(conn, kind=KIND_OUTPUT_BLOCKED)} == {KIND_OUTPUT_BLOCKED}
        assert len(recent_security_events(conn, kind=KIND_PROMPT_INJECTION)) == 3
        assert len(recent_security_events(conn, channel="email")) == 2
        assert len(recent_security_events(conn, kind=KIND_OUTPUT_BLOCKED, channel="email")) == 1

    def test_row_factory_of_the_application(self, conn):
        conn.row_factory = sqlite3.Row
        self._fill(conn, count=1)
        [event] = recent_security_events(conn)
        assert isinstance(event, dict)
        assert event["sender"] == "+2126•••••670"

    def test_missing_table_gives_an_empty_list(self):
        connection = sqlite3.connect(":memory:")
        assert recent_security_events(connection) == []
        assert count_security_events(connection) == 0


class TestCountSecurityEvents:
    """Comptage des événements."""

    def test_count_by_kind_and_date(self, conn):
        old = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recent = datetime(2026, 10, 1, tzinfo=timezone.utc)
        log_security_event(conn, channel="web", sender=None, kind=KIND_PROMPT_INJECTION, created_at=old)
        log_security_event(conn, channel="web", sender=None, kind=KIND_PROMPT_INJECTION, created_at=recent)
        log_security_event(conn, channel="web", sender=None, kind=KIND_OUTPUT_BLOCKED, created_at=recent)
        assert count_security_events(conn) == 3
        assert count_security_events(conn, kind=KIND_PROMPT_INJECTION) == 2
        assert count_security_events(conn, since=datetime(2026, 6, 1, tzinfo=timezone.utc)) == 2
        assert count_security_events(conn, kind=KIND_OUTPUT_BLOCKED, since=old) == 1
