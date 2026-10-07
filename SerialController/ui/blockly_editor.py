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
import itertools
import json
import queue
import socket
import threading
import urllib.parse
import webbrowser
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import WindowUtils
from services import (
    blockly_capture,
    blockly_color,
    blockly_match,
    blockly_run,
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

#: 試し実行の司令塔（本体の PokeControllerApp）。開始・停止はこれの手順を
#: GUI スレッドで呼ぶ。無ければ（本体と繋がっていなければ）試し実行しない。
_RUN_HOST: Any = None

#: GUI スレッドへ頼む仕事の待ち行列。受け口のスレッドは Tk を触れないため、
#: 積んで結果を待つ。取り出すのはエディタの窓の見回り（after）。
_GUI_CALLS: queue.Queue[tuple[Callable[[], Any], Future[Any]]] = queue.Queue()

#: GUI スレッドの応答を待つ上限(秒)。本体が固まっていても受け口は返す。
GUI_CALL_TIMEOUT_S = 5.0


class _Trial:
    """いまの（直近の）試し実行。様子と、走らせたクラスの組。"""

    def __init__(self, session: blockly_run.TrialSession, cls: type) -> None:
        self.session = session
        self.cls = cls


_TRIAL: _Trial | None = None
_RUN_IDS = itertools.count(1)
#: 区切り（ブレークポイント）の既定。次の実行にも引き継ぐ。
_BREAKPOINTS: frozenset[str] = frozenset()


def _set_run_host(host: Any) -> None:
    """試し実行の司令塔を差し替える（開く時・検証用）。"""
    global _RUN_HOST
    _RUN_HOST = host


def _call_on_gui(fn: Callable[[], Any]) -> Any:
    """fn を GUI スレッドで走らせ、結果を待って返す（例外もそのまま投げ直す）。"""
    fut: Future[Any] = Future()
    _GUI_CALLS.put((fn, fut))
    try:
        return fut.result(timeout=GUI_CALL_TIMEOUT_S)
    except TimeoutError:
        # 取り下げられれば、後で黙って走らせない（断ったのに始まるのを防ぐ）。
        if fut.cancel():
            raise
        # もう走り始めていて取り下げられない。終わりを待って結果を使う。
        return fut.result()


def drain_gui_calls() -> None:
    """待ち行列の仕事を片付ける。GUI スレッドの見回りから呼ぶ。"""
    while True:
        try:
            fn, fut = _GUI_CALLS.get_nowait()
        except queue.Empty:
            return
        if not fut.set_running_or_notify_cancel():
            continue
        try:
            fut.set_result(fn())
        except Exception as e:
            fut.set_exception(e)


def _our_command(trial: _Trial | None) -> Any:
    """走っている命令が、この試し実行のものならそれを返す。"""
    host = _RUN_HOST
    if trial is None or host is None:
        return None
    runner = getattr(host, "runner", None)
    cmd = getattr(runner, "running_command", None)
    if cmd is not None and type(cmd) is trial.cls:
        return cmd
    return None


def _run_state(since: int) -> dict[str, Any]:
    """編集画面へ返す試し実行の様子。"""
    host = _RUN_HOST
    trial = _TRIAL
    busy = False
    if host is not None:
        try:
            busy = bool(host.runner.is_busy())
        except Exception:
            busy = False
    cmd = _our_command(trial)
    if trial is not None and cmd is None and trial.session.running:
        # 結果は後始末の手前で決まるため、走り終えたなら実行器が空く前に出ている。
        # 結果が無いまま実行器が手放した（止まらない作業を打ち切った）ときは、
        # 編集画面が「実行中」のまま待ち続けないよう停止として閉じる。
        trial.session.end("停止")
    if trial is None:
        state: dict[str, Any] = {
            "runId": 0,
            "running": False,
            "result": "",
            "blockId": "",
            "error": "",
            "errorBlock": "",
            "logs": [],
            "vars": [],
        }
    else:
        state = trial.session.snapshot(since)
    paused = False
    if cmd is not None:
        try:
            paused = bool(cmd.isPaused())
        except Exception:
            paused = False
    state.update(
        {
            "available": host is not None,
            "paused": paused,
            "busy": busy,
            # 本体側で一覧のコマンドが走っている（試し実行は始められない）。
            "otherBusy": busy and cmd is None,
        }
    )
    return state


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
        if path == "/run/state":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                since = int(query.get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._reply(True, "", _run_state(since))
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
            # 形は下で確かめるまで分からない（配列・数値も読める）。
            payload: Any = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._reply(False, f"保存できません: {e}")
            return None
        if not isinstance(payload, dict):
            # 配列などは各口の .get で落ち、応答なしで切れるため、ここで断る。
            self._reply(
                False,
                "受け付けません: JSONの形が違います（オブジェクトではありません）",
            )
            return None
        return payload

    def do_POST(self) -> None:
        if self._refuse_foreign(is_post=True):
            return
        if urllib.parse.urlsplit(self.path).path == "/run":
            payload = self._read_json()
            if payload is None:
                return
            self._start_trial(payload)
            return
        if urllib.parse.urlsplit(self.path).path == "/run/control":
            payload = self._read_json()
            if payload is None:
                return
            self._control_trial(payload)
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

    def _start_trial(self, payload: dict[str, Any]) -> None:
        """保存せずに今の組み立てを本体の実行器で走らせる（試し実行）。"""
        global _TRIAL, _BREAKPOINTS
        host = _RUN_HOST
        if host is None:
            self._reply(
                False,
                "本体と繋がっていないため試し実行できません（保存して本体で実行してください）",
            )
            return
        if "breakpoints" in payload:
            _BREAKPOINTS = _as_ids(payload.get("breakpoints"))
        session = blockly_run.TrialSession(next(_RUN_IDS))
        session.breakpoints = _BREAKPOINTS
        cls, errors = blockly_run.build_trial_class(
            str(payload.get("code", "")), session
        )
        if cls is None:
            self._reply(False, "実行できません:\n- " + "\n- ".join(errors))
            return
        trial = _Trial(session, cls)
        try:
            message = _call_on_gui(lambda: host.start_external_command(cls))
        except Exception as e:
            # 開始の後の手順（一覧の作り直し等）で落ちても、走っていれば追跡する。
            # 追跡しないと編集画面から見えず、止めることもできない。
            if _our_command(trial) is not None:
                _TRIAL = trial
                self._reply(
                    True,
                    f"試し実行を始めました（本体で注意: {e}）",
                    {"runId": session.run_id},
                )
                return
            self._reply(False, f"本体が応答しません: {e}")
            return
        if message:
            self._reply(False, str(message))
            return
        _TRIAL = trial
        self._reply(True, "試し実行を始めました", {"runId": session.run_id})

    def _control_trial(self, payload: dict[str, Any]) -> None:
        """試し実行の停止・一時停止・再開・1つ進む・区切りの差し替え。"""
        global _BREAKPOINTS
        action = str(payload.get("action", ""))
        trial = _TRIAL
        if action == "breakpoints":
            _BREAKPOINTS = _as_ids(payload.get("breakpoints"))
            if trial is not None:
                trial.session.breakpoints = _BREAKPOINTS
            self._reply(True, "")
            return
        host = _RUN_HOST
        cmd = _our_command(trial)
        if host is None or trial is None or cmd is None:
            self._reply(False, "試し実行は動いていません")
            return
        session = trial.session
        try:
            if action == "stop":
                session.stop_requested = True
                _call_on_gui(host.stopPlay)
            elif action == "pause":
                _call_on_gui(lambda: host.set_running_paused(True))
            elif action == "resume":
                _call_on_gui(lambda: host.set_running_paused(False))
            elif action == "step":
                # 次のブロックの手前で止まるよう頼んでから動かす。
                session.step_pending = True
                _call_on_gui(lambda: host.set_running_paused(False))
            else:
                self._reply(False, f"知らない操作です: {action}")
                return
        except Exception as e:
            self._reply(False, f"本体が応答しません: {e}")
            return
        self._reply(True, "")

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


def _as_ids(value: Any) -> frozenset[str]:
    """区切りのID一覧（JSON配列）を集合にする。形が違えば空。"""
    if not isinstance(value, list):
        return frozenset()
    return frozenset(str(v) for v in value if isinstance(v, str) and v)


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


class _ReloadWatch:
    """保存先の変化を見て、一覧の作り直しが要るかを決める。

    実行中は作り直せない（reloadCommands 側も断る）。変化を見た時点で
    「見た」ことにすると、実行中に保存した分を取りこぼすため、作り直せた
    ときにだけ見た時刻を進める。
    """

    def __init__(self, mtime: Callable[[], float] = lambda: _py_dir_mtime()) -> None:
        self._mtime = mtime
        self._seen = mtime()

    def tick(self, busy: bool) -> bool:
        """作り直すべきなら真（呼び出し側が作り直す）。"""
        try:
            cur = self._mtime()
        except Exception:
            return False
        if cur == self._seen or busy:
            return False
        self._seen = cur
        return True


#: 見回りの間隔(ms)。試し実行の操作（停止・一時停止）を待たせないよう短くする。
POLL_MS = 100
#: 保存先を見に行く間隔（見回り何回ごとか）。約1秒。
RELOAD_EVERY_TICKS = 10


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
    run_host: Any = None,
) -> None:
    """エディタを開く。終わったら一覧を作り直す。

    run_host は試し実行の司令塔（本体）。start_external_command・stopPlay・
    set_running_paused・syncPauseState・runner を持つ。無ければ試し実行しない。
    """
    import tkinter as tk
    import tkinter.messagebox as tkmsg

    if is_busy():
        print("実行中はBlocklyエディタを開けません")
        tkmsg.showwarning("Blockly", "実行中はBlocklyエディタを開けません", parent=root)
        return
    global _GET_FRAME
    _GET_FRAME = get_frame
    _set_run_host(run_host)
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

    watch = _ReloadWatch()
    ticks = 0

    def poll() -> None:
        """GUI スレッドの見回り。受け口から頼まれた仕事と一覧の作り直しを行う。

        サーバスレッドから tkinter を触れないため、試し実行の開始・停止は
        待ち行列で受け、ここで片付ける。保存先の変化は約1秒ごとに見る。
        窓が閉じたら連鎖を止める。
        """
        nonlocal ticks
        try:
            alive = bool(win.winfo_exists())
        except Exception:
            return
        if not alive:
            return
        try:
            drain_gui_calls()
        except Exception:
            pass
        sync = getattr(run_host, "syncPauseState", None)
        if callable(sync):
            try:
                sync()
            except Exception:
                pass
        ticks += 1
        if ticks >= RELOAD_EVERY_TICKS:
            ticks = 0
            if watch.tick(busy=is_busy()):
                reload_commands()
        try:
            win.after(POLL_MS, poll)
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
    win.after(POLL_MS, poll)
