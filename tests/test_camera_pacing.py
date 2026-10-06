"""取込ループの周期保持の検査。

待機の寝過ごし（Windows のタイマ粒度で 1ms 前後）が周期ごとに累積すると、
fps=60 を指定しても取込が 57fps 程度に落ちる（実測ログ 2026-10-04）。
実時間に依存させると不安定なので、仮想時計で寝過ごしを再現する。
"""

from __future__ import annotations

import types
from typing import Any

import numpy as np
import pytest
from core import Camera as CamMod
from core.Camera import Camera

_OVERSLEEP_S = 0.0012  # 毎回の待機がこれだけ長引く
_READ_S = 0.0005  # read() はドライバのバッファから即座に返る


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def perf_counter(self) -> float:
        return self.t

    def perf_counter_ns(self) -> int:
        return int(self.t * 1e9)


class _OversleepingStop:
    """wait(timeout) が timeout より少し長く眠る停止 Event の代役。"""

    def __init__(self, clock: _Clock, stop_after_s: float) -> None:
        self._clock = clock
        self._deadline = clock.t + stop_after_s

    def is_set(self) -> bool:
        return self._clock.t >= self._deadline

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is not None and timeout > 0:
            self._clock.t += timeout + _OVERSLEEP_S
        return self.is_set()


class _InstantCap:
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.reads = 0
        self._frame = np.zeros((2, 2, 3), dtype=np.uint8)

    def read(self) -> tuple[bool, Any]:
        self._clock.t += _READ_S
        self.reads += 1
        return True, self._frame


@pytest.mark.parametrize("fps", [30, 60])
def test_capture_rate_holds_configured_fps_even_when_waits_oversleep(
    monkeypatch: pytest.MonkeyPatch, fps: int
) -> None:
    # Given: 待機が毎回 1.2ms 寝過ごし、read は即座に返る環境
    clock = _Clock()
    monkeypatch.setattr(
        CamMod,
        "time",
        types.SimpleNamespace(
            perf_counter=clock.perf_counter, perf_counter_ns=clock.perf_counter_ns
        ),
    )
    camera = Camera(fps=fps)
    cap = _InstantCap(clock)
    duration_s = 10.0
    stop = _OversleepingStop(clock, duration_s)

    # When: 仮想時間で 10 秒ぶん取込ループを回す
    camera._update(stop_event=stop, generation=camera._generation, camera=cap)  # type: ignore[arg-type]  # 停止 Event の代役

    # Then: 寝過ごしが累積せず、取込は設定 fps の 99% 以上を保つ
    measured = cap.reads / duration_s
    assert measured >= fps * 0.99, f"取込 {measured:.1f}fps < 設定 {fps}fps"
    # 追いつこうとして設定 fps を超えて連射しない
    assert measured <= fps * 1.01, f"取込 {measured:.1f}fps > 設定 {fps}fps"
