"""テンキー数字とメイン行数字の区別割当の検証。実機・画面なしで回す。

テンキーの 1 とメイン行の 1 は pynput では char が同じ "1" になるため、
従来の char 照合では区別できない。Windows の仮想キーコード
(VK_NUMPAD0〜VK_NUMPAD9 = 96〜105) を見て "Numpad.0"〜"Numpad.9" の
正規文字列へ分け、ini への保存・再読込・解決の往復で区別が保たれること。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from Commands.Keys import Button
from KeyConfig import key_to_text, text_to_display
from Keyboard import SwitchKeyboardController


class _Missing:
    """vk 属性を持たせないための番兵。getattr ガードの検証に使う。"""

    pass


_MISSING = _Missing()


class FakeKey:
    """pynput のキーの代役。char / name / vk だけ持つ。"""

    def __init__(
        self,
        char: str | None = None,
        name: str | None = None,
        vk: Any = _MISSING,
    ) -> None:
        if char is not None:
            self.char = char
        if name is not None:
            self.name = name
        # vk を渡さなければ属性自体を作らない（他プラットフォーム想定）
        if vk is not _MISSING:
            self.vk = vk


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


def _write_ini(ini: Path, button_a: str, button_b: str) -> None:
    """A/B の割当だけ持つ最小 ini を書く。"""
    ini.write_text(
        "[KeyMap-Button]\n"
        f"Button.A = {button_a}\n"
        f"Button.B = {button_b}\n"
        "[KeyMap-Direction]\n"
        "[KeyMap-Hat]\n",
        encoding="utf-8",
    )


def test_key_to_text_prefers_vk_over_char(tmp_path: Path) -> None:
    """Given: テンキー1 (vk=97, char='1') / When: key_to_text / Then: "Numpad.1"。"""
    _ = tmp_path
    assert key_to_text(FakeKey(char="1", vk=97)) == "Numpad.1"


def test_key_to_text_covers_all_numpad_digits() -> None:
    """Given: vk=96〜105 / When: key_to_text / Then: "Numpad.0"〜"Numpad.9"。"""
    for vk in range(96, 106):
        assert key_to_text(FakeKey(char=str(vk - 96), vk=vk)) == f"Numpad.{vk - 96}"


def test_key_to_text_main_row_keeps_char() -> None:
    """Given: メイン行1 (vk=49, char='1') / When: key_to_text / Then: "1" のまま。"""
    assert key_to_text(FakeKey(char="1", vk=49)) == "1"


def test_key_to_text_without_vk_falls_back() -> None:
    """Given: vk 無し (NumLock OFF・他PF想定) / When: key_to_text / Then: 従来路。"""
    assert key_to_text(FakeKey(char="1")) == "1"
    assert key_to_text(FakeKey(name="up")) == "Key.up"


def test_key_to_text_vk_out_of_range_falls_back() -> None:
    """Given: 範囲外の vk / When: key_to_text / Then: char を優先する従来路。"""
    assert key_to_text(FakeKey(char="a", vk=65)) == "a"


def test_to_input_key_accepts_numpad(tmp_path: Path) -> None:
    """Given: "Numpad.1" / When: _to_input_key / Then: 正規文字列として受理。"""
    ini = tmp_path / "settings.ini"
    _write_ini(ini, "Numpad.1", "1")
    kb = SwitchKeyboardController(FakeKeyPress(), setting_path=str(ini))
    assert kb._to_input_key("Numpad.1") == "Numpad.1"
    # 既存形式はそのまま読めること（ini スキーマ非破壊）
    assert kb._to_input_key("1") == "1"
    assert kb._to_input_key("Key.up") is not None
    assert kb._to_input_key("") is None
    assert kb._to_input_key("10000") is None
    assert kb._to_input_key("Foo.bar") is None


def test_text_to_display_numpad() -> None:
    """Given: "Numpad.1" / When: text_to_display / Then: "テンキー1"。"""
    assert text_to_display("Numpad.1") == "テンキー1"
    assert text_to_display("Numpad.0") == "テンキー0"


def test_text_to_display_numpad_not_placeholder() -> None:
    """Given: "Numpad.1" / When: isdigit 判定 / Then: 旧プレースホルダと衝突しない。"""
    assert "Numpad.1".isdigit() is False
    assert text_to_display("Numpad.1") == "テンキー1"
    # 回帰: 複数桁数字は旧プレースホルダのまま（"Numpad.1" は巻き込まれない）
    assert text_to_display("10000") == "(未設定 10000)"


def test_numpad_and_main_row_coexist(tmp_path: Path) -> None:
    """Given: A=Numpad.1・B=1 / When: 各々押下 / Then: 別操作として解決される。"""
    ini = tmp_path / "settings.ini"
    _write_ini(ini, "Numpad.1", "1")
    keys = FakeKeyPress()
    kb = SwitchKeyboardController(keys, setting_path=str(ini))

    kb.on_press(FakeKey(char="1", vk=97))
    assert keys.inputs == [(Button.A,)]

    keys.inputs.clear()
    kb.on_press(FakeKey(char="1", vk=49))
    assert keys.inputs == [(Button.B,)]


def test_save_reload_resolve_roundtrip(tmp_path: Path) -> None:
    """Given: Numpad.2 保存 / When: 再読込して押下 / Then: 新割当で解決される。"""
    ini = tmp_path / "settings.ini"
    _write_ini(ini, "Numpad.1", "1")
    keys = FakeKeyPress()
    kb = SwitchKeyboardController(keys, setting_path=str(ini))

    # 保存前の割当が生きていること（前提の確認）
    kb.on_press(FakeKey(char="1", vk=97))
    assert keys.inputs == [(Button.A,)]

    # 保存→再読込の往復（キーコンフィグ適用の再現）
    _write_ini(ini, "Numpad.2", "1")
    assert kb.reload_key_map() is True

    keys.inputs.clear()
    kb.on_press(FakeKey(char="1", vk=97))
    assert keys.inputs == []
    kb.on_press(FakeKey(char="2", vk=98))
    assert keys.inputs == [(Button.A,)]
