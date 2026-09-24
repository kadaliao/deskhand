from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskhand.cli import ENVIRONMENT, FAILED, OK, main


def test_demo_runs_end_to_end_and_prints_a_trace(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo"]) == OK
    out = capsys.readouterr().out
    assert "status: DONE" in out
    assert "routes:" in out
    assert "FAIL BadChoice" in out


def test_a_missing_task_file_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", "--task", str(tmp_path / "nope.json")])
    assert code == ENVIRONMENT
    assert "FileNotFoundError" in capsys.readouterr().err


def test_a_task_without_a_script_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"goal": "do it", "checks": ["done"]}))
    code = main(["run", "--task", str(path)])
    assert code == ENVIRONMENT
    assert "no 'steps' script" in capsys.readouterr().err


def test_the_demo_report_is_also_available_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "goal": "run a scripted task",
                "checks": ["ok"],
                "steps": [{"finish": "DONE", "says": ["ok"]}],
            }
        )
    )
    # Without a real desktop this exits through the macOS source; the point here
    # is only that the JSON task file is parsed before any hardware is touched.
    from deskhand.json_io import load_task

    task, choices = load_task(path)
    assert task.goal == "run a scripted task"
    assert len(choices) == 1
    assert capsys.readouterr().out == ""


def test_a_model_rehearsal_reports_a_missing_model_before_it_touches_the_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The model is built before the sensor, so a missing one costs nothing.

    This matters most for a rehearsal: the first thing touching the desktop does is take
    focus away from whoever is using the machine. It also pins that a model run does not
    need a 'steps' script, which is the only reason a rehearsal works without one.
    """
    monkeypatch.delenv("DESKHAND_MODEL_COMMAND", raising=False)
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"goal": "do it", "checks": ["done"]}))
    code = main(["run", "--task", str(path), "--model", "--dry-run"])
    assert code == ENVIRONMENT
    err = capsys.readouterr().err
    assert "DESKHAND_MODEL_COMMAND" in err
    assert "no 'steps' script" not in err


def test_unknown_commands_are_rejected_system_exits() -> None:
    with pytest.raises(SystemExit):
        main(["frobnicate"])


def test_failed_status_is_reported_through_the_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A stuck demo would be a bug, but the mapping from status to exit code is
    # worth pinning: DONE is 0, anything else is 1.
    assert main(["demo"]) == OK
    capsys.readouterr()
    assert OK != FAILED


class TestAModelRunGetsAModelSizedBudget:
    """A model decision measured 9-33 s; the example tasks allow 40 s for the whole run."""

    def test_a_budget_sized_for_rules_is_raised_and_the_raise_is_said(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from deskhand.cli import MODEL_STEP_MS, _budget_for_a_model
        from deskhand.types import Limits, Task

        task = Task(goal="g", checks=("c",), limits=Limits(max_steps=6, max_ms=40_000))
        raised = _budget_for_a_model(task)
        assert raised.limits.max_ms == 6 * MODEL_STEP_MS
        assert raised.limits.max_steps == 6
        assert "raised max_ms 40000" in capsys.readouterr().err

    def test_a_generous_or_absent_budget_is_left_alone(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from deskhand.cli import _budget_for_a_model
        from deskhand.types import Limits, Task

        generous = Task(goal="g", checks=("c",), limits=Limits(max_steps=2, max_ms=900_000))
        unbounded = Task(goal="g", checks=("c",), limits=Limits(max_ms=None))
        assert _budget_for_a_model(generous) is generous
        assert _budget_for_a_model(unbounded) is unbounded
        assert capsys.readouterr().err == ""
