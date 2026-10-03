"""RED contracts for the capture/display coordinate transform layer; # noqa: SIZE_OK -
approved brief requires one module per contract group, and the shared double
vocabulary this file needs lives in ``tests/gdi_present_doubles.py``, which the
brief forbids touching.

The layer is ``SerialController/core/coordinates.py`` -- tkinter-free and
numpy-free, so it satisfies ``task bounds``. It exists to close one latent bug:
``CaptureArea._captureRatio`` (removed) scaled by ``camera.capture_size``, the
*requested* size, while the three consumers clamped by the *delivered* frame's
``shape``. They agree only because no camera-side resize exists yet.
``mouseCtrlLeftPress`` is the sharpest instance: the probe stays in bounds and
silently reports the wrong pixel, with no exception. The same derivation also
turned a zero ``capture_size`` into ratio 0.0, so every click reported the
top-left pixel -- also in bounds, also silent.

This release implements and proves exactly one path: ``capture == display ==
(1280, 720)``. Every transform must be the identity there, the round trip must
be lossless, and the four consumers must produce byte-identical results to the
ratio arithmetic they use today. What the layer does at a *non-integral* scale
is pinned too, so the rounding rule is explicit rather than absorbed silently
by the callers' own ``int()`` / ``round()``.

Three seams are used, matching the existing present-path contracts:

* **Static** (``ast``) for the class shape -- the ``_mapper`` entry point and the
  removal of ``_captureRatio`` / ``_showRatio`` / ``_ratio``. A real
  ``tk.Frame`` cannot be built without a display.
* **Instance-level** against the recording doubles for the four consumers, driven
  through the real methods so a behaviour change is caught rather than a
  re-implementation.
* **Loguru sink** for the two consumers whose capture coordinate exists *only* in
  a log line. The assertion is on the extracted integers, never on the wording,
  so rewording the sentence does not break the contract while a changed
  coordinate does.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest
from gdi_present_doubles import (
    PointerEvent,
    bare_capture_area,
)
from gdi_source_readers import (
    GUI_ASSETS_SOURCE,
    class_method,
    class_node,
    module_tree,
    referenced_names,
)

#: The fixed operating assumption this release proves. ``capture_size`` is
#: ``(width, height)``; a delivered frame of this size is ``(720, 1280, 3)``.
CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720
CAPTURE_SIZE = (CAPTURE_WIDTH, CAPTURE_HEIGHT)

#: Coordinates pinned at the identity, including both ends and a negative. The
#: negative matters: every consumer clamps at 0 immediately after scaling, so a
#: truncation rule that differs from floor is only observable off the clamp.
IDENTITY_COORDINATES: list[tuple[int, int]] = [
    (0, 0),
    (1, 1),
    (CAPTURE_WIDTH - 1, CAPTURE_HEIGHT - 1),
    (CAPTURE_WIDTH // 2, CAPTURE_HEIGHT // 2),
    (-5, -7),
]

#: Every integer appearing in a captured log line, in order. The assertions below
#: compare tuples of these, so a reworded message keeps passing while a changed
#: coordinate fails.
_INT_TOKEN = re.compile(r"-?\d+")

#: The offset a letterboxed preview actually has: the image sits 100 px right of
#: and 50 px below the display area's corner, which is what ``fit_rect`` returns
#: for a viewport wider than 16:9. Two distinct non-zero values on purpose, so an
#: axis swap in the translation cannot pass.
DISPLAY_ORIGIN: tuple[int, int] = (100, 50)

#: ``(content, viewport, expected)``. ``expected`` is ``(x, y, width, height)``.
_FitRectCase = tuple[tuple[int, int], tuple[int, int], tuple[int, int, int, int]]

#: The four placements the brief fixes by hand, in (width, height) throughout.
#: ``narrow`` is limited by width, ``tall`` and ``exact`` land on the same rule
#: with different margins, and ``wide`` is the case the width branch cannot
#: handle: the viewport is wide enough that height, not width, is the binding
#: constraint, so swapping the branches shows up as a stretched image.
FIT_RECT_CASES: list[_FitRectCase] = [
    ((1280, 720), (1000, 700), (0, 68, 1000, 563)),
    ((1280, 720), (1280, 720), (0, 0, 1280, 720)),
    ((1280, 720), (1920, 1200), (0, 60, 1920, 1080)),
    ((1280, 720), (2000, 720), (360, 0, 1280, 720)),
]


# ---------------------------------------------------------------------------
# Test-only doubles. Kept here rather than in gdi_present_doubles.py because the
# brief forbids touching existing test files.
# ---------------------------------------------------------------------------
class _RecordingFrame:
    """A real BGR ndarray that also remembers which 1x1 window was sliced out.

    ``mouseCtrlLeftPress`` reports its result only through a log line, so the
    honest way to pin *which pixel it probed* is to observe the slice it handed
    to ``cv2``. The slice key is the observable; the colour log is the echo.
    """

    def __init__(self, height: int, width: int) -> None:
        self.array = np.zeros((height, width, 3), np.uint8)
        self.probes: list[tuple[tuple[int, int], tuple[int, int]]] = []

    @property
    def shape(self) -> tuple[int, ...]:
        return self.array.shape

    def __getitem__(self, key: Any) -> Any:
        rows, columns = key
        self.probes.append(((rows.start, rows.stop), (columns.start, columns.stop)))
        return self.array[key]

    def copy(self) -> Any:
        """A detached copy. The range path stores it and never indexes it."""
        return self.array.copy()


@dataclass
class _RecordingCamera:
    """The camera surface the four coordinate consumers read.

    ``StubCamera`` in ``gdi_present_doubles.py`` is a slots dataclass, so an
    instance attribute cannot be grafted onto it to record ``saveCapture``. This
    double covers exactly the two methods those four consumers call.
    """

    capture_size: tuple[int, int] = CAPTURE_SIZE
    frame: Any = None
    saves: list[dict[str, Any]] = field(default_factory=list)
    #: What ``saveCapture`` answers. ``None`` is the default rather than ``True``
    #: so a double left alone reports the failure a save path that never returns
    #: a value would produce, and the success contract has to ask for it.
    save_result: bool | None = None

    def readFrame(self, copy: bool = False) -> Any:
        frame = self.frame
        if frame is None:
            return None
        return frame.copy() if copy else frame

    def saveCapture(self, *args: Any, **kwargs: Any) -> bool | None:
        self.saves.append({"args": args, "kwargs": dict(kwargs)})
        return self.save_result


@contextmanager
def _captured_log() -> Iterator[list[str]]:
    """Collect INFO-and-above loguru records, then detach the sink.

    ``record["message"]`` rather than ``str(message)``: the string form carries
    the timestamp and the ``module:function:line`` prefix, whose digits would be
    indistinguishable from the coordinates being asserted on.
    """
    from loguru import logger

    messages: list[str] = []
    handler = logger.add(
        lambda message: messages.append(str(message.record["message"])), level="INFO"
    )
    try:
        yield messages
    finally:
        logger.remove(handler)


def _logged_integers(messages: list[str]) -> list[int]:
    """Every integer in ``messages``, in order. The wording is not the contract."""
    return [int(token) for message in messages for token in _INT_TOKEN.findall(message)]


def _printed_integers_in_order(text: str, expected: list[int]) -> bool:
    """True when ``expected`` appears in ``text`` as an ordered subsequence.

    Subsequence, not equality, on purpose: a success line also carries the save
    directory, whose digits are not coordinates and may change. What must not
    change is the identity and the order of the crop values, so ``(400, 300)``
    first is a failure even though every one of the four numbers is present.
    """
    remaining = list(expected)
    for token in _INT_TOKEN.findall(text):
        if remaining and int(token) == remaining[0]:
            remaining.pop(0)
    return not remaining


def _mapper_type() -> Any:
    """``CoordinateMapper``, imported at call time so absence is a per-test RED."""
    from core.coordinates import CoordinateMapper

    return CoordinateMapper


def _today_ratio(numer: tuple[int, int], denom: tuple[int, int]) -> tuple[float, float]:
    """``CaptureArea._ratio`` as it stood before the port, verbatim.

    The reference the new layer is compared against. It is a different shape of
    code from the layer -- a float ratio combined with an ``int()`` at each call
    site -- so the comparison is a real cross-check rather than a projection of
    the output onto itself.
    """

    def one(a: float, b: float) -> float:
        return float(a) / float(b) if b else 1.0

    return one(numer[0], denom[0]), one(numer[1], denom[1])


def _identity_mapper() -> Any:
    return _mapper_type()(
        capture_size=CAPTURE_SIZE,
        display_size=CAPTURE_SIZE,
    )


def _offset_mapper(display_origin: tuple[int, int] = DISPLAY_ORIGIN) -> Any:
    """A mapper at the fixed operating assumption whose image is offset.

    The size pair is left at the identity on purpose: with a scale of 1.0 the
    only difference from ``_identity_mapper`` is the translation, so a failure
    here is attributable to the origin rather than to the rounding rule.
    """
    return _mapper_type()(
        capture_size=CAPTURE_SIZE,
        display_size=CAPTURE_SIZE,
        display_origin=display_origin,
    )


def _fit_rect() -> Any:
    """``fit_rect``, imported at call time so its absence is a per-test RED.

    Same seam as ``_mapper_type``: a module-level import would turn every
    placement test red for the same reason and hide which one actually broke.
    """
    from core.coordinates import fit_rect

    return fit_rect


def _area_at_fixed_size(frame: Any, requested_size: tuple[int, int]) -> Any:
    """A bare ``CaptureArea`` at 1280x720 whose camera reports ``requested_size``.

    ``frame`` is the frame the camera actually delivers. ``requested_size`` is
    what ``camera.capture_size`` claims. The two are equal for every regression
    guard and deliberately unequal for the bug this layer exists to close.
    """
    bare = bare_capture_area(width=CAPTURE_WIDTH, height=CAPTURE_HEIGHT)
    bare.area.camera = _RecordingCamera(capture_size=requested_size, frame=frame)
    return bare


def _range_area(save_result: bool | None) -> Any:
    """A bare ``CaptureArea`` at the fixed assumption whose ``saveCapture`` answers
    ``save_result``.

    The return value is the only thing ``ReleaseRangeSS`` can branch on, so the
    success and failure contracts are the same drag with a different answer from
    the camera. ``None`` is included because a save path that returns nothing is
    indistinguishable from a failure to the caller that has to branch on it.
    """
    area = _area_at_fixed_size(
        np.zeros((CAPTURE_HEIGHT, CAPTURE_WIDTH, 3), np.uint8), CAPTURE_SIZE
    ).area
    area.camera.save_result = save_result
    return area


def _capture_area_node() -> ast.ClassDef:
    return class_node(module_tree(GUI_ASSETS_SOURCE), "CaptureArea")


def _class_method_names(node: ast.ClassDef) -> set[str]:
    return {child.name for child in node.body if isinstance(child, ast.FunctionDef)}


def _self_camera_attributes(node: ast.AST) -> set[str]:
    """Every ``self.camera.<attr>`` read reachable from ``node``.

    Scoped to the ``self.camera`` chain on purpose. A bare ``referenced_names``
    match on ``capture_size`` would also reject ``mapper.capture_size``, which is
    the *correct* post-fix spelling, so it cannot tell the two apart.
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        owner = child.value
        if (
            isinstance(owner, ast.Attribute)
            and owner.attr == "camera"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "self"
        ):
            found.add(child.attr)
    return found


def _gui_capture_area() -> Any:
    """The real ``CaptureArea`` class, reached through the ``Any`` boundary."""
    import GuiAssets as module

    return module.CaptureArea


# ===========================================================================
# 1. The module and the class shape
# ===========================================================================


def test_coordinate_mapper_is_a_frozen_slotted_dataclass() -> None:
    # Given: the transform layer's class.
    mapper_type = _mapper_type()
    mapper = _identity_mapper()

    # Then: it is a dataclass, because the layer is two sizes and two pure
    # transforms -- there is no behaviour that needs a mutable object.
    assert dataclasses.is_dataclass(mapper_type)

    # Then: and it is frozen, so a mapper cannot be re-pointed at another frame
    # after a consumer has taken its coordinates from it.
    with pytest.raises(dataclasses.FrozenInstanceError):
        mapper.capture_size = (64, 64)

    # Then: and it is slotted, so a typo like ``mapper.capture_sizes`` fails at
    # the attribute lookup rather than silently creating a third size.
    assert not hasattr(mapper, "__dict__")


def test_coordinate_mapper_carries_capture_display_and_origin_in_that_order() -> None:
    # Given: the transform layer's class.
    mapper_type = _mapper_type()

    # Then: the three fields are the only ones, in this order -- the two sizes
    # and the offset the image sits at inside the display area. A fourth field
    # would be a fourth source of truth for the scale, which is the bug.
    assert [item.name for item in dataclasses.fields(mapper_type)] == [
        "capture_size",
        "display_size",
        "display_origin",
    ]

    # Then: and the keyword form is the supported constructor, so all three
    # names are pinned as the public API rather than as a positional convention.
    assert _identity_mapper() == mapper_type(
        capture_size=(1280, 720), display_size=(1280, 720)
    )

    # Then: and ``display_origin`` defaults to the corner, so every construction
    # that omits it keeps the two-size behaviour it had before the origin
    # existed. A default of anything else would silently move every caller.
    assert mapper_type(
        capture_size=(1280, 720), display_size=(1280, 720)
    ).display_origin == (0, 0)

    # Then: the types are left to mypy on the production side; a test that read
    # ``field.type`` would be asserting a string, not a contract.


def test_to_capture_takes_two_ints_and_an_opt_in_keyword_clamp() -> None:
    # Given: the transform layer's class.
    mapper_type = _mapper_type()

    # When: the two transforms are inspected.
    to_capture = inspect.signature(mapper_type.to_capture)
    to_display = inspect.signature(mapper_type.to_display)

    # Then: ``to_capture`` takes self, x, y and a keyword-only ``clamp`` that
    # defaults to off. The clamp is keyword-only because clamping is a decision
    # a consumer makes about the point it is about to index, not a default that
    # should ever be applied by accident.
    assert tuple(to_capture.parameters) == ("self", "x", "y", "clamp")
    assert to_capture.parameters["clamp"].kind is inspect.Parameter.KEYWORD_ONLY
    assert to_capture.parameters["clamp"].default is False

    # Then: and ``to_display`` takes only the point. Nothing indexes a buffer on
    # the display side -- ``ImgRect`` draws, it does not sample -- so a display
    # clamp would be a bound nothing needs (YAGNI).
    assert tuple(to_display.parameters) == ("self", "x", "y")


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_to_capture_returns_ints_and_never_a_float_at_identity(
    coordinate: tuple[int, int],
) -> None:
    # Given: a mapper whose capture size equals its display size.
    mapper = _identity_mapper()
    x, y = coordinate

    # When: a display point is mapped into capture space.
    mapped = mapper.to_capture(x, y)

    # Then: both components are plain ints, so every consumer's own ``int()``
    # becomes a no-op and the rounding rule lives in exactly one place.
    assert all(type(value) is int for value in mapped), mapped


# ===========================================================================
# 2. Identity at capture == display
# ===========================================================================


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_to_capture_is_the_identity_when_capture_equals_display(
    coordinate: tuple[int, int],
) -> None:
    # Given: a mapper at the fixed operating assumption, capture == display.
    mapper = _identity_mapper()
    x, y = coordinate

    # When: a display point is mapped into capture space.
    mapped = mapper.to_capture(x, y)

    # Then: it comes back untouched -- including 0, 1, the maximum, and a
    # negative, because the low clamp lives in the caller and would otherwise
    # hide a broken identity path.
    assert mapped == (x, y)


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_to_display_is_the_identity_when_capture_equals_display(
    coordinate: tuple[int, int],
) -> None:
    # Given: a mapper at the fixed operating assumption, capture == display.
    mapper = _identity_mapper()
    x, y = coordinate

    # When: a capture point is mapped into display space.
    mapped = mapper.to_display(x, y)

    # Then: it comes back untouched, so ``ImgRect``'s recognition box lands on
    # the same pixel it was detected on.
    assert mapped == (x, y)


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_display_then_capture_round_trips_losslessly_at_identity(
    coordinate: tuple[int, int],
) -> None:
    # Given: a mapper at the fixed operating assumption.
    mapper = _identity_mapper()
    x, y = coordinate

    # When: a point goes display -> capture -> display.
    there = mapper.to_capture(x, y)
    back = mapper.to_display(*there)

    # Then: the round trip is lossless, so a detection box and the click that
    # produced it refer to the same pixel.
    assert back == (x, y)


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_capture_then_display_round_trips_losslessly_at_identity(
    coordinate: tuple[int, int],
) -> None:
    # Given: a mapper at the fixed operating assumption.
    mapper = _identity_mapper()
    x, y = coordinate

    # When: a point goes capture -> display -> capture.
    there = mapper.to_display(x, y)
    back = mapper.to_capture(*there)

    # Then: the round trip is lossless in the other direction too, which is the
    # one ``ImgRect`` exercises on every recognition result.
    assert back == (x, y)


# ===========================================================================
# 3. The rounding rule
# ===========================================================================


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        (3, 1),  # 3 * 0.5 = 1.5 -> 1. round() would give 2.
        (7, 3),  # 7 * 0.5 = 3.5 -> 3. round() would give 4 (banker's rounds to even).
    ],
)
def test_to_capture_truncates_toward_zero_rather_than_rounding_to_nearest(
    display: int, expected: int
) -> None:
    # Given: a mapper whose capture width is half its display width, so the
    # scale is exactly 0.5 and every odd coordinate lands on a .5 boundary.
    mapper = _mapper_type()(capture_size=(640, 720), display_size=(1280, 720))

    # When: an odd display coordinate is mapped into capture space.
    mapped = mapper.to_capture(display, 0)

    # Then: it truncates toward zero. Three of the four consumers call ``int()``
    # on a float ratio today, so truncation is the rule that keeps them
    # byte-identical once a scale other than 1.0 exists.
    assert mapped == (expected, 0)


def test_to_capture_truncates_toward_zero_rather_than_flooring_a_negative() -> None:
    # Given: a mapper whose capture width is half its display width.
    mapper = _mapper_type()(capture_size=(640, 720), display_size=(1280, 720))

    # When: a negative display coordinate is mapped, unclamped.
    mapped = mapper.to_capture(-7, 0)

    # Then: it truncates toward zero, giving -3, not floor's -4. Truncation is
    # the rule because every consumer clamps at 0 on the very next line, so the
    # only axis on which int() and floor() could ever differ is one that is
    # immediately discarded -- and pinning it keeps the rule unambiguous.
    assert mapped == (-3, 0)


def test_to_display_truncates_toward_zero_on_a_non_integral_scale() -> None:
    # Given: a mapper whose display is half as wide as its capture, so mapping
    # capture -> display scales by exactly 0.5 and an odd coordinate lands on a
    # .5 boundary. The direction matters: capture -> display must divide by the
    # capture size, and 1.0 at the fixed assumption cannot tell a division the
    # right way round from the wrong one.
    mapper = _mapper_type()(capture_size=(1280, 720), display_size=(640, 360))

    # When: an odd capture coordinate is mapped into display space.
    mapped = mapper.to_display(7, 0)

    # Then: it truncates toward zero, the same single rule as ``to_capture``,
    # rather than rounding 3.5 to 4.
    assert mapped == (3, 0)


# ===========================================================================
# 4. Degenerate sizes
# ===========================================================================


def test_to_capture_does_not_divide_by_zero_when_the_display_size_is_zero() -> None:
    # Given: a mapper whose display size is zero, which is what an unconnected
    # camera's ``capture_size`` of 0 used to produce.
    mapper = _mapper_type()(capture_size=(1280, 720), display_size=(0, 0))

    # When: a display point is mapped into capture space.
    mapped = mapper.to_capture(640, 360)

    # Then: no ZeroDivisionError, and the point comes back at ratio 1 so the
    # click that triggered it still resolves instead of tearing down the UI.
    assert mapped == (640, 360)


def test_to_display_does_not_divide_by_zero_when_the_capture_size_is_zero() -> None:
    # Given: a mapper whose capture size is zero.
    mapper = _mapper_type()(capture_size=(0, 0), display_size=(1280, 720))

    # When: a capture point is mapped into display space.
    mapped = mapper.to_display(640, 360)

    # Then: no ZeroDivisionError, and the point comes back at ratio 1.
    assert mapped == (640, 360)


def test_clamped_to_capture_is_never_negative_for_a_degenerate_capture_size() -> None:
    # Given: a mapper with no capture extent, so ``capture_size - 1`` is -1 and
    # a naive upper clamp would return -1 and wrap the slice to the far end of
    # the buffer.
    mapper = _mapper_type()(capture_size=(0, 0), display_size=(1280, 720))

    # When: a negative point is mapped with the clamp enabled.
    mapped = mapper.to_capture(-5, -7, clamp=True)

    # Then: it is floored at 0, never negative, so no consumer can build a slice
    # that silently reads the wrong end of a frame.
    assert mapped == (0, 0)


def test_clamped_to_capture_applies_the_upper_bound_from_the_capture_size() -> None:
    # Given: a mapper at the fixed operating assumption.
    mapper = _identity_mapper()

    # When: a point past the last column and row is mapped with the clamp.
    mapped = mapper.to_capture(CAPTURE_WIDTH + 100, CAPTURE_HEIGHT + 100, clamp=True)

    # Then: it is held at the last addressable pixel, which is the arithmetic
    # ``coordinates.CoordinateMapper.to_capture`` performs identically:
    # min(max(value, 0), size - 1).
    assert mapped == (CAPTURE_WIDTH - 1, CAPTURE_HEIGHT - 1)


# ===========================================================================
# 5. The mapper is built from the delivered frame
# ===========================================================================


def test_capture_area_defines_the_named_mapper_entry_point() -> None:
    # Given: the parsed CaptureArea class body.
    node = _capture_area_node()
    defined = _class_method_names(node)

    # Then: the layer is reached through one named method. The old derivation was
    # two private methods plus a static helper, so "reach it by accident" was
    # easy; one method with one optional argument is not.
    assert "_mapper" in defined, (
        f"CaptureArea defines no _mapper; defined: {sorted(defined)}"
    )

    # Then: and the argument set is pinned, because the whole fix hangs on it --
    # the frame that is about to be indexed, or nothing at all.
    parameters = inspect.signature(_gui_capture_area()._mapper).parameters
    assert tuple(parameters) == ("self", "frame")
    assert parameters["frame"].default is None


@pytest.mark.parametrize(
    "method_name", ["StartRangeSS", "ReleaseRangeSS", "mouseCtrlLeftPress", "ImgRect"]
)
def test_every_coordinate_consumer_reaches_the_layer_through_the_named_entry_point(
    method_name: str,
) -> None:
    # Given: one of the four consumers of the capture/display mapping.
    consumer = class_method(_capture_area_node(), method_name)

    # Then: it reaches the layer through ``_mapper``, so there is exactly one
    # place where the two sizes are turned into a scale.
    assert "_mapper" in referenced_names(consumer), (
        f"{method_name} does not reach the layer through CaptureArea._mapper"
    )

    # Then: and it does not construct a mapper itself, which would be a second
    # place to get the derivation wrong.
    assert "CoordinateMapper" not in referenced_names(consumer), (
        f"{method_name} builds a CoordinateMapper inline instead of calling _mapper"
    )


def test_pixel_probe_follows_the_delivered_frame_and_not_the_requested_capture_size() -> (
    None
):
    # Given: a camera whose DELIVERED frame is 640x720 while its REQUESTED
    # capture size still claims 1280x720 -- the state a camera-side resize
    # leaves behind. A click at display x=1000 is column 500 of the frame that
    # actually arrived.
    frame = _RecordingFrame(CAPTURE_HEIGHT, 640)
    area = _area_at_fixed_size(frame, CAPTURE_SIZE).area

    # When: the pixel is probed at that display position.
    with _captured_log() as messages:
        getattr(area, "mouseCtrlLeftPress")(PointerEvent(x=1000, y=360))

    # Then: the 1x1 window sliced out of the delivered frame is the one holding
    # column 500, not the one column 639 that clamping a ratio of 1.0 would
    # reach. The probe stays in bounds either way, so only the slice can tell
    # the two apart -- and a wrong pixel is the bug.
    assert frame.probes == [((360, 361), (500, 501))], frame.probes

    # Then: and the coordinate it reports is the same one it probed.
    assert len(messages) == 2, messages
    assert _logged_integers(messages[:1]) == [1000, 360, 500, 360]


@pytest.mark.parametrize(
    "requested_size",
    [CAPTURE_SIZE, (2560, 1440), (640, 360), (0, 0)],
    ids=["equal", "larger", "smaller", "degenerate"],
)
def test_pixel_probe_does_not_depend_on_the_requested_capture_size(
    requested_size: tuple[int, int],
) -> None:
    # Given: the same delivered 640x720 frame behind four different claims from
    # ``camera.capture_size``. Only the last one is today's degenerate case; the
    # middle two are what a resize negotiation leaves behind.
    frame = _RecordingFrame(CAPTURE_HEIGHT, 640)
    area = _area_at_fixed_size(frame, requested_size).area

    # When: the pixel is probed.
    getattr(area, "mouseCtrlLeftPress")(PointerEvent(x=1000, y=360))

    # Then: the probe is identical in every case, because the scale and the
    # clamp read the one source the consumer indexes -- the delivered frame.
    # This is the invariant that makes a disagreement between them structurally
    # impossible rather than merely unlikely.
    assert frame.probes == [((360, 361), (500, 501))], (
        f"requested size {requested_size} moved the probe: {frame.probes}"
    )


def test_clamped_pixel_probe_and_its_scale_read_one_size() -> None:
    # Given: a delivered frame and a mapper built from it, and the arithmetic
    # ``mouseCtrlLeftPress`` performs once the two are unified.
    frame = _RecordingFrame(CAPTURE_HEIGHT, 640)
    mapper = _mapper_type()(
        capture_size=(frame.shape[1], frame.shape[0]),
        display_size=CAPTURE_SIZE,
    )

    # When: a display point is mapped and clamped through the layer.
    mapped = mapper.to_capture(1000, 360, clamp=True)

    # Then: the value is a pure function of ``capture_size`` -- the same number
    # the clamp bounds it by -- so a scale and a clamp built from two different
    # sizes cannot both be right.
    assert mapped == (500, 360)
    assert mapped[0] == min(max(int(1000 * 640 / 1280), 0), frame.shape[1] - 1)
    assert mapped[1] == min(max(int(360 * 720 / 720), 0), frame.shape[0] - 1)


# ===========================================================================
# 6. Parity with the arithmetic the four consumers use today
# ===========================================================================


@pytest.mark.parametrize(
    ("capture_size", "display_size"),
    [((1280, 720), (1280, 720)), ((640, 720), (1280, 720))],
    ids=["identity", "half_width"],
)
def test_to_capture_matches_todays_capture_ratio_arithmetic(
    capture_size: tuple[int, int], display_size: tuple[int, int]
) -> None:
    # Given: today's ``_ratio(capture_size, show_size)`` and a mapper over the
    # same two sizes. The identity case alone cannot detect an inverted ratio --
    # 1.0 is 1.0 either way -- so a non-integral scale is included, which is the
    # only place the direction of the division is observable at all.
    ratio_x, ratio_y = _today_ratio(capture_size, display_size)
    mapper = _mapper_type()(capture_size=capture_size, display_size=display_size)

    # When: each pinned display coordinate is mapped through both.
    for display_x, display_y in [(0, 0), (1, 1), (7, 3), (1000, 360), (-5, -7)]:
        # Then: the layer agrees with ``int(display * ratio)``, which is what
        # ``StartRangeSS``, ``ReleaseRangeSS`` and ``mouseCtrlLeftPress`` do.
        assert mapper.to_capture(display_x, display_y) == (
            int(display_x * ratio_x),
            int(display_y * ratio_y),
        ), (display_x, display_y)


def test_to_display_matches_todays_show_ratio_arithmetic_at_identity() -> None:
    # Given: today's ``_ratio(show_size, capture_size)`` and a mapper at the
    # fixed operating assumption.
    ratio_x, ratio_y = _today_ratio(CAPTURE_SIZE, CAPTURE_SIZE)
    mapper = _identity_mapper()

    # When: each pinned capture coordinate is mapped through both.
    for capture_x, capture_y in [(0, 0), (100, 50), (200, 150), (1279, 719)]:
        # Then: the layer agrees. Only the identity case is compared, because
        # ``ImgRect`` rounds rather than truncating and the two rules coincide
        # only at 1.0 -- a non-integral comparison would be pinning a decision
        # this task explicitly leaves open.
        assert mapper.to_display(capture_x, capture_y) == (
            int(round(capture_x * ratio_x)),
            int(round(capture_y * ratio_y)),
        ), (capture_x, capture_y)


# ===========================================================================
# 7. Regression guards: the four consumers at capture == display
# ===========================================================================


def test_release_range_ssa_crop_box_for_a_forward_drag() -> None:
    # Given: a range selection dragged from (100, 50) to (400, 300) at the fixed
    # operating assumption, so the ratio is exactly 1.0.
    area = _area_at_fixed_size(
        np.zeros((CAPTURE_HEIGHT, CAPTURE_WIDTH, 3), np.uint8), CAPTURE_SIZE
    ).area
    camera = area.camera

    # When: the drag is pressed, moved and released.
    getattr(area, "StartRangeSS")(PointerEvent(x=100, y=50))
    getattr(area, "MotionRangeSS")(PointerEvent(x=400, y=300))
    getattr(area, "ReleaseRangeSS")(PointerEvent(x=400, y=300))

    # Then: the crop box is the display rect itself, corner for corner, and it
    # is handed over as a list of four ints -- the shape ``saveCapture`` reads.
    assert len(camera.saves) == 1
    save = camera.saves[0]
    assert save["args"] == (), save
    assert save["kwargs"]["crop"] == 1
    assert save["kwargs"]["crop_ax"] == [100, 50, 400, 300]


def test_release_range_ssa_crop_box_for_a_backward_drag() -> None:
    # Given: the same selection dragged from the far corner back to the origin.
    area = _area_at_fixed_size(
        np.zeros((CAPTURE_HEIGHT, CAPTURE_WIDTH, 3), np.uint8), CAPTURE_SIZE
    ).area
    camera = area.camera

    # When: the drag is pressed at the far corner and released at the origin.
    getattr(area, "StartRangeSS")(PointerEvent(x=400, y=300))
    getattr(area, "MotionRangeSS")(PointerEvent(x=100, y=50))
    getattr(area, "ReleaseRangeSS")(PointerEvent(x=100, y=50))

    # Then: the corners are still ordered, because ``ReleaseRangeSS`` swaps them
    # at ``ReleaseRangeSS`` before building the box.
    assert camera.saves[0]["kwargs"]["crop_ax"] == [100, 50, 400, 300]


def test_start_range_ssa_logs_the_capture_coordinates_the_identity_ratio_produces() -> (
    None
):
    # Given: a range selection pressed at (100, 50) at the fixed assumption.
    area = _area_at_fixed_size(
        np.zeros((CAPTURE_HEIGHT, CAPTURE_WIDTH, 3), np.uint8), CAPTURE_SIZE
    ).area

    # When: the press is handled.
    with _captured_log() as messages:
        getattr(area, "StartRangeSS")(PointerEvent(x=100, y=50))

    # Then: exactly one line is emitted, and the four integers on it are the
    # display pair followed by the capture pair. ``StartRangeSS`` reports its
    # capture coordinate nowhere else, so the line is the only observable; the
    # assertion is on the numbers, not on the sentence around them.
    assert len(messages) == 1, messages
    assert _logged_integers(messages) == [100, 50, 100, 50]


def test_mouse_ctrl_left_press_probes_the_pixel_at_the_identity_coordinate() -> None:
    # Given: a full-size delivered frame at the fixed operating assumption.
    frame = _RecordingFrame(CAPTURE_HEIGHT, CAPTURE_WIDTH)
    area = _area_at_fixed_size(frame, CAPTURE_SIZE).area

    # When: the pixel at the centre of the display is probed.
    getattr(area, "mouseCtrlLeftPress")(PointerEvent(x=640, y=360))

    # Then: the 1x1 window cut from the frame is exactly the centre pixel, so
    # the one-pixel probe stays a one-pixel probe after the port.
    assert frame.probes == [((360, 361), (640, 641))], frame.probes


def test_img_rect_draws_the_display_rect_todays_show_ratio_produces() -> None:
    # Given: a bare area at the fixed operating assumption.
    from core.preview_renderer import ImgRectState, RectState

    area = _area_at_fixed_size(
        np.zeros((CAPTURE_HEIGHT, CAPTURE_WIDTH, 3), np.uint8), CAPTURE_SIZE
    ).area

    # When: a recognition box of (100, 50) - (200, 150) is reported.
    getattr(area, "ImgRect")(100.0, 50.0, 200.0, 150.0, "blue")

    # Then: the drawn rect is the box itself, with the white outer border one
    # capture pixel wider on every side, and the colour is the Win32 COLORREF
    # for "blue". This is the pre-port ``ImgRect`` with a ratio of 1.0.
    assert area._img_rect == ImgRectState(
        outer=RectState(x0=99, y0=49, x1=201, y1=151),
        inner=RectState(x0=100, y0=50, x1=200, y1=150),
        visible=True,
        color=0x00FF0000,
    )


# ===========================================================================
# 8. The old derivation is gone
# ===========================================================================


def test_capture_area_no_longer_defines_the_ratio_derivation() -> None:
    # Given: the parsed CaptureArea class body.
    node = _capture_area_node()
    defined = _class_method_names(node)

    # Then: the three functions that derived a scale from the requested size are
    # gone. Leaving ``_ratio`` behind would keep the old derivation callable by
    # accident, which is the failure mode the layer is meant to remove.
    for name in ("_captureRatio", "_showRatio", "_ratio"):
        assert name not in defined, (
            f"CaptureArea still defines {name}; the old derivation must be deleted"
        )

    # Then: and the removal is not an artefact of an empty class body.
    for name in ("StartRangeSS", "ReleaseRangeSS", "mouseCtrlLeftPress", "ImgRect"):
        assert name in defined, f"CaptureArea no longer defines {name}"
    assert "_mapper" in defined


def test_capture_area_keeps_the_capture_size_ratio_read_out_of_the_frame() -> None:
    # Given: the parsed ``mouseCtrlLeftPress`` source.
    press = class_method(_capture_area_node(), "mouseCtrlLeftPress")

    # Then: it no longer asks the camera what size it asked for. The scale and
    # the clamp both come from the frame that is about to be indexed, so
    # ``self.camera.capture_size`` is off this path entirely -- that mismatch is
    # the bug, and a surviving read is how it would come back.
    assert "capture_size" not in _self_camera_attributes(press), (
        "mouseCtrlLeftPress still reads self.camera.capture_size instead of the "
        "delivered frame's shape"
    )


# ===========================================================================
# 9. The range capture reports its outcome to the person who pressed the button
# ===========================================================================


def test_release_range_ssa_prints_one_success_line_with_the_crop_coordinates(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a forward drag whose save succeeds.
    area = _range_area(True)

    # When: the drag is pressed, moved and released.
    getattr(area, "StartRangeSS")(PointerEvent(x=100, y=50))
    getattr(area, "MotionRangeSS")(PointerEvent(x=400, y=300))
    getattr(area, "ReleaseRangeSS")(PointerEvent(x=400, y=300))

    # Then: exactly one line reaches the GUI log pane. ``sys.stdout`` is what
    # ``LogPane`` drains, so ``print`` is the only channel the person at the
    # keyboard can see -- and the file-only loguru sink is why they currently
    # cannot tell "did nothing" from "saved".
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, lines

    # Then: and it opens with the success wording, so a failure line can never
    # be read as a successful capture.
    assert lines[0].startswith("範囲キャプチャを保存しました:"), lines[0]

    # Then: and it carries the four crop coordinates in order. This is the only
    # place the caller learns *what* was saved rather than just that something
    # was. The save directory is deliberately not pinned -- it moves.
    assert _printed_integers_in_order(lines[0], [100, 50, 400, 300]), lines[0]


@pytest.mark.parametrize("save_result", [False, None], ids=["false", "none"])
def test_release_range_ssa_prints_one_failure_line_when_the_save_does_not_succeed(
    capsys: pytest.CaptureFixture[str],
    save_result: bool | None,
) -> None:
    # Given: a drag whose camera reports failure -- as False, or as None from a
    # save path that never returns a value at all.
    area = _range_area(save_result)

    # When: the drag is pressed, moved and released.
    getattr(area, "StartRangeSS")(PointerEvent(x=100, y=50))
    getattr(area, "MotionRangeSS")(PointerEvent(x=400, y=300))
    getattr(area, "ReleaseRangeSS")(PointerEvent(x=400, y=300))

    # Then: one line, and that line is the whole message -- nothing in the
    # failure case is the caller's to act on but the fact that it failed.
    assert capsys.readouterr().out.splitlines() == [
        "範囲キャプチャに失敗しました（詳細はログファイル）"
    ]


def test_start_and_motion_range_ssa_print_nothing_to_the_log_pane(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a drag in progress on a camera that would report success on
    # release, so silence here cannot be blamed on a failing save.
    area = _range_area(True)

    # When: the press is handled.
    getattr(area, "StartRangeSS")(PointerEvent(x=100, y=50))

    # Then: nothing reaches the log pane. The press is an in-flight state, and
    # a line for it would be a second, earlier report of a result the person
    # has not been given yet.
    assert capsys.readouterr().out == ""

    # When: the drag moves.
    getattr(area, "MotionRangeSS")(PointerEvent(x=400, y=300))

    # Then: still nothing. A line per motion event would bury the one line
    # that says the capture is done.
    assert capsys.readouterr().out == ""


def test_release_range_ssa_prints_the_crop_coordinates_in_order_for_a_backward_drag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the same successful drag, released at the origin instead of at the
    # far corner.
    area = _range_area(True)

    # When: the drag runs the other way.
    getattr(area, "StartRangeSS")(PointerEvent(x=400, y=300))
    getattr(area, "MotionRangeSS")(PointerEvent(x=100, y=50))
    getattr(area, "ReleaseRangeSS")(PointerEvent(x=100, y=50))

    # Then: one line carrying the same ordered rectangle. A line built from the
    # raw press/release pair would read 400 and 300 first and describe the
    # wrong two corners, which is the one thing a range capture message is for.
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, lines
    assert _printed_integers_in_order(lines[0], [100, 50, 400, 300]), lines[0]


# ===========================================================================
# 10. 映像を表示領域に収めて中央に置く配置計算
# ===========================================================================


@pytest.mark.parametrize(
    ("content", "viewport", "expected"),
    FIT_RECT_CASES,
    ids=["narrow", "exact", "tall", "wide"],
)
def test_fit_rect_keeps_the_aspect_ratio_and_centres_the_result(
    content: tuple[int, int],
    viewport: tuple[int, int],
    expected: tuple[int, int, int, int],
) -> None:
    # Given: a 16:9 content size and a viewport to place it in.
    fit_rect = _fit_rect()

    # When: the largest rectangle of that shape that fits is computed.
    rect = fit_rect(content, viewport)

    # Then: it is the rectangle the brief fixes by hand -- the aspect ratio is
    # kept and the leftover space becomes margin on both sides of the axis that
    # is left over, rather than being spent on stretching the image.
    assert rect == expected, rect


def test_fit_rect_letterboxes_a_portrait_viewport_instead_of_stretching() -> None:
    # Given: a viewport taller than it is wide, so width is the binding axis.
    fit_rect = _fit_rect()

    # When: a 16:9 content is placed in it.
    rect = fit_rect(CAPTURE_SIZE, (400, 900))

    # Then: the whole width is used and the height shrinks to match, so 400/225
    # is 16:9 to within a pixel. Scaling each axis independently would have
    # filled 400x900 and turned the picture into a different shape.
    assert rect == (0, 337, 400, 225), rect

    # Then: and the leftover height is split above and below, not all below.
    _, y, _, height = rect
    assert y == (900 - height) // 2, rect


@pytest.mark.parametrize(
    ("content", "viewport"),
    [
        ((0, 720), (1000, 700)),
        ((1280, 0), (1000, 700)),
        ((-1280, 720), (1000, 700)),
        ((1280, 720), (0, 700)),
        ((1280, 720), (1000, -1)),
        ((0, 0), (0, 0)),
    ],
    ids=[
        "zero_width_content",
        "zero_height_content",
        "negative_width_content",
        "zero_width_viewport",
        "negative_height_viewport",
        "both_empty",
    ],
)
def test_fit_rect_returns_an_empty_rect_when_either_extent_is_not_positive(
    content: tuple[int, int],
    viewport: tuple[int, int],
) -> None:
    # Given: a content or viewport size with no extent on some axis -- the state
    # a camera that has not delivered a frame yet leaves behind.
    fit_rect = _fit_rect()

    # When: a placement is asked for.
    rect = fit_rect(content, viewport)

    # Then: an empty rectangle, because the aspect ratio is undefined here and
    # dividing by either extent would raise inside the preview's resize path.
    assert rect == (0, 0, 0, 0), rect


@pytest.mark.parametrize(
    "viewport",
    [(1000, 700), (700, 1000), (1920, 1200), (1200, 1920), (333, 777)],
)
def test_fit_rect_never_returns_a_rect_that_overflows_its_viewport(
    viewport: tuple[int, int],
) -> None:
    # Given: viewports of both orientations around the fixed 16:9 content,
    # including one that divides unevenly on both axes.
    fit_rect = _fit_rect()

    # When: a placement is computed.
    x, y, width, height = fit_rect(CAPTURE_SIZE, viewport)

    # Then: the rectangle is inside the viewport on every side. Rounding the
    # scaled axis to the nearest pixel can overshoot by one, and a rectangle
    # that starts inside and ends outside would be drawn clipped for no reason.
    assert 0 <= x and x + width <= viewport[0], (viewport, width, height)
    assert 0 <= y and y + height <= viewport[1], (viewport, width, height)
    assert width > 0 and height > 0, (viewport, width, height)


# ===========================================================================
# 11. 表示面の中で映像を置いている位置（display_origin）
# ===========================================================================


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_a_display_origin_of_zero_leaves_the_transform_untouched(
    coordinate: tuple[int, int],
) -> None:
    # Given: a mapper that omits the origin -- the shape every already-written
    # call site builds, since the field defaults to the corner.
    mapper = _identity_mapper()
    x, y = coordinate

    # When: a display point is mapped into capture space.
    there = mapper.to_capture(x, y)

    # Then: the origin contributes nothing, so the two-size behaviour and every
    # contract pinned above survive the third field unchanged.
    assert there == (x, y), there

    # When: the capture point is mapped back.
    back = mapper.to_display(*there)

    # Then: still the identity.
    assert back == (x, y), back


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_to_capture_subtracts_the_display_origin_before_scaling(
    coordinate: tuple[int, int],
) -> None:
    # Given: a mapper at the identity scale whose image is offset inside the
    # display area, so subtraction is the only thing the mapping can do.
    mapper = _offset_mapper()
    x, y = coordinate

    # When: a display point is mapped into capture space.
    mapped = mapper.to_capture(x, y)

    # Then: the offset comes off first. A click on the image's own top-left
    # pixel must be capture (0, 0) -- the offset is where the image is, not part
    # of the pixel it shows.
    assert mapped == (x - DISPLAY_ORIGIN[0], y - DISPLAY_ORIGIN[1]), mapped


def test_to_capture_subtracts_the_display_origin_before_applying_the_scale() -> None:
    # Given: a mapper that halves the width, so the origin and the scale are
    # both observable in the result.
    mapper = _mapper_type()(
        capture_size=(640, 720),
        display_size=(1280, 720),
        display_origin=(10, 20),
    )

    # When: a display point is mapped into capture space.
    mapped = mapper.to_capture(30, 40)

    # Then: (30 - 10) * 0.5 = 10 and (40 - 20) * 1.0 = 20. Scaling first and
    # subtracting after would give (5, 0), which is inside the frame and
    # therefore silently wrong -- the same failure shape as the ratio bug.
    assert mapped == (10, 20), mapped


def test_to_capture_clamps_against_the_image_extent_not_the_display_area() -> None:
    # Given: an offset mapper at the fixed operating assumption.
    mapper = _offset_mapper()

    # When: a click on the margin above and to the left of the image is clamped.
    on_margin = mapper.to_capture(40, 10, clamp=True)

    # Then: it lands on the image's own top-left pixel. Clamping before the
    # subtraction would instead report an interior pixel of the image, which is
    # in bounds and indistinguishable from a deliberate click.
    assert on_margin == (0, 0), on_margin

    # When: a click past the image's right and bottom edges is clamped.
    past_edge = mapper.to_capture(1400, 800, clamp=True)

    # Then: it is held at the image's last addressable pixel, so the bound still
    # comes from ``capture_size`` and the margin does not widen the target.
    assert past_edge == (CAPTURE_WIDTH - 1, CAPTURE_HEIGHT - 1), past_edge


def test_to_display_adds_the_display_origin() -> None:
    # Given: an offset mapper at the fixed operating assumption.
    mapper = _offset_mapper()

    # When: the image's own corners are mapped into display space.
    top_left = mapper.to_display(0, 0)
    bottom_right = mapper.to_display(CAPTURE_WIDTH - 1, CAPTURE_HEIGHT - 1)

    # Then: the first lands on the image's corner inside the display area, which
    # is the point a drawn rectangle is positioned against, and the second at
    # the origin plus the image's own extent.
    assert top_left == DISPLAY_ORIGIN, top_left
    assert bottom_right == (
        DISPLAY_ORIGIN[0] + CAPTURE_WIDTH - 1,
        DISPLAY_ORIGIN[1] + CAPTURE_HEIGHT - 1,
    ), bottom_right


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_display_then_capture_round_trips_losslessly_with_a_display_origin(
    coordinate: tuple[int, int],
) -> None:
    # Given: an offset mapper at the identity scale.
    mapper = _offset_mapper()
    x, y = coordinate

    # When: a display point goes to capture space and back.
    there = mapper.to_capture(x, y)
    back = mapper.to_display(*there)

    # Then: the round trip is lossless, because the origin is a translation
    # applied once on the way in and undone once on the way out. A click and the
    # box it drew therefore still refer to the same pixel.
    assert back == (x, y), back


@pytest.mark.parametrize("coordinate", IDENTITY_COORDINATES)
def test_capture_then_display_round_trips_losslessly_with_a_display_origin(
    coordinate: tuple[int, int],
) -> None:
    # Given: an offset mapper at the identity scale.
    mapper = _offset_mapper()
    x, y = coordinate

    # When: a capture point goes to display space and back.
    there = mapper.to_display(x, y)
    back = mapper.to_capture(*there)

    # Then: it is lossless in the other direction too, which is the one
    # ``ImgRect`` exercises on every recognition result.
    assert back == (x, y), back


# ===========================================================================
# 12. 表示座標の長さをキャプチャ座標の長さに直す
# ===========================================================================


@pytest.mark.parametrize(
    ("capture_size", "display_size", "length", "expected"),
    [
        ((2560, 1440), (1280, 720), 10, 20),
        ((640, 720), (1280, 720), 10, 5),
        (CAPTURE_SIZE, CAPTURE_SIZE, 10, 10),
    ],
    ids=["double", "half", "identity"],
)
def test_length_to_capture_scales_by_the_capture_to_display_width_ratio(
    capture_size: tuple[int, int],
    display_size: tuple[int, int],
    length: int,
    expected: int,
) -> None:
    # Given: a mapper whose capture width differs from its display width -- the
    # state a letterboxed preview is always in.
    mapper = _mapper_type()(capture_size=capture_size, display_size=display_size)

    # When: a display-space length (a stick circle's radius) is converted.
    converted = mapper.length_to_capture(length)

    # Then: it scales by the x ratio, so the drawn circle keeps the size it has
    # on screen instead of drifting by the display's own scale.
    assert converted == expected, converted


def test_length_to_capture_rounds_to_nearest_rather_than_truncating() -> None:
    # Given: a mapper that halves the width, so an odd length lands on .5.
    mapper = _mapper_type()(capture_size=(640, 720), display_size=(1280, 720))

    # When: a 3 px display length is converted -- 3 * 0.5 = 1.5.
    converted = mapper.length_to_capture(3)

    # Then: it rounds to 2, where truncating toward zero would give 1. A length
    # has no "toward zero" contract the way a coordinate does, so the rule here
    # is deliberately the opposite of ``to_capture``'s.
    assert converted == 2, converted


def test_length_to_capture_breaks_an_exact_half_toward_even() -> None:
    # Given: the same halving mapper.
    mapper = _mapper_type()(capture_size=(640, 720), display_size=(1280, 720))

    # When: lengths that land exactly on .5 are converted.
    below = mapper.length_to_capture(5)
    above = mapper.length_to_capture(7)

    # Then: they round to the nearest even pixel. The half-pixel tie is the one
    # place the rule could differ without any other test noticing, so it is
    # pinned here rather than left to whichever rounding the caller happened to
    # use.
    assert (below, above) == (2, 4), (below, above)


def test_length_to_capture_never_returns_less_than_one_for_a_positive_length() -> None:
    # Given: a mapper that halves the width, so a 1 px display length is half a
    # capture pixel.
    mapper = _mapper_type()(capture_size=(640, 720), display_size=(1280, 720))

    # When: that length is converted.
    converted = mapper.length_to_capture(1)

    # Then: it is held at 1. A 0 px radius would hand the drawing code a
    # degenerate circle, which is a silent visual fault rather than an error --
    # and the caller already asked for something that exists.
    assert converted == 1, converted


@pytest.mark.parametrize("length", [0, -5], ids=["zero", "negative"])
def test_length_to_capture_returns_zero_for_a_non_positive_length(length: int) -> None:
    # Given: the halving mapper.
    mapper = _mapper_type()(capture_size=(640, 720), display_size=(1280, 720))

    # When: a zero or negative display length is converted.
    converted = mapper.length_to_capture(length)

    # Then: it comes back as 0 rather than being raised to the 1 px floor,
    # because "nothing was asked for" is not the same as "something tiny".
    assert converted == 0, converted


def test_length_to_capture_uses_a_ratio_of_one_when_the_display_width_is_zero() -> None:
    # Given: a mapper whose display size is still zero, which is what a camera
    # that has not delivered a frame yet leaves behind.
    mapper = _mapper_type()(capture_size=CAPTURE_SIZE, display_size=(0, 0))

    # When: a display length is converted.
    converted = mapper.length_to_capture(12)

    # Then: it comes back at ratio 1, matching ``to_capture``'s degradation, so
    # the first frame has a usable length instead of a ZeroDivisionError raised
    # from inside the drawing path.
    assert converted == 12, converted
