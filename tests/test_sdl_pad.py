"""SDL 読み取りと読み取り口選択の検証。実機・画面なしで回す。

Switch Pro コンなど XInput に対応していないパッドは SDL（pygame）
経由で読む。XInputReader と同じ PadState を返すため、割当や申告は
そのまま使い回せる。PadSource が番号ごとに繋がっている方を選ぶこと。
"""

from __future__ import annotations

from core.sdl_pad import SdlPadReader, _axis_to_short, _trigger_to_byte


def test_axis_conversion() -> None:
    assert _axis_to_short(0.0) == 0
    assert _axis_to_short(1.0) == 32767
    assert _axis_to_short(-1.0) == -32767
    assert _axis_to_short(2.0) == 32767
    assert _trigger_to_byte(0.0) == 0
    assert _trigger_to_byte(1.0) == 255
    assert _trigger_to_byte(0.5) == 128


def test_normalize_axis_sint16() -> None:
    """pygame 2.6 の int 軸値を -1.0〜1.0 へ直す。"""
    from core.sdl_pad import _normalize_axis

    assert _normalize_axis(0) == 0.0
    assert _normalize_axis(32767) == 1.0
    assert _normalize_axis(-32768) == -1.0
    # 画像の実測：中立の微小値が Sint16 のまま来る
    assert abs(_normalize_axis(-1324) - (-1324 / 32767)) < 1e-9
    # float 実装はそのまま
    assert _normalize_axis(0.5) == 0.5


class FakeController:
    """GameController の代役。中身は差し替え式。標準番号で持つ。"""

    def __init__(self) -> None:
        self.buttons: dict[int, int] = {}
        self.axes: list[float] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self._attached = True

    def get_button(self, num: int) -> int:
        return self.buttons.get(num, 0)

    def get_axis(self, index: int) -> float:
        return self.axes[index]

    def get_numaxes(self) -> int:
        return len(self.axes)

    def get_instance_id(self) -> int:
        return 0

    def init(self) -> None:
        return None

    def attached(self) -> bool:
        return self._attached


class FakeGcModule:
    """pygame._sdl2.controller の代役。"""

    def __init__(
        self, sticks: dict[int, FakeController], names: dict[int, str]
    ) -> None:
        self.sticks = sticks
        self.names = names

    def init(self) -> None:
        return None

    def update(self) -> None:
        return None

    def get_count(self) -> int:
        return len(self.names)

    def is_controller(self, index: int) -> bool:
        return index in self.sticks

    def name_forindex(self, index: int) -> str:
        return self.names[index]

    def Controller(self, index: int) -> FakeController:
        return self.sticks[index]


def _install_fake_gc(
    monkeypatch, sticks: dict[int, FakeController], names: dict[int, str]
) -> None:
    import sys
    import types

    fake_pygame = types.ModuleType("pygame")
    fake_pygame.init = lambda: None  # type: ignore[attr-defined]
    fake_pygame.event = types.SimpleNamespace(pump=lambda: None)  # type: ignore[attr-defined]
    fake_sdl2 = types.ModuleType("pygame._sdl2")
    fake_gc = FakeGcModule(sticks, names)
    fake_controller = types.ModuleType("pygame._sdl2.controller")
    for attr in (
        "init",
        "update",
        "get_count",
        "is_controller",
        "name_forindex",
        "Controller",
    ):
        setattr(fake_controller, attr, getattr(fake_gc, attr))
    fake_sdl2.controller = fake_gc  # type: ignore[attr-defined]
    fake_pygame._sdl2 = fake_sdl2  # type: ignore[attr-defined]
    # 生joystick の代役。軸はコントローラと共有する。
    fake_joystick = types.SimpleNamespace(
        init=lambda: None,
        get_count=lambda: len(sticks),
        Joystick=lambda i: sticks[i],
    )
    fake_pygame.joystick = fake_joystick  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pygame", fake_pygame)
    monkeypatch.setitem(sys.modules, "pygame.joystick", fake_joystick)
    monkeypatch.setitem(sys.modules, "pygame._sdl2", fake_sdl2)
    monkeypatch.setitem(sys.modules, "pygame._sdl2.controller", fake_controller)


def test_read_pumps_even_when_no_controller(monkeypatch) -> None:
    """未接続でも event.pump を呼ぶ（SDLの到着イベント処理に必須）。"""
    calls = {"pump": 0}
    _install_fake_gc(monkeypatch, {}, {})
    import pygame as _pg

    _pg.event.pump = lambda: calls.__setitem__("pump", calls["pump"] + 1)
    reader = SdlPadReader()
    state = reader.read(0)
    assert state.connected is False
    assert calls["pump"] == 1


def test_ensure_retries_after_failure(monkeypatch) -> None:
    """初回init失敗は恒久化せず、間隔後に再試行する。"""
    import sys
    import types

    import core.sdl_pad as sdl_pad

    monkeypatch.setattr(sdl_pad, "ENSURE_RETRY_S", 0.0)
    attempts = {"n": 0}

    def _init() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("init failed")

    fake_pygame = types.ModuleType("pygame")
    fake_pygame.init = _init  # type: ignore[attr-defined]
    fake_controller = types.ModuleType("pygame._sdl2.controller")
    fake_controller.init = lambda: None  # type: ignore[attr-defined]
    fake_sdl2 = types.ModuleType("pygame._sdl2")
    fake_sdl2.controller = fake_controller  # type: ignore[attr-defined]
    fake_pygame._sdl2 = fake_sdl2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pygame", fake_pygame)
    monkeypatch.setitem(sys.modules, "pygame._sdl2", fake_sdl2)
    monkeypatch.setitem(sys.modules, "pygame._sdl2.controller", fake_controller)

    reader = SdlPadReader()
    assert reader.available is False
    assert reader.available is True


def test_missing_pad_resets_sdl(monkeypatch) -> None:
    """未接続が続いたら SDL を作り直す。"""
    import core.sdl_pad as sdl_pad

    monkeypatch.setattr(sdl_pad, "RESET_MISSING_S", 0.0)
    quits = {"n": 0}
    _install_fake_gc(monkeypatch, {}, {})
    import pygame as _pg

    _pg.quit = lambda: quits.__setitem__("n", quits["n"] + 1)
    reader = SdlPadReader()
    assert reader.read(0).connected is False
    assert reader.read(0).connected is False
    assert quits["n"] >= 1


def test_sdl_reads_buttons_hat_stick(monkeypatch) -> None:
    stick = FakeController()
    # 標準番号: 0=A・6=START・11/14=十字上右・15=MISC(CAPTURE)
    stick.buttons = {0: 1, 6: 1, 11: 1, 14: 1, 15: 1}
    # 標準軸: 左X全開・左Y上全開・左トリガ全押し
    stick.axes = [1.0, -1.0, 0.0, 0.0, 1.0, 0.0]
    _install_fake_gc(monkeypatch, {0: stick}, {0: "Pro Controller"})
    reader = SdlPadReader()
    assert reader.available is True
    state = reader.read(0)
    assert state.connected is True
    assert "A" in state.pressed
    assert "START" in state.pressed
    assert "DPAD_UP" in state.pressed
    assert "DPAD_RIGHT" in state.pressed
    assert "CAPTURE" in state.pressed
    assert state.thumb_lx == 32767
    assert state.thumb_ly == 32767
    assert state.left_trigger == 255
    assert state.right_trigger == 0
    # 同じ内容ならパケットは進まない
    assert reader.read(0).packet == state.packet


def test_axes_come_from_raw_joystick(monkeypatch) -> None:
    """軸は生joystick、ボタンは GameController から読む混成。"""
    gc_stick = FakeController()
    gc_stick.buttons = {0: 1}
    gc_stick.axes = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    joy_stick = FakeController()
    joy_stick.axes = [0.0, 0.0, 0.0, 0.0, -1.0, -1.0]
    _install_fake_gc(monkeypatch, {0: gc_stick}, {0: "Pro Controller"})
    import sys

    monkeypatch.setattr(
        sys.modules["pygame"].joystick,  # type: ignore[attr-defined]
        "Joystick",
        lambda i: joy_stick,
    )
    state = SdlPadReader().read(0)
    assert state.connected is True
    assert "A" in state.pressed
    assert (state.thumb_lx, state.thumb_ly) == (0, 0)
    assert (state.left_trigger, state.right_trigger) == (0, 0)


def test_dead_raw_handle_reopens(monkeypatch) -> None:
    """生実体が死んだら捨てて開き直す（抜き差し対応）。"""
    gc_stick = FakeController()
    dead_joy = FakeController()

    def _boom() -> int:
        raise RuntimeError("unplugged")

    dead_joy.get_numaxes = _boom  # type: ignore[method-assign]
    live_joy = FakeController()
    _install_fake_gc(monkeypatch, {0: gc_stick}, {0: "Pro Controller"})
    import sys

    monkeypatch.setattr(
        sys.modules["pygame"].joystick,  # type: ignore[attr-defined]
        "Joystick",
        lambda i: live_joy,
    )
    reader = SdlPadReader()
    reader._sticks[0] = (gc_stick, dead_joy, 7)
    state = reader.read(0)
    assert state.connected is True
    assert reader._sticks[0][1] is live_joy


def test_device_id_change_reopens(monkeypatch) -> None:
    """番号を使い回した別デバイスなら開き直す（ID仕様）。"""
    gc_stick = FakeController()
    gc_stick._attached = False
    old_joy = FakeController()
    old_joy.get_instance_id = lambda: 7  # type: ignore[method-assign]
    new_joy = FakeController()
    new_joy.get_instance_id = lambda: 8  # type: ignore[method-assign]
    _install_fake_gc(monkeypatch, {0: gc_stick}, {0: "Pro Controller"})
    import sys

    monkeypatch.setattr(
        sys.modules["pygame"].joystick,  # type: ignore[attr-defined]
        "Joystick",
        lambda i: new_joy,
    )
    reader = SdlPadReader()
    reader._sticks[0] = (gc_stick, old_joy, 7)
    state = reader.read(0)
    assert state.connected is True
    assert reader._sticks[0][1] is new_joy
    assert reader._sticks[0][2] == 8


def test_sdl_guide_maps_to_home(monkeypatch) -> None:
    stick = FakeController()
    stick.buttons = {5: 1}
    _install_fake_gc(monkeypatch, {0: stick}, {0: "Pro Controller"})
    state = SdlPadReader().read(0)
    assert state.connected is True
    assert "HOME" in state.pressed


def test_sdl_non_controller_is_disconnected(monkeypatch) -> None:
    _install_fake_gc(monkeypatch, {}, {0: "Keyboard"})
    reader = SdlPadReader()
    assert reader.read(0).connected is False


def test_sdl_disconnected_without_pygame(monkeypatch) -> None:
    import sys

    for mod in ("pygame", "pygame._sdl2", "pygame._sdl2.controller"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    reader = SdlPadReader()
    # pygame が無ければ未接続（import 失敗経路）
    monkeypatch.setattr(reader, "_ensure", lambda: False)
    assert reader.available is False
    assert reader.read(0).connected is False
    assert reader.scan() == []


def test_sdl_names_listing(monkeypatch) -> None:
    stick = FakeController()
    _install_fake_gc(monkeypatch, {0: stick}, {0: "Pro Controller"})
    assert SdlPadReader().names() == {0: "Pro Controller"}


def test_names_pumps_events_so_hotplug_shows_up(monkeypatch) -> None:
    """操作OFF（read が呼ばれない）でも、一覧取得が pump して到着を拾う。"""
    sticks: dict[int, FakeController] = {}
    names: dict[int, str] = {}
    _install_fake_gc(monkeypatch, sticks, names)
    import pygame as _pg

    def _pump() -> None:
        # SDL は pump のときに到着イベントを処理して列挙を更新する。
        if 0 not in names:
            sticks[0] = FakeController()
            names[0] = "Pro Controller"

    _pg.event.pump = _pump
    reader = SdlPadReader()
    assert reader.names() == {0: "Pro Controller"}


def test_names_survive_pump_failure(monkeypatch) -> None:
    """pump が失敗しても名前の取得は続ける（pygame の機種差対策）。"""
    stick = FakeController()
    _install_fake_gc(monkeypatch, {0: stick}, {0: "Pro Controller"})
    import pygame as _pg

    def _boom() -> None:
        raise RuntimeError("pump failed")

    _pg.event.pump = _boom
    assert SdlPadReader().names() == {0: "Pro Controller"}


def test_identities_expose_instance_ids(monkeypatch) -> None:
    first = FakeController()
    first.get_instance_id = lambda: 41  # type: ignore[method-assign]
    second = FakeController()
    second.get_instance_id = lambda: 42  # type: ignore[method-assign]
    _install_fake_gc(monkeypatch, {0: first, 1: second}, {0: "A", 1: "B"})
    assert SdlPadReader().identities() == {0: "41", 1: "42"}
