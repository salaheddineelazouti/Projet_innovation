"""Services de l'application : objets liés au fil courant, base de connaissances, tâches de fond, configuration."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tecpap.chatbot.bot import WhatsAppAssistant
from tecpap.config import ConfigurationError, Settings, load_settings
from tecpap.rag.answer import KnowledgeAnswerer
from tecpap.rag.embeddings import LocalHashEmbedder, OpenAIEmbedder
from tecpap.web import create_app
from tecpap.web.worker import BackgroundWorker
from tests.fakes import FakeLLMClient

DOCUMENT = """---
title: Sachets fond plat
categorie: produits
---

## Usages

Les sachets fond plat servent aux sandwichs, aux tacos et aux viennoiseries.

## Papier

Ils sont fabriqués en papier kraft blanchi ou écru, de 40 à 60 g/m².
"""


@pytest.fixture
def knowledge_dir(tmp_path):
    directory = tmp_path / "knowledge"
    directory.mkdir()
    (directory / "sachets.md").write_text(DOCUMENT, encoding="utf-8")
    return directory


@pytest.fixture
def make_app(tmp_path):
    apps = []

    def build(llm=None, **overrides):
        app = create_app(Settings.for_tests(tmp_path, **overrides), llm=llm)
        apps.append(app)
        return app

    yield build
    for app in apps:
        app.extensions["tecpap"].close()


class TestKnowledgeBaseIndexing:
    def test_knowledge_base_is_indexed_at_startup(self, make_app, knowledge_dir):
        services = make_app(knowledge_dir=str(knowledge_dir)).extensions["tecpap"]
        knowledge = services.knowledge_base()
        assert knowledge.chunk_count() == 2
        assert isinstance(knowledge.embedder, LocalHashEmbedder)
        assert knowledge.search("sachets pour tacos")

    def test_indexing_is_idempotent(self, make_app, knowledge_dir):
        services = make_app(knowledge_dir=str(knowledge_dir)).extensions["tecpap"]
        stats = services.index_knowledge_base()
        assert (stats.added, stats.updated, stats.removed, stats.unchanged) == (0, 0, 0, 2)

    def test_embedder_change_reindexes(self, make_app, knowledge_dir):
        make_app(knowledge_dir=str(knowledge_dir)).extensions["tecpap"].close()
        services = make_app(FakeLLMClient(), knowledge_dir=str(knowledge_dir),
                            rag_embedder="openai").extensions["tecpap"]
        assert isinstance(services.embedder, OpenAIEmbedder)
        stats = services.index_knowledge_base()
        assert stats.embedder == services.embedder.name
        assert stats.unchanged == 2
        assert {chunk.embedder for chunk in services.knowledge_base().store.all()} == {services.embedder.name}

    def test_no_indexing_when_rag_is_disabled(self, make_app, knowledge_dir):
        services = make_app(knowledge_dir=str(knowledge_dir), rag_enabled=False).extensions["tecpap"]
        assert services.knowledge_base().chunk_count() == 0
        assert services.knowledge_answerer() is None

    def test_missing_directory_is_not_an_error(self, make_app, caplog):
        with caplog.at_level(logging.WARNING):
            services = make_app().extensions["tecpap"]
        assert services.index_knowledge_base() is None
        assert "introuvable" not in caplog.text


class TestThreadBoundObjects:
    def test_objects_use_the_connection_of_the_current_thread(self, make_app):
        services = make_app().extensions["tecpap"]
        main = services.db.connection
        assert services.conversation_store().conn is main
        assert services.knowledge_base() is services.knowledge_base()

        def objects():
            return (services.conversation_store().conn, services.knowledge_base().store.conn,
                    services.assistant().conn, services.db.connection)

        with ThreadPoolExecutor(max_workers=1) as pool:
            store, knowledge, assistant, connection = pool.submit(objects).result()
        assert store is knowledge is assistant is connection
        assert connection is not main

    def test_assistant_wiring(self, make_app):
        llm = FakeLLMClient()
        services = make_app(llm).extensions["tecpap"]
        assistant = services.assistant()
        assert isinstance(assistant, WhatsAppAssistant)
        assert (assistant.llm, assistant.router) == (llm, services.router)
        assert isinstance(assistant.knowledge, KnowledgeAnswerer)
        assert callable(assistant.extractor)

    def test_assistant_reopens_a_closed_database(self, make_app):
        services = make_app().extensions["tecpap"]
        services.db.close_all()
        assert services.conversation_store().conn is services.db.connection is not None

    def test_message_reservation(self, make_app):
        services = make_app().extensions["tecpap"]
        assert services.claim_message("SM1") is True
        assert services.claim_message("SM1") is False
        services.release_message("SM1")
        assert services.claim_message("SM1") is True
        assert services.claim_message(None) is True

    def test_sender_lock_is_shared_by_the_writings_of_a_number(self, make_app):
        services = make_app().extensions["tecpap"]
        assert services.sender_lock("whatsapp:+212612345678") is services.sender_lock("+212 6 12 34 56 78")
        assert services.sender_lock("") is services.sender_lock("")

    def test_close_closes_every_connection(self, make_app):
        services = make_app().extensions["tecpap"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: services.db.connection).result()
        services.close()
        assert services.db.open_connection_count() == 0
        assert services.worker.closed


class TestBackgroundWorker:
    def test_task_runs_in_another_thread(self):
        worker = BackgroundWorker()
        try:
            assert worker.submit(threading.current_thread).result(timeout=5) is not threading.current_thread()
        finally:
            worker.shutdown()

    def test_exception_is_logged_not_raised(self, caplog):
        worker = BackgroundWorker()

        def fail():
            raise RuntimeError("échec simulé")

        with caplog.at_level(logging.ERROR, logger="tecpap.web.worker"):
            assert worker.submit(fail).result(timeout=5) is None
        worker.shutdown()
        assert "Échec d'une tâche de fond" in caplog.text
        assert "échec simulé" in caplog.text

    def test_bounded_number_of_threads_and_pending_tasks(self):
        worker = BackgroundWorker(max_workers=2, max_pending=3)
        release = threading.Event()
        names: set[str] = set()

        def wait():
            names.add(threading.current_thread().name)
            release.wait(5)

        futures = [worker.submit(wait) for _ in range(3)]
        assert all(future is not None for future in futures)
        assert worker.submit(wait) is None
        release.set()
        for future in futures:
            future.result(timeout=5)
        assert len(names) <= 2
        assert worker.submit(wait) is not None
        worker.shutdown()

    def test_shutdown_refuses_new_tasks(self):
        worker = BackgroundWorker()
        future = worker.submit(time.sleep, 0.05)
        worker.shutdown(wait=True)
        assert future.done()
        assert worker.submit(time.sleep, 0) is None

    def test_invalid_sizes(self):
        with pytest.raises(ValueError):
            BackgroundWorker(max_workers=0)
        with pytest.raises(ValueError):
            BackgroundWorker(max_pending=0)


class TestEmbedderSetting:
    def test_default_is_local(self):
        assert load_settings({}).rag_embedder == "local"

    @pytest.mark.parametrize("raw, expected", [("openai", "openai"), ("OpenAI", "openai"), (" local ", "local")])
    def test_values(self, raw, expected):
        assert load_settings({"RAG_EMBEDDER": raw}).rag_embedder == expected

    def test_unknown_value_is_refused(self):
        with pytest.raises(ConfigurationError, match="RAG_EMBEDDER"):
            load_settings({"RAG_EMBEDDER": "maison"})
