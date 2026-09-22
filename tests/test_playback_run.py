"""M4's acceptance run, offline: a model decider and a model verifier, end to end.

``docs/PLAN.md`` asks for "an end-to-end run on M2's scenario with a model decider
and a model verifier, where the trace shows the decision was made from structured
state and the verification was independent of it".

M2's scenario needs a real Chromium application, Screen Recording and a real key.
This runs the same *shape* of task -- open a media app, search for a track, play
it -- against the scripted desktop in ``demo.py`` with a scripted model. That
makes the seam and its trace assertable on any machine, and it proves nothing
about how a real Chromium app behaves; that is still what M2 is for.

The scripted model is wrong on purpose for its first answer. Pressing whatever is
called "Play" is the most likely way to click the wrong thing in this scenario,
and the run is only interesting if the refusal comes back, the model reads it, and
the next answer is a target id that exists.
"""

from __future__ import annotations

import json

from deskhand import demo
from deskhand.deciders import LLMDecider
from deskhand.model import FakeModel
from deskhand.protocols import Verifier
from deskhand.runner import Runner
from deskhand.types import Report, Status
from deskhand.verify import ModelVerifier, NoVerifier

DECIDER = "scripted-decider"
VERIFIER = "scripted-verifier"


def reply(**payload: object) -> str:
    return json.dumps(payload)


DECIDER_REPLIES = [
    reply(verb="PRESS", target_label="Play", why="play the top result"),
    reply(verb="TYPE", target="search_box", value_from="query", why="search for it by name"),
    reply(verb="PRESS", target="row_midnight_city", why="open the matching track"),
    reply(finish="DONE", says=[demo.PLAYBACK_CHECK], why="the track is selected and named"),
]

VERIFIER_OK = reply(ok=True, how="the now-playing line names Midnight City")


class Run:
    """One scripted run: the report, and both sides of the conversation."""

    def __init__(
        self,
        *,
        decider_replies: list[str] | None = None,
        verifier_replies: list[str] | None = None,
        verifier: Verifier | None = None,
    ) -> None:
        self.decider_model = FakeModel(list(decider_replies or DECIDER_REPLIES), name=DECIDER)
        self.verifier_model = FakeModel(list(verifier_replies or [VERIFIER_OK]), name=VERIFIER)
        self.report: Report = Runner(
            sensor=demo.playback_sensor(),
            decider=LLMDecider(self.decider_model),
            verifier=ModelVerifier(self.verifier_model) if verifier is None else verifier,
        ).run(demo.playback_task())

    @property
    def target_ids_offered(self) -> set[str]:
        return {
            target["id"]
            for call in self.decider_model.calls
            for target in call.payload()["view"]["targets"]
        }

    @property
    def named_targets(self) -> list[str]:
        return [
            str(step.action.target)
            for step in self.report.steps
            if step.action is not None and step.action.target is not None
        ]


class TestTheAcceptanceRun:
    def test_it_reaches_done(self) -> None:
        assert Run().report.status is Status.DONE

    def test_the_verification_is_what_finished_it(self) -> None:
        """DONE is only reachable because a check came back confirmed."""
        report = Run().report
        assert [c.ok for c in report.checked] == [True]
        assert report.checked[0].check == demo.PLAYBACK_CHECK

    def test_the_verifier_was_asked_exactly_once_about_exactly_one_claim(self) -> None:
        run = Run()
        assert run.verifier_model.asked == 1
        assert json.loads(run.verifier_model.calls[0].prompt)["claim"] == demo.PLAYBACK_CHECK

    def test_the_wrong_first_answer_is_refused_and_explained(self) -> None:
        """An ambiguous label is the realistic mistake, and the ids are in the reason."""
        first = Run().report.steps[0]
        assert first.failed == "BadChoice"
        assert "ambiguous" in first.why
        assert "play_all" in first.why
        assert "row_play_button" in first.why
        assert first.action is None

    def test_the_model_is_told_what_went_wrong_before_it_answers_again(self) -> None:
        run = Run()
        second_question = run.decider_model.calls[1].payload()
        told = second_question["steps_so_far"]
        assert told and told[0]["failed"] == "BadChoice"
        assert "ambiguous" in told[0]["why"]

    def test_the_run_survived_the_bad_choice_rather_than_ending_on_it(self) -> None:
        report = Run().report
        assert report.steps_taken == 4
        assert [s.failed for s in report.steps] == ["BadChoice", None, None, None]

    def test_every_target_it_acted_on_was_one_it_had_been_offered(self) -> None:
        """The claim "the decision came from structured state", as an assertion.

        It could only name an id that appeared in the payload, and it did name one.
        """
        run = Run()
        assert run.named_targets == ["search_box", "row_midnight_city"]
        assert set(run.named_targets) <= run.target_ids_offered

    def test_the_model_was_asked_once_per_step(self) -> None:
        run = Run()
        assert run.decider_model.asked == run.report.steps_taken == 4

    def test_no_question_carries_a_position(self) -> None:
        """Every target in this scenario is semantic, so none of them has a place."""
        run = Run()
        for call in run.decider_model.calls:
            for target in call.payload()["view"]["targets"]:
                assert "at" not in target
                assert "score" not in target

    def test_the_verifier_never_sees_the_deciders_reasoning(self) -> None:
        run = Run()
        question = run.verifier_model.calls[0].text
        for rationale in (json.loads(item)["why"] for item in DECIDER_REPLIES):
            assert rationale not in question
        assert "steps_so_far" not in question

    def test_the_trace_shows_both_halves(self) -> None:
        report = Run().report
        assert report.steps[0].choice is not None
        assert report.steps[0].choice.why == "play the top result"
        assert VERIFIER in report.checked[0].how
        assert report.brief()["status"] == "DONE"


class TestAModelDeciderCannotConfirmItsOwnWork:
    def test_with_no_verifier_the_same_run_escalates(self) -> None:
        report = Run(verifier=NoVerifier()).report
        assert report.status is Status.ESCALATE
        assert "not confirmed" in report.why

    def test_a_model_cannot_pick_an_easier_claim_to_be_checked(self) -> None:
        """The claim a model writes is not the question the verifier is asked.

        Regression: with ``says`` choosing the claims, a model could finish with
        ``says: ["the window is open"]``, have *that* confirmed, and report DONE with
        "Midnight City is playing" never checked.
        """
        replies = [*DECIDER_REPLIES[:-1], reply(finish="DONE", says=["the window is open"])]
        run = Run(decider_replies=replies)
        assert [c.check for c in run.report.checked] == [demo.PLAYBACK_CHECK]
        assert json.loads(run.verifier_model.calls[0].prompt)["claim"] == demo.PLAYBACK_CHECK

    def test_a_false_claim_cannot_buy_a_done(self) -> None:
        replies = [
            *DECIDER_REPLIES[:-1],
            reply(finish="DONE", says=["the window is open"], why="easy"),
        ]
        run = Run(
            decider_replies=replies,
            verifier_replies=[reply(ok=False, how="nothing is playing yet")],
        )
        assert run.report.status is Status.ESCALATE
        assert [c.check for c in run.report.checked] == [demo.PLAYBACK_CHECK]

    def test_a_verifier_that_says_no_escalates_and_says_which_check(self) -> None:
        report = Run(verifier_replies=[reply(ok=False, how="nothing is playing yet")]).report
        assert report.status is Status.ESCALATE
        assert "Midnight City is playing" in report.why
        assert [c.ok for c in report.checked] == [False]
        assert "nothing is playing yet" in report.checked[0].how

    def test_a_verifier_that_cannot_be_asked_does_not_finish_the_run(self) -> None:
        report = Run(verifier_replies=["I am not answering"]).report
        assert report.status is Status.ESCALATE
        assert [c.ok for c in report.checked] == [False]
