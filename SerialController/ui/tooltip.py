#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tooltip.py - ボタンなどに説明を出す小さな吹き出し。

上に少し留まったら出し、離れるか押したら消す。すぐ出すと、画面を横切る
だけで吹き出しが点滅して目障りになる。束縛は add="+" で足し、部品の既存の
束縛を上書きしない。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

# 出すまでの待ち時間（ms）。OS の既定（Windows は約 0.5 秒）に合わせる。
DEFAULT_DELAY_MS = 500
# ポインタからのずらし（px）。真下に出すとポインタで文字が隠れる。
_OFFSET = (12, 18)


class Tooltip:
    """1 つの部品に 1 つの吹き出しを付ける。"""

    def __init__(
        self, widget: tk.Misc, text: str, delay_ms: int = DEFAULT_DELAY_MS
    ) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if self.window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_pointerx() + _OFFSET[0]
            y = self.widget.winfo_pointery() + _OFFSET[1]
            window = tk.Toplevel(self.widget)
        except tk.TclError:
            return  # 部品が破棄済み。吹き出しは出さないだけでよい。
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        tk.Label(
            window,
            text=self.text,
            justify="left",
            background="#FFFFE1",
            relief="solid",
            borderwidth=1,
            padx=4,
            pady=2,
        ).pack()
        self.window = window

    def _hide(self, _event: Any = None) -> None:
        self._cancel()
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
