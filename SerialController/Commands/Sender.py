#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Sender.py - 姿勢（押下状態）を1つに持ち、Transport へ渡す層。
# コメント方針: なぜこうするかの理由を書く。外部文書への参照は付けない。
# 下記の互換 import は Sender からは使わないが、モジュール直下の名前が
#   消えると外部コードの参照が壊れるため残す（未使用でよい）。
import os  # noqa: F401
import platform  # noqa: F401
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from logging import DEBUG, NullHandler, getLogger
from typing import Any, Dict, Optional

import InputLog
import serial  # noqa: F401

# 送信の下回り（線を開く・閉じる・1行書き出す）は Transport が持つ。
#   Sender は「姿勢」と「入力ログ」を持ち、何で運ぶかは知らない。
#   運び方を替えるときは Transport を差し替える。
from Commands import Transport

# 互換のため、従来 Sender の直下にあった定数をここからも見えるようにする。
#   実体は Transport 側の1つ。外部が Sender.MIN_SEND_INTERVAL と
#     書いていても壊れない（設定・検証コードが読む可能性がある）。
from Commands.Transport import (
    MIN_SEND_INTERVAL,
)

# 入力ログの既定。ここを書き換えれば起動時の書式が変わる
INPUT_LOG_FORMAT = "simple"
INPUT_LOG_STICK_CHANGE = False

# 送信の間引き幅とタイムアウトの定義は Transport が持つ。
#   上の import で名前だけをここへ引き込んである（実体は1つ）。
#   これらは「線の性質」であって「姿勢」ではないため、
#   プロトコルを替えれば値も意味も変わるので、運び方と同じ場所に置く。
# 入力調停（Input Control Arbitration）の既定。
#   "off"    … 調停しない。全部受理する（本家と同じ挙動）
#   "human"  … 人の手入力を優先。人が触った直後はスクリプトの申告を拒否
#   "script" … スクリプトを優先。スクリプトが書いた直後は人の申告を拒否
# 既定は off とする。本家の挙動を変えないためであり、実行中に
#   人の操作を拒否するかどうかは利用者が画面から選ぶ。script を選べば
#   一時停止で操作でき、そのとき自動側の押下は退避される。
#   解放と中立の申告はどの設定でも拒否しない。拒否すると押しっぱなしが
#   残るため、安全に関わる申告は調停より優先する。
ARBITRATION_MODE = "off"
# 選べる調停の名前。画面の候補もここから引く（書き並べない）。
ARBITRATION_MODES = ("off", "human", "script")
# 横取りが続く時間(秒)。優先された側が書いてからこの間、反対側を拒否する。
# NanoKVM の既定(2秒)に合わせた。短すぎると取り合いが収まらず、
# 長すぎると「操作できない時間」が体感で分かるほど伸びる。
ARBITRATION_COOLDOWN = 2.0
# 人の手による操作とみなす申告者。これ以外（script / None）は自動側とみなす。
HUMAN_SOURCES = ("mouse", "keyboard", "gui")
# 拒否の通知を間引く間隔(秒)。連打や毎フレームの申告で通知が溢れるのを防ぐ。
REJECT_NOTIFY_INTERVAL = 1.0


def list_arbitration_modes() -> tuple:
    """選べる調停の名前を返す（画面の候補づくり用）。"""
    return ARBITRATION_MODES


def resolve_arbitration_mode(name: Any, logger: Any = None) -> str:
    """設定ファイル由来の名前を、実際に使える名前へ解決する。

    知らない名前は既定（off）へ落とし、理由を残す。設定ファイルは人が
    手で書き換えられるため、綴りの誤りで黙って別の挙動になることを避け
    る。Transport の resolve_transport_name と同じ作法である。
    """
    text = str(name).strip().lower() if name is not None else ""
    if text in ARBITRATION_MODES:
        return text
    message = (
        "入力調停の設定 "
        + repr(name)
        + " は知らない名前です。"
        + ARBITRATION_MODE
        + " として扱います"
        + "（選べるのは "
        + " / ".join(ARBITRATION_MODES)
        + "）。"
    )
    if logger is not None:
        logger.warning(message)
    else:
        print(message)
    return ARBITRATION_MODE


class Sender:
    # 内容が変わらないので実体は1つでよい（旧コードは生成のたびに作り直していた）
    Buttons = InputLog.BUTTON_NAMES
    Hat = tuple(name.split(".")[1] for name in InputLog.HAT_NAMES)

    def __init__(
        self,
        is_show_serial: Any,
        if_print: bool = True,
        input_log_emit: Callable[[str], None] | None = None,
        transport: Transport.Transport | None = None,
    ) -> None:
        # 運び方（Transport）を1つ持つ。既定は従来と同じテキスト
        #   シリアルなので、何も指定しなければ挙動は不変。
        #   引数で差し替えられるようにしてあるのは、別のプロトコルを
        #   試すためと、検証で偽の線を差し込むため。
        self.transport = (
            transport if transport is not None else Transport.TextSerialTransport()
        )
        self.is_show_serial = is_show_serial

        self._logger = getLogger(__name__)
        self._logger.addHandler(NullHandler())
        self._logger.setLevel(DEBUG)
        self._logger.propagate = True
        # Transport にも同じログ口を使わせる（出どころを1つに保つ）
        self.transport._logger = self._logger

        self.L_holding = False
        self._L_holding = None
        self.R_holding = False
        self._R_holding = None
        self.is_print = if_print
        self.time_bef = time.perf_counter()
        self.time_aft = time.perf_counter()
        # 応答遅延の計測用。既定は切。入れたときだけ記録するので、
        #   通常利用では計測処理に触らず挙動が変わらない。
        self._perf_recording = False
        self._perf_log = deque(maxlen=10000)
        self._perf_event_time = None
        self._perf_event_source = None
        self._perf_write_start = None
        # 入力ログ。writeRow が送った1行を InputLogger へ流し、
        # 前回との差分から press / release と押下時間を組み立てて表示する。
        self.input_logger = InputLog.InputLogger(
            emit=input_log_emit,
            template=INPUT_LOG_FORMAT,
            log_stick_change=INPUT_LOG_STICK_CHANGE,
        )
        self.input_logger.set_enabled(if_print)
        # 送信行を入力ログへ運ぶ経路は、Transport の聞き手として
        #   繋ぐ。繋がったかを必ず見る。バイナリで運ぶ実装は送信行
        #   （文字列）を作らないため繋がらず、黙っていると「1行も出ない
        #   のに動いていると思う」形で静かに壊れる。
        self._input_log_linked = bool(
            self.transport.add_listener(self.input_logger.feed)
        )
        if not self._input_log_linked:
            msg = (
                "入力ログは、この通信方式（"
                + str(getattr(self.transport, "name", "?"))
                + "）では出せません（送信行を作らないため）。"
            )
            print(msg)
            self._logger.warning(msg)
        # 送信の前後で自分の帳簿を付けるための手。計測（time_bef /
        #   time_aft）と直前の行（before）は Sender の持ち物なので、
        #   Transport には持たせずここで受け取る。
        self.transport.set_hooks(self._onWriteBegin, self._onWriteEnd)
        # 姿勢と調停を触るあいだ守る錠。送信の入口は4経路あり、うち3つは
        # 別のスレッドから来る（GUI / コマンド / キーボード）。RLock なのは
        # holdHat が applyHat を呼ぶなど、同じスレッドで錠を取り直すため。
        self._lock = threading.RLock()
        # Pico live-state 能力がある線だけ worker を立てる。
        if self._liveCapable():
            self.startLiveWorker()

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

    # =====================================================================
    # 線そのものは Transport が受け持つ
    # =====================================================================
    # ここから下の openSerial / closeSerial / isOpened / writeRow /
    #   flushPending は、名前も引数も戻り値も従来どおり。中身だけを
    #   Transport への委譲に替えた。呼び出し側は直さなくてよい。
    #
    # なぜ「名前を残して中身だけ移す」のか:
    #   これらは既存コードが直接呼んでいる公開 API で、呼び出し箇所が多い。
    #   名前を替えると移植と改名が混ざり、問題が出たときに切り分けられない。

    def _liveCapable(self, transport: Any = None) -> bool:
        """Pico live-state worker が必要かを判断する唯一の場所。"""
        target = self.transport if transport is None else transport
        return (
            getattr(target, "capability", Transport.LEGACY_ROW)
            == Transport.PICO_LIVE_STATE
        )

    def isLiveCapable(self) -> bool:
        """live 経路（Pico の S 行）を使っているか。

        Keys.end の分岐用。live には 'end' という行が無く、送ると
        ERR になる。公開メソッドにしてあるのは、Keys が Transport の
        定数を知らなくて済むようにするため（循環 import 回避）。
        """
        return bool(self._liveCapable())

    def setTransport(self, transport: Transport.Transport) -> bool:
        """通信方式を切り替える。

        live worker を停止してから切り替え、新しい通信方式が live 対応で
        あれば 1 本だけ起動する。戻り値は入力ログを接続できたかどうかで
        あり、worker の停止に失敗した場合も False を返す。
        """
        with self._lock:
            if not self.stopLiveWorker():
                self._logger.error("PicoLiveWorker did not stop; transport unchanged")
                return False
            try:
                self.transport.remove_listener(self.input_logger.feed)
                self.transport.close()
            except Exception:
                self._logger.error(
                    f"Failed to close previous transport: {traceback.format_exc()}"
                )
            self.transport = transport
            self.transport._logger = self._logger
            self.transport.set_hooks(self._onWriteBegin, self._onWriteEnd)
            self._input_log_linked = bool(
                self.transport.add_listener(self.input_logger.feed)
            )
            if not self._input_log_linked:
                msg = (
                    "入力ログは、この通信方式（"
                    + str(getattr(self.transport, "name", "?"))
                    + "）では出せません（送信行を作らないため）。"
                )
                print(msg)
                self._logger.warning(msg)
            if self._liveCapable():
                self.startLiveWorker(self.transport)
            # 運び先が変わるので、キュー対応の記憶は捨てる。
            self._forgetQueueSupport()
            return self._input_log_linked

    def getTransportName(self) -> str:
        """いま使っている運び方の名前（設定画面・検算用）。"""
        return str(getattr(self.transport, "name", "?"))

    def isInputLogLinked(self) -> bool:
        """入力ログが送信行を受け取れているか（静かに壊れないための確認）。"""
        return bool(getattr(self, "_input_log_linked", False))

    @property
    def ser(self) -> Any:
        """生のシリアルオブジェクト。後方互換のための覗き窓。

        従来 Sender は self.ser を直に持っていた。実体は
          Transport 側へ移ったが、外から self.ser を見ているコードが
          あるため、同じ名前で覗けるようにしておく。
        """
        return getattr(self.transport, "ser", None)

    @ser.setter
    def ser(self, value: Any) -> None:
        # 差し込みたい場合（テスト・偽の線）も従来どおり書けるようにする
        setattr(self.transport, "ser", value)

    @property
    def before(self) -> Any:
        """最後に書き出した行。後方互換のための覗き窓（実体は Transport）。"""
        return getattr(self.transport, "_before", None)

    @property
    def _send_interval(self) -> float:
        """いま使っている間引き幅。後方互換のための覗き窓。"""
        return float(getattr(self.transport, "_send_interval", MIN_SEND_INTERVAL))

    def _calcSendInterval(self, baudrate: int) -> float:
        """通信速度から送信の最小間隔を決める。実体は Transport 側。

        名前を残しているのは、検証コード（verify_all.py など）が
          この名前で呼んでいる可能性があるため。
        """
        calc = getattr(self.transport, "calc_send_interval", None)
        if callable(calc):
            return calc(baudrate)
        return Transport.TextSerialTransport.calc_send_interval(baudrate)

    def _onWriteBegin(self, row: str, show: bool = True) -> None:
        """Transport が実際に書き出す直前に呼ばれる。計測の起点。

        引数 show の意味は「画面へ表示してよいか」であり、
          計測するかどうかではない。Transport は measure_perf を
          そのまま渡してくる。以前は measure_perf が偽だとフック自体が
          呼ばれず、間引きで保留された行（最も遅れる行）が計測から
          抜けていた。
        time_bef は従来どおり残す。外部が読んでいる可能性を否定
          できないため。
        """
        self.time_bef = time.perf_counter()
        if self._perf_recording:
            self._perf_write_start = self.time_bef

    def _onWriteEnd(self, row: str, show: bool = True) -> None:
        """Transport が書き出した直後に呼ばれる。計測の終点。

        表示（show）と計測を分けている。計測は常に行い、
          print だけを show で制御する。従来 measure_perf=False の
          行は print もされなかったので、表示の挙動は変わらない。
        """
        self.time_aft = time.perf_counter()
        if self._perf_recording:
            self._recordPerf(row)
        # Show sending serial datas
        if show and self._should_show_serial():
            print(row)

    # ------------------------------------------------------------------
    # 応答遅延の計測
    # ------------------------------------------------------------------
    # 何のためにあるか:
    #   送信の仕組みを変える前後で、応答の速さを比べるための物差し。
    #   測る対象は event_time（入口で申告を受けた時刻）から
    #     write_start_time（実際に線へ書き始めた時刻）までの間隔。
    #
    # なぜ既定で切ってあるか:
    #   測る行為そのものが遅延を生む。常時記録すると、測った値が
    #     「測っていないときの速さ」からずれる。必要なときだけ入れる。
    #   切ってあるあいだは計測処理を行わないので、
    #     通常利用の挙動は変わらない。
    #
    # event_time をどこで採るか:
    #   申告の入口（pressButtons / releaseButtons / setHat / setStick /
    #     holdHat / releaseHat）で採る。『いつ押されたか』であって
    #     『いつ送ったか』ではない。
    #   送信後に採った時刻は「送信後」の時刻なので、起点には使えない。

    def setPerfRecording(self, enabled: bool, capacity: int = 10000) -> None:
        """応答遅延の記録を入／切する。既定は切。"""
        with self._lock:
            if enabled and not self._perf_recording:
                self._perf_log = deque(maxlen=int(capacity))
            self._perf_recording = bool(enabled)

    def isPerfRecording(self) -> bool:
        """いま記録しているか。"""
        return bool(self._perf_recording)

    def markEvent(self, source: str | None = None) -> None:
        """入口で申告を受けた時刻を記録する。

        申告系の入口から必ず呼ぶ。ここを呼び忘れた経路は、
          その行の遅延が測れない（＝統計から抜ける）。
        記録が切のときは何もしない。
        """
        if not self._perf_recording:
            return
        with self._lock:
            # まだ線へ出ていない申告が続いたときは、最初の時刻を残す。
            #   上書きすると『待たされた時間』が消え、短く見える。
            if self._perf_event_time is None:
                self._perf_event_time = time.perf_counter()
                self._perf_event_source = source

    def _recordPerf(self, row: str) -> None:
        """1行ぶんの計測を記録へ積む。_onWriteEnd から呼ばれる。

        1件だけ持つ形（time_bef / time_aft）では中央値も95パーセン
          タイルも出せない。上書きされて消えるため、記録は溜める。
        event_time が無い行（内部由来の送信など）は None のまま積む。
          捨てないのは、送信本数を数えるときに欠けると合わなくなるため。
        """
        ev = self._perf_event_time
        start = self._perf_write_start
        end = self.time_aft
        self._perf_log.append(
            {
                "event": ev,
                "write_start": start,
                "write_end": end,
                "source": self._perf_event_source,
                "row": row,
                # 遅延そのものはここで引いておく。後から引くと、
                #   event が None の行を数え間違える。
                "latency": (start - ev)
                if (ev is not None and start is not None)
                else None,
            }
        )
        # 1行出したら次の申告のために空ける（次の event_time を採れる）
        self._perf_event_time = None
        self._perf_event_source = None

    def getPerfLog(self) -> list[dict[str, Any]]:
        """記録の写しを返す。読み取り専用。

        ここでは統計を計算しない。中央値の求め方や外れ値の扱いは
          測る側の判断なので、Sender は「起きたこと」だけを渡す。
        """
        with self._lock:
            return list(self._perf_log)

    def clearPerfLog(self) -> None:
        """記録を捨てる（測り直すとき用）。"""
        with self._lock:
            self._perf_log.clear()
            self._perf_event_time = None
            self._perf_event_source = None

    def openSerial(
        self, portNum: int, portName: str = "", baudrate: int = 9600
    ) -> bool:
        """線を開く。中身は Transport が行う。

        開けた場合は live 経路に限り worker を起動する。切断で worker を
        止めているため、ここで起こさないと開き直しても状態が送出されない。
        判定は他の呼び出し元と同じく _liveCapable に揃える。既に動いて
        いれば startLiveWorker が二重には起こさない。
        """
        opened = self.transport.open(portNum, portName, baudrate)
        if opened and self._liveCapable():
            self.startLiveWorker()
        if opened:
            # 相手が変わるので、キュー対応の記憶は捨てる。
            self._forgetQueueSupport()
        return opened

    def closeSerial(self) -> None:
        """回線を安全に閉じる。

        停止は順序が全てである。

            1  新規の live 入力の受付を停止する
            2  未送信の通常状態を破棄する
            3  中立を優先送信する
            4  送出の完了を期限つきで待つ
            5  worker へ停止を要求する
            6  worker を起床させる
            7  期限つきで停止を待つ
            8  入力ログを初期化する
            9  Transport を閉じる

        中立の送出は worker が生きているうちに行う。worker を止めてから
        mailbox へ入れても誰も送出しないため、押下が残る。

        ステップ 1 から 8 は try の内側に置き、ステップ 9 は finally で必ず実行
        する。途中で失敗しても回線を開いたままにしない。

        錠（_lock）は状態の変更だけに使う。送出の完了待ち・worker の
        join・入力ログの初期化まで錠の中で行うと、その間すべての申告と
        読み取りが止まり、GUI が固まったように見える。順序は
        変えず、待つ部分だけ外へ出す。
        """
        self._logger.debug("Closing the serial communication")
        live = self._liveCapable()
        try:
            with self._lock:
                if live:
                    # 1: これ以降の状態変化を mailbox へ入れない。
                    self._live_closing = True
                    # 2: 中立より後に古い状態が出ないようにする。
                    self.discardLive()
                    # 3: worker が生きているうちに中立を送る。
                    self.releaseAll()
                    self.putLive(self.snapshot(), priority=True)
            if live:
                # 4: 送出の完了を待つ。期限を過ぎたら次へ進む。
                if not self.waitLiveDrained(self.CLOSE_DRAIN_S):
                    self._logger.error("Neutral state was not sent before closing")
                # 5 から 7: 停止を要求し、起床させ、期限つきで待つ。
                if not self.stopLiveWorker(self.CLOSE_JOIN_S):
                    self._logger.error("PicoLiveWorker did not stop; closing anyway")
                # 8: 入力ログを初期化する。
                self.input_logger.reset()
        finally:
            # 9: どの手順が失敗しても回線は必ず閉じる。手順 1〜3 を
            # try の外に置くと、そこで例外が出たときに閉じず、かつ
            # 閉じ中の印が残って以後すべての live 入力を断る。
            with self._lock:
                self._live_closing = False
            self.transport.close()

    def isOpened(self) -> bool:
        self._logger.debug("Checking if serial communication is open")
        return self.transport.is_open()

    def _should_show_serial(self) -> bool:
        """is_show_serial は tk.BooleanVar でも素の bool でも受け付ける。

        worker スレッドからも呼ばれる。Tk の変数を別スレッドから読むと
        例外になることがあるため、読めないときは直前の値を保つ。
        """
        try:
            getter = getattr(self.is_show_serial, "get", None)
            value = bool(getter()) if callable(getter) else bool(self.is_show_serial)
        except Exception:
            # 読めないときは直前の値を保つ。表示の有無で送信は変えない。
            return bool(getattr(self, "_show_serial_last", True))
        self._show_serial_last = value
        return value

    def writeRow(
        self, row: str, is_show: bool = False, measure_perf: bool = True
    ) -> None:
        """1行送信する。中身は Transport が行う。

        間引き（coalescing）の条件も、送れなかったときの扱いも従来と
          同じ。移しただけで判断は変えていない。
        is_show は後方互換のため残置。入力ログは常に input_logger が行う。
        """
        self.transport.send_row(row, measure_perf=measure_perf)

    def _is_coalescable(self, row: str) -> bool:
        """間引いてよい行か。実体は Transport 側（名前だけ残す）。"""
        judge = getattr(self.transport, "_coalescable", None)
        return bool(judge(row)) if callable(judge) else False

    def flushPending(self) -> None:
        """間引きで保留した行があれば送る。

        「最後に少しだけ倒した」状態が送られずに残ると、操作が中途半端
        なまま止まる。GUI が離した時や切断時など、区切りで呼ぶ。
        """
        self.transport.flush_pending()

    def _write(self, row: str, measure_perf: bool = True) -> None:
        """実際に書き出す。実体は Transport 側（名前だけ残す）。

        互換のために戻した名前であり、リポジトリ外の利用者コードが
          触っている可能性は否定できない。
          消す判断は、消してよい根拠が取れてからにする。
        """
        writer = getattr(self.transport, "_write", None)
        if callable(writer):
            writer(row, measure_perf=measure_perf)
            return
        self.transport.send_row(row, measure_perf=measure_perf)

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

    # =====================================================================
    # 姿勢（Posture）を Sender が1つ持つ
    # =====================================================================
    # なぜここへ置くか:
    #   押下状態が系統ごとに分かれていると、どの系統も「自分の状態」しか
    #   知らないため、送る行が他系統の押下を消す。押しながら別系統で触ると
    #   ボタンが落ちたり、左右スティックを同時に倒せなかったりする。
    #
    #   Sender は最初から一意で、全経路が既にここへ集まっている。送信状態も
    #   既に持っているので、姿勢もここへ置けば「送信に関することは Sender」
    #   で一貫する。同じ錠で守れるため、錠も増えない。
    #
    # _buildRow について:
    #   Keys.SendFormat.convert2str と同じ結果を返す実装をここへ「並べて」
    #   置く。既存の動作を変えないため、一時的に同じ処理が2箇所に並ぶ。

    # 姿勢の初期値。Keys.py の CENTER / Hat.CENTER と同じ値を使う。
    # Keys を import しないのは循環を避けるため（Keys が Sender を使う）。
    POSTURE_CENTER = 128
    POSTURE_HAT_CENTER = 8

    def _initPosture(self) -> None:
        """姿勢を中立へ戻す。releaseAll と遅延初期化から呼ぶ。"""
        c = self.POSTURE_CENTER
        self._posture = {
            "btn": 0,
            "hat": self.POSTURE_HAT_CENTER,
            "lx": c,
            "ly": c,
            "rx": c,
            "ry": c,
        }
        # 変化印。convert2str と同じ意味で、読み取ると False へ戻る
        # （SendFormat の副作用つき getter と挙動を揃える）。
        self._L_stick_changed = False
        self._R_stick_changed = False
        # Hat は「値」であってビットではない。押していない間も直前の値を
        # 覚えておく必要がある（SendFormat.Hat_pos と同じ）。
        self._hat_pos = self.POSTURE_HAT_CENTER

    def _ensurePosture(self) -> None:
        """姿勢がまだ無ければ作る。

        遅延初期化。apply* の側で面倒を見る。
        """
        if not hasattr(self, "_posture"):
            self._initPosture()

    def applyButtons(
        self, press: Any = None, release: Any = None, source: Any = None
    ) -> None:
        """ボタンの押下と解放を差分で適用し、変化があれば live に渡す。

        解放を含む申告は優先送信とする。押下が次のスロットまで
        遅れても操作が一瞬遅れるだけだが、解放が遅れると押しっぱなしに
        なるため、破棄も待機もさせない。

        押下は申告した所有者のビット列へ記録し、実際に送る値は
        全所有者の OR とする。同じボタンを 2 者が押している場合、片方が
        離してももう片方が離すまで落とさないためである。所有者を指定
        しない申告は既定の所有者へ入るため、1 人しか居ない従来の使い方
        では合成しても同じ値になる。
        """
        snap = None
        owner = self._ownerKey(source)
        with self._lock:
            self._ensurePosture()
            self._ensureOwners()
            bits = int(self._owner_btn.get(owner, 0))
            for btn in press or ():
                bits |= int(btn)
            for btn in release or ():
                bits &= ~int(btn)
            if bits:
                self._owner_btn[owner] = bits
            else:
                self._owner_btn.pop(owner, None)
            if self._applyComposed():
                self._bumpRevision()
                if self._liveCapable():
                    snap = self.snapshot()
        if snap is not None:
            self.putLive(snap, priority=bool(release))

    def applyHat(self, hat: Any = None) -> None:
        """Hat の向きを差し替え、変化があれば live に渡す。

        中立へ戻す場合は優先送信とする。方向が残ると意図しない
        移動が続くため、解放と同じ扱いにする。
        """
        snap = None
        priority = False
        with self._lock:
            self._ensurePosture()
            self._ensureHatHold()
            if hat is None:
                held = self._heldHatValue()
                self._hat_pos = held if held is not None else self.POSTURE_HAT_CENTER
            else:
                self._hat_pos = int(hat)
            if self._posture["hat"] != self._hat_pos:
                self._posture["hat"] = self._hat_pos
                self._bumpRevision()
                priority = self._hat_pos == self.POSTURE_HAT_CENTER
                if self._liveCapable():
                    snap = self.snapshot()
        if snap is not None:
            self.putLive(snap, priority=priority)

    # 既定の所有者。source を渡さない経路はここへ集める。
    #   所有者が 1 人だけのときは、合成しても現行と同じ値になる。
    DEFAULT_OWNER = "default"

    def _ensureOwners(self) -> None:
        """所有者ごとの申告を保持する入れ物を作る（遅延初期化）。

        ボタンは owner ごとのビット列を持ち、送る値は全 owner の OR とする。
        同じボタンを 2 者が押している場合、両方が離すまで落とさないため
        である。Hat とスティックは値であり OR できないため、owner ごとに
        (値, revision) を持ち、revision が最大のものを採る。
        """
        if not hasattr(self, "_owner_btn"):
            self._owner_btn: dict[str, int] = {}
        if not hasattr(self, "_owner_hat"):
            self._owner_hat: dict[str, Any] = {}
        if not hasattr(self, "_owner_stick"):
            self._owner_stick: dict[str, Any] = {}

    def _ownerKey(self, source: Any = None) -> str:
        """所有者の名札を返す。指定が無ければ既定の所有者とする。

        名札は source 文字列のみとし、実行世代までは持たない。基本は
        1 マイコンに 1 コマンドであり、複数機を操作する場合は本体を
        複数起動するため、同一プロセスで複数のコマンドが同時に姿勢を
        書く場面が無いからである。
        """
        return self.DEFAULT_OWNER if source is None else str(source)

    def _composeButtons(self) -> int:
        """全所有者のビット列を OR して、実際に送る値を返す。"""
        self._ensureOwners()
        merged = 0
        for bits in self._owner_btn.values():
            merged |= int(bits)
        return merged

    def _composeLatest(self, table: dict[str, Any]) -> Any | None:
        """revision が最大の申告の値を返す。申告が無ければ None。

        辞書の並び順は新しさを意味しないため、順序の根拠を revision に
        一本化する。
        """
        latest = None
        latest_rev = -1
        for value, rev in table.values():
            if int(rev) >= latest_rev:
                latest_rev = int(rev)
                latest = value
        return latest

    def suspendOwner(self, source: Any = None) -> bool:
        """所有者 1 人分の申告を退避し、姿勢から外す。

        退避したものがあれば True を返す。Pause で人へ操作を渡す前に、
        script の押下を姿勢から消しておく。消しておかないと、人が同じ
        ボタンを離したときに script の押下まで巻き込んで落ちる。

        既に退避してある場合は上書きしない。Pause 中にもう一度 Pause が
        来ても、人が触った後の状態を script の姿勢として復元しないため
        である。
        """
        owner = self._ownerKey(source)
        snap = None
        with self._lock:
            self._ensurePosture()
            self._ensureOwners()
            self._ensureHatHold()
            self._ensureSuspended()
            if owner in self._owner_saved:
                return False
            saved = {
                "btn": self._owner_btn.pop(owner, None),
                "hat": self._owner_hat.pop(owner, None),
                "stick": self._owner_stick.pop(owner, None),
            }
            if all(v is None for v in saved.values()):
                # 押していないものは退避しない。姿勢も変わらない。
                return False
            self._owner_saved[owner] = saved
            self._hat_held.pop(str(source), None)
            if self._applyComposed():
                self._bumpRevision()
                if self._liveCapable():
                    snap = self.snapshot()
        if snap is not None:
            # 押していた状態を外す方向なので、解放と同じ優先送信とする。
            self.putLive(snap, priority=True)
        return True

    def restoreOwner(self, source: Any = None) -> bool:
        """退避した申告を戻す。

        戻したものがあれば True を返す。人が触った分は人の所有分として
        別に持っているため、この復帰では手を触れない。
        """
        owner = self._ownerKey(source)
        snap = None
        with self._lock:
            self._ensurePosture()
            self._ensureOwners()
            self._ensureHatHold()
            self._ensureSuspended()
            saved = self._owner_saved.pop(owner, None)
            if saved is None:
                return False
            if saved["btn"] is not None:
                self._owner_btn[owner] = saved["btn"]
            if saved["hat"] is not None:
                # 退避したときの値を、いまの revision で入れ直す。
                value = saved["hat"][0]
                self._owner_hat[owner] = (value, self.getRevision() + 1)
                self._hat_held[str(source)] = int(value)
            if saved["stick"] is not None:
                # Hat と同じく、いまの revision で入れ直す。古い revision
                # のまま戻すと、退避中に来た他の申告に負け続ける。
                claim = saved["stick"][0]
                self._owner_stick[owner] = (claim, self.getRevision() + 1)
            if self._applyComposed():
                self._bumpRevision()
                if self._liveCapable():
                    snap = self.snapshot()
        if snap is not None:
            self.putLive(snap, priority=True)
        return True

    def isOwnerSuspended(self, source: Any = None) -> bool:
        """その所有者の申告が退避中かを返す（読むだけ）。"""
        with self._lock:
            self._ensureSuspended()
            return self._ownerKey(source) in self._owner_saved

    def getSuspended(self) -> dict[str, Any]:
        """退避中の申告の写しを返す（検算用・副作用なし）。"""
        with self._lock:
            self._ensureSuspended()
            return {k: dict(v) for k, v in self._owner_saved.items()}

    def _ensureSuspended(self) -> None:
        """退避の入れ物を作る（遅延初期化）。"""
        if not hasattr(self, "_owner_saved"):
            self._owner_saved: dict[str, Any] = {}

    def dropOwner(self, source: Any = None) -> bool:
        """所有者 1 人分の申告だけを取り下げる。

        取り下げたものがあれば True を返す。他の所有者の押下は残す。
        全員分を落とすのは releaseAll であり、こちらは Stop の意味を持つ。
        """
        owner = self._ownerKey(source)
        snap = None
        with self._lock:
            self._ensurePosture()
            self._ensureOwners()
            had = self._owner_btn.pop(owner, None) is not None
            had = (self._owner_hat.pop(owner, None) is not None) or had
            had = (self._owner_stick.pop(owner, None) is not None) or had
            if had and self._applyComposed():
                self._bumpRevision()
                if self._liveCapable():
                    snap = self.snapshot()
        if snap is not None:
            self.putLive(snap, priority=True)
        return had

    def getOwners(self) -> dict[str, Any]:
        """所有者ごとの申告の写しを返す（検算用・副作用なし）。"""
        with self._lock:
            self._ensureOwners()
            return {
                "btn": dict(self._owner_btn),
                "hat": dict(self._owner_hat),
                "stick": dict(self._owner_stick),
            }

    def _composeStickSide(self, side: str) -> Any | None:
        """その側のスティック申告のうち revision が最大の座標を返す。

        申告が無ければ None。呼び出し側は self._lock を保持していること。
        所有者ごとの申告は ({"L": (x, y), "R": (x, y)}, revision) の形で
        _owner_stick に入る。Hat と同じく revision 順で選ぶ。
        """
        self._ensureOwners()
        latest = None
        latest_rev = -1
        for claim, rev in self._owner_stick.values():
            if side in claim and int(rev) >= latest_rev:
                latest_rev = int(rev)
                latest = claim[side]
        return latest

    def _applyComposed(self) -> bool:
        """合成した結果を姿勢へ書き、変化があれば True を返す。

        呼び出し側は self._lock を保持していること。所有者が 1 人だけの
        ときは合成しても同じ値になるため、送信行は現行と変わらない。
        スティックも合成する。記録だけして合成しないと、suspend で
        申告を外しても姿勢に値が残り、Pause で倒しが漏れる。
        """
        self._ensurePosture()
        self._ensureOwners()
        changed = False
        merged = self._composeButtons()
        if int(self._posture["btn"]) != merged:
            self._posture["btn"] = merged
            changed = True
        hat = self._composeLatest(self._owner_hat)
        if hat is None:
            hat = self.POSTURE_HAT_CENTER
        if int(self._posture["hat"]) != int(hat):
            self._posture["hat"] = int(hat)
            self._hat_pos = int(hat)
            changed = True
        for side, kx, ky in (("L", "lx", "ly"), ("R", "rx", "ry")):
            target = self._composeStickSide(side)
            tx, ty = (
                target
                if target is not None
                else (self.POSTURE_CENTER, self.POSTURE_CENTER)
            )
            if int(self._posture[kx]) != int(tx) or int(self._posture[ky]) != int(ty):
                self._posture[kx] = int(tx)
                self._posture[ky] = int(ty)
                if side == "L":
                    self._L_stick_changed = True
                else:
                    self._R_stick_changed = True
                changed = True
        return changed

    def _ensureHatHold(self) -> None:
        """Hat の押しっぱなし記録がまだ無ければ作る（遅延初期化）。"""
        if not hasattr(self, "_hat_held"):
            # 申告者(source) -> Hat の値。誰が押しているかまで持つのは、
            # 系統ごとに離すタイミングが違うため。1つの値では上書きになる。
            self._hat_held: dict[str, int] = {}

    def _heldHatValue(self) -> int | None:
        """押しっぱなしにされている Hat の値。無ければ None。

        複数の系統が別々の向きを押している場合は、revision が最大の申告
        を採る。Hat は 1 つの値しか持てないため、どれかを選ぶしかない。
        辞書順は新しさを意味しないため、順序の根拠を revision に一本化した。
        """
        self._ensureHatHold()
        self._ensureOwners()
        return self._composeLatest(self._owner_hat)

    def holdHat(self, hat: Any, source: str | None = None) -> bool:
        """十字キーを押しっぱなしにすることを申告する。

        これを申告しておくと、他の系統から中立へ戻す申告が来ても
          向きが保たれる。
        解除は releaseHat。同じ source が何度呼んでも最後の値になる。

        申告は (値, revision) で記録する。複数の系統が別々の向きを
        押している場合、revision が最大のものが採られる。
        """
        if not self._accept(source):
            return False
        self.markEvent(source)  # 入口の時刻（受理した申告だけ測る）
        with self._lock:
            self._ensureHatHold()
            self._ensureOwners()
            owner = self._ownerKey(source)
            self._hat_held[str(source)] = int(hat)
            self._owner_hat[owner] = (int(hat), self.getRevision() + 1)
            self.applyHat(int(hat))
        return True

    def releaseHat(self, source: str | None = None) -> bool:
        """十字キーの押しっぱなしをやめる。

        自分の分だけ取り下げる。他の系統がまだ押していれば、その向きへ戻る。
        """
        if not self._accept(source, releasing=True):
            return False
        self.markEvent(source)  # 入口の時刻（受理した申告だけ測る）
        with self._lock:
            self._ensureHatHold()
            self._ensureOwners()
            self._hat_held.pop(str(source), None)
            self._owner_hat.pop(self._ownerKey(source), None)
            self.applyHat(None)
        return True

    def getHatHold(self) -> dict[str, int]:
        """押しっぱなしの一覧（確認・デバッグ用）。"""
        with self._lock:
            self._ensureHatHold()
            return dict(self._hat_held)

    def applyStick(
        self,
        stick: str,
        x: int | None = None,
        y: int | None = None,
        source: Any = None,
    ) -> None:
        """スティック座標を差分で適用し、変化があれば live に渡す。

        中立へ戻す場合のみ優先送信とする。途中の座標は次の値で
        置き換えてよいが、中立は倒したままの状態を解くため待たせない。

        申告は所有者ごとに記録し、送る値は _applyComposed の合成結果と
        する。記録しないと suspend / restore / drop がスティックを拾えず、
        Pause で倒しが漏れる。1 人だけの従来の使い方では合成しても同じ
        値になるため、送信内容は変わらない。
        """
        snap = None
        priority = False
        owner = self._ownerKey(source)
        with self._lock:
            self._ensurePosture()
            self._ensureOwners()
            side = (
                "R"
                if str(stick).upper().endswith("R")
                or str(stick).upper().endswith("RIGHT")
                else "L"
            )
            kx = side.lower() + "x"
            ky = side.lower() + "y"
            # 自分の前回申告を土台に、渡された軸だけ上書きする。申告が
            # 初めての側は現在の姿勢を土台にする（差分の意味を保つ）。
            prev_claim, _ = self._owner_stick.get(owner, ({}, -1))
            claim = dict(prev_claim)
            for name, ax, ay in (("L", "lx", "ly"), ("R", "rx", "ry")):
                if name not in claim:
                    claim[name] = (int(self._posture[ax]), int(self._posture[ay]))
            cx, cy = claim[side]
            if x is not None:
                cx = int(x)
            if y is not None:
                cy = int(y)
            claim[side] = (cx, cy)
            self._owner_stick[owner] = (claim, self.getRevision() + 1)
            if self._applyComposed():
                self._bumpRevision()
                if self._liveCapable():
                    snap = self.snapshot()
            if snap is not None:
                priority = (
                    int(self._posture[kx]) == self.POSTURE_CENTER
                    and int(self._posture[ky]) == self.POSTURE_CENTER
                )
        if snap is not None:
            self.putLive(snap, priority=priority)

    def releaseAll(self) -> None:
        """全項目を中立に戻し、変化があれば live に渡す。

        Stop、Neutral、Release All の経路であり、常に優先送信と
        する。停止後に押下が残ってはならない。

        所有者ごとの申告もすべて取り下げる。これは Stop の意味を
        持つ経路であり、誰の押下も残さない。1 人分だけ取り下げるのは
        dropOwner である。
        """
        snap = None
        with self._lock:
            before = dict(self._posture) if hasattr(self, "_posture") else None
            self._initPosture()
            self._ensureHatHold()
            self._ensureOwners()
            self._hat_held.clear()
            self._owner_btn.clear()
            self._owner_hat.clear()
            self._owner_stick.clear()
            self._ensureSuspended()
            self._owner_saved.clear()
            # Stop は渡した状態も解く。誰の押下も残さない
            #   経路であり、渡したままだと次の実行で調停が効かない。
            self._ensureArbitration()
            self._arb_handed_over = False
            # 自動側の主導権も手放す。Stop は自動側が終わる合図であり、
            #   cooldown は『最後に自動側が申告してから何秒』で測るため、
            #   履歴を残すと停止後も人の操作が拒否される。
            self._arb_last_auto = None
            self._L_stick_changed = True
            self._R_stick_changed = True
            if before != self._posture:
                self._bumpRevision()
                if self._liveCapable():
                    snap = self.snapshot()
        if snap is not None:
            self.putLive(snap, priority=True)

    def getPosture(self) -> dict[str, int]:
        """現在の姿勢の写しを返す（確認・デバッグ用）。"""
        with self._lock:
            self._ensurePosture()
            return dict(self._posture)

    def _buildRow(self) -> str:
        """現在の姿勢から送信行を組む。

        Keys.SendFormat.convert2str と1文字も違わない結果を返すこと。
        書式は "0x00XX hat [lx ly] [rx ry]"（変化した側だけが付く可変長）。
          - btn は2ビット左シフトし、下位2bit へ L/R の変化印を入れる
            （0x2 = L が変化 / 0x1 = R が変化）
          - 座標は16進、Hat は10進
          - 先頭は format(x, "#06x") で必ず "0x" + 4桁
        この "0x" は「先頭を必ず 0 にする」ための仕掛けでもある。
          外すと ParseLine の座標行判定を通らない行が
          できるため、短くしてはいけない。
        convert2str と同じく、読み取ると変化印は False へ戻る。
        """
        with self._lock:
            self._ensurePosture()
            p = self._posture
            space = " "
            send_btn = int(p["btn"]) << 2
            str_L = ""
            str_R = ""
            if self._L_stick_changed:
                send_btn |= 0x2
                str_L = format(p["lx"], "x") + space + format(p["ly"], "x")
            if self._R_stick_changed:
                send_btn |= 0x1
                str_R = format(p["rx"], "x") + space + format(p["ry"], "x")
            row = (
                format(send_btn, "#06x")
                + space
                + str(int(p["hat"]))
                + (space + str_L if self._L_stick_changed else "")
                + (space + str_R if self._R_stick_changed else "")
            )
            # 読み取ったので印を下ろす（convert2str と同じ副作用）
            self._L_stick_changed = False
            self._R_stick_changed = False
            return row

    def verifyAgainstSendFormat(self, sf: Any) -> bool:
        """SendFormat と姿勢を突き合わせ、行が一致するか見る。

        使い方: 同じ操作を SendFormat と Sender の apply* の両方へ与えて
        から呼ぶ。True なら「姿勢の持ち方を変えても通信内容は変わらない」
        ことが確かめられたことになる。

        目視ではなく機械で比べる。

        注意: convert2str も _buildRow も「読むと変化印が下りる」
        副作用を持つ。したがって、この関数は1度しか正しく呼べない。
        2度目は両方とも印が下りた状態の行を返すので、比較の意味が変わる。
        """
        mine = self._buildRow()
        theirs = sf.convert2str()
        if mine != theirs:
            self._logger.error(
                f"_buildRow mismatch: mine={mine!r} sendformat={theirs!r}"
            )
        return mine == theirs

    # =====================================================================
    # 状態の統合と、調停の口
    # =====================================================================
    # 配線は全て Sender に集まったが、KeyPress が自分専用の姿勢を
    #   持ち続けていると、書き出すたびに「自分の内容で全体を上書き」する。
    #   ここでは KeyPress から専用状態を外し、差分だけを申告させる。
    #
    # 調停（arbitration）について:
    #   姿勢が本当に1つになると、スクリプトと人の操作が同じ姿勢を
    #   書き換える。遠隔操作の実装では、人の手入力を
    #   最優先し、自動側を横取りして一定時間拒否する「入力調停」を
    #   置くのが一般的。
    #   ただしここでは調停を入れない。理由は2つ。
    #     1. 現状も混ざっている（混ざり方が壊れていただけ）
    #     2. 調停は「所有者」という新しい状態を持ち込む。統合とは別の問題で、
    #       混ぜると成否が分からなくなる。
    #   代わりに『口』だけ用意する: source で誰の申告かを渡せるようにし、
    #     受理したかを bool で返す。ここでは常に True（全部受理）。

    # =====================================================================
    # 入力調停（Input Control Arbitration）
    # =====================================================================
    # 姿勢が本当に1つになった結果、スクリプト・キーボード・マウスが
    #   同じ姿勢を書き換えるようになった。
    #   だが「常に混ざってよいか」は別問題である。
    #
    # 方針:
    #   ・誰の申告かは source で分かる
    #   ・受理したかは bool で返す
    #   ・ここでは「受理するかどうかの判断」だけを _accept へ入れる。
    #     _accept の1箇所を書き換えれば全経路に効く設計なので、
    #     足すのはこの1箇所で済む。
    #
    # 既定は "off"（全部受理）にしてある。理由:
    #   ・それまでの挙動を1文字も変えないため（既存コマンドへの影響なし）
    #   ・調停は「うっかり触って邪魔する」を防ぐ仕組みだが、裏返すと
    #     「触ったのに効かない時間」を作る。どちらが良いかは使い方次第で、
    #     利用者が選べるべきもの。
    #
    # 拒否は黙って捨てない。_notifyReject で必ず知らせる。
    #   黙って無視すると「スクリプトが動かない」と見え、原因が分からなく
    #   なる。

    def _ensureArbitration(self) -> None:
        """調停の状態がまだ無ければ作る（遅延初期化）。

        _ensurePosture と同じ作法。__init__ を書き換えないのは、
        古い版から作られた Sender でも動くようにするため。
        """
        if not hasattr(self, "_arb_mode"):
            self._arb_mode: str = ARBITRATION_MODE
            self._arb_cooldown: float = ARBITRATION_COOLDOWN
            # 最後に書いた時刻。どちらも「まだ誰も書いていない」で始める
            self._arb_last_human: float | None = None
            self._arb_last_auto = None
            self._arb_notifier: Callable[[str], None] | None = None
            self._arb_last_notify: float = 0.0
            self._arb_suppressed: int = 0
            self._arb_rejected: dict[str, int] = {"human": 0, "auto": 0}
            # 自動側が明示的に操作を人へ渡している最中か。
            self._arb_handed_over = False

    def setArbitration(
        self, mode: str | None = None, cooldown: float | None = None
    ) -> None:
        """調停の設定を変える。設定画面や起動引数から呼ぶ。

        mode は "off" / "human" / "script"。
          off    … 調停しない（既定。従来と同じ）
          human  … 人の手入力を優先。人が触った直後はスクリプトを拒否
          script … スクリプトを優先。スクリプトが書いた直後は人を拒否
        cooldown は横取りが続く秒数。
        """
        with self._lock:
            self._ensureArbitration()
            if mode is not None:
                mode = str(mode).lower()
                if mode not in ("off", "human", "script"):
                    raise ValueError(
                        f"unknown arbitration mode: {mode!r} (off / human / script)"
                    )
                self._arb_mode = mode
                # 切り替えた瞬間に前の持ち主が残らないよう履歴を捨てる
                self._arb_last_human = None
                self._arb_last_auto = None
            if cooldown is not None:
                self._arb_cooldown = max(0.0, float(cooldown))

    def getArbitration(self) -> dict[str, Any]:
        """現在の調停の設定と実績を返す（設定画面・確認用）。"""
        with self._lock:
            self._ensureArbitration()
            return {
                "mode": self._arb_mode,
                "cooldown": self._arb_cooldown,
                "rejected_human": self._arb_rejected["human"],
                "rejected_auto": self._arb_rejected["auto"],
            }

    def setArbitrationNotifier(self, fn: Callable[[str], None] | None) -> None:
        """拒否を知らせる先を差し替える（既定は print とログ）。

        GUI ならログ欄へ、CUI なら標準出力へ、といった具合に
        呼び出し側が決められるようにする。
        """
        with self._lock:
            self._ensureArbitration()
            self._arb_notifier = fn

    @staticmethod
    def _isHumanSource(source: str | None) -> bool:
        """人の手による操作か。source が無いもの(None)は自動側とみなす。"""
        return str(source).lower() in HUMAN_SOURCES

    def _notifyReject(self, source: str | None, winner: str) -> None:
        """拒否したことを知らせる。黙って捨てない。

        ただし毎回出すと、連打や毎フレームの申告で通知が溢れて
        肝心の内容が流れる。REJECT_NOTIFY_INTERVAL の間は数だけ数え、
        次に出すときへまとめる。

        スクリプトを優先して人の操作を拒否した場合は、代わりの
        道を必ず添える。塞ぐだけで方法を書かないと、利用者からは動かな
        いとしか見えない。一時停止すれば操作でき、そのとき自動側の押下
        は退避されるため、押しっぱなしのまま渡ることもない。

        通知先の呼び出しは錠の外で行う。GUI 側の通知先が Sender へ
        呼び戻すと、錠を持ったままではデッドロックするためである。
        間引きの帳簿だけを錠の中で済ませ、文面を作ってから外へ出る。
        """
        with self._lock:
            self._ensureArbitration()
            now = time.perf_counter()
            if now - self._arb_last_notify < REJECT_NOTIFY_INTERVAL:
                self._arb_suppressed += 1
                return
            extra = (
                f"（ほか {self._arb_suppressed} 件）" if self._arb_suppressed else ""
            )
            self._arb_last_notify = now
            self._arb_suppressed = 0
            notifier = self._arb_notifier
            mode = self._arb_mode
            cooldown = self._arb_cooldown
        msg = (
            f"入力調停: {source} からの操作を受け付けませんでした{extra}。"
            f"（{winner} を優先中 / mode={mode} "
            f"cooldown={cooldown}秒）"
        )
        if self._isHumanSource(source):
            msg += "　一時停止すると操作できます。"
        if callable(notifier):
            notifier(msg)
            return
        print(msg)
        self._logger.info(msg)

    def setHandedOver(self, handed: bool) -> None:
        """自動側が操作を人へ渡している最中かを設定する。

        一時停止のように、自動側が明示的に手を引いている間は調停を
        止める。cooldown は『最後に自動側が申告してから何秒』で測る
        ため、自動側が申告を続けている限り人は永久に通らない。停止して
        いるつもりでも、keepalive や別スレッドの申告が続けば同じである。
        時間ではなく、渡したという事実で判断する。
        """
        with self._lock:
            self._ensureArbitration()
            self._arb_handed_over = bool(handed)
            if handed:
                # 渡した時点で自動側の履歴を捨てる。残すと、渡した直後の
                # 人の申告が『直前まで自動側が書いていた』と判定される。
                self._arb_last_auto = None

    def isHandedOver(self) -> bool:
        """いま人へ渡している最中かを返す（読むだけ）。"""
        with self._lock:
            self._ensureArbitration()
            return bool(getattr(self, "_arb_handed_over", False))

    def _accept(self, source: str | None, releasing: bool = False) -> bool:
        """その申告を受理してよいか。

        ここを書き換えるだけで
          全経路（pressButtons / releaseButtons / setHat /
          sendPosture / sendNeutralAll）に効く。
        スティック（setStick）は対象外とする。連続値であり所有権に
          なじまず、拒否すると半倒しが残るためである。

        判断は対称に作ってある。優先する側が書いてから cooldown の
          間だけ、反対側の申告を拒否する。どちらを優先するかが mode。
          「所有権」という長く続く状態を持たずに済むので、状態が増えない。

        cooldown は「最後に触ってからの無操作時間」である。触り続けて
          いる間は優先が続く（スライド式）。これは意図した挙動であり、
          人が触っている間は人が取りたい、という使い方に合わせている。
          逆に言うと、優先側が触り続けると反対側は永久に通らない。
          一時停止などで明示的に手を引く場合は setHandedOver(True) で
          調停そのものを止める（時間ではなく事実で判断する）。

        releasing が真の申告は拒否しない。解放と中立を拒否すると
          押しっぱなしが残り、調停の都合で安全が損なわれる。押下を断る
          ことはできても、離す操作を断る理由は無い。

        拒否の通知は錠の外で出す。_notifyReject は帳簿の更新だけを
          錠の中で行い、通知先の呼び出しは外で行うため、ここでは
          通知の要否だけを覚えて錠を出る。
        """
        notify = None
        if releasing:
            return True
        with self._lock:
            self._ensureArbitration()
            if getattr(self, "_arb_handed_over", False):
                # 自動側が手を引いている間は調停しない。時間ではなく、
                # 渡したという事実で判断する（一時停止中など）。
                return True

            if self._arb_mode == "off":
                return True

            now = time.perf_counter()
            is_human = self._isHumanSource(source)

            if self._arb_mode == "human":
                if is_human:
                    # 人が触ったら即座に主導権を取る（横取り＝preempt）。
                    # 触り続けている間は優先が続く（スライド式・意図どおり）。
                    self._arb_last_human = now
                    return True
                if (
                    self._arb_last_human is not None
                    and now - self._arb_last_human < self._arb_cooldown
                ):
                    self._arb_rejected["auto"] += 1
                    notify = (source, "人の操作")
                else:
                    self._arb_last_auto = now
                    return True
            elif self._arb_mode == "script":
                # スクリプトを優先する。書き続ける間は優先が続く
                # （スライド式・意図どおり）。
                if not is_human:
                    self._arb_last_auto = now
                    return True
                if (
                    self._arb_last_auto is not None
                    and now - self._arb_last_auto < self._arb_cooldown
                ):
                    self._arb_rejected["human"] += 1
                    notify = (source, "スクリプト")
                else:
                    self._arb_last_human = now
                    return True
            else:
                # 未知の mode は調停しない。黙って優先側として扱うより、
                # 通して利用者の手で止められる形にする。
                return True
        # ここへ落ちてくるのは拒否した経路だけである。受理した経路は
        #   上で True を返しているため、末尾の return True は到達不能で
        #   置かない（warn_no_return が将来の経路追加を見張る）。
        self._notifyReject(*notify)
        return False

    def pressButtons(self, btns: Any, source: str | None = None) -> bool:
        """ボタンを押す（差分の申告）。受理したら True。

        呼び出し側は「押したいボタン」だけを渡す。
          押していない他のボタンには一切触れないので、別系統が
          押しているものを消さない。
        """
        if not self._accept(source):
            return False
        self.markEvent(source)  # 入口の時刻（受理した申告だけ測る）
        self.applyButtons(press=btns, source=source)
        return True

    def releaseButtons(self, btns: Any, source: str | None = None) -> bool:
        """ボタンを離す（差分の申告）。受理したら True。"""
        if not self._accept(source, releasing=True):
            return False
        self.markEvent(source)  # 入口の時刻（受理した申告だけ測る）
        self.applyButtons(release=btns, source=source)
        return True

    def setHat(self, hat: Any = None, source: str | None = None) -> bool:
        """十字キーの向きを申告する。None で中立。受理したら True。

        向きの申告も所有者ごとに記録する。holdHat だけが記録していた
        頃は、通常の押下が suspend / drop の対象外になり、Pause で
        十字キーが漏れていた。None（中立）は解放と同じ扱いにし、自分の
        申告を取り下げる。
        """
        if not self._accept(source, releasing=hat is None):
            return False
        self.markEvent(source)  # 入口の時刻（受理した申告だけ測る）
        with self._lock:
            self._ensureHatHold()
            self._ensureOwners()
            owner = self._ownerKey(source)
            if hat is None:
                self._hat_held.pop(str(source), None)
                self._owner_hat.pop(owner, None)
            else:
                self._owner_hat[owner] = (int(hat), self.getRevision() + 1)
        self.applyHat(hat)
        return True

    def setStick(
        self,
        stick: str,
        x: int | None = None,
        y: int | None = None,
        source: str | None = None,
    ) -> bool:
        """スティックの座標を申告する。常に受理して True を返す。

        スティックは連続値であり、ボタンのような所有権になじまない。
        中立へ戻す途中も含めて拒否すると半倒しが残るため、調停の対象外
        とする。人の手で倒せる操作は、調停の mode にかかわらず出せる。
        複数系統が同時に触った場合は revision が新しい申告が採られる。
        """
        self.markEvent(source)  # 入口の時刻
        self.applyStick(stick, x, y, source=source)
        return True

    def sendPosture(self, source: str | None = None) -> bool:
        """現在姿勢を送る。Pico live経路ではmailboxだけを使う。

        送るのは「合成後の全体」である。棄却された系統の値は入って
        いないが、通った別系統の値（常時受理のスティックなど）は入る。
        調停で送出そのものを断られた行は、次の受理で送り直される。
        中立への復帰は sendNeutralAll（常時受理）で行うため、解放が
        届かないことはない。
        """
        if not self._accept(source):
            return False
        # apply* が既に mailbox へ入れている。ここで同期送信
        #   するとGUIスレッドが Transport の錠と UART 書き込みを待ち、
        #   worker と二重送信になる。legacy だけ従来どおり同期送信する。
        if self._liveCapable():
            return True
        self.writeRow(self._buildRow())
        return True

    def sendNeutralAll(self, source: str | None = None) -> bool:
        """すべて中立へ戻す。live 経路では優先送信で mailbox へ渡す。

        既に中立であっても再送する。releaseAll は状態が変わらな
        ければ mailbox へ渡さないため、ここで現在の snapshot を優先送信
        する。revision は増やさない。
        """
        if not self._accept(source, releasing=True):
            return False
        self.releaseAll()
        if self._liveCapable():
            self.putLive(self.snapshot(), priority=True)
            return True
        self.writeRow(self._buildRow())
        return True

    def _bumpRevision(self) -> int:
        """姿勢が変わったことを記録する。錠の中から呼ぶこと。

        呼び忘れると snapshot の revision が据え置きになり、
          「変わっていない」と誤判定され、worker が送信を省く。
        """
        rev = getattr(self, "_posture_revision", 0) + 1
        self._posture_revision = rev
        return rev

    def getRevision(self) -> int:
        """いまの revision。読むだけで何も変えない。"""
        return int(getattr(self, "_posture_revision", 0))

    def snapshot(self) -> dict[str, Any]:
        """現在の姿勢の写しを1つ返す（読み取り専用・副作用なし）。

        返すのは新しい辞書なので、呼び出し側が書き換えても
          Sender の状態には影響しない。逆に、返したあとで Sender 側が
          変わっても、返した写しは変わらない（immutable として扱える）。

        含めるもの: btn / hat / lx / ly / rx / ry / revision。
          変化印（_L/_R_stick_changed）は含めない。あれは legacy の
            可変長書式のための状態で、状態そのものではないため。
        """
        with self._lock:
            self._ensurePosture()
            p = self._posture
            return {
                "btn": int(p["btn"]),
                "hat": int(p["hat"]),
                "lx": int(p["lx"]),
                "ly": int(p["ly"]),
                "rx": int(p["rx"]),
                "ry": int(p["ry"]),
                "revision": self.getRevision(),
            }

    def verifySnapshotPurity(self, times: int = 3) -> bool:
        """snapshot を何度読んでも同じ値かを確かめる。

        _buildRow は2度目に違う行を返す（変化印が下りるため）。
          snapshot がその轍を踏んでいないことを機械で確かめる。
        目視で「副作用は書いていない」と言うのではなく、実際に
          複数回読んで一致を見る。
        """
        if times < 2:
            times = 2
        first = self.snapshot()
        for _ in range(times - 1):
            if self.snapshot() != first:
                return False
        return True

    # ------------------------------------------------------------------
    # Pico 専用 full-state encoder
    #
    # 何のためか:
    #   Pico ファームは "S <btn> <hat> <lx> <ly> <rx> <ry>" を受け取る。
    #   従来書式（_buildRow）とは別書式なので、変換する口が要る。
    #
    # 従来書式と決定的に違う2点（ここを間違えると別のボタンが押される）:
    #   1. 従来書式は btn を2ビット左シフトし、下位2bit へスティックの
    #     変化印を入れる。Pico はシフトしない。同じ姿勢でも値が4倍違う。
    #   2. 従来書式は変化した側のスティックだけを付ける可変長。
    #     Pico は6項目すべて必須。足りないと ERR を返される。
    #
    # だから Pico 側には「変化印」という概念が要らない。
    #   毎回すべての軸を送るので、1行落ちても次の行で復旧する
    #   （フルステート方式）。
    #
    # 書式の細かい決まり（すべて Pico ファームの受信仕様による）:
    #   6項目とも16進。hat も16進。
    #   "0x" を付けてはいけない（x は不正文字として ERR になる）。
    #   区切りは空白1つ以上。
    #
    # 純関数にしてある理由:
    #   snapshot だけを引数に取り、self を読まない（@staticmethod）。
    #   同じ snapshot からは常に同じ行が出る。何度呼んでも変わらない。
    #   _buildRow のような読み取り副作用を持ち込まない。
    # ------------------------------------------------------------------

    @staticmethod
    def encodePicoState(snap: dict[str, Any]) -> str:
        """snapshot から Pico 用の1行を組む（純関数・副作用なし）。

        引数は snapshot() が返す辞書（btn / hat / lx / ly / rx / ry）。
          revision は行に含めない。通信内容ではなく PC 側の順序情報。

        戻り値に改行は付けない。線へ出すときに付ける側が決める
          （legacy 側も同じ約束: Transport が付ける）。
        """
        return "S " + " ".join(
            format(Sender._picoField(snap, key), "x")
            for key in ("btn", "hat", "lx", "ly", "rx", "ry")
        )

    @staticmethod
    def _picoField(snap: dict[str, Any], key: str) -> int:
        """1項目を Pico が受け取れる範囲へ収める。

        範囲外を黙って送らない。btn は16bit、他は8bit が上限で、
          超えた値を送ると Pico 側で別の意味になる（切り詰められる）。
        hat は 8 超を中立へ丸める（Pico ファームと同じ規則）。
          PC 側で先に丸めるのは、送った行と Pico の状態を一致させるため。
          丸めを Pico 任せにすると、PC が思っている姿勢とずれる。
        """
        value = int(snap[key])
        if value < 0:
            raise ValueError(f"Pico へ負の値は送れません: {key}={value}")
        if key == "btn":
            if value > 0xFFFF:
                raise ValueError(f"btn が16bitを超えています: {value}")
            return value
        if key == "hat":
            # 8 超は中立（8）。Pico ファームと同じ扱いを PC 側でも行う。
            return value if value <= 8 else 8
        if value > 0xFF:
            raise ValueError(f"{key} が8bitを超えています: {value}")
        return value

    def buildPicoRow(self) -> str:
        """いまの姿勢から Pico 用の1行を組む（読み取りだけ・副作用なし）。

        snapshot() を通すので、_buildRow のように変化印は下りない。
          何度呼んでも同じ行が返る。125Hz で呼び続けても壊れない。
        """
        return self.encodePicoState(self.snapshot())

    @staticmethod
    def verifyPicoEncoder() -> bool:
        """encoder が仕様どおりかを機械で確かめる。

        目視で「書けている」と言わず、実際に組んで照合する。
        Pico ファームの受け入れ条件だけを根拠にする。
        """
        base = {
            "btn": 0,
            "hat": 8,
            "lx": 0x80,
            "ly": 0x80,
            "rx": 0x80,
            "ry": 0x80,
            "revision": 0,
        }

        # 中立の行
        if Sender.encodePicoState(base) != "S 0 8 80 80 80 80":
            return False
        # B ボタン(0x0002)。従来書式と違いシフトしない
        b_down = dict(base, btn=0x0002)
        if Sender.encodePicoState(b_down) != "S 2 8 80 80 80 80":
            return False
        # 16進で出ること（10進なら 255 になる）
        maxed = dict(base, lx=0xFF)
        if Sender.encodePicoState(maxed) != "S 0 8 ff 80 80 80":
            return False
        # 何度呼んでも同じ（純関数）
        if Sender.encodePicoState(base) != Sender.encodePicoState(base):
            return False
        # 項目数は必ず7（S + 6項目）
        if len(Sender.encodePicoState(base).split()) != 7:
            return False
        # "0x" が混ざらない（混ざると Pico が ERR を返す）
        if "0x" in Sender.encodePicoState(dict(base, btn=0xABCD)):
            return False
        # hat の 8 超は中立へ丸める
        if Sender.encodePicoState(dict(base, hat=9)) != "S 0 8 80 80 80 80":
            return False
        # 範囲外は黙って送らず例外にする
        try:
            Sender.encodePicoState(dict(base, lx=0x100))
            return False
        except ValueError:
            pass
        return True

    # ------------------------------------------------------------------
    # Pico live worker と latest-state mailbox
    #
    # 何をするものか:
    #   8ms（125Hz）ごとに現在の姿勢を1行送り続ける仕組み。
    #   実物のコントローラーが常に現在値を返しているのと同じ考え方。
    #
    # mailbox（郵便受け）と呼んでいるもの:
    #   容量1の置き場。新しい姿勢が来たら古いものを上書きする。
    #   積まない（FIFO にしない）のが要点。125Hz のライブ状態で
    #     行列を作ると、古い姿勢が後から届いて遅延が溜まる。
    #   Transport の間引きも同じ作りで、
    #     保留は常に1行・後から来た行で上書きしている。
    #
    # 前提
    #   startLiveWorker を呼ばない限りスレッドは起動しない。
    #
    # 優先送信
    #   Stop、Neutral、Release は次のスロットを待たずに送出する。
    #   スティックの中間値は破棄してよいが、解放操作は破棄できない。
    #   破棄すると押下状態が残る。
    # ------------------------------------------------------------------

    # 8 ms は 125 Hz に相当し、Pico ファームの報告周期および USB 記述子の
    #   間隔と同じ値である。これより短い周期で送出しても Switch には
    #   届かず、UART が滞留する。
    LIVE_SLOT_S = 0.008
    # keepalive 間隔。実機で測定した維持上限 180 ms の半分を 8 ms 単位に
    #   切り下げた値である。
    LIVE_KEEPALIVE_S = 0.088
    # 切断時に中立の送出を待つ上限。8 ms スロットの数回分を見込む。
    CLOSE_DRAIN_S = 0.05
    # 切断時に worker の停止を待つ上限。閉じられない状態を作らない。
    CLOSE_JOIN_S = 0.30
    # この長さ未満の press は Pico の時刻付きキューへ回す。
    #   8 ms 未満の押下は mailbox の構造により
    #   線に出ない回がある。mailbox は容量 1 で上書きするため、
    #   worker が見に来る前に押して離すと押下が解放に上書きされる。
    #   周期の乱れではなく設計の帰結なので、PC 側では解けない。
    #   0 にすればこの経路を使わなくなる（実質の無効化）。
    QUEUE_THRESHOLD_S = 0.008
    # キューの実行が終わるのを待つ上限。押下の長さ＋往復の余裕。
    QUEUE_WAIT_MARGIN_S = 0.5

    def _ensureLiveState(self) -> None:
        """live 経路の mailbox、worker 状態、統計、表示状態を初期化する。"""
        if not hasattr(self, "_live_lock"):
            self._live_lock = threading.Lock()
        if not hasattr(self, "_live_box"):
            self._live_box = None
        if not hasattr(self, "_live_wake"):
            self._live_wake = threading.Event()
        if not hasattr(self, "_live_stop"):
            self._live_stop = threading.Event()
        if not hasattr(self, "_live_thread"):
            self._live_thread = None
        if not hasattr(self, "_live_stats"):
            self._live_stats = {
                "put": 0,
                "replaced": 0,
                "sent": 0,
                "keepalive": 0,
                "priority": 0,
                "last_revision": -1,
                "inversions": 0,
            }
        if not hasattr(self, "_live_last_snap"):
            self._live_last_snap: dict[str, Any] | None = None
        if not hasattr(self, "_live_last_sent_at"):
            self._live_last_sent_at: float | None = None
        if not hasattr(self, "_live_timer_raised"):
            self._live_timer_raised = False
        if not hasattr(self, "_live_closing"):
            self._live_closing = False
        if not hasattr(self, "_live_last_shown_at"):
            self._live_last_shown_at: float | None = None
        if not hasattr(self, "_live_last_shown_snap"):
            self._live_last_shown_snap: dict[str, Any] | None = None

    def putLive(
        self, snap: dict[str, Any] | None = None, priority: bool = False
    ) -> None:
        """最新の状態を容量 1 の mailbox へ置く（古い未送信は破棄する）。

        通常の変化では worker を起床させない。次の 8 ms スロットで最新値
        が読み出される。即時に起床させると 125 Hz を超え、UART が滞留する。

        切断中は優先送信だけを受け付ける。切断時に送る中立の
        後に、通常の状態が入らないようにするためである。
        """
        self._ensureLiveState()
        if self._live_closing and not priority:
            return
        if snap is None:
            snap = self.snapshot()
        with self._live_lock:
            if self._live_box is not None:
                self._live_stats["replaced"] += 1
            self._live_box = snap
            self._live_stats["put"] += 1
            if priority:
                self._live_stats["priority"] += 1
        if priority:
            self._live_wake.set()

    def takeLive(self) -> dict[str, Any] | None:
        """mailbox から取り出す。取り出した後は空にする。

        新しい状態がなければ None を返し、worker は送出しない。同じ状態を
        送出し続けないのは、Pico が 8 ms ごとに自身で HID レポートを送出する
        ためである。
        """
        self._ensureLiveState()
        with self._live_lock:
            snap, self._live_box = self._live_box, None
        return snap

    def _showLiveRow(
        self, snap: dict[str, Any], now: float, is_keepalive: bool
    ) -> bool:
        """送信行の表示可否を返す。表示は 20 Hz に制限し、解放と中立は即時に
        表示する。keepalive は表示しない。
        """
        if is_keepalive or not self._should_show_serial():
            return False
        prev = self._live_last_shown_snap
        releasing = False
        if prev is not None:
            stick_released = any(
                int(prev[k]) != self.POSTURE_CENTER
                and int(snap[k]) == self.POSTURE_CENTER
                for k in ("lx", "ly", "rx", "ry")
            )
            releasing = (
                (int(snap["btn"]) & int(prev["btn"])) != int(prev["btn"])
                or (
                    int(prev["hat"]) != self.POSTURE_HAT_CENTER
                    and int(snap["hat"]) == self.POSTURE_HAT_CENTER
                )
                or stick_released
            )
        due = (
            self._live_last_shown_at is None or now - self._live_last_shown_at >= 0.050
        )
        if releasing or due:
            self._live_last_shown_at = now
            self._live_last_shown_snap = dict(snap)
            return True
        return False

    def _beginPreciseTimer(self) -> None:
        """Windows のタイマ分解能を 1ms へ上げる。

        既定の分解能は約15.6ms。Event.wait(8ms) がそこへ丸められ、
          実測で約16ms（＝2スロット分）になっていた。
        winmm が無い環境（Windows 以外）では何もしない。
        """
        if getattr(self, "_live_timer_raised", False):
            return
        try:
            import ctypes

            ctypes.WinDLL("winmm").timeBeginPeriod(1)
            self._live_timer_raised = True
        except Exception:
            # 上げられなくても送信は続ける。周期が粗いだけで壊れない。
            self._live_timer_raised = False

    def _endPreciseTimer(self) -> None:
        """上げた分解能を必ず戻す（上げっぱなしは消費電力に響く）。"""
        if not getattr(self, "_live_timer_raised", False):
            return
        try:
            import ctypes

            ctypes.WinDLL("winmm").timeEndPeriod(1)
        except Exception:
            pass
        self._live_timer_raised = False

    def _liveLoop(self, transport: Any) -> None:
        """8 ms の締切ごとに最新状態を送り、無変化時は 88 ms で再送する。

        待ちには起床合図（_live_wake）を使う。締切までの残り時間を上限と
        して待ち、合図が来た場合は締切を待たずに送出する。これが
        優先送信であり、解放と中立を次のスロットまで待たせない。

        通常の変化では合図を出さないため、送出は 8 ms 周期に収まる。これ
        より速く送っても Pico の HID レポート周期を超えるだけである。
        """
        self._ensureLiveState()
        self._beginPreciseTimer()
        try:
            deadline = time.perf_counter()
            while not self._live_stop.is_set():
                deadline += self.LIVE_SLOT_S
                remain = deadline - time.perf_counter()
                if remain > 0:
                    # 合図が来れば締切前でも起きる（優先送信）。
                    if self._live_wake.wait(remain):
                        self._live_wake.clear()
                        deadline = time.perf_counter()
                else:
                    # 遅れた分は取り戻さない。締切を現在時刻へ引き直す。
                    deadline = time.perf_counter()
                if self._live_stop.is_set():
                    break
                snap = self.takeLive()
                now = time.perf_counter()
                is_keepalive = False
                if snap is None:
                    with self._live_lock:
                        last_snap = self._live_last_snap
                        last_at = self._live_last_sent_at
                    if (
                        last_snap is None
                        or last_at is None
                        or now - last_at < self.LIVE_KEEPALIVE_S
                    ):
                        continue
                    snap = last_snap
                    is_keepalive = True
                with self._live_lock:
                    self._live_last_snap = snap
                self._recordLiveOrder(snap)
                try:
                    show = self._showLiveRow(snap, now, is_keepalive)
                    transport.send_row(self.encodePicoState(snap), measure_perf=show)
                    with self._live_lock:
                        self._live_stats["sent"] += 1
                        if is_keepalive:
                            self._live_stats["keepalive"] += 1
                        self._live_last_sent_at = time.perf_counter()
                except Exception:
                    self._logLiveError()
        finally:
            # worker の終了時は必ず戻す（異常終了でも通る）。
            self._endPreciseTimer()

    def _recordLiveOrder(self, snap: dict[str, Any]) -> None:
        """revision が逆転していないかを数える。"""
        rev = int(snap.get("revision", 0))
        if rev < self._live_stats["last_revision"]:
            self._live_stats["inversions"] += 1
        self._live_stats["last_revision"] = rev

    def _logLiveError(self) -> None:
        """送信の失敗を記録する。黙って捨てない。"""
        logger = getattr(self, "_logger", None)
        if logger is not None:
            logger.error("live worker send failed:\n" + traceback.format_exc())

    def startLiveWorker(self, transport: Any = None) -> bool:
        """workerを1本だけ起動し、現在姿勢を最初に送る。"""
        self._ensureLiveState()
        if self._live_thread is not None and self._live_thread.is_alive():
            return False
        if transport is None:
            transport = getattr(self, "transport", None)
        if transport is None:
            return False
        self._live_stop.clear()
        self._live_wake.clear()
        self._live_last_snap = None
        self._live_last_sent_at = None
        self._live_last_shown_at = None
        self._live_last_shown_snap = None
        self.putLive(self.snapshot())
        thread = threading.Thread(
            target=self._liveLoop, args=(transport,), name="PicoLiveWorker", daemon=True
        )
        self._live_thread = thread
        thread.start()
        return True

    def queueThreshold(self) -> float:
        """短い press をキューへ回す境目（秒）を返す。

        判断の場所を 1 つに保つ。呼び出し側が定数を直接読むと、
        将来この条件へ何かを足したときに片方だけ直す事故が起きる。
        """
        return float(getattr(self, "QUEUE_THRESHOLD_S", 0.0))

    def shouldQueue(self, duration: float) -> bool:
        """この press を Pico のキューへ回すべきかを返す。

        条件は 3 つある。閾値より短いこと、live 経路であること、
        キュー対応が否定されていないこと。
        legacy（Leonardo）にはキューが無いので、必ず False になる。
        閾値が 0 ならこの経路を使わない（実質の無効化）。
        live 経路でも、Q 応答が無い相手（wakecon など）だと分かれば
        使わない。送りっぱなしでは非対応を見分けられず、押下が黙って
        消えるためである（対応の有無は runQueued が1度だけ確かめる）。
        """
        if self._queueKnownUnsupported():
            return False
        limit = self.queueThreshold()
        if limit <= 0:
            return False
        try:
            if float(duration) >= limit:
                return False
        except (TypeError, ValueError):
            return False
        return self._liveCapable()

    def _queueKnownUnsupported(self) -> bool:
        """キュー非対応と確定済みか（読むだけ）。"""
        return bool(getattr(self, "_queue_unsupported", False))

    def _noteQueueSupported(self, supported: bool) -> None:
        """キュー対応の有無を覚える。非対応と分かれば以後 Q へ回さない。

        対応は QOK で確定する。満杯（QFULL）は対応の証拠なので、
        非対応にはしない（混んでいるだけで、従来経路へ落とす）。
        無応答・書式違い（ERR）は非対応とみなす。
        繋ぎ直し・通信方式の切替えでは _forgetQueueSupport で消す。
        """
        if supported:
            self._queue_unsupported = False
        else:
            self._queue_unsupported = True

    def _forgetQueueSupport(self) -> None:
        """キュー対応の記憶を消す。相手が変わる場面で呼ぶ。"""
        self._queue_unsupported = False

    def runQueued(
        self, duration: float, buttons: Any = None, source: str | None = None
    ) -> bool:
        """押下を、Pico のキューで duration だけ実行する。

        押して待って離すのを PC 側で行わず、Pico に時刻と長さを渡す。
        mailbox を通らないので、8 ms 未満でも押下が消えない。

        buttons を受け取る形にしてある。呼び出し側が事前に姿勢を作ると、
          Q 行と S 行の両方で押す形になり、S 行は mailbox を通るため
          消えうる。消えない経路を作る意味がなくなるので、ここで
          buttons を受け取れば呼び出し側は姿勢を作らなくてよい。

        受理されたら True を返す。False のときは呼び出し側が従来の
        経路へ落とす。キューが満杯・実行中・応答が来ないといった
        場面で操作そのものが消えるのは最悪だからである。

        UART への書き出しは錠の外で行う。錠の中で線を待つと、その間
        姿勢の読み取りまで止まる。写しと行文は錠の中で作り、送るのは
        外で行う。Q の前に mailbox の古い S を破棄する。さもないと
        Q より古い状態が後から送出され、押下が一瞬戻る。
        なお実行中の S ワーカーまでは止めない。Q の実行と S の送出の
        調停は Pico 側が行う前提であり、PC が待つのは次の操作と
        混ざらないためだけである。

        N は送らない。N は Pico 側で全体を中立に戻すため、hold
        で押しっぱなしにしている姿勢まで解除され、短い press のたびに
        hold が途切れる。Q 行自体が完全な姿勢を持つので N は不要である。

        Q と R は応答で確かめる。Q には QOK、R には QRUN を期待する。
        送りっぱなしでは、キューを持たない相手（wakecon など）でも
        送れたことになり、押下が黙って消える。QFULL（混雑）は対応の
        証拠なので非対応にせず、従来経路へ落とすだけにする。
        無応答・ERR は非対応と覚え、以後 Q へ回さない。
        R が通らなかったときは、積んだ Q が次回の R で誤爆しないよう
        N で流す。N はキューを空にする最後の砦である。
        """
        transport = self.transport
        if transport is None or not self._liveCapable():
            return False

        # いまの姿勢を土台にし、押すボタンだけを足す。
        #   hold で押しっぱなしのものを土台が持っているので、
        #   キュー経由でも hold が維持される。
        snap = self.snapshot()
        if buttons is not None:
            bits = int(snap["btn"])
            for btn in buttons if isinstance(buttons, (list, tuple)) else [buttons]:
                try:
                    bits |= int(btn)
                except (TypeError, ValueError):
                    return False  # 解釈できない値は従来経路へ落とす
            snap = dict(snap)
            snap["btn"] = bits

        try:
            ms = max(1, int(round(float(duration) * 1000.0)))
        except (TypeError, ValueError):
            return False
        line = self.encodeQueuedState(snap, tick=0, dur=ms)
        try:
            self.discardLive()
            answered = self._runQueueExchange(transport, line)
            if not answered:
                return False
            return self._waitQueueDone(transport, duration)
        except Exception:
            # 送れなかった理由は問わない。呼び出し側が従来経路へ落とす。
            return False

    def _runQueueExchange(self, transport: Any, line: str) -> bool:
        """Q と R を応答付きで送る。実行まで漕ぎ着けたら True。

        Q には QOK を期待する。QFULL は混雑（対応はしている）なので
        非対応にはせず False で従来経路へ落とす。無応答・ERR は非対応
        と覚える。R には QRUN を期待し、通らなければ積んだ Q を N で
        流してから False を返す。
        """
        from Commands.WakeLink import expect

        first = expect(transport, line, ("QOK", "QFULL", "ERR", "BUSY"), timeout=0.5)
        if first is None or first.startswith("ERR"):
            # 無応答・書式違いはキュー非対応。以後は Q へ回さない。
            self._noteQueueSupported(False)
            return False
        if not first.startswith("QOK"):
            # QFULL / BUSY: 対応はしているが今は混んでいる。
            return False
        self._noteQueueSupported(True)
        second = expect(transport, "R", ("QRUN", "QEMPTY", "ERR", "BUSY"), timeout=0.5)
        if second is not None and second.startswith("QRUN"):
            return True
        # R が通らないと、積んだ Q が次回の R で誤爆する。N で流す。
        #   応答は待たない。pico-wakeCon に N は無く、待つと 0.5 秒だけ
        #   止まる。応答のある旧ファームの OK 行は、次の expect / query
        #   が読み始めに捨てる（drain）ため、残しても害は無い。
        self._sendQueueLine(transport, "N")
        return False

    @staticmethod
    def encodeQueuedState(snap: dict[str, Any], tick: int, dur: int) -> str:
        """snapshot から Q 行を組む（純関数・副作用なし）。

        書式は Pico ファームのキュー行の解釈と対である。S 行の先頭へ
        tick と dur を足しただけなので、encodePicoState と同じ並びを
        そのまま使う。書式の解釈を 2 か所に分けない。
        """
        body = Sender.encodePicoState(snap)
        return "Q %04x %04x %s" % (tick & 0xFFFF, dur & 0xFFFF, body[2:])

    def _sendQueueLine(self, transport: Any, line: str) -> bool:
        """Q 行や R 行を 1 本送る。送れたら True。

        後方互換のため残す。runQueued は応答付きの _runQueueExchange を
        使う。送りっぱなしでは非対応を見分けられないため、新規には
        こちらの使用を推奨しない。
        """
        writer = getattr(transport, "send_row", None)
        if not callable(writer):
            return False
        writer(line, measure_perf=False)
        return True

    def _waitQueueDone(self, transport: Any, duration: float) -> bool:
        """実行が終わるまで待つ。

        R まで通った後の待ちである。Pico は自分の時計で実行するので、
        PC が待たなくても押下は正しく行われる。
        PC が待つのは「次の操作と混ざらないため」だけである。
        対象は 8 ms 未満なので、待っても実害が無い。
        """
        time.sleep(float(duration))
        return True

    def discardLive(self) -> bool:
        """mailbox の未送信の状態を破棄する。

        破棄したものがあれば True を返す。中立を送る前に呼ぶことで、
        中立の後に古い状態が送出されることを防ぐ。
        """
        self._ensureLiveState()
        with self._live_lock:
            had = self._live_box is not None
            self._live_box = None
        return had

    def waitLiveDrained(self, timeout: float = 0.05) -> bool:
        """mailbox が空になるまで待つ。

        worker が取り出して送出し終えると mailbox は空になる。期限内に
        空になれば True を返す。無期限には待たない。終了できない状態を
        作らないためである。
        """
        self._ensureLiveState()
        limit = time.perf_counter() + max(float(timeout), 0.0)
        while time.perf_counter() < limit:
            with self._live_lock:
                if self._live_box is None:
                    return True
            if not self.isLiveWorkerRunning():
                # worker が居なければ待っても空にならない。
                return False
            time.sleep(0.001)
        with self._live_lock:
            return self._live_box is None

    def stopLiveWorker(self, timeout: float = 1.0) -> bool:
        """常駐ワーカーを止める。止まるまで待つ。

        止め方の順序が大事: 先に停止印を立て、次に起こす。
          逆にすると、起こした直後に眠り直す窓ができる。
        """
        self._ensureLiveState()
        self._live_stop.set()
        self._live_wake.set()
        thread = self._live_thread
        if thread is None:
            return True
        thread.join(timeout)
        alive = thread.is_alive()
        if not alive:
            self._live_thread = None
        return not alive

    def isLiveWorkerRunning(self) -> bool:
        """ワーカーが動いているか（読むだけ）。"""
        self._ensureLiveState()
        thread = self._live_thread
        return thread is not None and thread.is_alive()

    def getLiveStats(self) -> dict[str, Any]:
        """帳簿の写しを返す（読むだけ・副作用なし）。"""
        self._ensureLiveState()
        with self._live_lock:
            return dict(self._live_stats)

    def clearLiveStats(self) -> None:
        """live統計を初期化する。"""
        self._ensureLiveState()
        with self._live_lock:
            for key in (
                "put",
                "replaced",
                "sent",
                "keepalive",
                "priority",
                "inversions",
            ):
                self._live_stats[key] = 0
            self._live_stats["last_revision"] = -1
