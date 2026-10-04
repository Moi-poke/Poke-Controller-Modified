#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notebook_fit.py - Notebook の高さを開いているタブに合わせる。

ttk.Notebook は全タブの中で一番高いものに高さを合わせる。設定タブ欄では
Bcon タブが高く、低いタブを開いていても欄がスクロールしていた。高さだけを
開いているタブの要求高さで決め、幅は全タブの最大のままにする（タブを
切り替えるたびに横幅が変わると、隣のログ欄やプレビューの並びが揺れる）。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

# 開いているタブの中身が増減したかを見に行く間隔（ms）。
# タブの要求高さが変わっても Tk は外へ知らせない（Notebook の高さを決め打ち
# にすると、タブ自身の大きさも変わらず <Configure> が来ない）ので見に行く。
POLL_MS = 250


class SelectedTabHeight:
    """notebook の height を、開いているタブの要求高さへ合わせ続ける。"""

    def __init__(
        self, notebook: Any, on_change: Callable[[], None] | None = None
    ) -> None:
        """on_change は Notebook の要求サイズが変わった後に呼ぶ（スクロールする
        入れ物にバーの要否を決め直させる。要求サイズだけが変わっても入れ物には
        知らせが来ない）。"""
        self.notebook = notebook
        self._on_change = on_change
        self._last: tuple[int, int] | None = None
        notebook.bind("<<NotebookTabChanged>>", self.sync, add="+")
        self.sync()
        notebook.after(POLL_MS, self._tick)

    def sync(self, _event: Any = None) -> None:
        """今開いているタブの要求高さを Notebook の高さにする。"""
        nb = self.notebook
        try:
            selected = nb.select()
            if not selected:
                return
            height = nb.nametowidget(selected).winfo_reqheight()
            if int(str(nb.cget("height"))) != height:
                nb.configure(height=height)
            # 幅は Bcon タブの出し入れでも変わるので、高さと一緒に見る。
            size = (height, nb.winfo_reqwidth())
        except (tk.TclError, KeyError, ValueError):
            return
        if size != self._last and self._on_change is not None:
            # 並べ直し（Notebook → 親）が済んでから知らせる。
            nb.after_idle(self._notify)
        self._last = size

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except tk.TclError:
            pass

    def _tick(self) -> None:
        try:
            if not self.notebook.winfo_exists():
                return  # 閉じた後は見に行かない。
        except tk.TclError:
            return
        self.sync()
        self.notebook.after(POLL_MS, self._tick)
