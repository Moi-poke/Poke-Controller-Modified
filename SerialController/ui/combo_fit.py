#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""combo_fit.py - コンボボックスの幅を候補の長さに合わせる。

固定の文字数で幅を決めると、USB シリアルの名前（'COM3: USB Serial Port
(COM3)'）が切れたり、逆に短い候補しか無い欄がタブを無駄に押し広げたりする。
候補を実際の書体で測って最小幅を決め、上限で頭打ちにする（長い機器名は
伸びる列で見せる）。幅は ttk の width と同じ「'0' の文字数」で指定する。
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
import tkinter.ttk as ttk
from typing import Any


def combo_font(combo: Any) -> tkfont.Font:
    """コンボボックスが文字を描く書体。テーマに指定が無ければ既定の書体。"""
    name = ""
    try:
        name = str(ttk.Style(combo).lookup("TCombobox", "font"))
    except tk.TclError:
        pass
    try:
        return tkfont.nametofont(name or "TkDefaultFont", root=combo)
    except tk.TclError:
        return tkfont.nametofont("TkDefaultFont", root=combo)


def fit_combo_width(combo: Any, *, min_chars: int, max_chars: int) -> int:
    """一番長い候補（と今の値）が入る幅にする。決めた文字数を返す。"""
    try:
        values = [str(v) for v in combo.tk.splitlist(combo.cget("values"))]
        values.append(str(combo.get()))
        font = combo_font(combo)
        zero = max(1, int(font.measure("0")))
        longest = max((int(font.measure(v)) for v in values), default=0)
    except (tk.TclError, AttributeError, TypeError, ValueError):
        # 幅合わせは見た目だけの処理。測れないときは今の幅のまま進める。
        return int(min_chars)
    # 端の余白で最後の 1 文字が欠けないよう 1 文字分足す。
    chars = math.ceil(longest / zero) + 1
    chars = max(min_chars, min(max_chars, chars))
    combo.configure(width=chars)
    return chars
