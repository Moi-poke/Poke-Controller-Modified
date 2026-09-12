#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_scheduler.py - Pico live送出の時刻門（エッジ列＋最低保持）。

mailbox（容量1・上書き）の置換である。上書きは8ms未満の押下を
消すため、エッジは列に溜め、送出開始から最低保持秒が経つまで
次のエッジへ進めない。短い押下は押下側が延長される（要求より
短くは絶対にしない）。スティックのみの差分は畳んでよい
（legacyの間引きと同じ基準。ボタンのエッジは絶対に捨てない）。

時計を持たない。時刻は引数でもらう（検証は仮想時刻を通す）。
排他は自前ロックで行う（workerと申告スレッドが同時に触る）。
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class LiveScheduler:
    """送出する状態の順序と最低保持を守る門。"""

    def __init__(
        self,
        slot_s: float = 0.008,
        min_dwell_s: float = 0.016,
        capacity: int = 32,
    ) -> None:
        self._slot_s = float(slot_s)
        self._min_dwell_s = float(min_dwell_s)
        self._pending: deque[dict[str, Any]] = deque()
        self._capacity = max(1, int(capacity))
        self._current: dict[str, Any] | None = None
        self._current_sent_at: float | None = None
        self._put = 0
        self._merged = 0
        self._dropped = 0
        self._lock = threading.Lock()

    @staticmethod
    def _stick_only(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
        """btnとhatが等しければスティックのみの差分とみなす。"""
        try:
            return int(prev["btn"]) == int(nxt["btn"]) and int(prev["hat"]) == int(
                nxt["hat"]
            )
        except (KeyError, TypeError, ValueError):
            return False

    def push(self, snap: dict[str, Any]) -> None:
        """1エッジを列へ積む。捨てずに数える（溢れは最古を捨て計数）。"""
        with self._lock:
            self._put += 1
            if self._pending and self._stick_only(self._pending[-1], snap):
                self._pending[-1] = dict(snap)
                self._merged += 1
                return
            if len(self._pending) >= self._capacity:
                self._pending.popleft()
                self._dropped += 1
            self._pending.append(dict(snap))

    def advance(self, now: float) -> None:
        """dwell満了なら最古を送出中へ進める。満了前は何もしない。"""
        with self._lock:
            if not self._pending:
                return
            if (
                self._current is not None
                and self._current_sent_at is not None
                and float(now) - self._current_sent_at < self._min_dwell_s
            ):
                return
            self._current = self._pending.popleft()
            self._current_sent_at = float(now)

    def current(self) -> dict[str, Any] | None:
        """いま送出すべき状態。無ければNone（workerは送らない）。"""
        with self._lock:
            return None if self._current is None else dict(self._current)

    def clear(self) -> bool:
        """未送出を破棄する。破棄物があればTrue（discardLive用）。"""
        with self._lock:
            had = bool(self._pending)
            self._pending.clear()
            return had

    def pending_empty(self) -> bool:
        with self._lock:
            return not self._pending

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"put": self._put, "merged": self._merged, "dropped": self._dropped}

    def reset_stats(self) -> None:
        """put/merged/droppedの数えを0へ戻す（列と送出中は残す）。"""
        with self._lock:
            self._put = 0
            self._merged = 0
            self._dropped = 0

    def set_min_dwell(self, seconds: float) -> None:
        """最低保持を変える。8ms〜64ms以外は無視（現状維持）。"""
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if not 0.008 <= value <= 0.064:
            return
        with self._lock:
            self._min_dwell_s = value
