"""The console, over real HTTP, on the scripted desktop: what it serves, and what it refuses."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from deskhand.console.desk import DemoDesk
from deskhand.console.server import Console, demo_task_payload, make_server
from deskhand.json_io import task_file_from_dict


class Client:
    def __init__(self, port: int, token: str) -> None:
        self.port = port
        self.token = token

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        token: str | None = "",
        host: str | None = None,
    ) -> tuple[int, Any]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token is not None:
            headers["X-Deskhand-Token"] = token or self.token
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        kind = response.getheader("Content-Type") or ""
        return response.status, json.loads(raw) if "json" in kind else raw.decode("utf-8")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    console = Console(DemoDesk(), tmp_path)
    server = make_server(console, port=0)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield Client(server.server_address[1], console.token)
    finally:
        server.shutdown()
        server.server_close()


def wait_for_run(client: Client, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _, run = client.call("GET", f"/api/runs/{run_id}")
        if run["state"] != "running":
            return dict(run)
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


class TestWhoMayAsk:
    def test_the_page_carries_the_token_and_the_api_needs_it(self, client: Client) -> None:
        status, page = client.call("GET", "/", token=None)
        assert status == 200
        assert client.token in page
        assert client.call("GET", "/api/state", token=None)[0] == 401
        assert client.call("GET", "/api/state", token="wrong")[0] == 401
        assert client.call("GET", "/api/state")[0] == 200

    def test_a_foreign_host_is_refused_even_with_the_token(self, client: Client) -> None:
        """DNS rebinding: a page on evil.example resolving to 127.0.0.1."""
        status, _ = client.call("GET", "/api/state", host=f"evil.example:{client.port}")
        assert status == 403

    def test_a_report_page_needs_the_token_too(self, client: Client) -> None:
        assert client.call("GET", "/runs/anything.html", token=None)[0] == 401

    def test_static_files_cannot_escape_their_folder(self, client: Client) -> None:
        assert client.call("GET", "/static/../server.py", token=None)[0] == 404
        assert client.call("GET", "/static/app.js", token=None)[0] == 200


class TestLooking:
    def test_the_view_has_every_target_with_its_rectangle(self, client: Client) -> None:
        status, view = client.call("GET", "/api/view")
        assert status == 200
        assert view["app"] == "Clip Editor"
        effects = next(t for t in view["targets"] if t["id"] == "effects_button")
        assert effects["box"] == [40, 80, 90, 28]
        assert view["frame"] == [0, 40, 420, 260]

    def test_the_demo_has_no_screenshot(self, client: Client) -> None:
        assert client.call("GET", "/api/shot")[0] == 204


class TestTasks:
    def test_the_demo_task_is_a_valid_task_file(self) -> None:
        loaded = task_file_from_dict(demo_task_payload())
        assert loaded.expect and loaded.steps

    def test_save_list_load_delete(self, client: Client) -> None:
        payload = demo_task_payload()
        status, saved = client.call("POST", "/api/tasks", {"name": "blur on", "task": payload})
        assert status == 200 and saved["name"] == "blur-on"
        names = [t["name"] for t in client.call("GET", "/api/tasks")[1]]
        assert names == ["demo", "blur-on"]
        assert client.call("GET", "/api/tasks/blur-on")[1] == payload
        client.call("DELETE", "/api/tasks/blur-on")
        assert client.call("GET", "/api/tasks/blur-on")[0] == 404

    def test_a_task_that_run_would_refuse_is_not_saved(self, client: Client) -> None:
        bad = {"goal": "g", "checks": ["c"], "expect": {"other": {"label": "x"}}}
        status, reply = client.call("POST", "/api/tasks", {"name": "bad", "task": bad})
        assert status == 400
        assert "does not have" in reply["error"]

    def test_a_rehearsal_executes_nothing(self, client: Client) -> None:
        status, rehearsal = client.call("POST", "/api/rehearse", {"task": demo_task_payload()})
        assert status == 200
        assert rehearsal["blocked"] is False
        assert [f["later"] for f in rehearsal["findings"]] == [False, True, True, False]
        # Still on the first scene: nothing was pressed.
        assert "blur_button" not in {t["id"] for t in client.call("GET", "/api/view")[1]["targets"]}


class TestRunning:
    def test_a_run_needs_an_explicit_confirmation(self, client: Client) -> None:
        status, reply = client.call("POST", "/api/runs", {"task": demo_task_payload()})
        assert status == 400
        assert "confirm" in reply["error"]

    def test_a_run_finishes_verified_and_leaves_a_report(self, client: Client) -> None:
        status, started = client.call(
            "POST", "/api/runs", {"task": demo_task_payload(), "confirm": True}
        )
        assert status == 202
        run = wait_for_run(client, started["id"])
        assert run["state"] == "finished"
        assert run["report"]["status"] == "DONE"
        assert run["report"]["checks"][0]["ok"] is True
        assert [r["id"] for r in client.call("GET", "/api/runs")[1]] == [started["id"]]
        page_status, page = client.call("GET", f"/runs/{started['id']}.html")
        assert page_status == 200 and "Gaussian Blur" in page

    def test_a_task_with_no_steps_and_no_model_is_refused(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DESKHAND_MODEL_COMMAND", raising=False)
        task = {"goal": "g", "checks": ["c"]}
        status, reply = client.call("POST", "/api/runs", {"task": task, "confirm": True})
        assert status == 400
        assert "no steps" in reply["error"]
