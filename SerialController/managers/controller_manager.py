import tkinter as tk
from tkinter import ttk
import platform

from GuiAssets import ControllerGUI
from Keyboard import SwitchKeyboardController

class ControllerManager:
    def __init__(self, app):
        self.app = app
        self.root = app.root

    def setup_ui(self, parent):
        self.control_lf = ttk.Labelframe(parent, text="Controller")

        self.app.is_use_keyboard = tk.BooleanVar()
        cb_use_keyboard = ttk.Checkbutton(self.control_lf, text="Use Keyboard", variable=self.app.is_use_keyboard, command=self.activateKeyboard)
        cb_use_keyboard.grid(column=0, padx="10", pady="5", sticky="ew")

        is_use_left_stick_mouse = tk.BooleanVar()
        cb_left_stick_mouse = ttk.Checkbutton(self.control_lf, text="Use LStick Mouse", variable=is_use_left_stick_mouse, command=self.app.activate_Left_stick_mouse)
        cb_left_stick_mouse.grid(column=1, row=0, padx="10", pady="5", sticky="ew")

        is_use_right_stick_mouse = tk.BooleanVar()
        cb_right_stick_mouse = ttk.Checkbutton(self.control_lf, text="Use RStick Mouse", variable=is_use_right_stick_mouse, command=self.app.activate_Right_stick_mouse)
        cb_right_stick_mouse.grid(column=1, row=1, padx="10", pady="5", sticky="ew")

        simpleConButton = ttk.Button(self.control_lf, text="Controller", command=self.createControllerWindow)
        simpleConButton.grid(column=0, padx="10", pady="5", row="1", sticky="ew")

        # For now, we still need to assign these to the camera_lf in the camera_manager
        self.app.camera_manager.camera_lf.is_use_left_stick_mouse = is_use_left_stick_mouse
        self.app.camera_manager.camera_lf.is_use_right_stick_mouse = is_use_right_stick_mouse

        return self.control_lf

    def activateKeyboard(self):
        is_windows = platform.system() == "Windows"
        if self.app.is_use_keyboard.get():
            if self.app.keyboard is None:
                self.app.keyboard = SwitchKeyboardController(self.app.keyPress)
                self.app.keyboard.listen()
            if is_windows:
                self.root.bind("<FocusIn>", self.app.onFocusInController)
                self.root.bind("<FocusOut>", self.app.onFocusOutController)
        else:
            if self.app.keyboard is not None:
                self.app.keyboard.stop()
                self.app.keyboard = None
            if is_windows:
                self.root.unbind("<FocusIn>")
                self.root.unbind("<FocusOut>")

    def createControllerWindow(self):
        if self.app.controller is not None:
            self.app.controller.focus_force()
            return
        window = ControllerGUI(self.root, self.app.serial_manager.ser)
        window.protocol("WM_DELETE_WINDOW", self.app.closingController)
        self.app.controller = window
