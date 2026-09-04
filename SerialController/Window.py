#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Window.py - Poke-Controller Modified のメインウィンドウ.

PokeControllerApp : アプリ本体。UI 構築・カメラ・シリアル・コマンド実行を束ねる。

UI 構築は __init__ に全部書くと追えなくなるので _build_*_frame に分けている。

画面の状態に依存しない処理は、次のモジュールへ分離してある。

    CommandTags : コマンドのタグ（tags.json / クラス属性 / フォルダ名の合成）
    CommandStats: コマンドの使用履歴（実行回数 / 最終実行日時）
    CommandPalette: Ctrl+K のコマンド検索パレット
    LogPane     : ログ欄への描画とキュー（print のリダイレクトを含む）
    WindowUtils : COMポート列挙・識別子生成など、self を見ない小道具
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import traceback
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
from Commands import McuCommandBase, PythonCommandBase, Sender, Transport
from Commands.Keys import KeyPress
from GuiAssets import CaptureArea, ControllerGUI
from Keyboard import SwitchKeyboardController
import CommandTags
import CommandStats
import CommandPalette
import TagEditor
import LogPane
import WindowUtils
import WindowGeometry
from CommandTags import TAG_ALL, TAG_UNCLASSIFIED
from Menubar import PokeController_Menubar


NAME = "Poke-Controller"
VERSION = "v3.5.2 Modified-AI"  # based on 1.0-beta3(custom by @dragonite303)


# タイトルに出すコマンド名の上限。長い名前でウィンドウ名が埋まるのを防ぐ
TITLE_COMMAND_MAX = 20


# すべてのパスをこの1点から解決する。起動する場所（カレント
# ディレクトリ）が変わっても同じ場所を指すようにするため。
# 相対パスのままだと、別ディレクトリから絶対パスで起動した場合や
# パッケージ化した場合に、あるはずのフォルダを見つけられない。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

OPEN_DIR_ICON_PATH = os.path.join(BASE_DIR, "assets", "icons8-OpenDir-16.png")

FPS_VALUES = [60, 45, 30, 15, 5]
BAUD_RATE_VALUES = [9600, 4800, 19200, 38400, 57600, 115200]
SHOW_SIZE_VALUES = ["640x360", "1280x720", "1920x1080"]
COM_PORT_NOT_FOUND = "(ポートが見つかりません)"

# 停止を頼んでから、戻って来ないかを見に行く間隔(ms)。
# 後始末が呼ばれない経路に入ったとき、操作だけは戻すために使う。
STOP_WATCH_MS = 500

# 停止しないことを知らせる間隔(ms)。500ms ごとの見張りで毎回出すと
# ログが埋まるため、知らせるのはこの間隔だけにする。
STOP_NOTIFY_MS = 5000


class PokeControllerApp:
    """メインウィンドウ."""

    def __init__(self, master: Tk | None = None, profile: str = "",
                 transport: str = "") -> None:
        """profile を渡すと設定・共有メモリを分けて並列起動できる。

        2026/08/25 段 VI-b: transport は通信方式のプリセット名。
          起動引数（--transport）から渡す。空なら settings.ini の
          [Transport] name を使う。引数のほうが強い（その場かぎりで
          試したいときに、設定を書き換えずに済ませるため）。
        """
        if master is None:
            master = Tk()
        self.root = master
        # 起動引数で指定された通信方式。設定より優先する
        self._transport_override = str(transport).strip()
        # どの台のウィンドウか一目で分かるようタイトルに出す。
        self.profile = Settings.GuiSettings.sanitize_profile(profile)
        self._running_command = ""  # タイトルに出す実行中コマンド名
        self._paused = False  # 一時停止中か（タイトル表示に使う）
        self.root.title(f"{NAME} {VERSION}")  # UI 構築後に詳細版へ更新する

        self._init_state()
        self._build_ui()

        # 標準出力をログエリアにリダイレクト
        sys.stdout = LogPane.QueueStdoutRedirector(self.logArea)
        self._display_after_id = self.logArea.after(
            LogPane.FLUSH_INTERVAL_MS, self.display_text
        )
        self.loadSettings()
        self._apply_settings_to_widgets()
        self._setup_camera_name()

        self._start_camera()
        self._start_serial()
        self._build_preview()
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
        # Baud Rate は画面から変えられないようにしている（意図的な制限）。
        self.baud_rate_state = "disabled"
        self.os_name = platform.system()

        self.controller: ControllerGUI | None = None
        self.poke_treeview: Any = None
        self.keyPress: KeyPress | None = None
        self.keyboard: SwitchKeyboardController | None = None
        self.camera: Camera | None = None
        self.ser: Sender.Sender | None = None
        # いま使っている通信方式の名前（画面表示と保存に使う）
        self.transport_name = tk.StringVar()
        # 2026/08/29 段4-c: 入力調停の設定（画面表示と保存に使う）。
        #   実体は Sender が持つ。ここは画面の選択値だけを持つ。
        self.arbitration_mode = tk.StringVar()
        self.preview: CaptureArea | None = None
        self.cur_command: Any = None
        # コマンドの実行状態。idle / running / stopping の3つ。
        # 画面の表示ではなくこれを唯一の情報源にする（表示は結果）。
        self._command_state = "idle"
        # 実行の世代。Start のたびに1つ進める。後始末が「どの実行に対する
        # ものか」を見分けるために使う（同じ番号のときだけ画面へ反映する）。
        self._run_token = 0
        # 停止を待っている秒数。0 なら待っていない。タイトルに出して
        # 「終了するしかない」状態が見て分かるようにする。
        self._stop_waited = 0
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
        # コマンド検索パレット（Ctrl+K）。二重に開かないよう参照を持つ
        self._palette: Any = None
        # 使用履歴（実行回数 / 最終実行日時）。書き出しは終了時に1回だけ
        self.command_stats: dict[str, dict] = CommandStats.load(self.profile)
        self._closing = False
        self._stats_dirty = False
        self.camera_dic: dict[int, str] | None = None
        # cam_id -> 表示名 / cam_id -> 識別子。同型ボードの区別に使う
        self.camera_keys: dict[int, str] = {}
        self._camera_labels: list[str] = []
        self.camera_key = tk.StringVar()
        self._display_after_id: Any = None
        self._sash_after_id: Any = None
        # 停止の見張り（_watchStopped）の予約。終了時に取り消せるよう
        self._watch_after_id: Any = None
        self._sash_restore_attempts = 0

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

        # 2026/08/25 段 VI-b: 通信方式（Transport）の選択。
        #   候補は Transport.py の登録簿から引く。ここに名前を
        #     書き並べない（実装を足したのに画面に出ない、を防ぐ）。
        self.transport_label = ttk.Label(self.serial_lf)
        self.transport_label.config(text="Transport: ")
        self.transport_label.grid(column="0", padx="5", row="1", sticky="ew")

        self.transport_cb = ttk.Combobox(self.serial_lf)
        self.transport_cb.config(
            state="readonly", textvariable=self.transport_name, width="28"
        )
        self.transport_cb.grid(column="1", padx="5", row="1", sticky="ew")
        self.transport_cb.bind(
            "<<ComboboxSelected>>", self.applyTransport, add=""
        )

        # 2026/08/29 段4-c: 入力調停の選択。候補は Sender の許可値から
        #   引く（画面に名前を書き並べない）。既定の off は本家と同じ
        #   挙動で、script を選ぶと実行中の手操作を断る。
        self.arbitration_label = ttk.Label(self.serial_lf)
        self.arbitration_label.config(text="入力調停: ")
        self.arbitration_label.grid(column="0", padx="5", row="2", sticky="ew")

        self.arbitration_cb = ttk.Combobox(self.serial_lf)
        self.arbitration_cb.config(
            state="readonly", textvariable=self.arbitration_mode, width="28"
        )
        self.arbitration_cb.grid(column="1", padx="5", row="2", sticky="ew")
        self.arbitration_cb.bind(
            "<<ComboboxSelected>>", self.applyArbitration, add=""
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
        self.Command_nb.pack(
            fill="both", expand=True, padx="5", pady="5", side="top"
        )

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
        self._sash_after_id = self.root.after_idle(self._restore_sash)

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

        # 段 VI-b: 通信方式の候補と現在値。利用者定義のプラグインは
        #   候補を組む前に読み込む（読み込み後でないと一覧に出ない）。
        self._loadTransportPlugins()
        self._refreshTransportChoices()
        self._refreshArbitrationChoices()

        WindowUtils.selectCombobox(self.fps_cb, self.fps.get())
        WindowUtils.selectCombobox(self.show_size_cb, self.show_size.get())
        self.show_size_tmp = self.show_size_cb["values"].index(self.show_size_cb.get())

        # Baud Rate は候補で縛らない。GameCube の自動化や独自マイコンで
        # 9600 / 4800 以外を使う人がいる。設定にある値が候補に無ければ
        # 候補そのものへ足して選ぶ。矯正すると設定ファイルへ書き戻る
        # 経路があるため、利用者の値が起動のたびに消える。
        current_rate = str(self.settings.baud_rate.get())
        rates = [str(v) for v in BAUD_RATE_VALUES]
        if current_rate not in rates:
            rates.append(current_rate)
            self.baud_rate_cb.config(values=rates)
        self.baud_rate.set(current_rate)

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
            # ただし手入力を適用する口が無く、打っても反映されなかった（WIN-14）。
            # Enter とフォーカス移動で適用する。打鍵ごとに適用すると、
            # 「12」と打ちたいのに「1」の時点で開きに行ってしまう。
            self._bindCameraEntry()
            return

        if self.os_name not in ("Windows", "Darwin"):
            self.camera_name_fromDLL.set(
                "Unknown environment. Cannot show Camera name."
            )
            self.Camera_Name.config(state="disable")
            self._bindCameraEntry()
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

    def _bindCameraEntry(self) -> None:
        """Camera ID を手入力で適用できるようにする。

        カメラ名の一覧を出せない環境（Linux / 未知の OS）では、ID を
        直接打つ以外に選ぶ手段が無い。Entry は state="normal" のままで
        打てるが、適用する契機がどこにも無かったため打っても何も
        起きなかった（WIN-14）。

        適用の契機は Enter とフォーカス移動にする。打鍵ごとに開きに
        行くと、「12」と打ちたいのに「1」の時点で別のカメラを掴む。
        """
        self.camera_entry.bind("<Return>", self._onCameraEntryApplied, add="+")
        self.camera_entry.bind("<FocusOut>", self._onCameraEntryApplied, add="+")

    def _onCameraEntryApplied(self, *event: Any) -> None:
        """入力された Camera ID を実際に開いて設定へ残す。

        空や数値以外なら何もしない。打ちかけでフォーカスが外れた
        だけの場合に、0 番のカメラを開きに行かないようにするため。
        """
        cam_id = self._cameraIdOrNone()
        if cam_id is None:
            return
        if self.openCamera():
            self._on_setting_changed()

    def _bind_keys(self) -> None:
        self.root.bind("<Key-F5>", self.ReloadCommandWithF5)
        self.root.bind("<Key-F6>", self.StartCommandWithF6)
        self.root.bind("<Key-Escape>", self.StopCommandWithEsc)
        self.root.bind("<Key-F7>", self.PauseCommandWithF7)
        self.root.bind("<Control-k>", self.openCommandPalette)
        self.root.bind("<Control-K>", self.openCommandPalette)
        logger.debug("Bind F5 / F6 / F7 / Escape / Ctrl+K keys")

    # ------------------------------------------------------------------
    # カメラ
    # ------------------------------------------------------------------

    def _current_fps(self) -> int:
        try:
            fps = int(self.fps.get())
        except (TypeError, ValueError):
            fps = 45
        if fps not in FPS_VALUES:
            fps = 45
        self.fps.set(str(fps))
        return fps

    def _currentBaudRate(self) -> int:
        """Baud Rate を通常の int で返す。数値として読めないときだけ既定。

        BAUD_RATE_VALUES は Combobox に出す「よく使う値」であって、
        使ってよい値の一覧ではない。GameCube の自動化では別の速度を
        使うし、独自のマイコンを載せている人はもっと速い値を入れる。
        候補に無いことを理由に書き換えてはいけない。設定ファイルへ
        書き戻す経路があるので、矯正すると利用者の設定が壊れて
        元の値も分からなくなる。

        ここで守るのは「数値として読めること」だけ。読めない値を
        そのまま流すと接続の途中で例外になり、開かない理由も出ない。
        """
        try:
            return int(self.baud_rate.get())
        except (TypeError, ValueError, tk.TclError):
            fallback = BAUD_RATE_VALUES[0]
            logger.warning(
                f"Baud Rate を数値として読めないため {fallback} を使います"
            )
            return fallback

    def _cameraIdOrNone(self) -> int | None:
        """Camera ID を通常の int で返す。空や不正なら None を返す。

        IntVar は中身が空文字や非数値のとき get() が TclError を投げる。
        Entry を空にした状態で設定を変えると、そこから先の処理が
        まとめて落ちる。呼ぶ側が毎回 try で囲むのではなく、取り出す
        場所を1つにしてそこで守る。
        """
        try:
            return int(self.camera_id.get())
        except (tk.TclError, ValueError):
            return None

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

    def openCamera(self) -> bool:
        """選択中のカメラへ切り替え、成功時だけ True を返す。"""
        if self.camera is None:
            return False
        try:
            cam_id = self.camera_id.get()
        except (tk.TclError, ValueError):
            return False
        if self.camera_dic is not None and self.camera_dic.get(cam_id) == "Disable":
            self.camera.destroy()
            print("カメラを無効にしました")
            logger.info("Camera is disabled")
            return True
        if self.camera.openCamera(cam_id):
            return True
        message = f"Camera ID {cam_id} cannot open."
        print(message)
        logger.error(message)
        return False


    def assignCamera(self, event: Any = None) -> None:
        """入力途中のIDで表示名だけ追随させる。切替と保存は行わない。"""
        try:
            cam_id = int(self.camera_entry.get().strip())
        except (TypeError, ValueError, tk.TclError):
            return
        if self.camera_dic is not None and cam_id in self.camera_dic:
            self.Camera_Name.current(cam_id)

    def applyCameraId(self, event: Any = None) -> str:
        """Return/FocusOutでIDを検証し、切替成功時だけ保存する。"""
        previous = self.settings.camera_id.get()
        try:
            cam_id = int(self.camera_entry.get().strip())
        except (TypeError, ValueError, tk.TclError):
            print("Camera IDが不正です。元の値へ戻します")
            self.camera_id.set(previous)
            self.assignCamera()
            return "break"
        if cam_id < 0 or (self.camera_dic is not None and cam_id not in self.camera_dic):
            print("Camera IDが範囲外です。元の値へ戻します")
            self.camera_id.set(previous)
            self.assignCamera()
            return "break"
        self.camera_id.set(cam_id)
        self.camera_key.set(self.camera_keys.get(cam_id, ""))
        self.assignCamera()
        if self.openCamera():
            self._on_setting_changed()
            return "break"
        self.camera_id.set(previous)
        self.camera_key.set(self.camera_keys.get(previous, ""))
        self.assignCamera(); self.openCamera(); return "break"
    def locateCameraCmbbox(self) -> None:
        """接続されているカメラを列挙してコンボボックスへ入れる。

        同型のキャプチャボードを複数挿すと Name が完全に一致するため、
        名前だけでは選んだ台が分からない。表示は必ず先頭に番号を付け、
        Windows では DevicePath から作った短い識別子も添える。
        """
        devices: list[tuple[str, str]] = []  # (名前, 識別子)

        if self.os_name == "Windows":
            import clr

            # カレントディレクトリ基準の相対指定だと、起動場所が違うだけで
            # 見つけられない。このファイルの場所から解決する。
            dll_path = os.path.normpath(
                os.path.join(BASE_DIR, "..", "DirectShowLib", "DirectShowLib-2005")
            )
            clr.AddReference(dll_path)
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
        self.camera_entry.bind("<Return>", self.applyCameraId)
        self.camera_entry.bind("<FocusOut>", self.applyCameraId)
        self.Camera_Name.current(matched)

    def set_cameraid(self, event: Any = None) -> None:
        """カメラ名の選択を検証し、成功した場合だけ設定へ保存する。"""
        if self.camera_dic is None:
            return
        index = self.Camera_Name.current()
        if index < 0 or index not in self.camera_dic:
            message = "カメラの選択が不正です。現在のカメラを使い続けます"
            print(message)
            logger.warning(message)
            return
        previous = self.settings.camera_id.get()
        self.camera_id.set(index)
        self.camera_key.set(self.camera_keys.get(index, ""))
        if self.openCamera():
            self._on_setting_changed()
            return
        self.camera_id.set(previous)
        self.camera_key.set(self.camera_keys.get(previous, ""))
        self.assignCamera()
        self.openCamera()




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
        """Baud Rate の選択は受け取らない（意図的に何もしない）。

        画面の Combobox は state="disabled" にしてあり、そもそも選び
        直せない。ここが空なのは実装漏れではなく、その状態に合わせて
        いる。

        Switch の自動化では 9600 から変える理由が無い。一方 GameCube
        の自動化では別の速度を使うが、その利用者は Switch 側の数百分の
        一で、かつ自分でスクリプトを直せる人に限られる。画面から触れる
        ようにすると、多数派である Switch の利用者が誤って変更し、
        「繋がらない」という問い合わせだけが増える。

        速度を変える必要がある場合は settings.ini の baud_rate を直接
        書き換える。Settings は候補で縛らず value > 0 だけを検査するの
        で、任意の値がそのまま通る。マイコン側の SERIAL_BAUD と同じ値
        にすること。片方だけ変えると文字が化けて一切通信できない。
        """
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
        WindowUtils.openDirectory(os.path.join(BASE_DIR, "Captures"), self.os_name)

    def OpenCommandDir(self) -> None:
        if self.Command_nb.index("current") == 0:  # type: ignore
            directory = os.path.join(BASE_DIR, "Commands", "PythonCommands")
        else:
            directory = os.path.join(BASE_DIR, "Commands", "McuCommands")
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

        # 停止を待っている間はそれを出す。ボタンが disabled のままな理由が
        # 画面から分かるようにするため（待つ以外の手は終了しかない）。
        waited = getattr(self, "_stop_waited", 0)
        if waited:
            parts.append(f"⏳停止待ち {waited // 1000}秒")

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
        """ポートを選んだ時点で設定へ書く。

        以前は接続に成功したときだけ保存していた。選んだが繋がなかった
        場合・接続に失敗した場合・選んだ直後に落ちた場合は残らず、
        他の設定が即時保存なのにここだけ挙動が違っていた。
        """
        self._apply_selected_port()
        self._on_setting_changed()

    def reloadSerialPort(self) -> None:
        """一覧を取り直してから接続し直す（Reload Port ボタン）。

        Keyboard の停止と再生成は activateSerial 側へ集約したので、
        ここでは行わない。二重に止めても害は無いが、同じ手順が2か所に
        あると片方だけ直す事故が起きる。
        """
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

    # ------------------------------------------------------------------
    # 通信方式（Transport）のプリセット  2026/08/25 段 VI-b
    # ------------------------------------------------------------------
    # 段 VI で「差し替えられる」形は作ったが、選ぶ手段が無かった。
    #   ここで設定・起動引数・画面の3つから名前で選べるようにする。
    #   本体は実装を知らない。名前を登録簿へ渡すだけ（PORTBACK 2章）。

    def _selectedTransportName(self) -> str:
        """これから使う通信方式の名前を決める。

        優先順は 起動引数 > 設定ファイル。引数を上に置くのは、
          設定を書き換えずにその場で試せるようにするため。
        """
        name = self._transport_override or self.settings.transport_name.get()
        return Transport.resolve_transport_name(name)

    def _loadTransportPlugins(self) -> None:
        """利用者が置いた自作の Transport を読み込む。

        フォルダ指定が空なら何もしない（既定）。読めたものは名前を
          出す。黙って足すと、候補が増えた理由が分からなくなる。
        """
        directory = self.settings.transport_plugin_dir.get().strip()
        if not directory:
            return
        if not os.path.isabs(directory):
            directory = os.path.join(BASE_DIR, directory)
        added = Transport.load_transport_plugins(directory)
        if added:
            message = "通信方式を読み込みました: " + ", ".join(added)
            print(message)
            logger.info(message)

    def _refreshTransportChoices(self) -> None:
        """選択欄の候補を登録簿から組み直し、現在値を選ぶ。"""
        names = Transport.list_transports()
        self.transport_cb.config(values=names)
        current = self._selectedTransportName()
        self.transport_name.set(current)

    def applyTransport(self, event: Any = None) -> None:
        """選択された通信方式へ差し替える。

        開いている線は Sender.setTransport が閉じる。差し替えたら
          開き直して、選んだ方式で実際に繋がる状態にする。
        戻り値は「入力ログが繋がったか」。繋がらない方式もあるので
          ここで理由を出す（黙ると1行も出ない理由が分からない）。
        """
        name = Transport.resolve_transport_name(self.transport_name.get())
        # 解決後の名前を画面へ戻す。知らない名前を選んだまま残さない
        self.transport_name.set(name)
        # 画面から選び直した以上、起動引数の指定はもう効かせない
        self._transport_override = ""
        if self.ser is None:
            self._on_setting_changed()
            return
        if name == self.ser.getTransportName():
            return
        transport = Transport.create_transport(name, logger=logger)
        linked = self.ser.setTransport(transport)
        message = f"通信方式を {name} に切り替えました。"
        print(message)
        logger.info(message)
        if not linked:
            print("  注記: この方式では入力ログを出せません。")
        # 線は閉じられているので開き直す（従来どおり繋がった状態に戻す）
        self.activateSerial()
        self._on_setting_changed()

    # 入力調停（誰の操作を優先するか）  2026/08/29 段4-c
    #   既定は off で本家と同じ挙動。画面から選び直せる。

    def _refreshArbitrationChoices(self) -> None:
        """選択欄の候補を Sender の許可値から組み直し、現在値を選ぶ。"""
        self.arbitration_cb.config(values=list(Sender.list_arbitration_modes()))
        mode = Sender.resolve_arbitration_mode(
            self.settings.arbitration_mode.get(), logger=logger)
        self.arbitration_mode.set(mode)

    def _arbitrationCooldown(self) -> float:
        """設定の秒数を数へ直す。読めない値は既定の 2 秒として扱う。"""
        try:
            return max(0.0, float(self.settings.arbitration_cooldown.get()))
        except (TypeError, ValueError):
            logger.warning("入力調停の cooldown を読めません。2.0 秒とします")
            return 2.0

    def applyArbitration(self, event: Any = None) -> None:
        """選ばれた入力調停を Sender へ反映する。

        線を開いていなくても選べる。次に開いたときへ効かせるため、
          画面の値は設定へ残す。Sender があればその場で適用する。
        """
        mode = Sender.resolve_arbitration_mode(
            self.arbitration_mode.get(), logger=logger)
        # 解決後の名前を画面へ戻す。知らない名前を選んだまま残さない
        self.arbitration_mode.set(mode)
        self.settings.arbitration_mode.set(mode)
        if self.ser is not None:
            self.ser.setArbitration(mode=mode,
                                    cooldown=self._arbitrationCooldown())
        message = f"入力調停を {mode} に切り替えました。"
        if mode == "script":
            message += "　実行中の手操作は断ります（一時停止すれば操作できます）。"
        elif mode == "human":
            message += "　手で触った直後はスクリプトの操作を断ります。"
        else:
            message += "　どちらも断りません（本家と同じ挙動）。"
        print(message)
        logger.info(message)
        self._on_setting_changed()


    def _start_serial(self) -> None:
        # 入力ログは print と混ぜず、専用のキューへ流す。同じ経路だと
        # 入力ログが上限を食い尽くしてコマンドの出力が捨てられる。
        # 段 VI-b: 運び方は登録簿から名前で作る。作れなければ
        #   Transport 側が理由を出して既定へ戻すので None にはならない。
        self.ser = Sender.Sender(
            self.is_show_serial,
            input_log_emit=LogPane.emitInputLog,
            transport=Transport.create_transport(
                self._selectedTransportName(), logger=logger
            ),
        )
        # 段4-c: 設定の入力調停を反映する。Sender を作り直しても
        #   画面の選択が効いたままになるよう、生成のたびに適用する。
        self.ser.setArbitration(mode=self.arbitration_mode.get(),
                                cooldown=self._arbitrationCooldown())
        self._apply_input_log_settings()
        self.activateSerial()


    def activateSerial(self) -> None:
        """ポートを開く。既に開いていれば閉じてから開き直す。

        Keyboard の停止と再生成もここで面倒を見る。開き直すと KeyPress
        を作り直すが、Keyboard は生成時に渡された古い KeyPress を持ち
        続けるため、押しっぱなしの記録も前の接続のまま残る。呼び出し元
        へ任せると、入口が増えたときに片方だけ直す事故が起きる。
        """
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

        # 開き直す前に必ず止める。呼び出し元が止めているかどうかに
        # 依存しない。チェックの状態は覚えておき、開けたときだけ戻す。
        was_enabled = self.is_use_keyboard.get()
        self._stopKeyboard()

        # 旧コードは自分自身を再帰呼び出ししていた。閉じてそのまま開けばよい。
        if self.ser.isOpened():
            print("Port is already opened and being closed.")
            self.ser.closeSerial()

        self.keyPress = None
        if self.ser.openSerial(
            self.com_port.get(), self.com_port_name.get(), self._currentBaudRate()
        ):
            message = f"COM Port {self.com_port_name.get()} connected successfully"
            print(message)
            logger.debug(message)
            # 2026/08/25 段 V-b: この KeyPress はキーボード操作専用。
            #   入力調停で「人の手入力」として扱われるよう名札を付ける。
            self.keyPress = KeyPress(self.ser, source="keyboard")
            # 新しい KeyPress で作り直す。開けたときだけ戻すので、
            # 失敗時にチェックが入ったまま実体が無い状態にはならない。
            if was_enabled:
                self.is_use_keyboard.set(True)
                self.activateKeyboard()

            # 一部だけ settings へ入れて save() すると、他の項目は起動時の
            # 古い値のまま書き戻される（Show Serial を切り替えてから接続
            # すると元へ戻る、という形で表面化していた）。常に全項目を
            # 集めてから書く _on_setting_changed() に一本化する。
            self._on_setting_changed()
        else:
            self.keyPress = None
            self.is_use_keyboard.set(False)
            message = f"COM Port {self.com_port_name.get()} を開けませんでした"
            print(message)
            logger.warning(message)
            self._on_setting_changed()
        self._update_title()

    def inactivateSerial(self) -> None:
        """ポートを閉じる（Disconnect Port ボタン）。

        keyPress を捨てるだけでは Keyboard のリスナーが生き残る。
        閉じたあとも打鍵を拾い、閉じた Sender へ書きに行く。さらに
        Windows では FocusOut→FocusIn で keyPress=None のまま作り直す
        経路があり、そこで例外になる。切断とキーボードは同時に止める。
        """
        self._stopKeyboard()
        # 画面のチェックも外す。入ったままだと「有効なのに効かない」
        # 状態になり、次に接続したとき勝手に動き出したように見える。
        self.is_use_keyboard.set(False)

        if self.ser is not None and self.ser.isOpened():
            print("Port is closed.")
            self.ser.closeSerial()

        self.keyPress = None
        self._on_setting_changed()
        self._update_title()

    def _stopKeyboard(self) -> None:
        """キーボード操作を止める。止まっていれば何もしない。

        停止処理は切断・再接続・終了の3か所から呼ばれる。同じ手順を
        3回書くと、片方だけ直したときに挙動が食い違う。
        """
        if self.keyboard is not None:
            try:
                self.keyboard.stop()
            except Exception as e:
                logger.warning(f"キーボードの停止で例外: {e}")
            self.keyboard = None

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
            self._stopKeyboard()
            if not is_windows:
                return
            self.root.unbind("<FocusIn>")
            self.root.unbind("<FocusOut>")

    def onFocusInController(self, event: Any) -> None:
        """Windows で窓に戻ったときキーボード操作を復帰させる。

        復帰の条件を「keyboard が None」だけにすると、切断して
        keyPress を捨てたあとでも作り直してしまう。Keyboard 側は
        None を渡されると ValueError を投げるため、窓を切り替えた
        だけで例外になる。接続とチェックの両方が生きているときに限る。
        """
        if event.widget != self.root:
            return
        if self.keyboard is not None:
            return
        if self.keyPress is None or not self.is_use_keyboard.get():
            return
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
        """コマンドを読み込む。パスは import 名になるので相対で渡す。

        Utility.getModuleNames は受け取ったパスの区切りを "." へ置き換えて
        そのまま import 名にする。ここへ絶対パスを渡すと、Windows では
        "c:.PokeCon.....Commands.PythonCommands.MashA" という名前になり、
        先頭の "c:" をパッケージとして探しに行って全件が
        ModuleNotFoundError になる。例外は Utility 側で握られるため
        起動は成功し、「コマンドが1つも出ない」形でしか現れない。

        import 名はカレントディレクトリからの相対でなければならない。
        __main__ で os.chdir(BASE_DIR) しているので、ここは相対のまま
        で BASE_DIR を指す。フォルダを開くボタン（絶対パスでよい）とは
        用途が違うので、同じ書き方に揃えてはいけない。
        """
        python_dir = util.ospath("Commands/PythonCommands")
        mcu_dir = util.ospath("Commands/McuCommands")
        self.py_loader = CommandLoader(python_dir, PythonCommandBase.PythonCommand)
        self.mcu_loader = CommandLoader(mcu_dir, McuCommandBase.McuCommand)

        self.py_classes = self.py_loader.load()
        self.mcu_classes = self.mcu_loader.load()
        if not self.py_classes and not self.mcu_classes:
            # 全滅は「壊れたコマンドが1つある」とは症状が違う。
            # 黙って空の一覧を出すと原因に辿り着けないので知らせる。
            message = (
                "コマンドを1つも読み込めませんでした"
                f"（{python_dir} / {mcu_dir}）"
            )
            print(message)
            logger.error(message)
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
                matched = tags + CommandStats.virtualTags(
                    self.command_stats, name
                )
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

    def openCommandPalette(self, *event: Any) -> str:
        """Ctrl+K でコマンドを検索して実行する小窓を開く。

        Combobox を開いて目で探す操作を、キーボードだけで済ませる。
        候補・表示名・並び順はすべて一覧側と同じ材料（タグと使用履歴）を
        使う。ここで独自の規則を作ると、同じ名前で探しているのに一覧と
        結果が違う、という分かりにくい状態になる。

        走行中は開かない。選び直せてしまうと、いま走っているコマンドと
        画面の表示が食い違い、Stop が何に対する操作か分からなくなる。
        """
        if self._isCommandBusy():
            print("実行中はコマンドを切り替えられません")
            return "break"

        if self._palette is not None:
            self._palette.lift()
            return "break"

        # いま見えているタブを対象にする。Python と Mcu で候補が別なので、
        # 画面と違うタブのコマンドを出すと選んだあとに取り違える。
        combo = self.py_cb
        names = self.py_all_names
        tag_table = self.py_tags
        if self.Command_nb.index(self.Command_nb.select()) != 0:  # type: ignore
            combo = self.mcu_cb
            names = self.mcu_all_names
            tag_table = self.mcu_tags

        # 表示名は一覧と同じ組み立て方にする（タグ前置＋使用履歴）
        labels = {}
        for name in names:
            tags = tag_table.get(name, [])
            label = CommandTags.displayName(name, tags)
            used = CommandStats.summary(self.command_stats, name)
            labels[name] = f"{label}  — {used}" if used else label

        def onClose() -> None:
            self._palette = None

        self._palette = CommandPalette.CommandPalette(
            self.root, list(names), labels, self.command_stats,
            self._runFromPalette, onClose,
        )
        return "break"

    def _runFromPalette(self, name: str) -> None:
        """パレットで選ばれたコマンドを一覧へ反映してから実行する。

        ここで直接コマンドを生成せず、既存の経路（表示を合わせてから
        startPlay）に通す。生成と開始を2か所に持つと、停止や一時停止の
        状態管理が二重になり、どちらが正か決められなくなる。
        """
        combo = self.py_cb
        if self.Command_nb.index(self.Command_nb.select()) != 0:  # type: ignore
            combo = self.mcu_cb

        # 絞り込みで一覧から外れていると選べないので、先に解除する。
        # 検索欄に文字が残ったままだと、実行後の一覧が空に見えて戸惑う。
        if self.search_name.get() or self.tag_name.get() != TAG_ALL:
            self.clearCommandFilter()

        # 素の名前 → いまの表示名。表示名は使用履歴で変わるため毎回引き直す
        relabel = {n: lb for lb, n in self._shown_names.get(combo, {}).items()}
        label = relabel.get(name)
        if label is None:
            print(f"コマンドが見つかりません: {name}")
            logger.warning(f"Command not found in the list: {name}")
            return

        combo.set(label)
        self.assignCommand()
        self.startPlay()


    def openTagEditor(self, *event: Any) -> None:
        """選択中のコマンドのタグを編集する小窓を開く。

        窓の中身は TagEditor.py へ切り出した。ここに残すのは「どの
        タブが選ばれているか」「開いている窓を1つに保つ」という、
        この画面にしか分からない判断だけにする。
        """
        combo = self.py_cb
        table = self.py_tags
        if self.Command_nb.index(self.Command_nb.select()) != 0:  # type: ignore
            combo = self.mcu_cb
            table = self.mcu_tags

        # 開いている最中に本体を触られると、保存時に食い違う。
        if self._tag_editor is not None:
            self._tag_editor.lift()
            return

        def onClose() -> None:
            self._tag_editor = None

        self._tag_editor = TagEditor.openEditor(
            self.root,
            self._selectedName(combo),
            table,
            self.setCommandItems,
            onClose,
        )

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
        """選択クラスが変わった場合だけ、選択時インスタンスを作り直す。"""
        if self._isCommandBusy():
            return

        def ensure(current: Any, selected: Any) -> Any:
            if selected is None:
                return None
            if current is not None and type(current) is selected:
                return current
            return self._buildCommand(selected)

        mcu_class = self.mcu_map.get(self._selectedName(self.mcu_cb))
        self.mcu_cur_command = ensure(self.mcu_cur_command, mcu_class)
        py_class = self.py_map.get(self._selectedName(self.py_cb))
        self.py_cur_command = ensure(self.py_cur_command, py_class)

        if self.Command_nb.index(self.Command_nb.select()) == 0:  # type: ignore
            self.cur_command = self.py_cur_command
        else:
            self.cur_command = self.mcu_cur_command
        enabled = self.cur_command is not None
        self.startButton["state"] = "normal" if enabled else "disabled"













    def _buildCommand(self, cmd_class: Any) -> Any:
        """コマンドを1つ生成する。失敗したら None を返す。

        ダイアログを出すコマンドはワーカースレッドから tk を触ることに
        なるため、生成した時点で GUI のルートを渡しておく。画像認識の
        コマンドは gui（プレビュー）を受け取れるが、通常のコマンドは
        受け取れず、渡し口が無いままだった。属性で渡せば全種類に効く。
        """
        if cmd_class is None:
            return None
        try:
            if issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
                # 旧: except TypeError で握っていたため、コマンド内部で起きた
                # TypeError まで「古い形式」と誤判定し引数1つで再生成していた。
                # シグネチャを見て渡せる引数の数を先に決める。
                if WindowUtils.acceptsGuiArg(cmd_class):
                    command = cmd_class(self.camera, self.preview)
                else:
                    command = cmd_class(self.camera)
            else:
                command = cmd_class()
        except Exception:
            name = getattr(cmd_class, "NAME", getattr(cmd_class, "__name__", "?"))
            logger.error(traceback.format_exc())
            print(f"コマンドの初期化に失敗しました: {name}")
            return None

        # ダイアログを GUI スレッドで作らせるための足がかり。
        # PythonCommandBase._guiRoot がここを最初に見る。
        # McuCommand はダイアログを出さないので渡す意味が無く、
        # 無用な属性を生やさないよう対象を絞る（WIN-09）。
        if isinstance(command, PythonCommandBase.PythonCommand):
            # 代入できないコマンド（__slots__ や __setattr__ を持つもの）が
            # あるため握る。ここは初期化の失敗として扱ってはいけない。
            # 失敗を致命扱いにすると __slots__ のコマンドが選べなくなり、
            # 2026/08/13 に直した後方互換の破壊を再発させる。
            # 渡せなくても従来どおり動く（ダイアログが呼び出し元の
            # スレッドで作られるだけで、これは以前と同じ）。
            try:
                command.gui_root = self.root
            except Exception as e:
                # ログ欄にも出す。debug だけだと、ダイアログまわりで
                # 妙な挙動を追うときに手がかりが残らない。
                name = getattr(cmd_class, "NAME", getattr(cmd_class, "__name__", "?"))
                message = f"gui_root を渡せませんでした（{name}）: {e}"
                print(message)
                logger.debug(message)
        # 設定はここでは渡さない。以前は command.settings = self.settings と
        # 参照ごと渡していたが、それでは reload_com_port が tk 変数の get()
        # をワーカースレッドから呼ぶことになり、Tcl を別スレッドで触る形に
        # なっていた（PCB-20 案C）。COM の設定は Start の直前に GUI スレッド
        # で _snapshotSerialConfig() が通常の Python 値へ写す。生成から Start
        # までに COM ポートを選び直される可能性があるため、渡す時点は生成時
        # ではなく Start 直前でなければならない。

        return command

    def _snapshotSerialConfig(self, command: Any) -> None:
        """COM の設定を通常の Python 値へ写してコマンドへ渡す。

        必ず GUI スレッド（Start のコールバック）から呼ぶこと。tk 変数の
        get() は Tcl インタプリタを呼ぶため、コマンド側のワーカー
        スレッドから読むと Tkinter のスレッド制約に触れる。ここで int /
        str へ落としてしまえば、以後は Tk と無関係な値になる。

        写す時点が Start の直前であることも要点。コマンドの生成は選択時に
        行われるので、生成時に渡すと、そのあと COM ポートを選び直しても
        古い値のまま残る。Start のたびに上書きする。

        値そのものは画面の変数（self.com_port など）から取る。settings は
        起動時に読んだ内容で、画面で選び直した分は _on_setting_changed()
        を通るまで入らない。いま繋いでいる先と一致するのは画面側。
        """
        try:
            config = {
                "com_port": int(self.com_port.get()),
                "com_port_name": str(self.com_port_name.get()),
                "baud_rate": self._currentBaudRate(),
            }
        except (tk.TclError, ValueError) as e:
            # 空欄や未選択のときは変換に失敗する。ここで止めはしない
            # （COM を使わないコマンドまで動かせなくなる）。渡さなければ
            # reload_com_port が理由を出して False を返す。
            logger.warning(f"COM の設定を写せませんでした: {e}")
            return

        try:
            command.serial_config = config
        except Exception:
            # __slots__ や __setattr__ を持つコマンドには渡せない。
            # gui_root と同じ扱いで、渡せなくても実行そのものは続ける。
            logger.debug(f"serial_config を渡せませんでした: {type(command)}")

    def _isCommandBusy(self) -> bool:
        """コマンドが走っている、または停止処理の最中かを返す。

        以前はボタンの表示文字（"Start" かどうか）で判定していた。
        表示は状態を映すためのもので、状態そのものではない。文言を
        変えた・開始の途中で例外が出た・外から表示を触った、のどれでも
        判定が狂う。状態は _command_state だけで持つ。
        """
        return self._command_state != "idle"

    def _setCommandRunningUI(self) -> None:
        """実行中の見た目にそろえる。状態の変更とセットで呼ぶ。"""
        self.startButton["text"] = "Stop"
        self.startButton["command"] = self.stopPlay
        self.startButton["state"] = "normal"
        self.reloadCommandButton["state"] = "disabled"
        # 一時停止に対応しない種類（MCU コマンド）では押せないようにする。
        # 押せるのに「対応していません」とだけ出るのは、壊れて見える。
        supports_pause = callable(getattr(self.cur_command, "togglePause", None))
        self.pauseButton["text"] = "Pause"
        self.pauseButton["state"] = "normal" if supports_pause else "disabled"
        self._running_command = str(getattr(self.cur_command, "NAME", ""))
        self._paused = False
        self._update_title()

    def reloadCommands(self) -> None:
        """リロード後も同じコマンドが選ばれた状態に戻す。

        復元は表示位置ではなく名前で行う。絞り込みや並び替えが入ると
        位置は当てにならないうえ、タグを前置した表示名も変わりうる。
        """
        # 実行中の再ロードを断る。ボタンは disabled にしてあるが、
        # F5 のキーバインドは生きているため、ここで塞がないと通る。
        # 走っているインスタンスは古いクラス定義を持ったまま、モジュール
        # 側のグローバルやクラス変数だけが新しくなり、新旧が混在する。
        if self._isCommandBusy():
            print("実行中はコマンドを再ロードできません")
            logger.warning("Reload is unavailable while a command is running")
            return

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
        """選択中のコマンドを開始する。

        順序が重要。先に状態と見た目を実行中へ倒してから start する。
        逆にすると、ごく短いコマンドが start の中で終わった場合に、
        後始末が先に走り、あとから実行中の見た目へ書き換えてしまう。
        """
        # 二重起動を断る。Tk は同じボタンのコールバックを並行実行しないが、
        # F6・パレット・外部呼び出しからも入って来られる。
        if self._isCommandBusy():
            print("すでにコマンドが動いています")
            logger.warning("A command is already running")
            return

        self.assignCommand()

        # 旧コードはメッセージを出すだけで先へ進み、None.NAME で落ちていた
        if self.cur_command is None:
            print("No commands have been assigned yet.")
            logger.warning("No commands have been assigned yet.")
            return

        # シリアル未接続でも開始は妨げない。従来はここに判定が無く、
        # 画像認識だけのコマンドや机上検証（verify_all）は繋がなくても
        # 最後まで走っていた。ここで一律に止めると、既存のコマンドが
        # 動かなくなる。既存コマンドは REQUIRES_SERIAL を書いていないので、
        # 既定は「不要」でなければ後方互換が壊れる。
        # 明示的に REQUIRES_SERIAL = True と書いたコマンドだけを止める。
        if getattr(self.cur_command, "REQUIRES_SERIAL", False):
            if self.ser is None or not self.ser.isOpened():
                message = "このコマンドは COM ポートの接続が必要です"
                print(message)
                logger.warning(message)
                return
        elif self.ser is None or not self.ser.isOpened():
            # 止めはしないが、黙って始めると「動いているのに何も起きない」
            # に見える。操作を送る段で失敗することを先に知らせておく。
            print("注意: COMポートが未接続です（操作の送信はできません）")

        # COM の設定を GUI スレッドのここで通常値へ写す。コマンドの
        # 生成は選択時なので、そこで渡すと選び直したあとの値を拾えない。
        # reload_com_port はこの値だけを見る（tk 変数は読まない）。
        self._snapshotSerialConfig(self.cur_command)

        message = f"Start {self.cur_command.NAME}"
        print(message)
        logger.info(message)
        # 前回の実行を先に読む。record より後だと今回の分で上書きされる。
        cmd_name = str(self.cur_command.NAME)
        before = CommandStats.summary(self.command_stats, cmd_name)
        if before:
            print(f"  ({before})")

        # 先に実行中へ倒す。start はスレッドを起こすので、戻ったときには
        # もう終わっていることがある。あとから実行中の見た目にすると、
        # 終了後の後始末を上書きして Stop 表示のまま固まる。
        self._command_state = "running"
        self._setCommandRunningUI()

        # 実行ごとに世代を進める。後始末（stopPlayPost）が自分の世代の
        # ものかを見分けるために使う。番号が無いと、止まりきらなかった
        # 前回のスレッドが後から後始末を呼んだときに、いま走っている
        # コマンドの画面を「空き」へ戻してしまう。
        self._run_token += 1
        token = self._run_token

        try:
            started = self.cur_command.start(
                self.ser, lambda: self.stopPlayPost(token)
            )
        except Exception:
            # スレッドを起こす前に落ちると後始末も呼ばれない。ここで戻す。
            print("コマンドを開始できませんでした")
            logger.error(traceback.format_exc())
            self._stopPlayPostOnGui()
            return

        # start が例外を出さずに「何もしなかった」場合を拾う。
        # PythonCommand は前のスレッドが生きていれば False、MCU コマンドは
        # ポート未接続で False を返す。どちらも後始末は呼ばれないため、
        # ここで戻さないと画面だけ実行中のまま固まる。
        # 判定は is False で行う。start を上書きしている既存のコマンドは
        # 戻り値を返さない（None）ため、not started で見ると全て失敗扱いに
        # なってしまう。None は従来どおり「開始できた」とみなす。
        if started is False:
            print("コマンドを開始できませんでした")
            logger.warning(f"Failed to start: {cmd_name}")
            self._stopPlayPostOnGui()
            return

        # 使用履歴は開始できたあとで数える。開始前に足すと、起動に失敗した
        # 回数まで「実行回数」に混ざる。ファイルへ書くのは終了時に1回だけ。
        CommandStats.record(self.command_stats, cmd_name)
        self._stats_dirty = True
        # 記録は辞書へ即時入るので、選択肢もその場で作り直せる。
        # Reload を待つと「さっき使ったのに最近使ったに出ない」ことになる。
        self._refreshTagChoices()

    def stopPlay(self) -> None:
        """実行中のコマンドへ停止を要求する。

        停止したことにするのは、後始末(stopPlayPost)が呼ばれたとき。
        ただし end() が例外を投げた・コマンドが後始末を呼ばない・
        外部I/O で抜けられない、のいずれでも呼ばれない。その場合に
        ボタンが disabled のまま操作を受け付けなくなるため、見張りを置く。
        """
        if self.cur_command is None:
            return
        message = f"Stop {self.cur_command.NAME}"
        print(message)
        logger.info(message)
        self._command_state = "stopping"
        self.startButton["state"] = "disabled"
        try:
            self.cur_command.end(self.ser)
        except Exception:
            logger.error(traceback.format_exc())
            print("停止要求で例外が発生しました")
            self._stopPlayPostOnGui(self._run_token)
            return
        self._watch_after_id = self.root.after(
            STOP_WATCH_MS, lambda: self._watchStopped(0, self._run_token)
        )

    def _watchStopped(self, waited: int = 0, token: int | None = None) -> None:
        """停止を頼んだのに戻って来ない場合、操作だけは戻す。

        スレッドが生きているかどうかは実体に聞く。生きていれば、まだ
        待つ。死んでいるのに後始末が来ていないなら、後始末が呼ばれない
        経路に入ったということなので、こちらで画面を空きへ戻す。

        生きている間は待ち続ける。時間で打ち切って画面だけ空きへ戻すと、
        止まっていないスレッドと次に始めたコマンドが同じシリアルを同時に
        操作することになる。Python のスレッドは外から安全に殺せないので、
        「勝手に戻す」よりも「戻せないことを伝える」ほうが安全側になる。
        代わりに、待っていることと経過を一定間隔で知らせる。

        token は Stop を始めた時点の世代番号。見張りが遅れて発火した
        ときに、すでに次の実行が始まっていれば触らない。
        """
        if token is not None and token != self._run_token:
            logger.debug("古い実行の見張りを終了しました")
            return

        if self._command_state != "stopping":
            return
        thread = getattr(self.cur_command, "thread", None)
        if thread is not None and thread.is_alive():
            waited += STOP_WATCH_MS
            if waited % STOP_NOTIFY_MS == 0:
                message = (
                    f"コマンドが停止しません（経過 {waited // 1000}秒）。"
                    "外部の入出力を待っている可能性があります"
                )
                print(message)
                logger.warning(message)
                self._stop_waited = waited
                self._update_title()
            self._watch_after_id = self.root.after(
                STOP_WATCH_MS, lambda: self._watchStopped(waited, token)
            )
            return
        self._stop_waited = 0
        message = "コマンドの後始末が呼ばれませんでした。操作を戻します"
        print(message)
        logger.warning("postProcess was not called. restoring the UI")
        self._stopPlayPostOnGui(token)

    def stopPlayPost(self, token: int | None = None) -> None:
        """コマンド終了後の後始末。

        呼び出し元は PythonCommandBase の _cleanup で、コマンドを走らせて
        いるワーカースレッドから直接呼ばれる。tkinter はスレッドセーフで
        ないので、ウィジェットを触る処理は after(0) で GUI スレッドへ渡す。
        """
        if token is not None and token != self._run_token:
            # 止まりきらなかった前回のスレッドが、いまごろ後始末を呼んで
            # きた場合。すでに別のコマンドが走っているので画面は触らない。
            logger.debug("古い実行の後始末を無視しました")
            return
        if self._closing:
            # 終了処理が始まっている。ここで after を積むと、destroy の
            # 直前に割り込んで破棄途中のウィジェットを触ることがある。
            # 画面はもう畳む段なので後始末は不要。
            logger.debug("終了処理中のため後始末を省きました")
            return
        try:
            self.root.after(0, lambda t=token: self._stopPlayPostOnGui(t))
        except (tk.TclError, RuntimeError):
            # 終了処理の最中に終わった場合。画面はもう無いので何もしない。
            # RuntimeError は「GUI スレッドが mainloop にいない」ときに
            # after が投げる。捕まえないと呼び出し元（_cleanup）へ抜け、
            # 正常な停止が異常終了に化ける。
            logger.debug("Window is already destroyed. skipped the post process")

    def _stopPlayPostOnGui(self, token: int | None = None) -> None:
        """後始末の本体。必ず GUI スレッドで動く。

        token は「どの実行の後始末か」を表す世代番号。after(0) で積んで
        から実際に走るまでの間に、次の実行が始まっていることがある。
        積む時点だけで見ても足りず、走る時点でも見る必要がある。
        （実行1の後始末が積まれたまま _watchStopped が先に画面を戻し、
        　利用者が実行2を始めたあとで積まれていた分が走ると、動いて
        　いる実行2の画面が空きへ戻り Start が押せてしまう）

        操作を戻すことを最優先にする。一覧の作り直しは付随処理なので、
        そこで例外が出てもボタンは戻っていなければならない。以前は
        ひと続きだったため、履歴の更新で落ちると Stop 表示のまま
        操作を受け付けなくなる余地があった。
        """
        if token is not None and token != self._run_token:
            logger.debug("古い実行の GUI 後処理を無視しました")
            return

        # 同じ実行について複数回呼ばれても、一覧の作り直しまでは
        # 繰り返さない。stopPlayPost 経由と _watchStopped 経由の
        # 両方から来ることがあるため。状態の復元は冪等なので通す。
        already_idle = self._command_state == "idle"

        self._command_state = "idle"
        self._stop_waited = 0
        self.startButton["text"] = "Start"
        self.startButton["command"] = self.startPlay
        self.startButton["state"] = "normal"
        self.reloadCommandButton["state"] = "normal"
        self.pauseButton["text"] = "Pause"
        self.pauseButton["state"] = "disabled"
        self._paused = False
        self._running_command = ""
        self._update_title()

        # ここから先は無くても操作できる処理。失敗しても状態は戻す。
        if already_idle:
            # すでに戻っている＝別経路で後始末済み。一覧の作り直しを
            # 二重に走らせても実害は無いが、選択の復元が二度動くため省く。
            return
        try:
            # 走行中は選択を動かさないため applyCommandFilter を見送っている。
            # 空いたこの時点で一覧を見直し、「最近使った」「よく使う」の
            # 並びと絞り込みを実際の履歴に合わせる。
            self._refreshTagChoices()
            self.applyCommandFilter()
        except Exception:
            logger.error(traceback.format_exc())
            print("コマンド一覧の更新に失敗しました（操作は続けられます）")

    def ReloadCommandWithF5(self, *event: Any) -> None:
        # 実行中の可否は reloadCommands 側で判断する。入口ごとに条件を
        # 書くと、片方だけ直したときに挙動が食い違う。
        self.reloadCommands()

    def StartCommandWithF6(self, *event: Any) -> None:
        if self._isCommandBusy():
            print("Command is now working!")
            logger.debug("Command is now working!")
            return
        self.startPlay()

    def StopCommandWithEsc(self, *event: Any) -> None:
        # 停止処理の最中は受け付けない。二重に end() を呼ぶ意味が無い。
        if self._command_state == "running":
            self.stopPlay()

    def togglePause(self) -> None:
        """実行中のコマンドを一時停止／再開する。

        停止（Stop）はコマンドを終わらせるため、次に動かすときは最初
        からやり直しになる。長い手順の途中で少し手を離したいだけの
        ときに使えないので、状態を保ったまま足止めする口を分けて置く。

        一時停止に対応しない種類ではボタン自体を disabled にしてある
        （_setCommandRunningUI）。ここへ来るのは F7 の打鍵だけ。
        """
        cmd = self.cur_command
        if cmd is None or not getattr(cmd, "alive", False):
            return
        if not callable(getattr(cmd, "togglePause", None)):
            # MCU コマンドなど、一時停止に対応しない種類
            print("This command does not support pause.")
            return
        paused = cmd.togglePause()
        self.pauseButton["text"] = "Resume" if paused else "Pause"
        self._paused = paused
        self._update_title()

    def PauseCommandWithF7(self, *event: Any) -> None:
        if self._command_state == "running":
            self.togglePause()

    # ------------------------------------------------------------------
    # ログ表示
    # ------------------------------------------------------------------

    def display_text(self) -> None:
        self._display_after_id = None
        if self._closing:
            return
        try:
            follow = self.log_autoscroll.get()
            LogPane.flushQueue(LogPane.text_queue, self.logArea, follow)
            LogPane.flushQueue(LogPane.sub_log_queue, self.subLogArea, follow)
            if self.show_input_log.get():
                if self.ser is not None and self.ser.isOpened():
                    self.ser.flushInputLog()
                LogPane.flushQueue(LogPane.input_log_queue, self.inputLogArea, follow)
        except tk.TclError:
            if not self._closing:
                logger.debug("ログWidget破棄中の更新を停止しました")
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            if not self._closing:
                try:
                    self._display_after_id = self.logArea.after(LogPane.FLUSH_INTERVAL_MS, self.display_text)
                except (tk.TclError, RuntimeError):
                    self._display_after_id = None
    def run(self) -> None:
        logger.debug("Start Poke-Controller")
        self.mainwindow.mainloop()

    def _stopRunningCommand(self) -> None:
        """終了に先立ってコマンドを止める。

        alive は「停止を要求されていないか」でしかない。finish() も
        sendStopRequest() もスレッドが抜ける前に False にするため、
        alive=False でもスレッドが後始末の途中ということがある。
        そこだけを見て戻ると、直後に閉じたシリアルへ書きに行く。
        判断はスレッドの生存で行う。

        止まりきるまでは待たない。長い wait や外部I/O の最中だと
        いつ抜けるか読めず、待つと画面が固まったように見える。
        スレッドは daemon なので、抜けきらなくてもプロセスは終わる。
        ここでの目的は、閉じたシリアルへ書きに行くのを減らすこと。
        """
        cmd = self.cur_command
        if cmd is None:
            return

        thread = getattr(cmd, "thread", None)
        running = thread is not None and thread.is_alive()
        if not running and not getattr(cmd, "alive", False):
            return

        if getattr(cmd, "alive", False):
            try:
                cmd.end(self.ser)
            except Exception as e:
                logger.warning(f"停止要求で例外: {e}")

        # 後始末（キーを離す・postProcess）が走る余地を与える。待ちは
        # 短く区切る。ここで長く待つと終了操作そのものが固まって見える。
        if running:
            thread.join(timeout=1.0)
            if thread.is_alive():
                print("コマンドが停止しないまま終了します")
                logger.warning("Command did not stop in time. exiting anyway")

    def exit(self) -> None:
        """終了処理。入口が複数あるので、二重に走らせない。

        × ボタン（WM_DELETE_WINDOW）とメニューの「終了」の2経路があり、
        確認ダイアログを出しているあいだにもう一方から入れる。2回目は
        破棄済みのウィジェットを触るため TclError（bad window path name）
        になる。_closing は「終了処理を始めたか」の印なので、判定は
        ダイアログより前に置く。後ろに置くと、確認を待っている間に
        入って来た2回目を防げない。
        """
        if self._closing:
            logger.debug("exit() は既に実行中のため無視しました")
            return
        if not tkmsg.askyesno("確認", "Poke Controllerを終了しますか？"):
            return

        self._closing = True
        # after の ID は「登録したウィジェット」に紐づく。別の
        # ウィジェットで after_cancel しても取り消せず、予約は生き残る。
        # display_text は logArea.after で、_restore_sash は root.after で
        # 登録しているので、取り消しも同じ相手へ頼む。取り消し漏れが
        # あると destroy の最中に発火し、破棄途中のウィジェットを触って
        # TclError（can't delete Tcl command）になる。
        for widget, after_id in (
            (self.logArea, self._display_after_id),
            (self.root, self._sash_after_id),
            (self.root, self._watch_after_id),
        ):
            if after_id is None:
                continue
            try:
                widget.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass
        self._display_after_id = None
        self._sash_after_id = None
        self._watch_after_id = None
        self._stopRunningCommand()

        self._stopKeyboard()
        self.closingController()
        # 映像の描画ループをここで止める。CaptureArea のマウス操作
        # （LStick / RStick Mouse）は self.ser へ書くため、シリアルを
        # 閉じる前に「送る側」を止めておく。Unbind だけでは capture()
        # の周回自体は生き続け、閉じた口を持ったまま回ることになる。
        # 逆順にすると「閉じた先へ書きに行く」経路が残る（WIN-06）。
        if self.preview is not None:
            try:
                self.preview.UnbindLeftClick()
                self.preview.UnbindRightClick()
            except Exception as e:
                logger.warning(f"マウス操作の解除で例外: {e}")
            try:
                self.preview.stopCapture()
            except Exception as e:
                logger.warning(f"映像の停止で例外: {e}")

        if self.ser is not None and self.ser.isOpened():
            self.ser.closeSerial()
            print("Serial disconnected")

        # ウィンドウを壊す前に位置とサイズを控える（destroy 後は取れない）
        self._remember_geometry()
        self._save_settings()
        # 使用履歴も同じ場所で書き出す。実行のたびに書きに行かない代わり、
        # ここを通らないと記録が残らないので、設定の保存と並べておく。
        if self._stats_dirty:
            CommandStats.save(self.command_stats, self.profile)

        # 映像を止めたあとで解放する。順序を逆にすると解放済みメモリを読む
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
        self.settings.baud_rate.set(self._currentBaudRate())
        self.settings.camera_id.set(self._cameraIdOrNone() or 0)
        self.settings.camera_key.set(self.camera_key.get())
        self.settings.input_log_enabled.set(self.show_input_log.get())
        # 段 VI-b: 通信方式。起動引数で一時的に替えている場合も、
        #   画面に出ている値＝実際に使っている値なのでそのまま保存する。
        self.settings.transport_name.set(self.transport_name.get())
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
        # 保存の失敗で操作そのものを止めない。チェックを1つ入れた拍子に
        # 例外が上がると、そのウィジェットのコールバックが切れて以後
        # 反応しなくなる。書けなかったことはログへ出し、操作は続ける。
        try:
            self._save_settings()
        except Exception as e:
            logger.warning(f"設定の保存に失敗しました: {e}")
            print(f"設定の保存に失敗しました: {e}")


if __name__ == "__main__":
    # 起動場所に依存せず、このファイルのある場所を作業ディレクトリにする。
    # Commands や Captures を相対で開く箇所が残っているため、ここで揃える。
    os.chdir(BASE_DIR)

    PokeConLogger.root_logger()
    logger.info("The root logger is created.")

    parser = argparse.ArgumentParser(description=NAME)
    parser.add_argument(
        "--profile",
        default="",
        help="設定と共有メモリを分ける名前。複数台を並列起動するときに指定する"
        "（例: --profile switch1）。未指定なら従来どおり settings.ini を使う。",
    )
    parser.add_argument(
        "--transport",
        default="",
        help="通信方式のプリセット名（例: --transport legacy_text）。"
        "設定ファイルより優先する。未指定なら settings.ini の [Transport] "
        "name を使う。使える名前は Transport.py の登録簿にあるもの。",
    )
    args = parser.parse_args()

    app = PokeControllerApp(profile=args.profile, transport=args.transport)
    app.run()
