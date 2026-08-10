#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Window.py - Poke-Controller Modified のメインウィンドウ.

PokeControllerApp      : アプリ本体。UI 構築・カメラ・シリアル・コマンド実行を束ねる。
QueueStdoutRedirector  : print() をキューへ流し、GUI 側が一定間隔でまとめて描画する。

UI 構築は __init__ に全部書くと追えなくなるので _build_*_frame に分けている。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import inspect
import platform
import re
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from tkinter import Tk
from typing import Any

import cv2
from loguru import logger
from pygubu.widgets.scrollbarhelper import ScrollbarHelper

import PokeConLogger
import Settings
import Utility as util
from Camera import Camera
from CommandLoader import CommandLoader
from Commands import McuCommandBase, PythonCommandBase, Sender
from Commands.Keys import KeyPress
from GuiAssets import CaptureArea, ControllerGUI
from Keyboard import SwitchKeyboardController
from Menubar import PokeController_Menubar


NAME = "Poke-Controller"
VERSION = "v3.1.3 Modified"  # based on 1.0-beta3(custom by @dragonite303)

# タイトルに出すコマンド名の上限。長い名前でウィンドウ名が埋まるのを防ぐ
TITLE_COMMAND_MAX = 20

# ログ描画の間隔。初回も再帰も同じ値を使う（別々にすると挙動が読めない）
# ログ描画の間隔。README!C61 の知見どおり、映像(33ms)と文字情報は周期を
# 分ける。16ms だとキューが空でも毎秒60回 after が回り、映像描画と競合する。
LOG_FLUSH_INTERVAL_MS = 200
LOG_FLUSH_MAX_LINES = 512  # 1回の描画で取り出す上限（周期を伸ばした分増やす）
LOG_MAX_LINES = 5000  # ログ欄に残す行数。Text は行数に比例して重くなる
LOG_QUEUE_MAX = 20000  # 未描画の行を溜める上限。超えた分は古い方から捨てる

# 相対パスだとカレントディレクトリ次第で読めなくなるため、
# このファイルの場所を基準に解決する（GuiAssets.py と同じ方針）。
OPEN_DIR_ICON_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "assets",
        "icons8-OpenDir-16.png",
    )
)

FPS_VALUES = [60, 45, 30, 15, 5]
BAUD_RATE_VALUES = [9600, 4800]
SHOW_SIZE_VALUES = ["640x360", "1280x720", "1920x1080"]
COM_PORT_NOT_FOUND = "(ポートが見つかりません)"


# print() の内容を受け渡すキュー。
# __main__ の中で定義するとモジュールとして import した時に NameError になるため
# モジュールのトップレベルに置く。
class _DropOldestQueue(queue.Queue):
    """満杯なら古い方を捨てて入れ替えるキュー。

    無制限キューだと、print を大量に出すコマンドで GUI の取り出し速度
    （LOG_FLUSH_MAX_LINES / LOG_FLUSH_INTERVAL_MS）を超えた分が際限なく
    溜まり、メモリを食ったままコマンド停止後も延々と流れ続ける。
    新しい行のほうが知りたい情報なので、捨てるのは古い方にする。
    捨てた件数は数えておき、描画時に「... N行省略 ...」として1行で出す。
    """

    def __init__(self, maxsize: int = LOG_QUEUE_MAX) -> None:
        super().__init__(maxsize=maxsize)
        self._dropped = 0
        self._drop_lock = threading.Lock()

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        """満杯なら最古の1件を捨ててから入れる。書き手は待たせない。"""
        while True:
            try:
                # put_nowait / get_nowait は内部で self.put / self.get を
                # 呼ぶ実装のため、put を上書きした本クラスでは無限再帰に
                # なる。基底の put / get を block=False で直に呼ぶ。
                super().put(item, block=False)
                return
            except queue.Full:
                try:
                    super().get(block=False)
                except queue.Empty:
                    continue
                with self._drop_lock:
                    self._dropped += 1

    def take_dropped(self) -> int:
        """前回の呼び出し以降に捨てた件数を返して 0 に戻す。"""
        with self._drop_lock:
            dropped, self._dropped = self._dropped, 0
        return dropped


# print() の内容を受け渡すキュー。
text_queue: queue.Queue = _DropOldestQueue()

# 入力ログ専用のキュー。print と混ぜないことで、コマンドの出力が
# 入力ログに押し流されるのを防ぐ。上限も別に持たせ、入力ログが
# 溢れてもコマンドの出力は失われないようにする。
input_log_queue: queue.Queue = _DropOldestQueue()

# 副ログ（ログタブの下側）用のキュー。print と混ぜないことで、
# 「残しておきたい結果」が通常の進捗表示に押し流されないようにする。
# 上側と同じ _DropOldestQueue なので、溢れても新しい行が残る。
sub_log_queue: queue.Queue = _DropOldestQueue()


class QueueStdoutRedirector:
    """print() をキューに積むだけの標準出力リダイレクタ.

    ウィジェットへの書き込みは GUI スレッド側（display_text）がまとめて行う。
    write のたびに描画すると print が多いコマンドで極端に重くなる。
    """

    def __init__(self, text_widget: Any = None) -> None:
        # text_widget は使わないが、旧コードとの互換のため引数だけ残す
        self.text_widget = text_widget
        self.buffer: queue.Queue = text_queue

    def write(self, string: str) -> None:
        self.buffer.put(string)

    def flush(self) -> None:
        pass


class PokeControllerApp:
    """メインウィンドウ."""

    def __init__(self, master: Tk | None = None, profile: str = "") -> None:
        """profile を渡すと設定・共有メモリを分けて並列起動できる。"""
        if master is None:
            master = Tk()
        self.root = master
        # どの台のウィンドウか一目で分かるようタイトルに出す。
        self.profile = Settings.GuiSettings.sanitize_profile(profile)
        self._running_command = ""  # タイトルに出す実行中コマンド名
        self._paused = False  # 一時停止中か（タイトル表示に使う）
        self.root.title(f"{NAME} {VERSION}")  # UI 構築後に詳細版へ更新する

        self._init_state()
        self._build_ui()

        # 標準出力をログエリアにリダイレクト
        sys.stdout = QueueStdoutRedirector(self.logArea)
        self.logArea.after(LOG_FLUSH_INTERVAL_MS, self.display_text)

        self.loadSettings()
        self._apply_settings_to_widgets()
        self._setup_camera_name()

        self._start_camera()
        self._start_serial()
        self._build_preview()
        # ポートが決まってからタイトルを組み立て直す
        self._update_title()

        self.loadCommands()
        self._bind_keys()

        self.mainwindow = self.frame_1
        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.preview.startCapture()

        self.menu = PokeController_Menubar(self)
        self.root.config(menu=self.menu)

    # ------------------------------------------------------------------
    # 状態
    # ------------------------------------------------------------------

    def _init_state(self) -> None:
        # Baud Rate を変更したい場合は "readonly" にする
        self.baud_rate_state = "disabled"
        self.os_name = platform.system()

        self.controller: ControllerGUI | None = None
        self.poke_treeview: Any = None
        self.keyPress: KeyPress | None = None
        self.keyboard: SwitchKeyboardController | None = None
        self.camera: Camera | None = None
        self.ser: Sender.Sender | None = None
        self.preview: CaptureArea | None = None
        self.cur_command: Any = None
        self.py_cur_command: Any = None
        self.mcu_cur_command: Any = None
        self.camera_dic: dict[int, str] | None = None
        # cam_id -> 表示名 / cam_id -> 識別子。同型ボードの区別に使う
        self.camera_keys: dict[int, str] = {}
        self._camera_labels: list[str] = []
        self.camera_key = tk.StringVar()
        # 設定を GUI へ流し込み終えたか。True になるまで自動保存はしない
        self._settings_ready = False

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.frame_1 = ttk.Frame(self.root)
        self._build_camera_frame()
        self._build_serial_frame()
        self._build_control_frame()
        self._build_command_frame()
        self._build_log_area()

        self.frame_1.config(height="720", padding="5", relief="flat", width="1280")
        self.frame_1.pack(expand="true", fill="both", side="top")
        self.frame_1.columnconfigure("3", weight="1")

    def _build_camera_frame(self) -> None:
        self.camera_lf = ttk.Labelframe(self.frame_1)

        self.camera_id_label = ttk.Label(self.camera_lf)
        self.camera_id_label.config(anchor="center", text="Camera ID:")
        self.camera_id_label.grid(padx="5", sticky="ew")

        self.camera_entry = ttk.Entry(self.camera_lf)
        self.camera_id = tk.IntVar()
        self.camera_entry.config(state="normal", textvariable=self.camera_id)
        self.camera_entry.grid(column="1", padx="5", row="0", sticky="ew")
        self.camera_entry.columnconfigure("1", uniform="0")

        self.reloadButton = ttk.Button(self.camera_lf)
        self.reloadButton.config(text="Reload Camera", command=self.openCamera)
        self.reloadButton.grid(column="2", padx="5", row="0", sticky="ew")

        self.separator_1 = ttk.Separator(self.camera_lf)
        self.separator_1.config(orient="vertical")
        self.separator_1.grid(column="3", row="0", sticky="ns")

        self.is_show_realtime = tk.BooleanVar()
        self.cb_show_realtime = ttk.Checkbutton(self.camera_lf)
        self.cb_show_realtime.config(
            text="Show Realtime",
            variable=self.is_show_realtime,
            command=self._on_setting_changed,
        )
        self.cb_show_realtime.grid(column="4", row="0")

        self.separator_2 = ttk.Separator(self.camera_lf)
        self.separator_2.config(orient="vertical")
        self.separator_2.grid(column="5", row="0", sticky="ns")

        # -- キャプチャ操作
        self.capture_f = ttk.Frame(self.camera_lf)
        self.captureButton = ttk.Button(self.capture_f)
        self.captureButton.config(text="Capture", command=self.saveCapture)
        self.captureButton.grid(column="0", row="0")

        self.open_folder_img = tk.PhotoImage(file=OPEN_DIR_ICON_PATH)
        self.OpencaptureButton = ttk.Button(self.capture_f)
        self.OpencaptureButton.config(
            image=self.open_folder_img, command=self.OpenCaptureDir
        )
        self.OpencaptureButton.grid(column="1", row="0")
        self.capture_f.grid(column="6", row="0", sticky="ns")

        # -- FPS / 表示サイズ
        self.camera_f2 = ttk.Frame(self.camera_lf)
        self.fps_label = ttk.Label(self.camera_f2)
        self.fps_label.config(text="FPS:")
        self.fps_label.grid(padx="5", sticky="ew")

        self.fps = tk.StringVar()
        self.fps_cb = ttk.Combobox(self.camera_f2)
        self.fps_cb.config(
            justify="right",
            state="readonly",
            textvariable=self.fps,
            values=FPS_VALUES,
            width="5",
        )
        self.fps_cb.grid(column="1", padx="10", row="0", sticky="ew")
        self.fps_cb.bind("<<ComboboxSelected>>", self.applyFps, add="")

        self.separator_3 = ttk.Separator(self.camera_f2)
        self.separator_3.config(orient="vertical")
        self.separator_3.grid(column="2", row="0", sticky="ns")

        self.show_size_label = ttk.Label(self.camera_f2)
        self.show_size_label.config(text="Show Size:")
        self.show_size_label.grid(column="3", padx="5", row="0", sticky="ew")

        self.show_size = tk.StringVar()
        self.show_size_cb = ttk.Combobox(self.camera_f2)
        self.show_size_cb.config(
            textvariable=self.show_size, state="readonly", values=SHOW_SIZE_VALUES
        )
        self.show_size_cb.grid(column="4", padx="10", row="0", sticky="ew")
        self.show_size_cb.bind("<<ComboboxSelected>>", self.applyWindowSize, add="")
        self.camera_f2.grid(column="0", columnspan="7", row="3", sticky="nsew")

        # -- カメラ名
        self.camera_name_l = ttk.Label(self.camera_lf)
        self.camera_name_l.config(anchor="center", text="Camera Name: ")
        self.camera_name_l.grid(column="0", padx="5", row="1", sticky="ew")

        self.camera_name_fromDLL = tk.StringVar()
        self.Camera_Name = ttk.Combobox(self.camera_lf)
        self.Camera_Name.config(state="readonly", textvariable=self.camera_name_fromDLL)
        self.Camera_Name.grid(
            column="1", columnspan="6", padx="5", row="1", sticky="ew"
        )
        self.Camera_Name.bind("<<ComboboxSelected>>", self.set_cameraid, add="")

        # CaptureArea が master 経由で参照するフラグ（GuiAssets 側の仕様に合わせる）
        self.camera_lf.is_use_left_stick_mouse = tk.BooleanVar()
        self.camera_lf.is_use_right_stick_mouse = tk.BooleanVar()

        self.camera_lf.config(height="200", text="Camera", width="200")
        self.camera_lf.grid(columnspan="3", padx="5", sticky="ew")

    def _build_serial_frame(self) -> None:
        self.serial_lf = ttk.Labelframe(self.frame_1)

        self.com_port_label = ttk.Label(self.serial_lf)
        self.com_port_label.config(text="COM Port: ")
        self.com_port_label.grid(padx="5", sticky="ew")

        self.com_port = tk.IntVar()
        self.com_port_name = tk.StringVar()
        # Combobox に出す表示名（'COM3: USB シリアル デバイス'）を持つ。
        # 実際に開くデバイス名は com_port_name、末尾の番号は com_port。
        # com_port_label は上の Label ウィジェットが使っているため別名にする。
        self.com_port_text = tk.StringVar()
        self._com_port_map: dict[str, str] = {}
        self.com_port_cb = ttk.Combobox(self.serial_lf)
        self.com_port_cb.config(
            state="readonly", textvariable=self.com_port_text, width="28"
        )
        self.com_port_cb.grid(column="1", padx="5", row="0", sticky="ew")
        self.com_port_cb.bind("<<ComboboxSelected>>", self.onComPortSelected, add="")

        self.baud_rate_label = ttk.Label(self.serial_lf)
        self.baud_rate_label.config(text="Baud Rate: ")
        self.baud_rate_label.grid(column="2", padx="5", row="0", sticky="ew")

        self.baud_rate = tk.StringVar()
        self.baud_rate_cb = ttk.Combobox(self.serial_lf)
        self.baud_rate_cb.config(
            justify="right",
            state=self.baud_rate_state,
            textvariable=self.baud_rate,
            values=BAUD_RATE_VALUES,
            width="6",
        )
        self.baud_rate_cb.grid(column="3", padx="5", row="0", sticky="ew")
        self.baud_rate_cb.bind("<<ComboboxSelected>>", self.applyBaudRate, add="")

        self.reloadComPort = ttk.Button(self.serial_lf)
        self.reloadComPort.config(text="Reload Port", command=self.reloadSerialPort)
        self.reloadComPort.grid(column="4", padx="5", row="0")

        self.disconnectComPort = ttk.Button(self.serial_lf)
        self.disconnectComPort.config(
            text="Disconnect Port", command=self.inactivateSerial
        )
        self.disconnectComPort.grid(column="5", padx="5", row="0")

        self.separator_4 = ttk.Separator(self.serial_lf)
        self.separator_4.config(orient="vertical")
        self.separator_4.grid(column="6", padx="5", row="0", sticky="ns")

        self.is_show_serial = tk.BooleanVar()
        self.cb_show_serial = ttk.Checkbutton(self.serial_lf)
        self.cb_show_serial.config(
            text="Show Serial",
            variable=self.is_show_serial,
            command=self._on_setting_changed,
        )
        self.cb_show_serial.grid(
            column="7", columnspan="2", padx="5", row="0", sticky="ew"
        )

        self.serial_lf.config(text="Serial Settings")
        self.serial_lf.grid(
            column="0", columnspan="2", padx="5", row="1", sticky="nsew"
        )

    def _build_control_frame(self) -> None:
        self.control_lf = ttk.Labelframe(self.frame_1)

        self.is_use_keyboard = tk.BooleanVar()
        self.cb_use_keyboard = ttk.Checkbutton(self.control_lf)
        self.cb_use_keyboard.config(
            text="Use Keyboard",
            variable=self.is_use_keyboard,
            command=self._on_keyboard_toggled,
        )
        self.cb_use_keyboard.grid(column="0", padx="10", pady="5", sticky="ew")

        self.cb_left_stick_mouse = ttk.Checkbutton(self.control_lf)
        self.cb_left_stick_mouse.config(
            text="Use LStick Mouse",
            variable=self.camera_lf.is_use_left_stick_mouse,
            command=self._on_left_stick_toggled,
        )
        self.cb_left_stick_mouse.grid(
            column="1", row="0", padx="10", pady="5", sticky="ew"
        )

        self.cb_right_stick_mouse = ttk.Checkbutton(self.control_lf)
        self.cb_right_stick_mouse.config(
            text="Use RStick Mouse",
            variable=self.camera_lf.is_use_right_stick_mouse,
            command=self._on_right_stick_toggled,
        )
        self.cb_right_stick_mouse.grid(
            column="1", row="1", padx="10", pady="5", sticky="ew"
        )

        self.simpleConButton = ttk.Button(self.control_lf)
        self.simpleConButton.config(
            text="Controller", command=self.createControllerWindow
        )
        self.simpleConButton.grid(column="0", padx="10", pady="5", row="1", sticky="ew")

        self.control_lf.config(height="200", text="Controller")
        self.control_lf.grid(
            column="0", padx="5", row="2", columnspan="2", sticky="nsew"
        )

    def _build_command_frame(self) -> None:
        self.command_lf = ttk.Labelframe(self.frame_1)
        self.Commands_f = ttk.Frame(self.command_lf)
        self.Commands_2_f = ttk.Frame(self.command_lf)

        self.Command_nb = ttk.Notebook(self.Commands_f)
        self.py_name = tk.StringVar()
        self.py_cb = ttk.Combobox(self.Command_nb)
        self.py_cb.config(state="readonly", textvariable=self.py_name)
        self.py_cb.pack(side="top")
        self.Command_nb.add(self.py_cb, padding="5", text="Python Command")

        self.mcu_name = tk.StringVar()
        self.mcu_cb = ttk.Combobox(self.Command_nb)
        self.mcu_cb.config(state="readonly", textvariable=self.mcu_name)
        self.mcu_cb.pack(side="top")
        self.Command_nb.add(self.mcu_cb, padding="5", text="Mcu Command")
        self.Command_nb.pack(fill="both", expand=True, padx="5", pady="5", side="left")

        self.OpenCommandDirButton = ttk.Button(self.Commands_f)
        self.OpenCommandDirButton.config(
            image=self.open_folder_img, command=self.OpenCommandDir
        )
        self.OpenCommandDirButton.pack(
            fill="y", expand=False, side="left", ipadx="5", pady="15"
        )

        self.reloadCommandButton = ttk.Button(self.Commands_2_f)
        self.reloadCommandButton.config(text="Reload", command=self.reloadCommands)
        self.reloadCommandButton.grid(
            column="0", padx="5", pady="5", row="1", sticky="ew"
        )

        self.startButton = ttk.Button(self.Commands_2_f)
        self.startButton.config(text="Start", command=self.startPlay)
        self.startButton.grid(column="1", padx="5", pady="5", row="1", sticky="ew")

        # 一時停止。Start/Stop の隣に置く。停止と紛らわしくならないよう
        # 実行中だけ押せる状態にする。
        self.pauseButton = ttk.Button(self.Commands_2_f)
        self.pauseButton.config(
            text="Pause", command=self.togglePause, state="disabled"
        )
        self.pauseButton.grid(column="2", padx="5", pady="5", row="1", sticky="ew")

        self.Commands_f.pack(
            fill="both", expand=True, padx="5", pady="5", anchor=tk.E, side="top"
        )
        self.Commands_2_f.pack(
            fill=None, expand=True, padx="5", pady="5", anchor=tk.E, side="top"
        )
        self.command_lf.config(height="200", text="Command")
        self.command_lf.grid(column="2", padx="5", row="1", rowspan="2", sticky="nsew")

    def _build_log_area(self) -> None:
        """ログ欄を組み立てる。

        入力ログは1操作で press と release の2行が出るため、コマンドの
        print やシステムメッセージと同じ欄に流すと、それらが押し流されて
        読めなくなる（テキストエリアの輻輳）。量の桁が違うものは欄を分ける。
        タブにしておけば場所を取らず、必要なときだけ見に行ける。

        さらに「ログ」タブの中身は上下2枚に分ける。1枚だと、常時流れる
        進捗と、残しておきたい結果が混ざって流れてしまう。上を主ログ
        （print と通常の出力）、下を副ログ（明示的に出した内容）とし、
        仕切りは ttk.PanedWindow でドラッグして高さを変えられるようにする。
        使い分けは Python コマンド側の print2 / log2 で行う。
        """
        self.log_nb = ttk.Notebook(self.frame_1)

        # 「ログ」= コマンドの出力とシステムメッセージ（上下2枚）
        self.log_pane = ttk.PanedWindow(self.log_nb, orient="vertical")

        self.log_scroll = ScrollbarHelper(self.log_pane, scrolltype="both")
        self.logArea = self._make_log_text(self.log_scroll)
        # weight を付けておくと、ウィンドウを広げた分が両方へ配分される
        self.log_pane.add(self.log_scroll, weight=3)

        self.sub_scroll = ScrollbarHelper(self.log_pane, scrolltype="both")
        self.subLogArea = self._make_log_text(self.sub_scroll)
        self.log_pane.add(self.sub_scroll, weight=2)

        self.log_nb.add(self.log_pane, text="ログ")

        # 「入力」= シリアルへ送った操作のログ
        self.input_scroll = ScrollbarHelper(self.log_nb, scrolltype="both")
        self.inputLogArea = self._make_log_text(self.input_scroll)
        self.log_nb.add(self.input_scroll, text="入力")

        self.log_nb.grid(
            column="3", padx="5", pady="5", row="0", rowspan="3", sticky="nsew"
        )

        self._build_log_toolbar()
        # 仕切り位置の復元は、ウィジェットの大きさが確定してからでないと
        # 効かない（構築直後は高さが1のため sashpos が無視される）。
        self.root.after_idle(self._restore_sash)

    def _make_log_text(self, holder: Any) -> tk.Text:
        """ログ表示用の Text を作る（2つの欄で同じ設定を使う）。"""
        # wrap を既定の "char" のままにすると、長い行が来るたびに折り返し
        # 計算でレイアウトを取り直す。横スクロールは holder が
        # scrolltype="both" なので既にある。
        text = tk.Text(holder.container, wrap="none")
        text.config(
            blockcursor="true", height="10", insertunfocussed="none", maxundo="0"
        )
        text.config(relief="flat", state="disabled", undo="false", width="50")
        text.pack(expand="true", fill="both", side="top")
        holder.add_child(text)
        holder.config(borderwidth="1", padding="1", relief="sunken")
        return text

    def _build_log_toolbar(self) -> None:
        """ログ欄の下に、表示の絞り込みと消去を置く。

        欄を分けても、コマンドが大量に print すれば「ログ」側は流れる。
        止めたいときにすぐ止められる口を用意しておく。
        """
        bar = ttk.Frame(self.frame_1)
        bar.grid(column="3", padx="5", row="3", sticky="ew")

        self.log_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="追従", variable=self.log_autoscroll).pack(
            side="left"
        )

        self.show_input_log = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bar,
            text="入力ログ",
            variable=self.show_input_log,
            command=self._on_input_log_toggled,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(bar, text="消去", command=self.clearLog).pack(side="right")

    def _on_input_log_toggled(self) -> None:
        """入力ログの ON/OFF。切ると Sender 側で出力そのものを止める。

        表示だけ止めても、行を作る処理とキューの往復は残る。元を止めた
        ほうが軽く、意図も分かりやすい。
        """
        enabled = bool(self.show_input_log.get())
        if self.ser is not None:
            self.ser.setInputLogEnabled(enabled)
        self.settings.input_log_enabled.set(enabled)
        self._on_setting_changed()

    def clearLog(self) -> None:
        """いま見えているタブのログを消す。

        「ログ」タブは上下2枚あるので両方を消す。片方だけ残ると、
        どちらを消したのか分からず紛らわしい。
        """
        for target in self._active_log_areas():
            target.configure(state="normal")
            target.delete("1.0", "end")
            target.configure(state="disabled")

    def _active_log_areas(self) -> list:
        """選択中のタブに対応する Text を返す（ログタブは2枚）。"""
        try:
            if self.log_nb.index("current") == 1:
                return [self.inputLogArea]
        except tk.TclError:
            pass
        return [self.logArea, self.subLogArea]

    def _active_log_area(self) -> tk.Text:
        """後方互換。主たる1枚を返す。"""
        return self._active_log_areas()[0]

    # -- 仕切り位置 ---------------------------------------------------------

    def _restore_sash(self) -> None:
        """ログ欄の仕切り位置を前回の値へ戻す。

        sashpos は「上端からの画素数」なので、ウィンドウの高さが変わると
        意味が変わってしまう。割合(0.0〜1.0)で持っておき、実際の高さを
        掛けて戻す。極端な値だと片方が潰れて操作できなくなるため、
        1割〜9割の範囲に収める。
        """
        try:
            ratio = float(self.settings.log_sash_ratio.get())
        except (TypeError, ValueError):
            ratio = 0.6
        ratio = min(0.9, max(0.1, ratio))
        try:
            height = self.log_pane.winfo_height()
            if height <= 1:
                # まだ実寸が決まっていない。次の空き時間に再挑戦する
                self.root.after(100, self._restore_sash)
                return
            self.log_pane.sashpos(0, int(height * ratio))
        except tk.TclError:
            logger.debug("Failed to restore the log sash position")
        # 動かされたら覚える。<ButtonRelease> はドラッグの確定時に来る
        self.log_pane.bind("<ButtonRelease-1>", self._remember_sash, add="+")

    def _remember_sash(self, *event: Any) -> None:
        """仕切りを動かしたら割合として控える。"""
        try:
            height = self.log_pane.winfo_height()
            if height <= 1:
                return
            ratio = self.log_pane.sashpos(0) / height
        except (tk.TclError, ZeroDivisionError):
            return
        ratio = min(0.9, max(0.1, ratio))
        if abs(ratio - float(self.settings.log_sash_ratio.get() or 0)) < 0.01:
            return  # 誤差程度の変化で毎回書きに行かない
        self.settings.log_sash_ratio.set(round(ratio, 3))
        self._on_setting_changed()

    # ------------------------------------------------------------------
    # 設定の反映
    # ------------------------------------------------------------------

    def loadSettings(self) -> None:
        self.settings = Settings.GuiSettings(self.profile)
        self.settings.load()

    def _apply_settings_to_widgets(self) -> None:
        """設定ファイルの値を各 tk 変数へ流し込む。"""
        self.is_show_realtime.set(self.settings.is_show_realtime.get())
        self.is_show_serial.set(self.settings.is_show_serial.get())
        self.is_use_keyboard.set(self.settings.is_use_keyboard.get())
        self.fps.set(str(self.settings.fps.get()))
        self.show_size.set(self.settings.show_size.get())
        self.com_port.set(self.settings.com_port.get())
        self.com_port_name.set(self.settings.com_port_name.get())
        self.baud_rate.set(str(self.settings.baud_rate.get()))
        self.camera_id.set(self.settings.camera_id.get())
        self.camera_key.set(self.settings.camera_key.get())
        self.camera_lf.is_use_left_stick_mouse.set(
            self.settings.is_use_left_stick_mouse.get()
        )
        self.camera_lf.is_use_right_stick_mouse.set(
            self.settings.is_use_right_stick_mouse.get()
        )

        # 入力ログの表示。settings.ini の [Input Log] enabled と対にする
        self.show_input_log.set(self.settings.input_log_enabled.get())

        self._select_combobox(self.fps_cb, self.fps.get())
        self._select_combobox(self.show_size_cb, self.show_size.get())
        self.show_size_tmp = self.show_size_cb["values"].index(self.show_size_cb.get())

        # 旧 settings.ini は com_port(番号) しか持たないことがある。
        # 名前が空なら番号から COM<n> を組み立てて選び直せるようにする。
        if not self.com_port_name.get() and self.com_port.get():
            self.com_port_name.set(f"COM{self.com_port.get()}")
        # 一覧を取り込み、前回のポートがあればそれを選ぶ
        self.refreshComPorts()

        # 前回のウィンドウ位置とサイズを戻す
        self._restore_geometry()

        # ここまでは「設定を GUI へ流し込む」段階なので保存してはいけない。
        # 以降の変更（＝利用者の操作）だけを保存対象にする。
        self._settings_ready = True

    @staticmethod
    def _select_combobox(combobox: ttk.Combobox, value: str) -> None:
        """設定値が候補に無くても落ちないようにする。"""
        values = list(combobox["values"])
        if value in values:
            combobox.current(values.index(value))
        else:
            logger.warning(f"'{value}' is not in {values}. fallback to the first item.")
            combobox.current(0)

    def _setup_camera_name(self) -> None:
        """OS ごとにカメラ名コンボボックスの扱いを切り替える。

        旧コードは if / elif が両方 != "Linux" で、elif が到達不能だった。
        """
        if self.os_name == "Linux":
            self.camera_name_fromDLL.set(
                "Linux environment. So that cannot show Camera name."
            )
            self.Camera_Name.config(state="disable")
            # 旧コードはここで __init__ ごと return していたためカメラもシリアルも
            # 初期化されず起動不能だった。カメラ ID を手入力すれば使えるので続行する。
            return

        if self.os_name not in ("Windows", "Darwin"):
            self.camera_name_fromDLL.set(
                "Unknown environment. Cannot show Camera name."
            )
            self.Camera_Name.config(state="disable")
            return

        try:
            self.locateCameraCmbbox()
            self.camera_entry.config(state="disable")
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            message = (
                "An error occurred when displaying the camera name in the Win/Mac "
                "environment."
            )
            self.camera_name_fromDLL.set(message)
            logger.warning(message)
            self.Camera_Name.config(state="disable")

    def _bind_keys(self) -> None:
        self.root.bind("<Key-F5>", self.ReloadCommandWithF5)
        self.root.bind("<Key-F6>", self.StartCommandWithF6)
        self.root.bind("<Key-Escape>", self.StopCommandWithEsc)
        self.root.bind("<Key-F7>", self.PauseCommandWithF7)
        logger.debug("Bind F5 / F6 / F7 / Escape keys")

    # ------------------------------------------------------------------
    # カメラ
    # ------------------------------------------------------------------

    def _current_fps(self) -> int:
        """StringVar なので必ず int に直してから使う。"""
        try:
            return int(self.fps.get())
        except (TypeError, ValueError):
            logger.warning(f"Invalid fps: {self.fps.get()}. fallback to 45.")
            return 45

    def _start_camera(self) -> None:
        self.camera = Camera(self._current_fps())
        self.openCamera()

    def _build_preview(self) -> None:
        width, height = map(int, self.show_size.get().split("x"))
        self.preview = CaptureArea(
            self.camera,
            self._current_fps(),
            self.is_show_realtime,
            self.ser,
            self.camera_lf,
            width,
            height,
            self.settings.is_take_stick_log.get(),
        )
        self.preview.config(cursor="crosshair")
        self.preview.grid(
            column="0", columnspan="7", row="2", padx="5", pady="5", sticky=tk.NSEW
        )

        # 復元したチェック状態を実際のマウス操作へ反映する。設定を読んだ
        # だけではバインドされないため、起動直後は「チェックが入っている
        # のにマウスで動かせない」状態になっていた。
        self.preview.ApplyLStickMouse()
        self.preview.ApplyRStickMouse()

    def openCamera(self) -> None:
        """カメラを開く。失敗しても落とさず、理由をログとカメラ名欄に出す。"""
        if self.camera is None:
            return
        if not self.camera.openCamera(self.camera_id.get()):
            message = f"Camera ID {self.camera_id.get()} cannot open."
            print(message)
            logger.error(message)

    def assignCamera(self, event: Any = None) -> None:
        """ID 直接入力に合わせて、表示中のカメラ名を追随させる。"""
        if self.camera_dic is None:
            return
        cam_id = self.camera_id.get()
        name = self.camera_dic.get(cam_id)
        if name is not None:
            key = self.camera_keys.get(cam_id, "")
            self.camera_name_fromDLL.set(self._camera_label(cam_id, name, key))

    def locateCameraCmbbox(self) -> None:
        """接続されているカメラを列挙してコンボボックスへ入れる。

        同型のキャプチャボードを複数挿すと Name が完全に一致するため、
        名前だけでは選んだ台が分からない。表示は必ず先頭に番号を付け、
        Windows では DevicePath から作った短い識別子も添える。
        """
        devices: list[tuple[str, str]] = []  # (名前, 識別子)

        if self.os_name == "Windows":
            import clr

            clr.AddReference(r"..\DirectShowLib\DirectShowLib-2005")
            from DirectShowLib import DsDevice, FilterCategory

            captureDevices = DsDevice.GetDevicesOfCat(FilterCategory.VideoInputDevice)
            for device in captureDevices:
                # DevicePath は USB のポートごとに異なる。差し直さない限り不変
                path = getattr(device, "DevicePath", "") or ""
                devices.append((device.Name, self._device_key(path)))

        elif self.os_name == "Darwin":
            cmd = (
                'system_profiler SPCameraDataType | grep "^    [^ ]" '
                '| sed "s/    //" | sed "s/://" '
            )
            res = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
            ret = res.stdout.decode("utf-8")
            # macOS からは固有の識別子が取れないので番号だけで区別する
            devices = [(line, "") for line in ret.split("\n") if line]

        else:
            return

        self.camera_dic = {i: name for i, (name, _) in enumerate(devices)}
        self.camera_keys = {i: key for i, (_, key) in enumerate(devices)}
        disable_id = len(devices)
        self.camera_dic[disable_id] = "Disable"
        self.camera_keys[disable_id] = ""

        # 表示名は必ず一意にする。番号が入るので同名でも取り違えない
        self._camera_labels = [
            self._camera_label(i, name, self.camera_keys[i])
            for i, name in self.camera_dic.items()
        ]
        self.Camera_Name["values"] = self._camera_labels
        logger.debug(f"Camera list: {self._camera_labels}")

        dev_num = len(devices)
        if dev_num == 0:
            print("No camera devices can be found.")
            logger.warning("No camera devices can be found.")

        # 同じ物理ボードを掴み直せるよう、まず識別子で照合する。
        # 認識順は起動のたびに入れ替わることがあるため番号だけでは足りない。
        saved_key = self.camera_key.get()
        matched = None
        if saved_key:
            for cam_id, key in self.camera_keys.items():
                if key and key == saved_key:
                    matched = cam_id
                    break
        if matched is None:
            matched = self.camera_id.get()
        if not 0 <= matched <= disable_id:
            print("Inappropriate camera ID! -> set to 0")
            logger.warning("Inappropriate camera ID! -> set to 0")
            matched = 0

        self.camera_id.set(matched)
        self.camera_key.set(self.camera_keys.get(matched, ""))
        self.camera_entry.bind("<KeyRelease>", self.assignCamera)
        self.Camera_Name.current(matched)

    @staticmethod
    def _device_key(device_path: str) -> str:
        """DevicePath から短い識別子を作る。

        DevicePath は100文字を超えることがあり、そのままでは一覧に出せない。
        USB のインスタンス ID を含めてハッシュし、先頭6桁だけを使う。
        """
        if not device_path:
            return ""
        digest = hashlib.md5(device_path.encode("utf-8", "ignore")).hexdigest()
        return digest[:6]

    @staticmethod
    def _camera_label(cam_id: int, name: str, key: str) -> str:
        """'0: ボード名 [a1b2c3]' の形にする。同名でも見分けられる。"""
        return f"{cam_id}: {name}" + (f" [{key}]" if key else "")

    def set_cameraid(self, event: Any = None) -> None:
        """コンボボックスの選択位置からカメラ ID を決める。

        旧実装は表示名の文字列一致で ID を逆引きしていたため、同型ボードを
        2枚挿すと名前が同じになり、どれを選んでも最初の1台に当たっていた。
        選択位置(index)は一意なのでそれをそのまま ID とする。
        """
        if self.camera_dic is None:
            return
        index = self.Camera_Name.current()
        if index < 0 or index not in self.camera_dic:
            logger.warning("Camera selection is invalid. keep the current ID.")
            return
        self.camera_id.set(index)
        self.camera_key.set(self.camera_keys.get(index, ""))
        self._on_setting_changed()

    def saveCapture(self) -> None:
        if self.camera is not None:
            self.camera.saveCapture()

    # ------------------------------------------------------------------
    # 各種設定の変更
    # ------------------------------------------------------------------

    def applyFps(self, event: Any = None) -> None:
        fps = self._current_fps()
        print(f"changed FPS to: {fps} [fps]")
        if self.preview is not None:
            self.preview.setFps(fps)
        if self.camera is not None:
            self.camera.setFps(fps)
        self._on_setting_changed()

    def applyBaudRate(self, event: Any = None) -> None:
        # 未実装（Baud Rate は activateSerial 側で反映される）
        pass

    def applyWindowSize(self, event: Any = None) -> None:
        """表示サイズを変更する。キャンセルされたら元に戻す。"""
        if self.preview is None:
            return
        current_index = self.show_size_cb["values"].index(self.show_size_cb.get())
        if self.show_size_tmp == current_index:
            return

        width, height = map(int, self.show_size.get().split("x"))
        self.preview.setShowsize(height, width)

        if tkmsg.askokcancel("確認", "この画面サイズに変更しますか？"):
            self.show_size_tmp = current_index
            self._on_setting_changed()
        else:
            self.show_size_cb.current(self.show_size_tmp)
            width_bef, height_bef = map(int, self.show_size.get().split("x"))
            self.preview.setShowsize(height_bef, width_bef)

    def OpenCaptureDir(self) -> None:
        self._open_directory("Captures")

    def OpenCommandDir(self) -> None:
        if self.Command_nb.index("current") == 0:  # type: ignore
            directory = os.path.join("Commands", "PythonCommands")
        else:
            directory = os.path.join("Commands", "McuCommands")
        self._open_directory(directory)

    def _open_directory(self, directory: str) -> None:
        """OS のファイラでフォルダを開く。"""
        logger.debug(f"Open folder: '{directory}'")
        if not os.path.isdir(directory):
            logger.warning(f"Directory not found: '{directory}'")
            return
        if self.os_name == "Windows":
            subprocess.call(["explorer", directory])
        elif self.os_name == "Darwin":
            subprocess.run(["open", directory])
        else:
            subprocess.run(["xdg-open", directory])

    # ------------------------------------------------------------------
    # シリアル / キーボード
    # ------------------------------------------------------------------

    def listComPorts(self) -> list[tuple[str, str]]:
        """接続されているシリアルポートを (デバイス名, 表示名) で返す。

        pyserial 同梱の list_ports を使う。カメラ名の列挙と違って
        OS ごとの分岐が要らず、Windows / macOS / Linux で同じに書ける。
        """
        try:
            from serial.tools import list_ports
        except ImportError:
            logger.error("pyserial の list_ports が見つかりません")
            return []

        ports = []
        for port in sorted(list_ports.comports(), key=lambda p: p.device):
            desc = (port.description or "").strip()
            # 説明が device と同じだと 'COM3: COM3' と重複するので省く
            if desc and desc != port.device:
                label = f"{port.device}: {desc}"
            else:
                label = port.device
            ports.append((port.device, label))
        return ports

    @staticmethod
    def _port_to_number(device: str) -> int:
        """'COM3' から 3 を取り出す。COM 形式でなければ 0。

        settings.ini の com_port(int) を保つためだけの変換。
        /dev/tty.usbserial-A5 のような名前から末尾の数字を取ると
        別のデバイスを指してしまうため、COM<数字> のときだけ変換する。
        """
        matched = re.fullmatch(r"COM(\d+)", (device or "").strip(), re.IGNORECASE)
        return int(matched.group(1)) if matched else 0

    def _update_title(self) -> None:
        """ウィンドウ名を今の状態に合わせて組み立て直す。

        並列起動すると同じ名前の窓が並ぶため、タスクバーで見分けられる
        必要がある。タスクバーは先頭から表示が切れるので、見分けに要る
        情報ほど前へ置く（[プロファイル] → ポート → 実行状態 → アプリ名）。
        """
        parts = []
        if self.profile:
            parts.append(f"[{self.profile}]")

        # UI 構築前に呼ばれても落ちないよう getattr で取る
        port_var = getattr(self, "com_port_name", None)
        device = port_var.get() if port_var is not None else ""
        opened = self.ser is not None and self.ser.isOpened()
        if device:
            parts.append(device if opened else f"{device}(未接続)")
        else:
            parts.append("未接続")

        if self._running_command:
            name = self._running_command
            if len(name) > TITLE_COMMAND_MAX:
                name = name[: TITLE_COMMAND_MAX - 1] + "…"
            mark = "⏸" if self._paused else "▶"
            parts.append(f"{mark}{name}")

        head = " ".join(parts)
        self.root.title(f"{head} - {NAME} {VERSION}")

    def refreshComPorts(self, keep_current: bool = True) -> None:
        """ポート一覧を取り直して Combobox へ入れる。"""
        before = self.com_port_name.get()
        ports = self.listComPorts()
        self._com_port_map = {label: device for device, label in ports}
        labels = [label for _, label in ports] or [COM_PORT_NOT_FOUND]
        self.com_port_cb["values"] = labels

        # 抜き差ししても選び直さずに済むよう、同じデバイスがあれば残す
        target = None
        if keep_current and before:
            for label, device in self._com_port_map.items():
                if device == before:
                    target = label
                    break
        self.com_port_text.set(target or labels[0])
        self._apply_selected_port()

    def _apply_selected_port(self) -> None:
        """選択中の表示名から、実際に開くデバイス名と番号を決める。"""
        device = self._com_port_map.get(self.com_port_text.get(), "")
        self.com_port_name.set(device)
        self.com_port.set(self._port_to_number(device))
        self._update_title()

    def onComPortSelected(self, event: Any = None) -> None:
        self._apply_selected_port()

    def reloadSerialPort(self) -> None:
        """一覧を取り直してから接続し直す（Reload Port ボタン）。"""
        self.refreshComPorts()
        self.activateSerial()

    @staticmethod
    def _emit_input_log(text: str) -> None:
        """入力ログ1行を専用キューへ積む。

        呼び出し元はコマンドのスレッドやシリアル送信の経路なので、ここで
        ウィジェットを触ってはいけない（tkinter はスレッドセーフでない）。
        キューへ入れるだけにして、描画は GUI スレッドの display_text が行う。
        """
        input_log_queue.put(text + "\n")

    def _apply_input_log_settings(self) -> None:
        """入力ログの設定を Sender へ反映する。

        書式は settings.ini の [Input Log] format で決める。プリセット名
        （simple / detail / compact / csv / command / raw）でもテンプレート
        文字列そのものでもよい。誤った書式でも起動は止めない（ログは
        補助機能なので、ここで落ちるほうが困る）。

        メニューの「入力ログの書式」から呼ばれると、開いている最中でも
        すぐ反映される。設定画面は値を書いてここを呼ぶだけでよい。
        """
        if self.ser is None:
            return
        try:
            # 記録する操作の絞り込み。空なら書式ごとの既定に任せる
            actions = self.settings.input_log_actions.get().strip()
            self.ser.setInputLogFormat(
                self.settings.input_log_format.get(), actions or None
            )
            self.ser.setInputLogEnabled(self.settings.input_log_enabled.get())
            self.ser.setInputLogStickChange(self.settings.input_log_stick_change.get())
        except Exception as e:
            logger.warning(f"入力ログの設定を適用できませんでした: {e}")

    def _start_serial(self) -> None:
        # 入力ログは print と混ぜず、専用のキューへ流す。同じ経路だと
        # 入力ログが上限を食い尽くしてコマンドの出力が捨てられる。
        self.ser = Sender.Sender(
            self.is_show_serial, input_log_emit=self._emit_input_log
        )
        self._apply_input_log_settings()
        self.activateSerial()
        self.activateKeyboard()

    def activateSerial(self) -> None:
        """ポートを開く。既に開いていれば閉じてから開き直す。"""
        if self.ser is None:
            return

        if self.baud_rate.get() == "4800":
            ret = tkmsg.askquestion(
                "確認",
                "Baud Rateを4800にすると動かなくなる可能性があります。\n変更しますか？",
            )
            if ret != "yes":
                self.baud_rate_cb.set(value=9600)
                return

        # 旧コードは自分自身を再帰呼び出ししていた。閉じてそのまま開けばよい。
        if self.ser.isOpened():
            print("Port is already opened and being closed.")
            self.ser.closeSerial()
            self.keyPress = None

        if self.ser.openSerial(
            self.com_port.get(), self.com_port_name.get(), int(self.baud_rate.get())
        ):
            message = f"COM Port {self.com_port_name.get()} connected successfully"
            print(message)
            logger.debug(message)
            self.keyPress = KeyPress(self.ser)
            # 一部だけ settings へ入れて save() すると、他の項目は起動時の
            # 古い値のまま書き戻される（Show Serial を切り替えてから接続
            # すると元へ戻る、という形で表面化していた）。常に全項目を
            # 集めてから書く _on_setting_changed() に一本化する。
            self._on_setting_changed()
        self._update_title()

    def inactivateSerial(self) -> None:
        if self.ser is not None and self.ser.isOpened():
            print("Port is closed.")
            self.ser.closeSerial()
            self.keyPress = None
            self._update_title()

    def activateKeyboard(self) -> None:
        is_windows = self.os_name == "Windows"

        if self.is_use_keyboard.get():
            # keyPress が None のまま生成すると、キーを押した瞬間に
            # AttributeError になる（Keyboard.py 側に None チェックが無い）
            if self.keyPress is None:
                logger.warning("シリアル未接続のためキーボード操作を有効化できません")
                self.is_use_keyboard.set(False)
                return

            if self.keyboard is None:
                self.keyboard = SwitchKeyboardController(self.keyPress)
                self.keyboard.listen()
            if not is_windows:
                return
            self.root.bind("<FocusIn>", self.onFocusInController)
            self.root.bind("<FocusOut>", self.onFocusOutController)
        else:
            # 旧コードは Windows 以外だと停止処理ごと素通りしていた
            if self.keyboard is not None:
                self.keyboard.stop()
                self.keyboard = None
            if not is_windows:
                return
            self.root.unbind("<FocusIn>")
            self.root.unbind("<FocusOut>")

    def onFocusInController(self, event: Any) -> None:
        if event.widget == self.root and self.keyboard is None:
            self.keyboard = SwitchKeyboardController(self.keyPress)
            self.keyboard.listen()

    def onFocusOutController(self, event: Any) -> None:
        if event.widget == self.root and self.keyboard is not None:
            self.keyboard.stop()
            self.keyboard = None

    def createControllerWindow(self) -> None:
        if self.controller is not None:
            self.controller.focus_force()
            return
        window = ControllerGUI(self.root, self.ser)
        window.protocol("WM_DELETE_WINDOW", self.closingController)
        self.controller = window

    def closingController(self) -> None:
        if self.controller is not None:
            self.controller.destroy()
            self.controller = None

    def _on_keyboard_toggled(self) -> None:
        """Use Keyboard の切り替え。有効化に失敗した場合も保存する。

        activateKeyboard は「シリアル未接続なら False へ戻す」ことがある。
        保存はその後に行い、画面に見えている状態と設定を一致させる。
        """
        self.activateKeyboard()
        self._on_setting_changed()

    def _on_left_stick_toggled(self) -> None:
        self.activate_Left_stick_mouse()
        self._on_setting_changed()

    def _on_right_stick_toggled(self) -> None:
        self.activate_Right_stick_mouse()
        self._on_setting_changed()

    def activate_Left_stick_mouse(self) -> None:
        if self.preview is not None:
            self.preview.ApplyLStickMouse()

    def activate_Right_stick_mouse(self) -> None:
        if self.preview is not None:
            self.preview.ApplyRStickMouse()

    # ------------------------------------------------------------------
    # コマンド
    # ------------------------------------------------------------------

    def loadCommands(self) -> None:
        self.py_loader = CommandLoader(
            util.ospath("Commands/PythonCommands"), PythonCommandBase.PythonCommand
        )
        self.mcu_loader = CommandLoader(
            util.ospath("Commands/McuCommands"), McuCommandBase.McuCommand
        )
        self.py_classes = self.py_loader.load()
        self.mcu_classes = self.mcu_loader.load()
        self.setCommandItems()
        self.assignCommand()

    def setCommandItems(self) -> None:
        self.py_cb["values"] = [c.NAME for c in self.py_classes]
        if self.py_classes:
            self.py_cb.current(0)
        self.mcu_cb["values"] = [c.NAME for c in self.mcu_classes]
        if self.mcu_classes:
            self.mcu_cb.current(0)

    def assignCommand(self) -> None:
        """選択中のコマンドを生成する。"""
        if self.mcu_classes:
            self.mcu_cur_command = self.mcu_classes[self.mcu_cb.current()]()

        if self.py_classes:
            cmd_class = self.py_classes[self.py_cb.current()]
            if issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
                # 旧: except TypeError で握っていたため、コマンド内部で起きた
                # TypeError まで「古い形式」と誤判定し引数1つで再生成していた。
                # シグネチャを見て渡せる引数の数を先に決める。
                if self._accepts_gui_arg(cmd_class):
                    self.py_cur_command = cmd_class(self.camera, self.preview)
                else:
                    self.py_cur_command = cmd_class(self.camera)
            else:
                self.py_cur_command = cmd_class()

        if self.Command_nb.index(self.Command_nb.select()) == 0:  # type: ignore
            self.cur_command = self.py_cur_command
        else:
            self.cur_command = self.mcu_cur_command

    @staticmethod
    def _accepts_gui_arg(cmd_class: type) -> bool:
        """__init__ が gui（認識位置表示用）を受け取れるかを判定する。"""
        try:
            params = inspect.signature(cmd_class.__init__).parameters
        except (TypeError, ValueError):
            return False

        # *args を持つなら何でも渡せる
        if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values()):
            return True

        # self / cam を除いて、あと1つ以上受け取れるか
        positional = [
            p
            for p in params.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        return len(positional) >= 3

        if self.Command_nb.index(self.Command_nb.select()) == 0:  # type: ignore
            self.cur_command = self.py_cur_command
        else:
            self.cur_command = self.mcu_cur_command

    def reloadCommands(self) -> None:
        """リロード後も同じコマンドが選ばれた状態に戻す。"""
        oldval_mcu = self.mcu_cb.get()
        oldval_py = self.py_cb.get()

        self.py_classes = self.py_loader.reload()
        self.mcu_classes = self.mcu_loader.reload()

        self.setCommandItems()
        if oldval_mcu in self.mcu_cb["values"]:
            self.mcu_cb.set(oldval_mcu)
        if oldval_py in self.py_cb["values"]:
            self.py_cb.set(oldval_py)
        self.assignCommand()
        print("Finished reloading command modules.")
        logger.info("Reloaded commands.")

    def startPlay(self, *event: Any) -> None:
        self.assignCommand()

        # 旧コードはメッセージを出すだけで先へ進み、None.NAME で落ちていた
        if self.cur_command is None:
            print("No commands have been assigned yet.")
            logger.warning("No commands have been assigned yet.")
            return

        message = f"{self.startButton['text']} {self.cur_command.NAME}"
        print(message)
        logger.info(message)
        self.cur_command.start(self.ser, self.stopPlayPost)

        self.startButton["text"] = "Stop"
        self.startButton["command"] = self.stopPlay
        self.reloadCommandButton["state"] = "disabled"
        self.pauseButton["state"] = "normal"
        self._running_command = str(getattr(self.cur_command, "NAME", ""))
        self._update_title()

    def stopPlay(self) -> None:
        if self.cur_command is None:
            return
        message = f"{self.startButton['text']} {self.cur_command.NAME}"
        print(message)
        logger.info(message)
        self.startButton["state"] = "disabled"
        self.cur_command.end(self.ser)

    def stopPlayPost(self) -> None:
        self.startButton["text"] = "Start"
        self.startButton["command"] = self.startPlay
        self.startButton["state"] = "normal"
        self.reloadCommandButton["state"] = "normal"
        self.pauseButton["text"] = "Pause"
        self.pauseButton["state"] = "disabled"
        self._paused = False
        self._running_command = ""
        self._update_title()

    def ReloadCommandWithF5(self, *event: Any) -> None:
        self.reloadCommands()

    def StartCommandWithF6(self, *event: Any) -> None:
        if self.startButton["text"] == "Stop":
            print("Command is now working!")
            logger.debug("Command is now working!")
        elif self.startButton["text"] == "Start":
            self.startPlay()

    def StopCommandWithEsc(self, *event: Any) -> None:
        if self.startButton["text"] == "Stop":
            self.stopPlay()

    def togglePause(self) -> None:
        """実行中のコマンドを一時停止／再開する。

        停止（Stop）はコマンドを終わらせるため、次に動かすときは最初
        からやり直しになる。長い手順の途中で少し手を離したいだけの
        ときに使えないので、状態を保ったまま足止めする口を分けて置く。
        """
        cmd = self.cur_command
        if cmd is None or not getattr(cmd, "alive", False):
            return
        if not hasattr(cmd, "togglePause"):
            # MCU コマンドなど、一時停止に対応しない種類
            print("This command does not support pause.")
            return
        paused = cmd.togglePause()
        self.pauseButton["text"] = "Resume" if paused else "Pause"
        self._paused = paused
        self._update_title()

    def PauseCommandWithF7(self, *event: Any) -> None:
        if self.startButton["text"] == "Stop":
            self.togglePause()

    # ------------------------------------------------------------------
    # ログ表示
    # ------------------------------------------------------------------

    def display_text(self) -> None:
        """キューに溜まった出力をまとめて描画する。

        「ログ」タブの上下2枚と「入力」の計3つの欄へ、それぞれの
        キューから流し込む。1回の after で全部を処理するので周期は1つ。
        """
        self._flush_queue(text_queue, self.logArea)
        self._flush_queue(sub_log_queue, self.subLogArea)

        if self.show_input_log.get():
            # 集約の取り残し（最後の1回）を吐き出させてから描画する
            if self.ser is not None:
                self.ser.flushInputLog()
            self._flush_queue(input_log_queue, self.inputLogArea)

        self.logArea.after(LOG_FLUSH_INTERVAL_MS, self.display_text)

    def _flush_queue(self, q: queue.Queue, area: tk.Text) -> None:
        """キューの内容を1つの Text へまとめて書き出す。

        update_idletasks() は呼ばない。次の after で待ちに入れば tkinter
        が自然に描くので不要で、映像描画の after と重なると描画が二重に
        走る。see("end") もスクロール計算が乗るため、末尾を見ているときだけ
        呼ぶ（過去ログを遡っている最中に勝手に飛ばされるのも防げる）。
        """
        lines = []
        while len(lines) < LOG_FLUSH_MAX_LINES:
            try:
                lines.append(q.get_nowait())
            except queue.Empty:
                break

        dropped = 0
        if isinstance(q, _DropOldestQueue):
            dropped = q.take_dropped()

        if not lines and not dropped:
            return

        # 末尾を見ているか、書き換える前に判定する
        at_bottom = area.yview()[1] >= 0.999

        area.configure(state="normal")
        if dropped:
            area.insert("end", f"... {dropped} 行省略 ...\n")
        if lines:
            area.insert("end", "".join(lines))
        self._trim(area)
        if at_bottom and self.log_autoscroll.get():
            area.see("end")
        area.configure(state="disabled")

    def _trim(self, area: tk.Text) -> None:
        """ログ欄の行数に上限を設ける。

        tk.Text は行数に比例して重くなる（README!C63）。上限が無いと
        長時間実行した終盤ほど insert のたびの再描画が遅くなる。
        超えた分は先頭から捨てる。state は呼び出し側で normal にしてある。
        """
        # "行.桁" 形式。末尾に空行が付くので実行数は -1
        total = int(area.index("end-1c").split(".")[0])
        if total > LOG_MAX_LINES:
            area.delete("1.0", f"end-{LOG_MAX_LINES}l")

    def run(self) -> None:
        logger.debug("Start Poke-Controller")
        self.mainwindow.mainloop()

    def exit(self) -> None:
        if not tkmsg.askyesno("確認", "Poke Controllerを終了しますか？"):
            return

        if self.ser is not None and self.ser.isOpened():
            self.ser.closeSerial()
            print("Serial disconnected")

        if self.keyboard is not None:
            self.keyboard.stop()
            self.keyboard = None

        self.closingController()
        # ウィンドウを壊す前に位置とサイズを控える（destroy 後は取れない）
        self._remember_geometry()
        self._save_settings()

        # 破棄前に描画ループを止める。順序を逆にすると解放済みメモリを読む
        if self.preview is not None:
            self.preview.stopCapture()
        if self.camera is not None:
            self.camera.destroy()
        cv2.destroyAllWindows()

        # 標準出力を元に戻さないと、破棄済みウィジェットへ書きに行くことがある
        sys.stdout = sys.__stdout__

        logger.debug("Stop Poke Controller")
        self.root.destroy()

    def _save_settings(self) -> None:
        """GUI の現在値をすべて設定ファイルへ書き出す。

        「一部の項目だけ set して save()」を作らないこと。save() は
        self.setting を丸ごと組み直すため、set し忘れた項目は起動時に
        読んだ古い値で書き戻される。保存の入口は必ずここに集約する。
        """
        self.settings.is_show_realtime.set(self.is_show_realtime.get())
        self.settings.is_show_serial.set(self.is_show_serial.get())
        self.settings.is_use_keyboard.set(self.is_use_keyboard.get())
        self.settings.is_use_left_stick_mouse.set(
            self.camera_lf.is_use_left_stick_mouse.get()
        )
        self.settings.is_use_right_stick_mouse.set(
            self.camera_lf.is_use_right_stick_mouse.get()
        )
        self.settings.fps.set(self._current_fps())
        self.settings.show_size.set(self.show_size.get())
        self.settings.com_port.set(self.com_port.get())
        self.settings.com_port_name.set(self.com_port_name.get())
        self.settings.baud_rate.set(int(self.baud_rate.get()))
        self.settings.camera_id.set(self.camera_id.get())
        self.settings.camera_key.set(self.camera_key.get())
        self.settings.input_log_enabled.set(self.show_input_log.get())
        self.settings.save()

    def _remember_geometry(self) -> None:
        """ウィンドウの位置とサイズを設定へ控える。

        最大化・最小化された状態の geometry を保存すると、次回そのまま
        復元されて使いにくい。通常状態(normal)のときだけ控える。
        """
        try:
            if self.root.state() != "normal":
                return
            self.settings.window_geometry.set(self.root.geometry())
        except tk.TclError:
            # 破棄済みなど。位置の記憶は本質ではないので黙って諦める
            logger.debug("Failed to read the window geometry")

    def _restore_geometry(self) -> None:
        """前回のウィンドウ位置とサイズを復元する。"""
        if not self.settings.restore_geometry.get():
            return
        geometry = self.settings.window_geometry.get()
        if not geometry:
            return
        # 画面構成が変わって完全に画面外になっていたら復元しない
        if not self._geometry_on_screen(geometry):
            logger.warning(f"Saved geometry is off-screen. ignored: {geometry}")
            return
        try:
            self.root.geometry(geometry)
        except tk.TclError:
            logger.warning(f"Invalid geometry in settings: {geometry}")

    def _geometry_on_screen(self, geometry: str) -> bool:
        """保存された位置が画面内に残っているかを判定する。

        モニタを外した後などに画面外の座標を復元すると、ウィンドウが
        どこにも見えず操作できなくなる。左上が画面内にあるかだけ見る
        （厳密な多画面判定は tkinter では取れないので、これで十分）。
        """
        m = re.search(r"([+-]\d+)([+-]\d+)$", geometry)
        if not m:
            return True  # サイズだけの指定なら位置は変わらない
        x, y = int(m.group(1)), int(m.group(2))
        margin = 100  # タイトルバーが掴める程度は画面内に残っていること
        return (
            -margin <= x <= self.root.winfo_screenwidth() - margin
            and -margin <= y <= self.root.winfo_screenheight() - margin
        )

    def _on_setting_changed(self, *event: Any) -> None:
        """GUI の設定が変わったら即座に書き出す。

        保存を終了時だけにすると、強制終了・クラッシュ・OS のシャット
        ダウンで設定が丸ごと失われる。チェックを入れた時点で書いておけば、
        どう終わっても次回に持ち越せる。書き込みは Settings 側が一時
        ファイル＋os.replace の原子的置換なので、途中で落ちても壊れない。
        """
        if not getattr(self, "_settings_ready", False):
            # UI 構築中・設定流し込み中は書かない（既定値で上書きしてしまう）
            return
        self._save_settings()


if __name__ == "__main__":
    # 実行階層に SerialController フォルダがあればそこへ移動する
    if "SerialController" in os.listdir():
        os.chdir("SerialController")

    PokeConLogger.root_logger()
    logger.info("The root logger is created.")

    parser = argparse.ArgumentParser(description=NAME)
    parser.add_argument(
        "--profile",
        default="",
        help="設定と共有メモリを分ける名前。複数台を並列起動するときに指定する"
        "（例: --profile switch1）。未指定なら従来どおり settings.ini を使う。",
    )
    args = parser.parse_args()

    app = PokeControllerApp(profile=args.profile)
    app.run()
