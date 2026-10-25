"""Tests du schéma de la commande extraite par le modèle de langue (tecpap.extraction.schema)."""

from __future__ import annotations

import random
import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tecpap.extraction import rules, schema
from tecpap.extraction.prompts import build_extraction_prompt
from tecpap.extraction.schema import ORDER_FIELDS, ExtractedOrder, parse_llm_order


class TestFields:
    """Les champs du schéma sont ceux demandés au modèle."""

    def test_fields_match_the_extraction_prompt(self):
        prompt = build_extraction_prompt("contenu")
        requested = re.findall(r"^- (\w+): (?:string|number|boolean)", prompt, flags=re.MULTILINE)
        assert tuple(requested) == ORDER_FIELDS

    def test_fields_match_the_rules(self):
        assert ORDER_FIELDS == tuple(rules.ORDER_FIELD_KINDS)

    def test_every_field_is_optional(self):
        order = ExtractedOrder()
        assert all(getattr(order, name) is None for name in ORDER_FIELDS)

    def test_model_is_immutable(self):
        order = ExtractedOrder(quantite="10")
        with pytest.raises(ValidationError):
            order.quantite = 5


class TestNumbers:
    """Nombres écrits en texte, lus comme les quantités des règles."""

    @pytest.mark.parametrize("value, expected", [
        (10000, 10000.0), ("10000", 10000.0), ("10 000", 10000.0), ("10.000", 10000.0), ("10,000", 10000.0),
        ("10k", 10000.0), ("2 mille", 2000.0), ("1,5 million", 1_500_000.0), ("٣٠٠٠", 3000.0), ("jouj alaf", 2000.0),
        ("3000 pcs", 3000.0), ("0", 0.0), ("-5", -5.0), (Decimal("12.5"), 12.5),
    ])
    def test_quantity(self, value, expected):
        assert parse_llm_order({"quantite": value}) == {"quantite": expected}

    @pytest.mark.parametrize("value, expected", [
        ("tlata d alaf", 3000.0), ("3 d alaf", 3000.0), ("alf w khamsmya", 1500.0), ("تلاتة د الاف", 3000.0),
        ("deux mille cinq cents", 2500.0), ("eighteen hundred", 1800.0), ("2 mille 500", 2500.0),
        ("environ 800", 800.0),
    ])
    def test_quantity_written_in_words_or_approximately(self, value, expected):
        assert parse_llm_order({"quantite": value}) == {"quantite": expected}

    def test_unit_is_kept_as_written_then_normalized_by_the_rules(self):
        parsed = parse_llm_order({"Quantité": "12", "Unité": " cartons "})
        assert parsed == {"quantite": 12.0, "unite": "cartons"}
        assert rules.normalize_order(parsed).data["unite"] == "carton"

    @pytest.mark.parametrize("value", ["beaucoup", "", "15/11/2026", "2000 à 3000", True, [], {}, float("nan"),
                                       float("inf"), None])
    def test_invalid_quantity_is_dropped(self, value):
        assert parse_llm_order({"quantite": value}) == {}

    @pytest.mark.parametrize("field, value, expected", [
        ("prix_unitaire", "0,45", 0.45), ("prix_unitaire", "2,10 DH", 2.1), ("prix_total", "21 000", 21000.0),
        ("confiance", "85", 85.0), ("confiance", 0.9, 0.9), ("confiance", "85 %", 85.0),
    ])
    def test_prices_and_confidence(self, field, value, expected):
        assert parse_llm_order({field: value})[field] == expected

    @pytest.mark.parametrize("value, expected", [("80", 80), ("80 g", 80), (80.0, 80), (80.5, 80.5), ("100g", 100)])
    def test_grammage_is_an_integer_when_round(self, value, expected):
        parsed = parse_llm_order({"grammage": value})["grammage"]
        assert parsed == expected
        assert type(parsed) is type(expected)

    @pytest.mark.parametrize("value, expected", [("1", 1), (2, 2), (3.0, 3)])
    def test_order_line(self, value, expected):
        assert parse_llm_order({"ligne_commande": value}) == {"ligne_commande": expected}

    @pytest.mark.parametrize("value", [1.5, "un et demi", "abc"])
    def test_invalid_order_line(self, value):
        assert parse_llm_order({"ligne_commande": value}) == {}


class TestBooleans:
    """Indicateur « est un bon de commande »."""

    @pytest.mark.parametrize("value", [True, "true", "True", "oui", "OUI", "yes", 1, "1"])
    def test_true(self, value):
        assert parse_llm_order({"est_bon_commande": value}) == {"est_bon_commande": True}

    @pytest.mark.parametrize("value", [False, "false", "non", "no", 0, "0"])
    def test_false(self, value):
        assert parse_llm_order({"est_bon_commande": value}) == {"est_bon_commande": False}

    @pytest.mark.parametrize("value", ["peut-être", 2, [], None, "null"])
    def test_unreadable(self, value):
        assert parse_llm_order({"est_bon_commande": value}) == {}


class TestTexts:
    """Nettoyage des textes."""

    def test_whitespace_and_control_characters(self):
        assert parse_llm_order({"entreprise_cliente": "  Snack\x00  Atlas \n"}) == {"entreprise_cliente": "Snack Atlas"}

    @pytest.mark.parametrize("value", ["", "  ", "null", "None", "N/A", "inconnu", "Non spécifié"])
    def test_null_like_texts_are_dropped(self, value):
        assert parse_llm_order({"entreprise_cliente": value, "devise": value}) == {}

    def test_numbers_become_text(self):
        assert parse_llm_order({"numero_commande": 2291, "code_client": 231.0}) == {
            "numero_commande": "2291", "code_client": "231"}

    def test_long_texts_are_truncated(self):
        parsed = parse_llm_order({"entreprise_cliente": "A" * 1000, "numero_commande": "B" * 1000,
                                  "informations_supplementaires": "C" * 5000})
        assert (len(parsed["entreprise_cliente"]), len(parsed["numero_commande"]),
                len(parsed["informations_supplementaires"])) == (rules.NAME_MAX, rules.SHORT_TEXT_MAX,
                                                                 rules.LONG_TEXT_MAX)

    def test_free_text_keeps_line_breaks(self):
        assert parse_llm_order({"informations_supplementaires": "logo  noir\n\nlivraison  quai 3"}) == {
            "informations_supplementaires": "logo noir\nlivraison quai 3"}

    def test_list_of_texts(self):
        assert parse_llm_order({"informations_supplementaires": ["logo", "2 couleurs"]}) == {
            "informations_supplementaires": "logo; 2 couleurs"}

    @pytest.mark.parametrize("value, expected", [(True, "oui"), (False, "non"), ("logo 2 couleurs", "logo 2 couleurs")])
    def test_printing(self, value, expected):
        assert parse_llm_order({"impression_client": value}) == {"impression_client": expected}

    @pytest.mark.parametrize("value", [True, {"nom": "Atlas"}, [["imbriqué"]], [], object()])
    def test_unusable_texts(self, value):
        assert "entreprise_cliente" not in parse_llm_order({"entreprise_cliente": value})

    def test_nested_lists_are_ignored(self):
        assert parse_llm_order({"entreprise_cliente": ["Snack Atlas", ["imbriqué"]]}) == {
            "entreprise_cliente": "Snack Atlas"}

    def test_values_are_not_normalized_by_the_schema(self):
        # Le contrôle métier (catalogue, unités, dates ISO) est fait par rules.normalize_order.
        assert parse_llm_order({"type_produit": "sachets", "unite": "pcs", "date_livraison": "15/11/2026"}) == {
            "type_produit": "sachets", "unite": "pcs", "date_livraison": "15/11/2026"}

    @pytest.mark.parametrize("value, expected", [(date(2026, 11, 15), "2026-11-15"),
                                                 (datetime(2026, 11, 15, 9, 30), "2026-11-15")])
    def test_date_objects(self, value, expected):
        assert parse_llm_order({"date_livraison": value}) == {"date_livraison": expected}


class TestKeysAndCurrency:
    """Clés reconnues et devise lue dans les prix."""

    def test_unknown_keys_are_ignored(self):
        assert parse_llm_order({"quantite": 5, "commentaire": "x", "is_reorder": True, 3: "y", None: 1}) == {
            "quantite": 5.0}

    @pytest.mark.parametrize("key", ["Quantité", "QUANTITE", "quantité", " quantite "])
    def test_key_variants(self, key):
        assert parse_llm_order({key: "10"}) == {"quantite": 10.0}

    def test_key_with_dash_or_space(self):
        assert parse_llm_order({"date-livraison": "2026-11-15", "Est Bon Commande": "oui"}) == {
            "date_livraison": "2026-11-15", "est_bon_commande": True}

    @pytest.mark.parametrize("raw", [{"Quantité": "999", "quantite": "10"}, {"quantite": "10", "Quantité": "999"}])
    def test_exact_key_wins(self, raw):
        assert parse_llm_order(raw) == {"quantite": 10.0}

    @pytest.mark.parametrize("price, currency", [("2,10 DH", "MAD"), ("1,95 €", "EUR"), ("3 $", "USD")])
    def test_currency_read_in_a_price(self, price, currency):
        assert parse_llm_order({"prix_unitaire": price})["devise"] == currency

    def test_explicit_currency_wins(self):
        assert parse_llm_order({"prix_unitaire": "1,95 €", "devise": "MAD"})["devise"] == "MAD"

    def test_no_currency_invented(self):
        assert "devise" not in parse_llm_order({"prix_unitaire": "1,95"})

    @pytest.mark.parametrize("raw", [None, [], [{"quantite": 5}], "quantite: 5", 42, True])
    def test_non_mapping(self, raw):
        assert parse_llm_order(raw) == {}


class TestRobustness:
    """Aucune exception, quelle que soit la réponse du modèle."""

    def test_random_values_never_raise(self):
        generator = random.Random(1024)
        values = [None, "", "abc", "10 000", "-5", "٣٠٠٠", "oui", 0, 1, -1, 1e308, float("nan"), True, [], {},
                  ["a", 1], {"x": [1, 2]}, object(), Decimal("1E+5000"), "1" * 10000, "\x00", date(2026, 1, 1)]
        for _ in range(500):
            raw = {name: generator.choice(values) for name in ORDER_FIELDS if generator.random() < 0.7}
            parsed = parse_llm_order(raw)
            assert set(parsed) <= set(ORDER_FIELDS)
            assert None not in parsed.values()

    def test_field_by_field_validation_when_a_field_fails(self, monkeypatch):
        def broken_parse(value):
            return "pas un nombre" if value == "casse" else rules.parse_quantity(value)

        monkeypatch.setattr(schema, "parse_quantity", broken_parse)
        assert parse_llm_order({"quantite": "casse", "prix_total": "12", "entreprise_cliente": "Snack Atlas"}) == {
            "prix_total": 12.0, "entreprise_cliente": "Snack Atlas"}

    def test_complete_llm_answer(self):
        raw = {
            "numero_commande": "BC-2026-0417", "ligne_commande": 1, "site_vente": "SXP", "code_client": None,
            "entreprise_cliente": "Boulangerie Lfarah Derb Omar", "code_article": "KE70", "type_produit":
            "Sachets fond plat", "nature_produit": "Sachets fond plat kraft écru 70 g 18x8x30", "quantite": "10 000",
            "unite": "unités", "date_commande": None, "date_livraison": "2026-11-15", "commercial": "DIVERS",
            "type_sac": "KRAFT", "format_sac": "18x8x30", "type_papier": "kraft écru", "grammage": 70, "laize": None,
            "impression_client": None, "prix_unitaire": "0,45", "prix_total": None, "devise": "MAD",
            "informations_supplementaires": "", "confiance": 92, "est_bon_commande": "true",
        }
        assert parse_llm_order(raw) == {
            "numero_commande": "BC-2026-0417", "ligne_commande": 1, "site_vente": "SXP",
            "entreprise_cliente": "Boulangerie Lfarah Derb Omar", "code_article": "KE70",
            "type_produit": "Sachets fond plat", "nature_produit": "Sachets fond plat kraft écru 70 g 18x8x30",
            "quantite": 10000.0, "unite": "unités", "date_livraison": "2026-11-15", "commercial": "DIVERS",
            "type_sac": "KRAFT", "format_sac": "18x8x30", "type_papier": "kraft écru", "grammage": 70,
            "prix_unitaire": 0.45, "devise": "MAD", "confiance": 92.0, "est_bon_commande": True,
        }

    def test_parsed_order_feeds_the_rules(self):
        parsed = parse_llm_order({"quantite": "10 000", "type_produit": "sachets", "unite": "unités",
                                  "entreprise_cliente": "Snack Atlas", "confiance": 90})
        result = rules.normalize_order(parsed, today=date(2026, 10, 24))
        assert (result.data["type_produit"], result.data["unite"], result.confidence) == (
            "Sachets fond plat", "pièces", 95)
