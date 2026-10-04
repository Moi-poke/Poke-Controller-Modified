"""コマンドタブの並びの契約。実 Tk。

コマンドのコンボボックスを Notebook のページそのものにしていたため、ページの
広さいっぱいに縦横へ引き伸ばされ、1 行の選択欄が大きな箱になっていた。
ページは枠にして、選択欄は 1 行の高さのまま置く。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator
from typing import Any

import pytest
from ui.command_panel import CommandPanelMixin


class _Host(CommandPanelMixin):
    """組み立てに要る口だけを持つ偽物。処理の入口は何もしない関数で返す。"""

    def __init__(self, window: tk.Toplevel) -> None:
        self.root = window
        self.tab_command = ttk.Frame(window)
        self.tab_command.pack(fill="both", expand=True)
        self.open_folder_img = tk.PhotoImage(master=window, width=16, height=16)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


@pytest.fixture
def host(tk_root: tk.Tk) -> Iterator[_Host]:
    window = tk.Toplevel(tk_root)
    try:
        h = _Host(window)
        h._build_command_frame()
        # タブ欄が広い・高い場合（ログを右に置いた標準配置）。
        window.geometry("900x480")
        window.update()
        yield h
    finally:
        window.destroy()


def test_the_command_choosers_keep_a_single_line_height(host: _Host) -> None:
    # Then: 選択欄は 1 行の高さ（検索欄と同じ程度）。ページの広さへ引き伸ばさない。
    line = host.search_entry.winfo_height()
    for combo in (host.py_cb, host.mcu_cb):
        assert combo.winfo_reqheight() <= line * 1.5
    assert host.py_cb.winfo_height() <= line * 1.5


def test_the_run_buttons_sit_right_under_the_command_chooser(host: _Host) -> None:
    # Then: 選ぶ→開始 が近くに並ぶ（間に大きな空白を挟まない）。
    chooser_bottom = host.py_cb.winfo_rooty() + host.py_cb.winfo_height()
    gap = host.startButton.winfo_rooty() - chooser_bottom
    assert 0 <= gap <= host.search_entry.winfo_height() * 2


def test_the_command_chooser_still_spans_the_width(host: _Host) -> None:
    # Then: 長いコマンド名が切れないよう、横は欄いっぱいに使う。
    assert host.py_cb.winfo_width() >= host.Command_nb.winfo_width() * 0.8
