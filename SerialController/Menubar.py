import tkinter

import cv2
import tkinter as tk

from KeyConfig import PokeKeycon
from LineNotify import Line_Notify
# from Window_renewed import PokeConApp
from get_pokestatistics import GetFromHomeGUI
from logging import getLogger, DEBUG, NullHandler, INFO
from assets.gui_base import AppBase
from GuiAssets import ControllerGUI


class PokeController_Menubar(tk.Menu):
    def __init__(self, master: AppBase, **kw):
        self.logger = getLogger(__name__)
        self.logger.addHandler(NullHandler())
        # self.logger.setLevel(INFO)
        self.logger.propagate = True

        self.master = master
        self.root = self.master.root
        self.ser = self.master.ser
        self.preview = self.master.preview
        self.show_size_cb = self.master.combobox_show_size
        self.keyboard = self.master.keyboard
        self.settings = self.master.settings
        self.camera = self.master.camera
        self.poke_treeview = None
        self.key_config = None
        self.line = None
        self.controller = None

        tk.Menu.__init__(self, self.root, **kw)
        self.menu = tk.Menu(self, tearoff=False)
        self.menu_command = tk.Menu(self, tearoff=False)
        self.add(tk.CASCADE, menu=self.menu, label='メニュー')
        self.menu.add(tk.CASCADE, menu=self.menu_command, label='コマンド')

        self.menu.add('separator')
        self.menu.add('command', label='設定(dummy)')
        # TODO: setup command_id_arg 'false' for menuitem.
        self.menu.add('command', command=self.exit, label='終了')

        # menu
        self.menu_options = tk.Menu(self, tearoff=False)
        self.is_use_keyboard = tk.BooleanVar(value=self.settings.is_use_keyboard.get())
        self.isShowInput = tk.BooleanVar(value=self.settings.isShowInput.get())
        self.mi_checkbutton_use_keyboard = 0
        self.menu_options.add('checkbutton', label='Keyboard Control', variable=self.is_use_keyboard)
        self.mi_command_open_gui_controller = 1
        self.menu_options.add('checkbutton', label='Show Input', variable=self.isShowInput)
        self.mi_command_open_gui_controller = 2
        self.menu_options.add('command', label='GUI Controller')
        self.menu_options.entryconfigure(self.mi_command_open_gui_controller, command=self.open_gui_controller)
        self.is_use_L_stick_mouse = tk.BooleanVar(value=self.settings.is_use_L_stick_mouse.get())
        self.mi_checkbutton_L_stick = 3
        self.menu_options.add('checkbutton', label='Left stick / mouse', variable=self.is_use_L_stick_mouse,
                              command=self.applyLeft)
        self.is_use_R_stick_mouse = tk.BooleanVar(value=self.settings.is_use_R_stick_mouse.get())
        self.mi_checkbutton_R_stick = 4
        self.menu_options.add('checkbutton', label='Right stick / mouse', variable=self.is_use_R_stick_mouse,
                              command=self.applyRight)
        self.mi_separator1 = 5
        self.menu_options.add('separator')
        self.submenu2 = tk.Menu(self.menu_options, tearoff=False)
        self.menu_options.add(tk.CASCADE, menu=self.submenu2, label='dummy')
        self.menu_options.configure(tearoff=False, title='Option')
        self.add(tk.CASCADE, menu=self.menu_options, label='Option')

        self.AssignMenuCommand()
        self.LineTokenSetting()

    # TODO: setup command_id_arg 'false' for menuitem.

    def applyLeft(self):
        self.preview.ApplyLStickMouse()

    def applyRight(self):
        self.preview.ApplyRStickMouse()

    def AssignMenuCommand(self):
        self.logger.debug("Assigning menu command")
        self.menu_command.add('command', command=self.LineTokenSetting, label='LINE Token Check')
        # TODO: setup command_id_arg 'false' for menuitem.
        self.menu_command.add('command', command=self.OpenPokeHomeCoop, label='Pokemon Home 連携')
        self.menu_command.add('command', command=self.OpenKeyConfig, label='キーコンフィグ')
        self.menu_command.add('command', command=self.ResetWindowSize, label='画面サイズのリセット')

    # TODO: setup command_id_arg 'false' for menuitem.

    def OpenPokeHomeCoop(self):
        self.logger.debug("Open Pokemon home cooperate window")
        if self.poke_treeview is not None:
            self.poke_treeview.focus_force()
            return

        window2 = GetFromHomeGUI(self.root, self.settings.season, self.settings.is_SingleBattle)
        window2.protocol("WM_DELETE_WINDOW", self.closingGetFromHome)
        self.poke_treeview = window2

    def closingGetFromHome(self):
        self.logger.debug("Close Pokemon home cooperate window")
        self.poke_treeview.destroy()
        self.poke_treeview = None

    def LineTokenSetting(self):
        self.logger.debug("Show line API")
        if self.line is None:
            self.line = Line_Notify(self.camera)

        self.line.getRateLimit()
        # LINE.send_text_n_image("CAPTURE")

    def OpenKeyConfig(self):
        self.logger.debug("Open KeyConfig window")
        if self.key_config is not None:
            self.key_config.focus_force()
            return
        s = tk.Checkbutton(self, )
        s.deselect()

        kc_window = PokeKeycon(self.root)
        kc_window.protocol("WM_DELETE_WINDOW", self.closingKeyConfig)
        self.key_config = kc_window

    def closingKeyConfig(self):
        self.logger.debug("Close KeyConfig window")
        self.key_config.destroy()
        self.key_config = None

    def ResetWindowSize(self):
        self.logger.debug("Reset window size")
        self.preview.setShowsize(360, 640)
        self.show_size_cb.current(0)

    def exit(self):
        self.logger.debug("Close Menubar")
        if self.ser.isOpened():
            self.ser.closeSerial()
            self.logger.debug("serial disconnected")

        # stop listening to keyboard events
        if self.keyboard is not None:
            self.keyboard.stop()
            self.keyboard = None

        # save settings
        self.settings.save()

        self.camera.destroy()
        cv2.destroyAllWindows()
        self.master.destroy()

    def open_gui_controller(self):
        if self.controller is not None:
            self.controller.focus_force()
            return

        window = ControllerGUI(self.root, self.ser)
        window.protocol("WM_DELETE_WINDOW", self.closingController)
        self.controller = window

    def closingController(self) -> None:
        self.controller.destroy()
        self.controller = None


if __name__ == '__main__':
    root = tk.Tk()
    widget = PokeController_Menubar(root)
    widget.pack(expand=True, fill='both')
    root.mainloop()
