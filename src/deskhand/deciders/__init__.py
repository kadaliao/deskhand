"""Deciders that ship with the package.

Both are deterministic and offline: the whole loop, including failure paths,
is testable without a model, a network, or a Mac.
"""

from .rule import (
    Rule,
    RuleDecider,
    has,
    press,
    press_key,
    scroll,
    stop,
    type_into,
    untouched,
)
from .scripted import ScriptedDecider

__all__ = [
    "Rule",
    "RuleDecider",
    "ScriptedDecider",
    "has",
    "press",
    "press_key",
    "scroll",
    "stop",
    "type_into",
    "untouched",
]
