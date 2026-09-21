"""Replay a fixed list of choices. Used by tests and by the CLI."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence

from ..errors import DeciderStuck
from ..types import Choice, Step, Task, View


class ScriptedDecider:
    """Hands out choices in order, and says so when it runs out."""

    def __init__(self, choices: Iterable[Choice]) -> None:
        self._queue = deque(choices)
        self._served = 0

    @property
    def served(self) -> int:
        return self._served

    @property
    def remaining(self) -> int:
        return len(self._queue)

    def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice:
        del task, view, steps
        if not self._queue:
            raise DeciderStuck("script is exhausted")
        self._served += 1
        return self._queue.popleft()
