"""Run M2's scenario with a real model, on a scripted desktop, with no permissions.

This is M4's acceptance run made runnable: open a media app, search for a track,
play it -- the same shape of task as milestone M2, against the scripted desktop in
``deskhand.demo`` instead of a real Chromium window. It needs no Accessibility
grant, no Screen Recording, and no macOS, because the desktop is a state machine.

It does need a model, and the model comes from your own command:

    DESKHAND_MODEL_COMMAND="ollama run llama3.1" python examples/model_run.py
    DESKHAND_MODEL_COMMAND="llm -m gpt-4o"       python examples/model_run.py

The prompt goes to that command on stdin and one JSON object is read back from
stdout, so the key stays wherever the command already keeps it and this project
keeps no vendor in its dependencies. Two separate commands are started, on
purpose: the decider and the verifier must not share a conversation, or the
verification is not independent of the decision.

What this run does *not* establish is that a real Chromium application behaves
like this scripted one. That is what M2 is for, and it is still open.
"""

from __future__ import annotations

import sys

from deskhand import demo, json_io
from deskhand.deciders import LLMDecider
from deskhand.model import model_from_env
from deskhand.runner import Runner
from deskhand.types import Status
from deskhand.verify import ModelVerifier


def main() -> int:
    task = demo.playback_task()
    decider_model = model_from_env()
    verifier_model = model_from_env()
    print(f"decider : {decider_model.name}")
    print(f"verifier: {verifier_model.name}")
    print(f"goal    : {task.goal}")

    report = Runner(
        sensor=demo.playback_sensor(),
        decider=LLMDecider(decider_model),
        verifier=ModelVerifier(verifier_model),
    ).run(task)

    for step in report.steps:
        if step.failed:
            print(f"{step.n:>3} FAIL {step.failed}: {step.why}")
            continue
        what = step.action.verb if step.action else "FINISH"
        label = step.target_label or ""
        print(f"{step.n:>3} {what:<7} {label:<22} {step.choice.why if step.choice else ''}")

    print(f"\nstatus: {report.status}  steps: {report.steps_taken}  why: {report.why}")
    for check in report.checked:
        print(f"  check {'ok ' if check.ok else 'NO '} {check.check}  ({check.how})")
    print("\nfull trace:")
    print(json_io.dump(report.brief()))

    return 0 if report.status is Status.DONE else 1


if __name__ == "__main__":
    sys.exit(main())
