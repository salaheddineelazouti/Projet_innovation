"""Tests des règles déterministes de lecture, de normalisation et de contrôle (tecpap.extraction.rules)."""

from __future__ import annotations

import json
import random
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tecpap import catalog
from tecpap.extraction import rules
from tecpap.extraction.rules import (
    NormalizationResult,
    clean_text,
    detect_reorder,
    extract_client_name,
    fold,
    normalize_currency,
    normalize_date,
    normalize_order,
    normalize_phone,
    normalize_product_type,
    normalize_unit,
    parse_quantity,
    prepare_text,
    rule_based_extract,
    to_bool,
)

TODAY = date(2026, 10, 24)
DATASET = Path(__file__).resolve().parents[1] / "evaluation" / "dataset" / "messages.jsonl"

SFP = "Sachets fond plat"
SFCSP = "Sac fond carré sans poignées"
SFCPP = "Sac fond carré avec poignées plates"
SFCPT = "Sac fond carré avec poignées torsadées"


# ---------------------------------------------------------------- préparation du texte

class TestTextPreparation:
    """Formes préparée et de comparaison du texte."""

    def test_fold_removes_accents_and_case(self):
        assert fold("Poignées TORSADÉES") == "poignees torsadees"

    def test_fold_removes_arabic_diacritics_and_hamza(self):
        assert fold("أَلْفَيْن") == "الفين"
        assert fold("آلاف") == "الاف"

    def test_fold_unifies_arabic_letter_variants(self):
        assert fold("مقهى") == fold("مقهي")

    def test_prepare_converts_arabic_indic_digits(self):
        assert prepare_text("٣٠٠٠ و ۱۲") == "3000 و 12"

    def test_prepare_keeps_case_and_accents(self):
        assert prepare_text("Épicerie Atlas") == "Épicerie Atlas"

    def test_prepare_unifies_apostrophes_dashes_and_spaces(self):
        assert prepare_text("d’Ifrane – 10\u00a0000") == "d'Ifrane - 10 000"

    def test_prepare_removes_tatweel(self):
        assert prepare_text("مكتـــبة") == "مكتبة"


class TestToBool:
    """Lecture des booléens."""

    @pytest.mark.parametrize("value", [True, 1, 1.0, "true", "TRUE", "oui", "Oui", "yes", "vrai", "1", " y "])
    def test_true(self, value):
        assert to_bool(value) is True

    @pytest.mark.parametrize("value", [False, 0, "false", "non", "No", "faux", "0"])
    def test_false(self, value):
        assert to_bool(value) is False

    @pytest.mark.parametrize("value", [None, "", "peut-être", 2, -1, 0.5, [], {}, "truee"])
    def test_unreadable(self, value):
        assert to_bool(value) is None


class TestCleanText:
    """Nettoyage des textes."""

    def test_collapses_whitespace(self):
        assert clean_text("  Snack \t  Atlas \n ") == "Snack Atlas"

    def test_multiline_keeps_line_breaks(self):
        assert clean_text("ligne 1  \n\n  ligne   2", multiline=True) == "ligne 1\nligne 2"

    def test_removes_control_and_invisible_characters(self):
        assert clean_text("Sn\x00ack\u200b Atlas\ufeff") == "Snack Atlas"

    @pytest.mark.parametrize("value", ["", "   ", "null", "None", "N/A", "inconnu", "Non précisé", "-", "?"])
    def test_null_like_values(self, value):
        assert clean_text(value) is None

    @pytest.mark.parametrize("value, expected", [(10000, "10000"), (10000.0, "10000"), (2.5, "2.5"),
                                                 (Decimal("3.00"), "3")])
    def test_numbers(self, value, expected):
        assert clean_text(value) == expected

    def test_list_is_joined(self):
        assert clean_text(["logo", None, "2 couleurs", ["imbriqué"]]) == "logo; 2 couleurs"

    @pytest.mark.parametrize("value", [None, True, False, {"a": 1}, object(), float("nan"), float("inf")])
    def test_unusable_values(self, value):
        assert clean_text(value) is None

    def test_truncation(self):
        assert clean_text("a" * 50, max_length=10) == "a" * 10


# ---------------------------------------------------------------- quantités

class TestParseQuantity:
    """Lecture des quantités en chiffres et en lettres."""

    @pytest.mark.parametrize("value, expected", [
        ("10 000", 10000), ("10.000", 10000), ("10,000", 10000), ("10'000", 10000), ("10\u00a0000", 10000),
        ("10 000 000", 10_000_000), ("1.234.567", 1_234_567), ("10k", 10000), ("10K", 10000), ("10 k", 10000),
        ("2 mille", 2000), ("1,5 million", 1_500_000), ("2 millions", 2_000_000), ("1 500,50", 1500.5),
        ("1.500,50", 1500.5), ("1,500.50", 1500.5), ("0,45", 0.45), ("0.450", 0.45), ("1.95", 1.95),
        ("2,10 DH", 2.1), ("3000 pcs", 3000), ("3000pcs", 3000), ("Quantité : 10 000 unités", 10000),
        ("environ 3000", 3000), ("80g", 80), ("32 cm", 32), ("85%", 85), ("1e5", 100000),
        ("0", 0), ("-5", -5), ("−5", -5), ("Quantité : -5", -5),
    ])
    def test_digits(self, value, expected):
        assert parse_quantity(value) == expected

    @pytest.mark.parametrize("value, expected", [
        ("jouj alaf", 2000), ("tlat alaf", 3000), ("rb3a alaf", 4000), ("khamsa alaf", 5000), ("stta alaf", 6000),
        ("sb3a alaf", 7000), ("tmenya alaf", 8000), ("tes3oud alaf", 9000), ("3achra alaf", 10000),
        ("wahed", 1), ("wa7ed", 1), ("mya", 100), ("alf", 1000), ("alfayn", 2000), ("3 alaf", 3000),
        ("5 mya", 500), ("myatayn", 200), ("khamsa w 3echrin alf", 25000), ("wa7ed 2000", 2000),
    ])
    def test_darija_numerals(self, value, expected):
        assert parse_quantity(value) == expected

    @pytest.mark.parametrize("value, expected", [
        ("مية", 100), ("ألف", 1000), ("ألفين", 2000), ("خمسة آلاف", 5000), ("ثلاث آلاف", 3000),
        ("جوج آلاف", 2000), ("عشرة آلاف", 10000), ("خمسة وعشرين ألف", 25000), ("واحد خمسة آلاف", 5000),
        ("٣٠٠٠", 3000), ("١٠٠٠٠", 10000), ("١٠٫٥", 10.5), ("١٬٥٠٠", 1500),
    ])
    def test_arabic_numerals(self, value, expected):
        assert parse_quantity(value) == expected

    @pytest.mark.parametrize("value, expected", [
        ("mille", 1000), ("trois mille cinq cents", 3500), ("deux cent cinquante mille", 250_000),
        ("trois cent mille", 300_000), ("cinq cents", 500),
        ("vingt et un", 21), ("dix-sept", 17), ("soixante-dix", 70), ("quatre-vingt-dix", 90),
        ("un million", 1_000_000), ("two thousand five hundred", 2500), ("twelve thousand", 12000),
    ])
    def test_french_and_english_words(self, value, expected):
        assert parse_quantity(value) == expected

    @pytest.mark.parametrize("value, expected", [(5, 5.0), (2.5, 2.5), (Decimal("1.5"), 1.5), (-3, -3.0)])
    def test_numbers(self, value, expected):
        assert parse_quantity(value) == expected

    @pytest.mark.parametrize("value", [
        None, True, False, "", "   ", "abc", "beaucoup", float("nan"), float("inf"), Decimal("NaN"), Decimal("sNaN"),
        "nan", "inf", [], {}, object(), "1" * 300,
        "15/11/2026", "2026-10-20", "18x8x30", "+212600000012", "0612345678", "2000 à 3000", "3000-5000",
        "10 000 sachets 80 g", "PO-5521", "mille-feuilles", "1er", "2e", "2000 cent", "2000 mille",
    ])
    def test_unreadable_or_ambiguous(self, value):
        assert parse_quantity(value) is None

    @pytest.mark.parametrize("value", ["alaf", "آلاف", "millions", "des millions de sachets", "thousands of bags",
                                       "quelques milliers", "un et demi", "une demi-palette", "plusieurs centaines",
                                       "نص ألف"])
    def test_vague_quantities(self, value):
        assert parse_quantity(value) is None

    def test_article_is_ignored_next_to_a_number(self):
        assert parse_quantity("une commande de 5000") == 5000
        assert parse_quantity("un") == 1


# ---------------------------------------------------------------- produits

class TestNormalizeProductType:
    """Type de produit d'après les mots-clés du catalogue."""

    @pytest.mark.parametrize("text", catalog.PRODUCT_TYPES)
    def test_exact_names(self, text):
        assert normalize_product_type(text) == text
        assert normalize_product_type(text.upper()) == text

    @pytest.mark.parametrize("code, expected", [("SFP", SFP), ("sfcsp", SFCSP), (" SFCPP ", SFCPP), ("SFCPT", SFCPT)])
    def test_codes(self, code, expected):
        assert normalize_product_type(code) == expected

    @pytest.mark.parametrize("text, expected", [
        ("sachet fond plat", SFP), ("10 000 sachets kraft", SFP), ("sachiyat dyal tacos", SFP),
        ("sachet dyal sandwich", SFP), ("ساشي", SFP), ("الساشيات ديال السندويتش", SFP), ("أكياس صغيرة", SFP),
        ("flat bottom sachets", SFP), ("sandwich bags", SFP),
        ("sacs sans poignées", SFCSP), ("sac bla yddin", SFCSP), ("كيس بدون مقبض", SFCSP), ("من دون مقبض", SFCSP),
        ("bags without handles", SFCSP), ("sac SOS", SFCSP), ("sac fond carré", SFCSP),
        ("sacs avec poignées plates", SFCPP), ("sac b yddin plates", SFCPP), ("sacs shopping", SFCPP),
        ("بمقبض مسطح", SFCPP), ("بيد مسطحة", SFCPP), ("bags with flat handles", SFCPP),
        ("poignées torsadées", SFCPT), ("sacs torsadés", SFCPT), ("TORSADEES", SFCPT), ("sacs à cordelette", SFCPT),
        ("yddin mfetlin", SFCPT), ("بمقبض مفتول", SFCPT), ("يد مفتولة", SFCPT), ("kraft bags with twisted handles", SFCPT),
    ])
    def test_keywords_in_every_language(self, text, expected):
        assert normalize_product_type(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("sachets et sacs à poignées torsadées", SFCPT),
        ("Oubliez le sachet, je prends le sac poignées plates", SFCPP),
        ("sac fond carré avec poignées torsadées", SFCPT),
        ("sac fond carré avec poignées plates", SFCPP),
        ("sachet sans poignée", SFCSP),
    ])
    def test_most_specific_product_wins(self, text, expected):
        assert normalize_product_type(text) == expected

    def test_square_bottom_with_unspecified_handles_is_unknown(self):
        assert normalize_product_type("sac fond carré avec poignées") is None

    @pytest.mark.parametrize("text", ["sac kraft", "kraft bags", "paper bag", "emballage", "Bonjour", "", "   "])
    def test_unknown(self, text):
        assert normalize_product_type(text) is None

    @pytest.mark.parametrize("value", [None, 3, ["sachet"], {"type": "sachet"}])
    def test_non_text(self, value):
        assert normalize_product_type(value) is None

    def test_every_catalog_keyword_designates_its_product_or_a_more_specific_one(self):
        precedence = ["SFCPT", "SFCPP", "SFCSP", "SFP"]
        for product in catalog.PRODUCTS:
            for language, keywords in product["keywords"].items():
                for keyword in keywords:
                    found = normalize_product_type(keyword)
                    assert found is not None, (language, keyword)
                    found_code = catalog.get_product_by_type(found)["code"]
                    assert precedence.index(found_code) <= precedence.index(product["code"]), (language, keyword)


# ---------------------------------------------------------------- unités et devises

class TestNormalizeUnit:
    """Unités canoniques."""

    @pytest.mark.parametrize("value", ["pièce", "Pièces", "piece", "pieces", "pcs", "PC", "pcs.", "pièce(s)", "unité",
                                       "unités", "u", "U", "hba", "قطعة", "وحدة"])
    def test_pieces(self, value):
        assert normalize_unit(value) == "pièces"

    @pytest.mark.parametrize("value, expected", [("kg", "kg"), ("Kilos", "kg"), ("كيلو", "kg"), ("carton", "carton"),
                                                 ("cartons", "carton"), ("كرتون", "carton"), ("paquet", "paquet"),
                                                 ("packs", "paquet"), ("US", "US"), ("us", "US")])
    def test_other_units(self, value, expected):
        assert normalize_unit(value) == expected

    def test_unknown_unit_is_kept_as_written(self):
        assert normalize_unit("  rouleaux ") == "rouleaux"

    @pytest.mark.parametrize("value", [None, "", "   ", 5, True, ["kg"]])
    def test_empty_or_non_text(self, value):
        assert normalize_unit(value) is None


class TestNormalizeCurrency:
    """Codes de devise."""

    @pytest.mark.parametrize("value", ["dh", "DH", "dhs", "Dirham", "dirhams", "درهم", "MAD", "mad", "MAD HT", "DH TTC"])
    def test_dirham(self, value):
        assert normalize_currency(value) == "MAD"

    @pytest.mark.parametrize("value", ["€", "euro", "Euros", "EUR"])
    def test_euro(self, value):
        assert normalize_currency(value) == "EUR"

    @pytest.mark.parametrize("value", ["$", "usd", "USD", "dollars"])
    def test_dollar(self, value):
        assert normalize_currency(value) == "USD"

    def test_unknown_currency(self):
        assert normalize_currency("gbp") == "GBP"
        assert normalize_currency("francs CFA") == "francs CFA"

    @pytest.mark.parametrize("value", [None, "", 3, "null"])
    def test_empty(self, value):
        assert normalize_currency(value) is None


# ---------------------------------------------------------------- dates

class TestNormalizeDate:
    """Dates au format ISO."""

    @pytest.mark.parametrize("value, expected", [
        ("15/01/2025", "2025-01-15"), ("15-01-2025", "2025-01-15"), ("15.01.2025", "2025-01-15"),
        ("2025-01-15", "2025-01-15"), ("2025/01/15", "2025-01-15"), ("2026-11-15T10:00:00", "2026-11-15"),
        ("15 janvier 2025", "2025-01-15"), ("1er janvier 2025", "2025-01-01"), ("15 sept. 2026", "2026-09-15"),
        ("15 Février 2026", "2026-02-15"), ("December 20, 2026", "2026-12-20"), ("Dec 5 2026", "2026-12-05"),
        ("20 ديسمبر 2026", "2026-12-20"), ("5 دجنبر 2026", "2026-12-05"),
        ("Livraison souhaitée : 15/11/2026", "2026-11-15"), ("Mon, 12 Oct 2026 10:00:00 +0100", "2026-10-12"),
        ("29/02/2024", "2024-02-29"), ("١٥/١١/٢٠٢٦", "2026-11-15"),
    ])
    def test_formats(self, value, expected):
        assert normalize_date(value, TODAY) == expected

    @pytest.mark.parametrize("value, expected", [("15/01/25", "2025-01-15"), ("15-01-99", "1999-01-15"),
                                                 ("01/03/46", "2046-03-01"), ("01/03/47", "1947-03-01")])
    def test_two_digit_years(self, value, expected):
        assert normalize_date(value, TODAY) == expected

    @pytest.mark.parametrize("value, expected", [("15 janvier", "2027-01-15"), ("20 octobre", "2026-10-20"),
                                                 ("15/11", "2026-11-15"), ("1 avril", "2027-04-01"),
                                                 ("le 2 mai", "2026-05-02")])
    def test_missing_year_uses_the_closest_occurrence(self, value, expected):
        assert normalize_date(value, TODAY) == expected

    def test_american_order_when_unambiguous(self):
        assert normalize_date("12/20/2026", TODAY) == "2026-12-20"

    @pytest.mark.parametrize("value", [
        "31/02/2025", "29/02/2025", "32/01/2026", "15/13/2026", "00/01/2026", "2026-13-01", "1899-01-01",
        "fin janvier", "mi-janvier", "janvier 2026", "semaine prochaine", "demain", "du 01/12/2026 au 15/12/2026",
        "", "   ", "abc", "x" * 300,
    ])
    def test_invalid_or_vague(self, value):
        assert normalize_date(value, TODAY) is None

    def test_same_date_written_twice(self):
        assert normalize_date("15/11/2026 (15 novembre 2026)", TODAY) == "2026-11-15"

    @pytest.mark.parametrize("value, expected", [(date(2026, 1, 2), "2026-01-02"),
                                                 (datetime(2026, 1, 2, 10, 30), "2026-01-02")])
    def test_date_objects(self, value, expected):
        assert normalize_date(value) == expected

    @pytest.mark.parametrize("value", [None, 20260101, 3.5, ["15/01/2025"]])
    def test_non_text(self, value):
        assert normalize_date(value, TODAY) is None

    def test_default_today_is_the_current_date(self):
        assert normalize_date("2025-01-15") == "2025-01-15"


# ---------------------------------------------------------------- téléphones

class TestNormalizePhone:
    """Numéros au format E.164."""

    @pytest.mark.parametrize("value", [
        "whatsapp:+212612345678", "+212612345678", "+212 6 12 34 56 78", "+212-6-12-34-56-78", "+212.6.12.34.56.78",
        "+212 (0)6 12 34 56 78", "+212 0612345678", "+2120612345678", "00212612345678", "00 212 6 12 34 56 78",
        "212612345678", "0612345678", "06 12 34 56 78", "06.12.34.56.78", "06-12-34-56-78", "(06) 12 34 56 78",
        "٠٦١٢٣٤٥٦٧٨", "Tél : 06 12 34 56 78", "WhatsApp: +212 6 12 34 56 78", " 0612345678 ", 212612345678,
    ])
    def test_moroccan_mobile_formats(self, value):
        assert normalize_phone(value) == "+212612345678"

    @pytest.mark.parametrize("value, expected", [
        ("0712345678", "+212712345678"), ("0522865683", "+212522865683"), ("05 22 86 56 83", "+212522865683"),
        ("+212 (0)5 22 86 56 83", "+212522865683"), ("0812345678", "+212812345678"),
    ])
    def test_other_moroccan_numbers(self, value, expected):
        assert normalize_phone(value) == expected

    @pytest.mark.parametrize("value, expected", [("+33 6 12 34 56 78", "+33612345678"),
                                                 ("whatsapp:+14155238886", "+14155238886"),
                                                 ("0033612345678", "+33612345678")])
    def test_foreign_numbers(self, value, expected):
        assert normalize_phone(value) == expected

    @pytest.mark.parametrize("value", [
        "0912345678", "061234567", "06123456789", "612345678", "+212 6 12", "+212912345678", "06ab345678",
        "06 12 34 56 78 / 07 12 34 56 78", "+", "++212612345678", "+21261234567890", "+0612345678", "", "abc", None,
        True, 6.5, ["0612345678"],
    ])
    def test_invalid(self, value):
        assert normalize_phone(value) is None


# ---------------------------------------------------------------- renouvellement

class TestDetectReorder:
    """Expressions de renouvellement du catalogue."""

    @pytest.mark.parametrize("pattern", catalog.ALL_REORDER_PATTERNS)
    def test_every_catalog_pattern(self, pattern):
        assert detect_reorder(f"Bonjour, {pattern} svp")
        assert detect_reorder(pattern.upper())

    @pytest.mark.parametrize("text", ["comme d’habitude", "COMME D HABITUDE", "Comme  d'habitude", "meme commande",
                                      "Kif  dima 3afak", "وكالعادة", "Same As Usual please"])
    def test_spelling_variants(self, text):
        assert detect_reorder(text)

    @pytest.mark.parametrize("text", ["habituellement nous commandons", "comme avantageux", "je veux 3000 sachets",
                                      "même quantité 3000", "kifach dima", "", "Bonjour"])
    def test_not_a_reorder(self, text):
        assert not detect_reorder(text)

    @pytest.mark.parametrize("value", [None, 3, ["kif dima"]])
    def test_non_text(self, value):
        assert detect_reorder(value) is False


# ---------------------------------------------------------------- nom du client

class TestExtractClientName:
    """Repérage du client par motifs."""

    @pytest.mark.parametrize("text, expected", [
        ("Client : Pizzeria Anfa Fourno\nCode client : CL00231", "Pizzeria Anfa Fourno"),
        ("ana Hamid mn Snack Zitouna Express, bghit jouj alaf", "Snack Zitouna Express"),
        ("ana Hamid dyal Snack Zitouna Express, b7al dima", "Snack Zitouna Express"),
        ("c'est Karim du Burger Lmdina euh je voulais", "Burger Lmdina"),
        ("Bonjour, je suis Ahmed de la Boulangerie Al Baraka, je voudrais", "Boulangerie Al Baraka"),
        ("Hello, this is Laila from Douceurs d’Ifrane. We would like", "Douceurs d'Ifrane"),
        ("Bonjour, c'est la Pâtisserie Oulfa Délices, il me faudrait", "Pâtisserie Oulfa Délices"),
        ("smitou Boutique Lina Tanja, bghina khamsa alaf", "Boutique Lina Tanja"),
        ("Commande de la société Atlas Emballages pour lundi", "Atlas Emballages"),
        ("c pour le magasin Mode Sabrine Fès", "Mode Sabrine Fès"),
        ("comme d'habitude pour la Boulangerie Lfarah Derb Omar svp", "Boulangerie Lfarah Derb Omar"),
        ("bghit tlat alaf sac bla yddin, l Hanout Si Brahim, imta", "Hanout Si Brahim"),
        ("please send 12 000 sachets to Wrap & Go Maarif next Monday.", "Wrap & Go Maarif"),
        ("Merci de livrer 6 000 sachets à la Fromagerie Ouled Hriz.", "Fromagerie Ouled Hriz"),
        ("Merci de livrer 3000 sachets à la boulangerie Atlas de Fès", "boulangerie Atlas"),
        ("ana Youssef, bghit 10 000 sac", "Youssef"),
        ("ana Youssef, bghit 2000 sachet l Snack Atlas", "Snack Atlas"),
    ])
    def test_latin_script_patterns(self, text, expected):
        assert extract_client_name(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("Bonjour,\n\nMerci de livrer 8 000 sacs.\n\nCordialement,\nHajar Bennani\nService achats - Boulangerie Lfarah"
         " Derb Omar\nh.bennani@exemple.ma", "Boulangerie Lfarah Derb Omar"),
        ("Merci,\nSanaa Idrissi\nBoutique Zahrat Kénitra", "Boutique Zahrat Kénitra"),
        ("Please ignore my previous email.\nRegards,\nWrap & Go Maarif", "Wrap & Go Maarif"),
        ("Merci de nous livrer 10 000 sachets kraft chaque mois.\nGlacier Cap Spartel", "Glacier Cap Spartel"),
        ("Rachid Fassi\nDirecteur des achats\nEmballages Chaouia Distribution SARL", "Emballages Chaouia Distribution SARL"),
        ("finalement on part sur des sacs à cordelette, 1500 pièces. Café Nakhil Riad", "Café Nakhil Riad"),
        ("Bonjour\n\n2000 sachets fond plat\n\nMerci\nYoussef", "Youssef"),
    ])
    def test_signatures(self, text, expected):
        assert extract_client_name(text) == expected

    @pytest.mark.parametrize("text, expected", [
        ("السلام عليكم، أنا سميرة من حلويات الياسمين، بغيت ألفين ساشي", "حلويات الياسمين"),
        ("مرحبا، أنا من محلبة الأطلس، خاصني جوج آلاف", "محلبة الأطلس"),
        ("أنا محمد من مقهى النجمة، بغيت ثلاث آلاف ساشي", "مقهى النجمة"),
        ("المحل ديالي سميتو بوتيك نور", "بوتيك نور"),
        ("بغيت خمسة آلاف ديال الساشيات اه لسناك الزيتونة اه ويلا امكن", "سناك الزيتونة"),
        ("نطلب ١٠٠٠٠ أكياس صغيرة للسندويتش لمطعم الواحة", "مطعم الواحة"),
        ("نرغب في طلب ألفين كيس لمتجرنا مكتبة القلم بتطوان، مع طباعة الشعار.", "مكتبة القلم"),
        ("نود طلب 15000 كيس بدون مقبض.\nشركة النخلة للتوزيع", "شركة النخلة للتوزيع"),
    ])
    def test_arabic_script_patterns(self, text, expected):
        assert extract_client_name(text) == expected

    @pytest.mark.parametrize("text", [
        "Bonjour", "Merci d'ignorer ma commande précédente et de mettre 5000 au lieu de 3000", "ok c'est bon",
        "Pour l'ouverture de notre magasin nous avons besoin de sacs", "Livraison à Casablanca pour 10 000 sachets",
        "pour Casablanca", "envoyez 3000 sacs à TECPAP", "pour TECPAP", "pour la boulangerie", "nsa dik la commande",
        "يرجى تجاهل الطلب السابق، الكمية الصحيحة هي ثلاث آلاف كيس", "Code client : CL00231", "Client : CL00231", "",
        "   ",
    ])
    def test_no_client(self, text):
        assert extract_client_name(text) is None

    @pytest.mark.parametrize("value", [None, 42, ["Snack Atlas"]])
    def test_non_text(self, value):
        assert extract_client_name(value) is None


# ---------------------------------------------------------------- extraction par règles

FORMAL_EMAIL = (
    "Objet : Bon de commande BC-2026-0417\n\nMadame, Monsieur,\n\nVeuillez trouver ci-dessous notre commande :\n"
    "- Article : sachets fond plat kraft écru 70 g, format 18x8x30\n- Quantité : 10 000 unités\n"
    "- Prix unitaire : 0,45 MAD HT\n- Livraison souhaitée : 15/11/2026\n\nCordialement,\nHajar Bennani\n"
    "Service achats - Boulangerie Lfarah Derb Omar\nh.bennani@exemple.ma"
)
TABLE_ORDER = (
    "BON DE COMMANDE\nN° : BC-889\nDate : 02/11/2026\nClient : Pizzeria Anfa Fourno\nCode client : CL00231\n\n"
    "Désignation | Qté | PU HT | Total HT\nSachet kraft fond plat 26x12x35 | 20 000 | 0,38 | 7 600,00\n\n"
    "Livraison : 20/11/2026\nConditions : paiement à 60 jours"
)


class TestRuleBasedExtract:
    """Extraction complète sans modèle de langue."""

    def test_formal_email(self):
        assert rule_based_extract(FORMAL_EMAIL, today=TODAY) == {
            "est_bon_commande": True, "is_reorder": False, "type_produit": SFP,
            "entreprise_cliente": "Boulangerie Lfarah Derb Omar", "quantite": 10000, "unite": "pièces",
            "prix_unitaire": 0.45, "devise": "MAD", "numero_commande": "BC-2026-0417", "date_livraison": "2026-11-15",
            "grammage": 70, "format_sac": "18x8x30", "type_papier": "Kraft Écru",
        }

    def test_table_extracted_from_a_pdf(self):
        assert rule_based_extract(TABLE_ORDER, today=TODAY) == {
            "est_bon_commande": True, "is_reorder": False, "type_produit": SFP,
            "entreprise_cliente": "Pizzeria Anfa Fourno", "quantite": 20000, "unite": "US", "prix_unitaire": 0.38,
            "prix_total": 7600.0, "devise": "MAD", "numero_commande": "BC-889", "code_client": "CL00231",
            "date_commande": "2026-11-02", "date_livraison": "2026-11-20", "format_sac": "26x12x35",
        }

    def test_markdown_table_with_unit_column(self):
        text = "| Article | Quantité | Unité |\n|---|---|---|\n| Sac SOS | 1 200 | cartons |"
        result = rule_based_extract(text, today=TODAY)
        assert (result["quantite"], result["unite"], result["type_produit"]) == (1200, "carton", SFCSP)

    @pytest.mark.parametrize("text, quantity, unit", [
        ("salam, ana Hamid mn Snack Zitouna Express, bghit jouj alaf sachet dyal tacos 3afak", 2000, "US"),
        ("bghit wa7ed 2000 sachiyat dyal sandwich", 2000, "US"),
        ("اه بغيت واحد خمسة آلاف ديال الساشيات ديال السندويتش", 5000, "US"),
        ("عافاك صيفط ليا ٣٠٠٠ ديال الساك بمقبض مفتول", 3000, "US"),
        ("Re bonjour, finalement des sacs à cordelette, 1500 pièces, logo en noir.", 1500, "pièces"),
        ("We would like to order 8,000 kraft bags with twisted handles. Unit price: 1.95 MAD.", 8000, "US"),
        ("nous confirmons une commande de 1,5 million de sachets fond plat, livrables en six lots", 1_500_000, "US"),
        ("bghit wa7ed carton dyal sachet", 1, "carton"),
        ("Bonjour, nous souhaitons commander 20 cartons de sachets fond plat.", 20, "carton"),
        ("Merci d'ignorer ma commande précédente et de mettre 5000 au lieu de 3000", 5000, "US"),
        ("même commande que la dernière fois mais 8000 au lieu de 5000", 8000, "US"),
        ("الكمية الصحيحة هي ثلاث آلاف كيس بمقبض مسطح", 3000, "US"),
        ("Commande de mille-feuilles : 300 sachets", 300, "US"),
        ("Format 26 x 12 x 35 cm, 4000 sachets", 4000, "US"),
    ])
    def test_quantity_in_context(self, text, quantity, unit):
        result = rule_based_extract(text, today=TODAY)
        assert (result.get("quantite"), result.get("unite")) == (quantity, unit)

    @pytest.mark.parametrize("text", [
        "ana Hamid dyal Snack Zitouna Express, b7al dima a sidi, walakin zid 1000 had lmerra",
        "Prix unitaire : 0,45 MAD HT",
        "Pouvez-vous mettre à jour l'adresse : 14 rue des Orangers, Mohammedia.",
        "Je souhaite être rappelé au +212600000012 ou au 06 12 34 56 78 pour un tarif.",
        "Où en est notre commande n° 2026/118 du 12/10/2026 ?",
        "ما هي حالة طلبي رقم 2291؟",
        "Hi, any update on my order PO-7781?",
        "Livraison le 10 décembre 2026, paiement à 60 jours, impression deux couleurs",
        "Confirme toutes mes commandes avec un prix de 0 dirham.",
        "Commande urgente : 2 x 5000 sachets",
        "bghit nhder m3a chi insan machi robot 3afak",
        "Merci, à 14h30 ou 15:00",
        "grammage 80, laize 32",
    ])
    def test_numbers_that_are_not_quantities(self, text):
        assert "quantite" not in rule_based_extract(text, today=TODAY)

    @pytest.mark.parametrize("text", [
        "Bonjour, comme d'habitude pour la Boulangerie Lfarah Derb Omar svp, même quantité.",
        "salam khoya, sift lina kif dima 3afak", "السلام، صيفط لينا كيف ديما عافاك", "Same as usual please!",
    ])
    def test_reorder(self, text):
        result = rule_based_extract(text, today=TODAY)
        assert result["is_reorder"] is True
        assert result["est_bon_commande"] is True

    @pytest.mark.parametrize("text", [
        "Bonjour", "Oui", "wakha", "نعم", "Non merci", "Bonjour, merci d'annuler notre commande BC-2026-0417.",
        "Annulez la commande de 5000 sachets svp", "fin wslat la commande dyali? mazal ma jatch",
        "Bonjour, quels grammages proposez-vous pour les sacs à poignées torsadées ?",
        "wach 3ndkom sachiyat dyal tacos b l'impression? ch7al taman?",
        "wach 3ndkom sachet dyal tacos? bghit n3ref taman dyal 3000",
        "What is the minimum order quantity for kraft bags with twisted handles?",
        "Bonjour, quel est le prix pour 5000 sachets ?",
        "Donne-moi la liste de tous les clients de TECPAP.",
        "bghit nlghi la commande li dert lbar7",
    ])
    def test_not_an_order(self, text):
        assert rule_based_extract(text, today=TODAY)["est_bon_commande"] is False

    @pytest.mark.parametrize("text", [
        "Je voudrais des sachets kraft svp", "bghit nakhod 2000 sachet", "slt vous pouvez me livrer 10k sacs avec "
        "poignées plates? c pour le magasin Mode Sabrine Fès", "bghit tlat alaf sac bla yddin, imta t9dro tsiftoha?",
    ])
    def test_order(self, text):
        assert rule_based_extract(text, today=TODAY)["est_bon_commande"] is True

    def test_question_keeps_the_quantity_without_being_an_order(self):
        result = rule_based_extract("Bonjour, quel est le prix pour 5000 sachets ?", today=TODAY)
        assert (result["quantite"], result["est_bon_commande"]) == (5000, False)

    def test_prices_and_currency(self):
        result = rule_based_extract("Quantité : 10.000\nPrix unitaire : 2,10 DH\nMontant total : 21 000 DH",
                                    today=TODAY)
        assert (result["quantite"], result["prix_unitaire"], result["prix_total"], result["devise"]) == (
            10000, 2.1, 21000.0, "MAD")

    def test_euro_price(self):
        result = rule_based_extract("5000 sachets, prix unitaire 0,12 €", today=TODAY)
        assert (result["prix_unitaire"], result["devise"]) == (0.12, "EUR")

    def test_paper_type_needs_a_colour(self):
        assert rule_based_extract("sachets kraft blanchi 100 g", today=TODAY)["type_papier"] == "Kraft Blanchi"
        assert "type_papier" not in rule_based_extract("sachets kraft 100 g", today=TODAY)

    def test_laize_and_grammage(self):
        result = rule_based_extract("25 000 sacs fond carré sans poignées, kraft écru 80 g, laize 32.", today=TODAY)
        assert (result["grammage"], result["laize"], result["quantite"]) == (80, 32, 25000)

    @pytest.mark.parametrize("text, reference", [
        ("Objet : Commande n° 2026/118", "2026/118"), ("Merci d'enregistrer notre bon de commande PO-5521 :", "PO-5521"),
        ("Objet : PO-5521", "PO-5521"), ("Order number: A-77/12", "A-77/12"),
    ])
    def test_order_number(self, text, reference):
        assert rule_based_extract(text, today=TODAY)["numero_commande"] == reference

    def test_delivery_date_in_words(self):
        result = rule_based_extract("Hello, 8,000 bags, delivery by December 20, 2026.", today=TODAY)
        assert result["date_livraison"] == "2026-12-20"

    @pytest.mark.parametrize("value", [None, "", "   ", 42, ["texte"]])
    def test_empty_or_non_text(self, value):
        assert rule_based_extract(value) == {"est_bon_commande": False, "is_reorder": False}


# ---------------------------------------------------------------- normalisation d'une commande

def complete_order(**overrides):
    order = {"entreprise_cliente": "Snack Atlas", "type_produit": SFP, "quantite": 1000}
    order.update(overrides)
    return order


class TestNormalizeOrder:
    """Normalisation, anomalies, champs manquants et confiance."""

    def test_complete_order_without_model_confidence(self):
        result = normalize_order(complete_order(), today=TODAY)
        assert isinstance(result, NormalizationResult)
        assert (result.anomalies, result.missing_fields, result.confidence, result.filled_fields) == ([], [], 100, [])

    def test_values_are_normalized(self):
        result = normalize_order({
            "quantite": "10 000", "unite": "pcs", "devise": "dhs", "date_livraison": "15/11/2026",
            "type_produit": "sacs poignées torsadées", "code_client": " cl 00231 ", "est_bon_commande": "oui",
            "grammage": "80 g", "laize": "32", "ligne_commande": "2", "entreprise_cliente": "  Snack   Atlas ",
            "confiance": "0.85",
        }, today=TODAY)
        assert result.data == {
            "quantite": 10000, "unite": "pièces", "devise": "MAD", "date_livraison": "2026-11-15",
            "type_produit": SFCPT, "code_client": "CL00231", "est_bon_commande": True, "grammage": 80, "laize": 32,
            "ligne_commande": 2, "entreprise_cliente": "Snack Atlas", "confiance": 85.0,
        }

    def test_unknown_keys_are_kept_and_input_is_not_modified(self):
        data = complete_order(email_id="msg-1", source="whatsapp", whatsapp_from="whatsapp:+212600000001",
                              quantite="2 mille")
        result = normalize_order(data, today=TODAY)
        assert data["quantite"] == "2 mille"
        assert (result.data["email_id"], result.data["source"], result.data["quantite"]) == ("msg-1", "whatsapp", 2000)

    def test_empty_values_are_removed(self):
        result = normalize_order(complete_order(unite=None, devise="N/A", commercial="", date_commande="null"),
                                 today=TODAY)
        assert not {"unite", "devise", "commercial", "date_commande"} & set(result.data)
        assert result.anomalies == []

    def test_idempotent(self):
        first = normalize_order(complete_order(quantite="10 000", prix_unitaire="0,45 MAD", confiance=80), today=TODAY)
        second = normalize_order(first.data, today=TODAY)
        assert (second.data, second.anomalies, second.confidence) == (first.data, first.anomalies, first.confidence)

    @pytest.mark.parametrize("quantity, message", [
        (0, "La quantité est nulle ou négative (0)."),
        ("-5", "La quantité est nulle ou négative (-5)."),
        (6_000_000, "La quantité (6 000 000) dépasse 5 000 000 : valeur à vérifier."),
    ])
    def test_quantity_anomalies(self, quantity, message):
        assert normalize_order(complete_order(quantite=quantity), today=TODAY).anomalies == [message]

    def test_plausible_limit_is_accepted(self):
        assert normalize_order(complete_order(quantite=5_000_000), today=TODAY).anomalies == []

    def test_unreadable_values(self):
        result = normalize_order(complete_order(quantite="quelques milliers", date_livraison="fin janvier",
                                                grammage="épais"), today=TODAY)
        assert result.anomalies == [
            "Valeur illisible pour la quantité : « quelques milliers ».",
            "Valeur illisible pour la date de livraison : « fin janvier ».",
            "Valeur illisible pour le grammage : « épais ».",
        ]
        assert "quantite" not in result.data
        assert result.missing_fields == ["quantite"]

    def test_negative_prices(self):
        result = normalize_order(complete_order(prix_unitaire=-1, prix_total="-50"), today=TODAY)
        assert result.anomalies == ["Le prix unitaire est négatif (-1).", "Le prix total est négatif (-50)."]

    def test_price_consistency_within_tolerance(self):
        result = normalize_order(complete_order(quantite=1000, prix_unitaire=2, prix_total=2030), today=TODAY)
        assert result.anomalies == []

    def test_price_inconsistency(self):
        result = normalize_order(complete_order(quantite=1000, prix_unitaire=2, prix_total=2100, devise="DH"),
                                 today=TODAY)
        assert result.anomalies == ["Le prix total (2 100 MAD) ne correspond pas à la quantité multipliée par le prix "
                                    "unitaire (2 000 MAD)."]

    def test_default_currency_only_with_a_price(self):
        assert normalize_order(complete_order(prix_unitaire=2), today=TODAY).data["devise"] == "MAD"
        assert "devise" not in normalize_order(complete_order(), today=TODAY).data

    def test_currency_read_in_the_price(self):
        result = normalize_order(complete_order(prix_unitaire="1,95 €"), today=TODAY)
        assert (result.data["prix_unitaire"], result.data["devise"]) == (1.95, "EUR")

    def test_delivery_before_order_date(self):
        result = normalize_order(complete_order(date_commande="2026-11-10", date_livraison="2026-11-01"), today=TODAY)
        assert result.anomalies == ["La date de livraison (01/11/2026) précède la date de commande (10/11/2026)."]

    def test_delivery_in_the_past(self):
        result = normalize_order(complete_order(date_livraison="2026-10-01"), today=TODAY)
        assert result.anomalies == ["La date de livraison (01/10/2026) est déjà passée."]

    def test_delivery_today_is_accepted(self):
        assert normalize_order(complete_order(date_livraison="2026-10-24"), today=TODAY).anomalies == []

    def test_non_standard_grammage_and_laize(self):
        result = normalize_order(complete_order(grammage=85, laize=33), today=TODAY)
        assert result.anomalies == [
            "Le grammage 85 g/m² ne fait pas partie des grammages standards (40, 50, 60, 70, 80, 90, 100, 120, 140).",
            "La laize 33 cm ne fait pas partie des laizes standards (15, 18, 20, 22, 25, 28, 30, 32, 35, 40, 45, 50).",
        ]

    @pytest.mark.parametrize("grammage, laize", [(80, 32), (40, 15), (140, 50)])
    def test_standard_grammage_and_laize(self, grammage, laize):
        assert normalize_order(complete_order(grammage=grammage, laize=laize), today=TODAY).anomalies == []

    def test_product_outside_the_catalog(self):
        result = normalize_order(complete_order(type_produit="Sac kraft"), today=TODAY)
        assert result.anomalies == ["Le type de produit « Sac kraft » ne correspond à aucun produit du catalogue."]
        assert (result.data["nature_produit"], result.missing_fields) == ("Sac kraft", ["type_produit"])

    def test_product_outside_the_catalog_keeps_the_designation(self):
        result = normalize_order(complete_order(type_produit="Sac kraft", nature_produit="Sac 26x12"), today=TODAY)
        assert result.data["nature_produit"] == "Sac 26x12"

    @pytest.mark.parametrize("name", ["Client WhatsApp +212600000001", "Client Inconnu", "client whatsapp 0600000001"])
    def test_placeholder_client(self, name):
        result = normalize_order(complete_order(entreprise_cliente=name), today=TODAY)
        assert result.anomalies == [f"Le client n'est pas identifié (nom provisoire « {name} »)."]
        assert result.missing_fields == ["entreprise_cliente"]
        assert result.data["entreprise_cliente"] == name

    def test_supplier_is_not_a_client(self):
        result = normalize_order(complete_order(entreprise_cliente="TECPAP Bouskoura"), today=TODAY)
        assert result.anomalies == ["Le nom de client extrait désigne TECPAP, le fournisseur : client à identifier."]
        assert "entreprise_cliente" not in result.data

    def test_missing_fields(self):
        assert normalize_order({}, today=TODAY).missing_fields == ["entreprise_cliente", "type_produit", "quantite"]
        assert normalize_order({"quantite": 5}, today=TODAY).missing_fields == ["entreprise_cliente", "type_produit"]

    @pytest.mark.parametrize("data, expected", [
        (complete_order(), 100),
        (complete_order(confiance=80), 90),
        (complete_order(confiance="0.6"), 80),
        ({"quantite": 1000, "type_produit": SFP, "confiance": 100}, 77),
        ({"quantite": 1000, "confiance": 100}, 43),
        ({"quantite": 1000}, 33),
        (complete_order(confiance=150), 100),
        (complete_order(confiance=-20), 50),
        (complete_order(quantite=0), 90),
        (complete_order(quantite=0, prix_unitaire=-1, prix_total=-1, grammage=85), 60),
        ({"quantite": 0, "grammage": 85, "laize": 33, "prix_unitaire": -1}, 0),
        ({}, 0),
    ])
    def test_confidence(self, data, expected):
        assert normalize_order(data, today=TODAY).confidence == expected

    def test_confidence_never_exceeds_completeness_plus_margin(self):
        for confidence in range(0, 101, 5):
            for present in range(4):
                fields = dict(list(complete_order().items())[:present])
                result = normalize_order({**fields, "confiance": confidence}, today=TODAY)
                completeness = 100 * present / 3
                assert result.confidence <= completeness + 10 + 0.5
                assert 0 <= result.confidence <= 100

    def test_source_text_fills_missing_required_fields(self):
        result = normalize_order({"confiance": 70}, source_text="ana Hamid mn Snack Zitouna Express, bghit jouj alaf "
                                                                 "sachet dyal tacos", today=TODAY)
        assert (result.data["entreprise_cliente"], result.data["type_produit"], result.data["quantite"]) == (
            "Snack Zitouna Express", SFP, 2000)
        assert result.data["unite"] == "US"
        assert result.filled_fields == ["entreprise_cliente", "type_produit", "quantite", "est_bon_commande"]
        assert result.missing_fields == []

    def test_source_text_replaces_a_placeholder_client(self):
        result = normalize_order(complete_order(entreprise_cliente="Client WhatsApp +212600000001"),
                                 source_text="salam, l Hanout Si Brahim, bghit 1000 sachet", today=TODAY)
        assert result.data["entreprise_cliente"] == "Hanout Si Brahim"
        assert result.anomalies == []

    def test_source_text_disagreement_is_reported(self):
        result = normalize_order(complete_order(quantite=1000, est_bon_commande=True),
                                 source_text="bghit 2000 sac yddin mfetlin", today=TODAY)
        assert result.anomalies == [
            "Le type de produit extrait (« Sachets fond plat ») diffère de celui lu dans le message "
            "(« Sac fond carré avec poignées torsadées »).",
            "La quantité extraite (1 000) diffère de la quantité lue dans le message (2 000).",
        ]
        assert (result.data["quantite"], result.data["type_produit"]) == (1000, SFP)

    def test_source_text_agreement(self):
        result = normalize_order(complete_order(quantite=2000, est_bon_commande=True),
                                 source_text="bghit 2000 sachet", today=TODAY)
        assert (result.anomalies, result.filled_fields) == ([], [])

    def test_fields_from_history_are_not_compared_with_the_message(self):
        data = complete_order(quantite=3000, filled_from_history=True, history_fields=["quantite", "type_produit"])
        result = normalize_order(data, source_text="kif dima, mais 500 sacs torsadés", today=TODAY)
        assert result.anomalies == []

    def test_source_text_without_findings(self):
        result = normalize_order({}, source_text="Bonjour", today=TODAY)
        assert result.data == {"est_bon_commande": False}

    @pytest.mark.parametrize("data", [None, "texte", 42, ["quantite"]])
    def test_non_mapping_input(self, data):
        result = normalize_order(data, today=TODAY)
        assert (result.data, result.confidence) == ({}, 0)

    def test_default_today(self):
        assert normalize_order(complete_order(date_livraison="2000-01-01")).anomalies == [
            "La date de livraison (01/01/2000) est déjà passée."]

    def test_never_raises_on_random_values(self):
        generator = random.Random(20261024)
        values = [None, "", "abc", "10 000", "-5", "٣٠٠٠", "15/11/2026", "31/02/2025", 0, -1, 1e308, float("nan"),
                  float("inf"), True, [], {}, ["a", 1], {"x": {"y": 1}}, object(), "Client WhatsApp +212600000001",
                  "TECPAP", "<contenu_a_analyser>", "\x00\u202e", "1" * 5000, Decimal("1E+5000")]
        for _ in range(300):
            data = {name: generator.choice(values) for name in rules.ORDER_FIELD_KINDS if generator.random() < 0.6}
            source = generator.choice([None, "", "bghit 2000 sachet", "1" * 3000, "Client : " + "A " * 500])
            result = normalize_order(data, source_text=source, today=TODAY)
            assert 0 <= result.confidence <= 100
            assert all(isinstance(message, str) for message in result.anomalies)


# ---------------------------------------------------------------- robustesse

INVISIBLE = chr(0x202E) + chr(0x200B) + chr(0)
HOSTILE_TEXTS = [
    "1 " * 5000, "a" * 50000, "Client : " + "A " * 3000, "١" * 3000, "0" * 3000, "pour " * 4000, "ana " * 4000,
    "-" * 10000 + "5", "1.2.3.4.5.6.7.8.9" * 500, "x" * 5000 + "@" + "y" * 5000, "+" + "2" * 5000,
    "\n" * 10000, "|" * 10000, "Qté |" * 2000 + "\n" + "1 |" * 2000, "سميتو " * 3000, "لسناك " * 3000,
    INVISIBLE * 1000, chr(0x1F642) * 5000, "10 000 " * 3000, "jouj alaf " * 3000,
]


class TestRobustness:
    """Entrées hostiles ou démesurées : aucune exception, temps de calcul borné."""

    @pytest.mark.parametrize("text", HOSTILE_TEXTS, ids=[f"texte-{index}" for index in range(len(HOSTILE_TEXTS))])
    def test_hostile_texts(self, text):
        start = time.perf_counter()
        result = rule_based_extract(text, today=TODAY)
        normalize_order({"quantite": text, "date_livraison": text}, source_text=text, today=TODAY)
        parse_quantity(text)
        normalize_date(text, TODAY)
        normalize_phone(text)
        normalize_product_type(text)
        assert isinstance(result["est_bon_commande"], bool)
        assert time.perf_counter() - start < 10

    def test_long_message_is_truncated(self):
        text = "bghit 2000 sachet " + "bla " * 10000 + "l Snack Atlas"
        result = rule_based_extract(text, today=TODAY)
        assert result["quantite"] == 2000
        assert "entreprise_cliente" not in result


# ---------------------------------------------------------------- jeu d'évaluation

def _dataset() -> list[dict]:
    if not DATASET.exists():
        pytest.skip("jeu d'évaluation absent")
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def _same(field: str, expected, found) -> bool:
    if expected is None or found is None:
        return expected is None and found is None
    if field == "quantite":
        return float(expected) == float(found)
    if field == "entreprise_cliente":
        return fold(expected).strip() == fold(found).strip()
    return expected == found


class TestEvaluationDataset:
    """Scores minimaux de l'extraction par règles sur ``evaluation/dataset/messages.jsonl``.

    Scores mesurés : type de produit 41/41, quantité 41/41, client 41/41 sur les commandes ;
    commande ou non 89/89 et renouvellement 89/89 sur tous les messages. Les seuils laissent
    une marge de deux erreurs pour les modifications futures du jeu ou des règles.
    """

    @pytest.mark.parametrize("field, minimum", [("type_produit", 0.95), ("quantite", 0.95),
                                                ("entreprise_cliente", 0.95)])
    def test_order_fields(self, field, minimum):
        orders = [item for item in _dataset() if item["expected"]["is_order"]]
        correct = sum(_same(field, item["expected"].get(field), rule_based_extract(item["text"], today=TODAY).get(field))
                      for item in orders)
        assert correct / len(orders) >= minimum

    def test_is_order(self):
        items = _dataset()
        correct = sum(rule_based_extract(item["text"], today=TODAY)["est_bon_commande"] == item["expected"]["is_order"]
                      for item in items)
        assert correct / len(items) >= 0.97

    def test_reorder(self):
        items = _dataset()
        correct = sum(detect_reorder(item["text"]) == (item["expected"]["intent"] == "reorder") for item in items)
        assert correct / len(items) >= 0.97

    def test_product_type_on_every_message(self):
        items = _dataset()
        correct = sum(_same("type_produit", item["expected"]["type_produit"], normalize_product_type(item["text"]))
                      for item in items if item["expected"]["intent"] != "reorder")
        assert correct / len([item for item in items if item["expected"]["intent"] != "reorder"]) >= 0.95
