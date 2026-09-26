"""Retained, attributable record of what a real capture board granted. # noqa: SIZE_OK

The periodic-run gap this closes, in one paragraph. The 60 Hz run in
``preview_fps_support`` drives ``SyntheticFrameSource``, so its ``report.json``
records ``claim="not_applicable_synthetic_source"`` even with a real board
attached; and ``test_capture_device_readback.py`` proves the driver honours MJPG
only by *not skipping*, so a board that refuses leaves a skip reason in the
pytest summary and no artifact at all. Nothing retained, anywhere, says what a
board actually granted. This module is that record.

Two decisions shape it. The artifact is written whether or not the request was
honoured, because a silent YUY2 fallback is a measurement and not a failure to
report -- dropping it would be the one outcome worse than a bad board. And it
carries a source pin, because the request is the variable under test: a driver
honouring MJPG says nothing about a run that never asked, and two runs cannot be
compared without knowing which revision asked.

The request order is recorded rather than re-asserted. ``_configure_capture``
already pins MJPG-first on the source, but a source assertion cannot show that
the ordering reached the hardware; the recording proxy below observes the calls
production actually made to the real device.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Final

import cv2
from core.Camera import CaptureDeviceReadback, _configure_capture

TESTS_DIR: Final[Path] = Path(__file__).resolve().parent
REPO_ROOT: Final[Path] = TESTS_DIR.parent

# The formal operating assumption. Named here rather than imported so the
# artifact states the request it made without the reader chasing a config.
REQUESTED_SIZE: Final[tuple[int, int]] = (1280, 720)
REQUESTED_FPS: Final[int] = 60

ARTIFACT_NAME: Final[str] = "capture-device.json"
SCHEMA_VERSION: Final[int] = 1

# The block's two discriminators. A board was read, or there was no board. A
# board that misbehaved is the first claim with the mismatch flags raised, never
# the second: "no board" must not become a place to file a board's failure.
CLAIM_PHYSICAL: Final[str] = "physical_capture_device"
CLAIM_NO_DEVICE: Final[str] = "not_applicable_no_capture_device"

# The requester whose revision the artifact is attributed to.
PINNED_SOURCES: Final[tuple[str, ...]] = ("SerialController/core/Camera.py",)

NO_DEVICE_NOTE: Final[str] = (
    "no capture device opens under cv2.CAP_DSHOW at any probed index"
)

# How many indices to try when no index was named. Matches the range
# test_capture_device_readback probes, so the two agree on what "no device"
# means rather than each scanning a different number of ports.
PROBE_INDEX_LIMIT: Final[int] = 4


class ArtifactExists(RuntimeError):
    """A second write would have replaced evidence already on disk."""


@dataclass(frozen=True, slots=True)
class CaptureDeviceProbe:
    """One probe against a real board, or the recorded absence of one."""

    claim: str
    device_index: int | None
    readback: CaptureDeviceReadback | None
    request_order: tuple[int, ...]
    frame_decoded: bool
    decoded_shape: tuple[int, int, int] | None
    note: str


class _RecordingCapture:
    """A pass-through that records the ``set`` calls production makes.

    ``_configure_capture`` only calls ``set`` and ``get``, so forwarding those
    plus ``read``/``release``/``isOpened`` is complete for that path. Without
    this the ordering would have to be taken from the source rather than from the
    hardware, which is the whole reason the order is in the artifact.
    """

    def __init__(self, capture: Any) -> None:
        self._capture = capture
        self.request_order: list[int] = []

    def set(self, prop: int, value: float) -> bool:
        self.request_order.append(prop)
        return bool(self._capture.set(prop, value))

    def get(self, prop: int) -> float:
        return float(self._capture.get(prop))

    def isOpened(self) -> bool:
        return bool(self._capture.isOpened())

    def read(self) -> tuple[bool, Any]:
        return self._capture.read()

    def release(self) -> None:
        self._capture.release()


def _open(index: int) -> Any:
    """Open exactly as ``Camera.openCamera`` opens on Windows."""
    if os.name == "nt":
        return cv2.VideoCapture(index, cv2.CAP_DSHOW)
    return cv2.VideoCapture(index)


def find_capture_device(limit: int = PROBE_INDEX_LIMIT) -> int | None:
    """The first index that opens, or None. Every handle is released before return.

    A backend that raises while opening is an honest "no device" rather than a
    crash that would lose the run, which is the whole point of a periodic probe.
    """
    for index in range(limit):
        capture: Any = None
        try:
            capture = _open(index)
            if bool(capture.isOpened()):
                return index
        except Exception:  # noqa: BLE001 - a raising backend means "no device"
            return None
        finally:
            if capture is not None:
                capture.release()
    return None


def probe_capture_device(
    *,
    size: tuple[int, int] = REQUESTED_SIZE,
    fps: int = REQUESTED_FPS,
    index: int | None = None,
) -> CaptureDeviceProbe:
    """Apply the production request to a real board and record what came back.

    A named ``index`` is used as given: if it does not open, that is the answer,
    because a reader who asked for a particular index wants that index and not a
    scan of the others. ``None`` scans instead.
    """
    resolved = find_capture_device() if index is None else index
    if resolved is None:
        return CaptureDeviceProbe(
            claim=CLAIM_NO_DEVICE,
            device_index=None,
            readback=None,
            request_order=(),
            frame_decoded=False,
            decoded_shape=None,
            note=NO_DEVICE_NOTE,
        )

    capture = _open(resolved)
    proxy = _RecordingCapture(capture)
    try:
        if not proxy.isOpened():
            return CaptureDeviceProbe(
                claim=CLAIM_NO_DEVICE,
                device_index=None,
                readback=None,
                request_order=(),
                frame_decoded=False,
                decoded_shape=None,
                note=(
                    f"capture device {resolved} did not open: "
                    "cv2.VideoCapture reports isOpened() false"
                ),
            )
        readback = _configure_capture(proxy, size, fps)
        decoded, frame = proxy.read()
        shape = (
            tuple(int(value) for value in frame.shape)
            if decoded and frame is not None
            else None
        )
        return CaptureDeviceProbe(
            claim=CLAIM_PHYSICAL,
            device_index=resolved,
            readback=readback,
            request_order=tuple(proxy.request_order),
            frame_decoded=shape is not None,
            decoded_shape=shape,  # type: ignore[arg-type]
            note="",
        )
    finally:
        capture.release()


def block_of(probe: CaptureDeviceProbe) -> dict[str, Any]:
    """The ``capture_device`` block, in the shape the 60 Hz report already uses.

    The field list is derived from the production dataclass so a field added
    there cannot be silently absent here. A probe with no readback fills the
    measured fields with ``None`` rather than 0, matching the synthetic block:
    zeros would read as a board that reported 0x0 at 0 fps.
    """
    if probe.readback is not None:
        return {"claim": probe.claim, **asdict(probe.readback)}
    block: dict[str, Any] = {name: None for name in _measured_field_names()}
    block["claim"] = probe.claim
    block["fourcc_readable"] = False
    return block


def _measured_field_names() -> tuple[str, ...]:
    return tuple(field.name for field in fields(CaptureDeviceReadback))


def mjpg_honoured(probe: CaptureDeviceProbe) -> bool:
    """Whether a real board granted MJPG. Absent hardware honoured nothing."""
    readback = probe.readback
    return readback is not None and readback.fourcc == "MJPG"


def _source_pins() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative in PINNED_SOURCES:
        path = REPO_ROOT / relative
        payload = path.read_bytes()
        rows.append(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return rows


def _runtime_metadata() -> dict[str, str]:
    return {
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "opencv_version": cv2.__version__,
    }


def build_artifact(
    probe: CaptureDeviceProbe,
    *,
    size: tuple[int, int],
    fps: int,
) -> dict[str, Any]:
    """The full retained record: the request, the outcome, and who asked."""
    return {
        "schema_version": SCHEMA_VERSION,
        "claim": probe.claim,
        "device_index": probe.device_index,
        "mjpg_honoured": mjpg_honoured(probe),
        "request": {"size": [int(size[0]), int(size[1])], "fps": int(fps)},
        "request_order": list(probe.request_order),
        "frame_decoded": probe.frame_decoded,
        "decoded_shape": (
            list(probe.decoded_shape) if probe.decoded_shape is not None else None
        ),
        "capture_device": block_of(probe),
        "note": probe.note,
        "source_pins": _source_pins(),
        "runtime_metadata": _runtime_metadata(),
    }


def write_artifact(directory: Path, artifact: dict[str, Any]) -> None:
    """Write the artifact once. A second write raises rather than replacing it.

    Evidence that can be silently overwritten is not evidence, and a periodic run
    that retries must fail loudly instead of erasing what it measured.
    """
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with (directory / ARTIFACT_NAME).open("x", encoding="utf-8") as handle:
            json.dump(artifact, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise ArtifactExists(
            f"{directory / ARTIFACT_NAME} already exists; a retained artifact is "
            "never overwritten, so point the run at a fresh directory"
        ) from exc
