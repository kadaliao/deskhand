#!/usr/bin/env python
"""One real execution that puts itself back: switch terminal tabs and switch back.

This is the smallest real-machine smoke test that exercises the whole chain --
observe, validate, freshness guard, act, settle, judge, restore -- without
changing anything a person would miss. It is deliberately paranoid:

* it only ever acts on targets whose kind is exactly ``radiobutton:tabbutton``.
  The "Close tab" buttons sit in the same row of the same window and are one
  attribute away; they are refused before an action is built.
* it refuses to run when it cannot tell which tab is currently selected, rather
  than guessing and leaving the terminal somewhere unexpected.
* it verifies the restore, not just the switch.

Run it against a terminal with at least two tabs:

    uv run python examples/verify_execution.py Ghostty
"""

from __future__ import annotations

import sys
import time

from deskhand import Choice, Task, Verb, build_action
from deskhand.sensors.macos.apps import activate, frontmost
from deskhand.sensors.macos.screen import open_sensor

TAB = "radiobutton:tabbutton"


def focus(app: str) -> tuple[str, int]:
    for _ in range(4):
        activate(app)
        time.sleep(0.4)
        current = frontmost()
        if current and current[0] == app:
            return current
    raise SystemExit(f"could not make {app!r} frontmost; it is {frontmost()}")


def main() -> int:
    app = sys.argv[1] if len(sys.argv) > 1 else "Ghostty"
    task = Task(
        goal="switch terminal tab and back", checks=("the original tab is selected",), inputs={}
    )
    print("frontmost:", focus(app))

    sensor = open_sensor(pixels=False)
    started = time.perf_counter()
    view = sensor.observe()
    look_ms = round((time.perf_counter() - started) * 1000)
    print(f"observe: {len(view.targets)} targets in {look_ms}ms | window={view.window!r}")

    tabs = [t for t in view.targets if t.kind == TAB]
    for tab in tabs:
        print(
            f"  {tab.id}  {tab.label!r:<28} selected={tab.selected}"
            f" verbs={sorted(str(v) for v in tab.actions)}"
        )

    selected = [t for t in tabs if t.selected]
    others = [t for t in tabs if t.selected is False and Verb.PRESS in t.actions]
    if len(selected) != 1:
        print(f"ABORT: expected exactly one selected tab, found {len(selected)}")
        return 1
    if not others:
        print("ABORT: no other tab to switch to")
        return 1

    original, target = selected[0], others[0]
    action = build_action(Choice(verb=Verb.PRESS, target=target.id), view, task)
    if action.target != target.id or target.kind != TAB:  # belt and braces
        print("ABORT: refusing to act on anything that is not a tab button")
        return 1
    print(f"\nselected={original.label!r} -> pressing {target.label!r}")
    print("is_stale:", sensor.is_stale(view, action))

    started = time.perf_counter()
    route = sensor.act(view, action)
    act_ms = round((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    after = sensor.settle(view, budget_ms=2500)
    settle_ms = round((time.perf_counter() - started) * 1000)

    now = [t for t in after.targets if t.kind == TAB and t.selected]
    switched = bool(now) and now[0].id == target.id
    total = look_ms + act_ms + settle_ms
    print(f"acted: route={route!r} in {act_ms}ms | settled in {settle_ms}ms | step {total}ms")
    print(
        f"  revision changed={after.revision != view.revision}"
        f" | content changed={after.content != view.content}"
    )
    print(f"now selected: {[t.label for t in now]} -> switched={switched}")

    # Put it back. The point of the test is that nothing is left changed.
    restore = build_action(Choice(verb=Verb.PRESS, target=original.id), after, task)
    sensor.act(after, restore)
    restored = sensor.settle(after, budget_ms=2500)
    final = [t for t in restored.targets if t.kind == TAB and t.selected]
    back = bool(final) and final[0].id == original.id
    print(f"restored: {[t.label for t in final]} -> back={back}")

    ok = switched and back and route.startswith("ax-")
    print(
        f"\nverdict: {'PASS' if ok else 'FAIL'}"
        f" (switched={switched}, restored={back}, semantic={route.startswith('ax-')})"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
