"""Keyboard のフォーカス門の検証。実機・画面なしで回す。

pynput の Listener は OS 全体の打鍵を拾うため、窓外の打鍵まで
Switch へ流れる（誤爆）。on_press は is_active 述語で閉門し、
離鍵は常時通す（押しっぱなしの残留を防ぐ）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    tmp_path: Path, active: bool, keys: FakeKeyPress | None = None
) -> tuple[SwitchKeyboardController, FakeKeyPress]:
    ini = tmp_path / "settings.ini"
    ini.write_text(
        "[KeyMap-Button]\n"
        "Button.A = a\n"
        "[KeyMap-Direction]\n"
        "Direction.UP = Key.up\n"
        "[KeyMap-Hat]\n",
        encoding="utf-8",
    )
    keys = keys if keys is not None else FakeKeyPress()
    return (
        SwitchKeyboardController(keys, setting_path=str(ini), is_active=lambda: active),
        keys,
    )


def test_press_through_when_active(tmp_path: Path) -> None:
    kb, keys = make_controller(tmp_path, True)
    kb.on_press("a")
    kb.on_press("a")  # オートリピートは重ねない
    assert len(keys.inputs) == 1
    kb.on_release("a")
    assert len(keys.ends) == 1


def test_press_blocked_when_inactive(tmp_path: Path) -> None:
    kb, keys = make_controller(tmp_path, False)
    kb.on_press("a")
    assert keys.inputs == []
    assert kb.holding == []


def test_release_passes_when_inactive(tmp_path: Path) -> None:
    """有効時に押して無効時に離す：離鍵は通して残留させない。"""
    flag = {"active": True}
    keys = FakeKeyPress()
    ini = tmp_path / "settings.ini"
    ini.write_text("[KeyMap-Button]\nButton.A = a\n", encoding="utf-8")
    kb = SwitchKeyboardController(
        keys, setting_path=str(ini), is_active=lambda: flag["active"]
    )
    kb.on_press("a")
    assert len(keys.inputs) == 1
    flag["active"] = False
    kb.on_release("a")
    assert len(keys.ends) == 1
    assert kb.holding == []


def test_inactive_release_of_unknown_key_is_noop(tmp_path: Path) -> None:
    kb, keys = make_controller(tmp_path, False)
    kb.on_release("a")
    assert keys.inputs == [] and keys.ends == []


def test_direction_blocked_when_inactive(tmp_path: Path) -> None:
    from pynput.keyboard import Key

    kb, keys = make_controller(tmp_path, False)
    kb.on_press(Key.up)
    assert keys.inputs == []
    assert kb.holdingDir == []


def test_service_passes_predicate_to_keyboard(tmp_path: Path) -> None:
    """SerialService が受けた述語を Keyboard 実体へ渡すこと。"""
    from services.serial_service import SerialService

    flag = {"active": False}
    notices: list[str] = []
    service = SerialService(
        notify_user=notices.append,
        base_dir=".",
        input_log_emit=lambda line: None,
        keyboard_active=lambda: flag["active"],
    )
    ini = tmp_path / "settings.ini"
    ini.write_text("[KeyMap-Button]\nButton.A = a\n", encoding="utf-8")
    service.key_press = FakeKeyPress()  # type: ignore[assignment]
    assert service.set_keyboard_enabled(True, str(ini)) is None
    assert service.keyboard is not None
    assert service.keyboard.is_active is not None
    service.keyboard.on_press("a")
    assert service.keyboard.key.inputs == []
    flag["active"] = True
    service.keyboard.on_press("a")
    assert len(service.keyboard.key.inputs) == 1
    service.stop_keyboard()
