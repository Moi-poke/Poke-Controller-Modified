"""RED-only contracts for the GDI preview renderer; # noqa: SIZE_OK.

The approved brief requires one module covering design steps A, B and C of
``docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md``.

Neither production module exists yet, so every reference to them goes through a
lazy import helper typed ``Any``. That is the documented boundary convention
used elsewhere in this suite: the type checker must not pre-empt the runtime
RED, and a missing module must fail each test individually with
``ModuleNotFoundError`` / ``AttributeError`` rather than collapsing the whole
file into a single collection error.

``RecordingGdiApi`` is the seam that makes the surface testable headless: the
whole attach/compose/present/release pipeline runs against it with no display,
no window, no Tk and no GDI. Its first block is the *production contract* --
the only Win32 entry points ``GdiSurface`` may call on its injected api object.
Its second block is test-only inspection and fault injection.
"""

from __future__ import annotations

import ast
import ctypes
import dataclasses
import importlib
import os
import typing
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Win32 / GDI constants, named so expectations read instead of being magic.
# ---------------------------------------------------------------------------
_BITSPIXEL = 12  # wingdi.h
_SRCCOPY = 0x00CC0020
_BI_RGB = 0
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_WS_CLIPSIBLINGS = 0x04000000
_SWP_SHOWWINDOW = 0x0040
_HWND_TOP = 0
_PS_SOLID = 0
_PS_DASH = 1
_CLR_INVALID = 0xFFFFFFFF
_PREFILL = 0x5A
_ALPHA = 0xFF
_SIZE = (1280, 720)
_PARENT_HWND = 0xABCD
# COLORREF is 0x00BBGGRR (design section 6). Only white is pinned, because it is
# the one colour the design names; the stick and guide colours are the Tk names
# "cyan" and "red" and the design does not fix their COLORREF encoding.
_WHITE = 0x00FFFFFF
# Two arbitrary recognition colours, neither equal to a static pen colour, so
# the pen cache is provably growing because the colour changed.
_RECOGNITION = 0x00A0C0E0
_RECOGNITION_CHANGED = 0x0000FF00

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "SerialController" / "core"
_RENDERER_SOURCE = "preview_renderer.py"
_SURFACE_SOURCE = "gdi_surface.py"

# A second WINFUNCTYPE trampoline is the 0xC0000409 fatal shape a previous crash
# was traced to (design section 7), and ``_WNDPROC_TYPE`` at
# ``preview_clock.py:209`` is existing dead code that must not be revived.
_BACKTRACKING_NAMES = frozenset(
    {"WINFUNCTYPE", "CFUNCTYPE", "_WNDPROC_FACTORY", "_WNDPROC_TYPE"}
)
_TRAMPOLINE_FROM_CLOCK = frozenset(
    {"_WNDPROC_TYPE", "_WNDPROC_FACTORY", "_CtypesWin32Api", "preview_clock"}
)
# Mirrors tools/check_core.py BANNED_TOP + BANNED_APP for the renderer, plus the
# PIL and ui entry points the brief calls out by name.
_RENDERER_BANNED_IMPORTS = frozenset(
    {
        "tkinter",
        "pygubu",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        "GuiAssets",
        "Window",
        "ui",
        "Camera",
        "Settings",
        "Utility",
        "PokeConLogger",
    }
)
# Design section 6 creation order; asserted as a relative order, not a total one.
_REQUIRED_ATTACH_SEQUENCE = (
    "register_class_ex",
    "create_window_ex",
    "enable_window",
    "get_device_caps",
    "create_dib_section",
    "create_compatible_dc",
    "select_object",
    "set_window_pos",
)
_SELF_TEST_OUTCOMES = frozenset(
    {"not_run", "sentinel_matched", "wrong_color", "covered"}
)


# ---------------------------------------------------------------------------
# Recorded call shapes. Field names mirror what production passes, so a mismatch
# is a contract failure rather than a test artefact.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DibHeader:
    """Mirror of the ``BITMAPINFOHEADER`` handed to ``CreateDIBSection``."""

    bi_width: int
    bi_height: int  # NEGATIVE for the top-down DIB the design requires.
    bi_bit_count: int
    bi_compression: int  # BI_RGB == 0


@dataclass(frozen=True, slots=True)
class DibSection:
    """Mirror of what ``CreateDIBSection`` hands back."""

    hbitmap: int
    bits_address: int
    bi_width: int
    bi_height: int
    bi_bit_count: int


@dataclass(frozen=True, slots=True)
class DcRequest:
    hwnd: int


@dataclass(frozen=True, slots=True)
class PixelRequest:
    hdc: int
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class ShapeRequest:
    hdc: int
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True, slots=True)
class PenRequest:
    style: int
    width: int
    color: int


@dataclass(frozen=True, slots=True)
class BrushRequest:
    color: int


@dataclass(frozen=True, slots=True)
class BitBltRequest:
    dest_dc: int
    dest_x: int
    dest_y: int
    width: int
    height: int
    src_dc: int
    src_x: int
    src_y: int
    rop: int


@dataclass(frozen=True, slots=True)
class CreateWindowRecord:
    class_name: str
    instance: int
    parent: int
    ex_style: int
    x: int
    y: int
    width: int
    height: int
    style: int
    hwnd: int


@dataclass(frozen=True, slots=True)
class WindowPosRecord:
    hwnd: int
    insert_after: int
    x: int
    y: int
    width: int
    height: int
    flags: int


@dataclass(frozen=True, slots=True)
class _Dib:
    address: int
    size: int
    stride: int
    bit_count: int
    width: int
    height: int


class _DefWindowProcSentinel:
    """Stands in for ``user32.DefWindowProcW``.

    Deliberately a plain Python object: a ctypes trampoline carries ``_flags_``
    and is an instance of the callback type, so this sentinel keeps "the surface
    passed the api's native proc" and "the surface built a Python window
    procedure" distinguishable at runtime as well as in the source.
    """

    __slots__ = ()


_DEF_WINDOW_PROC = _DefWindowProcSentinel()


class RecordingGdiApi:
    """Deterministic Win32/GDI double that drives the whole preview surface.

    Handles, DCs, DIB memory and return values are all deterministic, so the
    exact Win32 call sequence and its release order can be asserted with no
    display, no window, no Tk interpreter and no GDI object anywhere.
    """

    def __init__(
        self,
        *,
        bits_pixel: int = 32,
        client_size: tuple[int, int] = _SIZE,
        prefill: int = _PREFILL,
        clip_box: tuple[int, int, int, int] = (0, 0, *_SIZE),
        bitblt_results: list[bool] | None = None,
        get_dc_results: list[int] | None = None,
        get_pixel_results: list[int] | None = None,
        register_class_results: list[int] | None = None,
        create_window_results: list[int] | None = None,
        compatible_dc_results: list[int] | None = None,
        null_present_dc: bool = False,
    ) -> None:
        self.bits_pixel = bits_pixel
        self.prefill = prefill
        self.bitblt_results = list(bitblt_results or [])
        self.get_dc_results = list(get_dc_results or [])
        self.get_pixel_results = list(get_pixel_results or [])
        self.register_class_results = list(register_class_results or [])
        self.create_window_results = list(create_window_results or [])
        self.compatible_dc_results = list(compatible_dc_results or [])
        self.null_present_dc = null_present_dc
        self.last_error = 0
        # Ordered logs: names for order assertions, name+args for interleaving.
        self.calls: list[str] = []
        self.log: list[tuple[str, tuple[Any, ...]]] = []
        self.dpi_aware_calls = 0
        self.register_class_calls: list[tuple[str, int, Any]] = []
        self.create_window_calls: list[CreateWindowRecord] = []
        self.enable_window_calls: list[tuple[int, bool]] = []
        self.get_dc_calls: list[DcRequest] = []
        self.release_dc_calls: list[tuple[int, int]] = []
        self.device_caps_calls: list[tuple[int, int]] = []
        self.dib_headers: list[DibHeader] = []
        self.dib_sections: list[DibSection] = []
        self.compatible_dc_calls: list[int] = []
        self.select_object_calls: list[tuple[int, int]] = []
        self.window_pos_calls: list[WindowPosRecord] = []
        self.pen_calls: list[PenRequest] = []
        self.pen_handles: list[int] = []
        self.brush_calls: list[BrushRequest] = []
        self.brush_handles: list[int] = []
        self.hollow_brush_handles: list[int] = []
        self.ellipse_calls: list[ShapeRequest] = []
        self.rectangle_calls: list[ShapeRequest] = []
        self.bit_blt_calls: list[BitBltRequest] = []
        self.pixel_calls: list[PixelRequest] = []
        self.clip_box_calls: list[int] = []
        self.client_rect_calls: list[int] = []
        self.desktop_window_calls = 0
        self.window_proc_calls = 0
        self.destroy_window_calls: list[int] = []
        self.delete_dc_calls: list[int] = []
        self.delete_object_calls: list[int] = []
        self._next_handle = 0x1000
        self._previously_selected = 0xDEAD_BEEF
        self._clip_box = clip_box
        self._client_rect = (0, 0, client_size[0], client_size[1])
        self._dc_by_hwnd: dict[int, int] = {}
        # Handles currently held from a GetDC. Win32 hands out an invalid DC
        # once it is released, which is what the memory-DC contract below
        # depends on, so validity is state rather than a value.
        self._held_dcs: set[int] = set()
        self._window_hwnds: list[int] = []
        self._memory_dc = 0
        self._blocks: list[Any] = []
        self._dib: _Dib | None = None
        self._self_test_done = False

    # -- production contract: the Win32 entry points GdiSurface may call -----
    def set_process_dpi_aware(self) -> bool:
        self._record("set_process_dpi_aware")
        self.dpi_aware_calls += 1
        return True

    def register_class_ex(
        self, class_name: str, instance: int, window_proc: Any
    ) -> int:
        self._record("register_class_ex", class_name, instance, window_proc)
        self.register_class_calls.append((class_name, instance, window_proc))
        if self.register_class_results:
            return self.register_class_results.pop(0)
        return 0x00C1

    def create_window_ex(
        self,
        class_name: str,
        instance: int,
        parent: int,
        ex_style: int,
        x: int,
        y: int,
        width: int,
        height: int,
        style: int,
    ) -> int:
        hwnd = self._new_handle()
        self._record(
            "create_window_ex",
            class_name,
            instance,
            parent,
            ex_style,
            x,
            y,
            width,
            height,
            style,
        )
        if self.create_window_results:
            hwnd = self.create_window_results.pop(0)
        self.create_window_calls.append(
            CreateWindowRecord(
                class_name=class_name,
                instance=instance,
                parent=parent,
                ex_style=ex_style,
                x=x,
                y=y,
                width=width,
                height=height,
                style=style,
                hwnd=hwnd,
            )
        )
        self._window_hwnds.append(hwnd)
        return hwnd

    def enable_window(self, hwnd: int, enabled: bool) -> bool:
        self._record("enable_window", hwnd, enabled)
        self.enable_window_calls.append((hwnd, enabled))
        return True

    def get_dc(self, hwnd: int) -> int:
        self._record("get_dc", hwnd)
        self.get_dc_calls.append(DcRequest(hwnd))
        if self.get_dc_results:
            return self.get_dc_results.pop(0)
        if self.null_present_dc and self._self_test_done and hwnd in self._window_hwnds:
            return 0
        hdc = self._dc_by_hwnd.get(hwnd)
        if hdc is None:
            hdc = self._new_handle()
            self._dc_by_hwnd[hwnd] = hdc
        self._held_dcs.add(hdc)
        return hdc

    def release_dc(self, hwnd: int, hdc: int) -> int:
        self._record("release_dc", hwnd, hdc)
        self.release_dc_calls.append((hwnd, hdc))
        self._held_dcs.discard(hdc)
        return 1

    def get_device_caps(self, hdc: int, index: int) -> int:
        self._record("get_device_caps", hdc, index)
        self.device_caps_calls.append((hdc, index))
        return self.bits_pixel

    def create_dib_section(self, info: Any) -> Any:
        self._record("create_dib_section", info)
        header = DibHeader(
            bi_width=int(info.bi_width),
            bi_height=int(info.bi_height),
            bi_bit_count=int(info.bi_bit_count),
            bi_compression=int(info.bi_compression),
        )
        self.dib_headers.append(header)
        channels = max(1, header.bi_bit_count // 8)
        stride = ((header.bi_width * channels + 3) // 4) * 4
        size = max(4, stride * abs(header.bi_height))
        block = ctypes.create_string_buffer(bytes([self.prefill]) * size)
        self._blocks.append(block)
        self._dib = _Dib(
            address=ctypes.addressof(block),
            size=size,
            stride=stride,
            bit_count=header.bi_bit_count,
            width=header.bi_width,
            height=abs(header.bi_height),
        )
        section = DibSection(
            hbitmap=self._new_handle(),
            bits_address=self._dib.address,
            bi_width=header.bi_width,
            bi_height=header.bi_height,
            bi_bit_count=header.bi_bit_count,
        )
        self.dib_sections.append(section)
        return section

    def create_compatible_dc(self, hdc: int) -> int:
        self._record("create_compatible_dc", hdc)
        self.compatible_dc_calls.append(hdc)
        if hdc not in self._held_dcs:
            # GetDC していない DC、または ReleaseDC 済みの DC は Win32 で
            # CreateCompatibleDC が NULL を返す（実測 last_error=6）。これを
            # モデル化しないと、解放後の DC を再使う欠陥が二重で緑になる。
            self.last_error = 6
            return 0
        if self.compatible_dc_results:
            return self.compatible_dc_results.pop(0)
        self._memory_dc = self._new_handle()
        return self._memory_dc

    def select_object(self, hdc: int, obj: int) -> int:
        self._record("select_object", hdc, obj)
        self.select_object_calls.append((hdc, obj))
        return self._previously_selected

    def set_window_pos(
        self,
        hwnd: int,
        insert_after: int,
        x: int,
        y: int,
        width: int,
        height: int,
        flags: int,
    ) -> bool:
        self._record("set_window_pos", hwnd, insert_after, x, y, width, height, flags)
        self.window_pos_calls.append(
            WindowPosRecord(
                hwnd=hwnd,
                insert_after=insert_after,
                x=x,
                y=y,
                width=width,
                height=height,
                flags=flags,
            )
        )
        return True

    def create_pen(self, style: int, width: int, color: int) -> int:
        self._record("create_pen", style, width, color)
        self.pen_calls.append(PenRequest(style, width, color))
        handle = self._new_handle()
        self.pen_handles.append(handle)
        return handle

    def create_hollow_brush(self) -> int:
        self._record("create_hollow_brush")
        handle = self._new_handle()
        self.hollow_brush_handles.append(handle)
        return handle

    def create_solid_brush(self, color: int) -> int:
        self._record("create_solid_brush", color)
        self.brush_calls.append(BrushRequest(color))
        handle = self._new_handle()
        self.brush_handles.append(handle)
        return handle

    def ellipse(self, hdc: int, left: int, top: int, right: int, bottom: int) -> bool:
        self._record("ellipse", hdc, left, top, right, bottom)
        self.ellipse_calls.append(ShapeRequest(hdc, left, top, right, bottom))
        return True

    def rectangle(self, hdc: int, left: int, top: int, right: int, bottom: int) -> bool:
        self._record("rectangle", hdc, left, top, right, bottom)
        self.rectangle_calls.append(ShapeRequest(hdc, left, top, right, bottom))
        return True

    def bit_blt(
        self,
        dest_dc: int,
        dest_x: int,
        dest_y: int,
        width: int,
        height: int,
        src_dc: int,
        src_x: int,
        src_y: int,
        rop: int,
    ) -> bool:
        self._record(
            "bit_blt",
            dest_dc,
            dest_x,
            dest_y,
            width,
            height,
            src_dc,
            src_x,
            src_y,
            rop,
        )
        self.bit_blt_calls.append(
            BitBltRequest(
                dest_dc=dest_dc,
                dest_x=dest_x,
                dest_y=dest_y,
                width=width,
                height=height,
                src_dc=src_dc,
                src_x=src_x,
                src_y=src_y,
                rop=rop,
            )
        )
        if self.bitblt_results:
            return self.bitblt_results.pop(0)
        return True

    def get_pixel(self, hdc: int, x: int, y: int) -> int:
        self._record("get_pixel", hdc, x, y)
        self.pixel_calls.append(PixelRequest(hdc, x, y))
        self._self_test_done = True
        if self.get_pixel_results:
            return self.get_pixel_results.pop(0)
        dib = self._dib
        if dib is None or dib.bit_count != 32 or not self._blit_covered(hdc, x, y):
            return 0
        blue, green, red, _alpha = ctypes.string_at(
            dib.address + y * dib.stride + x * 4, 4
        )
        return (red << 16) | (green << 8) | blue

    def get_clip_box(self, hdc: int) -> tuple[int, int, int, int]:
        self._record("get_clip_box", hdc)
        self.clip_box_calls.append(hdc)
        return self._clip_box

    def get_client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        self._record("get_client_rect", hwnd)
        self.client_rect_calls.append(hwnd)
        left, top, right, bottom = self._client_rect
        return (left, top, right, bottom)

    def get_desktop_window(self) -> int:
        self._record("get_desktop_window")
        self.desktop_window_calls += 1
        return 0xFFFFFFFF

    def native_window_proc(self) -> Any:
        self.window_proc_calls += 1
        return _DEF_WINDOW_PROC

    def destroy_window(self, hwnd: int) -> bool:
        self._record("destroy_window", hwnd)
        self.destroy_window_calls.append(hwnd)
        return True

    def delete_dc(self, hdc: int) -> bool:
        self._record("delete_dc", hdc)
        self.delete_dc_calls.append(hdc)
        return True

    def delete_object(self, obj: int) -> bool:
        self._record("delete_object", obj)
        self.delete_object_calls.append(obj)
        return True

    def get_last_error(self) -> int:
        return self.last_error

    # -- test-only inspection and fault injection; not a production contract
    @property
    def child_hwnd(self) -> int:
        assert self._window_hwnds, "no child window has been created yet"
        return self._window_hwnds[-1]

    @property
    def child_dc(self) -> int:
        return self._dc_by_hwnd[self.child_hwnd]

    @property
    def memory_dc(self) -> int:
        return self._memory_dc

    @property
    def bitmap_handle(self) -> int:
        return self.dib_sections[0].hbitmap

    def brush_count(self) -> int:
        return len(self.brush_handles) + len(self.hollow_brush_handles)

    def all_brush_handles(self) -> tuple[int, ...]:
        return (*self.brush_handles, *self.hollow_brush_handles)

    def created_object_handles(self) -> tuple[int, ...]:
        """Every GDI object handle handed out, in creation order.

        DIB section の HBITMAP も含める。設計 §6 は DIB をメモリ DC に
        SelectObject することを要求し、test_attach_issues_the_creation_
        sequence_in_order がその呼び出しを固定する。HBITMAP を漏らすと
        「選択される GDI オブジェクトは全て生成済み」という検査と
        互いに矛盾し、どの実装も両方を満たせない。
        """
        return (
            *(section.hbitmap for section in self.dib_sections),
            *self.pen_handles,
            *self.brush_handles,
            *self.hollow_brush_handles,
        )

    def set_client_rect(self, size: tuple[int, int]) -> None:
        self._client_rect = (0, 0, size[0], size[1])

    def fill_dib(self, value: int) -> None:
        dib = self._require_dib()
        payload = bytes([value]) * dib.size
        ctypes.memmove(dib.address, payload, len(payload))

    def dib_bytes(self) -> bytes:
        dib = self._require_dib()
        return ctypes.string_at(dib.address, dib.size)

    def dib_bgra(self) -> Any:
        """A 32bpp ``(h, w, 4)`` copy of the DIB memory, read independently."""
        dib = self._require_dib()
        assert dib.bit_count == 32, "dib_bgra is only meaningful for a 32bpp DIB"
        rows = np.frombuffer(self.dib_bytes(), dtype=np.uint8).reshape(
            dib.height, dib.stride
        )
        return rows[:, : dib.width * 4].reshape(dib.height, dib.width, 4).copy()

    def dib_is_uniform(self, value: int) -> bool:
        raw = self.dib_bytes()
        return raw == bytes([value]) * len(raw)

    def presented_blits(self) -> list[BitBltRequest]:
        return [blit for blit in self.bit_blt_calls if blit.src_dc == self.memory_dc]

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append(name)
        self.log.append((name, args))

    def _require_dib(self) -> _Dib:
        if self._dib is None:
            raise AssertionError("no DIB section has been created yet")
        return self._dib

    def _blit_covered(self, hdc: int, x: int, y: int) -> bool:
        if not self.bit_blt_calls:
            return False
        blit = self.bit_blt_calls[-1]
        if blit.dest_dc != hdc:
            return False
        return (
            blit.dest_x <= x < blit.dest_x + blit.width
            and blit.dest_y <= y < blit.dest_y + blit.height
        )

    def _new_handle(self) -> int:
        handle = self._next_handle
        self._next_handle += 1
        return handle


# ---------------------------------------------------------------------------
# Lazy module access. The modules do not exist yet, so each test reaches them
# through these helpers and fails on its own.
# ---------------------------------------------------------------------------
def _renderer_module() -> Any:
    """Import ``core/preview_renderer`` at call time so absence is a per-test RED."""
    import core.preview_renderer as module

    return module


def _surface_module() -> Any:
    """Import ``core/gdi_surface`` at call time so absence is a per-test RED."""
    import core.gdi_surface as module

    return module


def _fresh_surface_module() -> Any:
    """Import ``core/gdi_surface`` with a pristine process-wide class guard.

    Reloading resets the module globals, so the once-per-process registration
    assertion starts from a surface that has not registered yet.
    """
    return importlib.reload(importlib.import_module("core.gdi_surface"))


# ---------------------------------------------------------------------------
# Source inspection. These read the production file instead of importing it, so
# a missing module is reported as a missing file with a readable message.
# ---------------------------------------------------------------------------
def _source_tree(name: str) -> ast.Module:
    path = _CORE / name
    assert path.is_file(), f"production module is missing: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(alias.name for alias in node.names)
            if module:
                names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def _referenced_names(tree: ast.Module) -> set[str]:
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
    return referenced


def _structure_fields(tree: ast.Module) -> list[tuple[str, str]]:
    """``[(field_name, type_source)]`` for every ctypes ``_fields_`` list."""
    fields: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = tuple(node.targets)
        if not any(
            isinstance(target, ast.Name) and target.id == "_fields_"
            for target in targets
        ):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for element in node.value.elts:
            if isinstance(element, (ast.Tuple, ast.List)) and len(element.elts) == 2:
                name_node, type_node = element.elts
                if isinstance(name_node, ast.Constant):
                    fields.append(
                        (str(name_node.value), ast.unparse(type_node).replace(" ", ""))
                    )
    return fields


def _attribute_assignments(tree: ast.Module, attribute: str) -> list[ast.AST]:
    """Every value assigned to a ``.<attribute>`` target anywhere in the module."""
    values: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
            value: ast.AST | None = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
            value = node.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == attribute:
                values.append(value)
    return values


def _is_null_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is None or node.value == 0
    return isinstance(node, ast.Name) and node.id == "NULL"


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------
def _frame(height: int, width: int) -> Any:
    """A deterministic C-contiguous BGR frame, distinct per pixel."""
    generator = np.random.default_rng(20260926)
    return generator.integers(0, 256, (height, width, 3), dtype=np.uint8)


def _attached(api: RecordingGdiApi, module: Any, size: tuple[int, int] = _SIZE) -> Any:
    surface = module.GdiSurface(api=api)
    surface.attach(_PARENT_HWND, size)
    return surface


def _empty_overlay() -> Any:
    return _renderer_module().OverlayState()


def _full_overlay(module: Any, color: int = _RECOGNITION) -> Any:
    """The design's seven-shape overlay: both sticks, the guide and the ImgRect."""
    return module.OverlayState(
        left_stick=module.StickState(
            active=True, center_x=640, center_y=360, radius=60, knob_x=660, knob_y=350
        ),
        right_stick=module.StickState(
            active=True, center_x=200, center_y=120, radius=30, knob_x=215, knob_y=108
        ),
        guide=module.RectState(x0=100, y0=200, x1=300, y1=400, visible=True),
        img_rect=module.ImgRectState(
            outer=module.RectState(x0=10, y0=20, x1=110, y1=220),
            inner=module.RectState(x0=20, y0=30, x1=100, y1=200),
            visible=True,
            color=color,
        ),
    )


def _required_in_order(calls: Sequence[str], required: Sequence[str]) -> None:
    """Assert every required call happened and that their order is the required one."""
    positions: list[int] = []
    for name in required:
        assert name in calls, f"{name} was never issued; issued calls were {calls}"
        positions.append(list(calls).index(name))
    assert positions == sorted(positions), (
        f"required calls were issued out of order: {dict(zip(required, positions))}"
    )


def _selections_before_shape(
    api: RecordingGdiApi, shape: str, index: int
) -> tuple[int, int]:
    """The ``(pen, brush)`` handles selected into the memory DC before a shape.

    Walks the argument-level log rather than the per-method lists, so a pen that
    is re-selected every frame and a cached pen that is not are both visible.
    """
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


def _last_shape_selections(api: RecordingGdiApi, shape: str) -> tuple[int, int]:
    """The ``(pen, brush)`` selected before the most recent ``shape`` call.

    Call counts accumulate across composes, so the cache tests must anchor on
    the last occurrence rather than on a running index.
    """
    selections = _all_shape_selections(api, shape)
    assert selections, f"no {shape} call was recorded"
    return selections[-1]


def _all_shape_selections(api: RecordingGdiApi, shape: str) -> list[tuple[int, int]]:
    pens = set(api.pen_handles)
    brushes = set(api.brush_handles) | set(api.hollow_brush_handles)
    pen = brush = 0
    found: list[tuple[int, int]] = []
    for name, args in api.log:
        if name == "select_object" and args[0] == api.memory_dc:
            if args[1] in pens:
                pen = args[1]
            elif args[1] in brushes:
                brush = args[1]
        elif name == shape:
            found.append((pen, brush))
    return found


def _shape_sequence(api: RecordingGdiApi) -> list[tuple[str, int, int, int, int]]:
    """Every ``Ellipse`` / ``Rectangle`` in the order the renderer issued it."""
    shapes: list[tuple[str, int, int, int, int]] = []
    for name, args in api.log:
        if name in {"ellipse", "rectangle"}:
            shapes.append((name, args[1], args[2], args[3], args[4]))
    return shapes


def _assert_frozen_slotted(cls: Any, field_names: tuple[str, ...]) -> None:
    """A frozen ``slots=True`` dataclass with exactly ``field_names`` in order."""
    assert cls.__dataclass_params__.frozen is True, f"{cls.__name__} must be frozen"
    assert hasattr(cls, "__slots__"), f"{cls.__name__} must use slots"
    assert "__dict__" not in vars(cls), f"{cls.__name__} must not carry a __dict__"
    assert tuple(field.name for field in dataclasses.fields(cls)) == field_names


# ===========================================================================
# A. core/preview_renderer.py -- the protocol and the overlay state
# ===========================================================================


def test_render_result_is_frozen_slotted_with_documented_defaults() -> None:
    # Given: the renderer module, whose RenderResult carries a default detail.
    render_result = _renderer_module().RenderResult

    # Then: frozen, slotted, and the field order is ok / elapsed_ns / detail.
    _assert_frozen_slotted(render_result, ("ok", "elapsed_ns", "detail"))
    result = render_result(ok=True, elapsed_ns=1234)
    assert result.detail == ""
    assert result == render_result(True, 1234, "")
    assert dataclasses.fields(render_result)[2].default == ""

    # When: an attribute is set.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.ok = False

    # Then: the mutation is refused and elapsed_ns has no default to omit.
    with pytest.raises(TypeError):
        render_result(ok=True)


def test_overlay_state_types_are_frozen_slotted_with_documented_defaults() -> None:
    # Given: the four overlay value types from design section 5.
    module = _renderer_module()
    stick, rect = module.StickState, module.RectState
    img_rect, overlay = module.ImgRectState, module.OverlayState

    # Then: each is frozen, slotted, and carries the documented field order.
    # StickState is absolute, not an offset model, so the ring can stay put while
    # the knob moves and snaps (design section 5, GuiAssets.py:982 and :997).
    _assert_frozen_slotted(
        stick, ("active", "center_x", "center_y", "radius", "knob_x", "knob_y")
    )
    _assert_frozen_slotted(rect, ("x0", "y0", "x1", "y1", "visible"))
    _assert_frozen_slotted(img_rect, ("outer", "inner", "visible", "color"))
    _assert_frozen_slotted(overlay, ("left_stick", "right_stick", "guide", "img_rect"))

    # Then: every field default is the design's zero / inactive value.
    assert stick() == module.StickState(False, 0, 0, 0, 0, 0)
    assert rect() == module.RectState(0, 0, 0, 0, False)
    assert img_rect() == module.ImgRectState(rect(), rect(), False, 0)

    # Then: OverlayState is constructible with no arguments at all, and
    # ImgRectState nests two independent RectStates rather than one rectangle.
    empty = overlay()
    assert empty.left_stick == stick()
    assert empty.right_stick == stick()
    assert empty.guide == rect()
    assert empty.img_rect == img_rect()
    assert empty.img_rect.outer == rect()
    assert empty.img_rect.inner == rect()

    # When/Then: every component refuses attribute assignment.
    components = (
        empty.left_stick,
        empty.right_stick,
        empty.guide,
        empty.img_rect,
        empty.img_rect.outer,
        empty.img_rect.inner,
    )
    for component in components:
        first_field = dataclasses.fields(component)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(component, first_field, 1)


def test_overlay_state_replacement_yields_a_new_value_and_leaves_the_original() -> None:
    # Given: an all-default overlay captured on the main thread.
    module = _renderer_module()
    original = _empty_overlay()

    # When: one component is replaced through dataclasses.replace.
    updated = dataclasses.replace(
        original,
        left_stick=module.StickState(
            active=True, center_x=640, center_y=360, radius=60
        ),
    )

    # Then: a new overlay carries the change and the original is untouched, so
    # a render can never observe a half-updated overlay.
    assert updated is not original
    assert updated.left_stick.active is True
    assert updated.left_stick.center_x == 640
    assert original.left_stick == module.StickState()
    assert original.left_stick.active is False

    # When: each of the frozen types is replaced in turn, including the nesting.
    replaced_stick = dataclasses.replace(module.StickState(), knob_x=660)
    replaced_rect = dataclasses.replace(module.RectState(), visible=True)
    replaced_img = dataclasses.replace(
        module.ImgRectState(), inner=module.RectState(x0=20), color=0x00A0C0E0
    )
    replaced_result = dataclasses.replace(
        module.RenderResult(ok=True, elapsed_ns=1), ok=False
    )

    # Then: replace works on all of them and the originals stay as they were.
    assert replaced_stick.knob_x == 660
    assert replaced_rect.visible is True
    assert replaced_img.inner.x0 == 20
    assert replaced_img.color == 0x00A0C0E0
    assert replaced_result.ok is False


def test_preview_renderer_protocol_is_runtime_checkable_over_its_method_set() -> None:
    # Given: a conforming fake and a deliberately incomplete one.
    protocol = _renderer_module().PreviewRenderer

    class Conforming:
        def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None:
            return None

        def resize(self, size: tuple[int, int]) -> None:
            return None

        def compose(self, frame: Any, overlay: Any) -> Any:
            return None

        def present(self) -> Any:
            return None

        def release(self) -> None:
            return None

        def client_size(self) -> tuple[int, int]:
            return _SIZE

    class Incomplete:
        def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None:
            return None

        def resize(self, size: tuple[int, int]) -> None:
            return None

        def compose(self, frame: Any, overlay: Any) -> Any:
            return None

        def present(self) -> Any:
            return None

    # Then: the protocol is runtime checkable and pins exactly the six methods.
    assert getattr(protocol, "_is_runtime_protocol", False) is True
    assert isinstance(Conforming(), protocol) is True
    assert isinstance(Incomplete(), protocol) is False


def test_preview_renderer_module_imports_no_tkinter_and_no_app_layer() -> None:
    # Given: the renderer source, read rather than imported so that absence is a
    # missing-file RED with a readable message.
    imported = _imported_names(_source_tree(_RENDERER_SOURCE))

    # Then: no GUI toolkit, no image library, no app-layer or ui import, so the
    # module keeps core/ inside the task bounds gate.
    assert not (imported & _RENDERER_BANNED_IMPORTS)
    assert not {name for name in imported if name.startswith("ui.")}


def test_overlay_coordinates_are_display_pixel_ints_left_to_the_caller() -> None:
    # Given: the overlay coordinate fields, which are display pixels in ints.
    module = _renderer_module()
    hints = typing.get_type_hints(module.StickState)
    hints |= typing.get_type_hints(module.RectState)
    hints |= typing.get_type_hints(module.ImgRectState)

    # Then: the field names are the documented contract across all three types.
    assert set(hints) == {
        "active",
        "center_x",
        "center_y",
        "radius",
        "knob_x",
        "knob_y",
        "x0",
        "y0",
        "x1",
        "y1",
        "visible",
        "outer",
        "inner",
        "color",
    }

    # Then: every coordinate and colour field is annotated int, every flag bool.
    coordinates = (
        "center_x",
        "center_y",
        "radius",
        "knob_x",
        "knob_y",
        "x0",
        "y0",
        "x1",
        "y1",
        "color",
    )
    for name in coordinates:
        assert hints[name] is int, f"{name} must be annotated int"
    for name in ("active", "visible"):
        assert hints[name] is bool
    assert hints["outer"] is not int
    assert hints["inner"] is not int

    # When: a float is assigned to an int-annotated field.
    stick = module.StickState(center_x=640.5, center_y=360.25)

    # Then: the value round-trips as given. The dataclass does not validate, so
    # this test documents that the CALLER owns the display-pixel contract
    # instead of pretending the annotation is enforced.
    assert stick.center_x == 640.5
    assert stick.center_y == 360.25
    assert module.RectState(x0=1.5).x0 == 1.5
    assert module.ImgRectState(color=0.5).color == 0.5


# ===========================================================================
# B. core/gdi_surface.py -- the surface against a recording double
# ===========================================================================


def test_attach_issues_the_creation_sequence_in_order() -> None:
    # Given: a fresh double and a surface module with an unset class guard.
    module = _fresh_surface_module()
    api = RecordingGdiApi()

    # When: a 1280x720 child is attached to a foreign parent HWND.
    module.GdiSurface(api=api).attach(_PARENT_HWND, _SIZE)

    # Then: design section 6's creation order holds, relative to each other.
    _required_in_order(api.calls, _REQUIRED_ATTACH_SEQUENCE)

    # Then: the child is a plain WS_CHILD window with no layered ex-style.
    created = api.create_window_calls[0]
    assert created.style == _WS_CHILD | _WS_VISIBLE | _WS_CLIPSIBLINGS
    assert created.ex_style == 0
    assert (created.parent, created.x, created.y) == (_PARENT_HWND, 0, 0)
    assert (created.width, created.height) == _SIZE

    # Then: the child is disabled so the Tk bind() stick control keeps the mouse.
    assert api.enable_window_calls == [(api.child_hwnd, False)]

    # Then: the DIB is top-down 32bpp BI_RGB, matching the destination.
    header = api.dib_headers[0]
    assert (header.bi_width, header.bi_height) == (1280, -720)
    assert header.bi_bit_count == 32
    assert header.bi_compression == _BI_RGB

    # Then: the bit depth is queried on the child's WINDOW dc, never the memory
    # dc, because GetDeviceCaps on a memory DC returns 0 on this machine.
    caps_hdc, caps_index = api.device_caps_calls[0]
    assert caps_index == _BITSPIXEL
    assert caps_hdc == api.child_dc
    assert caps_hdc != api.memory_dc
    assert DcRequest(api.child_hwnd) in api.get_dc_calls
    assert (api.child_hwnd, api.child_dc) in api.release_dc_calls

    # Then: the DIB is selected into a compatible DC and the child is placed.
    assert api.compatible_dc_calls == [api.child_dc]
    assert (api.memory_dc, api.bitmap_handle) in api.select_object_calls
    placed = api.window_pos_calls[-1]
    assert (placed.x, placed.y, placed.width, placed.height) == (0, 0, 1280, 720)
    assert placed.insert_after == _HWND_TOP
    assert placed.flags & _SWP_SHOWWINDOW == _SWP_SHOWWINDOW


def test_window_class_is_registered_once_per_process() -> None:
    # Given: one double shared by two surfaces, so a per-process guard is visible.
    module = _fresh_surface_module()
    api = RecordingGdiApi()

    # When: a first surface attaches and registers the class.
    first = module.GdiSurface(api=api)
    first.attach(_PARENT_HWND, _SIZE)
    assert len(api.register_class_calls) == 1

    # When: a second, independent surface attaches in the same process.
    before = len(api.register_class_calls)
    module.GdiSurface(api=api).attach(_PARENT_HWND, _SIZE)

    # Then: the class is not registered again, so camera reopen cannot fail.
    assert len(api.register_class_calls) == before
    assert api.create_window_calls[-1].hwnd != api.create_window_calls[0].hwnd


@pytest.mark.parametrize(
    ("reported", "allowed"),
    [(24, {24}), (32, {32}), (48, {16, 24, 32}), (0, {16, 24, 32})],
)
def test_back_buffer_bit_depth_is_queried_from_the_child_window_dc(
    reported: int, allowed: set[int]
) -> None:
    # Given: a double reporting one BITSPIXEL value for the child window DC.
    module = _fresh_surface_module()
    api = RecordingGdiApi(bits_pixel=reported)

    # When: the surface attaches.
    surface = module.GdiSurface(api=api)
    surface.attach(_PARENT_HWND, _SIZE)

    # Then: the DIB follows the queried depth, clamped into the supported set.
    assert api.dib_headers[0].bi_bit_count in allowed
    assert api.device_caps_calls[0][0] == api.child_dc
    assert surface.client_size() == _SIZE


def test_back_buffer_is_a_four_channel_view_created_once_at_attach() -> None:
    # Given: an attached 32bpp surface.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    view = surface.back_buffer

    # Then: the view is (h, w, 4) over the DIB base pointer, created once.
    assert view.shape == (720, 1280, 4)
    assert view.dtype == np.uint8
    assert len(api.dib_headers) == 1
    assert len(api.dib_sections) == 1
    assert api.dib_sections[0].bits_address != 0

    # When: two more composes run.
    for _ in range(2):
        assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True
        assert surface.back_buffer is view, "the back buffer must be reused"

    # Then: still one DIB and one base pointer -- the design allows no per-frame
    # allocation, and cvtColor(dst=) is what guarantees it.
    assert len(api.dib_headers) == 1
    assert len(api.dib_sections) == 1


def test_compose_fills_through_cvtcolor_setting_the_alpha_lane() -> None:
    # Given: a 32bpp surface whose whole DIB is prefilled 0x5A, the value
    # measured to survive a strided numpy write, and one deterministic frame.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    api.fill_dib(_PREFILL)
    frame = _frame(720, 1280)

    # When: the frame is composited.
    result = surface.compose(frame, _empty_overlay())

    # Then: the first three channels equal the frame elementwise.
    assert result.ok is True, result.detail
    assert np.array_equal(api.dib_bgra()[:, :, :3], frame)

    # Then: the fourth channel is 0xFF everywhere. This is the observable proof
    # of which path ran: back[:, :, :3] = frame would leave byte 3 at 0x5A.
    alpha = api.dib_bgra()[:, :, 3]
    assert np.all(alpha == _ALPHA)
    assert not np.any(alpha == _PREFILL)


def test_compose_source_calls_cvtcolor_with_a_dst_keyword() -> None:
    # Given: the surface source, read rather than imported.
    tree = _source_tree(_SURFACE_SOURCE)

    # Then: the fill is cvtColor into the DIB base pointer, in BGR to BGRA.
    cvt_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cvtColor"
    ]
    assert cvt_calls, "compose must fill through cv2.cvtColor"
    assert any(
        keyword.arg == "dst" for call in cvt_calls for keyword in call.keywords
    ), "cvtColor must be given dst=, not the allocating form"
    assert "COLOR_BGR2BGRA" in _referenced_names(tree)


def test_compose_rejects_a_non_contiguous_frame_before_present() -> None:
    # Given: a 1280x720 view that is NOT C-contiguous although its leading stride
    # still exceeds w*3 -- the exact case design section 3 says proves nothing.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    strided = np.zeros((720, 1280 * 2, 3), dtype=np.uint8)[:, ::2, :]
    assert strided.shape == (720, 1280, 3)
    assert strided.flags.c_contiguous is False
    assert strided.strides[0] >= 1280 * 3
    blits_before = len(api.bit_blt_calls)

    # When: the non-contiguous frame is composited.
    result = surface.compose(strided, _empty_overlay())

    # Then: it is rejected with a detail naming the reason, never copied or scaled.
    assert result.ok is False
    assert result.detail == "frame_not_contiguous"
    assert len(api.bit_blt_calls) == blits_before

    # And: present was not reached, so the failure armed no pending frame.
    present = surface.present()
    assert present.ok is False
    assert present.detail == "no_frame"


def test_matching_frame_composes_and_presents_successfully() -> None:
    # Given: a 1280x720 surface and a 1280x720 frame.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    frame = _frame(720, 1280)
    blits_before = len(api.presented_blits())

    # When: the frame is composited and then presented.
    composed = surface.compose(frame, _empty_overlay())
    presented = surface.present()

    # Then: both succeed, the buffer holds the frame, and one blit happened.
    assert (composed.ok, presented.ok) == (True, True)
    assert (composed.detail, presented.detail) == ("ok", "ok")
    assert len(api.presented_blits()) == blits_before + 1
    assert np.array_equal(api.dib_bgra()[:, :, :3], frame)


def test_mismatched_frame_is_discarded_without_touching_the_buffer() -> None:
    # Given: a 1280x720 surface already holding one composited frame.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True
    before = api.dib_bytes()
    blits_before = len(api.bit_blt_calls)

    # When: a 640x360 frame arrives; the design forbids scaling it.
    result = surface.compose(_frame(360, 640), _empty_overlay())

    # Then: it is discarded and counted, the buffer is untouched, nothing blitted.
    assert result.ok is False
    assert result.detail == "dimension_mismatch"
    assert api.dib_bytes() == before
    assert len(api.bit_blt_calls) == blits_before


def test_odd_width_frame_is_discarded_rather_than_crashing() -> None:
    # Given: a 1280x720 surface.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    before = api.dib_bytes()

    # When: a 1281-wide frame arrives.
    result = surface.compose(_frame(720, 1281), _empty_overlay())

    # Then: the 1:1 check rejects it on the same reason, without an exception.
    assert result.ok is False
    assert result.detail == "dimension_mismatch"
    assert api.dib_bytes() == before
    assert surface.client_size() == _SIZE


def test_frame_is_accepted_after_a_stretched_resize_keeps_preview_alive() -> None:
    # Given: an attached 1280x720 surface and the 1280x720 frame the capture
    # board keeps producing, whatever the preview window is doing.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    blits_before = len(api.presented_blits())

    # When: the frame is delivered to the untouched attach-size surface.
    control = surface.compose(_frame(720, 1280), _empty_overlay())
    control_shown = surface.present()
    control_blits = len(api.presented_blits()) - blits_before

    # Then: it shows, so the resize below is the only variable left.
    assert (control.ok, control_shown.ok) == (True, True)
    assert control.detail == "ok"
    assert control_blits == 1

    # When: the parent stretches the child to 1600x900 -- aspect preserving, the
    # shape a user gets by making the window bigger -- and reports it.
    stretched = (1600, 900)
    api.set_client_rect(stretched)
    surface.resize(stretched)
    assert surface.client_size() == stretched
    blits_after = len(api.presented_blits())

    # When: the very same 1280x720 frame arrives again, unchanged.
    composed = surface.compose(_frame(720, 1280), _empty_overlay())
    presented = surface.present()
    new_blits = len(api.presented_blits()) - blits_after

    # Then: it is accepted and shown, so stretching the window cannot be what
    # blanks the preview -- the DIB has to follow the client box rather than
    # staying pinned to the size it was attached with.
    assert composed.ok is True, (
        f"stretched resize discarded a live frame: detail={composed.detail!r} "
        f"new_blits={new_blits}"
    )
    assert composed.detail == "ok"
    assert presented.ok is True
    assert new_blits == 1
    blits = api.presented_blits()
    assert (blits[-1].width, blits[-1].height) == stretched


def test_frame_is_accepted_after_an_aspect_distorting_resize() -> None:
    # Given: the same surface whose client box was distorted to 1274x718, the
    # two-pixels-smaller box a window manager border leaves behind. 16:9 is not
    # preserved and no whole-pixel scale factor exists, so neither is a rounding
    # detail that may be waved away.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    control = surface.compose(_frame(720, 1280), _empty_overlay())
    assert (control.ok, surface.present().ok) == (True, True)
    distorted = (1274, 718)
    api.set_client_rect(distorted)
    surface.resize(distorted)
    assert surface.client_size() == distorted
    blits_after = len(api.presented_blits())

    # When: the unchanged 1280x720 source frame is composited into that box.
    composed = surface.compose(_frame(720, 1280), _empty_overlay())
    presented = surface.present()
    new_blits = len(api.presented_blits()) - blits_after

    # Then: it composes instead of reporting the size invariant, and presents
    # across the whole new client area.
    assert composed.ok is True, (
        f"distorting resize discarded a live frame: detail={composed.detail!r} "
        f"new_blits={new_blits}"
    )
    assert composed.detail == "ok"
    assert presented.ok is True
    assert new_blits == 1
    blits = api.presented_blits()
    assert (blits[-1].width, blits[-1].height) == distorted


def test_compose_issues_no_window_dc_and_no_blit() -> None:
    # Given: an attached surface with the attach-time calls already recorded.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    dcs_before = len(api.get_dc_calls)
    blits_before = len(api.bit_blt_calls)

    # When: three composes run with no present in between.
    for _ in range(3):
        assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True

    # Then: compose never takes the window DC and never blits.
    assert len(api.get_dc_calls) == dcs_before
    assert len(api.bit_blt_calls) == blits_before


def test_present_issues_one_srccopy_blit_of_the_full_client_area() -> None:
    # Given: an attached surface with one frame composed after the self test.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    attach_blits = len(api.presented_blits())
    assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True

    # When: the frame is presented.
    result = surface.present()

    # Then: exactly one further SRCCOPY blit, memory DC to the child window DC,
    # with the full source and destination rects equal to the client size.
    assert result.ok is True
    blits = api.presented_blits()
    assert len(blits) == attach_blits + 1
    blit = blits[-1]
    assert blit.dest_dc == api.child_dc
    assert blit.src_dc == api.memory_dc
    assert (blit.dest_x, blit.dest_y, blit.width, blit.height) == (0, 0, 1280, 720)
    assert (blit.src_x, blit.src_y) == (0, 0)
    assert blit.rop == _SRCCOPY


def test_compose_and_present_each_report_a_non_negative_elapsed_ns() -> None:
    # Given: an attached surface.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)

    # When: a frame is composited and presented.
    composed = surface.compose(_frame(720, 1280), _empty_overlay())
    presented = surface.present()

    # Then: each is timed independently as a non-negative int nanosecond count.
    for result in (composed, presented):
        assert isinstance(result.elapsed_ns, int)
        assert result.elapsed_ns >= 0

    # When: a frame is rejected.
    rejected = surface.compose(_frame(360, 640), _empty_overlay())

    # Then: a rejected result is still reported and still timed, never raised.
    assert rejected.ok is False
    assert isinstance(rejected.elapsed_ns, int)
    assert rejected.elapsed_ns >= 0


def test_present_without_an_intervening_compose_is_rejected_as_no_frame() -> None:
    # Given: an attached surface whose composed frame was already shown.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True
    assert surface.present().ok is True
    blits_before = len(api.bit_blt_calls)

    # When: present is called again with no intervening compose.
    result = surface.present()

    # Then: it is rejected with the design's no_frame detail and blits nothing.
    # Rejection is the safer of the two admissible behaviours: a never-composed
    # or already-shown buffer must not be presented as if it were fresh.
    assert result.ok is False
    assert result.detail == "no_frame"
    assert len(api.bit_blt_calls) == blits_before


def test_failed_blit_is_reported_and_never_raises() -> None:
    # Given: a double whose present blit reports failure after a healthy self test.
    module = _fresh_surface_module()
    api = RecordingGdiApi(bitblt_results=[True, False])
    surface = _attached(api, module)
    assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True

    # When: present runs and the blit fails.
    result = surface.present()

    # Then: the failure is reported, not raised, so the Tk mainloop survives.
    assert result.ok is False
    assert result.detail == "bitblt_failed"
    assert isinstance(result.elapsed_ns, int)


def test_null_child_dc_at_present_is_reported_and_never_raises() -> None:
    # Given: a double whose GetDC returns NULL once the self test has finished.
    module = _fresh_surface_module()
    api = RecordingGdiApi(null_present_dc=True)
    surface = _attached(api, module)
    assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True

    # When: present runs with no usable child DC.
    result = surface.present()

    # Then: the unusable DC is reported as a failed result, never raised.
    assert result.ok is False
    assert result.detail == "getdc_failed"
    assert isinstance(result.elapsed_ns, int)


def test_null_child_dc_at_attach_never_raises() -> None:
    # Given: a double whose very first GetDC, the caps query, returns NULL.
    module = _fresh_surface_module()
    api = RecordingGdiApi(get_dc_results=[0])

    # When/Then: attach does not crash, and the surface refuses to pretend.
    surface = module.GdiSurface(api=api)
    surface.attach(_PARENT_HWND, _SIZE)
    composed = surface.compose(_frame(720, 1280), _empty_overlay())
    assert composed.ok is False
    assert isinstance(composed.elapsed_ns, int)


def test_attach_ends_with_a_memory_dc_the_surface_can_paint_through() -> None:
    # Given: a fresh double and the surface module with an unset class guard.
    module = _fresh_surface_module()
    api = RecordingGdiApi()

    # When: a 1280x720 child is attached.
    surface = module.GdiSurface(api=api)
    surface.attach(_PARENT_HWND, _SIZE)

    # Then: a memory DC exists, and it is the one the DIB is selected into.
    # CreateCompatibleDC is issued from a window DC that is still held:
    # ReleaseDC 済みの DC で呼ぶと NULL が返り、以降 _memory_dc == 0 のまま
    # present が毎回 no_hwnd を返し、1 枚も描かれないまま動く。
    assert api.memory_dc != 0
    assert (api.memory_dc, api.bitmap_handle) in api.select_object_calls
    compatible_at = next(
        index
        for index, (name, _args) in enumerate(api.log)
        if name == "create_compatible_dc"
    )
    assert [args for name, args in api.log[:compatible_at] if name == "release_dc"] == (
        []
    ), "the window DC was released before the memory DC was created from it"

    # Then: the startup self test ran, which it cannot do without a memory DC.
    assert surface.self_test().outcome == "sentinel_matched"

    # Then: and a composed frame actually reaches the child window DC.
    assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True
    assert surface.present().ok is True
    assert api.presented_blits()[-1].src_dc == api.memory_dc


def test_a_smaller_show_size_still_paints_the_fixed_capture_frame() -> None:
    # Given: the show size the app ships with (config.py's default, and what
    # the menu's Reset picks) and the camera's fixed capture size, which is
    # what the frames actually are. Nothing hands the show size to the camera,
    # so the two differ from the first frame onwards.
    module = _fresh_surface_module()
    show_size = (640, 360)
    api = RecordingGdiApi(client_size=show_size)
    surface = _attached(api, module, show_size)

    # When: a live 1280x720 capture frame is composited (core.Camera's
    # CAPTURE_SIZE, fixed at camera construction and never resized after).
    composed = surface.compose(_frame(720, 1280), _empty_overlay())
    presented = surface.present()

    # Then: it is accepted and blitted, so a small preview box cannot be what
    # blanks the preview. Sizing the expectation from the attach box instead
    # rejects every frame forever, which is indistinguishable from a dead
    # camera in the log.
    assert composed.ok is True, f"detail={composed.detail!r}"
    assert composed.detail == "ok"
    assert presented.ok is True
    blits = api.presented_blits()
    assert blits and (blits[-1].width, blits[-1].height) == show_size

    # Then: and the frame was drawn 1:1 into the top-left of the box, so the
    # picture is never scaled: only the part that fits was copied.
    drawn = api.dib_bgra()[:, :, :3]
    assert np.array_equal(drawn, _frame(720, 1280)[:360, :640])
    assert api.dib_bgra().shape == (360, 640, 4)


def test_a_failed_memory_dc_frees_the_dib_it_had_already_created() -> None:
    # Given: a double whose CreateCompatibleDC fails, as a GDI handle
    # exhaustion or a driver refusal makes it.
    module = _fresh_surface_module()
    api = RecordingGdiApi(compatible_dc_results=[0])

    # When: a child is attached.
    surface = module.GdiSurface(api=api)
    surface.attach(_PARENT_HWND, _SIZE)

    # Then: the DIB was built before the DC, so it exists and has to be freed
    # by hand. It never entered a memory DC, so nothing else will ever own it:
    # "a DIB section dies with its DC" does not apply, and a GDI handle would
    # survive every release.
    assert len(api.dib_sections) == 1
    orphan = api.dib_sections[0].hbitmap
    assert orphan in api.delete_object_calls

    # Then: and the handle ledger balances -- every GDI object the surface
    # created was deleted, so a failed attach leaks nothing at all.
    assert set(api.created_object_handles()) == set(api.delete_object_calls)

    # Then: the child window went away with it, and the surface is not attached.
    assert api.destroy_window_calls == [api.child_hwnd]
    assert surface.compose(_frame(720, 1280), _empty_overlay()).detail == "no_hwnd"


def test_a_refused_child_window_creation_owns_no_gdi_resource() -> None:
    # Given: a double whose CreateWindowExW returns NULL, as a foreign parent
    # HWND or an exhausted desktop makes it.
    module = _fresh_surface_module()
    api = RecordingGdiApi(create_window_results=[0])

    # When: a child is attached anyway.
    surface = module.GdiSurface(api=api)
    surface.attach(_PARENT_HWND, _SIZE)

    # Then: no DC is taken at all. GetDC(0) hands out the SCREEN DC, so
    # proceeding past a NULL hwnd silently builds against the screen.
    assert api.get_dc_calls == []

    # Then: and neither the 3.7MB DIB nor a memory DC exists to be orphaned,
    # because release() early-returns on a zero child and nobody would free
    # them.
    assert api.dib_headers == []
    assert api.compatible_dc_calls == []

    # Then: and the surface refuses to pretend rather than reporting a frame
    # it never drew.
    assert surface.compose(_frame(720, 1280), _empty_overlay()).detail == "no_hwnd"


def test_empty_overlay_issues_no_shape_calls() -> None:
    # Given: an attached surface and the all-default overlay.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    pens_at_attach = len(api.pen_calls)

    # When: a frame is composited with no overlay component set.
    result = surface.compose(_frame(720, 1280), _empty_overlay())

    # Then: no GDI shape call is issued, and the pen cache does not grow either.
    assert result.ok is True
    assert api.ellipse_calls == []
    assert api.rectangle_calls == []
    assert len(api.pen_calls) == pens_at_attach


def test_left_stick_alone_issues_ring_then_knob_with_plus_one_extents() -> None:
    # Given: an attached surface and one active left stick.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    stick = module.StickState(
        active=True, center_x=640, center_y=360, radius=60, knob_x=660, knob_y=350
    )

    # When: only the left stick is composited.
    result = surface.compose(_frame(720, 1280), module.OverlayState(left_stick=stick))

    # Then: exactly the ring and the knob, both into the memory DC, in that order.
    assert result.ok is True
    assert _shape_sequence(api) == [
        ("ellipse", 579, 299, 701, 421),
        ("ellipse", 653, 343, 667, 357),
    ]
    assert all(call.hdc == api.memory_dc for call in api.ellipse_calls)
    assert api.rectangle_calls == []

    # Then: the +1 compensation makes the rendered extent Tk's 2r+1, not 2r-1.
    ring, knob = api.ellipse_calls
    assert (ring.right - ring.left) - 1 == 2 * 60 + 1
    assert (knob.right - knob.left) - 1 == 2 * (60 // 10) + 1

    # Then: the knob is the one filled shape, so its brush is not the ring's.
    ring_pen, ring_brush = _selections_before_shape(api, "ellipse", 0)
    knob_pen, knob_brush = _selections_before_shape(api, "ellipse", 1)
    assert ring_pen and ring_brush
    assert knob_pen and knob_brush
    assert ring_pen != 0
    assert knob_brush != ring_brush


def test_right_stick_alone_issues_its_own_pair_from_its_own_centre() -> None:
    # Given: an attached surface and one active right stick.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    stick = module.StickState(
        active=True, center_x=200, center_y=120, radius=30, knob_x=215, knob_y=108
    )

    # When: only the right stick is composited.
    result = surface.compose(_frame(720, 1280), module.OverlayState(right_stick=stick))

    # Then: two ellipses from the right stick's own absolute centre and radius.
    assert result.ok is True
    assert _shape_sequence(api) == [
        ("ellipse", 169, 89, 231, 151),
        ("ellipse", 211, 104, 219, 112),
    ]
    assert (231 - 169) - 1 == 2 * 30 + 1
    assert (219 - 211) - 1 == 2 * (30 // 10) + 1


def test_both_sticks_issue_four_ellipses_left_pair_before_right_pair() -> None:
    # Given: an attached surface with both sticks active.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    overlay = module.OverlayState(
        left_stick=module.StickState(
            active=True, center_x=640, center_y=360, radius=60, knob_x=660, knob_y=350
        ),
        right_stick=module.StickState(
            active=True, center_x=200, center_y=120, radius=30, knob_x=215, knob_y=108
        ),
    )

    # When: the frame is composited.
    result = surface.compose(_frame(720, 1280), overlay)

    # Then: left ring, left knob, right ring, right knob -- in that order.
    assert result.ok is True
    assert _shape_sequence(api) == [
        ("ellipse", 579, 299, 701, 421),
        ("ellipse", 653, 343, 667, 357),
        ("ellipse", 169, 89, 231, 151),
        ("ellipse", 211, 104, 219, 112),
    ]


def test_guide_alone_issues_one_dashed_rectangle_with_plus_one_extents() -> None:
    # Given: an attached surface and a visible SelectArea guide.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    guide = module.RectState(x0=100, y0=200, x1=300, y1=400, visible=True)

    # When: only the guide is composited.
    result = surface.compose(_frame(720, 1280), module.OverlayState(guide=guide))

    # Then: exactly one Rectangle, expanded by 1 on the right and the bottom only.
    #       Tk's rectangle is inclusive on both edges, GDI excludes right and
    #       bottom, so only those two need the +1. Extending the left and top
    #       as well would draw the box one pixel larger than Tk did.
    assert result.ok is True
    assert _shape_sequence(api) == [("rectangle", 100, 200, 301, 401)]
    assert api.ellipse_calls == []

    # Then: the guide pen is dashed, so the SelectArea reads as a range box.
    pen, brush = _selections_before_shape(api, "rectangle", 0)
    assert pen in api.pen_handles
    assert _PS_DASH in [request.style for request in api.pen_calls]
    assert brush in set(api.hollow_brush_handles)


def test_img_rect_alone_issues_outer_then_inner_with_their_documented_pens() -> None:
    # Given: an attached surface and a visible ImgRect with two distinct rects.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    img_rect = module.ImgRectState(
        outer=module.RectState(x0=10, y0=20, x1=110, y1=220),
        inner=module.RectState(x0=20, y0=30, x1=100, y1=200),
        visible=True,
        color=_RECOGNITION,
    )

    # When: only the ImgRect is composited.
    result = surface.compose(_frame(720, 1280), module.OverlayState(img_rect=img_rect))

    # Then: outer then inner, each drawn with its own coordinates plus the GDI
    # extent compensation. The outer already carries the capture-pixel +1.0
    # expansion, and that expansion is ORTHOGONAL to the bounding-box rule, so
    # both apply: the GDI +1 is not a second copy of the capture-pixel one.
    assert result.ok is True
    assert _shape_sequence(api) == [
        ("rectangle", 10, 20, 111, 221),
        ("rectangle", 20, 30, 101, 201),
    ]
    assert api.ellipse_calls == []

    # Then: the outer is a 4 px white solid pen, the inner a 2 px pen in the
    # recognition colour the overlay was given, and the two pens differ.
    outer_pen, _outer_brush = _selections_before_shape(api, "rectangle", 0)
    inner_pen, _inner_brush = _selections_before_shape(api, "rectangle", 1)
    assert outer_pen != inner_pen
    outer_request = api.pen_calls[api.pen_handles.index(outer_pen)]
    inner_request = api.pen_calls[api.pen_handles.index(inner_pen)]
    assert (outer_request.style, outer_request.width) == (_PS_SOLID, 4)
    assert outer_request.color == _WHITE
    assert (inner_request.style, inner_request.width) == (_PS_SOLID, 2)
    assert inner_request.color == _RECOGNITION


def test_full_overlay_issues_all_seven_shapes_in_the_documented_order() -> None:
    # Given: an attached surface with every component active or visible.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    overlay = module.OverlayState(
        left_stick=module.StickState(
            active=True, center_x=640, center_y=360, radius=60, knob_x=660, knob_y=350
        ),
        right_stick=module.StickState(
            active=True, center_x=200, center_y=120, radius=30, knob_x=215, knob_y=108
        ),
        guide=module.RectState(x0=100, y0=200, x1=300, y1=400, visible=True),
        img_rect=module.ImgRectState(
            outer=module.RectState(x0=10, y0=20, x1=110, y1=220),
            inner=module.RectState(x0=20, y0=30, x1=100, y1=200),
            visible=True,
            color=_RECOGNITION,
        ),
    )

    # When: the frame is composited.
    result = surface.compose(_frame(720, 1280), overlay)

    # Then: the design's full shape order, with the exact integer extents. The
    # two ImgRect entries carry the GDI +1 like every other shape.
    assert result.ok is True
    assert _shape_sequence(api) == [
        ("ellipse", 579, 299, 701, 421),
        ("ellipse", 653, 343, 667, 357),
        ("ellipse", 169, 89, 231, 151),
        ("ellipse", 211, 104, 219, 112),
        ("rectangle", 100, 200, 301, 401),
        ("rectangle", 10, 20, 111, 221),
        ("rectangle", 20, 30, 101, 201),
    ]
    assert len(api.ellipse_calls) == 4
    assert len(api.rectangle_calls) == 3
    assert all(call.hdc == api.memory_dc for call in api.rectangle_calls)


def test_static_gdi_objects_are_created_once_at_attach_and_never_per_frame() -> None:
    # Given: an attached surface, with its static pens and brushes already built.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    assert len(api.pen_calls) >= 1
    static_brushes = api.brush_count()
    assert static_brushes >= 1
    overlay = _full_overlay(module)

    # When: the same full overlay is composited three times.
    counts = []
    for _ in range(3):
        assert surface.compose(_frame(720, 1280), overlay).ok is True
        counts.append(len(api.pen_calls))

    # Then: only the first compose grows the pen cache, for the new recognition
    # colour, and no later compose creates anything at all.
    assert counts[1] == counts[0]
    assert counts[2] == counts[0]
    assert api.brush_count() == static_brushes

    # Then: every selected object is one the double handed out, so nothing is
    # leaked by re-creating a pen per frame.
    created = api.created_object_handles()
    assert all(obj in created for _hdc, obj in api.select_object_calls)


def test_pen_cache_adds_one_pen_for_a_new_colour_and_none_for_a_repeat() -> None:
    # Given: an attached surface, past the point where the static set is built.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    static_pens = len(api.pen_calls)
    assert static_pens >= 1

    # When: the same ImgRect recognition colour is composited three times.
    for _ in range(3):
        overlay = _full_overlay(module, color=_RECOGNITION)
        assert surface.compose(_frame(720, 1280), overlay).ok is True
    repeated = len(api.pen_calls)
    first_inner = _last_shape_selections(api, "rectangle")[0]

    # Then: exactly one cached pen for that colour, created on first use only.
    assert repeated == static_pens + 1
    assert api.pen_calls[static_pens] == PenRequest(
        style=_PS_SOLID, width=2, color=_RECOGNITION
    )

    # When: the recognition colour changes.
    changed = _full_overlay(module, color=_RECOGNITION_CHANGED)
    assert surface.compose(_frame(720, 1280), changed).ok is True

    # Then: exactly one additional pen carrying the new colour verbatim, and the
    # inner rectangle now selects it instead of the cached original.
    assert len(api.pen_calls) == repeated + 1
    assert api.pen_calls[repeated] == PenRequest(
        style=_PS_SOLID, width=2, color=_RECOGNITION_CHANGED
    )
    assert _last_shape_selections(api, "rectangle")[0] != first_inner

    # When: the original colour comes back.
    assert (
        surface.compose(_frame(720, 1280), _full_overlay(module, color=_RECOGNITION)).ok
        is True
    )

    # Then: the cache is keyed on the colour, not appended to, so nothing grows
    # and the original pen is selected again.
    assert len(api.pen_calls) == repeated + 1
    assert _last_shape_selections(api, "rectangle")[0] == first_inner


def test_release_order_is_reverse_of_construction() -> None:
    # Given: an attached surface that has grown the pen cache, composed, presented.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    static_pens = len(api.pen_calls)
    for color in (_RECOGNITION, _RECOGNITION_CHANGED):
        assert (
            surface.compose(_frame(720, 1280), _full_overlay(module, color=color)).ok
            is True
        )
    assert surface.present().ok is True
    before = len(api.calls)
    objects = api.created_object_handles()
    brushes = api.all_brush_handles()
    assert objects and brushes
    assert len(api.pen_handles) == static_pens + 2

    # When: the surface is released.
    surface.release()

    # Then: DestroyWindow, then DeleteDC, then the pen and brush deletions.
    issued = api.calls[before:]
    _required_in_order(issued, ("destroy_window", "delete_dc", "delete_object"))
    assert api.destroy_window_calls == [api.child_hwnd]
    assert api.delete_dc_calls == [api.memory_dc]

    # Then: every cached pen is deleted along with the static objects -- the
    # cache is emptied, not just abandoned. The HBITMAP is excluded because it
    # is created but never deleted, pinned separately below.
    assert set(api.delete_object_calls) == set(api.pen_handles) | set(brushes)
    assert set(api.pen_handles) <= set(api.delete_object_calls)

    # Then: the brush is deleted after the pens, as design section 11 orders it.
    tail = api.delete_object_calls[-len(brushes) :]
    head = api.delete_object_calls[: -len(brushes)]
    assert set(tail) == set(brushes)
    assert set(head).isdisjoint(brushes)

    # Then: the DIB section HBITMAP is never DeleteObject'd. A DIB section dies
    # with its DC and deleting both is a double free.
    assert api.bitmap_handle not in api.delete_object_calls

    # Then: the window class is never unregistered, because it is registered
    # once per process and the surface can be recreated on camera reopen.
    assert "unregister_class" not in api.calls

    # And: nothing is created during teardown either.
    assert not [name for name in issued if name.startswith("create_")]


def test_release_is_idempotent_and_survives_a_failed_present() -> None:
    # Given: a surface whose present blit failed, then a first release.
    module = _fresh_surface_module()
    api = RecordingGdiApi(bitblt_results=[True, False])
    surface = _attached(api, module)
    assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True
    assert surface.present().ok is False
    before = len(api.calls)

    # When: release runs after the failure, and then runs again.
    surface.release()
    first_pass = api.calls[before:]

    # Then: the teardown order still holds after a failed present.
    _required_in_order(first_pass, ("destroy_window", "delete_dc", "delete_object"))

    # When: release and compose are both called after teardown.
    surface.release()
    after_release = surface.compose(_frame(720, 1280), _empty_overlay())

    # Then: the second release issues nothing, and a post-teardown compose is
    # rejected instead of writing through a released DIB or a freed DC.
    assert api.calls[before:] == first_pass
    assert after_release.ok is False
    assert after_release.detail == "no_hwnd"
    assert api.calls[before:] == first_pass


def test_client_size_reads_the_child_client_rect_as_int_pair() -> None:
    # Given: an attached 1280x720 surface.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)

    # When: the client size is read.
    size = surface.client_size()

    # Then: it is the child's GetClientRect reduced to a pair of plain ints.
    assert size == _SIZE
    assert all(type(value) is int for value in size)
    assert api.client_rect_calls == [api.child_hwnd]


def test_client_size_goes_stale_until_resize_reaches_the_child() -> None:
    # Given: an attached 1280x720 surface.
    module = _fresh_surface_module()
    api = RecordingGdiApi()
    surface = _attached(api, module)
    assert surface.client_size() == _SIZE

    # When: the parent resizes and the child is resized through SetWindowPos.
    surface.resize((800, 600))

    # Then: the new size reaches the child window.
    placed = api.window_pos_calls[-1]
    assert (placed.x, placed.y, placed.width, placed.height) == (0, 0, 800, 600)

    # Then: client_size still reports the old box, which is exactly why the Tk
    # <Configure> handler must call resize on every parent resize.
    assert surface.client_size() == _SIZE
    api.set_client_rect((800, 600))
    assert surface.client_size() == (800, 600)


# ===========================================================================
# C. the no-trampoline property and the child-DC startup self test
# ===========================================================================


def test_surface_module_creates_no_python_window_procedure() -> None:
    # Given: the surface source, read rather than imported.
    tree = _source_tree(_SURFACE_SOURCE)
    referenced = _referenced_names(tree)
    imported = _imported_names(tree)

    # Then: no WINFUNCTYPE, CFUNCTYPE, _WNDPROC_FACTORY or _WNDPROC_TYPE is
    # referenced or imported anywhere: the crash shape, and the dead type that
    # must not be revived from preview_clock.
    assert not (referenced & _BACKTRACKING_NAMES)
    assert not (imported & _BACKTRACKING_NAMES)
    assert not (imported & _TRAMPOLINE_FROM_CLOCK)


def test_window_class_uses_a_cast_defwindowproc_and_a_null_background_brush() -> None:
    # Given: the surface source.
    tree = _source_tree(_SURFACE_SOURCE)

    # Then: the class is registered with the cast native proc, so WM_PAINT and
    # WM_ERASEBKGND stay a no-op instead of forcing a Python callable.
    assert {"cast", "c_void_p", "DefWindowProcW"} <= _referenced_names(tree)

    # Then: the WNDCLASSEXW declares hbrBackground as a void pointer, so the
    # struct default of zero really is NULL.
    assert ("hbrBackground", "ctypes.c_void_p") in _structure_fields(tree)

    # Then: nothing anywhere assigns a non-NULL background brush.
    assignments = _attribute_assignments(tree, "hbrBackground")
    assert assignments, "hbrBackground must be assigned explicitly to NULL"
    assert all(_is_null_literal(value) for value in assignments)


def test_startup_self_test_reads_the_child_dc_and_never_the_screen_dc() -> None:
    # Given: an attach whose DIB is prefilled 0x5A, so a sentinel write shows up.
    module = _fresh_surface_module()
    api = RecordingGdiApi(prefill=_PREFILL)

    # When: the surface attaches, which runs the one-time self test.
    surface = _attached(api, module)

    # Then: a sentinel was written into the DIB; it no longer holds the prefill.
    assert api.dib_is_uniform(_PREFILL) is False

    # Then: it was blitted to the child's DC and read back from that same DC.
    blits = [blit for blit in api.presented_blits() if blit.dest_dc == api.child_dc]
    assert len(blits) == 1
    assert api.pixel_calls
    assert all(pixel.hdc == api.child_dc for pixel in api.pixel_calls)

    # Then: never the screen DC and never the desktop window. A foreign
    # occluder leaves the screen showing the previous frame, so a screen
    # readback is a false negative and the user sees a frozen picture.
    assert api.desktop_window_calls == 0
    assert all(request.hwnd != 0 for request in api.get_dc_calls)

    # Then: the healthy outcome is its own label, and the self test runs once.
    result = surface.self_test()
    assert result.outcome == "sentinel_matched"
    assert result.outcome in _SELF_TEST_OUTCOMES
    assert result.fully_occluded is False
    assert result.clip_box == (0, 0, 1280, 720)

    # When: more frames are composited and presented.
    for _ in range(3):
        assert surface.compose(_frame(720, 1280), _empty_overlay()).ok is True
        assert surface.present().ok is True

    # Then: the self test stays one-shot, never per frame.
    assert len(api.pixel_calls) == len(blits)


def test_self_test_reports_covered_for_clr_invalid_and_consults_the_clip_box() -> None:
    # Given: a child whose GetPixel returns CLR_INVALID with an empty clip box,
    # which is the fully occluded case design section 7 records.
    module = _fresh_surface_module()
    api = RecordingGdiApi(get_pixel_results=[_CLR_INVALID], clip_box=(0, 0, 0, 0))

    # When: the surface attaches and runs the self test.
    surface = _attached(api, module)
    result = surface.self_test()

    # Then: CLR_INVALID gets its own label, never folded into wrong colour, and
    # the clip box is consulted because its emptiness is the only usable signal
    # that the child is fully occluded.
    assert result.outcome == "covered"
    assert result.outcome != "wrong_color"
    assert result.fully_occluded is True
    assert api.clip_box_calls
    assert result.clip_box == (0, 0, 0, 0)


def test_self_test_reports_covered_without_occlusion_when_the_clip_box_is_full() -> (
    None
):
    # Given: CLR_INVALID with a full clip box, the Tk Label over the child case.
    module = _fresh_surface_module()
    api = RecordingGdiApi(get_pixel_results=[_CLR_INVALID], clip_box=(0, 0, 1280, 720))

    # When: the surface attaches and runs the self test.
    surface = _attached(api, module)
    result = surface.self_test()

    # Then: the point read still failed, but the child is not fully occluded.
    assert result.outcome == "covered"
    assert result.fully_occluded is False
    assert api.clip_box_calls


def test_self_test_distinguishes_a_wrong_colour_readback_from_an_occluder() -> None:
    # Given: a valid but different readback with a full clip box.
    module = _fresh_surface_module()
    api = RecordingGdiApi(get_pixel_results=[0x00FF00FF], clip_box=(0, 0, 1280, 720))

    # When: the surface attaches and runs the self test.
    surface = _attached(api, module)
    result = surface.self_test()

    # Then: a wrong pixel is its own outcome and the clip box is not consulted,
    # because only a failed point read leaves the occluder unnamed.
    assert result.outcome == "wrong_color"
    assert result.fully_occluded is False
    assert api.clip_box_calls == []


def _library_of(handle_name: str) -> Any:
    return {
        "user32": ctypes.WinDLL("user32", use_last_error=True),
        "gdi32": ctypes.WinDLL("gdi32", use_last_error=True),
        "kernel32": ctypes.WinDLL("kernel32", use_last_error=True),
    }[handle_name]


@pytest.mark.skipif(os.name != "nt", reason="Win32 symbol lookup is Windows-only")
def test_every_win32_symbol_resolves_against_the_library_it_is_read_from() -> None:
    # Given: every library-qualified symbol the real api reads, found by
    #       walking the module rather than by trusting the constructor.
    module = _surface_module()
    source = Path(str(module.__file__)).read_text(encoding="utf-8")
    reads = sorted(
        {
            f"{node.value.id}.{node.attr}"
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"user32", "gdi32", "kernel32"}
        }
    )

    # Then: each resolves on the library it is read from. A wrong-library read
    #       raises AttributeError on first use and nothing else: the recording
    #       double implements the names, so it cannot see this at all, and the
    #       cost of finding it by hand was a full gated E2E cycle.
    assert reads, "no library-qualified Win32 reads found; the AST walk is broken"
    missing = [
        qualified
        for qualified in reads
        if not hasattr(
            _library_of(qualified.split(".", 1)[0]), qualified.split(".", 1)[1]
        )
    ]
    assert not missing, f"read from a library that does not export them: {missing}"


@pytest.mark.skipif(os.name != "nt", reason="Win32 symbol lookup is Windows-only")
def test_the_real_apis_every_symbol_declares_argtypes_and_restype() -> None:
    # Given: the real ctypes api rather than the recording double.
    module = _surface_module()

    # When: it is constructed, which resolves every symbol by name.
    api = module.CtypesGdiApi()

    # Then: each resolved function declares both. A missing argtypes is the
    #       documented intermittent-failure hazard: GDI handles on this machine
    #       are sometimes above 32 bits and sometimes not, so the truncation
    #       passes a smoke test and fails in the field.
    functions = {
        name: value
        for name, value in vars(api).items()
        if hasattr(value, "argtypes") and hasattr(value, "restype")
    }
    assert len(functions) >= 20, f"only {len(functions)} symbols resolved"
    undeclared = [name for name, fn in functions.items() if fn.argtypes is None]
    assert not undeclared, f"no argtypes declared: {sorted(undeclared)}"
