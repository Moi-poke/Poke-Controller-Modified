"""Contracts for the non-Windows fallback surface ``ui/photo_surface.py``; # noqa: SIZE_OK -
one indivisible fixture set, for the reason given at the end of this docstring.

``PhotoImageSurface`` is the only ``PreviewRenderer`` a mac/Linux box can ever
get, and it is reached through a public protocol, not through a private call
site: any future ``CaptureArea`` refactor, any second host, any test double can
hand it a degenerate size, a non-contiguous frame, or call ``present()`` with
nothing staged. ``GdiSurface`` already refuses all three
(``gdi_surface.py:772-788``, ``:798-805``, ``:860-861``). This file pins the
same three refusals on the fallback so the two backends cannot drift apart, and
so neither of them can be wedged by a caller that misbehaves.

The observability half of the parity is pinned here too, because it drifts the
same way: ``client_size`` must measure the live box (``gdi_surface.py:759-763``),
a refused frame must be logged once rather than once per frame
(``:888-893``), ``self_test`` must answer in the shape ``GuiAssets.py:421-427``
reads through ``hasattr``, and an ``attach`` that cannot build its canvas must
warn *and* print (``:659-667``) instead of letting ``TclError`` end the app at
construction.

**This is boundary hygiene, not a fix for an observed freeze.** A probe of the
current wiring proved the re-entrant ``resize`` is unreachable in practice:
``<Configure>`` is bound on the ``Frame`` (``GuiAssets.py:428``) and the canvas
is packed ``fill=BOTH, expand=True`` (``photo_surface.py:54``), so ``config()``
on the canvas never generates a ``<Configure>`` that re-enters ``resize`` -- the
control converged in one step. A guard that is currently unreachable is still
worth having *at a public protocol boundary*, because reachability is a property
of today's wiring and not of the contract: the second host, the ``pack``-less
future layout, or a subclass that binds ``<Configure>`` to the canvas all
re-introduce the nesting without anyone editing ``resize``.

Everything here is headless. ``attach`` builds a real ``tk.Canvas``, so the
``Canvas`` and ``ImageTk.PhotoImage`` constructors are monkeypatched for the
instance-level contracts; a real ``PhotoImage`` additionally needs a live Tk
root, and the module must stay importable without one.

The file is over the pure-LOC ceiling because the doubles and the contracts are
one indivisible fixture, for the same reason ``gdi_present_doubles.py`` keeps
``bare_capture_area`` whole: ``_attach`` clears the record that ``attach`` itself
populated, and every contract below measures the calls it provoked *against that
cleared baseline*. Split the doubles from the contracts and a future test can
build a surface without the precondition and measure the attach it inherited
instead of its own -- a wrong answer that reads green. Splitting by subject
instead (resize / compose / present) would duplicate the same fixture three
times for three guards that share one canvas double.
"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

_SERIAL_CONTROLLER = str(Path(__file__).resolve().parent.parent / "SerialController")

#: Sizes that must leave the surface exactly as it was. ``(1, 1)`` is the one
#: Tk actually sends: a ``<Configure>`` arrives before the geometry manager has
#: settled, so a surface that honours it configures itself into a 1x1 box and
#: never recovers. The rest are the shapes a confused or hostile caller can
#: hand the protocol.
DEGENERATE_SIZES: tuple[tuple[int, int], ...] = (
    (1, 1),
    (0, 0),
    (1, 720),
    (1280, 1),
    (-4, 720),
)

#: The size every attached surface is built at. Distinct from ``(1, 1)`` and
#: from every other size used below, so "nothing happened" and "it resized" can
#: never be confused.
ATTACHED_SIZE = (640, 360)

#: The pointer sequences ``CaptureArea`` binds on itself, in the canonical
#: spelling ``host.bind()`` answers with rather than the one the source typed
#: (``GuiAssets.py:1400-1402`` left stick, ``:1407-1409`` right).  Tk rewrites
#: ``<ButtonPress-1>`` to ``<Button-1>`` and ``<Button1-Motion>`` to
#: ``<B1-Motion>``, and the surface forwards whatever the host *reports*, so
#: these are the names the tests bind and expect back.  Five groups: press,
#: drag, release, the right-button set, and plain motion.
POINTER_SEQUENCES: tuple[str, ...] = (
    "<Button-1>",
    "<B1-Motion>",
    "<ButtonRelease-1>",
    "<Button-3>",
    "<B3-Motion>",
    "<ButtonRelease-3>",
    "<Motion>",
)

#: The same shapes behind modifiers: range select (``GuiAssets.py:395-397``)
#: and the colour probe (``:393-394``).  They ride the identical round trip, so
#: they are pinned only to show a modifier survives it and not just the name.
MODIFIER_SEQUENCES: tuple[str, ...] = (
    "<Control-Shift-Button-1>",
    "<Control-Shift-B1-Motion>",
    "<Control-Shift-ButtonRelease-1>",
    "<Control-Button-1>",
    "<Control-ButtonRelease-1>",
)

#: A sequence nothing in the tree binds, so the forwarding cannot be a
#: hand-copied list of the twelve above.
UNUSED_SEQUENCE = "<Shift-B1-Motion>"


def _photo_surface_module() -> Any:
    """``ui.photo_surface``, imported at call time so a break is a per-test RED."""
    import ui.photo_surface as module

    return module


def _capture_size() -> tuple[int, int]:
    """The camera's frame size, which ``compose`` judges frames against."""
    from core.Camera import CAPTURE_SIZE

    return (int(CAPTURE_SIZE[0]), int(CAPTURE_SIZE[1]))


def _contiguous_frame(width: int, height: int) -> np.ndarray:
    """A C-contiguous BGR frame of the given width and height."""
    frame = np.zeros((height, width, 3), np.uint8)
    frame[:, :, 0] = 17  # a value no zeroed or reordered buffer can fake
    assert frame.flags.c_contiguous is True
    return frame


def _non_contiguous_frame(width: int, height: int) -> np.ndarray:
    """A BGR frame of the right shape whose rows are strided, not packed.

    A channel slice of a 6-channel array keeps ``(height, width, 3)`` while every
    row steps 6 bytes per pixel, so the contiguity flag is false and the shape
    check downstream cannot be what refuses it.
    """
    frame = np.zeros((height, width, 6), np.uint8)[:, :, :3]
    frame[:, :, 0] = 17
    assert frame.shape == (height, width, 3)
    assert frame.flags.c_contiguous is False
    return frame


class _RecordingCanvas:
    """The ``tk.Canvas`` stand-in ``attach`` builds its canvas from.

    Records ``pack`` / ``bind`` / ``unbind`` / ``create_image`` / ``config`` /
    ``itemconfig`` / ``delete`` and the overlay item calls, and can re-enter or
    fail from inside ``config`` so the ``resize`` guard is exercised at the only
    seam that can nest. It also answers ``winfo_width`` / ``winfo_height`` /
    ``winfo_ismapped``, because the live-size and self-test contracts below ask
    the surface what the box currently is rather than what it once requested.
    """

    def __init__(self, host: Any = None, **_options: Any) -> None:
        self.host = host
        self.options = _options
        self.pack_calls: list[dict[str, Any]] = []
        self.bindings: dict[str, Any] = {}
        self.bound_handlers: dict[str, list[Any]] = {}
        self.unbind_calls: list[tuple[str, Any]] = []
        self.image_ids: list[tuple[int, int, str]] = []
        self.config_calls: list[tuple[int, int]] = []
        self.itemconfig_calls: list[tuple[Any, dict[str, Any]]] = []
        self.coords_calls: list[tuple[Any, tuple[Any, ...]]] = []
        self.delete_calls: list[str] = []
        self.item_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.destroyed = 0
        #: Set by a test to run inside ``config``, which is the only call that
        #: can re-enter ``resize`` or raise out of it.
        self.on_config: Callable[..., None] | None = None
        #: What ``winfo_width`` / ``winfo_height`` report. A window Tk has not
        #: placed on screen yet measures 1x1 -- the same 1x1 the <Configure>
        #: storm of the module docstring is about, and the only honest starting
        #: value for a headless double. A test sets it directly to move the box
        #: the way ``fill=BOTH, expand=True`` moves a real one.
        self.winfo_size: tuple[int, int] = (1, 1)
        #: What ``winfo_ismapped`` reports. ``pack`` into a toplevel that is not
        #: up yet only queues the map, so a canvas built during app
        #: construction is not on screen until the mainloop runs.
        self.mapped = False
        #: What ``winfo_viewable`` reports. True when the canvas is mapped AND
        #: no ancestor is minimized. A test sets this independently of
        #: ``mapped`` so the viewable-vs-ismapped distinction is testable.
        self.viewable = False

    def pack(self, **kwargs: Any) -> None:
        self.pack_calls.append(kwargs)

    def bind(self, sequence: str, func: Any, add: Any = None) -> str:
        """The only seam the pointer forwarding is installed through.

        ``add="+"`` *accumulates* on a real Tk widget, so the handlers are kept
        in a list per sequence instead of one slot: a second forwarding of a
        sequence that already has one is a second handler, and a double that
        overwrites cannot tell the two apart. ``bindings`` still holds the most
        recent handler, because the contracts above reach a sequence by name;
        :meth:`handlers` is what answers "how many forwardings does this
        sequence carry".
        """
        if add:
            self.bound_handlers.setdefault(sequence, []).append(func)
        else:
            self.bound_handlers[sequence] = [func]
        self.bindings[sequence] = func
        return sequence

    def unbind(self, sequence: str, funcid: Any = None) -> str:
        """Drop a binding the way Tk does: all of it, or one named funcid."""
        self.unbind_calls.append((sequence, funcid))
        if funcid is None:
            self.bound_handlers.pop(sequence, None)
            self.bindings.pop(sequence, None)
            return ""
        remaining = [
            handler
            for handler in self.bound_handlers.get(sequence, [])
            if handler is not funcid
        ]
        if remaining:
            self.bound_handlers[sequence] = remaining
        else:
            self.bound_handlers.pop(sequence, None)
            self.bindings.pop(sequence, None)
        return ""

    def handlers(self, sequence: str) -> list[Any]:
        """Every handler Tk would run for ``sequence``, in installation order."""
        return list(self.bound_handlers.get(sequence, []))

    def create_image(self, x: int, y: int, **kwargs: Any) -> str:
        item = f"image{len(self.image_ids)}"
        self.image_ids.append((x, y, str(kwargs.get("anchor"))))
        return item

    def config(self, **kwargs: Any) -> None:
        self.config_calls.append((int(kwargs["width"]), int(kwargs["height"])))
        if self.on_config is not None:
            self.on_config(**kwargs)
        # The box only moves once the call returns. A ``config`` that raised was
        # never accepted, so the size must not advance either -- the same order
        # the surface keeps for its own ``_size`` (photo_surface.py:81-84).
        self.winfo_size = (int(kwargs["width"]), int(kwargs["height"]))

    def winfo_width(self) -> int:
        return self.winfo_size[0]

    def winfo_height(self) -> int:
        return self.winfo_size[1]

    def winfo_ismapped(self) -> bool:
        return self.mapped

    def winfo_viewable(self) -> bool:
        return self.mapped and self.viewable

    def itemconfig(self, item: Any, **kwargs: Any) -> None:
        self.itemconfig_calls.append((item, kwargs))

    def coords(self, item: Any, *coordinates: Any) -> tuple[Any, ...]:
        """Move the image item, which is how a letterboxed picture is centred.

        Tk answers the requested coordinates, so the double records and returns
        them the same way. A picture that is scaled but never moved keeps its top
        left corner at the canvas origin, so the margins land on the wrong sides.
        """
        self.coords_calls.append((item, coordinates))
        return coordinates

    def delete(self, tag: str) -> None:
        self.delete_calls.append(tag)

    def create_oval(self, *coordinates: Any, **kwargs: Any) -> str:
        self.item_calls.append(("oval", coordinates))
        return "oval"

    def create_rectangle(self, *coordinates: Any, **kwargs: Any) -> str:
        self.item_calls.append(("rectangle", coordinates))
        return "rectangle"

    def destroy(self) -> None:
        self.destroyed += 1


class _PointerEvent:
    """The event a Tk binding is handed, carrying the two fields handlers read.

    Tk builds a *fresh* event for the far side of a generated event, so this
    double does too. A double that handed the original object back would let a
    production path pass the same event along instead of copying the
    coordinates across, and the two are indistinguishable there.

    ``keysym`` and ``delta`` are present on every instance, because
    ``tkinter.Misc._substitute`` assigns both unconditionally -- a production
    branch that picks its options with ``hasattr`` therefore always fires, on
    pointer events too.
    """

    def __init__(self, x: int, y: int, keysym: str = "??", delta: int = 0) -> None:
        self.x = x
        self.y = y
        self.keysym = keysym
        self.delta = delta


def _accepted_generate_options(sequence: str) -> frozenset[str]:
    """The options real ``event generate`` takes for this event type.

    Tk answers ``"<type> event doesn't accept "-<option>" option"`` for any
    other option, which is what makes the per-event split in ``_reemitter``
    load-bearing rather than cosmetic. The table is the one measured against
    the Tk this suite runs on; ``test_tk_only_accepts_the_option_its_own_event``
    is what keeps it honest.
    """
    options = frozenset({"x", "y", "when", "time", "state", "rootx", "rooty"})
    tokens = sequence.strip("<>").split("-")
    if "MouseWheel" in tokens:
        return options | {"delta"}
    if "Key" in tokens or "KeyRelease" in tokens:
        return options | {"keysym"}
    return options


class _HostFrame:
    """A ``tk.Frame`` stand-in that does what Tk does with a host-raised event.

    Tk raises a pointer event on the child under the pointer, so a Canvas
    filling the host runs its own bindings and the host's never fire. This
    double models the far side of that round trip so the contract can be proven
    with no display: ``event_generate`` hands a fresh event at the given
    coordinates to whatever the host has bound, and records it either way.

    ``bind()`` with no sequence answers the tuple the surface reads to decide
    what to forward. Tk answers with its canonical spelling, so the tests bind
    canonical names and get the same string back. ``unbind()`` really drops the
    handler, the way ``CaptureArea.UnbindLeftClick`` drops it from the Frame,
    so a test can hand the surface a binding that has been withdrawn.
    ``event_generate`` refuses the options Tk refuses for that event type, and
    records every option it did accept so a test can assert what the far side
    was handed.
    """

    def __init__(self, **options: Any) -> None:
        self.options = dict(options)
        self.config_calls: list[dict[str, Any]] = []
        self.cursor_history: list[str] = []
        self.raised: list[tuple[str, int, int]] = []
        self.raised_kwargs: list[tuple[str, dict[str, Any]]] = []
        self.delivered: list[tuple[str, int, int]] = []
        self.unbind_calls: list[str] = []
        self.pack_propagate_calls: list[bool] = []
        self._handlers: dict[str, Any] = {}

    def pack_propagate(self, flag: bool | None = None) -> bool | None:
        """Tk と同じく、引数なしは現在値を答え、引数ありは設定を記録する。"""
        if flag is None:
            return not self.pack_propagate_calls or self.pack_propagate_calls[-1]
        self.pack_propagate_calls.append(bool(flag))
        return None

    def bind(
        self, sequence: str | None = None, func: Any = None, add: Any = None
    ) -> Any:
        if sequence is None:
            return tuple(self._handlers)
        self._handlers[sequence] = func
        return sequence

    def unbind(self, sequence: str, funcid: Any = None) -> None:
        """Forget a binding, so the host can shrink as well as grow."""
        self.unbind_calls.append(sequence)
        self._handlers.pop(sequence, None)

    def config(self, **kwargs: Any) -> None:
        self.config_calls.append(kwargs)
        if "cursor" in kwargs:
            self.cursor_history.append(str(kwargs["cursor"]))

    def event_generate(self, sequence: str, **kwargs: Any) -> str:
        # Mirrors Tk's own refusal, so a reemit that hands over an option the
        # event type does not take fails the way it fails on a real display --
        # with TclError, not with a silently dropped field.
        rejected = sorted(
            option
            for option in kwargs
            if option not in _accepted_generate_options(sequence)
        )
        if rejected:
            raise tk.TclError(
                f'{sequence} event doesn\'t accept "-{rejected[0]}" option'
            )
        self.raised_kwargs.append((sequence, dict(kwargs)))
        point = (int(kwargs["x"]), int(kwargs["y"]))
        self.raised.append((sequence, *point))
        handler = self._handlers.get(sequence)
        if handler is not None:
            handler(_PointerEvent(*point))
            self.delivered.append((sequence, *point))
        return ""


@dataclass(frozen=True, slots=True)
class _Attached:
    """A real ``PhotoImageSurface`` wired to a recording canvas."""

    surface: Any
    canvas: _RecordingCanvas
    host: _HostFrame
    photos: list[Any] = field(default_factory=list)


def _attach(
    monkeypatch: pytest.MonkeyPatch,
    size: tuple[int, int] = ATTACHED_SIZE,
    host: _HostFrame | None = None,
) -> _Attached:
    """A surface whose ``attach`` ran against doubles, with the records cleared.

    ``attach`` ends by calling ``resize``, which legitimately configures the
    canvas once. The records are cleared afterwards so every assertion below is
    about the calls the test itself provoked, not about the attach it inherited.

    ``host`` supplies the host's own ``bind`` answers, because ``attach`` reads
    them to decide which pointer events to forward -- so a test about that round
    trip has to hand one in already bound, before ``attach`` looks.
    """
    module = _photo_surface_module()
    photos: list[Any] = []

    class _PhotoImage:
        def __init__(self, image: Any) -> None:
            photos.append(image)

    canvas = _RecordingCanvas()

    def _canvas_factory(master: Any, **options: Any) -> _RecordingCanvas:
        """The stand-in records the options production built the canvas with.

        Both premises in the pointer section -- an empty cursor and no border
        or highlight ring -- are constructor options, so a factory that dropped
        them would leave those tests asserting against an ``options`` the
        production code never passed.
        """
        canvas.host = master
        canvas.options = options
        return canvas

    monkeypatch.setattr(module.tk, "Canvas", _canvas_factory)
    monkeypatch.setattr(module.ImageTk, "PhotoImage", _PhotoImage)

    surface = module.PhotoImageSurface(host=host if host is not None else _HostFrame())
    surface.attach(0, size)
    assert surface._canvas is canvas, "attach did not install the double"
    assert canvas.config_calls == [size], (
        f"attach configured the canvas {canvas.config_calls}, expected [{size}] -- "
        "the fixture's cleared-record precondition is wrong"
    )
    canvas.config_calls.clear()
    return _Attached(surface=surface, canvas=canvas, host=surface._host, photos=photos)


@contextmanager
def _captured_log() -> Iterator[list[str]]:
    """Collect INFO-and-above loguru messages, then detach the sink.

    ``record["message"]`` rather than ``str(message)``: the string form carries a
    timestamp and a ``module:function:line`` prefix, and the contracts below are
    about which messages reach the log and how often.
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


# ===========================================================================
# resize() refuses the sizes that cannot end in a drawn frame
# ===========================================================================


def test_attach_stops_the_canvas_request_from_resizing_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: photo 面は resize のたびに Canvas の要求サイズを表示面の実寸に
    # 合わせる。Canvas はホスト Frame に pack されているので、伝播が生きて
    # いるとその要求がホストを通じて grid へ届き、配置が変わって
    # <Configure> → resize → 要求の変更、と循環する（fit 表示でマウスを押す
    # たびにプレビューが一瞬縮んで戻る不具合の原因）。
    # When: 面をホストへ attach する。
    attached = _attach(monkeypatch)

    # Then: ホストの pack 伝播は止められている。ホストの大きさを決めるのは
    # grid だけになり、GDI 面（子窓は Tk の配置に関与しない）と同じ形になる。
    assert attached.host.pack_propagate_calls == [False], (
        attached.host.pack_propagate_calls
    )


@pytest.mark.parametrize("degenerate", DEGENERATE_SIZES)
def test_resize_to_a_degenerate_size_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    degenerate: tuple[int, int],
) -> None:
    # Given: an attached surface at a good size, with the records cleared.
    attached = _attach(monkeypatch)
    surface, canvas = attached.surface, attached.canvas

    # When: the size a <Configure> sends before geometry settles arrives.
    surface.resize(degenerate)

    # Then: the canvas is not reconfigured at all. A 1x1 (or 0x0, or negative)
    # box is not a smaller preview, it is a box nothing can ever be drawn in, and
    # honouring it is how a surface ends up permanently blank.
    assert canvas.config_calls == [], (
        f"resize({degenerate}) configured the canvas {canvas.config_calls}; the "
        "degenerate size must not reach Tk"
    )

    # Then: and the surface keeps the last size it could actually draw at, so
    # compose still has a box to crop or pad into.
    assert surface.client_size() == ATTACHED_SIZE, surface.client_size()

    # Then: and it is not wedged -- the next good size still takes effect, so the
    # refusal is a skip, not a latch.
    surface.resize((1280, 720))
    assert canvas.config_calls == [(1280, 720)], canvas.config_calls
    assert surface.client_size() == (1280, 720)


def test_resize_to_the_size_it_already_has_configures_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface at a good size, with the records cleared.
    attached = _attach(monkeypatch)
    surface, canvas = attached.surface, attached.canvas

    # When: the same size arrives repeatedly, as a <Configure> storm would.
    for _attempt in range(5):
        surface.resize(ATTACHED_SIZE)

    # Then: Tk is never asked, so the fixed point is reached after one step and
    # every later call is a no-op. A resize that re-issues the same config
    # forever is the other half of an unbounded feedback loop.
    assert canvas.config_calls == [], canvas.config_calls
    assert surface.client_size() == ATTACHED_SIZE

    # Then: and the box the surface reports is the one the canvas was given.
    assert canvas.config_calls == [], "the no-op path must not accumulate records"
    assert canvas.image_ids, "attach created no image item; the fixture is wrong"


def test_resize_before_attach_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a constructed but unattached surface -- no canvas exists yet.
    surface = _photo_surface_module().PhotoImageSurface(host=None)
    assert surface._canvas is None
    assert surface.client_size() == (0, 0)

    # When: a size arrives before attach.
    surface.resize((1280, 720))

    # Then: nothing is recorded as the size, because there is no box to hold it.
    # A surface that remembers a size it was never given has a _size compose
    # will pad a frame into -- a box that does not exist.
    assert surface.client_size() == (0, 0), (
        f"resize before attach recorded {surface.client_size()}; the size must "
        "only be recorded once a canvas has taken it"
    )
    assert surface._canvas is None


# ===========================================================================
# resize() is bounded: it cannot nest, and the guard always comes off
# ===========================================================================


def test_a_reentrant_resize_is_refused_instead_of_nesting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface whose canvas re-enters resize from config --
    # the only way a Tk callback can come back through this method.
    attached = _attach(monkeypatch)
    surface, canvas = attached.surface, attached.canvas
    reentries: list[tuple[int, int]] = []

    def _reenter(**kwargs: Any) -> None:
        reentries.append((int(kwargs["width"]), int(kwargs["height"])))
        surface.resize((1920, 1080))

    canvas.on_config = _reenter

    # When: the outer resize runs. Unbounded nesting would end in RecursionError
    # raised out of resize; the point of the guard is that it does not.
    surface.resize((1280, 720))

    # Then: the nested call is refused, so config is reached exactly once and
    # the re-entry is observable as a refusal rather than as a stack overflow.
    assert canvas.config_calls == [(1280, 720)], (
        f"config was reached {canvas.config_calls}; the nested resize must not "
        "reach Tk a second time"
    )
    assert reentries == [(1280, 720)], (
        f"config re-entered {len(reentries)} time(s); exactly one proves the "
        "inner call returned instead of recursing"
    )

    # Then: and the outer size wins, not the nested one, so the surface settles
    # on the size the caller asked for last from outside the guard.
    assert surface.client_size() == (1280, 720), surface.client_size()


def test_the_reentrancy_guard_is_cleared_after_a_normal_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface, with a resize that has re-entered once.
    attached = _attach(monkeypatch)
    surface, canvas = attached.surface, attached.canvas
    canvas.on_config = lambda **_: surface.resize((1920, 1080))
    surface.resize((1280, 720))
    canvas.config_calls.clear()

    # When: the next resize arrives, with the re-entry removed.
    canvas.on_config = None
    surface.resize((1920, 1080))

    # Then: it reaches Tk, so the guard is per-call and not a one-shot latch that
    # would freeze the surface after a single nested attempt.
    assert canvas.config_calls == [(1920, 1080)], canvas.config_calls
    assert surface.client_size() == (1920, 1080)


def test_the_reentrancy_guard_is_cleared_when_the_resize_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface whose canvas raises from config, the way a Tk
    # call raises on a destroyed interpreter.
    attached = _attach(monkeypatch)
    surface, canvas = attached.surface, attached.canvas

    def _explode(**_kwargs: Any) -> None:
        raise RuntimeError("tk interpreter went away")

    canvas.on_config = _explode

    # When: a resize runs into it.
    with pytest.raises(RuntimeError, match="tk interpreter went away"):
        surface.resize((1280, 720))

    # Then: the size is unchanged, because the canvas never accepted the new
    # one. A _size that ran ahead of the canvas would leave compose padding a
    # frame into a box the canvas does not have.
    assert surface.client_size() == ATTACHED_SIZE, surface.client_size()

    # Then: and the guard is off again, so the surface still resizes once Tk
    # recovers. A guard cleared in a bare "after the call" statement, rather than
    # in a finally, leaves every later resize permanently refused.
    # config_calls counts attempts, so the attempt that raised is dropped before
    # the recovery call is measured.
    canvas.on_config = None
    canvas.config_calls.clear()
    surface.resize((1280, 720))
    assert canvas.config_calls == [(1280, 720)], canvas.config_calls
    assert surface.client_size() == (1280, 720)


# ===========================================================================
# compose() refuses a frame it cannot copy, in its own words
# ===========================================================================


def test_a_non_contiguous_frame_is_refused_with_its_own_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface and a frame of the capture size whose rows are
    # strided rather than packed.
    from core.preview_renderer import OverlayState

    attached = _attach(monkeypatch)
    width, height = _capture_size()
    frame = _non_contiguous_frame(width, height)

    # When: it is composited. A raise here fails the test; the contract is that
    # nothing raises out of compose.
    result = attached.surface.compose(frame, OverlayState())

    # Then: the refusal is named for its own cause. "dimension_mismatch" would
    # send an operator to the camera's resolution, which is exactly right here;
    # a non-contiguous frame is a buffer-layout fault, not a size one.
    assert (result.ok, result.detail) == (False, "frame_not_contiguous"), (
        f"compose returned ok={result.ok} detail={result.detail!r}; a "
        "non-contiguous frame must be refused as frame_not_contiguous"
    )

    # Then: and the fixture is the shape it claims to be, so the refusal cannot
    # be a size check wearing the wrong detail.
    assert frame.shape[1::-1] == (width, height)


def test_a_non_contiguous_frame_never_reaches_the_present_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface that has already composited one good frame.
    from core.preview_renderer import OverlayState

    attached = _attach(monkeypatch)
    width, height = _capture_size()
    overlay = OverlayState()
    first = attached.surface.compose(_contiguous_frame(width, height), overlay)
    assert first.ok is True, first.detail
    attached.surface.present()
    assert attached.canvas.itemconfig_calls, "the control frame never reached Tk"

    # When: a strided frame arrives, and present is asked as the caller would.
    refused = attached.surface.compose(_non_contiguous_frame(width, height), overlay)
    presented = attached.surface.present()

    # Then: nothing new is on the canvas, because the refused frame staged
    # nothing. Presenting the previous frame's PhotoImage instead would be a lie
    # about which frame is on screen.
    assert refused.ok is False
    assert (presented.ok, presented.detail) == (False, "no_frame"), presented
    assert len(attached.canvas.itemconfig_calls) == 1, (
        attached.canvas.itemconfig_calls,
        "a refused frame still put a PhotoImage on the canvas",
    )


def test_a_contiguous_frame_of_the_capture_size_still_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface and a well-formed frame.
    from core.preview_renderer import OverlayState

    attached = _attach(monkeypatch)
    width, height = _capture_size()
    frame = _contiguous_frame(width, height)

    # When: it is composited.
    result = attached.surface.compose(frame, OverlayState())

    # Then: it is accepted, so the contiguity check refuses the strided frame
    # and not every frame -- a check that rejects the healthy path is worse than
    # no check, because it blanks the preview.
    assert (result.ok, result.detail) == (True, "ok"), result


# ===========================================================================
# The picture is scaled into the viewport, centred, and margined in black
# ===========================================================================
#
# ``test_capture_area_surface_contract.py:1422`` used to pin a 1:1 crop here,
# through a fixture that calls ``resize`` *before* it installs a canvas
# (``:1447`` then ``:1448``). The guards above make that ordering a no-op, which
# is correct -- a size handed to a surface with no box must not be recorded as
# if it had been -- so the older fixture no longer describes a state the
# production API can produce. The contract itself is carried here as well, over
# the real ``attach`` path: ``fit_rect`` decides where inside the viewport the
# picture sits, ``compose`` scales to exactly that size, ``present`` moves the
# image item there, and the Canvas background supplies the margins.
#
# The overlay is scaled with the picture. ``OverlayState`` carries capture
# coordinates (0..1280, 0..720) while the Canvas is in viewport pixels, so an
# overlay that is drawn unscaled lands in the wrong place the moment the two
# sizes differ -- which is always.


def test_a_capture_frame_is_scaled_down_to_fill_a_smaller_show_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface at a show size smaller than the capture size,
    # and a live frame at the capture size.
    from core.preview_renderer import OverlayState

    show_size = (640, 360)
    attached = _attach(monkeypatch, size=show_size)
    width, height = _capture_size()
    assert (width, height) != show_size, (
        f"the capture size {width}x{height} equals the show size {show_size}, so "
        "the scaling path below is never taken"
    )
    frame = _contiguous_frame(width, height)

    # When: it is composited and presented.
    composed = attached.surface.compose(frame, OverlayState())
    presented = attached.surface.present()

    # Then: both succeed, so <Configure> cannot be what blanks this backend
    # either.
    assert (composed.ok, presented.ok) == (True, True), (composed, presented)

    # Then: and the whole frame is scaled into the whole box rather than
    # cropped, so nothing of the capture is cut away.
    image = attached.photos[-1]
    assert image.size == show_size, image.size
    painted = np.asarray(image)[:, :, ::-1]
    assert painted.shape == (show_size[1], show_size[0], 3), painted.shape

    # Then: and it is placed at the origin, because 640x360 is exactly 16:9 and
    # therefore has no margin to be offset by.
    assert attached.canvas.coords_calls, (
        "the image item was never moved; a scaled picture still needs to be "
        "placed, and this one happens to belong at (0, 0)"
    )
    _item, coordinates = attached.canvas.coords_calls[-1]
    assert tuple(coordinates) == (0, 0), attached.canvas.coords_calls


def test_a_capture_frame_is_scaled_and_centred_in_a_non_sixteen_by_nine_show_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a viewport wider than 16:9 allows, so the picture fits by height and
    # the margins are top and bottom.
    from core.preview_renderer import OverlayState

    viewport = (1000, 700)
    attached = _attach(monkeypatch, size=viewport)
    width, height = _capture_size()
    frame = _contiguous_frame(width, height)

    # When: a frame is composited and presented.
    composed = attached.surface.compose(frame, OverlayState())
    assert composed.ok is True, composed.detail
    assert attached.surface.present().ok is True

    # Then: the picture is 1000x563 -- 1000 wide, 1000*720/1280 = 562.5 rounded
    # down -- and it is moved down to y=68, because (700-563)//2 = 68 rows of
    # margin sit above it. Leaving the item at the origin would put every
    # margin below the picture instead of splitting them.
    image = attached.photos[-1]
    assert image.size == (1000, 563), image.size
    _item, coordinates = attached.canvas.coords_calls[-1]
    assert tuple(coordinates) == (0, 68), attached.canvas.coords_calls

    # Then: and the margin is the canvas background rather than part of the
    # image, so the surface never pays for the bars with a bigger picture.
    assert attached.canvas.options["background"] == "black", attached.canvas.options


def test_the_overlay_is_scaled_with_the_picture_and_offset_by_its_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a viewport of 1000x700, whose picture is 1000x563 at y=68 -- a
    # scale of 1000/1280 = 0.781 in x and 563/720 in y, plus a 68 row offset.
    from core.preview_renderer import OverlayState, RectState, StickState

    viewport = (1000, 700)
    attached = _attach(monkeypatch, size=viewport)
    width, height = _capture_size()
    overlay = OverlayState(
        left_stick=StickState(
            active=True, center_x=640, center_y=360, radius=60, knob_x=660, knob_y=350
        ),
        guide=RectState(x0=0, y0=0, x1=1280, y1=720, visible=True),
    )

    # When: it is composited and presented.
    composed = attached.surface.compose(_contiguous_frame(width, height), overlay)
    assert composed.ok is True, composed.detail
    assert attached.surface.present().ok is True

    # Then: the stick ring follows the scale and the margin: 640*1000/1280 = 500,
    # 68 + 360*563/720 = 68 + 281.5 -> 350, and the radius
    # round(60*1000/1280) = 47. Drawn unscaled the circle would sit at (640, 360)
    # with r=60 -- 140 px right of the stick the user is holding.
    ring, knob, guide = attached.canvas.item_calls
    assert ring[0] == "oval", attached.canvas.item_calls
    assert tuple(ring[1]) == (500 - 47, 350 - 47, 500 + 47, 350 + 47)

    # Then: and the knob scales with it, keeping the ring/10 relationship in the
    # space the user can actually see: 660 -> 516, 350 -> 68+274 = 342, and
    # 47//10 = 4.
    assert (knob[0], tuple(knob[1])) == ("oval", (516 - 4, 342 - 4, 516 + 4, 342 + 4))

    # Then: and a rect spanning the whole capture frame maps onto the whole
    # fitted rect, which is the one case whose answer is checkable by hand:
    # (0,0) -> (0,68) and (1280,720) -> (1000, 68+563).
    assert (guide[0], tuple(guide[1])) == ("rectangle", (0, 68, 1000, 631))


def test_compose_refuses_a_viewport_with_no_room_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a surface attached at a viewport of (0, 0). resize refuses it, so
    # the fitted rect is never computed -- the state a caller that hands attach a
    # nonsense size produces.
    from core.preview_renderer import OverlayState

    module = _photo_surface_module()
    photos: list[Any] = []

    class _PhotoImage:
        def __init__(self, image: Any) -> None:
            photos.append(image)

    monkeypatch.setattr(module.tk, "Canvas", _RecordingCanvas)
    monkeypatch.setattr(module.ImageTk, "PhotoImage", _PhotoImage)
    surface = module.PhotoImageSurface(host=_HostFrame())
    surface.attach(0, (0, 0))
    assert surface._dest == (0, 0, 0, 0), surface._dest

    # When: a perfectly good capture frame is composited into it.
    width, height = _capture_size()
    result = surface.compose(_contiguous_frame(width, height), OverlayState())

    # Then: it is refused under its own detail and nothing is raised. cv2.resize
    # answers a zero destination with an exception, and an exception out of
    # compose ends the preview update path instead of skipping one frame. This
    # is a guard added with the scaling path, not a behaviour it removed: the
    # 1:1 crop it replaced returned a 0x0 image instead.
    assert (result.ok, result.detail) == (False, "no_room"), result
    assert photos == [], photos


def test_a_capture_frame_at_the_capture_size_is_not_scaled_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a viewport of exactly the capture size, the fast path.
    from core.preview_renderer import OverlayState, StickState

    attached = _attach(monkeypatch, size=(1280, 720))
    width, height = _capture_size()
    overlay = OverlayState(
        left_stick=StickState(
            active=True, center_x=640, center_y=360, radius=60, knob_x=660, knob_y=350
        )
    )
    frame = _contiguous_frame(width, height)

    # When: it is composited and presented.
    composed = attached.surface.compose(frame, overlay)
    assert composed.ok is True, composed.detail
    assert attached.surface.present().ok is True

    # Then: the picture is the frame, unmodified and pixel for pixel, so the
    # per-frame conversion is the only cost.
    image = attached.photos[-1]
    assert image.size == (width, height), image.size
    assert np.array_equal(np.asarray(image)[:, :, ::-1], frame)

    # Then: and the overlay is drawn at capture coordinates, because at 1:1 the
    # scale is 1 and the offset 0. This is the control for the scaling test
    # above: without it, a surface that scaled by a constant factor and nothing
    # else would pass both.
    ring, knob = attached.canvas.item_calls
    assert tuple(ring[1]) == (640 - 60, 360 - 60, 640 + 60, 360 + 60), ring
    assert tuple(knob[1]) == (660 - 6, 350 - 6, 660 + 6, 350 + 6), knob


# ===========================================================================
# present() names the two refusals apart
# ===========================================================================


def test_present_before_compose_reports_no_frame_not_no_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface with nothing composited.
    attached = _attach(monkeypatch)

    # When: present is asked for a frame that was never composed.
    result = attached.surface.present()

    # Then: the canvas exists, so "no_hwnd" would be false.
    assert attached.surface._canvas is not None, "the fixture is not attached"

    # Then: and the detail is the one that is true. An operator reading
    # "no_hwnd" goes looking at attach; the surface is attached and the caller
    # simply presented nothing.
    assert (result.ok, result.detail) == (False, "no_frame"), (
        f"present returned ok={result.ok} detail={result.detail!r}; an attached "
        "surface with nothing staged must say no_frame, not no_hwnd"
    )

    # Then: and no canvas call was made, so there is nothing to have drawn.
    assert attached.canvas.itemconfig_calls == []
    assert attached.canvas.item_calls == []


def test_present_on_an_unattached_surface_still_reports_no_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a constructed but unattached surface.
    surface = _photo_surface_module().PhotoImageSurface(host=None)

    # When: present is asked.
    result = surface.present()

    # Then: the unattached case keeps its own detail, so the two refusals stay
    # distinguishable and the reordering below did not merge them.
    assert (result.ok, result.detail) == (False, "no_hwnd"), result


def test_compose_on_an_unattached_surface_still_reports_no_hwnd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a constructed but unattached surface.
    from core.preview_renderer import OverlayState

    surface = _photo_surface_module().PhotoImageSurface(host=None)
    width, height = _capture_size()

    # When: compose is asked with a valid contiguous frame.
    result = surface.compose(_contiguous_frame(width, height), OverlayState())

    # Then: the unattached case keeps its own detail, same as present.
    assert (result.ok, result.detail) == (False, "no_hwnd"), result


def test_a_second_present_after_one_frame_reports_no_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface that has composed and presented one frame.
    from core.preview_renderer import OverlayState

    attached = _attach(monkeypatch)
    width, height = _capture_size()
    composed = attached.surface.compose(
        _contiguous_frame(width, height), OverlayState()
    )
    assert composed.ok is True, composed.detail
    assert attached.surface.present().ok is True
    assert len(attached.canvas.itemconfig_calls) == 1

    # When: present is called again with nothing new composed.
    result = attached.surface.present()

    # Then: it says no_frame, because the gate was cleared by the present that
    # did draw. A gate that is only ever set would make every frame after the
    # first a silent no-op that still reports success.
    assert (result.ok, result.detail) == (False, "no_frame"), result

    # Then: and the canvas was not touched a second time.
    assert len(attached.canvas.itemconfig_calls) == 1, attached.canvas.itemconfig_calls


# ===========================================================================
# client_size() reports the live box, not the box that was asked for
# ===========================================================================
#
# ``gdi_surface.py:759-763`` measures the child with ``GetClientRect`` on every
# call, so the number moves with the window even though ``_size`` -- the box the
# back buffer was built at -- does not. The fallback reported ``_size``, which is
# a different truth: the canvas is packed ``fill=BOTH, expand=True``
# (``photo_surface.py:54``), so the geometry manager owns the real size and
# ``resize``'s request is only what was asked for. A caller asking why a frame
# lands in the wrong box needs the box the pixels actually go into.


def test_client_size_follows_the_canvas_and_not_the_requested_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface whose canvas has been given a different box by
    # the geometry manager, which is the ordinary case once one settles.
    attached = _attach(monkeypatch)
    assert attached.surface.client_size() == ATTACHED_SIZE

    # When: the canvas moves behind the surface's back.
    attached.canvas.winfo_size = (800, 450)

    # Then: the surface reports where the frame is drawn, so a disagreement
    # between the two boxes is visible from outside instead of needing a
    # debugger. Reporting ``_size`` here would answer a question nobody asked.
    assert attached.surface.client_size() == (800, 450), (
        f"client_size() reported {attached.surface.client_size()} after the canvas "
        f"moved to {attached.canvas.winfo_size}; the live box is the one the frame "
        "is drawn into, and the requested one is what resize asked for"
    )


def test_client_size_is_zero_while_no_canvas_exists() -> None:
    # Given: a constructed but unattached surface.
    surface = _photo_surface_module().PhotoImageSurface(host=None)

    # Then: it reports (0, 0), which is the same answer GdiSurface gives for the
    # same state (``gdi_surface.py:760-761``). Reading winfo with no guard
    # raises AttributeError out of a method the protocol promises to be safe to
    # call at any time.
    assert surface.client_size() == (0, 0)


# ===========================================================================
# A refused frame is logged once, not once per frame
# ===========================================================================
#
# ``compose`` runs per frame, so an unthrottled warning at a refusal emits
# thousands of lines a minute and buries the one line that matters.
# ``GdiSurface._note_failure`` (``gdi_surface.py:888-893``) keys a set by the
# detail, so each cause is recorded once and its repeats are silent. The
# fallback logged nothing at all, which left a permanently blank preview on
# mac/Linux with no trace of why.


def test_one_kind_of_refusal_is_logged_once_however_many_frames_hit_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface, and a frame whose rows are strided.
    from core.preview_renderer import OverlayState

    attached = _attach(monkeypatch)
    width, height = _capture_size()
    refused = _non_contiguous_frame(width, height)

    # When: the same strided frame is composited five times, as a stuck capture
    # would.
    with _captured_log() as messages:
        for _attempt in range(5):
            result = attached.surface.compose(refused, OverlayState())
            assert result.detail == "frame_not_contiguous"

    # Then: one line, because it is one cause. Five refusals of the same cause
    # are five copies of the same fact, and per-frame logging would bury the
    # facts that are not repeats.
    assert len(messages) == 1, f"five identical refusals logged {messages!r}"


def test_a_second_kind_of_refusal_is_still_logged_after_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a surface that has already refused a strided frame, so the dedup
    # key for that cause is spent.
    from core.preview_renderer import OverlayState

    attached = _attach(monkeypatch)
    width, height = _capture_size()
    attached.surface.compose(_non_contiguous_frame(width, height), OverlayState())

    # When: a frame of the wrong resolution arrives -- a different cause, with a
    # different fix.
    odd = (width + 1, height)
    with _captured_log() as messages:
        result = attached.surface.compose(_contiguous_frame(*odd), OverlayState())

    # Then: it is logged, because the key is the cause and not "we already said
    # something". A set that only remembered that it had spoken would hide the
    # second fault for the rest of the session.
    assert result.detail == "dimension_mismatch", result
    assert len(messages) == 1, f"the second cause logged {messages!r}"

    # Then: and the line carries the size that was thrown away, the only number
    # an operator can act on. A count of refusals would say how many, never
    # which resolution the camera came back with.
    assert f"{odd[0]}x{odd[1]}" in messages[0], messages[0]


# ===========================================================================
# self_test() answers in GdiSurface's result shape
# ===========================================================================
#
# ``GuiAssets.py:421-427`` logs the surface's ``self_test()`` outcome at startup,
# duck-typed through ``hasattr`` precisely so the six-member ``PreviewRenderer``
# protocol (``test_gdi_surface_contract.py:1033-1072``) need not grow a
# seventh. With no ``self_test`` here, that startup record reads
# ``outcome=not_supported`` on every mac/Linux boot -- and the outcome is the
# one field that says whether a user can see the preview at all.
#
# The two backends cannot read the same thing: GDI writes a sentinel into its
# back buffer and reads it back through the child DC
# (``gdi_surface.py:1035-1068``), which no Canvas offers. What a Canvas can be
# asked is the truth underneath that verdict -- is any part of the preview on
# screen. ``fully_occluded`` keeps GdiSurface's field name and here means "none
# of the preview is visible", which is the fact GDI's ``covered`` outcome
# reports.


def test_self_test_before_attach_says_nothing_has_run_yet() -> None:
    # Given: a constructed but unattached surface.
    surface = _photo_surface_module().PhotoImageSurface(host=None)

    # Then: GuiAssets finds the method by hasattr, ...
    assert hasattr(surface, "self_test")

    # Then: and the answer carries the three fields GdiSurface's
    # ``SelfTestResult`` carries, so the startup log needs no second shape and
    # no second reader.
    result = surface.self_test()
    assert isinstance(result.outcome, str)
    assert result.outcome == "not_run", result.outcome
    assert result.fully_occluded is None
    assert result.clip_box == (0, 0, 0, 0)


def test_self_test_on_a_mapped_canvas_reports_the_box_it_is_showing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface whose canvas Tk has put on screen.
    attached = _attach(monkeypatch)
    attached.canvas.mapped = True
    attached.canvas.viewable = True

    # When: the self-test is asked what the user can see.
    result = attached.surface.self_test()

    # Then: the surface is visible, in the box it is visible in, so the startup
    # record separates "seen" from "never shown" -- the two states a blank
    # preview report is otherwise indistinguishable between.
    assert result.outcome == "visible", result.outcome
    assert result.fully_occluded is False
    assert result.clip_box == (0, 0, *ATTACHED_SIZE), result.clip_box


@pytest.mark.parametrize(
    ("mapped", "box", "outcome", "clip_box"),
    [
        (False, ATTACHED_SIZE, "not_mapped", (0, 0, 0, 0)),
        (True, (1, 1), "empty_box", (0, 0, 1, 1)),
    ],
)
def test_self_test_reports_nothing_visible_when_the_canvas_is_not_on_screen(
    monkeypatch: pytest.MonkeyPatch,
    mapped: bool,
    box: tuple[int, int],
    outcome: str,
    clip_box: tuple[int, int, int, int],
) -> None:
    # Given: an attached surface whose canvas is not on screen -- never mapped,
    # as during app construction, or mapped into a box with no room in it.
    attached = _attach(monkeypatch)
    attached.canvas.mapped = mapped
    attached.canvas.viewable = mapped
    attached.canvas.winfo_size = box

    # When: the self-test is asked.
    result = attached.surface.self_test()

    # Then: it says which of the two it is, and reports that no part of the
    # preview can be seen. That is the verdict worth having: frames may be
    # arriving, drawing fine, and simply never reaching the screen.
    assert result.outcome == outcome, result.outcome
    assert result.fully_occluded is True
    assert result.clip_box == clip_box, result.clip_box


def test_self_test_reports_not_mapped_when_mapped_but_not_viewable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface whose canvas is mapped but whose ancestor is
    # minimized. winfo_ismapped() is True; winfo_viewable() is False.
    attached = _attach(monkeypatch)
    attached.canvas.mapped = True
    attached.canvas.viewable = False
    attached.canvas.winfo_size = ATTACHED_SIZE

    # When: the self-test is asked.
    result = attached.surface.self_test()

    # Then: the outcome is not_mapped, not visible. If the implementation used
    # winfo_ismapped() instead of winfo_viewable(), this would return visible.
    assert result.outcome == "not_mapped", result.outcome
    assert result.fully_occluded is True
    assert result.clip_box == (0, 0, 0, 0), result.clip_box


# ===========================================================================
# An attach that cannot build its canvas is visible to the user
# ===========================================================================
#
# ``GdiSurface.attach`` warns to the log *and* prints when the child window
# cannot be made (``gdi_surface.py:659-667``): a warning only reaches the file
# log, so a user whose preview never appears has no way to learn why from the
# screen. The fallback let the ``TclError`` out of ``attach``, which on a
# headless box ends the app at construction instead of degrading to a blank
# preview that says why.


def test_an_attach_that_cannot_build_a_canvas_warns_and_prints(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a Tk that cannot make a Canvas at all -- no display, or an
    # interpreter that has gone away.
    module = _photo_surface_module()

    def _no_canvas(*_args: Any, **_kwargs: Any) -> Any:
        raise module.tk.TclError("no display name and no $DISPLAY")

    monkeypatch.setattr(module.tk, "Canvas", _no_canvas)
    surface = module.PhotoImageSurface(host="host-double")

    # When: attach runs into it.
    with _captured_log() as messages:
        surface.attach(0, ATTACHED_SIZE)

    # Then: the file log records it, with the stage named the way the GDI side
    # names its own, so one search covers both backends.
    assert len(messages) == 1, f"the failed attach logged {messages!r}"
    assert "stage=attach" in messages[0], messages[0]

    # Then: and the GUI gets a line of its own, because a line in the file is
    # not something a user staring at a blank preview will ever read.
    assert capsys.readouterr().out.strip(), (
        "attach failed without printing anything the user could read"
    )

    # Then: and the surface is left unattached rather than half-built, so the
    # per-frame refusals go on naming the truth: there is no box at all, not a
    # box that is broken.
    assert surface._canvas is None
    assert surface.client_size() == (0, 0)


# ===========================================================================
# The module stays importable with no Tk interpreter
# ===========================================================================


def test_importing_the_module_constructs_no_tk_interpreter() -> None:
    # Given: a fresh interpreter where every Tk constructor is a trap.
    probe = "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {_SERIAL_CONTROLLER!r})",
            "import tkinter",
            "class _Banned:",
            "    def __init__(self, *a, **k):",
            "        raise AssertionError('a Tk object was constructed at import')",
            "tkinter.Tk = _Banned",
            "tkinter.Toplevel = _Banned",
            "tkinter.Canvas = _Banned",
            "tkinter.Frame = _Banned",
            "import ui.photo_surface",
            "print('imported')",
        ]
    )

    # When: the module is imported.
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    # Then: it imports, so the fallback can be selected by a host that has no
    # display yet. A module-level Canvas or Tk would make the import itself the
    # crash, before any contract above could run.
    assert completed.returncode == 0, (
        f"importing ui.photo_surface with Tk constructors banned failed:\n"
        f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
    )
    assert "imported" in completed.stdout, completed.stdout


# ===========================================================================
# The picture is not a hole in the host's input
# ===========================================================================
#
# GDI's video lives in a foreign HWND, which Tk does not know about, so every
# click on it lands on the Frame and the six bound handlers run on Windows.
# The fallback's Canvas is a real Tk child filling the same box, so Tk raises
# the pointer event on the *Canvas*: ``host.bind()`` still lists the sequences
# and none of them ever fire. Stick drag, range select and the colour probe are
# all dead over the picture, and the cursor never becomes a dot, because the
# handlers that change it are the ones that never run.
#
# The fix has to be generic. ``CaptureArea`` rebinds at runtime
# (``BindLeftClick`` / ``BindRightClick``, ``GuiAssets.py:1398-1410``) and a
# second host will bind sequences this file has never heard of, so a
# hand-copied list of the six would be correct only until the next binding.
# The surface therefore reads ``host.bind()`` and re-emits.
#
# Doubles, not a real Tk root, because a real one cannot answer honestly here:
# ``event generate`` dispatches only to a *mapped* toplevel, and a headless box
# has none, so a live-root assertion would be either an error or -- worse --
# vacuously green. A real Tk was used once to establish the two facts these
# contracts lean on: the generated event arrives with ``x``/``y`` equal to what
# was passed, and dispatching on the host does not re-enter the canvas binding
# (the canvas is not among the host's bindtags), so the round trip terminates.


@pytest.mark.parametrize("sequence", POINTER_SEQUENCES + MODIFIER_SEQUENCES)
def test_a_pointer_event_over_the_canvas_reaches_the_host_handler(
    monkeypatch: pytest.MonkeyPatch, sequence: str
) -> None:
    # Given: an attached surface whose host has this sequence bound, the way
    # CaptureArea binds its own stick, range-select and probe sequences
    # (GuiAssets.py:393-397, :1400-1402, :1407-1409).
    host = _HostFrame()
    host.bind(sequence, lambda event: None)
    attached = _attach(monkeypatch, host=host)

    # Then: the canvas is bound to it too. This is the precondition the whole
    # section rests on, and it is a RED on its own: a canvas with no bindings
    # is Tk raising the event on a widget nobody is listening to.
    assert sequence in attached.canvas.bindings, (
        f"the canvas is bound to {sorted(attached.canvas.bindings)}; a pointer "
        f"event over the picture stops there and {sequence} never reaches the host"
    )

    # When: the pointer goes down well inside the picture.
    attached.canvas.bindings[sequence](_PointerEvent(37, 211))

    # Then: the host's own handler ran, with the coordinates the canvas saw.
    assert host.delivered == [(sequence, 37, 211)], (
        f"{sequence} over the canvas reached {host.delivered!r}; the host's "
        "handler has to be run with the same x/y the canvas reported"
    )


@pytest.mark.parametrize("point", [(0, 0), (1, 1), (639, 359)])
def test_a_forwarded_event_carries_the_canvas_coordinates_unchanged(
    monkeypatch: pytest.MonkeyPatch, point: tuple[int, int]
) -> None:
    # Given: an attached surface whose host drags on <B1-Motion>, the sequence
    # every stick and range gesture spends most of its time on.
    host = _HostFrame()
    host.bind("<B1-Motion>", lambda event: None)
    attached = _attach(monkeypatch, host=host)
    x, y = point

    # When: the pointer moves over the picture.
    attached.canvas.bindings["<B1-Motion>"](_PointerEvent(x, y))

    # Then: the coordinates arrive as they were raised, at both corners and the
    # far edge, so nothing is shifted or clamped on the way. Every handler reads
    # them as a show-space point to convert (GuiAssets.py:853, :948, :1252), so
    # a constant added in transit would be a silent stick offset, a range box
    # drawn one pixel out, or a probe reading the neighbouring pixel.
    assert host.delivered == [("<B1-Motion>", x, y)], host.delivered

    # Then: and "unchanged" is arithmetic rather than an agreement between two
    # doubles -- the canvas is built with no border and no highlight ring, so it
    # sits at the host's origin (the host itself carries the same two, pinned by
    # test_capture_area_init_creates_no_photo_image). A border would make
    # canvas-relative and host-relative differ by exactly that border, and no
    # test above would notice.
    assert attached.canvas.options["borderwidth"] == 0, attached.canvas.options
    assert attached.canvas.options["highlightthickness"] == 0, attached.canvas.options


def test_the_canvas_takes_the_hosts_pointer_bindings_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host that binds everything CaptureArea binds, plus the
    # <Configure> that Tk raises on the host itself (GuiAssets.py:452).
    host = _HostFrame()
    for sequence in (*POINTER_SEQUENCES, *MODIFIER_SEQUENCES, "<Configure>"):
        host.bind(sequence, lambda event: None)
    attached = _attach(monkeypatch, host=host)

    # Then: the canvas is bound to every pointer sequence and to nothing else.
    # <Configure> is the one that must be left out: Tk raises it on the host
    # when the host's own box moves, and it is what calls resize
    # (GuiAssets.py:481), so forwarding it would hand the surface its own size
    # changes back and re-enter the resize this file already guards.
    assert set(attached.canvas.bindings) - {"<Destroy>"} == set(
        POINTER_SEQUENCES + MODIFIER_SEQUENCES
    ), (
        f"the canvas is bound to {sorted(attached.canvas.bindings)}; it must "
        "mirror the host's pointer bindings and exclude the events Tk raises on "
        "the host itself, <Configure> among them"
    )
    assert "<Destroy>" in attached.canvas.bindings, (
        "the surface no longer watches its own canvas destruction, so a "
        "destroyed canvas raises TclError instead of taking the no_hwnd path"
    )


def test_structural_and_hover_events_are_never_forwarded_to_the_canvas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host that binds pointer sequences plus structural, hover and
    # focus ones a real app may carry.
    host = _HostFrame()
    for sequence in (
        *POINTER_SEQUENCES,
        "<Enter>",
        "<Leave>",
        "<FocusIn>",
        "<FocusOut>",
        "<Expose>",
        "<Map>",
    ):
        host.bind(sequence, lambda event: None)
    attached = _attach(monkeypatch, host=host)

    # When/Then: only the pointer family reaches the canvas. An exclusion list
    # can only name what its author remembered; an allowlist cannot forward
    # what it never names, so a future <Enter> handler stays on the host.
    assert set(attached.canvas.bindings) - {"<Destroy>"} == set(POINTER_SEQUENCES), (
        f"the canvas is bound to {sorted(attached.canvas.bindings)}"
    )


def test_keymap_is_not_forwarded_despite_containing_key_substring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host that binds <Keymap>, which contains "Key" as a substring.
    host = _HostFrame()
    host.bind("<Keymap>", lambda event: None)
    attached = _attach(monkeypatch, host=host)

    # When/Then: <Keymap> is NOT forwarded. Substring matching would have
    # forwarded it because "Key" in "<Keymap>" is True. Exact token matching
    # splits on "-" and compares each token, so "Keymap" != "Key".
    assert "<Keymap>" not in attached.canvas.bindings, (
        f"<Keymap> was forwarded: {sorted(attached.canvas.bindings)}"
    )


def test_canvas_destroy_releases_the_surface_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface.
    attached = _attach(monkeypatch)
    surface = attached.surface

    # When: Tk destroys the canvas out from under the surface.
    destroy = attached.canvas.bindings.get("<Destroy>")
    assert destroy is not None, (
        "the canvas has no <Destroy> binding, so a destroyed canvas keeps "
        "answering itemconfig/config with TclError instead of no_hwnd"
    )
    destroy(_PointerEvent(0, 0))

    # Then: present/resize take the unattached path instead of raising.
    assert surface.present().detail == "no_hwnd"
    surface.resize((800, 600))
    assert surface.client_size() == (0, 0)


def test_a_binding_the_app_never_uses_is_forwarded_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host with one binding that appears nowhere in the tree.
    host = _HostFrame()
    host.bind(UNUSED_SEQUENCE, lambda event: None)
    attached = _attach(monkeypatch, host=host)

    # When: the pointer drags with Shift held.
    attached.canvas.bindings[UNUSED_SEQUENCE](_PointerEvent(11, 13))

    # Then: it is delivered, so the surface reads the host's bindings instead of
    # carrying a copy of the twelve above. A hand-written list would pass every
    # test in this section and fail the first time a binding is added -- which
    # is the whole reason the list is not written down.
    assert host.delivered == [(UNUSED_SEQUENCE, 11, 13)], host.delivered


def test_a_pointer_reemit_hands_the_host_coordinates_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host bound to every pointer and modifier sequence it carries.
    sequences = POINTER_SEQUENCES + MODIFIER_SEQUENCES + (UNUSED_SEQUENCE,)
    host = _HostFrame()
    for sequence in sequences:
        host.bind(sequence, lambda event: None)
    attached = _attach(monkeypatch, host=host)

    # When: each one is raised over the picture.
    for sequence in sequences:
        attached.canvas.bindings[sequence](_PointerEvent(23, 47))

    # Then: every reemit carried only what a pointer event accepts. Tk refuses
    # "-keysym" and "-delta" on these types, so one of them would raise and
    # swallow the gesture rather than merely arrive with a junk field -- which
    # is why the branch in _reemit is keyed on the event type and not on what
    # the event object happens to carry.
    assert [sequence for sequence, _ in host.raised_kwargs] == list(sequences), (
        host.raised_kwargs
    )
    for sequence, kwargs in host.raised_kwargs:
        assert set(kwargs) == {"x", "y", "when"}, (sequence, sorted(kwargs))


def test_a_key_reemit_carries_the_keysym_and_a_wheel_reemit_carries_the_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host bound to a key and to the wheel -- the two events whose
    # meaning lives in a field rather than in the coordinates.
    host = _HostFrame()
    host.bind("<Key-a>", lambda event: None)
    host.bind("<MouseWheel>", lambda event: None)
    attached = _attach(monkeypatch, host=host)

    # When: a key press and a wheel turn happen over the picture.
    attached.canvas.bindings["<Key-a>"](_PointerEvent(1, 2, keysym="a"))
    attached.canvas.bindings["<MouseWheel>"](_PointerEvent(1, 2, delta=120))

    # Then: the fields arrived. The host reads them off the forwarded event to
    # tell which key was pressed and which way the wheel turned, so dropping
    # them would be a silent loss of input rather than a visible failure.
    by_sequence = dict(host.raised_kwargs)
    assert by_sequence["<Key-a>"]["keysym"] == "a", by_sequence
    assert by_sequence["<MouseWheel>"]["delta"] == 120, by_sequence


def test_the_double_refuses_an_option_the_event_type_cannot_take() -> None:
    # Given: the double that stands in for Tk. It has to model the refusal, or
    # the two tests above would pass against a stand-in that accepts anything.
    host = _HostFrame()

    # When/Then: a key option on a pointer event is red here exactly as it is
    # TclError on a display, and the reverse holds for the wheel's option.
    for sequence, option, value in (
        ("<Button-1>", "keysym", "a"),
        ("<Motion>", "keysym", "a"),
        ("<ButtonRelease-1>", "delta", 0),
        ("<Key-a>", "delta", 120),
        ("<MouseWheel>", "keysym", "a"),
    ):
        with pytest.raises(tk.TclError) as caught:
            host.event_generate(sequence, x=1, y=2, when="now", **{option: value})
        assert f"-{option}" in str(caught.value), (
            sequence,
            option,
            str(caught.value),
        )
        assert option not in _accepted_generate_options(sequence)


def test_tk_only_accepts_the_option_its_own_event_takes() -> None:
    # Given: a real Tk root, so the table the double enforces is checked
    # against Tk itself rather than against a second opinion.
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"no display for the real-Tk option check: {error}")
    try:
        root.geometry("64x64+0+0")
        root.update()

        # Tk's overloads cannot see through a dict built at runtime, so both
        # call sites unpack a dict pinned at an explicit Any boundary.
        event_kwargs: dict[str, Any]

        # Then/When: the pointer types reject both foreign options...
        for sequence in ("<Button-1>", "<Motion>", "<ButtonRelease-1>"):
            for option, value in (("keysym", "a"), ("delta", 0)):
                event_kwargs = {option: value}
                with pytest.raises(tk.TclError, match="doesn't accept"):
                    root.event_generate(sequence, x=1, y=1, when="now", **event_kwargs)
                assert option not in _accepted_generate_options(sequence)

        # ...and the two types that own them accept exactly those.
        for sequence, option, value in (
            ("<MouseWheel>", "delta", 120),
            ("<Key>", "keysym", "a"),
            ("<KeyRelease>", "keysym", "a"),
        ):
            event_kwargs = {option: value}
            root.event_generate(sequence, x=1, y=1, when="now", **event_kwargs)
            assert option in _accepted_generate_options(sequence)
    finally:
        root.destroy()


def test_the_cursor_over_the_picture_follows_the_host_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface over a crosshair host, as CaptureArea creates
    # it (GuiAssets.py:338), with the press and release handlers that change the
    # cursor to a dot and back (GuiAssets.py:1251, :1273).
    host = _HostFrame(cursor="tcross")
    for sequence in POINTER_SEQUENCES:
        host.bind(sequence, lambda event: None)

    def _press(_event: Any) -> None:
        host.config(cursor="dot")

    def _release(_event: Any) -> None:
        host.config(cursor="tcross")

    host.bind("<Button-1>", _press)
    host.bind("<ButtonRelease-1>", _release)
    attached = _attach(monkeypatch, host=host)

    # When: a drag starts and ends on the picture.
    attached.canvas.bindings["<Button-1>"](_PointerEvent(10, 20))
    attached.canvas.bindings["<ButtonRelease-1>"](_PointerEvent(10, 20))

    # Then: the host's cursor followed the gesture, which is only possible if
    # both events reached the handlers that set it. A drag that stops at the
    # canvas leaves the crosshair up for the whole gesture, so the user is told
    # nothing is held.
    assert host.cursor_history == ["dot", "tcross"], (
        f"the host's cursor went {host.cursor_history} across a press and a "
        "release on the picture; each setting comes from a handler that only "
        "runs if the event got past the canvas"
    )

    # Then: and the canvas never names a cursor of its own, so the host's is
    # what shows over the picture. It is built with an empty cursor for that
    # reason -- Tk takes a child's cursor from its parent when the child's is
    # empty -- and a cursor set here would win over the host's for the whole
    # area the picture covers.
    assert attached.canvas.options["cursor"] == "", attached.canvas.options
    assert not [c for c in attached.canvas.config_calls if "cursor" in c], (
        f"the surface set a cursor on the canvas: {attached.canvas.config_calls}"
    )


# ===========================================================================
# recompose() redraws the overlay with no new frame
# ===========================================================================
#
# スティックを離した時に新しいフレームは来ない。来るのはオーバーレイだけ。
# GdiSurface には専用の経路が既にあるが、この面には無い。無いと mac/Linux
# だけスティックの表示が残り、押していないのに押しているように見える。


def test_recompose_redraws_the_overlay_without_a_new_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface that has composited and presented one frame
    # with an active left stick, with the records cleared so what gets
    # measured below is the recompose's own effect and not the present's.
    from core.preview_renderer import OverlayState, RenderResult, StickState

    attached = _attach(monkeypatch)
    surface, canvas = attached.surface, attached.canvas
    width, height = _capture_size()
    held = OverlayState(
        left_stick=StickState(
            active=True,
            center_x=100,
            center_y=100,
            radius=20,
            knob_x=110,
            knob_y=100,
        )
    )
    composed = surface.compose(_contiguous_frame(width, height), held)
    assert composed.ok is True, composed.detail
    assert surface.present().ok is True
    assert canvas.item_calls, "the held stick never reached the canvas"
    canvas.item_calls.clear()
    canvas.delete_calls.clear()
    photos_after_present = len(attached.photos)

    # When: the stick is released and only the overlay is recomposited. No
    # new frame arrives -- the camera has nothing new to say, and a caller
    # that had one would have called compose instead.
    result = surface.recompose(OverlayState())

    # Then: it answers in the protocol's own shape, so the overlay-only path
    # is a first-class operation and not something the caller has to fake by
    # recompositing a stale frame.
    assert isinstance(result, RenderResult), result
    assert result.ok is True, result

    # Then: and the canvas reflects the new overlay exactly. The held
    # stick's ovals are taken down and nothing is drawn in their place,
    # because a released stick draws no circle.
    assert canvas.delete_calls == ["overlay"], canvas.delete_calls
    assert canvas.item_calls == [], (
        f"recompose drew {canvas.item_calls}; a released stick must leave no "
        "overlay items behind"
    )

    # Then: and no PhotoImage was built, because the camera buffer was never
    # re-read. A recompose that rebuilt the frame would do the erased video's
    # work twice and could not be told apart from a full compose.
    assert len(attached.photos) == photos_after_present, (
        f"recompose built {len(attached.photos) - photos_after_present} new "
        "PhotoImage(s); the overlay-only path must not touch the frame"
    )


# ===========================================================================
# The picture catches up with bindings the host adds or drops later
# ===========================================================================
#
# ``attach`` forwards whatever the host is bound to at the moment it runs, and
# ``CaptureArea.__init__`` attaches right there (``GuiAssets.py:445``). The six
# stick sequences are bound *after* that: ``camera_panel.py:473-474`` reaches
# ``BindLeftClick`` / ``BindRightClick`` through ``ApplyLStickMouse`` /
# ``ApplyRStickMouse`` (``GuiAssets.py:1527-1539``). So on this backend the
# gestures that matter most are exactly the ones never forwarded -- the Canvas
# covers the whole host box, Tk raises the pointer event on the Canvas, and the
# host's six handlers sit there unread. The same wiring works on Windows
# because GDI's video lives in a foreign HWND Tk cannot hit-test, so nothing
# this section pins is broken there.
#
# One public method closes the gap: ``sync_host_binds()`` re-reads
# ``host.bind()``, forwards what is new, drops what the host no longer reports,
# and is idempotent. Idempotence is the load-bearing part and not a nicety:
# ``canvas.bind(..., add="+")`` *accumulates* on a real Tk widget, so a second
# forwarding of a sequence that already has one is a second handler, and each
# extra ``Bind*`` call would then run the host handler once more per drag. The
# double therefore keeps one list per sequence -- see ``_RecordingCanvas.bind``
# -- because a canvas double that merely overwrites one slot per sequence cannot
# see the duplication and would let this section pass vacuously.


def _fire(canvas: _RecordingCanvas, sequence: str, event: Any) -> None:
    """Every handler Tk would run for ``sequence`` on ``canvas``, in order.

    The counterpart of Tk's binding table: a sequence nobody is bound to runs
    nothing at all, which is what makes "the Canvas stopped re-emitting" and
    "the Canvas never had a handler" indistinguishable from the outside.
    """
    for handler in canvas.handlers(sequence):
        handler(event)


def test_a_binding_the_host_adds_after_attach_is_forwarded_by_the_next_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attached surface whose host carries no pointer binding at all --
    # the state attach() sees for the sticks, because BindLeftClick runs later.
    host = _HostFrame()
    attached = _attach(monkeypatch, host=host)
    assert host.bind() == (), host.bind()
    assert set(attached.canvas.bindings) == {"<Destroy>"}, sorted(
        attached.canvas.bindings
    )
    pressed: list[tuple[int, int]] = []

    # When: the host is bound afterwards and the surface is asked to catch up.
    host.bind("<Button-1>", lambda event: pressed.append((event.x, event.y)))
    attached.surface.sync_host_binds()

    # Then: the canvas holds the sequence, which it did not at attach time.
    assert "<Button-1>" in attached.canvas.bindings, (
        f"the canvas is bound to {sorted(attached.canvas.bindings)}; a press "
        "bound on the host after attach stops at the canvas and the host's "
        "handler never runs"
    )

    # Then: and the press over the picture runs the handler bound after attach,
    # with the coordinates the canvas reported.
    _fire(attached.canvas, "<Button-1>", _PointerEvent(41, 42))
    assert pressed == [(41, 42)], pressed
    assert host.delivered == [("<Button-1>", 41, 42)], host.delivered


def test_a_binding_the_host_drops_is_taken_off_the_canvas_by_the_next_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host carrying two sequences from the left drag, both forwarded
    # by attach because both were already bound when it ran.
    dropped = "<Button-1>"
    kept = "<B1-Motion>"
    host = _HostFrame()
    for sequence in (dropped, kept):
        host.bind(sequence, lambda event: None)
    attached = _attach(monkeypatch, host=host)
    assert {dropped, kept} <= set(attached.canvas.bindings), sorted(
        attached.canvas.bindings
    )

    # When: one of them is unbound -- what UnbindLeftClick does to the Frame --
    # and the surface is asked to catch up.
    host.unbind(dropped)
    attached.surface.sync_host_binds()

    # Then: the sequence the host no longer reports is gone from the canvas.
    assert dropped not in attached.canvas.bindings, (
        f"the canvas is still bound to {sorted(attached.canvas.bindings)}; a "
        "forwarding the host has withdrawn keeps the gesture alive after the "
        "assignment is taken away"
    )

    # Then: and a press over that part of the picture re-emits nothing at all.
    _fire(attached.canvas, dropped, _PointerEvent(7, 9))
    assert host.raised == [], host.raised

    # Then: and the sequence that was *not* unbound is still forwarded, so the
    # sync removed one forwarding instead of clearing the canvas. Without this
    # the assertion above would also pass if the whole table had been wiped.
    _fire(attached.canvas, kept, _PointerEvent(7, 9))
    assert host.raised == [(kept, 7, 9)], host.raised


def test_syncing_the_same_host_bindings_twice_forwards_each_of_them_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host bound to one drag sequence, already forwarded by attach.
    host = _HostFrame()
    dragged: list[tuple[int, int]] = []
    host.bind("<B1-Motion>", lambda event: dragged.append((event.x, event.y)))
    attached = _attach(monkeypatch, host=host)
    assert len(attached.canvas.handlers("<B1-Motion>")) == 1, (
        "the fixture is wrong: attach must have forwarded the sequence once"
    )

    # When: the surface is asked to catch up three more times, as one Bind* /
    # Unbind* / Bind* cycle on a real widget would.
    for _attempt in range(3):
        attached.surface.sync_host_binds()

    # Then: the canvas still carries exactly one handler for it. add="+" appends,
    # so a re-forwarded sequence is not replaced but accumulated -- and a second
    # copy would move the stick twice per pixel of mouse travel.
    assert len(attached.canvas.handlers("<B1-Motion>")) == 1, (
        f"the canvas holds {len(attached.canvas.handlers('<B1-Motion>'))} "
        "handler(s) for <B1-Motion>; bind(..., add='+') accumulates, so a sync "
        "that does not remember what it already forwarded doubles the gesture"
    )

    # Then: and one drag over the picture runs the host's handler once.
    _fire(attached.canvas, "<B1-Motion>", _PointerEvent(21, 22))
    assert dragged == [(21, 22)], dragged
    assert host.raised == [("<B1-Motion>", 21, 22)], host.raised


def test_syncing_the_host_binds_before_attach_raises_nothing_and_does_nothing() -> None:
    # Given: a constructed but unattached surface, so there is no canvas to
    # forward onto -- the state CaptureArea is in before __init__ reaches
    # attach(), and the state the surface returns to when the canvas is gone.
    surface = _photo_surface_module().PhotoImageSurface(host=_HostFrame())
    assert surface._canvas is None

    # When: the sync is asked for anyway. A raise here ends the caller, and the
    # callers are ordinary UI paths (Bind*/Unbind*) with nobody to catch them.
    with _captured_log() as messages:
        surface.sync_host_binds()

    # Then: nothing happened and nothing was remembered, because there is no
    # box to hold a forwarding in the first place.
    assert surface._canvas is None
    assert surface.client_size() == (0, 0), surface.client_size()

    # Then: and it said nothing about it either. "Doing nothing" that logs a
    # warning per call is not nothing: Bind*/Unbind* run on every settings
    # change, and a warning each time is how a file log gets filled with a
    # condition that is the normal state of an unattached surface.
    assert messages == [], messages
