"""A rule-based decider: ordered ``when -> then`` pairs over the live view.

This is the honest baseline. It is deterministic, needs no network, and is what
the first real-machine milestone runs on. An LLM decider can be dropped in later
without touching the loop, because ``Decider`` is the only thing it has to
implement.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..types import Choice, Status, Step, Task, Verb, View

When = Callable[[Task, View, tuple[Step, ...]], bool]
Then = Callable[[Task, View, tuple[Step, ...]], Choice]


@dataclass(frozen=True, slots=True)
class Rule:
    when: When
    then: Then


def rule(*, when: When, then: Then) -> Rule:
    return Rule(when, then)


class RuleDecider:
    """First matching rule wins."""

    def __init__(self, rules: Sequence[Rule], *, fallback: Choice | None = None) -> None:
        self._rules = list(rules)
        self._fallback = fallback

    def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice:
        for item in self._rules:
            if item.when(task, view, tuple(steps)):
                return item.then(task, view, tuple(steps))
        if self._fallback is not None:
            return self._fallback
        return Choice(
            finish=Status.ESCALATE,
            why="no rule matched the current view and no fallback was configured",
        )


# --------------------------------------------------------------------------- #
# conditions
# --------------------------------------------------------------------------- #


def has(label: str | None = None, *, kind: str | None = None) -> When:
    """The view offers an enabled target matching this label and/or kind."""

    def when(task: Task, view: View, steps: tuple[Step, ...]) -> bool:
        del task, steps
        return bool(view.find(label=label, kind=kind))

    return when


def untouched(label: str) -> When:
    """No step so far has aimed at a target with this label.

    The cheapest possible loop guard: never press the same thing twice.
    """

    def when(task: Task, view: View, steps: tuple[Step, ...]) -> bool:
        del task, view
        for step in steps:
            if step.target_label and label.casefold() in step.target_label.casefold():
                return False
        return True

    return when


# --------------------------------------------------------------------------- #
# choices
# --------------------------------------------------------------------------- #


def press(label: str, *, kind: str | None = None) -> Then:
    def then(task: Task, view: View, steps: tuple[Step, ...]) -> Choice:
        del task, steps
        found = view.find(label=label, kind=kind)
        return Choice(
            verb=Verb.PRESS,
            target_label=found[0].label if len(found) == 1 else label,
            why=f"pressing {label!r}",
        )

    return then


def type_into(label: str, value_from: str) -> Then:
    def then(task: Task, view: View, steps: tuple[Step, ...]) -> Choice:
        del task, steps
        found = view.find(label=label)
        return Choice(
            verb=Verb.TYPE,
            target_label=found[0].label if len(found) == 1 else label,
            value_from=value_from,
            why=f"typing {value_from!r} into {label!r}",
        )

    return then


def press_key(key: str) -> Then:
    def then(task: Task, view: View, steps: tuple[Step, ...]) -> Choice:
        del task, view, steps
        return Choice(verb=Verb.KEY, key=key, why=f"pressing {key}")

    return then


def scroll(direction: str) -> Then:
    def then(task: Task, view: View, steps: tuple[Step, ...]) -> Choice:
        del task, view, steps
        return Choice(verb=Verb.SCROLL, scroll=direction, why=f"scrolling {direction}")

    return then


def stop(status: Status, why: str, *, says: Sequence[str] = ()) -> Then:
    """Declare the run finished. DONE still has to survive the Verifier."""

    def then(task: Task, view: View, steps: tuple[Step, ...]) -> Choice:
        del task, view, steps
        return Choice(finish=status, says=tuple(says), why=why)

    return then
