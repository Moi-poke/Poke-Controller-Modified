"""キー割り当ての再読み込み（reload_key_map）の検証。実機・画面なしで回す。

現状はキーコンフィグ保存後にセーブ→切替の入れ直しが必要で、
生きている SwitchKeyboardController に新割当が届かない。
ini を書き換えて reload_key_map() を呼べば再生成なしで
新割当が解決できること。holding 系は持ち越し、残留は作らない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from Commands.Keys import Button
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


def _write_ini(ini: Path, button_a_key: str) -> None:
    """割当 v1/v2 を切り替えるための最小 ini を書く。"""
    ini.write_text(
        "[KeyMap-Button]\n"
        f"Button.A = {button_a_key}\n"
        "[KeyMap-Direction]\n"
        "[KeyMap-Hat]\n",
        encoding="utf-8",
    )


def test_reload_picks_up_new_binding(tmp_path: Path) -> None:
    """Given: v1（A=a）で生成した実体 / When: v2（A=b）へ書換え再読込 / Then: b が解決される。"""
    ini = tmp_path / "settings.ini"
    _write_ini(ini, "a")
    keys = FakeKeyPress()
    kb = SwitchKeyboardController(keys, setting_path=str(ini))

    # v1 の割当が生きていること（前提の確認）
    kb.on_press("a")
    assert keys.inputs == [(Button.A,)]

    # v2 へ書き換えて再読み込みする（再生成はしない）
    _write_ini(ini, "b")
    kb.reload_key_map()

    # 旧割当は解決されず、新割当だけが届くこと
    keys.inputs.clear()
    kb.on_release("a")
    kb.on_press("a")
    assert keys.inputs == []
    kb.on_press("b")
    assert keys.inputs == [(Button.A,)]


def test_reload_preserves_holding_without_leak(tmp_path: Path) -> None:
    """Given: 押下中（holding あり）/ When: 再読込 / Then: 押下中は保持し残留は残さない。"""
    ini = tmp_path / "settings.ini"
    _write_ini(ini, "a")
    keys = FakeKeyPress()
    kb = SwitchKeyboardController(keys, setting_path=str(ini))

    kb.on_press("a")
    assert kb.holding == ["a"]

    # 押下中のまま ini を v2 へ切り替えて再読み込みする
    _write_ini(ini, "b")
    kb.reload_key_map()

    # 押下中のキーは持ち越す（離鍵で正しく終わわれること）
    assert kb.holding == ["a"]
    assert kb.holdingDir == []
    kb.on_release("a")
    assert kb.holding == []
    assert len(keys.ends) == 1

    # 再読込後は新割当で押せること（状態の残留なし）
    kb.on_press("b")
    assert keys.inputs[-1] == (Button.A,)
    assert kb.holding == ["b"]
