"""Waiting for an interface to stop moving.

This is small enough to look obviously correct and it was wrong for a while: the
loop slept 50 ms before its first look and 50 ms between looks, so it spent most
of its time asleep *after* the interface had already settled. On a warm
accessibility walk (about 5 to 10 ms) that was a sixfold cost on every step, and no
test existed to notice.

It lives in its own module with its clock and sleep injected so it can be tested
by the iteration rather than by stopwatch.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

GRACE_FRAMES = 4
"""Looks to wait for an action that never changes the shape of anything.

Scrolling moves geometry, ``WAIT`` does nothing, and typing changes a value: none
of those alter the shape, so waiting for a shape change would burn the whole
budget. Four looks is the compromise between "do not overrule a slow application"
and "do not pay for nothing".
"""

MAX_FRAMES = 6
"""Hard cap on looks, so an interface that never stops moving cannot spend the
whole settle budget. Hitting it is reported rather than hidden, and it is what
stops a page that is churning on every single read -- a live log, a spinner, a
progress bar -- from costing a full second per step.

Six rather than twelve: with a warm walk at 5 ms this is 30 ms, and on a page that
never settles it is the difference between half a second and a second. A genuine
slow effect does not need more looks, it needs the two agreements that follow it,
and those happen at whatever the application's own pace is inside this cap."""

AGREEMENTS = 2
"""How many consecutive *comparisons* must agree before an interface is quiet.

Two agreements take three looks, because the first look is the baseline. One
agreement would be two looks and is cheaper by a walk (about 5 to 10 ms), but a
mid-animation coincidence is exactly the thing settling exists to avoid catching.
"""

BACKOFF_CAP_S = 0.02
"""Longest pause between looks while the interface is still changing."""


@dataclass(frozen=True, slots=True)
class Convergence[T]:
    """What happened while waiting."""

    last: T
    frames: int
    slept_s: float
    stable: bool
    """Whether the interface agreed with itself. False means the frame cap or the
    budget ran out first, which is worth reporting rather than hiding."""

    @property
    def slept_ms(self) -> int:
        return round(self.slept_s * 1000)


def converge[T](
    look: Callable[[], T],
    key: Callable[[T], str],
    *,
    budget_s: float,
    start_from: str | None = None,
    agreements: int = AGREEMENTS,
    grace_frames: int = GRACE_FRAMES,
    max_frames: int = MAX_FRAMES,
    backoff_cap_s: float = BACKOFF_CAP_S,
    now: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> Convergence[T]:
    """Look repeatedly until the interface agrees with itself.

    ``start_from`` is the signature from *before* the action and is the reason this
    works at all. Without it, a walk taken immediately after acting still shows the
    pre-action state, two more identical walks agree with it, and the loop returns
    proudly having waited for nothing. With it, "still the same as before" is
    understood as "the effect has not appeared yet".

    An action that never changes the shape -- a scroll, a typed character, a wait --
    is given ``grace_frames`` looks before its silence is accepted as settled.
    """
    deadline = now() + max(0.0, budget_s)
    previous: str | None = start_from
    moved = False
    stable = 0
    frames = 0
    slept = 0.0

    while True:
        current = look()
        frames += 1
        signature = key(current)
        if start_from is None or signature != start_from:
            moved = True

        if previous is not None and signature == previous:
            stable += 1
            if stable >= agreements and (moved or frames >= grace_frames):
                return Convergence(current, frames, slept, True)
        else:
            stable = 0
        previous = signature

        if now() >= deadline or frames >= max_frames:
            return Convergence(current, frames, slept, False)

        if stable == 0 and frames >= grace_frames:
            # Yield only while the interface is actually moving. Sleeping on a walk
            # that agreed with the previous one is what made this loop cost 100 ms on
            # a desktop that had already settled.
            pause = min(0.002 * (frames - grace_frames + 1), backoff_cap_s)
            sleep(pause)
            slept += pause
