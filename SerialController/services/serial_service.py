#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_service.py - シリアル通信の所有と接続手順を1つに持つ層。

Window から「Sender の生成・通信方式の切替・接続／切断・キーボードの
寿命管理」の手順をここへ移している。Window に残すのは tk 変数の読み書き・
確認ダイアログ・見た目の反映だけ。

なぜ分けるか:
  ・接続手順（止める→閉じる→開く→作り直す）は入口が増えるたびに
    片方だけ直す事故が起きる。手順はここだけに置く。
  ・tkinter を触らない。設定値は通常の Python 値で受け取り、
    利用者への通知は notify_user（Window は print を渡す）へ出す。
    ヘッドレスの検証でそのまま動く。

キーボード操作の寿命もここが持つ。開き直すと KeyPress を作り直すが、
Keyboard は生成時に渡された古い KeyPress を持ち続けるため、
接続の切り替えと同時に作り直さないと押しっぱなしの記録が残る。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from Keyboard import SwitchKeyboardController
from core import transport
from core.Keys import KeyPress
from core.serial.arbitration import list_arbitration_modes, resolve_arbitration_mode
from core.serial.sender import Sender
from loguru import logger


@dataclass
class SenderSpec:
    """Sender 生成に要る設定値一式（tk 変数ではなく通常の値）。"""

    transport_name: str
    is_show_serial: Any
    arbitration_mode: str
    arbitration_cooldown: float
    input_log_format: str
    input_log_actions: str
    input_log_enabled: bool
    input_log_stick_change: bool
    # ライブ入力の最低保持ミリ秒（8〜64。範囲外はSender側で無視する）
    live_min_dwell_ms: int


class SerialService:
    """送り先（Sender）とその周辺の所有者。"""

    def __init__(
        self,
        notify_user: Callable[[str], None],
        base_dir: str,
        input_log_emit: Callable[[str], None] | None,
        keyboard_active: Callable[[], bool] | None = None,
    ) -> None:
        """notify_user は利用者への1行通知（Window は print を渡す）。

        base_dir は transport プラグインフォルダの相対解決用。
        input_log_emit は入力ログの送り先（LogPane.emitInputLog）。
        keyboard_active は押下を受け付けるかの判定（Window は窓の
        フォーカスを渡す）。省略時は常時受け付ける。
        """
        self._notify = notify_user
        self._base_dir = base_dir
        self._emit = input_log_emit
        self._keyboard_active = keyboard_active
        self.sender: Sender | None = None
        self.key_press: KeyPress | None = None
        self.keyboard: SwitchKeyboardController | None = None
        # 起動引数で指定された通信方式。設定より優先する（一時的な指定で、
        # 画面から選び直したら効かせない）。
        self.transport_override = ""

    # -- 選択の解決（純粋な手続き） ------------------------------------------

    @staticmethod
    def resolve_transport(name: str | None) -> str:
        """通信方式名を実際に使える名前へ直す（未知は既定へ理由つきで落とす）。"""
        return transport.resolve_transport_name(name)

    @staticmethod
    def list_transports() -> list[str]:
        """登録されている通信方式名の一覧（選択欄の候補に使う）。"""
        return transport.list_transports()

    def selected_transport_name(self, configured: str) -> str:
        """これから使う通信方式の名前を決める。

        優先順は 起動引数 > 設定ファイル。引数を上に置くのは、
        設定を書き換えずにその場で試せるようにするため。
        """
        name = self.transport_override or configured
        return transport.resolve_transport_name(name)

    def clear_override(self) -> None:
        """起動引数の指定を捨てる。画面から選び直したら呼ぶ。"""
        self.transport_override = ""

    @staticmethod
    def arbitration_modes() -> tuple[str, ...]:
        """選べる入力調停の名前（選択欄の候補に使う）。"""
        return list_arbitration_modes()

    @staticmethod
    def resolve_arbitration(mode: str | None, logger: Any = None) -> str:
        """入力調停名を実際に使える名前へ直す（未知は既定へ理由つきで落とす）。"""
        return resolve_arbitration_mode(mode, logger=logger)

    # -- 構築 -----------------------------------------------------------------

    def load_plugins(self, plugin_dir: str) -> None:
        """利用者が置いた自作の Transport を読み込む。

        フォルダ指定が空なら何もしない（既定）。読めたものは名前を
        出す。黙って足すと、候補が増えた理由が分からなくなる。
        """
        directory = (plugin_dir or "").strip()
        if not directory:
            return
        if not os.path.isabs(directory):
            directory = os.path.join(self._base_dir, directory)
        added = transport.load_transport_plugins(directory)
        if added:
            message = "通信方式を読み込みました: " + ", ".join(added)
            self._notify(message)
            logger.info(message)

    def build_sender(self, spec: SenderSpec, transport_logger: Any = None) -> None:
        """Sender を作り、調停と入力ログを反映する。

        運び方は登録簿から名前で作る。作れなければ Transport 側が
        理由を出して既定へ戻すので None にはならない。
        """
        # 入力ログは print と混ぜず、専用のキューへ流す。同じ経路だと
        # 入力ログが上限を食い尽くしてコマンドの出力が捨てられる。
        self.sender = Sender(
            spec.is_show_serial,
            input_log_emit=self._emit,
            transport=transport.create_transport(
                spec.transport_name, logger=transport_logger
            ),
        )
        # 設定の入力の優先付けを反映する。送信側を作り直しても
        # 画面の選択が効いたままになるよう、生成のたびに適用する。
        self.sender.setArbitration(
            mode=spec.arbitration_mode, cooldown=spec.arbitration_cooldown
        )
        # ライブ入力の最低保持を反映する（範囲外はSender側で無視する）
        self.sender.setLiveMinDwell(spec.live_min_dwell_ms)
        self.apply_input_log(
            spec.input_log_format,
            spec.input_log_actions,
            spec.input_log_enabled,
            spec.input_log_stick_change,
        )

    def apply_input_log(
        self, format: str, actions: str, enabled: bool, stick_change: bool
    ) -> None:
        """入力ログの設定を Sender へ反映する。

        書式はプリセット名でもテンプレート文字列そのものでもよい。
        誤った書式でも起動は止めない（ログは補助機能なので、
        ここで落ちるほうが困る）。
        """
        if self.sender is None:
            return
        try:
            # 記録する操作の絞り込み。空なら書式ごとの既定に任せる
            self.sender.setInputLogFormat(format, actions.strip() or None)
            self.sender.setInputLogEnabled(enabled)
            self.sender.setInputLogStickChange(stick_change)
        except Exception as e:
            message = f"入力ログの設定を適用できませんでした: {e}"
            self._notify(message)
            logger.warning(message)

    def set_input_log_enabled(self, enabled: bool) -> None:
        """入力ログの出力を止める・再開する（表示切替用）。"""
        if self.sender is not None:
            self.sender.setInputLogEnabled(enabled)

    def set_arbitration(self, mode: str, cooldown: float) -> None:
        """入力調停を Sender へ反映する。Sender が無くてもよい。"""
        if self.sender is not None:
            self.sender.setArbitration(mode=mode, cooldown=cooldown)

    def switch_transport(
        self, name: str, transport_logger: Any = None
    ) -> tuple[bool, bool]:
        """選択された通信方式へ差し替える。

        開いている線は Sender.setTransport が閉じる。
        戻り値は (差し替えたか, 入力ログが繋がったか)。繋がらない方式も
        あるのでここで理由を出す（黙ると1行も出ない理由が分からない）。
        呼び出し側は差し替えたら開き直す（従来どおり繋がった状態に戻す）。
        """
        if self.sender is None:
            return (False, False)
        if name == self.sender.getTransportName():
            return (False, True)
        new_transport = transport.create_transport(name, logger=transport_logger)
        linked = self.sender.setTransport(new_transport)
        # setTransportのFalseは2通りある。入力ログの無い方式への切替
        # （運び物は替わる）と、worker停止失敗での不変である。見分けは
        # 実体の同一性で行い、替わっていなければ失敗として扱う。
        if not linked and self.sender.transport is not new_transport:
            message = f"通信方式を {name} に切り替えられませんでした。"
            self._notify(message)
            logger.warning(message)
            return (False, False)
        message = f"通信方式を {name} に切り替えました。"
        self._notify(message)
        logger.info(message)
        if not linked:
            self._notify("  注記: この方式では入力ログを出せません。")
        return (True, linked)

    # -- 接続 -----------------------------------------------------------------

    def connect(
        self,
        port_num: int,
        port_name: str,
        baud: int,
        keyboard_enabled: bool,
        setting_path: str,
    ) -> tuple[bool, bool]:
        """ポートを開く。既に開いていれば閉じてから開き直す。

        戻り値は (繋がったか, キーボード操作が有効か)。
        開き直す前にキーボードは必ず止める。KeyPress を作り直すため、
        古い KeyPress を持つ Keyboard を残すと押しっぱなしの記録が残る。
        """
        if self.sender is None:
            return (False, False)
        # 開き直す前に必ず止める。呼び出し元が止めているかどうかに
        # 依存しない。
        self.stop_keyboard()

        # 旧コードは自分自身を再帰呼び出ししていた。閉じてそのまま開けばよい。
        if self.sender.isOpened():
            self._notify("Port is already opened and being closed.")
            self.sender.closeSerial()

        self.key_press = None
        if self.sender.openSerial(port_num, port_name, baud):
            via = self.sender.getTransportName()
            message = f"COM Port {port_name} connected successfully ({via} / {baud}bps)"
            self._notify(message)
            logger.debug(message)
            # この KeyPress はキーボード操作専用。
            # 入力の優先付けで「人の手入力」として扱われるよう名札を付ける。
            self.key_press = KeyPress(self.sender, source="keyboard")
            keyboard_active = False
            if keyboard_enabled:
                keyboard_active = self.set_keyboard_enabled(True, setting_path) is None
            return (True, keyboard_active)

        self.key_press = None
        message = f"COM Port {port_name} を開けませんでした ({baud}bps)"
        self._notify(message)
        logger.warning(message)
        return (False, False)

    def disconnect(self) -> None:
        """ポートを閉じる（Disconnect Port ボタン）。

        keyPress を捨てるだけでは Keyboard のリスナーが生き残る。
        閉じたあとも打鍵を拾い、閉じた Sender へ書きに行く。切断と
        キーボードは同時に止める。
        """
        self.stop_keyboard()
        if self.sender is not None and self.sender.isOpened():
            self._notify("Port is closed.")
            self.sender.closeSerial()
        self.key_press = None

    # -- キーボード -------------------------------------------------------------

    def set_keyboard_enabled(self, enabled: bool, setting_path: str) -> str | None:
        """キーボード操作の有効・無効を切り替える。

        成功したら None、失敗したら理由を返す（呼び出し側は画面の
        チェックを戻す）。理由はここで利用者とファイルの両方へ出す。
        """
        if not enabled:
            self.stop_keyboard()
            return None
        # keyPress が無いまま生成すると、キーを押した瞬間に
        # AttributeError になる（Keyboard 側に None チェックはあるが、
        # 生成自体は通ってしまうため先に断つ）。
        if self.key_press is None:
            message = "シリアル未接続のためキーボード操作を有効にできません"
            self._notify(message)  # チェックが勝手に外れる理由を画面にも出す
            logger.warning(message)
            return message
        if self.keyboard is None:
            try:
                self.keyboard = SwitchKeyboardController(
                    self.key_press,
                    setting_path=setting_path,
                    is_active=self._keyboard_active,
                )
                self.keyboard.listen()
            except Exception as e:
                message = f"キーボード操作を開始できませんでした: {e}"
                self._notify(message)
                logger.warning(message)
                self.keyboard = None
                return message
        return None

    def stop_keyboard(self) -> None:
        """キーボード操作を止める。止まっていれば何もしない。

        停止処理は切断・再接続・終了の3か所から呼ばれる。同じ手順を
        3回書くと、片方だけ直したときに挙動が食い違う。
        """
        if self.keyboard is not None:
            try:
                self.keyboard.stop()
            except Exception as e:
                logger.warning(f"キーボードの停止で例外: {e}")
            self.keyboard = None

    # -- 状態・終了 ---------------------------------------------------------------

    def is_open(self) -> bool:
        """回線が開いているかを返す。"""
        return self.sender is not None and self.sender.isOpened()

    def flush_input_log(self) -> None:
        """溜まった入力ログを送り先へ流す（表示ポンプから呼ぶ）。"""
        if self.sender is not None:
            self.sender.flushInputLog()

    def shutdown(self) -> bool:
        """終了時の後始末。キーボードを止め、開いていれば閉じる。

        戻り値は「閉じたか」。呼び出し側は切断の表示に使う。
        """
        self.stop_keyboard()
        if self.sender is not None and self.sender.isOpened():
            self.sender.closeSerial()
            return True
        return False
