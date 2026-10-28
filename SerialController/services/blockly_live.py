#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""開いているBlocklyへライブでパッチを渡す仲介（GUI非依存）。

ブラウザは一定間隔で `next_request` を引き、自分のワークスペースの版
（revision）が要求の `baseRevision` と一致するときだけ適用する。人間が
先に編集していれば版が変わるため、適用せず conflict を返す。これで
「AIの変更で人の編集を潰す」事故を防ぐ。

保存はしない。適用結果はブラウザが返し、CLIはそれを待つ。
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any

#: ブラウザが名乗ってから他のタブに譲るまでの猶予（秒）。
LEASE_SECONDS = 5.0

#: 要求がブラウザに届かないまま失効するまで（秒）。
REQUEST_SECONDS = 10.0


class LiveBridge:
    """1つのエディタと1つのCLI要求をつなぐ。スレッド間で共有してよい。"""

    def __init__(self, *, clock: Any = time.monotonic, wall: Any = time.time) -> None:
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._client = ""
        self._client_at = 0.0
        self._snapshot: dict[str, Any] | None = None
        self._queue: collections.deque[dict[str, Any]] = collections.deque()
        self._active: dict[str, Any] | None = None
        self._results: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    # -- ブラウザ側 ----------------------------------------------------------

    def update(
        self,
        client: str,
        revision: str,
        workspace: dict[str, Any],
        dirty: bool,
    ) -> bool:
        """ブラウザの現在のワークスペースを記録する。貸与中なら False。"""
        now = self._clock()
        with self._lock:
            if (
                self._client
                and self._client != client
                and now - self._client_at < LEASE_SECONDS
            ):
                return False
            self._client = client
            self._client_at = now
            self._snapshot = {
                "revision": revision,
                "workspace": workspace,
                "dirty": bool(dirty),
                "client": client,
                "at": now,
            }
            return True

    def next_request(self, client: str) -> dict[str, Any]:
        """ブラウザに次の要求を1つ渡す。貸与が他タブにあれば leased=False。"""
        now = self._clock()
        with self._lock:
            if self._client and self._client != client:
                if now - self._client_at < LEASE_SECONDS:
                    return {"leased": False, "request": None}
                self._client = client
            self._client = client
            self._client_at = now
            self._expire_locked(now)
            if self._active is not None:
                return {"leased": True, "request": None}
            if not self._queue:
                return {"leased": True, "request": None}
            request = self._queue.popleft()
            self._active = request
            return {"leased": True, "request": request}

    def complete(
        self,
        client: str,
        request_id: int,
        *,
        ok: bool,
        revision: str = "",
        workspace: dict[str, Any] | None = None,
        error: str = "",
        status: str = "",
    ) -> bool:
        """ブラウザの適用結果を受ける。要求が切り替わっていれば False。"""
        with self._cond:
            active = self._active
            if active is None or int(active["id"]) != int(request_id):
                return False
            if self._client and self._client != client:
                return False
            self._active = None
            if revision and isinstance(workspace, dict):
                self._snapshot = {
                    "revision": revision,
                    "workspace": workspace,
                    "dirty": True,
                    "client": client,
                    "at": self._clock(),
                }
            self._results[int(request_id)] = {
                "ok": bool(ok),
                "revision": revision,
                "workspace": workspace,
                "error": error,
                "status": status or ("applied" if ok else "failed"),
            }
            self._cond.notify_all()
            return True

    # -- CLI側 ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            if self._snapshot is None:
                return None
            return {
                "revision": self._snapshot["revision"],
                "workspace": self._snapshot["workspace"],
                "dirty": self._snapshot["dirty"],
                "client": self._snapshot["client"],
                "queued": len(self._queue),
                "active": int(self._active["id"]) if self._active else 0,
            }

    def enqueue(
        self,
        operations: list[dict[str, Any]],
        base_revision: str,
        *,
        mode: str = "ops",
        workspace: dict[str, Any] | None = None,
    ) -> tuple[int | None, str]:
        """要求を積む。ブラウザが居なければ (None, 理由)。

        `mode="ops"` はフィールドだけを差分適用する。`mode="load"` は
        構造変更（追加・接続・削除）で、検証済みworkspace全体を読み込む。
        """
        now = self._clock()
        with self._cond:
            if self._snapshot is None or not self._client:
                return (
                    None,
                    "エディタの状態がまだありません（開いて少し待ってください）",
                )
            if now - self._client_at > LEASE_SECONDS:
                return None, "エディタが応答していません（開き直してください）"
            if self._snapshot["revision"] != base_revision:
                return None, "エディタの内容が変わっています（読み直してください）"
            request = {
                "id": self._next_id,
                "mode": mode,
                "ops": operations,
                "baseRevision": base_revision,
                # ブラウザ側の Date.now()（ミリ秒）と比べられるよう、実時刻の
                # ミリ秒で渡す。
                "deadline": self._wall() * 1000.0 + REQUEST_SECONDS * 1000.0,
            }
            if mode == "load":
                request["workspace"] = workspace
            self._next_id += 1
            self._queue.append(request)
            self._cond.notify_all()
            return int(request["id"]), ""

    def wait(self, request_id: int, *, timeout: float) -> dict[str, Any]:
        """結果を待つ。来なければ timeout を返す。"""
        deadline = self._clock() + max(0.0, float(timeout))
        with self._cond:
            while int(request_id) not in self._results:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    # ブラウザが結果を返さないまま待ち切った。要求を握ったまま
                    # にすると次のパッチが永久に詰まるため、ここで手放す。
                    if self._active is not None and int(self._active["id"]) == int(
                        request_id
                    ):
                        self._active = None
                    self._results.pop(int(request_id), None)
                    return {
                        "ok": False,
                        "status": "timeout",
                        "error": "応答待ちがタイムアウトしました",
                    }
                self._cond.wait(min(remaining, 0.5))
            return self._results.pop(int(request_id))

    def reset(self) -> None:
        """試験用に空へ戻す。"""
        with self._cond:
            self._client = ""
            self._client_at = 0.0
            self._snapshot = None
            self._queue.clear()
            self._active = None
            self._results.clear()
            self._next_id = 1
            self._cond.notify_all()

    # -- 内部 ----------------------------------------------------------------

    def _expire_locked(self, now: float) -> None:
        if self._active is not None:
            active = self._active
            if self._wall() * 1000.0 >= float(active.get("deadline", 0.0)):
                self._active = None
                self._results[int(active["id"])] = {
                    "ok": False,
                    "status": "expired",
                    "error": "エディタが要求を受け取れませんでした",
                }
                self._cond.notify_all()
        while self._queue:
            request = self._queue[0]
            if self._wall() * 1000.0 < float(request.get("deadline", 0.0)):
                break
            self._queue.popleft()
            self._results[int(request["id"])] = {
                "ok": False,
                "status": "expired",
                "error": "エディタが要求を受け取れませんでした",
            }
            self._cond.notify_all()
