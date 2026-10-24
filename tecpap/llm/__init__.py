"""Accès aux modèles de langue (LLM, grands modèles de langue) de l'API OpenAI.

Le module :mod:`tecpap.llm.client` est le seul endroit du paquet qui importe le SDK OpenAI :
les autres modules passent par :class:`LLMClient`, qu'on remplace dans les tests par une
doublure sans accès réseau.
"""

from tecpap.llm.client import ChatResult, LLMClient, ToolCall

__all__ = ["ChatResult", "LLMClient", "ToolCall"]
