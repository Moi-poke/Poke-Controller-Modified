#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blocklyエディタの起動と保存受け口（tkinter）。

実保存は services.blockly_save が持ち、ここでは配信・起動・
終了時の作り直しだけにする。保存ハンドラ（サーバスレッド）では
tkinterを触らず、ファイル書きの結果だけをJSONで返す。再読込は
エディタを閉じた後のGUIスレッドで行う（実行中は reloadCommands
側も塞いでいるが、入口でも断つ）。
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
import urllib.parse
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import WindowUtils
from services import blockly_capture, blockly_match, blockly_save, blockly_templates

_BLOCKLY_DIR = Path(WindowUtils.APP_DIR) / "assets" / "blockly"

_server: http.server.ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None

_GET_FRAME: Callable[[], bytes | None] | None = None


class _Handler(http.server.SimpleHTTPRequestHandler):
    """静的配信＋`/save`受け口。ログは出さない。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(_BLOCKLY_DIR), **kwargs)

    def log_message(self, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        """`/list`・`/load`の受け口。それ以外は静的配信に任せる。"""
        path = urllib.parse.urlsplit(self.path).path
        if path == "/list":
            stems = blockly_save.list_blockly(WindowUtils.APP_DIR)
            self._reply(True, "", {"stems": stems})
            return
        if path == "/load":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            stem = query.get("stem", [""])[0]
            res = blockly_save.load_blockly(WindowUtils.APP_DIR, stem)
            if res.status != "ok":
                self._reply(False, res.message)
                return
            self._reply(
                True, res.message, {"stem": stem, "workspaceJson": res.workspace_json}
            )
            return
        if path == "/templates":
            names = blockly_templates.list_image_templates(WindowUtils.APP_DIR)
            self._reply(True, "", {"templates": names})
            return
        if path == "/frame":
            getter = _GET_FRAME
            if getter is None:
                self._reply(False, blockly_capture.NO_CAMERA_MESSAGE)
                return
            try:
                png = getter()
            except Exception:
                png = None
            if png is None:
                self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        if path == "/template_image":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            name = query.get("name", [""])[0]
            reason = blockly_capture.validate_template_name(name.strip())
            if reason is not None:
                self._reply(False, f"画像を出せません: {reason}")
                return
            base = Path(WindowUtils.APP_DIR) / "Template"
            target = (base / Path(str(name).replace("\\", "/"))).resolve()
            try:
                target.relative_to(base.resolve())
            except ValueError:
                self._reply(False, "画像を出せません: `..` は使えません")
                return
            content_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".bmp": "image/bmp",
            }
            ctype = content_types.get(target.suffix.lower())
            if ctype is None or not target.is_file():
                self._reply(False, f"画像を出せません: Template/{name} がありません")
                return
            try:
                body = target.read_bytes()
            except OSError as e:
                self._reply(False, f"画像を出せません: {e}")
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def _read_json(self) -> dict[str, Any] | None:
        """POST本文を読む。壊れていたら応答済みで None を返す。"""
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload: dict[str, Any] = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError) as e:
            self._reply(False, f"保存できません: {e}")
            return None
        return payload

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path == "/delete":
            payload = self._read_json()
            if payload is None:
                return
            res = blockly_save.delete_blockly(
                WindowUtils.APP_DIR, str(payload.get("stem", ""))
            )
            print(res.message)
            self._reply(res.status == "deleted", res.message)
            return
        if urllib.parse.urlsplit(self.path).path == "/template":
            payload = self._read_json()
            if payload is None:
                return
            getter = _GET_FRAME
            if getter is None:
                self._reply(False, blockly_capture.NO_CAMERA_MESSAGE)
                return
            try:
                png = getter()
            except Exception:
                png = None
            if png is None:
                self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                return
            res = blockly_capture.save_template(
                WindowUtils.APP_DIR,
                str(payload.get("name", "")),
                png,
                payload.get("rect"),
            )
            print(res.message)
            extra = {"path": res.rel} if res.status == "saved" else {}
            self._reply(res.status == "saved", res.message, extra)
            return
        if urllib.parse.urlsplit(self.path).path == "/match":
            payload = self._read_json()
            if payload is None:
                return
            source = str(payload.get("source", "frame"))
            if source == "upload":
                try:
                    png = blockly_match.decode_upload_image(
                        str(payload.get("image", ""))
                    )
                except ValueError as e:
                    self._reply(False, f"照合できません: {e}")
                    return
            else:
                getter = _GET_FRAME
                if getter is None:
                    self._reply(False, blockly_capture.NO_CAMERA_MESSAGE)
                    return
                try:
                    png = getter()
                except Exception:
                    png = None
                if png is None:
                    self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                    return
            res = blockly_match.match_template(
                WindowUtils.APP_DIR,
                png,
                str(payload.get("template", "")),
                payload.get("threshold", 0.7),
                bool(payload.get("use_gray", True)),
                payload.get("crop", None),
            )
            print(res.message)
            extra = (
                {
                    "score": res.score,
                    "matched": res.matched,
                    "count": res.count,
                    "rect": res.rect,
                }
                if res.status == "ok"
                else {}
            )
            self._reply(res.status == "ok", res.message, extra)
            return
        if urllib.parse.urlsplit(self.path).path == "/save":
            payload = self._read_json()
            if payload is None:
                return
            res = blockly_save.save_blockly(
                WindowUtils.APP_DIR,
                str(payload.get("stem", "")),
                str(payload.get("workspaceJson", "")),
                str(payload.get("pythonCode", "")),
            )
            print(res.message)
            self._reply(res.status == "saved", res.message, {"warnings": res.warnings})
            return
        self.send_error(404)

    def _reply(
        self, ok: bool, message: str, extra: dict[str, Any] | None = None
    ) -> None:
        body = json.dumps(
            {"ok": ok, "message": message, **(extra or {})}, ensure_ascii=False
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _py_dir_mtime() -> float:
    """保存先フォルダの更新時刻。読めなければ 0.0（比較不能＝変化なし扱い）。"""
    try:
        return (
            Path(WindowUtils.APP_DIR, *blockly_save.PY_DIR_REL.split("/"))
            .stat()
            .st_mtime
        )
    except OSError:
        return 0.0


def _stop_server() -> None:
    """待ち受けを止める。何度呼んでもよい。"""
    global _server, _thread
    server, _server = _server, None
    if server is not None:
        server.shutdown()
        server.server_close()
    thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout=5.0)


def stop_blockly_editor() -> None:
    """Menubar.closeAllから呼ぶ。開きっぱなしでの終了を防ぐ。"""
    _stop_server()


def open_blockly_editor(
    root: Any,
    *,
    is_busy: Callable[[], bool],
    reload_commands: Callable[[], None],
    get_frame: Callable[[], bytes | None] | None = None,
) -> None:
    """エディタを開く。終わったら一覧を作り直す。"""
    import tkinter as tk
    import tkinter.messagebox as tkmsg

    if is_busy():
        print("実行中はBlocklyエディタを開けません")
        tkmsg.showwarning("Blockly", "実行中はBlocklyエディタを開けません", parent=root)
        return
    global _GET_FRAME
    _GET_FRAME = get_frame
    _stop_server()
    handler = functools.partial(_Handler)
    global _server, _thread
    _server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = _server.server_address[1]
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    url = f"http://127.0.0.1:{port}/editor.html"
    webbrowser.open(url)

    win = tk.Toplevel(root)
    win.title("Blocklyエディタ")
    win.resizable(False, False)
    tk.Label(win, text="ブラウザで編集し、保存ボタンで保存します。").pack(
        padx=10, pady=10
    )
    tk.Label(win, text=url).pack(padx=10, pady=(0, 10))

    last_mtime = _py_dir_mtime()

    def poll() -> None:
        """保存先の変化を一覧へ反映する（自動保存はしない）。

        サーバスレッドからtkinterを触れないため、GUI側が約1秒ごとに
        見に行く。実行中は reloadCommands 側も断るが、ここでも塞いで
        毎秒の通知を出さない。窓が閉じたら連鎖を止める。
        """
        nonlocal last_mtime
        try:
            alive = bool(win.winfo_exists())
        except Exception:
            return
        if not alive:
            return
        try:
            cur = _py_dir_mtime()
        except Exception:
            cur = last_mtime
        if cur != last_mtime:
            last_mtime = cur
            if not is_busy():
                reload_commands()
        try:
            win.after(1000, poll)
        except Exception:
            pass

    def on_close() -> None:
        _stop_server()
        try:
            win.destroy()
        except Exception:
            pass
        if not is_busy():
            reload_commands()

    win.protocol("WM_DELETE_WINDOW", on_close)
    win.after(1000, poll)
