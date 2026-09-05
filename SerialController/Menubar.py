import tkinter as tk
from typing import Any

import DiscordNotify
from InputLogConfig import InputLogConfig
from KeyConfig import PokeKeycon
from WakeSetup import WakeSetup
from get_pokestatistics import GetFromHomeGUI
from loguru import logger


class PokeController_Menubar(tk.Menu):
    def __init__(self, master: Any, **kw: Any) -> None:
        # 親クラスを先に初期化する。tk.Menu.__init__ は self.master を
        # 親ウィジェット(root)で上書きするため、アプリ本体は self.app に持つ
        self.app = master
        tk.Menu.__init__(self, master.root, **kw)

        self.poke_treeview: Any | None = None
        self.key_config: PokeKeycon | None = None
        self.wake_setup: WakeSetup | None = None
        self.input_log_config: InputLogConfig | None = None

        self.menu = tk.Menu(self, tearoff=False)
        self.menu_command = tk.Menu(self, tearoff=False)
        self.add(tk.CASCADE, menu=self.menu, label="メニュー")
        self.menu.add(tk.CASCADE, menu=self.menu_command, label="コマンド")

        self.menu.add("separator")
        # 「設定(dummy)」は command 未指定の未実装項目だったため、実装されるまで
        # メニューから外す（誤クリックで無反応になるのを避ける）。
        # self.menu.add("command", label="設定", command=self.openSettings)
        self.menu.add("command", command=self.exit, label="終了")

        self.AssignMenuCommand()
        # self.LineTokenSetting()

    # Window 側で再接続・再生成される値は都度参照する（古い参照を掴まないため）
    @property
    def root(self) -> tk.Misc:
        return self.app.root

    @property
    def ser(self) -> Any:
        return self.app.ser

    @property
    def preview(self) -> Any:
        return self.app.preview

    @property
    def show_size_cb(self) -> Any:
        return self.app.show_size_cb

    @property
    def keyboard(self) -> Any:
        return self.app.keyboard

    @property
    def settings(self) -> Any:
        return self.app.settings

    @property
    def camera(self) -> Any:
        return self.app.camera

    def AssignMenuCommand(self) -> None:
        logger.debug("Assigning menu command")
        # self.menu_command.add(
        #     "command", command=self.LineTokenSetting, label="LINE Token Check"
        # )
        self.menu_command.add(
            "command", command=self.OpenPokeHomeCoop, label="Pokemon Home 連携"
        )
        self.menu_command.add(
            "command", command=self.OpenKeyConfig, label="キーコンフィグ"
        )
        self.menu_command.add(
            "command", command=self.OpenWakeSetup, label="Switch2 Wake設定"
        )
        self.menu_command.add(
            "command", command=self.OpenInputLogConfig, label="入力ログの書式"
        )
        self.menu_command.add(
            "command", command=self.ResetWindowSize, label="画面サイズのリセット"
        )
        self.menu_command.add(
            "command",
            command=self.open_discord_notify_setting,
            label="Discord通知の設定",
        )

    @staticmethod
    def _alive(window: Any) -> bool:
        """ウィンドウが生きているか。破棄済み参照の再利用を防ぐ。

        画面側が destroy されても参照は残るため、「None か」だけでは
        足りない。OpenInputLogConfig と同じ判定を他でも使う。
        """
        try:
            return window is not None and bool(window.winfo_exists())
        except Exception:
            return False

    def OpenPokeHomeCoop(self) -> None:
        logger.debug("Open Pokemon home cooperate window")
        treeview = self.poke_treeview
        if treeview is not None and self._alive(treeview):
            treeview.focus_force()
            return
        self.poke_treeview = None

        window2 = GetFromHomeGUI(
            self.root, self.settings.season, self.settings.is_SingleBattle
        )
        window2.protocol("WM_DELETE_WINDOW", self.closingGetFromHome)
        self.poke_treeview = window2

    def closingGetFromHome(self) -> None:
        logger.debug("Close Pokemon home cooperate window")
        if self.poke_treeview is not None:
            try:
                self.poke_treeview.destroy()
            except Exception:
                pass
            self.poke_treeview = None

    def OpenKeyConfig(self) -> None:
        logger.debug("Open KeyConfig window")
        key_config = self.key_config
        if key_config is not None and self._alive(key_config):
            key_config.focus_force()
            return
        self.key_config = None

        kc_window = PokeKeycon(self.root)
        kc_window.protocol("WM_DELETE_WINDOW", self.closingKeyConfig)
        self.key_config = kc_window

    def closingKeyConfig(self) -> None:
        logger.debug("Close KeyConfig window")
        if self.key_config is not None:
            try:
                self.key_config.destroy()
            except Exception:
                pass
            self.key_config = None

    def OpenWakeSetup(self) -> None:
        logger.debug("Open WakeSetup window")
        wake_window = getattr(self.wake_setup, "window", None)
        if wake_window is not None and self._alive(wake_window):
            wake_window.focus_force()
            return
        self.wake_setup = None
        self.wake_setup = WakeSetup(self.root, self.ser)
        self.wake_setup.window.protocol("WM_DELETE_WINDOW", self.closingWakeSetup)

    def closingWakeSetup(self) -> None:
        logger.debug("Close WakeSetup window")
        if self.wake_setup is not None:
            try:
                self.wake_setup.close()
            except Exception:
                pass
            self.wake_setup = None

    def closeAll(self) -> None:
        """子窓をすべて閉じる。終了処理から呼ぶ。

        開きっぱなしのまま root.destroy() へ進むと、破棄途中の
        ウィジェットを after 予約が触って TclError になる。
        """
        for name in ("wake_setup", "key_config", "poke_treeview", "input_log_config"):
            window = getattr(self, name, None)
            if window is None:
                continue
            try:
                close = getattr(window, "close", None)
                if callable(close):
                    close()
                    continue
                if self._alive(window):
                    window.destroy()
            except Exception:
                pass
            finally:
                setattr(self, name, None)

    def OpenInputLogConfig(self) -> None:
        """入力ログの書式を決める画面を開く。

        変更は即座に反映させたいので、設定を書いたあと Window 側の
        _apply_input_log_settings() を呼び戻してもらう。書式の解釈は
        InputLog が、Sender への受け渡しは Window が持つ形を崩さない。

        既に開いているかの判定は「参照が None か」だけでは足りない。
        画面側が destroy されても参照は残るため、破棄済みのウィンドウへ
        focus_force() を呼んで TclError になっていた。生きているかを
        確かめ、死んでいれば掴み直す。
        """
        logger.debug("Open InputLogConfig window")
        if self.input_log_config is not None:
            if self.input_log_config.alive():
                self.input_log_config.focus_force()
                return
            self.input_log_config = None  # 破棄済み。作り直す

        self.input_log_config = InputLogConfig(
            self.root,
            self.settings,
            on_change=self.app._apply_input_log_settings,
            on_close=self.closingInputLogConfig,
        )

    def closingInputLogConfig(self) -> None:
        """画面が閉じたときに呼ばれる。参照を捨てるだけでよい。

        破棄そのものは画面側が済ませている。ここで destroy を呼ぶと
        二重破棄になるため触らない。
        """
        logger.debug("Close InputLogConfig window")
        self.input_log_config = None

    def ResetWindowSize(self) -> None:
        logger.debug("Reset window size")
        self.preview.setShowsize(360, 640)
        self.show_size_cb.current(0)

    def open_discord_notify_setting(self) -> None:
        webhook = DiscordNotify.Discord_Notify()
        DiscordNotify.WebhookGUI(self.root, webhook=webhook)

    def exit(self) -> None:
        # 終了処理は Window.exit() に一本化する
        logger.debug("Close Menubar")
        self.app.exit()
