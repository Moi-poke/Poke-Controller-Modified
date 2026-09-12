"""連打の立ち上がり計数の検証。合成波で回す（実機不要）。"""

import numpy as np
from core import audio_latency as AL

RATE = 44100


def _clicks(times_s: list[float], amp: float = 0.5) -> np.ndarray:
    """指定時刻にクリック（30ms正弦バースト）を置いた3秒波形。"""
    x = np.zeros(int(3.0 * RATE), dtype=np.float32)
    n = int(0.03 * RATE)
    tone = (np.sin(2 * np.pi * 2000.0 * np.arange(n) / RATE) * amp).astype(np.float32)
    for t in times_s:
        i = int(t * RATE)
        x[i : i + n] += tone[: max(0, min(n, len(x) - i))]
    return x


def test_counts_rapid_clicks() -> None:
    """100ms間隔の連打を数え落とさない（本命の受入条件）。"""
    times = [0.5 + 0.1 * i for i in range(10)]
    found = AL.count_onsets(_clicks(times), RATE)
    assert len(found) == 10
    for got, want in zip(found, times):
        assert abs(got - int(want * RATE)) < 1500


def test_silence_counts_zero() -> None:
    assert AL.count_onsets(np.zeros(RATE, dtype=np.float32), RATE) == []


def test_closer_than_refractory_merges() -> None:
    """不応期（60ms）より近い2発は1件に畳む（二重計数を防ぐ）。"""
    found = AL.count_onsets(_clicks([0.5, 0.53]), RATE)
    assert len(found) == 1


def test_absolute_at_inverts_tap() -> None:
    """Tap内挿で時刻→通算件数を返す（locate_playedの逆）。"""
    tap = [(10.0, 44100), (10.5, 66150)]
    assert AL.absolute_at(10.25, tap, RATE) == 55125
    assert AL.absolute_at(99.0, tap, RATE) is None
