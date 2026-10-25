"""Tests de la lecture de la base de connaissances et du découpage en extraits."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tecpap.rag import documents
from tecpap.rag.documents import (
    CHUNK_OVERLAP_CHARS,
    MAX_CHUNK_CHARS,
    Document,
    build_indexed_text,
    chunk_document,
    content_hash,
    iter_knowledge_files,
    load_chunks,
    load_documents,
    parse_document,
    split_front_matter,
    split_sections,
    split_text,
)

REPO_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

FRONT_MATTER_DOC = """---
title: Produits TECPAP
categorie: produits
langue: fr
mots_cles:
  - sachet
  - sachiyat
  - ساشي
---

# Produits TECPAP

Introduction de la gamme.

## Sachets fond plat (code SFP)

Les sachets fond plat servent à emballer les sandwichs.

## Sac fond carré sans poignées (code SFCSP)

Le sac fond carré sans poignées sert aux boulangeries.
"""


def write(directory: Path, relative: str, content: str) -> Path:
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestFrontMatter:
    def test_parses_yaml_lists_and_scalars(self):
        metadata, body = split_front_matter(FRONT_MATTER_DOC)
        assert metadata["title"] == "Produits TECPAP"
        assert metadata["mots_cles"] == ["sachet", "sachiyat", "ساشي"]
        assert body.lstrip().startswith("# Produits TECPAP")

    def test_without_front_matter_returns_whole_text(self):
        metadata, body = split_front_matter("# Titre\n\nTexte")
        assert metadata == {}
        assert body == "# Titre\n\nTexte"

    def test_utf8_bom_is_ignored(self):
        metadata, body = split_front_matter("﻿---\ntitle: Avec BOM\n---\n# T\n")
        assert metadata == {"title": "Avec BOM"}
        assert body == "# T\n"

    def test_windows_line_endings(self):
        metadata, body = split_front_matter("---\r\ntitle: Fenêtres\r\n---\r\n# T\r\n")
        assert metadata == {"title": "Fenêtres"}
        assert body.startswith("# T")

    def test_invalid_yaml_is_logged_and_ignored(self, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.documents"):
            metadata, body = split_front_matter("---\ntitle: [non fermé\n---\n# T\ntexte", "cassé.md")
        assert metadata == {}
        assert body == "# T\ntexte"
        assert "cassé.md" in caplog.text

    def test_non_mapping_yaml_is_ignored(self, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.documents"):
            metadata, body = split_front_matter("---\n- a\n- b\n---\ntexte", "liste.md")
        assert metadata == {}
        assert body == "texte"
        assert "dictionnaire attendu" in caplog.text

    def test_empty_front_matter(self):
        assert split_front_matter("---\n\n---\ntexte") == ({}, "texte")

    def test_unclosed_front_matter_is_plain_text(self):
        raw = "---\ntitle: sans fin\n# Titre"
        assert split_front_matter(raw) == ({}, raw)

    def test_dangerous_yaml_tags_are_refused(self):
        # yaml.safe_load refuse les balises qui construisent des objets Python.
        metadata, _ = split_front_matter("---\ntitle: !!python/object/apply:os.system ['echo x']\n---\ntexte")
        assert metadata == {}

    def test_simple_reader_used_without_pyyaml(self, monkeypatch):
        monkeypatch.setattr(documents, "_load_yaml", documents._parse_simple_yaml)
        metadata, _ = split_front_matter(FRONT_MATTER_DOC)
        assert metadata == {"title": "Produits TECPAP", "categorie": "produits", "langue": "fr",
                            "mots_cles": ["sachet", "sachiyat", "ساشي"]}

    def test_simple_reader_subset(self):
        parsed = documents._parse_simple_yaml(
            "- orphelin\n# commentaire\ntitle: 'Titre entre apostrophes'\nvide:\nligne sans deux-points\n"
            "mots_cles:\n  - \"a\"\n  -\n"
        )
        assert parsed == {"title": "Titre entre apostrophes", "vide": [], "mots_cles": ["a", ""]}


class TestParseDocument:
    def test_metadata_fields(self):
        document = parse_document(FRONT_MATTER_DOC, doc_id="produits", source_path="produits.md")
        assert document == Document(
            doc_id="produits", source_path="produits.md", title="Produits TECPAP", category="produits",
            language="fr", keywords=("sachet", "sachiyat", "ساشي"), body=document.body,
        )

    def test_title_falls_back_to_first_heading_then_file_name(self):
        assert parse_document("Intro\n# Mon titre\n## S\nx", doc_id="a", source_path="a.md").title == "Mon titre"
        assert parse_document("## Section\nx", doc_id="b", source_path="dossier/b.md").title == "b"

    def test_keywords_as_comma_separated_string(self):
        document = parse_document("---\nmots_cles: sachet, kraft , ,sachet\n---\nx", doc_id="d", source_path="d.md")
        assert document.keywords == ("sachet", "kraft")

    def test_keywords_with_numbers_and_nested_values(self):
        document = parse_document("---\nmots_cles:\n  - 80\n  - [a, b]\n  - kraft\n---\nx", doc_id="d", source_path="d.md")
        assert document.keywords == ("80", "kraft")

    def test_scalar_keyword_and_non_text_title(self):
        document = parse_document("---\ntitle: [liste]\nmots_cles: 12\n---\n# Vrai titre\n", doc_id="d", source_path="d.md")
        assert document.title == "Vrai titre"
        assert document.keywords == ("12",)


class TestSections:
    def test_intro_and_level_two_sections(self):
        document = parse_document(FRONT_MATTER_DOC, doc_id="produits", source_path="produits.md")
        sections = split_sections(document)
        assert [section.heading for section in sections] == [
            "Produits TECPAP", "Sachets fond plat (code SFP)", "Sac fond carré sans poignées (code SFCSP)",
        ]
        assert sections[0].text == "Introduction de la gamme."
        assert sections[1].text == "Les sachets fond plat servent à emballer les sandwichs."

    def test_headings_inside_code_blocks_are_text(self):
        body = "## A\navant\n```\n## pas un titre\n~~~\n```\naprès\n~~~yaml\n## non plus\n~~~\n## B\nfin"
        sections = split_sections(Document(doc_id="d", source_path="d.md", title="T", body=body))
        assert [section.heading for section in sections] == ["A", "B"]
        assert "## pas un titre" in sections[0].text
        assert "## non plus" in sections[0].text

    def test_level_three_headings_stay_in_section(self):
        sections = split_sections(Document(doc_id="d", source_path="d.md", title="T",
                                           body="## A\n### Détail\ntexte\n#### Plus fin\nsuite"))
        assert len(sections) == 1
        assert "### Détail" in sections[0].text

    def test_empty_sections_and_closing_hashes(self):
        body = "# Titre\n\n## Vide\n\n## Pleine ##\ncontenu\n##SansEspace\nreste"
        sections = split_sections(Document(doc_id="d", source_path="d.md", title="Titre", body=body))
        assert [section.heading for section in sections] == ["Pleine"]
        assert sections[0].text == "contenu\n##SansEspace\nreste"

    def test_document_without_text(self):
        assert split_sections(Document(doc_id="d", source_path="d.md", title="T", body="# T\n\n## A\n")) == []


class TestSplitText:
    def test_short_text_is_one_chunk(self):
        assert split_text("  Texte court.  ") == ["Texte court."]

    def test_empty_text(self):
        assert split_text("   \n ") == []

    def test_long_text_respects_maximum_and_overlaps(self):
        text = " ".join(f"Phrase {index} sur les sachets en papier kraft." for index in range(100))
        chunks = split_text(text)
        assert len(chunks) > 1
        assert all(len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks)
        for previous, current in zip(chunks, chunks[1:], strict=False):
            # Le début de l'extrait suivant reprend la fin du précédent.
            assert current[:40] in previous
            assert len(current) > CHUNK_OVERLAP_CHARS / 2
        # Aucun mot n'est perdu : chaque phrase figure dans au moins un extrait.
        joined = " ".join(chunks)
        assert all(f"Phrase {index} " in joined for index in range(100))

    def test_cuts_between_paragraphs_first(self):
        paragraph = "Ligne de texte. " * 30
        chunks = split_text(f"{paragraph.strip()}\n\n{paragraph.strip()}", max_chars=600, overlap=0)
        assert chunks == [paragraph.strip(), paragraph.strip()]

    def test_word_longer_than_maximum_is_cut(self):
        chunks = split_text("x" * 2000, max_chars=900)
        assert [len(chunk) for chunk in chunks] == [900, 900, 200]

    def test_arabic_text(self):
        text = "، ".join(["أكياس ورقية من الكرافت للمطاعم والمحلات"] * 60)
        chunks = split_text(text, max_chars=300, overlap=50)
        assert len(chunks) > 1
        assert all(len(chunk) <= 300 for chunk in chunks)

    def test_invalid_parameters(self):
        with pytest.raises(ValueError):
            split_text("texte", max_chars=0)
        # Un chevauchement plus grand que l'extrait est ramené sous la longueur maximale.
        chunks = split_text("mot " * 100, max_chars=50, overlap=500)
        assert all(len(chunk) <= 50 for chunk in chunks)


class TestChunks:
    def test_chunk_fields_and_numbering(self):
        document = parse_document(FRONT_MATTER_DOC, doc_id="produits", source_path="produits.md")
        chunks = chunk_document(document)
        assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
        first = chunks[1]
        assert (first.doc_id, first.source_path, first.title) == ("produits", "produits.md", "Produits TECPAP")
        assert first.section == "Sachets fond plat (code SFP)"
        assert first.keywords == ("sachet", "sachiyat", "ساشي")
        assert first.content_hash == content_hash(first.title, first.section, first.keywords, first.text)

    def test_indexed_text_contains_every_field(self):
        chunk = chunk_document(parse_document(FRONT_MATTER_DOC, doc_id="p", source_path="p.md"))[1]
        indexed = chunk.indexed_text()
        assert indexed == build_indexed_text(chunk.title, chunk.section, chunk.keywords, chunk.text)
        for part in ("Produits TECPAP", "Sachets fond plat", "sachiyat, ساشي", "sandwichs"):
            assert part in indexed

    @pytest.mark.parametrize("field", ["title", "section", "keywords", "text"])
    def test_hash_changes_with_each_indexed_field(self, field):
        values = {"title": "T", "section": "S", "keywords": ("a",), "text": "x"}
        reference = content_hash(**values)
        values[field] = ("b",) if field == "keywords" else values[field] + "!"
        assert content_hash(**values) != reference

    def test_long_section_gives_several_chunks(self):
        body = "## Longue\n" + " ".join(f"Phrase {index} du texte." for index in range(150))
        chunks = chunk_document(Document(doc_id="d", source_path="d.md", title="T", body=body))
        assert len(chunks) > 1
        assert {chunk.section for chunk in chunks} == {"Longue"}
        assert len({chunk.content_hash for chunk in chunks}) == len(chunks)


class TestLoading:
    def test_ignores_files_and_folders_starting_with_underscore(self, tmp_path):
        write(tmp_path, "produits.md", FRONT_MATTER_DOC)
        write(tmp_path, "_guide_redaction.md", "# Guide\n## Règle\nNe pas indexer.")
        write(tmp_path, "_brouillons/note.md", "# Note\n## S\nNe pas indexer.")
        write(tmp_path, "faq/questions.md", "# FAQ\n## Question\nRéponse.")
        write(tmp_path, "notes.txt", "pas du Markdown")
        files = [path.relative_to(tmp_path).as_posix() for path in iter_knowledge_files(tmp_path)]
        assert files == ["faq/questions.md", "produits.md"]
        assert [document.doc_id for document in load_documents(tmp_path)] == ["faq/questions", "produits"]

    def test_missing_directory_gives_nothing(self, tmp_path):
        assert iter_knowledge_files(tmp_path / "absent") == []
        assert load_chunks(tmp_path / "absent") == []

    def test_unreadable_file_is_skipped(self, tmp_path, caplog):
        write(tmp_path, "bon.md", "# Bon\n## S\nTexte.")
        (tmp_path / "mauvais.md").write_bytes(b"# Mauvais\n\xff\xfe\xfa texte")
        with caplog.at_level(logging.WARNING, logger="tecpap.rag.documents"):
            loaded = load_documents(tmp_path)
        assert [document.doc_id for document in loaded] == ["bon"]
        assert "mauvais.md" in caplog.text

    def test_directory_named_like_markdown_is_ignored(self, tmp_path):
        (tmp_path / "dossier.md").mkdir()
        write(tmp_path, "vrai.md", "# Vrai\n## S\nTexte.")
        assert [path.name for path in iter_knowledge_files(tmp_path)] == ["vrai.md"]


class TestRepositoryKnowledgeBase:
    """Contrôles sur les documents réels du dossier knowledge/."""

    def test_writing_guide_is_not_indexed(self):
        doc_ids = {document.doc_id for document in load_documents(REPO_KNOWLEDGE_DIR)}
        assert "_guide_redaction" not in doc_ids
        assert {"produits", "commander", "contact", "papiers_et_codes_articles", "questions_frequentes"} <= doc_ids

    def test_every_document_has_title_and_keywords(self):
        for document in load_documents(REPO_KNOWLEDGE_DIR):
            assert document.title
            assert document.keywords, document.doc_id

    def test_chunks_respect_maximum_length(self):
        chunks = load_chunks(REPO_KNOWLEDGE_DIR)
        assert chunks
        assert all(0 < len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)
        keys = [(chunk.doc_id, chunk.chunk_index) for chunk in chunks]
        assert len(keys) == len(set(keys))
