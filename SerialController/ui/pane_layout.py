#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pane_layout.py - 配置の形どおりに ttk.PanedWindow を組み立てる。

形（どの欄をどの向きに入れ子にするか）は core.pane_arrangement が決める。
ここはそれを作るだけ。仕切りはドラッグで動かせる。

欄（プレビュー・設定タブ・ログ）は作り直さず、仕切りだけを作り直す。欄の
中身（カメラや実行中の状態）を壊さずに配置を変えられるようにするため。
そのため仕切りも欄も同じ親（frame_1）の子にする。Tk の幾何管理は「管理する側が、
される側の親か、その子孫」であれば良いので、入れ子の仕切りも兄弟で組める。
ただし兄弟の重なり順は作った順なので、先に作った欄は仕切りの裏に隠れる。
組み立ての最後に欄を前へ出す（lift）。
"""

from __future__ import annotations

import tkinter.ttk as ttk
from typing import Any

from core.pane_arrangement import Leaf, Node, Split, describe

# 仕切りの見た目。Windows 標準（vista）テーマは仕切りを地の色で描くので、
# どこを掴めばよいか分からない。色は窓の地（#F0F0F0）と 3.13:1（非テキスト
# 要素のコントラストの目安 3:1 以上）、太さは掴みやすい 6px にする。
# 仕切り（Sash）の太さはテーマ全体の設定なので、ログ欄の上下の仕切りも揃う。
STYLE = "PokeCon.TPanedwindow"
SASH_COLOR = "#7d8996"
SASH_THICKNESS = 6


class PaneArranger:
    """1 つの親の中で、欄を形どおりに並べ直す。"""

    def __init__(self, parent: Any, widgets: dict[str, Any]) -> None:
        self.parent = parent
        self.widgets = widgets
        self._panes: list[ttk.PanedWindow] = []
        # 仕切りごとの形（例: "V[preview,tabs]"）。位置を覚えるときの鍵。
        self._keys: dict[str, str] = {}
        # 形ごとの仕切り位置（全長に対する割合）。組み直しても前の位置へ戻す。
        self.sash_ratios: dict[str, list[float]] = {}
        self.root_pane: ttk.PanedWindow | None = None
        self._tree: Node | None = None
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        style = ttk.Style(parent)
        style.configure(STYLE, background=SASH_COLOR)
        style.configure("Sash", sashthickness=SASH_THICKNESS)

    def apply(self, tree: Node) -> None:
        """今の並びを捨てて、形どおりに並べ直す。"""
        self._tree = tree
        self._clear()
        top = self._build(tree)
        top.grid(in_=self.parent, row=0, column=0, sticky="nsew")
        for widget in self.widgets.values():
            widget.lift()
        self._restore_sashes()

    def _build(self, node: Node) -> Any:
        if isinstance(node, Leaf):
            return self.widgets[node.name]
        assert isinstance(node, Split)
        pane = ttk.PanedWindow(self.parent, orient=node.orient, style=STYLE)
        self._panes.append(pane)
        self._keys[str(pane)] = describe(node)
        if self.root_pane is None:
            self.root_pane = pane
        for child in node.children:
            widget = self._build(child)
            pane.add(widget, weight=child.weight)
        return pane

    def _clear(self) -> None:
        """欄を仕切りから外し、古い仕切りを壊す。欄そのものは壊さない。"""
        self.remember_sashes()
        for pane in self._panes:
            for slave in pane.panes():
                pane.forget(slave)
        for widget in self.widgets.values():
            widget.grid_forget()
        for pane in self._panes:
            pane.destroy()
        self._panes = []
        self._keys = {}
        self.root_pane = None

    def reset_sashes(self) -> None:
        """仕切りを既定の位置へ戻す（ドラッグできない人のための代わりの手段）。"""
        self._panes_forget_positions()
        if self._tree is not None:
            self.apply(self._tree)

    def _panes_forget_positions(self) -> None:
        # 組み直しの前に控えると今の位置が残るので、先に仕切りを壊してから捨てる。
        self._clear()
        self.sash_ratios = {}

    @staticmethod
    def _length(pane: ttk.PanedWindow) -> int:
        horizontal = str(pane.cget("orient")) == "horizontal"
        return int(pane.winfo_width() if horizontal else pane.winfo_height())

    def remember_sashes(self) -> None:
        """今の仕切り位置を、形ごとに割合で控える（ドラッグ後にも呼ぶ）。"""
        for pane in self._panes:
            length = self._length(pane)
            count = len(pane.panes()) - 1
            if length <= 1 or count <= 0:
                continue  # まだ実寸が無い。0 割りや嘘の値を控えない。
            self.sash_ratios[self._keys[str(pane)]] = [
                pane.sashpos(i) / length for i in range(count)
            ]

    def _restore_sashes(self) -> None:
        """控えてある形なら、前の割合で仕切りを置き直す。"""
        if not any(self._keys[str(p)] in self.sash_ratios for p in self._panes):
            return
        # 実寸が決まらないと sashpos は効かない。ここで一度だけ寸法を確定させる。
        self.parent.update_idletasks()
        for pane in self._panes:
            ratios = self.sash_ratios.get(self._keys[str(pane)])
            length = self._length(pane)
            if not ratios or length <= 1 or len(ratios) != len(pane.panes()) - 1:
                continue
            for index, ratio in enumerate(ratios):
                pane.sashpos(index, int(length * ratio))
