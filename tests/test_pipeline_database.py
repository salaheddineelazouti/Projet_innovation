"""Base des commandes : connexions par fil d'exécution, traçabilité de l'extraction, rattachement du téléphone."""

from __future__ import annotations

import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tecpap.db import backup as backup_module
from tecpap.db.database import (
    ORDER_AUDIT_FIELDS,
    ORDER_UPDATABLE_FIELDS,
    DatabaseManager,
    OrderUpdateError,
    close_connections_to,
    decode_text_list,
    encode_text_list,
)

THREAD_TIMEOUT = 30
"""Durée maximale (secondes) d'attente d'un fil d'exécution de test."""


def join_thread(thread: threading.Thread) -> None:
    """Attend la fin d'un fil ; un fil encore actif après :data:`THREAD_TIMEOUT` fait échouer le test.

    Un verrou ou une connexion bloqué se voit alors dans le rapport du test, au lieu de suspendre
    toute l'exécution.
    """
    thread.join(timeout=THREAD_TIMEOUT)
    assert not thread.is_alive(), "le fil ne s'est pas terminé : verrou ou connexion bloqué"


AUDITED_ORDER = {
    "entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 3000,
    "source": "whatsapp", "whatsapp_from": "whatsapp:+212612345678", "message_sid": "SM-AUDIT-1",
    "message_brut": "salam, bghit 3000 sachet", "langue": "darija_latin", "modele_llm": "gpt-4o",
    "routage_modele": "Extraction de commande : modèle principal.",
    "anomalies": ["La date de livraison (01/01/2020) est déjà passée.", "Contenu suspect « très »"],
    "champs_manquants": ["entreprise_cliente"],
}


class TestThreadConnections:
    """Une connexion SQLite par fil d'exécution, avec les mêmes réglages."""

    def test_each_thread_has_its_own_connection(self, temp_db):
        main = temp_db.connection
        seen = []

        def read():
            connection = temp_db.connection
            seen.append(connection)
            return connection.execute("PRAGMA journal_mode").fetchone()[0], \
                connection.execute("PRAGMA busy_timeout").fetchone()[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            settings = list(pool.map(lambda _: read(), range(4)))
        assert all(connection is not main for connection in seen)
        assert len({id(connection) for connection in seen}) == 2
        assert settings == [("wal", 30000)] * 4
        assert temp_db.connection is main

    def test_concurrent_orders_and_statistics(self, temp_db):
        errors: list[BaseException] = []
        stats: list[dict] = []
        barrier = threading.Barrier(6, timeout=THREAD_TIMEOUT)

        def work(worker: int) -> None:
            try:
                barrier.wait()
                for index in range(15):
                    temp_db.create_order({"entreprise_cliente": f"Client {worker}", "quantite": index + 1,
                                          "message_sid": f"SM-{worker}-{index}", "source": "whatsapp"})
                    stats.append(temp_db.get_stats())
                    temp_db.get_all_orders()
            except BaseException as exc:  # l'erreur est vérifiée par le fil principal
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(worker,)) for worker in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            join_thread(thread)

        assert errors == []
        assert temp_db.get_stats()["total_orders"] == 90
        assert temp_db.get_stats()["total_clients"] == 6
        assert max(item["total_orders"] for item in stats) == 90

    def test_same_message_from_several_threads_creates_one_order(self, temp_db):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: temp_db.create_order(dict(AUDITED_ORDER)), range(8)))
        assert len(set(ids)) == 1
        assert len(temp_db.get_all_orders()) == 1

    def test_connections_of_finished_threads_are_closed(self, temp_db):
        def touch():
            temp_db.connection.execute("SELECT 1")

        for _ in range(5):
            thread = threading.Thread(target=touch)
            thread.start()
            join_thread(thread)
        # Chaque ouverture ferme d'abord les connexions des fils terminés : restent celle du fil
        # principal et celle du dernier fil.
        assert temp_db.open_connection_count() == 2

    def test_disconnect_only_closes_the_current_thread(self, temp_db):
        opened = threading.Event()
        release = threading.Event()
        results = []

        def hold():
            connection = temp_db.connection
            opened.set()
            release.wait(10)
            results.append(connection.execute("SELECT COUNT(*) FROM commandes").fetchone()[0])

        thread = threading.Thread(target=hold)
        thread.start()
        opened.wait(10)
        temp_db.disconnect()
        assert temp_db.connection is None
        release.set()
        join_thread(thread)
        assert results == [0]
        assert temp_db.connect() is True

    def test_close_all_closes_every_connection_until_connect(self, temp_db):
        other = []
        thread = threading.Thread(target=lambda: other.append(temp_db.connection))
        thread.start()
        join_thread(thread)
        main = temp_db.connection

        assert temp_db.close_all() == 2
        assert temp_db.connection is None
        with pytest.raises(sqlite3.ProgrammingError):
            main.execute("SELECT 1")
        with pytest.raises(sqlite3.ProgrammingError):
            other[0].execute("SELECT 1")

        assert temp_db.connect() is True
        assert temp_db.connection is not main
        assert temp_db.get_stats()["total_orders"] == 0

    def test_lazy_opening_needs_an_opened_manager(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "base.db"))
        assert db.connection is None
        db.init_database()
        result = []
        thread = threading.Thread(target=lambda: result.append(db.connection))
        thread.start()
        join_thread(thread)
        assert result[0] is not None
        db.close_all()

    def test_memory_database_is_shared_by_all_threads(self):
        db = DatabaseManager(":memory:")
        db.init_database()
        db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 1})
        counts = []
        thread = threading.Thread(target=lambda: counts.append(db.get_stats()["total_orders"]))
        thread.start()
        join_thread(thread)
        assert counts == [1]
        db.disconnect()
        assert db.connection is None

    def test_restore_closes_the_connections_and_the_manager_reopens(self, temp_db, tmp_path):
        backup_dir = str(tmp_path / "sauvegardes")
        temp_db.create_order({"entreprise_cliente": "Avant", "quantite": 1})
        path = backup_module.create_backup(compress=False, db_path=temp_db.db_file, backup_dir=backup_dir)
        temp_db.create_order({"entreprise_cliente": "Après", "quantite": 2})

        assert backup_module.restore_backup(os.path.basename(path), db_path=temp_db.db_file,
                                            backup_dir=backup_dir) is True
        assert temp_db.connection is None
        assert temp_db.connect() is True
        assert [order["client_nom"] for order in temp_db.get_all_orders()] == ["Avant"]

    def test_close_connections_to_targets_one_database(self, temp_db, tmp_path):
        other = DatabaseManager(str(tmp_path / "autre.db"))
        other.connect()
        assert close_connections_to(temp_db.db_file) == 1
        assert temp_db.connection is None
        assert other.connection is not None
        other.disconnect()


class TestAuditColumns:
    """Colonnes de traçabilité de l'extraction et déduplication par identifiant de message."""

    def test_columns_and_unique_index_exist(self, temp_db):
        columns = {row[1] for row in temp_db.connection.execute("PRAGMA table_info(commandes)")}
        assert set(ORDER_AUDIT_FIELDS) <= columns
        indexes = {row[1]: row[2] for row in temp_db.connection.execute("PRAGMA index_list(commandes)")}
        assert indexes["idx_commandes_message_sid"] == 1

    def test_related_tables_are_created(self, temp_db):
        tables = {row[0] for row in temp_db.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"security_events", "rag_chunks", "conversations", "chat_messages"} <= tables

    def test_old_database_is_migrated(self, tmp_path):
        path = tmp_path / "ancienne.db"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE commandes (id INTEGER PRIMARY KEY AUTOINCREMENT, numero_commande TEXT UNIQUE, "
                           "client_id INTEGER, produit_id INTEGER, nature_produit TEXT, quantite REAL, unite TEXT, "
                           "prix_unitaire REAL, prix_total REAL, devise TEXT, date_commande DATE, date_livraison DATE, "
                           "informations_supplementaires TEXT, confiance INTEGER, statut TEXT DEFAULT 'en_attente', "
                           "email_id TEXT, email_subject TEXT, email_from TEXT, created_at TIMESTAMP "
                           "DEFAULT CURRENT_TIMESTAMP, validated_at TIMESTAMP, validated_by TEXT)")
        connection.execute("INSERT INTO commandes (quantite, email_id) VALUES (5, 'ancien')")
        connection.commit()
        connection.close()

        db = DatabaseManager(str(path))
        db.init_database()
        db.init_database()
        order = db.get_all_orders()[0]
        assert order["message_sid"] is None and order["anomalies_list"] == []
        db.create_order(dict(AUDITED_ORDER))
        assert len(db.get_all_orders()) == 2
        db.close_all()

    def test_audit_fields_are_stored_and_decoded(self, temp_db):
        order = temp_db.get_order(temp_db.create_order(dict(AUDITED_ORDER)))
        for name in ("message_sid", "message_brut", "langue", "modele_llm", "routage_modele"):
            assert order[name] == AUDITED_ORDER[name]
        assert order["anomalies_list"] == AUDITED_ORDER["anomalies"]
        assert order["champs_manquants_list"] == ["entreprise_cliente"]
        # JSON sans échappement des caractères non ASCII.
        assert "très" in order["anomalies"] and "\\u" not in order["anomalies"]
        listed = temp_db.get_all_orders()[0]
        assert listed["anomalies_list"] == AUDITED_ORDER["anomalies"]

    def test_duplicate_message_sid_returns_the_existing_order(self, temp_db):
        first = temp_db.create_order(dict(AUDITED_ORDER))
        second = temp_db.create_order(dict(AUDITED_ORDER, quantite=9999, entreprise_cliente="Autre"))
        assert first == second
        assert temp_db.is_message_processed("SM-AUDIT-1") is True
        assert temp_db.find_order_by_message_sid(" SM-AUDIT-1 ") == first
        assert temp_db.is_message_processed("SM-INCONNU") is False
        assert temp_db.is_message_processed(None) is False
        assert len(temp_db.get_all_orders()) == 1

    def test_orders_without_message_sid_are_not_deduplicated(self, temp_db):
        temp_db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 1})
        temp_db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 2})
        assert len(temp_db.get_all_orders()) == 2

    def test_long_message_is_truncated(self, temp_db):
        order = temp_db.get_order(temp_db.create_order(dict(AUDITED_ORDER, message_brut="x" * 30_000)))
        assert len(order["message_brut"]) == 20_000

    @pytest.mark.parametrize("field", ORDER_AUDIT_FIELDS)
    def test_audit_fields_cannot_be_updated(self, temp_db, field):
        assert field not in ORDER_UPDATABLE_FIELDS
        order_id = temp_db.create_order(dict(AUDITED_ORDER))
        with pytest.raises(OrderUpdateError):
            temp_db.update_order(order_id, {field: "modifié"})

    @pytest.mark.parametrize("value, expected", [
        (None, None), ([], "[]"), (["a", "é"], '["a", "é"]'), (("a",), '["a"]'), ("texte", '["texte"]'),
        ('["déjà"]', '["déjà"]'), (["a", None, ""], '["a"]'),
    ])
    def test_encode_text_list(self, value, expected):
        assert encode_text_list(value) == expected

    @pytest.mark.parametrize("raw, expected", [
        (None, []), ("", []), ("[]", []), ('["a", "b"]', ["a", "b"]), ("pas du JSON", ["pas du JSON"]),
        ('{"a": 1}', ['{"a": 1}']),
    ])
    def test_decode_text_list(self, raw, expected):
        assert decode_text_list(raw) == expected


class TestClientPhone:
    """Le téléphone d'un expéditeur n'est rattaché à un client existant qu'après validation."""

    def test_existing_client_found_by_name_keeps_no_phone(self, temp_db):
        client = temp_db.get_or_create_client("Boulangerie Nour", email="achats@nour.ma")
        found = temp_db.get_or_create_client("boulangerie nour", telephone="+212699999999")
        assert found["id"] == client["id"]
        assert found["telephone"] is None
        assert temp_db.get_or_create_client("Boulangerie Nour")["telephone"] is None

    def test_new_client_gets_the_phone(self, temp_db):
        assert temp_db.get_or_create_client("Snack Atlas", telephone="+212612345678")["telephone"] == "+212612345678"

    def test_order_in_the_name_of_an_existing_client_does_not_attach_the_phone(self, temp_db):
        temp_db.get_or_create_client("Boulangerie Nour")
        temp_db.create_order({"entreprise_cliente": "Boulangerie Nour", "quantite": 10, "source": "whatsapp",
                              "whatsapp_from": "whatsapp:+212699999999"})
        assert temp_db.get_or_create_client("Boulangerie Nour")["telephone"] is None

    def test_attach_phone_to_client(self, temp_db):
        client = temp_db.get_or_create_client("Boulangerie Nour")
        assert temp_db.attach_phone_to_client(client["id"], "whatsapp:+212699999999") is True
        assert temp_db.get_or_create_client("Boulangerie Nour")["telephone"] == "+212699999999"
        assert temp_db.attach_phone_to_client(client["id"], "+212600000000") is False
        assert temp_db.get_or_create_client("Boulangerie Nour")["telephone"] == "+212699999999"

    @pytest.mark.parametrize("phone", [None, "", "whatsapp:", "   "])
    def test_attach_without_phone(self, temp_db, phone):
        client = temp_db.get_or_create_client("Boulangerie Nour")
        assert temp_db.attach_phone_to_client(client["id"], phone) is False

    def test_attach_to_unknown_client(self, temp_db):
        assert temp_db.attach_phone_to_client(999, "+212699999999") is False
