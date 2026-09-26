# GDI Preview Renderer 窶・1280x720 蝗ｺ螳壼・蜉帛燕謠舌・譛蟆乗ｧ区・

Status: design for review, not yet implemented.
Supersedes the `StretchDIBits` + Tk-Canvas-overlay shape of the 60 Hz plan.

## 0. Why the previous shape was abandoned

Two facts, both established by execution on this machine, not by reasoning.

1. A child window is always composited **above** its parent's painted pixels.
   `WS_CLIPCHILDREN` stops the parent painting *into* the child's rect; it does
   not make the child transparent. So a GDI child holding the video cannot sit
   behind a Tk Canvas that draws the stick / `SelectArea` / `ImgRect` overlay.
2. Tk 8.6 rejects `-background ""` on a Canvas (`unknown color name ""`), so the
   Canvas cannot be made see-through. The alternative 窶・blitting into the
   Canvas's own HWND 窶・works, but `cv.coords()` repaints the item's old bounding
   box **from the canvas background**, erasing the video. Measured: after
   `cv.coords()` the vacated box read `(204,204,204)`, the canvas background.
   That is `_moveKnob` at `GuiAssets.py:997` and `ImgRect` at `:1232-1233`,
   both running at 125 Hz during a stick drag.

Therefore the video and the overlay share one surface, and the overlay is drawn
by the renderer rather than by Tk. This is the user's decision.

## 1. Operating assumption

`capture_size == (1280, 720)` is fixed. Every automation feature 窶・image
recognition, coordinate readout, range selection, click position, `ReleaseRangeSS`,
the record features 窶・works in the 1280x720 capture coordinate system.

640x360, 1920x1080 and any other size are deferred. The design does not
generalise for them. `CoordinateMapper` exists so the coordinate systems are
separated and the transform has one obvious home, but the first release
implements and proves only the `capture == display` path.

The performance target is 1280x720 @ 60 fps. Draw quality and stability at that
size come first.

## 2. Measured budget at 1280x720

300 iterations after 50 warmup, all three arms in the same session, because
cross-session comparison proved unreliable (see the caveat at the end).

| stage | median | p99 | max |
|---|---|---|---|
| `cv2.cvtColor(frame, BGR2BGRA, dst=back)` | 0.0860 ms | 0.2255 | 0.4399 |
| GDI shapes (2 `Ellipse` + 2 `Rectangle`) | 0.0167 ms | 窶・| 窶・|
| `BitBlt` 1:1 `SRCCOPY` to the child window DC | 0.3273 ms | 窶・| 窶・|
| `GetDC` + `ReleaseDC` x2 | 0.0236 ms | 窶・| 窶・|
| **full tick** | **0.4536 ms** | **1.0444** | **1.1616** |

**2.7% of the 16.667 ms budget.** Budget split: blit 72%, fill 19%, DC 5%,
shapes 4%. The blit is the bottleneck and always was; it is not urgent at this
margin, so no dirty-rectangle work and no blit-mode tuning is justified.

Head to head, isolated sub-timings from the same session:

| variant | fill | BitBlt | total |
|---|---|---|---|
| **32bpp + `cvtColor(dst=)`** | 0.0860 | 0.3273 | **0.4536** |
| 24bpp + `np.copyto` | 0.1399 | 0.8862 | 1.0664 |
| 32bpp + numpy `[:, :, :3]` | 3.5934 | 0.3273 | 3.5934 |

## 3. Back buffer is 32bpp, and the bit depth is queried

An earlier draft of this document specified a 24bpp back buffer on the theory
that stride `w*3` would make the frame copy a plain memcpy. Measurement refuted
it on both counts, and the 24bpp variant loses at *neither* stage in its favour:

- The child window's DC is **32bpp** (`GetDeviceCaps(GetDC(child), BITSPIXEL) = 32`,
  matching `dmBitsPerPel` for both attached displays). A 24bpp source therefore
  forces GDI to convert every pixel: the 24bpp blit costs 0.8862 ms against
  0.3273 ms for 32bpp, a reproducible 2.71x penalty. A 24bpp-to-24bpp
  memory-to-memory `BitBlt` is only 0.0518 ms, which isolates the conversion as
  the cause.
- The 32bpp fill via `cv2.cvtColor` is **cheaper than the 24bpp memcpy**
  (0.0860 vs 0.1399 ms). The 24bpp buffer wins neither stage.

So the back buffer matches the **destination**, not the source. `bpp` is read
once at attach from the child window DC and clamped to {16, 24, 32}; it is a
display property, so hardcoding it is not safe in general. Query the **window**
DC 窶・`GetDeviceCaps` on a memory DC is unreliable on this machine and returned
`BITSPIXEL = 0`.

A 32bpp row is always `w * 4`, so the stride is unconditionally tight and the
`w % 4 == 0` constraint that 24bpp carried does not apply.

### The fill is OpenCV, and that is load-bearing

| fill into the 32bpp DIB | median |
|---|---|
| **`cv2.cvtColor(frame, BGR2BGRA, dst=back)`** | **0.0851** |
| `back[:, :, :3] = frame` | 2.9217 |
| `np.copyto(back[:, :, :3], frame)` | ~2.9窶・.0 |
| `np.copyto(back.reshape(-1,4)[:, :3], ...)` | 3.1525 |
| alpha prefilled once, then the strided write | 3.2014 |
| `np.concatenate(..., out=back)` | 4.0042 |

The best numpy arm is **37.6x slower** than `cvtColor`. `np.dstack` has no
`out=`; `np.copyto` will not auto-pad a missing trailing channel. No numpy path
is competitive, so OpenCV stays in this path deliberately.

`cvtColor` with `dst=` writes in place 窶・the base pointer is unchanged and
`back[:, :, :3] == frame` elementwise 窶・and is **8.2x cheaper** than the
allocating form, which is disqualified outright. The design requires no per-frame
allocation.

`cvtColor` also sets the alpha byte to `0xFF` unconditionally every frame, so no
alpha prefill is needed on this path. That matters only on a hypothetical
numpy-only path, where the prefill costs 0.2028 ms once at attach.

### Colour order

Memory order for both 24bpp and 32bpp `BI_RGB` is B, G, R, which is OpenCV's
native order. The `cv2.cvtColor(BGR2RGB)` at `GuiAssets.py:629` exists only for
Tk's `PhotoImage` and is **deleted outright**; `BGR2BGRA` keeps the channel order
and only appends alpha. Measured mapping: `COLORREF = (ch0 << 16) | (ch1 << 8) |
ch2`, i.e. numpy channel N surfaces as COLORREF byte (2 - N). Verified over all
921,600 pixels of a random 720p frame.

`StretchDIBits` and `SetDIBitsToDevice` are both absent from this design. The
former is broken on this machine and the latter is superseded by the back buffer.

### Measurement caveat

The arm-level tick numbers were bimodal 窶・arms A and B swung 3窶・x between passes
while the isolated sub-timings stayed stable to within 0.9%. Whatever causes it
acts on the `BitBlt`-to-the-window step. **Do not trust a single arm-level tick
in any future measurement of this renderer; measure the stages.** The decision
above rests on the isolated sub-timings for that reason.

### Input precondition

`cvtColor` into `dst=` requires a C-contiguous source, and the live path hands the
renderer raw `VideoCapture.read()` output whose stride at 1280x720 has not been
verified on this machine. The assertion is `frame.flags.c_contiguous` (equivalently
`strides == (w * 3, 3, 1)`) 窶・**not** `strides[0] >= w * 3`, which every
C-contiguous array satisfies and which therefore proves nothing.

## 4. Architecture

```
camera thread                 main thread (after_idle, dirty-flag gated)
-----------                   -------------------------------------------
Camera._update
  read() -> fresh BGR ndarray
  t_capture_ns = perf_counter_ns()
  [deferred: output-size resize, no dst=]
  LatestFrame.put(frame, t_capture_ns)
      -> t_ready_ns sampled in-lock
                              PreviewClock._dispatch_one_tick
                                Camera.readFrameWithTiming()
                                  -> FrameSnapshot(frame, seq, t_c, t_r)
                                1:1 check: frame.shape == surface size
                                Renderer.compose(frame, overlay)
                                  np.copyto(back, frame)      # 0.18 ms
                                  GDI shapes into memDC       # tens of us
                                Renderer.present()
                                  BitBlt memDC -> child DC    # 0.25 ms
```

The worker never touches GDI and never enters Tk. The main thread only blits.

## 5. A. Renderer interface

`SerialController/core/preview_renderer.py` 窶・tkinter-free, so it satisfies
`task bounds` for `core/`.

```python
class PreviewRenderer(Protocol):
    def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None: ...
    def resize(self, size: tuple[int, int]) -> None: ...
    def compose(self, frame: np.ndarray, overlay: OverlayState) -> RenderResult: ...
    def present(self) -> RenderResult: ...
    def release(self) -> None: ...
    def client_size(self) -> tuple[int, int]: ...
```

`RenderResult` is frozen:

```python
@dataclass(frozen=True, slots=True)
class RenderResult:
    ok: bool
    elapsed_ns: int
    detail: str = ""      # "ok" | "dimension_mismatch" | "no_frame" | "no_hwnd"
```

`compose` and `present` are separate so instrumentation can time the composite
and the blit independently, which is the `t_blit` metric split the plan requires.
Both return a `RenderResult` rather than raising: a failed present must never
take down the Tk mainloop.

### Overlay state 窶・logical, not graphical

Canvas items are abolished. The overlay is data; the renderer draws it.

```python
@dataclass(frozen=True, slots=True)
class StickState:
    active: bool = False
    center_x: int = 0        # 螟門捉蜀・・荳ｭ蠢・よ款荳倶ｸｭ繧ょ虚縺九↑縺・ｼ・uiAssets.py:978-982・・    center_y: int = 0
    radius: int = 0
    knob_x: int = 0          # 繝弱ヶ縺ｮ荳ｭ蠢・ゅラ繝ｩ繝・げ縺ｧ蜍輔￥・・997・・    knob_y: int = 0

@dataclass(frozen=True, slots=True)
class RectState:
    x0: int = 0
    y0: int = 0
    x1: int = 0
    y1: int = 0
    visible: bool = False

@dataclass(frozen=True, slots=True)
class ImgRectState:
    outer: RectState = RectState()   # 4px 逋ｽ縲Ｄapture 蠎ｧ讓・+1.0 陬懈ｭ｣貂医∩・・1218・・    inner: RectState = RectState()   # 2px 隱崎ｭ倩牡縲り｣懈ｭ｣縺ｪ縺暦ｼ・1221・・    visible: bool = False

@dataclass(frozen=True, slots=True)
class ImgRectState:
    outer: RectState = RectState()   # 4px white
    inner: RectState = RectState()   # 2px recognition colour
    visible: bool = False
    color: int = 0                   # COLORREF of the inner border
```

`ImgRectState` carries two rectangles because the current code draws two
(`:1224-1229`): a 4.5 px white outer border and a 2.5 px recognition-coloured
inner border, and they are **not** the same rectangle 窶・the outer is computed
with a `+1.0` capture-pixel expansion (`:1218`) and the inner without (`:1221`).
Collapsing them to one rectangle would silently drop the white border.

`color` lives on the overlay state rather than on the surface because ﾂｧ5's thesis
is "the overlay is data; the renderer draws it". A surface-level colour setter
would be hidden mutable state the caller must remember before every compose, and
the Protocol's `compose(frame, overlay)` signature carries no other channel for
it. It is the fourth appended field, so `ImgRectState(outer, inner, visible)`
still constructs positionally.

**Tk and Win32 disagree on colour encoding.** Tk's internal form is
`0x00RRGGBB`; Win32 `COLORREF` is `0x00BBGGRR`. Every colour crossing from the
current Tk code into a pen or brush is converted at the boundary 窶・this is a real
bug surface, not a formality, and it is why `color` is specified as a `COLORREF`
rather than as a Tk colour name.

All coordinates are **display** coordinates in pixels, matching what the Canvas
used. Immutable and replaced wholesale, so a render can never observe a
half-updated overlay. `OverlayState` is captured on the main thread at compose
time; nothing crosses threads.

### GDI drawing of the overlay

Drawn with GDI calls into the memory DC after the frame copy, never with numpy
rasterisation. A 2.76 MB numpy pass per shape would cost more than the shapes.

`k = radius // 10` is the knob radius, inherited from `GuiAssets.py:980`.

**Extent convention.** GDI excludes the right and bottom edge of a bounding box,
so a call `Ellipse(hdc, l, t, r, b)` paints `extent = r - l - 1` pixels. A Tk
`create_oval`/`create_rectangle` with the same coordinates paints `r - l + 1`
pixels, because its box is inclusive. Every shape therefore passes `right + 1`
and `bottom + 1` to restore the Tk extent exactly. Omitting it renders every
overlay one pixel narrower and one pixel shorter than it looks today.

This compensation is **orthogonal** to the `+1.0` capture-pixel expansion on
`ImgRect`'s outer rectangle at `:1218`: that one widens the white border in
capture space, this one compensates for the GDI bounding-box rule. Both apply.

In order, and only for components that are active:

| overlay | GDI call | current Tk call it replaces |
|---|---|---|
| left ring | `Ellipse(hdc, cx-r-1, cy-r-1, cx+r+1, cy+r+1)` | `create_oval` `:982` |
| left knob | `Ellipse(hdc, kx-k-1, ky-k-1, kx+k+1, ky+k+1)`, **solid** brush | `create_oval` `:983` |
| right ring | as left | right-stick equivalent |
| right knob | as left, solid | right-stick equivalent |
| `SelectArea` | `Rectangle(hdc, x0-1, y0-1, x1+1, y1+1)`, dashed pen | `create_rectangle` `:733-740` |
| `ImgRect` outer | `Rectangle(hdc, o.x0, o.y0, o.x1+1, o.y1+1)`, 4 px white | `create_rectangle` `:1224-1226` |
| `ImgRect` inner | `Rectangle(hdc, i.x0, i.y0, i.x1+1, i.y1+1)`, 2 px, cached colour | `create_rectangle` `:1227-1229` |

An all-default `OverlayState` issues **zero** shape calls. Left stick alone
issues 2, guide alone 1, `ImgRect` alone 2, everything together 7.

**Two stroke widths are rounded.** Tk accepts the float widths `4.5` and `2.5`;
`CreatePen` takes an integer. The port uses 4 and 2, which is within half a
pixel of the original and imperceptible. The knob is the one filled shape
(`:983` passes `fill=`), so it needs a solid brush in the stick's colour while
every other shape uses `NULL_BRUSH`.

### GDI object inventory

Created once at `attach`, never per frame:

- 4 static pens: left ring, right ring, guide (`PS_DASH`, width 1), `ImgRect`
  outer (`PS_SOLID`, width 4, white)
- 1 `NULL_BRUSH`
- 1 solid brush per stick colour, for the knob

The `ImgRect` inner colour is dynamic, taken from the recognition result, so it
cannot be one of the static pens. Pens for it are cached by
`(style, width, colour)` in a dict, created on first use and reused; the cache is
keyed, not appended, so returning to a previously used colour reuses the original
handle. It is bounded by the number of distinct recognition colours actually in
use, which is small and stable, and it is emptied on `release`. This is the one
place a GDI allocation is permitted after attach, and only on an actual colour
change.

Shape drawing is not a cost concern: 2 `Ellipse` + 2 `Rectangle` measured
0.0167 ms on the 32bpp buffer, about 4% of the tick. The shipped shape set is
larger 窶・4 ellipses and 3 rectangles for a full overlay, and the knob is filled
rather than hollow 窶・so the real figure is somewhat above 0.0167 ms, still
negligible against the 16.667 ms budget.

## 6. B. Child HWND and video present

`SerialController/core/gdi_surface.py` 窶・tkinter-free; it receives the parent
HWND as an `int`, so it needs no Tk import and can live in `core/`.

State it owns for its whole life:

- the window class, registered **once per process** and deliberately never
  unregistered (see teardown, section 11)
- the child HWND
- the memory DC and the DIB section, whose bit depth is **queried** from the
  child window DC (section 3)
- the numpy view over `ppvBits`, shaped `(h, w, 4)` for the 32bpp branch that
  ships
- the pens and brushes: a `NULL_BRUSH` for every outline, one solid brush per
  stick colour for the knob, and a pen cache keyed by `(style, width, colour)`
  for the dynamic recognition colour

Creation, in order:

1. `RegisterClassExW` with `hbrBackground = NULL` and
   `lpfnWndProc = ctypes.cast(DefWindowProcW, ctypes.c_void_p)`. **No Python
   window procedure is created.** The probe proved content survives forced
   `InvalidateRect(erase=1)+UpdateWindow` and
   `RedrawWindow(INVALIDATE|ERASE|ALLCHILDREN)` with exactly this class, and the
   project already uses the same trick at `preview_clock.py:470`.
2. `CreateWindowExW` with `WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS`, parented to
   the Frame's HWND, at `(0, 0, w, h)`.
3. `EnableWindow(child, FALSE)` so the 125 Hz Tk `bind()` stick control keeps
   receiving mouse input. The probe confirmed painting is unaffected.
4. `CreateDIBSection` for 32bpp top-down, `CreateCompatibleDC`,
   `SelectObject` the HBITMAP into it, and wrap `ppvBits` as
   `np.ctypeslib.as_array(...)` 竊・`reshape(h, w, 4)`. Query `bpp` from
   `GetDeviceCaps(GetDC(child), BITSPIXEL)` first, per section 3.
5. `SetWindowPos(child, HWND_TOP, 0, 0, w, h, SWP_SHOWWINDOW)`.

Sizing is against `GetClientRect(frame_hwnd)`, **not** `winfo_width`: with default
Tk borders the HWND client area exceeds `winfo_width` by `2 * bd`, and only
`bd=0, highlightthickness=0` makes them equal. This box is also 150% DPI
(`GetDpiForSystem=120`) on a 7680x2160 virtual desktop, so `SetProcessDPIAware()`
must be called before any screen-coordinate reasoning or `GetWindowRect` is
virtualised and the 1:1 check breaks silently.

**The 1:1 check.** `client_size()` returns the child's `GetClientRect` as a cheap
integer pair. The present path compares it against `frame.shape[1::-1]`. On
mismatch the frame is **discarded and counted** (`detail="dimension_mismatch"`);
it is never scaled. Under the fixed assumption this cannot happen, which is the
point 窶・the check exists so a future size change fails loudly instead of
producing a stretched or torn image.

**Windows declarations that must not be got wrong.** The probe found handles on
this box are sometimes above 32 bits and sometimes not, so a missing `argtypes`
fails *intermittently* and survives a smoke test. One run returned
`HBITMAP=0xFFFFFFFFBD051BBE`, whose bits 63..32 are all ones 窶・indistinguishable
from a sign-extended 32-bit handle 窶・while the next run returned `0x6C053A7E`.
**Both `argtypes` and `restype` are mandatory on every call**, not just
`restype`:

- `restype = ctypes.c_void_p` on `CreateDIBSection`, `CreateCompatibleDC`,
  `SelectObject`, `GetDC`, `CreateWindowExW`, `GetWindowDC`.
- `argtypes` on all of the above. `GetDC.argtypes = [c_void_p]`; with the naive
  default the HDC is truncated to 32 bits and the next call faults.
- `GetPixel.restype = c_ulong`, and the DIB dword is `0x00BBGGRR` in memory, so
  any readback comparison must convert rather than compare dwords.
- `SetWindowLongPtrW.restype = c_ssize_t`, same for `GetWindowLongPtrW`.
- `WNDCLASSEXW.lpfnWndProc` declared `c_void_p`. `ctypes.wintypes.WNDPROC` is a
  callable type and would force a Python function.
- `SelectObject` returns the **previously** selected object, so it is not a valid
  success check. Verify with a pixel round-trip. `GetCurrentObject` is unusable
  on this box (returns NULL, `GetLastError=87`).
- `GetGuiResources` lives in `user32`, not `kernel32`.
- `gdi32.GetObject` does not exist; it is `GetObjectW`.
- `GetDIBits` on a **window** DC with `hbm=NULL` returns 0 scan lines silently,
  with `GetLastError()==0`, and the header you then read is your own input echoed
  back. To read a window's real format, go through a
  `CreateCompatibleBitmap(child_hdc, 16, 16)` DDB. Do not use the `hbm=NULL` form
  for verification.
- `EnumDisplaySettingsW` silently returns garbage unless `dmSize` is set first
  from a correctly sized raw buffer.

## 7. C. WM_PAINT

Nothing to do, deliberately.

With `hbrBackground = NULL` and `DefWindowProcW`, `WM_PAINT` is a no-op and
`WM_ERASEBKGND` is suppressed. The probe verified the presented pixels survive
`InvalidateRect` with erase, and `RedrawWindow` with
`INVALIDATE | ERASE | ALLCHILDREN`, inside a real Tk mainloop, and were readable
on screen by direct `GetPixel` on the screen DC.

**We deliberately create zero Python window procedures.** The project already
defines `_WNDPROC_TYPE` at `preview_clock.py:209` and never instantiates it; the
file registers its class with a cast `DefWindowProcW`. Adding a second
`WINFUNCTYPE` trampoline is the `0xC0000409` shape that a previous crash was
traced to. If a real proc is ever needed, reuse `_WNDPROC_TYPE`.

The one caveat the probe found is a **detection** problem, not a fix, and it has
three distinct shapes that need three distinct signals:

| condition | child DC read | screen DC read | `GetClipBox` | far probe |
|---|---|---|---|---|
| healthy | sentinel | sentinel | `(0,0,1280,720)` | far sentinel |
| foreign occluder above | **fails** | sentinel (false negative) | **`(0,0,0,0)` empty** | fails |
| video child is the 2nd child | **fails** | sentinel (false negative) | **`(0,0,0,0)` empty** | fails |
| Tk `Label` over the child | **fails** | `0xFF00FF` (the label) | `(0,0,1280,720)` full | far sentinel |

The root cause of the previously unexplained case is **clipping**: a fully
covered child window's DC has an empty clip region, so `BitBlt` has no
destination pixels, draws nothing, and still returns 1 with
`GetLastError()==0`. Not the class, not z-order, not first paint.

Three consequences the self-test must absorb:

- **Read the child DC, never the screen DC.** For a foreign occluder the screen
  still shows the previous frame, so a screen readback is a false negative 窶・the
  user sees a *frozen* picture, not a wrong one, which is the harder failure to
  notice.
- `CLR_INVALID` is indistinguishable from "`GetPixel` unsupported" and needs its
  own outcome label, not to be folded into "wrong colour".
- `WindowFromPoint` returned NULL at every probe in every run, so the occluder
  cannot be named by hit-testing or a z-order walk. `GetClipBox` emptiness is the
  usable positive signal for the fully-covered case; nothing distinguishes the Tk
  case except a failed point read.

The design therefore has exactly one foreign child, and a one-time self-test
after the first present: write a known sentinel, `BitBlt`, read it back from the
**child** DC, and on `CLR_INVALID` report `covered` and additionally check
`GetClipBox` so the log can say whether the child is fully occluded. Once per
surface, never per frame.

## 8. D and E. Overlay wiring into CaptureArea

`CaptureArea` changes base class from `tk.Canvas` to `tk.Frame`. All 23 canvas
item call sites go; the 29 non-drawing methods are untouched.

State replaces items: `self._stick_left`, `self._stick_right`, `self._guide`,
`self._img_rect`, all `OverlayState` components, updated in place by the mouse
handlers and read once at compose time.

Every mouse `bind()` moves to the Frame. Tk `bind` does not propagate from a
child to its parent, and the child is disabled, so the Frame is what receives
the events. `event.x` / `event.y` are Frame-relative and the child is at `(0,0)`
at the same size, so the coordinates are identical to today's Canvas-relative
ones and every clamp against `show_width` / `show_height` stays correct.

`cursor` moves to the Frame for the same reason: the cursor under the pointer
comes from the window being hit-tested.

`self.config(width=..., height=...)` in `setShowsize` configures the Frame; the
child is resized through `SetWindowPos` in the `<Configure>` handler, not through
Tk. The probe confirmed Windows moves children with the parent automatically but
**does not resize them**, so `<Configure>` is mandatory.

`_showDisabled` composites a BGR `ndarray` instead of swapping a `PhotoImage`.

### Non-Windows fallback

`AGENTS.md` requires mac/Linux users to keep working and non-`nt` paths to fail
gracefully. `ui/photo_surface.py` implements the same `PreviewRenderer` protocol
over `ImageTk.PhotoImage` for `os.name != "nt"`. That is a platform
requirement, not generalisation.

## 9. Coordinate systems

`SerialController/core/coordinates.py`:

```python
@dataclass(frozen=True, slots=True)
class CoordinateMapper:
    capture_size: tuple[int, int]
    display_size: tuple[int, int]

    def capture_to_display(self, x: int, y: int) -> tuple[int, int]: ...
    def display_to_capture(self, x: int, y: int) -> tuple[int, int]: ...
```

At `capture == display` both are the identity, which is the only path this
release implements and proves. The transform exists so the two coordinate systems
are never silently conflated again.

**`_captureRatio` (`GuiAssets.py:704`) is replaced by `display_to_capture`.** It
currently computes `camera.capture_size / show_size` and is correct only because
no runtime resize exists. The day one does, it returns 2.0 where it must return
1.0, which corrupts the `ReleaseRangeSS` crop (`:781-789`), the logged capture
coordinates (`:743`, `:768`) and the `mouseCtrlLeftPress` pixel probe (`:825`) 窶・all silently, because the existing `frame.shape` clamp keeps the probe in bounds
and it then reports the **wrong pixel**. Under the fixed assumption the current
code is already correct; the mapper makes that a property rather than a
coincidence.

## 10. F. Instrumentation

`PasteInstrumentation` becomes `PresentInstrumentation`. It wraps
`Renderer.compose` and `Renderer.present` instead of `area._photo.paste`.

Recorded per tick: `t_compose_ns`, `t_present_ns`, `t_total_ns`, `overlay_primitives`,
`frames_composed`, `frames_discarded_dimension_mismatch`.

Report and artifact renames, all in `tests/preview_fps_support.py`:

| current | new |
|---|---|
| `paste_summary` | `present_summary` |
| `paste_gaps` | `present_gaps` |
| `intervals.jsonl` | `present_intervals.jsonl` |
| `PasteRecord` | `PresentRecord` |
| `DispatchRecord.paste_observed` | `.present_observed` |

`REQUIRED_REPORT_FIELDS` and `REQUIRED_EVIDENCE_FILES` are updated, and
`REQUIRED_EVIDENCE_FILES` order is load-bearing 窶・the manifest-order assertion
compares against the tuple literally.

`SerialController/core/Camera.py` is added to `SOURCE_PIN_PATHS`, since Task 2
changed it and the pin list must cover the present path's inputs.

### The false-pass trap, closed

Today `paste_entries` feeds only `paste_summary`, `paste_gaps` and
`intervals.jsonl`. If the present path produced nothing, those would be silently
zero, `cadence_passed` would be `False`, and `_report_passed` would still return
`True` because it only reads `functional_accepted`. **A completely broken present
path would report GREEN.**

So: `evidence_ok` must additionally require `present_summary.count >= 2` for a
`native_production` run. Performance stays reference-only; a non-functional
present path does gate. A new test asserts a run with an empty present stream is
rejected.

## 11. Teardown

The probe found **no handle leak**: `GR_GDIOBJECTS` 36 -> 36 and process handles
254 -> 254 across 200 present cycles.

Order, on the GUI thread, reverse of construction, inserted as a new
`"surface_released"` phase between `preview_stopped` (`Window.py:486`) and
`stopping_services` (`:491`):

1. `stopCapture()` returns `StopResult.STOPPED` 窶・the only point at which no
   further `_dispatch_one_tick` can run.
2. Drop the frame reference. `lpvBits` points into the frame array; nothing after
   this may compose. This must precede `DestroyWindow`.
3. `DestroyWindow(child)`.
4. `DeleteDC(mem_dc)`. A DIB section dies with its DC 窶・calling `DeleteObject`
   on the HBITMAP as well is a double free. Then delete the pens and the brush.
5. **Do not** `UnregisterClassW`. The class is registered once per process and
   the surface can be recreated on camera reopen, so unregistering would make the
   second creation fail. The process exiting releases it.

`pending_teardown_proven` only asserts `stop_result == "stopped"`,
`root_destroyed_after_stop`, `window_class_destroyed` and
`window_class_unregistered` for the *clock's* class, so inserting a phase after
the stop proof and before root destroy cannot regress it.

## 12. Implementation order

Each step leaves the tree green.

| | step | exit criterion |
|---|---|---|
| A | `preview_renderer.py`: protocol, overlay state, `RenderResult` | importable, bounds-clean, unit tests green |
| B | `gdi_surface.py`: class, child HWND, 24bpp DIB, `BitBlt`, frame copy | the re-verification gate in section 3 passes; a standalone script presents a test pattern into a Tk Frame and reads it back |
| C | WM_PAINT policy: assert the class config and the no-trampoline property | a test asserts no `WINFUNCTYPE` is instantiated by the surface; the startup self-test exists and reports |
| D | stick overlay: `StickState` plus GDI ellipses; `CaptureArea` becomes a `tk.Frame`; binds and cursor move | the stick control still works at 125 Hz; `test_camera_threading.py::test_gui_draw_skips_same_seq` updated |
| E | `ImgRect` and `SelectArea` overlays | the recognition box and the range selector both draw and clear |
| F | `PresentInstrumentation`, report and artifact renames, `SOURCE_PIN_PATHS`, the `present_summary.count >= 2` gate | the false-pass test is red before the gate and green after |
| G | 1280x720 real-machine E2E | 60 fps reference recorded; `t_compose` and `t_present` both in the artifact; no fatal across a soak |

Steps A-C carry no `CaptureArea` risk. D is the real refactor and is deliberately
separated from the GDI work: the `PhotoImageSurface` fallback keeps the protocol
honest until GDI is proven.

## 13. Real-machine reference

Recorded on the development machine, Windows 11 23H2 build 22631.6199, Tk 8.6.12,
150% DPI, 1280x720, configured 60 fps, 180 Hz synthetic source, camera
substituted by `SyntheticFrameSource` so the numbers isolate the present path.

**Formal run**, 10 s warmup and a 60 s measurement window, which yields 3600
presents and so exceeds the 3000-frame requirement:

```
present : n=3600  mean=59.9998 Hz  p1=57.8235 Hz  span=59.9836 s
dispatch: n=3600  mean=59.9997 Hz  p1=58.0131 Hz  span=59.9836 s
period_skipped_count 0   blit_skipped_no_new_frame 0   suppressed_blit_interval_count 0
functional_accepted True   status passed   within_reference False
```

**Four 10 s runs** agree closely, so the mean is not a lucky sample:

| run | present mean | present p1 | dispatch p1 |
|---|---|---|---|
| smoke | 60.0012 | 57.967 | 58.205 |
| soak 1 | 60.003 | 57.827 | 58.012 |
| soak 2 | 60.001 | 57.720 | 58.095 |
| soak 3 | 60.000 | 57.881 | 58.047 |
| formal 60 s | 59.9998 | 57.824 | 58.013 |

**What this establishes.**

- The mean is on target to within 0.0002 Hz over 3600 frames, which is the
  headline: the pacer, the mailbox and the blit together sustain 60 fps at
  1280x720.
- **The blit is essentially free.** Present mean sits 0.0001 Hz from dispatch
  mean, so the 0.45 ms present costs nothing measurable against the dispatch
  cadence. That is the design's whole claim, now measured rather than predicted.
- Every skip counter is zero across all five runs: the pacer is never late by a
  whole period, no dispatch ever found an empty mailbox, and no nominal dispatch
  went missing.
- Three soak runs produced no fatal. This matters because the previous
  `0xC0000409` crash was traced to ctypes callback re-entry and this design puts
  a foreign HWND inside the Tk mainloop for the first time.

**What remains.** `p1` is 57.82 Hz against a 59.0 floor, and the decomposition
says the blit is not the cause: dispatch `p1` is 58.01 on the same run, so the
present path contributes about 0.19 Hz of the 1.19 Hz shortfall and the dispatch
path carries the rest. Since cadence is a recorded reference and not a gate, the
run is green 窶・but the `p1` gap is real and belongs to the dispatch path, which
this task did not change. Chasing it means instrumenting `_dispatch_tick` and the
`after_idle` hop rather than the renderer.

**Teardown**, verified on the gated teardown run with the new
`surface_released` phase in place:

```
pending_teardown_proven true    root_alive_while_pending true
root_destroyed_after_stop true window_class_destroyed true
window_class_unregistered true  preview_worker_alive false
pending_after_ids []           post_teardown_camera_read_count 0
```

The new phase did not regress the existing proof: it runs after the stop proof
and before the root is destroyed, and the GDI surface releases cleanly inside it.

## 14. Open items

### Carried out of steps A-C

- **The COLORREF byte-order conversion has no test.** The self-test sentinel is
  `0x0000FF00`, chosen because its high and low bytes are equal, which makes the
  round trip byte-order agnostic. So the self test proves "the child DC is
  readable and shows our pixels" and proves nothing about the conversion 窶・and
  that conversion is exactly the surface where Tk's `0x00RRGGBB` and Win32's
  `0x00BBGGRR` disagree. The recording double has the same blind spot: its
  `get_pixel` reconstructs Tk's order. **A follow-up assertion is required
  against `CtypesGdiApi`, not the double.** This is the highest-priority gap.
- **The guide rectangle is probably one pixel too wide on the left and top.**
  Section 5's extent convention says only `right+1` / `bottom+1`, but the
  `SelectArea` row and the test both specify `x0-1, y0-1, x1+1, y1+1`. The
  current Tk code builds the box as `min, min, max+1, max+1`, so the `-1` is
  probably wrong. Resolve in step E, where the range selector moves.
- **`gdi_surface.py` is 714 pure LOC**, over the project's 250 ceiling, because
  the 26-method api Protocol plus its ctypes implementation is roughly half of
  it. Split into `gdi_surface.py` and `win32_gdi_api.py` before it grows further.
- **Only the 32bpp branch is specified**, but `attach` is exercised at 16 and 24
  bpp because the bit depth is clamped to {16, 24, 32}. The 16/24bpp view and
  fill paths are unspecified. Either specify them or narrow the clamp.
- **`hInstance` is a sentinel, not a pass-through.** The api has no
  `get_module_handle`, so `instance=0` means "resolve `GetModuleHandleW(None)`
  yourself" inside `CtypesGdiApi`. MSDN requires the handle, so this is correct
  but the parameter does not mean what its name suggests.
- **`present` checks `no_frame` before taking the DC**, which deviates from the
  prose ordering but avoids a pointless `GetDC`/`ReleaseDC` pair at 60 Hz.
  Deliberate; recorded so it is not mistaken for an oversight.

### Still open from earlier sections

- **The bit depth is queried, not hardcoded.** The design ships the 32bpp branch,
  but the back buffer format is a property of the display, so `bpp` is read from
  `GetDeviceCaps(GetDC(child), BITSPIXEL)` at attach.
- **The stride of a real 1280x720 capture frame is unverified.** A camera was
  present during one probe, but the design-size reconfigure (MJPG + 1280x720)
  raised a `cv2.Mat` assertion on that run, so only a 640x480 frame was measured
  (`strides=(1920,3,1)`, C-contiguous). Hence the runtime `c_contiguous`
  assertion in section 3 rather than a one-time check.
- **The arm-level tick measurements are bimodal** and the cause is unexplained;
  it acts on the `BitBlt`-to-the-window step. Stage-level measurement is the only
  trustworthy method for this renderer.
- **Alpha compositing is unproven in the harmless direction.** `cvtColor` emits
  `0xFF` every frame and `WS_EX_LAYERED` is clear on the child, so alpha should be
  ignored; forcing it to `0x00` did not make the window vanish. But the probe had
  no demonstrated-transparent positive control, so "alpha is ignored" is
  *consistent with* the measurement, not proven by it.
- 640x360 and 1920x1080 are out of scope by decision. When they are revisited,
  the camera-side output-size plumbing does not exist yet 窶・`Camera.capture_size`
  is written only in `__init__` and read only by `_configure_capture` at open 窶・  and a producer-side resize must allocate fresh, never `dst=`, or it breaks the
  tear-free invariant committed in `f11b40a`. `SHOW_SIZE_VALUES` still offers
  1920x1080, so until that work lands the renderer must keep the 1:1 discard
  rather than attempt a scale.
- `preview_filter.apply_filter` and `apply_correction` both return BGR, so they
  compose into the back buffer unchanged.
- A non-`nt` box runs `PhotoImageSurface` and cannot hit 60 fps. That is a
  platform limit, not a regression.
