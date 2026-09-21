from __future__ import annotations

import pytest

from deskhand.types import (
    Box,
    Choice,
    Limits,
    Status,
    Target,
    Task,
    Verb,
    View,
    content_digest,
    structure_digest,
)


def button(target_id: str, label: str, value: str | None = None) -> Target:
    return Target(
        id=target_id,
        kind="button",
        label=label,
        value=value,
        actions=frozenset({Verb.PRESS}),
        box=Box(10, 20, 40, 20),
    )


class TestBox:
    def test_center_area_and_containment(self) -> None:
        box = Box(10, 20, 40, 20)
        assert box.center == (30, 30)
        assert box.area == 800
        assert box.holds(30, 30)
        assert not box.holds(9, 30)

    def test_overlap_is_intersection_over_union(self) -> None:
        assert Box(0, 0, 10, 10).overlap(Box(0, 0, 10, 10)) == 1.0
        assert Box(0, 0, 10, 10).overlap(Box(100, 0, 10, 10)) == 0.0

    def test_touching_boxes_do_not_overlap(self) -> None:
        assert Box(0, 0, 10, 10).overlap(Box(10, 0, 10, 10)) == 0.0


class TestDigests:
    def test_moving_a_control_changes_content_but_not_structure(self) -> None:
        before = (button("a", "Play"),)
        moved = Target(
            id="a",
            kind="button",
            label="Play",
            actions=frozenset({Verb.PRESS}),
            box=Box(300, 400, 40, 20),
        )
        assert structure_digest(before) == structure_digest((moved,))
        assert content_digest(before) != content_digest((moved,))

    def test_relabelling_changes_both(self) -> None:
        before = (button("a", "Play"),)
        after = (button("a", "Pause"),)
        assert structure_digest(before) != structure_digest(after)
        assert content_digest(before) != content_digest(after)

    def test_value_change_in_place_changes_both(self) -> None:
        # The regression this exists for: text changing in a control whose layout
        # never moves used to look like nothing happened.
        assert structure_digest((button("a", "Enabled", "on"),)) != structure_digest(
            (button("a", "Enabled", "off"),)
        )
        assert content_digest((button("a", "Enabled", "on"),)) != content_digest(
            (button("a", "Enabled", "off"),)
        )

    def test_order_does_not_matter(self) -> None:
        assert structure_digest((button("a", "A"), button("b", "B"))) == structure_digest(
            (button("b", "B"), button("a", "A"))
        )

    def test_sub_pixel_jitter_changes_nothing(self) -> None:
        shifted = Target(id="a", kind="button", label="Play", box=Box(10.4, 20.9, 40, 20))
        assert structure_digest((button("a", "Play"),)) == structure_digest((shifted,))
        assert content_digest((button("a", "Play"),)) == content_digest((shifted,))

    def test_focus_alone_is_not_a_structural_change(self) -> None:
        focused = Target(id="a", kind="button", label="Play", focused=True, box=Box(10, 20, 40, 20))
        assert structure_digest((button("a", "Play"),)) == structure_digest((focused,))
        assert content_digest((button("a", "Play"),)) != content_digest((focused,))


class TestTarget:
    def test_spoken_merges_the_extra_names(self) -> None:
        target = Target(
            id="a", kind="text_field", label="Search", labels=("Search Effects", "Search")
        )
        assert target.spoken() == "Search Search Effects"

    def test_anywhere_verbs_are_always_available(self) -> None:
        target = button("a", "Play")
        assert target.can(Verb.SCROLL)
        assert target.can(Verb.PRESS)
        assert not target.can(Verb.TYPE)

    def test_semantic_brief_hides_coordinates(self) -> None:
        brief = button("a", "Play").brief()
        assert "at" not in brief
        assert brief["actions"] == ["PRESS"]

    def test_visual_brief_reports_relative_position_inside_the_frame(self) -> None:
        target = Target(
            id="p",
            kind="text",
            label="Play",
            visual=True,
            box=Box(110, 220, 20, 20),
            score=0.9,
        )
        brief = target.brief(frame=Box(100, 200, 100, 100))
        assert brief["visual"] is True
        assert brief["at"] == [0.2, 0.3]


class TestView:
    def test_index_lookup_and_missing_key(self) -> None:
        view = View(app="A", window="W", revision="1", targets=(button("a", "Play"),))
        assert view.target("a").label == "Play"
        with pytest.raises(KeyError):
            view.target("nope")

    def test_find_filters_by_kind_label_and_visibility(self) -> None:
        disabled = Target(id="b", kind="button", label="Play", enabled=False)
        view = View(app="A", window="W", revision="1", targets=(button("a", "Play"), disabled))
        assert [t.id for t in view.find(label="play")] == ["a"]
        assert view.find(kind="button") == (view.target("a"),)
        assert view.find(label="Play", visual=True) == ()

    def test_brief_only_lists_enabled_targets(self) -> None:
        view = View(
            app="A",
            window="W",
            revision="1",
            targets=(
                button("a", "Play"),
                Target(id="b", kind="button", label="Off", enabled=False),
            ),
        )
        assert [t["label"] for t in view.brief()["targets"]] == ["Play"]

    def test_content_defaults_to_a_digest_of_the_targets(self) -> None:
        view = View(app="A", window="W", revision="1", targets=(button("a", "Play"),))
        assert view.content == content_digest(view.targets)


class TestTaskAndLimits:
    def test_task_requires_a_goal_and_a_check(self) -> None:
        with pytest.raises(ValueError, match="goal"):
            Task(goal="  ", checks=("x",))
        with pytest.raises(ValueError, match="checks"):
            Task(goal="do it", checks=())

    def test_limits_reject_a_zero_step_budget(self) -> None:
        with pytest.raises(ValueError, match="max_steps"):
            Limits(max_steps=0)

    def test_notes_are_labelled_as_advisory(self) -> None:
        task = Task(goal="do it", checks=("done",), notes=("do not touch other clips",))
        assert task.brief()["notes"] == ["do not touch other clips"]


class TestChoice:
    def test_exactly_one_of_verb_or_finish(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            Choice(verb=Verb.PRESS, finish=Status.DONE)
        with pytest.raises(ValueError, match="exactly one"):
            Choice()
        assert Choice(finish=Status.STUCK).finish is Status.STUCK

    def test_a_targeted_verb_needs_a_target(self) -> None:
        with pytest.raises(ValueError, match="needs a target"):
            Choice(verb=Verb.PRESS)
        assert Choice(verb=Verb.SCROLL, scroll="DOWN").verb is Verb.SCROLL
