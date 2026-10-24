"""Tests du module tecpap.extraction.extractor avec un client OpenAI simulé (aucun appel réseau)."""

import json
from unittest.mock import Mock, patch

import pytest

from tecpap.config import Settings
from tecpap.extraction import extractor as extractor_module
from tecpap.extraction.extractor import (
    PRODUCT_TYPES,
    REORDER_PATTERNS,
    DataExtractor,
    auto_generate_article_code,
)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Les extracteurs créés sans configuration explicite utilisent la configuration de test."""
    settings = Settings.for_tests(tmp_path)
    monkeypatch.setattr(extractor_module, "load_settings", lambda: settings)
    return settings


def _llm_response(content):
    response = Mock()
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = content
    return response


def _client_returning(*contents):
    client = Mock()
    client.chat.completions.create.side_effect = [_llm_response(content) for content in contents]
    return client


NOT_A_REORDER = json.dumps({"is_reorder": False, "client_name": None, "confidence": 90})


class TestDataExtractorInit:
    """Initialisation."""

    def test_data_extractor_creates_instance(self):
        assert DataExtractor() is not None

    def test_data_extractor_has_model(self):
        extractor = DataExtractor()
        assert extractor.model == "gpt-4o"

    def test_model_comes_from_settings(self, tmp_path):
        extractor = DataExtractor(settings=Settings.for_tests(tmp_path, model_extraction="modele-test"))
        assert extractor.model == "modele-test"

    def test_no_client_without_api_key(self):
        assert DataExtractor().client is None

    def test_client_created_with_api_key(self, tmp_path):
        with patch("tecpap.extraction.extractor.OpenAI") as openai_class:
            DataExtractor(settings=Settings.for_tests(tmp_path, openai_api_key="sk-test"))
        openai_class.assert_called_once()

    def test_data_extractor_accepts_db_manager(self):
        mock_db = Mock()
        assert DataExtractor(db_manager=mock_db).db is mock_db

    def test_set_database_method_exists(self):
        assert callable(DataExtractor().set_database)

    def test_set_database_updates_db(self):
        extractor = DataExtractor()
        mock_db = Mock()
        extractor.set_database(mock_db)
        assert extractor.db is mock_db


class TestProductTypes:
    """Types de produits."""

    def test_product_types_defined(self):
        assert isinstance(PRODUCT_TYPES, list)

    def test_product_types_not_empty(self):
        assert len(PRODUCT_TYPES) == 4

    def test_product_types_contains_sachets(self):
        assert any("sachet" in p.lower() for p in PRODUCT_TYPES)

    def test_product_types_contains_sacs(self):
        assert any("sac" in p.lower() for p in PRODUCT_TYPES)


class TestReorderPatterns:
    """Expressions de renouvellement."""

    def test_reorder_patterns_defined(self):
        assert isinstance(REORDER_PATTERNS, list)

    def test_reorder_patterns_not_empty(self):
        assert len(REORDER_PATTERNS) > 0

    def test_reorder_patterns_contains_french(self):
        assert {"comme d'habitude", "même commande", "renouveler"} <= set(REORDER_PATTERNS)

    def test_reorder_patterns_contains_darija(self):
        assert {"kif dima", "bhal dima", "b7al dima"} <= set(REORDER_PATTERNS)

    def test_reorder_patterns_contains_arabic(self):
        assert "كالعادة" in REORDER_PATTERNS


class TestClientNameNormalization:
    """Normalisation des noms de clients."""

    @pytest.fixture
    def extractor(self):
        return DataExtractor()

    def test_normalize_client_name_method_exists(self, extractor):
        assert callable(extractor.normalize_client_name)

    def test_normalize_empty_name(self, extractor):
        assert extractor.normalize_client_name("") == ""

    def test_normalize_none_name(self, extractor):
        assert extractor.normalize_client_name(None) == ""

    def test_normalize_lowercase(self, extractor):
        result = extractor.normalize_client_name("AHMED BENALI")
        assert result == result.lower()

    def test_normalize_removes_accents(self, extractor):
        assert extractor.normalize_client_name("Société Café") == "societe cafe"


class TestErrorHandling:
    """Noms de clients inhabituels."""

    @pytest.fixture
    def extractor(self):
        return DataExtractor()

    def test_normalize_handles_special_characters(self, extractor):
        assert extractor.normalize_client_name("Société M'hamid-Café") == "societe mhamid cafe"

    def test_normalize_handles_numbers(self, extractor):
        assert extractor.normalize_client_name("Company 123 Inc") == "company 123 inc"


class TestExtractFromEmailMethod:
    """Méthodes publiques de l'extraction."""

    def test_extract_from_email_method_exists(self):
        assert callable(DataExtractor().extract_from_email)

    def test_detect_reorder_intent_method_exists(self):
        assert callable(DataExtractor().detect_reorder_intent)

    def test_find_matching_client_method_exists(self):
        assert callable(DataExtractor().find_matching_client)


class TestClientMatching:
    """Rapprochement d'un nom cité avec les clients connus."""

    def test_find_matching_client(self, temp_db):
        temp_db.get_or_create_client("Chhiwat Fès")
        extractor = DataExtractor(db_manager=temp_db)
        assert extractor.find_matching_client("chhiwat fes") == "Chhiwat Fès"

    def test_no_match_returns_none(self, temp_db):
        temp_db.get_or_create_client("Boulangerie Atlas")
        assert DataExtractor(db_manager=temp_db).find_matching_client("Pizzeria Rio") is None

    def test_without_database(self):
        assert DataExtractor().find_matching_client("Atlas") is None

    def test_last_order_prefers_validated(self, temp_db):
        first = temp_db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 100, "email_id": "a"})
        temp_db.create_order({"entreprise_cliente": "Snack Atlas", "quantite": 999, "email_id": "b"})
        temp_db.update_order_status(first, "validee")
        last = DataExtractor(db_manager=temp_db).get_client_last_order("Snack Atlas")
        assert last["id"] == first


class TestFillFromHistory:
    """Complément d'une commande depuis l'historique."""

    def test_missing_fields_are_filled(self):
        extracted = {"entreprise_cliente": "Atlas", "quantite": None, "confiance": 40}
        last_order = {"id": 7, "produit_type": "Sachets fond plat", "quantite": 2000, "unite": "pièces"}
        result = DataExtractor().fill_from_history(extracted, last_order)
        assert result["quantite"] == 2000
        assert result["type_produit"] == "Sachets fond plat"
        assert result["filled_from_history"] is True
        assert result["history_source_order"] == "ID-7"
        assert result["confiance"] == 85

    def test_extracted_fields_are_kept(self):
        result = DataExtractor().fill_from_history({"quantite": 50}, {"quantite": 2000})
        assert result["quantite"] == 50
        assert "filled_from_history" not in result


class TestDataExtractorWithMockedAPI:
    """Extraction complète avec un client OpenAI simulé."""

    def test_extract_with_mocked_api(self):
        client = _client_returning(NOT_A_REORDER, json.dumps({"est_bon_commande": True, "quantite": 1000}))
        extractor = DataExtractor(client=client)
        assert extractor.client is client
        assert extractor.extract_from_email({"body": "1000 sachets"})["quantite"] == 1000

    def test_extract_without_client_returns_none(self):
        assert DataExtractor().extract_from_email({"subject": "Commande", "body": "500 sachets"}) is None

    def test_standard_extraction(self):
        order = {"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 500,
                 "nature_produit": "sachet kraft blanchi 80g", "est_bon_commande": True}
        client = _client_returning(NOT_A_REORDER, "```json\n" + json.dumps(order) + "\n```")
        result = DataExtractor(client=client).extract_from_email({"subject": "Commande", "body": "500 sachets"})
        assert result["entreprise_cliente"] == "Snack Atlas"
        assert result["code_article"] == "KB80"
        assert client.chat.completions.create.call_count == 2

    def test_extraction_uses_low_temperature_and_catalog(self):
        client = _client_returning(NOT_A_REORDER, json.dumps({"est_bon_commande": False}))
        DataExtractor(client=client).extract_from_email({"body": "bonjour"})
        call = client.chat.completions.create.call_args_list[1]
        assert call.kwargs["temperature"] == 0.1
        prompt = call.kwargs["messages"][1]["content"]
        for product_type in PRODUCT_TYPES:
            assert product_type in prompt

    def test_invalid_json_returns_none(self):
        client = _client_returning(NOT_A_REORDER, "pas du JSON")
        assert DataExtractor(client=client).extract_from_email({"body": "x"}) is None

    def test_reorder_is_filled_from_history(self, temp_db):
        order_id = temp_db.create_order({
            "entreprise_cliente": "Chhiwat Fès", "type_produit": "Sac fond carré avec poignées plates",
            "nature_produit": "sacs poignées plates", "quantite": 8000, "unite": "pièces", "email_id": "old",
        })
        temp_db.update_order_status(order_id, "validee")
        reorder = json.dumps({"is_reorder": True, "client_name": "chhiwat fes", "reorder_indicators": ["b7al dima"]})
        client = _client_returning(reorder, json.dumps({"entreprise_cliente": None, "est_bon_commande": False}))

        result = DataExtractor(db_manager=temp_db, client=client).extract_from_email({"body": "commande b7al dima"})

        assert result["entreprise_cliente"] == "Chhiwat Fès"
        assert result["quantite"] == 8000
        assert result["is_reorder"] is True
        assert result["est_bon_commande"] is True


class TestDetectReorderIntent:
    """Détection d'une demande de renouvellement."""

    def test_detect_reorder_returns_dict(self):
        client = _client_returning(json.dumps({"is_reorder": False, "client_name": None, "confidence": 50}))
        result = DataExtractor(client=client).detect_reorder_intent("Bonjour, nouvelle commande")
        assert isinstance(result, dict)
        assert "is_reorder" in result

    def test_detect_reorder_returns_default_on_error(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("panne")
        result = DataExtractor(client=client).detect_reorder_intent("Bonjour")
        assert result == {"is_reorder": False, "client_name": None, "confidence": 0}

    def test_detect_reorder_uses_light_model(self, tmp_path):
        client = _client_returning(NOT_A_REORDER)
        extractor = DataExtractor(settings=Settings.for_tests(tmp_path, model_light="leger"), client=client)
        extractor.detect_reorder_intent("x")
        assert client.chat.completions.create.call_args.kwargs["model"] == "leger"


class TestArticleCodeGeneration:
    """Code article calculé quand l'extraction n'en fournit pas."""

    def test_existing_code_is_kept(self):
        assert auto_generate_article_code({"code_article": "KE70L30"}) == "KE70L30"

    def test_code_from_description(self):
        assert auto_generate_article_code({"nature_produit": "kraft blanchi 100g laize 28 mondi"}) == "KB100L28MON"

    def test_default_code(self):
        assert auto_generate_article_code({}) == "KE80"


class TestPDFExtraction:
    """Extraction du texte des PDF (pypdf)."""

    def test_extract_text_from_pdf_method_exists(self):
        assert callable(DataExtractor().extract_text_from_pdf)

    def test_extract_pdf_nonexistent_file(self):
        assert DataExtractor().extract_text_from_pdf("/nonexistent/path.pdf") == ""

    def test_extract_pdf_text(self, tmp_path):
        from reportlab.pdfgen import canvas

        pdf_path = tmp_path / "bon.pdf"
        pdf = canvas.Canvas(str(pdf_path))
        pdf.drawString(100, 750, "Bon de commande 3000 sachets")
        pdf.save()
        assert "3000 sachets" in DataExtractor().extract_text_from_pdf(str(pdf_path))


class TestImageExtraction:
    """Lecture du texte des images."""

    def test_extract_text_from_image_method_exists(self):
        assert callable(DataExtractor().extract_text_from_image)

    def test_image_without_client_returns_empty(self, tmp_path):
        path = tmp_path / "photo.png"
        path.write_bytes(b"\x89PNG")
        assert DataExtractor().extract_text_from_image(str(path)) == ""


class TestAttachments:
    """Pièces jointes de types variés."""

    def test_text_attachment(self, tmp_path):
        path = tmp_path / "commande.txt"
        path.write_text("bghit 2000 sachet", encoding="utf-8")
        assert DataExtractor().process_attachments([str(path)]) == {"commande.txt": "bghit 2000 sachet"}

    def test_unsupported_attachment_is_ignored(self, tmp_path):
        path = tmp_path / "archive.zip"
        path.write_bytes(b"PK")
        assert DataExtractor().process_attachments([str(path)]) == {}


class TestModuleImports:
    """Noms importables depuis le module."""

    def test_import_data_extractor_module(self):
        from tecpap.extraction import extractor

        assert hasattr(extractor, "DataExtractor")

    def test_import_product_types(self):
        from tecpap.extraction.extractor import PRODUCT_TYPES

        assert PRODUCT_TYPES is not None

    def test_import_reorder_patterns(self):
        from tecpap.extraction.extractor import REORDER_PATTERNS

        assert REORDER_PATTERNS is not None

    def test_import_company_info(self):
        from tecpap.extraction.extractor import COMPANY_INFO

        assert COMPANY_INFO["name"] == "TECPAP"
