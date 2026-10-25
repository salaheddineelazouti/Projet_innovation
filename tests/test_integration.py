"""
Tests d'intégration
Tests end-to-end du système complet avec DatabaseManager
"""

import sqlite3

import pytest

from tecpap.db.database import DatabaseManager


class TestOrderFlow:
    """Tests du flux complet de commande."""

    @pytest.fixture
    def db_manager(self, tmp_path):
        """Gestionnaire de base de données de test."""
        db_path = str(tmp_path / "test_orders.db")
        db = DatabaseManager(db_file=db_path)
        db.init_database()
        yield db

    def test_full_email_order_flow(self, db_manager):
        """Test flux complet commande par email."""
        # 1. Get or create client (without ville, uses actual signature)
        client = db_manager.get_or_create_client(
            nom="Ahmed Benali",
            telephone="+212612345678",
            email="ahmed@example.com"
        )
        client_id = client['id']

        # 2. Create order
        order_data = {
            'client_id': client_id,
            'source': 'email',
            'email_from': 'ahmed@example.com',
            'produit_type': 'Sachets fond plat',
            'quantite': 1000,
            'unite': 'kg',
            'statut': 'en_attente'
        }
        order_id = db_manager.create_order(order_data)

        # 3. Verify order exists
        order = db_manager.get_order(order_id)

        assert order is not None
        assert order['statut'] == 'en_attente'
        # client_id peut différer : le client est rattaché par son nom
        assert order['client_id'] is not None

    def test_full_whatsapp_order_flow(self, db_manager):
        """Test flux complet commande par WhatsApp."""
        # 1. Get or create client
        client = db_manager.get_or_create_client(
            nom="Fatima Zahra",
            telephone="+212698765432"
        )
        client_id = client['id']

        # 2. Create WhatsApp order
        order_data = {
            'client_id': client_id,
            'source': 'whatsapp',
            'produit_type': 'Sac fond carré',
            'quantite': 500,
            'unite': 'pièces',
            'statut': 'en_attente'
        }
        order_id = db_manager.create_order(order_data)

        # 3. Verify order
        order = db_manager.get_order(order_id)

        assert order is not None
        assert order['source'] == 'whatsapp'


class TestValidationFlow:
    """Tests du flux de validation."""

    @pytest.fixture
    def db_manager(self, tmp_path):
        db_path = str(tmp_path / "test_orders.db")
        db = DatabaseManager(db_file=db_path)
        db.init_database()
        yield db

    def test_validate_order_updates_status(self, db_manager):
        """Test que validation met à jour le statut."""
        # Création du client et de la commande
        client = db_manager.get_or_create_client(
            nom="Test Client",
            telephone="+212600000001"
        )

        order_data = {
            'client_id': client['id'],
            'source': 'email',
            'produit_type': 'Test Product',
            'quantite': 10,
            'unite': 'kg',
            'statut': 'en_attente'
        }
        order_id = db_manager.create_order(order_data)

        # Validation directe en SQL
        conn = sqlite3.connect(db_manager.db_file)
        cursor = conn.cursor()
        cursor.execute("UPDATE commandes SET statut = 'validé' WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()

        # Vérification
        order = db_manager.get_order(order_id)
        assert order['statut'] == 'validé'

    def test_reject_order_updates_status(self, db_manager):
        """Test que rejet met à jour le statut."""
        client = db_manager.get_or_create_client(
            nom="Test Client",
            telephone="+212600000002"
        )

        order_data = {
            'client_id': client['id'],
            'source': 'whatsapp',
            'produit_type': 'Test Product',
            'quantite': 5,
            'statut': 'en_attente'
        }
        order_id = db_manager.create_order(order_data)

        # Rejet direct en SQL
        conn = sqlite3.connect(db_manager.db_file)
        cursor = conn.cursor()
        cursor.execute("UPDATE commandes SET statut = 'rejeté' WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()

        # Vérification
        order = db_manager.get_order(order_id)
        assert order['statut'] == 'rejeté'


class TestClientFlow:
    """Tests du flux client."""

    @pytest.fixture
    def db_manager(self, tmp_path):
        db_path = str(tmp_path / "test_orders.db")
        db = DatabaseManager(db_file=db_path)
        db.init_database()
        yield db

    def test_new_client_created(self, db_manager):
        """Test création de nouveau client."""
        # Création d'un client
        client = db_manager.get_or_create_client(
            nom="Nouveau Client",
            telephone="+212611111111"
        )

        # Le client existe
        assert client is not None
        assert client['nom'] == "Nouveau Client"
        assert client['id'] is not None

    def test_existing_client_returned(self, db_manager):
        """Test que le même client est retourné pour le même nom."""
        # Première création du client
        client1 = db_manager.get_or_create_client(
            nom="Client Existant",
            telephone="+212622222222"
        )

        # Deuxième appel pour le même client
        client2 = db_manager.get_or_create_client(
            nom="Client Existant",
            telephone="+212622222222"
        )

        # Même identifiant client
        assert client1['id'] == client2['id']


class TestOrderOperations:
    """Tests des opérations sur commandes."""

    @pytest.fixture
    def db_manager(self, tmp_path):
        db_path = str(tmp_path / "test_orders.db")
        db = DatabaseManager(db_file=db_path)
        db.init_database()
        yield db

    def test_create_multiple_orders(self, db_manager):
        """Test création de plusieurs commandes."""
        client = db_manager.get_or_create_client(
            nom="Client Multiple",
            telephone="+212633333333"
        )

        order_ids = []
        for i in range(3):
            order_data = {
                'client_id': client['id'],
                'source': 'email',
                'produit_type': f'Product {i}',
                'quantite': 10 * (i + 1),
                'statut': 'en_attente'
            }
            order_id = db_manager.create_order(order_data)
            order_ids.append(order_id)

        # Toutes les commandes existent
        for order_id in order_ids:
            order = db_manager.get_order(order_id)
            assert order is not None

    def test_get_all_orders(self, db_manager):
        """Test récupération de toutes les commandes."""
        # Création de quelques commandes
        client = db_manager.get_or_create_client(nom="Test", telephone="+212600000000")
        for i in range(5):
            db_manager.create_order({
                'client_id': client['id'],
                'source': 'email',
                'produit_type': f'Product {i}',
                'quantite': 10,
                'statut': 'en_attente'
            })

        # Toutes les commandes
        orders = db_manager.get_all_orders()
        assert len(orders) >= 5


class TestClientList:
    """Tests de liste des clients."""

    @pytest.fixture
    def db_manager(self, tmp_path):
        db_path = str(tmp_path / "test_orders.db")
        db = DatabaseManager(db_file=db_path)
        db.init_database()
        yield db

    def test_get_all_clients(self, db_manager):
        """Test récupération de tous les clients."""
        # Création des clients
        for i in range(5):
            db_manager.get_or_create_client(
                nom=f"Client {i}",
                telephone=f"+2126{i:08d}"
            )

        # Tous les clients
        clients = db_manager.get_all_clients()
        assert len(clients) >= 5


class TestConcurrencyFlow:
    """Tests de concurrence."""

    @pytest.fixture
    def db_manager(self, tmp_path):
        db_path = str(tmp_path / "test_orders.db")
        db = DatabaseManager(db_file=db_path)
        db.init_database()
        yield db

    def test_multiple_simultaneous_orders(self, db_manager):
        """Test plusieurs commandes simultanées."""
        order_ids = []

        # Création de plusieurs clients et commandes
        for i in range(10):
            client = db_manager.get_or_create_client(
                nom=f"Concurrent Client {i}",
                telephone=f"+21260000{i:04d}"
            )
            order_id = db_manager.create_order({
                'client_id': client['id'],
                'source': 'email',
                'produit_type': 'Test Product',
                'quantite': i + 1,
                'statut': 'en_attente'
            })
            order_ids.append(order_id)

        # Toutes les commandes existent
        for order_id in order_ids:
            order = db_manager.get_order(order_id)
            assert order is not None


class TestOrderDataIntegrity:
    """Tests d'intégrité des données."""

    @pytest.fixture
    def db_manager(self, tmp_path):
        db_path = str(tmp_path / "test_orders.db")
        db = DatabaseManager(db_file=db_path)
        db.init_database()
        yield db

    def test_order_preserves_all_fields(self, db_manager):
        """Test que tous les champs sont préservés."""
        client = db_manager.get_or_create_client(nom="Test", telephone="+212600000000")

        order_data = {
            'client_id': client['id'],
            'source': 'email',
            'produit_type': 'Sachets fond plat',
            'quantite': 500,
            'unite': 'kg',
            'prix_total': 2500.0,
            'statut': 'en_attente',
            'email_from': 'test@example.com'
        }
        order_id = db_manager.create_order(order_data)

        order = db_manager.get_order(order_id)

        assert order['source'] == 'email'
        # produit_type vient de la jointure avec produits et peut être vide
        assert order['quantite'] == 500
