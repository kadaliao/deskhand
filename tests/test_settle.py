"""The settle loop is tested by iteration, not by stopwatch.

It used to sleep 50 ms before its first look and 50 ms between looks, which cost
about 100 ms on every single step regardless of how fast the interface settled. No
test existed, so nothing noticed. These tests are the ones that would have.
"""

from __future__ import annotations

from deskhand.settle import BACKOFF_CAP_S, MAX_FRAMES, converge


class Clock:
    """A clock that only advances when something sleeps on it."""

    def __init__(self) -> None:
        self.t = 0.0
        self.naps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.t += seconds


def walker(*signatures: str) -> object:
    """A look function that replays signatures, then repeats the last forever."""
    seen: list[int] = []

    def look() -> str:
        index = min(len(seen), len(signatures) - 1)
        seen.append(1)
        return signatures[index]

    return look


class TestQuietDesktop:
    def test_a_desktop_that_is_already_quiet_does_not_sleep_at_all(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "a"), lambda s: s, budget_s=10.0, now=clock.now, sleep=clock.sleep
        )
        assert result.stable is True
        assert clock.naps == []

    def test_the_first_look_is_a_baseline_and_then_two_agreements(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "a"), lambda s: s, budget_s=10.0, now=clock.now, sleep=clock.sleep
        )
        assert result.frames == 3  # a baseline look plus the default two agreements

    def test_a_longer_quiet_window_costs_proportionally(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "a", "a", "a"),
            lambda s: s,
            budget_s=10.0,
            agreements=4,
            now=clock.now,
            sleep=clock.sleep,
        )
        assert result.frames == 5  # the baseline plus four agreements
        assert clock.naps == []


class TestSettling:
    def test_a_change_resets_the_quiet_count(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "b", "c", "c"), lambda s: s, budget_s=10.0, now=clock.now, sleep=clock.sleep
        )
        assert result.stable is True
        assert result.frames == 5  # a, b, c, then two more agreeing with c
        assert result.last == "c"

    def test_it_does_not_sleep_while_the_desktop_is_still_settling_quickly(self) -> None:
        clock = Clock()
        # Two changes, then quiet: the backoff only starts once agreements fail.
        converge(
            walker("a", "b", "c", "c"), lambda s: s, budget_s=10.0, now=clock.now, sleep=clock.sleep
        )
        assert clock.naps == [] or max(clock.naps) < 0.01

    def test_the_last_value_is_the_one_returned(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "b", "b"), lambda s: s, budget_s=10.0, now=clock.now, sleep=clock.sleep
        )
        assert result.last == "b"


class TestBounds:
    def test_a_churning_desktop_gives_up_at_the_frame_cap(self) -> None:
        # A page that changes on every read will never agree with itself, and the cap
        # is what stops it spending the whole budget, one step at a time.
        clock = Clock()
        counter = iter(range(10_000))
        result = converge(
            lambda: str(next(counter)),
            lambda s: s,
            budget_s=100.0,
            now=clock.now,
            sleep=clock.sleep,
        )
        assert result.stable is False
        assert result.frames == MAX_FRAMES
        assert clock.t < 100.0

    def test_the_budget_still_bounds_a_walk_that_is_slow(self) -> None:
        # When a walk is expensive, the budget is what stops the loop, not the cap.
        clock = Clock()

        def slow_look() -> str:
            clock.t += 0.3
            return str(clock.t)

        result = converge(slow_look, lambda s: s, budget_s=1.0, now=clock.now, sleep=clock.sleep)
        assert result.stable is False
        assert result.frames == 4

    def test_a_never_settling_desktop_sleeps_rather_than_spins(self) -> None:
        clock = Clock()
        counter = iter(range(10_000))
        converge(
            lambda: str(next(counter)),
            lambda s: s,
            budget_s=100.0,
            now=clock.now,
            sleep=clock.sleep,
        )
        assert clock.naps
        assert max(clock.naps) <= BACKOFF_CAP_S

    def test_the_backoff_is_capped(self) -> None:
        clock = Clock()
        counter = iter(range(10_000))
        converge(
            lambda: str(next(counter)),
            lambda s: s,
            budget_s=100.0,
            max_frames=100,
            now=clock.now,
            sleep=clock.sleep,
        )
        assert max(clock.naps) == BACKOFF_CAP_S

    def test_a_zero_budget_still_looks_once(self) -> None:
        clock = Clock()
        result = converge(walker("a"), lambda s: s, budget_s=0.0, now=clock.now, sleep=clock.sleep)
        assert result.frames == 1
        assert result.stable is False
        assert result.last == "a"

    def test_a_negative_budget_does_not_explode(self) -> None:
        clock = Clock()
        result = converge(walker("a"), lambda s: s, budget_s=-1.0, now=clock.now, sleep=clock.sleep)
        assert result.frames == 1


class TestStartFrom:
    """Why ``before`` is needed: a walk taken right after acting still shows the state
    from before the action, and two more identical walks agree with it, so the loop
    would return having waited for nothing. The pair of rules is "an action that
    changed the shape must settle", and "an action that never changes the shape is
    allowed to be silent after a grace period"."""

    def test_an_action_with_no_visible_effect_is_accepted_after_the_grace(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "a"),
            lambda s: s,
            budget_s=10.0,
            start_from="a",
            grace_frames=4,
            now=clock.now,
            sleep=clock.sleep,
        )
        assert result.stable is True
        assert result.frames == 4

    def test_a_change_from_the_before_state_settles_normally(self) -> None:
        clock = Clock()
        result = converge(
            walker("b", "b"),
            lambda s: s,
            budget_s=10.0,
            start_from="a",
            now=clock.now,
            sleep=clock.sleep,
        )
        assert result.stable is True
        assert result.frames == 3
        assert clock.naps == []

    def test_without_a_before_state_the_baseline_is_the_first_look(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "a"), lambda s: s, budget_s=10.0, now=clock.now, sleep=clock.sleep
        )
        assert result.stable is True
        assert result.frames == 3


class TestReporting:
    def test_slept_time_is_reported_in_milliseconds(self) -> None:
        clock = Clock()
        counter = iter(range(10_000))
        result = converge(
            lambda: str(next(counter)), lambda s: s, budget_s=0.05, now=clock.now, sleep=clock.sleep
        )
        assert result.slept_ms == round(result.slept_s * 1000)
        assert result.slept_ms > 0

    def test_a_quiet_desktop_reports_no_time_slept(self) -> None:
        clock = Clock()
        result = converge(
            walker("a", "a"), lambda s: s, budget_s=10.0, now=clock.now, sleep=clock.sleep
        )
        assert result.slept_ms == 0
        assert result.slept_s == 0.0
