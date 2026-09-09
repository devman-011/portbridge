"""Standalone input validation, kept separate from ui.py so it has no
import-time dependency on a terminal being attached (used by both the
interactive prompts and the non-interactive CLI argument parser)."""

from __future__ import annotations

from portbridge.errors import InvalidPortError

MIN_PORT = 1
MAX_PORT = 65535


def parse_port(value) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise InvalidPortError(value)
    if not (MIN_PORT <= port <= MAX_PORT):
        raise InvalidPortError(value)
    return port
