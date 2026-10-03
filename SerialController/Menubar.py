import os
import tkinter as tk
import webbrowser
from tkinter import messagebox
from typing import Any

import DiscordNotify
import WindowUtils
from InputLogConfig import InputLogConfig
from KeyConfig import PokeKeycon
from SerialMonitor import SerialMonitor
from get_pokestatistics import GetFromHomeGUI
from loguru import logger

# 使い方の置き場所（ヘルプ > ドキュメント / バージョン情報）。
DOCS_URL = "https://github.com/Moi-poke/Poke-Controller-Modified"

# ヘルプ > ショートカット一覧 に出す表。キーの束縛は ui/command_panel.py の
# _bind_keys が正。ここを変えるときはそちらと揃える。
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("F5", "コマンドを再読み込み"),
    ("F6", "コマンドを開始"),
    ("F7", "一時停止 / 再開"),
    ("Esc", "コマンドを停止"),
    ("Ctrl+K", "コマンドを選択"),
)


class PokeController_Menubar(tk.Menu):
    def __init__(self, master: Any, **kw: Any) -> None:
        # 親クラスを先に初期化する。tk.Menu.__init__ は self.master を
        # 親ウィジェット(root)で上書きするため、アプリ本体は self.app に持つ
        self.app = master
        tk.Menu.__init__(self, master.root, **kw)

        self.poke_treeview: Any | None = None
        self.key_config: PokeKeycon | None = None
        self.serial_monitor: SerialMonitor | None = None
        self.input_log_config: InputLogConfig | None = None
        self.display_settings: Any | None = None

        # 見出しは一般的なデスクトップアプリの並び（OBS 等の国内向け訳と同じ）。
        # (F) などは Alt キーから辿るための下線位置。
        self.menu_file = self._cascade("ファイル(F)")
        self.menu_command = self._cascade("コマンド(C)")
        self.menu_view = self._cascade("表示(V)")
        self.menu_connect = self._cascade("接続(N)")
        self.menu_tools = self._cascade("ツール(T)")
        self.menu_help = self._cascade("ヘルプ(H)")
        # 16:9 固定の入/切。チェックで今の状態が見えるようにする。
        self.aspect_locked = tk.BooleanVar(master=master.root, value=False)

        self.AssignMenuCommand()

    def _cascade(self, label: str) -> tk.Menu:
        """見出しを 1 つ足す。括弧の中の英字に下線を引く。"""
        menu = tk.Menu(self, tearoff=False)
        self.add(tk.CASCADE, menu=menu, label=label, underline=label.index("(") + 1)
        return menu

    def _app_call(self, name: str) -> Any:
        """app の入口を、押した時点で引く。

        Window 側で作り直される部品もあるので、組み立て時に束縛しない。
        """
        return lambda: getattr(self.app, name)()

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
    def keyboard(self) -> Any:
        return self.app.keyboard

    @property
    def settings(self) -> Any:
        return self.app.settings

    @property
    def camera(self) -> Any:
        return self.app.camera

    def AssignMenuCommand(self) -> None:
        """全メニューの項目を並べる。

        コマンド系はショートカットと同じ入口（StartCommandWithF6 など）を呼ぶ。
        入口が分かれると、片方だけ直したときに挙動が食い違うため。
        """
        logger.debug("Assigning menu command")
        call = self._app_call

        m = self.menu_file
        m.add_command(label="キャプチャを保存", command=call("saveCapture"))
        m.add_command(label="キャプチャフォルダを開く", command=call("OpenCaptureDir"))
        m.add_separator()
        m.add_command(label="設定フォルダを開く", command=self.OpenSettingsDir)
        m.add_command(label="ログフォルダを開く", command=self.OpenLogDir)
        m.add_separator()
        m.add_command(label="終了", command=self.exit)

        m = self.menu_command
        m.add_command(
            label="開始", accelerator="F6", command=call("StartCommandWithF6")
        )
        m.add_command(
            label="停止", accelerator="Esc", command=call("StopCommandWithEsc")
        )
        m.add_command(
            label="一時停止 / 再開",
            accelerator="F7",
            command=call("PauseCommandWithF7"),
        )
        m.add_command(
            label="再読み込み", accelerator="F5", command=call("ReloadCommandWithF5")
        )
        m.add_separator()
        m.add_command(
            label="コマンドを選択...",
            accelerator="Ctrl+K",
            command=call("openCommandPalette"),
        )
        m.add_command(label="コマンドフォルダを開く", command=call("OpenCommandDir"))
        m.add_separator()
        m.add_command(label="スクリプトの導入...", command=self.OpenScriptInstall)
        m.add_command(label="スクリプトの削除...", command=self.OpenScriptUninstall)
        m.add_command(label="Blocklyエディタ...", command=self.OpenBlocklyEditor)

        m = self.menu_view
        # プレビューの表示設定（モードと固定サイズ）。ウィンドウ全体の
        # サイズとは別の概念なので、頭に置いて区切る。
        m.add_command(label="表示設定...", command=self.OpenDisplaySettings)
        m.add_separator()
        # ウィンドウのレイアウト。複数台を 1 画面に並べるときに使う。
        # variable を共有して 1 組のラジオにする。
        for value, text in (
            ("standard", "標準"),
            ("compact", "コンパクト（複数台向け）"),
            ("preview", "プレビューのみ"),
        ):
            m.add_radiobutton(
                command=self._layout_command(value),
                label=text,
                value=value,
                variable=self.app.layout_mode,
            )
        m.add_separator()
        m.add_command(
            label="ウィンドウ 1280x720",
            command=lambda: self.applyWindowSize(1280, 720),
        )
        m.add_command(
            label="ウィンドウ 1920x1080",
            command=lambda: self.applyWindowSize(1920, 1080),
        )
        m.add_checkbutton(
            label="16:9 に固定",
            variable=self.aspect_locked,
            command=lambda: self.lockAspect(bool(self.aspect_locked.get())),
        )

        m = self.menu_connect
        m.add_command(label="シリアルポートを再接続", command=call("reloadSerialPort"))
        m.add_command(label="シリアルポートを切断", command=call("inactivateSerial"))
        m.add_separator()
        m.add_command(label="Bcon設定", command=self.OpenBconSetup)
        m.add_command(label="シリアルモニタ...", command=self.OpenSerialMonitor)
        m.add_separator()
        m.add_command(label="カメラを再読み込み", command=call("openCamera"))
        m.add_command(label="音声デバイスを再読み込み", command=call("reloadAudio"))

        m = self.menu_tools
        m.add_command(label="キーコンフィグ...", command=self.OpenKeyConfig)
        m.add_command(label="入力ログの書式...", command=self.OpenInputLogConfig)
        m.add_command(
            label="Discord通知の設定...", command=self.open_discord_notify_setting
        )
        m.add_command(label="Pokemon HOME 連携...", command=self.OpenPokeHomeCoop)

        m = self.menu_help
        m.add_command(label="ショートカット一覧", command=self.ShowShortcuts)
        m.add_command(label="ドキュメント", command=self.OpenDocs)
        m.add_separator()
        m.add_command(label="エラー報告をコピー...", command=self.OpenErrorReport)
        m.add_separator()
        m.add_command(label="バージョン情報", command=self.ShowAbout)

    def _layout_command(self, value: str) -> Any:
        """レイアウト 1 つ分の command。ループ変数を取り違えないよう閉じ込める。"""
        return lambda: self.app.applyLayout(value)

    def _os_name(self) -> str:
        import platform

        return str(getattr(self.app, "os_name", platform.system()))

    def OpenSettingsDir(self) -> None:
        """設定ファイル（settings*.ini）のある場所を開く。"""
        WindowUtils.openDirectory(WindowUtils.APP_DIR, self._os_name())

    def OpenLogDir(self) -> None:
        """ログの出力先を開く。PokeConLogger が ../log へ書くのと同じ場所。"""
        path = os.path.normpath(os.path.join(WindowUtils.APP_DIR, os.pardir, "log"))
        WindowUtils.openDirectory(path, self._os_name())

    def ShowShortcuts(self) -> None:
        """アプリ全体のショートカットを一覧で見せる。"""
        lines = [f"{key}\t{text}" for key, text in SHORTCUTS]
        messagebox.showinfo("ショートカット一覧", "\n".join(lines), parent=self.root)

    def OpenDocs(self) -> None:
        """リポジトリの README（使い方）をブラウザで開く。"""
        webbrowser.open(DOCS_URL)

    def ShowAbout(self) -> None:
        """バージョンと配布元を出す。エラー報告や質問の前に確認するため。"""
        version = str(getattr(self.app, "app_version", ""))
        messagebox.showinfo(
            "バージョン情報",
            f"Poke-Controller Modified {version}\n{DOCS_URL}",
            parent=self.root,
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

    def lockAspect(self, lock: bool) -> None:
        """16:9 の縦横比固定を入/切する。失敗は警告のみ。"""
        root: Any = self.root
        try:
            if lock:
                root.wm_aspect(16, 9, 16, 9)
            else:
                root.wm_aspect("", "", "", "")
        except Exception as e:
            logger.warning(f"縦横比の切替に失敗しました: {e!r}")
            return
        logger.info(f"縦横比の固定を{'有効' if lock else '解除'}にしました。")

    def OpenBconSetup(self) -> None:
        """Bcon設定タブを前面にする。別窓は開かない。

        タブは Transport が bcon 系のときだけ出る。出ていないときは、
        メニューが無反応に見えないよう理由を画面へ出す。
        """
        logger.debug("Select Bcon tab")
        if self.app.select_bcon_tab():
            return
        print(
            "Bcon設定は Transport を switch-bcon（または switch-bcon-proc）に"
            "すると、シリアルの隣のタブに出ます。"
        )

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
            "key_config",
            "poke_treeview",
            "input_log_config",
            "display_settings",
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

    def OpenDisplaySettings(self) -> None:
        """プレビューの表示設定（モードと固定サイズ）を開く。

        一度に1つだけ。既に開いていれば前面に出すだけで作り直さない。
        「参照が None か」だけでは破棄済みの窓を掴むので、OpenInputLogConfig
        と同じ生存判定をここでも使う。
        """
        logger.debug("Open DisplaySettings window")
        from ui import display_settings

        if self.display_settings is not None and self._alive(self.display_settings):
            self.display_settings.lift()
            return
        self.display_settings = None
        # 色変数は UI 構築が済めば必ずあるが、偽 app（テスト用）に無い
        # ときのために getattr で守る（OpenKeyConfig と同じ方針）。
        color_var = getattr(self.app, "profile_color", None)
        dialog = display_settings.DisplaySettingsDialog(
            self.root,
            mode=self.app.show_mode.get(),
            size=self.app.show_size.get(),
            on_apply=self.app.applyDisplaySettings,
            color=color_var.get() if color_var is not None else "",
        )
        # 閉じたら参照を捨てる。Destroy は子にも飛ぶので親自身だけ拾う。
        dialog.bind("<Destroy>", self._on_display_settings_destroyed, add="+")
        self.display_settings = dialog

    def _on_display_settings_destroyed(self, event: Any) -> None:
        """表示設定の窓が破棄されたときの後始末。"""
        if event.widget is self.display_settings:
            self.display_settings = None

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
