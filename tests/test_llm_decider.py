"""What the model is told, and what it is allowed to ask for.

The prompt is asserted as data (``json.loads`` of the payload) rather than as a
string, so a change to what a model sees has to be a change to a structure these
tests can see. The refusals are the point: an agent that can be talked into a
coordinate click has no safety story, so three shapes of "click there" are pinned
to a refusal.
"""

from __future__ import annotations

import json

import pytest

from deskhand.deciders.llm import SYSTEM, LLMDecider, choice_from_reply, prompt_for
from deskhand.errors import BadChoice, ModelFailed
from deskhand.model import FakeModel
from deskhand.protocols import Decider
from deskhand.types import Box, Step, Target, Task, Times, Verb, View, structure_digest
from deskhand.validate import build_action

# Deliberately unlikely numbers, and ids without digits in them, so "is this
# coordinate in the prompt?" is a question with an unambiguous answer.
FAR_X, FAR_Y = 4123.0, 2897.0


def a_target(**over: object) -> Target:
    base: dict[str, object] = {
        "id": "go_button",
        "kind": "button",
        "label": "Go",
        "actions": frozenset({Verb.PRESS}),
        "box": Box(FAR_X, FAR_Y, 10, 10),
        "source": "test",
    }
    base.update(over)
    return Target(**base)  # type: ignore[arg-type]


def a_view(*targets: Target, frame: Box | None = None) -> View:
    return View(
        app="Demo",
        window="Demo",
        revision=structure_digest(targets),
        targets=targets,
        frame=frame if frame is not None else Box(0, 0, 800, 600),
    )


def a_task(**over: object) -> Task:
    base: dict[str, object] = {
        "goal": "Press Go",
        "checks": ("Go was pressed",),
        "inputs": {"query": "something"},
    }
    base.update(over)
    return Task(**base)  # type: ignore[arg-type]


def a_step(n: int = 1, **over: object) -> Step:
    base: dict[str, object] = {"n": n, "before": "a", "after": "b", "times": Times()}
    base.update(over)
    return Step(**base)  # type: ignore[arg-type]


class TestThePrompt:
    def test_the_payload_is_the_briefs_and_nothing_else(self) -> None:
        task, view = a_task(), a_view(a_target())
        _, prompt = prompt_for(task, view, ())
        payload = json.loads(prompt)
        assert payload["task"] == task.brief()
        assert payload["view"] == view.brief()
        assert payload["steps_so_far"] == []

    def test_the_system_text_is_stable_regardless_of_the_view(self) -> None:
        """A stable instruction is the cacheable half; only the payload varies."""
        first, _ = prompt_for(a_task(), a_view(a_target()), ())
        second, _ = prompt_for(a_task(), a_view(), ())
        assert first == second == SYSTEM

    def test_the_system_text_states_the_rules_that_structure_enforces(self) -> None:
        for rule in ('must appear in that target\'s "actions"', "value_from", "coordinate"):
            assert rule in SYSTEM

    def test_a_semantic_target_carries_no_position_at_all(self) -> None:
        view = a_view(a_target())
        _, prompt = prompt_for(a_task(), view, ())
        entry = json.loads(prompt)["view"]["targets"][0]
        assert "at" not in entry
        assert "score" not in entry

    def test_an_absolute_coordinate_is_nowhere_in_the_prompt(self) -> None:
        view = a_view(a_target(), a_target(id="other", label="Other"))
        _, prompt = prompt_for(a_task(), view, ())
        assert str(int(FAR_X)) not in prompt
        assert str(int(FAR_Y)) not in prompt

    def test_a_visual_target_carries_a_relative_position_only(self) -> None:
        """The deliberate exception: a pixel target needs to be aimable, so its
        position is sent as a fraction of the window, never as a screen point."""
        visual = a_target(id="blob", label="", visual=True, box=Box(400, 300, 20, 20))
        view = a_view(visual, frame=Box(0, 0, 800, 600))
        _, prompt = prompt_for(a_task(), view, ())
        entry = json.loads(prompt)["view"]["targets"][0]
        assert entry["visual"] is True
        assert 0.0 <= entry["at"][0] <= 1.0
        assert 0.0 <= entry["at"][1] <= 1.0
        assert str(int(FAR_X)) not in prompt

    def test_history_bounds_the_transcript(self) -> None:
        steps = tuple(a_step(n=n, why=f"step-{n}") for n in range(1, 6))
        _, prompt = prompt_for(a_task(), a_view(a_target()), steps, history=2)
        kept = json.loads(prompt)["steps_so_far"]
        assert len(kept) == 2
        assert [s["n"] for s in kept] == [4, 5]

    def test_zero_history_sends_none_of_it(self) -> None:
        steps = (a_step(why="step-1"),)
        _, prompt = prompt_for(a_task(), a_view(a_target()), steps, history=0)
        assert json.loads(prompt)["steps_so_far"] == []

    def test_a_failed_step_is_visible_to_the_next_decision(self) -> None:
        """The feedback path: the reason a bad choice does not simply repeat."""
        failed = a_step(failed="BadChoice", why="target 'Play' is ambiguous (2)")
        _, prompt = prompt_for(a_task(), a_view(a_target()), (failed,))
        entry = json.loads(prompt)["steps_so_far"][0]
        assert entry["failed"] == "BadChoice"
        assert "ambiguous" in entry["why"]


class TestChoosing:
    def test_a_reply_becomes_a_choice(self) -> None:
        model = FakeModel(['{"verb": "PRESS", "target": "go_button", "why": "it is the button"}'])
        choice = LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())
        assert choice.verb is Verb.PRESS
        assert choice.target == "go_button"
        assert choice.why == "it is the button"

    def test_a_finish_reply_becomes_a_claim(self) -> None:
        model = FakeModel(['{"finish": "DONE", "says": ["Go was pressed"]}'])
        choice = LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())
        assert choice.finish is not None
        assert choice.says == ("Go was pressed",)

    def test_the_reply_may_be_fenced_or_wrapped_in_prose(self) -> None:
        model = FakeModel(['Sure!\n```json\n{"verb": "WAIT"}\n```'])
        assert LLMDecider(model).choose(task=a_task(), view=a_view(), steps=()).verb is Verb.WAIT

    def test_the_decider_is_a_decider(self) -> None:
        assert isinstance(LLMDecider(FakeModel([])), Decider)

    def test_a_transport_failure_is_raised_for_the_loop_to_survive(self) -> None:
        model = FakeModel([ModelFailed("the model is down")])
        with pytest.raises(ModelFailed, match="the model is down"):
            LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())

    def test_a_reply_that_is_not_json_names_the_model_at_parse_time(self) -> None:
        model = FakeModel(["I decline"], name="test-model")
        with pytest.raises(ModelFailed, match="test-model reply 1"):
            LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())

    def test_a_reply_that_is_not_a_choice_names_the_model_and_the_step(self) -> None:
        model = FakeModel([{"nonsense": 1}], name="test-model")
        with pytest.raises(ModelFailed, match="test-model at step 1"):
            LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())


class TestTheModelCannotAskForACoordinateClick:
    """Three shapes of the same attempt, and none of them reach a click."""

    def test_a_target_given_as_a_pair_is_refused(self) -> None:
        model = FakeModel([{"verb": "PRESS", "target": [FAR_X, FAR_Y]}])
        with pytest.raises(ModelFailed, match="'target' must be a string, got list"):
            LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())

    def test_a_target_given_as_a_keyed_point_is_refused(self) -> None:
        model = FakeModel([{"verb": "PRESS", "target": {"x": FAR_X, "y": FAR_Y}}])
        with pytest.raises(ModelFailed, match="'target' must be a string, got dict"):
            LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())

    def test_a_target_given_as_a_spelt_point_survives_parsing_but_not_validation(self) -> None:
        """A string cannot be a coordinate: it is only ever looked up as an id."""
        model = FakeModel([f'{{"verb": "PRESS", "target": "{int(FAR_X)},{int(FAR_Y)}"}}'])
        choice = LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())
        with pytest.raises(BadChoice, match="not in the current view"):
            build_action(choice, a_view(a_target()), a_task())

    def test_a_point_smuggled_into_an_unknown_field_is_refused(self) -> None:
        model = FakeModel([{"verb": "PRESS", "target": "go_button", "click_at": [FAR_X, FAR_Y]}])
        with pytest.raises(ModelFailed, match="unknown choice field"):
            LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())


class TestTheModelCannotInventALiteral:
    def test_a_literal_value_is_refused(self) -> None:
        model = FakeModel([{"verb": "TYPE", "target": "go_button", "value": "hello"}])
        with pytest.raises(ModelFailed, match="not a usable choice"):
            LLMDecider(model).choose(task=a_task(), view=a_view(a_target()), steps=())

    def test_a_value_from_key_that_the_caller_did_not_supply_is_refused(self) -> None:
        field = a_target(kind="text_field", label="Search", actions=frozenset({Verb.TYPE}))
        model = FakeModel([{"verb": "TYPE", "target": "go_button", "value_from": "password"}])
        choice = LLMDecider(model).choose(task=a_task(), view=a_view(field), steps=())
        with pytest.raises(BadChoice, match=r"Task\.inputs has no 'password'"):
            build_action(choice, a_view(field), a_task())


class TestMalformedReplies:
    @pytest.mark.parametrize(
        "reply",
        [
            "I am not sure what to do.",
            '{"verb": "TELEPORT", "target": "go_button"}',
            '{"target": "go_button"}',
            '{"verb": "PRESS", "finish": "DONE"}',
            "{}",
        ],
    )
    def test_a_reply_that_is_not_a_usable_choice_is_a_model_failure(self, reply: str) -> None:
        with pytest.raises(ModelFailed):
            LLMDecider(FakeModel([reply])).choose(task=a_task(), view=a_view(a_target()), steps=())

    def test_choice_from_reply_names_where_it_failed(self) -> None:
        with pytest.raises(ModelFailed, match="verifier attempt 4"):
            choice_from_reply({"nonsense": 1}, where="verifier attempt 4")
