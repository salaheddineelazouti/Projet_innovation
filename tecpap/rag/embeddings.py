"""Plongements vectoriels des textes (représentation d'un texte par un vecteur de nombres).

Deux calculateurs de plongements sont disponibles :

- :class:`LocalHashEmbedder` fonctionne sans réseau ni modèle. Chaque mot normalisé
  (voir :mod:`tecpap.rag.text` : accents, lettres arabes, arabizi, mots vides) est entouré
  d'espaces puis découpé en n-grammes de caractères (suites de 3 à 5 caractères consécutifs) ;
  le mot entier compte aussi comme un élément. Chaque élément est envoyé vers l'une des
  ``dim`` coordonnées du vecteur par hachage (« hashing trick » : l'empreinte BLAKE2b de
  l'élément donne la coordonnée et un signe + ou −, ce qui évite de stocker un vocabulaire et
  fait se compenser en moyenne les collisions). La valeur d'une coordonnée est la fréquence
  sous-linéaire (1 + log de la fréquence, en anglais « term frequency », TF) des éléments qui
  y tombent ; le vecteur est enfin ramené à une norme euclidienne (L2) de 1. Deux textes qui
  partagent des morceaux de mots ont donc des vecteurs proches, y compris en écriture arabe,
  en arabizi ou avec des fautes de frappe. Le résultat est déterministe : l'empreinte BLAKE2b
  ne dépend pas du processus, contrairement à la fonction ``hash`` de Python.
- :class:`OpenAIEmbedder` utilise le modèle de plongements de l'API OpenAI par
  :meth:`tecpap.llm.client.LLMClient.embed` et se replie sur le calcul local quand l'API est
  indisponible (pas de clé, erreur réseau).

Les vecteurs de deux calculateurs différents ne sont pas comparables. Chaque calcul renvoie
donc un :class:`EmbeddingResult` qui indique le nom de l'espace vectoriel qui l'a produit
(``embedder``) ; ce nom est enregistré avec chaque vecteur, et une question est toujours
plongée dans l'espace des vecteurs auxquels elle est comparée (:meth:`embed_in`).
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

import numpy as np

from tecpap.rag.text import tokenize

logger = logging.getLogger(__name__)

DEFAULT_DIM = 512
"""Dimension par défaut des vecteurs du calcul local."""

NGRAM_SIZES = (3, 4, 5)
"""Longueurs des n-grammes de caractères du calcul local."""

LOCAL_EMBEDDER_VERSION = 1
"""Version de la méthode de calcul local ; elle fait partie du nom de l'espace vectoriel."""

MAX_EMBEDDED_CHARS = 8000
"""Longueur maximale d'un texte envoyé au calcul des plongements (le reste est ignoré)."""


@dataclass(frozen=True)
class EmbeddingResult:
    """Vecteurs calculés pour une liste de textes.

    Attributes:
        vectors: matrice ``float32`` de forme (nombre de textes, dimension), lignes de norme 1
            (ou nulles pour un texte sans mot).
        embedder: nom de l'espace vectoriel qui a produit les vecteurs.
    """

    vectors: np.ndarray
    embedder: str

    def __len__(self) -> int:
        return int(self.vectors.shape[0])


@runtime_checkable
class Embedder(Protocol):
    """Interface commune des calculateurs de plongements."""

    @property
    def name(self) -> str:
        """Nom de l'espace vectoriel visé par :meth:`embed`."""
        ...

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Plonge des textes dans l'espace :attr:`name`, ou dans un espace de repli."""
        ...

    def embed_in(self, space: str, texts: Sequence[str]) -> np.ndarray | None:
        """Plonge des textes dans l'espace ``space`` ; ``None`` si cet espace est inaccessible."""
        ...


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """Ramène chaque ligne à une norme L2 de 1 (les lignes nulles restent nulles), en ``float32``."""
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    safe = np.where(norms > 0, norms, 1.0)
    return (matrix / safe).astype(np.float32)


def _clip(text: str | None) -> str:
    return (text or "")[:MAX_EMBEDDED_CHARS]


@lru_cache(maxsize=200_000)
def _feature_hash(feature: str) -> int:
    """Empreinte BLAKE2b sur 64 bits d'un élément (n-gramme ou mot)."""
    return int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")


def text_features(text: str) -> Counter[str]:
    """Éléments d'un texte pour le calcul local : mots normalisés et leurs n-grammes de 3 à 5 caractères."""
    features: Counter[str] = Counter()
    for word in tokenize(text, stem=False):
        padded = f" {word} "
        features[padded] += 1
        for size in NGRAM_SIZES:
            for start in range(len(padded) - size + 1):
                features["#" + padded[start:start + size]] += 1
    return features


class LocalHashEmbedder:
    """Plongements locaux par hachage de n-grammes de caractères (sans réseau, déterministes).

    Args:
        dim: dimension des vecteurs (au moins 16).
    """

    def __init__(self, dim: int = DEFAULT_DIM):
        if dim < 16:
            raise ValueError("La dimension des vecteurs doit être au moins égale à 16")
        self.dim = dim

    def __repr__(self) -> str:
        return f"LocalHashEmbedder(dim={self.dim})"

    @property
    def name(self) -> str:
        """Nom de l'espace vectoriel : méthode, version et dimension."""
        return f"local-hash-v{LOCAL_EMBEDDER_VERSION}-{self.dim}"

    def vector(self, text: str) -> np.ndarray:
        """Vecteur ``float32`` de norme 1 d'un texte (nul pour un texte sans mot)."""
        vector = np.zeros(self.dim, dtype=np.float64)
        for feature, count in text_features(_clip(text)).items():
            digest = _feature_hash(feature)
            index = digest % self.dim
            sign = 1.0 if (digest >> 63) & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        return normalize_rows(vector)[0]

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Plonge les textes dans l'espace local (jamais d'échec)."""
        vectors = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            vectors[row] = self.vector(text)
        return EmbeddingResult(vectors=vectors, embedder=self.name)

    def embed_in(self, space: str, texts: Sequence[str]) -> np.ndarray | None:
        """Vecteurs dans l'espace ``space`` s'il s'agit de l'espace local, sinon ``None``."""
        if space != self.name:
            return None
        return self.embed(texts).vectors


class OpenAIEmbedder:
    """Plongements calculés par l'API OpenAI, avec repli sur le calcul local.

    Args:
        llm_client: client des modèles (:class:`tecpap.llm.client.LLMClient` ou doublure de
            test avec la même méthode ``embed``).
        model: modèle de plongements, par exemple ``"text-embedding-3-small"``.
        fallback: calculateur utilisé quand l'API est indisponible ; par défaut
            ``LocalHashEmbedder()``.
    """

    def __init__(self, llm_client: Any, model: str, fallback: LocalHashEmbedder | None = None):
        if not model:
            raise ValueError("Le nom du modèle de plongements est obligatoire")
        self.llm_client = llm_client
        self.model = model
        self.fallback = fallback or LocalHashEmbedder()

    def __repr__(self) -> str:
        return f"OpenAIEmbedder(model={self.model!r}, available={self.available})"

    @classmethod
    def from_settings(cls, settings: Any, llm_client: Any) -> OpenAIEmbedder:
        """Construit le calculateur avec le modèle ``settings.model_embedding``."""
        return cls(llm_client, settings.model_embedding)

    @property
    def remote_name(self) -> str:
        """Nom de l'espace vectoriel du modèle OpenAI."""
        return f"openai:{self.model}"

    @property
    def available(self) -> bool:
        """Vrai quand le client des modèles est configuré (clé d'API ou client fourni)."""
        return bool(getattr(self.llm_client, "available", False))

    @property
    def name(self) -> str:
        """Espace visé : celui du modèle OpenAI si le client est configuré, sinon l'espace local."""
        return self.remote_name if self.available else self.fallback.name

    def _remote_vectors(self, texts: Sequence[str]) -> np.ndarray | None:
        """Vecteurs du modèle OpenAI, ou ``None`` si l'appel échoue ou renvoie un résultat incohérent."""
        if not self.available:
            return None
        vectors = self.llm_client.embed([_clip(text) or " " for text in texts], model=self.model)
        if vectors is None or len(vectors) != len(texts):
            return None
        try:
            matrix = np.asarray(vectors, dtype=np.float32)
        except (TypeError, ValueError):
            logger.error("Plongements du modèle %s illisibles", self.model)
            return None
        if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
            logger.error("Plongements du modèle %s illisibles", self.model)
            return None
        return normalize_rows(matrix)

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Plonge les textes avec le modèle OpenAI, sinon avec le calcul local.

        Le champ ``embedder`` du résultat indique l'espace effectivement utilisé.
        """
        texts = list(texts)
        if not texts:
            return EmbeddingResult(np.zeros((0, self.fallback.dim), dtype=np.float32), self.name)
        vectors = self._remote_vectors(texts)
        if vectors is not None:
            return EmbeddingResult(vectors=vectors, embedder=self.remote_name)
        if self.available:
            logger.warning("Plongements OpenAI indisponibles (modèle %s) : calcul local utilisé", self.model)
        return self.fallback.embed(texts)

    def embed_in(self, space: str, texts: Sequence[str]) -> np.ndarray | None:
        """Vecteurs dans l'espace ``space`` (modèle OpenAI ou calcul local), ``None`` sinon."""
        if space == self.fallback.name:
            return self.fallback.embed(texts).vectors
        if space == self.remote_name:
            return self._remote_vectors(list(texts))
        return None


def vector_to_blob(vector: np.ndarray) -> bytes:
    """Sérialise un vecteur en octets (``float32``, ordre des octets petit-boutiste)."""
    return np.asarray(vector, dtype="<f4").tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    """Relit un vecteur sérialisé par :func:`vector_to_blob`."""
    if len(blob) % 4:
        raise ValueError("Vecteur sérialisé invalide : la taille doit être un multiple de 4 octets")
    return np.frombuffer(blob, dtype="<f4").astype(np.float32)


__all__ = [
    "DEFAULT_DIM",
    "Embedder",
    "EmbeddingResult",
    "LocalHashEmbedder",
    "OpenAIEmbedder",
    "blob_to_vector",
    "normalize_rows",
    "text_features",
    "vector_to_blob",
]
