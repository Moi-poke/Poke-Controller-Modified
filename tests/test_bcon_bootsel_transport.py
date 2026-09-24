"""bcon BOOTSEL要求のhostベクタ（実機不要）。"""

from __future__ import annotations

import threading
import time


def _make_scripted_ser(feed_delay: float = 0.001):
    """読み書きの記録を持つ疑似シリアル。"""

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


def test_bootsel_ack_returns_true():
    from core.transport import create_transport
    from core.transport.bcon_protocol import (
        BOOTSEL_MAGIC,
        T_BOOTSEL,
        T_STATUS,
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
                if len(frame) >= 2 and frame[0] == 0xAB and frame[1] == T_BOOTSEL:
                    assert frame[2] == 1
                    assert frame[3] == BOOTSEL_MAGIC
                    ser.feed(frame_build(T_STATUS, bytes(7), 0x00))
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        assert made.rx_pump_running() is True
        assert made.request_bootsel(timeout=2.0) is True
        assert ser.baudrate == 1000000
        with made._lock:
            assert made._tx_hold is False
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_bootsel_no_ack_returns_false_without_raise():
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    try:
        assert made.request_bootsel(timeout=0.2) is False
        with made._lock:
            assert made._tx_hold is False
    finally:
        made.stop_rx_pump()
