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


def test_short_press_reaches_wire_with_min_dwell() -> None:
    sender, fake = make_live_sender()
    try:
        sender.setLiveMinDwell(16)
        sender.pressButtons([Button.A])
        sender.releaseButtons([Button.A])  # 直後に離しても潰れない
        assert wait_for(lambda: len(fake.written) >= 3, timeout=2.0)
        # pressの送出は中立のdwell待ちで3行目になるため、後続行も待つ。
        assert wait_for(
            lambda: "S 4 8 80 80 80 80" in rows(fake)
            and rows(fake).index("S 4 8 80 80 80 80") < len(rows(fake)) - 1,
            timeout=2.0,
        )
        sent = rows(fake)
        assert "S 4 8 80 80 80 80" in sent  # pressがワイヤに出た
        assert sent.index("S 4 8 80 80 80 80") < len(sent) - 1  # 単発で終わらない
    finally:
        sender.stopLiveWorker(1.0)


def test_idle_repeats_every_slot() -> None:
    sender, fake = make_live_sender()
    try:
        assert wait_for(lambda: len(fake.written) >= 3, timeout=2.0)
        assert all(r.startswith("S ") for r in rows(fake))
    finally:
        sender.stopLiveWorker(1.0)


def test_idle_repeat_is_paced_for_pico_uart_poll() -> None:
    """無変化の再送はPicoのUARTポーリングに合わせて間引く。

    Picoは10ms周期ポーリング＋32B FIFOで読む。8ms毎の連送は2行が
    1窓に落ちてオーバーランする実測（ng約23%）のため、repeatは
    24ms間隔に制限する。新規エッジの即時性は変えない。
    """
    sender, fake = make_live_sender()
    try:
        time.sleep(0.5)
        n = len(fake.written)
        assert 5 <= n <= 45, f"repeat pacing broken: {n} rows in 0.5s"
    finally:
        sender.stopLiveWorker(1.0)


def test_clear_live_stats_resets_counts() -> None:
    sender, fake = make_live_sender()
    try:
        sender.pressButtons([Button.A])
        assert wait_for(lambda: len(fake.written) > 0, timeout=2.0)
        assert sender.stopLiveWorker(1.0) is True
        sender.clearLiveStats()
        stats = sender.getLiveStats()
        assert stats["put"] == 0
        assert stats["replaced"] == 0
        assert stats["dropped"] == 0
        assert stats["sent"] == 0
    finally:
        sender.stopLiveWorker(1.0)


def test_release_reaches_wire_on_slot() -> None:
    """通常の解放は次スロットで送出される（優先起床なし）。

    シリアル通信は8msごとにコントローラーの状態を送る仕様であり、
    pressは状態を変化させるだけである。解放だけを速達にする理由は
    ない（速達は次pressのdwell待ちを招き40ms周期を伸ばす）。
    停止系の中立は別経路（_closing指定）で守る。
    """
    import inspect

    assert "priority" not in inspect.signature(Sender.Sender.putLive).parameters
    sender, fake = make_live_sender()
    try:
        assert not hasattr(sender, "_live_wake")
        sender.pressButtons([Button.A])
        sender.releaseButtons([Button.A])
        assert wait_for(lambda: "S 0 8 80 80 80 80" in rows(fake), timeout=2.0)
        assert wait_for(lambda: "S 4 8 80 80 80 80" in rows(fake), timeout=2.0)
    finally:
        sender.stopLiveWorker(1.0)


def test_release_all_delivers_neutral() -> None:
    """停止系の解放は中立を届ける（優先ではなく到達で守る）。"""
    sender, fake = make_live_sender()
    try:
        sender.pressButtons([Button.A])
        sender.releaseAll()
        assert wait_for(lambda: "S 0 8 80 80 80 80" in rows(fake), timeout=2.0)
    finally:
        sender.stopLiveWorker(1.0)


def test_closing_guard_keeps_order() -> None:
    """切断中は通常申告を断り、切断用中立だけを通す（順序の保護）。"""
    sender, fake = make_live_sender()
    try:
        sender.pressButtons([Button.A])
        sender._live_closing = True
        try:
            before = sender.getLiveStats()["put"]
            sender.putLive(sender.snapshot())
            assert sender.getLiveStats()["put"] == before
            sender.putLive(sender.snapshot(), _closing=True)
            assert sender.getLiveStats()["put"] == before + 1
        finally:
            sender._live_closing = False
    finally:
        sender.stopLiveWorker(1.0)
