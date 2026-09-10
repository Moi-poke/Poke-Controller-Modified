"""Pico live 経路の発火検証。スレッドは本物、線だけ偽物。

legacy の同期送信は test_transport.py で見る。ここでは worker が
実際に S 行を送り出すこと（8ms スロット・keepalive・停止）を確かめる。
基板なしで回せるよう、pyserial の代わりに書き溜めるだけの偽物を差す。
"""

import threading
import time

from core import Sender, Transport
from core.Keys import Button


class FakeSerial:
    """pyserial の代わり。書かれたバイト列を溜めるだけ。"""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.is_open = True

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.is_open = False


def make_live_sender() -> tuple[Sender.Sender, FakeSerial]:
    transport = Transport.PicoUartTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    fake = FakeSerial()
    transport.ser = fake  # type: ignore[assignment]
    return sender, fake


def wait_for(predicate: object, timeout: float = 2.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        assert callable(predicate)
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.005)
    return bool(predicate())  # type: ignore[operator]


def rows(fake: FakeSerial) -> list[str]:
    return [b.decode("ascii").strip() for b in fake.written]


def test_live_worker_sends_s_rows() -> None:
    sender, fake = make_live_sender()
    try:
        assert sender._live_thread is not None and sender._live_thread.is_alive()
        assert wait_for(lambda: len(fake.written) > 0)
        assert rows(fake)[0].startswith("S ")
        assert rows(fake)[0] == "S 0 8 80 80 80 80"
    finally:
        # stopLiveWorker は内部で join まで行う。成功後は参照が外れる。
        assert sender.stopLiveWorker(1.0) is True


def test_live_send_posture_reaches_wire() -> None:
    sender, fake = make_live_sender()
    try:
        assert sender.pressButtons([Button.A])
        assert sender.sendPosture() is True
        assert wait_for(lambda: "S 4 8 80 80 80 80" in rows(fake))
    finally:
        sender.stopLiveWorker(1.0)


def test_pico_encoder_selfcheck() -> None:
    assert Sender.Sender.verifyPicoEncoder() is True


def test_snapshot_purity() -> None:
    sender, fake = make_live_sender()
    try:
        assert sender.verifySnapshotPurity() is True
    finally:
        sender.stopLiveWorker(1.0)
    _ = fake


def test_stale_generation_does_not_kill_winner_worker() -> None:
    """負けた世代の後始末は勝者の worker を殺さない（同一性で見分ける）。

    setTransport の二重切替では、古い世代の後始末が共有の
    stopLiveWorker() を呼ぶと、勝者が起こしたばかりの worker まで
    止めてしまう。止めるのは自分が起こしたスレッドが残っている
    ときだけにし、差し替わっていたら勝者に任せて触らない。
    """
    sender, fake = make_live_sender()
    try:
        winner = sender._live_thread
        assert winner is not None and winner.is_alive()
        # 古い世代が掴んでいたスレッド（既に止まった旧 worker 相当）。
        stale = threading.Thread(target=lambda: None, daemon=True)
        assert sender._stopLiveWorkerIf(stale) is True
        assert sender._live_thread is winner
        assert winner.is_alive(), "勝者の worker が殺されています"
    finally:
        assert sender.stopLiveWorker(1.0) is True
    _ = fake


def test_stop_live_worker_if_stops_own_thread() -> None:
    """自分が起こしたスレッドが残っていれば従来どおり止める。"""
    sender, fake = make_live_sender()
    try:
        mine = sender._live_thread
        assert mine is not None and mine.is_alive()
        assert sender._stopLiveWorkerIf(mine) is True
        assert not mine.is_alive()
        assert sender._live_thread is None
    finally:
        sender.stopLiveWorker(1.0)
    _ = fake
