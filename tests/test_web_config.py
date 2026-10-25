"""Configuration : clé de session en production, valeurs d'exemple, apostrophes, fuseau horaire, journalisation.

Aucune variable d'environnement du poste n'est lue : les valeurs sont passées à ``load_settings``.
"""

from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path

import pytest
from dotenv import dotenv_values

from tecpap.config import (
    DEFAULT_TIMEZONE,
    ENV_VARIABLES,
    MIN_SECRET_KEY_LENGTH,
    PLACEHOLDER_CHECKED_VARIABLES,
    ConfigurationError,
    Settings,
    is_placeholder,
    load_settings,
    strip_quotes,
)
from tecpap.llm.client import LLMClient
from tecpap.logging_config import LOG_FORMAT, QUIET_LOGGERS, configure_logging
from tecpap.web.auth import verify_credentials
from tests.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_PASSWORD_HASH

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"

EXAMPLE_SECRET_KEY = "remplacer-par-une-valeur-longue-et-aleatoire"
GOOD_KEY = "0123456789abcdef0123456789abcdef"
PRODUCTION = {"APP_ENV": "production", "SECRET_KEY": GOOD_KEY}


class TestProductionSecretKey:
    """SECRET_KEY signe les cookies de session : une clé connue ou courte permettrait de fabriquer une session."""

    @pytest.mark.parametrize("value", [
        EXAMPLE_SECRET_KEY,
        "valeur-longue-et-aleatoire",
        "remplacer-moi-par-une-cle-de-plus-de-trente-deux-caracteres",
    ])
    def test_example_value_is_refused(self, value):
        with pytest.raises(ConfigurationError, match="valeur d'exemple"):
            load_settings({"APP_ENV": "production", "SECRET_KEY": value})

    def test_example_value_is_refused_as_override(self):
        with pytest.raises(ConfigurationError, match="valeur d'exemple"):
            load_settings({"APP_ENV": "production"}, secret_key=EXAMPLE_SECRET_KEY)

    def test_minimum_length(self):
        accepted = "a1" * (MIN_SECRET_KEY_LENGTH // 2)
        assert len(accepted) == MIN_SECRET_KEY_LENGTH
        assert load_settings({"APP_ENV": "production", "SECRET_KEY": accepted}).secret_key == accepted
        with pytest.raises(ConfigurationError, match="trop courte"):
            load_settings({"APP_ENV": "production", "SECRET_KEY": accepted[:-1]})

    def test_missing_key_is_refused(self):
        with pytest.raises(ConfigurationError, match="obligatoire"):
            load_settings({"APP_ENV": "production"})

    def test_error_message_gives_the_command_to_generate_a_key(self):
        with pytest.raises(ConfigurationError, match="token_hex"):
            load_settings({"APP_ENV": "production", "SECRET_KEY": "court"})

    def test_quoted_key_is_unquoted_before_the_check(self):
        settings = load_settings({"APP_ENV": "production", "SECRET_KEY": f"'{GOOD_KEY}'"})
        assert settings.secret_key == GOOD_KEY

    def test_a_copy_of_the_example_file_is_refused_in_production(self):
        """Un fichier .env copié de .env.example laisse SECRET_KEY vide : la production ne démarre pas."""
        values = {name: value for name, value in dotenv_values(ENV_EXAMPLE).items() if value is not None}
        with pytest.raises(ConfigurationError, match="obligatoire"):
            load_settings({**values, "APP_ENV": "production"})


class TestDevelopmentSecretKey:
    """Hors production, une clé absente ou d'exemple est remplacée par une clé aléatoire."""

    def test_example_value_is_replaced_with_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="tecpap.config"):
            settings = load_settings({"SECRET_KEY": EXAMPLE_SECRET_KEY})
        assert settings.secret_key != EXAMPLE_SECRET_KEY
        assert len(settings.secret_key) >= MIN_SECRET_KEY_LENGTH
        assert "valeur d'exemple" in caplog.text

    def test_missing_key_gives_a_random_key_shared_by_the_process(self):
        assert load_settings({}).secret_key == load_settings({}).secret_key


class TestQuotes:
    """``docker run --env-file`` transmet les apostrophes telles quelles, python-dotenv les retire."""

    @pytest.mark.parametrize("value, expected", [
        ("'abc'", "abc"),
        ('"abc"', "abc"),
        ("abc", "abc"),
        ("'abc", "'abc"),
        ("abc'", "abc'"),
        ("'abc\"", "'abc\""),
        ("''", ""),
        ("'", "'"),
        ("'a'b'", "a'b"),
    ])
    def test_strip_quotes(self, value, expected):
        assert strip_quotes(value) == expected

    def test_quoted_values_are_read_without_quotes(self):
        settings = load_settings({"FLASK_HOST": "'0.0.0.0'", "ADMIN_USERNAME": '"responsable"'})
        assert (settings.host, settings.admin_username) == ("0.0.0.0", "responsable")

    def test_quoted_password_hash_still_verifies(self):
        settings = load_settings({"ADMIN_PASSWORD_HASH": f"'{TEST_ADMIN_PASSWORD_HASH}'"})
        assert settings.admin_password_hash == TEST_ADMIN_PASSWORD_HASH
        assert verify_credentials(settings, "admin", TEST_ADMIN_PASSWORD) is True

    def test_empty_quoted_value_keeps_the_default(self):
        assert load_settings({"FLASK_HOST": "''"}).host == "127.0.0.1"

    def test_line_printed_by_hash_password_works_with_env_file_of_docker(self, monkeypatch, capsys):
        """La ligne affichée par le script, lue littéralement (docker run --env-file), permet de se connecter."""
        spec = importlib.util.spec_from_file_location("hash_password_script", ROOT / "scripts" / "hash_password.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        monkeypatch.setattr(script.getpass, "getpass", lambda prompt="": "un-mot-de-passe-solide")
        assert script.main() == 0
        line = next(row for row in capsys.readouterr().out.splitlines() if row.startswith("ADMIN_PASSWORD_HASH="))
        name, _, value = line.partition("=")
        assert value.startswith("'") and value.endswith("'")
        settings = load_settings({name: value})
        assert verify_credentials(settings, "admin", "un-mot-de-passe-solide") is True


class TestPlaceholders:
    """Une valeur d'exemple de .env.example laissée en place équivaut à une variable absente."""

    @pytest.mark.parametrize("value", [
        "sk-remplacer-par-votre-cle",
        "remplacer-par-votre-jeton",
        "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "xxxx xxxx xxxx xxxx",
        "https://exemple.ngrok-free.app",
        "commandes@example.com",
        EXAMPLE_SECRET_KEY,
    ])
    def test_known_placeholders(self, value):
        assert is_placeholder(value) is True

    @pytest.mark.parametrize("value", [
        "sk-proj-AbCdEf0123456789", "AC" "0123456789abcdef0123456789abcdef", "abcd efgh ijkl mnop",
        "https://tecpap.example.org", "commandes@tecpap.ma", "", "   ", None,
    ])
    def test_real_values(self, value):
        assert is_placeholder(value) is False

    @pytest.mark.parametrize("variable, attribute", [
        ("OPENAI_API_KEY", "openai_api_key"),
        ("TWILIO_ACCOUNT_SID", "twilio_account_sid"),
        ("TWILIO_AUTH_TOKEN", "twilio_auth_token"),
        ("GMAIL_APP_PASSWORD", "gmail_app_password"),
        ("GMAIL_EMAIL", "gmail_email"),
        ("PUBLIC_BASE_URL", "public_base_url"),
    ])
    def test_placeholder_is_ignored_with_a_warning(self, variable, attribute, caplog):
        placeholder = {"OPENAI_API_KEY": "sk-remplacer-par-votre-cle", "TWILIO_ACCOUNT_SID": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                       "TWILIO_AUTH_TOKEN": "remplacer-par-votre-jeton", "GMAIL_APP_PASSWORD": "xxxx xxxx xxxx xxxx",
                       "GMAIL_EMAIL": "commandes@example.com", "PUBLIC_BASE_URL": "https://exemple.ngrok-free.app"}[variable]
        with caplog.at_level(logging.WARNING, logger="tecpap.config"):
            settings = load_settings({variable: placeholder})
        assert getattr(settings, attribute) is None
        assert variable in caplog.text

    def test_placeholder_api_key_leaves_the_assistant_in_rule_mode(self):
        settings = load_settings({"OPENAI_API_KEY": "sk-remplacer-par-votre-cle"})
        assert LLMClient.from_settings(settings).available is False

    def test_real_value_is_kept(self):
        assert load_settings({"OPENAI_API_KEY": "sk-proj-AbCdEf0123456789"}).openai_api_key == "sk-proj-AbCdEf0123456789"

    def test_checked_variables_are_known_variables(self):
        assert set(PLACEHOLDER_CHECKED_VARIABLES) <= set(ENV_VARIABLES)

    def test_example_file_holds_no_placeholder_value(self):
        """Une copie de .env.example ne contient ni clé d'exemple ni identifiant d'exemple."""
        for name, value in dotenv_values(ENV_EXAMPLE).items():
            assert not is_placeholder(value), name

    def test_secrets_are_empty_in_the_example_file(self):
        values = dotenv_values(ENV_EXAMPLE)
        for name in ("SECRET_KEY", "OPENAI_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "GMAIL_EMAIL",
                     "GMAIL_APP_PASSWORD", "PUBLIC_BASE_URL", "ADMIN_PASSWORD_HASH"):
            assert not values.get(name), name

    def test_a_copy_of_the_example_file_loads_with_every_service_disabled(self):
        values = {name: value for name, value in dotenv_values(ENV_EXAMPLE).items() if value is not None}
        settings = load_settings(values)
        assert settings.openai_api_key is None
        assert settings.twilio_auth_token is None
        assert settings.gmail_app_password is None
        assert settings.admin_password_hash is None


class TestExampleFileInDocker:
    """``docker run --env-file .env`` : les variables du fichier remplacent celles de l'image."""

    PATH_VARIABLES = ("DATABASE_PATH", "BACKUP_DIR", "EXPORT_DIR", "ATTACHMENTS_DIR", "WHATSAPP_MEDIA_DIR")

    @staticmethod
    def _active_assignments() -> dict[str, str]:
        """Affectations non commentées du fichier, lues littéralement comme le fait docker."""
        assignments = {}
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, _, value = line.partition("=")
                assignments[name.strip()] = value
        return assignments

    def test_image_paths_are_not_overridden(self):
        active = self._active_assignments()
        for name in (*self.PATH_VARIABLES, "APP_ENV"):
            assert name not in active, f"{name} ne doit pas être actif dans .env.example (l'image Docker le définit)"

    def test_commented_defaults_are_documented(self):
        content = ENV_EXAMPLE.read_text(encoding="utf-8")
        for name in self.PATH_VARIABLES:
            assert re.search(rf"^# {name}=", content, flags=re.MULTILINE), name
        assert "docker run --env-file" in content

    def test_image_environment_wins_for_the_data_paths(self):
        image = {"APP_ENV": "production", "DATABASE_PATH": "/data/db/orders.db", "BACKUP_DIR": "/data/backups",
                 "SECRET_KEY": GOOD_KEY}
        settings = load_settings({**image, **self._active_assignments(), "SECRET_KEY": GOOD_KEY})
        assert settings.is_production
        assert settings.database_path == "/data/db/orders.db"
        assert settings.backup_dir == "/data/backups"


class TestTimezone:
    """Fuseau horaire des dates affichées (variable APP_TIMEZONE)."""

    def test_default(self):
        settings = load_settings({})
        assert settings.timezone == DEFAULT_TIMEZONE == "Africa/Casablanca"
        assert settings.tzinfo.key == "Africa/Casablanca"

    def test_test_settings_have_the_default_timezone(self, tmp_path):
        assert Settings.for_tests(tmp_path).timezone == DEFAULT_TIMEZONE

    def test_variable(self):
        settings = load_settings({"APP_TIMEZONE": "Europe/Paris"})
        assert settings.timezone == "Europe/Paris"
        assert settings.tzinfo.key == "Europe/Paris"

    def test_unknown_timezone_is_refused(self):
        with pytest.raises(ConfigurationError, match="APP_TIMEZONE"):
            load_settings({"APP_TIMEZONE": "Mars/Olympus"})

    def test_variable_is_documented(self):
        assert "APP_TIMEZONE" in dotenv_values(ENV_EXAMPLE)


class TestDebugMode:
    """Le mode débogage affiche le détail des erreurs et empêche le démarrage du planificateur sous gunicorn."""

    def test_refused_in_production(self):
        with pytest.raises(ConfigurationError, match="FLASK_DEBUG"):
            load_settings({**PRODUCTION, "FLASK_DEBUG": "true"})

    def test_accepted_in_development(self):
        assert load_settings({"FLASK_DEBUG": "true"}).debug is True


class TestLoggingConfiguration:
    """Les bibliothèques Twilio et OpenAI n'écrivent pas leurs appels (adresse, identifiant de compte) au niveau INFO."""

    @pytest.fixture(autouse=True)
    def restore_levels(self):
        saved = {name: logging.getLogger(name).level for name in ("", *QUIET_LOGGERS)}
        yield
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)

    @pytest.mark.parametrize("name", QUIET_LOGGERS)
    def test_third_party_loggers_are_quiet_at_info(self, name):
        configure_logging("INFO")
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING

    def test_twilio_http_client_is_covered(self):
        assert "twilio.http_client" in QUIET_LOGGERS and "httpx" in QUIET_LOGGERS and "openai" in QUIET_LOGGERS

    def test_debug_level_keeps_every_message(self):
        configure_logging("DEBUG")
        assert logging.getLogger("twilio.http_client").getEffectiveLevel() == logging.DEBUG

    def test_error_level_is_kept(self):
        configure_logging("ERROR")
        assert logging.getLogger("httpx").getEffectiveLevel() == logging.ERROR

    def test_application_loggers_keep_the_configured_level(self):
        configure_logging("INFO")
        assert logging.getLogger("tecpap.web").getEffectiveLevel() == logging.INFO

    def test_format_shows_the_thread(self):
        assert "%(threadName)s" in LOG_FORMAT
