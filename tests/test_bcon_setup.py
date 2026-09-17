"""Bcon設定小窓のhostベクタ（実機不要・Task 6）。

窓自体は作らない（ヘッドレス）。純粋助けと接続手順（開→HELLO→
live開始）を偽transportで固める。Tkを使う箇所は __new__＋窓 stub で
閉鎖規律（溜め捨て・閉鎖後put禁止）だけ見る。
"""

from pathlib import Path
from typing import Any


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
