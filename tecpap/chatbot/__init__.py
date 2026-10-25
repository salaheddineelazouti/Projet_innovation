"""Assistant conversationnel WhatsApp de TECPAP.

- :mod:`tecpap.chatbot.bot` : :class:`WhatsAppAssistant`, automate de conversation (commande,
  renouvellement, suivi, questions, transfert à l'équipe commerciale) ;
- :mod:`tecpap.chatbot.intents` : intention d'un message, par règles ;
- :mod:`tecpap.chatbot.messages` : modèles de réponse en français, en darija et en arabe standard ;
- :mod:`tecpap.chatbot.tools` : :class:`AssistantTools`, seul accès aux données, limité à l'expéditeur ;
- :mod:`tecpap.chatbot.conversation` : :class:`ConversationStore`, état des conversations et messages.

Les noms publics sont chargés à la première utilisation : :mod:`tecpap.rag.answer` importe les
modèles de réponse de ce paquet, et :mod:`tecpap.chatbot.bot` importe :mod:`tecpap.rag.answer`.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "BotReply": "tecpap.chatbot.bot",
    "WhatsAppAssistant": "tecpap.chatbot.bot",
    "Conversation": "tecpap.chatbot.conversation",
    "ConversationStore": "tecpap.chatbot.conversation",
    "ensure_schema": "tecpap.chatbot.conversation",
    "IntentResult": "tecpap.chatbot.intents",
    "detect_intent": "tecpap.chatbot.intents",
    "render": "tecpap.chatbot.messages",
    "AssistantTools": "tecpap.chatbot.tools",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"le module {__name__!r} n'a pas d'attribut {name!r}")
    return getattr(importlib.import_module(module), name)
