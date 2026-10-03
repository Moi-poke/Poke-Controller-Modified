"""Contracts for ``ui/tooltip.py`` on a real Tk tree.

ボタンの上に少し留まると説明（とショートカット）が出て、離れるか押すと消える。
すぐ出すと画面を横切るだけで点滅するので、少し待ってから出す。
"""

from __future__ import annotations

import time
import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator

import pytest
from ui.tooltip import Tooltip


@pytest.fixture
def button(tk_root: tk.Tk) -> Iterator[ttk.Button]:
    window = tk.Toplevel(tk_root)
    window.geometry("200x100+0+0")
    widget = ttk.Button(window, text="開始")
    widget.pack()
    window.update()
    try:
        yield widget
    finally:
        window.destroy()


def _shown(tip: Tooltip) -> bool:
    """吹き出しが出ているか（属性を直接比べると mypy が型を絞り込みすぎる）。"""
    return tip.window is not None


def _pump(widget: tk.Misc, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        widget.update()
        time.sleep(0.01)


def test_hovering_shows_the_text_after_a_short_delay(button: ttk.Button) -> None:
    tip = Tooltip(button, "コマンドを開始 (F6)", delay_ms=50)

    # When: the pointer enters the button.
    button.event_generate("<Enter>", x=5, y=5)
    button.update()

    # Then: すぐには出ない（横切るだけで点滅させない）が、待てば出る。
    assert not _shown(tip)
    _pump(button, 0.3)
    assert _shown(tip)
    assert tip.text == "コマンドを開始 (F6)"


def test_leaving_or_pressing_hides_the_tip(button: ttk.Button) -> None:
    tip = Tooltip(button, "説明", delay_ms=10)
    button.event_generate("<Enter>", x=5, y=5)
    _pump(button, 0.2)
    assert _shown(tip)

    # When: the pointer leaves.
    button.event_generate("<Leave>")
    button.update()

    # Then: 消える。
    assert not _shown(tip)

    # When: shown again and the button is pressed.
    button.event_generate("<Enter>", x=5, y=5)
    _pump(button, 0.2)
    button.event_generate("<ButtonPress-1>", x=5, y=5)
    button.update()

    # Then: 押した瞬間に消える（押した結果を隠さない）。
    assert not _shown(tip)


def test_leaving_before_the_delay_cancels_the_tip(button: ttk.Button) -> None:
    tip = Tooltip(button, "説明", delay_ms=100)
    button.event_generate("<Enter>", x=5, y=5)
    button.event_generate("<Leave>")
    _pump(button, 0.3)

    # Then: 待っている間に離れたら出さない。
    assert not _shown(tip)


def test_existing_bindings_on_the_widget_are_kept(button: ttk.Button) -> None:
    seen: list[str] = []
    button.bind("<Enter>", lambda _e: seen.append("enter"), add="+")

    Tooltip(button, "説明")
    button.event_generate("<Enter>", x=5, y=5)
    button.update()

    # Then: 既存の束縛を上書きしない（add="+" で足す）。
    assert seen == ["enter"]


def test_every_tooltip_target_is_a_widget_the_window_builds() -> None:
    # Given: the tooltip table and every self.<name> assignment in the UI sources.
    import ast
    from pathlib import Path

    from Window import TOOLTIPS

    sources = [
        Path("SerialController/Window.py"),
        *Path("SerialController/ui").glob("*.py"),
    ]
    built: set[str] = set()
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        built.add(target.attr)

    # Then: 表の名前が実在する部品を指す（改名で黙って外れない）。
    missing = sorted(set(TOOLTIPS) - built)
    assert not missing, f"組み立てていない部品の説明: {missing}"
    # ショートカットのある操作は、説明にキーを書く（覚えなくても見つかる）。
    assert "F6" in TOOLTIPS["startButton"]
    assert "F7" in TOOLTIPS["pauseButton"]
    assert "F5" in TOOLTIPS["reloadCommandButton"]
