"""Sauvegardes : noms uniques, créations simultanées, copie d'avant restauration, rotation, migration du schéma."""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tecpap.db import backup as backup_module
from tecpap.db.database import DatabaseManager

ROOT = Path(__file__).resolve().parent.parent
THREAD_TIMEOUT = 60


def _load_cli():
    spec = importlib.util.spec_from_file_location("backup_cli", ROOT / "scripts" / "backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def paths(tmp_path):
    """Base d'une table et dossier de sauvegardes vide."""
    db_path = tmp_path / "base.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute("INSERT INTO test (name) VALUES ('Données')")
    connection.commit()
    connection.close()
    backup_dir = tmp_path / "sauvegardes"
    backup_dir.mkdir()
    return {"db_path": str(db_path), "backup_dir": str(backup_dir)}


def _names(backup_dir: str) -> list[str]:
    return sorted(os.listdir(backup_dir))


def _open_gzip_database(path: str, copy: Path) -> sqlite3.Connection:
    """Décompresse une sauvegarde dans ``copy`` et l'ouvre."""
    with gzip.open(path, "rb") as f_in, open(copy, "wb") as f_out:
        f_out.write(f_in.read())
    return sqlite3.connect(copy)


class TestUniqueNames:
    def test_two_backups_in_the_same_second_have_their_own_files(self, paths, monkeypatch):
        moment = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(backup_module, "_utc_now", lambda: moment)
        first = backup_module.create_backup(compress=True, **paths)
        second = backup_module.create_backup(compress=True, **paths)
        assert first and second and first != second
        assert os.path.basename(first) == "backup_20261025_120000_000000.db.gz"
        assert len(backup_module.list_backups(paths["backup_dir"])) == 2

    def test_backups_created_at_the_same_time_by_several_threads_are_all_readable(self, paths, tmp_path):
        results: list[str | None] = []
        barrier = threading.Barrier(5, timeout=THREAD_TIMEOUT)

        def work() -> None:
            barrier.wait()
            results.append(backup_module.create_backup(compress=True, **paths))

        threads = [threading.Thread(target=work) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT)
            assert not thread.is_alive()

        assert len(results) == 5 and all(results) and len(set(results)) == 5
        for index, path in enumerate(results):
            connection = _open_gzip_database(path, tmp_path / f"copie{index}.db")
            assert connection.execute("SELECT name FROM test").fetchone() == ("Données",)
            connection.close()
        assert not [name for name in _names(paths["backup_dir"]) if name.startswith("temp_")]
        history = json.loads(Path(paths["backup_dir"], backup_module.METADATA_FILENAME).read_text(encoding="utf-8"))
        assert len(history) == 5

    def test_a_failed_backup_leaves_no_file_behind(self, paths, monkeypatch):
        def broken(source, destination):
            Path(destination).write_bytes(b"partiel")
            raise sqlite3.OperationalError("disque plein")

        monkeypatch.setattr(backup_module, "_sqlite_copy", broken)
        assert backup_module.create_backup(compress=True, **paths) is None
        assert _names(paths["backup_dir"]) == []

    def test_history_dates_are_utc_with_an_explicit_offset(self, paths):
        backup_module.create_backup(compress=False, **paths)
        history = json.loads(Path(paths["backup_dir"], backup_module.METADATA_FILENAME).read_text(encoding="utf-8"))
        moment = datetime.fromisoformat(history[0]["date"])
        assert moment.utcoffset() == timedelta(0)

    def test_export_date_is_utc(self, paths):
        path = backup_module.export_to_json("export_x.json", **paths)
        exported = json.loads(Path(path).read_text(encoding="utf-8"))
        assert datetime.fromisoformat(exported["exported_at"]).utcoffset() == timedelta(0)


class TestListing:
    def test_new_names_carry_microseconds_and_legacy_names_still_parse(self, paths):
        backup_dir = paths["backup_dir"]
        for name in ("backup_20250103_120000.db.gz", "backup_20250103_120000_000500.db.gz",
                     "backup_20250103_120000_000500_pre_restore.db.gz", "pre_restore_20250102_090000.db",
                     "backup_sans_date.db"):
            Path(backup_dir, name).write_bytes(b"x")
        listed = {item["filename"]: item for item in backup_module.list_backups(backup_dir)}
        assert listed["backup_20250103_120000_000500.db.gz"]["created_at"] == "2025-01-03T12:00:00.000500+00:00"
        assert listed["backup_20250103_120000_000500.db.gz"]["pre_restore"] is False
        assert listed["backup_20250103_120000_000500_pre_restore.db.gz"]["pre_restore"] is True
        assert listed["pre_restore_20250102_090000.db"]["pre_restore"] is True
        assert listed["pre_restore_20250102_090000.db"]["date"] == "02/01/2025 10:00:00"
        assert listed["backup_sans_date.db"]["date"] == "N/A" and listed["backup_sans_date.db"]["created_at"] is None

    def test_most_recent_first_whatever_the_name_prefix(self, paths):
        backup_dir = paths["backup_dir"]
        for name in ("backup_20250101_100000.db.gz", "pre_restore_20250103_100000.db",
                     "backup_20250102_100000_000001_pre_restore.db.gz", "backup_sans_date.db"):
            Path(backup_dir, name).write_bytes(b"x")
        order = [item["filename"] for item in backup_module.list_backups(backup_dir)]
        assert order == ["pre_restore_20250103_100000.db", "backup_20250102_100000_000001_pre_restore.db.gz",
                         "backup_20250101_100000.db.gz", "backup_sans_date.db"]


class TestPreRestoreCopy:
    def test_the_copy_is_compressed_listed_and_rotated(self, paths, tmp_path):
        first = backup_module.create_backup(compress=True, **paths)
        for _ in range(3):
            assert backup_module.restore_backup(os.path.basename(first), **paths) is True
        listed = backup_module.list_backups(paths["backup_dir"])
        copies = [item for item in listed if item["pre_restore"]]
        assert len(copies) == 3 and all(item["compressed"] for item in copies)
        assert not [name for name in _names(paths["backup_dir"]) if name.startswith("pre_restore_")]

        deleted = backup_module.delete_old_backups(keep_count=2, backup_dir=paths["backup_dir"])
        assert deleted == 2
        assert len(backup_module.list_backups(paths["backup_dir"])) == 2

    def test_legacy_uncompressed_copies_are_rotated_too(self, paths):
        for day in (1, 2, 3):
            Path(paths["backup_dir"], f"pre_restore_2025010{day}_120000.db").write_bytes(b"x")
        Path(paths["backup_dir"], "backup_20250104_120000.db.gz").write_bytes(b"x")
        assert backup_module.delete_old_backups(keep_count=1, backup_dir=paths["backup_dir"]) == 3
        assert _names(paths["backup_dir"]) == ["backup_20250104_120000.db.gz"]

    def test_a_failed_restore_keeps_the_current_database(self, paths):
        broken = Path(paths["backup_dir"], "backup_20250101_000000.db.gz")
        broken.write_bytes(b"pas du gzip")
        assert backup_module.restore_backup(broken.name, **paths) is False
        connection = sqlite3.connect(paths["db_path"])
        assert connection.execute("SELECT name FROM test").fetchone() == ("Données",)
        connection.close()
        assert [name for name in _names(paths["backup_dir"]) if "pre_restore" in name or name.startswith("restore_")] == []


class TestRestoreMigratesTheSchema:
    @pytest.fixture
    def old_backup(self, tmp_path):
        """Sauvegarde d'une base sans les colonnes de traçabilité des versions récentes."""
        source = tmp_path / "ancienne.db"
        db = DatabaseManager(str(source))
        db.connect()
        db.init_database()
        db.create_order({"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 10,
                         "message_sid": "<ancien@x>"})
        db.connection.execute("DROP INDEX idx_commandes_message_sid")
        for column in ("message_sid", "message_brut", "langue", "modele_llm", "routage_modele", "anomalies",
                       "champs_manquants"):
            db.connection.execute(f"ALTER TABLE commandes DROP COLUMN {column}")
        db.connection.commit()
        db.close_all()
        backup_dir = tmp_path / "sauvegardes"
        path = backup_module.create_backup(compress=True, db_path=str(source), backup_dir=str(backup_dir))
        return {"filename": os.path.basename(path), "backup_dir": str(backup_dir)}

    def test_restored_database_accepts_orders_and_message_lookups(self, old_backup, tmp_path):
        live = tmp_path / "vivante.db"
        manager = DatabaseManager(str(live))
        manager.connect()
        manager.init_database()
        assert backup_module.restore_backup(old_backup["filename"], db_path=str(live),
                                            backup_dir=old_backup["backup_dir"]) is True
        assert manager.connect() is True
        assert manager.is_message_processed("<nouveau@x>") is False
        order_id = manager.create_order({"entreprise_cliente": "Biscuiterie Rif", "quantite": 5,
                                         "message_sid": "<nouveau@x>"})
        assert manager.is_message_processed("<nouveau@x>") is True
        assert manager.create_order({"entreprise_cliente": "Biscuiterie Rif", "quantite": 5,
                                     "message_sid": "<nouveau@x>"}) == order_id
        assert [row["entreprise_cliente"] if "entreprise_cliente" in row.keys() else row["client_nom"]
                for row in manager.get_all_orders()].count("Snack Atlas") == 1
        manager.close_all()

    def test_the_backup_file_itself_is_not_modified(self, old_backup, tmp_path):
        path = Path(old_backup["backup_dir"], old_backup["filename"])
        before = path.read_bytes()
        live = tmp_path / "vivante.db"
        DatabaseManager(str(live)).init_database()
        assert backup_module.restore_backup(old_backup["filename"], db_path=str(live),
                                            backup_dir=old_backup["backup_dir"]) is True
        assert path.read_bytes() == before

    def test_an_uncompressed_backup_is_migrated_on_a_copy(self, tmp_path):
        source = tmp_path / "ancienne.db"
        db = DatabaseManager(str(source))
        db.connect()
        db.init_database()
        db.connection.execute("DROP INDEX idx_commandes_message_sid")
        db.connection.execute("ALTER TABLE commandes DROP COLUMN message_sid")
        db.connection.commit()
        db.close_all()
        backup_dir = tmp_path / "sauvegardes"
        path = backup_module.create_backup(compress=False, db_path=str(source), backup_dir=str(backup_dir))
        live = tmp_path / "vivante.db"
        DatabaseManager(str(live)).init_database()
        assert backup_module.restore_backup(os.path.basename(path), db_path=str(live),
                                            backup_dir=str(backup_dir)) is True
        saved = sqlite3.connect(path)
        assert "message_sid" not in {row[1] for row in saved.execute("PRAGMA table_info(commandes)")}
        saved.close()
        restored = sqlite3.connect(live)
        assert "message_sid" in {row[1] for row in restored.execute("PRAGMA table_info(commandes)")}
        restored.close()


class TestRetention:
    @pytest.mark.parametrize("keep", [0, -1, -5])
    def test_keeping_less_than_one_backup_is_refused(self, paths, keep):
        for day in range(1, 4):
            Path(paths["backup_dir"], f"backup_2025010{day}_120000.db.gz").write_bytes(b"x")
        with pytest.raises(ValueError, match="au moins 1"):
            backup_module.delete_old_backups(keep_count=keep, backup_dir=paths["backup_dir"])
        assert len(backup_module.list_backups(paths["backup_dir"])) == 3

    @pytest.mark.parametrize("keep", [True, 2.5, "3", None])
    def test_other_types_are_refused(self, paths, keep):
        with pytest.raises(ValueError):
            backup_module.delete_old_backups(keep_count=keep, backup_dir=paths["backup_dir"])

    def test_old_json_exports_are_rotated(self, paths):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
        for index in range(5):
            path = Path(paths["backup_dir"], f"export_2026010{index}_000000.json")
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (base + index * 60, base + index * 60))
        Path(paths["backup_dir"], "backup_20250101_120000.db.gz").write_bytes(b"x")
        assert backup_module.delete_old_backups(keep_count=5, backup_dir=paths["backup_dir"]) == 2
        remaining = [name for name in _names(paths["backup_dir"]) if name.startswith("export_")]
        assert remaining == ["export_20260102_000000.json", "export_20260103_000000.json",
                             "export_20260104_000000.json"]
        assert backup_module.delete_old_exports(keep_count=1, backup_dir=paths["backup_dir"]) == 2


class TestCommandLine:
    @pytest.fixture
    def cli(self, paths, monkeypatch):
        monkeypatch.setenv("DATABASE_PATH", paths["db_path"])
        monkeypatch.setenv("BACKUP_DIR", paths["backup_dir"])
        monkeypatch.setenv("AUTH_ENABLED", "false")
        return _load_cli()

    @pytest.fixture
    def many(self, paths):
        for day in range(1, 11):
            Path(paths["backup_dir"], f"backup_202501{day:02d}_120000.db.gz").write_bytes(b"x")
        return paths

    @pytest.mark.parametrize("value", ["0", "-1", "-2"])
    def test_clean_refuses_a_count_below_one(self, cli, many, value, capsys):
        assert cli.main(["clean", value]) == 1
        assert "au moins 1" in capsys.readouterr().out
        assert len(backup_module.list_backups(many["backup_dir"])) == 10

    def test_clean_refuses_a_count_that_is_not_an_integer(self, cli, many, capsys):
        assert cli.main(["clean", "beaucoup"]) == 1
        assert len(backup_module.list_backups(many["backup_dir"])) == 10

    def test_clean_asks_for_confirmation_above_the_threshold(self, cli, many, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda prompt="": "non")
        assert cli.main(["clean", "2"]) == 0
        assert "annulée" in capsys.readouterr().out
        assert len(backup_module.list_backups(many["backup_dir"])) == 10

        monkeypatch.setattr("builtins.input", lambda prompt="": "oui")
        assert cli.main(["clean", "2"]) == 0
        assert len(backup_module.list_backups(many["backup_dir"])) == 2

    def test_clean_without_a_terminal_deletes_nothing_unless_confirmed_by_the_option(self, cli, many, monkeypatch):
        def no_terminal(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", no_terminal)
        assert cli.main(["clean", "2"]) == 0
        assert len(backup_module.list_backups(many["backup_dir"])) == 10
        assert cli.main(["clean", "2", "--yes"]) == 0
        assert len(backup_module.list_backups(many["backup_dir"])) == 2

    def test_a_small_deletion_needs_no_confirmation(self, cli, many, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("confirmation inutile"))
        assert cli.main(["clean", "8"]) == 0
        assert len(backup_module.list_backups(many["backup_dir"])) == 8

    def test_default_count_is_ten(self, cli, many):
        assert cli.main(["clean"]) == 0
        assert len(backup_module.list_backups(many["backup_dir"])) == 10

    def test_export_file_name_uses_utc(self, cli, paths):
        assert cli.main(["export"]) == 0
        exports = [name for name in _names(paths["backup_dir"]) if name.startswith("export_")]
        assert len(exports) == 1
