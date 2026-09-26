"""Opt-in 60 s+ production run against a physical capture board. # noqa: SIZE_OK

The gap this closes is stated plainly in the design doc: every 60 Hz number in
section 13 comes from ``SyntheticFrameSource``, so the pacer, the mailbox and
the blit are proven but no capture device is. A reader who mistakes those
numbers for a board measurement is being misled, and this file exists so the
board has numbers of its own.

The machinery is the synthetic machinery. ``RealCameraHarness`` swaps only the
source -- a ``RealCameraFrameSource`` over the production ``Camera`` -- and the
Tk root, the area, the preview clock, the instrumentation, the teardown and the
report are the identical code path, driven through ``_run_measurement`` with a
harness factory. A second driver would drift, so there is exactly one.

The device index is required and never scanned. An earlier artifact recorded a
virtual camera while believing it had measured a board, because OpenCV's DSHOW
index order is not the app's DirectShow enumeration order; a scan that picks
"the first device that opens" repeats that mistake by construction. A run that
will not name its board does not run.

The verdict stays reference-only. A board that grants YUY2 at 60 has not broken
anything, and on this machine that is exactly what the Elgato does, so the run
passes with the measured truth in the artifact. ``mjpg_honoured`` is published
as data, as it is in ``capture-device.json``, for a scheduler to gate on
elsewhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

import preview_fps_support as support
import pytest
from real_camera_source import REAL_SOURCE_FPS, REAL_SOURCE_SIZE, RealCameraFrameSource

GATE_ENV: Final[str] = "POKECON_RUN_REAL_CAMERA_PREVIEW_E2E"
EVIDENCE_DIR_ENV: Final[str] = "POKECON_REAL_CAMERA_EVIDENCE_DIR"
INDEX_ENV: Final[str] = "POKECON_REAL_CAMERA_DEVICE_INDEX"
WARMUP_ENV: Final[str] = "POKECON_REAL_CAMERA_WARMUP_S"
MEASUREMENT_ENV: Final[str] = "POKECON_REAL_CAMERA_MEASUREMENT_S"
WATCHDOG_ENV: Final[str] = "POKECON_REAL_CAMERA_WATCHDOG_TIMEOUT_S"

TEST_ID: Final[str] = "test_real_camera_production_60hz_records_reference"


class RealCameraHarness(support.RealCaptureHarness):
    """The production harness with only the source swapped for a board.

    Everything else -- root, area, clock, instrumentation, teardown, report --
    is inherited unchanged, so the numbers are comparable with the synthetic
    reference by construction rather than by audit.
    """

    def __init__(self, config: support.E2EConfig, device_index: int) -> None:
        super().__init__(config)
        self._device_index = int(device_index)

    def _make_source(self) -> Any:
        return RealCameraFrameSource(
            index=self._device_index,
            fps=self.config.configured_fps,
            capture_size=(REAL_SOURCE_SIZE[0], REAL_SOURCE_SIZE[1]),
        )


def _gate_is_on() -> bool:
    return os.environ.get(GATE_ENV, "").strip() == "1"


def _evidence_dir() -> Path:
    raw = os.environ.get(EVIDENCE_DIR_ENV, "").strip()
    if not raw:
        pytest.fail(
            f"{EVIDENCE_DIR_ENV} is required when {GATE_ENV}=1; the artifact is "
            "the deliverable and must not be written beside the sources"
        )
    return Path(raw)


def _device_index() -> int:
    raw = os.environ.get(INDEX_ENV, "").strip()
    if not raw:
        pytest.fail(
            f"{INDEX_ENV} is required when {GATE_ENV}=1; a run that will not "
            "name its board does not run, because OpenCV's index order is not "
            "the app's DirectShow order and a scan can measure the wrong device"
        )
    return int(raw)


def _seconds(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _build_config(evidence_dir: Path) -> support.E2EConfig:
    # Built directly, not through load_config: the synthetic gate enforces
    # source_hz == 180 for the 180 Hz producer, and a board has no such rate.
    # source_hz here is the camera's nominal delivery rate, stated honestly.
    return support.E2EConfig(
        phase=support.RunPhase.PRODUCTION,
        run_class=support.RunClass.NATIVE_PRODUCTION,
        configured_fps=60,
        diagnostic_rate_hz=None,
        warmup_s=_seconds(WARMUP_ENV, 10.0),
        measurement_s=_seconds(MEASUREMENT_ENV, 60.0),
        evidence_dir=evidence_dir,
        source_hz=REAL_SOURCE_FPS,
        child_mode=support.ChildMode.NORMAL,
        test_id=TEST_ID,
        watchdog_timeout_s=_seconds(WATCHDOG_ENV, 150.0),
    )


def _run_and_reload(directory: Path, index: int) -> dict[str, Any]:
    """Drive the real board through the production harness and re-read the report.

    Re-reading from disk rather than returning the in-memory dict is deliberate:
    the artifact is the deliverable, so what a reader will open is what gets
    asserted on.
    """
    config = _build_config(directory)
    support.claim_evidence_directory(directory)
    writer = support.EvidenceWriter(directory, role="parent")
    device_index = index

    def factory(cfg: support.E2EConfig) -> RealCameraHarness:
        return RealCameraHarness(cfg, device_index)

    # The in-memory return is discarded on purpose: the artifact on disk is the
    # deliverable, so what a reader will open is what gets asserted on.
    support._run_measurement(config, writer, harness_factory=factory)
    reloaded: Any = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    return reloaded


def test_real_camera_production_60hz_records_reference() -> None:
    # Given: a scheduled hardware run with the board named and the directory fresh.
    if not _gate_is_on():
        pytest.skip(f"set {GATE_ENV}=1 with {INDEX_ENV} to measure a real board")
    directory = _evidence_dir()
    index = _device_index()

    # When: the board is driven through the production 60 fps path.
    report = _run_and_reload(directory, index)

    # Then: the run says it measured a board, in the fields a reader checks
    # first, and nowhere claims synthetic. The synthetic reference and this run
    # share the harness, so the shapes match and only the claims differ.
    assert report["physical_camera_evidence"] is True
    assert report["physical_unique_frame_claim"] is True
    assert report["camera_thread_claim"] == "physical_capture_device"
    block = report["capture_device"]
    assert block["claim"] == "physical_capture_device"
    assert report["test_id"] == TEST_ID

    # Then: and it records what the board actually delivered, not what was
    # requested. The request is 1280x720 at 60; the artifact states the granted
    # geometry, rate and format so a reader learns the board's answer.
    assert isinstance(block["actual_width"], int)
    assert isinstance(block["actual_height"], int)
    assert isinstance(block["actual_fps"], float)
    assert isinstance(block["fourcc"], str)

    # Then: the required run metrics all survived. These are item 1's list:
    # the delivered shape is proven by decoded frames in camera_reads, the rest
    # are the production counters and cadence summaries, published as data.
    for field in (
        "camera_read_count",
        "camera_unique_sequence_count",
        "camera_duplicate_sequence_count",
        "camera_sequence_regression_count",
        "camera_last_sequence",
        "period_skipped_count",
        "blit_skipped_no_new_frame",
        "suppressed_blit_interval_count",
    ):
        value = report[field]
        assert isinstance(value, int) and not isinstance(value, bool) and value >= 0
    assert isinstance(report["present_summary"], dict)
    assert isinstance(report["dispatch_summary"], dict)
    assert report["present_summary"]["mean_hz"] >= 0.0
    assert report["dispatch_summary"]["mean_hz"] >= 0.0
    assert report["functional_accepted"] is True
