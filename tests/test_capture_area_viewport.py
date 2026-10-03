"""Contracts for the letterboxed preview viewport on ``CaptureArea``.

The preview is drawn by the renderer, not by Tk: the Frame hands the renderer a
**viewport** (its own pixel size) plus a capture-sized frame, and the renderer
scales that frame into the viewport by ``fit_rect`` and centres it, leaving
letterbox margins on the axis that has room to spare. Everything downstream of
that -- the pixel probe, the range selection, the stick overlay, the recognition
box -- therefore has to speak **capture** coordinates, and the widget has exactly
one place where a mouse position becomes one: the head of the handler.

This module pins that one place per consumer, with a viewport that is not the
capture size and therefore not the identity. A test written at capture ==
display would pass against a widget that still kept display coordinates, which
is exactly the shape the previous release had.

The seam is the recording doubles in ``tests/gdi_present_doubles.py``: a real
``tk.Frame`` cannot be built without a display, so every fact here is driven
through the real methods on a ``__new__``-built instance. The camera is a local
double because ``StubCamera`` is a slots dataclass whose ``saveCapture`` answers
``None`` and records nothing, while the crop rectangle only ever appears in its
argument.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from core.Camera import CAPTURE_SIZE
from gdi_present_doubles import PointerEvent, bare_capture_area, stick_drag, stick_press

#: The width and height in capture pixels, spelled out because the assertions
#: below quote the shape of a buffer and the index of a pixel.
CAPTURE_WIDTH, CAPTURE_HEIGHT = CAPTURE_SIZE

#: Every integer appearing in a captured log line, in order. The wording is not
#: the contract; the numbers a consumer reported are.
_INT_TOKEN = re.compile(r"-?\d+")

#: A viewport half the capture size, so every capture coordinate is exactly
#: twice the display coordinate and ``fit_rect`` produces no margin. It is the
#: simplest non-identity viewport there is, and it separates "converts" from
#: "passes display coordinates through" for every consumer at once.
HALF_VIEWPORT = (CAPTURE_WIDTH // 2, CAPTURE_HEIGHT // 2)  # (640, 360)

#: A viewport taller than 16:9, so the image is limited by its width and the
#: leftover height becomes a margin above and below it. ``fit_rect`` puts that
#: image at (0, 68) at 1000x563, which is the placement asserted here.
PILLARBOX_VIEWPORT = (1000, 700)
PILLARBOX_ORIGIN = (0, 68)
PILLARBOX_SIZE = (1000, 563)

#: The box the Frame *asked* for, which the geometry manager then refused to give
#: it. It is deliberately different from every viewport below: a widget whose
#: ``show_size`` happened to equal its viewport would convert correctly by
#: accident under the previous release too, so a test that pins ``show_size`` to
#: the viewport proves nothing about which of the two is the real reference.
REQUESTED_VIEWPORT = (800, 450)


class _Camera:
    """The camera surface a coordinate consumer reads: frames and crop boxes.

    ``StubCamera`` is a slots dataclass, so an instance attribute cannot be
    grafted onto it to record ``saveCapture`` -- and that call is the only place
    the range selection's result is observable. The crop rectangle appears in the
    argument rather than the return value, so this double records the argument
    and answers whatever the caller wants it to.
    """

    def __init__(
        self,
        capture_size: tuple[int, int] = CAPTURE_SIZE,
        frame: Any = None,
        save_result: bool = True,
    ) -> None:
        self.capture_size = capture_size
        self.frame = frame
        self.saves: list[dict[str, Any]] = []
        self.save_result = save_result
        self.sequence = 0

    def readFrame(self, copy: bool = False) -> Any:
        frame = self.frame
        if frame is None:
            return None
        return frame.copy() if copy else frame

    def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
        return self.readFrame(copy), self.sequence

    def frame_seq(self) -> int:
        return self.sequence

    def saveCapture(self, *args: Any, **kwargs: Any) -> bool:
        self.saves.append({"args": args, "kwargs": dict(kwargs)})
        return self.save_result


def _blank_frame() -> Any:
    """A capture-sized BGR frame of one flat colour."""
    import numpy as np

    frame = np.zeros((CAPTURE_HEIGHT, CAPTURE_WIDTH, 3), np.uint8)
    frame[:] = (7, 7, 7)
    return frame


def _marked_frame(row: int, column: int) -> Any:
    """A capture-sized frame whose single ``(row, column)`` pixel is unique.

    One pixel is the whole point: the colour probe converts exactly one 1x1
    window, so a frame that marks one pixel answers "which pixel did you read"
    through the colour the consumer logs, with no probe recording needed.
    """
    frame = _blank_frame()
    frame[row, column] = (10, 20, 30)
    return frame


def _area_at_viewport(
    viewport: tuple[int, int],
    *,
    show_size: tuple[int, int] = REQUESTED_VIEWPORT,
    frame: Any = None,
    save_result: bool = True,
) -> Any:
    """A bare ``CaptureArea`` whose viewport and capture size are both set.

    ``show_size`` is what the Frame asked for; ``viewport`` is what it was
    actually given. They are different on purpose -- see
    :data:`REQUESTED_VIEWPORT`.

    ``_allocBuffers`` is called last, at whatever buffer shape the production
    allocator produces, so a test may inspect it without re-deriving the rule.
    """
    width, height = show_size
    bare = bare_capture_area(width=width, height=height)
    area = bare.area
    area.camera = _Camera(
        capture_size=CAPTURE_SIZE, frame=frame, save_result=save_result
    )
    area._viewport = viewport
    area._allocBuffers()
    return bare


@contextmanager
def _captured_log() -> Iterator[list[str]]:
    """Collect INFO-and-above loguru messages, then detach the sink."""
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
    """Every integer in ``messages``, in order."""
    return [int(token) for message in messages for token in _INT_TOKEN.findall(message)]


def _marked_colour_seen(messages: list[str]) -> bool:
    """Whether any captured line reports the colour ``_marked_frame`` writes.

    A helper rather than an inline ``any(...)``: the assertion needs the colour
    triple and the surrounding assertion message, and inlining both in one
    statement makes the line unreadable. The colour is the observable that says
    *which* pixel the probe sliced, which the coordinate line alone cannot.
    """
    return any("Color [R: 30, G: 20, B: 10]" in message for message in messages)


# ===========================================================================
# 1. ビューポートは変換の基準になる
# ===========================================================================


def test_the_mapper_places_the_capture_size_inside_the_viewport_with_fit_rect() -> None:
    # Given: a bare area whose viewport is taller than 16:9 while the capture is
    # 16:9, so the image cannot fill the viewport and a margin is unavoidable.
    bare = _area_at_viewport(PILLARBOX_VIEWPORT)

    # When: the transform layer is built.
    mapper = getattr(bare.area, "_mapper")()

    # Then: the display size is the image's own rectangle -- margins excluded --
    # and the origin says where that rectangle sits in the viewport. Folding the
    # margin into the scale is the mistake this pins: the margin would then be
    # scaled a second time and a click would drift by one pixel per pixel of it.
    assert mapper.display_size == PILLARBOX_SIZE, mapper.display_size
    assert mapper.display_origin == PILLARBOX_ORIGIN, mapper.display_origin
    assert mapper.capture_size == CAPTURE_SIZE, mapper.capture_size


def test_configure_hands_the_surface_the_event_size_even_below_the_show_size() -> None:
    # Given: a bare area that asked for 800x450, so a Configure below that size
    # is the interesting case rather than the trivial one.
    bare = _area_at_viewport(HALF_VIEWPORT)
    assert bare.area.show_size == REQUESTED_VIEWPORT, bare.area.show_size

    # When: the Frame is given a smaller box than it asked for.
    getattr(bare.area, "_onConfigure")(PointerEvent(width=320, height=180))

    # Then: the surface is resized to the event size verbatim and the viewport
    # records it. Holding the show size up as a floor is what this replaces: the
    # renderer letterboxes whatever box it is given, so clamping it here would
    # leave the video drawn outside the widget and every click offset.
    assert bare.surface.resizes == [(320, 180)], bare.surface.resizes
    assert bare.area._viewport == (320, 180), bare.area._viewport


def test_configure_drops_the_frame_dedup_keys_so_the_next_tick_recomposes() -> None:
    # Given: a bare area that has already presented a frame, with the dedup keys
    # holding the generation it drew.
    bare = _area_at_viewport(HALF_VIEWPORT)
    area = bare.area
    area._last_frame_seq = 7
    area._last_disabled_seq = 7

    # When: the Frame is resized.
    getattr(area, "_onConfigure")(PointerEvent(width=320, height=180))

    # Then: both keys are dropped, because a drawn frame is not a drawn frame
    # any more -- the placement it was composited for is the old one. Without
    # this the camera would have to publish a new generation for the new
    # placement to appear, and a stopped camera would keep the old one forever.
    assert area._last_frame_seq is None, area._last_frame_seq
    assert area._last_disabled_seq is None, area._last_disabled_seq


def test_the_work_buffers_are_capture_sized_whatever_the_show_size_is() -> None:
    # Given: a bare area whose show size is half the capture size.
    bare = _area_at_viewport(HALF_VIEWPORT)
    area = bare.area
    area.show_width, area.show_height = HALF_VIEWPORT
    area.show_size = HALF_VIEWPORT

    # When: the buffers are (re)allocated.
    getattr(area, "_allocBuffers")()

    # Then: they are capture-sized, not display-sized. ``np.copyto`` in _convert
    # copies a converted frame -- which is capture-sized -- into these, so a
    # display-sized buffer raises on every filtered frame instead of drawing.
    expected = (CAPTURE_HEIGHT, CAPTURE_WIDTH, 3)
    assert area._filter_buf.shape == expected, area._filter_buf.shape
    assert area._correct_buf.shape == expected, area._correct_buf.shape

    # Then: and so is the disabled image, because the renderer discards anything
    # that is not capture-sized -- a differently sized one would be refused with
    # dimension_mismatch and the "camera is stopped" picture would never appear.
    assert area._disabled.shape == expected, area._disabled.shape


# ===========================================================================
# 2. 色取得 (Ctrl+左クリック)
# ===========================================================================


def test_the_colour_probe_reads_the_capture_pixel_under_a_halved_viewport() -> None:
    # Given: a viewport half the capture size, and a frame whose pixel at
    # capture (640, 360) is marked. Every other pixel is one flat colour, so the
    # logged colour names the pixel that was read and nothing else can.
    frame = _marked_frame(360, 640)
    bare = _area_at_viewport(HALF_VIEWPORT, frame=frame)

    # When: the centre of the display is clicked.
    with _captured_log() as messages:
        getattr(bare.area, "mouseCtrlLeftPress")(PointerEvent(x=320, y=180))

    # Then: the line reports the display point and the capture point it became --
    # (320, 180) at half scale is capture (640, 360), not (320, 180) passed
    # through, which is what a display-coordinate probe would report.
    assert _logged_integers(messages[:1]) == [320, 180, 640, 360], messages[:1]

    # Then: and the pixel it actually read is the marked one. BGR (10, 20, 30)
    # is RGB (30, 20, 10), so the R/G/B triple in the log is the marking.
    assert _marked_colour_seen(messages), messages


def test_a_click_on_the_letterbox_margin_is_clamped_onto_the_image_edge() -> None:
    # Given: a viewport taller than 16:9, so the image sits 68 px below the
    # viewport's top edge, and a frame whose capture (640, 0) is marked.
    frame = _marked_frame(0, 640)
    bare = _area_at_viewport(PILLARBOX_VIEWPORT, frame=frame)

    # When: y=10 is clicked, which is inside the margin above the image.
    with _captured_log() as messages:
        getattr(bare.area, "mouseCtrlLeftPress")(PointerEvent(x=500, y=10))

    # Then: the capture point is (640, 0). y is clamped to the image's own first
    # row rather than to the margin's, because the clamp bounds by capture_size
    # after the origin comes off -- clamping first would report an interior pixel
    # of the image for a click that never reached it.
    assert _logged_integers(messages[:1]) == [500, 10, 640, 0], messages[:1]
    assert _marked_colour_seen(messages), messages


def test_a_click_inside_the_image_has_the_display_origin_removed_first() -> None:
    # Given: the same letterboxed viewport, with the image at (0, 68).
    frame = _marked_frame(359, 640)
    bare = _area_at_viewport(PILLARBOX_VIEWPORT, frame=frame)

    # When: y=349 is clicked, which is (349 - 68) = 281 px into the image.
    with _captured_log() as messages:
        getattr(bare.area, "mouseCtrlLeftPress")(PointerEvent(x=500, y=349))

    # Then: the capture point is (640, 359). Scaling before subtracting the
    # origin would give int(349 * 720/563) = 446 instead, which is in bounds
    # and therefore silently wrong -- the same failure shape as the ratio bug
    # this layer exists to close.
    assert _logged_integers(messages[:1]) == [500, 349, 640, 359], messages[:1]
    assert _marked_colour_seen(messages), messages


# ===========================================================================
# 3. 範囲選択 (Ctrl+Shift+ドラッグ)
# ===========================================================================


def test_a_range_drag_crops_the_capture_rectangle_it_selected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a viewport half the capture size and a live frame.
    bare = _area_at_viewport(HALF_VIEWPORT, frame=_blank_frame(), save_result=True)
    area = bare.area
    camera = area.camera

    # When: a drag from display (100, 50) to (200, 100) is pressed, moved and
    # released -- both corners inside the viewport, so no clamping is involved
    # and the assertion is about the conversion alone.
    getattr(area, "StartRangeSS")(PointerEvent(x=100, y=50))
    getattr(area, "MotionRangeSS")(PointerEvent(x=200, y=100))
    getattr(area, "ReleaseRangeSS")(PointerEvent(x=200, y=100))

    # Then: the crop box is the capture rectangle those two display points name,
    # handed over as the list of four ints ``saveCapture`` reads. At half scale
    # that is (200, 100) - (400, 200); the display rectangle itself would have
    # been saved, cropping a quarter of the intended area out of the top left.
    assert len(camera.saves) == 1, camera.saves
    save = camera.saves[0]
    assert save["args"] == (), save
    assert save["kwargs"]["crop"] == 1, save
    assert save["kwargs"]["crop_ax"] == [200, 100, 400, 200], save

    # Then: and the guide rect the renderer draws is in capture coordinates too,
    # so the box on screen is the box that gets cut out.
    assert (area._guide.x0, area._guide.y0) == (200, 100), area._guide
    assert (area._guide.x1, area._guide.y1) == (401, 201), area._guide
    assert area._guide.visible is True, area._guide

    # Then: and the single success line still reaches the log pane.
    assert len(capsys.readouterr().out.splitlines()) == 1


def test_a_range_drag_outside_the_image_is_clamped_to_the_capture_extent() -> None:
    # Given: a viewport half the capture size, whose capture extent is 1280x720.
    bare = _area_at_viewport(HALF_VIEWPORT, frame=_blank_frame())
    area = bare.area

    # When: the drag is released far outside the viewport, which a Motion beyond
    # the widget's own box delivers.
    getattr(area, "StartRangeSS")(PointerEvent(x=100, y=50))
    getattr(area, "MotionRangeSS")(PointerEvent(x=5000, y=5000))
    getattr(area, "ReleaseRangeSS")(PointerEvent(x=5000, y=5000))

    # Then: the selection ends at the capture extent rather than at a coordinate
    # the frame does not have, so the crop cannot index past the buffer.
    assert area.max_x == CAPTURE_WIDTH, area.max_x
    assert area.max_y == CAPTURE_HEIGHT, area.max_y
    assert area.camera.saves[0]["kwargs"]["crop_ax"] == [200, 100, 1280, 720]


# ===========================================================================
# 4. スティック (左右ドラッグ)
# ===========================================================================


def test_the_stick_ring_is_capture_sized_for_the_viewport_it_was_pressed_in() -> None:
    # Given: a viewport half the capture size.
    bare = _area_at_viewport(HALF_VIEWPORT)
    area = bare.area

    # When: the left stick is pressed at the centre of the display.
    stick_press(area, 320, 180)

    # Then: the ring is centred on the capture point, and its radius is the
    # display radius (60 px) converted into capture units: 120 at half scale. A
    # radius left in display units would be drawn at half its intended size,
    # because the renderer draws in capture pixels.
    stick = area.overlay.left_stick
    assert stick.active is True, stick
    assert (stick.center_x, stick.center_y) == (640, 360), stick
    assert stick.radius == 120, stick.radius
    assert (stick.knob_x, stick.knob_y) == (640, 360), stick


def test_a_display_radius_drag_still_reaches_full_tilt_at_half_scale() -> None:
    # Given: a viewport half the capture size with the left stick pressed at the
    # centre of the display.
    bare = _area_at_viewport(HALF_VIEWPORT)
    area = bare.area
    stick_press(area, 320, 180)

    # When: the pointer is dragged 60 px to the right on screen -- exactly the
    # radius the user was shown.
    stick_drag(area, 380, 180)

    # Then: the tilt saturates. The gesture is defined in display pixels, so the
    # conversion has to happen before the magnitude is taken; a magnitude taken
    # in display units against a capture radius would read 0.5 here and the
    # stick would never reach the edge.
    assert area._lmag == 1.0, area._lmag
    assert area._langle == 0.0, area._langle

    # Then: and the knob is pinned to the snap circle in capture units --
    # radius + radius // 11 on the capture radius, so 130 rather than 65.
    stick = area.overlay.left_stick
    assert stick.knob_x == 640 + 130, stick
    assert stick.knob_y == 360, stick


def test_a_drag_inside_the_ring_puts_the_knob_on_the_capture_point() -> None:
    # Given: a viewport half the capture size with the right stick pressed.
    bare = _area_at_viewport(HALF_VIEWPORT)
    area = bare.area
    stick_press(area, 320, 180, side="R")

    # When: the pointer moves 15 px right and 10 px down on screen, inside the
    # ring.
    stick_drag(area, 335, 190, side="R")

    # Then: the knob sits exactly on the capture point of the pointer, which is
    # the display point scaled by the viewport and nothing else.
    stick = area.overlay.right_stick
    assert (stick.knob_x, stick.knob_y) == (670, 380), stick
    assert (stick.center_x, stick.center_y) == (640, 360), stick


# ===========================================================================
# 5. 認識枠 (ImgRect)
# ===========================================================================


def test_the_recognition_box_is_stored_in_capture_coordinates() -> None:
    # Given: a viewport half the capture size, so a conversion to display
    # coordinates would halve the box.
    bare = _area_at_viewport(HALF_VIEWPORT)
    area = bare.area

    # When: a recognition box in capture pixels is reported.
    getattr(area, "ImgRect")(10, 20, 110, 220, outline="blue")
    img_rect = area.overlay.img_rect

    # Then: the inner rectangle is the box itself and the outer one is expanded
    # by 1.0 capture pixel on every side. Both are capture coordinates: the
    # renderer draws them on the capture-sized back buffer, so converting to
    # display here would put the box on half the pixels it was found on.
    assert (img_rect.inner.x0, img_rect.inner.y0) == (10, 20), img_rect
    assert (img_rect.inner.x1, img_rect.inner.y1) == (110, 220), img_rect
    assert (img_rect.outer.x0, img_rect.outer.y0) == (9, 19), img_rect
    assert (img_rect.outer.x1, img_rect.outer.y1) == (111, 221), img_rect
    assert img_rect.visible is True, img_rect
