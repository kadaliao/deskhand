"""Finding the application to work on.

The sensor follows the frontmost application, so something has to make the
intended application frontmost. That is a precondition of every run, and stating
it in one place is better than hoping the caller remembers.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from typing import Any

from ...errors import CannotDo
from .ax import appkit

ANCESTORS = 12
"""How far up the process tree to look for the responsible application."""

REGULAR = 0
"""``NSApplicationActivationPolicyRegular``: has a Dock icon and a real window."""


def _all_apps() -> list[Any]:
    # NSWorkspace owns the list; NSRunningApplication has no class-level accessor.
    return list(appkit().NSWorkspace.sharedWorkspace().runningApplications() or [])


def _name(app: Any) -> str:
    return str(app.localizedName() or app.bundleIdentifier() or f"pid:{app.processIdentifier()}")


def _policy(app: Any) -> int:
    try:
        return int(app.activationPolicy())
    except Exception:
        return REGULAR


def _regular() -> list[Any]:
    """Applications a person would call applications.

    A bare ``runningApplications`` list contains 117 entries on a working machine,
    including Chrome helpers, Dock extras and settings extensions, and a partial
    name match happily picks one of those. Only regular applications have a
    window we could be working on.
    """
    return [app for app in _all_apps() if _policy(app) == REGULAR]


def running() -> list[str]:
    """Localised names of running applications that have a window to show."""
    return sorted({_name(app) for app in _regular()})


def frontmost() -> tuple[str, int] | None:
    app = appkit().NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    return (str(app.localizedName() or "?"), int(app.processIdentifier()))


def _parent_of(pid: int) -> int:
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return 0
    text = result.stdout.strip()
    return int(text) if text.isdigit() else 0


def responsible_app() -> tuple[str, int] | None:
    """The application macOS will attribute permissions to.

    TCC does not grant Accessibility or Screen Recording to ``python``, ``uv`` or
    ``deskhand``. It grants them to the application *responsible* for the process,
    which is the terminal or multiplexer the command was launched from. Walking up
    the parent chain until a pid matches a running application is the only
    reliable way to see which one that is, and getting it wrong is the usual
    reason a permission "is granted" and still does not work.
    """
    gui = {int(app.processIdentifier()): _name(app) for app in _all_apps()}
    pid = os.getpid()
    for _ in range(ANCESTORS):
        if pid in gui:
            return (gui[pid], pid)
        pid = _parent_of(pid)
        if pid <= 1:
            break
    return None


def ancestors() -> list[tuple[int, str]]:
    """The process chain, for when no ancestor is an application at all."""
    chain: list[tuple[int, str]] = []
    pid = os.getpid()
    for _ in range(ANCESTORS):
        try:
            comm = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
        except Exception:
            break
        if not comm:
            break
        chain.append((pid, comm))
        pid = _parent_of(pid)
        if pid <= 1:
            break
    return chain


def focus(
    name: str,
    *,
    attempts: int = 3,
    wait_s: float = 0.4,
    wake: Callable[[float], None] = time.sleep,
) -> str:
    """Make ``name`` frontmost, or raise.

    ``activate`` can appear to succeed while the frontmost application does not
    change: another window may hold focus, or the window server may not honour the
    request. Treating that as success and then measuring whatever happens to be in
    front produces numbers about the wrong application, silently. So this checks,
    retries, and then refuses with what it actually found.

    Measured, not hypothetical: a benchmark run asked for a browser, was told the
    activation succeeded, and then timed the terminal that was still in front.
    """
    if not name.strip():
        raise CannotDo("the application name is empty")
    wanted = name
    for _ in range(max(1, attempts)):
        current = frontmost()
        if current and current[0] == wanted:
            return current[0]
        wanted = activate(name)
        wake(wait_s)
    current = frontmost()
    if current and current[0] == wanted:
        return current[0]
    raise CannotDo(
        f"{wanted!r} did not become frontmost; it is {current[0] if current else 'unknown'}. "
        f"Anything measured now would be about the wrong application."
    )


def activate(name: str) -> str:
    """Bring a running application to the front. Returns its localised name.

    Raises with the list of candidates when nothing matches, because "app not
    found" without the alternatives is the least useful error in this domain.
    """
    wanted = name.strip().casefold()
    if not wanted:
        raise CannotDo("the application name is empty")

    apps = _regular()
    exact = [app for app in apps if _name(app).casefold() == wanted]
    matches = exact or [app for app in apps if wanted in _name(app).casefold()]
    if not matches:
        raise CannotDo(
            f"no running application matches {name!r}. Running: {sorted({_name(a) for a in apps})}"
        )

    app = matches[0]
    options = getattr(appkit(), "NSApplicationActivateIgnoringOtherApps", 1 << 1)
    activated = app.activateWithOptions_(options)
    if not activated and hasattr(app, "activate"):
        activated = bool(app.activate())
    if not activated:
        raise CannotDo(f"macOS refused to activate {_name(app)!r}")
    return _name(app)
