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
from ui.win32_subclass import WM_NCDESTROY, WindowSubclass

_WM_SIZING = 0x0214
# 差し替えを見分ける番号（同じ窓に別の差し替えがあっても取り違えない）。
_SUBCLASS_ID = 0x16_09


if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32")
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]


class WindowAspectLock:
    """root の窓を 16:9 に保つ。入/切はセッションの間だけ（ini へは残さない）。"""

    def __init__(self, root: Any) -> None:
        self._root = root
        self._enabled = False
        self._subclass = WindowSubclass(root, _SUBCLASS_ID, self._on_message)
        # 最小寸法は入れたときに読んでおく。窓の処理の途中で Tk を呼ばない。
        self._minimum = (1, 1)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> bool:
        """固定を入/切する。入れられなかったときは False を返す（落とさない）。"""
        if not enabled:
            self._enabled = False
            self._subclass.remove()
            self._set_wm_aspect(False)
            return False
        try:
            minimum = self._root.wm_minsize()
            self._minimum = (int(minimum[0]), int(minimum[1]))
        except (tk.TclError, TypeError, ValueError, IndexError):
            self._minimum = (1, 1)
        if sys.platform == "win32":
            if not self._subclass.install():
                return False
        elif not self._set_wm_aspect(True):
            return False
        self._enabled = True
        self._snap()
        return True

    def cleanup(self) -> None:
        """終了時。差し替えを戻す（窓が先に消えていても落ちない）。"""
        self._enabled = False
        self._subclass.remove()

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

    def _on_message(self, msg: int, wparam: int, lparam: int) -> int | None:
        """WM_SIZING の枠だけを直す。他は元の処理の値のまま。"""
        if msg == WM_NCDESTROY:
            self._enabled = False
            return None
        if msg != _WM_SIZING or not self._enabled or not lparam:
            return None
        self._fit_sizing_rect(wparam, lparam)
        return 1  # 枠を直したことを知らせる（WM_SIZING の約束）。

    def _fit_sizing_rect(self, edge: int, lparam: int) -> None:
        hwnd = self._subclass.hwnd
        if sys.platform != "win32" or hwnd is None:
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
