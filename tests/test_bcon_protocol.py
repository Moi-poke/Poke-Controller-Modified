"""bcon純粋層のhostベクタ（実機不要）。"""


def test_bcon_crc8_vector():
    from core.transport.bcon_protocol import crc8

    assert crc8(b"123456789") == 0xF4


def test_bcon_frame_build_state_neutral():
    from core.transport.bcon_protocol import T_STATE, frame_build

    payload = bytes([0x00, 0x00, 0x00, 0x00, 0x80, 0x80, 0x80, 0x80])
    frame = frame_build(T_STATE, payload, 0x00)
    assert frame[0] == 0xAB
    assert frame[1] == T_STATE
    assert frame[2] == 8
    assert len(frame) == 13


def test_bcon_parser_slides_on_bad_crc():
    from core.transport.bcon_protocol import T_PING, BconParser, frame_build

    good = frame_build(T_PING, b"", 0x05)
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    parser = BconParser()
    out = parser.feed(bytes(bad) + good)
    assert len(out) == 1
    assert out[0][0] == T_PING
    assert out[0][2] == 0x05


def test_bcon_hello_len_and_baud_table():
    from core.transport.bcon_protocol import (
        BAUD_TABLE,
        DEFAULT_BAUD_INDEX,
        T_BAUD_SET,
        T_HELLO,
        T_HELLO_ACK,
        frame_build,
        proto_expected_len,
    )

    assert proto_expected_len(T_HELLO) == 2
    assert proto_expected_len(T_HELLO_ACK) == 4
    assert proto_expected_len(T_BAUD_SET) == 1
    assert BAUD_TABLE[DEFAULT_BAUD_INDEX] == 1000000
    frame = frame_build(T_HELLO, bytes([0x04, 0x01]), 0x07)
    assert len(frame) == 7


def test_bcon_parser_resyncs_with_sync_byte_in_payload():
    from core.transport.bcon_protocol import T_PING, T_STATE, BconParser, frame_build

    payload = bytes([0xAB, 0x00, 0xAB, 0x00, 0xAB, 0xAB, 0xAB, 0xAB])
    first = frame_build(T_STATE, payload, 0x10)
    second = frame_build(T_PING, b"", 0x11)
    # SYNC紛れのゴミ前置でもpayload内SYNCに惑わず1件だけ確定する。
    parser = BconParser()
    out = parser.feed(b"\x00\xff\xab" + first)
    assert out == [(T_STATE, payload, 0x10)]
    # payload内SYNCを含む連続2件は順序どおり2件出る。
    parser = BconParser()
    out = parser.feed(first + second)
    assert out == [(T_STATE, payload, 0x10), (T_PING, b"", 0x11)]
    # 分割受信でもpayload内SYNCで割れない。
    parser = BconParser()
    assert parser.feed(first[:4]) == []
    out = parser.feed(first[4:])
    assert out == [(T_STATE, payload, 0x10)]


def test_bcon_rx_seq_first_not_counted_and_gap_events():
    import threading
    import time

    from core.transport import create_transport
    from core.transport.bcon_protocol import T_PING, frame_build

    class ScriptedSer:
        is_open = True

        def __init__(self) -> None:
            self._chunks: list[bytes] = []
            self._lock = threading.Lock()
            self.baudrate = 1000000

        def read(self, n: int) -> bytes:
            _ = n
            with self._lock:
                if self._chunks:
                    return self._chunks.pop(0)
            time.sleep(0.001)
            return b""

        def feed(self, data: bytes) -> None:
            with self._lock:
                self._chunks.append(bytes(data))

        def write(self, data: bytes) -> int:
            return len(data)

    made = create_transport("bcon")
    ser = ScriptedSer()
    made.ser = ser
    assert made.live_stats()["seq_gap"] == 0
    assert made.start_rx_pump() is True
    seen: list[tuple[int, bytes, int]] = []
    unsub = made.subscribe_rx(seen.append)
    try:

        def _wait_seen(n: int) -> None:
            deadline = time.perf_counter() + 2.0
            while time.perf_counter() < deadline and len(seen) < n:
                time.sleep(0.005)
            assert len(seen) >= n

        def _gap() -> int:
            return int(made.live_stats()["seq_gap"])

        ser.feed(frame_build(T_PING, b"", 0x20))
        _wait_seen(1)
        assert _gap() == 0  # 初回は数えない
        ser.feed(frame_build(T_PING, b"", 0x21))
        _wait_seen(2)
        assert _gap() == 0  # 連番は欠番なし
        ser.feed(frame_build(T_PING, b"", 0x23))
        _wait_seen(3)
        assert _gap() == 1  # 0x22欠番は1イベント（欠番数ではなく件数）
        ser.feed(frame_build(T_PING, b"", 0x24))
        _wait_seen(4)
        assert _gap() == 1
        ser.feed(frame_build(T_PING, b"", 0xFF))
        _wait_seen(5)
        assert _gap() == 2  # 大きな飛びも1イベント
        ser.feed(frame_build(T_PING, b"", 0x00))
        _wait_seen(6)
        assert _gap() == 2  # 0xFF→0x00は連番（mod256）
    finally:
        try:
            unsub()
        except Exception:
            pass
        made.stop_rx_pump()
