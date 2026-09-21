"""The four seams. Everything else in the package is an implementation.

* ``Source``    -- one way of seeing the screen (accessibility, pixels, anything).
* ``HitTester`` -- point on screen -> accessibility target, if one is there.
* ``Sensor``    -- what the hand actually sees and does. Owns settling.
* ``Decider``   -- what to do next. Knows nothing about macOS.
* ``Verifier``  -- decides whether a claim of DONE is true. Separate from the
                   decider on purpose: a decider must not be able to declare
                   its own work finished.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .types import Action, CheckResult, Choice, Step, Target, Task, View


@runtime_checkable
class Source(Protocol):
    """One perception source.

    Contract: only emit targets that are on screen *now*. Do not emit targets
    that are hidden, offscreen, collapsed, or zero-sized -- filtering here keeps
    the candidate set honest instead of teaching the decider to ignore junk.
    """

    name: str
    """Short id, e.g. ``"ax"`` or ``"ocr"``."""

    rank: int
    """Trust order. Lower wins. 0 = semantics, 10 = pixels."""

    def targets(self) -> tuple[Target, ...]:
        """Current targets, best reading per control, unsorted is fine."""
        ...


@runtime_checkable
class HitTester(Protocol):
    """Turns a pixel position back into an accessibility target.

    This is the single most valuable call in the whole design: it is what lets
    OCR act as a *pointer to* accessibility elements instead of a parallel
    universe of clickable rectangles.
    """

    def hit(self, x: float, y: float) -> Target | None:
        """The accessibility target at this point, or None if there is none."""
        ...


@runtime_checkable
class Sensor(Protocol):
    """Observe, aim, act, settle.

    ``settle`` belongs to the sensor rather than to the loop because only the
    sensor knows what "quiet" means here: a poll for some backends, an
    accessibility notification stream for macOS.
    """

    def observe(self) -> View:
        """One observation. Must be cheap enough to call in a settle loop."""
        ...

    def is_stale(self, view: View, action: Action) -> bool:
        """True if the action's guard no longer matches the live screen."""
        ...

    def act(self, view: View, action: Action) -> str:
        """Perform the action. Raise StaleTarget / CannotDo on failure.

        Do not re-run the freshness gate here: the runtime runs ``is_stale``
        immediately before this call, and a second check doubles the cost of every
        action. Only a genuine race between the gate and the execution belongs here.

        Returns the route taken (``"ax-press"``, ``"click"``, ``"keys"``...),
        so the trace can show how much of a run was done semantically rather
        than through pixels and keystrokes.
        """
        ...

    def settle(self, before: View, *, budget_ms: int) -> View:
        """Wait until the screen stops changing, then observe. Never raises."""
        ...


@runtime_checkable
class Decider(Protocol):
    """Chooses the next thing to do.

    May raise: every exception is counted against the failure budget, recorded
    in the trace, and the loop continues. A flaky model call must not end a run.
    """

    def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice: ...


@runtime_checkable
class Verifier(Protocol):
    """Confirms or rejects a decider's claim that checks are satisfied."""

    def confirm(self, *, task: Task, view: View, claims: Sequence[str]) -> tuple[CheckResult, ...]:
        """One result per claim. Unverified claims must come back ``ok=False``."""
        ...
