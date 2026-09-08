"""blockly_editorの配信受け口（/templates・/save）の検証。実HTTPで叩く。"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

import WindowUtils  # noqa: E402
from ui import blockly_editor  # noqa: E402


def make_app(base: Path) -> Path:
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    (app / "Template" / "my-pack").mkdir(parents=True)
    (app / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return app


def vision_code(tpl: str = "my-pack/a.png") -> str:
    return (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        f'        self.waitTemplate("{tpl}", timeout=10.0, threshold=0.7)\n'
    )


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[str]:
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


def get_json(host: str, path: str) -> dict:
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    assert resp.status == 200
    return json.loads(resp.read().decode("utf-8"))


def post_json(host: str, path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    assert resp.status == 200
    return json.loads(resp.read().decode("utf-8"))


def test_templates_lists_images(server: str) -> None:
    data = get_json(server, "/templates")
    assert data["ok"] is True
    assert data["templates"] == ["my-pack/a.png"]


def test_save_vision_ok_with_warnings(server: str) -> None:
    ws = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})
    data = post_json(
        server,
        "/save",
        {"stem": "VisionOk", "workspaceJson": ws, "pythonCode": vision_code("a.png")},
    )
    assert data["ok"] is True
    assert isinstance(data.get("warnings"), list)
    assert len(data["warnings"]) == 1


def test_save_dotdot_fails(server: str) -> None:
    ws = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})
    data = post_json(
        server,
        "/save",
        {
            "stem": "VisionNg",
            "workspaceJson": ws,
            "pythonCode": vision_code("../evil.png"),
        },
    )
    assert data["ok"] is False
