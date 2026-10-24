"""パッド一覧の巡回更新の契約（Tk なし）。

一覧は2秒ごとに取り直される。そのとき
  - ユーザーが入力中の遊び（デッドゾーン）を上書きしない
  - 選択中の番号は保ち、ラベルだけ新しくする
  - 抜き差しで番号が詰め直されたら、同じ個体（instance_id）を追う
を守る。ホットプラグの pump 自体は test_sdl_pad.py が見る。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from core.ui_inbox import UiInbox
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
        self.thread_ids: list[int] = []
        self.gate: threading.Event | None = None

    def _record(self) -> None:
        self.thread_ids.append(threading.get_ident())
        if self.gate is not None:
            self.gate.wait(3.0)

    def stop_gamepad(self) -> None:
        self._record()
        self.events.append("stop")

    def set_gamepad_enabled(
        self, enabled: bool, index: int, deadzone: int
    ) -> str | None:
        self._record()
        self.events.append(("enable", enabled, index, deadzone))
        return None


class _Source:
    def __init__(self, names: dict[int, str], identities: dict[int, str]) -> None:
        self.names_map = names
        self.id_map = identities
        self.thread_ids: list[int] = []
        self.gate: threading.Event | None = None
        self.names_calls = 0

    def names(self) -> dict[int, str]:
        self.names_calls += 1
        self.thread_ids.append(threading.get_ident())
        if self.gate is not None:
            self.gate.wait(3.0)
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
        # 列挙と掴み直しはワーカー。結果は inbox 経由で GUI へ戻す。
        self.inbox = UiInbox()
        self._gamepad_refresh_busy = False
        self._gamepad_refresh_thread: threading.Thread | None = None
        self._gamepad_restart_busy = False
        self._gamepad_restart_thread: threading.Thread | None = None
        self._gamepad_restart_pending: Any = None

    def refreshGamepads(self) -> None:
        """ワーカーを回し、テストでは完了まで待ってから返す。"""
        super().refreshGamepads()
        _settle(self)

    def _on_setting_changed(self) -> None:
        self.calls.append("save")

    def _bindGamepadMirror(self) -> None:
        self.calls.append("bind")


def _install_source(monkeypatch: pytest.MonkeyPatch, source: _Source) -> None:
    import core.pad_source as pad_source

    monkeypatch.setattr(pad_source, "PadSource", lambda: source)


def _settle(panel: _Panel, timeout: float = 3.0) -> None:
    """ワーカーの完了と inbox の反映を待つ（テスト用の同期点）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        panel.inbox.drain()
        threads = (
            getattr(panel, "_gamepad_refresh_thread", None),
            getattr(panel, "_gamepad_restart_thread", None),
        )
        busy = any(t is not None and t.is_alive() for t in threads)
        busy = busy or panel._gamepad_refresh_busy or panel._gamepad_restart_busy
        if not busy and panel.inbox.pending() == 0:
            return
        time.sleep(0.005)
    raise AssertionError("gamepad workers did not settle")


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
    assert panel.serial.thread_ids
    assert threading.get_ident() not in panel.serial.thread_ids
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


def test_refresh_enumerates_off_the_tk_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """列挙はワーカー。Tk（ここではメイン）スレッドを待たせない。"""
    source = _Source({0: "A"}, {0: "a"})
    source.gate = threading.Event()
    panel = _Panel()
    panel.gamepad_cb["values"] = ["0: previous"]
    panel.gamepad_choice.set("0: previous")
    _install_source(monkeypatch, source)

    started = time.monotonic()
    SerialPanelMixin.refreshGamepads(panel)
    assert time.monotonic() - started < 0.2
    # 列挙が終わるまでは前回の表示を保つ。
    assert panel.gamepad_choice.get() == "0: previous"

    source.gate.set()
    _settle(panel)
    assert source.thread_ids
    assert threading.get_ident() not in source.thread_ids
    assert panel.gamepad_choice.get() == "0: A"


def test_refresh_does_not_stack_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """巡回が重なっても列挙は1本だけ。"""
    source = _Source({0: "A"}, {0: "a"})
    source.gate = threading.Event()
    panel = _Panel()
    _install_source(monkeypatch, source)

    SerialPanelMixin.refreshGamepads(panel)
    SerialPanelMixin.refreshGamepads(panel)
    source.gate.set()
    _settle(panel)
    assert source.names_calls == 1


def test_restart_requests_do_not_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """連続で番号が移っても、再起動は1本ずつ最新番号へ行う。"""
    panel = _Panel(index=0, enabled=True)
    panel.serial.gate = threading.Event()
    panel._restart_gamepad_at(1)
    panel._restart_gamepad_at(2)
    assert getattr(panel, "_gamepad_restart_busy") is True
    pending = panel._gamepad_restart_pending
    assert pending == 2

    panel.serial.gate.set()
    _settle(panel)
    assert panel.serial.events == [
        "stop",
        ("enable", True, 1, 1638),
        "stop",
        ("enable", True, 2, 1638),
    ]
    assert getattr(panel, "_gamepad_restart_busy") is False
    assert getattr(panel, "_gamepad_restart_pending") is None


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
