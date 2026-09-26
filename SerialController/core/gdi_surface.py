"""プレビュー面の本体。Tk にも GDI にも直接触らず、注入された api だけを使う。

設計: docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md
6節・7節・11節。動画とオーバーレイは同じ面に描く。子窓は親の絵より常に上
に合成されるので、Canvas を透かす手は使えない（0節）。

このファイルは 2 ファイル制約のため、26 メソッドの api 契約・ctypes 実装・
面の本体を同居させている。
"""

from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np
from core.preview_renderer import (
    ImgRectState,
    OverlayState,
    RectState,
    RenderResult,
    StickState,
)
from loguru import logger

# ---------------------------------------------------------------------------
# Win32 / GDI 定数
# ---------------------------------------------------------------------------
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_WS_CLIPSIBLINGS = 0x04000000
_SWP_SHOWWINDOW = 0x0040
_HWND_TOP = 0
_SRCCOPY = 0x00CC0020
_PS_SOLID = 0
_PS_DASH = 1
_BI_RGB = 0
_BITSPIXEL = 12
_CLR_INVALID = 0xFFFFFFFF
_NULL_BRUSH_STOCK = 1

_WINDOW_CLASS = "PokeConPreviewSurface"
_SUPPORTED_BIT_DEPTHS = (16, 24, 32)
# ノブ半径は外周の 1/10（GuiAssets.py:980 の整数除算そのまま）。
_KNOB_RADIUS_DIVISOR = 10
# GDI のバウンディングボックスは右端と下端を含まないので extent は
# right-left-1。Tk の box は包含端で right-left+1 描く。見た目を保つには +1 を
# 渡す。認識枠の外面の capture 座標 +1.0 補正とは別の話なので両方足す。
_EXTENT_COMPENSATION = 1
# Tk は 4.5 / 2.5 の実数幅を受け付けるが CreatePen は整数。0.5px 以内の丸め
# なのでここでは 4 と 2 を使う。
_OUTER_STROKE_WIDTH = 4
_INNER_STROKE_WIDTH = 2
_GUIDE_STROKE_WIDTH = 1
_RING_STROKE_WIDTH = 1
# 3 バイトが同じなので、COLORREF でも Tk 内部形でも同じ値になる。
_WHITE = 0x00FFFFFF
# 起動時の自己検査で書き込む値。B と R が同じなので、DIB のメモリ順と COLORREF
# のバイト位置が食い違っても同じ値として読み戻る。sentinel の役割は「読めた
# か」だけなので、色順の検証には使わない。
_SELF_TEST_SENTINEL = 0x0000FF00
# クラスはプロセスに 1 度だけ登録し、解放時も UnregisterClass しない（11節 5）。
_CLASS_REGISTERED = False


def _colorref(tk_color: int) -> int:
    """Tk 内部の 0x00RRGGBB を Win32 COLORREF 0x00BBGGRR へ変換する。

    Tk 側の色は左スティックが "cyan"、右スティックと範囲枠が "red"、認識枠の
    外側が "white"、内側が `ImgRectState.color`。Tk と Win32 でバイト位置が逆
    なので、境界で必ず変換する。
    """
    red = (tk_color >> 16) & 0xFF
    green = (tk_color >> 8) & 0xFF
    blue = tk_color & 0xFF
    return (blue << 16) | (green << 8) | red


_LEFT_STICK_COLOR = _colorref(0x00FFFF)
_RIGHT_STICK_COLOR = _colorref(0xFF0000)
_GUIDE_COLOR = _RIGHT_STICK_COLOR


def _dib_channels(colorref: int) -> tuple[int, int, int, int]:
    """32bpp BI_RGB のメモリ順 B, G, R, A を並べる。COLORREF と逆。"""
    return ((colorref >> 16) & 0xFF, (colorref >> 8) & 0xFF, colorref & 0xFF, 0xFF)


def _is_empty_box(box: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = box
    return right <= left and bottom <= top


@dataclass(frozen=True, slots=True)
class DibInfo:
    """``CreateDIBSection`` に渡す BITMAPINFOHEADER。``bi_height`` は負=top-down。"""

    bi_width: int
    bi_height: int
    bi_bit_count: int
    bi_compression: int
    bi_planes: int = 1
    bi_clr_used: int = 0


@dataclass(frozen=True, slots=True)
class DibSection:
    """``CreateDIBSection`` の戻り。``bits_address`` が numpy が見張る先頭。"""

    hbitmap: int
    bits_address: int


@dataclass(frozen=True, slots=True)
class SelfTestResult:
    """起動時 1 回の自己検査の結果。``clip_box`` 未取得なら空ボックスのまま。"""

    outcome: str  # "not_run" | "sentinel_matched" | "wrong_color" | "covered"
    fully_occluded: bool
    clip_box: tuple[int, int, int, int] = (0, 0, 0, 0)


class GdiSurfaceApi(Protocol):
    """``GdiSurface`` が Win32 に触る唯一の口。26 メソッドが全部。"""

    def set_process_dpi_aware(self) -> bool: ...

    def register_class_ex(
        self, class_name: str, instance: int, window_proc: Any
    ) -> int: ...

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
    ) -> int: ...

    def enable_window(self, hwnd: int, enabled: bool) -> bool: ...

    def get_dc(self, hwnd: int) -> int: ...

    def release_dc(self, hwnd: int, hdc: int) -> int: ...

    def get_device_caps(self, hdc: int, index: int) -> int: ...

    def create_dib_section(self, info: DibInfo) -> DibSection: ...

    def create_compatible_dc(self, hdc: int) -> int: ...

    def select_object(self, hdc: int, obj: int) -> int: ...

    def set_window_pos(
        self,
        hwnd: int,
        insert_after: int,
        x: int,
        y: int,
        width: int,
        height: int,
        flags: int,
    ) -> bool: ...

    def create_pen(self, style: int, width: int, color: int) -> int: ...

    def create_hollow_brush(self) -> int: ...

    def create_solid_brush(self, color: int) -> int: ...

    def ellipse(
        self, hdc: int, left: int, top: int, right: int, bottom: int
    ) -> bool: ...

    def rectangle(
        self, hdc: int, left: int, top: int, right: int, bottom: int
    ) -> bool: ...

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
    ) -> bool: ...

    def get_pixel(self, hdc: int, x: int, y: int) -> int: ...

    def get_clip_box(self, hdc: int) -> tuple[int, int, int, int]: ...

    def get_client_rect(self, hwnd: int) -> tuple[int, int, int, int]: ...

    def get_desktop_window(self) -> int: ...

    def native_window_proc(self) -> Any: ...

    def destroy_window(self, hwnd: int) -> bool: ...

    def delete_dc(self, hdc: int) -> bool: ...

    def delete_object(self, obj: int) -> bool: ...

    def get_last_error(self) -> int: ...


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _as_handle(value: int | None) -> int:
    return int(value) if value else 0


class CtypesGdiApi:
    """実物の Win32 呼び出し。

    この環境ではハンドル値が 32 ビットを超える場合と超えない場合がある。
    ``argtypes`` を付けないと 32 ビットに切り詰められて断続的に壊れ、
    スモークテストでは取り逃がす。そのため全呼び出しに argtypes と restype
    の両方を必ず付ける。
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("GDI surface は Windows 専用")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._set_dpi_aware = user32.SetProcessDPIAware
        self._set_dpi_aware.argtypes = []
        self._set_dpi_aware.restype = ctypes.c_int

        self._register_class = user32.RegisterClassExW
        self._register_class.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
        self._register_class.restype = ctypes.c_ushort

        self._create_window = user32.CreateWindowExW
        self._create_window.argtypes = [
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._create_window.restype = ctypes.c_void_p

        self._enable_window = user32.EnableWindow
        self._enable_window.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._enable_window.restype = ctypes.c_int

        self._get_dc = user32.GetDC
        self._get_dc.argtypes = [ctypes.c_void_p]
        self._get_dc.restype = ctypes.c_void_p

        self._release_dc = user32.ReleaseDC
        self._release_dc.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._release_dc.restype = ctypes.c_int

        self._get_device_caps = user32.GetDeviceCaps
        self._get_device_caps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._get_device_caps.restype = ctypes.c_int

        self._get_client_rect = user32.GetClientRect
        self._get_client_rect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
        self._get_client_rect.restype = ctypes.c_int

        self._get_clip_box = gdi32.GetClipBox
        self._get_clip_box.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
        self._get_clip_box.restype = ctypes.c_int

        # GetPixel は COLORREF を返すので 32 ビット符号なし。
        self._get_pixel = gdi32.GetPixel
        self._get_pixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._get_pixel.restype = ctypes.c_ulong

        self._set_window_pos = user32.SetWindowPos
        self._set_window_pos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        self._set_window_pos.restype = ctypes.c_int

        self._get_desktop_window = user32.GetDesktopWindow
        self._get_desktop_window.argtypes = []
        self._get_desktop_window.restype = ctypes.c_void_p

        self._get_module_handle = kernel32.GetModuleHandleW
        self._get_module_handle.argtypes = [ctypes.c_wchar_p]
        self._get_module_handle.restype = ctypes.c_void_p

        self._create_dib_section = gdi32.CreateDIBSection
        self._create_dib_section.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFOHEADER),
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        self._create_dib_section.restype = ctypes.c_void_p

        self._create_compatible_dc = gdi32.CreateCompatibleDC
        self._create_compatible_dc.argtypes = [ctypes.c_void_p]
        self._create_compatible_dc.restype = ctypes.c_void_p

        self._select_object = gdi32.SelectObject
        self._select_object.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._select_object.restype = ctypes.c_void_p

        self._create_pen = gdi32.CreatePen
        self._create_pen.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        self._create_pen.restype = ctypes.c_void_p

        self._create_solid_brush = gdi32.CreateSolidBrush
        self._create_solid_brush.argtypes = [ctypes.c_ulong]
        self._create_solid_brush.restype = ctypes.c_void_p

        self._get_stock_object = gdi32.GetStockObject
        self._get_stock_object.argtypes = [ctypes.c_int]
        self._get_stock_object.restype = ctypes.c_void_p

        self._ellipse = gdi32.Ellipse
        self._ellipse.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._ellipse.restype = ctypes.c_int

        self._rectangle = gdi32.Rectangle
        self._rectangle.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._rectangle.restype = ctypes.c_int

        self._bit_blt = gdi32.BitBlt
        self._bit_blt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self._bit_blt.restype = ctypes.c_int

        self._destroy_window = user32.DestroyWindow
        self._destroy_window.argtypes = [ctypes.c_void_p]
        self._destroy_window.restype = ctypes.c_int

        self._delete_dc = gdi32.DeleteDC
        self._delete_dc.argtypes = [ctypes.c_void_p]
        self._delete_dc.restype = ctypes.c_int

        self._delete_object = gdi32.DeleteObject
        self._delete_object.argtypes = [ctypes.c_void_p]
        self._delete_object.restype = ctypes.c_int

        # Python のウィンドウプロシージャは作らない。2 つ目の trampoline は
        # 以前のクラッシュ 0xC0000409 の形として追跡済み（7節）。
        self._def_window_proc = user32.DefWindowProcW
        self._module_handle = _as_handle(self._get_module_handle(None))

    def set_process_dpi_aware(self) -> bool:
        return bool(self._set_dpi_aware())

    def register_class_ex(
        self, class_name: str, instance: int, window_proc: Any
    ) -> int:
        window_class = _WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        window_class.lpfnWndProc = ctypes.cast(window_proc, ctypes.c_void_p)
        window_class.hInstance = instance if instance else self._module_handle
        window_class.lpszClassName = class_name
        # NULL のままだと DefWindowProcW の既定で WM_PAINT も WM_ERASEBKGND
        # も何も起こらない。提示した絵を消されないようにする唯一の手段。
        window_class.hbrBackground = None
        return int(self._register_class(ctypes.byref(window_class)))

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
        return _as_handle(
            self._create_window(
                ex_style,
                class_name,
                None,
                style,
                x,
                y,
                width,
                height,
                parent,
                None,
                instance if instance else self._module_handle,
                None,
            )
        )

    def enable_window(self, hwnd: int, enabled: bool) -> bool:
        return bool(self._enable_window(hwnd, int(enabled)))

    def get_dc(self, hwnd: int) -> int:
        return _as_handle(self._get_dc(hwnd))

    def release_dc(self, hwnd: int, hdc: int) -> int:
        return int(self._release_dc(hwnd, hdc))

    def get_device_caps(self, hdc: int, index: int) -> int:
        return int(self._get_device_caps(hdc, index))

    def create_dib_section(self, info: DibInfo) -> DibSection:
        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth = info.bi_width
        header.biHeight = info.bi_height
        header.biPlanes = info.bi_planes
        header.biBitCount = info.bi_bit_count
        header.biCompression = info.bi_compression
        header.biClrUsed = info.bi_clr_used
        bits = ctypes.c_void_p()
        hbitmap = _as_handle(
            self._create_dib_section(
                None, ctypes.byref(header), 0, ctypes.byref(bits), None, 0
            )
        )
        return DibSection(hbitmap=hbitmap, bits_address=bits.value or 0)

    def create_compatible_dc(self, hdc: int) -> int:
        return _as_handle(self._create_compatible_dc(hdc))

    def select_object(self, hdc: int, obj: int) -> int:
        # 直前に選ばれたオブジェクトを返す。成功判定にはならない。
        return _as_handle(self._select_object(hdc, obj))

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
        return bool(
            self._set_window_pos(hwnd, insert_after, x, y, width, height, flags)
        )

    def create_pen(self, style: int, width: int, color: int) -> int:
        return _as_handle(self._create_pen(style, width, color))

    def create_hollow_brush(self) -> int:
        return _as_handle(self._get_stock_object(_NULL_BRUSH_STOCK))

    def create_solid_brush(self, color: int) -> int:
        return _as_handle(self._create_solid_brush(color))

    def ellipse(self, hdc: int, left: int, top: int, right: int, bottom: int) -> bool:
        return bool(self._ellipse(hdc, left, top, right, bottom))

    def rectangle(self, hdc: int, left: int, top: int, right: int, bottom: int) -> bool:
        return bool(self._rectangle(hdc, left, top, right, bottom))

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
        return bool(
            self._bit_blt(
                dest_dc, dest_x, dest_y, width, height, src_dc, src_x, src_y, rop
            )
        )

    def get_pixel(self, hdc: int, x: int, y: int) -> int:
        return int(self._get_pixel(hdc, x, y))

    def get_clip_box(self, hdc: int) -> tuple[int, int, int, int]:
        rect = _RECT()
        if not self._get_clip_box(hdc, ctypes.byref(rect)):
            return (0, 0, 0, 0)
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))

    def get_client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = _RECT()
        if not self._get_client_rect(hwnd, ctypes.byref(rect)):
            return (0, 0, 0, 0)
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))

    def get_desktop_window(self) -> int:
        return _as_handle(self._get_desktop_window())

    def native_window_proc(self) -> Any:
        return self._def_window_proc

    def destroy_window(self, hwnd: int) -> bool:
        return bool(self._destroy_window(hwnd))

    def delete_dc(self, hdc: int) -> bool:
        return bool(self._delete_dc(hdc))

    def delete_object(self, obj: int) -> bool:
        return bool(self._delete_object(obj))

    def get_last_error(self) -> int:
        return ctypes.get_last_error()


class GdiSurface:
    """子 HWND とバックバッファとオーバーレイ描画を持つ面。Tk には触らない。"""

    def __init__(self, api: GdiSurfaceApi | None = None) -> None:
        self._api: GdiSurfaceApi = api if api is not None else CtypesGdiApi()
        self._child = 0
        self._memory_dc = 0
        self._back: np.ndarray[tuple[int, ...], np.dtype[np.uint8]] | None = None
        self._size = (0, 0)
        self._pending_frame = False
        self._self_test = SelfTestResult("not_run", False, (0, 0, 0, 0))
        self._ring_pens: dict[int, int] = {}
        self._knob_brushes: dict[int, int] = {}
        self._guide_pen = 0
        self._outer_pen = 0
        self._hollow_brush = 0
        self._pen_cache: dict[tuple[int, int, int], int] = {}

    @property
    def back_buffer(self) -> np.ndarray[tuple[int, ...], np.dtype[np.uint8]] | None:
        return self._back

    def self_test(self) -> SelfTestResult:
        return self._self_test

    def attach(self, parent_hwnd: int, size: tuple[int, int]) -> None:
        global _CLASS_REGISTERED
        width, height = size
        # 画面座標が 150% DPI で仮想化されると 1:1 判断が静かに壊れる。
        self._api.set_process_dpi_aware()
        if not _CLASS_REGISTERED:
            # instance に渡す 0 は「実装側でモジュールハンドルを解決して」の
            # 意味。hInstance=NULL の登録はこの環境では通らない。
            self._api.register_class_ex(
                _WINDOW_CLASS, 0, self._api.native_window_proc()
            )
            _CLASS_REGISTERED = True
        child = self._api.create_window_ex(
            class_name=_WINDOW_CLASS,
            instance=0,
            parent=parent_hwnd,
            ex_style=0,
            x=0,
            y=0,
            width=width,
            height=height,
            style=_WS_CHILD | _WS_VISIBLE | _WS_CLIPSIBLINGS,
        )
        # 125Hz の Tk bind() がマウスを受け続けるよう子窓は入力を取らない。
        self._api.enable_window(child, False)
        window_dc = self._api.get_dc(child)
        if window_dc == 0:
            logger.warning(
                "プレビュー子窓の DC を取れない hwnd={} error={}",
                child,
                self._api.get_last_error(),
            )
            return
        try:
            # GetDeviceCaps はメモリ DC だと 0 を返すので子窓の DC で問う。
            reported = self._api.get_device_caps(window_dc, _BITSPIXEL)
        finally:
            self._api.release_dc(child, window_dc)
        bit_count = (
            reported if reported in _SUPPORTED_BIT_DEPTHS else _SUPPORTED_BIT_DEPTHS[-1]
        )
        info = DibInfo(
            bi_width=width,
            bi_height=-height,
            bi_bit_count=bit_count,
            bi_compression=_BI_RGB,
        )
        section = self._api.create_dib_section(info)
        if section.hbitmap == 0 or section.bits_address == 0:
            # ここで諦めておかないと NULL を 3.7MB ぶん展開して落ちる。
            logger.warning(
                "プレビュー用 DIB を作れない {}x{} {}bpp error={}",
                width,
                height,
                bit_count,
                self._api.get_last_error(),
            )
            return
        memory_dc = self._api.create_compatible_dc(window_dc)
        self._api.select_object(memory_dc, section.hbitmap)
        self._child = child
        self._memory_dc = memory_dc
        self._size = (width, height)
        self._back = self._wrap_bits(section, info)
        self._place(width, height)
        self._build_static_objects()
        self._run_self_test()

    def client_size(self) -> tuple[int, int]:
        if self._child == 0:
            return (0, 0)
        left, top, right, bottom = self._api.get_client_rect(self._child)
        return (int(right - left), int(bottom - top))

    def resize(self, size: tuple[int, int]) -> None:
        if self._child == 0:
            return
        self._place(size[0], size[1])

    def compose(self, frame: np.ndarray, overlay: OverlayState) -> RenderResult:
        started = time.perf_counter_ns()
        if self._child == 0 or self._back is None:
            return RenderResult(False, time.perf_counter_ns() - started, "no_hwnd")
        # strides[0] >= w*3 は C-contiguous でも常に成り立つので証拠にならない。
        if not frame.flags.c_contiguous:
            return RenderResult(
                False, time.perf_counter_ns() - started, "frame_not_contiguous"
            )
        # ここで止めないと 640x360 の frame が 1280x720 のバッファへ範囲外書き
        # 込む。縮小も拡大もせず捨てる。
        if frame.shape[1::-1] != self.client_size():
            return RenderResult(
                False, time.perf_counter_ns() - started, "dimension_mismatch"
            )
        cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA, dst=self._back)
        self._draw_overlay(overlay)
        self._pending_frame = True
        return RenderResult(True, time.perf_counter_ns() - started, "ok")

    def present(self) -> RenderResult:
        started = time.perf_counter_ns()
        if self._child == 0 or self._memory_dc == 0:
            return RenderResult(False, time.perf_counter_ns() - started, "no_hwnd")
        if not self._pending_frame:
            return RenderResult(False, time.perf_counter_ns() - started, "no_frame")
        child_dc = self._api.get_dc(self._child)
        if child_dc == 0:
            return RenderResult(False, time.perf_counter_ns() - started, "getdc_failed")
        width, height = self._size
        try:
            copied = self._api.bit_blt(
                child_dc, 0, 0, width, height, self._memory_dc, 0, 0, _SRCCOPY
            )
        finally:
            self._api.release_dc(self._child, child_dc)
        self._pending_frame = False
        if not copied:
            return RenderResult(
                False, time.perf_counter_ns() - started, "bitblt_failed"
            )
        return RenderResult(True, time.perf_counter_ns() - started, "ok")

    def release(self) -> None:
        if self._child == 0:
            return
        # lpvBits はこの配列を指すので DestroyWindow より先に落とす。
        self._back = None
        self._pending_frame = False
        child, memory_dc = self._child, self._memory_dc
        self._child = 0
        self._memory_dc = 0
        self._api.destroy_window(child)
        # DIB section は DC と共に死ぬ。HBITMAP にも DeleteObject すると二重解放。
        self._api.delete_dc(memory_dc)
        for pen in self._ring_pens.values():
            self._api.delete_object(pen)
        self._api.delete_object(self._guide_pen)
        self._api.delete_object(self._outer_pen)
        for pen in self._pen_cache.values():
            self._api.delete_object(pen)
        for brush in self._knob_brushes.values():
            self._api.delete_object(brush)
        if self._hollow_brush:
            self._api.delete_object(self._hollow_brush)
        self._ring_pens = {}
        self._knob_brushes = {}
        self._guide_pen = 0
        self._outer_pen = 0
        self._hollow_brush = 0
        self._pen_cache = {}

    def _place(self, width: int, height: int) -> None:
        self._api.set_window_pos(
            self._child, _HWND_TOP, 0, 0, width, height, _SWP_SHOWWINDOW
        )

    def _wrap_bits(
        self, section: DibSection, info: DibInfo
    ) -> np.ndarray[tuple[int, ...], np.dtype[np.uint8]]:
        channels = info.bi_bit_count // 8
        width = info.bi_width
        stride = ((width * channels + 3) // 4) * 4
        # np.frombuffer は読み取り専用になるので as_array で包む。
        raw = ctypes.cast(section.bits_address, ctypes.POINTER(ctypes.c_ubyte))
        return np.ctypeslib.as_array(raw, shape=(stride * -info.bi_height,)).reshape(
            -info.bi_height, width, channels
        )

    def _build_static_objects(self) -> None:
        # 静的な 4 本: 左外周・右外周・範囲枠（破線）・認識枠の外側（白 4px）。
        self._ring_pens = {
            color: self._api.create_pen(_PS_SOLID, _RING_STROKE_WIDTH, color)
            for color in (_LEFT_STICK_COLOR, _RIGHT_STICK_COLOR)
        }
        self._guide_pen = self._api.create_pen(
            _PS_DASH, _GUIDE_STROKE_WIDTH, _GUIDE_COLOR
        )
        self._outer_pen = self._api.create_pen(_PS_SOLID, _OUTER_STROKE_WIDTH, _WHITE)
        self._hollow_brush = self._api.create_hollow_brush()
        self._knob_brushes = {
            color: self._api.create_solid_brush(color) for color in self._ring_pens
        }

    def _pen(self, style: int, width: int, color: int) -> int:
        """attach 後に GDI 割り当てが許されるのはこの 1 箇所だけ。"""
        key = (style, width, color)
        cached = self._pen_cache.get(key)
        if cached is None:
            cached = self._api.create_pen(style, width, color)
            self._pen_cache[key] = cached
        return cached

    def _select(self, pen: int, brush: int) -> None:
        self._api.select_object(self._memory_dc, pen)
        self._api.select_object(self._memory_dc, brush)

    def _draw_stick(self, pen: int, knob_brush: int, stick: StickState) -> None:
        pad = _EXTENT_COMPENSATION
        self._select(pen, self._hollow_brush)
        self._api.ellipse(
            self._memory_dc,
            stick.center_x - stick.radius - pad,
            stick.center_y - stick.radius - pad,
            stick.center_x + stick.radius + pad,
            stick.center_y + stick.radius + pad,
        )
        knob = stick.radius // _KNOB_RADIUS_DIVISOR
        self._select(pen, knob_brush)
        self._api.ellipse(
            self._memory_dc,
            stick.knob_x - knob - pad,
            stick.knob_y - knob - pad,
            stick.knob_x + knob + pad,
            stick.knob_y + knob + pad,
        )

    def _draw_rect(self, pen: int, rect: RectState) -> None:
        self._select(pen, self._hollow_brush)
        self._api.rectangle(
            self._memory_dc,
            rect.x0,
            rect.y0,
            rect.x1 + _EXTENT_COMPENSATION,
            rect.y1 + _EXTENT_COMPENSATION,
        )

    def _draw_img_rect(self, img_rect: ImgRectState) -> None:
        # 外面は :1218 の capture 座標 +1.0 補正済み。GDI の +1 補正とは直交
        # するので両方を足す（同じ補正の重複ではない）。
        self._draw_rect(self._outer_pen, img_rect.outer)
        self._draw_rect(
            self._pen(_PS_SOLID, _INNER_STROKE_WIDTH, img_rect.color), img_rect.inner
        )

    def _draw_overlay(self, overlay: OverlayState) -> None:
        # 順序は設計 5節の表どおり。空の既定値なら 1 度も形を呼ばない。
        sticks = (
            (overlay.left_stick, _LEFT_STICK_COLOR),
            (overlay.right_stick, _RIGHT_STICK_COLOR),
        )
        for stick, color in sticks:
            if stick.active:
                self._draw_stick(
                    self._ring_pens[color], self._knob_brushes[color], stick
                )
        guide = overlay.guide
        if guide.visible:
            # 範囲枠だけは 4 辺とも 1 伸ばす。Tk 側が max_x+1 で箱を作る。
            self._select(self._guide_pen, self._hollow_brush)
            self._api.rectangle(
                self._memory_dc,
                guide.x0 - _EXTENT_COMPENSATION,
                guide.y0 - _EXTENT_COMPENSATION,
                guide.x1 + _EXTENT_COMPENSATION,
                guide.y1 + _EXTENT_COMPENSATION,
            )
        if overlay.img_rect.visible:
            self._draw_img_rect(overlay.img_rect)

    def _run_self_test(self) -> None:
        """sentinel を書いて child DC で読み戻す。面ごとに 1 回だけ。

        外部の occluder が重なると画面 DC には前のフレームが残るので読み戻しは
        偽陰性になり、利用者は「固まった絵」を見ることになる。読めるのは child
        DC だけ。完全に隠された window の DC は clip 領域が空なので BitBlt は
        何も描かずに 1 を返し、GetLastError も 0 のまま。
        """
        if self._back is None or self._memory_dc == 0:
            return
        channels = self._back.shape[2]
        self._back[0, 0, :] = _dib_channels(_SELF_TEST_SENTINEL)[:channels]
        child_dc = self._api.get_dc(self._child)
        if child_dc == 0:
            return
        width, height = self._size
        try:
            if not self._api.bit_blt(
                child_dc, 0, 0, width, height, self._memory_dc, 0, 0, _SRCCOPY
            ):
                return
            pixel = self._api.get_pixel(child_dc, 0, 0)
            if pixel == _CLR_INVALID:
                # 読めなかった時だけ occluder 特定に必要なのが clip 領域。
                clip_box = self._api.get_clip_box(child_dc)
                self._self_test = SelfTestResult(
                    "covered", _is_empty_box(clip_box), clip_box
                )
            elif pixel == _SELF_TEST_SENTINEL:
                clip_box = self._api.get_clip_box(child_dc)
                self._self_test = SelfTestResult("sentinel_matched", False, clip_box)
            else:
                # 読めて違う色なら occluder の影はもう要らないので聞かない。
                self._self_test = SelfTestResult("wrong_color", False, (0, 0, 0, 0))
        finally:
            self._api.release_dc(self._child, child_dc)
        if self._self_test.outcome != "sentinel_matched":
            logger.warning(
                "プレビュー面の自己検査に失敗 outcome={} fully_occluded={} clip_box={}",
                self._self_test.outcome,
                self._self_test.fully_occluded,
                self._self_test.clip_box,
            )
