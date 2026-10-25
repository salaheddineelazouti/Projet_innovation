"""Tests de la page « Base de connaissances » : documents indexés, état de l'index, réindexation et recherche de test.

Les plongements sont calculés localement (``RAG_EMBEDDER=local``, valeur de test) : aucun appel
réseau. La recherche de test n'appelle jamais le modèle de langue.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tecpap.config import Settings
from tecpap.web import create_app
from tecpap.web.routes import assistant
from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_PASSWORD_HASH, TEST_ADMIN_USERNAME
from tests.fakes import FakeLLMClient

PRODUCTS_DOCUMENT = """---
title: Nos sacs en papier kraft
categorie: produits
langue: fr
mots_cles:
  - sachet
  - sac kraft
  - poignées torsadées
---
# Nos sacs en papier kraft

## Sachets fond plat

Les sachets fond plat en papier kraft conviennent aux sandwichs, aux tacos et aux viennoiseries.

## Sac fond carré avec poignées torsadées

Le sac fond carré avec poignées torsadées est le modèle haut de gamme, apprécié des boutiques de vêtements.
"""

DELIVERY_DOCUMENT = """# Livraison

## Zones desservies

TECPAP livre Casablanca, Rabat et Marrakech avec ses propres camions.
"""

ANSWERED_QUESTION = "sachets fond plat pour sandwichs"
COMMERCIAL_QUESTION = "quel est le prix des sachets fond plat ?"
UNRELATED_QUESTION = "météo demain à Paris"


def write_documents(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "produits.md").write_text(PRODUCTS_DOCUMENT, encoding="utf-8")
    (directory / "livraison.md").write_text(DELIVERY_DOCUMENT, encoding="utf-8")
    (directory / "_guide_redaction.md").write_text("# Guide\n\n## Règles\n\nTexte réservé à l'équipe.\n",
                                                  encoding="utf-8")


def logged_in(app):
    """Client de test connecté ; ses requêtes portent l'en-tête X-CSRF-Token de la session."""
    client = app.test_client()
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/login", data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD,
                                           "csrf_token": token})
    assert response.status_code == 302
    with client.session_transaction() as session:
        client.environ_base["HTTP_X_CSRF_TOKEN"] = session["csrf_token"]
    return client


@pytest.fixture
def make_app(tmp_path):
    """Fabrique d'applications avec connexion et protection CSRF ; documents écrits avant le démarrage si demandé."""
    apps = []

    def build(*, documents: bool = True, llm=None, **overrides):
        settings = Settings.for_tests(tmp_path, auth_enabled=True, csrf_enabled=True,
                                      admin_username=TEST_ADMIN_USERNAME,
                                      admin_password_hash=TEST_ADMIN_PASSWORD_HASH, **overrides)
        if documents:
            write_documents(Path(settings.knowledge_dir))
        app = create_app(settings, llm=llm)
        apps.append(app)
        return app

    yield build
    for app in apps:
        app.extensions["tecpap"].close()


def _decision(html: str) -> str:
    return re.search(r'id="answerDecision" data-would-answer="(\d)"', html).group(1)


# ---------------------------------------------------------------------- documents et index

class TestKnowledgePage:
    """Documents, sections, calcul des plongements et état de l'index."""

    def test_login_is_required(self, make_app):
        response = make_app().test_client().get("/knowledge")
        assert response.status_code == 302
        assert "/login?next=" in response.headers["Location"]

    def test_documents_and_sections(self, make_app):
        client = logged_in(make_app())
        html = client.get("/knowledge").get_data(as_text=True)
        table = re.search(r'id="documentsTable".*?</table>', html, flags=re.DOTALL).group(0)
        assert "Nos sacs en papier kraft" in table and "produits.md" in table
        assert "Sachets fond plat" in table and "Sac fond carré avec poignées torsadées" in table
        assert "Livraison" in table and "Zones desservies" in table
        assert "_guide_redaction" not in table
        assert re.search(r'id="documentCount">2<', html)
        assert re.search(r'id="chunkCount">\d+<', html)

    def test_embedder_and_threshold(self, make_app):
        app = make_app()
        html = logged_in(app).get("/knowledge").get_data(as_text=True)
        services = app.extensions["tecpap"]
        assert f'id="embedderName">{services.embedder.name}<' in html
        assert "RAG_EMBEDDER=local" in html
        assert f'id="minScore">{services.settings.rag_min_score:.2f}<' in html
        assert f"{services.settings.rag_top_k} extraits au plus par question" in html

    def test_index_is_up_to_date_after_startup(self, make_app):
        html = logged_in(make_app()).get("/knowledge").get_data(as_text=True)
        assert 'data-up-to-date="1"' in html

    def test_modified_document_is_reported(self, make_app):
        app = make_app()
        knowledge_dir = Path(app.extensions["tecpap"].settings.knowledge_dir)
        (knowledge_dir / "livraison.md").write_text(DELIVERY_DOCUMENT + "\n## Délais\n\nVoir l'équipe.\n",
                                                    encoding="utf-8")
        (knowledge_dir / "produits.md").unlink()
        html = logged_in(app).get("/knowledge").get_data(as_text=True)
        status = re.search(r'id="indexStatus">.*?</ul>', html, flags=re.DOTALL).group(0)
        assert 'data-up-to-date="0"' in status
        assert "1 extrait(s) nouveau(x) ou modifié(s)" in status
        assert "2 extrait(s) supprimé(s) du dossier" in status

    def test_missing_directory(self, make_app):
        html = logged_in(make_app(documents=False)).get("/knowledge").get_data(as_text=True)
        assert "Aucun document indexé" in html
        assert "dossier de la base de connaissances introuvable" in html

    def test_disabled_rag_and_assistant_are_reported(self, make_app):
        html = logged_in(make_app(rag_enabled=False, chatbot_enabled=False)).get("/knowledge").get_data(as_text=True)
        assert 'id="ragDisabled"' in html
        assert 'id="chatbotDisabled"' in html

    def test_reindex_form_carries_the_session_token(self, make_app):
        client = logged_in(make_app())
        html = client.get("/knowledge").get_data(as_text=True)
        form = re.search(r'<form method="post"[^>]*id="reindexForm".*?</form>', html, flags=re.DOTALL).group(0)
        with client.session_transaction() as session:
            assert f'name="csrf_token" value="{session["csrf_token"]}"' in form


# ---------------------------------------------------------------------- réindexation

class TestReindex:
    """Bouton « Réindexer »."""

    def test_reindex_indexes_new_documents(self, make_app):
        app = make_app(documents=False)
        client = logged_in(app)
        write_documents(Path(app.extensions["tecpap"].settings.knowledge_dir))
        assert "Aucun document indexé" in client.get("/knowledge").get_data(as_text=True)

        response = client.post("/knowledge/reindex")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/knowledge")
        html = client.get("/knowledge").get_data(as_text=True)
        assert "Réindexation terminée" in html
        assert "Nos sacs en papier kraft" in html
        assert 'data-up-to-date="1"' in html
        last = re.search(r'id="lastIndex">.*?</div>', html, flags=re.DOTALL).group(0)
        assert "par admin" in last
        assert re.search(r"\d+ extraits indexés : [1-9]\d* ajouté\(s\), 0 mis à jour, 0 supprimé\(s\), 0 inchangé\(s\)", last)

    def test_second_reindex_changes_nothing(self, make_app):
        client = logged_in(make_app())
        client.post("/knowledge/reindex")
        last = re.search(r'id="lastIndex">.*?</div>', client.get("/knowledge").get_data(as_text=True),
                         flags=re.DOTALL).group(0)
        assert re.search(r"0 ajouté\(s\), 0 mis à jour, 0 supprimé\(s\), [1-9]\d* inchangé\(s\)", last)

    def test_force_recomputes_every_vector(self, make_app):
        client = logged_in(make_app())
        client.post("/knowledge/reindex", data={"force": "1"})
        last = re.search(r'id="lastIndex">.*?</div>', client.get("/knowledge").get_data(as_text=True),
                         flags=re.DOTALL).group(0)
        assert "vecteurs recalculés" in last
        assert re.search(r"0 ajouté\(s\), [1-9]\d* mis à jour, 0 supprimé\(s\), 0 inchangé\(s\)", last)

    def test_reindex_without_token_is_refused(self, make_app):
        app = make_app(documents=False)
        client = logged_in(app)
        client.environ_base.pop("HTTP_X_CSRF_TOKEN")
        write_documents(Path(app.extensions["tecpap"].settings.knowledge_dir))
        assert client.post("/knowledge/reindex").status_code == 403
        assert app.extensions["tecpap"].knowledge_base().chunk_count() == 0

    def test_reindex_with_the_form_token(self, make_app):
        app = make_app(documents=False)
        client = logged_in(app)
        client.environ_base.pop("HTTP_X_CSRF_TOKEN")
        write_documents(Path(app.extensions["tecpap"].settings.knowledge_dir))
        html = client.get("/knowledge").get_data(as_text=True)
        token = re.search(r'id="reindexForm".*?name="csrf_token" value="([^"]+)"', html, flags=re.DOTALL).group(1)
        assert client.post("/knowledge/reindex", data={"csrf_token": token}).status_code == 302
        assert app.extensions["tecpap"].knowledge_base().chunk_count() > 0

    def test_missing_directory(self, make_app):
        client = logged_in(make_app(documents=False))
        response = client.post("/knowledge/reindex", follow_redirects=True)
        assert "le dossier de la base de connaissances est introuvable" in response.get_data(as_text=True)

    def test_failure_is_reported(self, make_app, monkeypatch):
        app = make_app()
        monkeypatch.setattr(app.extensions["tecpap"], "index_knowledge_base", lambda force=False: None)
        response = logged_in(app).post("/knowledge/reindex", follow_redirects=True)
        html = response.get_data(as_text=True)
        assert "La réindexation a échoué" in html
        assert "Aucune depuis le démarrage du serveur" in html

    def test_get_is_not_allowed(self, make_app):
        assert logged_in(make_app()).get("/knowledge/reindex").status_code == 405


# ---------------------------------------------------------------------- recherche de test

class TestSearch:
    """Recherche de test : extraits, composantes du score, seuil et décision de l'assistant."""

    def test_hits_with_score_components(self, make_app):
        app = make_app()
        html = logged_in(app).get("/knowledge", query_string={"q": ANSWERED_QUESTION}).get_data(as_text=True)
        table = re.search(r'id="searchResults".*?</table>', html, flags=re.DOTALL).group(0)
        rows = re.findall(r"<tr>\s*<td class=\"text-slate-500\">\d+</td>.*?</tr>", table, flags=re.DOTALL)
        assert rows
        assert "Sachets fond plat" in rows[0]
        assert len(re.findall(r"\d\.\d{3}", rows[0])) >= 4
        min_score = app.extensions["tecpap"].settings.rag_min_score
        assert f"seuil : <strong>{min_score:.2f}</strong>" in html

    def test_answered_question(self, make_app):
        html = logged_in(make_app()).get("/knowledge", query_string={"q": ANSWERED_QUESTION}).get_data(as_text=True)
        assert _decision(html) == "1"
        assert "L'assistant répondrait" in html
        assert 'id="answerPreview"' in html
        assert 'title="Atteint le seuil"' in html

    def test_commercial_question_is_forwarded(self, make_app):
        html = logged_in(make_app()).get("/knowledge", query_string={"q": COMMERCIAL_QUESTION}).get_data(as_text=True)
        assert _decision(html) == "0"
        assert "Question commerciale" in html

    def test_unrelated_question_is_not_answered(self, make_app):
        html = logged_in(make_app()).get("/knowledge", query_string={"q": UNRELATED_QUESTION}).get_data(as_text=True)
        assert _decision(html) == "0"
        assert "Aucun extrait n&#39;atteint le seuil" in html
        assert 'title="Atteint le seuil"' not in html

    def test_disabled_rag_never_answers(self, make_app):
        html = logged_in(make_app(rag_enabled=False)).get(
            "/knowledge", query_string={"q": ANSWERED_QUESTION}).get_data(as_text=True)
        assert _decision(html) == "0"
        assert "la base n'est pas consultée" in html

    def test_language_of_the_answer(self, make_app):
        html = logged_in(make_app()).get("/knowledge", query_string={"q": "wach 3andkom sachet fond plat ?"}).get_data(
            as_text=True)
        assert "langue de réponse : Darija" in html

    def test_query_is_escaped(self, make_app):
        html = logged_in(make_app()).get(
            "/knowledge", query_string={"q": "<script>alert('x')</script> sachet"}).get_data(as_text=True)
        assert "<script>alert('x')" not in html
        assert 'value="&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt; sachet"' in html

    def test_no_model_call(self, make_app):
        llm = FakeLLMClient(chat=["Réponse inventée"])
        html = logged_in(make_app(llm=llm)).get("/knowledge", query_string={"q": ANSWERED_QUESTION}).get_data(
            as_text=True)
        assert _decision(html) == "1"
        assert llm.calls == []
        assert "Réponse inventée" not in html

    def test_search_failure(self, make_app, monkeypatch):
        app = make_app()
        client = logged_in(app)

        def failing_search(self, query, k=4):
            raise RuntimeError("index illisible")

        monkeypatch.setattr("tecpap.rag.retriever.KnowledgeBase.search", failing_search)
        html = client.get("/knowledge", query_string={"q": ANSWERED_QUESTION}).get_data(as_text=True)
        assert _decision(html) == "0"
        assert "La recherche a échoué" in html
        assert "index illisible" not in html

    def test_without_query_there_is_no_result_block(self, make_app):
        html = logged_in(make_app()).get("/knowledge").get_data(as_text=True)
        assert 'id="answerDecision"' not in html
        assert 'id="searchResults"' not in html

    def test_long_query_is_truncated(self, make_app):
        query = "sachet " * 400
        response = logged_in(make_app()).get("/knowledge", query_string={"q": query})
        assert response.status_code == 200
        value = re.search(r'name="q" value="([^"]*)"', response.get_data(as_text=True)).group(1)
        assert len(value) <= assistant.MAX_QUESTION_CHARS
