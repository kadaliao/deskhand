# What was kept, what was left behind

This project started from reading another one carefully. `arc-cua` has a good
skeleton and a set of sharp observations; it also has a set of habits worth not
copying. This file records both, so the reasoning does not have to be
rediscovered later.

## Kept: the parts that are actually the idea

| Kept | Where it lives now |
|---|---|
| Perception / decision / loop as three separate seams that only meet at protocols | `protocols.py` |
| A model may pick a target and a verb, but never invents a literal. Text and values are a whitelist supplied by the caller | `Task.inputs` + `validate.build_action` |
| The action space is built from what is on screen right now, not from a fixed menu | `View.targets[].actions`, 10 verbs |
| Prove the target still means what it meant before mutating it | `Fingerprint`, `Sensor.is_stale` |
| Bounded termination with a small set of terminal states | `Limits`, `Status` |
| The runtime owns "when is the UI ready again", not the model | `Sensor.settle` |
| Recognised text must not be used as an element's identity, because recognition is noisy | `pixels.target_id` (geometry, not spelling) |
| A step stream for observability | `Runner.steps()` |
| Prefer accessibility over pixels | now an invariant in `fusion.py`, not a sentence in a prompt |

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
| A hard dependency on one decision vendor | `Decider` is a protocol; the shipped deciders are deterministic and offline |

## Two things that are neither: honest trade-offs

* **Rejected decisions are not replayed.** After a stale rejection the loop asks
  the decider again against the fresh view rather than retrying the same action.
  Safer when the screen moved, but it consumes a decision. The trace shows it.
* **Settling polls a cheap structural probe** instead of subscribing to
  accessibility notifications. That is one accessibility walk per poll rather
  than a full pixel observation. `AXObserver` is the real answer and is on the
  plan, not hidden.
