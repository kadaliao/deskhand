"""The HTML page made from a run: what it must say, and what it must never do."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from deskhand import demo
from deskhand.cli import OK, main
from deskhand.report_html import render
from deskhand.runner import Runner


def demo_brief() -> dict[str, object]:
    runner = Runner(sensor=demo.sensor(), decider=demo.script(), verifier=demo.verifier())
    return runner.run(demo.task()).brief()


def step(n: int, **fields: object) -> dict[str, object]:
    base: dict[str, object] = {"n": n, "ms": {"look": 1, "decide": 2, "act": 3, "settle": 4}}
    base.update(fields)
    return base


class TestWhatThePageSays:
    def test_the_goal_status_checks_and_every_step_are_on_it(self) -> None:
        page = render(demo_brief())
        assert "Turn on the Gaussian Blur effect for the selected clip" in page
        assert 'class="pill ok">DONE<' in page
        assert "Gaussian Blur is enabled on the clip" in page
        assert page.count('<li class="step ') == 5

    def test_routes_are_counted_by_how_they_acted(self) -> None:
        page = render(
            {
                "status": "DONE",
                "steps": [
                    step(1, choice={"verb": "PRESS"}, via="ax-press", progress=True),
                    step(2, choice={"verb": "PRESS"}, via="click", progress=True),
                    step(3, choice={"verb": "KEY", "key": "DOWN"}, via="key", progress=True),
                ],
            }
        )
        assert '<div class="tile-value">1/3</div>' in page
        coordinate = re.search(r'Coordinate clicks</div><div class="tile-value">(\d+)<', page)
        assert coordinate is not None and coordinate.group(1) == "1"

    def test_a_refused_done_is_shown_as_a_claim_not_a_success(self) -> None:
        """The verifier refused it; the step must not be painted as if it had passed."""
        page = render(
            {"status": "ESCALATE", "steps": [step(1, choice={"finish": "DONE"})]},
        )
        assert 'class="step is-claim"' in page
        assert 'class="step is-ok"' not in page

    def test_a_step_chosen_by_id_names_the_id(self) -> None:
        page = render(
            {
                "status": "STUCK",
                "steps": [step(1, choice={"verb": "PRESS", "target": "win-01"}, failed="CannotDo")],
            }
        )
        assert '<span class="label">win-01</span>' in page

    def test_values_read_as_json_not_as_python(self) -> None:
        page = render(
            {
                "status": "DONE",
                "steps": [],
                "view": {
                    "app": "A",
                    "window": "w",
                    "targets": [{"id": "r", "kind": "radiobutton", "value": True}],
                },
            }
        )
        assert "value=true" in page
        assert "value=True" not in page

    def test_unverified_checks_are_listed_as_not_checked(self) -> None:
        page = render(
            {"status": "STUCK", "steps": [], "task": {"goal": "g", "checks": ["it is dark"]}}
        )
        assert "it is dark" in page
        assert "not checked" in page


class TestWhatThePageMustNeverDo:
    def test_text_from_the_desktop_or_a_model_is_escaped(self) -> None:
        hostile = "<script>alert(1)</script>"
        page = render(
            {
                "status": "DONE",
                "why": hostile,
                "task": {"goal": hostile, "checks": [hostile]},
                "steps": [step(1, choice={"verb": "PRESS", "why": hostile}, target_label=hostile)],
                "view": {
                    "app": hostile,
                    "window": hostile,
                    "targets": [{"id": hostile, "kind": hostile, "label": hostile}],
                },
            }
        )
        assert hostile not in page
        # One script element: the page's own filter.
        assert page.count("<script>") == 1

    def test_it_loads_nothing_from_anywhere(self) -> None:
        page = render(demo_brief())
        assert not re.search(r'(src|href)="(https?:)?//', page)
        assert "@import" not in page

    def test_a_sparse_payload_still_renders(self) -> None:
        page = render({"status": "STUCK", "steps": []})
        assert "No steps were taken." in page


class TestTheCommand:
    def test_demo_can_write_the_page(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "demo.html"
        assert main(["demo", "--report", str(out)]) == OK
        assert "Gaussian Blur" in out.read_text(encoding="utf-8")
        assert str(out.resolve()) in capsys.readouterr().out

    def test_report_turns_a_saved_json_report_into_a_page(self, tmp_path: Path) -> None:
        trace = tmp_path / "run.json"
        trace.write_text(json.dumps(demo_brief()), encoding="utf-8")
        assert main(["report", str(trace)]) == OK
        assert (tmp_path / "run.html").exists()

    def test_something_that_is_not_a_report_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        trace = tmp_path / "task.json"
        trace.write_text(json.dumps({"goal": "g", "checks": ["c"]}), encoding="utf-8")
        assert main(["report", str(trace)]) != OK
        assert "not a deskhand report" in capsys.readouterr().err
        assert not (tmp_path / "task.html").exists()
