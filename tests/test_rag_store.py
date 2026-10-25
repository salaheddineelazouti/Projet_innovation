"""Tests du stockage SQLite des extraits indexés et du calcul de similarité cosinus."""

from __future__ import annotations

import logging
import sqlite3

import numpy as np
import pytest

from tecpap.rag.documents import Chunk, content_hash
from tecpap.rag.store import (
    TABLE_NAME,
    ChunkStore,
    IndexedDocument,
    cosine_similarities,
    ensure_schema,
)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def make_chunk(doc_id: str = "produits", index: int = 0, text: str = "Sachets fond plat.",
               keywords: tuple[str, ...] = ("sachet", "ساشي")) -> Chunk:
    return Chunk(
        doc_id=doc_id,
        source_path=f"{doc_id}.md",
        title=f"Titre {doc_id}",
        section=f"Section {index}",
        text=text,
        content_hash=content_hash(f"Titre {doc_id}", f"Section {index}", keywords, text),
        chunk_index=index,
        keywords=keywords,
    )


def columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({TABLE_NAME})")]


class TestSchema:
    def test_creates_table_with_expected_columns(self, conn):
        ensure_schema(conn)
        assert columns(conn) == [
            "id", "doc_id", "source_path", "title", "section", "chunk_index", "text", "keywords",
            "content_hash", "embedder", "embedding", "created_at",
        ]

    def test_is_idempotent_and_keeps_rows(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk(), "local", np.ones(4))])
        ensure_schema(conn)
        ensure_schema(conn)
        assert store.count() == 1

    def test_adds_missing_columns_to_an_older_table(self, conn):
        conn.execute("""
            CREATE TABLE rag_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL, source_path TEXT NOT NULL,
                title TEXT NOT NULL, section TEXT NOT NULL, chunk_index INTEGER NOT NULL, text TEXT NOT NULL,
                content_hash TEXT NOT NULL, embedder TEXT NOT NULL, embedding BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE (doc_id, chunk_index))
        """)
        conn.execute("INSERT INTO rag_chunks (doc_id, source_path, title, section, chunk_index, text, content_hash, "
                     "embedder, embedding) VALUES ('d', 'd.md', 'T', 'S', 0, 'x', 'h', 'local', ?)",
                     (np.ones(2, dtype="<f4").tobytes(),))
        conn.commit()
        ensure_schema(conn)
        assert "keywords" in columns(conn)
        stored = ChunkStore(conn).all()
        assert stored[0].chunk.keywords == ()

    def test_doc_id_and_chunk_index_are_unique(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk(), "local", np.ones(4))])
        with pytest.raises(sqlite3.IntegrityError):
            store.apply(insert=[(make_chunk(text="autre"), "local", np.ones(4))])


class TestChunkStore:
    def test_round_trip_of_chunk_vector_and_space(self, conn):
        store = ChunkStore(conn)
        chunk = make_chunk()
        vector = np.array([0.25, -0.5, 1.0], dtype=np.float64)
        store.apply(insert=[(chunk, "local-hash-v1-512", vector)])
        (stored,) = store.all()
        assert stored.chunk == chunk
        assert stored.embedder == "local-hash-v1-512"
        assert stored.embedding.dtype == np.float32
        assert np.array_equal(stored.embedding, vector.astype(np.float32))
        blob = conn.execute("SELECT embedding FROM rag_chunks").fetchone()[0]
        assert bytes(blob) == vector.astype("<f4").tobytes()

    def test_rows_sorted_by_document_and_rank(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("b", 1), "e", np.ones(2)), (make_chunk("a", 0), "e", np.ones(2)),
                            (make_chunk("b", 0), "e", np.ones(2))])
        assert [(item.chunk.doc_id, item.chunk.chunk_index) for item in store.all()] == [("a", 0), ("b", 0), ("b", 1)]

    def test_signature_changes_on_every_write(self, conn):
        store = ChunkStore(conn)
        signatures = [store.signature()]
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2))])
        signatures.append(store.signature())
        first_id = store.all()[0].id
        # Remplacement du même extrait : même nombre de lignes, mais identifiant plus grand.
        store.apply(delete_ids=[first_id], insert=[(make_chunk("a", text="modifié"), "e", np.ones(2))])
        signatures.append(store.signature())
        store.apply(delete_ids=[store.all()[0].id])
        signatures.append(store.signature())
        # Deux tables vides ont la même signature, ce qui est sans risque : leur contenu est identique.
        assert signatures == [(0, 0), (1, 1), (1, 2), (0, 0)]

    def test_identifiers_are_never_reused(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2))])
        old_id = store.all()[0].id
        store.apply(delete_ids=[old_id])
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2))])
        assert store.all()[0].id > old_id

    def test_apply_is_atomic(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2))])
        existing = store.all()[0].id
        duplicate = [(make_chunk("b"), "e", np.ones(2)), (make_chunk("b"), "e", np.ones(2))]
        with pytest.raises(sqlite3.IntegrityError):
            store.apply(delete_ids=[existing], insert=duplicate)
        # Ni la suppression ni la première insertion n'ont été conservées.
        assert [item.chunk.doc_id for item in store.all()] == ["a"]

    def test_unreadable_rows_are_skipped(self, conn, caplog):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2)), (make_chunk("b"), "e", np.ones(2))])
        conn.execute("UPDATE rag_chunks SET embedding = ? WHERE doc_id = 'b'", (b"\x00\x01\x02",))
        conn.execute("UPDATE rag_chunks SET keywords = 'pas du json' WHERE doc_id = 'a'")
        conn.commit()
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.store"):
            stored = store.all()
        assert [item.chunk.doc_id for item in stored] == ["a"]
        assert stored[0].chunk.keywords == ()
        assert "vecteur illisible" in caplog.text

    @pytest.mark.parametrize("raw", ["", "[]"])
    def test_empty_keywords(self, conn, raw):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2))])
        conn.execute("UPDATE rag_chunks SET keywords = ?", (raw,))
        conn.commit()
        assert store.all()[0].chunk.keywords == ()

    def test_keywords_json_must_be_a_list(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2))])
        conn.execute("""UPDATE rag_chunks SET keywords = '{"a": 1}'""")
        conn.commit()
        assert store.all()[0].chunk.keywords == ()

    def test_arabic_keywords_are_stored_as_text(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk(keywords=("ساشي", "kif dima")), "e", np.ones(2))])
        raw = conn.execute("SELECT keywords FROM rag_chunks").fetchone()[0]
        assert raw == '["ساشي", "kif dima"]'

    def test_documents_summary(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("produits", 0), "e", np.ones(2)), (make_chunk("produits", 1), "e", np.ones(2)),
                            (make_chunk("contact", 0), "e", np.ones(2))])
        assert store.documents() == [
            IndexedDocument(doc_id="contact", title="Titre contact", source_path="contact.md", chunks=1),
            IndexedDocument(doc_id="produits", title="Titre produits", source_path="produits.md", chunks=2),
        ]

    def test_ids_include_unreadable_rows(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2)), (make_chunk("b"), "e", np.ones(2))])
        conn.execute("UPDATE rag_chunks SET embedding = ? WHERE doc_id = 'b'", (b"\x00",))
        conn.commit()
        assert len(store.all()) == 1
        assert store.ids() == {1, 2}

    def test_clear(self, conn):
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk("a"), "e", np.ones(2)), (make_chunk("b"), "e", np.ones(2))])
        assert store.clear() == 2
        assert store.count() == 0

    def test_empty_apply_does_nothing(self, conn):
        store = ChunkStore(conn)
        store.apply()
        assert store.signature() == (0, 0)

    def test_works_with_row_factory(self, conn):
        conn.row_factory = sqlite3.Row
        store = ChunkStore(conn)
        store.apply(insert=[(make_chunk(), "e", np.ones(2))])
        assert store.all()[0].chunk.doc_id == "produits"
        assert store.signature() == (1, 1)


class TestCosineSimilarities:
    def test_values_without_prior_normalization(self):
        matrix = np.array([[1.0, 0.0], [0.0, 2.0], [-3.0, 0.0], [1.0, 1.0]])
        similarities = cosine_similarities(matrix, np.array([2.0, 0.0]))
        assert similarities.dtype == np.float32
        assert np.allclose(similarities, [1.0, 0.0, -1.0, np.sqrt(0.5)])

    def test_zero_vectors_give_zero(self):
        assert np.array_equal(cosine_similarities(np.array([[0.0, 0.0], [1.0, 0.0]]), np.array([1.0, 0.0])), [0.0, 1.0])
        assert np.array_equal(cosine_similarities(np.array([[1.0, 0.0]]), np.zeros(2)), [0.0])

    def test_empty_matrix(self):
        assert cosine_similarities(np.zeros((0, 3)), np.ones(3)).shape == (0,)

    def test_dimension_mismatch(self):
        with pytest.raises(ValueError):
            cosine_similarities(np.ones((2, 3)), np.ones(4))
