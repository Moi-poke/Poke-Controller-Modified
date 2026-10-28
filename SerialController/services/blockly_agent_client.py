#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blockly エディタの受け口へ繋ぐローカル HTTP クライアント（GUI非依存）。

`tools/blockly_agent.py` の `trial`/`frame` が、GUIを import せずに
`/frame`・`/run/state`・`/run`・`/run/control` を使うための薄い層。
相手はユーザーが開いているエディタ（localhost のみ）に限る。

外部依存は標準ライブラリだけ。通信の失敗・不正な応答・`ok:false` は
日本語の理由を持つ `ValueError` にそろえ、CLI 側がそのまま出せるようにする。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: 相手にしてよいホスト名。エディタは 127.0.0.1/localhost で待つ。
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})

#: 応答本文の上限。フレームPNG（10MB）と状態JSONを見込む。
MAX_RESPONSE_BYTES = 12 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """転送は追わない（localhost の外へ出る経路を作らない）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def parse_origin(value: str) -> str:
    """`http://127.0.0.1:<port>` / `http://localhost:<port>` を検査して返す。

    資格情報・パス・問い合わせ・断片・別ホスト・https は拒否する。
    末尾の `/` だけは許す。おかしければ `ValueError`。
    """
    text = str(value).strip()
    if not text:
        raise ValueError("接続先（--server http://127.0.0.1:<port>）を書いてください")
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() != "http":
        raise ValueError("接続先は http://127.0.0.1:<port> の形で書いてください")
    if parts.username or parts.password:
        raise ValueError("接続先に資格情報は書けません")
    host = (parts.hostname or "").lower()
    if host not in LOCAL_HOSTS:
        raise ValueError("接続先は localhost（127.0.0.1 / localhost）だけです")
    if parts.query or parts.fragment:
        raise ValueError("接続先に問い合わせや断片は書けません")
    if parts.path not in ("", "/"):
        raise ValueError("接続先は origin だけにしてください（パスは書けません）")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("接続先のポート番号が不正です") from None
    if port is None:
        raise ValueError("接続先にポート番号を書いてください")
    return f"http://{host}:{port}"


class EditorClient:
    """エディタの受け口へ繋ぐ。接続先は origin だけを持つ。"""

    def __init__(self, base_url: str, *, timeout: float = 5.0) -> None:
        self.origin = parse_origin(base_url)
        self.timeout = float(timeout)
        if not (self.timeout > 0):
            raise ValueError("timeout は正の秒数で書いてください")
        self._opener = _opener()

    # -- 低レベル -------------------------------------------------------------

    def _url(self, path: str) -> str:
        return self.origin + path

    def _get(self, path: str, *, accept: str = "application/json") -> bytes:
        req = urllib.request.Request(
            self._url(path), method="GET", headers={"Accept": accept}
        )
        return self._send(req)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._post_raw(path, payload)
        if response.get("ok") is not True:
            message = response.get("message")
            raise ValueError(
                str(message)
                if isinstance(message, str) and message
                else "エディタが失敗を返しました"
            )
        return response

    def _post_raw(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._url(path),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": self.origin,
            },
        )
        return self._json_raw(self._send(req))

    def _send(self, req: urllib.request.Request) -> bytes:
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("エディタの応答が大きすぎます")
                return raw
        except urllib.error.HTTPError as e:
            # 応答本文に理由が入っていることがある（403 等）。
            detail = ""
            try:
                payload = json.loads(e.read(4096).decode("utf-8", "replace"))
                if isinstance(payload, dict) and isinstance(
                    payload.get("message"), str
                ):
                    detail = payload["message"]
            except Exception:
                detail = ""
            raise ValueError(
                f"エディタが要求を受け付けませんでした（HTTP {e.code}）"
                + (f": {detail}" if detail else "")
            ) from None
        except urllib.error.URLError as e:
            raise ValueError(
                "エディタに繋がりません（本体とBlocklyエディタが開いているか、"
                f"URLが正しいか確認してください）: {e.reason}"
            ) from None
        except TimeoutError:
            raise ValueError("エディタの応答がタイムアウトしました") from None
        except OSError as e:
            raise ValueError(f"エディタと通信できません: {e}") from None

    def _json_raw(self, raw: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("エディタの応答を JSON として読めません") from None
        if not isinstance(payload, dict):
            raise ValueError("エディタの応答がオブジェクトではありません")
        return payload

    def _json(self, raw: bytes) -> dict[str, Any]:
        payload = self._json_raw(raw)
        if payload.get("ok") is not True:
            message = payload.get("message")
            raise ValueError(
                str(message)
                if isinstance(message, str) and message
                else "エディタが失敗を返しました"
            )
        return payload

    # -- 公開 ----------------------------------------------------------------

    def frame(self) -> bytes:
        """`/frame` の生PNGを返す。画像でなければ `ValueError`。"""
        raw = self._get("/frame", accept="image/png")
        if not raw:
            raise ValueError("エディタのフレームが空です")
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("エディタのフレームが PNG ではありません")
        return raw

    def state(self, since: int = 0) -> dict[str, Any]:
        """`/run/state?since=<seq>` を読む。"""
        seq = int(since)
        if seq < 0:
            seq = 0
        return self._json(self._get(f"/run/state?since={seq}"))

    def start_trial(self, code: str, *, max_seconds: float) -> dict[str, Any]:
        """`/run` で時間上限つきの試し実行を始める。"""
        seconds = float(max_seconds)
        if not (seconds > 0):
            raise ValueError("試し実行の上限は正の秒数で書いてください")
        return self._post(
            "/run", {"code": str(code), "breakpoints": [], "maxSeconds": seconds}
        )

    def control(self, run_id: int, action: str) -> dict[str, Any]:
        """`/run/control` を runId 付きで送る。"""
        rid = int(run_id)
        if rid <= 0:
            raise ValueError("runId は正の整数で書いてください")
        return self._post("/run/control", {"action": str(action), "runId": rid})

    def live_state(self) -> dict[str, Any]:
        """開いているエディタの最新workspaceと版を読む（`ok:false` も返す）。"""
        return self._json_raw(self._get("/live/state"))

    def live_patch(self, operations: Any, base_revision: str) -> dict[str, Any]:
        """版を指定して、開いているエディタへパッチを渡す（`ok:false` も返す）。"""
        revision = str(base_revision).strip()
        if not revision:
            raise ValueError("baseRevision を書いてください")
        return self._post_raw(
            "/live/patch",
            {"operations": operations, "baseRevision": revision},
        )
