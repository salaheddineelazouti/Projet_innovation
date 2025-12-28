"""
Configuration et fixtures pour les tests pytest
"""

import pytest
import os
import sys
import tempfile
import shutil

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    # Create temp directory
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, 'test_orders.db')
    
    # Create database manager
    db = DatabaseManager(db_file=db_path)
    db.connect()
    db.init_database()
    
    yield db
    
    # Cleanup
    db.disconnect()
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_order_data():
    """Sample order data for testing."""
    return {
        'entreprise_cliente': 'Test Company SARL',
        'type_produit': 'Sachets fond plat',
        'quantite': 5000,
        'unite': 'pièces',
        'prix_unitaire': 0.5,
        'devise': 'MAD',
        'date_livraison': '2025-01-15',
        'confiance': 95,
        'source': 'email',
        'email_id': 'test_email_001',
        'email_subject': 'Commande Test',
        'email_from': 'client@test.com'
    }


@pytest.fixture
def sample_whatsapp_order():
    """Sample WhatsApp order data for testing."""
    return {
        'entreprise_cliente': 'Restaurant Atlas',
        'type_produit': 'Sac fond carré sans poignées',
        'quantite': 1000,
        'unite': 'pièces',
        'prix_unitaire': None,
        'devise': 'MAD',
        'confiance': 90,
        'source': 'whatsapp',
        'whatsapp_from': '+212612345678'
    }


@pytest.fixture
def sample_client_data():
    """Sample client data for testing."""
    return {
        'nom': 'Entreprise Test',
        'email': 'contact@test.ma',
        'telephone': '+212612345678',
        'adresse': '123 Rue Test, Casablanca'
    }


@pytest.fixture
def flask_app():
    """Create Flask app for testing."""
    from app import app
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


@pytest.fixture
def flask_client(flask_app):
    """Create Flask test client."""
    return flask_app.test_client()


@pytest.fixture
def temp_backup_dir():
    """Create temporary backup directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)
