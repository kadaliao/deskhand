"""The JSON boundary: what a caller is allowed to send and expect back.

Deliberately dumb and strict. Unknown fields are rejected rather than ignored,
because a silently dropped field is a bug the caller cannot see.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import Choice, Limits, Report, Status, Task, Verb, View
from .verify import Expectation

TASK_FIELDS = {"goal", "checks", "inputs", "notes", "limits", "steps", "expect"}
EXPECT_FIELDS = {"label", "kind", "selected", "value", "enabled", "focused", "absent"}
LIMIT_FIELDS = {
    "max_steps",
    "max_ms",
    "max_failures",
    "no_progress_steps",
    "loop_steps",
    "settle_ms",
}
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


CHOICE_TEXT_FIELDS = (
    "target",
    "target_label",
    "onto",
    "onto_label",
    "value_from",
    "key",
    "chord",
    "scroll",
    "why",
)
"""Choice fields that are one string, or absent."""


def _reject_unknown(payload: Mapping[str, Any], allowed: set[str], what: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown {what} field(s): {unknown}")


def _require_text(data: Mapping[str, Any]) -> None:
    """Refuse a wrongly typed scalar here, not three layers further down.

    A list where an id belongs used to reach ``Choice`` untouched and surface as
    "that id is not in the current view", which misdescribes what arrived. That
    matters more now that a model's reply comes through this door: "what arrived"
    is the thing worth reporting, and it is what the next decision is told.
    """
    for field in CHOICE_TEXT_FIELDS:
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"choice field {field!r} must be a string, got {type(value).__name__}")
    says = data.get("says")
    if says is None:
        return
    if not isinstance(says, list):
        raise ValueError(f"choice field 'says' must be a list, got {type(says).__name__}")
    wrong = [item for item in says if not isinstance(item, str)]
    if wrong:
        # Iterating a bare string would silently turn "abc" into three claims.
        raise ValueError(f"choice field 'says' must contain only strings, got {wrong[:4]}")


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
    _require_text(data)
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


def expectation_from_dict(check: str, payload: Any) -> Expectation:
    if not isinstance(payload, Mapping):
        raise ValueError(f"expect[{check!r}] must be an object like {{'label': 'Dark'}}")
    _reject_unknown(payload, EXPECT_FIELDS, f"expect[{check!r}]")
    label = payload.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"expect[{check!r}] needs a non-empty 'label'")
    kind = payload.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise ValueError(f"expect[{check!r}].kind must be a string")
    flags: dict[str, bool | None] = {}
    for name in ("selected", "enabled", "focused", "absent"):
        value = payload.get(name)
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"expect[{check!r}].{name} must be true or false")
        flags[name] = value
    value = payload.get("value")
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"expect[{check!r}].value must be a string, number or boolean")
    return Expectation(
        label=label,
        kind=kind,
        selected=flags["selected"],
        value=value,
        enabled=flags["enabled"],
        focused=flags["focused"],
        absent=bool(flags["absent"]),
    )


def expectations_from_payload(payload: Mapping[str, Any], task: Task) -> dict[str, Expectation]:
    """``expect`` maps a check, word for word, to the state that confirms it.

    A key that is not one of the task's checks is refused: it would declare how to verify
    something nobody asked for, while the check it was meant for silently goes unverified.
    """
    raw = payload.get("expect") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("'expect' must be an object mapping a check to what confirms it")
    unknown = sorted(set(raw) - set(task.checks))
    if unknown:
        raise ValueError(f"expect names check(s) the task does not have: {unknown}")
    return {str(check): expectation_from_dict(str(check), spec) for check, spec in raw.items()}


@dataclass(frozen=True, slots=True)
class TaskFile:
    """Everything a task file can say: the task, an optional script, how to check it."""

    task: Task
    steps: tuple[Choice, ...] = ()
    expect: Mapping[str, Expectation] = dataclasses.field(default_factory=dict)


def task_file_from_dict(payload: Mapping[str, Any]) -> TaskFile:
    task = task_from_dict(payload)
    return TaskFile(task, choices_from_payload(payload), expectations_from_payload(payload, task))


def load_task_file(path: str | Path) -> TaskFile:
    return task_file_from_dict(_read_object(path))


def load_task(path: str | Path) -> tuple[Task, tuple[Choice, ...]]:
    """Read a task file. ``steps`` is optional and becomes a ScriptedDecider script.

    These failures are deliberately not absorbed here: the CLI has one handler for
    a bad input file and reports it with an exit code. What this adds is *which*
    file, which ``json``'s own message does not say.
    """
    loaded = load_task_file(path)
    return loaded.task, loaded.steps


def _read_object(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"no task file at {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("task file must contain a JSON object")
    return payload


def dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
