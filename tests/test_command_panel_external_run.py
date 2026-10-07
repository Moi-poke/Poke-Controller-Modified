"""編集画面から本体の実行器を使う入口（試し実行）の検証。画面なし。"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("tkinter")

from fakes import FakeCommand, FakeScheduler, FakeSer  # noqa: E402
from services.command_runner import CommandRunner  # noqa: E402
from ui.command_panel import CommandPanelMixin  # noqa: E402


class _Button(dict[str, Any]):
    """ttk.Button の代役（["text"] と ["state"] だけ）。"""


class _Serial:
    def __init__(self) -> None:
        self.sender = FakeSer()


class _PausingCommand(FakeCommand):
    """一時停止を持つ命令（自分で止まることもある）。"""

    def __init__(self) -> None:
        super().__init__()
        self.paused = False

    def togglePause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    def isPaused(self) -> bool:
        return self.paused


def make_panel() -> Any:
    """CommandPanelMixin を画面なしで組む（必要な属性だけ持たせる）。"""
    clock = FakeScheduler()
    panel: Any = CommandPanelMixin.__new__(CommandPanelMixin)
    panel.runner = CommandRunner(
        stats={},
        schedule=clock.schedule,
        cancel=clock.cancel,
        notify_user=lambda _m: None,
        on_state_changed=lambda: None,
        on_list_refresh=lambda: None,
    )
    panel.pauseButton = _Button(text="一時停止", state="normal")
    panel._paused = False
    panel.cur_command = None
    panel.serial = _Serial()
    panel.built = []
    panel.snapshots = []
    panel.titles = 0

    def build(cls: Any) -> Any:
        cmd = cls()
        panel.built.append(cmd)
        return cmd

    panel._buildCommand = build
    panel._snapshotSerialConfig = panel.snapshots.append

    def title() -> None:
        panel.titles += 1

    panel._update_title = title
    return panel


def test_external_start_builds_and_runs_the_given_class() -> None:
    """渡したクラスを本体と同じ手順で作って走らせ、成功なら None を返すこと。"""
    panel = make_panel()
    # When: 試し実行のクラスを渡して開始
    message = panel.start_external_command(_PausingCommand)
    # Then: 作られた命令が走っている・COM の写しも行われた
    assert message is None
    assert panel.runner.state == "running"
    assert panel.runner.running_command is panel.built[0]
    assert panel.snapshots == [panel.built[0]]


def test_external_start_is_refused_while_another_command_runs() -> None:
    """別のコマンドが走っている間は開始せず、理由を返すこと。"""
    panel = make_panel()
    panel.runner.request_start(FakeCommand(), FakeSer())
    message = panel.start_external_command(_PausingCommand)
    assert message and "実行中" in message
    assert panel.built == []


def test_external_start_reports_a_class_that_cannot_be_built() -> None:
    """作れなかったときは開始せず、理由を返すこと。"""
    panel = make_panel()
    panel._buildCommand = lambda cls: None
    message = panel.start_external_command(_PausingCommand)
    assert message and "作れません" in message
    assert panel.runner.state == "idle"


def test_pause_targets_the_running_command_not_the_selected_one() -> None:
    """一時停止は一覧で選んでいるものではなく、いま走っている命令に効くこと。"""
    panel = make_panel()
    panel.start_external_command(_PausingCommand)
    running = panel.built[0]
    # Given: 一覧では別のコマンドを選んでいる
    panel.cur_command = _PausingCommand()
    # When: 本体の一時停止
    panel.togglePause()
    # Then: 走っている方が止まり、表示も再開になる
    assert running.paused is True
    assert panel.cur_command.paused is False
    assert panel.pauseButton["text"] == "再開"


def test_pause_label_follows_a_command_that_paused_itself() -> None:
    """区切り等で命令が自分から止まったら、本体のボタン表示も「再開」へ揃えること。"""
    panel = make_panel()
    panel.start_external_command(_PausingCommand)
    running = panel.built[0]
    # When: 命令が自分で止まる（作業スレッド側）→ GUI 側で揃える
    running.paused = True
    panel.syncPauseState()
    # Then: 本体のボタンも再開になる
    assert panel.pauseButton["text"] == "再開"
    assert panel._paused is True
    # When: 再開された
    running.paused = False
    panel.syncPauseState()
    assert panel.pauseButton["text"] == "一時停止"


def test_set_pause_only_changes_when_needed() -> None:
    """指定の状態にだけ揃える（二度押しで逆に戻さない）こと。"""
    panel = make_panel()
    panel.start_external_command(_PausingCommand)
    running = panel.built[0]
    panel.set_running_paused(True)
    panel.set_running_paused(True)
    assert running.paused is True
    panel.set_running_paused(False)
    assert running.paused is False
