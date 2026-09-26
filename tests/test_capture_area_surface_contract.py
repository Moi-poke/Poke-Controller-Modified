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
from pathlib import Path
from typing import Any

import numpy as np
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
    bare_capture_area,
    bgr_frame,
    filter_probe_frame,
    spy,
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


def test_constructor_signature_is_unchanged_and_positional() -> None:
    # Given: the real class.
    capture_area = _capture_area_class()

    # When: the constructor is inspected.
    parameters = list(inspect.signature(capture_area.__init__).parameters.values())

    # Then: the parameter order and defaults are exactly what they were, because
    # RealCaptureHarness.start and _run_teardown_probe both pass ser positionally.
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
    ]
    assert [parameter.default for parameter in parameters[1:5]] == [
        inspect.Parameter.empty
    ] * 4
    assert parameters[5].default is None
    assert parameters[6].default == 640
    assert parameters[7].default == 360
    assert parameters[8].default is None

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
