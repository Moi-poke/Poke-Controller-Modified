#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import atexit
import os
import threading
import time
import traceback
from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional

# Commands配下と同じ絶対importに統一する。相対importでは、パッケージとして
# 読み込む場合と単体で実行する場合とで、読み込めなくなる側が変わるため。
from Commands import CommandBase

# 対話部の実体は CommandDialog.py にある。
#   DialogMixin は PythonCommand へ重ねる（dialogue / dialogue6widget）。
#   PokeConDialogue も再公開する。既存の設定が
#       from Commands.PythonCommandBase import PokeConDialogue
#     と書いていても、そのまま使えるようにするため。
from Commands.CommandDialog import DialogMixin

# 操作APIと待ちの実体は CommandOperate.py にある。
#   OperateMixin は PythonCommand へ重ねる（press / hold / wait / checkIfAlive）。
#   StopThread も操作側に置く。
#     checkIfAlive / _gate が投げる例外なので、操作と同じ場所に置くのが自然。
#     既存の設定が from Commands.PythonCommandBase import StopThread と
#       書いていてもそのまま使える。
from Commands.CommandOperate import OperateMixin, StopThread

# 画像認識の実体は CommandVision.py にある。
#   このファイルは組み立て場所として ImageProcPythonCommand を
#     従来どおりの名前で公開し続ける。利用者の設定は
#     from Commands.PythonCommandBase import ImageProcPythonCommand
#     と書いたまま変えずに使える。
# テンプレート画像まわりの関数（モジュール関数）の実体は CommandVision.py にある。
#   ただし名前はここからも見えるようにしておく。外部の設定や検証が
#     from Commands.PythonCommandBase import clear_template_cache と
#     書いている場合に、置き場所の違いで動かなくなるのを防ぐため。
#   実体は1つ（CommandVision 側）なので、キャッシュも1つで一貫する。
from Commands.CommandVision import (
    LINE_EOL_MESSAGE,
    VisionMixin,
)
from Commands.Keys import KeyPress
from DiscordNotify import Discord_Notify
from LineNotify import Line_Notify
from deprecated import deprecated
from loguru import logger


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


# Python command
class PythonCommand(CommandBase.Command, OperateMixin, DialogMixin):
    def __init__(self) -> None:
        super(PythonCommand, self).__init__()
        # この操作が「誰のものか」を表す名札。
        #   do_safe が KeyPress を作るときに渡し、入力調停（Sender)が
        #   受理／拒否の判断に使う。既定は "script"（自動実行）。
        #   GUI の模擬コントローラやマウス操作は人の手によるものなので、
        #     派生クラス側で "gui" / "mouse" を指定する。
        #   調停が off（既定）の間はどの名札でも挙動は変わらない。
        self.input_source: str = "script"

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
                # 派生が独自 __init__ で super() を呼ばない場合に備え、
                # 名札が無ければ既定を使う（無ければ AttributeError になる）。
                self.keys = KeyPress(
                    ser, source=getattr(self, "input_source", "script")
                )

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
            # 後始末は finally へ一本化した（下の理由を参照）
        finally:
            # 実行中フラグはここで下ろす。thread 参照は None にしない。
            # finally はまだそのスレッドの中なので、None にすると
            # start() のガードが生きているうちに外れ、Start 連打で
            # 旧スレッドの末尾処理と新スレッドが同時に走る窓ができる。
            # 後始末は経路によらず必ず行う。以前は except Exception の
            # 中でしか呼んでおらず、Stop（StopThread）と正常終了では
            # keys.end() が走らなかった。押していたボタンが解放されず、
            # Switch 側で押しっぱなしになる（Stop 時の長押し）。
            self._cleanup(ser)
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
                keys = KeyPress(ser, source=getattr(self, "input_source", "script"))
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
        """一時停止し、押している分を退避して人へ操作を渡す。

        足止めするだけでは、押しっぱなしのまま人へ渡ることになる。人が
        同じボタンを離すと、こちらの押下まで巻き込んで落ちる。そこで
        自分の所有分を退避して姿勢から外してから渡す。
        ゲーム側から見ると、一時停止した時点でボタンが離れる。

        退避した分は resume で戻すため、再開時に押し直す必要はない。
        押していない場面では退避するものが無く、姿勢は変わらない。
        """
        if not self.alive:
            return
        if self._resume_event.is_set():
            self._resume_event.clear()
            self._pause_started = time.perf_counter()
            self._suspendPosture()
            self._setHandedOver(True)
            print("-- paused. --")
            logger.info("Command paused")

    def resume(self) -> None:
        """一時停止を解除し、退避した分を戻す。"""
        if not self._resume_event.is_set():
            elapsed = time.perf_counter() - self._pause_started
            self._paused_total += elapsed
            self._restorePosture()
            self._setHandedOver(False)
            self._resume_event.set()
            print(f"-- resumed. ({elapsed:.1f}s paused) --")
            logger.info(f"Command resumed after {elapsed:.1f}s")

    def _posture_owner(self) -> str:
        """自分が姿勢を申告するときの名札。"""
        keys = getattr(self, "keys", None)
        return str(getattr(keys, "source", "script") or "script")

    def _suspendPosture(self) -> bool:
        """自分の所有分を退避する。対応していない Sender では何もしない。

        古い Sender と組み合わせても動くようにする。書き出しを
        片方だけ行った場合に、例外で一時停止そのものが失敗しないため。
        """
        ser = getattr(getattr(self, "keys", None), "ser", None)
        suspend = getattr(ser, "suspendOwner", None)
        if not callable(suspend):
            return False
        try:
            return bool(suspend(self._posture_owner()))
        except Exception:
            logger.error(f"suspendOwner failed: {traceback.format_exc()}")
            return False

    def _restorePosture(self) -> bool:
        """退避した所有分を戻す。対応していない Sender では何もしない。"""
        ser = getattr(getattr(self, "keys", None), "ser", None)
        restore = getattr(ser, "restoreOwner", None)
        if not callable(restore):
            return False
        try:
            return bool(restore(self._posture_owner()))
        except Exception:
            logger.error(f"restoreOwner failed: {traceback.format_exc()}")
            return False

    def _setHandedOver(self, handed: bool) -> bool:
        """操作を人へ渡している最中かを Sender へ伝える。

        調停の cooldown は『最後に自動側が申告してから何秒』で測る。
        一時停止しても keepalive や別スレッドの申告が続けば、その時計は
        止まらず、人はいつまでも拒否される。渡したという事実を明示的に
        伝えることで、時間に頼らずに判断できるようにする。

        対応していない Sender では何もしない。書き出しを片方だけ行った
        場合に、例外で一時停止そのものが失敗しないため。
        """
        ser = getattr(getattr(self, "keys", None), "ser", None)
        setter = getattr(ser, "setHandedOver", None)
        if not callable(setter):
            return False
        try:
            setter(bool(handed))
            return True
        except Exception:
            logger.error(f"setHandedOver failed: {traceback.format_exc()}")
            return False

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

    # Use time glitch

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


class ImageProcPythonCommand(PythonCommand, VisionMixin):
    """画像認識つきのコマンド基底クラス。

    画像認識の本体は CommandVision.py（VisionMixin）にある。このクラスは
      PythonCommand（実行制御・操作・待ち・通知）と VisionMixin（画像認識）を
      重ねるだけの薄い層である。
    名前・継承関係・コンストラクタ引数は従来どおりなので、既存の設定は
      変えずに使える。
    template_path_name / profilename というクラス属性は持たせない。
    """

    def __init__(self, cam: Any, gui: Any = None) -> None:
        super(ImageProcPythonCommand, self).__init__()
        # 画像認識に使う状態（camera / gui / GPU まわり）は VisionMixin が
        #   用意する。cv2.cuda_GpuMat() は CUDA 無効ビルドで AttributeError に
        #   なるため、実際に GPU 版を呼んだときだけ確保する作りも移してある。
        self._initVision(cam, gui)

    def __post_init__(self) -> None:
        """通知の実体をカメラ付きで作り直す。

        これは画像認識ではなく通知の初期化なので、VisionMixin には置いていない。
        Line_Notify / Discord_Notify はこのファイルが読み込んでおり、
        画像認識側からは見えないため。
        """
        self.Line = Line_Notify(self.camera)
        self.Discord = Discord_Notify(camera=self.camera)

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
