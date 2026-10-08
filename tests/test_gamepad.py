"""SwitchGamepadController の申告と寿命の検証。実機・画面なしで回す。

読み取り口は偽物に差し替え、Sender 側も偽物にすることで、
XInput の DLL やシリアルが無くても回せる。見るのは以下である。
  ・押下→申告→解放の一連が差分で Sender へ届くこと
  ・送り主が gamepad であること（入力調停で人の手入力になる）
  ・抜き差し（未接続）で押下が残らないこと
  ・stop() で中立へ戻ること
  ・SerialService の寿命管理（有効化・停止・切断・終了）が
    キーボード操作と同じ作法で動くこと
"""

from __future__ import annotations

from Commands.Keys import Button
from Gamepad import SOURCE, SwitchGamepadController, hat_value_for
from core.xinput import PadState


class FakeSender:
    """Sender の代役。申告の記録だけ行う。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def pressButtons(self, btns, source=None) -> bool:
        self.calls.append(("press", tuple(btns), source))
        return True

    def releaseButtons(self, btns, source=None) -> bool:
        self.calls.append(("release", tuple(btns), source))
        return True

    def holdHat(self, hat, source=None) -> bool:
        self.calls.append(("holdHat", int(hat), source))
        return True

    def releaseHat(self, source=None) -> bool:
        self.calls.append(("releaseHat", source))
        return True

    def setHat(self, hat=None, source=None) -> bool:
        self.calls.append(("setHat", hat, source))
        return True

    def setStick(self, stick, x=None, y=None, source=None) -> bool:
        self.calls.append(("stick", stick, x, y, source))
        return True

    def sendPosture(self, source=None) -> bool:
        self.calls.append(("posture", source))
        return True

    def isOpened(self) -> bool:
        return False

    def flushPending(self) -> None:
        self.calls.append(("flush",))


class ScriptedReader:
    """読み取り口の代役。渡した状態を順に返す。

    パケット番号は読み取り順に振り直す。実機の dwPacketNumber は
    変化時のみ進むが、検証では毎回進めて毎回申告させる。
    """

    def __init__(self, states: list[PadState]) -> None:
        from dataclasses import replace

        self.states = [replace(s, packet=i) for i, s in enumerate(states)]
        self.index = 0

    def read(self, index: int = 0) -> PadState:
        if self.index < len(self.states):
            state = self.states[self.index]
            self.index += 1
            return state
        return self.states[-1]


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


def test_source_is_gamepad() -> None:
    assert SOURCE == "gamepad"


def test_hat_value_for_diagonal() -> None:
    from Commands.Keys import Hat

    assert hat_value_for({"UP", "RIGHT"}) == Hat.TOP_RIGHT
    assert hat_value_for(set()) == Hat.CENTER
    # 打ち消し合う組み合わせは中立へ倒す
    assert hat_value_for({"UP", "DOWN"}) == Hat.CENTER


def test_press_and_release_flow() -> None:
    sender = FakeSender()
    reader = ScriptedReader(
        [
            _pad(pressed=frozenset({"A"})),
            _pad(pressed=frozenset({"A", "B"})),
            _pad(pressed=frozenset({"B"})),
            _pad(pressed=frozenset()),
        ]
    )
    pad = SwitchGamepadController(sender, reader=reader)  # type: ignore[arg-type]
    assert pad.poll_once() is True
    assert ("press", (int(Button.A),), "gamepad") in sender.calls
    assert ("posture", "gamepad") in sender.calls
    pad.poll_once()
    assert ("press", (int(Button.B),), "gamepad") in sender.calls
    pad.poll_once()
    assert ("release", (int(Button.A),), "gamepad") in sender.calls
    pad.poll_once()
    assert ("release", (int(Button.B),), "gamepad") in sender.calls


def test_hat_announced_as_hold() -> None:
    sender = FakeSender()
    reader = ScriptedReader(
        [
            _pad(pressed=frozenset({"DPAD_UP"})),
            _pad(pressed=frozenset()),
        ]
    )
    pad = SwitchGamepadController(sender, reader=reader)  # type: ignore[arg-type]
    pad.poll_once()
    assert any(c[0] == "holdHat" and c[2] == "gamepad" for c in sender.calls)
    pad.poll_once()
    assert any(c[0] == "releaseHat" for c in sender.calls)


def test_stick_reports_changes_only() -> None:
    sender = FakeSender()
    reader = ScriptedReader(
        [
            _pad(thumb_lx=32767, thumb_ly=0),
            _pad(thumb_lx=32767, thumb_ly=0),
        ]
    )
    pad = SwitchGamepadController(sender, reader=reader)  # type: ignore[arg-type]
    pad.poll_once()
    sticks = [c for c in sender.calls if c[0] == "stick" and c[1] == "L"]
    assert len(sticks) == 1
    assert sticks[0][4] == "gamepad"
    sender.calls.clear()
    pad.poll_once()
    assert [c for c in sender.calls if c[0] == "stick"] == []


def test_same_packet_skips_declare() -> None:
    """同じパケット番号なら申告の計算を省く（変化の取りこぼしは無い）。"""
    from core.xinput import PadState as RealPadState

    sender = FakeSender()

    class FixedReader:
        def read(self, index: int = 0) -> RealPadState:
            return _pad(pressed=frozenset({"A"}), packet=7)

    pad = SwitchGamepadController(sender, reader=FixedReader())  # type: ignore[arg-type]
    assert pad.poll_once() is True
    first = list(sender.calls)
    assert any(c[0] == "press" for c in first)
    sender.calls.clear()
    assert pad.poll_once() is True
    assert sender.calls == []


def test_disconnect_releases_held() -> None:
    sender = FakeSender()
    reader = ScriptedReader(
        [
            _pad(pressed=frozenset({"A"})),
            PadState(),  # 抜いた
        ]
    )
    pad = SwitchGamepadController(sender, reader=reader)  # type: ignore[arg-type]
    assert pad.poll_once() is True
    assert pad.poll_once() is False
    assert ("release", (int(Button.A),), "gamepad") in sender.calls
    assert ("posture", "gamepad") in sender.calls


def test_route_switch_resets_packet_and_hold() -> None:
    """読み取り口が切り替わっても申告し直す（番号空間の取り違え防止）。"""
    sender = FakeSender()

    class RoutedReader:
        def __init__(self) -> None:
            self.route = "xinput"

        def route_of(self, index: int = 0) -> str:
            return self.route

        def read(self, index: int = 0) -> PadState:
            # 口が違ってもパケット番号が同値になる場合を再現する
            return _pad(pressed=frozenset({"A"}), packet=5)

    reader = RoutedReader()
    pad = SwitchGamepadController(sender, reader=reader)  # type: ignore[arg-type]
    assert pad.poll_once() is True
    assert ("press", (int(Button.A),), "gamepad") in sender.calls
    sender.calls.clear()
    # 同じ番号でも変化なしとして省かれる
    assert pad.poll_once() is True
    assert sender.calls == []
    # 口が切り替わったら同番号でも申告し直す
    reader.route = "sdl"
    assert pad.poll_once() is True
    assert ("press", (int(Button.A),), "gamepad") in sender.calls


def test_listen_restarts_dead_thread() -> None:
    """死んだ読み取りスレッドは listen で立て直す。"""
    import threading
    import time

    sender = FakeSender()
    reader = ScriptedReader([_pad(pressed=frozenset({"A"}))])
    pad = SwitchGamepadController(sender, reader=reader)  # type: ignore[arg-type]
    dead = threading.Thread(target=lambda: time.sleep(0.01))
    dead.start()
    dead.join(timeout=2.0)
    pad._thread = dead  # type: ignore[assignment]
    assert pad.running is False
    pad.listen()
    assert pad._thread is not None and pad._thread.is_alive()
    pad.stop()


def test_display_hook_receives_snapshot() -> None:
    """読み取り結果が表示口へ届く（仮想パッドの鏡用）。"""
    sender = FakeSender()
    seen: list[dict] = []
    reader = ScriptedReader([_pad(pressed=frozenset({"A"}), packet=9)])
    pad = SwitchGamepadController(
        sender,
        reader=reader,
        on_display=seen.append,  # type: ignore[arg-type]
    )
    assert pad.poll_once() is True
    assert len(seen) == 1
    assert seen[0]["connected"] is True
    assert "A" in seen[0]["buttons"]


def test_display_hook_clears_on_disconnect() -> None:
    sender = FakeSender()
    seen: list[dict] = []
    reader = ScriptedReader([PadState()])
    pad = SwitchGamepadController(
        sender,
        reader=reader,
        on_display=seen.append,  # type: ignore[arg-type]
    )
    assert pad.poll_once() is False
    assert seen == [{"connected": False}]


def test_stop_releases_all() -> None:
    sender = FakeSender()
    reader = ScriptedReader([_pad(pressed=frozenset({"A", "X"}))])
    pad = SwitchGamepadController(sender, reader=reader)  # type: ignore[arg-type]
    pad.poll_once()
    sender.calls.clear()
    pad.stop()
    kinds = [c[0] for c in sender.calls]
    assert "release" in kinds
    assert "posture" in kinds
    assert "flush" in kinds


def test_inactive_gate_blocks_poll() -> None:
    sender = FakeSender()
    reader = ScriptedReader([_pad(pressed=frozenset({"A"}))])
    pad = SwitchGamepadController(
        sender,
        is_active=lambda: False,
        reader=reader,  # type: ignore[arg-type]
    )
    assert pad.poll_once() is False
    assert sender.calls == []


def test_requires_sender() -> None:
    import pytest

    with pytest.raises(ValueError):
        SwitchGamepadController(None)  # type: ignore[arg-type]


def test_service_gamepad_lifecycle() -> None:
    from services.serial_service import SerialService

    service = SerialService(
        notify_user=lambda _m: None, base_dir=".", input_log_emit=lambda _l: None
    )
    # 未接続では理由を返して断る
    err = service.set_gamepad_enabled(True, 0)
    assert err is not None
    assert service.gamepad is None
    # 切る側は常に成功
    assert service.set_gamepad_enabled(False) is None
    service.sender = FakeSender()  # type: ignore[assignment]
    assert service.set_gamepad_enabled(True, 0) is None
    service.stop_gamepad()
    assert service.gamepad is None
    # 切断・終了でも止まる
    assert service.set_gamepad_enabled(True, 0) is None
    service.disconnect()
    assert service.gamepad is None


def test_gamepad_is_human_source() -> None:
    from core.serial.arbitration import Arbiter

    assert Arbiter.is_human_source("gamepad") is True
    assert Arbiter.is_human_source("GAMEPAD") is True
