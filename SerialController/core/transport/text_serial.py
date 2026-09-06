#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""text_serial.py - pyserial でテキスト行を送る実装（線そのもの）。

ここにある処理は Sender の writeRow / _is_coalescable / _write /
openSerial / closeSerial からそのまま移したもので、間引きの条件も
例外の扱いも同じ。送る中身は変えない。
"""

from __future__ import annotations

import os
import platform
import threading
import time
from collections.abc import Callable
from logging import DEBUG, NullHandler, getLogger
from typing import Any, cast

import serial
from core.transport.base import PICO_LIVE_STATE, Transport

# 送信の最小間隔(秒)。この間隔より短く呼ばれた分は最新値へまとめて1回にする。
# 9600bps 時代の固定値 0.02 をそのまま使うと、通信速度を上げても間引きが
# 頭を押さえてしまう。38400 では1行(22バイト)の伝送が約5.7ms なのに対し、
# 0.02 は50行/秒＝20msに1本しか通さない。速度を3倍にした効果が出ない。
#
# かといって短くしすぎると、今度は送信が伝送速度を追い越す。この線には
# フロー制御が無く、相手の空き状況を見ずに送るため、溢れた分は取りこぼす。
# そこで下限を「1行の伝送時間の2倍」とし、上限は従来の 0.02 に据え置く。
MIN_SEND_INTERVAL = 0.02  # 上限（従来値）。接続前や算出不能時はこれ
SEND_ROW_BYTES = 22  # "0x0003 8 80 80 80 80" + CRLF の最大長
BITS_PER_BYTE = 10  # 8N1（スタート1＋データ8＋ストップ1）
SEND_INTERVAL_MARGIN = 2.0  # 伝送時間の何倍を下限にするか
# write が無期限にブロックし、GUI ごと固まるのを防ぐ
READ_TIMEOUT = 0.5
WRITE_TIMEOUT = 0.5


class TextSerialTransport(Transport):
    """P1 legacy_text - 現行と同じテキスト行を pyserial で送る実装。

    本家のマイコン（Leonardo）へそのまま繋がる。既定として
      これだけを用意し、挙動を変えない。
    """

    name = "legacy_text"

    def __init__(self, logger: Any = None) -> None:
        self.ser: serial.Serial | None = None
        self._logger = logger if logger is not None else getLogger(__name__)
        if logger is None:
            self._logger.addHandler(NullHandler())
            self._logger.setLevel(DEBUG)
            self._logger.propagate = True
        # 送信の入口は4経路あり、うち3つは別のスレッドから来る（GUI /
        # コマンド / キーボード）。守らないと間引き判定が誤る・保留行が
        # 失われる。RLock にするのは close が flush_pending を呼び、
        # 同じスレッドで錠を取り直すため（Lock だとそこで自分自身を待つ）。
        self._lock = threading.RLock()
        self._last_write = 0.0
        self._pending: str | None = None
        self._before: str | None = None
        self._send_interval = MIN_SEND_INTERVAL
        self._on_write_begin: Callable[..., None] | None = None
        self._on_write_end: Callable[..., None] | None = None
        self.listeners: list[Callable[[str], None]] = []
        # 一度落ちた聞き手の id()。同じ苦情を繰り返さない
        self._listener_ng: set[int] = set()
        # 受信の分配先。読みスレッドは1本だけで、購読者と応答待ちの
        # 両方へ同じ行を届ける。2か所で read すると横取りが起きるため、
        # 読み口はここへ一本化する (WakeLink もここを通る)。
        self._rx_lock = threading.Lock()
        self._rx_subs: list[Callable[[str], None]] = []
        self._rx_waiters: list[tuple[tuple[str, ...], threading.Event, dict]] = []
        self._rx_thread: threading.Thread | None = None
        self._rx_stop = threading.Event()

    # -- 聞き手の付け外し ---------------------------------------------------

    def add_listener(self, func: Callable[[str], None]) -> bool:
        """送信行を受け取る相手を足す。入力ログはここへ繋ぐ。

        重ねて足さない。同じ相手を2回足すと同じ行が2回届く。
        """
        with self._lock:
            if func not in self.listeners:
                self.listeners.append(func)
        return True

    def remove_listener(self, func: Callable[[str], None]) -> None:
        """聞き手を外す。外したら苦情の記録も消す（付け直せる）。"""
        with self._lock:
            if func in self.listeners:
                self.listeners.remove(func)
            self._listener_ng.discard(id(func))

    # -- 受信の読みポンプ -----------------------------------------------------
    # 読みスレッドは1本だけにする。WakeLink の応答待ちとモニタ表示が
    # 別々に read すると、相手の行を横取りして応答が消える。
    # ポンプが読んだ行は購読者全員と、待機中の wait_rx へ届ける。

    def subscribe_rx(self, func: Callable[[str], None]) -> Callable[[], None]:
        """受信1行ごとの購読。戻り値は解除用の呼び出し。"""
        with self._rx_lock:
            if func not in self._rx_subs:
                self._rx_subs.append(func)

        def _unsub() -> None:
            with self._rx_lock:
                if func in self._rx_subs:
                    self._rx_subs.remove(func)

        return _unsub

    def wait_rx(self, prefixes: Any, timeout: float = 0.5) -> str | None:
        """最初に前方一致した1行を待つ。見つかればその行を返す。

        ポンプが動いていないときは None を返し、呼び出し側は
        従来の直接読みへ回る。購読者にも同じ行が届く (横取りしない)。
        """
        if isinstance(prefixes, str):
            prefixes = (prefixes,)
        try:
            want = tuple(prefixes)
            limit = max(0.0, float(timeout))
        except (TypeError, ValueError):
            return None
        if not self.rx_pump_running():
            return None
        found: dict[str, str] = {}
        done = threading.Event()
        entry = (want, done, found)
        with self._rx_lock:
            self._rx_waiters.append(entry)
        try:
            if not done.wait(limit):
                return None
            return found.get("line")
        finally:
            with self._rx_lock:
                if entry in self._rx_waiters:
                    self._rx_waiters.remove(entry)

    def rx_pump_running(self) -> bool:
        """読みポンプが動いているか（読むだけ）。"""
        thread = self._rx_thread
        return thread is not None and thread.is_alive()

    def start_rx_pump(self) -> bool:
        """読みポンプを1本だけ起動する。動いていれば True。"""
        if self.rx_pump_running():
            return True
        if self.ser is None:
            return False
        self._rx_stop.clear()
        thread = threading.Thread(
            target=self._rx_loop, name="PokeConRxPump", daemon=True
        )
        self._rx_thread = thread
        thread.start()
        return True

    def stop_rx_pump(self) -> None:
        """読みポンプを止める。止まるまで待つ (読みタイムアウトが上限)。"""
        self._rx_stop.set()
        thread = self._rx_thread
        if thread is None:
            return
        thread.join(READ_TIMEOUT + 0.5)
        if not thread.is_alive():
            self._rx_thread = None

    def _dispatch_rx(self, text: str) -> None:
        """1行を購読者と待機中の wait_rx へ届ける。"""
        with self._rx_lock:
            subs = list(self._rx_subs)
            waiters = list(self._rx_waiters)
        for func in subs:
            try:
                func(text)
            except Exception:
                self._logger.debug("rx 購読者で例外", exc_info=True)
        for want, done, found in waiters:
            try:
                if any(text.startswith(prefix) for prefix in want):
                    found.setdefault("line", text)
                    done.set()
            except Exception:
                self._logger.debug("rx 待機の照合で例外", exc_info=True)

    def _rx_loop(self) -> None:
        """受信を読み続け、行ができたら分配する。例外では落ちない。"""
        buf = bytearray()
        while not self._rx_stop.is_set():
            ser = self.ser
            if ser is None:
                break
            try:
                chunk = ser.read(64)
            except Exception:
                self._logger.debug("rx 読み取りで例外", exc_info=True)
                break
            if not chunk:
                continue
            buf.extend(chunk)
            while True:
                idx = buf.find(b"\n")
                if idx < 0:
                    break
                raw = bytes(buf[:idx])
                del buf[: idx + 1]
                try:
                    text = raw.decode("ascii", errors="replace").strip()
                except Exception:
                    continue
                self._dispatch_rx(text)

    # -- 線の開け閉め -------------------------------------------------------

    @staticmethod
    def calc_send_interval(baudrate: int) -> float:
        """通信速度から送信の最小間隔を決める。

        速い線ほど間引きを緩めてよいが、いくらでも短くしてよいわけでは
        ない。この線には相手の空き状況を見る仕組みが無いので、1行を送り
        終える前に次を積むと送信バッファが詰まり、操作が遅れて効くように
        なる。そこで1行の伝送時間に余裕を掛けたものを下限とし、従来値
        0.02 を上限に置く。

        9600 では算出値が約 0.046 秒となり上限側の 0.02 が採られるため、
        従来と同じ挙動になる。38400 では約 0.0115 秒となり間引きが緩む。
        """
        try:
            baudrate = int(baudrate)
        except (TypeError, ValueError):
            return MIN_SEND_INTERVAL
        if baudrate <= 0:
            return MIN_SEND_INTERVAL
        row_sec = SEND_ROW_BYTES * BITS_PER_BYTE / baudrate
        return min(MIN_SEND_INTERVAL, row_sec * SEND_INTERVAL_MARGIN)

    @property
    def send_interval(self) -> float:
        """いま使っている間引き幅（検算・表示用）。"""
        return self._send_interval

    @staticmethod
    def _default_port_path(portNum: int) -> str | None:
        """portName 未指定時の候補パス。分岐は組み立てのみ。

        posix/Darwin 分岐は残す（mac/linux の利用者がいる）。
        未知OSでは None を返し、呼び出し側が portName 指定へ誘導する。
        """
        if os.name == "nt":
            return "COM" + str(portNum)
        if os.name == "posix":
            if platform.system() == "Darwin":
                return "/dev/tty.usbserial-" + str(portNum)
            return "/dev/ttyUSB" + str(portNum)
        return None

    def _open_serial(self, path: str, baudrate: int) -> bool:
        """1つのパスを開く。成否だけ返す（例外はここで塞ぐ）。"""
        # connecting to はファイル側のみ。成功時の GUI 表示は
        # Window.activateSerial が行うため重複させない。
        self._logger.info(f"connecting to {path}({baudrate})")
        try:
            self.ser = serial.Serial(
                path,
                baudrate,
                timeout=READ_TIMEOUT,
                write_timeout=WRITE_TIMEOUT,
            )
            return True
        except (OSError, serial.SerialException, ValueError) as e:
            # 開失敗は利用者の次の行動が変わる重大事なので GUI＋ファイルの両方。
            print("COM Port: can't be established")
            self._logger.error(f"COM Port: can't be established: {e}")
            return False

    def open(
        self,
        portNum: int,
        portName: str = "",
        baudrate: int = 9600,
        **extra: Any,
    ) -> bool:
        # extra は将来の接続方式のための余白。TextSerial 系は使わない。
        _ = extra
        # baudrate は StringVar 由来の str が渡ることがあるため int に正規化。
        # 失敗時は落とさず既定間隔のまま False を返す（mac/linux でも落ちない）。
        try:
            baudrate = int(baudrate)
        except (TypeError, ValueError) as e:
            print("COM Port: can't be established")
            self._logger.error(f"Baud rate が不正です: {e}")
            self._send_interval = MIN_SEND_INTERVAL
            return False
        # 間引き幅は速度で決まる。ポートを開く前に決めておけば、
        # 開けなかった場合も次の接続まで前回の値が残らない。
        self._send_interval = self.calc_send_interval(baudrate)

        if portName is not None and portName != "":
            opened = self._open_serial(portName, baudrate)
        else:
            path = self._default_port_path(portNum)
            if path is None:
                # 未知OSでも portName 指定があれば上へ流れて開けに行く。
                # ここへ来るのは portName 無しの場合のみ。
                print("Not supported OS")
                self._logger.warning(
                    "Not supported OS: portName を直接指定してください"
                )
                return False
            opened = self._open_serial(path, baudrate)
        if opened:
            self.start_rx_pump()
        return opened

    def close(self) -> None:
        # 読みポンプを先に止める。閉じたポートへ読みに行かせない。
        # ポンプは書き錠を取らないので、ここで待っても詰まらない。
        self.stop_rx_pump()
        # 切断の一連を1つの区切りとして守る。閉じている最中に別スレッドが
        # send_row を呼ぶと、閉じたポートへ書き込むことになる。
        with self._lock:
            # 間引きで保留したままの行があれば先に送る（中途半端な状態で
            # 切断すると、その姿勢のまま残ってしまう）
            self.flush_pending()
            if self.ser is None:
                return
            self.ser.close()

    def is_open(self) -> bool:
        """回線が開いているか。新しい pySerial の is_open 属性を優先する。"""
        if self.ser is None:
            return False
        prop = getattr(self.ser, "is_open", None)
        if isinstance(prop, bool):
            return prop
        legacy = getattr(self.ser, "isOpen", None)
        if callable(legacy):
            try:
                return bool(legacy())
            except Exception as e:
                # 判定不能時は閉じている扱い。ファイル側にだけ残す。
                self._logger.debug(f"is_open 判定に失敗: {e!r}")
                return False
        return False

    # -- 送信 ---------------------------------------------------------------

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        """テキスト1行をシリアルへ送る。

        9600bps では1文字あたり約1ms、'3 8 80 80 80 80\\r\\n' の18文字で
        約19ms かかる。スティック操作のように高頻度で呼ばれると送信
        バッファに積み上がり、操作が数百ms遅れて効く「効きが悪い」
        状態になる。そこで最小送信間隔を設け、間隔内に来た行は送らずに
        保留し、後から来た行で上書きする (coalescing)。スティックは
        「最後の値」だけ届けば十分なので、途中の行を捨ててもよい。

        ただし取りこぼしてはいけない行がある。ボタンの押下・解放は
        捨てると押しっぱなしになるため、ボタン/Hat が変化した行は
        間隔を無視して必ず送る（判定は _coalescable が行う）。
        """
        with self._lock:
            now = time.perf_counter()
            if self._coalescable(row) and now - self._last_write < self._send_interval:
                # まだ送らない。保留しておき、次の送信機会か flush で出す
                self._pending = row
                return

            # 保留中の行があれば先に送る。送信行は「変化したスティックだけ」を
            # 含む可変長形式なので、捨てると倒した姿勢がどこにも届かなくなる。
            pending, self._pending = self._pending, None
            if pending is not None:
                self._write(pending, measure_perf=False)

            self._write(row, measure_perf)

    def _coalescable(self, row: str) -> bool:
        """間引いてよい行か（＝スティックだけが変わった行か）を判定する。

        送信行の形式は "btn hat [lx ly] [rx ry]"。先頭2トークン
        （ボタンのビット列と Hat）が前回と同じなら、変化したのは
        スティックだけなので途中を捨ててよい。
        """
        prev = self._before
        if prev is None:
            return False
        try:
            return row.split(" ")[:2] == prev.split(" ")[:2]
        except AttributeError:
            return False

    def flush_pending(self) -> None:
        """間引きで保留した行があれば送る。

        「最後に少しだけ倒した」状態が送られずに残ると、操作が中途半端
        なまま止まる。GUI が離した時や切断時など、区切りで呼ぶ。
        """
        with self._lock:
            row, self._pending = self._pending, None
            if row is not None:
                self._write(row, measure_perf=True)

    def _notify(self, row: str) -> None:
        """聞き手へ配る。聞き手が落ちても送信は止めない。

        ログのために Switch の操作が止まってはいけない。
        ただし黙って消さない。同じ相手の苦情は1度だけファイル側へ出す。
        GUI（print）には出さない。入力ログの受け取り失敗で利用者の
        次の行動は変わらないため。
        """
        for func in list(self.listeners):
            try:
                func(row)
            except Exception as e:
                key = id(func)
                if key not in self._listener_ng:
                    self._listener_ng.add(key)
                    self._logger.warning(
                        f"送信行の受け取りで例外が出ました（以後は黙ります）: {e!r}"
                    )

    def _write(self, row: str, measure_perf: bool = True) -> None:
        """実際にシリアルへ書き出す。

        フックの呼び出しから measure_perf の条件を外し、在れば必ず呼ぶ形にした。
        理由: 間引きで保留された行（send_row が measure_perf=False で
          送る分）こそ最も遅れるのに、そこだけ計測されていなかった。
          このまま応答遅延を測ると、遅い行が統計から丸ごと抜けて
            「速い」という誤った結果が出る。
        measure_perf 引数は残す。外部（Sender.writeRow /
          writeRow_wo_perf_counter）が渡しており、消すと壊れる。
          意味は「画面へ表示してよいか」へ寄せ、フックへ渡す。
          何を測るかと、何を見せるかは別の話なので分ける。
        """
        ok = False
        try:
            if self._on_write_begin is not None:
                self._on_write_begin(row, measure_perf)

            # 送る文字列は ASCII 固定なので utf-8 経由より安く作れる
            # 開く前の線は None のままである。その場合の AttributeError は
            #   下の except で「開いていない線」として扱う（従来どおり）。
            ser = cast("serial.Serial", self.ser)
            ser.write(row.encode("ascii") + b"\r\n")
            self._last_write = time.perf_counter()
            ok = True

            if self._on_write_end is not None:
                self._on_write_end(row, measure_perf)
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
            # 前回(_before)は送れたときだけ進める。送れなかった行を
            # 前回にすると、次に来たスティック行が「変わっていない」と
            # 誤判定され、間引きで捨てられる。送れていないのに捨てるのが
            # 最悪なので、失敗時は基準を動かさない。
            if ok:
                self._before = row

        # 送った行だけを配る。間引きで送らなかった行は Switch にも届いて
        # いないので、記録に残すとログと実機の挙動がずれる。送信の成否は
        # 問わない（送れなかった操作が記録から消えると切り分けができない）。
        self._notify(row)


class PicoUartTransport(TextSerialTransport):
    """Pico 用。フルステートの S 行をシリアルへ送出する。

    繋ぎ方は2通りあり、この層から見ると どちらも同じ「COM ポート」である:

      ①有線: PC → USBシリアル変換器 → Pico の UART → Switch
          Pico の USB は Switch が占有するため、PC とは繋げない。
          そこで FT232 などの変換器を挟む。115200bps。

      ②無線（pico-wakecon）: PC → Pico の USB(CDC) → Bluetooth → Switch
          無線化で Pico の USB が空いたので、PC と直結できる。
          変換器が要らず、線が1本になる。

    どちらも送る中身は同じ S 行なので、この実装を分ける必要は無い。
      違うのは「どの COM 番号か」だけで、それは利用者が選ぶ。

    注意: Q/R の時刻付きキューを持つのは pico_firmware の Pico だけ
      であり、pico-wakecon は持たない。Q へ回すかは Sender が応答で
      確かめるため、ここで相手を選ぶ必要は無い。
    """

    name = "pico_uart"
    capability = PICO_LIVE_STATE

    def add_listener(self, func: Callable[[str], None]) -> bool:
        # 125 Hz の送信行と keepalive を入力ログに流さない。
        return False

    def remove_listener(self, func: Callable[[str], None]) -> None:
        pass

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        # mailbox で最新状態に畳んでいるため、ここでは間引かない。
        with self._lock:
            self._write(row, measure_perf)
