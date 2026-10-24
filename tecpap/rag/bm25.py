"""Recherche par mots-clés avec la fonction de score Okapi BM25 (Python pur).

Pour une question q et un document d, le score Okapi BM25 vaut :

    BM25(q, d) = somme, sur les mots t de q, de  idf(t) × tf(t, d) × (k1 + 1)
                                                  / (tf(t, d) + k1 × (1 − b + b × |d| / moyenne(|d|)))

- ``tf(t, d)`` : nombre d'occurrences du mot t dans d ;
- ``|d|`` : nombre de mots de d, comparé à la longueur moyenne des documents ;
- ``idf(t) = ln(1 + (N − n(t) + 0,5) / (n(t) + 0,5))`` : fréquence documentaire inverse (« inverse
  document frequency »), élevée pour un mot présent dans peu des N documents (n(t) documents le
  contiennent). Cette variante, celle de Lucene, reste positive même pour un mot présent
  partout ;
- ``k1`` (1,2) règle la saturation : la dixième occurrence d'un mot compte bien moins que la
  première ; ``b`` (0,75) règle la correction par la longueur du document.

Un mot répété dans la question compte une fois par occurrence, comme dans la formule d'origine.

Le score BM25 n'a pas de borne supérieure : sa valeur dépend de la longueur de la question et de
la base. :meth:`BM25Index.coverage` en donne une version ramenée entre 0 et 1, comparable d'une
question à l'autre : la part du poids idf des mots de la question que l'on retrouve dans le
document.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

DEFAULT_K1 = 1.2
DEFAULT_B = 0.75


class BM25Index:
    """Index Okapi BM25 d'une liste de documents déjà découpés en mots.

    Args:
        documents: un document par élément, sous la forme de la liste de ses mots.
        k1: paramètre de saturation de la fréquence (positif ou nul).
        b: paramètre de correction par la longueur, entre 0 et 1.
    """

    def __init__(self, documents: Sequence[Sequence[str]], k1: float = DEFAULT_K1, b: float = DEFAULT_B):
        if k1 < 0:
            raise ValueError("k1 doit être positif ou nul")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b doit être compris entre 0 et 1")
        self.k1 = k1
        self.b = b
        self._frequencies = [Counter(tokens) for tokens in documents]
        self._lengths = [len(tokens) for tokens in documents]
        total = sum(self._lengths)
        self.average_length = total / len(self._lengths) if self._lengths and total else 0.0
        document_frequency: Counter[str] = Counter()
        for frequencies in self._frequencies:
            document_frequency.update(frequencies.keys())
        self._document_frequency = document_frequency

    def __len__(self) -> int:
        return len(self._frequencies)

    @property
    def max_idf(self) -> float:
        """Idf d'un mot absent de tous les documents (valeur maximale de l'idf)."""
        return self.idf("")

    def idf(self, token: str) -> float:
        """Fréquence documentaire inverse d'un mot (variante de Lucene, toujours positive)."""
        count = self._document_frequency.get(token, 0) if token else 0
        size = len(self._frequencies)
        return math.log(1.0 + (size - count + 0.5) / (count + 0.5))

    def _term_weight(self, frequency: int, length: int) -> float:
        """Saturation de la fréquence d'un mot dans un document, entre 0 et ``k1 + 1``."""
        if frequency <= 0:
            return 0.0
        relative_length = length / self.average_length if self.average_length else 1.0
        norm = self.k1 * (1.0 - self.b + self.b * relative_length)
        return frequency * (self.k1 + 1.0) / (frequency + norm)

    def score(self, query: Sequence[str], index: int) -> float:
        """Score BM25 de la question ``query`` (liste de mots) pour le document ``index``."""
        frequencies = self._frequencies[index]
        length = self._lengths[index]
        return sum(self.idf(token) * self._term_weight(frequencies.get(token, 0), length) for token in query)

    def scores(self, query: Sequence[str]) -> list[float]:
        """Scores BM25 de la question pour tous les documents, dans l'ordre de l'index."""
        if not query:
            return [0.0] * len(self._frequencies)
        weights = {token: self.idf(token) for token in set(query)}
        counts = Counter(query)
        results = []
        for frequencies, length in zip(self._frequencies, self._lengths, strict=True):
            total = 0.0
            for token, occurrences in counts.items():
                frequency = frequencies.get(token, 0)
                if frequency:
                    total += occurrences * weights[token] * self._term_weight(frequency, length)
            results.append(total)
        return results

    def coverage(self, query: Sequence[str], index: int) -> float:
        """Part, entre 0 et 1, du poids idf des mots de la question présents dans le document.

        ``couverture = somme des idf(t) des mots t de q présents dans d / somme des idf(t) de
        tous les mots de q``. Un mot de la question absent de toute la base reçoit l'idf
        maximal : une question qui porte surtout sur des notions absentes de la base (météo,
        délais, prix…) obtient une couverture faible, même si un mot courant comme « TECPAP »
        y figure. Une question sans mot donne 0.
        """
        if not query:
            return 0.0
        frequencies = self._frequencies[index]
        total = matched = 0.0
        for token in query:
            weight = self.idf(token)
            total += weight
            if frequencies.get(token, 0):
                matched += weight
        return matched / total if total else 0.0


__all__ = ["DEFAULT_B", "DEFAULT_K1", "BM25Index"]
