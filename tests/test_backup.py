"""Tests du module tecpap.db.backup : sauvegarde, restauration, nettoyage, statistiques, export JSON.

Chaque test travaille sur une base et un dossier de sauvegardes temporaires, passés
explicitement aux fonctions (paramètres ``db_path`` et ``backup_dir``).
"""

import gzip
import json
import os
import sqlite3

import pytest

from tecpap.db import backup as backup_module
from tecpap.db.database import DatabaseManager


def _create_db(path, statements):
    conn = sqlite3.connect(str(path))
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


@pytest.fixture
def paths(tmp_path):
    """Base de test (une table, une ligne) et dossier de sauvegardes vide."""
    db_path = tmp_path / "test_orders.db"
    _create_db(db_path, [
        "CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)",
        "INSERT INTO test (name) VALUES ('Test Data')",
    ])
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    return {"db_path": str(db_path), "backup_dir": str(backup_dir)}


class TestBackupCreation:
    """Création de sauvegardes."""

    def test_create_compressed_backup(self, paths):
        backup_path = backup_module.create_backup(compress=True, **paths)
        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert backup_path.endswith(".db.gz")

    def test_create_uncompressed_backup(self, paths):
        backup_path = backup_module.create_backup(compress=False, **paths)
        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert backup_path.endswith(".db")

    def test_backup_file_is_valid_gzip(self, paths):
        backup_path = backup_module.create_backup(compress=True, **paths)
        with gzip.open(backup_path, "rb") as f:
            assert len(f.read()) > 0

    def test_backup_contains_data(self, paths):
        backup_path = backup_module.create_backup(compress=False, **paths)
        conn = sqlite3.connect(backup_path)
        result = conn.execute("SELECT name FROM test WHERE id = 1").fetchone()
        conn.close()
        assert result == ("Test Data",)

    def test_backup_filename_format(self, paths):
        filename = os.path.basename(backup_module.create_backup(compress=True, **paths))
        assert filename.startswith("backup_")
        assert "_" in filename

    def test_metadata_saved(self, paths):
        backup_module.create_backup(compress=True, **paths)
        metadata_path = os.path.join(paths["backup_dir"], "backup_history.json")
        assert os.path.exists(metadata_path)
        with open(metadata_path, encoding="utf-8") as f:
            assert json.load(f)[0]["db_path"] == paths["db_path"]

    def test_missing_database_returns_none(self, tmp_path):
        result = backup_module.create_backup(db_path=str(tmp_path / "absente.db"), backup_dir=str(tmp_path / "b"))
        assert result is None

    def test_backup_includes_uncheckpointed_wal_writes(self, tmp_path):
        """Une écriture encore dans le journal WAL figure dans la sauvegarde."""
        db = DatabaseManager(str(tmp_path / "wal.db"))
        db.connect()
        db.init_database()
        db.get_or_create_client("Client WAL")
        backup_path = backup_module.create_backup(compress=False, db_path=db.db_file,
                                                  backup_dir=str(tmp_path / "backups"))
        db.disconnect()

        conn = sqlite3.connect(backup_path)
        names = [row[0] for row in conn.execute("SELECT nom FROM clients")]
        conn.close()
        assert "Client WAL" in names


class TestBackupRestore:
    """Restauration de sauvegardes."""

    @pytest.fixture
    def with_backup(self, paths):
        """Sauvegarde de la base puis modification de la base d'origine."""
        backup_path = backup_module.create_backup(compress=True, **paths)
        conn = sqlite3.connect(paths["db_path"])
        conn.execute("UPDATE test SET name = 'Modified'")
        conn.commit()
        conn.close()
        return dict(paths, backup_filename=os.path.basename(backup_path))

    def _restore(self, setup):
        return backup_module.restore_backup(
            setup["backup_filename"], db_path=setup["db_path"], backup_dir=setup["backup_dir"]
        )

    def test_restore_backup(self, with_backup):
        assert self._restore(with_backup) is True

    def test_restore_recovers_data(self, with_backup):
        self._restore(with_backup)
        conn = sqlite3.connect(with_backup["db_path"])
        result = conn.execute("SELECT name FROM test WHERE id = 1").fetchone()
        conn.close()
        assert result[0] == "Test Data"

    def test_restore_creates_pre_restore_backup(self, with_backup):
        self._restore(with_backup)
        pre_restore = [f for f in os.listdir(with_backup["backup_dir"]) if f.endswith("_pre_restore.db.gz")]
        assert len(pre_restore) == 1
        # La copie contient la base telle qu'elle était avant la restauration.
        with gzip.open(os.path.join(with_backup["backup_dir"], pre_restore[0]), "rb") as f_in:
            content = f_in.read()
        copy = os.path.join(with_backup["backup_dir"], "lecture.db")
        with open(copy, "wb") as f_out:
            f_out.write(content)
        conn = sqlite3.connect(copy)
        assert conn.execute("SELECT name FROM test").fetchone() == ("Modified",)
        conn.close()

    def test_restore_leaves_no_temporary_file(self, with_backup):
        self._restore(with_backup)
        assert not [f for f in os.listdir(with_backup["backup_dir"]) if f.startswith("restore_")]

    def test_restore_nonexistent_file(self, paths):
        assert backup_module.restore_backup("nonexistent_backup.db.gz", **paths) is False

    @pytest.mark.parametrize("filename", ["../orders.db", "sous/dossier.db", "..\\orders.db", ""])
    def test_restore_rejects_paths(self, paths, filename):
        assert backup_module.restore_backup(filename, **paths) is False

    def test_restore_rejects_corrupted_backup(self, paths):
        corrupted = os.path.join(paths["backup_dir"], "backup_20250101_000000.db")
        with open(corrupted, "wb") as f:
            f.write(b"ceci n'est pas une base SQLite")
        assert backup_module.restore_backup("backup_20250101_000000.db", **paths) is False
        conn = sqlite3.connect(paths["db_path"])
        assert conn.execute("SELECT name FROM test").fetchone() == ("Test Data",)
        conn.close()


class TestBackupList:
    """Liste des sauvegardes."""

    @pytest.fixture
    def with_backups(self, paths):
        for index in range(3):
            filename = f"backup_2025010{index + 1}_120000.db.gz"
            with gzip.open(os.path.join(paths["backup_dir"], filename), "wb") as f:
                f.write(b"donnees")
        return paths

    def test_list_backups(self, with_backups):
        assert len(backup_module.list_backups(with_backups["backup_dir"])) == 3

    def test_list_backups_sorted_by_date(self, with_backups):
        names = [b["filename"] for b in backup_module.list_backups(with_backups["backup_dir"])]
        assert names == sorted(names, reverse=True)

    def test_list_backup_contains_required_fields(self, with_backups):
        for backup in backup_module.list_backups(with_backups["backup_dir"]):
            assert {"filename", "size", "compressed", "date", "path"} <= set(backup)

    def test_list_backup_date_is_formatted(self, with_backups):
        # Le nom du fichier porte l'heure UTC ; la date affichée est celle de Casablanca (UTC+1 en janvier).
        newest = backup_module.list_backups(with_backups["backup_dir"])[0]
        assert newest["date"] == "03/01/2025 13:00:00"
        assert newest["created_at"] == "2025-01-03T12:00:00+00:00"

    def test_list_ignores_other_files(self, with_backups):
        open(os.path.join(with_backups["backup_dir"], "notes.txt"), "w").close()
        assert len(backup_module.list_backups(with_backups["backup_dir"])) == 3


class TestBackupCleanup:
    """Suppression des anciennes sauvegardes."""

    @pytest.fixture
    def many_backups(self, paths):
        for day in range(1, 16):
            filename = f"backup_202501{day:02d}_120000.db.gz"
            with gzip.open(os.path.join(paths["backup_dir"], filename), "wb") as f:
                f.write(b"donnees")
        return paths

    def test_delete_old_backups(self, many_backups):
        deleted = backup_module.delete_old_backups(keep_count=5, backup_dir=many_backups["backup_dir"])
        assert deleted == 10
        assert len(backup_module.list_backups(many_backups["backup_dir"])) == 5

    def test_keeps_newest_backups(self, many_backups):
        newest = [b["filename"] for b in backup_module.list_backups(many_backups["backup_dir"])[:5]]
        backup_module.delete_old_backups(keep_count=5, backup_dir=many_backups["backup_dir"])
        remaining = [b["filename"] for b in backup_module.list_backups(many_backups["backup_dir"])]
        assert remaining == newest

    def test_nothing_deleted_under_limit(self, many_backups):
        assert backup_module.delete_old_backups(keep_count=50, backup_dir=many_backups["backup_dir"]) == 0


class TestDatabaseStats:
    """Statistiques de la base."""

    @pytest.fixture
    def db_with_data(self, tmp_path):
        db_path = tmp_path / "test_orders.db"
        statements = ["CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)",
                      "CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL)"]
        statements += [f"INSERT INTO users (name) VALUES ('User {i}')" for i in range(10)]
        statements += [f"INSERT INTO orders (total) VALUES ({i * 10.5})" for i in range(25)]
        _create_db(db_path, statements)
        return str(db_path)

    def test_get_db_stats(self, db_with_data):
        stats = backup_module.get_db_stats(db_with_data)
        assert stats is not None
        assert "size" in stats
        assert "tables" in stats

    def test_stats_table_counts(self, db_with_data):
        stats = backup_module.get_db_stats(db_with_data)
        assert stats["tables"]["users"] == 10
        assert stats["tables"]["orders"] == 25

    def test_stats_missing_database(self, tmp_path):
        assert backup_module.get_db_stats(str(tmp_path / "absente.db")) is None


class TestJsonExport:
    """Export JSON de la base."""

    @pytest.fixture
    def export_paths(self, tmp_path):
        db_path = tmp_path / "test_orders.db"
        _create_db(db_path, [
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)",
            "INSERT INTO items (name) VALUES ('Item A')",
            "INSERT INTO items (name) VALUES ('Item B')",
        ])
        return {"db_path": str(db_path), "backup_dir": str(tmp_path / "backups")}

    def test_export_to_json(self, export_paths):
        export_path = backup_module.export_to_json("test_export.json", **export_paths)
        assert export_path is not None
        assert os.path.exists(export_path)

    def test_export_json_valid_format(self, export_paths):
        with open(backup_module.export_to_json("test_export.json", **export_paths), encoding="utf-8") as f:
            data = json.load(f)
        assert "exported_at" in data
        assert "tables" in data

    def test_export_json_contains_data(self, export_paths):
        with open(backup_module.export_to_json("test_export.json", **export_paths), encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["tables"]["items"]) == 2

    def test_export_rejects_path_in_filename(self, export_paths):
        assert backup_module.export_to_json("../sortie.json", **export_paths) is None


class TestDefaultPaths:
    """Chemins par défaut lus dans la configuration."""

    def test_defaults_come_from_environment(self, paths, monkeypatch):
        monkeypatch.setenv("DATABASE_PATH", paths["db_path"])
        monkeypatch.setenv("BACKUP_DIR", paths["backup_dir"])
        backup_path = backup_module.create_backup(compress=False)
        assert os.path.dirname(backup_path) == paths["backup_dir"]
        assert backup_module.list_backups()[0]["path"] == backup_path


class TestFormatSize:
    """Formatage des tailles."""

    def test_format_bytes(self):
        assert "B" in backup_module.format_size(500)

    def test_format_kilobytes(self):
        assert "KB" in backup_module.format_size(2048)

    def test_format_megabytes(self):
        assert "MB" in backup_module.format_size(5 * 1024 * 1024)

    def test_format_gigabytes(self):
        assert "GB" in backup_module.format_size(2 * 1024 * 1024 * 1024)


class TestSafeFilename:
    """Contrôle des noms de fichiers de sauvegarde."""

    @pytest.mark.parametrize("name", ["backup_20250101_120000.db.gz", "pre_restore_x.db"])
    def test_simple_names_are_accepted(self, name):
        assert backup_module.is_safe_backup_filename(name)

    @pytest.mark.parametrize("name", ["", "..", "../x.db", "a/b.db", "a\\b.db"])
    def test_paths_are_refused(self, name):
        assert not backup_module.is_safe_backup_filename(name)
