from __future__ import annotations

import pytest

from deskhand.errors import BadChoice
from deskhand.types import Box, Choice, Status, Target, Task, Verb, View
from deskhand.validate import build_action, resolve_target


def target(
    target_id: str,
    label: str,
    *verbs: Verb,
    kind: str = "button",
    box: Box | None = None,
    enabled: bool = True,
) -> Target:
    return Target(
        id=target_id,
        kind=kind,
        label=label,
        actions=frozenset(verbs),
        box=box if box is not None else Box(0, 0, 10, 10),
        enabled=enabled,
    )


PLAY = target("play", "Play", Verb.PRESS, Verb.OPEN)
FIELD = target("field", "Search Effects", Verb.TYPE, Verb.PRESS, kind="text_field")
DROP = target("drop", "Timeline", Verb.PRESS)

TASK = Task(goal="do it", checks=("done",), inputs={"query": "Gaussian Blur"})


def view(*targets: Target) -> View:
    return View(app="A", window="W", revision="r", targets=tuple(targets))


class TestResolve:
    def test_by_id(self) -> None:
        choice = Choice(verb=Verb.PRESS, target="play")
        assert resolve_target(choice, view(PLAY), role="target") is PLAY

    def test_by_label_is_case_and_punctuation_insensitive(self) -> None:
        choice = Choice(verb=Verb.PRESS, target_label="  play ")
        assert resolve_target(choice, view(PLAY), role="target") is PLAY

    def test_unknown_id_is_rejected(self) -> None:
        with pytest.raises(BadChoice, match="not in the current view"):
            resolve_target(Choice(verb=Verb.PRESS, target="ghost"), view(PLAY), role="target")

    def test_missing_label_is_rejected(self) -> None:
        with pytest.raises(BadChoice, match="no target matching"):
            resolve_target(
                Choice(verb=Verb.PRESS, target_label="Export"), view(PLAY), role="target"
            )

    def test_ambiguous_label_is_rejected_rather_than_guessed(self) -> None:
        twin = target("play2", "Play", Verb.PRESS)
        with pytest.raises(BadChoice, match="ambiguous"):
            resolve_target(
                Choice(verb=Verb.PRESS, target_label="Play"), view(PLAY, twin), role="target"
            )

    def test_disabled_targets_are_not_candidates(self) -> None:
        off = target("off", "Play", Verb.PRESS, enabled=False)
        with pytest.raises(BadChoice, match="no target matching"):
            resolve_target(Choice(verb=Verb.PRESS, target_label="Play"), view(off), role="target")


class TestBuildAction:
    def test_freezes_the_choice_with_a_guard(self) -> None:
        action = build_action(Choice(verb=Verb.OPEN, target="play"), view(PLAY), TASK)
        assert action.verb is Verb.OPEN
        assert action.target == "play"
        assert action.guard == PLAY.fingerprint()
        assert action.visual is False

    def test_a_verb_the_target_does_not_offer_is_rejected(self) -> None:
        with pytest.raises(BadChoice, match="not offered"):
            build_action(Choice(verb=Verb.TYPE, target="play"), view(PLAY), TASK)

    def test_type_needs_an_input_key_that_the_caller_supplied(self) -> None:
        with pytest.raises(BadChoice, match="value_from"):
            build_action(Choice(verb=Verb.TYPE, target="field"), view(FIELD), TASK)
        with pytest.raises(BadChoice, match="no 'password'"):
            build_action(
                Choice(verb=Verb.TYPE, target="field", value_from="password"), view(FIELD), TASK
            )

    def test_type_resolves_the_literal_from_the_task(self) -> None:
        action = build_action(
            Choice(verb=Verb.TYPE, target="field", value_from="query"), view(FIELD), TASK
        )
        assert action.value == "Gaussian Blur"

    def test_a_disabled_target_is_rejected(self) -> None:
        off = target("play", "Play", Verb.PRESS, enabled=False)
        with pytest.raises(BadChoice, match="disabled"):
            build_action(Choice(verb=Verb.PRESS, target="play"), view(off), TASK)

    def test_drag_needs_a_destination_that_is_a_different_target(self) -> None:
        draggable = target("clip", "A.mov", Verb.PRESS, Verb.DRAG, kind="clip")
        with pytest.raises(BadChoice, match="destination"):
            build_action(Choice(verb=Verb.DRAG, target="clip"), view(draggable), TASK)
        with pytest.raises(BadChoice, match="same target"):
            build_action(Choice(verb=Verb.DRAG, target="clip", onto="clip"), view(draggable), TASK)
        action = build_action(
            Choice(verb=Verb.DRAG, target="clip", onto="drop"), view(draggable, DROP), TASK
        )
        assert action.onto == "drop"
        assert action.onto_guard == DROP.fingerprint()

    def test_key_and_scroll_arguments_are_checked(self) -> None:
        with pytest.raises(BadChoice, match="KEY requires"):
            build_action(Choice(verb=Verb.KEY), view(PLAY), TASK)
        with pytest.raises(BadChoice, match="SCROLL direction"):
            build_action(Choice(verb=Verb.SCROLL, scroll="SIDEWAYS"), view(PLAY), TASK)
        assert (
            build_action(Choice(verb=Verb.SCROLL, scroll="DOWN"), view(PLAY), TASK).scroll == "DOWN"
        )

    def test_chords_are_checked(self) -> None:
        with pytest.raises(BadChoice, match="needs a modifier"):
            build_action(Choice(verb=Verb.CHORD, chord="A"), view(PLAY), TASK)
        with pytest.raises(BadChoice, match="unknown modifier"):
            build_action(Choice(verb=Verb.CHORD, chord="HYPER+A"), view(PLAY), TASK)
        action = build_action(Choice(verb=Verb.CHORD, chord="CMD+SHIFT+Z"), view(PLAY), TASK)
        assert action.chord == "CMD+SHIFT+Z"

    def test_a_finish_choice_is_not_executable(self) -> None:
        with pytest.raises(BadChoice, match="cannot be executed"):
            build_action(Choice(finish=Status.DONE), view(PLAY), TASK)
