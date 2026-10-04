"""Masking secrets in stored command payloads.

A staff member's password travels in the command payload (``staff.create`` / ``staff.update``). The bridge
overwrites it in the inbox once the command finishes (``FastApiIntegrationPlugin::scrubPayload``), and the
API masks it whenever it shows a payload, so a pending row or a row the scrub missed never reveals it either.

``SECRET_KEY`` must be the same pattern as the PHP constant of the same name in ``bridge/plugin.php`` —
``tests/unit/test_redaction.py`` compares them.
"""

import json
import re
from typing import Any

SECRET_KEY = r"pass(word|wd)?|secret|token"
_SECRET = re.compile(SECRET_KEY, re.IGNORECASE)
MASK = "[redacted]"


def redact(value: Any) -> Any:
    """A copy of ``value`` with the value of every secret-looking key replaced by ``[redacted]``."""
    if isinstance(value, dict):
        return {
            key: (MASK if isinstance(key, str) and _SECRET.search(key) and item not in (None, "") else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def without_secrets(value: Any) -> Any:
    """A copy of ``value`` with every secret-looking key dropped, for comparing two payloads of which one has
    already been scrubbed by the bridge."""
    if isinstance(value, dict):
        return {k: without_secrets(v) for k, v in value.items() if not (isinstance(k, str) and _SECRET.search(k))}
    if isinstance(value, list):
        return [without_secrets(v) for v in value]
    return value


def redact_json(text: str | None) -> str | None:
    """``redact`` for a JSON document held as text; anything that is not JSON is withheld rather than shown."""
    if text is None:
        return None
    try:
        return json.dumps(redact(json.loads(text)))
    except (TypeError, ValueError):
        return f'"{MASK}"'
