#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_scheduler.py - Pico live送出の時刻門（エッジ列＋最低保持）。

mailbox（容量1・上書き）の置換である。上書きは8ms未満の押下を
消すため、エッジは列に溜め、送出開始から最低保持秒が経つまで
次のエッジへ進めない。短い押下は押下側が延長される（要求より
短くは絶対にしない）。スティックのみの差分は畳んでよい
（legacyの間引きと同じ基準。ボタンのエッジは絶対に捨てない）。

未送出の中立は、異なる押下が来たら落とす。ワイヤに出ていない
中立を落としても見た目は変わらず、レガシー（状態行の上書き）と
同等の遷移になる。同ボタンの再押下では残す（中立の可視性が要る
連打のため）。ただし中立の age（申告からの経過）が 1 dwell 未満
なら落として融合する。出ていない中立を短い保持へ延ばしても見え
方は変わらず、周期だけが延びる（背中合わせ連打の累積遅延の原因）。
融合した中立は merged に数える（dropped ではない）。

時計を持たない。時刻は引数でもらう（検証は仮想時刻を通す）。
排他は自前ロックで行う（workerと申告スレッドが同時に触る）。
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

# 中立の値。Sender の姿勢初期値（POSTURE_CENTER / POSTURE_HAT_CENTER）と
# 同じ値を使う。Keys の CENTER / Hat.CENTER とも一致する。
_NEUTRAL_HAT = 8
_NEUTRAL_AXIS = 128


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
        # 未送出の列。各要素は (姿勢, 申告時刻)。時刻は融合判定
        # （未送出の中立の age）に使う。申告側が時刻を渡さない
        # 場合は -inf とし、age による融合はしない（従来動作）。
        self._pending: deque[tuple[dict[str, Any], float]] = deque()
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

    @staticmethod
    def _is_full_neutral(snap: dict[str, Any]) -> bool:
        """全項目が中立か。押下も倒しも無い状態とみなす。"""
        try:
            return (
                int(snap["btn"]) == 0
                and int(snap["hat"]) == _NEUTRAL_HAT
                and int(snap["lx"]) == _NEUTRAL_AXIS
                and int(snap["ly"]) == _NEUTRAL_AXIS
                and int(snap["rx"]) == _NEUTRAL_AXIS
                and int(snap["ry"]) == _NEUTRAL_AXIS
            )
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _same_content(left: dict[str, Any], right: dict[str, Any]) -> bool:
        """姿勢の6項目が等しいか。revision（順序情報）は比べない。"""
        try:
            return all(
                int(left[key]) == int(right[key])
                for key in ("btn", "hat", "lx", "ly", "rx", "ry")
            )
        except (KeyError, TypeError, ValueError):
            return False

    def push(self, snap: dict[str, Any], now: float | None = None) -> None:
        """1エッジを列へ積む。捨てずに数える（溢れは最古を捨て計数）。

        now は申告時刻（sender が perf_counter を渡す）。未送出の
        全中立の age 判定に使う。省略時は age による融合をしない。
        """
        try:
            at = float(now) if now is not None else float("-inf")
        except (TypeError, ValueError):
            at = float("-inf")
        with self._lock:
            self._put += 1
            if self._pending and self._stick_only(self._pending[-1][0], snap):
                self._pending[-1] = (dict(snap), at)
                self._merged += 1
                return
            if (
                self._pending
                and self._is_full_neutral(self._pending[-1][0])
                and not self._is_full_neutral(snap)
            ):
                # 末尾の中立はまだ送出していない。その後に異なる状態が
                # 来たら、中立を挟まず直接遷移してよい（出ていない物は
                # 落としても見た目が変わらない）。直近の非中立と同じ
                # 内容の再押下は残す（中立の可視性が要る連打のため）。
                # ただし中立の age が 1 dwell 未満なら落として融合する。
                # 出ていない中立を短い保持へ延ばしても見え方は変わらず、
                # 周期だけが延びる（背中合わせ連打の累積遅延の原因）。
                # 参照は列内の新しい方から探し、無ければ送出中を見る。
                # どちらにも無ければ（起動直後など）残す。
                ref: dict[str, Any] | None = None
                for older, _ in reversed(list(self._pending)[:-1]):
                    if not self._is_full_neutral(older):
                        ref = older
                        break
                if ref is None and self._current is not None:
                    if not self._is_full_neutral(self._current):
                        ref = self._current
                tail_at = self._pending[-1][1]
                if ref is not None and not self._same_content(ref, snap):
                    self._pending[-1] = (dict(snap), at)
                    self._merged += 1
                    return
                if (
                    ref is not None
                    and self._same_content(ref, snap)
                    and at - tail_at < self._min_dwell_s
                ):
                    self._pending[-1] = (dict(snap), at)
                    self._merged += 1
                    return
            if len(self._pending) >= self._capacity:
                self._pending.popleft()
                self._dropped += 1
            self._pending.append((dict(snap), at))

    def advance(self, now: float) -> None:
        """dwell満了なら最古を送出中へ進める。満了前は何もしない。

        進めるとき、先頭の未送出中立より後ろに非中立があれば中立を
        飛ばす（mergedに数える）。元スクリプト（生ser直書き）は状態を
        上書きし中立を挟まないため、追い越された中立を送出すると周期
        だけが延びる。後続がない中立は飛ばさない（解放の到達保証）。
        """
        with self._lock:
            if not self._pending:
                return
            if (
                self._current is not None
                and self._current_sent_at is not None
                and float(now) - self._current_sent_at < self._min_dwell_s
            ):
                return
            while (
                len(self._pending) > 1
                and self._is_full_neutral(self._pending[0][0])
                and any(
                    not self._is_full_neutral(snap)
                    for snap, _ in list(self._pending)[1:]
                )
            ):
                self._pending.popleft()
                self._merged += 1
            self._current, _ = self._pending.popleft()
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
