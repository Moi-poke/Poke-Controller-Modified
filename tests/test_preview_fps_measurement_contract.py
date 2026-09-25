# noqa: E501  # noqa: SIZE_OK - approved single-file Task 3 contract matrix.
"""Deterministic contracts for preview FPS measurement and evidence."""

from __future__ import annotations

import json
import tkinter as tk
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import preview_fps_support as support
import pytest

_ONE_SECOND_NS = 1_000_000_000
_START_NS = _ONE_SECOND_NS


def _write_valid_artifact_manifest(directory: Path) -> dict[str, Any]:
    for name in support.REQUIRED_EVIDENCE_FILES:
        content = "x" if name == "report.json" else "{}\n"
        (directory / name).write_text(content, encoding="utf-8")
    support.write_artifact_manifest(directory)
    manifest_path = directory / support.ARTIFACT_MANIFEST_FILE
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_artifact_manifest_payload(
    directory: Path,
    payload: Mapping[str, Any],
) -> None:
    (directory / support.ARTIFACT_MANIFEST_FILE).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _passing_cadence(configured_fps: int) -> support.CadenceResult:
    return support.CadenceResult(
        count=configured_fps * 60,
        mean_hz=float(configured_fps),
        p1_hz=float(configured_fps - 1),
    )


def _sub_target_cadence(configured_fps: int) -> support.CadenceResult:
    """A cadence that breaches the mean and P1 bounds of its own contract."""
    return support.CadenceResult(
        count=configured_fps - 1,
        mean_hz=float(configured_fps) - 1.0,
        p1_hz=float(configured_fps) - 1.1,
    )


def test_run_classes_are_exact() -> None:
    assert tuple(member.value for member in support.RunClass) == (
        "native_production",
        "fallback_functional",
        "diagnostic",
    )


def test_run_phases_are_exact() -> None:
    assert tuple(member.value for member in support.RunPhase) == (
        "production",
        "fallback",
        "diagnostic",
        "teardown",
    )


def test_tk_delay_is_positive_integer_milliseconds() -> None:
    # Given: exact nanosecond delays for a 60-second window and a sub-ms delay.
    assert support._tk_delay_ms(60 * _ONE_SECOND_NS) == 60_000
    assert support._tk_delay_ms(1) == 1


def test_invalid_tk_path_counts_as_destroyed_root() -> None:
    # Given: Tk has already destroyed the widget command.
    class DestroyedRoot:
        def winfo_exists(self) -> bool:
            raise tk.TclError("invalid command name")

    # When/Then: the teardown probe records the required destroyed state.
    assert support._root_is_destroyed(DestroyedRoot()) is True


def test_fixed_window_uses_half_open_nominal_boundaries() -> None:
    # Given: entries immediately before, at, and after the one-second boundary.
    entries = (_START_NS - 1, _START_NS, _START_NS + _ONE_SECOND_NS)

    # When: the fixed nominal window is summarized.
    summary = support.summarize_cadence(
        entries,
        measurement_start_ns=_START_NS,
        nominal_window_end_ns=_START_NS + _ONE_SECOND_NS,
        configured_measurement_s=1.0,
    )

    # Then: only the start entry counts and the mean uses the configured duration.
    assert summary.count == 1
    assert summary.mean_hz == 1.0
    assert summary.p1_hz == 0.0


def test_fixed_window_counts_3600_entries_over_60_seconds() -> None:
    # Given: 3,600 deterministic 60 Hz entries.
    entries = tuple(
        _START_NS + (index * _ONE_SECOND_NS) // 60 for index in range(3_600)
    )

    # When: the configured 60-second window is summarized.
    summary = support.summarize_cadence(
        entries,
        measurement_start_ns=_START_NS,
        nominal_window_end_ns=_START_NS + 60 * _ONE_SECOND_NS,
        configured_measurement_s=60.0,
    )

    # Then: the hand-derived fixed-window mean is exactly 60 Hz.
    assert summary.count == 3_600
    assert summary.mean_hz == 60.0
    assert summary.p1_hz == pytest.approx(59.9999988, rel=1e-12)


def test_endpoint_intervals_require_both_entries_inside_window() -> None:
    # Given: three in-window entries plus an excluded nominal-end entry.
    entries = (
        _START_NS,
        _START_NS + 10_000_000,
        _START_NS + 20_000_000,
        _START_NS + _ONE_SECOND_NS,
    )

    # When/Then: only the two complete in-window intervals are returned.
    assert support._endpoint_intervals(
        entries,
        _START_NS,
        _START_NS + _ONE_SECOND_NS,
    ) == [
        (_START_NS, _START_NS + 10_000_000),
        (_START_NS + 10_000_000, _START_NS + 20_000_000),
    ]


def test_cadence_gap_rows_preserve_recorded_sequence_ids() -> None:
    # Given: a 30 ms interval with known dispatch sequence IDs.
    entries = (1_000, 31_000_000)
    sequence_by_entry = {1_000: 41, 31_000_000: 42}

    # When: the interval crosses the 25 ms gap threshold.
    rows = support._cadence_gap_rows(
        entries,
        stream="dispatch",
        window_start_ns=0,
        window_end_ns=60_000_000,
        sequence_by_entry_ns=sequence_by_entry,
    )

    # Then: the forensic row retains the actual before/after sequences.
    assert len(rows) == 1
    assert rows[0]["before_sequence"] == 41
    assert rows[0]["after_sequence"] == 42


def test_qpc_gap_rows_convert_ticks_to_nanoseconds_before_threshold() -> None:
    # Given: a 150,000-tick gap at a non-10 MHz QPC frequency.
    evidence_rows = (
        {
            "record_type": "worker_tick",
            "qpc_ns": 0,
            "sequence": 10,
            "qpc_frequency_hz": 5_000_000,
        },
        {
            "record_type": "worker_tick",
            "qpc_ns": 150_000,
            "sequence": 11,
            "qpc_frequency_hz": 5_000_000,
        },
    )

    # When: QPC gap forensics are built.
    rows = support._qpc_gap_rows(
        evidence_rows,
        record_type="worker_tick",
        stream="worker_tick",
    )

    # Then: the QPC delta is 30 ms and remains explicitly in the QPC domain.
    assert len(rows) == 1
    assert rows[0]["gap_ns"] == 30_000_000
    assert rows[0]["clock_domain"] == "qpc"
    assert rows[0]["qpc_frequency_hz"] == 5_000_000


def test_dispatch_record_exposes_required_mutable_fields() -> None:
    # Given: one dispatch that has entered but not exited.
    record = support.DispatchRecord(
        dispatch_id=7,
        dispatch_enter_ns=100,
        dispatch_exit_ns=None,
        sequence=8,
        epoch=2,
        generation=3,
        configured_fps=60,
        schedule="unknown",
        paste_observed=False,
    )

    # When: dispatch completion and paste correlation are recorded.
    record.schedule = "active"
    record.dispatch_exit_ns = 200
    record.paste_observed = True

    # Then: the mutable evidence record retains the production inputs.
    assert record.dispatch_id == 7
    assert record.dispatch_exit_ns == 200
    assert record.configured_fps == 60
    assert record.schedule == "active"
    assert record.paste_observed is True


def test_production_fps_values_are_exact() -> None:
    assert support.PRODUCTION_FPS_VALUES == (5, 15, 30, 45, 60)


@pytest.mark.parametrize("configured_fps", support.PRODUCTION_FPS_VALUES)
def test_production_contract_keeps_configured_mean_cap_and_p1(
    configured_fps: int,
) -> None:
    # Given/When: a supported configured FPS is converted to its contract.
    contract = support.cadence_contract(configured_fps)

    # Then: F..F+0.1 and P1 >= F-1 remain literal, including F=60 -> P1 59.
    assert contract == support.CadenceContract(
        configured_fps=configured_fps,
        target_mean_min_hz=float(configured_fps),
        target_mean_max_hz=configured_fps + 0.1,
        target_p1_min_hz=configured_fps - 1.0,
        cap_tolerance_hz=0.1,
        cap_tolerance_semantics="finite_window_boundary_not_rate_relaxation",
    )


@pytest.mark.parametrize("configured_fps", support.PRODUCTION_FPS_VALUES)
def test_native_production_decision_publishes_performance_reference_per_breach(
    configured_fps: int,
) -> None:
    # Given: both streams meet the configured production contract.
    contract = support.cadence_contract(configured_fps)
    passing = _passing_cadence(configured_fps)
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

    # When/Then: both streams inside the contract means the reference is met.
    assert decision.production_accepted is True
    assert decision.accepted is True
    assert decision.performance_reference.within_reference is True
    below_mean = support.CadenceResult(
        passing.count, configured_fps - 0.1, passing.p1_hz
    )
    below_p1 = support.CadenceResult(
        passing.count, passing.mean_hz, configured_fps - 1.000001
    )
    above_cap = support.CadenceResult(
        passing.count, configured_fps + 0.100001, passing.p1_hz
    )

    # When: either stream independently breaches the mean, P1, or cap bound.
    for dispatch, paste in (
        (below_mean, passing),
        (passing, below_mean),
        (below_p1, passing),
        (passing, below_p1),
        (above_cap, passing),
        (passing, above_cap),
    ):
        breached = support.decide_acceptance(
            run_class="native_production",
            clock_mode="high_resolution",
            contract=contract,
            dispatch=dispatch,
            paste=paste,
            measurement_overshoot_ok=True,
            functional_ok=True,
            evidence_ok=True,
        )

        # Then: the miss is published on both flags, and still does not gate.
        assert not breached.production_accepted
        assert breached.performance_reference.within_reference is False
        assert breached.functional_accepted is True
        assert breached.accepted is True


def test_normal_teardown_evidence_requires_actual_cleanup_facts() -> None:
    # Given: a complete legacy teardown payload.
    teardown = {
        "stop_result": "stopped",
        "root_destroyed_after_stop": True,
        "preview_worker_alive": False,
        "synthetic_source_alive": False,
        "pending_after_ids": [],
        "window_class_destroyed": None,
        "window_class_unregistered": None,
        "post_teardown_camera_read_count": 0,
        "source_stopped": True,
        "instrumentation_closed": True,
    }

    # When/Then: each required fact is independently load-bearing.
    assert support.normal_teardown_evidence_ok(teardown, native_clock=False)
    for field, value in (
        ("stop_result", "pending"),
        ("root_destroyed_after_stop", False),
        ("preview_worker_alive", True),
        ("pending_after_ids", ["after-1"]),
        ("post_teardown_camera_read_count", 1),
    ):
        invalid = {**teardown, field: value}
        assert not support.normal_teardown_evidence_ok(invalid, native_clock=False)
    native_invalid = {
        **teardown,
        "window_class_destroyed": False,
        "window_class_unregistered": False,
    }
    assert not support.normal_teardown_evidence_ok(
        native_invalid,
        native_clock=True,
    )


def test_real_harness_close_reports_post_stop_facts(tmp_path: Path) -> None:
    # Given: stop leaves one pending callback while the actual worker is dead.
    class FakeRoot:
        def __init__(self) -> None:
            self.destroyed = False
            self.tk = SimpleNamespace(call=lambda *_args: ["after-9"])

        def destroy(self) -> None:
            self.destroyed = True

        def winfo_exists(self) -> int:
            return int(not self.destroyed)

    class FakeArea:
        def stopCapture(self) -> None:
            return None

    class FakeWorker:
        def is_alive(self) -> bool:
            return False

    class FakeClock:
        def __init__(self) -> None:
            self._worker = FakeWorker()

        def health_snapshot(self) -> dict[str, int | str | bool]:
            return {
                "running": True,
                "window_class_destroyed": True,
                "window_class_unregistered": True,
            }

    class FakeSource(support.SyntheticFrameSource):
        def stop(self) -> bool:
            return True

        def is_alive(self) -> bool:
            return False

        def camera_summary(self) -> dict[str, int | bool | str]:
            return {"post_teardown_camera_read_count": 0}

    class FakeInstrumentation(support.PasteInstrumentation):
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def close(self) -> None:
            return None

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
    harness.area = FakeArea()
    harness.source = FakeSource()
    harness.instrumentation = FakeInstrumentation()
    harness.clock = FakeClock()
    harness.preview_worker_native_id = 12
    harness.end_snapshot = {"running": True}
    harness.root = FakeRoot()

    # When: the real close method completes.
    teardown = harness.close()

    # Then: it reports stop, pending IDs, and worker state observed after stop.
    assert teardown["stop_result"] == "stopped"
    assert teardown["pending_after_ids"] == ["after-9"]
    assert teardown["preview_worker_alive"] is False


def test_fallback_report_does_not_require_native_window_cleanup(
    tmp_path: Path,
) -> None:
    # Given: a functional after-mode report with no native window resources.
    class FakeInstrumentation:
        dispatch_records = [
            SimpleNamespace(dispatch_enter_ns=1_100_000_000, sequence=1),
            SimpleNamespace(dispatch_enter_ns=1_200_000_000, sequence=2),
        ]
        pastes = [
            SimpleNamespace(paste_enter_ns=1_100_000_000, sequence=1),
            SimpleNamespace(paste_enter_ns=1_200_000_000, sequence=2),
        ]

        def dispatch_entries(self) -> tuple[int, ...]:
            return (1_100_000_000, 1_200_000_000)

        def paste_entries(self) -> tuple[int, ...]:
            return (1_100_000_000, 1_200_000_000)

    camera_summary = {
        "camera_read_count": 2,
        "camera_frame_present_count": 2,
        "camera_none_count": 0,
        "camera_read_error_count": 0,
        "camera_unique_sequence_count": 2,
        "camera_duplicate_sequence_count": 0,
        "camera_sequence_regression_count": 0,
        "camera_last_sequence": 2,
        "post_teardown_camera_read_count": 0,
        "camera_thread_claim": "not_applicable_synthetic_source",
    }
    thread_ids = {
        "schema_version": 1,
        "main": 1,
        "window_owner": 1,
        "preview_worker": 0,
        "camera_source": 2,
        "camera_thread": None,
        "camera_thread_claim": "not_applicable_synthetic_source",
    }
    pins = {
        relative: support.FilePin(relative, True, 0, "0" * 64)
        for relative in support.SOURCE_PIN_PATHS
    }
    harness: Any = SimpleNamespace(
        source=SimpleNamespace(
            camera_summary=lambda: camera_summary,
            camera_records=lambda: (),
        ),
        instrumentation=FakeInstrumentation(),
        area=object(),
        clock=object(),
        clock_mode="after",
        dispatch_oracle="legacy CaptureArea.after callback entry",
        start_pins=pins,
        end_pins=pins,
        start_snapshot={},
        end_snapshot={},
        end_accounting={"latest_only_accounting_ok": False},
        health_rows=[],
        thread_ids_payload=lambda: thread_ids,
    )
    config = support.E2EConfig(
        phase=support.RunPhase.FALLBACK,
        run_class=support.RunClass.FALLBACK_FUNCTIONAL,
        configured_fps=15,
        diagnostic_rate_hz=None,
        warmup_s=0.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.CHILD,
        test_id="test_after_fallback_functional_at_configured_target",
        watchdog_timeout_s=10.0,
    )
    teardown = {
        "stop_result": "stopped",
        "root_destroyed_after_stop": True,
        "preview_worker_alive": False,
        "synthetic_source_alive": False,
        "pending_after_ids": [],
        "post_teardown_camera_read_count": 0,
        "source_stopped": True,
        "instrumentation_closed": True,
        "window_class_destroyed": False,
        "window_class_unregistered": False,
    }

    # When: the report evaluates fallback functional evidence.
    report, _artifacts = support._build_report(
        config,
        harness,
        support.Window(1_000_000_000, 2_000_000_000),
        teardown,
        (),
    )

    # Then: fallback is functional without native cleanup or production acceptance.
    assert report["functional_accepted"] is True
    assert report["test_passed"] is True
    assert report["production_accepted"] is False
    assert report["accepted"] is False


def test_native_production_after_mode_is_functional_but_not_accepted() -> None:
    # Given: perfect cadence reported by the legacy after clock.
    contract = support.cadence_contract(60)
    passing = _passing_cadence(60)

    # When: native production is evaluated with clock_mode=after.
    decision = support.decide_acceptance(
        run_class="native_production",
        clock_mode="after",
        contract=contract,
        dispatch=passing,
        paste=passing,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    )

    # Then: it is never production-accepted even with passing evidence.
    assert decision.functional_accepted is True
    assert decision.production_accepted is False
    assert decision.accepted is False


def test_native_production_decision_publishes_performance_reference() -> None:
    # Given: a native production run whose cadence is below the hard target.
    low = _sub_target_cadence(5)

    # When: acceptance is decided for the sub-target run.
    decision: Any = support.decide_acceptance(
        run_class="native_production",
        clock_mode="high_resolution",
        contract=support.cadence_contract(5),
        dispatch=low,
        paste=low,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    )

    # Then: the missed reference is published without gating acceptance.
    assert decision.functional_accepted is True
    assert decision.test_passed is True
    assert decision.performance_reference.within_reference is False


def test_native_production_decision_exposes_both_skip_counters_separately() -> None:
    # Given: a native production run with both skip counters reported.
    low = _sub_target_cadence(5)
    decide: Callable[..., Any] = support.decide_acceptance

    # When: acceptance is decided with the two independent counters.
    decision: Any = decide(
        run_class="native_production",
        clock_mode="high_resolution",
        contract=support.cadence_contract(5),
        dispatch=low,
        paste=low,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
        period_skipped_count=4,
        blit_skipped_no_new_frame=37,
    )

    # Then: each counter is readable on its own, without collapsing into one.
    assert decision.period_skipped_count == 4
    assert decision.blit_skipped_no_new_frame == 37
    assert decision.period_skipped_count != decision.blit_skipped_no_new_frame


def test_native_production_acceptance_does_not_gate_on_sub_target_cadence() -> None:
    # Given: a native production run whose cadence is below the hard target.
    low = _sub_target_cadence(5)

    # When: acceptance is decided.
    decision: Any = support.decide_acceptance(
        run_class="native_production",
        clock_mode="high_resolution",
        contract=support.cadence_contract(5),
        dispatch=low,
        paste=low,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    )

    # Then: the sub-target performance numbers do not gate acceptance.
    assert decision.functional_accepted is True
    assert decision.accepted is True


def test_native_production_report_records_both_skip_diagnostics(
    tmp_path: Path,
) -> None:
    # Given: a native production window whose measured blit rate is sub-target.
    class FakeInstrumentation:
        dispatch_records = [
            SimpleNamespace(dispatch_enter_ns=1_000_000_000, sequence=1),
            SimpleNamespace(dispatch_enter_ns=1_250_000_000, sequence=2),
            SimpleNamespace(dispatch_enter_ns=1_500_000_000, sequence=3),
            SimpleNamespace(dispatch_enter_ns=1_750_000_000, sequence=4),
        ]
        pastes = [
            SimpleNamespace(paste_enter_ns=1_000_000_000, sequence=1),
            SimpleNamespace(paste_enter_ns=1_250_000_000, sequence=2),
            SimpleNamespace(paste_enter_ns=1_500_000_000, sequence=3),
            SimpleNamespace(paste_enter_ns=1_750_000_000, sequence=4),
        ]

        def dispatch_entries(self) -> tuple[int, ...]:
            return (1_000_000_000, 1_250_000_000, 1_500_000_000, 1_750_000_000)

        def paste_entries(self) -> tuple[int, ...]:
            return (1_000_000_000, 1_250_000_000, 1_500_000_000, 1_750_000_000)

    camera_summary = {
        "camera_read_count": 4,
        "camera_frame_present_count": 4,
        "camera_none_count": 0,
        "camera_read_error_count": 0,
        "camera_unique_sequence_count": 4,
        "camera_duplicate_sequence_count": 0,
        "camera_sequence_regression_count": 0,
        "camera_last_sequence": 4,
        "post_teardown_camera_read_count": 0,
        "camera_thread_claim": "not_applicable_synthetic_source",
    }
    thread_ids = {
        "schema_version": 1,
        "main": 1,
        "window_owner": 1,
        "preview_worker": 0,
        "camera_source": 2,
        "camera_thread": None,
        "camera_thread_claim": "not_applicable_synthetic_source",
    }
    pins = {
        relative: support.FilePin(relative, True, 0, "0" * 64)
        for relative in support.SOURCE_PIN_PATHS
    }
    harness: Any = SimpleNamespace(
        source=SimpleNamespace(
            camera_summary=lambda: camera_summary,
            camera_records=lambda: (),
        ),
        instrumentation=FakeInstrumentation(),
        area=object(),
        clock=object(),
        clock_mode="high_resolution",
        dispatch_oracle="PreviewClock WNDPROC to CaptureArea entry",
        start_pins=pins,
        end_pins=pins,
        start_snapshot={"period_skipped_count": 0, "blit_skipped_no_new_frame": 0},
        end_snapshot={"period_skipped_count": 0, "blit_skipped_no_new_frame": 3},
        end_accounting={
            "worker_tick_published_count": 5,
            "pending_tick_superseded_count": 0,
            "main_dispatch_count": 4,
            "stale_tick_dropped_count": 0,
            "pending_tick_present_at_window_end": 1,
            "latest_only_accounting_ok": True,
            "period_skipped_count": 0,
            "blit_skipped_no_new_frame": 3,
        },
        health_rows=[],
        thread_ids_payload=lambda: thread_ids,
    )
    config = support.E2EConfig(
        phase=support.RunPhase.PRODUCTION,
        run_class=support.RunClass.NATIVE_PRODUCTION,
        configured_fps=5,
        diagnostic_rate_hz=None,
        warmup_s=0.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.CHILD,
        test_id="test_native_production_5hz_shortfall_is_discriminable",
        watchdog_timeout_s=10.0,
    )
    teardown = {
        "stop_result": "stopped",
        "root_destroyed_after_stop": True,
        "preview_worker_alive": False,
        "synthetic_source_alive": False,
        "pending_after_ids": [],
        "post_teardown_camera_read_count": 0,
        "source_stopped": True,
        "instrumentation_closed": True,
        "window_class_destroyed": True,
        "window_class_unregistered": True,
    }

    # When: the real report builder summarizes the finished window.
    report, _artifacts = support._build_report(
        config,
        harness,
        support.Window(1_000_000_000, 2_000_000_000),
        teardown,
        (),
    )

    # Then: both skip diagnostics and the suppressed-interval measure are kept.
    assert report["period_skipped_count"] == 0
    assert report["blit_skipped_no_new_frame"] == 3
    assert report["suppressed_blit_interval_count"] == 2

    # Then: the sub-target performance numbers are recorded without gating.
    assert report["dispatch_summary"]["mean_hz"] == 4.0
    assert report["functional_accepted"] is True
    assert report["performance_reference"]["within_reference"] is False


def test_required_report_fields_demand_both_skip_diagnostics() -> None:
    # Given/When: the terminal E2E artifact contract lists its required fields.
    required = set(support.REQUIRED_REPORT_FIELDS)

    # Then: the two skip counters and the suppressed-interval measure are demanded.
    assert {
        "period_skipped_count",
        "blit_skipped_no_new_frame",
        "suppressed_blit_interval_count",
    } <= required


def test_fallback_functional_evidence_does_not_require_native_accounting() -> None:
    # Given: functional and non-accounting evidence pass while native counters are absent.
    functional_ok = True
    non_accounting_evidence_ok = True
    latest_only_accounting_ok = False

    # When/Then: fallback can pass functionally, but native production cannot.
    assert support.evidence_gate_passes(
        "fallback_functional",
        functional_ok=functional_ok,
        non_accounting_evidence_ok=non_accounting_evidence_ok,
        latest_only_accounting_ok=latest_only_accounting_ok,
    )
    assert not support.evidence_gate_passes(
        "native_production",
        functional_ok=functional_ok,
        non_accounting_evidence_ok=non_accounting_evidence_ok,
        latest_only_accounting_ok=latest_only_accounting_ok,
    )


@pytest.mark.parametrize("configured_fps", support.PRODUCTION_FPS_VALUES)
def test_measurement_overshoot_limit_uses_two_periods_or_fifty_ms(
    configured_fps: int,
) -> None:
    # Given: a nominal 60-second window and its bounded stop delay.
    start_ns = 1_000_000_000
    limit_ns = max(2 * (_ONE_SECOND_NS // configured_fps), 50_000_000)
    nominal_end_ns = start_ns + 60 * _ONE_SECOND_NS

    # When/Then: equality passes and one nanosecond beyond the bound fails.
    assert support.measurement_overshoot(
        start_ns, nominal_end_ns + limit_ns, 60.0, configured_fps
    ) == (limit_ns, limit_ns, True)
    assert support.measurement_overshoot(
        start_ns, nominal_end_ns + limit_ns + 1, 60.0, configured_fps
    ) == (limit_ns + 1, limit_ns, False)


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
    assert not support.latest_only_accounting_balanced(
        worker_tick_published_count=100,
        main_dispatch_count=87,
        pending_tick_superseded_count=7,
        stale_tick_dropped_count=4,
        pending_tick_present_at_window_end=1,
    )


def test_accounting_parsing_fails_closed_when_fields_are_missing() -> None:
    # Given: missing counters, missing status, and a string status payload.
    empty_accounting = support._accounting_values({})
    statusless_accounting = support._accounting_values(
        {
            "worker_tick_published_count": 0,
            "pending_tick_superseded_count": 0,
            "main_dispatch_count": 0,
            "stale_tick_dropped_count": 0,
            "pending_tick_present_at_window_end": 0,
        }
    )
    string_status_accounting = support._accounting_values(
        {
            "worker_tick_published_count": 0,
            "pending_tick_superseded_count": 0,
            "main_dispatch_count": 0,
            "stale_tick_dropped_count": 0,
            "pending_tick_present_at_window_end": 0,
            "latest_only_accounting_ok": "true",
        }
    )

    # When/Then: incomplete accounting is rejected instead of synthesized.
    assert empty_accounting is None
    assert statusless_accounting is None
    assert string_status_accounting is None


@pytest.mark.parametrize("malformed_worker_count", ["1", True, 1.0])
def test_accounting_parsing_rejects_coerced_counter_types(
    malformed_worker_count: str | bool | float,
) -> None:
    # Given: all required keys but a string, bool, or float worker counter.
    accounting = {
        "worker_tick_published_count": malformed_worker_count,
        "pending_tick_superseded_count": 0,
        "main_dispatch_count": 0,
        "stale_tick_dropped_count": 0,
        "pending_tick_present_at_window_end": 0,
        "latest_only_accounting_ok": True,
    }

    # When/Then: the parser rejects the payload without coercing its counter.
    assert support._accounting_values(accounting) is None


def test_missing_source_pins_are_unchanged_but_incomplete(
    tmp_path: Path,
) -> None:
    # Given: the same absent source path at both measurement boundaries.
    missing = tmp_path / "missing.py"
    start = support.pin_files((missing,))
    end = support.pin_files((missing,))

    # When/Then: absence is stable evidence but never complete evidence.
    assert next(iter(start.values())) == support.FilePin(
        path=str(missing), exists=False, bytes=0, sha256=""
    )
    assert support.source_pins_unchanged(start, end) is True
    assert support.source_pins_complete(end) is False


def test_source_and_artifact_pins_detect_content_change(
    tmp_path: Path,
) -> None:
    # Given: a source artifact with a stable initial hash.
    artifact = tmp_path / "report.json"
    artifact.write_text('{"status":"measured"}\n', encoding="utf-8")
    start = support.pin_files((artifact,))
    pin = next(iter(start.values()))
    assert pin.exists is True
    assert pin.bytes == artifact.stat().st_size
    assert len(pin.sha256) == 64

    # When: the bytes change and are pinned again.
    artifact.write_text('{"status":"passed"}\n', encoding="utf-8")
    end = support.pin_files((artifact,))

    # Then: the change is detected and the existing artifact remains complete.
    assert support.source_pins_unchanged(start, end) is False
    assert support.source_pins_complete(end) is True


def test_terminal_artifact_manifest_accepts_valid_complete_manifest(
    tmp_path: Path,
) -> None:
    # Given/When: a manifest with every required row and matching file content.
    manifest = _write_valid_artifact_manifest(tmp_path)

    # Then: verification succeeds and the manifest excludes itself.
    support.verify_artifact_manifest(tmp_path)
    assert [row["path"] for row in manifest["artifacts"]] == list(
        support.REQUIRED_EVIDENCE_FILES
    )


def test_terminal_artifact_manifest_detects_tampering(tmp_path: Path) -> None:
    # Given: a complete manifest with a stable report hash.
    _write_valid_artifact_manifest(tmp_path)

    # When: the finalized report changes.
    (tmp_path / "report.json").write_text("tampered", encoding="utf-8")

    # Then: verification fails rather than accepting altered evidence.
    with pytest.raises(AssertionError, match="artifact manifest mismatch"):
        support.verify_artifact_manifest(tmp_path)


def test_terminal_artifact_manifest_rejects_missing_required_row(
    tmp_path: Path,
) -> None:
    # Given: a valid manifest with one required row removed.
    manifest = _write_valid_artifact_manifest(tmp_path)
    rows = manifest["artifacts"]
    assert isinstance(rows, list)
    manifest["artifacts"] = [row for row in rows if row["path"] != "thread_ids.json"]
    _write_artifact_manifest_payload(tmp_path, manifest)

    # When/Then: the required file set is rejected.
    with pytest.raises(AssertionError, match="file set mismatch"):
        support.verify_artifact_manifest(tmp_path)


def test_terminal_artifact_manifest_rejects_duplicate_path(
    tmp_path: Path,
) -> None:
    # Given: a valid manifest plus a duplicate row for report.json.
    manifest = _write_valid_artifact_manifest(tmp_path)
    rows = manifest["artifacts"]
    assert isinstance(rows, list)
    report_row = next(row for row in rows if row["path"] == "report.json")
    rows.append(dict(report_row))
    _write_artifact_manifest_payload(tmp_path, manifest)

    # When/Then: duplicate paths are rejected before dictionary collapse.
    with pytest.raises(AssertionError, match="duplicate path"):
        support.verify_artifact_manifest(tmp_path)


@pytest.mark.parametrize("missing_field", ["bytes", "sha256"])
def test_terminal_artifact_manifest_rejects_missing_metadata(
    tmp_path: Path,
    missing_field: str,
) -> None:
    # Given: a manifest row missing required size or digest metadata.
    manifest = _write_valid_artifact_manifest(tmp_path)
    rows = manifest["artifacts"]
    assert isinstance(rows, list)
    report_row = next(row for row in rows if row["path"] == "report.json")
    report_row.pop(missing_field)
    _write_artifact_manifest_payload(tmp_path, manifest)

    # When/Then: missing metadata is rejected without defaulting.
    with pytest.raises(AssertionError, match="invalid artifact metadata"):
        support.verify_artifact_manifest(tmp_path)


@pytest.mark.parametrize("malformed_bytes", ["1", True, 1.0, -1])
def test_terminal_artifact_manifest_rejects_malformed_bytes(
    tmp_path: Path,
    malformed_bytes: str | bool | float | int,
) -> None:
    # Given: a one-byte report whose manifest size uses a non-exact int value.
    manifest = _write_valid_artifact_manifest(tmp_path)
    rows = manifest["artifacts"]
    assert isinstance(rows, list)
    report_row = next(row for row in rows if row["path"] == "report.json")
    report_row["bytes"] = malformed_bytes
    _write_artifact_manifest_payload(tmp_path, manifest)

    # When/Then: string/bool/float/negative sizes are not coerced or accepted.
    with pytest.raises(AssertionError, match="invalid artifact metadata"):
        support.verify_artifact_manifest(tmp_path)


@pytest.mark.parametrize("malformed_sha256", ["0" * 63, "z" * 64, 123])
def test_terminal_artifact_manifest_rejects_malformed_sha256(
    tmp_path: Path,
    malformed_sha256: str | int,
) -> None:
    # Given: a manifest row with an invalid digest type, length, or alphabet.
    manifest = _write_valid_artifact_manifest(tmp_path)
    rows = manifest["artifacts"]
    assert isinstance(rows, list)
    report_row = next(row for row in rows if row["path"] == "report.json")
    report_row["sha256"] = malformed_sha256
    _write_artifact_manifest_payload(tmp_path, manifest)

    # When/Then: the row is rejected as malformed SHA-256 metadata.
    with pytest.raises(AssertionError, match="invalid SHA-256"):
        support.verify_artifact_manifest(tmp_path)


def test_failure_report_has_schema_and_compares_actual_source_pins(
    tmp_path: Path,
) -> None:
    # Given: source bytes change between the parent and failure boundaries.
    source = tmp_path / "source.py"
    source.write_text("first\n", encoding="utf-8")
    start_pins = support.pin_files((source,))
    source.write_text("second\n", encoding="utf-8")
    end_pins = support.pin_files((source,))
    config = support.E2EConfig(
        phase=support.RunPhase.PRODUCTION,
        run_class=support.RunClass.NATIVE_PRODUCTION,
        configured_fps=60,
        diagnostic_rate_hz=None,
        warmup_s=1.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.NORMAL,
        test_id="test_native_production_60hz_meets_hard_contract",
        watchdog_timeout_s=10.0,
    )

    # When: the parent synthesizes a child-failure report.
    report = support._minimal_failure_report(
        config,
        {"timed_out": False},
        start_pins=start_pins,
        end_pins=end_pins,
    )

    # Then: required schema fields exist and source drift is reported honestly.
    required_fields = {
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
        "paste_summary",
        "production_accepted",
        "functional_accepted",
        "test_passed",
        "accepted",
        "worker_tick_published_count",
        "pending_tick_superseded_count",
        "main_dispatch_count",
        "stale_tick_dropped_count",
        "pending_tick_present_at_window_end",
        "latest_only_accounting_ok",
        "wake_reason_counts",
        "gap_threshold_ns",
        "dispatch_gaps",
        "paste_gaps",
        "worker_tick_gaps",
        "control_wake_gaps",
        "thread_ids",
        "source_pins_unchanged",
        "source_pins_complete",
        "artifact_manifest_path",
        "physical_camera_evidence",
        "physical_unique_frame_claim",
        "compositor_or_scanout_proof",
    }
    assert required_fields <= report.keys()
    assert report["source_pins_unchanged"] is False


def test_parent_replaces_malformed_child_report_before_manifest_hashing(
    tmp_path: Path,
) -> None:
    # Given: a failed child leaves a malformed report and a matching stale manifest.
    for name in support.REQUIRED_EVIDENCE_FILES:
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text('{"accepted":true}\n', encoding="utf-8")
    support.write_artifact_manifest(tmp_path)
    config = support.E2EConfig(
        phase=support.RunPhase.PRODUCTION,
        run_class=support.RunClass.NATIVE_PRODUCTION,
        configured_fps=60,
        diagnostic_rate_hz=None,
        warmup_s=1.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.NORMAL,
        test_id="test_native_production_60hz_meets_hard_contract",
        watchdog_timeout_s=10.0,
    )
    process_result = {
        "pid": 123,
        "returncode": 1,
        "timed_out": False,
        "escalation": None,
        "elapsed_ns": 1,
        "timeout_s": 10.0,
    }
    writer = support.EvidenceWriter(tmp_path, role="parent")

    # When: the parent finalizes failure artifacts.
    support._ensure_parent_artifacts(config, writer, process_result)

    # Then: the report is replaced before rehashing, with explicit failure evidence.
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert isinstance(report, dict)
    assert report["report_schema_version"] == support.REPORT_SCHEMA_VERSION
    assert report["accepted"] is False
    assert report["production_accepted"] is False
    assert report["artifact_manifest_path"] == support.ARTIFACT_MANIFEST_FILE
    assert report["watchdog"] == process_result
    assert isinstance(report["source_pins_unchanged"], bool)
    support.verify_artifact_manifest(tmp_path)


def test_parent_preserves_complete_successful_native_report(
    tmp_path: Path,
) -> None:
    # Given: a successful child leaves a complete accepted native report.
    config = support.E2EConfig(
        phase=support.RunPhase.PRODUCTION,
        run_class=support.RunClass.NATIVE_PRODUCTION,
        configured_fps=60,
        diagnostic_rate_hz=None,
        warmup_s=1.0,
        measurement_s=1.0,
        evidence_dir=tmp_path,
        source_hz=180,
        child_mode=support.ChildMode.NORMAL,
        test_id="test_native_production_60hz_meets_hard_contract",
        watchdog_timeout_s=10.0,
    )
    process_result = {
        "pid": 123,
        "returncode": 0,
        "timed_out": False,
        "escalation": None,
        "elapsed_ns": 1,
        "timeout_s": 10.0,
    }
    child_report = support._minimal_failure_report(
        config,
        process_result,
        start_pins=support._source_pins(),
        end_pins=support._source_pins(),
    )
    child_report.update(
        {
            "accepted": True,
            "production_accepted": True,
            "test_passed": True,
            "status": "passed",
        }
    )
    for name in support.REQUIRED_EVIDENCE_FILES:
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    (tmp_path / "report.json").write_text(
        json.dumps(child_report, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    support.write_artifact_manifest(tmp_path)
    writer = support.EvidenceWriter(tmp_path, role="parent")

    # When: the parent finalizes artifacts for the successful child.
    support._ensure_parent_artifacts(config, writer, process_result)

    # Then: the complete accepted report remains unchanged.
    retained_report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert isinstance(retained_report, dict)
    assert retained_report["accepted"] is True
    assert retained_report["production_accepted"] is True
    assert retained_report["status"] == "passed"
    support.verify_artifact_manifest(tmp_path)


def test_terminal_artifact_manifest_rejects_missing_required_file(
    tmp_path: Path,
) -> None:
    # Given: every required artifact except report.json exists.
    for name in support.REQUIRED_EVIDENCE_FILES:
        if name != "report.json":
            (tmp_path / name).write_text("{}\n", encoding="utf-8")
    support.write_artifact_manifest(tmp_path)

    # When/Then: a manifest row with exists=false is never accepted.
    with pytest.raises(AssertionError, match="required artifact missing"):
        support.verify_artifact_manifest(tmp_path)


def test_thread_manifest_requires_real_thread_separation() -> None:
    valid = {
        "main": 10,
        "window_owner": 10,
        "preview_worker": 11,
        "camera_source": 12,
        "camera_thread": None,
        "camera_thread_claim": "not_applicable_synthetic_source",
    }
    assert support.thread_ids_valid(valid)
    assert not support.thread_ids_valid({**valid, "preview_worker": 10})
    assert not support.thread_ids_valid({**valid, "camera_source": 10})
    assert not support.thread_ids_valid({**valid, "camera_thread": 13})
    assert not support.thread_ids_valid({**valid, "camera_thread_claim": "physical"})


def test_camera_reads_record_duplicates_regressions_and_thread() -> None:
    # Given: a synthetic source forced through stable and regressing sequences.
    source = support.SyntheticFrameSource()
    source._seq = 3
    source.readFrameWithSeq()
    source.readFrameWithSeq()
    source._seq = 2
    source.readFrameWithSeq()

    # When: camera evidence is read.
    rows = source.camera_records()

    # Then: duplicate and regression are distinct, and no physical claim is made.
    assert [(row.sequence, row.duplicate, row.regression) for row in rows] == [
        (3, False, False),
        (3, True, False),
        (2, False, True),
    ]
    assert all(row.thread_native_id > 0 for row in rows)
    assert all(row.frame_present for row in rows)
    assert all(row.error is None for row in rows)
    assert source.physical_camera_evidence is False
    assert source.physical_unique_frame_claim is False


def test_native_and_legacy_dispatch_targets_are_selected_once() -> None:
    # Given: one area with a native clock and one without.
    native = SimpleNamespace(_preview_clock=object())
    legacy = SimpleNamespace()

    # When/Then: native wraps the dispatch seam while pre-production wraps capture.
    assert support.select_dispatch_target(native) == (
        "_dispatch_tick",
        "PreviewClock WNDPROC to CaptureArea entry",
    )
    assert support.select_dispatch_target(legacy) == (
        "capture",
        "legacy CaptureArea.after callback entry",
    )
