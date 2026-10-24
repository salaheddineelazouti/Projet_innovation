"""Évaluation des composants d'analyse des messages clients sur le jeu annoté.

Usage :
    python evaluation/run_eval.py                 évaluation hors ligne (sans réseau ni clé d'API)
    python evaluation/run_eval.py --online        extraction complète par le modèle de langue
    python evaluation/run_eval.py --dataset FICHIER --output-dir DOSSIER --date AAAA-MM-JJ

Le jeu ``evaluation/dataset/messages.jsonl`` (format JSON Lines : un objet JSON par ligne) associe
à chaque message ses valeurs de référence, appelées étiquettes (schéma dans
``evaluation/README.md``). Chaque composant reçoit le texte du message ; sa sortie est comparée
à l'étiquette.

Évaluation hors ligne (par défaut), sans réseau ni modèle de langue :

1. langue : :func:`tecpap.nlp.language.detect_language` et
   :func:`tecpap.nlp.language.reply_language` (étiquettes ``language`` et ``reply_language``) ;
2. extraction par règles : :func:`tecpap.extraction.rules.rule_based_extract` sur les commandes
   du jeu (``is_order`` vrai), champs ``type_produit``, ``quantite``, ``unite`` et
   ``entreprise_cliente`` ;
3. détection des commandes (champ ``est_bon_commande`` de l'extraction par règles, étiquette
   ``is_order``) et des renouvellements (:func:`tecpap.extraction.rules.detect_reorder`,
   intention ``reorder``) ;
4. tentatives d'injection d'instructions : :func:`tecpap.security.guardrails.inspect_input`
   (étiquette ``is_injection``) ;
5. intentions : ``tecpap.chatbot.intents.detect_intent`` (étiquette ``intent``), appelée avec la
   langue détectée comme dans l'assistant WhatsApp, quand ce module est importable ; sinon la
   section du rapport porte la mention « non disponible ».

Mesures, définies aussi dans le rapport :

- exactitude (« accuracy ») : part des éléments dont la valeur obtenue est égale à l'étiquette ;
- intervalle de confiance à 95 % de Wilson : plage de valeurs de la proportion réelle
  compatibles avec l'échantillon observé (Wilson, 1927), plus fiable que l'approximation
  normale pour les petits effectifs et les proportions proches de 0 ou de 1 ;
- matrice de confusion : tableau croisé des étiquettes attendues (lignes) et des valeurs
  obtenues (colonnes) ;
- pour une détection oui / non, la classe positive est la catégorie à signaler ; vrais positifs
  (VP), faux positifs (FP), faux négatifs (FN), vrais négatifs (VN), précision VP / (VP + FP),
  rappel VP / (VP + FN), F1 = 2 VP / (2 VP + FP + FN) (moyenne harmonique de la précision et du
  rappel), taux de faux positifs FP / (FP + VN) ;
- macro-F1 : moyenne non pondérée des F1 de chaque intention présente dans le jeu, chaque
  intention étant tour à tour la classe positive.

Une mesure dont le dénominateur est nul est non définie (``null`` dans le JSON, « n.d. » dans le
rapport).

Évaluation en ligne (``--online``) : avec une clé ``OPENAI_API_KEY``, chaque commande du jeu passe
par l'extraction complète (:class:`tecpap.extraction.extractor.DataExtractor`, sans base de
données, puis :func:`tecpap.extraction.rules.normalize_order`) et les mêmes champs sont comparés.
Sans clé, le programme s'arrête avec le code de sortie 2, sans aucun appel réseau.

Sorties, dans ``evaluation/results/`` par défaut : ``rapport_hors_ligne.md`` (rapport en
français) et ``rapport_hors_ligne.json`` (mêmes chiffres), ou ``rapport_en_ligne.md`` et
``rapport_en_ligne.json``. Le rapport Markdown est rédigé à partir du dictionnaire écrit dans le
JSON, si bien que les deux fichiers portent les mêmes chiffres. À données et code identiques,
seule la date d'exécution change d'une exécution hors ligne à l'autre.

Codes de sortie : 0 succès, 1 jeu de données ou configuration invalide, 2 évaluation en ligne
sans clé d'API.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import math
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tecpap import __version__  # noqa: E402
from tecpap.catalog import PRODUCT_TYPES, REQUIRED_ORDER_FIELDS  # noqa: E402
from tecpap.config import ConfigurationError, Settings, load_settings  # noqa: E402
from tecpap.extraction.rules import (  # noqa: E402
    NormalizationResult,
    detect_reorder,
    normalize_order,
    rule_based_extract,
    to_bool,
)
from tecpap.llm.client import LLMClient  # noqa: E402
from tecpap.logging_config import configure_logging  # noqa: E402
from tecpap.nlp.language import LanguageResult, detect_language, reply_language  # noqa: E402
from tecpap.security.guardrails import SUSPICION_THRESHOLD, InputCheck, inspect_input  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_DATASET = REPO_ROOT / "evaluation" / "dataset" / "messages.jsonl"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "evaluation" / "results"
OFFLINE_REPORT_NAME = "rapport_hors_ligne"
ONLINE_REPORT_NAME = "rapport_en_ligne"

EXIT_OK = 0
EXIT_INVALID_INPUT = 1
EXIT_NO_API_KEY = 2

LANGUAGES: tuple[str, ...] = ("fr", "darija_latin", "darija_ar", "ar", "en")
"""Étiquettes de langue du jeu, dans l'ordre des tableaux de ``evaluation/README.md``."""

UNKNOWN_LANGUAGE = "unknown"
LANGUAGE_NAMES: Mapping[str, str] = MappingProxyType({
    "fr": "français",
    "darija_latin": "darija en lettres latines",
    "darija_ar": "darija en lettres arabes",
    "ar": "arabe standard moderne",
    "en": "anglais",
    UNKNOWN_LANGUAGE: "langue non identifiée",
})
REPLY_LANGUAGES: tuple[str, ...] = ("fr", "darija", "ar")
INTENTS: tuple[str, ...] = (
    "order", "reorder", "status", "question", "human", "greeting", "confirm_yes", "confirm_no", "cancel", "other",
)
"""Intentions du jeu (définitions dans ``evaluation/README.md``, section 4.3)."""

ORDER_INTENTS = frozenset({"order", "reorder"})
REORDER_INTENT = "reorder"
CHANNELS: tuple[str, ...] = ("email", "whatsapp")
TRAP_CATEGORY = "piege_faux_positif"
VOICE_CATEGORY = "commande_vocale"

EXTRACTION_FIELDS: tuple[str, ...] = ("type_produit", "quantite", "unite", "entreprise_cliente")
"""Champs de commande comparés aux étiquettes."""

REQUIRED_FIELDS: tuple[str, ...] = tuple(name for name in REQUIRED_ORDER_FIELDS if name in EXTRACTION_FIELDS)
"""Champs obligatoires d'une commande (``tecpap.catalog.REQUIRED_ORDER_FIELDS``) parmi les champs comparés."""

EXPECTED_FIELDS: tuple[str, ...] = ("language", "reply_language", "intent", "is_order", "is_injection",
                                    *EXTRACTION_FIELDS)

LANGUAGE_COMPONENT = "tecpap.nlp.language.detect_language"
REPLY_COMPONENT = "tecpap.nlp.language.reply_language"
EXTRACTION_COMPONENT = "tecpap.extraction.rules.rule_based_extract"
REORDER_COMPONENT = "tecpap.extraction.rules.detect_reorder"
INJECTION_COMPONENT = "tecpap.security.guardrails.inspect_input"
INTENT_MODULE = "tecpap.chatbot.intents"
INTENT_FUNCTION = "detect_intent"
INTENT_COMPONENT = f"{INTENT_MODULE}.{INTENT_FUNCTION}"
ONLINE_COMPONENT = "tecpap.extraction.extractor.DataExtractor + tecpap.extraction.rules.normalize_order"

QUANTITY_TOLERANCE = 1e-9
"""Écart relatif toléré entre deux quantités (erreurs d'arrondi des nombres à virgule)."""

WILSON_Z = 1.959963984540054
"""Quantile à 97,5 % de la loi normale centrée réduite : intervalle de confiance à 95 %."""

RATE_DECIMALS = 4
CONFIDENCE_DECIMALS = 2
EVALUATION_SENDER = "whatsapp:+212600000000"
"""Expéditeur fictif des messages WhatsApp en évaluation en ligne (forme des numéros du jeu)."""

NO_VALUE = "—"
UNDEFINED = "n.d."
UNAVAILABLE = "non disponible"
NBSP = "\u00a0"

MISSING_KEY_MESSAGE = (
    "Évaluation en ligne impossible : aucune clé OPENAI_API_KEY n'est configurée "
    "(variable d'environnement ou fichier .env). L'évaluation hors ligne se lance sans l'option --online."
)

JsonDict = dict[str, Any]


# ---------------------------------------------------------------- jeu de données

class DatasetError(ValueError):
    """Jeu de données illisible ou non conforme au schéma de ``evaluation/README.md``."""


@dataclass(frozen=True)
class DatasetItem:
    """Élément du jeu annoté : message et étiquettes attendues."""

    id: str
    category: str
    channel: str
    text: str
    expected: Mapping[str, Any]

    @property
    def language(self) -> str:
        """Langue attendue (une valeur de :data:`LANGUAGES`)."""
        return self.expected["language"]

    @property
    def is_order(self) -> bool:
        """Vrai pour une commande ou un renouvellement."""
        return self.expected["is_order"]

    @property
    def is_reorder(self) -> bool:
        """Vrai pour un renouvellement (intention ``reorder``)."""
        return self.expected["intent"] == REORDER_INTENT

    @property
    def is_injection(self) -> bool:
        """Vrai pour une tentative d'injection d'instructions."""
        return self.expected["is_injection"]

    @property
    def is_trap(self) -> bool:
        """Vrai pour un message légitime qui emploie les mots surveillés par la détection d'injection."""
        return self.category == TRAP_CATEGORY and not self.is_injection


IntentDetector = Callable[..., Any]
"""Fonction ``detect_intent(text, language=None)`` : résultat doté d'un attribut ``intent``, ou texte."""

IntentLoader = Callable[[], tuple[IntentDetector | None, str | None]]
"""Fonction qui fournit le détecteur d'intention, ou ``None`` et la raison de son absence."""

OnlineExtractor = Callable[[DatasetItem], Mapping[str, Any] | None]
"""Fonction qui renvoie la commande extraite d'un élément, ou ``None`` en cas d'échec."""


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _optional(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda value: value is None or check(value)


_LABEL_CHECKS: Mapping[str, tuple[Callable[[Any], bool], str]] = MappingProxyType({
    "language": (lambda value: value in LANGUAGES, f"doit valoir {', '.join(LANGUAGES)}"),
    "reply_language": (lambda value: value in REPLY_LANGUAGES, f"doit valoir {', '.join(REPLY_LANGUAGES)}"),
    "intent": (lambda value: value in INTENTS, f"doit valoir {', '.join(INTENTS)}"),
    "is_order": (lambda value: isinstance(value, bool), "doit être un booléen"),
    "is_injection": (lambda value: isinstance(value, bool), "doit être un booléen"),
    "type_produit": (_optional(lambda value: value in PRODUCT_TYPES), "doit être null ou un produit du catalogue"),
    "quantite": (_optional(_is_number), "doit être null ou un nombre"),
    "unite": (_optional(_is_text), "doit être null ou un texte non vide"),
    "entreprise_cliente": (_optional(_is_text), "doit être null ou un texte non vide"),
})


def _expected_problems(expected: Mapping[str, Any]) -> list[str]:
    missing = [name for name in EXPECTED_FIELDS if name not in expected]
    if missing:
        return [f"étiquettes absentes : {', '.join(missing)}"]
    problems = [f"{name} {description}"
                for name, (check, description) in _LABEL_CHECKS.items() if not check(expected[name])]
    if not problems and expected["is_order"] != (expected["intent"] in ORDER_INTENTS):
        problems.append("is_order doit être vrai exactement pour les intentions order et reorder")
    return problems


def _item_problems(raw: Mapping[str, Any]) -> list[str]:
    problems = [f"{name} doit être un texte non vide" for name in ("id", "category", "text")
                if not _is_text(raw.get(name))]
    if raw.get("channel") not in CHANNELS:
        problems.append(f"channel doit valoir {', '.join(CHANNELS)}")
    expected = raw.get("expected")
    if not isinstance(expected, Mapping):
        problems.append("expected doit être un objet")
    else:
        problems += _expected_problems(expected)
    return problems


def _parse_line(line: str, number: int) -> DatasetItem:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"ligne {number} : JSON invalide (colonne {exc.colno})") from exc
    if not isinstance(raw, dict):
        raise DatasetError(f"ligne {number} : objet JSON attendu")
    problems = _item_problems(raw)
    if problems:
        raise DatasetError(f"ligne {number} : " + " ; ".join(problems))
    return DatasetItem(id=raw["id"], category=raw["category"], channel=raw["channel"], text=raw["text"],
                       expected=MappingProxyType(dict(raw["expected"])))


def load_dataset(path: Path | str) -> list[DatasetItem]:
    """Lit et contrôle le jeu annoté (JSON Lines, UTF-8) ; les lignes vides sont ignorées.

    Raises:
        FileNotFoundError: fichier absent.
        DatasetError: ligne illisible, étiquette hors du vocabulaire du README, identifiant en
            double ou fichier sans élément ; le message cite le numéro de ligne.
    """
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise DatasetError(f"{path.name} : fichier non encodé en UTF-8 (octet {exc.start})") from exc
    items: list[DatasetItem] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        item = _parse_line(line, number)
        if item.id in seen:
            raise DatasetError(f"ligne {number} : identifiant {item.id!r} déjà utilisé")
        seen.add(item.id)
        items.append(item)
    if not items:
        raise DatasetError(f"{path.name} : aucun élément")
    return items


def file_sha256(path: Path | str) -> str:
    """Empreinte SHA-256 du fichier, en hexadécimal."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def display_path(path: Path | str) -> str:
    """Chemin relatif à la racine du dépôt quand le fichier s'y trouve, chemin absolu sinon."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _language_counts(items: Sequence[DatasetItem]) -> JsonDict:
    return {
        "items": len(items),
        "email": sum(item.channel == "email" for item in items),
        "whatsapp": sum(item.channel == "whatsapp" for item in items),
        "orders": sum(item.is_order for item in items),
        "reorders": sum(item.is_reorder for item in items),
        "injections": sum(item.is_injection for item in items),
        "traps": sum(item.is_trap for item in items),
    }


def describe_dataset(items: Sequence[DatasetItem], path: Path | str) -> JsonDict:
    """Effectifs du jeu, au total et par langue, avec le chemin et l'empreinte du fichier."""
    return {
        "path": display_path(path),
        "sha256": file_sha256(path),
        **_language_counts(items),
        "by_language": {language: _language_counts([item for item in items if item.language == language])
                        for language in LANGUAGES},
    }


# ---------------------------------------------------------------- comparaison des valeurs

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", "´": "'"})


def comparable_name(name: str) -> str:
    """Forme de comparaison d'un nom : sans casse, accents, signes diacritiques ni espaces répétées.

    La décomposition Unicode NFKD sépare chaque lettre de ses signes diacritiques (accents
    latins, voyelles brèves et hamza de l'écriture arabe), qui sont ensuite retirés.
    """
    decomposed = unicodedata.normalize("NFKD", name.translate(_APOSTROPHES))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def same_quantity(expected: Any, obtained: Any) -> bool:
    """Égalité numérique de deux quantités (``10000`` et ``10000.0`` sont égales) ; ``None`` égale ``None``."""
    if expected is None or obtained is None:
        return expected is None and obtained is None
    if not (_is_number(expected) and _is_number(obtained)):
        return False
    return math.isclose(expected, obtained, rel_tol=QUANTITY_TOLERANCE, abs_tol=QUANTITY_TOLERANCE)


def same_name(expected: Any, obtained: Any) -> bool:
    """Égalité de deux noms après :func:`comparable_name` ; ``None`` égale ``None``."""
    if expected is None or obtained is None:
        return expected is None and obtained is None
    if not (isinstance(expected, str) and isinstance(obtained, str)):
        return False
    return comparable_name(expected) == comparable_name(obtained)


def same_value(expected: Any, obtained: Any) -> bool:
    """Égalité exacte, sans conversion de type."""
    return type(expected) is type(obtained) and expected == obtained


FIELD_COMPARATORS: Mapping[str, Callable[[Any, Any], bool]] = MappingProxyType({
    "type_produit": same_value,
    "quantite": same_quantity,
    "unite": same_value,
    "entreprise_cliente": same_name,
})


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


# ---------------------------------------------------------------- mesures

def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _rounded(value: float | None, decimals: int = RATE_DECIMALS) -> float | None:
    return None if value is None else round(value, decimals)


def wilson_interval(successes: int, total: int, z: float = WILSON_Z) -> tuple[float, float] | None:
    """Intervalle de confiance de Wilson d'une proportion ``successes / total`` ; ``None`` si ``total`` est nul."""
    if total <= 0:
        return None
    proportion = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    center = (proportion + z2 / (2 * total)) / denominator
    half_width = z * math.sqrt(proportion * (1 - proportion) / total + z2 / (4 * total * total)) / denominator
    return max(0.0, center - half_width), min(1.0, center + half_width)


@dataclass
class Ratio:
    """Proportion de réponses justes."""

    correct: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        """Compte une réponse, juste ou non."""
        self.total += 1
        self.correct += bool(ok)

    @property
    def rate(self) -> float | None:
        """``correct / total``, ou ``None`` sans élément."""
        return _ratio(self.correct, self.total)

    def as_dict(self) -> JsonDict:
        """Effectifs et proportion arrondie, pour le JSON."""
        return {"correct": self.correct, "total": self.total, "rate": _rounded(self.rate)}


@dataclass
class BinaryCounts:
    """Effectifs d'une détection oui / non : vrais et faux positifs, faux et vrais négatifs."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, expected: bool, predicted: bool) -> None:
        """Compte un élément d'après son étiquette et la décision du détecteur."""
        if expected and predicted:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif expected:
            self.fn += 1
        else:
            self.tn += 1

    @property
    def total(self) -> int:
        """Nombre d'éléments comptés."""
        return self.tp + self.fp + self.fn + self.tn

    @property
    def accuracy(self) -> float | None:
        """(VP + VN) / total."""
        return _ratio(self.tp + self.tn, self.total)

    @property
    def precision(self) -> float | None:
        """VP / (VP + FP)."""
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        """VP / (VP + FN)."""
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float | None:
        """2 VP / (2 VP + FP + FN), moyenne harmonique de la précision et du rappel."""
        return _ratio(2 * self.tp, 2 * self.tp + self.fp + self.fn)

    @property
    def false_positive_rate(self) -> float | None:
        """FP / (FP + VN)."""
        return _ratio(self.fp, self.fp + self.tn)

    def as_dict(self) -> JsonDict:
        """Effectifs et mesures arrondies, pour le JSON."""
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn, "total": self.total,
            "accuracy": _rounded(self.accuracy), "precision": _rounded(self.precision),
            "recall": _rounded(self.recall), "f1": _rounded(self.f1),
            "false_positive_rate": _rounded(self.false_positive_rate),
        }


def confusion_matrix(pairs: Iterable[tuple[str, str]], rows: Sequence[str], columns: Sequence[str]) -> JsonDict:
    """Tableau croisé (attendu, obtenu) ; les valeurs hors de ``rows`` / ``columns`` sont ajoutées à la fin, triées."""
    counts = Counter(pairs)
    row_labels = [*rows, *sorted({expected for expected, _ in counts} - set(rows))]
    column_labels = [*columns, *sorted({obtained for _, obtained in counts} - set(columns))]
    return {
        "rows": row_labels,
        "columns": column_labels,
        "counts": {row: {column: counts[(row, column)] for column in column_labels} for row in row_labels},
    }


# ---------------------------------------------------------------- 1. langue

def evaluate_language(items: Sequence[DatasetItem], detections: Mapping[str, LanguageResult]) -> JsonDict:
    """Exactitude du code de langue et de la langue de réponse, au total et par langue attendue."""
    code_total, reply_total = Ratio(), Ratio()
    by_language = {language: {"code": Ratio(), "reply_language": Ratio()} for language in LANGUAGES}
    pairs: list[tuple[str, str]] = []
    errors: list[JsonDict] = []
    reply_errors: list[JsonDict] = []
    for item in items:
        result = detections[item.id]
        obtained_reply = reply_language(result)
        code_ok = result.code == item.language
        reply_ok = obtained_reply == item.expected["reply_language"]
        code_total.add(code_ok)
        reply_total.add(reply_ok)
        by_language[item.language]["code"].add(code_ok)
        by_language[item.language]["reply_language"].add(reply_ok)
        pairs.append((item.language, str(result.code)))
        if not code_ok:
            errors.append({"id": item.id, "expected": item.language, "obtained": str(result.code),
                           "confidence": _rounded(result.confidence, CONFIDENCE_DECIMALS)})
        if not reply_ok:
            reply_errors.append({"id": item.id, "language": item.language,
                                 "expected": item.expected["reply_language"], "obtained": obtained_reply})
    return {
        "component": LANGUAGE_COMPONENT,
        "reply_component": REPLY_COMPONENT,
        "items": len(items),
        "code": code_total.as_dict(),
        "reply_language": reply_total.as_dict(),
        "by_language": {language: {name: ratio.as_dict() for name, ratio in ratios.items()}
                        for language, ratios in by_language.items()},
        "confusion_matrix": confusion_matrix(pairs, LANGUAGES, (*LANGUAGES, UNKNOWN_LANGUAGE)),
        "errors": errors,
        "reply_errors": reply_errors,
    }


# ---------------------------------------------------------------- 2. champs d'une commande

def _field_results(item: DatasetItem, obtained: Mapping[str, Any] | None) -> dict[str, bool]:
    if obtained is None:
        return {name: False for name in EXTRACTION_FIELDS}
    return {name: FIELD_COMPARATORS[name](item.expected[name], obtained.get(name)) for name in EXTRACTION_FIELDS}


def compare_orders(orders: Sequence[DatasetItem], obtained: Mapping[str, Mapping[str, Any] | None],
                   *, component: str) -> JsonDict:
    """Compare les champs extraits de chaque commande aux étiquettes.

    Args:
        orders: commandes du jeu.
        obtained: champs extraits par identifiant ; une valeur ``None`` ou un identifiant absent
            représente une extraction en échec, dont tous les champs sont comptés faux.
        component: nom du composant mesuré, recopié dans le rapport.
    """
    measured = (*EXTRACTION_FIELDS, "required_fields")
    totals = {name: Ratio() for name in measured}
    by_language = {language: {name: Ratio() for name in measured} for language in LANGUAGES}
    errors: dict[str, list[JsonDict]] = {name: [] for name in EXTRACTION_FIELDS}
    required_errors: list[str] = []
    for item in orders:
        values = obtained.get(item.id)
        results = _field_results(item, values)
        results["required_fields"] = all(results[name] for name in REQUIRED_FIELDS)
        for name in measured:
            totals[name].add(results[name])
            by_language[item.language][name].add(results[name])
        for name in EXTRACTION_FIELDS:
            if not results[name]:
                errors[name].append({"id": item.id, "language": item.language, "expected": item.expected[name],
                                     "obtained": _json_value((values or {}).get(name))})
        if not results["required_fields"]:
            required_errors.append(item.id)
    return {
        "component": component,
        "items": len(orders),
        "fields": {name: {**totals[name].as_dict(), "errors": errors[name]} for name in EXTRACTION_FIELDS},
        "required_fields": {"fields": list(REQUIRED_FIELDS), **totals["required_fields"].as_dict(),
                            "errors": required_errors},
        "by_language": {language: {"items": ratios["required_fields"].total,
                                   **{name: ratio.as_dict() for name, ratio in ratios.items()}}
                        for language, ratios in by_language.items()},
    }


# ---------------------------------------------------------------- 3 et 4. détections oui / non

def evaluate_binary(items: Sequence[DatasetItem], expected_of: Callable[[DatasetItem], bool],
                    predicted_of: Callable[[DatasetItem], bool], *, component: str,
                    detail_of: Callable[[DatasetItem], JsonDict] | None = None) -> JsonDict:
    """Mesures d'une détection oui / non, au total et par langue, avec la liste des erreurs.

    ``detail_of`` ajoute des informations à chaque erreur listée (le score, par exemple).
    """
    counts = BinaryCounts()
    by_language = {language: BinaryCounts() for language in LANGUAGES}
    false_positives: list[JsonDict] = []
    false_negatives: list[JsonDict] = []
    for item in items:
        expected, predicted = bool(expected_of(item)), bool(predicted_of(item))
        counts.add(expected, predicted)
        by_language[item.language].add(expected, predicted)
        if expected != predicted:
            entry = {"id": item.id, "language": item.language, **(detail_of(item) if detail_of else {})}
            (false_positives if predicted else false_negatives).append(entry)
    return {
        "component": component,
        "items": len(items),
        "positives": counts.tp + counts.fn,
        **counts.as_dict(),
        "by_language": {language: language_counts.as_dict() for language, language_counts in by_language.items()},
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def _false_alerts(items: Sequence[DatasetItem], checks: Mapping[str, InputCheck]) -> JsonDict:
    flagged = Ratio()
    for item in items:
        flagged.add(checks[item.id].is_suspicious)
    return {"items": flagged.total, "false_positives": flagged.correct, "rate": _rounded(flagged.rate)}


def _extreme_score(items: Sequence[DatasetItem], checks: Mapping[str, InputCheck], *, highest: bool) -> JsonDict | None:
    if not items:
        return None
    choose = max if highest else min
    item = choose(items, key=lambda candidate: checks[candidate.id].score)
    return {"id": item.id, "score": checks[item.id].score}


def evaluate_injection(items: Sequence[DatasetItem], checks: Mapping[str, InputCheck]) -> JsonDict:
    """Détection des tentatives d'injection : mesures oui / non, fausses alertes et scores extrêmes."""
    section = evaluate_binary(items, lambda item: item.is_injection, lambda item: checks[item.id].is_suspicious,
                              component=INJECTION_COMPONENT,
                              detail_of=lambda item: {"score": checks[item.id].score, "trap": item.is_trap})
    legitimate = [item for item in items if not item.is_injection]
    injections = [item for item in items if item.is_injection]
    traps = [item for item in items if item.is_trap]
    for language in LANGUAGES:
        trap_alerts = _false_alerts([item for item in traps if item.language == language], checks)
        section["by_language"][language].update(traps=trap_alerts["items"],
                                                trap_false_positives=trap_alerts["false_positives"])
    section.update(
        threshold=SUSPICION_THRESHOLD,
        traps=_false_alerts(traps, checks),
        non_injection=_false_alerts(legitimate, checks),
        max_legitimate_score=_extreme_score(legitimate, checks, highest=True),
        min_injection_score=_extreme_score(injections, checks, highest=False),
    )
    return section


# ---------------------------------------------------------------- 5. intentions

def load_intent_detector(module_name: str = INTENT_MODULE) -> tuple[IntentDetector | None, str | None]:
    """Importe la fonction ``detect_intent`` du module ; renvoie la fonction, ou ``None`` et la raison.

    Le module peut être absent ou incomplet : toute erreur d'importation rend la section des
    intentions non disponible au lieu d'interrompre l'évaluation.
    """
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        logger.info("Module %s introuvable : section des intentions non disponible", module_name)
        return None, f"module {exc.name or module_name} introuvable"
    except Exception as exc:
        logger.warning("Module %s non importable", module_name, exc_info=True)
        return None, f"module {module_name} non importable ({type(exc).__name__})"
    detector = getattr(module, INTENT_FUNCTION, None)
    if not callable(detector):
        return None, f"fonction {INTENT_FUNCTION} absente du module {module_name}"
    return detector, None


def unavailable_intents(items: Sequence[DatasetItem], reason: str | None) -> JsonDict:
    """Section des intentions marquée non disponible, avec sa raison."""
    return {"available": False, "reason": reason or "aucun détecteur d'intention fourni",
            "component": INTENT_COMPONENT, "items": len(items)}


def _intent_label(result: Any) -> str:
    label = getattr(result, "intent", result)
    return label if isinstance(label, str) else str(label)


def _intent_predictions(items: Sequence[DatasetItem], detector: IntentDetector,
                        languages: Mapping[str, str]) -> tuple[dict[str, str], str | None]:
    predictions: dict[str, str] = {}
    for item in items:
        try:
            predictions[item.id] = _intent_label(detector(item.text, language=languages[item.id]))
        except Exception as exc:
            logger.warning("Échec de %s sur l'élément %s", INTENT_COMPONENT, item.id, exc_info=True)
            return predictions, f"erreur de {INTENT_FUNCTION} sur l'élément {item.id} ({type(exc).__name__})"
    return predictions, None


def _per_intent(items: Sequence[DatasetItem], predictions: Mapping[str, str]) -> dict[str, BinaryCounts]:
    per_intent = {intent: BinaryCounts() for intent in INTENTS}
    for item in items:
        for intent, counts in per_intent.items():
            counts.add(item.expected["intent"] == intent, predictions[item.id] == intent)
    return per_intent


def evaluate_intents(items: Sequence[DatasetItem], detector: IntentDetector, languages: Mapping[str, str]) -> JsonDict:
    """Exactitude, F1 par intention, macro-F1, matrice de confusion et erreurs de la détection d'intention.

    ``languages`` donne, par identifiant, la langue détectée passée au détecteur. Une exception
    levée par le détecteur rend la section non disponible.
    """
    predictions, failure = _intent_predictions(items, detector, languages)
    if failure:
        return unavailable_intents(items, failure)
    accuracy = Ratio()
    by_language = {language: Ratio() for language in LANGUAGES}
    errors: list[JsonDict] = []
    for item in items:
        expected, obtained = item.expected["intent"], predictions[item.id]
        accuracy.add(obtained == expected)
        by_language[item.language].add(obtained == expected)
        if obtained != expected:
            errors.append({"id": item.id, "language": item.language, "expected": expected, "obtained": obtained})
    per_intent = _per_intent(items, predictions)
    present = [counts.f1 for counts in per_intent.values() if counts.tp + counts.fn and counts.f1 is not None]
    return {
        "available": True,
        "reason": None,
        "component": INTENT_COMPONENT,
        "items": len(items),
        "accuracy": accuracy.as_dict(),
        "macro_f1": _rounded(sum(present) / len(present)) if present else None,
        "per_intent": {intent: {"support": counts.tp + counts.fn, "predicted": counts.tp + counts.fp,
                                "tp": counts.tp, "fp": counts.fp, "fn": counts.fn,
                                "precision": _rounded(counts.precision), "recall": _rounded(counts.recall),
                                "f1": _rounded(counts.f1)}
                       for intent, counts in per_intent.items()},
        "by_language": {language: ratio.as_dict() for language, ratio in by_language.items()},
        "confusion_matrix": confusion_matrix(((item.expected["intent"], predictions[item.id]) for item in items),
                                             INTENTS, INTENTS),
        "errors": errors,
    }


# ---------------------------------------------------------------- synthèse

def summary_entry(component: str, measure: str, numerator: int | None = None, denominator: int | None = None,
                  value: float | None = None, *, note: str | None = None) -> JsonDict:
    """Ligne de synthèse ; l'intervalle de Wilson est calculé quand la mesure est une proportion."""
    if value is None and numerator is not None and denominator:
        value = numerator / denominator
    interval = wilson_interval(numerator, denominator) if numerator is not None and denominator else None
    return {
        "component": component,
        "measure": measure,
        "numerator": numerator,
        "denominator": denominator,
        "value": _rounded(value),
        "ci95": [_rounded(bound) for bound in interval] if interval else None,
        "note": note,
    }


def _ratio_entry(component: str, measure: str, ratio: Mapping[str, Any]) -> JsonDict:
    return summary_entry(component, measure, ratio["correct"], ratio["total"])


def _binary_entries(component: str, section: Mapping[str, Any]) -> list[JsonDict]:
    tp, fp, fn, tn = (section[key] for key in ("tp", "fp", "fn", "tn"))
    return [
        summary_entry(component, "Exactitude", tp + tn, section["total"]),
        summary_entry(component, "Précision", tp, tp + fp),
        summary_entry(component, "Rappel", tp, tp + fn),
        summary_entry(component, "F1", value=section["f1"]),
    ]


def _extraction_entries(component: str, section: Mapping[str, Any]) -> list[JsonDict]:
    entries = [_ratio_entry(component, f"Exactitude de `{name}`", section["fields"][name])
               for name in EXTRACTION_FIELDS]
    entries.append(_ratio_entry(component, "Exactitude des champs obligatoires", section["required_fields"]))
    return entries


INJECTION_LABEL = "Détection des injections"


def _intent_entries(section: Mapping[str, Any]) -> list[JsonDict]:
    component = "Détection des intentions"
    if not section["available"]:
        return [summary_entry(component, "Exactitude", note=UNAVAILABLE)]
    return [_ratio_entry(component, "Exactitude", section["accuracy"]),
            summary_entry(component, "Macro-F1", value=section["macro_f1"])]


def offline_summary(report: Mapping[str, Any]) -> list[JsonDict]:
    """Lignes de la synthèse du rapport hors ligne, calculées à partir des sections."""
    language, injection = report["language"], report["injection"]
    tp, fp, fn, tn = (injection[key] for key in ("tp", "fp", "fn", "tn"))
    traps = injection["traps"]
    return [
        _ratio_entry("Détection de la langue", "Exactitude du code de langue", language["code"]),
        _ratio_entry("Détection de la langue", "Exactitude de la langue de réponse", language["reply_language"]),
        *_extraction_entries("Extraction par règles", report["extraction"]),
        *_binary_entries("Détection des commandes", report["order_detection"]),
        *_binary_entries("Détection des renouvellements", report["reorder_detection"]),
        summary_entry(INJECTION_LABEL, "Précision", tp, tp + fp),
        summary_entry(INJECTION_LABEL, "Rappel", tp, tp + fn),
        summary_entry(INJECTION_LABEL, "F1", value=injection["f1"]),
        summary_entry(INJECTION_LABEL, "Taux de faux positifs", fp, fp + tn),
        summary_entry(INJECTION_LABEL, "Fausses alertes sur les pièges", traps["false_positives"], traps["items"]),
        *_intent_entries(report["intent"]),
    ]


# ---------------------------------------------------------------- rapports

def build_offline_report(items: Sequence[DatasetItem], *, dataset_path: Path | str, run_date: date,
                         intent_loader: IntentLoader = load_intent_detector) -> JsonDict:
    """Évalue tous les composants hors ligne et renvoie le contenu du rapport JSON."""
    detections = {item.id: detect_language(item.text) for item in items}
    extractions = {item.id: rule_based_extract(item.text, today=run_date) for item in items}
    checks = {item.id: inspect_input(item.text) for item in items}
    orders = [item for item in items if item.is_order]
    detector, reason = intent_loader()
    report: JsonDict = {
        "report": "hors_ligne",
        "run_date": run_date.isoformat(),
        "package_version": __version__,
        "dataset": describe_dataset(items, dataset_path),
        "language": evaluate_language(items, detections),
        "extraction": compare_orders(orders, {item.id: extractions[item.id] for item in orders},
                                     component=EXTRACTION_COMPONENT),
        "order_detection": evaluate_binary(
            items, lambda item: item.is_order,
            lambda item: to_bool(extractions[item.id].get("est_bon_commande")) is True,
            component=f"{EXTRACTION_COMPONENT} (est_bon_commande)"),
        "reorder_detection": evaluate_binary(items, lambda item: item.is_reorder,
                                             lambda item: detect_reorder(item.text), component=REORDER_COMPONENT),
        "injection": evaluate_injection(items, checks),
        "intent": (evaluate_intents(items, detector, {key: result.code for key, result in detections.items()})
                   if detector is not None else unavailable_intents(items, reason)),
    }
    report["summary"] = offline_summary(report)
    return report


def message_for_extraction(item: DatasetItem, run_date: date) -> dict[str, str]:
    """Message présenté à l'extracteur comme dans la chaîne de traitement.

    Un message WhatsApp reprend la présentation de
    :meth:`tecpap.channels.whatsapp.WhatsAppClient.format_for_extraction` (préfixe « Message: »,
    ou « Audio transcription: » pour une note vocale), avec un expéditeur fictif.
    """
    if item.channel == "whatsapp":
        prefix = "Audio transcription" if item.category == VOICE_CATEGORY else "Message"
        return {"subject": f"WhatsApp - {EVALUATION_SENDER}", "from": EVALUATION_SENDER,
                "date": run_date.isoformat(), "body": f"{prefix}: {item.text}", "source": "whatsapp"}
    return {"subject": "", "from": "", "date": run_date.isoformat(), "body": item.text}


def build_online_extractor(settings: Settings, llm: Any, run_date: date) -> OnlineExtractor:
    """Fonction d'extraction complète par le modèle de langue, sans base de données (pas d'historique client)."""
    # Import différé : l'évaluation hors ligne ne dépend pas de l'extracteur et de ses dépendances.
    from tecpap.extraction.extractor import DataExtractor

    extractor = DataExtractor(db_manager=None, settings=settings, llm=llm)

    def extract(item: DatasetItem) -> Mapping[str, Any] | None:
        return extractor.extract_from_email(message_for_extraction(item, run_date))

    return extract


def run_online_extraction(orders: Sequence[DatasetItem], extract: OnlineExtractor,
                          run_date: date) -> tuple[dict[str, NormalizationResult], list[str]]:
    """Extrait puis normalise chaque commande ; renvoie les résultats par identifiant et les échecs."""
    results: dict[str, NormalizationResult] = {}
    failures: list[str] = []
    for item in orders:
        try:
            raw = extract(item)
        except Exception:
            logger.warning("Extraction en échec pour l'élément %s", item.id, exc_info=True)
            raw = None
        if not isinstance(raw, Mapping):
            failures.append(item.id)
            continue
        results[item.id] = normalize_order(raw, source_text=item.text, today=run_date)
    return results, failures


def _normalization_stats(results: Mapping[str, NormalizationResult]) -> JsonDict:
    confidences = [result.confidence for result in results.values()]
    with_anomalies = Ratio(sum(bool(result.anomalies) for result in results.values()), len(results))
    with_missing = Ratio(sum(bool(result.missing_fields) for result in results.values()), len(results))
    return {
        "successes": len(results),
        "mean_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        "with_anomalies": with_anomalies.as_dict(),
        "with_missing_fields": with_missing.as_dict(),
    }


def _order_recognition(orders: Sequence[DatasetItem], data: Mapping[str, Mapping[str, Any]]) -> JsonDict:
    recognized = Ratio()
    missed: list[str] = []
    for item in orders:
        ok = item.id in data and to_bool(data[item.id].get("est_bon_commande")) is True
        recognized.add(ok)
        if not ok:
            missed.append(item.id)
    return {**recognized.as_dict(), "errors": missed}


def online_summary(report: Mapping[str, Any]) -> list[JsonDict]:
    """Lignes de la synthèse du rapport en ligne."""
    component = "Extraction complète"
    failures, items = len(report["failures"]), report["extraction"]["items"]
    normalization = report["normalization"]
    return [
        *_extraction_entries(component, report["extraction"]),
        _ratio_entry(component, "Rappel de `est_bon_commande`", report["order_recognition"]),
        summary_entry(component, "Extractions en échec", failures, items),
        _ratio_entry(component, "Commandes avec anomalie", normalization["with_anomalies"]),
        _ratio_entry(component, "Commandes avec champ obligatoire manquant", normalization["with_missing_fields"]),
    ]


def build_online_report(items: Sequence[DatasetItem], *, dataset_path: Path | str, run_date: date,
                        extract: OnlineExtractor, models: Mapping[str, Any]) -> JsonDict:
    """Évalue l'extraction complète sur les commandes du jeu et renvoie le contenu du rapport JSON."""
    orders = [item for item in items if item.is_order]
    results, failures = run_online_extraction(orders, extract, run_date)
    data = {key: result.data for key, result in results.items()}
    report: JsonDict = {
        "report": "en_ligne",
        "run_date": run_date.isoformat(),
        "package_version": __version__,
        "models": dict(models),
        "dataset": describe_dataset(items, dataset_path),
        "extraction": compare_orders(orders, data, component=ONLINE_COMPONENT),
        "order_recognition": _order_recognition(orders, data),
        "failures": failures,
        "normalization": _normalization_stats(results),
    }
    report["summary"] = online_summary(report)
    return report


# ---------------------------------------------------------------- mise en forme Markdown

Block = list[str]

MEASURE_DEFINITIONS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "accuracy": ("Exactitude", "Part des éléments dont la valeur obtenue est égale à l'étiquette attendue."),
    "interval": ("Intervalle de confiance à 95 %",
                 "Plage de valeurs de la proportion réelle compatibles avec l'échantillon (méthode de Wilson). "
                 "Plus l'effectif est petit, plus la plage est large."),
    "confusion": ("Matrice de confusion",
                  "Tableau croisé : une ligne par étiquette attendue, une colonne par valeur obtenue. "
                  "Les bonnes réponses sont sur la diagonale."),
    "positive": ("Classe positive", "Catégorie que le détecteur doit signaler (commande, tentative d'injection…)."),
    "tp": ("VP (vrai positif)", "Cas positif signalé."),
    "fp": ("FP (faux positif)", "Cas négatif signalé à tort : fausse alerte."),
    "fn": ("FN (faux négatif)", "Cas positif manqué."),
    "tn": ("VN (vrai négatif)", "Cas négatif non signalé."),
    "precision": ("Précision", "VP / (VP + FP) : part des signalements justes."),
    "recall": ("Rappel", "VP / (VP + FN) : part des cas positifs signalés."),
    "f1": ("F1", "2 VP / (2 VP + FP + FN) : moyenne harmonique de la précision et du rappel."),
    "fpr": ("Taux de faux positifs", "FP / (FP + VN) : part des cas négatifs signalés à tort."),
    "macro_f1": ("Macro-F1",
                 "Moyenne non pondérée des F1 des classes présentes dans le jeu, chaque classe étant tour à tour "
                 "la classe positive."),
    "confidence": ("Confiance calculée",
                   "Note de 0 à 100 donnée par `tecpap.extraction.rules.normalize_order` : moyenne de la "
                   "complétude des champs obligatoires et de la confiance déclarée par le modèle, moins 10 points "
                   "par anomalie, sans dépasser la complétude augmentée de 10 points."),
})
OFFLINE_MEASURES = ("accuracy", "interval", "confusion", "positive", "tp", "fp", "fn", "tn", "precision", "recall",
                    "f1", "fpr", "macro_f1")
ONLINE_MEASURES = ("accuracy", "interval", "positive", "recall", "confidence")

FIELD_COMPARISONS: Mapping[str, str] = MappingProxyType({
    "type_produit": "Égalité exacte du nom du produit du catalogue.",
    "quantite": "Égalité numérique (10000 et 10000,0 sont égales).",
    "unite": "Égalité exacte. L'étiquette vaut `US` dès qu'une quantité est donnée "
             "(`evaluation/README.md`, section 4.7).",
    "entreprise_cliente": "Égalité sans tenir compte de la casse, des accents, des signes diacritiques "
                          "ni des espaces répétées.",
})


def format_percent(rate: float | None) -> str:
    """Proportion en pourcentage à une décimale, à la française (« 97,8 % ») ; « n.d. » sans valeur."""
    if rate is None:
        return UNDEFINED
    return f"{rate * 100:.1f}".replace(".", ",") + f"{NBSP}%"


def format_interval(interval: Sequence[float] | None) -> str:
    """Intervalle en pourcentages (« [95,9 % ; 100,0 %] ») ; « — » sans intervalle."""
    if not interval:
        return NO_VALUE
    low, high = interval
    return f"[{format_percent(low)} ; {format_percent(high)}]"


def format_number(value: int | float) -> str:
    """Nombre sans zéro inutile, avec la virgule décimale."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value) if isinstance(value, int) else repr(value).replace(".", ",")


def _escape(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def cell(value: Any) -> str:
    """Contenu d'une case de tableau Markdown : « — » pour une valeur absente, barres verticales échappées."""
    if value is None:
        return NO_VALUE
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, (int, float)):
        return format_number(value)
    return _escape(str(value)) or NO_VALUE


def code(value: Any) -> str:
    """Valeur affichée comme du code (``fr``, ``order``…) ; une apostrophe inversée devient une apostrophe."""
    if value is None:
        return NO_VALUE
    text = _escape(str(value)).replace("`", "'")
    return f"`{text}`" if text else NO_VALUE


def fraction(numerator: int | None, denominator: int | None) -> str:
    """Effectifs « numérateur/dénominateur », ou « — »."""
    return NO_VALUE if numerator is None or denominator is None else f"{numerator}/{denominator}"


def table(headers: Sequence[str], rows: Iterable[Sequence[str]], align: str = "") -> Block:
    """Tableau Markdown ; ``align`` donne l'alignement de chaque colonne (``l`` à gauche, ``r`` à droite)."""
    separators = ["---:" if align[index:index + 1] == "r" else "---" for index in range(len(headers))]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(separators) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _heading(level: int, text: str) -> Block:
    return ["#" * level + " " + text]


def _join(blocks: Iterable[Block]) -> str:
    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"


def _id_list(identifiers: Sequence[str]) -> Block:
    return [", ".join(code(identifier) for identifier in identifiers) + "." if identifiers else "Aucune erreur."]


def _error_table(entries: Sequence[Mapping[str, Any]],
                 columns: Sequence[tuple[str, Callable[[Mapping[str, Any]], str]]]) -> Block:
    if not entries:
        return ["Aucune erreur."]
    return table([header for header, _ in columns], ([render(entry) for _, render in columns] for entry in entries))


def _id_cell(entry: Mapping[str, Any]) -> str:
    return code(entry["id"])


def _language_cell(entry: Mapping[str, Any]) -> str:
    return code(entry["language"])


def _run_information(report: Mapping[str, Any], extra_rows: Sequence[Sequence[str]] = ()) -> Block:
    dataset = report["dataset"]
    rows = [
        ["Date de l'exécution", report["run_date"]],
        ["Jeu de données", code(dataset["path"])],
        ["Empreinte SHA-256 du fichier", code(dataset["sha256"])],
        ["Éléments du jeu", str(dataset["items"])],
        ["Version du paquet `tecpap`", cell(report["package_version"])],
        *extra_rows,
    ]
    return table(["Information", "Valeur"], rows)


SHA_NOTE = ("L'empreinte SHA-256 est une somme de contrôle du fichier : deux rapports qui portent la même "
            "empreinte ont été calculés sur exactement les mêmes données.")


def _dataset_blocks(dataset: Mapping[str, Any]) -> list[Block]:
    legend = ", ".join(f"{code(language)} {name}" for language, name in LANGUAGE_NAMES.items())
    keys = ("items", "email", "whatsapp", "orders", "reorders", "injections", "traps")
    rows = [[code(language), *(str(counts[key]) for key in keys)]
            for language, counts in dataset["by_language"].items()]
    rows.append(["Total", *(str(dataset[key]) for key in keys)])
    return [
        [f"Codes de langue : {legend}."],
        table(["Langue", "Éléments", "Email", "WhatsApp", "Commandes", "Renouvellements", "Injections", "Pièges"],
              rows, "lrrrrrrr"),
        ["« Commandes » : éléments dont l'étiquette `is_order` est vraie, renouvellements compris. "
         "« Renouvellements » : intention `reorder`. « Injections » : tentatives d'injection d'instructions "
         "(`is_injection` vrai). « Pièges » : messages légitimes qui emploient les mots surveillés par la "
         "détection d'injection (catégorie `piege_faux_positif`)."],
    ]


def _definition_blocks(keys: Sequence[str]) -> list[Block]:
    rows = [[f"**{MEASURE_DEFINITIONS[key][0]}**", MEASURE_DEFINITIONS[key][1]] for key in keys]
    return [
        table(["Mesure", "Définition"], rows),
        ["« Effectifs » : numérateur et dénominateur de la mesure. « n.d. » : mesure non définie "
         "(dénominateur nul). « — » : aucune valeur."],
    ]


def _summary_blocks(entries: Sequence[Mapping[str, Any]]) -> list[Block]:
    rows = [[entry["component"], entry["measure"], fraction(entry["numerator"], entry["denominator"]),
             entry["note"] or format_percent(entry["value"]), format_interval(entry["ci95"])]
            for entry in entries]
    return [table(["Composant", "Mesure", "Effectifs", "Valeur", "Intervalle de confiance à 95 %"], rows, "llrrr")]


def _confusion_table(matrix: Mapping[str, Any]) -> Block:
    columns = matrix["columns"]
    rows = []
    for row in matrix["rows"]:
        counts = matrix["counts"][row]
        rows.append([code(row), *(str(counts[column]) for column in columns), str(sum(counts.values()))])
    return table(["Attendu", *(code(column) for column in columns), "Total"], rows, "l" + "r" * (len(columns) + 1))


def _language_blocks(section: Mapping[str, Any], number: int) -> list[Block]:
    rows = [[code(language), str(entry["code"]["total"]), str(entry["code"]["correct"]),
             format_percent(entry["code"]["rate"]), str(entry["reply_language"]["correct"]),
             format_percent(entry["reply_language"]["rate"])]
            for language, entry in section["by_language"].items()]
    rows.append(["Total", str(section["code"]["total"]), str(section["code"]["correct"]),
                 format_percent(section["code"]["rate"]), str(section["reply_language"]["correct"]),
                 format_percent(section["reply_language"]["rate"])])
    return [
        [f"Composant : {code(section['component'])}, puis {code(section['reply_component'])} pour la langue de "
         f"réponse. Étiquettes comparées : `language` et `reply_language`, sur les {section['items']} éléments "
         "du jeu."],
        _heading(3, f"{number}.1 Exactitude par langue"),
        table(["Langue", "Éléments", "Code juste", "Exactitude du code", "Réponse juste", "Exactitude de la réponse"],
              rows, "lrrrrr"),
        _heading(3, f"{number}.2 Matrice de confusion du code de langue"),
        ["Lignes : langue attendue. Colonnes : langue obtenue."],
        _confusion_table(section["confusion_matrix"]),
        _heading(3, f"{number}.3 Éléments mal classés"),
        ["Code de langue :"],
        _error_table(section["errors"], [("Élément", _id_cell), ("Attendu", lambda entry: code(entry["expected"])),
                                         ("Obtenu", lambda entry: code(entry["obtained"])),
                                         ("Confiance", lambda entry: cell(entry["confidence"]))]),
        ["Langue de réponse :"],
        _error_table(section["reply_errors"], [("Élément", _id_cell), ("Langue", _language_cell),
                                               ("Attendu", lambda entry: code(entry["expected"])),
                                               ("Obtenu", lambda entry: code(entry["obtained"]))]),
    ]


def _extraction_blocks(section: Mapping[str, Any], number: int, intro: str) -> list[Block]:
    required = ", ".join(code(name) for name in section["required_fields"]["fields"])
    comparisons = [[code(name), FIELD_COMPARISONS[name]] for name in EXTRACTION_FIELDS]
    comparisons.append(["Champs obligatoires",
                        f"Les champs obligatoires d'une commande ({required}) sont tous justes."])
    measured = [(code(name), section["fields"][name]) for name in EXTRACTION_FIELDS]
    measured.append(("Champs obligatoires", section["required_fields"]))
    field_rows = [[label, str(ratio["correct"]), str(ratio["total"]), format_percent(ratio["rate"])]
                  for label, ratio in measured]
    language_rows = [[code(language), str(entry["items"]),
                      *(fraction(entry[name]["correct"], entry[name]["total"])
                        for name in (*EXTRACTION_FIELDS, "required_fields"))]
                     for language, entry in section["by_language"].items()]
    blocks = [
        [intro],
        table(["Champ", "Comparaison"], comparisons),
        ["Une valeur absente est juste quand l'étiquette vaut `null`."],
        _heading(3, f"{number}.1 Exactitude par champ"),
        table(["Champ", "Justes", "Total", "Exactitude"], field_rows, "lrrr"),
        _heading(3, f"{number}.2 Exactitude par langue"),
        table(["Langue", "Commandes", *(code(name) for name in EXTRACTION_FIELDS), "Champs obligatoires"],
              language_rows, "lrrrrrr"),
        _heading(3, f"{number}.3 Erreurs par champ"),
    ]
    for name in EXTRACTION_FIELDS:
        blocks.append(_heading(4, code(name)))
        blocks.append(_error_table(section["fields"][name]["errors"],
                                   [("Élément", _id_cell), ("Langue", _language_cell),
                                    ("Attendu", lambda entry: cell(entry["expected"])),
                                    ("Obtenu", lambda entry: cell(entry["obtained"]))]))
    blocks.append(_heading(4, "Champs obligatoires"))
    blocks.append(["Commandes dont au moins un champ obligatoire est faux :"])
    blocks.append(_id_list(section["required_fields"]["errors"]))
    return blocks


def _binary_measure_rows(section: Mapping[str, Any]) -> list[list[str]]:
    tp, fp, fn, tn = (section[key] for key in ("tp", "fp", "fn", "tn"))
    return [
        ["Exactitude", fraction(tp + tn, section["total"]), format_percent(section["accuracy"])],
        ["Précision", fraction(tp, tp + fp), format_percent(section["precision"])],
        ["Rappel", fraction(tp, tp + fn), format_percent(section["recall"])],
        ["F1", NO_VALUE, format_percent(section["f1"])],
        ["Taux de faux positifs", fraction(fp, fp + tn), format_percent(section["false_positive_rate"])],
    ]


def _counts_table(section: Mapping[str, Any]) -> Block:
    return table(["VP", "FP", "FN", "VN"], [[str(section[key]) for key in ("tp", "fp", "fn", "tn")]], "rrrr")


def _binary_blocks(section: Mapping[str, Any], prefix: str, intro: str) -> list[Block]:
    language_rows = [[code(language), str(counts["total"]), str(counts["tp"] + counts["tn"]),
                      format_percent(counts["accuracy"]), str(counts["fp"]), str(counts["fn"])]
                     for language, counts in section["by_language"].items()]
    error_columns = [("Élément", _id_cell), ("Langue", _language_cell)]
    return [
        [intro],
        table(["Mesure", "Effectifs", "Valeur"], _binary_measure_rows(section), "lrr"),
        _counts_table(section),
        _heading(4, f"{prefix} — par langue"),
        table(["Langue", "Éléments", "Justes", "Exactitude", "FP", "FN"], language_rows, "lrrrrr"),
        _heading(4, f"{prefix} — faux positifs"),
        _error_table(section["false_positives"], error_columns),
        _heading(4, f"{prefix} — faux négatifs"),
        _error_table(section["false_negatives"], error_columns),
    ]


def _detection_blocks(report: Mapping[str, Any], number: int) -> list[Block]:
    orders, reorders = report["order_detection"], report["reorder_detection"]
    return [
        _heading(3, f"{number}.1 Commandes"),
        *_binary_blocks(orders, "Commandes",
                        f"Composant : champ `est_bon_commande` renvoyé par {code(EXTRACTION_COMPONENT)}. Étiquette : "
                        f"`is_order`. Classe positive : commande ({orders['positives']} éléments sur "
                        f"{orders['items']})."),
        _heading(3, f"{number}.2 Renouvellements"),
        *_binary_blocks(reorders, "Renouvellements",
                        f"Composant : {code(reorders['component'])}. Étiquette : intention `reorder`. Classe "
                        f"positive : renouvellement ({reorders['positives']} éléments sur {reorders['items']})."),
    ]


def _score_rows(section: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for label, entry in (("Score maximal d'un message légitime", section["max_legitimate_score"]),
                         ("Score minimal d'une injection", section["min_injection_score"])):
        rows.append([label, NO_VALUE if entry is None else str(entry["score"]),
                     NO_VALUE if entry is None else code(entry["id"])])
    rows.append(["Seuil de signalement", str(section["threshold"]), NO_VALUE])
    return rows


def _injection_blocks(section: Mapping[str, Any], number: int) -> list[Block]:
    traps, legitimate = section["traps"], section["non_injection"]
    measure_rows = _binary_measure_rows(section)
    measure_rows.append(["Fausses alertes sur les pièges", fraction(traps["false_positives"], traps["items"]),
                         format_percent(traps["rate"])])
    detection_rows = [[code(language), str(counts["tp"] + counts["fn"]), str(counts["tp"]),
                       format_percent(counts["precision"]), format_percent(counts["recall"]),
                       format_percent(counts["f1"])]
                      for language, counts in section["by_language"].items()]
    alert_rows = [[code(language), str(counts["fp"] + counts["tn"]), str(counts["fp"]),
                   format_percent(counts["false_positive_rate"]), str(counts["traps"]),
                   str(counts["trap_false_positives"])]
                  for language, counts in section["by_language"].items()]
    score_column = ("Score", lambda entry: str(entry["score"]))
    return [
        [f"Composant : {code(section['component'])}. Un message est signalé quand son score atteint "
         f"{section['threshold']} (sur 100). Étiquette : `is_injection`. Classe positive : tentative d'injection "
         f"({section['positives']} éléments sur {section['items']}). Les {legitimate['items']} autres éléments, "
         f"dont {traps['items']} pièges, sont les cas négatifs."],
        _heading(3, f"{number}.1 Mesures"),
        table(["Mesure", "Effectifs", "Valeur"], measure_rows, "lrr"),
        _counts_table(section),
        _heading(3, f"{number}.2 Scores"),
        table(["Mesure", "Score", "Élément"], _score_rows(section), "lrl"),
        _heading(3, f"{number}.3 Détection par langue"),
        table(["Langue", "Injections", "Détectées", "Précision", "Rappel", "F1"], detection_rows, "lrrrrr"),
        _heading(3, f"{number}.4 Fausses alertes par langue"),
        table(["Langue", "Légitimes", "Fausses alertes", "Taux de faux positifs", "Pièges", "Alertes sur pièges"],
              alert_rows, "lrrrrr"),
        _heading(3, f"{number}.5 Erreurs"),
        ["Injections manquées (faux négatifs) :"],
        _error_table(section["false_negatives"], [("Élément", _id_cell), ("Langue", _language_cell), score_column]),
        ["Fausses alertes (faux positifs) :"],
        _error_table(section["false_positives"], [("Élément", _id_cell), ("Langue", _language_cell), score_column,
                                                  ("Piège", lambda entry: cell(entry["trap"]))]),
    ]


def _intent_blocks(section: Mapping[str, Any], number: int) -> list[Block]:
    if not section["available"]:
        return [[f"Section {UNAVAILABLE} : {section['reason']}. Cette section mesure la fonction "
                 f"{code(INTENT_COMPONENT)} quand le module est importable."]]
    accuracy = section["accuracy"]
    measure_rows = [["Exactitude", fraction(accuracy["correct"], accuracy["total"]), format_percent(accuracy["rate"])],
                    ["Macro-F1", NO_VALUE, format_percent(section["macro_f1"])]]
    intent_rows = [[code(intent), str(entry["support"]), str(entry["predicted"]), str(entry["tp"]),
                    format_percent(entry["precision"]), format_percent(entry["recall"]), format_percent(entry["f1"])]
                   for intent, entry in section["per_intent"].items()]
    language_rows = [[code(language), str(ratio["total"]), str(ratio["correct"]), format_percent(ratio["rate"])]
                     for language, ratio in section["by_language"].items()]
    return [
        [f"Composant : {code(section['component'])}, appelé avec la langue obtenue par "
         f"{code(LANGUAGE_COMPONENT)}, comme dans l'assistant WhatsApp. Étiquette : `intent` (définitions dans "
         f"`evaluation/README.md`, section 4.3), sur les {section['items']} éléments du jeu."],
        table(["Mesure", "Effectifs", "Valeur"], measure_rows, "lrr"),
        _heading(3, f"{number}.1 Mesures par intention"),
        ["« Attendus » : éléments étiquetés avec l'intention. « Prédits » : éléments auxquels le détecteur "
         "l'attribue."],
        table(["Intention", "Attendus", "Prédits", "VP", "Précision", "Rappel", "F1"], intent_rows, "lrrrrrr"),
        _heading(3, f"{number}.2 Exactitude par langue"),
        table(["Langue", "Éléments", "Justes", "Exactitude"], language_rows, "lrrr"),
        _heading(3, f"{number}.3 Matrice de confusion"),
        ["Lignes : intention attendue. Colonnes : intention obtenue."],
        _confusion_table(section["confusion_matrix"]),
        _heading(3, f"{number}.4 Éléments mal classés"),
        _error_table(section["errors"], [("Élément", _id_cell), ("Langue", _language_cell),
                                         ("Attendu", lambda entry: code(entry["expected"])),
                                         ("Obtenu", lambda entry: code(entry["obtained"]))]),
    ]


def _numbered_sections(builders: Sequence[tuple[str, Callable[[int], list[Block]]]]) -> list[Block]:
    blocks: list[Block] = []
    for number, (title, build) in enumerate(builders, start=1):
        blocks.append(_heading(2, f"{number}. {title}"))
        blocks.extend(build(number))
    return blocks


def render_offline_markdown(report: Mapping[str, Any]) -> str:
    """Rapport hors ligne en Markdown, rédigé à partir du contenu du rapport JSON."""
    orders = report["extraction"]["items"]
    extraction_intro = (f"Composant : {code(EXTRACTION_COMPONENT)}, appliqué aux {orders} commandes du jeu "
                        "(étiquette `is_order` vraie).")
    builders = [
        ("Jeu de données", lambda number: _dataset_blocks(report["dataset"])),
        ("Mesures employées", lambda number: _definition_blocks(OFFLINE_MEASURES)),
        ("Synthèse", lambda number: _summary_blocks(report["summary"])),
        ("Détection de la langue", lambda number: _language_blocks(report["language"], number)),
        ("Extraction par règles des commandes",
         lambda number: _extraction_blocks(report["extraction"], number, extraction_intro)),
        ("Détection des commandes et des renouvellements", lambda number: _detection_blocks(report, number)),
        ("Détection des tentatives d'injection", lambda number: _injection_blocks(report["injection"], number)),
        ("Détection des intentions", lambda number: _intent_blocks(report["intent"], number)),
    ]
    return _join([
        _heading(1, "Évaluation hors ligne de l'analyse des messages clients"),
        ["Mesure, sans réseau ni modèle de langue, des composants déterministes qui analysent les messages "
         "clients, sur le jeu annoté décrit dans `evaluation/README.md`."],
        _run_information(report),
        [SHA_NOTE],
        *_numbered_sections(builders),
    ])


def _online_quality_blocks(report: Mapping[str, Any]) -> list[Block]:
    recognition, normalization = report["order_recognition"], report["normalization"]
    anomalies, missing = normalization["with_anomalies"], normalization["with_missing_fields"]
    rows = [
        ["Rappel de `est_bon_commande`", fraction(recognition["correct"], recognition["total"]),
         format_percent(recognition["rate"])],
        ["Extractions en échec", fraction(len(report["failures"]), report["extraction"]["items"]),
         format_percent(_ratio(len(report["failures"]), report["extraction"]["items"]))],
        ["Commandes avec anomalie", fraction(anomalies["correct"], anomalies["total"]),
         format_percent(anomalies["rate"])],
        ["Commandes avec champ obligatoire manquant", fraction(missing["correct"], missing["total"]),
         format_percent(missing["rate"])],
        ["Confiance calculée moyenne", NO_VALUE, cell(normalization["mean_confidence"])],
    ]
    return [
        ["Classe positive : commande. Toutes les commandes du jeu sont positives : la proportion de commandes "
         "reconnues (`est_bon_commande` vrai) est le rappel. Une extraction est en échec quand l'extracteur ne "
         "renvoie aucune commande (modèle indisponible, réponse illisible). Les anomalies et les champs "
         "manquants sont ceux de `normalize_order`, sur les extractions réussies."],
        table(["Mesure", "Effectifs", "Valeur"], rows, "lrr"),
        ["Commandes non reconnues :"],
        _id_list(recognition["errors"]),
        ["Extractions en échec :"],
        _id_list(report["failures"]),
    ]


def render_online_markdown(report: Mapping[str, Any]) -> str:
    """Rapport en ligne en Markdown, rédigé à partir du contenu du rapport JSON."""
    models = report["models"]
    extra = [["Modèle d'extraction", code(models.get("extraction"))],
             ["Modèle léger (renouvellement)", code(models.get("light"))],
             ["Température", cell(models.get("temperature"))]]
    orders = report["extraction"]["items"]
    extraction_intro = (f"Composant : {code(ONLINE_COMPONENT)}, appliqué aux {orders} commandes du jeu (étiquette "
                        "`is_order` vraie). L'extracteur fonctionne sans base de données : un renouvellement n'est "
                        "pas complété par l'historique du client.")
    builders = [
        ("Jeu de données", lambda number: _dataset_blocks(report["dataset"])),
        ("Mesures employées", lambda number: _definition_blocks(ONLINE_MEASURES)),
        ("Synthèse", lambda number: _summary_blocks(report["summary"])),
        ("Champs extraits", lambda number: _extraction_blocks(report["extraction"], number, extraction_intro)),
        ("Reconnaissance des commandes et normalisation", lambda number: _online_quality_blocks(report)),
    ]
    return _join([
        _heading(1, "Évaluation en ligne de l'extraction des commandes"),
        ["Mesure de l'extraction complète par le modèle de langue, suivie des règles de normalisation, sur les "
         "commandes du jeu annoté décrit dans `evaluation/README.md`. Les réponses du modèle varient d'une "
         "exécution à l'autre : les chiffres valent pour la date et les modèles indiqués."],
        _run_information(report, extra),
        [SHA_NOTE],
        *_numbered_sections(builders),
    ])


# ---------------------------------------------------------------- écriture et ligne de commande

def write_report(report: Mapping[str, Any], markdown: str, output_dir: Path | str, name: str) -> tuple[Path, Path]:
    """Écrit ``<name>.md`` et ``<name>.json`` (UTF-8, fins de ligne « \\n ») ; renvoie les deux chemins."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path, json_path = directory / f"{name}.md", directory / f"{name}.json"
    markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    logger.info("Rapport écrit : %s", markdown_path)
    return markdown_path, json_path


def summary_lines(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    """Lignes de synthèse pour la console."""
    lines = []
    for entry in entries:
        value = entry["note"] or format_percent(entry["value"])
        counts = fraction(entry["numerator"], entry["denominator"])
        detail = f"{value} ({counts})" if counts != NO_VALUE else value
        lines.append(f"{entry['component']} — {entry['measure'].replace('`', '')} : {detail}")
    return lines


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("date au format AAAA-MM-JJ attendue") from None


def build_parser() -> argparse.ArgumentParser:
    """Options de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Évalue les composants d'analyse des messages clients sur le jeu annoté.")
    parser.add_argument("--online", action="store_true",
                        help="extraction complète par le modèle de langue (clé OPENAI_API_KEY requise)")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET,
                        help="jeu annoté au format JSON Lines (par défaut evaluation/dataset/messages.jsonl)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="dossier des rapports (par défaut evaluation/results)")
    parser.add_argument("--date", type=_parse_date,
                        help="date d'exécution inscrite dans le rapport, AAAA-MM-JJ (par défaut la date du jour)")
    return parser


def _load_items(path: Path) -> list[DatasetItem] | None:
    try:
        return load_dataset(path)
    except FileNotFoundError:
        print(f"Jeu de données introuvable : {path}", file=sys.stderr)
    except (OSError, DatasetError) as exc:
        print(f"Jeu de données invalide ({path}) : {exc}", file=sys.stderr)
    return None


def _print_outcome(report: Mapping[str, Any], paths: Sequence[Path]) -> None:
    for path in paths:
        print(f"Écrit : {path}")
    for line in summary_lines(report["summary"]):
        print(line)


def _online_client(settings: Settings | None, llm: Any) -> tuple[Settings | None, Any]:
    if settings is None:
        try:
            settings = load_settings()
        except ConfigurationError as exc:
            print(f"Configuration invalide : {exc}", file=sys.stderr)
            return None, None
    return settings, (llm if llm is not None else LLMClient.from_settings(settings))


def _run_online(args: argparse.Namespace, run_date: date, settings: Settings | None, llm: Any) -> int:
    settings, client = _online_client(settings, llm)
    if settings is None:
        return EXIT_INVALID_INPUT
    if not client.available:
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        return EXIT_NO_API_KEY
    items = _load_items(args.dataset)
    if items is None:
        return EXIT_INVALID_INPUT
    models = {"extraction": settings.model_extraction, "light": settings.model_light,
              "temperature": settings.llm_temperature}
    report = build_online_report(items, dataset_path=args.dataset, run_date=run_date,
                                 extract=build_online_extractor(settings, client, run_date), models=models)
    _print_outcome(report, write_report(report, render_online_markdown(report), args.output_dir, ONLINE_REPORT_NAME))
    return EXIT_OK


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None, llm: Any = None) -> int:
    """Lance l'évaluation ; renvoie le code de sortie du programme.

    Args:
        argv: arguments de la ligne de commande (par défaut ``sys.argv[1:]``).
        settings: configuration de l'évaluation en ligne ; par défaut lue dans l'environnement.
        llm: client des modèles de langue de l'évaluation en ligne ; par défaut construit depuis
            la configuration.
    """
    args = build_parser().parse_args(argv)
    run_date = args.date or date.today()
    if args.online:
        return _run_online(args, run_date, settings, llm)
    items = _load_items(args.dataset)
    if items is None:
        return EXIT_INVALID_INPUT
    report = build_offline_report(items, dataset_path=args.dataset, run_date=run_date)
    _print_outcome(report, write_report(report, render_offline_markdown(report), args.output_dir,
                                        OFFLINE_REPORT_NAME))
    return EXIT_OK


def _use_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    _use_utf8_console()
    configure_logging("WARNING")
    sys.exit(main())
