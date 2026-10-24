"""パッド一覧の巡回更新の契約（Tk なし）。

一覧は2秒ごとに取り直される。そのとき
  - ユーザーが入力中の遊び（デッドゾーン）を上書きしない
  - 選択中の番号は保ち、ラベルだけ新しくする
  - 抜き差しで番号が詰め直されたら、同じ個体（instance_id）を追う
を守る。ホットプラグの pump 自体は test_sdl_pad.py が見る。
"""

from __future__ import annotations

from typing import Any

import pytest
from ui.serial_panel import SerialPanelMixin


class _Var:
    """tk 変数の代役。書き込み回数も数える。"""

    def __init__(self, value: Any) -> None:
        self._value = value
        self.writes = 0

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value
        self.writes += 1


class _Combo:
    def __init__(self) -> None:
        self.values: list[str] = []

    def __setitem__(self, key: str, value: Any) -> None:
        if key != "values":
            raise KeyError(key)
        self.values = list(value)


class _Spin:
    def __init__(self, focused: bool) -> None:
        self.focused = focused

    def focus_get(self) -> Any:
        return self if self.focused else None


class _Settings:
    def __init__(self, index: int, deadzone: int) -> None:
        self.gamepad_index = _Var(index)
        self.gamepad_deadzone = _Var(deadzone)


class _Serial:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def stop_gamepad(self) -> None:
        self.events.append("stop")

    def set_gamepad_enabled(
        self, enabled: bool, index: int, deadzone: int
    ) -> str | None:
        self.events.append(("enable", enabled, index, deadzone))
        return None


class _Source:
    def __init__(self, names: dict[int, str], identities: dict[int, str]) -> None:
        self.names_map = names
        self.id_map = identities

    def names(self) -> dict[int, str]:
        return dict(self.names_map)

    def identities(self) -> dict[int, str]:
        return dict(self.id_map)


class _Panel(SerialPanelMixin):
    def __init__(
        self,
        *,
        index: int = 0,
        deadzone: int = 1638,
        field: str = "1638",
        focused: bool = False,
        enabled: bool = False,
    ) -> None:
        self.settings = _Settings(index, deadzone)
        self.gamepad_cb = _Combo()
        self.gamepad_choice = _Var("")
        self._gamepad_map: dict[str, int] = {}
        self.gamepad_dz = _Var(field)
        self.gamepad_dz_spin = _Spin(focused)
        self.is_use_gamepad = _Var(enabled)
        self.serial = _Serial()
        self._gamepad_ids: dict[int, str] = {}
        self._gamepad_identity = ""
        self.calls: list[str] = []

    def _on_setting_changed(self) -> None:
        self.calls.append("save")

    def _bindGamepadMirror(self) -> None:
        self.calls.append("bind")


def _install_source(monkeypatch: pytest.MonkeyPatch, source: _Source) -> None:
    import core.pad_source as pad_source

    monkeypatch.setattr(pad_source, "PadSource", lambda: source)


def test_refresh_keeps_the_selected_index_and_updates_its_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _Source({0: "A", 1: "B"}, {0: "a", 1: "b"})
    panel = _Panel(index=1)
    _install_source(monkeypatch, source)

    panel.refreshGamepads()
    assert panel.gamepad_choice.get() == "1: B"

    # 名前だけ変わった（同じ個体が同じ番号にいる）。
    source.names_map = {0: "A", 1: "B (new)"}
    panel.refreshGamepads()

    assert panel.gamepad_choice.get() == "1: B (new)"
    assert panel.settings.gamepad_index.get() == 1


def test_refresh_follows_the_same_device_when_indices_shift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """選択中の B が 0 番へ詰め直されたら、番号1の別個体へ乗り換えない。"""
    source = _Source({0: "A", 1: "B"}, {0: "a", 1: "b"})
    panel = _Panel(index=1)
    _install_source(monkeypatch, source)
    panel.refreshGamepads()

    source.names_map = {0: "B", 1: "C"}
    source.id_map = {0: "b", 1: "c"}
    panel.refreshGamepads()

    assert panel.settings.gamepad_index.get() == 0
    assert panel.gamepad_choice.get() == "0: B"
    assert "save" in panel.calls


def test_refresh_restarts_the_reader_when_the_selected_device_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _Source({0: "A", 1: "B"}, {0: "a", 1: "b"})
    panel = _Panel(index=1, enabled=True)
    _install_source(monkeypatch, source)
    panel.refreshGamepads()

    source.names_map = {0: "B", 1: "C"}
    source.id_map = {0: "b", 1: "c"}
    panel.refreshGamepads()

    # 番号を固定で持つ読取器を作り直す（画面と実際の読み先を一致させる）。
    assert panel.serial.events == ["stop", ("enable", True, 0, 1638)]
    assert "bind" in panel.calls


def test_refresh_does_not_overwrite_the_deadzone_while_editing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _Source({0: "A"}, {0: "a"})
    panel = _Panel(index=0, deadzone=1638, field="3000", focused=True)
    _install_source(monkeypatch, source)

    panel.refreshGamepads()

    assert panel.gamepad_dz.get() == "3000"


def test_refresh_restores_a_stale_deadzone_when_not_editing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _Source({0: "A"}, {0: "a"})
    panel = _Panel(index=0, deadzone=1638, field="3000", focused=False)
    _install_source(monkeypatch, source)

    panel.refreshGamepads()

    assert panel.gamepad_dz.get() == "1638"


def test_refresh_does_not_rewrite_a_matching_deadzone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _Source({0: "A"}, {0: "a"})
    panel = _Panel(index=0, deadzone=1638, field="1638", focused=False)
    _install_source(monkeypatch, source)

    panel.refreshGamepads()

    assert panel.gamepad_dz.writes == 0


def test_follow_does_not_chase_a_device_that_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """抜けた個体を追い続けず、今の番号の個体を新しい選択にする。"""
    source = _Source({0: "A", 1: "B"}, {0: "a", 1: "b"})
    panel = _Panel(index=1)
    _install_source(monkeypatch, source)
    panel.refreshGamepads()

    # B が抜け、番号1には別の C が挿さった。
    source.names_map = {0: "A", 1: "C"}
    source.id_map = {0: "a", 1: "c"}
    panel.refreshGamepads()

    assert panel.settings.gamepad_index.get() == 1
    assert panel.gamepad_choice.get() == "1: C"
    assert panel._gamepad_identity == "c"
