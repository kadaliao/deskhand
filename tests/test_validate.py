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


class TestAContainerIsNotTheControlItIsNamedAfter:
    """A window advertises AXPress -- it means "raise me" -- and it has a title.

    Measured on a `zh-Hans` macOS: the appearance task's first step, ``PRESS 外观``,
    resolved to the settings *window* because its title is exactly ``外观``, while the
    sidebar row the task meant carried that word only as a pixel-derived alias. `PRESS`
    on a window is not a press: the sensor degraded it to a coordinate click at the
    window's centre, which is a blind click into whatever happens to be there.
    """

    def test_a_window_titled_like_a_control_loses_to_the_control(self) -> None:
        window = target("w", "Appearance", Verb.PRESS, kind="window:standardwindow")
        row = target("r", "Appearance", Verb.PRESS, kind="row:outlinerow")
        choice = Choice(verb=Verb.PRESS, target_label="Appearance")
        assert resolve_target(choice, view(window, row), role="target") is row

    def test_a_pixel_named_alias_still_loses_to_a_real_label(self) -> None:
        """The row is the looser match and the window the exact one; the row wins."""
        window = target("w", "Appearance", Verb.PRESS, kind="window:standardwindow")
        row = Target(
            id="r",
            kind="row:outlinerow",
            label="",
            labels=("Appearance",),
            actions=frozenset({Verb.PRESS}),
            box=Box(0, 0, 10, 10),
        )
        choice = Choice(verb=Verb.PRESS, target_label="Appearance")
        assert resolve_target(choice, view(window, row), role="target") is row

    def test_a_window_is_still_a_target_when_nothing_else_matched_it(self) -> None:
        window = target("w", "Appearance", Verb.PRESS, kind="window:standardwindow")
        choice = Choice(verb=Verb.PRESS, target_label="Appearance")
        assert resolve_target(choice, view(window), role="target") is window

    def test_two_controls_with_the_same_name_are_still_refused(self) -> None:
        """The narrowing must not become guessing between equally good candidates."""
        one = target("one", "Dark", Verb.PRESS)
        two = target("two", "Dark", Verb.PRESS)
        choice = Choice(verb=Verb.PRESS, target_label="Dark")
        with pytest.raises(BadChoice, match="ambiguous"):
            resolve_target(choice, view(one, two), role="target")

    def test_a_group_loses_to_a_control_too(self) -> None:
        group = target("g", "Timeline", Verb.PRESS, kind="group:hostingview")
        button = target("b", "Timeline", Verb.PRESS)
        choice = Choice(verb=Verb.PRESS, target_label="Timeline")
        assert resolve_target(choice, view(group, button), role="target") is button


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
