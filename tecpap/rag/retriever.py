"""Recherche hybride dans la base de connaissances et exemples de commandes validées.

Indexation (:meth:`KnowledgeBase.index`)
    Les extraits du dossier de la base de connaissances (:mod:`tecpap.rag.documents`) sont
    comparés à ceux de la table ``rag_chunks`` (:mod:`tecpap.rag.store`). Seuls les extraits
    absents de la table ou modifiés (empreinte ``content_hash`` différente) sont plongés ; un vecteur
    déjà calculé pour le même contenu est réutilisé, même si l'extrait a changé de rang. Les extraits
    des documents supprimés sont retirés. Tous les vecteurs de l'index appartiennent au même
    espace vectoriel : si le calcul OpenAI échoue et que le calcul local prend le relais, les
    extraits déjà indexés dans l'espace OpenAI sont recalculés localement, et l'indexation
    suivante revient à l'espace OpenAI dès qu'il redevient accessible.

Recherche (:meth:`KnowledgeBase.search`)
    Deux classements des extraits sont calculés :

    - par mots-clés, avec le score Okapi BM25 (:mod:`tecpap.rag.bm25`) sur le texte indexé de
      chaque extrait (titre du document, titre de section, mots-clés du document, texte) ;
    - par similarité cosinus entre le vecteur de la question et celui de l'extrait (recherche
      dense), la question étant plongée dans l'espace vectoriel des extraits.

    Les deux classements sont fusionnés par Reciprocal Rank Fusion (RRF, fusion par les rangs
    réciproques) : chaque extrait reçoit la somme, sur les classements où il figure, de
    ``1 / (k + rang)``, avec k = 60 (valeur de l'article d'origine, Cormack et al., 2009). La
    fusion n'utilise que les rangs : les scores des deux méthodes, d'échelles différentes, n'ont
    pas besoin d'être rendus comparables.

Score de pertinence (:attr:`SearchHit.score`)
    Le score comparé au seuil ``rag_min_score`` (en dessous, la base de connaissances ne répond
    pas) est la moyenne de deux mesures entre 0 et 1, toutes deux indépendantes du calcul de
    plongements configuré :

    - la couverture BM25 (:meth:`tecpap.rag.bm25.BM25Index.coverage`) : part du poids idf des
      mots de la question présents dans l'extrait. Elle exige les mots exacts (après
      normalisation) et pénalise les notions absentes de la base (prix, délais, météo…) ;
    - la similarité lexicale : similarité cosinus des vecteurs de n-grammes de caractères du
      calcul local (:class:`tecpap.rag.embeddings.LocalHashEmbedder`, dimension 512), qui
      tolère les variantes d'écriture et les mots de la même famille.

    Mesurée sur le jeu de questions annotées ``tests/data/rag_queries.jsonl``, cette moyenne
    sépare mieux les questions auxquelles la base répond des autres que chacune des deux
    mesures prise seule, et le seuil garde le même sens que les vecteurs de l'index viennent du
    modèle OpenAI ou du calcul local. Le seuil par défaut a été choisi sur ce jeu de questions
    (voir :mod:`tecpap.rag.calibration`). Les questions en anglais, langue absente de la base, obtiennent des
    scores faibles et sont transmises à l'équipe commerciale plus souvent que les autres. Une
    question de prix peut obtenir un score élevé grâce à la section du produit concerné : le
    refus de donner un prix relève de l'instruction de réponse, pas de la recherche.

Exemples de commandes (:class:`OrderExampleRetriever`)
    Les commandes validées dont le message d'origine (colonne ``message_brut``) est connu
    servent d'exemples pour l'extraction (« few-shot » : quelques exemples résolus placés dans
    l'instruction du modèle). Les plus proches du message à traiter sont choisies par similarité
    cosinus ; les informations qui identifient un client sont remplacées par des marqueurs
    (:func:`anonymize_text`) : nom de l'entreprise (nom complet et mots qui la distinguent) par
    ``<CLIENT>``, numéros de téléphone par ``<TELEPHONE>``, adresses email par ``<EMAIL>``, noms de
    personnes précédés d'un titre ou d'une formule de présentation par ``<PERSONNE>``, adresses
    postales par ``<ADRESSE>``, longs numéros d'identification par ``<NUMERO>``, adresses de sites
    par ``<URL>`` et ligne « DE : » d'un email par ``<EXPEDITEUR>`` ; la signature, après la formule
    de politesse, est supprimée. Un exemple ne montre que la façon d'écrire une commande : il ne
    contient aucune donnée utile à un autre client.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from tecpap.catalog import PRODUCTS
from tecpap.rag.bm25 import BM25Index
from tecpap.rag.documents import Chunk, load_chunks
from tecpap.rag.embeddings import (
    DEFAULT_DIM,
    Embedder,
    LocalHashEmbedder,
    OpenAIEmbedder,
    normalize_rows,
)
from tecpap.rag.store import ChunkStore, IndexedDocument, StoredChunk, cosine_similarities
from tecpap.rag.text import normalize_text, tokenize

logger = logging.getLogger(__name__)

RRF_K = 60
"""Constante k de la fusion Reciprocal Rank Fusion."""

DEFAULT_TOP_K = 4
"""Nombre d'extraits renvoyés par défaut par :meth:`KnowledgeBase.search`."""

LEXICAL_EMBEDDER = LocalHashEmbedder(DEFAULT_DIM)
"""Calcul local qui donne la similarité lexicale du score de pertinence (toujours le même)."""


# ---------------------------------------------------------------- résultats


@dataclass(frozen=True)
class IndexStats:
    """Bilan d'une indexation.

    Attributes:
        added: extraits absents de l'index avant l'indexation.
        updated: extraits réécrits à un rang déjà indexé : contenu différent à ce rang, espace
            vectoriel différent, ou indexation forcée. Le vecteur est réutilisé quand ce contenu
            était déjà indexé dans le même espace (extrait déplacé, par exemple) ; sinon, il est
            recalculé.
        removed: extraits supprimés (documents supprimés ou raccourcis).
        unchanged: extraits conservés tels quels.
        embedder: espace vectoriel de l'index après l'indexation.
    """

    added: int
    updated: int
    removed: int
    unchanged: int
    embedder: str

    @property
    def total(self) -> int:
        """Nombre d'extraits indexés après l'indexation."""
        return self.added + self.updated + self.unchanged

    @property
    def changed(self) -> bool:
        """Vrai si l'indexation a modifié la table."""
        return bool(self.added or self.updated or self.removed)

    def __str__(self) -> str:
        return (f"{self.total} extraits indexés ({self.added} ajoutés, {self.updated} mis à jour, "
                f"{self.removed} supprimés, {self.unchanged} inchangés) ; vecteurs : {self.embedder}")


@dataclass(frozen=True)
class SearchHit:
    """Extrait trouvé par :meth:`KnowledgeBase.search`.

    Attributes:
        chunk: extrait.
        score: score de pertinence entre 0 et 1, comparé au seuil ``rag_min_score`` : moyenne
            de ``coverage`` et ``lexical_score`` (voir la documentation du module).
        dense_score: similarité cosinus entre la question et l'extrait dans l'espace vectoriel
            de l'index (0 si la question n'a pas pu y être plongée).
        bm25_score: score Okapi BM25 brut.
        coverage: couverture BM25, entre 0 et 1.
        lexical_score: similarité cosinus des n-grammes de caractères (calcul local), ramenée à 0
            si elle est négative.
        rrf_score: score de la fusion Reciprocal Rank Fusion, qui ordonne les résultats.
    """

    chunk: Chunk
    score: float
    dense_score: float
    bm25_score: float
    coverage: float = 0.0
    lexical_score: float = 0.0
    rrf_score: float = 0.0


def best_score(hits: Iterable[SearchHit]) -> float:
    """Plus grand score de pertinence d'une liste de résultats (0 pour une liste vide).

    Les résultats sont ordonnés par la fusion RRF : le premier n'a pas toujours le plus grand
    score de pertinence.
    """
    return max((hit.score for hit in hits), default=0.0)


def relevance_score(coverage: float, lexical_score: float) -> float:
    """Score de pertinence : moyenne de la couverture BM25 et de la similarité lexicale, entre 0 et 1."""
    value = (max(0.0, coverage) + max(0.0, lexical_score)) / 2.0
    return float(min(1.0, value))


def reciprocal_rank_fusion(rankings: Iterable[Sequence[int]], k: int = RRF_K) -> dict[int, float]:
    """Fusionne des classements (listes d'indices, du meilleur au moins bon) par RRF.

    Returns:
        Pour chaque indice présent dans au moins un classement, la somme des ``1 / (k + rang)``,
        le rang commençant à 1.
    """
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking, start=1):
            fused[index] = fused.get(index, 0.0) + 1.0 / (k + rank)
    return fused


def _ranking(scores: np.ndarray) -> list[int]:
    """Indices des scores strictement positifs, du plus grand au plus petit (ordre stable)."""
    order = np.argsort(-scores, kind="stable")
    return [int(index) for index in order if scores[index] > 0]


# ---------------------------------------------------------------- index en mémoire


@dataclass
class _SearchIndex:
    """Données de recherche relues depuis la table : extraits, BM25, vecteurs par espace."""

    signature: tuple[int, int]
    stored: list[StoredChunk]
    bm25: BM25Index
    spaces: dict[str, tuple[np.ndarray, np.ndarray]]  # espace -> (indices des extraits, matrice)
    lexical: np.ndarray

    @classmethod
    def build(cls, signature: tuple[int, int], stored: list[StoredChunk], k1: float, b: float) -> _SearchIndex:
        texts = [item.chunk.indexed_text() for item in stored]
        grouped: dict[str, list[int]] = {}
        for position, item in enumerate(stored):
            grouped.setdefault(item.embedder, []).append(position)
        spaces = {}
        for space, positions in grouped.items():
            dims = {stored[position].embedding.shape[0] for position in positions}
            if len(dims) != 1:
                logger.warning("Vecteurs de dimensions différentes dans l'espace %s : recherche dense ignorée", space)
                continue
            matrix = normalize_rows(np.vstack([stored[position].embedding for position in positions]))
            spaces[space] = (np.asarray(positions, dtype=np.int64), matrix)
        lexical_space = spaces.get(LEXICAL_EMBEDDER.name)
        if lexical_space is not None and len(lexical_space[0]) == len(stored):
            lexical = np.zeros_like(lexical_space[1])
            lexical[lexical_space[0]] = lexical_space[1]
        else:
            lexical = LEXICAL_EMBEDDER.embed(texts).vectors
        return cls(
            signature=signature,
            stored=stored,
            bm25=BM25Index([tokenize(text) for text in texts], k1=k1, b=b),
            spaces=spaces,
            lexical=lexical,
        )


# ---------------------------------------------------------------- base de connaissances


class KnowledgeBase:
    """Base de connaissances indexée dans SQLite, avec recherche hybride BM25 + vecteurs.

    Args:
        conn: connexion SQLite (la table ``rag_chunks`` est créée si besoin).
        embedder: calcul des plongements (:class:`LocalHashEmbedder` ou :class:`OpenAIEmbedder`).
        knowledge_dir: dossier des documents Markdown.
        rrf_k: constante k de la fusion RRF.
        bm25_k1, bm25_b: paramètres du score Okapi BM25.

    Les méthodes peuvent être appelées depuis plusieurs fils d'exécution : l'accès à la
    connexion est protégé par un verrou.
    """

    def __init__(self, conn: sqlite3.Connection, embedder: Embedder, knowledge_dir: Path | str, *,
                 rrf_k: int = RRF_K, bm25_k1: float = 1.2, bm25_b: float = 0.75):
        if rrf_k < 1:
            raise ValueError("rrf_k doit être au moins égal à 1")
        self.store = ChunkStore(conn)
        self.embedder = embedder
        self.knowledge_dir = Path(knowledge_dir)
        self.rrf_k = rrf_k
        self.bm25_k1 = bm25_k1
        self.bm25_b = bm25_b
        self._lock = threading.RLock()
        self._index: _SearchIndex | None = None
        self._warned_spaces: set[str] = set()

    @classmethod
    def from_settings(cls, conn: sqlite3.Connection, settings: Any, llm_client: Any = None) -> KnowledgeBase:
        """Construit la base avec le dossier ``settings.knowledge_dir``.

        Le calcul des plongements suit ``settings.rag_embedder`` (voir :func:`build_embedder`) :
        calcul local par défaut, modèle ``settings.model_embedding`` avec ``openai`` et un client
        des modèles configuré.
        """
        return cls(conn, build_embedder(settings, llm_client), settings.knowledge_dir)

    # ------------------------------------------------------------------ indexation

    def index(self, force: bool = False) -> IndexStats:
        """Met l'index à jour à partir du dossier de la base de connaissances.

        Args:
            force: recalcule les vecteurs de tous les extraits.

        Returns:
            Le bilan de l'indexation. Si le dossier n'existe pas, l'index n'est pas modifié
            (aucune suppression) et un avertissement est journalisé.
        """
        with self._lock:
            existing = self.store.all()
            if not self.knowledge_dir.is_dir():
                logger.warning("Dossier de la base de connaissances introuvable (%s) : index inchangé",
                               self.knowledge_dir)
                space = existing[0].embedder if existing else self.embedder.name
                return IndexStats(0, 0, 0, len(existing), space)
            chunks = load_chunks(self.knowledge_dir)
            stats = self._synchronize(chunks, existing, force)
            self._index = None
        if stats.changed:
            logger.info("Base de connaissances indexée : %s", stats)
        return stats

    def _synchronize(self, chunks: list[Chunk], existing: list[StoredChunk], force: bool) -> IndexStats:
        """Calcule et écrit les différences entre les extraits lus et ceux de la table."""
        target = self.embedder.name
        by_key = {(item.chunk.doc_id, item.chunk.chunk_index): item for item in existing}
        reusable = {} if force else {
            item.chunk.content_hash: item.embedding for item in existing if item.embedder == target
        }

        unchanged: list[StoredChunk] = []
        to_write: list[Chunk] = []
        for chunk in chunks:
            previous = by_key.get((chunk.doc_id, chunk.chunk_index))
            if (not force and previous is not None and previous.chunk == chunk
                    and previous.embedder == target):
                unchanged.append(previous)
            else:
                to_write.append(chunk)

        missing = [chunk for chunk in to_write if chunk.content_hash not in reusable]
        vectors: dict[str, np.ndarray] = {}
        space = target
        if missing:
            result = self.embedder.embed([chunk.indexed_text() for chunk in missing])
            space = result.embedder
            vectors = {chunk.content_hash: result.vectors[row] for row, chunk in enumerate(missing)}
        if space != target:
            # Repli sur un autre espace : tout l'index doit passer dans cet espace.
            to_write = list(chunks)
            unchanged = []
            others = [chunk for chunk in chunks if chunk.content_hash not in vectors]
            if others:
                recomputed = self.embedder.embed_in(space, [chunk.indexed_text() for chunk in others])
                if recomputed is None:
                    raise RuntimeError(f"Espace vectoriel {space} inaccessible pendant l'indexation")
                vectors.update({chunk.content_hash: recomputed[row] for row, chunk in enumerate(others)})
        else:
            vectors = {**reusable, **vectors}

        kept_ids = {item.id for item in unchanged}
        new_keys = {(chunk.doc_id, chunk.chunk_index) for chunk in chunks}
        written_keys = {(chunk.doc_id, chunk.chunk_index) for chunk in to_write}
        # Toutes les lignes non conservées sont supprimées, y compris celles que la lecture a
        # ignorées (vecteur illisible) : leur couple (doc_id, chunk_index) peut être réécrit.
        delete_ids = sorted(self.store.ids() - kept_ids)
        self.store.apply(
            delete_ids=delete_ids,
            insert=[(chunk, space, vectors[chunk.content_hash]) for chunk in to_write],
        )
        return IndexStats(
            added=len(written_keys - set(by_key)),
            updated=len(written_keys & set(by_key)),
            removed=len(set(by_key) - new_keys),
            unchanged=len(unchanged),
            embedder=space,
        )

    # ------------------------------------------------------------------ consultation

    def chunk_count(self) -> int:
        """Nombre d'extraits indexés."""
        with self._lock:
            return self.store.count()

    def documents(self) -> list[IndexedDocument]:
        """Documents indexés, avec leur nombre d'extraits."""
        with self._lock:
            return self.store.documents()

    def _search_index(self) -> _SearchIndex:
        """Index en mémoire, relu depuis la table quand elle a changé."""
        signature = self.store.signature()
        if self._index is None or self._index.signature != signature:
            self._index = _SearchIndex.build(signature, self.store.all(), self.bm25_k1, self.bm25_b)
        return self._index

    def _dense_scores(self, index: _SearchIndex, query: str) -> np.ndarray:
        """Similarité cosinus de la question avec chaque extrait, dans l'espace de chaque extrait."""
        scores = np.zeros(len(index.stored), dtype=np.float32)
        for space, (positions, matrix) in index.spaces.items():
            vectors = self.embedder.embed_in(space, [query])
            if vectors is None or len(vectors) != 1 or vectors.shape[1] != matrix.shape[1]:
                if space not in self._warned_spaces:
                    logger.warning("Question impossible à plonger dans l'espace %s : recherche par mots-clés "
                                   "seule pour %d extraits (relancer l'indexation)", space, len(positions))
                    self._warned_spaces.add(space)
                continue
            scores[positions] = cosine_similarities(matrix, vectors[0])
        return scores

    def search(self, query: str, k: int = DEFAULT_TOP_K) -> list[SearchHit]:
        """Cherche les extraits les plus proches d'une question.

        Args:
            query: question du client, dans n'importe quelle langue.
            k: nombre maximal d'extraits renvoyés.

        Returns:
            Au plus ``k`` extraits, ordonnés par la fusion RRF des classements BM25 et dense ;
            une liste vide pour une question vide, un index vide, ou quand aucun extrait n'a de
            score BM25 ni de similarité cosinus strictement positifs. Le seuil de réponse se
            compare à :func:`best_score` des résultats.
        """
        if not query or not query.strip() or k <= 0:
            return []
        with self._lock:
            index = self._search_index()
        if not index.stored:
            return []
        tokens = tokenize(query)
        bm25 = np.asarray(index.bm25.scores(tokens), dtype=np.float64)
        dense = self._dense_scores(index, query)
        fused = reciprocal_rank_fusion([_ranking(bm25), _ranking(dense)], self.rrf_k)
        if not fused:
            return []
        lexical = cosine_similarities(index.lexical, LEXICAL_EMBEDDER.vector(query))
        order = sorted(fused, key=lambda position: (-fused[position], position))[:k]
        hits = []
        for position in order:
            coverage = index.bm25.coverage(tokens, position)
            lexical_score = max(0.0, float(lexical[position]))
            hits.append(SearchHit(
                chunk=index.stored[position].chunk,
                score=relevance_score(coverage, lexical_score),
                dense_score=float(dense[position]),
                bm25_score=float(bm25[position]),
                coverage=float(coverage),
                lexical_score=lexical_score,
                rrf_score=float(fused[position]),
            ))
        return hits


def build_embedder(settings: Any, llm_client: Any = None) -> Embedder:
    """Calcul des plongements selon la configuration (``settings.rag_embedder``, variable ``RAG_EMBEDDER``).

    - ``local`` (valeur par défaut) : :class:`LocalHashEmbedder`, hors ligne et déterministe ;
    - ``openai`` : :class:`OpenAIEmbedder` (modèle ``settings.model_embedding``) quand un client
      des modèles disponible est fourni ou qu'une clé ``openai_api_key`` est configurée ;
      :class:`LocalHashEmbedder` sinon.

    La question et les extraits sont toujours comparés dans le même espace vectoriel : après un
    changement de ce réglage, :meth:`KnowledgeBase.index` recalcule les vecteurs de l'index.
    """
    if getattr(settings, "rag_embedder", "local") != "openai":
        return LocalHashEmbedder()
    if llm_client is None and getattr(settings, "openai_api_key", None):
        from tecpap.llm.client import LLMClient

        llm_client = LLMClient.from_settings(settings)
    if llm_client is not None and getattr(llm_client, "available", False):
        return OpenAIEmbedder(llm_client, settings.model_embedding)
    return LocalHashEmbedder()


# ---------------------------------------------------------------- exemples de commandes

CLIENT_PLACEHOLDER = "<CLIENT>"
PHONE_PLACEHOLDER = "<TELEPHONE>"
EMAIL_PLACEHOLDER = "<EMAIL>"
PERSON_PLACEHOLDER = "<PERSONNE>"
ADDRESS_PLACEHOLDER = "<ADRESSE>"
NUMBER_PLACEHOLDER = "<NUMERO>"
URL_PLACEHOLDER = "<URL>"
SENDER_PLACEHOLDER = "<EXPEDITEUR>"

ORDER_EXAMPLE_FIELDS: tuple[str, ...] = (
    "type_produit", "quantite", "unite", "type_papier", "grammage", "laize", "format_sac",
    "type_sac", "impression_client", "prix_unitaire", "prix_total", "devise", "date_livraison",
)
"""Champs d'une commande validée repris dans un exemple (le client devient ``<CLIENT>``)."""

_EMAIL_PATTERN = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", re.UNICODE)
# Numéro international (+212…, 00212…) ou marocain (05…, 06…, 07…, 08…), séparateurs usuels admis.
_PHONE_PATTERN = re.compile(
    r"(?:whatsapp:)?(?<![\w+])(?:(?:\+|00)\s?\d{1,3}[\s.\-]?(?:\(0\)\s?)?\d(?:[\s.\-]?\d){6,11}"
    r"|0[5-8](?:[\s.\-]?\d){8})(?!\d)"
)


# Ponctuation qui peut entourer un nom de client dans un message (guillemets, parenthèses,
# virgule, point-virgule et point d'interrogation arabes…).
_NAME_PUNCTUATION = ".,;:!?()[]«»\"'“”‘’،؛؟"

# Mot qui commence par une majuscule (nom propre), et mots de liaison des noms et des rues.
_CAPITALIZED = r"[A-ZÀ-ÖØ-Þ][\wÀ-ÿ'’-]*"
_LINKING = r"(?:ibn|ben|bin|abou|abu|el|al|de|du|des|la|le|d['’])"
_PROPER_NAME = rf"{_CAPITALIZED}(?:\s+(?:{_CAPITALIZED}|{_LINKING})){{0,3}}"
_ARABIC_WORD = r"[\u0600-\u06FF]+"

_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# Numéro d'au moins huit chiffres : identifiant commun de l'entreprise (ICE, 15 chiffres), registre
# du commerce… Une quantité reste lisible : au plus sept chiffres (5 000 000 pièces), ou écrite avec
# des séparateurs.
_LONG_NUMBER_PATTERN = re.compile(r"(?<![\w.,])\d{8,}(?!\w)")
_SENDER_LINE_PATTERN = re.compile(r"^(DE|FROM)\s*:.*$", re.MULTILINE)
_CLOSING_PATTERN = re.compile(
    r"\b(cordialement|bien cordialement|bien à vous|salutations(?: distinguées| cordiales)?|respectueusement|"
    r"best regards|kind regards|envoyé de mon|sent from)\b.*$",
    re.IGNORECASE | re.DOTALL,
)
_PERSON_TITLES = (r"(?:M\.|MM\.|Mr\.?|Mrs\.?|Ms\.?|Mme\.?|Mlle\.?|Dr\.?|Maître|Si|Sidi|Lalla|Haj|Hajj|Hajja|"
                  r"(?i:monsieur|madame|mademoiselle|docteur))")
_TITLED_PERSON_PATTERN = re.compile(
    rf"(?<!\w)(?:{_PERSON_TITLES}\s+{_PROPER_NAME}"
    rf"|(?:السيد|السيدة|الأستاذ|الأستاذة|الحاج|الحاجة|سي|لالة)\s+{_ARABIC_WORD}(?:\s+{_ARABIC_WORD})?)"
)
_INTRODUCED_PERSON_PATTERN = re.compile(
    rf"(?<!\w)((?i:je suis|je m'appelle|mon nom est|contact|responsable|gérant|gerant|directeur|directrice|"
    rf"de la part de|à l'attention de|a l'attention de|ana|smiti|ismi))(\s*:?\s+){_PROPER_NAME}"
)
_STREET_KEYWORDS = (r"(?i:rue|avenue|av\.|boulevard|bd\.?|lotissement|quartier|route|résidence|residence|"
                    r"immeuble|imm\.|derb|hay|zone industrielle|parc industriel)")
_ADDRESS_PATTERN = re.compile(
    rf"(?<!\w)(?:\d{{1,4}}\s*(?:bis|ter)?\s*,?\s+)?{_STREET_KEYWORDS}\s+"
    rf"(?:\d{{1,3}}(?!\d)|(?:{_LINKING}\s+){{0,2}}{_CAPITALIZED}(?:\s+(?:{_CAPITALIZED}|{_LINKING})){{0,4}})"
    rf"|(?:شارع|زنقة|حي|درب|تجزئة|إقامة)\s+{_ARABIC_WORD}(?:\s+{_ARABIC_WORD})?"
)

# Mots d'un nom d'entreprise qui ne la distinguent pas : formes juridiques, types de commerce et
# mots du catalogue ; seuls les autres mots sont aussi remplacés par <CLIENT> dans un message.
_GENERIC_NAME_WORDS = frozenset(normalize_text(word) for word in (
    "société", "societe", "entreprise", "établissement", "établissements", "etablissement", "etablissements",
    "groupe", "maison", "chez", "boutique", "magasin", "atelier", "centre", "marché", "marche", "supermarché",
    "supermarche", "superette", "boulangerie", "pâtisserie", "patisserie", "boucherie", "épicerie", "epicerie",
    "restaurant", "café", "cafe", "snack", "hôtel", "hotel", "pharmacie", "traiteur", "pizzeria", "glacier",
    "laiterie", "fast", "food", "burger", "pour", "dans", "avec", "kraft", "papier", "emballage", "sachet",
    "sachets", "sacs", "sandwich", "tacos",
))


def _catalog_words() -> frozenset[str]:
    """Mots des mots-clés et des désignations du catalogue : ils ne désignent jamais un client."""
    words: set[str] = set()
    for product in PRODUCTS:
        texts = [product["type"], *product["usages"]]
        for keywords in product["keywords"].values():
            texts.extend(keywords)
        for text in texts:
            words.update(normalize_text(text).split())
    return frozenset(words)


_CATALOG_WORDS = _catalog_words()


@dataclass(frozen=True)
class OrderExample:
    """Commande validée anonymisée, servant d'exemple pour l'extraction.

    Attributes:
        order_id: identifiant de la commande (pour la traçabilité, jamais montré à un client).
        message: message d'origine anonymisé.
        order: champs de :data:`ORDER_EXAMPLE_FIELDS` connus, plus ``entreprise_cliente``
            égal à ``<CLIENT>``.
        similarity: similarité cosinus avec le message à traiter.
    """

    order_id: int
    message: str
    order: dict[str, Any]
    similarity: float


def anonymize_text(text: str, names: Iterable[str | None] = (), phones: Iterable[str | None] = (),
                   emails: Iterable[str | None] = ()) -> str:
    """Remplace par des marqueurs ce qui identifie un client dans un texte.

    Sont remplacés, dans cet ordre : emails (``<EMAIL>``), numéros de téléphone
    (``<TELEPHONE>``), noms de clients (``<CLIENT>`` : nom complet, puis chaque mot qui distingue
    l'entreprise, voir :func:`_distinctive_words`), adresses de sites (``<URL>``), numéros d'au
    moins huit chiffres, tels que l'identifiant commun de l'entreprise (``<NUMERO>``), ligne
    « DE : » d'un email (``<EXPEDITEUR>``), noms de personnes précédés d'un titre (« M. »,
    « Mme », « السيد »…) ou d'une formule de présentation (« je suis », « ana »…) (``<PERSONNE>``)
    et adresses postales (rue, avenue, quartier, « شارع »…) (``<ADRESSE>``). Le texte qui suit une
    formule de politesse (« cordialement »…) est supprimé : c'est la signature.

    Args:
        text: texte à anonymiser.
        names: noms à remplacer par ``<CLIENT>`` (sans tenir compte de la casse, des accents
            ni des espaces multiples).
        phones: numéros connus du client, remplacés en plus des numéros reconnus par motif.
        emails: emails connus du client, remplacés en plus des emails reconnus par motif.
    """
    if not text:
        return ""
    for email in _clean_values(emails):
        text = re.sub(re.escape(email), EMAIL_PLACEHOLDER, text, flags=re.IGNORECASE)
    text = _EMAIL_PATTERN.sub(EMAIL_PLACEHOLDER, text)
    for phone in _clean_values(phones):
        text = text.replace(phone, PHONE_PLACEHOLDER)
    text = _PHONE_PATTERN.sub(PHONE_PLACEHOLDER, text)
    clean_names = sorted(_clean_values(names), key=len, reverse=True)
    for name in clean_names:
        text = _replace_name(text, name)
    for word in sorted({word for name in clean_names for word in _distinctive_words(name)}, key=len, reverse=True):
        text = _replace_name(text, word)
    text = _URL_PATTERN.sub(URL_PLACEHOLDER, text)
    text = _LONG_NUMBER_PATTERN.sub(NUMBER_PLACEHOLDER, text)
    text = _SENDER_LINE_PATTERN.sub(lambda match: f"{match.group(1)} : {SENDER_PLACEHOLDER}", text)
    text = _CLOSING_PATTERN.sub(lambda match: match.group(1), text)
    text = _TITLED_PERSON_PATTERN.sub(PERSON_PLACEHOLDER, text)
    text = _INTRODUCED_PERSON_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{PERSON_PLACEHOLDER}", text)
    return _ADDRESS_PATTERN.sub(ADDRESS_PLACEHOLDER, text)


def _distinctive_words(name: str) -> list[str]:
    """Mots d'un nom d'entreprise qui la distinguent : au moins quatre lettres, ni mot courant ni mot du catalogue.

    « Boulangerie Atlas SARL » donne « atlas » : un message qui écrit seulement « Atlas » ne doit pas
    révéler le nom du client.
    """
    words = []
    for word in normalize_text(name).split():
        word = word.strip(_NAME_PUNCTUATION)
        if len(word) >= 4 and not word.isdigit() and word not in _GENERIC_NAME_WORDS \
                and word not in _CATALOG_WORDS:
            words.append(word)
    return words


def _clean_values(values: Iterable[str | None]) -> list[str]:
    cleaned = []
    for value in values:
        if value and str(value).strip() and len(str(value).strip()) >= 3:
            cleaned.append(str(value).strip().removeprefix("whatsapp:"))
    return cleaned


def _replace_name(text: str, name: str) -> str:
    """Remplace ``name`` dans ``text`` sans tenir compte de la casse, des accents ni des espaces."""
    target = normalize_text(name).split()
    if not target:
        return text
    words = list(re.finditer(r"\S+", text))
    normalized = [normalize_text(match.group()).strip(_NAME_PUNCTUATION) for match in words]
    size = len(target)
    pieces: list[str] = []
    cursor = 0
    position = 0
    while position <= len(words) - size:
        if normalized[position:position + size] == target:
            first, last = words[position], words[position + size - 1]
            start = first.start() + len(first.group()) - len(first.group().lstrip(_NAME_PUNCTUATION))
            end = last.end() - (len(last.group()) - len(last.group().rstrip(_NAME_PUNCTUATION)))
            pieces.append(text[cursor:start])
            pieces.append(CLIENT_PLACEHOLDER)
            cursor = end
            position += size
        else:
            position += 1
    pieces.append(text[cursor:])
    return "".join(pieces)


class OrderExampleRetriever:
    """Commandes validées proches d'un message reçu, anonymisées, pour l'extraction par exemples.

    Args:
        conn: connexion à la base des commandes (tables ``commandes`` et ``clients``).
        embedder: calcul des plongements.
        max_candidates: nombre maximal de commandes validées comparées (les plus récentes).
        max_message_chars: longueur maximale du message d'un exemple.

    Les vecteurs des messages sont gardés en mémoire (clé : espace vectoriel, commande et
    empreinte du message) pour ne pas les recalculer à chaque appel ; seuls ceux des commandes
    candidates du dernier appel sont conservés.
    """

    def __init__(self, conn: sqlite3.Connection, embedder: Embedder, *, max_candidates: int = 500,
                 max_message_chars: int = 1500):
        self.conn = conn
        self.embedder = embedder
        self.max_candidates = max_candidates
        self.max_message_chars = max_message_chars
        self._cache: dict[tuple[str, int, str], np.ndarray] = {}

    def _columns(self, table: str) -> set[str]:
        # Nom de table fixe, jamais issu d'une saisie.
        return {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _candidates(self) -> list[dict[str, Any]]:
        """Commandes validées avec un message d'origine non vide ; [] si la colonne n'existe pas."""
        columns = self._columns("commandes")
        if "message_brut" not in columns or "statut" not in columns:
            return []
        selected = ["c.id", "c.message_brut"] + [
            f"c.{name}" for name in ORDER_EXAMPLE_FIELDS if name in columns and name != "type_produit"
        ]
        selected += [f"c.{name}" for name in ("email_from", "whatsapp_from") if name in columns]
        joins = ""
        client_columns = self._columns("clients") if "client_id" in columns else set()
        if {"id", "nom"} <= client_columns:
            selected += ["cl.nom AS client_nom", "cl.email AS client_email" if "email" in client_columns
                         else "NULL AS client_email",
                         "cl.telephone AS client_telephone" if "telephone" in client_columns
                         else "NULL AS client_telephone"]
            joins += " LEFT JOIN clients cl ON c.client_id = cl.id"
        product_columns = self._columns("produits") if "produit_id" in columns else set()
        if {"id", "type"} <= product_columns:
            selected.append("p.type AS type_produit")
            joins += " LEFT JOIN produits p ON c.produit_id = p.id"
        query = (f"SELECT {', '.join(selected)} FROM commandes c{joins} "  # nosec B608
                 "WHERE c.statut = 'validee' AND c.message_brut IS NOT NULL AND TRIM(c.message_brut) != '' "
                 "ORDER BY c.id DESC LIMIT ?")
        cursor = self.conn.execute(query, (self.max_candidates,))
        names = [description[0] for description in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def _vectors(self, space: str, rows: list[dict[str, Any]]) -> np.ndarray | None:
        """Vecteurs des messages dans l'espace ``space`` (cache, puis calcul des manquants)."""
        keys = [(space, int(row["id"]), hashlib.sha256(row["message_brut"].encode("utf-8")).hexdigest())
                for row in rows]
        cache = {key: self._cache[key] for key in keys if key in self._cache}
        missing = [position for position, key in enumerate(keys) if key not in cache]
        if missing:
            texts = [rows[position]["message_brut"] for position in missing]
            computed = self.embedder.embed_in(space, texts)
            if computed is None or len(computed) != len(missing):
                return None
            for row_number, position in enumerate(missing):
                cache[keys[position]] = computed[row_number]
        self._cache = cache
        return np.vstack([cache[key] for key in keys])

    def similar_validated_orders(self, text: str, k: int = 3) -> list[OrderExample]:
        """Jusqu'à ``k`` commandes validées les plus proches de ``text``, anonymisées.

        Renvoie une liste vide si le texte est vide, si la colonne ``message_brut`` n'existe pas
        encore, si aucune commande validée n'a de message, ou si les plongements sont
        indisponibles.
        """
        if not text or not text.strip() or k <= 0:
            return []
        try:
            rows = self._candidates()
        except sqlite3.Error:
            logger.warning("Lecture des commandes validées impossible : aucun exemple", exc_info=True)
            return []
        if not rows:
            return []
        query = self.embedder.embed([text])
        vectors = self._vectors(query.embedder, rows)
        if vectors is None:
            logger.warning("Plongements des commandes indisponibles dans l'espace %s : aucun exemple",
                           query.embedder)
            return []
        similarities = cosine_similarities(vectors, query.vectors[0])
        order = np.argsort(-similarities, kind="stable")[:k]
        return [self._example(rows[int(position)], float(similarities[int(position)])) for position in order]

    def _example(self, row: dict[str, Any], similarity: float) -> OrderExample:
        names = [row.get("client_nom")]
        phones = [row.get("client_telephone"), row.get("whatsapp_from")]
        emails = [row.get("client_email"), row.get("email_from")]

        def clean(value: Any) -> Any:
            return anonymize_text(value, names, phones, emails) if isinstance(value, str) else value

        message = clean(row["message_brut"].strip())[: self.max_message_chars]
        order: dict[str, Any] = {"entreprise_cliente": CLIENT_PLACEHOLDER}
        for name in ORDER_EXAMPLE_FIELDS:
            value = row.get(name)
            if value is not None and value != "":
                order[name] = clean(value)
        return OrderExample(order_id=int(row["id"]), message=message, order=order, similarity=similarity)


__all__ = [
    "ADDRESS_PLACEHOLDER",
    "CLIENT_PLACEHOLDER",
    "DEFAULT_TOP_K",
    "EMAIL_PLACEHOLDER",
    "LEXICAL_EMBEDDER",
    "NUMBER_PLACEHOLDER",
    "ORDER_EXAMPLE_FIELDS",
    "PERSON_PLACEHOLDER",
    "PHONE_PLACEHOLDER",
    "RRF_K",
    "SENDER_PLACEHOLDER",
    "URL_PLACEHOLDER",
    "IndexStats",
    "KnowledgeBase",
    "OrderExample",
    "OrderExampleRetriever",
    "SearchHit",
    "anonymize_text",
    "best_score",
    "build_embedder",
    "reciprocal_rank_fusion",
    "relevance_score",
]
