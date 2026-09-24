"""An application whose pane appears after the settle that followed the click.

M1's run on System Settings: the sidebar click settled on the sidebar's new selection
while the pane was still loading, so the next step's control ("浅色") was not in the view
it was checked against. The run must look again and let the script offer the step once
more, rather than skip it and claim a DONE nobody can confirm.
"""

from __future__ import annotations

from deskhand.deciders.scripted import ScriptedDecider
from deskhand.runner import Runner
from deskhand.sensors.fake import FakeSensor
from deskhand.types import Box, Choice, Status, Target, Task, Verb, View
from deskhand.verify import Expectation, ExpectVerifier

ROW = Target(
    id="row", kind="row", label="Appearance", actions=frozenset({Verb.PRESS}), box=Box(0, 0, 9, 9)
)
LIGHT = Target(
    id="light", kind="button", label="Light", actions=frozenset({Verb.PRESS}), box=Box(20, 0, 9, 9)
)
LIGHT_ON = Target(
    id="light",
    kind="button",
    label="Light",
    actions=frozenset({Verb.PRESS}),
    box=Box(20, 0, 9, 9),
    selected=True,
)
SPINNER = Target(id="spin", kind="progress", label="Loading", box=Box(40, 0, 9, 9))


class SlowPane(FakeSensor):
    """The pane finishes loading only on the second settle after the click."""

    def __init__(self) -> None:
        super().__init__(
            {
                "home": (ROW,),
                "loading": (ROW, SPINNER),
                "pane": (ROW, LIGHT),
                "done": (ROW, LIGHT_ON),
            },
            start="home",
            edges={("home", "row", Verb.PRESS): "loading", ("pane", "light", Verb.PRESS): "done"},
        )
        self.settles = 0

    def settle(self, before: View, *, budget_ms: int) -> View:
        self.settles += 1
        if self.scene == "loading" and self.settles >= 2:
            self._scene = "pane"
        return super().settle(before, budget_ms=budget_ms)


TASK = Task(goal="Light mode", checks=("Light is selected",))
SCRIPT = (
    Choice(verb=Verb.PRESS, target_label="Appearance"),
    Choice(verb=Verb.PRESS, target_label="Light"),
    Choice(finish=Status.DONE, says=("Light is selected",)),
)


def test_a_step_that_was_early_is_offered_again_once_the_pane_is_there() -> None:
    report = Runner(
        sensor=SlowPane(),
        decider=ScriptedDecider(SCRIPT),
        verifier=ExpectVerifier({"Light is selected": Expectation(label="Light", selected=True)}),
    ).run(TASK)
    assert report.status is Status.DONE
    assert [s.failed for s in report.steps] == [None, "BadChoice", None, None]
    assert report.steps[2].target_label == "Light"


def test_a_step_that_is_wrong_for_good_is_not_retried() -> None:
    script = (Choice(verb=Verb.PRESS, target_label="Nowhere"), *SCRIPT)
    decider = ScriptedDecider(script)
    report = Runner(sensor=SlowPane(), decider=decider).run(TASK)
    assert report.steps[0].failed == "BadChoice"
    assert report.steps[1].choice is script[1]  # moved on after one refusal
