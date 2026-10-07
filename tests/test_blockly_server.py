"""Blocklyエディタ配信の検証（古い資産を出さない・同じ番地で待ち受ける）。"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

from ui import blockly_editor  # noqa: E402


@pytest.fixture()
def server() -> Iterator[str]:
    """本物の受け口を一時ポートで立てる。"""
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5.0)


def get(host: str, path: str) -> http.client.HTTPResponse:
    """GETして応答を返す（本文は読み切る）。"""
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    resp.read()
    return resp


def free_port() -> int:
    """空いている番号を1つ得る。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def test_static_assets_are_never_served_from_browser_cache(server: str) -> None:
    """アプリ更新後に古いJSが使われないよう、静的資産は都度取り直させること。"""
    # Given/When: 編集画面とブロック定義を取る
    for path in ["/editor.html", "/pokecon_blocks.js", "/pokecon_editor.js"]:
        resp = get(server, path)
        # Then: 取れて、キャッシュ禁止が付いている
        assert resp.status == 200, path
        assert resp.getheader("Cache-Control") == "no-store", path


def test_json_replies_are_not_cached_either(server: str) -> None:
    """一覧などのJSON応答も都度取り直させること。"""
    # Given/When: 一覧を取る
    resp = get(server, "/list")
    # Then: キャッシュ禁止
    assert resp.getheader("Cache-Control") == "no-store"


def test_list_names_the_install_so_drafts_stay_per_install(
    save_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """一覧は置き場ごとの識別子を返し、ブラウザ内の控えを置き場ごとに分けられること。"""
    import WindowUtils

    host, _ = save_server
    # Given/When: 同じ置き場で2回・別の置き場で1回取る
    conn = http.client.HTTPConnection(host, timeout=10)

    def app_id() -> str:
        conn.request("GET", "/list")
        resp = conn.getresponse()
        value: str = json.loads(resp.read().decode("utf-8"))["appId"]
        return value

    first, again = app_id(), app_id()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(WindowUtils, "APP_DIR", str(other))
    elsewhere = app_id()
    # Then: 同じ置き場では同じ、別の置き場では違う
    assert first == again and len(first) >= 8
    assert elsewhere != first


def test_server_prefers_the_fixed_port_so_browser_storage_survives_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """毎回同じ番地で待ち受け、ブラウザ内の控え・画面設定が次回も使えること。"""
    # Given: 決め打ちの番号が空いている
    port = free_port()
    monkeypatch.setattr(blockly_editor, "PREFERRED_PORT", port)
    # When: 待ち受けを作る
    httpd = blockly_editor._bind_server()
    try:
        # Then: その番号で待ち受ける
        assert httpd.server_address[1] == port
    finally:
        httpd.server_close()


def test_server_falls_back_to_any_port_when_the_fixed_one_is_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """決め打ちの番号が塞がっていても、別の番号で開けること（起動を止めない）。"""
    # Given: 決め打ちの番号を他が使っている
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken = blocker.getsockname()[1]
    monkeypatch.setattr(blockly_editor, "PREFERRED_PORT", taken)
    try:
        # When: 待ち受けを作る
        httpd = blockly_editor._bind_server()
        try:
            # Then: 別の番号で開けている
            assert httpd.server_address[1] not in (0, taken)
        finally:
            httpd.server_close()
    finally:
        blocker.close()


def post(
    host: str, path: str, body: bytes, headers: dict[str, str]
) -> tuple[int, bytes]:
    """POSTして状態番号と本文を返す。"""
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    return resp.status, resp.read()


@pytest.fixture()
def save_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[str, Path]]:
    """保存先を一時置き場にした受け口。"""
    import WindowUtils

    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    monkeypatch.setattr(WindowUtils, "APP_DIR", str(app))
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{httpd.server_address[1]}", app
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5.0)


SAVE_BODY = json.dumps(
    {
        "stem": "Evil",
        "workspaceJson": json.dumps({"blocks": {"blocks": []}}),
        "pythonCode": (
            "from Commands.PythonCommandBase import PythonCommand\n\n\n"
            "class C(PythonCommand):\n"
            '    NAME = "x"\n\n'
            "    def do(self) -> None:\n"
            "        pass\n"
        ),
    }
).encode("utf-8")


def test_cross_site_page_cannot_write_a_command(
    save_server: tuple[str, Path],
) -> None:
    """別サイトのページ（Origin違い）からの保存は受け付けず、ファイルも書かないこと。"""
    host, app = save_server
    # Given/When: 別オリジンを名乗るPOST
    status, _ = post(
        host,
        "/save",
        SAVE_BODY,
        {"Content-Type": "application/json", "Origin": "http://evil.example"},
    )
    # Then: 拒否され、何も書かれない
    assert status == 403
    assert not (app / "Commands" / "PythonCommands" / "Evil.py").exists()


def test_simple_form_post_without_json_type_is_refused(
    save_server: tuple[str, Path],
) -> None:
    """text/plain の単純POST（no-corsで送れる形）は受け付けないこと。"""
    host, app = save_server
    # Given/When: JSON以外の種別で送る
    status, _ = post(host, "/save", SAVE_BODY, {"Content-Type": "text/plain"})
    # Then: 拒否され、何も書かれない
    assert status == 403
    assert not (app / "Commands" / "PythonCommands" / "Evil.py").exists()


def test_rebound_host_name_is_refused(save_server: tuple[str, Path]) -> None:
    """127.0.0.1/localhost 以外の Host（DNSリバインディング）は受け付けないこと。"""
    host, _ = save_server
    port = host.rsplit(":", 1)[1]
    # Given/When: 攻撃者のドメインを Host に持つ要求
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", "/list", headers={"Host": f"evil.example:{port}"})
    resp = conn.getresponse()
    resp.read()
    # Then: 拒否される
    assert resp.status == 403


def test_same_origin_editor_can_still_save(save_server: tuple[str, Path]) -> None:
    """編集画面自身（同じオリジン）からの保存は従来どおり通ること（対照）。"""
    host, app = save_server
    # Given/When: 同じオリジンからJSONで保存
    status, _ = post(
        host,
        "/save",
        SAVE_BODY,
        {"Content-Type": "application/json", "Origin": f"http://{host}"},
    )
    # Then: 書ける
    assert status == 200
    assert (app / "Commands" / "PythonCommands" / "Evil.py").exists()


def test_opaque_null_origin_is_refused(save_server: tuple[str, Path]) -> None:
    """Origin が null（サンドボックスiframe等）の保存も受け付けないこと。"""
    host, app = save_server
    # Given/When: Origin: null で送る
    status, _ = post(
        host,
        "/save",
        SAVE_BODY,
        {"Content-Type": "application/json", "Origin": "null"},
    )
    # Then: 拒否され、何も書かれない
    assert status == 403
    assert not (app / "Commands" / "PythonCommands" / "Evil.py").exists()


def test_editor_page_refuses_to_be_framed_by_other_sites(server: str) -> None:
    """編集画面は他サイトのiframeに埋め込めないこと（クリック誘導で削除させない）。"""
    # Given/When: 編集画面を取る
    resp = get(server, "/editor.html")
    # Then: 埋め込み禁止の指定がある
    assert resp.getheader("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in (resp.getheader("Content-Security-Policy") or "")
