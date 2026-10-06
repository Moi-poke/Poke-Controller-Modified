"""CaptureArea の位相合わせ配線の検査。Tk も GDI も触らない。

判断そのもの（いつ据え直すか）は test_preview_phase.py が仮想時間で
検査する。ここでは「表示 tick の観測が PhaseLock へ渡り、要求が出たら
after で 1 回だけ予約され、予約時刻に PreviewClock.realign が呼ばれる」
という配線だけを見る。
"""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

import GuiAssets
import numpy as np
import pytest
from GuiAssets import CaptureArea
from core.preview_phase import PhaseLock

_MS = 1_000_000
_PERIOD = round(1e9 / 60)


class _TimedCamera:
    def __init__(self) -> None:
        self.seq = 0
        self.t_ready_ns = 0

    def readFrameWithTiming(self) -> tuple[Any, int, int, int]:
        return None, self.seq, self.t_ready_ns, self.t_ready_ns


class _Clock:
    def __init__(self) -> None:
        self.realigns = 0

    def realign(self) -> None:
        self.realigns += 1


def _area(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[CaptureArea, _TimedCamera, list[tuple[int, Callable[[], None]]], list[int]]:
    now = [0]
    monkeypatch.setattr(
        GuiAssets, "time", types.SimpleNamespace(perf_counter_ns=lambda: now[0])
    )
    area = CaptureArea.__new__(CaptureArea)
    camera = _TimedCamera()
    area.camera = camera
    area._phase_lock = PhaseLock(_PERIOD)
    area._realign_after_id = None
    scheduled: list[tuple[int, Callable[[], None]]] = []

    def after(delay_ms: int, callback: Callable[[], None]) -> str:
        scheduled.append((delay_ms, callback))
        return f"after-{len(scheduled)}"

    area.after = after  # type: ignore[method-assign]  # Tk の after の代役
    return area, camera, scheduled, now


def _see(
    area: CaptureArea,
    camera: _TimedCamera,
    now: list[int],
    seq: int,
    ready: int,
    at: int,
) -> None:
    camera.seq, camera.t_ready_ns = seq, ready
    now[0] = at
    area._trackPhase(seq)


def test_a_miss_after_steady_frames_schedules_one_realign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 16.67ms ごとに届くフレームを 3 枚見た表示面
    area, camera, scheduled, now = _area(monkeypatch)
    for seq in (1, 2, 3):
        _see(area, camera, now, seq, seq * _PERIOD, seq * _PERIOD + 200_000)
    assert scheduled == []

    # When: 次の到着直前の tick が同じ seq を見た（空振り）
    _see(area, camera, now, 3, 3 * _PERIOD, 4 * _PERIOD - 300_000)
    # 予約が残っている間にもう一度空振りしても
    _see(area, camera, now, 3, 3 * _PERIOD, 4 * _PERIOD - 100_000)

    # Then: 次の到着から半周期後へ 1 回だけ予約する
    assert len(scheduled) == 1
    delay_ms, callback = scheduled[0]
    assert delay_ms == 9  # (0.3ms + 8.33ms) を ms へ切り上げ
    assert callback == area._realignPreview


def test_a_seq_that_moved_between_reads_is_not_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 公開時刻を読む間に次のフレームが届いた（seq がずれた）
    area, camera, scheduled, now = _area(monkeypatch)
    observed: list[int] = []
    monkeypatch.setattr(
        area._phase_lock,
        "observe",
        lambda now_ns, seq, t_ready_ns: observed.append(seq),
    )
    camera.seq, camera.t_ready_ns = 5, 5 * _PERIOD
    # When: 表示 tick は seq=4 を描いていた
    area._trackPhase(4)
    # Then: 食い違った組み合わせでは判断しない
    assert observed == []
    assert scheduled == []


def test_a_camera_without_timing_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 公開時刻を出さないカメラ（共有メモリ版など）
    area, _camera, scheduled, _now = _area(monkeypatch)
    area.camera = object()
    # When/Then: 何度観測しても例外も予約も出ない
    for _ in range(5):
        area._trackPhase(1)
    assert scheduled == []


def test_realign_callback_reaches_the_clock_only_while_capturing() -> None:
    # Given: 表示中の面と、その表示クロック
    area = CaptureArea.__new__(CaptureArea)
    clock = _Clock()
    area._preview_clock = clock  # type: ignore[assignment]  # PreviewClock の代役
    area._capturing = True
    area._preview_stop_requested = False
    area._realign_after_id = "after-1"

    # When: 予約時刻が来た
    area._realignPreview()
    # Then: クロックを据え直し、予約は消える
    assert clock.realigns == 1
    assert area._realign_after_id is None

    # When: 停止要求の後に古い予約が来ても
    area._preview_stop_requested = True
    area._realignPreview()
    # Then: クロックには触らない
    assert clock.realigns == 1


def test_dispatch_tick_feeds_the_drawn_seq_to_phase_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: フレームを 1 枚描く表示 tick
    area = CaptureArea.__new__(CaptureArea)
    area._capturing = True
    area._preview_stop_requested = False
    area.is_show_var = types.SimpleNamespace(get=lambda: True)  # type: ignore[assignment]
    area._stat_shown = 0
    area._stat_began_at = None
    area._stat_draw_ms = 0.0
    area._stat_draw_max_ms = 0.0
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    area._readLatest = lambda: (frame, 42)  # type: ignore[method-assign]
    area._drawFrame = lambda f, seq=None: True  # type: ignore[method-assign]
    tracked: list[int] = []
    area._trackPhase = tracked.append  # type: ignore[method-assign]

    # When: tick を 1 回処理する
    area._dispatch_tick()

    # Then: 描いた seq が位相合わせへ渡る
    assert tracked == [42]
