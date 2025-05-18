# import argparse
# import time
from __future__ import annotations

# import hashlib
import builtins
import os
import platform
import queue
import subprocess
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
import tomllib
import traceback

# from get_pokestatistics import GetFromHomeGUI
from dataclasses import dataclass
from datetime import datetime
from logging import DEBUG, NullHandler, StreamHandler, getLogger  # noqa: F401
from tkinter import Tk
from typing import Any, Optional, Protocol  # noqa: F401

import cv2
import PokeConLogger
import serial.tools.list_ports
import Settings
import Utility as util
from Camera import Camera  # , CameraQueue
from CommandLoader import CommandLoader
from Commands import McuCommandBase, PythonCommandBase, Sender
from Commands.Keys import KeyPress
from GuiAssets import CaptureArea, ControllerGUI
from Keyboard import SwitchKeyboardController
from loguru import logger
from Menubar import PokeController_Menubar
from pygubu.widgets.filterabletreeview import FilterableTreeview

os.environ["NUMBA_DEBUG"] = "0"

# from customtkinter import CTkComboBox
# import threading
from pygubu.widgets.scrollbarhelper import ScrollbarHelper

MAX_RECENT_COMMANDS = 20


def get_version() -> Any:
    """
    pyproject.tomlファイルからバージョン情報を取得する関数

    Returns:
        Any: プロジェクトのバージョン情報
    """
    with open("pyproject.toml", "rb") as f:
        pyp = tomllib.load(f)
    return pyp["project"]["version"]


# アプリケーション定数
NAME = "Poke-Controller Modified"
VERSION = f"v{get_version()}"  # based on 1.0-beta3(custom by @dragonite303)
DEFAULT_FPS = 60
DEFAULT_WINDOW_SIZE = "640x360"
CAPTURE_DIR = "Captures"
# 開発者向け設定（未実装）
DEVELOPER_MODE = False

"""
Todo:
- デバッグ用にPoke-Controller本体にコントローラーを接続して動かしたい
- 画像認識の時の枠を設定でON/OFFできると嬉しい
- コマンドのリロード機能の実装
- コマンドフィルター時にStartするとバグりそうなので、一意の識別子でかんりするようにする
- 出力先の選択機能(?)
- 新規追加機能(always show gui con.)の実装
- pythonコマンドの実行日時を記録するタブの作成
"""


class PokeControllerApp:
    def __init__(self, master: Tk | None = None) -> None:
        """初期化"""
        self._setup_custom_print()
        self._initialize_root_window(master)
        self._setup_instance_variables()
        self._initialize_ui()
        self._setup_initial_configurations()
        self._load_tab_state()  # この行を追加
        # Menu bar 初期化
        self.menu = PokeController_Menubar(self)
        self.root.config(menu=self.menu)

    def _initialize_root_window(self, master: Tk | None) -> None:
        """ルートウィンドウの初期化処理"""
        if master is None:
            master = Tk()
        self.root = master
        self.root.title(f"{NAME} {VERSION}")
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(1, weight=1)

    def _setup_instance_variables(self) -> None:
        """インスタンス変数の初期化"""
        # Baud Rateを変更する場合は"readonly"に変更してください。
        self.baud_rate_state: str = "disabled"
        self.ser: Sender.Sender
        self.preview: CaptureArea
        self.settings: Settings.GuiSettings
        self.controller: ControllerGUI | None = None
        self.poke_treeview: ttk.Treeview | None = None
        self.keyPress: KeyPress | None = None
        self.keyboard: SwitchKeyboardController | None = None
        self.camera_dic: dict[str, str] = {}
        self.selected_py_id: int | None = None  # Pythonコマンド選択ID
        self.selected_mcu_id: int | None = None  # MCUコマンド選択ID
        self.selected_recent_py_id: int | None = None  # Recentコマンド選択ID

    def _setup_initial_configurations(self) -> None:
        """初期設定の適用"""

        # 仮置フレームを削除
        # self.camera.destroy()

        # 標準出力をログにリダイレクト(旧式も残しておく)
        # sys.stdout = QueueStdoutRedirector(self.log_text_area)
        # self.log_text_area.after(100, self.display_text)
        # sys.stdout = QueuePrintRedirector(self.log_text_1)
        # sys.stderr = QueuePrintRedirector(self.log_text_2)
        self.log_text_1.after(100, lambda: self.display_text(self.log_text_1))
        self.log_text_2.after(100, lambda: self.display_text(self.log_text_2))
        # th_text = threading.Thread(target=self.display_text)
        # th_text.start()

        # load settings file
        self.loadSettings()
        # 各tk変数に設定値をセット(コピペ簡単のため)
        self.show_realtime_var.set(self.settings.is_show_realtime.get())
        self.show_serial_var.set(self.settings.is_show_serial.get())
        self.use_keyboard_var.set(self.settings.is_use_keyboard.get())
        self.fps_var.set(self.settings.fps.get())
        self.show_size_var.set(self.settings.show_size.get())
        self.com_port_var.set(self.settings.com_port.get())
        self.baud_rate_var.set(self.settings.baud_rate.get())
        self.camera_id_var.set(self.settings.camera_id.get())
        # 各コンボボックスを現在の設定値に合わせて表示
        self.fps_combobox.current(self.fps_combobox["values"].index(self.fps_var.get()))
        self.show_size_combobox.current(
            self.show_size_combobox["values"].index(self.show_size_var.get())
        )

        # 最近使用したコマンド履歴を読み込む
        self._load_recent_commands()

        if platform.system() != "Linux":
            try:
                self.locateCameraCmbbox()
                self.camera_id_entry.configure(state="disabled")

                self.com_port_combobox.configure(
                    state="readonly",
                    textvariable=self.com_name_var,
                    values=[port.device for port in serial.tools.list_ports.comports()],
                )
                self.com_port_entry.configure(state="disabled")
                self.com_port_combobox.current(
                    self.com_port_combobox["values"].index(
                        f"COM{self.com_port_entry.get()}"
                    )
                )
            except Exception as e:
                logger.error(f"An error occurred: {e}")
                logger.error(traceback.format_exc())
                # Locate an entry instead whenever dll is not imported successfully
                self.camera_name_var.set(
                    "An error occurred when displaying the camera name in the Win/Mac environment."
                )
                logger.warning(
                    "An error occurred when displaying the camera name in the Win/Mac environment."
                )
                self.camera_name_combobox.configure(state="disabled")
                self.com_port_combobox.configure(
                    state="disabled",
                )
        elif platform.system() != "Linux":
            self.camera_name_var.set("Camera name detection is not supported on Linux")
            self.camera_name_combobox.config(state="disable")
            self.use_keyboard_chk.config(state="disable")
            return
        else:
            self.camera_name_var.set("Unknown environment. Cannot show Camera name.")
            self.camera_name_combobox.config(state="disable")

        # open up a camera
        self.camera = Camera(self.fps_var.get())
        # self.camera = CameraQueue(self.fps.get(), self.camera_id_var.get())
        self.openCamera()
        # activate serial communication
        self.ser = Sender.Sender(self.show_serial_var)
        self.activateSerial()
        self.activateKeyboard()
        self.preview = CaptureArea(
            camera=self.camera,
            fps=self.fps_var.get(),
            is_show=self.show_realtime_var,
            ser=self.ser,
            master=self.root,
            left_stick_mouse_var=self.left_stick_mouse_var,
            right_stick_mouse_var=self.right_stick_mouse_var,
            show_width=int(self.show_size_var.get().split("x")[0]),
            show_height=int(self.show_size_var.get().split("x")[1]),
        )
        self.preview.config(cursor="crosshair")
        self.preview.grid(column=0, padx=5, pady=5, row=1, sticky=tk.NSEW)
        self.loadCommands()

        self.show_size_tmp = self.show_size_combobox["values"].index(
            self.show_size_combobox.get()
        )
        self.root.bind("<Key-F5>", self.ReloadCommandWithF5)
        logger.debug("Bind F5 key to reload commands")
        self.root.bind("<Key-F6>", self.StartCommandWithF6)
        logger.debug("Bind F6 key to execute commands")
        self.root.bind("<Key-Escape>", self.StopCommandWithEsc)
        logger.debug("Bind Escape key to stop commands")

        # Main widget
        self.mainwindow = self.root

        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.preview.startCapture()

        self.menu = PokeController_Menubar(self)
        self.root.config(menu=self.menu)

        # logging.debug(f'python version: {sys.version}')

    def _initialize_ui(self) -> None:
        """UI全体を初期化し、主要コンポーネントをセットアップする"""
        self._setup_utilities()
        self._setup_tabs()
        self._setup_log_area()
        if not DEVELOPER_MODE:
            self.camera_api_label.grid_remove()
            self.camera_api_combobox.grid_remove()
            self.camera_resolution_label.grid_remove()
            self.camera_resolution_combobox.grid_remove()

        # カラムソート用の変数を初期化
        self.sort_column = ""
        self.sort_reverse = False

    def _setup_utilities(self) -> None:
        self.utility_frame = ttk.Frame(self.root, name="utility_frame")
        self.capture_button = ttk.Button(self.utility_frame, name="capture_button")
        self.capture_button.configure(text="Capture")
        self.capture_button.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        self.capture_button.configure(command=self.saveCapture)
        self.open_capture_button = ttk.Button(
            self.utility_frame, name="open_capture_button", command=self.OpenCaptureDir
        )
        self.img_icons8OpenDir16 = tk.PhotoImage(file="./assets/icons8-OpenDir-16.png")
        self.open_capture_button.configure(
            image=self.img_icons8OpenDir16, style="Toolbutton", width=5
        )
        self.open_capture_button.grid(column=1, padx=5, pady=5, row=0, sticky="nsew")
        self.preview_size_label = ttk.Label(
            self.utility_frame, name="preview_size_label"
        )
        self.preview_size_label.configure(text="Preview Size:")
        self.preview_size_label.grid(column=2, padx=5, pady=5, row=0)
        self.show_size_combobox = ttk.Combobox(
            self.utility_frame, name="show_size_combobox"
        )
        self.show_size_var = tk.StringVar()
        self.show_size_combobox.configure(
            state="readonly",
            takefocus=False,
            textvariable=self.show_size_var,
            values=["640x360", "1280x720", "1920x1080", "2560x1440", "3840x2160"],
            width=10,
        )
        self.show_size_combobox.grid(column=3, padx=5, pady=5, row=0, sticky="ew")
        self.show_size_combobox.bind(
            "<<ComboboxSelected>>", self.applyWindowSize, add=""
        )
        self.clear_log_button = ttk.Button(self.utility_frame, name="clear_log_button")
        self.clear_log_button.configure(style="Toolbutton", text="Clear Log")
        self.clear_log_button.grid(column=5, padx=5, pady=5, row=0, sticky="e")
        self.clear_log_button.configure(command=self.clear_log)
        self.utility_frame.grid(column=0, padx=5, pady=5, row=0, sticky="ew")
        self.utility_frame.columnconfigure(4, weight=1)

    def _setup_tabs(self) -> None:
        self.tabs = ttk.Notebook(self.root, name="tabs")
        self.tabs.configure(height=0, takefocus=False, width=0)
        self.camera_tab_frame = ttk.Frame(self.tabs, name="camera_tab_frame")
        self.camera_tab_frame.configure(height=0, width=0)
        self.camera_id_label = ttk.Label(self.camera_tab_frame, name="camera_id_label")
        self.camera_id_label.configure(anchor="e", text="Camera ID:")
        self.camera_id_label.grid(column=0, padx=5, pady=5, row=0, sticky="ew")
        self.camera_id_entry = ttk.Entry(self.camera_tab_frame, name="camera_id_entry")
        self.camera_id_var = tk.IntVar(value=0)
        self.camera_id_entry.configure(
            justify="center", textvariable=self.camera_id_var, width=0
        )
        _text_ = "0"
        self.camera_id_entry.delete("0", "end")
        self.camera_id_entry.insert("0", _text_)
        self.camera_id_entry.grid(
            column=1, ipadx=20, padx=5, pady=5, row=0, sticky="ew"
        )
        self.camera_id_entry.bind("<KeyRelease>", self.assignCamera, add="")
        self.reload_camera_button = ttk.Button(
            self.camera_tab_frame, name="reload_camera_button"
        )
        self.reload_camera_button.configure(text="Reload Camera", width=0)
        self.reload_camera_button.grid(column=3, padx=5, pady=5, row=0, sticky="ew")
        self.reload_camera_button.configure(command=self.openCamera)
        self.show_tealtime_chk = ttk.Checkbutton(
            self.camera_tab_frame, name="show_tealtime_chk"
        )
        self.show_realtime_var = tk.BooleanVar()
        self.show_tealtime_chk.configure(
            offvalue=0,
            onvalue=1,
            text="Realtime view",
            variable=self.show_realtime_var,
            width=0,
        )
        self.show_tealtime_chk.grid(column=2, padx=5, pady=5, row=2, sticky="e")
        self.camera_name_combobox = ttk.Combobox(
            self.camera_tab_frame, name="camera_name_combobox"
        )
        self.camera_name_var = tk.StringVar()
        self.camera_name_combobox.configure(
            state="readonly", textvariable=self.camera_name_var
        )
        self.camera_name_combobox.grid(
            column=1, columnspan=3, padx=5, pady=5, row=1, sticky="ew"
        )
        self.camera_name_combobox.bind(
            "<<ComboboxSelected>>", self.set_cameraid, add=""
        )
        self.camera_name_label = ttk.Label(
            self.camera_tab_frame, name="camera_name_label"
        )
        self.camera_name_label.configure(anchor="e", text="Camera Name:")
        self.camera_name_label.grid(column=0, padx=5, pady=5, row=1, sticky="ew")
        self.show_fps_label = ttk.Label(self.camera_tab_frame, name="show_fps_label")
        self.show_fps_label.configure(anchor="e", text="Show FPS:")
        self.show_fps_label.grid(column=0, padx=5, pady=5, row=2, sticky="ew")
        self.camera_api_label = ttk.Label(
            self.camera_tab_frame, name="camera_api_label"
        )
        self.camera_api_label.configure(anchor="e", text="Camera API:")
        self.camera_api_label.grid(column=0, padx=5, pady=5, row=3, sticky="ew")
        self.camera_resolution_label = ttk.Label(
            self.camera_tab_frame, name="camera_resolution_label"
        )
        self.camera_resolution_label.configure(anchor="e", text="Resolution:")
        self.camera_resolution_label.grid(column=0, padx=5, pady=5, row=4, sticky="ew")
        self.fps_combobox = ttk.Combobox(self.camera_tab_frame, name="fps_combobox")
        self.fps_var = tk.StringVar()
        self.fps_combobox.configure(
            state="readonly",
            takefocus=False,
            textvariable=self.fps_var,
            values=["0", "5", "15", "30", "45", "60"],
            width=0,
        )
        self.fps_combobox.grid(column=1, padx=5, pady=5, row=2, sticky="ew")
        self.fps_combobox.bind("<<ComboboxSelected>>", self.applyFps, add="")
        self.camera_api_combobox = ttk.Combobox(
            self.camera_tab_frame, name="camera_api_combobox"
        )
        self.camera_api_combobox.configure(state="readonly")
        self.camera_api_combobox.grid(
            column=1, columnspan=3, padx=5, pady=5, row=3, sticky="ew"
        )
        self.camera_resolution_combobox = ttk.Combobox(
            self.camera_tab_frame, name="camera_resolution_combobox"
        )
        self.camera_resolution_combobox.configure(state="readonly")
        self.camera_resolution_combobox.grid(
            column=1, columnspan=3, padx=5, pady=5, row=4, sticky="ew"
        )
        self.camera_tab_frame.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        self.camera_tab_frame.grid_anchor("nw")
        self.camera_tab_frame.rowconfigure(0, weight=0)
        self.camera_tab_frame.columnconfigure(1, weight=1)
        self.camera_tab_frame.columnconfigure(2, weight=1)
        self.camera_tab_frame.columnconfigure(3, weight=1)
        self.tabs.add(
            self.camera_tab_frame,
            compound="center",
            state="normal",
            sticky="nsew",
            text="Camera",
        )
        self.serial_tab_frame = ttk.Frame(self.tabs, name="serial_tab_frame")
        self.serial_tab_frame.configure(height=0, width=0)
        self.com_port_label = ttk.Label(self.serial_tab_frame, name="com_port_label")
        self.com_port_label.configure(anchor="e", text="COM Port:")
        self.com_port_label.grid(
            column=0, padx=5, pady=5, row=0, rowspan=2, sticky="ew"
        )
        self.com_port_entry = ttk.Entry(self.serial_tab_frame, name="com_port_entry")
        self.com_port_var = tk.StringVar()
        self.com_port_entry.configure(textvariable=self.com_port_var)
        self.com_port_entry.grid(column=1, padx=5, pady=5, row=0, sticky="w")
        self.com_port_combobox = ttk.Combobox(
            self.serial_tab_frame, name="com_port_combobox"
        )
        self.com_name_var = tk.StringVar()
        self.com_port_combobox.grid(
            column=1, columnspan=3, padx=5, pady=5, row=1, sticky="ew"
        )
        self.com_port_combobox.bind("<<ComboboxSelected>>", self.set_serial_entry)
        self.baud_rate_label = ttk.Label(self.serial_tab_frame, name="baud_rate_label")
        self.baud_rate_label.configure(anchor="e", text="Baud Rate:")
        self.baud_rate_label.grid(column=0, row=2, sticky="ew")
        self.baud_rate_combobox = ttk.Combobox(
            self.serial_tab_frame, name="baud_rate_combobox"
        )
        self.baud_rate_var = tk.StringVar()
        self.baud_rate_combobox.configure(
            state="disabled",
            textvariable=self.baud_rate_var,
            values=["9600", "4800"],
        )
        self.baud_rate_combobox.grid(
            column=1, columnspan=1, padx=5, pady=5, row=2, sticky="w"
        )
        self.baud_rate_combobox.bind("<<ComboboxSelected>>", self.applyBaudRate, add="")
        self.show_serial_chk = ttk.Checkbutton(
            self.serial_tab_frame, name="show_serial_chk"
        )
        self.show_serial_var = tk.BooleanVar()
        self.show_serial_chk.configure(
            text="Show Serial", variable=self.show_serial_var
        )
        self.show_serial_chk.grid(column=2, padx=5, pady=5, row=2)
        self.serial_frame = ttk.Frame(self.serial_tab_frame, name="serial_frame")
        self.serial_frame.configure(height=0, width=0)
        self.scan_port_button = ttk.Button(self.serial_frame, name="scan_port_button")
        self.scan_port_button.configure(text="Scan", width=0)
        self.scan_port_button.grid(
            column=1, ipadx=3, ipady=3, padx=5, pady=5, row=0, sticky="ew"
        )
        # self.scan_port_button.configure(command=self.scan_port)
        self.reload_serial_button = ttk.Button(
            self.serial_frame, name="reload_serial_button"
        )
        self.reload_serial_button.configure(text="Connect", width=0)
        self.reload_serial_button.grid(
            column=2, ipadx=3, ipady=3, padx=5, pady=5, row=0, sticky="ew"
        )
        self.reload_serial_button.configure(command=self.activateSerial)
        self.disconnect_serial_button = ttk.Button(
            self.serial_frame, name="disconnect_serial_button"
        )
        self.disconnect_serial_button.configure(text="Disconnect", width=0)
        self.disconnect_serial_button.grid(
            column=3, ipadx=3, ipady=3, padx=5, pady=5, row=0, sticky="ew"
        )
        self.disconnect_serial_button.configure(command=self.inactivateSerial)
        self.serial_frame.grid(
            column=2, columnspan=4, padx=5, pady=5, row=0, sticky="e"
        )
        self.serial_tab_frame.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        self.serial_tab_frame.grid_anchor("nw")
        self.serial_tab_frame.columnconfigure(1, weight=1)
        self.serial_tab_frame.columnconfigure(2, weight=1)
        self.serial_tab_frame.columnconfigure(3, weight=1)
        self.tabs.add(
            self.serial_tab_frame, compound="center", sticky="nsew", text="Serial"
        )
        self.control_tab_frame = ttk.Frame(self.tabs, name="control_tab_frame")
        self.control_tab_frame.configure(height=0, width=0)
        self.open_gui_controller_button = ttk.Button(
            self.control_tab_frame, name="open_gui_controller_button"
        )
        self.open_gui_controller_button.configure(text="Open GUI Controller", width=0)
        self.open_gui_controller_button.grid(
            column=0, ipadx=3, ipady=3, padx=5, pady=5, row=0, sticky="w"
        )
        self.open_gui_controller_button.configure(command=self.createControllerWindow)
        self.show_gui_controller_always_chk = ttk.Checkbutton(
            self.control_tab_frame, name="show_gui_controller_always_chk"
        )
        self.always_show_gui_controller_var = tk.BooleanVar()
        self.show_gui_controller_always_chk.configure(
            state="disabled",
            text="Always Show GUI Controller",
            variable=self.always_show_gui_controller_var,
        )
        self.show_gui_controller_always_chk.grid(
            column=0, padx=5, pady=5, row=1, sticky="ew"
        )
        self.use_lstick_mouse_chk = ttk.Checkbutton(
            self.control_tab_frame,
            name="use_lstick_mouse_chk",
            command=self.activate_Left_stick_mouse,
        )
        self.left_stick_mouse_var = tk.BooleanVar()
        self.use_lstick_mouse_chk.configure(
            text="L-Stick Mouse Control", variable=self.left_stick_mouse_var
        )
        self.use_lstick_mouse_chk.grid(column=0, padx=5, pady=5, row=2, sticky="ew")
        self.use_keyboard_chk = ttk.Checkbutton(
            self.control_tab_frame, name="use_keyboard_chk"
        )
        self.use_keyboard_var = tk.BooleanVar()
        self.use_keyboard_chk.configure(
            text="Keyboaed Control", variable=self.use_keyboard_var
        )
        self.use_keyboard_chk.grid(column=1, padx=5, pady=5, row=1, sticky="w")
        self.use_rstick_mouse_chk = ttk.Checkbutton(
            self.control_tab_frame,
            name="use_rstick_mouse_chk",
            command=self.activate_Right_stick_mouse,
        )
        self.right_stick_mouse_var = tk.BooleanVar()
        self.use_rstick_mouse_chk.configure(
            text="R-Stick Mouse Control", variable=self.right_stick_mouse_var
        )
        self.use_rstick_mouse_chk.grid(column=1, padx=5, pady=5, row=2, sticky="w")
        self.control_tab_frame.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        self.control_tab_frame.grid_anchor("nw")
        self.control_tab_frame.rowconfigure(0, uniform="1")
        self.control_tab_frame.columnconfigure(0, uniform="1", weight=1)
        self.control_tab_frame.columnconfigure(1, uniform="1", weight=1)
        self.tabs.add(self.control_tab_frame, sticky="nsew", text="Control")
        self.command_frame = ttk.Frame(self.tabs)
        self.command_notebook: ttk.Notebook = ttk.Notebook(
            self.command_frame, name="command_notebook"
        )
        self.command_notebook.configure(height=0, width=0)
        self.python_command_tab_frame = ttk.Frame(
            self.command_notebook, name="python_command_tab_frame"
        )
        self.python_command_tab_frame.configure(height=0, width=0)
        self.python_command_scroll = ScrollbarHelper(
            self.python_command_tab_frame,
            scrolltype="both",
            name="python_command_scroll",
        )
        self.python_command_scroll.configure(height=3, usemousewheel=False, width=0)
        self.python_command_tree = FilterableTreeview(
            self.python_command_scroll.container, name="python_command_tree"
        )
        self.python_command_tree.configure(height=3, selectmode="browse")
        self.python_command_tree_cols = [
            "python_id_col",
            "python_name_col",
            "python_last_executed",
            "python_description_col",
            "python_author_col",
        ]
        self.python_command_tree_dcols = [
            "python_id_col",
            "python_name_col",
            "python_last_executed",
            "python_description_col",
            "python_author_col",
        ]
        self.python_command_tree.configure(
            columns=self.python_command_tree_cols,
            displaycolumns=self.python_command_tree_dcols,
        )
        self.python_command_tree.column(
            "#0", anchor="w", stretch=False, width=0, minwidth=0
        )
        self.python_command_tree.column(
            "python_id_col", anchor="w", stretch=False, width=40
        )
        self.python_command_tree.column(
            "python_name_col", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.python_command_tree.column(
            "python_last_executed", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.python_command_tree.column(
            "python_description_col", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.python_command_tree.column(
            "python_author_col", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.python_command_tree.heading("#0", anchor="w", text="")
        self.python_command_tree_heading("python_id_col", "ID")
        self.python_command_tree_heading("python_name_col", "Name")
        self.python_command_tree_heading("python_last_executed", "最終実行")
        self.python_command_tree_heading("python_description_col", "Description")
        self.python_command_tree_heading("python_author_col", "Author")
        self.python_command_tree.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        # Pythonコマンド選択時に選択IDを記憶
        self.python_command_tree.bind(
            "<<TreeviewSelect>>", self.on_python_selection, add=""
        )
        self.python_command_scroll.add_child(self.python_command_tree)
        self.python_command_scroll.grid(
            columnspan=5, padx=5, pady=5, row=0, sticky="nsew"
        )
        self.python_command_tab_frame.grid(
            column=0, padx=5, pady=5, row=0, sticky="nsew"
        )
        self.python_command_tab_frame.grid_anchor("nw")
        self.python_command_tab_frame.rowconfigure(0, weight=1)
        self.python_command_tab_frame.columnconfigure(0, weight=1)
        self.python_command_tab_frame.columnconfigure(1, uniform="0", weight=1)
        self.command_notebook.add(
            self.python_command_tab_frame, sticky="nsew", text="Python"
        )

        self.mcu_command_tab_frame = ttk.Frame(
            self.command_notebook, name="mcu_command_tab_frame"
        )
        self.mcu_command_tab_frame.configure(height=3, width=0)
        self.mcu_command_scroll = ScrollbarHelper(
            self.mcu_command_tab_frame, scrolltype="both", name="mcu_command_scroll"
        )
        self.mcu_command_scroll.configure(height=3, usemousewheel=False)
        self.mcu_command_tree = FilterableTreeview(
            self.mcu_command_scroll.container, name="mcu_command_tree"
        )
        self.mcu_command_tree.configure(height=3, selectmode="browse")
        self.mcu_command_tree_cols = [
            "mcu_id_col",
            "mcu_name_col",
            "mcu_description_col",
            "mcu_author_col",
        ]
        self.mcu_command_tree_dcols = [
            "mcu_id_col",
            "mcu_name_col",
            "mcu_description_col",
            "mcu_author_col",
        ]
        self.mcu_command_tree.configure(
            columns=self.mcu_command_tree_cols,
            displaycolumns=self.mcu_command_tree_dcols,
        )
        self.mcu_command_tree.column(
            "#0", anchor="w", stretch=False, width=0, minwidth=0
        )
        self.mcu_command_tree.column("mcu_id_col", anchor="w", stretch=False, width=40)
        self.mcu_command_tree.column(
            "mcu_name_col", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.mcu_command_tree.column(
            "mcu_description_col", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.mcu_command_tree.column(
            "mcu_author_col", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.mcu_command_tree.heading("#0", anchor="w", text="")
        self.mcu_command_tree.heading(
            "mcu_id_col",
            anchor="w",
            text="ID",
            command=lambda: self.sort_treeview(
                self.mcu_command_tree, "mcu_id_col", False
            ),
        )
        self.mcu_command_tree.heading(
            "mcu_name_col",
            anchor="w",
            text="Name",
            command=lambda: self.sort_treeview(
                self.mcu_command_tree, "mcu_name_col", False
            ),
        )
        self.mcu_command_tree.heading(
            "mcu_description_col",
            anchor="w",
            text="Description",
            command=lambda: self.sort_treeview(
                self.mcu_command_tree, "mcu_description_col", False
            ),
        )
        self.mcu_command_tree.heading(
            "mcu_author_col",
            anchor="w",
            text="Author",
            command=lambda: self.sort_treeview(
                self.mcu_command_tree, "mcu_author_col", False
            ),
        )
        self.mcu_command_tree.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        # MCUコマンド選択時に選択IDを記憶
        self.mcu_command_tree.bind("<<TreeviewSelect>>", self.on_mcu_selection, add="")
        self.mcu_command_scroll.add_child(self.mcu_command_tree)
        self.mcu_command_scroll.grid(columnspan=5, padx=5, pady=5, row=0, sticky="nsew")
        self.mcu_command_tab_frame.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        self.mcu_command_tab_frame.grid_anchor("nw")
        self.mcu_command_tab_frame.rowconfigure(0, weight=1)
        self.mcu_command_tab_frame.columnconfigure(0, weight=1)
        self.mcu_command_tab_frame.columnconfigure(1, uniform="0", weight=1)
        self.command_notebook.add(self.mcu_command_tab_frame, sticky="nsew", text="MCU")
        self.command_notebook.grid(
            column=0, columnspan=5, padx=5, pady=5, row=0, sticky="nsew"
        )
        # here

        self.recent_python_command_tab_frame = ttk.Frame(
            self.command_notebook, name="recent_python_command_tab_frame"
        )
        self.recent_python_command_tab_frame.configure(height=0, width=0)
        self.recent_python_command_scroll = ScrollbarHelper(
            self.recent_python_command_tab_frame,
            scrolltype="both",
            name="python_command_scroll",
        )
        self.recent_python_command_scroll.configure(
            height=3, usemousewheel=False, width=0
        )
        self.recent_python_command_tree = FilterableTreeview(
            self.recent_python_command_scroll.container,
            name="recent_python_command_tree",
        )
        self.recent_python_command_tree.configure(height=3, selectmode="browse")
        self.recent_python_command_tree_cols = [
            "recent_python_id_col",
            "recent_python_name_col",
            "recent_python_time_col",
        ]
        self.recent_python_command_tree_dcols = [
            "recent_python_id_col",
            "recent_python_name_col",
            "recent_python_time_col",
        ]
        self.recent_python_command_tree.configure(
            columns=self.recent_python_command_tree_cols,
            displaycolumns=self.recent_python_command_tree_dcols,
        )
        self.recent_python_command_tree.column(
            "#0", anchor="w", stretch=False, width=0, minwidth=0
        )
        self.recent_python_command_tree.column(
            "recent_python_id_col", anchor="w", stretch=False, width=40
        )
        self.recent_python_command_tree.column(
            "recent_python_name_col", anchor="w", stretch=True, width=0, minwidth=50
        )
        self.recent_python_command_tree.column(
            "recent_python_time_col",
            anchor="w",
            stretch=True,
            width=0,
            minwidth=50,
        )
        self.recent_python_command_tree.heading("#0", anchor="w", text="")
        self.recent_python_command_tree.heading(
            "recent_python_id_col",
            anchor="w",
            text="ID",
            command=lambda: self.sort_treeview(
                self.recent_python_command_tree, "recent_python_id_col", False
            ),
        )
        self.recent_python_command_tree.heading(
            "recent_python_name_col",
            anchor="w",
            text="Name",
            command=lambda: self.sort_treeview(
                self.recent_python_command_tree, "recent_python_name_col", False
            ),
        )
        self.recent_python_command_tree.heading(
            "recent_python_time_col",
            anchor="w",
            text="last use",
            command=lambda: self.sort_treeview(
                self.recent_python_command_tree, "recent_python_time_col", False
            ),
        )
        self.recent_python_command_tree.grid(
            column=0, padx=5, pady=5, row=0, sticky="nsew"
        )
        self.recent_python_command_tree.bind(
            "<<TreeviewSelect>>", self._on_recent_python_selection, add=""
        )
        self.recent_python_command_scroll.add_child(self.recent_python_command_tree)
        self.recent_python_command_scroll.grid(
            columnspan=5, padx=5, pady=5, row=0, sticky="nsew"
        )

        # クリアボタンを最近使用タブに追加
        clear_button_frame = ttk.Frame(self.recent_python_command_tab_frame)
        clear_button_frame.grid(column=0, padx=5, pady=5, row=1, sticky="ew")
        clear_button = ttk.Button(
            clear_button_frame, text="履歴をクリア", command=self.clear_recent_commands
        )
        clear_button.grid(column=0, padx=5, pady=5, row=0, sticky="w")

        self.recent_python_command_tab_frame.grid(
            column=0, padx=5, pady=5, row=0, sticky="nsew"
        )
        self.recent_python_command_tab_frame.grid_anchor("nw")
        self.recent_python_command_tab_frame.rowconfigure(0, weight=1)
        self.recent_python_command_tab_frame.columnconfigure(0, weight=1)
        self.recent_python_command_tab_frame.columnconfigure(1, uniform="0", weight=1)
        self.command_notebook.add(
            self.recent_python_command_tab_frame, sticky="nsew", text="履歴(Python)"
        )

        self.reload_commands_button = ttk.Button(
            self.command_frame, name="reload_button"
        )
        self.reload_commands_button.configure(text="Reload")
        self.reload_commands_button.grid(column=3, padx=5, pady=5, row=1, sticky="e")
        self.reload_commands_button.configure(command=self.reloadCommands)
        self.start_button = ttk.Button(self.command_frame, name="start_button")
        self.start_button.configure(text="Start")
        self.start_button.grid(column=4, padx=5, pady=5, row=1)
        self.start_button.configure(command=self.startPlay)
        self.open_command_dir_button = ttk.Button(
            self.command_frame, name="open_command_dir_button"
        )
        self.open_command_dir_button.configure(
            image=self.img_icons8OpenDir16, style="Toolbutton", width=5
        )
        self.open_command_dir_button.grid(column=2, padx=5, pady=5, row=1, sticky="e")
        self.open_command_dir_button.configure(command=self.OpenCommandDir)
        self.filter_entry = ttk.Entry(self.command_frame, name="filter_entry")
        self.filter_var = tk.StringVar(value="")
        self.filter_entry.configure(textvariable=self.filter_var)

        self.filter_entry.bind("<KeyPress-KP_Enter>", self.on_do_filter_command, add="")
        self.filter_entry.bind("<KeyPress-Return>", self.on_do_filter_command, add="")
        _text_ = ""
        self.filter_entry.delete("0", "end")
        self.filter_entry.insert("0", _text_)
        self.filter_entry.grid(column=0, padx=5, pady=5, row=1, sticky="ew")
        self.clear_filter_button = ttk.Button(
            self.command_frame,
            name="clear_filter_button",
            command=self.on_filter_remove,
        )
        self.clear_filter_button.configure(style="Toolbutton", text="✕", width=0)
        self.clear_filter_button.grid(column=1, padx=5, pady=5, row=1, sticky="w")
        self.command_frame.grid(column=0, row=0, sticky="nsew")
        self.command_frame.grid_anchor("n")
        self.command_frame.rowconfigure(0, weight=1)
        self.command_frame.columnconfigure(0, weight=1)
        self.tabs.add(self.command_frame, sticky="nsew", text="Command")
        self.tabs.grid(column=0, padx=5, pady=5, row=2, sticky="nsew")

    def _setup_log_area(self) -> None:
        self.panedwindow_1 = ttk.Panedwindow(self.root, orient="vertical")
        self.panedwindow_1.configure(height=0, width=600)
        self.scrollbarhelper_2 = ScrollbarHelper(self.panedwindow_1, scrolltype="both")
        self.scrollbarhelper_2.configure(usemousewheel=False)
        self.log_text_1 = tk.Text(self.scrollbarhelper_2.container, name="log_text_1")
        self.log_text_1.configure(
            height=0,
            takefocus=False,
            width=0,
            blockcursor=True,
            insertunfocussed="none",
            maxundo=0,
            # relief="flat",
            state="disabled",
            undo=False,
        )
        self.log_text_1.grid(
            padx=5,
            pady=5,
            sticky="nsew",
        )
        self.scrollbarhelper_2.add_child(self.log_text_1)
        self.scrollbarhelper_2.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        self.panedwindow_1.add(self.scrollbarhelper_2, weight=2)
        self.scrollbarhelper_1 = ScrollbarHelper(self.panedwindow_1, scrolltype="both")
        self.scrollbarhelper_1.configure(usemousewheel=False)
        self.log_text_2 = tk.Text(self.scrollbarhelper_1.container, name="log_text_2")
        self.log_text_2.configure(
            height=0,
            takefocus=False,
            width=0,
            blockcursor=True,
            insertunfocussed="none",
            maxundo=0,
            # relief="flat",
            state="disabled",
            undo=False,
        )
        self.log_text_2.grid(padx=5, pady=5, sticky="nsew")
        self.scrollbarhelper_1.add_child(self.log_text_2)
        self.scrollbarhelper_1.grid(column=0, padx=5, pady=5, row=0, sticky="nsew")
        self.panedwindow_1.add(self.scrollbarhelper_1, weight=1)
        self.panedwindow_1.grid(
            column=1, padx=5, pady=5, row=0, rowspan=4, sticky="nsew"
        )

    def openCamera(self) -> None:
        self.camera.openCamera(self.camera_id_var.get())

    def assignCamera(self, event: Any) -> None:
        if platform.system() != "Linux":
            self.camera_name_label.set(self.camera_dic[int(self.camera_id_var.get())])  # type: ignore

    def locateCameraCmbbox(self) -> None:
        def update_camera_combobox() -> None:
            self.camera_name_combobox["values"] = list(self.camera_dic.values())
            logger.debug(f"Camera list: {self.camera_name_combobox['values']}")

        def add_disable_option() -> None:
            next_id = str(max(map(int, self.camera_dic.keys())) + 1)
            self.camera_dic[next_id] = "Disable"

        system = platform.system()
        self.camera_dic = {}

        if system == "Windows":
            import clr

            clr.AddReference(r"..\DirectShowLib\DirectShowLib-2005")
            from DirectShowLib import DsDevice, FilterCategory

            # Get names of detected camera devices
            captureDevices = DsDevice.GetDevicesOfCat(FilterCategory.VideoInputDevice)
            self.camera_dic = {
                str(i): device.Name for i, device in enumerate(captureDevices)
            }

        elif system == "Darwin":
            cmd = 'system_profiler SPCameraDataType | grep "^    [^ ]" | sed "s/    //" | sed "s/://" '
            res = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
            # 出力結果の加工
            device_names = [
                name for name in res.stdout.decode("utf-8").split("\n") if name
            ]
            self.camera_dic = {str(i): name for i, name in enumerate(device_names)}

            # TODO: swift
        elif system == "Linux":
            try:  # 動作未確認
                # /dev/video* を列挙して確認
                video_devices = sorted(
                    [f for f in os.listdir("/dev") if f.startswith("video")],
                    key=lambda x: int(x.replace("video", "")),
                )
                self.camera_dic = {
                    str(i): f"/dev/{dev}" for i, dev in enumerate(video_devices)
                }
            except Exception:
                return None
        else:
            logger.debug("Unsupported OS for Camera detection")
            return None

        add_disable_option()
        update_camera_combobox()

        dev_count = len(self.camera_dic)
        current_id = self.camera_id_var.get()

        if current_id > dev_count - 1:
            logger.debug("Inappropriate camera ID! -> set to 0")
            print("Inappropriate camera ID! -> set to 0")
            self.camera_id_var.set(0)
            if dev_count == 0:
                logger.debug("No camera devices can be found.")
                print("No camera devices can be found.")
        #
        self.camera_id_entry.bind("<KeyRelease>", self.assignCamera)
        self.camera_name_combobox.current(self.camera_id_var.get())

    def saveCapture(self) -> None:
        self.camera.saveCapture()

    def OpenCaptureDir(self) -> None:
        directory = "Captures"
        logger.debug(f"Open folder: '{directory}'")
        if platform.system() == "Windows":
            subprocess.call(f'explorer "{directory}"')
        elif platform.system() == "Darwin":
            command = f'open "{directory}"'
            subprocess.run(command, shell=True)

    def set_cameraid(self, event: tk.Event | None = None) -> None:
        if not self.camera_dic:
            return
        keys = [
            k
            for k, v in self.camera_dic.items()
            if v == self.camera_name_combobox.get()
        ]
        try:
            ret: int = int(keys[0]) if keys else 0
        except (ValueError, IndexError):
            ret = 0
            logger.error(f"Invalid camera ID: {keys[0] if keys else 'None'}")
        self.camera_id_var.set(ret)

    def activateSerial(self) -> None:
        if self.baud_rate_var.get() == "4800":
            ret = tkmsg.askquestion(
                "確認",
                "Baud Rateを4800にすると動かなくなる可能性があります。\n変更しますか？",
            )
            if ret != "yes":
                self.baud_rate_combobox.set(value=9600)
                return
        if self.ser.isOpened():
            print("Port is already opened and being closed.")
            self.ser.closeSerial()
            self.keyPress = None
            self.activateSerial()
        else:
            if self.ser.openSerial(
                portNum=self.com_port_var.get(),
                baudrate=self.baud_rate_var.get(),
            ):
                print(
                    "COM Port "
                    + str(self.com_port_var.get())
                    + " connected successfully"
                )
                logger.debug(
                    "COM Port "
                    + str(self.com_port_var.get())
                    + " connected successfully"
                )
                self.keyPress = KeyPress(self.ser)
                self.settings.com_port.set(self.com_port_var.get())
                self.settings.baud_rate.set(self.baud_rate_var.get())
                self.settings.save()

    def inactivateSerial(self) -> None:
        if self.ser.isOpened():
            print("Port is already opened and being closed.")
            self.ser.closeSerial()
            self.keyPress = None

    def set_serial_entry(self, event: tk.Event) -> None:
        ...
        _var = self.com_port_combobox.get()
        self.com_port_var.set(_var[3:])

    def activateKeyboard(self) -> None:
        system = platform.system()

        if self.use_keyboard_var.get():
            # enable Keyboard as controller
            if self.keyboard is None:
                self.keyboard = SwitchKeyboardController(self.keyPress)
                self.keyboard.listen()

            # bind focus
            if system != "Darwin":
                self.root.bind("<FocusIn>", self.onFocusInController)
                self.root.bind("<FocusOut>", self.onFocusOutController)
                self.filter_entry.bind("<FocusIn>", self.on_focus_in_filter_entry)
                self.filter_entry.bind("<FocusOut>", self.on_focus_out_filter_entry)

        else:
            # stop listening to keyboard events
            if self.keyboard is not None:
                self.keyboard.stop()
                self.keyboard = None

            if system != "Darwin":
                self.root.bind("<FocusIn>", lambda _: None)
                self.root.bind("<FocusOut>", lambda _: None)

    def onFocusInController(self, event: Any) -> None:
        # enable Keyboard as controller
        if event.widget == self.root and self.keyboard is None:
            self.keyboard = SwitchKeyboardController(self.keyPress)
            self.keyboard.listen()
            return

    def onFocusOutController(self, event: Any) -> None:
        # stop listening to keyboard events
        if event.widget == self.root and self.keyboard:
            self.keyboard.stop()
            self.keyboard = None
            return

    def on_focus_in_filter_entry(self, event: Any) -> None:
        # stop listening to keyboard events
        if event.widget == self.filter_entry and self.keyboard:
            self.keyboard.stop()
            self.keyboard = None
            return

    def on_focus_out_filter_entry(self, event: Any) -> None:
        # enable Keyboard as controller
        if event.widget == self.filter_entry and self.keyboard is None:
            self.keyboard = SwitchKeyboardController(self.keyPress)
            self.keyboard.listen()
            return

    def loadCommands(self) -> None:
        self.py_loader = CommandLoader(
            util.ospath("Commands/PythonCommands"), PythonCommandBase.PythonCommand
        )  # コマンドの読み込み
        self.mcu_loader = CommandLoader(
            util.ospath("Commands/McuCommands"), McuCommandBase.McuCommand
        )
        self.py_classes = self.py_loader.load()
        self.mcu_classes = self.mcu_loader.load()

        # 最近使用したコマンド履歴を読み込む
        self._load_recent_commands()

        self.setCommandItems()
        self.assignCommand()

    def setCommandItems(self) -> None:
        for row in self.python_command_tree.get_children():
            self.python_command_tree.delete(row)
        for row in self.mcu_command_tree.get_children():
            self.mcu_command_tree.delete(row)

        # ハッシュから最終実行時間のマップを作成
        hash_to_last_executed = {}
        recent_commands = self.settings.get_recent_commands()
        for key, cmd_info in recent_commands.items():
            if cmd_info.hash:
                hash_to_last_executed[cmd_info.hash] = cmd_info.last_executed

        # Pythonコマンドツリーに項目を追加
        for idx, cmd_class in enumerate(self.py_classes):
            cmd_hash = self.py_loader.class_hashes.get(cmd_class, "")
            last_executed = ""
            if cmd_hash and cmd_hash in hash_to_last_executed:
                last_executed = hash_to_last_executed[cmd_hash].strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            self.python_command_tree.insert(
                parent="",
                index="end",
                iid=idx,
                text=cmd_class.NAME,
                values=(
                    idx,
                    cmd_class.NAME,
                    last_executed,  # 最終実行時間
                ),
                tags=[cmd_hash] if cmd_hash else [],
            )
        if self.py_classes:
            self.python_command_tree.selection_set(0)
            self.selected_py_id = 0

        # MCUコマンドツリーに項目を追加
        for idx, cmd_class in enumerate(self.mcu_classes):
            self.mcu_command_tree.insert(
                parent="",
                index="end",
                iid=idx,
                values=(
                    idx,
                    cmd_class.NAME,
                ),
            )
        if self.mcu_classes:
            self.mcu_command_tree.selection_set(0)
            self.selected_mcu_id = 0

    def assignCommand(self) -> None:
        """
        ユーザーが選択したコマンドを割り当てるメソッド
        MCUとPythonのコマンドを処理し、選択されたタブに応じて現在のコマンドを設定する
        """
        try:
            # self._assign_mcu_command()
            # self._assign_python_command()
            self._set_current_command()

            logger.debug(f"コマンド割り当て完了: {self.cur_command.__class__.__name__}")
        except IndexError as e:
            logger.error(f"コマンド割り当て時のインデックスエラー: {e}")
        except Exception as e:
            logger.error(f"コマンド割り当て中に予期しないエラーが発生: {e}")
            logger.error(traceback.format_exc())

    def reloadCommands(self) -> None:
        self.on_filter_remove()
        # 現在選択されているコマンドIDを保持
        selected_mcu_id = (
            self.mcu_command_tree.selection()[0]
            if self.mcu_command_tree.selection()
            else ""
        )
        selected_py_id = (
            self.python_command_tree.selection()[0]
            if self.python_command_tree.selection()
            else ""
        )

        # コマンドをリロード
        self.py_classes = self.py_loader.reload()
        self.mcu_classes = self.mcu_loader.reload()

        # 最近使用したコマンドリストから、新しいpython commandのリストに存在しないものを削除
        self._clean_invalid_recent_commands()

        # Restore the command selecting state if possible
        self.setCommandItems()
        # Clear and repopulate treeviews
        self.python_command_tree.delete(*self.python_command_tree.get_children())
        self.mcu_command_tree.delete(*self.mcu_command_tree.get_children())
        self.setCommandItems()

        # Restore selections if possible
        if selected_mcu_id:
            self.mcu_command_tree.selection_set(selected_mcu_id)
        if selected_py_id:
            self.python_command_tree.selection_set(selected_py_id)
        self.assignCommand()
        print("Finished reloading command modules.")
        logger.info("Reloaded commands.")

    def startPlay(self, *event: Any) -> None:
        if self.cur_command is None:
            print("No commands have been assigned yet.")
            logger.info("No commands have been assigned yet.")
            return

        # set and init selected command
        self.assignCommand()

        print(self.start_button["text"] + " " + self.cur_command.NAME)
        logger.info(self.start_button["text"] + " " + self.cur_command.NAME)

        self.cur_command.start(self.ser, self.stopPlayPost)

        # コマンドの実行情報を記録
        if self.command_notebook.index("current") == 0:  # type:ignore
            self._add_recent_treeview_command()

            # python_command_treeのlast_execute列に現在時刻を追加
            current_time = datetime.now()
            selected_item = self.python_command_tree.selection()
            if selected_item:
                # 既存の値を取得
                current_values = self.python_command_tree.item(selected_item[0])[
                    "values"
                ]
                new_values = list(current_values)

                # 最終実行時間を設定（インデックス2の位置）
                if len(new_values) > 2:
                    new_values[2] = current_time.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    new_values.append(current_time.strftime("%Y-%m-%d %H:%M:%S"))

                # 項目を更新
                self.python_command_tree.item(
                    selected_item[0], values=tuple(new_values)
                )

        self.start_button["text"] = "Stop"
        self.start_button["command"] = self.stopPlay
        self.reload_commands_button["state"] = "disabled"

    def stopPlay(self) -> None:
        print(self.start_button["text"] + " " + self.cur_command.NAME)
        logger.info(self.start_button["text"] + " " + self.cur_command.NAME)
        self.start_button["state"] = "disabled"
        self.cur_command.end(self.ser)

    def stopPlayPost(self) -> None:
        self.start_button["text"] = "Start"
        self.start_button["command"] = self.startPlay
        self.start_button["state"] = "normal"
        self.reload_commands_button["state"] = "normal"

    def ReloadCommandWithF5(self, *event: Any) -> None:
        self.reloadCommands()

    def StartCommandWithF6(self, *event: Any) -> None:
        if self.start_button["text"] == "Stop":
            print("Command is now working!")
            logger.debug("Command is now working!")
        elif self.start_button["text"] == "Start":
            self.startPlay()

    def StopCommandWithEsc(self, *event: Any) -> None:
        if self.start_button["text"] == "Stop":
            self.stopPlay()

    def on_python_selection(self, event: tk.Event | None = None) -> None:
        sel = self.python_command_tree.selection()
        self.selected_py_id = int(sel[0]) if sel else None

    def on_mcu_selection(self, event: tk.Event | None = None) -> None:
        sel = self.mcu_command_tree.selection()
        self.selected_mcu_id = int(sel[0]) if sel else None

    def _on_recent_python_selection(self, event: tk.Event | None = None) -> None:
        """最近使用したコマンドが選択されたときの処理"""
        sel = self.recent_python_command_tree.selection()
        if not sel:
            self.selected_recent_py_id = None
            return

        # 選択されたアイテムのIDを取得
        selected_item = self.recent_python_command_tree.item(sel[0])
        selected_id = selected_item["values"][0]  # ID列の値を取得

        # 対応するPythonコマンドを選択する
        # self.command_notebook.select(0)  # Pythonタブを選択
        self.python_command_tree.selection_set(str(selected_id))
        self.python_command_tree.see(str(selected_id))
        self.selected_py_id = selected_id

    def _setup_custom_print(self) -> None:
        """カスタムのprint関数をセットアップする"""
        # オリジナルのprint関数を保存
        self._original_print = builtins.print

        # カスタムprint関数
        def custom_print(*args: Any, **kwargs: Any) -> None:
            if "file" in kwargs.keys():
                self._original_print(*args, **kwargs)
                return
            # 文字列に変換して結合
            output = " ".join(str(arg) for arg in args)
            output += "\n"

            # fileパラメータを取得し、削除（デフォルトのprint関数に渡さないため）
            output_target = kwargs.pop("output_target", 0)

            # output_targetに基づいて出力先を決定
            if output_target == 0:
                # log_text_1に出力
                text_queue_1.put(output)
            elif output_target == 1:
                # log_text_2に出力
                text_queue_2.put(output)
            else:
                # その他の場合は元のprint関数を使用
                self._original_print(*args, **kwargs)

        # グローバルのprint関数を置き換え
        builtins.print = custom_print  # type: ignore

    def display_text(self, widget: tk.Text) -> None:
        w = widget
        buf: list[str] = []
        queue_to_use = text_queue_1 if w == self.log_text_1 else text_queue_2

        for _ in range(60):
            try:
                buf.append(queue_to_use.get_nowait())
            except queue.Empty:
                break

        if buf:
            text = "".join(buf)
            w.config(state="normal")
            w.insert("end", text)
            w.see("end")
            w.config(state="disabled")

        interval = int(1000 / DEFAULT_FPS)
        w.after(interval, lambda: self.display_text(w))

    def clear_log(self) -> None:
        try:
            self.log_text_1.configure(state="normal")
            self.log_text_2.configure(state="normal")
            self.log_text_1.delete("1.0", tk.END)
            self.log_text_2.delete("1.0", tk.END)
            self.log_text_1.configure(state="disabled")
            self.log_text_2.configure(state="disabled")
        except (AttributeError, tk.TclError) as e:
            # ロガーを使用してエラー詳細を記録
            logger.error(f"ログテキストのクリアに失敗しました: {str(e)}")

    def loadSettings(self) -> None:
        self.settings = Settings.GuiSettings()
        self.settings.load()

    def apply_settings_to_ui(self) -> None:
        """GuiSettingsの値をUI側のバインディング変数に反映"""
        var_map = {
            self.show_realtime_var: self.settings.is_show_realtime,
            self.show_serial_var: self.settings.is_show_serial,
            self.use_keyboard_var: self.settings.is_use_keyboard,
            self.fps_var: self.settings.fps,
            self.show_size_var: self.settings.show_size,
            self.com_port_var: self.settings.com_port,
            self.baud_rate_var: self.settings.baud_rate,
            self.camera_id_var: self.settings.camera_id,
        }

        for ui_var, setting_var in var_map.items():
            ui_var.set(setting_var.get())

    def apply_ui_to_settings(self) -> None:
        """UIのバインディング変数の値をGuiSettingsに反映"""
        var_pairs = [
            (self.settings.is_show_realtime, self.show_realtime_var),
            (self.settings.is_show_serial, self.show_serial_var),
            (self.settings.is_use_keyboard, self.use_keyboard_var),
            (self.settings.fps, self.fps_var),
            (self.settings.show_size, self.show_size_var),
            (self.settings.com_port, self.com_port_var),
            (self.settings.baud_rate, self.baud_rate_var),
            (self.settings.camera_id, self.camera_id_var),
        ]

        for setting_var, ui_var in var_pairs:
            setting_var.set(ui_var.get())  # type:ignore

    def _load_recent_commands(self) -> None:
        """最近使用したコマンド情報を読み込み、ツリービューを更新"""
        # 直接ツリービューの更新処理を呼ぶだけ
        self._update_recent_treeview()
        logger.debug("Recent commands loaded")

    def _save_tab_state(self) -> None:
        """現在のタブ状態を保存"""
        try:
            # メインのタブとコマンドタブの状態を保存
            main_tab = self.tabs.index(self.tabs.select())  # type:ignore
            command_tab = self.command_notebook.index(  # type:ignore
                self.command_notebook.select()
            )

            self.settings.save_tab_state(main_tab, command_tab)
        except Exception as e:
            logger.error(f"Failed to save tab state: {e}")

    def _load_tab_state(self) -> None:
        """保存されたタブ状態を読み込む"""
        try:
            tab_state = self.settings.get_tab_state()

            # メインタブの状態を復元
            if "main_tab" in tab_state:
                self.tabs.select(tab_state["main_tab"])

            # コマンドタブの状態を復元
            if "command_tab" in tab_state:
                self.command_notebook.select(tab_state["command_tab"])
        except Exception as e:
            logger.error(f"Failed to load tab state: {e}")

    def on_do_filter_command(self, event: tk.Event | None = None) -> None:
        self.python_command_tree.filter_by(self.filter_var.get())

    def on_filter_remove(self, event: tk.Event | None = None) -> None:
        self.filter_var.set("")
        self.python_command_tree.filter_remove()

    def OpenCommandDir(self) -> None:
        if self.command_notebook.index("current") in [0, 2]:  # type: ignore
            directory = os.path.join("Commands", "PythonCommands")
        else:
            directory = os.path.join("Commands", "McuCommands")
        logger.debug(f"Open folder: '{directory}'")
        if platform.system() == "Windows":
            subprocess.call(f'explorer "{directory}"')
        elif platform.system() == "Darwin":
            command = f'open "{directory}"'
            subprocess.run(command, shell=True)

    def applyFps(self, event: Optional[tk.Event] = None) -> None:
        print("changed FPS to: " + self.fps_var.get() + " [fps]")
        self.preview.setFps(self.fps_var.get())

    def applyBaudRate(self, event: Any = None) -> None:
        # 未実装
        pass

    def applyWindowSize(self, event: Optional[tk.Event] = None) -> None:
        width, height = map(int, self.show_size_var.get().split("x"))
        self.preview.setShowsize(height, width)
        if self.show_size_tmp != self.show_size_combobox["values"].index(
            self.show_size_combobox.get()
        ):
            ret = tkmsg.askokcancel("確認", "この画面サイズに変更しますか？")
        else:
            return

        if ret:
            self.show_size_tmp = self.show_size_combobox["values"].index(
                self.show_size_combobox.get()
            )
        else:
            self.show_size_combobox.current(self.show_size_tmp)
            width_bef, height_bef = map(int, self.show_size_var.get().split("x"))
            self.preview.setShowsize(height_bef, width_bef)
            self.root.geometry("")
            # self.show_size_tmp = self.show_size_cb['values'].index(self.show_size_cb.get())

    def createControllerWindow(self) -> None:
        if isinstance(self.controller, ControllerGUI):
            self.controller.focus_force()
            return

        window = ControllerGUI(self.root, self.ser)
        window.protocol("WM_DELETE_WINDOW", self.closingController)
        self.controller = window

    def activate_Left_stick_mouse(self) -> None:
        self.preview.ApplyLStickMouse()

    def activate_Right_stick_mouse(self) -> None:
        self.preview.ApplyRStickMouse()

    def _assign_mcu_command(self) -> None:
        """MCUコマンドを割り当てるヘルパーメソッド"""
        if self.selected_mcu_id is None:
            logger.warning("MCUコマンドが選択されていません")
            return
        # ユーザー選択時に記憶したIDを使用
        self.mcu_cur_command = self.mcu_classes[self.selected_mcu_id]()

    def _assign_python_command(self) -> None:
        """Pythonコマンドを割り当てるヘルパーメソッド"""
        if self.selected_py_id is None:
            logger.warning("Pythonコマンドが選択されていません")
            return
        # ユーザー選択時に記憶したIDを使用
        cmd_class = self.py_classes[self.selected_py_id]
        # logger.info(
        #     self.python_command_tree.item(self.python_command_tree.selection(), "tags")
        # )

        if not issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
            # 通常のPythonコマンド
            self.py_cur_command = cmd_class()
            return

        # ImageProcPythonCommandの場合は引数が異なる
        try:
            # 新しいスタイル: カメラとプレビューを渡す
            self.py_cur_command = cmd_class(self.camera, self.preview)
        except TypeError:
            # 古いスタイル: カメラのみ
            self.py_cur_command = cmd_class(self.camera)
            logger.info(
                f"旧スタイルのImageProcPythonCommandを使用: {cmd_class.__name__}"
            )
        except Exception as e:
            logger.warning(f"コマンドの初期化エラー: {e}")
            # フォールバック
            self.py_cur_command = cmd_class(self.camera)

    def _set_current_command(self) -> None:
        """コマンドの設定を行う

        タブの選択状態またはselected_py_id/selected_mcu_idに基づいて
        適切なコマンドを設定する
        """
        # 最近使用タブを含むタブインデックスを取得
        current_tab_index = self.command_notebook.index(self.command_notebook.select())  # type: ignore

        # タブによって処理を変える（2はRecentタブ）
        if current_tab_index == 2:  # 最近使用タブの場合
            # 選択されたPythonコマンドIDがあれば、それを使用
            if self.selected_py_id is not None:
                self._assign_python_command()
                self.cur_command = self.py_cur_command
            else:
                logger.warning("最近使用タブでコマンドが選択されていません")
                self.cur_command = None
        elif current_tab_index == 0:  # Pythonコマンドタブの場合
            # Python IDがあれば設定
            if self.selected_py_id is not None:
                self._assign_python_command()
                self.cur_command = self.py_cur_command
            else:
                logger.warning("Pythonコマンドが選択されていません")
                self.cur_command = None
        else:  # MCUコマンドタブの場合
            # MCU IDがあれば設定
            if self.selected_mcu_id is not None:
                self._assign_mcu_command()
                self.cur_command = self.mcu_cur_command
            else:
                logger.warning("MCUコマンドが選択されていません")
                self.cur_command = None

    def _clean_invalid_recent_commands(self) -> None:
        """
        最近使用したコマンドリストから、現在のPythonコマンドリストに存在しないものを削除する
        """
        # 現在この機能はSettingsクラスに統合されています
        pass

    def closingController(self) -> None:
        if self.controller is not None:
            self.controller.destroy()
            self.controller = None

    # ツリー選択時にIDを保持するハンドラー
    def clear_recent_commands(self) -> None:
        """最近使用したコマンド履歴をクリアする"""
        # 確認ダイアログを表示
        ret = tkmsg.askyesno("確認", "最近使用したコマンド履歴をクリアしますか？")
        if not ret:
            return

        # 設定クラスに履歴クリアを依頼
        self.settings.clear_recent_commands()

        # ツリービューをクリア
        for item in self.recent_python_command_tree.get_children():
            self.recent_python_command_tree.delete(item)

        print("Command history cleared.")

    def _add_recent_treeview_command(self) -> None:
        """選択されたPythonコマンドを最近使用したコマンドのツリービューに追加する"""
        if self.selected_py_id is None:
            return

        # 選択されたアイテムの情報を取得
        selected_item = self.python_command_tree.item(str(self.selected_py_id))
        command_name = selected_item["values"][1]  # Name列の値を取得

        # コマンドクラスのファイルパスを取得
        file_path = self.py_classes[self.selected_py_id].__module__

        # ハッシュ値を取得
        cmd_class = self.py_classes[self.selected_py_id]
        cmd_hash = self.py_loader.class_hashes.get(cmd_class, "")

        # コマンド情報を作成
        command_info = Settings.RecentCommandInfo(
            id=self.selected_py_id,
            name=command_name,
            last_executed=datetime.now(),
            file_path=file_path,
            hash=cmd_hash,
        )

        # 設定クラスに保存
        self.settings.add_recent_command(command_info)

        # ツリービューを更新
        self._update_recent_treeview()

        # Pythonコマンドのlast_executed列も更新
        self._update_python_command_last_executed()

    def _update_recent_treeview(self) -> None:
        """最近使用したコマンドのツリービューを更新する"""
        # ツリービューをクリア
        for item in self.recent_python_command_tree.get_children():
            self.recent_python_command_tree.delete(item)

        # 最新の使用順で並べ替えられたコマンドリストを取得
        recent_commands = self.settings.get_sorted_recent_commands(MAX_RECENT_COMMANDS)

        # ツリービューに追加
        for cmd_info in recent_commands:
            self.recent_python_command_tree.insert(
                "",
                "end",
                cmd_info.hash
                or f"{cmd_info.id}_{cmd_info.name}",  # hash値またはID+名前をiidとして使用
                values=(
                    cmd_info.id,
                    cmd_info.name,
                    cmd_info.last_executed.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )

    def python_command_tree_heading(self, column: str, text: str) -> None:
        """Treeviewのカラムヘッダーを設定し、クリックイベントをバインドする"""
        self.python_command_tree.heading(
            column,
            text=text,
            command=lambda col=column: self.sort_treeview(
                self.python_command_tree, col, False
            ),
        )

    def sort_treeview(
        self, treeview: FilterableTreeview, column: str, reverse: bool
    ) -> None:
        """Treeviewの指定されたカラムでソートする"""
        # 現在の項目をすべて取得
        data = [
            (treeview.set(item, column), item) for item in treeview.get_children("")
        ]

        # ソート方向を決定
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = reverse

        # ソート（IDカラムは数値としてソート、それ以外は文字列としてソート）
        if column.endswith("id_col"):
            # IDの場合は数値ソート
            data.sort(
                key=lambda x: int(x[0]) if x[0].isdigit() else 0,
                reverse=self.sort_reverse,
            )
        elif column.endswith("last_executed"):
            # 日付の場合は日付ソート（空白の場合は最も古い日付とする）
            def parse_date(date_str: str) -> datetime:
                try:
                    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    return datetime.min

            data.sort(key=lambda x: parse_date(x[0]), reverse=self.sort_reverse)
        else:
            # 通常のテキストソート
            data.sort(reverse=self.sort_reverse)

        # ソート後の順番でTreeviewを再構成
        for index, (val, item) in enumerate(data):
            treeview.move(item, "", index)

        # ソート状態を視覚的に表示（昇順/降順の矢印を追加）
        for col in treeview["columns"]:
            if col == column:
                text = treeview.heading(col)["text"].replace(" ▼", "").replace(" ▲", "")

                direction = "▼" if self.sort_reverse else "▲"
                treeview.heading(col, text=f"{text} {direction}")
            else:
                # 他のカラムからソート表示を削除
                text = treeview.heading(col)["text"]
                if "▼" in text:
                    text = text.replace(" ▼", "")
                elif "▲" in text:
                    text = text.replace(" ▲", "")
                treeview.heading(col, text=text)

    def _update_python_command_last_executed(self) -> None:
        """Pythonコマンドツリービューの最終実行時間列を更新する"""
        # 最近使用したコマンドを取得
        recent_commands = self.settings.get_recent_commands()

        for idx, cmd_class in enumerate(self.py_classes):
            cmd_hash = self.py_loader.class_hashes.get(cmd_class, "")
            if not cmd_hash:
                continue

            # ハッシュに基づいて最終実行時間を取得
            for key, cmd_info in recent_commands.items():
                if cmd_info.hash == cmd_hash:
                    # 最終実行時間をツリービュー項目に更新
                    current_values = self.python_command_tree.item(str(idx))["values"]

                    # 値が少なくとも2つある場合のみ処理（ID, 名前は最低限必要）
                    if len(current_values) >= 2:
                        # 既存の値のコピーを作成
                        new_values = list(current_values)

                        # 最終実行時間を設定（インデックス2の位置）
                        if len(new_values) > 2:
                            new_values[2] = cmd_info.last_executed.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )
                        else:
                            # 項目が足りない場合は追加
                            new_values.append(
                                cmd_info.last_executed.strftime("%Y-%m-%d %H:%M:%S")
                            )

                        # ツリービュー項目を更新
                        self.python_command_tree.item(
                            str(idx), values=tuple(new_values)
                        )
                    break

    def run(self) -> None:
        logger.debug("Start Poke-Controller")
        self.mainwindow.mainloop()

    def exit(self) -> None:
        ret = tkmsg.askyesno("確認", "Poke Controllerを終了しますか？")
        if ret:
            if self.ser.isOpened():
                self.ser.closeSerial()
                print("Serial disconnected")
                # logger.info("Serial disconnected")

            # stop listening to keyboard events
            if isinstance(self.keyboard, SwitchKeyboardController):
                self.keyboard.stop()
                self.keyboard = None

            # save settings
            self.apply_ui_to_settings()
            self.settings.save()

            # タブの状態を保存
            self._save_tab_state()  # この行を追加

            self.camera.destroy()
            cv2.destroyAllWindows()
            logger.debug("Stop Poke Controller")
            self.root.destroy()


class QueuePrintRedirector(object):
    """
    高速な標準出力リダイレクト用クラス
    バッファに出力を蓄積し、一定間隔でまとめてテキストウィジェットに反映します。
    """

    def __init__(self, text_widget: tk.Text, flush_interval: int = 1000 // 60):
        self.text_widget = text_widget
        self.buffer: queue.Queue = (
            text_queue_1 if text_widget.winfo_name() == "log_text_1" else text_queue_2
        )

    def write(self, string: str) -> None:
        self.buffer.put(string)

    def flush(self) -> None:
        pass


# class StdoutRedirector(object):
#     """
#     標準出力をtextウィジェットにリダイレクトするクラス
#     重いので止めました →# update_idletasks()で出力のたびに随時更新(従来はfor loopのときなどにまとめて出力されることがあった)
#     """

#     def __init__(self, text_widget: tk.Text) -> None:
#         self.text_space = text_widget

#     def write(self, string: str) -> None:
#         self.text_space.configure(state="normal")
#         self.text_space.insert("end", string)
#         self.text_space.see("end")
#         # self.text_space.update_idletasks()
#         self.text_space.configure(state="disabled")

#     def flush(self) -> None:
#         pass


if __name__ == "__main__":
    # もし実行階層でlsした結果にSerialControllerフォルダがある場合はそこに移動する
    if "SerialController" in os.listdir():
        os.chdir("SerialController")

    # Todo: プロファイル機能(引数により異なる設定ファイルを利用する)を追加する
    logger = PokeConLogger.root_logger()
    # logger = logger
    logger.info("The root logger is created.")

    text_queue_1: queue.Queue = queue.Queue()
    text_queue_2: queue.Queue = queue.Queue()

    root = Tk()
    try:
        app = PokeControllerApp(root)
        app.run()
    except Exception:
        logger.error(traceback.format_exc())
