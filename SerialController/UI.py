import tkinter as tk
import tkinter.ttk as ttk

from pygubu.widgets.scrollbarhelper import ScrollbarHelper
from managers.camera_manager import CameraManager
from managers.serial_manager import SerialManager
from managers.command_manager import CommandManager
from managers.controller_manager import ControllerManager

class UI:
    def __init__(self, master: tk.Tk, app, camera_manager, serial_manager, command_manager, controller_manager):
        self.master = master
        self.app = app
        self.camera_manager = camera_manager
        self.serial_manager = serial_manager
        self.command_manager = command_manager
        self.controller_manager = controller_manager

    def setup_ui(self):
        # build ui
        self.app.frame_1 = ttk.Frame(self.master)
        self.app.frame_1.pack(expand="true", fill="both", side="top")

        # Camera Frame
        camera_frame = self.camera_manager.setup_ui(self.app.frame_1)
        camera_frame.grid(columnspan=3, padx="5", sticky="ew")

        # Serial Frame
        serial_frame = self.serial_manager.setup_ui(self.app.frame_1)
        serial_frame.grid(column="0", columnspan=2, padx="5", row="1", sticky="nsew")

        # Command Frame
        command_frame = self.command_manager.setup_ui(self.app.frame_1)
        command_frame.grid(column="2", padx="5", row="1", rowspan="2", sticky="nsew")

        # Controller Frame
        controller_frame = self.controller_manager.setup_ui(self.app.frame_1)
        controller_frame.grid(column="0", padx="5", row="2", columnspan="2", sticky="nsew")

        # Log Area
        self.app.log_scroll = ScrollbarHelper(self.app.frame_1, scrolltype="both")
        self.app.logArea = tk.Text(self.app.log_scroll.container)
        self.app.logArea.config(
            blockcursor="true", height="10", insertunfocussed="none", maxundo="0"
        )
        self.app.logArea.config(relief="flat", state="disabled", undo="false", width="50")
        self.app.logArea.pack(expand="true", fill="both", side="top")
        self.app.log_scroll.add_child(self.app.logArea)
        self.app.log_scroll.config(borderwidth="1", padding="1", relief="sunken")
        self.app.log_scroll.grid(
            column="3", padx="5", pady="5", row="0", rowspan="3", sticky="nsew"
        )
        self.app.frame_1.columnconfigure("3", weight="1")
