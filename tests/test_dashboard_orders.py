"""Tests du contrôle de l'extraction dans la fiche et la liste des commandes.

La fiche montre la langue détectée, le modèle de langue et la raison de ce choix, la confiance,
les anomalies, les champs obligatoires manquants et le message d'origine (échappé) ; la liste
montre le nombre d'anomalies et la langue de chaque commande.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from tecpap.chatbot.conversation import ConversationStore

RAW_MESSAGE = "Salam,\n<script>alert('pirate')</script>\nbghit 5000 sachet fond plat"


@pytest.fixture
def db(secure_app):
    return secure_app.extensions["tecpap"].db


@pytest.fixture
def audited_order(db):
    """Commande WhatsApp avec toutes les informations de traçabilité de l'extraction."""
    return db.create_order({
        "entreprise_cliente": "Snack Atlas",
        "type_produit": "Sachets fond plat",
        "quantite": 5000,
        "source": "whatsapp",
        "whatsapp_from": "+212612345678",
        "confiance": 64,
        "langue": "darija_latin",
        "modele_llm": "gpt-4o",
        "routage_modele": "Darija : modèle d'extraction le plus fort (langue peu dotée).",
        "anomalies": ["Quantité <b>inhabituelle</b> pour ce client", "Grammage non standard : 95 g/m²"],
        "champs_manquants": ["date_livraison", "entreprise_cliente"],
        "message_brut": RAW_MESSAGE,
    })


@pytest.fixture
def plain_order(db, sample_order_data):
    """Commande sans informations de traçabilité (enregistrée avant leur ajout, par exemple)."""
    data = dict(sample_order_data)
    data.pop("confiance")
    return db.create_order(data)


def _block(html: str, element_id: str) -> str:
    match = re.search(rf'id="{element_id}"[^>]*>(.*?)</', html, flags=re.DOTALL)
    assert match, element_id
    return match.group(1).strip()


class TestOrderDetail:
    """Fiche d'une commande."""

    def test_language_model_and_routing(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        assert _block(html, "orderLanguage") == "Darija (lettres latines)"
        assert _block(html, "orderModel") == "gpt-4o"
        assert _block(html, "orderRouting") == "Darija : modèle d&#39;extraction le plus fort (langue peu dotée)."

    def test_confidence(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        assert _block(html, "orderConfidence") == "64 %"
        assert 'style="width: 64%"' in html

    def test_anomalies_are_listed_and_escaped(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        anomalies = re.search(r'id="orderAnomalies">.*?</ul>', html, flags=re.DOTALL).group(0)
        assert anomalies.count("<li") == 2
        assert "Quantité &lt;b&gt;inhabituelle&lt;/b&gt; pour ce client" in anomalies
        assert "Grammage non standard : 95 g/m²" in anomalies
        assert "<b>inhabituelle</b>" not in html
        assert 'id="extractionAudit"' in html and "border-l-amber-500" in html

    def test_missing_fields_have_french_labels(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        missing = re.search(r'id="orderMissingFields">.*?</div>', html, flags=re.DOTALL).group(0)
        assert "Date de livraison" in missing and "Client" in missing
        assert "date_livraison" not in missing

    def test_raw_message_in_an_escaped_pre_block(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        block = re.search(r'<pre[^>]*id="rawMessage">(.*?)</pre>', html, flags=re.DOTALL)
        assert block
        assert "overflow-y-auto" in re.search(r'<pre[^>]*id="rawMessage">', html).group(0)
        assert block.group(1) == "Salam,\n&lt;script&gt;alert(&#39;pirate&#39;)&lt;/script&gt;\nbghit 5000 sachet fond plat"
        assert "<script>alert('pirate')" not in html

    def test_order_without_audit_information(self, logged_in_client, plain_order):
        html = logged_in_client.get(f"/orders/{plain_order}").get_data(as_text=True)
        assert _block(html, "orderLanguage") == "Non détectée"
        assert _block(html, "orderModel") == "Aucun modèle enregistré"
        assert _block(html, "orderConfidence") == "Non calculée"
        assert 'id="orderRouting"' not in html
        assert "Aucune anomalie détectée" in html
        assert 'id="orderMissingFields"' not in html
        assert "Message d'origine non conservé" in html

    def test_confidence_is_bounded(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Café Rif", "confiance": 140, "email_id": "mail-2"})
        html = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        assert _block(html, "orderConfidence") == "100 %"

    def test_link_to_the_whatsapp_conversation(self, logged_in_client, secure_app, audited_order):
        store = ConversationStore(secure_app.extensions["tecpap"].db.connection,
                                  clock=lambda: datetime.now(timezone.utc))
        conversation = store.get_or_create("whatsapp:+212612345678")
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        assert f'href="/conversations/{conversation.id}"' in html

    def test_no_conversation_link_without_conversation(self, logged_in_client, audited_order, plain_order):
        for order_id in (audited_order, plain_order):
            html = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
            assert 'id="conversationLink"' not in html


class TestOrderList:
    """Pastilles de la liste des commandes."""

    def _row(self, html: str, order_id: int) -> str:
        rows = re.findall(r"<tr>.*?</tr>", html, flags=re.DOTALL)
        return next(row for row in rows if f'href="/orders/{order_id}"' in row)

    def test_anomaly_count_and_language(self, logged_in_client, audited_order):
        row = self._row(logged_in_client.get("/orders").get_data(as_text=True), audited_order)
        assert 'data-anomalies="2"' in row
        assert "2 anomalies" in row
        assert 'data-language="darija_latin"' in row
        assert "Darija" in row
        assert 'title="Langue détectée&nbsp;: Darija (lettres latines)"' in row

    def test_anomaly_titles_are_escaped(self, logged_in_client, audited_order):
        row = self._row(logged_in_client.get("/orders").get_data(as_text=True), audited_order)
        assert 'title="Quantité &lt;b&gt;inhabituelle&lt;/b&gt; pour ce client ; Grammage non standard : 95 g/m²"' in row

    def test_single_anomaly(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Café Rif", "email_id": "mail-3", "langue": "fr",
                                    "anomalies": ["Prix incohérent"]})
        row = self._row(logged_in_client.get("/orders").get_data(as_text=True), order_id)
        assert re.search(r"</i>1 anomalie\s*</span>", row)
        assert re.search(r"</i>FR\s*</span>", row)

    def test_no_badges_without_audit_information(self, logged_in_client, plain_order):
        row = self._row(logged_in_client.get("/orders").get_data(as_text=True), plain_order)
        assert "data-anomalies" not in row
        assert "data-language" not in row

    def test_status_filter_still_works(self, logged_in_client, db, audited_order, plain_order):
        db.update_order_status(plain_order, "validee", "admin")
        html = logged_in_client.get("/orders?status=validee").get_data(as_text=True)
        assert set(re.findall(r'href="/orders/(\d+)"', html)) == {str(plain_order)}


# ---------------------------------------------------------------------- mise en forme française

NNBSP = " "  # espace fine insécable : séparateur des milliers
NBSP = " "   # espace insécable : entre un nombre et son unité ou sa devise


class TestFrenchNumbers:
    """Quantités et prix écrits à la française (« 10 000 », « 2,5 », « 1 234,50 MAD »)."""

    def test_detail_page(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 10000, "unite": "US",
                                    "prix_unitaire": 1234.5, "prix_total": 12345000, "email_id": "mail-n1"})
        html = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        assert _block(html, "orderQuantity") == f"10{NNBSP}000{NBSP}US"
        assert _block(html, "orderUnitPrice") == f"1{NNBSP}234,50{NBSP}MAD"
        assert _block(html, "orderTotalPrice") == f"12{NNBSP}345{NNBSP}000,00{NBSP}MAD"
        assert "10,000" not in html and "1,234.50" not in html

    def test_decimal_quantity_is_not_rounded(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 2.5, "unite": "kg",
                                    "email_id": "mail-n2"})
        html = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        assert _block(html, "orderQuantity") == f"2,5{NBSP}kg"
        row = TestOrderList()._row(logged_in_client.get("/orders").get_data(as_text=True), order_id)
        assert ">2,5<" in row

    def test_list_and_home_page(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 10000, "email_id": "mail-n3"})
        row = TestOrderList()._row(logged_in_client.get("/orders").get_data(as_text=True), order_id)
        assert f">10{NNBSP}000<" in row
        assert f"10{NNBSP}000" in logged_in_client.get("/").get_data(as_text=True)


class TestOrderReferences:
    """Référence affichée comme le client la reçoit : numéro de commande, sinon « CMD-<id> »."""

    def test_reference_without_number(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "email_id": "mail-r1"})
        detail = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        assert _block(detail, "orderReference") == f"CMD-{order_id}"
        assert f"<title>Commande CMD-{order_id} - TECPAP</title>" in detail
        row = TestOrderList()._row(logged_in_client.get("/orders").get_data(as_text=True), order_id)
        assert f"CMD-{order_id}" in row and f"#{order_id}" not in row
        assert f"CMD-{order_id}" in logged_in_client.get("/").get_data(as_text=True)

    def test_order_number_comes_first(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "numero_commande": "BC-2026-0417",
                                    "email_id": "mail-r2"})
        detail = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        assert _block(detail, "orderReference") == "BC-2026-0417"


# ---------------------------------------------------------------------- formulaire de modification

def _select(html: str, name: str) -> list[tuple[str, bool]]:
    block = re.search(rf'<select id="edit_{name}" name="{name}".*?</select>', html, flags=re.DOTALL).group(0)
    return [(value, bool(selected)) for value, selected in re.findall(r'<option value="([^"]*)" ?(selected)?', block)]


class TestEditForm:
    """Le formulaire ne remplace jamais une valeur extraite par la première option d'une liste."""

    def test_unit_choices_cover_the_canonical_units(self):
        from tecpap.extraction.rules import CANONICAL_UNITS
        from tecpap.web.routes import pages

        assert {value for value, _ in pages.UNIT_CHOICES} == set(CANONICAL_UNITS)

    @pytest.mark.parametrize("unit", ["paquet", "carton", "kg", "pièces", "US"])
    def test_canonical_unit_is_selected(self, logged_in_client, db, unit):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "unite": unit, "email_id": "mail-u1"})
        options = _select(logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True), "unite")
        assert [value for value, selected in options if selected] == [unit]
        assert [value for value, _ in options].count(unit) == 1

    def test_unknown_unit_is_kept(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "unite": "rouleaux", "email_id": "mail-u2"})
        options = _select(logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True), "unite")
        assert [value for value, selected in options if selected] == ["rouleaux"]

    def test_missing_unit_is_not_replaced(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "email_id": "mail-u3"})
        db.update_order(order_id, {"unite": None})
        options = _select(logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True), "unite")
        assert options[0] == ("", True)

    @pytest.mark.parametrize("bag_type", ["SAC FOND CARRE", "Kraft", "KRAFT"])
    def test_bag_type_is_kept(self, logged_in_client, db, bag_type):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "type_sac": bag_type, "email_id": "mail-b1"})
        options = _select(logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True), "type_sac")
        assert [value for value, selected in options if selected] == [bag_type]
        assert [value for value, _ in options].count(bag_type) == 1

    def test_decimal_quantities_and_prices_are_accepted(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 2.5, "email_id": "mail-q1"})
        html = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        for name in ("quantite", "prix_unitaire", "prix_total"):
            field = re.search(rf'<input type="number" id="edit_{name}"[^>]*>', html).group(0)
            assert 'step="any"' in field and 'min="0"' in field, name
        assert 'value="2.5"' in re.search(r'<input type="number" id="edit_quantite"[^>]*>', html).group(0)

    def test_every_field_has_a_label(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "email_id": "mail-l1"})
        html = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        form = re.search(r'<form id="editForm".*?</form>', html, flags=re.DOTALL).group(0)
        controls = re.findall(r'<(?:input|select)[^>]*\bid="(edit_[a-z_]+)"', form)
        assert len(controls) == 15
        for control in controls:
            assert f'<label for="{control}"' in form, control

    def test_only_changed_fields_are_sent(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "email_id": "mail-c1"})
        html = logged_in_client.get(f"/orders/{order_id}").get_data(as_text=True)
        assert "const initialValues = Object.fromEntries(new FormData(editForm));" in html
        assert "if (value !== initialValues[name]) changes[name] = value;" in html

    def test_partial_update_keeps_the_other_fields(self, logged_in_client, db):
        order_id = db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 20, "unite": "paquet",
                                    "type_sac": "SAC FOND CARRE", "email_id": "mail-c2"})
        response = logged_in_client.post(f"/api/orders/{order_id}/update", json={"code_article": "KB100L28MON"})
        assert response.status_code == 200
        order = db.get_order(order_id)
        assert (order["code_article"], order["unite"], order["type_sac"]) == ("KB100L28MON", "paquet", "SAC FOND CARRE")


# ---------------------------------------------------------------------- validation et rejet

class TestDecisionFeedback:
    """Le résultat d'une validation ou d'un rejet dit si le client a été prévenu."""

    def test_detail_page_reads_the_notification_flags(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        assert "return data.email_sent === false && data.whatsapp_sent === false;" in html
        assert "client non prévenu" in html
        assert "La commande a été validée avec succès" not in html

    def test_list_page_reads_the_notification_flags(self, logged_in_client, audited_order):
        html = logged_in_client.get("/orders").get_data(as_text=True)
        assert "data.email_sent === false && data.whatsapp_sent === false" in html

    def test_rejection_reason_is_presented_as_a_message_to_the_client(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        modal = re.search(r'<div id="rejectModal".*?</textarea>', html, flags=re.DOTALL).group(0)
        assert '<label for="rejectReason"' in modal
        assert "Motif communiqué au client (email et WhatsApp)" in modal
        assert "Ce texte est envoyé tel quel au client" in modal
        listing = logged_in_client.get("/orders").get_data(as_text=True)
        assert "envoyé tel quel au client" in listing
        assert "const reason = answer.trim();" in listing

    def test_modals_are_dialogs(self, logged_in_client, audited_order):
        html = logged_in_client.get(f"/orders/{audited_order}").get_data(as_text=True)
        for modal in ("validateModal", "rejectModal", "resultModal"):
            tag = re.search(rf'<div id="{modal}"[^>]*>', html).group(0)
            assert 'role="dialog"' in tag and 'aria-modal="true"' in tag and "aria-labelledby=" in tag

    def test_validation_response_reports_each_channel(self, logged_in_client, plain_order):
        data = logged_in_client.post(f"/api/orders/{plain_order}/validate", json={}).get_json()
        assert data["email_sent"] is False and data["whatsapp_sent"] is False


# ---------------------------------------------------------------------- filtres de la liste

class TestOrderListFilters:
    """Filtres par canal (``?source=``) et recherche (``?q=``)."""

    @pytest.fixture
    def orders(self, db):
        whatsapp = db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 2000, "source": "whatsapp",
                                    "whatsapp_from": "whatsapp:+212612345678"})
        older_whatsapp = db.create_order({"entreprise_cliente": "Café Rif", "quantite": 300, "email_id": "wa-1",
                                          "email_subject": "WhatsApp de +212600000001"})
        email = db.create_order({"entreprise_cliente": "Boulangerie Nour", "quantite": 500, "source": "email",
                                 "email_id": "mail-f1", "email_from": "commandes@nour.ma"})
        return {"whatsapp": whatsapp, "older_whatsapp": older_whatsapp, "email": email}

    @staticmethod
    def _ids(html: str) -> set[int]:
        return {int(value) for value in re.findall(r'href="/orders/(\d+)"', html)}

    def test_source_filter(self, logged_in_client, orders):
        whatsapp = logged_in_client.get("/orders?source=whatsapp").get_data(as_text=True)
        assert self._ids(whatsapp) == {orders["whatsapp"], orders["older_whatsapp"]}
        assert self._ids(logged_in_client.get("/orders?source=email").get_data(as_text=True)) == {orders["email"]}
        assert len(self._ids(logged_in_client.get("/orders?source=fax").get_data(as_text=True))) == 3

    def test_source_filter_shows_in_the_filter_bar(self, logged_in_client, orders):
        html = logged_in_client.get("/orders?source=whatsapp").get_data(as_text=True)
        bar = re.search(r'id="sourceFilters">.*?</div>', html, flags=re.DOTALL).group(0)
        assert re.search(r'href="/orders\?source=whatsapp" aria-current="true"', bar)
        assert 'href="/orders?status=en_attente&amp;source=whatsapp"' in html
        assert 'id="exportFilterNote"' in html

    @pytest.mark.parametrize("query, expected", [
        ("CMD-{whatsapp}", {"whatsapp"}), ("cmd {email}", {"email"}), ("#{email}", {"email"}),
        ("snack", {"whatsapp"}), ("CAFE RIF", {"older_whatsapp"}), ("nour.ma", {"email"}),
        ("0612345678", {"whatsapp"}), ("06 12 34 56", {"whatsapp"}), ("+212 6 12 34 56 78", {"whatsapp"}),
        ("introuvable", set()), ("999", set()),
    ])
    def test_search(self, logged_in_client, orders, query, expected):
        text = query.format(**orders)
        html = logged_in_client.get("/orders", query_string={"q": text}).get_data(as_text=True)
        assert self._ids(html) == {orders[name] for name in expected}
        assert 'id="searchFilter"' in html
        assert f'name="q" value="{text}"' in html

    def test_search_finds_the_client_phone(self, logged_in_client, db, orders):
        db.attach_phone_to_client(db.get_order(orders["email"])["client_id"], "0698765432")
        html = logged_in_client.get("/orders?q=0698765432").get_data(as_text=True)
        assert self._ids(html) == {orders["email"]}

    def test_search_is_escaped(self, logged_in_client, orders):
        html = logged_in_client.get("/orders", query_string={"q": "<script>x</script>"}).get_data(as_text=True)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;x&lt;/script&gt;" in html

    def test_filters_combine(self, logged_in_client, db, orders):
        db.update_order_status(orders["whatsapp"], "validee", "admin")
        html = logged_in_client.get("/orders?status=en_attente&source=whatsapp").get_data(as_text=True)
        assert self._ids(html) == {orders["older_whatsapp"]}

    def test_client_email_is_shown(self, logged_in_client, db, orders):
        email = db.get_order(orders["email"])["client_email"]
        row = TestOrderList()._row(logged_in_client.get("/orders").get_data(as_text=True), orders["email"])
        assert email and email in row


class TestOrderMatching:
    """Règles de :func:`tecpap.web.routes.pages.order_matches` et :func:`~tecpap.web.routes.pages.order_source`."""

    def test_order_source(self):
        from tecpap.web.routes.pages import order_source

        assert order_source({"source": "whatsapp"}) == "whatsapp"
        assert order_source({"source": "email", "email_subject": "WhatsApp de +2126"}) == "whatsapp"
        assert order_source({"source": "email", "email_subject": "Commande"}) == "email"
        assert order_source({}) == "email"

    def test_reference_search_is_exact(self):
        from tecpap.web.routes.pages import order_matches

        assert order_matches({"id": 4}, "CMD-4")
        assert not order_matches({"id": 42}, "CMD-4")
        assert order_matches({"id": 42, "numero_commande": "BC-42"}, "bc-4")

    def test_empty_search_matches_everything(self):
        from tecpap.web.routes.pages import order_matches

        assert order_matches({"id": 1}, "   ")
