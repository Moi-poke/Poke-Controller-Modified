"""ライブ編集のHTTP受け口（/live/*）の検証。

本物の受け口を一時ポートで立て、ブラウザの代役が `/live/next` を引いて
`/live/result` を返すところまで通す。Blockly の生成は node を使う。
"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import WindowUtils
import cv2
import numpy as np
import pytest
from blockly_node import NEEDS_NODE
from core import blockly_headless
from services import blockly_author
from ui import blockly_editor

pytestmark = NEEDS_NODE

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"


def _app(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    shutil.copytree(BLOCKLY, app / "assets" / "blockly")
    template = app / "Template" / "controller_check"
    template.mkdir(parents=True)
    image = np.full((20, 20, 3), 120, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", image)
    assert ok
    (template / "btn_a.png").write_bytes(bytes(buf))
    return app


def _workspace(app: Path) -> dict[str, Any]:
    catalog = blockly_headless.catalog(app)
    assert catalog.ok, catalog.errors
    built = blockly_author.build_workspace(
        {
            "name": "live",
            "do": [{"wait": 1}, {"press": "A", "duration": 0.02, "wait": 0}],
        },
        catalog.data,
    )
    assert built.ok, built.errors
    assert built.workspace is not None
    workspace = built.workspace
    blocks: list[dict[str, Any]] = []
    stack = list(workspace["blocks"]["blocks"])
    while stack:
        block = stack.pop()
        if not isinstance(block, dict):
            continue
        blocks.append(block)
        for slot in (block.get("inputs") or {}).values():
            if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                stack.append(slot["block"])
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            stack.append(nxt["block"])
    for index, block in enumerate(blocks):
        block["id"] = f"live{index}"
    return workspace


def _empty_workspace(app: Path) -> dict[str, Any]:
    catalog = blockly_headless.catalog(app)
    assert catalog.ok, catalog.errors
    built = blockly_author.build_workspace(
        {"name": "live struct", "do": []}, catalog.data
    )
    assert built.ok, built.errors
    assert built.workspace is not None
    workspace = built.workspace
    blocks: list[dict[str, Any]] = []
    stack = list(workspace["blocks"]["blocks"])
    while stack:
        block = stack.pop()
        if not isinstance(block, dict):
            continue
        blocks.append(block)
        for slot in (block.get("inputs") or {}).values():
            if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                stack.append(slot["block"])
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            stack.append(nxt["block"])
    for index, block in enumerate(blocks):
        block["id"] = f"empty{index}"
    return workspace


def _block(workspace: dict[str, Any], block_id: str) -> dict[str, Any]:
    stack = list(workspace["blocks"]["blocks"])
    while stack:
        block = stack.pop()
        if not isinstance(block, dict):
            continue
        if block.get("id") == block_id:
            return block
        for slot in (block.get("inputs") or {}).values():
            if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                stack.append(slot["block"])
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            stack.append(nxt["block"])
    raise AssertionError(f"block not found: {block_id}")


def _wait_block(workspace: dict[str, Any]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    stack = list(workspace["blocks"]["blocks"])
    while stack:
        block = stack.pop()
        if not isinstance(block, dict):
            continue
        blocks.append(block)
        for slot in (block.get("inputs") or {}).values():
            if isinstance(slot, dict) and isinstance(slot.get("block"), dict):
                stack.append(slot["block"])
        nxt = block.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("block"), dict):
            stack.append(nxt["block"])
    return next(b for b in blocks if b.get("type") == "pokecon_wait")


@pytest.fixture()
def live_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, Path]]:
    app = _app(tmp_path)
    monkeypatch.setattr(WindowUtils, "APP_DIR", str(app))
    blockly_editor._LIVE.reset()
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{httpd.server_address[1]}", app
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5.0)
        blockly_editor._LIVE.reset()


def post(host: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    conn = http.client.HTTPConnection(host, timeout=20)
    conn.request(
        "POST",
        path,
        body=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": f"http://{host}",
        },
    )
    resp = conn.getresponse()
    value: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    return value


def get(host: str, path: str) -> dict[str, Any]:
    conn = http.client.HTTPConnection(host, timeout=20)
    conn.request("GET", path)
    resp = conn.getresponse()
    value: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    return value


def _snapshot(
    host: str,
    client: str,
    workspace: dict[str, Any],
    revision: str,
    dirty: bool = False,
) -> dict[str, Any]:
    return post(
        host,
        "/live/snapshot",
        {
            "client": client,
            "revision": revision,
            "workspace": workspace,
            "dirty": dirty,
        },
    )


def _wait_request(host: str, client: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = get(host, f"/live/next?client={client}")
        if payload.get("ok") and payload.get("request"):
            return payload["request"]
        time.sleep(0.02)
    raise AssertionError("ライブ要求が届きませんでした")


def test_live_patch_flow(live_server: tuple[str, Path]) -> None:
    host, _ = live_server
    workspace = _workspace(Path(WindowUtils.APP_DIR))
    assert _snapshot(host, "browser", workspace, "rev1")["leased"] is True
    assert get(host, "/live/next?client=browser")["request"] is None
    result: dict[str, Any] = {}

    def run_patch() -> None:
        result.update(
            post(
                host,
                "/live/patch",
                {
                    "operations": {
                        "operations": [
                            {
                                "op": "set_field",
                                "selector": {"type": "pokecon_wait"},
                                "field": "SEC",
                                "value": 2.5,
                            }
                        ]
                    },
                    "baseRevision": "rev1",
                },
            )
        )

    thread = threading.Thread(target=run_patch)
    thread.start()
    request = _wait_request(host, "browser")
    assert request["baseRevision"] == "rev1"
    assert len(request["ops"]) == 1
    op = request["ops"][0]
    block = _wait_block(workspace)
    assert op["blockId"] == block["id"]
    assert op["value"] == 2.5
    block["fields"]["SEC"] = 2.5
    done = post(
        host,
        "/live/result",
        {
            "client": "browser",
            "id": request["id"],
            "ok": True,
            "status": "applied",
            "revision": "rev2",
            "workspace": workspace,
        },
    )
    assert done["ok"] is True
    thread.join(timeout=5)
    assert result["ok"] is True
    assert result["status"] == "applied"
    assert result["revision"] == "rev2"
    state = get(host, "/live/state")
    assert state["revision"] == "rev2"
    assert _wait_block(state["workspace"])["fields"]["SEC"] == 2.5


def test_live_patch_conflict_and_unavailable(live_server: tuple[str, Path]) -> None:
    host, _ = live_server
    workspace = _workspace(Path(WindowUtils.APP_DIR))
    _snapshot(host, "browser", workspace, "rev2")
    stale = post(
        host,
        "/live/patch",
        {"operations": {"operations": []}, "baseRevision": "rev1"},
    )
    assert stale["ok"] is False
    assert stale["status"] == "conflict"
    assert stale["revision"] == "rev2"

    blockly_editor._LIVE.reset()
    missing = post(
        host,
        "/live/patch",
        {"operations": {"operations": []}, "baseRevision": "rev2"},
    )
    assert missing["ok"] is False
    assert missing["status"] == "unavailable"


def test_live_structural_patch_uses_load_mode(
    live_server: tuple[str, Path],
) -> None:
    host, _ = live_server
    workspace = _empty_workspace(Path(WindowUtils.APP_DIR))
    program = _block(workspace, "empty0")
    assert _snapshot(host, "browser", workspace, "rev1")["leased"] is True
    result: dict[str, Any] = {}

    def run_patch() -> None:
        result.update(
            post(
                host,
                "/live/patch",
                {
                    "operations": {
                        "operations": [
                            {
                                "op": "create_block",
                                "type": "pokecon_press",
                                "id": "newpress",
                                "fields": {"BUTTON": "A"},
                                "parent": {"blockId": program["id"]},
                                "input": "DO",
                            }
                        ]
                    },
                    "baseRevision": "rev1",
                },
            )
        )

    thread = threading.Thread(target=run_patch)
    thread.start()
    request = _wait_request(host, "browser")
    assert request["mode"] == "load"
    patched = request["workspace"]
    child = patched["blocks"]["blocks"][0]["inputs"]["DO"]["block"]
    assert child["id"] == "newpress"
    assert child["type"] == "pokecon_press"
    post(
        host,
        "/live/result",
        {
            "client": "browser",
            "id": request["id"],
            "ok": True,
            "status": "applied",
            "revision": "rev2",
            "workspace": patched,
        },
    )
    thread.join(timeout=5)
    assert result["ok"] is True
    assert result["status"] == "applied"
    assert "self.press(Button.A" in result["code"]


def test_live_cli_status_and_patch(
    live_server: tuple[str, Path], tmp_path: Path
) -> None:
    host, _ = live_server
    workspace = _workspace(Path(WindowUtils.APP_DIR))
    _snapshot(host, "browser", workspace, "rev1")
    status = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "blockly_agent.py"),
            "live",
            "status",
            "--server",
            f"http://{host}",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert status.returncode == 0, status.stderr
    state = json.loads(status.stdout)
    assert state["revision"] == "rev1"
    assert isinstance(state["workspace"], dict)

    ops_path = tmp_path / "live-ops.json"
    ops_path.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op": "set_field",
                        "selector": {"type": "pokecon_wait"},
                        "field": "SEC",
                        "value": 4,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tools" / "blockly_agent.py"),
            "live",
            "patch",
            "--server",
            f"http://{host}",
            "--ops",
            str(ops_path),
            "--base-revision",
            "rev1",
            "--json",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    request = _wait_request(host, "browser")
    block = _wait_block(workspace)
    block["fields"]["SEC"] = 4
    post(
        host,
        "/live/result",
        {
            "client": "browser",
            "id": request["id"],
            "ok": True,
            "status": "applied",
            "revision": "rev2",
            "workspace": workspace,
        },
    )
    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, stderr
    payload = json.loads(stdout)
    assert payload["status"] == "applied"
    assert payload["revision"] == "rev2"


def test_second_tab_does_not_lease(live_server: tuple[str, Path]) -> None:
    host, _ = live_server
    workspace = _workspace(Path(WindowUtils.APP_DIR))
    first = _snapshot(host, "tab-a", workspace, "rev1")
    second = _snapshot(host, "tab-b", workspace, "rev1")
    assert first["leased"] is True
    assert second["leased"] is False
    assert get(host, "/live/next?client=tab-b")["leased"] is False
