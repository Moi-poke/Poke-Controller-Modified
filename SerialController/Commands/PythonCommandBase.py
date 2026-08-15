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
        self._running: bool = False  # start〜do_safe 終了までの実行中フラグ
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
        # ダイアログを GUI スレッドで作るためのルート。Window が生成時に
        # 差し込む。CUI 実行やテストでは None のままで、その場合は従来
        # どおり呼び出し元のスレッドで直に作る。
        self.gui_root: Optional[Any] = None
        # COM の設定は Window が Start の直前にここへ写す（通常の Python
        # 値の辞書）。tk 変数を持たせるとワーカースレッドから Tcl を触る
        # ことになるため、値だけを受け取る。無ければ reload_com_port は
        # 理由を出して False を返す（設定ファイルを読み直さない）。
        self.serial_config: Optional[Dict[str, Any]] = None

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
        # 初期化も try の内側で行う。ここを外に置くと、Line_Notify /
        # Discord_Notify のコンストラクタや KeyPress の生成で落ちたとき
        # except にも finally にも入らず、後始末（_cleanup）が呼ばれない。
        # postProcess が永久に呼ばれないため Window は実行中のまま固まり、
        # しかもワーカー内の未捕捉例外は stderr へ出るのでログ欄にも出ない。
        try:
            self.__post_init__()

            if self.keys is None:
                self.keys = KeyPress(ser)

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
            # 実行中フラグはここで下ろす。thread 参照は None にしない。
            # finally はまだそのスレッドの中なので、None にすると
            # start() のガードが生きているうちに外れ、Start 連打で
            # 旧スレッドの末尾処理と新スレッドが同時に走る窓ができる。
            self._running = False

    def _cleanup(self, ser: Any = None) -> None:
        """コマンド終了時の後始末。二重に呼ばれても安全にする。"""
        keys = self.keys
        if keys is None and ser is not None:
            # 途中で keys を捨てられていてもボタンは必ず離す。ただし
            # ここが失敗すると postProcess へ到達せず、Window が実行中の
            # まま固まる。KeyPress の生成で落ちた流れではここも同じ例外に
            # なるため、後始末の続行を優先して握る。
            try:
                keys = KeyPress(ser)
            except Exception:
                logger.error(f"Failed to recreate KeyPress: {traceback.format_exc()}")
        if keys is not None:
            try:
                keys.end()
            except Exception:
                logger.error(f"Failed to release keys: {traceback.format_exc()}")
        self.keys = None

        postProcess, self.postProcess = self.postProcess, None
        if postProcess is not None:
            # 後始末の失敗で本来の停止理由を上書きしない。ここで例外が
            # 抜けると checkIfAlive が StopThread を投げられず、正常な
            # 停止が do_safe の except Exception へ落ちて「異常終了」に
            # 化ける。ログにだけ残して再送出はしない。
            try:
                postProcess()
            except Exception:
                logger.error(f"postProcess failed: {traceback.format_exc()}")

    def start(self, ser: Any, postProcess: Optional[Callable[[], None]] = None) -> bool:
        # 実行中かどうかは _running で見る。thread.is_alive() だけだと、
        # do_safe の finally がまだそのスレッドの中で走っている最中に
        # 判定が通り、旧スレッドの末尾処理と新スレッドが同時に走る。
        # 開始できたかどうかは戻り値で返す。呼び出し元（Window）は例外の
        # 有無しか見られず、「何も開始していないのに実行中の表示になる」
        # 状態を検出できなかった。
        if self._running or (self.thread is not None and self.thread.is_alive()):
            print("-- command is already running. --")
            logger.warning("Command is already running")
            return False

        self._running = True
        self.alive = True
        self._stop_event.clear()
        # 一時停止の状態は毎回まっさらにする。前回の一時停止が残ると、
        # 次の実行が最初の待ちで固まる。
        self._resume_event.set()
        self._paused_total = 0.0
        self.postProcess = postProcess
        # daemon=True にしないと、GUI を閉じてもコマンドのスレッドが
        # 生きているあいだプロセスが終わらない。画面だけ消えて残り続け、
        # 利用者からは「終了できない」ように見える。停止要求は出すが、
        # 長い wait の最中や外部I/O待ちでは即座に抜けられないため、
        # 最後の逃げ道としてデーモンにしておく。
        # 生成と開始の失敗で状態を残さない。ここで落ちると _running が
        # True のまま、postProcess も握ったままになり、以後この
        # コマンドは二重起動ガードに阻まれて二度と起動できなくなる。
        try:
            self.thread = threading.Thread(
                target=self.do_safe, args=(ser,), daemon=True
            )
            self.thread.start()
        except Exception:
            logger.error(f"Failed to start command thread: {traceback.format_exc()}")
            print("コマンドのスレッドを開始できませんでした。")
            self._running = False
            self.thread = None
            self.postProcess = None
            return False
        return True

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

    def _subLogQueue(self) -> Optional[Any]:
        """副ログ用のキューを返す。取れなければ None。

        旧実装は __main__ → import Window の順に sub_log_queue を探して
        いた。しかし Window.py はモジュール直下にこの名前を持たない
        （実体は LogPane.sub_log_queue で、Window は描画のとき参照する
        だけ）。そのため必ず None が返り、print2 / log2 は常に通常の
        print へ落ちて副ログ欄は永久に空だった。例外が出ないぶん、
        「動いているのに何も出ない」形で気づきにくい。

        LogPane を直接見る。LogPane.py が import するのは queue /
        threading / tkinter / typing だけで Commands を参照しないため
        循環しない（import Window のほうが Window→Commands→本モジュール
        の循環になっていた）。常に通常のモジュールとして読まれるので、
        Window.py が __main__ として動くときにキューが2つできる問題も
        起きない。CUI やテストでも読み込むだけで取れる。

        self.sub_log_queue を先に見るのは差し替え用。Window から明示的に
        注入したい場合や、テストで受け皿を差し込む場合に使う。
        """
        queue = getattr(self, "sub_log_queue", None)
        if queue is not None:
            return queue
        try:
            import LogPane
        except Exception:
            return None
        return getattr(LogPane, "sub_log_queue", None)

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
        """押しっぱなしを解放する。Pause では足止めしない。

        _gate() を通さないのは意図的。一時停止は「押下状態を保ったまま
        処理だけ止める」方針なので、解放をそこで足止めすると押した
        ままで止まる。解放は常に許す。ただし keys が捨てられている
        場合だけは AttributeError ではなく StopThread にする（停止
        直後に呼ばれると、本来の停止が別の例外へ化けるため）。
        """
        self._gateRelease()
        self.keys.holdEnd(buttons)
        self.checkIfAlive()

    def _gateRelease(self) -> None:
        """解放系の関所。停止だけを見て、一時停止では足止めしない。"""
        if self.keys is None:
            raise StopThread("keys are already released")

    # 待ちを刻む幅は _TICK 側で持つ。_SPIN_MARGIN はスピンを廃止した
    # 現在は未使用だが、外部コマンドが参照している可能性があるため
    # 名前だけ残す（削除すると AttributeError になりうる）。
    _SPIN_MARGIN = 0.001

    def _precise_sleep(self, wait: float) -> None:
        """指定時間だけ待つ。停止要求が来たら待ち切らずに戻る。

        旧実装は末尾を _SPIN_MARGIN だけビジースピンして精度を
        確保していた。press() の既定が duration=0.1 / wait=0.1 の
        ため1操作あたり2回通り、実行中ずっと CPU を焼いていた。

        さらに一時停止ぶんの補正もこのスピンで消化していたため、
        止めた秒数だけ Resume 直後に1コアが張り付き、GIL を握って
        映像描画(33ms)とログ描画(200ms)まで巻き添えにしていた。
        PCB-11 でスピンを廃止し、待ちは _wait_or_stop へ一本化した。

        停止要求は _wait_or_stop が _TICK 刻みで拾う。以前は待機中に
        停止を見ておらず、wait(10) の最中に Stop を押しても最大10秒
        止まらなかった。
        """
        # 待ちの本体。停止・一時停止は _wait_or_stop が拾う。
        # 末尾のスピンは PCB-11 で廃止した（補正ぶんを1コア全開で
        # 消化し、Resume 直後に映像とログの描画を引きずっていた）。
        self._wait_or_stop(wait)

    _TICK = 0.05

    def _wait_or_stop(self, timeout: float) -> bool:
        """timeout 秒待つ。停止要求が来たら True を返して即座に戻る。

        _TICK 刻みで待ち直す。旧実装は _stop_event.wait(timeout) で一息に
        待っていたが、Pause は _resume_event.clear() で行うため、この
        wait は起きない。結果、その待ちが満了するまで（wait(10) なら最大
        10秒）足止めに入らなかった。刻んでおけば停止も一時停止も最大
        _TICK で拾える。

        刻んでも負荷は増えない。Event.wait は OS のタイマーで寝るので、
        50ms 刻みなら毎秒20回起きるだけで、ビジースピンとは桁が違う。
        旧実装では補正ぶんの時間がすべて末尾のスピンで消化され、Resume
        直後にコアが張り付いて映像描画(33ms)とログ描画(200ms)を引きずって
        いた。

        一時停止の最中は残り時間を減らさない。止めているあいだに時計が
        進むと、再開した直後に「待ち終わったこと」にされ、次の操作が
        即座に飛ぶ。押下時間や画面遷移の待ちが飛ぶと手順がずれる。
        """
        remain = float(timeout)
        while remain > 0:
            if not self._resume_event.is_set():
                # 一時停止中。remain は減らさない（時間も止める）
                self._waitResume()
                if self._stop_event.is_set():
                    return True
                continue
            step = min(self._TICK, remain)
            if self._stop_event.wait(step):
                return True
            remain -= step
        return False

    def _waitResume(self) -> None:
        """一時停止が解除されるまで待つ。停止要求が来たらすぐ戻る。"""
        while not self._resume_event.wait(0.05):
            if self._stop_event.is_set():
                return

    def _deadline(self, timeout: float) -> Callable[[], bool]:
        """「時間切れか」を返す関数を作る。一時停止ぶんは数えない。

        実時間の絶対期限（perf_counter() + timeout）にすると、Pause して
        いるあいだも時計だけが進む。timeout より長く止めてから再開すると、
        1回も追加の照合をしないまま時間切れと判定され、画面認識の分岐が
        誤った側へ進む。「少し手を離すために止めた」だけで手順が壊れる。

        補正式は _precise_sleep と同じ（経過 − 一時停止の増分）。待ちの
        期限を測る場所はここへ寄せ、式が方々へ散らないようにする。
        """
        start = time.perf_counter()
        paused0 = self._pausedSeconds()
        limit = float(timeout)

        def expired() -> bool:
            elapsed = time.perf_counter() - start - (self._pausedSeconds() - paused0)
            return elapsed >= limit

        return expired

    def _runElapsed(self) -> float:
        """一時停止ぶんを除いた経過の目盛りを返す（差分だけに意味がある）。

        「静止が quiet 秒続いたか」のように、2点間の間隔を測る用途で使う。
        実時間で測ると、途中で一時停止した分まで「続いた」ことになる。
        """
        return time.perf_counter() - self._pausedSeconds()

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
    ) -> list | dict | None:
        """入力ダイアログを出し、閉じられるまで待って結果を返す。Cancel は None。"""
        return self._runDialogue(title, message, need, mode=0)

    def dialogue6widget(
        self, title: str, dialogue_list: list, need: type = list
    ) -> list | dict | None:
        """6種のウィジェットに対応した入力ダイアログ版。Cancel は None。"""
        return self._runDialogue(title, dialogue_list, need, mode=1)

    # 停止要求のあと、ダイアログが閉じ切るのを待つ上限(秒)。
    _DIALOGUE_CLOSE_WAIT = 1.0
    # ダイアログが開かないまま待ち続けたときに警告を出す間隔(秒)。
    _DIALOGUE_OPEN_TIMEOUT = 30.0

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
        holder = {}

        def build() -> None:
            # ここは GUI スレッド。widget の生成と mainloop 的な待ちは
            # すべてこの中で完結する。PokeConDialogue.__init__ の末尾は
            # wait_window で、閉じられるまでここから戻らない。
            try:
                self.message_dialogue = tk.Toplevel(root)
                holder["top"] = self.message_dialogue
                dlg = PokeConDialogue(self.message_dialogue, title, message, mode=mode)
                box["value"] = dlg.ret_value(need)
            except Exception:
                box["error"] = traceback.format_exc()
            finally:
                self.message_dialogue = None
                holder.pop("top", None)
                done.set()

        def closeOnGui() -> None:
            """ダイアログを閉じる。必ず GUI スレッドから呼ぶこと。

            既に閉じられている・Window ごと壊れている場合があるので、
            winfo_exists() を見てから destroy し、TclError は握る。
            二重 destroy と、破棄後のアクセスを両方防ぐ。
            """
            top = holder.get("top")
            if top is None:
                return
            try:
                if top.winfo_exists():
                    top.destroy()
            except tk.TclError:
                logger.debug("dialogue was already destroyed")

        root = self._guiRoot()
        if root is None:
            # GUI が無い（CUI 実行・テスト）ときは従来どおり直に作る
            build()
        else:
            root.after(0, build)

        waited = 0.0
        while not done.wait(0.1):
            waited += 0.1
            if self._stop_event.is_set():
                # 停止要求。以前はここで抜けるだけだったため、ダイアログが
                # 画面に残ったままになっていた。_stop_event はワーカー側の
                # 待ちを解くだけで、GUI スレッドの wait_window には作用
                # しない。build() の finally も走らないので message_dialogue
                # も None に戻らない。UI は idle に戻るのにダイアログだけ
                # 残り、Window を閉じたあとで OK を押すと TclError になる。
                logger.warning("Stop requested while a dialogue is open")
                if root is not None:
                    # destroy は GUI スレッドへ依頼する。破棄されると
                    # wait_window が解け、build() が finally まで進んで
                    # done がセットされる。
                    root.after(0, closeOnGui)
                    done.wait(self._DIALOGUE_CLOSE_WAIT)
                else:
                    closeOnGui()
                self.checkIfAlive()
                # checkIfAlive は StopThread を送出するため通常ここへは
                # 来ない。alive を落とさずに停止要求だけ来た場合の保険。
                return [] if need is list else {}
            if root is not None and waited >= self._DIALOGUE_OPEN_TIMEOUT:
                # mainloop が回っていないと after(0, build) は実行されず、
                # done が永久にセットされない。黙って待ち続けると停止も
                # 終了もできなくなるので、気づけるよう定期的に知らせる。
                logger.warning(
                    f"dialogue is not responding for {self._DIALOGUE_OPEN_TIMEOUT}s"
                    f" (title={title})"
                )
                waited = 0.0

        if "error" in box:
            raise RuntimeError(f"dialogue failed:\n{box['error']}")
        return box.get("value")

    def _guiRoot(self) -> Optional[Any]:
        """ダイアログを載せる tk のルートを返す。無ければ None。

        以前は gui（画像認識コマンドが受け取るプレビュー）しか見て
        いなかった。通常の PythonCommand は cmd_class() で作られ gui を
        持たないため、必ず None になり、ダイアログをワーカースレッドで
        直接生成していた。スレッド安全化が画像認識コマンドにしか効いて
        いなかったことになる。Window 側が生成時に渡す gui_root を先に
        見ることで、すべての種類のコマンドで GUI スレッドへ渡せる。
        """
        root = getattr(self, "gui_root", None)
        if root is not None and hasattr(root, "after"):
            return root

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
        """生の文字列をそのまま送る。1件ごとに停止・一時停止を見る。

        旧実装はリストごと Keys 側へ丸投げしていたため、次の3つがあった。
        ①送信の手前で _gate() を通らないので、Pause 中でも送信が始まり、
        停止要求も見ない ②Keys 側の time.sleep は停止イベントを見ないため、
        リストの途中で Stop しても全件を送り終えるまで止まらない（waittime
        が長いとその間ずっと効かない）③zip が短い方に合わせて黙って
        打ち切るので、数がずれると後ろが無言で送られない。

        ループをこちら側へ持ち上げ、1件ごとに関所（_gate）と停止対応の
        待ち（wait）を挟む。Keys.py は触らずに済む。self.keys が None の
        場合も _gate が StopThread にするので、AttributeError にならない。
        """
        if len(serialcommands) != len(waittime):
            raise ValueError(
                f"serialcommands({len(serialcommands)}件)と"
                f"waittime({len(waittime)}件)の数が違います。"
            )
        # 余計なものが付いている可能性があるので確認して削除する
        checkedcommands = []
        for row in serialcommands:
            checkedcommands.append(row.replace("\r", "").replace("\n", ""))

        for row, wtime in zip(checkedcommands, waittime):
            self._gate()
            self.wait(float(wtime))
            # 待ちはこちらで済ませたので、Keys 側では待たせない
            self.keys.serialcommand_direct_send([row], [0.0])

    # Reload COM port (temporary function)
    def reload_com_port(self, retry: int = 3) -> bool:
        """COM ポートを開き直す。

        旧実装は closeSerial 後に自分自身を再帰呼び出ししていたため、
        切断に失敗して isOpened() が True を返し続けると RecursionError で
        落ちた。回数上限つきのループに置き換える。

        設定は Window が Start の直前に serial_config へ写した通常値だけを
        使う。ここで tk 変数（settings.com_port など）を読んではいけない。
        このメソッドはワーカースレッドで走るため、Tcl インタプリタを別の
        スレッドから触ることになり Tkinter の制約に反する。設定ファイルの
        読み直し（Settings.GuiSettings / configparser）も行わない。設定を
        読む責務を Window とここの2箇所に置くと、画面に出ている値と実際に
        繋ぐ先が食い違う経路ができる。
        """
        if self.keys is None or getattr(self.keys, "ser", None) is None:
            msg = "シリアルが未接続のため COM ポートを開き直せません。"
            print(msg)
            logger.error(msg)
            return False

        config = self._serialConfig()
        if config is None:
            return False

        port = config["com_port"]
        name = config["com_port_name"]
        baud = config["baud_rate"]

        for _ in range(max(1, retry)):
            if self.keys.ser.isOpened():
                print("Port is already opened and being closed.")
                self.keys.ser.closeSerial()
                if self.keys.ser.isOpened():
                    logger.warning("Failed to close the port. retrying...")
                    self.wait(0.5)
                    continue

            if self.keys.ser.openSerial(port, name, baud):
                msg = f"COM Port {name or port} connected successfully"
                print(msg)
                logger.debug(msg)
                return True

            self.wait(0.5)

        msg = f"COM Port {name or port} failed to reconnect"
        print(msg)
        logger.error(msg)
        return False

    def _serialConfig(self) -> Optional[Dict[str, Any]]:
        """Window が写した COM 設定を検証して返す。不正なら None。

        代わりの設定をここで作らないのが要点。無ければ「開き直せない」と
        知らせて失敗する。黙って既定の settings.ini を読むと、プロファイル
        起動中の台が別の台の COM 番号へ繋ぎに行く（launcher.py はプロファイル
        ごとに別プロセスを起こすので、並列起動は現実的な運用）。
        """
        config = getattr(self, "serial_config", None)
        if not isinstance(config, dict):
            msg = "COM の設定を受け取っていないため開き直せません。"
            print(msg)
            logger.error(
                f"serial_config is missing or invalid: {type(config).__name__}"
            )
            return None

        try:
            port = int(config["com_port"])
            name = str(config["com_port_name"])
            baud = int(config["baud_rate"])
        except (KeyError, TypeError, ValueError) as e:
            msg = f"COM の設定が不正なため開き直せません: {e}"
            print(msg)
            logger.error(msg)
            return None

        if not name and port <= 0:
            msg = "COM の設定が空のため開き直せません。"
            print(msg)
            logger.error(msg)
            return None

        return {"com_port": port, "com_port_name": name, "baud_rate": baud}


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

        # self._logger は存在しない（__init__ で作っていない）。ここが
        # AttributeError になると、本来は警告を出して続行する経路が
        # build() の except で拾われ、RuntimeError: dialogue failed へ
        # 化けていた。引数の指定ミスがコマンド全体の異常終了になる。
        logger.warning(f"Wrong arg: {need}. Returns list instead.")
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


TEMPLATE_PATH = path.normpath(
    path.join(path.dirname(path.dirname(path.abspath(__file__))), "Template")
)


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
    """テンプレート画像のキャッシュ（CPU 側）を捨てる。

    ★単独で呼ばないこと。GPU 側のキャッシュ（clearCudaCache）は別に
    持っているため、片方だけ捨てると CPU は新しい画像・GPU は古い画像
    で判定する。差し替え時は clearTemplateCaches() を使う。

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
        """GPU 側のキャッシュを捨てる。

        ★単独で呼ばないこと。CPU 側（clear_template_cache）と
        別管理のため、片方だけ捨てると両者で違う画像を使う。
        差し替え時は clearTemplateCaches() を使う。
        """
        self._cuda_matchers.clear()
        self._cuda_templates.clear()

    def clearTemplateCaches(self) -> None:
        """テンプレートのキャッシュを CPU・GPU まとめて捨てる。

        実行中に画像を差し替えたときは必ずこちらを呼ぶ。片方だけ
        捨てると、CPU 版は新しい画像・GPU 版は古い画像で判定し、
        「差し替えたのに直らない」形でしか症状が出ない。
        """
        clear_template_cache()
        self.clearCudaCache()

    def __post_init__(self) -> None:
        self.Line = Line_Notify(self.camera)
        self.Discord = Discord_Notify(camera=self.camera)

    # -- 入力の検証（画像認識の共通前処理） ---------------------------------

    def _readFrameOrRaise(self) -> np.ndarray:
        """現在のフレームを返す。取得できなければ RuntimeError にする。

        readFrame() は未接続・Disable 中・取得スレッド停止のいずれでも
        None を返す。そのまま crop すると TypeError: NoneType is not
        subscriptable、crop 無しなら cvtColor で cv2.error になり、
        どちらもメッセージから原因（カメラなのか crop 指定なのか）が
        読み取れない。ここで止めて言い切る。
        """
        camera = getattr(self, "camera", None)
        if camera is None:
            raise RuntimeError("カメラが割り当てられていません。")
        frame = camera.readFrame()
        if frame is None or getattr(frame, "size", 0) == 0:
            raise RuntimeError(
                "カメラから画像を取得できません"
                "（未接続 / Disable / 取得スレッド停止）。"
            )
        return frame

    @staticmethod
    def _cropOrRaise(src: np.ndarray, crop: Any) -> np.ndarray:
        """crop を検査してから切り出す。おかしければ ValueError。

        numpy のスライスは範囲外でも例外を出さず、黙って狭い配列を返す。
        crop の指定ミスが「別の場所を照合し続ける」形で表面化するため、
        閾値をいくら下げても直らない。これがこの一連で最も危ない。
        切り詰めて続行せず、その場で止めるのが要点。
        """
        if not crop:
            return src
        if len(crop) != 4:
            raise ValueError(f"crop は [x1, y1, x2, y2] の4要素です: {crop}")
        x1, y1, x2, y2 = (int(v) for v in crop)
        height, width = src.shape[0], src.shape[1]
        if x1 >= x2 or y1 >= y2:
            raise ValueError(f"crop の左右または上下が逆です: {crop}")
        if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
            raise ValueError(f"crop が画面({width}x{height})の外を指しています: {crop}")
        return src[y1:y2, x1:x2]

    @staticmethod
    def _checkTemplate(src: np.ndarray, template: Any, mask: Any = None) -> None:
        """テンプレートとマスクの整合を見る。合わなければ ValueError。

        いずれも cv2.error になる条件だが、cv2 のメッセージは行列の
        次元しか語らないため、use_gray の指定漏れなのかテンプレートの
        取り違えなのかが分からない。
        """
        if template is None or getattr(template, "size", 0) == 0:
            raise ValueError("テンプレート画像が空です。")
        if template.ndim != src.ndim:
            raise ValueError(
                f"色の形式が違います（画面 ndim={src.ndim} /"
                f" テンプレート ndim={template.ndim}）。use_gray を揃えてください。"
            )
        if src.ndim == 3 and template.shape[2] != src.shape[2]:
            # ndim が同じでもチャンネル数は違いうる。アルファ付き PNG を
            # IMREAD_COLOR 以外で読むと 4ch になり、画面(3ch)と食い違う。
            raise ValueError(
                f"チャンネル数が違います（画面 {src.shape[2]}ch /"
                f" テンプレート {template.shape[2]}ch）。"
                "アルファ付きの画像は mask_path で渡してください。"
            )
        if template.shape[0] > src.shape[0] or template.shape[1] > src.shape[1]:
            raise ValueError(
                f"テンプレート({template.shape[1]}x{template.shape[0]})が"
                f"照合範囲({src.shape[1]}x{src.shape[0]})より大きいです。"
                "crop の指定を見直してください。"
            )
        if mask is not None and mask.shape[:2] != template.shape[:2]:
            raise ValueError(
                f"マスク({mask.shape[1]}x{mask.shape[0]})とテンプレート"
                f"({template.shape[1]}x{template.shape[0]})の大きさが違います。"
            )

    def _prepareSrc(self, crop: Any = None, use_gray: bool = True) -> np.ndarray:
        """readFrame → crop → 色変換 をまとめて行う（検証つき）。

        crop を先に切ってから色変換する。逆にすると使わない領域まで
        変換することになり、crop が全体の 1/9 でも 1280x720 の全面を
        変換してしまう（判定ループでは毎回この無駄が乗る）。
        """
        src = self._cropOrRaise(self._readFrameOrRaise(), crop)
        if use_gray:
            src = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
        return src

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
        crop = crop or []
        src = self._prepareSrc(crop, use_gray)

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

        self._checkTemplate(src, template, mask)
        w, h = template.shape[1], template.shape[0]

        res = cv2.matchTemplate(src, template, method, mask)
        # マスク併用の TM_CCORR_NORMED は分母0の領域で NaN を返しうる。
        # NaN が混じると minMaxLoc の結果が不定になるため潰しておく。
        res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if show_value:
            print(template_path + " ZNCC value: " + str(max_val))

        # crop したときは切り出した中の座標なので、画面全体の座標へ戻す。
        # 足さないと矩形が crop の左上ぶん左上へずれて描かれる。
        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0
        top_left = (max_loc[0] + dx, max_loc[1] + dy)
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

        crop = crop or []
        src = self._prepareSrc(crop, use_gray)
        # crop したときは切り出した中の座標になるので、矩形を描く前に
        # 画面全体の座標へ戻す。足さないと crop の左上ぶんずれる。
        dx = crop[0] if len(crop) == 4 else 0
        dy = crop[1] if len(crop) == 4 else 0

        max_val_list = []
        judge_threshold_list = []
        for template_path in template_path_list:
            template = _imread_or_raise(
                template_path,
                cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
            )
            self._checkTemplate(src, template)
            w, h = template.shape[1], template.shape[0]

            method = cv2.TM_CCOEFF_NORMED
            res = cv2.matchTemplate(src, template, method)
            # 他の照合と同じく NaN を潰す。TM_CCOEFF_NORMED は分母に
            # 「テンプレートの平均からの偏差の二乗和」を持つため、真っ白・
            # 真っ黒など定数のテンプレートでは分母が 0 になり NaN が出る。
            # minMaxLoc の結果が不定になるうえ、np.argmax は NaN を最大と
            # 見なすので、1枚でも定数テンプレートが混ざるとそれが常に
            # 勝者になり、他がどれだけ一致していても無視される。
            res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            if show_value:
                print(template_path + " ZNCC value: " + str(max_val))

            top_left = (max_loc[0] + dx, max_loc[1] + dy)
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
        """CUDA を使ったテンプレートマッチング（グレースケール専用）。

        マッチャを CV_8UC1 で作るため、カラー(3ch)の GpuMat を渡すと
        アサーション失敗になる。CUDA の TM_CCOEFF_NORMED は多チャンネル
        非対応で、dtype を変えるだけでは解決しない手法側の制約のため、
        use_gray=False はここで断る。黙って誤った結果を返すより、
        「この関数では出来ない」と言い切って CPU 版へ誘導する。

        not_show_false は本体で参照していない（互換のため残置）。
        """
        if not use_gray:
            raise ValueError(
                "isContainTemplateGPU はグレースケールのみ対応です。"
                "カラーで照合する場合は isContainTemplate() を"
                "使ってください。"
            )
        if not self._ensure_cuda():
            raise RuntimeError(
                "CUDA 対応の OpenCV が見つかりません。"
                "isContainTemplate() を使ってください。"
            )

        src = self._prepareSrc(None, use_gray)

        self.gsrc.upload(src)

        # CPU 版と同じ入力検証を通す。GPU 側だけ検証が無いと、
        # テンプレートが照合範囲より大きい場合に cv2 のアサーション
        # メッセージだけが出て、原因が crop なのか画像なのか分からない。
        # 検証には CPU 上のテンプレートが要るので、キャッシュと同じ
        # 読み方でもう一度読む（lru_cache が効くので実費は無い）。
        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )
        self._checkTemplate(src, template)
        gtmpl = self._cudaTemplate(template_path, use_gray)

        method = cv2.TM_CCOEFF_NORMED
        matcher = self._cudaMatcher(cv2.CV_8UC1, method)
        gresult = matcher.match(self.gsrc, gtmpl)
        resultg = gresult.download()
        # CPU 版と同じく NaN を潰す。定数テンプレート（真っ白・真っ黒）
        # では TM_CCOEFF_NORMED の分母が 0 になり NaN が出るため。
        resultg = np.nan_to_num(resultg, nan=0.0, posinf=0.0, neginf=0.0)
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
    #
    # 期限は必ず _deadline() で測る。実時間の絶対期限にすると、一時停止
    # しているあいだも時計だけが進み、再開した直後に「時間切れ」と判定
    # されて1回も照合せずに False を返す。

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
        src = self._prepareSrc(crop, use_gray)

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

        self._checkTemplate(src, template, mask)
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
        expired = self._deadline(timeout)
        while True:
            hit, _, _ = self._matchOnce(
                template_path, threshold, use_gray, crop, mask_path, show_value
            )
            if hit:
                return True
            if expired():
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
        expired = self._deadline(timeout)
        while True:
            hit, _, _ = self._matchOnce(
                template_path, threshold, use_gray, crop, mask_path
            )
            if not hit:
                return True
            if expired():
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
            return self._prepareSrc(crop, True)

        expired = self._deadline(timeout)
        f1, f2 = gray(), gray()
        quiet_from = None
        while True:
            self.wait(interval)
            f3 = gray()
            mask = self.getInterframeDiff(f1, f2, f3, threshold)
            moved = float(np.count_nonzero(mask)) / float(mask.size)
            if moved < ratio:
                if quiet_from is None:
                    # 静止し始めた時刻。実時間で持つと、一時停止していた
                    # あいだも「静止が続いた」ことになり、再開した瞬間に
                    # 停止とみなして早々に True を返す。
                    quiet_from = self._runElapsed()
                elif self._runElapsed() - quiet_from >= float(quiet):
                    return True
            else:
                quiet_from = None
            if expired():
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
        frame = self._cropOrRaise(self._readFrameOrRaise(), crop)

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
        src = self._prepareSrc(crop, use_gray)

        template = _imread_or_raise(
            template_path,
            cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR,
        )
        self._checkTemplate(src, template)
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
        frame = self._cropOrRaise(self._readFrameOrRaise(), crop)

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
