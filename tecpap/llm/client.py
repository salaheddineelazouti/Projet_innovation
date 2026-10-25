"""Client des modèles de langue de l'API OpenAI.

:class:`LLMClient` regroupe les appels utilisés par l'application :

- :meth:`LLMClient.chat_json` : réponse sous la forme d'un objet JSON (extraction d'une
  commande, détection d'un renouvellement, classification d'un email) ;
- :meth:`LLMClient.chat` : conversation, avec appel d'outils (le modèle demande l'exécution
  d'une fonction déclarée par l'application, qui reste seule à l'exécuter) ;
- :meth:`LLMClient.transcribe` : transcription d'un fichier audio (Whisper) ;
- :meth:`LLMClient.image_to_text` : lecture du texte d'une image par un modèle de vision ;
- :meth:`LLMClient.embed` : plongements vectoriels (représentation de chaque texte par un
  vecteur de nombres, utilisée pour la recherche par similarité).

Aucune méthode ne lève d'exception vers l'appelant : en cas d'erreur, l'incident est
journalisé et la méthode renvoie ``None``. Les erreurs passagères (délai dépassé, connexion
interrompue, limite de débit atteinte, erreur 5xx du serveur) donnent lieu à un seul nouvel
essai après une courte pause. Le SDK OpenAI est construit sans nouvel essai automatique, pour
que le nombre total d'appels reste celui décrit ici.

Le contenu des messages n'est jamais écrit dans le journal : il contient des données de
clients. Seuls l'opération, le modèle et le type d'erreur le sont.

Ce module est le seul du paquet à importer le SDK OpenAI.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

import openai

if TYPE_CHECKING:
    from tecpap.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)
"""Erreurs du SDK qui justifient un nouvel essai (délai, connexion, débit, erreur 5xx)."""

MAX_ATTEMPTS = 2
"""Nombre maximal d'appels pour une même requête : le premier essai et un nouvel essai."""

DEFAULT_RETRY_DELAY = 1.0
"""Pause, en secondes, avant le nouvel essai."""

EMBEDDING_BATCH_SIZE = 256
"""Nombre de textes envoyés par requête de calcul des plongements."""

MAX_LOGGED_ERROR_CHARS = 200


@dataclass
class ToolCall:
    """Appel d'outil demandé par le modèle : nom de la fonction et arguments décodés du JSON."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    """Réponse d'une conversation : texte éventuel, appels d'outils demandés, modèle utilisé."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""


def strip_code_fence(text: str) -> str:
    """Retire un éventuel bloc de code Markdown (```json … ```) autour d'une réponse."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1:] if first_newline != -1 else text[3:]
        text = text.rstrip()
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def parse_json_object(text: str | None) -> dict[str, Any] | None:
    """Décode un objet JSON renvoyé par un modèle, ou renvoie ``None``.

    Le décodage tolère un bloc de code Markdown autour du JSON et un texte parasite avant la
    première accolade ou après la dernière. Une valeur JSON qui n'est pas un objet (liste,
    nombre…) est refusée.
    """
    if not text:
        return None
    candidate = strip_code_fence(text)
    start, end = candidate.find("{"), candidate.rfind("}")
    attempts = [candidate]
    if start != -1 and end > start:
        attempts.append(candidate[start:end + 1])
    for attempt in attempts:
        try:
            value = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _parse_tool_arguments(raw: Any, tool_name: str) -> dict[str, Any]:
    """Décode les arguments d'un appel d'outil ; un JSON invalide donne un dictionnaire vide."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Arguments illisibles pour l'outil %s : JSON invalide", tool_name)
        return {}
    if not isinstance(value, dict):
        logger.warning("Arguments inattendus pour l'outil %s : objet JSON attendu", tool_name)
        return {}
    return value


def _parse_tool_call(call: Any) -> ToolCall:
    function = getattr(call, "function", None)
    name = getattr(function, "name", None) or ""
    return ToolCall(
        id=getattr(call, "id", None) or "",
        name=name,
        arguments=_parse_tool_arguments(getattr(function, "arguments", None), name),
    )


def _describe_error(exc: Exception) -> str:
    """Résumé d'une erreur pour le journal : type, code HTTP éventuel, début du message."""
    parts = [type(exc).__name__]
    status = getattr(exc, "status_code", None)
    if status:
        parts.append(f"(code HTTP {status})")
    message = str(exc).strip()
    if message:
        parts.append(message[:MAX_LOGGED_ERROR_CHARS])
    return " ".join(parts)


class LLMClient:
    """Accès aux modèles OpenAI, sans exception vers l'appelant.

    Args:
        api_key: clé d'API OpenAI ; sans clé (et sans ``client``), le client est indisponible
            et chaque appel renvoie ``None``.
        timeout: délai maximal d'une requête, en secondes.
        client: objet compatible avec le SDK OpenAI déjà construit (tests, configuration
            particulière). Sans lui, le client du SDK est créé à la première requête.
        retry_delay: pause avant le nouvel essai d'une requête en erreur passagère, en secondes.
    """

    def __init__(self, api_key: str | None, timeout: float = 60.0, client: Any = None, *,
                 retry_delay: float = DEFAULT_RETRY_DELAY):
        self._api_key = api_key or None
        self.timeout = timeout
        self.retry_delay = retry_delay
        self._client = client
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMClient:
        """Construit le client à partir de la configuration (clé d'API et délai maximal)."""
        return cls(api_key=settings.openai_api_key, timeout=settings.llm_timeout)

    def __repr__(self) -> str:
        return f"LLMClient(available={self.available})"

    @property
    def available(self) -> bool:
        """Vrai quand une clé d'API est configurée ou qu'un client a été fourni."""
        return self._client is not None or self._api_key is not None

    # ------------------------------------------------------------------ requêtes

    def chat_json(self, *, model: str, system: str, user: str, temperature: float = 0.1,
                  max_tokens: int = 1000) -> dict[str, Any] | None:
        """Demande au modèle une réponse sous la forme d'un objet JSON.

        La requête impose le format objet JSON (``response_format``) ; l'API exige alors que
        le mot « JSON » figure dans les messages.

        Returns:
            L'objet décodé, ou ``None`` en cas d'erreur ou de réponse illisible.
        """
        def request(client: Any) -> str:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content or ""

        content = self._call("Réponse JSON", model, request)
        if content is None:
            return None
        data = parse_json_object(content)
        if data is None:
            logger.error("Réponse JSON du modèle %s illisible : objet JSON attendu", model)
        return data

    def chat(self, *, model: str, messages: list[dict[str, Any]], temperature: float = 0.1,
             max_tokens: int = 800, tools: list[dict[str, Any]] | None = None,
             tool_choice: str | dict[str, Any] | None = None) -> ChatResult | None:
        """Envoie une conversation au modèle, avec ou sans outils déclarés.

        Args:
            messages: messages au format de l'API (``role`` et ``content``).
            tools: schémas des fonctions que le modèle peut demander d'appeler.
            tool_choice: ``"auto"``, ``"none"``, ``"required"``, ou un dictionnaire qui impose
                une fonction précise ; ignoré sans ``tools``.

        Returns:
            Le texte et les appels d'outils demandés, ou ``None`` en cas d'erreur. Les
            arguments d'un appel d'outil illisibles sont remplacés par un dictionnaire vide.
        """
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            params["tools"] = tools
            if tool_choice:
                params["tool_choice"] = tool_choice

        def request(client: Any) -> ChatResult:
            response = client.chat.completions.create(**params)
            message = response.choices[0].message
            return ChatResult(
                content=message.content,
                tool_calls=[_parse_tool_call(call) for call in (message.tool_calls or [])],
                model=getattr(response, "model", None) or model,
            )

        return self._call("Conversation", model, request)

    def transcribe(self, file_path: str, *, model: str, language: str | None = None,
                   prompt: str | None = None) -> str | None:
        """Transcrit un fichier audio.

        Args:
            file_path: fichier audio ; son extension indique le format à l'API.
            language: code ISO 639-1 de la langue parlée (``"fr"``, ``"ar"``) ; sans valeur,
                le modèle détecte la langue lui-même.
            prompt: vocabulaire qui oriente la transcription (noms de produits, termes métier).

        Returns:
            Le texte transcrit, ou ``None`` en cas d'erreur ou de transcription vide.
        """
        options: dict[str, Any] = {"model": model, "response_format": "text"}
        if language:
            options["language"] = language
        if prompt:
            options["prompt"] = prompt

        def request(client: Any) -> str:
            with open(file_path, "rb") as audio_file:
                result = client.audio.transcriptions.create(file=audio_file, **options)
            text = result if isinstance(result, str) else getattr(result, "text", None)
            return (text or "").strip()

        text = self._call("Transcription audio", model, request)
        if text == "":
            logger.warning("Transcription vide (modèle %s)", model)
            return None
        return text

    def image_to_text(self, image_bytes: bytes, mime_type: str, *, model: str, instruction: str,
                      max_tokens: int = 2000, temperature: float = 0.0) -> str | None:
        """Fait lire une image par un modèle de vision selon ``instruction``.

        Returns:
            Le texte renvoyé par le modèle, ou ``None`` en cas d'erreur ou de réponse vide.
        """
        encoded = base64.b64encode(image_bytes).decode("ascii")
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            ],
        }]

        def request(client: Any) -> str:
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=temperature,
            )
            return (response.choices[0].message.content or "").strip()

        text = self._call("Lecture d'image", model, request)
        if text == "":
            logger.warning("Lecture d'image sans texte (modèle %s)", model)
            return None
        return text

    def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]] | None:
        """Calcule le plongement vectoriel de chaque texte, dans l'ordre reçu.

        Les textes sont envoyés par lots de :data:`EMBEDDING_BATCH_SIZE`. Une liste vide donne
        une liste vide sans appel à l'API.

        Returns:
            Un vecteur par texte, ou ``None`` si un lot est en erreur.
        """
        texts = list(texts)
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start:start + EMBEDDING_BATCH_SIZE]

            def request(client: Any, batch: list[str] = batch) -> list[list[float]]:
                response = client.embeddings.create(model=model, input=batch)
                items = sorted(enumerate(response.data), key=lambda pair: getattr(pair[1], "index", pair[0]))
                return [list(item.embedding) for _, item in items]

            batch_vectors = self._call("Calcul des plongements", model, request)
            if batch_vectors is None:
                return None
            if len(batch_vectors) != len(batch):
                logger.error("Plongements incomplets (modèle %s) : %d vecteurs pour %d textes",
                             model, len(batch_vectors), len(batch))
                return None
            vectors.extend(batch_vectors)
        return vectors

    # ------------------------------------------------------------------ mécanique commune

    def _sdk(self) -> Any:
        """Client du SDK, créé à la première requête ; ``None`` sans clé d'API."""
        if self._client is None and self._api_key is not None:
            with self._lock:
                if self._client is None:
                    self._client = openai.OpenAI(api_key=self._api_key, timeout=self.timeout, max_retries=0)
        return self._client

    def _call(self, operation: str, model: str, request: Callable[[Any], T]) -> T | None:
        """Exécute ``request`` avec un nouvel essai sur erreur passagère ; ``None`` en cas d'échec."""
        client = self._sdk()
        if client is None:
            logger.warning("%s impossible : clé OPENAI_API_KEY absente", operation)
            return None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return request(client)
            except TRANSIENT_ERRORS as exc:
                if attempt < MAX_ATTEMPTS:
                    delay = self.retry_delay * attempt
                    logger.warning("%s (modèle %s) : erreur passagère %s, nouvel essai dans %.1f s",
                                   operation, model, _describe_error(exc), delay)
                    time.sleep(delay)
                    continue
                logger.error("%s (modèle %s) en échec après %d essais : %s",
                             operation, model, MAX_ATTEMPTS, _describe_error(exc))
            except Exception as exc:
                # Toute autre erreur (requête refusée, clé invalide, réponse inattendue) est
                # définitive : un nouvel essai donnerait le même résultat.
                logger.error("%s (modèle %s) en échec : %s", operation, model, _describe_error(exc))
                return None
        return None
