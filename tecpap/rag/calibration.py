"""Mesure de la séparation des questions et choix du seuil ``rag_min_score``.

L'assistant ne répond à partir de la base de connaissances que si le meilleur score de
pertinence des extraits trouvés (:func:`tecpap.rag.retriever.best_score`) atteint
``rag_min_score``. Le seuil se choisit sur un jeu de questions annotées (format JSON Lines, un
objet par ligne) : des questions auxquelles la base répond (``"answerable": true``, avec les
documents attendus dans ``expected_docs``) et des questions hors du champ de la base (prix,
délais, météo, données d'autres clients…, ``"answerable": false``).

Mesures calculées :

- aire sous la courbe ROC (« receiver operating characteristic ») : probabilité qu'une question
  à laquelle la base répond obtienne un meilleur score qu'une question hors champ, tirées au
  hasard (1 = séparation parfaite, 0,5 = hasard) ;
- au seuil choisi : taux de vrais positifs (part des questions auxquelles la base répond qui
  passent le seuil), taux de vrais négatifs (part des questions hors champ arrêtées) et
  exactitude équilibrée (moyenne des deux, insensible au déséquilibre entre les deux classes) ;
- rappel au rang 1 et au rang k : part des questions auxquelles la base répond dont le premier
  extrait (ou l'un des k premiers) vient d'un document attendu.

Le seuil retenu maximise l'exactitude équilibrée (à égalité, celui qui arrête le plus de
questions hors champ). Pour vérifier que ce choix ne colle pas au jeu de questions, le jeu est
aussi coupé en deux moitiés (split dev/test, alternance dans chaque langue et chaque classe) :
le seuil choisi sur la moitié « dev » est évalué sur la moitié « test ».
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from tecpap.rag.retriever import best_score


class _Searchable(Protocol):
    def search(self, query: str, k: int = ...) -> list[Any]: ...


@dataclass(frozen=True)
class LabelledQuery:
    """Question annotée du jeu de calibration."""

    id: str
    language: str
    query: str
    answerable: bool
    category: str = ""
    expected_docs: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryOutcome:
    """Résultat de la recherche pour une question annotée."""

    query: LabelledQuery
    best_score: float
    docs: tuple[str, ...]

    @property
    def top_doc(self) -> str | None:
        """Document du premier extrait renvoyé, ou ``None``."""
        return self.docs[0] if self.docs else None


@dataclass(frozen=True)
class ThresholdMetrics:
    """Taux mesurés à un seuil donné."""

    threshold: float
    true_positive_rate: float
    true_negative_rate: float

    @property
    def balanced_accuracy(self) -> float:
        """Moyenne du taux de vrais positifs et du taux de vrais négatifs."""
        return (self.true_positive_rate + self.true_negative_rate) / 2.0


@dataclass(frozen=True)
class SeparationReport:
    """Bilan de séparation d'un ensemble de questions annotées."""

    positives: int
    negatives: int
    auc: float
    at_threshold: ThresholdMetrics
    best: ThresholdMetrics
    recall_at_1: float
    recall_at_k: float
    k: int
    by_language: dict[str, ThresholdMetrics] = field(default_factory=dict)


def load_labelled_queries(path: Path | str) -> list[LabelledQuery]:
    """Lit un jeu de questions annotées au format JSON Lines (les lignes vides sont ignorées).

    Raises:
        ValueError: ligne qui n'est pas un objet JSON avec ``id``, ``query`` et ``answerable``.
    """
    queries = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            queries.append(LabelledQuery(
                id=str(data["id"]),
                language=str(data.get("language", "")),
                query=str(data["query"]),
                answerable=bool(data["answerable"]),
                category=str(data.get("category", "")),
                expected_docs=tuple(str(doc) for doc in data.get("expected_docs", [])),
            ))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Ligne {number} invalide dans {path} : {exc}") from exc
    return queries


def run_queries(kb: _Searchable, queries: Iterable[LabelledQuery], k: int = 4) -> list[QueryOutcome]:
    """Cherche chaque question et relève le meilleur score et les documents trouvés."""
    outcomes = []
    for item in queries:
        hits = kb.search(item.query, k=k)
        outcomes.append(QueryOutcome(query=item, best_score=best_score(hits),
                                     docs=tuple(hit.chunk.doc_id for hit in hits)))
    return outcomes


def roc_auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """Aire sous la courbe ROC (statistique de Mann-Whitney ; une égalité compte pour moitié)."""
    if not positives or not negatives:
        raise ValueError("Il faut au moins une question de chaque classe")
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1.0
            elif positive == negative:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def metrics_at(threshold: float, positives: Sequence[float], negatives: Sequence[float]) -> ThresholdMetrics:
    """Taux de vrais positifs (score ≥ seuil) et de vrais négatifs (score < seuil)."""
    tpr = sum(score >= threshold for score in positives) / len(positives) if positives else 0.0
    tnr = sum(score < threshold for score in negatives) / len(negatives) if negatives else 0.0
    return ThresholdMetrics(threshold=threshold, true_positive_rate=tpr, true_negative_rate=tnr)


def best_threshold(positives: Sequence[float], negatives: Sequence[float]) -> ThresholdMetrics:
    """Seuil qui maximise l'exactitude équilibrée (à égalité : le plus grand taux de vrais négatifs).

    Les seuils essayés sont les milieux entre deux scores consécutifs distincts, ce qui laisse
    une marge des deux côtés de la frontière.
    """
    scores = sorted(set(positives) | set(negatives))
    if not scores:
        raise ValueError("Aucun score à séparer")
    candidates = [scores[0] - 1e-6] + [(low + high) / 2 for low, high in zip(scores, scores[1:], strict=False)]
    candidates.append(scores[-1] + 1e-6)
    results = [metrics_at(threshold, positives, negatives) for threshold in candidates]
    return max(results, key=lambda item: (round(item.balanced_accuracy, 12), item.true_negative_rate))


def _scores(outcomes: Iterable[QueryOutcome]) -> tuple[list[float], list[float]]:
    positives, negatives = [], []
    for outcome in outcomes:
        (positives if outcome.query.answerable else negatives).append(outcome.best_score)
    return positives, negatives


def separation_report(outcomes: Sequence[QueryOutcome], threshold: float, k: int = 4) -> SeparationReport:
    """Bilan de séparation des questions au seuil ``threshold``."""
    positives, negatives = _scores(outcomes)
    answerable = [outcome for outcome in outcomes if outcome.query.answerable]
    recall_1 = sum(outcome.top_doc in outcome.query.expected_docs for outcome in answerable)
    recall_k = sum(any(doc in outcome.query.expected_docs for doc in outcome.docs[:k]) for outcome in answerable)
    by_language = {}
    for language in sorted({outcome.query.language for outcome in outcomes}):
        group_pos, group_neg = _scores(o for o in outcomes if o.query.language == language)
        by_language[language] = metrics_at(threshold, group_pos, group_neg)
    return SeparationReport(
        positives=len(positives),
        negatives=len(negatives),
        auc=roc_auc(positives, negatives),
        at_threshold=metrics_at(threshold, positives, negatives),
        best=best_threshold(positives, negatives),
        recall_at_1=recall_1 / len(answerable) if answerable else 0.0,
        recall_at_k=recall_k / len(answerable) if answerable else 0.0,
        k=k,
        by_language=by_language,
    )


def split_dev_test(queries: Sequence[LabelledQuery]) -> tuple[list[LabelledQuery], list[LabelledQuery]]:
    """Coupe le jeu en deux moitiés équilibrées : alternance dans chaque couple (langue, classe)."""
    dev: list[LabelledQuery] = []
    test: list[LabelledQuery] = []
    seen: dict[tuple[str, bool], int] = {}
    for item in queries:
        key = (item.language, item.answerable)
        (dev if seen.get(key, 0) % 2 == 0 else test).append(item)
        seen[key] = seen.get(key, 0) + 1
    return dev, test


def dev_test_check(outcomes: Sequence[QueryOutcome]) -> tuple[ThresholdMetrics, ThresholdMetrics]:
    """Seuil choisi sur la moitié dev, puis taux mesurés avec ce seuil sur la moitié test."""
    by_id = {outcome.query.id: outcome for outcome in outcomes}
    dev, test = split_dev_test([outcome.query for outcome in outcomes])
    dev_pos, dev_neg = _scores(by_id[item.id] for item in dev)
    chosen = best_threshold(dev_pos, dev_neg)
    test_pos, test_neg = _scores(by_id[item.id] for item in test)
    return chosen, metrics_at(chosen.threshold, test_pos, test_neg)


def _percent(value: float) -> str:
    return f"{100 * value:.1f} %"


def format_report(report: SeparationReport, dev_test: tuple[ThresholdMetrics, ThresholdMetrics] | None = None) -> str:
    """Présentation du bilan en lignes de texte, en français."""
    current, best = report.at_threshold, report.best
    lines = [
        f"Questions : {report.positives} auxquelles la base répond, {report.negatives} hors champ",
        f"Aire sous la courbe ROC : {report.auc:.3f}",
        f"Rappel au rang 1 : {_percent(report.recall_at_1)} ; au rang {report.k} : {_percent(report.recall_at_k)}",
        f"Seuil {current.threshold:.2f} : vrais positifs {_percent(current.true_positive_rate)}, "
        f"vrais négatifs {_percent(current.true_negative_rate)}, "
        f"exactitude équilibrée {_percent(current.balanced_accuracy)}",
        f"Meilleur seuil mesuré {best.threshold:.3f} : vrais positifs {_percent(best.true_positive_rate)}, "
        f"vrais négatifs {_percent(best.true_negative_rate)}, "
        f"exactitude équilibrée {_percent(best.balanced_accuracy)}",
    ]
    for language, metrics in report.by_language.items():
        lines.append(f"  {language} : vrais positifs {_percent(metrics.true_positive_rate)}, "
                     f"vrais négatifs {_percent(metrics.true_negative_rate)}")
    if dev_test is not None:
        chosen, measured = dev_test
        lines.append(f"Split dev/test : seuil {chosen.threshold:.3f} choisi sur dev "
                     f"(exactitude équilibrée {_percent(chosen.balanced_accuracy)}), "
                     f"sur test : exactitude équilibrée {_percent(measured.balanced_accuracy)}")
    return "\n".join(lines)


__all__ = [
    "LabelledQuery",
    "QueryOutcome",
    "SeparationReport",
    "ThresholdMetrics",
    "best_threshold",
    "dev_test_check",
    "format_report",
    "load_labelled_queries",
    "metrics_at",
    "roc_auc",
    "run_queries",
    "separation_report",
    "split_dev_test",
]
