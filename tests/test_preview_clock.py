"""RED-only clock contracts; # noqa: SIZE_OK - approved brief requires one module."""

from __future__ import annotations

import _tkinter
import os
import threading
import time
import tkinter as tk
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
import preview_fps_support as support
import pytest
from GuiAssets import CaptureArea
from ui import preview_clock

type WakeSource = Literal["timer", "control", "stale_timer", "error"]

#: The real-Tk starvation check (``test_native_wake_poll_does_not_starve_tk_after_timers``).
#: A 20 ms heartbeat must fire at least five times inside a 0.5 s window, so the
#: margin against a healthy run (~25 beats) survives a loaded machine. ``_POLL_BUDGET``
#: is a hang guard, not a throttle: a poll that re-arms through ``after_idle`` never
#: gives ``dooneevent`` its turn back, and the budget is what ends that spin.
_HEARTBEAT_MS = 20
_MIN_HEARTBEATS = 5
_WINDOW_S = 0.5
_POLL_BUDGET = 4_000

#: Roots whose Tk event loop was pumped, kept referenced on purpose. Releasing
#: the last reference of a *pumped* root (``Tk.__del__`` -> ``Tcl_DeleteInterp``)
#: kills a later test in the same process with a Windows fatal exception
#: (0x80000003, "Garbage-collecting" + a worker thread inside
#: ``test_stop_signal_failure_retries_before_join_and_cleanup``); keeping the
#: reference defers that finalization to process exit and the crash disappears.
#: Reproduced with a bare Tk pump and no ``PreviewClock`` involved, so this is
#: not the preview clock's doing; root cause not identified yet. Same repro:
#: ``pytest tests/test_preview_clock.py::test_native_wake_poll_does_not_starve_tk_after_timers
#: tests/test_preview_clock.py::test_stop_signal_failure_retries_before_join_and_cleanup``.
_PUMPED_TK_ROOTS: list[Any] = []


class FakeRoot:
    def __init__(self) -> None:
        self.callbacks: dict[str, Callable[[], None]] = {}
        self.cancelled: set[str] = set()
        self._next_id = 0

    def after(self, delay_ms: int, callback: Callable[[], None]) -> str:
        self._next_id += 1
        after_id = f"after-{self._next_id}"
        self.callbacks[after_id] = callback
        return after_id

    def after_cancel(self, after_id: str) -> None:
        self.cancelled.add(after_id)
        self.callbacks.pop(after_id, None)

    def run_next(self) -> None:
        after_id, callback = next(iter(self.callbacks.items()))
        del self.callbacks[after_id]
        callback()


class InjectedRuntimeError(RuntimeError):
    pass


class RetryFailRoot(FakeRoot):
    def __init__(self) -> None:
        super().__init__()
        self.fail_retry_once = True

    def after(self, delay_ms: int, callback: Callable[[], None]) -> str:
        if self.fail_retry_once and callback.__name__ == "_retry_teardown":
            self.fail_retry_once = False
            raise InjectedRuntimeError("injected teardown retry scheduling failure")
        return super().after(delay_ms, callback)


class TclInterpRoot(FakeRoot):
    """呼べる ``interpaddr`` を持つ root（Tcl async 通知の生成が成功する条件）。"""

    def __init__(self) -> None:
        super().__init__()
        self.interp_probes = 0

    @property
    def tk(self) -> Any:
        self.interp_probes += 1
        return SimpleNamespace(interpaddr=lambda: 1)


class AfterIdleRoot(FakeRoot):
    """実 Tk と同じく ``after_idle`` を持つ root（``after`` は health/fallback 用）。"""

    def __init__(self) -> None:
        super().__init__()
        self.idle_callbacks: dict[str, Callable[[], None]] = {}
        self.after_delays: list[int] = []

    def after_idle(self, callback: Callable[[], None]) -> str:
        after_id = f"idle-{len(self.idle_callbacks) + 1}"
        self.idle_callbacks[after_id] = callback
        return after_id

    def after(self, delay_ms: int, callback: Callable[[], None]) -> str:
        self.after_delays.append(delay_ms)
        return super().after(delay_ms, callback)

    def after_cancel(self, after_id: str) -> None:
        self.idle_callbacks.pop(after_id, None)
        super().after_cancel(after_id)

    def run_idle(self) -> None:
        after_id, callback = next(iter(self.idle_callbacks.items()))
        del self.idle_callbacks[after_id]
        callback()


class FakeNativeRuntime:
    def __init__(
        self,
        *,
        stop_join_results: list[bool] | None = None,
        join_exceptions: list[Exception | None] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.controls: list[preview_clock._ControlState] = []
        self.initial_control: preview_clock._ControlState | None = None
        self.stop_requests = 0
        self.destroy_calls = 0
        self.stop_join_results = stop_join_results or [True]
        self.join_exceptions = list(join_exceptions or [])
        self.pending: list[preview_clock.PreviewTick] = []
        self.pending_tick_present_at_window_end = False
        self.current_epoch = 0
        self.current_generation = 0
        self.worker_tick_published_count = 0
        self.main_dispatch_count = 0
        self.consumed_sequences: list[int] = []
        self.pending_tick_superseded_count = 0
        self.stale_tick_dropped_count = 0
        self.wake_callback: Callable[[], None] | None = None
        self.wake_sources: list[WakeSource] = []
        self.pending_wake_source: WakeSource | None = None
        self.health: dict[str, int | str | bool] = {
            "worker_thread_native_id": threading.get_native_id() + 1,
            "last_sequence": 0,
            "last_delivered_sequence": 0,
            "last_wake_qpc": 0,
            "post_failure_count": 0,
            "last_post_error": 0,
            "stop_signal_succeeded": True,
            "running": True,
            "window_class_destroyed": False,
            "window_class_unregistered": False,
        }

    def set_initial_control(self, control: preview_clock._ControlState) -> None:
        self.initial_control = control

    def bind_wake_callback(self, callback: Callable[[], None]) -> None:
        self.wake_callback = callback

    def start(self) -> None:
        return None

    def request_control(self, control: preview_clock._ControlState) -> None:
        self.events.append(f"control:{control.kind.value}")
        self.current_epoch = control.epoch
        self.current_generation = control.generation
        self.controls.append(control)

    def request_stop(self) -> None:
        self.stop_requests += 1

    def join(self, timeout_s: float) -> bool:
        assert timeout_s >= 0.0
        if self.join_exceptions:
            error = self.join_exceptions.pop(0)
            if error is not None:
                raise error
        return self.stop_join_results.pop(0)

    def destroy(self) -> None:
        assert not self.stop_join_results
        self.destroy_calls += 1
        self.health["running"] = False
        self.health["window_class_destroyed"] = True
        self.health["window_class_unregistered"] = True

    def publish_tick(self, tick: preview_clock.PreviewTick) -> None:
        self.worker_tick_published_count += 1
        self.pending.append(tick)

    def has_live_resources(self) -> bool:
        return bool(self.health["running"] or self.destroy_calls == 0)

    def consume_tick(
        self,
    ) -> preview_clock.PreviewTick | preview_clock._StaleTickDrop | None:  # noqa: E501
        if not self.pending:
            return None
        tick = self.pending[-1]
        self.pending_tick_superseded_count += len(self.pending) - 1
        self.pending.clear()
        if (
            tick.epoch != self.current_epoch
            or tick.generation != self.current_generation
        ):
            self.stale_tick_dropped_count += 1
            return preview_clock._StaleTickDrop.STALE
        self.main_dispatch_count += 1
        self.consumed_sequences.append(tick.sequence)
        return tick

    def snapshot(self) -> dict[str, int | str | bool]:
        return dict(self.health)

    def begin_accounting_window(self) -> None:
        return None

    def end_accounting_window(self) -> dict[str, int | str | bool]:
        return {
            "worker_tick_published_count": self.worker_tick_published_count,
            "main_dispatch_count": self.main_dispatch_count,
            "pending_tick_superseded_count": self.pending_tick_superseded_count,
            "stale_tick_dropped_count": self.stale_tick_dropped_count,
            "pending_tick_present_at_window_end": int(
                self.pending_tick_present_at_window_end
            ),
        }

    def drain_evidence(self) -> tuple[dict[str, object], ...]:  # noqa: E501  # noqa: OBJECT_OK
        return ()

    def emit_wake(self, source: WakeSource) -> None:
        self.wake_sources.append(source)
        self.pending_wake_source = source
        assert self.wake_callback is not None
        self.wake_callback()

    def consume_wake(self) -> preview_clock._RuntimeWake | None:
        source = self.pending_wake_source
        self.pending_wake_source = None
        if source is None:
            return None
        control = self.controls[-1]
        reason: Literal["clock_ready", "tick", "control", "error"]
        if source == "control":
            reason = "control"
        elif source == "error":
            reason = "error"
        else:
            reason = "tick"
        return preview_clock._RuntimeWake(
            reason=reason,
            epoch=control.epoch,
            generation=control.generation,
            configured_fps=control.configured_fps,
        )


class _PacerRuntime(FakeNativeRuntime):
    """``FakeNativeRuntime`` whose health is served by a real ``_worker_main`` run."""

    def __init__(self, mailbox: preview_clock._SharedState) -> None:
        super().__init__()
        self.mailbox = mailbox

    def snapshot(self) -> dict[str, int | str | bool]:
        return {**super().snapshot(), **self.mailbox.snapshot()}


class _NeverWakingRuntime(FakeNativeRuntime):
    """``consume_wake_signal()`` が常に False のランタイム。

    wake は一度も起床しないので、``_poll_native_wake`` の再予約だけが毎回
    起きる。poll の再予約が Tk のイベントループを占有するなら、それは
    poll とは無関係な ``after(ms)`` タイマーが止まる形でしか現れない。

    ``poll_budget`` は「poll の呼び出し回数の上限」であり、上限に達した時点で
    ``stop_clock()`` を呼んで再予約を止める。``after_idle`` の自己再予約は
    Tk の idle 排出が尽きない限り ``doonevent`` が返らないため、テスト自身が
    ハングしないための装置である。仕様どおりに ``after`` で再予約する実装では
    0.5 秒の窓内で上限に達しない（``after(1)`` の poll は高々数百回）。
    """

    def __init__(self, poll_budget: int) -> None:
        super().__init__()
        self.poll_budget = poll_budget
        self.poll_calls = 0
        self.poll_budget_exhausted = False
        self._stop_clock: Callable[[], object] | None = None

    def bind_stop_clock(self, stop_clock: Callable[[], object]) -> None:
        self._stop_clock = stop_clock

    def consume_wake_signal(self) -> bool:
        self.poll_calls += 1
        if self.poll_calls >= self.poll_budget:
            self.poll_budget_exhausted = True
            if self._stop_clock is not None:
                self._stop_clock()
        return False


def _tick(
    control: preview_clock._ControlState, sequence: int
) -> preview_clock.PreviewTick:
    return preview_clock.PreviewTick(
        epoch=control.epoch,
        generation=control.generation,
        configured_fps=control.configured_fps,
        sequence=sequence,
        deadline_qpc=sequence,
        emitted_qpc=sequence,
    )


def _dispatch_with_label(
    calls: list[str], label: str = "dispatch"
) -> Callable[[], preview_clock.DispatchResult]:
    def dispatch() -> preview_clock.DispatchResult:
        calls.append(label)
        return preview_clock.DispatchResult(schedule="active")

    return dispatch


def _dispatch_with_count(
    calls: list[int], runtime: FakeNativeRuntime
) -> Callable[[], preview_clock.DispatchResult]:
    def dispatch() -> preview_clock.DispatchResult:
        calls.append(runtime.main_dispatch_count)
        return preview_clock.DispatchResult(schedule="active")

    return dispatch


def _close_native_runtime(runtime: Any) -> None:
    runtime.request_stop()
    assert runtime.join(1.0)
    runtime.destroy()


def _pending_poll_ids(root: FakeRoot) -> list[str]:
    """``after`` 予約されたまま残っている ``_poll_native_wake`` の after id 一覧。"""
    return [
        after_id
        for after_id, callback in root.callbacks.items()
        if callback.__name__ == "_poll_native_wake"
    ]


def _force_stop_signal_success(api: Any) -> None:
    api.cancel_timer_results.clear()
    api.set_event_results.clear()
    api.cancel_timer_results.extend([True, True])
    api.set_event_results.extend([True, True])


class FakePreviewClock:
    def __init__(self) -> None:
        self.generation = 1
        self.configured_fps = 60
        self.stop_result = preview_clock.StopResult.STOPPED

    def set_fps(self, fps: int) -> None:
        self.configured_fps = fps
        self.generation += 1

    def stop(self) -> preview_clock.StopResult:
        return self.stop_result

    def health_snapshot(self) -> dict[str, int | str | bool]:
        return {
            "generation": self.generation,
            "configured_fps": self.configured_fps,
        }


@pytest.fixture
def area() -> Any:
    harness = CaptureArea.__new__(CaptureArea)
    harness.camera = SimpleNamespace(
        capture_size=(1280, 720),
        readFrameWithSeq=lambda: (
            np.zeros((720, 1280, 3), dtype=np.uint8),
            1,
        ),
    )
    harness._capturing = True
    harness._preview_stop_requested = False
    harness._preview_clock = FakePreviewClock()
    harness._configured_fps = 60
    harness.is_show_var = SimpleNamespace(get=lambda: True)
    harness._stat_shown = 0
    harness._stat_began_at = None
    harness._stat_draw_ms = 0.0
    harness._stat_draw_max_ms = 0.0
    harness._last_frame_seq = None
    harness._camera_observation = {
        "camera_read_count": 0,
        "camera_frame_present_count": 0,
        "camera_none_count": 0,
        "camera_read_error_count": 0,
        "camera_unique_sequence_count": 0,
        "camera_duplicate_sequence_count": 0,
        "camera_sequence_regression_count": 0,
        "camera_last_sequence": 0,
        "post_teardown_camera_read_count": 0,
    }
    harness._drawFrame = lambda _frame, _seq=None: None
    harness._rect_after_id = None
    harness._select_after_id = None
    return harness


def test_capture_area_dispatches_one_tick_without_scheduling(
    area: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[int] = []
    monkeypatch.setattr(
        area,
        "after",
        lambda delay_ms, _callback: scheduled.append(delay_ms),
    )
    result = area._dispatch_tick()
    assert result == preview_clock.DispatchResult(schedule="active")
    assert scheduled == []


def test_capture_area_set_fps_same_value_does_not_change_generation(
    area: Any,
) -> None:
    before = area._preview_clock.health_snapshot()["generation"]
    area.setFps(60)
    assert area._preview_clock.health_snapshot()["generation"] == before


def test_capture_area_set_fps_change_reanchors_clock(
    area: Any,
) -> None:
    before = area._preview_clock.health_snapshot()["generation"]
    area.setFps(30)
    after = area._preview_clock.health_snapshot()
    assert after["generation"] == int(before) + 1
    assert after["configured_fps"] == 30


def test_camera_observation_distinguishes_duplicate_and_regression(
    area: Any,
) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    def read_sequence(sequence: int) -> Any:
        return lambda: (frame, sequence)

    area.camera.readFrameWithSeq = read_sequence(10)
    area._dispatch_tick()
    area.camera.readFrameWithSeq = read_sequence(10)
    area._dispatch_tick()
    area.camera.readFrameWithSeq = read_sequence(9)
    area._dispatch_tick()
    snapshot = area._preview_evidence_snapshot()
    assert snapshot["camera_duplicate_sequence_count"] == 1
    assert snapshot["camera_sequence_regression_count"] == 1
    assert snapshot["camera_last_sequence"] == 10


def test_stop_capture_returns_stop_result_and_blocks_post_teardown_reads(
    area: Any,
) -> None:
    assert area.stopCapture() is preview_clock.StopResult.STOPPED
    area._dispatch_tick()
    assert area._preview_evidence_snapshot()["post_teardown_camera_read_count"] == 0


@pytest.mark.parametrize("configured_fps", (5, 15, 30, 45, 60))
def test_public_clock_accepts_only_production_fps(configured_fps: int) -> None:
    clock = preview_clock.PreviewClock(
        FakeRoot(),
        lambda: preview_clock.DispatchResult(schedule="active"),
        configured_fps,
        200,
    )
    assert clock.health_snapshot()["configured_fps"] == configured_fps


@pytest.mark.parametrize("fps", (1, 24, 59, 60.01, 60.03, 60.06, 61))
def test_public_clock_rejects_non_production_fps(fps: float) -> None:
    with pytest.raises(ValueError, match="configured FPS"):
        preview_clock.PreviewClock(
            FakeRoot(),
            lambda: preview_clock.DispatchResult(schedule="active"),
            fps,
            200,
        )


@pytest.mark.parametrize(
    ("frequency_hz", "fps", "expected"),
    ((10_000_000, 60, 166_667), (10_000_000, 5, 2_000_000)),
)
def test_qpc_interval_uses_integer_ceiling(
    frequency_hz: int, fps: int, expected: int
) -> None:
    assert preview_clock._qpc_interval(frequency_hz, fps) == expected


def test_negative_due_time_uses_integer_ceiling() -> None:
    assert (
        preview_clock._negative_due_100ns(
            deadline_qpc=1_001,
            now_qpc=1_000,
            frequency_hz=10_000_000,
        )
        == -1
    )


def test_deadline_skip_uses_one_arithmetic_grid_step() -> None:
    # Given: a worker that woke 351 ticks, or 3 whole periods, past its deadline.
    advance: Callable[..., Any] = preview_clock._advance_deadline

    # When: the pacer re-anchors the deadline on the integer grid.
    new_deadline_qpc, periods_skipped = advance(
        deadline_qpc=100,
        now_qpc=451,
        interval_qpc=100,
    )

    # Then: the firing schedule is unchanged: the legacy raw ``skipped`` local.
    assert new_deadline_qpc == 100 + ((451 - 100) // 100 + 1) * 100

    # Then: only the whole periods actually jumped over are reported.
    assert (new_deadline_qpc, periods_skipped) == (500, 3)


@pytest.mark.parametrize(
    (
        "now_qpc",
        "deadline_qpc",
        "interval_qpc",
        "expected_new_deadline_qpc",
        "expected_periods_skipped",
    ),
    (
        pytest.param(100, 200, 100, 200, 0, id="not_yet_due"),
        pytest.param(100, 100, 100, 200, 0, id="exactly_on_time"),
        pytest.param(199, 100, 100, 200, 0, id="one_tick_short_of_a_full_period"),
        pytest.param(200, 100, 100, 300, 1, id="one_period_behind"),
        pytest.param(451, 100, 100, 500, 3, id="many_periods_behind"),
    ),
)
def test_advance_deadline_reports_whole_periods_skipped(
    now_qpc: int,
    deadline_qpc: int,
    interval_qpc: int,
    expected_new_deadline_qpc: int,
    expected_periods_skipped: int,
) -> None:
    """``periods_skipped`` counts whole periods jumped over, not grid steps.

    It is ``0`` while the deadline is still in the future and
    ``(now_qpc - deadline_qpc) // interval_qpc`` once the deadline is due, so a
    tick that fires on time reports nothing. The reported deadline keeps the
    legacy ``skipped = steps + 1`` grid advance, so the firing schedule of the
    pacer is bit-for-bit unchanged.
    """
    # Given: a due-or-not-yet-due pacer deadline on the QPC grid.
    advance: Callable[..., Any] = preview_clock._advance_deadline

    # When: the worker re-anchors the deadline after the timer fires.
    new_deadline_qpc, periods_skipped = advance(
        deadline_qpc=deadline_qpc,
        now_qpc=now_qpc,
        interval_qpc=interval_qpc,
    )

    # Then: the new deadline is grid-consistent and only whole skips are named.
    assert new_deadline_qpc == expected_new_deadline_qpc
    assert periods_skipped == expected_periods_skipped


def test_start_dispatches_one_synchronous_tick_before_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    events: list[str] = []
    runtime = FakeNativeRuntime(events=events)
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(events),
        60,
        200,
    )
    clock.start()
    assert events == ["dispatch", "control:start"]
    assert len(runtime.controls) == 1
    assert runtime.controls[0].kind is preview_clock._ControlKind.START
    assert runtime.controls[0].configured_fps == 60


def test_native_start_never_installs_tcl_async_wake_trampoline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ネイティブ起動は Tcl async 通知を作らず、起床の排出は after タイマーの poll に任せる。

    起床は PostMessageW で届き、Tk 側は idle 列ではなく after() タイマーで排出する。
    """
    root = TclInterpRoot()
    captured: list[dict[str, Any]] = []

    def capture_factory(**kwargs: Any) -> FakeNativeRuntime:
        captured.append(kwargs)
        return FakeNativeRuntime()

    monkeypatch.setattr(preview_clock, "_create_native_runtime", capture_factory)
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()

    assert len(captured) == 1
    assert root.interp_probes == 0
    assert not hasattr(root, "_pokECON_tcl_async_notifiers")


def test_same_fps_is_noop_and_different_fps_reanchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    events: list[str] = []
    runtime = FakeNativeRuntime(events=events)
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(events),
        60,
        200,
    )
    clock.start()
    start_events = events.copy()
    generation = int(clock.health_snapshot()["generation"])
    clock.set_fps(60)
    assert events == start_events
    assert all(
        control.kind is not preview_clock._ControlKind.SET_FPS
        for control in runtime.controls
    )
    assert clock.health_snapshot()["generation"] == generation
    clock.set_fps(30)
    assert events == [*start_events, "control:set_fps"]
    assert clock.health_snapshot()["generation"] == generation + 1
    assert runtime.controls[-1].configured_fps == 30
    assert runtime.controls[-1].kind is preview_clock._ControlKind.SET_FPS


def test_control_wake_never_dispatches_a_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    calls: list[str] = []
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(calls, "tick"),
        60,
        200,
    )
    clock.start()
    runtime.emit_wake("control")
    assert runtime.wake_sources == ["control"]
    assert calls == ["tick"]
    assert runtime.worker_tick_published_count == 0
    assert runtime.main_dispatch_count == 0


def test_timer_wake_dispatches_one_pending_tick_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    calls: list[str] = []
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(calls, "tick"),
        60,
        200,
    )
    clock.start()
    runtime.publish_tick(_tick(runtime.controls[-1], 1))
    runtime.emit_wake("timer")
    assert runtime.wake_sources == ["timer"]
    assert calls == ["tick", "tick"]
    assert runtime.worker_tick_published_count == 1
    assert runtime.main_dispatch_count == 1
    assert runtime.consumed_sequences == [1]
    assert runtime.pending == []


def test_latest_only_mailbox_accounting_balances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    dispatch_calls: list[int] = []
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_count(dispatch_calls, runtime),
        60,
        200,
    )
    clock.start()
    dispatch_calls.clear()
    start_control = runtime.controls[-1]
    first_tick = _tick(start_control, 1)
    second_tick = _tick(start_control, 2)
    runtime.publish_tick(first_tick)
    runtime.publish_tick(second_tick)
    assert runtime.pending == [first_tick, second_tick]
    runtime.emit_wake("timer")
    assert dispatch_calls == [1]
    assert runtime.main_dispatch_count == 1
    assert runtime.consumed_sequences == [2]
    assert runtime.pending_tick_superseded_count == 1
    assert runtime.pending == []

    clock.set_fps(30)
    current_control = runtime.controls[-1]
    runtime.publish_tick(_tick(start_control, 3))
    runtime.emit_wake("stale_timer")
    assert dispatch_calls == [1]
    assert runtime.main_dispatch_count == 1
    assert runtime.pending_tick_superseded_count == 1
    assert runtime.stale_tick_dropped_count == 1

    runtime.publish_tick(_tick(current_control, 4))
    runtime.pending_tick_present_at_window_end = True
    assert len(runtime.pending) == 1
    accounting = runtime.end_accounting_window()
    assert accounting == {
        "worker_tick_published_count": 4,
        "main_dispatch_count": 1,
        "pending_tick_superseded_count": 1,
        "stale_tick_dropped_count": 1,
        "pending_tick_present_at_window_end": 1,
    }
    published = int(accounting["worker_tick_published_count"])
    dispatched = int(accounting["main_dispatch_count"])
    superseded = int(accounting["pending_tick_superseded_count"])
    stale_dropped = int(accounting["stale_tick_dropped_count"])
    pending_at_end = int(accounting["pending_tick_present_at_window_end"])
    assert published == dispatched + superseded + stale_dropped + pending_at_end


def test_blit_skipped_no_new_frame_counter_tracks_only_empty_mailbox_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    calls: list[str] = []
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(calls, "tick"),
        60,
        200,
    )
    clock.start()
    try:
        # Given: a native clock that has not yet suppressed any blit.
        assert int(clock.health_snapshot()["blit_skipped_no_new_frame"]) == 0

        # When: a timer wake fires while the latest-wins mailbox holds no tick.
        runtime.emit_wake("timer")

        # Then: the suppressed blit is counted and nothing is dispatched.
        assert int(clock.health_snapshot()["blit_skipped_no_new_frame"]) == 1
        assert calls == ["tick"]

        # When: the next timer wake carries a new frame.
        runtime.publish_tick(_tick(runtime.controls[-1], 1))
        runtime.emit_wake("timer")

        # Then: the blit runs and the suppression counter stays unchanged.
        assert int(clock.health_snapshot()["blit_skipped_no_new_frame"]) == 1
        assert calls == ["tick", "tick"]
    finally:
        clock.stop()


def test_pacer_lateness_and_missing_frames_have_distinct_counter_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a worker whose pacer wakes far past its deadline on the second tick.
    late_api = support.FakeWin32Api(
        qpc_values=[0, 1_000_000, 5_000_000],
        wait_results=[2, 2, 0],
    )
    late_context = preview_clock._worker_context_for_test(late_api)
    late_runtime = _PacerRuntime(late_context.mailbox)
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: late_runtime
    )
    late_calls: list[str] = []
    late_clock = preview_clock.PreviewClock(
        FakeRoot(), _dispatch_with_label(late_calls, "tick"), 60, 200
    )
    late_clock.start()
    try:
        preview_clock._worker_main(late_context)
        late_runtime.publish_tick(_tick(late_runtime.controls[-1], 1))
        late_runtime.emit_wake("timer")
        pacer_late = late_clock.health_snapshot()
    finally:
        late_clock.stop()

    # Given: a fresh clock whose mailbox is never filled by a worker.
    empty_runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: empty_runtime
    )
    empty_calls: list[str] = []
    empty_clock = preview_clock.PreviewClock(
        FakeRoot(), _dispatch_with_label(empty_calls, "tick"), 60, 200
    )
    empty_clock.start()
    try:
        empty_runtime.emit_wake("timer")
        no_new_frame = empty_clock.health_snapshot()
    finally:
        empty_clock.stop()

    # Then: the on-time first tick reports 0, the late second tick reports 22.
    assert int(pacer_late["period_skipped_count"]) == 22

    # Then: pacer lateness and a missing frame carry different signatures.
    # Both signatures are the same presence vector, (period_skipped, blit_skipped),
    # so "differ" is a real discrimination and not an artifact of inverted predicates.
    pacer_late_signature = (
        int(pacer_late["period_skipped_count"]) > 0,
        int(pacer_late["blit_skipped_no_new_frame"]) > 0,
    )
    no_new_frame_signature = (
        int(no_new_frame["period_skipped_count"]) > 0,
        int(no_new_frame["blit_skipped_no_new_frame"]) > 0,
    )
    assert pacer_late_signature == (True, False)
    assert no_new_frame_signature == (False, True)
    assert pacer_late_signature != no_new_frame_signature


def test_undelivered_tick_messages_fail_health_and_start_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    runtime.health["last_sequence"] = 2
    root.run_next()
    assert clock.mode() == "after"
    assert int(clock.health_snapshot()["last_health_callback_result"]) != 0


def test_stop_pending_retains_runtime_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime(stop_join_results=[False, True])
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    assert clock.stop() is preview_clock.StopResult.PENDING
    assert runtime.destroy_calls == 0
    assert clock.stop() is preview_clock.StopResult.STOPPED
    assert runtime.destroy_calls == 1


def test_native_init_failure_starts_only_after_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()

    def fail_factory(**_kwargs: Any) -> FakeNativeRuntime:
        raise preview_clock._PreviewClockError("injected high-resolution init failure")

    monkeypatch.setattr(preview_clock, "_create_native_runtime", fail_factory)
    calls: list[str] = []
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(calls, "tick"),
        15,
        200,
    )
    clock.start()
    assert calls == ["tick"]
    assert clock.mode() == "after"
    assert root.callbacks
    clock.stop()
    assert clock.health_snapshot()["state"] == "stopped"


def test_high_resolution_timer_creation_uses_required_access() -> None:
    api = support.FakeWin32Api()
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    try:
        assert api.timer_create_calls == [(None, None, 0x00000002, 0x00100002)]
        assert api.ordinary_timer_create_calls == 0
    finally:
        _close_native_runtime(runtime)


def test_set_waitable_timer_is_one_shot_without_manual_reset_argument() -> None:
    api = support.FakeWin32Api()
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    try:
        api.auto_run_one_timer_wake()
        timer_call = api.set_waitable_timer_calls[-1]
        assert timer_call.period_ms == 0
        assert timer_call.completion_routine is None
        assert timer_call.f_resume is False
        assert timer_call.due_100ns < 0
        assert not hasattr(timer_call, "manual_reset")
    finally:
        _close_native_runtime(runtime)


def test_worker_context_contains_no_tk_or_dispatch_callable() -> None:
    context = support.worker_context_for_test()
    assert not hasattr(context, "root")
    assert not hasattr(context, "dispatch_tick")
    assert not hasattr(context, "after")
    assert not hasattr(context, "capture_area")


def test_post_message_failure_stops_timer_and_marks_health() -> None:
    api = support.FakeWin32Api(post_message_results=[False])
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    try:
        assert runtime.join(1.0)
        health = runtime.snapshot()
        assert health["post_failure_count"] == 1
        assert health["last_post_error"] != 0
        assert health["running"] is False
        assert api.rearm_after_post_failure is False
        assert len(api.set_waitable_timer_calls) == 1
    finally:
        _close_native_runtime(runtime)


@pytest.mark.skipif(os.name != "nt", reason="Win32 adapter is Windows-only")
def test_ctypes_adapter_routes_window_procedures_through_user32(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFunction:
        def __init__(self, library_name: str) -> None:
            self.library_name = library_name
            self.argtypes: list[Any] = []
            self.restype: Any = object

        def __call__(self, *_args: Any) -> int:
            return 1

    class FakeLibrary:
        def __init__(self, library_name: str) -> None:
            self.library_name = library_name

        def __getattr__(self, name: str) -> FakeFunction:
            del name
            return FakeFunction(self.library_name)

    loaded_libraries: list[str] = []

    def fake_windll(library_name: str, **_kwargs: Any) -> FakeLibrary:
        loaded_libraries.append(library_name)
        return FakeLibrary(library_name)

    monkeypatch.setattr(preview_clock.ctypes, "WinDLL", fake_windll)

    # Given: the Win32 adapter is constructed on Windows.
    # When: the adapter resolves its native procedure libraries.
    api = preview_clock._CtypesWin32Api()

    # Then: window procedures come from user32, not kernel32.
    assert loaded_libraries == ["kernel32", "user32"]
    assert api._register_class.library_name == "user32"
    assert api._create_window.library_name == "user32"
    assert api._destroy_window.library_name == "user32"
    assert api._unregister_class.library_name == "user32"
    assert api._post_message.library_name == "user32"


@pytest.mark.skipif(os.name != "nt", reason="Win32 adapter is Windows-only")
def test_ctypes_set_waitable_timer_signature_matches_six_argument_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFunction:
        argtypes: list[Any]
        restype: Any

        def __init__(self) -> None:
            self.argtypes = []
            self.restype = object

        def __call__(self, *_args: Any) -> int:
            return 1

    class FakeLibrary:
        def __getattr__(self, name: str) -> FakeFunction:
            del name
            return FakeFunction()

    monkeypatch.setattr(
        preview_clock.ctypes, "WinDLL", lambda *_args, **_kwargs: FakeLibrary()
    )
    api = preview_clock._CtypesWin32Api()
    set_timer = getattr(api, "_set_timer")
    assert len(set_timer.argtypes) == 6
    calls: list[tuple[Any, ...]] = []

    def record_call(*args: Any) -> int:
        calls.append(args)
        return 1

    setattr(api, "_set_timer", record_call)
    assert api.set_waitable_timer(123, -1, 0, None, None, False)
    assert len(calls) == 1
    assert len(calls[0]) == 6


def test_wndproc_accepts_native_window_creation() -> None:
    runtime = support.build_runtime_for_test(support.FakeWin32Api())

    # Given: Windows sends WM_NCCREATE before the message-only window exists.
    # When: the native window procedure handles that creation message.
    result = runtime._wndproc(0, 0x0081, 0, 0)

    # Then: creation is accepted so CreateWindowExW can complete.
    assert result == 1


def test_wndproc_defers_wake_callback_until_main_thread_drain() -> None:
    runtime = support.build_runtime_for_test(support.FakeWin32Api())
    calls: list[str] = []
    runtime.bind_wake_callback(lambda: calls.append("wake"))
    try:
        assert runtime._wndproc(0, preview_clock._PREVIEW_WAKE_MESSAGE, 0, 0) == 0
        assert calls == []
        assert runtime.consume_wake_signal() is True
        assert calls == ["wake"]
        assert runtime.consume_wake_signal() is False
    finally:
        runtime.destroy()


def test_native_wake_poll_drains_signal_when_runtime_exposes_deferred_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PollingRuntime(FakeNativeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.poll_pending = False

        def consume_wake_signal(self) -> bool:
            if not self.poll_pending:
                return False
            self.poll_pending = False
            self.pending_wake_source = "timer"
            assert self.wake_callback is not None
            self.wake_callback()
            return True

    root = FakeRoot()
    runtime = PollingRuntime()
    calls: list[str] = []
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(calls, "tick"),
        60,
        200,
    )
    try:
        # Given: a native runtime exposes a deferred wake drain.
        clock.start()
        poll_ids = [
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_poll_native_wake"
        ]
        assert poll_ids

        # When: the main-thread poll drains one pending native wake.
        runtime.publish_tick(_tick(runtime.controls[-1], 1))
        runtime.poll_pending = True
        poll_callback = root.callbacks.pop(poll_ids[0])
        poll_callback()

        # Then: dispatch happens once from the safe Tk callback.
        assert calls == ["tick", "tick"]
    finally:
        clock.stop()


def test_native_wake_poll_always_rearms_with_at_least_a_one_millisecond_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingRoot(FakeRoot):
        def __init__(self) -> None:
            super().__init__()
            self.delays: list[int] = []

        def after(self, delay_ms: int, callback: Callable[[], None]) -> str:
            self.delays.append(delay_ms)
            return super().after(delay_ms, callback)

    class PollingRuntime(FakeNativeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.poll_pending = False

        def consume_wake_signal(self) -> bool:
            if not self.poll_pending:
                return False
            self.poll_pending = False
            self.pending_wake_source = "timer"
            assert self.wake_callback is not None
            self.wake_callback()
            return True

    root = RecordingRoot()
    runtime = PollingRuntime()
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label([], "tick"),
        60,
        200,
    )
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    try:
        # Given: the native drain starts without a pending wake.
        clock.start()
        assert root.delays[-1] >= 1

        # When: a pending wake is consumed.
        runtime.publish_tick(_tick(runtime.controls[-1], 1))
        runtime.poll_pending = True
        poll_id = next(
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_poll_native_wake"
        )
        root.callbacks.pop(poll_id)()

        # Then: the re-armed delay is still at least 1 ms. A 0 ms self-reservation
        # is the one that starves Tk's own idle work, so a consumed wake must not
        # buy the loop a zero-delay spin.
        assert root.delays[-1] >= 1

        # Then: the following empty poll returns to the idle delay.
        next_poll_id = next(
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_poll_native_wake"
        )
        root.callbacks.pop(next_poll_id)()
        assert root.delays[-1] == 1
    finally:
        clock.stop()


def test_native_start_arms_and_rearms_the_timer_wake_poll_without_after_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """poll は ``after_idle`` ではなく ``after``（1 ms 以上）でしか予約されない。

    ``after_idle`` による自己再予約は Tk の idle 列だけが消化するため、同じ
    Interpreter の ``after(ms)`` タイマーが発火しなくなり、アプリ側の
    ``after()`` 駆動処理が全て止まる（実測: 3 秒で心拍 0 回）。
    """

    class PollingRuntime(FakeNativeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.poll_pending = False

        def consume_wake_signal(self) -> bool:
            if not self.poll_pending:
                return False
            self.poll_pending = False
            self.pending_wake_source = "timer"
            assert self.wake_callback is not None
            self.wake_callback()
            return True

    root = AfterIdleRoot()
    runtime = PollingRuntime()
    calls: list[str] = []
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label(calls, "tick"),
        60,
        200,
    )
    armed_poll_id = ""
    try:
        # Given: a real Tk root shape that offers after_idle.
        clock.start()

        # Then: after_idle is never used at all, and the poll is a plain after()
        # timer with a delay of at least 1 ms.
        assert root.idle_callbacks == {}, root.idle_callbacks
        assert root.after_delays[-1] >= 1
        poll_ids = _pending_poll_ids(root)
        assert len(poll_ids) == 1, poll_ids

        # When: the poll runs with no pending native wake.
        root.callbacks.pop(poll_ids[0])()

        # Then: it re-arms exactly one after() poll and dispatches nothing.
        assert root.idle_callbacks == {}, root.idle_callbacks
        assert root.after_delays[-1] >= 1
        assert len(_pending_poll_ids(root)) == 1
        assert calls == ["tick"]

        # When: a native wake is pending and the re-armed poll runs.
        runtime.publish_tick(_tick(runtime.controls[-1], 1))
        runtime.poll_pending = True
        root.callbacks.pop(_pending_poll_ids(root)[0])()

        # Then: the wake is dispatched and the poll stays armed through after().
        assert calls == ["tick", "tick"]
        assert runtime.main_dispatch_count == 1
        assert root.idle_callbacks == {}, root.idle_callbacks
        assert root.after_delays[-1] >= 1
        armed_poll_ids = _pending_poll_ids(root)
        assert len(armed_poll_ids) == 1, armed_poll_ids
        armed_poll_id = armed_poll_ids[0]
    finally:
        clock.stop()

    # Then: stopping the clock cancels the armed poll.
    assert armed_poll_id in root.cancelled
    assert _pending_poll_ids(root) == []


def test_native_wake_poll_does_not_starve_tk_after_timers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """poll の再予約は、実 Tk 上の無関係な ``after(ms)`` タイマーを餓死させない。

    起動直後の ``_WINDOW_S`` 秒間、poll と無関係な 20 ms 心拍が 5 回以上鳴る
    こと。``after_idle`` の自己再予約だと Tk のイベントループが idle 排出に
    占有され、``after(ms)`` タイマーが一度も発火しないので心拍は 0 のままに
    なる（実測: 3 秒で心拍 0 回）。

    終了判定は Tk タイマーに依存しない。``mainloop()`` / ``update()`` は
    不具合時に戻らないので使わず、``doonevent`` を壁時計と poll 呼び出し
    回数の双方で打ち切る。root の生存については ``_PUMPED_TK_ROOTS`` の
    コメントを参照。
    """
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"no display for the real-Tk poll starvation check: {error}")
    root.withdraw()
    runtime = _NeverWakingRuntime(poll_budget=_POLL_BUDGET)
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult(schedule="active"), 60, 200
    )
    runtime.bind_stop_clock(clock.stop)
    beats = 0

    def beat() -> None:
        nonlocal beats
        beats += 1
        root.after(_HEARTBEAT_MS, beat)

    try:
        clock.start()

        # Given: an unrelated after(20 ms) heartbeat on the same interpreter.
        root.after(_HEARTBEAT_MS, beat)

        # When: the event loop is pumped for the wall-clock window.
        deadline = time.perf_counter() + _WINDOW_S
        while time.perf_counter() < deadline and not runtime.poll_budget_exhausted:
            root.tk.dooneevent(_tkinter.DONT_WAIT | _tkinter.ALL_EVENTS)

        # Then: the app's own after() timers kept firing. A poll that owns the
        # event loop instead leaves the window at zero heartbeats.
        assert beats >= _MIN_HEARTBEATS, (
            f"{beats} heartbeat(s) in a {_WINDOW_S:.1f}s window after "
            f"{runtime.poll_calls} poll call(s); the native wake poll starved "
            "Tk's own after(ms) timers"
        )
    finally:
        clock.stop()
        root.destroy()
        _PUMPED_TK_ROOTS.append(root)


def test_native_wake_drain_processes_all_queued_wakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DrainingRuntime(FakeNativeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.remaining_signals = 0
            self.drain_calls = 0

        def consume_wake_signal(self) -> bool:
            if self.remaining_signals == 0:
                return False
            self.remaining_signals -= 1
            self.drain_calls += 1
            self.pending_wake_source = "timer"
            assert self.wake_callback is not None
            self.wake_callback()
            return True

    root = FakeRoot()
    runtime = DrainingRuntime()
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label([], "tick"),
        60,
        200,
    )
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    try:
        # Given: multiple native wakes are queued before service runs.
        clock.start()
        runtime.remaining_signals = 3

        # When: the main-thread async service drains the queue.
        drained = clock._drain_native_wakes()

        # Then: all queued wakes are consumed in one service callback.
        assert drained is True
        assert runtime.drain_calls == 3
        assert runtime.remaining_signals == 0
    finally:
        clock.stop()


def test_native_wake_drain_hands_the_loop_back_within_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EndlessRuntime(FakeNativeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.drain_calls = 0

        def consume_wake_signal(self) -> bool:
            self.drain_calls += 1
            if self.drain_calls > 100:
                raise RuntimeError("drain spun past its budget")
            time.sleep(0.001)
            return True

    root = FakeRoot()
    runtime = EndlessRuntime()
    clock = preview_clock.PreviewClock(
        root,
        _dispatch_with_label([], "tick"),
        60,
        200,
    )
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    try:
        # Given: a wake source that never reports an empty queue.
        clock.start()

        # When: the main-thread drain runs.
        drained = clock._drain_native_wakes()

        # Then: the drain hands the loop back inside its time budget.
        assert drained is True
        assert runtime.drain_calls <= 10
    finally:
        clock.stop()


def test_startup_control_survives_worker_initialization_and_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    schedules = iter(("active", "idle"))
    runtimes: list[Any] = []

    def dispatch() -> preview_clock.DispatchResult:
        return preview_clock.DispatchResult(schedule=next(schedules))

    def factory(**_kwargs: Any) -> Any:
        runtime = support.build_runtime_for_test(support.FakeWin32Api())
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(preview_clock, "_create_native_runtime", factory)
    clock = preview_clock.PreviewClock(root, dispatch, 60, 200)
    try:
        clock.start()
        first = runtimes[-1]
        first_control = first._initial_control
        assert first_control is not None
        assert first._context.config.epoch == first_control.epoch
        assert first._context.config.generation == first_control.generation
        assert first._state.current_control == first_control
        assert first_control.active is True
        assert clock.stop() is preview_clock.StopResult.STOPPED

        clock.start()
        second = runtimes[-1]
        second_control = second._initial_control
        assert second_control is not None
        assert second_control.active is False
        assert second_control.epoch == int(clock.health_snapshot()["epoch"])
        assert second._context.config.epoch == second_control.epoch
    finally:
        for runtime in runtimes:
            thread = getattr(runtime, "_thread", None)
            if thread is not None and thread.is_alive():
                runtime.request_stop()
                assert runtime.join(1.0)
            if getattr(runtime, "_hwnd", 0) or getattr(runtime, "_class_atom", 0):
                runtime.destroy()


def test_successful_native_destroy_preserves_cleanup_health_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    try:
        assert clock.stop() is preview_clock.StopResult.STOPPED
        snapshot = clock.health_snapshot()
        assert snapshot["running"] is False
        assert snapshot["window_class_destroyed"] is True
        assert snapshot["window_class_unregistered"] is True
    finally:
        if runtime.destroy_calls == 0:
            runtime.destroy()


def test_stop_during_pending_after_cleanup_never_enters_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime(stop_join_results=[False, True])
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    try:
        runtime.health["last_sequence"] = 2
        root.run_next()
        assert clock.stop() is preview_clock.StopResult.PENDING
        assert clock.mode() == "stopped"
        assert runtime.destroy_calls == 0
        root.run_next()
        assert clock.health_snapshot()["state"] == "stopped"
        assert runtime.destroy_calls == 1
    finally:
        if runtime.destroy_calls == 0:
            clock.stop()


def test_join_failure_keeps_teardown_pending_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    runtime = FakeNativeRuntime(
        stop_join_results=[True],
        join_exceptions=[RuntimeError("injected join failure"), None],
    )
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    try:
        assert clock.stop() is preview_clock.StopResult.PENDING
        assert clock.health_snapshot()["state"] == "teardown_pending"
        assert runtime.destroy_calls == 0
        root.run_next()
        assert clock.health_snapshot()["state"] == "stopped"
        assert runtime.destroy_calls == 1
    finally:
        if runtime.destroy_calls == 0:
            clock.stop()


def test_retry_scheduling_failure_keeps_live_native_cleanup_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = RetryFailRoot()
    runtime = FakeNativeRuntime(stop_join_results=[False, True])
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    try:
        clock.start()
        assert clock.stop() is preview_clock.StopResult.PENDING
        assert clock.health_snapshot()["state"] == "teardown_pending"
        assert runtime.destroy_calls == 0
        assert clock.stop() is preview_clock.StopResult.STOPPED
        assert runtime.destroy_calls == 1
    finally:
        if runtime.destroy_calls == 0:
            clock.stop()


def test_destroy_window_false_retains_references_until_retry() -> None:
    api = support.FakeWin32Api(destroy_window_results=[False, True])
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    try:
        runtime.request_stop()
        assert runtime.join(1.0)
        with pytest.raises(preview_clock._PreviewClockError, match="DestroyWindow"):
            runtime.destroy()
        assert runtime._hwnd != 0
        assert runtime._wndproc_ref is not None
        assert runtime.snapshot()["window_class_destroyed"] is False
        runtime.destroy()
        assert runtime.snapshot()["window_class_destroyed"] is True
    finally:
        if runtime._hwnd or runtime._class_atom:
            runtime.destroy()


def test_unregister_class_false_retains_class_references_until_retry() -> None:
    api = support.FakeWin32Api(unregister_class_results=[False, True])
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    try:
        runtime.request_stop()
        assert runtime.join(1.0)
        with pytest.raises(preview_clock._PreviewClockError, match="UnregisterClass"):
            runtime.destroy()
        assert runtime._hwnd == 0
        assert runtime._class_atom != 0
        assert runtime._wndproc_ref is not None
        assert runtime.snapshot()["window_class_unregistered"] is False
        runtime.destroy()
        assert runtime.snapshot()["window_class_unregistered"] is True
    finally:
        if runtime._hwnd or runtime._class_atom:
            runtime.destroy()


def test_non_post_worker_error_publishes_one_sanitized_error_wake() -> None:
    api = support.FakeWin32Api(set_timer_results=[False])
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    try:
        assert runtime.join(1.0)
        wake = runtime.consume_wake()
        assert wake is not None
        assert wake.reason == "error"
        assert wake.error_name == "SetWaitableTimer"
        health = runtime.snapshot()
        assert health["last_worker_error_name"] == "SetWaitableTimer"
        assert health["post_failure_count"] == 0
        assert len(api.post_message_calls) == 1
    finally:
        _close_native_runtime(runtime)


def test_fallback_schedule_transitions_and_stop_advance_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    schedules = iter(("active", "idle", "active"))

    def fail_factory(**_kwargs: Any) -> FakeNativeRuntime:
        raise preview_clock._PreviewClockError("injected high-resolution init failure")

    monkeypatch.setattr(preview_clock, "_create_native_runtime", fail_factory)
    clock = preview_clock.PreviewClock(
        root,
        lambda: preview_clock.DispatchResult(schedule=next(schedules)),
        15,
        200,
    )
    clock.start()
    start_generation = int(clock.health_snapshot()["generation"])
    root.run_next()
    assert int(clock.health_snapshot()["generation"]) == start_generation + 1
    root.run_next()
    assert int(clock.health_snapshot()["generation"]) == start_generation + 2
    before_stop = int(clock.health_snapshot()["generation"])
    assert clock.stop() is preview_clock.StopResult.STOPPED
    assert int(clock.health_snapshot()["generation"]) == before_stop + 1


def test_wndproc_base_exception_boundary_returns_lresult_and_preserves_refs() -> None:
    api = support.FakeWin32Api()
    runtime = support.build_runtime_for_test(api)

    def explode() -> None:
        raise KeyboardInterrupt("foreign callback failure")

    runtime.bind_wake_callback(explode)
    try:
        assert runtime._wndproc(0, preview_clock._PREVIEW_WAKE_MESSAGE, 0, 0) == 0
        assert runtime.consume_wake_signal() is True
        assert runtime.snapshot()["last_worker_error_name"] == "WNDPROC"
        assert runtime._wndproc_ref is not None
    finally:
        runtime.destroy()


def test_stop_signal_failure_retries_before_join_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = support.FakeWin32Api(
        cancel_timer_results=[False, True],
        set_event_results=[True, False, True],
    )
    runtime = support.build_runtime_for_test(api)
    root = FakeRoot()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    try:
        clock.start()
        assert clock.stop() is preview_clock.StopResult.PENDING
        assert clock.health_snapshot()["state"] == "teardown_pending"
        failed_health = runtime.snapshot()
        assert failed_health["stop_signal_failure_count"] == 2
        assert failed_health["last_stop_signal_error_name"] == "CancelWaitableTimer"
        assert failed_health["last_worker_error_name"] == "CancelWaitableTimer"
        assert runtime._hwnd != 0
        assert runtime._class_atom != 0
        assert len(api.cancel_waitable_timer_calls) == 1
        assert len(api.set_event_calls) == 2
        root.run_next()
        snapshot = clock.health_snapshot()
        assert snapshot["state"] == "stopped"
        assert snapshot["running"] is False
        assert len(api.cancel_waitable_timer_calls) == 2
        assert len(api.set_event_calls) == 3
    finally:
        if runtime.has_live_resources():
            _force_stop_signal_success(api)
            _close_native_runtime(runtime)


def test_failed_stop_signal_keeps_resources_pending_without_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = support.FakeWin32Api(
        cancel_timer_results=[False, False, False],
        set_event_results=[True, False, False, False],
    )
    runtime = support.build_runtime_for_test(api)
    root = FakeRoot()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    try:
        clock.start()
        assert clock.stop() is preview_clock.StopResult.PENDING
        root.run_next()
        assert clock.stop() is preview_clock.StopResult.PENDING
        assert clock.health_snapshot()["state"] == "teardown_pending"
        assert runtime.has_live_resources()
        assert runtime._hwnd != 0
        assert runtime._class_atom != 0
        assert runtime._wndproc_ref is not None
        assert api.destroy_window_calls == []
        assert api.close_handle_calls == []
        api.cancel_timer_results.extend([True, True])
        api.set_event_results.extend([True, True])
        assert clock.stop() is preview_clock.StopResult.STOPPED
        assert runtime.has_live_resources() is False
    finally:
        if runtime.has_live_resources():
            _force_stop_signal_success(api)
            _close_native_runtime(runtime)


def test_recovery_stop_signal_failure_remains_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = support.FakeWin32Api(
        cancel_timer_results=[False, True],
        set_event_results=[True, False, True],
    )
    runtime = support.build_runtime_for_test(api)
    root = FakeRoot()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    try:
        clock.start()
        control = runtime._state.current_control
        assert control is not None
        runtime._state.publish_tick(control, 1, 1, 10_000_000)
        runtime._state.publish_tick(control, 2, 2, 10_000_000)
        root.run_next()
        assert clock.health_snapshot()["state"] == "teardown_pending"
        assert runtime.has_live_resources()
        root.run_next()
        assert clock.health_snapshot()["state"] == "after"
    finally:
        if runtime.has_live_resources():
            _force_stop_signal_success(api)
            _close_native_runtime(runtime)


def test_new_fallback_health_does_not_reuse_archived_runtime_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = FakeRoot()
    first_runtime = FakeNativeRuntime()
    first_runtime.health.update(
        {
            "qpc_frequency_hz": 12_345,
            "worker_thread_native_id": 999,
            "last_sequence": 7,
            "worker_tick_published_count": 7,
            "main_dispatch_count": 6,
        }
    )
    factory_calls = 0

    def factory(**_kwargs: Any) -> FakeNativeRuntime:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return first_runtime
        raise preview_clock._PreviewClockError("injected second native init failure")

    monkeypatch.setattr(preview_clock, "_create_native_runtime", factory)
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    assert clock.stop() is preview_clock.StopResult.STOPPED
    cleanup_health = clock.health_snapshot()
    assert cleanup_health["cleanup_window_class_destroyed"] is True
    assert cleanup_health["cleanup_window_class_unregistered"] is True
    assert cleanup_health["cleanup_worker_running"] is False
    assert cleanup_health["cleanup_qpc_frequency_hz"] == 12_345
    assert cleanup_health["cleanup_worker_thread_native_id"] == 999
    assert cleanup_health["cleanup_last_sequence"] == 7

    clock.start()
    fallback_health = clock.health_snapshot()
    assert fallback_health["state"] == "after"
    assert fallback_health["qpc_frequency_hz"] == 0
    assert fallback_health["worker_thread_native_id"] == 0
    assert fallback_health["last_sequence"] == 0
    assert fallback_health["worker_tick_published_count"] == 0
    assert fallback_health["main_dispatch_count"] == 0
    assert fallback_health["cleanup_qpc_frequency_hz"] == 12_345
    assert clock.stop() is preview_clock.StopResult.STOPPED


def test_successful_stop_archives_raw_evidence_for_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = support.FakeWin32Api(cancel_timer_results=[False])
    runtime = support.build_runtime_for_test(api)
    root = FakeRoot()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    try:
        clock.start()
        root.run_next()
        control = runtime._state.current_control
        assert control is not None
        runtime._state.record_control_wake(control, 10_000_000, 100)
        runtime._state.publish_tick(control, 200, 200, 10_000_000)
        runtime._state.record_worker_error("InjectedWorkerError", 42)
        assert clock.stop() is preview_clock.StopResult.STOPPED
        rows = clock._drain_evidence()
        record_types = {str(row.get("record_type")) for row in rows}
        assert {
            "worker_tick",
            "control_wake",
            "worker_error",
            "stop_signal_failure",
            "health_callback",
        } <= record_types

        monkeypatch.setattr(
            preview_clock,
            "_create_native_runtime",
            lambda **_kwargs: (_ for _ in ()).throw(
                preview_clock._PreviewClockError("injected fallback failure")
            ),
        )
        clock.start()
        fallback_health = clock.health_snapshot()
        assert fallback_health["state"] == "after"
        assert fallback_health["qpc_frequency_hz"] == 0
        assert fallback_health["worker_thread_native_id"] == 0
        assert fallback_health["last_sequence"] == 0
        assert clock.stop() is preview_clock.StopResult.STOPPED
    finally:
        if runtime.has_live_resources():
            _force_stop_signal_success(api)
            _close_native_runtime(runtime)


def test_health_callback_is_not_rearmed_while_one_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a native clock with one health callback already scheduled.
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    try:
        first_health_id = next(
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_health_callback"
        )

        # When: a valid control wake arrives before the health timeout.
        runtime.emit_wake("control")

        # Then: the existing health callback remains the sole pending callback.
        health_ids = [
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_health_callback"
        ]
        assert health_ids == [first_health_id]
    finally:
        clock.stop()


def test_health_callback_rearms_after_a_healthy_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a native clock with a pending health callback.
    root = FakeRoot()
    runtime = FakeNativeRuntime()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    try:
        first_health_id = next(
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_health_callback"
        )
        # When: the health callback observes a healthy worker.
        root.callbacks.pop(first_health_id)()

        # Then: one replacement health callback is pending.
        health_ids = [
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_health_callback"
        ]
        assert len(health_ids) == 1
        assert health_ids[0] != first_health_id
    finally:
        clock.stop()


def test_health_arm_does_not_duplicate_when_after_services_a_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a root that services a native wake while arming the health timer.
    class ReentrantRoot(FakeRoot):
        def __init__(self) -> None:
            super().__init__()
            self.runtime: FakeNativeRuntime | None = None
            self.triggered = False

        def after(self, delay_ms: int, callback: Callable[[], None]) -> str:
            after_id = super().after(delay_ms, callback)
            if (
                not self.triggered
                and callback.__name__ == "_health_callback"
                and self.runtime is not None
            ):
                self.triggered = True
                self.runtime.emit_wake("control")
            return after_id

    root = ReentrantRoot()
    runtime = FakeNativeRuntime()
    root.runtime = runtime
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    clock.start()
    try:
        # When: startup completes after the reentrant wake.
        health_ids = [
            after_id
            for after_id, callback in root.callbacks.items()
            if callback.__name__ == "_health_callback"
        ]

        # Then: only one health timer remains pending.
        assert len(health_ids) == 1
    finally:
        clock.stop()


def test_stop_event_is_signaled_before_waitable_timer_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = support.FakeWin32Api()
    runtime = support.build_runtime_for_test(api)
    root = FakeRoot()
    monkeypatch.setattr(
        preview_clock, "_create_native_runtime", lambda **_kwargs: runtime
    )
    clock = preview_clock.PreviewClock(
        root, lambda: preview_clock.DispatchResult("active"), 60, 200
    )
    try:
        clock.start()
        api.stop_signal_order.clear()
        assert clock.stop() is preview_clock.StopResult.STOPPED
        assert api.stop_signal_order == ["set_event", "cancel_timer"]
    finally:
        if runtime.has_live_resources():
            _close_native_runtime(runtime)
