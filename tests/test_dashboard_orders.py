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
        assert 'title="Langue détectée : Darija (lettres latines)"' in row

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
