"""Bcon タブの並べ方の契約。実 Tk。

ボタン 17 個が見出しなしで並び、「W0無線」「X破棄」「K鍵削除」のような
符号付きの文言で、取り消せない操作（鍵削除・BOOTSEL）が普段の操作の
隣にあった。見出し付きのまとまりに分け、取り消せない操作は別のまとまりへ
離し、文言は何が起きるかで書く。
"""

from __future__ import annotations

import re
import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator
from typing import Any

import pytest

# 取り消せない操作。押し間違えないよう普段の操作と別のまとまりに置く。
_DESTRUCTIVE = ("_btn_keys", "_btn_clear", "_btn_bootsel")


def _buttons(setup: Any) -> dict[str, ttk.Button]:
    return {
        name: value
        for name, value in vars(setup).items()
        if name.startswith("_btn_") and isinstance(value, ttk.Button)
    }


def _group(widget: tk.Misc) -> ttk.Labelframe | None:
    """部品を包む一番内側の見出し付きまとまり。無ければ None。"""
    parent: Any = widget.master
    while parent is not None:
        if isinstance(parent, ttk.Labelframe):
            return parent
        parent = parent.master
    return None


@pytest.fixture(params=[True, False], ids=["tab", "window"])
def bcon(request: Any, tk_root: tk.Tk) -> Iterator[Any]:
    from BconSetup import BconSetup

    window = tk.Toplevel(tk_root)
    if request.param:
        tab = ttk.Frame(window)
        tab.pack(fill="both", expand=True)
        setup = BconSetup(tab, None, embedded=True, sender_provider=lambda: None)
    else:
        setup = BconSetup(window, None)
    try:
        window.update()
        yield setup
    finally:
        setup.close()
        window.destroy()


def test_every_bcon_action_button_sits_inside_a_titled_group(bcon: Any) -> None:
    # Given: タブ（埋め込み）と別窓の両方で組み立てた Bcon 設定。
    buttons = _buttons(bcon)
    assert len(buttons) >= 17

    # Then: どのボタンも見出し付きのまとまりの中にある（何の操作か分かる）。
    for name, button in buttons.items():
        group = _group(button)
        assert group is not None, name
        assert str(group.cget("text")).strip(), name


def test_irreversible_bcon_actions_are_kept_apart_from_everyday_ones(
    bcon: Any,
) -> None:
    buttons = _buttons(bcon)

    # Then: 取り消せない操作は 1 つのまとまりに集まり、
    destructive = {str(_group(buttons[name])) for name in _DESTRUCTIVE}
    assert len(destructive) == 1

    # Then: そのまとまりには普段の操作が混ざらない。
    for name, button in buttons.items():
        if name not in _DESTRUCTIVE:
            assert str(_group(button)) not in destructive, name


def test_bcon_buttons_say_what_they_do_instead_of_short_codes(bcon: Any) -> None:
    # Then: 「W0」「E1」「X破棄」「K鍵削除」のような符号を先頭に付けない。
    for name, button in _buttons(bcon).items():
        text = str(button.cget("text"))
        assert not re.match(r"^[WEXK]\d?(?![a-z])", text), (name, text)


def test_the_reconnect_button_is_disabled_while_another_action_runs(
    bcon: Any,
) -> None:
    # When: an action is running (作業スレッドの間は二重起動させない).
    bcon._set_busy(True)

    # Then: 再接続を含め全ボタンが押せない。
    for name, button in _buttons(bcon).items():
        assert str(button.cget("state")) == tk.DISABLED, name

    # When: the action finishes.
    bcon._set_busy(False)

    # Then: 全ボタンが戻る。
    for name, button in _buttons(bcon).items():
        assert str(button.cget("state")) == tk.NORMAL, name


def test_the_bcon_log_has_a_scrollbar(bcon: Any) -> None:
    # Then: ログ欄はスクロールバーで遡れる（1000 行まで溜まる）。
    command = str(bcon._log.cget("yscrollcommand"))
    assert command
    scrollbars = [
        child
        for child in bcon._log.master.winfo_children()
        if isinstance(child, ttk.Scrollbar)
    ]
    assert scrollbars
