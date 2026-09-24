"""A task file can say what done looks like, so a scripted run can finish honestly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskhand.cli import main
from deskhand.json_io import load_task_file, task_file_from_dict
from deskhand.rehearse import rehearse
from deskhand.types import Box, CheckResult, Choice, Status, Target, Task, Verb, View
from deskhand.verify import Expectation, ExpectVerifier


def radio(target_id: str, label: str, *, selected: bool) -> Target:
    return Target(
        id=target_id,
        kind="radiobutton",
        label=label,
        actions=frozenset({Verb.PRESS}),
        box=Box(0, 0, 10, 10),
        selected=selected,
    )


def view(*targets: Target) -> View:
    return View(app="Settings", window="Appearance", revision="r", targets=targets)


TASK = Task(goal="dark mode", checks=("Appearance is Dark",))
DARK = Expectation(label="Dark", selected=True)


class TestAnExpectation:
    def test_holds_when_the_named_control_is_in_the_declared_state(self) -> None:
        ok, how = DARK.judge(
            view(radio("a", "Light", selected=False), radio("b", "Dark", selected=True))
        )
        assert ok
        assert "(b)" in how

    def test_says_what_it_found_when_it_does_not_hold(self) -> None:
        ok, how = DARK.judge(view(radio("b", "Dark", selected=False)))
        assert not ok
        assert "selected=False" in how

    def test_a_missing_control_is_not_a_pass(self) -> None:
        ok, how = DARK.judge(view(radio("a", "Light", selected=True)))
        assert not ok
        assert "no target named 'Dark'" in how

    def test_a_pixel_alias_counts_as_a_name(self) -> None:
        row = Target(id="r", kind="row", labels=("外观",), selected=True, box=Box(0, 0, 1, 1))
        assert Expectation(label="外观", selected=True).judge(view(row))[0]

    def test_absent_holds_only_when_nothing_is_named_that(self) -> None:
        gone = Expectation(label="Error", absent=True)
        assert gone.judge(view(radio("a", "Light", selected=True)))[0]
        assert not gone.judge(view(radio("e", "Error", selected=False)))[0]

    def test_kind_narrows_the_match(self) -> None:
        window = Target(id="w", kind="window:standard", label="Dark", selected=True)
        assert not Expectation(label="Dark", kind="radiobutton", selected=True).judge(view(window))[
            0
        ]


class TestTheVerifier:
    def test_a_declared_check_is_confirmed_against_the_view(self) -> None:
        verifier = ExpectVerifier({"Appearance is Dark": DARK})
        (result,) = verifier.confirm(
            task=TASK, view=view(radio("b", "Dark", selected=True)), claims=TASK.checks
        )
        assert result.ok

    def test_an_undeclared_check_is_refused_without_a_fallback(self) -> None:
        verifier = ExpectVerifier({})
        (result,) = verifier.confirm(task=TASK, view=view(), claims=TASK.checks)
        assert not result.ok
        assert not verifier.covers("Appearance is Dark")

    def test_an_undeclared_check_goes_to_the_fallback(self) -> None:
        class Yes:
            def confirm(self, *, task: Task, view: View, claims: object) -> tuple[CheckResult, ...]:
                return tuple(CheckResult(c, True, "fallback said so") for c in claims)  # type: ignore[attr-defined]

        verifier = ExpectVerifier({}, fallback=Yes())
        (result,) = verifier.confirm(task=TASK, view=view(), claims=TASK.checks)
        assert result.ok and result.how == "fallback said so"
        assert verifier.covers("anything")

    def test_a_rehearsal_calls_a_declared_check_confirmable(self) -> None:
        verifier = ExpectVerifier({"Appearance is Dark": DARK})
        done = Choice(finish=Status.DONE, says=("Appearance is Dark",))
        finding = rehearse(TASK, (done,), view(), verifier=verifier).findings[0]
        assert finding.ok


class TestTheTaskFile:
    def payload(self, **expect: object) -> dict[str, object]:
        return {"goal": "g", "checks": ["it is dark"], "expect": expect}

    def test_expect_is_read(self) -> None:
        loaded = task_file_from_dict(
            self.payload(**{"it is dark": {"label": "Dark", "selected": True}})
        )
        assert loaded.expect == {"it is dark": Expectation(label="Dark", selected=True)}

    def test_an_expectation_for_a_check_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(ValueError, match="does not have"):
            task_file_from_dict(self.payload(**{"it is light": {"label": "Light"}}))

    def test_unknown_fields_and_wrong_types_are_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown"):
            task_file_from_dict(self.payload(**{"it is dark": {"label": "Dark", "checked": True}}))
        with pytest.raises(ValueError, match="true or false"):
            task_file_from_dict(
                self.payload(**{"it is dark": {"label": "Dark", "selected": "yes"}})
            )
        with pytest.raises(ValueError, match="label"):
            task_file_from_dict(self.payload(**{"it is dark": {"selected": True}}))

    def test_the_examples_declare_what_done_means(self) -> None:
        examples = Path(__file__).resolve().parent.parent / "examples"
        for name in ("appearance.json", "appearance.zh-CN.json"):
            loaded = load_task_file(examples / name)
            assert set(loaded.expect) == set(loaded.task.checks), name


def test_a_task_file_with_a_bad_expectation_fails_before_anything_is_touched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps({"goal": "g", "checks": ["c"], "expect": {"x": {"label": "y"}}, "steps": []})
    )
    assert main(["run", "--task", str(path)]) != 0
    assert "does not have" in capsys.readouterr().err
