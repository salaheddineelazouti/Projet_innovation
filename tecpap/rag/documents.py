"""Lecture de la base de connaissances et découpage en extraits.

La base de connaissances est un dossier de fichiers Markdown (``knowledge/**/*.md``), rédigés par
l'équipe commerciale. Les fichiers dont le nom commence par « _ » (guides de rédaction) ne sont
pas indexés.

Format d'un document :

- un en-tête YAML facultatif (« front matter »), encadré par deux lignes ``---`` au tout début
  du fichier, avec les clés ``title`` (titre), ``categorie``, ``langue`` et ``mots_cles``
  (liste de mots employés par les clients, en français, en darija et en arabe) ;
- un titre ``#`` puis des sections ``##``.

Chaque section ``##`` est l'unité de recherche. Le texte placé avant la première section
(introduction du document) forme une section qui porte le titre du document. Une section de
plus de :data:`MAX_CHUNK_CHARS` caractères est coupée en plusieurs extraits (« chunks ») selon
une fenêtre glissante avec chevauchement : la coupe se fait de préférence entre deux
paragraphes, sinon entre deux lignes, deux phrases ou deux mots, et chaque extrait reprend les
:data:`CHUNK_OVERLAP_CHARS` derniers caractères environ du précédent, pour qu'une phrase coupée
reste lisible dans l'un des deux.

L'empreinte ``content_hash`` (SHA-256) d'un extrait couvre tout ce qui est indexé : titre du
document, titre de section, mots-clés et texte. Elle permet de ne recalculer que les extraits
modifiés à chaque indexation.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tecpap.rag.text import join_fields

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 900
"""Longueur maximale d'un extrait, en caractères."""

CHUNK_OVERLAP_CHARS = 120
"""Longueur approximative du chevauchement entre deux extraits consécutifs d'une section."""

IGNORED_PREFIX = "_"
"""Préfixe des fichiers non indexés (guides de rédaction destinés à l'équipe commerciale)."""

_FRONT_MATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
# Séparateurs essayés dans l'ordre : paragraphe, ligne, fin de phrase, espace.
_SEPARATORS = (r"\n[ \t]*\n", r"\n", r"(?<=[.!?;:؟؛])[ \t]+", r"[ \t]+")


@dataclass(frozen=True)
class Document:
    """Document de la base de connaissances, tel que lu sur le disque.

    Attributes:
        doc_id: chemin relatif au dossier de la base, sans extension (``"produits"``).
        source_path: chemin relatif au dossier de la base (``"produits.md"``).
        title: titre (en-tête ``title``, sinon premier titre ``#``, sinon nom du fichier).
        category: thème (en-tête ``categorie``), éventuellement vide.
        language: langue de rédaction (en-tête ``langue``), éventuellement vide.
        keywords: mots-clés de l'en-tête ``mots_cles``.
        body: texte Markdown après l'en-tête.
    """

    doc_id: str
    source_path: str
    title: str
    category: str = ""
    language: str = ""
    keywords: tuple[str, ...] = ()
    body: str = ""


@dataclass(frozen=True)
class Section:
    """Section ``##`` d'un document : titre et texte sans la ligne de titre."""

    heading: str
    text: str


@dataclass(frozen=True)
class Chunk:
    """Extrait indexé : une section, ou un morceau d'une section trop longue.

    Attributes:
        doc_id: identifiant du document (voir :class:`Document`).
        source_path: chemin du fichier relatif au dossier de la base.
        title: titre du document.
        section: titre de la section.
        text: texte de l'extrait, sans la ligne de titre.
        content_hash: empreinte SHA-256 de :meth:`indexed_text`.
        chunk_index: rang de l'extrait dans le document, à partir de 0.
        keywords: mots-clés du document.
    """

    doc_id: str
    source_path: str
    title: str
    section: str
    text: str
    content_hash: str
    chunk_index: int = 0
    keywords: tuple[str, ...] = field(default=())

    def indexed_text(self) -> str:
        """Texte indexé : titre, titre de section, mots-clés et texte, un champ par ligne."""
        return build_indexed_text(self.title, self.section, self.keywords, self.text)


def build_indexed_text(title: str, section: str, keywords: Iterable[str], text: str) -> str:
    """Assemble les champs indexés d'un extrait (titre, section, mots-clés, texte)."""
    return join_fields([title, section, ", ".join(keywords), text])


def content_hash(title: str, section: str, keywords: Iterable[str], text: str) -> str:
    """Empreinte SHA-256 (hexadécimale) des champs indexés d'un extrait."""
    indexed = build_indexed_text(title, section, keywords, text)
    return hashlib.sha256(indexed.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- en-tête YAML


def _parse_simple_yaml(block: str) -> dict[str, Any]:
    """Lit le sous-ensemble YAML des en-têtes (``clé: valeur`` et listes ``- élément``).

    Sert quand la bibliothèque PyYAML n'est pas installée.
    """
    data: dict[str, Any] = {}
    current_list: list[str] | None = None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") or stripped == "-":
            if current_list is not None:
                current_list.append(_unquote(stripped[1:].strip()))
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        value = value.strip()
        if value:
            data[key.strip()] = _unquote(value)
            current_list = None
        else:
            current_list = []
            data[key.strip()] = current_list
    return data


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _load_yaml(block: str) -> Any:
    """Décode un en-tête YAML avec ``yaml.safe_load`` (lecteur simple si PyYAML est absent)."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - dépend de l'environnement d'exécution
        return _parse_simple_yaml(block)
    return yaml.safe_load(block)


def split_front_matter(raw: str, source: str = "") -> tuple[dict[str, Any], str]:
    """Sépare l'en-tête YAML du texte d'un document.

    Args:
        raw: contenu complet du fichier.
        source: nom du fichier, pour le journal.

    Returns:
        Les métadonnées (dictionnaire vide sans en-tête ou si l'en-tête est invalide) et le texte
        qui suit l'en-tête.
    """
    match = _FRONT_MATTER.match(raw)
    if not match:
        return {}, raw.lstrip("﻿")
    body = raw[match.end():]
    try:
        metadata = _load_yaml(match.group(1))
    except Exception as exc:  # yaml.YAMLError et erreurs de structure
        logger.warning("En-tête YAML illisible dans %s (%s) : métadonnées ignorées", source or "document",
                       type(exc).__name__)
        return {}, body
    if metadata is None:
        return {}, body
    if not isinstance(metadata, dict):
        logger.warning("En-tête YAML de %s ignoré : dictionnaire attendu", source or "document")
        return {}, body
    return metadata, body


def _as_text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _as_keywords(value: Any) -> tuple[str, ...]:
    """Mots-clés d'un en-tête : liste YAML, ou chaîne séparée par des virgules."""
    if value is None:
        return ()
    if isinstance(value, str):
        items: Iterable[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    keywords: list[str] = []
    for item in items:
        text = _as_text(item)
        if text and text not in keywords:
            keywords.append(text)
    return tuple(keywords)


# ---------------------------------------------------------------- lecture des fichiers


def _first_heading(body: str, level: int = 1) -> str:
    for heading_level, heading, _ in _iter_lines(body):
        if heading_level == level:
            return heading
    return ""


def _iter_lines(body: str) -> Iterable[tuple[int, str, str]]:
    """Parcourt les lignes : (niveau du titre ou 0, texte du titre, ligne d'origine).

    Les lignes à l'intérieur d'un bloc de code (``` ou ~~~) ne sont jamais des titres.
    """
    fence: str | None = None
    for line in body.splitlines():
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            yield 0, "", line
            continue
        heading = _HEADING.match(line) if fence is None else None
        if heading:
            yield len(heading.group(1)), heading.group(2).strip(), line
        else:
            yield 0, "", line


def parse_document(raw: str, *, doc_id: str, source_path: str) -> Document:
    """Construit un :class:`Document` à partir du contenu d'un fichier Markdown."""
    metadata, body = split_front_matter(raw, source_path)
    title = _as_text(metadata.get("title")) or _first_heading(body) or Path(source_path).stem
    return Document(
        doc_id=doc_id,
        source_path=source_path,
        title=title,
        category=_as_text(metadata.get("categorie")),
        language=_as_text(metadata.get("langue")),
        keywords=_as_keywords(metadata.get("mots_cles")),
        body=body,
    )


def iter_knowledge_files(knowledge_dir: Path | str) -> list[Path]:
    """Fichiers ``.md`` indexables du dossier (sous-dossiers compris), triés par chemin.

    Un fichier dont le nom, ou le nom d'un dossier parent, commence par « _ » est ignoré.
    """
    root = Path(knowledge_dir)
    if not root.is_dir():
        return []
    files = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if any(part.startswith(IGNORED_PREFIX) for part in relative.parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def load_documents(knowledge_dir: Path | str) -> list[Document]:
    """Lit les documents de la base de connaissances (fichiers illisibles ignorés et journalisés)."""
    root = Path(knowledge_dir)
    documents = []
    for path in iter_knowledge_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Document %s ignoré : lecture impossible (%s)", relative, type(exc).__name__)
            continue
        doc_id = relative[: -len(path.suffix)] if path.suffix else relative
        documents.append(parse_document(raw, doc_id=doc_id, source_path=relative))
    return documents


# ---------------------------------------------------------------- sections et extraits


def split_sections(document: Document) -> list[Section]:
    """Découpe un document en sections ``##``.

    Le texte placé avant la première section, sans le titre ``#`` du document, forme une
    section qui porte le titre du document. Les sections sans texte sont ignorées.
    """
    sections: list[Section] = []
    heading = document.title
    lines: list[str] = []
    for level, text, line in _iter_lines(document.body):
        if level == 2:
            _append_section(sections, heading, lines)
            heading, lines = text, []
        elif level == 1:
            continue
        else:
            lines.append(line)
    _append_section(sections, heading, lines)
    return sections


def _append_section(sections: list[Section], heading: str, lines: list[str]) -> None:
    text = "\n".join(lines).strip()
    if text:
        sections.append(Section(heading=heading, text=text))


def _split_units(text: str, max_chars: int, level: int = 0) -> list[str]:
    """Coupe un texte en morceaux d'au plus ``max_chars`` caractères, séparateurs compris.

    La concaténation des morceaux redonne le texte d'origine.
    """
    if len(text) <= max_chars:
        return [text]
    if level == len(_SEPARATORS):
        return [text[start:start + max_chars] for start in range(0, len(text), max_chars)]
    parts = re.split(f"({_SEPARATORS[level]})", text)
    units: list[str] = []
    for index in range(0, len(parts), 2):
        piece = parts[index] + (parts[index + 1] if index + 1 < len(parts) else "")
        if piece:
            units.extend(_split_units(piece, max_chars, level + 1))
    return units


def _overlap_tail(text: str, overlap: int) -> str:
    """Fin d'un extrait reprise au début du suivant, commençant au début d'un mot."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    space = re.search(r"\s", tail)
    return tail[space.end():].lstrip() if space else ""


def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Découpe un texte en extraits d'au plus ``max_chars`` caractères avec chevauchement.

    Args:
        text: texte d'une section.
        max_chars: longueur maximale d'un extrait (au moins 1).
        overlap: longueur approximative reprise du précédent extrait (inférieure à ``max_chars``).

    Returns:
        Les extraits, sans espaces en début ni en fin ; une liste vide pour un texte vide.
    """
    if max_chars < 1:
        raise ValueError("max_chars doit être au moins égal à 1")
    overlap = max(0, min(overlap, max_chars - 1))
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for unit in _split_units(text, max_chars):
        if current and len(current) + len(unit) > max_chars:
            finished = current.strip()
            if finished:
                chunks.append(finished)
            tail = _overlap_tail(finished, overlap)
            current = tail + " " if tail and len(tail) + 1 + len(unit) <= max_chars else ""
        current += unit
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_document(document: Document, max_chars: int = MAX_CHUNK_CHARS,
                   overlap: int = CHUNK_OVERLAP_CHARS) -> list[Chunk]:
    """Découpe un document en extraits numérotés à partir de 0."""
    chunks: list[Chunk] = []
    for section in split_sections(document):
        for text in split_text(section.text, max_chars, overlap):
            chunks.append(Chunk(
                doc_id=document.doc_id,
                source_path=document.source_path,
                title=document.title,
                section=section.heading,
                text=text,
                content_hash=content_hash(document.title, section.heading, document.keywords, text),
                chunk_index=len(chunks),
                keywords=document.keywords,
            ))
    return chunks


def load_chunks(knowledge_dir: Path | str, max_chars: int = MAX_CHUNK_CHARS,
                overlap: int = CHUNK_OVERLAP_CHARS) -> list[Chunk]:
    """Lit la base de connaissances et renvoie tous ses extraits, document par document."""
    chunks: list[Chunk] = []
    for document in load_documents(knowledge_dir):
        chunks.extend(chunk_document(document, max_chars, overlap))
    return chunks


__all__ = [
    "CHUNK_OVERLAP_CHARS",
    "IGNORED_PREFIX",
    "MAX_CHUNK_CHARS",
    "Chunk",
    "Document",
    "Section",
    "build_indexed_text",
    "chunk_document",
    "content_hash",
    "iter_knowledge_files",
    "load_chunks",
    "load_documents",
    "parse_document",
    "split_front_matter",
    "split_sections",
    "split_text",
]
