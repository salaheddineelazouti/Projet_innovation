"""Sauvegarde et restauration de la base SQLite.

Les copies passent par l'API de sauvegarde de SQLite (``sqlite3.Connection.backup``) : la copie
est cohérente même si l'application écrit au même moment, et elle inclut les écritures encore
présentes dans le journal WAL. Une sauvegarde peut être compressée en gzip.

Chaque fonction accepte le chemin de la base (``db_path``) et le dossier des sauvegardes
(``backup_dir``) ; quand ils sont omis, ils sont lus dans la configuration
(``Settings.database_path`` et ``Settings.backup_dir``).

Fichiers du dossier des sauvegardes :

- ``backup_AAAAMMJJ_HHMMSS_mmmmmm.db.gz`` : sauvegarde (heure UTC, microsecondes comprises, pour
  que deux sauvegardes lancées dans la même seconde aient chacune leur fichier) ;
- ``backup_AAAAMMJJ_HHMMSS_mmmmmm_pre_restore.db.gz`` : copie de la base faite juste avant une
  restauration, pour pouvoir revenir en arrière ; elle est listée et supprimée par la rotation
  comme les autres sauvegardes ;
- ``export_*.json`` : exports complets en JSON, conservés en nombre limité
  (:data:`EXPORT_KEEP_COUNT`) car ils contiennent des données personnelles.

Une sauvegarde n'apparaît dans le dossier qu'une fois complète (écriture dans un fichier
temporaire, puis renommage), et les créations de sauvegarde sont exécutées une à la fois dans le
processus. Les heures du nom de fichier et de l'historique sont en UTC ; la liste
(:func:`list_backups`) donne en plus la date dans le fuseau horaire de l'entreprise.

Une restauration met à jour le schéma de la sauvegarde (colonnes, index et tables ajoutés depuis
sa création, voir :func:`tecpap.db.database.migrate_schema`) avant de remplacer la base : la base
restaurée est utilisable par la version courante de l'application.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from tecpap.config import load_settings
from tecpap.db.database import close_connections_to, migrate_schema
from tecpap.formatting import resolve_timezone

logger = logging.getLogger(__name__)

METADATA_FILENAME = "backup_history.json"
TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
"""Format de l'horodatage (UTC) d'un nom de sauvegarde, sans les microsecondes."""

PRE_RESTORE_SUFFIX = "_pre_restore"
"""Fin du nom (avant l'extension) d'une copie de la base faite avant une restauration."""

DISPLAY_DATE_FORMAT = "%d/%m/%Y %H:%M:%S"
"""Format de la date affichée d'une sauvegarde (heure de l'entreprise)."""

EXPORT_KEEP_COUNT = 3
"""Nombre d'exports JSON (``export_*.json``) conservés par :func:`delete_old_exports`."""

# Sauvegarde ``backup_<date>_<heure>[_<microsecondes>][_pre_restore].db[.gz]``, ou ancienne copie
# ``pre_restore_<date>_<heure>.db`` d'avant une restauration.
_BACKUP_NAME_RE = re.compile(
    r"^(?P<kind>backup|pre_restore)_(?P<stamp>\d{8}_\d{6})(?:_(?P<micro>\d{6}))?(?P<pre>_pre_restore)?\.db(?:\.gz)?$"
)
_EXPORT_NAME_RE = re.compile(r"^export_.+\.json$")

# Une seule création de sauvegarde à la fois ; l'historique est relu puis réécrit sous le même verrou.
_BACKUP_LOCK = threading.RLock()


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _remove_quietly(path: str) -> None:
    """Supprime un fichier temporaire (et les fichiers annexes d'une base SQLite) sans lever d'erreur."""
    for candidate in (path, f"{path}-wal", f"{path}-shm", f"{path}-journal"):
        try:
            os.remove(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("Suppression du fichier temporaire %s impossible", os.path.basename(candidate))


def _new_backup_path(backup_dir: str, extension: str, suffix: str = "") -> str:
    """Chemin d'un nouveau fichier de sauvegarde, sans homonyme dans ``backup_dir``.

    Le nom porte l'heure UTC à la microseconde ; si un fichier de même nom existe déjà (autre
    processus), la microseconde suivante est essayée.
    """
    moment = _utc_now()
    while True:
        name = f"backup_{moment.strftime(TIMESTAMP_FORMAT)}_{moment.microsecond:06d}{suffix}{extension}"
        path = os.path.join(backup_dir, name)
        if not os.path.exists(path):
            return path
        moment += timedelta(microseconds=1)


def _write_backup(db_path: str, backup_dir: str, *, compress: bool, suffix: str = "") -> str:
    """Copie la base dans un nouveau fichier de sauvegarde et renvoie son chemin.

    La copie est écrite dans un fichier temporaire du dossier des sauvegardes, puis renommée : le
    fichier ``backup_*`` n'existe jamais à moitié écrit. Les fichiers temporaires sont supprimés
    en cas d'erreur.

    Raises:
        OSError, sqlite3.Error: copie ou compression impossible.
    """
    with _BACKUP_LOCK:
        final_path = _new_backup_path(backup_dir, ".db.gz" if compress else ".db", suffix)
        handle, copy_path = tempfile.mkstemp(prefix="temp_", suffix=".db", dir=backup_dir)
        os.close(handle)
        compressed_path = f"{copy_path}.gz"
        try:
            _sqlite_copy(db_path, copy_path)
            if compress:
                with open(copy_path, "rb") as f_in, gzip.open(compressed_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.replace(compressed_path, final_path)
            else:
                os.replace(copy_path, final_path)
            return final_path
        finally:
            _remove_quietly(copy_path)
            _remove_quietly(compressed_path)


def create_backup(compress: bool = True, db_path: str | None = None, backup_dir: str | None = None) -> str | None:
    """Crée une sauvegarde horodatée de la base.

    Args:
        compress: compresse la sauvegarde en gzip (``backup_AAAAMMJJ_HHMMSS_mmmmmm.db.gz``).
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

    try:
        with _BACKUP_LOCK:
            backup_path = _write_backup(db_path, backup_dir, compress=compress)
            size = os.path.getsize(backup_path)
            logger.info("Sauvegarde créée : %s (%s)", backup_path, format_size(size))
            save_backup_metadata(os.path.basename(backup_path), size, db_path=db_path, backup_dir=backup_dir)
        return backup_path
    except (OSError, sqlite3.Error):
        logger.exception("Échec de la sauvegarde de %s", db_path)
        return None


def restore_backup(backup_filename: str, db_path: str | None = None, backup_dir: str | None = None) -> bool:
    """Restaure la base depuis une sauvegarde du dossier ``backup_dir``.

    Étapes :

    1. la sauvegarde est copiée (et décompressée si besoin) dans un fichier temporaire, dont le
       schéma est mis à jour (:func:`tecpap.db.database.migrate_schema`) : une sauvegarde faite par
       une version plus ancienne reçoit les colonnes, l'index et les tables attendus par la version
       courante ; une sauvegarde illisible, ou dont le schéma ne peut pas être mis à jour, est
       refusée sans que la base actuelle soit modifiée ;
    2. la base actuelle est copiée, compressée, dans ``backup_<date>_pre_restore.db.gz`` pour
       pouvoir revenir en arrière (cette copie est listée et supprimée par la rotation comme les
       autres sauvegardes) ;
    3. toutes les connexions des gestionnaires ouverts sur cette base sont fermées
       (:func:`tecpap.db.database.close_connections_to`) : aucune lecture en cours ne porte sur
       l'ancienne base, et chaque gestionnaire reprend au prochain ``connect()`` ;
    4. la copie temporaire remplace la base.

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

    handle, temp_restore = tempfile.mkstemp(prefix="restore_", suffix=".db", dir=backup_dir)
    os.close(handle)
    try:
        if backup_filename.endswith(".gz"):
            with gzip.open(backup_path, "rb") as f_in, open(temp_restore, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            _sqlite_copy(backup_path, temp_restore)
        migrate_schema(temp_restore)

        with _BACKUP_LOCK:
            if os.path.exists(db_path):
                pre_restore = _write_backup(db_path, backup_dir, compress=True, suffix=PRE_RESTORE_SUFFIX)
                logger.info("Copie de la base avant restauration : %s", pre_restore)

            closed = close_connections_to(db_path)
            if closed:
                logger.info("%d connexion(s) à la base fermée(s) avant la restauration", closed)
            _sqlite_copy(temp_restore, db_path)
        logger.info("Base de données restaurée depuis %s", backup_filename)
        return True
    except (OSError, sqlite3.Error, EOFError):
        logger.exception("Échec de la restauration depuis %s", backup_filename)
        return False
    finally:
        _remove_quietly(temp_restore)


def _parse_backup_name(filename: str) -> tuple[datetime | None, bool]:
    """Heure UTC inscrite dans un nom de sauvegarde (``None`` sans date), et copie d'avant restauration ou non."""
    match = _BACKUP_NAME_RE.match(filename)
    if match is None:
        return None, filename.startswith("pre_restore_")
    moment = datetime.strptime(match["stamp"], TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    if match["micro"]:
        moment = moment.replace(microsecond=int(match["micro"]))
    return moment, match["kind"] == "pre_restore" or bool(match["pre"])


def list_backups(backup_dir: str | None = None) -> list[dict[str, Any]]:
    """Liste les sauvegardes (``backup_*.db`` et ``backup_*.db.gz``), des plus récentes aux plus anciennes.

    Les copies faites avant une restauration en font partie (``pre_restore`` vaut alors ``True``),
    de même que les anciennes copies ``pre_restore_*.db``. Chaque élément contient ``filename``,
    ``path``, ``size``, ``size_formatted``, ``compressed``, ``pre_restore``, ``created_at`` (heure
    UTC en ISO 8601, ``None`` si le nom du fichier ne porte pas de date) et ``date`` (la même heure
    dans le fuseau horaire de l'entreprise, « JJ/MM/AAAA HH:MM:SS », ou « N/A »).
    """
    backup_dir = ensure_backup_dir(backup_dir)
    zone = resolve_timezone()
    backups = []

    for filename in os.listdir(backup_dir):
        if not (filename.startswith(("backup_", "pre_restore_")) and filename.endswith((".db", ".db.gz"))):
            continue
        filepath = os.path.join(backup_dir, filename)
        stat = os.stat(filepath)
        moment, pre_restore = _parse_backup_name(filename)

        backups.append({
            "filename": filename,
            "path": filepath,
            "size": stat.st_size,
            "size_formatted": format_size(stat.st_size),
            "date": moment.astimezone(zone).strftime(DISPLAY_DATE_FORMAT) if moment else "N/A",
            "created_at": moment.isoformat() if moment else None,
            "compressed": filename.endswith(".gz"),
            "pre_restore": pre_restore,
        })

    # Les plus récentes d'abord ; un nom sans date vient en dernier.
    backups.sort(key=lambda backup: (backup["created_at"] or "", backup["filename"]), reverse=True)
    return backups


def delete_old_backups(keep_count: int = 10, backup_dir: str | None = None) -> int:
    """Supprime les sauvegardes les plus anciennes pour n'en garder que ``keep_count``.

    Les copies d'avant restauration comptent comme les autres sauvegardes. Les exports JSON
    anciens sont aussi supprimés (:func:`delete_old_exports`).

    Raises:
        ValueError: ``keep_count`` est inférieur à 1 (garder zéro sauvegarde supprimerait toutes
            les sauvegardes, et un nombre négatif ne désignerait que les plus anciennes).

    Returns:
        Le nombre de fichiers supprimés (sauvegardes et exports).
    """
    if isinstance(keep_count, bool) or not isinstance(keep_count, int) or keep_count < 1:
        raise ValueError("Le nombre de sauvegardes conservées doit être au moins 1")

    backups = list_backups(backup_dir)
    deleted_exports = delete_old_exports(backup_dir=backup_dir)
    if len(backups) <= keep_count:
        logger.info("%d sauvegarde(s), limite %d : aucune suppression", len(backups), keep_count)
        return deleted_exports

    deleted_backups = 0
    for backup in backups[keep_count:]:
        try:
            os.remove(backup["path"])
            deleted_backups += 1
            logger.info("Sauvegarde supprimée : %s", backup["filename"])
        except OSError:
            logger.exception("Échec de la suppression de %s", backup["filename"])

    logger.info("%d ancienne(s) sauvegarde(s) supprimée(s)", deleted_backups)
    return deleted_exports + deleted_backups


def delete_old_exports(keep_count: int = EXPORT_KEEP_COUNT, backup_dir: str | None = None) -> int:
    """Supprime les exports JSON (``export_*.json``) les plus anciens pour n'en garder que ``keep_count``.

    Un export contient toutes les données de la base, personnelles comprises : il n'est pas
    conservé indéfiniment. L'ordre est celui de la date de modification des fichiers.

    Returns:
        Le nombre de fichiers supprimés.
    """
    directory = _backup_dir(backup_dir)
    if not os.path.isdir(directory):
        return 0
    exports = [os.path.join(directory, name) for name in os.listdir(directory) if _EXPORT_NAME_RE.match(name)]
    exports.sort(key=os.path.getmtime, reverse=True)
    deleted = 0
    for path in exports[max(0, keep_count):]:
        try:
            os.remove(path)
            deleted += 1
            logger.info("Export supprimé : %s", os.path.basename(path))
        except OSError:
            logger.exception("Échec de la suppression de %s", os.path.basename(path))
    return deleted


def save_backup_metadata(filename: str, size: int, db_path: str | None = None,
                         backup_dir: str | None = None) -> None:
    """Ajoute une entrée à l'historique des sauvegardes (``backup_history.json``, dates en UTC)."""
    backup_dir = _backup_dir(backup_dir)
    metadata_path = os.path.join(backup_dir, METADATA_FILENAME)

    with _BACKUP_LOCK:
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
            "date": _utc_now().isoformat(timespec="seconds"),
            "db_path": _db_path(db_path),
        })

        # Écriture dans un fichier temporaire puis renommage : l'historique n'est jamais tronqué.
        temp_path = f"{metadata_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, metadata_path)


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
            export_data: dict[str, Any] = {"exported_at": _utc_now().isoformat(timespec="seconds"), "tables": {}}
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
