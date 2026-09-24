"""Bcon設定小窓のhostベクタ（実機不要・Task 6）。

窓自体は作らない（ヘッドレス）。純粋助けと接続手順（開→HELLO→
live開始）を偽transportで固める。Tkを使う箇所は __new__＋窓 stub で
閉鎖規律（溜め捨て・閉鎖後put禁止）だけ見る。
"""

from pathlib import Path
from typing import Any

import pytest


def test_bcon_setup_imports_without_touching_wake():
    import importlib.util
    from pathlib import Path

    assert Path("SerialController/BconSetup.py").exists()
    assert Path("SerialController/WakeSetup.py").exists()
    spec = importlib.util.spec_from_file_location(
        "bcon_setup", "SerialController/BconSetup.py"
    )
    assert spec is not None and spec.loader is not None


def test_bcon_setup_does_not_import_wake() -> None:
    """並置であって分岐・委譲ではない。WakeSetupを読まない。"""
    src = Path("SerialController/BconSetup.py").read_text(encoding="utf-8")
    assert "from WakeSetup" not in src
    assert "import WakeSetup" not in src
    assert "Bcon設定" in src


def test_bcon_setup_class_shape() -> None:
    """WakeSetupと同形の窓口（作業スレッド＋after還流の部品）を持つ。"""
    from BconSetup import BconSetup

    for name in (
        "close",
        "_poll",
        "_run",
        "_guarded",
        "_transport",
        "_on_connect",
        "_on_capture",
        "_on_beacon",
        "_on_status",
        "_on_ping",
        "_on_wired_show",
        "_on_wired_off",
        "_on_wired_on",
        "_on_clear",
        "_on_delete_keys",
        "_on_color",
        "_update_player_lamps",
        "_bind_player_info",
        "_unbind_player_info",
        "_on_rx_frame",
    ):
        assert hasattr(BconSetup, name), name


def test_bcon_setup_close_drains_and_ignores_after_close() -> None:
    """閉じたら溜めを捨て、閉じた後の届けは積まない（WakeSetupと同一規律）。"""
    import queue as _q

    from BconSetup import BconSetup

    obj = BconSetup.__new__(BconSetup)
    obj._sender = object()
    obj._queue = _q.Queue()
    obj._busy = True
    obj._stop = False
    obj._closed = False

    class StubWindow:
        def winfo_exists(self) -> bool:
            return False

        def destroy(self) -> None:
            return None

        def after(self, _ms: int, _fn: Any) -> Any:
            return None

    obj.window = StubWindow()  # type: ignore[attr-defined]
    obj._queue.put(("log", "あと"))
    obj._queue.put(("done", ""))
    obj.close()
    assert obj._busy is False
    assert obj._queue.empty()
    obj._guarded(lambda: None)
    assert obj._queue.empty()
    assert obj._busy is False


def test_parse_capture_seconds() -> None:
    from BconSetup import parse_capture_seconds

    assert parse_capture_seconds("15") == 15
    assert parse_capture_seconds(1) == 1
    assert parse_capture_seconds("60") == 60
    assert parse_capture_seconds(" 30 ") == 30
    assert parse_capture_seconds("0") is None
    assert parse_capture_seconds("61") is None
    assert parse_capture_seconds("abc") is None
    assert parse_capture_seconds("") is None
    assert parse_capture_seconds(None) is None


def test_parse_color_hex_and_build_payload() -> None:
    from BconSetup import build_color_payload, parse_color_hex

    assert parse_color_hex("ff0000") == (255, 0, 0)
    assert parse_color_hex("#00FF00") == (0, 255, 0)
    assert parse_color_hex("0000ff") == (0, 0, 255)
    assert parse_color_hex("fff") is None
    assert parse_color_hex("xyz123") is None
    assert parse_color_hex("") is None
    payload = build_color_payload([(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)])
    assert payload == bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0])
    assert len(payload) == 12
    try:
        build_color_payload([(0, 0, 0)] * 3)
    except ValueError:
        pass
    else:
        raise AssertionError("3色は12BにならないためValueErrorのはず")


def test_describe_bcon_errcode() -> None:
    from BconSetup import config_reject_type, describe_bcon_errcode

    assert "正常" in describe_bcon_errcode(0x00)
    assert "CRC" in describe_bcon_errcode(0x02)
    assert "CAPTURE" in describe_bcon_errcode(0x10)
    assert "BEACON" in describe_bcon_errcode(0x11)
    assert "WIRED" in describe_bcon_errcode(0x14)
    assert "不明" in describe_bcon_errcode(0xFF)
    assert config_reject_type(0x10) == 0x30
    assert config_reject_type(0x11) == 0x31
    assert config_reject_type(0x14) == 0x34
    assert config_reject_type(0x00) is None
    assert config_reject_type(0x06) is None


def test_format_status_flags() -> None:
    from BconSetup import format_status_flags

    assert "有線" in format_status_flags(0x20)
    wired_off = format_status_flags(0x03)
    assert "USB" in wired_off
    assert "Switch" in wired_off
    assert "有線" not in wired_off
    assert "なし" in format_status_flags(0x00)


def test_decode_player_lamp_bitmask() -> None:
    """プレイヤーLEDの下位4bitをそのまま4つの真偽へ（並べ替え無し）。"""
    from BconSetup import decode_player_lamp

    # bit0=LED1, bit1=LED2, bit2=LED3, bit3=LED4。0x00-0x0Fを総当たり。
    for value in range(0x10):
        assert decode_player_lamp(value) == (
            bool(value & 0x01),
            bool(value & 0x02),
            bool(value & 0x04),
            bool(value & 0x08),
        )
    # 代表値の抜き取り。
    assert decode_player_lamp(0x00) == (False, False, False, False)
    assert decode_player_lamp(0x01) == (True, False, False, False)
    assert decode_player_lamp(0x03) == (True, True, False, False)
    assert decode_player_lamp(0x07) == (True, True, True, False)
    assert decode_player_lamp(0x0F) == (True, True, True, True)
    assert decode_player_lamp(0x02) == (False, True, False, False)
    assert decode_player_lamp(0x05) == (True, False, True, False)
    assert decode_player_lamp(0x0A) == (False, True, False, True)


def test_decode_player_flags_imu_vib() -> None:
    """プレイヤーflagsはbit0=IMU・bit1=振動のみ。予約上位bitは無視。"""
    from BconSetup import decode_player_flags

    assert decode_player_flags(0x00) == (False, False)
    assert decode_player_flags(0x01) == (True, False)
    assert decode_player_flags(0x02) == (False, True)
    assert decode_player_flags(0x03) == (True, True)
    # 予約bit（bit2-7）は見ない: 0xFC=1111_1100 -> (False, False)。
    assert decode_player_flags(0xFC) == (False, False)
    assert decode_player_flags(0xFF) == (True, True)


def test_player_lamp_s2_edge_no_raise() -> None:
    """S2端: 予約/空/1byteでも落とさない（据え置き or 全消灯）。

    GREENで窓口 _update_player_lamps(payload) を足すとき、b"" や1byte・
    予約値は「据え置き or 全消灯」で例外にしない契約にする。ここでは
    その手前の復号段が全byteで例外を出さないことだけ固める。
    """
    from BconSetup import decode_player_flags, decode_player_lamp

    for value in range(0x100):
        lamp = decode_player_lamp(value)
        assert len(lamp) == 4
        assert all(isinstance(bit, bool) for bit in lamp)
        flags = decode_player_flags(value)
        assert len(flags) == 2
        assert all(isinstance(bit, bool) for bit in flags)


def test_is_bcon_transport() -> None:
    from BconSetup import is_bcon_transport

    class Bcon:
        name = "bcon"
        capability = "BCON_STATE"

    class Legacy:
        name = "legacy_text"
        capability = "TEXT_ROW"

    assert is_bcon_transport(Bcon()) is True
    assert is_bcon_transport(Legacy()) is False
    assert is_bcon_transport(None) is False
    assert is_bcon_transport(object()) is False


def test_send_config_frame_uses_session_mouth() -> None:
    from BconSetup import send_config_frame

    class Fake:
        def __init__(self) -> None:
            self.sent: list[tuple[int, bytes]] = []

        def _send_session_frame(self, type_: int, payload: bytes, note: str) -> bool:
            _ = note
            self.sent.append((type_, bytes(payload)))
            return True

    fake = Fake()
    assert send_config_frame(fake, 0x30, b"\x0f") is True
    assert fake.sent == [(0x30, b"\x0f")]
    # 会話口が無ければ送らずFalse（落とさない）。
    assert send_config_frame(object(), 0x30, b"\x0f") is False
    assert send_config_frame(None, 0x30, b"\x0f") is False

    class Raiser:
        def _send_session_frame(self, type_: int, payload: bytes, note: str) -> bool:
            raise RuntimeError("boom")

    assert send_config_frame(Raiser(), 0x30, b"\x0f") is False


class _FakeBconTransport:
    """接続手順の記録用。線には触らない。"""

    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self, hello_ok: bool = True, opened: bool = True) -> None:
        self.calls: list[str] = []
        self._opened = opened
        self._hello_ok = hello_ok
        self.sent: list[tuple[int, bytes]] = []
        self.rx_subs: list[Any] = []
        self.rx_unsub_calls = 0
        self.player_info = b""
        self.status: dict[str, int] = {
            "flags": 0,
            "last_seq": 0,
            "err_crc": 0,
            "err_drop": 0,
            "errcode": 0,
        }

    def is_open(self) -> bool:
        return self._opened

    def stop_live_loop(self) -> bool:
        self.calls.append("stop")
        return True

    def hello(self, timeout: float = 3.0) -> bool:
        _ = timeout
        self.calls.append("hello")
        return self._hello_ok

    def start_live_loop(self, interval_s: float = 1.0 / 120.0) -> bool:
        _ = interval_s
        self.calls.append("start")
        return True

    def _send_session_frame(self, type_: int, payload: bytes, note: str) -> bool:
        _ = note
        self.sent.append((type_, bytes(payload)))
        return True

    def subscribe_rx(self, func: Any) -> Any:
        # 実物BconTransportと同じく重複は足さない（同一の手は1つ）。
        if func not in self.rx_subs:
            self.rx_subs.append(func)

        def _unsub() -> None:
            self.rx_unsub_calls += 1
            if func in self.rx_subs:
                self.rx_subs.remove(func)

        return _unsub

    def emit_frame(self, type_: int, payload: bytes) -> None:
        """RXポンプの代役。購読者へその場で1フレーム渡す（別スレッド相当）。"""
        for func in list(self.rx_subs):
            func((type_, bytes(payload), 0))

    def last_player_info(self) -> bytes:
        return bytes(self.player_info)

    def request_status(self, timeout: float = 1.0) -> dict[str, int]:
        _ = timeout
        return dict(self.status)


class _FakeSender:
    def __init__(self, transport: Any) -> None:
        self.transport = transport
        self.calls: list[str] = []

    def stopLiveWorker(self, timeout: float = 1.0) -> bool:
        _ = timeout
        self.calls.append("worker_stop")
        return True

    def startLiveWorker(self, transport: Any = None) -> bool:
        _ = transport
        self.calls.append("worker_start")
        return True


def test_connect_bcon_hello_before_live() -> None:
    """開→HELLO→live開始の順。HELLO_ACKの前にSTATEは流さない。"""
    from BconSetup import connect_bcon

    transport = _FakeBconTransport(hello_ok=True)
    sender = _FakeSender(transport)
    logs: list[str] = []
    assert connect_bcon(transport, logs.append, sender) is True
    # 止めて→HELLO→起こすの順。STATE抑止が先。
    assert transport.calls == ["stop", "hello", "start"]
    assert sender.calls == ["worker_stop", "worker_start"]
    assert any("HELLO" in text for text in logs)


def test_connect_bcon_hello_fail_never_starts() -> None:
    """HELLO不通・版違いはNEUTRAL維持＋止める。liveは起こさない。"""
    from BconSetup import connect_bcon

    transport = _FakeBconTransport(hello_ok=False)
    sender = _FakeSender(transport)
    logs: list[str] = []
    assert connect_bcon(transport, logs.append, sender) is False
    assert transport.calls == ["stop", "hello"]
    assert "start" not in transport.calls
    assert sender.calls == ["worker_stop"]
    assert "worker_start" not in sender.calls
    assert any("NEUTRAL" in text for text in logs)


def test_connect_bcon_refuses_non_bcon_and_closed() -> None:
    from BconSetup import connect_bcon

    logs: list[str] = []

    class Legacy:
        name = "legacy_text"
        capability = "TEXT_ROW"
        called = False

        def hello(self, timeout: float = 3.0) -> bool:
            _ = timeout
            Legacy.called = True
            return True

    assert connect_bcon(Legacy(), logs.append, None) is False
    assert Legacy.called is False

    closed = _FakeBconTransport(hello_ok=True, opened=False)
    assert connect_bcon(closed, logs.append, None) is False
    assert closed.calls == []
    assert connect_bcon(None, logs.append, None) is False


def test_connect_bcon_never_raises() -> None:
    from BconSetup import connect_bcon

    class Boom:
        name = "bcon"
        capability = "BCON_STATE"

        def is_open(self) -> bool:
            return True

        def stop_live_loop(self) -> bool:
            raise RuntimeError("stop boom")

        def hello(self, timeout: float = 3.0) -> bool:
            raise RuntimeError("hello boom")

        def start_live_loop(self, interval_s: float = 1.0 / 120.0) -> bool:
            raise RuntimeError("start boom")

    logs: list[str] = []
    assert connect_bcon(Boom(), logs.append, None) is False


def test_transport_default_stays_legacy() -> None:
    """設定面は註記だけ。既定値は legacy_text のまま（既存iniを変えない）。"""
    from config import default_sections

    assert default_sections()["Transport"]["name"] == "legacy_text"
    src = Path("SerialController/config.py").read_text(encoding="utf-8")
    assert "bcon" in src


def test_menubar_opens_bcon_setup() -> None:
    """Bcon設定がメニューから開ける。WakeSetupは非推奨でメニューに出さない。"""
    from Menubar import PokeController_Menubar

    assert hasattr(PokeController_Menubar, "OpenBconSetup")
    assert hasattr(PokeController_Menubar, "closingBconSetup")
    assert not hasattr(PokeController_Menubar, "OpenWakeSetup")
    src = Path("SerialController/Menubar.py").read_text(encoding="utf-8")
    assert "Bcon設定" in src
    assert "Switch2 Wake設定" not in src
    assert "WakeSetup" not in src


# -- Task 4: PLAYER_INFO の実況配線（subscribe→queue→_poll→ランプ）--------


class _StubLampCanvas:
    """Canvasの偽物。itemconfigure(item, fill=...) の色だけ覚える。"""

    def __init__(self) -> None:
        self.fills: dict[int, str] = {}

    def itemconfigure(self, item: int, fill: str) -> None:
        self.fills[item] = fill


class _StubLabel:
    """Labelの偽物。configure(text=...) だけ覚える。"""

    def __init__(self) -> None:
        self.text = ""

    def configure(self, text: str) -> None:
        self.text = text


class _StubLogText:
    """ログ欄の偽物。挿入だけ覚える。"""

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
    """Toplevelの偽物。after は予約せず捨てる（mainloopなし）。"""

    def __init__(self) -> None:
        self.exists = True

    def winfo_exists(self) -> bool:
        return self.exists

    def destroy(self) -> None:
        self.exists = False

    def after(self, ms: int, func: Any) -> Any:
        _ = (ms, func)
        return None


def _make_headless_setup(transport: Any) -> Any:
    """__new__＋stubで、窓を作らず queue とランプ描画先だけ持つ検査体を作る。"""
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
    obj._lamp_canvas = _StubLampCanvas()
    obj._imu_label = _StubLabel()
    obj._vib_label = _StubLabel()
    obj._log = _StubLogText()
    obj.window = _StubWindow()
    return obj


def _assert_lamps(obj: Any, lamp_byte: int) -> None:
    """ランプ4灯の色がLED下位4bitの復号と一致することを確かめる。"""
    from BconSetup import (
        PLAYER_LAMP_OFF_COLOR,
        PLAYER_LAMP_ON_COLOR,
        decode_player_lamp,
    )

    for item, is_on in zip(obj._lamp_items, decode_player_lamp(lamp_byte)):
        expected = PLAYER_LAMP_ON_COLOR if is_on else PLAYER_LAMP_OFF_COLOR
        assert obj._lamp_canvas.fills[item] == expected


def test_player_info_frame_reaches_lamps_via_queue_and_poll() -> None:
    """RX→queue→手動_pollでランプが更新される（mainloopなし・別スレッド相当）。"""
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    obj._bind_player_info(transport)
    assert len(transport.rx_subs) == 1

    # ポンプスレッドの代役。この時点では widget に触れない（queue だけ）。
    transport.emit_frame(0x23, b"\x05\x03")
    assert obj._queue.qsize() == 1
    assert obj._lamp_canvas.fills == {}
    assert obj._imu_label.text == ""

    # GUIスレッドの _poll を手で1回まわす（after予約は stub が捨てる）。
    obj._poll()
    _assert_lamps(obj, 0x05)
    assert obj._imu_label.text == "IMU: あり"
    assert obj._vib_label.text == "振動: あり"

    # PLAYER_INFO以外（STATUS 0x20）は拾わない。
    transport.emit_frame(0x20, b"\x00" * 7)
    assert obj._queue.empty()

    # close で解除され、解除後・閉鎖後のフレームは積まれない。
    obj.close()
    assert transport.rx_unsub_calls == 1
    assert transport.rx_subs == []
    transport.emit_frame(0x23, b"\x0f\x00")
    obj._on_rx_frame((0x23, b"\x0f\x00", 0))
    assert obj._queue.empty()

    # 閉じた後に張り直さない（遅れて来た bind を持ち込まない）。
    obj._bind_player_info(transport)
    assert transport.rx_subs == []


def test_on_status_refreshes_lamps_from_last_player_info() -> None:
    """状態確認は last_player_info() をログとランプ表示の両方へ反映する。"""
    from BconSetup import PLAYER_LAMP_OFF_COLOR

    transport = _FakeBconTransport()
    transport.status = {
        "flags": 0x03,
        "last_seq": 0,
        "err_crc": 0,
        "err_drop": 0,
        "errcode": 0,
    }
    transport.player_info = b"\x0a\x01"
    obj = _make_headless_setup(transport)
    obj._run = lambda func: func()  # 作業スレッドを起こさずその場で走らせる。

    obj._on_status()
    obj._poll()
    _assert_lamps(obj, 0x0A)
    assert obj._imu_label.text == "IMU: あり"
    assert obj._vib_label.text == "振動: なし"
    assert "PLAYER_INFO: 0a 01" in obj._log.text

    # 未受信（b""）は全消灯へ倒す（古い点灯を残さない）。
    transport.player_info = b""
    obj._on_status()
    obj._poll()
    assert set(obj._lamp_canvas.fills.values()) == {PLAYER_LAMP_OFF_COLOR}
    assert "PLAYER_INFO: （なし）" in obj._log.text


def test_player_info_rebinds_on_transport_replace_and_skips_legacy() -> None:
    """先が替わったら古い購読を外して張り直す。bcon以外の口には張らない。"""
    first = _FakeBconTransport()
    second = _FakeBconTransport()
    obj = _make_headless_setup(first)

    obj._bind_player_info(first)
    obj._bind_player_info(first)
    assert len(first.rx_subs) == 1  # 重複除けは実物側の責務

    obj._bind_player_info(second)
    assert first.rx_subs == []
    assert first.rx_unsub_calls == 1
    assert len(second.rx_subs) == 1

    class _LegacyRigged:
        name = "legacy_text"
        capability = "TEXT_ROW"

        def __init__(self) -> None:
            self.subscribed = False

        def subscribe_rx(self, func: Any) -> Any:
            _ = func
            self.subscribed = True

            def _unsub() -> None:
                return None

            return _unsub

    legacy = _LegacyRigged()
    obj._bind_player_info(legacy)
    assert legacy.subscribed is False
    assert obj._rx_transport is second

    obj.close()
    assert second.rx_subs == []
    assert second.rx_unsub_calls == 1


# -- Task: CONFIG直後の新規拒否検出（STATUS差分・diff verdictが来るまで赤）--


@pytest.mark.parametrize(
    ("post_errcode", "post_flags", "expect_w0", "expect_type_name", "what"),
    [
        # 有線中0x10→W0無線の案内あり。
        pytest.param(0x10, 0x20, True, "CAPTURE", "取込", id="wired-0x10"),
        # 有線中0x11→W0無線の案内あり。
        pytest.param(0x11, 0x20, True, "BEACON", "BEACON", id="wired-0x11"),
        # 無線ならW0文なし。
        pytest.param(0x10, 0x00, False, "CAPTURE", "取込", id="wireless-0x10"),
    ],
)
def test_config_result_detects_fresh_reject(
    post_errcode: int,
    post_flags: int,
    expect_w0: bool,
    expect_type_name: str,
    what: str,
) -> None:
    """送出前後のSTATUS差分で今回の拒否を見分ける（diff verdictまで赤）。"""
    import inspect

    from BconSetup import describe_bcon_errcode, format_status_block

    # Given: 送出前のSTATUS写し（正常・err_drop=5・last_seq=10）。
    pre_status = {
        "flags": 0x00,
        "last_seq": 10,
        "err_crc": 0,
        "err_drop": 5,
        "errcode": 0x00,
    }
    # 送出後のSTATUS（errcode変化＋err_drop/last_seq前進＋有線flag）。
    post_status = {
        "flags": post_flags,
        "last_seq": 11,
        "err_crc": 0,
        "err_drop": 6,
        "errcode": post_errcode,
    }

    transport = _FakeBconTransport()
    transport.status = dict(post_status)
    obj = _make_headless_setup(transport)

    # 差分経路の証跡: pre写しを受け取る口があること（今は無いので赤）。
    sig = inspect.signature(obj._config_result)
    assert "pre_status" in sig.parameters, (
        "pre snapshotを受け取るdiff verdict口がありません"
    )

    # When: 送出前後の差分で今回の拒否を判定する。
    obj._config_result(transport, what, pre_status=pre_status)

    kind, text = obj._queue.get_nowait()
    assert kind == "log"
    # Then: 今回の拒否として報告する（既存文言そのまま）。
    assert "拒否されました" in text
    assert expect_type_name in text
    # describe_bcon_errcodeの説明文を保つ（直書きにしない）。
    assert describe_bcon_errcode(post_errcode) in text
    # 有線中の0x10/0x11だけW0無線への案内を添える。
    if expect_w0:
        assert "W0無線" in text
    else:
        assert "W0" not in text
    # STATUS全体を末尾に添える。
    assert text.endswith(format_status_block(post_status))
    # pre起点の新規検出であること: post側の値（err_drop=6・last_seq=11）が載る。
    assert "err_drop=6" in text
    assert "last_seq=11" in text


# -- _config_result の端行列（実機不要・STATUS偽装で固定）------------------


def _config_result_drain_text(obj: Any) -> tuple[str, str]:
    """_config_result が積んだ1件を取り出す（log1件のはず）。"""
    kind, text = obj._queue.get_nowait()
    assert obj._queue.empty()
    assert kind == "log"
    return kind, text


def test_config_result_edge_matrix() -> None:
    """CONFIG直後STATUS読みの端を固める（取込の口で代表）。

    (a) 無応答→送りました。STATUS応答がありません。/(b) 不正→送りました。＋
    状態ブロック/(c) errcode=0x00→受け付けました。前置きは一字一句固定。
    """
    from BconSetup import format_status_block

    what = "取込"

    class _NoneStatus:
        """STATUSを返さない口（無応答の代役）。"""

        def request_status(self, timeout: float = 1.0) -> Any:
            _ = timeout
            return None

    class _RaiseStatus:
        """STATUS読みで落とす口（例外でも落とさない契約）。"""

        def request_status(self, timeout: float = 1.0) -> Any:
            _ = timeout
            raise RuntimeError("boom")

    class _DictStatus:
        """決まった辞書を返す口（中身は差し替え式）。"""

        def __init__(self, status: Any) -> None:
            self._status = status

        def request_status(self, timeout: float = 1.0) -> Any:
            _ = timeout
            return self._status

    # (a) 無応答・例外・口なしは同じ一文（完全一致で固める）。
    no_answer = f"{what}を送りました。STATUS応答がありません。"
    for transport in (_NoneStatus(), _RaiseStatus(), object(), None):
        obj = _make_headless_setup(None)
        obj._config_result(transport, what)
        _, text = _config_result_drain_text(obj)
        assert text == no_answer

    # (b) 不正STATUS（flags型違い・dict外し）は送りました。＋状態ブロック。
    malformed: list[Any] = [
        {"errcode": 0, "flags": "xx"},  # 型違い（int化でValueError）
        {"errcode": 0, "flags": None},  # 型違い（int化でTypeError）
        {"errcode": "zz", "flags": 0},  # 型違い（errcode側）
        "junk",  # dict外し（.getが無くAttributeError）
        [0x20],  # dict外し（list）
    ]
    for status in malformed:
        obj = _make_headless_setup(None)
        obj._config_result(_DictStatus(status), what)
        _, text = _config_result_drain_text(obj)
        assert text.startswith(f"{what}を送りました。")
        assert format_status_block(status) in text

    # (b2) 鍵欠け {} の実測：.get既定（errcode=0扱い）で受け付けました側へ
    # 倒れる。送りました側へ寄せたいなら新規配管が要る（現状はここで固定）。
    obj = _make_headless_setup(None)
    obj._config_result(_DictStatus({}), what)
    _, text = _config_result_drain_text(obj)
    assert text.startswith(f"{what}を受け付けました。")

    # (c) 正常（errcode=0x00・動かず）は受け付けました。＋状態ブロック。
    ok_status = {
        "flags": 0,
        "last_seq": 1,
        "err_crc": 0,
        "err_drop": 0,
        "errcode": 0,
    }
    obj = _make_headless_setup(None)
    obj._config_result(_DictStatus(ok_status), what)
    _, text = _config_result_drain_text(obj)
    assert text == f"{what}を受け付けました。{format_status_block(ok_status)}"


def test_config_result_ignores_stale_errcode() -> None:
    """粘着errcodeは今回のせいにしない。前後で計数が動かなければ受け付け扱い。"""
    transport = _FakeBconTransport()
    # 送出前から残る古いerrcode=0x03。err_dropもlast_seqも動かない。
    transport.status = {
        "flags": 0x00,
        "last_seq": 7,
        "err_crc": 0,
        "err_drop": 2,
        "errcode": 0x03,
    }
    obj = _make_headless_setup(transport)
    # 送出前の写し。事後STATUSも同じ値（粘着・不動）を返す。
    pre = dict(transport.status)
    # 新しい差分対応の入口。まだ無いので現行ではTypeErrorで落ちる。
    obj._config_result(transport, "W0", pre)  # type: ignore[arg-type]
    obj._poll()
    # 今回は何も悪くしていないので受け付け扱いになるはず。
    assert "受け付けました" in obj._log.text
    assert "拒否されました" not in obj._log.text


# -- Task: 送出前STATUS写しの配線（caller job → _config_result まで赤）--------
# 方針メモ: BEACON直書き盤（_on_beacon :951-996）は今回触らない。_config_result
# へ寄せる置換は別件とする（有線0x10/0x11案内の文言差があるため）。
# 本盤は _on_capture / _on_color の2口だけ pre_status=pre を渡す。


class _SnapshotBconTransport(_FakeBconTransport):
    """送出前写しを残す口。線に触れず順序だけ記録する。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.events: list[str] = []
        self.pre_status: dict[str, int] = {
            "flags": 0x00,
            "last_seq": 7,
            "err_crc": 0,
            "err_drop": 2,
            "errcode": 0x03,
        }

    def last_status(self) -> dict[str, int]:
        # 実物同様、呼ぶだけで線に触れない写しを返す。
        self.events.append("snapshot")
        return dict(self.pre_status)

    def _send_session_frame(self, type_: int, payload: bytes, note: str) -> bool:
        _ = note
        self.events.append("send")
        return super()._send_session_frame(type_, payload, note)


class _StubSeconds:
    """取込秒数欄の偽物。get() だけ返す。"""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def get(self) -> Any:
        return self._raw


def _run_capture_job_headless(obj: Any) -> None:
    """作業スレッドを起こさずその場で走らせ、溜めを画面へ流す。"""
    obj._run = lambda func: func()
    obj._on_capture()
    obj._poll()


def test_capture_job_passes_pre_status_snapshot() -> None:
    """取込jobが送出前の写しを _config_result へ渡す（順序つき・赤）。"""
    from BconSetup import T_CAPTURE_START

    # Given: 粘着 0x03 が送出前から残る（err_drop=2・last_seq=7で不動）。
    transport = _SnapshotBconTransport()
    transport.status = dict(transport.pre_status)
    obj = _make_headless_setup(transport)
    obj._seconds = _StubSeconds("15")  # type: ignore[attr-defined]

    seen: dict[str, Any] = {}
    orig = obj._config_result

    def _spy(transport_arg: Any, what: str, pre_status: Any = None) -> None:
        seen["pre_status"] = pre_status
        seen["what"] = what
        orig(transport_arg, what, pre_status=pre_status)

    obj._config_result = _spy  # type: ignore[method-assign]

    # When: 取込jobをその場で走らせる。
    _run_capture_job_headless(obj)

    # Then: 写しは送出より先に取り、今回分として渡す。
    assert transport.events[:2] == ["snapshot", "send"]
    assert seen["what"] == "取込"
    assert seen["pre_status"] == transport.pre_status
    assert (T_CAPTURE_START, b"\x0f") in transport.sent
    # 粘着errcodeは今回のせいにしない（受け付け扱い＋注記）。
    assert "受け付けました" in obj._log.text
    assert "過去の記録" in obj._log.text
    assert "拒否されました" not in obj._log.text


def test_capture_job_reports_fresh_reject_with_diff() -> None:
    """送出で計数が動いたら今回の拒否として出す（新規検出・赤）。"""
    # Given: 送出前は正常、送出後は err_drop が進んだ拒否。
    transport = _SnapshotBconTransport()
    transport.pre_status = {
        "flags": 0x00,
        "last_seq": 10,
        "err_crc": 0,
        "err_drop": 5,
        "errcode": 0x00,
    }
    transport.status = {
        "flags": 0x00,
        "last_seq": 11,
        "err_crc": 0,
        "err_drop": 6,
        "errcode": 0x03,
    }
    obj = _make_headless_setup(transport)
    obj._seconds = _StubSeconds("15")  # type: ignore[attr-defined]

    # When: 取込jobをその場で走らせる。
    _run_capture_job_headless(obj)

    # Then: 今回の拒否として報告する。
    assert transport.events[:2] == ["snapshot", "send"]
    assert "拒否されました" in obj._log.text
    assert "err_drop=6" in obj._log.text


# -- Task: RX→UI pipe の洪水対策（bounded + flush cap + last-wins合流）--
# 方針: DropOldestQueue で bound、_poll は1 tickの取出しに上限、
# rumble/player_info は tick 内の最新だけ適用（≤1 UI更新/key）、
# Text は単一batched insert＋行cap＋at-bottom時のみsee。
# 溢れは「... N行省略 ...」で可視化し、黙って捨てない（合流の畳み込み除く）。


class _StubCapLogText:
    """行cap検査用のログ欄偽物。yview/index/delete/seeを持つ。"""

    def __init__(self, at_bottom: bool = True) -> None:
        self.text = ""
        self.see_calls = 0
        self._at_bottom = at_bottom

    def configure(self, **kwargs: Any) -> None:
        _ = kwargs

    def insert(self, index: Any, text: str) -> None:
        _ = index
        self.text += text

    def yview(self) -> tuple[float, float]:
        return (0.0, 1.0) if self._at_bottom else (0.0, 0.5)

    def index(self, spec: str) -> str:
        _ = spec
        # LogPane.trim と同じ「末尾に空行が付く」数え方に寄せる。
        total = self.text.count("\n")
        return f"{max(total, 1)}.0"

    def delete(self, start: str, end: str) -> None:
        _ = start
        # "end-{n}l" から残す行数を読み、末尾n-1行だけ残す。
        keep = 0
        try:
            keep = int(str(end).split("end-")[1].split("l")[0]) - 1
        except (IndexError, ValueError):
            keep = 0
        lines = self.text.split("\n")
        if keep > 0:
            lines = lines[-keep - 1 :] if len(lines) > keep + 1 else lines
        self.text = "\n".join(lines)

    def see(self, index: Any) -> None:
        _ = index
        self.see_calls += 1


def _pipe_caps() -> tuple[int, int, int]:
    """(queue bound, flush cap, text行cap)。実装前は文字通りの上限値で赤にする。"""
    import BconSetup as _m

    return (
        int(getattr(_m, "BCON_QUEUE_MAX", 1024)),
        int(getattr(_m, "BCON_FLUSH_MAX", 128)),
        int(getattr(_m, "BCON_MAX_LINES", 1000)),
    )


def test_flood_rx_queue_bounded_and_overflow_visible() -> None:
    """溢れたら古い方を捨て、捨てた件数を「... N行省略 ...」で可視化する。"""
    from LogPane import DropOldestQueue

    src = Path("SerialController/BconSetup.py").read_text(encoding="utf-8")
    assert "DropOldestQueue" in src

    queue_max, _, _ = _pipe_caps()
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    obj._queue = DropOldestQueue(maxsize=16)
    for i in range(32):
        obj._queue.put(("log", f"line-{i:02d}"))
    assert obj._queue.qsize() <= 16
    obj._poll()
    assert "省略" in obj._log.text
    # 新しい方は残る（古い方から捨てる）。
    assert "line-31" in obj._log.text


def test_flood_rumble_playerinfo_last_wins_single_update() -> None:
    """100連打の rumble/player_info は tick 内の最新だけ適用（≤1更新/key）。"""
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    obj._bind_player_info(transport)

    lamp_calls: list[tuple[Any, Any]] = []
    orig_lamps = obj._update_player_lamps

    def _spy(lamp: Any, flags: Any = None) -> None:
        lamp_calls.append((lamp, flags))
        orig_lamps(lamp, flags)

    obj._update_player_lamps = _spy  # type: ignore[method-assign]

    for i in range(100):
        transport.emit_frame(0x22, bytes([i & 0xFF, (255 - i) & 0xFF]))
        transport.emit_frame(0x23, bytes([i & 0x0F, i & 0x03]))
    obj._poll()

    assert len(lamp_calls) <= 1
    assert obj._log.text.count("振動 L=") <= 1
    # 最新だけが生かされる（i=99 の値）。
    assert "L=99" in obj._log.text
    assert "R=156" in obj._log.text
    _assert_lamps(obj, 99 & 0x0F)
    assert obj._imu_label.text == "IMU: あり"
    assert obj._vib_label.text == "振動: あり"


def test_flood_log_text_line_cap() -> None:
    """Text は行capを超えて伸びない（単一batched insert＋trim）。"""
    _, _, max_lines = _pipe_caps()
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    obj._log = _StubCapLogText()

    for tick in range(8):
        for i in range(250):
            obj._queue.put(("log", f"tick{tick}-{i:03d}"))
        obj._poll()
    total = obj._log.text.count("\n")
    assert total <= max_lines + 2


def test_flood_poll_tick_capped_with_counters() -> None:
    """1 tickの吐き出しに上限があり、数え（drain・ms・qsize）が上限を証跡する。"""
    import time as _time

    _, flush_max, _ = _pipe_caps()
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    for i in range(2000):
        obj._queue.put(("log", f"flood-{i:04d}"))
    obj._ensure_counters()

    started = _time.perf_counter()
    obj._poll()
    elapsed_ms = (_time.perf_counter() - started) * 1000.0

    snap = obj.counters_snapshot()
    assert snap["poll_last_drained"] <= flush_max
    assert not obj._queue.empty()  # 残りは次tickへ（1回で抱えない）。
    assert snap["queue_max_qsize"] >= 2000 or snap["queue_max_qsize"] > flush_max
    assert snap["poll_last_ms"] < 1000.0
    assert elapsed_ms < 1000.0


def test_capture_job_without_last_status_mouth_never_raises() -> None:
    """last_status口の無い旧運搬器でも落とさず送る（None退避・赤）。"""
    # Given: last_status を持たない旧い口（_FakeBconTransport素体）。
    transport = _FakeBconTransport()
    transport.status = {
        "flags": 0x00,
        "last_seq": 1,
        "err_crc": 0,
        "err_drop": 0,
        "errcode": 0x00,
    }
    assert not hasattr(transport, "last_status")
    obj = _make_headless_setup(transport)
    obj._seconds = _StubSeconds("15")  # type: ignore[attr-defined]

    seen: dict[str, Any] = {}
    orig = obj._config_result

    def _spy(transport_arg: Any, what: str, pre_status: Any = None) -> None:
        seen["pre_status"] = pre_status
        orig(transport_arg, what, pre_status=pre_status)

    obj._config_result = _spy  # type: ignore[method-assign]

    # When: 取込jobをその場で走らせる（例外なく通ること）。
    _run_capture_job_headless(obj)

    # Then: 送りは通し、写し無し（None）で判定へ倒す。
    assert transport.sent != []
    assert seen["pre_status"] is None
    assert "受け付けました" in obj._log.text


# -- Task: PLAYER_INFO flags bit2 cap_saved + 取込期限の成功表示 --------


def test_decode_player_save_bit2() -> None:
    """PLAYER_INFO flags bit2=cap_savedだけ見る。予約bit3-7は無視。"""
    from BconSetup import decode_player_save

    # Given: bit2だけが保存の証拠。
    # When/Then: 0x04で真、0x00-0x03で偽。
    assert decode_player_save(0x04) is True
    assert decode_player_save(0x00) is False
    assert decode_player_save(0x01) is False
    assert decode_player_save(0x02) is False
    assert decode_player_save(0x03) is False
    # bit2あり＋IMU/振動ありも真。
    assert decode_player_save(0x05) is True
    assert decode_player_save(0x06) is True
    assert decode_player_save(0x07) is True
    # 予約bit3-7は無視: bit2なしは偽、ありは真。
    assert decode_player_save(0xF8) is False
    assert decode_player_save(0xF0) is False
    assert decode_player_save(0xFB) is False
    assert decode_player_save(0xFF) is True
    assert decode_player_save(0xF4) is True


def _capture_arm_headless(obj: Any, seconds: int = 15) -> dict[str, Any]:
    """取込期限のafter予約を捕まえる。実Tkなしで壁時計の遅延だけ見る。"""
    scheduled: dict[str, Any] = {}
    calls: list[tuple[int, Any]] = []

    def _capture_after(ms: int, func: Any) -> Any:
        calls.append((ms, func))
        # _pollの120ms再予約と期限予約が同居するため期限だけ抜く。
        if ms > 1000:
            scheduled["ms"] = ms
            scheduled["func"] = func
            return "deadline-id"
        return None

    obj.window.after = _capture_after  # type: ignore[method-assign]
    obj._run = lambda func: func()
    obj._seconds = _StubSeconds(str(seconds))  # type: ignore[attr-defined]
    obj._on_capture()
    obj._poll()
    return scheduled


def test_capture_deadline_success_shows_save_text() -> None:
    """期限までにbit2を見たら保存文を出す（BEACONは見ない）。"""
    from BconSetup import SAVE_SUCCESS_TEXT

    # Given: 取込15秒で期限を張る。
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    scheduled = _capture_arm_headless(obj, 15)
    assert scheduled["ms"] == (15 + 5) * 1000

    # When: PLAYER_INFO flags bit2が届き、期限が来る。
    transport.emit_frame(0x23, b"\x00\x04")
    obj._poll()
    scheduled["func"]()
    obj._poll()

    # Then: 保存文だけ出す。
    assert SAVE_SUCCESS_TEXT in obj._log.text
    assert SAVE_SUCCESS_TEXT == "取込を保存しました。"


def test_capture_deadline_timeout_without_save() -> None:
    """期限までにbit2が無ければ取込終了の注意を出す。"""
    # Given: 取込15秒で期限を張る。保存の証拠は来ない。
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    scheduled = _capture_arm_headless(obj, 15)

    # When: 期限が来る（PLAYER_INFOなし）。
    scheduled["func"]()
    obj._poll()

    # Then: 保存文ではなく終了注意を出す。
    assert "取込期間が終了しましたが保存を確認できませんでした。" in obj._log.text
    assert "取込を保存しました。" not in obj._log.text


def test_capture_deadline_no_beacon_dependency() -> None:
    """BEACON送受では保存にしない。PLAYER_INFO bit2だけが証拠。"""
    from BconSetup import SAVE_SUCCESS_TEXT

    # Given: 取込15秒で期限を張る。
    transport = _FakeBconTransport()
    obj = _make_headless_setup(transport)
    scheduled = _capture_arm_headless(obj, 15)

    # When: BEACONを送り、RUMBLEが届いても、期限が来る。
    obj._run = lambda func: func()
    obj._on_beacon()
    obj._poll()
    transport.emit_frame(0x22, b"\x10\x20")
    obj._poll()
    scheduled["func"]()
    obj._poll()

    # Then: 保存文は出ない。
    assert SAVE_SUCCESS_TEXT not in obj._log.text
    assert "取込期間が終了しましたが保存を確認できませんでした。" in obj._log.text
