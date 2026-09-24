"""Keyboard の斜め入力（WASD同時押し）の検証。実機・画面なしで回す。

WASD 割り当てで w（上）と d（右）を同時押ししたら、
Direction.UP_RIGHT が入ることを期待する。
現状の inputDir() は Key.up などの矢印キー決め打ちのため、
文字キー割り当てでは斜めに解決できず単方向に落ちる（赤テスト）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from Commands.Keys import Direction
from Keyboard import SwitchKeyboardController


class FakeKeyPress:
    """KeyPress の代役。input/inputEnd の呼び出しだけ記録する。"""

    def __init__(self) -> None:
        self.inputs: list[Any] = []
        self.ends: list[Any] = []

    def input(self, *args: Any) -> None:
        self.inputs.append(args)

    def inputEnd(self, *args: Any, **kwargs: Any) -> None:
        _ = kwargs
        self.ends.append(args)


def make_controller(
    tmp_path: Path, keys: FakeKeyPress | None = None
) -> tuple[SwitchKeyboardController, FakeKeyPress]:
    """WASD 割り当ての ini を書いてコントローラーを作る。"""
    ini = tmp_path / "settings.ini"
    ini.write_text(
        "[KeyMap-Button]\n"
        "Button.A = a\n"
        "[KeyMap-Direction]\n"
        "Direction.UP = w\n"
        "Direction.LEFT = a\n"
        "Direction.DOWN = s\n"
        "Direction.RIGHT = d\n"
        "[KeyMap-Hat]\n",
        encoding="utf-8",
    )
    keys = keys if keys is not None else FakeKeyPress()
    return (
        SwitchKeyboardController(keys, setting_path=str(ini), is_active=lambda: True),
        keys,
    )


def test_wasd_chord_inputs_up_right(tmp_path: Path) -> None:
    """w＋d 同時押しで Direction.UP_RIGHT が入ること。"""
    kb, keys = make_controller(tmp_path)
    kb.on_press("w")
    kb.on_press("d")
    assert keys.inputs[-1] == (Direction.UP_RIGHT,)
