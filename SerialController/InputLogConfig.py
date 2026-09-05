#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""InputLogConfig.py - 入力ログの表示書式を決める設定画面.

メニュー「入力ログの書式」から開く。プリセットを選ぶだけでも使えるし、
テンプレートを直接書いて自分好みの並びにもできる。打った内容がその場で
見本の行に反映されるので、実機を操作して確かめ直す必要がない。

書式そのものの仕様（差し込み欄・省略ブロック [ ]）は InputLog.py が持つ。
この画面は入力を受け取って InputLog.preview_lines() に見せてもらうだけで、
書式の解釈は一切持たない。二重に実装すると必ず食い違うため。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from typing import Any, Callable, Optional

import InputLog
from loguru import logger

# 対象イベントのチェックボックス。(内部名, 画面に出す名前)
ACTION_ITEMS = (
    ("PRESS", "押した時"),
    ("RELEASE", "離した時"),
    ("CHANGE", "スティックの向きが変わった時"),
)

FONT_MONO = ("Consolas", 10)
FONT_HINT = ("", 9)


class InputLogConfig:
    """入力ログの書式を決めるウィンドウ。

    値の反映は「即時」。選んだ瞬間に Sender へ渡し、settings.ini へも書く。
    ログは試しながら整えるものなので、OK を押すまで反映されないと
    「効いているのか分からない」状態が続いてしまう。
    """

    def __init__(
        self,
        master: tk.Misc,
        settings: Any,
        on_change: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        """master に載せて開く。

        settings : GuiSettings。書式・対象イベント・スティック変化の保存先
        on_change: 変更を実際に反映させるための呼び出し口（Window 側が渡す）
        on_close : 閉じたことを知らせる先。呼び出し元が参照を捨てるために使う
        """
        self.settings = settings
        self.on_change = on_change
        self.on_close = on_close
        self._closed = False
        self._loading = True  # 初期表示中は保存しない（既定値で上書きするため）

        self.win = tk.Toplevel(master)
        self.win.title("入力ログの書式")
        self.win.resizable(True, False)

        self.preset = tk.StringVar()
        self.template = tk.StringVar()
        self.stick_change = tk.BooleanVar()
        self.actions = {name: tk.BooleanVar(value=True) for name, _ in ACTION_ITEMS}

        self._build_ui()
        self._load()
        self._loading = False
        self._refresh_preview()

        # × ボタンも「閉じる」ボタンと同じ経路にする。呼び出し元が
        # protocol() を上書きしなくても後始末が漏れないようにするため。
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------
    # 画面の組み立て
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = ttk.Frame(self.win, padding=10)
        root.pack(expand=True, fill="both")
        root.columnconfigure(0, weight=1)

        self._build_preset(root)
        self._build_template(root)
        self._build_actions(root)
        self._build_preview(root)
        self._build_fields(root)
        self._build_buttons(root)

    def _build_preset(self, parent: ttk.Frame) -> None:
        """よく使う書式を名前で選ぶ欄。"""
        box = ttk.LabelFrame(parent, text="書式を選ぶ", padding=8)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)

        # 表示は「名前: 説明」。選んだあと名前だけを取り出す
        self._preset_texts = [
            f"{key}: {InputLog.PRESET_LABELS.get(key, '')}" for key in InputLog.PRESETS
        ]
        self.preset_cb = ttk.Combobox(
            box,
            state="readonly",
            values=self._preset_texts,
            textvariable=self.preset,
        )
        self.preset_cb.grid(row=0, column=0, sticky="ew")
        self.preset_cb.bind("<<ComboboxSelected>>", self._on_preset_selected)

    def _build_template(self, parent: ttk.Frame) -> None:
        """書式を直接書く欄。プリセットもここへ展開されるので改造しやすい。"""
        box = ttk.LabelFrame(parent, text="書式（直接編集できます）", padding=8)
        box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        box.columnconfigure(0, weight=1)

        entry = ttk.Entry(box, textvariable=self.template, font=FONT_MONO)
        entry.grid(row=0, column=0, sticky="ew")
        # 打つそばから見本へ反映する。trace なら貼り付けや削除も拾える
        self.template.trace_add("write", self._on_template_changed)

        ttk.Label(
            box,
            style="Hint.TLabel",
            font=FONT_HINT,
            text="[ ] で囲むと、中身が空のときだけ丸ごと消えます"
            "（押した時は押下時間が無いので [ ({dur})] とします）。",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_actions(self, parent: ttk.Frame) -> None:
        """どの操作を記録するかの絞り込み。"""
        box = ttk.LabelFrame(parent, text="記録する操作", padding=8)
        box.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        for i, (name, label) in enumerate(ACTION_ITEMS):
            ttk.Checkbutton(
                box,
                text=label,
                variable=self.actions[name],
                command=self._on_actions_changed,
            ).grid(row=0, column=i, sticky="w", padx=(0, 12))

        ttk.Checkbutton(
            box,
            text="スティックを倒している間の変化も記録する",
            variable=self.stick_change,
            command=self._on_stick_changed,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        ttk.Label(
            box,
            font=FONT_HINT,
            text="　向きが変わったとき（15度以上）と、倒す深さが変わったとき"
            "（0.25以上）に1行出ます。「離した時」を選んでいる場合も"
            "別途この行が出ます。",
        ).grid(row=2, column=0, columnspan=3, sticky="w")

    def _build_preview(self, parent: ttk.Frame) -> None:
        """いまの書式で実際に出る行を見せる。"""
        box = ttk.LabelFrame(parent, text="この書式だとこう出ます", padding=8)
        box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        box.columnconfigure(0, weight=1)

        self.preview = tk.Text(box, height=4, wrap="none", font=FONT_MONO)
        self.preview.configure(state="disabled", relief="flat", background="#f5f5f5")
        self.preview.grid(row=0, column=0, sticky="ew")

    def _build_fields(self, parent: ttk.Frame) -> None:
        """使える差し込み欄の一覧。クリックで書式へ挿し込める。"""
        box = ttk.LabelFrame(
            parent, text="使える差し込み欄（クリックで追加）", padding=8
        )
        box.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        box.columnconfigure(0, weight=1)

        tree = ttk.Treeview(box, columns=("desc",), height=7, selectmode="browse")
        tree.heading("#0", text="欄")
        tree.heading("desc", text="意味")
        tree.column("#0", width=110, stretch=False)
        tree.column("desc", width=430)
        for field, desc in InputLog.FIELD_HELP:
            tree.insert("", "end", text=field, values=(desc,))
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<Double-1>", self._on_field_picked)
        self.field_tree = tree

        ttk.Label(
            box,
            font=FONT_HINT,
            text="行をダブルクリックすると、書式の末尾に足します。",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_buttons(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=5, column=0, sticky="ew", pady=(10, 0))

        ttk.Button(bar, text="既定に戻す", command=self.reset_to_default).pack(
            side="left"
        )
        ttk.Button(bar, text="閉じる", command=self.close).pack(side="right")

        ttk.Label(
            bar,
            font=FONT_HINT,
            text="変更はその場で反映され、設定ファイルにも保存されます。",
        ).pack(side="right", padx=(0, 10))

    # ------------------------------------------------------------------
    # 読み込み / 反映
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """settings.ini の内容を画面へ流し込む。"""
        saved = self.settings.input_log_format.get()
        # プリセット名で保存されている場合は、中身の書式へ展開して見せる。
        # 展開しておかないと「選んだ書式を少しだけ直す」ができない。
        self.template.set(InputLog.PRESETS.get(saved, saved))
        self._select_preset_of(saved)
        self.stick_change.set(bool(self.settings.input_log_stick_change.get()))

        wanted = self._saved_actions()
        for name, _ in ACTION_ITEMS:
            self.actions[name].set(name in wanted)

    def _saved_actions(self) -> tuple:
        """保存された対象イベントを返す。未設定なら書式に応じた既定。"""
        raw = ""
        getter = getattr(self.settings, "input_log_actions", None)
        if getter is not None:
            raw = str(getter.get() or "")
        if raw.strip():
            return tuple(a.strip().upper() for a in raw.split(",") if a.strip())
        saved = self.settings.input_log_format.get()
        return InputLog.PRESET_ACTIONS.get(saved) or tuple(
            name for name, _ in ACTION_ITEMS
        )

    def _select_preset_of(self, saved: str) -> None:
        """保存値がプリセットならコンボへ選択状態を反映する。"""
        for key, text in zip(InputLog.PRESETS, self._preset_texts):
            if key == saved or InputLog.PRESETS[key] == saved:
                self.preset.set(text)
                return
        self.preset.set("")  # 自作の書式。どれにも当たらない

    def _current_actions(self) -> tuple:
        return tuple(name for name, _ in ACTION_ITEMS if self.actions[name].get())

    # ------------------------------------------------------------------
    # 操作への応答
    # ------------------------------------------------------------------

    def _on_preset_selected(self, event: Any = None) -> None:
        """プリセットを選んだら、その中身を書式欄へ展開する。"""
        text = self.preset.get()
        key = text.split(":", 1)[0].strip()
        if key not in InputLog.PRESETS:
            return
        # 対象イベントもプリセットの既定へ合わせる（command は離した時だけ）
        wanted = InputLog.PRESET_ACTIONS.get(key) or tuple(
            name for name, _ in ACTION_ITEMS
        )
        for name, _ in ACTION_ITEMS:
            self.actions[name].set(name in wanted)
        self.template.set(InputLog.PRESETS[key])  # trace が保存まで行う

    def _on_template_changed(self, *args: Any) -> None:
        """書式が変わるたびに見本を作り直し、そのまま反映する。"""
        if self._loading:
            return
        self._refresh_preview()
        self._apply()

    def _on_actions_changed(self) -> None:
        if not self._current_actions():
            # 全部外すと何も記録されない。気づけないので戻す
            tkmsg.showinfo("確認", "少なくとも1つは選んでください。", parent=self.win)
            self.actions["PRESS"].set(True)
        self._refresh_preview()
        self._apply()

    def _on_stick_changed(self) -> None:
        self._apply()

    def _on_field_picked(self, event: Any = None) -> None:
        """一覧で選んだ差し込み欄を書式の末尾へ足す。"""
        item = self.field_tree.focus()
        if not item:
            return
        field = self.field_tree.item(item, "text")
        self.template.set(self.template.get() + field)

    def reset_to_default(self) -> None:
        """標準の書式へ戻す。"""
        for name, _ in ACTION_ITEMS:
            self.actions[name].set(True)
        self.stick_change.set(False)
        self._select_preset_of("simple")
        self.template.set(InputLog.DEFAULT_FORMAT)

    # ------------------------------------------------------------------
    # 見本と保存
    # ------------------------------------------------------------------

    def _refresh_preview(self) -> None:
        """いまの書式で出る行を見本欄へ書き出す。"""
        lines = InputLog.preview_lines(self.template.get(), self._current_actions())
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("end", "\n".join(lines) or "(記録される行がありません)")
        self.preview.configure(state="disabled")

    def _apply(self) -> None:
        """書式を settings.ini へ書き、Sender へも即座に反映する。"""
        if self._loading:
            return
        self.settings.input_log_format.set(self.template.get())
        self.settings.input_log_stick_change.set(bool(self.stick_change.get()))
        actions = getattr(self.settings, "input_log_actions", None)
        if actions is not None:
            actions.set(",".join(self._current_actions()))

        if self.on_change is not None:
            try:
                self.on_change()
            except Exception as e:
                # 反映に失敗しても画面は開いたままにする（書式は直せる）
                logger.warning(f"入力ログの書式を反映できませんでした: {e}")

    # ------------------------------------------------------------------
    # 終了
    # ------------------------------------------------------------------

    def close(self) -> None:
        """画面を閉じる。閉じ方が何であれ、後始末は必ずここを通す。

        「閉じる」ボタンと × ボタンで経路が分かれていると、片方だけ
        呼び出し元への通知が漏れる。実際、close() が直接 destroy して
        いたため Menubar 側が破棄済みの画面を掴んだままになり、次に
        開こうとして focus_force() で TclError になっていた。
        """
        if self._closed:
            return  # 二重に閉じても何も起きないようにする
        self._closed = True

        # 呼び出し元（Menubar）に参照を捨ててもらう。ここを通さないと
        # 「閉じたのに開けない」状態になる。
        callback, self.on_close = self.on_close, None
        try:
            self.win.destroy()
        except tk.TclError:
            pass  # 既に壊れている。閉じる目的は果たせている
        if callback is not None:
            callback()

    def destroy(self) -> None:
        self.close()

    def alive(self) -> bool:
        """まだ画面が生きているか。呼び出し元が掴み直す判断に使う。"""
        try:
            return not self._closed and bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def protocol(self, event: str, func: Callable[..., Any]) -> None:
        self.win.protocol(event, func)

    def focus_force(self) -> None:
        self.win.focus_force()
