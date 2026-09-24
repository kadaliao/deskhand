"""What the console asks of a desktop, and the two desktops that answer.

``MacDesk`` is this machine. ``DemoDesk`` is the scripted desktop ``deskhand demo`` runs
on: it needs no permission at all, so the console can be tried -- and tested -- anywhere.
The server only talks to this interface, which is also what a native shell around the
console would talk to.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from .. import demo
from ..model import ENV_COMMAND as MODEL_COMMAND_ENV
from ..protocols import Sensor
from ..sensors.fake import FakeSensor
from ..types import Box, View

PIXEL_MODES = {"auto": "auto", "on": True, "off": False}


@dataclass(frozen=True, slots=True)
class Shot:
    """A picture of one window, and where on screen it is."""

    data: bytes
    box: Box
    media: str = "image/jpeg"


class Desk(Protocol):
    kind: str
    """``"mac"`` or ``"demo"``: shown in the console so nobody mistakes one for the other."""

    def doctor(self) -> dict[str, Any]: ...

    def apps(self) -> list[dict[str, Any]]: ...

    def sensor(self, app: str | None, pixels: str) -> Sensor: ...

    def shot(self, app: str | None, window: str = "", box: Box | None = None) -> Shot | None: ...

    def focus(self, app: str) -> str: ...


def model_status() -> dict[str, Any]:
    command = os.environ.get(MODEL_COMMAND_ENV, "").strip()
    return {
        "configured": bool(command),
        "env": MODEL_COMMAND_ENV,
        # The first word only: the rest of a command line can carry a key.
        "program": command.split()[0] if command else None,
    }


class MacDesk:
    kind = "mac"

    def __init__(self) -> None:
        # One sensor per application and pixel mode, kept: a sensor remembers its last
        # recognition, so looking again at a window that has not changed reads nothing.
        self._sensors: dict[tuple[str, str], Sensor] = {}

    def doctor(self) -> dict[str, Any]:
        from ..sensors.macos.screen import permissions

        modules: dict[str, bool] = {}
        for name in ("ApplicationServices", "Quartz", "AppKit", "Vision"):
            try:
                __import__(name)
            except Exception:
                modules[name] = False
            else:
                modules[name] = True
        report: dict[str, Any] = {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "modules": modules,
            "permissions": {"accessibility": False, "screen_recording": False},
            "model": model_status(),
        }
        if all(modules.values()):
            report["permissions"] = permissions()
        return report

    def apps(self) -> list[dict[str, Any]]:
        from ..sensors.macos.apps import frontmost, on_screen

        front = frontmost()
        return [
            {"name": name, "pid": pid, "front": bool(front and front[1] == pid)}
            for name, pid in on_screen()
        ]

    def sensor(self, app: str | None, pixels: str) -> Sensor:
        from ..sensors.macos.screen import open_sensor

        key = (app or "", pixels)
        if key not in self._sensors:
            mode = PIXEL_MODES.get(pixels, "auto")
            self._sensors[key] = open_sensor(pixels=mode, app=app or None)  # type: ignore[arg-type]
        return self._sensors[key]

    def shot(self, app: str | None, window: str = "", box: Box | None = None) -> Shot | None:
        from ..sensors.macos.apps import find, frontmost
        from ..sensors.macos.ocr import screen_capture_allowed, snapshot

        if not screen_capture_allowed():
            return None
        found = find(app) if app else frontmost()
        if found is None:
            return None
        taken = snapshot(found[1], window, box=box)
        return None if taken is None else Shot(*taken)

    def focus(self, app: str) -> str:
        from ..sensors.macos.apps import focus

        return focus(app)


class DemoDesk:
    """The scripted desktop. One instance keeps its state, so a run visibly changes it."""

    kind = "demo"

    def __init__(self) -> None:
        self._sensor = demo.sensor()

    def reset(self) -> None:
        self._sensor = demo.sensor()

    @property
    def fake(self) -> FakeSensor:
        return self._sensor

    def doctor(self) -> dict[str, Any]:
        return {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "modules": {},
            "permissions": {"accessibility": True, "screen_recording": True},
            "model": model_status(),
            "demo": True,
        }

    def apps(self) -> list[dict[str, Any]]:
        view: View = self._sensor.observe()
        return [{"name": view.app, "pid": 0, "front": True}]

    def sensor(self, app: str | None, pixels: str) -> Sensor:
        del app, pixels
        return self._sensor

    def shot(self, app: str | None, window: str = "", box: Box | None = None) -> Shot | None:
        del app, window, box
        return None

    def focus(self, app: str) -> str:
        return app
