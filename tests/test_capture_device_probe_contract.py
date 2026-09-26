"""RED contracts for the retained capture-device artifact. # noqa: SIZE_OK

The periodic run this file exists for is the one gap left in Task 4.
``test_capture_device_readback.py`` proves the readback logic and, on a real
board, proves the driver honours MJPG -- but when the driver refuses it
``pytest.skip``s, and a skip reason lives in the pytest summary and nowhere
else. ``preview_fps_support`` compounds this: its 60 Hz run drives
``SyntheticFrameSource``, so ``report.json`` records
``claim="not_applicable_synthetic_source"`` even with a real board attached. No
periodic run therefore produces an attributable record of what a board actually
granted, on any machine.

The artifact has to be retained whether or not the request was honoured -- a
YUY2 fallback is a measurement, not a failure to report. So these contracts pin
honesty and attributability, not a verdict: a machine fact cannot be a CI gate,
so nothing here asserts MJPG. ``mjpg_honoured`` is published as data for a
scheduler to gate on elsewhere.

The block's field list is never written out here. It is derived from the
production ``CaptureDeviceReadback``, and agreement with the existing report
block is asserted on real output against ``test_capture_device_readback``, so
one reader handles the 60 Hz report and this artifact alike.
"""

from __future__ import annotations

import importlib
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from test_capture_device_readback import CAPTURE_DEVICE_KEYS


def _probe_module() -> Any:
    return importlib.import_module("capture_device_probe")


def _readback_type() -> Any:
    module: Any = importlib.import_module("core.Camera")
    return module.CaptureDeviceReadback


class _RecordingCapture:
    """A ``cv2.VideoCapture`` double recording its sets and replaying its gets.

    ``state`` is the device's own answer and no ``set`` can move it, which is the
    silent fallback verbatim: the driver accepts the call and grants something
    else. The production request is applied by the real ``_configure_capture``,
    so the ordering recorded in the artifact is the shipped ordering.
    """

    def __init__(self, state: dict[str, float] | None = None, *, opened: bool = True):
        self.sets: list[tuple[int, float]] = []
        self.released = False
        self.opened = opened
        values = state or {}
        self._values: dict[int, float] = {
            cv2.CAP_PROP_FRAME_WIDTH: values.get("width", 1280.0),
            cv2.CAP_PROP_FRAME_HEIGHT: values.get("height", 720.0),
            cv2.CAP_PROP_FPS: values.get("fps", 60.0),
            cv2.CAP_PROP_FOURCC: values.get("fourcc", 0.0),
        }
        self.read_ok = bool(values.get("read_ok", True))

    def set(self, prop: int, value: float) -> bool:
        self.sets.append((prop, value))
        return True

    def get(self, prop: int) -> float:
        return self._values.get(prop, 0.0)

    def isOpened(self) -> bool:
        return self.opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        width = int(self._values[cv2.CAP_PROP_FRAME_WIDTH])
        height = int(self._values[cv2.CAP_PROP_FRAME_HEIGHT])
        return self.read_ok, np.zeros((height, width, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True

    @property
    def properties_set(self) -> tuple[int, ...]:
        return tuple(prop for prop, _value in self.sets)


def _fourcc(name: str) -> int:
    return int(cv2.VideoWriter_fourcc(*name))  # type: ignore[attr-defined]


def _run_probe(
    monkeypatch: pytest.MonkeyPatch, state: dict[str, float] | None = None
) -> tuple[Any, Any, _RecordingCapture]:
    """Drive the real probe against a device double, returning the capture too."""
    module = _probe_module()
    capture = _RecordingCapture(state)
    monkeypatch.setattr(cv2, "VideoCapture", lambda *_a, **_k: capture)
    probe = module.probe_capture_device(
        size=module.REQUESTED_SIZE, fps=module.REQUESTED_FPS, index=0
    )
    return module, probe, capture


def _absent_probe(module: Any) -> Any:
    """A no-device probe, constructed without touching hardware."""
    return module.CaptureDeviceProbe(
        claim=module.CLAIM_NO_DEVICE,
        device_index=None,
        readback=None,
        request_order=(),
        frame_decoded=False,
        decoded_shape=None,
        note="constructed for a headless contract",
    )


# ===========================================================================
# A. The block is derived from production, never re-listed
# ===========================================================================


def test_a_measured_block_is_exactly_the_readback_plus_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a device that honoured the request.
    module, probe, _capture = _run_probe(
        monkeypatch, {"fourcc": float(_fourcc("MJPG"))}
    )

    # When: the block is built.
    block = module.block_of(probe)

    # Then: its fields are the production dataclass's fields plus one
    # discriminator. A hand-maintained list would drift when a field is added and
    # the drift would be invisible: the artifact would still serialise, just
    # without the new measurement.
    assert set(block) == {f.name for f in fields(_readback_type())} | {"claim"}
    assert block["claim"] == module.CLAIM_PHYSICAL


def test_a_measured_block_carries_the_readback_own_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a device that honoured the request.
    module, probe, _capture = _run_probe(
        monkeypatch, {"fourcc": float(_fourcc("MJPG"))}
    )

    # When: the block is built.
    block = module.block_of(probe)

    # Then: every measured value is the readback's own field value, not a
    # restatement of the request. A block that echoed the request back would make
    # the artifact a copy of the intent rather than a record of the outcome.
    readback = asdict(probe.readback)
    assert readback["requested_width"] == module.REQUESTED_SIZE[0]
    assert readback["fourcc"] == "MJPG"
    for key, value in readback.items():
        assert block[key] == value, key


def test_the_block_agrees_with_the_sixty_hz_report_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given/When: a real block and the shape the 60 Hz report uses for the same
    # name. Then: they are the same shape, so one reader handles the synthetic
    # report and this artifact, and a field added to either is a contract failure
    # rather than a field some reader silently misses.
    module, probe, _capture = _run_probe(
        monkeypatch, {"fourcc": float(_fourcc("MJPG"))}
    )
    assert set(module.block_of(probe)) == set(CAPTURE_DEVICE_KEYS)


# ===========================================================================
# B. The ordering is recorded, so MJPG-first is observable on the device
# ===========================================================================


def test_the_applied_request_order_is_recorded_with_fourcc_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a cooperating device.
    _module, probe, capture = _run_probe(
        monkeypatch, {"fourcc": float(_fourcc("MJPG"))}
    )

    # When: the order the production request actually reached the device is read.
    # Then: it is the recorded order, and FOURCC precedes both resolution
    # properties. test_capture_device_readback pins this on the source; recording
    # it means a periodic run's artifact shows the ordering that reached the
    # hardware, which a source assertion cannot. The whole order is pinned so a
    # reordering cannot hide behind a satisfied first element.
    assert probe.request_order == capture.properties_set
    assert probe.request_order == (
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
        cv2.CAP_PROP_BUFFERSIZE,
    )


# ===========================================================================
# C. A fallback is a measurement, and it is retained
# ===========================================================================


def test_a_silent_fallback_is_retained_rather_than_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a device granting the requested size and rate but YUY2, which is
    # exactly this machine's NVIDIA virtual camera.
    module, probe, _capture = _run_probe(
        monkeypatch, {"fourcc": float(_fourcc("YUY2")), "fps": 60.0}
    )

    # When: the block is built.
    block = module.block_of(probe)

    # Then: it is a physical claim carrying the measured answer and the raised
    # mismatch flag. The probe must not turn a silent fallback into an absent
    # device, and must not drop it: this is the run a reader consults to learn
    # the board ignored MJPG.
    assert block["claim"] == module.CLAIM_PHYSICAL
    assert block["fourcc"] == "YUY2"
    assert block["fourcc_not_mjpg"] is True
    assert block["size_not_applied"] is False
    assert block["fps_not_applied"] is False
    assert block["actual_fps"] == pytest.approx(60.0)
    assert probe.device_index == 0


def test_the_honoured_verdict_is_published_as_data_not_asserted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one device that honoured MJPG and one that did not.
    module = _probe_module()
    _m, granted, _c = _run_probe(monkeypatch, {"fourcc": float(_fourcc("MJPG"))})
    _m, refused, _c = _run_probe(monkeypatch, {"fourcc": float(_fourcc("YUY2"))})

    # When/Then: each records the truth as a boolean and neither raises. A
    # machine fact is not a code failure, so the scheduler reads the flag rather
    # than the probe deciding the run failed.
    assert module.mjpg_honoured(granted) is True
    assert module.mjpg_honoured(refused) is False


def test_an_absent_device_is_never_reported_as_honouring_mjpg() -> None:
    # Given: a machine with no board.
    module = _probe_module()

    # When/Then: the verdict is False rather than an optimistic True. Absent
    # hardware has honoured nothing, and a scheduler keying on this flag must not
    # read "no board" as "the board passed".
    assert module.mjpg_honoured(_absent_probe(module)) is False


def test_a_decoded_frame_is_required_before_a_delivered_size_is_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a device whose driver echoes a size it cannot actually deliver.
    _module, probe, _capture = _run_probe(
        monkeypatch, {"fourcc": float(_fourcc("MJPG")), "read_ok": False}
    )

    # Then: the probe says the stream was not decoded and the delivered shape is
    # None rather than the echoed request. A driver that reports a size and then
    # delivers nothing is exactly what a decoded frame is the only guard for.
    assert probe.frame_decoded is False
    assert probe.decoded_shape is None


def test_a_decoded_frame_records_the_shape_actually_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a device granting 640x480 while 1280x720 was requested.
    _module, probe, _capture = _run_probe(
        monkeypatch,
        {"fourcc": float(_fourcc("MJPG")), "width": 640.0, "height": 480.0},
    )

    # When/Then: the decoded shape is recorded, so the artifact carries proof of
    # the delivered geometry rather than only the driver's claim about it.
    assert probe.frame_decoded is True
    assert probe.decoded_shape == (480, 640, 3)


def test_the_device_is_released_however_the_probe_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a cooperating device.
    _module, _probe, capture = _run_probe(
        monkeypatch, {"fourcc": float(_fourcc("MJPG"))}
    )

    # When: the probe completed.
    # Then: the device is released. A periodic run that leaked a handle per run
    # would stop finding a board after enough runs, and the failure would look
    # like the hardware going away.
    assert capture.released is True


# ===========================================================================
# D. An absent device is distinguishable from a broken one
# ===========================================================================


def test_a_machine_with_no_device_says_so_without_claiming_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a machine where the device does not open.
    module = _probe_module()
    monkeypatch.setattr(
        cv2,
        "VideoCapture",
        lambda *_a, **_k: _RecordingCapture({}, opened=False),
    )

    # When: the probe runs and builds its block.
    probe = module.probe_capture_device(
        size=module.REQUESTED_SIZE, fps=module.REQUESTED_FPS, index=0
    )
    block = module.block_of(probe)

    # Then: the claim separates "no board" from "a board that misbehaved", and
    # every measured field is None. Zeros would read as a board that reported
    # 0x0 at 0 fps in an unreadable format, which is a different fault entirely.
    # None rather than the "unreadable" sentinel too: that sentinel is what
    # _configure_capture emits for a device which opened and could not name its
    # own stream, and reusing it here would collapse "no board" into the fault
    # the sentinel exists to distinguish. It also matches the shape the 60 Hz
    # report already uses for a source that never had a device.
    assert block["claim"] == module.CLAIM_NO_DEVICE
    assert probe.readback is None
    assert probe.device_index is None
    assert set(block) == set(CAPTURE_DEVICE_KEYS)
    for key in set(CAPTURE_DEVICE_KEYS) - {
        "claim",
        "fourcc_readable",
        "size_not_applied",
        "fourcc_not_mjpg",
        "fps_not_applied",
    }:
        assert block[key] is None, key
    assert block["fourcc_readable"] is False


# ===========================================================================
# E. The artifact is attributable and immutable
# ===========================================================================


def test_the_artifact_pins_camera_py_so_a_reading_can_name_its_revision() -> None:
    # Given/When: an artifact built from a no-device probe.
    module = _probe_module()
    artifact = module.build_artifact(
        _absent_probe(module), size=module.REQUESTED_SIZE, fps=module.REQUESTED_FPS
    )

    # Then: it carries Camera.py's size and digest. A board measurement without
    # the code revision that requested it cannot be compared across runs, and the
    # request is the variable under test: a driver honouring MJPG says nothing if
    # the run never asked for MJPG.
    pins = {row["path"]: row for row in artifact["source_pins"]}
    camera = pins["SerialController/core/Camera.py"]
    assert camera["bytes"] > 0
    assert len(camera["sha256"]) == 64
    assert artifact["schema_version"] == module.SCHEMA_VERSION


def test_the_artifact_names_the_request_it_made() -> None:
    # Given: an artifact built from a no-device probe.
    module = _probe_module()
    artifact = module.build_artifact(
        _absent_probe(module), size=module.REQUESTED_SIZE, fps=module.REQUESTED_FPS
    )

    # Then: the request is stated at the top level, so a reader learns what was
    # asked for without first decoding the block, and the request order travels
    # with it rather than only inside the probe.
    assert artifact["request"] == {
        "size": [module.REQUESTED_SIZE[0], module.REQUESTED_SIZE[1]],
        "fps": module.REQUESTED_FPS,
    }
    assert artifact["request_order"] == []


def test_the_artifact_records_the_verdict_alongside_the_block() -> None:
    # Given: a physical-claim probe with no readback.
    module = _probe_module()
    probe = module.CaptureDeviceProbe(
        claim=module.CLAIM_PHYSICAL,
        device_index=2,
        readback=None,
        request_order=(cv2.CAP_PROP_FOURCC,),
        frame_decoded=False,
        decoded_shape=None,
        note="",
    )

    # When: the artifact is built.
    artifact = module.build_artifact(
        probe, size=module.REQUESTED_SIZE, fps=module.REQUESTED_FPS
    )

    # Then: the verdict and the device index are top level, so a scheduler does
    # not have to reach into the block to decide whether to keep the run.
    assert artifact["mjpg_honoured"] is False
    assert artifact["device_index"] == 2
    assert artifact["claim"] == module.CLAIM_PHYSICAL


def test_writing_the_artifact_refuses_to_overwrite_an_existing_one(
    tmp_path: Path,
) -> None:
    # Given: a directory already holding an artifact from an earlier run.
    module = _probe_module()
    artifact = module.build_artifact(
        _absent_probe(module), size=module.REQUESTED_SIZE, fps=module.REQUESTED_FPS
    )
    module.write_artifact(tmp_path, artifact)
    original = (tmp_path / module.ARTIFACT_NAME).read_text(encoding="utf-8")

    # When/Then: a second write is refused rather than replacing the first.
    # Evidence that can be silently overwritten is not evidence; a periodic run
    # that retries must fail loudly instead of erasing what it measured.
    with pytest.raises(module.ArtifactExists):
        module.write_artifact(tmp_path, artifact)
    assert (tmp_path / module.ARTIFACT_NAME).read_text(encoding="utf-8") == original


def test_writing_the_artifact_creates_the_directory_and_pretty_prints(
    tmp_path: Path,
) -> None:
    # Given: a directory that does not exist yet.
    module = _probe_module()
    target = tmp_path / "nested" / "run-1"
    artifact = module.build_artifact(
        _absent_probe(module), size=module.REQUESTED_SIZE, fps=module.REQUESTED_FPS
    )

    # When: the artifact is written.
    module.write_artifact(target, artifact)

    # Then: the tree is created and the file is indented, sorted JSON ending in a
    # newline, matching every other artifact in this project so a reader can diff
    # two runs directly.
    text = (target / module.ARTIFACT_NAME).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "\n  " in text
