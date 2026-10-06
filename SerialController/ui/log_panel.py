#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log_panel.py - ログ欄の組み立てと表示ポンプを受け持つMixin.

PokeControllerApp に混ぜて使う（多重継承）。状態は self 越しに
触るため、触る属性は下に宣言しておく（mypy のため。値は Window 側が持つ）。
1欄ぶんの描画は ui/log_view.py の LogView、キューは LogPane が持つ。
ここは欄どうしの連携（下の欄から上へ飛ぶ・検索の対象・絞り込み）と、
一定間隔の取り出しを行う。

ログ欄の形（2026-10 に作り直し）
--------------------------------
「ログ」タブ
  上の欄 … すべての出力を時系列で。水準ごとに色と記号、時刻、
           同じ行のまとめ（×N）、末尾にいる間だけ追従。
  下の欄 … 結果とエラー。print2 の出力、エラー・警告、手でピン留め
           した行だけが残る。上の欄は流れて消えるが、こちらは残る。
           行をクリックすると、上の欄のその時点へ飛んで前後が見える
           （IDE の「問題」パネルと出力の関係）。
「入力」タブ
  シリアルへ送った操作のログ（量の桁が違うので別タブのまま）。
下端のツールバー
  検索（Ctrl+F、Enter で次・Shift+Enter で前）、エラー/警告の件数
  （押すと次の箇所へ）、「表示」メニュー（絞り込み・時刻・折り返し・
  まとめ・集約・入力ログの記録・コピー・保存・消去）。
"""

from __future__ import annotations

import functools
import gc
import time
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.ttk as ttk
import traceback
from typing import Any

import LogPane
import WindowGeometry
from core.log_model import (
    FILTER_GROUPS,
    FILTER_LABELS,
    PROBLEM_LEVELS,
    LineClassifier,
    LogEntry,
    entries_for,
    visible_levels,
)
from loguru import logger
from services.serial_service import SerialService
from ui.log_view import LogView, level_tag
from ui.tooltip import Tooltip

_STREAM_HINT = "コマンドの出力がここに流れます。"
_PIN_HINT = (
    "print2 の出力・エラー・警告・ピン留めした行がここに残ります。\n"
    "行をクリックすると、上の欄のその時点へ移動します。"
)
_INPUT_HINT = "コントローラーへ送った操作がここに出ます。"
_INPUT_OFF_HINT = "入力ログの記録は止まっています。\n「表示」メニューから再開できます。"
#: 下の欄の見出しに出す知らせを消すまでの時間。
_STATUS_MS = 4000
#: 検索語の入力から実際に探すまでの待ち（打つたびに全文を走らせない）。
_FIND_DEBOUNCE_MS = 200


class LogPanelMixin:
    """ログ欄Mixin。単体では使わない。"""

    frame_1: Any
    log_area: Any
    root: Any
    settings: Any
    serial: SerialService
    log_nb: Any
    log_pane: Any
    logView: LogView
    pinView: LogView
    inputView: LogView
    logArea: Any
    subLogArea: Any
    inputLogArea: Any
    show_input_log: Any
    log_show_time: Any
    log_wrap: Any
    log_group_similar: Any
    log_collect_problems: Any
    log_bar: Any
    log_search_var: Any
    log_find_status: Any
    log_status_var: Any
    log_filter_vars: dict[str, Any]
    log_problem_buttons: dict[str, Any]
    _log_search_entry: Any
    _log_find_after: Any
    _log_status_after: Any
    _log_target: Any
    _log_classifier: LineClassifier
    _log_problem_counts: dict[str, int]
    _pin_title: Any
    _closing: bool
    _display_after_id: Any
    _sash_after_id: Any
    _sash_restore_attempts: int
    _stat_ticks: int
    camera: Any
    preview: Any
    runner: Any
    _on_setting_changed: Any

    # -- 組み立て ------------------------------------------------------------

    def _build_log_area(self) -> None:
        """ログ欄を組み立てる。

        入力ログは1操作で press と release の2行が出るため、コマンドの
        print と同じ欄に流すと押し流されて読めなくなる（量の桁が違う）。
        タブを分けて、必要なときだけ見に行けるようにしている。

        「ログ」タブの上下は役割で分ける。上は流れる時系列、下は残す一覧。
        従来は下が print2 専用で、使わないスクリプトでは常に空だった。
        エラー・警告も下へ集めることで、どのスクリプトでも下の欄が働く。
        """
        self._log_classifier = LineClassifier()
        self._log_problem_counts = {level: 0 for level in PROBLEM_LEVELS}
        self._log_find_after = None
        self._log_status_after = None
        # 表示の設定。設定ファイルはこの後（Window.loadSettings）で読まれる
        # ため、ここでは既定値で組み、_load_log_view_settings で合わせる。
        self.log_show_time = tk.BooleanVar(master=self.root, value=True)
        self.log_wrap = tk.BooleanVar(master=self.root, value=True)
        self.log_group_similar = tk.BooleanVar(master=self.root, value=True)
        self.log_collect_problems = tk.BooleanVar(master=self.root, value=True)
        self.show_input_log = tk.BooleanVar(master=self.root, value=True)

        self.log_area = ttk.Frame(self.frame_1)
        self.log_area.rowconfigure(0, weight=1)
        self.log_area.columnconfigure(0, weight=1)
        self.log_nb = ttk.Notebook(self.log_area)

        self.log_pane = ttk.PanedWindow(self.log_nb, orient="vertical")
        self.logView = LogView(self.log_pane, empty_hint=_STREAM_HINT)
        # weight を付けておくと、ウィンドウを広げた分が両方へ配分される
        self.log_pane.add(self.logView, weight=3)

        pin_frame = ttk.Frame(self.log_pane)
        pin_frame.rowconfigure(1, weight=1)
        pin_frame.columnconfigure(0, weight=1)
        head = ttk.Frame(pin_frame)
        head.grid(row=0, column=0, sticky="ew")
        self._pin_title = ttk.Label(head, text="結果とエラー")
        self._pin_title.pack(side="left", padx=(4, 0))
        self.log_status_var = tk.StringVar(master=self.root, value="")
        ttk.Label(head, textvariable=self.log_status_var, foreground="#7A4A00").pack(
            side="left", padx=(8, 0)
        )
        clear_pins = ttk.Button(
            head, text="消去", width=5, command=self._clear_pins, takefocus=False
        )
        clear_pins.pack(side="right")
        Tooltip(clear_pins, "下の欄（結果とエラー）だけを消します")
        self.pinView = LogView(
            pin_frame, empty_hint=_PIN_HINT, on_activate=self._on_pin_activated
        )
        self.pinView.text.configure(cursor="hand2")
        self.pinView.grid(row=1, column=0, sticky="nsew")
        self.log_pane.add(pin_frame, weight=2)
        self.log_nb.add(self.log_pane, text="ログ")

        # 入力ログは書式（InputLogConfig）が時刻を持つので、ここでは出さない。
        # 押下ごとの行は回数より時刻の並びが大事なのでまとめない。
        self.inputView = LogView(
            self.log_nb, empty_hint=_INPUT_HINT, show_time=False, group_similar=False
        )
        self.log_nb.add(self.inputView, text="入力")
        self.log_nb.grid(column=0, padx="5", pady=(5, 2), row=0, sticky="nsew")

        # 従来の名前（Window・E2E が参照）。実体は各欄の Text。
        self.logArea = self.logView.text
        self.subLogArea = self.pinView.text
        self.inputLogArea = self.inputView.text
        self._log_target = self.logView
        for view in (self.logView, self.pinView, self.inputView):
            view.text.bind(
                "<FocusIn>", lambda _e, v=view: self._set_log_target(v), add="+"
            )
            view.text.bind(
                "<ButtonPress-1>", lambda _e, v=view: self._set_log_target(v), add="+"
            )
            view.text.bind("<Button-3>", lambda e, v=view: self._log_menu(e, v))
        self.log_nb.bind("<<NotebookTabChanged>>", self._on_log_tab_changed, add="+")

        self._build_log_toolbar()
        # 仕切り位置の復元は、ウィジェットの大きさが確定してからでないと
        # 効かない（構築直後は高さが1のため sashpos が無視される）。
        self._sash_after_id = self.root.after_idle(self._restore_sash)

    def _build_log_toolbar(self) -> None:
        """ログ欄の下の1段。左に検索、右に件数と「表示」メニュー。

        欄が狭くても1段に収まるよう、常に使う物（検索・件数）だけを出し、
        たまにしか変えない物はメニューへ入れる。
        """
        bar = ttk.Frame(self.log_area)
        bar.grid(column=0, padx="5", pady=(0, 4), row=1, sticky="ew")
        # レイアウト切替（layout_panel の _apply_layout）で出し入れするため、
        # ローカル変数だと外から触れず「隠せない」バーになる。
        self.log_bar = bar
        bar.columnconfigure(3, weight=1)

        self.log_search_var = tk.StringVar(master=self.root, value="")
        self.log_find_status = tk.StringVar(master=self.root, value="")
        entry = ttk.Entry(bar, textvariable=self.log_search_var, width=18)
        entry.grid(row=0, column=0, sticky="w")
        self._log_search_entry = entry
        Tooltip(entry, "ログを検索（Ctrl+F）。Enter で次、Shift+Enter で前、Esc で解除")
        entry.bind("<Return>", lambda _e: self._log_find_step(False))
        entry.bind("<Shift-Return>", lambda _e: self._log_find_step(True))
        entry.bind("<Escape>", self._log_find_cancel)
        # 何の欄か分かるよう、空のときだけ薄く書いておく（ttk.Entry には
        # placeholder が無いので、ラベルを重ねて出し入れする）。
        hint = ttk.Label(
            entry, text="検索（Ctrl+F）", foreground="#767676", background="#FFFFFF"
        )
        hint.bind("<Button-1>", lambda _e: entry.focus_set())

        def _sync_hint(*_args: Any) -> None:
            if self.log_search_var.get() or entry.focus_get() is entry:
                hint.place_forget()
            else:
                hint.place(x=4, rely=0.5, anchor="w")

        entry.bind("<FocusIn>", _sync_hint, add="+")
        entry.bind("<FocusOut>", _sync_hint, add="+")
        self.log_search_var.trace_add("write", _sync_hint)
        _sync_hint()
        self.log_search_var.trace_add("write", self._on_search_typed)
        prev_btn = ttk.Button(
            bar, text="▲", width=2, command=lambda: self._log_find_step(True)
        )
        prev_btn.grid(row=0, column=1)
        next_btn = ttk.Button(
            bar, text="▼", width=2, command=lambda: self._log_find_step(False)
        )
        next_btn.grid(row=0, column=2)
        Tooltip(prev_btn, "前の一致へ（Shift+Enter）")
        Tooltip(next_btn, "次の一致へ（Enter）")
        ttk.Label(bar, textvariable=self.log_find_status, foreground="#6B6B6B").grid(
            row=0, column=3, sticky="w", padx=(4, 0)
        )

        style = ttk.Style(self.root)
        style.configure("LogError.TButton", foreground="#A0001C")
        style.configure("LogWarning.TButton", foreground="#7A4A00")
        self.log_problem_buttons = {}
        for col, (level, mark, name) in enumerate(
            (("error", "✖", "エラー"), ("warning", "⚠", "警告")), start=4
        ):
            btn = ttk.Button(
                bar,
                text=f"{mark} 0",
                width=5,
                style=f"Log{level.capitalize()}.TButton",
                command=functools.partial(self._log_next_problem, level),
            )
            btn.grid(row=0, column=col, padx=(2, 0))
            Tooltip(btn, f"{name}の件数。押すと上の欄の次の{name}へ移動します")
            self.log_problem_buttons[level] = btn

        menu_btn = ttk.Menubutton(bar, text="表示", width=5)
        menu_btn.grid(row=0, column=6, padx=(4, 0))
        menu_btn["menu"] = self._build_log_view_menu(menu_btn)

        # Ctrl+F はどこにフォーカスがあっても検索欄へ。Ctrl+K（コマンド
        # パレット）と同じく root に結ぶ。
        self.root.bind("<Control-f>", self._focus_log_search, add="+")
        self.root.bind("<Control-F>", self._focus_log_search, add="+")

    def _build_log_view_menu(self, owner: Any) -> tk.Menu:
        menu = tk.Menu(owner, tearoff=False)
        self.log_filter_vars = {}
        for group in FILTER_GROUPS:
            # 絞り込みは保存しない。次の起動で「行が出ない」と迷わせないため。
            var = tk.BooleanVar(master=self.root, value=True)
            self.log_filter_vars[group] = var
            menu.add_checkbutton(
                label=f"{FILTER_LABELS[group]}を表示",
                variable=var,
                command=self._apply_log_filters,
            )
        menu.add_separator()
        menu.add_checkbutton(
            label="時刻を表示",
            variable=self.log_show_time,
            command=self._apply_log_view_settings,
        )
        menu.add_checkbutton(
            label="長い行を折り返す",
            variable=self.log_wrap,
            command=self._apply_log_view_settings,
        )
        menu.add_checkbutton(
            label="同じ行の繰り返しを ×N にまとめる",
            variable=self.log_group_similar,
            command=self._apply_log_view_settings,
        )
        menu.add_checkbutton(
            label="エラーと警告を下の欄にも集める",
            variable=self.log_collect_problems,
            command=self._apply_log_view_settings,
        )
        menu.add_checkbutton(
            label="入力ログを記録する",
            variable=self.show_input_log,
            command=self._on_input_log_toggled,
        )
        menu.add_separator()
        menu.add_command(label="この欄をすべてコピー", command=self._log_copy_all)
        menu.add_command(label="この欄をファイルに保存...", command=self._log_save)
        menu.add_separator()
        menu.add_command(label="ログをすべて消去", command=self.clearAllLogs)
        return menu

    # -- 設定の反映 ----------------------------------------------------------

    def _apply_log_view_settings(self) -> None:
        """メニューで変えたとき。反映して保存する。"""
        self._reflect_log_view()
        self._on_setting_changed()

    def _reflect_log_view(self) -> None:
        show_time = bool(self.log_show_time.get())
        wrap = bool(self.log_wrap.get())
        group = bool(self.log_group_similar.get())
        for view in (self.logView, self.pinView):
            view.set_show_time(show_time)
            view.group_similar = group
        for view in (self.logView, self.pinView, self.inputView):
            view.set_wrap(wrap)
        self.inputView.set_empty_hint(
            _INPUT_HINT if self.show_input_log.get() else _INPUT_OFF_HINT
        )

    def _load_log_view_settings(self, settings: Any) -> None:
        """設定ファイルの値を表示へ（Window._apply_settings_to_widgets から）。"""
        self.log_show_time.set(bool(settings.log_show_time.get()))
        self.log_wrap.set(bool(settings.log_wrap.get()))
        self.log_group_similar.set(bool(settings.log_group_similar.get()))
        self.log_collect_problems.set(bool(settings.log_collect_problems.get()))
        self.show_input_log.set(bool(settings.input_log_enabled.get()))
        self._reflect_log_view()

    def _store_log_view_settings(self, settings: Any) -> None:
        """表示の値を設定へ（保存の直前に Window から）。"""
        settings.log_show_time.set(bool(self.log_show_time.get()))
        settings.log_wrap.set(bool(self.log_wrap.get()))
        settings.log_group_similar.set(bool(self.log_group_similar.get()))
        settings.log_collect_problems.set(bool(self.log_collect_problems.get()))
        settings.input_log_enabled.set(bool(self.show_input_log.get()))

    def _apply_log_filters(self) -> None:
        groups = {g: bool(v.get()) for g, v in self.log_filter_vars.items()}
        shown = visible_levels(groups)
        hidden = {lv for levels in FILTER_GROUPS.values() for lv in levels} - shown
        self.logView.set_hidden_levels(hidden)
        # 絞り込みで行が消えたら、検索の件数も見えている物に合わせ直す。
        if self.log_search_var.get():
            self.log_find()

    def _on_input_log_toggled(self) -> None:
        """入力ログの ON/OFF。切ると Sender 側で出力そのものを止める。

        表示だけ止めても、行を作る処理とキューの往復は残る。元を止めた
        ほうが軽く、意図も分かりやすい。
        """
        enabled = bool(self.show_input_log.get())
        self.serial.set_input_log_enabled(enabled)
        self.settings.input_log_enabled.set(enabled)
        self.inputView.set_empty_hint(_INPUT_HINT if enabled else _INPUT_OFF_HINT)
        self._on_setting_changed()

    # -- 欄の選択（検索・コピーの対象） --------------------------------------

    def _set_log_target(self, view: LogView) -> None:
        if view is not self._log_target:
            self._log_target.clear_find()
            self._log_target = view
            self.log_find_status.set("")

    def _on_log_tab_changed(self, _event: Any = None) -> None:
        try:
            on_input = self.log_nb.index("current") == 1
        except tk.TclError:
            return
        self._set_log_target(self.inputView if on_input else self.logView)

    # -- 検索 ----------------------------------------------------------------

    def log_find(self, backwards: bool = False) -> tuple[int, int]:
        """検索語で対象の欄を探し、(何件目, 全件) を返す。状態欄も更新する。"""
        pattern = self.log_search_var.get()
        found = self._log_target.find(pattern, backwards)
        if not pattern:
            self.log_find_status.set("")
        elif found[1] == 0:
            self.log_find_status.set("見つかりません")
        else:
            self.log_find_status.set(f"{found[0]}/{found[1]}")
        return found

    def _log_find_step(self, backwards: bool) -> str:
        self.log_find(backwards)
        return "break"

    def _on_search_typed(self, *_args: Any) -> None:
        if self._log_find_after is not None:
            try:
                self.root.after_cancel(self._log_find_after)
            except (tk.TclError, ValueError):
                pass
        self._log_find_after = self.root.after(_FIND_DEBOUNCE_MS, self._run_typed_find)

    def _run_typed_find(self) -> None:
        self._log_find_after = None
        if self._closing:
            return
        # 語が変わったら一致の並びも変わる。読んでいる位置から探し直す。
        self._log_target.clear_find()
        self.log_find()

    def _log_find_cancel(self, _event: Any = None) -> str:
        self.log_search_var.set("")
        self._log_target.clear_find()
        self.log_find_status.set("")
        self._log_target.text.focus_set()
        return "break"

    def _focus_log_search(self, _event: Any = None) -> str:
        self._log_search_entry.focus_set()
        self._log_search_entry.select_range(0, "end")
        return "break"

    # -- エラー・警告への移動 ------------------------------------------------

    def _log_next_problem(self, level: str) -> None:
        """上の欄の次のエラー（警告）へ。隠していれば見えるようにする。"""
        group = "error" if level == "error" else "warning"
        var = self.log_filter_vars.get(group)
        if var is not None and not var.get():
            var.set(True)
            self._apply_log_filters()
        self.log_nb.select(0)
        if self.logView.next_tagged(level_tag(level)) is None:
            self._log_status(f"{'エラー' if level == 'error' else '警告'}はありません")

    def _update_problem_buttons(self) -> None:
        marks = {"error": "✖", "warning": "⚠"}
        for level, btn in self.log_problem_buttons.items():
            btn.configure(text=f"{marks[level]} {self._log_problem_counts[level]}")
        count = self.pinView.line_count()
        self._pin_title.configure(
            text=f"結果とエラー（{count}）" if count else "結果とエラー"
        )

    # -- 下の欄から上の欄へ --------------------------------------------------

    def _on_pin_activated(self, pin_abs: int) -> None:
        anchor = self.pinView.anchors.get(pin_abs)
        if anchor is None:
            self._log_status("この行は上の欄に対応する位置がありません")
            return
        line = self.logView.line_of(anchor)
        if line is None:
            self._log_status("元の行は上の欄から消えています（上限超過または消去）")
            return
        if self.logView.is_line_hidden(line):
            for var in self.log_filter_vars.values():
                var.set(True)
            self._apply_log_filters()
        self.logView.jump_to_line(line)

    def _log_status(self, message: str) -> None:
        self.log_status_var.set(message)
        if self._log_status_after is not None:
            try:
                self.root.after_cancel(self._log_status_after)
            except (tk.TclError, ValueError):
                pass
        self._log_status_after = self.root.after(_STATUS_MS, self._clear_log_status)

    def _clear_log_status(self) -> None:
        self._log_status_after = None
        try:
            self.log_status_var.set("")
        except tk.TclError:
            pass

    def _pin_line(self, line: int) -> None:
        """上の欄の1行を下の欄へピン留めする（右クリックから）。"""
        view = self.logView
        tags = view.text.tag_names(f"{line}.0 lineend")
        level = next((t[3:] for t in tags if t.startswith("lv_")), "info")
        entry = LogEntry(view.line_text(line), level)
        self.pinView.append([entry], anchors=[view.abs_of(line)])
        self._update_problem_buttons()

    # -- 右クリック ----------------------------------------------------------

    def _log_menu(self, event: Any, view: LogView) -> str:
        self._set_log_target(view)
        line = view.line_at(event.x, event.y)
        has_line = 1 <= line <= view.line_count()
        has_sel = bool(view.text.tag_ranges("sel"))
        menu = tk.Menu(view.text, tearoff=False)
        if view is self.logView:
            menu.add_command(
                label="この行を下の欄にピン留め",
                command=lambda: self._pin_line(line),
                state="normal" if has_line else "disabled",
            )
            menu.add_separator()
        if view is self.pinView:
            menu.add_command(
                label="上の欄のこの位置へ移動",
                command=lambda: self._on_pin_activated(view.abs_of(line)),
                state="normal" if has_line else "disabled",
            )
            menu.add_separator()
        menu.add_command(
            label="選択範囲をコピー",
            command=lambda: self._copy_text(view.text.get("sel.first", "sel.last")),
            state="normal" if has_sel else "disabled",
        )
        menu.add_command(
            label="この行をコピー",
            command=lambda: self._copy_text(view.line_text(line)),
            state="normal" if has_line else "disabled",
        )
        menu.add_command(label="この欄をすべてコピー", command=self._log_copy_all)
        menu.add_command(label="この欄をファイルに保存...", command=self._log_save)
        menu.add_separator()
        menu.add_command(label="この欄を消去", command=lambda: self._clear_view(view))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _copy_text(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _log_copy_all(self) -> None:
        self._copy_text(self._log_target.all_text())
        self._log_status("コピーしました")

    def _log_save(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="ログを保存",
            defaultextension=".txt",
            initialfile=time.strftime("pokecon-log-%Y%m%d-%H%M%S.txt"),
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self._log_target.all_text())
        except OSError as exc:
            logger.warning(f"ログを保存できませんでした: {exc}")
            print(f"ログを保存できませんでした: {exc}")
            return
        self._log_status("保存しました")

    # -- 消去 ----------------------------------------------------------------

    def _clear_view(self, view: LogView) -> None:
        view.clear()
        if view is self.logView:
            self._log_classifier = LineClassifier()
        if view is self.pinView:
            self._log_problem_counts = {level: 0 for level in PROBLEM_LEVELS}
        self._update_problem_buttons()

    def _clear_pins(self) -> None:
        self._clear_view(self.pinView)

    def clearLog(self) -> None:
        """いま見えているタブのログを消す（ログタブは上下とも）。"""
        for view in self._active_log_views():
            self._clear_view(view)

    def clearAllLogs(self) -> None:
        for view in (self.logView, self.pinView, self.inputView):
            self._clear_view(view)

    def _active_log_views(self) -> list[LogView]:
        try:
            if self.log_nb.index("current") == 1:
                return [self.inputView]
        except tk.TclError:
            pass
        return [self.logView, self.pinView]

    def _active_log_areas(self) -> list:
        """選択中のタブに対応する Text を返す（ログタブは2枚）。"""
        return [view.text for view in self._active_log_views()]

    def _active_log_area(self) -> tk.Text:
        """後方互換。主たる1枚を返す。"""
        return self._active_log_areas()[0]

    # -- 映像の実測 ----------------------------------------------------------

    def _log_video_stats(self) -> None:
        """取込fpsと表示fpsを5秒ごとに1行出す（ラグの切り分け用）。

        取込 < 設定なら機器・帯域側、表示 < 取込なら描画・GUI側が
        遅い。呼ばれるたびの実測ではなく期間平均を見る。
        """
        ticks = getattr(self, "_stat_ticks", 0) + 1
        self._stat_ticks = ticks
        if ticks % 25 != 0:
            return
        try:
            camera = getattr(self, "camera", None)
            preview = getattr(self, "preview", None)
            cap = camera.getStats() if camera is not None else None
            shown = preview.getStats() if preview is not None else None
        except Exception as e:
            logger.debug(f"映像実測を取れませんでした: {e}")
            return
        if cap is None:
            cap_text = "-"
        else:
            cap_text = f"{cap.get('fps', 0.0)}fps(avg {cap.get('avg_ms', 0.0)}ms)"
        if shown is None:
            shown_text = "-"
        else:
            # 同じ実測を下端の状態の 1 行にも出す（ログを読まなくても分かる）。
            publish = getattr(self, "publishVideoStats", None)
            if publish is not None:
                publish(float(shown.get("fps", 0.0)))
            shown_text = (
                f"{shown.get('fps', 0.0)}fps"
                f"(draw {shown.get('draw_ms', 0.0)}ms/max {shown.get('draw_max_ms', 0.0)}ms)"
            )
        log_ms = round(getattr(self, "_log_flush_ms", 0.0), 1)
        log_max_ms = round(getattr(self, "_log_flush_max_ms", 0.0), 1)
        gap_max_ms = round(getattr(self, "_pump_gap_max_ms", 0.0), 1)
        self._log_flush_max_ms = 0.0
        self._pump_gap_max_ms = 0.0
        # 世代別GCの回収回数の差分。突発停止（スタッター）がGC由来かを見る。
        # 回収そのものは裏で走るため、ここでは回数だけ拾う（安い）。
        try:
            gc_now = [g["collections"] for g in gc.get_stats()[:3]]
        except Exception:
            gc_now = []
        gc_prev = getattr(self, "_gc_prev", None)
        self._gc_prev = gc_now
        if gc_prev is not None and len(gc_now) == 3 and len(gc_prev) == 3:
            gc_text = f"gc +{gc_now[0] - gc_prev[0]}/+{gc_now[1] - gc_prev[1]}/+{gc_now[2] - gc_prev[2]}"
        else:
            gc_text = "gc -/-/-"
        try:
            run_state = self.runner.state
        except Exception:
            run_state = "?"
        logger.debug(
            f"映像: 取込 {cap_text} / 表示 {shown_text} / "
            f"ログ {log_ms}ms(max {log_max_ms}ms) / ポンプ間隔max {gap_max_ms}ms"
            f" / {gc_text} [{run_state}]"
        )

    # -- 仕切り位置 ---------------------------------------------------------

    def _restore_sash(self) -> None:
        if self._closing:
            return
        self._sash_after_id = None
        self._sash_restore_attempts += 1
        try:
            restored = WindowGeometry.restoreSash(self.log_pane, self.settings)
            if not restored and self._sash_restore_attempts < 50:
                self._sash_after_id = self.root.after(100, self._restore_sash)
                return
        except (tk.TclError, RuntimeError):
            return
        if not restored:
            logger.warning("ログ欄の仕切り位置を復元できませんでした")
        self.log_pane.bind("<ButtonRelease-1>", self._remember_sash, add="+")

    def _remember_sash(self, *event: Any) -> None:
        if WindowGeometry.rememberSash(self.log_pane, self.settings):
            self._on_setting_changed()

    # -- 取り出しポンプ ------------------------------------------------------

    def _to_entries(self, items: list[Any], dropped: int, kind: str) -> list[LogEntry]:
        """キューの中身を LogEntry へそろえる。kind は stream / result / input。"""
        entries: list[LogEntry] = []
        if dropped:
            entries.append(
                LogEntry(
                    f"… {dropped} 行を省略しました（出力が速すぎて表示が追いつきません）",
                    "system",
                )
            )
        for item in items:
            if isinstance(item, LogEntry):
                entries.append(item)
                continue
            # 書かれた時刻（LogPane.StampedLine）があればそれを使う。
            ts = getattr(item, "ts", None)
            if kind == "stream":
                entries.extend(self._log_classifier.entries(str(item), ts))
            elif kind == "result":
                entries.extend(entries_for(str(item), "result", "all", ts))
            else:
                entries.extend(entries_for(str(item), "input", ts=ts))
        return entries

    def _pump_logs(self) -> None:
        """キューを取り出し、上の欄へ流し、残す行を下の欄へ写す。"""
        items, dropped = LogPane.drain(LogPane.text_queue)
        stream = self._to_entries(items, dropped, "stream")
        # print2 は時系列の文脈（上の欄）と、残す一覧（下の欄）の両方へ。
        sub_items, sub_dropped = LogPane.drain(LogPane.sub_log_queue)
        stream += self._to_entries(sub_items, sub_dropped, "result")
        if stream:
            base_before = self.logView._base
            abs_lines = self.logView.append(stream)
            if self.logView._base != base_before and not getattr(
                self, "_log_trim_noticed", False
            ):
                # 黙って消えると「出たはずの行が無い」と迷わせる。一度だけ知らせる。
                self._log_trim_noticed = True
                self._log_status(
                    f"上の欄は最新 {self.logView.max_lines} 行だけを残します"
                    "（下の欄の行は残ります）"
                )
            collect = bool(self.log_collect_problems.get())
            pins: list[LogEntry] = []
            anchors: list[int] = []
            for entry, abs_line in zip(stream, abs_lines):
                if not entry.pin:
                    continue
                if entry.level in PROBLEM_LEVELS:
                    self._log_problem_counts[entry.level] += 1
                    if not collect:
                        continue
                pins.append(entry)
                anchors.append(abs_line)
            if pins:
                self.pinView.append(pins, anchors=anchors)
            self._update_problem_buttons()
        if self.show_input_log.get():
            if self.serial.is_open():
                self.serial.flush_input_log()
        in_items, in_dropped = LogPane.drain(LogPane.input_log_queue)
        if in_items or in_dropped:
            self.inputView.append(self._to_entries(in_items, in_dropped, "input"))

    def display_text(self) -> None:
        self._display_after_id = None
        if self._closing:
            return
        # ポンプ自体の間隔の最大も見る。GUI スレッドが詰まると
        # 200ms 周期が崩れる。描画もログ流しも軽いのに間隔だけ
        # 開くなら、別の after 予約や重い処理が主犯である。
        now = time.perf_counter()
        last_at = getattr(self, "_pump_last_at", None)
        self._pump_last_at = now
        if last_at is not None:
            gap_ms = (now - last_at) * 1000.0
            if gap_ms > getattr(self, "_pump_gap_max_ms", 0.0):
                self._pump_gap_max_ms = gap_ms
        try:
            flush_began = time.perf_counter()
            # 待機時高速路：全キュー空ならTcl往復を省く。
            # qsizeは目安であり、競合で残っていても次周期で拾うだけ（欠落なし）。
            if not LogPane.queues_idle_hint():
                self._pump_logs()
            flush_ms = (time.perf_counter() - flush_began) * 1000.0
            prev = getattr(self, "_log_flush_ms", 0.0)
            self._log_flush_ms = (
                flush_ms if prev <= 0.0 else prev * 0.9 + flush_ms * 0.1
            )
            if flush_ms > getattr(self, "_log_flush_max_ms", 0.0):
                self._log_flush_max_ms = flush_ms
            self._log_video_stats()
        except tk.TclError:
            if not self._closing:
                logger.debug("ログWidget破棄中の更新を停止しました")
        except Exception:
            # ここの失敗をログ欄へ流すと、壊れたポンプが自分の失敗を
            # 流し続けることになる。ファイルにだけ残す。
            logger.bind(gui=False).error(traceback.format_exc())
        finally:
            if not self._closing:
                try:
                    self._display_after_id = self.logArea.after(
                        LogPane.FLUSH_INTERVAL_MS, self.display_text
                    )
                except (tk.TclError, RuntimeError):
                    self._display_after_id = None
