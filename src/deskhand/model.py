"""The model seam: one narrow way for a decider or a verifier to ask a model.

Kept thin on purpose, and vendor-free on purpose. This module names no provider,
reads no vendor-specific key, and ships no HTTP client. A :class:`Model` is
anything that turns one prompt into one JSON object.

Why a seam rather than a client:

* **A model call is the least reliable part of the run.** Every failure path in
  ``runner.py`` is about a decision that went wrong, and none of them are
  reachable in a test if the only way to obtain a decision is a network call.
  ``FakeModel`` is a script of strings, so the interesting paths are testable on
  any machine, offline.
* **The safety property is not which vendor is used.** It is that coordinate
  clicks cannot be asked for and cannot be expressed. That is enforced by types
  and by ``validate.build_action``, not by asking a model politely -- see
  ``deciders/llm.py``.

``CommandModel`` is the shipped real transport, and it is deliberately not an
API client: it runs *your* command, from ``DESKHAND_MODEL_COMMAND``, with the
prompt on stdin and reads one JSON object back from stdout. That keeps the key
in whatever already holds it (``ollama run``, ``llm``, a vendor CLI, a shell
script) and keeps a vendor out of this package's dependency list.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .errors import ModelFailed

ENV_COMMAND = "DESKHAND_MODEL_COMMAND"
"""A command that reads a prompt on stdin and writes one JSON object to stdout."""

ENV_TIMEOUT = "DESKHAND_MODEL_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 120.0

SEPARATOR = "\n\n"
"""Between the system text and the prompt, for transports that take one string."""

_DECODER = json.JSONDecoder()
_FENCE = "```"


@runtime_checkable
class Model(Protocol):
    """One prompt in, one JSON object out.

    ``ask`` is allowed to raise anything: the loop counts a failure, records the
    reason, and asks again. What it may not do is answer with something that is
    not an object, because a decider or a verifier cannot act on prose.
    """

    @property
    def name(self) -> str:
        """Short id, used where a run has to say which model answered.

        So an escalation names the model that could not be asked, and a confirmed
        check names the model that confirmed it.
        """
        ...

    def ask(self, *, system: str, prompt: str) -> Mapping[str, Any]:
        """One question, one JSON object.

        ``system`` is the stable instruction and ``prompt`` is the state of the
        world; a transport that takes a single string joins them with
        :data:`SEPARATOR`. Free to raise: the loop counts the failure and asks
        again, so a bad call costs a step rather than a run.
        """
        ...


# --------------------------------------------------------------------------- #
# reading a reply
# --------------------------------------------------------------------------- #


def parse_object(text: str, *, what: str) -> dict[str, Any]:
    """The one JSON object in a model reply.

    ``Model.ask`` is documented as returning one object, so a reply containing two
    is a failure to report rather than a puzzle to solve. This used to take the
    *first* one, which is the wrong answer whenever the first is a draft, an echoed
    format example, or a decoy -- and a decoy is perfectly valid JSON, so it became a
    real action on a real screen.

    A reply that repeats the *same* object (fenced, then echoed in prose) is accepted.
    Two different objects are refused, and the reason is fed back into the next
    decision, which is the only thing that can fix it.
    """
    found = _objects(text)
    if not found:
        raise ModelFailed(f"{what}: no JSON object in the reply: {excerpt(text)}")
    distinct = _distinct(found)
    if len(distinct) > 1:
        raise ModelFailed(
            f"{what}: the reply contains {len(distinct)} different JSON objects and only one "
            f"can be acted on: {excerpt(text)}"
        )
    return distinct[0]


def _objects(text: str) -> list[dict[str, Any]]:
    """Every JSON object in the reply, wherever it is: fenced, bare, or in prose.

    Fenced blocks are scanned twice over -- once as a block, once as part of the whole
    reply -- and that is deliberate: it makes a repeated object indistinguishable from
    a single one, while a disagreement between them stays visible.
    """
    found: list[dict[str, Any]] = []
    for candidate in [*_fences(text), text]:
        found.extend(_scan(candidate))
    return found


def _fences(text: str) -> list[str]:
    """The inside of each ``` block, so a model's own formatting is not read as prose."""
    blocks: list[str] = []
    rest = text
    while True:
        start = rest.find(_FENCE)
        if start == -1:
            break
        end = rest.find(_FENCE, start + len(_FENCE))
        if end == -1:
            break
        blocks.append(rest[start + len(_FENCE) : end])
        rest = rest[end + len(_FENCE) :]
    return blocks


def _scan(text: str) -> list[dict[str, Any]]:
    """Every complete JSON object in one string.

    ``raw_decode`` does the parsing, so nesting and braces inside strings are handled
    correctly instead of by a hand-rolled brace counter. Scanning resumes *after* each
    object that decodes, so the members of a nested object are not counted as objects
    of their own -- which would otherwise make one nested reply look ambiguous.
    """
    found: list[dict[str, Any]] = []
    start = text.find("{")
    while start != -1:
        try:
            value, end = _DECODER.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            found.append(value)
            start = text.find("{", max(end, start + 1))
        else:
            start = text.find("{", start + 1)
    return found


def _distinct(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in found:
        if item not in out:
            out.append(item)
    return out


def excerpt(text: str, *, limit: int = 200) -> str:
    """The start of a long reply, on one line, so a failure message stays readable."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}..."


# --------------------------------------------------------------------------- #
# transports
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ModelCall:
    """One request, kept so a test can assert what a model was actually told."""

    system: str
    prompt: str

    @property
    def text(self) -> str:
        """Both halves in one string: what a single-string transport would send."""
        return f"{self.system}{SEPARATOR}{self.prompt}"

    def payload(self) -> dict[str, Any]:
        """The prompt as the structured state it was built from."""
        return parse_object(self.prompt, what="recorded prompt")


Reply = str | Mapping[str, Any] | BaseException
"""A scripted answer: a JSON object, a raw reply string, or an exception to raise."""


class FakeModel:
    """A model whose answers are a list, and whose questions are recorded.

    Raises rather than improvising when the script runs out: a test that silently
    receives a default answer is a test that has stopped testing.
    """

    def __init__(self, replies: Iterable[Reply], *, name: str = "fake") -> None:
        self.name = name
        self._replies = deque(replies)
        self.calls: list[ModelCall] = []

    @property
    def asked(self) -> int:
        return len(self.calls)

    def ask(self, *, system: str, prompt: str) -> Mapping[str, Any]:
        self.calls.append(ModelCall(system=system, prompt=prompt))
        if not self._replies:
            raise ModelFailed(f"{self.name}: the script ran out after {self.asked} question(s)")
        reply = self._replies.popleft()
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Mapping):
            return reply
        return parse_object(reply, what=f"{self.name} reply {self.asked}")


@dataclass(frozen=True, slots=True)
class CommandModel:
    """Runs a command once per question. Holds no key and knows no provider.

    The prompt goes to stdin, the reply is read from stdout, and the command's
    own environment supplies whatever credentials it needs. Shell features are
    not interpreted, so a command that needs a pipe or a redirect should be
    ``sh -c '...'``.
    """

    command: str
    timeout_s: float = DEFAULT_TIMEOUT_S

    @property
    def name(self) -> str:
        return f"command:{self.command}"

    def ask(self, *, system: str, prompt: str) -> Mapping[str, Any]:
        argv = shlex.split(self.command)
        if not argv:
            raise ModelFailed(f"{ENV_COMMAND} is set but empty")
        try:
            done = subprocess.run(
                argv,
                input=f"{system}{SEPARATOR}{prompt}",
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ModelFailed(f"{argv[0]!r} is not on PATH: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ModelFailed(f"{argv[0]!r} did not answer within {self.timeout_s}s") from exc
        except OSError as exc:
            raise ModelFailed(f"could not run {argv[0]!r}: {exc}") from exc
        if done.returncode != 0:
            raise ModelFailed(
                f"{argv[0]!r} exited {done.returncode}: {excerpt(done.stderr) or 'no stderr'}"
            )
        return parse_object(done.stdout, what=f"reply from {argv[0]!r}")


def model_from_env(env: Mapping[str, str] | None = None) -> Model:
    """The one place a provider is named, and the name comes from the caller.

    There is no default. A run that quietly fell back to a deterministic decider
    would be a lie about what decided, so a missing command is an error that says
    how to set one instead.
    """
    source = os.environ if env is None else env
    command = (source.get(ENV_COMMAND) or "").strip()
    if not command:
        raise ModelFailed(
            f"no model configured: set {ENV_COMMAND} to a command that reads a prompt on stdin "
            f"and writes one JSON object to stdout. For example, a local model: "
            f'{ENV_COMMAND}="ollama run llama3.1"'
        )
    raw = (source.get(ENV_TIMEOUT) or "").strip()
    if not raw:
        return CommandModel(command=command)
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ModelFailed(f"{ENV_TIMEOUT} is not a number: {raw!r}") from exc
    return CommandModel(command=command, timeout_s=timeout)
