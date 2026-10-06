"""履歴タブの契約。実 Tk。本体より先に書くため今は RED。

履歴は新しい順に並び、選び直して再実行できる。存在しない命令の
行は薄く出し、実行の対象にしない。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from core import CommandHistory
from ui.command_panel import CommandPanelMixin
from ui.history_panel import HistoryPanelMixin


class _PyCmd:
    """一覧にある Python 命令の代わり。名前だけ要る。"""

    NAME = "PyCmd"


class _McuCmd:
    """一覧にある MCU 命令の代わり。名前だけ要る。"""

    NAME = "McuCmd"


class _FakeRunner:
    """走行器の偽物。状態（idle / running / stopping）を切り替えられる。"""

    def __init__(self) -> None:
        self._busy = False
        self.state = "idle"
        self.running_command: Any = None
        self.history_dirty = False
        self._current: Any = None

    def is_busy(self) -> bool:
        return self._busy or self.state != "idle"

    @property
    def current_history_entry(self) -> Any:
        return self._current


class _Host(HistoryPanelMixin, CommandPanelMixin):
    """組み立てに要る口だけを持つ器。足りない口は何もしない関数で返す。"""

    def __init__(self, window: tk.Toplevel) -> None:
        self.root: Any = window
        self.tab_command = ttk.Frame(window)
        self.tab_history = ttk.Frame(window)
        self.setting_nb = ttk.Notebook(window)
        self.setting_nb.add(self.tab_command, text="コマンド")
        self.setting_nb.add(self.tab_history, text="履歴")
        self.setting_nb.pack(fill="both", expand=True)
        self.open_folder_img = tk.PhotoImage(master=window, width=16, height=16)
        self.runner: Any = _FakeRunner()
        self.command_history: list[Any] = []
        self.start_calls: list[str] = []
        self.stop_calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None

    def startPlay(self, *args: Any) -> None:
        """開始のふり。呼ばれた回数だけ残す。"""
        _ = args
        self.start_calls.append("start")

    def stopPlay(self) -> None:
        """停止のふり。呼ばれた回数だけ残す。"""
        self.stop_calls.append("stop")

    def assignCommand(self) -> None:
        """選び直しは何もしない（素名の解決だけ検証するため）。"""


def _double_click(widget: tk.Misc, x: int, y: int) -> None:
    """ダブルクリックを起こす。

    Tk は event_generate("<Double-1>") を受け付けないため、時刻の近い
    押下と解放を 2 回重ね、Tk 自身にダブルクリックと判定させる。
    """
    for t in (1000, 1050):
        widget.event_generate("<ButtonPress-1>", x=x, y=y, time=t)
        widget.event_generate("<ButtonRelease-1>", x=x, y=y, time=t + 5)


def _row_ids(host: _Host) -> list[str]:
    return list(host.history_tv.get_children())


def _row_name(host: _Host, iid: str) -> str:
    values = host.history_tv.item(iid, "values")
    assert isinstance(values, tuple)
    return str(values[2])


@pytest.fixture
def host(tk_root: tk.Tk) -> Iterator[_Host]:
    window = tk.Toplevel(tk_root)
    try:
        h = _Host(window)
        h._build_command_frame()
        h.py_map = {"PyCmd": _PyCmd, "PyCmd2": _PyCmd}
        h.mcu_map = {"McuCmd": _McuCmd}
        h._shown_names = {
            h.py_cb: {"PyCmd表示": "PyCmd", "PyCmd2表示": "PyCmd2"},
            h.mcu_cb: {"McuCmd表示": "McuCmd"},
        }
        h.py_cb["values"] = ("PyCmd表示", "PyCmd2表示")
        h.mcu_cb["values"] = ("McuCmd表示",)
        now = datetime(2026, 1, 2, 3, 4, 5)
        entries: list[Any] = []
        missing = CommandHistory.begin(
            entries,
            CommandHistory.KIND_PYTHON,
            "消えたコマンド",
            now,
        )
        CommandHistory.finish(missing, CommandHistory.RESULT_DONE, 5.0)
        mcu = CommandHistory.begin(entries, CommandHistory.KIND_MCU, "McuCmd", now)
        CommandHistory.finish(mcu, CommandHistory.RESULT_DONE, 75.0)
        py = CommandHistory.begin(entries, CommandHistory.KIND_PYTHON, "PyCmd", now)
        CommandHistory.finish(py, CommandHistory.RESULT_DONE, 1.0)
        h.command_history = entries
        h._history_confirm = lambda: True
        h._build_history_frame()
        # 隠れたタブの Treeview は描画されず bbox が空になる。利用者が
        # 行を叩くときは必ず履歴タブが開いているので、それに合わせる。
        h.setting_nb.select(h.tab_history)
        window.geometry("900x480")
        window.update()
        yield h
    finally:
        window.destroy()


def test_rows_show_newest_first_with_formatted_values(host: _Host) -> None:
    # Given: 新しい順に 3 件の履歴
    # When: 一覧を眺める
    iids = _row_ids(host)
    # Then: 新しい順に日時・種別・名・結果・所要時間が並び件数が出る
    assert [_row_name(host, iid) for iid in iids] == [
        "PyCmd",
        "McuCmd",
        "消えたコマンド",
    ]
    values = host.history_tv.item(iids[1], "values")
    assert tuple(values) == (
        "2026-01-02 03:04:05",
        "MCU",
        "McuCmd",
        "完了",
        "1:15",
    )
    assert str(host.history_count_label["text"]) == "3 件"


def test_double_click_runs_the_python_command(host: _Host) -> None:
    # Given: 先頭行（Python の PyCmd）が見えている
    host.root.update()
    iid = _row_ids(host)[0]
    box = host.history_tv.bbox(iid)
    assert isinstance(box, tuple)
    # When: 行の真ん中をダブルクリックする
    _double_click(host.history_tv, box[0] + box[2] // 2, box[1] + box[3] // 2)
    host.root.update()
    # Then: その命令が選ばれて開始が 1 回呼ばれる
    assert host.py_cb.get() == "PyCmd表示"
    assert host.start_calls == ["start"]


def test_enter_on_mcu_row_selects_mcu_page_and_runs(host: _Host) -> None:
    # Given: MCU の行に触れる
    host.root.update()
    iid = next(iid for iid in _row_ids(host) if _row_name(host, iid) == "McuCmd")
    host.history_tv.selection_set(iid)
    host.history_tv.focus(iid)
    # キーはフォーカスのある部品にしか届かない（実操作でも同じ）。
    host.history_tv.focus_force()
    host.root.update()
    # When: Enter を押す
    host.history_tv.event_generate("<Return>")
    host.root.update()
    # Then: MCU 側の頁が選ばれて開始が呼ばれる
    assert host.Command_nb.index(host.Command_nb.select()) == 1
    assert host.mcu_cb.get() == "McuCmd表示"
    assert host.start_calls == ["start"]


def test_missing_row_is_tagged_and_does_not_run(host: _Host) -> None:
    # Given: 一覧に無い命令の行
    host.root.update()
    iid = next(
        iid for iid in _row_ids(host) if _row_name(host, iid) == "消えたコマンド"
    )
    # When/Then: 薄い印が付き、叩いても開始しない
    assert "missing" in host.history_tv.item(iid, "tags")
    box = host.history_tv.bbox(iid)
    assert isinstance(box, tuple)
    _double_click(host.history_tv, box[0] + box[2] // 2, box[1] + box[3] // 2)
    host.root.update()
    assert host.start_calls == []


def test_run_button_disabled_while_busy_and_double_click_ignored(
    host: _Host,
) -> None:
    # Given: 走っている最中
    host.runner._busy = True
    host.refresh_history_view()
    host.root.update()
    # When/Then: 実行は押せず、叩いても開始しない
    assert str(host.historyRunButton["state"]) == "disabled"
    iid = _row_ids(host)[0]
    box = host.history_tv.bbox(iid)
    assert isinstance(box, tuple)
    _double_click(host.history_tv, box[0] + box[2] // 2, box[1] + box[3] // 2)
    host.root.update()
    assert host.start_calls == []


def test_delete_key_removes_selection_but_keeps_running(host: _Host) -> None:
    # Given: 実行中の行（McuCmd）と選んだ行（PyCmd）
    running = next(e for e in host.command_history if e.name == "McuCmd")
    host.runner._current = running
    host.refresh_history_view()
    iid_py = next(iid for iid in _row_ids(host) if _row_name(host, iid) == "PyCmd")
    iid_mcu = next(iid for iid in _row_ids(host) if _row_name(host, iid) == "McuCmd")
    host.history_tv.selection_set((iid_py, iid_mcu))
    host.history_tv.focus_force()
    host.root.update()
    # When: Delete を押す
    host.history_tv.event_generate("<Delete>")
    host.root.update()
    # Then: 選んだ行だけ消え、実行中の行は残り、汚れ印が立つ
    assert [e.name for e in host.command_history] == ["McuCmd", "消えたコマンド"]
    assert host.runner.history_dirty is True


def test_search_filters_rows_and_updates_count(host: _Host) -> None:
    # Given: 3 件の一覧
    # When: 語を入れて作り直す
    host.history_query.set("McuCmd")
    host.refresh_history_view()
    host.root.update()
    # Then: 該当行だけ残り、件数は絞り込み形式になる
    assert [_row_name(host, iid) for iid in _row_ids(host)] == ["McuCmd"]
    assert str(host.history_count_label["text"]) == "1 / 3 件"


def test_sorting_by_name_toggles_direction(host: _Host) -> None:
    # Given: 日時順の並び
    # When: 名前列を 1 回押す
    host.sort_history_by("name")
    host.root.update()
    # Then: 名前の昇順になる
    assert [_row_name(host, iid) for iid in _row_ids(host)] == [
        "McuCmd",
        "PyCmd",
        "消えたコマンド",
    ]
    # When: もう 1 回押す
    host.sort_history_by("name")
    host.root.update()
    # Then: 降順に反転する
    assert [_row_name(host, iid) for iid in _row_ids(host)] == [
        "消えたコマンド",
        "PyCmd",
        "McuCmd",
    ]


def test_clear_needs_confirm_and_keeps_running(host: _Host) -> None:
    # Given: 実行中の行がある履歴
    running = next(e for e in host.command_history if e.name == "McuCmd")
    host.runner._current = running
    # When: 確認で断る
    host._history_confirm = lambda: False
    host.clear_history()
    # Then: 何も消えない
    assert len(host.command_history) == 3
    # When: 確認して消す
    host._history_confirm = lambda: True
    host.clear_history()
    # Then: 実行中の行だけ残る
    assert [e.name for e in host.command_history] == ["McuCmd"]


def test_select_only_chooses_without_running(host: _Host) -> None:
    # Given: 先頭行を選んだ状態
    iid = _row_ids(host)[0]
    host.history_tv.selection_set(iid)
    # When: 選ぶだけの操作をする
    host.select_history_selection()
    host.root.update()
    # Then: 開始は呼ばず、コマンドタブが開く
    assert host.start_calls == []
    assert host.py_cb.get() == "PyCmd表示"
    assert host.setting_nb.select() == str(host.tab_command)


def test_heading_double_click_does_not_run(host: _Host) -> None:
    # Given: 行が見えている一覧
    host.root.update()
    assert _row_ids(host)
    # When: 見出しの辺りをダブルクリックする（行の外）
    _double_click(host.history_tv, 10, 5)
    host.root.update()
    # Then: 開始は呼ばれない（行叩きの検査が何でも通るわけではない）
    assert host.start_calls == []


def test_stop_button_is_disabled_while_idle(host: _Host) -> None:
    # Given: 何も走っていない
    # When: 一覧を作り直す
    host.refresh_history_view()
    # Then: 止めるものが無いので停止は押せない
    assert str(host.historyStopButton["state"]) == "disabled"


def test_stop_button_stops_a_running_command(host: _Host) -> None:
    # Given: 履歴から走らせた無限ループ系のコマンドが走っている
    host.runner.state = "running"
    host.refresh_history_view()
    # When: 履歴タブの停止を押す
    assert str(host.historyStopButton["state"]) == "normal"
    host.historyStopButton.invoke()
    # Then: 停止が 1 回要求され、開始はされない
    assert host.stop_calls == ["stop"]
    assert host.start_calls == []
    assert str(host.historyRunButton["state"]) == "disabled"


def test_stop_button_follows_runner_state_changes(host: _Host) -> None:
    # Given: 走っている最中で停止が押せる
    host.runner.state = "running"
    host._apply_runner_state()
    assert str(host.historyStopButton["state"]) == "normal"
    # When: 停止処理に入った（一覧の作り直しは来ない合図）
    host.runner.state = "stopping"
    host._apply_runner_state()
    # Then: 二重に止めないよう停止は押せなくなる
    assert str(host.historyStopButton["state"]) == "disabled"
    # When: 空きへ戻る
    host.runner.state = "idle"
    host._apply_runner_state()
    # Then: 停止は押せず、選んでいる行があれば実行できる
    assert str(host.historyStopButton["state"]) == "disabled"
