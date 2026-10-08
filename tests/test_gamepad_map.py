"""XInput 読み取り・割当・申告の検証。実機・画面なしで回す。

PC に接続したコントローラ（XInput 系）の入力が、Switch 側の操作へ
正しく割り当てられ、Sender へ差分申告されること。DLL が無い環境でも
落ちずに未接続として扱えること。
"""

from __future__ import annotations

from core import gamepad_map
from core.gamepad_map import MappedInput, diff_mapped, map_state, stick_to_xy
from core.xinput import (
    BTN_A,
    BTN_DPAD_DOWN,
    BTN_DPAD_LEFT,
    BTN_DPAD_RIGHT,
    BTN_DPAD_UP,
    PadState,
    XInputReader,
    pressed_names,
)


def _pad(**kwargs) -> PadState:
    base = {
        "connected": True,
        "buttons": 0,
        "left_trigger": 0,
        "right_trigger": 0,
        "thumb_lx": 0,
        "thumb_ly": 0,
        "thumb_rx": 0,
        "thumb_ry": 0,
        "packet": 1,
        "pressed": frozenset(),
    }
    base.update(kwargs)
    return PadState(**base)


def test_sdl_deadzone_is_zero() -> None:
    """SDL 経路はデッドゾーンなし（GUI と同等）。"""
    state = _pad(thumb_lx=1000, thumb_ly=0)
    assert map_state(state, left_deadzone=0).stick_l != (128, 128)


def test_gamepad_deadzone_clamped() -> None:
    """範囲外の遊びは 0〜8192 に丸める。"""
    from Gamepad import SwitchGamepadController
    from test_gamepad import FakeSender

    assert SwitchGamepadController(FakeSender(), deadzone=-5).deadzone == 0  # type: ignore[arg-type]
    assert SwitchGamepadController(FakeSender(), deadzone=99999).deadzone == 8192  # type: ignore[arg-type]
    assert SwitchGamepadController(FakeSender(), deadzone=1600).deadzone == 1600  # type: ignore[arg-type]


def test_pressed_names_bits_to_names() -> None:
    assert pressed_names(BTN_A | BTN_DPAD_UP) == frozenset({"A", "DPAD_UP"})
    assert pressed_names(0) == frozenset()


def test_button_mapping_defaults() -> None:
    state = _pad(pressed=frozenset({"A", "START", "BACK", "LEFT_SHOULDER"}))
    mapped = map_state(state)
    assert mapped.connected is True
    assert mapped.buttons == frozenset({"A", "PLUS", "MINUS", "L"})


def test_dpad_maps_to_hat_dirs() -> None:
    state = _pad(pressed=frozenset({"DPAD_UP", "DPAD_RIGHT"}))
    mapped = map_state(state)
    assert mapped.hat_dirs == frozenset({"UP", "RIGHT"})
    assert mapped.buttons == frozenset()


def test_trigger_threshold() -> None:
    assert map_state(_pad(right_trigger=29)).buttons == frozenset()
    assert map_state(_pad(right_trigger=31)).buttons == frozenset({"ZR"})
    assert map_state(_pad(left_trigger=255)).buttons == frozenset({"ZL"})


def test_stick_center_and_deadzone() -> None:
    assert stick_to_xy(0, 0, 7849) == (128, 128)
    assert stick_to_xy(100, -100, 7849) == (128, 128)


def test_stick_full_deflection_reaches_edge() -> None:
    x, y = stick_to_xy(32767, 32767, 7849)
    assert x == 255
    assert y == 0  # Y は反転する（上が小さい）
    x2, y2 = stick_to_xy(-32768, -32767, 7849)
    assert x2 == 0
    assert y2 == 255


def test_disconnected_maps_to_neutral() -> None:
    mapped = map_state(PadState())
    assert mapped.connected is False
    assert mapped.buttons == frozenset()
    assert mapped.hat_dirs == frozenset()
    assert mapped.stick_l == (128, 128)
    assert mapped.stick_r == (128, 128)


def test_diff_reports_press_release_only() -> None:
    old = MappedInput(connected=True, buttons=frozenset({"A"}))
    new = MappedInput(connected=True, buttons=frozenset({"B"}))
    diff = diff_mapped(old, new)
    assert diff["press"] == frozenset({"B"})
    assert diff["release"] == frozenset({"A"})


def test_diff_hat_and_stick_changes() -> None:
    old = MappedInput(connected=True)
    new = MappedInput(
        connected=True,
        hat_dirs=frozenset({"UP"}),
        stick_l=(200, 128),
    )
    diff = diff_mapped(old, new)
    assert diff["hat_changed"] is True
    assert diff["hat_dirs"] == frozenset({"UP"})
    assert diff["stick_l_changed"] is True
    assert diff["stick_r_changed"] is False


def test_reader_out_of_range_is_disconnected() -> None:
    reader = XInputReader()
    assert reader.read(-1).connected is False
    assert reader.read(4).connected is False


def test_reader_without_dll_is_disconnected(monkeypatch) -> None:
    monkeypatch.setattr(gamepad_map, "DEADZONE_TRIGGER", 30)
    import core.xinput as xinput_mod

    monkeypatch.setattr(xinput_mod, "_load_library", lambda: None)
    reader = XInputReader()
    assert reader.available is False
    assert reader.read(0).connected is False
    assert reader.scan() == []


def test_dpad_bits_round_trip() -> None:
    bits = BTN_DPAD_UP | BTN_DPAD_RIGHT | BTN_DPAD_DOWN | BTN_DPAD_LEFT
    names = pressed_names(bits)
    assert names == frozenset({"DPAD_UP", "DPAD_RIGHT", "DPAD_DOWN", "DPAD_LEFT"})
    mapped = map_state(_pad(pressed=names))
    assert mapped.hat_dirs == frozenset({"UP", "RIGHT", "DOWN", "LEFT"})
