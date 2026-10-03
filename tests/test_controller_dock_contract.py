"""仮想コントローラをメイン画面（コントローラタブ）に埋め込む契約。実 Tk。

別窓しか無いと、窓の後ろに隠れて探しに行く手間が出る。タブの中に常に置き、
必要なら別ウィンドウへ出せる。押しっぱなしの解放（閉じたら必ず離す）は
埋め込みでも別窓でも同じに守る。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator
from typing import Any

import pytest
from GuiAssets import ControllerGUI
from ui.controller_dock import ControllerDock


class _Sender:
    """送り先の偽物。押した・離したの記録だけ取る。"""

    def __init__(self) -> None:
        self.pressed: list[int] = []
        self.released: list[int] = []

    def isOpened(self) -> bool:
        return True

    def pressButtons(self, buttons: list[int], source: str = "") -> bool:
        self.pressed.extend(buttons)
        return True

    def releaseButtons(self, buttons: list[int], source: str = "") -> bool:
        self.released.extend(buttons)
        return True

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: True


@pytest.fixture
def area(tk_root: tk.Tk) -> Iterator[tk.Toplevel]:
    window = tk.Toplevel(tk_root)
    try:
        yield window
    finally:
        window.destroy()


def _placement(dock: ControllerDock) -> str:
    """出している場所（mypy の型の絞り込みを避けるため関数で読む）。"""
    if dock.embedded is not None and dock.floating is None:
        return "embedded"
    if dock.embedded is None and dock.floating is not None:
        return "floating"
    return "broken"


def _toplevels(root: tk.Misc) -> list[tk.Misc]:
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]


def test_a_container_builds_the_controller_inside_it_without_a_new_window(
    area: tk.Toplevel,
) -> None:
    holder = tk.Frame(area)
    holder.pack()
    before = len(_toplevels(area))

    pad = ControllerGUI(area, _Sender(), container=holder)

    # Then: 新しい窓は開かず、渡した枠の中にボタンが並ぶ。
    assert len(_toplevels(area)) == before
    assert pad.window.master is holder
    assert pad._buttons


def test_controller_buttons_never_take_keyboard_focus(area: tk.Toplevel) -> None:
    pad = ControllerGUI(area, _Sender(), container=tk.Frame(area))

    # Then: キーボード操作中に Space でボタンが押されて入力が飛ばないよう、
    #       ボタンはフォーカスを取らない。
    assert all(str(b.cget("takefocus")) == "0" for b in pad._buttons.values())


def test_destroying_an_embedded_controller_releases_held_buttons(
    area: tk.Toplevel,
) -> None:
    sender = _Sender()
    pad = ControllerGUI(area, sender, container=tk.Frame(area))
    pad._onPress("A")
    assert sender.pressed

    # When: the embedded controller is torn down (e.g. popped out).
    pad.destroy()

    # Then: 押しっぱなしは必ず離してから消える（Switch 側に残らない）。
    assert sender.released == sender.pressed


def test_the_dock_pops_out_to_a_window_and_returns_when_it_closes(
    area: tk.Toplevel,
) -> None:
    holder = tk.Frame(area)
    holder.pack()
    dock = ControllerDock(area, holder, lambda: _Sender())

    # Given: 最初はタブの中に埋め込まれている。
    assert _placement(dock) == "embedded"

    # When: popped out.
    dock.pop_out()
    area.update()

    # Then: 別窓に移り、タブには「戻す」案内だけが残る。
    assert _placement(dock) == "floating"
    assert isinstance(getattr(dock.floating, "window", None), tk.Toplevel)

    # When: the floating window is closed.
    dock.close_floating()
    area.update()

    # Then: タブへ戻る。
    assert _placement(dock) == "embedded"


def test_popping_out_twice_just_raises_the_existing_window(area: tk.Toplevel) -> None:
    holder = tk.Frame(area)
    dock = ControllerDock(area, holder, lambda: _Sender())
    dock.pop_out()
    first = dock.floating

    dock.pop_out()

    # Then: 2 つ目は開かない（同じ入力を 2 箇所から送らない）。
    assert dock.floating is first
    dock.close_floating()
