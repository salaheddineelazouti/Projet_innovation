"""Sauvegardes de la base : page de gestion et API (création, liste, téléchargement,
restauration, suppression, export JSON, réglage du planificateur)."""

from __future__ import annotations

import os
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
from tecpap.web.services import get_services

bp = Blueprint("backups", __name__)


def _invalid_filename():
    return jsonify({"error": "Invalid filename"}), 400


def _backup_file(filename: str) -> str:
    """Chemin absolu d'un fichier du dossier des sauvegardes."""
    return os.path.abspath(os.path.join(get_services().settings.backup_dir, filename))


@bp.route("/api/backup/create", methods=["POST"])
def api_create_backup():
    """Crée une sauvegarde manuelle (compressée sauf ``{"compress": false}``)."""
    settings = get_services().settings
    try:
        data = request.get_json(silent=True)
        compress = data.get("compress", True) if isinstance(data, dict) and data else True
        backup_path = create_backup(compress=compress, db_path=settings.database_path, backup_dir=settings.backup_dir)
        if backup_path:
            return jsonify({
                "success": True,
                "message": "Sauvegarde créée avec succès",
                "path": backup_path,
                "filename": os.path.basename(backup_path),
            })
        return jsonify({"success": False, "message": "Erreur lors de la création"}), 500
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


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
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/backup/download/<filename>")
def api_download_backup(filename: str):
    """Télécharge une sauvegarde existante."""
    try:
        if not is_safe_backup_filename(filename):
            return _invalid_filename()
        backup_path = _backup_file(filename)
        if not os.path.exists(backup_path):
            return jsonify({"error": "Backup not found"}), 404
        return send_file(backup_path, as_attachment=True, download_name=filename)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/backup/download-latest")
def api_download_latest_backup():
    """Crée une sauvegarde compressée et la télécharge."""
    settings = get_services().settings
    try:
        backup_path = create_backup(compress=True, db_path=settings.database_path, backup_dir=settings.backup_dir)
        if not backup_path:
            return jsonify({"error": "Failed to create backup"}), 500
        return send_file(os.path.abspath(backup_path), as_attachment=True, download_name=os.path.basename(backup_path))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/backup/restore/<filename>", methods=["POST"])
def api_restore_backup(filename: str):
    """Restaure la base depuis une sauvegarde puis rouvre la connexion de l'application."""
    services = get_services()
    try:
        if not is_safe_backup_filename(filename):
            return _invalid_filename()
        if restore_backup(filename, db_path=services.settings.database_path, backup_dir=services.settings.backup_dir):
            services.db.disconnect()
            services.db.connect()
            return jsonify({"success": True, "message": f"Base de données restaurée depuis {filename}"})
        return jsonify({"success": False, "message": "Erreur lors de la restauration"}), 500
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/backup/delete/<filename>", methods=["DELETE"])
def api_delete_backup(filename: str):
    """Supprime une sauvegarde."""
    try:
        if not is_safe_backup_filename(filename):
            return _invalid_filename()
        backup_path = _backup_file(filename)
        if not os.path.exists(backup_path):
            return jsonify({"error": "Backup not found"}), 404
        os.remove(backup_path)
        return jsonify({"success": True, "message": f"Sauvegarde {filename} supprimée"})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@bp.route("/api/backup/export-json")
def api_export_json():
    """Exporte toute la base en JSON et télécharge le fichier."""
    settings = get_services().settings
    try:
        output_file = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        export_path = export_to_json(output_file, db_path=settings.database_path, backup_dir=settings.backup_dir)
        if export_path:
            return send_file(os.path.abspath(export_path), as_attachment=True, download_name=output_file)
        return jsonify({"error": "Failed to export"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/backup/scheduler/status")
def api_scheduler_status():
    """État du planificateur de sauvegardes."""
    return jsonify(get_services().backup_scheduler.get_status())


@bp.route("/api/backup/scheduler/configure", methods=["POST"])
def api_configure_scheduler():
    """Règle l'intervalle (``interval_hours``) et le nombre de sauvegardes conservées (``keep_backups``)."""
    scheduler = get_services().backup_scheduler
    try:
        data = request.get_json(silent=True)
        data = data if isinstance(data, dict) else {}
        scheduler.configure(interval_hours=data.get("interval_hours"), keep_backups=data.get("keep_backups"))
        return jsonify({"success": True, "message": "Configuration mise à jour", "status": scheduler.get_status()})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


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
