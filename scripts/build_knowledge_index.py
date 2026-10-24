"""Construit ou met à jour l'index de recherche de la base de connaissances.

Usage :
    python scripts/build_knowledge_index.py                  mise à jour (extraits modifiés seulement)
    python scripts/build_knowledge_index.py --force          recalcul de tous les vecteurs
    python scripts/build_knowledge_index.py --db autre.db    autre base que DATABASE_PATH
    python scripts/build_knowledge_index.py --local          calcul local des vecteurs, sans API
    python scripts/build_knowledge_index.py --evaluate tests/data/rag_queries.jsonl
                                                             mesure de la séparation des questions annotées

Le dossier des documents vient de KNOWLEDGE_DIR (ou de --knowledge-dir). Avec une clé
OPENAI_API_KEY, les vecteurs sont calculés par le modèle EMBEDDING_MODEL ; sans clé, ou avec
--local, par le calcul local. Le script affiche le bilan de l'indexation : extraits ajoutés,
mis à jour, supprimés et inchangés.

Avec --evaluate, le script mesure ensuite, sur un jeu de questions annotées, la séparation entre
les questions auxquelles la base répond et les autres au seuil RAG_MIN_SCORE (ou --threshold),
et indique le seuil qui les sépare le mieux.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tecpap.config import load_settings  # noqa: E402
from tecpap.logging_config import configure_logging  # noqa: E402
from tecpap.rag.calibration import (  # noqa: E402
    dev_test_check,
    format_report,
    load_labelled_queries,
    run_queries,
    separation_report,
)
from tecpap.rag.embeddings import LocalHashEmbedder  # noqa: E402
from tecpap.rag.retriever import KnowledgeBase, build_embedder  # noqa: E402


def _use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def build_parser() -> argparse.ArgumentParser:
    """Options de la ligne de commande."""
    parser = argparse.ArgumentParser(description="Indexe la base de connaissances pour la recherche documentaire.")
    parser.add_argument("--force", action="store_true", help="recalcule les vecteurs de tous les extraits")
    parser.add_argument("--db", help="chemin de la base SQLite (par défaut DATABASE_PATH)")
    parser.add_argument("--knowledge-dir", help="dossier des documents (par défaut KNOWLEDGE_DIR)")
    parser.add_argument("--local", action="store_true", help="calcul local des vecteurs, sans appel à l'API")
    parser.add_argument("--evaluate", metavar="FICHIER",
                        help="jeu de questions annotées (JSON Lines) à évaluer après l'indexation")
    parser.add_argument("--threshold", type=float, help="seuil évalué (par défaut RAG_MIN_SCORE)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Indexe la base de connaissances ; renvoie le code de sortie du programme."""
    _use_utf8_console()
    args = build_parser().parse_args(argv)
    settings = load_settings()
    configure_logging(settings.log_level)

    knowledge_dir = Path(args.knowledge_dir or settings.knowledge_dir)
    if not knowledge_dir.is_dir():
        print(f"Dossier de la base de connaissances introuvable : {knowledge_dir}", file=sys.stderr)
        return 1
    db_path = args.db or settings.database_path
    embedder = LocalHashEmbedder() if args.local else build_embedder(settings)

    conn = sqlite3.connect(db_path)
    try:
        knowledge = KnowledgeBase(conn, embedder, knowledge_dir)
        stats = knowledge.index(force=args.force)
        print(f"Base de connaissances : {knowledge_dir}")
        print(f"Index : {db_path}")
        print(stats)
        if args.evaluate:
            threshold = settings.rag_min_score if args.threshold is None else args.threshold
            queries = load_labelled_queries(args.evaluate)
            outcomes = run_queries(knowledge, queries, k=settings.rag_top_k)
            report = separation_report(outcomes, threshold, k=settings.rag_top_k)
            print()
            print(format_report(report, dev_test_check(outcomes)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
