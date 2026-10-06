#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""history_panel.py - 実行履歴タブの組み立てと選び直し・再実行の画面側。

PokeControllerApp に混ぜて使う（多重継承）。1 回の実行 = 1 行を
新しい順に並べ、ダブルクリック / Enter で選び直して実行できる。
存在しない命令の行は薄く出し、実行の対象にしない。

CommandStats（回数 + 最終日時）とは別物で、そちらには触らない。
Tk ウィジェットに触るのは GUI スレッドだけにする（呼び出し元は
すべて GUI スレッドの合図）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from collections.abc import Callable
from typing import Any

from core import CommandHistory
from loguru import logger

# 列名 → 見出し文字。並べ替えの印（▲▼）はここへ足す。
_HISTORY_TITLES: dict[str, str] = {
    "started": "日時",
    "kind": "種別",
    "name": "コマンド",
    "result": "結果",
    "seconds": "所要時間",
}

_HISTORY_COLUMNS: tuple[str, ...] = ("started", "kind", "name", "result", "seconds")


class HistoryPanelMixin:
    """履歴パネルMixin。単体では使わない。"""

    root: Any
    tab_history: Any
    tab_command: Any
    setting_nb: Any
    runner: Any
    command_history: list[Any]
    py_map: dict[str, Any]
    mcu_map: dict[str, Any]
    startPlay: Any
    stopPlay: Any
    selectCommandByName: Any
    history_tv: Any
    history_query: Any
    history_search_entry: Any
    history_count_label: Any
    historyRunButton: Any
    historyStopButton: Any
    historySelectButton: Any
    historyDeleteButton: Any
    historyClearButton: Any
    history_menu: Any
    _history_sort_column: str
    _history_sort_desc: bool
    _history_confirm: Callable[[], bool]

    def _build_history_frame(self) -> None:
        # 並べ替えの初期値は日時順（新しい方が先）。欄を作るたびに戻す。
        self._history_sort_column = "started"
        self._history_sort_desc = True
        # 検証で差し替えた確認手順は残す。ここで上書きすると検証も
        # 利用者の操作も同じ問いかけになり、取り違えの検査ができない。
        if "_history_confirm" not in self.__dict__:
            self._history_confirm = self._ask_history_clear_confirm
        self.history_lf = ttk.Labelframe(self.tab_history, text="実行履歴")
        self.history_lf.pack(fill="both", expand=True, padx=5, pady=5)

        # 上段は検索と件数。探す→選ぶ が上から下へ並ぶよう検索を上に置く。
        search_f = ttk.Frame(self.history_lf)
        search_f.pack(fill="x", padx=5, pady=2, side="top")
        ttk.Label(search_f, text="検索").pack(side="left", padx="2")
        self.history_query = tk.StringVar()
        self.history_search_entry = ttk.Entry(search_f, textvariable=self.history_query)
        self.history_search_entry.pack(side="left", fill="x", expand=True, padx="2")
        # 打つそばから絞る。確定を待つと、目当てが出るまで見えない。
        self.history_search_entry.bind(
            "<KeyRelease>", self._on_history_search_changed, add=""
        )
        # Esc は検索語を捨てて抜ける。迷子になったときの戻り道。
        # "break" で止めるのは、検索欄にいる間の Esc を停止操作へ
        # 伝えないため（打ち間違いで走行中が止まると困る）。
        self.history_search_entry.bind(
            "<Escape>", self._on_history_search_escape, add=""
        )
        self.history_search_entry.bind(
            "<Return>", self._on_history_search_leave, add=""
        )
        self.history_search_entry.bind(
            "<KP_Enter>", self._on_history_search_leave, add=""
        )
        self.history_count_label = ttk.Label(search_f, text="0 件")
        self.history_count_label.pack(side="right", padx="2")

        # 中段は一覧。名前だけ伸ばし、日時・種別・結果・所要時間は幅を保つ。
        list_f = ttk.Frame(self.history_lf)
        list_f.pack(fill="both", expand=True, padx=5, pady="2", side="top")
        self.history_tv = ttk.Treeview(
            list_f,
            columns=_HISTORY_COLUMNS,
            show="headings",
            selectmode="extended",
            height=8,
        )
        for column in _HISTORY_COLUMNS:
            self.history_tv.heading(
                column,
                text=_HISTORY_TITLES[column],
                command=lambda c=column: self.sort_history_by(c),
            )
        self.history_tv.column("started", width=150, stretch=False)
        self.history_tv.column("kind", width=70, stretch=False)
        self.history_tv.column("name", width=220, stretch=True)
        self.history_tv.column("result", width=110, stretch=False)
        self.history_tv.column("seconds", width=80, stretch=False)
        # 実行中は青系、失敗は赤系、存在しない命令は薄く出す。
        # 色そのものより「行の状態が見た目で分かる」ことが要点。
        self.history_tv.tag_configure("running", foreground="#0066CC")
        self.history_tv.tag_configure("error", foreground="#CC0000")
        self.history_tv.tag_configure("missing", foreground="gray50")
        scrollbar = ttk.Scrollbar(
            list_f, orient="vertical", command=self.history_tv.yview
        )
        self.history_tv.configure(yscrollcommand=scrollbar.set)
        self.history_tv.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        # 行の真ん叩きだけが実行になる。見出し・区切りは対象にしない。
        self.history_tv.bind("<Double-1>", self._on_history_double_click)
        self.history_tv.bind("<Return>", self._on_history_return_key)
        self.history_tv.bind("<KP_Enter>", self._on_history_return_key)
        self.history_tv.bind("<Delete>", self._on_history_delete_key)
        self.history_tv.bind("<Control-c>", self._on_history_copy_key)
        self.history_tv.bind("<Control-C>", self._on_history_copy_key)
        self.history_tv.bind("<<TreeviewSelect>>", self._on_history_selected)
        self.history_tv.bind("<Button-3>", self._on_history_right_click)

        # 下段は操作。選ぶ→実行 が左から右へ続くよう並べる。
        button_f = ttk.Frame(self.history_lf)
        button_f.pack(fill="x", padx=5, pady=(0, 5), side="top")
        self.historyRunButton = ttk.Button(
            button_f, text="実行", command=self.run_history_selection
        )
        self.historyRunButton.pack(side="left", padx="2")
        # 履歴から走らせた無限ループ系（手動停止が前提）のコマンドを、
        # コマンドタブへ戻らずにその場で止められるようにする。実行と
        # 兼用にしないのは、ダブルクリックの打ち損じで止まらないため。
        self.historyStopButton = ttk.Button(
            button_f, text="停止", command=self.stop_from_history
        )
        self.historyStopButton.pack(side="left", padx="2")
        self.historySelectButton = ttk.Button(
            button_f, text="選択", command=self.select_history_selection
        )
        self.historySelectButton.pack(side="left", padx="2")
        self.historyDeleteButton = ttk.Button(
            button_f, text="削除", command=self.delete_history_selection
        )
        self.historyDeleteButton.pack(side="left", padx="2")
        self.historyClearButton = ttk.Button(
            button_f, text="全消去", command=self.clear_history
        )
        self.historyClearButton.pack(side="left", padx="2")

        # 右クリックの一覧。ボタンと同じ手順に通す（二重に持たない）。
        self.history_menu = tk.Menu(self.root, tearoff=0)
        self.history_menu.add_command(label="実行", command=self.run_history_selection)
        self.history_menu.add_command(label="停止", command=self.stop_from_history)
        self.history_menu.add_command(
            label="コマンドタブで選択", command=self.select_history_selection
        )
        self.history_menu.add_command(
            label="コマンド名をコピー", command=self.copy_history_selection
        )
        self.history_menu.add_separator()
        self.history_menu.add_command(
            label="履歴から削除", command=self.delete_history_selection
        )
        self.history_menu.add_command(
            label="履歴をすべて消去", command=self.clear_history
        )
        self._update_history_heading_marks()
        self.refresh_history_view()

    def _ask_history_clear_confirm(self) -> bool:
        """全消去の確認。検証では差し替えて使う。"""
        try:
            return bool(
                tkmsg.askyesno(
                    "履歴の消去",
                    "実行履歴をすべて消去しますか？",
                    parent=self.root,
                )
            )
        except Exception as e:
            logger.warning(f"履歴消去の確認が出せませんでした: {e}")
            return False

    def _history_entry_by_iid(self, iid: str) -> Any | None:
        """行番号（entry.id の文字）から履歴の行を引く。無ければ None。"""
        try:
            wanted = int(iid)
        except (TypeError, ValueError):
            return None
        for entry in self.command_history:
            if getattr(entry, "id", None) == wanted:
                return entry
        return None

    def _history_is_missing(self, entry: Any) -> bool:
        """その行のコマンドが現在の一覧に無いか。"""
        if entry.kind == CommandHistory.KIND_MCU:
            return entry.name not in self.mcu_map
        return entry.name not in self.py_map

    def refresh_history_view(self) -> None:
        """一覧を作り直す。選択と先頭位置はできるだけ保つ。

        欄がまだ無い（組み立て前）の呼び出しでは何もしない。終了直後や
        再読み込みの合図は組み立て順によらず飛んで来るため。
        """
        tv = self.__dict__.get("history_tv")
        if tv is None:
            return
        query = str(self.history_query.get())
        entries = CommandHistory.filtered(self.command_history, query)
        column = self.__dict__.get("_history_sort_column", "started")
        desc = bool(self.__dict__.get("_history_sort_desc", True))
        ordered = sorted(
            entries,
            key=lambda e: CommandHistory.sort_key(e, column),
            reverse=desc,
        )
        # 作り直しの前後で選択と先頭位置を保つ。消えた行は捨てる。
        kept = set(tv.selection())
        try:
            top = tv.yview()[0]
        except Exception:
            top = None
        for iid in tv.get_children():
            tv.delete(iid)
        for entry in ordered:
            tags: list[str] = []
            if entry.result == CommandHistory.RESULT_RUNNING:
                tags.append("running")
            if entry.result == CommandHistory.RESULT_ERROR or str(
                entry.result
            ).startswith("打切り"):
                tags.append("error")
            if self._history_is_missing(entry):
                tags.append("missing")
            tv.insert(
                "",
                "end",
                iid=str(entry.id),
                values=(
                    entry.started,
                    CommandHistory.kind_label(entry.kind),
                    entry.name,
                    entry.result,
                    CommandHistory.format_duration(entry.seconds),
                ),
                tags=tuple(tags),
            )
        still = [iid for iid in tv.get_children() if iid in kept]
        if still:
            tv.selection_set(tuple(still))
        if top is not None:
            try:
                tv.yview_moveto(top)
            except Exception:
                pass
        total = len(self.command_history)
        if query.strip():
            self.history_count_label["text"] = f"{len(ordered)} / {total} 件"
        else:
            self.history_count_label["text"] = f"{total} 件"
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        """ボタンが押せるかを今の選択に合わせる。"""
        tv = self.__dict__.get("history_tv")
        if tv is None:
            return
        sel = tv.selection()
        entry = self._history_entry_by_iid(sel[0]) if len(sel) == 1 else None
        busy = bool(self.runner.is_busy())
        can_run = entry is not None and not self._history_is_missing(entry) and not busy
        runnable = "normal" if can_run else "disabled"
        self.historyRunButton["state"] = runnable
        # 停止は走っている間だけ押せる。停止処理の最中に押せると、
        # 止まりきらない間に二重に頼んだように見えて紛らわしい。
        running = str(getattr(self.runner, "state", "idle")) == "running"
        self.historyStopButton["state"] = "normal" if running else "disabled"
        self.historySelectButton["state"] = runnable
        self.historyDeleteButton["state"] = "normal" if len(sel) >= 1 else "disabled"
        self.historyClearButton["state"] = (
            "normal" if len(self.command_history) >= 1 else "disabled"
        )

    def _update_history_heading_marks(self) -> None:
        """並べ替えの印（▲▼）を今の列へ付ける。"""
        tv = self.__dict__.get("history_tv")
        if tv is None:
            return
        column = self.__dict__.get("_history_sort_column", "started")
        desc = bool(self.__dict__.get("_history_sort_desc", True))
        for name in _HISTORY_COLUMNS:
            text = _HISTORY_TITLES[name]
            if name == column:
                text += " ▼" if desc else " ▲"
            try:
                tv.heading(name, text=text)
            except Exception:
                pass

    def sort_history_by(self, column: str) -> None:
        """見出し押しで並べ替える。同じ列は昇順・降順を反転する。"""
        if column not in _HISTORY_COLUMNS:
            column = "started"
        if self.__dict__.get("_history_sort_column") == column:
            self._history_sort_desc = not self._history_sort_desc
        elif column == "started":
            # 日時は新しい方が先に見たいので、切り替え直後は降順にする。
            self._history_sort_column = column
            self._history_sort_desc = True
        else:
            self._history_sort_column = column
            self._history_sort_desc = False
        self._update_history_heading_marks()
        self.refresh_history_view()

    def run_history_selection(self) -> None:
        """選んだ 1 行を選び直して実行する。"""
        if self.runner.is_busy():
            print("実行中は履歴から開始できません")
            return
        sel = self.history_tv.selection()
        if len(sel) != 1:
            return
        entry = self._history_entry_by_iid(sel[0])
        if entry is None:
            return
        if self._history_is_missing(entry):
            print(f"コマンドが見つかりません: {entry.name}")
            return
        if self.selectCommandByName(entry.kind, entry.name):
            self.startPlay()

    def stop_from_history(self) -> None:
        """走っているコマンドを止める。手順はコマンドタブの停止と同じ。"""
        if str(getattr(self.runner, "state", "idle")) != "running":
            return
        self.stopPlay()

    def update_history_buttons(self) -> None:
        """実行状態の合図で呼ぶ入口。一覧は作り直さずボタンだけ見直す。"""
        self._update_history_buttons()

    def select_history_selection(self) -> None:
        """選んだ 1 行をコマンドタブで選ぶだけ（実行しない）。"""
        if self.runner.is_busy():
            print("実行中は履歴から選択できません")
            return
        sel = self.history_tv.selection()
        if len(sel) != 1:
            return
        entry = self._history_entry_by_iid(sel[0])
        if entry is None:
            return
        if self._history_is_missing(entry):
            print(f"コマンドが見つかりません: {entry.name}")
            return
        if not self.selectCommandByName(entry.kind, entry.name):
            return
        # コマンドタブを開く。枠が無い構成では選ぶだけに留める。
        notebook = getattr(self, "setting_nb", None)
        tab = getattr(self, "tab_command", None)
        if notebook is None or tab is None:
            return
        try:
            notebook.select(tab)
        except Exception as e:
            logger.warning(f"コマンドタブを開けませんでした: {e}")

    def delete_history_selection(self) -> None:
        """選んだ行を履歴から消す。実行中の行は残す。"""
        sel = self.history_tv.selection()
        if not sel:
            return
        ids: list[int] = []
        for iid in sel:
            try:
                ids.append(int(iid))
            except (TypeError, ValueError):
                continue
        if not ids:
            return
        running = self.runner.current_history_entry
        running_id = getattr(running, "id", None)
        targets = [i for i in ids if i != running_id]
        if not targets:
            return
        removed = CommandHistory.remove(self.command_history, targets)
        if removed:
            self.runner.history_dirty = True
            self.refresh_history_view()

    def clear_history(self) -> None:
        """履歴をすべて消す。実行中の行は残す。確認が要る。"""
        if not self._history_confirm():
            return
        CommandHistory.clear(
            self.command_history, keep=self.runner.current_history_entry
        )
        self.runner.history_dirty = True
        self.refresh_history_view()

    def copy_history_selection(self) -> None:
        """選んだ行のコマンド名を表示順に改行区切りで写す。"""
        sel = set(self.history_tv.selection())
        if not sel:
            return
        names: list[str] = []
        for iid in self.history_tv.get_children():
            if iid in sel:
                values = self.history_tv.item(iid, "values")
                if values:
                    names.append(str(values[2]))
        if not names:
            return
        text = "\n".join(names)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception as e:
            logger.warning(f"履歴の写しに失敗しました: {e}")

    def _on_history_search_changed(self, event: Any = None) -> None:
        """検索欄の入力で絞り直す。"""
        _ = event
        self.refresh_history_view()

    def _on_history_search_escape(self, event: Any = None) -> str:
        """検索語を捨てて検索欄から抜ける（停止操作へ伝えない）。"""
        _ = event
        self.history_query.set("")
        self.refresh_history_view()
        try:
            self.root.focus_set()
        except Exception:
            pass
        return "break"

    def _on_history_search_leave(self, event: Any = None) -> Any:
        """検索欄からフォーカスを外す。"""
        _ = event
        try:
            self.root.focus_set()
        except Exception:
            pass
        return "break"

    def _on_history_double_click(self, event: Any) -> None:
        """行の真ん叩きで実行する。見出し・区切りは対象にしない。"""
        try:
            region = self.history_tv.identify_region(event.x, event.y)
        except Exception:
            return
        if region not in ("cell", "tree"):
            return
        # ダブルクリックの前にその行を選ぶ。押した行と走る行が
        # ずれないようにするため（選択だけ古いまま残る事故を防ぐ）。
        try:
            row = self.history_tv.identify_row(event.x, event.y)
        except Exception:
            row = ""
        if row:
            self.history_tv.selection_set(row)
            self.history_tv.focus(row)
            self._update_history_buttons()
        self.run_history_selection()

    def _on_history_return_key(self, event: Any = None) -> None:
        """Enter で選んだ行を実行する。"""
        _ = event
        self.run_history_selection()

    def _on_history_delete_key(self, event: Any = None) -> None:
        """Delete で選んだ行を消す。"""
        _ = event
        self.delete_history_selection()

    def _on_history_copy_key(self, event: Any = None) -> None:
        """Ctrl+C でコマンド名を写す。"""
        _ = event
        self.copy_history_selection()

    def _on_history_selected(self, event: Any = None) -> None:
        """選択が変わったらボタンを見直す。"""
        _ = event
        self._update_history_buttons()

    def _on_history_right_click(self, event: Any) -> None:
        """右クリックした行を選んで一覧を出す。"""
        try:
            row = self.history_tv.identify_row(event.x, event.y)
        except Exception:
            return
        if row and row not in self.history_tv.selection():
            self.history_tv.selection_set(row)
            self.history_tv.focus(row)
            self._update_history_buttons()
        try:
            self.history_menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass
        finally:
            try:
                self.history_menu.grab_release()
            except Exception:
                pass
