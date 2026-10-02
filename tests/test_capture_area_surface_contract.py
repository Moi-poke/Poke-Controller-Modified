"""RED contracts for design steps D and F on ``CaptureArea``; # noqa: SIZE_OK -
approved brief requires one module per design step group.

The approved brief merges what the design splits into steps D (``CaptureArea``
becomes a ``tk.Frame`` hosting the GDI surface) and F (the measurement harness
instruments the real present path). They ship together because
``tests/preview_fps_support.py`` reads ``area._photo`` today: removing the
PhotoImage breaks the gated E2E, and that E2E is skipped unless
``POKECON_RUN_PREVIEW_FPS_E2E=1``, so ``task ci`` stays green and hides it. A
separate "done" for either half is therefore a false green.

Design: ``docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md``
sections 8, 9, 10 and 11.

Two deliberately different tools are used here:

* **Source-level** (``ast``) for everything about the class itself -- the base
  class, the absence of every canvas call, the absence of the PhotoImage, the
  constructor signature, ``_convert``'s body. A real ``tk.Frame`` cannot be
  constructed without a display, so class shape is read from the parsed file.
* **Instance-level** against the recording doubles in
  ``tests/gdi_present_doubles.py`` for everything about behaviour -- the
  compose/present sequence, the same-seq skip, the cursor, the binds, the
  ``<Configure>`` handler, ``setShowsize`` and the filter chain.

The load-bearing assertion is
:func:`test_capture_area_class_body_issues_no_tk_canvas_calls`. The design exists
because ``cv.coords()`` repaints the item's old bounding box from the canvas
background and erases the video (design section 0), so a single surviving
``coords()`` inside ``CaptureArea`` reintroduces the exact bug this refactor
removes.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from gdi_present_doubles import (
    CANVAS_ITEM_API,
    FILTER_LOWER,
    FILTER_UPPER,
    NON_NEUTRAL_CORRECTION,
    PHOTO_IMAGE_ATTRIBUTES,
    PIL_SYMBOLS,
    SHOW_HEIGHT,
    SHOW_WIDTH,
    PointerEvent,
    StubCamera,
    bare_capture_area,
    bgr_frame,
    filter_probe_frame,
    spy,
    stick_press,
    stick_release,
)
from gdi_source_readers import (
    GUI_ASSETS_SOURCE,
    WINDOW_SOURCE,
    assigned_attribute_names,
    attribute_attr_names,
    call_attr_names,
    class_method,
    class_node,
    dotted_name,
    module_tree,
    referenced_names,
)

#: The non-Windows fallback renderer, which is the codebase's remaining canvas
#: user. The canvas API set is only provably non-vacuous if some file still
#: exercises it, and design section 8 puts that file here rather than in
#: ``GuiAssets.py``.
PHOTO_SURFACE_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "SerialController"
    / "ui"
    / "photo_surface.py"
)


def _gui_assets() -> Any:
    """Import ``GuiAssets`` at call time so a missing module is a per-test RED."""
    import GuiAssets as module

    return module


def _capture_area_class() -> Any:
    """The real ``CaptureArea`` class, reached through the ``Any`` boundary."""
    return _gui_assets().CaptureArea


def _capture_area_node() -> ast.ClassDef:
    return class_node(module_tree(GUI_ASSETS_SOURCE), "CaptureArea")


def _bind_sequences_in(node: ast.AST) -> list[str]:
    """The first positional argument of every ``bind`` call in ``node``."""
    sequences: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        name = func.attr if isinstance(func, ast.Attribute) else None
        if isinstance(func, ast.Name):
            name = func.id
        if name != "bind" or not child.args:
            continue
        first = child.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            sequences.append(first.value)
    return sequences


def _bind_targets_in(node: ast.AST) -> dict[str, str]:
    """``{sequence: unparsed callback}`` for every ``bind`` call in ``node``."""
    targets: dict[str, str] = {}
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        name = func.attr if isinstance(func, ast.Attribute) else None
        if isinstance(func, ast.Name):
            name = func.id
        if name != "bind" or len(child.args) < 2:
            continue
        sequence = child.args[0]
        if isinstance(sequence, ast.Constant) and isinstance(sequence.value, str):
            targets[sequence.value] = ast.unparse(child.args[1])
    return targets


def _frame_shaped_arrays(area: Any) -> list[Any]:
    """Every ``(height, width, 3)`` uint8 ndarray held on the instance."""
    return [
        value
        for value in vars(area).values()
        if isinstance(value, np.ndarray)
        and value.dtype == np.uint8
        and value.ndim == 3
        and value.shape[2] == 3
    ]


def mask_pixels(frame: Any) -> int:
    """The pixel count of a single-channel-shaped frame, for the mask guard."""
    return int(frame.shape[0]) * int(frame.shape[1])


# ===========================================================================
# D1. The base class changed and the canvas is gone
# ===========================================================================


def test_capture_area_is_a_frame_and_not_a_canvas() -> None:
    # Given: the real CaptureArea class.
    import tkinter as tk

    capture_area = _capture_area_class()

    # Then: it is a Frame, because the video lives in a child HWND and the
    # overlay is drawn by the renderer rather than by Tk canvas items.
    assert issubclass(capture_area, tk.Frame)

    # Then: and no longer a Canvas, so it inherits no item machinery at all.
    assert issubclass(capture_area, tk.Canvas) is False


def test_capture_area_class_body_issues_no_tk_canvas_calls() -> None:
    # Given: the parsed CaptureArea class body.
    node = _capture_area_node()

    # When: every attribute read and every call inside the class body is scanned.
    attributes = attribute_attr_names(node)
    calls = call_attr_names(node)

    # Then: no canvas item API survives. The walk is scoped to the class, so
    # ControllerGUI in the same file may keep using canvas-shaped Tk freely.
    assert not (attributes & CANVAS_ITEM_API), (
        f"CaptureArea still touches the Tk canvas API: {sorted(attributes & CANVAS_ITEM_API)}"
    )
    assert not (calls & CANVAS_ITEM_API), (
        f"CaptureArea still calls the Tk canvas API: {sorted(calls & CANVAS_ITEM_API)}"
    )

    # Then: and the prohibition is not vacuous -- the canvas API set is still
    # exercised by a real file, so the class-scoped walk above discriminates.
    # The remaining canvas user is the non-Windows fallback renderer, not a
    # second class in GuiAssets.py.
    assert not (
        attribute_attr_names(module_tree(GUI_ASSETS_SOURCE)) & CANVAS_ITEM_API
    ), "GuiAssets.py still touches the canvas API outside CaptureArea"
    assert attribute_attr_names(module_tree(PHOTO_SURFACE_SOURCE)) & CANVAS_ITEM_API, (
        "no file in the tree uses a canvas, so the class-scoped assertion above "
        "cannot distinguish anything"
    )


def _widget_option_value(node: ast.expr) -> Any:
    """A widget option's value, compared by value rather than by unparsed text.

    ``ast.unparse`` renders a string constant through ``repr``, so a
    double-quoted expectation can never match it; the option's value is the
    contract, not its spelling. A non-literal falls back to the unparsed text
    because ``bd`` is a legitimate Tk alias a caller may pass as a name.
    """
    if isinstance(node, ast.Constant):
        return ast.literal_eval(node)
    return ast.unparse(node)


def test_capture_area_init_creates_no_photo_image() -> None:
    # Given: the real CaptureArea __init__ source.
    init = class_method(_capture_area_node(), "__init__")

    # Then: it builds no Tk image. A PhotoImage is a Tcl-side object the DIB
    # never touches, and it pins one pixel buffer per display size.
    assert "PhotoImage" not in referenced_names(init)

    # Then: and the widget is still created with the two options the Frame keeps,
    # so the Tk geometry and the child HWND client area stay in step.
    widget_options = {
        keyword.arg: _widget_option_value(keyword.value)
        for call in ast.walk(init)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "__init__"
        for keyword in call.keywords
        if keyword.arg is not None
    }
    assert widget_options.get("cursor") == "tcross", (
        f"__init__ must keep cursor='tcross'; got {widget_options.get('cursor')!r}"
    )
    assert widget_options.get("borderwidth") in {0, "bd"}, (
        "the Frame must keep a zero border width so the HWND client area equals "
        f"winfo_width; got {widget_options.get('borderwidth')!r}"
    )


def test_capture_area_no_longer_carries_the_photo_image_item_attributes() -> None:
    # Given: the parsed CaptureArea class body.
    node = _capture_area_node()

    # Then: _photo, im and im_ are gone from the class entirely. _photo is what
    # the measurement harness monkeypatches today, and im / im_ are the canvas
    # image-item bookkeeping that only existed to re-point the item.
    attributes = attribute_attr_names(node)
    assert not (attributes & PHOTO_IMAGE_ATTRIBUTES), (
        "CaptureArea still carries the PhotoImage/canvas-item attributes: "
        f"{sorted(attributes & PHOTO_IMAGE_ATTRIBUTES)}"
    )

    # Then: and none of them survives as a class-level default either.
    class_level: set[str] = set()
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            class_level.add(child.target.id)
        if isinstance(child, ast.Assign):
            class_level.update(
                target.id for target in child.targets if isinstance(target, ast.Name)
            )
    assert not (class_level & PHOTO_IMAGE_ATTRIBUTES)


def test_load_disabled_image_returns_a_bgr_array_of_the_show_size() -> None:
    # Given: a bare area at a known show size, with no Tk interpreter.
    bare = bare_capture_area(width=SHOW_WIDTH, height=SHOW_HEIGHT)

    # When: the disabled image is loaded.
    disabled = getattr(bare.area, "_loadDisabledImage")()

    # Then: it is a BGR ndarray of the show size, because _showDisabled now
    # composites pixels instead of swapping a Tk image reference.
    assert isinstance(disabled, np.ndarray)
    assert disabled.dtype == np.uint8
    assert disabled.shape == (SHOW_HEIGHT, SHOW_WIDTH, 3)

    # Then: and it is a real decode of Images/disabled.png, not the black
    # placeholder the type check would also accept, so the loader provably ran.
    assert disabled.any(), "the disabled image is entirely zero; nothing was decoded"


# ===========================================================================
# D2. The overlay is the source of truth and the present path is compose+present
# ===========================================================================


def test_capture_area_exposes_the_documented_overlay_surface_and_buffer_state() -> None:
    # Given: the parsed CaptureArea class body.
    assigned = assigned_attribute_names(_capture_area_node())

    # Then: the four OverlayState components the design names are initialised by
    # the class, so the overlay is data rather than canvas items.
    for name in ("_stick_left", "_stick_right", "_guide", "_img_rect"):
        assert name in assigned, f"__init__ never assigns self.{name}"

    # Then: the renderer that owns the present path is held as one reference, so
    # compose / present / resize / release all go through a single owner.
    assert "_surface" in assigned, "CaptureArea holds no self._surface"
    assert "_disabled" in assigned, (
        "the disabled image must be held as a BGR array, not a Tk image"
    )

    # Then: and the filter/correction work buffers survive, because _convert still
    # has to hand the renderer a BGR buffer when either is active.
    for name in ("_filter_buf", "_correct_buf"):
        assert name in assigned, f"__init__ never allocates self.{name}"


def test_default_constructed_overlay_is_the_all_default_overlay_state() -> None:
    # Given: a bare area whose four components carry the documented defaults.
    from core import preview_renderer

    bare = bare_capture_area()

    # Then: the overlay accessor assembles them into an all-default OverlayState,
    # i.e. zero shapes, so nothing is drawn until something is active.
    overlay = bare.area.overlay
    assert overlay == preview_renderer.OverlayState()
    assert overlay.left_stick == preview_renderer.StickState()
    assert overlay.right_stick == preview_renderer.StickState()
    assert overlay.guide == preview_renderer.RectState()
    assert overlay.img_rect == preview_renderer.ImgRectState()
    assert overlay.left_stick.active is False
    assert overlay.guide.visible is False
    assert overlay.img_rect.visible is False


def test_draw_frame_composes_then_presents_and_issues_no_other_drawing_call() -> None:
    # Given: a bare area with a recording surface and an all-default overlay.
    bare = bare_capture_area()
    frame = bgr_frame()
    expected_overlay = bare.area.overlay

    # When: one frame is drawn.
    getattr(bare.area, "_drawFrame")(frame, 1)

    # Then: the present path is exactly compose-then-present, with the frame and
    # the area's current overlay handed to compose.
    assert bare.surface.names == ["compose", "present"]
    composed_frame, composed_overlay = bare.surface.composes[0]
    assert composed_frame is frame
    assert composed_overlay == expected_overlay
    assert bare.surface.presents == 1

    # Then: and the widget was never asked to draw anything.
    assert bare.widget.names == []


def test_draw_frame_skips_the_same_seq_and_resumes_on_a_new_one() -> None:
    # Given: a bare area with a recording surface.
    bare = bare_capture_area()
    frame = bgr_frame()

    # When: the same sequence number is drawn twice.
    getattr(bare.area, "_drawFrame")(frame, 7)
    getattr(bare.area, "_drawFrame")(frame, 7)

    # Then: the second draw composes nothing, so a 5-10 Hz source that repeats a
    # sequence cannot inflate the measured present cadence.
    assert len(bare.surface.composes) == 1
    assert bare.surface.presents == 1

    # When: a new sequence number arrives.
    getattr(bare.area, "_drawFrame")(frame, 8)

    # Then: it composes and presents again.
    assert len(bare.surface.composes) == 2
    assert bare.surface.presents == 2


def test_draw_frame_takes_the_disabled_path_for_a_none_frame() -> None:
    # Given: a bare area whose disabled path and _convert are both spied, so the
    # real work still runs and only entry is observed.
    bare = bare_capture_area()
    disabled_entries = spy(bare.area, "_showDisabled")
    convert_entries = spy(bare.area, "_convert")

    # When: no frame is available.
    getattr(bare.area, "_drawFrame")(None, 5)

    # Then: the disabled path runs, and it runs once.
    assert len(disabled_entries) == 1

    # Then: and a missing frame never reaches the present path as a frame, nor
    # through the filter chain that would have to reshape it.
    assert all(composed is not None for composed in bare.surface.composed_frames())
    assert convert_entries == []


# ===========================================================================
# D4. Cursor and event bindings live on the Frame
# ===========================================================================


def test_stick_press_sets_the_dot_cursor_and_release_restores_tcross() -> None:
    # Given: a bare area per side, with a recording widget.
    for side, press_name, release_name in (
        ("L", "mouseLeftPress", "mouseLeftRelease"),
        ("R", "mouseRightPress", "mouseRightRelease"),
    ):
        bare = bare_capture_area()

        # When: the stick is pressed and then released.
        getattr(bare.area, press_name)(PointerEvent(x=3, y=4), None)
        getattr(bare.area, release_name)(None)

        # Then: the cursor goes crosshair -> dot -> crosshair, because the cursor
        # under the pointer comes from the window being hit-tested and that is now
        # the Frame rather than the canvas.
        assert bare.widget.cursor_values() == ["dot", "tcross"], side


def test_bind_left_click_binds_on_the_frame_itself() -> None:
    # Given: a bare area with a recording widget.
    bare = bare_capture_area()

    # When: the left drag is assigned to the left stick.
    getattr(bare.area, "BindLeftClick")()

    # Then: the three left-drag sequences are bound on the widget, which is now
    # the Frame: the child HWND is disabled, so the Frame receives the events.
    assert bare.widget.bound_sequences() == [
        "<ButtonPress-1>",
        "<Button1-Motion>",
        "<ButtonRelease-1>",
    ]


def test_unbind_left_click_unbinds_from_the_frame_itself() -> None:
    # Given: a bare area that has bound, pressed and released the left drag.
    bare = bare_capture_area()
    getattr(bare.area, "BindLeftClick")()
    getattr(bare.area, "mouseLeftPress")(PointerEvent(x=3, y=4), None)
    getattr(bare.area, "mouseLeftRelease")(None)
    bare.widget.unbind_calls.clear()

    # When: the left drag assignment is removed.
    getattr(bare.area, "UnbindLeftClick")()

    # Then: the same three sequences are unbound from the widget.
    assert bare.widget.unbind_calls == [
        "<ButtonPress-1>",
        "<Button1-Motion>",
        "<ButtonRelease-1>",
    ]


def test_bind_and_unbind_click_keep_their_signatures() -> None:
    # Given: the real class.
    capture_area = _capture_area_class()

    # When: the four bind/unbind entry points are inspected.
    signatures = {
        name: tuple(inspect.signature(getattr(capture_area, name)).parameters)
        for name in (
            "BindLeftClick",
            "UnbindLeftClick",
            "BindRightClick",
            "UnbindRightClick",
        )
    }

    # Then: they still take nothing but self, so every existing caller in
    # CaptureArea, CaptureAreaProxy and the harness keeps working unchanged.
    for name, parameters in signatures.items():
        assert parameters == ("self",), f"{name} changed signature: {parameters}"


def test_configure_handler_resizes_the_surface_with_the_new_client_size() -> None:
    # Given: a bare area built by __new__, so no Tk event loop ever bound
    # anything and the handler has to be reached by name.
    bare = bare_capture_area()
    init = class_method(_capture_area_node(), "__init__")
    configured_sequences = _bind_sequences_in(init)

    # Then: <Configure> is still bound, because Windows moves children with
    # the parent but does not resize them, so the handler is mandatory.
    assert "<Configure>" in configured_sequences, (
        f"__init__ binds {configured_sequences}; <Configure> is mandatory"
    )

    # Then: and it is bound to the named handler, so the binding cannot be
    # dropped while the method survives, and the method stays reachable from a
    # __new__-built instance.
    assert _bind_targets_in(init).get("<Configure>") == "self._onConfigure", (
        f"__init__ binds <Configure> to {_bind_targets_in(init).get('<Configure>')!r}"
    )

    # When: the Frame is resized by its geometry manager.
    getattr(bare.area, "_onConfigure")(PointerEvent(width=1280, height=720))

    # Then: the surface is resized to the new client size and nothing else is
    # touched -- the child HWND is placed by the renderer, not by Tk.
    assert bare.surface.names == ["resize"]
    assert bare.surface.resizes == [(1280, 720)]


# ===========================================================================
# D5. setShowsize
# ===========================================================================


def test_constructor_signature_keeps_the_positional_order_and_adds_a_renderer() -> None:
    # Given: the real class.
    capture_area = _capture_area_class()

    # When: the constructor is inspected.
    parameters = list(inspect.signature(capture_area.__init__).parameters.values())

    # Then: the parameter order and defaults are still exactly what they were for
    # every existing parameter, because RealCaptureHarness.start and
    # _run_teardown_probe both pass ser positionally. The renderer-selection seam
    # (tests/test_renderer_selection.py) supersedes the earlier "unchanged" pin by
    # appending one trailing keyword; the order it appended at is now part of the
    # contract, so the list below is that signature, not a stale copy of it.
    assert [parameter.name for parameter in parameters] == [
        "self",
        "camera",
        "fps",
        "is_show",
        "ser",
        "master",
        "show_width",
        "show_height",
        "take_stick_log",
        "renderer",
    ]
    assert [parameter.default for parameter in parameters[1:5]] == [
        inspect.Parameter.empty
    ] * 4
    assert parameters[5].default is None
    assert parameters[6].default == 640
    assert parameters[7].default == 360
    assert parameters[8].default is None

    # Then: and the appended keyword defaults to the platform's own choice, so
    # every caller written before the seam existed still gets GDI on Windows.
    assert parameters[9].default == "auto"

    # Then: and all of them stay positionally callable, which the harness relies on.
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters
    )


def test_set_show_size_updates_widget_buffers_and_surface() -> None:
    # Given: a bare area at 8x8 with a recording surface.
    bare = bare_capture_area()
    assert _frame_shaped_arrays(bare.area), "the bare area has no work buffers"

    # When: the show size is changed. The parameter order is (height, width) and
    # is deliberately asymmetric; swapping it is the easy mistake here.
    getattr(bare.area, "setShowsize")(480, 800)

    # Then: the show geometry, the widget configuration and the surface all follow.
    assert bare.area.show_height == 480
    assert bare.area.show_width == 800
    assert bare.area.show_size == (800, 480)
    assert any(
        call.get("width") == 800 and call.get("height") == 480
        for call in bare.widget.config_calls
    ), bare.widget.config_calls
    assert bare.surface.resizes == [(800, 480)]

    # Then: and every work buffer was reallocated at the new shape.
    assert {array.shape for array in _frame_shaped_arrays(bare.area)} == {(480, 800, 3)}


def test_set_show_size_creates_no_photo_image_and_no_disabled_tk() -> None:
    # Given: the parsed setShowsize source.
    show_size = class_method(_capture_area_node(), "setShowsize")

    # Then: it neither rebuilds a Tk image nor re-derives one under the old name.
    assert "PhotoImage" not in referenced_names(show_size)
    assert "disabled_tk" not in referenced_names(show_size), (
        "setShowsize still assigns the disabled_tk Tk image attribute"
    )
    assert not (call_attr_names(show_size) & CANVAS_ITEM_API)

    # Then: and at runtime the instance carries no disabled_tk attribute at all.
    bare = bare_capture_area()
    getattr(bare.area, "setShowsize")(480, 800)
    assert "disabled_tk" not in vars(bare.area)


# ===========================================================================
# D6. _convert neither resizes nor swaps the channel order
# ===========================================================================


def test_convert_neither_resizes_nor_swaps_the_channel_order() -> None:
    # Given: the parsed _convert source.
    convert = class_method(_capture_area_node(), "_convert")

    # Then: it does not resize, because the resize moved camera-side and the
    # present path's 1:1 check must be able to fail loudly instead of scaling.
    assert "resize" not in call_attr_names(convert), (
        "_convert still resizes; the design moved that to the camera side"
    )

    # Then: and it does not convert BGR to RGB, because the DIB takes BGR
    # directly and COLOR_BGR2BGRA keeps the order.
    assert "COLOR_BGR2RGB" not in referenced_names(convert)


def test_draw_frame_skips_convert_entirely_without_a_filter_or_correction() -> None:
    # Given: a bare area with no filter and no correction, and a _convert spy.
    bare = bare_capture_area()
    bare.area._filter_enabled = False
    bare.area._correction_active = False
    convert_entries = spy(bare.area, "_convert")
    frame = bgr_frame()

    # When: a frame is drawn.
    getattr(bare.area, "_drawFrame")(frame, 1)

    # Then: _convert is not called at all and the frame reaches compose as the very
    # same object, so the no-filter path allocates nothing per frame.
    assert convert_entries == []
    assert bare.surface.composed_frames()[0] is frame


def test_a_filtered_frame_reaches_compose_as_the_filters_bgr_output() -> None:
    # Given: a bare area with a narrow gray_out HSV filter active.
    import cv2
    from core import preview_filter

    bare = bare_capture_area()
    lower, upper = preview_filter.validate_hsv(list(FILTER_LOWER), list(FILTER_UPPER))
    bare.area._filter_enabled = True
    bare.area._filter_lower = lower
    bare.area._filter_upper = upper
    bare.area._filter_mode = "gray_out"
    frame = filter_probe_frame()

    # Then: the fixture really does hold pixels inside and outside the window, so
    # the BGR assertion below cannot pass because the filter was a no-op.
    selected = int((preview_filter.hsv_mask(frame, lower, upper) > 0).sum())
    assert 0 < selected < mask_pixels(frame), (
        f"{selected} of {mask_pixels(frame)} pixels are in the window; the fixture "
        "needs both an in-mask and an out-of-mask region"
    )

    # When: a frame is drawn.
    getattr(bare.area, "_drawFrame")(frame, 1)

    # Then: the buffer the filter produced is what reaches compose, unchanged.
    expected = preview_filter.apply_filter(frame, lower, upper, "gray_out")
    composed = bare.surface.composed_frames()[0]
    assert np.array_equal(composed, expected)
    assert composed.shape == (SHOW_HEIGHT, SHOW_WIDTH, 3)

    # Then: and it is BGR, not the RGB round trip. The saturated half survives
    # apply_filter with its channels intact, so the two orders differ.
    assert not np.array_equal(composed, cv2.cvtColor(expected, cv2.COLOR_BGR2RGB))


def test_a_corrected_frame_reaches_compose_as_bgr_not_rgb() -> None:
    # Given: a bare area with a non-neutral colour correction and no filter.
    import cv2
    from core import preview_filter

    bare = bare_capture_area()
    correction = preview_filter.validate_correction(NON_NEUTRAL_CORRECTION)
    assert preview_filter.is_correction_neutral(correction) is False
    bare.area._filter_enabled = False
    bare.area._correction = correction
    bare.area._correction_active = True
    frame = bgr_frame()

    # When: a frame is drawn.
    getattr(bare.area, "_drawFrame")(frame, 1)

    # Then: the correction's BGR output is what reaches compose, and it is not the
    # RGB round trip: the hue shift is channel-order sensitive, so a stray
    # COLOR_BGR2RGB anywhere on this path would show up here.
    expected = preview_filter.apply_correction(frame, correction)
    composed = bare.surface.composed_frames()[0]
    assert np.array_equal(composed, expected)
    assert not np.array_equal(composed, cv2.cvtColor(expected, cv2.COLOR_BGR2RGB))


# ===========================================================================
# Gating property: CaptureArea's own dependence on PIL/ImageTk is gone
# ===========================================================================


def test_capture_area_class_body_keeps_no_pil_image_dependency() -> None:
    # Given: the parsed CaptureArea class body.
    node = _capture_area_node()

    # Then: the class reaches for no PIL symbol at all. Scoped to the class on
    # purpose: design section 8 keeps an ImageTk.PhotoImage based
    # PhotoImageSurface for os.name != "nt", so a module-level PIL import may
    # legitimately remain for that fallback. What must not remain is
    # CaptureArea's own dependence on a Tk image, and a local import inside the
    # class body is exactly the signal that it still has one.
    names = referenced_names(node)
    assert not (names & PIL_SYMBOLS), (
        f"CaptureArea still references {sorted(names & PIL_SYMBOLS)}"
    )

    # Then: and it imports no PIL symbol inside its own body either.
    imported: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in child.names)
        elif isinstance(child, ast.ImportFrom):
            imported.add((child.module or "").split(".")[0])
            imported.update(alias.name for alias in child.names)
    assert not (imported & PIL_SYMBOLS), (
        f"CaptureArea imports {sorted(imported & PIL_SYMBOLS)} inside its own body"
    )


# ===========================================================================
# Gating property: the teardown gains a surface_released phase (design 11)
# ===========================================================================


def test_window_exit_releases_the_surface_between_preview_and_services() -> None:
    # Given: the real Window._continue_exit source.
    continue_exit = class_method(
        class_node(module_tree(WINDOW_SOURCE), "PokeControllerApp"), "_continue_exit"
    )
    markers = _teardown_markers(continue_exit)

    # Then: the phase order is exactly preview_stopped -> surface_released ->
    # stopping_services, with nothing between them. release() has to run after
    # stopCapture() is the only point at which no further _dispatch_one_tick can
    # compose, and before the services go down, because lpvBits points into the
    # frame array and nothing after the release may compose.
    phases = [value for kind, value in markers if kind == "phase"]
    assert phases[:3] == ["preview_stopped", "surface_released", "stopping_services"]
    assert phases.count("surface_released") == 1

    # Then: release is actually called in that phase, not merely labelled.
    release_positions = [
        index for index, (kind, _value) in enumerate(markers) if kind == "release"
    ]
    assert release_positions, "_continue_exit never calls the surface's release()"
    phase_positions = [
        index for index, (kind, _value) in enumerate(markers) if kind == "phase"
    ]
    surface_phase = phases.index("surface_released")
    released_phase = phase_positions.index(surface_phase)
    assert min(release_positions) > released_phase, (
        "release() runs before the surface_released phase, so the frame reference "
        "is not dropped before the child window is destroyed"
    )

    # Then: and release runs before the root is destroyed, so the child HWND and
    # its DIB are still owned by a live Tk at teardown time.
    destroy_positions = [
        index for index, (kind, _value) in enumerate(markers) if kind == "destroy"
    ]
    assert destroy_positions, "_continue_exit never destroys the root"
    assert max(release_positions) < min(destroy_positions)


# ===========================================================================
# H. A failed render must be observable
# ===========================================================================
#
# ``GuiAssets.py:627-628`` calls ``self._surface.compose(...)`` and
# ``self._surface.present()`` and discards both ``RenderResult``s.
# ``_dispatch_tick`` then counts the tick in ``_stat_shown`` whatever the
# outcome (``GuiAssets.py:486``), and nothing in the tree calls
# ``surface.self_test()`` -- ``GdiSurface`` runs it inside ``attach``
# (``gdi_surface.py:698``) and only writes to the file log when it *fails*
# (``gdi_surface.py:929``). A run where every single blit is refused therefore
# reports healthy fps, draws nothing, and logs nothing, which is the reported
# symptom: "preview shows no video".
#
# The design already mandates the counter these tests name
# (``design.md:358,508``); it was never implemented. Each test below is RED for
# its own reason and says which sub-assertion is unmet, so the fix is told what
# is missing rather than merely that something is.


#: The counter the design mandates for a frame refused by the 1:1 check
#: (``design.md:508``). Named here, not invented here: the design is the SSOT
#: for what the field is called.
DISCARD_COUNTER = "frames_discarded_dimension_mismatch"


class _RefusingSurface:
    """A local ``PreviewRenderer`` double that refuses every render.

    The shared ``RecordingSurface`` always returns ``ok=True``
    (``gdi_present_doubles.py:180``), so a contract about a *failed* render
    cannot be expressed with it. Mutating the shared double is not an option
    either: two other contract modules build on it, and a class-level default
    that returns ``ok=False`` would silently invalidate their "presented"
    assertions. So the double is local to this module.

    The two failure details are the pair a real size mismatch produces:
    ``GdiSurface.compose`` returns ``dimension_mismatch`` *without* setting
    ``_pending_frame`` (``gdi_surface.py:722``), so the ``present()`` that
    follows finds nothing staged and returns ``no_frame``
    (``gdi_surface.py:735``). Modelling that exactly is what makes the symptom
    reproducible: every tick issues both calls, neither puts a pixel on screen.
    """

    def __init__(self, detail: str = "dimension_mismatch") -> None:
        self.detail = detail
        self.calls: list[str] = []
        self.composes: list[tuple[Any, Any]] = []
        self.results: list[tuple[str, Any]] = []
        self.attach_calls: list[tuple[int, tuple[int, int]]] = []
        self.resizes: list[tuple[int, int]] = []
        self.releases = 0
        self._client_size = (0, 0)

    def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None:
        self.calls.append("attach")
        self.attach_calls.append((parent_hwnd, (size[0], size[1])))

    def resize(self, size: tuple[int, int]) -> None:
        self.calls.append("resize")
        self.resizes.append((size[0], size[1]))

    def compose(self, frame: Any, overlay: Any) -> Any:
        from core import preview_renderer

        self.calls.append("compose")
        self.composes.append((frame, overlay))
        result = preview_renderer.RenderResult(
            ok=False, elapsed_ns=0, detail=self.detail
        )
        self.results.append(("compose", result))
        return result

    def present(self) -> Any:
        from core import preview_renderer

        self.calls.append("present")
        result = preview_renderer.RenderResult(
            ok=False, elapsed_ns=0, detail="no_frame"
        )
        self.results.append(("present", result))
        return result

    def release(self) -> None:
        self.calls.append("release")
        self.releases += 1

    def client_size(self) -> tuple[int, int]:
        return self._client_size

    # -- test-only inspection --------------------------------------------
    def successful_presents(self) -> int:
        return sum(
            1 for name, result in self.results if name == "present" and result.ok
        )


@dataclass(frozen=True, slots=True)
class _RefusingBare:
    """A bare area plus the double installed in place of its surface."""

    area: Any
    surface: _RefusingSurface
    camera: Any


def _failing_bare(detail: str = "dimension_mismatch") -> _RefusingBare:
    """A bare area whose installed surface refuses every render.

    The refusing surface is handed back beside the area rather than written into
    ``BareArea.surface``: that field is typed as the shared ``RecordingSurface``,
    so overwriting it with a different double is a lie the type checker is right
    to reject -- and the area's own ``_surface`` is what production reads anyway.
    """
    bare = bare_capture_area()
    surface = _RefusingSurface(detail)
    bare.area._surface = surface
    _seed_runtime_state(bare.area)
    return _RefusingBare(area=bare.area, surface=surface, camera=bare.camera)


def _seed_runtime_state(area: Any) -> None:
    """Open a fresh ``getStats()`` period and seed the camera observation dict.

    ``bare_capture_area()`` seeds the present-path state; both of these are
    ``__init__``-only state (``GuiAssets.py:344-354`` and ``:366-371``), so a
    headless ``__new__``-built area has to be given them before ``_dispatch_tick``
    can run at all. The keys are spelled out rather than derived from the parsed
    ``__init__``: if one were ever dropped upstream, ``_readLatest`` would raise
    and ``_dispatch_tick`` would swallow it (``GuiAssets.py:499``), so the
    preconditions in the tests below -- which count the calls that did reach the
    surface -- are what make that drift loud instead of silent.
    """
    area._stat_shown = 0
    area._stat_began_at = None
    area._stat_draw_ms = 0.0
    area._stat_draw_max_ms = 0.0
    area._camera_observation = {
        "camera_read_count": 0,
        "camera_frame_present_count": 0,
        "camera_none_count": 0,
        "camera_read_error_count": 0,
        "camera_unique_sequence_count": 0,
        "camera_duplicate_sequence_count": 0,
        "camera_sequence_regression_count": 0,
        "camera_last_sequence": 0,
        "post_teardown_camera_read_count": 0,
    }


@contextmanager
def _captured_log() -> Iterator[list[str]]:
    """Collect INFO-and-above loguru messages, then detach the sink.

    ``record["message"]`` rather than ``str(message)``: the string form carries a
    timestamp and a ``module:function:line`` prefix, and the fix under test is
    about a specific detail string surviving into the log.
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


def _failure_counter(area: Any, name: str) -> int | None:
    """``area``'s counter called ``name``, or None when it exposes no such count.

    The design names the counter; it does not say where the count is kept, so a
    plain instance attribute and a one-level mapping both satisfy it. These
    tests pin the count, not the container. Booleans are rejected on purpose:
    ``isinstance(True, int)`` is true in Python and a flag is not a count.
    """
    direct = getattr(area, name, None)
    if isinstance(direct, int) and not isinstance(direct, bool):
        return direct
    for value in vars(area).values():
        if not isinstance(value, dict):
            continue
        nested = value.get(name)
        if isinstance(nested, int) and not isinstance(nested, bool):
            return nested
    return None


def _call_line_of(node: ast.AST, attr: str) -> int | None:
    """The source line of the first ``x.attr(...)`` call, or None when absent."""
    lines = [
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == attr
    ]
    return min(lines) if lines else None


def _drive_ticks(area: Any, camera: Any, ticks: int) -> None:
    """Run ``_dispatch_tick`` ``ticks`` times, each with a fresh sequence.

    Distinct sequence numbers are mandatory: ``_drawFrame`` skips a repeated
    ``seq`` (``GuiAssets.py:620``), so a reused sequence would measure the
    dedup path instead of the present path.
    """
    for index in range(1, ticks + 1):
        camera.frame = bgr_frame()
        camera.sequence = index
        getattr(area, "_dispatch_tick")()


def test_a_refused_compose_is_counted_and_its_detail_is_logged() -> None:
    # Given: a bare area whose surface refuses every render with the detail the
    # 1:1 check produces.
    failing = _failing_bare()
    area, surface = failing.area, failing.surface
    frame = bgr_frame()

    # When: one frame is drawn.
    with _captured_log() as messages:
        getattr(area, "_drawFrame")(frame, 1)

    # Then: the fixture really did refuse the frame, so the assertions below
    # cannot pass because the double accidentally succeeded.
    assert surface.calls == ["compose", "present"], surface.calls
    assert surface.results[0][1].ok is False
    assert surface.results[0][1].detail == "dimension_mismatch"

    # Then: (a1) the refusal is counted. The design mandates
    # frames_discarded_dimension_mismatch (design.md:508) precisely so a size
    # mismatch cannot pass silently, and today _drawFrame drops both
    # RenderResults on the floor.
    counter = _failure_counter(area, DISCARD_COUNTER)
    assert counter == 1, (
        "(a1) UNMET: compose() returned ok=False detail='dimension_mismatch' but "
        f"CaptureArea recorded no failure count. The design mandates {DISCARD_COUNTER!r} "
        f"(design.md:508); counter value is {counter!r} and the area exposes no such "
        f"counter among {sorted(vars(area))}."
    )

    # Then: (a2) and the detail reaches the log, because a counter nobody reads
    # and a healthy fps is the same invisible failure with extra bookkeeping.
    assert any("dimension_mismatch" in message for message in messages), (
        "(a2) UNMET: the frame was discarded with detail='dimension_mismatch' and "
        f"nothing was logged. Captured messages: {messages!r}."
    )


def test_the_shown_statistic_counts_successful_presents_not_attempted_ones() -> None:
    ticks = 5

    # Given: a control run against the shared all-ok surface, so the failing run
    # below cannot report fps 0.0 merely because the statistic is dead.
    control = bare_capture_area()
    _seed_runtime_state(control.area)
    _drive_ticks(control.area, control.camera, ticks)
    control_stats = control.area.getStats()
    assert control.surface.presents == ticks, control.surface.names
    assert control_stats["fps"] > 0.0, (
        "control UNMET: a run of "
        f"{ticks} successful presents reported {control_stats!r}, so the "
        "measurement path is broken and the assertion below proves nothing"
    )

    # When: the same number of ticks against a surface that refuses everything.
    failing = _failing_bare()
    area, surface = failing.area, failing.surface
    with _captured_log() as messages:
        _drive_ticks(area, failing.camera, ticks)

    # Then: every tick really reached the present path and none of them landed,
    # which is what makes a reported fps above zero a lie rather than noise.
    assert surface.calls == ["compose", "present"] * ticks, surface.calls
    assert surface.successful_presents() == 0, surface.results
    assert all(not result.ok for _name, result in surface.results), surface.results

    # Then: (b) the reported statistic counts successful presents, so a run that
    # blitted nothing reports fps 0. _dispatch_tick increments _stat_shown
    # unconditionally (GuiAssets.py:486), which is why "no video" and "healthy
    # fps" are the same observation today.
    stats = area.getStats()
    assert stats["fps"] == 0.0, (
        "(b) UNMET: a run of "
        f"{ticks} ticks with 0 successful blits reported {stats!r}. The shown "
        "statistic counts attempted presents, so a preview that drew nothing "
        f"still looks healthy. Captured messages: {messages!r}."
    )


def test_attach_surfaces_the_surface_self_test_outcome() -> None:
    # Given: the real __init__ source. A real tk.Frame cannot be constructed
    # without a display and __init__ is the only place attach() is called, so
    # this contract is read from the parsed file -- the module's stated
    # alternative to a live instance.
    init = class_method(_capture_area_node(), "__init__")
    calls = call_attr_names(init)
    attach_line = _call_line_of(init, "attach")
    selftest_line = _call_line_of(init, "_schedule_selftest")

    # Then: (c1) attach reads the self test, and reads it after the attach that
    # creates the child HWND. GdiSurface runs the self test inside attach
    # (gdi_surface.py:698), so a read placed before it can only ever observe the
    # "not_run" default the surface is constructed with.
    assert selftest_line is not None, (
        "(c1) UNMET: __init__ never schedules the self test on the surface, so "
        "the startup self test's outcome is unobservable from the widget -- a "
        f"run whose child window is covered or clipped says nothing. __init__ "
        f"calls {sorted(calls)}."
    )
    assert attach_line is not None and selftest_line > attach_line, (
        "(c1) UNMET: _schedule_selftest() is called at line "
        f"{selftest_line} but attach() is at line {attach_line}. attach creates "
        "the child HWND and runs the self test, so scheduling it earlier only "
        "ever sees the 'not_run' default."
    )

    # Then: (c2) and the outcome is neither logged nor kept, which makes the
    # read pointless: GdiSurface's own warning (gdi_surface.py:929) is the only
    # trace, and it exists only for the failing branch.
    run_selftest = class_method(_capture_area_node(), "_run_selftest")
    run_calls = call_attr_names(run_selftest)
    run_assigned = assigned_attribute_names(run_selftest)
    assert "logger" in run_calls or any(
        "selftest" in name.lower() for name in run_assigned
    ), (
        "(c2) UNMET: the self_test() outcome is read and then thrown away. It "
        "must be logged or kept on the instance; _run_selftest() calls "
        f"{sorted(run_calls)} and assigns {sorted(run_assigned)}."
    )


class _SelfTestSurface:
    """The one seam the poll reads: ``self_test()`` answering in order.

    The last answer repeats, so a poll that should have stopped keeps asking
    and the test can say so instead of hanging on a short list.
    """

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls = 0

    def self_test(self) -> Any:
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer


class _DestroyEvent:
    """A ``<Destroy>`` event carrying only the field the cancel path reads."""

    def __init__(self, widget: Any) -> None:
        self.widget = widget


def test_the_self_test_poll_stops_the_moment_the_surface_answers() -> None:
    # Given: a surface that can answer, and a widget double that records after.
    from ui.photo_surface import SelfTestResult

    bare = bare_capture_area()
    area = bare.area
    area._surface = _SelfTestSurface(
        SelfTestResult("visible", False, (0, 0, SHOW_WIDTH, SHOW_HEIGHT))
    )

    # When: the startup poll runs.
    area._schedule_selftest()

    # Then: a settled verdict ends the polling there and then -- arming a
    # retry would leave the widget's after queue holding a callback that
    # re-reads an answer that has not changed.
    assert area.surface_selftest.outcome == "visible", area.surface_selftest
    assert bare.widget.after_calls == [], bare.widget.after_calls


def test_the_self_test_poll_retries_while_the_surface_stays_unmapped() -> None:
    # Given: a surface that reports "not mapped" once and a real answer after.
    from ui.photo_surface import SelfTestResult

    bare = bare_capture_area()
    area = bare.area
    area._surface = _SelfTestSurface(
        SelfTestResult("not_mapped", True, (0, 0, 0, 0)),
        SelfTestResult("visible", False, (0, 0, SHOW_WIDTH, SHOW_HEIGHT)),
    )

    # When: the startup poll runs before the window is mapped.
    area._schedule_selftest()

    # Then: "not mapped" is not a verdict. Host visibility is the wrong thing
    # to key on -- the pack that maps the Canvas runs in an idle handler, so
    # the host can be viewable while the Canvas still answers "not mapped",
    # and a single probe would freeze that misreading into the record.
    assert area.surface_selftest.outcome == "not_mapped", area.surface_selftest
    assert len(bare.widget.after_calls) == 1, bare.widget.after_calls
    delay_ms, _callback = bare.widget.after_calls[0]
    assert delay_ms == _capture_area_class()._SELFTEST_POLL_MS, delay_ms

    # When: the armed retry runs once the window is up.
    bare.widget.after_calls[0][1]()

    # Then: the placeholder is replaced by the real answer and the polling
    # stops -- the retry is not rearmed.
    assert area.surface_selftest.outcome == "visible", area.surface_selftest
    assert len(bare.widget.after_calls) == 1, bare.widget.after_calls


def test_the_self_test_poll_retries_while_the_surface_reports_not_run() -> None:
    # Given: a surface whose first probe ran before its window existed (the
    # attach-time BitBlt can fail outright) and a real answer after.
    from ui.photo_surface import SelfTestResult

    bare = bare_capture_area()
    area = bare.area
    area._surface = _SelfTestSurface(
        SelfTestResult("not_run", False, (0, 0, 0, 0)),
        SelfTestResult("covered", False, (0, 0, SHOW_WIDTH, SHOW_HEIGHT)),
    )

    # When: the startup poll runs before the surface can run its test.
    area._schedule_selftest()

    # Then: "not_run" is not a verdict either. __init__ schedules the poll
    # before pack, so a surface whose startup probe fails there would be
    # frozen into the record as "never ran" while the window goes on to
    # render normally -- exactly the record the E2E then rejects.
    assert area.surface_selftest.outcome == "not_run", area.surface_selftest
    assert len(bare.widget.after_calls) == 1, bare.widget.after_calls
    delay_ms, _callback = bare.widget.after_calls[0]
    assert delay_ms == _capture_area_class()._SELFTEST_POLL_MS, delay_ms

    # When: the armed retry runs once the surface can answer.
    bare.widget.after_calls[0][1]()

    # Then: the placeholder is replaced by the real answer and the polling
    # stops -- the retry is not rearmed.
    assert area.surface_selftest.outcome == "covered", area.surface_selftest
    assert len(bare.widget.after_calls) == 1, bare.widget.after_calls


def test_the_self_test_poll_gives_up_at_its_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a surface that never gets mapped, and a clock that steps past the
    # deadline between arming and probing.
    from ui.photo_surface import SelfTestResult

    bare = bare_capture_area()
    area = bare.area
    area._surface = _SelfTestSurface(SelfTestResult("not_mapped", True, (0, 0, 0, 0)))
    clock = iter([1000.0, 1004.0])
    monkeypatch.setattr(_gui_assets().time, "monotonic", lambda: next(clock, 1004.0))

    # When: the startup poll runs.
    area._schedule_selftest()

    # Then: the deadline turns an unanswered poll into a recorded verdict
    # rather than an endless queue of 50 ms retries.
    assert area.surface_selftest.outcome == "not_mapped", area.surface_selftest
    assert bare.widget.after_calls == [], bare.widget.after_calls


def test_the_self_test_poll_records_a_surface_without_a_self_test() -> None:
    # Given: a surface carrying no self_test at all, which RecordingSurface
    # is -- the same shape a renderer without the method would have.
    bare = bare_capture_area()
    area = bare.area
    assert not hasattr(bare.surface, "self_test"), dir(bare.surface)

    # When: the startup poll runs.
    area._schedule_selftest()

    # Then: None is recorded and the polling ends at once. None means "no such
    # method" and is distinct from the "pending" placeholder, so a reader can
    # tell a surface that cannot answer from one that has not answered yet.
    assert area.surface_selftest is None, area.surface_selftest
    assert bare.widget.after_calls == [], bare.widget.after_calls


def test_a_destroy_cancels_the_armed_self_test_after_and_a_child_does_not() -> None:
    # Given: the wiring, read from the parsed __init__ because a bare area
    # never runs it -- the binding is the half a runtime test would miss.
    init = class_method(_capture_area_node(), "__init__")
    assert _bind_targets_in(init).get("<Destroy>") == "self._cancelSelftest", (
        f"<Destroy> is bound to {_bind_targets_in(init).get('<Destroy>')!r}, so a "
        "widget destroyed while a retry is armed keeps the callback, and Tk "
        "answers it with 'invalid command name'"
    )

    # Given: a poll that has armed exactly one retry.
    from ui.photo_surface import SelfTestResult

    bare = bare_capture_area()
    area = bare.area
    area._surface = _SelfTestSurface(SelfTestResult("not_mapped", True, (0, 0, 0, 0)))
    area._schedule_selftest()
    armed = area._selftest_after_id
    assert armed is not None, "the poll armed no retry to cancel"

    # When: the host itself is destroyed.
    area._cancelSelftest(_DestroyEvent(area))

    # Then: the armed after is cancelled and forgotten...
    assert bare.widget.after_cancel_calls == [armed], bare.widget.after_cancel_calls
    assert area._selftest_after_id is None, area._selftest_after_id

    # When: only a child is destroyed instead.
    bare.widget.after_cancel_calls.clear()
    area._selftest_after_id = armed
    area._cancelSelftest(_DestroyEvent(object()))

    # Then: the poll is left alone. The Canvas going away is already answered
    # by the surface's own not_run, so cancelling here would strand the
    # placeholder instead of resolving it.
    assert bare.widget.after_cancel_calls == [], bare.widget.after_cancel_calls
    assert area._selftest_after_id == armed, area._selftest_after_id


# ===========================================================================
# I. A None frame has to reach the screen, as a presented disabled image
# ===========================================================================
#
# ``_drawFrame`` routes ``frame=None`` to ``_showDisabled``
# (``GuiAssets.py:622-626``), and ``_showDisabled`` does compose and present
# (``GuiAssets.py:665-666``). So on paper the missing-frame case is handled, and
# the test at :376 agrees -- it proves the *routing* and stops there. Three
# things a user would call a bug are still unproved:
#
# 1. Nothing observes the composed buffer, so "it presented the disabled image"
#    is an assumption about three lines nobody asserts. The routing test can
#    only say "not None", which is equally true of a stale buffer.
# 2. The same-seq skip at ``GuiAssets.py:619-621`` runs *before* the None check,
#    and a real source hands out a **frozen** generation number when it dies:
#    ``CameraQueue.readFrameWithSeq`` returns ``(None, seq)`` with the unchanged
#    ``seq`` once the shared memory is gone (``core/Camera.py:1011-1018``). A
#    capture process that dies after frame 7 therefore offers ``(None, 7)``
#    forever, ``7`` is exactly the sequence the last healthy tick stored, and
#    the disabled image is never composited: the preview freezes on the last
#    live frame with nothing on screen saying the camera is gone.
# 3. The cause is unnamed. ``dimension_mismatch`` is a *size* diagnosis, and a
#    frame that does not exist cannot be one, so a missing frame has to be
#    attributable in its own right or a dead camera and a resized window are
#    the same observation -- section H's "no video, looks healthy" failure.


def _names_the_missing_frame(messages: list[str]) -> bool:
    """Whether some captured line attributes the disabled image to a missing frame.

    Deliberately generous about wording: ``design.md:508`` mandates counters for
    ``frames_composed`` and ``frames_discarded_dimension_mismatch`` and says
    nothing about how a missing frame is worded, so pinning a phrase would pin
    an invention. What is pinned is that the cause is *attributed*: the line
    talks about the absent frame, the camera or the source, and it is not the
    dimension-mismatch detail relabelled. A line naming none of the three
    cannot tell an operator which of the two causes to go looking for.
    """
    for message in messages:
        lowered = message.lower()
        if "dimension_mismatch" in lowered:
            continue
        if any(word in lowered for word in ("frame", "camera", "source")):
            return True
    return False


class _RaisingCamera:
    """A source whose every read raises, as a closed capture does.

    Local to this module for the same reason ``_RefusingSurface`` is: the shared
    ``StubCamera`` always returns, so a contract about a *failing* source cannot
    be expressed with it, and mutating the shared double is not an option. Its
    generation number stays put, matching the real shape -- the shared-memory
    ``seq`` is a counter the worker bumps, so a source that stops delivering
    keeps handing out the last value it reached.
    """

    def __init__(self, frozen_sequence: int, capture_size: tuple[int, int]) -> None:
        self.sequence = frozen_sequence
        self.capture_size = capture_size
        self.reads = 0

    def _fail(self) -> Any:
        self.reads += 1
        raise OSError("capture is closed")

    def readFrame(self, copy: bool = False) -> Any:
        return self._fail()

    def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
        return self._fail()

    def frame_seq(self) -> int:
        return self.sequence

    def saveCapture(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_a_none_frame_composes_and_presents_the_disabled_image() -> None:
    # Given: a bare area whose source is in the state a never-opened or
    # already-torn-down capture reports. StubCamera.frame defaults to None, so
    # the shared double *is* a None-delivering source with no special case.
    bare = bare_capture_area()
    _seed_runtime_state(bare.area)
    assert bare.camera.frame is None, (
        "the fixture is not a None-delivering source; StubCamera.frame must "
        f"default to None but is {bare.camera.frame!r}"
    )
    disabled = bare.area._disabled
    assert disabled.any(), "the disabled buffer is blank; nothing was loaded"

    # When: one tick runs against that source, with the sequence advancing so
    # the same-seq skip is not the subject here. The mid-run failure owns that
    # interaction, because it is where the skip actually bites.
    bare.camera.sequence = 5
    with _captured_log() as messages:
        result = getattr(bare.area, "_dispatch_tick")()

    # Then: (a1) the tick composes and then presents -- one present, in that
    # order. PASSES TODAY: _showDisabled already does both (GuiAssets.py:665-666).
    # Kept as the guard for T6, because a fix that makes the None path cheaper
    # (skip the compose, cache the buffer, fold it into the dedup) must not
    # quietly stop putting the image on the screen.
    assert bare.surface.names == ["compose", "present"], (
        f"(a1) UNMET: a None frame produced the present-path calls "
        f"{bare.surface.names}; the disabled image has to be composited and "
        "presented, in that order."
    )
    assert bare.surface.presents == 1, (
        f"(a1) UNMET: the surface was presented {bare.surface.presents} time(s) "
        "for a single None-frame tick; expected exactly 1."
    )

    # Then: (a2) and what it presented IS the disabled image, not merely some
    # non-None buffer. The routing test at :376 can only rule out None; this
    # names which image. PASSES TODAY: _showDisabled composes self._disabled
    # (GuiAssets.py:665). Kept as the guard, because a fix that hands compose a
    # stale work buffer would satisfy (a1) and every test that exists today.
    composed = bare.surface.composed_frames()[0]
    assert composed is not None and np.array_equal(composed, disabled), (
        "(a2) UNMET: the buffer presented for a None frame is not the area's "
        f"disabled image. presented shape "
        f"{None if composed is None else composed.shape} vs disabled "
        f"{disabled.shape}."
    )

    # Then: (a3) and no exception escaped the tick -- read from the return value
    # and the log rather than from the absence of a traceback. _dispatch_tick
    # catches Exception and reports it as "capture failed"
    # (GuiAssets.py:499-500), so a disabled path implemented by raising leaves
    # the *last live frame* on screen, which looks exactly like success and is
    # the same observable (a2) exists to rule out.
    # PASSES TODAY: _showDisabled raises nothing. Kept as the guard.
    from ui import preview_clock

    assert isinstance(result, preview_clock.DispatchResult), (
        f"(a3) UNMET: _dispatch_tick returned {result!r} instead of a "
        "DispatchResult, so the tick did not complete normally."
    )
    swallowed = [message for message in messages if "capture failed" in message]
    assert not swallowed, (
        "(a3) UNMET: the None-frame tick was implemented by raising and having "
        f"_dispatch_tick swallow it: {swallowed!r}. The screen then keeps "
        "showing the previous frame rather than the disabled image."
    )

    # Then: (a4) the cause is attributed, and attributed as itself. A frame that
    # does not exist is not a size mismatch, so the two must stay separable or
    # a dead camera and a resized window are the same observation.
    # PASSES TODAY: _readLatest bumps camera_none_count (GuiAssets.py:581-582)
    # and compositing a correctly-sized disabled image raises no size mismatch
    # at all. Kept so a fix that folds the two causes into one count fails
    # loudly instead of quietly.
    observation = bare.area._camera_observation
    assert observation["camera_none_count"] == 1, (
        f"(a4) UNMET: the missing frame was not attributed at the read layer. "
        f"camera_none_count is {observation['camera_none_count']!r} of "
        f"{observation!r}."
    )
    assert not _failure_counter(bare.area, DISCARD_COUNTER), (
        f"(a4) UNMET: a frame that does not exist was also counted as "
        f"{DISCARD_COUNTER}; a missing source and a size mismatch are different "
        "faults and must not share a count."
    )

    # Then: (a4b) and the attribution reaches the log. RED today: nothing in the
    # None-frame path logs at all, so the only way to learn the camera stopped
    # is to have been watching the screen when it did. This is section H's "a
    # counter nobody reads" one level up -- the count exists (above), but an
    # operator gets nothing without the evidence file.
    assert _names_the_missing_frame(messages), (
        "(a4b) UNMET: a None frame composited and presented the disabled image "
        "and nothing named the cause, so the capture dying mid-run and the "
        f"window being resized are the same observation. Captured messages: "
        f"{messages!r}."
    )


def test_a_mid_run_source_failure_shows_the_disabled_image() -> None:
    # Given: a bare area on a live source -- the state a working capture card
    # is in -- and the disabled image it has to fall back to.
    bare = bare_capture_area()
    _seed_runtime_state(bare.area)
    disabled = bare.area._disabled
    live = bgr_frame()

    # When: the source is healthy and delivers frame 7.
    bare.camera.frame = live
    bare.camera.sequence = 7
    getattr(bare.area, "_dispatch_tick")()

    # Then: live video really is on the screen, so the transition below is a
    # change of what is presented rather than a no-op that would pass anyway.
    assert bare.surface.presents == 1, (
        f"(b0) UNMET: the healthy tick did not present: {bare.surface.names}"
    )
    assert bare.surface.composed_frames()[0] is live, (
        "(b0) UNMET: the healthy tick did not present the source frame, so the "
        "transition assertions below would be measuring nothing."
    )
    live_presents = bare.surface.presents

    # When: the source dies mid-run. The shape is taken from the real reader
    # rather than invented: CameraQueue.readFrameWithSeq returns (None, seq)
    # with the *unchanged* generation number once the shared memory is gone
    # (core/Camera.py:1011-1018), so a capture process that dies after frame 7
    # keeps offering 7 forever while offering no frame at all.
    bare.camera.frame = None
    with _captured_log() as messages:
        getattr(bare.area, "_dispatch_tick")()

    # Then: (b1) the disabled image is composited and presented, so the frozen
    # live frame is replaced by one that says the camera stopped.
    # RED today: the same-seq skip at GuiAssets.py:619-621 returns before the
    # None check at :622, and 7 is precisely the sequence the healthy tick
    # stored, so the second tick composes nothing at all.
    assert bare.surface.presents > live_presents, (
        f"(b1) UNMET: the source delivered (None, 7) right after a healthy frame "
        f"7 and the surface was presented {bare.surface.presents} time(s) in "
        "total. The same-seq skip at GuiAssets.py:620 runs before the None check "
        "at :622, so the preview keeps showing the last live frame forever and "
        f"the disabled image is never composited. Captured messages: {messages!r}."
    )
    after_failure = bare.surface.composed_frames()[live_presents:]
    assert after_failure and np.array_equal(after_failure[0], disabled), (
        "(b1) UNMET: the buffer presented after the failure is not the disabled "
        f"image: {None if not after_failure else after_failure[0].shape} vs "
        f"{disabled.shape}."
    )


def test_a_raising_source_falls_back_to_the_disabled_image_and_recovers() -> None:
    # Given: a live source, driven one healthy tick so there is something on the
    # screen to be displaced.
    bare = bare_capture_area()
    _seed_runtime_state(bare.area)
    disabled = bare.area._disabled
    live = bgr_frame()
    bare.camera.frame = live
    bare.camera.sequence = 3
    getattr(bare.area, "_dispatch_tick")()
    assert bare.surface.composed_frames()[0] is live, (
        "(b2) UNMET: the healthy tick did not present the source frame, so the "
        "fallback and recovery assertions below would be measuring nothing."
    )
    live_presents = bare.surface.presents

    # When: the source starts raising from both read paths, which is what the
    # thread-backed Camera does once the capture is closed. The stand-in is
    # swapped onto the area rather than mutating StubCamera, which keeps the
    # healthy control above describing a source that works.
    bare.area.camera = _RaisingCamera(
        frozen_sequence=3, capture_size=bare.camera.capture_size
    )
    getattr(bare.area, "_dispatch_tick")()

    # Then: (b2) the disabled image is presented. PASSES TODAY: _readLatest
    # turns the failure into (None, None) (GuiAssets.py:563-573), and a None seq
    # bypasses the same-seq skip at :620 -- which is exactly why the frozen-seq
    # source above is the harder case. Kept as the guard: the fallback has to
    # hold for the raising source too, not only for the quiet one.
    assert bare.area._camera_observation["camera_read_error_count"] == 1, (
        "(b2) UNMET: the raising source was not recorded as a read failure. "
        f"observation is {bare.area._camera_observation!r}."
    )
    assert bare.surface.presents == live_presents + 1, (
        f"(b2) UNMET: after the source began raising, the surface was presented "
        f"{bare.surface.presents} time(s) in total; expected "
        f"{live_presents + 1}. The disabled image must reach the screen."
    )
    after_failure = bare.surface.composed_frames()[live_presents:]
    assert after_failure and np.array_equal(after_failure[0], disabled), (
        "(b2) UNMET: the buffer presented after the source began raising is not "
        f"the disabled image: {None if not after_failure else after_failure[0].shape}"
        f" vs {disabled.shape}."
    )

    # When: the source comes back and publishes a new generation.
    recovered = bgr_frame()
    bare.area.camera = StubCamera(
        capture_size=bare.camera.capture_size, frame=recovered, sequence=4
    )
    getattr(bare.area, "_dispatch_tick")()

    # Then: (b3) live video is presented again, so the fallback is not sticky.
    # PASSES TODAY: 4 differs from the stored 3, so the dedup does not apply.
    # Kept as the guard for the fix of (b1), where the dedup key is exactly what
    # gets touched: a fix that pins _last_frame_seq to the failed generation
    # would satisfy (b1) and strand the preview on the disabled image forever.
    tail = bare.surface.composed_frames()[live_presents + 1 :]
    assert tail and tail[0] is recovered, (
        f"(b3) UNMET: after the source recovered, what is presented is still the "
        f"disabled image. tail shapes: "
        f"{[None if frame is None else frame.shape for frame in tail]}."
    )
    assert not np.array_equal(tail[0], disabled), (
        "(b3) UNMET: recovery composited the disabled image again; the source "
        "published a real frame and that frame has to reach the screen."
    )


def _teardown_markers(node: ast.AST) -> list[tuple[str, str]]:
    """``[(kind, value)]`` for phase assignments, ``release()`` and ``destroy()``.

    Kind is one of ``phase``, ``release`` or ``destroy``; the value is the phase
    name for a phase and the dotted call target otherwise. The list is in source
    order, which is the order the statements would run.
    """
    marked: list[tuple[int, str, str]] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Assign) and any(
            isinstance(target, ast.Attribute) and target.attr == "_exit_phase"
            for target in child.targets
        ):
            value = child.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                marked.append((child.lineno, "phase", value.value))
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr in {"release", "destroy"}:
            marked.append((child.lineno, child.func.attr, dotted_name(child.func)))
    return [(kind, value) for _line, kind, value in sorted(marked)]


# ===========================================================================
# J. The non-Windows backend obeys the same size rule
# ===========================================================================


def test_photo_surface_paints_the_capture_frame_into_a_smaller_show_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the fallback surface at the shipped show size, with ImageTk and
    # the Canvas stubbed out, because a real PhotoImage needs a Tk root and
    # this backend is otherwise unreachable without a display.
    import ui.photo_surface as module
    from core import preview_renderer

    built: list[Any] = []

    class _PhotoImage:
        def __init__(self, image: Any) -> None:
            built.append(image)

    class _Canvas:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.bindings: dict[str, Any] = {}
            return None

        def bind(self, sequence: str, func: Any = None, add: Any = None) -> str:
            if func is not None:
                self.bindings[sequence] = func
            return sequence

        def pack(self, **kwargs: Any) -> None:
            return None

        def create_image(self, *args: Any, **kwargs: Any) -> str:
            return "image"

        def config(self, **kwargs: Any) -> None:
            return None

        def itemconfig(self, _item: Any, **_kwargs: Any) -> None:
            return None

        def delete(self, _tag: str) -> None:
            return None

    class _Host:
        """A host that binds nothing, which is what makes it forward nothing.

        ``attach`` reads ``host.bind()`` to decide which pointer events the
        canvas has to hand on. ``None`` cannot answer that, and it only ever
        survived here because the ``_Canvas`` double below ignores the master
        it is handed -- so the real ``tk.Canvas(None)`` never runs.
        """

        def bind(self, *_args: Any) -> tuple[str, ...]:
            return ()

    monkeypatch.setattr(module.ImageTk, "PhotoImage", _PhotoImage)
    monkeypatch.setattr(module.tk, "Canvas", _Canvas)
    area = module.PhotoImageSurface(host=_Host())
    show_size = (640, 360)
    # Attach through the production path: hand-wiring _canvas after a bare
    # resize() leaves an invalid state (no canvas at resize time), which the
    # surface now correctly refuses instead of recording a phantom size.
    area.attach(0, show_size)
    area._image_id = "image"
    frame = bgr_frame(1280, 720)
    assert frame.shape == (720, 1280, 3) and frame.flags.c_contiguous is True

    # When: a live 1280x720 capture frame is composited and presented.
    composed = area.compose(frame, preview_renderer.OverlayState())
    presented = area.present()

    # Then: both succeed, so <Configure> cannot be what blanks this backend
    # either. It refused the live frame for exactly as long as the GDI surface
    # did, because it compared the frame against the show size instead of the
    # capture size the camera actually produces.
    assert (composed.ok, presented.ok) == (True, True)
    assert (composed.detail, presented.detail) == ("ok", "ok")

    # Then: and the frame was drawn 1:1 into the top-left of the show size,
    # never scaled, so both backends show the same picture.
    image = built[-1]
    assert image.size == show_size
    assert np.array_equal(np.asarray(image)[:, :, ::-1], frame[:360, :640])


# ===========================================================================
# K. An overlay change with an unchanged seq must still repaint
# ===========================================================================
#
# ``_drawFrame`` dedups on the camera sequence alone
# (``GuiAssets.py:752-754``): a repeated ``seq`` returns ``False`` before the
# overlay is ever consulted. The mouse handlers only mutate the frozen
# ``StickState`` / ``OverlayState`` data -- redraw is purely timer-driven
# through ``PreviewClock`` -> ``_dispatch_tick`` -> ``_drawFrame`` -- so
# ``_drawFrame`` is the ONLY place that can notice an overlay change. On a
# capture board running 5-10 fps the consequence is visible: the ring lags
# 100-200 ms behind the drag, and after the finger lifts the stale ring stays
# on screen until the camera happens to publish a new generation.
#
# Both tests are RED today and both fail on the return value as their primary
# assertion, so the RED reason is unambiguous: ``_drawFrame`` returns
# ``False``. The secondary assertion (the surface really was asked to repaint)
# is made on the existing compose recording, ``RecordingSurface.composes``;
# the double has no recompose counter, and inventing one is another agent's
# task in a later wave.


def test_draw_frame_repaints_when_only_the_overlay_changed() -> None:
    # Given: a bare area whose camera has published exactly one generation, so
    # the same-seq skip has a sequence to skip.
    bare = bare_capture_area()
    frame = bgr_frame()
    bare.camera.frame = frame
    bare.camera.sequence = 1

    # When: the first frame is drawn...
    first = getattr(bare.area, "_drawFrame")(frame, 1)

    # Then: ...and it really was presented and stored its seq, so the second
    # call below is a same-seq call rather than a first call in a second
    # costume. PASSES today; it is the guard that keeps the RED below about
    # the overlay rather than about the fixture.
    assert first is True
    assert bare.area._last_frame_seq == 1

    # When: the overlay changes while the camera frame does not -- the stick is
    # armed between two ticks of a 5-10 fps source -- and the same seq is drawn
    # again.
    stick_press(bare.area, 3, 4, "L")
    second = getattr(bare.area, "_drawFrame")(frame, 1)

    # Then: the second draw repaints, because the overlay is part of what is
    # on the screen. RED today: the skip at GuiAssets.py:752-754 keys on seq
    # alone, so the armed ring waits for the next camera generation -- up to
    # 100-200 ms at 5-10 fps -- before it appears.
    assert second is True, (
        "UNMET: _drawFrame returned False for a repeated seq after the overlay "
        "changed. The same-seq skip at GuiAssets.py:752-754 keys on seq alone, "
        "so an armed stick is not drawn until the camera publishes a new frame."
    )

    # Then: and the surface really was asked to repaint, so a fix that returns
    # True without composing would satisfy the assertion above and still draw
    # nothing. Asserted on the compose recording plus the recompose counter
    # (``RecordingSurface.composes`` + ``recompose_calls``): the overlay-only
    # path repaints through recompose, not compose.
    assert len(bare.surface.composes) + len(bare.surface.recompose_calls) == 2, (
        f"UNMET: the surface recorded {len(bare.surface.composes)} compose(s) "
        f"and {len(bare.surface.recompose_calls)} recompose(s) "
        "for two draws; an overlay change with an unchanged seq has to reach "
        "the surface again."
    )


def test_draw_frame_repaints_after_release_without_a_new_frame() -> None:
    # Given: a bare area on generation 1 with the left stick armed, so the
    # overlay on screen is a ring the camera did not change.
    bare = bare_capture_area()
    frame = bgr_frame()
    bare.camera.frame = frame
    bare.camera.sequence = 1
    getattr(bare.area, "_drawFrame")(frame, 1)
    stick_press(bare.area, 3, 4, "L")
    assert bare.area._last_frame_seq == 1

    # When: the stick is released -- the overlay changes back to default -- and
    # the same seq is drawn again before the camera publishes a new frame.
    stick_release(bare.area, "L")
    repainted = getattr(bare.area, "_drawFrame")(frame, 1)

    # Then: the draw repaints, so the stale ring is erased at once. RED today:
    # the same-seq skip returns False and the ring stays on screen until the
    # next camera generation, up to 100-200 ms at 5-10 fps.
    assert repainted is True, (
        "UNMET: _drawFrame returned False for a repeated seq after the stick "
        "was released. The same-seq skip at GuiAssets.py:752-754 keys on seq "
        "alone, so the released ring is not erased until the camera publishes "
        "a new frame."
    )

    # Then: and the surface really was asked to repaint. Asserted on the
    # compose recording plus the recompose counter
    # (``RecordingSurface.composes`` + ``recompose_calls``): the overlay-only
    # path repaints through recompose, not compose.
    assert len(bare.surface.composes) + len(bare.surface.recompose_calls) == 2, (
        f"UNMET: the surface recorded {len(bare.surface.composes)} compose(s) "
        f"and {len(bare.surface.recompose_calls)} recompose(s) "
        "for two draws; releasing the stick with an unchanged seq has to reach "
        "the surface again."
    )
