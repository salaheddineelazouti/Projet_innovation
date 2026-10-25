"""Sauvegarde et restauration de la base depuis la ligne de commande.

Usage :
    python scripts/backup.py backup [--no-compress]   créer une sauvegarde
    python scripts/backup.py list                     lister les sauvegardes
    python scripts/backup.py restore                  restaurer une sauvegarde (choix interactif)
    python scripts/backup.py clean [N] [--yes]        ne garder que les N plus récentes (10 par défaut, N >= 1) ;
                                                      confirmation demandée au-delà de 5 suppressions (--yes : sans)
    python scripts/backup.py stats                    nombre de lignes par table
    python scripts/backup.py export                   exporter toute la base en JSON

Les chemins de la base et du dossier des sauvegardes viennent de la configuration
(DATABASE_PATH et BACKUP_DIR). Les copies de la base faites avant une restauration figurent dans
la liste et sont supprimées par ``clean`` comme les autres sauvegardes ; les exports JSON les plus
anciens sont aussi supprimés (3 conservés).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tecpap.config import load_settings  # noqa: E402
from tecpap.db.backup import (  # noqa: E402
    create_backup,
    delete_old_backups,
    export_to_json,
    get_db_stats,
    list_backups,
    restore_backup,
)
from tecpap.logging_config import configure_logging  # noqa: E402

USAGE = __doc__.split("Les chemins")[0]
CONFIRM_WORDS = ("oui", "o", "yes", "y")
CLEAN_CONFIRM_THRESHOLD = 5
"""Nombre de suppressions à partir duquel ``clean`` demande une confirmation."""


def _use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def print_backups(backups: list[dict]) -> None:
    """Affiche le tableau des sauvegardes."""
    if not backups:
        print("Aucune sauvegarde trouvée.")
        return
    print(f"\n{'#':<3} {'Fichier':<35} {'Taille':<12} {'Date':<20} Compressée")
    print("-" * 80)
    for index, backup in enumerate(backups, 1):
        compressed = "oui" if backup["compressed"] else "non"
        print(f"{index:<3} {backup['filename']:<35} {backup['size_formatted']:<12} {backup['date']:<20} {compressed}")
    print("-" * 80)
    print(f"Total : {len(backups)} sauvegarde(s)\n")


def print_stats(stats: dict | None) -> None:
    """Affiche la taille de la base et le nombre de lignes par table."""
    if not stats:
        print("Statistiques indisponibles (base introuvable ou illisible).")
        return
    print(f"Taille de la base : {stats['size']}")
    for table, count in stats["tables"].items():
        print(f"  - {table} : {count} enregistrement(s)")


def restore_interactive(db_path: str, backup_dir: str) -> int:
    """Propose la liste des sauvegardes et restaure celle choisie après confirmation."""
    backups = list_backups(backup_dir)
    print_backups(backups)
    if not backups:
        return 1
    choice = input("Numéro de la sauvegarde à restaurer (q pour quitter) : ").strip()
    if choice.lower() == "q":
        return 0
    try:
        index = int(choice) - 1
    except ValueError:
        print("Entrée invalide.")
        return 1
    if not 0 <= index < len(backups):
        print("Numéro invalide.")
        return 1
    filename = backups[index]["filename"]
    confirm = input(f"Confirmer la restauration de {filename} ? (oui/non) : ").strip().lower()
    if confirm not in CONFIRM_WORDS:
        print("Restauration annulée.")
        return 0
    if restore_backup(filename, db_path=db_path, backup_dir=backup_dir):
        print(f"Base restaurée depuis {filename}.")
        return 0
    print("Échec de la restauration (voir le journal).")
    return 1


def clean_command(arguments: list[str], backup_dir: str) -> int:
    """Supprime les sauvegardes les plus anciennes pour n'en garder que N (``arguments`` : N et ``--yes``).

    Refuse N inférieur à 1 (garder zéro sauvegarde les supprimerait toutes). Au-delà de
    :data:`CLEAN_CONFIRM_THRESHOLD` suppressions, demande une confirmation, sauf avec ``--yes``.
    """
    assume_yes = "--yes" in arguments
    values = [argument for argument in arguments if argument != "--yes"]
    try:
        keep = int(values[0]) if values else 10
    except ValueError:
        print("Le nombre de sauvegardes à garder doit être un entier.")
        return 1
    if keep < 1:
        print("Le nombre de sauvegardes conservées doit être au moins 1.")
        return 1

    to_delete = len(list_backups(backup_dir)) - keep
    if to_delete > CLEAN_CONFIRM_THRESHOLD and not assume_yes:
        try:
            confirm = input(f"{to_delete} sauvegarde(s) vont être supprimées. Confirmer ? (oui/non) : ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in CONFIRM_WORDS:
            print("Suppression annulée.")
            return 0
    deleted = delete_old_backups(keep_count=keep, backup_dir=backup_dir)
    print(f"{deleted} ancien(s) fichier(s) supprimé(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _use_utf8_console()
    settings = load_settings()
    configure_logging(settings.log_level)
    db_path, backup_dir = settings.database_path, settings.backup_dir

    if not argv:
        print(USAGE)
        print_stats(get_db_stats(db_path))
        return 0

    command = argv[0].lower()
    if command == "backup":
        path = create_backup(compress="--no-compress" not in argv, db_path=db_path, backup_dir=backup_dir)
        print(f"Sauvegarde créée : {path}" if path else "Échec de la sauvegarde (voir le journal).")
        return 0 if path else 1
    if command == "list":
        print_backups(list_backups(backup_dir))
        return 0
    if command == "restore":
        return restore_interactive(db_path, backup_dir)
    if command == "clean":
        return clean_command(argv[1:], backup_dir)
    if command == "stats":
        print_stats(get_db_stats(db_path))
        return 0
    if command == "export":
        path = export_to_json(f"export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
                              db_path=db_path, backup_dir=backup_dir)
        print(f"Export JSON créé : {path}" if path else "Échec de l'export (voir le journal).")
        return 0 if path else 1

    print(f"Commande inconnue : {command}\n{USAGE}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
