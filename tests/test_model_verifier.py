"""Confirming a claim with a model, and the ways that must not confirm anything.

A verifier is the last line between "the agent says it is done" and "the run
reports DONE", so every failure mode here has to fall towards *unconfirmed*. Each
of those is a test, and so is the independence claim: the question must not carry
the decider's reasoning.
"""

from __future__ import annotations

import json

import pytest

from deskhand.errors import ModelFailed
from deskhand.model import FakeModel
from deskhand.protocols import Verifier
from deskhand.types import Box, Target, Task, Verb, View, structure_digest
from deskhand.verify import CHECK_SYSTEM, ModelVerifier, check_prompt

CHECK = "Go was pressed"


def a_target(**over: object) -> Target:
    base: dict[str, object] = {
        "id": "go_button",
        "kind": "button",
        "label": "Go",
        "actions": frozenset({Verb.PRESS}),
        "box": Box(10, 20, 30, 40),
        "source": "test",
    }
    base.update(over)
    return Target(**base)  # type: ignore[arg-type]


def a_view(*targets: Target) -> View:
    return View(
        app="Demo",
        window="Demo",
        revision=structure_digest(targets),
        targets=targets,
        frame=Box(0, 0, 800, 600),
    )


def a_task() -> Task:
    return Task(goal="Press Go", checks=(CHECK,), inputs={}, notes=("Do not touch anything else.",))


class TestTheQuestion:
    def test_the_payload_is_the_claim_the_task_and_the_view(self) -> None:
        task, view = a_task(), a_view(a_target())
        system, prompt = check_prompt(task, view, CHECK)
        payload = json.loads(prompt)
        assert payload["claim"] == CHECK
        assert payload["task"] == task.brief()
        assert payload["view"] == view.brief()
        assert system == CHECK_SYSTEM

    def test_the_question_demands_a_boolean(self) -> None:
        assert "boolean" in CHECK_SYSTEM
        assert "did not perform the task" in CHECK_SYSTEM

    def test_no_decider_reasoning_reaches_the_question(self) -> None:
        """Independence, as a property of the prompt rather than a promise.

        The runner hands the verifier a task, a view and claims. A decider's
        rationale must not be reachable from any of them.
        """
        system, prompt = check_prompt(a_task(), a_view(a_target()), CHECK)
        assert "because I considered the alternatives" not in f"{system}{prompt}"
        assert "steps_so_far" not in prompt


class TestConfirming:
    def test_a_boolean_true_confirms_and_carries_the_reason(self) -> None:
        model = FakeModel([{"ok": True, "how": "the dialog is gone"}])
        results = ModelVerifier(model).confirm(task=a_task(), view=a_view(), claims=(CHECK,))
        assert len(results) == 1
        assert results[0].ok is True
        assert "the dialog is gone" in results[0].how
        assert model.name in results[0].how

    def test_a_boolean_false_does_not_confirm(self) -> None:
        model = FakeModel([{"ok": False, "how": "the button is still enabled"}])
        results = ModelVerifier(model).confirm(task=a_task(), view=a_view(), claims=(CHECK,))
        assert results[0].ok is False
        assert "still enabled" in results[0].how

    def test_one_question_per_claim(self) -> None:
        """So a set of claims cannot be confirmed by a single answer."""
        model = FakeModel([{"ok": True}, {"ok": False}])
        results = ModelVerifier(model).confirm(
            task=a_task(), view=a_view(), claims=("first", "second")
        )
        assert [r.ok for r in results] == [True, False]
        assert model.asked == 2
        assert json.loads(model.calls[0].prompt)["claim"] == "first"
        assert json.loads(model.calls[1].prompt)["claim"] == "second"

    def test_no_claims_means_no_questions(self) -> None:
        model = FakeModel([])
        assert ModelVerifier(model).confirm(task=a_task(), view=a_view(), claims=()) == ()
        assert model.asked == 0

    def test_it_is_a_verifier(self) -> None:
        assert isinstance(ModelVerifier(FakeModel([])), Verifier)

    def test_it_says_it_can_cover_any_wording(self) -> None:
        """Unlike a predicate verifier, a model is not limited to registered keys,
        so a rehearsal must not warn that a claim has no predicate."""
        assert ModelVerifier(FakeModel([])).covers("a claim nobody registered") is True


class TestNeverConfirmingByAccident:
    @pytest.mark.parametrize(
        "reply",
        [
            '{"ok": "true"}',
            '{"ok": 1}',
            '{"ok": null}',
            '{"how": "looks right to me"}',
            '{"ok": "yes", "how": "it seems fine"}',
            "{}",
        ],
    )
    def test_anything_other_than_a_boolean_is_not_a_confirmation(self, reply: str) -> None:
        results = ModelVerifier(FakeModel([reply])).confirm(
            task=a_task(), view=a_view(), claims=(CHECK,)
        )
        assert results[0].ok is False
        assert "boolean" in results[0].how

    def test_a_transport_failure_is_not_a_confirmation(self) -> None:
        model = FakeModel([ModelFailed("the model is down")], name="test-model")
        results = ModelVerifier(model).confirm(task=a_task(), view=a_view(), claims=(CHECK,))
        assert results[0].ok is False
        assert "could not ask test-model" in results[0].how

    def test_a_reply_that_is_not_json_is_not_a_confirmation(self) -> None:
        results = ModelVerifier(FakeModel(["I suppose so?"])).confirm(
            task=a_task(), view=a_view(), claims=(CHECK,)
        )
        assert results[0].ok is False

    def test_a_missing_reason_is_reported_rather_than_invented(self) -> None:
        results = ModelVerifier(FakeModel([{"ok": True}])).confirm(
            task=a_task(), view=a_view(), claims=(CHECK,)
        )
        assert results[0].ok is True
        assert "no reason given" in results[0].how

    def test_a_run_out_of_script_does_not_confirm(self) -> None:
        model = FakeModel([{"ok": True}])
        results = ModelVerifier(model).confirm(task=a_task(), view=a_view(), claims=("a", "b"))
        assert results[0].ok is True
        assert results[1].ok is False
