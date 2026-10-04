#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""controller_dock.py - 仮想コントローラをタブに埋め込み、別窓へ出し入れする。

別窓だけだと他の窓の後ろに隠れ、探しに行く手間が出る。コントローラタブの中に
常に置き、広く使いたいときは別ウィンドウへ出せるようにする。別窓を閉じたら
タブへ戻す。入力の送り口が 2 つにならないよう、出している場所は常に 1 つ。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Callable
from typing import Any

from ui.controller_pad import ControllerGUI

TITLE = "仮想コントローラ"
POP_TEXT = "別ウィンドウで開く"
BACK_TEXT = "タブに戻す"


class ControllerDock:
    """holder の中に枠を作って仮想コントローラを置き、別窓と行き来させる。"""

    def __init__(self, root: Any, holder: Any, sender: Callable[[], Any]) -> None:
        self.root = root
        self.holder = holder
        # 送り先は押すたびに引く（再接続で差し替わっても古い物を掴まない）。
        self._sender = sender
        self.embedded: ControllerGUI | None = None
        self.floating: ControllerGUI | None = None

        self.frame = ttk.Labelframe(holder)
        # 操作盤の高さだけを取る（タブの残りを空白で埋めない）。
        self.frame.pack(fill="x")
        # 出し入れのボタンは枠の見出しの横に置く。専用の行を作らず、
        # 同じボタンが「開く」と「戻す」を兼ねる（今どちらか文字で分かる）。
        title = ttk.Frame(self.frame)
        ttk.Label(title, text=TITLE).pack(side="left")
        self.pop_button = ttk.Button(title, text=POP_TEXT, command=self.toggle)
        self.pop_button.pack(side="left", padx=(8, 0))
        self.frame.config(labelwidget=title)
        # 別窓で出している間だけ見せる 1 行の案内。操作盤の場所は畳む。
        self._away = ttk.Label(
            self.frame, text="別ウィンドウで表示中", foreground="#5f6670"
        )
        self._pad_area = ttk.Frame(self.frame)
        self._embed()

    def _embed(self) -> None:
        self._away.pack_forget()
        self._pad_area.pack(fill="x", padx=4, pady=4)
        self.pop_button.config(text=POP_TEXT)
        # 送り先は関数のまま渡す。埋め込みは起動時から置きっぱなしなので、
        # 組み立て時の送り先（まだ無い／作り直し前）を掴まないようにする。
        self.embedded = ControllerGUI(self.root, self._sender, container=self._pad_area)

    def toggle(self) -> None:
        """見出しのボタン。タブにあれば別窓へ、別窓にあればタブへ。"""
        if self.floating is None:
            self.pop_out()
        else:
            self.close_floating()

    def pop_out(self) -> None:
        """別ウィンドウで開く。既に開いていれば前に出すだけ。"""
        if self.floating is not None:
            self.floating.focus_force()
            return
        if self.embedded is not None:
            # 押しっぱなしを離してから消す（destroy が解放まで行う）。
            self.embedded.destroy()
            self.embedded = None
        self._pad_area.pack_forget()
        self._away.pack(anchor="w", padx=8, pady=4)
        self.pop_button.config(text=BACK_TEXT)
        self.floating = ControllerGUI(self.root, self._sender)
        self.floating.protocol("WM_DELETE_WINDOW", self.close_floating)

    def close_floating(self) -> None:
        """別ウィンドウを閉じ、タブへ戻す。"""
        if self.floating is None:
            return
        self.floating.destroy()
        self.floating = None
        try:
            self._embed()
        except tk.TclError:
            # 終了の途中でタブが先に消えていれば、戻す先は無い。
            self.embedded = None

    def shutdown(self) -> None:
        """終了時。どちらに出ていても押しっぱなしを離してから消す。"""
        for pad in (self.floating, self.embedded):
            if pad is not None:
                try:
                    pad.destroy()
                except tk.TclError:
                    pass
        self.floating = None
        self.embedded = None
