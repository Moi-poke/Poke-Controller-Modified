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

import base64
import functools
import hashlib
import http.server
import json
import socket
import threading
import urllib.parse
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import WindowUtils
from services import (
    blockly_capture,
    blockly_color,
    blockly_match,
    blockly_save,
    blockly_templates,
)

_BLOCKLY_DIR = Path(WindowUtils.APP_DIR) / "assets" / "blockly"

#: POST本文の上限（/save・/delete・/template用）。localhost用の安全弁。
MAX_BODY_BYTES = 5 * 1024 * 1024
#: アップロード付き照合（/match source=upload）用の上限。
#: 10MBの生画像がbase64で約13.4MBになるため余裕を見る。
MAX_UPLOAD_BODY_BYTES = 16 * 1024 * 1024

#: 待ち受けの決め打ち番号。毎回同じ番地（オリジン）にすると、ブラウザ内の
#: 編集控え・画面設定（localStorage）が次回も使える。塞がっていれば任意番号。
PREFERRED_PORT = 51793


class _ExclusiveServer(http.server.ThreadingHTTPServer):
    """番号の横取りをしない待ち受け。

    既定の SO_REUSEADDR は Windows では使用中の番号にも重ねて bind できて
    しまうため切り、Windows では排他指定を付ける（塞がっていれば素直に失敗）。
    """

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


def _bind_server() -> http.server.ThreadingHTTPServer:
    """決め打ち番号で待ち受け、塞がっていれば任意番号へ落とす。"""
    handler = functools.partial(_Handler)
    try:
        return _ExclusiveServer(("127.0.0.1", PREFERRED_PORT), handler)
    except OSError:
        return _ExclusiveServer(("127.0.0.1", 0), handler)


_server: http.server.ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None

_GET_FRAME: Callable[[], bytes | None] | None = None


class _Handler(http.server.SimpleHTTPRequestHandler):
    """静的配信＋`/save`受け口。ログは出さない。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(_BLOCKLY_DIR), **kwargs)

    def log_message(self, *args: Any) -> None:
        return

    #: 受け付ける Host 名。localhost 専用の受け口のため、それ以外の名前で
    #: 届いた要求（DNSリバインディング）は断る。
    _LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})

    def _refuse_foreign(self, *, is_post: bool) -> bool:
        """よそのページからの要求なら403で断り、真を返す。

        `/save` は `.py` を書き、一覧の作り直しで読み込まれる（＝コード実行）。
        待ち受け番号は固定のため、閲覧中の別サイトから狙われないよう、
        Host・Origin・JSON種別で同じオリジンの編集画面だけを通す。
        JSON種別はブラウザでは事前確認（preflight）が要り、ここは応じないため
        no-cors の単純POSTも届かない。
        """
        # 待ち受けは常に IPv4 の (host, port) 組（_bind_server・試験とも）。
        port = self.server.server_address[1]  # type: ignore[index]
        host = str(self.headers.get("Host", "") or "")
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        reason = ""
        if host and name.lower() not in self._LOCAL_HOSTS:
            reason = "Host が違います"
        if is_post and not reason:
            origin = self.headers.get("Origin")
            allowed = {f"http://{h}:{port}" for h in self._LOCAL_HOSTS}
            if origin is not None and origin not in allowed:
                reason = "別のページからの要求です"
            ctype = str(self.headers.get("Content-Type", "") or "").lower()
            if not reason and not ctype.startswith("application/json"):
                reason = "JSON 以外の要求です"
        if not reason:
            return False
        if is_post:
            # 未読のまま閉じるとRSTで応答が届かないため、上限つきで読み捨てる。
            try:
                remaining = int(str(self.headers.get("Content-Length", "0")).strip())
                remaining = min(max(remaining, 0), MAX_UPLOAD_BODY_BYTES + 65536)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except Exception:
                pass
        body = json.dumps(
            {"ok": False, "message": f"受け付けません: {reason}"}, ensure_ascii=False
        ).encode("utf-8")
        self.close_connection = True
        self.send_response(403)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def end_headers(self) -> None:
        # アプリ更新後に古い資産・一覧をブラウザが使い回さないよう、都度取り直させる。
        self.send_header("Cache-Control", "no-store")
        # 他サイトのiframeへの埋め込みを断る（重ねたクリック誘導で削除等をさせない）。
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        super().end_headers()

    def do_GET(self) -> None:
        """`/list`・`/load`の受け口。それ以外は静的配信に任せる。"""
        if self._refuse_foreign(is_post=False):
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/list":
            stems = blockly_save.list_blockly(WindowUtils.APP_DIR)
            # 置き場ごとの識別子。番地を固定したため、別インストールの編集画面とも
            # ブラウザ内の保存領域を共有する。控えをこれで分ける（中身は推測不能でよい）。
            app_id = hashlib.sha256(
                str(Path(WindowUtils.APP_DIR).resolve()).encode("utf-8")
            ).hexdigest()[:16]
            self._reply(True, "", {"stems": stems, "appId": app_id})
            return
        if path == "/load":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            stem = query.get("stem", [""])[0]
            res = blockly_save.load_blockly(WindowUtils.APP_DIR, stem)
            if res.status != "ok":
                self._reply(False, res.message)
                return
            # 手編集検出は保存時の要約値との比較（上書き保存で消えるため知らせる）。
            extra: dict[str, Any] = {
                "stem": stem,
                "workspaceJson": res.workspace_json,
                "externalEdit": res.external_edit,
            }
            self._reply(True, res.message, extra)
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

    def _read_json(self, max_bytes: int = MAX_BODY_BYTES) -> dict[str, Any] | None:
        """POST本文を読む。壊れていたら応答済みで None を返す。"""
        try:
            length = int(str(self.headers.get("Content-Length", "0")).strip())
        except (TypeError, ValueError) as e:
            self._reply(False, f"保存できません: {e}")
            return None
        if length < 0 or length > max_bytes:
            # 未読のまま閉じるとRSTで応答が届かないため読み捨てる。
            # 申告どおりに全部読むと巨大申告で固まるため、上限＋64KBで打ち切る。
            try:
                remaining = min(length, max_bytes + 65536)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except Exception:
                pass
            self.close_connection = True
            self._reply(False, "保存できません: 本文が大きすぎます")
            return None
        try:
            payload: dict[str, Any] = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError) as e:
            self._reply(False, f"保存できません: {e}")
            return None
        return payload

    def do_POST(self) -> None:
        if self._refuse_foreign(is_post=True):
            return
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
            payload = self._read_json(MAX_UPLOAD_BODY_BYTES)
            if payload is None:
                return
            source = str(payload.get("source", "frame"))
            frame: Any = None
            if source == "upload":
                try:
                    # 復号は1回だけ（decode→ndarray→matchへ受渡し）。
                    # 文言は従来のbytes APIと同一に保つ。
                    frame = blockly_match.decode_upload_array(
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
                    frame = getter()
                except Exception:
                    frame = None
                if frame is None:
                    self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                    return
            res = blockly_match.match_template(
                WindowUtils.APP_DIR,
                frame,
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
        if urllib.parse.urlsplit(self.path).path == "/color_ratio":
            payload = self._read_json(MAX_UPLOAD_BODY_BYTES)
            if payload is None:
                return
            source = str(payload.get("source", "frame"))
            frame = None
            if source == "upload":
                try:
                    # 復号は1回だけ（decode→ndarray→ratioへ受渡し）。
                    # 文言は従来のbytes APIと同一に保つ。
                    frame = blockly_match.decode_upload_array(
                        str(payload.get("image", ""))
                    )
                except ValueError as e:
                    self._reply(False, f"色を見られません: {e}")
                    return
            else:
                getter = _GET_FRAME
                if getter is None:
                    self._reply(False, blockly_capture.NO_CAMERA_MESSAGE)
                    return
                try:
                    frame = getter()
                except Exception:
                    frame = None
                if frame is None:
                    self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                    return
            ratio, err = blockly_color.ratio_on_png(
                frame,
                payload.get("lower"),
                payload.get("upper"),
                payload.get("crop", None),
            )
            if err is not None:
                self._reply(False, f"色を見られません: {err}")
                return
            message = f"割合 {ratio * 100:.1f}%"
            print(message)
            self._reply(True, message, {"ratio": ratio})
            return
        if urllib.parse.urlsplit(self.path).path == "/filter_preview":
            payload = self._read_json(MAX_UPLOAD_BODY_BYTES)
            if payload is None:
                return
            source = str(payload.get("source", "frame"))
            frame = None
            if source == "upload":
                try:
                    # 復号は1回だけ（decode→ndarray→filterへ受渡し）。
                    # 文言は従来のbytes APIと同一に保つ。
                    frame = blockly_match.decode_upload_array(
                        str(payload.get("image", ""))
                    )
                except ValueError as e:
                    self._reply(False, f"色を見られません: {e}")
                    return
            else:
                getter = _GET_FRAME
                if getter is None:
                    self._reply(False, blockly_capture.NO_CAMERA_MESSAGE)
                    return
                try:
                    frame = getter()
                except Exception:
                    frame = None
                if frame is None:
                    self._reply(False, blockly_capture.FRAME_FAIL_MESSAGE)
                    return
            out, ratio, err = blockly_color.filter_png(
                frame,
                payload.get("lower"),
                payload.get("upper"),
                payload.get("mode"),
                payload.get("crop", None),
            )
            if err is not None or out is None:
                self._reply(False, f"色を見られません: {err}")
                return
            message = f"割合 {ratio * 100:.1f}%"
            print(message)
            image = base64.b64encode(out).decode("ascii")
            self._reply(True, message, {"ratio": ratio, "image": image})
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
    """待ち受けを止める。何度呼んでもよい。

    GUIスレッドを固めないよう shutdown/close だけここで行い、
    join は短命のdaemon番兵に任せる（serve側もdaemonのため
    万一残っても放置でよい）。
    """
    global _server, _thread
    server, _server = _server, None
    if server is not None:
        server.shutdown()
        server.server_close()
    thread, _thread = _thread, None
    if thread is not None:

        def _join() -> None:
            thread.join(timeout=5.0)

        threading.Thread(target=_join, name="BlocklyJoin", daemon=True).start()


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
    global _server, _thread
    _server = _bind_server()
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

    def copy_url() -> None:
        """起動URLをクリップボードへ入れる（自動で開かない環境用）。"""
        try:
            win.clipboard_clear()
            win.clipboard_append(url)
        except Exception:
            pass

    def reopen() -> None:
        """タブを閉じてしまったときに開き直す（編集中の控えはブラウザ側に残る）。"""
        try:
            webbrowser.open(url)
        except Exception:
            pass

    buttons = tk.Frame(win)
    buttons.pack(padx=10, pady=(0, 10))
    tk.Button(buttons, text="ブラウザで開く", command=reopen).pack(side=tk.LEFT, padx=4)
    tk.Button(buttons, text="URLをコピー", command=copy_url).pack(side=tk.LEFT, padx=4)

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
