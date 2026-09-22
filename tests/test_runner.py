from __future__ import annotations

import time
from collections.abc import Sequence

import pytest

from deskhand import demo
from deskhand.deciders.scripted import ScriptedDecider
from deskhand.runner import Runner
from deskhand.sensors.fake import FakeSensor
from deskhand.types import (
    Action,
    Box,
    Choice,
    Limits,
    Status,
    Step,
    Target,
    Task,
    Verb,
    View,
)
from deskhand.validate import build_action
from deskhand.verify import PredicateVerifier, WaivedVerifier

TASK = Task(goal="press things", checks=("everything pressed",), inputs={"text": "hello"})

A = Target(
    id="a", kind="button", label="Alpha", actions=frozenset({Verb.PRESS}), box=Box(0, 0, 10, 10)
)
B = Target(
    id="b", kind="button", label="Beta", actions=frozenset({Verb.PRESS}), box=Box(20, 0, 10, 10)
)
C = Target(
    id="c", kind="button", label="Gamma", actions=frozenset({Verb.PRESS}), box=Box(40, 0, 10, 10)
)
GHOST = Target(
    id="ghost_edge",
    kind="button",
    label="Ghost Edge",
    actions=frozenset({Verb.PRESS}),
    box=Box(60, 0, 10, 10),
)

EDGES = {("s1", "a", Verb.PRESS): "s2", ("s2", "b", Verb.PRESS): "s3"}


def sensor(**kwargs: object) -> FakeSensor:
    return FakeSensor(
        {"s1": (A, GHOST), "s2": (A, B, GHOST), "s3": (A, B, C, GHOST)},
        start="s1",
        edges=EDGES,
        **kwargs,  # type: ignore[arg-type]
    )


def yes(task: Task, view: View) -> bool:
    del task, view
    return True


def verifier() -> PredicateVerifier:
    return PredicateVerifier({"everything pressed": yes, "done": yes})


def press(label: str, target_id: str | None = None) -> Choice:
    return Choice(verb=Verb.PRESS, target=target_id, target_label=None if target_id else label)


class TestHappyPath:
    def test_the_demo_reaches_done(self) -> None:
        report = Runner(sensor=demo.sensor(), decider=demo.script(), verifier=demo.verifier()).run(
            demo.task()
        )
        assert report.status is Status.DONE
        assert [c.ok for c in report.checked] == [True]

    def test_the_deliberately_wrong_choice_is_recorded_not_fatal(self) -> None:
        report = Runner(sensor=demo.sensor(), decider=demo.script(), verifier=demo.verifier()).run(
            demo.task()
        )
        failures = [s for s in report.steps if s.failed]
        assert [s.failed for s in failures] == ["BadChoice"]

    def test_every_executed_step_reports_its_route(self) -> None:
        report = Runner(sensor=demo.sensor(), decider=demo.script(), verifier=demo.verifier()).run(
            demo.task()
        )
        routes = [s.via for s in report.steps if s.action is not None]
        assert routes == ["fake", "fake", "fake"]

    def test_steps_carry_per_phase_timings(self) -> None:
        report = Runner(sensor=demo.sensor(), decider=demo.script(), verifier=demo.verifier()).run(
            demo.task()
        )
        executed = [s for s in report.steps if s.action is not None]
        assert executed
        for step in executed:
            assert step.times.total == (
                step.times.look + step.times.decide + step.times.act + step.times.settle
            )
            assert step.times.total >= 0

    def test_the_terminal_step_is_part_of_the_trace(self) -> None:
        report = Runner(sensor=demo.sensor(), decider=demo.script(), verifier=demo.verifier()).run(
            demo.task()
        )
        assert report.steps[-1].choice is not None
        assert report.steps[-1].choice.finish is Status.DONE


class TestCompletionIsNotTheDecidersCall:
    def test_done_without_a_verifier_escalates(self) -> None:
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("everything pressed",))])
        report = Runner(sensor=sensor(), decider=decider).run(TASK)
        assert report.status is Status.ESCALATE
        assert "not confirmed" in report.why
        assert [c.ok for c in report.checked] == [False]

    def test_the_decider_does_not_choose_what_gets_checked(self) -> None:
        """DONE is a claim about every criterion, not about the ones it happened to name."""
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("an easier claim",))])
        verifier = PredicateVerifier({"everything pressed": yes, "an easier claim": yes})
        report = Runner(sensor=sensor(), decider=decider, verifier=verifier).run(TASK)
        assert [c.check for c in report.checked] == ["everything pressed"]

    def test_naming_a_subset_does_not_narrow_what_is_verified(self) -> None:
        task = Task(goal="press things", checks=("one", "two"))
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("one",))])
        report = Runner(
            sensor=sensor(), decider=decider, verifier=PredicateVerifier({"one": yes, "two": yes})
        ).run(task)
        assert [c.check for c in report.checked] == ["one", "two"]
        assert report.status is Status.DONE

    def test_a_subset_claim_cannot_hide_an_unmet_criterion(self) -> None:
        """The hole this closes: the decider used to pick which criterion was checked.

        With ``claims = tuple(choice.says) or task.checks``, naming only the criterion
        that happens to pass reported DONE while the criterion that fails was never
        looked at. An untrusted decider (a model) makes that reachable.
        """
        task = Task(goal="press things", checks=("one", "two"))
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("one",))])
        report = Runner(
            sensor=sensor(),
            decider=decider,
            verifier=PredicateVerifier({"one": yes, "two": lambda t, v: False}),
        ).run(task)
        assert report.status is Status.ESCALATE
        assert "two" in report.why

    def test_an_unregistered_check_stays_unverified(self) -> None:
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("everything pressed",))])
        report = Runner(sensor=sensor(), decider=decider, verifier=PredicateVerifier({})).run(TASK)
        assert report.status is Status.ESCALATE
        assert "no predicate registered" in report.checked[0].how

    def test_a_failed_verification_does_not_become_done(self) -> None:
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("everything pressed",))])
        report = Runner(
            sensor=sensor(),
            decider=decider,
            verifier=PredicateVerifier({"everything pressed": lambda t, v: False}),
        ).run(TASK)
        assert report.status is Status.ESCALATE

    def test_a_decider_may_report_stuck_directly(self) -> None:
        decider = ScriptedDecider([Choice(finish=Status.STUCK, why="nothing fits")])
        report = Runner(sensor=sensor(), decider=decider).run(TASK)
        assert report.status is Status.STUCK
        assert report.why == "nothing fits"


class TestTheWaiverIsNotAConfirmation:
    """``--trust-decider`` must read as a waiver, not as a passing check.

    Regression: it seeded a predicate that returned ``True``, so a report said
    "predicate matched the live view" for a check that nothing performed.

    Measured on a real machine: on a Chinese macOS the appearance task resolved
    ``外观`` to the settings *window* rather than the sidebar row, coordinate-clicked
    that window, failed to disambiguate ``深色``, changed nothing, and was reported
    ``DONE`` with that wording while the machine stayed in Light mode.
    """

    def test_a_waived_check_does_not_claim_a_match(self) -> None:
        task = Task(goal="do it", checks=("it is done",))
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("it is done",))])
        report = Runner(sensor=sensor(), decider=decider, verifier=WaivedVerifier()).run(task)
        how = report.checked[0].how
        assert report.status is Status.DONE
        assert report.checked[0].ok is True
        assert "WAIVED" in how
        assert "NOT independently checked" in how
        assert "matched" not in how

    def test_a_waiver_still_covers_every_criterion(self) -> None:
        task = Task(goal="do it", checks=("one", "two", "three"))
        decider = ScriptedDecider([Choice(finish=Status.DONE, says=("one",))])
        report = Runner(sensor=sensor(), decider=decider, verifier=WaivedVerifier()).run(task)
        assert [c.check for c in report.checked] == ["one", "two", "three"]


class TestFailuresAreSurvivable:
    def test_a_decider_that_raises_is_budgeted_not_fatal(self) -> None:
        class Boom:
            def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice:
                del task, view, steps
                raise RuntimeError("model exploded")

        report = Runner(sensor=sensor(), decider=Boom(), limits=Limits(max_failures=2)).run(TASK)
        assert report.status is Status.ESCALATE
        assert "decider failed 3 times" in report.why
        assert "model exploded" in report.why
        assert [s.failed for s in report.steps] == ["RuntimeError"] * 3

    def test_an_impossible_target_is_recorded_then_the_run_continues(self) -> None:
        decider = ScriptedDecider(
            [
                press("Nothing"),
                press("Alpha", "a"),
                press("Beta", "b"),
                Choice(finish=Status.DONE, says=("everything pressed",)),
            ]
        )
        report = Runner(sensor=sensor(), decider=decider, verifier=verifier()).run(TASK)
        assert report.status is Status.DONE
        assert report.steps[0].failed == "BadChoice"
        assert "no target matching" in report.steps[0].why

    def test_an_action_the_backend_cannot_do_does_not_end_the_run(self) -> None:
        decider = ScriptedDecider(
            [
                press("Ghost Edge", "ghost_edge"),
                press("Alpha", "a"),
                press("Beta", "b"),
                Choice(finish=Status.DONE, says=("everything pressed",)),
            ]
        )
        report = Runner(sensor=sensor(), decider=decider, verifier=verifier()).run(TASK)
        assert report.status is Status.DONE
        assert report.steps[0].failed == "CannotDo"
        assert "no transition" in report.steps[0].why

    def test_a_stale_target_is_re_observed_and_retried(self) -> None:
        class StaleOnce(FakeSensor):
            def __init__(self) -> None:
                super().__init__(
                    {"s1": (A, GHOST), "s2": (A, B, GHOST), "s3": (A, B, C, GHOST)},
                    start="s1",
                    edges=EDGES,
                )
                self.calls = 0

            def is_stale(self, view: View, action: Action) -> bool:
                self.calls += 1
                return self.calls == 1

        decider = ScriptedDecider(
            [
                press("Alpha", "a"),
                press("Beta", "b"),
                Choice(finish=Status.DONE, says=("everything pressed",)),
            ]
        )
        report = Runner(sensor=StaleOnce(), decider=decider, verifier=verifier()).run(TASK)
        assert report.status is Status.DONE
        assert report.steps[0].failed == "StaleTarget"
        assert "changed between deciding and acting" in report.steps[0].why

    def test_the_failure_budget_is_forgiven_after_real_progress(self) -> None:
        class StaleTwice(FakeSensor):
            def __init__(self) -> None:
                super().__init__(
                    {"s1": (A, GHOST), "s2": (A, B, GHOST), "s3": (A, B, C, GHOST)},
                    start="s1",
                    edges=EDGES,
                )
                self.calls = 0

            def is_stale(self, view: View, action: Action) -> bool:
                self.calls += 1
                return self.calls in {1, 3}

        # Note the repeats: a rejected decision is never replayed, the decider is
        # asked again against the fresh view, and that consumes a decision.
        decider = ScriptedDecider(
            [
                press("Alpha", "a"),
                press("Alpha", "a"),
                press("Beta", "b"),
                press("Beta", "b"),
                Choice(finish=Status.DONE, says=("everything pressed",)),
            ]
        )
        report = Runner(
            sensor=StaleTwice(), decider=decider, verifier=verifier(), limits=Limits(max_failures=1)
        ).run(TASK)
        assert [s.failed for s in report.steps] == ["StaleTarget", None, "StaleTarget", None, None]
        assert report.status is Status.DONE


class TestBounds:
    def test_no_progress_is_declared_stuck(self) -> None:
        frozen = sensor(stuck_scenes=frozenset({"s1"}))
        decider = ScriptedDecider([press("Alpha", "a")] * 4)
        report = Runner(sensor=frozen, decider=decider, limits=Limits(no_progress_steps=2)).run(
            TASK
        )
        assert report.status is Status.STUCK
        assert "2 steps in a row changed nothing" in report.why

    def test_movement_alone_counts_as_something_happening(self) -> None:
        moved = Target(
            id="a",
            kind="button",
            label="Alpha",
            actions=frozenset({Verb.PRESS}),
            box=Box(500, 500, 10, 10),
        )
        sliding = FakeSensor(
            {"p1": (A,), "p2": (moved,)}, start="p1", edges={("p1", "a", Verb.PRESS): "p2"}
        )
        decider = ScriptedDecider(
            [press("Alpha", "a"), Choice(finish=Status.DONE, says=("everything pressed",))]
        )
        report = Runner(sensor=sliding, decider=decider, verifier=verifier()).run(TASK)
        step = report.steps[0]
        assert step.changed is False
        assert step.progress is True
        assert report.status is Status.DONE

    def test_the_step_budget_escalates(self) -> None:
        decider = ScriptedDecider([press("Alpha", "a"), press("Beta", "b"), press("Gamma", "c")])
        report = Runner(sensor=sensor(), decider=decider, limits=Limits(max_steps=2)).run(TASK)
        assert report.status is Status.ESCALATE
        assert "step budget of 2 exhausted" in report.why
        assert report.steps_taken == 2

    def test_the_wall_clock_budget_escalates(self) -> None:
        class Slow:
            def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice:
                del task, view, steps
                time.sleep(0.03)
                return press("Alpha", "a")

        report = Runner(sensor=sensor(), decider=Slow(), limits=Limits(max_ms=1)).run(TASK)
        assert report.status is Status.ESCALATE
        assert "wall clock budget of 1ms exhausted" in report.why

    def test_cancelling_stops_the_run(self) -> None:
        box: list[Runner] = []

        class CancelOnSecond:
            def __init__(self) -> None:
                self.calls = 0

            def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice:
                del task, view, steps
                self.calls += 1
                if self.calls == 2:
                    box[0].cancel()
                return press("Alpha", "a")

        runner = Runner(sensor=sensor(stuck_scenes=frozenset({"s1"})), decider=CancelOnSecond())
        box.append(runner)
        report = runner.run(TASK)
        assert report.status is Status.ESCALATE
        assert "cancelled by caller" in report.why


class TestSensorContract:
    def test_a_view_from_before_the_desktop_moved_is_stale(self) -> None:
        live = sensor()
        before = live.observe()
        action = build_action(press("Alpha", "a"), before, TASK)
        live.act(before, action)
        assert live.is_stale(before, action)

    def test_a_stale_action_never_lands_on_the_new_desktop(self) -> None:
        from deskhand.errors import CannotDo

        live = sensor()
        before = live.observe()
        action = build_action(press("Ghost Edge", "ghost_edge"), before, TASK)
        live.act(before, build_action(press("Alpha", "a"), before, TASK))
        with pytest.raises(CannotDo):
            live.act(before, action)
        assert len(live.done) == 1  # the stale action was not recorded as done

    def test_stepping_through_the_scripted_desktop_works(self) -> None:
        live = sensor()
        for expected_scene, (label, target_id) in enumerate(
            [("Alpha", "a"), ("Beta", "b")], start=1
        ):
            view = live.observe()
            action = build_action(press(label, target_id), view, TASK)
            assert not live.is_stale(view, action)
            live.act(view, action)
            assert live.scene == f"s{expected_scene + 1}"
