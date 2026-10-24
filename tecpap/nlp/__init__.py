"""Traitement automatique de la langue des messages clients, sans modèle de langue.

:mod:`tecpap.nlp.language` identifie la langue et l'écriture d'un message (français, anglais,
arabe standard, darija en lettres arabes ou latines) et en déduit la langue de réponse.
"""

from tecpap.nlp.language import LanguageResult, detect_language, reply_language

__all__ = ["LanguageResult", "detect_language", "reply_language"]
