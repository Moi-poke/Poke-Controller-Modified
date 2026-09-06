"""CommandRunner の状態遷移の検証。画面なし・時計は偽物で回す。"""

from typing import Any

from fakes import FakeCommand, FakeScheduler, FakeSer, FakeThread, StuckThread
from services.command_runner import STOP_NOTIFY_MS, STOP_WATCH_MS, CommandRunner


def make_runner(
    stats: dict[str, dict] | None = None,
) -> tuple[CommandRunner, FakeScheduler, list[str], list[str], list[str]]:
    if stats is None:
        stats = {}
    clock = FakeScheduler()
    notices: list[str] = []
    states: list[str] = []
    refreshes: list[str] = []
    runner = CommandRunner(
        stats=stats,
        schedule=clock.schedule,
        cancel=clock.cancel,
        notify_user=notices.append,
        on_state_changed=lambda: states.append("changed"),
        on_list_refresh=lambda: refreshes.append("refresh"),
    )
    return runner, clock, notices, states, refreshes


def test_start_runs_and_records() -> None:
    runner, clock, notices, states, refreshes = make_runner()
    cmd = FakeCommand()
    runner.request_start(cmd, FakeSer())
    assert runner.state == "running"
    assert runner.running_command is cmd
    assert cmd.start_calls and cmd.start_calls[0][0] is not None
    assert runner.stats_dirty is True
    assert states  # 見た目への合図が出ている
    assert refreshes  # 一覧の作り直しの合図が出ている
    assert any("Start" in m for m in notices)
    _ = clock


def test_double_start_refused() -> None:
    runner, _, notices, _, _ = make_runner()
    runner.request_start(FakeCommand(), FakeSer())
    runner.request_start(FakeCommand(), FakeSer())
    assert runner.state == "running"
    assert any("すでに" in m for m in notices)


def test_start_without_command() -> None:
    runner, _, notices, _, _ = make_runner()
    runner.request_start(None, FakeSer())
    assert runner.state == "idle"


def test_requires_serial_gate() -> None:
    runner, _, notices, _, _ = make_runner()
    cmd: Any = FakeCommand()
    cmd.REQUIRES_SERIAL = True
    runner.request_start(cmd, FakeSer(opened=False))
    assert runner.state == "idle"
    assert any("COM" in m for m in notices)
    runner.request_start(cmd, FakeSer(opened=True))
    assert runner.state == "running"


def test_start_exception_recovers() -> None:
    runner, _, notices, states, _ = make_runner()
    runner.request_start(FakeCommand(start_raises=RuntimeError("boom")), FakeSer())
    assert runner.state == "idle"
    assert runner.running_command is None
    assert any("開始できません" in m for m in notices)
    assert states  # 戻りの合図も出ている


def test_start_false_recovers() -> None:
    runner, _, _, _, _ = make_runner()
    runner.request_start(FakeCommand(start_result=False), FakeSer())
    assert runner.state == "idle"


def test_stop_then_post_restores_idle() -> None:
    runner, clock, _, _, refreshes = make_runner()
    cmd = FakeCommand(thread=FakeThread(alive=False))
    runner.request_start(cmd, FakeSer())
    runner.request_stop()
    assert runner.state == "stopping"
    assert cmd.end_calls  # end へ送り先が渡っている
    assert cmd.resumed  # 停止前に resume で印を戻している
    assert len(clock.queued) == 1  # 見張りが予約されている
    clock.run_next()  # スレッドは死んでいるので後始末へ回る
    assert runner.state == "idle"
    assert runner.running_command is None
    assert refreshes


def test_finish_calls_post() -> None:
    runner, clock, _, _, _ = make_runner()
    cmd = FakeCommand(thread=FakeThread(alive=True))
    runner.request_start(cmd, FakeSer())
    on_done = cmd.start_calls[0][1]
    on_done()  # worker 側は schedule(0) へ積むだけ
    assert runner.state == "running"
    assert len(clock.queued) == 1
    clock.run_next()
    assert runner.state == "idle"


def test_stale_token_ignored() -> None:
    runner, clock, _, states, _ = make_runner()
    cmd = FakeCommand(thread=FakeThread(alive=False))
    runner.request_start(cmd, FakeSer())
    before = len(states)
    runner.stop_post(token=999)  # 古い世代の後始末
    assert runner.state == "running"
    assert len(states) == before
    assert not clock.queued


def test_watch_waits_for_live_thread() -> None:
    runner, clock, notices, _, _ = make_runner()
    cmd = FakeCommand(thread=FakeThread(alive=True))
    runner.request_start(cmd, FakeSer())
    runner.request_stop()
    # thread が生きている間は待ち続け、5000ms で知らせる
    steps = STOP_NOTIFY_MS // STOP_WATCH_MS
    for _ in range(steps):
        assert runner.state == "stopping"
        clock.run_next()
    assert any("停止しません" in m for m in notices)
    # 抜けたら後始末へ回る
    assert cmd.thread is not None and cmd.thread.is_alive()
    cmd.thread._alive = False
    clock.run_next()
    assert runner.state == "idle"


def test_end_exception_still_restores() -> None:
    runner, clock, notices, _, _ = make_runner()
    cmd = FakeCommand(thread=FakeThread(alive=False), end_raises=RuntimeError("boom"))
    runner.request_start(cmd, FakeSer())
    runner.request_stop()
    assert runner.state == "idle"
    assert any("停止要求で例外" in m for m in notices)
    _ = clock


def test_shutdown_clean_and_stuck() -> None:
    runner, _, _, _, _ = make_runner()
    assert runner.shutdown(FakeSer()) is True  # 走っていない

    runner2, _, notices, _, _ = make_runner()
    cmd = FakeCommand(thread=FakeThread(alive=False))
    cmd.alive = False
    runner2.request_start(cmd, FakeSer())
    assert runner2.shutdown(FakeSer()) is True

    runner3, _, notices3, _, _ = make_runner()
    stuck = FakeCommand(thread=StuckThread(alive=True))
    runner3.request_start(stuck, FakeSer())
    assert runner3.shutdown(FakeSer()) is False
    assert any("停止しないまま終了" in m for m in notices3)
    _ = notices


def test_closing_skips_post() -> None:
    runner, clock, _, _, _ = make_runner()
    runner.request_start(FakeCommand(), FakeSer())
    runner.notify_closing()
    runner.stop_post(token=runner._run_token)
    assert not clock.queued
    assert runner.state == "running"


def test_cancel_watch() -> None:
    runner, clock, _, _, _ = make_runner()
    runner.request_start(FakeCommand(thread=FakeThread(alive=True)), FakeSer())
    runner.request_stop()
    assert len(clock.queued) == 1
    runner.cancel_watch()
    assert clock.cancelled == [1]
