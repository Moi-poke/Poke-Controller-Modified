# Windows High Resolution Preview Clock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the host preview's production `after` pacing with a Windows QPC waitable-timer bridge that meets the configured FPS contract for 5, 15, 30, 45, and 60 FPS while keeping a functional-only portable fallback.

**Architecture:** A new `SerialController/ui/preview_clock.py` owns a Tk-main-thread facade, a message-only window, one daemon worker, a high-resolution one-shot waitable timer, a latest-only mailbox, health recovery, and an exclusive `after` fallback. `CaptureArea` keeps camera reads, image conversion, `PhotoImage.paste`, and one-tick dispatch on the Tk main thread, while `Window` waits for `StopResult.STOPPED` before service, camera, or root teardown. The opt-in real-Tk harness measures native dispatch and paste cadence, records repeatable artifacts, and separates production acceptance from diagnostic and fallback results.

**Tech Stack:** Python 3.12, tkinter, ctypes Win32 API, `QueryPerformanceCounter`, `QueryPerformanceFrequency`, high-resolution `CreateWaitableTimerExW`, one-shot `SetWaitableTimer`, `WaitForMultipleObjects`, message-only `HWND`, Pillow `PhotoImage`, NumPy, OpenCV, pytest 9, uv, Ruff, mypy, PowerShell 5.1.

**Spec:** `docs/superpowers/specs/2026-09-25-windows-high-resolution-preview-clock-design.md`

## Global Constraints

- Work in `C:\PokeCon\Poke-Controller-Modified` on the existing `release/v4.0` checkout. The current baseline is HEAD `11b4ee805e07622eceb0baa89873fd6b076f1c49`, with unrelated dirty files and untracked E2E support that must be preserved.
- Do not run `git reset`, `git checkout`, `git clean`, `git add`, `git commit`, or any other history-changing or staging command. No commits are made because the user and repository rules require an explicit commit instruction.
- Before every task, record `git status --short`. After every task, confirm that only the files named by that task changed in addition to the pre-existing dirty state.
- Production FPS values remain exactly 5, 15, 30, 45, and 60 in `WindowUtils.FPS_VALUES` and `config.py`. Do not add 60.01, 60.03, or 60.06 to UI, config, or the public API.
- For configured `F`, dispatch and paste must each satisfy `mean_hz >= F`, `mean_hz <= F + 0.1`, and endpoint-complete `p1_hz >= F - 1`. At `F=60`, this is mean `>=60.0 Hz` and P1 `>=59 Hz`. Do not lower the P1 requirement.
- The `+0.1 Hz` cap is `cap_tolerance_semantics="finite_window_boundary_not_rate_relaxation"`. It never lowers the mean minimum to `F - 0.1`.
- Diagnostic rates 60.01, 60.03, and 60.06 are harness-only. Their reports must set `diagnostic_only=true`, `production_accepted=false`, and `accepted=false` while keeping `configured_fps=60`.
- The `after` backend is exclusive. It runs only after native resources are fully torn down. Its reports use `run_class="fallback_functional"`, `functional_accepted=true` when lifecycle checks pass, `production_accepted=false`, and `accepted=false`.
- Initial E2E and contract RED evidence must exist before any production file is changed. Do not add implementation-only unit tests after production code.
- The worker may call only QPC, waitable timer, event wait, control signal, immutable mailbox writes, `PostMessageW`, and shared health updates. It must not call Tk, `after`, widgets, camera reads, image conversion, `PhotoImage.paste`, logging routines, or `time.perf_counter_ns()`.
- No worker queue, catch-up burst, `after(0)`, `after(1)`, `SetTimer`, `WM_TIMER`, or timer-queue polling may drive preview dispatch.
- `time.perf_counter_ns()` is only an acceptance and E2E measurement clock. QPC values stay in QPC fields and are never converted into `perf_counter_ns` values.
- A native run that cannot deliver custom messages to the Tk event loop is a failed architecture precondition. Do not pass it by treating the fallback as production parity.
- Synthetic 180 Hz source evidence does not prove a physical camera produced 60 unique frames. Reports must state `physical_camera_evidence=false`, `physical_unique_frame_claim=false`, and `compositor_or_scanout_proof=false`.
- Do not change firmware, serial transport, wire protocol, camera source architecture, UI layout, filters, save behavior, config schema, or unrelated code.
- Every real-Tk E2E run must retain its evidence directory outside the repository. Every final gate must run the repository's full `task ci` gate, Python compilation, focused preview tests, artifact/hash audit, and final review.

---

## File Structure

| Path | Responsibility after this plan |
|---|---|
| `SerialController/ui/preview_clock.py` | New public clock facade, frozen cross-thread records, state machine, Win32 resources, worker, WNDPROC dispatch, latest-only accounting, health recovery, and exclusive `after` fallback. |
| `SerialController/GuiAssets.py` | Keep one camera read, conversion, paste, statistics, and mouse-input tick in `CaptureArea`; delegate scheduling and lifecycle to `PreviewClock`; expose private E2E observability without crossing threads. |
| `SerialController/Window.py` | Split external `exit()` from internal `_continue_exit()`, wait for preview stop, and guard root destruction while teardown is pending. |
| `tests/test_preview_clock.py` | New focused isolation and failure-injection tests for clock math, fake runtime behavior, Win32 setup, worker wake separation, mailbox accounting, health recovery, cleanup, and fallback. |
| `tests/preview_fps_support.py` | Extend the existing untracked harness in place with configurable targets, native dispatch measurement, reports, source/artifact hashes, thread IDs, camera and teardown evidence, and matrix configuration. |
| `tests/test_preview_fps_e2e.py` | Add non-GUI contract checks and opt-in real-Tk native, fallback, diagnostic, and `Window.exit()` lifecycle tests. |
| `tests/test_preview_fps_measurement_contract.py` | Parameterize all five production targets, fixed windows, P1, cap, overshoot, acceptance, and artifact decisions. |
| `tests/test_preview_fps_internal_rate_contract.py` | Keep diagnostic rates separate from configured production FPS and prove they cannot be production accepted. |
| `SerialController/ui/camera_panel.py` | Verify only. Its existing `applyFps()` already forwards `_current_fps()` to `CaptureArea.setFps()` and `Camera.setFps()`; no diff is expected. |
| `SerialController/config.py` | Verify only. Its existing valid FPS set remains exactly 5, 15, 30, 45, and 60. |
| `SerialController/WindowUtils.py` | Verify only. Its existing `FPS_VALUES` remains `[60, 45, 30, 15, 5]`. |

## Task 1: Lock the production acceptance and report contracts with RED tests

**Files:**
- Modify: `tests/test_preview_fps_measurement_contract.py`
- Modify: `tests/test_preview_fps_internal_rate_contract.py`
- Modify: `tests/test_preview_fps_e2e.py`
- Verify without modifying: `SerialController/WindowUtils.py`
- Verify without modifying: `SerialController/config.py`

**Interfaces:**
- Consumes: existing `preview_fps_support` harness, nominal fixed-window helpers, and the approved thresholds.
- Produces: failing tests that require `PRODUCTION_FPS_VALUES`, `DIAGNOSTIC_RATES_HZ`, `CadenceContract`, `CadenceResult`, `AcceptanceDecision`, `cadence_contract()`, `measurement_overshoot()`, and `decide_acceptance()` with the exact signatures below.

```python
PRODUCTION_FPS_VALUES: Final[tuple[int, ...]] = (5, 15, 30, 45, 60)
DIAGNOSTIC_RATES_HZ: Final[tuple[float, ...]] = (60.01, 60.03, 60.06)

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

@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    production_accepted: bool
    functional_accepted: bool
    test_passed: bool
    accepted: bool

def cadence_contract(configured_fps: int) -> CadenceContract: ...
def measurement_overshoot(
    nominal_start_ns: int,
    actual_end_ns: int,
    configured_measurement_s: float,
    configured_fps: int,
) -> tuple[int, int, bool]: ...
def decide_acceptance(
    *,
    run_class: str,
    clock_mode: str,
    contract: CadenceContract,
    dispatch: CadenceResult,
    paste: CadenceResult,
    measurement_overshoot_ok: bool,
    functional_ok: bool,
    evidence_ok: bool,
) -> AcceptanceDecision: ...
```

- [ ] **Step 1: Record the dirty-state baseline without changing it**

Run from `C:\PokeCon\Poke-Controller-Modified`:

```powershell
git status --short
git rev-parse HEAD
```

Expected: branch context is `release/v4.0`, HEAD begins with `11b4ee805e07622eceb0baa89873fd6b076f1c49`, and unrelated dirty entries remain visible. Do not clean, stage, or rewrite them.

- [ ] **Step 2: Add all-target fixed-window, P1, cap, and overshoot tests**

Add these exact parameterized contracts to `tests/test_preview_fps_measurement_contract.py`:

```python
import pytest


@pytest.mark.parametrize("configured_fps", (5, 15, 30, 45, 60))
def test_production_contract_keeps_configured_mean_cap_and_p1(
    configured_fps: int,
) -> None:
    contract = support.cadence_contract(configured_fps)
    assert contract == support.CadenceContract(
        configured_fps=configured_fps,
        target_mean_min_hz=float(configured_fps),
        target_mean_max_hz=configured_fps + 0.1,
        target_p1_min_hz=configured_fps - 1.0,
        cap_tolerance_hz=0.1,
        cap_tolerance_semantics="finite_window_boundary_not_rate_relaxation",
    )


@pytest.mark.parametrize("configured_fps", (5, 15, 30, 45, 60))
def test_native_production_decision_requires_dispatch_and_paste(
    configured_fps: int,
) -> None:
    contract = support.cadence_contract(configured_fps)
    passing = support.CadenceResult(
        count=configured_fps * 60,
        mean_hz=float(configured_fps),
        p1_hz=float(configured_fps - 1),
    )
    decision = support.decide_acceptance(
        run_class="native_production",
        clock_mode="high_resolution",
        contract=contract,
        dispatch=passing,
        paste=passing,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    )
    assert decision.production_accepted is True
    assert decision.accepted is True

    below_p1 = support.CadenceResult(
        count=passing.count,
        mean_hz=passing.mean_hz,
        p1_hz=configured_fps - 1.000001,
    )
    assert not support.decide_acceptance(
        run_class="native_production",
        clock_mode="high_resolution",
        contract=contract,
        dispatch=below_p1,
        paste=passing,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    ).production_accepted

    above_cap = support.CadenceResult(
        count=passing.count,
        mean_hz=configured_fps + 0.100001,
        p1_hz=passing.p1_hz,
    )
    assert not support.decide_acceptance(
        run_class="native_production",
        clock_mode="high_resolution",
        contract=contract,
        dispatch=above_cap,
        paste=passing,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    ).production_accepted


@pytest.mark.parametrize("configured_fps", (5, 15, 30, 45, 60))
def test_measurement_overshoot_limit_uses_two_periods_or_fifty_ms(
    configured_fps: int,
) -> None:
    start_ns = 1_000_000_000
    period_ns = 1_000_000_000 // configured_fps
    limit_ns = max(2 * period_ns, 50_000_000)
    nominal_end_ns = start_ns + 60 * 1_000_000_000
    assert support.measurement_overshoot(
        start_ns, nominal_end_ns + limit_ns, 60.0, configured_fps
    ) == (limit_ns, limit_ns, True)
    assert support.measurement_overshoot(
        start_ns, nominal_end_ns + limit_ns + 1, 60.0, configured_fps
    ) == (limit_ns + 1, limit_ns, False)
```

Keep the existing half-open fixed-window test. Change no threshold to make the current `after` baseline pass.

- [ ] **Step 3: Add diagnostic and fallback non-acceptance tests**

Replace the old internal-rate assumptions with these exact tests in `tests/test_preview_fps_internal_rate_contract.py`:

```python
@pytest.mark.parametrize("diagnostic_rate_hz", (60.01, 60.03, 60.06))
def test_diagnostic_rate_keeps_configured_fps_and_cannot_be_accepted(
    diagnostic_rate_hz: float,
) -> None:
    contract = support.cadence_contract(60)
    cadence = support.CadenceResult(count=3600, mean_hz=diagnostic_rate_hz, p1_hz=59.0)
    decision = support.decide_acceptance(
        run_class="diagnostic",
        clock_mode="high_resolution",
        contract=contract,
        dispatch=cadence,
        paste=cadence,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    )
    assert decision.production_accepted is False
    assert decision.accepted is False
    assert decision.test_passed is True


def test_after_fallback_is_functional_only() -> None:
    contract = support.cadence_contract(60)
    cadence = support.CadenceResult(count=3600, mean_hz=60.0, p1_hz=59.0)
    decision = support.decide_acceptance(
        run_class="fallback_functional",
        clock_mode="after",
        contract=contract,
        dispatch=cadence,
        paste=cadence,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    )
    assert decision.functional_accepted is True
    assert decision.production_accepted is False
    assert decision.accepted is False


def test_configured_fps_never_overwritten_by_diagnostic_rate() -> None:
    config = support.E2EConfig(
        run_class=support.RunClass.DIAGNOSTIC,
        configured_fps=60,
        diagnostic_rate_hz=60.03,
        warmup_s=2.0,
        measurement_s=5.0,
        evidence_dir=Path("C:/strict-evidence"),
        source_hz=180,
        child_mode=support.ChildMode.NORMAL,
        test_id="test_diagnostic_rate_is_reference_only",
        phase=support.RunPhase.DIAGNOSTIC,
        watchdog_timeout_s=15.0,
    )
    assert config.execution_contract()["configured_fps"] == 60
    assert config.execution_contract()["diagnostic_rate_hz"] == 60.03
```

- [ ] **Step 4: Add source pin, artifact manifest, thread ID, and accounting RED tests**

Add these contracts to `tests/test_preview_fps_measurement_contract.py`:

```python
def test_source_pins_include_production_and_evidence_files() -> None:
    assert support.SOURCE_PIN_PATHS == (
        "SerialController/ui/preview_clock.py",
        "SerialController/GuiAssets.py",
        "SerialController/Window.py",
        "SerialController/ui/camera_panel.py",
        "SerialController/config.py",
        "SerialController/WindowUtils.py",
        "tests/test_preview_clock.py",
        "tests/preview_fps_support.py",
        "tests/test_preview_fps_e2e.py",
        "tests/test_preview_fps_measurement_contract.py",
        "tests/test_preview_fps_internal_rate_contract.py",
    )


def test_latest_only_accounting_equation() -> None:
    assert support.latest_only_accounting_balanced(
        worker_tick_published_count=100,
        main_dispatch_count=88,
        pending_tick_superseded_count=7,
        stale_tick_dropped_count=4,
        pending_tick_present_at_window_end=1,
    )


def test_manifest_detects_changed_or_missing_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.py"
    source.write_text("first\n", encoding="utf-8")
    start = support.pin_files((source,))
    source.write_text("second\n", encoding="utf-8")
    end = support.pin_files((source,))
    assert not support.source_pins_unchanged(start, end)


def test_thread_manifest_requires_distinct_main_and_worker() -> None:
    assert support.thread_ids_valid(
        {"main": 10, "window_owner": 10, "preview_worker": 11, "camera_source": 12}
    )
    assert not support.thread_ids_valid(
        {"main": 10, "window_owner": 10, "preview_worker": 10, "camera_source": 12}
    )
```

- [ ] **Step 5: Add opt-in real-Tk test entry points without implementing production code**

Add these test functions to `tests/test_preview_fps_e2e.py`:

```python
def test_native_production_60hz_meets_hard_contract() -> None:
    support.run_gated_test("test_native_production_60hz_meets_hard_contract")


def test_native_production_15hz_meets_configured_contract() -> None:
    support.run_gated_test("test_native_production_15hz_meets_configured_contract")


def test_native_extended_target_meets_configured_contract() -> None:
    support.run_gated_test("test_native_extended_target_meets_configured_contract")


def test_after_fallback_functional_at_configured_target() -> None:
    support.run_gated_test("test_after_fallback_functional_at_configured_target")


def test_diagnostic_rate_is_reference_only() -> None:
    support.run_gated_test("test_diagnostic_rate_is_reference_only")


def test_window_exit_waits_for_preview_teardown() -> None:
    support.run_gated_test("test_window_exit_waits_for_preview_teardown")
```

The tests must remain opt-in. Calling `run_gated_test()` without the gate must skip before creating a Tk root.

- [ ] **Step 6: Prove the contract RED state before production edits**

Run:

```powershell
uv run --frozen pytest tests/test_preview_fps_measurement_contract.py tests/test_preview_fps_internal_rate_contract.py -q
```

Expected: FAIL because `cadence_contract`, `decide_acceptance`, expanded source pins, and manifest helpers do not exist yet. Confirm that no production file has changed.

Run the 60 FPS gated entry point once to prove the E2E gate is RED:

```powershell
$run = Join-Path $env:TEMP ("PokeCon-preview-fps-contract-red-60-" + [guid]::NewGuid().ToString("N"))
$env:POKECON_RUN_PREVIEW_FPS_E2E = "1"
$env:POKECON_FPS_TEST_ID = "test_native_production_60hz_meets_hard_contract"
$env:POKECON_FPS_PHASE = "production"
$env:POKECON_FPS_RUN_CLASS = "native_production"
$env:POKECON_FPS_CONFIGURED_FPS = "60"
$env:POKECON_FPS_DIAGNOSTIC_RATE_HZ = ""
$env:POKECON_FPS_WARMUP_S = "10"
$env:POKECON_FPS_MEASUREMENT_S = "60"
$env:POKECON_FPS_SOURCE_HZ = "180"
$env:POKECON_FPS_EVIDENCE_DIR = $run
$env:POKECON_FPS_CHILD_MODE = "normal"
$env:POKECON_FPS_WATCHDOG_TIMEOUT_S = "100"
uv run --frozen pytest tests/test_preview_fps_e2e.py::test_native_production_60hz_meets_hard_contract -q
```

Expected: FAIL during contract validation or while collecting an incomplete report. Record the command, exit code, and evidence path in the task notes. Do not change production code yet.

- [ ] **Step 7: Review checkpoint for Task 1**

Run:

```powershell
git diff --check -- tests/test_preview_fps_measurement_contract.py tests/test_preview_fps_internal_rate_contract.py tests/test_preview_fps_e2e.py
git status --short
```

Confirm only the three test files were added to the pre-existing dirty set. Do not stage or commit.

---

## Task 2: Add clock isolation and failure-injection RED tests

**Files:**
- Create: `tests/test_preview_clock.py`
- Modify: `tests/preview_fps_support.py` only enough to add `tests/test_preview_clock.py` to `SOURCE_PIN_PATHS` after Task 3's full pin tuple exists.

**Interfaces:**
- Consumes: the missing public `PreviewClock`, `StopResult`, and `DispatchResult` interfaces from the approved design.
- Produces: failing tests for exact FPS validation, QPC conversion, deadline skipping, wake separation, latest-only accounting, teardown, `PostMessageW` failure, exclusive fallback, and the worker/Tk boundary.

- [ ] **Step 1: Create deterministic fake runtime and fake root test doubles**

Define these exact helpers in `tests/test_preview_clock.py`:

```python
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

import preview_fps_support as support
from ui import preview_clock


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


class FakeNativeRuntime:
    def __init__(self, *, stop_join_results: list[bool] | None = None) -> None:
        self.controls: list[preview_clock._ControlState] = []
        self.stop_requests = 0
        self.destroy_calls = 0
        self.stop_join_results = stop_join_results or [True]
        self.pending: preview_clock.PreviewTick | None = None
        self.wake_callback: Callable[[], None] | None = None
        self.health: dict[str, int | str | bool] = {
            "worker_thread_native_id": threading.get_native_id() + 1,
            "last_sequence": 0,
            "last_delivered_sequence": 0,
            "last_wake_qpc": 0,
            "post_failure_count": 0,
            "last_post_error": 0,
            "running": True,
        }

    def bind_wake_callback(self, callback: Callable[[], None]) -> None:
        self.wake_callback = callback

    def start(self) -> None:
        return None

    def request_control(self, control: preview_clock._ControlState) -> None:
        self.controls.append(control)

    def request_stop(self) -> None:
        self.stop_requests += 1

    def join(self, timeout_s: float) -> bool:
        assert timeout_s >= 0.0
        return self.stop_join_results.pop(0)

    def destroy(self) -> None:
        assert not self.stop_join_results
        self.destroy_calls += 1

    def consume_tick(self) -> preview_clock.PreviewTick | None:
        tick, self.pending = self.pending, None
        return tick

    def snapshot(self) -> dict[str, int | str | bool]:
        return dict(self.health)

    def begin_accounting_window(self) -> None:
        return None

    def end_accounting_window(self) -> dict[str, int | str | bool]:
        return {}

    def drain_evidence(self) -> tuple[dict[str, object], ...]:
        return ()

    def emit_wake(self) -> None:
        assert self.wake_callback is not None
        self.wake_callback()
```

- [ ] **Step 2: Add public interface and conversion RED tests**

Add these exact tests:

```python
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
            fps,  # type: ignore[arg-type]
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
    assert preview_clock._negative_due_100ns(
        deadline_qpc=1_001,
        now_qpc=1_000,
        frequency_hz=10_000_000,
    ) == -101


def test_deadline_skip_uses_one_arithmetic_grid_step() -> None:
    assert preview_clock._advance_deadline(
        deadline_qpc=100,
        now_qpc=451,
        interval_qpc=100,
    ) == 500
```

- [ ] **Step 3: Add facade, wake separation, accounting, and fallback RED tests**

Add:

```python
def test_start_dispatches_one_synchronous_tick_before_control(
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
        lambda: calls.append("tick") or preview_clock.DispatchResult("active"),
        60,
        200,
    )
    clock.start()
    assert calls == ["tick"]
    assert len(runtime.controls) == 1
    assert runtime.controls[0].kind is preview_clock._ControlKind.START
    assert runtime.controls[0].configured_fps == 60


def test_same_fps_is_noop_and_different_fps_reanchors(
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
    generation = int(clock.health_snapshot()["generation"])
    clock.set_fps(60)
    assert clock.health_snapshot()["generation"] == generation
    clock.set_fps(30)
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
        lambda: calls.append("tick") or preview_clock.DispatchResult("active"),
        60,
        200,
    )
    clock.start()
    runtime.emit_wake()
    assert calls == ["tick"]


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
        lambda: calls.append("tick") or preview_clock.DispatchResult("active"),
        15,
        200,
    )
    clock.start()
    assert calls == ["tick"]
    assert clock.mode() == "after"
    assert root.callbacks
    clock.stop()
    assert clock.health_snapshot()["state"] == "stopped"
```

- [ ] **Step 4: Add Win32 call-shape and worker-boundary RED tests**

Add tests with a fake Win32 API that assert:

```python
def test_high_resolution_timer_creation_uses_required_access() -> None:
    api = support.FakeWin32Api()
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    assert api.timer_create_calls == [
        (None, None, 0x00000002, 0x00100002)
    ]
    assert api.ordinary_timer_create_calls == 0


def test_set_waitable_timer_is_one_shot_without_manual_reset_argument() -> None:
    api = support.FakeWin32Api()
    runtime = support.build_runtime_for_test(api)
    runtime.start()
    api.auto_run_one_timer_wake()
    timer_call = api.set_waitable_timer_calls[-1]
    assert timer_call.period_ms == 0
    assert timer_call.completion_routine is None
    assert timer_call.f_resume is False
    assert timer_call.due_100ns < 0
    assert not hasattr(timer_call, "manual_reset")


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
    runtime.join(1.0)
    health = runtime.snapshot()
    assert health["post_failure_count"] == 1
    assert health["last_post_error"] != 0
    assert health["running"] is False
    assert api.rearm_after_post_failure is False
```

The support fakes must record actual API arguments, not infer them from implementation state.

- [ ] **Step 5: Prove the isolation RED state**

Run:

```powershell
uv run --frozen pytest tests/test_preview_clock.py -q
```

Expected: FAIL during import because `SerialController/ui/preview_clock.py` does not exist. Save the failure output. Do not create production code before this RED result.

- [ ] **Step 6: Review checkpoint for Task 2**

Run:

```powershell
git diff --check -- tests/test_preview_clock.py
git status --short
```

Confirm the new test file is the only additional path. Do not stage or commit.

---

## Task 3: Implement the opt-in harness, reports, artifacts, and legacy-after E2E RED

**Files:**
- Modify: `tests/preview_fps_support.py`
- Modify: `tests/test_preview_fps_e2e.py`
- Modify: `tests/test_preview_fps_measurement_contract.py`
- Modify: `tests/test_preview_fps_internal_rate_contract.py`

**Interfaces:**
- Consumes: all RED tests from Tasks 1 and 2 that target harness behavior.
- Produces: a complete machine-readable harness that can measure the current `after` path before production integration and later measure native dispatch without changing production files in this task.

```python
class RunClass(StrEnum):
    NATIVE_PRODUCTION = "native_production"
    FALLBACK_FUNCTIONAL = "fallback_functional"
    DIAGNOSTIC = "diagnostic"

class RunPhase(StrEnum):
    PRODUCTION = "production"
    FALLBACK = "fallback"
    DIAGNOSTIC = "diagnostic"
    TEARDOWN = "teardown"

@dataclass(frozen=True, slots=True)
class FilePin:
    path: str
    exists: bool
    bytes: int
    sha256: str

@dataclass(slots=True)
class DispatchRecord:
    dispatch_id: int
    dispatch_enter_ns: int
    dispatch_exit_ns: int | None
    sequence: int
    epoch: int
    generation: int
    configured_fps: int
    schedule: str
    paste_observed: bool

def pin_files(paths: Sequence[Path]) -> dict[str, FilePin]: ...
def source_pins_unchanged(
    start: Mapping[str, FilePin], end: Mapping[str, FilePin]
) -> bool: ...
def source_pins_complete(pins: Mapping[str, FilePin]) -> bool:
    return all(pin.exists for pin in pins.values())
def latest_only_accounting_balanced(
    *,
    worker_tick_published_count: int,
    main_dispatch_count: int,
    pending_tick_superseded_count: int,
    stale_tick_dropped_count: int,
    pending_tick_present_at_window_end: int,
) -> bool: ...
```

- [ ] **Step 1: Replace callback-only production measurement with dispatch-first records**

Refactor `PasteInstrumentation` so production acceptance wraps `CaptureArea._dispatch_tick`, not `CaptureArea.after`. Keep callback timing only as fallback and health diagnostics.

Use this record flow:

```python
original_dispatch = area._dispatch_tick

def dispatch_wrapper() -> Any:
    clock = getattr(area, "_preview_clock", None)
    clock_health = (
        clock.health_snapshot()
        if clock is not None
        else {
            "last_sequence": 0,
            "epoch": 0,
            "generation": 0,
            "configured_fps": configured_fps,
        }
    )
    record = DispatchRecord(
        dispatch_id=self._next_dispatch_id,
        dispatch_enter_ns=time.perf_counter_ns(),
        dispatch_exit_ns=None,
        sequence=int(clock_health["last_sequence"]),
        epoch=int(clock_health["epoch"]),
        generation=int(clock_health["generation"]),
        configured_fps=int(clock_health["configured_fps"]),
        schedule="unknown",
        paste_observed=False,
    )
    self._next_dispatch_id += 1
    try:
        result = original_dispatch()
        if isinstance(result, preview_clock.DispatchResult):
            record.schedule = result.schedule
        else:
            record.schedule = "legacy_unknown"
        return result
    finally:
        record.dispatch_exit_ns = time.perf_counter_ns()
        dispatch_records.append(record)

area._dispatch_tick = dispatch_wrapper
```

The wrapper must not call `root.after`, sleep, alter the mailbox, or change the configured FPS. The existing paste wrapper sets `dispatch_records[-1].paste_observed=true` when the paste occurs inside that dispatch. Restore the instance method during teardown.

For the pre-production RED only, select the method once during harness construction:

```python
if hasattr(area, "_preview_clock") and area._preview_clock is not None:
    setattr(area, "_dispatch_tick", dispatch_wrapper)
    dispatch_oracle = "PreviewClock WNDPROC to CaptureArea entry"
else:
    setattr(area, "capture", dispatch_wrapper)
    dispatch_oracle = "legacy CaptureArea.after callback entry"
```

The legacy branch sets `clock_mode="after"` and can never set `production_accepted=true`. After Task 5, native production runs must take the `_dispatch_tick` branch and reject the legacy branch.

- [ ] **Step 2: Implement exact production and diagnostic threshold decisions**

Implement the interfaces from Task 1 with this decision core:

```python
def decide_acceptance(
    *,
    run_class: str,
    clock_mode: str,
    contract: CadenceContract,
    dispatch: CadenceResult,
    paste: CadenceResult,
    measurement_overshoot_ok: bool,
    functional_ok: bool,
    evidence_ok: bool,
) -> AcceptanceDecision:
    evidence_passed = functional_ok and evidence_ok
    if run_class == RunClass.DIAGNOSTIC:
        return AcceptanceDecision(False, evidence_passed, evidence_passed, False)
    if run_class == RunClass.FALLBACK_FUNCTIONAL:
        return AcceptanceDecision(False, evidence_passed, evidence_passed, False)
    cadence_passed = all(
        result.count >= 2
        and contract.target_mean_min_hz <= result.mean_hz <= contract.target_mean_max_hz
        and result.p1_hz >= contract.target_p1_min_hz
        for result in (dispatch, paste)
    )
    production_passed = (
        clock_mode == "high_resolution"
        and cadence_passed
        and measurement_overshoot_ok
        and evidence_passed
    )
    return AcceptanceDecision(
        production_accepted=production_passed,
        functional_accepted=evidence_passed,
        test_passed=evidence_passed,
        accepted=production_passed,
    )
```

- [ ] **Step 3: Implement the fixed nominal window, endpoint-only P1, and overshoot gate**

Use entry timestamps for both cadence streams. Do not require interval endpoints to have exited before the window end.

```python
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
```

Keep the nearest-rank P1 rule:

```python
rank = max(1, math.ceil(0.01 * len(intervals)))
p1_hz = sorted(NS_PER_S / (right - left) for left, right in intervals)[rank - 1]
```

Calculate actual overshoot as `max(0, actual_end_ns - nominal_end_ns)` and fail production acceptance when it exceeds `max(2 * target_period_ns, 50_000_000)`. Keep actual elapsed rate diagnostic-only.

- [ ] **Step 4: Add all required evidence files and final hash manifest**

Use these exact source pins:

```python
SOURCE_PIN_PATHS: Final[tuple[str, ...]] = (
    "SerialController/ui/preview_clock.py",
    "SerialController/GuiAssets.py",
    "SerialController/Window.py",
    "SerialController/ui/camera_panel.py",
    "SerialController/config.py",
    "SerialController/WindowUtils.py",
    "tests/test_preview_clock.py",
    "tests/preview_fps_support.py",
    "tests/test_preview_fps_e2e.py",
    "tests/test_preview_fps_measurement_contract.py",
    "tests/test_preview_fps_internal_rate_contract.py",
)
```

Before `preview_clock.py` exists, `pin_files()` records it with `exists=false`, `bytes=0`, and an empty SHA-256 value in both the start and end sets. This keeps the file set identical and lets the pre-production E2E RED retain a complete report. `source_pins_unchanged=true` may be true for that baseline, but `source_pins_complete=false` forces `evidence_ok=false` and `accepted=false`. After Task 4 creates the file, every pin must have `exists=true`, and the same source tuple must pass.

Write these files:

```text
report.json
wakes.jsonl
clock_pairs.jsonl
intervals.jsonl
dispatch_intervals.jsonl
camera_reads.jsonl
health.jsonl
teardown.json
thread_ids.json
source_pins.json
artifact_hashes.json
```

`source_pins.json` shape:

```json
{
  "schema_version": 1,
  "start": [{"path": "...", "exists": true, "bytes": 0, "sha256": "..."}],
  "end": [{"path": "...", "exists": true, "bytes": 0, "sha256": "..."}],
  "unchanged": true,
  "complete": true
}
```

`artifact_hashes.json` is the terminal manifest. Hash every required evidence file except `artifact_hashes.json` itself, after `report.json` is final. The parent then re-hashes each file and fails the test on any mismatch.

- [ ] **Step 5: Record thread IDs and physical-evidence limits**

Write:

```json
{
  "schema_version": 1,
  "main": 0,
  "window_owner": 0,
  "preview_worker": 0,
  "camera_source": 0,
  "camera_thread": null
}
```

For the synthetic harness, require `main == window_owner`, require `preview_worker != main` when native mode ran, require `camera_source != main`, and use `camera_thread=null` with `camera_thread_claim="not_applicable_synthetic_source"`.

Every report must include:

```python
"physical_camera_evidence": False,
"physical_unique_frame_claim": False,
"compositor_or_scanout_proof": False,
```

- [ ] **Step 6: Add camera sequence and teardown evidence**

Extend `SyntheticFrameSource` to record each read with `perf_counter_ns`, native thread ID, sequence, frame presence, duplicate, regression, and error. Merge those rows into `camera_reads.jsonl`.

Add a `teardown` phase that uses a real Tk root and a controlled `PokeControllerApp` assembled with `object.__new__`. Attach the real `CaptureArea` and `PreviewClock`, but wrap only `PreviewClock.stop` in the child process so the first call returns `StopResult.PENDING` and the second returns `StopResult.STOPPED`. Call `PokeControllerApp.exit()` directly, assert the root still answers `winfo_exists()` while pending, run only the scheduled `_continue_exit` retry, then verify the root is destroyed and `teardown.json` records the full phase order. This is the required real-Tk teardown path without firmware, serial hardware, or a physical camera.

The teardown payload must include:

```python
{
    "stop_result": "stopped",
    "root_alive_while_pending": True,
    "root_destroyed_after_stop": True,
    "preview_worker_alive": False,
    "synthetic_source_alive": False,
    "post_teardown_camera_read_count": 0,
    "pending_after_ids": [],
    "window_class_destroyed": True,
    "window_class_unregistered": True,
}
```

- [ ] **Step 7: Freeze accounting, extract raw evidence, and enforce gap forensics**

At the exact measurement start callback, call the private main-thread hook when the native clock exists:

```python
clock = getattr(area, "_preview_clock", None)
if clock is not None:
    clock._begin_evidence_window()
    start_snapshot = clock.health_snapshot()
else:
    start_snapshot = {}
```

At the first callback at or after the nominal end, freeze the window before stopping the clock:

```python
window_end_ns = time.perf_counter_ns()
if clock is not None:
    end_accounting = clock._end_evidence_window()
    end_snapshot = clock.health_snapshot()
else:
    end_accounting = {"latest_only_accounting_ok": False}
    end_snapshot = {}
stop_result = area.stopCapture()
if stop_result is None:
    stop_result = "stopped"
```

The legacy baseline is allowed to write this false accounting result so the report exists, but it remains ineligible for production acceptance.

`end_accounting` must include both session totals and deltas from `start_snapshot`, including `pending_tick_present_at_window_end`. The harness must assert:

```python
worker_tick_published_count == (
    main_dispatch_count
    + pending_tick_superseded_count
    + stale_tick_dropped_count
    + pending_tick_present_at_window_end
)
```

After stop, drain the private evidence queue on the Tk main thread and partition records by `record_type`:

```text
wake, clock_pair, worker_tick, control_wake, reentrant_wake,
stale_drop, post_failure, health_callback
```

Write wake and failure rows to `wakes.jsonl`, paired QPC and `perf_counter_ns` rows to `clock_pairs.jsonl`, and health snapshots to `health.jsonl`. Do not convert QPC to `perf_counter_ns`.

Use `GAP_THRESHOLD_NS=25_000_000`. Build gap rows for paste, dispatch, worker tick, and control wake. Each row contains `stream`, `clock_domain`, `start_ns`, `end_ns`, `gap_ns`, `before_sequence`, `after_sequence`, `recovery`, and `reason`. Paste and dispatch gaps use `perf_counter`; worker-tick and control-wake gaps use QPC and retain their QPC frequency. Never subtract timestamps from different clock domains. Preserve every gap, including a single event. Do not add a zero-gap acceptance condition.

For the extended `configured_fps=5` functional run, perform these transitions during warmup and return to active 5 FPS before the nominal measurement window: set 5 to 15, set 15 back to 5, set `is_show` false long enough for at least two 200 ms idle callbacks, then set it true. Record each generation and schedule transition in `health.jsonl`. The acceptance window still measures only the final active 5 FPS state.

The final `report.json` must contain at least:

```text
configured_fps, diagnostic_rate_hz, run_class, diagnostic_only, clock_mode,
target_mean_min_hz, target_mean_max_hz, target_p1_min_hz,
cap_tolerance_hz, cap_tolerance_semantics,
measurement_start_ns, nominal_window_end_ns, actual_window_end_ns,
measurement_overshoot_ns, measurement_overshoot_limit_ns,
measurement_overshoot_ok, dispatch_summary, paste_summary,
production_accepted, functional_accepted, test_passed, accepted,
worker_tick_published_count, pending_tick_superseded_count,
main_dispatch_count, stale_tick_dropped_count,
pending_tick_present_at_window_end, latest_only_accounting_ok,
wake_reason_counts, gap_threshold_ns, dispatch_gaps, paste_gaps,
worker_tick_gaps, control_wake_gaps,
camera_read_count, camera_frame_present_count, camera_none_count,
camera_read_error_count, camera_unique_sequence_count,
camera_duplicate_sequence_count, camera_sequence_regression_count,
camera_last_sequence, post_teardown_camera_read_count,
thread_ids, source_pins_unchanged, source_pins_complete,
artifact_manifest_path, physical_camera_evidence,
physical_unique_frame_claim, compositor_or_scanout_proof
```

- [ ] **Step 8: Update E2E configuration without overwriting configured FPS**

Use this immutable execution contract:

```python
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
```

A diagnostic run with `diagnostic_rate_hz=60.03` still records `configured_fps=60`. The UI and public `PreviewClock` constructor never receive 60.03.

The child harness applies private test seams without changing production defaults:

```python
from ui import preview_clock

original_factory = preview_clock._create_native_runtime

def diagnostic_factory(**kwargs: Any) -> Any:
    return original_factory(
        **kwargs,
        diagnostic_rate_hz=config.diagnostic_rate_hz,
    )

def fallback_factory(**_kwargs: Any) -> Any:
    raise preview_clock._PreviewClockError(
        "E2E forced native initialization failure"
    )

if config.run_class is RunClass.DIAGNOSTIC:
    preview_clock._create_native_runtime = diagnostic_factory
elif config.run_class is RunClass.FALLBACK_FUNCTIONAL:
    preview_clock._create_native_runtime = fallback_factory
```

The fallback run therefore exercises the real `PreviewClock.start()` cleanup and exclusive `after` path. The diagnostic run changes only the private QPC interval calculation. Both runs retain configured FPS 60 in native tick data and reports. A native production or native diagnostic run must skip before Tk creation when `os.name != "nt"`; it must never substitute fallback. Functional fallback runs remain portable.

- [ ] **Step 9: Make the deterministic contract tests GREEN**

Run:

```powershell
uv run --frozen pytest tests/test_preview_fps_measurement_contract.py tests/test_preview_fps_internal_rate_contract.py -q
```

Expected: PASS, except the source-pin test may remain RED until `preview_clock.py` exists. If that is the only failure, preserve it as the expected pre-production RED and continue to the E2E baseline.

- [ ] **Step 10: Capture the current 60 FPS `after` baseline RED with retained artifacts**

Run:

```powershell
$run = Join-Path $env:TEMP ("PokeCon-preview-fps-after-red-60-" + [guid]::NewGuid().ToString("N"))
$env:POKECON_RUN_PREVIEW_FPS_E2E = "1"
$env:POKECON_FPS_TEST_ID = "test_native_production_60hz_meets_hard_contract"
$env:POKECON_FPS_PHASE = "production"
$env:POKECON_FPS_RUN_CLASS = "native_production"
$env:POKECON_FPS_CONFIGURED_FPS = "60"
$env:POKECON_FPS_DIAGNOSTIC_RATE_HZ = ""
$env:POKECON_FPS_WARMUP_S = "10"
$env:POKECON_FPS_MEASUREMENT_S = "60"
$env:POKECON_FPS_SOURCE_HZ = "180"
$env:POKECON_FPS_EVIDENCE_DIR = $run
$env:POKECON_FPS_CHILD_MODE = "normal"
$env:POKECON_FPS_WATCHDOG_TIMEOUT_S = "100"
uv run --frozen pytest tests/test_preview_fps_e2e.py::test_native_production_60hz_meets_hard_contract -q
$baselineExit = $LASTEXITCODE
"baseline_exit=$baselineExit evidence=$run"
```

Expected before Task 4: FAIL. The retained `report.json` must show `clock_mode="after"`, `production_accepted=false`, and the measured dispatch or paste P1 below 59 Hz. If the machine happens to measure 59 Hz or better, retain the run but mark `run_class="native_production"` and `clock_mode="after"`; the run must still fail because `after` is not accepted as native production parity.

- [ ] **Step 11: Review checkpoint for Task 3**

Run:

```powershell
git diff --check -- tests/preview_fps_support.py tests/test_preview_fps_e2e.py tests/test_preview_fps_measurement_contract.py tests/test_preview_fps_internal_rate_contract.py
git status --short
```

Confirm no production file changed. Do not stage or commit.

---

## Task 4: Implement `PreviewClock`, the QPC worker, Win32 message delivery, health recovery, and fallback

**Files:**
- Create: `SerialController/ui/preview_clock.py`
- Modify: `tests/test_preview_clock.py` only to improve failure messages or add missing assertions identified by the first implementation run.
- Modify: `tests/preview_fps_support.py` to add the fake Win32 API helpers already required by Task 2.

**Interfaces:**
- Consumes: the RED tests and harness contracts from Tasks 1 through 3.
- Produces: the exact public API below, an internal native runtime, deterministic accounting, and exclusive fallback.

```python
class StopResult(StrEnum):
    STOPPED = "stopped"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class DispatchResult:
    schedule: Literal["active", "idle"]


@dataclass(frozen=True, slots=True)
class PreviewTick:
    epoch: int
    generation: int
    configured_fps: int
    sequence: int
    deadline_qpc: int
    emitted_qpc: int


@dataclass(frozen=True, slots=True)
class PreviewWake:
    kind: Literal["clock_ready", "tick", "error"]
    epoch: int
    generation: int
    configured_fps: int
    sequence: int = 0
    qpc_frequency_hz: int = 0
    error_name: str = ""


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    worker_thread_native_id: int
    last_sequence: int
    last_wake_qpc: int
    post_failure_count: int
    last_post_error: int
    running: bool


class PreviewClock:
    def __init__(
        self,
        root: Any,
        dispatch_tick: Callable[[], DispatchResult],
        configured_fps: int,
        idle_interval_ms: int,
    ) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> StopResult: ...
    def set_fps(self, fps: int) -> None: ...
    def mode(self) -> Literal["stopped", "high_resolution", "after"]: ...
    def health_snapshot(self) -> Mapping[str, int | str | bool]: ...
```

Private E2E seams must remain private and main-thread only:

```python
class _PreviewClockError(RuntimeError):
    pass

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

class _NativeRuntime(Protocol):
    def bind_wake_callback(self, callback: Callable[[], None]) -> None: ...
    def start(self) -> None: ...
    def request_control(self, control: _ControlState) -> None: ...
    def request_stop(self) -> None: ...
    def join(self, timeout_s: float) -> bool: ...
    def destroy(self) -> None: ...
    def consume_tick(self) -> PreviewTick | None: ...
    def snapshot(self) -> Mapping[str, int | str | bool]: ...
    def begin_accounting_window(self) -> None: ...
    def end_accounting_window(self) -> Mapping[str, int | str | bool]: ...
    def drain_evidence(self) -> tuple[Mapping[str, object], ...]: ...

def _create_native_runtime(
    *,
    owner_thread_id: int,
    configured_fps: int,
    idle_interval_ms: int,
    diagnostic_rate_hz: float | None = None,
) -> _NativeRuntime: ...

def _begin_evidence_window(self) -> None: ...
def _end_evidence_window(self) -> Mapping[str, int | str | bool]: ...
def _drain_evidence(self) -> tuple[Mapping[str, object], ...]: ...
```

`PreviewClock.start()` always calls `_create_native_runtime()` with its production `configured_fps` and no diagnostic rate. The E2E child may wrap that private factory in-process and supply `diagnostic_rate_hz` only for a diagnostic reference run. The public constructor and `PreviewTick.configured_fps` remain unchanged.

Keep one lock-protected immutable control slot. Write the new `_ControlState`, then signal the auto-reset control event. If controls collapse before the worker drains them, the newest state wins and the worker reanchors once from that state. A control wake never reads the tick mailbox and never publishes a tick.

`health_snapshot()` returns a fresh mapping containing at least these scalar fields:

```text
state, mode, epoch, generation, configured_fps, qpc_frequency_hz,
worker_thread_native_id, last_sequence, last_delivered_sequence,
last_wake_qpc, worker_tick_published_count, pending_tick_superseded_count,
main_dispatch_count, stale_tick_dropped_count, post_failure_count,
last_post_error, running, pending_tick_present, dispatch_in_flight,
reentrant_wake_count, last_health_callback_result
```

- [ ] **Step 1: Implement the fake Win32 API and worker-context test seams**

Add `FakeWin32Api`, `TimerCreateCall`, `SetWaitableTimerCall`, `build_runtime_for_test()`, and `worker_context_for_test()` to `tests/preview_fps_support.py`. The fake API must execute the real `_WindowsNativeRuntime` setup and worker loop against deterministic fake handles, QPC values, wait results, and Win32 return values. It must not duplicate production scheduling logic in the fake.

Run:

```powershell
uv run --frozen pytest tests/test_preview_clock.py -k "timer_creation or set_waitable_timer or worker_context or post_message_failure" -q
```

Expected: FAIL because `preview_clock.py` and its internal runtime still do not exist. This preserves test-first order inside the production task.

- [ ] **Step 2: Add public records, validation, state, and pure conversion functions**

Implement the exact public records above and these helpers:

```python
def _validate_configured_fps(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("configured FPS must be an integer")
    if value not in (5, 15, 30, 45, 60):
        raise ValueError("configured FPS must be one of 5, 15, 30, 45, 60")
    return value


def _qpc_interval(frequency_hz: int, rate_hz: float) -> int:
    return max(1, math.ceil(frequency_hz / rate_hz))


def _negative_due_100ns(
    deadline_qpc: int, now_qpc: int, frequency_hz: int
) -> int:
    delta_qpc = max(0, deadline_qpc - now_qpc)
    due_100ns = max(
        1,
        math.ceil(delta_qpc * 10_000_000 / frequency_hz),
    )
    return -due_100ns


def _advance_deadline(
    deadline_qpc: int, now_qpc: int, interval_qpc: int
) -> int:
    if deadline_qpc > now_qpc:
        return deadline_qpc
    skipped = (now_qpc - deadline_qpc) // interval_qpc + 1
    return deadline_qpc + skipped * interval_qpc
```

Reject booleans and non-integral public FPS values. The private diagnostic rate may be a float and is never stored in `PreviewTick.configured_fps`.

- [ ] **Step 3: Implement the state machine and exclusive `after` fallback**

Use these states exactly:

```python
class _ClockState(StrEnum):
    STOPPED = "stopped"
    STARTING_NATIVE = "starting_native"
    HIGH_RESOLUTION = "high_resolution"
    AFTER = "after"
    STOPPING = "stopping"
    TEARDOWN_PENDING = "teardown_pending"
    FAILED = "failed"
```

The fallback scheduler must use absolute deadlines:

```python
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
        skipped = math.floor(
            (now_s - self._fallback_deadline_s) / interval_s
        ) + 1
        self._fallback_deadline_s += skipped * interval_s
    return max(
        1,
        math.ceil((self._fallback_deadline_s - now_s) * 1000.0),
    )
```

`start()` must execute one synchronous `dispatch_tick()`, then try native setup, or start only `after` if setup fails. A successful start from `STOPPED` increments epoch and generation; a message from an older epoch is stale. `set_fps()` must be a no-op for the same value. A real change, active/idle change, start, stop, or runtime failure increments generation. Idle re-entry re-anchors at configured `F`.

- [ ] **Step 4: Implement latest-only mailbox and the accounting equation**

Use one lock-protected pending slot. On replacement, increment `pending_tick_superseded_count`. On stale epoch or generation, increment `stale_tick_dropped_count`. On a valid pop, increment `main_dispatch_count` before invoking the callback.

At an evidence-window boundary, return this exact equation:

```python
published == (
    dispatched
    + superseded
    + stale_dropped
    + pending_present_at_window_end
)
```

Expose counters through `health_snapshot()` and `_end_evidence_window()`. The end boundary must freeze a consistent snapshot while holding the same lock used by mailbox mutation.

- [ ] **Step 5: Implement the Win32 API wrapper and resource initialization**

Load Win32 functions only when `os.name == "nt"`. Use `ctypes.WinDLL("kernel32", use_last_error=True)` and explicit `argtypes` and `restype` for:

```text
QueryPerformanceFrequency
QueryPerformanceCounter
CreateWaitableTimerExW
CreateEventW
RegisterClassExW
CreateWindowExW
SetWaitableTimer
CancelWaitableTimer
WaitForMultipleObjects
PostMessageW
DestroyWindow
UnregisterClassW
CloseHandle
GetLastError
```

Use these exact resource rules:

```python
timer = CreateWaitableTimerExW(
    None,
    None,
    CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
    TIMER_MODIFY_STATE | SYNCHRONIZE,
)
stop_event = CreateEventW(None, False, False, None)
control_event = CreateEventW(None, False, False, None)
```

Do not request `TIMER_ALL_ACCESS`. Do not add `CREATE_WAITABLE_TIMER_MANUAL_RESET`. Use a process-unique class name, `HWND_MESSAGE` as parent, and retain the class atom, `WNDCLASSEXW`, `WNDPROC`, function objects, and HWND on the main-thread runtime until cleanup completes.

If any init step fails, close every acquired handle, destroy any created HWND, unregister any registered class, clear callback references, and fall back. Never retry with an ordinary waitable timer.

- [ ] **Step 6: Implement the worker with exact wake priority and one-shot arming**

The worker target must receive a context containing only API functions, handles, HWND, mailbox, health, and immutable configuration. It must not receive `root`, `dispatch_tick`, `CaptureArea`, or any `after` callable.

Use this loop:

```python
while True:
    wait_result = wait_for_multiple_objects(
        [stop_event, control_event, timer_handle], False, INFINITE
    )
    if wait_result == WAIT_FAILED:
        record_worker_error(GetLastError())
        break
    if stop_event_is_signaled():
        break
    if control_event_is_signaled():
        apply_control_state_and_reanchor()
        continue
    if timer_is_signaled(wait_result):
        now_qpc = query_performance_counter()
        if now_qpc < deadline_qpc:
            arm_one_shot(deadline_qpc, now_qpc)
            continue
        tick = publish_one_tick_to_mailbox()
        if not post_message(hwnd, PREVIEW_WAKE_MESSAGE, 0, 0):
            record_post_failure(GetLastError())
            break
        record_tick_posted(tick)
        deadline_qpc = _advance_deadline(
            deadline_qpc, now_qpc, interval_qpc
        )
        arm_one_shot(deadline_qpc, now_qpc)
```

Call `SetWaitableTimer` with a pointer to negative `LARGE_INTEGER`, `lPeriod=0`, completion routine `NULL`, and `fResume=FALSE`. There is no manual-reset argument. An early timer wake rearms the same deadline. A late wake advances the grid with one integer calculation and never dispatches a catch-up burst. Worker errors return only a sanitized error class name and Win32 error value in `PreviewWake` or `WorkerHealth`; the worker never logs.

- [ ] **Step 7: Implement clock-ready, tick, and error WNDPROC dispatch**

Use one custom message, for example `WM_APP + 0xC1`, and pass no pointer payload. The mailbox remains the source of immutable tick data.

WNDPROC behavior:

```python
if wake.kind == "clock_ready":
    record_qpc_frequency_only()
elif wake.kind == "tick":
    if dispatch_in_flight:
        record_reentrant_wake()
        leave_pending_tick_in_mailbox()
    else:
        dispatch_at_most_one_pending_tick()
elif wake.kind == "error":
    request_native_failure_recovery()
```

Consume the message, not the mailbox, for a reentrant wake. After the outer dispatch returns, drain at most one already-pending tick. Do not loop to empty and do not queue ticks. Catch every exception at the WNDPROC boundary, log only on the Tk main thread, and keep the scheduler alive for ordinary dispatch errors.

- [ ] **Step 8: Implement the one-shot health watchdog and native failure recovery**

Arm the first health callback immediately after worker start and before returning to the Tk main loop:

```python
health_timeout_ms = max(
    250,
    math.ceil(4000.0 / self._configured_fps),
)
```

Rearm it only after a valid native wake. Detect all of:

```python
post_failure_increased
or not worker_running
or worker_sequence_minus_delivered_sequence_greater_than_one
or last_wake_is_stale
```

On detection, close the dispatch gate, invalidate generation and pending tick, request worker stop, cancel the health callback, and use a dedicated root retry callback to finish join and class cleanup. Start `after` fallback only after the native runtime reaches full cleanup. Do not use the health callback as a frame scheduler.

- [ ] **Step 9: Implement idempotent stop and main-thread-only class cleanup**

Use this order:

```python
state = STOPPING
close_dispatch_gate()
increment_generation_and_clear_pending()
signal_stop_event()
cancel_waitable_timer()
if worker.join(1.0):
    worker_closed_kernel_handles()
    destroy_window_on_main_thread()
    unregister_class_on_main_thread()
    clear_wndproc_and_ctypes_references()
    state = STOPPED
    return StopResult.STOPPED
state = TEARDOWN_PENDING
return StopResult.PENDING
```

A later `stop()` continues the same cleanup. While pending, reject a new `start()`, do not destroy the root, and retain HWND, class, callback, and handle references. Closing the fallback after ID must return `STOPPED` immediately.

- [ ] **Step 10: Run the focused clock tests and fix only contract failures**

Run:

```powershell
uv run --frozen pytest tests/test_preview_clock.py -q
```

Expected: PASS with no worker test seeing Tk, no `SetTimer` or timer-queue call, and no `PostMessageW` retry.

- [ ] **Step 11: Run the previously blocked source-pin contract**

Run:

```powershell
uv run --frozen pytest tests/test_preview_fps_measurement_contract.py::test_source_pins_include_production_and_evidence_files -q
```

Expected: PASS with all eleven pinned files.

- [ ] **Step 12: Review checkpoint for Task 4**

Run:

```powershell
git diff --check -- SerialController/ui/preview_clock.py tests/test_preview_clock.py tests/preview_fps_support.py
git status --short
```

Inspect the new module for owner-thread assertions, callback lifetime, handle cleanup, and absence of worker-side Tk calls. Do not stage or commit.

---

## Task 5: Integrate one-tick dispatch and observability into `CaptureArea`

**Files:**
- Modify: `SerialController/GuiAssets.py:243-649`
- Modify: `tests/test_preview_clock.py`
- Modify: `tests/preview_fps_support.py`
- Modify: `tests/test_preview_fps_e2e.py`

**Interfaces:**
- Consumes: `PreviewClock`, `DispatchResult`, and `StopResult` from Task 4.
- Produces: `CaptureArea._dispatch_tick() -> DispatchResult`, `CaptureArea.stopCapture() -> StopResult`, configured-F re-anchoring through `setFps()`, and private evidence snapshots.

- [ ] **Step 1: Add failing CaptureArea integration tests**

Add a controlled `CaptureArea` fixture so these lifecycle tests stay headless and do not patch the production scheduler:

```python
from types import SimpleNamespace
import numpy as np

from GuiAssets import CaptureArea


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
```

Add these exact tests:

```python
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
    assert area._preview_evidence_snapshot()[
        "post_teardown_camera_read_count"
    ] == 0
```

- [ ] **Step 2: Run the focused tests to prove RED**

Run:

```powershell
uv run --frozen pytest tests/test_preview_clock.py -k "capture_area or camera_observation or stop_capture" -q
```

Expected: FAIL because `CaptureArea` still schedules `after` from `capture()` and has no `_dispatch_tick()` or lifecycle result.

- [ ] **Step 3: Replace scheduling state with a `PreviewClock` owner**

Add `import WindowUtils` and `from ui import preview_clock` to `GuiAssets.py`, then define the exact validation helper:

```python
def _validated_preview_fps(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("FPS must be one of 5, 15, 30, 45, 60")
    try:
        fps = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("FPS must be one of 5, 15, 30, 45, 60") from exc
    if fps not in WindowUtils.FPS_VALUES:
        raise ValueError("FPS must be one of 5, 15, 30, 45, 60")
    return fps
```

In `CaptureArea.__init__`, store:

```python
self._configured_fps = _validated_preview_fps(fps)
self._preview_clock: preview_clock.PreviewClock | None = None
self._camera_observation = {
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
self._preview_stop_requested = False
```

Create the clock in `startCapture()` with this exact lifecycle:

```python
def startCapture(self) -> None:
    if self._capturing:
        return
    self._capturing = True
    self._preview_stop_requested = False
    self._preview_clock = preview_clock.PreviewClock(
        self.winfo_topleft(),
        self._dispatch_tick,
        self._configured_fps,
        IDLE_INTERVAL_MS,
    )
    self._preview_clock.start()
```

`PreviewClock.start()` performs the first synchronous dispatch. `startCapture()` itself must not call `_dispatch_tick()` a second time.

- [ ] **Step 4: Implement one synchronous camera/draw/paste tick**

Implement:

```python
def _dispatch_tick(self) -> preview_clock.DispatchResult:
    if not self._capturing or self._preview_stop_requested:
        return preview_clock.DispatchResult(schedule="idle")
    started = time.perf_counter()
    showing = True
    try:
        showing = bool(self.is_show_var.get())
        if showing:
            frame, seq = self._readLatest()
            self._drawFrame(frame, seq)
            self._stat_shown += 1
            if self._stat_began_at is None:
                self._stat_began_at = started
            draw_ms = (time.perf_counter() - started) * 1000.0
            if self._stat_draw_ms <= 0.0:
                self._stat_draw_ms = draw_ms
            else:
                self._stat_draw_ms = self._stat_draw_ms * 0.9 + draw_ms * 0.1
            if draw_ms > self._stat_draw_max_ms:
                self._stat_draw_max_ms = draw_ms
        return preview_clock.DispatchResult(
            schedule="active" if showing else "idle"
        )
    except Exception as exc:
        logger.error(f"capture failed: {exc}")
        return preview_clock.DispatchResult(
            schedule="active" if showing else "idle"
        )
```

Keep `capture()` only as a compatibility shim if another current caller needs it. It may call `_dispatch_tick()` once, but it must not call `after` or schedule any next frame.

- [ ] **Step 5: Add camera sequence observability at the read boundary**

Replace `_readLatest()` with a single-attempt observation update. Count one camera read per dispatch, count a `readFrameWithSeq()` exception even when the legacy `readFrame()` fallback succeeds, and count `None` separately:

```python
def _readLatest(self) -> tuple[Any, int | None]:
    if self._preview_stop_requested:
        self._camera_observation["post_teardown_camera_read_count"] += 1
        return None, None

    self._camera_observation["camera_read_count"] += 1
    frame: Any = None
    sequence: int | None = None
    primary_error = False
    fallback_error = False
    read_seq = getattr(self.camera, "readFrameWithSeq", None)
    if callable(read_seq):
        try:
            frame, sequence = read_seq()
            sequence = int(sequence)
        except Exception:
            primary_error = True

    if not callable(read_seq) or primary_error:
        try:
            frame = self.camera.readFrame()
        except Exception:
            fallback_error = True

    if primary_error or fallback_error:
        self._camera_observation["camera_read_error_count"] += 1

    if fallback_error:
        return None, None
    if sequence is None:
        sequence_getter = getattr(self.camera, "frame_seq", None)
        if callable(sequence_getter):
            try:
                sequence = int(sequence_getter())
            except Exception:
                sequence = None
    if frame is None:
        self._camera_observation["camera_none_count"] += 1
    else:
        self._camera_observation["camera_frame_present_count"] += 1

    if sequence is not None:
        last_sequence = int(
            self._camera_observation["camera_last_sequence"]
        )
        if sequence == last_sequence:
            self._camera_observation["camera_duplicate_sequence_count"] += 1
        elif sequence < last_sequence:
            self._camera_observation["camera_sequence_regression_count"] += 1
        else:
            self._camera_observation["camera_unique_sequence_count"] += 1
        self._camera_observation["camera_last_sequence"] = max(
            last_sequence, sequence
        )
    return frame, sequence
```

Do not treat duplicates as a functional failure. Synthetic E2E requires zero read errors, zero regressions, and zero post-teardown reads.

- [ ] **Step 6: Make `setFps()` a validated generation-aware operation**

Implement:

```python
def setFps(self, fps: Any) -> None:
    fps_value = _validated_preview_fps(fps)
    if fps_value == self._configured_fps:
        return
    self._configured_fps = fps_value
    self.next_frames = 1000.0 / fps_value
    if self._preview_clock is not None:
        self._preview_clock.set_fps(fps_value)
    logger.info(
        f"FPS set to {fps_value} (interval {self.next_frames:.1f} ms)"
    )
```

`camera_panel.applyFps()` needs no change. It already calls this method and then updates the camera source.

- [ ] **Step 7: Return the lifecycle result and cancel non-preview callbacks**

Implement:

```python
def stopCapture(self) -> preview_clock.StopResult:
    self._capturing = False
    self._preview_stop_requested = True
    for name in ("_rect_after_id", "_select_after_id"):
        after_id = getattr(self, name, None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass
            setattr(self, name, None)
    if self._preview_clock is None:
        return preview_clock.StopResult.STOPPED
    result = self._preview_clock.stop()
    if result is preview_clock.StopResult.STOPPED:
        self._preview_clock = None
    return result
```

Do not cancel the clock health callback or teardown retry from `CaptureArea`; `PreviewClock` owns those IDs.

- [ ] **Step 8: Add a main-thread-only evidence snapshot**

Implement:

```python
def _preview_evidence_snapshot(self) -> dict[str, int | str | bool | dict[str, object]]:
    clock = self._preview_clock
    return {
        **self._camera_observation,
        "clock_health": dict(clock.health_snapshot()) if clock is not None else {},
    }
```

The harness calls this only on the Tk main thread. The worker never receives `CaptureArea` or this method.

- [ ] **Step 9: Run focused integration tests**

Run:

```powershell
uv run --frozen pytest tests/test_preview_clock.py -k "capture_area or camera_observation or stop_capture" -q
```

Expected: PASS.

- [ ] **Step 10: Review checkpoint for Task 5**

Run:

```powershell
git diff --check -- SerialController/GuiAssets.py tests/test_preview_clock.py tests/preview_fps_support.py tests/test_preview_fps_e2e.py
git status --short
```

Confirm `camera_panel.py`, `config.py`, and `WindowUtils.py` have no diff. Do not stage or commit.

---

## Task 6: Split `Window.exit()` and `_continue_exit()` around preview teardown

**Files:**
- Modify: `SerialController/Window.py:78-136, 428-536`
- Modify: `tests/test_preview_fps_e2e.py`
- Modify: `tests/preview_fps_support.py`

**Interfaces:**
- Consumes: `CaptureArea.stopCapture() -> StopResult` from Task 5.
- Produces: `exit()` as the confirmed external entry and `_continue_exit()` as the idempotent internal teardown procedure.

- [ ] **Step 1: Add RED shutdown-order and root-survival tests**

Add `from Window import PokeControllerApp` and `from ui import preview_clock` inside the test module or test helper. Add a fake app test to `tests/test_preview_fps_e2e.py` without constructing the full app:

```python
def test_exit_waits_for_preview_stop_before_any_service_teardown() -> None:
    app, events, root = make_fake_exit_app(
        stop_results=[preview_clock.StopResult.PENDING, preview_clock.StopResult.STOPPED]
    )
    app.exit()
    assert events[:3] == ["unbind_left", "unbind_right", "preview_stop_pending"]
    assert root.destroy_calls == 0
    assert "runner_shutdown" not in events
    assert "serial_shutdown" not in events
    assert "camera_destroy" not in events

    root.run_next()
    assert events.index("preview_stopped") < events.index("child_windows_closed")
    assert events.index("preview_stopped") < events.index("runner_shutdown")
    assert events.index("preview_stopped") < events.index("serial_shutdown")
    assert events.index("preview_stopped") < events.index("camera_destroy")
    assert events[-1] == "root_destroy"


def test_repeated_external_exit_does_not_reenter_teardown() -> None:
    app, events, _root = make_fake_exit_app(
        stop_results=[preview_clock.StopResult.STOPPED]
    )
    app.exit()
    app.exit()
    assert events.count("preview_stopped") == 1
    assert events.count("root_destroy") == 1
```

`make_fake_exit_app()` must monkeypatch `Window.tkmsg.askyesno` to return `True`, create `PokeControllerApp` with `object.__new__`, attach a manual fake root plus fake runner, menu, serial, camera, audio, preview, settings, and geometry hooks, and record the exact call order. The fake preview's `stopCapture()` pops the supplied `StopResult` values and records `preview_stop_pending` or `preview_stopped` before returning.

- [ ] **Step 2: Run the shutdown tests to prove RED**

Run:

```powershell
uv run --frozen pytest tests/test_preview_fps_e2e.py -k "exit_waits or repeated_external_exit" -q
```

Expected: FAIL because `exit()` still swallows `stopCapture()` failure, has no `_continue_exit()`, and destroys the root in the same call.

- [ ] **Step 3: Add explicit exit state without changing confirmation behavior**

Initialize in `_init_state()`:

```python
self._exit_requested = False
self._exit_phase = "idle"
self._exit_retry_after_id: str | None = None
```

Add `from ui.preview_clock import StopResult` to `Window.py`. Implement the external entry as:

```python
def exit(self) -> None:
    if self._closing:
        logger.debug("exit() は既に実行中のため無視しました")
        return
    if not tkmsg.askyesno("確認", "Poke Controllerを終了しますか？"):
        return
    self._closing = True
    self._exit_requested = True
    self._exit_phase = "stopping_preview"
    self.runner.notify_closing()
    self._cancel_exit_afters()
    self._continue_exit()
```

Implement `_cancel_exit_afters()` with the existing cancellation ownership and ordering:

```python
def _cancel_exit_afters(self) -> None:
    self.runner.cancel_watch()
    try:
        self._cancel_player_lamp_patrol()
    except Exception as exc:
        logger.warning(f"ランプ巡回の停止で例外: {exc}")
    for widget, after_id in (
        (self.logArea, self._display_after_id),
        (self.root, self._sash_after_id),
    ):
        if after_id is None:
            continue
        try:
            widget.after_cancel(after_id)
        except (tk.TclError, ValueError):
            pass
    self._display_after_id = None
    self._sash_after_id = None
```

This method runs before preview stop. It does not call `menu.closeAll()`, the runner's serial shutdown, or `PreviewClock.stop()`.

- [ ] **Step 4: Implement preview-first `_continue_exit()`**

Use this order:

```python
def _continue_exit(self) -> None:
    if not self._exit_requested:
        return
    if self._exit_phase == "stopping_preview":
        self._exit_retry_after_id = None
        result = self._stop_preview_for_exit()
        if result is not StopResult.STOPPED:
            self._exit_phase = "stopping_preview"
            self._exit_retry_after_id = self.root.after(
                50, self._continue_exit
            )
            return
        self._exit_phase = "preview_stopped"

    if self._exit_phase != "preview_stopped":
        return

    self._exit_phase = "stopping_services"
    if self.menu is not None:
        try:
            self.menu.closeAll()
        except Exception as exc:
            logger.warning(f"子窓を閉じるときに例外: {exc}")
    self.runner.shutdown(self.ser)
    self.serial.stop_keyboard()
    self.closingController()
    if self.serial.shutdown():
        print("Serial disconnected")
    self._save_exit_settings_and_stats()
    if self.camera is not None:
        self.camera.destroy()
    self._stop_audio()
    cv2.destroyAllWindows()
    sys.stdout = sys.__stdout__
    logger.debug("Stop Poke Controller")
    self._exit_phase = "root_destroyed"
    self.root.destroy()
```

Implement the three helpers called by `_continue_exit()`:

```python
def _stop_preview_for_exit(self) -> StopResult:
    preview = self.preview
    if preview is None:
        return StopResult.STOPPED
    for unbind in (preview.UnbindLeftClick, preview.UnbindRightClick):
        try:
            unbind()
        except Exception as exc:
            logger.warning(f"マウス操作の解除で例外: {exc}")
    try:
        return preview.stopCapture()
    except Exception as exc:
        logger.warning(f"映像の停止で例外: {exc}")
        return StopResult.PENDING


def _save_exit_settings_and_stats(self) -> None:
    try:
        WindowGeometry.rememberSash(self.log_pane, self.settings)
    except Exception as exc:
        logger.warning(f"仕切り位置の保存に失敗しました: {exc}")
    self._remember_geometry()
    try:
        self._save_settings()
    except Exception as exc:
        logger.warning(f"終了時の設定保存に失敗しました: {exc}")
    if self.runner.stats_dirty:
        CommandStats.save(self.command_stats, self.profile)


def _stop_audio(self) -> None:
    try:
        self._stop_meter()
    except Exception as exc:
        logger.warning(f"音声メーターの停止で例外: {exc}")
    try:
        self.audio_service.shutdown()
    except Exception as exc:
        logger.warning(f"音声の停止で例外: {exc}")
```

A preview-stop exception returns `PENDING`, so the root remains alive and the 50 ms teardown retry calls the idempotent stop path again.

- [ ] **Step 5: Preserve settings and teardown behavior after preview stops**

Keep the existing geometry, sash, settings, and command-stat writes after serial shutdown. Keep audio shutdown, OpenCV cleanup, stdout restore, and logger finalization in their current order after camera destruction. No code after `root.destroy()` may touch a widget.

- [ ] **Step 6: Run shutdown tests and compile the changed files**

Run:

```powershell
uv run --frozen pytest tests/test_preview_fps_e2e.py -k "exit_waits or repeated_external_exit" -q
uv run --frozen python -m py_compile SerialController/Window.py SerialController/GuiAssets.py SerialController/ui/preview_clock.py
```

Expected: PASS and successful compilation.

- [ ] **Step 7: Review checkpoint for Task 6**

Run:

```powershell
git diff --check -- SerialController/Window.py tests/test_preview_fps_e2e.py tests/preview_fps_support.py
git status --short
```

Confirm no root destroy or service teardown call can occur while phase is `stopping_preview`. Do not stage or commit.

---

## Task 7: Run the real-Tk native, fallback, diagnostic, and all-target matrices

**Files:**
- Modify only if a reproduced contract gap is found:
  - `SerialController/ui/preview_clock.py`
  - `SerialController/GuiAssets.py`
  - `SerialController/Window.py`
  - `tests/preview_fps_support.py`
  - `tests/test_preview_fps_e2e.py`
  - `tests/test_preview_clock.py`
- Do not modify firmware, serial, camera source, config values, or UI layout.

**Interfaces:**
- Consumes: the completed clock, CaptureArea, Window, and harness.
- Produces: retained evidence for default native 60 and 15 FPS, optional native 5, 30, and 45 FPS, fallback functional coverage for all five targets, diagnostic-only fractional rates, teardown, hashes, and cleanup.

- [ ] **Step 1: Run all deterministic preview contracts before the long E2E**

Run:

```powershell
uv run --frozen pytest tests/test_preview_clock.py tests/test_preview_fps_measurement_contract.py tests/test_preview_fps_internal_rate_contract.py -q
```

Expected: PASS. This proves all configured values 5, 15, 30, 45, and 60 satisfy the deterministic threshold logic and all diagnostic/fallback non-acceptance rules.

- [ ] **Step 2: Run the default native 60 FPS gate**

```powershell
$run60 = Join-Path $env:TEMP ("PokeCon-preview-fps-native-60-" + [guid]::NewGuid().ToString("N"))
$env:POKECON_RUN_PREVIEW_FPS_E2E = "1"
$env:POKECON_FPS_TEST_ID = "test_native_production_60hz_meets_hard_contract"
$env:POKECON_FPS_PHASE = "production"
$env:POKECON_FPS_RUN_CLASS = "native_production"
$env:POKECON_FPS_CONFIGURED_FPS = "60"
Remove-Item Env:POKECON_FPS_DIAGNOSTIC_RATE_HZ -ErrorAction SilentlyContinue
$env:POKECON_FPS_WARMUP_S = "10"
$env:POKECON_FPS_MEASUREMENT_S = "60"
$env:POKECON_FPS_SOURCE_HZ = "180"
$env:POKECON_FPS_EVIDENCE_DIR = $run60
$env:POKECON_FPS_CHILD_MODE = "normal"
$env:POKECON_FPS_WATCHDOG_TIMEOUT_S = "100"
uv run --frozen pytest tests/test_preview_fps_e2e.py::test_native_production_60hz_meets_hard_contract -q
if ($LASTEXITCODE -ne 0) { throw "native 60 FPS failed; evidence=$run60" }
```

Expected: PASS. `report.json` must show `clock_mode="high_resolution"`, `production_accepted=true`, `accepted=true`, dispatch and paste mean from 60.0 through 60.1 Hz, and both P1 values at least 59 Hz.

If native custom messages are not delivered by Tk, FAIL. Do not reinterpret the run as fallback success.

- [ ] **Step 3: Run the default native 15 FPS gate**

```powershell
$run15 = Join-Path $env:TEMP ("PokeCon-preview-fps-native-15-" + [guid]::NewGuid().ToString("N"))
$env:POKECON_FPS_TEST_ID = "test_native_production_15hz_meets_configured_contract"
$env:POKECON_FPS_CONFIGURED_FPS = "15"
$env:POKECON_FPS_EVIDENCE_DIR = $run15
uv run --frozen pytest tests/test_preview_fps_e2e.py::test_native_production_15hz_meets_configured_contract -q
if ($LASTEXITCODE -ne 0) { throw "native 15 FPS failed; evidence=$run15" }
```

Expected: PASS. Both means must be from 15.0 through 15.1 Hz and both P1 values at least 14 Hz.

- [ ] **Step 4: Run optional extended native targets 5, 30, and 45**

Use this loop, retaining each directory:

```powershell
foreach ($fps in 5, 30, 45) {
    $run = Join-Path $env:TEMP ("PokeCon-preview-fps-native-$fps-" + [guid]::NewGuid().ToString("N"))
    $env:POKECON_FPS_TEST_ID = "test_native_extended_target_meets_configured_contract"
    $env:POKECON_FPS_CONFIGURED_FPS = "$fps"
    $env:POKECON_FPS_EVIDENCE_DIR = $run
    uv run --frozen pytest tests/test_preview_fps_e2e.py::test_native_extended_target_meets_configured_contract -q
    if ($LASTEXITCODE -ne 0) { throw "native FPS $fps failed; evidence=$run" }
}
```

Expected: PASS for each target. The 5 FPS run also exercises active and idle transitions, set-FPS re-anchoring, and lifecycle in its functional assertions.

- [ ] **Step 5: Run fallback functional coverage for all five targets**

```powershell
foreach ($fps in 5, 15, 30, 45, 60) {
    $run = Join-Path $env:TEMP ("PokeCon-preview-fps-fallback-$fps-" + [guid]::NewGuid().ToString("N"))
    $env:POKECON_FPS_TEST_ID = "test_after_fallback_functional_at_configured_target"
    $env:POKECON_FPS_PHASE = "fallback"
    $env:POKECON_FPS_RUN_CLASS = "fallback_functional"
    $env:POKECON_FPS_CONFIGURED_FPS = "$fps"
    Remove-Item Env:POKECON_FPS_DIAGNOSTIC_RATE_HZ -ErrorAction SilentlyContinue
    $env:POKECON_FPS_WARMUP_S = "2"
    $env:POKECON_FPS_MEASUREMENT_S = "5"
    $env:POKECON_FPS_EVIDENCE_DIR = $run
    $env:POKECON_FPS_WATCHDOG_TIMEOUT_S = "20"
    uv run --frozen pytest tests/test_preview_fps_e2e.py::test_after_fallback_functional_at_configured_target -q
    if ($LASTEXITCODE -ne 0) { throw "fallback FPS $fps failed; evidence=$run" }
}
```

Expected: PASS for functional behavior. Every report must say `clock_mode="after"`, `functional_accepted=true`, `production_accepted=false`, and `accepted=false`.

- [ ] **Step 6: Run diagnostic-only 60.01, 60.03, and 60.06 references**

```powershell
foreach ($rate in 60.01, 60.03, 60.06) {
    $run = Join-Path $env:TEMP ("PokeCon-preview-fps-diagnostic-$rate-" + [guid]::NewGuid().ToString("N"))
    $env:POKECON_FPS_TEST_ID = "test_diagnostic_rate_is_reference_only"
    $env:POKECON_FPS_PHASE = "diagnostic"
    $env:POKECON_FPS_RUN_CLASS = "diagnostic"
    $env:POKECON_FPS_CONFIGURED_FPS = "60"
    $env:POKECON_FPS_DIAGNOSTIC_RATE_HZ = "$rate"
    $env:POKECON_FPS_WARMUP_S = "2"
    $env:POKECON_FPS_MEASUREMENT_S = "10"
    $env:POKECON_FPS_EVIDENCE_DIR = $run
    $env:POKECON_FPS_WATCHDOG_TIMEOUT_S = "25"
    uv run --frozen pytest tests/test_preview_fps_e2e.py::test_diagnostic_rate_is_reference_only -q
    if ($LASTEXITCODE -ne 0) { throw "diagnostic rate $rate failed; evidence=$run" }
}
```

Expected: PASS as diagnostic evidence. Reports must keep `configured_fps=60`, store the requested rate in `diagnostic_rate_hz`, and set `diagnostic_only=true`, `production_accepted=false`, and `accepted=false`.

- [ ] **Step 7: Audit source pins, artifact hashes, and required files**

Run against each native production evidence directory:

```powershell
param([Parameter(Mandatory=$true)][string]$EvidenceDir)
$sourceManifest = Get-Content (Join-Path $EvidenceDir "source_pins.json") -Raw | ConvertFrom-Json
$artifactManifest = Get-Content (Join-Path $EvidenceDir "artifact_hashes.json") -Raw | ConvertFrom-Json
if (-not $sourceManifest.unchanged) { throw "source pins changed" }
if (-not $sourceManifest.complete) { throw "source pin set is incomplete" }
$repoRoot = (Get-Location).Path
foreach ($entry in $sourceManifest.end) {
    $sourcePath = Join-Path $repoRoot $entry.path
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "missing source: $($entry.path)" }
    $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sourceHash -ne $entry.sha256) { throw "source hash mismatch: $($entry.path)" }
}
foreach ($entry in $artifactManifest.artifacts) {
    $path = Join-Path $EvidenceDir $entry.path
    if (-not (Test-Path -LiteralPath $path)) { throw "missing artifact: $($entry.path)" }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $entry.sha256) { throw "hash mismatch: $($entry.path)" }
    if ((Get-Item -LiteralPath $path).Length -ne $entry.bytes) {
        throw "size mismatch: $($entry.path)"
    }
}
```

Expected: no source change, missing artifact, hash mismatch, or size mismatch.

- [ ] **Step 8: Run the real-Tk `Window.exit()` teardown gate**

```powershell
$runTeardown = Join-Path $env:TEMP ("PokeCon-preview-fps-teardown-" + [guid]::NewGuid().ToString("N"))
$env:POKECON_FPS_TEST_ID = "test_window_exit_waits_for_preview_teardown"
$env:POKECON_FPS_PHASE = "teardown"
$env:POKECON_FPS_RUN_CLASS = "native_production"
$env:POKECON_FPS_CONFIGURED_FPS = "60"
Remove-Item Env:POKECON_FPS_DIAGNOSTIC_RATE_HZ -ErrorAction SilentlyContinue
$env:POKECON_FPS_WARMUP_S = "0"
$env:POKECON_FPS_MEASUREMENT_S = "1"
$env:POKECON_FPS_EVIDENCE_DIR = $runTeardown
$env:POKECON_FPS_WATCHDOG_TIMEOUT_S = "15"
uv run --frozen pytest tests/test_preview_fps_e2e.py::test_window_exit_waits_for_preview_teardown -q
if ($LASTEXITCODE -ne 0) { throw "Window.exit teardown failed; evidence=$runTeardown" }
```

Expected: PASS. `teardown.json` must show `root_alive_while_pending=true`, `stop_result="stopped"`, and `root_destroyed_after_stop=true`.

- [ ] **Step 9: Verify process, worker, source-thread, root, and serial cleanup**

For every evidence directory, confirm:

```text
watchdog.timed_out is false
watchdog.returncode is 0
teardown.preview_worker_alive is false
teardown.synthetic_source_alive is false
teardown.root_destroyed_after_stop is true
teardown.window_class_destroyed is true
teardown.window_class_unregistered is true
teardown.post_teardown_camera_read_count is 0
pending_after_ids is empty
```

The synthetic harness does not open a real serial port. For runtime QA, close the app and verify no child Python process, camera process, or serial session remains. Do not remove profile lock files manually.

- [ ] **Step 10: Perform manual runtime QA with the production app**

Run:

```powershell
task app
```

Verify manually:

1. The app starts with the existing configured FPS.
2. Selecting each of 5, 15, 30, 45, and 60 updates the preview without an `after(0)` or `after(1)` burst.
3. Toggling preview visibility enters 200 ms idle pacing and re-anchors at the configured FPS on return.
4. Save capture, filters, and mouse input still work.
5. Closing while preview is active keeps the root alive until native teardown completes, then closes serial and camera in order.
6. Reopening the app creates a new clock epoch and no stale native message dispatches.
7. Record physical camera, compositor, and scan-out observations as unverified. Do not claim them from synthetic evidence.

- [ ] **Step 11: Review checkpoint for Task 7**

Run:

```powershell
git diff --check
git status --short
```

Compare the final status with the saved initial status. Only the approved host production files, preview tests, and this plan may be additional changes. Do not stage or commit.

---

## Task 8: Run the full quality gates and final review

**Files:**
- Verify: all changed Python, test, and plan files.
- Do not modify: any unrelated dirty file.

**Interfaces:**
- Consumes: all implementation and evidence tasks.
- Produces: a verified uncommitted change set with no diagnostics, passing repository gates, valid retained evidence, and a final review decision.

- [ ] **Step 1: Run LSP diagnostics on every changed Python file**

Run diagnostics for:

```text
SerialController/ui/preview_clock.py
SerialController/GuiAssets.py
SerialController/Window.py
tests/test_preview_clock.py
tests/preview_fps_support.py
tests/test_preview_fps_e2e.py
tests/test_preview_fps_measurement_contract.py
tests/test_preview_fps_internal_rate_contract.py
```

Expected: zero errors and zero warnings attributable to this change.

- [ ] **Step 2: Run Python compilation**

```powershell
uv run --frozen python -m py_compile SerialController/ui/preview_clock.py SerialController/GuiAssets.py SerialController/Window.py tests/test_preview_clock.py tests/preview_fps_support.py tests/test_preview_fps_e2e.py tests/test_preview_fps_measurement_contract.py tests/test_preview_fps_internal_rate_contract.py
```

Expected: exit code 0.

- [ ] **Step 3: Run the complete repository gate**

```powershell
task ci
```

Expected: Ruff check, Ruff format check, mypy, bounds, user API checks, and the full test suite all pass. The final test count will exceed the current 787-test baseline because this plan adds tests.

- [ ] **Step 4: Verify the plan did not expand scope**

Run read-only checks:

```powershell
git diff --name-only
git diff -- SerialController/config.py SerialController/WindowUtils.py SerialController/ui/camera_panel.py
```

Expected: no production diff in `config.py`, `WindowUtils.py`, or `camera_panel.py`. No firmware, serial, transport, protocol, installer, or generated-file diff.

- [ ] **Step 5: Perform final code and evidence review**

Load `superpowers:requesting-code-review` and review:

```text
Public API matches the approved spec exactly
Worker never touches Tk or perf_counter_ns
Only one native timer and one worker are active
Control wake never publishes a tick
Latest-only accounting balances
PostMessage failure never retries
Fallback starts only after native cleanup
CaptureArea performs one main-thread tick
setFps re-anchors only on a real change
Window root survives StopResult.PENDING
All production FPS targets use F, F+0.1, and F-1
P1 remains at least 59 Hz at F=60
Fallback and diagnostic runs are never production accepted
All required artifacts and hashes exist
No physical-camera, compositor, or scan-out claim was added
No unrelated dirty file was changed
No commit was made
```

Address every review finding, then rerun only the affected focused test and the full `task ci` gate if production or test code changed. Do not rerun successful long E2E evidence unless the source hash changed.

- [ ] **Step 6: Final status checkpoint**

```powershell
git status --short
git log --oneline -1
```

Expected: HEAD is still `11b4ee805e07622eceb0baa89873fd6b076f1c49` unless the user separately changed the checkout before execution. The index is unchanged, no new commit exists, and the pre-existing dirty state remains intact apart from the approved uncommitted work.

## Completion Record Format

The executor must report:

```text
Plan file:
Branch and starting HEAD:
Pre-existing dirty paths:
Changed paths:
Focused RED evidence:
Current-after RED evidence directory:
Native 60 evidence directory and accepted result:
Native 15 evidence directory and accepted result:
Extended native evidence directories:
Fallback evidence directories and functional results:
Diagnostic evidence directories:
Source pin audit:
Artifact hash audit:
task ci result:
Python compile result:
LSP diagnostics result:
Runtime QA result:
Physical camera or compositor status: not verified
Commits made: none
Outstanding user decisions: none
```

## Post-Fix Boundary Record — Tcl Async Wake Removal (crash mitigation only)

### What changed

`PreviewClock.start()` no longer builds a Tcl async wake notifier. Wake delivery is
`PostMessageW` from the worker to the message-only window, drained on the Tk main
thread by the `after_idle` poll. The notifier machinery that this disabled was then
deleted rather than left dormant, because a dormant `_TclAsyncNotifier` plus a
`wake_notifier` seam is a reactivation footgun: the ctypes trampoline it installs is
the crash source, not an incidental detail.

Deleted from `SerialController/ui/preview_clock.py`:

- `class _TclAsyncNotifier` (the `Tcl_AsyncCreate` / `Tcl_AsyncMark` ctypes trampoline)
- `_TclAsyncProc = ctypes.CFUNCTYPE(None, ctypes.c_void_p)`
- `class _WakeNotifier(Protocol)`
- `PreviewClock._create_wake_notifier()` and `PreviewClock._close_wake_notifier()`
- `PreviewClock._wake_notifier` and `_WindowsNativeRuntime._wake_notifier` fields
- the `wake_notifier` parameter of `_create_native_runtime`,
  `_WindowsNativeRuntime.__init__`, and the `_WorkerContext` dataclass
- the notifier branch of `_worker_post_wake` and the notifier guard in
  `_schedule_native_wake_poll`

Deliberately preserved: `_worker_post_wake`'s `PostMessageW` branch, and
`_schedule_native_wake_poll`'s `after_idle`-then-`after(delay_ms)` fallback. WNDPROC
registration, source pins, the E2E resolution, and every acceptance threshold are
untouched.

### Evidence status

Retained and verifiable — post-fix native 60 run, no fatal:

- Artifact directory:
  `C:\Users\moilo\AppData\Local\Temp\PokeCon-postfix-native60-020e4776a3284168b6169c6f4a7f6c5d`
- `watchdog.returncode = 1` with `timed_out = false`; no fatal marker appears in the child log
- `dispatch p1_hz = 58.003967`

- `paste p1_hz = 56.773021`
- `production_accepted = false`

NOT retained — do not treat as established: the earlier "0 fatals in 14 notifier-free
probe runs" figure has no surviving artifact in this repository. It is a reported
observation, not a reproducible record. The code comment in `start()` therefore states
only the retained fact (the ctypes re-entry produced process-fatal exits) and carries
no run count.

### Still-red boundary: the 60 Hz P1 contract is NOT fixed

The crash mitigation is complete; the FPS contract is not. The gate at `F=60` requires
`mean_hz >= 60.0` and endpoint-complete `p1_hz >= 59`. The retained post-fix artifact
reports dispatch `p1_hz = 58.004` and paste `p1_hz = 56.773`, both below the 59 Hz
floor, hence `production_accepted = false`. The binding constraint is paste-side cost
at 1280x720, not the wake path. No threshold, mean window, or gap bound was relaxed
to make this pass.

Soak verification of the crash mitigation is now complete for the shipped source: three sequential actual-code production runs, no fatal, with manifests independently verified:

- `C:\Users\moilo\AppData\Local\Temp\PokeCon-soak-postfix-1-78d1ada6ac5d4ee89e8fbc475aad780f`
- `C:\Users\moilo\AppData\Local\Temp\PokeCon-soak-postfix-2-0ca9467e9fce4154aae52ed07f1a9f03`
- `C:\Users\moilo\AppData\Local\Temp\PokeCon-soak-postfix-3-e482be155fba4c3d9a60b2860c24c584`

All three were ordinary acceptance RED (dispatch P1 `58.116`, `55.577`, `57.378`; paste P1 `57.028`, `53.750`, `55.092`), not fatals. This verifies the crash mitigation's shipped build; it does not change the still-red performance contract.

The post-deletion teardown proof is re-pinned to the shipped source at `C:\Users\moilo\AppData\Local\Temp\PokeCon-postfix-teardown-final-9570c59712f244b785622a71bbda784f`: `pending_teardown_proven=true`, `root_alive_while_pending=true`, and `preview_clock.py` SHA-256 `ba045fde4d23…`.

After the final review-only test cleanup, the exact-working-tree evidence is additionally retained at `C:\Users\moilo\AppData\Local\Temp\PokeCon-final-native60-8f698372e2734992b7310c5d39f8535b` (no fatal, RED, dispatch P1 `53.262`, paste P1 `52.746`) and `C:\Users\moilo\AppData\Local\Temp\PokeCon-final-teardown-f11adeda968a4ca8a504f7cda2a0131f` (pending teardown proven). Both manifests and source pins verified; final test-file SHA-256 is `48aa32e6a9df…`.

