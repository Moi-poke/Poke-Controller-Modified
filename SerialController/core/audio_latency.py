#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_latency.py - 往復遅延の実測.

選択中の出力へ計測ブリップ（チャープ）を鳴らし、選択中の入力で
相互相関検出して片道遅延の中央値を返す。shorter is better の比較用。
sounddevice も tkinter もここでは読まないわけにいかない部分だけに留め、
検出・時刻計算は純粋関数に切り出して合成波で検証する。

時刻の考え方（どちらも perf_counter 領域）:
  再生側: write() 直前の時刻 ＋ 出力遅延 ＋ チャープ先頭までの秒数
  収録側: 入力コールバック時刻 － 入力遅延 － 塊末尾からの秒数
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import numpy as np
from core.AudioCapture import AUDIO_RATE, to_device
from scipy import signal

# 計測ブリップの形。短い上昇チャープ（部屋・機器の癖に強い）。
CHIRP_SECONDS = 0.05
CHIRP_F0 = 2000.0
CHIRP_F1 = 4000.0
CHIRP_GAP = 0.8
CHIRP_COUNT = 5
# 検出の敷居。正規化相関の頂点がこの値以上、かつ中央値の3倍以上。
DETECT_THRESHOLD = 0.6
DETECT_PROMINENCE = 3.0


def make_chirp(rate: int) -> np.ndarray:
    """計測ブリップ（両端を丸めた上昇チャープ）。"""
    count = max(1, int(float(rate) * CHIRP_SECONDS))
    t = np.arange(count, dtype=np.float64) / float(rate)
    phase = (
        2.0
        * np.pi
        * (CHIRP_F0 * t + (CHIRP_F1 - CHIRP_F0) * t * t / (2.0 * CHIRP_SECONDS))
    )
    wave = 0.8 * np.sin(phase).astype(np.float32)
    taper = np.hanning(count).astype(np.float32)
    return (wave * taper).astype(np.float32)


def find_impulse(
    hay: np.ndarray,
    needle: np.ndarray,
    threshold: float = DETECT_THRESHOLD,
    start: int = 0,
) -> int | None:
    """needle の先頭位置を返す。無ければ None。

    正規化相互相関の頂点で見る。頂点が閾値未満、または山が立って
    いなければ（頂点が中央値の3倍未満）検出なしとする。
    start 以前は探さない（前回ぶんの除外用）。
    """
    hay = np.asarray(hay, dtype=np.float64).ravel()
    needle = np.asarray(needle, dtype=np.float64).ravel()
    if hay.size == 0 or needle.size == 0 or hay.size < needle.size:
        return None
    start = max(0, min(int(start), hay.size - 1))
    hay_z = hay - hay.mean()
    needle_z = needle - needle.mean()
    denom = float(np.linalg.norm(hay_z) * np.linalg.norm(needle_z))
    if denom <= 0.0:
        return None
    corr = np.abs(signal.correlate(hay_z, needle_z, mode="valid"))
    tail = corr[start:]
    if tail.size == 0:
        return None
    peak = int(np.argmax(tail)) + start
    peak_val = float(corr[peak]) / denom
    median = float(np.median(tail)) / denom
    if peak_val < threshold:
        return None
    if median > 0.0 and peak_val < median * DETECT_PROMINENCE:
        return None
    return peak


def play_time(t_write: float, out_latency: float, offset: int, rate: int) -> float:
    """チャープ先頭が鳴る時刻の推定。"""
    return t_write + float(out_latency) + float(offset) / float(rate)


def summarize(delays_ms: Sequence[float]) -> dict[str, Any]:
    """計測値のまとめ。空なら detected=0。"""
    values = sorted(float(v) for v in delays_ms)
    if not values:
        return {"detected": 0, "total": CHIRP_COUNT, "median_ms": -1.0}
    mid = values[len(values) // 2]
    return {"detected": len(values), "total": CHIRP_COUNT, "median_ms": mid}


def locate_played(
    tap: Sequence[tuple[float, int]],
    absolute: int,
    in_latency: float,
    in_rate: int,
) -> float | None:
    """通算 absolute 件目の取り込み時刻。Tapから内挿する。無ければ None。"""
    for t_cb, total_after in tap:
        if total_after > absolute:
            return t_cb - (total_after - absolute) / float(in_rate) - float(in_latency)
    return None


def pair_delays(
    play_times: Sequence[float],
    found_caps: Sequence[float],
    lo_s: float = -0.05,
    hi_s: float = 2.0,
) -> list[float]:
    """発射と検出を時刻近接で突き合わせる。順に貪欲に組にする。

    順番固定の突き合わせは、1件の見逃し・誤検出で後続すべてが
    ずれる。時刻が近い物同士で組めば、欠けても残りは正しく組める。
    窓外（-50ms〜2s）は組まない。
    """
    plays = sorted(float(p) for p in play_times)
    caps = sorted(float(c) for c in found_caps)
    used = [False] * len(plays)
    delays: list[float] = []
    for cap in caps:
        best = -1
        best_abs = 0.0
        for i, play in enumerate(plays):
            if used[i]:
                continue
            delta = cap - play
            if delta < lo_s:
                break
            if delta <= hi_s and (best < 0 or abs(delta) < best_abs):
                best = i
                best_abs = abs(delta)
        if best >= 0:
            used[best] = True
            delays.append((cap - plays[best]) * 1000.0)
    return delays


def to_device_chirp(needle44: np.ndarray, out_rate: int) -> np.ndarray:
    """検出用チャープを出力レートへ直す。件数は比例配分する。"""
    count = max(1, int(round(needle44.size * float(out_rate) / AUDIO_RATE)))
    return to_device(needle44, int(out_rate), count)


class Emitter:
    """コールバック給電の発射器。play_times に鳴らし始め推定を積む。

    blocking write では返りのタイミングが振れて中央値が暴れるため、
    モニターと同じ給電形にする。鳴らし始めはコールバック時刻＋
    出力遅延で推定する（機器ごとの申告ずれは定数バイアスとして残る。
    順位付けには影響しない）。
    """

    def __init__(
        self,
        chirp: np.ndarray,
        rate: int,
        out_latency: float,
        count: int = CHIRP_COUNT,
        gap_s: float = CHIRP_GAP,
        prime_s: float = 0.5,
    ) -> None:
        self._chirp = np.asarray(chirp, dtype=np.float32).ravel()
        self._rate = int(rate)
        self._out_latency = float(out_latency)
        self._left = int(count)
        self._gap = max(0, int(float(gap_s) * rate))
        self._pending: np.ndarray = np.zeros(0, dtype=np.float32)
        # 開いた直後のコールバックは遅れがちで、初発が欠けて
        # 検出不能になる。無音で慣らしてから鳴らし始める。
        self._gap_left = max(0, int(float(prime_s) * rate))
        self.play_times: list[float] = []
        self.emitted = 0
        self.underruns = 0

    @property
    def done(self) -> bool:
        """発射しきったか（鳴り終わりは別途待つ）。"""
        return self._left <= 0 and self._pending.size == 0

    def callback(self, outdata: Any, frames: int, _time: Any, status: Any) -> None:
        """PortAudioスレッドから呼ばれる。無音埋め＋順に発射する。"""
        if status and getattr(status, "output_underflow", False):
            self.underruns += 1
        t_cb = time.perf_counter()
        buf = np.asarray(outdata).ravel()
        pos = 0
        while pos < frames:
            if self._pending.size == 0:
                if self._left <= 0:
                    break
                if self._gap_left > 0:
                    step = min(self._gap_left, frames - pos)
                    self._gap_left -= step
                    pos += step
                    continue
                self._pending = self._chirp.copy()
                self.play_times.append(t_cb + self._out_latency)
                self._left -= 1
                self.emitted += 1
            step = min(self._pending.size, frames - pos)
            buf[pos : pos + step] = self._pending[:step]
            self._pending = self._pending[step:]
            pos += step
            if self._pending.size == 0:
                self._gap_left = self._gap
        if pos < frames:
            buf[pos:] = 0.0
