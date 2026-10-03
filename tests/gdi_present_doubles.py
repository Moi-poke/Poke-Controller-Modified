"""Headless doubles for the GDI present-path contracts; # noqa: SIZE_OK.

Design: `docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md`
sections 8 and 9.

Nothing in this module creates a Tk interpreter, a window, a GDI object or a
thread, and nothing reads the wall clock to decide an assertion. These doubles
are what make the `CaptureArea` refactor testable at all: a real `tk.Frame`
cannot be constructed without a display, so every instance-level fact is driven
through a `__new__`-style instance whose widget, surface and master
collaborators are replaced by recorders.

Two seams live here:

* :class:RecordingSurface -- the `PreviewRenderer`-shaped double that records
  the compose / present / resize / release traffic the real present path issues.
* :class:RecordingWidget / :class:RecordingMaster / :class:StubCamera --
  `tk` stand-ins for the Frame's own config/bind/after calls, for the settings
  flags on `master`, and for the camera the area reads its frames and its
  capture size from.

The contract vocabulary the doubles encode (show size, stick radius, the derived
knob and snap radii, the banned canvas API, the colour fixtures) is shared by all
three contract test modules, which is why it lives beside the doubles rather
than in any one of them. The file is over the 250 pure-LOC ceiling because
`bare_capture_area` is one indivisible fixture: splitting the attribute set
across two modules would let two test files disagree about what a bare
`CaptureArea` is, and that disagreement is exactly the bug the refactor risks.

Static source inspection lives in the sibling module `gdi_source_readers.py`;
this one holds runtime behaviour only.

The file is a helper module, not a test module: its name deliberately does not
start with `test_` so pytest never collects it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
SERIAL_CONTROLLER = _REPO_ROOT / "SerialController"
GUI_ASSETS_SOURCE = SERIAL_CONTROLLER / "GuiAssets.py"
WINDOW_SOURCE = SERIAL_CONTROLLER / "Window.py"
HARNESS_SOURCE = _REPO_ROOT / "tests" / "preview_fps_support.py"

CAPTURE_AREA = "CaptureArea"

#: Every Tk Canvas drawing / item method the design abolishes. ``coords()`` is
#: the load-bearing one -- it repaints the item's old bounding box from the
#: canvas background, which erases the video (design section 0). The others are
#: banned so the refactor cannot be half-applied.
CANVAS_ITEM_API: frozenset[str] = frozenset(
    {
        "create_image",
        "create_rectangle",
        "create_oval",
        "create_line",
        "coords",
        "itemconfig",
        "delete",
        "tag_bind",
        "tag_config",
        "find_withtag",
        "bbox",
    }
)

#: The Canvas/PhotoImage attribute names the refactor deletes outright. The
#: measurement harness reads ``area._photo`` today, and ``im`` / ``im_`` are the
#: canvas image-item bookkeeping that only existed to re-point the item.
PHOTO_IMAGE_ATTRIBUTES: frozenset[str] = frozenset({"_photo", "im", "im_"})

#: PIL symbols the ``CaptureArea`` class body must no longer reach for. Scoped to
#: the class, not the module: design section 8 keeps ``ImageTk.PhotoImage`` for
#: the non-Windows ``PhotoImageSurface`` fallback, which lives in ``ui/``.
PIL_SYMBOLS: frozenset[str] = frozenset({"Image", "ImageTk", "PhotoImage"})

#: The show size every bare instance is built at, and the derived show geometry.
SHOW_WIDTH = 8
SHOW_HEIGHT = 8
SHOW_SIZE = (SHOW_WIDTH, SHOW_HEIGHT)

#: The area's stick radius, and the two derived radii the design fixes
#: (design section 5: ``k = radius // 10`` and the snap distance
#: ``radius + radius // 11`` from ``GuiAssets.py:991``).
STICK_RADIUS = 60
KNOB_RADIUS = STICK_RADIUS // 10
SNAP_DISTANCE = STICK_RADIUS + STICK_RADIUS // 11

#: A distinct, order-sensitive BGR frame. Channel values differ so a stray
#: ``COLOR_BGR2RGB`` swap is observable rather than invisible.
_FILTER_SATURATED_BGR = (0, 255, 255)  # OpenCV hue 30: inside the test window
_FILTER_NEUTRAL_BGR = (255, 0, 0)  # OpenCV hue 120: outside the test window
FILTER_LOWER = (25, 200, 200)
FILTER_UPPER = (35, 255, 255)

#: A non-neutral colour correction. The hue shift is what makes the output
#: channel-order sensitive, which is the property the BGR assertion needs.
NON_NEUTRAL_CORRECTION: dict[str, float | int] = {
    "gamma": 1.0,
    "contrast": 1.1,
    "brightness": 10,
    "saturation": 1.4,
    "hue_shift": 30,
}

#: Tk colour names the recognition path actually passes (``CommandVision.py:534``
#: and ``:541``) with the Win32 ``COLORREF`` each must become. Tk stores
#: ``0x00RRGGBB``; Win32 is ``0x00BBGGRR``, so both are byte-reversed
#: (design section 5). A passthrough implementation gets both wrong.
TK_BLUE_COLORREF = 0x00FF0000
TK_RED_COLORREF = 0x000000FF
WHITE_COLORREF = 0x00FFFFFF


# ---------------------------------------------------------------------------
# Recorded call shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One method call seen by a double, with its positional and keyword args."""

    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class _Recording:
    """Shared ordered call log. Names for order, args for interleaving."""

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []

    @property
    def names(self) -> list[str]:
        return [call.name for call in self.calls]

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(RecordedCall(name, args, dict(kwargs)))


# ---------------------------------------------------------------------------
# The renderer double
# ---------------------------------------------------------------------------
class RecordingSurface(_Recording):
    """``PreviewRenderer``-shaped double for the present path.

    All seven protocol methods exist, so ``isinstance(surface, PreviewRenderer)``
    holds and a defensive check in production cannot reject the double. Only
    ``compose`` / ``recompose`` / ``present`` / ``resize`` / ``release`` are the
    ones the ``CaptureArea`` contract talks about; ``attach`` and ``client_size``
    exist to satisfy the protocol and are recorded for completeness.
    """

    def __init__(self, client_size: tuple[int, int] = (1280, 720)) -> None:
        super().__init__()
        self.attach_calls: list[tuple[int, tuple[int, int]]] = []
        self.resizes: list[tuple[int, int]] = []
        self.composes: list[tuple[Any, Any]] = []
        self.recompose_calls: list[Any] = []
        self.presents = 0
        self.releases = 0
        self._client_size = client_size

    def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None:
        self._record("attach", parent_hwnd, size)
        self.attach_calls.append((parent_hwnd, (size[0], size[1])))

    def resize(self, size: tuple[int, int]) -> None:
        self._record("resize", size)
        self.resizes.append((size[0], size[1]))

    def compose(self, frame: Any, overlay: Any) -> Any:
        self._record("compose", frame, overlay)
        self.composes.append((frame, overlay))
        return _render_result(ok=True, detail="ok")

    def recompose(self, overlay: Any) -> Any:
        self._record("recompose", overlay)
        self.recompose_calls.append(overlay)
        return _render_result(ok=True, detail="ok")

    def present(self) -> Any:
        self._record("present")
        self.presents += 1
        return _render_result(ok=True, detail="ok")

    def release(self) -> None:
        self._record("release")
        self.releases += 1

    def client_size(self) -> tuple[int, int]:
        return self._client_size

    # -- test-only inspection --------------------------------------------
    def composed_frames(self) -> list[Any]:
        return [frame for frame, _overlay in self.composes]


def _render_result(ok: bool, detail: str) -> Any:
    """A real ``RenderResult``, so the double satisfies the renderer contract."""
    from core import preview_renderer

    return preview_renderer.RenderResult(ok=ok, elapsed_ns=0, detail=detail)


# ---------------------------------------------------------------------------
# The widget double
# ---------------------------------------------------------------------------
class RecordingWidget(_Recording):
    """``tk.Frame`` stand-in: records config/bind/unbind/after, no interpreter.

    It deliberately has **no** ``create_image`` / ``coords`` / ``itemconfig`` /
    ``delete``. A ``CaptureArea`` that still reaches for a canvas item therefore
    fails loudly with ``AttributeError`` here, which is the runtime counterpart
    of the static ``CANVAS_ITEM_API`` assertion.
    """

    def __init__(self) -> None:
        super().__init__()
        self.config_calls: list[dict[str, Any]] = []
        self.bind_calls: list[tuple[str, Any]] = []
        self.unbind_calls: list[str] = []
        self.after_calls: list[tuple[int, Any]] = []
        self.after_cancel_calls: list[str] = []
        self.destroy_calls = 0
        self._next_after = 0

    def config(self, **kwargs: Any) -> None:
        self._record("config", **kwargs)
        self.config_calls.append(dict(kwargs))

    def configure(self, **kwargs: Any) -> None:
        self._record("configure", **kwargs)
        self.config_calls.append(dict(kwargs))

    def bind(self, sequence: Any = None, func: Any = None, add: Any = None) -> str:
        self._record("bind", sequence, func, add)
        self.bind_calls.append((sequence, func))
        return f"{sequence}-bind"

    def unbind(self, sequence: Any = None, funcid: Any = None) -> None:
        self._record("unbind", sequence, funcid)
        self.unbind_calls.append(sequence)

    def after(self, delay_ms: int = 0, func: Any = None, *args: Any) -> str:
        self._record("after", delay_ms, func, args)
        self.after_calls.append((delay_ms, func))
        self._next_after += 1
        return f"after-{self._next_after}"

    def after_cancel(self, after_id: str) -> None:
        self._record("after_cancel", after_id)
        self.after_cancel_calls.append(after_id)

    # -- test-only inspection --------------------------------------------
    def cursor_values(self) -> list[Any]:
        return [call["cursor"] for call in self.config_calls if "cursor" in call]

    def bound_sequences(self) -> list[str]:
        return [sequence for sequence, _func in self.bind_calls]

    def handler_for(self, sequence: str) -> Any:
        for bound, func in self.bind_calls:
            if bound == sequence:
                return func
        raise AssertionError(
            f"{sequence!r} was never bound; bound sequences were {self.bound_sequences()}"
        )


class _SettingFlag:
    """The ``tk.BooleanVar`` surface ``master.is_use_*_stick_mouse`` provides."""

    def __init__(self, value: bool) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = value


@dataclass(slots=True)
class RecordingMaster:
    """The two settings flags the stick / range handlers read off ``master``."""

    is_use_left_stick_mouse: _SettingFlag = field(
        default_factory=lambda: _SettingFlag(True)
    )
    is_use_right_stick_mouse: _SettingFlag = field(
        default_factory=lambda: _SettingFlag(True)
    )

    @classmethod
    def with_sticks(cls, left: bool = True, right: bool = True) -> RecordingMaster:
        return cls(_SettingFlag(left), _SettingFlag(right))


@dataclass(slots=True)
class StubCamera:
    """The camera surface ``CaptureArea`` reads: size, frames and sequences."""

    capture_size: tuple[int, int] = SHOW_SIZE
    frame: Any = None
    sequence: int = 0

    def readFrame(self, copy: bool = False) -> Any:
        return None if self.frame is None else self.frame.copy() if copy else self.frame

    def readFrameWithSeq(self, copy: bool = False) -> tuple[Any, int]:
        return self.readFrame(copy), self.sequence

    def readFrameWithTiming(self, copy: bool = False) -> tuple[Any, int, int, int]:
        return self.readFrame(copy), self.sequence, 1_000, 2_000

    def frame_seq(self) -> int:
        return self.sequence

    def saveCapture(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass(frozen=True, slots=True)
class PointerEvent:
    """A Tk event double. ``x``/``y`` for mouse, ``width``/``height`` for Configure."""

    x: int = 0
    y: int = 0
    width: int | None = None
    height: int | None = None


# ---------------------------------------------------------------------------
# The bare CaptureArea
# ---------------------------------------------------------------------------
def disabled_frame(width: int = SHOW_WIDTH, height: int = SHOW_HEIGHT) -> Any:
    """A deterministic BGR ``ndarray`` standing in for the disabled image."""
    frame = np.empty((height, width, 3), np.uint8)
    frame[:] = (7, 11, 13)
    return frame


def bgr_frame(width: int = SHOW_WIDTH, height: int = SHOW_HEIGHT) -> Any:
    """A deterministic C-contiguous BGR frame with distinct per-pixel values."""
    generator = np.random.default_rng(20260926)
    return generator.integers(0, 256, (height, width, 3), dtype=np.uint8)


def filter_probe_frame(width: int = SHOW_WIDTH, height: int = SHOW_HEIGHT) -> Any:
    """A frame with both an in-mask and an out-of-mask half.

    The saturated top half survives ``apply_filter(..., "gray_out")`` with its
    channels intact, which is what makes the filter output channel-order
    sensitive; the plain bottom half is greyed. Both classes must be present or
    the BGR assertion would pass vacuously, so the callers assert that.
    """
    frame = np.empty((height, width, 3), np.uint8)
    top = max(1, height // 2)
    frame[:top, :] = _FILTER_SATURATED_BGR
    frame[top:, :] = _FILTER_NEUTRAL_BGR
    return frame


@dataclass(slots=True)
class BareArea:
    """A ``__new__``-built ``CaptureArea`` plus the doubles installed on it.

    ``surface``, ``widget`` and ``master`` are the three seams every
    instance-level test needs. ``show_size`` and the buffers are seeded at the
    requested show size, and ``_allocBuffers`` is called last so whichever
    buffers the production ``_allocBuffers`` allocates exist at the right shape.
    """

    area: Any
    surface: RecordingSurface
    widget: RecordingWidget
    master: RecordingMaster
    camera: StubCamera

    @property
    def variables(self) -> dict[str, Any]:
        return vars(self.area)


def bare_capture_area(
    *,
    width: int = SHOW_WIDTH,
    height: int = SHOW_HEIGHT,
    left_stick: bool = True,
    right_stick: bool = True,
) -> BareArea:
    """Build a real ``CaptureArea`` with no Tk interpreter behind it.

    The seeded attribute set is exactly the instance state the present-path
    contract demands, so a missing attribute is a statement about the contract
    rather than an accident of the fixture:

    * ``_surface``          -- the ``PreviewRenderer`` that owns the present path
    * ``_viewport``         -- the display box the surface was last sized to
    * ``_stick_radius``     -- ``radius`` in capture units
    * ``_stick_left`` / ``_stick_right`` / ``_guide`` / ``_img_rect`` -- the four
      design-named ``OverlayState`` components
    * ``_disabled``         -- the BGR disabled image
    * ``_filter_buf`` / ``_correct_buf`` -- the BGR filter/correction work buffers
    * ``_last_frame_seq``   -- the same-seq skip key
    """
    from GuiAssets import CaptureArea as _CaptureAreaClass

    area = _CaptureAreaClass.__new__(_CaptureAreaClass)
    surface = RecordingSurface(client_size=(width, height))
    widget = RecordingWidget()
    master = RecordingMaster.with_sticks(left_stick, right_stick)
    camera = StubCamera(capture_size=(width, height))

    from core import preview_renderer

    defaults = preview_renderer.OverlayState()
    area.master = master
    area.camera = camera
    area.ser = None
    area.is_show_var = _SettingFlag(True)

    area.radius = STICK_RADIUS
    # The stick radius in capture units. Production recomputes it on every press
    # from the viewport; this is the value that recomputation produces while
    # capture_size == show_size, so a bare instance is not already wrong.
    area._stick_radius = STICK_RADIUS
    area.show_width = width
    area.show_height = height
    area.show_size = (width, height)
    # The viewport the Frame was actually given. Equal to the show size until a
    # <Configure> says otherwise, and the only reference CaptureArea._mapper
    # derives a display size and a display origin from.
    area._viewport = (width, height)

    area.lx_init, area.ly_init = 0, 0
    area.rx_init, area.ry_init = 0, 0
    area.min_x, area.max_x = 0, 0
    area.min_y, area.max_y = 0, 0
    area.ss = None

    area._langle = None
    area._lmag = None
    area._rangle = None
    area._rmag = None
    area._lrec = None
    area._rrec = None

    area._configured_fps = 60
    area.next_frames = 1000.0 / 60
    area._preview_clock = None
    area._capturing = True
    area._preview_stop_requested = False

    area._filter_enabled = False
    area._filter_lower = [0, 0, 0]
    area._filter_upper = [179, 255, 255]
    area._filter_mode = "gray_out"
    area._correction = None
    area._correction_active = False

    area._last_sent = 0.0
    area._last_sent_mag = 0.0
    area._motion_last = {}

    area._last_frame_seq = None
    area._rect_after_id = None
    area._selftest_after_id = None

    area._surface = surface
    area._stick_left = defaults.left_stick
    area._stick_right = defaults.right_stick
    area._guide = defaults.guide
    area._img_rect = defaults.img_rect
    area._disabled = disabled_frame(width, height)

    for name, method in (
        ("config", widget.config),
        ("configure", widget.configure),
        ("bind", widget.bind),
        ("unbind", widget.unbind),
        ("after", widget.after),
        ("after_cancel", widget.after_cancel),
    ):
        setattr(area, name, method)

    area._allocBuffers()
    return BareArea(
        area=area, surface=surface, widget=widget, master=master, camera=camera
    )


def spy(area: Any, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    """Wrap a bound method on ``area`` so entry is observable but work still runs.

    Returns the recorded call list. This is deliberately not a stub: the wrapped
    method runs, so a test can assert that a phase was entered without thereby
    deciding how that phase is implemented.
    """
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    original = getattr(area, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    setattr(area, name, wrapper)
    return calls


def clear_motion_throttle(area: Any) -> None:
    """Reset the Motion rate limiter so synthetic drags are not throttled.

    A real mouse delivers Motion at ~125 Hz while ``MOTION_MIN_INTERVAL`` is
    8 ms, so consecutive synthetic events inside one test would be swallowed by
    the throttle rather than by the contract under test. Clearing the limiter
    between events reproduces the real cadence without sleeping.
    """
    limiter = getattr(area, "_motion_last", None)
    if isinstance(limiter, dict):
        limiter.clear()


def stick_press(area: Any, x: int, y: int, side: str = "L") -> None:
    """Press a stick at an absolute display position."""
    event = PointerEvent(x=x, y=y)
    if side == "L":
        getattr(area, "mouseLeftPress")(event, None)
    else:
        getattr(area, "mouseRightPress")(event, None)


def stick_drag(area: Any, x: int, y: int, side: str = "L") -> None:
    """Drag a pressed stick to an absolute display position."""
    clear_motion_throttle(area)
    event = PointerEvent(x=x, y=y)
    if side == "L":
        getattr(area, "mouseLeftPressing")(event, None)
    else:
        getattr(area, "mouseRightPressing")(event, None)


def stick_release(area: Any, side: str = "L") -> None:
    """Release a stick."""
    if side == "L":
        getattr(area, "mouseLeftRelease")(None)
    else:
        getattr(area, "mouseRightRelease")(None)


# ---------------------------------------------------------------------------
