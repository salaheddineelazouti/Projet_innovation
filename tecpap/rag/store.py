"""Stockage des extraits indexés et de leurs vecteurs dans SQLite.

Table ``rag_chunks`` (une ligne par extrait) :

========================  =====================================================================
Colonne                   Contenu
========================  =====================================================================
``id``                    identifiant (croissant : jamais réutilisé après une suppression)
``doc_id``                identifiant du document (chemin relatif sans extension)
``source_path``           chemin du fichier relatif au dossier de la base de connaissances
``title``                 titre du document
``section``               titre de la section
``chunk_index``           rang de l'extrait dans le document, à partir de 0
``text``                  texte de l'extrait
``keywords``              mots-clés du document (liste JSON)
``content_hash``          empreinte SHA-256 des champs indexés
``embedder``              nom de l'espace vectoriel du vecteur
``embedding``             vecteur ``float32`` sérialisé (ordre des octets petit-boutiste)
``created_at``            date d'écriture de la ligne
========================  =====================================================================

Le couple (``doc_id``, ``chunk_index``) est unique. Une ligne n'est jamais modifiée sur place :
un extrait modifié est supprimé puis réinséré. Comme un identifiant supprimé n'est jamais
réutilisé (``AUTOINCREMENT``), deux états de la table qui ont le même couple (nombre de lignes,
plus grand identifiant) ont le même contenu. :meth:`ChunkStore.signature` s'en sert pour savoir,
à moindre coût, si l'index a changé depuis la dernière lecture (par exemple après une indexation
lancée depuis un autre processus).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from tecpap.rag.documents import Chunk
from tecpap.rag.embeddings import blob_to_vector, vector_to_blob

logger = logging.getLogger(__name__)

TABLE_NAME = "rag_chunks"

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS rag_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id TEXT NOT NULL,
        source_path TEXT NOT NULL,
        title TEXT NOT NULL,
        section TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        text TEXT NOT NULL,
        keywords TEXT NOT NULL DEFAULT '[]',
        content_hash TEXT NOT NULL,
        embedder TEXT NOT NULL,
        embedding BLOB NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (doc_id, chunk_index)
    )
"""

# Colonnes ajoutées après la première version de la table (bases existantes).
_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("keywords", "TEXT NOT NULL DEFAULT '[]'"),
)

_SELECT_COLUMNS = (
    "id, doc_id, source_path, title, section, chunk_index, text, keywords, content_hash, embedder, embedding"
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crée la table ``rag_chunks`` si elle n'existe pas et ajoute les colonnes manquantes.

    La fonction peut être appelée à chaque démarrage : elle ne modifie pas une table à jour. Elle
    valide (« commit ») la transaction en cours de la connexion.
    """
    with conn:
        conn.execute(_CREATE_TABLE)
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()}
        for column, definition in _MIGRATION_COLUMNS:
            if column not in existing:
                # Nom et définition proviennent de la constante _MIGRATION_COLUMNS, jamais d'une saisie.
                conn.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {column} {definition}")


@dataclass(frozen=True)
class StoredChunk:
    """Extrait relu depuis la base, avec son vecteur.

    Attributes:
        id: identifiant de la ligne.
        chunk: extrait (texte et métadonnées).
        embedder: nom de l'espace vectoriel du vecteur.
        embedding: vecteur ``float32``.
    """

    id: int
    chunk: Chunk
    embedder: str
    embedding: np.ndarray


@dataclass(frozen=True)
class IndexedDocument:
    """Résumé d'un document indexé : identifiant, titre, fichier et nombre d'extraits."""

    doc_id: str
    title: str
    source_path: str
    chunks: int


def _decode_keywords(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _row_to_stored(row: tuple) -> StoredChunk | None:
    (row_id, doc_id, source_path, title, section, chunk_index, text, keywords, digest, embedder, blob) = row
    try:
        vector = blob_to_vector(bytes(blob))
    except (TypeError, ValueError):
        logger.warning("Extrait %s (%s) ignoré : vecteur illisible", row_id, doc_id)
        return None
    chunk = Chunk(
        doc_id=doc_id,
        source_path=source_path,
        title=title,
        section=section,
        text=text,
        content_hash=digest,
        chunk_index=int(chunk_index),
        keywords=_decode_keywords(keywords),
    )
    return StoredChunk(id=int(row_id), chunk=chunk, embedder=embedder, embedding=vector)


class ChunkStore:
    """Lecture et écriture de la table ``rag_chunks`` sur une connexion SQLite.

    Args:
        conn: connexion ouverte ; la table est créée si besoin (:func:`ensure_schema`).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        ensure_schema(conn)

    def signature(self) -> tuple[int, int]:
        """Nombre de lignes et plus grand identifiant : change à chaque écriture de l'index."""
        count, max_id = self.conn.execute(f"SELECT COUNT(*), COALESCE(MAX(id), 0) FROM {TABLE_NAME}").fetchone()
        return int(count), int(max_id)

    def ids(self) -> set[int]:
        """Identifiants de toutes les lignes, lisibles ou non."""
        return {int(row[0]) for row in self.conn.execute(f"SELECT id FROM {TABLE_NAME}")}  # nosec B608

    def count(self) -> int:
        """Nombre d'extraits indexés."""
        return self.signature()[0]

    def all(self) -> list[StoredChunk]:
        """Tous les extraits, triés par document puis par rang ; les lignes illisibles sont ignorées."""
        rows = self.conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM {TABLE_NAME} ORDER BY doc_id, chunk_index"  # nosec B608
        ).fetchall()
        stored = (_row_to_stored(tuple(row)) for row in rows)
        return [item for item in stored if item is not None]

    def documents(self) -> list[IndexedDocument]:
        """Documents indexés avec leur nombre d'extraits, triés par identifiant."""
        rows = self.conn.execute(
            f"SELECT doc_id, MIN(title), MIN(source_path), COUNT(*) FROM {TABLE_NAME} "
            "GROUP BY doc_id ORDER BY doc_id"
        ).fetchall()
        return [IndexedDocument(doc_id=row[0], title=row[1], source_path=row[2], chunks=int(row[3])) for row in rows]

    def apply(self, *, delete_ids: Iterable[int] = (),
              insert: Iterable[tuple[Chunk, str, np.ndarray]] = ()) -> None:
        """Supprime puis insère des extraits dans une seule transaction.

        Args:
            delete_ids: identifiants des lignes à supprimer.
            insert: extraits à insérer, avec le nom de l'espace vectoriel et le vecteur.
        """
        delete_params = [(int(row_id),) for row_id in delete_ids]
        insert_params = [
            (chunk.doc_id, chunk.source_path, chunk.title, chunk.section, chunk.chunk_index, chunk.text,
             json.dumps(list(chunk.keywords), ensure_ascii=False), chunk.content_hash, embedder,
             vector_to_blob(vector))
            for chunk, embedder, vector in insert
        ]
        with self.conn:
            if delete_params:
                self.conn.executemany(f"DELETE FROM {TABLE_NAME} WHERE id = ?", delete_params)  # nosec B608
            if insert_params:
                self.conn.executemany(
                    f"INSERT INTO {TABLE_NAME} (doc_id, source_path, title, section, chunk_index, text, "  # nosec B608
                    "keywords, content_hash, embedder, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    insert_params,
                )

    def clear(self) -> int:
        """Vide l'index ; renvoie le nombre de lignes supprimées."""
        with self.conn:
            cursor = self.conn.execute(f"DELETE FROM {TABLE_NAME}")  # nosec B608
        return cursor.rowcount


def cosine_similarities(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Similarité cosinus entre chaque ligne de ``matrix`` et ``vector`` (0 pour un vecteur nul).

    Les deux arguments n'ont pas besoin d'être normalisés. Des dimensions différentes lèvent
    ``ValueError``.
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    if matrix.size == 0:
        return np.zeros(matrix.shape[0] if matrix.ndim == 2 else 0, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != vector.shape[0]:
        raise ValueError("Dimensions incompatibles pour le calcul de similarité cosinus")
    norms = np.linalg.norm(matrix, axis=1) * float(np.linalg.norm(vector))
    dots = matrix @ vector
    with np.errstate(divide="ignore", invalid="ignore"):
        similarities = np.where(norms > 0, dots / np.where(norms > 0, norms, 1.0), 0.0)
    return similarities.astype(np.float32)


__all__ = [
    "TABLE_NAME",
    "ChunkStore",
    "IndexedDocument",
    "StoredChunk",
    "cosine_similarities",
    "ensure_schema",
]
