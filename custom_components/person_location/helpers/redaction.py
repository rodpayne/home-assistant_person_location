"""redaction.py - Helper functions for redacting sensitive data in diagnostics.

Redact when logging to avoid exposing sensitive information:

API keys
passwords/secrets
token-like configuration values
nested dictionaries/lists
values embedded in configuration structures
"""

import logging

from ..const import (
    DEFAULT_API_KEY_NOT_SET,
    REDACT_KEYS,
)

_LOGGER = logging.getLogger(__name__)


def redact_sensitive_data(data: dict) -> dict:
    """Return a copy of data with sensitive fields redacted."""
    redacted = {}

    for key, value in data.items():
        if key in REDACT_KEYS and value != DEFAULT_API_KEY_NOT_SET:
            redacted[key] = "**REDACTED**"
        elif isinstance(value, dict):
            redacted[key] = redact_sensitive_data(value)
        else:
            redacted[key] = value

    return redacted
