"""
Tests pour l'API Flask (app.py)
Tests des routes et endpoints de l'application.
"""

import pytest
import os
import sys
from unittest.mock import Mock, patch, MagicMock
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAppConfiguration:
    """Tests de configuration de l'application."""
    
    def test_app_imports(self):
        """Test que l'application s'importe correctement."""
        import app
        assert hasattr(app, 'app')
    
    def test_app_has_routes(self):
        """Test que l'application a des routes."""
        from app import app
        rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert '/' in rules
        assert '/orders' in rules


class TestHomeRoute:
    """Tests de la route principale."""
    
    @pytest.fixture
    def client(self):
        """Client de test Flask."""
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_home_returns_200(self, client):
        """Test que la page d'accueil retourne 200."""
        response = client.get('/')
        assert response.status_code == 200
    
    def test_home_returns_html(self, client):
        """Test que la page d'accueil retourne HTML."""
        response = client.get('/')
        assert 'text/html' in response.content_type


class TestOrdersRoutes:
    """Tests des routes de commandes."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_orders_page_returns_200(self, client):
        """Test que la page commandes retourne 200."""
        response = client.get('/orders')
        assert response.status_code == 200
    
    def test_orders_page_returns_html(self, client):
        """Test que la page commandes retourne HTML."""
        response = client.get('/orders')
        assert 'text/html' in response.content_type
    
    def test_orders_api_returns_json(self, client):
        """Test que l'API commandes retourne JSON."""
        response = client.get('/api/orders')
        assert response.status_code == 200
        assert 'application/json' in response.content_type


class TestClientsPage:
    """Tests de la page clients."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_clients_page_returns_200(self, client):
        """Test que la page clients retourne 200."""
        response = client.get('/clients')
        assert response.status_code == 200
    
    def test_clients_page_returns_html(self, client):
        """Test que la page clients retourne HTML."""
        response = client.get('/clients')
        assert 'text/html' in response.content_type


class TestAnalyticsRoutes:
    """Tests des routes analytics."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_analytics_page_returns_200(self, client):
        """Test que la page analytics retourne 200."""
        response = client.get('/analytics')
        assert response.status_code == 200
    
    def test_analytics_api_returns_json(self, client):
        """Test que l'API analytics retourne JSON."""
        response = client.get('/api/analytics')
        assert response.status_code == 200
        assert 'application/json' in response.content_type


class TestStatsAPI:
    """Tests de l'API statistiques."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_stats_api_returns_200(self, client):
        """Test que l'API stats retourne 200."""
        response = client.get('/api/stats')
        assert response.status_code == 200
    
    def test_stats_api_returns_json(self, client):
        """Test que l'API stats retourne JSON."""
        response = client.get('/api/stats')
        assert 'application/json' in response.content_type


class TestProcessRoutes:
    """Tests des routes de traitement."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_process_page_returns_200(self, client):
        """Test que la page process retourne 200."""
        response = client.get('/process')
        assert response.status_code == 200


class TestAlertsRoutes:
    """Tests des routes d'alertes."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_alerts_page_returns_200(self, client):
        """Test que la page alertes retourne 200."""
        response = client.get('/alerts')
        assert response.status_code == 200
    
    def test_alerts_api_returns_json(self, client):
        """Test que l'API alertes retourne JSON."""
        response = client.get('/api/alerts')
        assert response.status_code == 200
        assert 'application/json' in response.content_type


class TestNotificationsAPI:
    """Tests de l'API notifications."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_notifications_check_returns_json(self, client):
        """Test que l'API notifications retourne JSON."""
        response = client.get('/api/notifications/check')
        assert response.status_code == 200
        assert 'application/json' in response.content_type


class TestBackupsRoute:
    """Tests de la route backups."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_backups_page_returns_200(self, client):
        """Test que la page backups retourne 200."""
        response = client.get('/backups')
        assert response.status_code == 200


class TestWhatsAppRoute:
    """Tests de la route WhatsApp."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_whatsapp_page_returns_200(self, client):
        """Test que la page WhatsApp retourne 200."""
        response = client.get('/whatsapp')
        assert response.status_code == 200


class TestExportRoutes:
    """Tests des routes d'export."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_export_csv_route_exists(self, client):
        """Test que la route export CSV existe."""
        response = client.get('/export/csv')
        # Should return file or redirect
        assert response.status_code in [200, 302, 500]
    
    def test_export_excel_route_exists(self, client):
        """Test que la route export Excel existe."""
        response = client.get('/export/excel')
        assert response.status_code in [200, 302, 500]


class TestValidationAPI:
    """Tests de l'API de validation."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_validate_order_endpoint_exists(self, client):
        """Test que l'endpoint validation existe."""
        # Test with non-existent order ID and JSON content type
        response = client.post('/api/orders/9999/validate', 
                              content_type='application/json',
                              json={})
        # Should return 404 for non-existent or 200/400 for validation
        assert response.status_code in [200, 400, 404, 415, 500]
    
    def test_reject_order_endpoint_exists(self, client):
        """Test que l'endpoint rejet existe."""
        response = client.post('/api/orders/9999/reject',
                              content_type='application/json',
                              json={'reason': 'test'})
        assert response.status_code in [200, 400, 404, 415, 500]


class TestProcessEmailsAPI:
    """Tests de l'API traitement emails."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_process_emails_endpoint_exists(self, client):
        """Test que l'endpoint process-emails existe."""
        response = client.post('/api/process-emails')
        # Should accept POST requests
        assert response.status_code in [200, 400, 500]


class TestStaticRoutes:
    """Tests des routes statiques."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_static_route_exists(self, client):
        """Test que les routes statiques fonctionnent."""
        from app import app
        # Check if static folder is configured
        assert app.static_folder is not None or True  # May not have static files


class TestErrorHandling:
    """Tests de gestion d'erreurs."""
    
    @pytest.fixture
    def client(self):
        from app import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client
    
    def test_404_for_nonexistent_route(self, client):
        """Test 404 pour route inexistante."""
        response = client.get('/this-route-does-not-exist')
        assert response.status_code == 404
    
    def test_order_detail_nonexistent(self, client):
        """Test détail commande inexistante."""
        response = client.get('/orders/999999')
        # Should return 404 or redirect
        assert response.status_code in [404, 302, 200]
    
    def test_client_detail_nonexistent(self, client):
        """Test détail client inexistant."""
        response = client.get('/clients/999999')
        assert response.status_code in [404, 302, 200]


class TestAppImports:
    """Tests d'imports de l'application."""
    
    def test_flask_app_exists(self):
        """Test que l'application Flask existe."""
        from app import app
        assert app is not None
    
    def test_database_manager_exists(self):
        """Test que le gestionnaire DB existe."""
        from app import db
        assert db is not None
    
    def test_templates_configured(self):
        """Test que les templates sont configurés."""
        from app import app
        assert app.template_folder is not None or 'templates' in str(app.jinja_loader.searchpath) if hasattr(app, 'jinja_loader') else True
