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

import csv
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from Keyboard import SwitchKeyboardController
from core import transport
from core.Keys import KeyPress
from core.serial.arbitration import list_arbitration_modes, resolve_arbitration_mode
from core.serial.sender import Sender
from loguru import logger
from services import run_journal
from services.run_journal import read_m_counts


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
        log_dir: str | None = None,
    ) -> None:
        """notify_user は利用者への1行通知（Window は print を渡す）。

        base_dir は transport プラグインフォルダの相対解決用。
        input_log_emit は入力ログの送り先（LogPane.emitInputLog）。
        keyboard_active は押下を受け付けるかの判定（Window は窓の
        フォーカスを渡す）。省略時は常時受け付ける。
        log_dir は走行記録CSVの置き場。省略時は base_dir/log。
        """
        self._notify = notify_user
        self._base_dir = base_dir
        self._emit = input_log_emit
        self._keyboard_active = keyboard_active
        self._log_dir = log_dir or os.path.join(base_dir, "log")
        self.sender: Sender | None = None
        self.key_press: KeyPress | None = None
        self.keyboard: SwitchKeyboardController | None = None
        # 走行記録の進行中状態。begin/end 以外は触らない。
        self._run_active = False
        self._run_name = ""
        self._run_t0 = 0.0
        self._run_stats_before: dict[str, Any] = {}
        self._run_csv_path = ""
        self._run_file: Any = None
        self._run_writer: Any = None
        self._run_rows = 0
        self._run_transport: Any = None
        # 前走終了時のM計数。次走の差分の基準にする（初走は基準なし）。
        self.last_m: dict[str, int] | None = None
        # コード版の写し（走行開始行に付ける）。初回だけgitへ聞く。
        self._code_version_cache: str | None = None
        # 起動引数で指定された通信方式。設定より優先する（一時的な指定で、
        # 画面から選び直したら効かせない）。
        self.transport_override = ""

    def set_log_dir(self, path: str) -> None:
        """走行記録CSVの置き場を変える（検証用）。"""
        self._log_dir = str(path)

    def code_version(self) -> str:
        """コード版（git短縮ハッシュ）。取れなければ空文字。

        ビルド違いの混線防止用。重いので初回だけ聞いて覚える。
        失敗は握る（記録の欠落は実行を止めない）。
        """
        if self._code_version_cache is not None:
            return self._code_version_cache
        version = ""
        try:
            import subprocess

            repo = os.path.dirname(os.path.abspath(self._base_dir))
            done = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if done.returncode == 0:
                version = done.stdout.strip()
        except Exception:
            version = ""
        self._code_version_cache = version
        return version

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

    # -- 走行記録（コマンドの開始・終了に付随する診断） ----------------------
    # 10走0成功のような切り分けに使う。何が・どの条件で走り、PC側で
    # 落としたか（dropped）・Picoが拒否したか（ng）・線に何が出たか
    # （CSV）をファイルログへ残す。記録の失敗で実行は壊さない。

    # 1走のワイヤ記録の上限行数。125Hzで約13分ぶん。超えたら記録だけ止める。
    MAX_RUN_ROWS = 100000

    @staticmethod
    def _ms(value: Any) -> Any:
        """秒→ミリ秒整数。取れなければ "—"。"""
        try:
            return int(round(float(value) * 1000.0))
        except (TypeError, ValueError):
            return "—"

    def _live_stats(self) -> dict[str, Any]:
        """いまのlive統計の写し。取れなければ空辞書。"""
        try:
            sender = self.sender
            if sender is None:
                return {}
            stats = sender.getLiveStats()
            return dict(stats) if isinstance(stats, dict) else {}
        except Exception:
            return {}

    def _unwrap_run_rows(self) -> None:
        """ワイヤ記録の包みを外す。無ければ何もしない。"""
        transport = self._run_transport
        self._run_transport = None
        if transport is None:
            return
        try:
            instance = getattr(transport, "__dict__", None)
            if isinstance(instance, dict):
                instance.pop("send_row", None)
        except Exception:
            logger.debug("ワイヤ記録の解除に失敗", exc_info=True)

    def _close_run_file(self) -> None:
        """記録CSVを閉じる。無ければ何もしない。"""
        try:
            if self._run_file is not None:
                self._run_file.close()
        except Exception:
            logger.debug("記録CSVの close に失敗", exc_info=True)
        finally:
            self._run_file = None
            self._run_writer = None

    def begin_command_run(self, cmd_name: Any, profile: str = "") -> None:
        """走行記録を始める。runner が開始確定後に呼ぶ。

        live統計の基準・ワイヤ記録CSV・開始1行を残す。線が無くても
        開始行だけは残す（記録の欠落と実行の失敗を区別するため）。
        """
        try:
            if self._run_active:
                self._finish_run_rows("上書き終了")
            name = str(cmd_name)
            sender = self.sender
            transport = getattr(sender, "transport", None) if sender else None
            tname = getattr(transport, "name", "?") if transport else "?"
            dwell = self._ms(getattr(sender, "_live_min_dwell_s", None))
            repeat = self._ms(getattr(type(sender), "LIVE_REPEAT_S", None))
            self._run_stats_before = self._live_stats()
            self._run_name = name
            self._run_t0 = time.perf_counter()
            self._run_rows = 0
            self._run_csv_path = ""
            csv_path = ""
            if transport is not None:
                try:
                    os.makedirs(self._log_dir, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    csv_path = os.path.join(
                        self._log_dir,
                        run_journal.run_csv_name(stamp, profile, name),
                    )
                    handle = open(csv_path, "w", newline="", encoding="utf-8")
                    writer = csv.writer(handle)
                    writer.writerow(["t", "line"])
                    orig = transport.send_row

                    def _rec(
                        row: str, measure_perf: bool = True, _o: Any = orig
                    ) -> Any:
                        started = time.perf_counter()
                        try:
                            return _o(row, measure_perf)
                        finally:
                            self._record_wire_row(writer, handle, started, row)

                    transport.send_row = _rec  # type: ignore[method-assign]
                    self._run_file = handle
                    self._run_writer = writer
                    self._run_transport = transport
                    self._run_csv_path = csv_path
                except Exception:
                    logger.debug("ワイヤ記録の開始に失敗", exc_info=True)
                    self._run_csv_path = ""
            self._run_active = True
            logger.info(
                run_journal.format_run_start(
                    cmd_name=name,
                    profile=profile,
                    transport=tname,
                    dwell_ms=dwell,
                    repeat_ms=repeat,
                    version=self.code_version(),
                )
            )
        except Exception:
            logger.debug("走行記録の開始に失敗", exc_info=True)
            self._run_active = False

    def _record_wire_row(self, writer: Any, handle: Any, at: float, row: Any) -> None:
        """送出1行をCSVへ写す。上限を超えたら記録だけ止める。"""
        try:
            if self._run_writer is not writer or self._run_rows >= self.MAX_RUN_ROWS:
                if self._run_rows >= self.MAX_RUN_ROWS and self._run_writer is writer:
                    self._run_writer = None
                    logger.warning("ワイヤ記録が上限に達したため記録を止めます")
                return
            writer.writerow([f"{at:.6f}", str(row)])
            self._run_rows += 1
            if self._run_rows % 200 == 0:
                handle.flush()
        except Exception:
            logger.debug("ワイヤ記録の書き出しに失敗", exc_info=True)

    def _finish_run_rows(self, reason: Any) -> dict[str, Any]:
        """記録の包み外しと集計。end_command_run の本体（同期部）。"""
        seconds = 0.0
        try:
            seconds = max(0.0, time.perf_counter() - self._run_t0)
        except Exception:
            seconds = 0.0
        self._unwrap_run_rows()
        self._close_run_file()
        after = self._live_stats()
        delta = run_journal.live_delta(self._run_stats_before, after)
        summary: dict[str, Any] = {
            "reason": str(reason),
            "seconds": seconds,
            "rows": int(self._run_rows),
            "csv_path": self._run_csv_path,
            "live_delta": delta,
            "m_thread": None,
        }
        self._run_active = False
        return summary

    def _finish_m_counts(self, name: str) -> None:
        """走行終了時のM計数を裏で読み、差分を残す。失敗は握る。"""
        try:
            sender = self.sender
            transport = getattr(sender, "transport", None) if sender else None
            if transport is None or getattr(transport, "ser", None) is None:
                logger.debug("M取得なし（serを持たない方式）")
                return
            now = read_m_counts(transport)
            text = run_journal.format_m_delta(self.last_m, now if now else None)
            if now:
                self.last_m = now
            logger.info(f"M確定[{name}]: {text}")
        except Exception:
            logger.debug("M確定の取得に失敗", exc_info=True)

    def end_command_run(self, reason: Any = "完了") -> dict[str, Any]:
        """走行記録を閉じる。runner が後始末確定時に呼ぶ。

        終了1行（理由・秒数・live差分・M差分・CSV）を残す。M計数は
        裏スレッドで読む（表を固めない）。戻り値は集計辞書。
        """
        try:
            if not self._run_active:
                return {
                    "reason": str(reason),
                    "seconds": 0.0,
                    "rows": 0,
                    "csv_path": "",
                    "live_delta": {},
                    "m_thread": None,
                }
            name = self._run_name
            summary = self._finish_run_rows(reason)
            live_text = run_journal.format_live_delta(summary["live_delta"])
            if self.last_m is None:
                m_text = "M基準なし（裏で取得中）"
            else:
                m_text = "前走基準あり（裏で取得中。確定行を待つこと）"
            csv_path = summary["csv_path"] or "記録なし"
            logger.info(
                run_journal.format_run_end(
                    cmd_name=name,
                    reason=summary["reason"],
                    seconds=summary["seconds"],
                    live_text=live_text,
                    m_text=m_text,
                    csv_path=csv_path,
                    rows=summary["rows"],
                )
            )
            try:
                thread = threading.Thread(
                    target=self._finish_m_counts,
                    args=(name,),
                    name="PokeConRunM",
                    daemon=True,
                )
                thread.start()
                summary["m_thread"] = thread
            except Exception:
                logger.debug("M取得スレッドの起動に失敗", exc_info=True)
            return summary
        except Exception:
            logger.debug("走行記録の終了に失敗", exc_info=True)
            self._run_active = False
            return {
                "reason": str(reason),
                "seconds": 0.0,
                "rows": 0,
                "csv_path": "",
                "live_delta": {},
                "m_thread": None,
            }
