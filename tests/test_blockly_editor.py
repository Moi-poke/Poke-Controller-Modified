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
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: make_png())
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5.0)


def make_png(width: int = 200, height: int = 100) -> bytes:
    import cv2
    import numpy as np

    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (60, 120, 200)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def get_bytes(host: str, path: str) -> tuple[int, str, bytes]:
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, resp.getheader("Content-Type", ""), resp.read()


def test_frame_returns_png(server: str) -> None:
    status, ctype, body = get_bytes(server, "/frame")
    assert status == 200
    assert "image/png" in ctype
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_frame_no_camera_fails(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", None)
    data = get_json(server, "/frame")
    assert data["ok"] is False


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


def test_template_saves_and_lists(server: str) -> None:
    data = post_json(
        server,
        "/template",
        {"name": "mark", "rect": {"x": 0, "y": 0, "width": 1, "height": 1}},
    )
    assert data["ok"] is True
    assert data["path"] == "blockly/mark.png"
    listed = get_json(server, "/templates")
    assert "blockly/mark.png" in listed["templates"]


def test_template_bad_name_fails(server: str) -> None:
    data = post_json(
        server,
        "/template",
        {"name": "../evil", "rect": {"x": 0, "y": 0, "width": 1, "height": 1}},
    )
    assert data["ok"] is False


def test_template_bad_rect_fails(server: str) -> None:
    data = post_json(
        server,
        "/template",
        {"name": "mark", "rect": {"x": 0, "y": 0, "width": 0, "height": 1}},
    )
    assert data["ok"] is False


def test_template_no_camera_fails(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", None)
    data = post_json(
        server,
        "/template",
        {"name": "mark", "rect": {"x": 0, "y": 0, "width": 1, "height": 1}},
    )
    assert data["ok"] is False


def make_match_frame() -> bytes:
    import cv2
    import numpy as np

    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:] = (60, 60, 60)
    img[30:70, 60:140] = (255, 255, 255)
    # TM_CCOEFF_NORMEDは分散ゼロ（真っ白など）のテンプレだと全面1.0になり
    # 位置も判定できないため、内側に黒の目印を入れる（Task 1のfixtureと同一条件）。
    # 切出し位置・大きさ（60,30,80x40）は変えない。
    img[40:60, 80:120] = (0, 0, 0)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def test_match_frame_hit(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv2
    import numpy as np
    from services import blockly_match

    frame = make_match_frame()
    arr = np.frombuffer(frame, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    tpl = img[30:70, 60:140]
    ok, buf = cv2.imencode(".png", tpl)
    assert ok
    app = Path(WindowUtils.APP_DIR)
    dest = app / "Template" / "pack"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "part.png").write_bytes(bytes(buf))
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is True
    assert data["matched"] is True
    assert data["rect"] == {"x": 60, "y": 30, "width": 80, "height": 40}
    assert blockly_match.parse_threshold(data["score"]) >= 0.99
    assert data["score"] >= 0.99


def test_match_upload_hit(server: str) -> None:
    import base64

    import cv2
    import numpy as np

    frame = make_match_frame()
    arr = np.frombuffer(frame, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    ok, buf = cv2.imencode(".png", img[30:70, 60:140])
    assert ok
    app = Path(WindowUtils.APP_DIR)
    dest = app / "Template" / "pack"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "part.png").write_bytes(bytes(buf))
    data = post_json(
        server,
        "/match",
        {
            "template": "pack/part.png",
            "threshold": 0.7,
            "source": "upload",
            "image": base64.b64encode(frame).decode(),
        },
    )
    assert data["ok"] is True
    assert data["matched"] is True


def test_match_missing_template_fails(server: str) -> None:
    data = post_json(
        server,
        "/match",
        {"template": "pack/none.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is False


def test_match_bad_threshold_fails(server: str) -> None:
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": "high", "source": "frame"},
    )
    assert data["ok"] is False


def test_match_no_camera_fails(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", None)
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is False


def test_match_crop_hit(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv2
    import numpy as np

    frame = make_match_frame()
    arr = np.frombuffer(frame, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    ok, buf = cv2.imencode(".png", img[30:70, 60:140])
    assert ok
    app = Path(WindowUtils.APP_DIR)
    dest = app / "Template" / "pack"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "part.png").write_bytes(bytes(buf))
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/match",
        {
            "template": "pack/part.png",
            "threshold": 0.7,
            "source": "frame",
            "crop": [40, 10, 180, 90],
        },
    )
    assert data["ok"] is True
    assert data["rect"] == {"x": 60, "y": 30, "width": 80, "height": 40}
    assert data["count"] == 1


def test_match_bad_crop_fails(server: str) -> None:
    data = post_json(
        server,
        "/match",
        {
            "template": "pack/part.png",
            "threshold": 0.7,
            "source": "frame",
            "crop": [180, 10, 40, 90],
        },
    )
    assert data["ok"] is False


def test_match_response_has_count(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import cv2
    import numpy as np

    frame = make_match_frame()
    arr = np.frombuffer(frame, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    assert img is not None
    ok, buf = cv2.imencode(".png", img[30:70, 60:140])
    assert ok
    app = Path(WindowUtils.APP_DIR)
    dest = app / "Template" / "pack"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "part.png").write_bytes(bytes(buf))
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/match",
        {"template": "pack/part.png", "threshold": 0.7, "source": "frame"},
    )
    assert data["ok"] is True
    assert data["count"] >= 1


def test_template_image_returns_bytes(server: str) -> None:
    status, ctype, body = get_bytes(server, "/template_image?name=my-pack/a.png")
    assert status == 200
    assert "image/png" in ctype
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_template_image_missing_fails(server: str) -> None:
    status, ctype, body = get_bytes(server, "/template_image?name=pack/none.png")
    assert status == 200
    assert "application/json" in ctype
    assert json.loads(body.decode("utf-8"))["ok"] is False


def test_template_image_traversal_fails(server: str) -> None:
    status, ctype, body = get_bytes(server, "/template_image?name=../evil.png")
    assert status == 200
    assert "application/json" in ctype
    assert json.loads(body.decode("utf-8"))["ok"] is False


def _post_raw(host: str, raw: bytes) -> bytes:
    import socket

    h, port = host.split(":")
    sock = socket.create_connection((h, int(port)), timeout=10)
    try:
        sock.sendall(raw)
        sock.settimeout(10)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        return data
    finally:
        sock.close()


def _body_json(raw: bytes) -> dict:
    _, _, body = raw.partition(b"\r\n\r\n")
    return json.loads(body.decode("utf-8"))


def test_save_garbage_content_length_fails(server: str) -> None:
    raw = (
        b"POST /save HTTP/1.1\r\nHost: x\r\n"
        b"Content-Length: garbage\r\n"
        b"Content-Type: application/json\r\n"
        b"Connection: close\r\n\r\n{}"
    )
    resp = _post_raw(server, raw)
    assert b"200" in resp.split(b"\r\n", 1)[0]
    assert _body_json(resp)["ok"] is False


def test_save_oversized_body_fails(server: str) -> None:
    try:
        cap = int(blockly_editor.MAX_BODY_BYTES)
    except AttributeError:
        cap = 5 * 1024 * 1024
    ws = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})
    data = post_json(
        server,
        "/save",
        {
            "stem": "VisionOk",
            "workspaceJson": ws,
            "pythonCode": vision_code("a.png"),
            "pad": "x" * (cap + 1024),
        },
    )
    assert data["ok"] is False


def test_save_spoofed_huge_length_fails_fast(server: str) -> None:
    """偽の巨大Content-Lengthは溜め込まず速く断る（閉じ込め）。"""
    import socket
    import time

    try:
        cap = int(blockly_editor.MAX_BODY_BYTES)
    except AttributeError:
        cap = 5 * 1024 * 1024
    claimed = cap * 20
    # 上限＋64KBだけ送れば新しい実装は読み捨てて応答できる。
    # 旧実装は申告全体を読もうとして待ち続けるため遅い。
    send_size = cap + 128 * 1024
    header = (
        "POST /save HTTP/1.1\r\nHost: x\r\n"
        f"Content-Length: {claimed}\r\n"
        "Content-Type: application/json\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    body = b"x" * send_size
    host, port = server.split(":")
    sock = socket.create_connection((host, int(port)), timeout=10)
    try:
        sock.settimeout(10)
        start = time.monotonic()
        sock.sendall(header + body)
        # 送り切ったら半閉じせず応答を待つ（旧実装は残りを待って詰まる）。
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:
                # ヘッダ＋JSON応答ぶんを読めば十分（本文は捨てる）。
                if b"\r\n\r\n" in data:
                    break
        elapsed = time.monotonic() - start
    finally:
        sock.close()
    assert elapsed < 8.0, f"応答が遅すぎます: {elapsed:.1f}s"
    assert b"200" in data.split(b"\r\n", 1)[0]
    assert _body_json(data)["ok"] is False


def make_red_png(width: int = 20, height: int = 20) -> bytes:
    import cv2
    import numpy as np

    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (0, 0, 255)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def test_color_ratio_hit(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = make_red_png()
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/color_ratio",
        {"source": "frame", "lower": [0, 100, 100], "upper": [10, 255, 255]},
    )
    assert data["ok"] is True
    assert data["ratio"] > 0.9


def test_filter_preview_gray_out(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    frame = make_red_png()
    monkeypatch.setattr(blockly_editor, "_GET_FRAME", lambda: frame)
    data = post_json(
        server,
        "/filter_preview",
        {
            "source": "frame",
            "lower": [0, 100, 100],
            "upper": [10, 255, 255],
            "mode": "gray_out",
        },
    )
    assert data["ok"] is True
    assert base64.b64decode(data["image"])[:8] == b"\x89PNG\r\n\x1a\n"


def test_filter_bad_mode_fails(server: str) -> None:
    data = post_json(
        server,
        "/filter_preview",
        {
            "source": "frame",
            "lower": [0, 100, 100],
            "upper": [10, 255, 255],
            "mode": "weird",
        },
    )
    assert data["ok"] is False
