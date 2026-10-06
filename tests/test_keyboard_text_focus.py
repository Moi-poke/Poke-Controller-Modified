"""文字入力欄にフォーカスがある間、キーボード操作を Switch へ送らないことの検証。

ログ欄に検索欄を足したことで、打った検索語がコントローラーの操作として
送られる経路ができた。打鍵の門（_kb_window_active）が入力欄で下りることを
実 Tk の部品で確かめる。
"""

from __future__ import annotations

import threading
import tkinter as tk
import tkinter.ttk as ttk
from types import SimpleNamespace

from ui.serial_panel import SerialPanelMixin, _is_text_input


def _host(root: tk.Misc) -> SimpleNamespace:
    host = SimpleNamespace(
        root=root,
        _kb_window_active=threading.Event(),
        serial=SimpleNamespace(keyboard=object()),
    )
    return host


def test_editable_entries_are_text_inputs_but_readonly_ones_are_not(
    tk_root: tk.Tk,
) -> None:
    # Given: 打てる欄・読み取り専用・ボタン
    top = tk.Toplevel(tk_root)
    try:
        entry = ttk.Entry(top)
        readonly = ttk.Combobox(top, state="readonly")
        log_text = tk.Text(top, state="disabled")
        button = ttk.Button(top)
        # Then: 文字を打てる物だけが入力欄
        assert _is_text_input(entry)
        assert not _is_text_input(readonly)
        assert not _is_text_input(log_text)
        assert not _is_text_input(button)
        assert not _is_text_input(None)
    finally:
        top.destroy()


def test_focus_into_search_entry_closes_the_keyboard_gate(tk_root: tk.Tk) -> None:
    top = tk.Toplevel(tk_root)
    try:
        entry = ttk.Entry(top)
        button = ttk.Button(top)
        host = _host(top)
        # When: ボタンにフォーカスが入る
        SerialPanelMixin.onFocusInController(host, SimpleNamespace(widget=button))  # type: ignore[arg-type]
        # Then: 打鍵はコントローラーへ送られる
        assert host._kb_window_active.is_set()
        # When: 検索欄にフォーカスが入る
        SerialPanelMixin.onFocusInController(host, SimpleNamespace(widget=entry))  # type: ignore[arg-type]
        # Then: 打鍵は送られない（検索語が Switch へ届かない）
        assert not host._kb_window_active.is_set()
    finally:
        top.destroy()
