"""Opt-in hardware entry point for the retained capture-device record. # noqa: SIZE_OK

The periodic run. Everything here is gated off by default, because the claim it
makes is a fact about a machine rather than about the code: a board that
silently falls back to YUY2 has not broken anything, and a CI lane must not go
red because the hardware under it is not an MJPG board.

What makes it safe to run on a schedule anyway is that the artifact is retained
either way. The failure this exists to prevent is a run that measured YUY2,
learned nothing durable, and reported success -- so ``REQUIRE_MJPG`` stays off
by default and the run passes with ``mjpg_honoured: false`` in the artifact,
which is a retained, attributable answer rather than a silent one. A hardware
lane that owns a real board sets it and gets a real gate.

The evidence directory must be fresh. ``capture_device_probe.write_artifact``
refuses to overwrite, and a second write failing here is the intended signal: a
periodic run that reuses a directory is about to erase a measurement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final

import capture_device_probe as probe_module
import cv2
import pytest

GATE_ENV: Final[str] = "POKECON_RUN_CAPTURE_DEVICE_PROBE"
EVIDENCE_DIR_ENV: Final[str] = "POKECON_CAPTURE_DEVICE_EVIDENCE_DIR"
INDEX_ENV: Final[str] = "POKECON_CAPTURE_DEVICE_INDEX"
REQUIRE_MJPG_ENV: Final[str] = "POKECON_CAPTURE_DEVICE_REQUIRE_MJPG"

# The order the production request must have reached the hardware. Pinned again
# here against the artifact rather than only against the source, because this run
# exists to record what the board saw.
EXPECTED_REQUEST_ORDER: Final[tuple[int, ...]] = (
    cv2.CAP_PROP_FOURCC,
    cv2.CAP_PROP_FRAME_WIDTH,
    cv2.CAP_PROP_FRAME_HEIGHT,
    cv2.CAP_PROP_FPS,
    cv2.CAP_PROP_BUFFERSIZE,
)


def _gate_is_on() -> bool:
    return os.environ.get(GATE_ENV, "").strip() == "1"


def _required_mjpg() -> bool:
    return os.environ.get(REQUIRE_MJPG_ENV, "").strip() == "1"


def _evidence_dir(suffix: str) -> Path:
    raw = os.environ.get(EVIDENCE_DIR_ENV, "").strip()
    if not raw:
        pytest.fail(
            f"{EVIDENCE_DIR_ENV} is required when {GATE_ENV}=1; the artifact is "
            "the deliverable and must not be written beside the sources"
        )
    # Each entry point gets its own subdirectory. The two runs below both write,
    # and the artifact is never overwritable, so a shared directory would make one
    # test destroy the other's evidence instead of failing on its own claim.
    return Path(raw) / suffix


def _requested_index() -> int | None:
    raw = os.environ.get(INDEX_ENV, "").strip()
    return int(raw) if raw else None


def _run_and_write(directory: Path, index: int | None) -> dict[str, Any]:
    """Probe the real board and retain the artifact. Returns it as re-read.

    Re-reading from disk rather than returning the in-memory dict is the point of
    the exercise: the artifact is the deliverable, so what a reader will open is
    what gets asserted on.
    """
    probe = probe_module.probe_capture_device(
        size=probe_module.REQUESTED_SIZE,
        fps=probe_module.REQUESTED_FPS,
        index=index,
    )
    artifact = probe_module.build_artifact(
        probe,
        size=probe_module.REQUESTED_SIZE,
        fps=probe_module.REQUESTED_FPS,
    )
    probe_module.write_artifact(directory, artifact)
    reloaded: Any = json.loads(
        (directory / probe_module.ARTIFACT_NAME).read_text(encoding="utf-8")
    )
    return reloaded


def test_retained_capture_device_artifact_records_the_real_board() -> None:
    # Given: a scheduled hardware run, pointed at a fresh evidence directory.
    if not _gate_is_on():
        pytest.skip(f"set {GATE_ENV}=1 on a machine with a capture board to run")
    directory = _evidence_dir("board")
    index = _requested_index()

    # When: the real board is probed through the production request.
    artifact = _run_and_write(directory, index)

    # Then: the artifact names the request it made and the revision that made it,
    # so the measurement is comparable across runs rather than a bare number.
    assert artifact["schema_version"] == probe_module.SCHEMA_VERSION
    assert artifact["request"] == {
        "size": [
            probe_module.REQUESTED_SIZE[0],
            probe_module.REQUESTED_SIZE[1],
        ],
        "fps": probe_module.REQUESTED_FPS,
    }
    pins = {row["path"]: row for row in artifact["source_pins"]}
    assert len(pins["SerialController/core/Camera.py"]["sha256"]) == 64

    # Then: and it separates an absent board from a board that misbehaved, so a
    # reader can tell "nothing was attached" from "what was attached ignored
    # MJPG" without parsing prose.
    assert artifact["claim"] in {
        probe_module.CLAIM_PHYSICAL,
        probe_module.CLAIM_NO_DEVICE,
    }
    block = artifact["capture_device"]
    assert block["claim"] == artifact["claim"]

    # Then: MJPG-first is shown to have reached the hardware, which is the one
    # claim a source assertion cannot make. It is checked only when a board was
    # actually there, since no device received no request.
    if artifact["claim"] == probe_module.CLAIM_PHYSICAL:
        assert tuple(artifact["request_order"]) == EXPECTED_REQUEST_ORDER

    # Then: and the verdict is published rather than asserted, unless this lane
    # owns a real board and opted into the gate.
    if _required_mjpg():
        assert artifact["mjpg_honoured"] is True, (
            f"capture board {artifact['device_index']} did not honour MJPG: "
            f"fourcc={block['fourcc']!r} at "
            f"{block['actual_width']}x{block['actual_height']} "
            f"{block['actual_fps']}fps; the artifact is retained at "
            f"{directory / probe_module.ARTIFACT_NAME}"
        )


def test_a_periodic_run_cannot_silently_reuse_an_evidence_directory() -> None:
    # Given: a scheduled run pointed at a directory a previous run already used.
    if not _gate_is_on():
        pytest.skip(f"set {GATE_ENV}=1 on a machine with a capture board to run")
    directory = _evidence_dir("reuse")
    index = _requested_index()
    _run_and_write(directory, index)

    # When/Then: the second run is refused. A schedule that reuses a directory
    # would otherwise overwrite the previous measurement, and the run that notices
    # is the run that has already lost the earlier one.
    with pytest.raises(probe_module.ArtifactExists):
        _run_and_write(directory, index)
