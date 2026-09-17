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
    PROTO_VER,
    RESULT_DOWNGRADED,
    RESULT_OK,
    RESULT_UNSUPPORTED,
    T_BAUD_SET,
    T_HELLO,
    T_HELLO_ACK,
    T_NEUTRAL,
    T_PING,
    T_PLAYER_INFO,
    T_PONG,
    T_STATE,
    T_STATUS,
    T_STATUS_REQ,
    T_WIRED_MODE,
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
        self._rx_waiters: list[
            tuple[tuple[int, ...], threading.Event, dict[str, Any]]
        ] = []
        self._parser = BconParser()
        self._rx_thread: threading.Thread | None = None
        self._rx_stop = threading.Event()
        self._last_tx = 0.0
        # -- 会話層（Task 4: HELLO/PING/STATUS・baud hunt）の保持 --
        # 直近STATUS（flags/last_seq/err_crc/err_drop/errcode）。錠は_lock。
        self._last_status: dict[str, int] = {
            "flags": 0,
            "last_seq": 0,
            "err_crc": 0,
            "err_drop": 0,
            "errcode": 0,
        }
        self._last_status_time = 0.0
        self._last_player_info = b""
        self._last_player_time = 0.0
        # 直近HELLO_ACK（adopted/major/minor/result）と版表示用。
        self._last_hello_ack: dict[str, int] = {
            "adopted": 0,
            "major": 0,
            "minor": 0,
            "result": 0,
        }
        self._last_hello_time = 0.0
        self._hello_version: tuple[int, int] | None = None
        # baud huntのlast-good先頭用。hello成功で更新する。
        self._last_good_baud = BCON_DEFAULT_BAUDRATE
        # hunt中のSTATE抑え。Trueのあいだsend_rowは捨てる（HELLO等の
        # 会話は通す）。huntの出入口で立ててlock・失敗のどちらでも倒す。
        self._tx_hold = False
        # 受信SEQ（Pico→PC方向）の欠番計数。初回は数えない。
        self._rx_last_seq: int | None = None
        self._rx_seq_gap = 0
        # ポンプが読んだ直近フレームの記録（TYPE→(frame, 時刻)）。
        # 購読者・待機への分配とは別に残す。握手の成否判定には使わない。
        self._last_frame_by_type: dict[int, tuple[BconFrame, float]] = {}

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
            old = self.ser
            self.ser = ser
            # 開き直しで古い欠片が残ると次回の先頭整列を乱すため捨てる。
            self._parser = BconParser()
        if old is not None:
            try:
                old.close()
            except Exception as e:
                self._logger.debug(f"bconの古い線の close に失敗: {e!r}")
        return True

    def close(self) -> None:
        """線を閉じる。読みポンプを先に止め、閉じるのは錠の外で行う。"""
        self.stop_rx_pump()
        with self._lock:
            ser, self.ser = self.ser, None
            # 欠片を持ち越すと次回接続の先頭整列を乱すため捨てる。
            self._parser = BconParser()
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

    def get_raw_serial(self) -> None:
        """生のシリアルは渡さない。bconはバイナリ専用のため直読み禁止。

        基底の既定は所持のserを返すが、bconの口をWakeLinkのテキスト
        直読み（CRLF区切り）へ渡すと応答の取り違え・化けの元になる。
        Noneを返すとWakeLinkは例外なく失敗扱い（False/空）で返し、
        bcon側の読みはsubscribe_rx/wait_rx経由へ寄る。
        """
        return None

    # -- 第1区画:姿勢受付（行→中間姿勢。Phase 2でも親側に残る）--

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        """1行ぶんの姿勢をSTATEバイナリ1発で送る。

        行→中間姿勢（mapperはY 1:1）→ここでY反転1回→LEN8/12
        →`frame_build`→単一`write()`。錠はSEQ採番だけに使い、
        `write`は外で行う（TextSerialTransportと同一規律）。
        不正行・未開線・書込失敗は落とさず捨てる。baud hunt中は
        抑えが立っているため捨てる（会話フレームは別口で通す）。
        """
        with self._lock:
            held = self._tx_hold
        if held:
            self._logger.debug("bconはbaud hunt中のためSTATE送出を抑えます")
            return
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

    def _describe_errcode(self, errcode: int) -> str:
        """STATUS errcodeの日本語説明。CONFIG拒否は0x10+(TYPE&0x0F)で解く。"""
        table = {
            0x00: "正常",
            0x01: "LEN不正",
            0x02: "CRC不一致",
            0x03: "SEQ欠番",
            0x04: "未対応",
            0x05: "UART overrun",
            0x06: "overflow",
        }
        if errcode in table:
            return table[errcode]
        if 0x10 <= errcode <= 0x1F:
            return f"CONFIG拒否(0x10+(TYPE&0x0F)=0x{errcode:02X})"
        return f"不明(0x{errcode:02X})"

    def _note_rx_seq(self, seq: int) -> None:
        """Pico→PC方向のSEQ欠番を数える。初回は数えない。落とさない。"""
        try:
            seq = int(seq) & 0xFF
        except (TypeError, ValueError):
            return
        try:
            with self._lock:
                last = self._rx_last_seq
                if last is None:
                    self._rx_last_seq = seq
                    return
                if seq != ((last + 1) & 0xFF):
                    self._rx_seq_gap += 1
                self._rx_last_seq = seq
        except Exception:
            self._logger.debug("rx SEQ計数で例外", exc_info=True)

    def _note_status_frame(self, payload: bytes) -> None:
        """STATUS payloadを直近保持へ写す。errcode変化だけ可視化する。"""
        if len(payload) != 7:
            self._logger.debug(f"STATUS長が不正のため捨てます: {len(payload)}")
            return
        try:
            flags = payload[0]
            last_seq = payload[1]
            err_crc = int.from_bytes(payload[2:4], "little")
            err_drop = int.from_bytes(payload[4:6], "little")
            errcode = payload[6]
        except Exception:
            self._logger.debug("STATUS解釈で例外", exc_info=True)
            return
        try:
            with self._lock:
                old = int(self._last_status.get("errcode", 0))
                self._last_status = {
                    "flags": int(flags) & 0xFF,
                    "last_seq": int(last_seq) & 0xFF,
                    "err_crc": int(err_crc) & 0xFFFF,
                    "err_drop": int(err_drop) & 0xFFFF,
                    "errcode": int(errcode) & 0xFF,
                }
                self._last_status_time = time.perf_counter()
        except Exception:
            self._logger.debug("STATUS保持で例外", exc_info=True)
            return
        if errcode != 0 and errcode != old:
            msg = (
                "bconのSTATUS異常を検出しました"
                f"(errcode=0x{errcode:02X} {self._describe_errcode(errcode)}"
                f" flags=0x{flags:02X} err_crc={err_crc} err_drop={err_drop})。"
                "正常復帰で0へ戻ります。"
            )
            print(msg)
            self._logger.warning(msg)

    def _dispatch_rx(self, frame: BconFrame) -> None:
        """1フレームを購読者と待機中の wait_rx へ届ける。

        届ける前に会話層の保持（直近TYPE・STATUS・HELLO_ACK・RX SEQ）を
        更新する。購読者・待機への配送は錠の外で行い、1件の失敗では
        落とさない。読み口は_rx_loopへ一本化し、ここではreadしない。
        """
        try:
            ftype = int(frame[0]) & 0xFF
        except (TypeError, ValueError, IndexError):
            ftype = -1
        try:
            now = time.perf_counter()
            with self._lock:
                if 0 <= ftype <= 0xFF:
                    self._last_frame_by_type[ftype] = (frame, now)
                if ftype == T_HELLO_ACK:
                    try:
                        payload = frame[1]
                        if len(payload) == 4:
                            self._last_hello_ack = {
                                "adopted": int(payload[0]) & 0xFF,
                                "major": int(payload[1]) & 0xFF,
                                "minor": int(payload[2]) & 0xFF,
                                "result": int(payload[3]) & 0xFF,
                            }
                            self._last_hello_time = now
                            adopted = int(payload[0]) & 0xFF
                            if adopted == PROTO_VER:
                                try:
                                    self._hello_version = (
                                        int(payload[1]) & 0xFF,
                                        int(payload[2]) & 0xFF,
                                    )
                                except (TypeError, ValueError, IndexError):
                                    pass
                    except Exception:
                        self._logger.debug("HELLO_ACK保持で例外", exc_info=True)
                elif ftype == T_PLAYER_INFO:
                    try:
                        self._last_player_info = bytes(frame[1])
                        self._last_player_time = now
                    except Exception:
                        self._logger.debug("PLAYER_INFO保持で例外", exc_info=True)
        except Exception:
            self._logger.debug("rx直近保持で例外", exc_info=True)
        try:
            self._note_rx_seq(frame[2])
        except Exception:
            self._logger.debug("rx SEQ記録で例外", exc_info=True)
        if ftype == T_STATUS:
            try:
                self._note_status_frame(frame[1])
            except Exception:
                self._logger.debug("STATUS記録で例外", exc_info=True)
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

    # -- 会話層:HELLO/PING/STATUS/BAUD（Task 4・無音ハング防止）--

    def _send_session_frame(self, type_: int, payload: bytes, row_note: str) -> bool:
        """会話フレームを1発で送る。SEQ採番だけ錠内、writeは外。"""
        try:
            seq = self._take_seq()
            frame = frame_build(int(type_) & 0xFF, bytes(payload), seq)
        except ValueError as e:
            self._logger.warning(f"bconの会話フレームが組めません: {e}")
            return False
        except Exception as e:
            self._logger.warning(f"bconの会話フレーム組立に失敗: {e!r}")
            return False
        with self._lock:
            ser = self.ser
            begin = self._on_write_begin
            end = self._on_write_end
        if ser is None:
            self._logger.debug("bconは開いていないため会話を送りません")
            return False
        before = (
            len(getattr(ser, "written", []) or []) if hasattr(ser, "written") else -1
        )
        try:
            self._write_frame(frame, row_note, False, begin, end, ser)
        except Exception as e:
            self._logger.debug(f"bconの会話送出で例外: {e!r}")
            return False
        if hasattr(ser, "written"):
            try:
                after = len(ser.written or [])
                return after != before
            except Exception:
                return True
        return True

    def _current_baud(self) -> int | None:
        """現在のbaud率。取れなければNone（落とさない）。"""
        try:
            ser = self.ser
            if ser is None:
                return None
            rate = getattr(ser, "baudrate", None)
            if rate is None:
                return None
            return int(rate)
        except (TypeError, ValueError):
            return None
        except Exception:
            self._logger.debug("baud取得で例外", exc_info=True)
            return None

    def _set_baud(self, rate: int) -> bool:
        """host側baudを切り替える。失敗はFalse（落とさない）。"""
        try:
            ser = self.ser
            if ser is None:
                return False
            ser.baudrate = int(rate)
            return True
        except (TypeError, ValueError, AttributeError) as e:
            self._logger.debug(f"baud切替に失敗: {e!r}")
            return False
        except Exception as e:
            self._logger.debug(f"baud切替で例外: {e!r}")
            return False

    def _send_neutral_once(self) -> None:
        """NEUTRALを1発だけ送る（UNSUPPORTED時の維持用）。失敗は捨てる。"""
        try:
            self._send_session_frame(T_NEUTRAL, b"", "bcon:NEUTRAL")
        except Exception:
            self._logger.debug("NEUTRAL送出で例外", exc_info=True)

    def hello(self, timeout: float = 3.0) -> bool:
        """HELLO(ver=4)を送りHELLO_ACKを確認する。失敗はFalse＋可視log。

        送る前に待ちを登録してから送る（送ってから登録するとポンプが
        先に読んで届かない）。HELLO_ACKの`[adopted, major, minor, RESULT]`
        を見て`adopted==0x04 and RESULT==0x00`ならTrue。DOWNGRADEDを含む
        非OKはNEUTRALを1発保ち、版を表示してFalseで止める（PCはver=4
        専用のため送り続けハングにしない）。ACK不在は期限まで再HELLOし、
        尽きたら再huntへの案内を出してFalse。例外は投げない。
        直近保持は購読者・待機への分配記録に使い、成功の代用にはしない。
        握手は必ず往復で行う。
        """
        try:
            try:
                limit = float(timeout)
            except (TypeError, ValueError):
                self._logger.warning(f"bconのhello待ちが不正です: {timeout!r}")
                return False
            if math.isnan(limit):
                limit = 0.0
            elif math.isinf(limit):
                limit = 10.0
            else:
                limit = max(0.0, min(limit, 10.0))
            if self.ser is None:
                msg = "bconが開いていないためHELLOを送りません。"
                print(msg)
                self._logger.warning(msg)
                return False
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためHELLOを送りません。"
                        print(msg)
                        self._logger.warning(msg)
                        return False
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためHELLOを送りません。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
            if limit <= 0.0:
                msg = "bconのHELLO待ちが0のため送らずFalseを返します。"
                print(msg)
                self._logger.warning(msg)
                return False
            # 握手は必ず往復で行う。直近保持の流用では送った証拠にならない。
            start = time.perf_counter()
            deadline = start + limit
            attempt = 0
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0.0:
                    break
                attempt += 1
                grain = min(0.5, remaining)
                found: dict[str, Any] = {}
                done = threading.Event()
                entry = ((T_HELLO_ACK,), done, found)
                with self._rx_lock:
                    self._rx_waiters.append(entry)
                try:
                    ok = self._send_session_frame(
                        T_HELLO, bytes([PROTO_VER, 0x01]), "bcon:HELLO"
                    )
                    if not ok:
                        msg = "bconのHELLO送出に失敗しました。"
                        print(msg)
                        self._logger.warning(msg)
                        return False
                    if not done.wait(grain):
                        continue
                    got = found.get("frame")
                    if got is None:
                        continue
                    payload = got[1]
                    if len(payload) != 4:
                        self._logger.warning(
                            f"bconのHELLO_ACK長が不正です: {len(payload)}"
                        )
                        continue
                    adopted = int(payload[0]) & 0xFF
                    major = int(payload[1]) & 0xFF
                    minor = int(payload[2]) & 0xFF
                    result = int(payload[3]) & 0xFF
                    if adopted == PROTO_VER and result == RESULT_OK:
                        with self._lock:
                            rate = self._current_baud()
                            if rate is not None:
                                self._last_good_baud = rate
                        self._logger.info(
                            f"bconのHELLOが通りました(v{major}.{minor})。"
                        )
                        return True
                    if adopted == PROTO_VER and result == RESULT_DOWNGRADED:
                        msg = (
                            "bconがDOWNGRADEDで返しました"
                            f"(v{major}.{minor})。PCはver=4専用のため"
                            "NEUTRALを保ち止めます（送り続けません）。"
                        )
                        print(msg)
                        self._logger.warning(msg)
                        self._send_neutral_once()
                        return False
                    # UNSUPPORTED・版違いはNEUTRAL維持＋版表示して止める。
                    if result == RESULT_UNSUPPORTED or adopted != PROTO_VER:
                        msg = (
                            "bconの版が合いません"
                            f"(adopted=0x{adopted:02X} v{major}.{minor} "
                            f"RESULT=0x{result:02X})。PCはver=4で送っています。"
                            "NEUTRALを保ち止めます（送り続けません）。"
                        )
                    else:
                        msg = (
                            "bconのHELLO応答が想定外です"
                            f"(adopted=0x{adopted:02X} v{major}.{minor} "
                            f"RESULT=0x{result:02X})。"
                            "NEUTRALを保ち止めます（送り続けません）。"
                        )
                    print(msg)
                    self._logger.warning(msg)
                    self._send_neutral_once()
                    return False
                finally:
                    with self._rx_lock:
                        if entry in self._rx_waiters:
                            self._rx_waiters.remove(entry)
            msg = (
                "bconのHELLO_ACKが来ませんでした"
                f"({attempt}回再送・{limit:.1f}s)。"
                "再huntへ回してください(baud_hunt)。"
            )
            print(msg)
            self._logger.warning(msg)
            return False
        except Exception as e:
            self._logger.debug(f"helloで例外: {e!r}", exc_info=True)
            return False

    def ping(self, timeout: float = 1.0) -> float | None:
        """PINGを送りPONGのSEQエコーでRTT秒を返す。失敗はNone＋可視log。

        購読登録→送出の順を守る。期限いっぱいまで1つの購読で待ち、
        PONG payload先頭が送ったSEQと一致したら往復秒を返す。違うSEQの
        PONGは無視する（掛け直しで隙間を作らない）。途中で区切らない。
        """
        try:
            try:
                limit = float(timeout)
            except (TypeError, ValueError):
                self._logger.warning(f"bconのping待ちが不正です: {timeout!r}")
                return None
            if math.isnan(limit):
                limit = 0.0
            elif math.isinf(limit):
                limit = 5.0
            else:
                limit = max(0.0, min(limit, 5.0))
            if self.ser is None:
                msg = "bconが開いていないためPINGを送りません。"
                print(msg)
                self._logger.warning(msg)
                return None
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためPINGを送りません。"
                        print(msg)
                        self._logger.warning(msg)
                        return None
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためPINGを送りません。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
            if limit <= 0.0:
                msg = "bconのPING待ちが0のため送らずNoneを返します。"
                print(msg)
                self._logger.warning(msg)
                return None
            # 購読登録→採番→送出（ポンプが先に読んでも届く順序）。
            # 期限内は1つの購読で待ち続け、SEQ一致だけを拾う。待ちの
            # 掛け直しはしない（掛け直しの隙間に落ちたPONGは戻らない）。
            matched: dict[str, Any] = {}
            done = threading.Event()

            def _on_pong(frame: BconFrame) -> None:
                try:
                    if (int(frame[0]) & 0xFF) != T_PONG:
                        return
                    want = matched.get("seq")
                    if want is None:
                        return
                    payload = frame[1]
                    if len(payload) != 1:
                        return
                    if (int(payload[0]) & 0xFF) == (int(want) & 0xFF):
                        matched["rtt"] = time.perf_counter() - float(
                            matched.get("t0", 0.0)
                        )
                        done.set()
                except Exception:
                    self._logger.debug("PONG照合で例外", exc_info=True)

            unsub = self.subscribe_rx(_on_pong)
            try:
                try:
                    sent_seq = self._take_seq()
                    frame = frame_build(T_PING, b"", sent_seq)
                except Exception as e:
                    self._logger.warning(f"bconのPING組立に失敗: {e!r}")
                    return None
                with self._lock:
                    ser = self.ser
                    begin = self._on_write_begin
                    end = self._on_write_end
                if ser is None:
                    return None
                matched["seq"] = sent_seq
                matched["t0"] = time.perf_counter()
                self._write_frame(frame, "bcon:PING", False, begin, end, ser)
                if done.wait(limit):
                    try:
                        return float(matched.get("rtt", 0.0))
                    except (TypeError, ValueError):
                        return None
            finally:
                try:
                    unsub()
                except Exception:
                    self._logger.debug("PONG購読解除で例外", exc_info=True)
            msg = f"bconのPONGが来ませんでした({limit:.1f}s)。"
            print(msg)
            self._logger.warning(msg)
            return None
        except Exception as e:
            self._logger.debug(f"pingで例外: {e!r}", exc_info=True)
            return None

    def last_status(self) -> dict[str, int]:
        """直近STATUSの写しを返す（flags/last_seq/err_crc/err_drop/errcode）。

        未受信は全0。呼ぶだけで線に触れない。例外は投げない。
        """
        try:
            with self._lock:
                return dict(self._last_status)
        except Exception:
            return {
                "flags": 0,
                "last_seq": 0,
                "err_crc": 0,
                "err_drop": 0,
                "errcode": 0,
            }

    def last_player_info(self) -> bytes:
        """直近PLAYER_INFO payloadの写し。未受信は空。例外は投げない。"""
        try:
            with self._lock:
                return bytes(self._last_player_info)
        except Exception:
            return b""

    def request_status(self, timeout: float = 1.0) -> dict[str, int] | None:
        """STATUS_REQを送り即時STATUSを待つ。PLAYER_INFO付随も受ける。

        成功はlast_status()と同形の写し、失敗はNone＋可視log。
        待ち登録→送出の順を守る。例外は投げない。
        """
        try:
            try:
                limit = float(timeout)
            except (TypeError, ValueError):
                self._logger.warning(f"bconの状態確認待ちが不正です: {timeout!r}")
                return None
            if math.isnan(limit):
                limit = 0.0
            elif math.isinf(limit):
                limit = 5.0
            else:
                limit = max(0.0, min(limit, 5.0))
            if self.ser is None:
                msg = "bconが開いていないため状態確認を送りません。"
                print(msg)
                self._logger.warning(msg)
                return None
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないため状態確認を送りません。"
                        print(msg)
                        self._logger.warning(msg)
                        return None
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したため状態確認を送りません。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
            if limit <= 0.0:
                msg = "bconの状態確認待ちが0のため送らずNoneを返します。"
                print(msg)
                self._logger.warning(msg)
                return None
            found: dict[str, Any] = {}
            done = threading.Event()
            entry = ((T_STATUS,), done, found)
            with self._rx_lock:
                self._rx_waiters.append(entry)
            try:
                ok = self._send_session_frame(T_STATUS_REQ, b"", "bcon:STATUS_REQ")
                if not ok:
                    msg = "bconの状態確認送出に失敗しました。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
                if not done.wait(limit):
                    msg = f"bconのSTATUSが来ませんでした({limit:.1f}s)。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
                return self.last_status()
            finally:
                with self._rx_lock:
                    if entry in self._rx_waiters:
                        self._rx_waiters.remove(entry)
        except Exception as e:
            self._logger.debug(f"request_statusで例外: {e!r}", exc_info=True)
            return None

    def baud_hunt(
        self,
        candidates: list[int] | tuple[int, ...] | None = None,
        timeout_per: float = 0.5,
    ) -> int | None:
        """baudをlast-good先頭＋降順sweepで探し2連続有効でlockする。

        candidates無しはBAUD表の降順（last-goodを先頭へ）。0-4の小さな
        値は表index、それ以外はbps値として読む。hunt中はSTATE送出を
        抑える（send_rowは捨てる。HELLO等の会話は通す）。lock・失敗の
        いずれでも抑えを解く。成功はbps、失敗はNone＋可視log。
        例外は投げない。BREAK受信時もここへ回す。
        """
        try:
            try:
                per = float(timeout_per)
            except (TypeError, ValueError):
                per = 0.5
            if math.isnan(per):
                per = 0.5
            elif math.isinf(per):
                per = 2.0
            else:
                per = max(0.1, min(per, 2.0))
            if self.ser is None:
                msg = "bconが開いていないためbaud huntしません。"
                print(msg)
                self._logger.warning(msg)
                return None
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためbaud huntしません。"
                        print(msg)
                        self._logger.warning(msg)
                        return None
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためbaud huntしません。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
            order: list[int] = []
            if candidates is None:
                try:
                    with self._lock:
                        last_good = int(self._last_good_baud)
                except (TypeError, ValueError):
                    last_good = BCON_DEFAULT_BAUDRATE
                all_rates = sorted(set(BAUD_TABLE.values()), reverse=True)
                order = [last_good] + [r for r in all_rates if r != last_good]
            else:
                try:
                    raw_list = list(candidates)
                except TypeError:
                    msg = f"bconのhunt候補が不正です: {candidates!r}"
                    print(msg)
                    self._logger.warning(msg)
                    return None
                for item in raw_list:
                    try:
                        value = int(item)  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        continue
                    if 0 <= value <= 4 and value in BAUD_TABLE:
                        order.append(int(BAUD_TABLE[value]))
                    elif value > 0:
                        order.append(int(value))
                seen: set[int] = set()
                dedup: list[int] = []
                for rate in order:
                    if rate not in seen:
                        dedup.append(rate)
                        seen.add(rate)
                order = dedup
                if not order:
                    msg = f"bconのhunt候補が空です: {candidates!r}"
                    print(msg)
                    self._logger.warning(msg)
                    return None
            self._logger.info(
                f"bconのbaud huntを始めます(候補={order})。STATE送出を抑えます。"
            )
            with self._lock:
                self._tx_hold = True
            try:
                for rate in order:
                    try:
                        if not self._set_baud(rate):
                            continue
                        with self._lock:
                            self._parser = BconParser()
                        if not self.hello(timeout=per):
                            continue
                        if not self.hello(timeout=per):
                            continue
                        with self._lock:
                            self._last_good_baud = int(rate)
                        msg = f"bconのbaudを{rate}bpsでlockしました(2連続有効)。"
                        print(msg)
                        self._logger.info(msg)
                        return int(rate)
                    except Exception:
                        self._logger.debug("hunt1候補で例外", exc_info=True)
                        continue
                msg = (
                    f"bconのbaud huntに失敗しました(候補={order})。"
                    "配線・電源を確認してください。"
                )
                print(msg)
                self._logger.warning(msg)
                return None
            finally:
                with self._lock:
                    self._tx_hold = False
        except Exception as e:
            self._logger.debug(f"baud_huntで例外: {e!r}", exc_info=True)
            return None

    def set_baud_index(self, index: int, timeout: float = 2.0) -> bool:
        """BAUD_SETでPicoとhostのbaudを切り替える。失敗はFalse＋可視log。

        旧レートでBAUD_SET→STATUS-ACK確認→100ms guard→双方切替→
        残り時間でadopt確認（HELLO 1往復）。adopt無しは2s目安で旧レートへ
        自動復帰する。Pico側はadopt後にBCBRへ永続保存する。例外は投げない。
        """
        try:
            try:
                idx = int(index)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                msg = f"bconのbaud指定が不正です: {index!r}"
                print(msg)
                self._logger.warning(msg)
                return False
            if idx not in BAUD_TABLE:
                msg = f"bconのbaud指定が範囲外です: {index!r}(0-4)。"
                print(msg)
                self._logger.warning(msg)
                return False
            try:
                limit = float(timeout)
            except (TypeError, ValueError):
                limit = 2.0
            if math.isnan(limit):
                limit = 2.0
            elif math.isinf(limit):
                limit = 5.0
            else:
                limit = max(0.2, min(limit, 5.0))
            if self.ser is None:
                msg = "bconが開いていないためbaud切替しません。"
                print(msg)
                self._logger.warning(msg)
                return False
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためbaud切替しません。"
                        print(msg)
                        self._logger.warning(msg)
                        return False
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためbaud切替しません。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
            old_rate = self._current_baud()
            if old_rate is None:
                msg = "bconの現在baudが取れないため切替しません。"
                print(msg)
                self._logger.warning(msg)
                return False
            new_rate = int(BAUD_TABLE[idx])
            if old_rate == new_rate:
                self._logger.info(f"bconのbaudは既に{new_rate}bpsです。")
                return True
            # 旧レートでSTATUS-ACK確認。
            found: dict[str, Any] = {}
            done = threading.Event()
            entry = ((T_STATUS,), done, found)
            with self._rx_lock:
                self._rx_waiters.append(entry)
            try:
                ok = self._send_session_frame(
                    T_BAUD_SET, bytes([idx & 0xFF]), "bcon:BAUD_SET"
                )
                if not ok:
                    msg = "bconのBAUD_SET送出に失敗しました。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
                if not done.wait(min(0.8, limit)):
                    msg = "bconのBAUD_SET応答(STATUS)が来ないため切替しません。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
            finally:
                with self._rx_lock:
                    if entry in self._rx_waiters:
                        self._rx_waiters.remove(entry)
            # 100ms guard後に双方切替。
            try:
                time.sleep(0.1)
            except Exception:
                pass
            if not self._set_baud(new_rate):
                msg = f"bconのhost側を{new_rate}bpsへ切替えられません。"
                print(msg)
                self._logger.warning(msg)
                return False
            with self._lock:
                self._parser = BconParser()
            remaining = max(0.2, limit - 0.8 - 0.1 - (0.0 if limit > 1.0 else 0.0))
            if self.hello(timeout=min(remaining, 2.0)):
                with self._lock:
                    self._last_good_baud = new_rate
                msg = (
                    f"bconのbaudを{new_rate}bpsへ切替えました"
                    "(HELLOでadopt確認。Pico側はadopt後にBCBRへ永続保存します)。"
                )
                print(msg)
                self._logger.info(msg)
                return True
            # 2s目安で自動復帰。
            try:
                self._set_baud(old_rate)
            except Exception:
                pass
            with self._lock:
                try:
                    self._parser = BconParser()
                except Exception:
                    pass
            msg = (
                f"bconの新baud({new_rate}bps)でadopt確認できないため"
                f"{old_rate}bpsへ戻しました。"
            )
            print(msg)
            self._logger.warning(msg)
            return False
        except Exception as e:
            self._logger.debug(f"set_baud_indexで例外: {e!r}", exc_info=True)
            return False

    def set_wired_mode(self, enable: bool, timeout: float = 1.0) -> bool:
        """WIRED_MODEを切り替える。失敗はFalse＋可視log。

        切替はFlash保存・約500ms後自発再起動する。再起動中のSTATE送出
        は止め、復帰後に再HELLOからやり直すこと。有線中のCAPTURE/BEACON
        要求は0x10/0x11拒否になるため、先にWIRED_MODE=0＋再起動が必要な
        旨を表示する。例外は投げない。
        """
        try:
            flag = bool(enable)
            try:
                limit = float(timeout)
            except (TypeError, ValueError):
                limit = 1.0
            if math.isnan(limit):
                limit = 1.0
            elif math.isinf(limit):
                limit = 5.0
            else:
                limit = max(0.1, min(limit, 5.0))
            if self.ser is None:
                msg = "bconが開いていないため有線切替しません。"
                print(msg)
                self._logger.warning(msg)
                return False
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないため有線切替しません。"
                        print(msg)
                        self._logger.warning(msg)
                        return False
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したため有線切替しません。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
            found: dict[str, Any] = {}
            done = threading.Event()
            entry = ((T_STATUS,), done, found)
            with self._rx_lock:
                self._rx_waiters.append(entry)
            try:
                ok = self._send_session_frame(
                    T_WIRED_MODE,
                    bytes([0x01 if flag else 0x00]),
                    "bcon:WIRED_MODE",
                )
                if not ok:
                    msg = "bconの有線切替送出に失敗しました。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
                if not done.wait(limit):
                    msg = f"bconの有線切替応答が来ませんでした({limit:.1f}s)。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
            finally:
                with self._rx_lock:
                    if entry in self._rx_waiters:
                        self._rx_waiters.remove(entry)
            if flag:
                note = (
                    "bconを有線へ切替えました。Flash保存・約500ms後自発再起動"
                    "します。再起動中のSTATE送出は止め、復帰後に再HELLOから"
                    "やり直してください。有線中のCAPTURE/BEACONは0x10/0x11"
                    "拒否になります。"
                )
            else:
                note = (
                    "bconを無線へ切替えました。Flash保存・約500ms後自発再起動"
                    "します。再起動中のSTATE送出は止め、復帰後に再HELLOから"
                    "やり直してください。復帰後はCAPTURE/BEACONが使えます。"
                )
            print(note)
            self._logger.warning(note)
            return True
        except Exception as e:
            self._logger.debug(f"set_wired_modeで例外: {e!r}", exc_info=True)
            return False
