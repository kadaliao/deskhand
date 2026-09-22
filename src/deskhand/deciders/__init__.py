"""Deciders that ship with the package.

``RuleDecider`` and ``ScriptedDecider`` are deterministic and offline, which is
what makes the whole loop -- including every failure path -- testable without a
model, a network or a Mac. ``LLMDecider`` asks a model through the seam in
``deskhand.model``; it needs a transport, and it changes nothing else.
"""

from .llm import LLMDecider, choice_from_reply, prompt_for
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
    "LLMDecider",
    "Rule",
    "RuleDecider",
    "ScriptedDecider",
    "choice_from_reply",
    "has",
    "press",
    "press_key",
    "prompt_for",
    "scroll",
    "stop",
    "type_into",
    "untouched",
]
