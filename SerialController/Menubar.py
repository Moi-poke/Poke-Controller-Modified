# import traceback
import tkinter as tk
from typing import TYPE_CHECKING, Optional

import cv2
import DiscordNotify

# from LineNotify import Line_Notify
from get_pokestatistics import GetFromHomeGUI
from KeyConfig import PokeKeycon
from loguru import logger

if TYPE_CHECKING:
    from .Window_old import PokeControllerApp


class PokeController_Menubar(tk.Menu):
    def __init__(self, master, **kw):  # type: ignore
        self.master: PokeControllerApp = master  # type:ignore
        self.root = self.master.root
        self.ser = self.master.ser
        self.preview = self.master.preview
        self.show_size_cb = self.master.show_size_combobox
        self.keyboard = self.master.keyboard
        self.settings = self.master.settings
        self.camera = self.master.camera
        self.poke_treeview: Optional[GetFromHomeGUI] = None  # type:ignore
        self.key_config: Optional[PokeKeycon] = None  # type:ignore
        self.line = None

        tk.Menu.__init__(self, self.root, **kw)
        self.menu = tk.Menu(self, tearoff=False)
        self.menu_command = tk.Menu(self, tearoff=False)
        self.add(tk.CASCADE, menu=self.menu, label="メニュー")  # type:ignore
        self.menu.add(tk.CASCADE, menu=self.menu_command, label="コマンド")  # type:ignore

        self.menu.add("separator")  # type:ignore
        self.menu.add("command", label="設定(dummy)")  # type:ignore
        # TODO: setup command_id_arg 'false' for menuitem.
        self.menu.add("command", command=self.exit, label="終了")  # type:ignore

        self.AssignMenuCommand()
        # self.LineTokenSetting()

    # TODO: setup command_id_arg 'false' for menuitem.

    def AssignMenuCommand(self) -> None:
        logger.debug("Assigning menu command")
        # self.menu_command.add(
        #     "command", command=self.LineTokenSetting, label="LINE Token Check"
        # )
        # TODO: setup command_id_arg 'false' for menuitem.
        self.menu_command.add(  # type:ignore
            "command", command=self.OpenPokeHomeCoop, label="Pokemon Home 連携"
        )
        self.menu_command.add(  # type:ignore
            "command", command=self.OpenKeyConfig, label="キーコンフィグ"
        )
        self.menu_command.add(  # type:ignore
            "command", command=self.ResetWindowSize, label="画面サイズのリセット"
        )
        self.menu_command.add(  # type:ignore
            "command",
            command=self.open_discord_notify_setting,
            label="Discord通知の設定",
        )

    # TODO: setup command_id_arg 'false' for menuitem.

    def OpenPokeHomeCoop(self) -> None:
        logger.debug("Open Pokemon home cooperate window")
        if self.poke_treeview is not None:
            self.poke_treeview.focus_force()
            return

        window2 = GetFromHomeGUI(
            self.root, self.settings.season, self.settings.is_SingleBattle
        )
        window2.protocol("WM_DELETE_WINDOW", self.closingGetFromHome)
        self.poke_treeview = window2

    def closingGetFromHome(self) -> None:
        logger.debug("Close Pokemon home cooperate window")
        if self.poke_treeview:
            self.poke_treeview.destroy()
        self.poke_treeview = None

    # def LineTokenSetting(self) -> None:
    #     try:
    #         logger.debug("Show line API")
    #         if self.line is None:
    #             self.line = Line_Notify(self.camera)
    #         print(self.line)
    #         self.line.getRateLimit()
    #         # LINE.send_text_n_image("CAPTURE")
    #     except Exception as E:
    #         logger.error(E)
    #         logger.error(traceback.format_exc())

    def OpenKeyConfig(self) -> None:
        logger.debug("Open KeyConfig window")
        if self.key_config is not None:
            self.key_config.focus_force()
            return

        kc_window = PokeKeycon(self.root)
        kc_window.protocol("WM_DELETE_WINDOW", self.closingKeyConfig)
        self.key_config = kc_window

    def closingKeyConfig(self) -> None:
        logger.debug("Close KeyConfig window")
        if self.key_config:
            self.key_config.destroy()
        self.key_config = None

    def ResetWindowSize(self) -> None:
        logger.debug("Reset window size")
        self.preview.setShowsize(360, 640)
        self.show_size_cb.current(0)

    def open_discord_notify_setting(self) -> None:
        webhook = DiscordNotify.Discord_Notify()
        DiscordNotify.WebhookGUI(self.root, webhook=webhook)

    def exit(self) -> None:
        logger.debug("Close Menubar")
        if self.ser.isOpened():
            self.ser.closeSerial()
            print("serial disconnected")

        # stop listening to keyboard events
        if self.keyboard is not None:
            self.keyboard.stop()
            self.keyboard = None

        # save settings
        self.settings.save()

        self.camera.destroy()
        cv2.destroyAllWindows()
        self.master.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    widget = PokeController_Menubar(root)  # type:ignore
    widget.pack(expand=True, fill="both")
    root.mainloop()
