"""Fixtures pytest partagées.

Chaque test qui a besoin de l'application ou de la base reçoit une configuration isolée
(``Settings.for_tests``) dont tous les chemins pointent dans le dossier temporaire du test :
aucun test ne lit le fichier ``.env`` du poste ni ne crée ``orders.db`` à la racine du dépôt,
et aucun service externe n'est appelé.

``flask_app`` et ``flask_client`` fonctionnent sans connexion ni protection CSRF ;
``secure_app`` et ``secure_client`` activent les deux, avec le mot de passe
:data:`TEST_ADMIN_PASSWORD`.
"""

from __future__ import annotations

import pytest
from werkzeug.security import generate_password_hash

from tecpap.config import Settings
from tecpap.db.database import DatabaseManager
from tecpap.web import create_app

TEST_ADMIN_USERNAME = "admin"
TEST_ADMIN_PASSWORD = "mot-de-passe-de-test"
# PBKDF2 avec peu d'itérations : la vérification reste rapide dans les tests.
TEST_ADMIN_PASSWORD_HASH = generate_password_hash(TEST_ADMIN_PASSWORD, method="pbkdf2:sha256:1000")


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
def secure_settings(tmp_path) -> Settings:
    """Configuration de test avec connexion obligatoire et protection CSRF."""
    return Settings.for_tests(
        tmp_path,
        auth_enabled=True,
        csrf_enabled=True,
        admin_username=TEST_ADMIN_USERNAME,
        admin_password_hash=TEST_ADMIN_PASSWORD_HASH,
    )


@pytest.fixture
def secure_app(secure_settings):
    """Application Flask avec connexion obligatoire et protection CSRF."""
    app = create_app(secure_settings)
    yield app
    app.extensions["tecpap"].close()


@pytest.fixture
def secure_client(secure_app):
    """Client de test de :func:`secure_app` (aucune session ouverte au départ)."""
    with secure_app.test_client() as client:
        yield client


@pytest.fixture
def csrf_token_for():
    """Fonction qui renvoie le jeton CSRF de la session d'un client de test.

    Sans jeton dans la session, une page est affichée une fois (la page de connexion, ou
    l'accueil quand la connexion est désactivée) : c'est elle qui crée le jeton, comme dans un
    navigateur.
    """
    def read_token(client) -> str | None:
        with client.session_transaction() as session:
            return session.get("csrf_token")

    def token_for(client) -> str:
        for url in ("/login", "/"):
            token = read_token(client)
            if token:
                return token
            client.get(url)
        token = read_token(client)
        assert token, "aucune page n'a créé de jeton CSRF"
        return token

    return token_for


@pytest.fixture
def login(secure_client, csrf_token_for):
    """Fonction qui envoie le formulaire de connexion (identifiants valides par défaut)."""
    def submit(username: str = TEST_ADMIN_USERNAME, password: str = TEST_ADMIN_PASSWORD,
               next_url: str | None = None, client=None, **environ):
        client = client or secure_client
        data = {"username": username, "password": password, "csrf_token": csrf_token_for(client)}
        if next_url is not None:
            data["next"] = next_url
        return client.post("/login", data=data, environ_base=environ)

    return submit


@pytest.fixture
def logged_in_client(secure_client, login, csrf_token_for):
    """Client de :func:`secure_app` connecté, dont toutes les requêtes portent l'en-tête X-CSRF-Token."""
    response = login()
    assert response.status_code == 302
    secure_client.environ_base["HTTP_X_CSRF_TOKEN"] = csrf_token_for(secure_client)
    return secure_client


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
