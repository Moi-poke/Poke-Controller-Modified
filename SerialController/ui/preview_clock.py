# noqa: E501  # noqa: SIZE_OK - approved Task 4 single-module Win32 runtime and facade contract.
"""High-resolution preview clock with an exclusive Tk ``after`` fallback."""

from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal, Protocol, assert_never

from loguru import logger

_SUPPORTED_FPS: Final[tuple[int, ...]] = (5, 15, 30, 45, 60)
_WM_APP: Final[int] = 0x8000
_WM_NCCREATE: Final[int] = 0x0081
_PREVIEW_WAKE_MESSAGE: Final[int] = _WM_APP + 0xC1
_HWND_MESSAGE: Final[int] = -3
_INFINITE: Final[int] = 0xFFFFFFFF
_WAIT_OBJECT_0: Final[int] = 0
_WAIT_FAILED: Final[int] = 0xFFFFFFFF
_CREATE_WAITABLE_TIMER_HIGH_RESOLUTION: Final[int] = 0x00000002
_TIMER_MODIFY_STATE: Final[int] = 0x00000002
_SYNCHRONIZE: Final[int] = 0x00100000
_ZERO: Final[int] = 0
_NATIVE_WAKE_POLL_MS: Final[int] = 0
_NATIVE_WAKE_IDLE_POLL_MS: Final[int] = 1


class StopResult(StrEnum):
    """Result of a main-thread stop request."""

    STOPPED = "stopped"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Result returned by one main-thread preview tick."""

    schedule: Literal["active", "idle"]


@dataclass(frozen=True, slots=True)
class PreviewTick:
    """Immutable QPC-domain tick published by the native worker."""

    epoch: int
    generation: int
    configured_fps: int
    sequence: int
    deadline_qpc: int
    emitted_qpc: int


@dataclass(frozen=True, slots=True)
class PreviewWake:
    """Immutable public wake record used by the native runtime boundary."""

    kind: Literal["clock_ready", "tick", "error"]
    epoch: int
    generation: int
    configured_fps: int
    sequence: int = 0
    qpc_frequency_hz: int = 0
    error_name: str = ""


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    """Immutable view of the worker's published health state."""

    worker_thread_native_id: int
    last_sequence: int
    last_wake_qpc: int
    post_failure_count: int
    last_post_error: int
    running: bool


class _PreviewClockError(RuntimeError):
    """Raised when native preview-clock setup cannot be completed."""


class _ControlKind(StrEnum):
    START = "start"
    SET_FPS = "set_fps"
    ACTIVE = "active"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class _ControlState:
    kind: _ControlKind
    epoch: int
    generation: int
    configured_fps: int
    active: bool


class _ClockState(StrEnum):
    STOPPED = "stopped"
    STARTING_NATIVE = "starting_native"
    HIGH_RESOLUTION = "high_resolution"
    AFTER = "after"
    STOPPING = "stopping"
    TEARDOWN_PENDING = "teardown_pending"
    FAILED = "failed"


class _StaleTickDrop(StrEnum):
    """Sentinel outcome for a mailbox tick dropped as a superseded generation.

    ``consume_tick`` returns ``None`` for two unrelated reasons, and the main
    thread must be able to tell them apart: an empty latest-wins mailbox (the
    blit had no new frame to draw) versus a tick whose epoch/generation no
    longer matches (already counted by ``stale_tick_dropped_count``). Counting
    both in one place would conflate pacer phase offsets with stale drops and
    destroy the discrimination, so the stale case is returned explicitly.
    """

    STALE = "stale_tick_drop"


class _NativeRuntime(Protocol):
    def bind_wake_callback(self, callback: Callable[[], None]) -> None: ...

    def start(self) -> None: ...

    def request_control(self, control: _ControlState) -> None: ...

    def request_stop(self) -> None: ...

    def join(self, timeout_s: float) -> bool: ...

    def destroy(self) -> None: ...

    def consume_tick(self) -> PreviewTick | _StaleTickDrop | None: ...

    def snapshot(self) -> Mapping[str, int | str | bool]: ...

    def begin_accounting_window(self) -> None: ...

    def end_accounting_window(self) -> Mapping[str, int | str | bool]: ...

    def drain_evidence(self) -> tuple[Mapping[str, object], ...]:  # noqa: E501  # noqa: OBJECT_OK
        ...


def _validate_configured_fps(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("configured FPS must be an integer")  # noqa: E501  # noqa: GENERIC_ERR_OK
    if value not in _SUPPORTED_FPS:
        raise ValueError("configured FPS must be one of 5, 15, 30, 45, 60")  # noqa: E501  # noqa: GENERIC_ERR_OK
    return value


def _validate_idle_interval(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("idle interval must be a positive integer")  # noqa: E501  # noqa: GENERIC_ERR_OK
    return value


def _qpc_interval(frequency_hz: int, rate_hz: float) -> int:
    return max(1, math.ceil(frequency_hz / rate_hz))


def _negative_due_100ns(deadline_qpc: int, now_qpc: int, frequency_hz: int) -> int:
    delta_qpc = max(0, deadline_qpc - now_qpc)
    due_100ns = max(1, math.ceil(delta_qpc * 10_000_000 / frequency_hz))
    return -due_100ns


def _advance_deadline(
    deadline_qpc: int, now_qpc: int, interval_qpc: int
) -> tuple[int, int]:
    """Re-anchor the deadline and report how many whole periods were jumped.

    ``periods_skipped`` is ``0`` while the deadline is still in the future and
    ``(now_qpc - deadline_qpc) // interval_qpc`` once it is due, so an on-time
    tick reports nothing. The returned deadline keeps the legacy
    ``skipped = periods_skipped + 1`` grid advance, so the firing schedule of the
    pacer is unchanged; only what is reported is new.
    """
    if deadline_qpc > now_qpc:
        return deadline_qpc, 0
    periods_skipped = (now_qpc - deadline_qpc) // interval_qpc
    return deadline_qpc + (periods_skipped + 1) * interval_qpc, periods_skipped


def _validate_diagnostic_rate(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("diagnostic rate must be finite and positive")  # noqa: E501  # noqa: GENERIC_ERR_OK
    return value


if os.name == "nt":
    _WNDPROC_FACTORY = ctypes.WINFUNCTYPE
else:
    _WNDPROC_FACTORY = ctypes.CFUNCTYPE

_WNDPROC_TYPE = _WNDPROC_FACTORY(
    ctypes.c_ssize_t,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
)
type _WndProc = Any


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


class _RuntimeApi(Protocol):
    def query_performance_frequency(self) -> int: ...

    def query_performance_counter(self) -> int: ...

    def create_waitable_timer_ex(
        self,
        attributes: int | None,
        name: str | None,
        flags: int,
        desired_access: int,
    ) -> int: ...

    def create_event(
        self,
        attributes: int | None,
        manual_reset: bool,
        initial_state: bool,
        name: str | None,
    ) -> int: ...

    def register_class_ex(
        self,
        class_name: str,
        instance: int,
        window_proc: _WndProc,
    ) -> int: ...

    def create_window_ex(
        self,
        class_name: str,
        instance: int,
        parent: int,
    ) -> int: ...

    def set_waitable_timer(
        self,
        handle: int,
        due_100ns: int,
        period_ms: int,
        completion_routine: None,
        completion_argument: None,
        resume: bool,
    ) -> bool: ...

    def cancel_waitable_timer(self, handle: int) -> bool: ...

    def set_event(self, handle: int) -> bool: ...

    def event_is_set(self, handle: int) -> bool: ...

    def wait_for_multiple_objects(
        self,
        handles: Sequence[int],
        wait_all: bool,
        timeout_ms: int,
    ) -> int: ...

    def post_message(
        self, hwnd: int, message: int, wparam: int, lparam: int
    ) -> bool: ...

    def destroy_window(self, hwnd: int) -> bool: ...

    def unregister_class(self, class_name: str, instance: int) -> bool: ...

    def close_handle(self, handle: int) -> bool: ...

    def get_last_error(self) -> int: ...

    def get_module_handle(self, module_name: str | None) -> int: ...

    def native_window_proc(self) -> _WndProc: ...


def _as_handle(value: int | ctypes.c_void_p | None) -> int:
    if value is None:
        return _ZERO
    if isinstance(value, ctypes.c_void_p):
        return int(value.value or _ZERO)
    return int(value)


class _CtypesWin32Api:
    """Small explicit-signature adapter around kernel32."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise _PreviewClockError("Win32 API is only available on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._query_frequency = self._kernel32.QueryPerformanceFrequency
        self._query_counter = self._kernel32.QueryPerformanceCounter
        self._create_timer = self._kernel32.CreateWaitableTimerExW
        self._create_event = self._kernel32.CreateEventW
        self._register_class = self._user32.RegisterClassExW
        self._create_window = self._user32.CreateWindowExW
        self._set_timer = self._kernel32.SetWaitableTimer
        self._cancel_timer = self._kernel32.CancelWaitableTimer
        self._set_event = self._kernel32.SetEvent
        self._wait = self._kernel32.WaitForMultipleObjects
        self._post_message = self._user32.PostMessageW
        self._destroy_window = self._user32.DestroyWindow
        self._unregister_class = self._user32.UnregisterClassW
        self._close_handle = self._kernel32.CloseHandle
        self._get_last_error = self._kernel32.GetLastError
        self._get_module_handle = self._kernel32.GetModuleHandleW
        self._native_wndproc = self._user32.DefWindowProcW
        self._native_wndproc.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        self._native_wndproc.restype = ctypes.c_ssize_t
        self._query_frequency.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
        self._query_frequency.restype = ctypes.c_int
        self._query_counter.argtypes = [ctypes.POINTER(ctypes.c_longlong)]
        self._query_counter.restype = ctypes.c_int
        self._create_timer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self._create_timer.restype = ctypes.c_void_p
        self._create_event.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_wchar_p,
        ]
        self._create_event.restype = ctypes.c_void_p
        self._register_class.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
        self._register_class.restype = ctypes.c_ushort
        self._create_window.argtypes = [
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._create_window.restype = ctypes.c_void_p
        self._set_timer.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_longlong),
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self._set_timer.restype = ctypes.c_int
        self._cancel_timer.argtypes = [ctypes.c_void_p]
        self._cancel_timer.restype = ctypes.c_int
        self._set_event.argtypes = [ctypes.c_void_p]
        self._set_event.restype = ctypes.c_int
        self._wait.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        self._wait.restype = ctypes.c_uint32
        self._post_message.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        self._post_message.restype = ctypes.c_int
        self._destroy_window.argtypes = [ctypes.c_void_p]
        self._destroy_window.restype = ctypes.c_int
        self._unregister_class.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        self._unregister_class.restype = ctypes.c_int
        self._close_handle.argtypes = [ctypes.c_void_p]
        self._close_handle.restype = ctypes.c_int
        self._get_last_error.argtypes = []
        self._get_last_error.restype = ctypes.c_uint32
        self._get_module_handle.argtypes = [ctypes.c_wchar_p]
        self._get_module_handle.restype = ctypes.c_void_p
        self._last_wndclass: _WNDCLASSEXW | None = None

    def query_performance_frequency(self) -> int:
        value = ctypes.c_longlong()
        if not self._query_frequency(ctypes.byref(value)):
            raise OSError(self.get_last_error(), "QueryPerformanceFrequency failed")
        return int(value.value)

    def query_performance_counter(self) -> int:
        value = ctypes.c_longlong()
        if not self._query_counter(ctypes.byref(value)):
            raise OSError(self.get_last_error(), "QueryPerformanceCounter failed")
        return int(value.value)

    def create_waitable_timer_ex(
        self,
        attributes: int | None,
        name: str | None,
        flags: int,
        desired_access: int,
    ) -> int:
        return _as_handle(self._create_timer(attributes, name, flags, desired_access))

    def create_event(
        self,
        attributes: int | None,
        manual_reset: bool,
        initial_state: bool,
        name: str | None,
    ) -> int:
        return _as_handle(
            self._create_event(
                attributes,
                int(manual_reset),
                int(initial_state),
                name,
            )
        )

    def register_class_ex(
        self,
        class_name: str,
        instance: int,
        window_proc: _WndProc,
    ) -> int:
        window_class = _WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        window_class.lpfnWndProc = ctypes.cast(window_proc, ctypes.c_void_p)
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        self._last_wndclass = window_class
        return int(self._register_class(ctypes.byref(window_class)))

    def create_window_ex(
        self,
        class_name: str,
        instance: int,
        parent: int,
    ) -> int:
        return _as_handle(
            self._create_window(
                0,
                class_name,
                "PokeConPreviewClock",
                0,
                0,
                0,
                0,
                0,
                parent,
                None,
                instance,
                None,
            )
        )

    def set_waitable_timer(
        self,
        handle: int,
        due_100ns: int,
        period_ms: int,
        completion_routine: None,
        completion_argument: None,
        resume: bool,
    ) -> bool:
        due_time = ctypes.c_longlong(due_100ns)
        return bool(
            self._set_timer(
                handle,
                ctypes.byref(due_time),
                period_ms,
                completion_routine,
                completion_argument,
                int(resume),
            )
        )

    def cancel_waitable_timer(self, handle: int) -> bool:
        return bool(self._cancel_timer(handle))

    def set_event(self, handle: int) -> bool:
        return bool(self._set_event(handle))

    def event_is_set(self, handle: int) -> bool:
        return False

    def wait_for_multiple_objects(
        self,
        handles: Sequence[int],
        wait_all: bool,
        timeout_ms: int,
    ) -> int:
        if not handles:
            return _WAIT_FAILED
        array_type = ctypes.c_void_p * len(handles)
        handle_array = array_type(*handles)
        return int(
            self._wait(
                len(handles),
                handle_array,
                int(wait_all),
                timeout_ms,
            )
        )

    def post_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> bool:
        return bool(self._post_message(hwnd, message, wparam, lparam))

    def destroy_window(self, hwnd: int) -> bool:
        return bool(self._destroy_window(hwnd))

    def unregister_class(self, class_name: str, instance: int) -> bool:
        return bool(self._unregister_class(class_name, instance))

    def close_handle(self, handle: int) -> bool:
        return bool(self._close_handle(handle))

    def get_last_error(self) -> int:
        return int(self._get_last_error())

    def get_module_handle(self, module_name: str | None) -> int:
        return _as_handle(self._get_module_handle(module_name))

    def native_window_proc(self) -> _WndProc:
        return self._native_wndproc

    @property
    def last_wndclass(self) -> _WNDCLASSEXW | None:
        return self._last_wndclass


@dataclass(frozen=True, slots=True)
class _WorkerConfig:
    epoch: int
    generation: int
    configured_fps: int
    diagnostic_rate_hz: float | None
    idle_interval_ms: int
    deadline_qpc: int
    interval_qpc: int


@dataclass(frozen=True, slots=True)
class _WorkerHandles:
    stop_event: int
    control_event: int
    timer_handle: int


@dataclass(frozen=True, slots=True)
class _CleanupEvidence:
    window_class_destroyed: bool
    window_class_unregistered: bool
    worker_running: bool
    qpc_frequency_hz: int
    worker_thread_native_id: int
    last_sequence: int
    worker_tick_published_count: int
    main_dispatch_count: int


@dataclass(frozen=True, slots=True)
class _RuntimeWake:
    reason: Literal["clock_ready", "tick", "control", "error"]
    epoch: int
    generation: int
    configured_fps: int
    sequence: int = 0
    qpc_frequency_hz: int = 0
    error_name: str = ""
    error_value: int = 0
    qpc: int = 0


class _SharedState:
    """Lock-protected mailbox, health, wake, and accounting state."""

    def __init__(self, configured_fps: int, diagnostic_rate_hz: float | None) -> None:
        self.lock = threading.Lock()
        self.configured_fps = configured_fps
        self.diagnostic_rate_hz = diagnostic_rate_hz
        self.control: _ControlState | None = None
        self.current_control: _ControlState | None = None
        self.pending: PreviewTick | None = None
        self.wakes: deque[_RuntimeWake] = deque()
        self.evidence: list[Mapping[str, object]] = []  # noqa: E501  # noqa: OBJECT_OK
        self.qpc_frequency_hz = 0
        self.worker_thread_native_id = 0
        self.last_sequence = 0
        self.last_delivered_sequence = 0
        self.last_wake_qpc = 0
        self.post_failure_count = 0
        self.last_post_error = 0
        self.last_worker_error_name = ""
        self.last_worker_error_value = 0
        self.stop_signal_succeeded = True
        self.stop_signal_failure_count = 0
        self.last_stop_signal_error_name = ""
        self.last_stop_signal_error_value = 0
        self.running = False
        self.worker_tick_published_count = 0
        self.period_skipped_count = 0
        self.pending_tick_superseded_count = 0
        self.main_dispatch_count = 0
        self.stale_tick_dropped_count = 0
        self.dispatch_in_flight = False
        self.reentrant_wake_count = 0
        self.last_health_callback_result = 0
        self.window_class_destroyed = False
        self.window_class_unregistered = False
        self._accounting_events: list[tuple[str, int]] = []
        self._accounting_start_index: int | None = None
        self._accounting_start_pending = False
        self._accounting_start_period_skipped = 0

    def set_qpc_frequency(self, frequency_hz: int) -> None:
        with self.lock:
            self.qpc_frequency_hz = frequency_hz

    def mark_worker_id(self, worker_thread_native_id: int) -> None:
        with self.lock:
            self.worker_thread_native_id = worker_thread_native_id
            self.running = True

    def mark_stopped(self) -> None:
        with self.lock:
            self.running = False

    def begin_stop_signal_attempt(self) -> None:
        with self.lock:
            self.stop_signal_succeeded = True

    def record_stop_signal_failure(self, error_name: str, error_value: int) -> None:
        with self.lock:
            self.stop_signal_succeeded = False
            self.stop_signal_failure_count += 1
            self.last_stop_signal_error_name = error_name
            self.last_stop_signal_error_value = error_value
            self.last_worker_error_name = error_name
            self.last_worker_error_value = error_value
            self._append_evidence_locked(
                {
                    "record_type": "stop_signal_failure",
                    "error_name": error_name,
                    "error_value": error_value,
                    "stop_signal_failure_count": self.stop_signal_failure_count,
                }
            )

    def set_control(self, control: _ControlState) -> None:
        with self.lock:
            previous = self.current_control
            self.control = control
            self.current_control = control
            if previous is not None and (
                previous.epoch != control.epoch
                or previous.generation != control.generation
            ):
                self._drop_pending_locked()

    def worker_control(self, fallback: _ControlState) -> _ControlState:
        with self.lock:
            control = self.current_control or fallback
            previous = self.current_control
            self.control = None
            self.current_control = control
            if previous is not None and (
                previous.epoch != control.epoch
                or previous.generation != control.generation
            ):
                self._drop_pending_locked()
            return control

    def take_control(self) -> _ControlState | None:
        with self.lock:
            control = self.control
            self.control = None
            return control

    def invalidate_pending(self) -> None:
        with self.lock:
            self._drop_pending_locked()

    def _drop_pending_locked(self) -> None:
        if self.pending is not None:
            self._accounting_events.append(("stale", self.pending.sequence))
            self.pending = None
            self.stale_tick_dropped_count += 1

    def publish_tick(
        self,
        control: _ControlState,
        deadline_qpc: int,
        emitted_qpc: int,
        qpc_frequency_hz: int,
    ) -> PreviewTick:
        with self.lock:
            if self.pending is not None:
                self._accounting_events.append(("superseded", self.pending.sequence))
                self.pending_tick_superseded_count += 1
            sequence = self.last_sequence + 1
            self._accounting_events.append(("published", sequence))
            tick = PreviewTick(
                epoch=control.epoch,
                generation=control.generation,
                configured_fps=control.configured_fps,
                sequence=sequence,
                deadline_qpc=deadline_qpc,
                emitted_qpc=emitted_qpc,
            )
            self.pending = tick
            self.last_sequence = sequence
            self.last_wake_qpc = emitted_qpc
            self.worker_tick_published_count += 1
            self._append_evidence_locked(
                {
                    "record_type": "worker_tick",
                    "epoch": tick.epoch,
                    "generation": tick.generation,
                    "configured_fps": tick.configured_fps,
                    "sequence": tick.sequence,
                    "deadline_qpc": tick.deadline_qpc,
                    "emitted_qpc": tick.emitted_qpc,
                    "qpc_frequency_hz": qpc_frequency_hz,
                }
            )
            return tick

    def consume_tick(
        self, epoch: int, generation: int
    ) -> PreviewTick | _StaleTickDrop | None:
        with self.lock:
            tick = self.pending
            self.pending = None
            if tick is None:
                return None
            if tick.epoch != epoch or tick.generation != generation:
                self._accounting_events.append(("stale", tick.sequence))
                self.stale_tick_dropped_count += 1
                self._append_evidence_locked(
                    {
                        "record_type": "stale_drop",
                        "epoch": tick.epoch,
                        "generation": tick.generation,
                        "sequence": tick.sequence,
                    }
                )
                return _StaleTickDrop.STALE
            self._accounting_events.append(("dispatched", tick.sequence))
            self.main_dispatch_count += 1
            self.last_delivered_sequence = tick.sequence
            return tick

    def record_periods_skipped(self, periods_skipped: int) -> None:
        """Accumulate whole pacer periods the worker jumped over this tick."""
        with self.lock:
            self.period_skipped_count += periods_skipped

    def enqueue_wake(self, wake: _RuntimeWake) -> None:
        with self.lock:
            self.wakes.append(wake)
            if wake.qpc:
                self.last_wake_qpc = max(self.last_wake_qpc, wake.qpc)

    def take_wake(self) -> _RuntimeWake | None:
        with self.lock:
            if not self.wakes:
                return None
            return self.wakes.popleft()

    def record_control_wake(
        self,
        control: _ControlState,
        qpc_frequency_hz: int,
        now_qpc: int,
    ) -> None:
        with self.lock:
            self.last_wake_qpc = now_qpc
            self._append_evidence_locked(
                {
                    "record_type": "control_wake",
                    "reason": control.kind.value,
                    "epoch": control.epoch,
                    "generation": control.generation,
                    "configured_fps": control.configured_fps,
                    "qpc_frequency_hz": qpc_frequency_hz,
                    "wake_qpc": now_qpc,
                }
            )

    def record_post_failure(self, error_value: int) -> None:
        with self.lock:
            self.post_failure_count += 1
            self.last_post_error = error_value
            self.running = False
            self._append_evidence_locked(
                {
                    "record_type": "post_failure",
                    "error_value": error_value,
                    "post_failure_count": self.post_failure_count,
                }
            )

    def record_worker_error(self, error_name: str, error_value: int) -> None:
        with self.lock:
            self.last_worker_error_name = error_name
            self.last_worker_error_value = error_value
            self.running = False
            self._append_evidence_locked(
                {
                    "record_type": "worker_error",
                    "error_name": error_name,
                    "error_value": error_value,
                }
            )

    def record_reentrant_wake(self) -> None:
        with self.lock:
            self.reentrant_wake_count += 1
            self._append_evidence_locked(
                {
                    "record_type": "reentrant_wake",
                    "reentrant_wake_count": self.reentrant_wake_count,
                }
            )

    def set_dispatch_in_flight(self, value: bool) -> None:
        with self.lock:
            self.dispatch_in_flight = value

    def set_health_result(self, value: int) -> None:
        with self.lock:
            self.last_health_callback_result = value
            self._append_evidence_locked(
                {
                    "record_type": "health_callback",
                    "result": value,
                }
            )

    def record_health_callback(
        self,
        snapshot: Mapping[str, int | str | bool],
        result: int,
    ) -> None:
        with self.lock:
            self.last_health_callback_result = result
            self._append_evidence_locked(
                {
                    "record_type": "health_callback",
                    "result": result,
                    "running": bool(snapshot.get("running", False)),
                    "worker_thread_native_id": int(
                        snapshot.get("worker_thread_native_id", 0)
                    ),
                    "last_sequence": int(snapshot.get("last_sequence", 0)),
                    "last_delivered_sequence": int(
                        snapshot.get("last_delivered_sequence", 0)
                    ),
                    "qpc_frequency_hz": int(snapshot.get("qpc_frequency_hz", 0)),
                    "post_failure_count": int(snapshot.get("post_failure_count", 0)),
                    "last_post_error": int(snapshot.get("last_post_error", 0)),
                }
            )

    def begin_accounting(self) -> None:
        with self.lock:
            self._accounting_start_index = len(self._accounting_events)
            self._accounting_start_pending = self.pending is not None
            self._accounting_start_period_skipped = self.period_skipped_count

    def end_accounting(self) -> dict[str, int | bool]:
        with self.lock:
            start_index = self._accounting_start_index or 0
            events = self._accounting_events[start_index:]
            counts = {
                "published": sum(kind == "published" for kind, _ in events)
                + int(self._accounting_start_pending),
                "dispatched": sum(kind == "dispatched" for kind, _ in events),
                "superseded": sum(kind == "superseded" for kind, _ in events),
                "stale": sum(kind == "stale" for kind, _ in events),
            }
            result: dict[str, int | bool] = {
                "worker_tick_published_count": counts["published"],
                "main_dispatch_count": counts["dispatched"],
                "pending_tick_superseded_count": counts["superseded"],
                "stale_tick_dropped_count": counts["stale"],
                "pending_tick_present_at_window_end": int(self.pending is not None),
                "period_skipped_count": max(
                    0, self.period_skipped_count - self._accounting_start_period_skipped
                ),
            }
            result["latest_only_accounting_ok"] = self._accounting_balanced_locked(
                result
            )
            self._accounting_start_index = None
            self._accounting_start_pending = False
            self._accounting_start_period_skipped = 0
            self._accounting_events.clear()
            return result

    @staticmethod
    def _accounting_balanced_locked(values: Mapping[str, int | bool]) -> bool:
        published = values["worker_tick_published_count"]
        dispatched = values["main_dispatch_count"]
        superseded = values["pending_tick_superseded_count"]
        stale = values["stale_tick_dropped_count"]
        pending = values["pending_tick_present_at_window_end"]
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (published, dispatched, superseded, stale, pending)
        ):
            return False
        return published == dispatched + superseded + stale + pending

    def _append_evidence_locked(self, row: Mapping[str, object]) -> None:  # noqa: E501  # noqa: OBJECT_OK
        self.evidence.append(dict(row))

    def snapshot(self) -> dict[str, int | str | bool]:
        with self.lock:
            return {
                "qpc_frequency_hz": self.qpc_frequency_hz,
                "worker_thread_native_id": self.worker_thread_native_id,
                "last_sequence": self.last_sequence,
                "last_delivered_sequence": self.last_delivered_sequence,
                "last_wake_qpc": self.last_wake_qpc,
                "post_failure_count": self.post_failure_count,
                "last_post_error": self.last_post_error,
                "last_worker_error_name": self.last_worker_error_name,
                "last_worker_error_value": self.last_worker_error_value,
                "stop_signal_succeeded": self.stop_signal_succeeded,
                "stop_signal_failure_count": self.stop_signal_failure_count,
                "last_stop_signal_error_name": self.last_stop_signal_error_name,
                "last_stop_signal_error_value": self.last_stop_signal_error_value,
                "running": self.running,
                "worker_tick_published_count": self.worker_tick_published_count,
                "period_skipped_count": self.period_skipped_count,
                "pending_tick_superseded_count": self.pending_tick_superseded_count,
                "main_dispatch_count": self.main_dispatch_count,
                "stale_tick_dropped_count": self.stale_tick_dropped_count,
                "pending_tick_present": self.pending is not None,
                "dispatch_in_flight": self.dispatch_in_flight,
                "reentrant_wake_count": self.reentrant_wake_count,
                "last_health_callback_result": self.last_health_callback_result,
                "window_class_destroyed": self.window_class_destroyed,
                "window_class_unregistered": self.window_class_unregistered,
            }

    def drain_evidence(self) -> tuple[Mapping[str, object], ...]:  # noqa: E501  # noqa: OBJECT_OK
        with self.lock:
            rows = tuple(dict(row) for row in self.evidence)
            self.evidence.clear()
            return rows


@dataclass(frozen=True, slots=True)
class _WorkerContext:
    api: _RuntimeApi
    handles: _WorkerHandles
    hwnd: int
    message_id: int
    mailbox: _SharedState
    health: _SharedState
    config: _WorkerConfig


def _worker_signal_from_public(wake: _RuntimeWake) -> PreviewWake:
    if wake.reason == "error":
        return PreviewWake(
            kind="error",
            epoch=wake.epoch,
            generation=wake.generation,
            configured_fps=wake.configured_fps,
            qpc_frequency_hz=wake.qpc_frequency_hz,
            error_name=wake.error_name,
        )
    if wake.reason == "clock_ready":
        return PreviewWake(
            kind="clock_ready",
            epoch=wake.epoch,
            generation=wake.generation,
            configured_fps=wake.configured_fps,
            qpc_frequency_hz=wake.qpc_frequency_hz,
        )
    return PreviewWake(
        kind="tick",
        epoch=wake.epoch,
        generation=wake.generation,
        configured_fps=wake.configured_fps,
        sequence=wake.sequence,
        qpc_frequency_hz=wake.qpc_frequency_hz,
    )


def _worker_post_wake(context: _WorkerContext, wake: _RuntimeWake) -> bool:
    context.mailbox.enqueue_wake(wake)
    if context.api.post_message(context.hwnd, context.message_id, 0, 0):
        return True
    context.mailbox.record_post_failure(context.api.get_last_error())
    return False


def _worker_arm_timer(context: _WorkerContext, deadline_qpc: int, now_qpc: int) -> bool:
    due_100ns = _negative_due_100ns(
        deadline_qpc,
        now_qpc,
        context.mailbox.qpc_frequency_hz,
    )
    if context.api.set_waitable_timer(
        context.handles.timer_handle,
        due_100ns,
        0,
        None,
        None,
        False,
    ):
        return True
    _worker_post_error(context, "SetWaitableTimer")
    return False


def _worker_close_handles(context: _WorkerContext) -> None:
    handles = (
        context.handles.timer_handle,
        context.handles.control_event,
        context.handles.stop_event,
    )
    for handle in handles:
        if handle:
            try:
                context.api.close_handle(handle)
            except (OSError, RuntimeError, ValueError, AttributeError):
                context.mailbox.record_worker_error("CloseHandle", 0)


def _worker_post_error(context: _WorkerContext, error_name: str) -> None:
    error_value = context.api.get_last_error()
    context.mailbox.record_worker_error(error_name, error_value)
    current_control = context.mailbox.current_control
    if current_control is None:
        current_control = _ControlState(
            _ControlKind.START,
            context.config.epoch,
            context.config.generation,
            context.config.configured_fps,
            True,
        )
    _worker_post_wake(
        context,
        _RuntimeWake(
            "error",
            current_control.epoch,
            current_control.generation,
            current_control.configured_fps,
            qpc_frequency_hz=context.mailbox.qpc_frequency_hz,
            error_name=error_name,
            error_value=error_value,
        ),
    )


def _worker_main(context: _WorkerContext) -> None:
    """Run only timer, event, QPC, mailbox, health, and message operations."""
    api = context.api
    state = context.mailbox
    handles = context.handles
    config = context.config
    control = state.worker_control(
        _ControlState(
            _ControlKind.START,
            config.epoch,
            config.generation,
            config.configured_fps,
            True,
        )
    )
    interval_qpc = config.interval_qpc
    deadline_qpc = config.deadline_qpc
    state.mark_worker_id(threading.get_native_id())
    try:
        if not _worker_arm_timer(
            context, deadline_qpc, api.query_performance_counter()
        ):
            return
        if not _worker_post_wake(
            context,
            _RuntimeWake(
                "clock_ready",
                control.epoch,
                control.generation,
                control.configured_fps,
                qpc_frequency_hz=state.qpc_frequency_hz,
                qpc=deadline_qpc - config.interval_qpc,
            ),
        ):
            return
        while True:
            wait_result = api.wait_for_multiple_objects(
                [handles.stop_event, handles.control_event, handles.timer_handle],
                False,
                _INFINITE,
            )
            if wait_result == _WAIT_FAILED:
                _worker_post_error(context, "WaitForMultipleObjects")
                return
            stop_signaled = wait_result == _WAIT_OBJECT_0 or api.event_is_set(
                handles.stop_event
            )
            if stop_signaled:
                return
            control_signaled = wait_result == _WAIT_OBJECT_0 + 1 or api.event_is_set(
                handles.control_event
            )
            if control_signaled:
                next_control = state.take_control()
                if next_control is None:
                    continue
                control = next_control
                rate_hz = (
                    state.diagnostic_rate_hz
                    if state.diagnostic_rate_hz is not None
                    else float(control.configured_fps)
                )
                interval_qpc = _qpc_interval(state.qpc_frequency_hz, rate_hz)
                deadline_qpc = api.query_performance_counter() + interval_qpc
                state.record_control_wake(
                    control,
                    state.qpc_frequency_hz,
                    deadline_qpc - interval_qpc,
                )
                if not _worker_arm_timer(
                    context,
                    deadline_qpc,
                    deadline_qpc - interval_qpc,
                ):
                    return
                if not _worker_post_wake(
                    context,
                    _RuntimeWake(
                        "control",
                        control.epoch,
                        control.generation,
                        control.configured_fps,
                        qpc_frequency_hz=state.qpc_frequency_hz,
                        qpc=deadline_qpc - interval_qpc,
                    ),
                ):
                    return
                continue
            if wait_result != _WAIT_OBJECT_0 + 2:
                continue
            now_qpc = api.query_performance_counter()
            if now_qpc < deadline_qpc:
                if not _worker_arm_timer(context, deadline_qpc, now_qpc):
                    return
                continue
            tick = state.publish_tick(
                control,
                deadline_qpc,
                now_qpc,
                state.qpc_frequency_hz,
            )
            if not _worker_post_wake(
                context,
                _RuntimeWake(
                    "tick",
                    control.epoch,
                    control.generation,
                    control.configured_fps,
                    sequence=tick.sequence,
                    qpc_frequency_hz=state.qpc_frequency_hz,
                    qpc=now_qpc,
                ),
            ):
                return
            deadline_qpc, periods_skipped = _advance_deadline(
                deadline_qpc, now_qpc, interval_qpc
            )
            state.record_periods_skipped(periods_skipped)
            if not _worker_arm_timer(context, deadline_qpc, now_qpc):
                return
    except Exception as exc:  # noqa: E501  # noqa: BROAD_EXCEPT_OK - worker boundary must remain alive.
        _worker_post_error(context, type(exc).__name__)
    finally:
        state.mark_stopped()
        _worker_close_handles(context)


class _WindowsNativeRuntime:
    """Own all Win32 resources and the sole native worker thread."""

    def __init__(
        self,
        *,
        owner_thread_id: int,
        configured_fps: int,
        idle_interval_ms: int,
        diagnostic_rate_hz: float | None = None,
        api: _RuntimeApi | None = None,
    ) -> None:
        self._assert_owner(owner_thread_id)
        self._configured_fps = _validate_configured_fps(configured_fps)
        self._idle_interval_ms = _validate_idle_interval(idle_interval_ms)
        self._diagnostic_rate_hz = _validate_diagnostic_rate(diagnostic_rate_hz)
        self._owner_thread_id = owner_thread_id
        self._api = api if api is not None else _CtypesWin32Api()
        self._state = _SharedState(self._configured_fps, self._diagnostic_rate_hz)
        self._callback: Callable[[], None] | None = None
        self._wake_signal = threading.Event()
        self._thread: threading.Thread | None = None
        self._context: _WorkerContext | None = None
        self._initial_control: _ControlState | None = None
        self._timer_handle = 0
        self._stop_event = 0
        self._control_event = 0
        self._class_name = f"PokeConPreviewClock_{os.getpid()}_{id(self):x}"
        self._class_atom = 0
        self._class_instance = self._api.get_module_handle(None)
        self._hwnd = 0
        self._wndproc_ref: _WndProc | None = None
        self._wndclass_ref: _WNDCLASSEXW | None = None
        self._handles_closed = False
        self._initialized = False
        self._initialize()

    def _assert_owner(self, owner_thread_id: int | None = None) -> None:
        expected = self._owner_thread_id if owner_thread_id is None else owner_thread_id
        if threading.get_native_id() != expected:
            raise _PreviewClockError("preview clock operation crossed the owner thread")

    def _initialize(self) -> None:
        try:
            frequency_hz = self._api.query_performance_frequency()
            if frequency_hz <= 0:
                raise _PreviewClockError("QPC frequency is not positive")
            self._api.query_performance_counter()
            self._state.set_qpc_frequency(frequency_hz)
            self._timer_handle = self._api.create_waitable_timer_ex(
                None,
                None,
                _CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                _TIMER_MODIFY_STATE | _SYNCHRONIZE,
            )
            self._require_handle(self._timer_handle, "CreateWaitableTimerExW")
            self._stop_event = self._api.create_event(None, False, False, None)
            self._require_handle(self._stop_event, "CreateEventW(stop)")
            self._control_event = self._api.create_event(None, False, False, None)
            self._require_handle(self._control_event, "CreateEventW(control)")
            self._wndproc_ref = self._api.native_window_proc()
            self._class_atom = self._api.register_class_ex(
                self._class_name,
                self._class_instance,
                self._wndproc_ref,
            )
            if self._class_atom == 0:
                raise _PreviewClockError("RegisterClassExW failed")
            self._wndclass_ref = getattr(self._api, "last_wndclass", None)
            self._hwnd = self._api.create_window_ex(
                self._class_name,
                self._class_instance,
                _HWND_MESSAGE,
            )
            if self._hwnd == 0:
                raise _PreviewClockError("CreateWindowExW failed")
            self._initialized = True
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._cleanup_initialization()
            raise

    @staticmethod
    def _require_handle(handle: int, operation: str) -> None:
        if not handle:
            raise _PreviewClockError(f"{operation} failed")

    def bind_wake_callback(self, callback: Callable[[], None]) -> None:
        self._assert_owner()
        self._callback = callback

    def consume_wake_signal(self) -> bool:
        self._assert_owner()
        with self._state.lock:
            pending = bool(self._state.wakes)
        if not pending and not self._wake_signal.is_set():
            return False
        self._wake_signal.clear()
        callback = self._callback
        if callback is not None:
            try:
                callback()
            except BaseException:  # noqa: E501  # noqa: BROAD_EXCEPT_OK - native wake boundary.
                self._state.record_worker_error("WNDPROC", 0)
        return True

    def set_initial_control(self, control: _ControlState) -> None:
        self._assert_owner()
        if self._thread is not None:
            raise _PreviewClockError("cannot set initial control after worker start")
        self._initial_control = control
        self._state.set_control(control)

    def start(self) -> None:
        self._assert_owner()
        if not self._initialized:
            raise _PreviewClockError("native runtime is not initialized")
        if self._thread is not None:
            return
        frequency_hz = self._state.snapshot()["qpc_frequency_hz"]
        if not isinstance(frequency_hz, int):
            raise _PreviewClockError("native runtime has no QPC frequency")
        initial_control = self._initial_control or _ControlState(
            _ControlKind.START,
            1,
            1,
            self._configured_fps,
            True,
        )
        if self._state.current_control is None:
            self._state.set_control(initial_control)
        anchor_qpc = self._api.query_performance_counter()
        initial_rate = self._diagnostic_rate_hz or float(initial_control.configured_fps)
        interval_qpc = _qpc_interval(frequency_hz, initial_rate)
        context = _WorkerContext(
            api=self._api,
            handles=_WorkerHandles(
                stop_event=self._stop_event,
                control_event=self._control_event,
                timer_handle=self._timer_handle,
            ),
            hwnd=self._hwnd,
            message_id=_PREVIEW_WAKE_MESSAGE,
            mailbox=self._state,
            health=self._state,
            config=_WorkerConfig(
                epoch=initial_control.epoch,
                generation=initial_control.generation,
                configured_fps=initial_control.configured_fps,
                diagnostic_rate_hz=self._diagnostic_rate_hz,
                idle_interval_ms=self._idle_interval_ms,
                deadline_qpc=anchor_qpc + interval_qpc,
                interval_qpc=interval_qpc,
            ),
        )
        self._context = context
        self._thread = threading.Thread(
            target=_worker_main,
            args=(context,),
            name="PreviewClockQpcWorker",
            daemon=True,
        )
        self._thread.start()

    def request_control(self, control: _ControlState) -> None:
        self._assert_owner()
        if self._thread is None:
            self._state.set_control(control)
            return
        self._state.set_control(control)
        if not self._api.set_event(self._control_event):
            self._state.record_worker_error("SetEvent", self._api.get_last_error())

    def request_stop(self) -> None:
        self._assert_owner()
        self._state.begin_stop_signal_attempt()
        if self._stop_event:
            try:
                if not self._api.set_event(self._stop_event):
                    self._state.record_stop_signal_failure(
                        "SetEvent", self._api.get_last_error()
                    )
            except (OSError, RuntimeError, ValueError, AttributeError):
                self._state.record_stop_signal_failure("SetEvent", 0)
        if self._timer_handle:
            try:
                if not self._api.cancel_waitable_timer(self._timer_handle):
                    self._state.record_stop_signal_failure(
                        "CancelWaitableTimer", self._api.get_last_error()
                    )
            except (OSError, RuntimeError, ValueError, AttributeError):
                self._state.record_stop_signal_failure("CancelWaitableTimer", 0)

    def join(self, timeout_s: float) -> bool:
        self._assert_owner()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout_s)
        return not thread.is_alive()

    def destroy(self) -> None:
        self._assert_owner()
        if self._thread is not None and self._thread.is_alive():
            raise _PreviewClockError("cannot destroy a live native worker")
        if self._hwnd:
            if not self._api.destroy_window(self._hwnd):
                raise _PreviewClockError("DestroyWindow failed")
            self._state.window_class_destroyed = True
            self._hwnd = 0
        if self._class_atom:
            if not self._api.unregister_class(self._class_name, self._class_instance):
                raise _PreviewClockError("UnregisterClass failed")
            self._state.window_class_unregistered = True
            self._class_atom = 0
        if not self._handles_closed:
            if self._thread is None:
                for handle in (
                    self._timer_handle,
                    self._stop_event,
                    self._control_event,
                ):
                    if handle:
                        self._api.close_handle(handle)
            self._handles_closed = True
        self._callback = None
        self._wake_signal.clear()
        self._wndproc_ref = None
        self._wndclass_ref = None
        self._context = None

    def has_live_resources(self) -> bool:
        thread = self._thread
        return bool(
            (thread is not None and thread.is_alive())
            or self._hwnd
            or self._class_atom
            or not self._handles_closed
        )

    def consume_tick(self) -> PreviewTick | _StaleTickDrop | None:
        control = self._state.current_control
        if control is None:
            return self._state.consume_tick(1, 1)
        return self._state.consume_tick(control.epoch, control.generation)

    def consume_wake(self) -> _RuntimeWake | None:
        return self._state.take_wake()

    def record_reentrant_wake(self) -> None:
        self._state.record_reentrant_wake()

    def record_health_callback(
        self,
        snapshot: Mapping[str, int | str | bool],
        result: int,
    ) -> None:
        self._state.record_health_callback(snapshot, result)

    def set_dispatch_gate(self, value: bool) -> None:
        self._state.set_dispatch_in_flight(value)

    def invalidate_pending(self) -> None:
        self._state.invalidate_pending()

    def snapshot(self) -> dict[str, int | str | bool]:
        snapshot = self._state.snapshot()
        thread = self._thread
        snapshot["running"] = bool(thread is not None and thread.is_alive())
        snapshot["worker_thread_native_id"] = int(
            snapshot.get("worker_thread_native_id", 0)
        )
        return snapshot

    def begin_accounting_window(self) -> None:
        self._state.begin_accounting()

    def end_accounting_window(self) -> dict[str, int | bool]:
        return self._state.end_accounting()

    def drain_evidence(self) -> tuple[Mapping[str, object], ...]:  # noqa: E501  # noqa: OBJECT_OK
        return self._state.drain_evidence()

    def _wndproc(
        self,
        hwnd: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        del hwnd, wparam, lparam
        if message == _WM_NCCREATE:
            return 1
        if message != _PREVIEW_WAKE_MESSAGE:
            return 0
        self._wake_signal.set()
        return 0

    def _cleanup_initialization(self) -> None:
        if self._hwnd:
            try:
                self._api.destroy_window(self._hwnd)
            except (OSError, RuntimeError, ValueError, AttributeError):
                self._state.record_worker_error("initialization_cleanup", 0)
            self._hwnd = 0
        if self._class_atom:
            try:
                self._api.unregister_class(self._class_name, self._class_instance)
            except (OSError, RuntimeError, ValueError, AttributeError):
                self._state.record_worker_error("initialization_cleanup", 0)
            self._class_atom = 0
        for handle in (
            self._timer_handle,
            self._stop_event,
            self._control_event,
        ):
            if handle:
                try:
                    self._api.close_handle(handle)
                except (OSError, RuntimeError, ValueError, AttributeError):
                    self._state.record_worker_error("initialization_cleanup", 0)
        self._timer_handle = 0
        self._stop_event = 0
        self._control_event = 0
        self._wndproc_ref = None
        self._wndclass_ref = None
        self._callback = None
        self._wake_signal.clear()
        self._handles_closed = True


def _create_native_runtime(
    *,
    owner_thread_id: int,
    configured_fps: int,
    idle_interval_ms: int,
    diagnostic_rate_hz: float | None = None,
) -> _NativeRuntime:
    if os.name != "nt":
        raise _PreviewClockError("native preview clock requires Windows")
    return _WindowsNativeRuntime(
        owner_thread_id=owner_thread_id,
        configured_fps=configured_fps,
        idle_interval_ms=idle_interval_ms,
        diagnostic_rate_hz=diagnostic_rate_hz,
    )


def _worker_context_for_test(api: _RuntimeApi) -> _WorkerContext:
    state = _SharedState(60, None)
    state.set_qpc_frequency(10_000_000)
    return _WorkerContext(
        api=api,
        handles=_WorkerHandles(stop_event=101, control_event=102, timer_handle=103),
        hwnd=104,
        message_id=_PREVIEW_WAKE_MESSAGE,
        mailbox=state,
        health=state,
        config=_WorkerConfig(
            epoch=1,
            generation=1,
            configured_fps=60,
            diagnostic_rate_hz=None,
            idle_interval_ms=200,
            deadline_qpc=1_000_000,
            interval_qpc=166_667,
        ),
    )


class PreviewClock:
    """Main-thread facade for native scheduling and exclusive fallback."""

    def __init__(
        self,
        root: Any,
        dispatch_tick: Callable[[], DispatchResult],
        configured_fps: int,
        idle_interval_ms: int,
    ) -> None:
        self._root = root
        self._dispatch_tick = dispatch_tick
        self._configured_fps = _validate_configured_fps(configured_fps)
        self._idle_interval_ms = _validate_idle_interval(idle_interval_ms)
        self._owner_thread_id = threading.get_native_id()
        self._state = _ClockState.STOPPED
        self._epoch = 0
        self._generation = 0
        self._runtime: _NativeRuntime | None = None
        self._cleanup_evidence: _CleanupEvidence | None = None
        self._archived_evidence: tuple[Mapping[str, object], ...] = ()  # noqa: E501  # noqa: OBJECT_OK
        self._native_stop_signal_succeeded: bool | None = None
        self._last_schedule: Literal["active", "idle"] = "active"
        self._fallback_deadline_s: float | None = None
        self._fallback_after_id: str | None = None
        self._health_after_id: str | None = None
        self._health_arm_in_progress = False
        self._native_wake_after_id: str | None = None
        self._teardown_after_id: str | None = None
        self._teardown_target: Literal["stopped", "after"] | None = None
        self._native_stop_requested = False
        self._last_post_failure_count = 0
        self._last_valid_wake_monotonic: float | None = None
        self._last_health_callback_result = 0
        self._dispatch_in_flight = False
        self._reentrant_wake_count = 0
        self._blit_skipped_no_new_frame = 0
        self._blit_skipped_at_window_start = 0
        self._accounting_window_started = False

    def _assert_owner(self) -> None:
        if threading.get_native_id() != self._owner_thread_id:
            raise _PreviewClockError("preview clock operation crossed the owner thread")

    def start(self) -> None:
        self._assert_owner()
        match self._state:
            case _ClockState.STOPPED:
                pass
            case _ClockState.TEARDOWN_PENDING:
                raise _PreviewClockError("preview clock teardown is pending")
            case (
                _ClockState.STARTING_NATIVE
                | _ClockState.HIGH_RESOLUTION
                | _ClockState.AFTER
                | _ClockState.STOPPING
                | _ClockState.FAILED
            ):
                return
            case unreachable:
                assert_never(unreachable)
        self._state = _ClockState.STARTING_NATIVE
        self._epoch += 1
        self._generation += 1
        self._native_stop_signal_succeeded = None
        initial_result = self._invoke_dispatch()
        self._last_schedule = initial_result.schedule
        initial_control = _ControlState(
            _ControlKind.START,
            self._epoch,
            self._generation,
            self._configured_fps,
            self._last_schedule == "active",
        )
        runtime: _NativeRuntime | None = None
        # Tcl async wake は意図的に使わない。ctypes の Tcl_AsyncMark トランポリンは
        # ワーカースレッドから Tcl インタプリタへ再入し、プロセスを fatal 終了させた
        # 実測があるため。起床は PostMessageW + after_idle 排出経路に一本化する。
        try:
            runtime = _create_native_runtime(
                owner_thread_id=self._owner_thread_id,
                configured_fps=self._configured_fps,
                idle_interval_ms=self._idle_interval_ms,
            )
            self._runtime = runtime
            set_initial_control = getattr(runtime, "set_initial_control", None)
            if callable(set_initial_control):
                set_initial_control(initial_control)
            runtime.bind_wake_callback(self._on_native_wake)
            runtime.start()
            self._native_stop_requested = False
            self._last_post_failure_count = 0
            self._last_valid_wake_monotonic = time.monotonic()
            self._state = _ClockState.HIGH_RESOLUTION
            self._send_control(_ControlKind.START, self._last_schedule == "active")
            self._arm_health_callback()
            self._schedule_native_wake_poll(_NATIVE_WAKE_IDLE_POLL_MS)
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            self._generation += 1
            cleaned = True
            if runtime is not None:
                cleaned = self._dispose_failed_native_runtime(runtime)
            if not cleaned:
                self._state = _ClockState.TEARDOWN_PENDING
                self._teardown_target = "after"
                self._schedule_teardown_retry()
                return
            self._enter_after_fallback()
            if not isinstance(exc, _PreviewClockError):
                logger.warning(
                    "preview native clock unavailable: {}", type(exc).__name__
                )

    def _dispose_failed_native_runtime(self, runtime: _NativeRuntime) -> bool:
        self._request_native_stop(runtime)
        try:
            joined = runtime.join(1.0)
        except (OSError, RuntimeError, ValueError, AttributeError):
            joined = False
            self._last_health_callback_result = -1
        if not joined:
            return False
        if self._native_worker_live() and not bool(self._native_stop_signal_succeeded):
            return False
        try:
            runtime.destroy()
            self._archive_runtime_evidence(runtime)
            self._archive_cleanup_evidence(runtime)
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1
            return False
        if self._runtime is runtime:
            self._runtime = None
        self._native_stop_signal_succeeded = None
        return True

    def stop(self) -> StopResult:
        self._assert_owner()
        match self._state:
            case _ClockState.STOPPED:
                self._state = _ClockState.STOPPED
                return StopResult.STOPPED
            case _ClockState.FAILED:
                if self._native_resources_live():
                    self._state = _ClockState.TEARDOWN_PENDING
                    self._teardown_target = "stopped"
                    return self._finish_native_teardown(
                        StopResult.PENDING, retry_stop_signal=True
                    )
                self._state = _ClockState.STOPPED
                return StopResult.STOPPED
            case _ClockState.AFTER:
                self._cancel_fallback()
                self._generation += 1
                self._state = _ClockState.STOPPED
                return StopResult.STOPPED
            case _ClockState.HIGH_RESOLUTION | _ClockState.STARTING_NATIVE:
                self._state = _ClockState.STOPPING
                self._close_dispatch_gate()
                self._invalidate_generation()
                self._cancel_health_callback()
                self._cancel_native_wake_poll()
                self._native_stop_requested = False
                self._native_stop_signal_succeeded = None
                self._teardown_target = "stopped"
                if self._runtime is not None:
                    self._request_native_stop(self._runtime)
                    self._native_stop_requested = True
                return self._finish_native_teardown(StopResult.STOPPED)
            case _ClockState.STOPPING | _ClockState.TEARDOWN_PENDING:
                if self._teardown_target == "after":
                    self._teardown_target = "stopped"
                    if self._teardown_after_id is not None:
                        return StopResult.PENDING
                    return self._finish_native_teardown(
                        StopResult.PENDING, retry_stop_signal=True
                    )
                return self._continue_native_teardown()
            case unreachable:
                assert_never(unreachable)

    def set_fps(self, fps: int) -> None:
        self._assert_owner()
        value = _validate_configured_fps(fps)
        if value == self._configured_fps:
            return
        self._configured_fps = value
        self._generation += 1
        match self._state:
            case _ClockState.HIGH_RESOLUTION:
                self._invalidate_generation(clear_only=True)
                self._send_control(
                    _ControlKind.SET_FPS,
                    self._last_schedule == "active",
                )
            case _ClockState.AFTER:
                self._fallback_deadline_s = None
                self._reschedule_fallback()
            case (
                _ClockState.STOPPED
                | _ClockState.STARTING_NATIVE
                | _ClockState.STOPPING
                | _ClockState.TEARDOWN_PENDING
                | _ClockState.FAILED
            ):
                pass
            case unreachable:
                assert_never(unreachable)

    def mode(self) -> Literal["stopped", "high_resolution", "after"]:
        match self._state:
            case _ClockState.HIGH_RESOLUTION | _ClockState.STARTING_NATIVE:
                return "high_resolution"
            case _ClockState.AFTER:
                return "after"
            case (
                _ClockState.STOPPED
                | _ClockState.STOPPING
                | _ClockState.TEARDOWN_PENDING
                | _ClockState.FAILED
            ):
                return "stopped"
            case unreachable:
                assert_never(unreachable)

    def health_snapshot(self) -> Mapping[str, int | str | bool]:
        runtime_snapshot: Mapping[str, int | str | bool] = {}
        if self._runtime is not None:
            runtime_snapshot = self._runtime.snapshot()
        state_value = self._state.value
        mode_value = self.mode()
        cleanup = self._cleanup_evidence
        defaults: dict[str, int | str | bool] = {
            "state": state_value,
            "mode": mode_value,
            "epoch": self._epoch,
            "generation": self._generation,
            "configured_fps": self._configured_fps,
            "qpc_frequency_hz": 0,
            "worker_thread_native_id": 0,
            "last_sequence": 0,
            "last_delivered_sequence": 0,
            "last_wake_qpc": 0,
            "worker_tick_published_count": 0,
            "period_skipped_count": 0,
            "blit_skipped_no_new_frame": 0,
            "pending_tick_superseded_count": 0,
            "main_dispatch_count": 0,
            "stale_tick_dropped_count": 0,
            "post_failure_count": 0,
            "last_post_error": 0,
            "running": False,
            "pending_tick_present": False,
            "dispatch_in_flight": self._dispatch_in_flight,
            "reentrant_wake_count": self._reentrant_wake_count,
            "last_health_callback_result": self._last_health_callback_result,
            "window_class_destroyed": False,
            "window_class_unregistered": False,
            "cleanup_window_class_destroyed": (
                cleanup.window_class_destroyed if cleanup is not None else False
            ),
            "cleanup_window_class_unregistered": (
                cleanup.window_class_unregistered if cleanup is not None else False
            ),
            "cleanup_worker_running": (
                cleanup.worker_running if cleanup is not None else False
            ),
            "cleanup_qpc_frequency_hz": (
                cleanup.qpc_frequency_hz if cleanup is not None else 0
            ),
            "cleanup_worker_thread_native_id": (
                cleanup.worker_thread_native_id if cleanup is not None else 0
            ),
            "cleanup_last_sequence": (
                cleanup.last_sequence if cleanup is not None else 0
            ),
            "cleanup_worker_tick_published_count": (
                cleanup.worker_tick_published_count if cleanup is not None else 0
            ),
            "cleanup_main_dispatch_count": (
                cleanup.main_dispatch_count if cleanup is not None else 0
            ),
            "pending_health_after": self._health_after_id or "",
            "pending_fallback_after": self._fallback_after_id or "",
            "pending_teardown_after": self._teardown_after_id or "",
        }
        defaults.update(runtime_snapshot)
        defaults["state"] = state_value
        defaults["mode"] = mode_value
        defaults["epoch"] = self._epoch
        defaults["generation"] = self._generation
        defaults["configured_fps"] = self._configured_fps
        defaults["dispatch_in_flight"] = self._dispatch_in_flight
        defaults["blit_skipped_no_new_frame"] = self._blit_skipped_no_new_frame
        defaults["reentrant_wake_count"] = self._reentrant_wake_count
        defaults["last_health_callback_result"] = self._last_health_callback_result
        if self._runtime is None and cleanup is not None:
            defaults["window_class_destroyed"] = cleanup.window_class_destroyed
            defaults["window_class_unregistered"] = cleanup.window_class_unregistered
        return dict(defaults)

    def _send_control(self, kind: _ControlKind, active: bool) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.request_control(
            _ControlState(
                kind=kind,
                epoch=self._epoch,
                generation=self._generation,
                configured_fps=self._configured_fps,
                active=active,
            )
        )

    def _invoke_dispatch(self) -> DispatchResult:
        try:
            result = self._dispatch_tick()
        except Exception:  # noqa: E501  # noqa: BROAD_EXCEPT_OK - Tk callback boundary.
            logger.exception("preview dispatch failed")
            self._last_health_callback_result = -1
            return DispatchResult(schedule=self._last_schedule)
        if result.schedule != self._last_schedule:
            self._last_schedule = result.schedule
            match self._state:
                case _ClockState.HIGH_RESOLUTION:
                    self._generation += 1
                    self._send_control(
                        _ControlKind.ACTIVE
                        if result.schedule == "active"
                        else _ControlKind.IDLE,
                        result.schedule == "active",
                    )
                case _ClockState.AFTER:
                    self._generation += 1
                    self._fallback_deadline_s = None
                case (
                    _ClockState.STOPPED
                    | _ClockState.STARTING_NATIVE
                    | _ClockState.STOPPING
                    | _ClockState.TEARDOWN_PENDING
                    | _ClockState.FAILED
                ):
                    pass
                case unreachable:
                    assert_never(unreachable)
        return result

    def _drain_native_wakes(self) -> bool:
        self._assert_owner()
        runtime = self._runtime
        if self._state is not _ClockState.HIGH_RESOLUTION or runtime is None:
            return False
        consume_wake_signal = getattr(runtime, "consume_wake_signal", None)
        if not callable(consume_wake_signal):
            self._on_native_wake()
            return True
        drained = False
        while self._state is _ClockState.HIGH_RESOLUTION and self._runtime is runtime:
            if not consume_wake_signal():
                break
            drained = True
        return drained

    def _on_native_wake(self) -> None:
        self._assert_owner()
        if self._state is not _ClockState.HIGH_RESOLUTION or self._runtime is None:
            return
        wake: _RuntimeWake | None
        consume_wake = getattr(self._runtime, "consume_wake", None)
        if callable(consume_wake):
            wake = consume_wake()
        else:
            wake = None
        if wake is None:
            snapshot = self._runtime.snapshot()
            wake = _RuntimeWake(
                "tick",
                self._epoch,
                self._generation,
                self._configured_fps,
                qpc_frequency_hz=int(snapshot.get("qpc_frequency_hz", 0)),
            )
        if wake.epoch != self._epoch or wake.generation != self._generation:
            self._last_health_callback_result = -1
            return
        match wake.reason:
            case "clock_ready":
                self._last_valid_wake_monotonic = time.monotonic()
                self._arm_health_callback()
            case "control":
                self._last_valid_wake_monotonic = time.monotonic()
                self._arm_health_callback()
            case "tick":
                self._last_valid_wake_monotonic = time.monotonic()
                self._dispatch_one_tick()
                self._arm_health_callback()
            case "error":
                self._last_health_callback_result = 1
                self._begin_native_recovery("after")
            case unreachable:
                assert_never(unreachable)

    def _dispatch_one_tick(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        if self._dispatch_in_flight:
            self._reentrant_wake_count += 1
            record_reentrant = getattr(runtime, "record_reentrant_wake", None)
            if callable(record_reentrant):
                record_reentrant()
            return
        tick = runtime.consume_tick()
        if isinstance(tick, _StaleTickDrop):
            return
        if tick is None:
            self._blit_skipped_no_new_frame += 1
            return
        self._dispatch_in_flight = True
        set_dispatch_gate = getattr(runtime, "set_dispatch_gate", None)
        if callable(set_dispatch_gate):
            set_dispatch_gate(True)
        before_reentrant = self._reentrant_wake_count
        try:
            self._invoke_dispatch()
        finally:
            self._dispatch_in_flight = False
            if callable(set_dispatch_gate):
                set_dispatch_gate(False)
        if self._reentrant_wake_count > before_reentrant:
            self._dispatch_one_tick()

    def _schedule_native_wake_poll(self, delay_ms: int) -> None:
        runtime = self._runtime
        if self._state is not _ClockState.HIGH_RESOLUTION or runtime is None:
            return
        consume_wake_signal = getattr(runtime, "consume_wake_signal", None)
        if not callable(consume_wake_signal):
            return
        if self._native_wake_after_id is not None:
            return
        try:
            after_idle = getattr(self._root, "after_idle", None)
            if callable(after_idle):
                self._native_wake_after_id = str(after_idle(self._poll_native_wake))
            else:
                self._native_wake_after_id = str(
                    self._root.after(delay_ms, self._poll_native_wake)
                )
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1
            self._begin_native_recovery("after")

    def _poll_native_wake(self) -> None:
        self._native_wake_after_id = None
        runtime = self._runtime
        if self._state is not _ClockState.HIGH_RESOLUTION or runtime is None:
            return
        consume_wake_signal = getattr(runtime, "consume_wake_signal", None)
        if not callable(consume_wake_signal):
            return
        try:
            drained = self._drain_native_wakes()
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1
            self._begin_native_recovery("after")
            return
        self._schedule_native_wake_poll(
            _NATIVE_WAKE_POLL_MS if drained else _NATIVE_WAKE_IDLE_POLL_MS
        )

    def _cancel_native_wake_poll(self) -> None:
        after_id = self._native_wake_after_id
        self._native_wake_after_id = None
        if after_id is None:
            return
        try:
            self._root.after_cancel(after_id)
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1

    def _arm_health_callback(self) -> None:
        if self._state is not _ClockState.HIGH_RESOLUTION:
            return
        if self._health_after_id is not None or self._health_arm_in_progress:
            return
        self._health_arm_in_progress = True
        try:
            timeout_ms = max(250, math.ceil(4000.0 / self._configured_fps))
            self._health_after_id = str(
                self._root.after(timeout_ms, self._health_callback)
            )
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._begin_native_recovery("after")
        finally:
            self._health_arm_in_progress = False

    def _cancel_health_callback(self) -> None:
        after_id = self._health_after_id
        self._health_after_id = None
        if after_id is None:
            return
        try:
            self._root.after_cancel(after_id)
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1

    def _health_callback(self) -> None:
        self._assert_owner()
        self._health_after_id = None
        if self._state is not _ClockState.HIGH_RESOLUTION or self._runtime is None:
            return
        snapshot = self._runtime.snapshot()
        post_failure_increased = (
            int(snapshot.get("post_failure_count", 0)) > self._last_post_failure_count
        )
        worker_running = bool(snapshot.get("running", False))
        sequence_gap = (
            int(snapshot.get("last_sequence", 0))
            - int(snapshot.get("last_delivered_sequence", 0))
            > 1
        )
        last_wake_stale = self._last_valid_wake_monotonic is None or (
            time.monotonic() - self._last_valid_wake_monotonic
        ) * 1000.0 > max(250, math.ceil(4000.0 / self._configured_fps))
        self._last_post_failure_count = int(snapshot.get("post_failure_count", 0))
        health_failed = (
            post_failure_increased
            or not worker_running
            or sequence_gap
            or last_wake_stale
        )
        health_result = 1 if health_failed else 0
        self._last_health_callback_result = health_result
        record_health_callback = getattr(self._runtime, "record_health_callback", None)
        if callable(record_health_callback):
            record_health_callback(snapshot, health_result)
        if health_failed:
            self._begin_native_recovery("after")
        else:
            self._arm_health_callback()

    def _begin_native_recovery(self, target: Literal["stopped", "after"]) -> None:
        if self._state is not _ClockState.HIGH_RESOLUTION:
            return
        self._state = _ClockState.STOPPING
        self._teardown_target = target
        self._close_dispatch_gate()
        self._invalidate_generation()
        self._cancel_health_callback()
        self._cancel_native_wake_poll()
        if not self._native_stop_requested:
            self._native_stop_requested = True
            runtime = self._runtime
            if runtime is not None:
                self._request_native_stop(runtime)
        self._finish_native_teardown(
            StopResult.PENDING if target == "after" else StopResult.STOPPED
        )

    def _invalidate_generation(self, *, clear_only: bool = False) -> None:
        if not clear_only:
            self._generation += 1
        runtime = self._runtime
        if runtime is None:
            return
        invalidate = getattr(runtime, "invalidate_pending", None)
        if callable(invalidate):
            invalidate()

    def _close_dispatch_gate(self) -> None:
        self._dispatch_in_flight = False
        runtime = self._runtime
        if runtime is not None:
            set_dispatch = getattr(runtime, "set_dispatch_gate", None)
            if callable(set_dispatch):
                set_dispatch(False)

    def _request_native_stop(self, runtime: _NativeRuntime) -> bool:
        try:
            runtime.request_stop()
            snapshot = runtime.snapshot()
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._native_stop_signal_succeeded = False
            self._last_health_callback_result = -1
            return False
        self._native_stop_signal_succeeded = bool(
            snapshot.get("stop_signal_succeeded", True)
        )
        if not self._native_stop_signal_succeeded:
            self._last_health_callback_result = -1
        return self._native_stop_signal_succeeded

    def _native_worker_live(self) -> bool:
        runtime = self._runtime
        if runtime is None:
            return False
        try:
            return bool(runtime.snapshot().get("running", False))
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1
            return True

    def _archive_runtime_evidence(self, runtime: _NativeRuntime) -> None:
        rows = runtime.drain_evidence()
        self._archived_evidence = (*self._archived_evidence, *rows)

    def _archive_cleanup_evidence(self, runtime: _NativeRuntime) -> None:
        snapshot = runtime.snapshot()
        self._cleanup_evidence = _CleanupEvidence(
            window_class_destroyed=bool(snapshot.get("window_class_destroyed", False)),
            window_class_unregistered=bool(
                snapshot.get("window_class_unregistered", False)
            ),
            worker_running=bool(snapshot.get("running", False)),
            qpc_frequency_hz=int(snapshot.get("qpc_frequency_hz", 0)),
            worker_thread_native_id=int(snapshot.get("worker_thread_native_id", 0)),
            last_sequence=int(snapshot.get("last_sequence", 0)),
            worker_tick_published_count=int(
                snapshot.get("worker_tick_published_count", 0)
            ),
            main_dispatch_count=int(snapshot.get("main_dispatch_count", 0)),
        )

    def _native_resources_live(self) -> bool:
        runtime = self._runtime
        if runtime is None:
            return False
        checker = getattr(runtime, "has_live_resources", None)
        if callable(checker):
            try:
                return bool(checker())
            except (OSError, RuntimeError, ValueError, AttributeError):
                self._last_health_callback_result = -1
                return True
        try:
            snapshot = runtime.snapshot()
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1
            return True
        return bool(
            snapshot.get("running", False)
            or not bool(snapshot.get("window_class_destroyed", False))
            or not bool(snapshot.get("window_class_unregistered", False))
        )

    def _finish_native_teardown(
        self,
        final_result: StopResult,
        *,
        retry_stop_signal: bool = False,
    ) -> StopResult:
        runtime = self._runtime
        if runtime is None:
            if self._teardown_target == "after":
                self._enter_after_fallback()
            else:
                self._state = _ClockState.STOPPED
            self._native_stop_signal_succeeded = None
            return final_result
        if retry_stop_signal and self._native_worker_live():
            if not self._request_native_stop(runtime):
                self._state = _ClockState.TEARDOWN_PENDING
                self._schedule_teardown_retry()
                return StopResult.PENDING
        try:
            joined = runtime.join(1.0)
        except (OSError, RuntimeError, ValueError, AttributeError):
            joined = False
            self._last_health_callback_result = -1
        if not joined:
            self._state = _ClockState.TEARDOWN_PENDING
            self._schedule_teardown_retry()
            return StopResult.PENDING
        try:
            runtime.destroy()
            self._archive_runtime_evidence(runtime)
            self._archive_cleanup_evidence(runtime)
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._state = _ClockState.TEARDOWN_PENDING
            self._schedule_teardown_retry()
            return StopResult.PENDING
        self._runtime = None
        self._native_stop_signal_succeeded = None
        if self._teardown_target == "after":
            self._enter_after_fallback()
            return final_result
        self._state = _ClockState.STOPPED
        self._teardown_target = None
        return final_result

    def _continue_native_teardown(self) -> StopResult:
        return self._finish_native_teardown(StopResult.STOPPED, retry_stop_signal=True)

    def _schedule_teardown_retry(self) -> None:
        if self._teardown_after_id is not None:
            return
        try:
            self._teardown_after_id = str(self._root.after(50, self._retry_teardown))
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1
            self._state = (
                _ClockState.TEARDOWN_PENDING
                if self._native_resources_live()
                else _ClockState.FAILED
            )

    def _retry_teardown(self) -> None:
        self._assert_owner()
        self._teardown_after_id = None
        self._continue_native_teardown()

    def _enter_after_fallback(self) -> None:
        self._cancel_health_callback()
        self._cancel_native_wake_poll()
        self._teardown_after_id = None
        self._runtime = None
        self._native_stop_signal_succeeded = None
        self._state = _ClockState.AFTER
        self._fallback_deadline_s = None
        self._reschedule_fallback()

    def _reschedule_fallback(self) -> None:
        if self._state is not _ClockState.AFTER:
            return
        if self._fallback_after_id is not None:
            try:
                self._root.after_cancel(self._fallback_after_id)
            except (OSError, RuntimeError, ValueError, AttributeError):
                self._last_health_callback_result = -1
            self._fallback_after_id = None
        now_s = time.perf_counter()
        delay_ms = self._after_delay_ms(now_s)
        try:
            self._fallback_after_id = str(
                self._root.after(delay_ms, self._fallback_tick)
            )
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._state = _ClockState.FAILED

    def _fallback_tick(self) -> None:
        self._assert_owner()
        self._fallback_after_id = None
        if self._state is not _ClockState.AFTER:
            return
        self._invoke_dispatch()
        self._reschedule_fallback()

    def _cancel_fallback(self) -> None:
        after_id = self._fallback_after_id
        self._fallback_after_id = None
        self._fallback_deadline_s = None
        if after_id is None:
            return
        try:
            self._root.after_cancel(after_id)
        except (OSError, RuntimeError, ValueError, AttributeError):
            self._last_health_callback_result = -1

    def _after_delay_ms(self, now_s: float) -> int:
        if self._fallback_deadline_s is None:
            interval_s = (
                1.0 / self._configured_fps
                if self._last_schedule == "active"
                else self._idle_interval_ms / 1000.0
            )
            self._fallback_deadline_s = now_s + interval_s
        if self._fallback_deadline_s <= now_s:
            interval_s = (
                1.0 / self._configured_fps
                if self._last_schedule == "active"
                else self._idle_interval_ms / 1000.0
            )
            skipped = math.floor((now_s - self._fallback_deadline_s) / interval_s) + 1
            self._fallback_deadline_s += skipped * interval_s
        return max(
            1,
            math.ceil((self._fallback_deadline_s - now_s) * 1000.0),
        )

    def _begin_evidence_window(self) -> None:
        self._assert_owner()
        self._accounting_window_started = True
        self._blit_skipped_at_window_start = self._blit_skipped_no_new_frame
        if self._runtime is not None:
            self._runtime.begin_accounting_window()

    def _end_evidence_window(self) -> Mapping[str, int | str | bool]:
        self._assert_owner()
        if self._runtime is None:
            return {
                "worker_tick_published_count": 0,
                "pending_tick_superseded_count": 0,
                "main_dispatch_count": 0,
                "stale_tick_dropped_count": 0,
                "pending_tick_present_at_window_end": 0,
                "latest_only_accounting_ok": False,
                "period_skipped_count": 0,
                "blit_skipped_no_new_frame": 0,
            }
        result = dict(self._runtime.end_accounting_window())
        result.setdefault("latest_only_accounting_ok", False)
        result.setdefault("period_skipped_count", 0)
        result["blit_skipped_no_new_frame"] = max(
            0, self._blit_skipped_no_new_frame - self._blit_skipped_at_window_start
        )
        self._accounting_window_started = False
        self._blit_skipped_at_window_start = 0
        return result

    def _drain_evidence(self) -> tuple[Mapping[str, object], ...]:  # noqa: E501  # noqa: OBJECT_OK
        self._assert_owner()
        active_rows = (
            self._runtime.drain_evidence() if self._runtime is not None else ()
        )
        rows = (*self._archived_evidence, *active_rows)
        self._archived_evidence = ()
        return rows
