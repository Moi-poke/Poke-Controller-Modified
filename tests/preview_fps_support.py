# noqa: E501  # noqa: SIZE_OK - approved one-file E2E harness requirement.
"""Opt-in real-Tk preview FPS measurement, evidence, and child-process harness."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, Literal, NamedTuple, NoReturn, TypedDict, assert_never

import numpy as np

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SERIAL_CONTROLLER = REPO_ROOT / "SerialController"
if str(SERIAL_CONTROLLER) not in sys.path:
    sys.path.insert(0, str(SERIAL_CONTROLLER))

NS_PER_S: Final[int] = 1_000_000_000
NS_PER_MS: Final[int] = 1_000_000
SOURCE_HZ: Final[int] = 180
SOURCE_WIDTH: Final[int] = 1280
SOURCE_HEIGHT: Final[int] = 720
PRODUCTION_FPS_VALUES: Final[tuple[int, ...]] = (5, 15, 30, 45, 60)
DIAGNOSTIC_RATES_HZ: Final[tuple[float, ...]] = (60.01, 60.03, 60.06)
GAP_THRESHOLD_NS: Final[int] = 25_000_000
REPORT_SCHEMA_VERSION: Final[int] = 3
CAP_TOLERANCE_SEMANTICS: Final[str] = "finite_window_boundary_not_rate_relaxation"
MEAN_HZ_SEMANTICS: Final[str] = (
    "endpoint_derived: observed first-to-last in-window span divided by the "
    "adjacent in-window interval count, not the nominal measurement window"
)
SOURCE_PIN_PATHS: Final[tuple[str, ...]] = (
    "SerialController/ui/preview_clock.py",
    "SerialController/GuiAssets.py",
    "SerialController/Window.py",
    "SerialController/core/Camera.py",
    "SerialController/ui/camera_panel.py",
    "SerialController/config.py",
    "SerialController/WindowUtils.py",
    "tests/test_preview_clock.py",
    "tests/preview_fps_support.py",
    "tests/test_preview_fps_e2e.py",
    "tests/test_preview_fps_measurement_contract.py",
    "tests/test_preview_fps_internal_rate_contract.py",
)
REQUIRED_EVIDENCE_FILES: Final[tuple[str, ...]] = (
    "report.json",
    "wakes.jsonl",
    "clock_pairs.jsonl",
    "present_intervals.jsonl",
    "dispatch_intervals.jsonl",
    "camera_reads.jsonl",
    "health.jsonl",
    "teardown.json",
    "thread_ids.json",
    "source_pins.json",
)
REQUIRED_REPORT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "configured_fps",
        "diagnostic_rate_hz",
        "run_class",
        "diagnostic_only",
        "clock_mode",
        "target_mean_min_hz",
        "target_mean_max_hz",
        "target_p1_min_hz",
        "cap_tolerance_hz",
        "cap_tolerance_semantics",
        "measurement_start_ns",
        "nominal_window_end_ns",
        "actual_window_end_ns",
        "measurement_overshoot_ns",
        "measurement_overshoot_limit_ns",
        "measurement_overshoot_ok",
        "dispatch_summary",
        "present_summary",
        "production_accepted",
        "functional_accepted",
        "test_passed",
        "accepted",
        "performance_reference",
        "period_skipped_count",
        "blit_skipped_no_new_frame",
        "suppressed_blit_interval_count",
        "worker_tick_published_count",
        "pending_tick_superseded_count",
        "main_dispatch_count",
        "stale_tick_dropped_count",
        "pending_tick_present_at_window_end",
        "latest_only_accounting_ok",
        "wake_reason_counts",
        "gap_threshold_ns",
        "dispatch_gaps",
        "present_gaps",
        "worker_tick_gaps",
        "control_wake_gaps",
        "camera_read_count",
        "camera_frame_present_count",
        "camera_none_count",
        "camera_read_error_count",
        "camera_unique_sequence_count",
        "camera_duplicate_sequence_count",
        "camera_sequence_regression_count",
        "camera_last_sequence",
        "post_teardown_camera_read_count",
        "capture_device",
        "thread_ids",
        "source_pins_unchanged",
        "source_pins_complete",
        "artifact_manifest_path",
        "physical_camera_evidence",
        "physical_unique_frame_claim",
        "compositor_or_scanout_proof",
    }
)
ARTIFACT_MANIFEST_FILE: Final[str] = "artifact_hashes.json"
# 実機デバイスの読み戻し表（report.json の capture_device 1個に収まる）。
# 物理ボードが無い実行では全計測値を None にする。0 を書くと「0x0・0fps・
# 読めない FOURCC の壊れたボード」と同じ形になり、実測していないのに実測を
# 主張できてしまう。None はドライバが出せない値。主張の有無は claim が担う
# （camera_thread_claim と同じ書き方）。
SYNTHETIC_CAPTURE_DEVICE: Final[dict[str, Any]] = {
    "claim": "not_applicable_synthetic_source",
    "requested_width": None,
    "requested_height": None,
    "requested_fps": None,
    "actual_width": None,
    "actual_height": None,
    "actual_fps": None,
    "fourcc": None,
    "fourcc_readable": False,
    "size_not_applied": False,
    "fourcc_not_mjpg": False,
    "fps_not_applied": False,
}
CLAIM_TOKEN_ENV: Final[str] = "POKECON_FPS_CLAIM_TOKEN"
PARENT_HANDOFF_FILE: Final[str] = "parent-handoff.json"
CHILD_OWNER_FILE: Final[str] = "child-owner.json"
CHILD_ENVIRONMENT_FILE: Final[str] = "child-environment.json"
PARENT_OWNERSHIP_FILES: Final[frozenset[str]] = frozenset(
    {"environment.json", PARENT_HANDOFF_FILE}
)

type EvidenceRole = Literal["parent", "child"]


class ExecutionContract(TypedDict):
    phase: str
    run_class: str
    configured_fps: int
    diagnostic_rate_hz: float | None
    source_hz: int
    warmup_s: float
    measurement_s: float
    evidence_dir: str
    test_id: str
    watchdog_timeout_s: float


class ParentHandoffPayload(TypedDict):
    schema_version: int
    claim_token: str
    execution_contract: ExecutionContract
    source_pins: list[dict[str, str | int | bool]]


class ChildOwnershipPayload(TypedDict):
    schema_version: int
    claim_token: str
    pid: int
    execution_contract: ExecutionContract
    source_pins: list[dict[str, str | int | bool]]


@dataclass(frozen=True, slots=True)
class FilePin:
    path: str
    exists: bool
    bytes: int
    sha256: str


@dataclass(slots=True)  # noqa: E501  # noqa: MUTABLE_OK - brief-required in-place completion.
class DispatchRecord:
    """Mutable while its dispatch is in flight, then frozen by teardown timing."""

    dispatch_id: int
    dispatch_enter_ns: int
    dispatch_exit_ns: int | None
    sequence: int
    epoch: int
    generation: int
    configured_fps: int
    schedule: str
    present_observed: bool


@dataclass(frozen=True, slots=True)
class CameraReadRecord:
    perf_counter_ns: int
    thread_native_id: int
    sequence: int
    frame_present: bool
    duplicate: bool
    regression: bool
    error: str | None
    post_teardown: bool


@dataclass(frozen=True, slots=True)
class PresentRecord:
    present_enter_ns: int
    present_exit_ns: int
    present_duration_ns: int
    # compose と present を別々に包むのは、合成時間と blit 時間を
    # 独立して測れるようにするため（設計 5節）。
    compose_duration_ns: int
    sequence: int | None
    dispatch_id: int | None


@dataclass(frozen=True, slots=True)
class DrawRecord:
    draw_enter_ns: int
    draw_exit_ns: int
    sequence: int | None
    frame_present: bool
    duplicate_sequence: bool


@dataclass(frozen=True, slots=True)
class CallbackSchedule:
    callback_name: str
    requested_delay_ms: int
    schedule_enter_ns: int
    scheduled_due_ns: int
    after_id: str


@dataclass(frozen=True, slots=True)
class CadenceContract:
    configured_fps: int
    target_mean_min_hz: float
    target_mean_max_hz: float
    target_p1_min_hz: float
    cap_tolerance_hz: float
    cap_tolerance_semantics: str


@dataclass(frozen=True, slots=True)
class CadenceResult:
    count: int
    mean_hz: float
    p1_hz: float
    # Observed alongside ``count`` so the N-events-give-N-1-intervals relation is
    # falsifiable by an independent quantity rather than by inverting mean_hz.
    interval_count: int = 0
    observed_span_ns: int = 0
    mean_hz_semantics: str = MEAN_HZ_SEMANTICS


@dataclass(frozen=True, slots=True)
class PerformanceReference:
    """Whether the measured cadence met the configured production reference.

    Recorded, never gated: a miss is a measurement result, not a test failure.
    """

    within_reference: bool


@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    production_accepted: bool
    functional_accepted: bool
    test_passed: bool
    accepted: bool
    performance_reference: PerformanceReference = PerformanceReference(True)
    period_skipped_count: int = 0
    blit_skipped_no_new_frame: int = 0


@dataclass(frozen=True, slots=True)
class Window:
    start_ns: int
    end_ns: int


class ConfigError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


class HarnessStateError(RuntimeError):
    """The opt-in harness cannot safely continue from its current state."""


def make_fake_exit_app(
    monkeypatch: Any,
    *,
    stop_results: Sequence[Any],
) -> tuple[Any, list[str], Any]:
    """Build a real Window exit caller with deterministic external fakes."""
    from Window import PokeControllerApp
    from ui import preview_clock

    events: list[str] = []

    class ManualRoot:
        def __init__(self) -> None:
            self.callbacks: list[tuple[str, int, Any]] = []
            self.scheduled_delays: list[int] = []
            self.destroy_calls = 0

        def after(self, delay_ms: int, callback: Any) -> str:
            after_id = f"after-{len(self.callbacks) + 1}"
            self.callbacks.append((after_id, delay_ms, callback))
            self.scheduled_delays.append(delay_ms)
            return after_id

        def after_cancel(self, after_id: str) -> None:
            self.callbacks = [row for row in self.callbacks if row[0] != after_id]

        def run_next(self) -> None:
            assert self.callbacks
            _after_id, _delay_ms, callback = self.callbacks.pop(0)
            callback()

        def destroy(self) -> None:
            self.destroy_calls += 1
            events.append("root_destroy")

    class FakeSurface:
        def release(self) -> None:
            events.append("surface_released")

    class FakePreview:
        def __init__(self) -> None:
            self.results = deque(stop_results)
            self.surface = FakeSurface()

        def UnbindLeftClick(self) -> None:
            events.append("unbind_left")

        def UnbindRightClick(self) -> None:
            events.append("unbind_right")

        def stopCapture(self) -> preview_clock.StopResult:
            result = self.results.popleft()
            match result:
                case preview_clock.StopResult.PENDING:
                    events.append("preview_stop_pending")
                case preview_clock.StopResult.STOPPED:
                    events.append("preview_stopped")
                case unreachable:
                    assert_never(unreachable)
            return result

    def record(phase: str) -> None:
        events.append(phase)

    def serial_shutdown() -> bool:
        record("serial_shutdown")
        return False

    root = ManualRoot()
    runner = SimpleNamespace(
        notify_closing=lambda: None,
        cancel_watch=lambda: None,
        shutdown=lambda _sender: record("runner_shutdown"),
        stats_dirty=True,
    )
    serial = SimpleNamespace(
        sender=object(),
        stop_keyboard=lambda: record("keyboard_stop"),
        shutdown=serial_shutdown,
    )
    app = object.__new__(PokeControllerApp)
    app.root = root
    app.preview = FakePreview()
    app.runner = runner
    app.serial = serial
    app.menu = SimpleNamespace(closeAll=lambda: record("child_windows_closed"))
    app.camera = SimpleNamespace(destroy=lambda: record("camera_destroy"))
    app.audio_service = SimpleNamespace(shutdown=lambda: record("audio_shutdown"))
    app.settings = object()
    app.logArea = root
    app.log_pane = object()
    app.command_stats = {}
    app.profile = ""
    app._closing = False
    app._exit_requested = False
    app._exit_phase = "idle"
    app._exit_retry_after_id = None
    app._display_after_id = None
    app._sash_after_id = None
    setattr(app, "_cancel_player_lamp_patrol", lambda: None)
    setattr(app, "_remember_geometry", lambda: record("geometry_saved"))
    setattr(app, "_save_settings", lambda: record("settings_saved"))
    setattr(app, "_stop_meter", lambda: record("audio_meter_stop"))
    setattr(app, "closingController", lambda: record("controller_closing"))

    import Window as window_module

    monkeypatch.setattr(
        window_module.tkmsg,
        "askyesno",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        window_module.cv2,
        "destroyAllWindows",
        lambda: record("cv2_destroy"),
    )
    monkeypatch.setattr(
        window_module.WindowGeometry,
        "rememberSash",
        lambda *_args: record("sash_saved"),
    )
    monkeypatch.setattr(
        window_module.CommandStats,
        "save",
        lambda *_args: record("stats_saved"),
    )
    return app, events, root


@dataclass(frozen=True, slots=True)
class ClaimToken:
    value: str


class RunClass(StrEnum):
    NATIVE_PRODUCTION = "native_production"
    FALLBACK_FUNCTIONAL = "fallback_functional"
    DIAGNOSTIC = "diagnostic"


class RunPhase(StrEnum):
    PRODUCTION = "production"
    FALLBACK = "fallback"
    DIAGNOSTIC = "diagnostic"
    TEARDOWN = "teardown"


class ChildMode(StrEnum):
    NORMAL = "normal"
    CHILD = "child"


@dataclass(frozen=True, slots=True)
class E2EConfig:
    phase: RunPhase
    run_class: RunClass
    configured_fps: int
    diagnostic_rate_hz: float | None
    warmup_s: float
    measurement_s: float
    evidence_dir: Path
    source_hz: int
    child_mode: ChildMode
    test_id: str
    watchdog_timeout_s: float

    def execution_contract(self) -> ExecutionContract:
        return {
            "phase": self.phase.value,
            "run_class": self.run_class.value,
            "configured_fps": self.configured_fps,
            "diagnostic_rate_hz": self.diagnostic_rate_hz,
            "source_hz": self.source_hz,
            "warmup_s": self.warmup_s,
            "measurement_s": self.measurement_s,
            "evidence_dir": str(self.evidence_dir),
            "test_id": self.test_id,
            "watchdog_timeout_s": self.watchdog_timeout_s,
        }

    def as_json(self) -> dict[str, Any]:
        return {**self.execution_contract(), "child_mode": self.child_mode.value}


TEST_CONTRACTS: Final[dict[str, tuple[RunPhase, RunClass]]] = {
    "test_native_production_60hz_meets_hard_contract": (
        RunPhase.PRODUCTION,
        RunClass.NATIVE_PRODUCTION,
    ),
    "test_native_production_15hz_meets_configured_contract": (
        RunPhase.PRODUCTION,
        RunClass.NATIVE_PRODUCTION,
    ),
    "test_native_production_5hz_records_performance_reference": (
        RunPhase.PRODUCTION,
        RunClass.NATIVE_PRODUCTION,
    ),
    "test_native_extended_target_meets_configured_contract": (
        RunPhase.PRODUCTION,
        RunClass.NATIVE_PRODUCTION,
    ),
    "test_after_fallback_functional_at_configured_target": (
        RunPhase.FALLBACK,
        RunClass.FALLBACK_FUNCTIONAL,
    ),
    "test_diagnostic_rate_is_reference_only": (
        RunPhase.DIAGNOSTIC,
        RunClass.DIAGNOSTIC,
    ),
    "test_window_exit_waits_for_preview_teardown": (
        RunPhase.TEARDOWN,
        RunClass.NATIVE_PRODUCTION,
    ),
}


def pin_files(paths: Sequence[Path]) -> dict[str, FilePin]:
    pins: dict[str, FilePin] = {}
    for path in paths:
        path_text = str(path)
        if not path.is_file():
            pins[path_text] = FilePin(path_text, False, 0, "")
            continue
        payload = path.read_bytes()
        pins[path_text] = FilePin(
            path_text,
            True,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )
    return pins


def source_pins_unchanged(
    start: Mapping[str, FilePin], end: Mapping[str, FilePin]
) -> bool:
    return start == end


def source_pins_complete(pins: Mapping[str, FilePin]) -> bool:
    return all(pin.exists for pin in pins.values())


def latest_only_accounting_balanced(
    *,
    worker_tick_published_count: int,
    main_dispatch_count: int,
    pending_tick_superseded_count: int,
    stale_tick_dropped_count: int,
    pending_tick_present_at_window_end: int,
) -> bool:
    return worker_tick_published_count == (
        main_dispatch_count
        + pending_tick_superseded_count
        + stale_tick_dropped_count
        + pending_tick_present_at_window_end
    )


def thread_ids_valid(thread_ids: Mapping[str, int | str | None]) -> bool:
    try:
        main_raw = thread_ids["main"]
        window_owner_raw = thread_ids["window_owner"]
        preview_worker_raw = thread_ids["preview_worker"]
        camera_source_raw = thread_ids["camera_source"]
        if (
            main_raw is None
            or window_owner_raw is None
            or preview_worker_raw is None
            or camera_source_raw is None
        ):
            return False
        main = int(main_raw)
        window_owner = int(window_owner_raw)
        preview_worker = int(preview_worker_raw)
        camera_source = int(camera_source_raw)
    except (KeyError, TypeError, ValueError):
        return False
    camera_thread_valid = (
        "camera_thread" in thread_ids and thread_ids["camera_thread"] is None
    )
    camera_claim_valid = (
        thread_ids.get("camera_thread_claim") == "not_applicable_synthetic_source"
    )
    return bool(
        main == window_owner
        and preview_worker != main
        and camera_source != main
        and camera_thread_valid
        and camera_claim_valid
    )


def cadence_contract(configured_fps: int) -> CadenceContract:
    if configured_fps not in PRODUCTION_FPS_VALUES:
        raise ConfigError(
            "POKECON_FPS_CONFIGURED_FPS",
            f"must be one of {PRODUCTION_FPS_VALUES}",
        )
    return CadenceContract(
        configured_fps=configured_fps,
        target_mean_min_hz=float(configured_fps),
        target_mean_max_hz=configured_fps + 0.1,
        target_p1_min_hz=configured_fps - 1.0,
        cap_tolerance_hz=0.1,
        cap_tolerance_semantics=CAP_TOLERANCE_SEMANTICS,
    )


def measurement_overshoot(
    measurement_start_ns: int,
    actual_window_end_ns: int,
    configured_measurement_s: float,
    configured_fps: int,
) -> tuple[int, int, bool]:
    cadence_contract(configured_fps)
    nominal_end_ns = measurement_start_ns + int(configured_measurement_s * NS_PER_S)
    overshoot_ns = max(0, actual_window_end_ns - nominal_end_ns)
    target_period_ns = NS_PER_S // configured_fps
    limit_ns = max(2 * target_period_ns, 50_000_000)
    return overshoot_ns, limit_ns, overshoot_ns <= limit_ns


def _endpoint_intervals(
    entries_ns: Sequence[int], window_start_ns: int, window_end_ns: int
) -> list[tuple[int, int]]:
    inside = sorted(
        stamp for stamp in entries_ns if window_start_ns <= stamp < window_end_ns
    )
    return [
        (left, right)
        for left, right in zip(inside, inside[1:], strict=False)
        if window_start_ns <= left and right < window_end_ns
    ]


def _tk_delay_ms(duration_ns: int) -> int:
    return max(1, math.ceil(duration_ns / NS_PER_MS))


def _root_exists(root: Any) -> bool:
    import tkinter as tk

    try:
        return bool(root.winfo_exists())
    except tk.TclError:
        return False


def _root_is_destroyed(root: Any) -> bool:
    import tkinter as tk

    try:
        return not bool(root.winfo_exists())
    except tk.TclError:
        return True


def summarize_cadence(
    entries_ns: Sequence[int],
    *,
    measurement_start_ns: int,
    nominal_window_end_ns: int,
    configured_measurement_s: float,
) -> CadenceResult:
    count = sum(
        measurement_start_ns <= stamp < nominal_window_end_ns for stamp in entries_ns
    )
    intervals = _endpoint_intervals(
        entries_ns,
        measurement_start_ns,
        nominal_window_end_ns,
    )
    if not intervals:
        p1_hz = 0.0
    else:
        rank = max(1, math.ceil(0.01 * len(intervals)))
        frequencies = sorted(NS_PER_S / (right - left) for left, right in intervals)
        p1_hz = frequencies[rank - 1]
    interval_count = len(intervals)
    observed_span_ns = intervals[-1][1] - intervals[0][0] if intervals else 0
    mean_hz = interval_count * NS_PER_S / observed_span_ns if observed_span_ns else 0.0
    return CadenceResult(
        count=count,
        mean_hz=mean_hz,
        p1_hz=p1_hz,
        interval_count=interval_count,
        observed_span_ns=observed_span_ns,
    )


def normal_teardown_evidence_ok(
    teardown: Mapping[str, Any],
    *,
    native_clock: bool,
) -> bool:
    common = bool(
        teardown.get("stop_result") == "stopped"
        and teardown.get("root_destroyed_after_stop") is True
        and teardown.get("preview_worker_alive") is False
        and teardown.get("synthetic_source_alive") is False
        and teardown.get("pending_after_ids") == []
        and teardown.get("post_teardown_camera_read_count") == 0
        and teardown.get("source_stopped") is True
        and teardown.get("instrumentation_closed") is True
    )
    if not native_clock:
        return common
    return bool(
        common
        and teardown.get("window_class_destroyed") is True
        and teardown.get("window_class_unregistered") is True
    )


def evidence_gate_passes(
    run_class: str,
    *,
    functional_ok: bool,
    non_accounting_evidence_ok: bool,
    latest_only_accounting_ok: bool,
) -> bool:
    try:
        run_class_value = RunClass(run_class)
    except ValueError as exc:
        raise ConfigError("run_class", f"unknown run class: {run_class}") from exc
    match run_class_value:
        case RunClass.FALLBACK_FUNCTIONAL:
            return functional_ok and non_accounting_evidence_ok
        case RunClass.DIAGNOSTIC | RunClass.NATIVE_PRODUCTION:
            return (
                functional_ok
                and non_accounting_evidence_ok
                and latest_only_accounting_ok
            )
        case unreachable:
            assert_never(unreachable)


def decide_acceptance(
    *,
    run_class: str,
    clock_mode: str,
    contract: CadenceContract,
    dispatch: CadenceResult,
    present: CadenceResult,
    measurement_overshoot_ok: bool,
    functional_ok: bool,
    evidence_ok: bool,
    period_skipped_count: int = 0,
    blit_skipped_no_new_frame: int = 0,
) -> AcceptanceDecision:
    try:
        run_class_value = RunClass(run_class)
    except ValueError as exc:
        raise ConfigError("run_class", f"unknown run class: {run_class}") from exc
    evidence_passed = functional_ok and evidence_ok
    match run_class_value:
        case RunClass.DIAGNOSTIC | RunClass.FALLBACK_FUNCTIONAL:
            return AcceptanceDecision(
                production_accepted=False,
                functional_accepted=evidence_passed,
                test_passed=evidence_passed,
                accepted=False,
                period_skipped_count=period_skipped_count,
                blit_skipped_no_new_frame=blit_skipped_no_new_frame,
            )
        case RunClass.NATIVE_PRODUCTION:
            cadence_passed = all(
                result.count >= 2
                and contract.target_mean_min_hz
                <= result.mean_hz
                <= contract.target_mean_max_hz
                and result.p1_hz >= contract.target_p1_min_hz
                for result in (dispatch, present)
            )
            native_clock = clock_mode == "high_resolution"
            return AcceptanceDecision(
                production_accepted=(
                    native_clock
                    and cadence_passed
                    and measurement_overshoot_ok
                    and evidence_passed
                ),
                functional_accepted=evidence_passed,
                test_passed=evidence_passed,
                # Cadence is a recorded reference, not a gate: a sub-target run
                # is still accepted when it is functionally sound. A native
                # production run that fell back to the ``after`` clock is not.
                accepted=native_clock and evidence_passed,
                performance_reference=PerformanceReference(
                    within_reference=cadence_passed
                ),
                period_skipped_count=period_skipped_count,
                blit_skipped_no_new_frame=blit_skipped_no_new_frame,
            )
        case unreachable:
            assert_never(unreachable)


def select_dispatch_target(
    area: Any,
    dispatch_target: str | None = None,
) -> tuple[str, str]:
    if dispatch_target == "_dispatch_tick":
        return (
            dispatch_target,
            "PreviewClock WNDPROC to CaptureArea entry",
        )
    if dispatch_target == "capture":
        return (dispatch_target, "legacy CaptureArea.after callback entry")
    if dispatch_target is not None:
        raise ValueError(f"unsupported preview dispatch target: {dispatch_target}")
    clock = getattr(area, "_preview_clock", None)
    if clock is not None:
        return (
            "_dispatch_tick",
            "PreviewClock WNDPROC to CaptureArea entry",
        )
    return ("capture", "legacy CaptureArea.after callback entry")


def _start_capture_and_get_clock(area: Any) -> Any | None:
    area.startCapture()
    return getattr(area, "_preview_clock", None)


def _source_paths() -> tuple[Path, ...]:
    return tuple(REPO_ROOT / relative for relative in SOURCE_PIN_PATHS)


def _source_pins() -> dict[str, FilePin]:
    absolute = pin_files(_source_paths())
    return {
        relative: absolute[str(REPO_ROOT / relative)] for relative in SOURCE_PIN_PATHS
    }


def _source_pin_rows(
    pins: Mapping[str, FilePin],
) -> list[dict[str, str | int | bool]]:
    return [
        {
            "path": relative,
            "exists": pins[relative].exists,
            "bytes": pins[relative].bytes,
            "sha256": pins[relative].sha256,
        }
        for relative in SOURCE_PIN_PATHS
    ]


def _source_pins_from_rows(rows: Any) -> dict[str, FilePin]:
    if not isinstance(rows, list):
        raise ConfigError("source_pins", "parent source pin rows are invalid")
    pins: dict[str, FilePin] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ConfigError("source_pins", "parent source pin row is invalid")
        path = row.get("path")
        exists = row.get("exists")
        byte_count = row.get("bytes")
        sha256 = row.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(exists, bool)
            or not isinstance(byte_count, int)
            or not isinstance(sha256, str)
        ):
            raise ConfigError("source_pins", "parent source pin row is invalid")
        pins[path] = FilePin(path, exists, byte_count, sha256)
    if set(pins) != set(SOURCE_PIN_PATHS):
        raise ConfigError("source_pins", "parent source pin file set is invalid")
    return pins


def _missing_source_pins() -> dict[str, FilePin]:
    return {relative: FilePin(relative, False, 0, "") for relative in SOURCE_PIN_PATHS}


def _runtime_metadata() -> dict[str, str | int | float]:
    import tkinter as tk

    return {
        "pid": os.getpid(),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "tk_version": float(tk.TkVersion),
        "tcl_version": float(tk.TclVersion),
    }


def write_artifact_manifest(directory: Path) -> None:
    rows: list[dict[str, str | int | bool]] = []
    for name in REQUIRED_EVIDENCE_FILES:
        path = directory / name
        pin = pin_files((path,))[str(path)]
        rows.append(
            {
                "path": name,
                "exists": pin.exists,
                "bytes": pin.bytes,
                "sha256": pin.sha256,
            }
        )
    (directory / ARTIFACT_MANIFEST_FILE).write_text(
        json.dumps(
            {"schema_version": 1, "artifacts": rows},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def verify_artifact_manifest(directory: Path) -> None:
    manifest_path = directory / ARTIFACT_MANIFEST_FILE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError("artifact manifest is missing or invalid") from exc
    if not isinstance(manifest, dict):
        raise AssertionError("artifact manifest root is invalid")
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise AssertionError("artifact manifest schema mismatch")
    if schema_version != 1:
        raise AssertionError("artifact manifest schema mismatch")
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        raise AssertionError("artifact manifest rows are invalid")
    recorded: dict[str, tuple[bool, int, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AssertionError("artifact manifest row is invalid")
        name = row.get("path")
        if not isinstance(name, str) or name == ARTIFACT_MANIFEST_FILE:
            raise AssertionError("artifact manifest path is invalid")
        if name in recorded:
            raise AssertionError(f"artifact manifest duplicate path: {name}")
        if not {"exists", "bytes", "sha256"} <= row.keys():
            raise AssertionError(f"invalid artifact metadata: {name}")
        exists = row.get("exists")
        if not isinstance(exists, bool):
            raise AssertionError(f"invalid artifact metadata: {name}")
        if exists is not True:
            raise AssertionError(f"required artifact missing: {name}")
        byte_count = row.get("bytes")
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
        ):
            raise AssertionError(f"invalid artifact metadata: {name}")
        sha256 = row.get("sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in sha256)
        ):
            raise AssertionError(f"invalid SHA-256 for artifact: {name}")
        recorded[name] = (exists, byte_count, sha256)
    if set(recorded) != set(REQUIRED_EVIDENCE_FILES):
        raise AssertionError("artifact manifest file set mismatch")
    for name in REQUIRED_EVIDENCE_FILES:
        path = directory / name
        pin = pin_files((path,))[str(path)]
        if pin.exists is not True:
            raise AssertionError(f"required artifact missing: {name}")
        current = (pin.exists, pin.bytes, pin.sha256)
        if current != recorded[name]:
            raise AssertionError(
                f"artifact manifest mismatch for {name}: "
                f"expected {recorded[name]!r}, got {current!r}"
            )


class EvidenceWriter:
    def __init__(self, directory: Path, role: EvidenceRole) -> None:
        if not directory.is_dir():
            raise ConfigError(
                "POKECON_FPS_EVIDENCE_DIR",
                "must be claimed before evidence is written",
            )
        self.directory = directory
        self.role = role

    def _require_role(self, expected: EvidenceRole, artifact: str) -> None:
        if self.role != expected:
            raise ConfigError(artifact, f"requires {expected} writer")

    def write_json(self, name: str, payload: Mapping[str, Any]) -> None:
        if self.role == "child" and name in PARENT_OWNERSHIP_FILES:
            raise ConfigError(name, "parent ownership artifact is immutable")
        (self.directory / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_jsonl(self, name: str, rows: Sequence[Mapping[str, Any]]) -> None:
        content = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        (self.directory / name).write_text(content, encoding="utf-8")

    def _write_json_exclusive(self, name: str, payload: Mapping[str, Any]) -> None:
        try:
            with (self.directory / name).open("x", encoding="utf-8") as artifact:
                json.dump(payload, artifact, indent=2, sort_keys=True)
                artifact.write("\n")
        except FileExistsError as exc:
            raise ConfigError(name, "ownership token is already consumed") from exc

    def write_environment(self, config: E2EConfig) -> None:
        self._require_role("parent", "environment.json")
        self.write_json(
            "environment.json",
            {
                **config.as_json(),
                "source_pins": _source_pin_rows(_source_pins()),
                "runtime_metadata": _runtime_metadata(),
            },
        )

    def write_parent_handoff(self, config: E2EConfig, token: ClaimToken) -> None:
        self._require_role("parent", PARENT_HANDOFF_FILE)
        payload: ParentHandoffPayload = {
            "schema_version": 1,
            "claim_token": token.value,
            "execution_contract": config.execution_contract(),
            "source_pins": _source_pin_rows(_source_pins()),
        }
        self._write_json_exclusive(PARENT_HANDOFF_FILE, payload)

    def claim_child_ownership(
        self, config: E2EConfig, token: ClaimToken
    ) -> ChildOwnershipPayload:
        self._require_role("child", CHILD_OWNER_FILE)
        payload: ChildOwnershipPayload = {
            "schema_version": 1,
            "claim_token": token.value,
            "pid": os.getpid(),
            "execution_contract": config.execution_contract(),
            "source_pins": _source_pin_rows(_source_pins()),
        }
        self._write_json_exclusive(CHILD_OWNER_FILE, payload)
        return payload

    def write_child_environment(
        self, config: E2EConfig, ownership: ChildOwnershipPayload
    ) -> None:
        self._require_role("child", CHILD_ENVIRONMENT_FILE)
        self.write_json(
            CHILD_ENVIRONMENT_FILE,
            {
                **config.as_json(),
                "source_pins": _source_pin_rows(_source_pins()),
                "runtime_metadata": _runtime_metadata(),
                "ownership": ownership,
            },
        )

    def write_required_empty_evidence(self) -> None:
        for name in (
            "wakes.jsonl",
            "clock_pairs.jsonl",
            "present_intervals.jsonl",
            "dispatch_intervals.jsonl",
            "camera_reads.jsonl",
        ):
            self.write_jsonl(name, ())
        (self.directory / "health.jsonl").write_text("", encoding="utf-8")
        self.write_json("teardown.json", {"cleanup_status": "not_run"})
        self.write_json(
            "thread_ids.json",
            {
                "schema_version": 1,
                "main": 0,
                "window_owner": 0,
                "preview_worker": 0,
                "camera_source": 0,
                "camera_thread": None,
                "camera_thread_claim": "not_applicable_synthetic_source",
            },
        )
        pins = _source_pins()
        self.write_json(
            "source_pins.json",
            {
                "schema_version": 1,
                "start": _source_pin_rows(pins),
                "end": _source_pin_rows(pins),
                "unchanged": True,
                "complete": source_pins_complete(pins),
            },
        )


class SyntheticFrameSource:
    physical_camera_evidence = False
    physical_unique_frame_claim = False

    def __init__(self, source_hz: int = SOURCE_HZ) -> None:
        if source_hz != SOURCE_HZ:
            raise ConfigError("POKECON_FPS_SOURCE_HZ", "must be exactly 180")
        self.source_hz = source_hz
        self.capture_size = (SOURCE_WIDTH, SOURCE_HEIGHT)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stopped = False
        self._frame = np.empty((SOURCE_HEIGHT, SOURCE_WIDTH, 3), dtype=np.uint8)
        self._frame[:] = (23, 47, 91)
        self._frame.setflags(write=False)
        self._seq = 0
        self._last_read_seq: int | None = None
        self._reads: list[CameraReadRecord] = []
        self._publish_ns: list[int] = []
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._stopped = False
        self._thread = threading.Thread(
            target=self._produce,
            name="PreviewFpsSyntheticSource",
            daemon=True,
        )
        self._thread.start()

    def _produce(self) -> None:
        period_ns = NS_PER_S // self.source_hz
        next_ns = time.perf_counter_ns()
        while not self._stop.is_set():
            now_ns = time.perf_counter_ns()
            if now_ns < next_ns:
                if self._stop.wait((next_ns - now_ns) / NS_PER_S):
                    return
                continue
            with self._lock:
                self._seq += 1
                self._publish_ns.append(now_ns)
            next_ns += period_ns
            if next_ns <= now_ns:
                skipped = (now_ns - next_ns) // period_ns + 1
                next_ns += skipped * period_ns

    def stop(self) -> bool:
        self._stop.set()
        with self._lock:
            self._stopped = True
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=1.0)
        return not thread.is_alive()

    def destroy(self) -> bool:
        return self.stop()

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def producer_thread_native_id(self) -> int:
        thread = self._thread
        if thread is None or thread.native_id is None:
            return 0
        return int(thread.native_id)

    def _record_read_locked(
        self,
        sequence: int,
        *,
        frame_present: bool,
        error: str | None,
    ) -> None:
        duplicate = self._last_read_seq == sequence
        regression = self._last_read_seq is not None and sequence < self._last_read_seq
        self._reads.append(
            CameraReadRecord(
                perf_counter_ns=time.perf_counter_ns(),
                thread_native_id=threading.get_native_id(),
                sequence=sequence,
                frame_present=frame_present,
                duplicate=duplicate,
                regression=regression,
                error=error,
                post_teardown=self._stopped,
            )
        )
        self._last_read_seq = sequence

    def readFrame(self, copy: bool = False) -> np.ndarray | None:
        if copy:
            raise AssertionError("synthetic source is immutable")
        with self._lock:
            self._record_read_locked(
                self._seq,
                frame_present=True,
                error=None,
            )
            return self._frame

    def readFrameWithSeq(self, copy: bool = False) -> tuple[np.ndarray | None, int]:
        if copy:
            raise AssertionError("synthetic source is immutable")
        with self._lock:
            sequence = self._seq
            self._record_read_locked(
                sequence,
                frame_present=True,
                error=None,
            )
            return self._frame, sequence

    def readFrameWithTiming(
        self, copy: bool = False
    ) -> tuple[np.ndarray | None, int, int, int]:
        # core.Camera と同じ 4 つ組。提示経路の入力はここを通る。
        if copy:
            raise AssertionError("synthetic source is immutable")
        with self._lock:
            sequence = self._seq
            self._record_read_locked(
                sequence,
                frame_present=True,
                error=None,
            )
            t_capture_ns = time.perf_counter_ns()
            t_ready_ns = time.perf_counter_ns()
            return self._frame, sequence, t_capture_ns, t_ready_ns

    def frame_seq(self) -> int:
        with self._lock:
            return self._seq

    def camera_records(self) -> tuple[CameraReadRecord, ...]:
        with self._lock:
            return tuple(self._reads)

    def camera_summary(self) -> dict[str, Any]:
        rows = self.camera_records()
        sequences = [row.sequence for row in rows]
        return {
            "camera_read_count": len(rows),
            "camera_frame_present_count": sum(row.frame_present for row in rows),
            "camera_none_count": sum(not row.frame_present for row in rows),
            "camera_read_error_count": sum(row.error is not None for row in rows),
            "camera_unique_sequence_count": len(set(sequences)),
            "camera_duplicate_sequence_count": sum(row.duplicate for row in rows),
            "camera_sequence_regression_count": sum(row.regression for row in rows),
            "camera_last_sequence": sequences[-1] if sequences else 0,
            "post_teardown_camera_read_count": sum(row.post_teardown for row in rows),
            "camera_thread_claim": "not_applicable_synthetic_source",
            "capture_device": dict(SYNTHETIC_CAPTURE_DEVICE),
        }


class PresentInstrumentation:
    def __init__(
        self,
        area: Any,
        configured_fps: int,
        dispatch_target: str | None = None,
    ) -> None:
        self.area = area
        self.configured_fps = configured_fps
        self.surface = area._surface
        self.target_name, self.dispatch_oracle = select_dispatch_target(
            area,
            dispatch_target,
        )
        self.original_dispatch = getattr(area, self.target_name)
        self.original_after = area.after
        self.original_draw = area._drawFrame
        self.original_compose = self.surface.compose
        self.original_present = self.surface.present
        self.target_had_instance_value = self.target_name in vars(area)
        self.target_instance_value = vars(area).get(self.target_name)
        self.after_had_instance_value = "after" in vars(area)
        self.after_instance_value = vars(area).get("after")
        self.draw_had_instance_value = "_drawFrame" in vars(area)
        self.draw_instance_value = vars(area).get("_drawFrame")
        self.compose_had_instance_value = "compose" in vars(self.surface)
        self.compose_instance_value = vars(self.surface).get("compose")
        self.present_had_instance_value = "present" in vars(self.surface)
        self.present_instance_value = vars(self.surface).get("present")
        self.dispatch_records: list[DispatchRecord] = []
        self.presents: list[PresentRecord] = []
        self.draws: list[DrawRecord] = []
        self.callback_schedules: list[CallbackSchedule] = []
        self._active_dispatch: DispatchRecord | None = None
        self._active_draw: DrawRecord | None = None
        self._last_draw_sequence: int | None = None
        self._compose_duration_ns = 0
        self._next_dispatch_id = 0
        self._closed = False
        self._dispatch_result_type: Any = None
        try:
            from ui import preview_clock

            self._dispatch_result_type = preview_clock.DispatchResult
        except ImportError:
            self._dispatch_result_type = None
        self._install()

    def _clock_health(self) -> Mapping[str, Any]:
        clock = getattr(self.area, "_preview_clock", None)
        if clock is None:
            return {}
        return clock.health_snapshot()

    def _install(self) -> None:
        def dispatch_wrapper(*args: Any, **kwargs: Any) -> Any:
            health = self._clock_health()
            record = DispatchRecord(
                dispatch_id=self._next_dispatch_id,
                dispatch_enter_ns=time.perf_counter_ns(),
                dispatch_exit_ns=None,
                sequence=int(health.get("last_sequence", 0)),
                epoch=int(health.get("epoch", 0)),
                generation=int(health.get("generation", 0)),
                configured_fps=int(health.get("configured_fps", self.configured_fps)),
                schedule="unknown",
                present_observed=False,
            )
            self._next_dispatch_id += 1
            previous = self._active_dispatch
            self._active_dispatch = record
            try:
                result = self.original_dispatch(*args, **kwargs)
                if self._dispatch_result_type is not None and isinstance(
                    result, self._dispatch_result_type
                ):
                    record.schedule = str(result.schedule)
                else:
                    record.schedule = "legacy_unknown"
                return result
            finally:
                record.dispatch_exit_ns = time.perf_counter_ns()
                self.dispatch_records.append(record)
                self._active_dispatch = previous

        def after_wrapper(
            delay_ms: int, callback: Callable[..., Any], *args: Any, **kwargs: Any
        ) -> Any:
            enter_ns = time.perf_counter_ns()
            after_id = self.original_after(delay_ms, callback, *args, **kwargs)
            self.callback_schedules.append(
                CallbackSchedule(
                    callback_name=getattr(
                        callback, "__name__", type(callback).__name__
                    ),
                    requested_delay_ms=int(delay_ms),
                    schedule_enter_ns=enter_ns,
                    scheduled_due_ns=enter_ns + int(delay_ms) * NS_PER_MS,
                    after_id=str(after_id),
                )
            )
            return after_id

        def draw_wrapper(frame: Any, sequence: int | None = None) -> Any:
            enter_ns = time.perf_counter_ns()
            duplicate = sequence is not None and sequence == self._last_draw_sequence
            if sequence is not None:
                self._last_draw_sequence = sequence
            record = DrawRecord(
                draw_enter_ns=enter_ns,
                draw_exit_ns=enter_ns,
                sequence=sequence,
                frame_present=frame is not None,
                duplicate_sequence=duplicate,
            )
            self.draws.append(record)
            previous = self._active_draw
            self._active_draw = record
            try:
                return self.original_draw(frame, sequence)
            finally:
                self.draws[-1] = DrawRecord(
                    draw_enter_ns=record.draw_enter_ns,
                    draw_exit_ns=time.perf_counter_ns(),
                    sequence=record.sequence,
                    frame_present=record.frame_present,
                    duplicate_sequence=record.duplicate_sequence,
                )
                self._active_draw = previous

        def compose_wrapper(*args: Any, **kwargs: Any) -> Any:
            enter_ns = time.perf_counter_ns()
            try:
                return self.original_compose(*args, **kwargs)
            finally:
                self._compose_duration_ns = time.perf_counter_ns() - enter_ns

        def present_wrapper(*args: Any, **kwargs: Any) -> Any:
            enter_ns = time.perf_counter_ns()
            active_dispatch = self._active_dispatch
            active_draw = self._active_draw
            try:
                return self.original_present(*args, **kwargs)
            finally:
                exit_ns = time.perf_counter_ns()
                if active_dispatch is not None:
                    active_dispatch.present_observed = True
                self.presents.append(
                    PresentRecord(
                        present_enter_ns=enter_ns,
                        present_exit_ns=exit_ns,
                        present_duration_ns=exit_ns - enter_ns,
                        compose_duration_ns=self._compose_duration_ns,
                        sequence=active_draw.sequence if active_draw else None,
                        dispatch_id=(
                            active_dispatch.dispatch_id if active_dispatch else None
                        ),
                    )
                )

        setattr(self.area, self.target_name, dispatch_wrapper)
        self.area.after = after_wrapper
        self.area._drawFrame = draw_wrapper
        self.surface.compose = compose_wrapper
        self.surface.present = present_wrapper

    @staticmethod
    def _restore_instance(target: Any, name: str, had_value: bool, value: Any) -> None:
        if had_value:
            setattr(target, name, value)
            return
        try:
            delattr(target, name)
        except AttributeError:
            return

    def close(self) -> None:
        if self._closed:
            return
        self._restore_instance(
            self.area,
            self.target_name,
            self.target_had_instance_value,
            self.target_instance_value,
        )
        self._restore_instance(
            self.area,
            "after",
            self.after_had_instance_value,
            self.after_instance_value,
        )
        self._restore_instance(
            self.area,
            "_drawFrame",
            self.draw_had_instance_value,
            self.draw_instance_value,
        )
        self._restore_instance(
            self.surface,
            "compose",
            self.compose_had_instance_value,
            self.compose_instance_value,
        )
        self._restore_instance(
            self.surface,
            "present",
            self.present_had_instance_value,
            self.present_instance_value,
        )
        self._closed = True

    def dispatch_entries(self) -> tuple[int, ...]:
        return tuple(record.dispatch_enter_ns for record in self.dispatch_records)

    def present_entries(self) -> tuple[int, ...]:
        return tuple(record.present_enter_ns for record in self.presents)


class RealCaptureHarness:
    def __init__(self, config: E2EConfig) -> None:
        self.config = config
        self.root: Any | None = None
        self.source: SyntheticFrameSource | None = None
        self.area: Any | None = None
        self.instrumentation: PresentInstrumentation | None = None
        self.is_show: Any | None = None
        self.clock: Any | None = None
        self.clock_mode = "after"
        self.dispatch_oracle = "legacy CaptureArea.after callback entry"
        self.start_pins = _source_pins()
        self.end_pins = self.start_pins
        self.start_snapshot: dict[str, Any] = {}
        self.end_snapshot: dict[str, Any] = {}
        self.end_accounting: Mapping[str, Any] = {"latest_only_accounting_ok": False}
        self.health_rows: list[dict[str, Any]] = []
        self.measurement_window: Window | None = None
        self.stop_result: str = "not_stopped"
        self.preview_worker_native_id = 0
        self.main_native_id = threading.get_native_id()
        self._preview_clock_module: Any | None = None
        self.preview_clock_available = False
        self._original_factory: Any | None = None
        self._measurement_stop_after_id: str | None = None
        self._measurement_quit_after_id: str | None = None
        self._closed = False

    def _install_factory_patch(self) -> None:
        try:
            from ui import preview_clock
        except ImportError:
            return
        self._preview_clock_module = preview_clock
        self.preview_clock_available = True
        self._original_factory = preview_clock._create_native_runtime
        original_factory = self._original_factory
        match self.config.run_class:
            case RunClass.DIAGNOSTIC:
                diagnostic_rate_hz = self.config.diagnostic_rate_hz
                if diagnostic_rate_hz is None:
                    raise ConfigError(
                        "POKECON_FPS_DIAGNOSTIC_RATE_HZ",
                        "is required for diagnostic runs",
                    )

                def diagnostic_factory(**kwargs: Any) -> Any:
                    return original_factory(
                        **kwargs,
                        diagnostic_rate_hz=diagnostic_rate_hz,
                    )

                preview_clock._create_native_runtime = diagnostic_factory
            case RunClass.FALLBACK_FUNCTIONAL:

                def fallback_factory(**_kwargs: Any) -> Any:
                    raise preview_clock._PreviewClockError(
                        "E2E forced native initialization failure"
                    )

                preview_clock._create_native_runtime = fallback_factory
            case RunClass.NATIVE_PRODUCTION:
                return
            case unreachable:
                assert_never(unreachable)

    def _restore_factory_patch(self) -> None:
        preview_clock = self._preview_clock_module
        original_factory = self._original_factory
        if preview_clock is not None and original_factory is not None:
            preview_clock._create_native_runtime = original_factory
        self._preview_clock_module = None
        self._original_factory = None

    def start(self) -> None:
        import tkinter as tk

        from GuiAssets import CaptureArea

        self._install_factory_patch()
        self.source = SyntheticFrameSource(self.config.source_hz)
        self.root = tk.Tk()
        self.root.title("PokeCon preview FPS E2E")
        self.root.geometry("1280x720+0+0")
        setattr(
            self.root,
            "is_use_left_stick_mouse",
            tk.BooleanVar(master=self.root, value=True),
        )
        setattr(
            self.root,
            "is_use_right_stick_mouse",
            tk.BooleanVar(master=self.root, value=True),
        )
        self.is_show = tk.BooleanVar(master=self.root, value=True)
        self.area = CaptureArea(
            self.source,
            self.config.configured_fps,
            self.is_show,
            None,
            master=self.root,
            show_width=SOURCE_WIDTH,
            show_height=SOURCE_HEIGHT,
            take_stick_log=False,
        )
        self.area.pack(fill=tk.BOTH, expand=True)
        self.root.update_idletasks()
        self.source.start()
        dispatch_target = (
            None
            if self.config.run_class is RunClass.FALLBACK_FUNCTIONAL
            else "_dispatch_tick"
        )
        self.instrumentation = PresentInstrumentation(
            self.area,
            configured_fps=self.config.configured_fps,
            dispatch_target=dispatch_target,
        )
        self.clock = _start_capture_and_get_clock(self.area)
        self.dispatch_oracle = self.instrumentation.dispatch_oracle
        if self.clock is None:
            self.clock_mode = "after"
        else:
            self.clock_mode = str(self.clock.mode())
            snapshot = self.clock.health_snapshot()
            self.preview_worker_native_id = int(
                snapshot.get("worker_thread_native_id", 0)
            )
        self.health_rows.append(
            {
                "record_type": "harness_start",
                "clock_mode": self.clock_mode,
                "configured_fps": self.config.configured_fps,
                "diagnostic_rate_hz": self.config.diagnostic_rate_hz,
                "thread_ids": self.thread_ids_payload(),
            }
        )

    def _record_transition(
        self, transition: str, before: Mapping[str, Any], after: Mapping[str, Any]
    ) -> None:
        self.health_rows.append(
            {
                "record_type": "schedule_transition",
                "transition": transition,
                "before": dict(before),
                "after": dict(after),
            }
        )

    def _health_snapshot(self) -> dict[str, Any]:
        if self.clock is None:
            return {}
        return dict(self.clock.health_snapshot())

    def begin_evidence_window(self, measurement_start_ns: int) -> None:
        if self.clock is not None:
            self.clock._begin_evidence_window()
            self.start_snapshot = self._health_snapshot()
        else:
            self.start_snapshot = {}
        self.health_rows.append(
            {
                "record_type": "measurement_start_snapshot",
                "perf_counter_ns": measurement_start_ns,
                "snapshot": self.start_snapshot,
            }
        )

    def end_evidence_window(self, actual_window_end_ns: int) -> None:
        if self.clock is not None:
            self.end_accounting = self.clock._end_evidence_window()
            self.end_snapshot = self._health_snapshot()
        else:
            self.end_accounting = {"latest_only_accounting_ok": False}
            self.end_snapshot = {}
        self.health_rows.append(
            {
                "record_type": "measurement_end_snapshot",
                "perf_counter_ns": actual_window_end_ns,
                "snapshot": self.end_snapshot,
                "accounting": dict(self.end_accounting),
            }
        )
        if self.area is None:
            raise HarnessStateError("CaptureArea was not created")
        self._record_stop_result(self.area.stopCapture())
        if self.stop_result == "pending":
            self._schedule_measurement_stop_retry()
        else:
            self._schedule_measurement_quit()

    def _record_stop_result(self, result: Any) -> None:
        self.stop_result = (
            "stopped" if result is None else str(getattr(result, "value", result))
        )

    def _schedule_measurement_stop_retry(self) -> None:
        if self.root is None or self._measurement_stop_after_id is not None:
            return
        self._measurement_stop_after_id = str(
            self.root.after(50, self._retry_measurement_stop)
        )

    def _schedule_measurement_quit(self) -> None:
        if self.root is None or self._measurement_quit_after_id is not None:
            return
        self._measurement_quit_after_id = str(
            self.root.after(50, self._quit_after_measurement_stop)
        )

    def _retry_measurement_stop(self) -> None:
        self._measurement_stop_after_id = None
        if self.area is None:
            raise HarnessStateError("CaptureArea was not created")
        self._record_stop_result(self.area.stopCapture())
        if self.stop_result == "pending":
            self._schedule_measurement_stop_retry()
            return
        self._schedule_measurement_quit()

    def _quit_after_measurement_stop(self) -> None:
        self._measurement_quit_after_id = None
        if self.root is not None:
            self.root.quit()

    def _complete_five_hz_warmup(self, root: Any) -> None:
        area = self.area
        is_show = self.is_show
        instrumentation = self.instrumentation
        if area is None or is_show is None or instrumentation is None:
            raise HarnessStateError("5 Hz warmup harness is incomplete")
        before = self._health_snapshot()
        area.setFps(15)
        self._record_transition("set_fps_15", before, self._health_snapshot())
        root.after(67, lambda: self._complete_five_hz_idle(root))

    def _complete_five_hz_idle(self, root: Any) -> None:
        area = self.area
        is_show = self.is_show
        instrumentation = self.instrumentation
        if area is None or is_show is None or instrumentation is None:
            raise HarnessStateError("5 Hz idle harness is incomplete")
        before = self._health_snapshot()
        area.setFps(5)
        self._record_transition("set_fps_5", before, self._health_snapshot())
        before_idle = self._health_snapshot()
        is_show.set(False)
        self._record_transition(
            "show_false",
            before_idle,
            self._health_snapshot(),
        )
        idle_turns = 0
        started_ns = time.perf_counter_ns()

        def check_idle() -> None:
            nonlocal idle_turns
            idle_turns += 1
            elapsed_ns = time.perf_counter_ns() - started_ns
            if idle_turns < 2 or elapsed_ns < 400_000_000:
                root.after(50, check_idle)
                return
            before_idle = self._health_snapshot()
            is_show.set(True)
            self._record_transition(
                "show_true",
                before_idle,
                self._health_snapshot(),
            )
            root.after(200, self._begin_measurement_window)

        root.after(50, check_idle)

    def _begin_measurement_window(self) -> None:
        root = self.root
        if root is None:
            raise HarnessStateError("Tk root was not created")
        measurement_start_ns = time.perf_counter_ns()
        self.begin_evidence_window(measurement_start_ns)
        nominal_end_ns = measurement_start_ns + int(
            self.config.measurement_s * NS_PER_S
        )
        self.measurement_window = Window(measurement_start_ns, nominal_end_ns)
        root.after(
            _tk_delay_ms(int(self.config.measurement_s * NS_PER_S)),
            self._measurement_tick,
        )

    def _measurement_tick(self) -> None:
        if self.measurement_window is None or self.root is None:
            raise HarnessStateError("measurement window was not initialized")
        now_ns = time.perf_counter_ns()
        if now_ns < self.measurement_window.end_ns:
            remaining_ns = self.measurement_window.end_ns - now_ns
            self.root.after(
                _tk_delay_ms(remaining_ns),
                self._measurement_tick,
            )
            return
        self.end_evidence_window(now_ns)
        self.measurement_window = Window(
            self.measurement_window.start_ns,
            now_ns,
        )

    def run_measurement(self) -> Window:
        root = self.root
        if root is None:
            raise HarnessStateError("harness was not started")
        origin_ns = time.perf_counter_ns()
        warmup_deadline_ns = origin_ns + int(self.config.warmup_s * NS_PER_S)

        def warmup_tick() -> None:
            now_ns = time.perf_counter_ns()
            if now_ns < warmup_deadline_ns:
                remaining_ns = warmup_deadline_ns - now_ns
                root.after(
                    _tk_delay_ms(remaining_ns),
                    warmup_tick,
                )
                return
            if self.config.configured_fps == 5:
                self._complete_five_hz_warmup(root)
            else:
                self._begin_measurement_window()

        root.after(0, warmup_tick)
        root.mainloop()
        if self.measurement_window is None:
            raise HarnessStateError("Tk event loop ended before measurement completed")
        return self.measurement_window

    def thread_ids_payload(self) -> dict[str, int | None | str]:
        source_thread_id = (
            0 if self.source is None else self.source.producer_thread_native_id()
        )
        return {
            "schema_version": 1,
            "main": self.main_native_id,
            "window_owner": self.main_native_id,
            "preview_worker": self.preview_worker_native_id,
            "camera_source": source_thread_id,
            "camera_thread": None,
            "camera_thread_claim": "not_applicable_synthetic_source",
        }

    def close(self) -> dict[str, Any]:
        if self._closed:
            return {"already_closed": True}
        phase_order: list[str] = []
        if self.area is not None:
            self._record_stop_result(self.area.stopCapture())
        if self._measurement_stop_after_id is not None and self.root is not None:
            self.root.after_cancel(self._measurement_stop_after_id)
            self._measurement_stop_after_id = None
        if self._measurement_quit_after_id is not None and self.root is not None:
            self.root.after_cancel(self._measurement_quit_after_id)
            self._measurement_quit_after_id = None
        phase_order.append("preview_stop")
        source_stopped = True
        if self.source is not None:
            source_stopped = self.source.stop()
        phase_order.append("synthetic_source_stop")
        instrumentation_closed = True
        if self.instrumentation is not None:
            self.instrumentation.close()
        phase_order.append("instrumentation_restore")
        self._restore_factory_patch()
        pending_after_ids = [] if self.root is None else _pending_after_ids(self.root)
        preview_worker_alive = _clock_worker_alive(self.clock)
        root_destroyed = False
        if self.root is not None:
            import tkinter as tk

            try:
                self.root.destroy()
                root_destroyed = _root_is_destroyed(self.root)
            except (tk.TclError, RuntimeError, AttributeError):
                root_destroyed = False
        phase_order.append("root_destroy")
        self._closed = True
        camera_summary = {} if self.source is None else self.source.camera_summary()
        class_evidence: bool | None = None
        if self.clock is not None:
            health = self._health_snapshot()
            class_evidence = bool(
                health.get("window_class_destroyed", False)
                and health.get("window_class_unregistered", False)
            )
        return {
            "stop_result": self.stop_result,
            "root_alive_while_pending": None,
            "root_destroyed_after_stop": root_destroyed,
            "preview_worker_alive": preview_worker_alive,
            "synthetic_source_alive": (
                False if self.source is None else self.source.is_alive()
            ),
            "post_teardown_camera_read_count": int(
                camera_summary.get("post_teardown_camera_read_count", 0)
            ),
            "pending_after_ids": pending_after_ids,
            "window_class_destroyed": class_evidence,
            "window_class_unregistered": class_evidence,
            "source_stopped": source_stopped,
            "instrumentation_closed": instrumentation_closed,
            "phase_order": phase_order,
            "preview_clock_available": self.preview_clock_available,
            "pending_teardown_proven": False,
        }


def _record_to_row(record: Any) -> dict[str, Any]:
    if not isinstance(record, type) and is_dataclass(record):
        field_names = getattr(record, "__dataclass_fields__", {})
        return {name: getattr(record, name) for name in field_names}
    if isinstance(record, Mapping):
        return dict(record)
    raise ConfigError("evidence_queue", f"unsupported record type: {type(record)}")


def _drain_clock_evidence(clock: Any | None) -> list[dict[str, Any]]:
    if clock is None:
        return []
    drain = getattr(clock, "_drain_evidence", None)
    if not callable(drain):
        return []
    records = drain()
    return [_record_to_row(record) for record in records]


def _row_int(row: Mapping[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError):
        return default


def _accounting_values(
    accounting: Mapping[str, Any],
) -> dict[str, int | bool] | None:
    balance_keys = (
        "worker_tick_published_count",
        "pending_tick_superseded_count",
        "main_dispatch_count",
        "stale_tick_dropped_count",
        "pending_tick_present_at_window_end",
    )
    diagnostic_keys = ("period_skipped_count", "blit_skipped_no_new_frame")
    counters: dict[str, int] = {}
    for key in (*balance_keys, *diagnostic_keys):
        value = accounting.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        counters[key] = value
    explicit_status = accounting.get("latest_only_accounting_ok")
    if not isinstance(explicit_status, bool):
        return None
    return {
        **counters,
        "latest_only_accounting_ok": (
            explicit_status
            and latest_only_accounting_balanced(
                **{key: counters[key] for key in balance_keys}
            )
        ),
    }


def _interval_rows(
    entries_ns: Sequence[int],
    window_start_ns: int,
    window_end_ns: int,
) -> list[dict[str, int | float]]:
    return [
        {
            "start_ns": left,
            "end_ns": right,
            "interval_ns": right - left,
            "frequency_hz": NS_PER_S / (right - left),
        }
        for left, right in _endpoint_intervals(
            entries_ns,
            window_start_ns,
            window_end_ns,
        )
    ]


def _cadence_gap_rows(
    entries_ns: Sequence[int],
    *,
    stream: str,
    window_start_ns: int,
    window_end_ns: int,
    sequence_by_entry_ns: Mapping[int, int | None],
) -> list[dict[str, str | int | None]]:
    rows: list[dict[str, str | int | None]] = []
    for left, right in _endpoint_intervals(
        entries_ns,
        window_start_ns,
        window_end_ns,
    ):
        gap_ns = right - left
        if gap_ns < GAP_THRESHOLD_NS:
            continue
        rows.append(
            {
                "stream": stream,
                "clock_domain": "perf_counter",
                "start_ns": left,
                "end_ns": right,
                "gap_ns": gap_ns,
                "before_sequence": sequence_by_entry_ns.get(left),
                "after_sequence": sequence_by_entry_ns.get(right),
                "recovery": "next_entry_observed",
                "reason": "endpoint_interval_at_or_above_threshold",
            }
        )
    return rows


def _qpc_timestamp(row: Mapping[str, Any]) -> int:
    for key in ("qpc_ns", "timestamp_qpc", "emitted_qpc", "wake_qpc"):
        try:
            return int(row[key])
        except (KeyError, TypeError, ValueError):
            continue
    raise ConfigError("evidence_queue", "QPC evidence row has no QPC timestamp")


def _qpc_gap_rows(
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    record_type: str,
    stream: str,
) -> list[dict[str, str | int]]:
    selected = [row for row in evidence_rows if row.get("record_type") == record_type]
    selected.sort(key=_qpc_timestamp)
    rows: list[dict[str, str | int]] = []
    for left, right in zip(selected, selected[1:], strict=False):
        qpc_frequency_hz = _row_int(
            right,
            "qpc_frequency_hz",
            _row_int(left, "qpc_frequency_hz"),
        )
        if qpc_frequency_hz <= 0:
            raise ConfigError(
                "evidence_queue",
                "QPC gap evidence requires a positive qpc_frequency_hz",
            )
        gap_ticks = _qpc_timestamp(right) - _qpc_timestamp(left)
        gap_ns = math.ceil(gap_ticks * NS_PER_S / qpc_frequency_hz)
        if gap_ns < GAP_THRESHOLD_NS:
            continue
        rows.append(
            {
                "stream": stream,
                "clock_domain": "qpc",
                "start_ns": _qpc_timestamp(left),
                "end_ns": _qpc_timestamp(right),
                "gap_ns": gap_ns,
                "before_sequence": _row_int(left, "sequence"),
                "after_sequence": _row_int(right, "sequence"),
                "recovery": "next_wake_observed",
                "reason": "qpc_interval_at_or_above_threshold",
                "qpc_frequency_hz": qpc_frequency_hz,
            }
        )
    return rows


def _build_report(
    config: E2EConfig,
    harness: RealCaptureHarness,
    window: Window,
    teardown: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        harness.source is None
        or harness.instrumentation is None
        or harness.area is None
    ):
        raise HarnessStateError("harness was not started")
    instrumentation = harness.instrumentation
    contract = cadence_contract(config.configured_fps)
    nominal_end_ns = window.start_ns + int(config.measurement_s * NS_PER_S)
    dispatch_entries = instrumentation.dispatch_entries()
    present_entries = instrumentation.present_entries()
    dispatch = summarize_cadence(
        dispatch_entries,
        measurement_start_ns=window.start_ns,
        nominal_window_end_ns=nominal_end_ns,
        configured_measurement_s=config.measurement_s,
    )
    present = summarize_cadence(
        present_entries,
        measurement_start_ns=window.start_ns,
        nominal_window_end_ns=nominal_end_ns,
        configured_measurement_s=config.measurement_s,
    )
    overshoot_ns, overshoot_limit_ns, overshoot_ok = measurement_overshoot(
        window.start_ns,
        window.end_ns,
        config.measurement_s,
        config.configured_fps,
    )
    dispatch_interval_rows = _interval_rows(
        dispatch_entries,
        window.start_ns,
        nominal_end_ns,
    )
    present_interval_rows = _interval_rows(
        present_entries,
        window.start_ns,
        nominal_end_ns,
    )
    dispatch_sequence_by_entry = {
        record.dispatch_enter_ns: record.sequence
        for record in instrumentation.dispatch_records
    }
    present_sequence_by_entry = {
        record.present_enter_ns: record.sequence for record in instrumentation.presents
    }
    dispatch_gaps = _cadence_gap_rows(
        dispatch_entries,
        stream="dispatch",
        window_start_ns=window.start_ns,
        window_end_ns=nominal_end_ns,
        sequence_by_entry_ns=dispatch_sequence_by_entry,
    )
    present_gaps = _cadence_gap_rows(
        present_entries,
        stream="present",
        window_start_ns=window.start_ns,
        window_end_ns=nominal_end_ns,
        sequence_by_entry_ns=present_sequence_by_entry,
    )
    worker_tick_gaps = _qpc_gap_rows(
        evidence_rows,
        record_type="worker_tick",
        stream="worker_tick",
    )
    control_wake_gaps = _qpc_gap_rows(
        evidence_rows,
        record_type="control_wake",
        stream="control_wake",
    )
    clock_pairs = [
        dict(row) for row in evidence_rows if row.get("record_type") == "clock_pair"
    ]
    health_callbacks = [
        dict(row)
        for row in evidence_rows
        if row.get("record_type") == "health_callback"
    ]
    wake_rows = [
        dict(row)
        for row in evidence_rows
        if row.get("record_type") not in {"clock_pair", "health_callback"}
    ]
    wake_reason_counts = dict(
        Counter(
            str(row.get("reason", row.get("record_type", "unknown")))
            for row in wake_rows
        )
    )
    parsed_accounting = _accounting_values(harness.end_accounting)
    accounting = parsed_accounting or {
        "worker_tick_published_count": 0,
        "pending_tick_superseded_count": 0,
        "main_dispatch_count": 0,
        "stale_tick_dropped_count": 0,
        "pending_tick_present_at_window_end": 0,
        "latest_only_accounting_ok": False,
        "period_skipped_count": 0,
        "blit_skipped_no_new_frame": 0,
    }
    camera = harness.source.camera_summary()
    thread_ids = harness.thread_ids_payload()
    pins_unchanged = source_pins_unchanged(
        harness.start_pins,
        harness.end_pins,
    )
    pins_complete = source_pins_complete(harness.end_pins)
    functional_ok = bool(
        normal_teardown_evidence_ok(
            teardown,
            native_clock=harness.clock_mode == "high_resolution",
        )
        and camera["camera_read_error_count"] == 0
        and camera["camera_sequence_regression_count"] == 0
    )
    non_accounting_evidence_ok = bool(
        pins_unchanged and pins_complete and thread_ids_valid(thread_ids)
    )
    # 提示が1度も観測されない計測は cadence が黙って 0 になり、実行の
    # 証拠としては壊れている。ここでは性能基準（performance_reference）
    # ではなく機能判定に落とす。cadence_passed には入れないので、
    # 基準未満でも素直な実行は通る。
    evidence_ok = bool(
        present.count >= 2 or config.run_class is not RunClass.NATIVE_PRODUCTION
    ) and evidence_gate_passes(
        config.run_class.value,
        functional_ok=functional_ok,
        non_accounting_evidence_ok=non_accounting_evidence_ok,
        latest_only_accounting_ok=bool(accounting["latest_only_accounting_ok"]),
    )
    decision = decide_acceptance(
        run_class=config.run_class.value,
        clock_mode=harness.clock_mode,
        contract=contract,
        dispatch=dispatch,
        present=present,
        measurement_overshoot_ok=overshoot_ok,
        functional_ok=functional_ok,
        evidence_ok=evidence_ok,
        period_skipped_count=int(accounting["period_skipped_count"]),
        blit_skipped_no_new_frame=int(accounting["blit_skipped_no_new_frame"]),
    )
    status = "passed" if decision.test_passed else "failed"
    if config.run_class is RunClass.NATIVE_PRODUCTION and not decision.accepted:
        status = "red"
    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "test_id": config.test_id,
        "phase": config.phase.value,
        "configured_fps": config.configured_fps,
        "diagnostic_rate_hz": config.diagnostic_rate_hz,
        "run_class": config.run_class.value,
        "diagnostic_only": config.run_class is RunClass.DIAGNOSTIC,
        "clock_mode": harness.clock_mode,
        "dispatch_oracle": harness.dispatch_oracle,
        "target_mean_min_hz": contract.target_mean_min_hz,
        "target_mean_max_hz": contract.target_mean_max_hz,
        "target_p1_min_hz": contract.target_p1_min_hz,
        "cap_tolerance_hz": contract.cap_tolerance_hz,
        "cap_tolerance_semantics": contract.cap_tolerance_semantics,
        "measurement_start_ns": window.start_ns,
        "nominal_window_end_ns": nominal_end_ns,
        "actual_window_end_ns": window.end_ns,
        "measurement_overshoot_ns": overshoot_ns,
        "measurement_overshoot_limit_ns": overshoot_limit_ns,
        "measurement_overshoot_ok": overshoot_ok,
        "dispatch_summary": asdict(dispatch),
        "present_summary": asdict(present),
        "production_accepted": decision.production_accepted,
        "functional_accepted": decision.functional_accepted,
        "test_passed": decision.test_passed,
        "accepted": decision.accepted,
        "performance_reference": asdict(decision.performance_reference),
        "period_skipped_count": decision.period_skipped_count,
        "blit_skipped_no_new_frame": decision.blit_skipped_no_new_frame,
        # Derived from the dispatch interval stream, deliberately independent of
        # blit_skipped_no_new_frame so the two remain separate measurements.
        # N in-window events always yield exactly N-1 adjacent pairs, so the
        # event count is the only denominator that reports a lost dispatch.
        "suppressed_blit_interval_count": max(
            0,
            round(config.configured_fps * config.measurement_s) - dispatch.count,
        ),
        "worker_tick_published_count": accounting["worker_tick_published_count"],
        "pending_tick_superseded_count": accounting["pending_tick_superseded_count"],
        "main_dispatch_count": accounting["main_dispatch_count"],
        "stale_tick_dropped_count": accounting["stale_tick_dropped_count"],
        "pending_tick_present_at_window_end": accounting[
            "pending_tick_present_at_window_end"
        ],
        "latest_only_accounting_ok": accounting["latest_only_accounting_ok"],
        "wake_reason_counts": wake_reason_counts,
        "gap_threshold_ns": GAP_THRESHOLD_NS,
        "dispatch_gaps": dispatch_gaps,
        "present_gaps": present_gaps,
        "worker_tick_gaps": worker_tick_gaps,
        "control_wake_gaps": control_wake_gaps,
        **camera,
        "thread_ids": thread_ids,
        "source_pins_unchanged": pins_unchanged,
        "source_pins_complete": pins_complete,
        "artifact_manifest_path": ARTIFACT_MANIFEST_FILE,
        "physical_camera_evidence": False,
        "physical_unique_frame_claim": False,
        "compositor_or_scanout_proof": False,
        "status": status,
        "teardown": dict(teardown),
        "start_accounting_snapshot": harness.start_snapshot,
        "end_accounting_snapshot": harness.end_snapshot,
        "end_accounting": dict(harness.end_accounting),
    }
    artifacts: dict[str, Any] = {
        "wakes": wake_rows,
        "clock_pairs": clock_pairs,
        "present_intervals": present_interval_rows,
        "dispatch_intervals": dispatch_interval_rows,
        "camera_reads": [asdict(row) for row in harness.source.camera_records()],
        "health": [*harness.health_rows, *health_callbacks],
        "teardown": dict(teardown),
        "thread_ids": thread_ids,
        "source_pins": {
            "schema_version": 1,
            "start": _source_pin_rows(harness.start_pins),
            "end": _source_pin_rows(harness.end_pins),
            "unchanged": pins_unchanged,
            "complete": pins_complete,
        },
    }
    return report, artifacts


def _write_artifacts(
    writer: EvidenceWriter,
    report: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> None:
    writer.write_jsonl("wakes.jsonl", artifacts["wakes"])
    writer.write_jsonl("clock_pairs.jsonl", artifacts["clock_pairs"])
    writer.write_jsonl("present_intervals.jsonl", artifacts["present_intervals"])
    writer.write_jsonl("dispatch_intervals.jsonl", artifacts["dispatch_intervals"])
    writer.write_jsonl("camera_reads.jsonl", artifacts["camera_reads"])
    writer.write_jsonl("health.jsonl", artifacts["health"])
    writer.write_json("teardown.json", artifacts["teardown"])
    writer.write_json("thread_ids.json", artifacts["thread_ids"])
    writer.write_json("source_pins.json", artifacts["source_pins"])
    writer.write_json("report.json", report)
    write_artifact_manifest(writer.directory)
    verify_artifact_manifest(writer.directory)


class _TeardownDependencies:
    stats_dirty = False
    stop_waited = 0
    sender: Any = None

    def notify_closing(self) -> None:
        return None

    def cancel_watch(self) -> None:
        return None

    def shutdown(self, *_args: Any) -> bool:
        return True

    def stop_keyboard(self) -> None:
        return None


class _ZeroHeightPane:
    def winfo_height(self) -> int:
        return 0


def _pending_after_ids(root: Any) -> list[str]:
    import tkinter as tk

    try:
        return [str(after_id) for after_id in root.tk.call("after", "info")]
    except (tk.TclError, TypeError, ValueError):
        return []


def _clock_worker_alive(clock: Any) -> bool:
    runtime = getattr(clock, "_runtime", None)
    worker = getattr(clock, "_worker", None)
    if worker is None and runtime is not None:
        worker = getattr(runtime, "worker", None)
    if worker is None and runtime is not None:
        worker = getattr(runtime, "_thread", None)
    return bool(
        worker is not None
        and callable(getattr(worker, "is_alive", None))
        and worker.is_alive()
    )


def _zero_camera_evidence() -> dict[str, Any]:
    return {
        "camera_read_count": 0,
        "camera_frame_present_count": 0,
        "camera_none_count": 0,
        "camera_read_error_count": 0,
        "camera_unique_sequence_count": 0,
        "camera_duplicate_sequence_count": 0,
        "camera_sequence_regression_count": 0,
        "camera_last_sequence": 0,
        "post_teardown_camera_read_count": 0,
        "camera_thread_claim": "not_applicable_synthetic_source",
        "capture_device": dict(SYNTHETIC_CAPTURE_DEVICE),
    }


def _write_teardown_result(
    config: E2EConfig,
    writer: EvidenceWriter,
    *,
    start_pins: Mapping[str, FilePin],
    end_pins: Mapping[str, FilePin],
    teardown: Mapping[str, Any],
    camera: Mapping[str, Any],
    thread_ids: Mapping[str, int | str | None],
    health: Mapping[str, Any],
) -> dict[str, Any]:
    pins_unchanged = source_pins_unchanged(start_pins, end_pins)
    pins_complete = source_pins_complete(end_pins)
    pending_proven = bool(teardown.get("pending_teardown_proven"))
    functional_ok = bool(
        teardown.get("stop_result") == "stopped"
        and teardown.get("root_alive_while_pending") is True
        and teardown.get("root_destroyed_after_stop") is True
        and teardown.get("preview_worker_alive") is False
        and teardown.get("synthetic_source_alive") is False
        and teardown.get("post_teardown_camera_read_count") == 0
        and teardown.get("pending_after_ids") == []
        and teardown.get("window_class_destroyed") is True
        and teardown.get("window_class_unregistered") is True
    )
    evidence_ok = bool(
        pending_proven
        and functional_ok
        and pins_unchanged
        and pins_complete
        and thread_ids_valid(thread_ids)
    )
    contract = cadence_contract(config.configured_fps)
    camera_rows = list(camera.get("records", []))
    report_camera = {key: value for key, value in camera.items() if key != "records"}
    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "test_id": config.test_id,
        "phase": config.phase.value,
        "configured_fps": config.configured_fps,
        "diagnostic_rate_hz": config.diagnostic_rate_hz,
        "run_class": config.run_class.value,
        "diagnostic_only": False,
        "clock_mode": str(teardown.get("clock_mode", "unavailable")),
        "target_mean_min_hz": contract.target_mean_min_hz,
        "target_mean_max_hz": contract.target_mean_max_hz,
        "target_p1_min_hz": contract.target_p1_min_hz,
        "cap_tolerance_hz": contract.cap_tolerance_hz,
        "cap_tolerance_semantics": contract.cap_tolerance_semantics,
        "measurement_start_ns": 0,
        "nominal_window_end_ns": 0,
        "actual_window_end_ns": 0,
        "measurement_overshoot_ns": 0,
        "measurement_overshoot_limit_ns": 0,
        "measurement_overshoot_ok": False,
        "dispatch_summary": asdict(CadenceResult(0, 0.0, 0.0)),
        "present_summary": asdict(CadenceResult(0, 0.0, 0.0)),
        "production_accepted": False,
        "functional_accepted": functional_ok,
        "test_passed": evidence_ok,
        "accepted": False,
        "performance_reference": asdict(PerformanceReference(within_reference=False)),
        "period_skipped_count": 0,
        "blit_skipped_no_new_frame": 0,
        "suppressed_blit_interval_count": 0,
        "worker_tick_published_count": 0,
        "pending_tick_superseded_count": 0,
        "main_dispatch_count": 0,
        "stale_tick_dropped_count": 0,
        "pending_tick_present_at_window_end": 0,
        "latest_only_accounting_ok": False,
        "wake_reason_counts": {},
        "gap_threshold_ns": GAP_THRESHOLD_NS,
        "dispatch_gaps": [],
        "present_gaps": [],
        "worker_tick_gaps": [],
        "control_wake_gaps": [],
        "thread_ids": dict(thread_ids),
        "source_pins_unchanged": pins_unchanged,
        "source_pins_complete": pins_complete,
        "artifact_manifest_path": ARTIFACT_MANIFEST_FILE,
        "physical_camera_evidence": False,
        "physical_unique_frame_claim": False,
        "compositor_or_scanout_proof": False,
        "status": "passed" if evidence_ok else "failed",
        "teardown": dict(teardown),
    }
    report.update(report_camera)
    source_pins = {
        "schema_version": 1,
        "start": _source_pin_rows(start_pins),
        "end": _source_pin_rows(end_pins),
        "unchanged": pins_unchanged,
        "complete": pins_complete,
    }
    _write_artifacts(
        writer,
        report,
        {
            "wakes": [],
            "clock_pairs": [],
            "present_intervals": [],
            "dispatch_intervals": [],
            "camera_reads": camera_rows,
            "health": [dict(health)],
            "teardown": dict(teardown),
            "thread_ids": dict(thread_ids),
            "source_pins": source_pins,
        },
    )
    return report


def _unavailable_teardown_result(
    config: E2EConfig,
    writer: EvidenceWriter,
    start_pins: Mapping[str, FilePin],
) -> dict[str, Any]:
    teardown = {
        "stop_result": "unavailable",
        "root_alive_while_pending": None,
        "root_destroyed_after_stop": False,
        "preview_worker_alive": False,
        "synthetic_source_alive": False,
        "post_teardown_camera_read_count": 0,
        "pending_after_ids": [],
        "window_class_destroyed": None,
        "window_class_unregistered": None,
        "pending_teardown_proven": False,
        "phase_order": ["preview_clock_unavailable"],
        "preview_clock_available": False,
    }
    return _write_teardown_result(
        config,
        writer,
        start_pins=start_pins,
        end_pins=start_pins,
        teardown=teardown,
        camera=_zero_camera_evidence(),
        thread_ids={
            "schema_version": 1,
            "main": threading.get_native_id(),
            "window_owner": threading.get_native_id(),
            "preview_worker": 0,
            "camera_source": 0,
            "camera_thread": None,
            "camera_thread_claim": "not_applicable_synthetic_source",
        },
        health={"record_type": "teardown_unavailable"},
    )


def _run_teardown_probe(
    config: E2EConfig,
    writer: EvidenceWriter,
) -> dict[str, Any]:
    import tkinter as tk

    start_pins = _source_pins()
    try:
        import Window as window_module
        from GuiAssets import CaptureArea
        from ui import preview_clock
    except ImportError:
        return _unavailable_teardown_result(config, writer, start_pins)

    root = tk.Tk()
    root.geometry("640x360+0+0")
    setattr(
        root,
        "is_use_left_stick_mouse",
        tk.BooleanVar(master=root, value=True),
    )
    setattr(
        root,
        "is_use_right_stick_mouse",
        tk.BooleanVar(master=root, value=True),
    )
    is_show = tk.BooleanVar(master=root, value=True)
    source = SyntheticFrameSource(config.source_hz)
    area = CaptureArea(
        source,
        config.configured_fps,
        is_show,
        None,
        master=root,
        show_width=SOURCE_WIDTH,
        show_height=SOURCE_HEIGHT,
        take_stick_log=False,
    )
    dependencies = _TeardownDependencies()
    app = object.__new__(window_module.PokeControllerApp)
    app.root = root
    app.mainwindow = root
    app.preview = area
    app.camera = source
    app.runner = dependencies
    app.serial = dependencies
    app.serial_service = dependencies
    app.menu = None
    app.logArea = root
    app.log_pane = _ZeroHeightPane()
    app.settings = object()
    app.command_stats = None
    app.profile = "preview-fps-e2e"
    app.audio_service = dependencies
    app.log_pane = _ZeroHeightPane()
    app._closing = False
    app._exit_requested = False
    app._exit_phase = "idle"
    app._exit_retry_after_id = None
    app._display_after_id = None
    app._sash_after_id = None
    app._save_settings = lambda: None
    app._remember_geometry = lambda: None
    app._cancel_player_lamp_patrol = lambda: None
    app._stop_meter = lambda: None
    app.closingController = lambda: None
    source.start()
    clock = _start_capture_and_get_clock(area)
    if clock is None:
        root.destroy()
        source.stop()
        raise HarnessStateError("CaptureArea did not create its owned PreviewClock")
    phase_order: list[str] = []
    stop_call_count = 0
    original_stop = clock.stop
    stop_result_type = preview_clock.StopResult

    def stop_wrapper() -> Any:
        nonlocal stop_call_count
        stop_call_count += 1
        result = stop_result_type.PENDING if stop_call_count == 1 else original_stop()
        phase_order.append(f"preview_stop:{result.value}")
        return result

    clock.stop = stop_wrapper
    original_askyesno = window_module.tkmsg.askyesno
    window_module.tkmsg.askyesno = lambda *_args, **_kwargs: True
    root_alive_while_pending = False
    root_destroyed_after_stop = False
    preview_worker_alive = False
    source_alive_after_app = True
    pending_before_retry: list[str] = []
    retry_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    try:
        app.exit()
        root_alive_while_pending = _root_exists(root)
        pending_before_retry = _pending_after_ids(root)
        retry_raw = getattr(app, "_exit_retry_after_id", None)
        retry_id = None if retry_raw is None else str(retry_raw)
        if root_alive_while_pending and retry_id is not None:
            try:
                root.after_cancel(retry_id)
            except (tk.TclError, ValueError):
                pass
            root.after(0, app._continue_exit)
            root.update()
        root_destroyed_after_stop = _root_is_destroyed(root)
        preview_worker_alive = _clock_worker_alive(clock)
        source_alive_after_app = source.is_alive()
        phase_order.append("scheduled_retry_observed")
    except (
        AttributeError,
        HarnessStateError,
        tk.TclError,
        TypeError,
        ValueError,
    ) as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
    finally:
        window_module.tkmsg.askyesno = original_askyesno
        if clock.health_snapshot().get("running", False):
            original_stop()
        source.stop()
        if _root_exists(root):
            root.destroy()
    health = dict(clock.health_snapshot())
    camera = source.camera_summary()
    teardown = {
        "stop_result": (
            "stopped" if stop_call_count >= 2 else "pending_or_unavailable"
        ),
        "root_alive_while_pending": root_alive_while_pending,
        "root_destroyed_after_stop": root_destroyed_after_stop,
        "preview_worker_alive": preview_worker_alive,
        "synthetic_source_alive": source_alive_after_app,
        "post_teardown_camera_read_count": camera["post_teardown_camera_read_count"],
        "pending_after_ids": _pending_after_ids(root),
        "window_class_destroyed": bool(health.get("window_class_destroyed", False)),
        "window_class_unregistered": bool(
            health.get("window_class_unregistered", False)
        ),
        "pending_teardown_proven": bool(
            stop_call_count >= 2
            and root_alive_while_pending
            and root_destroyed_after_stop
            and not preview_worker_alive
        ),
        "phase_order": phase_order,
        "preview_clock_available": True,
        "pending_after_ids_before_retry": pending_before_retry,
        "scheduled_retry_id": retry_id,
        "error_type": error_type,
        "error_message": error_message,
    }
    thread_ids: dict[str, int | str | None] = {
        "schema_version": 1,
        "main": threading.get_native_id(),
        "window_owner": threading.get_native_id(),
        "preview_worker": int(health.get("worker_thread_native_id", 0)),
        "camera_source": source.producer_thread_native_id(),
        "camera_thread": None,
        "camera_thread_claim": "not_applicable_synthetic_source",
    }
    camera_rows = [asdict(row) for row in source.camera_records()]
    return _write_teardown_result(
        config,
        writer,
        start_pins=start_pins,
        end_pins=_source_pins(),
        teardown=teardown,
        camera={**camera, "records": camera_rows},
        thread_ids=thread_ids,
        health=health,
    )


def _run_measurement(
    config: E2EConfig,
    writer: EvidenceWriter,
) -> dict[str, Any]:
    match config.phase:
        case RunPhase.TEARDOWN:
            return _run_teardown_probe(config, writer)
        case RunPhase.PRODUCTION | RunPhase.FALLBACK | RunPhase.DIAGNOSTIC:
            pass
        case unreachable:
            assert_never(unreachable)
    import tkinter as tk

    harness = RealCaptureHarness(config)
    teardown: dict[str, Any]
    try:
        harness.start()
        window = harness.run_measurement()
        teardown = harness.close()
        harness.end_pins = _source_pins()
        evidence_rows = _drain_clock_evidence(harness.clock)
        report, artifacts = _build_report(
            config,
            harness,
            window,
            teardown,
            evidence_rows,
        )
        _write_artifacts(writer, report, artifacts)
        return report
    except (
        AssertionError,
        AttributeError,
        HarnessStateError,
        ImportError,
        OSError,
        TypeError,
        ValueError,
        tk.TclError,
    ) as exc:
        teardown = harness.close()
        harness.end_pins = _source_pins()
        pins = harness.end_pins
        report = _minimal_failure_report(
            config,
            {},
            start_pins=harness.start_pins,
            end_pins=pins,
        )
        camera_summary = (
            _zero_camera_evidence()
            if harness.source is None
            else harness.source.camera_summary()
        )
        report.update(
            {
                "clock_mode": harness.clock_mode,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "teardown": teardown,
                "thread_ids": harness.thread_ids_payload(),
                **camera_summary,
            }
        )
        writer.write_required_empty_evidence()
        writer.write_json("teardown.json", teardown)
        writer.write_json(
            "source_pins.json",
            {
                "schema_version": 1,
                "start": _source_pin_rows(harness.start_pins),
                "end": _source_pin_rows(pins),
                "unchanged": source_pins_unchanged(
                    harness.start_pins,
                    pins,
                ),
                "complete": source_pins_complete(pins),
            },
        )
        _write_artifacts(
            writer,
            report,
            {
                "wakes": [],
                "clock_pairs": [],
                "present_intervals": [],
                "dispatch_intervals": [],
                "camera_reads": [],
                "health": [],
                "teardown": teardown,
                "thread_ids": harness.thread_ids_payload(),
                "source_pins": {
                    "schema_version": 1,
                    "start": _source_pin_rows(harness.start_pins),
                    "end": _source_pin_rows(pins),
                    "unchanged": source_pins_unchanged(
                        harness.start_pins,
                        pins,
                    ),
                    "complete": source_pins_complete(pins),
                },
            },
        )
        raise


def _parse_required(name: str) -> str:
    raw = os.environ.get(name)
    if raw is None or not raw or raw != raw.strip():
        raise ConfigError(name, "is required and must be non-empty")
    return raw


def _parse_int(name: str, raw: str) -> int:
    if not raw.isdigit():
        raise ConfigError(name, "must be a base-10 integer")
    return int(raw)


def _parse_float(name: str, raw: str, *, positive: bool = True) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(name, "must be a finite number") from exc
    if not math.isfinite(value):
        raise ConfigError(name, "must be a finite number")
    if (positive and value <= 0.0) or (not positive and value < 0.0):
        qualifier = "positive" if positive else "nonnegative"
        raise ConfigError(name, f"must be a finite {qualifier} number")
    return value


def _parse_enum[T: StrEnum](name: str, raw: str, enum_type: type[T]) -> T:
    try:
        return enum_type(raw.lower())
    except ValueError as exc:
        raise ConfigError(name, f"unknown value: {raw}") from exc


def _gate_is_enabled() -> bool | None:
    raw = os.environ.get("POKECON_RUN_PREVIEW_FPS_E2E")
    if raw is None or raw == "0":
        return None
    if raw != "1":
        raise ConfigError("POKECON_RUN_PREVIEW_FPS_E2E", "must be exactly 0 or 1")
    return True


def load_config(expected_test_id: str | None = None) -> E2EConfig | None:
    if _gate_is_enabled() is None:
        return None
    phase = _parse_enum(
        "POKECON_FPS_PHASE", _parse_required("POKECON_FPS_PHASE"), RunPhase
    )
    run_class = _parse_enum(
        "POKECON_FPS_RUN_CLASS",
        _parse_required("POKECON_FPS_RUN_CLASS"),
        RunClass,
    )
    test_id = _parse_required("POKECON_FPS_TEST_ID")
    if expected_test_id is not None and test_id != expected_test_id:
        raise ConfigError("POKECON_FPS_TEST_ID", "does not match collected test")
    expected_contract = TEST_CONTRACTS.get(test_id)
    if expected_contract is None:
        raise ConfigError("POKECON_FPS_TEST_ID", f"unknown test: {test_id}")
    expected_phase, expected_run_class = expected_contract
    if phase is not expected_phase:
        raise ConfigError("POKECON_FPS_PHASE", "does not match test id")
    if run_class is not expected_run_class:
        raise ConfigError("POKECON_FPS_RUN_CLASS", "does not match test id")
    configured_fps = _parse_int(
        "POKECON_FPS_CONFIGURED_FPS",
        _parse_required("POKECON_FPS_CONFIGURED_FPS"),
    )
    if configured_fps not in PRODUCTION_FPS_VALUES:
        raise ConfigError(
            "POKECON_FPS_CONFIGURED_FPS",
            f"must be one of {PRODUCTION_FPS_VALUES}",
        )
    diagnostic_raw = os.environ.get("POKECON_FPS_DIAGNOSTIC_RATE_HZ", "")
    diagnostic_rate_hz = (
        None
        if not diagnostic_raw
        else _parse_float(
            "POKECON_FPS_DIAGNOSTIC_RATE_HZ",
            diagnostic_raw,
        )
    )
    if run_class is RunClass.DIAGNOSTIC:
        if diagnostic_rate_hz not in DIAGNOSTIC_RATES_HZ:
            raise ConfigError(
                "POKECON_FPS_DIAGNOSTIC_RATE_HZ",
                f"must be one of {DIAGNOSTIC_RATES_HZ}",
            )
        if configured_fps != 60:
            raise ConfigError(
                "POKECON_FPS_CONFIGURED_FPS",
                "diagnostic runs preserve configured FPS 60",
            )
    elif diagnostic_rate_hz is not None:
        raise ConfigError(
            "POKECON_FPS_DIAGNOSTIC_RATE_HZ",
            "is only valid for diagnostic runs",
        )
    source_hz = _parse_int(
        "POKECON_FPS_SOURCE_HZ",
        _parse_required("POKECON_FPS_SOURCE_HZ"),
    )
    if source_hz != SOURCE_HZ:
        raise ConfigError("POKECON_FPS_SOURCE_HZ", "must be exactly 180")
    positive_windows = phase is not RunPhase.TEARDOWN
    warmup_s = _parse_float(
        "POKECON_FPS_WARMUP_S",
        _parse_required("POKECON_FPS_WARMUP_S"),
        positive=positive_windows,
    )
    measurement_s = _parse_float(
        "POKECON_FPS_MEASUREMENT_S",
        _parse_required("POKECON_FPS_MEASUREMENT_S"),
        positive=positive_windows,
    )
    watchdog_timeout_s = _parse_float(
        "POKECON_FPS_WATCHDOG_TIMEOUT_S",
        _parse_required("POKECON_FPS_WATCHDOG_TIMEOUT_S"),
    )
    child_mode = _parse_enum(
        "POKECON_FPS_CHILD_MODE",
        _parse_required("POKECON_FPS_CHILD_MODE"),
        ChildMode,
    )
    evidence_dir = Path(_parse_required("POKECON_FPS_EVIDENCE_DIR"))
    if not evidence_dir.is_absolute():
        raise ConfigError("POKECON_FPS_EVIDENCE_DIR", "must be absolute")
    evidence_dir = evidence_dir.resolve()
    if evidence_dir == REPO_ROOT or REPO_ROOT in evidence_dir.parents:
        raise ConfigError(
            "POKECON_FPS_EVIDENCE_DIR",
            "must be outside the repository",
        )
    return E2EConfig(
        phase=phase,
        run_class=run_class,
        configured_fps=configured_fps,
        diagnostic_rate_hz=diagnostic_rate_hz,
        warmup_s=warmup_s,
        measurement_s=measurement_s,
        evidence_dir=evidence_dir,
        source_hz=source_hz,
        child_mode=child_mode,
        test_id=test_id,
        watchdog_timeout_s=watchdog_timeout_s,
    )


def claim_evidence_directory(directory: Path) -> None:
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError as exc:
        raise ConfigError(
            "POKECON_FPS_EVIDENCE_DIR",
            "must not already exist",
        ) from exc


def _new_claim_token() -> ClaimToken:
    return ClaimToken(secrets.token_urlsafe(32))


def _validate_entry_mode(config: E2EConfig, entry: Literal["parent", "child"]) -> None:
    expected = ChildMode.NORMAL if entry == "parent" else ChildMode.CHILD
    if config.child_mode is not expected:
        raise ConfigError(
            "POKECON_FPS_CHILD_MODE",
            f"{entry} entry requires {expected.value}",
        )


def _skip_without_gate() -> NoReturn:
    import pytest

    pytest.skip("set POKECON_RUN_PREVIEW_FPS_E2E=1 to run the real-Tk FPS E2E")


def _native_platform_available() -> bool:
    return os.name == "nt"


def _skip_non_windows_native(config: E2EConfig) -> None:
    if _native_platform_available() or config.run_class is RunClass.FALLBACK_FUNCTIONAL:
        return
    import pytest

    pytest.skip("native preview FPS runs require Windows and never substitute fallback")


def _parse_claim_token() -> ClaimToken:
    return ClaimToken(_parse_required(CLAIM_TOKEN_ENV))


def admit_child_run(config: E2EConfig) -> tuple[EvidenceWriter, ChildOwnershipPayload]:
    if not config.evidence_dir.is_dir():
        raise ConfigError(
            "POKECON_FPS_EVIDENCE_DIR",
            "must be claimed by parent before child entry",
        )
    token = _parse_claim_token()
    handoff_path = config.evidence_dir / PARENT_HANDOFF_FILE
    try:
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(PARENT_HANDOFF_FILE, "is unreadable or invalid") from exc
    if not isinstance(handoff, dict):
        raise ConfigError(PARENT_HANDOFF_FILE, "must contain an object")
    if handoff.get("schema_version") != 1:
        raise ConfigError(PARENT_HANDOFF_FILE, "unsupported schema version")
    if handoff.get("claim_token") != token.value:
        raise ConfigError(CLAIM_TOKEN_ENV, "does not match parent handoff")
    if handoff.get("execution_contract") != config.execution_contract():
        raise ConfigError(PARENT_HANDOFF_FILE, "execution contract mismatch")
    if handoff.get("source_pins") != _source_pin_rows(_source_pins()):
        raise ConfigError("source_pins", "parent handoff does not match sources")
    writer = EvidenceWriter(config.evidence_dir, role="child")
    return writer, writer.claim_child_ownership(config, token)


def _run_child_process(
    config: E2EConfig,
    token: ClaimToken,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["POKECON_FPS_CHILD_MODE"] = ChildMode.CHILD.value
    env["POKECON_FPS_TEST_ID"] = config.test_id
    env["POKECON_FPS_PHASE"] = config.phase.value
    env[CLAIM_TOKEN_ENV] = token.value
    log_path = config.evidence_dir / f"{config.test_id}.child.log"
    started_ns = time.perf_counter_ns()
    timed_out = False
    escalation: str | None = None
    with log_path.open("w", encoding="utf-8") as child_log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=child_log,
            stderr=subprocess.STDOUT,
        )
        try:
            process.wait(timeout=config.watchdog_timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=1.0)
                escalation = "terminate"
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
                escalation = "kill"
    return {
        "pid": process.pid,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "escalation": escalation,
        "elapsed_ns": time.perf_counter_ns() - started_ns,
        "timeout_s": config.watchdog_timeout_s,
    }


def _minimal_failure_report(
    config: E2EConfig,
    process_result: Mapping[str, Any],
    *,
    start_pins: Mapping[str, FilePin],
    end_pins: Mapping[str, FilePin],
) -> dict[str, Any]:
    contract = cadence_contract(config.configured_fps)
    camera = _zero_camera_evidence()
    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "test_id": config.test_id,
        "phase": config.phase.value,
        "configured_fps": config.configured_fps,
        "diagnostic_rate_hz": config.diagnostic_rate_hz,
        "run_class": config.run_class.value,
        "diagnostic_only": config.run_class is RunClass.DIAGNOSTIC,
        "clock_mode": "unavailable",
        "target_mean_min_hz": contract.target_mean_min_hz,
        "target_mean_max_hz": contract.target_mean_max_hz,
        "target_p1_min_hz": contract.target_p1_min_hz,
        "cap_tolerance_hz": contract.cap_tolerance_hz,
        "cap_tolerance_semantics": contract.cap_tolerance_semantics,
        "measurement_start_ns": 0,
        "nominal_window_end_ns": 0,
        "actual_window_end_ns": 0,
        "measurement_overshoot_ns": 0,
        "measurement_overshoot_limit_ns": measurement_overshoot(
            0, 0, 0.0, config.configured_fps
        )[1],
        "measurement_overshoot_ok": False,
        "dispatch_summary": asdict(CadenceResult(0, 0.0, 0.0)),
        "present_summary": asdict(CadenceResult(0, 0.0, 0.0)),
        "production_accepted": False,
        "functional_accepted": False,
        "test_passed": False,
        "accepted": False,
        "performance_reference": asdict(PerformanceReference(within_reference=False)),
        "period_skipped_count": 0,
        "blit_skipped_no_new_frame": 0,
        "suppressed_blit_interval_count": 0,
        "worker_tick_published_count": 0,
        "pending_tick_superseded_count": 0,
        "main_dispatch_count": 0,
        "stale_tick_dropped_count": 0,
        "pending_tick_present_at_window_end": 0,
        "latest_only_accounting_ok": False,
        "wake_reason_counts": {},
        "gap_threshold_ns": GAP_THRESHOLD_NS,
        "dispatch_gaps": [],
        "present_gaps": [],
        "worker_tick_gaps": [],
        "control_wake_gaps": [],
        **camera,
        "thread_ids": {
            "schema_version": 1,
            "main": 0,
            "window_owner": 0,
            "preview_worker": 0,
            "camera_source": 0,
            "camera_thread": None,
            "camera_thread_claim": "not_applicable_synthetic_source",
        },
        "source_pins_unchanged": source_pins_unchanged(start_pins, end_pins),
        "source_pins_complete": source_pins_complete(end_pins),
        "artifact_manifest_path": ARTIFACT_MANIFEST_FILE,
        "physical_camera_evidence": False,
        "physical_unique_frame_claim": False,
        "compositor_or_scanout_proof": False,
        "status": "child-failed",
        "watchdog": dict(process_result),
    }
    return report


def _ensure_parent_artifacts(
    config: E2EConfig,
    writer: EvidenceWriter,
    process_result: Mapping[str, Any],
) -> None:
    end_pins = _source_pins()
    try:
        handoff = json.loads(
            (config.evidence_dir / PARENT_HANDOFF_FILE).read_text(encoding="utf-8")
        )
        start_pins = _source_pins_from_rows(handoff.get("source_pins"))
    except (ConfigError, OSError, json.JSONDecodeError, AttributeError):
        start_pins = _missing_source_pins()
    report_path = config.evidence_dir / "report.json"
    try:
        child_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        child_report = None
    report_schema_version = (
        child_report.get("report_schema_version")
        if isinstance(child_report, dict)
        else None
    )
    child_failed = bool(
        process_result.get("timed_out") is True or process_result.get("returncode") != 0
    )
    accepted = child_report.get("accepted") if isinstance(child_report, dict) else None
    production_accepted = (
        child_report.get("production_accepted")
        if isinstance(child_report, dict)
        else None
    )
    acceptance_consistent = bool(
        isinstance(accepted, bool)
        and isinstance(production_accepted, bool)
        and (not child_failed or (accepted is False and production_accepted is False))
    )
    complete_child_report = bool(
        isinstance(child_report, dict)
        and type(report_schema_version) is int
        and report_schema_version == REPORT_SCHEMA_VERSION
        and REQUIRED_REPORT_FIELDS <= child_report.keys()
        and child_report.get("artifact_manifest_path") == ARTIFACT_MANIFEST_FILE
        and acceptance_consistent
    )
    report_replaced = not complete_child_report
    if report_replaced:
        writer.write_json(
            "report.json",
            _minimal_failure_report(
                config,
                process_result,
                start_pins=start_pins,
                end_pins=end_pins,
            ),
        )
    for name in (
        "wakes.jsonl",
        "clock_pairs.jsonl",
        "present_intervals.jsonl",
        "dispatch_intervals.jsonl",
        "camera_reads.jsonl",
        "health.jsonl",
    ):
        if not (config.evidence_dir / name).is_file():
            (config.evidence_dir / name).write_text("", encoding="utf-8")
    if not (config.evidence_dir / "teardown.json").is_file():
        writer.write_json(
            "teardown.json",
            {
                "cleanup_status": "synthesized_by_parent",
                "child_killed": bool(process_result["timed_out"]),
                "pending_teardown_proven": False,
            },
        )
    if not (config.evidence_dir / "thread_ids.json").is_file():
        writer.write_json(
            "thread_ids.json",
            {
                "schema_version": 1,
                "main": 0,
                "window_owner": 0,
                "preview_worker": 0,
                "camera_source": 0,
                "camera_thread": None,
                "camera_thread_claim": "not_applicable_synthetic_source",
            },
        )
    if not (config.evidence_dir / "source_pins.json").is_file():
        writer.write_json(
            "source_pins.json",
            {
                "schema_version": 1,
                "start": _source_pin_rows(start_pins),
                "end": _source_pin_rows(end_pins),
                "unchanged": source_pins_unchanged(start_pins, end_pins),
                "complete": source_pins_complete(end_pins),
            },
        )
    if report_replaced or not (config.evidence_dir / ARTIFACT_MANIFEST_FILE).is_file():
        write_artifact_manifest(config.evidence_dir)
    verify_artifact_manifest(config.evidence_dir)


def _report_passed(config: E2EConfig, report: Mapping[str, Any]) -> bool:
    match config.phase:
        case RunPhase.TEARDOWN:
            return report.get("test_passed") is True and report.get("accepted") is False
        case RunPhase.PRODUCTION | RunPhase.FALLBACK | RunPhase.DIAGNOSTIC:
            pass
        case unreachable_phase:
            assert_never(unreachable_phase)
    match config.run_class:
        case RunClass.NATIVE_PRODUCTION:
            # Cadence is a recorded performance reference, not a pass/fail gate.
            return report.get("functional_accepted") is True
        case RunClass.FALLBACK_FUNCTIONAL | RunClass.DIAGNOSTIC:
            return report.get("test_passed") is True and report.get("accepted") is False
        case unreachable_run_class:
            assert_never(unreachable_run_class)


def _run_parent(
    config: E2EConfig,
    writer: EvidenceWriter,
    token: ClaimToken,
) -> None:
    result = _run_child_process(config, token)
    writer.write_environment(config)
    writer.write_json("watchdog.json", result)
    _ensure_parent_artifacts(config, writer, result)
    report_path = config.evidence_dir / "report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError("child report is missing or invalid") from exc
    if result["timed_out"] or result["returncode"] != 0:
        raise AssertionError(
            f"preview FPS E2E is RED; retained evidence: {config.evidence_dir}"
        )
    passed = _report_passed(config, report)
    if not passed:
        raise AssertionError(
            f"preview FPS report did not satisfy {config.run_class.value}: "
            f"{config.evidence_dir}"
        )


def run_gated_test(test_id: str) -> None:
    config = load_config(test_id)
    if config is None:
        _skip_without_gate()
    _validate_entry_mode(config, "parent")
    _skip_non_windows_native(config)
    claim_evidence_directory(config.evidence_dir)
    token = _new_claim_token()
    writer = EvidenceWriter(config.evidence_dir, role="parent")
    writer.write_environment(config)
    writer.write_parent_handoff(config, token)
    _run_parent(config, writer, token)


def _run_child_from_environment() -> int:
    config = load_config()
    if config is None:
        return 0
    _validate_entry_mode(config, "child")
    writer, ownership = admit_child_run(config)
    writer.write_child_environment(config, ownership)
    report = _run_measurement(config, writer)
    passed = _report_passed(config, report)
    if not passed:
        raise AssertionError(
            "production acceptance failed"
            if config.run_class is RunClass.NATIVE_PRODUCTION
            else "functional/diagnostic evidence failed"
        )
    return 0


class TimerCreateCall(NamedTuple):
    attributes: int | None
    name: str | None
    flags: int
    desired_access: int


@dataclass(frozen=True, slots=True)
class SetWaitableTimerCall:
    timer_handle: int
    due_100ns: int
    period_ms: int
    completion_routine: None
    completion_argument: None
    f_resume: bool


class FakeWin32Api:
    """Deterministic Win32 boundary used to run the real worker loop."""

    def __init__(
        self,
        *,
        qpc_frequency_hz: int = 10_000_000,
        qpc_values: list[int] | None = None,
        post_message_results: list[bool] | None = None,
        timer_creation_results: list[int | None] | None = None,
        set_timer_results: list[bool] | None = None,
        cancel_timer_results: list[bool] | None = None,
        set_event_results: list[bool] | None = None,
        destroy_window_results: list[bool] | None = None,
        unregister_class_results: list[bool] | None = None,
        wait_results: list[int] | None = None,
        auto_timer_on_start: bool | None = None,
    ) -> None:
        self.qpc_frequency_hz = qpc_frequency_hz
        self.qpc_values = list(qpc_values or [])
        self.post_message_results = list(post_message_results or [])
        self.timer_creation_results = list(timer_creation_results or [])
        self.set_timer_results = list(set_timer_results or [])
        self.cancel_timer_results = list(cancel_timer_results or [])
        self.set_event_results = list(set_event_results or [])
        self.destroy_window_results = list(destroy_window_results or [])
        self.unregister_class_results = list(unregister_class_results or [])
        self.timer_create_calls: list[TimerCreateCall] = []
        self.ordinary_timer_create_calls = 0
        self.event_create_calls: list[tuple[int | None, bool, bool, str | None]] = []
        self.set_waitable_timer_calls: list[SetWaitableTimerCall] = []
        self.cancel_waitable_timer_calls: list[int] = []
        self.wait_calls: list[tuple[tuple[int, ...], bool, int]] = []
        self.post_message_calls: list[tuple[int, int, int, int]] = []
        self.register_class_calls: list[tuple[str, int]] = []
        self.create_window_calls: list[tuple[str, int, int]] = []
        self.destroy_window_calls: list[int] = []
        self.unregister_class_calls: list[tuple[str, int]] = []
        self.close_handle_calls: list[int] = []
        self.set_event_calls: list[int] = []
        self.stop_signal_order: list[str] = []
        self.last_error = 0
        self.rearm_after_post_failure = True
        self._auto_timer_on_start = (
            bool(self.post_message_results and self.post_message_results[0] is False)
            if auto_timer_on_start is None
            else auto_timer_on_start
        )
        self._force_timer_once = False
        self._timer_armed = False
        self._auto_timer_consumed = False
        self._worker_exited = False
        self._next_handle = 100
        self._events: dict[int, bool] = {}
        self._stop_event_handle: int | None = None
        self._scripted_wait_results = deque(wait_results or [])
        self._condition = threading.Condition()
        self._qpc_index = 0
        self._module_handle = 1
        self.class_proc: Callable[..., int] | None = None
        self.last_wndclass: Any | None = None

    def _new_handle(self) -> int:
        handle = self._next_handle
        self._next_handle += 1
        return handle

    def query_performance_frequency(self) -> int:
        return self.qpc_frequency_hz

    def query_performance_counter(self) -> int:
        with self._condition:
            if self.qpc_values:
                return self.qpc_values.pop(0)
            self._qpc_index += 1
            step = max(1, self.qpc_frequency_hz // 60)
            return self._qpc_index * step

    def create_waitable_timer_ex(
        self,
        attributes: int | None,
        name: str | None,
        flags: int,
        desired_access: int,
    ) -> int:
        self.timer_create_calls.append(
            TimerCreateCall(attributes, name, flags, desired_access)
        )
        if flags != 0x00000002:
            self.ordinary_timer_create_calls += 1
            return 0
        if self.timer_creation_results:
            configured = self.timer_creation_results.pop(0)
            return int(configured or 0)
        return self._new_handle()

    def create_event(
        self,
        attributes: int | None,
        manual_reset: bool,
        initial_state: bool,
        name: str | None,
    ) -> int:
        self.event_create_calls.append((attributes, manual_reset, initial_state, name))
        handle = self._new_handle()
        if self._stop_event_handle is None:
            self._stop_event_handle = handle
        with self._condition:
            self._events[handle] = initial_state
        return handle

    def register_class_ex(
        self,
        class_name: str,
        instance: int,
        window_proc: Callable[..., int],
    ) -> int:
        self.register_class_calls.append((class_name, instance))
        self.class_proc = window_proc
        return 201

    def create_window_ex(self, class_name: str, instance: int, parent: int) -> int:
        self.create_window_calls.append((class_name, instance, parent))
        return 202

    def set_waitable_timer(
        self,
        handle: int,
        due_100ns: int,
        period_ms: int,
        completion_routine: None,
        completion_argument: None,
        resume: bool,
    ) -> bool:
        self.set_waitable_timer_calls.append(
            SetWaitableTimerCall(
                timer_handle=handle,
                due_100ns=due_100ns,
                period_ms=period_ms,
                completion_routine=completion_routine,
                completion_argument=completion_argument,
                f_resume=resume,
            )
        )
        if self.set_timer_results:
            result = self.set_timer_results.pop(0)
            if not result:
                self._worker_exited = True
                return False
        with self._condition:
            self._timer_armed = True
            self._condition.notify_all()
        return True

    def cancel_waitable_timer(self, handle: int) -> bool:
        self.cancel_waitable_timer_calls.append(handle)
        self.stop_signal_order.append("cancel_timer")
        if self.cancel_timer_results and not self.cancel_timer_results.pop(0):
            self.last_error = 6
            return False
        with self._condition:
            self._timer_armed = False
            self._condition.notify_all()
        return True

    def set_event(self, handle: int) -> bool:
        self.set_event_calls.append(handle)
        if handle == self._stop_event_handle:
            self.stop_signal_order.append("set_event")
        if self.set_event_results and not self.set_event_results.pop(0):
            self.last_error = 5
            return False
        with self._condition:
            self._events[handle] = True
            self._condition.notify_all()
        return True

    def event_is_set(self, handle: int) -> bool:
        with self._condition:
            return bool(self._events.get(handle, False))

    def wait_for_multiple_objects(
        self,
        handles: Sequence[int],
        wait_all: bool,
        timeout_ms: int,
    ) -> int:
        self.wait_calls.append((tuple(handles), wait_all, timeout_ms))
        with self._condition:
            if self._scripted_wait_results:
                return self._scripted_wait_results.popleft()
            while True:
                if self.event_is_set(handles[0]):
                    return 0
                if len(handles) > 1 and self.event_is_set(handles[1]):
                    return 1
                if self._force_timer_once:
                    self._force_timer_once = False
                    return 2
                if (
                    self._auto_timer_on_start
                    and not self._auto_timer_consumed
                    and self._timer_armed
                ):
                    self._auto_timer_consumed = True
                    return 2
                if timeout_ms != 0xFFFFFFFF:
                    self._condition.wait(timeout_ms / 1000.0)
                    return 258
                self._condition.wait(0.01)

    def post_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> bool:
        self.post_message_calls.append((hwnd, message, wparam, lparam))
        result = self.post_message_results.pop(0) if self.post_message_results else True
        if not result:
            self.last_error = 5
            self.rearm_after_post_failure = False
            self._worker_exited = True
        return result

    def destroy_window(self, hwnd: int) -> bool:
        self.destroy_window_calls.append(hwnd)
        if self.destroy_window_results:
            return self.destroy_window_results.pop(0)
        return True

    def unregister_class(self, class_name: str, instance: int) -> bool:
        self.unregister_class_calls.append((class_name, instance))
        if self.unregister_class_results:
            return self.unregister_class_results.pop(0)
        return True

    def close_handle(self, handle: int) -> bool:
        self.close_handle_calls.append(handle)
        with self._condition:
            self._events.pop(handle, None)
        return True

    def get_last_error(self) -> int:
        return self.last_error

    def get_module_handle(self, module_name: str | None) -> int:
        del module_name
        return self._module_handle

    def native_window_proc(self) -> Callable[..., int]:
        def window_proc(
            hwnd: int,
            message: int,
            wparam: int,
            lparam: int,
        ) -> int:
            del hwnd, wparam, lparam
            return 1 if message == 0x0081 else 0

        return window_proc

    def auto_run_one_timer_wake(self) -> None:
        with self._condition:
            deadline = time.monotonic() + 1.0
            while not self.set_waitable_timer_calls and not self._worker_exited:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return
                self._condition.wait(remaining)
            before = len(self.set_waitable_timer_calls)
            self._force_timer_once = True
            self._condition.notify_all()
            while (
                len(self.set_waitable_timer_calls) <= before and not self._worker_exited
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return
                self._condition.wait(remaining)


def build_runtime_for_test(api: FakeWin32Api) -> Any:
    from ui import preview_clock

    if api.post_message_results == [False]:
        api.post_message_results[:] = [True, False]
    return preview_clock._WindowsNativeRuntime(
        owner_thread_id=threading.get_native_id(),
        configured_fps=60,
        idle_interval_ms=200,
        api=api,
    )


def worker_context_for_test() -> Any:
    from ui import preview_clock

    return preview_clock._worker_context_for_test(FakeWin32Api())


if __name__ == "__main__":
    raise SystemExit(_run_child_from_environment())
