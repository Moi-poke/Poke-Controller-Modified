"""RUMBLE表示のhostベクタ（実機不要）。

BconSetup: 0x22 frame → queue("rumble",(l,r)) → _pollでログ行
「振動 L=.. R=..」。PLAYER_INFOランプは触らない。
serial_panel: 500ms巡回が last_rumble() を隣接ラベルへ反映する。
"""

from typing import Any


class _StubLabel:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, text: str) -> None:
        self.text = text

    config = configure


class _StubCanvas:
    def __init__(self) -> None:
        self.fills: dict[int, str] = {}

    def itemconfigure(self, item: int, fill: str) -> None:
        self.fills[item] = fill


class _StubLog:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, **kwargs: Any) -> None:
        _ = kwargs

    def insert(self, index: Any, text: str) -> None:
        _ = index
        self.text += text

    def see(self, index: Any) -> None:
        _ = index


class _StubWindow:
    def after(self, ms: int, func: Any) -> Any:
        _ = (ms, func)
        return None

    def winfo_exists(self) -> bool:
        return False

    def destroy(self) -> None:
        return None


class _FakeBconTransport:
    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self) -> None:
        self.rx_subs: list[Any] = []
        self.rx_unsub_calls = 0
        self.player_info = b""
        self.rumble = b""
        self.status: dict[str, int] = {
            "flags": 0,
            "last_seq": 0,
            "err_crc": 0,
            "err_drop": 0,
            "errcode": 0,
        }

    def subscribe_rx(self, func: Any) -> Any:
        if func not in self.rx_subs:
            self.rx_subs.append(func)

        def _unsub() -> None:
            self.rx_unsub_calls += 1
            if func in self.rx_subs:
                self.rx_subs.remove(func)

        return _unsub

    def last_player_info(self) -> bytes:
        return bytes(self.player_info)

    def last_rumble(self) -> bytes:
        return bytes(self.rumble)

    def request_status(self, timeout: float = 1.0) -> dict[str, int]:
        _ = timeout
        return dict(self.status)


class _FakeSender:
    def __init__(self, transport: Any) -> None:
        self.transport = transport


def _make_headless_setup(transport: Any) -> Any:
    import queue as _q

    from BconSetup import BconSetup

    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = _FakeSender(transport)
    obj._queue = _q.Queue()
    obj._busy = False
    obj._stop = False
    obj._closed = False
    obj._rx_unsub = None
    obj._rx_transport = None
    obj._lamp_items = [10, 11, 12, 13]
    obj._lamp_canvas = _StubCanvas()
    obj._imu_label = _StubLabel()
    obj._vib_label = _StubLabel()
    obj._log = _StubLog()
    obj.window = _StubWindow()
    return obj


def test_rumble_frame_queued_and_poll_logs振動() -> None:
    """0x22 frame → queue entry → _pollでログ行「振動 L=.. R=..」が出る。"""
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    obj._bind_player_info(transport)

    obj._on_rx_frame((0x22, bytes([0x0A, 0x05]), 0))
    assert obj._queue.qsize() == 1
    kind, payload = obj._queue.get_nowait()
    assert kind == "rumble"
    assert tuple(payload) == (0x0A, 0x05)
    # widgetには触れていない（queueだけ）。
    assert obj._log.text == ""

    obj._on_rx_frame((0x22, bytes([0x0A, 0x05]), 0))
    obj._poll()
    assert "振動" in obj._log.text
    assert "L=10" in obj._log.text
    assert "R=5" in obj._log.text


def test_rumble_non_0x22_ignored() -> None:
    """0x22以外（STATUS等）はrumbleとして積まない。PLAYERランプは不変。"""
    from BconSetup import PLAYER_LAMP_OFF_COLOR

    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    obj._bind_player_info(transport)

    obj._on_rx_frame((0x20, bytes([0x00] * 7), 0))
    assert obj._queue.empty()
    obj._poll()
    assert obj._log.text == ""
    assert obj._lamp_canvas.fills == {}
    assert PLAYER_LAMP_OFF_COLOR == "gray"


def test_rumble_short_payload_safe_fallback() -> None:
    """短い欠片でも落とさない（安全側へ倒す）。"""
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    obj._bind_player_info(transport)

    obj._on_rx_frame((0x22, b"\x01", 0))
    assert obj._queue.qsize() == 1
    obj._poll()  # raiseしない
    assert "振動" in obj._log.text

    obj._on_rx_frame((0x22, b"", 0))
    obj._poll()  # raiseしない
    assert "振動" in obj._log.text


def test_rumble_on_status_echoes_last_rumble() -> None:
    """状態確認は last_rumble() を RUMBLE: L=.. R=.. で写す。空は振動:（なし）。"""
    transport = _FakeBconTransport()
    transport.rumble = bytes([0x0A, 0x05])
    obj = _make_headless_setup(transport)
    obj._run = lambda func: func()

    obj._on_status()
    obj._poll()
    assert "RUMBLE" in obj._log.text
    assert "L=10" in obj._log.text
    assert "R=5" in obj._log.text

    transport.rumble = b""
    obj._on_status()
    obj._poll()
    assert "振動" in obj._log.text
    assert "なし" in obj._log.text


def test_rumble_patrol_updates_label() -> None:
    """500ms巡回が購読写しを隣接ラベルへ反映する。PLAYERランプ不変。"""
    from ui.serial_panel import SerialPanelMixin

    transport = _FakeBconTransport()

    mixin: Any = SerialPanelMixin.__new__(SerialPanelMixin)
    mixin._player_lamp_items = [20, 21, 22, 23]
    mixin.player_lamp_canvas = _StubCanvas()
    mixin.rumble_label = _StubLabel()
    mixin.root = _StubWindow()
    mixin._bcon_transport = lambda: transport  # type: ignore[method-assign]

    mixin._poll_player_lamp()  # 購読を結線する（TkからRPCしない）。
    assert len(transport.rx_subs) == 1
    transport.rx_subs[0]((0x23, b"\x05", 0))
    transport.rx_subs[0]((0x22, bytes([0x03, 0x04]), 0))

    mixin._poll_player_lamp()
    assert "3" in mixin.rumble_label.text
    assert "4" in mixin.rumble_label.text
    # PLAYERランプは0x05の復号のまま（rumbleで上書きしない）。
    assert mixin.player_lamp_canvas.fills[20] == "green yellow"
    assert mixin.player_lamp_canvas.fills[21] == "gray"

    transport.rx_subs[0]((0x22, b"", 0))
    mixin._poll_player_lamp()
    assert "なし" in mixin.rumble_label.text


class _CountingRpcTransport(_FakeBconTransport):
    """getter呼び出しを数える。Tk巡回からは呼ばれない契約。"""

    def __init__(self) -> None:
        super().__init__()
        self.player_info_calls = 0
        self.rumble_calls = 0

    def last_player_info(self) -> bytes:
        self.player_info_calls += 1
        return super().last_player_info()

    def last_rumble(self) -> bytes:
        self.rumble_calls += 1
        return super().last_rumble()


class _NoGetterTransport:
    """getterも持たない運搬器。購読だけある（別プロセス版の最小形）。"""

    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self) -> None:
        self.rx_subs: list[Any] = []

    def subscribe_rx(self, func: Any) -> Any:
        if func not in self.rx_subs:
            self.rx_subs.append(func)

        def _unsub() -> None:
            if func in self.rx_subs:
                self.rx_subs.remove(func)

        return _unsub


class _RecordingWindow(_StubWindow):
    """after予約とafter_cancelを取り消し線として記録する。"""

    def __init__(self) -> None:
        self.scheduled: list[Any] = []
        self.cancelled: list[Any] = []
        self._next_id = 0

    def after(self, ms: int, func: Any) -> Any:
        _ = ms
        self._next_id += 1
        self.scheduled.append((self._next_id, func))
        return self._next_id

    def after_cancel(self, after_id: Any) -> None:
        self.cancelled.append(after_id)


def _make_patrol_mixin(transport: Any, window: Any) -> Any:
    from ui.serial_panel import SerialPanelMixin

    mixin: Any = SerialPanelMixin.__new__(SerialPanelMixin)
    mixin._player_lamp_items = [20, 21, 22, 23]
    mixin.player_lamp_canvas = _StubCanvas()
    mixin.rumble_label = _StubLabel()
    mixin.root = window
    mixin._bcon_transport = lambda: transport  # type: ignore[method-assign]
    mixin._player_lamp_after = None
    return mixin


def test_patrol_tick_never_calls_rpc_getters() -> None:
    """Tk巡回は last_player_info/last_rumble を呼ばない（Q1=A）。
    未受信は従来どおり全消灯＋振動:（なし）。"""
    transport = _CountingRpcTransport()
    mixin = _make_patrol_mixin(transport, _RecordingWindow())

    mixin._poll_player_lamp()

    assert transport.player_info_calls == 0
    assert transport.rumble_calls == 0
    assert all(color == "gray" for color in mixin.player_lamp_canvas.fills.values())
    assert "なし" in mixin.rumble_label.text


def test_patrol_reflects_subscribe_cache() -> None:
    """購読経路のframeが次の巡回でランプ・振動ラベルへ写る（RPCなし）。"""
    transport = _CountingRpcTransport()
    mixin = _make_patrol_mixin(transport, _RecordingWindow())

    mixin._poll_player_lamp()  # 購読を結線する（TkからRPCしない）。
    assert len(transport.rx_subs) == 1
    transport.rx_subs[0]((0x23, b"\x05", 0))
    transport.rx_subs[0]((0x22, bytes([0x03, 0x04]), 0))

    mixin._poll_player_lamp()

    assert transport.player_info_calls == 0
    assert transport.rumble_calls == 0
    # PLAYERランプは0x05の復号のまま（rumbleで上書きしない）。
    assert mixin.player_lamp_canvas.fills[20] == "green yellow"
    assert mixin.player_lamp_canvas.fills[21] == "gray"
    assert "3" in mixin.rumble_label.text
    assert "4" in mixin.rumble_label.text


def test_patrol_cancel_on_close() -> None:
    """close/destroyでafter予約を取り消す（発火残し無し）。"""
    transport = _FakeBconTransport()
    window = _RecordingWindow()
    mixin = _make_patrol_mixin(transport, window)

    mixin._poll_player_lamp()
    after_id = getattr(mixin, "_player_lamp_after", None)
    assert after_id is not None

    mixin._cancel_player_lamp_patrol()

    assert after_id in window.cancelled
    assert getattr(mixin, "_player_lamp_after", None) is None


def test_patrol_transport_swap_resubscribes_without_rpc() -> None:
    """運搬器の差し替えで旧購読を外し新購読へ付け替える。getter無しでも落ちない。"""
    first = _CountingRpcTransport()
    second = _NoGetterTransport()
    holder: dict[str, Any] = {"transport": first}
    mixin = _make_patrol_mixin(None, _RecordingWindow())
    mixin._bcon_transport = lambda: holder["transport"]  # type: ignore[method-assign]

    mixin._poll_player_lamp()
    assert len(first.rx_subs) == 1

    holder["transport"] = second
    mixin._poll_player_lamp()  # getter無しでも落ちない・RPCしない。

    assert len(first.rx_subs) == 0
    assert len(second.rx_subs) == 1
    assert first.player_info_calls == 0
    assert first.rumble_calls == 0
    assert "なし" in mixin.rumble_label.text
