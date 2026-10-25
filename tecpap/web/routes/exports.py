"""Téléchargement des exports : Excel standard, Excel au format SAGE X3, CSV et rapport PDF.

Chaque export est écrit dans un fichier temporaire au nom unique du dossier des exports, lu en
mémoire puis supprimé avant l'envoi : deux téléchargements simultanés ne partagent jamais un
fichier, et aucun export ne reste sur le disque.

Les trois exports de tableaux acceptent les mêmes filtres que le formulaire de la page SAGE X3 :
``status``, ``date_from`` et ``date_to`` (jours « AAAA-MM-JJ » inclus). Un filtre invalide
(date mal écrite, période inversée, statut inconnu, ou statut autre que « validee » pour
l'export SAGE X3) donne une page d'erreur 400 qui explique le refus : un filtre n'est jamais
ignoré en silence.
"""

from __future__ import annotations

import io
import logging
import os
from collections.abc import Callable

from flask import Blueprint, request, send_file

from tecpap.catalog import PAPER_TYPE_NAMES, PAPER_TYPES, STANDARD_GRAMMAGES, STANDARD_LAIZES, SUPPLIERS
from tecpap.reporting.analytics import ExportFilterError, is_stock_unit
from tecpap.reporting.pdf_report import generate_pdf_report_improved
from tecpap.web.errors import error_page
from tecpap.web.services import get_services

logger = logging.getLogger(__name__)

bp = Blueprint("exports", __name__)

EXPORT_FILTERS = ("status", "date_from", "date_to")
"""Champs du formulaire d'export transmis aux trois exports de tableaux."""


def _filters() -> dict[str, str] | None:
    """Filtres d'export lus dans la chaîne de requête (:data:`EXPORT_FILTERS`)."""
    filters = {name: request.args[name] for name in EXPORT_FILTERS if request.args.get(name)}
    return filters or None


def _remove(filepath: str) -> None:
    try:
        os.remove(filepath)
    except OSError:
        logger.warning("Fichier d'export temporaire impossible à supprimer : %s", filepath)


def _send_export(build: Callable[[], str], download_name: str):
    """Construit l'export, l'envoie en pièce jointe et supprime son fichier temporaire."""
    try:
        filepath = build()
    except ExportFilterError as error:
        return error_page("Export impossible", str(error), 400)
    try:
        with open(filepath, "rb") as handle:
            content = handle.read()
    finally:
        _remove(filepath)
    return send_file(io.BytesIO(content), as_attachment=True, download_name=download_name)


@bp.route("/export/excel")
def export_excel():
    """Classeur Excel des commandes (colonnes de la base), filtrable par statut et par période."""
    report_generator = get_services().report_generator
    return _send_export(lambda: report_generator.export_to_excel(filters=_filters()), "commandes.xlsx")


@bp.route("/export/excel/sage")
def export_excel_sage():
    """Classeur Excel au format d'import SAGE X3 : commandes validées, filtrables par période."""
    report_generator = get_services().report_generator
    return _send_export(lambda: report_generator.export_to_excel_sage(filters=_filters()), "commandes_sage_x3.xlsx")


@bp.route("/export/csv")
def export_csv():
    """Fichier CSV des commandes (colonnes de la base), filtrable par statut et par période."""
    report_generator = get_services().report_generator
    return _send_export(lambda: report_generator.export_to_csv(filters=_filters()), "commandes.csv")


@bp.route("/export/pdf")
def export_pdf():
    """Rapport PDF d'aide à la décision."""
    services = get_services()

    def build() -> str:
        filepath = services.report_generator.new_export_path("rapport.pdf")
        try:
            return generate_pdf_report_improved(services.db, filepath=filepath)
        except BaseException:
            _remove(filepath)
            raise

    return _send_export(build, "rapport_commandes.pdf")


@bp.app_template_global("is_stock_unit")
def is_stock_unit_global(unit: str | None) -> bool:
    """Vrai pour l'unité de stock de SAGE X3 (« US », « pièces ») ; une unité absente vaut « US »."""
    return is_stock_unit(unit)


@bp.app_template_global("article_code_options")
def article_code_options() -> dict[str, list[dict[str, str]]]:
    """Choix du générateur de codes articles de la page SAGE X3, lus dans le catalogue.

    Chaque choix a une valeur (texte envoyé à ``/api/article-code/generate``) et un libellé :
    types de papier, grammages et laizes standards, fournisseurs (sauf le fournisseur par défaut).
    """
    paper_types = []
    for code, label in PAPER_TYPE_NAMES.items():
        name = next((key for key, value in PAPER_TYPES.items() if value == code and key.startswith("kraft ")), label)
        paper_types.append({"value": name, "label": f"{label} ({code})"})
    return {
        "paper_types": paper_types,
        "grammages": [{"value": str(value), "label": str(value)} for value in STANDARD_GRAMMAGES],
        "laizes": [{"value": str(value), "label": str(value)} for value in STANDARD_LAIZES],
        "suppliers": [
            {"value": name, "label": f"{name.upper()} ({code})"}
            for name, code in SUPPLIERS.items() if name != "default"
        ],
    }
