"""窓の縁をドラッグしている間は中身を並べ直さない契約。実 Tk・Windows のみ。

Tk の部品はそれぞれが Windows の子窓で、ドラッグの 1 段ごとに全部を動かし
描き直すため、本体の画面では 8fps 前後まで落ちていた（部品を隠すと上がる。
プレビューの描画を止めても変わらない）。ドラッグ中は中身をドラッグ前の
大きさに止めて枠だけを動かし、手を止めたときと離したときに 1 回だけ並べ直す。
止めている間は中身の代わりにドラッグ前の見た目の絵 1 枚を出す（子窓が
残っていると、動かさなくても窓が 1 段広がるたびに子窓の数だけ重い）。
ここでは実際の窓へ WM_ENTERSIZEMOVE / WM_SIZING / WM_EXITSIZEMOVE を送る。
"""

from __future__ import annotations

import ctypes
import os
import time
import tkinter as tk
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="窓のメッセージは Windows だけ")

WM_SIZING = 0x0214
WM_ENTERSIZEMOVE = 0x0231
WM_EXITSIZEMOVE = 0x0232
WMSZ_BOTTOMRIGHT = 8


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _send(win: Any, msg: int, wparam: int = 0, lparam: Any = 0) -> None:
    hwnd = int(win.wm_frame(), 16)
    ctypes.windll.user32.SendMessageW(hwnd, msg, wparam, lparam)  # type: ignore[attr-defined]  # Windows だけ


def _sizing(win: Any) -> None:
    """縁を掴んで動かし始めた（枠は今の大きさのまま）と窓へ送る。"""
    hwnd = int(win.wm_frame(), 16)
    rect = _Rect()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))  # type: ignore[attr-defined]  # Windows だけ
    _send(win, WM_SIZING, WMSZ_BOTTOMRIGHT, ctypes.byref(rect))


@pytest.fixture
def window(tk_root: tk.Tk) -> Iterator[tuple[tk.Toplevel, tk.Frame]]:
    win = tk.Toplevel(tk_root)
    try:
        win.geometry("600x400+100+100")
        content = tk.Frame(win, bg="#334")
        content.pack(expand=True, fill="both", side="top")
        tk.Label(win, text="状態").pack(side="bottom", fill="x", before=content)
        win.update()
        yield win, content
    finally:
        win.destroy()


def _shown(freeze: Any, content: tk.Frame) -> tuple[int, int]:
    """利用者に見えている中身の大きさ（ドラッグ中は代わりの絵の大きさ）。"""
    shown = freeze.stand_in if freeze.stand_in is not None else content
    assert shown.winfo_ismapped()
    return shown.winfo_width(), shown.winfo_height()


def _settle(win: tk.Misc, seconds: float = 0.05) -> None:
    deadline = time.monotonic() + seconds
    while True:
        win.update()
        if time.monotonic() >= deadline:
            return
        time.sleep(0.01)


def test_the_content_keeps_its_size_while_the_edge_is_dragged(
    window: tuple[tk.Toplevel, tk.Frame],
) -> None:
    from ui.live_resize import ResizeFreeze

    win, content = window
    freeze = ResizeFreeze(win, content)
    before = (content.winfo_width(), content.winfo_height())

    # When: a resize drag starts and the window grows under it.
    _send(win, WM_ENTERSIZEMOVE)
    _sizing(win)
    win.geometry("800x500")
    _settle(win)

    # Then: 見えているのはドラッグ前の大きさの絵 1 枚で、部品は動かし直さない
    #       （中身は隠して子窓の数だけ重くなるのを避ける）。
    assert freeze.stand_in is not None
    assert not content.winfo_ismapped()
    assert _shown(freeze, content) == before

    # When: the drag ends.
    _send(win, WM_EXITSIZEMOVE)
    _settle(win)

    # Then: 新しい大きさで 1 回だけ並べ直す（元の置き方へ戻り、絵は消える）。
    assert freeze.stand_in is None
    assert content.winfo_ismapped()
    assert content.winfo_width() == 800
    assert content.winfo_height() > before[1]
    assert content.winfo_manager() == "pack"
    freeze.cleanup()


def test_pausing_mid_drag_lays_the_content_out_for_the_current_size(
    window: tuple[tk.Toplevel, tk.Frame],
) -> None:
    from ui.live_resize import PAUSE_MS, ResizeFreeze

    win, content = window
    freeze = ResizeFreeze(win, content)
    _send(win, WM_ENTERSIZEMOVE)
    _sizing(win)
    win.geometry("800x500")
    _settle(win)

    # When: the pointer stays still for a moment while still holding the edge.
    _settle(win, PAUSE_MS * 2 / 1000)

    # Then: 手を止めたら今の大きさで並べ直す（離すまで待たせない）。
    assert _shown(freeze, content)[0] == 800

    # When: the drag continues.
    _sizing(win)
    win.geometry("900x560")
    _settle(win)

    # Then: また中身を止める。
    assert _shown(freeze, content)[0] == 800
    _send(win, WM_EXITSIZEMOVE)
    _settle(win)
    assert _shown(freeze, content)[0] == 900
    freeze.cleanup()


def test_moving_the_window_without_resizing_leaves_the_layout_alone(
    window: tuple[tk.Toplevel, tk.Frame],
) -> None:
    from ui.live_resize import ResizeFreeze

    win, content = window
    freeze = ResizeFreeze(win, content)

    # When: the title bar is dragged (move only; no WM_SIZING).
    _send(win, WM_ENTERSIZEMOVE)
    win.geometry("+150+120")
    _settle(win)
    _send(win, WM_EXITSIZEMOVE)
    _settle(win)

    # Then: 置き方は変えない。
    assert content.winfo_manager() == "pack"
    freeze.cleanup()


def test_the_status_bar_order_survives_a_drag(
    window: tuple[tk.Toplevel, tk.Frame],
) -> None:
    from ui.live_resize import ResizeFreeze

    win, content = window
    # Given: 中身の後ろにも部品がある（戻すときに末尾へ回すと順が変わる）。
    tk.Label(win, text="下").pack(side="bottom", fill="x")
    win.update()
    order = [str(w) for w in win.pack_slaves()]
    assert order.index(str(content)) < len(order) - 1
    freeze = ResizeFreeze(win, content)

    _send(win, WM_ENTERSIZEMOVE)
    _sizing(win)
    win.geometry("700x450")
    _settle(win)
    _send(win, WM_EXITSIZEMOVE)
    _settle(win)

    # Then: 下の状態表示との並び順は元のまま（戻したら下に潜った、を防ぐ）。
    assert [str(w) for w in win.pack_slaves()] == order
    freeze.cleanup()


def test_closing_the_window_mid_drag_does_not_crash(
    window: tuple[tk.Toplevel, tk.Frame],
    tk_root: tk.Tk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ui import screen_grab
    from ui.live_resize import WATCH_MS, ResizeFreeze

    # Given: 絵を撮れず、中身を出したまま止めている（落ちたのはこの経路）。
    #        下には状態表示がある（本体と同じ並び）。
    monkeypatch.setattr(screen_grab, "grab", lambda *_args: None)
    win, content = window
    freeze = ResizeFreeze(win, content)
    _send(win, WM_ENTERSIZEMOVE)
    _sizing(win)
    win.geometry("800x500")
    _settle(win)
    assert freeze.frozen
    assert freeze.stand_in is None

    # When: the window is closed while the drag still holds the content,
    #       with the watcher already due (it then runs inside the destroy).
    time.sleep(WATCH_MS * 2 / 1000)
    win.destroy()
    _settle(tk_root, WATCH_MS * 4 / 1000)

    # Then: 壊している途中の窓へ並べ直しに行かない（行くと Tk の中で落ちた）。
    freeze.cleanup()
    assert not freeze.frozen
    # 後片付け: Tk はドラッグ中の印をアプリ全体で持つ。終わりを送らずに窓を
    # 壊したので、別の窓へ終わりを送って戻す（残すと後のテストの配置が狂う）。
    _send(tk_root, WM_EXITSIZEMOVE)
    _settle(tk_root)
