#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import traceback
import platform

import serial
from logging import getLogger, DEBUG, NullHandler

from typing import Any, Callable, Dict, Optional
import InputLog


# 入力ログの既定。ここを書き換えれば起動時の書式が変わる
INPUT_LOG_FORMAT = "simple"
INPUT_LOG_STICK_CHANGE = False

# 送信の最小間隔(秒)。9600bps では1行(18文字)に約19ms かかるため、
# これより短い間隔で呼ばれた分は最新値へまとめて1回にする。
MIN_SEND_INTERVAL = 0.02

# シリアルの入出力タイムアウト(秒)。無指定だと相手が受け取らない状態で
# write が無期限にブロックし、GUI ごと固まる。
READ_TIMEOUT = 0.5
WRITE_TIMEOUT = 0.5


class Sender:
    # 内容が変わらないので実体は1つでよい（旧コードは生成のたびに作り直していた）
    Buttons = InputLog.BUTTON_NAMES
    Hat = tuple(name.split(".")[1] for name in InputLog.HAT_NAMES)

    def __init__(
        self,
        is_show_serial: Any,
        if_print: bool = True,
        input_log_emit: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.ser = None
        self.is_show_serial = is_show_serial

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True

        self.before = None
        self.L_holding = False
        self._L_holding = None
        self.R_holding = False
        self._R_holding = None
        self.is_print = if_print
        self.time_bef = time.perf_counter()
        self.time_aft = time.perf_counter()
        # 入力ログ。writeRow が送った1行を InputLogger へ流し、
        # 前回との差分から press / release と押下時間を組み立てて表示する。
        self.input_logger = InputLog.InputLogger(
            emit=input_log_emit,
            template=INPUT_LOG_FORMAT,
            log_stick_change=INPUT_LOG_STICK_CHANGE,
        )
        self.input_logger.set_enabled(if_print)
        # 送信の間引き用。最後に実際に書き出した時刻と、保留中の行
        self._last_write = 0.0
        self._pending = None

    # -- 入力ログの設定 -----------------------------------------------------

    def setInputLogFormat(self, template: str, actions: Any = None) -> None:
        """表示書式を差し替える。

        template はプリセット名（simple / detail / compact / csv / command /
        raw）か、テンプレート文字列そのもの。差し込み欄と省略ブロックの書き方は
        InputLog.py の冒頭を参照。

        actions は記録する操作の絞り込み。"PRESS,RELEASE" のような文字列か
        ("PRESS",) のような並び。None なら書式ごとの既定に従う。
        """
        self.input_logger.set_format(template, actions)

    def setInputLogEnabled(self, enabled: bool) -> None:
        """入力ログの出力を止める・再開する。"""
        self.input_logger.set_enabled(enabled)

    def setInputLogStickChange(self, enabled: bool) -> None:
        """倒したままの向き変更（CHANGE）を出すかどうか。"""
        self.input_logger.log_stick_change = bool(enabled)

    def flushInputLog(self) -> None:
        """入力ログの集約で保留になっている行を出し切る。

        連打は「A x12」の形にまとめて出すが、確定するのは次の操作が来た
        ときなので最後の1回が残る。GUI が描画のたびにここを呼ぶことで、
        操作をやめた後も取り残されずに表示される。
        """
        self.input_logger.flush()

    def openSerial(
        self, portNum: int, portName: str = "", baudrate: int = 9600
    ) -> bool:
        # baudrate は StringVar 由来の str が渡ることがあるため int に正規化する
        baudrate = int(baudrate)

        try:
            if portName is None or portName == "":
                if os.name == "nt":
                    print(
                        "connecting to "
                        + "COM"
                        + str(portNum)
                        + "("
                        + str(baudrate)
                        + ")"
                    )
                    self._logger.info(
                        "connecting to "
                        + "COM"
                        + str(portNum)
                        + "("
                        + str(baudrate)
                        + ")"
                    )
                    self.ser = serial.Serial(
                        "COM" + str(portNum),
                        baudrate,
                        timeout=READ_TIMEOUT,
                        write_timeout=WRITE_TIMEOUT,
                    )
                    return True
                elif os.name == "posix":
                    if platform.system() == "Darwin":
                        print(
                            "connecting to "
                            + "/dev/tty.usbserial-"
                            + str(portNum)
                            + "("
                            + str(baudrate)
                            + ")"
                        )
                        self._logger.info(
                            "connecting to "
                            + "/dev/tty.usbserial-"
                            + str(portNum)
                            + "("
                            + str(baudrate)
                            + ")"
                        )
                        self.ser = serial.Serial(
                            "/dev/tty.usbserial-" + str(portNum),
                            baudrate,
                            timeout=READ_TIMEOUT,
                            write_timeout=WRITE_TIMEOUT,
                        )
                        return True
                    else:
                        print(
                            "connecting to "
                            + "/dev/ttyUSB"
                            + str(portNum)
                            + "("
                            + str(baudrate)
                            + ")"
                        )
                        self._logger.info(
                            "connecting to "
                            + "/dev/ttyUSB"
                            + str(portNum)
                            + "("
                            + str(baudrate)
                            + ")"
                        )
                        self.ser = serial.Serial(
                            "/dev/ttyUSB" + str(portNum),
                            baudrate,
                            timeout=READ_TIMEOUT,
                            write_timeout=WRITE_TIMEOUT,
                        )
                        return True
                else:
                    print("Not supported OS")
                    self._logger.warning("Not supported OS")
                    return False
            else:
                print("connecting to " + portName)
                self._logger.info("connecting to " + portName)
                self.ser = serial.Serial(
                    portName,
                    baudrate,
                    timeout=READ_TIMEOUT,
                    write_timeout=WRITE_TIMEOUT,
                )
                return True
        except IOError as e:
            print("COM Port: can't be established")
            self._logger.error(f"COM Port: can't be established: {e}")
            # print(e)
            return False

    def closeSerial(self) -> None:
        self._logger.debug("Closing the serial communication")
        # 間引きで保留したままの行があれば先に送る（中途半端な状態で
        # 切断すると、その姿勢のまま残ってしまう）
        self.flushPending()
        # 押しっぱなしのまま切断すると次回接続へ状態が持ち越されるので解放しておく
        self.input_logger.reset()
        if self.ser is None:
            return
        self.ser.close()

    def isOpened(self) -> bool:
        self._logger.debug("Checking if serial communication is open")
        return True if self.ser is not None and self.ser.isOpen() else False

    def _should_show_serial(self) -> bool:
        """is_show_serial は tk.BooleanVar でも素の bool でも受け付ける。"""
        getter = getattr(self.is_show_serial, "get", None)
        return bool(getter()) if callable(getter) else bool(self.is_show_serial)

    def writeRow(
        self, row: str, is_show: bool = False, measure_perf: bool = True
    ) -> None:
        """シリアルへ1行送信する。

        9600bps では1文字あたり約1ms、'3 8 80 80 80 80\r\n' の18文字で
        約19ms かかる。スティック操作のように高頻度で呼ばれると送信
        バッファに積み上がり、操作が数百ms遅れて効く「効きが悪い」
        状態になる。そこで最小送信間隔(MIN_SEND_INTERVAL)を設け、
        間隔内に来た行は送らずに保留し、後から来た行で上書きする
        (coalescing)。スティックは「最後の値」だけ届けば十分なので、
        途中の行を捨てても操作の結果は変わらない。

        ただし取りこぼしてはいけない行がある。ボタンの押下・解放は
        捨てると押しっぱなしになるため、ボタン/Hat が変化した行は
        間隔を無視して必ず送る（判定は _is_coalescable が行う）。

        measure_perf=False にすると perf_counter による計測を行わない
        （旧 writeRow_wo_perf_counter 相当）。
        is_show は後方互換のため残置。入力ログは常に input_logger が行う。
        """
        now = time.perf_counter()
        if self._is_coalescable(row) and now - self._last_write < MIN_SEND_INTERVAL:
            # まだ送らない。保留しておき、次の送信機会か flush で出す
            self._pending = row
            return

        # 保留中の行があれば先に送る。送信行は「変化したスティックだけ」を
        # 含む可変長形式なので、捨てると倒した姿勢がどこにも届かなくなる。
        pending, self._pending = self._pending, None
        if pending is not None:
            self._write(pending, measure_perf=False)

        self._write(row, measure_perf)

    def _is_coalescable(self, row: str) -> bool:
        """間引いてよい行か（＝スティックだけが変わった行か）を判定する。

        送信行の形式は "btn hat [lx ly] [rx ry]"。先頭2トークン
        （ボタンのビット列と Hat）が前回と同じなら、変化したのは
        スティックだけなので途中を捨ててよい。
        """
        prev = self.before
        if prev is None:
            return False
        try:
            return row.split(" ")[:2] == prev.split(" ")[:2]
        except AttributeError:
            return False

    def flushPending(self) -> None:
        """間引きで保留した行があれば送る。

        「最後に少しだけ倒した」状態が送られずに残ると、操作が中途半端
        なまま止まる。GUI が離した時や切断時など、区切りで呼ぶ。
        """
        row, self._pending = self._pending, None
        if row is not None:
            self._write(row, measure_perf=True)

    def _write(self, row: str, measure_perf: bool = True) -> None:
        """実際にシリアルへ書き出す。"""
        try:
            if measure_perf:
                self.time_bef = time.perf_counter()

            # 送る文字列は ASCII 固定なので utf-8 経由より安く作れる
            self.ser.write(row.encode("ascii") + b"\r\n")
            self._last_write = time.perf_counter()

            if measure_perf:
                self.time_aft = self._last_write
        except serial.SerialTimeoutException as e:
            # write_timeout を付けたことで、相手が受け取らない状態でも
            # 無期限に固まらず、ここへ落ちてくる
            print("Serial write timeout")
            self._logger.error(f"Serial write timeout: {e}")
        except serial.serialutil.SerialException as e:
            print(e)
            self._logger.error(f"Error : {e}")
        except AttributeError as e:
            print("Using a port that is not open.")
            self._logger.error(f"Maybe Using a port that is not open.: {e}")
        finally:
            self.before = row

        # 送った行だけを記録する。間引きで送らなかった行は Switch にも
        # 届いていないので、記録に残すとログと実機の挙動がずれる。
        # 送信の成否は問わない（送れなかった操作が記録から消えると
        # 原因の切り分けができなくなるため）。
        self.input_logger.feed(row)

        # Show sending serial datas
        if self._should_show_serial():
            print(row)

    def writeRow_wo_perf_counter(self, row: str, is_show: bool = False) -> None:
        """後方互換のための薄いラッパ。writeRow(measure_perf=False) と等価。"""
        self.writeRow(row, is_show=is_show, measure_perf=False)

    def show_input(self, output: Any) -> None:
        """後方互換の入口。旧APIはトークン列を受け取っていた。

        中身は input_logger へ委譲する。press / release / 押下時間の判定は
        すべて InputLog 側が持つので、ここでは行へ戻して渡すだけでよい。
        """
        try:
            if isinstance(output, (list, tuple)):
                row = " ".join(str(x) for x in output)
            else:
                row = str(output)
            self.input_logger.feed(row)
        except Exception:
            self._logger.error(f"show_input failed: {traceback.format_exc()}")
