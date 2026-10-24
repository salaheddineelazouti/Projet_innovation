"""Fixtures pytest partagées.

Chaque test qui a besoin de l'application ou de la base reçoit une configuration isolée
(``Settings.for_tests``) dont tous les chemins pointent dans le dossier temporaire du test :
aucun test ne lit le fichier ``.env`` du poste ni ne crée ``orders.db`` à la racine du dépôt,
et aucun service externe n'est appelé.
"""

from __future__ import annotations

import pytest

from tecpap.config import Settings
from tecpap.db.database import DatabaseManager
from tecpap.web import create_app


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    """Configuration de test : chemins dans ``tmp_path``, aucune clé d'API."""
    return Settings.for_tests(tmp_path)


@pytest.fixture
def temp_db(test_settings):
    """Base SQLite temporaire initialisée."""
    db = DatabaseManager(db_file=test_settings.database_path)
    db.connect()
    db.init_database()
    yield db
    db.disconnect()


@pytest.fixture
def flask_app(test_settings):
    """Application Flask construite sur la configuration de test."""
    app = create_app(test_settings)
    yield app
    app.extensions["tecpap"].close()


@pytest.fixture
def flask_client(flask_app):
    """Client de test Flask (requêtes HTTP simulées, sans serveur)."""
    with flask_app.test_client() as client:
        yield client


@pytest.fixture
def services(flask_app):
    """Services de l'application de test (base, clients WhatsApp et email…)."""
    return flask_app.extensions["tecpap"]


@pytest.fixture
def sample_order_data():
    """Commande reçue par email."""
    return {
        "entreprise_cliente": "Test Company SARL",
        "type_produit": "Sachets fond plat",
        "quantite": 5000,
        "unite": "pièces",
        "prix_unitaire": 0.5,
        "devise": "MAD",
        "date_livraison": "2025-01-15",
        "confiance": 95,
        "source": "email",
        "email_id": "test_email_001",
        "email_subject": "Commande Test",
        "email_from": "client@test.com",
    }


@pytest.fixture
def sample_whatsapp_order():
    """Commande reçue par WhatsApp."""
    return {
        "entreprise_cliente": "Restaurant Atlas",
        "type_produit": "Sac fond carré sans poignées",
        "quantite": 1000,
        "unite": "pièces",
        "prix_unitaire": None,
        "devise": "MAD",
        "confiance": 90,
        "source": "whatsapp",
        "whatsapp_from": "+212612345678",
    }


@pytest.fixture
def sample_client_data():
    """Coordonnées d'un client."""
    return {
        "nom": "Entreprise Test",
        "email": "contact@test.ma",
        "telephone": "+212612345678",
        "adresse": "123 Rue Test, Casablanca",
    }


@pytest.fixture
def temp_backup_dir(tmp_path):
    """Dossier temporaire de sauvegardes."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(exist_ok=True)
    return backup_dir
