# import argparse
# import time
from __future__ import annotations

import os
import platform
import queue
import subprocess
import sys
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
import traceback
from logging import DEBUG, NullHandler, StreamHandler, getLogger  # noqa: F401
from tkinter import Misc, Tk
from typing import Any, Optional, Protocol  # noqa: F401

import cv2
import PokeConLogger
import Settings
import tomllib
import Utility as util
from Camera import Camera  # , CameraQueue
from CommandLoader import CommandLoader
from Commands import McuCommandBase, PythonCommandBase, Sender
from Commands.Keys import KeyPress
from GuiAssets import CaptureArea, ControllerGUI
from Keyboard import SwitchKeyboardController
from loguru import logger
from Menubar import PokeController_Menubar
# from customtkinter import CTkComboBox

# import threading
from pygubu.widgets.scrollbarhelper import ScrollbarHelper

# from get_pokestatistics import GetFromHomeGUI


def get_version() -> Any:
    with open("pyproject.toml", "rb") as f:
        pyp = tomllib.load(f)
    return pyp["project"]["version"]


NAME = "Poke-Controller Modified"
VERSION = f"v{str(get_version())}"  # based on 1.0-beta3(custom by @dragonite303)
DEFAULT_FPS = 60
DEFAULT_WINDOW_SIZE = "640x360"
CAPTURE_DIR = "Captures"


"""
Todo:
- デバッグ用にPoke-Controller本体にコントローラーを接続して動かしたい
- 画像認識の時の枠を設定でON/OFFできると嬉しい
"""


class LabelframeWithStickVar(ttk.Labelframe):
    def __init__(self, master: Misc | None = None, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.left_stick_mouse_var: tk.BooleanVar = tk.BooleanVar()
        self.right_stick_mouse_var: tk.BooleanVar = tk.BooleanVar()


# Legacy Poke-Controller
class PokeControllerApp:
    def __init__(self, master: Tk | None = None) -> None:
        """PokeControllerアプリケーションの初期化を行う"""
        self._initialize_root_window(master)
        self._setup_instance_variables()
        self._initialize_ui()
        self._setup_initial_configurations()

    def _initialize_root_window(self, master: Tk | None) -> None:
        """ルートウィンドウの初期化処理"""
        if master is None:
            master = Tk()
        self.root = master
        self.root.title(f"{NAME} {VERSION}")
        # self.root.resizable(0, 0)

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

    def _setup_initial_configurations(self) -> None:
        """初期設定の適用"""

        # 仮置フレームを削除
        # self.camera.destroy()

        # 標準出力をログにリダイレクト(旧式も残しておく)
        sys.stdout = QueueStdoutRedirector(self.log_text_area)
        self.log_text_area.after(100, self.display_text)
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

        if platform.system() != "Linux":
            try:
                self.locateCameraCmbbox()
                self.camera_id_entry.config(state="disable")
            except Exception as e:
                logger.error(f"An error occurred: {e}")
                logger.error(traceback.format_exc())
                # Locate an entry instead whenever dll is not imported successfully
                self.camera_name_var.set(
                    "An error occurred when displaying the camera name in the Win/Mac "
                    "environment."
                )
                logger.warning(
                    "An error occurred when displaying the camera name in the Win/Mac environment."
                )
                self.camera_name_combobox.config(state="disable")
        elif platform.system() != "Linux":
            self.camera_name_var.set(
                "Linux environment. So that cannot show Camera name."
            )
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
            self.camera,
            self.fps_var.get(),
            self.show_realtime_var,
            self.ser,
            self.camera_frame,
            *list(map(int, self.show_size_var.get().split("x"))),
        )
        self.preview.config(cursor="crosshair")
        self.preview.grid(column=0, columnspan=7, row=2, padx=5, pady=5, sticky=tk.NSEW)
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
        self.mainwindow = self.main_frame

        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.preview.startCapture()

        self.menu = PokeController_Menubar(self)
        self.root.config(menu=self.menu)

        # logging.debug(f'python version: {sys.version}')

    def _initialize_ui(self) -> None:
        """UI全体を初期化し、主要コンポーネントをセットアップする"""
        self._setup_main_frame()
        self._setup_camera_section()
        self._setup_serial_section()
        self._setup_controller_section()
        self._setup_command_section()
        self._setup_log_section()

    def _setup_main_frame(self) -> None:
        """メインフレームの基本設定を行う"""
        self.main_frame = ttk.Frame(
            self.root, padding=5, relief="flat", width=1280, height=720
        )
        self.main_frame.pack(expand=True, fill="both", side="top")
        self.main_frame.columnconfigure(3, weight=1)

    def _setup_camera_section(self) -> None:
        """カメラ設定関連のUIコンポーネントをセットアップする"""
        self.camera_frame = LabelframeWithStickVar(self.main_frame, text="Camera")
        self.camera_frame.grid(columnspan=3, padx=5, sticky="ew")
        self._setup_camera_id_controls()
        self._setup_realtime_display_controls()
        self._setup_capture_controls()
        self._setup_camera_settings()
        self._setup_camera_name_selection()

    def _setup_camera_id_controls(self) -> None:
        """カメラID関連のコントロールをセットアップ"""
        self.camera_id_label = ttk.Label(
            self.camera_frame, text="Camera ID:", anchor="center"
        )
        self.camera_id_label.grid(column=0, row=0, padx=5, sticky="ew")
        self.camera_id_var = tk.IntVar()
        self.camera_id_entry = ttk.Entry(
            self.camera_frame, textvariable=self.camera_id_var, width=5
        )
        self.camera_id_entry.grid(column=1, row=0, padx=5, sticky="ew")
        self.reload_camera_button = ttk.Button(
            self.camera_frame, text="Reload Camera", command=self.openCamera
        )
        self.reload_camera_button.grid(column=2, row=0, padx=5, sticky="ew")

    def _setup_realtime_display_controls(self) -> None:
        """リアルタイム表示関連のコントロールをセットアップ"""
        self.sep_realtime = ttk.Separator(self.camera_frame, orient="vertical")
        self.sep_realtime.grid(column=3, row=0, sticky="ns")
        self.show_realtime_var = tk.BooleanVar()
        self.show_realtime_chk = ttk.Checkbutton(
            self.camera_frame, text="Show Realtime", variable=self.show_realtime_var
        )
        self.show_realtime_chk.grid(column=4, row=0)

    def _setup_capture_controls(self) -> None:
        """キャプチャ関連のコントロールをセットアップ"""
        self.sep_capture = ttk.Separator(self.camera_frame, orient="vertical")
        self.sep_capture.grid(column=5, row=0, sticky="ns")
        self.capture_frame = ttk.Frame(self.camera_frame)
        self.capture_frame.grid(column=6, row=0, sticky="ns")
        self.capture_button = ttk.Button(
            self.capture_frame, text="Capture", command=self.saveCapture
        )
        self.capture_button.grid(column=0, row=0)
        self.open_folder_img = tk.PhotoImage(file="./assets/icons8-OpenDir-16.png")
        self.open_capture_dir_button = ttk.Button(
            self.capture_frame, image=self.open_folder_img, command=self.OpenCaptureDir
        )
        self.open_capture_dir_button.grid(column=1, row=0)

    def _setup_camera_settings(self) -> None:
        """FPSと表示サイズ設定をセットアップ"""
        self.camera_settings_frame = ttk.Frame(self.camera_frame)
        self.camera_settings_frame.grid(column=0, columnspan=7, row=3, sticky="nsew")
        self._setup_fps_controls()
        self._setup_window_size_controls()

    def _setup_fps_controls(self) -> None:
        """FPS設定コントロールをセットアップ"""
        self.fps_label = ttk.Label(self.camera_settings_frame, text="FPS:")
        self.fps_label.grid(column=0, row=0, padx=5, sticky="ew")
        self.fps_var = tk.StringVar()
        self.fps_combobox = ttk.Combobox(
            self.camera_settings_frame,
            state="readonly",
            textvariable=self.fps_var,
            values=["60", "45", "30", "15", "5"],
            width=5,
            justify="right",
        )
        self.fps_combobox.grid(column=1, row=0, padx=0, sticky="ew")
        self.fps_combobox.bind("<<ComboboxSelected>>", self.applyFps)
        self.sep_size = ttk.Separator(self.camera_settings_frame, orient="vertical")
        self.sep_size.grid(column=2, row=0, sticky="ns")

    def _setup_window_size_controls(self) -> None:
        """ウィンドウサイズ設定コントロールをセットアップ"""
        self.window_size_label = ttk.Label(
            self.camera_settings_frame, text="Show Size:"
        )
        self.window_size_label.grid(column=3, row=0, padx=5, sticky="ew")
        self.show_size_var = tk.StringVar()
        self.show_size_combobox = ttk.Combobox(
            self.camera_settings_frame,
            state="readonly",
            textvariable=self.show_size_var,
            values=["640x360", "1280x720", "1920x1080"],
        )
        self.show_size_combobox.grid(column=4, row=0, padx=10, sticky="ew")
        self.show_size_combobox.bind("<<ComboboxSelected>>", self.applyWindowSize)

    def _setup_camera_name_selection(self) -> None:
        """カメラ名選択コントロールをセットアップ"""
        self.camera_name_label = ttk.Label(
            self.camera_frame, text="Camera Name:", anchor="center"
        )
        self.camera_name_label.grid(column=0, row=1, padx=5, sticky="ew")
        self.camera_name_var = tk.StringVar()
        self.camera_name_combobox = ttk.Combobox(
            self.camera_frame,
            state="readonly",
            textvariable=self.camera_name_var,
            width=40,
        )
        self.camera_name_combobox.grid(
            column=1, columnspan=6, row=1, padx=5, sticky="ew"
        )
        self.camera_name_combobox.bind("<<ComboboxSelected>>", self.set_cameraid)

    def _setup_serial_section(self) -> None:
        """シリアル設定関連のUIコンポーネントをセットアップする"""
        self.serial_frame = ttk.Labelframe(self.main_frame, text="Serial Settings")
        self.serial_frame.grid(column=0, row=1, columnspan=2, padx=5, sticky="nsew")
        self._setup_com_port_controls()
        self._setup_baud_rate_controls()
        self._setup_serial_action_buttons()
        self._setup_serial_display_controls()

    def _setup_com_port_controls(self) -> None:
        """COMポート設定コントロールをセットアップ"""
        self.com_port_label = ttk.Label(self.serial_frame, text="COM Port:")
        self.com_port_label.grid(column=0, row=0, padx=5, sticky="ew")
        self.com_port_var = tk.IntVar()
        self.com_port_entry = ttk.Entry(
            self.serial_frame, textvariable=self.com_port_var, width=5
        )
        self.com_port_entry.grid(column=1, row=0, padx=5, sticky="ew")

    def _setup_baud_rate_controls(self) -> None:
        """ボーレート設定コントロールをセットアップ"""
        self.baud_rate_label = ttk.Label(self.serial_frame, text="Baud Rate:")
        self.baud_rate_label.grid(column=2, row=0, padx=5, sticky="ew")
        self.baud_rate_var = tk.StringVar()
        self.baud_rate_combobox = ttk.Combobox(
            self.serial_frame,
            state=self.baud_rate_state,
            textvariable=self.baud_rate_var,
            values=["9600", "4800"],
            width=6,
            justify="right",
        )
        self.baud_rate_combobox.grid(column=3, row=0, padx=5, sticky="ew")
        self.baud_rate_combobox.bind("<<ComboboxSelected>>", self.applyBaudRate)

    def _setup_serial_action_buttons(self) -> None:
        """シリアル操作ボタンをセットアップ"""
        self.reload_serial_button = ttk.Button(
            self.serial_frame, text="Reload Port", command=self.activateSerial
        )
        self.reload_serial_button.grid(column=4, row=0, padx=5)
        self.disconnect_serial_button = ttk.Button(
            self.serial_frame, text="Disconnect Port", command=self.inactivateSerial
        )
        self.disconnect_serial_button.grid(column=5, row=0, padx=5)

    def _setup_serial_display_controls(self) -> None:
        """シリアル表示設定コントロールをセットアップ"""
        self.sep_serial = ttk.Separator(self.serial_frame, orient="vertical")
        self.sep_serial.grid(column=6, row=0, padx=5, sticky="ns")
        self.show_serial_var = tk.BooleanVar()
        self.show_serial_chk = ttk.Checkbutton(
            self.serial_frame, text="Show Serial", variable=self.show_serial_var
        )
        self.show_serial_chk.grid(column=7, row=0, columnspan=2, padx=5, sticky="ew")

    def _setup_controller_section(self) -> None:
        """コントローラ設定関連のUIコンポーネントをセットアップする"""
        self.control_frame = ttk.Labelframe(
            self.main_frame, text="Controller", height=200
        )
        self.control_frame.grid(
            column=0, row=2, columnspan=2, padx=5, pady=5, sticky="nsew"
        )
        self._setup_input_mode_controls()
        self._setup_controller_window_button()

    def _setup_input_mode_controls(self) -> None:
        """入力モード設定コントロールをセットアップ"""
        self.use_keyboard_var = tk.BooleanVar()
        self.use_keyboard_chk = ttk.Checkbutton(
            self.control_frame,
            text="Use Keyboard",
            variable=self.use_keyboard_var,
            command=self.activateKeyboard if platform.system() != "Darwin" else None,  # type: ignore
        )
        self.use_keyboard_chk.grid(column=0, row=0, padx=10, pady=5, sticky="ew")
        self.camera_frame.left_stick_mouse_var = tk.BooleanVar()
        self.use_lstick_mouse_chk = ttk.Checkbutton(
            self.control_frame,
            text="Use LStick Mouse",
            variable=self.camera_frame.left_stick_mouse_var,
            command=self.activate_Left_stick_mouse,
        )
        self.use_lstick_mouse_chk.grid(column=1, row=0, padx=10, pady=5, sticky="ew")
        self.camera_frame.right_stick_mouse_var = tk.BooleanVar()
        self.use_rstick_mouse_chk = ttk.Checkbutton(
            self.control_frame,
            text="Use RStick Mouse",
            variable=self.camera_frame.right_stick_mouse_var,
            command=self.activate_Right_stick_mouse,
        )
        self.use_rstick_mouse_chk.grid(column=1, row=1, padx=10, pady=5, sticky="ew")

    def _setup_controller_window_button(self) -> None:
        """コントローラウィンドウボタンをセットアップ"""
        self.open_controller_button = ttk.Button(
            self.control_frame, text="Controller", command=self.createControllerWindow
        )
        self.open_controller_button.grid(column=0, row=1, padx=10, pady=5, sticky="ew")

    def _setup_command_section(self) -> None:
        """コマンドタブ関連のUIコンポーネントをセットアップする"""
        self.command_frame = ttk.Labelframe(self.main_frame, text="Command", height=200)
        self.command_frame.grid(column=2, row=1, rowspan=2, padx=5, sticky="nsew")
        self._setup_command_list()
        self._setup_command_action_buttons()

    def _setup_command_list(self) -> None:
        """コマンドリストをセットアップ"""
        self.commands_list_frame = ttk.Frame(self.command_frame)
        self.commands_action_frame = ttk.Frame(self.command_frame)
        self.command_notebook = ttk.Notebook(self.commands_list_frame)
        self._setup_python_command_controls()
        self._setup_mcu_command_controls()
        self.command_notebook.pack(
            fill="both", expand=True, side="left", padx=5, pady=5
        )
        self.commands_list_frame.pack(
            fill="both", expand=True, side="top", anchor=tk.E, padx=5, pady=5
        )

    def _setup_python_command_controls(self) -> None:
        """Pythonコマンドコントロールをセットアップ"""
        self.selected_python_command_var = tk.StringVar()
        self.python_command_combobox = ttk.Combobox(
            self.command_notebook,
            state="readonly",
            textvariable=self.selected_python_command_var,
        )
        self.command_notebook.add(
            self.python_command_combobox, text="Python Command", padding=5
        )

    def _setup_mcu_command_controls(self) -> None:
        """MCUコマンドコントロールをセットアップ"""
        self.selected_mcu_command_var = tk.StringVar()
        self.mcu_command_combobox = ttk.Combobox(
            self.command_notebook,
            state="readonly",
            textvariable=self.selected_mcu_command_var,
        )
        self.command_notebook.add(
            self.mcu_command_combobox, text="Mcu Command", padding=5
        )

    def _setup_command_action_buttons(self) -> None:
        """コマンド操作ボタンをセットアップ"""
        self.open_command_dir_button = ttk.Button(
            self.commands_list_frame,
            image=self.open_folder_img,
            command=self.OpenCommandDir,
        )
        self.open_command_dir_button.pack(side="left", ipadx=5, pady=15)
        self.reload_commands_button = ttk.Button(
            self.commands_action_frame, text="Reload", command=self.reloadCommands
        )
        self.reload_commands_button.grid(column=0, row=1, padx=5, pady=5, sticky="ew")
        self.start_button = ttk.Button(
            self.commands_action_frame, text="Start", command=self.startPlay
        )
        self.start_button.grid(column=1, row=1, padx=5, pady=5, sticky="ew")
        self.commands_action_frame.pack(
            fill="none", expand=True, side="top", anchor=tk.E, padx=5, pady=5
        )

    def _setup_log_section(self) -> None:
        """ログ出力関連のUIコンポーネントをセットアップする"""
        self.log_scroll_helper = ScrollbarHelper(self.main_frame, scrolltype="both")
        self.log_text_area = tk.Text(
            self.log_scroll_helper.container,
            height=10,
            width=50,
            blockcursor=True,
            insertunfocussed="none",
            maxundo=0,
            relief="flat",
            state="disabled",
            undo=False,
        )
        self.log_text_area.pack(expand=True, fill="both", side="top")
        self.log_scroll_helper.add_child(self.log_text_area)
        self.log_scroll_helper.config(borderwidth=1, padding=1, relief="sunken")
        self.log_scroll_helper.grid(
            column=3, row=0, rowspan=3, padx=5, pady=5, sticky="nsew"
        )

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
            # /dev/video* を列挙して確認
            video_devices = sorted(
                [f for f in os.listdir("/dev") if f.startswith("video")],
                key=lambda x: int(x.replace("video", "")),
            )
            self.camera_dic = {
                str(i): f"/dev/{dev}" for i, dev in enumerate(video_devices)
            }
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

    def OpenCommandDir(self) -> None:
        if self.command_notebook.index("current") == 0:  # type: ignore
            directory = os.path.join("Commands", "PythonCommands")
        else:
            directory = os.path.join("Commands", "McuCommands")
        logger.debug(f"Open folder: '{directory}'")
        if platform.system() == "Windows":
            subprocess.call(f'explorer "{directory}"')
        elif platform.system() == "Darwin":
            command = f'open "{directory}"'
            subprocess.run(command, shell=True)

    def set_cameraid(self, event: Any = None) -> None:
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

    def applyFps(self, event: Any = None) -> None:
        print("changed FPS to: " + self.fps_var.get() + " [fps]")
        self.preview.setFps(self.fps_var.get())

    def applyBaudRate(self, event: Any = None) -> None:
        # 未実装
        pass

    def applyWindowSize(self, event: Any = None) -> None:
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

    def onFocusOutController(self, event: Any) -> None:
        # stop listening to keyboard events
        if event.widget == self.root and self.keyboard:
            self.keyboard.stop()
            self.keyboard = None

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

    def loadCommands(self) -> None:
        self.py_loader = CommandLoader(
            util.ospath("Commands/PythonCommands"), PythonCommandBase.PythonCommand
        )  # コマンドの読み込み
        self.mcu_loader = CommandLoader(
            util.ospath("Commands/McuCommands"), McuCommandBase.McuCommand
        )
        self.py_classes = self.py_loader.load()
        self.mcu_classes = self.mcu_loader.load()
        self.setCommandItems()
        self.assignCommand()

    def setCommandItems(self) -> None:
        self.python_command_combobox["values"] = [c.NAME for c in self.py_classes]
        self.python_command_combobox.current(0)
        self.mcu_command_combobox["values"] = [c.NAME for c in self.mcu_classes]
        self.mcu_command_combobox.current(0)

    def assignCommand(self) -> None:
        # 選択されているコマンドを取得する
        self.mcu_cur_command = self.mcu_classes[
            self.mcu_command_combobox.current()
        ]()  # MCUコマンドについて

        # pythonコマンドは画像認識を使うかどうかで分岐している
        cmd_class = self.py_classes[self.python_command_combobox.current()]
        if issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
            try:  # 画像認識の際に認識位置を表示する引数追加。互換性のため従来のはexceptに。
                self.py_cur_command = cmd_class(self.camera, self.preview)
            except TypeError:
                self.py_cur_command = cmd_class(self.camera)
            except Exception as e:
                logger.warning(f"Old Command Style: {e}")
                self.py_cur_command = cmd_class(self.camera)

        else:
            self.py_cur_command = cmd_class()

        if self.command_notebook.index(self.command_notebook.select()) == 0:  # type: ignore
            self.cur_command = self.py_cur_command
        else:
            self.cur_command = self.mcu_cur_command

    def reloadCommands(self) -> None:
        # 表示しているタブを読み取って、どのコマンドを表示しているか取得、リロード後もそれが選択されるようにする
        oldval_mcu = self.mcu_command_combobox.get()
        oldval_py = self.python_command_combobox.get()

        self.py_classes = self.py_loader.reload()
        self.mcu_classes = self.mcu_loader.reload()

        # Restore the command selecting state if possible
        self.setCommandItems()
        if oldval_mcu in self.mcu_command_combobox["values"]:
            self.mcu_command_combobox.set(oldval_mcu)
        if oldval_py in self.python_command_combobox["values"]:
            self.python_command_combobox.set(oldval_py)
        self.assignCommand()
        print("Finished reloading command modules.")
        logger.info("Reloaded commands.")

    def startPlay(self, *event: Any) -> None:
        if self.cur_command is None:
            print("No commands have been assigned yet.")
            logger.info("No commands have been assigned yet.")

        # set and init selected command
        self.assignCommand()

        print(self.start_button["text"] + " " + self.cur_command.NAME)
        logger.info(self.start_button["text"] + " " + self.cur_command.NAME)
        self.cur_command.start(self.ser, self.stopPlayPost)

        self.start_button["text"] = "Stop"
        self.start_button["command"] = self.stopPlay
        self.reload_commands_button["state"] = "disabled"

    def stopPlay(self) -> None:
        print(self.start_button["text"] + " " + self.cur_command.NAME)
        logger.info(self.start_button["text"] + " " + self.cur_command.NAME)
        self.start_button["state"] = "disabled"
        self.cur_command.end(self.ser)

        logger.info(self.preview.winfo_geometry())

    def stopPlayPost(self) -> None:
        self.start_button["text"] = "Start"
        self.start_button["command"] = self.startPlay
        self.start_button["state"] = "normal"
        self.reload_commands_button["state"] = "normal"

    def run(self) -> None:
        logger.debug("Start Poke-Controller")
        self.mainwindow.mainloop()

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

            self.camera.destroy()
            cv2.destroyAllWindows()
            logger.debug("Stop Poke Controller")
            self.root.destroy()

    def closingController(self) -> None:
        if self.controller is not None:
            self.controller.destroy()
            self.controller = None

    def loadSettings(self) -> None:
        self.settings = Settings.GuiSettings()
        self.settings.load()

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

    def display_text(self) -> None:
        batch = ""
        num = 0
        while not text_queue.empty() and num <= 60:
            message = text_queue.get()
            batch += message
            num += 1

        if batch:
            self.log_text_area.configure(state="normal")
            self.log_text_area.insert("end", batch)
            self.log_text_area.see("end")
            self.log_text_area.configure(state="disabled")
            self.log_text_area.update_idletasks()

        self.log_text_area.after(1000 // 60, self.display_text)


class QueueStdoutRedirector(object):
    """
    高速な標準出力リダイレクト用クラス
    バッファに出力を蓄積し、一定間隔でまとめてテキストウィジェットに反映します。
    """

    def __init__(self, text_widget: tk.Text, flush_interval: int = 1000 // 60):
        self.text_widget = text_widget
        self.buffer: queue.Queue = text_queue

    def write(self, string: str) -> None:
        self.buffer.put(string)

    def flush(self) -> None:
        pass


class StdoutRedirector(object):
    """
    標準出力をtextウィジェットにリダイレクトするクラス
    重いので止めました →# update_idletasks()で出力のたびに随時更新(従来はfor loopのときなどにまとめて出力されることがあった)
    """

    def __init__(self, text_widget: tk.Text) -> None:
        self.text_space = text_widget

    def write(self, string: str) -> None:
        self.text_space.configure(state="normal")
        self.text_space.insert("end", string)
        self.text_space.see("end")
        # self.text_space.update_idletasks()
        self.text_space.configure(state="disabled")

    def flush(self) -> None:
        pass


if __name__ == "__main__":
    # もし実行階層でlsした結果にSerialControllerフォルダがある場合はそこに移動する
    if "SerialController" in os.listdir():
        os.chdir("SerialController")

    # Todo: プロファイル機能(引数により異なる設定ファイルを利用する)を追加する
    logger = PokeConLogger.root_logger()
    # logger = logger
    logger.info("The root logger is created.")

    text_queue: queue.Queue = queue.Queue()

    root = Tk()
    app = PokeControllerApp(root)
    app.run()
