"""Configuration de l'application.

Toute la configuration est lue une seule fois depuis les variables d'environnement (et le
fichier ``.env`` s'il existe) par :func:`load_settings`, puis transmise aux modules sous la forme
d'un objet :class:`Settings` immuable. Aucun module métier ne lit ``os.environ`` directement :
on peut ainsi construire une configuration complète pour les tests sans toucher à
l'environnement du poste (voir :meth:`Settings.for_tests`).

La liste des variables, leurs valeurs par défaut et leur rôle sont documentés dans
``.env.example``.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ENVIRONMENTS = ("development", "production", "test")

RAG_EMBEDDERS = ("local", "openai")
"""Calculs de plongements vectoriels de la base de connaissances (variable ``RAG_EMBEDDER``).

- ``local`` : n-grammes de caractères hachés, calculés sur le serveur (hors ligne, gratuit et
  déterministe : le même texte donne toujours le même vecteur) ;
- ``openai`` : modèle ``EMBEDDING_MODEL`` de l'API OpenAI (clé ``OPENAI_API_KEY`` nécessaire).
"""

TRUE_VALUES = frozenset({"1", "true", "yes", "oui"})
FALSE_VALUES = frozenset({"0", "false", "no", "non"})

# Clé tirée au hasard une fois par processus, utilisée hors production quand SECRET_KEY
# n'est pas défini. Les sessions restent valides tant que le processus tourne ; en production
# plusieurs processus doivent partager la même clé, d'où l'obligation de la définir.
_PROCESS_SECRET_KEY = secrets.token_hex(32)


class ConfigurationError(ValueError):
    """Variable de configuration absente ou invalide."""


def parse_bool(value: str, name: str = "valeur") -> bool:
    """Convertit une chaîne en booléen.

    Valeurs acceptées (sans tenir compte de la casse) : 1/0, true/false, yes/no, oui/non.
    """
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ConfigurationError(
        f"{name} : valeur booléenne invalide {value!r} (valeurs acceptées : 1/0, true/false, yes/no, oui/non)"
    )


def _parse_str(value: str, name: str) -> str:
    return value


def _parse_int(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} : nombre entier attendu, reçu {value!r}") from exc


def _parse_float(value: str, name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} : nombre décimal attendu, reçu {value!r}") from exc


def _parse_env(value: str, name: str) -> str:
    return value.lower()


def _parse_choice(value: str, name: str) -> str:
    """Valeur choisie dans une liste fermée, sans tenir compte de la casse (vérifiée par Settings)."""
    return value.lower()


@dataclass(frozen=True)
class Settings:
    """Configuration complète de l'application (immuable).

    Les champs sensibles (clés d'API, mots de passe, jetons) sont exclus de ``repr`` pour ne
    pas apparaître dans les journaux.
    """

    env: str = "development"
    secret_key: str = field(default="", repr=False)
    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False

    database_path: str = "orders.db"
    backup_dir: str = "backups"
    export_dir: str = "exports"
    attachments_dir: str = "attachments"
    whatsapp_media_dir: str = "whatsapp_media"
    knowledge_dir: str = "knowledge"

    openai_api_key: str | None = field(default=None, repr=False)
    model_extraction: str = "gpt-4o"
    model_light: str = "gpt-4o-mini"
    model_vision: str = "gpt-4o"
    model_transcription: str = "whisper-1"
    transcription_language: str | None = None
    model_embedding: str = "text-embedding-3-small"
    llm_temperature: float = 0.1
    llm_timeout: float = 60.0

    gmail_email: str | None = None
    gmail_app_password: str | None = field(default=None, repr=False)
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587

    twilio_account_sid: str | None = field(default=None, repr=False)
    twilio_auth_token: str | None = field(default=None, repr=False)
    twilio_whatsapp_number: str = "whatsapp:+14155238886"
    twilio_validate_signature: bool = True
    public_base_url: str | None = None
    whatsapp_async_reply: bool = True

    chatbot_enabled: bool = True
    rag_enabled: bool = True
    rag_top_k: int = 4
    rag_min_score: float = 0.23
    rag_embedder: str = "local"
    whatsapp_rate_limit_per_minute: int = 10
    max_media_bytes: int = 16 * 1024 * 1024

    auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password_hash: str | None = field(default=None, repr=False)
    csrf_enabled: bool = True
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    # None : cookie de session « Secure » (envoyé seulement en HTTPS) en production uniquement.
    session_cookie_secure: bool | None = None
    trust_proxy_headers: bool = False

    backup_scheduler_enabled: bool = True
    backup_interval_hours: int = 6
    backup_keep: int = 20

    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.env not in ENVIRONMENTS:
            raise ConfigurationError(
                f"APP_ENV : valeur {self.env!r} inconnue (valeurs acceptées : {', '.join(ENVIRONMENTS)})"
            )
        if not self.secret_key:
            if self.env == "production":
                raise ConfigurationError(
                    "SECRET_KEY est obligatoire en production : définissez une valeur longue et aléatoire, "
                    "par exemple avec python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            object.__setattr__(self, "secret_key", _PROCESS_SECRET_KEY)
        if self.session_cookie_secure is None:
            object.__setattr__(self, "session_cookie_secure", self.env == "production")
        if not 1 <= self.port <= 65535:
            raise ConfigurationError(f"FLASK_PORT : port {self.port} hors de l'intervalle 1-65535")
        for name in ("backup_interval_hours", "backup_keep", "rag_top_k", "whatsapp_rate_limit_per_minute",
                     "max_media_bytes", "smtp_port", "login_max_attempts", "login_lockout_minutes"):
            if getattr(self, name) < 1:
                raise ConfigurationError(f"{name} : valeur strictement positive attendue")
        if self.llm_timeout <= 0:
            raise ConfigurationError("LLM_TIMEOUT : valeur strictement positive attendue")
        if self.rag_embedder not in RAG_EMBEDDERS:
            raise ConfigurationError(
                f"RAG_EMBEDDER : valeur {self.rag_embedder!r} inconnue (valeurs acceptées : {', '.join(RAG_EMBEDDERS)})"
            )

    @property
    def is_production(self) -> bool:
        """Vrai quand l'application tourne en production (APP_ENV=production)."""
        return self.env == "production"

    @property
    def is_testing(self) -> bool:
        """Vrai pour la configuration des tests automatisés (APP_ENV=test)."""
        return self.env == "test"

    @classmethod
    def for_tests(cls, base_dir: Path | str, **overrides: Any) -> Settings:
        """Configuration isolée pour les tests automatisés.

        Tous les chemins pointent dans ``base_dir`` (en général le ``tmp_path`` de pytest),
        aucune clé d'API n'est définie, l'authentification, la protection CSRF, la
        vérification de signature Twilio, le planificateur de sauvegardes et la réponse
        WhatsApp asynchrone sont désactivés. L'environnement du poste n'est pas lu.
        """
        base = Path(base_dir)
        values: dict[str, Any] = {
            "env": "test",
            "secret_key": "cle-secrete-reservee-aux-tests",
            "debug": False,
            "database_path": str(base / "orders.db"),
            "backup_dir": str(base / "backups"),
            "export_dir": str(base / "exports"),
            "attachments_dir": str(base / "attachments"),
            "whatsapp_media_dir": str(base / "whatsapp_media"),
            "knowledge_dir": str(base / "knowledge"),
            "openai_api_key": None,
            "gmail_email": None,
            "gmail_app_password": None,
            "twilio_account_sid": None,
            "twilio_auth_token": None,
            "public_base_url": None,
            "auth_enabled": False,
            "admin_password_hash": None,
            "csrf_enabled": False,
            "trust_proxy_headers": False,
            "twilio_validate_signature": False,
            "backup_scheduler_enabled": False,
            "whatsapp_async_reply": False,
            "log_level": "WARNING",
        }
        values.update(overrides)
        return cls(**values)


# Correspondance variable d'environnement -> attribut de Settings -> fonction de conversion.
_ENV_SPEC: tuple[tuple[str, str, Callable[[str, str], Any]], ...] = (
    ("APP_ENV", "env", _parse_env),
    ("SECRET_KEY", "secret_key", _parse_str),
    ("FLASK_HOST", "host", _parse_str),
    ("FLASK_PORT", "port", _parse_int),
    ("FLASK_DEBUG", "debug", parse_bool),
    ("DATABASE_PATH", "database_path", _parse_str),
    ("BACKUP_DIR", "backup_dir", _parse_str),
    ("EXPORT_DIR", "export_dir", _parse_str),
    ("ATTACHMENTS_DIR", "attachments_dir", _parse_str),
    ("WHATSAPP_MEDIA_DIR", "whatsapp_media_dir", _parse_str),
    ("KNOWLEDGE_DIR", "knowledge_dir", _parse_str),
    ("OPENAI_API_KEY", "openai_api_key", _parse_str),
    ("LLM_MODEL_EXTRACTION", "model_extraction", _parse_str),
    ("LLM_MODEL_LIGHT", "model_light", _parse_str),
    ("LLM_MODEL_VISION", "model_vision", _parse_str),
    ("TRANSCRIPTION_MODEL", "model_transcription", _parse_str),
    ("TRANSCRIPTION_LANGUAGE", "transcription_language", _parse_str),
    ("EMBEDDING_MODEL", "model_embedding", _parse_str),
    ("LLM_TEMPERATURE", "llm_temperature", _parse_float),
    ("LLM_TIMEOUT", "llm_timeout", _parse_float),
    ("GMAIL_EMAIL", "gmail_email", _parse_str),
    ("GMAIL_APP_PASSWORD", "gmail_app_password", _parse_str),
    ("SMTP_SERVER", "smtp_server", _parse_str),
    ("SMTP_PORT", "smtp_port", _parse_int),
    ("TWILIO_ACCOUNT_SID", "twilio_account_sid", _parse_str),
    ("TWILIO_AUTH_TOKEN", "twilio_auth_token", _parse_str),
    ("TWILIO_WHATSAPP_NUMBER", "twilio_whatsapp_number", _parse_str),
    ("TWILIO_VALIDATE_SIGNATURE", "twilio_validate_signature", parse_bool),
    ("PUBLIC_BASE_URL", "public_base_url", _parse_str),
    ("WHATSAPP_ASYNC_REPLY", "whatsapp_async_reply", parse_bool),
    ("CHATBOT_ENABLED", "chatbot_enabled", parse_bool),
    ("RAG_ENABLED", "rag_enabled", parse_bool),
    ("RAG_TOP_K", "rag_top_k", _parse_int),
    ("RAG_MIN_SCORE", "rag_min_score", _parse_float),
    ("RAG_EMBEDDER", "rag_embedder", _parse_choice),
    ("WHATSAPP_RATE_LIMIT_PER_MINUTE", "whatsapp_rate_limit_per_minute", _parse_int),
    ("MAX_MEDIA_BYTES", "max_media_bytes", _parse_int),
    ("AUTH_ENABLED", "auth_enabled", parse_bool),
    ("ADMIN_USERNAME", "admin_username", _parse_str),
    ("ADMIN_PASSWORD_HASH", "admin_password_hash", _parse_str),
    ("CSRF_ENABLED", "csrf_enabled", parse_bool),
    ("LOGIN_MAX_ATTEMPTS", "login_max_attempts", _parse_int),
    ("LOGIN_LOCKOUT_MINUTES", "login_lockout_minutes", _parse_int),
    ("SESSION_COOKIE_SECURE", "session_cookie_secure", parse_bool),
    ("TRUST_PROXY_HEADERS", "trust_proxy_headers", parse_bool),
    ("BACKUP_SCHEDULER_ENABLED", "backup_scheduler_enabled", parse_bool),
    ("BACKUP_INTERVAL_HOURS", "backup_interval_hours", _parse_int),
    ("BACKUP_KEEP", "backup_keep", _parse_int),
    ("LOG_LEVEL", "log_level", lambda value, name: value.upper()),
)

ENV_VARIABLES: tuple[str, ...] = tuple(var for var, _, _ in _ENV_SPEC)
"""Noms de toutes les variables d'environnement lues par :func:`load_settings`."""

_FIELD_NAMES = frozenset(f.name for f in fields(Settings))


def is_reloader_child_process(env: Mapping[str, str] | None = None) -> bool:
    """Vrai dans le processus enfant lancé par le rechargeur de Werkzeug.

    En mode débogage, le serveur de développement de Flask relance l'application dans un
    processus enfant marqué par la variable ``WERKZEUG_RUN_MAIN=true``. Cette variable est écrite
    par Werkzeug, pas par l'utilisateur : elle décrit le processus courant et ne fait donc pas
    partie de :class:`Settings`.

    Args:
        env: variables à consulter ; par défaut ``os.environ``.
    """
    source = os.environ if env is None else env
    return source.get("WERKZEUG_RUN_MAIN") == "true"


def load_settings(env: Mapping[str, str] | None = None, **overrides: Any) -> Settings:
    """Construit la configuration à partir des variables d'environnement.

    Args:
        env: dictionnaire de variables à utiliser. Quand il vaut ``None``, le fichier
            ``.env`` est chargé (sans écraser les variables déjà définies) puis ``os.environ``
            est lu.
        **overrides: valeurs d'attributs de :class:`Settings` qui remplacent celles de
            l'environnement (utile pour les scripts et les tests).

    Une variable absente ou vide garde sa valeur par défaut. Une valeur invalide lève
    :class:`ConfigurationError`, de même qu'une production sans ``SECRET_KEY``.
    """
    if env is None:
        load_dotenv()
        env = os.environ
    unknown = set(overrides) - _FIELD_NAMES
    if unknown:
        raise ConfigurationError(f"Paramètres de configuration inconnus : {', '.join(sorted(unknown))}")

    values: dict[str, Any] = {}
    for var, attribute, parser in _ENV_SPEC:
        raw = env.get(var)
        if raw is None or not raw.strip():
            continue
        values[attribute] = parser(raw.strip(), var)
    values.update(overrides)
    return Settings(**values)
