"""bcon別プロセス送出のhostベクタ（実機不要）。

子プロセスの代役（fake spawn＋本物の子mainをスレッド実行）で
proxy全系を決定的に検証する。実spawnはsmokeのみ。
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from core.transport.bcon_protocol import T_BEACON_START as _T_BEACON


class _FakeWorkerHandle:
    """multiprocessing.Processの代役。子mainをスレッドで回す。"""

    def __init__(self, entry: Any, cmd_q: Any, evt_q: Any, cfg: dict) -> None:
        self._thread = threading.Thread(
            target=entry, args=(cmd_q, evt_q, cfg), daemon=True
        )
        self._alive = False

    def start(self) -> None:
        self._alive = True
        self._thread.start()

    def is_alive(self) -> bool:
        return self._alive and self._thread.is_alive()

    def terminate(self) -> None:
        self._alive = False

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)


def _fake_spawn(entry: Any, cfg: dict) -> tuple[Any, Any, Any]:
    cmd_q: Any = queue.Queue()
    evt_q: Any = queue.Queue()
    return _FakeWorkerHandle(entry, cmd_q, evt_q, cfg), cmd_q, evt_q


class _FakeSerial:
    """pyserialの代役。開閉と空読みだけ行う。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.is_open = True
        self.written: list[bytes] = []

    def read(self, n: int = 1) -> bytes:
        import time as _time

        _time.sleep(0.001)
        return b""

    @property
    def in_waiting(self) -> int:
        return 0

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.is_open = False


def test_proc_proxy_opens_and_closes_with_fake_worker(monkeypatch):
    import core.transport.bcon as _bcon
    from core.transport import bcon_proc

    monkeypatch.setattr(_bcon.serial, "Serial", _FakeSerial)
    made = bcon_proc.BconProcTransport(_spawn=_fake_spawn)
    try:
        assert made.open(3, "COM3", 1000000) is True
        assert made.is_open() is True
    finally:
        made.close()
    assert made.is_open() is False


class _ScriptedSer(_FakeSerial):
    """応答を差し込める疑似シリアル。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        import threading as _threading

        self._chunks: list[bytes] = []
        self._lock = _threading.Lock()
        self.baudrate = 1000000

    def read(self, n: int = 1) -> bytes:
        with self._lock:
            if self._chunks:
                return self._chunks.pop(0)
        import time as _time

        _time.sleep(0.001)
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


def _open_proc_with_scripted_ser(monkeypatch):
    import threading as _threading
    import time as _time

    import core.transport.bcon as _bcon
    from core.transport import bcon_proc
    from core.transport.bcon_protocol import (
        T_HELLO,
        T_HELLO_ACK,
        T_STATUS,
        frame_build,
    )

    # openのたび新しい線を渡す。使い回すと旧ワーカーのcloseが
    # 同一実体を閉じて再open後のis_openが偽になる（実物は毎回新規）。
    sers: list[_ScriptedSer] = []

    def _factory(*args: Any, **kwargs: Any) -> _ScriptedSer:
        ser = _ScriptedSer(*args, **kwargs)
        sers.append(ser)
        return ser

    monkeypatch.setattr(_bcon.serial, "Serial", _factory)
    made = bcon_proc.BconProcTransport(_spawn=_fake_spawn)
    assert made.open(3, "COM3", 1000000) is True
    stop = _threading.Event()

    def _frames() -> list[tuple[_ScriptedSer, bytes]]:
        out: list[tuple[_ScriptedSer, bytes]] = []
        for ser in list(sers):
            with ser._lock:
                out.extend((ser, f) for f in ser.written)
        return out

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            for ser, frame in _frames()[seen:]:
                seen += 1
                if len(frame) < 2 or frame[0] != 0xAB:
                    continue
                if frame[1] == T_HELLO:
                    ser.feed(
                        frame_build(T_HELLO_ACK, bytes([0x04, 0x00, 0x01, 0x00]), 0x41)
                    )
                elif frame[1] == _T_BEACON:
                    ser.feed(
                        frame_build(
                            T_STATUS,
                            bytes([0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                            0x52,
                        )
                    )
                else:
                    ser.feed(
                        frame_build(
                            T_STATUS,
                            bytes([0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                            0x50,
                        )
                    )
            _time.sleep(0.002)

    worker = _threading.Thread(target=_responder, daemon=True)
    worker.start()
    return made, sers, stop, worker


def _all_written(sers: list[_ScriptedSer]) -> list[bytes]:
    out: list[bytes] = []
    for ser in sers:
        with ser._lock:
            out.extend(ser.written)
    return out


def test_proc_session_ops_round_trip(monkeypatch):
    made, sers, stop, worker = _open_proc_with_scripted_ser(monkeypatch)
    try:
        assert made.hello(timeout=2.0) is True
        status = made.send_beacon(timeout=2.0)
        assert status is not None
        assert status["errcode"] == 0
        assert any(
            len(f) >= 2 and f[0] == 0xAB and f[1] == _T_BEACON
            for f in _all_written(sers)
        )
        stats = made.live_stats()
        assert stats["sent"] >= 0
        assert stats["err_drop"] == 0
    finally:
        stop.set()
        worker.join(1.0)
        made.close()


def test_send_config_public_path(monkeypatch):
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_STATUS_REQ

    made = create_transport("switch-bcon")
    ser = _ScriptedSer()
    made.ser = ser
    assert made.send_config(T_STATUS_REQ, b"") is True
    assert any(
        len(f) >= 3 and f[0] == 0xAB and f[1] == T_STATUS_REQ for f in ser.written
    )
    assert made.send_config(0x99, b"toolong" * 8) is False


def test_proc_pump_delivers_on_arrival():
    """到着確認駆動のポンプが即時分配する（タイムアウト待ちしない）。"""
    import time as _time

    from core.transport import create_transport
    from core.transport.bcon_protocol import T_STATUS, frame_build

    made = create_transport("switch-bcon")
    ser = _ScriptedSer()
    made.ser = ser
    assert made.start_rx_pump() is True
    try:
        ser.feed(
            frame_build(
                T_STATUS, bytes([0x40, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00]), 0x60
            )
        )
        deadline = _time.perf_counter() + 2.0
        while _time.perf_counter() < deadline:
            if made.last_status().get("last_seq") == 0x07:
                break
            _time.sleep(0.01)
        assert made.last_status().get("last_seq") == 0x07
    finally:
        made.stop_rx_pump()


def test_proc_spawn_smoke_and_double_close():
    """本物のspawnで起動・丁寧な失敗・二重closeする。"""
    from core.transport import bcon_proc

    calls: list[int] = []
    real_spawn = bcon_proc._default_spawn

    def _counting(entry: Any, cfg: dict) -> Any:
        calls.append(1)
        return real_spawn(entry, cfg)

    made = bcon_proc.BconProcTransport(_spawn=_counting)
    try:
        assert made._spawn_worker() is True
        assert made.proc_alive() is True
        assert made.open(9999, "", 1000000) is False
        assert calls == [1]
        assert made.is_open() is False
        assert made.request_status(timeout=0.2) is None
        assert made.live_stats()["sent"] == 0
    finally:
        made.close()
        made.close()


def test_proc_worker_crash_fail_safe(monkeypatch):
    """子が死んでもハングせず安全既定値。reopenで復帰。"""
    made, sers, stop, worker = _open_proc_with_scripted_ser(monkeypatch)
    try:
        assert made.hello(timeout=2.0) is True
        made._terminate_worker_for_test()
        assert made.request_status(timeout=0.3) is None
        assert made.send_beacon(timeout=0.3) is None
        assert made.live_stats()["sent"] == 0
        assert made.open(3, "COM3", 1000000) is True
        assert made.is_open() is True
    finally:
        stop.set()
        worker.join(1.0)
        made.close()


def test_proc_socket_framing_round_trip_and_eof():
    """ソケットキューは往復し、相手死亡でEOFになる。"""
    import socket as _socket
    import time as _time

    from core.transport import bcon_proc

    listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    sender = _socket.create_connection(("127.0.0.1", port), timeout=5.0)
    conn, _addr = listener.accept()
    listener.close()
    putter = bcon_proc._SocketQueue(sender, name="put")
    getter = bcon_proc._SocketQueue(conn, name="get")
    try:
        putter.put(("hello", 1, b"abc"))
        assert getter.get(timeout=5.0) == ("hello", 1, b"abc")
        sender.close()
        deadline = _time.perf_counter() + 5.0
        while True:
            try:
                getter.get(timeout=0.2)
            except bcon_proc._PeerGone:
                break
            assert _time.perf_counter() < deadline, "EOFが来ない"
    finally:
        putter.close()
        getter.close()


def test_proc_live_loop_and_hooks(monkeypatch):
    import time as _time

    made, sers, stop, worker = _open_proc_with_scripted_ser(monkeypatch)
    hooked: list[tuple[str, str]] = []
    made.set_hooks(
        lambda row, show=True: hooked.append(("begin", row)),
        lambda row, show=True: hooked.append(("end", row)),
    )
    try:
        assert made.start_live_loop() is True
        assert made.live_loop_running() is True
        made.send_row("0 8 80 80 80 80")
        made.flush_pending()
        deadline = _time.perf_counter() + 3.0
        states: list[bytes] = []
        while _time.perf_counter() < deadline:
            states = [
                f
                for f in _all_written(sers)
                if len(f) >= 2 and f[0] == 0xAB and f[1] == 0x01
            ]
            if states:
                break
            _time.sleep(0.05)
        assert states, "STATEが線に出ない"
        assert any(kind == "begin" for kind, _row in hooked)
        assert made.stop_live_loop() is True
        assert made.live_loop_running() is False
    finally:
        stop.set()
        worker.join(1.0)
        made.close()
