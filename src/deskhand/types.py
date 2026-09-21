"""The whole shape of the data. Plain names, no prefixes, no jargon."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .fingerprint import GRID, Fingerprint, digest, grid, norm_text

# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #


class Status(StrEnum):
    """How a run ended."""

    DONE = "DONE"
    STUCK = "STUCK"
    ESCALATE = "ESCALATE"


class Verb(StrEnum):
    """Everything the hand can do. Deliberately small and closed.

    Ten verbs instead of a new action type per API. ``ON_A_TARGET`` verbs name
    one target; the rest are desktop-wide.
    """

    PRESS = "PRESS"
    OPEN = "OPEN"
    MENU = "MENU"
    TYPE = "TYPE"
    SET = "SET"
    DRAG = "DRAG"
    KEY = "KEY"
    CHORD = "CHORD"
    SCROLL = "SCROLL"
    WAIT = "WAIT"


ON_A_TARGET: frozenset[Verb] = frozenset(
    {Verb.PRESS, Verb.OPEN, Verb.MENU, Verb.TYPE, Verb.SET, Verb.DRAG}
)
"""Verbs that must resolve to a target in the current view."""

ANYWHERE: frozenset[Verb] = frozenset({Verb.KEY, Verb.CHORD, Verb.SCROLL, Verb.WAIT})
"""Verbs that need no target."""

FROM_INPUT: frozenset[Verb] = frozenset({Verb.TYPE, Verb.SET})
"""Verbs whose payload must come from ``Task.inputs``, never from the decider."""

SCROLLS: frozenset[str] = frozenset({"UP", "DOWN", "LEFT", "RIGHT"})
MODIFIERS: frozenset[str] = frozenset({"CMD", "SHIFT", "ALT", "CTRL", "FN"})


# --------------------------------------------------------------------------- #
# what is on screen
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Box:
    """A rectangle in global screen points."""

    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def holds(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h

    def overlap(self, other: Box) -> float:
        """Intersection over union, 0..1."""
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.w, other.x + other.w)
        bottom = min(self.y + self.h, other.y + other.h)
        if right <= left or bottom <= top:
            return 0.0
        intersection = (right - left) * (bottom - top)
        union = self.area + other.area - intersection
        return intersection / union if union > 0 else 0.0


@dataclass(frozen=True, slots=True)
class Target:
    """Something on screen you can aim at.

    ``visual`` marks a target that only exists in pixels. Visual targets are
    executed by moving the real mouse, so they are matched loosely and should be
    a last resort: if a point on screen resolves to an accessibility element,
    the accessibility element wins and the visual target disappears.
    """

    id: str
    kind: str
    label: str = ""
    labels: tuple[str, ...] = ()
    value: str | None = None
    actions: frozenset[Verb] = frozenset()
    box: Box | None = None
    source: str = "unknown"
    visual: bool = False
    enabled: bool = True
    focused: bool = False
    score: float = 1.0
    note: str = ""

    def can(self, verb: Verb) -> bool:
        return verb in self.actions or verb in ANYWHERE

    def spoken(self) -> str:
        """Everything this target is called, primary label first."""
        seen: list[str] = []
        for candidate in (self.label, *self.labels):
            if candidate and candidate not in seen:
                seen.append(candidate)
        return " ".join(seen)

    def fingerprint(self) -> Fingerprint:
        """Signature used to prove the target still means what it meant."""
        if self.visual:
            x, y = self.box.center if self.box else (0.0, 0.0)
            return Fingerprint(
                "visual", digest([norm_text(self.spoken())]), self.spoken(), grid(x), grid(y)
            )
        rows = [
            f"kind={self.kind}",
            f"label={norm_text(self.label)}",
            f"value={norm_text(str(self.value))}",
            f"enabled={self.enabled}",
            f"focused={self.focused}",
        ]
        return Fingerprint("semantic", digest(rows))

    def brief(self, *, frame: Box | None = None) -> dict[str, Any]:
        """The decider-visible form. No raw coordinates unless it is a pixel target."""
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "actions": sorted(str(a) for a in self.actions) or sorted(str(a) for a in ANYWHERE),
        }
        if self.labels:
            out["also_called"] = list(self.labels)
        if self.value not in (None, ""):
            out["value"] = self.value
        if self.visual:
            out["visual"] = True
            if self.box is not None and frame is not None and frame.w > 0 and frame.h > 0:
                cx, cy = self.box.center
                out["at"] = [round((cx - frame.x) / frame.w, 2), round((cy - frame.y) / frame.h, 2)]
            out["score"] = round(self.score, 2)
        if self.focused:
            out["focused"] = True
        if not self.enabled:
            out["enabled"] = False
        if self.note:
            out["note"] = self.note
        return out


def _identity_row(target: Target) -> str:
    """What is here and what it says. No geometry, no focus.

    Geometry is deliberately excluded so that a re-walk with sub-pixel jitter, or
    a control that merely moved, does not look like the desktop changed. For pixel
    targets the words are part of the identity, so recognition noise can register
    as a change -- it is only ever used in the safe direction (see ``content``).
    """
    return (
        f"{target.kind}|{norm_text(target.label)}|{norm_text(str(target.value))}"
        f"|{int(target.enabled)}|{target.source}"
    )


def _place_row(target: Target) -> str:
    box = target.box
    place = "no-box" if box is None else f"{int(box.x // GRID)},{int(box.y // GRID)}"
    return f"{place}|{int(target.focused)}"


def structure_digest(targets: tuple[Target, ...]) -> str:
    """Does the desktop still mean what it meant? Used for settling and change.

    Sorted, so accessibility walk order cannot change the answer.
    """
    return digest(sorted(_identity_row(t) for t in targets))


def content_digest(targets: tuple[Target, ...]) -> str:
    """Identity *and* placement. Used to decide whether anything happened at all.

    Movement counts. A list that scrolled with its text intact, a panel that slid
    into place, a caret that moved: all are progress, none are a reason to declare
    the run stuck.
    """
    return digest(sorted(f"{_identity_row(t)}|{_place_row(t)}" for t in targets))


@dataclass(frozen=True, slots=True)
class View:
    """One observation of the desktop.

    ``revision`` answers "does this still mean what it meant?" and is used for
    settling. ``content`` adds placement, so it also changes when something merely
    moved; it is used only to decide whether anything happened at all. That split
    is what stops a changed list and a sliding panel from looking like a stalemate.
    """

    app: str
    window: str
    revision: str
    targets: tuple[Target, ...]
    content: str = ""
    frame: Box | None = None
    notes: Mapping[str, Any] = field(default_factory=dict)
    at_ms: int = 0
    _index: dict[str, Target] | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.content:
            object.__setattr__(self, "content", content_digest(self.targets))

    def target(self, target_id: str) -> Target:
        index = self._index
        if index is None:
            index = {t.id: t for t in self.targets}
            object.__setattr__(self, "_index", index)
        return index[target_id]

    def find(
        self,
        *,
        kind: str | None = None,
        label: str | None = None,
        visual: bool | None = None,
    ) -> tuple[Target, ...]:
        wanted = norm_text(label) if label else None
        out = []
        for target in self.targets:
            if kind is not None and target.kind != kind:
                continue
            if visual is not None and target.visual != visual:
                continue
            if wanted is not None and wanted not in norm_text(target.spoken()):
                continue
            if not target.enabled:
                continue
            out.append(target)
        return tuple(out)

    def brief(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "window": self.window,
            "targets": [t.brief(frame=self.frame) for t in self.targets if t.enabled],
        }


# --------------------------------------------------------------------------- #
# the job
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Limits:
    """Every bound the loop respects. One object, no scattered timers."""

    max_steps: int = 30
    max_ms: int | None = None
    max_failures: int = 3
    no_progress_steps: int = 3
    settle_ms: int = 2500

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("Limits.max_steps must be >= 1")


@dataclass(frozen=True, slots=True)
class Task:
    """What the caller asks for.

    ``notes`` is advisory text handed to the decider. It is *not* enforced --
    naming it ``notes`` rather than ``constraints`` is deliberate, so nobody
    believes the runtime is holding a line it is not holding.
    """

    goal: str
    checks: tuple[str, ...]
    inputs: Mapping[str, str | int | float | bool] = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("Task.goal cannot be empty")
        if not self.checks:
            raise ValueError("Task.checks must name at least one acceptance criterion")

    def brief(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "checks": list(self.checks),
            "inputs": dict(self.inputs),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# deciding
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Choice:
    """What a decider returned: one verb, or a claim that we are finished.

    ``says`` carries the checks the decider believes are satisfied. A claim is
    only a claim; a Verifier has to confirm it before the run may report DONE.
    """

    verb: Verb | None = None
    finish: Status | None = None
    target: str | None = None
    target_label: str | None = None
    onto: str | None = None
    onto_label: str | None = None
    value_from: str | None = None
    key: str | None = None
    chord: str | None = None
    scroll: str | None = None
    says: tuple[str, ...] = ()
    why: str = ""
    hard: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.verb is None) == (self.finish is None):
            raise ValueError("Choice must carry exactly one of verb or finish")
        if (
            self.verb is not None
            and self.target is None
            and self.target_label is None
            and self.verb not in ANYWHERE
        ):
            raise ValueError(f"{self.verb} needs a target or a target_label")

    def brief(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.finish is not None:
            out["finish"] = str(self.finish)
            if self.says:
                out["says"] = list(self.says)
        if self.verb is not None:
            out["verb"] = str(self.verb)
        for key in (
            "target",
            "target_label",
            "onto",
            "onto_label",
            "value_from",
            "key",
            "chord",
            "scroll",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.why:
            out["why"] = self.why
        return out


@dataclass(frozen=True, slots=True)
class Action:
    """A Choice that survived validation against the live view."""

    verb: Verb
    target: str | None = None
    onto: str | None = None
    value: str | None = None
    key: str | None = None
    chord: str | None = None
    scroll: str | None = None
    guard: Fingerprint | None = None
    onto_guard: Fingerprint | None = None
    visual: bool = False

    def brief(self) -> dict[str, Any]:
        out: dict[str, Any] = {"verb": str(self.verb)}
        for key in ("target", "onto", "value", "key", "chord", "scroll"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


# --------------------------------------------------------------------------- #
# what happened
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Times:
    """Per-phase cost of one step, in milliseconds."""

    look: int = 0
    decide: int = 0
    act: int = 0
    settle: int = 0

    @property
    def total(self) -> int:
        return self.look + self.decide + self.act + self.settle


@dataclass(frozen=True, slots=True)
class Step:
    """One turn of the loop, whether or not it succeeded."""

    n: int
    before: str
    after: str
    times: Times
    choice: Choice | None = None
    action: Action | None = None
    target_label: str | None = None
    via: str | None = None
    changed: bool = False
    progress: bool = False
    failed: str | None = None
    why: str = ""

    def brief(self) -> dict[str, Any]:
        out: dict[str, Any] = {"n": self.n}
        if self.choice is not None:
            out["choice"] = self.choice.brief()
        if self.target_label:
            out["target_label"] = self.target_label
        if self.failed:
            out["failed"] = self.failed
        if self.action is not None:
            out["action"] = self.action.brief()
        if self.via:
            out["via"] = self.via
        out["changed"] = self.changed
        out["progress"] = self.progress
        out["ms"] = {
            "look": self.times.look,
            "decide": self.times.decide,
            "act": self.times.act,
            "settle": self.times.settle,
        }
        if self.why:
            out["why"] = self.why
        return out


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: str
    ok: bool
    how: str

    def brief(self) -> dict[str, Any]:
        return {"check": self.check, "ok": self.ok, "how": self.how}


@dataclass(frozen=True, slots=True)
class Report:
    status: Status
    task: Task
    view: View
    steps: tuple[Step, ...]
    checked: tuple[CheckResult, ...] = ()
    why: str = ""

    @property
    def steps_taken(self) -> int:
        return len(self.steps)

    def brief(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "why": self.why,
            "steps_taken": self.steps_taken,
            "checks": [c.brief() for c in self.checked],
            "steps": [s.brief() for s in self.steps],
            "view": self.view.brief(),
        }
