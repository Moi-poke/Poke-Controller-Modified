"""bconの120Hz送信・Sender統合・計測のhostベクタ（実機不要・Task 5）。

Task 3/4の約束は保つ。send_rowの即送（loop停止時は単一write）・
単一write・TYPE一致wait・get_raw_serial()->None・会話のnever-raises・
UNSUPPORTED停止はここでは変えず、loopと統計とS行受付だけを足す。
"""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path


def _make_fake_ser(write_delay: float = 0.0):
    """書き溜めるだけの疑似シリアル。1write呼び出しが1要素になる。"""

    class FakeSer:
        is_open = True

        def __init__(self) -> None:
            self.written: list[bytes] = []
            self.baudrate = 1000000
            self.closed = False
            self._lock = threading.Lock()

        def write(self, data: bytes) -> int:
            if write_delay > 0.0:
                time.sleep(write_delay)
            with self._lock:
                self.written.append(bytes(data))
            return len(data)

        def read(self, n: int) -> bytes:
            _ = n
            time.sleep(0.001)
            return b""

        def close(self) -> None:
            self.closed = True

        def snapshot(self) -> list[bytes]:
            with self._lock:
                return list(self.written)

    return FakeSer()


def _state_frames(written: list[bytes]) -> list[bytes]:
    """STATEのLEN8フレームだけを抜く（会話フレームは除く）。"""
    return [
        b
        for b in written
        if len(b) == 13 and b[0] == 0xAB and b[1] == 0x01 and b[2] == 8
    ]


def _buttons_of(frame: bytes) -> int:
    """STATE payloadのBTN u32LE（予約bit落とし済み）。"""
    return int.from_bytes(frame[3:7], "little") & 0x003FFFFF


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def _load_jitter_tool():
    path = Path(__file__).resolve().parent.parent / "tools" / "bcon_jitter.py"
    spec = importlib.util.spec_from_file_location("bcon_jitter", str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bcon_live_capable_and_stats_shape():
    from core import Sender, Transport

    probe = Sender.Sender.__new__(Sender.Sender)
    made = Transport.create_transport("bcon")
    assert probe._liveCapable(made) is True
    assert set(made.live_stats().keys()) >= {
        "sent",
        "dropped",
        "seq_gap",
        "err_crc",
        "err_drop",
    }


def test_bcon_send_row_accepts_pico_s_row_neutral():
    """live workerの出すS行（姿勢空間）をSTATE 1発で送る。"""
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    made.send_row("S 0 8 80 80 80 80")
    states = _state_frames(ser.snapshot())
    assert len(states) == 1
    assert _buttons_of(states[0]) == 0
    assert states[0][7:11] == bytes([0x80, 0x80, 0x80, 0x80])


def test_bcon_s_row_buttons_map_to_viper_not_wire():
    """姿勢空間のY/B/AはVIIPERのY/B/Aへ載る（wire解釈のずれ防止）。

    姿勢A=0x04をwireと読むとbit2→BTN_Yになる。正しくはBTN_A(1<<1)。
    """
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    made.send_row("S 1 8 80 80 80 80")
    made.send_row("S 2 8 80 80 80 80")
    made.send_row("S 4 8 80 80 80 80")
    states = _state_frames(ser.snapshot())
    assert len(states) == 3
    assert _buttons_of(states[0]) == (1 << 2)
    assert _buttons_of(states[1]) == (1 << 0)
    assert _buttons_of(states[2]) == (1 << 1)


def test_bcon_s_row_hat_and_sticks():
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    made.send_row("S 0 0 80 80 80 80")
    states = _state_frames(ser.snapshot())
    assert len(states) == 1
    assert _buttons_of(states[0]) == (1 << 11)
    made.send_row("S 0 8 ff 80 7f 81")
    states = _state_frames(ser.snapshot())
    assert len(states) == 2
    # Yは反転しない。Pico側pack（4096-Y）が唯一の反転であり、
    # PC側で反転するとwakecon経路と逆端に載る（二重反転）。
    assert states[1][7:11] == bytes([0xFF, 0x80, 0x7F, 0x81])


def test_bcon_stick_y_passes_through_uninverted():
    """wire行・S行ともY値はそのまま載る（PC側で反転しない）。

    Keys上（LY=0x00）はUART LY=0x00のまま送り、Pico側packの
    反転1回で輸送最大端へ載る。wakecon経路（無反転）と同一の端。
    """
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    made.send_row("2 08 00 ff")
    made.send_row("S 0 8 80 00 80 ff")
    states = _state_frames(ser.snapshot())
    assert len(states) == 2
    assert states[0][7:11] == bytes([0x00, 0xFF, 0x80, 0x80])
    assert states[1][7:11] == bytes([0x80, 0x00, 0x80, 0xFF])


def test_bcon_live_loop_refreshes_and_seqs():
    """loop起動中は最新姿勢を120Hzで送り直す。1呼出し1フレーム・SEQ連番。"""
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    made.send_row("end")
    assert made.start_live_loop() is True
    try:
        assert made.live_loop_running() is True
        assert _wait_for(lambda: made.live_stats()["sent"] >= 5, timeout=2.0)
        states = _state_frames(ser.snapshot())
        assert len(states) >= 5
        seqs = [b[11] for b in states]
        for prev, cur in zip(seqs, seqs[1:]):
            assert cur == ((prev + 1) & 0xFF)
    finally:
        made.stop_live_loop()
    assert made.live_loop_running() is False


def test_bcon_live_loop_coalesces_burst_into_merged():
    """loop回転より速い連打は最新1件へ畳みmergedに数える（backlogなし）。"""
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser(write_delay=0.03)
    made.ser = ser
    assert made.start_live_loop() is True
    try:
        for _ in range(6):
            made.send_row("S 4 8 80 80 80 80")
        assert _wait_for(lambda: made.live_stats()["merged"] >= 1, timeout=2.0)
        for frame in _state_frames(ser.snapshot()):
            assert len(frame) == 13
    finally:
        made.stop_live_loop()


def test_bcon_live_stats_shape_and_counts():
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    stats = made.live_stats()
    assert set(stats.keys()) >= {
        "sent",
        "merged",
        "dropped",
        "seq_gap",
        "err_crc",
        "err_drop",
        "rtt_ms",
    }
    made.send_row("10 08")
    stats = made.live_stats()
    assert stats["sent"] == 1
    assert stats["merged"] == 0
    assert stats["dropped"] == 0
    assert stats["seq_gap"] == 0
    assert stats["rtt_ms"] is None


def test_bcon_hold_suppresses_loop_and_counts_dropped():
    """hunt中の抑えはloopの送出も止め捨てた分をdroppedに数える。"""
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    # loopは姿勢の捏造をしない。更新対象が無いと送らないため先に1発載せる。
    made.send_row("end")
    assert made.start_live_loop() is True
    try:
        assert _wait_for(lambda: made.live_stats()["sent"] >= 3, timeout=2.0)
        before = made.live_stats()["sent"]
        with made._lock:
            made._tx_hold = True
        try:
            made.send_row("S 4 8 80 80 80 80")
            time.sleep(0.05)
            mid = made.live_stats()
            assert mid["sent"] == before
            assert mid["dropped"] >= 1
        finally:
            with made._lock:
                made._tx_hold = False
        made.send_row("S 4 8 80 80 80 80")
        assert _wait_for(lambda: made.live_stats()["sent"] > before, timeout=2.0)
    finally:
        made.stop_live_loop()


def test_bcon_set_baud_window_holds_tx():
    """set_baudの切替・復帰窓は抑えが立ちSTATEが出ない（Task 4残余の持越し）。

    応答はSTATUSだけ返し新baudのHELLOには答えないため復帰経路を通る。
    """
    from core.transport import create_transport
    from core.transport.bcon_protocol import T_BAUD_SET, T_STATUS, frame_build

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    ser.baudrate = 1000000
    made.start_rx_pump()
    stop = threading.Event()

    def _responder() -> None:
        seen = 0
        while not stop.is_set():
            pending = ser.snapshot()
            for frame in pending[seen:]:
                seen += 1
                if len(frame) >= 2 and frame[0] == 0xAB and frame[1] == T_BAUD_SET:
                    time.sleep(0.25)
                    if stop.is_set():
                        return
                    status = bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                    made._parser.feed(frame_build(T_STATUS, status, 0x40))
                    for want, done, found in list(made._rx_waiters):
                        if T_STATUS in want:
                            found.setdefault("frame", (T_STATUS, status, 0x40))
                            done.set()
                    return
            time.sleep(0.002)

    worker = threading.Thread(target=_responder, daemon=True)
    worker.start()
    done: dict[str, object] = {}

    def _switch() -> None:
        done["ok"] = made.set_baud_index(1, timeout=1.5)

    switcher = threading.Thread(target=_switch, daemon=True)
    switcher.start()
    try:
        assert _wait_for(lambda: made._tx_hold is True, timeout=1.5)
        before = len(_state_frames(ser.snapshot()))
        made.send_row("S 4 8 80 80 80 80")
        time.sleep(0.05)
        assert len(_state_frames(ser.snapshot())) == before
        switcher.join(3.0)
        assert done.get("ok") is False
        assert ser.baudrate == 1000000
        assert made._tx_hold is False
    finally:
        stop.set()
        switcher.join(3.0)
        worker.join(1.0)
        made.stop_rx_pump()


def test_bcon_sender_live_reaches_bcon_wire_as_viper():
    """Senderのlive経路（S行）がbconの線へVIIPERで届く（統合の要）。"""
    from core import Sender, Transport
    from core.Keys import Button

    made = Transport.create_transport("bcon")
    ser = _make_fake_ser()
    sender = Sender.Sender(is_show_serial=False, transport=made)
    try:
        made.ser = ser
        assert sender.pressButtons([Button.A]) is True
        assert _wait_for(
            lambda: any(
                _buttons_of(f) == (1 << 1) for f in _state_frames(ser.snapshot())
            ),
            timeout=2.0,
        )
    finally:
        sender.stopLiveWorker(1.0)


def test_bcon_close_stops_live_loop():
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_fake_ser()
    made.ser = ser
    assert made.start_live_loop() is True
    made.close()
    assert made.live_loop_running() is False
    assert made.ser is None


def test_bcon_live_loop_needs_no_tkinter():
    """送信周期をtkinterのafterに載せない（独立スレッド＋絶対時刻）。"""
    root = Path(__file__).resolve().parent.parent
    src = (root / "SerialController" / "core" / "transport" / "bcon.py").read_text(
        encoding="utf-8"
    )
    assert "import tkinter" not in src
    assert "from tkinter" not in src
    assert ".after(" not in src
    assert "perf_counter" in src
    assert "next_tx" in src


def test_bcon_jitter_percentile_vectors():
    tool = _load_jitter_tool()
    assert tool.percentile([], 50) == 0.0
    assert tool.percentile([8.33], 50) == 8.33
    assert tool.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert tool.percentile([1.0, 2.0, 3.0, 4.0], 99) == 4.0
    assert tool.percentile([4.0, 1.0, 3.0, 2.0], 99) == 4.0


def test_bcon_jitter_summary_line_shape():
    tool = _load_jitter_tool()
    line = tool.summarize(8.31, 9.87, 1200, 0, 0, 0)
    assert line == "p50=8.31ms p99=9.87ms sent=1200 seq_gap=0 err_crc=+0 err_drop=+0"


def test_bcon_jitter_arg_defaults():
    tool = _load_jitter_tool()
    args = tool.parse_args(["--port", "COM9"])
    assert args.port == "COM9"
    assert args.baud == 1000000
    assert args.secs == 10
