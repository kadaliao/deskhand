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


def _quartz() -> Any:
    try:
        import Quartz
    except ImportError as exc:  # pragma: no cover - macOS only
        raise CannotDo("pyobjc Quartz is unavailable; install the macos extra") from exc
    return Quartz


def _as_int(value: object) -> int:
    """An integer from a window-server dictionary, or 0 when it is not one."""
    return value if isinstance(value, int) else 0


def _window_server_front() -> tuple[str, int] | None:
    """Owner of the frontmost normal window, from a fresh window-server query.

    A query, not a subscription. ``NSWorkspace`` learns about activation from
    notifications delivered on a run loop, and a command line process has none, so it
    answers with whatever was in front when it was first asked.
    """
    try:
        quartz = _quartz()
        windows = (
            quartz.CGWindowListCopyWindowInfo(
                quartz.kCGWindowListOptionOnScreenOnly | quartz.kCGWindowListExcludeDesktopElements,
                quartz.kCGNullWindowID,
            )
            or []
        )
    except Exception:  # no window server, or no Quartz: fall back to NSWorkspace
        return None
    for window in windows:
        if _as_int(window.get(quartz.kCGWindowLayer)) != 0:  # panels, Dock, menu extras
            continue
        pid = _as_int(window.get(quartz.kCGWindowOwnerPID))
        if pid <= 0:
            continue
        return (str(window.get(quartz.kCGWindowOwnerName) or "?"), pid)
    return None


def frontmost() -> tuple[str, int] | None:
    """The application owning the frontmost window, as of *now*.

    Read from the window server rather than from
    ``NSWorkspace.frontmostApplication()``, which in a process with no run loop serves a
    snapshot taken at first access and never updates it. Measured: after ``open`` raised
    System Settings, ``NSWorkspace`` still named the application from five seconds earlier
    while the window server and System Events both named System Settings.

    That one stale read made every ``--focus`` fail on an activation that had succeeded,
    and made the error it printed name the wrong cause. The window server is also what
    decides which window is in front, so it is the thing being asked about.
    """
    live = _window_server_front()
    if live is not None:
        return live
    app = appkit().NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    # processIdentifier() is a pid_t, not a string: int() cannot raise on it.
    # ast-grep-ignore
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
    # The isdigit() guard is what the int() rule cannot see.
    # ast-grep-ignore
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
    # As in frontmost(): pid_t is already an int, so int() here cannot raise.
    # ast-grep-ignore
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


def _matching(name: str) -> list[Any]:
    """Running regular applications this name could mean, best match first.

    An exact name wins over a substring, and a substring never reaches a helper or an
    extension because ``_regular()`` has already dropped those.
    """
    wanted = name.strip().casefold()
    if not wanted:
        return []
    apps = _regular()
    exact = [app for app in apps if _name(app).casefold() == wanted]
    return exact or [app for app in apps if wanted in _name(app).casefold()]


def _mdfind(query: str, *, timeout_s: float = 10.0) -> str:
    """Spotlight's stdout for a query, or an empty string when it cannot answer."""
    try:
        done = subprocess.run(
            ["mdfind", query], capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _resolve_app_path(name: str) -> str:
    """The bundle on disk for the name a person would use, or an empty string.

    ``open -a`` matches the bundle name, not the localised display name: on a zh-Hans
    macOS it refuses ``系统设置`` while Spotlight resolves that exact string to
    ``/System/Applications/System Settings.app``. Resolving first and opening the path is
    what lets ``--focus`` start a closed application in a localised interface.
    """
    query = "kMDItemDisplayName == '{}' && kMDItemContentType == 'com.apple.application-bundle'"
    for line in _mdfind(query.format(name.replace("'", "''"))).splitlines():
        candidate = line.strip()
        if candidate.endswith(".app"):
            return candidate
    return ""


def spawn(argv: list[str], *, timeout_s: float = 15.0) -> bool:
    """Run a LaunchServices command. Returns whether it reported success."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _executable_running(path: str) -> bool:
    """Whether a bundle's executable is in the process table.

    Deliberately not ``NSWorkspace``. In a process with no run loop,
    ``runningApplications()`` serves a snapshot, so an application that appears *after*
    that process starts is never seen. Measured: ``open`` launched System Settings
    (``pgrep`` found it within a second) while ``NSWorkspace`` in the same process
    reported it absent for six seconds, and the run gave up on an application that was
    already up. The process table has no such memory.
    """
    probe = f"{path}/Contents/MacOS/"
    try:
        done = subprocess.run(
            ["pgrep", "-f", probe], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(done.stdout.strip())


def start(
    name: str,
    *,
    wake: Callable[[float], None] = time.sleep,
    tries: int = 24,
    opener: Callable[[list[str]], bool] = spawn,
    running: Callable[[str], bool] = _executable_running,
) -> str:
    """Start an application that is not running, and wait for it to exist.

    ``--focus`` used to refuse an application that was merely *closed*, and list the ones
    that were open. The diagnosis was right and the answer was useless: the fix is to
    open it, which macOS will do on request. Measured, on this project's own appearance
    task: System Settings had been closed, so ``--focus 系统设置`` could not run at all --
    twice -- while one ``open`` would have started it in a second.

    Returns the name the caller asked for, not a name read back from the system: the
    caller compares it against the frontmost application, and in a process with no run
    loop the system will not tell us what the new application is called. Whether it then
    reaches the front is still ``focus``'s question to answer, not this function's.
    """
    if not name.strip():
        raise CannotDo("the application name is empty")
    path = _resolve_app_path(name)
    argv = ["open", path] if path else ["open", "-a", name]
    if not opener(argv):
        raise CannotDo(f"could not start {name!r}: {' '.join(argv)} was refused")
    if not path:
        # Spotlight knows no bundle by that name, so there is nothing to identify the
        # process by and `open -a`'s own answer is all there is.
        return name.strip()
    # `open` returns as soon as LaunchServices accepts the request, so wait for the
    # process to exist before reporting success.
    for _ in range(max(1, tries)):
        wake(0.25)
        if running(path):
            return name.strip()
    raise CannotDo(f"{name!r} was started but never appeared in the process list")


def _is_front(name: str) -> bool:
    current = frontmost()
    return bool(current and current[0] == name)


def raise_window(name: str, *, opener: Callable[[list[str]], bool] = spawn) -> bool:
    """Ask LaunchServices to bring a *running* application to the front.

    This is the mechanism that works, and it was missing. ``activate`` uses
    ``NSRunningApplication.activateWithOptions_``, which on macOS 26 returns ``True`` and
    does nothing, and the cooperative ``activate()`` that replaced it is not exposed by
    pyobjc -- so for an application that was already running, this module never used a
    mechanism that could succeed, and then blamed macOS for ignoring a request.

    Measured with an *active* caller, which is what settled it: run from a frontmost
    Ghostty, ``--focus 系统设置`` still failed while the frontmost application was that
    same Ghostty. So the earlier explanation ("macOS ignores an inactive caller") was not
    the cause; the missing call was.
    """
    path = _resolve_app_path(name)
    return opener(["open", path]) if path else opener(["open", "-a", name])


def focus(
    name: str,
    *,
    attempts: int = 6,
    wait_s: float = 0.5,
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

    Measured again, worse, on macOS 26 with System Settings behind WeChat:
    ``NSRunningApplication.activateWithOptions_`` returned True while the frontmost
    application did not change, and the cooperative ``activate()`` that replaced it is not
    exposed by pyobjc at all -- so for an application that was already running there was
    no mechanism here that could succeed. The fix is to ask LaunchServices
    (:func:`raise_window`), which is what actually raises a window.

    The first explanation offered for this was wrong, and is left here as the lesson: "macOS
    ignores an activation request from an application that is not itself active" fitted
    every measurement taken from an inactive caller, and was disproved the moment the same
    failure appeared with an *active* one. Run from a frontmost Ghostty, the frontmost
    application was that same Ghostty, and the activation still did nothing.
    """
    if not name.strip():
        raise CannotDo("the application name is empty")
    wanted = name
    for _ in range(max(1, attempts)):
        current = frontmost()
        if current and current[0] == wanted:
            return current[0]
        # Activating a closed application cannot work -- there is no process to bring
        # forward -- so start it instead of refusing the run.
        wanted = activate(name) if _matching(name) else start(name)
        if not _is_front(wanted):
            # `activate` returned success and the frontmost application did not change,
            # which is its behaviour on this macOS. LaunchServices is the mechanism that
            # actually raises a window, so ask it before spending another attempt.
            raise_window(name)
        wake(wait_s)
    current = frontmost()
    if current and current[0] == wanted:
        return current[0]
    raise CannotDo(
        f"{wanted!r} did not become frontmost; it is {current[0] if current else 'unknown'}. "
        f"Anything measured now would be about the wrong application. "
        f"{_why_activation_cannot_work(wanted)}"
    )


def _why_activation_cannot_work(wanted: str) -> str:
    """The actionable half of a failed focus, and it depends on who was asking.

    Must never raise. It runs while building a refusal, and it asks about the process chain,
    which means AppKit -- and CI runs this suite where pyobjc does not exist. It did raise
    there: every focus failure was reported as "pyobjc AppKit is unavailable", replacing the
    explanation it was supposed to add. Caught by CI, not locally.
    """
    try:
        owner = responsible_app()
        asked_from_front = owner is not None and _is_front(owner[0])
    except Exception:  # no AppKit, or nothing to ask: the generic message is still true
        owner, asked_from_front = None, False
    who = f"{owner[0]!r}" if owner else "no application at all"
    if asked_from_front:
        # The caller was active and it still did not come forward, so do not blame the
        # caller: the activation call itself does not work here.
        return (
            f"The requesting application ({who}) is itself in front, so this is not macOS "
            f"refusing an inactive caller. Activation goes through LaunchServices here, "
            f"and that request was accepted without the window coming forward -- most "
            f"likely the window server declined it, or {wanted!r} has no window to raise."
        )
    return (
        f"macOS ignores an activation request from an application that is not itself "
        f"active, and this process belongs to {who}, which is not in front. Run deskhand "
        f"from a frontmost terminal window, or bring {wanted!r} forward yourself and "
        f"leave the machine undisturbed while the run works."
    )


def activate(name: str) -> str:
    """Bring a running application to the front. Returns its localised name.

    Raises with the list of candidates when nothing matches, because "app not
    found" without the alternatives is the least useful error in this domain.
    """
    wanted = name.strip().casefold()
    if not wanted:
        raise CannotDo("the application name is empty")

    matches = _matching(name)
    if not matches:
        raise CannotDo(
            f"no running application matches {name!r}. "
            f"Running: {sorted({_name(a) for a in _regular()})}"
        )

    app = matches[0]
    options = getattr(appkit(), "NSApplicationActivateIgnoringOtherApps", 1 << 1)
    # Both calls are requests, not evidence: `activateWithOptions_` returns True on
    # macOS 26 whether or not anything moves. The second one is the API that replaced
    # it, and pyobjc does not expose it yet -- the hasattr guard is what keeps this
    # honest rather than aspirational, so do not read it as belt and braces.
    activated = app.activateWithOptions_(options)
    if not activated and hasattr(app, "activate"):
        activated = bool(app.activate())
    if not activated:
        raise CannotDo(f"macOS refused to activate {_name(app)!r}")
    return _name(app)
