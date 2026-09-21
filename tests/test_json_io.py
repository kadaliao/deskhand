from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskhand.json_io import (
    choice_from_dict,
    choices_from_payload,
    dump,
    load_task,
    report_to_dict,
    task_from_dict,
    view_to_dict,
)
from deskhand.runner import Runner
from deskhand.types import Box, Choice, Status, Target, Verb
from deskhand.verify import PredicateVerifier


def payload() -> dict[str, object]:
    return {
        "goal": "Press Alpha",
        "checks": ["Alpha was pressed"],
        "inputs": {"text": "hello"},
        "notes": ["do not press Beta"],
        "limits": {"max_steps": 4, "max_ms": 5000},
        "steps": [
            {"verb": "PRESS", "target_label": "Alpha"},
            {"finish": "DONE", "says": ["Alpha was pressed"]},
        ],
    }


class TestTaskFromDict:
    def test_parses_everything(self) -> None:
        task = task_from_dict(payload())
        assert task.goal == "Press Alpha"
        assert task.checks == ("Alpha was pressed",)
        assert task.inputs == {"text": "hello"}
        assert task.notes == ("do not press Beta",)
        assert task.limits.max_steps == 4
        assert task.limits.max_ms == 5000

    def test_unknown_fields_are_rejected_not_ignored(self) -> None:
        bad = payload() | {"verification": ["typo"]}
        with pytest.raises(ValueError, match="unknown task field"):
            task_from_dict(bad)

    def test_unknown_limit_fields_are_rejected(self) -> None:
        bad = dict(payload())
        bad["limits"] = {"max_steps": 2, "max_seconds": 3}
        with pytest.raises(ValueError, match="unknown limits field"):
            task_from_dict(bad)

    def test_missing_goal_is_an_error(self) -> None:
        with pytest.raises(KeyError):
            task_from_dict({"checks": ["x"]})


class TestChoices:
    def test_a_script_is_parsed_into_choices(self) -> None:
        choices = choices_from_payload(payload())
        assert choices[0].verb is Verb.PRESS
        assert choices[0].target_label == "Alpha"
        assert choices[1].finish is Status.DONE

    def test_choice_fields_are_strict(self) -> None:
        with pytest.raises(ValueError, match="unknown choice field"):
            choice_from_dict({"verb": "PRESS", "element": "a"})

    def test_a_bad_verb_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            choice_from_dict({"verb": "SMASH"})


class TestFiles:
    def test_load_task_reads_the_file_and_its_script(self, tmp_path: Path) -> None:
        path = tmp_path / "task.json"
        path.write_text(json.dumps(payload()))
        task, choices = load_task(path)
        assert task.goal == "Press Alpha"
        assert len(choices) == 2

    def test_a_script_without_steps_has_no_choices(self, tmp_path: Path) -> None:
        path = tmp_path / "task.json"
        path.write_text(json.dumps({k: v for k, v in payload().items() if k != "steps"}))
        task, choices = load_task(path)
        assert task.goal == "Press Alpha"
        assert choices == ()


class TestReport:
    def test_report_and_view_serialise(self) -> None:
        from deskhand.deciders.scripted import ScriptedDecider
        from deskhand.sensors.fake import FakeSensor

        target = Target(
            id="a",
            kind="button",
            label="Alpha",
            actions=frozenset({Verb.PRESS}),
            box=Box(0, 0, 10, 10),
        )
        sensor = FakeSensor({"s": (target,)}, start="s")
        task = task_from_dict({k: v for k, v in payload().items() if k != "steps"})
        runner = Runner(
            sensor=sensor,
            decider=ScriptedDecider([Choice(finish=Status.DONE, says=("Alpha was pressed",))]),
            verifier=PredicateVerifier({"Alpha was pressed": lambda t, v: True}),
        )
        report = runner.run(task)
        blob = report_to_dict(report)
        assert blob["status"] == "DONE"
        assert blob["steps_taken"] == 1
        assert blob["checks"][0]["ok"] is True
        assert view_to_dict(report.view)["app"] == "Fake App"

    def test_dump_is_readable(self) -> None:
        assert "\n" in dump({"a": 1})
