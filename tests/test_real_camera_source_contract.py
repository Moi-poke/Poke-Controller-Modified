"""RED contracts for a source that measures a physical board, honestly. # noqa: SIZE_OK

The harness can only drive ``SyntheticFrameSource``, so even with a real board
attached the 60 Hz run records ``claim="not_applicable_synthetic_source"`` and
``physical_camera_evidence: False`` in its report. A real source bolted on
without changing that would still report itself as synthetic -- the exact
conflation the brief forbids. So these contracts pin the two things that make a
real-camera run distinguishable, not the run's verdict.

What is pinned here is interface and honesty, not hardware. A fake camera stands
in for the board, so nothing in this file touches hardware and everything runs
in CI; the gated entry point that actually opens a device lives next to the
other E2E entry points. A real source that cannot prove these contracts must not
be admitted, because an unprovable source is indistinguishable from a synthetic
one wearing a costume.
"""

from __future__ import annotations

import importlib
from dataclasses import fields
from typing import Any

import numpy as np
import pytest


def _real_module() -> Any:
    return importlib.import_module("real_camera_source")


def _support_module() -> Any:
    return importlib.import_module("preview_fps_support")


def _readback_type() -> Any:
    module: Any = importlib.import_module("core.Camera")
    return module.CaptureDeviceReadback


class _FakeCamera:
    """A production ``Camera`` that never touches hardware.

    Opens exactly once, delivers a fixed frame, and records that it was asked
    to. Every method the harness or the area can reach exists, so a source
    built on it proves it forwards rather than reimplements.
    """

    def __init__(self) -> None:
        self.capture_size = (1280, 720)
        self.opened = False
        self.destroyed = False
        self.open_calls = 0
        self._frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self._seq = 41

    def openCamera(self, index: int) -> bool:
        self.open_calls += 1
        self.opened = True
        self.open_index = index
        return True

    def isRunning(self) -> bool:
        return self.opened

    def destroy(self) -> bool:
        self.destroyed = True
        self.opened = False
        return True

    def readFrame(self, copy: bool = False) -> np.ndarray | None:
        return self._frame.copy() if copy else self._frame

    def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
        frame = self.readFrame(copy=copy)
        self._seq += 1
        return frame, self._seq

    def readFrameWithTiming(self, copy: bool = False) -> tuple[Any, int, int, int]:
        import time

        frame, sequence = self.readFrameWithSeq(copy=copy)
        now = time.perf_counter_ns()
        return frame, sequence, now, now

    def frame_seq(self) -> int:
        return self._seq

    def getCaptureDeviceReadback(self) -> Any:
        # The production dataclass with fixed honest values, not a look-alike:
        # asdict() rejects anything else, and the block under test is derived
        # from the dataclass fields, so a non-dataclass double would prove
        # nothing about the real shape.
        return _readback_type()(
            requested_width=1280,
            requested_height=720,
            requested_fps=60,
            actual_width=1280,
            actual_height=720,
            actual_fps=60.0002,
            fourcc="YUY2",
            fourcc_readable=True,
            size_not_applied=False,
            fourcc_not_mjpg=True,
            fps_not_applied=False,
        )

    def getStats(self) -> dict[str, float]:
        return {"fps": 60.0, "avg_ms": 0.5}


def _make_source(**kwargs: Any) -> Any:
    module = _real_module()
    return module.RealCameraFrameSource(camera=_FakeCamera(), index=0, **kwargs)


# ===========================================================================
# A. It is the same shape as the synthetic source the harness drives
# ===========================================================================


def test_the_source_exposes_the_harness_interface() -> None:
    # Given/When: a real source over a fake camera.
    source = _make_source()

    # Then: every method the harness reaches exists with the same call shape, so
    # swapping the source does not change the harness. A source that widened the
    # harness's assumptions would make the real run incomparable with the
    # synthetic reference.
    for name in (
        "start",
        "stop",
        "destroy",
        "is_alive",
        "readFrame",
        "readFrameWithSeq",
        "readFrameWithTiming",
        "frame_seq",
        "producer_thread_native_id",
        "camera_records",
        "camera_summary",
    ):
        assert callable(getattr(source, name)), name
    assert source.capture_size == (1280, 720)


def test_a_read_returns_a_real_shaped_frame_with_an_advancing_sequence() -> None:
    # Given: a real source over a fake camera.
    source = _make_source()

    # When: two reads with sequence are taken.
    frame_a, sequence_a = source.readFrameWithSeq()
    frame_b, sequence_b = source.readFrameWithSeq()

    # Then: frames are 720p BGR and the sequence advances, so the beat analysis
    # this enables can distinguish a new frame from a repeated one. A source
    # that returned the same sequence twice would make every later duplicate
    # count meaningless.
    assert frame_a is not None and frame_a.shape == (720, 1280, 3)
    assert sequence_b == sequence_a + 1


def test_reads_are_recorded_as_camera_records() -> None:
    # Given: a real source that has served three reads.
    source = _make_source()
    source.readFrame()
    source.readFrameWithSeq()
    source.readFrame()

    # When: the records are read.
    rows = source.camera_records()

    # Then: all three reads are recorded with present frames and no errors, in
    # the same record shape the synthetic source uses, so one reader handles
    # both. The harness splats these into ``camera_reads.jsonl``, and a shape
    # mismatch there would silently drop the real-camera evidence.
    assert len(rows) == 3
    support = _support_module()
    assert all(isinstance(row, support.CameraReadRecord) for row in rows)
    assert all(row.frame_present for row in rows)
    assert all(row.error is None for row in rows)


# ===========================================================================
# B. It cannot be mistaken for a synthetic source
# ===========================================================================


def test_the_source_declares_itself_physical() -> None:
    # Given/When: both sources, read live.
    real = _real_module().RealCameraFrameSource
    synthetic = _support_module().SyntheticFrameSource

    # Then: the two agree on nothing. The report's two evidence flags are read
    # off these attributes rather than written as literals, so a real run cannot
    # report itself as synthetic and a synthetic run cannot claim a board.
    assert real.physical_camera_evidence is True
    assert real.physical_unique_frame_claim is True
    assert synthetic.physical_camera_evidence is False
    assert synthetic.physical_unique_frame_claim is False


def test_the_summary_claims_a_physical_device_with_the_real_readback() -> None:
    # Given: a real source over a fake camera.
    source = _make_source()

    # When: the summary is read.
    summary = source.camera_summary()

    # Then: it carries the synthetic source's keys plus the physical claim, and
    # the device block is the fake camera's readback rather than the synthetic
    # non-physical values. A reader asking "was a device involved" gets the
    # answer from one field, and asking "what did it grant" gets the numbers.
    synthetic_keys = {
        "camera_read_count",
        "camera_frame_present_count",
        "camera_none_count",
        "camera_read_error_count",
        "camera_unique_sequence_count",
        "camera_duplicate_sequence_count",
        "camera_sequence_regression_count",
        "camera_last_sequence",
        "post_teardown_camera_read_count",
        "camera_thread_claim",
        "capture_device",
    }
    assert synthetic_keys <= set(summary)
    assert summary["camera_thread_claim"] == "physical_capture_device"
    block = summary["capture_device"]
    assert block["claim"] == "physical_capture_device"
    assert block["fourcc"] == "YUY2"
    assert block["fourcc_not_mjpg"] is True
    assert set(block) == {f.name for f in fields(_readback_type())} | {"claim"}


def test_duplicates_are_visible_in_the_summary() -> None:
    # Given: a real source fed the same sequence twice in a row.
    source = _make_source()
    source._record(42, frame_present=True, error=None)
    source._record(42, frame_present=True, error=None)

    # When: the summary is read.
    summary = source.camera_summary()

    # Then: the duplicate count reflects the second one. Item 2's beat analysis
    # lives on these counters, so they have to move on a real source exactly as
    # they do on the synthetic one. A single record can never be a duplicate
    # because there is nothing for it to repeat, so the test records two.
    assert summary["camera_duplicate_sequence_count"] >= 1


# ===========================================================================
# C. It starts, stops, and releases -- loudly
# ===========================================================================


def test_start_opens_the_named_device_and_stop_releases_it() -> None:
    # Given: a real source over a fake camera that has not been opened.
    camera = _FakeCamera()
    module = _real_module()
    source = module.RealCameraFrameSource(camera=camera, index=2)

    # When: the source is started and then stopped.
    assert source.start() is True
    assert camera.opened is True
    assert camera.open_index == 2
    assert source.stop() is True

    # Then: the device was opened at the named index and released on stop. A
    # periodic run that leaked a handle would stop finding the board, and a
    # source that ignored its index would measure a different device than the
    # run claims.
    assert camera.destroyed is True


def test_a_device_that_will_not_open_is_a_loud_failure_not_silent_synthetic() -> None:
    # Given: a camera that refuses to open.
    class ClosedCamera(_FakeCamera):
        def openCamera(self, index: int) -> bool:
            self.open_calls += 1
            return False

    module = _real_module()
    source = module.RealCameraFrameSource(camera=ClosedCamera(), index=0)

    # When/Then: starting raises rather than producing an empty source. A real
    # source that degraded into recording nothing would be a synthetic run in
    # disguise, and the gated entry point must fail at admission rather than
    # retain an artifact that claims a board was measured.
    with pytest.raises(module.DeviceUnavailable):
        source.start()


def test_destroy_after_a_failed_start_still_releases_nothing_safely() -> None:
    # Given: a source whose device never opened.
    class ClosedCamera(_FakeCamera):
        def openCamera(self, index: int) -> bool:
            return False

    module = _real_module()
    source = module.RealCameraFrameSource(camera=ClosedCamera(), index=0)

    # When/Then: destroying it does not raise. Teardown must be total even on
    # the failure path, or a gated run that fails to admit leaves a handle or a
    # thread behind for the next run to trip over.
    assert source.destroy() is True
