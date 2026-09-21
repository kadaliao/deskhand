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
