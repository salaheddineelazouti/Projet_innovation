"""Tests du choix de modèle par tâche et par langue (tecpap.llm.router)."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError

import pytest

from tecpap.config import Settings, load_settings
from tecpap.extraction.prompts import TRANSCRIPTION_HINT
from tecpap.llm import router as router_module
from tecpap.llm.router import (
    ANSWER_MAX_TEMPERATURE,
    LANGUAGE_LABELS,
    TASK_LABELS,
    TASKS,
    ModelChoice,
    ModelRouter,
    normalize_language,
    transcription_hint,
)
from tecpap.nlp.language import LANGUAGE_CODES, detect_language

MODELS = {
    "model_extraction": "modele-principal",
    "model_light": "modele-leger",
    "model_vision": "modele-vision",
    "model_transcription": "modele-transcription",
    "model_embedding": "modele-vecteurs",
}

LIGHT_LANGUAGES = ("fr", "en")
LOW_RESOURCE_LANGUAGES = ("ar", "darija_ar", "darija_latin", "darija")
UNIDENTIFIED_LANGUAGES = ("unknown", None, "", "es", "zz", "  ")
ALL_LANGUAGES = (*LIGHT_LANGUAGES, *LOW_RESOURCE_LANGUAGES, *UNIDENTIFIED_LANGUAGES)

# Modèle attendu pour chaque tâche dont le choix ne dépend pas de la langue.
FIXED_MODELS = {
    "extraction": "modele-principal",
    "vision": "modele-vision",
    "transcription": "modele-transcription",
    "embedding": "modele-vecteurs",
}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings.for_tests(tmp_path, llm_temperature=0.1, **MODELS)


@pytest.fixture
def router(settings) -> ModelRouter:
    return ModelRouter(settings)


def expected_model(task: str, language: str | None) -> str:
    """Modèle prévu par la politique documentée dans le module."""
    if task in FIXED_MODELS:
        return FIXED_MODELS[task]
    return "modele-leger" if language in LIGHT_LANGUAGES else "modele-principal"


class TestPolicy:
    """Chaque tâche croisée avec chaque langue."""

    @pytest.mark.parametrize("language", ALL_LANGUAGES)
    @pytest.mark.parametrize("task", TASKS)
    def test_model_for_every_task_and_language(self, router, task, language):
        choice = router.route(task, language)
        assert isinstance(choice, ModelChoice)
        assert choice.model == expected_model(task, language)

    @pytest.mark.parametrize("language", ALL_LANGUAGES)
    @pytest.mark.parametrize("task", TASKS)
    def test_reason_is_a_french_sentence_naming_the_task(self, router, task, language):
        reason = router.route(task, language).reason
        assert reason.startswith(TASK_LABELS[task])
        assert reason.endswith(".")
        assert " : " in reason

    @pytest.mark.parametrize("language", LANGUAGE_CODES)
    def test_extraction_always_uses_the_main_model(self, router, language):
        assert router.route("extraction", language).model == "modele-principal"

    @pytest.mark.parametrize("task", ["reorder_detection", "intent", "answer"])
    @pytest.mark.parametrize("language", LIGHT_LANGUAGES)
    def test_short_tasks_in_well_resourced_languages_use_the_light_model(self, router, task, language):
        choice = router.route(task, language)
        assert choice.model == "modele-leger"
        assert "bien dotée" in choice.reason

    @pytest.mark.parametrize("task", ["reorder_detection", "intent", "answer"])
    @pytest.mark.parametrize("language", LOW_RESOURCE_LANGUAGES)
    def test_short_tasks_in_low_resource_languages_use_the_main_model(self, router, task, language):
        choice = router.route(task, language)
        assert choice.model == "modele-principal"
        assert "peu dotée" in choice.reason

    @pytest.mark.parametrize("task", ["reorder_detection", "intent", "answer"])
    @pytest.mark.parametrize("language", UNIDENTIFIED_LANGUAGES)
    def test_short_tasks_without_identified_language_use_the_main_model(self, router, task, language):
        choice = router.route(task, language)
        assert choice.model == "modele-principal"
        assert "sans langue identifiée" in choice.reason

    @pytest.mark.parametrize("task", ["extraction", "reorder_detection", "intent", "answer"])
    def test_reason_names_the_language_for_language_dependent_tasks(self, router, task):
        assert "(darija en lettres latines)" in router.route(task, "darija_latin").reason
        assert "(arabe standard)" in router.route(task, "ar").reason

    @pytest.mark.parametrize("task", ["vision", "transcription", "embedding"])
    def test_reason_ignores_the_language_for_media_and_vectors(self, router, task):
        assert router.route(task, "fr").reason == router.route(task, "darija_ar").reason

    def test_language_code_is_case_and_space_insensitive(self, router):
        assert router.route("intent", " FR ").model == "modele-leger"
        assert router.route("intent", "Darija_Latin").model == "modele-principal"

    def test_detected_languages_route_as_expected(self, router):
        assert router.route("intent", detect_language("Bonjour, où en est ma commande ?").code).model == "modele-leger"
        assert router.route("intent", detect_language("fin wslat la commande dyali?").code).model == "modele-principal"
        assert router.route("intent", detect_language("فين وصلات الكوموند ديالي؟").code).model == "modele-principal"
        assert router.route("intent", detect_language("ok").code).model == "modele-principal"


class TestTemperature:
    """Température par tâche."""

    @pytest.mark.parametrize("task", [task for task in TASKS if task != "answer"])
    def test_tasks_use_configured_temperature(self, tmp_path, task):
        router = ModelRouter(Settings.for_tests(tmp_path, llm_temperature=0.7))
        assert router.route(task, "fr").temperature == 0.7

    @pytest.mark.parametrize("language", ["fr", "darija_latin", None])
    def test_answer_temperature_is_capped(self, tmp_path, language):
        router = ModelRouter(Settings.for_tests(tmp_path, llm_temperature=0.7))
        assert router.route("answer", language).temperature == ANSWER_MAX_TEMPERATURE

    def test_answer_temperature_below_the_cap_is_kept(self, tmp_path):
        router = ModelRouter(Settings.for_tests(tmp_path, llm_temperature=0.05))
        assert router.route("answer", "fr").temperature == 0.05

    def test_cap_value(self):
        assert ANSWER_MAX_TEMPERATURE == 0.2


class TestSettingsOverride:
    """Les modèles viennent de la configuration."""

    def test_default_models(self, tmp_path):
        router = ModelRouter(Settings.for_tests(tmp_path))
        assert router.route("extraction", "fr").model == "gpt-4o"
        assert router.route("intent", "fr").model == "gpt-4o-mini"
        assert router.route("intent", "darija_ar").model == "gpt-4o"
        assert router.route("vision", "fr").model == "gpt-4o"
        assert router.route("transcription", None).model == "whisper-1"
        assert router.route("embedding", None).model == "text-embedding-3-small"

    def test_models_from_environment_variables(self):
        environment = {"LLM_MODEL_EXTRACTION": "grand", "LLM_MODEL_LIGHT": "petit", "LLM_TEMPERATURE": "0.3"}
        router = ModelRouter(load_settings(environment))
        assert router.route("intent", "en").model == "petit"
        assert router.route("intent", "ar").model == "grand"
        assert router.route("extraction", "en").temperature == 0.3

    def test_router_does_not_modify_settings(self, settings, router):
        before = dict(settings.__dict__)
        for task in TASKS:
            router.route(task, "darija_latin")
        assert settings.__dict__ == before


class TestUnknownTask:
    """Une tâche inconnue est une erreur de programmation : elle lève ValueError."""

    @pytest.mark.parametrize("task", ["translation", "", "EXTRACTION", " extraction", "chat", None, 42])
    def test_unknown_task_raises(self, router, task):
        with pytest.raises(ValueError, match="Tâche inconnue"):
            router.route(task, "fr")

    def test_error_lists_known_tasks(self, router):
        with pytest.raises(ValueError) as excinfo:
            router.route("traduction", "fr")
        for task in TASKS:
            assert task in str(excinfo.value)


class TestTranscription:
    """Vocabulaire et langue transmis au modèle de transcription."""

    @pytest.mark.parametrize("language", [None, "fr", "ar", "darija_latin", "darija_ar", "unknown"])
    def test_hint_is_the_business_vocabulary(self, router, language):
        assert transcription_hint(language) == TRANSCRIPTION_HINT
        assert router.transcription_hint(language) == TRANSCRIPTION_HINT

    def test_hint_without_argument(self, router):
        assert transcription_hint() == TRANSCRIPTION_HINT
        assert router.transcription_hint() == TRANSCRIPTION_HINT

    def test_automatic_language_by_default(self, router):
        assert router.transcription_language is None

    def test_configured_language(self, tmp_path):
        router = ModelRouter(Settings.for_tests(tmp_path, transcription_language="ar"))
        assert router.transcription_language == "ar"


class TestModelChoice:
    """Valeur renvoyée par le routeur."""

    def test_choice_is_frozen(self, router):
        choice = router.route("intent", "fr")
        with pytest.raises(FrozenInstanceError):
            choice.model = "autre"

    def test_choice_is_deterministic_and_hashable(self, router):
        first, second = router.route("answer", "darija_ar"), router.route("answer", "darija_ar")
        assert first == second
        assert hash(first) == hash(second)

    def test_fields(self):
        choice = ModelChoice(model="m", temperature=0.1, reason="r")
        assert (choice.model, choice.temperature, choice.reason) == ("m", 0.1, "r")


class TestNormalizeLanguage:
    """Normalisation du code de langue."""

    @pytest.mark.parametrize("code", list(LANGUAGE_LABELS))
    def test_known_codes_are_kept(self, code):
        assert normalize_language(code) == code

    @pytest.mark.parametrize("value", [None, "", "es", "français", 3, ["fr"]])
    def test_other_values_become_unknown(self, value):
        assert normalize_language(value) == "unknown"

    def test_every_detected_code_has_a_label(self):
        for code in LANGUAGE_CODES:
            assert code in LANGUAGE_LABELS


class TestLogging:
    """Le routeur journalise son choix sans contenu de message."""

    def test_choice_is_logged_at_debug_level(self, router, caplog):
        with caplog.at_level(logging.DEBUG, logger=router_module.__name__):
            router.route("intent", "darija_latin")
        messages = [record.getMessage() for record in caplog.records if record.name == router_module.__name__]
        assert messages == ["Modèle modele-principal retenu pour la tâche intent (langue darija_latin)"]
