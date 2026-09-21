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

CAN_PROMPT = {ACCESSIBILITY: True, SCREEN_RECORDING: False}
"""Whether macOS will show its own dialog for this permission.

Measured, not assumed. For Screen Recording, tccd answers a CLI or daemon caller
with ``AUTHREQ_CTX ... preflight=no`` followed by "Service
kTCCServiceScreenCapture does not allow prompting; returning denied.": no dialog
appears and the caller is told nothing. Accessibility does prompt, and asking for
it also adds the calling application to the Accessibility list as a pending
entry, which is most of the work.
"""

_PANE_BASE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"

PANE_URL = {
    ACCESSIBILITY: _PANE_BASE + "Accessibility",
    SCREEN_RECORDING: _PANE_BASE + "ScreenCapture",
}
FACE = {ACCESSIBILITY: "Accessibility", SCREEN_RECORDING: "Screen Recording"}


def status() -> dict[str, bool]:
    return {ACCESSIBILITY: trusted(), SCREEN_RECORDING: screen_capture_allowed()}


def missing(current: dict[str, bool] | None = None) -> list[str]:
    state = current if current is not None else status()
    return [name for name, granted in state.items() if not granted]


def request(name: str) -> tuple[bool, str]:
    """Ask macOS for a permission. Returns ``(granted_now, what happened)``.

    The decision never happens inside this call: the user has to answer, and for
    Screen Recording there is no dialog to answer. The returned note says which
    of those it was, because "I asked and nothing happened" is the moment people
    give up on a permission problem.
    """
    if name == ACCESSIBILITY:
        options = {ax().kAXTrustedCheckOptionPrompt: True}
        granted = bool(ax().AXIsProcessTrustedWithOptions(options))
        note = (
            "asked; macOS shows an Accessibility dialog and lists this application"
            if not granted
            else "already granted"
        )
        return granted, note
    if name == SCREEN_RECORDING:
        from .ocr import _quartz

        granted = bool(_quartz().CGRequestScreenCaptureAccess())
        if granted:
            return True, "already granted"
        return False, (
            "macOS will not prompt for Screen Recording from a command line process; "
            "add the application in System Settings by hand"
        )
    raise ValueError(f"unknown permission {name!r}")


def open_pane(name: str) -> bool:
    """Open the exact System Settings page for a permission.

    The only part of this problem that can be automated with confidence: showing
    a person the right list is more useful than explaining where it is.
    """
    url = PANE_URL.get(name)
    if url is None:
        raise ValueError(f"unknown permission {name!r}")
    from .ax import appkit

    nsurl = appkit().NSURL.URLWithString_(url)
    return bool(appkit().NSWorkspace.sharedWorkspace().openURL_(nsurl))


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

    for name in (ACCESSIBILITY, SCREEN_RECORDING):
        if state[name]:
            continue
        lines.append(f"{FACE[name]}: {SETTINGS} > {FACE[name]}")
        if not CAN_PROMPT[name]:
            lines.append(
                f"  macOS cannot prompt for {FACE[name]} from a command line process, so there is"
            )
            lines.append("  no dialog to click: add the application in that list with the + button")

    lines.append("after granting, restart the application named above; macOS only")
    lines.append("re-reads Screen Recording permission at process start")
    lines.append("`deskhand permit --open` jumps straight to the right settings page")
    return lines
