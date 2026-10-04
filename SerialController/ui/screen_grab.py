#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""screen_grab.py - 画面の一部を速く撮る（Windows のみ）。

PIL.ImageGrab は 1100x700 で 80〜150ms かかり（CAPTUREBLT 付きで重ね窓まで
撮るため）、ドラッグの始めに使うと引っかかりになる。画面の DC から BitBlt で
1 回写すだけにする（数 ms）。Windows 以外は None を返す。
"""

from __future__ import annotations

import ctypes
import sys

from PIL import Image

_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0
_BI_RGB = 0


class _BitmapInfoHeader(ctypes.Structure):
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


if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32")
    _gdi32 = ctypes.WinDLL("gdi32")
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    _gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    _gdi32.BitBlt.restype = wintypes.BOOL
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]


def grab(x: int, y: int, width: int, height: int) -> Image.Image | None:
    """画面座標の (x, y) から width x height を撮る。撮れなければ None。"""
    if sys.platform != "win32" or width <= 0 or height <= 0:
        return None
    screen = _user32.GetDC(None)
    if not screen:
        return None
    memory = _gdi32.CreateCompatibleDC(screen)
    header = _BitmapInfoHeader()
    header.biSize = ctypes.sizeof(_BitmapInfoHeader)
    header.biWidth = width
    header.biHeight = -height  # 負で上から下の並び（PIL と同じ向き）。
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = _BI_RGB
    bits = ctypes.c_void_p()
    bitmap = _gdi32.CreateDIBSection(
        memory, ctypes.byref(header), _DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    try:
        if not bitmap or not bits.value:
            return None
        previous = _gdi32.SelectObject(memory, bitmap)
        ok = _gdi32.BitBlt(memory, 0, 0, width, height, screen, x, y, _SRCCOPY)
        _gdi32.SelectObject(memory, previous)
        if not ok:
            return None
        buffer = (ctypes.c_char * (width * height * 4)).from_address(bits.value)
        return Image.frombuffer(
            "RGB", (width, height), bytes(buffer), "raw", "BGRX", 0, 1
        )
    finally:
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(memory)
        _user32.ReleaseDC(None, screen)
