"""Doublures de test des services externes (modèle de langue, Twilio…).

Les tests importent d'ici les objets qui remplacent les clients d'API réels, pour qu'aucun
test n'appelle le réseau.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import deque
from collections.abc import Iterable
from typing import Any

from tecpap.llm.client import ChatResult


class FakeLLMClient:
    """Doublure de :class:`tecpap.llm.client.LLMClient`, avec la même interface.

    Chaque méthode renvoie la réponse suivante de sa file (``chat_json``, ``chat``,
    ``transcribe``, ``image_to_text``), ou ``None`` quand la file est vide, comme le client réel
    en cas d'échec. Une réponse peut être une fonction : elle reçoit alors les arguments de
    l'appel et renvoie la réponse. Pour ``chat``, une chaîne est convertie en
    :class:`ChatResult` sans appel d'outil.

    Tous les appels sont enregistrés dans :attr:`calls` sous la forme ``(méthode, arguments)``.
    ``embed`` est déterministe : chaque mot est haché vers une coordonnée du vecteur, si bien
    que deux textes qui partagent des mots ont des vecteurs proches.

    Args:
        available: valeur de la propriété ``available`` (``False`` imite l'absence de clé).
        embedding_dim: dimension des vecteurs renvoyés par ``embed``.
    """

    def __init__(self, *, chat_json: Iterable[Any] = (), chat: Iterable[Any] = (),
                 transcribe: Iterable[Any] = (), image_to_text: Iterable[Any] = (),
                 available: bool = True, embedding_dim: int = 64):
        self.available = available
        self.embedding_dim = embedding_dim
        self.queues: dict[str, deque[Any]] = {
            "chat_json": deque(chat_json),
            "chat": deque(chat),
            "transcribe": deque(transcribe),
            "image_to_text": deque(image_to_text),
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # ------------------------------------------------------------------ scénario

    def queue(self, method: str, *results: Any) -> FakeLLMClient:
        """Ajoute des réponses à la file de ``method`` ; renvoie la doublure pour chaîner les appels."""
        self.queues[method].extend(results)
        return self

    def calls_to(self, method: str) -> list[dict[str, Any]]:
        """Arguments des appels reçus par ``method``, dans l'ordre."""
        return [kwargs for name, kwargs in self.calls if name == method]

    def _next(self, method: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, kwargs))
        if not self.available or not self.queues[method]:
            return None
        result = self.queues[method].popleft()
        if callable(result):
            result = result(**kwargs)
        return result

    # ------------------------------------------------------------------ interface de LLMClient

    def chat_json(self, *, model: str, system: str, user: str, temperature: float = 0.1,
                  max_tokens: int = 1000) -> dict[str, Any] | None:
        return self._next("chat_json", {"model": model, "system": system, "user": user,
                                        "temperature": temperature, "max_tokens": max_tokens})

    def chat(self, *, model: str, messages: list[dict[str, Any]], temperature: float = 0.1,
             max_tokens: int = 800, tools: list[dict[str, Any]] | None = None,
             tool_choice: str | dict[str, Any] | None = None) -> ChatResult | None:
        result = self._next("chat", {"model": model, "messages": messages, "temperature": temperature,
                                     "max_tokens": max_tokens, "tools": tools, "tool_choice": tool_choice})
        if isinstance(result, str):
            return ChatResult(content=result, tool_calls=[], model=model)
        return result

    def transcribe(self, file_path: str, *, model: str, language: str | None = None,
                   prompt: str | None = None) -> str | None:
        return self._next("transcribe", {"file_path": file_path, "model": model,
                                         "language": language, "prompt": prompt})

    def image_to_text(self, image_bytes: bytes, mime_type: str, *, model: str, instruction: str,
                      max_tokens: int = 2000, temperature: float = 0.0) -> str | None:
        return self._next("image_to_text", {"image_bytes": image_bytes, "mime_type": mime_type, "model": model,
                                            "instruction": instruction, "max_tokens": max_tokens,
                                            "temperature": temperature})

    def embed(self, texts: Iterable[str], *, model: str) -> list[list[float]] | None:
        texts = list(texts)
        self.calls.append(("embed", {"texts": texts, "model": model}))
        if not self.available:
            return None
        return [hash_embedding(text, self.embedding_dim) for text in texts]


def hash_embedding(text: str, dim: int = 64) -> list[float]:
    """Vecteur déterministe d'un texte : un mot haché (SHA-256) vers une coordonnée, norme 1.

    Un texte sans mot donne le vecteur nul.
    """
    vector = [0.0] * dim
    for word in re.findall(r"\w+", text.lower()):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def unavailable_llm() -> FakeLLMClient:
    """Doublure qui imite l'absence de clé d'API : chaque appel renvoie ``None``."""
    return FakeLLMClient(available=False)


__all__ = ["FakeLLMClient", "hash_embedding", "unavailable_llm"]
