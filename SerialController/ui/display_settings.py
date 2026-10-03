#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""display_settings.py - 表示設定のダイアログ（tkinter）。

プレビューの表示モード（固定サイズ／ウィンドウに合わせる）と、固定モードの
ときのプリセットを決める画面。判断そのものは core.display_mode が持ち、ここは
選ぶ・画面変数へ書く・適用を呼ぶだけ。Window 本体は import しない（循環になる）。

一度に1つだけ開いてほしいので、開いているかの判定と参照の保持は呼び出し側
（Menubar）が持つ。ここは渡された値で1つの窓を構成するだけ。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Callable

from core.display_mode import (
    DEFAULT_SHOW_MODE,
    DEFAULT_SHOW_SIZE,
    SHOW_MODES,
    SHOW_SIZES,
)

# モード名のラベル。fit だけ意味が伝わりにくいため 16:9 を保つことを明示する。
_MODE_LABELS: dict[str, str] = {
    "fixed": "固定サイズ",
    "fit": "ウィンドウに合わせる（16:9 を維持）",
}


def _known(value: str, choices: tuple[str, ...], fallback: str) -> str:
    """候補に無い値は既定へ落とす（壊れた設定で起動を止めないため）。"""
    return value if value in choices else fallback


class DisplaySettingsDialog(tk.Toplevel):
    """表示設定。OK／適用の瞬間に ``on_apply(mode, size)`` を呼ぶ。

    適用は即時。ウィンドウの最小サイズと設定の保存は呼び出し側
    （applyDisplaySettings）が行うので、ここでは変数へ書くところまでで
    save を重複させない。
    """

    def __init__(
        self,
        master: tk.Misc,
        mode: str,
        size: str,
        on_apply: Callable[[str, str], None],
    ) -> None:
        super().__init__(master)
        self._on_apply = on_apply
        self.title("表示設定")
        # メインウィンドウのモーダルにはしない（描画は裏で進むので）。
        # 親ウィンドウの上に湧くだけで、最前面には固定しない。
        # master の型は tk.Misc だが transient は Wm しか受けない。渡されるのは
        # メインウィンドウ（root = Tk）だけなので、この1点だけはみ出す。
        self.transient(master)  # type: ignore[call-overload]

        # 渡された値が壊れていても候補表の範囲に収める（画面の嘘を避ける）。
        # 属性名に _var を付けているのは、Misc.size() と衝突するため。
        known_mode = _known(mode, SHOW_MODES, DEFAULT_SHOW_MODE)
        known_size = _known(size, SHOW_SIZES, DEFAULT_SHOW_SIZE)
        self.mode_var = tk.StringVar(value=known_mode)
        self.size_var = tk.StringVar(value=known_size)

        body = ttk.Frame(self, padding=10)
        body.grid(column=0, row=0, sticky="ew")
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="表示モード").grid(column=0, columnspan=2, row=0)
        for row, value in enumerate(SHOW_MODES, start=1):
            ttk.Radiobutton(
                body,
                text=_MODE_LABELS[value],
                variable=self.mode_var,
                value=value,
                command=self._sync_enabled,
            ).grid(column=0, columnspan=2, row=row, sticky="w")

        size_row = len(SHOW_MODES) + 1
        ttk.Label(body, text="固定サイズ").grid(
            column=0, row=size_row, pady=(8, 0), sticky="w"
        )
        self.size_cb = ttk.Combobox(
            body,
            textvariable=self.size_var,
            state="readonly",
            values=list(SHOW_SIZES),
            width=12,
        )
        self.size_cb.grid(column=1, padx="5", pady=(8, 0), row=size_row, sticky="w")

        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.grid(column=0, row=1, sticky="e")
        specs = (
            ("適用", self._apply),
            ("OK", self._ok),
            ("キャンセル", self._cancel),
        )
        for column, (text, command) in enumerate(specs):
            ttk.Button(buttons, text=text, command=command).grid(
                column=column, padx="5", row=0
            )

        self._sync_enabled()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _event: self._cancel(), add="+")

    def _sync_enabled(self) -> None:
        """fit のときは固定サイズ欄を無効化する（選ばない項目は触らせない）。"""
        state = "disabled" if self.mode_var.get() == "fit" else "readonly"
        self.size_cb.config(state=state)

    def _chosen(self) -> tuple[str, str]:
        """いま選んだ (mode, size) を返す。無効化中のサイズもそのまま載せる。"""
        return (self.mode_var.get(), self.size_var.get())

    def _apply(self) -> None:
        """選んだ内容を適用する（窓は閉じない）。"""
        self._on_apply(*self._chosen())

    def _ok(self) -> None:
        """適用してから閉じる。適用の失敗で窓を閉じないまま残さない。"""
        try:
            self._apply()
        finally:
            self.destroy()

    def _cancel(self) -> None:
        """何も適用せず閉じる。この窓の変数は捨てられるので保存しない。"""
        self.destroy()
