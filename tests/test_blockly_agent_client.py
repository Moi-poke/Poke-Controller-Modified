"""エディタの受け口へ繋ぐローカル HTTP クライアント（EditorClient）の検証。

相手は本物の http.server（127.0.0.1 の任意ポート）にする。外の network は
使わない。接続先の検査・フレームの生バイト・runId 付きの操作・転送を
追わないことを確かめる。
"""

from __future__ import annotations

import http.server
import json
import threading
import urllib.parse
from collections.abc import Iterator
from typing import Any

import pytest
from services.blockly_agent_client import EditorClient, parse_origin

#: `/frame` が返す本物の PNG の先頭（中身は問わない）。
PNG = b"\x89PNG\r\n\x1a\n" + b"body-of-a-png"


class _Recorder:
    """受け取った要求と、返す応答の決め打ち。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.routes: dict[str, tuple[int, dict[str, str], bytes]] = {}


def _handler_class(rec: _Recorder) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            return

        def _handle(self) -> None:
            length = int(str(self.headers.get("Content-Length") or 0))
            body = self.rfile.read(length) if length else b""
            rec.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            path = urllib.parse.urlsplit(self.path).path
            route = rec.routes.get(path)
            if route is None:
                status, headers, payload = (
                    404,
                    {"Content-Type": "application/json"},
                    b'{"ok": false, "message": "not found"}',
                )
            else:
                status, headers, payload = route
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _handle
        do_POST = _handle

    return Handler


@pytest.fixture()
def editor_server() -> Iterator[tuple[str, _Recorder]]:
    """127.0.0.1 の空きポートで本物の受け口を立てる。"""
    rec = _Recorder()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(rec))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", rec
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5.0)


def _json(payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return (
        200,
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


# -- 接続先の検査 ---------------------------------------------------------------


def test_parse_origin_accepts_local_http_origins_with_a_port() -> None:
    """127.0.0.1 と localhost の http で、ポートがあるものだけを受けること。"""
    assert parse_origin("http://127.0.0.1:51793") == "http://127.0.0.1:51793"
    assert parse_origin("http://LOCALHOST:51793") == "http://localhost:51793"
    assert parse_origin("  http://127.0.0.1:1/  ") == "http://127.0.0.1:1"


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com:51793",
        "https://127.0.0.1:51793",
        "http://127.0.0.1:51793/editor.html",
        "http://127.0.0.1:51793?since=1",
        "http://127.0.0.1:51793#log",
        "http://user:pw@127.0.0.1:51793",
        "http://127.0.0.1",
        "http://127.0.0.1:notaport",
        "",
    ],
)
def test_parse_origin_rejects_other_shapes(value: str) -> None:
    """別ホスト・https・パス・問い合わせ・断片・資格情報・ポート無しは断ること。"""
    with pytest.raises(ValueError):
        parse_origin(value)
    with pytest.raises(ValueError):
        EditorClient(value)


# -- /frame ---------------------------------------------------------------------


def test_frame_returns_the_exact_png_bytes(
    editor_server: tuple[str, _Recorder],
) -> None:
    """/frame の生バイトをそのまま返すこと。"""
    base, rec = editor_server
    rec.routes["/frame"] = (200, {"Content-Type": "image/png"}, PNG)
    client = EditorClient(base)
    assert client.frame() == PNG
    assert rec.requests[-1]["method"] == "GET"
    assert rec.requests[-1]["path"] == "/frame"


def test_frame_rejects_a_body_that_is_not_png(
    editor_server: tuple[str, _Recorder],
) -> None:
    """PNG でない応答は ValueError にすること。"""
    base, rec = editor_server
    rec.routes["/frame"] = (200, {"Content-Type": "image/png"}, b"GIF89a....")
    with pytest.raises(ValueError, match="PNG"):
        EditorClient(base).frame()


def test_http_error_carries_the_server_message(
    editor_server: tuple[str, _Recorder],
) -> None:
    """HTTP エラーの JSON に理由があれば、その文言を添えて ValueError にすること。"""
    base, rec = editor_server
    rec.routes["/frame"] = (
        503,
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps(
            {"ok": False, "message": "カメラがありません"}, ensure_ascii=False
        ).encode("utf-8"),
    )
    with pytest.raises(ValueError, match="カメラがありません"):
        EditorClient(base).frame()


# -- /run/state・/run・/run/control ----------------------------------------------


def test_state_appends_since(editor_server: tuple[str, _Recorder]) -> None:
    """様子の取りに行きに since を付けること。"""
    base, rec = editor_server
    rec.routes["/run/state"] = _json({"ok": True, "runId": 4})
    client = EditorClient(base)
    assert client.state(7)["runId"] == 4
    assert rec.requests[-1]["path"] == "/run/state?since=7"


def test_start_trial_posts_code_breakpoints_and_the_limit(
    editor_server: tuple[str, _Recorder],
) -> None:
    """/run へ、コード・空の区切り・時間上限を同じオリジンから送ること。"""
    base, rec = editor_server
    rec.routes["/run"] = _json({"ok": True, "runId": 3})
    client = EditorClient(base)
    assert client.start_trial("print('x')", max_seconds=1.5)["runId"] == 3
    request = rec.requests[-1]
    assert request["method"] == "POST"
    assert request["headers"]["content-type"] == "application/json"
    assert request["headers"]["origin"] == base
    assert json.loads(request["body"]) == {
        "code": "print('x')",
        "breakpoints": [],
        "maxSeconds": 1.5,
    }


def test_control_posts_the_action_and_run_id(
    editor_server: tuple[str, _Recorder],
) -> None:
    """/run/control へ action と runId を送ること。"""
    base, rec = editor_server
    rec.routes["/run/control"] = _json({"ok": True, "message": ""})
    client = EditorClient(base)
    client.control(3, "stop")
    request = rec.requests[-1]
    assert request["headers"]["origin"] == base
    assert json.loads(request["body"]) == {"action": "stop", "runId": 3}


def test_ok_false_with_a_message_becomes_a_value_error(
    editor_server: tuple[str, _Recorder],
) -> None:
    """HTTP 200 でも ok が false なら、理由を添えて ValueError にすること。"""
    base, rec = editor_server
    rec.routes["/run/control"] = _json(
        {"ok": False, "message": "試し実行が切り替わったため操作しません"}
    )
    with pytest.raises(ValueError, match="切り替わった"):
        EditorClient(base).control(3, "stop")


# -- 転送 -----------------------------------------------------------------------


def test_a_redirect_is_not_followed(
    editor_server: tuple[str, _Recorder],
) -> None:
    """転送は追わず、エラーとして返すこと（localhost の外へ出ない）。"""
    base, rec = editor_server
    rec.routes["/frame"] = (302, {"Location": base + "/elsewhere"}, b"")
    rec.routes["/elsewhere"] = (200, {"Content-Type": "image/png"}, PNG)
    with pytest.raises(ValueError, match="HTTP 302"):
        EditorClient(base).frame()
    assert [request["path"] for request in rec.requests] == ["/frame"]
