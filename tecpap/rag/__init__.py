"""Génération augmentée par la recherche (RAG, « retrieval-augmented generation »).

L'assistant répond aux questions des clients à partir des extraits de la base de connaissances
les plus proches de la question, et seulement quand ces extraits sont assez pertinents.

- :mod:`tecpap.rag.text` : normalisation des textes (français, arabe, darija, arabizi) ;
- :mod:`tecpap.rag.documents` : lecture des documents Markdown et découpage en extraits ;
- :mod:`tecpap.rag.embeddings` : plongements vectoriels (calcul local ou modèle OpenAI) ;
- :mod:`tecpap.rag.bm25` : score Okapi BM25 ;
- :mod:`tecpap.rag.store` : table SQLite ``rag_chunks`` ;
- :mod:`tecpap.rag.retriever` : indexation, recherche hybride, exemples de commandes validées ;
- :mod:`tecpap.rag.calibration` : mesure de la séparation et choix du seuil ``rag_min_score``.
"""

from tecpap.rag.documents import Chunk
from tecpap.rag.embeddings import EmbeddingResult, LocalHashEmbedder, OpenAIEmbedder
from tecpap.rag.retriever import (
    IndexStats,
    KnowledgeBase,
    OrderExample,
    OrderExampleRetriever,
    SearchHit,
    best_score,
    build_embedder,
)
from tecpap.rag.store import ensure_schema

__all__ = [
    "Chunk",
    "EmbeddingResult",
    "IndexStats",
    "KnowledgeBase",
    "LocalHashEmbedder",
    "OpenAIEmbedder",
    "OrderExample",
    "OrderExampleRetriever",
    "SearchHit",
    "best_score",
    "build_embedder",
    "ensure_schema",
]
