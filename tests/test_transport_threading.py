"""transport のスレッド競合まわりの回帰検証（課題 1〜8）。

実機なしで回す。遅い聞き手・遅い close・二重起動・ser 寿命・
ポンプ死・_write 経由・wait_rx 上限・細部の錠をまとめて見る。
"""

from __future__ import annotations

import math
import threading
import time

from core import InputLog, Sender, Transport


class _FastSer:
    """速い偽シリアル。書いたものだけ溜める。"""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.is_open = True
        self.closed = False

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def read(self, n: int) -> bytes:
        _ = n
        time.sleep(0.001)
        return b""

    def close(self) -> None:
        self.is_open = False
        self.closed = True


class _ScriptedSer:
    """read() で仕込みを返す偽シリアル。"""

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])
        self._lock = threading.Lock()
        self.is_open = True
        self.written: list[bytes] = []
        self.closed = False

    def read(self, n: int) -> bytes:
        _ = n
        with self._lock:
            if self._chunks:
                return self._chunks.pop(0)
        time.sleep(0.001)
        return b""

    def feed(self, data: bytes) -> None:
        with self._lock:
            self._chunks.append(data)

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.is_open = False
        self.closed = True


class _FailOnceSer(_ScriptedSer):
    """最初の数回だけ例外を投げ、その後は動く偽シリアル。"""

    def __init__(self, fails: int = 3) -> None:
        super().__init__()
        self._fails = fails

    def read(self, n: int) -> bytes:
        _ = n
        if self._fails > 0:
            self._fails -= 1
            raise RuntimeError("受信の故障のふり")
        return super().read(n)


class _SlowCloseTransport(Transport.Transport):
    """close が遅い偽の線（setTransport の wedge 再現用）。"""

    name = "slowclose"
    capability = Transport.LEGACY_ROW

    def __init__(self, delay: float = 0.30) -> None:
        self.rows: list[str] = []
        self._opened = True
        self._delay = delay
        self._listeners: list[object] = []
        self.close_calls = 0

    def open(
        self, portNum: int, portName: str = "", baudrate: int = 9600, **extra: object
    ) -> bool:
        _ = (portNum, portName, baudrate, extra)
        self._opened = True
        return True

    def close(self) -> None:
        self.close_calls += 1
        time.sleep(self._delay)
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        _ = measure_perf
        self.rows.append(row)

    def add_listener(self, func: object) -> bool:
        self._listeners.append(func)
        return True

    def remove_listener(self, func: object) -> None:
        if func in self._listeners:
            self._listeners.remove(func)


class _RecordingTransport(Transport.Transport):
    """send_row と _write のどちらが呼ばれたか記録する。"""

    name = "recording"
    capability = Transport.LEGACY_ROW

    def __init__(self) -> None:
        self.send_rows: list[str] = []
        self.direct_writes: list[str] = []
        self._opened = True

    def open(
        self, portNum: int, portName: str = "", baudrate: int = 9600, **extra: object
    ) -> bool:
        _ = (portNum, portName, baudrate, extra)
        return True

    def close(self) -> None:
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        _ = measure_perf
        self.send_rows.append(row)

    def _write(self, row: str, measure_perf: bool = True) -> None:
        _ = measure_perf
        self.direct_writes.append(row)


class _TrackLock:
    """with で使った回数を数える錠（細部の錠の有無を見る用）。"""

    def __init__(self, base: threading.Lock | threading.RLock) -> None:
        self._base = base
        self.entries = 0

    def __enter__(self) -> None:
        self.entries += 1
        self._base.acquire()
        return None

    def __exit__(self, *args: object) -> None:
        self._base.release()
        return None

    def acquire(self, *args: object, **kwargs: object) -> bool:
        self.entries += 1
        return bool(self._base.acquire(*args, **kwargs))  # type: ignore[arg-type]

    def release(self) -> None:
        self._base.release()


def _quiet_transport() -> Transport.TextSerialTransport:
    t = Transport.TextSerialTransport()
    t._send_interval = 0.0
    return t


def test_send_row_does_not_hold_lock_during_notify() -> None:
    """課題1: 遅い聞き手がいても聞き手の追加は詰まらない。"""
    t = _quiet_transport()
    t.ser = _FastSer()  # type: ignore[assignment]
    received: list[str] = []

    def _slow(row: str) -> None:
        time.sleep(0.30)
        received.append(row)

    t.add_listener(_slow)
    done = threading.Event()

    def _send() -> None:
        t.send_row("0x0004 8")
        done.set()

    th = threading.Thread(target=_send, daemon=True)
    th.start()
    time.sleep(0.05)
    start = time.perf_counter()
    t.add_listener(lambda row: None)
    elapsed = time.perf_counter() - start
    th.join(2.0)
    assert done.is_set()
    # 錠を持ったままだと 0.3 秒待たされる。外なら一瞬で終わる。
    assert elapsed < 0.15, f"錠を持ったまま通知している: {elapsed:.3f}s"
    assert received == ["0x0004 8"]


def test_send_row_order_preserved_single_thread() -> None:
    """課題1: 順序は保つ（単スレッドの回帰）。"""
    t = _quiet_transport()
    fake = _FastSer()
    t.ser = fake  # type: ignore[assignment]
    got: list[str] = []
    t.add_listener(got.append)
    rows = ["0x0004 8", "0x0008 8", "0x0010 8"]
    for row in rows:
        t.send_row(row)
    wire = [b.decode("ascii").strip() for b in fake.written]
    assert wire == rows
    assert got == rows


def test_contended_send_row_all_delivered_quickly() -> None:
    """課題1: N スレッド x 行でも欠けず、直列より速い。"""
    t = _quiet_transport()
    fake = _FastSer()
    t.ser = fake  # type: ignore[assignment]

    def _slow(row: str) -> None:
        _ = row
        time.sleep(0.010)

    t.add_listener(_slow)
    n_threads = 4
    per_thread = 10
    barrier = threading.Barrier(n_threads)

    def _worker(tid: int) -> None:
        barrier.wait()
        for i in range(per_thread):
            # ボタンを変えて間引きを避ける（順序の欠けを見やすくする）。
            t.send_row(f"0x{(0x1000 + tid * 64 + i):04x} 8")

    threads = [
        threading.Thread(target=_worker, args=(tid,), daemon=True)
        for tid in range(n_threads)
    ]
    start = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join(10.0)
    elapsed = time.perf_counter() - start
    assert all(not th.is_alive() for th in threads)
    # 40 行 x 0.01 秒 = 直列なら 0.4 秒。並列なら 0.3 秒を切るはず。
    assert len(fake.written) == n_threads * per_thread
    assert elapsed < 0.30, f"直列のまま詰まっている: {elapsed:.3f}s"


def test_set_transport_releases_lock_during_close() -> None:
    """課題2: 遅い close 中も姿勢の読み取りは詰まらない。"""
    old = _SlowCloseTransport(delay=0.30)
    sender = Sender.Sender(is_show_serial=False, transport=old)
    new = _SlowCloseTransport(delay=0.0)
    done = threading.Event()

    def _switch() -> None:
        sender.setTransport(new)
        done.set()

    th = threading.Thread(target=_switch, daemon=True)
    th.start()
    time.sleep(0.05)
    start = time.perf_counter()
    _ = sender.snapshot()
    elapsed = time.perf_counter() - start
    th.join(5.0)
    assert done.is_set()
    assert elapsed < 0.15, f"setTransport が錠を持ったまま止めている: {elapsed:.3f}s"


def test_set_transport_double_switch_no_leak() -> None:
    """課題2: 二重切替でも worker が漏れず、参照が替わる。"""
    before = {th.name for th in threading.enumerate() if th.name == "PicoLiveWorker"}
    pico = Transport.PicoUartTransport()
    sender = Sender.Sender(is_show_serial=False, transport=pico)
    fake = _FastSer()
    pico.ser = fake  # type: ignore[assignment]
    assert sender.isLiveWorkerRunning()
    from fakes import FakeTransport

    first = FakeTransport()
    second = FakeTransport()
    assert sender.setTransport(first) is True
    assert not sender.isLiveWorkerRunning()
    assert sender.setTransport(second) is True
    assert sender.transport is second
    time.sleep(0.05)
    leaked = [
        th
        for th in threading.enumerate()
        if th.name == "PicoLiveWorker" and th.is_alive()
    ]
    # 最初から生きていた分を除いて数える（今回は止めたので 0 のはず）。
    assert len(leaked) == len(before)
    sender.closeSerial()


def test_start_rx_pump_concurrent_single_thread() -> None:
    """課題3: 読みポンプの二重起動は1本だけ残る。"""
    t = _quiet_transport()
    t.ser = _ScriptedSer()  # type: ignore[assignment]
    n = 20
    barrier = threading.Barrier(n)

    def _start() -> None:
        barrier.wait()
        t.start_rx_pump()

    threads = [threading.Thread(target=_start, daemon=True) for _ in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(5.0)
    try:
        assert t.rx_pump_running()
        alive = [
            th
            for th in threading.enumerate()
            if th.name == "PokeConRxPump" and th.is_alive()
        ]
        # この検証だけが立てた分は1本のはず（他の検証と並列しない前提）。
        assert len(alive) == 1, f"ポンプが {len(alive)} 本漏れている"
    finally:
        t.stop_rx_pump()


def test_start_live_worker_concurrent_single_thread() -> None:
    """課題3: live worker の二重起動は1本だけ残る。"""
    pico = Transport.PicoUartTransport()
    sender = Sender.Sender(is_show_serial=False, transport=pico)
    sender.transport.ser = _FastSer()  # type: ignore[attr-defined]
    assert sender.stopLiveWorker(1.0)
    n = 20
    barrier = threading.Barrier(n)

    def _start() -> None:
        barrier.wait()
        sender.startLiveWorker()

    threads = [threading.Thread(target=_start, daemon=True) for _ in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(5.0)
    try:
        assert sender.isLiveWorkerRunning()
        alive = [
            th
            for th in threading.enumerate()
            if th.name == "PicoLiveWorker" and th.is_alive()
        ]
        assert len(alive) == 1, f"worker が {len(alive)} 本漏れている"
    finally:
        assert sender.stopLiveWorker(1.0)


def test_open_closes_previous_ser() -> None:
    """課題4: 開き直しは古い線を閉じてから開く。"""
    t = Transport.TextSerialTransport()
    made: list[_ScriptedSer] = []
    import serial as pyserial

    real_serial = pyserial.Serial

    def _factory(
        path: str,
        baudrate: int,
        timeout: float | None = None,
        write_timeout: float | None = None,
    ) -> _ScriptedSer:
        _ = (path, baudrate, timeout, write_timeout)
        fake = _ScriptedSer()
        made.append(fake)
        return fake  # type: ignore[return-value]

    try:
        pyserial.Serial = _factory  # type: ignore[assignment]
        assert t.open(0, "COM1", 9600) is True
        assert t.open(0, "COM2", 9600) is True
    finally:
        pyserial.Serial = real_serial
        try:
            t.close()
        except Exception:
            pass
    assert len(made) == 2
    assert made[0].closed is True, "古い線を閉じずに上書きしている"


def test_close_clears_ser() -> None:
    """課題4: 閉じたら ser は None（閉じた物を返さない）。"""
    t = Transport.TextSerialTransport()
    fake = _ScriptedSer()
    t.ser = fake  # type: ignore[assignment]
    t.close()
    assert t.ser is None
    assert t.get_raw_serial() is None
    assert t.is_open() is False


def test_rx_loop_death_warns_and_recovers() -> None:
    """課題5: 読みポンプが落ちたら警告し、次で復帰できる。"""
    t = Transport.TextSerialTransport()
    bad = _FailOnceSer(fails=2)
    t.ser = bad  # type: ignore[assignment]
    warnings: list[str] = []
    errors: list[str] = []

    class _Rec:
        def debug(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)

        def info(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)

        def warning(self, msg: object, *args: object, **kwargs: object) -> None:
            _ = kwargs
            warnings.append(str(msg) + " " + " ".join(str(a) for a in args))

        def error(self, *args: object, **kwargs: object) -> None:
            _ = kwargs
            errors.append(" ".join(str(a) for a in args))

    t._logger = _Rec()  # type: ignore[assignment]
    assert t.start_rx_pump() is True
    deadline = time.perf_counter() + 2.0
    while t.rx_pump_running() and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert not t.rx_pump_running(), "壊れた読み取りでもポンプが生きている"
    assert warnings, "落ちたのに警告が出ていない（debug だけ）"
    # 復帰: まともな線に差し替えて購読し直したら動く。
    good = _ScriptedSer()
    t.ser = good  # type: ignore[assignment]
    got: list[str] = []
    t.subscribe_rx(got.append)
    assert t.rx_pump_running(), "購読し直してもポンプが戻らない"
    try:
        good.feed(b"PONG\r\n")
        deadline = time.perf_counter() + 2.0
        while not got and time.perf_counter() < deadline:
            time.sleep(0.01)
        assert "PONG" in got
    finally:
        t.stop_rx_pump()
    _ = errors


def test_sender_write_uses_locked_send_row() -> None:
    """課題6: Sender._write は錠を通る send_row 経由で送る。"""
    rec = _RecordingTransport()
    sender = Sender.Sender(is_show_serial=False, transport=rec)
    sender._write("0x0004 8")
    assert rec.send_rows == ["0x0004 8"], "錠を迂回する _write 直呼びのまま"
    assert rec.direct_writes == []


def test_wait_rx_clamps_inf() -> None:
    """課題7: wait_rx(inf) は 5 秒で打ち切る（固まらない）。"""
    t = Transport.TextSerialTransport()
    t.ser = _ScriptedSer()  # type: ignore[assignment]
    assert t.start_rx_pump() is True
    try:
        out: dict[str, object] = {}

        def _wait() -> None:
            out["line"] = t.wait_rx(("PONG",), timeout=math.inf)

        th = threading.Thread(target=_wait, daemon=True)
        start = time.perf_counter()
        th.start()
        th.join(7.0)
        elapsed = time.perf_counter() - start
        assert not th.is_alive(), "inf で無期限に待っている"
        assert out.get("line") is None
        assert elapsed < 6.0, f"打ち切りが遅い: {elapsed:.3f}s"
        assert elapsed >= 4.5, f"早すぎる打ち切り: {elapsed:.3f}s"
    finally:
        t.stop_rx_pump()


def test_wait_rx_nan_returns_immediately() -> None:
    """課題7: wait_rx(nan) は 0 秒扱いで即返す。"""
    t = Transport.TextSerialTransport()
    t.ser = _ScriptedSer()  # type: ignore[assignment]
    assert t.start_rx_pump() is True
    try:
        start = time.perf_counter()
        assert t.wait_rx(("PONG",), timeout=math.nan) is None
        assert time.perf_counter() - start < 0.5
    finally:
        t.stop_rx_pump()


def test_live_stats_guarded_by_lock() -> None:
    """課題8: live 統計の更新は _live_lock の内側で行う。"""
    pico = Transport.PicoUartTransport()
    sender = Sender.Sender(is_show_serial=False, transport=pico)
    sender.transport.ser = _FastSer()  # type: ignore[attr-defined]
    try:
        base = threading.Lock()
        tracked = _TrackLock(base)
        sender._live_lock = tracked  # type: ignore[assignment]
        tracked.entries = 0
        sender._recordLiveOrder({"revision": 1})
        assert tracked.entries >= 1, "_live_stats を錠の外で触っている"
    finally:
        sender.stopLiveWorker(1.0)


def test_sender_hooks_guarded_by_lock() -> None:
    """課題8: 計測フックは Sender の錠の内側で帳簿を付ける。"""
    from fakes import FakeTransport

    sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
    sender.setPerfRecording(True)
    base = threading.RLock()
    tracked = _TrackLock(base)
    sender._lock = tracked  # type: ignore[assignment]
    tracked.entries = 0
    sender._onWriteBegin("0x0004 8", True)
    begin_entries = tracked.entries
    sender._onWriteEnd("0x0004 8", True)
    end_entries = tracked.entries
    sender.setPerfRecording(False)
    assert begin_entries >= 1, "_onWriteBegin が錠の外で帳簿を付けている"
    assert end_entries > begin_entries, "_onWriteEnd が錠の外で帳簿を付けている"


def test_inputlog_format_and_enabled_take_lock() -> None:
    """課題8: 書式と有効無効の切替は InputLog の錠を取る。"""
    logger = InputLog.InputLogger(emit=lambda line: None)
    base = threading.Lock()
    tracked = _TrackLock(base)
    logger._lock = tracked  # type: ignore[assignment]
    tracked.entries = 0
    logger.set_format("simple")
    assert tracked.entries >= 1, "set_format が錠を取っていない"
    tracked.entries = 0
    logger.set_enabled(False)
    assert tracked.entries >= 1, "set_enabled が錠を取っていない"
