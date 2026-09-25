import tkinter as tk
from typing import Any

import DiscordNotify
import WindowUtils
from BconSetup import BconSetup
from InputLogConfig import InputLogConfig
from KeyConfig import PokeKeycon
from SerialMonitor import SerialMonitor
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
        self.bcon_setup: BconSetup | None = None
        self.serial_monitor: SerialMonitor | None = None
        self.input_log_config: InputLogConfig | None = None

        self.menu = tk.Menu(self, tearoff=False)
        self.menu_command = tk.Menu(self, tearoff=False)
        self.menu_view = tk.Menu(self, tearoff=False)
        self.add(tk.CASCADE, menu=self.menu, label="メニュー")
        self.menu.add(tk.CASCADE, menu=self.menu_command, label="コマンド")
        self.menu.add(tk.CASCADE, menu=self.menu_view, label="表示")
        self.menu_view.add(
            "command",
            command=lambda: self.applyWindowSize(1280, 720),
            label="1280x720",
        )
        self.menu_view.add(
            "command",
            command=lambda: self.applyWindowSize(1920, 1080),
            label="1920x1080",
        )
        self.menu_view.add("separator")
        self.menu_view.add(
            "command", command=lambda: self.lockAspect(True), label="16:9 に固定"
        )
        self.menu_view.add(
            "command", command=lambda: self.lockAspect(False), label="固定を解除"
        )

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
        self.menu_command.add("command", command=self.OpenBconSetup, label="Bcon設定")
        self.menu_command.add(
            "command", command=self.OpenSerialMonitor, label="シリアルモニタ"
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
        self.menu_command.add(
            "command", command=self.OpenScriptInstall, label="スクリプトの導入..."
        )
        self.menu_command.add(
            "command", command=self.OpenScriptUninstall, label="スクリプトの削除..."
        )
        self.menu_command.add(
            "command", command=self.OpenBlocklyEditor, label="Blocklyエディタ..."
        )
        self.menu_command.add(
            "command",
            command=self.OpenErrorReport,
            label="エラー報告をコピー...",
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

        # プロファイル別iniを編集するため、アプリ本体のprofileを渡す。
        # 旧来の呼び出し・テスト用の偽appにprofileが無くても落ちないようgetattrで守る。
        kc_window = PokeKeycon(
            self.root,
            profile=getattr(self.app, "profile", ""),
            on_saved=self._reload_live_keymap,
        )
        kc_window.protocol("WM_DELETE_WINDOW", self.closingKeyConfig)
        self.key_config = kc_window

    def _reload_live_keymap(self) -> None:
        """保存直後に生きている実体へ割り当てを読み直させる。

        実体が無ければ何もしない（次回の有効化で新割当を読む）。
        偽app（テスト用）に serial が無くても落ちない。
        """
        serial = getattr(self.app, "serial", None)
        reload = getattr(serial, "reload_keyboard_map", None)
        if callable(reload):
            reload()

    def closingKeyConfig(self) -> None:
        logger.debug("Close KeyConfig window")
        if self.key_config is not None:
            try:
                self.key_config.destroy()
            except Exception:
                pass
            self.key_config = None

    def applyWindowSize(self, width: int, height: int) -> None:
        """メインウィンドウ全体を指定サイズにする。保存は終了時に行う。"""
        root: Any = self.root
        try:
            root.geometry(f"{int(width)}x{int(height)}")
        except Exception as e:
            logger.warning(f"画面サイズの変更に失敗しました: {e!r}")
            return
        logger.info(f"画面サイズを{int(width)}x{int(height)}にしました。")

    def lockAspect(self, lock: bool) -> bool:
        """16:9 の縦横比固定を入/切し、実効状態を返す."""
        effective = self.app.set_window_aspect_lock(lock)
        if lock and effective:
            logger.info("縦横比の固定を有効にしました。")
        elif not lock:
            logger.info("縦横比の固定を解除しました。")
        return effective

    def OpenBconSetup(self) -> None:
        logger.debug("Open BconSetup window")
        bcon_window = getattr(self.bcon_setup, "window", None)
        if bcon_window is not None and self._alive(bcon_window):
            bcon_window.focus_force()
            return
        self.bcon_setup = None
        self.bcon_setup = BconSetup(self.root, self.ser)
        self.bcon_setup.window.protocol("WM_DELETE_WINDOW", self.closingBconSetup)

    def closingBconSetup(self) -> None:
        logger.debug("Close BconSetup window")
        if self.bcon_setup is not None:
            try:
                self.bcon_setup.close()
            except Exception:
                pass
            self.bcon_setup = None

    def OpenSerialMonitor(self) -> None:
        logger.debug("Open SerialMonitor window")
        mon_window = getattr(self.serial_monitor, "window", None)
        if mon_window is not None and self._alive(mon_window):
            mon_window.focus_force()
            return
        self.serial_monitor = None
        self.serial_monitor = SerialMonitor(self.root, self.ser)
        self.serial_monitor.window.protocol(
            "WM_DELETE_WINDOW", self.closingSerialMonitor
        )

    def closingSerialMonitor(self) -> None:
        logger.debug("Close SerialMonitor window")
        if self.serial_monitor is not None:
            try:
                self.serial_monitor.close()
            except Exception:
                pass
            self.serial_monitor = None

    def closeAll(self) -> None:
        """子窓をすべて閉じる。終了処理から呼ぶ。

        開きっぱなしのまま root.destroy() へ進むと、破棄途中の
        ウィジェットを after 予約が触って TclError になる。
        """
        try:
            from ui import blockly_editor

            blockly_editor.stop_blockly_editor()
        except Exception:
            pass
        for name in (
            "bcon_setup",
            "key_config",
            "poke_treeview",
            "input_log_config",
        ):
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

    def OpenScriptInstall(self) -> None:
        """配布zipを選んで導入する。実手順は ui.script_pack_dialogs。"""
        from ui import script_pack_dialogs

        script_pack_dialogs.install_script_zip(
            self.root,
            WindowUtils.APP_DIR,
            is_busy=lambda: self.app.runner.is_busy(),
            reload_commands=self.app.reloadCommands,
        )

    def OpenScriptUninstall(self) -> None:
        """導入済みを選んで削除する。実手順は ui.script_pack_dialogs。"""
        from ui import script_pack_dialogs

        script_pack_dialogs.uninstall_script_dialog(
            self.root,
            WindowUtils.APP_DIR,
            is_busy=lambda: self.app.runner.is_busy(),
            reload_commands=self.app.reloadCommands,
        )

    def OpenBlocklyEditor(self) -> None:
        """Blocklyエディタを開く。実手順は ui.blockly_editor。"""
        from services import blockly_capture
        from ui import blockly_editor

        blockly_editor.open_blockly_editor(
            self.root,
            is_busy=lambda: self.app.runner.is_busy(),
            reload_commands=self.app.reloadCommands,
            get_frame=blockly_capture.build_get_frame(lambda: self.camera),
        )

    def OpenErrorReport(self) -> None:
        """エラー報告の小窓を開く。実手順は ui.error_report_dialog。"""
        import platform

        from ui import error_report_dialog

        app = self.app
        try:
            transport = str(app.transport_name.get())
        except Exception:
            transport = ""
        error_report_dialog.open_error_report(
            self.root,
            app_version=str(getattr(app, "app_version", "")),
            os_name=str(getattr(app, "os_name", platform.system())),
            python_version=platform.python_version(),
            profile=str(getattr(app, "profile", "")),
            transport=transport,
        )

    def exit(self) -> None:
        # 終了処理は Window.exit() に一本化する
        logger.debug("Close Menubar")
        self.app.exit()
