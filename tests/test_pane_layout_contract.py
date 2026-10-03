"""Contracts for ``ui/pane_layout.py``: building the pane tree in a real Tk.

形（core.pane_arrangement）どおりに欄が並び、隠した欄は消え、何度組み直しても
古い仕切りが残らないことを、実際の位置と表示状態で確かめる。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator

import pytest
from core.pane_arrangement import arrangement_tree
from ui.pane_layout import PaneArranger


class _Areas:
    def __init__(self, window: tk.Toplevel) -> None:
        self.window = window
        self.parent = ttk.Frame(window)
        self.parent.pack(fill="both", expand=True)
        # 欄は Window と同じく仕切りより先に、parent の子として作る。
        self.widgets = {
            "preview": tk.Frame(self.parent, width=320, height=180, bg="black"),
            "tabs": tk.Frame(self.parent, width=320, height=120, bg="gray"),
            "log": tk.Frame(self.parent, width=200, height=120, bg="white"),
        }
        self.arranger = PaneArranger(self.parent, self.widgets)

    def apply(self, arrangement: str, *, tabs: bool = True, log: bool = True) -> None:
        tree = arrangement_tree(arrangement, show_tabs=tabs, show_log=log, stretch=True)
        self.arranger.apply(tree)
        self.window.update_idletasks()
        self.window.update()

    def box(self, name: str) -> tuple[int, int, int, int]:
        w = self.widgets[name]
        return (w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())

    def mapped(self, name: str) -> bool:
        return bool(self.widgets[name].winfo_ismapped())


@pytest.fixture
def areas(tk_root: tk.Tk) -> Iterator[_Areas]:
    window = tk.Toplevel(tk_root)
    window.geometry("900x700+0+0")
    try:
        yield _Areas(window)
    finally:
        window.destroy()


def test_log_right_puts_the_log_to_the_right_of_preview_and_tabs(areas: _Areas) -> None:
    areas.apply("log_right")
    px, py, _pw, ph = areas.box("preview")
    tx, ty, _tw, _th = areas.box("tabs")
    lx, _ly, _lw, _lh = areas.box("log")
    # Then: プレビューの下にタブ、その右にログ。
    assert tx == px and ty >= py + ph
    assert lx > px and lx > tx


def test_side_puts_tabs_over_log_to_the_right_of_the_preview(areas: _Areas) -> None:
    areas.apply("side")
    px, _py, pw, _ph = areas.box("preview")
    tx, ty, _tw, th = areas.box("tabs")
    lx, ly, _lw, _lh = areas.box("log")
    assert tx >= px + pw and lx == tx
    assert ly >= ty + th


def test_stack_puts_the_three_areas_top_to_bottom(areas: _Areas) -> None:
    areas.apply("stack")
    _px, py, _pw, ph = areas.box("preview")
    _tx, ty, _tw, th = areas.box("tabs")
    _lx, ly, _lw, _lh = areas.box("log")
    assert py + ph <= ty and ty + th <= ly


def test_collapsed_areas_disappear_and_come_back(areas: _Areas) -> None:
    # When: the log is collapsed.
    areas.apply("log_right", log=False)
    # Then: 消える。
    assert not areas.mapped("log")
    assert areas.mapped("tabs") and areas.mapped("preview")

    # When: everything but the preview is collapsed.
    areas.apply("log_right", tabs=False, log=False)
    # Then: プレビューだけが親いっぱいに広がる。
    assert not areas.mapped("tabs")
    _x, _y, w, h = areas.box("preview")
    assert w == areas.parent.winfo_width() and h == areas.parent.winfo_height()

    # When: expanded again.
    areas.apply("log_right")
    # Then: 戻る。
    assert areas.mapped("tabs") and areas.mapped("log")


def test_rearranging_many_times_leaves_no_stale_panes(areas: _Areas) -> None:
    for arrangement in ("side", "stack", "log_right") * 3:
        areas.apply(arrangement)

    # Then: 組み直すたびに古い仕切りを壊す（残ると裏に溜まり続ける）。
    panes = [
        child
        for child in areas.parent.winfo_children()
        if isinstance(child, ttk.PanedWindow)
    ]
    assert len(panes) == 2  # log_right は外側 1 + 内側 1
    assert all(areas.mapped(name) for name in ("preview", "tabs", "log"))
