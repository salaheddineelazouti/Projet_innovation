"""Page de préparation de l'export SAGE X3 (/sage) : aperçu, formulaire d'export et générateur de codes."""

from __future__ import annotations

import re

import pytest

from tecpap.catalog import STANDARD_GRAMMAGES, STANDARD_LAIZES, SUPPLIERS
from tecpap.web.routes.exports import article_code_options


def _order(db, client: str, *, statut=None, **extra) -> int:
    order_id = db.create_order({"entreprise_cliente": client, "type_produit": "Sachets fond plat",
                                "quantite": 5000, "unite": "pièces", **extra})
    if statut:
        db.update_order_status(order_id, statut)
    return order_id


def _page(flask_client) -> str:
    response = flask_client.get("/sage")
    assert response.status_code == 200
    return response.data.decode("utf-8").replace(" ", " ").replace("\xa0", " ")


def _preview_rows(page: str) -> list[str]:
    body = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    return re.findall(r"<tr>(.*?)</tr>", body, flags=re.S)


class TestPreview:
    def test_preview_lists_only_validated_orders(self, flask_client, services):
        _order(services.db, "Client Valide", statut="validee")
        _order(services.db, "Client Attente")
        _order(services.db, "Client Rejete", statut="rejetee")
        page = _page(flask_client)
        rows = _preview_rows(page)
        assert len(rows) == 1 and "Client Valide" in rows[0]
        assert "1 commande(s) validée(s)" in page

    def test_fully_delivered_line_shows_zero_remaining(self, flask_client, services):
        order_id = _order(services.db, "Client Livre", statut="validee")
        services.db.connection.execute(
            "UPDATE commandes SET quantite_livree = 5000, reste_a_livrer = 0 WHERE id = ?", (order_id,))
        services.db.connection.commit()
        cells = re.findall(r"<td[^>]*>\s*(.*?)\s*</td>", _preview_rows(_page(flask_client))[0], flags=re.S)
        assert cells[5:9] == ["5 000", "US", "5 000", "0"]

    def test_remaining_is_computed_when_missing(self, flask_client, services):
        order_id = _order(services.db, "Client Partiel", statut="validee")
        services.db.connection.execute(
            "UPDATE commandes SET quantite_livree = 2000, reste_a_livrer = NULL WHERE id = ?", (order_id,))
        services.db.connection.commit()
        cells = re.findall(r"<td[^>]*>\s*(.*?)\s*</td>", _preview_rows(_page(flask_client))[0], flags=re.S)
        assert cells[8] == "3 000"

    def test_carton_order_is_marked_for_conversion(self, flask_client, services):
        _order(services.db, "Client Cartons", statut="validee", unite="carton", quantite=20)
        row = _preview_rows(_page(flask_client))[0]
        assert "carton · à convertir" in row

    @pytest.mark.parametrize("unit, converted", [("US", False), ("pièces", False), (None, False),
                                                 ("carton", True), ("paquet", True), ("kg", True)])
    def test_only_non_stock_units_are_marked_for_conversion(self, flask_client, services, unit, converted):
        _order(services.db, "Client Unite", statut="validee", unite=unit)
        assert ("à convertir" in _preview_rows(_page(flask_client))[0]) is converted

    def test_reference_falls_back_to_cmd_id(self, flask_client, services):
        order_id = _order(services.db, "Client Sans Numero", statut="validee")
        assert f"CMD-{order_id}" in _preview_rows(_page(flask_client))[0]


class TestExportForm:
    def test_validated_status_is_the_default(self, flask_client):
        page = _page(flask_client)
        assert '<option value="validee" selected>' in page
        assert "Toutes les commandes (sauf supprimées)" in page

    def test_sage_buttons_announce_validated_orders(self, flask_client):
        page = _page(flask_client)
        assert 'href="/export/excel/sage"' in page
        assert "ne contient que les commandes validées" in page
        assert "Unités à convertir" in page

    def test_no_promise_of_future_api(self, flask_client):
        page = _page(flask_client)
        assert "ultérieurement" not in page and "En attendant" not in page


class TestArticleCodeGenerator:
    def test_options_come_from_catalog(self):
        options = article_code_options()
        assert [int(option["value"]) for option in options["grammages"]] == STANDARD_GRAMMAGES
        assert [int(option["value"]) for option in options["laizes"]] == STANDARD_LAIZES
        assert {option["value"] for option in options["suppliers"]} == set(SUPPLIERS) - {"default"}
        assert {option["value"] for option in options["paper_types"]} == {"kraft blanchi", "kraft ecru"}

    def test_page_offers_every_catalog_value(self, flask_client):
        page = _page(flask_client)
        generator = page.split('id="gen_grammage"', 1)[1].split("gen_result", 1)[0]
        for value in (*STANDARD_GRAMMAGES, *STANDARD_LAIZES):
            assert f'<option value="{value}"' in generator
        assert '<option value="smurfit">SMURFIT (SMU)</option>' in page
        assert '<option value="100" selected>' in page and '<option value="28" selected>' in page

    @pytest.mark.parametrize("paper_type, expected_prefix", [("kraft blanchi", "KB"), ("kraft ecru", "KE")])
    def test_option_values_give_expected_code(self, flask_client, paper_type, expected_prefix):
        response = flask_client.post("/api/article-code/generate",
                                     json={"paper_type": paper_type, "grammage": "90", "laize": "32",
                                           "supplier": "smurfit"})
        assert response.get_json()["code"] == f"{expected_prefix}90L32SMU"

    def test_code_is_built_by_the_server(self, flask_client):
        page = _page(flask_client)
        assert "/api/article-code/generate" in page
        assert "${paperType}${grammage}L${laize}${supplier}" not in page
