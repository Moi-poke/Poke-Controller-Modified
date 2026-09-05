#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CommandDialog.py - 入力ダイアログ（DialogMixin / PokeConDialogue）.

PythonCommandBase.py から対話部を切り出したもの。

なぜ分けるか:
  ダイアログは tkinter に強く依存する一方、コマンドの実行制御・操作・
  画像認識とはつながりが無い。混ざっている理由が無いため分けている。

構成:
  PokeConDialogue : ダイアログの窓そのもの（tkinter の組み立て）
  DialogMixin     : コマンドから使う入口（dialogue / dialogue6widget）

親クラス（PythonCommand）に依存するもの:
  ・checkIfAlive() … 停止要求が出ていたら例外を投げる
  ・_stop_event    … 停止要求のイベント
  ・message_dialogue … 生成したダイアログの保持先
  Mixin なので、重ねる側がこれらを持っていることが前提になる。

互換:
  PythonCommandBase.py が PokeConDialogue を re-import するので、
      from Commands.PythonCommandBase import PokeConDialogue
  と書いている既存のコードはそのまま動く。
"""

from __future__ import annotations

import threading
import tkinter as tk
import tkinter.ttk as ttk
import traceback
from typing import Any, Optional

from loguru import logger


class DialogMixin:
    """入力ダイアログの入口。PythonCommand へ重ねて使う。

    外向きの API（dialogue / dialogue6widget）は名前・引数・意味とも
      1文字も変えていない。既存コマンドは1行も直さずに動く。
    """

    # 停止要求のあと、ダイアログが閉じ切るのを待つ上限(秒)。
    _DIALOGUE_CLOSE_WAIT = 1.0
    # ダイアログが開かないまま待ち続けたときに警告を出す間隔(秒)。
    _DIALOGUE_OPEN_TIMEOUT = 30.0

    def dialogue(
        self, title: str, message: int | str | list, need: type = list
    ) -> list | dict | None:
        """入力ダイアログを出し、閉じられるまで待って結果を返す。Cancel は None。"""
        return self._runDialogue(title, message, need, mode=0)

    def dialogue6widget(
        self, title: str, dialogue_list: list, need: type = list
    ) -> list | dict | None:
        """7種のウィジェットに対応した入力ダイアログ版。Cancel は None。"""
        return self._runDialogue(title, dialogue_list, need, mode=1)

    def _runDialogue(self, title: str, message: Any, need: type, mode: int) -> Any:
        """ダイアログの生成を GUI スレッドへ委譲し、結果を受け取る。

        tkinter はスレッドセーフではなく、GUI スレッド以外から widget を
        生成するとフリーズやクラッシュの原因になる。コマンドはワーカー
        スレッドで動くので、生成そのものを after(0) でメインスレッドへ
        渡し、こちらは Event で結果を待つ。

        待ちには _stop_event を併用する。ダイアログを開いたまま Stop を
        押したときに、コマンド側が永久に待ち続けないようにするため。
        呼び出し規約（戻り値）は従来と同じなので既存コマンドは無修正で動く。
        """
        done = threading.Event()
        box = {}
        holder = {}

        def build() -> None:
            # ここは GUI スレッド。widget の生成と mainloop 的な待ちは
            # すべてこの中で完結する。PokeConDialogue.__init__ の末尾は
            # wait_window で、閉じられるまでここから戻らない。
            try:
                self.message_dialogue = tk.Toplevel(root)
                holder["top"] = self.message_dialogue
                dlg = PokeConDialogue(self.message_dialogue, title, message, mode=mode)
                box["value"] = dlg.ret_value(need)
            except Exception:
                box["error"] = traceback.format_exc()
            finally:
                self.message_dialogue = None
                holder.pop("top", None)
                done.set()

        def closeOnGui() -> None:
            """ダイアログを閉じる。必ず GUI スレッドから呼ぶこと。

            既に閉じられている・Window ごと壊れている場合があるので、
            winfo_exists() を見てから destroy し、TclError は握る。
            二重 destroy と、破棄後のアクセスを両方防ぐ。
            """
            top = holder.get("top")
            if top is None:
                return
            try:
                if top.winfo_exists():
                    top.destroy()
            except tk.TclError:
                logger.debug("dialogue was already destroyed")

        root = self._guiRoot()
        if root is None:
            # GUI が無い（CUI 実行・テスト）ときは従来どおり直に作る
            build()
        else:
            # after() を呼ぶこと自体がワーカースレッドからの Tcl 呼び出しに
            # なる。CPython + 標準の Tcl では受け付けられるが、非スレッド
            # ビルドの Tcl や、GUI が既に破棄されている場合は例外になる。
            # ここで落ちると done が永久にセットされず、コマンドが止まらない
            # ばかりか Stop も効かなくなる。渡せなかったときは GUI を諦めて
            # その場で作る（従来の CUI 経路と同じ）ほうが、まだ止められる。
            try:
                root.after(0, build)
            except (RuntimeError, tk.TclError) as e:
                logger.warning(f"GUI スレッドへ渡せませんでした: {e}")
                root = None
                build()

        waited = 0.0
        while not done.wait(0.1):
            waited += 0.1
            if self._stop_event.is_set():
                # 停止要求。以前はここで抜けるだけだったため、ダイアログが
                # 画面に残ったままになっていた。_stop_event はワーカー側の
                # 待ちを解くだけで、GUI スレッドの wait_window には作用
                # しない。build() の finally も走らないので message_dialogue
                # も None に戻らない。UI は idle に戻るのにダイアログだけ
                # 残り、Window を閉じたあとで OK を押すと TclError になる。
                logger.warning("Stop requested while a dialogue is open")
                if root is not None:
                    # destroy は GUI スレッドへ依頼する。破棄されると
                    # wait_window が解け、build() が finally まで進んで
                    # done がセットされる。渡せなければ自分で閉じる。
                    # 停止の最中なので、ここで例外を上げても得が無い。
                    try:
                        root.after(0, closeOnGui)
                        done.wait(self._DIALOGUE_CLOSE_WAIT)
                    except (RuntimeError, tk.TclError) as e:
                        logger.warning(f"ダイアログの後始末を渡せませんでした: {e}")
                        closeOnGui()
                else:
                    closeOnGui()
                self.checkIfAlive()
                # checkIfAlive は StopThread を送出するため通常ここへは
                # 来ない。alive を落とさずに停止要求だけ来た場合の保険。
                return [] if need is list else {}
            if root is not None and waited >= self._DIALOGUE_OPEN_TIMEOUT:
                # mainloop が回っていないと after(0, build) は実行されず、
                # done が永久にセットされない。黙って待ち続けると停止も
                # 終了もできなくなるので、気づけるよう定期的に知らせる。
                logger.warning(
                    f"dialogue is not responding for {self._DIALOGUE_OPEN_TIMEOUT}s"
                    f" (title={title})"
                )
                waited = 0.0

        if "error" in box:
            raise RuntimeError(f"dialogue failed:\n{box['error']}")
        return box.get("value")

    def _guiRoot(self) -> Optional[Any]:
        """ダイアログを載せる tk のルートを返す。無ければ None。

        以前は gui（画像認識コマンドが受け取るプレビュー）しか見て
        いなかった。通常の PythonCommand は cmd_class() で作られ gui を
        持たないため、必ず None になり、ダイアログをワーカースレッドで
        直接生成していた。スレッド安全化が画像認識コマンドにしか効いて
        いなかったことになる。Window 側が生成時に渡す gui_root を先に
        見ることで、すべての種類のコマンドで GUI スレッドへ渡せる。
        """
        root = getattr(self, "gui_root", None)
        if root is not None and hasattr(root, "after"):
            return root

        gui = getattr(self, "gui", None)
        for obj in (gui, getattr(gui, "master", None)):
            if obj is not None and hasattr(obj, "after"):
                return obj
        return None


class PokeConDialogue(object):
    def __init__(
        self, parent: Any, title: str, message: int | str | list, mode: int = 0
    ) -> None:
        """
        pokecon用ダイアログ生成関数(注意:mode=0と1でmessageの取り扱いが大きく異なる。)
        mode | int: 0のときEntryのみ、1のとき6種類のwidgetに対応
        title | str: タイトル
        message | mode=0の場合 : int/str/list: Entryのラベル、mode=1の場合 : list[widget, widget, ...]: widgetごとの設定をリスト化したもの
        widget | list : widgetごとの設定(ウィジェットの種類によってリストの中身は異なる。以下を参照。)
        checkbox/entry/colorentryの場合 : [type, subtitle, init]
        combobox/radiobutton/spinboxの場合 : [type, subtitle, selectlist, init] (例) ["Combo", "Combo(例)", ["hello", "world"], "hello"]、["RADIO", "Radio(例)", ["dog", "cat"],"dog"]、["Spin", "Spin(例)", list(map(str, range(10))), "3"]
        scaleの場合 : [type, subtitle, min, max, init, digit] (例) ["Scale", "scale(例)", 0, 100, 50.1, 2]
        type | str: widgetの種類(check/combo/entry/colorentry/radio/spin/scaleのいずれか。大文字小文字は問わない)
        subtitle | str : widgetのタイトル
        init | checkboxの場合bool,scaleの場合int/float,その他str : 初期値
        selectlist | list : 項目のリスト
        min/max | int/float : scaleの最小値と最大値
        digit | int : 有効桁数
        return : なし
        """
        self._ls = None
        self.isOK = None

        self.message_dialogue = parent
        self.message_dialogue.title(title)
        self.message_dialogue.attributes("-topmost", True)
        self.message_dialogue.protocol("WM_DELETE_WINDOW", self.close_window)

        self.main_frame = tk.Frame(self.message_dialogue)
        self.inputs = ttk.Frame(self.main_frame)

        self.title_label = ttk.Label(self.main_frame, text=title, anchor="center")
        self.title_label.grid(
            column=0, columnspan=2, ipadx="10", ipady="10", row=0, sticky="nsew"
        )

        self.dialogue_ls = {}
        # winfo_width()/height() は最初の描画前だと 1 を返すため、
        # update_idletasks() でジオメトリを確定させてから読む。
        self.message_dialogue.update_idletasks()
        master = self.message_dialogue.master
        x = master.winfo_x()
        w = master.winfo_width()
        y = master.winfo_y()
        h = master.winfo_height()
        w_ = self.message_dialogue.winfo_width()
        h_ = self.message_dialogue.winfo_height()
        self.message_dialogue.geometry(
            f"+{int(x + w / 2 - w_ / 2)}+{int(y + h / 2 - h_ / 2)}"
        )

        if mode == 0:
            self.mode0(message)
        else:
            self.mode1(message)

        self.inputs.grid(
            column=0, columnspan=2, ipadx="10", ipady="10", row=1, sticky="nsew"
        )
        self.inputs.grid_anchor("center")
        self.result = ttk.Frame(self.main_frame)
        self.OK = ttk.Button(self.result, command=self.ok_command)
        self.OK.configure(text="OK")
        self.OK.grid(column=0, row=1)
        self.Cancel = ttk.Button(self.result, command=self.cancel_command)
        self.Cancel.configure(text="Cancel")
        self.Cancel.grid(column=1, row=1, sticky="ew")
        self.result.grid(column=0, columnspan=2, pady=5, row=2, sticky="ew")
        self.result.grid_anchor("center")
        self.main_frame.pack()
        self.message_dialogue.master.wait_window(self.message_dialogue)

    def mode0(self, message: list | str | int) -> None:
        if type(message) is not list:
            message = [message]
        n = len(message)

        for i in range(n):
            key = message[i]
            if key in self.dialogue_ls:
                # キーがラベル文字列そのものなので、同じラベルを2つ渡すと
                # 後勝ちで上書きされ widget 数と戻り値の数が食い違っていた。
                raise ValueError(f"Duplicated label in dialogue message: {key!r}")
            self.dialogue_ls[key] = tk.StringVar()
            label = ttk.Label(self.inputs, text=key)
            entry = ttk.Entry(self.inputs, textvariable=self.dialogue_ls[key])
            label.grid(column=0, row=i, sticky="nsew", padx=3, pady=3)
            entry.grid(column=1, row=i, sticky="nsew", padx=3, pady=3)

    def mode1(self, dialogue_list: list) -> None:
        n = len(dialogue_list)
        frame = []

        scale_label_list = []  # scaleの値を表示するlabelを格納するリスト
        scale_index_list = []  # scaleが何番目のwidgetなのかを格納するリスト
        scale_digit_list = []  # scaleの有効桁数を格納するリスト

        def change_scale_value(
            event: object = None,
        ):  # scaleのバーを動かしたときにlabelの値を変更するための関数
            for i, (index, fmt) in enumerate(zip(scale_index_list, scale_digit_list)):
                if fmt != 0:
                    val = round(self.dialogue_ls[dialogue_list[index][1]].get(), fmt)
                    scale_label_list[i]["text"] = "%s" % val
                    self.dialogue_ls[dialogue_list[index][1]].set(val)
                else:
                    scale_label_list[i]["text"] = (
                        "%s" % self.dialogue_ls[dialogue_list[index][1]].get()
                    )

        for i in range(n):
            # widgetはすべてframeの中に入れる。scaleの場合、値を示すlabelもフレームの中に入れる。
            frame.append(ttk.LabelFrame(self.inputs, text=dialogue_list[i][1]))

            # Checkbox
            if dialogue_list[i][0].casefold() == "check".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.BooleanVar(
                    value=dialogue_list[i][2]
                )
                widget = ttk.Checkbutton(
                    frame[i], variable=self.dialogue_ls[dialogue_list[i][1]]
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)
            # Combobox
            elif dialogue_list[i][0].casefold() == "combo".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][3]
                )
                widget = ttk.Combobox(
                    frame[i],
                    values=dialogue_list[i][2],
                    textvariable=self.dialogue_ls[dialogue_list[i][1]],
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)
                # widget.current(0)
            # Entry
            elif dialogue_list[i][0].casefold() == "entry".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][2]
                )
                widget = ttk.Entry(
                    frame[i], textvariable=self.dialogue_ls[dialogue_list[i][1]]
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)
            # Color entry（16進入力と、その色の見本）
            elif dialogue_list[i][0].casefold() == "colorentry".casefold():
                variable = tk.StringVar(value=dialogue_list[i][2])
                self.dialogue_ls[dialogue_list[i][1]] = variable
                row = tk.Frame(frame[i])
                sample = tk.Label(row, width=4, relief="sunken", borderwidth=1)
                widget = ttk.Entry(row, textvariable=variable)

                def update_color(
                    *_args: object,
                    value: tk.StringVar = variable,
                    swatch: tk.Label = sample,
                ) -> None:
                    text = value.get().strip().lower()
                    if text.startswith("#"):
                        text = text[1:]
                    elif text.startswith("0x"):
                        text = text[2:]
                    valid = len(text) == 6 and all(
                        ch in "0123456789abcdef" for ch in text
                    )
                    swatch.configure(background=f"#{text}" if valid else "#d9d9d9")

                variable.trace_add("write", update_color)
                update_color()
                sample.grid(column=0, row=0, sticky="nsew", padx=(3, 6), pady=3)
                widget.grid(column=1, row=0, sticky="nsew", padx=(0, 3), pady=3)
                row.grid(column=0, row=0, sticky="nsew")
                row.grid_columnconfigure(1, weight=1)
            # Radiobutton
            elif dialogue_list[i][0].casefold() == "radio".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][3]
                )
                for j, text0 in enumerate(dialogue_list[i][2]):
                    widget = ttk.Radiobutton(
                        frame[i],
                        text=text0,
                        variable=self.dialogue_ls[dialogue_list[i][1]],
                        value=text0,
                    )
                    widget.grid(column=j, row=0, sticky="nsew", padx=3, pady=3)
            # Scale
            elif dialogue_list[i][0].casefold() == "scale".casefold():
                # scale は [種別, キー, 最小, 最大, 初期値, 有効桁数] の6要素が必須。
                # 検証せずに [5] を参照していたため、短いリストで IndexError になっていた。
                if len(dialogue_list[i]) < 6:
                    raise ValueError(
                        f"'scale' requires 6 elements "
                        f"[type, key, from, to, value, digit], "
                        f"but got {len(dialogue_list[i])}: {dialogue_list[i]}"
                    )
                scale_index_list.append(i)
                scale_digit_list.append(dialogue_list[i][5])
                if dialogue_list[i][5] != 0:  # 浮動小数点数
                    self.dialogue_ls[dialogue_list[i][1]] = tk.DoubleVar(
                        value=dialogue_list[i][4]
                    )
                    scale_label_list.append(
                        tk.Label(
                            frame[i],
                            width=10,
                            text="%s"
                            % round(
                                self.dialogue_ls[dialogue_list[i][1]].get(),
                                dialogue_list[i][5],
                            ),
                        )
                    )
                else:  # 整数
                    self.dialogue_ls[dialogue_list[i][1]] = tk.IntVar(
                        value=dialogue_list[i][4]
                    )
                    scale_label_list.append(
                        tk.Label(
                            frame[i],
                            width=10,
                            text="%s" % self.dialogue_ls[dialogue_list[i][1]].get(),
                        )
                    )
                widget = ttk.Scale(
                    frame[i],
                    from_=dialogue_list[i][2],
                    to=dialogue_list[i][3],
                    variable=self.dialogue_ls[dialogue_list[i][1]],
                    command=change_scale_value,
                )
                scale_label_list[-1].grid(
                    column=0, row=0, sticky="nsew", padx=3, pady=3
                )
                widget.grid(column=1, row=0, sticky="nsew", padx=3, pady=3)
            # Spinbox
            elif dialogue_list[i][0].casefold() == "spin".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][3]
                )
                widget = ttk.Spinbox(
                    frame[i],
                    values=dialogue_list[i][2],
                    textvariable=self.dialogue_ls[dialogue_list[i][1]],
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)

            frame[i].grid(column=0, row=i, sticky="nsew", padx=3, pady=3)

        # widgetのサイズをフレームのサイズに合わせる
        for i in range(n):
            if dialogue_list[i][0].casefold() == "scale".casefold():
                frame[i].grid_columnconfigure(0, weight=1)
                frame[i].grid_columnconfigure(1, weight=3)
            else:
                frame[i].grid_columnconfigure(0, weight=1)

    def ret_value(self, need: type) -> list | dict | None:
        """入力結果を返す。Cancel / ウィンドウを閉じた場合は None。

        以前は Cancel 時に False を返していたため、呼び出し側が dict を前提に
        .get() すると AttributeError になっていた。None を返すことで
        `if result is None:` の素直な分岐で扱えるようにした。
        """
        if not self.isOK:
            return None

        if need is dict:
            return {k: v.get() for k, v in self.dialogue_ls.items()}
        if need is list:
            return self._ls

        # self._logger は存在しない（__init__ で作っていない）。ここが
        # AttributeError になると、本来は警告を出して続行する経路が
        # build() の except で拾われ、RuntimeError: dialogue failed へ
        # 化けていた。引数の指定ミスがコマンド全体の異常終了になる。
        logger.warning(f"Wrong arg: {need}. Returns list instead.")
        return self._ls

    def close_window(self) -> None:
        self.message_dialogue.destroy()
        self.isOK = False

    def ok_command(self) -> None:
        self._ls = [v.get() for k, v in self.dialogue_ls.items()]
        self.message_dialogue.destroy()
        self.isOK = True

    def cancel_command(self) -> None:
        self.message_dialogue.destroy()
        self.isOK = False
