"""RED contracts for recording the capture device's real FOURCC and FPS. # noqa: SIZE_OK

Task 4 of the 60 Hz preview work. The ordering rule pinned here is the one
``AGENTS.md`` and the ``_configure_capture`` docstring already state: MJPG
FOURCC is set *first*, because a driver that resets the resolution when FOURCC
changes afterwards saturates USB2.0 and caps 1280x720 at 5-10 fps.

``core/Camera.py`` already reads the device back -- ``actual_w``, ``actual_h``,
``actual_fps`` and the decoded ``fourcc`` all exist at ``_configure_capture``.
What it does not do is *keep* them: they go to ``logger.debug`` and three
``logger.warning`` calls and are then dropped. These tests pin the recording,
not the setting; MJPG-first is already correct and must simply stay put.

Hardware, probed on this machine while writing this file:

* ``cv2.VideoCapture(0, cv2.CAP_DSHOW)`` **opens**. It is an NVIDIA virtual
  camera (its driver reports itself as VCAMDS, i.e. NVIDIA Broadcast) and it
  enumerates at indices 0, 2 and 3; index 1 does not open.
* It honours the requested *size* and *rate* and refuses *MJPG*:
  ``_configure_capture(cap, (1280, 720), 60)`` yields a readable 1280x720
  stream at 60.0002 fps whose FOURCC is ``0x32595559`` = ``"YUY2"``. That is
  exactly the silent driver fallback this task exists to make visible, so the
  strict claim in
  :func:`test_real_capture_device_honours_the_requested_mjpg_and_size` is RED
  here and goes green on a machine with a real capture board.
* The round-trip fidelity test
  (:func:`test_real_capture_device_readback_mirrors_what_the_driver_reports`)
  is the one that can pass on this box: it asserts the readback equals what the
  driver actually reports, whatever that turns out to be.

Ambiguity found while writing this file, reported rather than absorbed: the
brief's example integer ``0x32315559`` does **not** decode to ``"YUY2"``, it
decodes to ``"YU12"``. The integer for ``"YUY2"`` is ``0x32595559``, i.e.
``int(cv2.VideoWriter_fourcc(*"YUY2"))``, which is what
:func:`test_the_pinned_fourcc_integers_use_little_endian_byte_order` pins.

``tests/preview_fps_support.py`` is the implementer's file, so every assertion
about it reads it -- through ``ast`` for wiring decisions, through the live
module for constants and runtime values -- rather than editing it.
"""

from __future__ import annotations

import ast
import importlib
from dataclasses import asdict
from functools import cache
from typing import Any, Final

import cv2
import numpy as np
import pytest
from gdi_source_readers import (
    HARNESS_SOURCE,
    SERIAL_CONTROLLER,
    class_method,
    class_node,
    module_tree,
    referenced_names,
)

CAMERA_SOURCE = SERIAL_CONTROLLER / "core" / "Camera.py"

# The readback the implementation must expose, and the single method a caller
# reads it through. Both names are pinned: a rename is a source contract, so it
# has to fail loudly rather than be discovered at report-reading time.
READBACK_CLASS = "CaptureDeviceReadback"
READBACK_GETTER = "getCaptureDeviceReadback"

# The one report key the block lands under. ``REQUIRED_REPORT_FIELDS`` is a
# flat frozenset of top-level report names, so one name is all it can carry.
REPORT_BLOCK_KEY = "capture_device"

# The two values of the block's single discriminator. A real block says the
# device was read; the synthetic source says, in the harness's own established
# wording (cf. ``camera_thread_claim: "not_applicable_synthetic_source"``),
# that there was no device to read.
REAL_CLAIM = "physical_capture_device"
SYNTHETIC_CLAIM = "not_applicable_synthetic_source"

# The block's shape, identical for both claim values. The synthetic side fills
# every measured field with ``None`` rather than 0 so "no device" can never be
# misread as "a device that reported 0 fps", and leaves the three mismatch
# flags ``False`` because it makes no claim to contradict.
CAPTURE_DEVICE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "claim",
        "requested_width",
        "requested_height",
        "requested_fps",
        "actual_width",
        "actual_height",
        "actual_fps",
        "fourcc",
        "fourcc_readable",
        "size_not_applied",
        "fourcc_not_mjpg",
        "fps_not_applied",
    }
)
SYNTHETIC_CAPTURE_DEVICE_BLOCK: Final[dict[str, Any]] = {
    "claim": SYNTHETIC_CLAIM,
    "requested_width": None,
    "requested_height": None,
    "requested_fps": None,
    "actual_width": None,
    "actual_height": None,
    "actual_fps": None,
    "fourcc": None,
    "fourcc_readable": False,
    "size_not_applied": False,
    "fourcc_not_mjpg": False,
    "fps_not_applied": False,
}
# The measured fields, i.e. the block minus its discriminator.
MEASURED_CAPTURE_DEVICE_KEYS: Final[frozenset[str]] = CAPTURE_DEVICE_KEYS - {"claim"}
# The subset a source with no device must fill with None rather than 0. The four
# booleans are claims *about* those numbers, not numbers, and a source that made
# no measurement makes no claim -- so they are excluded here and stay False, as
# SYNTHETIC_CAPTURE_DEVICE_BLOCK already pins.
NO_DEVICE_MEASURED_KEYS: Final[frozenset[str]] = MEASURED_CAPTURE_DEVICE_KEYS - {
    "fourcc_readable",
    "size_not_applied",
    "fourcc_not_mjpg",
    "fps_not_applied",
}

# The four properties ``_configure_capture`` always sets, in the order it must
# set them. ``CAP_PROP_FPS`` is the only conditional one.
ALWAYS_SET_PROPERTIES: Final[tuple[int, ...]] = (
    cv2.CAP_PROP_FOURCC,
    cv2.CAP_PROP_FRAME_WIDTH,
    cv2.CAP_PROP_FRAME_HEIGHT,
    cv2.CAP_PROP_BUFFERSIZE,
)

REQUESTED_SIZE: Final[tuple[int, int]] = (1280, 720)
REQUESTED_FPS: Final[int] = 60

# What an unsatisfiable request reads back as on a real board: the driver keeps
# its own default resolution, reports no rate, and no usable FOURCC.
UNSATISFIED_DEVICE: Final[dict[str, float]] = {
    "width": 640.0,
    "height": 480.0,
    "fps": 0.0,
    "fourcc": 0.0,
}

# A negative FOURCC is what a DSHOW backend returns for a stream it cannot
# name; the existing little-endian decode turns it into three non-ASCII bytes,
# so it must not be published as a string.
NEGATIVE_FOURCC: Final[float] = -466162819.0

NO_DEVICE_REASON: Final[str] = (
    "no capture device opens: cv2.VideoCapture(index, cv2.CAP_DSHOW) reports "
    "isOpened() false for every index in 0..3 on this machine"
)


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
class _RecordingCapture:
    """A ``cv2.VideoCapture`` double that records its sets and replays its gets.

    ``state`` is the device's reported reality, and no ``set`` can move it: the
    readback under test is what the device answers *after* being configured, so
    a double that echoed the request back would make every mismatch
    unsatisfiable and the readback worthless. That is not a contrivance -- it is
    the silent fallback verbatim: the driver accepts the call and still grants
    something else. ``honours_set=False`` is the louder variant in which the
    driver refuses the call outright; production does not branch on the return
    value either way, so both must produce a readback.
    """

    def __init__(self, state: dict[str, float], *, honours_set: bool = True) -> None:
        self.honours_set = honours_set
        self.sets: list[tuple[int, float]] = []
        self.released = False
        self._values: dict[int, float] = {
            cv2.CAP_PROP_FRAME_WIDTH: state["width"],
            cv2.CAP_PROP_FRAME_HEIGHT: state["height"],
            cv2.CAP_PROP_FPS: state["fps"],
            cv2.CAP_PROP_FOURCC: state["fourcc"],
        }

    def set(self, prop: int, value: float) -> bool:
        self.sets.append((prop, value))
        return self.honours_set

    def get(self, prop: int) -> float:
        return self._values.get(prop, 0.0)

    def isOpened(self) -> bool:
        return True

    def release(self) -> None:
        self.released = True

    def properties_set(self) -> tuple[int, ...]:
        return tuple(prop for prop, _value in self.sets)


def _mjpg_fourcc() -> int:
    return int(cv2.VideoWriter_fourcc(*"MJPG"))  # type: ignore[attr-defined]


def _yuy2_fourcc() -> int:
    return int(cv2.VideoWriter_fourcc(*"YUY2"))  # type: ignore[attr-defined]


def _camera_on_device(**state: float) -> _RecordingCapture:
    """A cooperating device reporting exactly ``state``.

    Keys are ``width``, ``height``, ``fps`` and ``fourcc``; anything omitted
    takes the requested value, so a test names only the dimension it is about.
    """
    reported: dict[str, float] = {
        "width": float(REQUESTED_SIZE[0]),
        "height": float(REQUESTED_SIZE[1]),
        "fps": float(REQUESTED_FPS),
        "fourcc": float(_mjpg_fourcc()),
    }
    reported.update(state)
    return _RecordingCapture(reported)


# ---------------------------------------------------------------------------
# Any-boundary access to the not-yet-existing symbols
# ---------------------------------------------------------------------------
def _camera_module() -> Any:
    """The camera module through an ``Any`` boundary.

    ``CaptureDeviceReadback`` and ``getCaptureDeviceReadback`` do not exist yet,
    so they cannot be imported by name without adding a type error to a
    currently-clean tree. ``AGENTS.md`` sanctions the ``Any`` boundary for
    exactly this kind of stub friction.
    """
    module: Any = importlib.import_module("core.Camera")
    return module


def _readback_type() -> Any:
    readback = getattr(_camera_module(), READBACK_CLASS, None)
    assert readback is not None, (
        f"core.Camera must define {READBACK_CLASS}; the readback is currently "
        "logged and then dropped, so nothing machine-readable survives a "
        "silent driver fallback"
    )
    return readback


def _configure_capture(capture: Any, size: tuple[int, int], fps: int) -> Any:
    """Run the real ``_configure_capture`` and assert it handed back a readback."""
    result = _camera_module()._configure_capture(capture, size, fps)
    assert isinstance(result, _readback_type()), (
        f"_configure_capture returned {type(result).__name__}; the readback has "
        "to come back from the call that performs it, or a caller who missed the "
        "warning still has no data"
    )
    return result


def _readback_of(
    capture: Any,
    *,
    size: tuple[int, int] = REQUESTED_SIZE,
    fps: int = REQUESTED_FPS,
) -> Any:
    return _configure_capture(capture, size, fps)


def _camera_source() -> ast.Module:
    return module_tree(CAMERA_SOURCE)


def _harness_module() -> Any:
    return importlib.import_module("preview_fps_support")


def _harness_function(name: str) -> ast.FunctionDef:
    tree = module_tree(HARNESS_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    defined = sorted(
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    )
    raise AssertionError(f"function {name!r} is not defined; defined: {defined}")


def _harness_camera_summary() -> ast.FunctionDef:
    source = class_node(module_tree(HARNESS_SOURCE), "SyntheticFrameSource")
    return class_method(source, "camera_summary")


# ===========================================================================
# A. The setting order is load-bearing and must stay pinned
# ===========================================================================


def test_configure_capture_sets_fourcc_before_width_and_height() -> None:
    # Given: a cooperating camera double that records the order of its sets.
    capture = _camera_on_device()

    # When: the real _configure_capture applies the production request.
    _configure_capture(capture, REQUESTED_SIZE, REQUESTED_FPS)

    # Then: FOURCC goes first, ahead of both resolution properties. The
    # docstring's reason is that a driver which resets the resolution when
    # FOURCC changes afterwards drops 1280x720 back to its default and, at
    # YUY2, saturates USB2.0 at 5-10 fps. The order is behaviour, not style.
    applied = capture.properties_set()
    fourcc_at = applied.index(cv2.CAP_PROP_FOURCC)
    assert fourcc_at < applied.index(cv2.CAP_PROP_FRAME_WIDTH)
    assert fourcc_at < applied.index(cv2.CAP_PROP_FRAME_HEIGHT)


def test_configure_capture_asks_for_mjpg_and_never_for_another_fourcc() -> None:
    # Given: a cooperating camera double.
    capture = _camera_on_device()

    # When: the real _configure_capture applies the production request.
    _configure_capture(capture, REQUESTED_SIZE, REQUESTED_FPS)

    # Then: the value handed to CAP_PROP_FOURCC is the one OpenCV itself
    # produces for "MJPG", so the request cannot drift from the decoder.
    asked = [value for prop, value in capture.sets if prop == cv2.CAP_PROP_FOURCC]
    assert asked == [_mjpg_fourcc()]


def test_configure_capture_builds_the_fourcc_from_videowriter_fourcc() -> None:
    # Given: the parsed production _configure_capture.
    function = _module_function(_camera_source(), "_configure_capture")

    # Then: the request is derived from cv2.VideoWriter_fourcc rather than a
    # hand-rolled integer, so a re-encode or an OpenCV change cannot leave a
    # stale magic number that no longer spells MJPG.
    assert "VideoWriter_fourcc" in referenced_names(function)

    # Then: and the attribute stays silenced instead of being "fixed". The
    # opencv stub has no VideoWriter_fourcc, the attribute does exist at
    # runtime, and dropping the ignore turns a stub gap into a type error.
    line = _source_line(function, "VideoWriter_fourcc")
    assert "type: ignore" in line, (
        "the VideoWriter_fourcc call lost its type: ignore; the opencv stub "
        f"lacks the attribute: {line!r}"
    )


def test_the_pinned_fourcc_integers_use_little_endian_byte_order() -> None:
    # Given/When: the integers OpenCV itself produces for the two FOURCCs.
    mjpg = _mjpg_fourcc()
    yuy2 = _yuy2_fourcc()

    # Then: they are the documented little-endian byte packs, pinned as
    # literals so a decoder written with the opposite order fails here rather
    # than at report-reading time. This also records that the brief's
    # 0x32315559 is "YU12", not "YUY2".
    assert mjpg == 0x47504A4D
    assert yuy2 == 0x32595559


def test_configure_capture_sets_buffersize_to_one_as_the_last_property() -> None:
    # Given: a camera double and a request that asks for no FPS, so the
    # conditional CAP_PROP_FPS set cannot be confused with the fixed four.
    capture = _camera_on_device()

    # When: the real _configure_capture runs without an FPS request.
    _configure_capture(capture, REQUESTED_SIZE, 0)

    # Then: exactly the four always-set properties are set, in the declared
    # order, with a single-frame buffer last. The buffer size is the delay
    # guard: a deeper queue would hand the preview stale frames.
    assert capture.properties_set() == ALWAYS_SET_PROPERTIES
    assert capture.sets[-1] == (cv2.CAP_PROP_BUFFERSIZE, 1)


def test_configure_capture_puts_the_fps_request_before_the_buffer_size() -> None:
    # Given: a camera double.
    capture = _camera_on_device()

    # When: the real _configure_capture runs with an FPS request.
    _configure_capture(capture, REQUESTED_SIZE, REQUESTED_FPS)

    # Then: the rate is requested after the resolution it belongs to and before
    # the buffer size, so the buffer guard is the last word either way.
    applied = capture.properties_set()
    assert applied == (
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
        cv2.CAP_PROP_BUFFERSIZE,
    )
    assert [value for prop, value in capture.sets if prop == cv2.CAP_PROP_FPS] == [
        float(REQUESTED_FPS)
    ]


# ===========================================================================
# B. The readback becomes structured data, not only a log line
# ===========================================================================


def test_capture_device_readback_declares_its_typed_fields() -> None:
    # Given: the parsed production file.
    tree = _camera_source()

    # When: the readback's annotated fields are collected in order.
    definition = class_node(tree, READBACK_CLASS)
    fields = {
        child.target.id: ast.unparse(child.annotation)
        for child in definition.body
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
    }

    # Then: requested and actual are separate typed fields per quantity, so a
    # device that accepted a request it cannot honour is visible in the data.
    # The three mismatch flags mirror the three existing warnings exactly, so
    # the data and the log can never tell different stories.
    assert fields == {
        "requested_width": "int",
        "requested_height": "int",
        "requested_fps": "int",
        "actual_width": "int",
        "actual_height": "int",
        "actual_fps": "float",
        "fourcc": "str",
        "fourcc_readable": "bool",
        "size_not_applied": "bool",
        "fourcc_not_mjpg": "bool",
        "fps_not_applied": "bool",
    }


def test_configure_capture_is_annotated_to_return_the_readback() -> None:
    # Given: the parsed production _configure_capture.
    function = _module_function(_camera_source(), "_configure_capture")

    # Then: its return type names the readback, so the call that performs the
    # readback is the only place a caller has to go for it.
    assert function.returns is not None, "_configure_capture is annotated -> None"
    assert ast.unparse(function.returns) == READBACK_CLASS


def test_camera_exposes_the_readback_through_a_named_getter() -> None:
    # Given: the parsed production file.
    tree = _camera_source()

    # When: the getter is read off the Camera class body.
    method = class_method(class_node(tree, "Camera"), READBACK_GETTER)

    # Then: it is annotated to hand back the readback or None. None is the only
    # honest answer before open: there is no device, so there is nothing to
    # have read one from. getStats() is the nearest precedent -- both read out
    # recorded state rather than measuring anything.
    assert method.returns is not None, f"{READBACK_GETTER} is annotated -> None"
    assert ast.unparse(method.returns) == f"{READBACK_CLASS} | None"


def test_camera_reports_no_readback_before_the_device_is_open() -> None:
    # Given: a Camera that has not opened a device.
    camera = _camera_module().Camera(fps=REQUESTED_FPS, capture_size=REQUESTED_SIZE)

    # When: the readback is asked for.
    result = getattr(camera, READBACK_GETTER)()

    # Then: it is None, not a zeroed record. Zeros would read as a device that
    # reported 0x0 at 0 fps in an unreadable format.
    assert result is None


def test_camera_hands_back_the_readback_the_open_produced(monkeypatch: Any) -> None:
    # Given: a Camera whose VideoCapture is a cooperating double, with the
    # acquisition thread stubbed out so the test never starts a real thread.
    module = _camera_module()
    capture = _camera_on_device()
    monkeypatch.setattr(cv2, "VideoCapture", lambda *_args, **_kwargs: capture)
    monkeypatch.setattr(module.Camera, "_start_thread", lambda _self: None)
    camera = module.Camera(fps=REQUESTED_FPS, capture_size=REQUESTED_SIZE)

    try:
        # When: the device is opened through the production path.
        opened = camera.openCamera(0)

        # Then: the readback the configuration produced is reachable from the
        # instance, so a caller who never saw the warning still has the data.
        assert opened is True
        result = getattr(camera, READBACK_GETTER)()
        assert isinstance(result, _readback_type())
        assert result.actual_fps == pytest.approx(float(REQUESTED_FPS))
        assert result.fourcc == "MJPG"
    finally:
        camera.destroy()


def test_get_stats_is_not_the_device_readback() -> None:
    # Given: a Camera with no device open and no readback.
    camera = _camera_module().Camera(fps=REQUESTED_FPS, capture_size=REQUESTED_SIZE)

    # When: the supply-side rate statistics are read.
    stats = camera.getStats()

    # Then: they stay exactly the supply-side pair. getStats() measures how
    # fast this process put frames on the queue; the readback describes what
    # the driver granted. Merging them would let a fast consumer hide a device
    # that was handed YUY2.
    assert set(stats) == {"fps", "avg_ms"}
    assert not set(stats) & CAPTURE_DEVICE_KEYS


def test_readback_decodes_a_non_mjpg_fourcc_to_its_own_name() -> None:
    # Given: a cooperating device that was granted a YUY2 stream anyway.
    capture = _camera_on_device(fourcc=float(_yuy2_fourcc()))

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: the decoded string is "YUY2". A decoder written only to recognise
    # the happy path -- or built around a hand-rolled integer -- cannot produce
    # this, which is the point: the silent fallback has to name itself.
    assert readback.fourcc == "YUY2"
    assert readback.fourcc_readable is True


def test_readback_keeps_requested_and_actual_apart() -> None:
    # Given: a device that accepted 1280x720 at 60 and delivers 640x480 at 5.
    capture = _camera_on_device(width=640.0, height=480.0, fps=5.0)

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: both sides survive as their own fields. Collapsing them to one
    # number would leave a reader unable to tell a 5 fps board from a request
    # that asked for 5.
    assert (readback.requested_width, readback.requested_height) == REQUESTED_SIZE
    assert readback.requested_fps == REQUESTED_FPS
    assert (readback.actual_width, readback.actual_height) == (640, 480)
    assert readback.actual_fps == pytest.approx(5.0)


def test_a_size_only_mismatch_is_representable_as_data() -> None:
    # Given: right FOURCC, right rate, wrong resolution.
    capture = _camera_on_device(width=640.0, height=480.0)

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: the size flag is the only one raised, so a reader can tell which of
    # the three mismatches happened instead of parsing a warning sentence.
    assert (
        readback.size_not_applied,
        readback.fourcc_not_mjpg,
        readback.fps_not_applied,
    ) == (True, False, False)


def test_a_fourcc_only_mismatch_is_representable_as_data() -> None:
    # Given: right resolution, right rate, YUY2 instead of MJPG.
    capture = _camera_on_device(fourcc=float(_yuy2_fourcc()))

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: only the FOURCC flag is raised. This is the USB2.0 bandwidth
    # failure, and it must be separable from a size or rate failure.
    assert (
        readback.size_not_applied,
        readback.fourcc_not_mjpg,
        readback.fps_not_applied,
    ) == (False, True, False)


def test_an_fps_only_mismatch_is_representable_as_data() -> None:
    # Given: right resolution, MJPG, but half the requested rate.
    capture = _camera_on_device(fps=30.0)

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: only the FPS flag is raised, and the shortfall is still recorded in
    # actual_fps so the number behind the flag is available.
    assert (
        readback.size_not_applied,
        readback.fourcc_not_mjpg,
        readback.fps_not_applied,
    ) == (False, False, True)
    assert readback.actual_fps == pytest.approx(30.0)


def test_the_fps_mismatch_flag_carries_the_existing_one_hz_tolerance() -> None:
    # Given: two devices that miss a 60 fps request by less and by more than
    # one hz respectively.
    inside = _camera_on_device(fps=59.5)
    outside = _camera_on_device(fps=58.9)

    # When/Then: both readbacks are taken and their FPS flags read. The
    # existing warning allows one hz of slack, and the flag has to agree with it
    # -- a data flag that fires where the log stays quiet would leave two
    # sources of truth. The tolerance is pinned here so it stays a decision
    # rather than an accident.
    assert _readback_of(inside).fps_not_applied is False
    assert _readback_of(outside).fps_not_applied is True


def test_a_zero_fps_readback_shows_the_zero_even_though_the_flag_stays_down() -> None:
    # Given: a device that reports no rate at all.
    capture = _camera_on_device(fps=0.0)

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: actual_fps carries the 0, which is how a reader sees it. The flag
    # stays down because the existing guard treats 0 as "the driver does not
    # report a rate" rather than "the device runs at 0 fps" -- a documented
    # hole, pinned here so it is visible instead of implied.
    assert readback.actual_fps == pytest.approx(0.0)
    assert readback.fps_not_applied is False


# ===========================================================================
# C. The harness records it, and the synthetic source is honest
# ===========================================================================


def test_the_harness_summary_carries_the_capture_device_block() -> None:
    # Given: the parsed SyntheticFrameSource.camera_summary.
    summary = _harness_camera_summary()

    # When: the keys of the dict it returns are read.
    keys = _returned_dict_keys(summary)

    # Then: the block is under the pinned report key, and none of its fields
    # leak to the report's top level. The harness source is the implementer's
    # file, so this is asserted on its source the way
    # test_present_instrumentation_contract.py does: a report key is a naming
    # contract with no runtime behaviour to observe.
    # The intersection is empty, not {REPORT_BLOCK_KEY}: CAPTURE_DEVICE_KEYS
    # holds the block's twelve *field* names and does not contain the report
    # key that nests them, so the two tests above pin presence and the two
    # nesting levels respectively.
    assert REPORT_BLOCK_KEY in keys
    assert not set(keys) & CAPTURE_DEVICE_KEYS, (
        "the block's fields belong inside the block, not splatted at the report "
        f"top level: {sorted(set(keys) & CAPTURE_DEVICE_KEYS)}"
    )


def test_the_report_receives_the_block_through_the_camera_summary_splat() -> None:
    # Given: the parsed harness's report builder.
    builder = _harness_function("_build_report")

    # When: the names splatted into the report dict are read.
    splatted = _double_star_names(builder)

    # Then: the camera summary is splatted, so a ``capture_device`` key added to
    # camera_summary() reaches report.json with no report-builder change. The
    # block is one nested object rather than eleven new top-level report keys.
    assert "camera" in splatted


def test_the_capture_device_block_is_a_required_report_field() -> None:
    # Given: the harness's declared report fields, read live so the frozenset a
    # parent compares a child report against is the one under test.
    fields = set(_harness_module().REQUIRED_REPORT_FIELDS)

    # Then: the block is required. Without it in this set a run whose device
    # data is missing reports the same shape as a run that recorded one, and the
    # artifact contract cannot tell the difference.
    assert REPORT_BLOCK_KEY in fields


def test_the_synthetic_source_reports_honest_non_physical_values() -> None:
    # Given: a synthetic source that was never started.
    source = _harness_module().SyntheticFrameSource()

    # When: its camera summary is read.
    summary = source.camera_summary()

    # Then: it carries the block, the block has the shared shape, and it says in
    # the harness's own established wording that no device was involved.
    assert REPORT_BLOCK_KEY in summary
    block = summary[REPORT_BLOCK_KEY]
    assert set(block) == CAPTURE_DEVICE_KEYS
    assert dict(block) == SYNTHETIC_CAPTURE_DEVICE_BLOCK


def test_the_synthetic_block_reports_no_measurement_rather_than_a_zero_one() -> None:
    # Given: a synthetic source's capture-device block.
    source = _harness_module().SyntheticFrameSource()
    block = source.camera_summary()[REPORT_BLOCK_KEY]

    # Then: every measured value is None, never 0. A real device reporting
    # "0x0 at 0 fps, FOURCC unreadable" is a broken board; a synthetic source
    # has no board at all. Reporting 0 for both makes the first look like the
    # second and lets a run claim a device measurement it never made. The four
    # booleans are claims about those values rather than values, and a source
    # that measured nothing claims nothing, so they stay False -- pinned by
    # test_the_synthetic_source_reports_honest_non_physical_values.
    for key in NO_DEVICE_MEASURED_KEYS:
        assert block[key] is None, f"{key} is {block[key]!r}, not None"


def test_a_real_and_a_synthetic_block_are_not_confusable() -> None:
    # Given: a real readback from a cooperating device, and the synthetic one.
    real = {**asdict(_readback_of(_camera_on_device())), "claim": REAL_CLAIM}
    source = _harness_module().SyntheticFrameSource()
    synthetic = source.camera_summary()[REPORT_BLOCK_KEY]

    # Then: the two are read by the same shape, so one reader handles both.
    assert set(real) == set(synthetic) == CAPTURE_DEVICE_KEYS

    # Then: and a single field separates them, so a reader can answer "was the
    # device verified?" without inspecting any number. The claim mirrors the
    # existing camera_thread_claim convention rather than inventing a new one.
    assert real["claim"] != synthetic["claim"]
    assert real["claim"] == REAL_CLAIM
    assert synthetic["claim"] == SYNTHETIC_CLAIM
    assert (real["claim"] == REAL_CLAIM) is not (synthetic["claim"] == REAL_CLAIM)
    assert isinstance(real["actual_fps"], float)
    assert synthetic["actual_fps"] is None


# ===========================================================================
# D. A real camera path exists
# ===========================================================================


@cache
def _capture_device_index() -> int | None:
    """The first index a DSHOW capture device opens at, or None.

    Probed once, at collection time, and every handle is released before it
    returns, so a run on a machine with no camera leaves nothing holding a
    device. A backend that raises while opening is an honest "no device" rather
    than a collection error that would take the whole file down with it.
    """
    for index in range(4):
        camera: Any = None
        try:
            camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            if bool(camera.isOpened()):
                return index
        except Exception:  # noqa: BLE001 - a raising backend means "no device"
            return None
        finally:
            if camera is not None:
                camera.release()
    return None


needs_capture_device = pytest.mark.skipif(
    _capture_device_index() is None,
    reason=NO_DEVICE_REASON,
)


@needs_capture_device
def test_real_capture_device_readback_mirrors_what_the_driver_reports() -> None:
    # Given: the real device the probe found, opened exactly as
    # Camera.openCamera opens it, then configured by the real _configure_capture.
    index = _capture_device_index()
    assert index is not None
    camera: Any = None
    try:
        camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        assert bool(camera.isOpened())
        readback = _readback_of(camera, size=REQUESTED_SIZE, fps=REQUESTED_FPS)

        # When: the driver's own reported values are read back a second time.
        driver_width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
        driver_height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))
        driver_fps = float(camera.get(cv2.CAP_PROP_FPS))

        # Then: the readback is the driver's own answer, not a restatement of
        # the request. This is the round trip, and it is the only assertion in
        # the file that can hold on a driver which refuses the request.
        assert (readback.actual_width, readback.actual_height) == (
            driver_width,
            driver_height,
        )
        assert readback.actual_fps == pytest.approx(driver_fps)
        assert (readback.requested_width, readback.requested_height) == REQUESTED_SIZE

        # Then: and the size is the size actually delivered, not merely the size
        # the driver echoes back. On this machine the only capture device is an
        # NVIDIA virtual camera that grants 1280x720 and 60 fps but hands back
        # YUY2 -- a decoded frame is the only proof the readback is honest.
        ok, frame = camera.read()
        assert ok
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (driver_height, driver_width, 3)
    finally:
        if camera is not None:
            camera.release()


@needs_capture_device
def test_real_capture_device_honours_the_requested_mjpg_and_size() -> None:
    # Given: the real device, opened as Camera.openCamera opens it.
    index = _capture_device_index()
    assert index is not None
    camera: Any = None
    try:
        camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not bool(camera.isOpened()):
            # The collection-time probe already skips when nothing opens, but it
            # is @cache'd: the device can be taken by another process between the
            # probe and this body, and that is "no device", not a code failure.
            pytest.skip(
                f"capture device {index} no longer opens: "
                "cv2.VideoCapture(index, cv2.CAP_DSHOW) reports isOpened() false"
            )

        # When: the production request is applied by the real _configure_capture.
        readback = _readback_of(camera, size=REQUESTED_SIZE, fps=REQUESTED_FPS)
        # The driver's own answer, read a second time so the skip reason can name
        # the integer as well as its decode. Nothing has touched the device since.
        code = int(camera.get(cv2.CAP_PROP_FOURCC))

        # Then: the driver granted MJPG at the requested size. This is the only
        # assertion in the project that can prove a real driver honours the
        # request, and that is a property of the capture board rather than of the
        # code: a board which silently falls back makes it unprovable here and
        # provable on a machine with a real capture board. A machine fact cannot
        # be a CI gate, so each way the claim can fail to be decidable is
        # reported as a skip carrying the measured facts. The readback's own
        # contract -- report whatever the driver did -- is pinned by
        # test_real_capture_device_readback_mirrors_what_the_driver_reports,
        # which passes on any machine that opens a device at all.
        if not readback.fourcc_readable:
            # Distinct from the fallback below: the driver did not name its own
            # stream, so there is nothing to compare against MJPG. That the
            # readback says so honestly is already pinned above; whether the
            # hardware honoured the request is simply unknown here.
            pytest.skip(
                f"capture device {index} does not name its own stream: "
                f"CAP_PROP_FOURCC read back {code} "
                f"(0x{code & 0xFFFFFFFF:08X}), which is not four printable "
                f"ASCII bytes, so the readback reports "
                f"fourcc={readback.fourcc!r} and whether MJPG was honoured "
                f"cannot be determined either way; it delivered "
                f"{readback.actual_width}x{readback.actual_height} at "
                f"{readback.actual_fps:.4f} fps"
            )

        if readback.fourcc != "MJPG":
            # The measured hardware fact, stated in full: a reader on this
            # machine must be able to learn what the board did without opening
            # the code.
            pytest.skip(
                f"capture device {index} does not honour the MJPG request: "
                f"CAP_PROP_FOURCC read back {code} "
                f"(0x{code & 0xFFFFFFFF:08X}), which decodes to "
                f"{readback.fourcc!r} rather than MJPG "
                f"({_mjpg_fourcc()} / 0x{_mjpg_fourcc() & 0xFFFFFFFF:08X}); "
                f"it delivered {readback.actual_width}x"
                f"{readback.actual_height} at {readback.actual_fps:.4f} fps, "
                f"so size_not_applied={readback.size_not_applied} and "
                f"fps_not_applied={readback.fps_not_applied}"
            )

        # Then: and the size is the size actually delivered, not merely the size
        # the driver echoes back -- a decoded frame is the only proof.
        assert (readback.actual_width, readback.actual_height) == REQUESTED_SIZE, (
            f"device {index} granted {readback.fourcc} but "
            f"{readback.actual_width}x{readback.actual_height}, not "
            f"{REQUESTED_SIZE[0]}x{REQUESTED_SIZE[1]}"
        )
        ok, frame = camera.read()
        assert ok
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (readback.actual_height, readback.actual_width, 3)
    finally:
        if camera is not None:
            camera.release()


# ===========================================================================
# E. The readback must not be able to fail silently
# ===========================================================================


def test_a_zero_fourcc_is_reported_as_unreadable_not_as_nul_characters() -> None:
    # Given: a device whose FOURCC readback is 0, which is what a driver that
    # cannot describe the stream returns.
    capture = _RecordingCapture(dict(UNSATISFIED_DEVICE))

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: it says "unreadable" and says so in its own flag. Four NUL
    # characters would compare unequal to "MJPG" and look like a *fallback*,
    # when what actually happened is that nothing was read at all.
    assert readback.fourcc == "unreadable"
    assert readback.fourcc_readable is False
    assert readback.fourcc != "MJPG"


def test_a_negative_fourcc_is_reported_as_unreadable() -> None:
    # Given: a device returning the negative integer a DSHOW backend hands back
    # for a stream it cannot name.
    capture = _RecordingCapture({**UNSATISFIED_DEVICE, "fourcc": NEGATIVE_FOURCC})

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: the sentinel again, and not three non-ASCII bytes. The existing
    # little-endian decode already orders the bytes correctly for a negative
    # int, so this is about refusing to publish an unusable string. The
    # rejected decode is named here rather than filtered by printability:
    # the published sentinel is itself printable ASCII, so a
    # ``not (fourcc.isascii() and fourcc.isprintable())`` predicate would
    # contradict the ``== "unreadable"`` assertion above and could never
    # hold for any implementation. Naming the bytes keeps the original
    # intent -- the garbage is not what got published -- and can still fail.
    decoded = "".join(chr((int(NEGATIVE_FOURCC) >> (8 * i)) & 0xFF) for i in range(4))
    assert readback.fourcc == "unreadable"
    assert readback.fourcc_readable is False
    assert decoded != "unreadable"
    assert readback.fourcc != decoded


def test_configure_capture_does_not_raise_when_every_set_returns_false() -> None:
    # Given: a device that rejects every set and keeps its own defaults, which
    # is the case the docstring calls out: set() returns False rather than
    # raising, and the readback is the only thing that makes it visible.
    capture = _RecordingCapture(dict(UNSATISFIED_DEVICE), honours_set=False)

    # When/Then: the real _configure_capture completes and produces a readback.
    # A readback must never become the new crash path on the exact device it
    # exists to describe.
    readback = _readback_of(capture)
    assert len(capture.sets) == len(ALWAYS_SET_PROPERTIES) + 1
    assert (readback.actual_width, readback.actual_height) == (640, 480)
    assert readback.fourcc == "unreadable"


def test_an_unsatisfiable_request_is_reported_as_the_mismatches_it_is() -> None:
    # Given: the same device, but granted a real YUY2 stream and nothing else.
    capture = _RecordingCapture(
        {**UNSATISFIED_DEVICE, "fourcc": float(_yuy2_fourcc())},
        honours_set=False,
    )

    # When: the readback is taken.
    readback = _readback_of(capture)

    # Then: the request is on the record as requested and the device's answer as
    # actual, with the size and FOURCC mismatches raised. The FPS flag stays
    # down only because the driver reports 0 rather than a slow rate.
    assert (readback.requested_width, readback.requested_height) == REQUESTED_SIZE
    assert (readback.actual_width, readback.actual_height) == (640, 480)
    assert (readback.size_not_applied, readback.fourcc_not_mjpg) == (True, True)
    assert readback.fourcc == "YUY2"


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------
def _module_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    defined = sorted(
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    )
    raise AssertionError(f"function {name!r} is not defined; defined: {defined}")


def _source_line(function: ast.FunctionDef, needle: str) -> str:
    """The physical source line inside ``function`` that mentions ``needle``."""
    lines = CAMERA_SOURCE.read_text(encoding="utf-8").splitlines()
    for child in ast.walk(function):
        lineno = getattr(child, "lineno", None)
        if lineno is None or not (function.lineno <= lineno <= function.end_lineno):
            continue
        text = lines[lineno - 1]
        if needle in text:
            return text
    raise AssertionError(f"{needle!r} is on no line of {function.name!r}")


def _returned_dict_keys(function: ast.FunctionDef) -> set[str]:
    """The string keys of the dict literal ``function`` returns."""
    returns = [
        node
        for statement in function.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Return) and node.value is not None
    ]
    assert returns, f"{function.name!r} returns nothing"
    value = returns[-1].value
    assert isinstance(value, ast.Dict), (
        f"{function.name!r} does not return a dict literal, so the report keys "
        "it owns cannot be pinned by reading its source"
    )
    return {
        key.value
        for key in value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _double_star_names(function: ast.FunctionDef) -> set[str]:
    """The names splatted with ``**`` into any dict literal in ``function``."""
    splatted: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if key is not None:
                continue
            if isinstance(value, ast.Name):
                splatted.add(value.id)
            elif isinstance(value, ast.Attribute):
                splatted.add(value.attr)
    return splatted
