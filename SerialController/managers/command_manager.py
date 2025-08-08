import tkinter as tk
from tkinter import ttk
import os
import platform
import subprocess
from loguru import logger

import Utility as util
from CommandLoader import CommandLoader
from Commands import McuCommandBase, PythonCommandBase

class CommandManager:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.py_loader = None
        self.mcu_loader = None
        self.py_classes = []
        self.mcu_classes = []
        self.cur_command = None

    def setup_ui(self, parent):
        self.lf = ttk.Labelframe(parent, text="Command")

        Commands_f = ttk.Frame(self.lf)
        Commands_2_f = ttk.Frame(self.lf)

        self.Command_nb = ttk.Notebook(Commands_f)
        self.py_cb = ttk.Combobox(self.Command_nb, state="readonly")
        self.py_cb.pack(side="top")
        self.Command_nb.add(self.py_cb, padding="5", text="Python Command")

        self.mcu_cb = ttk.Combobox(self.Command_nb, state="readonly")
        self.mcu_cb.pack(side="top")
        self.Command_nb.add(self.mcu_cb, padding="5", text="Mcu Command")
        self.Command_nb.pack(fill="both", expand=True, padx="5", pady="5", side="left")

        self.OpenCommandDirButton = ttk.Button(Commands_f, image=self.app.open_folder_img, command=self.OpenCommandDir)
        self.OpenCommandDirButton.pack(fill="y", expand=False, side="left", ipadx="5", pady="15")

        self.reloadCommandButton = ttk.Button(Commands_2_f, text="Reload", command=self.reloadCommands)
        self.reloadCommandButton.grid(column="0", padx="5", pady="5", row="1", sticky="ew")

        self.startButton = ttk.Button(Commands_2_f, text="Start", command=self.startPlay)
        self.startButton.grid(column="1", padx="5", pady="5", row="1", sticky="ew")

        Commands_f.pack(fill="both", expand=True, padx="5", pady="5", anchor=tk.E, side="top")
        Commands_2_f.pack(fill=None, expand=True, padx="5", pady="5", anchor=tk.E, side="top")

        return self.lf

    def loadCommands(self):
        self.py_loader = CommandLoader(util.ospath("Commands/PythonCommands"), PythonCommandBase.PythonCommand)
        self.mcu_loader = CommandLoader(util.ospath("Commands/McuCommands"), McuCommandBase.McuCommand)
        self.py_classes = self.py_loader.load()
        self.mcu_classes = self.mcu_loader.load()
        self.setCommandItems()
        self.assignCommand()

    def setCommandItems(self):
        self.py_cb["values"] = [c.NAME for c in self.py_classes]
        if self.py_classes: self.py_cb.current(0)

        self.mcu_cb["values"] = [c.NAME for c in self.mcu_classes]
        if self.mcu_classes: self.mcu_cb.current(0)

    def assignCommand(self):
        if self.Command_nb.index(self.Command_nb.select()) == 0:
            if not self.py_classes: return
            cmd_class = self.py_classes[self.py_cb.current()]
            if issubclass(cmd_class, PythonCommandBase.ImageProcPythonCommand):
                try:
                    self.cur_command = cmd_class(self.app.camera_manager.camera, self.app.preview)
                except TypeError:
                    self.cur_command = cmd_class(self.app.camera_manager.camera)
            else:
                self.cur_command = cmd_class()
        else:
            if not self.mcu_classes: return
            self.cur_command = self.mcu_classes[self.mcu_cb.current()]()

        self.app.cur_command = self.cur_command

    def reloadCommands(self):
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
        logger.info("Reloaded commands.")

    def startPlay(self, *event):
        self.assignCommand()
        if self.cur_command is None:
            logger.info("No commands have been assigned yet.")
            return

        logger.info(f"Start {self.cur_command.NAME}")
        self.cur_command.start(self.app.serial_manager.ser, self.stopPlayPost)

        self.startButton["text"] = "Stop"
        self.startButton["command"] = self.stopPlay
        self.reloadCommandButton["state"] = "disabled"

    def stopPlay(self):
        if self.cur_command is None: return
        logger.info(f"Stop {self.cur_command.NAME}")
        self.startButton["state"] = "disabled"
        self.cur_command.end(self.app.serial_manager.ser)

    def stopPlayPost(self):
        self.startButton["text"] = "Start"
        self.startButton["command"] = self.startPlay
        self.startButton["state"] = "normal"
        self.reloadCommandButton["state"] = "normal"

    def OpenCommandDir(self):
        if self.Command_nb.index("current") == 0:
            directory = os.path.join("Commands", "PythonCommands")
        else:
            directory = os.path.join("Commands", "McuCommands")

        logger.debug(f"Open folder: '{directory}'")
        if platform.system() == "Windows":
            os.startfile(directory)
        elif platform.system() == "Darwin":
            subprocess.run(['open', directory])
