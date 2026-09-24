"""A run, as one self-contained HTML page.

The input is exactly what ``deskhand run --json`` prints (``Report.brief()``), so a
page can be made from a report saved yesterday on another machine. The output has no
external fonts, scripts or stylesheets: it is opened from disk, attached to an issue,
or archived next to the task file, and it has to look the same in each of those places.

Every value that came from the desktop or from a model is escaped: a window title or
a model's ``why`` is untrusted text, and this page is exactly where it would run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from html import escape
from typing import Any

from .paint import COORDINATE_ROUTES, semantic

PHASES = ("look", "decide", "act", "settle")
IN_SECONDS_FROM_MS = 10_000


def render(report: Mapping[str, Any], *, title: str | None = None) -> str:
    """Render a ``Report.brief()`` payload. Unknown or missing fields degrade, never raise."""
    steps: list[Mapping[str, Any]] = list(report.get("steps") or [])
    task: Mapping[str, Any] = report.get("task") or {}
    view: Mapping[str, Any] = report.get("view") or {}
    status = str(report.get("status") or "UNKNOWN")
    goal = str(task.get("goal") or "(task not recorded)")
    heading = title or goal

    body = "\n".join(
        (
            _header(status, goal, str(report.get("why") or "")),
            _tiles(steps),
            _checks(report.get("checks") or [], task),
            _timeline(steps),
            _targets(view),
            _task(task),
        )
    )
    return PAGE.format(title=escape(f"deskhand · {heading}"), css=CSS, body=body, js=JS)


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #


def _header(status: str, goal: str, why: str) -> str:
    return f"""
<header class="top">
  <div class="eyebrow">deskhand run report</div>
  <div class="headline">
    <h1>{escape(goal)}</h1>
    <span class="pill {_status_class(status)}">{escape(status)}</span>
  </div>
  <p class="why">{escape(why) or "&nbsp;"}</p>
</header>"""


def _tiles(steps: list[Mapping[str, Any]]) -> str:
    vias = [str(s["via"]) for s in steps if s.get("via")]
    ax = sum(1 for via in vias if semantic(via))
    aimed = sum(1 for via in vias if via in COORDINATE_ROUTES)
    failed = sum(1 for s in steps if s.get("failed"))
    total = sum(_total(s) for s in steps)
    tiles = [
        ("Steps", str(len(steps)), f"{failed} refused or failed" if failed else "none failed"),
        ("Wall time", _ms(total), "look + decide + act + settle"),
        ("Through accessibility", f"{ax}/{len(vias)}", "acted without moving the mouse"),
        ("Coordinate clicks", str(aimed), "aimed at a point on screen"),
    ]
    cells = "".join(
        f'<div class="tile{" warn" if name == "Coordinate clicks" and aimed else ""}">'
        f'<div class="tile-name">{escape(name)}</div>'
        f'<div class="tile-value">{escape(value)}</div>'
        f'<div class="tile-note">{escape(note)}</div></div>'
        for name, value, note in tiles
    )
    return f'<section class="tiles">{cells}</section>'


def _checks(checks: Iterable[Mapping[str, Any]], task: Mapping[str, Any]) -> str:
    rows = [
        f'<li class="{"ok" if c.get("ok") else "no"}">'
        f'<span class="mark" aria-hidden="true">{"✓" if c.get("ok") else "✕"}</span>'
        f'<span class="check">{escape(str(c.get("check", "")))}</span>'
        f'<span class="how">{escape(str(c.get("how", "")))}</span></li>'
        for c in checks
    ]
    if not rows:
        wanted = task.get("checks") or []
        rows = [
            f'<li class="pending"><span class="mark" aria-hidden="true">·</span>'
            f'<span class="check">{escape(str(c))}</span>'
            f'<span class="how">not checked: the run did not end in a verified claim</span></li>'
            for c in wanted
        ]
    if not rows:
        return ""
    return f'<section><h2>Checks</h2><ul class="checks">{"".join(rows)}</ul></section>'


def _timeline(steps: list[Mapping[str, Any]]) -> str:
    if not steps:
        return '<section><h2>Steps</h2><p class="empty">No steps were taken.</p></section>'
    longest = max((_total(s) for s in steps), default=0) or 1
    legend = "".join(
        f'<span class="key"><i class="sw {phase}"></i>{phase}</span>' for phase in PHASES
    )
    rows = "".join(_step(step, longest) for step in steps)
    return f"""
<section>
  <div class="section-head"><h2>Steps</h2><div class="legend">{legend}</div></div>
  <ol class="steps">{rows}</ol>
</section>"""


def _step(step: Mapping[str, Any], longest: int) -> str:
    choice: Mapping[str, Any] = step.get("choice") or {}
    failed = step.get("failed")
    what = str(choice.get("verb") or choice.get("finish") or ("FAIL" if failed else "?"))
    label = str(
        step.get("target_label")
        or choice.get("target_label")
        or _keys(choice)
        or choice.get("target")
        or ""
    )
    via = step.get("via")
    chips = []
    if failed:
        kind = "bad"
        chips.append(f'<span class="chip bad">{escape(str(failed))}</span>')
    elif choice.get("finish"):
        # A claim, not an outcome: a DONE the verifier refused must not read as success.
        # The outcome is the status at the top of the page.
        kind = "claim"
        chips.append('<span class="chip">claimed</span>')
    else:
        kind = "act"
        route = "ax" if semantic(via) else ("aim" if via in COORDINATE_ROUTES else "other")
        chips.append(f'<span class="chip {route}">{escape(str(via or "-"))}</span>')
        if not step.get("progress"):
            chips.append('<span class="chip warn">no progress</span>')
        elif not step.get("changed"):
            chips.append('<span class="chip">unchanged</span>')
    ms: Mapping[str, Any] = step.get("ms") or {}
    total = _total(step)
    bar = "".join(
        f'<i class="seg {phase}" style="width:{100 * int(ms.get(phase, 0) or 0) / longest:.2f}%" '
        f'title="{phase} {int(ms.get(phase, 0) or 0)} ms"></i>'
        for phase in PHASES
    )
    detail = escape(json.dumps(_detail(step), indent=2, ensure_ascii=False, default=str))
    why = str(step.get("why") or choice.get("why") or "")
    return f"""
<li class="step is-{kind}">
  <details>
    <summary>
      <span class="n">{escape(str(step.get("n", "")))}</span>
      <span class="verb">{escape(what)}</span>
      <span class="label">{escape(label)}</span>
      <span class="chips">{"".join(chips)}</span>
      <span class="bar" aria-hidden="true">{bar}</span>
      <span class="ms">{escape(_ms(total))}</span>
    </summary>
    <div class="more">
      {f'<p class="step-why">{escape(why)}</p>' if why else ""}
      <pre>{detail}</pre>
    </div>
  </details>
</li>"""


def _targets(view: Mapping[str, Any]) -> str:
    targets: list[Mapping[str, Any]] = list(view.get("targets") or [])
    if not view:
        return ""
    rows = "".join(_target_row(t) for t in targets)
    visual = sum(1 for t in targets if t.get("visual"))
    unnamed = sum(1 for t in targets if not t.get("label"))
    return f"""
<section>
  <div class="section-head">
    <h2>Final view</h2>
    <input id="filter" type="search" placeholder="Filter by id, kind, name or verb"
           aria-label="Filter targets" autocomplete="off">
  </div>
  <p class="meta">
    <b>{escape(str(view.get("app", "?")))}</b> · {escape(str(view.get("window", "")))}
    · {len(targets)} targets, {visual} from pixels, {unnamed} unnamed
    · <span id="shown">{len(targets)}</span> shown
  </p>
  <div class="scroll">
    <table class="targets">
      <colgroup>
        <col class="c-id"><col class="c-kind"><col class="c-name">
        <col class="c-verbs"><col class="c-state">
      </colgroup>
      <thead><tr><th>id</th><th>kind</th><th>name</th><th>verbs</th><th>state</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>"""


def _target_row(target: Mapping[str, Any]) -> str:
    names = [str(target.get("label") or "")]
    names += [str(n) for n in target.get("also_called") or []]
    shown = names[0] or '<span class="none">no name</span>'
    if names[0]:
        shown = escape(names[0])
    aliases = "".join(f'<span class="alias">{escape(n)}</span>' for n in names[1:] if n)
    state = [
        flag
        for flag, on in (
            ("pixels", target.get("visual")),
            ("selected", target.get("selected")),
            ("focused", target.get("focused")),
            ("disabled", target.get("enabled") is False),
        )
        if on
    ]
    if target.get("value") not in (None, ""):
        value = target["value"]
        shown_value = value if isinstance(value, str) else json.dumps(value)
        state.append(f"value={shown_value}")
    if target.get("note"):
        state.append(str(target["note"]))
    verbs = " ".join(str(v) for v in target.get("actions") or [])
    search = " ".join([str(target.get("id", "")), str(target.get("kind", "")), *names, verbs])
    return (
        f'<tr data-search="{escape(search.casefold())}"'
        f"{' class="visual"' if target.get('visual') else ''}>"
        f'<td class="mono">{escape(str(target.get("id", "")))}</td>'
        f'<td class="mono">{escape(str(target.get("kind", "")))}</td>'
        f"<td>{shown}{aliases}</td>"
        f'<td class="mono verbs">{escape(verbs)}</td>'
        f'<td class="state">{escape(", ".join(state))}</td></tr>'
    )


def _task(task: Mapping[str, Any]) -> str:
    if not task:
        return ""
    return f"""
<section>
  <details class="task">
    <summary><h2>Task as given</h2></summary>
    <pre>{escape(json.dumps(dict(task), indent=2, ensure_ascii=False, default=str))}</pre>
  </details>
</section>"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _total(step: Mapping[str, Any]) -> int:
    ms: Mapping[str, Any] = step.get("ms") or {}
    return sum(int(ms.get(phase, 0) or 0) for phase in PHASES)


def _ms(value: int) -> str:
    return f"{value / 1000:.1f} s" if value >= IN_SECONDS_FROM_MS else f"{value} ms"


def _keys(choice: Mapping[str, Any]) -> str:
    return " ".join(
        str(choice[key]) for key in ("key", "chord", "scroll", "value_from") if choice.get(key)
    )


def _detail(step: Mapping[str, Any]) -> dict[str, Any]:
    return {key: step[key] for key in ("choice", "action", "failed", "why") if step.get(key)}


def _status_class(status: str) -> str:
    return {"DONE": "ok", "STUCK": "bad"}.get(status, "warn")


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="deskhand">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<main>
{body}
</main>
<script>{js}</script>
</body>
</html>
"""

CSS = """
:root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #16181d; --muted: #5d6472; --line: #e3e6eb;
  --ok: #1a7f4b; --ok-bg: #e5f5ec; --bad: #c0362c; --bad-bg: #fbe9e7;
  --warn: #9a6200; --warn-bg: #fdf1dc; --chip: #eef0f3;
  --look: #3d7be0; --decide: #8a5cd6; --act: #e0922f; --settle: #2aa198;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111317; --panel: #191c21; --ink: #e8eaee; --muted: #9aa2b1; --line: #2a2e36;
    --ok: #5fd08f; --ok-bg: #16301f; --bad: #ff7a6e; --bad-bg: #3a1a17;
    --warn: #f1b24a; --warn-bg: #36280f; --chip: #242830;
    --look: #6a9ff0; --decide: #ad8af0; --act: #f0aa55; --settle: #4cc2b8;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Noto Sans CJK SC", sans-serif;
}
main { max-width: 1080px; margin: 0 auto; padding: 32px 16px 64px; min-width: 0; }
h1 { overflow-wrap: anywhere; }
h1 { font-size: 26px; line-height: 1.25; margin: 0; font-weight: 650; }
h2 { font-size: 13px; margin: 0; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); font-weight: 650; }
section { margin-top: 28px; }
.section-head { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
section > h2 { margin-bottom: 10px; }
.eyebrow { font: 12px var(--mono); color: var(--muted); margin-bottom: 6px; }
.headline { display: flex; align-items: flex-start; gap: 14px; justify-content: space-between; }
.why { color: var(--muted); margin: 8px 0 0; }
.pill { font: 600 13px var(--mono); padding: 4px 12px; border-radius: 999px; white-space: nowrap; }
.pill.ok { color: var(--ok); background: var(--ok-bg); }
.pill.bad { color: var(--bad); background: var(--bad-bg); }
.pill.warn { color: var(--warn); background: var(--warn-bg); }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; }
.tile.warn { border-color: var(--warn); }
.tile-name { font-size: 12px; color: var(--muted); }
.tile-value { font-size: 26px; font-weight: 650; font-variant-numeric: tabular-nums;
  margin-top: 2px; }
.tile.warn .tile-value { color: var(--warn); }
.tile-note { font-size: 12px; color: var(--muted); }
.checks { list-style: none; margin: 0; padding: 0; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px; }
.checks li { display: grid; grid-template-columns: 22px 1fr auto; gap: 10px; padding: 10px 14px;
  align-items: baseline; }
.checks li + li { border-top: 1px solid var(--line); }
.checks .mark { font-weight: 700; }
.checks .ok .mark { color: var(--ok); }
.checks .no .mark { color: var(--bad); }
.checks .pending .mark, .checks .how { color: var(--muted); }
.checks .how { font-size: 13px; text-align: right; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--muted); }
.legend .key { display: inline-flex; align-items: center; gap: 5px; }
.sw { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.look { background: var(--look); } .decide { background: var(--decide); }
.act { background: var(--act); } .settle { background: var(--settle); }
.steps { list-style: none; margin: 0; padding: 0; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.step + .step { border-top: 1px solid var(--line); }
.step summary { display: grid; align-items: center; gap: 10px; padding: 10px 14px;
  grid-template-columns: 28px 72px minmax(0, 1.2fr) minmax(0, 1fr) minmax(80px, 1fr) 64px;
  cursor: pointer; list-style: none; }
.step summary::-webkit-details-marker { display: none; }
.step summary:hover { background: var(--chip); }
.step .n { font: 12px var(--mono); color: var(--muted); text-align: right; }
.step .verb { font: 600 13px var(--mono); }
.step.is-bad .verb { color: var(--bad); }
.step.is-ok .verb { color: var(--ok); }
.step.is-warn .verb { color: var(--warn); }
.step.is-claim .verb { color: var(--muted); }
.step .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font: 12px var(--mono); padding: 1px 8px; border-radius: 6px; background: var(--chip);
  color: var(--muted); white-space: nowrap; }
.chip.ax { color: var(--ok); background: var(--ok-bg); }
.chip.aim, .chip.warn { color: var(--warn); background: var(--warn-bg); }
.chip.bad { color: var(--bad); background: var(--bad-bg); }
.bar { display: flex; height: 8px; border-radius: 4px; background: var(--chip); overflow: hidden; }
.seg { display: block; height: 100%; }
.ms { font: 12px var(--mono); text-align: right; color: var(--muted); white-space: nowrap;
  font-variant-numeric: tabular-nums; }
.more { padding: 0 14px 14px 52px; }
.step-why { margin: 0 0 8px; }
pre { margin: 0; font: 12px/1.5 var(--mono); background: var(--bg); border: 1px solid var(--line);
  border-radius: 8px; padding: 10px 12px; overflow-x: auto; white-space: pre-wrap;
  word-break: break-word; }
.meta { color: var(--muted); font-size: 13px; margin: 0 0 10px; }
.meta b { color: var(--ink); font-weight: 600; }
input[type=search] { font: inherit; font-size: 14px; padding: 7px 12px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink); width: 280px;
  max-width: 100%; }
input[type=search]:focus { outline: 2px solid var(--look); outline-offset: 1px; }
.scroll { overflow-x: auto; background: var(--panel); border: 1px solid var(--line);
  border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; table-layout: fixed;
  min-width: 640px; }
.c-id { width: 15%; } .c-kind { width: 19%; } .c-name { width: 30%; }
.c-verbs { width: 14%; } .c-state { width: 22%; }
td { overflow-wrap: anywhere; }
th { text-align: left; font-weight: 600; color: var(--muted); font-size: 12px;
  padding: 9px 12px; border-bottom: 1px solid var(--line); position: sticky; top: 0;
  background: var(--panel); }
td { padding: 7px 12px; border-top: 1px solid var(--line); vertical-align: top; }
tbody tr:first-child td { border-top: 0; }
tr.visual td:first-child { box-shadow: inset 3px 0 0 var(--act); }
.mono { font-family: var(--mono); font-size: 12px; }
.verbs, .state { color: var(--muted); }
.none { color: var(--muted); font-style: italic; }
.alias { display: inline-block; margin-left: 6px; font-size: 12px; color: var(--muted);
  background: var(--chip); border-radius: 5px; padding: 0 6px; }
.empty { color: var(--muted); }
.task summary { cursor: pointer; }
.task summary h2 { display: inline; }
.task pre { margin-top: 10px; }
@media (max-width: 720px) {
  .tiles { grid-template-columns: repeat(2, 1fr); }
  .headline { flex-direction: column; gap: 8px; }
  .step summary { grid-template-columns: 22px 56px minmax(0, 1fr) auto;
    grid-template-areas: "n verb label ms" ". chips chips chips" ". bar bar bar"; row-gap: 6px; }
  .step .n { grid-area: n; } .step .verb { grid-area: verb; } .step .label { grid-area: label; }
  .step .ms { grid-area: ms; } .step .chips { grid-area: chips; } .step .bar { grid-area: bar; }
  .more { padding-left: 14px; }
  .checks li { grid-template-columns: 22px 1fr; }
  .checks .how { grid-column: 2; text-align: left; }
}
"""

JS = """
(() => {
  const box = document.getElementById("filter");
  if (!box) return;
  const rows = Array.from(document.querySelectorAll("table.targets tbody tr"));
  const shown = document.getElementById("shown");
  box.addEventListener("input", () => {
    const words = box.value.toLowerCase().split(/\\s+/).filter(Boolean);
    let count = 0;
    for (const row of rows) {
      const hit = words.every((w) => row.dataset.search.includes(w));
      row.hidden = !hit;
      if (hit) count += 1;
    }
    shown.textContent = count;
  });
})();
"""
