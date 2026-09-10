"""スレッド実行指摘の回帰検証。実挙動・偽物で回す。"""

from __future__ import annotations

import threading
import time
from typing import Any


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


def test_tick_is_20ms() -> None:
    """停止遅延は最大20msにする。"""
    from core.CommandOperate import OperateMixin

    assert OperateMixin._TICK == 0.02


def test_watch_interval_is_200ms() -> None:
    """停止見張りの間隔は200msにする。"""
    from services import command_runner

    assert command_runner.STOP_WATCH_MS == 200


def test_stop_waited_updates_every_tick() -> None:
    """待ち時間の表示は通知周期でなく毎回進める。"""
    from fakes import FakeCommand, FakeSer, FakeThread
    from services.command_runner import STOP_WATCH_MS

    runner, clock = _make_runner()
    runner.request_start(FakeCommand(thread=FakeThread(alive=True)), FakeSer())
    runner.request_stop()
    clock.run_next()
    # 1回分（200ms）でも表示は進む。以前は5000msまで0のままだった。
    assert runner.stop_waited == STOP_WATCH_MS


def test_watch_escalates_after_30s() -> None:
    """止まらない作業は30秒で画面を戻し線を塞ぐ。二重駆動にしない。"""
    from fakes import FakeCommand, FakeSer, StuckThread
    from services import command_runner

    assert hasattr(command_runner, "STOP_ESCALATE_MS")
    escalate = int(command_runner.STOP_ESCALATE_MS)
    assert 5000 <= escalate <= 60000

    runner, clock = _make_runner()

    class FenceSer(FakeSer):
        def __init__(self) -> None:
            super().__init__(opened=True)
            self.calls: list[str] = []

        def discardLive(self) -> bool:
            self.calls.append("discardLive")
            return True

        def stopLiveWorker(self, timeout: float = 1.0) -> bool:
            _ = timeout
            self.calls.append("stopLiveWorker")
            return True

    ser = FenceSer()
    runner.request_start(FakeCommand(thread=StuckThread(alive=True)), ser)
    runner.request_stop()
    steps = escalate // command_runner.STOP_WATCH_MS + 2
    for _ in range(steps):
        if runner.state == "idle":
            break
        clock.run_next()
    assert runner.state == "idle"
    assert "discardLive" in ser.calls or "stopLiveWorker" in ser.calls
    # 二重駆動にしない。古い作業が生きている間は新規開始を断る。
    runner.request_start(FakeCommand(thread=StuckThread(alive=True)), ser)
    assert runner.state == "idle"


def test_stop_post_from_worker_does_not_call_tk() -> None:
    """作業側からの完了は待ち行列へ積みGUI側で取り出す。二重照合を保つ。"""
    from fakes import FakeCommand, FakeSer, FakeThread

    runner, clock = _make_runner()
    cmd = FakeCommand(thread=FakeThread(alive=False))
    runner.request_start(cmd, FakeSer())
    token = int(runner._run_token)
    calls: list[str] = []
    orig_schedule = clock.schedule

    def guarded(ms: int, fn: Any) -> Any:
        calls.append(threading.current_thread().name)
        return orig_schedule(ms, fn)

    runner._schedule = guarded  # type: ignore[method-assign]
    done: threading.Event = threading.Event()

    def worker() -> None:
        runner.stop_post(token)
        done.set()

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    assert done.wait(2.0)
    th.join(2.0)
    # 作業スレッドから直接の予約はしない。GUI側の取り出しで戻す。
    assert calls == []
    assert runner.state == "running"
    assert hasattr(runner, "drain_completions")
    runner.drain_completions()
    clock.run_all()
    assert runner.state == "idle"


def test_shutdown_fences_before_join_and_slices() -> None:
    """終了時は線を先に塞ぎ短く区切って待つ。閉じた線へ書かない。"""

    from fakes import FakeCommand, FakeSer
    from services.command_runner import CommandRunner

    order: list[str] = []

    class OrderThread:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout: float | None = None) -> None:
            assert timeout is not None and timeout <= 0.25 + 1e-9
            order.append(f"join:{timeout}")
            time.sleep(0.01)

    class OrderSer(FakeSer):
        def discardLive(self) -> bool:
            order.append("discardLive")
            return True

        def stopLiveWorker(self, timeout: float = 1.0) -> bool:
            _ = timeout
            order.append("stopLiveWorker")
            return True

    clock_calls: list[str] = []

    def _sched(ms: int, fn: Any) -> int:
        _ = (ms, fn)
        clock_calls.append("schedule")
        return 1

    runner = CommandRunner(
        stats={},
        schedule=_sched,
        cancel=lambda _id: None,
        notify_user=lambda m: None,
        on_state_changed=lambda: None,
        on_list_refresh=lambda: None,
    )
    cmd = FakeCommand(thread=OrderThread())  # type: ignore[arg-type]
    runner.request_start(cmd, OrderSer())
    t0 = time.perf_counter()
    ok = runner.shutdown(OrderSer())
    dt = time.perf_counter() - t0
    assert ok is False
    assert order[0] in ("discardLive", "stopLiveWorker")
    assert any(o.startswith("join:") for o in order)
    assert dt < 2.0
    # 閉じた後の書き込みは握る。残党が書いても例外にしない。
    assert runner.state in ("stopping", "running", "idle")


def test_dialog_mainloop_dead_aborts_as_cancel() -> None:
    """主循環が死んだ置き去りは有界でCancel扱いにし掃除する。"""
    import time as _time

    from Commands.CommandDialog import DialogMixin

    class StubRoot:
        def after(self, _ms: int, _fn: Any) -> Any:
            return 1  # 受け付けるが決して走らせない（主循環死）

    class Cmd(DialogMixin):
        def __init__(self) -> None:
            import threading as _th

            self._stop_event = _th.Event()
            self.message_dialogue = None
            self.gui_root: Any = StubRoot()

        def checkIfAlive(self) -> bool:
            if self._stop_event.is_set():
                from core.CommandOperate import StopThread

                raise StopThread("stop")
            return True

    cmd = Cmd()
    cmd._DIALOGUE_OPEN_TIMEOUT = 0.2  # type: ignore[attr-defined]
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["v"] = cmd.dialogue("題", ["項目"])
        except Exception as e:  # noqa: BLE001 - 結果の記録用
            box["e"] = e

    th = threading.Thread(target=run, daemon=True)
    t0 = _time.perf_counter()
    th.start()
    th.join(2.0)
    dt = _time.perf_counter() - t0
    assert not th.is_alive()
    assert dt < 2.0
    assert "v" in box
    # 置き去り abort は停止経路と同じ Cancel 形状にする（need=list なら []）。
    # timeout だけ None を返すと、呼び出し側の shape 分岐が割れる。
    assert box["v"] == []
    assert cmd.message_dialogue is None


def test_dialog_mainloop_dead_aborts_as_cancel_dict_shape() -> None:
    """置き去り abort の Cancel 形状は need=dict なら {}。"""
    from Commands.CommandDialog import DialogMixin

    class StubRoot:
        def after(self, _ms: int, _fn: Any) -> Any:
            return 1  # 受け付けるが決して走らせない（主循環死）

    class Cmd(DialogMixin):
        def __init__(self) -> None:
            import threading as _th

            self._stop_event = _th.Event()
            self.message_dialogue = None
            self.gui_root: Any = StubRoot()

        def checkIfAlive(self) -> bool:
            if self._stop_event.is_set():
                from core.CommandOperate import StopThread

                raise StopThread("stop")
            return True

    cmd = Cmd()
    cmd._DIALOGUE_OPEN_TIMEOUT = 0.2  # type: ignore[attr-defined]
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["v"] = cmd.dialogue("題", ["項目"], need=dict)
        except Exception as e:  # noqa: BLE001 - 結果の記録用
            box["e"] = e

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(2.0)
    assert not th.is_alive()
    assert "v" in box
    assert box["v"] == {}
    assert cmd.message_dialogue is None


def test_gui_tasks_bounded_drop_oldest() -> None:
    """対話委譲の待ち行列は有界にし、溢れた分は古い方から捨てて数える。"""
    from Commands import CommandDialog as D

    try:
        for _ in range(D._GUI_TASKS_MAX + 20):
            assert D._post_gui_task(object(), lambda: None) is True
        assert D._GUI_TASKS.qsize() <= D._GUI_TASKS_MAX
        assert D._take_gui_dropped() >= 20
    finally:
        D._purge_gui_tasks(None)


def test_purge_gui_tasks_by_root() -> None:
    """root 単位で溜まりを捨てられる（主循環死の後始末用）。"""
    from Commands import CommandDialog as D

    class Root:
        pass

    r1, r2 = Root(), Root()
    try:
        D._post_gui_task(r1, lambda: None)
        D._post_gui_task(r2, lambda: None)
        assert D._purge_gui_tasks(r1) == 1
        assert D._GUI_TASKS.qsize() == 1
    finally:
        D._purge_gui_tasks(None)


def test_cui_dialog_polls_stop(monkeypatch: Any) -> None:
    """CUI待機も停止を見て止まる。"""
    import threading as _th
    import tkinter as tk

    import Commands.CommandDialog as _dlg
    from Commands.CommandDialog import DialogMixin
    from core.CommandOperate import StopThread

    class Cmd(DialogMixin):
        def __init__(self) -> None:
            self._stop_event = _th.Event()
            self.message_dialogue = None
            self.gui_root = None

        def checkIfAlive(self) -> bool:
            if self._stop_event.is_set():
                raise StopThread("stop")
            return True

        def _guiRoot(self) -> Any | None:
            return None

    cmd = Cmd()

    # 実窓を出さず模擬で待たせる。停止中に作っても止まること。
    class FakeTop:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

    class SlowDlg:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            import time as _t

            _t.sleep(0.5)

        def ret_value(self, _need: Any) -> Any:
            return ["x"]

    monkeypatch.setattr(_dlg, "PokeConDialogue", SlowDlg)
    monkeypatch.setattr(tk, "Toplevel", FakeTop)
    try:
        cmd._stop_event.set()
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["v"] = cmd.dialogue("題", ["項目"])
            except StopThread as e:
                box["e"] = e
            except Exception as e:  # noqa: BLE001 - 結果の記録用
                box["other"] = e

        th = _th.Thread(target=run, daemon=True)
        th.start()
        th.join(2.0)
        assert not th.is_alive()
        assert "e" in box and isinstance(box["e"], StopThread)
    finally:
        cmd._stop_event.clear()


def test_wakesetup_close_drains_and_clears_busy() -> None:
    """閉じるとき溜まりを捨て使用中を戻す。閉じた後の届けは無視する。"""
    import queue as _q

    from WakeSetup import WakeSetup

    obj = WakeSetup.__new__(WakeSetup)
    obj._sender = object()
    obj._queue = _q.Queue()
    obj._busy = True
    obj._stop = False
    obj._closed = False

    class StubWindow:
        def winfo_exists(self) -> bool:
            return False

        def destroy(self) -> None:
            return None

        def after(self, _ms: int, _fn: Any) -> Any:
            return None

    obj.window = StubWindow()  # type: ignore[attr-defined]
    obj._queue.put(("log", "あと"))
    obj._queue.put(("done", ""))
    obj.close()
    assert obj._busy is False
    assert obj._queue.empty()
    # 閉じた後の届けは溜めない（捨てる）。
    obj._guarded(lambda: None)
    assert obj._queue.empty()
    assert obj._busy is False


def test_token_double_check_on_gui_side() -> None:
    """世代照合は積む時点＋走る時点の二重で行う。古い届けは捨てる。"""
    from fakes import FakeCommand, FakeSer, FakeThread

    runner, clock = _make_runner()
    cmd1 = FakeCommand(thread=FakeThread(alive=False))
    runner.request_start(cmd1, FakeSer())
    token1 = int(runner._run_token)
    # 作業側から古い世代を積む（積む時点の照合はGUI側で行う）。
    runner._pending.put(token1)
    # その間に次を開始する。
    runner.post_on_gui(token1)
    assert runner.state == "idle"
    cmd2 = FakeCommand(thread=FakeThread(alive=False))
    runner.request_start(cmd2, FakeSer())
    assert runner.state == "running"
    # 古い届けを取り出しても捨てる（積む時点の照合）。
    runner.drain_completions()
    assert runner.state == "running"
    assert runner.running_command is cmd2
    # 走る時点の照合も保つ。直接呼んでも古い世代は無視する。
    runner.post_on_gui(token1)
    assert runner.state == "running"
    assert runner.running_command is cmd2


def test_stress_start_stop_many() -> None:
    """開始・停止を繰り返しても固まらない。"""
    from fakes import FakeCommand, FakeSer, FakeThread

    runner, clock = _make_runner()
    for _ in range(30):
        cmd = FakeCommand(thread=FakeThread(alive=False))
        runner.request_start(cmd, FakeSer())
        assert runner.state == "running"
        runner.request_stop()
        clock.run_all()
        assert runner.state == "idle"


def test_stress_stop_during_dialog_many(monkeypatch: Any) -> None:
    """対話中の停止を繰り返しても置き去りにしない。"""
    import threading as _th

    from Commands.CommandDialog import DialogMixin
    from core.CommandOperate import StopThread

    class Cmd(DialogMixin):
        def __init__(self) -> None:
            self._stop_event = _th.Event()
            self.message_dialogue = None
            self.gui_root: Any = None

        def checkIfAlive(self) -> bool:
            if self._stop_event.is_set():
                raise StopThread("stop")
            return True

        def _guiRoot(self) -> Any | None:
            return None

    import tkinter as tk

    import Commands.CommandDialog as _dlg

    class FakeTop:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

    class SlowDlg:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            import time as _t

            _t.sleep(0.2)

        def ret_value(self, _need: Any) -> Any:
            return ["x"]

    monkeypatch.setattr(_dlg, "PokeConDialogue", SlowDlg)
    monkeypatch.setattr(tk, "Toplevel", FakeTop)
    for _ in range(10):
        cmd = Cmd()
        cmd._stop_event.set()
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["v"] = cmd.dialogue("題", ["項目"])
            except StopThread as e:
                box["e"] = e

        th = _th.Thread(target=run, daemon=True)
        th.start()
        th.join(2.0)
        assert not th.is_alive()
        assert "e" in box
        cmd._stop_event.clear()


def test_stress_exit_mid_run_many() -> None:
    """実行中の終了を繰り返しても閉じた線へ書かない。"""
    from fakes import FakeCommand, FakeSer, StuckThread

    for _ in range(10):
        runner, _clock = _make_runner()

        class NoWriteSer(FakeSer):
            def __init__(self) -> None:
                super().__init__(opened=True)
                self.writes = 0

            def discardLive(self) -> bool:
                return True

            def stopLiveWorker(self, timeout: float = 1.0) -> bool:
                _ = timeout
                return True

        ser = NoWriteSer()
        runner.request_start(FakeCommand(thread=StuckThread(alive=True)), ser)
        t0 = time.perf_counter()
        ok = runner.shutdown(ser)
        dt = time.perf_counter() - t0
        assert ok is False
        assert dt < 2.0
        assert ser.writes == 0


def test_stop_latency_p99_under_tick() -> None:
    """停止要求→抜けのp99は刻み＋余裕に収まる。"""
    import threading as _th

    from core.CommandOperate import OperateMixin, StopThread

    class Cmd(OperateMixin):
        def __init__(self) -> None:
            import threading as _t2

            self.keys: Any = None
            self.alive = True
            self._stop_event = _t2.Event()
            self._resume_event = _t2.Event()
            self._resume_event.set()
            self._paused_total = 0.0
            self._pause_started = 0.0

        def _cleanup(self, *_a: Any, **_k: Any) -> None:
            return None

        def _pausedSeconds(self) -> float:
            return 0.0

    lat: list[float] = []
    for _ in range(20):
        cmd = Cmd()
        outcome: dict[str, Any] = {}

        def run() -> None:
            t_start = time.perf_counter()
            try:
                cmd.wait(5.0)
            except StopThread:
                outcome["dt"] = time.perf_counter() - t_start
            except Exception as e:  # noqa: BLE001 - 記録用
                outcome["other"] = e

        th = _th.Thread(target=run, daemon=True)
        th.start()
        time.sleep(0.05)
        cmd._stop_event.set()
        cmd.alive = False
        th.join(2.0)
        assert not th.is_alive()
        assert "dt" in outcome
        # 要求時点からの抜け遅延を見る（待ち合わせ分を除く）。
        lat.append(float(outcome["dt"]) - 0.05)
    lat.sort()
    p99 = lat[min(len(lat) - 1, int(len(lat) * 0.99))]
    assert p99 < 0.05, f"p99={p99:.3f}s"
