"""bcon計測カウンタのhostベクタ（実機不要）。

H1(RPC遅延)/H2(drain数+ms)/H3(rx率)切り分け用の素朴な数え。
int/float/perf_counterだけで、per-frameのI/Oはしない。
worker側（_on_rx_frame・dispatch）からwidget・after()には触らない。
BconSetupとpanelは窓を作らず __new__＋stub で見る。
"""

import logging
import queue
import threading
import time
from types import SimpleNamespace
from typing import Any


def _make_headless_bcon() -> Any:
    """窓を作らないBconSetup素体。queueと窓stubだけ持つ。"""
    from BconSetup import BconSetup

    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = object()
    obj._queue = queue.Queue()
    obj._busy = False
    obj._stop = False
    obj._closed = False
    obj._rx_unsub = None
    obj._rx_transport = None

    class _StubWindow:
        def after(self, _ms: int, _fn: Any) -> Any:
            return None

    obj.window = _StubWindow()
    return obj


def test_poll_counts_drained_and_wall_ms() -> None:
    """_pollが吐き出し数と壁msを残す（H2切り分け用）。"""
    obj = _make_headless_bcon()
    for _ in range(3):
        obj._queue.put(("noop", ""))
    obj._poll()
    snap = obj.counters_snapshot()
    assert snap["poll_ticks"] == 1
    assert snap["poll_last_drained"] == 3
    assert snap["poll_drained_total"] == 3
    assert snap["poll_last_ms"] >= 0.0
    assert snap["poll_max_ms"] >= snap["poll_last_ms"]
    obj._poll()
    assert obj.counters_snapshot()["poll_ticks"] == 2


def test_rx_counts_by_ftype_with_rate() -> None:
    """_on_rx_frameがftype別に数える（H3切り分け用・選別前）。"""
    from BconSetup import T_PLAYER_INFO, T_RUMBLE

    obj = _make_headless_bcon()
    obj._on_rx_frame((T_PLAYER_INFO, b"\x05\x03", 0))
    obj._on_rx_frame((T_PLAYER_INFO, b"\x05\x03", 1))
    obj._on_rx_frame((T_RUMBLE, b"\x10\x20", 2))
    obj._on_rx_frame((0x20, b"\x00" * 7, 3))  # STATUSは積まないが数える。
    snap = obj.counters_snapshot()
    assert snap["rx_total"] == 4
    assert snap["rx_by_ftype"] == {T_PLAYER_INFO: 2, T_RUMBLE: 1, 0x20: 1}
    assert set(snap["rx_per_sec_by_ftype"]) == {T_PLAYER_INFO, T_RUMBLE, 0x20}
    for rate in snap["rx_per_sec_by_ftype"].values():
        assert rate >= 0.0
    # widgetには触れない（queueに積むだけ）。
    assert obj._queue.qsize() == 3


def test_counters_work_on_bare_new_object() -> None:
    """__init__を通さない素体でも数えが動く（欠品は自前で足す）。"""
    from BconSetup import T_PLAYER_INFO

    obj = _make_headless_bcon()
    assert not hasattr(obj, "_rx_counts")
    obj._on_rx_frame((T_PLAYER_INFO, b"\x05\x03", 0))
    obj._poll()
    snap = obj.counters_snapshot()
    assert snap["rx_total"] == 1
    assert snap["poll_ticks"] == 1


def test_qsize_sampling_records_peak() -> None:
    """_pollが入り口の溜まりを書き留める（d切り分け用）。"""
    obj = _make_headless_bcon()
    obj._queue.put(("noop", "a"))
    obj._queue.put(("noop", "b"))
    obj._poll()
    assert obj.counters_snapshot()["queue_max_qsize"] >= 2


def test_throttled_debug_logs_at_most_once_per_burst() -> None:
    """間引きdebugは連打で1件まで（per-frameのI/Oにしない）。"""
    obj = _make_headless_bcon()
    records: list[Any] = []

    class _H(logging.Handler):
        def emit(self, record: Any) -> Any:
            records.append(record)
            return None

    target = logging.getLogger("BconSetup")
    level = target.level
    handler = _H()
    target.setLevel(logging.DEBUG)
    target.addHandler(handler)
    try:
        for _ in range(10):
            obj._poll()
    finally:
        target.removeHandler(handler)
        target.setLevel(level)
    assert len(records) == 1


def test_serial_panel_records_rpc_latency() -> None:
    """panel巡回はTkでgetterを呼ばないためRPC遅延を残さない（Q1=A）。
    遅いgetterがあっても待たない。遅延の写し口自体は残す。"""
    from ui.serial_panel import SerialPanelMixin

    class _FakeTransport:
        name = "bcon"
        capability = "BCON_STATE"

        def __init__(self) -> None:
            self.getter_calls = 0

        def last_player_info(self) -> bytes:
            self.getter_calls += 1
            time.sleep(0.02)
            return b"\x05\x03"

        def last_rumble(self) -> bytes:
            self.getter_calls += 1
            return b""

    class _StubCanvas:
        def itemconfigure(self, _item: Any, **kwargs: Any) -> None:
            _ = kwargs

    class _StubLabel:
        def config(self, **kwargs: Any) -> None:
            _ = kwargs

    class _StubRoot:
        def after(self, _ms: int, _fn: Any) -> Any:
            return None

    fake = _FakeTransport()
    obj: Any = SerialPanelMixin.__new__(SerialPanelMixin)
    obj.serial = SimpleNamespace(sender=SimpleNamespace(transport=fake))
    obj._player_lamp_items = [1, 2, 3, 4]
    obj.player_lamp_canvas = _StubCanvas()
    obj.rumble_label = _StubLabel()
    obj.root = _StubRoot()
    obj._player_lamp_after = None
    obj._poll_player_lamp()
    assert fake.getter_calls == 0
    assert obj.bcon_rpc_latency_ms() == {}


def test_proc_call_latency_and_fanout(monkeypatch) -> None:
    """別過程RPCの往復msとrx配達数を残す（H1切り分け用）。"""
    import core.transport.bcon as _bcon
    from core.transport import bcon_proc

    class _FakeWorkerHandle:
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
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.is_open = True

        def read(self, n: int = 1) -> bytes:
            _ = n
            time.sleep(0.001)
            return b""

        @property
        def in_waiting(self) -> int:
            return 0

        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            self.is_open = False

    monkeypatch.setattr(_bcon.serial, "Serial", _FakeSerial)
    made = bcon_proc.BconProcTransport(_spawn=_fake_spawn)
    try:
        assert made.open(3, "COM3", 1000000) is True
        ok, _result = made._call("last_player_info", (), timeout=3.0)
        assert ok is True
        stats = made.rpc_stats()
        assert stats["count"] >= 1
        assert stats["last_method"] == "last_player_info"
        assert stats["last_ms"] >= 0.0
        assert stats["max_ms"] >= stats["last_ms"]
        seen: list[Any] = []
        made.subscribe_rx(seen.append)
        made._evt_q.put(("rx", 0x23, b"\x05\x03", 7))
        deadline = time.monotonic() + 2.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
        assert seen != []
        again = made.rpc_stats()
        assert again["rx_fanout_total"] >= 1
        assert again["rx_fanout_last"] >= 1
    finally:
        made.close()
