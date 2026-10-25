"""
Tests du module tecpap.db.database : opérations sur la base SQLite (base temporaire).
"""

import os

import pytest

from tecpap.catalog import PRODUCTS
from tecpap.db.database import (
    ORDER_STATUSES,
    ORDER_UPDATABLE_FIELDS,
    PRODUCT_CATALOG,
    DatabaseManager,
    OrderUpdateError,
    clean_order_updates,
    is_generic_client_name,
)


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test_orders.db"
    db = DatabaseManager(str(db_path))
    db.connect()
    db.init_database()
    yield db
    db.disconnect()


@pytest.fixture
def sample_order_data():
    """Sample order data for testing."""
    return {
        'numero_commande': 'CMD-TEST-001',
        'entreprise_cliente': 'Test Company',
        'email_from': 'test@example.com',
        'type_produit': 'Sachets fond plat',
        'nature_produit': 'Test Product',
        'quantite': 100,
        'unite': 'kg',
        'prix_total': 500.0,
        'devise': 'MAD',
        'source': 'email',
        'email_id': 'test_email_001',
        'confiance': 85
    }


@pytest.fixture
def sample_whatsapp_order():
    """Sample WhatsApp order data."""
    return {
        'entreprise_cliente': 'WhatsApp Client',
        'whatsapp_from': '+212612345678',
        'type_produit': 'Sachets fond plat',
        'nature_produit': 'Test Product',
        'quantite': 50,
        'prix_total': 250.0,
        'source': 'whatsapp'
    }


class TestDatabaseConnection:
    """Tests de connexion à la base de données."""

    def test_connect_creates_database(self, temp_db):
        """Test que la connexion crée le fichier de base de données."""
        assert temp_db.connection is not None
        assert os.path.exists(temp_db.db_file)

    def test_disconnect_closes_connection(self, temp_db):
        """Test que la déconnexion ferme la connexion."""
        temp_db.disconnect()
        assert temp_db.connection is None

    def test_reconnect_after_disconnect(self, temp_db):
        """Test de reconnexion après déconnexion."""
        temp_db.disconnect()
        result = temp_db.connect()
        assert result is True
        assert temp_db.connection is not None

    def test_wal_mode_enabled(self, temp_db):
        """Test que le mode WAL est activé."""
        cursor = temp_db.connection.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == 'wal'


class TestDatabaseInit:
    """Tests d'initialisation de la base de données."""

    def test_tables_created(self, temp_db):
        """Test que toutes les tables sont créées."""
        cursor = temp_db.connection.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        assert 'clients' in tables
        assert 'produits' in tables
        assert 'commandes' in tables
        assert 'logs' in tables

    def test_products_catalog_inserted(self, temp_db):
        """Test que le catalogue produits est inséré."""
        products = temp_db.get_all_products()
        assert len(products) == len(PRODUCT_CATALOG)

    def test_product_types_match_catalog(self, temp_db):
        """Test que les types de produits correspondent au catalogue."""
        products = temp_db.get_all_products()
        db_types = [p['type'] for p in products]
        catalog_types = [p['type'] for p in PRODUCT_CATALOG]

        for ct in catalog_types:
            assert ct in db_types


class TestClientOperations:
    """Tests des opérations sur les clients."""

    def test_get_or_create_client_new(self, temp_db):
        """Test de création d'un nouveau client."""
        client = temp_db.get_or_create_client(
            nom='Test Client',
            email='test@example.com',
            telephone='+212612345678'
        )

        assert client is not None
        assert client['nom'] == 'Test Client'
        assert 'id' in client

    def test_get_or_create_client_existing(self, temp_db):
        """Test de récupération d'un client existant."""
        client1 = temp_db.get_or_create_client(
            nom='Existing Client',
            email='existing@example.com'
        )

        client2 = temp_db.get_or_create_client(
            nom='Existing Client',
            email='existing@example.com'
        )

        assert client1['id'] == client2['id']

    def test_get_all_clients(self, temp_db):
        """Test de récupération de tous les clients."""
        temp_db.get_or_create_client('Client A', 'a@test.com')
        temp_db.get_or_create_client('Client B', 'b@test.com')

        clients = temp_db.get_all_clients()

        assert len(clients) >= 2

    def test_client_with_telephone(self, temp_db):
        """Test client avec téléphone."""
        client = temp_db.get_or_create_client(
            nom='Client Tel',
            telephone='+212698765432'
        )

        assert client['telephone'] == '+212698765432'

    def test_empty_client_name_uses_fallback(self, temp_db):
        """Test que nom vide utilise email comme fallback."""
        client = temp_db.get_or_create_client(
            nom='',
            email='fallback@test.com'
        )

        assert client is not None
        # L'email sert de nom à défaut
        assert client['nom'] == 'fallback@test.com'


class TestOrderOperations:
    """Tests des opérations sur les commandes."""

    def test_create_order(self, temp_db, sample_order_data):
        """Test de création d'une commande."""
        order_id = temp_db.create_order(sample_order_data)

        assert order_id is not None
        assert order_id > 0

    def test_create_order_creates_client(self, temp_db, sample_order_data):
        """Test que create_order crée aussi le client."""
        temp_db.create_order(sample_order_data)

        clients = temp_db.get_all_clients()
        client_names = [c['nom'] for c in clients]

        assert 'Test Company' in client_names

    def test_get_order_by_id(self, temp_db, sample_order_data):
        """Test de récupération d'une commande par ID."""
        order_id = temp_db.create_order(sample_order_data)

        order = temp_db.get_order(order_id)

        assert order is not None
        assert order['numero_commande'] == 'CMD-TEST-001'

    def test_get_all_orders(self, temp_db, sample_order_data):
        """Test de récupération de toutes les commandes."""
        # Création de plusieurs commandes
        for i in range(3):
            order_data = sample_order_data.copy()
            order_data['numero_commande'] = f'CMD-TEST-{i}'
            order_data['email_id'] = f'email_{i}'
            temp_db.create_order(order_data)

        orders = temp_db.get_all_orders()

        assert len(orders) >= 3

    def test_update_order_status(self, temp_db, sample_order_data):
        """Test de mise à jour du statut."""
        order_id = temp_db.create_order(sample_order_data)

        temp_db.update_order_status(order_id, 'validee')

        order = temp_db.get_order(order_id)
        assert order['statut'] == 'validee'

    def test_reject_order(self, temp_db, sample_order_data):
        """Test de rejet d'une commande."""
        order_id = temp_db.create_order(sample_order_data)

        # Rejet par update_order_status
        temp_db.update_order_status(order_id, 'rejetee')

        # Motif de rejet écrit directement en SQL
        cursor = temp_db.connection.cursor()
        cursor.execute("UPDATE commandes SET motif_rejet = ? WHERE id = ?",
                      ("Informations incomplètes", order_id))
        temp_db.connection.commit()

        order = temp_db.get_order(order_id)
        assert order['statut'] == 'rejetee'
        assert order['motif_rejet'] == "Informations incomplètes"

    def test_duplicate_email_id_handled(self, temp_db, sample_order_data):
        """Test que les email_id dupliqués sont gérés."""
        order_id1 = temp_db.create_order(sample_order_data)
        order_id2 = temp_db.create_order(sample_order_data)  # même email_id

        # Même identifiant : la commande existante est renvoyée
        assert order_id1 == order_id2

    def test_get_orders_by_status(self, temp_db, sample_order_data):
        """Test de filtrage par statut."""
        order_id = temp_db.create_order(sample_order_data)
        temp_db.update_order_status(order_id, 'validee')

        validated_orders = temp_db.get_all_orders(status='validee')

        assert len(validated_orders) >= 1

    def test_is_email_processed(self, temp_db, sample_order_data):
        """Test de vérification si email déjà traité."""
        # Avant la création de la commande
        assert temp_db.is_email_processed('test_email_001') is False

        # Après la création de la commande
        temp_db.create_order(sample_order_data)
        assert temp_db.is_email_processed('test_email_001') is True


class TestWhatsAppOrders:
    """Tests des commandes WhatsApp."""

    def test_create_whatsapp_order(self, temp_db, sample_whatsapp_order):
        """Test de création d'une commande WhatsApp."""
        order_id = temp_db.create_order(sample_whatsapp_order)

        order = temp_db.get_order(order_id)

        assert order['source'] == 'whatsapp'
        assert order['whatsapp_from'] == '+212612345678'

    def test_whatsapp_order_creates_client(self, temp_db, sample_whatsapp_order):
        """Test que la commande WhatsApp crée le client."""
        temp_db.create_order(sample_whatsapp_order)

        clients = temp_db.get_all_clients()

        assert len(clients) >= 1


class TestStatistics:
    """Tests des statistiques."""

    def test_get_stats(self, temp_db, sample_order_data):
        """Test de récupération des statistiques."""
        # Création de quelques commandes
        temp_db.create_order(sample_order_data)

        sample2 = sample_order_data.copy()
        sample2['numero_commande'] = 'CMD-002'
        sample2['email_id'] = 'email_002'
        temp_db.create_order(sample2)

        stats = temp_db.get_stats()

        assert 'total_orders' in stats
        assert stats['total_orders'] >= 2

    def test_get_orders_trend(self, temp_db, sample_order_data):
        """Test de récupération de la tendance."""
        temp_db.create_order(sample_order_data)

        trend = temp_db.get_orders_trend()

        assert isinstance(trend, list)


class TestProductOperations:
    """Tests des opérations sur les produits."""

    def test_get_all_products(self, temp_db):
        """Test récupération de tous les produits."""
        products = temp_db.get_all_products()

        assert len(products) == len(PRODUCT_CATALOG)

    def test_get_product_by_type(self, temp_db):
        """Test récupération produit par type."""
        product = temp_db.get_product_by_type('Sachets fond plat')

        assert product is not None
        assert 'Sachets' in product['type']

    def test_get_product_partial_match(self, temp_db):
        """Test recherche partielle de produit."""
        product = temp_db.get_product_by_type('fond plat')

        assert product is not None


class TestEdgeCases:
    """Tests des cas limites."""

    def test_special_characters_in_name(self, temp_db):
        """Test caractères spéciaux dans le nom."""
        client = temp_db.get_or_create_client(
            nom="Test & Co. <Special> 'Chars'",
            email='special@test.com'
        )

        assert client is not None
        assert '&' in client['nom']

    def test_arabic_characters(self, temp_db):
        """Test caractères arabes."""
        client = temp_db.get_or_create_client(
            nom='أحمد بن علي',
            email='arabic@test.com'
        )

        assert client is not None
        assert 'أحمد' in client['nom']

    def test_very_large_quantity(self, temp_db, sample_order_data):
        """Test grande quantité."""
        sample_order_data['quantite'] = 999999999

        order_id = temp_db.create_order(sample_order_data)
        order = temp_db.get_order(order_id)

        assert order['quantite'] == 999999999

    def test_null_optional_fields(self, temp_db):
        """Test champs optionnels null."""
        order_data = {
            'entreprise_cliente': 'Minimal Order',
            'source': 'email'
        }

        order_id = temp_db.create_order(order_data)

        assert order_id is not None

    def test_empty_database_stats(self, tmp_path):
        """Test stats sur DB vide."""
        db_path = tmp_path / "empty_test.db"
        db = DatabaseManager(str(db_path))
        db.connect()
        db.init_database()

        stats = db.get_stats()

        assert stats['total_orders'] == 0
        db.disconnect()


class TestClientHistory:
    """Tests de l'historique client."""

    def test_get_client_orders(self, temp_db, sample_order_data):
        """Test récupération commandes d'un client."""
        # Commande d'un même client
        order_id = temp_db.create_order(sample_order_data)
        order = temp_db.get_order(order_id)

        client_id = order['client_id']

        # Commandes du client
        cursor = temp_db.connection.cursor()
        cursor.execute("SELECT * FROM commandes WHERE client_id = ?", (client_id,))
        orders = cursor.fetchall()

        assert len(orders) >= 1


class TestSchemaMigration:
    """Ajout des colonnes manquantes sur une base créée par un schéma plus ancien."""

    def test_old_database_gets_new_columns(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "ancienne.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE commandes (id INTEGER PRIMARY KEY AUTOINCREMENT, numero_commande TEXT UNIQUE, "
                     "client_id INTEGER, produit_id INTEGER, nature_produit TEXT, quantite REAL, unite TEXT, "
                     "prix_unitaire REAL, prix_total REAL, devise TEXT, date_commande DATE, date_livraison DATE, "
                     "informations_supplementaires TEXT, confiance INTEGER, statut TEXT DEFAULT 'en_attente', "
                     "email_id TEXT, email_subject TEXT, email_from TEXT, created_at TIMESTAMP "
                     "DEFAULT CURRENT_TIMESTAMP, validated_at TIMESTAMP, validated_by TEXT)")
        conn.commit()
        conn.close()

        db = DatabaseManager(str(db_path))
        db.init_database()
        columns = {row[1] for row in db.connection.execute("PRAGMA table_info(commandes)")}
        db.disconnect()

        assert {"source", "whatsapp_from", "motif_rejet", "code_article", "grammage", "laize"} <= columns

    def test_init_database_twice(self, temp_db):
        temp_db.init_database()
        assert len(temp_db.get_all_products()) == len(PRODUCT_CATALOG)

    def test_parent_directory_is_created(self, tmp_path):
        db = DatabaseManager(str(tmp_path / "data" / "orders.db"))
        assert db.connect() is True
        db.disconnect()


class TestUpdateOrderWhitelist:
    """update_order n'écrit que les colonnes de ORDER_UPDATABLE_FIELDS, avec des valeurs vérifiées."""

    @pytest.fixture
    def order_id(self, temp_db, sample_order_data):
        return temp_db.create_order(sample_order_data)

    def test_every_editable_field_is_a_column(self, temp_db):
        columns = {row[1] for row in temp_db.connection.execute("PRAGMA table_info(commandes)")}
        assert set(ORDER_UPDATABLE_FIELDS) <= columns

    @pytest.mark.parametrize("field", ["id", "statut", "client_id", "confiance", "source", "email_id", "created_at",
                                       "validated_at", "validated_by", "updated_at", "colonne_inconnue"])
    def test_non_editable_field_raises(self, temp_db, order_id, field):
        before = temp_db.get_order(order_id)
        with pytest.raises(ValueError, match=field) as excinfo:
            temp_db.update_order(order_id, {field: "x"})
        assert isinstance(excinfo.value, OrderUpdateError)
        assert excinfo.value.fields == (field,)
        assert temp_db.get_order(order_id) == before

    @pytest.mark.parametrize("key", [
        "statut = 'validee', quantite", "quantite = 0 WHERE 1=1; --", "quantite; DROP TABLE commandes",
        "quantite=quantite", " quantite", "QUANTITE",
    ])
    def test_sql_looking_keys_rejected(self, temp_db, order_id, key):
        with pytest.raises(OrderUpdateError) as excinfo:
            temp_db.update_order(order_id, {key: 1})
        assert excinfo.value.fields == (key,)
        order = temp_db.get_order(order_id)
        assert (order["statut"], order["quantite"]) == ("en_attente", 100)
        assert len(temp_db.get_all_orders()) == 1

    def test_all_unknown_fields_are_listed(self, temp_db, order_id):
        with pytest.raises(OrderUpdateError) as excinfo:
            temp_db.update_order(order_id, {"zeta": 1, "statut": "validee", "quantite": 3})
        assert excinfo.value.fields == ("statut", "zeta")
        assert "statut, zeta" in str(excinfo.value)
        assert temp_db.get_order(order_id)["quantite"] == 100

    def test_valid_update(self, temp_db, order_id):
        temp_db.update_order(order_id, {"quantite": 250, "commercial": "NORD", "motif_rejet": None})
        order = temp_db.get_order(order_id)
        assert (order["quantite"], order["commercial"], order["motif_rejet"]) == (250, "NORD", None)
        assert order["updated_at"] is not None

    def test_empty_update_does_nothing(self, temp_db, order_id):
        before = temp_db.get_order(order_id)
        temp_db.update_order(order_id, {})
        assert temp_db.get_order(order_id) == before

    @pytest.mark.parametrize("value, expected", [
        (12, 12.0), (12.5, 12.5), ("12", 12.0), (" 1.5 ", 1.5), ("1e3", 1000.0), ("", None), (None, None),
    ])
    def test_real_columns(self, value, expected):
        assert clean_order_updates({"quantite": value}) == {"quantite": expected}

    @pytest.mark.parametrize("value, expected", [(80, 80), (80.0, 80), ("80", 80), ("80.0", 80), ("", None)])
    def test_integer_columns(self, value, expected):
        cleaned = clean_order_updates({"grammage": value})
        assert cleaned == {"grammage": expected}
        assert expected is None or isinstance(cleaned["grammage"], int)

    @pytest.mark.parametrize("field, value", [
        ("quantite", "beaucoup"), ("quantite", True), ("quantite", [1]), ("quantite", {"valeur": 1}),
        ("quantite", "nan"), ("quantite", float("inf")), ("prix_total", "1,5"), ("grammage", 80.5),
        ("grammage", "80.5"), ("laize", 2**70), ("produit_id", False),
    ])
    def test_invalid_numbers(self, field, value):
        with pytest.raises(OrderUpdateError, match=field) as excinfo:
            clean_order_updates({field: value})
        assert excinfo.value.fields == (field,)

    @pytest.mark.parametrize("value, expected", [("KB80L28", "KB80L28"), ("", ""), (None, None), (123, "123")])
    def test_text_columns(self, value, expected):
        assert clean_order_updates({"code_article": value}) == {"code_article": expected}

    @pytest.mark.parametrize("value", [True, ["a"], {"a": 1}, b"octets"])
    def test_invalid_text(self, value):
        with pytest.raises(OrderUpdateError, match="texte attendu"):
            clean_order_updates({"commercial": value})

    def test_invalid_values_are_listed_together(self):
        with pytest.raises(OrderUpdateError) as excinfo:
            clean_order_updates({"quantite": "x", "grammage": "y", "commercial": "ok"})
        assert excinfo.value.fields == ("grammage", "quantite")
        assert "grammage (nombre entier attendu)" in str(excinfo.value)
        assert "quantite (nombre attendu)" in str(excinfo.value)

    def test_converted_values_are_written(self, temp_db, order_id):
        temp_db.update_order(order_id, {"quantite": "300", "grammage": "90", "prix_unitaire": ""})
        order = temp_db.get_order(order_id)
        assert (order["quantite"], order["grammage"], order["prix_unitaire"]) == (300, 90, None)


class TestUpdateOrderStatus:
    """Liste fermée des statuts."""

    @pytest.mark.parametrize("status", ORDER_STATUSES)
    def test_known_statuses(self, temp_db, sample_order_data, status):
        order_id = temp_db.create_order(sample_order_data)
        temp_db.update_order_status(order_id, status)
        assert temp_db.get_order(order_id)["statut"] == status

    def test_expected_statuses(self):
        assert ORDER_STATUSES == ("en_attente", "validee", "rejetee", "supprimee")

    @pytest.mark.parametrize("status", ["validée", "VALIDEE", "", "livree", "validee'; DROP TABLE commandes; --", None])
    def test_unknown_status_raises(self, temp_db, sample_order_data, status):
        order_id = temp_db.create_order(sample_order_data)
        with pytest.raises(ValueError, match="Statut de commande inconnu"):
            temp_db.update_order_status(order_id, status)
        assert temp_db.get_order(order_id)["statut"] == "en_attente"

    def test_validation_records_author(self, temp_db, sample_order_data):
        order_id = temp_db.create_order(sample_order_data)
        temp_db.update_order_status(order_id, "validee", "Équipe")
        order = temp_db.get_order(order_id)
        assert order["validated_by"] == "Équipe"
        assert order["validated_at"] is not None


class TestCatalogSeeding:
    """Produits insérés depuis le catalogue."""

    def test_catalog_built_from_products(self):
        assert [p["id"] for p in PRODUCT_CATALOG] == [p["id"] for p in PRODUCTS]
        assert [p["type"] for p in PRODUCT_CATALOG] == [p["type"] for p in PRODUCTS]

    def test_product_ids_match_catalog(self, temp_db):
        products = {p["id"]: p["type"] for p in temp_db.get_all_products()}
        assert products == {p["id"]: p["type"] for p in PRODUCTS}


class TestGenericClientNames:
    """Noms de clients provisoires."""

    @pytest.mark.parametrize("name", ["Client WhatsApp +212612345678", "Client Inconnu"])
    def test_generic_names(self, name):
        assert is_generic_client_name(name)

    @pytest.mark.parametrize("name", ["Snack Atlas", "", None])
    def test_real_or_empty_names(self, name):
        assert not is_generic_client_name(name)

    def test_whatsapp_order_without_name_uses_phone(self, temp_db):
        order_id = temp_db.create_order({"whatsapp_from": "whatsapp:+212600000009", "source": "whatsapp"})
        order = temp_db.get_order(order_id)
        assert order["client_nom"] == "Client WhatsApp +212600000009"
        assert order["client_telephone"] == "+212600000009"

    def test_code_client_format(self, temp_db, sample_order_data):
        order = temp_db.get_order(temp_db.create_order(sample_order_data))
        assert order["code_client"] == f"CL{order['client_id']:05d}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
