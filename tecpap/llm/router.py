"""Choix du modèle de langue selon la tâche et la langue du message.

Chaque appel à un modèle passe par :meth:`ModelRouter.route`, qui renvoie le modèle, la
température (paramètre d'échantillonnage : plus elle est basse, plus la réponse est stable et
reproductible) et une phrase qui justifie le choix. Cette phrase est conservée avec la commande
pour l'audit : on sait après coup quel modèle a lu chaque message, et pourquoi.

La darija et l'arabe sont des langues peu dotées pour les modèles de langue : peu de textes dans
ces langues ont servi à leur entraînement, et un modèle léger les comprend nettement moins bien
que le français ou l'anglais. Le modèle principal est donc préféré dès que le message n'est pas
en français ou en anglais, et toujours pour l'extraction, dont chaque champ alimente la commande.

Politique. Les modèles sont des attributs de :class:`tecpap.config.Settings`, réglables par
variables d'environnement (``LLM_MODEL_EXTRACTION``, ``LLM_MODEL_LIGHT``…) :

| Tâche | Langue | Modèle | Température | Raison |
|---|---|---|---|---|
| extraction | toutes | model_extraction | llm_temperature | chaque champ extrait alimente la commande |
| reorder_detection, intent | fr, en | model_light | llm_temperature | tâche courte, langue bien dotée |
| reorder_detection, intent | darija_*, ar, unknown | model_extraction | llm_temperature | langue peu dotée |
| answer | fr, en | model_light | llm_temperature, 0,2 au plus | réponse courte, langue bien dotée |
| answer | darija_*, ar, unknown | model_extraction | llm_temperature, 0,2 au plus | langue peu dotée |
| vision | toutes | model_vision | llm_temperature | lecture d'une image |
| transcription | toutes | model_transcription | llm_temperature (sans effet) | vocabulaire métier transmis |
| embedding | toutes | model_embedding | llm_temperature (sans effet) | un seul modèle : vecteurs comparables |

``darija_*`` désigne ``darija_ar``, ``darija_latin`` et ``darija`` (code de la langue de
réponse) ; une langue non identifiée est traitée comme une langue peu dotée. Une langue absente
ou hors de :data:`LANGUAGE_LABELS` vaut ``unknown``. La température d'une réponse au client est
plafonnée à :data:`ANSWER_MAX_TEMPERATURE` pour qu'elle reste proche des extraits de la base de
connaissances. Les API de transcription et de plongements vectoriels (« embeddings » :
représentation d'un texte par un vecteur de nombres) n'utilisent pas la température : la valeur
renvoyée (``llm_temperature``) est alors sans effet.

Transcription : la langue transmise au modèle est ``Settings.transcription_language``
(``None`` : détection automatique, adaptée aux notes vocales qui mêlent darija et français),
disponible par :attr:`ModelRouter.transcription_language`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from tecpap.extraction.prompts import TRANSCRIPTION_HINT

if TYPE_CHECKING:
    from tecpap.config import Settings

logger = logging.getLogger(__name__)

TASKS: tuple[str, ...] = (
    "extraction", "reorder_detection", "intent", "answer", "vision", "transcription", "embedding",
)
"""Tâches connues du routeur."""

LANGUAGE_SENSITIVE_TASKS: frozenset[str] = frozenset({"reorder_detection", "intent", "answer"})
"""Tâches dont le modèle dépend de la langue du message."""

LIGHT_MODEL_LANGUAGES: frozenset[str] = frozenset({"fr", "en"})
"""Langues bien dotées pour lesquelles le modèle léger suffit aux tâches courtes."""

ANSWER_MAX_TEMPERATURE = 0.2
"""Température maximale d'une réponse rédigée pour le client."""

TASK_LABELS: Mapping[str, str] = MappingProxyType({
    "extraction": "Extraction de commande",
    "reorder_detection": "Détection de renouvellement",
    "intent": "Classification de l'intention",
    "answer": "Réponse au client",
    "vision": "Lecture d'image",
    "transcription": "Transcription audio",
    "embedding": "Plongements vectoriels",
})
"""Libellé français de chaque tâche, repris dans la raison du choix."""

LANGUAGE_LABELS: Mapping[str, str] = MappingProxyType({
    "fr": "français",
    "en": "anglais",
    "ar": "arabe standard",
    "darija_ar": "darija en lettres arabes",
    "darija_latin": "darija en lettres latines",
    "darija": "darija",
    "unknown": "langue non identifiée",
})
"""Codes de langue acceptés par :meth:`ModelRouter.route` et leur libellé français."""


@dataclass(frozen=True)
class ModelChoice:
    """Modèle retenu pour un appel.

    Attributes:
        model: nom du modèle à appeler.
        temperature: température d'échantillonnage.
        reason: phrase en français qui justifie le choix (conservée pour l'audit).
    """

    model: str
    temperature: float
    reason: str


def transcription_hint(language: str | None = None) -> str:
    """Vocabulaire métier transmis au modèle de transcription (``TRANSCRIPTION_HINT``).

    Le même texte sert pour toutes les langues : la langue d'une note vocale n'est connue
    qu'après sa transcription, et ce vocabulaire réunit déjà les termes français, darija et
    arabes.

    Args:
        language: langue attendue, si elle est connue (sans effet sur le texte renvoyé).
    """
    return TRANSCRIPTION_HINT


def normalize_language(language: str | None) -> str:
    """Code de langue reconnu par le routeur : ``unknown`` pour une valeur absente ou inconnue.

    La casse et les espaces autour du code sont ignorés.
    """
    if not isinstance(language, str):
        return "unknown"
    code = language.strip().lower()
    return code if code in LANGUAGE_LABELS else "unknown"


class ModelRouter:
    """Choisit le modèle et la température de chaque appel (politique décrite dans le module).

    Args:
        settings: configuration de l'application, d'où viennent les noms de modèles, la
            température et la langue de transcription.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def transcription_language(self) -> str | None:
        """Langue imposée au modèle de transcription (``None`` : détection automatique)."""
        return self.settings.transcription_language

    def transcription_hint(self, language: str | None = None) -> str:
        """Vocabulaire métier transmis au modèle de transcription (voir :func:`transcription_hint`)."""
        return transcription_hint(language)

    def route(self, task: str, language: str | None) -> ModelChoice:
        """Modèle, température et raison du choix pour une tâche et une langue.

        Args:
            task: une des tâches de :data:`TASKS`.
            language: code de langue renvoyé par :func:`tecpap.nlp.language.detect_language`
                (``darija`` est aussi accepté) ; une valeur absente ou inconnue vaut ``unknown``.

        Raises:
            ValueError: ``task`` n'est pas une tâche de :data:`TASKS`.
        """
        if task not in TASKS:
            raise ValueError(f"Tâche inconnue {task!r} ; tâches acceptées : {', '.join(TASKS)}")
        code = normalize_language(language)
        model, explanation = self._model_and_explanation(task, code)
        label = TASK_LABELS[task]
        if task == "extraction" or task in LANGUAGE_SENSITIVE_TASKS:
            label = f"{label} ({LANGUAGE_LABELS[code]})"
        choice = ModelChoice(model=model, temperature=self._temperature(task), reason=f"{label} : {explanation}")
        logger.debug("Modèle %s retenu pour la tâche %s (langue %s)", model, task, code)
        return choice

    def _temperature(self, task: str) -> float:
        """Température de la tâche : ``llm_temperature``, plafonnée pour une réponse au client."""
        temperature = self.settings.llm_temperature
        return min(temperature, ANSWER_MAX_TEMPERATURE) if task == "answer" else temperature

    def _model_and_explanation(self, task: str, code: str) -> tuple[str, str]:
        """Modèle de la tâche et fin de la phrase qui justifie ce choix."""
        settings = self.settings
        if task in LANGUAGE_SENSITIVE_TASKS:
            if code in LIGHT_MODEL_LANGUAGES:
                return settings.model_light, "langue bien dotée, le modèle léger suffit pour cette tâche courte."
            if code == "unknown":
                return (settings.model_extraction,
                        "sans langue identifiée, le modèle principal limite les erreurs de compréhension.")
            return (settings.model_extraction,
                    "langue peu dotée pour les modèles de langue, le modèle principal est préféré.")
        if task == "extraction":
            return (settings.model_extraction,
                    "modèle principal pour toutes les langues, car chaque champ extrait alimente la commande.")
        if task == "vision":
            return settings.model_vision, "modèle de vision, capable de lire le texte d'une image."
        if task == "transcription":
            return (settings.model_transcription,
                    "modèle de transcription, orienté par le vocabulaire métier en français, darija et arabe.")
        return (settings.model_embedding,
                "modèle unique, pour que tous les vecteurs de la base de connaissances restent comparables.")
