"""A source that measures a physical board, and says so in its own shape. # noqa: SIZE_OK

The harness drives ``SyntheticFrameSource`` and reports
``physical_camera_evidence: False``, so a real board attached during a 60 Hz run
is still recorded as ``not_applicable_synthetic_source``. A real source that
reported those same values would be synthetic in disguise, so this module's
whole job is to make the disguise structurally impossible: the two evidence
flags are ``True`` on the class, the claim is physical, and a device that will
not open is a loud ``DeviceUnavailable`` rather than an empty stream.

The shape is deliberately the synthetic shape -- the same method names, the same
``camera_summary`` keys, the same ``CameraReadRecord`` rows -- so one harness
and one reader handle both. The only differences are the ones that answer "was
a device involved" and "what did it grant". A real source whose summary a
synthetic reader cannot parse has failed its only purpose.
"""

from __future__ import annotations

import importlib
import threading
import time
from dataclasses import asdict, fields
from typing import Any, Final

REAL_SOURCE_SIZE: Final[tuple[int, int]] = (1280, 720)
REAL_SOURCE_FPS: Final[int] = 60

CLAIM_PHYSICAL: Final[str] = "physical_capture_device"
THREAD_CLAIM_PHYSICAL: Final[str] = "physical_capture_device"


class DeviceUnavailable(RuntimeError):
    """The named device would not open. There is nothing to measure, so the
    gated entry point must fail at admission rather than retain an artifact
    that claims a board was involved."""


def _default_camera(fps: int, capture_size: tuple[int, int]) -> Any:
    """The production ``Camera`` through an ``Any`` boundary.

    ``AGENTS.md`` sanctions the boundary for stub friction: the import shape
    here matches every other test module that touches production, and it keeps
    the source constructible without hardware for contract runs that inject a
    double instead.
    """
    module: Any = importlib.import_module("core.Camera")
    return module.Camera(fps=fps, capture_size=capture_size)


def _readback_field_names() -> tuple[str, ...]:
    module: Any = importlib.import_module("core.Camera")
    return tuple(field.name for field in fields(module.CaptureDeviceReadback))


class RealCameraFrameSource:
    """The production ``Camera`` presented as a harness source.

    ``camera`` is injected so contracts run headless; the gated entry point
    passes ``None`` and the production camera is built. ``index`` is the device
    the run claims to have measured, and it is the only index touched -- a
    source that scanned for a board would attribute the wrong device's numbers
    to the named one.
    """

    physical_camera_evidence = True
    physical_unique_frame_claim = True

    def __init__(
        self,
        *,
        index: int,
        fps: int = REAL_SOURCE_FPS,
        capture_size: tuple[int, int] = REAL_SOURCE_SIZE,
        camera: Any | None = None,
    ) -> None:
        self._index = int(index)
        self._fps = int(fps)
        self._capture_size = (int(capture_size[0]), int(capture_size[1]))
        self._camera = (
            camera if camera is not None else _default_camera(fps, capture_size)
        )
        self._lock = threading.Lock()
        self._last_read_seq: int | None = None
        self._last_read_thread: int = 0
        self._reads: list[Any] = []
        self._stopped = False
        self._started = False

    @property
    def device_index(self) -> int:
        return self._index

    @property
    def capture_size(self) -> tuple[int, int]:
        size = getattr(self._camera, "capture_size", None)
        if isinstance(size, tuple) and len(size) == 2:
            return (int(size[0]), int(size[1]))
        return self._capture_size

    def start(self) -> bool:
        opened = bool(self._camera.openCamera(self._index))
        if not opened:
            raise DeviceUnavailable(
                f"capture device {self._index} would not open: "
                "cv2.VideoCapture reports isOpened() false, so there is no "
                "board here to measure and no synthetic stream will stand in"
            )
        with self._lock:
            self._started = True
            self._stopped = False
        return True

    def stop(self) -> bool:
        camera, started = self._camera, self._started
        with self._lock:
            self._stopped = True
            self._started = False
        if camera is None or not started:
            return True
        result = camera.destroy()
        return bool(result)

    def destroy(self) -> bool:
        return self.stop()

    def is_alive(self) -> bool:
        running = getattr(self._camera, "isRunning", None)
        if callable(running):
            try:
                return bool(running())
            except Exception:  # noqa: BLE001 - liveness must never raise
                return False
        return self._started and not self._stopped

    def _record(self, sequence: int, *, frame_present: bool, error: str | None) -> None:
        support: Any = importlib.import_module("preview_fps_support")
        duplicate = self._last_read_seq == sequence
        regression = self._last_read_seq is not None and sequence < self._last_read_seq
        self._reads.append(
            support.CameraReadRecord(
                perf_counter_ns=time.perf_counter_ns(),
                thread_native_id=threading.get_native_id(),
                sequence=int(sequence),
                frame_present=bool(frame_present),
                duplicate=bool(duplicate),
                regression=bool(regression),
                error=error,
                post_teardown=self._stopped,
            )
        )
        self._last_read_seq = int(sequence)
        self._last_read_thread = threading.get_native_id()

    def readFrame(self, copy: bool = False) -> Any:
        frame = self._camera.readFrame(copy=copy)
        with self._lock:
            self._record(
                self._camera.frame_seq(),
                frame_present=frame is not None,
                error=None,
            )
        return frame

    def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
        frame, sequence = self._camera.readFrameWithSeq(copy=copy)
        with self._lock:
            self._record(
                int(sequence),
                frame_present=frame is not None,
                error=None,
            )
        return frame, int(sequence)

    def readFrameWithTiming(self, copy: bool = False) -> tuple[Any, int, int, int]:
        frame, sequence = self._camera.readFrameWithSeq(copy=copy)
        now = time.perf_counter_ns()
        with self._lock:
            self._record(
                int(sequence),
                frame_present=frame is not None,
                error=None,
            )
        return frame, int(sequence), now, now

    def frame_seq(self) -> int:
        return int(self._camera.frame_seq())

    def producer_thread_native_id(self) -> int:
        # The acquisition thread belongs to the production Camera, which exposes
        # no public thread id. Reporting a fabricated one would be worse than
        # reporting none, so this is the most recent reader thread, or 0 before
        # any read -- and it is documented as such wherever it lands.
        with self._lock:
            return int(self._last_read_thread)

    def camera_records(self) -> tuple[Any, ...]:
        with self._lock:
            return tuple(self._reads)

    def _capture_device_block(self) -> dict[str, Any]:
        readback = None
        getter = getattr(self._camera, "getCaptureDeviceReadback", None)
        if callable(getter):
            try:
                readback = getter()
            except Exception:  # noqa: BLE001 - a failing getter is not a measurement
                readback = None
        if readback is not None:
            return {"claim": CLAIM_PHYSICAL, **asdict(readback)}
        block: dict[str, Any] = {name: None for name in _readback_field_names()}
        block["claim"] = CLAIM_PHYSICAL
        block["fourcc_readable"] = False
        return block

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
            "camera_thread_claim": THREAD_CLAIM_PHYSICAL,
            "capture_device": self._capture_device_block(),
        }
