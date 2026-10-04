"""設定タブ欄の高さを、開いているタブの高さで決める契約。実 Tk。

ttk.Notebook は全タブの中で一番高いもの（Bcon タブ）に高さを合わせるため、
低いタブを開いていても欄がスクロールし、縦のスクロールバーが出ていた。
高さは開いているタブに合わせ、幅はこれまでどおり全タブの最大にする
（タブを切り替えるたびに横幅が変わって並びが揺れないように）。
"""

from __future__ import annotations

import time
import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from ui.notebook_fit import POLL_MS, SelectedTabHeight
from ui.scroll_host import ScrollHost


@dataclass
class _Area:
    window: tk.Toplevel
    host: ScrollHost
    nb: ttk.Notebook
    short: ttk.Frame
    tall: ttk.Frame
    wide: ttk.Frame


@pytest.fixture
def area(tk_root: tk.Tk) -> Iterator[_Area]:
    window = tk.Toplevel(tk_root)
    try:
        window.geometry("500x300")
        host = ScrollHost(window)
        host.pack(fill="both", expand=True)
        host.inner.rowconfigure(0, weight=1)
        host.inner.columnconfigure(0, weight=1)
        nb = ttk.Notebook(host.inner)
        nb.grid(row=0, column=0, sticky="nsew")
        short = ttk.Frame(nb)
        tk.Frame(short, width=100, height=80).pack()
        tall = ttk.Frame(nb)
        tk.Frame(tall, width=100, height=700).pack()
        wide = ttk.Frame(nb)
        tk.Frame(wide, width=420, height=60).pack()
        for tab, text in ((short, "低い"), (tall, "高い"), (wide, "広い")):
            nb.add(tab, text=text)
        SelectedTabHeight(nb, on_change=host.relayout)
        window.update()
        yield _Area(window, host, nb, short, tall, wide)
    finally:
        window.destroy()


def _settle(area: _Area) -> None:
    """見に行く間隔の 2 回分、画面の処理を回す（中身の増減を拾わせる）。"""
    deadline = time.monotonic() + POLL_MS * 2 / 1000
    while time.monotonic() < deadline:
        area.window.update()
        time.sleep(0.02)


def test_a_short_tab_does_not_scroll_because_another_tab_is_tall(
    area: _Area,
) -> None:
    # When: the short tab is open.
    area.nb.select(area.short)
    _settle(area)

    # Then: 一番高いタブに引きずられず、縦のスクロールバーは出ない。
    assert area.nb.winfo_reqheight() < 300
    assert not area.host.vbar.winfo_ismapped()

    # When: the tall tab is opened.
    area.nb.select(area.tall)
    _settle(area)

    # Then: そのタブが収まらないときだけスクロールバーが出る。
    assert area.nb.winfo_reqheight() >= 700
    assert area.host.vbar.winfo_ismapped()

    # When: back to the short tab.
    area.nb.select(area.short)
    _settle(area)

    # Then: また消える。
    assert not area.host.vbar.winfo_ismapped()


def test_the_width_still_fits_the_widest_tab_whichever_is_open(area: _Area) -> None:
    widths = set()
    for tab in (area.short, area.tall, area.wide):
        area.nb.select(tab)
        _settle(area)
        widths.add(area.nb.winfo_reqwidth())

    # Then: 横幅は全タブの最大のまま（切り替えで並びが横に揺れない）。
    assert len(widths) == 1
    assert widths.pop() >= 420


def test_the_height_follows_the_open_tab_when_its_content_grows(
    area: _Area,
) -> None:
    area.nb.select(area.short)
    _settle(area)
    before = area.nb.winfo_reqheight()

    # When: the open tab gains content (e.g. Bcon rows appear on the serial tab).
    tk.Frame(area.short, width=100, height=150).pack()
    _settle(area)

    # Then: 増えた分だけ欄も高くなる（中身が切れたままにしない）。
    assert area.nb.winfo_reqheight() >= before + 150
