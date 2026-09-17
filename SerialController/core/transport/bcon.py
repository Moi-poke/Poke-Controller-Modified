#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon.py - bconバイナリをpyserialで送る実装（線そのもの）。

区画は4つ。姿勢受付・フレーム化SEQ・送信ループ・受信パーサ。
Phase 2では送信ループ区画だけを別プロセスへ移設する。
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from logging import DEBUG, NullHandler, getLogger
from typing import Any

import serial
from core.transport.base import BCON_STATE, Transport
from core.transport.bcon_protocol import (
    BAUD_TABLE,
    DEFAULT_BAUD_INDEX,
    T_STATE,
    BconParser,
    frame_build,
)

BCON_DEFAULT_BAUDRATE = BAUD_TABLE[DEFAULT_BAUD_INDEX]
BCON_READ_TIMEOUT = 0.05
BCON_WRITE_TIMEOUT = 0.2

# 受信フレームの中身（種別・荷物・連番）。SEQは方向別mod256。
BconFrame = tuple[int, bytes, int]


def _invert_y_u16(value: int) -> int:
    """u16域のYだけを反転する。中立0x0800は保つ。

    式は `4096 - v` の4095止め（Picoのpackと同一式）。中立は
    `4096 - 0x0800 == 0x0800` で動かない。`0xFFF - v` にすると
    中立が0x07FFへずれるため使わない。
    """
    inv = 4096 - int(value)
    if inv > 0xFFF:
        return 0xFFF
    if inv < 0:
        return 0
    return inv


class BconTransport(Transport):
    """bcon用。STATEバイナリを送りSTATUS/PONG/ACKを読む。"""

    name = "bcon"
    capability = BCON_STATE

    def __init__(self, logger: Any = None, use_len12: bool = False) -> None:
        self.ser: Any = None
        self._logger = logger if logger is not None else getLogger(__name__)
        if logger is None:
            self._logger.addHandler(NullHandler())
            self._logger.setLevel(DEBUG)
            self._logger.propagate = True
        self._lock = threading.RLock()
        self._tx_seq = 0
        self.use_len12 = bool(use_len12)
        self._on_write_begin: Callable[..., None] | None = None
        self._on_write_end: Callable[..., None] | None = None
        self._rx_lock = threading.Lock()
        self._rx_subs: list[Callable[[BconFrame], None]] = []
        self._rx_text_subs: list[Callable[[str], None]] = []
        self._rx_waiters: list[
            tuple[tuple[int, ...], threading.Event, dict[str, Any]]
        ] = []
        self._parser = BconParser()
        self._rx_thread: threading.Thread | None = None
        self._rx_stop = threading.Event()
        self._last_tx = 0.0

    def open(
        self,
        portNum: int,
        portName: str = "",
        baudrate: int = BCON_DEFAULT_BAUDRATE,
        **extra: Any,
    ) -> bool:
        """線を開く。既定1Mbps。不正・不良はFalse＋logで落とさない。"""
        _ = extra
        try:
            rate = int(baudrate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            self._logger.warning(f"bconのbaud指定が不正です: {baudrate!r}")
            return False
        if rate <= 0:
            self._logger.warning(f"bconのbaud指定が不正です: {baudrate!r}")
            return False
        name = portName.strip() if isinstance(portName, str) else ""
        if not name:
            try:
                num = int(portNum)
            except (TypeError, ValueError):
                return False
            if num < 0:
                return False
            if os.name == "nt":
                name = f"COM{num + 1}"
            else:
                name = f"/dev/ttyUSB{num}"
        try:
            ser = serial.Serial(
                port=name,
                baudrate=rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=BCON_READ_TIMEOUT,
                write_timeout=BCON_WRITE_TIMEOUT,
            )
        except Exception as e:
            self._logger.warning(f"bconを開けませんでした({name} {rate}bps): {e!r}")
            return False
        with self._lock:
            self.ser = ser
        return True

    def close(self) -> None:
        """線を閉じる。読みポンプを先に止め、閉じるのは錠の外で行う。"""
        self.stop_rx_pump()
        with self._lock:
            ser, self.ser = self.ser, None
        if ser is None:
            return
        try:
            ser.close()
        except Exception as e:
            self._logger.debug(f"bconの close に失敗: {e!r}")

    def is_open(self) -> bool:
        """開いているか。pySerialのis_open属性を優先する。"""
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
                self._logger.debug(f"is_open 判定に失敗: {e!r}")
                return False
        return False

    # -- 第1区画:姿勢受付（行→中間姿勢。Phase 2でも親側に残る）--

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        """1行ぶんの姿勢をSTATEバイナリ1発で送る。

        行→中間姿勢（mapperはY 1:1）→ここでY反転1回→LEN8/12
        →`frame_build`→単一`write()`。錠はSEQ採番だけに使い、
        `write`は外で行う（TextSerialTransportと同一規律）。
        不正行・未開線・書込失敗は落とさず捨てる。
        """
        # 写像は関数内で読む。頂点で読むと循環する（transport初期化中→
        # registry→bcon→serial包→sender→Transport口→transport未完）。
        # 呼び出し時には初期化が済んでいるためここでは安全に読める。
        from core.serial.bcon_mapping import (
            state_to_len8,
            state_to_len12,
            wire_row_to_state,
        )

        try:
            state = wire_row_to_state(row)
        except ValueError as e:
            self._logger.warning(f"bconへ送れない行のため捨てます: {e}")
            return
        except Exception as e:
            self._logger.warning(f"bconの行変換に失敗したため捨てます: {e!r}")
            return
        # Y反転はここ1箇所。mapperは1:1、Picoは値をそのままpackする。
        try:
            state["ly"] = _invert_y_u16(state["ly"])
            state["ry"] = _invert_y_u16(state["ry"])
        except (KeyError, TypeError, ValueError) as e:
            self._logger.warning(f"bconのY反転に失敗したため捨てます: {e!r}")
            return
        if self.use_len12:
            payload = state_to_len12(state)
        else:
            payload = state_to_len8(state)
        with self._lock:
            seq = self._tx_seq
            self._tx_seq = (self._tx_seq + 1) & 0xFF
            ser = self.ser
            begin = self._on_write_begin
            end = self._on_write_end
        if ser is None:
            self._logger.debug("bconは開いていないため送りません")
            return
        frame = frame_build(T_STATE, payload, seq)
        self._write_frame(frame, row, measure_perf, begin, end, ser)

    def flush_pending(self) -> None:
        """保留があれば送り切る。骨格では間引きを持たないため何もしない。"""

    # -- 第2区画:フレーム化・SEQ（Phase 2でも境界は同じ）--

    def _take_seq(self) -> int:
        """送信用SEQを1つ採番する（方向別mod256）。"""
        with self._lock:
            seq = self._tx_seq
            self._tx_seq = (self._tx_seq + 1) & 0xFF
            return seq

    # -- 第3区画:送信ループ（単一write。Phase 2ではここだけ別過程へ）--

    def _write_frame(
        self,
        frame: bytes,
        row: str,
        measure_perf: bool,
        begin: Callable[..., None] | None,
        end: Callable[..., None] | None,
        ser: Any,
    ) -> None:
        """1フレームを1回のwriteで出す（錠を持たない）。"""
        if begin is not None:
            try:
                begin(row, measure_perf)
            except Exception:
                self._logger.debug("書き出し前フックで例外", exc_info=True)
        ok = False
        try:
            ser.write(frame)
            ok = True
            if end is not None:
                try:
                    end(row, measure_perf)
                except Exception:
                    self._logger.debug("書き出し後フックで例外", exc_info=True)
        except serial.SerialTimeoutException as e:
            print("Serial write timeout")
            self._logger.error(f"Serial write timeout: {e}")
        except serial.serialutil.SerialException as e:
            print(e)
            self._logger.error(f"Error : {e}")
        except AttributeError as e:
            print("Using a port that is not open.")
            self._logger.error(f"Maybe Using a port that is not open.: {e}")
        except Exception as e:
            self._logger.error(f"bconの書込に失敗: {e!r}")
        finally:
            if ok:
                with self._lock:
                    self._last_tx = time.perf_counter()

    # -- 聞き手（入力ログ用。バイナリは繋げない）--

    def add_listener(self, func: Callable[[str], None]) -> bool:
        """送信行を受け取る相手を足す。bconは繋げないためFalse。

        バイナリ輸送は送信行（文字列）を作らない。黙って繋いだ振りを
        すると入力ログが空のまま動いている扱いになる（静かに壊れる型）。
        PicoUartと同様にFalseを返し、呼び出し側が理由を出せるようにする。
        """
        _ = func
        return False

    def remove_listener(self, func: Callable[[str], None]) -> None:
        """聞き手を外す。繋いでいないため何もしない。"""
        _ = func

    # -- 第4区画:受信パーサ（RXポンプ・TYPE購読。Task 4でHELLOを載せる）--

    def subscribe_rx(self, func: Callable[[BconFrame], None]) -> Callable[[], None]:
        """受信フレームごとの購読。戻り値は解除用の呼び出し。

        ポンプが死んでいたら起こし直す。止めた覚えがないのに止まって
        いるのは故障であり、購読し直すときに直すのがいちばん早い。
        """
        if not self.rx_pump_running():
            if self.ser is not None and not self._rx_stop.is_set():
                try:
                    self.start_rx_pump()
                except Exception:
                    self._logger.debug("rx ポンプの再起動に失敗", exc_info=True)
        with self._rx_lock:
            if func not in self._rx_subs:
                self._rx_subs.append(func)

        def _unsub() -> None:
            with self._rx_lock:
                if func in self._rx_subs:
                    self._rx_subs.remove(func)

        return _unsub

    def wait_rx(
        self,
        wanted_types: int | tuple[int, ...],
        timeout: float = 0.5,
    ) -> BconFrame | None:
        """TYPE一致した最初の1フレームを待つ。見つかれば返す。

        基底のprefix前方一致ではなくフレームTYPE一致で待つ（バイナリに
        行が無いため）。送る前に待ちを登録してから送ること。送ってから
        登録するとポンプが先に読んで届かない（Task 4のhello/ping用）。
        """
        want: tuple[int, ...]
        if isinstance(wanted_types, int):
            want = (wanted_types & 0xFF,)
        else:
            try:
                want = tuple(int(t) & 0xFF for t in wanted_types)
            except TypeError:
                return None
        try:
            raw = float(timeout)
        except (TypeError, ValueError):
            return None
        if math.isnan(raw):
            limit = 0.0
        elif math.isinf(raw):
            limit = 5.0
        else:
            limit = max(0.0, min(raw, 5.0))
        if not self.rx_pump_running():
            if self.ser is None or self._rx_stop.is_set():
                return None
            try:
                if not self.start_rx_pump():
                    return None
            except Exception:
                self._logger.debug("rx ポンプの再起動に失敗", exc_info=True)
                return None
            if not self.rx_pump_running():
                return None
        found: dict[str, Any] = {}
        done = threading.Event()
        entry = (want, done, found)
        with self._rx_lock:
            self._rx_waiters.append(entry)
        try:
            if not done.wait(limit):
                return None
            frame = found.get("frame")
            if frame is None:
                return None
            return frame
        finally:
            with self._rx_lock:
                if entry in self._rx_waiters:
                    self._rx_waiters.remove(entry)

    def rx_pump_running(self) -> bool:
        """読みポンプが動いているか（読むだけ）。"""
        thread = self._rx_thread
        return thread is not None and thread.is_alive()

    def start_rx_pump(self) -> bool:
        """読みポンプを1本だけ起動する。動いていれば True。

        確認と起動を同じ錠の中で行う。二重起動で線が2本になると、
        古い方が止められず漏れる。
        """
        with self._rx_lock:
            thread = self._rx_thread
            if thread is not None and thread.is_alive():
                return True
            if self.ser is None:
                return False
            self._rx_stop.clear()
            thread = threading.Thread(
                target=self._rx_loop, name="BconRxPump", daemon=True
            )
            self._rx_thread = thread
            thread.start()
            return True

    def stop_rx_pump(self) -> None:
        """読みポンプを止める。止まるまで待つ（読みタイムアウトが上限）。

        待ちのあいだは錠を持たない。持ったまま join すると、起動側が
        止まるまで待たされる。
        """
        self._rx_stop.set()
        with self._rx_lock:
            thread = self._rx_thread
        if thread is None:
            return
        thread.join(BCON_READ_TIMEOUT + 0.5)
        with self._rx_lock:
            if self._rx_thread is thread and not thread.is_alive():
                self._rx_thread = None

    def _dispatch_rx(self, frame: BconFrame) -> None:
        """1フレームを購読者と待機中の wait_rx へ届ける。"""
        with self._rx_lock:
            subs = list(self._rx_subs)
            waiters = list(self._rx_waiters)
        for func in subs:
            try:
                func(frame)
            except Exception:
                self._logger.debug("rx 購読者で例外", exc_info=True)
        for want, done, found in waiters:
            try:
                if frame[0] in want:
                    found.setdefault("frame", frame)
                    done.set()
            except Exception:
                self._logger.debug("rx 待機の照合で例外", exc_info=True)

    def _rx_loop(self) -> None:
        """受信を読み続け、確定分を分配する。例外では落ちない。

        読み口はここへ一本化する。2か所で read すると横取りが起きる。
        """
        while not self._rx_stop.is_set():
            ser = self.ser
            if ser is None:
                break
            try:
                chunk = ser.read(64)
            except Exception:
                self._logger.warning(
                    "rx 読み取りで例外のためポンプを止めます", exc_info=True
                )
                break
            if not chunk:
                continue
            try:
                frames = self._parser.feed(bytes(chunk))
            except Exception:
                self._logger.debug("rx パーサで例外", exc_info=True)
                continue
            for frame in frames:
                self._dispatch_rx(frame)
