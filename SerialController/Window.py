from typing import Any
import cv2
import os
import sys
import tkinter.ttk as ttk
import tkinter.messagebox as tkmsg
from tkinter import Tk
from loguru import logger
import queue

import Settings
from GuiAssets import CaptureArea
from Menubar import PokeController_Menubar
from UI import UI
from managers.camera_manager import CameraManager
from managers.serial_manager import SerialManager
from managers.command_manager import CommandManager
from managers.controller_manager import ControllerManager
import PokeConLogger

NAME = "Poke-Controller"
VERSION = "v3.1.3 Modified"

class PokeControllerApp:
    def __init__(self, master: Tk | None = None):
        self.baud_rate_state = "disabled"

        if master is None:
            master = Tk()
        self.root = master
        self.root.title(NAME + " " + VERSION)

        self.controller = None
        self.poke_treeview = None
        self.keyPress = None
        self.keyboard = None
        self.cur_command = None

        # Create managers
        self.camera_manager = CameraManager(self, None)
        self.serial_manager = SerialManager(self)
        self.command_manager = CommandManager(self)
        self.controller_manager = ControllerManager(self)

        # Create UI
        self.ui = UI(self.root, self, self.camera_manager, self.serial_manager, self.command_manager, self.controller_manager)
        self.ui.setup_ui()

        # Standard output redirection
        sys.stdout = QueueStdoutRedirector(self.logArea)
        self.logArea.after(100, self.display_text)

        # Load settings
        self.loadSettings()
        self.apply_settings_to_ui()

        # Initialize
        self.preview = CaptureArea(
            None,
            self.settings.fps.get(),
            self.settings.is_show_realtime,
            None,
            self.camera_manager.camera_lf,
            *list(map(int, self.settings.show_size.get().split("x"))),
        )
        self.camera_manager.preview = self.preview
        self.camera_manager.initialize_camera()
        self.preview.camera = self.camera_manager.camera
        self.preview.config(cursor="crosshair")
        self.preview.grid(column="0", columnspan=7, row=2, padx="5", pady="5", sticky=tk.NSEW)

        self.serial_manager.initialize_serial()
        self.controller_manager.activateKeyboard()
        self.command_manager.loadCommands()

        self.root.bind("<Key-F5>", self.command_manager.reloadCommands)
        self.root.bind("<Key-F6>", self.command_manager.startPlay)
        self.root.bind("<Key-Escape>", lambda e: self.command_manager.stopPlay())

        self.mainwindow = self.frame_1
        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.preview.startCapture()

        self.menu = PokeController_Menubar(self)
        self.root.config(menu=self.menu)

    def apply_settings_to_ui(self):
        s = self.settings
        self.controller_manager.app.is_use_keyboard.set(s.is_use_keyboard.get())

        self.camera_manager.app.camera_id.set(s.camera_id.get())
        self.camera_manager.app.fps.set(s.fps.get())
        self.camera_manager.app.show_size.set(s.show_size.get())
        self.camera_manager.app.is_show_realtime.set(s.is_show_realtime.get())
        self.camera_manager.fps_cb.set(s.fps.get())
        self.camera_manager.show_size_cb.set(s.show_size.get())

        self.serial_manager.app.com_port.set(s.com_port.get())
        self.serial_manager.app.com_port_name.set(s.com_port_name.get())
        self.serial_manager.app.baud_rate.set(s.baud_rate.get())
        self.serial_manager.app.is_show_serial.set(s.is_show_serial.get())
        self.serial_manager.baud_rate_cb.set(s.baud_rate.get())

    def onFocusInController(self, event: Any) -> None:
        if event.widget == self.root and self.keyboard is None:
            self.keyboard = self.controller_manager.activateKeyboard()

    def onFocusOutController(self, event: Any) -> None:
        if event.widget == self.root and self.keyboard is not None:
            self.keyboard = self.controller_manager.activateKeyboard()

    def activate_Left_stick_mouse(self) -> None:
        self.preview.ApplyLStickMouse()

    def activate_Right_stick_mouse(self) -> None:
        self.preview.ApplyRStickMouse()

    def run(self) -> None:
        logger.debug("Start Poke-Controller")
        self.mainwindow.mainloop()

    def exit(self) -> None:
        if tkmsg.askyesno("確認", "Poke Controllerを終了しますか？"):
            self.serial_manager.destroy()
            if self.keyboard is not None:
                self.keyboard.stop()

            s = self.settings
            s.is_show_realtime.set(self.camera_manager.app.is_show_realtime.get())
            s.is_show_serial.set(self.serial_manager.app.is_show_serial.get())
            s.is_use_keyboard.set(self.controller_manager.app.is_use_keyboard.get())
            s.fps.set(self.camera_manager.app.fps.get())
            s.show_size.set(self.camera_manager.app.show_size.get())
            s.com_port.set(self.serial_manager.app.com_port.get())
            s.baud_rate.set(self.serial_manager.app.baud_rate.get())
            s.camera_id.set(self.camera_manager.app.camera_id.get())
            s.save()

            self.camera_manager.destroy()
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

    def display_text(self) -> None:
        batch = ""
        while not text_queue.empty():
            try:
                batch += text_queue.get_nowait()
            except queue.Empty:
                break
        if batch:
            self.logArea.configure(state="normal")
            self.logArea.insert("end", batch)
            self.logArea.see("end")
            self.logArea.configure(state="disabled")
            self.logArea.update_idletasks()
        self.logArea.after(100, self.display_text)

class QueueStdoutRedirector:
    def __init__(self, text_widget: Any):
        self.text_widget = text_widget
        self.buffer: queue.Queue = queue.Queue()

    def write(self, string: str) -> None:
        self.buffer.put(string)

    def flush(self) -> None:
        pass

if __name__ == "__main__":
    if "SerialController" in os.listdir():
        os.chdir("SerialController")
    import tkinter as tk
    logger = PokeConLogger.root_logger()
    logger.info("The root logger is created.")
    text_queue: queue.Queue = queue.Queue()
    root = tk.Tk()
    app = PokeControllerApp(root)
    app.run()
