"""Sauvegardes de la base : page de gestion et API (création, liste, téléchargement,
restauration, suppression, export JSON, réglage du planificateur).

Un nom de fichier reçu dans l'URL n'est accepté que s'il ne contient que des lettres non
accentuées, des chiffres, ``_``, ``.`` et ``-``, et s'il désigne un fichier situé directement
dans le dossier des sauvegardes une fois les liens symboliques résolus (``os.path.realpath``) :
aucune requête ne peut lire, restaurer ou supprimer un fichier ailleurs sur le serveur.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, send_file

from tecpap.db.backup import (
    create_backup,
    export_to_json,
    get_db_stats,
    is_safe_backup_filename,
    list_backups,
    restore_backup,
)
from tecpap.web.errors import json_error, server_error
from tecpap.web.scheduler import SchedulerConfigError
from tecpap.web.services import get_services

logger = logging.getLogger(__name__)

bp = Blueprint("backups", __name__)

BACKUP_FILENAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
"""Caractères autorisés dans un nom de fichier de sauvegarde reçu dans l'URL."""


def resolve_backup_path(filename: str, backup_dir: str) -> str | None:
    """Chemin réel d'un fichier situé directement dans ``backup_dir``, ou ``None`` si le nom est refusé."""
    if not BACKUP_FILENAME_PATTERN.fullmatch(filename) or not is_safe_backup_filename(filename):
        return None
    directory = os.path.realpath(backup_dir)
    path = os.path.realpath(os.path.join(directory, filename))
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(directory):
        return None
    return path


def _invalid_filename():
    return json_error("Nom de fichier de sauvegarde invalide", 400)


def _backup_not_found():
    return json_error("Sauvegarde introuvable", 404)


@bp.route("/api/backup/create", methods=["POST"])
def api_create_backup():
    """Crée une sauvegarde manuelle (compressée sauf ``{"compress": false}``)."""
    settings = get_services().settings
    try:
        data = request.get_json(silent=True)
        compress = data.get("compress", True) if isinstance(data, dict) and data else True
        backup_path = create_backup(compress=bool(compress), db_path=settings.database_path,
                                    backup_dir=settings.backup_dir)
        if backup_path:
            return jsonify({
                "success": True,
                "message": "Sauvegarde créée avec succès",
                "path": backup_path,
                "filename": os.path.basename(backup_path),
            })
        return jsonify({"success": False, "message": "Erreur lors de la création"}), 500
    except Exception:
        logger.exception("Échec de la création d'une sauvegarde")
        return server_error()


@bp.route("/api/backup/list")
def api_list_backups():
    """Liste des sauvegardes, statistiques de la base et état du planificateur."""
    services = get_services()
    try:
        return jsonify({
            "success": True,
            "backups": list_backups(services.settings.backup_dir),
            "db_stats": get_db_stats(services.settings.database_path),
            "scheduler": services.backup_scheduler.get_status(),
        })
    except Exception:
        logger.exception("Échec de la lecture de la liste des sauvegardes")
        return server_error()


@bp.route("/api/backup/download/<filename>")
def api_download_backup(filename: str):
    """Télécharge une sauvegarde existante."""
    backup_path = resolve_backup_path(filename, get_services().settings.backup_dir)
    if backup_path is None:
        return _invalid_filename()
    if not os.path.isfile(backup_path):
        return _backup_not_found()
    try:
        return send_file(backup_path, as_attachment=True, download_name=filename)
    except Exception:
        logger.exception("Échec du téléchargement de la sauvegarde %s", filename)
        return server_error()


@bp.route("/api/backup/download-latest")
def api_download_latest_backup():
    """Crée une sauvegarde compressée et la télécharge."""
    settings = get_services().settings
    try:
        backup_path = create_backup(compress=True, db_path=settings.database_path, backup_dir=settings.backup_dir)
        if not backup_path:
            return json_error("Échec de la création de la sauvegarde", 500)
        return send_file(os.path.abspath(backup_path), as_attachment=True, download_name=os.path.basename(backup_path))
    except Exception:
        logger.exception("Échec du téléchargement de la dernière sauvegarde")
        return server_error()


@bp.route("/api/backup/restore/<filename>", methods=["POST"])
def api_restore_backup(filename: str):
    """Restaure la base depuis une sauvegarde puis rouvre la connexion de l'application."""
    services = get_services()
    backup_path = resolve_backup_path(filename, services.settings.backup_dir)
    if backup_path is None:
        return _invalid_filename()
    if not os.path.isfile(backup_path):
        return _backup_not_found()
    try:
        if restore_backup(filename, db_path=services.settings.database_path, backup_dir=services.settings.backup_dir):
            services.db.disconnect()
            services.db.connect()
            return jsonify({"success": True, "message": f"Base de données restaurée depuis {filename}"})
        return jsonify({"success": False, "message": "Erreur lors de la restauration"}), 500
    except Exception:
        logger.exception("Échec de la restauration de la sauvegarde %s", filename)
        return server_error()


@bp.route("/api/backup/delete/<filename>", methods=["DELETE"])
def api_delete_backup(filename: str):
    """Supprime une sauvegarde."""
    backup_path = resolve_backup_path(filename, get_services().settings.backup_dir)
    if backup_path is None:
        return _invalid_filename()
    if not os.path.isfile(backup_path):
        return _backup_not_found()
    try:
        os.remove(backup_path)
        logger.info("Sauvegarde supprimée : %s", filename)
        return jsonify({"success": True, "message": f"Sauvegarde {filename} supprimée"})
    except Exception:
        logger.exception("Échec de la suppression de la sauvegarde %s", filename)
        return server_error()


@bp.route("/api/backup/export-json")
def api_export_json():
    """Exporte toute la base en JSON et télécharge le fichier."""
    settings = get_services().settings
    try:
        output_file = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        export_path = export_to_json(output_file, db_path=settings.database_path, backup_dir=settings.backup_dir)
        if export_path:
            return send_file(os.path.abspath(export_path), as_attachment=True, download_name=output_file)
        return json_error("Échec de l'export JSON", 500)
    except Exception:
        logger.exception("Échec de l'export JSON de la base")
        return server_error()


@bp.route("/api/backup/scheduler/status")
def api_scheduler_status():
    """État du planificateur de sauvegardes."""
    return jsonify(get_services().backup_scheduler.get_status())


@bp.route("/api/backup/scheduler/configure", methods=["POST"])
def api_configure_scheduler():
    """Règle l'intervalle (``interval_hours``) et le nombre de sauvegardes conservées (``keep_backups``)."""
    scheduler = get_services().backup_scheduler
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    try:
        scheduler.configure(interval_hours=data.get("interval_hours"), keep_backups=data.get("keep_backups"))
    except SchedulerConfigError as exc:
        # Messages de validation écrits pour l'utilisateur (voir BackupScheduler.configure).
        return json_error(exc.message, 400)
    except Exception:
        logger.exception("Échec du réglage du planificateur de sauvegardes")
        return server_error()
    return jsonify({"success": True, "message": "Configuration mise à jour", "status": scheduler.get_status()})


@bp.route("/backups")
def backups_page():
    """Page de gestion des sauvegardes."""
    services = get_services()
    return render_template(
        "backups.html",
        backups=list_backups(services.settings.backup_dir),
        db_stats=get_db_stats(services.settings.database_path),
        scheduler=services.backup_scheduler.get_status(),
    )
