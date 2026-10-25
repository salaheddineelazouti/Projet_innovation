"""Renomme les clients qui portent encore un nom provisoire (« Client WhatsApp +212… », « Client Inconnu »).

Ces noms sont attribués quand un message WhatsApp ne mentionne pas le nom du client. Le script
liste ces clients puis demande, pour chacun, le nom réel (Entrée pour le garder).

Usage :
    python scripts/rename_generic_clients.py               base définie par DATABASE_PATH
    python scripts/rename_generic_clients.py --db autre.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tecpap.config import load_settings  # noqa: E402


def _use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def find_generic_clients(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Renvoie les clients dont le nom est provisoire."""
    conn.row_factory = sqlite3.Row
    return conn.execute("""
        SELECT id, nom, telephone
        FROM clients
        WHERE nom LIKE 'Client WhatsApp%' OR nom LIKE 'Client Inconnu%'
    """).fetchall()


def rename_client(conn: sqlite3.Connection, client_id: int, new_name: str) -> None:
    """Donne le nom ``new_name`` au client ``client_id``."""
    conn.execute("UPDATE clients SET nom = ? WHERE id = ?", (new_name, client_id))
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Renomme les clients au nom provisoire.")
    parser.add_argument("--db", help="chemin de la base (par défaut DATABASE_PATH)")
    _use_utf8_console()
    args = parser.parse_args(argv)

    db_path = args.db or load_settings().database_path
    if not Path(db_path).exists():
        print(f"Base introuvable : {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        clients = find_generic_clients(conn)
        if not clients:
            print("Aucun client au nom provisoire.")
            return 0

        print(f"{len(clients)} client(s) au nom provisoire :")
        for client in clients:
            print(f"  id {client['id']} : {client['nom']} (téléphone : {client['telephone']})")

        for client in clients:
            print(f"\nClient id {client['id']} : {client['nom']}")
            new_name = input("  Nouveau nom (Entrée pour garder) : ").strip()
            if new_name:
                rename_client(conn, client["id"], new_name)
                print(f"  Renommé en : {new_name}")
            else:
                print("  Nom conservé")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
