"""audio_latency（押下→検知の実測部品）の検証。実機なしで回す。"""

import numpy as np
from core import audio_latency as AL


def _step(total: int, at: int, level: float = 0.3) -> np.ndarray:
    """at 件目から level になる階段波。"""
    x = np.zeros(total, dtype=np.float32)
    x[at:] = level
    return x


def test_onset_absolute_finds_edge() -> None:
    window = _step(44100, 20000)
    total = 100000
    at = AL.onset_absolute(window, total, 0.0)
    assert at is not None
    # 50ms窓・2048刻みのため、先頭から1窓ぶん以内の誤差で見つかる
    assert total - window.size <= at <= total - window.size + 20000 + 2205


def test_onset_absolute_skips_older() -> None:
    window = _step(44100, 30000)
    total = 100000
    base = total - window.size
    # 立ち上がりより前から探すと見つかる
    at = AL.onset_absolute(window, total, 0.0, base + 10000)
    assert at is not None
    assert abs(at - (base + 30000)) <= 2205 + 2048
    # ノイズだけでは上がらない（floor のおかげ）
    rng = np.random.RandomState(7)
    quiet = (rng.normal(0.0, 0.001, 44100)).astype(np.float32)
    assert AL.onset_absolute(quiet, total, 0.001) is None


def test_locate_played() -> None:
    tap = [(100.0, 44100), (101.0, 88200)]
    got = AL.locate_played(tap, 45000, 0.02, 44100)
    assert got is not None
    assert abs(got - (101.0 - (88200 - 45000) / 44100 - 0.02)) < 1e-9
    assert AL.locate_played(tap, 999999, 0.02, 44100) is None


def test_summarize() -> None:
    empty = AL.summarize([])
    assert empty["detected"] == 0
    assert empty["median_ms"] == -1.0
    full = AL.summarize([30.0, 10.0, 20.0], total=3)
    assert full["detected"] == 3
    assert full["total"] == 3
    assert full["median_ms"] == 20.0


class FakeCapture:
    """measure_press 用の偽物。押下から 0.1 秒後に立ち上がる波形を返す。"""

    def __init__(self) -> None:
        self.rate = 44100
        self.total = 0
        self.t0 = 100.0

    def readWindow(self, seconds: float):
        _ = seconds
        return np.zeros(int(0.3 * self.rate), dtype=np.float32)

    def read_stamped(self, seconds: float):
        _ = seconds
        # 2秒窓の末尾から 1.1 秒前（＝押下の 0.1 秒後）に立ち上がる。
        # 読み出しは押下の約1.2秒後（実sleep）の想定。
        size = int(2.0 * self.rate)
        at = size - int(1.1 * self.rate)
        window = _step(size, at)
        self.total += size
        tap = [(self.t0 + 1.2, self.total)]
        return (window, self.total, tap, 0.02, self.rate)


def test_measure_press() -> None:
    cap = FakeCapture()
    state = {"t": 100.0}

    def send_press() -> float:
        state["t"] += 1.0
        cap.t0 = state["t"]
        return state["t"]

    result = AL.measure_press(send_press, cap, tries=3)
    assert result["detected"] == 3
    # 押下の約0.1秒後の立ち上がり。走査刻みとsleep誤差ぶんの幅を見る。
    median = float(result["median_ms"])
    assert 30.0 < median < 150.0


def test_measure_press_send_failure() -> None:
    cap = FakeCapture()

    def send_press() -> float:
        raise RuntimeError("no serial")

    result = AL.measure_press(send_press, cap, tries=3)
    assert result["detected"] == 0
    assert "error" in result
