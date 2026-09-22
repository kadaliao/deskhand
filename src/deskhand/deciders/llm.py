"""A decider that asks a model, and cannot be talked into a coordinate click.

The prompt is built from ``Task.brief()`` and ``View.brief()`` and nothing else.
Those payloads carry target ids, kinds, labels, values and offered verbs; for
pixel targets only, a position *relative to the window frame*. A screen
coordinate is never sent.

More importantly it can never be obeyed: ``Choice`` has no field for a point,
``validate.build_action`` resolves a target by id or by label, and the reply
goes through the same strict boundary a JSON task file goes through, which
rejects unknown fields. So a reply that tries to click at ``(412, 88)`` is not
translated into a click -- it becomes a recorded failure that is fed back into
the next decision. ``tests/test_llm_decider.py`` pins that, in three shapes.

Why the payload is a pure function of its arguments: a prompt is the most
consequential thing in an agent and the least likely to be reviewed. ``prompt_for``
is testable without a model, so "what the model was told" is an assertion rather
than a hope.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .. import json_io
from ..errors import ModelFailed
from ..model import Model
from ..types import Choice, Step, Task, View

SYSTEM = """You operate a desktop by choosing one action at a time.

You are given the task, one observation of the screen, and the steps already taken.
Reply with exactly one JSON object, and nothing else.

Choose one action:

  {"verb": "PRESS", "target": "<a target id from the observation>", "why": "<short reason>"}
  {"verb": "OPEN" | "MENU", "target": "<id>", "why": "..."}
  {"verb": "TYPE", "target": "<id>", "value_from": "<a key in task.inputs>", "why": "..."}
  {"verb": "SET", "target": "<id>", "value_from": "<a key in task.inputs>", "why": "..."}
  {"verb": "DRAG", "target": "<id>", "onto": "<another id>", "why": "..."}
  {"verb": "KEY", "key": "<key name>", "why": "..."}
  {"verb": "CHORD", "chord": "CMD+A", "why": "..."}
  {"verb": "SCROLL", "scroll": "UP" | "DOWN" | "LEFT" | "RIGHT", "why": "..."}
  {"verb": "WAIT", "why": "..."}

Or say the work is finished:

  {"finish": "DONE", "says": ["<the checks you believe hold>"], "why": "..."}
  {"finish": "ESCALATE", "why": "..."}

Rules, in order of importance:

1. "target" must be an "id" that appears in the observation. Never invent one, never
   describe a place on the screen, and never give a coordinate. A reply with a point
   in it is refused rather than translated into a click.
2. "verb" must appear in that target's "actions". A target that does not offer a verb
   will not perform it.
3. Text is never yours to invent. "value_from" must name a key in "task.inputs"; there
   is no field for a literal.
4. Say DONE only when the observation itself shows that *every* check in "task.checks"
   is satisfied. An independent verifier is asked about all of them afterwards, and a
   claim it cannot confirm fails the run rather than finishing it. "says" is your own
   statement of what you believe, recorded for the trace; it does not narrow what is
   checked, so naming a subset does not make DONE easier to reach.
5. If a step in "steps_so_far" failed, do not repeat it. Read the failure and choose
   something different.
6. Prefer a target with "visual": true only when nothing semantic will do: it is a real
   mouse click at a place on the screen, not a control.
"""


def prompt_for(
    task: Task, view: View, steps: Sequence[Step], *, history: int = 8
) -> tuple[str, str]:
    """The exact system text and payload a model is asked with.

    ``history`` bounds the step record, because a long run would otherwise send a
    transcript that grows without limit. Zero sends none, which is what an
    ablation would need.
    """
    payload = {
        "task": task.brief(),
        "view": view.brief(),
        "steps_so_far": [step.brief() for step in steps[-history:]] if history > 0 else [],
    }
    return SYSTEM, json.dumps(payload, ensure_ascii=False, indent=1)


def choice_from_reply(reply: Mapping[str, Any], *, where: str) -> Choice:
    """A model's reply as a ``Choice``, through the same door a task file uses.

    ``json_io.choice_from_dict`` rejects unknown fields and unknown verb or
    status names, so a hallucinated field is a reportable failure instead of a
    silently ignored one.
    """
    try:
        return json_io.choice_from_dict(reply)
    except (TypeError, ValueError, KeyError) as exc:
        raise ModelFailed(f"{where}: the reply is not a usable choice: {exc}") from exc


class LLMDecider:
    """``Decider`` over a :class:`~deskhand.model.Model`.

    Failures are deliberately *raised* rather than absorbed here. The runtime
    already counts them, records the reason, and includes the failed step in the
    next ``steps_so_far`` -- which is where a model learns what it got wrong.
    """

    def __init__(self, model: Model, *, history: int = 8) -> None:
        self.model = model
        self.history = history

    def choose(self, *, task: Task, view: View, steps: Sequence[Step]) -> Choice:
        system, prompt = prompt_for(task, view, steps, history=self.history)
        reply = self.model.ask(system=system, prompt=prompt)
        return choice_from_reply(reply, where=f"{self.model.name} at step {len(steps) + 1}")
