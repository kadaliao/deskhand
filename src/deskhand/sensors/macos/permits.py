"""Permissions, and who they are actually granted to.

macOS does not grant Accessibility or Screen Recording to ``python``, to ``uv``,
or to this package. It grants them to the application *responsible* for the
process. For a command run in a terminal that is the terminal; for a command run
inside a detached multiplexer it is whatever the system decides that chain adds
up to, and the parent chain no longer reaches a terminal at all.

The only authority on the answer is macOS, so this module asks it: the request
functions below make the system show its own dialog naming the application that
would receive the permission. Read the name off the dialog, toggle that entry.
"""

from __future__ import annotations

from typing import Any

from .apps import ancestors, responsible_app
from .ax import ax, trusted
from .ocr import screen_capture_allowed

ACCESSIBILITY = "accessibility"
SCREEN_RECORDING = "screen_recording"

SETTINGS = "System Settings > Privacy & Security"


def status() -> dict[str, bool]:
    return {ACCESSIBILITY: trusted(), SCREEN_RECORDING: screen_capture_allowed()}


def missing(current: dict[str, bool] | None = None) -> list[str]:
    state = current if current is not None else status()
    return [name for name, granted in state.items() if not granted]


def request(name: str) -> bool:
    """Ask macOS for a permission, which makes it show its dialog.

    Returns the state *after* asking. A user decision does not happen inside this
    call, so a missing permission stays missing here even when the request was
    shown; what matters is that the dialog names the application.
    """
    if name == ACCESSIBILITY:
        options = {ax().kAXTrustedCheckOptionPrompt: True}
        return bool(ax().AXIsProcessTrustedWithOptions(options))
    if name == SCREEN_RECORDING:
        from .ocr import _quartz

        return bool(_quartz().CGRequestScreenCaptureAccess())
    raise ValueError(f"unknown permission {name!r}")


def blame() -> dict[str, Any]:
    """Who this process's permissions will be attributed to.

    Prefers a named application; falls back to the process chain, because when
    nothing in the chain is an application the honest answer is the chain.
    """
    app = responsible_app()
    if app is not None:
        return {"application": app[0], "pid": app[1], "chain": None}
    return {
        "application": None,
        "pid": None,
        "chain": [f"{pid} {comm}" for pid, comm in ancestors()],
    }


def to_do(current: dict[str, bool] | None = None, owner: dict[str, Any] | None = None) -> list[str]:
    """The instructions to print, as plain lines."""
    state = current if current is not None else status()
    who = owner if owner is not None else blame()
    name = who.get("application")

    if not missing(state):
        return ["nothing to grant: both permissions are in place"]

    lines: list[str] = []
    if name:
        lines.append(f"macOS attributes these permissions to: {name} (pid {who.get('pid')})")
        lines.append(f"grant them to {name!r}, not to python, uv or deskhand")
    else:
        lines.append(
            "no ancestor of this process is an application, so macOS has to pick one itself"
        )
        lines.append("run `deskhand permit` and read the name off the dialog it shows")
        for entry in who.get("chain") or []:
            lines.append(f"  process chain: {entry}")

    if not state[ACCESSIBILITY]:
        lines.append(f"Accessibility: {SETTINGS} > Accessibility")
    if not state[SCREEN_RECORDING]:
        lines.append(f"Screen Recording: {SETTINGS} > Screen Recording")

    lines.append("after granting, restart the application named above; macOS only")
    lines.append("re-reads Screen Recording permission at process start")
    return lines
