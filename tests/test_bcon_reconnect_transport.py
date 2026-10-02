"""bcon T_RECONNECT 再武装要求のhostベクタ（実機不要）。"""

from __future__ import annotations

import threading
import time

from test_bcon_bootsel_transport import _make_scripted_ser


def test_reconnect_ack_sends_len0_frame_and_returns_true():
    """T_RECONNECT は LEN0。ACK が来れば True、baud は変えない。"""
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_RECONNECT, T_STATUS, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    stop = threading.Event()
    seen: list[bytes] = []

    def _responder() -> None:
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[len(seen) :]:
                seen.append(frame)
                if len(frame) >= 3 and frame[0] == 0xAB and frame[1] == T_RECONNECT:
                    # LEN=0: [SYNC][TYPE][LEN] の3バイト目で 0 であること。
                    assert frame[2] == 0
                    ser.feed(frame_build(T_STATUS, bytes(7), 0x00))
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        assert made.rx_pump_running() is True
        assert made.request_reconnect(timeout=2.0) is True
        # レート切替はしない（再接続要求は旧レートのまま）。
        assert ser.baudrate == 1000000
        with made._lock:
            assert made._tx_hold is False
        assert any(f[0] == 0xAB and f[1] == T_RECONNECT for f in seen if len(f) >= 2), (
            "T_RECONNECT frame was actually written"
        )
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_reconnect_no_ack_returns_false_without_raise():
    """ACK が来なくても例外を投げず False。再武装は未確認。"""
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    try:
        assert made.request_reconnect(timeout=0.2) is False
        with made._lock:
            assert made._tx_hold is False
    finally:
        made.stop_rx_pump()


def test_reconnect_on_closed_transport_returns_false():
    """未オープンなら送らずに False。例外を出さない。"""
    from core.transport import create_transport

    made = create_transport("bcon")
    assert made.ser is None
    assert made.request_reconnect(timeout=0.2) is False


def test_reconnect_is_a_known_type_with_len_zero():
    """0x3B は未知型に落ちていない（既知型として長さ0で登録されている）。"""
    from core.transport.bcon_protocol import T_RECONNECT, proto_expected_len

    assert T_RECONNECT == 0x3B
    assert proto_expected_len(T_RECONNECT) == 0


def test_bootsel_resends_until_ack_because_hunt_cannot_lock_on_one_frame():
    """baud hunt 中は1発では命令が実行されない。繰り返し送出が必須。

    Pico は有効frame 2連続で baud を確定し、確定時に inbox を捨てる
    (Switch-bcon/src/main.c:274-281)。したがって最初の数回は
    「lock 消費」になり ACK は返らない。
    """
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_BOOTSEL, T_STATUS, frame_build

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    stop = threading.Event()
    seen: list[bytes] = []

    def _responder() -> None:
        # 最初の2回は「lock 消費」として ACK しない（実挙動の再現）。
        bootsels = 0
        while not stop.is_set():
            with ser._lock:
                pending = list(ser.written)
            for frame in pending[len(seen) :]:
                seen.append(frame)
                if len(frame) >= 2 and frame[0] == 0xAB and frame[1] == T_BOOTSEL:
                    bootsels += 1
                    if bootsels >= 3:
                        ser.feed(frame_build(T_STATUS, bytes(7), 0x00))
                        return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    try:
        assert made.request_bootsel(timeout=3.0) is True
        bootsels = sum(1 for f in seen if len(f) >= 2 and f[1] == T_BOOTSEL)
        assert bootsels >= 3, f"repeated until ack, got {bootsels}"
    finally:
        stop.set()
        worker.join(1.0)
        made.stop_rx_pump()


def test_send_until_status_gives_up_after_timeout_without_raise():
    """ACK が来ないまま期限に達したら False。例外は投げない。"""
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_RECONNECT

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    try:
        assert made._send_until_status(T_RECONNECT, b"", 0.3, "test:label") is False
        assert ser.baudrate == 1000000
        # 応答のないままでも購読は残らない（後続の要求を阻害しない）。
        with made._lock:
            assert made._rx_waiters == []
    finally:
        made.stop_rx_pump()


def test_proc_transport_exposes_bootsel_and_reconnect_like_the_direct_one():
    """proc 輸送器は本家と同じ操作面を持つこと（子側のRPC許可も必要）。

    bcon1M プロファイルは switch-bcon-proc を使う。以前は
    request_bootsel / request_reconnect が proc 側に無く、
    BconSetup の getattr が None になって必ず False になっていた。
    """
    from core.transport import create_transport
    from core.transport.bcon import BconTransport
    from core.transport.bcon_proc import _ALLOWED_CALLS, BconProcTransport

    direct = create_transport("bcon")
    proc = create_transport("switch-bcon-proc")
    try:
        assert isinstance(direct, BconTransport)
        assert isinstance(proc, BconProcTransport)
        for name in ("request_bootsel", "request_reconnect"):
            assert callable(getattr(direct, name, None)), f"direct has {name}"
            assert callable(getattr(proc, name, None)), f"proc has {name}"
            assert name in _ALLOWED_CALLS, f"{name} is allowed as a child RPC"
    finally:
        del direct
        del proc
