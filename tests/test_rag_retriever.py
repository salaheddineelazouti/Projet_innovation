"""Tests de la recherche hybride, de l'indexation, de la calibration du seuil et des exemples de commandes."""

from __future__ import annotations

import importlib.util
import json
import logging
import math
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest

from tecpap.config import Settings
from tecpap.rag import calibration
from tecpap.rag.bm25 import BM25Index
from tecpap.rag.calibration import (
    LabelledQuery,
    best_threshold,
    dev_test_check,
    format_report,
    load_labelled_queries,
    metrics_at,
    roc_auc,
    run_queries,
    separation_report,
    split_dev_test,
)
from tecpap.rag.documents import Chunk, load_documents
from tecpap.rag.embeddings import LocalHashEmbedder, OpenAIEmbedder
from tecpap.rag.retriever import (
    CLIENT_PLACEHOLDER,
    EMAIL_PLACEHOLDER,
    LEXICAL_EMBEDDER,
    PHONE_PLACEHOLDER,
    RRF_K,
    IndexStats,
    KnowledgeBase,
    OrderExampleRetriever,
    SearchHit,
    anonymize_text,
    best_score,
    build_embedder,
    reciprocal_rank_fusion,
    relevance_score,
)
from tecpap.rag.store import ChunkStore
from tests.fakes import FakeLLMClient, unavailable_llm

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
LABELLED_QUERIES = Path(__file__).resolve().parent / "data" / "rag_queries.jsonl"

PRODUCTS_DOC = """---
title: Produits TECPAP
categorie: produits
mots_cles:
  - sachet
  - sachiyat
  - ساشي
  - poignées torsadées
  - yddin mfetlin
  - مقبض مفتول
---

# Produits TECPAP

TECPAP fabrique des sacs en papier kraft.

## Sachets fond plat (code SFP)

Les sachets fond plat servent à emballer les sandwichs et les tacos.

Autres formulations : « sachiyat dyal tacos », « ساشي ديال السندويش ».

## Sac fond carré avec poignées torsadées (code SFCPT)

Le sac à poignées torsadées est le sac premium pour les cadeaux et le luxe.

Autres noms : yddin mfetlin, كيس بمقبض مفتول للهدايا.
"""

PAPERS_DOC = """---
title: Papiers kraft
mots_cles: [grammage, laize, gram, غراماج]
---

# Papiers kraft

## Grammage du papier kraft

Le grammage est la masse d'un mètre carré de papier, en g/m². Grammages standards : 40 à 140 g/m².

Autres formulations : « ch7al d gram ? », « شحال ديال الغراماج؟ ».

## Laize du papier kraft

La laize est la largeur de la bobine de papier, en cm.
"""

CONTACT_DOC = """---
title: Contact TECPAP
mots_cles: [adresse, fin kaynin, العنوان, téléphone]
---

# Contact TECPAP

## Adresse de TECPAP

L'adresse de TECPAP est : Parc Industriel CFCIM Bouskoura, Casablanca.

Autres formulations : « fin kaynin ? », « ما هو العنوان؟ ».

## Téléphone de TECPAP

Le numéro de téléphone de TECPAP est le +212 (0)5 22 86 56 83.
"""


class SpyEmbedder(LocalHashEmbedder):
    """Calcul local qui enregistre les textes plongés et les espaces demandés."""

    def __init__(self, dim: int = 512):
        super().__init__(dim)
        self.embedded: list[list[str]] = []
        self.spaces: list[str] = []

    def embed(self, texts):
        self.embedded.append(list(texts))
        return super().embed(texts)

    def embed_in(self, space, texts):
        self.spaces.append(space)
        return super().embed_in(space, texts)


def write_knowledge(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return directory


@pytest.fixture
def knowledge_dir(tmp_path) -> Path:
    return write_knowledge(tmp_path / "knowledge", {
        "produits.md": PRODUCTS_DOC, "papiers.md": PAPERS_DOC, "contact.md": CONTACT_DOC,
        "_guide.md": "# Guide\n## Règle\nTexte réservé à l'équipe commerciale.",
    })


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    yield connection
    connection.close()


@pytest.fixture
def spy() -> SpyEmbedder:
    return SpyEmbedder()


@pytest.fixture
def kb(conn, spy, knowledge_dir) -> KnowledgeBase:
    return KnowledgeBase(conn, spy, knowledge_dir)


def embedded_count(spy: SpyEmbedder) -> int:
    return sum(len(texts) for texts in spy.embedded)


def stored_spaces(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT DISTINCT embedder FROM rag_chunks")}


# ---------------------------------------------------------------- Okapi BM25


class TestBM25Index:
    CORPUS = [["sachet", "kraft", "sachet"], ["sac", "poign", "torsad"], ["kraft", "grammag"]]

    def test_idf_follows_lucene_formula(self):
        index = BM25Index(self.CORPUS)
        assert index.idf("kraft") == pytest.approx(math.log(1 + (3 - 2 + 0.5) / (2 + 0.5)))
        assert index.idf("absent") == pytest.approx(math.log(1 + 3.5 / 0.5)) == pytest.approx(index.max_idf)
        assert index.idf("kraft") < index.idf("sac") < index.max_idf

    def test_score_matches_formula(self):
        index = BM25Index(self.CORPUS, k1=1.2, b=0.75)
        average = 8 / 3
        weight = 2 * 2.2 / (2 + 1.2 * (1 - 0.75 + 0.75 * 3 / average))
        expected = index.idf("sachet") * weight
        assert index.score(["sachet"], 0) == pytest.approx(expected)
        assert index.scores(["sachet"]) == pytest.approx([expected, 0.0, 0.0])

    def test_frequency_saturates(self):
        index = BM25Index([["kraft"] * 1, ["kraft"] * 10, ["autre"] * 10], b=0.0)
        one, ten, _ = index.scores(["kraft"])
        assert one < ten < one * 2.2

    def test_length_normalization(self):
        corpus = [["kraft", "x"], ["kraft"] + ["y"] * 20]
        with_b = BM25Index(corpus, b=1.0).scores(["kraft"])
        without_b = BM25Index(corpus, b=0.0).scores(["kraft"])
        assert with_b[0] > with_b[1]
        assert without_b[0] == pytest.approx(without_b[1])

    def test_absent_word_scores_zero(self):
        index = BM25Index(self.CORPUS)
        assert index.score(["absent"], 0) == 0.0
        assert index.score([], 1) == 0.0

    def test_repeated_query_words_count_each_time(self):
        index = BM25Index(self.CORPUS)
        assert index.scores(["kraft", "kraft"])[2] == pytest.approx(2 * index.scores(["kraft"])[2])
        assert index.score(["kraft", "kraft"], 2) == pytest.approx(index.scores(["kraft", "kraft"])[2])

    def test_empty_query_and_empty_corpus(self):
        assert BM25Index(self.CORPUS).scores([]) == [0.0, 0.0, 0.0]
        empty = BM25Index([])
        assert len(empty) == 0
        assert empty.scores(["kraft"]) == []
        assert BM25Index([[]]).scores(["kraft"]) == [0.0]

    def test_coverage(self):
        index = BM25Index(self.CORPUS)
        assert index.coverage(["sachet", "kraft"], 0) == pytest.approx(1.0)
        assert index.coverage(["sachet", "kraft"], 1) == 0.0
        assert index.coverage([], 0) == 0.0
        partial = index.coverage(["sachet", "meteo"], 0)
        assert partial == pytest.approx(index.idf("sachet") / (index.idf("sachet") + index.max_idf))

    def test_unknown_words_lower_coverage_more_than_common_words(self):
        index = BM25Index(self.CORPUS)
        with_common = index.coverage(["sachet", "kraft"], 0)
        with_unknown = index.coverage(["sachet", "meteo"], 0)
        assert with_unknown < with_common

    def test_parameter_validation(self):
        with pytest.raises(ValueError):
            BM25Index([], k1=-1)
        with pytest.raises(ValueError):
            BM25Index([], b=1.5)


# ---------------------------------------------------------------- fusion et scores


class TestFusionAndScores:
    def test_reciprocal_rank_fusion(self):
        fused = reciprocal_rank_fusion([[2, 0], [0, 1]], k=60)
        assert fused[0] == pytest.approx(1 / 62 + 1 / 61)
        assert fused[2] == pytest.approx(1 / 61)
        assert fused[1] == pytest.approx(1 / 62)
        assert max(fused, key=fused.get) == 0
        assert reciprocal_rank_fusion([[], []]) == {}
        assert RRF_K == 60

    def test_relevance_score_is_bounded_mean(self):
        assert relevance_score(0.5, 0.3) == pytest.approx(0.4)
        assert relevance_score(-1.0, -0.2) == 0.0
        assert relevance_score(2.0, 2.0) == 1.0

    def test_best_score(self):
        chunk = Chunk("d", "d.md", "T", "S", "x", "h")
        hits = [SearchHit(chunk, 0.2, 0.0, 0.0), SearchHit(chunk, 0.6, 0.0, 0.0), SearchHit(chunk, 0.4, 0.0, 0.0)]
        assert best_score(hits) == 0.6
        assert best_score([]) == 0.0

    def test_search_hit_minimal_construction(self):
        hit = SearchHit(chunk=Chunk("d", "d.md", "T", "S", "x", "h"), score=0.5, dense_score=0.4, bm25_score=3.0)
        assert (hit.coverage, hit.lexical_score, hit.rrf_score) == (0.0, 0.0, 0.0)

    def test_index_stats(self):
        stats = IndexStats(added=2, updated=1, removed=3, unchanged=4, embedder="local-hash-v1-512")
        assert stats.total == 7
        assert stats.changed
        assert str(stats) == ("7 extraits indexés (2 ajoutés, 1 mis à jour, 3 supprimés, 4 inchangés) ; "
                              "vecteurs : local-hash-v1-512")
        assert not IndexStats(0, 0, 0, 5, "e").changed


# ---------------------------------------------------------------- indexation


class TestIndexing:
    def test_first_index_adds_every_chunk(self, kb, spy, conn):
        stats = kb.index()
        assert stats == IndexStats(added=7, updated=0, removed=0, unchanged=0, embedder=spy.name)
        assert kb.chunk_count() == 7
        assert embedded_count(spy) == 7
        assert stored_spaces(conn) == {spy.name}
        assert [document.doc_id for document in kb.documents()] == ["contact", "papiers", "produits"]

    def test_writing_guides_are_not_indexed(self, kb):
        kb.index()
        assert "_guide" not in {document.doc_id for document in kb.documents()}

    def test_second_index_is_idempotent(self, kb, spy):
        kb.index()
        spy.embedded.clear()
        stats = kb.index()
        assert stats == IndexStats(added=0, updated=0, removed=0, unchanged=7, embedder=spy.name)
        assert spy.embedded == []

    def test_only_modified_chunk_is_embedded(self, kb, spy, knowledge_dir):
        kb.index()
        spy.embedded.clear()
        path = knowledge_dir / "papiers.md"
        path.write_text(PAPERS_DOC.replace("en cm.", "exprimée en centimètres."), encoding="utf-8")
        stats = kb.index()
        assert (stats.added, stats.updated, stats.removed, stats.unchanged) == (0, 1, 0, 6)
        assert len(spy.embedded) == 1
        assert "exprimée en centimètres" in spy.embedded[0][0]

    def test_keyword_change_updates_every_chunk_of_the_document(self, kb, spy, knowledge_dir):
        kb.index()
        spy.embedded.clear()
        (knowledge_dir / "contact.md").write_text(CONTACT_DOC.replace("téléphone]", "téléphone, nmra]"),
                                                  encoding="utf-8")
        stats = kb.index()
        assert (stats.updated, stats.unchanged) == (2, 5)
        assert embedded_count(spy) == 2

    def test_vectors_are_reused_when_chunks_shift(self, kb, spy, knowledge_dir):
        kb.index()
        spy.embedded.clear()
        shifted = PAPERS_DOC.replace("## Grammage du papier kraft",
                                     "## Origine du papier kraft\n\nLe papier kraft vient du bois.\n\n"
                                     "## Grammage du papier kraft")
        (knowledge_dir / "papiers.md").write_text(shifted, encoding="utf-8")
        stats = kb.index()
        # Les deux sections existantes changent de rang (mise à jour sans recalcul), une est ajoutée.
        assert (stats.added, stats.updated, stats.removed, stats.unchanged) == (1, 2, 0, 5)
        assert embedded_count(spy) == 1
        assert "vient du bois" in spy.embedded[0][0]

    def test_deleted_document_is_removed(self, kb, conn, knowledge_dir):
        kb.index()
        (knowledge_dir / "contact.md").unlink()
        stats = kb.index()
        assert (stats.removed, stats.unchanged) == (2, 5)
        assert conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE doc_id = 'contact'").fetchone()[0] == 0

    def test_shortened_document_loses_its_last_chunks(self, kb, knowledge_dir):
        kb.index()
        (knowledge_dir / "papiers.md").write_text(PAPERS_DOC.split("## Laize")[0], encoding="utf-8")
        stats = kb.index()
        assert (stats.removed, stats.unchanged) == (1, 6)

    def test_renamed_with_underscore_is_removed(self, kb, knowledge_dir):
        kb.index()
        (knowledge_dir / "contact.md").rename(knowledge_dir / "_contact.md")
        assert kb.index().removed == 2

    def test_unreadable_row_is_replaced(self, kb, conn, caplog):
        kb.index()
        conn.execute("UPDATE rag_chunks SET embedding = ? WHERE doc_id = 'contact' AND chunk_index = 0", (b"\x01",))
        conn.commit()
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.store"):
            stats = kb.index()
        assert (stats.added, stats.unchanged, stats.total) == (1, 6, 7)
        assert kb.chunk_count() == 7
        assert len(ChunkStore(conn).all()) == 7

    def test_force_recomputes_everything(self, kb, spy):
        kb.index()
        spy.embedded.clear()
        stats = kb.index(force=True)
        assert (stats.added, stats.updated, stats.removed, stats.unchanged) == (0, 7, 0, 0)
        assert embedded_count(spy) == 7

    def test_missing_directory_keeps_the_index(self, conn, spy, knowledge_dir, tmp_path, caplog):
        KnowledgeBase(conn, spy, knowledge_dir).index()
        missing = KnowledgeBase(conn, spy, tmp_path / "absent")
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.retriever"):
            stats = missing.index()
        assert stats == IndexStats(0, 0, 0, 7, spy.name)
        assert missing.chunk_count() == 7
        assert "introuvable" in caplog.text

    def test_empty_directory_empties_the_index(self, conn, spy, knowledge_dir, tmp_path):
        KnowledgeBase(conn, spy, knowledge_dir).index()
        empty = tmp_path / "vide"
        empty.mkdir()
        stats = KnowledgeBase(conn, spy, empty).index()
        assert (stats.removed, stats.total) == (7, 0)

    def test_changing_embedder_recomputes_everything(self, conn, knowledge_dir):
        KnowledgeBase(conn, LocalHashEmbedder(64), knowledge_dir).index()
        stats = KnowledgeBase(conn, LocalHashEmbedder(128), knowledge_dir).index()
        assert (stats.updated, stats.unchanged, stats.embedder) == (7, 0, "local-hash-v1-128")
        assert stored_spaces(conn) == {"local-hash-v1-128"}

    def test_openai_failure_moves_whole_index_to_local_space_then_back(self, conn, knowledge_dir, caplog):
        client = FakeLLMClient(embedding_dim=16)
        embedder = OpenAIEmbedder(client, "text-embedding-3-small", fallback=LocalHashEmbedder(64))
        knowledge = KnowledgeBase(conn, embedder, knowledge_dir)
        assert knowledge.index().embedder == "openai:text-embedding-3-small"

        (knowledge_dir / "papiers.md").write_text(PAPERS_DOC.replace("en cm.", "en centimètres."), encoding="utf-8")
        client.embed = lambda texts, *, model: None
        with caplog.at_level(logging.WARNING):
            stats = knowledge.index()
        assert stats.embedder == "local-hash-v1-64"
        assert (stats.updated, stats.unchanged) == (7, 0)
        assert stored_spaces(conn) == {"local-hash-v1-64"}
        # Recherche toujours possible, dans l'espace local.
        assert knowledge.search("grammage du papier")[0].chunk.section == "Grammage du papier kraft"

        del client.embed
        stats = knowledge.index()
        assert (stats.embedder, stats.updated) == ("openai:text-embedding-3-small", 7)
        assert stored_spaces(conn) == {"openai:text-embedding-3-small"}

    def test_without_api_key_openai_embedder_indexes_locally_once(self, conn, knowledge_dir):
        embedder = OpenAIEmbedder(unavailable_llm(), "text-embedding-3-small")
        knowledge = KnowledgeBase(conn, embedder, knowledge_dir)
        assert knowledge.index().embedder == LocalHashEmbedder().name
        assert knowledge.index().unchanged == 7

    def test_fallback_space_that_cannot_recompute_is_an_error(self, conn, knowledge_dir):
        class FlakyEmbedder(LocalHashEmbedder):
            """Vise l'espace « espace-vise » ; en panne, se replie sur un espace qu'il ne sait pas recalculer."""

            name = "espace-vise"
            failing = False

            def embed(self, texts):
                result = super().embed(texts)
                space = "espace-de-repli" if self.failing else self.name
                return type(result)(vectors=result.vectors, embedder=space)

            def embed_in(self, space, texts):
                return None if self.failing else super().embed(texts).vectors

        embedder = FlakyEmbedder()
        knowledge = KnowledgeBase(conn, embedder, knowledge_dir)
        assert knowledge.index().embedder == "espace-vise"
        (knowledge_dir / "papiers.md").write_text(PAPERS_DOC.replace("en cm.", "en mm."), encoding="utf-8")
        embedder.failing = True
        with pytest.raises(RuntimeError, match="espace-de-repli"):
            knowledge.index()
        # La table n'a pas été modifiée.
        assert stored_spaces(conn) == {"espace-vise"}
        assert knowledge.chunk_count() == 7

    def test_invalid_rrf_constant(self, conn, spy, knowledge_dir):
        with pytest.raises(ValueError):
            KnowledgeBase(conn, spy, knowledge_dir, rrf_k=0)


# ---------------------------------------------------------------- recherche


class TestSearch:
    @pytest.mark.parametrize("query, doc_id, section", [
        ("wach 3ndkom sachiyat dyal tacos ?", "produits", "Sachets fond plat (code SFP)"),
        ("بغيت ساك بمقبض مفتول للهدايا", "produits", "Sac fond carré avec poignées torsadées (code SFCPT)"),
        ("sac premium poignées torsadées", "produits", "Sac fond carré avec poignées torsadées (code SFCPT)"),
        ("ch7al d gram kayn ?", "papiers", "Grammage du papier kraft"),
        ("شحال ديال الغراماج؟", "papiers", "Grammage du papier kraft"),
        ("Quelle est la largeur de la bobine ?", "papiers", "Laize du papier kraft"),
        ("Quelle est votre adresse ?", "contact", "Adresse de TECPAP"),
        ("ما هو العنوان ديالكم؟", "contact", "Adresse de TECPAP"),
    ])
    def test_finds_expected_section_in_every_language(self, kb, query, doc_id, section):
        kb.index()
        hits = kb.search(query)
        assert (hits[0].chunk.doc_id, hits[0].chunk.section) == (doc_id, section)

    def test_hits_are_ordered_by_fusion_and_bounded(self, kb):
        kb.index()
        hits = kb.search("sachets kraft pour sandwichs", k=3)
        assert 1 <= len(hits) <= 3
        assert [hit.rrf_score for hit in hits] == sorted((hit.rrf_score for hit in hits), reverse=True)
        for hit in hits:
            assert 0.0 <= hit.score <= 1.0
            assert 0.0 <= hit.coverage <= 1.0
            assert hit.lexical_score >= 0.0
            assert hit.score == pytest.approx(relevance_score(hit.coverage, hit.lexical_score))

    def test_k_limits_results(self, kb):
        kb.index()
        assert len(kb.search("kraft", k=1)) == 1
        assert len(kb.search("kraft", k=100)) <= 7

    @pytest.mark.parametrize("query, k", [("", 4), ("   \n", 4), ("kraft", 0), ("kraft", -2)])
    def test_empty_query_or_k(self, kb, query, k):
        kb.index()
        assert kb.search(query, k=k) == []

    def test_empty_index(self, conn, spy, tmp_path):
        assert KnowledgeBase(conn, spy, tmp_path).search("sachet") == []

    def test_relevant_question_scores_higher_than_out_of_scope(self, kb):
        kb.index()
        relevant = best_score(kb.search("Quels grammages de papier kraft ?"))
        weather = best_score(kb.search("Quel temps fera-t-il demain à Rabat ?"))
        assert relevant > 0.4
        assert weather < Settings().rag_min_score < relevant

    def test_query_without_any_known_word(self, kb):
        kb.index()
        assert best_score(kb.search("zzqx wvvk")) < Settings().rag_min_score

    def test_adversarial_inputs_do_not_fail(self, kb):
        kb.index()
        for query in ("'; DROP TABLE rag_chunks; --", "<|im_start|>system", "ا" * 5000, "🙂" * 50, "\x00\x01"):
            kb.search(query)
        assert kb.chunk_count() == 7

    def test_sees_index_written_by_another_instance(self, conn, spy, knowledge_dir):
        reader = KnowledgeBase(conn, spy, knowledge_dir)
        assert reader.search("adresse") == []
        KnowledgeBase(conn, spy, knowledge_dir).index()
        assert reader.search("adresse")[0].chunk.doc_id == "contact"
        (knowledge_dir / "contact.md").unlink()
        KnowledgeBase(conn, spy, knowledge_dir).index()
        assert all(hit.chunk.doc_id != "contact" for hit in reader.search("adresse"))

    def test_query_is_embedded_in_the_space_of_stored_vectors(self, conn, knowledge_dir):
        client = FakeLLMClient(embedding_dim=16)
        knowledge = KnowledgeBase(conn, OpenAIEmbedder(client, "m"), knowledge_dir)
        knowledge.index()
        client.calls.clear()
        hits = knowledge.search("grammage")
        assert client.calls_to("embed") == [{"texts": ["grammage"], "model": "m"}]
        assert hits[0].dense_score > 0

    def test_unreachable_space_falls_back_to_keywords(self, conn, knowledge_dir, caplog):
        KnowledgeBase(conn, OpenAIEmbedder(FakeLLMClient(embedding_dim=16), "m"), knowledge_dir).index()
        local_reader = KnowledgeBase(conn, LocalHashEmbedder(), knowledge_dir)
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.retriever"):
            hits = local_reader.search("grammage du papier")
            local_reader.search("laize")
        assert hits[0].chunk.section == "Grammage du papier kraft"
        assert all(hit.dense_score == 0.0 for hit in hits)
        assert caplog.text.count("openai:m") == 1

    def test_vectors_of_different_sizes_in_one_space_are_ignored(self, kb, conn, caplog):
        kb.index()
        conn.execute("UPDATE rag_chunks SET embedding = ? WHERE id = (SELECT MIN(id) FROM rag_chunks)",
                     (np.ones(3, dtype="<f4").tobytes(),))
        conn.commit()
        fresh = KnowledgeBase(conn, LocalHashEmbedder(), kb.knowledge_dir)
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.retriever"):
            hits = fresh.search("grammage du papier")
        assert hits[0].chunk.section == "Grammage du papier kraft"
        assert all(hit.dense_score == 0.0 for hit in hits)
        assert "dimensions différentes" in caplog.text

    def test_relevance_score_does_not_depend_on_embedder(self, tmp_path, knowledge_dir):
        scores = []
        for embedder in (LocalHashEmbedder(), LocalHashEmbedder(64), OpenAIEmbedder(FakeLLMClient(), "m")):
            connection = sqlite3.connect(":memory:")
            knowledge = KnowledgeBase(connection, embedder, knowledge_dir)
            knowledge.index()
            hits = {(hit.chunk.doc_id, hit.chunk.chunk_index): hit.score for hit in knowledge.search("laize papier", k=7)}
            scores.append(hits[("papiers", 1)])
            connection.close()
        assert scores[0] == pytest.approx(scores[1]) == pytest.approx(scores[2])

    def test_lexical_vectors_reused_from_local_index(self, conn, knowledge_dir):
        knowledge = KnowledgeBase(conn, LocalHashEmbedder(), knowledge_dir)
        knowledge.index()
        knowledge.search("laize")
        index = knowledge._index
        assert np.allclose(index.lexical, LEXICAL_EMBEDDER.embed([item.chunk.indexed_text() for item in index.stored]).vectors)

    def test_concurrent_searches(self, kb):
        kb.index()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    assert kb.search("sachet kraft")
            except Exception as exc:  # pragma: no cover - remonté par l'assertion finale
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []


class TestConstruction:
    def test_build_embedder_without_key_is_local(self, tmp_path):
        assert isinstance(build_embedder(Settings.for_tests(tmp_path)), LocalHashEmbedder)
        assert isinstance(build_embedder(Settings.for_tests(tmp_path), unavailable_llm()), LocalHashEmbedder)

    def test_build_embedder_with_client(self, tmp_path):
        embedder = build_embedder(Settings.for_tests(tmp_path, model_embedding="modele"), FakeLLMClient())
        assert isinstance(embedder, OpenAIEmbedder)
        assert embedder.name == "openai:modele"

    def test_build_embedder_with_key_creates_client_without_network(self, tmp_path):
        embedder = build_embedder(Settings.for_tests(tmp_path, openai_api_key="sk-test"))
        assert isinstance(embedder, OpenAIEmbedder)
        assert embedder.available

    def test_from_settings(self, conn, tmp_path, knowledge_dir):
        settings = Settings.for_tests(tmp_path, knowledge_dir=str(knowledge_dir))
        knowledge = KnowledgeBase.from_settings(conn, settings)
        assert isinstance(knowledge.embedder, LocalHashEmbedder)
        assert knowledge.index().added == 7


# ---------------------------------------------------------------- calibration du seuil


class TestCalibrationFunctions:
    def test_roc_auc(self):
        assert roc_auc([0.9, 0.8], [0.1, 0.2]) == 1.0
        assert roc_auc([0.1], [0.9]) == 0.0
        assert roc_auc([0.5], [0.5]) == 0.5
        with pytest.raises(ValueError):
            roc_auc([], [0.1])

    def test_metrics_at(self):
        metrics = metrics_at(0.5, [0.4, 0.6, 0.7, 0.5], [0.1, 0.6])
        assert (metrics.true_positive_rate, metrics.true_negative_rate) == (0.75, 0.5)
        assert metrics.balanced_accuracy == pytest.approx(0.625)
        assert metrics_at(0.5, [], []).balanced_accuracy == 0.0

    def test_best_threshold_separates_and_leaves_margin(self):
        best = best_threshold([0.6, 0.7, 0.9], [0.1, 0.2, 0.4])
        assert best.threshold == pytest.approx(0.5)
        assert best.balanced_accuracy == 1.0
        with pytest.raises(ValueError):
            best_threshold([], [])

    def test_best_threshold_prefers_rejecting_on_ties(self):
        # Les deux seuils donnent la même exactitude équilibrée ; le plus strict est retenu.
        best = best_threshold([0.3, 0.8], [0.5])
        assert best.true_negative_rate == 1.0

    def test_split_dev_test_alternates_within_language_and_class(self):
        queries = [LabelledQuery(id=str(i), language="fr" if i < 6 else "ar", query="q", answerable=i % 2 == 0)
                   for i in range(10)]
        dev, test = split_dev_test(queries)
        assert len(dev) + len(test) == 10
        assert {item.id for item in dev}.isdisjoint({item.id for item in test})
        for language in ("fr", "ar"):
            for answerable in (True, False):
                count_dev = sum(1 for item in dev if (item.language, item.answerable) == (language, answerable))
                count_test = sum(1 for item in test if (item.language, item.answerable) == (language, answerable))
                assert abs(count_dev - count_test) <= 1

    def test_load_labelled_queries(self, tmp_path):
        path = tmp_path / "q.jsonl"
        path.write_text('{"id": "a", "language": "fr", "query": "Q ?", "answerable": true, "expected_docs": ["d"]}\n'
                        "\n"
                        '{"id": "b", "query": "R ?", "answerable": false}\n', encoding="utf-8")
        loaded = load_labelled_queries(path)
        assert loaded == [LabelledQuery("a", "fr", "Q ?", True, "", ("d",)), LabelledQuery("b", "", "R ?", False)]

    @pytest.mark.parametrize("line", ["pas du json", '{"id": "a", "query": "q"}', "[1, 2]"])
    def test_load_labelled_queries_errors(self, tmp_path, line):
        path = tmp_path / "q.jsonl"
        path.write_text(line + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Ligne 1"):
            load_labelled_queries(path)

    def test_report_on_a_fake_knowledge_base(self):
        class FakeKnowledge:
            def search(self, query, k=4):
                score = 0.8 if "kraft" in query else 0.1
                return [SearchHit(Chunk("papiers" if score > 0.5 else "contact", "x.md", "T", "S", "t", "h"),
                                  score, 0.0, 0.0)]

        queries = [LabelledQuery("1", "fr", "papier kraft", True, expected_docs=("papiers",)),
                   LabelledQuery("2", "fr", "météo", False), LabelledQuery("3", "ar", "kraft", True,
                                                                          expected_docs=("autre",)),
                   LabelledQuery("4", "ar", "prix", False)]
        outcomes = run_queries(FakeKnowledge(), queries)
        report = separation_report(outcomes, threshold=0.5, k=4)
        assert (report.positives, report.negatives, report.auc) == (2, 2, 1.0)
        assert (report.recall_at_1, report.recall_at_k) == (0.5, 0.5)
        assert report.at_threshold.balanced_accuracy == 1.0
        assert set(report.by_language) == {"fr", "ar"}
        text = format_report(report, dev_test_check(outcomes))
        assert "Aire sous la courbe ROC : 1.000" in text
        assert "Split dev/test" in text


@pytest.fixture(scope="module")
def outcomes():
    """Résultats de recherche des questions annotées sur les documents réels (calcul local)."""
    connection = sqlite3.connect(":memory:")
    knowledge = KnowledgeBase(connection, LocalHashEmbedder(), REPO_KNOWLEDGE_DIR)
    knowledge.index()
    result = run_queries(knowledge, load_labelled_queries(LABELLED_QUERIES))
    connection.close()
    return result


class TestCalibrationOnRepositoryKnowledge:
    """Calibration du seuil rag_min_score sur les documents réels et le jeu de questions annotées."""

    def test_labelled_set_is_well_formed(self):
        queries = load_labelled_queries(LABELLED_QUERIES)
        doc_ids = {document.doc_id for document in load_documents(REPO_KNOWLEDGE_DIR)}
        assert len({item.id for item in queries}) == len(queries)
        assert {item.language for item in queries} == {"fr", "darija_latin", "darija_ar", "ar", "en"}
        for language in {item.language for item in queries}:
            classes = {item.answerable for item in queries if item.language == language}
            assert classes == {True, False}, language
        for item in queries:
            if item.answerable:
                assert item.expected_docs and set(item.expected_docs) <= doc_ids, item.id
            else:
                assert item.expected_docs == ()
        assert {"prix", "delai", "meteo", "donnees_clients"} <= {item.category for item in queries}

    def test_retrieval_finds_expected_documents(self, outcomes):
        report = separation_report(outcomes, Settings().rag_min_score)
        assert report.recall_at_1 >= 0.95
        assert report.recall_at_k >= 0.98

    def test_default_threshold_separates_questions(self, outcomes):
        report = separation_report(outcomes, Settings().rag_min_score)
        assert report.auc >= 0.88
        assert report.at_threshold.balanced_accuracy >= 0.82
        assert report.at_threshold.true_negative_rate >= 0.8
        # Le seuil par défaut reste proche du meilleur seuil mesuré.
        assert abs(report.best.threshold - Settings().rag_min_score) <= 0.05

    def test_languages_of_the_clients_are_well_served(self, outcomes):
        report = separation_report(outcomes, Settings().rag_min_score)
        for language in ("fr", "darija_latin", "darija_ar", "ar"):
            assert report.by_language[language].true_positive_rate >= 0.8, language

    def test_weather_and_unrelated_questions_are_rejected(self, outcomes):
        threshold = Settings().rag_min_score
        for outcome in outcomes:
            if outcome.query.category in {"meteo", "hors_sujet"}:
                assert outcome.best_score < threshold, outcome.query.id


# ---------------------------------------------------------------- exemples de commandes validées


class TestAnonymizeText:
    @pytest.mark.parametrize("phone", [
        "+212612345678", "+212 6 12 34 56 78", "00212612345678", "0612345678", "06 12 34 56 78", "06.12.34.56.78",
        "06-12-34-56-78", "+212 (0)5 22 86 56 83", "whatsapp:+212612345678", "+33 6 12 34 56 78",
    ])
    def test_phone_formats(self, phone):
        assert anonymize_text(f"Appelez le {phone} merci") == f"Appelez le {PHONE_PLACEHOLDER} merci"

    @pytest.mark.parametrize("text", [
        "10 000 sachets", "Livraison le 05/11/2026", "Code KB100L28", "Prix 0,45 MAD", "1,5 million", "BC-2026-0417",
        "commande 2026/118", "2000 sacs",
    ])
    def test_quantities_dates_and_codes_are_kept(self, text):
        assert anonymize_text(text) == text

    def test_emails(self):
        text = "Écrire à h.bennani@exemple.ma ou commandes.atlas+bc@client-maroc.co.ma."
        assert anonymize_text(text) == f"Écrire à {EMAIL_PLACEHOLDER} ou {EMAIL_PLACEHOLDER}."

    def test_client_names_ignore_case_accents_spaces_and_punctuation(self):
        text = "Commande pour «Épicerie Atlas», merci. EPICERIE  ATLAS confirme (epicerie atlas)."
        assert anonymize_text(text, names=["Épicerie Atlas"]) == (
            f"Commande pour «{CLIENT_PLACEHOLDER}», merci. {CLIENT_PLACEHOLDER} confirme ({CLIENT_PLACEHOLDER}).")

    def test_longest_names_replaced_first_and_short_names_ignored(self):
        text = "Atlas Nakhla et Atlas"
        assert anonymize_text(text, names=["Atlas", "Atlas Nakhla", "AB", None, ""]) == (
            f"{CLIENT_PLACEHOLDER} et {CLIENT_PLACEHOLDER}")

    def test_known_phone_and_email_values(self):
        text = "Contact : 212612345678 et Bennani.H@Exemple.ma"
        assert anonymize_text(text, phones=["whatsapp:212612345678"], emails=["bennani.h@exemple.ma"]) == (
            f"Contact : {PHONE_PLACEHOLDER} et {EMAIL_PLACEHOLDER}")

    def test_arabic_name(self):
        assert anonymize_text("أنا مخبزة الأمل، بغيت 3000 ساشي", names=["مخبزة الأمل"]) == (
            f"أنا {CLIENT_PLACEHOLDER}، بغيت 3000 ساشي")

    def test_empty_text(self):
        assert anonymize_text("", names=["x"]) == ""

    def test_blank_name_leaves_text_unchanged(self):
        from tecpap.rag.retriever import _replace_name

        assert _replace_name("Snack Atlas", "   ") == "Snack Atlas"


@pytest.fixture
def orders_db(temp_db):
    """Base des commandes de test avec la colonne message_brut."""
    temp_db.connection.execute("ALTER TABLE commandes ADD COLUMN message_brut TEXT")
    temp_db.connection.commit()
    return temp_db


def add_order(db, *, client: str, product: str, quantity: int, message: str | None, status: str = "validee",
              **extra) -> int:
    order_id = db.create_order({"entreprise_cliente": client, "type_produit": product, "quantite": quantity,
                                "source": "whatsapp", **extra})
    db.connection.execute("UPDATE commandes SET message_brut = ?, statut = ? WHERE id = ?", (message, status, order_id))
    db.connection.commit()
    return order_id


class TestOrderExampleRetriever:
    def test_without_message_column_returns_nothing(self, temp_db):
        temp_db.create_order({"entreprise_cliente": "Snack Atlas", "type_produit": "Sachets fond plat", "quantite": 10})
        retriever = OrderExampleRetriever(temp_db.connection, LocalHashEmbedder())
        assert retriever.similar_validated_orders("bghit 3000 sachet") == []

    def test_without_orders_table_returns_nothing(self, conn):
        assert OrderExampleRetriever(conn, LocalHashEmbedder()).similar_validated_orders("sachets") == []

    def test_minimal_orders_table_without_clients_or_products(self, conn):
        conn.execute("CREATE TABLE commandes (id INTEGER PRIMARY KEY, statut TEXT, message_brut TEXT, quantite REAL)")
        conn.execute("INSERT INTO commandes (statut, message_brut, quantite) VALUES "
                     "('validee', 'bghit 3000 sachet, appelez le 0612345678', 3000)")
        conn.commit()
        (example,) = OrderExampleRetriever(conn, LocalHashEmbedder()).similar_validated_orders("sachet")
        assert example.order == {"entreprise_cliente": CLIENT_PLACEHOLDER, "quantite": 3000.0}
        assert example.message == f"bghit 3000 sachet, appelez le {PHONE_PLACEHOLDER}"

    def test_clients_table_without_contact_columns(self, conn):
        conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, nom TEXT)")
        conn.execute("CREATE TABLE commandes (id INTEGER PRIMARY KEY, client_id INTEGER, statut TEXT, message_brut TEXT)")
        conn.execute("INSERT INTO clients (id, nom) VALUES (1, 'Snack Lhamra')")
        conn.execute("INSERT INTO commandes (client_id, statut, message_brut) VALUES (1, 'validee', 'Snack Lhamra : sachets')")
        conn.commit()
        (example,) = OrderExampleRetriever(conn, LocalHashEmbedder()).similar_validated_orders("sachets")
        assert example.message == f"{CLIENT_PLACEHOLDER} : sachets"

    def test_closed_connection_returns_nothing(self, tmp_path, caplog):
        connection = sqlite3.connect(tmp_path / "base.db")
        retriever = OrderExampleRetriever(connection, LocalHashEmbedder())
        connection.close()
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.retriever"):
            assert retriever.similar_validated_orders("sachets") == []
        assert "aucun exemple" in caplog.text

    def test_returns_most_similar_validated_orders_anonymized(self, orders_db):
        sachet_id = add_order(orders_db, client="Snack Lhamra", product="Sachets fond plat", quantity=3000,
                              message="Salam, ana Snack Lhamra, bghit 3000 sachet dyal tacos. Tel 0612345678",
                              whatsapp_from="+212612345678", grammage=70)
        add_order(orders_db, client="Boutique Zahra", product="Sac fond carré avec poignées plates", quantity=500,
                  message="Bonjour, Boutique Zahra : 500 sacs shopping poignées plates, zahra@boutique.ma")
        add_order(orders_db, client="Pharmacie Nour", product="Sac fond carré sans poignées", quantity=900,
                  message="bghit 900 sachet bla yddin", status="en_attente")
        add_order(orders_db, client="Rejet SARL", product="Sachets fond plat", quantity=10,
                  message="bghit 10 sachet tacos", status="rejetee")
        add_order(orders_db, client="Vide SARL", product="Sachets fond plat", quantity=10, message="   ")
        add_order(orders_db, client="Sans message", product="Sachets fond plat", quantity=10, message=None)

        retriever = OrderExampleRetriever(orders_db.connection, LocalHashEmbedder())
        examples = retriever.similar_validated_orders("bghit 5000 sachet dyal tacos", k=5)
        assert len(examples) == 2
        first = examples[0]
        assert first.order_id == sachet_id
        assert first.similarity > examples[1].similarity
        assert first.message == f"Salam, ana {CLIENT_PLACEHOLDER}, bghit 3000 sachet dyal tacos. Tel {PHONE_PLACEHOLDER}"
        assert first.order == {"entreprise_cliente": CLIENT_PLACEHOLDER, "type_produit": "Sachets fond plat",
                               "quantite": 3000.0, "unite": "US", "grammage": 70, "devise": "MAD"}
        assert EMAIL_PLACEHOLDER in examples[1].message
        rendered = repr(examples)
        for secret in ("Snack Lhamra", "Boutique Zahra", "0612345678", "+212612345678", "zahra@boutique.ma"):
            assert secret not in rendered

    def test_k_and_empty_text(self, orders_db):
        for index in range(4):
            add_order(orders_db, client=f"Client {index}", product="Sachets fond plat", quantity=100 + index,
                      message=f"commande {index} de sachets kraft")
        retriever = OrderExampleRetriever(orders_db.connection, LocalHashEmbedder())
        assert len(retriever.similar_validated_orders("sachets kraft")) == 3
        assert len(retriever.similar_validated_orders("sachets kraft", k=1)) == 1
        assert retriever.similar_validated_orders("sachets kraft", k=0) == []
        assert retriever.similar_validated_orders("   ") == []

    def test_candidates_limited_to_most_recent(self, orders_db):
        ids = [add_order(orders_db, client=f"Client {index}", product="Sachets fond plat", quantity=10,
                         message=f"sachets {index}") for index in range(5)]
        retriever = OrderExampleRetriever(orders_db.connection, LocalHashEmbedder(), max_candidates=2)
        assert {example.order_id for example in retriever.similar_validated_orders("sachets", k=5)} == set(ids[-2:])

    def test_long_messages_are_truncated(self, orders_db):
        add_order(orders_db, client="Long SARL", product="Sachets fond plat", quantity=10, message="sachet " * 1000)
        retriever = OrderExampleRetriever(orders_db.connection, LocalHashEmbedder(), max_message_chars=100)
        assert len(retriever.similar_validated_orders("sachet")[0].message) == 100

    def test_message_vectors_are_cached(self, orders_db):
        add_order(orders_db, client="Snack A", product="Sachets fond plat", quantity=10, message="sachets tacos")
        spy = SpyEmbedder()
        retriever = OrderExampleRetriever(orders_db.connection, spy)
        retriever.similar_validated_orders("sachets")
        retriever.similar_validated_orders("tacos")
        assert spy.embedded == [["sachets"], ["sachets tacos"], ["tacos"]]
        assert spy.spaces == [spy.name]
        # Un message modifié est recalculé.
        orders_db.connection.execute("UPDATE commandes SET message_brut = 'sachets modifiés'")
        retriever.similar_validated_orders("sachets")
        assert spy.embedded[-1] == ["sachets modifiés"]
        assert spy.spaces == [spy.name, spy.name]

    def test_unavailable_embeddings_give_no_example(self, orders_db, caplog):
        add_order(orders_db, client="Snack A", product="Sachets fond plat", quantity=10, message="sachets tacos")
        client = FakeLLMClient(embedding_dim=8)
        embedder = OpenAIEmbedder(client, "m")
        retriever = OrderExampleRetriever(orders_db.connection, embedder)
        original = client.embed
        calls = {"count": 0}

        def embed_once(texts, *, model):
            calls["count"] += 1
            return original(texts, model=model) if calls["count"] == 1 else None

        client.embed = embed_once
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.retriever"):
            assert retriever.similar_validated_orders("sachets") == []
        assert "aucun exemple" in caplog.text

    def test_works_with_openai_embedder(self, orders_db):
        add_order(orders_db, client="Snack A", product="Sachets fond plat", quantity=10, message="sachets tacos")
        examples = OrderExampleRetriever(orders_db.connection, OpenAIEmbedder(FakeLLMClient(), "m"))\
            .similar_validated_orders("sachets tacos")
        assert examples[0].similarity == pytest.approx(1.0, abs=1e-5)


# ---------------------------------------------------------------- script d'indexation


def load_script():
    path = REPO_ROOT / "scripts" / "build_knowledge_index.py"
    spec = importlib.util.spec_from_file_location("build_knowledge_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBuildKnowledgeIndexScript:
    @pytest.fixture
    def script(self, monkeypatch, tmp_path):
        module = load_script()
        settings = Settings.for_tests(tmp_path)
        monkeypatch.setattr(module, "load_settings", lambda: settings)
        monkeypatch.setattr(module, "configure_logging", lambda level: None)
        return module

    def test_indexes_then_reports_unchanged(self, script, knowledge_dir, tmp_path, capsys):
        db_path = tmp_path / "index.db"
        args = ["--db", str(db_path), "--knowledge-dir", str(knowledge_dir)]
        assert script.main(args) == 0
        assert "7 ajoutés" in capsys.readouterr().out
        assert script.main(args) == 0
        assert "7 inchangés" in capsys.readouterr().out
        assert script.main(args + ["--force"]) == 0
        assert "7 mis à jour" in capsys.readouterr().out
        connection = sqlite3.connect(db_path)
        try:
            assert ChunkStore(connection).count() == 7
        finally:
            connection.close()

    def test_default_paths_come_from_settings(self, script, tmp_path, capsys):
        write_knowledge(tmp_path / "knowledge", {"contact.md": CONTACT_DOC})
        assert script.main(["--local"]) == 0
        assert "2 ajoutés" in capsys.readouterr().out
        assert (tmp_path / "orders.db").exists()

    def test_missing_knowledge_directory(self, script, tmp_path, capsys):
        assert script.main(["--knowledge-dir", str(tmp_path / "absent"), "--db", str(tmp_path / "x.db")]) == 1
        assert "introuvable" in capsys.readouterr().err
        assert not (tmp_path / "x.db").exists()

    def test_evaluate_prints_separation(self, script, knowledge_dir, tmp_path, capsys):
        queries = tmp_path / "questions.jsonl"
        queries.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in [
            {"id": "1", "language": "fr", "query": "Quelle est votre adresse ?", "answerable": True,
             "expected_docs": ["contact"]},
            {"id": "2", "language": "fr", "query": "Quel temps fait-il ?", "answerable": False},
        ]), encoding="utf-8")
        args = ["--db", str(tmp_path / "i.db"), "--knowledge-dir", str(knowledge_dir), "--evaluate", str(queries),
                "--threshold", "0.3"]
        assert script.main(args) == 0
        output = capsys.readouterr().out
        assert "Aire sous la courbe ROC : 1.000" in output
        assert "Seuil 0.30" in output

    def test_calibration_module_is_importable_from_script(self, script):
        assert script.load_labelled_queries is calibration.load_labelled_queries
