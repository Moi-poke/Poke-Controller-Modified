#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""win32_subclass.py - Tk の窓の処理に割り込んで、Windows のメッセージを見る。

Tk は窓の縁のドラッグに関わるメッセージ（WM_SIZING / WM_ENTERSIZEMOVE など）を
Python へ出さない。SetWindowSubclass で窓の処理を差し替え、元の処理を
済ませた後に見る。Windows 以外では何もしない（install が False を返す）。
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from collections.abc import Callable
from typing import Any

from loguru import logger

WM_NCDESTROY = 0x0082

# (msg, wparam, lparam) -> 返す値。None なら元の処理の値を返す。
Handler = Callable[[int, int, int], int | None]


if sys.platform == "win32":
    from ctypes import wintypes

    _LRESULT = ctypes.c_ssize_t
    _SUBCLASSPROC = ctypes.WINFUNCTYPE(
        _LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        ctypes.c_size_t,
        ctypes.c_size_t,
    )
    _comctl32 = ctypes.WinDLL("comctl32")
    _comctl32.SetWindowSubclass.argtypes = [
        wintypes.HWND,
        _SUBCLASSPROC,
        ctypes.c_size_t,
        ctypes.c_size_t,
    ]
    _comctl32.SetWindowSubclass.restype = wintypes.BOOL
    _comctl32.RemoveWindowSubclass.argtypes = [
        wintypes.HWND,
        _SUBCLASSPROC,
        ctypes.c_size_t,
    ]
    _comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
    _comctl32.DefSubclassProc.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _comctl32.DefSubclassProc.restype = _LRESULT


class WindowSubclass:
    """root の外枠の窓へ割り込む。同じ窓へ複数付けるときは subclass_id を変える。"""

    def __init__(self, root: Any, subclass_id: int, handler: Handler) -> None:
        self._root = root
        self._id = subclass_id
        self._handler = handler
        self._hwnd: int | None = None
        self._proc: Any = None

    @property
    def installed(self) -> bool:
        return self._hwnd is not None

    @property
    def hwnd(self) -> int | None:
        """付けている窓。付けていなければ None。"""
        return self._hwnd

    def install(self) -> bool:
        """割り込みを付ける。付けられなかったら False（落とさない）。"""
        if sys.platform != "win32":
            return False
        if self._hwnd is not None:
            return True
        try:
            hwnd = int(self._root.wm_frame(), 16)
        except (tk.TclError, ValueError) as e:
            logger.warning(f"窓を特定できませんでした: {e!r}")
            return False
        # 呼び出し口は付けている間ずっと持っておく（消えると落ちる）。
        self._proc = _SUBCLASSPROC(self._window_proc)
        if not _comctl32.SetWindowSubclass(hwnd, self._proc, self._id, 0):
            logger.warning("窓の処理を差し替えられませんでした")
            self._proc = None
            return False
        self._hwnd = hwnd
        return True

    def remove(self) -> None:
        if sys.platform != "win32" or self._hwnd is None:
            return
        _comctl32.RemoveWindowSubclass(self._hwnd, self._proc, self._id)
        self._hwnd = None

    def _window_proc(
        self, hwnd: int, msg: int, wparam: int, lparam: int, _id: int, _ref: int
    ) -> int:
        if sys.platform != "win32":
            return 0
        result = int(_comctl32.DefSubclassProc(hwnd, msg, wparam, lparam))
        if msg == WM_NCDESTROY:
            # 窓が消える。外さないと消えた窓を指したまま残る。
            _comctl32.RemoveWindowSubclass(hwnd, self._proc, self._id)
            self._hwnd = None
        try:
            override = self._handler(msg, int(wparam), int(lparam))
        except Exception as e:  # 窓の処理の中で例外を外へ出すと Tk ごと落ちる。
            logger.warning(f"窓のメッセージの処理に失敗しました: {e!r}")
            return result
        return result if override is None else override
