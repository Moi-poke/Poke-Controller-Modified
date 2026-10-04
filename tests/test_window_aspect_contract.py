"""「16:9 に固定」が実際の窓に効く契約。実 Tk・Windows のみ。

Windows の Tk（8.6.12）は wm_aspect を受け付けるが、縁のドラッグには何も
効かない（右の縁を 300px 引くと 1102x450 になった）。ドラッグ中に Windows が
送る WM_SIZING を受け、その場で枠を 16:9 に直す。ここでは実際の窓へ
WM_SIZING を送り、窓の処理が枠を直したかを見る（マウスは動かさない）。
"""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="WM_SIZING は Windows だけ")

WM_SIZING = 0x0214
WMSZ_RIGHT = 2
WMSZ_BOTTOM = 6


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


@pytest.fixture
def window(tk_root: tk.Tk) -> Iterator[tk.Toplevel]:
    win = tk.Toplevel(tk_root)
    try:
        win.geometry("800x450+100+100")
        win.update()
        yield win
    finally:
        win.destroy()


def _user32() -> Any:
    return ctypes.windll.user32  # type: ignore[attr-defined]  # Windows だけ


def _frame_size(win: tk.Toplevel) -> tuple[int, int]:
    """枠（タイトルバー・縁）の分。外形から中身を引いた残り。"""
    hwnd = int(win.wm_frame(), 16)
    outer, inner = _Rect(), _Rect()
    _user32().GetWindowRect(hwnd, ctypes.byref(outer))
    _user32().GetClientRect(hwnd, ctypes.byref(inner))
    return (
        outer.right - outer.left - inner.right,
        outer.bottom - outer.top - inner.bottom,
    )


def _send_sizing(win: tk.Toplevel, edge: int, client_w: int, client_h: int) -> _Rect:
    """縁をドラッグして中身が client_w x client_h になりかけた、と窓へ送る。"""
    hwnd = int(win.wm_frame(), 16)
    outer = _Rect()
    _user32().GetWindowRect(hwnd, ctypes.byref(outer))
    frame_w, frame_h = _frame_size(win)
    rect = _Rect(
        outer.left,
        outer.top,
        outer.left + client_w + frame_w,
        outer.top + client_h + frame_h,
    )
    _user32().SendMessageW(hwnd, WM_SIZING, edge, ctypes.byref(rect))
    return rect


def _client_of(win: tk.Toplevel, rect: _Rect) -> tuple[int, int]:
    frame_w, frame_h = _frame_size(win)
    return rect.right - rect.left - frame_w, rect.bottom - rect.top - frame_h


def test_a_locked_window_stays_16_to_9_while_its_edge_is_dragged(
    window: tk.Toplevel,
) -> None:
    from ui.window_aspect import WindowAspectLock

    lock = WindowAspectLock(window)
    assert lock.set_enabled(True)

    # When: the right edge is dragged to 1100x450.
    rect = _send_sizing(window, WMSZ_RIGHT, 1100, 450)

    # Then: 枠は 16:9 に直される（横に引いた分、下へ伸びる）。
    width, height = _client_of(window, rect)
    assert width == 1100
    assert abs(width * 9 - height * 16) <= 16

    # When: the bottom edge is dragged instead.
    rect = _send_sizing(window, WMSZ_BOTTOM, 800, 630)

    # Then: 高さに合わせて幅が広がる（下の縁のドラッグも効く）。
    assert _client_of(window, rect) == (1120, 630)
    lock.cleanup()


def test_an_unlocked_window_resizes_freely(window: tk.Toplevel) -> None:
    from ui.window_aspect import WindowAspectLock

    lock = WindowAspectLock(window)
    lock.set_enabled(True)
    lock.set_enabled(False)

    # When: the right edge is dragged to a non-16:9 size.
    rect = _send_sizing(window, WMSZ_RIGHT, 1100, 450)

    # Then: 解除した後は手を加えない。
    assert _client_of(window, rect) == (1100, 450)
    lock.cleanup()


def test_turning_the_lock_on_makes_the_current_window_16_to_9(
    window: tk.Toplevel,
) -> None:
    from ui.window_aspect import WindowAspectLock

    window.geometry("1000x700")
    window.update()
    lock = WindowAspectLock(window)

    # When: the lock is turned on.
    lock.set_enabled(True)
    window.update()

    # Then: 今の幅を保って高さを 16:9 に合わせる。
    assert (window.winfo_width(), window.winfo_height()) == (1000, 562)
    lock.cleanup()


def test_closing_a_locked_window_is_safe(tk_root: tk.Tk) -> None:
    from ui.window_aspect import WindowAspectLock

    win = tk.Toplevel(tk_root)
    win.geometry("800x450")
    win.update()
    lock = WindowAspectLock(win)
    lock.set_enabled(True)

    # When: the window is closed while locked.
    win.destroy()
    tk_root.update()

    # Then: 後片付けしても落ちない（窓の処理の差し替えを戻す先が無くても）。
    lock.cleanup()
    assert not lock.enabled
