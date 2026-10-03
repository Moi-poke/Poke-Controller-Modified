"""Contracts for ``ui/scroll_host.py`` against a real Tk tree.

窓が中身の要求サイズより小さくなったとき、部品が切れて操作できなくなる代わりに
スクロールバーで届くようにする。逆に窓が十分大きいときは、従来どおり中身が
窓いっぱいに広がり、バーは出ない（fit 表示の伸び縮みを邪魔しない）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator

import pytest
from ui.scroll_host import ScrollHost

_REQ_W = 600
_REQ_H = 400


@pytest.fixture
def host(tk_root: tk.Tk) -> Iterator[ScrollHost]:
    window = tk.Toplevel(tk_root)
    scroll = ScrollHost(window)
    scroll.pack(fill="both", expand=True)
    # 中身は 600x400 を要求する。grid の子が要求を作る本物の構成に近づける。
    body = ttk.Frame(scroll.inner, width=_REQ_W, height=_REQ_H)
    body.grid(row=0, column=0, sticky="nsew")
    scroll.inner.columnconfigure(0, weight=1)
    scroll.inner.rowconfigure(0, weight=1)
    scroll.inner.grid_propagate(False)
    scroll.inner.config(width=_REQ_W, height=_REQ_H)
    try:
        yield scroll
    finally:
        window.destroy()


def _resize(host: ScrollHost, geometry: str) -> None:
    window = host.winfo_toplevel()
    window.geometry(geometry)
    window.update_idletasks()
    window.update()


def test_a_window_smaller_than_the_content_shows_both_scrollbars(
    host: ScrollHost,
) -> None:
    # When: the window is smaller than the 600x400 the content asks for.
    _resize(host, "300x200+0+0")

    # Then: 縦横ともバーが出て、中身は要求どおりの大きさのまま（縮まない）。
    assert host.vbar.winfo_ismapped()
    assert host.hbar.winfo_ismapped()
    assert host.inner.winfo_width() == _REQ_W
    assert host.inner.winfo_height() == _REQ_H


def test_scrolling_moves_the_content_so_the_hidden_part_can_be_reached(
    host: ScrollHost,
) -> None:
    # Given: a window too short for the content.
    _resize(host, "300x200+0+0")
    assert host.inner.winfo_rooty() >= host.winfo_rooty()

    # When: scrolled to the bottom.
    host.canvas.yview_moveto(1.0)
    host.winfo_toplevel().update_idletasks()

    # Then: 中身の上端が窓の上へ出て、下端が見える位置に来る。
    bottom = host.inner.winfo_rooty() + host.inner.winfo_height()
    assert host.inner.winfo_rooty() < host.winfo_rooty()
    assert bottom <= host.winfo_rooty() + host.canvas.winfo_height() + 1


def test_a_window_larger_than_the_content_stretches_it_without_scrollbars(
    host: ScrollHost,
) -> None:
    # When: the window is larger than the content request.
    _resize(host, "900x700+0+0")

    # Then: バーは出ず、中身は窓いっぱいに伸びる（fit 表示が効く）。
    assert not host.vbar.winfo_ismapped()
    assert not host.hbar.winfo_ismapped()
    assert host.inner.winfo_width() == host.canvas.winfo_width()
    assert host.inner.winfo_height() == host.canvas.winfo_height()


def test_shrinking_then_growing_removes_the_scrollbars_again(
    host: ScrollHost,
) -> None:
    # Given: scrollbars shown at a small size.
    _resize(host, "300x200+0+0")
    assert host.vbar.winfo_ismapped()

    # When: the window grows past the content request.
    _resize(host, "900x700+0+0")

    # Then: バーは消え、スクロール位置も先頭へ戻る（切れたまま残らない）。
    assert not host.vbar.winfo_ismapped()
    assert host.inner.winfo_rooty() == host.winfo_rooty()
