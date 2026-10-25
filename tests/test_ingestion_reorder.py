"""Renouvellements : l'historique n'est lu que pour l'expéditeur du message (tecpap.extraction.extractor).

Le modèle de langue est remplacé par ``FakeLLMClient`` ; la base est une base SQLite temporaire.
Cas couverts : un expéditeur ne reçoit jamais la dernière commande d'un autre client ; seules les
commandes validées servent d'historique ; un autre type de produit ne reprend ni désignation ni
prix ; une extraction par règles seules garde une confiance basse.
"""

from __future__ import annotations

import pytest

from tecpap.extraction.extractor import (
    REORDER_WITHOUT_HISTORY_ANOMALY,
    RULES_FALLBACK_ANOMALY,
    RULES_MAX_CONFIDENCE,
    DataExtractor,
    sender_identity,
)
from tests.fakes import FakeLLMClient

ATLAS_EMAIL = "Achats Atlas <achats@boulangerie-atlas.ma>"
ATLAS_PHONE = "whatsapp:+212612345678"
ATTACKER_EMAIL = "attaquant@exemple.invalid"
LOW_CONFIDENCE_THRESHOLD = 60


def _validated(db, *, client="Boulangerie Atlas", email_from=None, whatsapp_from=None, status="validee",
               product="Sachets fond plat", quantity=48000, **fields) -> int:
    """Commande d'un client, validée par défaut, envoyée depuis une adresse ou un numéro."""
    order_id = db.create_order({
        "entreprise_cliente": client, "type_produit": product, "quantite": quantity, "unite": "pièces",
        "nature_produit": "Sachet kraft 24x32 imprimé logo Atlas", "prix_unitaire": 0.42,
        "prix_total": quantity * 0.42, "devise": "MAD", "email_from": email_from, "whatsapp_from": whatsapp_from,
        "source": "whatsapp" if whatsapp_from else "email", "message_sid": f"sid-{client}-{email_from}-{status}",
        **fields,
    })
    if status != "en_attente":
        db.update_order_status(order_id, status)
    return order_id


def _llm_returning(**fields) -> FakeLLMClient:
    return FakeLLMClient(chat_json=[{"est_bon_commande": True, "confiance": 80, **fields}])


def _extract_email(db, settings, llm, sender: str, body: str = "Bonjour, comme d'habitude pour Atlas") -> dict:
    extractor = DataExtractor(db_manager=db, settings=settings, llm=llm)
    return extractor.extract_from_email({"subject": "Commande", "from": sender, "date": "2026-10-25", "body": body})


HISTORY_VALUES = ("nature_produit", "quantite", "prix_unitaire", "prix_total")


class TestHistoryIsBoundToTheSender:
    def test_another_sender_citing_the_client_gets_nothing(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL)
        for llm in (FakeLLMClient(available=False), _llm_returning(entreprise_cliente="Atlas")):
            result = _extract_email(temp_db, test_settings, llm, ATTACKER_EMAIL)
            assert all(not result.get(name) for name in HISTORY_VALUES), result
            assert not result.get("filled_from_history")
            assert result["entreprise_cliente"] in (None, "Atlas")
            assert any(item.startswith(REORDER_WITHOUT_HISTORY_ANOMALY) for item in result["anomalies"])

    @pytest.mark.parametrize("cited", ["%", "_", "a", "%a%", "Boulangerie%"])
    def test_wildcards_and_fragments_in_the_cited_name_match_nothing(self, temp_db, test_settings, cited):
        _validated(temp_db, email_from=ATLAS_EMAIL)
        result = _extract_email(temp_db, test_settings, _llm_returning(entreprise_cliente=cited), ATTACKER_EMAIL)
        assert not result.get("quantite") and not result.get("filled_from_history")
        assert result["entreprise_cliente"] == cited

    def test_the_senders_own_history_fills_the_missing_fields(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL)
        result = _extract_email(temp_db, test_settings, _llm_returning(entreprise_cliente="atlas"),
                                "ACHATS@Boulangerie-Atlas.ma")
        assert result["entreprise_cliente"] == "Boulangerie Atlas"
        assert result["quantite"] == 48000
        assert result["nature_produit"] == "Sachet kraft 24x32 imprimé logo Atlas"
        assert result["filled_from_history"] is True
        assert not any(item.startswith(REORDER_WITHOUT_HISTORY_ANOMALY) for item in result["anomalies"])

    def test_whatsapp_number_formats_are_the_same_sender(self, temp_db, test_settings):
        _validated(temp_db, whatsapp_from=ATLAS_PHONE)
        extractor = DataExtractor(db_manager=temp_db, settings=test_settings, llm=FakeLLMClient(available=False))
        for sender in ("whatsapp:+212612345678", "+212612345678", "0612345678", "+212 6 12 34 56 78"):
            result = extractor.extract_text("comme d'habitude pour Atlas", sender=sender)
            assert result["quantite"] == 48000, sender
        other = extractor.extract_text("comme d'habitude pour Atlas", sender="whatsapp:+212699999999")
        assert not other.get("quantite") and not other.get("filled_from_history")

    def test_a_similar_client_name_never_replaces_the_cited_name(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL)
        result = _extract_email(temp_db, test_settings, FakeLLMClient(available=False), "nouveau@najah.ma",
                                "Bonjour, comme d'habitude pour Boulangerie Najah")
        assert result["entreprise_cliente"] == "Boulangerie Najah"
        assert not result.get("quantite")
        near = [item for item in result["anomalies"] if item.startswith(REORDER_WITHOUT_HISTORY_ANOMALY)]
        assert near and "Boulangerie Atlas" in near[0]

    def test_the_senders_orders_for_another_client_are_not_used_for_the_cited_name(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL)
        result = _extract_email(temp_db, test_settings, FakeLLMClient(available=False), ATLAS_EMAIL,
                                "Bonjour, comme d'habitude pour Boulangerie Najah")
        assert result["entreprise_cliente"] == "Boulangerie Najah"
        assert not result.get("quantite")

    def test_the_assistant_extractor_never_reads_the_history(self, temp_db, test_settings):
        _validated(temp_db, whatsapp_from=ATLAS_PHONE)
        extractor = DataExtractor(db_manager=temp_db, settings=test_settings, llm=FakeLLMClient(available=False))
        result = extractor.assistant_extractor()("comme d'habitude pour Atlas", ATLAS_PHONE)
        assert not result.get("quantite") and not result.get("filled_from_history")

    def test_get_sender_last_order_needs_a_readable_sender(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL)
        extractor = DataExtractor(db_manager=temp_db, settings=test_settings, llm=FakeLLMClient())
        assert extractor.get_sender_last_order(None, "email") is None
        assert extractor.get_sender_last_order("pas une adresse", "email") is None
        assert extractor.get_sender_last_order("", "whatsapp") is None
        assert DataExtractor(settings=test_settings, llm=FakeLLMClient()).get_sender_last_order(
            ATLAS_EMAIL, "email") is None


class TestSenderIdentity:
    @pytest.mark.parametrize(("sender", "channel", "expected"), [
        ("Achats <Achats@Atlas.MA>", "email", "achats@atlas.ma"),
        ("achats@atlas.ma", "email", "achats@atlas.ma"),
        ("sans arobase", "email", None),
        ("whatsapp:+212612345678", "whatsapp", "+212612345678"),
        ("0612345678", "whatsapp", "+212612345678"),
        (None, "email", None),
        ("   ", "whatsapp", None),
    ])
    def test_identity(self, sender, channel, expected):
        assert sender_identity(sender, channel) == expected


class TestOnlyValidatedOrdersServeAsHistory:
    @pytest.mark.parametrize("status", ["rejetee", "supprimee", "en_attente"])
    def test_orders_that_are_not_validated_are_never_copied(self, temp_db, test_settings, status):
        _validated(temp_db, email_from=ATLAS_EMAIL, status=status, quantity=900000)
        result = _extract_email(temp_db, test_settings, FakeLLMClient(available=False), ATLAS_EMAIL,
                                "Bonjour, comme d'habitude pour Boulangerie Atlas")
        assert not result.get("quantite") and not result.get("filled_from_history")
        assert any(item.startswith(REORDER_WITHOUT_HISTORY_ANOMALY) for item in result["anomalies"])

    def test_the_latest_validated_order_wins(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL, quantity=1000, message_sid="vieille")
        _validated(temp_db, email_from=ATLAS_EMAIL, quantity=2500, message_sid="recente")
        _validated(temp_db, email_from=ATLAS_EMAIL, quantity=999000, status="rejetee", message_sid="rejetee")
        result = _extract_email(temp_db, test_settings, FakeLLMClient(available=False), ATLAS_EMAIL,
                                "Bonjour, comme d'habitude pour Boulangerie Atlas")
        assert result["quantite"] == 2500

    def test_the_total_price_is_not_kept_for_another_quantity(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL, quantity=5000, prix_unitaire=None, prix_total=10000)
        result = _extract_email(temp_db, test_settings, FakeLLMClient(available=False), ATLAS_EMAIL,
                                "Bonjour, comme d'habitude pour Boulangerie Atlas, 2000 sacs")
        assert result["quantite"] == 2000
        assert not result.get("prix_total")

    def test_the_total_price_follows_a_quantity_taken_from_the_history(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL, quantity=5000, prix_unitaire=None, prix_total=10000)
        result = _extract_email(temp_db, test_settings, FakeLLMClient(available=False), ATLAS_EMAIL,
                                "Bonjour, comme d'habitude pour Boulangerie Atlas")
        assert (result["quantite"], result["prix_total"]) == (5000, 10000)


class TestAnotherProductKeepsNothingOfTheOldOne:
    def test_designation_and_prices_are_not_copied_for_another_product_type(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL, nature_produit="SACHET FOND PLAT KRAFT BRUN 80G L32")
        llm = _llm_returning(entreprise_cliente="Boulangerie Atlas", type_produit="Sac fond carré avec poignées plates",
                             quantite=3000)
        result = _extract_email(temp_db, test_settings, llm, ATLAS_EMAIL,
                                "comme d'habitude mais en sac fond carré avec poignées plates, 3000 pièces")
        assert result["type_produit"] == "Sac fond carré avec poignées plates"
        assert not result.get("nature_produit") and not result.get("prix_unitaire")
        # Le code article (KE80L32) ne se déduit pas de l'ancienne désignation.
        assert result["code_article"] != "KE80L32"

    def test_the_same_product_type_keeps_the_history(self, temp_db, test_settings):
        _validated(temp_db, email_from=ATLAS_EMAIL)
        llm = _llm_returning(entreprise_cliente="Boulangerie Atlas", type_produit="Sachets fond plat", quantite=3000)
        result = _extract_email(temp_db, test_settings, llm, ATLAS_EMAIL, "comme d'habitude, 3000 pièces")
        assert result["nature_produit"] == "Sachet kraft 24x32 imprimé logo Atlas"
        assert result["prix_unitaire"] == 0.42
        assert result["code_article"] == "KE80"


class TestRulesOnlyExtractionStaysUnderTheAlertThreshold:
    def test_confidence_is_capped_without_a_model(self, test_settings):
        extractor = DataExtractor(settings=test_settings, llm=FakeLLMClient(available=False))
        result = extractor.extract_text("commande de 20 cartons de sacs SOS pour Boulangerie Atlas")
        assert RULES_FALLBACK_ANOMALY in result["anomalies"]
        assert result["modele_llm"] is None
        assert result["confiance"] <= RULES_MAX_CONFIDENCE < LOW_CONFIDENCE_THRESHOLD

    def test_failed_model_call_gives_the_same_cap(self, test_settings):
        extractor = DataExtractor(settings=test_settings, llm=FakeLLMClient(chat_json=[None]))
        result = extractor.extract_text("ana Snack Atlas, bghit 3000 sachet fond plat")
        assert result["modele_llm"] is None
        assert result["confiance"] <= RULES_MAX_CONFIDENCE

    def test_a_model_extraction_is_not_capped(self, test_settings):
        llm = _llm_returning(entreprise_cliente="Snack Atlas", type_produit="Sachets fond plat", quantite=3000,
                             confiance=95)
        result = DataExtractor(settings=test_settings, llm=llm).extract_text("ana Snack Atlas, bghit 3000 sachet")
        assert result["confiance"] > RULES_MAX_CONFIDENCE
