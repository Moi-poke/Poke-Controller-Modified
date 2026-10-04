#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scroll_host.py - 窓が中身より小さいときだけスクロールバーを出す入れ物。

本体のフレーム（inner）を Canvas の窓として載せる。窓が中身の要求サイズより
小さければバーを出して中身を要求サイズのまま見せ、大きければバーを出さずに
中身を窓いっぱいへ伸ばす。後者を守らないと、fit 表示（窓に合わせてプレビューが
伸び縮みする）が効かなくなる。

バーの出し入れは、この入れ物自身の大きさと中身の要求サイズだけで決める。
Canvas の実寸を見ると「バーが出る → Canvas が縮む → 判定が変わる」と循環する。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from typing import Any


class ScrollHost(ttk.Frame):
    """スクロール可能な入れ物。中身は ``inner`` の子として作る。"""

    def __init__(self, master: Any) -> None:
        super().__init__(master)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(
            yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._relayout, add="+")
        self.inner.bind("<Configure>", self._relayout, add="+")

    def relayout(self) -> None:
        """中身の要求サイズが変わったときに呼ぶ。

        中身の大きさはここで決め打ちしているため、要求サイズだけが変わっても
        中身の <Configure> は来ない。変えた側から呼んで決め直させる。
        """
        self._relayout()

    def _relayout(self, _event: Any = None) -> None:
        """バーの要否を決め、中身の大きさとスクロール範囲を合わせる。"""
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return  # まだ配置前。実寸が出てから 1 回で決める。
        req_w, req_h = self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()
        bar_w = self.vbar.winfo_reqwidth()
        bar_h = self.hbar.winfo_reqheight()

        # 片方のバーが出ると、もう片方の判定に使える面積が減る。2 回回せば収束する。
        need_v = need_h = False
        for _ in range(2):
            avail_w = width - (bar_w if need_v else 0)
            avail_h = height - (bar_h if need_h else 0)
            need_v = req_h > height - (bar_h if need_h else 0)
            need_h = req_w > width - (bar_w if need_v else 0)
        avail_w = width - (bar_w if need_v else 0)
        avail_h = height - (bar_h if need_h else 0)

        self._set_bar(self.vbar, need_v, row=0, column=1, sticky="ns")
        self._set_bar(self.hbar, need_h, row=1, column=0, sticky="ew")

        # 大きい窓では中身を窓いっぱいへ、小さい窓では要求のままにする。
        item_w, item_h = max(req_w, avail_w), max(req_h, avail_h)
        self.canvas.itemconfigure(self._window, width=item_w, height=item_h)
        self.canvas.configure(scrollregion=(0, 0, item_w, item_h))
        if not need_v:
            self.canvas.yview_moveto(0.0)
        if not need_h:
            self.canvas.xview_moveto(0.0)

    @staticmethod
    def _set_bar(bar: Any, needed: bool, *, row: int, column: int, sticky: str) -> None:
        if needed:
            bar.grid(row=row, column=column, sticky=sticky)
        else:
            bar.grid_remove()
