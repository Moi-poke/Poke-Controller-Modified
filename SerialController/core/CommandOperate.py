#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CommandOperate.py - 操作 API と待ち（OperateMixin）.

内容:
  ・操作   press / pressRep / pressEvery / hold / holdEnd
  ・待ち   wait / short_wait / _precise_sleep / _wait_or_stop / _deadline
  ・関所   _gate / _gateRelease / checkIfAlive / _waitResume
  ・その他 timeLeap（Switch の日付を進める操作）

操作と待ちは利用者の設定が最も多く呼ぶ部分である。実行制御
（start / do_safe / 停止）と分けているのは、操作の意味を知りたいときに
実行制御まで読まずに済むようにするためである。

親クラス（PythonCommand）に依存するもの:
  ・属性     keys / alive / _stop_event / _resume_event
  ・メソッド _cleanup / _pausedSeconds
  Mixin なので、重ねる側がこれらを持っていることが前提になる。
  逆に実行制御の側は checkIfAlive をここから使う（相互に使い合う関係）。

互換:
  PythonCommandBase.py が OperateMixin を PythonCommand へ重ねるので、
  press / hold / wait などの呼び出し方は変わらない。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from core.Keys import Button, Direction
from loguru import logger


# 停止要求を伝える例外。checkIfAlive / _gate / _gateRelease が投げるので、
#   操作側と一緒に置いている。
#   PythonCommandBase.py から再公開するので、
#       from Commands.PythonCommandBase import StopThread
#     と書いている既存の設定はそのまま使える。
class StopThread(Exception):
    pass


class OperateMixin:
    """操作 API と待ち。PythonCommand へ重ねて使う。

    外向きの API（press / pressRep / pressEvery / hold / holdEnd / wait / short_wait /
      checkIfAlive / timeLeap）は名前・引数・意味とも1文字も変えていない。
    """

    # 親（PythonCommand）が用意するもの。Mixin 単体には無いため型だけ宣言する。
    keys: Any
    alive: bool
    _stop_event: threading.Event
    _resume_event: threading.Event
    _cleanup: Callable[..., None]
    _pausedSeconds: Callable[[], float]

    # 待ちを刻む幅は _TICK 側で持つ。_SPIN_MARGIN はスピンを廃止した
    # 現在は未使用だが、外部コマンドが参照している可能性があるため
    # 名前だけ残す（削除すると AttributeError になりうる）。
    _SPIN_MARGIN = 0.001

    # 停止要求の拾い幅。50msだとStop→抜けが最大50ms遅れるため20msへ。
    # Event.waitで寝るのでCPU負荷は増えない（毎秒50回起きるだけ）。
    _TICK = 0.02

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

    # press button every interval(s), holding duration(s)
    def pressEvery(self, buttons: Any, interval: float, duration: float) -> None:
        """press開始間隔で duration だけ押す。waitの逆算が要らない。

        press(btn, duration, wait) が「押下＋解放後待ち」なのに対し、
        こちらは「開始間隔＋保持幅」で指定する。連打の周期を守りたい
        ときに使う（実測：duration 0.05・interval 0.09 で全通）。
        duration が interval を超えたら interval に丸め、理由を残す
        （負待ちで固まらないため）。
        """
        self._gate()
        interval = float(interval)
        duration = float(duration)
        if duration > interval:
            logger.warning(
                f"pressEvery: duration({duration}) が interval({interval}) を"
                "超えたため interval に丸めます"
            )
            duration = interval
        start = time.perf_counter()
        self.keys.input(buttons)
        self.wait(duration)
        self.keys.inputEnd(buttons)
        self.wait(max(0.0, interval - (time.perf_counter() - start)))
        self.checkIfAlive()

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

    def _precise_sleep(self, wait: float) -> None:
        """指定時間だけ待つ。停止要求が来たら待ち切らずに戻る。

        末尾を少しだけ忙しく回して精度を確保する作りにはしていない。
        press() の既定が duration=0.1 / wait=0.1 のため1操作あたり2回通り、
        実行中ずっと CPU を使うことになるためである。

        さらに一時停止ぶんの補正もその回転で消化すると、止めた秒数だけ
        再開直後に1コアが張り付き、映像描画(33ms)とログ描画(200ms)まで
        巻き添えにする。待ちは _wait_or_stop へ一本化している。

        停止要求は _wait_or_stop が _TICK 刻みで拾う。待機中に
        停止を見ない作りでは、wait(10) の最中に Stop を押しても最大10秒
        止まらなかったためである。
        """
        # 待ちの本体。停止・一時停止は _wait_or_stop が拾う。
        # 末尾を忙しく回す作りはやめている（補正ぶんを1コア全開で
        # 消化し、再開直後に映像とログの描画を引きずっていたため）。
        self._wait_or_stop(wait)

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
        while not self._resume_event.wait(self._TICK):
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
