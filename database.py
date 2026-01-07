"""
Database Manager Module
SQLite database for storing purchase orders, clients, products, and logs.
"""

import sqlite3
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

DATABASE_FILE = "orders.db"

# Product types catalog
PRODUCT_CATALOG = [
    {"id": 1, "type": "Sachets fond plat", "description": "Sachets à fond plat pour emballage"},
    {"id": 2, "type": "Sac fond carré sans poignées", "description": "Sacs fond carré sans poignées"},
    {"id": 3, "type": "Sac fond carré avec poignées plates", "description": "Sacs fond carré avec poignées plates"},
    {"id": 4, "type": "Sac fond carré avec poignées torsadées", "description": "Sacs fond carré avec poignées torsadées"}
]


class DatabaseManager:
    def __init__(self, db_file=DATABASE_FILE):
        self.db_file = db_file
        self.connection = None
        self._initialized = False
    
    def connect(self):
        """Connect to the SQLite database."""
        try:
            if self.connection:
                return True  # Already connected
            self.connection = sqlite3.connect(
                self.db_file, 
                check_same_thread=False,
                timeout=30.0  # Wait up to 30 seconds for locks
            )
            self.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA busy_timeout=30000")
            print(f"✅ Connexion à la base de données: {self.db_file}")
            return True
        except Exception as e:
            print(f"❌ Erreur de connexion DB: {e}")
            return False
    
    def disconnect(self):
        """Close the database connection."""
        if self.connection:
            try:
                self.connection.close()
                self.connection = None
                print("📤 Déconnexion de la base de données")
            except:
                pass
    
    def init_database(self):
        """Initialize database with required tables."""
        if not self.connection:
            self.connect()
        
        cursor = self.connection.cursor()
        
        # Create Clients table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                email TEXT,
                telephone TEXT,
                adresse TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create Products table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS produits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL UNIQUE,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create Orders table (compatible SAGE X3)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS commandes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_commande TEXT UNIQUE,
                ligne_commande INTEGER DEFAULT 1,
                site_vente TEXT DEFAULT 'SXP',
                client_id INTEGER,
                code_client TEXT,
                produit_id INTEGER,
                code_article TEXT,
                nature_produit TEXT,
                quantite REAL,
                unite TEXT DEFAULT 'US',
                quantite_livree REAL DEFAULT 0,
                reste_a_livrer REAL,
                quantite_facturee REAL DEFAULT 0,
                prix_unitaire REAL,
                prix_total REAL,
                devise TEXT DEFAULT 'MAD',
                date_commande DATE,
                date_livraison DATE,
                commercial TEXT DEFAULT 'DIVERS',
                type_sac TEXT,
                format_sac TEXT,
                type_papier TEXT,
                grammage INTEGER,
                laize INTEGER,
                impression_client TEXT,
                informations_supplementaires TEXT,
                confiance INTEGER,
                statut TEXT DEFAULT 'en_attente',
                source TEXT DEFAULT 'email',
                email_id TEXT,
                email_subject TEXT,
                email_from TEXT,
                whatsapp_from TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                validated_at TIMESTAMP,
                validated_by TEXT,
                motif_rejet TEXT,
                FOREIGN KEY (client_id) REFERENCES clients(id),
                FOREIGN KEY (produit_id) REFERENCES produits(id)
            )
        """)
        
        # Add source column if it doesn't exist (for existing databases)
        try:
            cursor.execute("ALTER TABLE commandes ADD COLUMN source TEXT DEFAULT 'email'")
            self.connection.commit()
        except:
            pass
        
        try:
            cursor.execute("ALTER TABLE commandes ADD COLUMN whatsapp_from TEXT")
            self.connection.commit()
        except:
            pass
        
        try:
            cursor.execute("ALTER TABLE commandes ADD COLUMN motif_rejet TEXT")
            self.connection.commit()
        except:
            pass
        
        # Add SAGE X3 compatible columns
        sage_columns = [
            ("ligne_commande", "INTEGER DEFAULT 1"),
            ("site_vente", "TEXT DEFAULT 'SXP'"),
            ("code_client", "TEXT"),
            ("code_article", "TEXT"),
            ("quantite_livree", "REAL DEFAULT 0"),
            ("reste_a_livrer", "REAL"),
            ("quantite_facturee", "REAL DEFAULT 0"),
            ("commercial", "TEXT DEFAULT 'DIVERS'"),
            ("type_sac", "TEXT"),
            ("format_sac", "TEXT"),
            ("type_papier", "TEXT"),
            ("grammage", "INTEGER"),
            ("laize", "INTEGER"),
            ("impression_client", "TEXT"),
            ("updated_at", "TIMESTAMP")
        ]
        
        for col_name, col_def in sage_columns:
            try:
                cursor.execute(f"ALTER TABLE commandes ADD COLUMN {col_name} {col_def}")
                self.connection.commit()
            except:
                pass
        
        # Create Logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                table_name TEXT,
                record_id INTEGER,
                details TEXT,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert default products if not exist
        for product in PRODUCT_CATALOG:
            cursor.execute("""
                INSERT OR IGNORE INTO produits (id, type, description)
                VALUES (?, ?, ?)
            """, (product['id'], product['type'], product['description']))
        
        self.connection.commit()
        print("✅ Base de données initialisée avec succès")
        
        self._log_action("INIT", "database", None, "Database initialized")
    
    def _log_action(self, action, table_name, record_id, details):
        """Log an action to the logs table."""
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO logs (action, table_name, record_id, details)
            VALUES (?, ?, ?, ?)
        """, (action, table_name, record_id, details))
        self.connection.commit()
    
    # Client operations
    def get_or_create_client(self, nom, email=None, telephone=None):
        """Get existing client or create new one.
        
        Logic: 
        - If we have a real client name (not generic), search by name first
        - Phone number is just contact info, NOT a unique client identifier
        - Multiple clients can share the same phone (e.g., same person ordering for different companies)
        """
        cursor = self.connection.cursor()
        
        # Default name if None or empty
        if not nom or nom.strip() == '':
            nom = email or telephone or 'Client Inconnu'
        
        # Check if the provided name is a "real" name (not a generic WhatsApp placeholder)
        is_real_name = nom and not nom.startswith('Client WhatsApp') and not nom.startswith('Client Inconnu')
        
        if is_real_name:
            # For real names, search by name first (case insensitive)
            cursor.execute("SELECT * FROM clients WHERE LOWER(nom) = LOWER(?)", (nom,))
            client = cursor.fetchone()
            
            if client:
                # Update telephone if not set
                if telephone and not client['telephone']:
                    cursor.execute("UPDATE clients SET telephone = ? WHERE id = ?", (telephone, client['id']))
                    self.connection.commit()
                return dict(client)
            
            # Not found by name - create new client with this real name
            cursor.execute("""
                INSERT INTO clients (nom, email, telephone)
                VALUES (?, ?, ?)
            """, (nom, email, telephone))
            self.connection.commit()
            
            client_id = cursor.lastrowid
            self._log_action("CREATE", "clients", client_id, f"Created client: {nom}")
            print(f"   👤 Nouveau client créé: {nom}")
            
            return {"id": client_id, "nom": nom, "email": email, "telephone": telephone}
        
        # For generic names (Client WhatsApp...), try to find by telephone
        if telephone:
            cursor.execute("SELECT * FROM clients WHERE telephone = ?", (telephone,))
            client = cursor.fetchone()
            if client:
                return dict(client)
        
        # Try to find by email if provided
        if email:
            cursor.execute("SELECT * FROM clients WHERE email = ?", (email,))
            client = cursor.fetchone()
            if client:
                return dict(client)
        
        # Create new client
        cursor.execute("""
            INSERT INTO clients (nom, email, telephone)
            VALUES (?, ?, ?)
        """, (nom, email, telephone))
        self.connection.commit()
        
        client_id = cursor.lastrowid
        self._log_action("CREATE", "clients", client_id, f"Created client: {nom}")
        
        return {"id": client_id, "nom": nom, "email": email, "telephone": telephone}
    
    def get_all_clients(self):
        """Get all clients with order statistics."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT c.*, 
                   COUNT(cmd.id) as total_orders,
                   MAX(cmd.created_at) as last_order_date
            FROM clients c
            LEFT JOIN commandes cmd ON c.id = cmd.client_id
            GROUP BY c.id
            ORDER BY c.nom
        """)
        return [dict(row) for row in cursor.fetchall()]
    
    # Product operations
    def get_product_by_type(self, type_name):
        """Get product by type name."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM produits WHERE type LIKE ?", (f"%{type_name}%",))
        product = cursor.fetchone()
        return dict(product) if product else None
    
    def get_all_products(self):
        """Get all products."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM produits")
        return [dict(row) for row in cursor.fetchall()]
    
    # Order operations
    def is_email_processed(self, email_id):
        """Check if an email has already been processed."""
        if not email_id or not self.connection:
            return False
        cursor = self.connection.cursor()
        # Force WAL checkpoint to see most recent data from other connections
        try:
            cursor.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except:
            pass
        cursor.execute("SELECT id FROM commandes WHERE email_id = ?", (email_id,))
        result = cursor.fetchone()
        return result is not None
    
    def create_order(self, order_data):
        """Create a new order from extracted data."""
        cursor = self.connection.cursor()
        
        # Check if order already exists (by numero_commande or email_id)
        numero = order_data.get('numero_commande')
        email_id = order_data.get('email_id')
        
        if numero:
            cursor.execute("SELECT id FROM commandes WHERE numero_commande = ?", (numero,))
            existing = cursor.fetchone()
            if existing:
                print(f"   ⚠️ Commande {numero} déjà existante (ID: {existing[0]})")
                return existing[0]
        
        if email_id:
            cursor.execute("SELECT id FROM commandes WHERE email_id = ?", (email_id,))
            existing = cursor.fetchone()
            if existing:
                print(f"   ⚠️ Email déjà traité (ID: {existing[0]})")
                return existing[0]
        
        # Get or create client - use whatsapp number or email as fallback
        client_name = order_data.get('entreprise_cliente')
        client_email = order_data.get('email_from') or order_data.get('whatsapp_from')
        
        # Extract phone number for WhatsApp orders
        client_phone = None
        if order_data.get('whatsapp_from'):
            client_phone = order_data.get('whatsapp_from', '').replace('whatsapp:', '')
        
        # If no client name, use whatsapp number formatted nicely
        if not client_name or client_name.strip() == '':
            if client_phone:
                client_name = f'Client WhatsApp {client_phone}'
            else:
                client_name = client_email or 'Client Inconnu'
        
        client = self.get_or_create_client(client_name, client_email, client_phone)
        
        # Get product
        product = None
        if order_data.get('type_produit'):
            product = self.get_product_by_type(order_data['type_produit'])
        
        # Calculate reste_a_livrer
        quantite = order_data.get('quantite') or 0
        quantite_livree = order_data.get('quantite_livree') or 0
        reste_a_livrer = quantite - quantite_livree
        
        # Generate code_client if not provided (format: CL + id padded to 5 digits)
        code_client = order_data.get('code_client') or f"CL{str(client['id']).zfill(5)}"
        
        # Insert order with SAGE X3 compatible fields
        cursor.execute("""
            INSERT INTO commandes (
                numero_commande, ligne_commande, site_vente, client_id, code_client,
                produit_id, code_article, nature_produit,
                quantite, unite, quantite_livree, reste_a_livrer, quantite_facturee,
                prix_unitaire, prix_total, devise,
                date_commande, date_livraison, commercial,
                type_sac, format_sac, type_papier, grammage, laize, impression_client,
                informations_supplementaires, confiance, source, 
                email_id, email_subject, email_from, whatsapp_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order_data.get('numero_commande'),
            order_data.get('ligne_commande', 1),
            order_data.get('site_vente', 'SXP'),
            client['id'],
            code_client,
            product['id'] if product else None,
            order_data.get('code_article'),
            order_data.get('nature_produit'),
            quantite,
            order_data.get('unite', 'US'),
            quantite_livree,
            reste_a_livrer,
            order_data.get('quantite_facturee', 0),
            order_data.get('prix_unitaire'),
            order_data.get('prix_total'),
            order_data.get('devise', 'MAD'),
            order_data.get('date_commande'),
            order_data.get('date_livraison'),
            order_data.get('commercial', 'DIVERS'),
            order_data.get('type_sac'),
            order_data.get('format_sac'),
            order_data.get('type_papier'),
            order_data.get('grammage'),
            order_data.get('laize'),
            order_data.get('impression_client'),
            order_data.get('informations_supplementaires'),
            order_data.get('confiance'),
            order_data.get('source', 'email'),
            order_data.get('email_id'),
            order_data.get('email_subject'),
            order_data.get('email_from'),
            order_data.get('whatsapp_from')
        ))
        
        self.connection.commit()
        order_id = cursor.lastrowid
        
        # Force WAL sync so other connections see this immediately (use new cursor)
        try:
            self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except:
            pass
        
        self._log_action("CREATE", "commandes", order_id, 
                        f"Created order: {order_data.get('numero_commande')}")
        
        print(f"   💾 Commande enregistrée (ID: {order_id})")
        return order_id
    
    def get_order(self, order_id):
        """Get order by ID with client and product info."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT c.*, cl.nom as client_nom, cl.email as client_email, cl.telephone as client_telephone, p.type as produit_type
            FROM commandes c
            LEFT JOIN clients cl ON c.client_id = cl.id
            LEFT JOIN produits p ON c.produit_id = p.id
            WHERE c.id = ?
        """, (order_id,))
        order = cursor.fetchone()
        return dict(order) if order else None
    
    def get_all_orders(self, status=None):
        """Get all orders, optionally filtered by status."""
        cursor = self.connection.cursor()
        
        if status:
            cursor.execute("""
                SELECT c.*, cl.nom as client_nom, p.type as produit_type
                FROM commandes c
                LEFT JOIN clients cl ON c.client_id = cl.id
                LEFT JOIN produits p ON c.produit_id = p.id
                WHERE c.statut = ?
                ORDER BY c.created_at DESC
            """, (status,))
        else:
            cursor.execute("""
                SELECT c.*, cl.nom as client_nom, p.type as produit_type
                FROM commandes c
                LEFT JOIN clients cl ON c.client_id = cl.id
                LEFT JOIN produits p ON c.produit_id = p.id
                ORDER BY c.created_at DESC
            """)
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_pending_orders(self):
        """Get orders pending validation."""
        return self.get_all_orders(status='en_attente')
    
    def update_order_status(self, order_id, status, validated_by=None):
        """Update order status."""
        cursor = self.connection.cursor()
        
        if status == 'validee':
            cursor.execute("""
                UPDATE commandes
                SET statut = ?, validated_at = ?, validated_by = ?
                WHERE id = ?
            """, (status, datetime.now().isoformat(), validated_by, order_id))
        else:
            cursor.execute("""
                UPDATE commandes SET statut = ? WHERE id = ?
            """, (status, order_id))
        
        self.connection.commit()
        self._log_action("UPDATE", "commandes", order_id, f"Status changed to: {status}")
        print(f"   ✅ Statut mis à jour: {status}")
    
    def update_order(self, order_id, updates):
        """Update order fields."""
        cursor = self.connection.cursor()
        
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [order_id]
        
        cursor.execute(f"""
            UPDATE commandes SET {set_clause}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, values)
        
        self.connection.commit()
        self._log_action("UPDATE", "commandes", order_id, f"Updated fields: {list(updates.keys())}")
    
    def delete_order(self, order_id):
        """Delete an order (soft delete by changing status)."""
        cursor = self.connection.cursor()
        cursor.execute("UPDATE commandes SET statut = 'supprimee' WHERE id = ?", (order_id,))
        self.connection.commit()
        self._log_action("DELETE", "commandes", order_id, "Order marked as deleted")
    
    # Statistics
    def get_stats(self):
        """Get database statistics."""
        cursor = self.connection.cursor()
        
        stats = {}
        
        cursor.execute("SELECT COUNT(*) FROM commandes")
        stats['total_orders'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE statut = 'en_attente'")
        stats['pending_orders'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE statut = 'validee'")
        stats['validated_orders'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM clients")
        stats['total_clients'] = cursor.fetchone()[0]
        
        # WhatsApp stats
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp'")
        stats['whatsapp_total'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'en_attente'")
        stats['whatsapp_pending'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'validee'")
        stats['whatsapp_validated'] = cursor.fetchone()[0]
        
        # Email stats
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'email' OR source IS NULL")
        stats['email_total'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE (source = 'email' OR source IS NULL) AND statut = 'en_attente'")
        stats['email_pending'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM commandes WHERE (source = 'email' OR source IS NULL) AND statut = 'validee'")
        stats['email_validated'] = cursor.fetchone()[0]
        
        return stats
    
    def get_top_clients(self, limit=5):
        """Get top clients by order count and quantity."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT cl.id, cl.nom, COUNT(c.id) as total_orders, SUM(c.quantite) as total_quantity
            FROM clients cl
            LEFT JOIN commandes c ON cl.id = c.client_id
            WHERE cl.nom IS NOT NULL AND cl.nom != ''
            GROUP BY cl.id
            HAVING total_orders > 0
            ORDER BY total_orders DESC, total_quantity DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_top_products(self, limit=5):
        """Get top products by order count."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT p.id, p.type, COUNT(c.id) as order_count
            FROM produits p
            LEFT JOIN commandes c ON p.id = c.produit_id
            WHERE p.type IS NOT NULL AND p.type != ''
            GROUP BY p.id
            HAVING order_count > 0
            ORDER BY order_count DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_orders_trend(self, days=7):
        """Get order count per day for the last N days."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM commandes
            WHERE created_at >= DATE('now', ?)
            GROUP BY DATE(created_at)
            ORDER BY date ASC
        """, (f'-{days} days',))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_logs(self, limit=50):
        """Get recent logs."""
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM logs ORDER BY created_at DESC LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def test_database():
    """Test database operations."""
    print("=" * 50)
    print("🧪 Test de la base de données")
    print("=" * 50)
    
    db = DatabaseManager()
    db.connect()
    db.init_database()
    
    # Show products
    print("\n📦 Produits disponibles:")
    products = db.get_all_products()
    for p in products:
        print(f"   {p['id']}. {p['type']}")
    
    # Test order creation
    print("\n🛒 Test création de commande...")
    test_order = {
        "numero_commande": "TEST-001",
        "entreprise_cliente": "Entreprise Test SARL",
        "type_produit": "Sachets fond plat",
        "nature_produit": "Sachets kraft 15x25cm",
        "quantite": 5000,
        "unite": "pièces",
        "prix_unitaire": 0.20,
        "prix_total": 1000,
        "devise": "MAD",
        "date_commande": "2024-12-23",
        "date_livraison": "2025-01-15",
        "confiance": 90,
        "email_id": "test-email-001",
        "email_subject": "Test Order",
        "email_from": "test@example.com"
    }
    
    order_id = db.create_order(test_order)
    
    # Get order
    order = db.get_order(order_id)
    print(f"\n📋 Commande créée:")
    print(f"   ID: {order['id']}")
    print(f"   N°: {order['numero_commande']}")
    print(f"   Client: {order['client_nom']}")
    print(f"   Produit: {order['produit_type']}")
    print(f"   Quantité: {order['quantite']} {order['unite']}")
    print(f"   Statut: {order['statut']}")
    
    # Stats
    stats = db.get_stats()
    print(f"\n📊 Statistiques:")
    print(f"   Total commandes: {stats['total_orders']}")
    print(f"   En attente: {stats['pending_orders']}")
    print(f"   Validées: {stats['validated_orders']}")
    print(f"   Clients: {stats['total_clients']}")
    
    db.disconnect()
    
    print("\n" + "=" * 50)
    print("✅ Test base de données terminé")
    print("=" * 50)


if __name__ == "__main__":
    test_database()
