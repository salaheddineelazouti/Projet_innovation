"""Traite les emails de commande récents depuis la ligne de commande.

Usage :
    python scripts/process_emails.py                     # 5 derniers emails, enregistrement en base
    python scripts/process_emails.py --max 20            # 20 derniers emails
    python scripts/process_emails.py --no-save           # analyse sans enregistrement ni email au client
    python scripts/process_emails.py --output commandes.json

Le fichier ``--output`` contient des données personnelles de clients : il ne doit pas être
versionné (``orders_output.json`` est exclu par ``.gitignore``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tecpap.config import load_settings  # noqa: E402
from tecpap.logging_config import configure_logging  # noqa: E402
from tecpap.processing.order_processor import OrderProcessor  # noqa: E402


def _use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def print_summary(orders: list[dict]) -> None:
    """Affiche un résumé de chaque bon de commande détecté."""
    if not orders:
        print("Aucun bon de commande détecté.")
        return
    print(f"{len(orders)} bon(s) de commande détecté(s) :")
    for index, order in enumerate(orders, 1):
        print(f"\nCommande {index}")
        print(f"  N° commande : {order.get('numero_commande') or 'N/A'}")
        print(f"  Client      : {order.get('entreprise_cliente') or 'N/A'}")
        print(f"  Produit     : {order.get('type_produit') or 'N/A'}")
        print(f"  Désignation : {order.get('nature_produit') or 'N/A'}")
        print(f"  Quantité    : {order.get('quantite', 'N/A')} {order.get('unite') or ''}")
        if order.get("prix_total"):
            print(f"  Total       : {order['prix_total']} {order.get('devise') or ''}")
        if order.get("date_livraison"):
            print(f"  Livraison   : {order['date_livraison']}")
        print(f"  Confiance   : {order.get('confiance', 'N/A')} %")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Traite les emails de commande récents de la boîte Gmail.")
    parser.add_argument("--max", type=int, default=5, help="nombre d'emails récents examinés (5 par défaut)")
    parser.add_argument("--no-save", action="store_true",
                        help="n'enregistre pas les commandes et n'envoie pas d'accusé de réception")
    parser.add_argument("--output", help="fichier JSON où écrire les commandes détectées")
    _use_utf8_console()
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)

    processor = OrderProcessor(settings=settings)
    orders = processor.process_new_emails(max_emails=args.max, save_to_db=not args.no_save)
    print_summary(orders)

    if args.output and orders:
        Path(args.output).write_text(json.dumps(orders, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"\nCommandes écrites dans {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
