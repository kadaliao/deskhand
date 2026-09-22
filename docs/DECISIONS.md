# What was kept, what was left behind

This project started from reading another one carefully. `arc-cua` has a good
skeleton and a set of sharp observations; it also has a set of habits worth not
copying. This file records both, so the reasoning does not have to be
rediscovered later.

## Kept: the parts that are actually the idea

| Kept | Where it lives now |
|---|---|
| Perception / decision / loop as three separate seams that only meet at protocols | `protocols.py` |
| A model may pick a target and a verb, but never invents a literal. Text and values are a whitelist supplied by the caller | `Task.inputs` + `validate.build_action`, now also applied to a model's reply (`deciders/llm.py`) |
| The action space is built from what is on screen right now, not from a fixed menu | `View.targets[].actions`, 10 verbs |
| Prove the target still means what it meant before mutating it | `Fingerprint`, `Sensor.is_stale` |
| Bounded termination with a small set of terminal states | `Limits`, `Status` |
| The runtime owns "when is the UI ready again", not the model | `Sensor.settle` |
| Recognised text must not be used as an element's identity, because recognition is noisy | `pixels.target_id` (geometry, not spelling) |
| A step stream for observability | `Runner.steps()` |
| Prefer accessibility over pixels | now an invariant in `fusion.py`, not a sentence in a prompt |
| Verification that cannot see the decision it is checking | `ModelVerifier` receives the task, the live view and the claims -- never the decider's rationale, its action or its steps -- and asks one question per claim, so a set of claims cannot be confirmed by one answer |
| A model call behind a one-method seam, so every failure path is testable without a network | `deskhand.model`: `Model.ask(system, prompt) -> Mapping[str, Any]`. The failure paths in `runner.py` are only reachable in a test if a decision can come from a list of strings |

## Left behind, with the reason

| Not kept | Why |
|---|---|
| Accessibility ids derived from `repr(ref)` | A repr contains a memory address. The reference project already had to patch around the resulting false staleness (its commit *"prevent false stale-target detection in AX backend"* compares semantic guards instead). Identity should come from role, label and bucketed geometry, which also makes a hit test able to match a walked element |
| Freshness checked twice per action (once by the loop, once inside the backend) | Doubles the cost of every action; for the pixel path it means a second window enumeration. The protocol now says so explicitly |
| One bad decision ends the whole run | A model choosing an impossible target is normal. Failures now have a budget, carry a reason, and are fed back into the next decision |
| A decider or provider error escaping as an exception | Same reason. There is a test for each failure path |
| Verification as an optional callback that defaults to "yes" | The reference project's headline feature is "agent-supplied verification criteria", but with no callback configured the policy's own `SUBTASK_COMPLETE` is accepted. Here a decider may claim `DONE`, and `NoVerifier` refuses to confirm it, which escalates instead |
| `constraints` that only ever reach the model as prose | If the runtime does not enforce it, calling it a constraint is a lie. Renamed `notes`, documented as advisory |
| Config fields that nothing reads | `post_action_settle_s` was declared, documented on the website, and never referenced. Every field in `Limits` is used here |
| "Is this text region editable?" answered by an English keyword list (`"search"`, `"email"`, `"whatdoyouwanttoplay"`) | Fails the moment the interface is not in English, and needs a new special case per app. Editability is a question for accessibility, answered by a hit test |
| Two perception sources concatenated into one list | A control then appears twice, once with semantics and once with coordinates, and the model is told to prefer one of them. Fusion now resolves pixel regions into accessibility elements and merges their words as extra names; only genuinely blind regions stay as pixels |
| `elapsed_ms` that is actually "time since the run started" | Misreads as per-step latency in a project whose selling point is latency. Steps carry `look / decide / act / settle` |
| Ten separate attribute reads per node, including nodes that will be discarded | One batched read per node, role-first short circuit, no reads for anonymous containers |
| No accessibility messaging timeout, and every error code flattened to "attribute missing" | A hung target application could block an observation forever, and a diagnosis looked like "the tree is empty". Now a 2 second timeout, and failures are logged |
| Electron/Chromium left to expose a stub tree | The reference project's Spotify example is pixel-heavy, which is a symptom: Chromium only builds its tree when a client sets `AXManualAccessibility`. The attribute is probed for and set |
| Every accessibility element marked visible | Offscreen, hidden and zero-sized elements were handed to the model as candidates. Visibility is derived from geometry and window bounds at the source |
| Coordinates hidden from the model entirely | It cannot tell "the first result" from "the third" when ids are opaque hashes. Pixel targets now carry a relative position inside the window frame, and nothing else does |
| One statement per line (1280 lines averaging 19 characters, 522 of them under 15 characters) | Unreviewable. `ruff format`, 100 columns, and a lint gate in CI |
| A hand-written copy of the docs for the website | Drifted within two commits (a documented `TYPESAFE_BASE_URL` that does not exist, and documented actions the policy can never emit). One source of truth here, generated later if a site is wanted |
| `.DS_Store` committed, no CI, no `py.typed` | Housekeeping, fixed |
| A hard dependency on one decision vendor | `Decider` is a protocol; the shipped deterministic deciders are offline, and the model path is a `Model` protocol whose only real transport is *the user's own command* (`DESKHAND_MODEL_COMMAND`), so no vendor is named and no key is handled here |
| A boundary that was "strict" about field names but not types | `json_io.choice_from_dict` rejected unknown fields and accepted `"target": [4123, 2897]`, which reached `Choice` and only failed later as "that id is not in the current view" -- a message that misdescribes what arrived. Now a wrong type is refused where it is read |

## Two things that are neither: honest trade-offs

* **Rejected decisions are not replayed.** After a stale rejection the loop asks
  the decider again against the fresh view rather than retrying the same action.
  Safer when the screen moved, but it consumes a decision. The trace shows it.
* **Settling polls a cheap structural probe** instead of subscribing to
  accessibility notifications. That is one accessibility walk per poll rather
  than a full pixel observation. `AXObserver` is the real answer and is on the
  plan, not hidden.
* **The shipped real model transport runs your command, not an HTTP request.**
  `CommandModel` starts a process per question (measured at ~25 ms of overhead in
  the run in `docs/PLAN.md`, against a model call that dominates it). That buys no
  vendor, no key in this codebase, no HTTP dependency, and compatibility with
  whatever the user already has -- `ollama run`, `llm`, a vendor CLI, a script.
  It gives up streaming, connection reuse and provider-specific retry behaviour;
  those belong in a transport someone can add without touching a decider, which is
  why the seam exists.

---

# Mistakes this project made, and what they have in common

Kept here on purpose. Most of these are the same mistakes the reference project
made, in the same places, which is the useful part: knowing that a class of bug
exists does not stop you from writing it.

### A benchmark that measured the wrong thing

`docs/PLAN.md` announced "612 ms per step, of which 595 ms is looking around" and
built milestone M3 on it. It was measured by running one step in a fresh process,
and the first accessibility request to an application costs 300–360 ms because
macOS builds the tree lazily. Warm walks are 9–53 ms. The real steady-state step
was about 45 ms, not 612. The lesson is not "measure more"; it is **measure the
same thing twice and be suspicious of the first number**.

### Hard-coded sleeps in the settle loop

The reference project hard-codes `0.03 / 0.18 / 0.65 / 1.5 / 2.5 / 0.10 / 0.12`
as settle timings. This project's first settle loop slept 50 ms before its first
look and 50 ms between looks. Same bug, smaller numbers, and it cost about 100 ms
on every step of a desktop that had already settled. Nothing tested it, because it
looks obviously correct.

### A settle digest that was wrong in the opposite direction

The reference project's structural signature deliberately ignores recognised text,
so a list whose content changed looks unchanged and the run reports BLOCKED. This
project's first version included every element's `value`, so a page with a clock in
it never looked quiet and settling burned its entire budget. One was too blunt, one
was too sensitive, and neither was tested against a live page.

### A field that existed in the design and not in the code

The review of the reference project complained about `post_action_settle_s`: a
config field that was declared, documented on the website, and read by nothing.
This project then read `AXSelected` and `AXExpanded` on every walk, documented
"selection is meaning" in `_place_row`, and **had no `selected` field on `Target` at
all**. It surfaced only because a real action needed to know which tab was active in
order to put it back.

### A boundary that was strict about names and not about types

`json_io.py` opens with "Deliberately dumb and strict. Unknown fields are rejected
rather than ignored", and that was true of field *names* only. A reply of
`{"verb": "PRESS", "target": [4123, 2897]}` passed straight through, because the
only question asked was whether `target` was a known key. The list then reached
`Choice.target` (typed `str | None`), survived `__post_init__`, and failed three
layers later as *"target id not in the current view: [4123.0, 2897.0]"*.

The failure was safe -- `build_action` resolves by id, so a list can never become a
click -- but it was misleading, and it became load-bearing the moment a model's
reply started coming through that door: "what arrived" is exactly what the next
decision is told. It surfaced while writing the test that was *supposed* to prove
the coordinate story, and the test was wrong in the same way the code was: it
asserted a refusal without asking at which layer.

### A claim that was allowed to choose what got checked

`Runner._terminal` verified `tuple(choice.says) or task.checks`. The intent reads
fine -- a decider says which checks it believes hold, a verifier confirms them -- and
it meant **the decider chose the question**. On a task with two criteria, a decider
that named only the one that passes reaches `DONE` while the other is never looked at.

That was survivable while every decider was a deterministic rule this project wrote.
Adding a model decider made it reachable: `{"finish": "DONE", "says": ["the window is
open"]}` had *that* confirmed against the screen and reported `DONE` with the real
criterion never checked -- the exact failure the verifier exists to prevent. The fix is
that `task.checks` is the only thing ever asked about; `says` is now honest about being
a record rather than a selection.

The lesson is about ordering, not about models. When a trust boundary moves -- an
untrusted decider in place of a rule this project wrote -- every field that was
harmless under the old trust model has to be re-read. `says` was harmless.

### A reply parser that acted on the first answer it found

`parse_object` took the first JSON object it could find, searching fenced blocks before
the rest of the reply. The original reasoning sounds fine: *a model that fences is
showing you its answer*, not thinking out loud.

The mirror case is what makes it wrong. A model that echoes the **format example** in a
fence and then answers in prose gets the example acted on. So does a reply with a draft
object before the final one, and a reply with a decoy before the real answer -- all
valid JSON, no trickery required. Because the first object is what a hand would click,
"take the first one" is a wrong-action generator whose failure mode is a real click on
a real screen.

Now two *different* objects are refused and the reason is fed back into the next
decision, while a reply that repeats the same object is still accepted. Taking the last
object was the other defensible reading and was rejected because it makes the opposite
bet -- that a trailing object is never a summary -- whereas refusing costs one step and
is self-correcting. The test that pinned the old behaviour had been written by the same
hand as the heuristic, which is why the docstring read like a justification rather than
a doubt.

### An API that reports success while doing nothing, three times over

`--focus` was written with the right instinct: "`activate` can appear to succeed while
the frontmost application does not change", so it verifies, retries, and refuses with
what it actually found. That instinct came from a benchmark that had timed the terminal
instead of the browser.

The retry could never have worked. Once Screen Recording was granted and a real run was
attempted, every activation mechanism reported success and none of them moved anything:

| Call | Reports | Reality |
|---|---|---|
| `NSRunningApplication.activateWithOptions_` | `True` | frontmost unchanged |
| `open -b com.apple.systempreferences` | exit `0` | frontmost unchanged |
| `open x-apple.systempreferences:...` | exit `0` | frontmost unchanged |

and the cooperative `activate()` that replaced the first of them is not exposed by
pyobjc at all, so the `hasattr` fallback reads like a safety net and is dead code. Three
retries of a call that cannot succeed is not resilience; it is the same failure three
times. The only reason it was visible at all is that the verification loop refused to
measure the wrong application.

The cause was not the target application, and the first explanation offered for it was
wrong. "macOS ignores an activation request from an application that is not itself active"
fitted every measurement taken from an inactive caller, and was disproved the moment the
same failure appeared with an *active* one: run from a frontmost Ghostty, the frontmost
application was that same Ghostty and the activation still did nothing.

There were two causes, and the second hid behind the first.

1. **The activation call was a no-op.** For an application that was *already running*, this
   module only ever called `NSRunningApplication.activateWithOptions_`; the cooperative
   `activate()` that replaced it is not exposed by pyobjc, so the `hasattr` fallback was
   dead code, and no mechanism that was present could have succeeded. `raise_window` now
   asks LaunchServices, which does raise a window.
2. **The verification read was stale, so success looked like failure.** `frontmost()` used
   `NSWorkspace.frontmostApplication()`, which in a process with no run loop answers with
   the application that was in front when it was first asked. Once LaunchServices was asked
   to raise a window, the window server and System Events both agreed it had worked while
   `NSWorkspace` in the same process still named the application from five seconds earlier
   -- so `focus` reported failure on an activation that had *succeeded*, and the error it
   printed named a cause that was wrong.

The lesson is about which part of a stack is allowed to be wrong. Everything here was
correct except one missing call, and the error message -- written with real evidence, in
this file's own voice -- confidently named a cause that turned out to be a coincidence of
the caller's situation. A diagnosis that fits every measurement so far is still a
diagnosis with a counterexample somewhere; the active-caller run was findable and was not
looked for.

One detail did hold up, and it is why the two looked like different bugs: *reading* the
screen kept working perfectly throughout, because Accessibility and Screen Recording were
granted to that same process chain. "Focus never works" and "permissions are fine" were
one fact wearing two symptoms.

### An API that does not update, in a process that is not an application

To start a closed application, `focus` opened it and then waited for it to appear -- using
`NSWorkspace.runningApplications()`, the same call the rest of the sensor uses. It waited
six seconds and reported that the application never appeared.

The application was already running. `pgrep` found it in under a second, and a *fresh*
process saw it in `NSWorkspace` immediately. In a process with no run loop,
`runningApplications()` serves a snapshot: an application that appears after that process
starts is never in it. So a check that looks like the most authoritative question available
("what is running?") was the one thing in the process that could not answer it.

Then the same, worse, for `frontmostApplication()`. Measured three ways inside one
process: `NSWorkspace` named 微信 throughout, while the window server
(`CGWindowListCopyWindowInfo`) and System Events both correctly reported 系统设置 and then
Ghostty as the front application changed underneath. A `frontmost()` built on that single
call was a frontmost reading taken once, at startup.

Two consequences, and the second is a safety one:

- every `--focus` failed on an activation that had succeeded, and printed a diagnosis of
  the wrong cause;
- `AXSource` observed the *stale* application while another was in front. A coordinate
  click is aimed at the window being observed, so during the model run two clicks intended
  for a System Settings sidebar row were delivered to Chrome, which was actually in front.
  The trace shows them as executed, with a route, and nothing happened.

Both now read the frontmost application from the window server, so what is observed and
what a click would hit are the same application by construction. The general lesson is
narrower than "do not use NSWorkspace": the sense of *now* belongs to the API being asked,
and an API whose state arrives by notification answers "now" only if something is
delivering those notifications. This project tests its settle loop for exactly that reason,
and then trusted a snapshot for the two things every run depends on.

### A check that could not fail

`--trust-decider` was implemented by seeding `PredicateVerifier` with a predicate that
returns `True` for every criterion. It warned on stderr and the warning was honest; the
*report* was not. The check came back `ok=True` with the note "predicate matched the live
view", which described a comparison that never happened.

The measurement that exposed it is blunt. On a Chinese macOS the appearance task resolved
`外观` to the settings **window**, coordinate-clicked it, failed to disambiguate `深色`,
changed nothing, and was reported `DONE` with a confirmed check -- while `defaults read -g
AppleInterfaceStyle` said the machine was still in Light mode.

A waiver is a legitimate feature: some runs are exploratory and nobody wants to configure
verification to look at a screen. What is not legitimate is a waiver whose output cannot
be told apart from a confirmation. `WaivedVerifier` now reports `WAIVED ... NOT
independently checked`, and a rehearsal reports a waiver *as* a waiver rather than as a
claim that looks confirmable -- a rehearsal promising the same false success would be the
same bug one step earlier.

The general shape: **an escape hatch has to be as legible as the thing it bypasses.**
`--trust-decider` said so on stderr, while every artefact anyone would actually read -- the
trace, the JSON report, the check line -- looked like a pass.

### A heuristic that stood in for the thing it was measuring

`pixels="auto"` compares the number of accessibility targets against `rich_at = 40` and
skips the screenshot above it. The intent is sound: do not pay for a Vision pass over a
window that is already described richly.

System Settings is the counterexample, and it is the window this project's own milestone is
about: **104 targets, 27 of them `row:outlinerow` sidebar rows with no name at all.** More
targets than almost any window, and precisely the ones a task needs are exactly the ones
with nothing to match on. The count answered "are there many targets", which was never the
question; "how many of them can be matched by a name a person would recognise" was.

It cost a session of perception work being unmeasurable through the default path, which is
why `--pixels` now exists to force the overlay. The heuristic itself is deliberately left
alone: the signal it should use (targets with no label) needs its own measurement before it
becomes the rule, and replacing one guess with another is how this file gets longer.

### The finding that came from being told "lai"

Running one real action found three defects that 108 tests and a careful review had
not: selection is expressed by the parent, a boolean `value` was being turned into
a string, and the settle loop could not tell "the effect has not appeared yet" from
"nothing is happening". The common thread is that all three needed a real
application's answers, and no amount of reading code substitutes for that.
