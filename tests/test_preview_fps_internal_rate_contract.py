"""Contracts for diagnostic-rate and fallback preview FPS acceptance."""

from __future__ import annotations

from pathlib import Path

import preview_fps_support as support
import pytest


def test_diagnostic_rates_are_exact() -> None:
    assert support.DIAGNOSTIC_RATES_HZ == (60.01, 60.03, 60.06)


@pytest.mark.parametrize("diagnostic_rate_hz", support.DIAGNOSTIC_RATES_HZ)
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
        present=cadence,
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
        present=cadence,
        measurement_overshoot_ok=True,
        functional_ok=True,
        evidence_ok=True,
    )
    assert decision.functional_accepted is True
    assert decision.production_accepted is False
    assert decision.accepted is False


@pytest.mark.parametrize("diagnostic_rate_hz", support.DIAGNOSTIC_RATES_HZ)
def test_configured_fps_never_overwritten_by_diagnostic_rate(
    diagnostic_rate_hz: float,
) -> None:
    # Given: a diagnostic-only configuration at each approved reference rate.
    config = support.E2EConfig(
        run_class=support.RunClass.DIAGNOSTIC,
        configured_fps=60,
        diagnostic_rate_hz=diagnostic_rate_hz,
        warmup_s=2.0,
        measurement_s=5.0,
        evidence_dir=Path("C:/strict-evidence"),
        source_hz=180,
        child_mode=support.ChildMode.NORMAL,
        test_id="test_diagnostic_rate_is_reference_only",
        phase=support.RunPhase.DIAGNOSTIC,
        watchdog_timeout_s=15.0,
    )

    # When: the immutable parent/child execution contract is assembled.
    contract = config.execution_contract()

    # Then: configured FPS remains 60 and only diagnostic_rate_hz changes.
    assert contract == {
        "phase": "diagnostic",
        "run_class": "diagnostic",
        "configured_fps": 60,
        "diagnostic_rate_hz": diagnostic_rate_hz,
        "source_hz": 180,
        "warmup_s": 2.0,
        "measurement_s": 5.0,
        "evidence_dir": "C:\\strict-evidence",
        "test_id": "test_diagnostic_rate_is_reference_only",
        "watchdog_timeout_s": 15.0,
    }
    assert config.configured_fps == 60
