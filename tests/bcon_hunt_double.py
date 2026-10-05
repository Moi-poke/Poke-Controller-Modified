"""baud hunt 中の Pico を真似る疑似シリアル (契約テスト用の部品)。

Switch-bcon の hunt (src/main.c core1_entry / bcon_frame_cb) と同じ規則:
- 未確定のあいだ slot を HUNT_DWELL_S ごとに巡回する (slot が UART の rate)。
- host の rate が現 slot と一致した有効 frame が同じ slot 滞在中に 2 連続で
  届いたら確定する。slot が変わると数え直し。確定前の frame には応答しない
  (TX 抑制)。確定を作った frame も inbox purge で実行されない。
- 確定後は同じ rate の HELLO に HELLO_ACK、STATUS_REQ に STATUS を返す。
rate 不一致の frame は Pico にとってゴミなので何もしない。
"""

from __future__ import annotations

import threading
import time

from core.transport.bcon_protocol import (
    T_HELLO,
    T_HELLO_ACK,
    T_STATUS,
    T_STATUS_REQ,
    frame_build,
)

HUNT_DWELL_S = 0.150
HUNT_SLOTS = (1000000, 921600, 460800, 115200)


class HuntingPicoSer:
    is_open = True

    def __init__(self, slots: tuple[int, ...] = HUNT_SLOTS, start_slot: int = 0):
        self._lock = threading.Lock()
        self._chunks: list[bytes] = []
        self.written: list[bytes] = []
        self.baudrate = 1000000
        self._slots = slots
        self._t0 = time.perf_counter()
        self._start = start_slot % len(slots)
        self._lock_slot: int | None = None
        self._lock_run = 0
        self.locked_rate: int | None = None
        self._tx_seq = 0

    def _dwell_now(self) -> int:
        # 滞在の通し番号。一巡して同じ slot に戻っても別の滞在として数え直す
        # (FW の hunt_set_slot が切替ごとに baud_lock_reset するのと同じ)。
        return int((time.perf_counter() - self._t0) / HUNT_DWELL_S)

    def _reply(self, type_: int, payload: bytes) -> None:
        self._chunks.append(frame_build(type_, payload, self._tx_seq))
        self._tx_seq = (self._tx_seq + 1) & 0xFF

    def write(self, data: bytes) -> int:
        with self._lock:
            self.written.append(bytes(data))
            if len(data) < 2 or data[0] != 0xAB:
                return len(data)
            if self.locked_rate is None:
                dwell = self._dwell_now()
                slot = (self._start + dwell) % len(self._slots)
                if self.baudrate != self._slots[slot]:
                    return len(data)
                if self._lock_slot == dwell:
                    self._lock_run += 1
                else:
                    self._lock_slot = dwell
                    self._lock_run = 1
                if self._lock_run >= 2:
                    self.locked_rate = self._slots[slot]
                return len(data)
            if self.baudrate != self.locked_rate:
                return len(data)
            if data[1] == T_HELLO:
                self._reply(T_HELLO_ACK, bytes([0x04, 0x00, 0x04, 0x00]))
            elif data[1] == T_STATUS_REQ:
                self._reply(T_STATUS, bytes(7))
            return len(data)

    def read(self, n: int) -> bytes:
        _ = n
        with self._lock:
            if self._chunks:
                return self._chunks.pop(0)
        time.sleep(0.001)
        return b""

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return sum(len(c) for c in self._chunks)
