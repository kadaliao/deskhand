from __future__ import annotations

import json

from deskhand import demo
from deskhand.rehearse import rehearse
from deskhand.types import Choice, Status, Task, Verb
from deskhand.verify import NoVerifier, PredicateVerifier

TASK = demo.task()
CLAIM = "Gaussian Blur is enabled on the clip"


def script(*choices: Choice) -> tuple[Choice, ...]:
    return choices


def home_rehearsal(verifier: object | None = None):
    sensor = demo.sensor()
    return rehearse(
        TASK,
        script(
            Choice(verb=Verb.PRESS, target_label="Nonexistent Control"),
            Choice(verb=Verb.PRESS, target_label="Effects"),
            Choice(verb=Verb.PRESS, target_label="Gaussian Blur"),
            Choice(finish=Status.DONE, says=(CLAIM,)),
        ),
        sensor.view("home"),
        verifier=verifier,
    )


class TestRehearsal:
    def test_a_missing_label_is_reported_without_running_anything(self) -> None:
        finding = home_rehearsal().findings[0]
        assert finding.ok is False
        assert "no target matching" in finding.detail

    def test_a_step_that_would_work_says_what_it_would_do(self) -> None:
        finding = home_rehearsal().findings[1]
        assert finding.ok is True
        assert "would PRESS button 'Effects'" in finding.detail
        assert finding.target is not None
        assert finding.target.id == "effects_button"

    def test_a_later_step_that_is_not_visible_yet_says_so(self) -> None:
        finding = home_rehearsal().findings[2]
        assert finding.ok is False
        assert "may reveal it" in finding.detail

    def test_only_the_first_step_is_a_real_verdict(self) -> None:
        assert home_rehearsal().first_blocked is True
        assert home_rehearsal().verdict.startswith("step 1 cannot run as written")

    def test_a_script_whose_first_step_works_is_reported_as_executable(self) -> None:
        sensor = demo.sensor()
        rehearsal = rehearse(
            TASK,
            script(
                Choice(verb=Verb.PRESS, target_label="Effects"),
                Choice(finish=Status.DONE, says=(CLAIM,)),
            ),
            sensor.view("home"),
            verifier=demo.verifier(),
        )
        assert rehearsal.first_blocked is False
        assert "step 1 is executable as written" in rehearsal.verdict

    def test_a_verb_the_target_does_not_offer_is_caught_before_running(self) -> None:
        sensor = demo.sensor()
        rehearsal = rehearse(
            TASK,
            script(Choice(verb=Verb.TYPE, target_label="Effects", value_from="x")),
            sensor.view("home"),
        )
        assert rehearsal.findings[0].ok is False
        assert "not offered" in rehearsal.findings[0].detail


class TestFinishFindings:
    def test_done_without_a_verifier_is_flagged(self) -> None:
        finding = home_rehearsal().findings[3]
        assert finding.ok is False
        assert "no verifier" in finding.detail

    def test_the_default_verifier_counts_as_no_verifier(self) -> None:
        finding = home_rehearsal(NoVerifier()).findings[3]
        assert finding.ok is False
        assert "no verifier" in finding.detail

    def test_a_registered_predicate_makes_done_confirmable(self) -> None:
        finding = home_rehearsal(PredicateVerifier({CLAIM: lambda t, v: True})).findings[3]
        assert finding.ok is True
        assert "confirmable" in finding.detail

    def test_a_claim_with_no_predicate_is_flagged_as_a_wording_mistake(self) -> None:
        finding = home_rehearsal(
            PredicateVerifier({"something else entirely": lambda t, v: True})
        ).findings[3]
        assert finding.ok is False
        assert "no predicate registered" in finding.detail

    def test_stopping_short_of_done_needs_no_verifier(self) -> None:
        sensor = demo.sensor()
        rehearsal = rehearse(
            TASK, script(Choice(finish=Status.STUCK, why="nothing fits")), sensor.view("home")
        )
        assert rehearsal.findings[0].ok is True

    def test_a_script_with_no_action_step_says_so(self) -> None:
        sensor = demo.sensor()
        rehearsal = rehearse(
            TASK, script(Choice(finish=Status.DONE, says=(CLAIM,))), sensor.view("home")
        )
        assert rehearsal.first is None
        assert "no action step" in rehearsal.verdict


class TestReport:
    def test_the_rehearsal_serialises(self) -> None:
        blob = home_rehearsal(demo.verifier()).brief()
        assert blob["app"] == "Fake App"
        assert len(blob["findings"]) == 4
        assert json.loads(json.dumps(blob))["findings"][1]["target"]["kind"] == "button"

    def test_a_finding_without_a_target_serialises(self) -> None:
        blob = home_rehearsal().brief()
        assert blob["findings"][0]["target"] is None

    def test_blocked_lists_every_finding_that_would_fail(self) -> None:
        rehearsal = home_rehearsal()
        assert len(rehearsal.blocked) == 3  # missing label, not-visible-yet, no verifier


def test_a_task_with_a_single_step_and_no_claims_uses_the_task_checks() -> None:
    task = Task(goal="do it", checks=("the thing happened",))
    sensor = demo.sensor()
    rehearsal = rehearse(
        task,
        script(Choice(finish=Status.DONE)),
        sensor.view("home"),
        verifier=PredicateVerifier({"the thing happened": lambda t, v: True}),
    )
    assert rehearsal.findings[0].ok is True


def test_targets_are_reported_with_their_actions_and_position() -> None:
    finding = home_rehearsal().findings[1]
    assert finding.target is not None
    assert finding.target.id == "effects_button"
    assert finding.target.actions == frozenset({Verb.PRESS})
    blob = finding.brief()
    assert blob["target"]["actions"] == ["PRESS"]
    assert blob["target"]["at"] == [40, 80]
