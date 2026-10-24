"""Tests du catalogue TECPAP (tecpap.catalog), source unique des données métier."""

import re

import pytest

from tecpap import catalog
from tecpap.db import database
from tecpap.extraction import article_codes, extractor

ARABIC_LETTER = re.compile("[\u0600-\u06ff]")


class TestProducts:
    """Les quatre familles de sacs."""

    def test_four_products_with_stable_ids(self):
        assert [p["id"] for p in catalog.PRODUCTS] == [1, 2, 3, 4]

    def test_product_types_and_codes(self):
        assert [(p["type"], p["code"]) for p in catalog.PRODUCTS] == [
            ("Sachets fond plat", "SFP"),
            ("Sac fond carré sans poignées", "SFCSP"),
            ("Sac fond carré avec poignées plates", "SFCPP"),
            ("Sac fond carré avec poignées torsadées", "SFCPT"),
        ]

    def test_every_product_has_all_fields(self):
        for product in catalog.PRODUCTS:
            assert {"id", "type", "code", "description", "usages", "keywords"} <= set(product)
            assert product["usages"]

    @pytest.mark.parametrize("language", ["fr", "darija", "ar", "en"])
    def test_keywords_for_each_language(self, language):
        for product in catalog.PRODUCTS:
            assert product["keywords"][language], (product["type"], language)

    def test_keyword_languages(self):
        for product in catalog.PRODUCTS:
            assert set(product["keywords"]) == {"fr", "darija", "ar", "en"}

    def test_keywords_are_trimmed_lowercase_texts_without_duplicates(self):
        for product in catalog.PRODUCTS:
            for language, words in product["keywords"].items():
                assert len(words) == len(set(words)), (product["type"], language)
                for word in words:
                    assert isinstance(word, str) and word == word.strip() and word, (language, word)
                    assert word == word.lower() or language != "fr", (language, word)

    def test_arabic_keywords_use_arabic_script(self):
        for product in catalog.PRODUCTS:
            assert all(ARABIC_LETTER.search(word) for word in product["keywords"]["ar"])

    def test_latin_keywords_have_no_arabic_script(self):
        for product in catalog.PRODUCTS:
            for language in ("fr", "darija", "en"):
                assert not any(ARABIC_LETTER.search(word) for word in product["keywords"][language])

    @pytest.mark.parametrize("product_id, keyword", [
        (1, "flat bottom bag"), (1, "sachiat"), (2, "without handles"), (2, "sac sos"), (2, "بدون مقابض"),
        (3, "flat handles"), (3, "مقابض مسطحة"), (4, "twisted handles"), (4, "mfetlin"), (4, "مقابض مفتولة"),
    ])
    def test_synonyms(self, product_id, keyword):
        product = catalog.get_product(product_id)
        assert any(keyword in words for words in product["keywords"].values())

    def test_english_keywords_by_language(self):
        assert "twisted handles" in catalog.product_keywords("en")["Sac fond carré avec poignées torsadées"]

    def test_product_types_list(self):
        assert catalog.PRODUCT_TYPES == [p["type"] for p in catalog.PRODUCTS]

    def test_lookups(self):
        assert catalog.get_product(3)["code"] == "SFCPP"
        assert catalog.get_product(9) is None
        assert catalog.get_product_by_type("sachets fond plat")["id"] == 1
        assert catalog.get_product_by_code("sfcpt")["id"] == 4
        assert catalog.get_product_by_code("XXX") is None

    def test_product_keywords_by_language(self):
        keywords = catalog.product_keywords("darija")
        assert "yddin mfetlin" in keywords["Sac fond carré avec poignées torsadées"]


class TestReferenceData:
    """Papiers, fournisseurs, grammages, laizes, entreprise."""

    def test_paper_codes(self):
        assert set(catalog.PAPER_TYPES.values()) == {"KB", "KE"}
        assert set(catalog.PAPER_TYPE_NAMES) == {"KB", "KE"}

    def test_most_specific_paper_type_first(self):
        keys = list(catalog.PAPER_TYPES)
        assert keys.index("kraft blanchi") < keys.index("kraft") < keys.index("blanchi")

    def test_standard_values_sorted(self):
        assert catalog.STANDARD_GRAMMAGES == sorted(catalog.STANDARD_GRAMMAGES)
        assert catalog.STANDARD_LAIZES == sorted(catalog.STANDARD_LAIZES)

    def test_company_info(self):
        assert catalog.COMPANY_INFO["name"] == "TECPAP"
        assert catalog.COMPANY_INFO["website"] == "www.tecpap.ma"

    def test_required_order_fields(self):
        assert catalog.REQUIRED_ORDER_FIELDS == ("entreprise_cliente", "type_produit", "quantite")


class TestReorderPatterns:
    """Expressions de renouvellement par langue."""

    def test_languages(self):
        assert set(catalog.REORDER_PATTERNS) == {"fr", "darija", "ar", "en"}

    @pytest.mark.parametrize("language, pattern", [
        ("fr", "comme d'habitude"), ("fr", "renouvellement"), ("darija", "kif dima"), ("darija", "b7al l3ada"),
        ("ar", "كالعادة"), ("en", "as usual"),
    ])
    def test_expected_patterns(self, language, pattern):
        assert pattern in catalog.REORDER_PATTERNS[language]

    def test_flat_list_has_no_duplicates(self):
        assert len(catalog.ALL_REORDER_PATTERNS) == len(set(catalog.ALL_REORDER_PATTERNS))
        for patterns in catalog.REORDER_PATTERNS.values():
            assert set(patterns) <= set(catalog.ALL_REORDER_PATTERNS)


class TestSingleSourceOfTruth:
    """Les autres modules réutilisent les constantes du catalogue."""

    def test_article_codes_use_catalog(self):
        assert article_codes.PAPER_TYPES is catalog.PAPER_TYPES
        assert article_codes.SUPPLIERS is catalog.SUPPLIERS
        assert article_codes.STANDARD_GRAMMAGES is catalog.STANDARD_GRAMMAGES
        assert article_codes.STANDARD_LAIZES is catalog.STANDARD_LAIZES
        assert set(article_codes.SAC_TYPES) == set(catalog.PRODUCT_TYPES)

    def test_extractor_uses_catalog(self):
        assert extractor.PRODUCT_TYPES is catalog.PRODUCT_TYPES
        assert extractor.COMPANY_INFO is catalog.COMPANY_INFO
        assert extractor.REORDER_PATTERNS == catalog.ALL_REORDER_PATTERNS

    def test_database_catalog_built_from_products(self):
        assert database.PRODUCT_CATALOG == [
            {"id": p["id"], "type": p["type"], "description": p["description"]} for p in catalog.PRODUCTS
        ]

    def test_standard_codes_cover_catalog_values(self):
        codes = article_codes.get_all_standard_codes()
        assert {c["grammage"] for c in codes} == set(catalog.STANDARD_GRAMMAGES)
        assert {c["laize"] for c in codes} == set(catalog.STANDARD_LAIZES)


class TestArticleCodes:
    """Construction et lecture des codes articles."""

    def test_generate(self):
        assert article_codes.generate_article_code("kraft blanchi", 100, 28, "mondi") == "KB100L28MON"
        assert article_codes.generate_article_code() == "KE80"

    def test_parse(self):
        assert article_codes.parse_article_code("KB100L28MON") == {
            "paper_type": "Kraft Blanchi", "grammage": 100, "laize": 28, "supplier": "Mondi", "raw_code": "KB100L28MON",
        }
        assert article_codes.parse_article_code("") is None

    def test_suggest(self):
        assert article_codes.suggest_article_code_from_description("sachets kraft blanchi 80g laize 25") == "KB80L25"
        assert article_codes.suggest_article_code_from_description(None) is None
