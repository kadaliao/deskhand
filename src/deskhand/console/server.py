"""The console's HTTP side: a small JSON API over one desktop, and the page that uses it.

Standard library only, bound to 127.0.0.1. Two things keep a web page elsewhere from
driving this desktop through the browser of the person running it:

- every ``/api`` and ``/runs`` request carries a per-launch token, which only the console's
  own page has (a cross-origin page can send a request but cannot read the page to learn
  the token, and a custom header forces a CORS preflight that is never answered);
- the ``Host`` header must be this server's own address, which defeats DNS rebinding.

Acting on the desktop additionally needs ``"confirm": true`` in the request, so a run is
never a side effect of loading something.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .. import demo, json_io
from ..deciders.llm import LLMDecider
from ..deciders.scripted import ScriptedDecider
from ..errors import DeskhandError, NoPermission
from ..model import model_from_env
from ..protocols import Decider, Verifier
from ..rehearse import rehearse
from ..report_html import render
from ..runner import Runner
from ..types import Box, Report, View
from ..verify import ExpectVerifier, ModelVerifier
from .desk import DemoDesk, Desk, MacDesk

MAX_BODY = 1_000_000
MODEL_STEP_MS = 35_000
STATIC = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


# --------------------------------------------------------------------------- #
# payloads
# --------------------------------------------------------------------------- #


def view_payload(view: View) -> dict[str, Any]:
    """Everything the page draws: every target with its rectangle, disabled ones included.

    ``View.brief`` is the decider's form and drops rectangles on purpose; a person
    looking at the window needs them, and needs to see the disabled controls too.
    """
    frame = view.frame
    targets = []
    for target in view.targets:
        item = target.brief(frame=frame)
        item["source"] = target.source
        item["enabled"] = target.enabled
        if target.box is not None:
            item["box"] = [target.box.x, target.box.y, target.box.w, target.box.h]
        targets.append(item)
    return {
        "app": view.app,
        "window": view.window,
        "frame": None if frame is None else [frame.x, frame.y, frame.w, frame.h],
        "targets": targets,
        "notes": json.loads(json_io.dump(dict(view.notes))),
    }


def demo_task_payload() -> dict[str, Any]:
    """The demo run as a task file, so the console has something to start from."""
    task = demo.task()
    script = demo.script()
    steps = [script.choose(task=task, view=demo.sensor().observe(), steps=()).brief()]
    while script.remaining:
        steps.append(script.choose(task=task, view=demo.sensor().observe(), steps=()).brief())
    return {
        "goal": task.goal,
        "checks": list(task.checks),
        "expect": {task.checks[0]: {"label": "Enabled", "value": "on"}},
        "notes": list(task.notes),
        "steps": steps[1:],  # the demo's deliberate wrong first step is for the CLI
    }


def _slug(name: str) -> str:
    kept = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip())
    kept = "-".join(part for part in kept.split("-") if part)
    if not kept:
        raise ValueError("a task needs a name")
    return kept[:80]


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #


@dataclass
class RunRecord:
    id: str
    app: str | None
    task: dict[str, Any]
    started: str
    state: str = "running"
    steps: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] | None = None
    error: str | None = None
    notice: str | None = None
    runner: Runner | None = None

    def brief(self, *, full: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "app": self.app,
            "goal": self.task.get("goal"),
            "started": self.started,
            "state": self.state,
            "status": None if self.report is None else self.report.get("status"),
            "steps_taken": len(self.steps),
            "error": self.error,
            "notice": self.notice,
        }
        if full:
            out["task"] = self.task
            out["steps"] = self.steps
            out["report"] = self.report
        return out


class Console:
    """One desktop, its saved tasks, and its runs. Thread-safe where it has to be."""

    def __init__(self, desk: Desk, home: Path) -> None:
        self.desk = desk
        self.home = home
        self.tasks_dir = home / "tasks"
        self.runs_dir = home / "runs"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(24)
        # Desktop access is serialised: one observation, rehearsal or run step at a time,
        # so a look taken from the page never interleaves with a run's act and settle.
        self.lock = threading.RLock()
        self.runs: dict[str, RunRecord] = {}

    # ------------------------------------------------------------------ state

    def state(self) -> dict[str, Any]:
        return {
            "desk": self.desk.kind,
            "home": str(self.home),
            "doctor": self.desk.doctor(),
            "active": next((r.id for r in self.runs.values() if r.state == "running"), None),
        }

    def apps(self) -> list[dict[str, Any]]:
        return self.desk.apps()

    def observe(self, app: str | None, pixels: str) -> dict[str, Any]:
        with self.lock:
            started = time.perf_counter()
            view = self.desk.sensor(app, pixels).observe()
        payload = view_payload(view)
        payload["ms"] = round((time.perf_counter() - started) * 1000)
        return payload

    def shot(
        self, app: str | None, window: str = "", frame: str = ""
    ) -> tuple[bytes, str, list[float]] | None:
        """A picture of the window a view described: same title, same rectangle."""
        box = None
        parts = frame.split(",")
        if len(parts) == 4:  # noqa: PLR2004 - x,y,w,h
            try:
                box = Box(*(float(part) for part in parts))
            except ValueError:
                box = None
        with self.lock:
            taken = self.desk.shot(app, window, box)
        if taken is None:
            return None
        return taken.data, taken.media, [taken.box.x, taken.box.y, taken.box.w, taken.box.h]

    # ------------------------------------------------------------------ tasks

    def tasks(self) -> list[dict[str, Any]]:
        found = []
        if self.desk.kind == "demo":
            found.append(self._task_row("demo", demo_task_payload(), builtin=True))
        for path in sorted(self.tasks_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            found.append(self._task_row(path.stem, payload, updated=path.stat().st_mtime))
        return found

    @staticmethod
    def _task_row(
        name: str, payload: dict[str, Any], *, builtin: bool = False, updated: float = 0
    ) -> dict[str, Any]:
        return {
            "name": name,
            "goal": payload.get("goal", ""),
            "checks": len(payload.get("checks") or []),
            "steps": len(payload.get("steps") or []),
            "builtin": builtin,
            "updated": updated,
        }

    def task(self, name: str) -> dict[str, Any]:
        if name == "demo" and self.desk.kind == "demo":
            return demo_task_payload()
        path = self.tasks_dir / f"{_slug(name)}.json"
        if not path.exists():
            raise KeyError(f"no saved task called {name!r}")
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def save_task(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        json_io.task_file_from_dict(payload)  # refuse what `run --task` would refuse
        slug = _slug(name)
        path = self.tasks_dir / f"{slug}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return {"name": slug, "path": str(path)}

    def delete_task(self, name: str) -> dict[str, Any]:
        path = self.tasks_dir / f"{_slug(name)}.json"
        path.unlink(missing_ok=True)
        return {"deleted": path.stem}

    # ------------------------------------------------------------ deciding

    def _plan(
        self, payload: dict[str, Any], *, use_model: bool
    ) -> tuple[json_io.TaskFile, Decider, Verifier | None, str | None]:
        loaded = json_io.task_file_from_dict(payload)
        task, notice = loaded.task, None
        verifier: Verifier | None = None
        decider: Decider
        if loaded.steps and not use_model:
            decider = ScriptedDecider(loaded.steps)
        elif use_model:
            limits = task.limits
            floor = limits.max_steps * MODEL_STEP_MS
            if limits.max_ms is not None and limits.max_ms < floor:
                notice = f"raised max_ms {limits.max_ms} -> {floor}: a model decision takes 9-33 s"
                task = replace(task, limits=replace(limits, max_ms=floor))
            decider = LLMDecider(model_from_env())
            verifier = ModelVerifier(model_from_env())
        else:
            raise ValueError("this task has no steps: add steps, or let a model decide")
        if loaded.expect:
            verifier = ExpectVerifier(loaded.expect, fallback=verifier)
        return json_io.TaskFile(task, loaded.steps, loaded.expect), decider, verifier, notice

    def rehearse(self, payload: dict[str, Any], app: str | None, pixels: str) -> dict[str, Any]:
        loaded, _, verifier, _ = self._plan(payload, use_model=False)
        with self.lock:
            view = self.desk.sensor(app, pixels).observe()
        result = rehearse(loaded.task, loaded.steps, view, verifier=verifier)
        return {**result.brief(), "blocked": result.first_blocked}

    # ------------------------------------------------------------------- runs

    def start_run(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("confirm") is not True:
            raise ValueError('a run acts on the desktop: it needs "confirm": true')
        if any(r.state == "running" for r in self.runs.values()):
            raise ValueError("another run is still going; stop it or wait for it")
        payload = body.get("task")
        if not isinstance(payload, dict):
            raise ValueError("'task' must be a task object")
        app = body.get("app") or None
        pixels = str(body.get("pixels") or "auto")
        loaded, decider, verifier, notice = self._plan(payload, use_model=bool(body.get("model")))
        record = RunRecord(
            id=datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2),
            app=app,
            task=payload,
            started=datetime.now().isoformat(timespec="seconds"),
            notice=notice,
        )
        self.runs[record.id] = record
        worker = threading.Thread(
            target=self._execute,
            kwargs={
                "record": record,
                "loaded": loaded,
                "decider": decider,
                "verifier": verifier,
                "pixels": pixels,
                "focus": bool(body.get("focus")),
            },
            name=f"deskhand-run-{record.id}",
            daemon=True,
        )
        worker.start()
        return record.brief(full=False)

    def _execute(
        self,
        *,
        record: RunRecord,
        loaded: json_io.TaskFile,
        decider: Decider,
        verifier: Verifier | None,
        pixels: str,
        focus: bool,
    ) -> None:
        try:
            with self.lock:
                if focus and record.app:
                    self.desk.focus(record.app)
                sensor = self.desk.sensor(record.app, pixels)
            runner = Runner(sensor=sensor, decider=decider, verifier=verifier)
            record.runner = runner
            stream = runner.steps(loaded.task)
            report: Report | None = None
            while report is None:
                with self.lock:
                    try:
                        step = next(stream)
                    except StopIteration as stop:
                        report = stop.value
                        break
                record.steps.append(step.brief())
            record.report = report.brief()
            record.state = "finished"
        except Exception as exc:  # the run is over either way; say how
            record.error = f"{type(exc).__name__}: {exc}"
            record.state = "failed"
            traceback.print_exc()
        finally:
            record.runner = None
            self._persist(record)

    def _persist(self, record: RunRecord) -> None:
        stored = record.brief()
        (self.runs_dir / f"{record.id}.json").write_text(
            json.dumps(stored, ensure_ascii=False, indent=2, default=str), "utf-8"
        )
        if record.report is not None:
            (self.runs_dir / f"{record.id}.html").write_text(render(record.report), "utf-8")

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        record = self.runs.get(run_id)
        if record is None:
            raise KeyError(f"no run {run_id!r} in progress")
        if record.runner is not None:
            record.runner.cancel()
        return record.brief(full=False)

    def list_runs(self) -> list[dict[str, Any]]:
        rows = {r.id: r.brief(full=False) for r in self.runs.values()}
        for path in self.runs_dir.glob("*.json"):
            if path.stem in rows:
                continue
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for heavy in ("task", "steps", "report"):
                stored.pop(heavy, None)
            rows[path.stem] = stored
        return sorted(rows.values(), key=lambda r: str(r.get("id")), reverse=True)

    def run(self, run_id: str) -> dict[str, Any]:
        record = self.runs.get(run_id)
        if record is not None:
            return record.brief()
        path = self.runs_dir / f"{_slug(run_id)}.json"
        if not path.exists():
            raise KeyError(f"no run {run_id!r}")
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def report_page(self, run_id: str) -> str:
        path = self.runs_dir / f"{_slug(run_id)}.html"
        if not path.exists():
            raise KeyError(f"run {run_id!r} has no report yet")
        return path.read_text(encoding="utf-8")

    def reset_demo(self) -> dict[str, Any]:
        if not isinstance(self.desk, DemoDesk):
            raise ValueError("only the demo desktop can be reset")
        with self.lock:
            self.desk.reset()
        return {"reset": True}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    console: Console
    hosts: frozenset[str]
    server_version = "deskhand"

    def log_message(self, format: str, *args: Any) -> None:  # quiet: the console is the UI
        del format, args

    # ----------------------------------------------------------------- guards

    def _allowed_host(self) -> bool:
        return self.headers.get("Host", "") in self.hosts

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        given = self.headers.get("X-Deskhand-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(given, self.console.token)

    # --------------------------------------------------------------- replies

    def _send(self, status: int, body: bytes, media: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"request body is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    # ---------------------------------------------------------------- routing

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def _route(self, method: str) -> None:
        if not self._allowed_host():
            self._error(HTTPStatus.FORBIDDEN, "wrong Host header")
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        path = url.path
        try:
            if method == "GET" and path in {"/", "/index.html"}:
                self._page()
                return
            if method == "GET" and path.startswith("/static/"):
                self._static(path.removeprefix("/static/"))
                return
            if not (path.startswith("/api/") or path.startswith("/runs/")):
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self._authorised(query):
                self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong console token")
                return
            self._api(method, path, query)
        except NoPermission as exc:
            self._error(HTTPStatus.FORBIDDEN, str(exc))
        except KeyError as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc.args[0] if exc.args else exc))
        except (DeskhandError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def _page(self) -> None:
        page = _asset("index.html").decode("utf-8")
        page = page.replace("{{TOKEN}}", self.console.token).replace(
            "{{DESK}}", self.console.desk.kind
        )
        self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, name: str) -> None:
        suffix = Path(name).suffix
        if "/" in name or suffix not in STATIC:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        self._send(HTTPStatus.OK, _asset(name), f"{STATIC[suffix]}; charset=utf-8")

    def _api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        console = self.console
        app = (query.get("app") or [""])[0] or None
        pixels = (query.get("pixels") or ["auto"])[0]
        parts = [p for p in path.split("/") if p]
        section, *rest = parts
        area = rest[0] if rest else ""
        name = rest[1] if len(rest) > 1 else None
        verb = rest[2] if len(rest) > 2 else None  # noqa: PLR2004 - the path's third part

        if section == "runs":
            if method != "GET" or not area:
                raise KeyError("not found")
            page = console.report_page(area.removesuffix(".html"))
            self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
            return

        route = (method, area)
        if route == ("GET", "state"):
            self._json(console.state())
        elif route == ("GET", "apps"):
            self._json(console.apps())
        elif route == ("GET", "view"):
            self._json(console.observe(app, pixels))
        elif route == ("GET", "shot"):
            window = (query.get("window") or [""])[0]
            self._shot(app, window, (query.get("frame") or [""])[0])
        elif route == ("GET", "tasks"):
            self._json(console.task(name) if name else console.tasks())
        elif route == ("POST", "tasks"):
            body = self._body()
            self._json(console.save_task(str(body.get("name") or ""), body.get("task") or {}))
        elif route == ("DELETE", "tasks") and name:
            self._json(console.delete_task(name))
        elif route == ("POST", "rehearse"):
            body = self._body()
            task = body.get("task") or {}
            self._json(
                console.rehearse(task, body.get("app") or None, str(body.get("pixels") or "auto"))
            )
        elif route == ("GET", "runs"):
            self._json(console.run(name) if name else console.list_runs())
        elif route == ("POST", "runs") and name and verb == "cancel":
            self._json(console.cancel_run(name))
        elif route == ("POST", "runs") and not name:
            self._json(console.start_run(self._body()), HTTPStatus.ACCEPTED)
        elif route == ("POST", "demo-reset"):
            self._json(console.reset_demo())
        else:
            raise KeyError(f"no route for {method} {path}")

    def _shot(self, app: str | None, window: str, frame: str) -> None:
        taken = self.console.shot(app, window, frame)
        if taken is None:
            self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")
            return
        data, media, box = taken
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Deskhand-Box", json.dumps(box))
        self.end_headers()
        self.wfile.write(data)


def _asset(name: str) -> bytes:
    return (resources.files("deskhand.console") / "static" / name).read_bytes()


def make_server(console: Console, *, port: int = 0) -> ThreadingHTTPServer:
    """Bind to 127.0.0.1 only. ``port=0`` picks a free one."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    bound = server.server_address[1]
    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "console": console,
            "hosts": frozenset({f"127.0.0.1:{bound}", f"localhost:{bound}"}),
        },
    )
    server.RequestHandlerClass = handler
    return server


def open_console(*, demo_desk: bool, home: Path) -> Console:
    desk: Desk = DemoDesk() if demo_desk else MacDesk()
    return Console(desk, home / "demo" if demo_desk else home)
