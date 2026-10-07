"""Blockly Phase 4（信頼性・運用）の検証。"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

import WindowUtils  # noqa: E402
from ui import blockly_editor  # noqa: E402


def test_url_copy_button_exists() -> None:
    """起動ダイアログにURLコピーの手段があること（開けない環境対策）。"""
    src = (
        Path(__file__).resolve().parent.parent
        / "SerialController"
        / "ui"
        / "blockly_editor.py"
    ).read_text(encoding="utf-8")
    assert "URLをコピー" in src


def make_app(base: Path) -> Path:
    """受け口検証用の最小アプリ構成を作る。"""
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    return app


def vision_code() -> str:
    """保存検証を通る最小の生成コードを返す."""
    return (
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "Phase4"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.wait(0.5)\n"
    )


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
    """実HTTP受け口を立てる。"""
    app = make_app(tmp_path)
    monkeypatch.setattr(WindowUtils, "APP_DIR", str(app))
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5.0)


def post_json(host: str, path: str, payload: dict) -> dict:
    """JSONをPOSTして応答を返す。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    assert resp.status == 200
    return json.loads(resp.read().decode("utf-8"))


def get_json(host: str, path: str) -> dict:
    """GETしてJSON応答を返す。"""
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    assert resp.status == 200
    return json.loads(resp.read().decode("utf-8"))


def test_load_flags_external_py_edit(server: str) -> None:
    """.pyだけ新しいときは手編集の可能性を知らせること。"""
    ws = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})
    saved = post_json(
        server,
        "/save",
        {"stem": "Phase4", "workspaceJson": ws, "pythonCode": vision_code()},
    )
    assert saved["ok"] is True
    app = Path(WindowUtils.APP_DIR)
    py_path = app / "Commands" / "PythonCommands" / "Phase4.py"
    json_path = app / "Commands" / "PythonCommands" / "Phase4.blockly.json"
    fresh = get_json(server, "/load?stem=Phase4")
    assert fresh["ok"] is True
    assert fresh.get("externalEdit", False) is False
    # 更新時刻だけ新しくしても（git取り出し相当）手編集とはみなさない。
    stat = json_path.stat()
    os.utime(
        py_path, ns=(stat.st_atime_ns + 2_000_000_000, stat.st_mtime_ns + 2_000_000_000)
    )
    touched = get_json(server, "/load?stem=Phase4")
    assert touched.get("externalEdit", False) is False
    # .pyの中身を書き換えたら知らせる。
    py_path.write_text(vision_code() + "        self.wait(1)\n", encoding="utf-8")
    flagged = get_json(server, "/load?stem=Phase4")
    assert flagged["ok"] is True
    assert flagged.get("externalEdit", False) is True
