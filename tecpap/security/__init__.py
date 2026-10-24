"""Sécurité autour du modèle de langue : détection d'injections, filtre des réponses, masquage, journal.

- :mod:`tecpap.security.guardrails` : nettoyage et contrôle des messages reçus, délimitation du
  contenu externe, canari, filtre des réponses générées ;
- :mod:`tecpap.security.redaction` : repérage et masquage des numéros de téléphone et des emails ;
- :mod:`tecpap.security.events` : journal des événements de sécurité ;
- :mod:`tecpap.security.signals` et :mod:`tecpap.security.text` : signaux pondérés et
  normalisation de texte utilisés par la détection.

Ces contrôles sont une défense en profondeur : l'autorisation est appliquée dans le code
(chaque outil de l'assistant est limité au numéro de l'expéditeur) et une commande légitime ne
doit jamais être bloquée.
"""

from tecpap.security.events import ensure_schema, log_security_event, recent_security_events
from tecpap.security.guardrails import (
    SUSPICION_THRESHOLD,
    UNTRUSTED_CONTENT_RULE,
    InputCheck,
    OutputCheck,
    OutputFilter,
    canary_instruction,
    inspect_input,
    neutralize_tags,
    new_canary,
    sanitize_text,
    untrusted_content_rule,
    wrap_untrusted,
)
from tecpap.security.redaction import mask_phone, redact_emails, redact_phones

__all__ = [
    "SUSPICION_THRESHOLD",
    "UNTRUSTED_CONTENT_RULE",
    "InputCheck",
    "OutputCheck",
    "OutputFilter",
    "canary_instruction",
    "ensure_schema",
    "inspect_input",
    "log_security_event",
    "mask_phone",
    "neutralize_tags",
    "new_canary",
    "recent_security_events",
    "redact_emails",
    "redact_phones",
    "sanitize_text",
    "untrusted_content_rule",
    "wrap_untrusted",
]
