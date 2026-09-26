"""RED contracts for the ``CaptureArea`` overlay state; # noqa: SIZE_OK -
approved brief requires one module per design step group.

Design: ``docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md``
sections 5 and 8. Canvas items are abolished and the overlay becomes data
(``core.preview_renderer.OverlayState``), so everything the stick handlers and
the recognition box used to draw has to survive as state the renderer can read.

This file owns the *state* half of that refactor. Its companion,
``tests/test_capture_area_surface_contract.py``, owns the class and the present
path. Two coverage gaps the plan for this step has to close live here as well:
``ImgRect`` and ``mouseLeftRelease`` have no covering test today, which is
exactly the state that the refactor is most likely to drop silently.

Three kinds of assertion are used, each for what it can actually prove:

* **State** -- drive the real ``mouseLeftPress`` / ``mouseLeftPressing`` /
  ``mouseLeftRelease`` on a ``__new__``-style instance and read the overlay back.
* **Source** -- ``ast`` for what cannot be constructed headless.
* **Rendered** -- feed the overlay the area actually produced into a real
  ``GdiSurface`` against the ``RecordingGdiApi`` double from
  ``tests/test_gdi_surface_contract.py``. That is the only way to pin the
  quantities the state does not store: the knob radius ``radius // 10`` and the
  ``ImgRect`` stroke widths 4 and 2 are the renderer's business, and asserting
  them at the boundary proves the state's coordinates survive all the way to a
  shape call.

The sign of the stick snap is the sharpest claim here. ``cy = y_init - d *
sin(rad)`` looks like a typo in isolation but is not: screen y grows downward,
so a positive angle tilts the knob upward. The test uses an asymmetric angle so
a ``+sin`` implementation lands 65 px away and cannot pass.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from gdi_present_doubles import (
    KNOB_RADIUS,
    SHOW_HEIGHT,
    SHOW_WIDTH,
    SNAP_DISTANCE,
    STICK_RADIUS,
    TK_BLUE_COLORREF,
    TK_RED_COLORREF,
    WHITE_COLORREF,
    bare_capture_area,
    stick_drag,
    stick_press,
    stick_release,
)

_SURFACE_SIZE = (1280, 720)
_PARENT_HWND = 0xABCD
# The GDI surface presents at the design's fixed 1280x720. A read-only frame is
# enough: compose copies it into the DIB, so no test can reach the next one
# through a shared buffer.
_SURFACE_FRAME = np.zeros((_SURFACE_SIZE[1], _SURFACE_SIZE[0], 3), np.uint8)
_SURFACE_FRAME.setflags(write=False)

_PS_SOLID = 0

#: The recognition box the pinned ``ImgRect`` call is given, in capture pixels.
_RECT_CAPTURE = (100, 200, 300, 400)


def _shape_calls(overlay: Any) -> list[tuple[str, int, int, int, int]]:
    """Every Ellipse / Rectangle the renderer issues for ``overlay``, in order.

    Runs a real ``GdiSurface`` against the recording Win32 double, so the numbers
    are the ones the design's extent convention produces rather than a
    re-derivation of them.
    """
    api = _composed_api(overlay)
    return [
        (name, args[1], args[2], args[3], args[4])
        for name, args in api.log
        if name in {"ellipse", "rectangle"}
    ]


def _composed_api(overlay: Any) -> Any:
    """A ``RecordingGdiApi`` that has composed ``overlay`` once, at 1280x720."""
    from core import gdi_surface
    from test_gdi_surface_contract import RecordingGdiApi

    api = RecordingGdiApi()
    surface = gdi_surface.GdiSurface(api=api)
    surface.attach(_PARENT_HWND, _SURFACE_SIZE)
    result = surface.compose(_SURFACE_FRAME, overlay)
    assert result.ok is True, result.detail
    return api


def _rectangle_pen_for(api: Any, index: int) -> Any:
    """The ``PenRequest`` selected before the ``index``-th ``Rectangle``."""
    pen_handle, _brush_handle = _selections_before_shape(api, "rectangle", index)
    return api.pen_calls[api.pen_handles.index(pen_handle)]


def _selections_before_shape(api: Any, shape: str, index: int) -> tuple[int, int]:
    """The ``(pen, brush)`` handles selected into the memory DC before a shape."""
    pens = set(api.pen_handles)
    brushes = set(api.brush_handles) | set(api.hollow_brush_handles)
    pen = brush = 0
    seen = 0
    for name, args in api.log:
        if name == "select_object" and args[0] == api.memory_dc:
            if args[1] in pens:
                pen = args[1]
            elif args[1] in brushes:
                brush = args[1]
        elif name == shape:
            if seen == index:
                return pen, brush
            seen += 1
    raise AssertionError(f"no {shape} call #{index} was recorded")


def _left_stick(bare: Any) -> Any:
    return bare.area.overlay.left_stick


def _right_stick(bare: Any) -> Any:
    return bare.area.overlay.right_stick


def _img_rect_visible(bare: Any) -> Any:
    """A fresh read of the recognition box's visibility flag.

    A helper rather than a chained attribute read, because the checker narrows a
    repeated member expression: an ``is False`` assertion followed by an
    ``is True`` assertion on ``bare.area.overlay.img_rect.visible`` would make
    the statements between them look unreachable.
    """
    return bare.area.overlay.img_rect.visible


# ===========================================================================
# D2.5 The default overlay is all-default and therefore draws nothing
# ===========================================================================


def test_the_default_overlay_draws_no_shape() -> None:
    # Given: a default-constructed area's overlay.
    bare = bare_capture_area()
    overlay = bare.area.overlay

    # When: the renderer composes it.
    api = _composed_api(overlay)

    # Then: zero shape calls, so nothing is drawn until something is active
    # (design section 5: an all-default overlay issues zero shape calls).
    assert api.ellipse_calls == []
    assert api.rectangle_calls == []


# ===========================================================================
# D3. The stick state machine, pinned exactly
# ===========================================================================


def test_press_arms_the_stick_at_the_press_point_with_the_area_radius() -> None:
    # Given: a bare area at the design's stick radius.
    bare = bare_capture_area()
    assert bare.area.radius == STICK_RADIUS

    # When: the left stick is pressed at (200, 300).
    stick_press(bare.area, 200, 300)
    stick = _left_stick(bare)

    # Then: it is active, the ring is centred on the press point at the area's
    # own radius, and the knob starts on the press point too.
    assert stick.active is True
    assert (stick.center_x, stick.center_y) == (200, 300)
    assert stick.radius == bare.area.radius
    assert (stick.knob_x, stick.knob_y) == (200, 300)


def test_the_knob_is_drawn_at_radius_over_ten_of_the_ring() -> None:
    # Given: a pressed left stick.
    bare = bare_capture_area()
    stick_press(bare.area, 200, 300)

    # When: the overlay the area produced is rendered.
    api = _composed_api(bare.area.overlay)

    # Then: the ring and the filled knob are the only two shapes, and the knob's
    # radius is radius // 10 -- the design inherits k = radius // 10 from
    # GuiAssets.py:980, and it is the renderer's business, not the state's.
    ring, knob = api.ellipse_calls
    assert api.rectangle_calls == []
    assert (ring.right - ring.left) - 1 == 2 * STICK_RADIUS + 1
    assert (knob.right - knob.left) - 1 == 2 * KNOB_RADIUS + 1
    assert (knob.right - knob.left) - 1 == 2 * (STICK_RADIUS // 10) + 1

    # Then: and the knob really is centred on the press point, so the derived
    # radius is anchored where the user actually clicked.
    assert ((knob.left + knob.right) // 2, (knob.top + knob.bottom) // 2) == (200, 300)


def test_a_short_drag_moves_the_knob_only_and_leaves_the_ring_put() -> None:
    # Given: a stick pressed at (200, 300).
    bare = bare_capture_area()
    stick_press(bare.area, 200, 300)

    # When: the pointer moves to (230, 320), inside the ring.
    magnitude = math.hypot(30, -20) / STICK_RADIUS
    assert magnitude < 1.0
    stick_drag(bare.area, 230, 320)
    stick = _left_stick(bare)

    # Then: the knob follows the pointer exactly.
    assert (stick.knob_x, stick.knob_y) == (230, 320)

    # Then: and the ring centre does not move, which is the whole point of
    # keeping the centre absolute rather than an offset (design section 5).
    assert (stick.center_x, stick.center_y) == (200, 300)


def test_a_full_tilt_drag_snaps_the_knob_above_the_ring_at_the_snap_radius() -> None:
    # Given: a stick pressed at (200, 300).
    bare = bare_capture_area()
    stick_press(bare.area, 200, 300)
    press_x, press_y = 200, 300

    # When: the pointer is dragged to (280, 254): 92.3 px away, so the tilt is
    # saturated, and the implied angle is ~29.9 degrees, which is deliberately
    # asymmetric so cos and sin differ.
    stick_drag(bare.area, 280, 254)
    stick = _left_stick(bare)

    # Then: the knob is pinned to the snap circle, not to the pointer, and the
    # snap distance is radius + radius // 11 -- strictly outside the ring.
    dx = 280 - press_x
    dy = press_y - 254
    assert math.hypot(dx, dy) > STICK_RADIUS
    angle = math.degrees(math.atan2(dy, dx))
    assert 20.0 < angle < 40.0, "the fixture angle must stay clearly asymmetric"
    radians = math.radians(angle)
    expected_x = press_x + SNAP_DISTANCE * math.cos(radians)
    expected_y = press_y - SNAP_DISTANCE * math.sin(radians)

    # A 0.5 px tolerance covers the float point itself. The knob is stored as
    # an int because GDI takes integers, and rounding the two components
    # independently moves the radius by up to ~0.7 px, so the radius is
    # checked at 1.0. A `+sin` implementation misses by
    # 2 * SNAP_DISTANCE * sin(angle) ~= 32 px, so this is not a weakened
    # comparison.
    assert stick.knob_x == pytest.approx(expected_x, abs=0.5)
    assert stick.knob_y == pytest.approx(expected_y, abs=0.5)

    # Then: and the sign is pinned explicitly. Screen y grows downward, so a
    # positive angle tilts the knob upward; `cy = y_init - d * sin` is correct and
    # `cy = y_init + d * sin` is not.
    assert expected_y < press_y
    assert stick.knob_y < stick.center_y

    # Then: and the knob sits outside the ring it belongs to. Integer rounding
    # moves the radius, so the tolerance has to admit it while still rejecting
    # anything that is not on the snap circle.
    distance = math.hypot(stick.knob_x - 200, stick.knob_y - 300)
    assert distance == pytest.approx(SNAP_DISTANCE, abs=1.0)
    assert distance > STICK_RADIUS


def test_release_deactivates_the_stick() -> None:
    # Given: a pressed and then dragged left stick.
    bare = bare_capture_area()
    stick_press(bare.area, 200, 300)
    stick_drag(bare.area, 230, 320)
    assert _left_stick(bare).active is True

    # When: the button is released.
    stick_release(bare.area)
    stick = _left_stick(bare)

    # Then: the stick is inactive, so the next compose issues no shape for it.
    assert stick.active is False


def test_the_ring_centre_never_moves_across_many_drags() -> None:
    # Given: a stick pressed at (200, 300).
    for side in ("L", "R"):
        bare = bare_capture_area()
        stick_press(bare.area, 200, 300, side=side)
        read = _left_stick if side == "L" else _right_stick
        assert read(bare).center_x == 200
        assert read(bare).center_y == 300

        # When: the pointer wanders inside and outside the ring many times.
        waypoints = [
            (230, 320),
            (200, 260),
            (400, 300),
            (100, 500),
            (201, 299),
        ]
        for x, y in waypoints:
            stick_drag(bare.area, x, y, side=side)

        # Then: the centre is exactly where the press put it, every time, and the
        # knob ends on the last event.
        stick = read(bare)
        assert (stick.center_x, stick.center_y) == (200, 300), side
        assert (stick.knob_x, stick.knob_y) == waypoints[-1], side
        assert stick.active is True, side


# ===========================================================================
# F3. Coverage gaps the refactor must not drop
# ===========================================================================


def test_img_rect_outer_carries_the_capture_pixel_expansion_and_the_inner_does_not() -> (
    None
):
    # Given: a bare area whose capture size equals its show size, so the display
    # and capture coordinate systems are the identity.
    bare = bare_capture_area()
    assert bare.camera.capture_size == (SHOW_WIDTH, SHOW_HEIGHT)
    x1, y1, x2, y2 = _RECT_CAPTURE

    # When: the recognition box is reported.
    getattr(bare.area, "ImgRect")(x1, y1, x2, y2, outline="blue")
    img_rect = bare.area.overlay.img_rect

    # Then: the outer rectangle is the capture box expanded by 1.0 capture pixel
    # on every side, and the inner rectangle is the box itself. Collapsing them
    # would silently drop the white border (design section 5).
    assert img_rect.outer.x0 == x1 - 1.0
    assert img_rect.outer.y0 == y1 - 1.0
    assert img_rect.outer.x1 == x2 + 1.0
    assert img_rect.outer.y1 == y2 + 1.0
    assert img_rect.inner.x0 == x1
    assert img_rect.inner.y0 == y1
    assert img_rect.inner.x1 == x2
    assert img_rect.inner.y1 == y2
    assert img_rect.outer != img_rect.inner

    # Then: and the two rectangles really are drawn as two, each with the GDI
    # bounding-box +1 on the right and bottom edge, and with the widths the
    # design rounds Tk's 4.5 and 2.5 to.
    shapes = _shape_calls(bare.area.overlay)
    assert shapes == [
        ("rectangle", x1 - 1, y1 - 1, x2 + 2, y2 + 2),
        ("rectangle", x1, y1, x2 + 1, y2 + 1),
    ]
    api = _composed_api(bare.area.overlay)
    outer_pen = _rectangle_pen_for(api, 0)
    inner_pen = _rectangle_pen_for(api, 1)
    assert (outer_pen.style, outer_pen.width) == (_PS_SOLID, 4)
    assert (inner_pen.style, inner_pen.width) == (_PS_SOLID, 2)
    assert outer_pen.color == WHITE_COLORREF


def test_img_rect_colour_reaches_the_overlay_as_a_win32_colorref() -> None:
    # Given: a bare area at ratio 1.0.
    bare = bare_capture_area()
    x1, y1, x2, y2 = _RECT_CAPTURE

    # When: the recognition box is reported with the two colour names the
    # recognition path actually passes (CommandVision.py:534 and :541).
    getattr(bare.area, "ImgRect")(x1, y1, x2, y2, outline="blue")
    blue_overlay = bare.area.overlay
    getattr(bare.area, "ImgRect")(x1, y1, x2, y2, outline="red")
    red_overlay = bare.area.overlay

    # Then: the colour lands on the overlay as a Win32 COLORREF, not a Tk name and
    # not Tk's own 0x00RRGGBB. Tk and Win32 disagree on the byte order, and the
    # two names are deliberately chosen so the disagreement is observable: a
    # passthrough gives 0x000000FF for blue where Win32 wants 0x00FF0000.
    assert isinstance(blue_overlay.img_rect.color, int)
    assert blue_overlay.img_rect.color == TK_BLUE_COLORREF
    assert red_overlay.img_rect.color == TK_RED_COLORREF
    assert blue_overlay.img_rect.color != red_overlay.img_rect.color

    # Then: and the inner border is the pen that colour, so the conversion
    # actually reaches GDI rather than only being stored.
    api = _composed_api(blue_overlay)
    assert _rectangle_pen_for(api, 1).color == TK_BLUE_COLORREF


def test_img_rect_visibility_toggles_the_pair_together() -> None:
    # Given: a fresh area with no recognition box drawn yet.
    bare = bare_capture_area()
    assert _img_rect_visible(bare) is False

    # When: a box is reported.
    getattr(bare.area, "ImgRect")(*_RECT_CAPTURE, outline="blue")
    assert _img_rect_visible(bare) is True
    assert len(_shape_calls(bare.area.overlay)) == 2

    # When: the box is hidden.
    getattr(bare.area, "deleteImageRect")()

    # Then: one flag governs the pair, so hiding takes both rectangles down and
    # the next compose issues no rectangle at all.
    assert _img_rect_visible(bare) is False
    assert _shape_calls(bare.area.overlay) == []


def test_mouse_left_release_clears_the_state_restores_the_cursor_and_rebinds() -> None:
    # Given: a left stick that was pressed and dragged, with the right stick also
    # assigned to the mouse.
    bare = bare_capture_area()
    stick_press(bare.area, 200, 300)
    stick_drag(bare.area, 230, 320)
    assert _left_stick(bare).active is True

    # When: the left button is released.
    stick_release(bare.area)

    # Then: the stick state is cleared, the cursor goes back to the crosshair, and
    # the sibling right drag is re-bound so the other stick still works.
    assert _left_stick(bare).active is False
    assert bare.widget.cursor_values()[-1] == "tcross"
    assert "<ButtonPress-3>" in bare.widget.bound_sequences()


def test_mouse_left_release_is_safe_with_no_press_active() -> None:
    # Given: a bare area where the left stick was never pressed.
    bare = bare_capture_area()
    assert _left_stick(bare).active is False

    # When: the release runs anyway, which happens whenever a press was lost to
    # a focus change or a modal dialog.
    stick_release(bare.area)

    # Then: it does not raise, the stick stays inactive, and the cursor is still
    # restored to the crosshair.
    assert _left_stick(bare).active is False
    assert bare.widget.cursor_values() == ["tcross"]
