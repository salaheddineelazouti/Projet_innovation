"""
Tests pour le module backup_database.py
Tests du système de sauvegarde et restauration
"""

import pytest
import os
import sys
import tempfile
import shutil
import sqlite3
import gzip

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import after path setup
import backup_database as backup_module


class TestBackupCreation:
    """Tests de création de sauvegardes."""
    
    @pytest.fixture
    def setup_test_db(self, tmp_path):
        """Setup test database and backup directory."""
        # Create test database
        db_path = tmp_path / "test_orders.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        cursor.execute("INSERT INTO test (name) VALUES ('Test Data')")
        conn.commit()
        conn.close()
        
        # Create backup directory
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        
        # Override module constants
        original_db = backup_module.DB_PATH
        original_backup = backup_module.BACKUP_DIR
        backup_module.DB_PATH = str(db_path)
        backup_module.BACKUP_DIR = str(backup_dir)
        
        yield {
            'db_path': str(db_path),
            'backup_dir': str(backup_dir),
            'tmp_path': tmp_path
        }
        
        # Restore original values
        backup_module.DB_PATH = original_db
        backup_module.BACKUP_DIR = original_backup
    
    def test_create_compressed_backup(self, setup_test_db):
        """Test création d'une sauvegarde compressée."""
        backup_path = backup_module.create_backup(compress=True)
        
        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert backup_path.endswith('.db.gz')
    
    def test_create_uncompressed_backup(self, setup_test_db):
        """Test création d'une sauvegarde non compressée."""
        backup_path = backup_module.create_backup(compress=False)
        
        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert backup_path.endswith('.db')
        assert not backup_path.endswith('.gz')
    
    def test_backup_file_is_valid_gzip(self, setup_test_db):
        """Test que le fichier compressé est un gzip valide."""
        backup_path = backup_module.create_backup(compress=True)
        
        # Try to decompress
        with gzip.open(backup_path, 'rb') as f:
            data = f.read()
        
        assert len(data) > 0
    
    def test_backup_contains_data(self, setup_test_db):
        """Test que la sauvegarde contient les données."""
        backup_path = backup_module.create_backup(compress=False)
        
        # Open backup and verify data
        conn = sqlite3.connect(backup_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM test WHERE id = 1")
        result = cursor.fetchone()
        conn.close()
        
        assert result is not None
        assert result[0] == 'Test Data'
    
    def test_backup_filename_format(self, setup_test_db):
        """Test format du nom de fichier."""
        backup_path = backup_module.create_backup(compress=True)
        filename = os.path.basename(backup_path)
        
        assert filename.startswith('backup_')
        assert '_' in filename  # Contains timestamp
    
    def test_metadata_saved(self, setup_test_db):
        """Test que les métadonnées sont sauvegardées."""
        backup_module.create_backup(compress=True)
        
        metadata_path = os.path.join(setup_test_db['backup_dir'], 'backup_history.json')
        assert os.path.exists(metadata_path)


class TestBackupRestore:
    """Tests de restauration de sauvegardes."""
    
    @pytest.fixture
    def setup_with_backup(self, tmp_path):
        """Setup with existing backup."""
        # Create test database
        db_path = tmp_path / "test_orders.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
        cursor.execute("INSERT INTO test (value) VALUES ('Original')")
        conn.commit()
        conn.close()
        
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        
        # Override module constants
        original_db = backup_module.DB_PATH
        original_backup = backup_module.BACKUP_DIR
        backup_module.DB_PATH = str(db_path)
        backup_module.BACKUP_DIR = str(backup_dir)
        
        # Create backup
        backup_path = backup_module.create_backup(compress=True)
        
        # Modify original database
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("UPDATE test SET value = 'Modified'")
        conn.commit()
        conn.close()
        
        yield {
            'db_path': str(db_path),
            'backup_dir': str(backup_dir),
            'backup_filename': os.path.basename(backup_path)
        }
        
        backup_module.DB_PATH = original_db
        backup_module.BACKUP_DIR = original_backup
    
    def test_restore_backup(self, setup_with_backup):
        """Test restauration d'une sauvegarde."""
        result = backup_module.restore_backup(setup_with_backup['backup_filename'])
        
        assert result is True
    
    def test_restore_recovers_data(self, setup_with_backup):
        """Test que la restauration récupère les données."""
        backup_module.restore_backup(setup_with_backup['backup_filename'])
        
        # Verify data is restored
        conn = sqlite3.connect(setup_with_backup['db_path'])
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM test WHERE id = 1")
        result = cursor.fetchone()
        conn.close()
        
        assert result[0] == 'Original'  # Not 'Modified'
    
    def test_restore_creates_pre_restore_backup(self, setup_with_backup):
        """Test que la restauration crée un backup pré-restauration."""
        backup_module.restore_backup(setup_with_backup['backup_filename'])
        
        backup_files = os.listdir(setup_with_backup['backup_dir'])
        pre_restore_files = [f for f in backup_files if f.startswith('pre_restore_')]
        
        assert len(pre_restore_files) >= 1
    
    def test_restore_nonexistent_file(self, setup_with_backup):
        """Test restauration d'un fichier inexistant."""
        result = backup_module.restore_backup('nonexistent_backup.db.gz')
        
        assert result is False


class TestBackupList:
    """Tests de listing des sauvegardes."""
    
    @pytest.fixture
    def setup_multiple_backups(self, tmp_path):
        """Setup with multiple backups."""
        db_path = tmp_path / "test_orders.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()
        
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        
        original_db = backup_module.DB_PATH
        original_backup = backup_module.BACKUP_DIR
        backup_module.DB_PATH = str(db_path)
        backup_module.BACKUP_DIR = str(backup_dir)
        
        # Create multiple backups
        import time
        backups = []
        for i in range(3):
            backup_path = backup_module.create_backup(compress=True)
            backups.append(backup_path)
            time.sleep(0.1)  # Small delay for different timestamps
        
        yield {
            'backup_dir': str(backup_dir),
            'backups': backups
        }
        
        backup_module.DB_PATH = original_db
        backup_module.BACKUP_DIR = original_backup
    
    def test_list_backups(self, setup_multiple_backups):
        """Test listing des sauvegardes."""
        backups = backup_module.list_backups()
        
        # Should have at least 1 backup (depends on setup)
        assert len(backups) >= 1
    
    def test_list_backups_sorted_by_date(self, setup_multiple_backups):
        """Test que les sauvegardes sont triées par date."""
        backups = backup_module.list_backups()
        
        # Should be sorted newest first
        dates = [b['filename'] for b in backups]
        assert dates == sorted(dates, reverse=True)
    
    def test_list_backup_contains_required_fields(self, setup_multiple_backups):
        """Test que chaque backup contient les champs requis."""
        backups = backup_module.list_backups()
        
        for backup in backups:
            assert 'filename' in backup
            assert 'size' in backup
            assert 'compressed' in backup


class TestBackupCleanup:
    """Tests du nettoyage des anciennes sauvegardes."""
    
    @pytest.fixture
    def setup_many_backups(self, tmp_path):
        """Setup with many backups."""
        db_path = tmp_path / "test_orders.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.close()
        
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        
        original_db = backup_module.DB_PATH
        original_backup = backup_module.BACKUP_DIR
        backup_module.DB_PATH = str(db_path)
        backup_module.BACKUP_DIR = str(backup_dir)
        
        # Create 15 backups
        import time
        for i in range(15):
            backup_module.create_backup(compress=True)
            time.sleep(0.05)
        
        yield {
            'backup_dir': str(backup_dir)
        }
        
        backup_module.DB_PATH = original_db
        backup_module.BACKUP_DIR = original_backup
    
    def test_delete_old_backups(self, setup_many_backups):
        """Test suppression des anciennes sauvegardes."""
        backup_module.delete_old_backups(keep_count=5)
        
        backups = backup_module.list_backups()
        
        assert len(backups) <= 5
    
    def test_keeps_newest_backups(self, setup_many_backups):
        """Test que les plus récentes sont gardées."""
        # Get newest before cleanup
        backups_before = backup_module.list_backups()
        newest_5_before = [b['filename'] for b in backups_before[:5]]
        
        backup_module.delete_old_backups(keep_count=5)
        
        backups_after = backup_module.list_backups()
        filenames_after = [b['filename'] for b in backups_after]
        
        # Newest 5 should still be there
        for filename in newest_5_before:
            assert filename in filenames_after


class TestDatabaseStats:
    """Tests des statistiques de base de données."""
    
    @pytest.fixture
    def setup_db_with_data(self, tmp_path):
        """Setup database with test data."""
        db_path = tmp_path / "test_orders.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        cursor.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL)")
        for i in range(10):
            cursor.execute("INSERT INTO users (name) VALUES (?)", (f"User {i}",))
        for i in range(25):
            cursor.execute("INSERT INTO orders (total) VALUES (?)", (i * 10.5,))
        conn.commit()
        conn.close()
        
        original_db = backup_module.DB_PATH
        backup_module.DB_PATH = str(db_path)
        
        yield {'db_path': str(db_path)}
        
        backup_module.DB_PATH = original_db
    
    def test_get_db_stats(self, setup_db_with_data):
        """Test récupération des statistiques."""
        stats = backup_module.get_db_stats()
        
        assert stats is not None
        assert 'size' in stats
        assert 'tables' in stats
    
    def test_stats_table_counts(self, setup_db_with_data):
        """Test comptage des enregistrements par table."""
        stats = backup_module.get_db_stats()
        
        assert stats['tables']['users'] == 10
        assert stats['tables']['orders'] == 25


class TestJsonExport:
    """Tests de l'export JSON."""
    
    @pytest.fixture
    def setup_for_export(self, tmp_path):
        """Setup for JSON export tests."""
        db_path = tmp_path / "test_orders.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        cursor.execute("INSERT INTO items (name) VALUES ('Item A')")
        cursor.execute("INSERT INTO items (name) VALUES ('Item B')")
        conn.commit()
        conn.close()
        
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        
        original_db = backup_module.DB_PATH
        original_backup = backup_module.BACKUP_DIR
        backup_module.DB_PATH = str(db_path)
        backup_module.BACKUP_DIR = str(backup_dir)
        
        yield {'backup_dir': str(backup_dir)}
        
        backup_module.DB_PATH = original_db
        backup_module.BACKUP_DIR = original_backup
    
    def test_export_to_json(self, setup_for_export):
        """Test export vers JSON."""
        export_path = backup_module.export_to_json('test_export.json')
        
        assert export_path is not None
        assert os.path.exists(export_path)
    
    def test_export_json_valid_format(self, setup_for_export):
        """Test que le JSON exporté est valide."""
        import json
        
        export_path = backup_module.export_to_json('test_export.json')
        
        with open(export_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert 'exported_at' in data
        assert 'tables' in data
    
    def test_export_json_contains_data(self, setup_for_export):
        """Test que l'export contient les données."""
        import json
        
        export_path = backup_module.export_to_json('test_export.json')
        
        with open(export_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert 'items' in data['tables']
        assert len(data['tables']['items']) == 2


class TestFormatSize:
    """Tests de formatage de taille."""
    
    def test_format_bytes(self):
        """Test formatage en bytes."""
        result = backup_module.format_size(500)
        assert 'B' in result
    
    def test_format_kilobytes(self):
        """Test formatage en KB."""
        result = backup_module.format_size(2048)
        assert 'KB' in result
    
    def test_format_megabytes(self):
        """Test formatage en MB."""
        result = backup_module.format_size(5 * 1024 * 1024)
        assert 'MB' in result
    
    def test_format_gigabytes(self):
        """Test formatage en GB."""
        result = backup_module.format_size(2 * 1024 * 1024 * 1024)
        assert 'GB' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
