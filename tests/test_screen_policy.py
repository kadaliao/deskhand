"""When the pixel overlay is worth its cost. Testable anywhere: no framework at import.

The "auto" choice used to be a count of accessibility targets. Counting was the wrong
proxy for "is this window described richly": it answers "are there many targets", and the
question is "how many of them can be matched by a name a person would recognise".
System Settings is the counterexample this project kept tripping over -- 104 targets, 64 of
them with no label, 27 of those the sidebar rows a task needs. More targets than almost any
window, and precisely the ones that matter had nothing to match on, so "auto" skipped the
overlay on the one window that needed it and M1's acceptance was unmeasurable through the
default path.
"""

from __future__ import annotations

from deskhand.sensors.macos.screen import MacSensor
from deskhand.types import Box, Target, Verb


def _targets(total: int, *, unnamed: int) -> tuple[Target, ...]:
    return tuple(
        Target(
            id=f"t{index}",
            kind="button",
            label="" if index < unnamed else f"named{index}",
            actions=frozenset({Verb.PRESS}),
            box=Box(0, 0, 10, 10),
        )
        for index in range(total)
    )


def want(total: int, *, unnamed: int) -> bool:
    return MacSensor(pixels="auto")._want_pixels(_targets(total, unnamed=unnamed))


class TestTheAutomaticChoice:
    def test_a_few_targets_are_worth_a_look(self) -> None:
        assert want(10, unnamed=0) is True

    def test_many_named_targets_are_not_worth_a_look(self) -> None:
        assert want(60, unnamed=0) is False

    def test_many_targets_with_no_names_are_worth_a_look(self) -> None:
        """The measured System Settings case: 104 targets, 64 of them unnamed."""
        assert want(104, unnamed=64) is True

    def test_the_boundary_is_a_proportion_of_unnamed_targets(self) -> None:
        assert want(100, unnamed=24) is False
        assert want(100, unnamed=25) is True

    def test_an_empty_window_is_still_looked_at(self) -> None:
        assert want(0, unnamed=0) is True

    def test_an_explicit_answer_wins_over_the_heuristic(self) -> None:
        assert MacSensor(pixels=True)._want_pixels(_targets(1000, unnamed=0)) is True
        assert MacSensor(pixels=False)._want_pixels(_targets(1, unnamed=1)) is False

    def test_the_two_thresholds_are_configurable(self) -> None:
        assert MacSensor(pixels="auto", rich_at=2)._want_pixels(_targets(3, unnamed=0)) is False
        assert (
            MacSensor(pixels="auto", unnamed_pct=90)._want_pixels(_targets(100, unnamed=50))
            is False
        )
