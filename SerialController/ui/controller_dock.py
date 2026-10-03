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

from GuiAssets import ControllerGUI


class ControllerDock:
    """タブの枠（holder）に仮想コントローラを置き、別窓と行き来させる。"""

    def __init__(self, root: Any, holder: Any, sender: Callable[[], Any]) -> None:
        self.root = root
        self.holder = holder
        # 送り先は開くたびに引く（再接続で差し替わっても古い物を掴まない）。
        self._sender = sender
        self.embedded: ControllerGUI | None = None
        self.floating: ControllerGUI | None = None

        bar = ttk.Frame(holder)
        bar.pack(fill="x", anchor="nw")
        self.pop_button = ttk.Button(
            bar, text="別ウィンドウで開く", command=self.pop_out
        )
        self.pop_button.pack(side="left")
        # 別窓で出している間だけ見せる案内と戻り道。
        self._away = ttk.Frame(holder)
        ttk.Label(self._away, text="別ウィンドウで表示中").pack(side="left")
        ttk.Button(self._away, text="ここに戻す", command=self.close_floating).pack(
            side="left", padx=8
        )
        self._pad_area = ttk.Frame(holder)
        self._pad_area.pack(fill="both", expand=True, anchor="nw", pady=(4, 0))
        self._embed()

    def _embed(self) -> None:
        self._away.pack_forget()
        self.pop_button.state(["!disabled"])
        self.embedded = ControllerGUI(
            self.root, self._sender(), container=self._pad_area
        )

    def pop_out(self) -> None:
        """別ウィンドウで開く。既に開いていれば前に出すだけ。"""
        if self.floating is not None:
            self.floating.focus_force()
            return
        if self.embedded is not None:
            # 押しっぱなしを離してから消す（destroy が解放まで行う）。
            self.embedded.destroy()
            self.embedded = None
        self.pop_button.state(["disabled"])
        self._away.pack(fill="x", anchor="nw", before=self._pad_area)
        self.floating = ControllerGUI(self.root, self._sender())
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
