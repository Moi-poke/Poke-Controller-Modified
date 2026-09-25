# noqa: E501  # noqa: SIZE_OK - approved single-file Task 3 E2E matrix.
"""Opt-in real-Tk preview FPS E2E entry points and child-admission contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import preview_fps_support as support
import pytest
from ui import preview_clock

_PREVIEW_TEST_ID = "test_native_production_60hz_meets_hard_contract"


def _set_gated_environment(
    monkeypatch: pytest.MonkeyPatch,
    evidence_dir: Path,
    child_mode: str,
) -> None:
    values = {
        "POKECON_RUN_PREVIEW_FPS_E2E": "1",
        "POKECON_FPS_TEST_ID": _PREVIEW_TEST_ID,
        "POKECON_FPS_PHASE": "production",
        "POKECON_FPS_RUN_CLASS": "native_production",
        "POKECON_FPS_CONFIGURED_FPS": "60",
        "POKECON_FPS_DIAGNOSTIC_RATE_HZ": "",
        "POKECON_FPS_WARMUP_S": "10",
        "POKECON_FPS_MEASUREMENT_S": "60",
        "POKECON_FPS_SOURCE_HZ": "180",
        "POKECON_FPS_EVIDENCE_DIR": str(evidence_dir),
        "POKECON_FPS_CHILD_MODE": child_mode,
        "POKECON_FPS_WATCHDOG_TIMEOUT_S": "100",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("POKECON_FPS_CLAIM_TOKEN", raising=False)


def _set_teardown_environment(
    monkeypatch: pytest.MonkeyPatch,
    evidence_dir: Path,
    *,
    warmup_s: str,
    measurement_s: str,
) -> None:
    values = {
        "POKECON_RUN_PREVIEW_FPS_E2E": "1",
        "POKECON_FPS_TEST_ID": "test_window_exit_waits_for_preview_teardown",
        "POKECON_FPS_PHASE": "teardown",
        "POKECON_FPS_RUN_CLASS": "native_production",
        "POKECON_FPS_CONFIGURED_FPS": "60",
        "POKECON_FPS_DIAGNOSTIC_RATE_HZ": "",
        "POKECON_FPS_WARMUP_S": warmup_s,
        "POKECON_FPS_MEASUREMENT_S": measurement_s,
        "POKECON_FPS_SOURCE_HZ": "180",
        "POKECON_FPS_EVIDENCE_DIR": str(evidence_dir),
        "POKECON_FPS_CHILD_MODE": "normal",
        "POKECON_FPS_WATCHDOG_TIMEOUT_S": "10",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _expected_execution_contract(evidence_dir: Path) -> dict[str, Any]:
    return {
        "phase": "production",
        "run_class": "native_production",
        "configured_fps": 60,
        "diagnostic_rate_hz": None,
        "source_hz": 180,
        "warmup_s": 10.0,
        "measurement_s": 60.0,
        "evidence_dir": str(evidence_dir),
        "test_id": _PREVIEW_TEST_ID,
        "watchdog_timeout_s": 100.0,
    }


def _source_pin_payload() -> list[dict[str, str | int | bool]]:
    return support._source_pin_rows(support._source_pins())


def _valid_handoff_payload(
    evidence_dir: Path,
    token: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "claim_token": token,
        "execution_contract": _expected_execution_contract(evidence_dir),
        "source_pins": _source_pin_payload(),
    }


def _write_handoff(evidence_dir: Path, payload: dict[str, Any]) -> None:
    (evidence_dir / "parent-handoff.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _forbid_real_harness_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_start(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("RealCaptureHarness.start must not run")

    monkeypatch.setattr(support.RealCaptureHarness, "start", forbidden_start)


def _expect_child_rejection_before_start(
    monkeypatch: pytest.MonkeyPatch,
    expected_field: str,
) -> None:
    _forbid_real_harness_start(monkeypatch)
    with pytest.raises(support.ConfigError) as exc_info:
        support._run_child_from_environment()
    assert exc_info.value.field == expected_field


def _accept_child_without_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        support,
        "_run_measurement",
        lambda *_args: {
            "accepted": True,
            "functional_accepted": True,
            "test_passed": True,
        },
    )


def test_parent_claims_unique_exact_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two otherwise identical parent runs use separate directories.
    evidence_dirs = [tmp_path / "run-a", tmp_path / "run-b"]
    monkeypatch.setattr(support, "_run_parent", lambda *_args: None)

    # When: each parent claims its directory without starting a real child.
    tokens: list[str] = []
    for evidence_dir in evidence_dirs:
        _set_gated_environment(monkeypatch, evidence_dir, "normal")
        support.run_gated_test(_PREVIEW_TEST_ID)
        payload = json.loads(
            (evidence_dir / "parent-handoff.json").read_text(encoding="utf-8")
        )
        assert payload["execution_contract"] == _expected_execution_contract(
            evidence_dir
        )
        token = payload["claim_token"]
        assert isinstance(token, str)
        tokens.append(token)

    # Then: ownership tokens are distinct and the configured target stays 60.
    assert (
        tokens[0] != tokens[1] and payload["execution_contract"]["configured_fps"] == 60
    )


def test_child_admission_requires_parent_claimed_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: child state exists without a parent-created directory.
    evidence_dir = tmp_path / "missing-parent-directory"
    _set_gated_environment(monkeypatch, evidence_dir, "child")
    monkeypatch.setenv("POKECON_FPS_CLAIM_TOKEN", "parent-token")

    # When/Then: admission fails before real harness startup.
    _expect_child_rejection_before_start(monkeypatch, "POKECON_FPS_EVIDENCE_DIR")


def test_child_admission_requires_parent_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a claimed directory without the handoff artifact.
    evidence_dir = tmp_path / "missing-handoff"
    evidence_dir.mkdir()
    _set_gated_environment(monkeypatch, evidence_dir, "child")
    monkeypatch.setenv("POKECON_FPS_CLAIM_TOKEN", "parent-token")

    # When/Then: the absent handoff is rejected before startup.
    _expect_child_rejection_before_start(monkeypatch, "parent-handoff.json")


def test_child_admission_rejects_missing_or_stale_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid parent handoff for a child with no matching token.
    evidence_dir = tmp_path / "token-mismatch"
    evidence_dir.mkdir()
    _write_handoff(evidence_dir, _valid_handoff_payload(evidence_dir, "parent-token"))
    _set_gated_environment(monkeypatch, evidence_dir, "child")

    # When/Then: both absent and stale credentials fail before startup.
    _expect_child_rejection_before_start(monkeypatch, "POKECON_FPS_CLAIM_TOKEN")
    monkeypatch.setenv("POKECON_FPS_CLAIM_TOKEN", "stale-token")
    _expect_child_rejection_before_start(monkeypatch, "POKECON_FPS_CLAIM_TOKEN")


def test_child_admission_rejects_contract_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the parent handoff claims 61 seconds while the child claims 60.
    evidence_dir = tmp_path / "contract-mismatch"
    evidence_dir.mkdir()
    payload = _valid_handoff_payload(evidence_dir, "parent-token")
    payload["execution_contract"] = {
        **_expected_execution_contract(evidence_dir),
        "measurement_s": 61.0,
    }
    _write_handoff(evidence_dir, payload)
    _set_gated_environment(monkeypatch, evidence_dir, "child")
    monkeypatch.setenv("POKECON_FPS_CLAIM_TOKEN", "parent-token")

    # When/Then: the immutable execution contract mismatch is rejected.
    _expect_child_rejection_before_start(monkeypatch, "parent-handoff.json")


def test_child_admission_rejects_source_pin_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one pinned source hash differs from the current checkout.
    evidence_dir = tmp_path / "source-pin-mismatch"
    evidence_dir.mkdir()
    payload = _valid_handoff_payload(evidence_dir, "parent-token")
    payload["source_pins"] = json.loads(json.dumps(payload["source_pins"]))
    payload["source_pins"][0]["sha256"] = "0" * 64
    _write_handoff(evidence_dir, payload)
    _set_gated_environment(monkeypatch, evidence_dir, "child")
    monkeypatch.setenv("POKECON_FPS_CLAIM_TOKEN", "parent-token")

    # When/Then: source drift is rejected before native or fallback startup.
    _expect_child_rejection_before_start(monkeypatch, "source_pins")


def test_first_child_claims_ownership_and_duplicate_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one valid parent handoff and token.
    evidence_dir = tmp_path / "first-child"
    evidence_dir.mkdir()
    _write_handoff(evidence_dir, _valid_handoff_payload(evidence_dir, "parent-token"))
    _set_gated_environment(monkeypatch, evidence_dir, "child")
    monkeypatch.setenv("POKECON_FPS_CLAIM_TOKEN", "parent-token")
    _accept_child_without_gui(monkeypatch)

    # When: the first child enters and a duplicate presents the same token.
    support._run_child_from_environment()
    with pytest.raises(support.ConfigError) as exc_info:
        support._run_child_from_environment()

    # Then: the duplicate cannot execute and exclusive ownership is retained.
    assert exc_info.value.field == "child-owner.json"
    marker = json.loads((evidence_dir / "child-owner.json").read_text(encoding="utf-8"))
    assert marker["source_pins"] == _source_pin_payload()


def test_child_environment_does_not_overwrite_parent_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a parent-owned environment sentinel.
    evidence_dir = tmp_path / "distinct-environment"
    evidence_dir.mkdir()
    parent_environment = {"owner": "parent", "sentinel": 1}
    (evidence_dir / "environment.json").write_text(
        json.dumps(parent_environment),
        encoding="utf-8",
    )
    _write_handoff(evidence_dir, _valid_handoff_payload(evidence_dir, "parent-token"))
    _set_gated_environment(monkeypatch, evidence_dir, "child")
    monkeypatch.setenv("POKECON_FPS_CLAIM_TOKEN", "parent-token")
    _accept_child_without_gui(monkeypatch)

    # When: the admitted child records its own environment.
    support._run_child_from_environment()

    # Then: parent ownership remains intact and child evidence is separate.
    assert (
        json.loads((evidence_dir / "environment.json").read_text(encoding="utf-8"))
        == parent_environment
    )
    assert (evidence_dir / "child-environment.json").is_file()


def test_real_harness_instruments_clock_created_by_start_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a CaptureArea lifecycle that creates its clock inside startCapture.
    import tkinter as tk

    import GuiAssets

    class FakeVariable:
        def __init__(self, *, master: Any, value: bool) -> None:
            _ = master
            self._value = value

        def get(self) -> bool:
            return self._value

        def set(self, value: bool) -> None:
            self._value = value

    class FakeRoot:
        def __init__(self) -> None:
            self.destroyed = False
            self.tk = SimpleNamespace(call=lambda *_args: [])

        def title(self, _value: str) -> None:
            return None

        def geometry(self, _value: str) -> None:
            return None

        def update_idletasks(self) -> None:
            return None

        def destroy(self) -> None:
            self.destroyed = True

        def winfo_exists(self) -> int:
            return int(not self.destroyed)

    class FakeClock:
        def mode(self) -> str:
            return "high_resolution"

        def health_snapshot(self) -> dict[str, int | str | bool]:
            return {
                "last_sequence": 0,
                "epoch": 1,
                "generation": 1,
                "configured_fps": 60,
                "worker_thread_native_id": 999,
                "running": True,
            }

    class FakePhoto:
        def paste(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class FakeArea:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self._photo = FakePhoto()
            self._preview_clock: FakeClock | None = None
            self.start_capture_calls = 0
            self._capturing = False

        def pack(self, **_kwargs: Any) -> None:
            return None

        def after(self, _delay_ms: int, _callback: Any) -> str:
            return "after-1"

        def after_cancel(self, _after_id: str) -> None:
            return None

        def _drawFrame(self, _frame: Any, _sequence: int | None = None) -> None:
            return None

        def capture(self) -> None:
            return None

        def _dispatch_tick(self) -> None:
            return None

        def startCapture(self) -> None:
            self.start_capture_calls += 1
            self._capturing = True
            self._preview_clock = FakeClock()
            setattr(self, "_dispatch_tick", self._native_dispatch)

        def _native_dispatch(self) -> None:
            return None

        def stopCapture(self) -> None:
            self._capturing = False

    class FakeSource:
        def __init__(self, _source_hz: int) -> None:
            self.camera_size = (1280, 720)

        def start(self) -> None:
            return None

        def stop(self) -> bool:
            return True

        def is_alive(self) -> bool:
            return False

        def producer_thread_native_id(self) -> int:
            return 998

        def camera_summary(self) -> dict[str, int | bool | str]:
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
            }

    monkeypatch.setattr(tk, "Tk", FakeRoot)
    monkeypatch.setattr(tk, "BooleanVar", FakeVariable)
    monkeypatch.setattr(GuiAssets, "CaptureArea", FakeArea)
    monkeypatch.setattr(support, "SyntheticFrameSource", FakeSource)
    config = support.E2EConfig(
        phase=support.RunPhase.PRODUCTION,
        run_class=support.RunClass.NATIVE_PRODUCTION,
        configured_fps=60,
        diagnostic_rate_hz=None,
        warmup_s=1.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.CHILD,
        test_id="test_native_production_60hz_meets_hard_contract",
        watchdog_timeout_s=10.0,
    )
    harness = support.RealCaptureHarness(config)

    # When: the real harness start method runs the fake area lifecycle.
    harness.start()
    try:
        # Then: instrumentation sees and wraps the post-start native dispatch.
        assert harness.area is not None
        assert harness.area.start_capture_calls == 1
        assert harness.clock is harness.area._preview_clock
        assert harness.instrumentation is not None
        assert harness.instrumentation.target_name == "_dispatch_tick"
    finally:
        harness.close()


def test_teardown_clock_selection_uses_area_owned_clock_after_start() -> None:
    # Given: startCapture replaces a pre-existing clock with the owned clock.
    class StaleClock:
        pass

    class OwnedClock:
        pass

    class LifecycleArea:
        def __init__(self) -> None:
            self._preview_clock: Any = StaleClock()
            self.start_calls = 0

        def startCapture(self) -> None:
            self.start_calls += 1
            self._preview_clock = OwnedClock()

    area = LifecycleArea()

    # When: the harness asks for the area-owned clock after startup.
    clock = support._start_capture_and_get_clock(area)

    # Then: it returns the replacement clock, never the stale pre-start object.
    assert area.start_calls == 1
    assert clock is area._preview_clock
    assert isinstance(clock, OwnedClock)


def test_preview_clock_source_pin_matches_repository_precondition() -> None:
    # Given/When: the source set is pinned against the current repository state.
    rows = _source_pin_payload()
    preview_clock = next(
        row for row in rows if row["path"] == "SerialController/ui/preview_clock.py"
    )
    preview_clock_path = support.REPO_ROOT / "SerialController/ui/preview_clock.py"

    # Then: absence is incomplete before Task 4; a later real file is complete.
    if preview_clock_path.is_file():
        byte_count = preview_clock["bytes"]
        sha256 = preview_clock["sha256"]
        assert preview_clock["exists"] is True
        assert isinstance(byte_count, int)
        assert isinstance(sha256, str)
        assert byte_count > 0
        assert len(sha256) == 64
        assert support.source_pins_complete(support._source_pins()) is True
    else:
        assert preview_clock["exists"] is False
        assert preview_clock["bytes"] == 0
        assert preview_clock["sha256"] == ""
        assert support.source_pins_complete(support._source_pins()) is False


def test_diagnostic_gate_preserves_configured_fps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: diagnostic rate 60.03 with its dedicated test identity.
    monkeypatch.setenv("POKECON_RUN_PREVIEW_FPS_E2E", "1")
    monkeypatch.setenv("POKECON_FPS_TEST_ID", "test_diagnostic_rate_is_reference_only")
    monkeypatch.setenv("POKECON_FPS_PHASE", "diagnostic")
    monkeypatch.setenv("POKECON_FPS_RUN_CLASS", "diagnostic")
    monkeypatch.setenv("POKECON_FPS_CONFIGURED_FPS", "60")
    monkeypatch.setenv("POKECON_FPS_DIAGNOSTIC_RATE_HZ", "60.03")
    monkeypatch.setenv("POKECON_FPS_WARMUP_S", "2")
    monkeypatch.setenv("POKECON_FPS_MEASUREMENT_S", "5")
    monkeypatch.setenv("POKECON_FPS_SOURCE_HZ", "180")
    monkeypatch.setenv("POKECON_FPS_EVIDENCE_DIR", str(tmp_path / "diagnostic"))
    monkeypatch.setenv("POKECON_FPS_CHILD_MODE", "normal")
    monkeypatch.setenv("POKECON_FPS_WATCHDOG_TIMEOUT_S", "15")

    # When/Then: the private diagnostic rate does not overwrite configured FPS.
    config = support.load_config("test_diagnostic_rate_is_reference_only")
    assert config is not None
    assert config.configured_fps == 60
    assert config.diagnostic_rate_hz == 60.03
    assert config.run_class is support.RunClass.DIAGNOSTIC


def test_native_gate_skips_before_tk_on_non_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a native production gate on a non-Windows platform.
    _set_gated_environment(monkeypatch, tmp_path / "not-created", "normal")
    _forbid_real_harness_start(monkeypatch)
    monkeypatch.setattr(support, "_native_platform_available", lambda: False)

    # When/Then: the run skips instead of substituting fallback or creating Tk.
    with pytest.raises(pytest.skip.Exception):
        support.run_gated_test(_PREVIEW_TEST_ID)
    assert not (tmp_path / "not-created").exists()


def test_teardown_phase_loader_allows_zero_windows_and_routes_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the required teardown environment with zero-duration windows.
    _set_teardown_environment(
        monkeypatch,
        tmp_path / "teardown",
        warmup_s="0",
        measurement_s="0",
    )
    routed_report = {"test_passed": True, "accepted": False}
    monkeypatch.setattr(
        support,
        "_run_teardown_probe",
        lambda _config, _writer: routed_report,
    )

    # When: the real loader parses the contract and selects the teardown body.
    config = support.load_config("test_window_exit_waits_for_preview_teardown")
    assert config is not None
    writer = support.EvidenceWriter(tmp_path, role="child")

    # Then: zero windows are preserved and only the pending probe is selected.
    assert config.warmup_s == 0.0
    assert config.measurement_s == 0.0
    assert support._run_measurement(config, writer) is routed_report


@pytest.mark.parametrize("duration_field", ["WARMUP", "MEASUREMENT"])
@pytest.mark.parametrize("invalid_duration", ["-1", "nan", "inf", "-inf"])
def test_teardown_phase_loader_rejects_negative_and_nonfinite_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration_field: str,
    invalid_duration: str,
) -> None:
    # Given: a real teardown config with an invalid warmup or measurement duration.
    warmup_s = invalid_duration if duration_field == "WARMUP" else "0"
    measurement_s = invalid_duration if duration_field == "MEASUREMENT" else "0"
    _set_teardown_environment(
        monkeypatch,
        tmp_path / f"teardown-{duration_field}-{invalid_duration}",
        warmup_s=warmup_s,
        measurement_s=measurement_s,
    )

    # When/Then: load_config rejects the invalid duration field.
    with pytest.raises(support.ConfigError) as exc_info:
        support.load_config("test_window_exit_waits_for_preview_teardown")
    assert exc_info.value.field == f"POKECON_FPS_{duration_field}_S"


def test_five_hz_warmup_records_show_false_transition(tmp_path: Path) -> None:
    # Given: a five-Hz area, idle variable, and root that records callbacks.
    class FakeArea:
        def __init__(self) -> None:
            self.fps_values: list[int] = []

        def setFps(self, fps: int) -> None:
            self.fps_values.append(fps)

    class FakeVariable:
        def __init__(self) -> None:
            self.values: list[bool] = []

        def set(self, value: bool) -> None:
            self.values.append(value)

    class FakeRoot:
        def __init__(self) -> None:
            self.callbacks: list[Any] = []

        def after(self, _delay_ms: int, callback: Any) -> str:
            self.callbacks.append(callback)
            return f"after-{len(self.callbacks)}"

    class FakeInstrumentation(support.PasteInstrumentation):
        def __init__(self) -> None:
            self.callback_schedules: list[support.CallbackSchedule] = []

    config = support.E2EConfig(
        phase=support.RunPhase.FALLBACK,
        run_class=support.RunClass.FALLBACK_FUNCTIONAL,
        configured_fps=5,
        diagnostic_rate_hz=None,
        warmup_s=1.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.CHILD,
        test_id="test_after_fallback_functional_at_configured_target",
        watchdog_timeout_s=10.0,
    )
    harness = support.RealCaptureHarness(config)
    harness.area = FakeArea()
    harness.is_show = FakeVariable()
    harness.instrumentation = FakeInstrumentation()
    harness.clock = None
    harness.health_rows = []
    root = FakeRoot()

    # When: the five-Hz warmup enters its idle phase.
    harness._complete_five_hz_idle(root)

    # Then: both set_fps(5) and show=false are explicit health transitions.
    transitions = [row["transition"] for row in harness.health_rows]
    assert transitions == ["set_fps_5", "show_false"]
    assert harness.is_show.values == [False]


def test_five_hz_idle_warmup_progresses_without_area_after_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a five-Hz idle warmup with no area-level after records.
    class FakeArea:
        def setFps(self, _fps: int) -> None:
            return None

    class FakeVariable:
        def __init__(self) -> None:
            self.values: list[bool] = []

        def set(self, value: bool) -> None:
            self.values.append(value)

    class FakeRoot:
        def __init__(self) -> None:
            self.callbacks: list[tuple[int, Callable[..., None]]] = []

        def after(self, delay_ms: int, callback: Callable[..., None]) -> str:
            self.callbacks.append((delay_ms, callback))
            return f"after-{len(self.callbacks)}"

    class FakeInstrumentation(support.PasteInstrumentation):
        def __init__(self) -> None:
            self.callback_schedules: list[support.CallbackSchedule] = []

    now_ns = 0

    def fake_now() -> int:
        return now_ns

    monkeypatch.setattr(support.time, "perf_counter_ns", fake_now)
    harness = support.RealCaptureHarness(
        support.E2EConfig(
            phase=support.RunPhase.FALLBACK,
            run_class=support.RunClass.FALLBACK_FUNCTIONAL,
            configured_fps=5,
            diagnostic_rate_hz=None,
            warmup_s=0.0,
            measurement_s=1.0,
            evidence_dir=Path("C:/five-hz-idle-test"),
            source_hz=180,
            child_mode=support.ChildMode.CHILD,
            test_id="test_after_fallback_functional_at_configured_target",
            watchdog_timeout_s=10.0,
        )
    )
    harness.area = FakeArea()
    harness.is_show = FakeVariable()
    harness.instrumentation = FakeInstrumentation()
    harness.clock = None
    harness.health_rows = []
    root = FakeRoot()

    # When: two idle-check turns elapse without area.after callbacks.
    harness._complete_five_hz_idle(root)
    for _ in range(2):
        assert root.callbacks
        delay_ms, callback = root.callbacks.pop(0)
        assert delay_ms == 50
        now_ns += 250_000_000
        callback()

    # Then: the measurement phase is scheduled after the idle interval.
    assert root.callbacks
    assert root.callbacks[0][0] == 200


def test_native_dispatch_instrumentation_is_installed_before_clock_binding() -> None:
    class FakePhoto:
        def paste(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class FakeArea:
        def __init__(self) -> None:
            self._photo = FakePhoto()
            self._preview_clock = None
            self.bound_dispatch: Any = None

        def after(self, delay_ms: int, _callback: Any) -> str:
            return f"after-{delay_ms}"

        def _drawFrame(self, _frame: Any, _sequence: int | None = None) -> None:
            return None

        def _dispatch_tick(self) -> preview_clock.DispatchResult:
            return preview_clock.DispatchResult(schedule="active")

        def startCapture(self) -> None:
            self.bound_dispatch = self._dispatch_tick

    area = FakeArea()
    original_dispatch = area._dispatch_tick
    instrumentation = support.PasteInstrumentation(
        area,
        configured_fps=60,
        dispatch_target="_dispatch_tick",
    )
    area.startCapture()

    assert area.bound_dispatch is not original_dispatch
    area.bound_dispatch()

    assert len(instrumentation.dispatch_records) == 1
    assert instrumentation.dispatch_records[0].schedule == "active"
    instrumentation.close()


def test_exit_waits_for_preview_stop_before_any_service_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a confirmed app whose preview clock needs one internal retry.
    app, events, root = support.make_fake_exit_app(
        monkeypatch,
        stop_results=[
            preview_clock.StopResult.PENDING,
            preview_clock.StopResult.STOPPED,
        ],
    )

    # When: the external exit entry starts and its scheduled retry runs.
    app.exit()

    # Then: pending preview stop keeps the root and every later subsystem alive.
    assert events[:3] == ["unbind_left", "unbind_right", "preview_stop_pending"]
    assert root.destroy_calls == 0
    assert root.scheduled_delays == [50]
    assert "runner_shutdown" not in events
    assert "serial_shutdown" not in events
    assert "camera_destroy" not in events

    root.run_next()

    # Then: all teardown follows the confirmed preview stop in exact order.
    assert events == [
        "unbind_left",
        "unbind_right",
        "preview_stop_pending",
        "unbind_left",
        "unbind_right",
        "preview_stopped",
        "child_windows_closed",
        "runner_shutdown",
        "keyboard_stop",
        "controller_closing",
        "serial_shutdown",
        "sash_saved",
        "geometry_saved",
        "settings_saved",
        "stats_saved",
        "camera_destroy",
        "audio_meter_stop",
        "audio_shutdown",
        "cv2_destroy",
        "root_destroy",
    ]


def test_repeated_external_exit_does_not_reenter_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an app whose preview stops immediately.
    app, events, root = support.make_fake_exit_app(
        monkeypatch,
        stop_results=[preview_clock.StopResult.STOPPED],
    )

    # When: the same external entry is requested twice.
    app.exit()
    events_after_first_exit = list(events)
    app.exit()

    # Then: the second request cannot repeat preview or root teardown.
    assert events == events_after_first_exit
    assert events.count("preview_stopped") == 1
    assert root.destroy_calls == 1


def test_measurement_keeps_root_alive_until_pending_preview_stop_completes(
    tmp_path: Path,
) -> None:
    class FakeRoot:
        def __init__(self) -> None:
            self.callbacks: list[tuple[int, Any]] = []
            self.quit_calls = 0

        def after(self, delay_ms: int, callback: Any) -> str:
            self.callbacks.append((delay_ms, callback))
            return f"after-{len(self.callbacks)}"

        def quit(self) -> None:
            self.quit_calls += 1

    class FakeClock:
        def _end_evidence_window(self) -> dict[str, bool]:
            return {"latest_only_accounting_ok": True}

        def health_snapshot(self) -> dict[str, int | str | bool]:
            return {}

    class FakeArea:
        def __init__(self) -> None:
            self.stop_calls = 0
            self.results = [
                preview_clock.StopResult.PENDING,
                preview_clock.StopResult.STOPPED,
            ]

        def stopCapture(self) -> preview_clock.StopResult:
            self.stop_calls += 1
            return self.results.pop(0)

    config = support.E2EConfig(
        phase=support.RunPhase.PRODUCTION,
        run_class=support.RunClass.NATIVE_PRODUCTION,
        configured_fps=60,
        diagnostic_rate_hz=None,
        warmup_s=0.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.CHILD,
        test_id="test_native_production_60hz_meets_hard_contract",
        watchdog_timeout_s=10.0,
    )
    harness = support.RealCaptureHarness(config)
    root = FakeRoot()
    area = FakeArea()
    harness.root = root
    harness.area = area
    harness.clock = FakeClock()
    harness.measurement_window = support.Window(0, 0)

    # Given: native preview stop is still pending at measurement end.
    # When: the measurement completion callback runs.
    harness._measurement_tick()

    # Then: the root stays alive and a retry is scheduled.
    assert area.stop_calls == 1
    assert root.quit_calls == 0
    assert root.callbacks[0][0] == 50

    root.callbacks[0][1]()

    # Then: the confirmed stop still yields one post-stop event-loop turn.
    assert area.stop_calls == 2
    assert root.quit_calls == 0
    assert root.callbacks[1][0] == 50

    root.callbacks[1][1]()

    # Then: the root quits only after the post-stop boundary.
    assert root.quit_calls == 1


def _assert_performance_reference_recorded(test_id: str) -> None:
    """A completed run must publish its cadence as data, not as a verdict."""
    support.run_gated_test(test_id)
    config = support.load_config(test_id)
    assert config is not None
    report = json.loads(
        (config.evidence_dir / "report.json").read_text(encoding="utf-8")
    )

    # The reference is present, serialised, and reports the measured truth.
    assert isinstance(report["performance_reference"], dict)
    assert isinstance(report["performance_reference"]["within_reference"], bool)
    assert report["dispatch_summary"]["mean_hz"] >= 0.0
    assert report["dispatch_summary"]["p1_hz"] >= 0.0

    # Both skip diagnostics are recorded as independent integers.
    for field in (
        "period_skipped_count",
        "blit_skipped_no_new_frame",
        "suppressed_blit_interval_count",
    ):
        value = report[field]
        assert isinstance(value, int) and not isinstance(value, bool) and value >= 0


def test_native_production_60hz_records_performance_reference() -> None:
    _assert_performance_reference_recorded(
        "test_native_production_60hz_meets_hard_contract"
    )


def test_native_production_15hz_records_performance_reference() -> None:
    _assert_performance_reference_recorded(
        "test_native_production_15hz_meets_configured_contract"
    )


def test_native_production_5hz_records_performance_reference() -> None:
    _assert_performance_reference_recorded(
        "test_native_production_5hz_records_performance_reference"
    )


def test_native_extended_target_records_performance_reference() -> None:
    _assert_performance_reference_recorded(
        "test_native_extended_target_meets_configured_contract"
    )


def test_after_fallback_functional_at_configured_target() -> None:
    support.run_gated_test("test_after_fallback_functional_at_configured_target")


def test_diagnostic_rate_is_reference_only() -> None:
    support.run_gated_test("test_diagnostic_rate_is_reference_only")


def test_window_exit_waits_for_preview_teardown() -> None:
    support.run_gated_test("test_window_exit_waits_for_preview_teardown")
