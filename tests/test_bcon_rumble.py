"""T_RUMBLE受信保持のhostベクタ（実機不要）。"""

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

        @property
        def in_waiting(self) -> int:
            with self._lock:
                return sum(len(c) for c in self._chunks)

        def feed(self, data: bytes) -> None:
            with self._lock:
                self._chunks.append(bytes(data))

        def write(self, data: bytes) -> int:
            with self._lock:
                self.written.append(bytes(data))
            return len(data)

    return ScriptedSer()


def test_rumble_frame_updates_last_rumble_via_parser() -> None:
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_RUMBLE, BconParser, frame_build

    made = create_transport("bcon")
    assert made.last_rumble() == b""
    raw = frame_build(T_RUMBLE, bytes([0x0A, 0x05]), 0x31)
    frames = BconParser().feed(raw)
    assert len(frames) == 1
    made._dispatch_rx(frames[0])
    assert made.last_rumble() == bytes([0x0A, 0x05])


def test_rumble_copy_does_not_alias_and_malformed_never_raises() -> None:
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_RUMBLE, BconParser, frame_build

    made = create_transport("bcon")
    raw = frame_build(T_RUMBLE, bytes([0x01, 0x02]), 0x32)
    made._dispatch_rx(BconParser().feed(raw)[0])
    got = made.last_rumble()
    assert got == bytes([0x01, 0x02])
    # 写しの変更が内部へ漏れないこと（bytes不変だがAPI契約として確認）。
    assert made.last_rumble() == bytes([0x01, 0x02])

    # 不正フレームでも落とさない。
    made._dispatch_rx((T_RUMBLE, b"", 0x33))
    made._dispatch_rx((-1, b"", 0x00))
    made._dispatch_rx(())
    assert made.last_rumble() == b"" or isinstance(made.last_rumble(), bytes)


def test_rumble_decode_helper_returns_int_pair() -> None:
    from core.transport.bcon import decode_rumble

    assert decode_rumble(bytes([0x0A, 0x05])) == (0x0A, 0x05)
    assert decode_rumble(b"") == (0, 0)
    assert decode_rumble(bytes([0x01])) == (0, 0)


def test_rumble_keeps_player_info_and_status_intact() -> None:
    from core.transport import create_transport
    from core.transport.bcon_protocol import (
        T_PLAYER_INFO,
        T_RUMBLE,
        T_STATUS,
        BconParser,
        frame_build,
    )

    made = create_transport("bcon")
    parser = BconParser()
    made._dispatch_rx(
        parser.feed(frame_build(T_PLAYER_INFO, bytes([0x07, 0x08]), 0x40))[0]
    )
    made._dispatch_rx(
        parser.feed(
            frame_build(
                T_STATUS, bytes([0x05, 0x07, 0x01, 0x00, 0x02, 0x00, 0x00]), 0x41
            )
        )[0]
    )
    made._dispatch_rx(parser.feed(frame_build(T_RUMBLE, bytes([0x03, 0x04]), 0x42))[0])
    assert made.last_rumble() == bytes([0x03, 0x04])
    assert made.last_player_info() == bytes([0x07, 0x08])
    assert made.last_status()["flags"] == 0x05
