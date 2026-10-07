"""CommandRunner と履歴の契約。画面なし・時計は偽物で回す。"""

import time
from datetime import datetime
from typing import Any

from core import CommandHistory
from fakes import FakeCommand, FakeScheduler, FakeSer, FakeThread
from services.command_runner import CommandRunner


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


def make_history_runner(
    times: list[float] | None = None,
) -> tuple[CommandRunner, FakeScheduler, list[Any]]:
    runner, clock, _, _, _ = make_runner()
    entries: list[Any] = []
    now = datetime(2026, 1, 2, 3, 4, 5)
    clock_times = [10.0, 15.5] if times is None else times

    def _monotonic() -> float:
        return clock_times.pop(0)

    def _kind_of(command: Any) -> str:
        _ = command
        return CommandHistory.KIND_PYTHON

    runner.set_history(entries, _kind_of, lambda: now, _monotonic)
    return runner, clock, entries


def test_start_adds_one_running_history_entry() -> None:
    # Given: 履歴を渡した走行器
    runner, _, entries = make_history_runner()
    # When: 開始できる
    runner.request_start(FakeCommand(), FakeSer())
    # Then: 履歴が 1 件増え、種別は kind_of の戻りで結果は実行中
    assert len(entries) == 1
    assert entries[0].kind == CommandHistory.KIND_PYTHON
    assert entries[0].result == CommandHistory.RESULT_RUNNING
    assert runner.history_dirty is True
    assert runner.current_history_entry is entries[0]


def test_runner_without_set_history_behaves_as_before() -> None:
    # Given: 履歴を渡さない走行器
    runner, clock, _, _, _ = make_runner()
    # When: 開始して止める
    runner.request_start(FakeCommand(thread=FakeThread(alive=False)), FakeSer())
    runner.request_stop()
    clock.run_next()
    # Then: 例外なく従来どおり動く
    assert runner.state == "idle"


def test_start_raises_adds_no_history() -> None:
    # Given: 履歴を渡した走行器
    runner, _, entries = make_history_runner()
    # When: 開始に失敗する
    runner.request_start(FakeCommand(start_raises=RuntimeError("boom")), FakeSer())
    # Then: 履歴は増えない
    assert entries == []
    assert runner.current_history_entry is None


def test_start_false_adds_no_history() -> None:
    # Given: 履歴を渡した走行器
    runner, _, entries = make_history_runner()
    # When: 開始が False で戻る
    runner.request_start(FakeCommand(start_result=False), FakeSer())
    # Then: 履歴は増えない
    assert entries == []
    assert runner.current_history_entry is None


def test_second_start_of_double_start_adds_no_history() -> None:
    # Given: 走り始めた走行器
    runner, _, entries = make_history_runner()
    runner.request_start(FakeCommand(), FakeSer())
    assert len(entries) == 1
    # When: 二重に開始する
    runner.request_start(FakeCommand(), FakeSer())
    # Then: 2 件目は増えない
    assert len(entries) == 1


def test_none_command_adds_no_history() -> None:
    # Given: 履歴を渡した走行器
    runner, _, entries = make_history_runner()
    # When: 空の命令で開始する
    runner.request_start(None, FakeSer())
    # Then: 履歴は増えない
    assert entries == []


def test_natural_finish_records_done_and_elapsed() -> None:
    # Given: 走り始めた走行器（時計は 10.0 秒に始まる）
    runner, clock, entries = make_history_runner()
    cmd = FakeCommand(thread=FakeThread(alive=True))
    runner.request_start(cmd, FakeSer())
    # When: 完了の届けを片づける
    cmd.start_calls[0][1]()
    clock.run_next()
    # Then: 結果は完了で秒は時計の差、実行中の行は消える
    assert entries[0].result == CommandHistory.RESULT_DONE
    assert entries[0].seconds == 5.5
    assert runner.current_history_entry is None
    assert runner.history_dirty is True


def test_manual_stop_records_stopped() -> None:
    # Given: 走り始めた走行器
    runner, clock, entries = make_history_runner()
    runner.request_start(FakeCommand(thread=FakeThread(alive=False)), FakeSer())
    # When: 止めて見張りを進める（線は死んでいる）
    runner.request_stop()
    clock.run_next()
    # Then: 結果は手動停止で実行中の行は消える
    assert runner.state == "idle"
    assert entries[0].result == CommandHistory.RESULT_STOPPED
    assert runner.current_history_entry is None


def test_failed_command_records_error() -> None:
    # Given: 走り始めた走行器
    runner, clock, entries = make_history_runner()
    cmd = FakeCommand(thread=FakeThread(alive=True))
    runner.request_start(cmd, FakeSer())
    # When: 失敗印のまま完了させる
    setattr(cmd, "_history_failed", True)
    cmd.start_calls[0][1]()
    clock.run_next()
    # Then: 終了理由が完了でも結果はエラー
    assert entries[0].result == CommandHistory.RESULT_ERROR
    assert runner.current_history_entry is None


def test_kind_of_error_keeps_running_without_history() -> None:
    # Given: 種別判定が落ちる履歴付きの走行器
    runner, _, _, _, _ = make_runner()
    entries: list[Any] = []

    def _bad_kind(command: Any) -> str:
        _ = command
        raise RuntimeError("boom")

    runner.set_history(entries, _bad_kind, datetime.now, time.monotonic)
    # When: 開始する
    runner.request_start(FakeCommand(), FakeSer())
    # Then: 状態は実行中のままで履歴は増えない
    assert runner.state == "running"
    assert entries == []


def test_two_runs_close_newest_first() -> None:
    # Given: 2 回ぶんの時計を持つ走行器
    runner, clock, entries = make_history_runner(times=[10.0, 11.0, 20.0, 23.0])
    first = FakeCommand(thread=FakeThread(alive=True))
    first.NAME = "first"
    second = FakeCommand(thread=FakeThread(alive=True))
    second.NAME = "second"
    # When: 2 回続けて走らせる
    runner.request_start(first, FakeSer())
    first.start_calls[0][1]()
    clock.run_next()
    runner.request_start(second, FakeSer())
    second.start_calls[0][1]()
    clock.run_next()
    # Then: 新しい方が先頭でそれぞれ正しく閉じる
    assert [e.name for e in entries] == ["second", "first"]
    assert entries[0].result == CommandHistory.RESULT_DONE
    assert entries[0].seconds == 3.0
    assert entries[1].result == CommandHistory.RESULT_DONE
    assert entries[1].seconds == 1.0
    assert runner.current_history_entry is None


def test_trial_run_from_the_editor_is_not_counted_or_kept_in_history() -> None:
    """編集画面の試し実行は、使用回数にも実行履歴にも残さないこと（再実行できないため）。"""
    # Given: 履歴を渡した走行器と、試し実行の印つきコマンド
    runner, _, entries = make_history_runner()
    stats: dict[str, dict] = runner._stats
    cmd: Any = FakeCommand()
    cmd.POKECON_TRIAL = True
    # When: 開始する
    runner.request_start(cmd, FakeSer())
    # Then: 走ってはいるが、回数・履歴は増えない
    assert runner.state == "running"
    assert entries == []
    assert stats == {}
    assert runner.stats_dirty is False
    # 対照: 印の無いコマンドは数える（検査が効いていることの確認）
    other, _, other_entries = make_history_runner()
    other.request_start(FakeCommand(), FakeSer())
    assert len(other_entries) == 1
