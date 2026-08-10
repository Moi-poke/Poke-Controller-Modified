#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from abc import abstractmethod
from os import path
import atexit
import functools
import os
from typing import Any, Callable, Dict, List, Optional, Tuple
import random
import re
import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
import traceback

import cv2
import numpy as np
from deprecated import deprecated
from loguru import logger

import Settings
from LineNotify import Line_Notify
from DiscordNotify import Discord_Notify

# 旧: from .Keys import ... （ここだけ相対 import で、パッケージとして
# import された場合と単体実行の場合で ImportError になる側が変わっていた）。
# 他の Commands 配下と同じ絶対 import に統一する。
from Commands import CommandBase
from Commands.Keys import Button, Direction, KeyPress

# LINE Notify は 2025/3/31 にサービス終了済み。メッセージを1箇所に集約する。
LINE_EOL_MESSAGE = "LINE通知は2025/3/31にサービスが終了しました。"

# テンプレート画像のキャッシュ件数。判定ループでは同じ画像を毎秒数十回
# 読み直すことになるため、読み込み結果を使い回す。
IMREAD_CACHE_SIZE = 128


def _begin_timer_period() -> None:
    """Windows のタイマー分解能を 1ms に上げる。

    既定は約15.6ms で、time.sleep / Event.wait がその粒度でしか効かない。
    分解能を上げておくと _SPIN_MARGIN を 5ms から 1ms へ下げられ、
    ビジーループの時間を 1/5 にできる。プロセス全体に効く設定なので
    import 時に1度だけ呼び、終了時に戻す。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
        atexit.register(ctypes.windll.winmm.timeEndPeriod, 1)
    except Exception:
        logger.warning("timeBeginPeriod is unavailable; timer stays coarse")


_begin_timer_period()


# the class For notifying stop signal is sent from Main window
class StopThread(Exception):
    pass


# Python command
class PythonCommand(CommandBase.Command):
    def __init__(self) -> None:
        super(PythonCommand, self).__init__()
        self.keys = None
        self.thread = None
        self.alive: bool = True
        # 停止要求。wait() はこれで待つので、長い待ちの最中でも
        # Stop を押せば即座に起きる（例外を投げるのは checkIfAlive）
        self._stop_event = threading.Event()
        # 一時停止。set されている間が「実行中」で、clear すると
        # 待ち・操作の手前で足止めする（停止とは別の仕組み）。
        self._resume_event = threading.Event()
        self._resume_event.set()  # 既定は実行中
        self._paused_total = 0.0  # 通算の一時停止秒数
        self._pause_started = 0.0
        self.postProcess = None
        self.message_dialogue = None

        # __post_init__ は do_safe 経由でしか呼ばれない。do() を直接呼ぶ
        # 使い方をされたときに AttributeError にならないよう None で初期化する。
        self.Line = None
        self.Discord = None

        self.traceback_limit = 5

    def __post_init__(self) -> None:
        self.Line = Line_Notify()
        self.Discord = Discord_Notify()

    @abstractmethod
    def do(self) -> None:
        pass

    def do_safe(self, ser: Any) -> None:
        self.__post_init__()

        if self.keys is None:
            self.keys = KeyPress(ser)

        try:
            if self.alive:
                self.do()
                self.finish()
        except StopThread:
            print("-- finished successfully. --")
            logger.info("Command finished successfully")
        except Exception:
            print("例外が発生しました。")
            print("--------------------------------")

            print(traceback.format_exc(limit=self.traceback_limit))
            logger.error(traceback.format_exc(limit=self.traceback_limit))
            print("--------------------------------")

            # ここで finish() を呼ぶと self.keys.ser を触るため、
            # 既に checkIfAlive が keys=None にしていた場合に二次例外となり、
            # 本来の例外が握り潰される。後始末は _cleanup に一本化する。
            self.alive = False
            # 待機中のスレッドを即座に起こす
            self._stop_event.set()
            self._cleanup(ser)
        finally:
            # 例外で終わった場合もスレッド参照を必ず捨てる。
            # ここを怠ると start() の生存判定に引っかかり、
            # 一度エラーで落ちたコマンドが二度と起動しなくなる。
            self.thread = None

    def _cleanup(self, ser: Any = None) -> None:
        """コマンド終了時の後始末。二重に呼ばれても安全にする。"""
        keys = self.keys
        if keys is None and ser is not None:
            # 途中で keys を捨てられていてもボタンは必ず離す
            keys = KeyPress(ser)
        if keys is not None:
            try:
                keys.end()
            except Exception:
                logger.error(f"Failed to release keys: {traceback.format_exc()}")
        self.keys = None

        postProcess, self.postProcess = self.postProcess, None
        if postProcess is not None:
            postProcess()

    def start(self, ser: Any, postProcess: Optional[Callable[[], None]] = None) -> None:
        # 前回のスレッドが残っていても、既に終了していれば起動を許可する。
        if self.thread is not None and self.thread.is_alive():
            print("-- command is already running. --")
            logger.warning("Command is already running")
            return

        self.alive = True
        self._stop_event.clear()
        # 一時停止の状態は毎回まっさらにする。前回の一時停止が残ると、
        # 次の実行が最初の待ちで固まる。
        self._resume_event.set()
        self._paused_total = 0.0
        self.postProcess = postProcess
        self.thread = threading.Thread(target=self.do_safe, args=(ser,))
        self.thread.start()

    def end(self, ser: Any = None) -> None:
        self.sendStopRequest()

    def sendStopRequest(self) -> None:
        """停止を要求する。呼び出し元のスレッドでは例外を投げない。

        これは GUI スレッドからも呼ばれる。以前は checkIfAlive() を
        経由していたため StopThread が GUI 側へ飛んでいた。実際の
        後始末と脱出は、ワーカースレッドが次に checkIfAlive() を
        通ったときに行われる。
        """
        if self.alive:
            self.alive = False
            # 待機中のスレッドを即座に起こす。以前は次に checkIfAlive を
            # 通るまで最大 wait 秒かかっていた。
            self._stop_event.set()
            print("-- sent a stop request. --")
            logger.info("Sending stop request")

    # -- 一時停止 -----------------------------------------------------------
    #
    # 停止（Stop）は「最初からやり直し」になるため、長いスクリプトの
    # 途中で少しだけ手を離したい場面では使えない。そこで、状態を保った
    # まま足止めするだけの仕組みを別に用意する。
    #
    # 停止と一時停止は別の Event で持つ。1つの旗で兼ねると「止まって
    # いる理由」が区別できず、再開すべき場面で終了してしまう。

    def pause(self) -> None:
        """一時停止する。押しているボタンはそのまま保つ。

        ここでボタンを離すと、再開時に押し直しが要るうえ、ゲーム側の
        状態も変わってしまう（hold で移動し続けている最中など）。
        足止めするだけにして、状態には手を触れない。
        """
        if not self.alive:
            return
        if self._resume_event.is_set():
            self._resume_event.clear()
            self._pause_started = time.perf_counter()
            print("-- paused. --")
            logger.info("Command paused")

    def resume(self) -> None:
        """一時停止を解除する。"""
        if not self._resume_event.is_set():
            elapsed = time.perf_counter() - self._pause_started
            self._paused_total += elapsed
            self._resume_event.set()
            print(f"-- resumed. ({elapsed:.1f}s paused) --")
            logger.info(f"Command resumed after {elapsed:.1f}s")

    def togglePause(self) -> bool:
        """一時停止と再開を切り替える。切り替え後が一時停止なら True。"""
        if self._resume_event.is_set():
            self.pause()
        else:
            self.resume()
        return not self._resume_event.is_set()

    def isPaused(self) -> bool:
        """いま一時停止中かどうか。"""
        return not self._resume_event.is_set()

    @property
    def paused_total(self) -> float:
        """通算で一時停止していた秒数。一時停止中は現在進行分も含む。"""
        return self._pausedSeconds()

    def _pausedSeconds(self) -> float:
        """通算の一時停止秒数。一時停止中は現在進行分も足して返す。

        待ちの締切を補正するのに使う。resume したときだけ加算する
        作りだと、一時停止している最中は 0 のままになり、待ちの
        途中で参照しても補正できない。"""
        total = self._paused_total
        if not self._resume_event.is_set():
            total += time.perf_counter() - self._pause_started
        return total

    # NOTE: Use this function if you want to get out from a command loop by yourself
    def finish(self) -> None:
        self.alive = False
        self.checkIfAlive()

    # -- 出力先の使い分け ---------------------------------------------------

    def print2(self, *args: Any, sep: str = " ", end: str = "\n") -> None:
        """ログタブの「下側」へ出す。print と同じ書き方で使える。

        print は常時流れる進捗に使い、こちらは「後で見返したい結果」に
        使う。欄が分かれていれば、進捗がいくら流れても結果は残る。
        例) print("探索中...") / print2("見つかった: 色違い 3体目")

        出力先の解決には注意が要る。Window.py は起動時に __main__ と
        して実行されるため、ここで import Window とすると Python は
        同じファイルをもう一度読み込み、別モジュールとして扱う。
        その結果 sub_log_queue が2つでき、こちらが入れたキューを
        GUI 側は見ないまま（＝画面に何も出ないまま）になる。
        そこで実行中の __main__ を先に見て、そこに sub_log_queue が
        あればそれを使う。GUI が無い場合は通常の print へ落とす。
        """
        text = sep.join(str(a) for a in args) + end
        queue = self._subLogQueue()
        if queue is None:
            # GUI が無い（CUI 実行・テスト）。標準出力へ出しておけば失われない
            print(text, end="")
            return
        queue.put(text)

    @staticmethod
    def _subLogQueue() -> Optional[Any]:
        """副ログ用のキューを返す。GUI が無ければ None。

        Window.py が __main__ として実行されている場合、import Window は
        同じファイルの2つ目のモジュールを作ってしまい、キューが別物に
        なる。実行中の __main__ を先に調べるのはこのため。
        """
        import sys

        main = sys.modules.get("__main__")
        queue = getattr(main, "sub_log_queue", None)
        if queue is not None:
            return queue
        try:
            import Window
        except Exception:
            return None
        return getattr(Window, "sub_log_queue", None)

    def log2(self, *args: Any, sep: str = " ", end: str = "\n") -> None:
        """print2 の別名。ログとして残す意図を明示したいとき用。"""
        self.print2(*args, sep=sep, end=end)

    # press button at duration times(s)
    def press(self, buttons: Any, duration: float = 0.1, wait: float = 0.1) -> None:
        self._gate()
        self.keys.input(buttons)
        self.wait(duration)
        self.keys.inputEnd(buttons)
        self.wait(wait)
        self.checkIfAlive()

    # press button at duration times(s) repeatedly
    def pressRep(
        self,
        buttons: Any,
        repeat: int,
        duration: float = 0.1,
        interval: float = 0.1,
        wait: float = 0.1,
    ) -> None:
        for i in range(0, repeat):
            self._gate()
            self.press(buttons, duration, 0 if i == repeat - 1 else interval)
        self.wait(wait)

    # add hold buttons
    def hold(self, buttons: Any, wait: float = 0.1) -> None:
        self._gate()
        self.keys.hold(buttons)
        self.wait(wait)
        self.checkIfAlive()

    # release holding buttons
    def holdEnd(self, buttons: Any) -> None:
        self.keys.holdEnd(buttons)
        self.checkIfAlive()

    # sleep では保証できない精度が要るときだけスピンする幅(秒)。
    # Windows では起動時に timeBeginPeriod(1) を呼んでタイマー分解能を
    # 1ms にしてあるので、5ms もスピンする必要がない。
    _SPIN_MARGIN = 0.001

    def _precise_sleep(self, wait: float) -> None:
        """指定時間だけ待つ。停止要求が来たら待ち切らずに戻る。

        全区間をスピンさせると1コアを100%消費する。press() の既定が
        duration=0.1 / wait=0.1 のため、旧実装ではコマンド実行中ずっと
        CPU を焼き続けていた。大半は Event.wait で明け渡し、末尾の
        _SPIN_MARGIN だけスピンして精度を確保する。

        Event.wait で待つのは停止要求のためでもある。以前は待機中に
        停止を見ていなかったので、wait(10) の最中に Stop を押しても
        最大10秒止まらなかった。

        起動時に timeBeginPeriod(1) を呼んでタイマー分解能を 1ms に
        してあるため、スピン幅は 5ms → 1ms で足りる（Switch の操作
        精度は数ms あれば十分）。
        """
        start = time.perf_counter()
        paused0 = self._pausedSeconds()
        rest = wait - self._SPIN_MARGIN
        if rest > 0 and self._wait_or_stop(rest):
            return  # 停止要求。残りは待たない
        while (time.perf_counter() - start - (self._pausedSeconds() - paused0)) < wait:
            if self._stop_event.is_set():
                return

    def _wait_or_stop(self, timeout: float) -> bool:
        """timeout 秒待つ。停止要求が来たら True を返して即座に戻る。

        一時停止の最中は、その時間を待ち時間として数えない。止めている
        あいだに時計が進むと、再開した直後に「待ち終わったこと」にされ、
        次の操作が即座に飛ぶ。押下時間や画面遷移の待ちが飛ぶと、
        再開してすぐ手順がずれる。止まっているあいだは時間も止める。
        """
        while True:
            start = time.perf_counter()
            if self._stop_event.wait(timeout):
                return True
            if self._resume_event.is_set():
                return False
            # 一時停止中。経過した分を引き、解除されるまで待ち直す
            timeout -= time.perf_counter() - start
            if timeout <= 0:
                timeout = 0.0
            self._waitResume()

    def _waitResume(self) -> None:
        """一時停止が解除されるまで待つ。停止要求が来たらすぐ戻る。"""
        while not self._resume_event.wait(0.05):
            if self._stop_event.is_set():
                return

    def _gate(self) -> None:
        """操作を送る手前の関所。停止なら抜け、一時停止なら足止めする。

        待ちの最中だけを見ていると、待ち時間0の連続操作（pressRep など）
        が素通りする。送信の直前に必ずここを通す。

        self.keys が None のときも捨てる。停止時の後始末（_cleanup）が
        keys を None にするため、停止直後に操作へ入ると None を触って
        AttributeError になり、本来の停止が別の例外に化けていた。
        """
        self.checkIfAlive()
        if not self._resume_event.is_set():
            self._waitResume()
            self.checkIfAlive()
        if self.keys is None:
            raise StopThread("keys are already released")

    # do nothing at wait time(s)
    def short_wait(self, wait: float) -> None:
        self._precise_sleep(float(wait))
        self.checkIfAlive()

    # do nothing at wait time(s)
    def wait(self, wait: float) -> None:
        """指定秒待つ。停止要求が来たら即座に起きる。

        役割分担は「wait は起きるだけ／checkIfAlive が StopThread を
        投げる」を保つ。
        """
        wait = float(wait)
        if wait > 0:
            self._precise_sleep(wait)
        self.checkIfAlive()

    def checkIfAlive(self) -> bool:
        if not self.alive:
            # thread の後始末は do_safe の finally が行う。ここで None に
            # すると、実行中のスレッド自身が start() の生存判定を消して
            # しまい二重起動を招く。
            self._cleanup()

            # raise exception for exit working thread
            logger.info("Exit from command successfully")
            raise StopThread("exit successfully")
        else:
            return True

    def dialogue(
        self, title: str, message: int | str | list, need: type = list
    ) -> list | dict:
        """入力ダイアログを出し、閉じられるまで待って結果を返す。"""
        return self._runDialogue(title, message, need, mode=0)

    def dialogue6widget(
        self, title: str, dialogue_list: list, need: type = list
    ) -> list | dict:
        """6種のウィジェットに対応した入力ダイアログ版。"""
        return self._runDialogue(title, dialogue_list, need, mode=1)

    def _runDialogue(self, title: str, message: Any, need: type, mode: int) -> Any:
        """ダイアログの生成を GUI スレッドへ委譲し、結果を受け取る。

        tkinter はスレッドセーフではなく、GUI スレッド以外から widget を
        生成するとフリーズやクラッシュの原因になる。コマンドはワーカー
        スレッドで動くので、生成そのものを after(0) でメインスレッドへ
        渡し、こちらは Event で結果を待つ。

        待ちには _stop_event を併用する。ダイアログを開いたまま Stop を
        押したときに、コマンド側が永久に待ち続けないようにするため。
        呼び出し規約（戻り値）は従来と同じなので既存コマンドは無修正で動く。
        """
        done = threading.Event()
        box = {}

        def build() -> None:
            # ここは GUI スレッド。widget の生成と mainloop 的な待ちは
            # すべてこの中で完結する。
            try:
                self.message_dialogue = tk.Toplevel()
                dlg = PokeConDialogue(self.message_dialogue, title, message, mode=mode)
                box["value"] = dlg.ret_value(need)
            except Exception:
                box["error"] = traceback.format_exc()
            finally:
                self.message_dialogue = None
                done.set()

        root = self._guiRoot()
        if root is None:
            # GUI が無い（CUI 実行・テスト）ときは従来どおり直に作る
            build()
        else:
            root.after(0, build)

        while not done.wait(0.1):
            if self._stop_event.is_set():
                # 停止要求。ダイアログは GUI スレッド側に残るが、
                # ここで待ち続けるとコマンドが終われなくなる。
                logger.warning("Stop requested while a dialogue is open")
                self.checkIfAlive()
                return [] if need is list else {}

        if "error" in box:
            raise RuntimeError(f"dialogue failed:\n{box['error']}")
        return box.get("value")

    def _guiRoot(self) -> Optional[Any]:
        """ダイアログを載せる tk のルートを返す。無ければ None。"""
        gui = getattr(self, "gui", None)
        for obj in (gui, getattr(gui, "master", None)):
            if obj is not None and hasattr(obj, "after"):
                return obj
        return None

    # Use time glitch

    # Controls the system time and get every-other-day bonus without any punishments
    def timeLeap(self, is_go_back: bool = True) -> None:
        self.press(Button.HOME, wait=1)
        self.press(Direction.DOWN)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Direction.RIGHT)
        self.press(Button.A, wait=1.5)  # System Settings
        self.press(Direction.DOWN, duration=2, wait=0.5)

        self.press(Button.A, wait=0.3)  # System Settings > System
        self.press(Direction.DOWN)
        self.press(Direction.DOWN)
        self.press(Direction.DOWN)
        self.press(Direction.DOWN, wait=0.3)
        self.press(Button.A, wait=0.2)  # Date and Time
        self.press(Direction.DOWN, duration=0.7, wait=0.2)

        # increment and decrement
        if is_go_back:
            self.press(Button.A, wait=0.2)
            self.press(Direction.UP, wait=0.2)  # Increment a year
            self.press(Direction.RIGHT, duration=1.5)
            self.press(Button.A, wait=0.5)

            self.press(Button.A, wait=0.2)
            self.press(Direction.LEFT, duration=1.5)
            self.press(Direction.DOWN, wait=0.2)  # Decrement a year
            self.press(Direction.RIGHT, duration=1.5)
            self.press(Button.A, wait=0.5)

        # use only increment
        # for use of faster time leap
        else:
            self.press(Button.A, wait=0.2)
            self.press(Direction.RIGHT)
            self.press(Direction.RIGHT)
            self.press(Direction.UP, wait=0.2)  # increment a day
            self.press(Direction.RIGHT, duration=1)
            self.press(Button.A, wait=0.5)

        self.press(Button.HOME, wait=1)
        self.press(Button.HOME, wait=1)

    @deprecated(reason="Use discord instead")
    def LINE_text(self, txt: str = "", token: str = "token") -> bool:
        """LINE Notify は 2025/3/31 にサービス終了。送信は行わない。

        以前は「終了しました」と出力した直後に送信を試み、例外を pass で
        握り潰していた（LINE_image は逆に except 側で出力しており非対称）。
        deprecated である以上、送信自体を行わず False を返す形に統一した。
        """
        logger.error(LINE_EOL_MESSAGE)
        return False

    def discord_text(
        self, content: str = "", index: int = 0, name: Optional[str] = None
    ) -> bool:
        """
        Discordにテキストメッセージを送信します。

        Args:
            content (str): 送信するテキストメッセージ。デフォルトは空文字列です。
            index (int): メッセージのインデックス番号。デフォルトは0です。
            name Optional(str): 通知先Webhookの名前。indexよりも優先されます。

        Returns:
            bool: 成功時はTrue、失敗時はFalse

        Raises:
            Exception: Discord通知に失敗した場合
        """
        without_image = True
        try:
            if name:
                self.Discord.send_message(
                    index=index, content=content, name=name, without_image=without_image
                )
            else:
                self.Discord.send_message(
                    index=index, content=content, without_image=without_image
                )

            return True
        except Exception as e:
            logger.error(f"Failed to send Discord text notification: {str(e)}")
            print(traceback.format_exc())
            return False

    # direct serial
    def direct_serial(self, serialcommands: List[str], waittime: List[float]) -> None:
        # 余計なものが付いている可能性があるので確認して削除する
        checkedcommands = []
        for row in serialcommands:
            checkedcommands.append(row.replace("\r", "").replace("\n", ""))
        self.keys.serialcommand_direct_send(checkedcommands, waittime)

    # Reload COM port (temporary function)
    def reload_com_port(self, retry: int = 3) -> bool:
        """COM ポートを開き直す。

        旧実装は closeSerial 後に自分自身を再帰呼び出ししていたため、
        切断に失敗して isOpened() が True を返し続けると RecursionError で
        落ちた。回数上限つきのループに置き換える。
        """
        settings = Settings.GuiSettings()

        for _ in range(max(1, retry)):
            if self.keys.ser.isOpened():
                print("Port is already opened and being closed.")
                self.keys.ser.closeSerial()
                if self.keys.ser.isOpened():
                    logger.warning("Failed to close the port. retrying...")
                    self.wait(0.5)
                    continue

            if self.keys.ser.openSerial(
                settings.com_port.get(),
                settings.com_port_name.get(),
                settings.baud_rate.get(),
            ):
                msg = f"COM Port {settings.com_port.get()} connected successfully"
                print(msg)
                logger.debug(msg)
                return True

            self.wait(0.5)

        msg = f"COM Port {settings.com_port.get()} failed to reconnect"
        print(msg)
        logger.error(msg)
        return False


class PokeConDialogue(object):
    def __init__(
        self, parent: Any, title: str, message: int | str | list, mode: int = 0
    ) -> None:
        """
        pokecon用ダイアログ生成関数(注意:mode=0と1でmessageの取り扱いが大きく異なる。)
        mode | int: 0のときEntryのみ、1のとき6種類のwidgetに対応
        title | str: タイトル
        message | mode=0の場合 : int/str/list: Entryのラベル、mode=1の場合 : list[widget, widget, ...]: widgetごとの設定をリスト化したもの
        widget | list : widgetごとの設定(ウィジェットの種類によってリストの中身は異なる。以下を参照。)
        checkbox/entryの場合 : [type, subtitle, init] (例) ["check", "Check(例)", True]、["ENTRY", "Entry(例)", "初期値"]
        combobox/radiobutton/spinboxの場合 : [type, subtitle, selectlist, init] (例) ["Combo", "Combo(例)", ["hello", "world"], "hello"]、["RADIO", "Radio(例)", ["dog", "cat"],"dog"]、["Spin", "Spin(例)", list(map(str, range(10))), "3"]
        scaleの場合 : [type, subtitle, min, max, init, digit] (例) ["Scale", "scale(例)", 0, 100, 50.1, 2]
        type | str: widgetの種類(check/combo/entry/radio/spin/scaleのいずれか。大文字小文字は問わない)
        subtitle | str : widgetのタイトル
        init | checkboxの場合bool,scaleの場合int/float,その他str : 初期値
        selectlist | list : 項目のリスト
        min/max | int/float : scaleの最小値と最大値
        digit | int : 有効桁数
        return : なし
        """
        self._ls = None
        self.isOK = None

        self.message_dialogue = parent
        self.message_dialogue.title(title)
        self.message_dialogue.attributes("-topmost", True)
        self.message_dialogue.protocol("WM_DELETE_WINDOW", self.close_window)

        self.main_frame = tk.Frame(self.message_dialogue)
        self.inputs = ttk.Frame(self.main_frame)

        self.title_label = ttk.Label(self.main_frame, text=title, anchor="center")
        self.title_label.grid(
            column=0, columnspan=2, ipadx="10", ipady="10", row=0, sticky="nsew"
        )

        self.dialogue_ls = {}
        # winfo_width()/height() は最初の描画前だと 1 を返すため、
        # update_idletasks() でジオメトリを確定させてから読む。
        self.message_dialogue.update_idletasks()
        master = self.message_dialogue.master
        x = master.winfo_x()
        w = master.winfo_width()
        y = master.winfo_y()
        h = master.winfo_height()
        w_ = self.message_dialogue.winfo_width()
        h_ = self.message_dialogue.winfo_height()
        self.message_dialogue.geometry(
            f"+{int(x + w / 2 - w_ / 2)}+{int(y + h / 2 - h_ / 2)}"
        )

        if mode == 0:
            self.mode0(message)
        else:
            self.mode1(message)

        self.inputs.grid(
            column=0, columnspan=2, ipadx="10", ipady="10", row=1, sticky="nsew"
        )
        self.inputs.grid_anchor("center")
        self.result = ttk.Frame(self.main_frame)
        self.OK = ttk.Button(self.result, command=self.ok_command)
        self.OK.configure(text="OK")
        self.OK.grid(column=0, row=1)
        self.Cancel = ttk.Button(self.result, command=self.cancel_command)
        self.Cancel.configure(text="Cancel")
        self.Cancel.grid(column=1, row=1, sticky="ew")
        self.result.grid(column=0, columnspan=2, pady=5, row=2, sticky="ew")
        self.result.grid_anchor("center")
        self.main_frame.pack()
        self.message_dialogue.master.wait_window(self.message_dialogue)

    def mode0(self, message: list | str) -> None:
        if type(message) is not list:
            message = [message]
        n = len(message)

        for i in range(n):
            key = message[i]
            if key in self.dialogue_ls:
                # キーがラベル文字列そのものなので、同じラベルを2つ渡すと
                # 後勝ちで上書きされ widget 数と戻り値の数が食い違っていた。
                raise ValueError(f"Duplicated label in dialogue message: {key!r}")
            self.dialogue_ls[key] = tk.StringVar()
            label = ttk.Label(self.inputs, text=key)
            entry = ttk.Entry(self.inputs, textvariable=self.dialogue_ls[key])
            label.grid(column=0, row=i, sticky="nsew", padx=3, pady=3)
            entry.grid(column=1, row=i, sticky="nsew", padx=3, pady=3)

    def mode1(self, dialogue_list: list) -> None:
        n = len(dialogue_list)
        frame = []

        scale_label_list = []  # scaleの値を表示するlabelを格納するリスト
        scale_index_list = []  # scaleが何番目のwidgetなのかを格納するリスト
        scale_digit_list = []  # scaleの有効桁数を格納するリスト

        def change_scale_value(
            event=None,
        ):  # scaleのバーを動かしたときにlabelの値を変更するための関数
            for i, (index, fmt) in enumerate(zip(scale_index_list, scale_digit_list)):
                if fmt != 0:
                    val = round(self.dialogue_ls[dialogue_list[index][1]].get(), fmt)
                    scale_label_list[i]["text"] = "%s" % val
                    self.dialogue_ls[dialogue_list[index][1]].set(val)
                else:
                    scale_label_list[i]["text"] = (
                        "%s" % self.dialogue_ls[dialogue_list[index][1]].get()
                    )

        for i in range(n):
            # widgetはすべてframeの中に入れる。scaleの場合、値を示すlabelもフレームの中に入れる。
            frame.append(ttk.LabelFrame(self.inputs, text=dialogue_list[i][1]))

            # Checkbox
            if dialogue_list[i][0].casefold() == "check".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.BooleanVar(
                    value=dialogue_list[i][2]
                )
                widget = ttk.Checkbutton(
                    frame[i], variable=self.dialogue_ls[dialogue_list[i][1]]
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)
            # Combobox
            elif dialogue_list[i][0].casefold() == "combo".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][3]
                )
                widget = ttk.Combobox(
                    frame[i],
                    values=dialogue_list[i][2],
                    textvariable=self.dialogue_ls[dialogue_list[i][1]],
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)
                # widget.current(0)
            # Entry
            elif dialogue_list[i][0].casefold() == "entry".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][2]
                )
                widget = ttk.Entry(
                    frame[i], textvariable=self.dialogue_ls[dialogue_list[i][1]]
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)
            # Radiobutton
            elif dialogue_list[i][0].casefold() == "radio".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][3]
                )
                for j, text0 in enumerate(dialogue_list[i][2]):
                    widget = ttk.Radiobutton(
                        frame[i],
                        text=text0,
                        variable=self.dialogue_ls[dialogue_list[i][1]],
                        value=text0,
                    )
                    widget.grid(column=j, row=0, sticky="nsew", padx=3, pady=3)
            # Scale
            elif dialogue_list[i][0].casefold() == "scale".casefold():
                # scale は [種別, キー, 最小, 最大, 初期値, 有効桁数] の6要素が必須。
                # 検証せずに [5] を参照していたため、短いリストで IndexError になっていた。
                if len(dialogue_list[i]) < 6:
                    raise ValueError(
                        f"'scale' requires 6 elements "
                        f"[type, key, from, to, value, digit], "
                        f"but got {len(dialogue_list[i])}: {dialogue_list[i]}"
                    )
                scale_index_list.append(i)
                scale_digit_list.append(dialogue_list[i][5])
                if dialogue_list[i][5] != 0:  # 浮動小数点数
                    self.dialogue_ls[dialogue_list[i][1]] = tk.DoubleVar(
                        value=dialogue_list[i][4]
                    )
                    scale_label_list.append(
                        tk.Label(
                            frame[i],
                            width=10,
                            text="%s"
                            % round(
                                self.dialogue_ls[dialogue_list[i][1]].get(),
                                dialogue_list[i][5],
                            ),
                        )
                    )
                else:  # 整数
                    self.dialogue_ls[dialogue_list[i][1]] = tk.IntVar(
                        value=dialogue_list[i][4]
                    )
                    scale_label_list.append(
                        tk.Label(
                            frame[i],
                            width=10,
                            text="%s" % self.dialogue_ls[dialogue_list[i][1]].get(),
                        )
                    )
                widget = ttk.Scale(
                    frame[i],
                    from_=dialogue_list[i][2],
                    to=dialogue_list[i][3],
                    variable=self.dialogue_ls[dialogue_list[i][1]],
                    command=change_scale_value,
                )
                scale_label_list[-1].grid(
                    column=0, row=0, sticky="nsew", padx=3, pady=3
                )
                widget.grid(column=1, row=0, sticky="nsew", padx=3, pady=3)
            # Spinbox
            elif dialogue_list[i][0].casefold() == "spin".casefold():
                self.dialogue_ls[dialogue_list[i][1]] = tk.StringVar(
                    value=dialogue_list[i][3]
                )
                widget = ttk.Spinbox(
                    frame[i],
                    values=dialogue_list[i][2],
                    textvariable=self.dialogue_ls[dialogue_list[i][1]],
                )
                widget.grid(column=0, row=0, sticky="nsew", padx=3, pady=3)

            frame[i].grid(column=0, row=i, sticky="nsew", padx=3, pady=3)

        # widgetのサイズをフレームのサイズに合わせる
        for i in range(n):
            if dialogue_list[i][0].casefold() == "scale".casefold():
                frame[i].grid_columnconfigure(0, weight=1)
                frame[i].grid_columnconfigure(1, weight=3)
            else:
                frame[i].grid_columnconfigure(0, weight=1)

    def ret_value(self, need: type) -> list | dict | None:
        """入力結果を返す。Cancel / ウィンドウを閉じた場合は None。

        以前は Cancel 時に False を返していたため、呼び出し側が dict を前提に
        .get() すると AttributeError になっていた。None を返すことで
        `if result is None:` の素直な分岐で扱えるようにした。
        """
        if not self.isOK:
            return None

        if need is dict:
            return {k: v.get() for k, v in self.dialogue_ls.items()}
        if need is list:
            return self._ls

        self._logger.warning(f"Wrong arg: {need}. Returns list instead.")
        return self._ls

    def close_window(self) -> None:
        self.message_dialogue.destroy()
        self.isOK = False

    def ok_command(self) -> None:
        self._ls = [v.get() for k, v in self.dialogue_ls.items()]
        self.message_dialogue.destroy()
        self.isOK = True

    def cancel_command(self) -> None:
        self.message_dialogue.destroy()
        self.isOK = False


TEMPLATE_PATH = "./Template/"


def _get_template_filespec(template_path: str) -> str:
    """
    テンプレート画像ファイルのパスを取得する。
    入力が絶対パスの場合は、`TEMPLATE_PATH`につなげずに返す。
    Args:
        template_path (str): 画像パス
    Returns:
        str: _description_
    """
    if path.isabs(template_path):
        return template_path
    else:
        return path.join(TEMPLATE_PATH, template_path)


@functools.lru_cache(maxsize=IMREAD_CACHE_SIZE)
def _imread_or_raise(template_path: str, flags: int) -> np.ndarray:
    """テンプレート画像を読み込む。失敗したら理由の分かる例外にする。

    cv2.imread はファイルが無い・壊れている場合に例外ではなく None を返す。
    そのまま .shape を触ると TypeError になり、原因がパスだと分からない。

    判定ループでは毎秒数十回、同じファイルを読んでデコードすることになり、
    matchTemplate 本体より重くなることがある。引数は path と flags だけで
    副作用が無いため lru_cache で包める。

    ★規約: 返る配列はキャッシュで共有される。呼び出し側で書き換えない
    こと（書き換えると以降の判定すべてに影響する）。テンプレート画像を
    差し替えたときは clear_template_cache() を呼ぶ。
    """
    filespec = _get_template_filespec(template_path)
    image = cv2.imread(filespec, flags)
    if image is None:
        raise FileNotFoundError(f"テンプレート画像を読み込めませんでした: {filespec}")
    image.flags.writeable = False  # 共有配列を誤って書き換えないための保険
    return image


def clear_template_cache() -> None:
    """テンプレート画像のキャッシュを捨てる。

    実行中にテンプレート画像を差し替えたときに呼ぶ。os.path.getmtime を
    キーへ含める案もあるが、判定のたびに stat が走るので採らなかった。
    """
    _imread_or_raise.cache_clear()


class ImageProcPythonCommand(PythonCommand):
    def __init__(self, cam: Any, gui: Any = None) -> None:
        super(ImageProcPythonCommand, self).__init__()

        # self._logger = getLogger(__name__)
        # self._logger.addHandler(NullHandler())
        # self._logger.setLevel(DEBUG)
        # self._logger.propagate = True

        self.camera = cam
        # deprecated
        # self.Line = Line_Notify(self.camera)

        self.gui = gui

        # cv2.cuda_GpuMat() は CUDA 無効ビルドでは AttributeError になる。
        # ここで無条件に生成すると、GPU を使わない全コマンドまで起動不能に
        # なるため、実際に GPU 版を呼んだときだけ確保する。
        self.gsrc = None
        self.gtmpl = None
        self.gresult = None

        # GPU 版のキャッシュ。matcher は (dtype, method)、テンプレートは
        # (path, use_gray) をキーにする
        self._cuda_matchers: dict = {}
        self._cuda_templates: dict = {}

    def _ensure_cuda(self) -> bool:
        """CUDA が使えるかを判定し、使えれば GpuMat を用意する。"""
        if self.gsrc is not None:
            return True
        if not hasattr(cv2, "cuda_GpuMat") or not hasattr(cv2, "cuda"):
            return False
        try:
            if cv2.cuda.getCudaEnabledDeviceCount() < 1:
                return False
            self.gsrc = cv2.cuda_GpuMat()
            self.gtmpl = cv2.cuda_GpuMat()
            self.gresult = cv2.cuda_GpuMat()
        except Exception:
            logger.warning(f"CUDA is unavailable: {traceback.format_exc(limit=1)}")
            return False
        return True

    def _cudaMatcher(self, dtype: int, method: int) -> Any:
        """テンプレートマッチャを (dtype, method) 単位でキャッシュする。

        呼び出しのたびに createTemplateMatching すると、GPU 版の利点
        （生成と転送の削減）を自分で打ち消すことになる。
        """
        key = (dtype, method)
        matcher = self._cuda_matchers.get(key)
        if matcher is None:
            matcher = cv2.cuda.createTemplateMatching(dtype, method)
            self._cuda_matchers[key] = matcher
        return matcher

    def _cudaTemplate(self, template_path: str, use_gray: bool) -> Any:
        """テンプレートを GpuMat にしてキャッシュする（転送を1度だけにする）。"""
        key = (template_path, bool(use_gray))
        gtmpl = self._cuda_templates.get(key)
        if gtmpl is None:
            template = _imread_or_raise(
                template_path,
                cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
            )
            gtmpl = cv2.cuda_GpuMat()
            gtmpl.upload(template)
            self._cuda_templates[key] = gtmpl
        return gtmpl

    def clearCudaCache(self) -> None:
        """GPU 側のキャッシュを捨てる（テンプレート差し替え時に呼ぶ）。"""
        self._cuda_matchers.clear()
        self._cuda_templates.clear()

    def __post_init__(self) -> None:
        self.Line = Line_Notify(self.camera)
        self.Discord = Discord_Notify(camera=self.camera)

    # Judge if current screenshot contains an image using template matching
    # It's recommended that you use gray_scale option unless the template color wouldn't be cared for performace
    # 現在のスクリーンショットと指定した画像のテンプレートマッチングを行います
    # 色の違いを考慮しないのであればパフォーマンスの点からuse_grayをTrueにしてグレースケール画像を使うことを推奨します
    def isContainTemplate(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        show_value=False,
        show_position=True,
        show_only_true_rect=True,
        ms=2000,
        crop=None,
        mask_path=None,
    ):
        # crop を先に切ってから色変換する。逆にすると使わない領域まで
        # 変換することになり、crop が全体の 1/9 でも 1280x720 全面を
        # 変換してしまう（判定ループでは毎回この無駄が乗る）。
        crop = crop or []
        src = self.camera.readFrame()
        if len(crop) == 4:
            src = src[crop[1] : crop[3], crop[0] : crop[2]]
        if use_gray:
            src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)

        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )

        # mask用画像読み込み
        if mask_path is None:
            mask = None
            method = cv2.TM_CCOEFF_NORMED
        else:
            mask = _imread_or_raise(mask_path, 0)
            method = cv2.TM_CCORR_NORMED

        w, h = template.shape[1], template.shape[0]

        res = cv2.matchTemplate(src, template, method, mask)
        # マスク併用の TM_CCORR_NORMED は分母0の領域で NaN を返しうる。
        # NaN が混じると minMaxLoc の結果が不定になるため潰しておく。
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if show_value:
            print(template_path + " ZNCC value: " + str(max_val))

        top_left = max_loc
        bottom_right = (top_left[0] + w + 1, top_left[1] + h + 1)
        tag = str(time.perf_counter()) + str(random.random())
        if max_val >= threshold:
            if self.gui is not None and show_position:
                # self.gui.delete("ImageRecRect")
                self.gui.ImgRect(
                    *top_left, *bottom_right, outline="blue", tag=tag, ms=ms
                )
            return True
        else:
            if self.gui is not None and show_position and not show_only_true_rect:
                # self.gui.delete("ImageRecRect")
                self.gui.ImgRect(
                    *top_left, *bottom_right, outline="red", tag=tag, ms=ms
                )
            return False

    # 現在のスクリーンショットと指定した複数の画像のテンプレートマッチングを行います
    # 相関値が最も大きい値となった画像のインデックス、各画像のテンプレートマッチングの閾値、閾値判定結果を返します。
    # 色の違いを考慮しないのであればパフォーマンスの点からuse_grayをTrueにしてグレースケール画像を使うことを推奨します
    def isContainTemplate_max(
        self,
        template_path_list,
        threshold=0.7,
        use_gray=True,
        show_value=False,
        show_position=True,
        show_only_true_rect=True,
        ms=2000,
        crop=None,
        break_on_hit=False,
    ):
        """複数テンプレートのうち相関が最大のものを返す。

        break_on_hit=True にすると、閾値を超えたものを見つけた時点で
        残りを評価せずに返す。「どれか1つに一致したか」を見たいだけの
        用途では全件回す必要がないため。戻り値の互換のため既定は False
        （既定のままなら従来どおり全件を評価し、最大値を返す）。
        打ち切った場合、未評価のテンプレートの相関値は 0.0 で埋める。
        """
        if not template_path_list:
            raise ValueError("template_path_list が空です。")

        # crop を先に切ってから色変換する（isContainTemplate と同じ理由）
        crop = crop or []
        src = self.camera.readFrame()
        if len(crop) == 4:
            src = src[crop[1] : crop[3], crop[0] : crop[2]]
        if use_gray:
            src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)

        max_val_list = []
        judge_threshold_list = []
        for template_path in template_path_list:
            template = _imread_or_raise(
                template_path,
                cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
            )
            w, h = template.shape[1], template.shape[0]

            method = cv2.TM_CCOEFF_NORMED
            res = cv2.matchTemplate(src, template, method)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            if show_value:
                print(template_path + " ZNCC value: " + str(max_val))

            top_left = max_loc
            bottom_right = (top_left[0] + w + 1, top_left[1] + h + 1)
            tag = str(time.perf_counter()) + str(random.random())
            max_val_list.append(max_val)
            judge_threshold_list.append(max_val >= threshold)

            if max_val >= threshold:
                if self.gui is not None and show_position:
                    # self.gui.delete("ImageRecRect")
                    self.gui.ImgRect(
                        *top_left, *bottom_right, outline="blue", tag=tag, ms=ms
                    )
            else:
                if self.gui is not None and show_position and not show_only_true_rect:
                    # self.gui.delete("ImageRecRect")
                    self.gui.ImgRect(
                        *top_left, *bottom_right, outline="red", tag=tag, ms=ms
                    )

            if break_on_hit and judge_threshold_list[-1]:
                # 一致が1つ見つかれば十分な用途では、残りを評価しない。
                # 戻り値の形を保つため、未評価分は 0.0 / False で埋める。
                rest = len(template_path_list) - len(max_val_list)
                max_val_list.extend([0.0] * rest)
                judge_threshold_list.extend([False] * rest)
                break

        return np.argmax(max_val_list), max_val_list, judge_threshold_list

    def isContainTemplateGPU(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        show_value=False,
        not_show_false=True,
    ):
        """CUDA を使ったテンプレートマッチング。"""
        if not self._ensure_cuda():
            raise RuntimeError(
                "CUDA 対応の OpenCV が見つかりません。"
                "isContainTemplate() を使ってください。"
            )

        src = self.camera.readFrame()
        src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY) if use_gray else src

        self.gsrc.upload(src)

        gtmpl = self._cudaTemplate(template_path, use_gray)

        method = cv2.TM_CCOEFF_NORMED
        matcher = self._cudaMatcher(cv2.CV_8UC1, method)
        gresult = matcher.match(self.gsrc, gtmpl)
        resultg = gresult.download()
        _, max_val, _, max_loc = cv2.minMaxLoc(resultg)

        if show_value:
            print(template_path + " ZNCC value: " + str(max_val))

        return bool(max_val >= threshold)

    # Get interframe difference binarized image
    # フレーム間差分により2値化された画像を取得
    def getInterframeDiff(
        self,
        frame1: np.ndarray,
        frame2: np.ndarray,
        frame3: np.ndarray,
        threshold: float,
    ) -> np.ndarray:
        diff1 = cv2.absdiff(frame1, frame2)
        diff2 = cv2.absdiff(frame2, frame3)

        diff = cv2.bitwise_and(diff1, diff2)

        # binarize
        img_th = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)[1]

        # remove noise
        mask = cv2.medianBlur(img_th, 3)
        return mask

    # -- 待つ・探す（出現待ち / 停止待ち / 位置取得） -----------------------

    def _matchOnce(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        show_value=False,
    ):
        """1回だけ照合し、(判定, 相関値, 中心座標) をまとめて返す。

        readFrame → crop → 色変換 → matchTemplate までの手順は
        isContainTemplate と同じ。同じ前処理が方々へ散らばると、crop と
        色変換の順序を入れ替えたときのような修正が、その全部に要る。
        ここへ寄せて、公開メソッドはこれを呼ぶだけにする。

        中心座標は crop の左上を足して画面全体の座標へ直して返す。
        切り出した中の座標のまま返すと、呼び出し側が毎回 crop[0] を
        足すことになり、足し忘れがいつか必ず起きる。
        """
        crop = crop or []
        src = self.camera.readFrame()
        if len(crop) == 4:
            src = src[crop[1] : crop[3], crop[0] : crop[2]]
        if use_gray:
            src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)

        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )
        if mask_path is None:
            mask = None
            method = cv2.TM_CCOEFF_NORMED
        else:
            mask = _imread_or_raise(mask_path, 0)
            method = cv2.TM_CCORR_NORMED

        h, w = template.shape[0], template.shape[1]
        res = cv2.matchTemplate(src, template, method, mask)
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if show_value:
            print(f"{template_path} ZNCC value: {max_val}")

        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0
        center = (int(max_loc[0] + dx + w / 2), int(max_loc[1] + dy + h / 2))
        return bool(max_val >= threshold), float(max_val), center

    def waitTemplate(
        self,
        template_path,
        timeout=10.0,
        interval=0.2,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        show_value=False,
    ) -> bool:
        """現れるまで待つ。見つかれば True、時間切れなら False。

        while not self.isContainTemplate(...): self.wait(0.5) と自前で
        書く形との違いは3つ。①必ず打ち切るので、想定外の画面へ入っても
        永久に回り続けない ②wait 経由なので停止要求で即座に抜ける
        ③時間切れを例外ではなく False で返すので、見つからなかった
        ときの分岐を呼び出し側で普通に書ける。
        """
        limit = time.perf_counter() + float(timeout)
        while True:
            hit, _, _ = self._matchOnce(
                template_path, threshold, use_gray, crop, mask_path, show_value
            )
            if hit:
                return True
            if time.perf_counter() >= limit:
                logger.debug(f"waitTemplate timeout: {template_path}")
                return False
            self.wait(interval)

    def waitTemplateGone(
        self,
        template_path,
        timeout=10.0,
        interval=0.2,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
    ) -> bool:
        """消えるまで待つ。消えれば True、時間切れなら False。

        ロード中の表示やメッセージ枠が抜けきるのを待つ用途。出現待ちと
        対で用意しておかないと、消える側の while だけが各コマンドへ
        残ることになる。
        """
        limit = time.perf_counter() + float(timeout)
        while True:
            hit, _, _ = self._matchOnce(
                template_path, threshold, use_gray, crop, mask_path
            )
            if not hit:
                return True
            if time.perf_counter() >= limit:
                logger.debug(f"waitTemplateGone timeout: {template_path}")
                return False
            self.wait(interval)

    def waitStable(
        self,
        quiet=0.5,
        timeout=10.0,
        threshold=20,
        interval=0.1,
        crop=None,
        ratio=0.001,
    ) -> bool:
        """画面の動きが止まるまで待つ。止まれば True、時間切れは False。

        テンプレート画像を1枚も用意せずに使えるのが利点。安全側に倒して
        self.wait(3.0) と固定で置いてある箇所を実際の停止検知へ替えると、
        1周あたりの待ちが実測ぶんまで縮む。

        getInterframeDiff は3枚から「動いた画素」だけを残すので、残った
        画素の割合が ratio 未満の状態が quiet 秒続いたら停止とみなす。
        1画素でも残ったら動きとみなす作りにすると、キャプチャのノイズで
        永久に止まらない。
        """
        crop = crop or []

        def gray() -> np.ndarray:
            """現在のフレームを crop してグレースケールで返す。"""
            frame = self.camera.readFrame()
            if len(crop) == 4:
                frame = frame[crop[1] : crop[3], crop[0] : crop[2]]
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        limit = time.perf_counter() + float(timeout)
        f1, f2 = gray(), gray()
        quiet_from = None
        while True:
            self.wait(interval)
            f3 = gray()
            mask = self.getInterframeDiff(f1, f2, f3, threshold)
            moved = float(np.count_nonzero(mask)) / float(mask.size)
            if moved < ratio:
                if quiet_from is None:
                    quiet_from = time.perf_counter()
                elif time.perf_counter() - quiet_from >= float(quiet):
                    return True
            else:
                quiet_from = None
            if time.perf_counter() >= limit:
                logger.debug(f"waitStable timeout: moved={moved:.4f}")
                return False
            f1, f2 = f2, f3

    def getTemplatePosition(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        show_value=False,
    ) -> Optional[Tuple[int, int]]:
        """一致位置の中心 (x, y) を画面全体の座標で返す。無ければ None。

        isContainTemplate も内部では max_loc を出しているが、GUI へ矩形を
        描くためだけに使って捨てている。戻り値の互換を壊さずに位置を
        取れるよう、別名のメソッドとして分ける。
        """
        hit, _, center = self._matchOnce(
            template_path, threshold, use_gray, crop, mask_path, show_value
        )
        return center if hit else None

    def preloadTemplates(
        self, template_paths: List[str], use_gray: bool = True
    ) -> List[str]:
        """先読みして、読めなかったパスの一覧を返す。

        _imread_or_raise が FileNotFoundError を投げるのは判定の瞬間なので、
        数時間走ったあとの分岐で初めてパスの打ち間違いが分かる。開始直後に
        読んでおけば、落ちるものは1秒で落ちる。lru_cache が効くため、
        そのまま暖機にもなる。
        """
        flags = cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR
        missing = []
        for template_path in template_paths:
            try:
                _imread_or_raise(template_path, flags)
            except FileNotFoundError:
                missing.append(template_path)
        if missing:
            logger.warning(f"読み込めないテンプレート: {missing}")
        return missing

    # -- 記録・複数検出・色 -------------------------------------------------

    def saveFrame(self, name: str = "frame", crop=None, folder: str = "Debug") -> str:
        """いまの画面を日時つきで保存し、保存先のパスを返す。

        show_value=True は print するだけなので、放置運用ではログが
        流れて消える。閾値を割ったときの画面が残っていれば、実機を
        止めずに机上で閾値を詰められる。

        名前には日時をミリ秒まで入れる。同じ判定が連続で外れたとき、
        秒までだと後の1枚が前の1枚を上書きしてしまう。
        """
        crop = crop or []
        frame = self.camera.readFrame()
        if len(crop) == 4:
            frame = frame[crop[1] : crop[3], crop[0] : crop[2]]

        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        stamp += f"_{int((time.time() % 1) * 1000):03d}"
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", str(name))
        filespec = path.join(folder, f"{stamp}_{safe}.png")

        # cv2.imwrite は非 ASCII のパスで静かに False を返す。書けたかを
        # 戻り値で見ておかないと「保存したはずの画像が無い」になる。
        if not cv2.imwrite(filespec, frame):
            logger.warning(f"画面を保存できませんでした: {filespec}")
            return ""
        return filespec

    def isContainTemplateDump(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        mask_path=None,
        folder: str = "Debug",
    ) -> bool:
        """判定し、外れたときだけ相関値つきで画面を保存する。

        isContainTemplate と戻り値も使い方も同じ。外れた回の画面が残る
        ので、閾値が渋いのか画面そのものが違うのかを後から切り分け
        られる。当たった回まで保存すると1周で数百枚になり使えない。
        """
        hit, val, _ = self._matchOnce(
            template_path, threshold, use_gray, crop, mask_path
        )
        if not hit:
            stem = path.splitext(path.basename(str(template_path)))[0]
            saved = self.saveFrame(f"{stem}_{val:.3f}", crop, folder)
            logger.debug(f"NG {template_path} val={val:.3f} -> {saved}")
        return hit

    def findAllTemplates(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        max_count: int = 20,
        show_value=False,
    ) -> List[Tuple[int, int]]:
        """閾値を超えた箇所すべての中心座標を、相関の高い順に返す。

        minMaxLoc は最大の1件しか返さないため、「並んでいる数」を数える
        用途には使えない。ここでは閾値を超えた点を全部拾う。

        ただし素直に拾うと、1つの対象に対して隣接する画素が数十件
        ヒットする。個数を数えるのが目的なら、これを間引かなければ
        答えが桁で狂う。テンプレートの幅・高さの半分より近い点は同じ
        対象とみなし、相関の高いほうだけを残す。
        """
        crop = crop or []
        src = self.camera.readFrame()
        if len(crop) == 4:
            src = src[crop[1] : crop[3], crop[0] : crop[2]]
        if use_gray:
            src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)

        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )
        h, w = template.shape[0], template.shape[1]
        res = cv2.matchTemplate(src, template, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)

        ys, xs = np.where(res >= threshold)
        if len(xs) == 0:
            return []

        order = np.argsort(res[ys, xs])[::-1]  # 相関の高い順に見る
        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0
        near_x, near_y = max(1, w // 2), max(1, h // 2)

        found: List[Tuple[int, int]] = []
        for i in order:
            x, y = int(xs[i]), int(ys[i])
            if any(abs(x - px) < near_x and abs(y - py) < near_y for px, py in found):
                continue
            found.append((x, y))
            if len(found) >= max_count:
                break

        if show_value:
            print(f"{template_path} hits: {len(found)}")
        return [(x + dx + w // 2, y + dy + h // 2) for x, y in found]

    def countTemplate(
        self,
        template_path,
        threshold=0.7,
        use_gray=True,
        crop=None,
        max_count: int = 20,
    ) -> int:
        """閾値を超えた箇所の個数を返す（findAllTemplates の件数）。"""
        return len(
            self.findAllTemplates(template_path, threshold, use_gray, crop, max_count)
        )

    def getColorRatio(self, crop, lower_hsv, upper_hsv) -> float:
        """指定領域で、その色が占める割合(0.0〜1.0)を返す。

        「画面が暗転した」「HPバーが赤い」「背景が白い（ロード中）」は、
        テンプレート画像を作るまでもない。HSV なら明るさの揺れに強く、
        グレースケールのテンプレートマッチより壊れにくい。

        色相は環状なので、赤のように 0 をまたぐ範囲は下限のほうが大きい
        値になる。その場合は2つに割って足す（そのまま inRange へ渡すと
        常に0件になり、「赤が無い」と誤判定する）。
        """
        crop = crop or []
        frame = self.camera.readFrame()
        if len(crop) == 4:
            frame = frame[crop[1] : crop[3], crop[0] : crop[2]]
        if frame.size == 0:
            return 0.0

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lo = np.array(lower_hsv, dtype=np.uint8)
        hi = np.array(upper_hsv, dtype=np.uint8)
        if int(lo[0]) <= int(hi[0]):
            mask = cv2.inRange(hsv, lo, hi)
        else:
            lo1 = np.array([lo[0], lo[1], lo[2]], dtype=np.uint8)
            hi1 = np.array([179, hi[1], hi[2]], dtype=np.uint8)
            lo2 = np.array([0, lo[1], lo[2]], dtype=np.uint8)
            hi2 = np.array([hi[0], hi[1], hi[2]], dtype=np.uint8)
            mask = cv2.bitwise_or(
                cv2.inRange(hsv, lo1, hi1), cv2.inRange(hsv, lo2, hi2)
            )
        total = float(mask.shape[0] * mask.shape[1])
        return float(np.count_nonzero(mask)) / total

    def isSimilarColor(self, crop, lower_hsv, upper_hsv, ratio: float = 0.6) -> bool:
        """指定領域が、おおむねその色で占められているかを返す。"""
        return self.getColorRatio(crop, lower_hsv, upper_hsv) >= float(ratio)

    @deprecated(reason="Use discord instead")
    def LINE_image(self, txt: str = "", token: str = "token") -> bool:
        """LINE Notify は 2025/3/31 にサービス終了。送信は行わない。"""
        logger.error(LINE_EOL_MESSAGE)
        return False

    def discord_image(
        self, content: str = "", index: int = 0, name: Optional[str] = None
    ) -> bool:
        """
        Discordにテキストメッセージを送信します。

        Args:
            content (str): 送信するテキストメッセージ。デフォルトは空文字列です。
            index (int): メッセージのインデックス番号。デフォルトは0です。
            name Optional(str): 通知先Webhookの名前。indexよりも優先されます。

        Returns:
            bool: 成功時はTrue、失敗時はFalse

        Raises:
            Exception: Discord通知に失敗した場合
        """
        try:
            if name:
                self.Discord.send_message(index=index, content=content, name=name)
            else:
                self.Discord.send_message(index=index, content=content)
            return True
        except Exception:
            logger.error("Failed to send Discord image notification.")
            print(traceback.format_exc())
            return False
