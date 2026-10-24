"""Tests de la configuration (tecpap.config)."""

import dataclasses
import re
from pathlib import Path

import pytest

from tecpap import __version__
from tecpap.config import ENV_VARIABLES, ConfigurationError, Settings, load_settings, parse_bool

ROOT = Path(__file__).resolve().parent.parent


class TestDefaults:
    """Valeurs par défaut (environnement vide)."""

    @pytest.fixture
    def settings(self):
        return load_settings({})

    def test_environment(self, settings):
        assert settings.env == "development"
        assert not settings.is_production
        assert not settings.is_testing

    def test_server(self, settings):
        assert (settings.host, settings.port, settings.debug) == ("127.0.0.1", 5000, False)

    def test_paths(self, settings):
        assert settings.database_path == "orders.db"
        assert settings.backup_dir == "backups"
        assert settings.export_dir == "exports"
        assert settings.attachments_dir == "attachments"
        assert settings.whatsapp_media_dir == "whatsapp_media"
        assert settings.knowledge_dir == "knowledge"

    def test_models(self, settings):
        assert settings.model_extraction == "gpt-4o"
        assert settings.model_light == "gpt-4o-mini"
        assert settings.model_vision == "gpt-4o"
        assert settings.model_transcription == "whisper-1"
        assert settings.transcription_language is None
        assert settings.model_embedding == "text-embedding-3-small"
        assert settings.llm_temperature == 0.1
        assert settings.llm_timeout == 60.0

    def test_channels(self, settings):
        assert settings.openai_api_key is None
        assert settings.gmail_email is None
        assert (settings.smtp_server, settings.smtp_port) == ("smtp.gmail.com", 587)
        assert settings.twilio_whatsapp_number == "whatsapp:+14155238886"
        assert settings.twilio_validate_signature is True
        assert settings.public_base_url is None
        assert settings.whatsapp_async_reply is True
        assert settings.max_media_bytes == 16 * 1024 * 1024

    def test_assistant_and_rag(self, settings):
        assert settings.chatbot_enabled is True
        assert (settings.rag_enabled, settings.rag_top_k, settings.rag_min_score) == (True, 4, 0.25)
        assert settings.whatsapp_rate_limit_per_minute == 10

    def test_auth_and_backups(self, settings):
        assert settings.auth_enabled is True
        assert settings.admin_username == "admin"
        assert settings.admin_password_hash is None
        assert (settings.backup_scheduler_enabled, settings.backup_interval_hours, settings.backup_keep) == (True, 6, 20)
        assert settings.log_level == "INFO"

    def test_development_secret_is_random_and_stable(self):
        first, second = load_settings({}), load_settings({})
        assert len(first.secret_key) >= 32
        assert first.secret_key == second.secret_key


class TestParsing:
    """Lecture des variables d'environnement."""

    @pytest.mark.parametrize("raw, expected", [
        ("1", True), ("0", False), ("true", True), ("False", False), ("YES", True), ("no", False),
        ("oui", True), ("Non", False), (" true ", True),
    ])
    def test_booleans(self, raw, expected):
        assert parse_bool(raw) is expected
        assert load_settings({"FLASK_DEBUG": raw}).debug is expected

    def test_invalid_boolean(self):
        with pytest.raises(ConfigurationError, match="FLASK_DEBUG"):
            load_settings({"FLASK_DEBUG": "peut-être"})

    def test_numbers(self):
        settings = load_settings({"FLASK_PORT": "8080", "LLM_TEMPERATURE": "0.3", "RAG_TOP_K": "6"})
        assert (settings.port, settings.llm_temperature, settings.rag_top_k) == (8080, 0.3, 6)

    def test_invalid_number(self):
        with pytest.raises(ConfigurationError, match="FLASK_PORT"):
            load_settings({"FLASK_PORT": "cinq mille"})

    def test_port_out_of_range(self):
        with pytest.raises(ConfigurationError):
            load_settings({"FLASK_PORT": "70000"})

    def test_empty_value_keeps_default(self):
        settings = load_settings({"OPENAI_API_KEY": "", "DATABASE_PATH": "  ", "TRANSCRIPTION_LANGUAGE": ""})
        assert settings.openai_api_key is None
        assert settings.database_path == "orders.db"
        assert settings.transcription_language is None

    def test_strings(self):
        settings = load_settings({
            "OPENAI_API_KEY": "sk-test", "PUBLIC_BASE_URL": "https://exemple.org", "TRANSCRIPTION_LANGUAGE": "ar",
            "DATABASE_PATH": "data/orders.db", "LOG_LEVEL": "debug",
        })
        assert settings.openai_api_key == "sk-test"
        assert settings.public_base_url == "https://exemple.org"
        assert settings.transcription_language == "ar"
        assert settings.database_path == "data/orders.db"
        assert settings.log_level == "DEBUG"

    def test_app_env_is_case_insensitive(self):
        assert load_settings({"APP_ENV": "Test"}).is_testing

    def test_unknown_app_env(self):
        with pytest.raises(ConfigurationError, match="APP_ENV"):
            load_settings({"APP_ENV": "recette"})

    def test_overrides_win_over_environment(self):
        assert load_settings({"FLASK_PORT": "8080"}, port=9090).port == 9090

    def test_unknown_override(self):
        with pytest.raises(ConfigurationError):
            load_settings({}, attribut_inconnu=1)

    def test_reads_process_environment(self, monkeypatch):
        monkeypatch.setenv("FLASK_PORT", "8123")
        assert load_settings().port == 8123


class TestProduction:
    """Règles propres à la production."""

    def test_production_requires_secret_key(self):
        with pytest.raises(ConfigurationError, match="SECRET_KEY"):
            load_settings({"APP_ENV": "production"})

    def test_production_with_secret_key(self):
        settings = load_settings({"APP_ENV": "production", "SECRET_KEY": "une-cle-longue"})
        assert settings.is_production
        assert settings.secret_key == "une-cle-longue"

    def test_secret_key_override(self):
        assert load_settings({"APP_ENV": "production"}, secret_key="cle").is_production


class TestForTests:
    """Configuration des tests automatisés."""

    def test_paths_inside_base_dir(self, tmp_path):
        settings = Settings.for_tests(tmp_path)
        for path in (settings.database_path, settings.backup_dir, settings.export_dir, settings.attachments_dir,
                     settings.whatsapp_media_dir, settings.knowledge_dir):
            assert Path(path).parent == tmp_path

    def test_external_services_disabled(self, tmp_path):
        settings = Settings.for_tests(tmp_path)
        assert settings.is_testing
        assert settings.openai_api_key is None
        assert settings.gmail_app_password is None
        assert settings.twilio_auth_token is None
        assert settings.auth_enabled is False
        assert settings.twilio_validate_signature is False
        assert settings.backup_scheduler_enabled is False
        assert settings.whatsapp_async_reply is False

    def test_ignores_process_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-reelle")
        assert Settings.for_tests(tmp_path).openai_api_key is None

    def test_overrides(self, tmp_path):
        assert Settings.for_tests(tmp_path, rag_top_k=2).rag_top_k == 2


class TestSettingsObject:
    """Propriétés de l'objet Settings."""

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            load_settings({}).port = 1

    def test_secrets_not_in_repr(self):
        settings = load_settings({"OPENAI_API_KEY": "sk-secret", "TWILIO_AUTH_TOKEN": "jeton-secret"})
        assert "sk-secret" not in repr(settings)
        assert "jeton-secret" not in repr(settings)

    def test_every_field_has_an_environment_variable(self):
        assert len(ENV_VARIABLES) == len(dataclasses.fields(Settings))


class TestDocumentation:
    """Cohérence avec les fichiers du dépôt."""

    def test_env_example_lists_every_variable(self):
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        declared = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", content, flags=re.MULTILINE))
        assert declared == set(ENV_VARIABLES)

    def test_pyproject_version_matches_package(self):
        content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert re.search(r'^version = "([^"]+)"', content, flags=re.MULTILINE).group(1) == __version__
