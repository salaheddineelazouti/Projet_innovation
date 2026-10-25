"""Sauvegarde et restauration de la base SQLite.

Les copies passent par l'API de sauvegarde de SQLite (``sqlite3.Connection.backup``) : la copie
est cohérente même si l'application écrit au même moment, et elle inclut les écritures encore
présentes dans le journal WAL. Une sauvegarde peut être compressée en gzip.

Chaque fonction accepte le chemin de la base (``db_path``) et le dossier des sauvegardes
(``backup_dir``) ; quand ils sont omis, ils sont lus dans la configuration
(``Settings.database_path`` et ``Settings.backup_dir``).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from typing import Any

from tecpap.config import load_settings
from tecpap.db.database import close_connections_to

logger = logging.getLogger(__name__)

METADATA_FILENAME = "backup_history.json"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


def _db_path(db_path: str | None) -> str:
    return db_path if db_path is not None else load_settings().database_path


def _backup_dir(backup_dir: str | None) -> str:
    return backup_dir if backup_dir is not None else load_settings().backup_dir


def is_safe_backup_filename(filename: str) -> bool:
    """Vrai si ``filename`` est un simple nom de fichier (ni chemin, ni remontée de dossier)."""
    return bool(filename) and ".." not in filename and "/" not in filename and "\\" not in filename


def ensure_backup_dir(backup_dir: str | None = None) -> str:
    """Crée le dossier des sauvegardes s'il n'existe pas et renvoie son chemin."""
    directory = _backup_dir(backup_dir)
    if not os.path.exists(directory):
        os.makedirs(directory)
        logger.info("Dossier de sauvegarde créé : %s", directory)
    return directory


def _sqlite_copy(source_path: str, destination_path: str) -> None:
    """Copie une base SQLite dans une autre avec l'API de sauvegarde de SQLite."""
    source = sqlite3.connect(source_path)
    try:
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def create_backup(compress: bool = True, db_path: str | None = None, backup_dir: str | None = None) -> str | None:
    """Crée une sauvegarde horodatée de la base.

    Args:
        compress: compresse la sauvegarde en gzip (``backup_AAAAMMJJ_HHMMSS.db.gz``).
        db_path: base à sauvegarder.
        backup_dir: dossier de destination.

    Returns:
        Le chemin du fichier créé, ou ``None`` si la base est introuvable ou en cas d'erreur.
    """
    db_path = _db_path(db_path)
    backup_dir = ensure_backup_dir(backup_dir)

    if not os.path.exists(db_path):
        logger.error("Base de données introuvable : %s", db_path)
        return None

    timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
    backup_filename = f"backup_{timestamp}.db.gz" if compress else f"backup_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_filename)

    try:
        if compress:
            temp_backup = os.path.join(backup_dir, f"temp_{timestamp}.db")
            try:
                _sqlite_copy(db_path, temp_backup)
                with open(temp_backup, "rb") as f_in, gzip.open(backup_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            finally:
                if os.path.exists(temp_backup):
                    os.remove(temp_backup)
        else:
            _sqlite_copy(db_path, backup_path)

        size = os.path.getsize(backup_path)
        logger.info("Sauvegarde créée : %s (%s)", backup_path, format_size(size))
        save_backup_metadata(backup_filename, size, db_path=db_path, backup_dir=backup_dir)
        return backup_path
    except (OSError, sqlite3.Error):
        logger.exception("Échec de la sauvegarde de %s", db_path)
        return None


def restore_backup(backup_filename: str, db_path: str | None = None, backup_dir: str | None = None) -> bool:
    """Restaure la base depuis une sauvegarde du dossier ``backup_dir``.

    La base actuelle est d'abord copiée dans ``pre_restore_AAAAMMJJ_HHMMSS.db`` pour pouvoir
    revenir en arrière. Les fichiers ``.db.gz`` sont décompressés avant la restauration. Juste
    avant la copie, toutes les connexions des gestionnaires ouverts sur cette base sont fermées
    (:func:`tecpap.db.database.close_connections_to`) : aucune lecture en cours ne porte sur
    l'ancienne base, et chaque gestionnaire reprend au prochain ``connect()``.

    Returns:
        ``True`` si la restauration a réussi.
    """
    if not is_safe_backup_filename(backup_filename):
        logger.error("Nom de sauvegarde refusé : %r", backup_filename)
        return False

    db_path = _db_path(db_path)
    backup_dir = _backup_dir(backup_dir)
    backup_path = os.path.join(backup_dir, backup_filename)

    if not os.path.exists(backup_path):
        logger.error("Sauvegarde introuvable : %s", backup_path)
        return False

    timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
    temp_restore = os.path.join(backup_dir, f"restore_{timestamp}.db")
    try:
        if os.path.exists(db_path):
            pre_restore = os.path.join(backup_dir, f"pre_restore_{timestamp}.db")
            _sqlite_copy(db_path, pre_restore)
            logger.info("Copie de la base avant restauration : %s", pre_restore)

        source = backup_path
        if backup_filename.endswith(".gz"):
            with gzip.open(backup_path, "rb") as f_in, open(temp_restore, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            source = temp_restore

        closed = close_connections_to(db_path)
        if closed:
            logger.info("%d connexion(s) à la base fermée(s) avant la restauration", closed)
        _sqlite_copy(source, db_path)
        logger.info("Base de données restaurée depuis %s", backup_filename)
        return True
    except (OSError, sqlite3.Error, EOFError):
        logger.exception("Échec de la restauration depuis %s", backup_filename)
        return False
    finally:
        if os.path.exists(temp_restore):
            os.remove(temp_restore)


def list_backups(backup_dir: str | None = None) -> list[dict[str, Any]]:
    """Liste les sauvegardes (``backup_*.db`` et ``backup_*.db.gz``), des plus récentes aux plus anciennes."""
    backup_dir = ensure_backup_dir(backup_dir)
    backups = []

    for filename in os.listdir(backup_dir):
        if not (filename.startswith("backup_") and (filename.endswith(".db") or filename.endswith(".db.gz"))):
            continue
        filepath = os.path.join(backup_dir, filename)
        stat = os.stat(filepath)

        date_str = filename.replace("backup_", "").replace(".db.gz", "").replace(".db", "")
        try:
            date_formatted = datetime.strptime(date_str, TIMESTAMP_FORMAT).strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            date_formatted = "N/A"

        backups.append({
            "filename": filename,
            "path": filepath,
            "size": stat.st_size,
            "size_formatted": format_size(stat.st_size),
            "date": date_formatted,
            "compressed": filename.endswith(".gz"),
        })

    backups.sort(key=lambda backup: backup["filename"], reverse=True)
    return backups


def delete_old_backups(keep_count: int = 10, backup_dir: str | None = None) -> int:
    """Supprime les sauvegardes les plus anciennes pour n'en garder que ``keep_count``.

    Returns:
        Le nombre de fichiers supprimés.
    """
    backups = list_backups(backup_dir)
    if len(backups) <= keep_count:
        logger.info("%d sauvegarde(s), limite %d : aucune suppression", len(backups), keep_count)
        return 0

    deleted_count = 0
    for backup in backups[keep_count:]:
        try:
            os.remove(backup["path"])
            deleted_count += 1
            logger.info("Sauvegarde supprimée : %s", backup["filename"])
        except OSError:
            logger.exception("Échec de la suppression de %s", backup["filename"])

    logger.info("%d ancienne(s) sauvegarde(s) supprimée(s)", deleted_count)
    return deleted_count


def save_backup_metadata(filename: str, size: int, db_path: str | None = None,
                         backup_dir: str | None = None) -> None:
    """Ajoute une entrée à l'historique des sauvegardes (``backup_history.json``)."""
    backup_dir = _backup_dir(backup_dir)
    metadata_path = os.path.join(backup_dir, METADATA_FILENAME)

    history: list[dict[str, Any]] = []
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as f:
                history = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.warning("Historique des sauvegardes illisible, il est recréé : %s", metadata_path)

    history.append({
        "filename": filename,
        "size": size,
        "date": datetime.now().isoformat(),
        "db_path": _db_path(db_path),
    })

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def format_size(size_bytes: float) -> str:
    """Formate une taille en octets de façon lisible (« 1.5 KB », « 2.0 MB »…)."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _table_names(cursor: sqlite3.Cursor) -> list[str]:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [row[0] for row in cursor.fetchall()]


def _quote_identifier(name: str) -> str:
    """Met un nom de table entre guillemets doubles (syntaxe SQL standard des identifiants)."""
    return '"' + name.replace('"', '""') + '"'


def get_db_stats(db_path: str | None = None) -> dict[str, Any] | None:
    """Renvoie la taille de la base et le nombre de lignes de chaque table, ou ``None``."""
    db_path = _db_path(db_path)
    if not os.path.exists(db_path):
        return None

    try:
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            stats: dict[str, Any] = {"size": format_size(os.path.getsize(db_path)), "tables": {}}
            for table_name in _table_names(cursor):
                # Nom de table lu dans sqlite_master et mis entre guillemets : pas une saisie externe.
                cursor.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}")  # nosec B608
                stats["tables"][table_name] = cursor.fetchone()[0]
            return stats
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("Lecture des statistiques impossible pour %s", db_path)
        return None


def export_to_json(output_file: str = "database_export.json", db_path: str | None = None,
                   backup_dir: str | None = None) -> str | None:
    """Exporte toutes les tables de la base dans un fichier JSON du dossier des sauvegardes.

    Returns:
        Le chemin du fichier créé, ou ``None`` en cas d'erreur.
    """
    db_path = _db_path(db_path)
    if not os.path.exists(db_path):
        logger.error("Base de données introuvable : %s", db_path)
        return None
    if not is_safe_backup_filename(output_file):
        logger.error("Nom de fichier d'export refusé : %r", output_file)
        return None

    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            export_data: dict[str, Any] = {"exported_at": datetime.now().isoformat(), "tables": {}}
            for table_name in _table_names(cursor):
                cursor.execute(f"SELECT * FROM {_quote_identifier(table_name)}")  # nosec B608
                export_data["tables"][table_name] = [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        output_path = os.path.join(ensure_backup_dir(backup_dir), output_file)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)

        logger.info("Export JSON créé : %s", output_path)
        return output_path
    except (OSError, sqlite3.Error):
        logger.exception("Échec de l'export JSON de %s", db_path)
        return None
