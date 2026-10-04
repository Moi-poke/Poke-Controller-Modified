#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""window_aspect.py - 「16:9 に固定」を実際の窓に効かせる。

Windows の Tk（8.6.12 で確認）は wm_aspect を受け付けるが、縁のドラッグには
何も効かない（右の縁を 300px 引くと 1102x450 になった）。Windows では窓の
処理を差し替え（SetWindowSubclass）、ドラッグ中に届く WM_SIZING の枠を
その場で 16:9 に直す。ドラッグを終えてから戻す方式だと、下の縁を引いても
幅基準で元の高さへ戻され、縦に広げられない。

Windows 以外は wm_aspect をそのまま使う（ウィンドウマネージャが従う）。
寸法の計算は core.aspect_lock に置く。
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from typing import Any

from core.aspect_lock import RATIO_H, RATIO_W, locked_rect, snap_client
from loguru import logger

_WM_SIZING = 0x0214
_WM_NCDESTROY = 0x0082
# 差し替えを見分ける番号（同じ窓に別の差し替えがあっても取り違えない）。
_SUBCLASS_ID = 0x16_09


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
    _user32 = ctypes.WinDLL("user32")
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
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


class WindowAspectLock:
    """root の窓を 16:9 に保つ。入/切はセッションの間だけ（ini へは残さない）。"""

    def __init__(self, root: Any) -> None:
        self._root = root
        self._enabled = False
        self._hwnd: int | None = None
        self._proc: Any = None
        # 最小寸法は入れたときに読んでおく。窓の処理の途中で Tk を呼ばない。
        self._minimum = (1, 1)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> bool:
        """固定を入/切する。入れられなかったときは False を返す（落とさない）。"""
        if not enabled:
            self._enabled = False
            self._unhook()
            self._set_wm_aspect(False)
            return False
        try:
            minimum = self._root.wm_minsize()
            self._minimum = (int(minimum[0]), int(minimum[1]))
        except (tk.TclError, TypeError, ValueError, IndexError):
            self._minimum = (1, 1)
        if sys.platform == "win32":
            if not self._hook():
                return False
        elif not self._set_wm_aspect(True):
            return False
        self._enabled = True
        self._snap()
        return True

    def cleanup(self) -> None:
        """終了時。差し替えを戻す（窓が先に消えていても落ちない）。"""
        self._enabled = False
        self._unhook()

    # ------------------------------------------------------------------

    def _snap(self) -> None:
        """入れた瞬間、今の幅を保って高さを 16:9 に合わせる。"""
        try:
            if self._root.state() != "normal":
                return  # 最大化中は窓の大きさを変えない。
            self._root.update_idletasks()
            width, height = self._root.winfo_width(), self._root.winfo_height()
            if width <= 1 or height <= 1:
                return
            target = snap_client(width, height, minimum=self._minimum)
            if target != (width, height):
                self._root.geometry("%dx%d" % target)
        except tk.TclError as e:
            logger.warning(f"16:9 の寸法へ合わせられませんでした: {e!r}")

    def _set_wm_aspect(self, on: bool) -> bool:
        try:
            if on:
                self._root.wm_aspect(RATIO_W, RATIO_H, RATIO_W, RATIO_H)
            else:
                self._root.wm_aspect("", "", "", "")
        except tk.TclError as e:
            logger.warning(f"縦横比の切替に失敗しました: {e!r}")
            return False
        return True

    def _hook(self) -> bool:
        if sys.platform != "win32":
            return False
        if self._hwnd is not None:
            return True
        try:
            hwnd = int(self._root.wm_frame(), 16)
        except (tk.TclError, ValueError) as e:
            logger.warning(f"16:9 固定: 窓を特定できませんでした: {e!r}")
            return False
        # 呼び出し口は差し替えている間ずっと持っておく（消えると落ちる）。
        self._proc = _SUBCLASSPROC(self._window_proc)
        if not _comctl32.SetWindowSubclass(hwnd, self._proc, _SUBCLASS_ID, 0):
            logger.warning("16:9 固定: 窓の処理を差し替えられませんでした")
            self._proc = None
            return False
        self._hwnd = hwnd
        return True

    def _unhook(self) -> None:
        if sys.platform != "win32" or self._hwnd is None:
            return
        _comctl32.RemoveWindowSubclass(self._hwnd, self._proc, _SUBCLASS_ID)
        self._hwnd = None

    def _window_proc(
        self, hwnd: int, msg: int, wparam: int, lparam: int, _id: int, _ref: int
    ) -> int:
        """差し替えた窓の処理。WM_SIZING の枠だけを直し、他は元の処理へ渡す。"""
        if sys.platform != "win32":
            return 0
        result = int(_comctl32.DefSubclassProc(hwnd, msg, wparam, lparam))
        if msg == _WM_NCDESTROY:
            # 窓が消える。差し替えを外す（外さないと消えた窓を指したまま残る）。
            _comctl32.RemoveWindowSubclass(hwnd, self._proc, _SUBCLASS_ID)
            self._hwnd = None
            self._enabled = False
            return result
        if msg != _WM_SIZING or not self._enabled or not lparam:
            return result
        try:
            self._fit_sizing_rect(hwnd, int(wparam), int(lparam))
        except Exception as e:  # 窓の処理の中で例外を外へ出すと Tk ごと落ちる。
            logger.warning(f"16:9 固定の計算に失敗しました: {e!r}")
            return result
        return 1  # 枠を直したことを知らせる（WM_SIZING の約束）。

    def _fit_sizing_rect(self, hwnd: int, edge: int, lparam: int) -> None:
        if sys.platform != "win32":
            return
        outer, inner = wintypes.RECT(), wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(outer))
        _user32.GetClientRect(hwnd, ctypes.byref(inner))
        # 枠（タイトルバー・メニュー・縁）の分。ドラッグの前の今の窓で測る。
        frame = (
            outer.right - outer.left - inner.right,
            outer.bottom - outer.top - inner.bottom,
        )
        rect = wintypes.RECT.from_address(lparam)
        left, top, right, bottom = locked_rect(
            edge,
            (rect.left, rect.top, rect.right, rect.bottom),
            frame,
            minimum=self._minimum,
        )
        rect.left, rect.top, rect.right, rect.bottom = left, top, right, bottom
