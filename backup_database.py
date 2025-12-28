"""
Database Backup & Restore System
Système de sauvegarde et restauration de la base de données SQLite
"""

import os
import shutil
import sqlite3
from datetime import datetime
import json
import gzip

# Configuration
DB_PATH = 'orders.db'
BACKUP_DIR = 'backups'

def ensure_backup_dir():
    """Create backup directory if it doesn't exist."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        print(f"📁 Dossier de sauvegarde créé: {BACKUP_DIR}/")

def create_backup(compress=True):
    """
    Create a backup of the database.
    
    Args:
        compress: If True, compress the backup using gzip
    
    Returns:
        str: Path to the backup file
    """
    ensure_backup_dir()
    
    if not os.path.exists(DB_PATH):
        print("❌ Base de données non trouvée!")
        return None
    
    # Generate backup filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if compress:
        backup_filename = f"backup_{timestamp}.db.gz"
    else:
        backup_filename = f"backup_{timestamp}.db"
    
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    try:
        # Use SQLite's backup API for safe copy
        source = sqlite3.connect(DB_PATH)
        
        if compress:
            # Create compressed backup
            temp_backup = os.path.join(BACKUP_DIR, f"temp_{timestamp}.db")
            dest = sqlite3.connect(temp_backup)
            source.backup(dest)
            dest.close()
            
            # Compress the backup
            with open(temp_backup, 'rb') as f_in:
                with gzip.open(backup_path, 'wb') as f_out:
                    f_out.writelines(f_in)
            
            # Remove temp file
            os.remove(temp_backup)
        else:
            dest = sqlite3.connect(backup_path)
            source.backup(dest)
            dest.close()
        
        source.close()
        
        # Get file size
        size = os.path.getsize(backup_path)
        size_str = format_size(size)
        
        print(f"✅ Sauvegarde créée avec succès!")
        print(f"   📄 Fichier: {backup_path}")
        print(f"   📊 Taille: {size_str}")
        print(f"   🕐 Date: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        
        # Save backup metadata
        save_backup_metadata(backup_filename, size)
        
        return backup_path
        
    except Exception as e:
        print(f"❌ Erreur lors de la sauvegarde: {e}")
        return None

def restore_backup(backup_filename):
    """
    Restore database from a backup file.
    
    Args:
        backup_filename: Name of the backup file to restore
    
    Returns:
        bool: True if successful, False otherwise
    """
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    if not os.path.exists(backup_path):
        print(f"❌ Fichier de sauvegarde non trouvé: {backup_path}")
        return False
    
    try:
        # Create a backup of current database before restoring
        if os.path.exists(DB_PATH):
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            pre_restore_backup = f"pre_restore_{timestamp}.db"
            shutil.copy2(DB_PATH, os.path.join(BACKUP_DIR, pre_restore_backup))
            print(f"📦 Sauvegarde pré-restauration: {pre_restore_backup}")
        
        # Check if backup is compressed
        if backup_filename.endswith('.gz'):
            # Decompress and restore
            with gzip.open(backup_path, 'rb') as f_in:
                with open(DB_PATH, 'wb') as f_out:
                    f_out.writelines(f_in)
        else:
            # Direct copy
            shutil.copy2(backup_path, DB_PATH)
        
        print(f"✅ Base de données restaurée avec succès!")
        print(f"   📄 Depuis: {backup_filename}")
        print(f"   🕐 Date: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Erreur lors de la restauration: {e}")
        return False

def list_backups():
    """List all available backups with details."""
    ensure_backup_dir()
    
    backups = []
    
    for filename in os.listdir(BACKUP_DIR):
        if filename.startswith('backup_') and (filename.endswith('.db') or filename.endswith('.db.gz')):
            filepath = os.path.join(BACKUP_DIR, filename)
            stat = os.stat(filepath)
            
            # Parse date from filename
            try:
                date_str = filename.replace('backup_', '').replace('.db.gz', '').replace('.db', '')
                date = datetime.strptime(date_str, '%Y%m%d_%H%M%S')
                date_formatted = date.strftime('%d/%m/%Y %H:%M:%S')
            except:
                date_formatted = 'N/A'
            
            backups.append({
                'filename': filename,
                'path': filepath,
                'size': stat.st_size,
                'size_formatted': format_size(stat.st_size),
                'date': date_formatted,
                'compressed': filename.endswith('.gz')
            })
    
    # Sort by date (newest first)
    backups.sort(key=lambda x: x['filename'], reverse=True)
    
    return backups

def print_backups():
    """Print a formatted list of backups."""
    backups = list_backups()
    
    if not backups:
        print("📭 Aucune sauvegarde trouvée.")
        return
    
    print("\n📦 SAUVEGARDES DISPONIBLES")
    print("=" * 70)
    print(f"{'#':<3} {'Fichier':<35} {'Taille':<12} {'Date':<20}")
    print("-" * 70)
    
    for i, backup in enumerate(backups, 1):
        compressed = "🗜️" if backup['compressed'] else "  "
        print(f"{i:<3} {backup['filename']:<35} {backup['size_formatted']:<12} {backup['date']:<20} {compressed}")
    
    print("-" * 70)
    print(f"Total: {len(backups)} sauvegarde(s)")
    print()

def delete_old_backups(keep_count=10):
    """
    Delete old backups, keeping only the most recent ones.
    
    Args:
        keep_count: Number of recent backups to keep
    """
    backups = list_backups()
    
    if len(backups) <= keep_count:
        print(f"✅ Nombre de sauvegardes ({len(backups)}) <= limite ({keep_count}). Rien à supprimer.")
        return
    
    # Backups to delete (oldest ones)
    to_delete = backups[keep_count:]
    
    deleted_count = 0
    for backup in to_delete:
        try:
            os.remove(backup['path'])
            print(f"🗑️ Supprimé: {backup['filename']}")
            deleted_count += 1
        except Exception as e:
            print(f"❌ Erreur suppression {backup['filename']}: {e}")
    
    print(f"\n✅ {deleted_count} ancienne(s) sauvegarde(s) supprimée(s)")

def save_backup_metadata(filename, size):
    """Save backup metadata to JSON file."""
    metadata_path = os.path.join(BACKUP_DIR, 'backup_history.json')
    
    history = []
    if os.path.exists(metadata_path):
        with open(metadata_path, 'r', encoding='utf-8') as f:
            history = json.load(f)
    
    history.append({
        'filename': filename,
        'size': size,
        'date': datetime.now().isoformat(),
        'db_path': DB_PATH
    })
    
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def format_size(size_bytes):
    """Format file size in human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

def get_db_stats():
    """Get statistics about the current database."""
    if not os.path.exists(DB_PATH):
        return None
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        stats = {
            'size': format_size(os.path.getsize(DB_PATH)),
            'tables': {}
        }
        
        # Count rows in each table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        for (table_name,) in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            stats['tables'][table_name] = count
        
        conn.close()
        return stats
        
    except Exception as e:
        print(f"Erreur: {e}")
        return None

def export_to_json(output_file='database_export.json'):
    """Export entire database to JSON format."""
    if not os.path.exists(DB_PATH):
        print("❌ Base de données non trouvée!")
        return None
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        export_data = {
            'exported_at': datetime.now().isoformat(),
            'tables': {}
        }
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        for (table_name,) in tables:
            cursor.execute(f"SELECT * FROM {table_name}")
            rows = cursor.fetchall()
            export_data['tables'][table_name] = [dict(row) for row in rows]
        
        conn.close()
        
        # Save to file
        output_path = os.path.join(BACKUP_DIR, output_file)
        ensure_backup_dir()
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"✅ Export JSON créé: {output_path}")
        return output_path
        
    except Exception as e:
        print(f"❌ Erreur lors de l'export: {e}")
        return None


# CLI Interface
if __name__ == '__main__':
    import sys
    
    print("\n" + "=" * 50)
    print("   💾 SYSTÈME DE SAUVEGARDE - OrderFlow")
    print("=" * 50)
    
    if len(sys.argv) < 2:
        # Show menu
        print("""
Commandes disponibles:
  
  python backup_database.py backup     - Créer une sauvegarde
  python backup_database.py list       - Lister les sauvegardes
  python backup_database.py restore    - Restaurer une sauvegarde
  python backup_database.py clean      - Supprimer anciennes sauvegardes
  python backup_database.py stats      - Statistiques de la base
  python backup_database.py export     - Exporter en JSON
        """)
        
        # Show current stats
        stats = get_db_stats()
        if stats:
            print("\n📊 État actuel de la base de données:")
            print(f"   Taille: {stats['size']}")
            for table, count in stats['tables'].items():
                print(f"   - {table}: {count} enregistrements")
        
        sys.exit(0)
    
    command = sys.argv[1].lower()
    
    if command == 'backup':
        compress = '--no-compress' not in sys.argv
        create_backup(compress=compress)
        
    elif command == 'list':
        print_backups()
        
    elif command == 'restore':
        print_backups()
        backups = list_backups()
        
        if not backups:
            sys.exit(1)
        
        try:
            choice = input("\n📥 Numéro de la sauvegarde à restaurer (ou 'q' pour quitter): ")
            if choice.lower() == 'q':
                sys.exit(0)
            
            idx = int(choice) - 1
            if 0 <= idx < len(backups):
                confirm = input(f"\n⚠️ Confirmer la restauration de {backups[idx]['filename']}? (oui/non): ")
                if confirm.lower() in ['oui', 'o', 'yes', 'y']:
                    restore_backup(backups[idx]['filename'])
                else:
                    print("Restauration annulée.")
            else:
                print("❌ Numéro invalide")
        except ValueError:
            print("❌ Entrée invalide")
            
    elif command == 'clean':
        keep = 10
        if len(sys.argv) > 2:
            try:
                keep = int(sys.argv[2])
            except:
                pass
        delete_old_backups(keep_count=keep)
        
    elif command == 'stats':
        stats = get_db_stats()
        if stats:
            print("\n📊 Statistiques de la base de données:")
            print(f"   Taille: {stats['size']}")
            print("\n   Tables:")
            for table, count in stats['tables'].items():
                print(f"   - {table}: {count} enregistrements")
        else:
            print("❌ Impossible de lire les statistiques")
            
    elif command == 'export':
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        export_to_json(f'export_{timestamp}.json')
        
    else:
        print(f"❌ Commande inconnue: {command}")
