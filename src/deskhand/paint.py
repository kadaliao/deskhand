"""Colour for a terminal, and nothing at all for anything else.

The words carry the meaning -- ``FAIL``, ``DONE``, ``via=click`` -- and colour only
makes them faster to find. So colour is added only when a person is reading: never
into a pipe, a file or a test capture, never under ``NO_COLOR``
(https://no-color.org), and ``FORCE_COLOR`` turns it on where detection guesses wrong.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

CODES = {
    "ok": "32",  # green
    "bad": "31",  # red
    "warn": "33",  # yellow
    "dim": "2",
    "bold": "1",
    "head": "1;36",  # bold cyan
}

COORDINATE_ROUTES = frozenset({"click", "click-menu", "double-click", "drag"})
"""Routes that moved the real mouse to a point: the ones a trace should make easy to spot."""


def semantic(via: str | None) -> bool:
    """Whether a route acted through accessibility rather than the mouse or keyboard."""
    return bool(via) and str(via).startswith("ax-")


def enabled(stream: TextIO | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    out = stream if stream is not None else sys.stdout
    return bool(getattr(out, "isatty", lambda: False)())


def paint(text: str, role: str, *, on: bool) -> str:
    if not on or not text:
        return text
    return f"\033[{CODES[role]}m{text}\033[0m"


def status_role(status: str) -> str:
    return {"DONE": "ok", "STUCK": "bad"}.get(status, "warn")


def route_role(via: str | None) -> str:
    if semantic(via):
        return "ok"
    return "warn" if via in COORDINATE_ROUTES else "dim"
