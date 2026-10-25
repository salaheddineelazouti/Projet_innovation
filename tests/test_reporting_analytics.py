"""Statistiques du tableau de bord, historique client et rapports PDF.

Règles vérifiées : une commande supprimée n'est comptée nulle part, le chiffre d'affaires ne
porte que sur les commandes validées, l'historique d'un client ne regroupe pas les commandes
d'un autre client au nom proche, et « Commandes ce mois » compte le mois en cours.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pypdf
import pytest

from tecpap.reporting.analytics import AIPredictor, Analytics, ClientHistory, ReportGenerator
from tecpap.reporting.pdf_report import generate_pdf_report_improved, pdf_safe_text


def _order(db, client: str, *, prix_total=None, quantite=1000, statut=None, created_at=None, **extra) -> int:
    order_id = db.create_order({"entreprise_cliente": client, "type_produit": "Sachets fond plat",
                                "quantite": quantite, "unite": "pièces", "prix_total": prix_total,
                                "devise": "MAD", **extra})
    if statut == "supprimee":
        db.delete_order(order_id)
    elif statut:
        db.update_order_status(order_id, statut)
    if created_at:
        db.connection.execute("UPDATE commandes SET created_at = ? WHERE id = ?", (created_at, order_id))
        db.connection.commit()
    return order_id


@pytest.fixture
def acme(temp_db):
    """Un client avec une commande validée, une rejetée, une supprimée et une en attente."""
    _order(temp_db, "Acme", prix_total=2000.0, statut="validee")
    _order(temp_db, "Acme", prix_total=450000.0, quantite=900000, statut="rejetee")
    _order(temp_db, "Acme", prix_total=100000.0, statut="supprimee")
    _order(temp_db, "Acme", prix_total=3000.0)
    return temp_db


class TestDashboardStats:
    def test_revenue_counts_only_validated_orders(self, acme):
        stats = Analytics(acme).get_dashboard_stats()
        assert stats["total_revenue"] == 2000.0
        assert stats["avg_order_value"] == 2000.0

    def test_deleted_orders_are_not_counted(self, acme):
        stats = Analytics(acme).get_dashboard_stats()
        assert stats["total_orders"] == 3
        assert stats["total_orders"] == stats["validated_orders"] + stats["pending_orders"] + stats["rejected_orders"]
        assert "supprimee" not in stats["orders_by_status"]
        assert stats["orders_by_product"][0]["count"] == 3

    def test_quantities_leave_out_rejected_and_deleted_orders(self, acme):
        stats = Analytics(acme).get_dashboard_stats()
        assert stats["total_quantity"] == 2000
        assert stats["orders_by_product"][0]["total_qty"] == 2000

    def test_top_clients_total_spent_is_validated_revenue(self, acme):
        top = Analytics(acme).get_dashboard_stats()["top_clients"][0]
        assert (top["nom"], top["order_count"], top["validated_count"], top["total_spent"]) == ("Acme", 3, 1, 2000.0)

    def test_monthly_revenue_is_validated_revenue(self, acme):
        trends = Analytics(acme).get_dashboard_stats()["monthly_trends"]
        assert sum(month["revenue"] or 0 for month in trends) == 2000.0
        assert sum(month["count"] for month in trends) == 3

    def test_orders_this_month(self, temp_db):
        _order(temp_db, "Ancien", created_at="2025-03-10 10:00:00")
        _order(temp_db, "Fin septembre", created_at="2026-09-30 22:30:00")
        _order(temp_db, "Octobre", created_at="2026-10-05 10:00:00")
        _order(temp_db, "Supprimee", statut="supprimee", created_at="2026-10-06 10:00:00")
        now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

        utc_stats = Analytics(temp_db, "UTC").get_dashboard_stats(now=now)
        assert (utc_stats["orders_this_month"], utc_stats["total_orders"]) == (1, 3)
        # En UTC+2 (fuseau fixe « Etc/GMT-2 »), 22:30 UTC le 30 septembre est déjà le 1er octobre.
        assert Analytics(temp_db, "Etc/GMT-2").get_dashboard_stats(now=now)["orders_this_month"] == 2


class TestClientHistory:
    @pytest.fixture
    def snacks(self, temp_db):
        _order(temp_db, "Snack", prix_total=500.0, quantite=100, statut="validee")
        _order(temp_db, "Snack Atlas", prix_total=50000.0, quantite=20000, statut="validee")
        _order(temp_db, "Snack Atlas", prix_total=60000.0, quantite=30000, statut="validee")
        return temp_db

    def test_name_does_not_match_longer_names(self, snacks):
        preferences = ClientHistory(snacks).get_client_preferences("Snack")
        assert preferences["total_orders"] == 1
        assert preferences["total_spent"] == 500.0
        assert preferences["average_quantity"] == 100

    def test_like_wildcards_are_not_interpreted(self, snacks):
        assert ClientHistory(snacks).get_client_preferences("S_ack") is None
        assert ClientHistory(snacks).get_client_preferences("%") is None

    def test_name_match_ignores_case(self, snacks):
        assert ClientHistory(snacks).get_client_preferences("snack atlas")["total_orders"] == 2

    def test_lookup_by_client_id(self, snacks):
        client_id = snacks.connection.execute("SELECT id FROM clients WHERE nom = 'Snack Atlas'").fetchone()[0]
        preferences = ClientHistory(snacks).get_client_preferences(client_id=client_id)
        assert (preferences["client_name"], preferences["total_orders"]) == ("Snack Atlas", 2)

    def test_empty_name_gives_nothing(self, snacks):
        assert ClientHistory(snacks).get_client_preferences("  ") is None

    def test_totals_follow_status_rules(self, acme):
        preferences = ClientHistory(acme).get_client_preferences("Acme")
        assert preferences["total_orders"] == 3  # la commande supprimée est ignorée
        assert preferences["total_spent"] == 2000.0  # commandes validées uniquement
        assert preferences["average_quantity"] == 1000  # la commande rejetée de 900 000 est écartée

    def test_client_value_uses_validated_revenue(self, acme, test_settings):
        prediction = AIPredictor(acme, settings=test_settings).predict_client_behavior("Acme")
        assert prediction["client_value"] == "medium"
        assert prediction["client"] == "Acme"

    def test_prediction_by_client_id(self, snacks, test_settings):
        client_id = snacks.connection.execute("SELECT id FROM clients WHERE nom = 'Snack'").fetchone()[0]
        prediction = AIPredictor(snacks, settings=test_settings).predict_client_behavior(client_id=client_id)
        assert (prediction["client"], prediction["predicted_quantity"], prediction["client_value"]) == \
            ("Snack", 100, "low")

    def test_suggest_reorder_uses_exact_client(self, snacks):
        partial = ClientHistory(snacks).suggest_reorder("Snack", {})
        assert partial["quantite"] == 100


def _pdf_text(path: str) -> str:
    text = "".join(page.extract_text() for page in pypdf.PdfReader(path).pages)
    return text.replace("\xa0", " ")


class TestPdfReports:
    def test_improved_report_shows_validated_revenue(self, acme, test_settings):
        path = generate_pdf_report_improved(acme, filepath=str(Path(test_settings.export_dir) / "r.pdf"))
        text = _pdf_text(path)
        assert "CA VALIDÉ" in text
        assert "2 000" in text and "552" not in text
        assert "Enregistrés" in text

    def test_simple_report_shows_validated_revenue(self, acme, test_settings):
        path = ReportGenerator(acme, test_settings).generate_pdf_report()
        assert Path(path).parent == Path(test_settings.export_dir)
        text = _pdf_text(path)
        assert "Chiffre d'affaires validé" in text
        assert "2 000,00 MAD" in text

    def test_pdf_text_has_no_narrow_space(self):
        assert pdf_safe_text("10 000 MAD") == "10 000 MAD"

    def test_default_report_path_is_unique(self, acme, test_settings, monkeypatch):
        monkeypatch.setattr("tecpap.reporting.pdf_report.load_settings", lambda: test_settings)
        first = generate_pdf_report_improved(acme)
        second = generate_pdf_report_improved(acme)
        assert first != second
        assert Path(first).parent == Path(test_settings.export_dir)


class TestAnalyticsPage:
    def test_month_card_and_rates(self, flask_client, services):
        db = services.db
        _order(db, "Ancien", created_at="2020-03-10 10:00:00")
        _order(db, "Recent", statut="rejetee")
        page = flask_client.get("/analytics").data.decode("utf-8")
        assert "Commandes ce mois" in page
        assert "2 au total" in page.replace(" ", " ").replace("\xa0", " ")
        assert "Taux de conversion" not in page
        assert "Taux de rejet" in page
        assert "Clients enregistrés" in page and "Clients actifs" not in page

    def test_api_analytics_exposes_month_count(self, flask_client, services):
        _order(services.db, "Recent")
        data = flask_client.get("/api/analytics").get_json()
        assert data["orders_this_month"] == 1
