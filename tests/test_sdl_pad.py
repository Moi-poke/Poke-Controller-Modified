"""SDL 読み取りと読み取り口選択の検証。実機・画面なしで回す。

Switch Pro コンなど XInput に対応していないパッドは SDL（pygame）
経由で読む。XInputReader と同じ PadState を返すため、割当や申告は
そのまま使い回せる。PadSource が番号ごとに繋がっている方を選ぶこと。
"""

from __future__ import annotations

from core.sdl_pad import SdlPadReader, _axis_to_short, _trigger_to_byte
from core.xinput import PadState


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
        self.quit_calls = 0

    def get_button(self, num: int) -> int:
        return self.buttons.get(num, 0)

    def get_axis(self, index: int) -> float:
        return self.axes[index]

    def get_numaxes(self) -> int:
        return len(self.axes)

    def get_instance_id(self) -> int:
        return 0

    def get_name(self) -> str:
        return "Fake"

    def init(self) -> None:
        return None

    def attached(self) -> bool:
        return self._attached

    def quit(self) -> None:
        self.quit_calls += 1


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
    monkeypatch,
    sticks: dict[int, FakeController],
    names: dict[int, str],
    gc_cls=FakeGcModule,
    joysticks: dict[int, FakeController] | None = None,
) -> None:
    import sys
    import types

    fake_pygame = types.ModuleType("pygame")
    fake_pygame.init = lambda: None  # type: ignore[attr-defined]
    fake_pygame.event = types.SimpleNamespace(pump=lambda: None)  # type: ignore[attr-defined]
    fake_sdl2 = types.ModuleType("pygame._sdl2")
    fake_gc = gc_cls(sticks, names)
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
    table = sticks if joysticks is None else joysticks
    fake_joystick = types.SimpleNamespace(
        init=lambda: None,
        get_count=lambda: len(table),
        Joystick=lambda i: table[i],
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


def test_missing_pad_does_not_quit_sdl(monkeypatch) -> None:
    """未接続が続いても SDL 全体は落とさない（共有ハンドルを壊さない）。"""
    import core.sdl_pad as sdl_pad

    monkeypatch.setattr(sdl_pad, "RESET_MISSING_S", 0.0)
    quits = {"n": 0}
    _install_fake_gc(monkeypatch, {}, {})
    import pygame as _pg

    _pg.quit = lambda: quits.__setitem__("n", quits["n"] + 1)
    reader = SdlPadReader()
    assert reader.read(0).connected is False
    assert reader.read(0).connected is False
    assert quits["n"] == 0


def test_invalidate_closes_handles_and_clears_backoff(monkeypatch) -> None:
    """手動再接続用の破棄は実体を閉じ、open の待ちも捨てる。"""
    _install_fake_gc(monkeypatch, {}, {})
    reader = SdlPadReader()
    old_gc = FakeController()
    old_joy = FakeController()
    reader._sticks[0] = (old_gc, old_joy, 7)
    reader._open_retry_at[0] = 1e9
    reader.invalidate()
    assert reader._sticks == {}
    assert reader._open_retry_at == {}
    assert (old_gc.quit_calls, old_joy.quit_calls) == (1, 1)


def test_open_failure_backs_off_without_blocking(monkeypatch) -> None:
    """open 失敗は sleep せず時刻で抑え、待ちが明けたら再試行する。"""
    opens = {"n": 0}

    class _FailingGc(FakeGcModule):
        def is_controller(self, index: int) -> bool:
            return True

        def Controller(self, index: int):
            opens["n"] += 1
            raise RuntimeError("busy")

    _install_fake_gc(monkeypatch, {}, {}, _FailingGc, joysticks={0: FakeController()})
    reader = SdlPadReader()
    assert reader.read(0).connected is False
    assert reader.read(0).connected is False
    assert opens["n"] == 1
    reader._open_retry_at[0] = 0.0
    assert reader.read(0).connected is False
    assert opens["n"] == 2


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
    """GameController未認識は未接続のまま、警告は1回だけ。"""
    from loguru import logger

    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        _install_fake_gc(monkeypatch, {}, {0: "Keyboard"})
        reader = SdlPadReader()
        assert reader.read(0).connected is False
        assert reader.read(0).connected is False
    finally:
        logger.remove(handler)
    assert sum("GameControllerとして認識" in m for m in messages) == 1


def test_sdl_ensure_failure_logs_once(monkeypatch) -> None:
    """SDL初期化の失敗は再試行しても1回だけ出す。"""
    import sys
    import types

    from loguru import logger

    fake = types.ModuleType("pygame")
    fake._sdl2 = types.ModuleType("pygame._sdl2")  # type: ignore[attr-defined]
    fake_sdl2 = fake._sdl2
    fake_gc = types.ModuleType("pygame._sdl2.controller")
    fake_gc.init = lambda: None  # type: ignore[attr-defined]
    fake_sdl2.controller = fake_gc  # type: ignore[attr-defined]
    fake.init = lambda: (_ for _ in ()).throw(RuntimeError("no sdl"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pygame", fake)
    monkeypatch.setitem(sys.modules, "pygame._sdl2", fake_sdl2)
    monkeypatch.setitem(sys.modules, "pygame._sdl2.controller", fake_gc)

    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        reader = SdlPadReader()
        assert reader.available is False
        reader._retry_at = 0.0
        assert reader.available is False
    finally:
        logger.remove(handler)
    assert sum("SDLを初期化できません" in m for m in messages) == 1


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


def test_names_skip_joysticks_that_are_not_controllers(monkeypatch) -> None:
    """read() が読めない生joystickを選択肢へ出さない。"""
    live = FakeController()
    table = {0: FakeController(), 1: live}
    _install_fake_gc(
        monkeypatch,
        {1: live},
        {0: "Keyboard", 1: "Pro Controller"},
        joysticks=table,
    )
    assert SdlPadReader().names() == {1: "Pro Controller"}


def test_names_find_a_controller_above_gc_count(monkeypatch) -> None:
    """gc.get_count() は本数であり番号の上限ではない（番号1を落とさない）。"""
    live = FakeController()
    table = {0: FakeController(), 1: live}
    _install_fake_gc(
        monkeypatch,
        {1: live},
        {1: "Pro Controller"},
        joysticks=table,
    )
    reader = SdlPadReader()
    assert reader.names() == {1: "Pro Controller"}
    live.get_instance_id = lambda: 42  # type: ignore[method-assign]
    assert reader.identities() == {1: "42"}


def test_identities_skip_joysticks_that_are_not_controllers(monkeypatch) -> None:
    live = FakeController()
    live.get_instance_id = lambda: 42  # type: ignore[method-assign]
    other = FakeController()
    other.get_instance_id = lambda: 41  # type: ignore[method-assign]
    table = {0: other, 1: live}
    _install_fake_gc(
        monkeypatch,
        {1: live},
        {0: "Keyboard", 1: "Pro Controller"},
        joysticks=table,
    )
    assert SdlPadReader().identities() == {1: "42"}


class _NoPad:
    """XInput/SDL の代役。常に未接続。"""

    available = False

    def read(self, index: int) -> PadState:
        return PadState()

    def scan(self) -> list[int]:
        return []

    def names(self) -> dict[int, str]:
        return {}


class _FakeHidPad:
    """HID直読みの代役。open/close回数を数える。"""

    def __init__(self, connected: bool = True, open_ok: bool = True) -> None:
        self.opened = False
        self.open_calls = 0
        self.open_retries: list[int] = []
        self.open_ok = open_ok
        self.close_calls = 0
        self.connected = connected

    def is_open(self) -> bool:
        return self.opened

    def open(self, retries: int = 5) -> bool:
        self.open_calls += 1
        self.open_retries.append(retries)
        if not self.open_ok:
            return False
        self.opened = True
        return True

    def read_pad(self) -> PadState:
        return PadState(connected=self.opened and self.connected)

    def close(self) -> None:
        self.close_calls += 1
        self.opened = False


def test_pad_source_hid_opens_once() -> None:
    """probe保持で初回から接続扱いになり、開き直さない。"""
    from core.pad_source import PadSource

    hid = _FakeHidPad()
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
    assert source.read(0).connected is True
    assert source.read(0).connected is True
    assert source.route_of(0) == "hid"
    assert hid.open_calls == 1
    assert hid.close_calls == 0


def test_pad_source_hid_never_reopens_open_handle() -> None:
    """開いているハンドルはprobe未取得でもopenを呼ばない。

    route が "hid" のまま未接続だと同一tickで _read_hid が2回呼ばれる。
    その両方で open を呼ぶと close→open が繰り返される。
    """
    from core.pad_source import PadSource

    hid = _FakeHidPad(connected=False)
    hid.opened = True  # IMU側が開いた直後（まだ0x30が無い）
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
    source._route[0] = "hid"
    assert source.read(0).connected is False
    assert source.read(0).connected is False
    assert hid.open_calls == 0
    assert hid.close_calls == 0


def test_pad_source_hid_reopens_after_external_close() -> None:
    """USB再列挙でハンドルが閉じたら次の読みで開き直す。"""
    from core.pad_source import PadSource

    hid = _FakeHidPad()
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
    assert source.read(0).connected is True
    hid.opened = False  # 読み取りスレッドが閉じた
    assert source.read(0).connected is True
    assert hid.open_calls == 2


def test_pad_source_hid_open_failure_backs_off() -> None:
    """open失敗は1回だけ試し、0.5秒は次のopenを呼ばない。"""
    from core.pad_source import PadSource

    hid = _FakeHidPad(open_ok=False)
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
    assert source.read(0).connected is False
    assert source.read(0).connected is False
    assert hid.open_calls == 1
    assert hid.open_retries == [1]
    # バックオフが明けたら再試行する。
    source._hid_retry_at = 0.0
    assert source.read(0).connected is False
    assert hid.open_calls == 2


def test_pad_source_does_not_wait_for_an_opening_handle() -> None:
    """open処理中は open を呼ばず、未接続を即返す。"""
    from core.pad_source import PadSource

    class _OpeningHid:
        def __init__(self) -> None:
            self.open_calls = 0

        def is_open(self) -> bool:
            return False

        def is_opening(self) -> bool:
            return True

        def open(self, retries: int = 5) -> bool:
            self.open_calls += 1
            return True

        def read_pad(self):
            return PadState()

    hid = _OpeningHid()
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
    assert source.read(0).connected is False
    assert hid.open_calls == 0


def test_pad_source_logs_hid_fallback_once() -> None:
    """SDL→HIDへ切り替えたことを1回だけ出す（巡回ごとに洪水にしない）。"""
    from core.pad_source import PadSource
    from loguru import logger

    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        hid = _FakeHidPad()
        source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
        assert source.read(0).connected is True
        source._route.clear()
        assert source.read(0).connected is True
    finally:
        logger.remove(handler)
    assert sum("HID直結" in m for m in messages) == 1


def test_sdl_open_failure_logs_once(monkeypatch) -> None:
    """SDL_GameControllerOpen の同じ失敗は毎tickではなく1回だけ出す。"""
    from loguru import logger

    class _FailingGc(FakeGcModule):
        def is_controller(self, index: int) -> bool:
            return True

        def Controller(self, index: int):
            raise RuntimeError("open failed")

    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        _install_fake_gc(
            monkeypatch, {}, {}, _FailingGc, joysticks={0: FakeController()}
        )
        reader = SdlPadReader()
        assert reader.read(0).connected is False
        assert reader.read(0).connected is False
    finally:
        logger.remove(handler)
    assert sum("開けません" in m for m in messages) == 1


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


class _CountingPad:
    """読み取り成功を切り替えられる SDL の代役。呼び出し回数を数える。"""

    available = True

    def __init__(self, connected: bool = False) -> None:
        self.connected = connected
        self.read_calls = 0
        self.invalidate_calls = 0

    def read(self, index: int) -> PadState:
        self.read_calls += 1
        return PadState(connected=self.connected)

    def scan(self) -> list[int]:
        return [0] if self.connected else []

    def names(self) -> dict[int, str]:
        return {0: "SDL"} if self.connected else {}

    def identities(self) -> dict[int, str]:
        return {0: "sdl-id"} if self.connected else {}

    def invalidate(self) -> None:
        self.invalidate_calls += 1


def test_pad_source_invalidate_drops_route_and_retries_sdl() -> None:
    """手動再接続は経路の記憶を捨て、SDL から選び直す。"""
    from core.pad_source import PadSource

    sdl = _CountingPad(connected=False)
    hid = _FakeHidPad()
    source = PadSource(xinput=_NoPad(), sdl=sdl, hid=hid)
    assert source.read(0).connected is True
    assert source.route_of(0) == "hid"

    sdl.connected = True
    source.invalidate()
    assert sdl.invalidate_calls == 1
    assert source.read(0).connected is True
    assert source.route_of(0) == "sdl"


def test_pad_source_does_not_reread_a_failed_route() -> None:
    """経路が失敗した回に同じ口を読み直さない（open待ちの二重化を防ぐ）。"""
    from core.pad_source import PadSource

    sdl = _CountingPad(connected=False)
    source = PadSource(xinput=_NoPad(), sdl=sdl, hid=_FakeHidPad(open_ok=False))
    source._route[0] = "sdl"
    assert source.read(0).connected is False
    assert sdl.read_calls == 1


def test_pad_source_invalidate_resets_hid_backoff() -> None:
    from core.pad_source import PadSource

    hid = _FakeHidPad(open_ok=False)
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
    assert source.read(0).connected is False
    assert hid.open_calls == 1
    source.invalidate()
    assert source.read(0).connected is False
    assert hid.open_calls == 2


def test_pad_source_returns_to_sdl_from_hid() -> None:
    """起動直後に SDL が間に合わず HID へ落ちても、後から SDL へ戻る。"""
    from core.pad_source import PadSource

    sdl = _CountingPad(connected=False)
    hid = _FakeHidPad()
    source = PadSource(xinput=_NoPad(), sdl=sdl, hid=hid)
    assert source.read(0).connected is True
    assert source.route_of(0) == "hid"

    sdl.connected = True
    source._sdl_reprobe_at = 0.0
    assert source.read(0).connected is True
    assert source.route_of(0) == "sdl"


def test_read_axes_works_without_a_gamecontroller(monkeypatch) -> None:
    """GameController 未認識でも、生joystick の校正済み軸は読める。"""
    stick = FakeController()
    stick.axes = [1.0, -1.0, 0.0, 0.0, 1.0, 1.0]
    _install_fake_gc(monkeypatch, {}, {}, joysticks={0: stick})
    reader = SdlPadReader()
    assert reader.read(0).connected is False
    axes = reader.read_axes(0)
    assert axes is not None
    assert axes[:6] == (32767, 32767, 0, 0, 255, 255)


def test_read_axes_can_require_a_device_name(monkeypatch) -> None:
    """同じ番号の別機種を混ぜないよう、名前で確かめられる。"""
    stick = FakeController()
    _install_fake_gc(monkeypatch, {}, {}, joysticks={0: stick})
    reader = SdlPadReader()
    assert reader.read_axes(0, name_hint="pro controller") is None
    stick.get_name = lambda: "Nintendo Switch Pro Controller"  # type: ignore[method-assign]
    assert reader.read_axes(0, name_hint="pro controller") is not None


class _SdlAxesOnly:
    """GameController は開けないが、軸だけ読める SDL の代役。"""

    available = True

    def __init__(self) -> None:
        self.hint = ""

    def read(self, index: int) -> PadState:
        return PadState()

    def scan(self) -> list[int]:
        return []

    def names(self) -> dict[int, str]:
        return {}

    def identities(self) -> dict[int, str]:
        return {}

    def invalidate(self) -> None:
        return None

    def read_axes(self, index: int, *, name_hint: str = ""):
        self.hint = name_hint
        return (
            32767,
            32767,
            0,
            0,
            0,
            0,
            (1.0, -1.0, 0.0, 0.0, -1.0, -1.0),
        )


def test_pad_source_uses_sdl_axes_on_the_hid_route() -> None:
    """HID の未校正スティックを SDL の校正済み軸で置き換える。"""
    from core.pad_source import PadSource

    class _HidWithButtons(_FakeHidPad):
        def read_pad(self) -> PadState:
            return PadState(
                connected=self.opened and self.connected,
                buttons=0x1000,
                pressed=frozenset({"A"}),
                thumb_lx=-20000,
                thumb_ly=-20000,
            )

    sdl = _SdlAxesOnly()
    source = PadSource(xinput=_NoPad(), sdl=sdl, hid=_HidWithButtons())
    state = source.read(0)
    assert state.connected is True
    # ボタンは HID のまま、スティックだけ SDL。
    assert "A" in state.pressed
    assert (state.thumb_lx, state.thumb_ly) == (32767, 32767)
    assert (state.thumb_rx, state.thumb_ry) == (0, 0)
    assert state.raw_axes == (1.0, -1.0, 0.0, 0.0, -1.0, -1.0)
    assert sdl.hint == "pro controller"


def test_invalidate_can_keep_raw_handles(monkeypatch) -> None:
    """自動の列挙やり直しは、HID補正に使う生joystick を止めない。"""
    _install_fake_gc(monkeypatch, {}, {})
    reader = SdlPadReader()
    raw = FakeController()
    reader._raw_sticks[0] = (raw, 0)
    reader.invalidate(close_raw=False)
    assert 0 in reader._raw_sticks
    assert raw.quit_calls == 0


def test_read_axes_finds_the_named_device_at_another_index(monkeypatch) -> None:
    """HIDが0番、SDLのProConが1番のように番号がずれても軸を拾う。"""
    other = FakeController()
    other.get_name = lambda: "Keyboard"  # type: ignore[method-assign]
    procon = FakeController()
    procon.axes = [1.0, -1.0, 0.0, 0.0, -1.0, -1.0]
    procon.get_name = lambda: "Nintendo Switch Pro Controller"  # type: ignore[method-assign]
    _install_fake_gc(monkeypatch, {}, {}, joysticks={0: other, 1: procon})
    axes = SdlPadReader().read_axes(0, name_hint="pro controller")
    assert axes is not None
    assert axes[:6] == (32767, 32767, 0, 0, 0, 0)


def test_raw_joystick_opens_before_gamecontroller(monkeypatch) -> None:
    """GCを先に開くと生joystick の open が失敗する環境がある（実測）。"""
    order: list[str] = []
    stick = FakeController()
    original_init = stick.init

    def _init() -> None:
        order.append("raw")
        original_init()

    stick.init = _init  # type: ignore[method-assign]

    class _RecordingGc(FakeGcModule):
        def Controller(self, index: int) -> FakeController:
            order.append("gc")
            return super().Controller(index)

    _install_fake_gc(monkeypatch, {0: stick}, {0: "Pro Controller"}, _RecordingGc)
    assert SdlPadReader().read(0).connected is True
    assert order == ["raw", "gc"]


def test_gc_open_failure_keeps_raw_axes_available(monkeypatch) -> None:
    """GC open が失敗しても生joystick は残し、軸補正に使える。"""

    class _FailingGc(FakeGcModule):
        def is_controller(self, index: int) -> bool:
            return True

        def Controller(self, index: int) -> FakeController:
            raise RuntimeError("busy")

    stick = FakeController()
    stick.axes = [1.0, -1.0, 0.0, 0.0, -1.0, -1.0]
    _install_fake_gc(monkeypatch, {}, {}, _FailingGc, joysticks={0: stick})
    reader = SdlPadReader()
    assert reader.read(0).connected is False
    assert 0 in reader._raw_sticks
    axes = reader.read_axes(0)
    assert axes is not None
    assert axes[:4] == (32767, 32767, 0, 0)


def test_raw_open_failure_warns_once(monkeypatch) -> None:
    """生joystick を開けないときは、理由を1回だけ残す。"""

    class _GcOnly(FakeGcModule):
        def is_controller(self, index: int) -> bool:
            return True

    from loguru import logger

    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        _install_fake_gc(monkeypatch, {}, {}, _GcOnly)
        reader = SdlPadReader()
        assert reader.read(0).connected is False
        assert reader.read(0).connected is False
    finally:
        logger.remove(handler)
    assert sum("生joystickを開けません" in m for m in messages) == 1


def test_pad_source_warns_once_when_sdl_axes_are_unavailable() -> None:
    """HID補正に使うSDL軸が無いことを、巡回ごとに洪水にしない。"""
    from core.pad_source import PadSource
    from loguru import logger

    class _NoAxes(_SdlAxesOnly):
        def read_axes(self, index: int, *, name_hint: str = ""):
            return None

    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        source = PadSource(xinput=_NoPad(), sdl=_NoAxes(), hid=_FakeHidPad())
        assert source.read(0).connected is True
        assert source.read(0).connected is True
    finally:
        logger.remove(handler)
    assert sum("スティック補正にSDL" in m for m in messages) == 1


def test_raw_open_failure_backs_off(monkeypatch) -> None:
    """生joystick の open 失敗を8ms周期で叩き続けない。"""
    _install_fake_gc(monkeypatch, {}, {}, joysticks={0: FakeController()})
    import pygame as _pg

    calls = {"n": 0}

    def _raise(index: int):
        calls["n"] += 1
        raise RuntimeError("busy")

    _pg.joystick.Joystick = _raise  # type: ignore[assignment]
    reader = SdlPadReader()
    assert reader.read(0).connected is False
    assert reader.read(0).connected is False
    assert calls["n"] == 1
    reader._raw_retry_at[0] = 0.0
    assert reader.read(0).connected is False
    assert calls["n"] == 2


def test_pad_source_hid_open_does_not_block_read() -> None:
    """HID openのhandshake待ちで8msの読み取りを止めない。"""
    import time

    from core.pad_source import PadSource

    class _SlowHid:
        def __init__(self) -> None:
            self.opened = False

        def is_open(self) -> bool:
            return self.opened

        def is_opening(self) -> bool:
            return False

        def open(self, retries: int = 5) -> bool:
            time.sleep(0.4)
            self.opened = True
            return True

        def read_pad(self) -> PadState:
            return PadState(connected=self.opened)

    hid = _SlowHid()
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=hid)
    started = time.monotonic()
    first = source.read(0)
    elapsed = time.monotonic() - started
    assert first.connected is False
    assert elapsed < 0.1  # openの0.4秒を待たない
    thread = source._hid_open_thread
    assert thread is not None
    thread.join(2.0)
    assert source.read(0).connected is True


def test_pad_source_hid_only_does_not_touch_sdl() -> None:
    """通常はHID有線に固定し、SDLを初期化・読み取りしない。"""
    from core.pad_source import PadSource

    sdl = _CountingPad(connected=True)
    hid = _FakeHidPad()
    source = PadSource(xinput=_NoPad(), sdl=sdl, hid=hid, allow_sdl=False)
    assert source.read(0).connected is True
    assert source.route_of(0) == "hid"
    assert sdl.read_calls == 0
    assert source.names() == {0: "Pro Controller (HID直結)"}
    assert source.identities() == {}
    assert source.scan() == [0]
    assert sdl.read_calls == 0


def test_pad_source_hid_disabled_uses_sdl() -> None:
    """HidHide未設定時はHIDを開かず、SDL/XInputだけを使う。"""
    from core.pad_source import PadSource

    sdl = _CountingPad(connected=True)
    hid = _FakeHidPad()
    source = PadSource(
        xinput=_NoPad(), sdl=sdl, hid=hid, allow_sdl=False, allow_hid=False
    )
    assert source.read(0).connected is True
    assert source.route_of(0) == "sdl"
    assert hid.open_calls == 0
    assert sdl.read_calls == 1
