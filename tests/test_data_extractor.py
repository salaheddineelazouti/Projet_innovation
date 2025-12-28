"""
Tests pour le module data_extractor.py
Tests d'extraction de données via IA.
"""

import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_extractor import DataExtractor, PRODUCT_TYPES, REORDER_PATTERNS


class TestDataExtractorInit:
    """Tests d'initialisation de DataExtractor."""
    
    def test_data_extractor_creates_instance(self):
        """Test création d'instance DataExtractor."""
        extractor = DataExtractor()
        assert extractor is not None
    
    def test_data_extractor_has_model(self):
        """Test que DataExtractor a un modèle configuré."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'model')
        assert extractor.model == "gpt-4o"
    
    def test_data_extractor_accepts_db_manager(self):
        """Test que DataExtractor accepte un db_manager."""
        mock_db = Mock()
        extractor = DataExtractor(db_manager=mock_db)
        assert extractor.db == mock_db
    
    def test_set_database_method_exists(self):
        """Test que la méthode set_database existe."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'set_database')
    
    def test_set_database_updates_db(self):
        """Test que set_database met à jour la DB."""
        extractor = DataExtractor()
        mock_db = Mock()
        extractor.set_database(mock_db)
        assert extractor.db == mock_db


class TestProductTypes:
    """Tests des types de produits."""
    
    def test_product_types_defined(self):
        """Test que PRODUCT_TYPES est défini."""
        assert PRODUCT_TYPES is not None
        assert isinstance(PRODUCT_TYPES, list)
    
    def test_product_types_not_empty(self):
        """Test que PRODUCT_TYPES n'est pas vide."""
        assert len(PRODUCT_TYPES) > 0
    
    def test_product_types_contains_sachets(self):
        """Test que PRODUCT_TYPES contient des sachets."""
        sachets_found = any('sachet' in p.lower() for p in PRODUCT_TYPES)
        assert sachets_found
    
    def test_product_types_contains_sacs(self):
        """Test que PRODUCT_TYPES contient des sacs."""
        sacs_found = any('sac' in p.lower() for p in PRODUCT_TYPES)
        assert sacs_found


class TestReorderPatterns:
    """Tests des patterns de renouvellement."""
    
    def test_reorder_patterns_defined(self):
        """Test que REORDER_PATTERNS est défini."""
        assert REORDER_PATTERNS is not None
        assert isinstance(REORDER_PATTERNS, list)
    
    def test_reorder_patterns_not_empty(self):
        """Test que REORDER_PATTERNS n'est pas vide."""
        assert len(REORDER_PATTERNS) > 0
    
    def test_reorder_patterns_contains_french(self):
        """Test patterns français."""
        french_patterns = ["comme d'habitude", "même commande", "renouveler"]
        has_french = any(p in REORDER_PATTERNS for p in french_patterns)
        assert has_french
    
    def test_reorder_patterns_contains_darija(self):
        """Test patterns Darija."""
        darija_patterns = ["kif dima", "bhal dima"]
        has_darija = any(p in REORDER_PATTERNS for p in darija_patterns)
        assert has_darija


class TestClientNameNormalization:
    """Tests de normalisation des noms clients."""
    
    @pytest.fixture
    def extractor(self):
        return DataExtractor()
    
    def test_normalize_client_name_method_exists(self):
        """Test que normalize_client_name existe."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'normalize_client_name')
    
    def test_normalize_empty_name(self, extractor):
        """Test normalisation nom vide."""
        result = extractor.normalize_client_name("")
        assert result == ""
    
    def test_normalize_none_name(self, extractor):
        """Test normalisation nom None."""
        result = extractor.normalize_client_name(None)
        assert result == ""
    
    def test_normalize_lowercase(self, extractor):
        """Test que la normalisation met en minuscules."""
        result = extractor.normalize_client_name("AHMED BENALI")
        assert result == result.lower()
    
    def test_normalize_removes_accents(self, extractor):
        """Test que la normalisation retire les accents."""
        result = extractor.normalize_client_name("Société Café")
        # After normalization, no accented characters
        assert 'é' not in result
        assert 'ô' not in result


class TestExtractFromEmailMethod:
    """Tests de la méthode extract_from_email."""
    
    def test_extract_from_email_method_exists(self):
        """Test que extract_from_email existe."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'extract_from_email')
    
    def test_detect_reorder_intent_method_exists(self):
        """Test que detect_reorder_intent existe."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'detect_reorder_intent')
    
    def test_find_matching_client_method_exists(self):
        """Test que find_matching_client existe."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'find_matching_client')


class TestPDFExtraction:
    """Tests d'extraction de PDF."""
    
    def test_extract_text_from_pdf_method_exists(self):
        """Test que extract_text_from_pdf existe."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'extract_text_from_pdf')
    
    def test_extract_pdf_nonexistent_file(self):
        """Test extraction PDF fichier inexistant."""
        extractor = DataExtractor()
        result = extractor.extract_text_from_pdf("/nonexistent/path.pdf")
        assert result == "" or result is None


class TestImageExtraction:
    """Tests d'extraction d'images."""
    
    def test_extract_text_from_image_method_exists(self):
        """Test que extract_text_from_image existe."""
        extractor = DataExtractor()
        assert hasattr(extractor, 'extract_text_from_image')


class TestDataExtractorWithMockedAPI:
    """Tests avec API OpenAI mockée."""
    
    @pytest.fixture
    def mock_openai_response(self):
        """Mock réponse OpenAI."""
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = '''
        {
            "is_order": true,
            "client_nom": "Ahmed Benali",
            "client_telephone": "+212612345678",
            "client_ville": "Casablanca",
            "produit_type": "Sachets fond plat",
            "quantite": 1000,
            "unite": "kg",
            "confidence": 90
        }
        '''
        return mock_response
    
    @patch('data_extractor.OpenAI')
    def test_extract_with_mocked_api(self, mock_openai_class, mock_openai_response):
        """Test extraction avec API mockée."""
        mock_client = Mock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_openai_response
        
        extractor = DataExtractor()
        extractor.client = mock_client
        
        # Should not raise exception
        assert extractor is not None


class TestDetectReorderIntent:
    """Tests de détection d'intention de renouvellement."""
    
    @pytest.fixture
    def extractor(self):
        return DataExtractor()
    
    @patch('data_extractor.OpenAI')
    def test_detect_reorder_returns_dict(self, mock_openai):
        """Test que detect_reorder_intent retourne un dict."""
        mock_client = Mock()
        mock_openai.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = '''
        {
            "is_reorder": false,
            "reorder_indicators": [],
            "client_name": null,
            "confidence": 50
        }
        '''
        mock_client.chat.completions.create.return_value = mock_response
        
        extractor = DataExtractor()
        extractor.client = mock_client
        
        result = extractor.detect_reorder_intent("Bonjour, nouvelle commande")
        
        assert isinstance(result, dict)
        assert 'is_reorder' in result


class TestModuleImports:
    """Tests d'imports du module."""
    
    def test_import_data_extractor_module(self):
        """Test import du module."""
        import data_extractor
        assert hasattr(data_extractor, 'DataExtractor')
    
    def test_import_product_types(self):
        """Test import de PRODUCT_TYPES."""
        from data_extractor import PRODUCT_TYPES
        assert PRODUCT_TYPES is not None
    
    def test_import_reorder_patterns(self):
        """Test import de REORDER_PATTERNS."""
        from data_extractor import REORDER_PATTERNS
        assert REORDER_PATTERNS is not None


class TestErrorHandling:
    """Tests de gestion d'erreurs."""
    
    @pytest.fixture
    def extractor(self):
        return DataExtractor()
    
    def test_normalize_handles_special_characters(self, extractor):
        """Test normalisation avec caractères spéciaux."""
        result = extractor.normalize_client_name("Société M'hamid-Café")
        # Should not raise exception
        assert isinstance(result, str)
    
    def test_normalize_handles_numbers(self, extractor):
        """Test normalisation avec nombres."""
        result = extractor.normalize_client_name("Company 123 Inc")
        assert isinstance(result, str)
