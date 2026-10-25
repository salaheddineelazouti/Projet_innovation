"""Base des commandes : dédoublonnage, code client, horodatages en UTC, position de lecture, migration."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tecpap.db.database import (
    SQLITE_TIMESTAMP_FORMAT,
    DatabaseManager,
    MailboxState,
    default_client_code,
    migrate_schema,
    utc_timestamp,
)
from tecpap.extraction.rules import ANOMALY_PENALTY
from tecpap.formatting import resolve_timezone

SQLITE_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _order(client: str, **fields) -> dict:
    return {"entreprise_cliente": client, "type_produit": "Sachets fond plat", "quantite": 100, **fields}


class TestOrderNumberIsNotAnIdentity:
    def test_two_clients_with_the_same_order_number_get_two_orders(self, temp_db):
        first, created_first = temp_db.save_order(_order("Snack Intrus", numero_commande="BC-2025-118", quantite=10,
                                                        email_from="intrus@exemple.ma", message_sid="<a@x>"))
        second, created_second = temp_db.save_order(_order("Boulangerie Atlas", numero_commande="BC-2025-118",
                                                          quantite=48000, email_from="achats@atlas.ma",
                                                          message_sid="<b@x>", confiance=90))
        assert (created_first, created_second) == (True, True)
        assert first != second
        stored = temp_db.get_order(second)
        assert stored["quantite"] == 48000
        assert stored["numero_commande"] is None
        assert "BC-2025-118" in stored["informations_supplementaires"]
        assert any("BC-2025-118" in item and f"CMD-{first}" in item for item in stored["anomalies_list"])
        assert stored["confiance"] == 90 - ANOMALY_PENALTY
        assert temp_db.get_order(first)["numero_commande"] == "BC-2025-118"

    def test_the_same_client_reusing_its_number_gets_a_new_flagged_order(self, temp_db):
        first = temp_db.create_order(_order("Snack Atlas", numero_commande="001", message_sid="<1@x>"))
        second = temp_db.create_order(_order("Snack Atlas", numero_commande="001", message_sid="<2@x>"))
        assert second != first
        assert temp_db.get_order(second)["anomalies_list"]

    def test_the_same_message_is_never_stored_twice(self, temp_db):
        order = _order("Snack Atlas", numero_commande="001", message_sid="<1@x>")
        first, created = temp_db.save_order(order)
        again, created_again = temp_db.save_order(dict(order))
        assert (again, created, created_again) == (first, True, False)
        assert len(temp_db.get_all_orders()) == 1

    def test_create_order_returns_only_the_identifier(self, temp_db):
        order_id = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        assert isinstance(order_id, int)
        assert temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>")) == order_id

    def test_the_order_number_is_kept_when_it_is_free(self, temp_db):
        order_id = temp_db.create_order(_order("Snack Atlas", numero_commande="BC-7", message_sid="<1@x>"))
        stored = temp_db.get_order(order_id)
        assert stored["numero_commande"] == "BC-7" and stored["anomalies_list"] == []

    def test_a_reused_mailbox_sequence_number_does_not_merge_two_emails(self, temp_db):
        first = temp_db.create_order(_order("Snack Atlas", email_id="57", message_sid="<a@mail.gmail.com>"))
        second = temp_db.create_order(_order("Biscuiterie Rif", email_id="57", message_sid="<b@other.ma>"))
        assert second != first
        assert len(temp_db.get_all_orders()) == 2

    def test_without_message_identifier_the_email_id_still_deduplicates(self, temp_db):
        first = temp_db.create_order(_order("Snack Atlas", email_id="7:41"))
        assert temp_db.create_order(_order("Snack Atlas", email_id="7:41")) == first


class TestClientCodeComesFromTheDatabase:
    def test_a_code_stated_for_another_client_is_replaced_and_flagged(self, temp_db):
        atlas = temp_db.create_order(_order("Boulangerie Atlas", message_sid="<1@x>"))
        atlas_code = temp_db.get_order(atlas)["code_client"]
        assert atlas_code == default_client_code(temp_db.get_order(atlas)["client_id"])

        intruder = temp_db.create_order(_order("Snack Intrus", code_client=atlas_code, message_sid="<2@x>",
                                               confiance=80))
        stored = temp_db.get_order(intruder)
        assert stored["code_client"] != atlas_code
        assert stored["code_client"] == default_client_code(stored["client_id"])
        assert any(atlas_code in item and stored["code_client"] in item for item in stored["anomalies_list"])
        assert atlas_code in stored["informations_supplementaires"]
        assert stored["confiance"] == 80 - ANOMALY_PENALTY

    def test_the_clients_own_code_raises_no_anomaly(self, temp_db):
        first = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        code = temp_db.get_order(first)["code_client"]
        second = temp_db.create_order(_order("Snack Atlas", code_client=f" {code.lower()} ", message_sid="<2@x>"))
        stored = temp_db.get_order(second)
        assert stored["code_client"] == code and stored["anomalies_list"] == []

    def test_the_code_of_the_last_validated_order_of_the_client_is_kept(self, temp_db):
        first = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        temp_db.connection.execute("UPDATE commandes SET code_client = 'SAGE-ATLAS' WHERE id = ?", (first,))
        temp_db.connection.commit()
        temp_db.update_order_status(first, "validee")
        second = temp_db.create_order(_order("Snack Atlas", message_sid="<2@x>"))
        stored = temp_db.get_order(second)
        assert stored["code_client"] == "SAGE-ATLAS" and stored["anomalies_list"] == []

    def test_an_unvalidated_code_is_not_trusted(self, temp_db):
        first = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        temp_db.connection.execute("UPDATE commandes SET code_client = 'FAUX' WHERE id = ?", (first,))
        temp_db.connection.commit()
        second = temp_db.create_order(_order("Snack Atlas", message_sid="<2@x>"))
        stored = temp_db.get_order(second)
        assert stored["code_client"] == default_client_code(stored["client_id"])


class TestTimestampsAreInUtc:
    def test_validation_date_is_written_in_utc_with_the_sqlite_format(self, temp_db):
        order_id = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        before = datetime.now(timezone.utc).replace(microsecond=0)
        temp_db.update_order_status(order_id, "validee", validated_by="admin")
        after = datetime.now(timezone.utc)
        stored = temp_db.get_order(order_id)["validated_at"]
        assert SQLITE_TIMESTAMP.match(stored)
        moment = datetime.strptime(stored, SQLITE_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
        assert before <= moment <= after

    def test_creation_and_validation_dates_share_one_format_and_time_scale(self, temp_db):
        order_id = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        temp_db.update_order_status(order_id, "validee")
        stored = temp_db.get_order(order_id)
        created = datetime.strptime(stored["created_at"], SQLITE_TIMESTAMP_FORMAT)
        validated = datetime.strptime(stored["validated_at"], SQLITE_TIMESTAMP_FORMAT)
        assert timedelta(0) <= validated - created < timedelta(minutes=1)

    def test_utc_timestamp_converts_an_aware_moment(self):
        moment = datetime(2026, 10, 26, 0, 30, tzinfo=timezone(timedelta(hours=1)))
        assert utc_timestamp(moment) == "2026-10-25 23:30:00"

    def test_legacy_local_validation_dates_are_converted_when_the_schema_is_updated(self, temp_db):
        order_id = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        temp_db.connection.execute("UPDATE commandes SET statut = 'validee', validated_at = ? WHERE id = ?",
                                   ("2026-10-25T03:56:13.161007", order_id))
        temp_db.connection.commit()
        temp_db.init_database()
        # Une valeur sans fuseau horaire est une heure locale du serveur.
        expected = datetime(2026, 10, 25, 3, 56, 13, 161007).astimezone(timezone.utc).strftime(SQLITE_TIMESTAMP_FORMAT)
        assert temp_db.get_order(order_id)["validated_at"] == expected

    def test_trend_groups_orders_by_day_of_the_requested_time_zone(self, temp_db):
        # « Etc/GMT-1 » désigne UTC+1 toute l'année (l'heure de Casablanca change selon les années).
        zone = resolve_timezone("Etc/GMT-1")
        today = datetime.now(timezone.utc).astimezone(zone).date()
        local_midnight = datetime(today.year, today.month, today.day, tzinfo=zone)
        # Une commande reçue à 00 h 30 en UTC+1 est encore la veille en UTC.
        created = (local_midnight + timedelta(minutes=30)).astimezone(timezone.utc)
        assert created.date() == today - timedelta(days=1)
        order_id = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        temp_db.connection.execute("UPDATE commandes SET created_at = ? WHERE id = ?",
                                   (created.strftime(SQLITE_TIMESTAMP_FORMAT), order_id))
        temp_db.connection.commit()
        assert temp_db.get_orders_trend(7, timezone_name="Etc/GMT-1") == [{"date": today.isoformat(), "count": 1}]
        assert temp_db.get_orders_trend(7, timezone_name="UTC") == [
            {"date": created.date().isoformat(), "count": 1}]

    def test_trend_uses_the_company_time_zone_by_default(self, temp_db):
        order_id = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        zone = resolve_timezone()
        stored = datetime.strptime(temp_db.get_order(order_id)["created_at"], SQLITE_TIMESTAMP_FORMAT)
        expected = stored.replace(tzinfo=timezone.utc).astimezone(zone).date().isoformat()
        assert temp_db.get_orders_trend() == [{"date": expected, "count": 1}]

    def test_trend_ignores_orders_older_than_the_period(self, temp_db):
        old = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>"))
        temp_db.connection.execute("UPDATE commandes SET created_at = '2020-01-01 10:00:00' WHERE id = ?", (old,))
        temp_db.connection.commit()
        assert temp_db.get_orders_trend(7) == []


class TestMailboxState:
    def test_unknown_mailbox_has_no_state(self, temp_db):
        assert temp_db.get_mailbox_state("commandes@tecpap.ma/INBOX") is None

    def test_state_only_moves_forward_for_one_uidvalidity(self, temp_db):
        temp_db.set_mailbox_state("m/INBOX", 7, 10)
        temp_db.set_mailbox_state("m/INBOX", 7, 4)
        assert temp_db.get_mailbox_state("m/INBOX") == MailboxState(7, 10)
        temp_db.set_mailbox_state("m/INBOX", 7, 12)
        assert temp_db.get_mailbox_state("m/INBOX") == MailboxState(7, 12)

    def test_a_new_uidvalidity_replaces_the_state(self, temp_db):
        temp_db.set_mailbox_state("m/INBOX", 7, 10)
        temp_db.set_mailbox_state("m/INBOX", 8, 3)
        assert temp_db.get_mailbox_state("m/INBOX") == MailboxState(8, 3)

    def test_each_mailbox_has_its_own_state(self, temp_db):
        temp_db.set_mailbox_state("a@x/INBOX", 7, 10)
        temp_db.set_mailbox_state("b@x/INBOX", 7, 2)
        assert temp_db.get_mailbox_state("a@x/INBOX").last_uid == 10
        assert temp_db.get_mailbox_state("b@x/INBOX").last_uid == 2


class TestSchemaMigration:
    @pytest.fixture
    def old_database(self, tmp_path):
        """Base d'une version antérieure : sans les colonnes de traçabilité, l'index ni la position de lecture."""
        path = tmp_path / "ancienne.db"
        db = DatabaseManager(str(path))
        db.connect()
        db.init_database()
        db.create_order(_order("Snack Atlas", message_sid="<1@x>", numero_commande="A-1"))
        db.connection.execute("DROP INDEX idx_commandes_message_sid")
        for column in ("message_sid", "message_brut", "langue", "modele_llm", "routage_modele", "anomalies",
                       "champs_manquants"):
            db.connection.execute(f"ALTER TABLE commandes DROP COLUMN {column}")
        db.connection.execute("DROP TABLE email_mailbox_state")
        db.connection.commit()
        db.close_all()
        return str(path)

    def test_migrate_schema_adds_what_the_current_version_needs(self, old_database):
        migrate_schema(old_database)
        connection = sqlite3.connect(old_database)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(commandes)")}
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(commandes)")}
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            kept = connection.execute("SELECT numero_commande FROM commandes").fetchall()
        finally:
            connection.close()
        assert {"message_sid", "message_brut", "anomalies", "champs_manquants"} <= columns
        assert "idx_commandes_message_sid" in indexes
        assert {"email_mailbox_state", "conversations", "security_events"} <= tables
        assert kept == [("A-1",)]

    def test_migrated_database_accepts_new_orders(self, old_database):
        migrate_schema(old_database)
        db = DatabaseManager(old_database)
        db.connect()
        try:
            order_id = db.create_order(_order("Biscuiterie Rif", message_sid="<9@x>"))
            assert db.is_message_processed("<9@x>") and order_id
        finally:
            db.close_all()

    def test_migrating_twice_changes_nothing(self, old_database):
        migrate_schema(old_database)
        migrate_schema(old_database)

    def test_migrate_schema_rejects_a_file_that_is_not_a_database(self, tmp_path):
        path = tmp_path / "pas_une_base.db"
        path.write_bytes(b"ceci n'est pas une base SQLite" * 20)
        with pytest.raises(sqlite3.DatabaseError):
            migrate_schema(str(path))


class TestStoredLists:
    def test_anomalies_are_stored_as_json_after_the_review(self, temp_db):
        order_id = temp_db.create_order(_order("Snack Atlas", message_sid="<1@x>", numero_commande="X-1",
                                               anomalies=["Date de livraison passée."]))
        again = temp_db.create_order(_order("Snack Atlas", message_sid="<2@x>", numero_commande="X-1",
                                            anomalies=["Date de livraison passée."]))
        raw = temp_db.connection.execute("SELECT anomalies FROM commandes WHERE id = ?", (again,)).fetchone()[0]
        items = json.loads(raw)
        assert items[0] == "Date de livraison passée." and len(items) == 2
        assert order_id != again
