"""baud_hunt と hunt 中 Pico の契約 (bcon_hunt_double の疑似 Pico 相手、実機不要)。"""

from __future__ import annotations

import time

from bcon_hunt_double import HUNT_DWELL_S, HuntingPicoSer
from core.transport.bcon_protocol import T_HELLO, frame_build


def _hunt_with(ser: HuntingPicoSer) -> int | None:
    from core.transport import create_transport

    made = create_transport("bcon")
    made.ser = ser
    made.start_rx_pump()
    try:
        return made.baud_hunt()
    finally:
        made.stop_rx_pump()


def test_double_does_not_lock_from_sparse_frames() -> None:
    # Given: 1M で hunt 中の疑似 Pico (対照。部品が甘いと本題が空振りする)
    ser = HuntingPicoSer()
    # When: 1 slot 滞在より長い間隔で HELLO を単発で送り続ける
    for i in range(6):
        ser.write(frame_build(T_HELLO, bytes([0x04, 0x01]), i))
        time.sleep(HUNT_DWELL_S * 1.4)
    # Then: 2 連続が揃わないので確定しない
    assert ser.locked_rate is None


def test_baud_hunt_locks_a_hunting_pico_at_its_rate() -> None:
    # Given: 4 slot を 150ms ずつ巡回している未確定の疑似 Pico
    for start in range(4):
        ser = HuntingPicoSer(start_slot=start)
        # When: host が既定の候補で baud_hunt する
        locked = _hunt_with(ser)
        # Then: Pico が確定した rate で lock を報告し、host もその rate にいる
        assert ser.locked_rate is not None, f"start_slot={start}"
        assert locked == ser.locked_rate, f"start_slot={start}"
        assert ser.baudrate == locked
