"""The JSON boundary: what a caller is allowed to send and expect back.

Deliberately dumb and strict. Unknown fields are rejected rather than ignored,
because a silently dropped field is a bug the caller cannot see.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .types import Choice, Limits, Report, Status, Task, Verb, View

TASK_FIELDS = {"goal", "checks", "inputs", "notes", "limits", "steps"}
LIMIT_FIELDS = {"max_steps", "max_ms", "max_failures", "no_progress_steps", "settle_ms"}
CHOICE_FIELDS = {
    "verb",
    "finish",
    "target",
    "target_label",
    "onto",
    "onto_label",
    "value_from",
    "key",
    "chord",
    "scroll",
    "says",
    "why",
}


def _reject_unknown(payload: Mapping[str, Any], allowed: set[str], what: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown {what} field(s): {unknown}")


def task_from_dict(payload: Mapping[str, Any]) -> Task:
    _reject_unknown(payload, TASK_FIELDS, "task")
    limits_payload = dict(payload.get("limits", {}))
    _reject_unknown(limits_payload, LIMIT_FIELDS, "limits")
    return Task(
        goal=str(payload["goal"]),
        checks=tuple(str(c) for c in payload["checks"]),
        inputs={str(k): v for k, v in dict(payload.get("inputs", {})).items()},
        notes=tuple(str(n) for n in payload.get("notes", ())),
        limits=Limits(**limits_payload) if limits_payload else Limits(),
    )


def choice_from_dict(payload: Mapping[str, Any]) -> Choice:
    _reject_unknown(payload, CHOICE_FIELDS, "choice")
    data = dict(payload)
    if "verb" in data:
        data["verb"] = Verb(data["verb"])
    if "finish" in data:
        data["finish"] = Status(data["finish"])
    if "says" in data:
        data["says"] = tuple(str(s) for s in data["says"])
    return Choice(**data)


def choices_from_payload(payload: Mapping[str, Any]) -> tuple[Choice, ...]:
    raw: Sequence[Mapping[str, Any]] = payload.get("steps", ())
    return tuple(choice_from_dict(step) for step in raw)


def view_to_dict(view: View) -> dict[str, Any]:
    return view.brief()


def report_to_dict(report: Report) -> dict[str, Any]:
    return report.brief()


def load_task(path: str | Path) -> tuple[Task, tuple[Choice, ...]]:
    """Read a task file. ``steps`` is optional and becomes a ScriptedDecider script."""
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("task file must contain a JSON object")
    return task_from_dict(payload), choices_from_payload(payload)


def dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
