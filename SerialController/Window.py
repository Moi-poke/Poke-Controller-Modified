#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Window.py - Poke-Controller Modified のメインウィンドウ.

PokeControllerApp : アプリ本体の組立。画面の部品ごとの手順は
ui/ の Mixin（camera / serial / command / log の各パネル）が持ち、
実行と接続の手順は services/ が持つ。ここに残すのは起動の組み立て・
設定の出し入れ・終了処理だけ。
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import threading
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from tkinter import Tk
from typing import Any

import LogPane
import Settings
import WindowGeometry
import WindowUtils
import cv2
from GuiAssets import CaptureArea, ControllerGUI
from Menubar import PokeController_Menubar
from core import CommandStats, PokeConLogger
from core.Camera import Camera
from loguru import logger
from services.audio_service import AudioService
from services.command_runner import CommandRunner
from services.serial_service import SerialService
from ui.audio_panel import AudioPanelMixin
from ui.camera_panel import CameraPanelMixin
from ui.command_panel import CommandPanelMixin
from ui.log_panel import LogPanelMixin
from ui.serial_panel import SerialPanelMixin

NAME = "Poke-Controller"
VERSION = "v4.0.1 Modified"  # based on 1.0-beta3(custom by @dragonite303)


# タイトルに出すコマンド名の上限。長い名前でウィンドウ名が埋まるのを防ぐ
TITLE_COMMAND_MAX = 20


# すべてのパスをこの1点から解決する。起動する場所（カレント
# ディレクトリ）が変わっても同じ場所を指すようにするため。
# 相対パスのままだと、別ディレクトリから絶対パスで起動した場合や
# パッケージ化した場合に、あるはずのフォルダを見つけられない。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class PokeControllerApp(
    CameraPanelMixin,
    AudioPanelMixin,
    SerialPanelMixin,
    CommandPanelMixin,
    LogPanelMixin,
):
    """メインウィンドウ（組立専用）。

    画面の部品ごとの手順は ui/ の Mixin が持つ。ここに残すのは
    起動の組み立て・設定の出し入れ・終了処理だけ。
    """

    def __init__(
        self, master: Tk | None = None, profile: str = "", transport: str = ""
    ) -> None:
        """profile を渡すと設定・共有メモリを分けて並列起動できる。

        transport は通信方式のプリセット名。
          起動引数（--transport）から渡す。空なら settings.ini の
          [Transport] name を使う。引数のほうが強い（その場かぎりで
          試したいときに、設定を書き換えずに済ませるため）。
        """
        if master is None:
            master = Tk()
        self.root = master
        # 起動引数で指定された通信方式。設定より優先する
        self._transport_override = str(transport).strip()
        # どの台のウィンドウか一目で分かるようタイトルに出す。
        self.profile = Settings.GuiSettings.sanitize_profile(profile)
        # 報告窓のために版情報を持たせる。ui 側から Window 本体を
        # import せずに済ませるため（循環防止）。
        self.app_name = NAME
        self.app_version = VERSION
        self._running_command = ""  # タイトルに出す実行中コマンド名
        self._paused = False  # 一時停止中か（タイトル表示に使う）
        self.root.title(f"{NAME} {VERSION}")  # UI 構築後に詳細版へ更新する

        self._init_state()
        self._build_ui()

        # 標準出力をログエリアにリダイレクト
        sys.stdout = LogPane.QueueStdoutRedirector(self.logArea)
        self._display_after_id: Any = self.logArea.after(
            LogPane.FLUSH_INTERVAL_MS, self.display_text
        )
        self.loadSettings()
        self._apply_settings_to_widgets()
        self._setup_camera_name()

        self._start_camera()
        self._start_audio()
        self._start_serial()
        self._build_preview()
        self._update_title()

        self.loadCommands()
        self._bind_keys()

        self.mainwindow = self.frame_1
        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        # _build_preview が済んでいるため、ここでは必ずある。
        if self.preview is None:
            raise RuntimeError("preview is not built")
        self.preview.startCapture()

        self.menu = PokeController_Menubar(self)
        self.root.config(menu=self.menu)

    # ------------------------------------------------------------------
    # 状態
    # ------------------------------------------------------------------

    def _init_state(self) -> None:
        # Baud Rate は画面から変えられないようにしている（意図的な制限）。
        self.baud_rate_state = "disabled"
        self.os_name = platform.system()

        self.controller: ControllerGUI | None = None
        self.poke_treeview: Any = None
        self.camera: Camera | None = None
        self.audio_service = AudioService(notify_user=print)
        # 送り先と実行状態の所有者。実体は services が持つ。
        # Window は tk 変数の読み書きと見た目の反映だけを行う。
        # 窓のフォーカス有無（キーボード操作の門）。pynput は OS 全体の
        # 打鍵を拾うため、Listener 側では窓内外を区別できない。別スレッド
        # から Tk を触れないので、GUI 側が Event へ写し、述語だけ渡す。
        # 初期は下ろしておく（結線直後の即時同期で正される）。
        self._kb_window_active = threading.Event()
        self.serial = SerialService(
            notify_user=print,
            base_dir=BASE_DIR,
            input_log_emit=LogPane.emitInputLog,
            keyboard_active=self._kb_window_active.is_set,
        )
        self.serial.transport_override = self._transport_override
        # いま使っている通信方式の名前（画面表示と保存に使う）
        self.transport_name = tk.StringVar()
        # 入力の優先付けの設定（画面表示と保存に使う）。
        #   実体は送信側が持つ。ここは画面の選択値だけを持つ。
        self.arbitration_mode = tk.StringVar()
        self.preview: CaptureArea | None = None
        self.cur_command: Any = None
        self.py_cur_command: Any = None
        self.mcu_cur_command: Any = None
        # 表示名 → クラスの対応表。UI 構築より前に空で用意する
        self.py_map: dict[str, type] = {}
        self.mcu_map: dict[str, type] = {}
        # 表示名 → タグ一覧。絞り込みと表示の前置に使う
        self.py_tags: dict[str, list[str]] = {}
        self.mcu_tags: dict[str, list[str]] = {}
        self.py_all_names: list[str] = []  # 絞り込み前の全件（検索の母集合）
        self.mcu_all_names: list[str] = []
        # Combobox ごとの「表示(タグ前置つき) → 素の名前」。引くのは素の名前
        self._shown_names: dict[Any, dict[str, str]] = {}
        # タグ編集の小窓。二重に開かないよう参照を持つ
        self._tag_editor: tk.Toplevel | None = None
        # コマンド検索パレット（Ctrl+K）。二重に開かないよう参照を持つ
        self._palette: Any = None
        # 使用履歴（実行回数 / 最終実行日時）。書き出しは終了時に1回だけ
        self.command_stats: dict[str, dict] = CommandStats.load(self.profile)
        # 実行の状態機械。タイマーは root.after で GUI スレッドへ回し、
        # 見た目の反映は合図（_apply_runner_state）で受ける。
        self.runner = CommandRunner(
            stats=self.command_stats,
            schedule=self.root.after,
            cancel=self.root.after_cancel,
            notify_user=print,
            on_state_changed=self._apply_runner_state,
            on_list_refresh=self._refresh_after_run,
        )
        # 走行記録の受け先。SerialService が担い、runner が開始・終了で呼ぶ。
        # 記録の失敗で実行は壊さない（runner・service 側で握る）。
        self.runner.set_diag(self.serial, self.profile)
        self._closing = False
        # カメラ開きの直列化錠と発行番号。起動時は裏スレッドで開き、
        # Reload は表で開く。両方が同時に走ると DirectShow の open が
        # 食い違うため錠で直列化し、番号で古い結果を捨てる。
        self._camera_open_lock = threading.Lock()
        self._camera_open_seq = 0
        self.camera_dic: dict[int, str] | None = None
        # cam_id -> 表示名 / cam_id -> 識別子。同型ボードの区別に使う
        self.camera_keys: dict[int, str] = {}
        self._camera_labels: list[str] = []
        self.camera_key = tk.StringVar()
        self._display_after_id = None
        self._sash_after_id: Any = None
        self._sash_restore_attempts = 0

    @property
    def ser(self) -> Any:
        """送り先。実体は serial サービスが持つ。

        Menubar や ControllerGUI が読む覗き窓。書き換えは
        サービスの手順（接続・切断・切替）を通すこと。
        """
        return self.serial.sender

    @property
    def keyboard(self) -> Any:
        """キーボード操作の実体。実体は serial サービスが持つ。

        Menubar が読む覗き窓。寿命管理はサービスが行う。
        """
        return self.serial.keyboard

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.frame_1 = ttk.Frame(self.root)
        self._build_camera_frame()
        self._build_audio_frame()
        self._build_serial_frame()
        self._build_control_frame()
        self._build_command_frame()
        self._build_log_area()

        self.frame_1.config(height=720, padding=5, relief="flat", width=1280)
        self.frame_1.pack(expand=True, fill="both", side="top")
        self.frame_1.columnconfigure(3, weight=1)

    def loadSettings(self) -> None:
        self.settings = Settings.GuiSettings(self.profile)
        self.settings.load()

    def _apply_settings_to_widgets(self) -> None:
        """設定ファイルの値を各 tk 変数へ流し込む。"""
        self.is_show_realtime.set(self.settings.is_show_realtime.get())
        self.is_show_serial.set(self.settings.is_show_serial.get())
        self.is_use_keyboard.set(self.settings.is_use_keyboard.get())
        self.fps.set(str(self.settings.fps.get()))
        self.show_size.set(self.settings.show_size.get())
        self.com_port.set(self.settings.com_port.get())
        self.com_port_name.set(self.settings.com_port_name.get())
        self.camera_id.set(self.settings.camera_id.get())
        self.camera_key.set(self.settings.camera_key.get())
        self.camera_lf.is_use_left_stick_mouse.set(
            self.settings.is_use_left_stick_mouse.get()
        )
        self.camera_lf.is_use_right_stick_mouse.set(
            self.settings.is_use_right_stick_mouse.get()
        )

        # 入力ログの表示。settings.ini の [Input Log] enabled と対にする
        self.show_input_log.set(self.settings.input_log_enabled.get())
        # 表示フィルタのパラメータ（ON/OFFは持たず起動時は常にOFF）。
        self._applyFilterSettings()

        # 通信方式の候補と現在値。利用者定義のプラグインは
        #   候補を組む前に読み込む（読み込み後でないと一覧に出ない）。
        self._loadTransportPlugins()
        self._refreshTransportChoices()
        self._refreshArbitrationChoices()

        WindowUtils.selectCombobox(self.fps_cb, self.fps.get())
        WindowUtils.selectCombobox(self.show_size_cb, self.show_size.get())
        self.show_size_tmp = self.show_size_cb["values"].index(self.show_size_cb.get())

        # Baud Rate は候補で縛らない。GameCube の自動化や独自マイコンで
        # 9600 / 4800 以外を使う人がいる。設定にある値が候補に無ければ
        # 候補そのものへ足して選ぶ。矯正すると設定ファイルへ書き戻る
        # 経路があるため、利用者の値が起動のたびに消える。
        current_rate = str(self.settings.baud_rate.get())
        rates = [str(v) for v in WindowUtils.BAUD_RATE_VALUES]
        if current_rate not in rates:
            rates.append(current_rate)
            self.baud_rate_cb.config(values=rates)
        self.baud_rate.set(current_rate)

        # 旧 settings.ini は com_port(番号) しか持たないことがある。
        # 名前が空なら番号から COM<n> を組み立てて選び直せるようにする。
        if not self.com_port_name.get() and self.com_port.get():
            self.com_port_name.set(f"COM{self.com_port.get()}")
        # 一覧を取り込み、前回のポートがあればそれを選ぶ
        self.refreshComPorts()

        # 前回のウィンドウ位置とサイズを戻す
        self._restore_geometry()
        self._apply_audio_widgets()

        # ここまでは「設定を GUI へ流し込む」段階なので保存してはいけない。
        # 以降の変更（＝利用者の操作）だけを保存対象にする。
        self._settings_ready = True

    # ------------------------------------------------------------------
    # シリアル / キーボード
    # ------------------------------------------------------------------

    def _update_title(self) -> None:
        """ウィンドウ名を今の状態に合わせて組み立て直す。

        並列起動すると同じ名前の窓が並ぶため、タスクバーで見分けられる
        必要がある。タスクバーは先頭から表示が切れるので、見分けに要る
        情報ほど前へ置く（[プロファイル] → ポート → 実行状態 → アプリ名）。
        """
        parts = []
        if self.profile:
            parts.append(f"[{self.profile}]")

        # UI 構築前に呼ばれても落ちないよう getattr で取る
        port_var = getattr(self, "com_port_name", None)
        device = port_var.get() if port_var is not None else ""
        opened = self.serial.is_open()
        if device:
            parts.append(device if opened else f"{device}(未接続)")
        else:
            parts.append("未接続")

        if self._running_command:
            name = self._running_command
            if len(name) > TITLE_COMMAND_MAX:
                name = name[: TITLE_COMMAND_MAX - 1] + "…"
            mark = "⏸" if self._paused else "▶"
            parts.append(f"{mark}{name}")

        # 停止を待っている間はそれを出す。ボタンが disabled のままな理由が
        # 画面から分かるようにするため（待つ以外の手は終了しかない）。
        waited = self.runner.stop_waited
        if waited:
            parts.append(f"⏳停止待ち {waited // 1000}秒")

        head = " ".join(parts)
        self.root.title(f"{head} - {NAME} {VERSION}")

    # ------------------------------------------------------------------
    # コマンド
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.debug("Start Poke-Controller")
        self.mainwindow.mainloop()

    def exit(self) -> None:
        """終了処理。入口が複数あるので、二重に走らせない。

        × ボタン（WM_DELETE_WINDOW）とメニューの「終了」の2経路があり、
        確認ダイアログを出しているあいだにもう一方から入れる。2回目は
        破棄済みのウィジェットを触るため TclError（bad window path name）
        になる。_closing は「終了処理を始めたか」の印なので、判定は
        ダイアログより前に置く。後ろに置くと、確認を待っている間に
        入って来た2回目を防げない。
        """
        if self._closing:
            logger.debug("exit() は既に実行中のため無視しました")
            return
        if not tkmsg.askyesno("確認", "Poke Controllerを終了しますか？"):
            return

        self._closing = True
        self.runner.notify_closing()
        # after の ID は「登録したウィジェット」に紐づく。別の
        # ウィジェットで after_cancel しても取り消せず、予約は生き残る。
        # display_text は logArea.after で、_restore_sash は root.after で
        # 登録しているので、取り消しも同じ相手へ頼む。取り消し漏れが
        # あると destroy の最中に発火し、破棄途中のウィジェットを触って
        # TclError（can't delete Tcl command）になる。
        # 停止の見張りの予約は runner が持つのでそちらで消す。
        self.runner.cancel_watch()
        for widget, after_id in (
            (self.logArea, self._display_after_id),
            (self.root, self._sash_after_id),
        ):
            if after_id is None:
                continue
            try:
                widget.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass
        self._display_after_id = None
        self._sash_after_id = None
        # 子窓（Wake設定・キーコンフィグ等）を先に閉じる。開きっぱなしの
        # まま destroy へ進むと、after 予約が破棄途中の窓を触る。
        try:
            if self.menu is not None:
                self.menu.closeAll()
        except Exception as e:
            logger.warning(f"子窓を閉じるときに例外: {e}")
        self.runner.shutdown(self.ser)

        self.serial.stop_keyboard()
        self.closingController()
        # 映像の描画ループをここで止める。CaptureArea のマウス操作
        # （LStick / RStick Mouse）は self.ser へ書くため、シリアルを
        # 閉じる前に「送る側」を止めておく。Unbind だけでは capture()
        # の周回自体は生き続け、閉じた口を持ったまま回ることになる。
        # 逆順にすると「閉じた先へ書きに行く」経路が残る。
        if self.preview is not None:
            try:
                self.preview.UnbindLeftClick()
                self.preview.UnbindRightClick()
            except Exception as e:
                logger.warning(f"マウス操作の解除で例外: {e}")
            try:
                self.preview.stopCapture()
            except Exception as e:
                logger.warning(f"映像の停止で例外: {e}")

        if self.serial.shutdown():
            print("Serial disconnected")

        # ウィンドウを壊す前に位置とサイズを控える（destroy 後は取れない）。
        # 仕切り位置もここで控える。drag 解放時には書いているが、最後に
        # 動かしたまま閉じた場合に備える。保存の失敗で終了を止めない。
        try:
            WindowGeometry.rememberSash(self.log_pane, self.settings)
        except Exception as e:
            logger.warning(f"仕切り位置の保存に失敗しました: {e}")
        self._remember_geometry()
        try:
            self._save_settings()
        except Exception as e:
            logger.warning(f"終了時の設定保存に失敗しました: {e}")
        # 使用履歴も同じ場所で書き出す。実行のたびに書きに行かない代わり、
        # ここを通らないと記録が残らないので、設定の保存と並べておく。
        if self.runner.stats_dirty:
            CommandStats.save(self.command_stats, self.profile)

        # 映像を止めたあとで解放する。順序を逆にすると解放済みメモリを読む
        if self.camera is not None:
            self.camera.destroy()
        try:
            self._stop_meter()
        except Exception as e:
            logger.warning(f"音声メーターの停止で例外: {e}")
        try:
            self.audio_service.shutdown()
        except Exception as e:
            logger.warning(f"音声の停止で例外: {e}")
        cv2.destroyAllWindows()

        # 標準出力を元に戻さないと、破棄済みウィジェットへ書きに行くことがある
        sys.stdout = sys.__stdout__

        logger.debug("Stop Poke Controller")
        self.root.destroy()

    def _save_settings(self) -> None:
        """GUI の現在値をすべて設定ファイルへ書き出す。

        「一部の項目だけ set して save()」を作らないこと。save() は
        self.setting を丸ごと組み直すため、set し忘れた項目は起動時に
        読んだ古い値で書き戻される。保存の入口は必ずここに集約する。
        """
        self.settings.is_show_realtime.set(self.is_show_realtime.get())
        self.settings.is_show_serial.set(self.is_show_serial.get())
        self.settings.is_use_keyboard.set(self.is_use_keyboard.get())
        self.settings.is_use_left_stick_mouse.set(
            self.camera_lf.is_use_left_stick_mouse.get()
        )
        self.settings.is_use_right_stick_mouse.set(
            self.camera_lf.is_use_right_stick_mouse.get()
        )
        self.settings.fps.set(self._current_fps())
        self.settings.show_size.set(self.show_size.get())
        self.settings.com_port.set(self.com_port.get())
        self.settings.com_port_name.set(self.com_port_name.get())
        self.settings.baud_rate.set(self._currentBaudRate())
        self.settings.camera_id.set(self._cameraIdOrNone() or 0)
        self.settings.camera_key.set(self.camera_key.get())
        # 音声は表示名（"番号: 名前"）ではなく番号で保存する。
        # 表示のまま書くと次回開けない（同名重複の曖昧さ・est suffix）。
        self.settings.audio_input.set(self._selected_index(self.audio_input_name.get()))
        self.settings.audio_output.set(
            self._selected_index(self.audio_output_name.get())
        )
        self.settings.audio_monitor_enabled.set(self.audio_monitor.get())
        self.settings.audio_monitor_volume.set(self.audio_volume.get())
        # 表示フィルタはパネル側の辞書が正本。設定変数へ写して保存する。
        filt = self._filt_params
        self.settings.filt_gamma.set(float(filt["gamma"]))
        self.settings.filt_contrast.set(float(filt["contrast"]))
        self.settings.filt_brightness.set(int(filt["brightness"]))
        self.settings.filt_saturation.set(float(filt["saturation"]))
        self.settings.filt_hue_shift.set(int(filt["hue_shift"]))
        self.settings.filt_lower_h.set(int(filt["lower"][0]))
        self.settings.filt_lower_s.set(int(filt["lower"][1]))
        self.settings.filt_lower_v.set(int(filt["lower"][2]))
        self.settings.filt_upper_h.set(int(filt["upper"][0]))
        self.settings.filt_upper_s.set(int(filt["upper"][1]))
        self.settings.filt_upper_v.set(int(filt["upper"][2]))
        self.settings.filt_mode.set(str(filt["mode"]))
        self.settings.input_log_enabled.set(self.show_input_log.get())
        # 通信方式。起動引数で一時的に替えている場合も、
        #   画面に出ている値＝実際に使っている値なのでそのまま保存する。
        self.settings.transport_name.set(self.transport_name.get())
        self.settings.save()

    def _remember_geometry(self) -> None:
        """ウィンドウの位置とサイズを設定へ控える。"""
        WindowGeometry.rememberGeometry(self.root, self.settings)

    def _restore_geometry(self) -> None:
        """前回のウィンドウ位置とサイズを復元する。"""
        WindowGeometry.restoreGeometry(self.root, self.settings)

    def _on_setting_changed(self, *event: Any) -> None:
        """GUI の設定が変わったら即座に書き出す。

        保存を終了時だけにすると、強制終了・クラッシュ・OS のシャット
        ダウンで設定が丸ごと失われる。チェックを入れた時点で書いておけば、
        どう終わっても次回に持ち越せる。書き込みは Settings 側が一時
        ファイル＋os.replace の原子的置換なので、途中で落ちても壊れない。
        """
        if not getattr(self, "_settings_ready", False):
            # UI 構築中・設定流し込み中は書かない（既定値で上書きしてしまう）
            return
        # 保存の失敗で操作そのものを止めない。チェックを1つ入れた拍子に
        # 例外が上がると、そのウィジェットのコールバックが切れて以後
        # 反応しなくなる。書けなかったことはログへ出し、操作は続ける。
        try:
            self._save_settings()
        except Exception as e:
            logger.warning(f"設定の保存に失敗しました: {e}")
            print(f"設定の保存に失敗しました: {e}")


def main(argv: list[str] | None = None) -> None:
    """アプリの起動口（エントリ集約）。

    起動場所に依存せず、このファイルのある場所を作業ディレクトリにする。
    Commands や Captures を相対で開く箇所が残っているため、ここで揃える。
    `python Window.py` と `python -m SerialController`（__main__.py 経由）
    のどちらからもここへ来る。launcher.py は従来どおり Window.py を叩く。
    """
    # chdir は起動時に1度だけ。コマンドのリロード等では行わない。
    os.chdir(BASE_DIR)

    PokeConLogger.root_logger()
    logger.info("The root logger is created.")

    parser = argparse.ArgumentParser(description=NAME)
    parser.add_argument(
        "--profile",
        default="",
        help="設定と共有メモリを分ける名前。複数台を並列起動するときに指定する"
        "（例: --profile switch1）。未指定なら従来どおり settings.ini を使う。",
    )
    parser.add_argument(
        "--transport",
        default="",
        help="通信方式のプリセット名（例: --transport legacy_text）。"
        "設定ファイルより優先する。未指定なら settings.ini の [Transport] "
        "name を使う。使える名前は Transport.py の登録簿にあるもの。",
    )
    args = parser.parse_args(argv)

    app = PokeControllerApp(profile=args.profile, transport=args.transport)

    # 未捕捉の例外を報告窓へ回す。ワーカー内は各所が握って
    # ファイルへ書くため、ここでは GUI スレッド等の未捕捉だけ扱う。
    try:
        from ui import error_report_dialog

        def _report_info() -> dict[str, str]:
            try:
                transport = str(app.transport_name.get())
            except Exception:
                transport = ""
            return {
                "app_version": str(getattr(app, "app_version", VERSION)),
                "os_name": str(getattr(app, "os_name", platform.system())),
                "python_version": platform.python_version(),
                "profile": str(getattr(app, "profile", "")),
                "transport": transport,
            }

        error_report_dialog.install_hooks(app.root, _report_info)
    except Exception:
        logger.warning("エラー報告フックを登録できませんでした")

    app.run()


if __name__ == "__main__":
    main()
