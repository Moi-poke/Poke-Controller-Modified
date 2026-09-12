#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_latency.py - 押下→検知の遅延実測.

PokeConがボタンを押してから、その音が取り込みに現れるまでの時間を
測る。Switch本体の操作確認画面など、押すと音が出る画面で使う。
T0（送信直後）もT1（検知）も自時計で取るため、loopback配線は要らない。
shorter is better の比較用（入力デバイスの切り替え評価など）。

検出は立ち上がり（RMSの跳ね）で見る。テンプレート不要で、
どの画面音にも使える。BGMがうるさい画面では誤検出するため、
静かな確認画面での使用が前提。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

# 立ち上がり検出の既定値。
ONSET_RATIO = 4.0  # 直前 baseline の何倍で立ち上がりとみなすか
ONSET_FLOOR = 0.005  # 無音時の下駄（RMS）
ONSET_HOP = 2048  # 走査の刻み（件数）
ONSET_WIN = 2205  # 判定窓（件数。50ms）
ONSET_TRIES = 3  # 押下回数（中央値を取る）
ONSET_SETTLE = 1.2  # 押してから読むまでの待ち（秒）


def window_rms(window: np.ndarray) -> float:
    """窓の実効値。空なら 0。"""
    if window.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(window.astype(np.float64) ** 2)))


def onset_absolute(
    window: np.ndarray,
    total: int,
    baseline_rms: float,
    from_absolute: int = 0,
    ratio: float = ONSET_RATIO,
    floor: float = ONSET_FLOOR,
    hop: int = ONSET_HOP,
    win: int = ONSET_WIN,
) -> int | None:
    """立ち上がりの先頭を通算件数で返す。無ければ None。

    window は通算 [total - size, total) の複製。from_absolute より
    古い物は探さない。baseline_rms の ratio 倍か floor を超えた
    最初の窓を立ち上がりとする。
    """
    size = int(window.size)
    if size <= 0 or win <= 0 or hop <= 0:
        return None
    threshold = max(float(baseline_rms) * float(ratio), float(floor))
    base = int(total) - size
    start = max(0, int(from_absolute) - base)
    pos = start
    while pos + win <= size:
        if window_rms(window[pos : pos + win]) >= threshold:
            return base + pos
        pos += hop
    return None


def summarize(delays_ms: Sequence[float], total: int = ONSET_TRIES) -> dict[str, Any]:
    """計測値のまとめ。空なら detected=0。"""
    values = sorted(float(v) for v in delays_ms)
    if not values:
        return {"detected": 0, "total": int(total), "median_ms": -1.0}
    mid = values[len(values) // 2]
    return {"detected": len(values), "total": int(total), "median_ms": mid}


def absolute_at(t0: float, tap: Sequence[tuple[float, int]], rate: int) -> int | None:
    """perf_counter時刻に対応する通算件数。Tapからの内挿。

    locate_played（通算→時刻）の逆向き。連打の各押下時刻を
    収録波形上の位置へ直すときに使う。Tapより後の時刻は None。
    """
    want = float(t0)
    denom = float(max(1, int(rate)))
    for t_cb, total_after in tap:
        if float(t_cb) >= want:
            return int(total_after - (float(t_cb) - want) * denom)
    return None


def count_onsets(
    window: np.ndarray,
    rate: int,
    refractory_s: float = 0.06,
    baseline_s: float = 0.5,
    ratio: float = ONSET_RATIO,
    floor: float = ONSET_FLOOR,
) -> list[int]:
    """全立ち上がり位置を通算件数で返す。不応期で間引く。

    window は先頭からの通算配列（先頭=0件目）。baseline は先頭
    baseline_s 秒の静けさから取るため、収録は押下より前に
    余白を置くこと。不応期より近い2発は1件に畳む（二重計数防止）。

    50ms窓の跳ね検出（onset_absolute）の繰り返しでは、音の尻尾で
    再検出して水増しする。ここでは5ms包絡の極大を拾う。減衰する
    クリック音は極大が1つなので、尻尾で二重計数しない。
    """
    x = np.asarray(window, dtype=np.float64).ravel()
    if x.size == 0:
        return []
    denom = max(1, int(rate))
    total = int(x.size)
    base_n = max(1, min(total, int(denom * float(baseline_s))))
    baseline = window_rms(x[:base_n])
    threshold = max(float(baseline) * float(ratio), float(floor))
    width = max(64, int(denom * 0.005))
    if x.size <= width:
        return []
    sq = x * x
    cumsum = np.concatenate(([0.0], np.cumsum(sq)))
    env = np.sqrt((cumsum[width:] - cumsum[:-width]) / float(width))
    refractory = max(1, int(denom * float(refractory_s)))
    found: list[int] = []
    pos = 0
    size = int(env.size)
    while pos < size:
        if env[pos] >= threshold:
            end = min(size, pos + refractory)
            peak = pos + int(np.argmax(env[pos:end]))
            found.append(peak)
            pos = peak + refractory
        else:
            pos += 1
    return found


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


def measure_press(
    send_press: Callable[[], float],
    capture: Any,
    tries: int = ONSET_TRIES,
) -> dict[str, Any]:
    """押下→検知を繰り返し、遅延の中央値を返す。裏スレッドで呼ぶこと。

    send_press は押して送信直後の perf_counter を返す関数。
    capture は readWindow / read_stamped を持つ音声源。
    戻り値は summarize 形に delays_ms を足した物。例外は握って
    error 入りで返す（呼び出し側で表示する）。
    """
    delays: list[float] = []
    try:
        for _ in range(max(1, int(tries))):
            # 直前の静けさを baseline にする（押す前の0.3秒）。
            baseline = 0.0
            try:
                before = capture.readWindow(0.3)
                if before is not None:
                    baseline = window_rms(before)
            except Exception:
                baseline = 0.0
            try:
                t0 = float(send_press())
            except Exception as e:
                return {
                    "detected": len(delays),
                    "total": int(tries),
                    "median_ms": -1.0,
                    "delays_ms": [],
                    "error": f"送信に失敗しました: {e}",
                }
            time.sleep(ONSET_SETTLE)
            try:
                stamped = capture.read_stamped(2.0)
            except Exception:
                stamped = None
            if stamped is None:
                continue
            window, total, tap, in_latency, in_rate = stamped
            rate = int(in_rate)
            # 送信時より古い物は探さない（0.3秒の余裕を見る）。
            search_from = int(total - (time.perf_counter() - t0 + 0.3) * rate)
            at = onset_absolute(window, total, baseline, search_from)
            if at is None:
                continue
            got_at = locate_played(tap, at, in_latency, rate)
            if got_at is None:
                continue
            delays.append((got_at - t0) * 1000.0)
    except Exception as e:
        return {
            "detected": len(delays),
            "total": int(tries),
            "median_ms": -1.0,
            "delays_ms": [round(v, 1) for v in delays],
            "error": f"計測に失敗しました: {e}",
        }
    result = summarize(delays, int(tries))
    result["delays_ms"] = [round(v, 1) for v in delays]
    return result
