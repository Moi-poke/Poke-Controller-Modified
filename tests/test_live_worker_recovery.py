"""live worker の回復と線の保護。Stop 打切り・同時申告・M計数の門を固定する。"""

from __future__ import annotations

import time
from typing import Any


def _wait_until(pred: Any, timeout: float = 3.0, step: float = 0.01) -> bool:
    """条件が成り立つまで待つ（裏スレッドの再試行を待つため）。"""
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


def _make_runner() -> Any:
    from fakes import FakeScheduler
    from services.command_runner import CommandRunner

    clock = FakeScheduler()
    runner = CommandRunner(
        stats={},
        schedule=clock.schedule,
        cancel=clock.cancel,
        notify_user=lambda m: None,
        on_state_changed=lambda: None,
        on_list_refresh=lambda: None,
    )
    return runner, clock


def _live_fence_ser() -> Any:
    """live 対応の線。塞ぐ側と起こす側の両方を数える。"""
    from fakes import FakeSer

    class LiveFenceSer(FakeSer):
        def __init__(self) -> None:
            super().__init__(opened=True)
            self.calls: list[str] = []
            self.running = True

        def isLiveCapable(self) -> bool:
            return True

        def isLiveWorkerRunning(self) -> bool:
            return self.running

        def discardLive(self) -> bool:
            self.calls.append("discardLive")
            return True

        def stopLiveWorker(self, timeout: float = 1.0) -> bool:
            _ = timeout
            self.calls.append("stopLiveWorker")
            self.running = False
            return True

        def startLiveWorker(self, transport: Any = None) -> bool:
            _ = transport
            self.calls.append("startLiveWorker")
            self.running = True
            return True

        def dropOwner(self, source: Any = None) -> bool:
            # 送り元 1 人分だけ取り下げる（全取り下げは人の入力を消す）。
            self.calls.append(f"dropOwner:{source}")
            return True

    return LiveFenceSer()


def test_fenced_live_worker_is_restarted_when_the_fence_is_cleared() -> None:
    """Given: live 対応の線を打切りで塞いだ / When: 塞いだ相手の死を確認する / Then: worker を起こし直すこと。"""
    from fakes import FakeCommand, FakeThread, StuckThread
    from services import command_runner

    runner, clock = _make_runner()
    ser = _live_fence_ser()
    runner.request_start(FakeCommand(thread=StuckThread(alive=True)), ser)
    runner.request_stop()
    steps = (
        int(command_runner.STOP_ESCALATE_MS) // int(command_runner.STOP_WATCH_MS) + 2
    )
    for _ in range(steps):
        if runner.state == "idle":
            break
        clock.run_next()
    # 打切りは worker を止める（ここまでは従来どおり）。
    assert "stopLiveWorker" in ser.calls
    # 塞ぎを外すときに戻さないと、以後の実行が1行も出ない無音になる。
    runner._fenced_thread = FakeThread(alive=False)
    runner._reap_fenced_thread()
    # 片付けは同期、起こし直しは裏スレッド（GUI を固めない）。
    assert _wait_until(lambda: "startLiveWorker" in ser.calls), ser.calls
    assert ser.calls.count("startLiveWorker") == 1, ser.calls
    # 再開の順序: 古い申告を捨てる → 逃げた送り元の持ち分だけ取り下げる → 起こす。
    # 逆にすると、古いスレッドが押していたボタンが新しいコマンドへ持ち越される。
    at = ser.calls.index("startLiveWorker")
    assert ser.calls[at - 2 : at] == ["discardLive", "dropOwner:script"], ser.calls


def test_resume_waits_for_a_stubborn_worker_before_starting() -> None:
    """Given: 止まりきっていない worker が残る線 / When: 塞ぎを外す / Then: 止めてから起こすこと。"""
    from fakes import FakeSer

    class StubbornSer(FakeSer):
        def __init__(self) -> None:
            super().__init__(opened=True)
            self.calls: list[str] = []
            self.checks = 0

        def isLiveCapable(self) -> bool:
            return True

        def isLiveWorkerRunning(self) -> bool:
            # 最初の 2 回はまだ生きている（stopper が join しきれなかった形）。
            self.checks += 1
            return self.checks <= 2

        def stopLiveWorker(self, timeout: float = 1.0) -> bool:
            _ = timeout
            self.calls.append("stopLiveWorker")
            return True

        def startLiveWorker(self, transport: Any = None) -> bool:
            _ = transport
            self.calls.append("startLiveWorker")
            return True

        def discardLive(self) -> bool:
            self.calls.append("discardLive")
            return True

        def releaseAll(self) -> None:
            self.calls.append("releaseAll")

    runner, _clock = _make_runner()
    ser = StubbornSer()
    runner._ser = ser
    runner._fenced_live = True
    runner._fenced_owner = "script"
    runner._resume_fenced_live()
    # 生きていると startLiveWorker は何もせず False を返し、_live_stop も
    # 消さないため、止め直してからでないと無音のままになる。
    assert _wait_until(lambda: ser.calls[-1:] == ["startLiveWorker"]), ser.calls
    assert "stopLiveWorker" in ser.calls, ser.calls


def test_fenced_live_worker_is_not_restarted_while_closing() -> None:
    """Given: 終了処理が始まっている / When: 塞ぎを外す / Then: worker を起こさないこと。"""
    from fakes import FakeThread

    runner, _clock = _make_runner()
    ser = _live_fence_ser()
    runner._ser = ser
    runner._fenced_thread = FakeThread(alive=False)
    runner._fenced_live = True
    runner.notify_closing()
    runner._reap_fenced_thread()
    assert ser.calls == [], ser.calls


def test_posture_push_keeps_the_sender_lock() -> None:
    """Given: live 経路の送信器 / When: ボタン・Hat・スティックを申告する / Then: 列への投入が錠の中であること。"""
    from core.Keys import Button
    from core.serial.sender import Sender
    from fakes import FakeTransport

    sender = Sender(is_show_serial=False, transport=FakeTransport())
    # live 対応の判定だけを差し替える（worker は起こさないので列は誰も吐かない）。
    sender._liveCapable = lambda *a, **k: True  # type: ignore[method-assign]
    held: list[bool] = []
    orig = sender.putLive

    def _record(snap: dict[str, Any] | None = None, *, _closing: bool = False) -> None:
        # RLock._is_owned は「いまこのスレッドが持っているか」。錠を離して
        #   から積むと、別スレッドの新しい revision が先に並ぶ。
        owned = getattr(sender._lock, "_is_owned", None)
        held.append(bool(owned()) if callable(owned) else False)
        orig(snap, _closing=_closing)

    sender.putLive = _record  # type: ignore[method-assign]
    sender.applyButtons(press=[Button.A])
    sender.applyHat(2)
    sender.setStick("L", 200, 100)
    assert held, "live へ1件も入っていない"
    assert all(held), f"錠の外で列へ積んでいる: {held}"


class _RecordingSer:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)


def test_m_counts_skip_a_transport_without_a_raw_serial() -> None:
    """Given: 生シリアルを渡さない方式 / When: M計数を読む / Then: 1バイトも書かず空を返すこと。"""
    from fakes import FakeTransport
    from services.run_journal import read_m_counts

    class RawRefusing(FakeTransport):
        """bcon と同じく生シリアルを渡さない方式。"""

        def __init__(self) -> None:
            super().__init__()
            self.ser = _RecordingSer()
            self.subscribed = 0

        def get_raw_serial(self) -> None:
            return None

        def subscribe_rx(self, callback: Any) -> Any:
            _ = callback
            self.subscribed += 1
            return lambda: None

    transport = RawRefusing()
    assert read_m_counts(transport) == {}
    # ASCII の M をバイナリ列へ混ぜると1フレーム壊れる。
    assert transport.ser.writes == []


def test_m_counts_still_reads_a_transport_with_a_raw_serial() -> None:
    """Given: 生シリアルを持つ方式 / When: M計数を読む / Then: 従来どおり M を書くこと。"""
    from fakes import FakeTransport
    from services.run_journal import read_m_counts

    class RawGiving(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.ser = _RecordingSer()

        def get_raw_serial(self) -> Any:
            return self.ser

        def subscribe_rx(self, callback: Any) -> Any:
            _ = callback
            return lambda: None

    transport = RawGiving()
    read_m_counts(transport, tries=1, poll_s=0.0, settle_s=0.0)
    assert b"M\n" in transport.ser.writes, transport.ser.writes


def test_live_worker_stays_silent_while_the_line_is_closed() -> None:
    """Given: まだ開いていない live 対応の線 / When: worker を回す / Then: 1行も送らないこと。"""
    from core.Keys import Button
    from core.serial.sender import Sender
    from fakes import FakeTransport

    class ClosedLiveTransport(FakeTransport):
        capability = "BCON_STATE"

        def __init__(self) -> None:
            super().__init__()
            self.rows: list[str] = []

        # 手元で判定できる方式（遠隔判定は送出ループから問い合わせない）。
        is_open_is_local = True

        def is_open(self) -> bool:
            return False

        def send_row(self, row: str, measure_perf: bool = True) -> None:
            _ = measure_perf
            self.rows.append(row)

    transport = ClosedLiveTransport()
    sender = Sender(is_show_serial=False, transport=transport)
    try:
        sender.startLiveWorker()
        sender.applyButtons(press=[Button.A])
        time.sleep(0.12)
        # 開く前に送ると毎スロット例外になり、ログを毎秒40行汚す。
        assert transport.rows == [], transport.rows
    finally:
        sender.stopLiveWorker(0.5)


def test_drop_owner_keeps_other_owners_and_clears_the_runaway_command() -> None:
    """Given: コマンド(script)と人の入力(gamepad)が押している / When: script だけ取り下げる / Then: 人の押下が残ること。"""
    from core.serial.sender import Sender
    from fakes import FakeTransport

    sender = Sender(is_show_serial=False, transport=FakeTransport())
    sender.pressButtons([1], source="script")  # 逃げたコマンドの押下
    sender.pressButtons([2], source="gamepad")  # 人が押し続けている
    assert int(sender.snapshot()["btn"]) == 3

    assert sender.dropOwner("script") is True

    # 人の押下は残る。全取り下げ（releaseAll）ならここが 0 になってしまう。
    assert int(sender.snapshot()["btn"]) == 2
    assert "script" not in sender.getOwners()["btn"]


def test_drop_owner_queues_a_state_without_the_runaway_press() -> None:
    """Given: 逃げたコマンドの押下が残る / When: 送り元を取り下げる / Then: 次の状態にその押下が無いこと。"""
    from core.serial.sender import Sender
    from fakes import FakeTransport

    sender = Sender(is_show_serial=False, transport=FakeTransport())
    sender._liveCapable = lambda *a, **k: True  # type: ignore[method-assign]
    pushed: list[dict[str, Any]] = []

    def _spy(snap: dict[str, Any] | None = None, *, _closing: bool = False) -> None:
        if snap is not None:
            pushed.append(snap)

    sender.putLive = _spy  # type: ignore[method-assign]
    sender.pressButtons([1], source="script")
    sender.dropOwner("script")

    assert pushed, "取り下げが列へ積まれていない"
    # 線に残る最後の状態がこれ。押下が残っていると Switch 側で押しっぱなしになる。
    assert int(pushed[-1]["btn"]) == 0, pushed[-1]


def test_cross_owner_sticks_do_not_roll_back() -> None:
    """Given: script が左、gamepad が右を申告している / When: script が左を動かす / Then: 右が巻き戻らないこと。"""
    from core.serial.sender import Sender
    from fakes import FakeTransport

    sender = Sender(is_show_serial=False, transport=FakeTransport())
    sender.setStick("L", 100, 100, source="script")
    sender.setStick("R", 200, 100, source="gamepad")
    sender.setStick("L", 150, 100, source="script")

    snap = sender.snapshot()
    assert (snap["lx"], snap["ly"]) == (150, 100), snap
    # 左の再申告で、触っていない右の gamepad 側まで巻き戻ってはいけない。
    assert (snap["rx"], snap["ry"]) == (200, 100), snap


def test_restore_owner_keeps_saved_stick_coordinates() -> None:
    """Given: script の左スティックを退避した / When: gamepad が右を動かした後に戻す / Then: 退避した左だけが戻ること。"""
    from core.serial.sender import Sender
    from fakes import FakeTransport

    sender = Sender(is_show_serial=False, transport=FakeTransport())
    sender.setStick("L", 100, 100, source="script")
    sender.setStick("R", 200, 100, source="gamepad")
    assert sender.suspendOwner("script") is True
    sender.setStick("R", 180, 100, source="gamepad")
    assert sender.restoreOwner("script") is True

    snap = sender.snapshot()
    assert (snap["lx"], snap["ly"]) == (100, 100), snap
    assert (snap["rx"], snap["ry"]) == (180, 100), snap


def test_open_after_slow_close_restarts_live_worker() -> None:
    """Given: 送出で詰まったまま close が join を諦めた線 / When: すぐ開き直す / Then: worker を起こし直すこと。"""
    import threading

    from core import Transport
    from core.serial.sender import Sender
    from fakes import FakeTransport

    class SlowLiveTransport(FakeTransport):
        name = "slowlive"
        capability = Transport.PICO_LIVE_STATE
        # 手元で開閉を判定できる方式（送出ループが問い合わせる）。
        is_open_is_local = True

        def __init__(self) -> None:
            super().__init__()
            self.release = threading.Event()
            self.blocked = threading.Event()
            self._block_once = True

        def send_row(self, row: str, measure_perf: bool = True) -> None:
            if self._block_once:
                self._block_once = False
                self.blocked.set()
                # 実線の書き込みタイムアウト／ドライバ停止の代役。
                self.release.wait(5.0)
            super().send_row(row, measure_perf=measure_perf)

    transport = SlowLiveTransport()
    sender = Sender(is_show_serial=False, transport=transport)
    try:
        assert sender.openSerial(1) is True
        assert transport.blocked.wait(3.0), "worker が送信で詰まっていない"

        sender.closeSerial()
        # close は join を諦めて戻る（古い worker はまだ詰まったまま）。
        assert sender.isLiveWorkerRunning() is True

        # 開き直しが join で待っている間に、詰まっていた送信を放す。
        timer = threading.Timer(0.2, transport.release.set)
        timer.start()
        try:
            assert sender.openSerial(1) is True
        finally:
            timer.cancel()

        # 古い worker が抜けたあと、代わりの worker が動き続けること。
        assert _wait_until(sender.isLiveWorkerRunning, timeout=2.0), (
            "開き直したのに worker が居ない（無音のまま）"
        )

        before = len(transport.rows)
        sender.setStick("L", 200, 100, source="script")
        expected = sender.encodePicoState(sender.snapshot())
        assert _wait_until(
            lambda: len(transport.rows) > before and transport.rows[-1] == expected
        ), transport.rows
    finally:
        transport.release.set()
        sender.stopLiveWorker(1.0)


def test_legacy_close_sends_neutral_and_resets_state() -> None:
    """Given: legacy の線で A を押した / When: 閉じる / Then: 中立の行を送り、押下状態も入力ログも残さないこと。"""
    from core.Keys import Button
    from core.serial.sender import Sender
    from fakes import FakeTransport

    transport = FakeTransport()
    sender = Sender(is_show_serial=False, transport=transport)
    assert sender.openSerial(1) is True
    sender.pressButtons([Button.A], source="keyboard")
    sender.sendPosture()
    assert transport.rows, "押下の行が出ていない"
    pressed = int(transport.rows[-1].split()[0], 16)
    assert pressed & 0xFFFC != 0, transport.rows

    sender.closeSerial()

    # 線に残る最後の行が中立であること（下位2bitは変化印）。
    parts = transport.rows[-1].split()
    assert int(parts[0], 16) & 0xFFFC == 0, transport.rows
    assert int(parts[1]) == 8, transport.rows
    assert int(sender.snapshot()["btn"]) == 0
    # 閉じたあとに押下が残ると、次の接続の差分が狂う。
    assert sender.input_logger._btn == 0
