"""Opt-in real-Tk proof that both preview renderers settle and keep drawing. # noqa: SIZE_OK

The termination argument for the preview resize is already made, headlessly, in
``tests/test_photo_surface_contract.py``: ``resize`` refuses a degenerate box,
refuses the size it already has, and refuses to nest
(``:379-429``, ``:456-542``). Every one of those runs against doubles, because a
``Canvas`` and a ``PhotoImage`` cannot be built without a live Tk root. So the
headless suite proves the guard is *correct* and says nothing about whether the
real wiring ever *reaches* it -- and reaching it is the whole hazard. On Windows
a ``<Configure>`` storm is not hypothetical: Tk raises ``<Configure>`` before the
geometry manager has settled (the 1x1 box), again when it settles, and again for
the manager's own redraw passes. If the guards were not there, that stream would
feed ``resize`` forever.

This file is the empirical half. For each backend it builds a real ``tk.Tk()``
and a real ``CaptureArea``, lets the geometry manager settle, runs the
production 60 Hz ``PreviewClock`` for a couple of seconds, and records what the
box actually did. The claims are narrow and are all falsifiable by the retained
artifact:

* the watchdog never fired, so neither the settle nor the run hung;
* the *effective* resize count is bounded, where effective means the guard let
  the call through -- see :data:`MAX_EFFECTIVE_RESIZES`;
* no ``<Configure>`` of width <= 1 ever produced an effective resize, which is
  the 1x1 storm refuted by observation rather than by argument;
* presents advanced, so the surface was not merely quiet but drawing.

**The non-vacuity control is what stops the bound from being a tautology.** The
same area, the same ``<Configure>`` recorder, the same counting seam and the
same bound are replayed against a ``resize`` whose three refusals have been
removed. That pass must exceed the bound and must record the 1x1 row as
effective. If it did not, the real run's ``<= 8`` would be measuring nothing --
a suite where the gate cannot fail is not a gate. The control is the same
measurement with one variable changed, so the difference between the two numbers
is attributable to the guards and to nothing else.

Gated off by default: it needs a real window, and a CI lane with no interactive
desktop must not go red for it. The evidence directory must be **outside the
repository** and must be fresh -- the artifact is a record of a machine, not a
source file, and a second run that overwrote the first would be destroying the
measurement it exists to keep. ``%TEMP%\\pokecon-renderer-e2e\\<run>`` is the
intended shape::

    set POKECON_RUN_RENDERER_E2E=1
    set POKECON_RENDERER_EVIDENCE_DIR=%TEMP%\\pokecon-renderer-e2e\\run-1
    set POKECON_RENDERER_WATCHDOG_TIMEOUT_S=60
    uv run --frozen pytest tests/test_renderer_e2e.py -q

**A finding this run produced, recorded here because it changed how the run is
driven.** The production pacer re-arms its own wake poll through ``after_idle``
(``preview_clock.py:2043-2049``) and every handler re-arms another one as its
last act (``:2068-2070``). That is an idletask chain with no end, so on a real
Tk root ``root.update()`` does not return once the clock is running: a first
attempt at this file hung until the watchdog killed it, and a bare
self-rearming ``after_idle`` chain kept one ``update()`` alive for 52 s across
10,000,000 handler calls. ``_tkinter``'s threaded build exposes no
``doonevent``, so the work per turn cannot be bounded from Python.

The split that follows costs the measurement nothing. Every ``<Configure>`` is
a window-system event, so the geometry phase -- the phase the resize bound is
measured in -- runs on the real ``root.update()`` with the real geometry
manager, including a real sweep of the window wider and back. The clock then
runs against :class:`_TimerShim`, which replaces only the queue the callback
sits in; the native worker, the mailbox, the wake drain, the dispatch and the
blit are the production ones, which is why the dispatch rate comes out at the
configured 60 Hz rather than at the drain rate. The production behaviour itself
is untouched and out of scope here -- this is an observation, not a fix.

Everything production-side is observed, never edited: the counting wrappers are
``monkeypatch`` installs on the surface classes for the duration of one test, and
the control's unguarded ``resize`` is re-expressed *here* with the line numbers
of the guarded one it replaces. No production file and no existing test helper is
touched, and the headless suite stays headless: nothing in this module
constructs a Tk object unless the gate is on.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest

TESTS_DIR: Final[Path] = Path(__file__).resolve().parent
REPO_ROOT: Final[Path] = TESTS_DIR.parent

#: The opt-in switch. Absent or anything but "1" and both tests skip before a
#: window exists.
GATE_ENV: Final[str] = "POKECON_RUN_RENDERER_E2E"
#: Where the artifacts go. Required when the gate is on, and refused if it
#: resolves inside the repository.
EVIDENCE_DIR_ENV: Final[str] = "POKECON_RENDERER_EVIDENCE_DIR"
#: The budget for the whole real-Tk exercise: construct, settle, run, control.
WATCHDOG_ENV: Final[str] = "POKECON_RENDERER_WATCHDOG_TIMEOUT_S"

#: The two ``PreviewRenderer`` implementations a ``CaptureArea`` can be built
#: with on a Windows box, and the two names ``WindowUtils.RENDERER_VALUES`` uses.
RENDERERS: Final[tuple[str, ...]] = ("gdi", "photo")

#: The class each renderer name must actually produce. Asserted rather than
#: assumed: ``_create_preview_surface`` falls back from ``gdi`` to the Canvas
#: implementation on any ``OSError`` (``GuiAssets.py:296-307``), so a run that
#: silently substituted one backend for the other would otherwise publish two
#: copies of the same measurement and call it parity.
EXPECTED_SURFACE_CLASS: Final[dict[str, str]] = {
    "gdi": "GdiSurface",
    "photo": "PhotoImageSurface",
}

#: The production target. 60 is the default a user gets and the one the FPS E2E
#: measures; a slower target would exercise a different number of dispatches.
CONFIGURED_FPS: Final[int] = 60

#: How long the production clock runs per renderer. Long enough for the native
#: pacer to arm, dispatch and present a couple of hundred frames; short enough
#: that a gated run stays a minute-scale operation.
RUN_S: Final[float] = 2.0

#: How long the teardown may keep pumping the shim waiting for a confirmed
#: stop. Bounded, because a stop that never confirms is a finding to report, not
#: a reason to keep the run going.
TEARDOWN_PUMP_S: Final[float] = 3.0

#: Default budget for the whole exercise, generous because the failure being
#: caught is a hang and a tight budget would fire on a slow machine instead.
DEFAULT_WATCHDOG_TIMEOUT_S: Final[float] = 60.0

#: The bound this file gates on, and the arithmetic behind it. A surface that
#: settles is allowed one box per *distinct* geometry, and a real Tk startup has
#: very few of those: the attach at the show size, the pre-settlement 1x1 (which
#: must be refused, so it costs nothing), the settled box, and the odd redraw
#: pass the geometry manager makes for itself. The slack is what keeps a
#: two-machine difference in the window manager from reading as a regression.
MAX_EFFECTIVE_RESIZES: Final[int] = 8
BOUND_RATIONALE: Final[str] = (
    "1 attach (CaptureArea.__init__ calls surface.attach with the show size, "
    "GuiAssets.py:440) + at most 2 geometry settles (Tk raises <Configure> "
    "before the geometry manager has settled, i.e. the 1x1 box, and once more "
    "when it settles) + 5 slack for the geometry manager's own redraw passes = "
    "8. Above this the box is not settling; it is the feedback loop the headless "
    "termination proof (tests/test_photo_surface_contract.py:410-429) rules out, "
    "and the non-vacuity control below shows this same machinery exceeding the "
    "bound the moment the refusals are removed."
)

#: The refusals the control removes, and that therefore have to still be in the
#: production source for the real run's claim to mean anything. Checked at run
#: time, against the live class, so a guard that was quietly dropped makes this
#: test RED instead of letting it certify a bound nothing enforces.
GUARD_PROBES: Final[tuple[tuple[str, str], ...]] = (
    ("re-entrancy guard", "self._rebuilding"),
    ("degenerate-box refusal", "width <= 1"),
    ("idempotence guard", "== self._size"),
)

SCHEMA_VERSION: Final[int] = 2
REPORT_ARTIFACT: Final[str] = "report.json"
GEOMETRY_TRACE_ARTIFACT: Final[str] = "geometry_trace.jsonl"
TEARDOWN_ARTIFACT: Final[str] = "teardown.json"
MANIFEST_ARTIFACT: Final[str] = "artifact_hashes.json"
#: Written by the watchdog thread itself, from outside the Tk loop, so a run
#: that is killed outright still leaves a record of why it was killed.
WATCHDOG_ARTIFACT: Final[str] = "watchdog_timeout.json"

#: The artifacts the manifest covers. The watchdog marker is listed too but
#: separately: it must be *absent* on a healthy run, so requiring it would
#: invert the meaning of the set.
REQUIRED_ARTIFACTS: Final[tuple[str, ...]] = (
    REPORT_ARTIFACT,
    GEOMETRY_TRACE_ARTIFACT,
    TEARDOWN_ARTIFACT,
)

#: The production files whose revision this measurement belongs to.
PINNED_SOURCES: Final[tuple[str, ...]] = (
    "SerialController/GuiAssets.py",
    "SerialController/ui/photo_surface.py",
    "SerialController/ui/preview_clock.py",
    "SerialController/core/gdi_surface.py",
)


# ===========================================================================
# The measurement
# ===========================================================================


class _Probe:
    """Everything the run measures. Three counters and the geometry trace.

    No per-frame logging anywhere. A 60 Hz run would emit thousands of lines and
    bury the one fact this file exists to record, and the production surfaces
    already deduplicate their own refusal logs for the same reason.
    """

    def __init__(self) -> None:
        #: Calls the surface's guards let through, including the attach-time one.
        self.commits = 0
        #: The box committed by each of those calls, for the artifact.
        self.commit_sizes: list[tuple[int, int]] = []
        self.present_calls = 0
        self.present_ok = 0
        #: One row per ``<Configure>``, in arrival order.
        self.rows: list[dict[str, Any]] = []
        #: Which pass a row belongs to. ``observed`` is Tk's own; the rest are
        #: the control's synthetic stream and are labelled as such so the trace
        #: can never be read as a claim about what Tk delivered.
        self.label = "observed"
        #: Whether the production ``resize`` still carries all three refusals,
        #: read from the live class before it is wrapped.
        self.guards_present: dict[str, bool] = {}

    def rows_where(
        self, *, label: str, resized: bool | None = None
    ) -> list[dict[str, Any]]:
        selected = [row for row in self.rows if row["source"] == label]
        if resized is None:
            return selected
        return [row for row in selected if bool(row["resized"]) is resized]

    def count(self, *, label: str, width_le_1: bool = False) -> int:
        """Rows in ``label`` that committed, optionally restricted to width <= 1.

        One function, used for the real run and for both control passes, so the
        three numbers being compared are produced by the same reading of the
        same trace rather than by three similar-looking expressions.
        """
        total = 0
        for row in self.rows_where(label=label, resized=True):
            if width_le_1 and int(row["width"]) > 1:
                continue
            total += 1
        return total


def _resized_counter(probe: _Probe, impl: Callable[..., None]) -> Callable[..., None]:
    """Wrap a ``resize`` implementation so accepted calls are counted.

    The seam is ``_size`` being *rebound*. Both backends assign ``self._size``
    only after their guards have let the call through
    (``photo_surface.py:153``, ``gdi_surface.py:754``), so a change of object
    identity is exactly "this call was accepted and the box was committed".

    Identity rather than value is deliberate. A ``<Configure>`` storm re-sends
    the *same* geometry, and a value comparison would score the storm as
    harmless even while every one of those events re-configures the box -- which
    is the failure being looked for. It is also the only seam that catches the
    attach-time call, which happens inside ``CaptureArea.__init__`` before any
    binding could be installed.
    """

    def _counting_resize(self: Any, size: tuple[int, int]) -> None:
        before = getattr(self, "_size", None)
        impl(self, size)
        after = getattr(self, "_size", None)
        # A missing box is not a commit: nothing was committed, so counting it
        # would let a surface that dropped its size read as one that resized.
        if after is None or after is before:
            return
        probe.commits += 1
        probe.commit_sizes.append((int(after[0]), int(after[1])))

    return _counting_resize


def _present_counter(probe: _Probe, impl: Callable[..., Any]) -> Callable[..., Any]:
    """Count presented frames. A counter, not a log: this runs per frame."""

    def _counting_present(self: Any) -> Any:
        probe.present_calls += 1
        result = impl(self)
        if bool(getattr(result, "ok", False)):
            probe.present_ok += 1
        return result

    return _counting_present


def _install_counters(probe: _Probe, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wrap both backends before any surface exists, and read the guards now.

    Both classes are wrapped, not just the requested one: which backend
    ``_create_preview_surface`` ends up with is not known until the area is
    constructed, and it can differ from the request. Wrapping only the requested
    class would leave a fallback run with no counters at all -- a trace of zeros
    that reads exactly like a healthy one.

    The guard probe also has to happen here: once ``resize`` is replaced, the
    class no longer carries the source this file is checking.
    """
    from core import gdi_surface
    from ui import photo_surface

    backends = {
        "GdiSurface": gdi_surface.GdiSurface,
        "PhotoImageSurface": photo_surface.PhotoImageSurface,
    }
    for name, backend in backends.items():
        source = inspect.getsource(backend.resize)
        for label, token in GUARD_PROBES:
            present = token in source
            probe.guards_present[f"{name}.{label}"] = present
            assert present, (
                f"{name}.resize no longer contains {token!r}, so the "
                f"{label} this run's bound depends on is gone. The bound would "
                f"then certify a guarantee nothing enforces; the source reads:\n"
                f"{source}"
            )
        monkeypatch.setattr(backend, "resize", _resized_counter(probe, backend.resize))
        monkeypatch.setattr(
            backend, "present", _present_counter(probe, backend.present)
        )
    return {name: backend for name, backend in backends.items()}


def _surface_size(area: Any) -> list[int]:
    """The box the surface holds, or ``[]`` if it does not say.

    Checked once, loudly, from the run body before the recorder goes on (an
    ``assert`` inside a Tk callback is swallowed by ``_report_exception`` and
    would turn a rename into a trace full of silent ``False``). Here the answer
    is simply absent, and the real-run assertion on the committed box count
    fails instead of quietly passing.
    """
    size = getattr(area.surface, "_size", None)
    if not isinstance(size, tuple) or len(size) != 2:
        return []
    return [int(size[0]), int(size[1])]


def _assert_surface_reports_its_box(area: Any) -> None:
    assert _surface_size(area), (
        f"{type(area.surface).__name__} does not expose a two-element _size, so "
        "this trace cannot say whether a resize was accepted. Rename-aware "
        "guards: a trace that cannot see the box reports every resize as "
        "'not resized' and the bound becomes unfalsifiable."
    )


def _install_configure_recorder(area: Any, probe: _Probe) -> Callable[[Any], None]:
    """Rebind ``<Configure>`` so every event is traced and still reaches production.

    ``CaptureArea.__init__`` binds ``self._onConfigure`` (``GuiAssets.py:452``);
    a plain ``bind`` replaces that binding, so the production handler is kept and
    only wrapped. ``add="+"`` would run both and attribute the commit to the
    wrong event.

    The returned callable is the binding itself. The control replays through it
    rather than through ``_onConfigure``, because that is the callable Tk
    invokes: a control that called the handler directly would be measuring one
    hop fewer than the real run and the two numbers would not be comparable.
    """
    production_handler = area._onConfigure

    def _record(event: Any) -> None:
        before = probe.commits
        production_handler(event)
        probe.rows.append(
            {
                "seq": len(probe.rows),
                "source": probe.label,
                "width": int(event.width),
                "height": int(event.height),
                "surface_size": _surface_size(area),
                "resized": probe.commits > before,
            }
        )

    area.bind("<Configure>", _record)
    return _record


class _ConfigureEvent:
    """The two fields ``CaptureArea._onConfigure`` reads off a Tk ``Configure``.

    Carrying only those two is enough because they are all the handler uses
    (``GuiAssets.py:488-493``). The handler's clamp to the show size is part of
    the path under test and is therefore *not* bypassed by the control.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height


class _SequenceCamera:
    """The production camera's read shape, backed by one buffer.

    ``CaptureArea._readLatest`` prefers ``readFrameWithSeq`` and de-duplicates on
    the sequence it returns (``GuiAssets.py:643-653``), and ``_drawFrame`` skips
    a repeat of the last drawn sequence (``:679-681``). A camera that kept
    returning one sequence would therefore exercise neither compose nor present,
    and a run that measured it would be measuring the dedup branch. The sequence
    advances on every read so the render path is the one under test.

    One buffer, written in place: a 2 s 60 Hz run allocates nothing per frame,
    so the probe's counters are not competing with the thing they measure.
    """

    def __init__(self, size: tuple[int, int]) -> None:
        self._frame = np.zeros((size[1], size[0], 3), np.uint8)
        self._seq = 0
        self.reads = 0

    def readFrameWithSeq(self, copy: bool = False) -> tuple[np.ndarray, int]:
        _ = copy
        self._seq += 1
        self.reads += 1
        # A small changing patch: enough that a presented frame is
        # distinguishable from a blank one, cheap enough to be free.
        self._frame[:8, :8, 0] = self._seq % 256
        self._frame[:8, :8, 1] = (self._seq * 7) % 256
        return self._frame, self._seq

    def readFrame(self, copy: bool = False) -> np.ndarray:
        return self.readFrameWithSeq(copy)[0]

    def frame_seq(self) -> int:
        return self._seq


def _settle_geometry(root: Any, size: tuple[int, int]) -> None:
    """Let the geometry manager place the window, and hand Tk its events.

    ``root.update()`` rather than a callback pump, because this is the phase
    where the real thing is being measured: real window-system events, real
    ``<Configure>`` deliveries, the real geometry manager. It is safe to call
    ``update()`` only while the preview clock is stopped, which is exactly when
    this runs -- see :class:`_TimerShim` for why.

    Three turns, not one: Tk coalesces a geometry request and the first
    ``update`` can satisfy only part of it, and a trace that recorded a
    half-applied size would be measuring the harness's impatience.
    """
    root.geometry(f"{size[0]}x{size[1]}+0+0")
    for _turn in range(3):
        root.update()
        time.sleep(0.02)


class _TimerShim:
    """The Tk timer wheel, replaced by a queue this test owns.

    Necessary, and not a convenience. The production pacer re-arms its own wake
    poll through ``after_idle`` (``preview_clock.py:2043-2049``), and every
    handler re-arms another one as its last act
    (``preview_clock.py:2068-2070``). That is an idletask chain with no end, so
    once the clock is running ``root.update()`` does not return: it services
    idles for as long as it can find them. Measured on this machine, a bare
    self-rearming ``after_idle`` chain kept one ``update()`` alive for 52 s
    across 10,000,000 handler calls, and ``_tkinter``'s threaded build exposes
    no ``dooneevent`` to bound the work per turn. So the run phase cannot be
    driven by ``update()`` at all, and the phase that has to be driven is the
    one whose assertions matter least: the resize count is measured entirely in
    the settle phase above, with the real event loop.

    What is *not* synthetic is the pacer itself. The native worker thread, the
    mailbox, the wake drain, the dispatch and the blit are the production ones;
    only the queue the callback sits in is local, and it is drained in due order
    against the real clock -- which is why the dispatch rate measured below
    comes out at the configured 60 Hz rather than at the drain rate.
    """

    def __init__(self, root: Any) -> None:
        self.root = root
        self.items: list[dict[str, Any]] = []
        self.calls = 0
        self._next_id = 0
        self._real_after = root.after
        self._real_after_idle = root.after_idle
        self._real_after_cancel = root.after_cancel
        root.after = self.after
        root.after_idle = self.after_idle
        root.after_cancel = self.after_cancel

    def _arm(self, delay_ms: int, callback: Any) -> str:
        self._next_id += 1
        after_id = f"shim-{self._next_id}"
        self.items.append(
            {
                "after_id": after_id,
                "due_s": time.perf_counter() + delay_ms / 1000.0,
                "callback": callback,
                "cancelled": False,
            }
        )
        return after_id

    def after(self, delay_ms: int, callback: Any) -> str:
        return self._arm(int(delay_ms), callback)

    def after_idle(self, callback: Any) -> str:
        return self._arm(0, callback)

    def after_cancel(self, after_id: str) -> None:
        for item in self.items:
            if item["after_id"] == after_id:
                item["cancelled"] = True
                return

    def restore(self) -> None:
        self.root.after = self._real_after
        self.root.after_idle = self._real_after_idle
        self.root.after_cancel = self._real_after_cancel

    def pending(self) -> int:
        return sum(1 for item in self.items if not item["cancelled"])

    def run_until(self, seconds: float) -> int:
        """Drain due callbacks until the wall-clock budget is spent."""
        calls = 0
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            now = time.perf_counter()
            due = [
                item
                for item in self.items
                if not item["cancelled"] and item["due_s"] <= now
            ]
            if not due:
                # Nothing is due: sleep, or this becomes the same busy spin the
                # shim exists to make measurable, one order of magnitude louder.
                time.sleep(0.0002)
                continue
            item = min(due, key=lambda entry: entry["due_s"])
            self.items.remove(item)
            item["callback"]()
            calls += 1
        self.items = [item for item in self.items if not item["cancelled"]]
        return calls


# ===========================================================================
# The watchdog
# ===========================================================================


class _Watchdog:
    """Fires at the budget from outside the Tk thread, and says so on disk.

    A hang is the failure this file exists to rule out, so the sentinel cannot
    live on the thread that hangs. A daemon ``Timer`` marks the artifact and
    writes its own file; the assertions read that flag, not the absence of an
    exception, so a run killed outright still leaves an attributable record
    rather than a missing one.
    """

    def __init__(self, directory: Path, timeout_s: float) -> None:
        self.directory = directory
        self.timeout_s = timeout_s
        self.fired = False
        self._timer = threading.Timer(timeout_s, self._mark)
        self._timer.daemon = True
        self._timer.start()

    def _mark(self) -> None:
        self.fired = True
        payload = {
            "schema_version": SCHEMA_VERSION,
            "watchdog_timeout_s": self.timeout_s,
            "pid": os.getpid(),
            "perf_counter_ns": time.perf_counter_ns(),
            "verdict": (
                "the real-Tk renderer exercise did not return within the budget; "
                "the Tk event loop or a <Configure> storm did not terminate"
            ),
        }
        # Exclusive: a second firing must not overwrite the first record.
        try:
            with (self.directory / WATCHDOG_ARTIFACT).open("x", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError:
            # The test is already failing on the flag; losing the marker must
            # not replace that failure with an exception from a daemon thread.
            return

    def disarm(self) -> None:
        self._timer.cancel()


# ===========================================================================
# The non-vacuity control
# ===========================================================================


def _unguarded_photo_resize(self: Any, size: tuple[int, int]) -> None:
    """``photo_surface.py:143-157`` with the three refusals removed.

    The body the guarded method runs once it has decided to accept the call, and
    nothing else. What is gone is exactly: the ``_rebuilding`` nesting guard, the
    ``width <= 1`` degenerate-box refusal, and the same-size idempotence guard.
    """
    if self._canvas is None:
        return
    width, height = int(size[0]), int(size[1])
    self._canvas.config(width=width, height=height)
    self._size = (width, height)


def _unguarded_gdi_resize(self: Any, size: tuple[int, int]) -> None:
    """``gdi_surface.py:772-788`` with the three refusals removed.

    Same removal as the fallback above, on the body that moves the child window
    and rebuilds the DIB and the memory DC.
    """
    if self._child == 0:
        return
    width, height = int(size[0]), int(size[1])
    self._place(width, height)
    if self._build_back_buffer(width, height):
        self._run_self_test()


UNGUARDED_RESIZE: Final[dict[str, Callable[..., None]]] = {
    "GdiSurface": _unguarded_gdi_resize,
    "PhotoImageSurface": _unguarded_photo_resize,
}


#: How many 1x1 rows the control's stream carries. More than one, so the claim
#: is about a repeated degenerate box rather than a single lucky refusal.
DEGENERATE_ROWS: Final[int] = 3


def _storm_stream(
    observed: Sequence[Mapping[str, Any]], show_size: tuple[int, int]
) -> list[tuple[int, int]]:
    """The event stream both control passes are driven with.

    The observed rows come first, so the stream is the real one extended rather
    than a synthetic one substituted.

    The tail's *order* is load-bearing, and it was wrong once. ``_onConfigure``
    clamps both axes up to the show size (``GuiAssets.py:488-493``), so a 1x1
    ``<Configure>`` does not arrive as a 1x1 box: it arrives as the show size.
    Placing the degenerate row while the surface still held something else
    therefore got a legitimate commit -- a real size change, not a missing
    guard -- and the comparison measured the clamp instead of the refusal.

    So the stream first brings the surface to the box the clamp produces, and
    only then sends the degenerate rows. That is the real startup situation
    rather than a rigged one: ``attach`` sizes the surface to the show size
    (``GuiAssets.py:440``), and the pre-settlement ``<Configure>`` clamps onto
    exactly that box. With the surface already holding it, "refused" is the
    right answer for a 1x1 row, and the guarded and unguarded passes separate on
    the guard rather than on the clamp.
    """
    events = [(int(row["width"]), int(row["height"])) for row in observed]
    clamp_target = (int(show_size[0]), int(show_size[1]))
    events.append(clamp_target)
    events.extend([(1, 1)] * DEGENERATE_ROWS)
    while len(events) <= MAX_EFFECTIVE_RESIZES + 4:
        events.append(clamp_target)
    return events


def _run_non_vacuity_control(
    area: Any,
    probe: _Probe,
    backend: Any,
    surface_name: str,
    record: Callable[[Any], None],
) -> dict[str, Any]:
    """Replay one stream through the same path twice: guards, then no guards.

    Everything is held fixed except ``resize``: the same area, the same surface
    instance, the same recorder, the same counting wrapper, the same
    :data:`MAX_EFFECTIVE_RESIZES`. So the gap between the two numbers is
    attributable to the refusals and to nothing else -- which is the only thing
    that makes the real run's ``<= 8`` a measurement rather than a decoration.
    """
    observed = list(probe.rows_where(label="observed"))
    show_size = (int(area.show_width), int(area.show_height))
    stream = _storm_stream(observed, show_size)

    probe.label = "storm_guarded"
    before = probe.commits
    for width, height in stream:
        record(_ConfigureEvent(width, height))
    guarded_commits = probe.commits - before

    probe.label = "storm_unguarded"
    guarded_size = _surface_size(area)
    unguarded_resize = backend.resize
    backend.resize = _resized_counter(probe, UNGUARDED_RESIZE[surface_name])
    try:
        before = probe.commits
        for width, height in stream:
            record(_ConfigureEvent(width, height))
        unguarded_commits = probe.commits - before
    finally:
        backend.resize = unguarded_resize
        probe.label = "observed"

    return {
        "surface_class": surface_name,
        "event_count": len(stream),
        "degenerate_rows": DEGENERATE_ROWS,
        "show_size": list(show_size),
        "guarded_effective_resize_count": guarded_commits,
        "unguarded_effective_resize_count": unguarded_commits,
        "guarded_rows_with_width_le_1_resized": probe.count(
            label="storm_guarded", width_le_1=True
        ),
        "unguarded_rows_with_width_le_1_resized": probe.count(
            label="storm_unguarded", width_le_1=True
        ),
        "guarded_final_size": guarded_size,
        "unguarded_final_size": _surface_size(area),
        "bound": MAX_EFFECTIVE_RESIZES,
        "bound_rationale": BOUND_RATIONALE,
        "removed_guards": [label for label, _token in GUARD_PROBES],
        "stream": [[width, height] for width, height in stream],
        "stream_note": (
            "the first rows are the geometry Tk itself delivered (source="
            "'observed' in geometry_trace.jsonl); the tail is the documented "
            "storm, labelled 'storm_guarded'/'storm_unguarded' in the trace so "
            "it is never read as a claim about what Tk sent. The guarded pass "
            "accepts the genuinely-new geometries in the observed prefix -- a "
            "size change is what it is for -- and refuses the degenerate row and "
            "every repeat, because by then it already holds the tail geometry."
        ),
        "scope_note": (
            "both passes are driven through CaptureArea._onConfigure, so the "
            "handler's clamp to the show size (GuiAssets.py:488-493) is applied "
            "on the way: a width<=1 <Configure> reaches the surface as the show "
            "size, and the stream places the degenerate rows only once the "
            "surface already holds that box. What the two numbers separate is "
            "the idempotence guard and the re-entrancy guard. The surface's own "
            "width<=1 refusal is not reached from this path at all; it is pinned "
            "headlessly at tests/test_photo_surface_contract.py:379-407 and "
            "gdi_surface.py:778, and this artifact does not claim otherwise."
        ),
    }


# ===========================================================================
# The run
# ===========================================================================


def _capture_size() -> tuple[int, int]:
    """The frame size both backends judge incoming frames against."""
    from core.Camera import CAPTURE_SIZE

    return (int(CAPTURE_SIZE[0]), int(CAPTURE_SIZE[1]))


def _run_renderer(
    renderer: str,
    directory: Path,
    watchdog_timeout_s: float,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Drive one backend on a real Tk root and write its artifacts.

    Returns the report **re-read from disk**, deliberately: the artifact is the
    deliverable, so what a reader opens is what the assertions are made against
    and a writer that quietly failed to flush is a failure, not a pass.
    """
    import tkinter as tk

    from GuiAssets import CaptureArea

    capture_width, capture_height = _capture_size()
    # The show size the area is built at. Deliberately smaller than the window:
    # CaptureArea clamps a <Configure> up to the show size
    # (GuiAssets.py:488-493), so a show size equal to the window would make every
    # later geometry change a no-op at the clamp and the resize path would never
    # be reached. Building small and the window large is what lets the sweep
    # below move the surface for real.
    show_size = (capture_width, capture_height // 2)
    start_window = (capture_width, capture_height)
    probe = _Probe()
    backends = _install_counters(probe, monkeypatch)

    watchdog = _Watchdog(directory, watchdog_timeout_s)
    total_started = time.perf_counter()
    root: Any = None
    area: Any = None
    record: Callable[[Any], None] | None = None
    backend: Any = None
    shim: _TimerShim | None = None
    camera = _SequenceCamera((capture_width, capture_height))
    teardown: dict[str, Any] = {"cleanup_status": "not_run"}
    control: dict[str, Any] = {}
    presents_at_start = 0
    run_elapsed_s = 0.0
    commits_after_settle = 0
    commits_after_run = 0
    # The real run's own commit count. Taken at the last moment before the
    # control starts driving the surface, so the number the bound is applied to
    # is the one the *observed* geometry produced. Reading it after the control
    # would fold the control's deliberately unguarded commits into the count and
    # make a healthy run look like the runaway it is meant to be contrasted
    # with.
    commits_before_control = 0
    shim_calls = 0
    teardown_pump_calls = 0
    clock_mode = "stopped"
    surface_name = ""
    surface_selftest: Any = None
    surface_selftest_live: Any = None
    try:
        root = tk.Tk()
        root.title(f"PokeCon renderer E2E ({renderer})")
        is_show = tk.BooleanVar(master=root, value=True)
        area = CaptureArea(
            camera,
            CONFIGURED_FPS,
            is_show,
            None,
            master=root,
            show_width=show_size[0],
            show_height=show_size[1],
            take_stick_log=False,
            renderer=renderer,
        )
        surface_name = type(area.surface).__name__
        _assert_surface_reports_its_box(area)
        record = _install_configure_recorder(area, probe)
        area.pack(fill=tk.BOTH, expand=True)
        backend = backends[surface_name]

        # Settle at the window size the run will use, then sweep the window
        # wider and back. The sweep is the part that matters: a startup-only
        # trace proves the surface accepted its very first box, whereas a real
        # geometry change on a real window proves the resize path runs at all
        # and still settles. Both passes are inside the budget and inside the
        # trace.
        _settle_geometry(root, start_window)
        widened = (capture_width + 320, capture_height + 80)
        _settle_geometry(root, widened)
        _settle_geometry(root, start_window)
        commits_after_settle = probe.commits

        shim = _TimerShim(root)
        area.startCapture()
        clock = getattr(area, "_preview_clock", None)
        assert clock is not None, (
            "startCapture left no PreviewClock, so the run never exercised the "
            "production pacer and the presents below would be measuring the "
            "constructor's single initial dispatch"
        )
        clock_mode = str(clock.mode())
        presents_at_start = probe.present_ok

        started = time.perf_counter()
        shim_calls = shim.run_until(RUN_S)
        run_elapsed_s = time.perf_counter() - started
        commits_after_run = probe.commits

        # End-of-run self-test capture, before teardown: the GuiAssets mirror
        # and the surface's own verdict, read at one moment while the window
        # is still mapped. The mirror is read here rather than after the
        # finally block because the non-vacuity control replays configure
        # events through _onConfigure, which -- once the mirror refresh below
        # lands -- would rewrite the mirror after the report was measured and
        # hide the staleness this field exists to catch. The live read must
        # also precede release(): photo's release() sets _canvas=None, after
        # which self_test() answers "not_run" and the comparison becomes
        # vacuous. Reading either at construction would snapshot the pre-run
        # "pending" value and fail every healthy run.
        surface_selftest = getattr(area, "surface_selftest", None)
        live_probe = getattr(area.surface, "self_test", None)
        surface_selftest_live = live_probe() if callable(live_probe) else None
    finally:
        try:
            stop_result = "not_reached"
            surface_released: bool | str = "not_reached"
            if area is None:
                pass
            else:
                # Confirmed stop, pumping the shim in between. A PENDING result
                # schedules its own retry through the wheel, so the retry has to
                # be run or the stop is never confirmed -- and an unconfirmed
                # stop is the one thing this file must not assert around.
                deadline = time.perf_counter() + TEARDOWN_PUMP_S
                try:
                    stop_result = str(area.stopCapture())
                    while stop_result != "stopped" and shim is not None:
                        if time.perf_counter() >= deadline:
                            break
                        teardown_pump_calls += shim.run_until(0.05)
                        stop_result = str(area.stopCapture())
                except Exception as error:
                    # Recorded rather than swallowed: a teardown that raises is
                    # itself a finding, and the artifact has to carry it.
                    stop_result = f"raised:{type(error).__name__}"
                if shim is not None:
                    shim.restore()
                # Late <Configure>s are window-system events, so they need the
                # real loop back. The clock is stopped by now, so update() is
                # bounded again.
                if root is not None:
                    for _turn in range(3):
                        try:
                            root.update()
                        except Exception:
                            break
                if record is not None and backend is not None:
                    commits_before_control = probe.commits
                    try:
                        control = _run_non_vacuity_control(
                            area, probe, backend, surface_name, record
                        )
                    except Exception as error:
                        control = {
                            "error": f"{type(error).__name__}: {error}",
                        }
                try:
                    area.surface.release()
                    surface_released = True
                except Exception as error:
                    surface_released = f"raised:{type(error).__name__}"
            root_destroyed = False
            if root is not None:
                try:
                    root.destroy()
                    root_destroyed = True
                except Exception:
                    root_destroyed = False
            teardown = {
                "schema_version": SCHEMA_VERSION,
                "renderer_requested": renderer,
                "surface_class": surface_name,
                "stop_result": stop_result,
                "surface_released": surface_released,
                "root_destroyed": root_destroyed,
                "clock_mode_before_teardown": clock_mode,
                "clock_mode_after_teardown": (
                    str(area._preview_clock.mode())
                    if area is not None
                    and getattr(area, "_preview_clock", None) is not None
                    else "stopped"
                ),
                "camera_reads": camera.reads,
                "run_elapsed_s": run_elapsed_s,
                "teardown_pump_calls": teardown_pump_calls,
                "watchdog_fired": watchdog.fired,
                "watchdog_timeout_s": watchdog_timeout_s,
                "cleanup_status": "completed",
            }
        finally:
            watchdog.disarm()
    total_elapsed_s = time.perf_counter() - total_started

    report = {
        "schema_version": SCHEMA_VERSION,
        "renderer_requested": renderer,
        "surface_class": surface_name,
        "surface_selftest": (
            {
                "outcome": str(getattr(surface_selftest, "outcome", "not_supported")),
                "fully_occluded": bool(
                    getattr(surface_selftest, "fully_occluded", False)
                ),
            }
            if surface_selftest is not None
            else None
        ),
        "surface_selftest_live": (
            {
                "outcome": str(
                    getattr(surface_selftest_live, "outcome", "not_supported")
                ),
                "fully_occluded": bool(
                    getattr(surface_selftest_live, "fully_occluded", False)
                ),
            }
            if surface_selftest_live is not None
            else None
        ),
        "clock_mode": clock_mode,
        "configured_fps": CONFIGURED_FPS,
        "show_size": list(show_size),
        "capture_size": [capture_width, capture_height],
        "window_sizes": [list(start_window), list(widened), list(start_window)],
        "run_s": RUN_S,
        "run_elapsed_s": run_elapsed_s,
        "total_elapsed_s": total_elapsed_s,
        "pump": {
            "geometry_phase": "root.update() -- real window-system events, the "
            "real geometry manager, and the resize count is measured here",
            "run_phase": "_TimerShim queue -- root.update() cannot be used once "
            "the pacer is running, because the pacer re-arms its own after_idle "
            "poll (preview_clock.py:2043-2070) and update() then never returns",
            "shim_callbacks_during_run": shim_calls,
            "teardown_pump_callbacks": teardown_pump_calls,
        },
        "watchdog_fired": watchdog.fired,
        "watchdog_timeout_s": watchdog_timeout_s,
        "watchdog_artifact_present": (directory / WATCHDOG_ARTIFACT).is_file(),
        "configure_event_count": len(probe.rows_where(label="observed")),
        "effective_resize_count": commits_before_control,
        "effective_resize_bound": MAX_EFFECTIVE_RESIZES,
        "effective_resize_bound_rationale": BOUND_RATIONALE,
        "effective_resize_count_with_width_le_1": probe.count(
            label="observed", width_le_1=True
        ),
        "effective_resize_sizes": [
            list(size) for size in probe.commit_sizes[:commits_before_control]
        ],
        "commits_after_settle": commits_after_settle,
        "commits_after_run": commits_after_run,
        "commits_counted_before_control": commits_before_control,
        "production_resize_guards_present": probe.guards_present,
        "present_calls": probe.present_calls,
        "presents_ok": probe.present_ok,
        "presents_at_start": presents_at_start,
        "presents_advanced": probe.present_ok > presents_at_start,
        "camera_reads": camera.reads,
        "non_vacuity_control": control,
        "source_pins": [_pin_source(relative) for relative in PINNED_SOURCES],
        "runtime_metadata": _runtime_metadata(),
    }
    _write_artifacts(directory, report, probe, teardown)
    reloaded: Any = json.loads(
        (directory / REPORT_ARTIFACT).read_text(encoding="utf-8")
    )
    return reloaded


# ===========================================================================
# Artifacts
# ===========================================================================


def _pin_source(relative: str) -> dict[str, Any]:
    """Hash one repository file, so the measurement is attributable to a revision."""
    path = REPO_ROOT / relative
    if not path.is_file():
        return {"path": relative, "exists": False, "bytes": 0, "sha256": ""}
    payload = path.read_bytes()
    return {
        "path": relative,
        "exists": True,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _pin_artifact(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    if not path.is_file():
        return {"path": name, "exists": False, "bytes": 0, "sha256": ""}
    payload = path.read_bytes()
    return {
        "path": name,
        "exists": True,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    """Write once. A second write is the intended signal, not a retry."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl_exclusive(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _write_manifest(directory: Path) -> dict[str, Any]:
    """Publish the hashes of what was written, and cover the watchdog marker.

    Tamper-evident rather than tamper-proof: the manifest is written last, so it
    is the one thing a later edit has to update too, and a mismatch is detected
    rather than prevented. That is the honest property for an artifact whose
    whole job is to be believed after the fact.
    """
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "algorithm": "sha256",
        "artifacts": [_pin_artifact(directory, name) for name in REQUIRED_ARTIFACTS],
        "watchdog_artifact": _pin_artifact(directory, WATCHDOG_ARTIFACT),
    }
    _write_json_exclusive(directory / MANIFEST_ARTIFACT, manifest)
    return manifest


def _verify_manifest(directory: Path, manifest: Mapping[str, Any]) -> None:
    rows = manifest["artifacts"]
    assert [row["path"] for row in rows] == list(REQUIRED_ARTIFACTS), rows
    for row in rows:
        current = _pin_artifact(directory, str(row["path"]))
        assert current == dict(row), (
            f"{row['path']} does not match the manifest written for it: "
            f"recorded {dict(row)!r}, on disk {current!r}"
        )


def _write_artifacts(
    directory: Path,
    report: Mapping[str, Any],
    probe: _Probe,
    teardown: Mapping[str, Any],
) -> None:
    # Report and trace first, teardown next, manifest last: the manifest must
    # only ever cover files that are already complete on disk.
    _write_json_exclusive(directory / REPORT_ARTIFACT, report)
    _write_jsonl_exclusive(directory / GEOMETRY_TRACE_ARTIFACT, probe.rows)
    _write_json_exclusive(directory / TEARDOWN_ARTIFACT, teardown)
    _write_manifest(directory)


def _runtime_metadata() -> dict[str, str | float]:
    import tkinter as tk

    return {
        "pid": os.getpid(),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "tk_version": float(tk.TkVersion),
        "tcl_version": float(tk.TclVersion),
    }


# ===========================================================================
# The gate
# ===========================================================================


def _gate_is_on() -> bool:
    return os.environ.get(GATE_ENV, "").strip() == "1"


def _evidence_root() -> Path:
    """The parent directory both renderers claim a subdirectory of."""
    raw = os.environ.get(EVIDENCE_DIR_ENV, "").strip()
    if not raw:
        pytest.fail(
            f"{EVIDENCE_DIR_ENV} is required when {GATE_ENV}=1; the artifact is "
            "the deliverable and must be written outside the repository, e.g. "
            "under %TEMP%"
        )
    try:
        resolved = Path(raw).resolve()
    except OSError:
        resolved = Path(raw)
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        pytest.fail(
            f"{EVIDENCE_DIR_ENV}={resolved} resolves inside the repository "
            f"({REPO_ROOT}). This artifact is a record of a machine, not a "
            "source file: writing it into the checkout would put an untracked "
            "measurement in a commit and a stale one in the next clone."
        )
    return resolved


def _claim(directory: Path) -> Path:
    """Create the directory, refusing to reuse one.

    A second run pointed at the same directory would overwrite the first
    measurement. Failing here is the signal; the alternative is an artifact
    that silently describes whichever run happened to finish last.
    """
    try:
        directory.mkdir(parents=True)
    except FileExistsError:
        pytest.fail(
            f"{directory} already exists. A second run would destroy the "
            f"measurement already in it; point {EVIDENCE_DIR_ENV} at a fresh "
            "directory."
        )
    except OSError as error:
        pytest.fail(f"{directory} could not be created: {error}")
    return directory


def _watchdog_timeout_s() -> float:
    raw = os.environ.get(WATCHDOG_ENV, "").strip()
    if not raw:
        return DEFAULT_WATCHDOG_TIMEOUT_S
    value = float(raw)
    if value <= 0.0:
        pytest.fail(f"{WATCHDOG_ENV}={raw} must be positive")
    return value


# ===========================================================================
# The tests
# ===========================================================================


@pytest.mark.parametrize("renderer", RENDERERS)
def test_renderer_settles_and_keeps_drawing(
    renderer: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an opted-in run pointed at a fresh directory outside the checkout.
    if not _gate_is_on():
        pytest.skip(
            f"set {GATE_ENV}=1 with {EVIDENCE_DIR_ENV} (outside the repository) "
            "to run the real-Tk renderer E2E"
        )
    directory = _claim(_evidence_root() / renderer)
    timeout_s = _watchdog_timeout_s()

    # When: a real Tk root carries a real CaptureArea on this backend, and the
    # production 60 Hz preview clock runs on it.
    report = _run_renderer(renderer, directory, timeout_s, monkeypatch)

    # Then: the run rendered the backend that was asked for. A silent fallback
    # would otherwise publish two copies of one measurement and call it parity.
    assert report["renderer_requested"] == renderer
    assert report["surface_class"] == EXPECTED_SURFACE_CLASS[renderer], (
        f"the {renderer!r} renderer produced {report['surface_class']!r}; "
        "_create_preview_surface fell back, so this run says nothing about the "
        "backend it was named for"
    )

    # Then: the surface reported its own visibility state. A covered or unmapped
    # window still advances presents, so counting presents alone cannot tell
    # "drawn" from "drawn somewhere invisible". The artifact above is retained;
    # the verdict is a skip with the outcome named, never a green pass.
    # Only known environmental factors skip: covered (occluded by another
    # window) and not_mapped (polling timeout did not resolve). Everything
    # else -- not_run (attach failure), empty_box (degenerate size),
    # wrong_color (sentinel mismatch), pending (the poll never completed), and
    # a missing verdict outright -- is a real regression and FAILs.
    # photo's "visible" means "viewable and non-empty size" only; GDI's
    # "sentinel_matched" proves pixels arrived (sentinel write + readback).
    # The artifact is the real evidence for both.
    assert report["surface_selftest"] is not None, (
        "surface_selftest is missing: CaptureArea recorded no verdict at all, "
        "so nothing was proven. See the log line for _run_selftest"
    )
    outcome = report["surface_selftest"]["outcome"]
    # "pending" means the after() poll never completed inside its budget, and a
    # never-ran verdict is a broken run -- not an environmental skip. Left
    # unhandled it would fall through to the equality assert below anyway, so
    # naming it here keeps the failure about what actually went wrong.
    assert outcome != "pending", (
        "surface_selftest outcome='pending': the self-test never ran; see "
        "surface_selftest in report.json"
    )
    # Freshness: the widget's mirror must agree with the surface's own
    # end-of-run verdict. _poll_selftest stops at the first definitive
    # outcome (GuiAssets.py:470-492), so a transient "covered" during
    # mapping freezes into the mirror while resize keeps re-testing the
    # surface -- without this check that staleness hides behind the
    # occlusion skip below and the run reads green while proving nothing.
    live = report["surface_selftest_live"]
    assert live is not None, (
        "surface_selftest_live is missing: the surface's self_test() was not "
        "read at the end of the run, so staleness cannot be judged"
    )
    assert live["outcome"] == outcome, (
        f"stale self-test mirror: mirror={outcome!r} but the surface's own "
        f"end-of-run verdict={live['outcome']!r}; _poll_selftest froze on an "
        "early outcome while resize re-tested; see surface_selftest_live in "
        "report.json"
    )
    if renderer == "gdi" and outcome == "covered":
        pytest.skip(
            f"gdi self-test outcome={outcome!r}: window occluded by another; "
            "see surface_selftest in report.json"
        )
    if outcome == "not_mapped":
        pytest.skip(
            f"self-test outcome={outcome!r}: window not viewable after "
            "polling timeout; see surface_selftest in report.json"
        )
    expected = "sentinel_matched" if renderer == "gdi" else "visible"
    assert outcome == expected, (
        f"{renderer} self-test outcome={outcome!r} != {expected!r}: "
        "pixels unproven on this run; see surface_selftest in report.json"
    )
    # The dict is filled one row per guard from the live source, so an empty
    # dict (the probe never ran) has to fail as loudly as a missing token.
    guards = report["production_resize_guards_present"]
    assert guards, "the resize guard probe recorded nothing"
    missing = sorted(label for label, present in guards.items() if not present)
    assert not missing, f"resize guards missing from the production source: {missing}"

    # Then: and it terminated. The watchdog is the claim, not the absence of an
    # exception -- a hang produces no exception to miss.
    assert report["watchdog_fired"] is False, (
        f"the watchdog fired after {report['watchdog_timeout_s']}s; "
        f"{(directory / WATCHDOG_ARTIFACT)} records why"
    )
    assert report["watchdog_artifact_present"] is False
    assert report["run_elapsed_s"] <= report["watchdog_timeout_s"], report[
        "run_elapsed_s"
    ]

    # Then: and the box settled rather than chased. Non-vacuous first: Tk must
    # have delivered geometry at all, or a trace of zeroes is indistinguishable
    # from a healthy one.
    assert report["configure_event_count"] >= 1, (
        "no <Configure> was observed, so this run measured a surface that never "
        "had to settle; the bound below would be satisfied vacuously"
    )
    assert report["effective_resize_count"] <= MAX_EFFECTIVE_RESIZES, (
        f"{report['effective_resize_count']} resizes were accepted for "
        f"{report['configure_event_count']} <Configure> events, above the bound "
        f"of {MAX_EFFECTIVE_RESIZES}. {BOUND_RATIONALE}"
    )

    # Then: and the box Tk reports before the geometry manager settles never
    # became a box. This is the 1x1 storm, refuted by observation.
    assert report["effective_resize_count_with_width_le_1"] == 0, (
        "a <Configure> of width <= 1 produced an effective resize, so a "
        "surface that cannot draw in 1 pixel accepted the box it can never "
        "recover from"
    )

    # Then: and the surface was not merely quiet. It was drawing, through the
    # whole run, at the production target.
    assert report["clock_mode"] == "high_resolution", (
        f"the production pacer fell back to {report['clock_mode']!r}; this run "
        "claims the 60 Hz path and would be measuring the fallback instead"
    )
    assert report["presents_at_start"] >= 1
    assert report["presents_advanced"] is True, (
        f"presents stood still at {report['presents_advanced'] and report['presents_ok']} "
        f"after starting from {report['presents_at_start']}; a surface that "
        "resized without hanging but stopped drawing is not a pass"
    )
    assert report["presents_ok"] > report["presents_at_start"]
    assert report["present_calls"] == report["presents_ok"], (
        "present() was refused at least once; the resize measurement would then "
        "be taken while the surface was already refusing to draw"
    )

    # Then: and the teardown completed on the same thread that ran the clock.
    teardown = json.loads((directory / TEARDOWN_ARTIFACT).read_text(encoding="utf-8"))
    assert teardown["stop_result"] == "stopped", teardown
    assert teardown["surface_released"] is True, teardown
    assert teardown["root_destroyed"] is True, teardown
    assert teardown["watchdog_fired"] is False

    # Then: and the artifacts are what the claims above were read from, still
    # matching their published hashes.
    manifest = json.loads((directory / MANIFEST_ARTIFACT).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["watchdog_artifact"]["exists"] is False
    _verify_manifest(directory, manifest)
    trace_lines = (
        (directory / GEOMETRY_TRACE_ARTIFACT).read_text(encoding="utf-8").splitlines()
    )
    stream = report["non_vacuity_control"]["stream"]
    assert len(trace_lines) == report["configure_event_count"] + 2 * len(stream), (
        f"geometry_trace.jsonl holds {len(trace_lines)} rows for "
        f"{report['configure_event_count']} observed <Configure> events and two "
        f"control passes of {len(stream)}; it must hold one row per <Configure> "
        "of the run and of both control passes"
    )


def test_the_resize_bound_can_actually_be_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same run, on the backend whose refusals are most load-bearing.
    if not _gate_is_on():
        pytest.skip(
            f"set {GATE_ENV}=1 with {EVIDENCE_DIR_ENV} (outside the repository) "
            "to run the real-Tk renderer E2E"
        )
    directory = _claim(_evidence_root() / "non-vacuity")
    timeout_s = _watchdog_timeout_s()

    # When: the guards are removed and the identical stream is replayed.
    report = _run_renderer("gdi", directory, timeout_s, monkeypatch)
    control = report["non_vacuity_control"]
    assert "error" not in control, (
        f"the non-vacuity control did not complete: {control['error']}"
    )

    # Then: the control exceeded the bound the real run is held to, through the
    # same recorder, the same counting seam and the same constant. A gate that
    # cannot fail is not a gate: without this, a run in which the counters were
    # never wired up would be indistinguishable from a healthy one.
    assert control["unguarded_effective_resize_count"] > MAX_EFFECTIVE_RESIZES, (
        f"the unguarded resize committed "
        f"{control['unguarded_effective_resize_count']} times over "
        f"{control['event_count']} events, which does not exceed the bound of "
        f"{MAX_EFFECTIVE_RESIZES}; the control is not exercising the failure it "
        "exists to demonstrate"
    )

    # Then: and the guarded pass over the very same stream stayed inside the
    # bound. One variable changed between the two numbers, so the difference is
    # the refusals and not the stream, the recorder or the counter.
    assert control["guarded_effective_resize_count"] <= MAX_EFFECTIVE_RESIZES, (
        f"the guarded resize committed {control['guarded_effective_resize_count']} "
        f"times over {control['event_count']} events, so the real run's bound is "
        "not a property of the guards either"
    )
    assert (
        control["guarded_effective_resize_count"]
        < (control["unguarded_effective_resize_count"])
    ), (
        f"both passes committed {control['guarded_effective_resize_count']} times; "
        "removing the refusals changed nothing, so the guarded number is not "
        "measuring the guards"
    )

    # Then: and the unguarded pass accepted *every* event, which is the whole
    # claim: with the refusals gone there is nothing to converge, so the count
    # is the length of the stream and nothing else. This is the shape a runaway
    # takes, and it is what the real run's bound is denying.
    assert control["unguarded_effective_resize_count"] == control["event_count"], (
        f"the unguarded resize accepted "
        f"{control['unguarded_effective_resize_count']} of "
        f"{control['event_count']} events; the demonstration needs every one of "
        "them accepted, or the stream is not reaching the surface at all"
    )

    # Then: and the 1x1 rows are where the two passes separate. Guarded, every
    # one of them is refused; unguarded, every one of them becomes a box. That
    # is the claim the real run makes about the storm Tk actually sends, shown
    # to be load-bearing rather than unreachable.
    assert (
        control["unguarded_rows_with_width_le_1_resized"] == control["degenerate_rows"]
    ), (
        f"the unguarded resize accepted "
        f"{control['unguarded_rows_with_width_le_1_resized']} of "
        f"{control['degenerate_rows']} degenerate rows; the demonstration needs "
        "all of them accepted"
    )
    assert control["guarded_rows_with_width_le_1_resized"] == 0, (
        f"the guarded resize accepted "
        f"{control['guarded_rows_with_width_le_1_resized']} degenerate row(s) "
        "while already holding the box the clamp produces, so the real run's "
        "'no resize at width <= 1' assertion is not describing the guard it "
        "claims to corroborate"
    )
