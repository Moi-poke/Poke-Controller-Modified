#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Window.py - Poke-Controller Modified のメインウィンドウ.

PokeControllerApp : アプリ本体。UI 構築・カメラ・シリアル・コマンド実行を束ねる。

UI 構築は __init__ に全部書くと追えなくなるので _build_*_frame に分けている。

画面の状態に依存しない処理は、次のモジュールへ分離してある。

    CommandTags : コマンドのタグ（tags.json / クラス属性 / フォルダ名の合成）
    CommandStats: コマンドの使用履歴（実行回数 / 最終実行日時）
    LogPane     : ログ欄への描画とキュー（print のリダイレクトを含む）
    WindowUtils : COMポート列挙・識別子生成など、self を見ない小道具
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
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
import CommandTags
import CommandStats
import LogPane
import WindowUtils
import WindowGeometry
from CommandTags import TAG_ALL, TAG_UNCLASSIFIED
from Menubar import PokeController_Menubar


NAME = "Poke-Controller"
VERSION = "v3.4.0 Modified-AI"  # based on 1.0-beta3(custom by @dragonite303)


# タイトルに出すコマンド名の上限。長い名前でウィンドウ名が埋まるのを防ぐ
TITLE_COMMAND_MAX = 20


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
        sys.stdout = LogPane.QueueStdoutRedirector(self.logArea)
        self.logArea.after(LogPane.FLUSH_INTERVAL_MS, self.display_text)

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
        # 表示名 → クラスの対応表。UI 構築より前に空で用意する
        self.py_map: dict[str, type] = {}
        self.mcu_map: dict[str, type] = {}
        # 表示名 → タグ一覧。絞り込みと表示の前置に使う
        self.py_tags: dict[str, list[str]] = {}
        self.mcu_tags: dict[str, list[str]] = {}
        self.py_all_names: list[str] = []  # 絞り込み前の全件（検索の母集合）
        self.mcu_all_names: list[str] = []
        # Combobox ごとの「表示(タグ前置つき) → 素の名前」。引くのは素の名前
        self._shown_names: dict[Any, dict[str, str]] = {}
        # タグ編集の小窓。二重に開かないよう参照を持つ
        self._tag_editor: tk.Toplevel | None = None
        # 使用履歴（実行回数 / 最終実行日時）。書き出しは終了時に1回だけ
        self.command_stats: dict[str, dict] = CommandStats.load()
        # 履歴が実行によって変わったか。変わっていなければ終了時に書かない
        self._stats_dirty = False
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

        # 検索とタグの絞り込み。コマンドの Notebook の真上へ置く。
        # 探す→選ぶ が上から下へ並ぶので、視線が戻らない。
        # 絞り込みは名前引きの上で動くため、表示が変わっても起動する
        # コマンドは取り違えない。
        self.filter_f = ttk.Frame(self.Commands_f)
        self.search_name = tk.StringVar()
        self.search_entry = ttk.Entry(self.filter_f, textvariable=self.search_name)
        self.search_entry.pack(side="left", fill="x", expand=True, padx="2")
        # 打つそばから絞る。確定を待つと、目当てが出るまで見えない。
        self.search_entry.bind("<KeyRelease>", self.onCommandFilterChanged, add="")
        # Enter で入力を終えて検索欄から抜ける。フォーカスが残ったままだと
        # キーボード操作でコントローラを動かす打鍵が検索欄へ入ってしまう。
        self.search_entry.bind("<Return>", self._leaveSearchBox, add="")
        self.search_entry.bind("<KP_Enter>", self._leaveSearchBox, add="")
        # Esc は絞り込みを解除して抜ける。迷子になったときの戻り道。
        self.search_entry.bind("<Escape>", self.clearCommandFilter, add="")

        self.tag_name = tk.StringVar(value=TAG_ALL)
        self.tag_cb = ttk.Combobox(self.filter_f, width=12)
        self.tag_cb.config(state="readonly", textvariable=self.tag_name)
        self.tag_cb["values"] = [TAG_ALL]
        self.tag_cb.pack(side="left", padx="2")
        self.tag_cb.bind("<<ComboboxSelected>>", self.onCommandFilterChanged, add="")

        # タグの編集。選択中のコマンドに対して開く。タグは絞り込みの
        # すぐ隣にあるほうが「絞れない→付ける」の流れが切れない。
        self.tagEditButton = ttk.Button(self.filter_f)
        self.tagEditButton.config(text="タグ", width=5, command=self.openTagEditor)
        self.tagEditButton.pack(side="left", padx="2")

        # コマンドフォルダを開くボタン。Notebook を縦積みにしたので、
        # 横に並べる置き場所は絞り込みの段しかない。右端へ寄せる。
        self.OpenCommandDirButton = ttk.Button(self.filter_f)
        self.OpenCommandDirButton.config(
            image=self.open_folder_img, command=self.OpenCommandDir
        )
        self.OpenCommandDirButton.pack(side="left", padx="2")

        # Notebook より先に pack して、絞り込みを上の段に置く。
        self.filter_f.pack(fill="x", expand=False, padx="5", pady="2", side="top")
        self.Command_nb.pack(fill="both", expand=True, padx="5", pady="5", side="top")

        # タブを切り替えたら、そのタブ側の一覧へ絞り込みをかけ直す。
        self.Command_nb.bind(
            "<<NotebookTabChanged>>", self.onCommandFilterChanged, add=""
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
        self.logArea = WindowUtils.makeLogText(self.log_scroll)
        # weight を付けておくと、ウィンドウを広げた分が両方へ配分される
        self.log_pane.add(self.log_scroll, weight=3)

        self.sub_scroll = ScrollbarHelper(self.log_pane, scrolltype="both")
        self.subLogArea = WindowUtils.makeLogText(self.sub_scroll)
        self.log_pane.add(self.sub_scroll, weight=2)

        self.log_nb.add(self.log_pane, text="ログ")

        # 「入力」= シリアルへ送った操作のログ
        self.input_scroll = ScrollbarHelper(self.log_nb, scrolltype="both")
        self.inputLogArea = WindowUtils.makeLogText(self.input_scroll)
        self.log_nb.add(self.input_scroll, text="入力")

        self.log_nb.grid(
            column="3", padx="5", pady="5", row="0", rowspan="3", sticky="nsew"
        )

        self._build_log_toolbar()
        # 仕切り位置の復元は、ウィジェットの大きさが確定してからでないと
        # 効かない（構築直後は高さが1のため sashpos が無視される）。
        self.root.after_idle(self._restore_sash)

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
        """いま見えているタブのログを消す。"""
        LogPane.clearAreas(self._active_log_areas())

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

        実寸がまだ決まっていない間は False が返るので、次の空き時間に
        再挑戦する。after を呼ぶのは root を持つこちら側の仕事。
        """
        if not WindowGeometry.restoreSash(self.log_pane, self.settings):
            self.root.after(100, self._restore_sash)
            return
        # 動かされたら覚える。<ButtonRelease> はドラッグの確定時に来る
        self.log_pane.bind("<ButtonRelease-1>", self._remember_sash, add="+")

    def _remember_sash(self, *event: Any) -> None:
        """仕切りを動かしたら割合として控える。"""
        if WindowGeometry.rememberSash(self.log_pane, self.settings):
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

        WindowUtils.selectCombobox(self.fps_cb, self.fps.get())
        WindowUtils.selectCombobox(self.show_size_cb, self.show_size.get())
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
            self.camera_name_fromDLL.set(WindowUtils.cameraLabel(cam_id, name, key))

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
                devices.append((device.Name, WindowUtils.deviceKey(path)))

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
            WindowUtils.cameraLabel(i, name, self.camera_keys[i])
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
            message = "カメラの選択が不正です。現在のカメラを使い続けます"
            print(message)
            logger.warning(message)
            return
        self.camera_id.set(index)
        self.camera_key.set(self.camera_keys.get(index, ""))
        self._on_setting_changed()

    def saveCapture(self) -> None:
        """画面の1枚を保存し、結果をログ欄へ知らせる。

        押した本人に成否が伝わらないと「効いていない」と区別が付かない。
        loguru は stderr へ書くのでログ欄には出ない。ここは print を使う
        （sys.stdout が LogPane 経由でログ欄へ流れている）。
        """
        if self.camera is None or not self.camera.isOpened():
            print("キャプチャできません: カメラが開いていません")
            return
        if self.camera.saveCapture():
            saved_to = getattr(self.camera, "capture_dir", "Captures")
            print(f"キャプチャを保存しました: {saved_to}")
        else:
            print("キャプチャに失敗しました（詳細はターミナルのログ）")

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
        WindowUtils.openDirectory("Captures", self.os_name)

    def OpenCommandDir(self) -> None:
        if self.Command_nb.index("current") == 0:  # type: ignore
            directory = os.path.join("Commands", "PythonCommands")
        else:
            directory = os.path.join("Commands", "McuCommands")
        WindowUtils.openDirectory(directory, self.os_name)

    # ------------------------------------------------------------------
    # シリアル / キーボード
    # ------------------------------------------------------------------

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
        ports = WindowUtils.listComPorts()
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
        self.com_port.set(WindowUtils.portToNumber(device))
        self._update_title()

    def onComPortSelected(self, event: Any = None) -> None:
        self._apply_selected_port()

    def reloadSerialPort(self) -> None:
        """一覧を取り直してから接続し直す（Reload Port ボタン）。"""
        self.refreshComPorts()
        self.activateSerial()

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
            message = f"入力ログの設定を適用できませんでした: {e}"
            print(message)
            logger.warning(message)

    def _start_serial(self) -> None:
        # 入力ログは print と混ぜず、専用のキューへ流す。同じ経路だと
        # 入力ログが上限を食い尽くしてコマンドの出力が捨てられる。
        self.ser = Sender.Sender(
            self.is_show_serial, input_log_emit=LogPane.emitInputLog
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
        else:
            # 失敗しても何も出ないと、タイトルが(未接続)のままな理由が
            # 分からない。開けなかったことをその場で知らせる。
            message = f"COM Port {self.com_port_name.get()} を開けませんでした"
            print(message)
            logger.warning(message)
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
                message = "シリアル未接続のためキーボード操作を有効にできません"
                print(message)  # チェックが勝手に外れる理由を画面にも出す
                logger.warning(message)
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
        """対応表とタグを作り直し、絞り込みの選択肢を整える。

        表示位置(current())でクラスを引くと、並び替えや絞り込みを入れた
        瞬間に「選んだものと違うコマンドが起動する」ことになる。しかも
        画面には正しい名前が出たままなので気づけない。名前を鍵にする。
        """
        self.py_map = CommandTags.buildCommandMap(self.py_classes)
        self.mcu_map = CommandTags.buildCommandMap(self.mcu_classes)

        overrides = CommandTags.loadOverrides()
        self.py_tags = {
            name: CommandTags.collectTags(cls, name, overrides)
            for name, cls in self.py_map.items()
        }
        self.mcu_tags = {
            name: CommandTags.collectTags(cls, name, overrides)
            for name, cls in self.mcu_map.items()
        }

        self.py_all_names = list(self.py_map.keys())
        self.mcu_all_names = list(self.mcu_map.keys())

        self._refreshTagChoices()
        self.applyCommandFilter(keep_selection=False)

    def _refreshTagChoices(self) -> None:
        """絞り込みの選択肢を作り直す。並びは CommandTags 側で決める。"""
        choices = CommandTags.sortTagChoices([self.py_tags, self.mcu_tags])
        # 履歴由来の仮想タグ（最近使った / よく使う）を後ろへ足す。
        # 実体のタグではないので保存も編集もされず、コマンド側に何も
        # 書かなくても効く。該当が無いときは選択肢に出さない。
        choices = choices + CommandStats.choices(self.command_stats)
        self.tag_cb["values"] = choices
        if self.tag_name.get() not in choices:
            self.tag_name.set(TAG_ALL)

    def applyCommandFilter(self, keep_selection: bool = True) -> None:
        """検索語とタグで一覧を絞り込み、名前順に並べ替えて反映する。

        両方のタブを毎回そろえて更新する。片方だけ直すと、もう一方は
        古い一覧のまま残り、タブを切り替えた瞬間に表示と対応表が食い違う。
        絞り込んだ結果と表示位置は連動しないが、クラスは名前で引くので
        取り違えは起きない（それが名前引きへ変えた理由でもある）。
        """
        keyword = self.search_name.get().strip().lower()
        tag = self.tag_name.get() or TAG_ALL

        for combo, names, tag_table in (
            (self.py_cb, self.py_all_names, self.py_tags),
            (self.mcu_cb, self.mcu_all_names, self.mcu_tags),
        ):
            shown: list[tuple[str, str]] = []
            for name in names:
                tags = tag_table.get(name, [])
                # 絞り込みの判定にだけ仮想タグを混ぜる。表示の前置は
                # 実体のタグだけにして、履歴で見た目が変わらないようにする。
                matched = tags + CommandStats.virtualTags(self.command_stats, name)
                if tag != TAG_ALL and tag not in matched:
                    continue
                label = CommandTags.displayName(name, tags)
                # 使用履歴を名前の後ろへ添える。前置しないのは、並べ替えが
                # タグ順であることを崩さないため。検索は label 全体を見るので、
                # 日付や回数でも絞り込める（副次的だが実用になる）。
                used = CommandStats.summary(self.command_stats, name)
                if used:
                    label = f"{label}  — {used}"
                if keyword and keyword not in label.lower():
                    continue
                shown.append((label, name))

            shown.sort(key=lambda pair: pair[0])
            # 選択の維持は素の名前で行う。表示名には使用履歴（前回・回数）
            # を添えており、実行するたびに文字列が変わる。表示名で突き合わせ
            # ると、走らせた直後に選択が先頭へ飛んでしまう。
            before_name = self._selectedName(combo)
            self._shown_names[combo] = dict(shown)
            combo["values"] = [label for label, _ in shown]
            # 素の名前 → 新しい表示名。選び直しに使う
            relabel = {name: label for label, name in shown}

            # 走行中は選択を動かさない。いま走っているコマンドと画面の
            # 表示が食い違うと、Stop が何に対する操作なのか分からなくなる。
            if self._isCommandBusy():
                if before_name in relabel:
                    combo.set(relabel[before_name])
            elif keep_selection and before_name in relabel:
                combo.set(relabel[before_name])
            elif shown:
                combo.current(0)
            else:
                # 0件でも前の表示が残ると「選べているのに動かない」に
                # 見える。空にして、選べていないことを見た目に出す。
                combo.set("")

        self.assignCommand()

    def onCommandFilterChanged(self, *event: Any) -> None:
        """検索欄・タグ・タブの切り替えから呼ばれる。"""
        self.applyCommandFilter()

    def clearCommandFilter(self, *event: Any) -> None:
        """検索語とタグを既定へ戻し、検索欄から抜ける。

        Esc は停止(StopCommandWithEsc)にも割り当ててある。検索欄に
        いるあいだは絞り込みの解除として使い、"break" を返して停止側へ
        伝わらないようにする。打ち間違いで走行中のコマンドが止まると
        困るため、ここで確実に止める。
        """
        self.search_name.set("")
        self.tag_name.set(TAG_ALL)
        self.applyCommandFilter()
        self._leaveSearchBox()
        return "break"

    def _leaveSearchBox(self, *event: Any) -> Any:
        """検索欄からフォーカスを外す。

        入れっぱなしだと、キーボード操作でコントローラを動かすつもりの
        打鍵が検索欄へ入ってしまう。Enter で「入力を終える」操作として
        抜けられるようにした。
        """
        self.Command_nb.focus_set()
        return "break"

    def openTagEditor(self, *event: Any) -> None:
        """選択中のコマンドのタグを編集する小窓を開く。

        タグはコード(TAGS)・フォルダ名・JSON の3つから来るが、
        画面から変えられるのは JSON だけ。ここで保存すると、その
        コマンドは以後 JSON の内容が優先される（上書きになる）。
        それを画面にも明記しないと、コードを直したのに反映されない
        という分かりにくい状態になる。
        """
        combo = self.py_cb
        table = self.py_tags
        if self.Command_nb.index(self.Command_nb.select()) != 0:  # type: ignore
            combo = self.mcu_cb
            table = self.mcu_tags

        name = self._selectedName(combo)
        if not name:
            print("No command is selected.")
            logger.warning("No command is selected.")
            return

        # 開いている最中に本体を触られると、保存時に食い違う。
        if self._tag_editor is not None:
            self._tag_editor.lift()
            self._tag_editor.focus_force()
            return

        win = tk.Toplevel(self.root)
        win.title(f"タグの編集 - {name}")
        win.transient(self.root)
        win.resizable(True, False)
        self._tag_editor = win

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=name).pack(anchor="w")
        ttk.Label(
            frame,
            text="タグをカンマ区切りで入力します（空にすると指定を取り消します）",
        ).pack(anchor="w", pady=(4, 0))

        current_tags = [t for t in table.get(name, []) if t != TAG_UNCLASSIFIED]
        entry_var = tk.StringVar(value=", ".join(current_tags))
        entry = ttk.Entry(frame, textvariable=entry_var, width=48)
        entry.pack(fill="x", pady=6)
        entry.focus_set()

        # いま何が効いているかを出す。JSON で上書きされているのか、
        # コードやフォルダ由来なのかが分からないと直す場所を誤る。
        overrides = CommandTags.loadOverrides()
        if name in overrides:
            source = "現在: Commands/tags.json の指定が効いています"
        else:
            source = "現在: コードの TAGS とフォルダ名から決まっています"
        ttk.Label(frame, text=source).pack(anchor="w")

        # 既存のタグはチェックで付け外しできるようにする。手で打ち直すと
        # 表記ゆれ（半角/全角・送り仮名）で別のタグが増えていくため。
        # 入力欄は残す。ここにしか無い新しいタグを足す口が要る。
        known = sorted(
            {t for tags in table.values() for t in tags if t != TAG_UNCLASSIFIED}
        )
        checked = {t: tk.BooleanVar(value=t in current_tags) for t in known}
        # 入力欄とチェックは互いを更新するので、再入を止める必要がある。
        # リストで持つのは、入れ子の関数から書き換えるため（nonlocal 相当）。
        syncing = []

        def syncFromChecks() -> None:
            """チェックの状態を入力欄へ反映する。

            入力欄を正とし、チェックはその編集手段という位置づけにする。
            2つを別々に読むと、保存時にどちらが正か決められなくなる。
            チェックに無い自由入力のタグは、そのまま後ろへ残す。
            """
            if syncing:
                return
            syncing.append(True)
            picked = [t for t in known if checked[t].get()]
            extra = [
                t.strip()
                for t in entry_var.get().replace("、", ",").split(",")
                if t.strip() and t.strip() not in known
            ]
            entry_var.set(", ".join(picked + extra))
            syncing.clear()

        if known:
            ttk.Label(frame, text="既にあるタグ（クリックで付け外し）").pack(
                anchor="w", pady=(6, 0)
            )
            # 数が増えても縦に伸び続けないよう、折り返して並べる
            known_f = ttk.Frame(frame)
            known_f.pack(fill="x")
            for pos, name_tag in enumerate(known):
                ttk.Checkbutton(
                    known_f,
                    text=name_tag,
                    variable=checked[name_tag],
                    command=syncFromChecks,
                ).grid(row=pos // 4, column=pos % 4, sticky="w", padx=2)

        def syncToChecks(*_event: Any) -> None:
            """入力欄を直接編集したとき、チェックの側を追従させる。

            片方だけ更新すると、見えている状態と保存される内容が食い違う。
            対になっているものは同時に更新する。
            """
            if syncing:
                return
            syncing.append(True)
            now = {
                t.strip()
                for t in entry_var.get().replace("、", ",").split(",")
                if t.strip()
            }
            for t in known:
                if checked[t].get() != (t in now):
                    checked[t].set(t in now)
            syncing.clear()

        entry_var.trace_add("write", lambda *_a: syncToChecks())

        button_f = ttk.Frame(frame)
        button_f.pack(fill="x", pady=(10, 0))

        def close() -> None:
            self._tag_editor = None
            win.destroy()

        def save() -> None:
            tags = [t.strip() for t in entry_var.get().replace("、", ",").split(",")]
            tags = [t for t in tags if t]
            current = CommandTags.loadOverrides()
            if tags:
                current[name] = tags
            else:
                # 空で保存＝JSON の指定を消す。コードとフォルダ由来へ戻す。
                current.pop(name, None)
            if not CommandTags.saveOverrides(current):
                tkmsg.showerror(
                    "タグの保存", "書き込みに失敗しました。ログを確認してください。"
                )
                return
            close()
            # 収集からやり直す。ここで setCommandItems を通さないと、
            # 保存はできているのに一覧のタグが古いままになる。
            self.setCommandItems()

        ttk.Button(button_f, text="保存", command=save).pack(side="right", padx=2)
        ttk.Button(button_f, text="閉じる", command=close).pack(side="right", padx=2)

        win.bind("<Return>", lambda _e: save())
        win.bind("<Escape>", lambda _e: close())
        win.protocol("WM_DELETE_WINDOW", close)

    def _selectedName(self, combo: ttk.Combobox) -> str:
        """Combobox の表示（タグ前置つき）から、素のコマンド名へ戻す。

        表示と鍵を分けておかないと、タグを付け替えただけで別物として
        扱われる。引くのは常に素の名前にする。
        """
        label = combo.get()
        return self._shown_names.get(combo, {}).get(label, label)

    def _restoreSelection(
        self, combo: ttk.Combobox, mapping: dict[str, type], target: type | None
    ) -> None:
        """リロード前に選んでいたクラスと同じ名前のものを選び直す。"""
        if target is None:
            return
        wanted = CommandTags.commandName(target)
        for label, name in self._shown_names.get(combo, {}).items():
            if name == wanted and label in combo["values"]:
                combo.set(label)
                return

    def assignCommand(self) -> None:
        """選択中のコマンドを生成する。

        引くのは表示位置ではなく表示名。対応表に無ければ生成せず、
        Start を押せない状態にする（押せてしまうと、何も起きない
        理由が分からない）。

        実行中・停止処理中は何もしない。ここで作り直すと、いま走って
        いる実体と、停止を頼んだ相手が別物になる。Stop を押しても
        止まらないうえ、後始末(stopPlayPost)も呼ばれないため、
        ボタンが disabled のまま操作を受け付けなくなる。
        """
        if self._isCommandBusy():
            return

        if self.mcu_map:
            mcu_class = self.mcu_map.get(self._selectedName(self.mcu_cb))
            self.mcu_cur_command = None if mcu_class is None else mcu_class()

        if self.py_map:
            cmd_class = self.py_map.get(self._selectedName(self.py_cb))
            if cmd_class is None:
                self.py_cur_command = None
            elif issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
                # 旧: except TypeError で握っていたため、コマンド内部で起きた
                # TypeError まで「古い形式」と誤判定し引数1つで再生成していた。
                # シグネチャを見て渡せる引数の数を先に決める。
                if WindowUtils.acceptsGuiArg(cmd_class):
                    self.py_cur_command = cmd_class(self.camera, self.preview)
                else:
                    self.py_cur_command = cmd_class(self.camera)
            else:
                self.py_cur_command = cmd_class()

        if self.Command_nb.index(self.Command_nb.select()) == 0:  # type: ignore
            self.cur_command = self.py_cur_command
        else:
            self.cur_command = self.mcu_cur_command

        enabled = self.cur_command is not None
        self.startButton["state"] = "normal" if enabled else "disabled"

    def _isCommandBusy(self) -> bool:
        """コマンドが走っている、または停止処理の最中かを返す。

        Start 表示に戻るのは後始末(stopPlayPost)が終わったときなので、
        「Start と表示されている」ことを空いている合図として使う。
        """
        return self.startButton["text"] != "Start"

    def reloadCommands(self) -> None:
        """リロード後も同じコマンドが選ばれた状態に戻す。

        復元は表示位置ではなく名前で行う。絞り込みや並び替えが入ると
        位置は当てにならないうえ、タグを前置した表示名も変わりうる。
        """
        old_py = self.py_map.get(self._selectedName(self.py_cb))
        old_mcu = self.mcu_map.get(self._selectedName(self.mcu_cb))

        self.py_classes = self.py_loader.reload()
        self.mcu_classes = self.mcu_loader.reload()

        self.setCommandItems()
        self._restoreSelection(self.py_cb, self.py_map, old_py)
        self._restoreSelection(self.mcu_cb, self.mcu_map, old_mcu)
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
        # 前回の実行を先に読む。record より後だと今回の分で上書きされる。
        cmd_name = str(self.cur_command.NAME)
        before = CommandStats.summary(self.command_stats, cmd_name)
        if before:
            print(f"  ({before})")

        # 使用履歴を数える。ファイルへ書くのは終了時にまとめて1回だけで、
        # 短いコマンドを連続で回しても入出力が積み上がらないようにする。
        CommandStats.record(self.command_stats, cmd_name)
        self._stats_dirty = True
        # 記録は辞書へ即時入るので、選択肢もその場で作り直せる。
        # Reload を待つと「さっき使ったのに最近使ったに出ない」ことになる。
        self._refreshTagChoices()

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
        """コマンド終了後の後始末。

        呼び出し元は PythonCommandBase の _cleanup で、コマンドを走らせて
        いるワーカースレッドから直接呼ばれる。tkinter はスレッドセーフで
        ないので、ウィジェットを触る処理は after(0) で GUI スレッドへ渡す。
        """
        try:
            self.root.after(0, self._stopPlayPostOnGui)
        except tk.TclError:
            # 終了処理の最中に終わった場合。画面はもう無いので何もしない
            logger.debug("Window is already destroyed. skipped the post process")

    def _stopPlayPostOnGui(self) -> None:
        """後始末の本体。必ず GUI スレッドで動く。"""
        self.startButton["text"] = "Start"
        self.startButton["command"] = self.startPlay
        self.startButton["state"] = "normal"
        self.reloadCommandButton["state"] = "normal"
        self.pauseButton["text"] = "Pause"
        self.pauseButton["state"] = "disabled"
        self._paused = False
        self._running_command = ""
        self._update_title()
        # 走行中は選択を動かさないため applyCommandFilter を見送っている。
        # 空いたこの時点で一覧を見直し、「最近使った」「よく使う」の
        # 並びと絞り込みを実際の履歴に合わせる。
        self._refreshTagChoices()
        self.applyCommandFilter()

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
        follow = self.log_autoscroll.get()
        LogPane.flushQueue(LogPane.text_queue, self.logArea, follow)
        LogPane.flushQueue(LogPane.sub_log_queue, self.subLogArea, follow)

        if self.show_input_log.get():
            # 集約の取り残し（最後の1回）を吐き出させてから描画する
            if self.ser is not None:
                self.ser.flushInputLog()
            LogPane.flushQueue(LogPane.input_log_queue, self.inputLogArea, follow)

        self.logArea.after(LogPane.FLUSH_INTERVAL_MS, self.display_text)

    def run(self) -> None:
        logger.debug("Start Poke-Controller")
        self.mainwindow.mainloop()

    def _stopRunningCommand(self) -> None:
        """終了に先立ってコマンドを止める。

        止まりきるまでは待たない。長い wait や外部I/O の最中だと
        いつ抜けるか読めず、待つと画面が固まったように見える。
        スレッドは daemon なので、抜けきらなくてもプロセスは終わる。
        ここでの目的は、閉じたシリアルへ書きに行くのを減らすこと。
        """
        cmd = self.cur_command
        if cmd is None or not getattr(cmd, "alive", False):
            return
        try:
            cmd.end(self.ser)
        except Exception as e:
            logger.warning(f"停止要求で例外: {e}")

        # 後始末（キーを離す・postProcess）が走る余地を与える。待ちは
        # 短く区切る。ここで長く待つと終了操作そのものが固まって見える。
        thread = getattr(cmd, "thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
            if thread.is_alive():
                print("コマンドが停止しないまま終了します")
                logger.warning("Command did not stop in time. exiting anyway")

    def exit(self) -> None:
        if not tkmsg.askyesno("確認", "Poke Controllerを終了しますか？"):
            return

        # 走っているコマンドを先に止める。止めずに destroy すると、
        # ワーカースレッドが閉じたシリアルへ書きに行く。停止要求を
        # 出したあと、後始末が走る余地を少しだけ与える。
        self._stopRunningCommand()

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
        # 使用履歴も同じ場所で書き出す。実行のたびに書きに行かない代わり、
        # ここを通らないと記録が残らないので、設定の保存と並べておく。
        if self._stats_dirty:
            CommandStats.save(self.command_stats)

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
        """ウィンドウの位置とサイズを設定へ控える。"""
        WindowGeometry.rememberGeometry(self.root, self.settings)

    def _restore_geometry(self) -> None:
        """前回のウィンドウ位置とサイズを復元する。"""
        WindowGeometry.restoreGeometry(self.root, self.settings)

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
