"""Téléchargement des exports : Excel standard, Excel au format SAGE X3, CSV et rapport PDF."""

from __future__ import annotations

import os

from flask import Blueprint, request, send_file

from tecpap.reporting.pdf_report import generate_pdf_report_improved
from tecpap.web.services import get_services

bp = Blueprint("exports", __name__)


def _filters(*names: str) -> dict[str, str] | None:
    """Filtres d'export lus dans la chaîne de requête (``status``, ``date_from``, ``date_to``)."""
    filters = {name: request.args[name] for name in names if request.args.get(name)}
    return filters or None


def _download(filepath: str, download_name: str):
    # Chemin absolu : Flask résout un chemin relatif par rapport au dossier du paquet web,
    # alors que les exports sont écrits par rapport au dossier de travail.
    return send_file(os.path.abspath(filepath), as_attachment=True, download_name=download_name)


@bp.route("/export/excel")
def export_excel():
    """Classeur Excel des commandes, filtrable par statut et par période."""
    filepath = get_services().report_generator.export_to_excel(filters=_filters("status", "date_from", "date_to"))
    return _download(filepath, "commandes.xlsx")


@bp.route("/export/excel/sage")
def export_excel_sage():
    """Classeur Excel au format d'import SAGE X3, filtrable par statut et par période."""
    report_generator = get_services().report_generator
    filepath = report_generator.export_to_excel_sage(filters=_filters("status", "date_from", "date_to"))
    return _download(filepath, "commandes_sage_x3.xlsx")


@bp.route("/export/csv")
def export_csv():
    """Fichier CSV des commandes, filtrable par statut."""
    filepath = get_services().report_generator.export_to_csv(filters=_filters("status"))
    return _download(filepath, "commandes.csv")


@bp.route("/export/pdf")
def export_pdf():
    """Rapport PDF d'aide à la décision."""
    services = get_services()
    filepath = generate_pdf_report_improved(
        services.db, filepath=os.path.join(services.settings.export_dir, "rapport.pdf")
    )
    return _download(filepath, "rapport_commandes.pdf")
