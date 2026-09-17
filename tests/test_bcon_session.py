"""bcon会話層のhostベクタ（実機不要・Task 4）。"""

import threading
import time


def _make_scripted_ser(feed_delay: float = 0.001):
    """読み書きの記録を持つ疑似シリアル。ポンプ横取り防止の単一読み口用。"""

    class ScriptedSer:
        is_open = True

        def __init__(self) -> None:
            self._chunks: list[bytes] = []
            self._lock = threading.Lock()
            self.written: list[bytes] = []
            self.baudrate = 1000000

        def read(self, n: int) -> bytes:
            _ = n
            with self._lock:
                if self._chunks:
                    return self._chunks.pop(0)
            time.sleep(feed_delay)
            return b""

        def feed(self, data: bytes) -> None:
            with self._lock:
                self._chunks.append(bytes(data))

        def write(self, data: bytes) -> int:
            with self._lock:
                self.written.append(bytes(data))
            return len(data)

    return ScriptedSer()


def test_bcon_hello_and_ping_with_scripted_ser():
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_HELLO_ACK, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    try:
        assert made.rx_pump_running() is True
        ser.feed(frame_build(T_HELLO_ACK, bytes([0x04, 0x00, 0x01, 0x00]), 0x00))
        assert made.hello(timeout=2.0) is True
    finally:
        made.stop_rx_pump()


def test_bcon_hello_sends_ver4_with_status_flag():
    from core.transport import create_transport
    from core.transport.bcon_protocol import (
        T_HELLO,
        T_HELLO_ACK,
        frame_build,
    )

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    stop = threading.Event()

    def _responder() -> None:
        seen = 0
        while not stop.is_set() and seen < 20:
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[seen:]:
                seen += 1
                if len(frame) >= 2 and frame[0] == 0xAB and frame[1] == T_HELLO:
                    ser.feed(
                        frame_build(T_HELLO_ACK, bytes([0x04, 0x00, 0x02, 0x00]), 0x00)
                    )
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        assert made.hello(timeout=2.0) is True
        assert len(ser.written) >= 1
        first = ser.written[0]
        assert first[0] == 0xAB
        assert first[1] == T_HELLO
        assert first[2] == 2
        assert first[3] == 0x04
        assert first[4] & 0x01 == 0x01
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_bcon_ping_returns_rtt_on_seq_echo():
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_PING, T_PONG, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    stop = threading.Event()

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[seen:]:
                seen += 1
                if len(frame) >= 4 and frame[0] == 0xAB and frame[1] == T_PING:
                    echo = frame[3]
                    ser.feed(frame_build(T_PONG, bytes([echo]), 0x09))
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        rtt = made.ping(timeout=2.0)
        assert rtt is not None
        assert 0.0 <= rtt < 2.0
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_bcon_hello_unsupported_holds_neutral_and_stops():
    from core.transport import create_transport
    from core.transport.bcon_protocol import (
        RESULT_UNSUPPORTED,
        T_HELLO,
        T_HELLO_ACK,
        T_NEUTRAL,
        frame_build,
    )

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    stop = threading.Event()

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[seen:]:
                seen += 1
                if len(frame) >= 2 and frame[0] == 0xAB and frame[1] == T_HELLO:
                    ser.feed(
                        frame_build(
                            T_HELLO_ACK,
                            bytes([0x03, 0x00, 0x01, RESULT_UNSUPPORTED]),
                            0x00,
                        )
                    )
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        assert made.hello(timeout=2.0) is False
        types = [f[1] for f in ser.written if len(f) >= 2 and f[0] == 0xAB]
        assert T_HELLO in types
        assert T_NEUTRAL in types
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_bcon_hello_timeout_returns_false_without_hang():
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    try:
        start = time.perf_counter()
        assert made.hello(timeout=0.3) is False
        assert time.perf_counter() - start < 2.0
    finally:
        made.stop_rx_pump()


def test_bcon_request_status_updates_last_status_and_player():
    from core.transport import create_transport
    from core.transport.bcon_protocol import (
        T_PLAYER_INFO,
        T_STATUS,
        T_STATUS_REQ,
        frame_build,
    )

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    assert made.last_status() == {
        "flags": 0,
        "last_seq": 0,
        "err_crc": 0,
        "err_drop": 0,
        "errcode": 0,
    }
    made.start_rx_pump()
    stop = threading.Event()

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[seen:]:
                seen += 1
                if len(frame) >= 2 and frame[0] == 0xAB and frame[1] == T_STATUS_REQ:
                    status = bytes([0x05, 0x07, 0x01, 0x00, 0x02, 0x00, 0x00])
                    ser.feed(frame_build(T_STATUS, status, 0x11))
                    ser.feed(frame_build(T_PLAYER_INFO, bytes([0x01, 0x02]), 0x12))
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        got = made.request_status(timeout=2.0)
        assert got is not None
        assert got["flags"] == 0x05
        assert got["last_seq"] == 0x07
        assert got["err_crc"] == 1
        assert got["err_drop"] == 2
        assert got["errcode"] == 0
        assert made.last_status() == got
        assert made.last_player_info() == bytes([0x01, 0x02])
        # 写しの変更が内部へ漏れないこと。
        got["flags"] = 0xFF
        assert made.last_status()["flags"] == 0x05
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_bcon_status_errcode_surfaces_but_keeps_latest():
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_STATUS, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    try:
        bad = bytes([0x00, 0x09, 0x03, 0x00, 0x00, 0x00, 0x02])
        ser.feed(frame_build(T_STATUS, bad, 0x21))
        deadline = time.perf_counter() + 2.0
        seen = made.last_status()
        while time.perf_counter() < deadline and seen["errcode"] != 0x02:
            time.sleep(0.005)
            seen = made.last_status()
        assert seen["errcode"] == 0x02
        assert seen["err_crc"] == 3
    finally:
        made.stop_rx_pump()


def test_bcon_rx_dispatch_reaches_subscriber_and_waiter():
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_PING, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    seen: list[tuple[int, bytes, int]] = []
    unsub = made.subscribe_rx(seen.append)
    try:
        ser.feed(frame_build(T_PING, b"", 0x33))
        got = made.wait_rx(T_PING, timeout=2.0)
        assert got is not None
        assert got[0] == T_PING
        assert got[2] == 0x33
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline and not seen:
            time.sleep(0.005)
        assert seen
        assert seen[0][0] == T_PING
    finally:
        try:
            unsub()
        except Exception:
            pass
        made.stop_rx_pump()


def test_bcon_session_never_raises_without_port():
    from core.transport import create_transport

    made = create_transport("bcon")
    made.ser = None
    assert made.hello(timeout=0.2) is False
    assert made.ping(timeout=0.2) is None
    assert made.request_status(timeout=0.2) is None
    assert made.baud_hunt([1000000], timeout_per=0.2) is None
    assert made.set_baud_index(3, timeout=0.3) is False
    assert made.set_wired_mode(True, timeout=0.2) is False
    assert set(made.last_status().keys()) == {
        "flags",
        "last_seq",
        "err_crc",
        "err_drop",
        "errcode",
    }


def test_bcon_baud_hunt_locks_two_consecutive():
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_HELLO, T_HELLO_ACK, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    stop = threading.Event()
    good_rate = 1000000

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
                rate = ser.baudrate
            for frame in pending[seen:]:
                seen += 1
                if (
                    len(frame) >= 2
                    and frame[0] == 0xAB
                    and frame[1] == T_HELLO
                    and rate == good_rate
                ):
                    ser.feed(
                        frame_build(T_HELLO_ACK, bytes([0x04, 0x00, 0x01, 0x00]), 0x00)
                    )
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        ser.baudrate = 115200
        locked = made.baud_hunt([115200, good_rate], timeout_per=0.5)
        assert locked == good_rate
        assert ser.baudrate == good_rate
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_bcon_baud_hunt_fails_to_none_without_hang():
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    try:
        start = time.perf_counter()
        assert made.baud_hunt([115200], timeout_per=0.2) is None
        assert time.perf_counter() - start < 3.0
    finally:
        made.stop_rx_pump()


def test_bcon_set_baud_index_guards_and_reverts():
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_BAUD_SET, T_HELLO, T_STATUS, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    ser.baudrate = 1000000
    made.start_rx_pump()
    stop = threading.Event()

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[seen:]:
                seen += 1
                if len(frame) < 2 or frame[0] != 0xAB:
                    continue
                if frame[1] == T_BAUD_SET:
                    status = bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    ser.feed(frame_build(T_STATUS, status, 0x40))
                elif frame[1] == T_HELLO:
                    # 新baudへ切替わった後は採用を返さない（復帰経路の検証）。
                    pass
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        assert made.set_baud_index(1, timeout=1.0) is False
        assert ser.baudrate == 1000000
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_bcon_set_wired_mode_notes_reboot():
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_STATUS, T_WIRED_MODE, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    stop = threading.Event()

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[seen:]:
                seen += 1
                if len(frame) >= 2 and frame[0] == 0xAB and frame[1] == T_WIRED_MODE:
                    status = bytes([0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    ser.feed(frame_build(T_STATUS, status, 0x50))
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        assert made.set_wired_mode(True, timeout=2.0) is True
        assert any(
            len(f) >= 2 and f[0] == 0xAB and f[1] == T_WIRED_MODE for f in ser.written
        )
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()
