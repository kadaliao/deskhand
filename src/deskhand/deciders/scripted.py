"""Replay a fixed list of choices. Used by tests and by the CLI."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence

from ..errors import BadChoice, DeciderStuck
from ..types import Choice, Step, Task, View
from ..validate import resolve_target


class ScriptedDecider:
    """Hands out choices in order, and says so when it runs out.

    A script is a plan written before the screen existed. When the choice it just gave
    was refused because its control was not in the view, and the control is in the view
    it is shown now, the same choice is offered once more instead of skipping to the next
    one: the step was early, not wrong. Only once, and only when the control resolves
    now, so a choice that is wrong for good still fails and moves on.
    """

    def __init__(self, choices: Iterable[Choice]) -> None:
        self._queue = deque(choices)
        self._served = 0
        self._last: Choice | None = None
        self._retried = False

    @property
    def served(self) -> int:
        return self._served

    @property
    def remaining(self) -> int:
        return len(self._queue)

    def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice:
        del task
        last = self._last
        if (
            last is not None
            and not self._retried
            and steps
            and steps[-1].choice is last
            and steps[-1].failed == "BadChoice"
            and _resolves(last, view)
        ):
            self._retried = True
            return last
        if not self._queue:
            raise DeciderStuck("script is exhausted")
        self._served += 1
        self._last = self._queue.popleft()
        self._retried = False
        return self._last


def _resolves(choice: Choice, view: View) -> bool:
    if choice.target is None and choice.target_label is None:
        return False
    try:
        return resolve_target(choice, view, role="target") is not None
    except BadChoice:
        return False
