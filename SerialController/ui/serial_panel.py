#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_panel.py - シリアル枠・操作枠の組み立てと接続手順の画面側。

PokeControllerApp に混ぜて使う（多重継承）。開く手順そのものは
services.SerialService が持ち、ここでは確認ダイアログ・tk 変数の
読み書き・見た目の反映だけを行う。
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import tkinter.messagebox as tkmsg
import tkinter.ttk as ttk
from typing import Any

import WindowUtils
from GuiAssets import ControllerGUI
from loguru import logger
from services.serial_service import SenderSpec, SerialService

# STATUS flags の bit5=有線。SSOTは Switch-bcon の spec/protocol_v3.md。
_STATUS_FLAG_WIRED = 0x20


class SerialPanelMixin:
    """シリアルパネルMixin。単体では使わない。"""

    frame_1: Any
    tab_serial: Any
    tab_controller: Any
    root: Any
    settings: Any
    os_name: str
    serial: SerialService
    transport_name: Any
    arbitration_mode: Any
    serial_lf: Any
    com_port_label: Any
    com_port: Any
    com_port_name: Any
    com_port_text: Any
    _com_port_map: dict[str, str]
    com_port_cb: Any
    baud_rate_label: Any
    baud_rate: Any
    baud_rate_cb: Any
    baud_rate_state: str
    reloadComPort: Any
    disconnectComPort: Any
    separator_4: Any
    is_show_serial: Any
    cb_show_serial: Any
    transport_label: Any
    transport_cb: Any
    arbitration_label: Any
    arbitration_cb: Any
    player_lamp_label: Any
    player_lamp_canvas: Any
    rumble_label: Any
    _player_lamp_items: list
    _player_lamp_after: Any
    _player_info_cache: bytes
    _rumble_cache: bytes
    _player_lamp_unsub: Any
    _player_lamp_transport: Any
    bcon_row: Any
    bcon_wired_label: Any
    bcon_wired: Any
    bcon_wired_rb_wireless: Any
    bcon_wired_rb_wired: Any
    bcon_emulate_label: Any
    bcon_emulate: Any
    bcon_emulate_cb: Any
    control_lf: Any
    is_use_keyboard: Any
    _kb_window_active: Any
    cb_use_keyboard: Any
    cb_left_stick_mouse: Any
    cb_right_stick_mouse: Any
    simpleConButton: Any
    controller: Any
    preview: Any
    camera_lf: Any
    applyBaudRate: Any
    _on_setting_changed: Any
    _update_title: Any

    def _build_serial_frame(self) -> None:
        self.serial_lf = ttk.Labelframe(self.tab_serial)

        self.com_port_label = ttk.Label(self.serial_lf)
        self.com_port_label.config(text="COM Port: ")
        self.com_port_label.grid(padx="5", sticky="ew")

        self.com_port = tk.IntVar()
        self.com_port_name = tk.StringVar()
        # Combobox に出す表示名（'COM3: USB シリアル デバイス'）を持つ。
        # 実際に開くデバイス名は com_port_name、末尾の番号は com_port。
        # com_port_label は上の Label ウィジェットが使っているため別名にする。
        self.com_port_text = tk.StringVar()
        self._com_port_map: dict[str, str] = {}
        self.com_port_cb = ttk.Combobox(self.serial_lf)
        self.com_port_cb.config(
            state="readonly", textvariable=self.com_port_text, width=28
        )
        self.com_port_cb.grid(column=1, padx="5", row=0, sticky="ew")
        self.com_port_cb.bind("<<ComboboxSelected>>", self.onComPortSelected, add="")

        self.baud_rate_label = ttk.Label(self.serial_lf)
        self.baud_rate_label.config(text="Baud Rate: ")
        self.baud_rate_label.grid(column=2, padx="5", row=0, sticky="ew")

        self.baud_rate = tk.StringVar()
        self.baud_rate_cb = ttk.Combobox(self.serial_lf)
        # values の int 群は実行時に文字列化される。注釈だけの問題のため無視する。
        self.baud_rate_cb.config(  # type: ignore[call-overload]
            justify="right",
            state=self.baud_rate_state,
            textvariable=self.baud_rate,
            values=WindowUtils.BAUD_RATE_VALUES,
            width=6,
        )
        self.baud_rate_cb.grid(column=3, padx="5", row=0, sticky="ew")
        self.baud_rate_cb.bind("<<ComboboxSelected>>", self.applyBaudRate, add="")

        self.reloadComPort = ttk.Button(self.serial_lf)
        self.reloadComPort.config(text="Reload Port", command=self.reloadSerialPort)
        self.reloadComPort.grid(column=4, padx="5", row=0)

        self.disconnectComPort = ttk.Button(self.serial_lf)
        self.disconnectComPort.config(
            text="Disconnect Port", command=self.inactivateSerial
        )
        self.disconnectComPort.grid(column=5, padx="5", row=0)

        self.separator_4 = ttk.Separator(self.serial_lf)
        self.separator_4.config(orient="vertical")
        self.separator_4.grid(column=6, padx="5", row=0, sticky="ns")

        self.is_show_serial = tk.BooleanVar()
        self.cb_show_serial = ttk.Checkbutton(self.serial_lf)
        self.cb_show_serial.config(
            text="Show Serial",
            variable=self.is_show_serial,
            command=self._on_setting_changed,
        )
        self.cb_show_serial.grid(column=7, columnspan=2, padx="5", row=0, sticky="ew")

        # 通信方式の選択。
        #   候補は通信方式の一覧から引く。ここに名前を
        #     書き並べない（実装を足したのに画面に出ない、を防ぐ）。
        self.transport_label = ttk.Label(self.serial_lf)
        self.transport_label.config(text="Transport: ")
        self.transport_label.grid(column=0, padx="5", row=1, sticky="ew")

        self.transport_cb = ttk.Combobox(self.serial_lf)
        self.transport_cb.config(
            state="readonly", textvariable=self.transport_name, width=28
        )
        self.transport_cb.grid(column=1, padx="5", row=1, sticky="ew")
        self.transport_cb.bind("<<ComboboxSelected>>", self.applyTransport, add="")

        # 入力の優先付けの選択。候補は送信側の許可値から
        #   引く（画面に名前を書き並べない）。既定の off は本家と同じ
        #   挙動で、script を選ぶと実行中の手操作を断る。
        self.arbitration_label = ttk.Label(self.serial_lf)
        self.arbitration_label.config(text="入力調停: ")
        self.arbitration_label.grid(column=0, padx="5", row=3, sticky="ew")

        self.arbitration_cb = ttk.Combobox(self.serial_lf)
        self.arbitration_cb.config(
            state="readonly", textvariable=self.arbitration_mode, width=28
        )
        self.arbitration_cb.grid(column=1, padx="5", row=3, sticky="ew")
        self.arbitration_cb.bind("<<ComboboxSelected>>", self.applyArbitration, add="")

        # プレイヤーランプ（bcon の PLAYER_INFO を省スペース表示）。
        # Bcon設定小窓の4灯と同じく bit0=LED1..bit3=LED4 の横並び。
        # Transport行の右端へ置き、左の空きをコンボの伸びで吸う。
        self.player_lamp_label = ttk.Label(self.serial_lf)
        self.player_lamp_label.config(text="LED: ")
        self.player_lamp_label.grid(column=2, padx="5", row=1, sticky="e")

        self.player_lamp_canvas = tk.Canvas(
            self.serial_lf, width=86, height=18, highlightthickness=0
        )
        self.player_lamp_canvas.grid(column=3, padx="5", row=1, sticky="e")
        self._player_lamp_items = []
        for index in range(4):
            left = 2 + index * 21
            self._player_lamp_items.append(
                self.player_lamp_canvas.create_rectangle(
                    left,
                    2,
                    left + 14,
                    16,
                    fill="gray",
                    outline="#4D4D4D",
                )
            )
        self._player_lamp_after = None
        # 巡回が読む購読写し。受信スレッドが置き、Tkは読むだけ。
        # 未受信・差し替え直後は空（全消灯・振動:（なし））。
        self._player_info_cache = b""
        self._rumble_cache = b""
        self._player_lamp_unsub = None
        self._player_lamp_transport = None
        self.rumble_label = ttk.Label(self.serial_lf)
        self.rumble_label.config(text="振動: ")
        self.rumble_label.grid(column=4, padx="5", row=1, sticky="e")

        # bcon の有線/無線切替と種別切替。同行の内枠へ束ねる。
        # 内枠が列いっぱいに伸びるため、種別の右端はCOM欄の右端に揃う。
        # bcon接続時のみ有効。
        self.bcon_wired_label = ttk.Label(self.serial_lf)
        self.bcon_wired_label.config(text="接続方式: ")
        self.bcon_wired_label.grid(column=0, padx="5", row=2, sticky="ew")

        self.bcon_row = ttk.Frame(self.serial_lf)
        self.bcon_row.grid(column=1, padx="5", row=2, sticky="ew")

        self.bcon_wired = tk.StringVar(value="無線")
        self.bcon_wired_rb_wireless = ttk.Radiobutton(
            self.bcon_row,
            text="無線",
            value="無線",
            variable=self.bcon_wired,
            command=self.applyBconWired,
        )
        self.bcon_wired_rb_wireless.pack(side="left")
        self.bcon_wired_rb_wired = ttk.Radiobutton(
            self.bcon_row,
            text="有線",
            value="有線",
            variable=self.bcon_wired,
            command=self.applyBconWired,
        )
        self.bcon_wired_rb_wired.pack(side="left")

        self.bcon_emulate_label = ttk.Label(self.bcon_row)
        self.bcon_emulate_label.config(text="種別: ")
        self.bcon_emulate_label.pack(side="left", padx=(8, 0))

        self.bcon_emulate = tk.StringVar(value="Pro Controller")
        self.bcon_emulate_cb = ttk.Combobox(self.bcon_row)
        self.bcon_emulate_cb.config(
            state="readonly",
            textvariable=self.bcon_emulate,
            width=14,
            values=("Pro Controller", "Joy-Con (L)", "Joy-Con (R)"),
        )
        self.bcon_emulate_cb.pack(side="left", fill="x", expand=True)
        self.bcon_emulate_cb.bind("<<ComboboxSelected>>", self.applyBconEmulate, add="")
        # 左のコンボ列を伸ばし、右端の表示・切替へ寄せる。
        self.serial_lf.columnconfigure(1, weight=1)

        self.serial_lf.config(text="Serial Settings")
        self.serial_lf.pack(fill="both", expand=True, padx=5, pady=5)
        self._refresh_bcon_rows()
        self._poll_player_lamp()

    def _build_control_frame(self) -> None:
        self.control_lf = ttk.Labelframe(self.tab_controller)

        self.is_use_keyboard = tk.BooleanVar()
        self.cb_use_keyboard = ttk.Checkbutton(self.control_lf)
        self.cb_use_keyboard.config(
            text="Use Keyboard",
            variable=self.is_use_keyboard,
            command=self._on_keyboard_toggled,
        )
        self.cb_use_keyboard.grid(column=0, padx="10", pady="5", sticky="ew")

        self.cb_left_stick_mouse = ttk.Checkbutton(self.control_lf)
        self.cb_left_stick_mouse.config(
            text="Use LStick Mouse",
            variable=self.camera_lf.is_use_left_stick_mouse,
            command=self._on_left_stick_toggled,
        )
        self.cb_left_stick_mouse.grid(column=1, row=0, padx="10", pady="5", sticky="ew")

        self.cb_right_stick_mouse = ttk.Checkbutton(self.control_lf)
        self.cb_right_stick_mouse.config(
            text="Use RStick Mouse",
            variable=self.camera_lf.is_use_right_stick_mouse,
            command=self._on_right_stick_toggled,
        )
        self.cb_right_stick_mouse.grid(
            column=1, row=1, padx="10", pady="5", sticky="ew"
        )

        self.simpleConButton = ttk.Button(self.control_lf)
        self.simpleConButton.config(
            text="Controller", command=self.createControllerWindow
        )
        self.simpleConButton.grid(column=0, padx="10", pady="5", row=1, sticky="ew")

        self.control_lf.config(height="200", text="Controller")
        self.control_lf.pack(fill="both", expand=True, padx=5, pady=5)

    def _currentBaudRate(self) -> int:
        """Baud Rate を通常の int で返す。数値として読めないときだけ既定。

        BAUD_RATE_VALUES は Combobox に出す「よく使う値」であって、
        使ってよい値の一覧ではない。GameCube の自動化では別の速度を
        使うし、独自のマイコンを載せている人はもっと速い値を入れる。
        候補に無いことを理由に書き換えてはいけない。設定ファイルへ
        書き戻す経路があるので、矯正すると利用者の設定が壊れて
        元の値も分からなくなる。

        ここで守るのは「数値として読めること」だけ。読めない値を
        そのまま流すと接続の途中で例外になり、開かない理由も出ない。
        """
        try:
            return int(self.baud_rate.get())
        except (TypeError, ValueError, tk.TclError):
            fallback = WindowUtils.BAUD_RATE_VALUES[0]
            logger.warning(f"Baud Rate を数値として読めないため {fallback} を使います")
            return fallback

    def refreshComPorts(self, keep_current: bool = True) -> None:
        """ポート一覧を取り直して Combobox へ入れる。"""
        before = self.com_port_name.get()
        ports = WindowUtils.listComPorts()
        self._com_port_map = {label: device for device, label in ports}
        labels = [label for _, label in ports] or [WindowUtils.COM_PORT_NOT_FOUND]
        self.com_port_cb["values"] = labels

        # 抜き差ししても選び直さずに済むよう、同じデバイスがあれば残す
        target = None
        if keep_current and before:
            for label, device in self._com_port_map.items():
                if device == before:
                    target = label
                    break
        self.com_port_text.set(target or labels[0])
        self._apply_selected_port()

    def _apply_selected_port(self) -> None:
        """選択中の表示名から、実際に開くデバイス名と番号を決める。"""
        device = self._com_port_map.get(self.com_port_text.get(), "")
        self.com_port_name.set(device)
        self.com_port.set(WindowUtils.portToNumber(device))
        self._update_title()

    def onComPortSelected(self, event: Any = None) -> None:
        """ポートを選んだ時点で設定へ書く。

        以前は接続に成功したときだけ保存していた。選んだが繋がなかった
        場合・接続に失敗した場合・選んだ直後に落ちた場合は残らず、
        他の設定が即時保存なのにここだけ挙動が違っていた。
        """
        self._apply_selected_port()
        self._on_setting_changed()

    def reloadSerialPort(self) -> None:
        """一覧を取り直してから接続し直す（Reload Port ボタン）。

        Keyboard の停止と再生成は activateSerial 側へ集約したので、
        ここでは行わない。二重に止めても害は無いが、同じ手順が2か所に
        あると片方だけ直す事故が起きる。
        """
        self.refreshComPorts()
        self.activateSerial()

    def _sender_spec(self) -> SenderSpec:
        """tk 変数の現在値を通常の値へ落として Sender 生成用にまとめる。

        Sender（とその先の worker）は tk 変数を読まない。ここで読むのは
        GUI スレッドのいまこの瞬間だけ。以後は Tk と無関係な値になる。
        """
        # 記録する操作の絞り込み。空なら書式ごとの既定に任せる
        actions = self.settings.input_log_actions.get().strip()
        return SenderSpec(
            transport_name=self._selectedTransportName(),
            is_show_serial=self.is_show_serial,
            arbitration_mode=self.arbitration_mode.get(),
            arbitration_cooldown=self._arbitrationCooldown(),
            input_log_format=self.settings.input_log_format.get(),
            input_log_actions=actions,
            input_log_enabled=self.settings.input_log_enabled.get(),
            input_log_stick_change=self.settings.input_log_stick_change.get(),
            # ライブ入力の最低保持ミリ秒（ini＋既定のみ）
            live_min_dwell_ms=self.settings.live_min_dwell_ms.get(),
        )

    def _apply_input_log_settings(self) -> None:
        """入力ログの設定を Sender へ反映する。

        書式の解釈はサービス側。メニューの「入力ログの書式」から
        呼ばれると、開いている最中でもすぐ反映される。
        """
        self.serial.apply_input_log(
            self.settings.input_log_format.get(),
            self.settings.input_log_actions.get(),
            self.settings.input_log_enabled.get(),
            self.settings.input_log_stick_change.get(),
        )

    # ------------------------------------------------------------------
    # 通信方式のプリセット
    # ------------------------------------------------------------------
    # 運び方を差し替えられる形は作ったが、選ぶ手段が無かった。
    #   ここで設定・起動引数・画面の3つから名前で選べるようにする。
    #   本体は実装を知らない。名前を登録簿へ渡すだけ。

    def _selectedTransportName(self) -> str:
        """これから使う通信方式の名前を決める（起動引数 > 設定ファイル）。"""
        return self.serial.selected_transport_name(self.settings.transport_name.get())

    def _loadTransportPlugins(self) -> None:
        """利用者が置いた自作の Transport を読み込む。"""
        self.serial.load_plugins(self.settings.transport_plugin_dir.get())

    def _refreshTransportChoices(self) -> None:
        """選択欄の候補を登録簿から組み直し、現在値を選ぶ。"""
        names = self.serial.list_transports()
        self.transport_cb.config(values=names)
        current = self._selectedTransportName()
        self.transport_name.set(current)

    def applyTransport(self, event: Any = None) -> None:
        """選択された通信方式へ差し替えて開き直す。"""
        name = self.serial.resolve_transport(self.transport_name.get())
        # 解決後の名前を画面へ戻す。知らない名前を選んだまま残さない
        self.transport_name.set(name)
        # 画面から選び直した以上、起動引数の指定はもう効かせない
        self.serial.clear_override()
        if self.serial.sender is None:
            self._on_setting_changed()
            return
        switched, _linked = self.serial.switch_transport(name, transport_logger=logger)
        if not switched:
            return
        # 線は閉じられているので開き直す（従来どおり繋がった状態に戻す）
        self.activateSerial()
        self._on_setting_changed()

    # 入力の優先付け（誰の操作を優先するか）
    #   既定は off で本家と同じ挙動。画面から選び直せる。

    def _refreshArbitrationChoices(self) -> None:
        """選択欄の候補を許可値から組み直し、現在値を選ぶ。"""
        self.arbitration_cb.config(values=list(self.serial.arbitration_modes()))
        mode = self.serial.resolve_arbitration(
            self.settings.arbitration_mode.get(), logger=logger
        )
        self.arbitration_mode.set(mode)

    def _arbitrationCooldown(self) -> float:
        """設定の秒数を数へ直す。読めない値は既定の 2 秒として扱う。"""
        try:
            return max(0.0, float(self.settings.arbitration_cooldown.get()))
        except (TypeError, ValueError):
            logger.warning("入力調停の cooldown を読めません。2.0 秒とします")
            return 2.0

    def applyArbitration(self, event: Any = None) -> None:
        """選ばれた入力調停を Sender へ反映する。

        線を開いていなくても選べる。次に開いたときへ効かせるため、
        画面の値は設定へ残す。Sender があればその場で適用する。
        """
        mode = self.serial.resolve_arbitration(
            self.arbitration_mode.get(), logger=logger
        )
        # 解決後の名前を画面へ戻す。知らない名前を選んだまま残さない
        self.arbitration_mode.set(mode)
        self.settings.arbitration_mode.set(mode)
        self.serial.set_arbitration(mode=mode, cooldown=self._arbitrationCooldown())
        message = f"入力調停を {mode} に切り替えました。"
        if mode == "script":
            message += "　実行中の手操作は断ります（一時停止すれば操作できます）。"
        elif mode == "human":
            message += "　手で触った直後はスクリプトの操作を断ります。"
        else:
            message += "　どちらも断りません（本家と同じ挙動）。"
        print(message)
        logger.info(message)
        self._on_setting_changed()

    def _bcon_transport(self) -> Any:
        """bcon接続中の運搬器。bcon以外・未接続はNone。

        名前ではなく能力で見る。別プロセス版（switch-bcon-proc）
        も同じ口を持つためここで拾う。
        """
        from core.transport.base import BCON_STATE

        sender = getattr(self.serial, "sender", None)
        transport = getattr(sender, "transport", None)
        if getattr(transport, "name", "") == "bcon":
            return transport
        try:
            if getattr(transport, "capability", "") == BCON_STATE:
                return transport
        except Exception:
            pass
        return None

    def _bcon_only_widgets(self) -> tuple:
        """bcon選択時だけ見せる部品。legacy系に無いLED・切替が対象。"""
        parts: list[Any] = [
            self.player_lamp_label,
            self.player_lamp_canvas,
            self.bcon_wired_label,
            self.bcon_row,
        ]
        rumble = getattr(self, "rumble_label", None)
        if rumble is not None:
            parts.append(rumble)
        return tuple(parts)

    def _refresh_bcon_rows(self) -> None:
        """bcon行の有効・無効を接続状態に合わせる。

        bcon以外では選べないうえ、行自体を隠す（legacy系に無い
        機能を見せない）。grid_remove/grid の対で戻す。
        """
        show = self._bcon_transport() is not None
        try:
            self.bcon_emulate_cb.config(state="readonly" if show else "disabled")
            rb_state = "normal" if show else "disabled"
            self.bcon_wired_rb_wireless.config(state=rb_state)
            self.bcon_wired_rb_wired.config(state=rb_state)
        except Exception:
            pass
        for widget in self._bcon_only_widgets():
            try:
                if show:
                    widget.grid()
                else:
                    widget.grid_remove()
            except Exception:
                pass

    def _sync_bcon_display(self) -> None:
        """接続直後に有線/無線の表示を実機へ合わせる。取得は別スレッド。

        開いた直後はHELLO往復中のため1回で届かないことがある。
        1秒おきに最大3回まで取り直す。
        """
        transport = self._bcon_transport()
        request = getattr(transport, "request_status", None)
        if transport is None or not callable(request):
            return

        def job() -> None:
            import time as _time

            # 購読を先に結ぶ。STATUS付随のPLAYER_INFOが購読なしでは
            # 画面の写しへ届かない（付随は変化時のみ送られるため、
            # 定常の実機では取りこぼすと灰色のまま残る）。
            try:
                self._ensure_player_lamp_subscription(transport)
            except Exception:
                pass
            for _ in range(3):
                try:
                    status = request(timeout=1.0)
                except Exception:
                    status = None
                try:
                    wired = bool(int(status.get("flags", 0)) & _STATUS_FLAG_WIRED)
                except (TypeError, ValueError, AttributeError):
                    _time.sleep(1.0)
                    continue
                # ランプ種付けはこの裏仕事で行う。Tk の巡回
                # （_poll_player_lamp）は購読写しを読むだけで、線へは
                # 取りに行かない。Tk を止めないよう取得はここだけに
                # 寄せ、画面への反映は after 経由の既存経路に任せる。
                # STATUS_REQ には PLAYER_INFO が付随する（付随分は購読
                # _on_bcon_rx_frame へ届く）。付随の無い応答では写しが
                # 空のまま残るため、到達済みの既定（全消灯）を置く。
                # 空でない写し（届き済みの真値）は上書きしない。
                # 見た目は空と同じ全消灯で、後続の購読で上書きされる。
                try:
                    if not bytes(getattr(self, "_player_info_cache", b"") or b""):
                        # 運搬器の保持に付随の真値があれば写す。購読なしで
                        # 届いた分（起動直前の定常分）を拾う。保持も空の
                        # ときだけ既定を置く。写しの読み書きは記憶域だけ
                        # で、Tk・線には触れない。
                        held = b""
                        reader = getattr(transport, "last_player_info", None)
                        if callable(reader):
                            try:
                                held = bytes(reader() or b"")
                            except Exception:
                                held = b""
                        self._player_info_cache = held if held else b"\x00"
                except (TypeError, ValueError):
                    pass
                try:
                    self.root.after(0, lambda: self._apply_bcon_wired_display(wired))
                except Exception:
                    pass
                return

        threading.Thread(target=job, daemon=True).start()

    def _apply_bcon_wired_display(self, wired: bool) -> None:
        try:
            self.bcon_wired.set("有線" if wired else "無線")
        except Exception:
            pass

    def applyBconWired(self, event: Any = None) -> None:
        """Bcon接続行で選んだ有線/無線を実機へ送る。"""
        transport = self._bcon_transport()
        if transport is None:
            print("bcon接続時のみ切替えられます。")
            self._refresh_bcon_rows()
            return
        enable = self.bcon_wired.get() == "有線"
        if (
            tkmsg.askquestion(
                "確認",
                f"{'有線(USB直結・BT停止)' if enable else '無線'}に切替えます。続けますか?",
            )
            != "yes"
        ):
            return
        setter = getattr(transport, "set_wired_mode", None)
        if not callable(setter):
            print("bcon接続時のみ切替えられます。")
            return
        self._run_bcon_setter("有線/無線切替", setter, (enable,), {"timeout": 1.0})

    def applyBconEmulate(self, event: Any = None) -> None:
        """Bcon接続行で選んだコントローラ種別を実機へ送る。"""
        transport = self._bcon_transport()
        names = ("Pro Controller", "Joy-Con (L)", "Joy-Con (R)")
        try:
            role = list(names).index(self.bcon_emulate.get())
        except ValueError:
            print("種別は一覧から選んでください。")
            return
        if transport is None:
            print("bcon接続時のみ切替えられます。")
            self._refresh_bcon_rows()
            return
        if (
            tkmsg.askquestion("確認", f"{names[role]}に切替えます。続けますか?")
            != "yes"
        ):
            return
        setter = getattr(transport, "set_emulate_mode", None)
        if not callable(setter):
            print("bcon接続時のみ切替えられます。")
            return
        self._run_bcon_setter("種別切替", setter, (role,), {"timeout": 1.0})

    def _run_bcon_setter(
        self, label: str, setter: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        """実機への切替 RPC を裏スレッドで走らせる。Tk は待たない。

        setter は子プロセス往復のためボタンから同期呼びすると Tk が
        数秒固まる。値は呼び出し前に Tk 側で読み切っているため、裏へ
        渡すのは素の値だけになる。反映（_refresh_bcon_rows）は Tk へ
        after(0) で戻す。終了後は捨てる。
        """

        def job() -> None:
            try:
                setter(*args, **kwargs)
            except Exception as e:
                # print はキュー経由のため裏からでも安全。利用者の目に
                # 届け、詳細はファイルへ残す。
                print(f"{label}に失敗しました: {e}")
                logger.warning(f"{label}に失敗しました: {e}")
            try:
                if getattr(self, "_closing", False):
                    return
                self.root.after(0, self._finish_bcon_setter)
            except Exception:
                pass

        threading.Thread(target=job, daemon=True).start()

    def _finish_bcon_setter(self) -> None:
        """裏での切替の反映。Tk スレッドで走る。"""
        if getattr(self, "_closing", False):
            return
        try:
            self._refresh_bcon_rows()
        except Exception:
            pass

    def _note_bcon_rpc(self, name: str, ms: float) -> None:
        # getter往復の写しを残すだけ（H1切り分け用・画面に触れない）。
        # 遅い往復だけdebugへ間引きで出す（既定では出ない）。
        try:
            last = getattr(self, "_bcon_rpc_last_ms", None)
            if not isinstance(last, dict):
                last = {}
                self._bcon_rpc_last_ms = last
            peak = getattr(self, "_bcon_rpc_max_ms", None)
            if not isinstance(peak, dict):
                peak = {}
                self._bcon_rpc_max_ms = peak
            value = float(ms)
            last[name] = value
            if value > float(peak.get(name, 0.0)):
                peak[name] = value
            if value >= 50.0:
                now = time.perf_counter()
                prev = float(getattr(self, "_bcon_rpc_log_t", 0.0))
                if now - prev >= 5.0:
                    self._bcon_rpc_log_t = now
                    logger.debug(f"bcon RPC遅延 {name}={value:.1f}ms")
        except Exception:
            pass

    def bcon_rpc_latency_ms(self) -> dict[str, float]:
        # 直近の往復ms写し。呼ぶだけで線・画面に触れない。
        try:
            raw = getattr(self, "_bcon_rpc_last_ms", None)
            return dict(raw) if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _on_bcon_rx_frame(self, frame: Any) -> None:
        """購読経路のframe写し。受信スレッドで走る。画面に触れない。

        本家・別プロセス版どちらの購読も (種別, payload, 連番) で来る。
        PLAYER_INFO(0x23)とRUMBLE(0x22)だけ写し、それ以外は捨てる。
        """
        try:
            ftype = int(frame[0]) & 0xFF
            payload = bytes(frame[1])
        except (TypeError, ValueError, IndexError):
            return
        try:
            if ftype == 0x23:
                self._player_info_cache = payload
            elif ftype == 0x22:
                self._rumble_cache = payload
        except Exception:
            pass

    def _ensure_player_lamp_subscription(self, transport: Any) -> None:
        """購読の付け替え。Tkで走るが線への往復はしない。

        運搬器が変わったときだけ繋ぎ直す。替わった側の古い写しは捨てる
        （前の運搬器の表示を残さない）。購読口の無い運搬器は写し捨てのみ。
        """
        try:
            current = getattr(self, "_player_lamp_transport", None)
        except Exception:
            current = None
        if transport is current:
            return
        try:
            unsub = getattr(self, "_player_lamp_unsub", None)
        except Exception:
            unsub = None
        if callable(unsub):
            try:
                unsub()
            except Exception:
                pass
        try:
            self._player_lamp_unsub = None
            self._player_lamp_transport = transport
            self._player_info_cache = b""
            self._rumble_cache = b""
        except Exception:
            pass
        if transport is None:
            return
        subscribe = getattr(transport, "subscribe_rx", None)
        if not callable(subscribe):
            return
        try:
            self._player_lamp_unsub = subscribe(self._on_bcon_rx_frame)
        except Exception:
            try:
                self._player_lamp_unsub = None
            except Exception:
                pass

    def _cancel_player_lamp_patrol(self) -> None:
        """巡回の予約を取り消す。終了・破棄の前に呼ぶ。"""
        try:
            after_id = getattr(self, "_player_lamp_after", None)
        except Exception:
            after_id = None
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except Exception:
            pass
        try:
            self._player_lamp_after = None
        except Exception:
            pass

    def _poll_player_lamp(self) -> None:
        """購読写しの反映だけ行う。500ms毎の画面側巡回。

        Tkから線への往復はしない。未受信の写しは空のまま
        （全消灯・振動:（なし））で置く。
        """
        try:
            transport = self._bcon_transport()
        except Exception:
            transport = None
        try:
            self._ensure_player_lamp_subscription(transport)
        except Exception:
            pass
        try:
            try:
                payload = bytes(getattr(self, "_player_info_cache", b"") or b"")
            except (TypeError, ValueError):
                payload = b""
            lamp = payload[0] if len(payload) >= 1 else 0
            try:
                value = int(lamp) & 0x0F
            except (TypeError, ValueError):
                value = 0
            for index, item in enumerate(self._player_lamp_items):
                color = "green yellow" if (value >> index) & 1 else "gray"
                self.player_lamp_canvas.itemconfigure(item, fill=color)
            try:
                rumble = bytes(getattr(self, "_rumble_cache", b"") or b"")
            except (TypeError, ValueError):
                rumble = b""
            try:
                from core.transport.bcon import decode_rumble

                left, right = decode_rumble(rumble)
                left, right = int(left) & 0xFF, int(right) & 0xFF
            except Exception:
                left, right = 0, 0
            label = getattr(self, "rumble_label", None)
            if label is not None:
                try:
                    if rumble:
                        label.config(text=f"振動: L={left} R={right}")
                    else:
                        label.config(text="振動: （なし）")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._player_lamp_after = self.root.after(500, self._poll_player_lamp)
        except Exception:
            self._player_lamp_after = None

    def _start_serial(self) -> None:
        self.serial.build_sender(self._sender_spec(), transport_logger=logger)
        self.activateSerial()

    def activateSerial(self) -> None:
        """ポートを開く。既に開いていれば閉じてから開き直す。

        開く手順そのものは serial サービスが行う。ここでは開く前の
        確認（Baud Rate）と、開いたあとの画面の辻褄だけを見る。
        """
        if self.baud_rate.get() == "4800":
            ret = tkmsg.askquestion(
                "確認",
                "Baud Rateを4800にすると動かなくなる可能性があります。\n変更しますか？",
            )
            if ret != "yes":
                self.baud_rate_cb.set(value=9600)
                return

        connected, keyboard_active = self.serial.connect(
            port_num=self.com_port.get(),
            port_name=self.com_port_name.get(),
            baud=self._currentBaudRate(),
            keyboard_enabled=self.is_use_keyboard.get(),
            setting_path=self.settings.setting_path,
        )
        # 開けたときだけキーボードのチェックを残す。失敗時にチェックが
        # 入ったまま実体が無い状態にはならない。
        # 一部だけ settings へ入れて save() すると、他の項目は起動時の
        # 古い値のまま書き戻される。常に全項目を集めてから書く
        # _on_setting_changed() に一本化する。
        self.is_use_keyboard.set(connected and keyboard_active)
        # 接続経路でもキーボードだけが生きるため、ここで結線する。
        # チェック切替時だけだと、繋ぎ直した回は監視なしになる。
        if connected and keyboard_active:
            self._bindKeyboardFocus()
        else:
            self._unbindKeyboardFocus()
        self._refresh_bcon_rows()
        if connected:
            self._sync_bcon_display()
        self._on_setting_changed()
        self._update_title()

    def inactivateSerial(self) -> None:
        """ポートを閉じる（Disconnect Port ボタン）。

        画面のチェックも外す。入ったままだと「有効なのに効かない」
        状態になり、次に接続したとき勝手に動き出したように見える。
        """
        self.serial.disconnect()
        self._unbindKeyboardFocus()
        self.is_use_keyboard.set(False)
        self._refresh_bcon_rows()
        self._on_setting_changed()
        self._update_title()

    def activateKeyboard(self) -> None:
        """キーボード操作の有効・無効を切り替える。

        実体の寿命は serial サービスが持つ。ここでは画面の辻褄
        （チェックの巻き戻し・フォーカス追従の結線）だけを見る。
        フォーカス追従は全 OS で行う（Tk の FocusIn/Out は共通）。
        """
        if self.is_use_keyboard.get():
            err = self.serial.set_keyboard_enabled(True, self.settings.setting_path)
            if err is not None:
                self.is_use_keyboard.set(False)
                return
            self._bindKeyboardFocus()
        else:
            self.serial.stop_keyboard()
            self._unbindKeyboardFocus()

    def _bindKeyboardFocus(self) -> None:
        """フォーカス追従を結線し、現在の状態へ即時合わせる。

        結線だけだと結線前の状態が残るため、直後に同期する。
        bind は同じ結び直しで置き換わるので重ね掛けは無害。
        """
        self.root.bind("<FocusIn>", self.onFocusInController)
        self.root.bind("<FocusOut>", self.onFocusOutController)
        self._syncKeyboardFocus()

    def _unbindKeyboardFocus(self) -> None:
        """フォーカス追従を外し、門を下ろす。"""
        self.root.unbind("<FocusIn>")
        self.root.unbind("<FocusOut>")
        self._kb_window_active.clear()

    def onFocusInController(self, event: Any) -> None:
        """窓内へフォーカスが戻ったら打鍵を受け付ける。

        部品間の移動でも来るが、立てるだけなので無害。実体が無い
        ときの作り直し（切断後の復帰用）は従来どおり残す。
        """
        self._kb_window_active.set()
        if self.serial.keyboard is not None:
            return
        if not self.is_use_keyboard.get():
            return
        err = self.serial.set_keyboard_enabled(True, self.settings.setting_path)
        if err is not None:
            self.is_use_keyboard.set(False)

    def onFocusOutController(self, event: Any) -> None:
        """窓外へ出たら打鍵を止める。

        部品間の移動でも来るため、その場で下ろさず直後に確かめる。
        従来の widget 照合（root のみ）は、子部品にフォーカスがある
        まま他アプリへ移ると外れて止まらなかった。終了間際の破棄で
        after 自体が例外のときは下ろしておく。
        """
        try:
            self.root.after(100, self._syncKeyboardFocus)
        except Exception:
            self._kb_window_active.clear()

    def _syncKeyboardFocus(self) -> None:
        """遅延判定：いま窓内のどこにも無ければ下ろす。"""
        try:
            focused = self.root.focus_get() is not None
        except Exception:
            focused = False
        if focused:
            self._kb_window_active.set()
        else:
            self._kb_window_active.clear()

    def createControllerWindow(self) -> None:
        if self.controller is not None:
            self.controller.focus_force()
            return
        window = ControllerGUI(self.root, self.serial.sender)
        window.protocol("WM_DELETE_WINDOW", self.closingController)
        self.controller = window

    def closingController(self) -> None:
        if self.controller is not None:
            self.controller.destroy()
            self.controller = None

    def _on_keyboard_toggled(self) -> None:
        """Use Keyboard の切り替え。有効化に失敗した場合も保存する。

        activateKeyboard は「シリアル未接続なら False へ戻す」ことがある。
        保存はその後に行い、画面に見えている状態と設定を一致させる。
        """
        self.activateKeyboard()
        self._on_setting_changed()

    def _on_left_stick_toggled(self) -> None:
        self.activate_Left_stick_mouse()
        self._on_setting_changed()

    def _on_right_stick_toggled(self) -> None:
        self.activate_Right_stick_mouse()
        self._on_setting_changed()

    def activate_Left_stick_mouse(self) -> None:
        if self.preview is not None:
            self.preview.ApplyLStickMouse()

    def activate_Right_stick_mouse(self) -> None:
        if self.preview is not None:
            self.preview.ApplyRStickMouse()
