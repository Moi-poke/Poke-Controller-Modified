#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon.py - bconバイナリをpyserialで送る実装（線そのもの）。

区画は4つ。姿勢受付・フレーム化SEQ・送信ループ・受信パーサ。
Phase 2では送信ループ区画だけを別プロセスへ移設する。
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from logging import DEBUG, NullHandler, getLogger
from typing import Any

import serial
from core.transport.base import BCON_STATE, Transport
from core.transport.bcon_protocol import (
    BAUD_TABLE,
    BOOTSEL_MAGIC,
    DEFAULT_BAUD_INDEX,
    PROTO_VER,
    RESULT_DOWNGRADED,
    RESULT_OK,
    RESULT_UNSUPPORTED,
    T_BAUD_SET,
    T_BEACON_START,
    T_BOOTSEL,
    T_COLOR_GET,
    T_COLOR_INFO,
    T_EMULATE_MODE,
    T_HELLO,
    T_HELLO_ACK,
    T_NEUTRAL,
    T_PING,
    T_PLAYER_INFO,
    T_PONG,
    T_RUMBLE,
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

# EMULATE_MODE の役割番号。Pico 側 personality と同じ並び。
# 0=Pro Controller・1=Joy-Con (L)・2=Joy-Con (R)。
EMULATE_ROLE_NAMES = ("Pro Controller", "Joy-Con (L)", "Joy-Con (R)")

# live loopの既定周期（120Hz）。Picoのtimeout-neutral 200msに対し十分
# 短く、USB 8msポーリングとも大きくは干渉しない。Phase 2で別過程へ
# 移すときも値はそのまま持っていく。
LIVE_INTERVAL_S = 1.0 / 120.0

# 姿勢空間（Keys.Button・Senderの持つ姿勢・PicoのS行）からwire空間
# （Modified行の<btn-hex>）へのbit写像。添字が姿勢bit・値がwire bit。
# bcon_mapping._WIRE_TO_VIIPERの添字に合わせる。Keys.Buttonの並び
# （Y,B,A,X,L,R,ZL,ZR,MINUS,PLUS,LCLICK,RCLICK,HOME,CAPTURE）に依存
# するため、並びを変えたらここも直すこと。14bit以降は輸送位置が
# 無いため落とす（spec §3.1のGR/GL/C/Headset落としと同一方針）。
_POSTURE_TO_WIRE = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)

# 受信フレームの中身（種別・荷物・連番）。SEQは方向別mod256。
BconFrame = tuple[int, bytes, int]


def _s_row_to_wire_row(row: str) -> str:
    """Pico式S行（姿勢空間）をModifiedのwire行へ読み替える。

    live workerは姿勢を `S btn hat lx ly rx ry`（姿勢空間・7欄）で
    渡してくる。bconの写像（wire_row_to_state）はwire空間しか読めない
    ため、ここでbtnだけをwire bitへ移し、Sを外した6欄へ直す。
    hat・stick欄は両空間で同値（hat 0-8・u8中央0x80）のため触らない。
    S行は6欄そろいのためLS/RS両旗（0x0003）を立て、lx/ly→左・
    rx/ry→右へ振る（wire側quirkのboth扱い）。欄不足・不正値は
    ValueError（呼び出し側が捨てる）。姿勢A=0x04をwireと読むと
    bit2→BTN_Yへずれるため、この直しが無いとliveのAがYになる。
    """
    parts = row.strip().split()
    if len(parts) != 7 or parts[0].upper() != "S":
        raise ValueError(f"bconへ送れないS行形式です: {row!r}")
    try:
        posture = int(parts[1], 16)
    except ValueError:
        raise ValueError(f"bconへ送れないS行形式です: {row!r}") from None
    wire = 0x0003
    bits = int(posture) & 0x3FFF
    for bit in range(14):
        if bits & (1 << bit):
            wire |= 1 << _POSTURE_TO_WIRE[bit]
    return f"{wire:x} {parts[2]} {parts[3]} {parts[4]} {parts[5]} {parts[6]}"


def _is_s_row(text: str) -> bool:
    """Pico式S行か（先頭S＋空白、またはS単独の崩れ）。wire行と紛れない。

    wireのbtn欄は16進数字のみでSを含まないため、先頭欄がSならS行と
    断定できる。小文字も受ける（encode側は大文字Sで出す）。
    """
    upper = text.upper()
    return upper.startswith("S ") or upper == "S"


def decode_rumble(payload: bytes) -> tuple[int, int]:
    """RUMBLE payload（2B: 左・右の強さ0-255）を通読用の対へ直す。

    不正（2B未満・非bytes的）は(0, 0)。例外は投げない。表示側の
    整数化だけを受け持ち、tkinterには触れない。
    """
    try:
        data = bytes(payload)
    except (TypeError, ValueError):
        return (0, 0)
    if len(data) < 2:
        return (0, 0)
    try:
        return (int(data[0]) & 0xFF, int(data[1]) & 0xFF)
    except (TypeError, ValueError, IndexError):
        return (0, 0)


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
        # 送出の錠。採番→組立→writeを1単位で包み線上のSEQ順＝採番順にする。
        # 必ず_lockの外側で取る（順序は_write_lock→_lock、逆は禁止）。
        self._write_lock = threading.Lock()
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
        self._last_rumble = b""
        self._last_rumble_time = 0.0
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
        # -- 第3区画:120Hz live loop（Task 5）の保持 --
        # 親→子は最新姿勢のみ（深さ1・上書き・backlogなし）。子→親は
        # STATUS/PONG/統計のみ。Phase 2ではこの区画だけ別過程へ移す。
        self._live_loop_thread: threading.Thread | None = None
        self._live_loop_stop = threading.Event()
        self._live_loop_event = threading.Event()
        self._live_interval_s = LIVE_INTERVAL_S
        self._live_pending: tuple[bytes, str, bool] | None = None
        self._live_latest: tuple[bytes, str] | None = None
        self._live_last_tx = 0.0
        self._live_sent = 0
        self._live_merged = 0
        self._live_dropped = 0
        self._live_rtt_ms: float | None = None
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
            # 既定パスの組み立てはTextSerial側へ一本化する（Darwin分岐の
            # 二重持ちを避ける）。未知OSのNoneはportName指定へ誘導する。
            try:
                from core.transport.text_serial import (
                    TextSerialTransport as _TextSerial,
                )

                default_path = _TextSerial._default_port_path(num)
            except Exception:
                self._logger.debug("既定パス解決に失敗", exc_info=True)
                default_path = None
            if default_path is None:
                self._logger.warning(
                    "Not supported OS: portName を直接指定してください"
                )
                return False
            name = default_path
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
            # 開き直しで古い姿勢を送り直さないよう最新保持も捨てる。
            # 数え直しのため統計も0へ戻す（live_statsの区切りはopen）。
            self._live_pending = None
            self._live_latest = None
            self._live_last_tx = 0.0
            self._live_sent = 0
            self._live_merged = 0
            self._live_dropped = 0
            self._live_rtt_ms = None
        if old is not None:
            try:
                old.close()
            except Exception as e:
                self._logger.debug(f"bconの古い線の close に失敗: {e!r}")
        return True

    def close(self) -> None:
        """線を閉じる。読みポンプを先に止め、閉じるのは錠の外で行う。

        終了時はNEUTRALを1発だけ保ってから閉じる（spec §3.1。200ms
        無音でPicoが全解放する前提で、閉じ際の押下残りを消す）。
        live loopは先に止める（止めないとNEUTRALの後に更新が載る）。
        いずれも失敗は捨てる（閉じない状態を作らない）。
        """
        try:
            self.stop_live_loop()
        except Exception:
            self._logger.debug("live loopの停止に失敗", exc_info=True)
        try:
            if self.ser is not None:
                with self._lock:
                    held = self._tx_hold
                if not held:
                    self._send_neutral_once()
        except Exception:
            self._logger.debug("終了時NEUTRALに失敗", exc_info=True)
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

        受ける行は2種。Modifiedのwire行（`<btn-hex> <hat> ...`・`end`）
        と、live workerの出すPico式S行（`S btn hat lx ly rx ry`・姿勢
        空間）である。S行はwire行へ読み替えてから写す（姿勢A=0x04を
        wireと読むとBTN_Yへずれる）。読み替え後はどちらも同じ道を通る。
        行→中間姿勢（Yは1:1のまま）→LEN8/12→`frame_build`→単一
        `write()`。Yの反転はPico側pack（4096-Y）が担う唯一の1回で、
        PC側では反転しない（wakecon経路と同一の端に載せる）。
        採番→組立→writeは_write_lockで1単位に包む（線上のSEQ順＝採番順。
        TextSerialTransportと同一規律）。
        不正行・未開線・書込失敗は落とさず捨てる。baud hunt中・baud
        切替窓は抑えが立っているため捨ててdroppedに数える（会話
        フレームは別口で通す）。live loop起動中は保留（最新1件上書き・
        上書き分はmerged）へ載せloopが送る。loop停止中はここで即送する。
        """

        with self._lock:
            held = self._tx_hold
        if held:
            with self._lock:
                self._live_dropped += 1
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
            text = row.strip() if isinstance(row, str) else ""
            if _is_s_row(text):
                state = wire_row_to_state(_s_row_to_wire_row(text))
            else:
                state = wire_row_to_state(row)
        except ValueError as e:
            self._logger.warning(f"bconへ送れない行のため捨てます: {e}")
            return
        except Exception as e:
            self._logger.warning(f"bconの行変換に失敗したため捨てます: {e!r}")
            return
        # Yは反転しない。mapperは1:1で、Pico側pack（4096-Y）が
        # 唯一の反転である。ここで反転するとwakecon経路（無反転）と
        # 逆端に載る（二重反転）。u16域のままLEN8/12へ渡す。
        if self.use_len12:
            payload = state_to_len12(state)
        else:
            payload = state_to_len8(state)
        note = row if isinstance(row, str) else ""
        with self._lock:
            thread = self._live_loop_thread
            loop_on = thread is not None and thread.is_alive()
            if loop_on:
                if self._live_pending is not None:
                    self._live_merged += 1
                self._live_pending = (payload, note, bool(measure_perf))
                event = self._live_loop_event
        if loop_on:
            try:
                event.set()
            except Exception:
                self._logger.debug("live loopの起床に失敗", exc_info=True)
            return
        # 同期送出も採番→組立→書込を1単位で包む（会話・loopとの逆転防止）。
        with self._write_lock:
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
            with self._lock:
                self._live_sent += 1
                self._live_latest = (payload, note)
                self._live_last_tx = time.perf_counter()

    def flush_pending(self) -> None:
        """保留があれば送り切る。loop起動中は起こして即送させる。"""
        try:
            if self.live_loop_running():
                self._live_loop_event.set()
        except Exception:
            self._logger.debug("flushの起床に失敗", exc_info=True)

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
    ) -> bool:
        """1フレームを1回のwriteで出す。成否を返す。

        呼び側は_write_lock保持で呼ぶ（採番→組立→書込の1単位）。
        短い書込・例外はFalse。SEQは巻き戻さない。例外は投げない。
        """
        if begin is not None:
            try:
                begin(row, measure_perf)
            except Exception:
                self._logger.debug("書き出し前フックで例外", exc_info=True)
        ok = False
        try:
            count = ser.write(frame)
            # 短い書込は失敗扱い（戻り無しは確かめようが無いため成功扱い）。
            ok = count is None or int(count) == len(frame)
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
        return ok

    # -- 第3区画:120Hz live loop（独立スレッド＋絶対時刻。Task 5）--

    def live_loop_running(self) -> bool:
        """120Hz loopが回っているか（読むだけ）。例外は投げない。"""
        try:
            thread = self._live_loop_thread
            return thread is not None and thread.is_alive()
        except Exception:
            return False

    def start_live_loop(self, interval_s: float = LIVE_INTERVAL_S) -> bool:
        """120Hz送出loopを1本だけ起こす。動いていれば True。

        確認と起動を同じ錠の中で行う。二重起動で線が2本になると、
        古い方が止められず漏れる。範囲外の周期は120Hzへ戻す。
        例外は投げない。
        """
        try:
            try:
                interval = float(interval_s)
            except (TypeError, ValueError):
                interval = LIVE_INTERVAL_S
            if math.isnan(interval) or math.isinf(interval):
                interval = LIVE_INTERVAL_S
            interval = max(0.001, min(interval, 0.2))
            with self._lock:
                thread = self._live_loop_thread
                if thread is not None and thread.is_alive():
                    return True
                self._live_interval_s = interval
                self._live_loop_stop.clear()
                self._live_loop_event.clear()
                worker = threading.Thread(
                    target=self._live_loop_run, name="BconLive120", daemon=True
                )
                self._live_loop_thread = worker
                worker.start()
                return True
        except Exception:
            self._logger.debug("live loopの起動に失敗", exc_info=True)
            return False

    def stop_live_loop(self) -> bool:
        """120Hz loopを止める。止まるまで待つ（上限0.5秒）。

        待ちのあいだは錠を持たない。持ったまま join すると、起動側が
        止まるまで待たされる。例外は投げない。
        """
        try:
            self._live_loop_stop.set()
            try:
                self._live_loop_event.set()
            except Exception:
                pass
            with self._lock:
                thread = self._live_loop_thread
            if thread is None:
                return True
            thread.join(0.5)
            alive = thread.is_alive()
            if not alive:
                with self._lock:
                    if self._live_loop_thread is thread:
                        self._live_loop_thread = None
            return not alive
        except Exception:
            self._logger.debug("live loopの停止に失敗", exc_info=True)
            return False

    def live_stats(self) -> dict[str, Any]:
        """送出統計の写しを返す（読むだけ・副作用なし）。

        sentはSTATE送出数（即送＋loop更新）。mergedはloop保留の畳み数
        （最新1件上書き・backlogなし）。droppedは抑え（hunt・baud切替窓）
        で捨てたSTATE数。不正行の破棄は数えない（写像の警告を見る）。
        seq_gapはPico→PC方向のSEQ欠番イベント数（初回不計数）。
        err_crc/err_dropは直近STATUSのPico側累積（link品質の正準）。
        rtt_msは直近pingの往復ms、未計測はNone。openで0へ戻る。
        呼ぶだけで線に触れない。例外は投げない。
        """
        try:
            with self._lock:
                status = dict(self._last_status)
                rtt = self._live_rtt_ms
                return {
                    "sent": int(self._live_sent),
                    "merged": int(self._live_merged),
                    "dropped": int(self._live_dropped),
                    "seq_gap": int(self._rx_seq_gap),
                    "err_crc": int(status.get("err_crc", 0)),
                    "err_drop": int(status.get("err_drop", 0)),
                    "rtt_ms": None if rtt is None else float(rtt),
                }
        except Exception:
            return {
                "sent": 0,
                "merged": 0,
                "dropped": 0,
                "seq_gap": 0,
                "err_crc": 0,
                "err_drop": 0,
                "rtt_ms": None,
            }

    def _live_loop_run(self) -> None:
        """120Hz loop本体。絶対時刻で刻み、遅れは追従しbacklogを作らない。

        tkinterのafterには載せない（GUIの詰まりから切り離す）。
        変化はsend_rowがeventで起こすため即送に近い。起床競合で1刻み
        遅れても保留は消さず次刻みで送る（欠落なし）。抑え（_tx_hold）
        が立つあいだは送らず保留を捨てる（hunt・baud切替窓のSTATE化け
        防止。捨て分はdropped）。保留取出しだけ錠内、採番→組立→書込は
        送出の錠で会話と排他（線上のSEQ順＝採番順）、購読者呼出しは錠の外
        （TextSerialTransportと同一規律）。
        1フレーム1write・SEQ方向別。例外では落ちない。
        """
        try:
            with self._lock:
                interval = float(self._live_interval_s)
        except Exception:
            interval = LIVE_INTERVAL_S
        next_tx = time.perf_counter() + interval
        while not self._live_loop_stop.is_set():
            now = time.perf_counter()
            timeout = next_tx - now
            if timeout > 0:
                try:
                    self._live_loop_event.wait(timeout)
                except Exception:
                    self._logger.debug("live loopの待ちで例外", exc_info=True)
            try:
                self._live_loop_event.clear()
            except Exception:
                pass
            if self._live_loop_stop.is_set():
                break
            now = time.perf_counter()
            with self._lock:
                held = self._tx_hold
                pending = self._live_pending
                self._live_pending = None
                latest = self._live_latest
                last_tx = self._live_last_tx
            if held:
                if pending is not None:
                    with self._lock:
                        self._live_dropped += 1
                next_tx = now + interval
                continue
            job: tuple[bytes, str, bool] | None = None
            if pending is not None:
                job = pending
            elif latest is not None and (now - last_tx) >= interval * 0.5:
                # 定期再送の門は半周期で見る。Windowsの待ちは±1ms揺らぐ
                # ため等号ちょうどで比べると1刻みおきに落とし120Hzが
                # 半減する。早めの再送はPico側200ms維持に無害である。
                job = (latest[0], latest[1], False)
            if job is None:
                if next_tx <= now:
                    next_tx = now + interval
                continue
            payload, note, show = job
            # 採番→組立→書込を1単位で包む（会話フレームとの逆転防止）。
            with self._write_lock:
                with self._lock:
                    seq = self._tx_seq
                    self._tx_seq = (self._tx_seq + 1) & 0xFF
                    ser = self.ser
                    begin = self._on_write_begin
                    end = self._on_write_end
                if ser is None:
                    self._logger.debug("bconのlive loopは開いていないため送りません")
                    next_tx = now + interval
                    continue
                try:
                    frame = frame_build(T_STATE, payload, seq)
                except Exception as e:
                    self._logger.debug(f"bconのlive組立に失敗: {e!r}")
                    next_tx = now + interval
                    continue
                self._write_frame(frame, note, show, begin, end, ser)
            now = time.perf_counter()
            with self._lock:
                self._live_sent += 1
                self._live_latest = (payload, note)
                self._live_last_tx = now
            next_tx += interval
            if next_tx <= now:
                next_tx = now + interval

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
        prefixes: Any = None,
        timeout: float = 0.5,
        wanted_types: Any = None,
    ) -> Any:
        """TYPE一致した最初の1フレームを待つ。見つかれば返す。

        基底のprefix前方一致ではなくフレームTYPE一致で待つ（バイナリに
        行が無いため）。送る前に待ちを登録してから送ること。送ってから
        登録するとポンプが先に読んで届かない（Task 4のhello/ping用）。
        基底と同名のprefixes口も受ける。不正値はNoneで返し投げない。
        """
        want: tuple[int, ...]
        raw: Any = wanted_types if wanted_types is not None else prefixes
        if raw is None:
            return None
        if isinstance(raw, int):
            want = (int(raw) & 0xFF,)
        elif isinstance(raw, (bytes, bytearray)):
            try:
                want = tuple(int(b) & 0xFF for b in bytes(raw))
            except (TypeError, ValueError):
                return None
            if not want:
                return None
        elif isinstance(raw, str):
            return None
        else:
            try:
                want = tuple(int(t) & 0xFF for t in raw)
            except (TypeError, ValueError, AttributeError):
                return None
            if not want:
                return None
        try:
            limit_raw = float(timeout)
        except (TypeError, ValueError):
            return None
        if math.isnan(limit_raw):
            limit = 0.0
        elif math.isinf(limit_raw):
            limit = 5.0
        else:
            limit = max(0.0, min(limit_raw, 5.0))
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
                elif ftype == T_RUMBLE:
                    try:
                        self._last_rumble = bytes(frame[1])
                        self._last_rumble_time = now
                    except Exception:
                        self._logger.debug("RUMBLE保持で例外", exc_info=True)
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
        到着確認（in_waiting）してから読む。ブロッキングreadは
        データ到着済みでも復帰が遅れる環境があり、PONG等の往復が
        タイムアウト周期に量子化される。到着を見てから読めば即復帰する。
        """
        while not self._rx_stop.is_set():
            ser = self.ser
            if ser is None:
                break
            try:
                waiting = getattr(ser, "in_waiting", None)
                if waiting is None:
                    chunk = ser.read(64)
                else:
                    try:
                        available = int(waiting() if callable(waiting) else waiting)
                    except Exception:
                        break
                    if available <= 0:
                        try:
                            time.sleep(0.001)
                        except Exception:
                            pass
                        continue
                    chunk = ser.read(min(int(available), 64))
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
        """会話フレームを1発で送る。採番→組立→書込を1単位で包む。

        待ち登録→送出の順は呼び側が守る（ここでは錠を持ったまま待たない）。
        書込の失敗（短い書込・例外）はFalseで返す。SEQは巻き戻さない。
        """
        # 採番→組立→書込のあいだ他の送出を入れない（線上のSEQ順＝採番順）。
        with self._write_lock:
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
            return bool(self._write_frame(frame, row_note, False, begin, end, ser))

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

    def _gui(self, msg: str, quiet: bool, info: bool = False) -> None:
        if not quiet:
            print(msg)
        if info:
            self._logger.info(msg)
        else:
            self._logger.warning(msg)

    def hello(self, timeout: float = 3.0, quiet: bool = False) -> bool:
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
                self._gui(msg, quiet)
                return False
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためHELLOを送りません。"
                        self._gui(msg, quiet)
                        return False
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためHELLOを送りません。"
                    self._gui(msg, quiet)
                    return False
            if limit <= 0.0:
                self._logger.warning("bconのHELLO待ちが0のため送らずFalseを返します。")
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
                        self._gui(msg, quiet)
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
                        self._gui(msg, quiet)
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
                    self._gui(msg, quiet)
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
            self._gui(msg, quiet)
            return False
        except Exception as e:
            self._logger.debug(f"helloで例外: {e!r}", exc_info=True)
            return False

    def ping(self, timeout: float = 1.0, quiet: bool = False) -> float | None:
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
                self._gui(msg, quiet)
                return None
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためPINGを送りません。"
                        self._gui(msg, quiet)
                        return None
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためPINGを送りません。"
                    self._gui(msg, quiet)
                    return None
            if limit <= 0.0:
                self._logger.warning("bconのPING待ちが0のため送らずNoneを返します。")
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
                # PINGも採番→組立→書込を1単位で包む（逆転防止）。
                with self._write_lock:
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
                    write_began = time.perf_counter()
                    self._write_frame(frame, "bcon:PING", False, begin, end, ser)
                    write_ms = (time.perf_counter() - write_began) * 1000.0
                if done.wait(limit):
                    try:
                        rtt = float(matched.get("rtt", 0.0))
                    except (TypeError, ValueError):
                        return None
                    try:
                        match_ms = max(0.0, rtt * 1000.0 - write_ms)
                        print(
                            f"PING内訳: 書込{write_ms:.1f}ms・応答待ち{match_ms:.1f}ms"
                        )
                    except Exception:
                        pass
                    try:
                        with self._lock:
                            self._live_rtt_ms = rtt * 1000.0
                    except Exception:
                        self._logger.debug("RTT保持で例外", exc_info=True)
                    return rtt
            finally:
                try:
                    unsub()
                except Exception:
                    self._logger.debug("PONG購読解除で例外", exc_info=True)
            msg = f"bconのPONGが来ませんでした({limit:.1f}s)。"
            self._gui(msg, quiet)
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

    def last_rumble(self) -> bytes:
        """直近RUMBLE payloadの写し。未受信は空。例外は投げない。"""
        try:
            with self._lock:
                return bytes(self._last_rumble)
        except Exception:
            return b""

    def request_status(
        self, timeout: float = 1.0, quiet: bool = False
    ) -> dict[str, int] | None:
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
                self._gui(msg, quiet)
                return None
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないため状態確認を送りません。"
                        self._gui(msg, quiet)
                        return None
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したため状態確認を送りません。"
                    self._gui(msg, quiet)
                    return None
            if limit <= 0.0:
                self._logger.warning(
                    "bconの状態確認待ちが0のため送らずNoneを返します。"
                )
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
                    self._gui(msg, quiet)
                    return None
                if not done.wait(limit):
                    msg = f"bconのSTATUSが来ませんでした({limit:.1f}s)。"
                    self._gui(msg, quiet)
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
                        if not self.hello(timeout=per, quiet=True):
                            continue
                        if not self.hello(timeout=per, quiet=True):
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
        切替・復帰窓はSTATE送出を抑える（huntの_tx_holdと同一旗）。
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
            # 切替・復帰窓はSTATE送出を抑える（Task 4残余の持越し）。
            # 切替瞬間の前後で線の速度が食い違い、STATEが化けてPico側の
            # errに積まれる。抑え中はsend_row・loopとも送らずdroppedへ
            # 数える（会話フレームは別口で通す）。抑えはlock・失敗の
            # いずれでも解く。窓が200msを超えるとPicoが一旦中立へ落とすが、
            # 復帰後の120Hz更新ですぐ姿勢が戻る（huntと同一扱い）。
            with self._lock:
                self._tx_hold = True
            try:
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
                if self.hello(timeout=min(remaining, 2.0), quiet=True):
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
            finally:
                with self._lock:
                    self._tx_hold = False
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

    def set_emulate_mode(self, role: int, timeout: float = 1.0) -> bool:
        """コントローラ種別(EMULATE_MODE)を切り替える。失敗はFalse＋可視log。

        role は 0=Pro Controller・1=Joy-Con (L)・2=Joy-Con (R)。
        切替はFlash保存・約500ms後自発再起動する。再起動中のSTATE送出
        は止め、復帰後に再HELLOからやり直すこと。例外は投げない。
        """
        try:
            try:
                role_value = int(role)
            except (TypeError, ValueError):
                role_value = -1
            if role_value not in (0, 1, 2):
                msg = f"bconの種別切替は0-2で指定してください: {role!r}"
                print(msg)
                self._logger.warning(msg)
                return False
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
                msg = "bconが開いていないため種別切替しません。"
                print(msg)
                self._logger.warning(msg)
                return False
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないため種別切替しません。"
                        print(msg)
                        self._logger.warning(msg)
                        return False
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したため種別切替しません。"
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
                    T_EMULATE_MODE,
                    bytes([role_value]),
                    "bcon:EMULATE_MODE",
                )
                if not ok:
                    msg = "bconの種別切替送出に失敗しました。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
                if not done.wait(limit):
                    msg = f"bconの種別切替応答が来ませんでした({limit:.1f}s)。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
            finally:
                with self._rx_lock:
                    if entry in self._rx_waiters:
                        self._rx_waiters.remove(entry)
            name = EMULATE_ROLE_NAMES[role_value]
            note = (
                f"bconを{name}へ切替えました。Flash保存・約500ms後自発再起動"
                "します。再起動中のSTATE送出は止め、復帰後に再HELLOから"
                "やり直してください。"
            )
            print(note)
            self._logger.warning(note)
            return True
        except Exception as e:
            self._logger.debug(f"set_emulate_modeで例外: {e!r}", exc_info=True)
            return False

    def request_bootsel(self, timeout: float = 1.0) -> bool:
        """BOOTSEL突入を要求する。失敗はFalse＋可視log。

        T_BOOTSEL＋BOOTSEL_MAGICを1発で送り、旧レートのままSTATUS-ACK
        を待つ。baud切替・_tx_hold操作・再起動側の処理はしない（Pico側が
        約500ms後に自発再起動する）。再起動中のSTATE送出は止め、復帰後に
        再HELLOからやり直すこと。例外は投げない。
        """
        try:
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
                msg = "bconが開いていないためBOOTSEL要求しません。"
                print(msg)
                self._logger.warning(msg)
                return False
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためBOOTSEL要求しません。"
                        print(msg)
                        self._logger.warning(msg)
                        return False
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためBOOTSEL要求しません。"
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
                    T_BOOTSEL,
                    bytes([BOOTSEL_MAGIC]),
                    "bcon:BOOTSEL",
                )
                if not ok:
                    msg = "bconのBOOTSEL要求送出に失敗しました。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
                if not done.wait(limit):
                    msg = f"bconのBOOTSEL応答が来ませんでした({limit:.1f}s)。"
                    print(msg)
                    self._logger.warning(msg)
                    return False
            finally:
                with self._rx_lock:
                    if entry in self._rx_waiters:
                        self._rx_waiters.remove(entry)
            self._logger.info(
                "bconへBOOTSEL突入を要求しました。約500ms後自発再起動"
                "します。再起動中のSTATE送出は止め、復帰後に再HELLOから"
                "やり直してください。"
            )
            return True
        except Exception as e:
            self._logger.debug(f"request_bootselで例外: {e!r}", exc_info=True)
            return False

    def send_config(self, type_: int, payload: bytes) -> bool:
        """CONFIG系を1発で送る。送出の成否を返す。

        利用者画面（BconSetup）や別プロセス側の共通口。会話の
        応答待ちは呼び側が行う。例外は投げない。
        """
        try:
            kind = int(type_) & 0xFF
            body = bytes(payload)
        except (TypeError, ValueError):
            return False
        return bool(self._send_session_frame(kind, body, f"bcon:0x{kind:02X}"))

    def request_color(self, timeout: float = 1.0) -> bytes | None:
        """COLOR_GETを送りCOLOR_INFOの12Bを待つ。失敗・無応答はNone。

        待ち登録→送出の順を守る。例外は投げない。
        """
        try:
            try:
                limit = float(timeout)
            except (TypeError, ValueError):
                limit = 1.0
            if math.isnan(limit):
                limit = 1.0
            elif math.isinf(limit):
                limit = 5.0
            else:
                limit = max(0.0, min(limit, 5.0))
            if self.ser is None:
                return None
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        return None
                except Exception:
                    return None
            if limit <= 0.0:
                return None
            found: dict[str, Any] = {}
            done = threading.Event()
            entry = ((T_COLOR_INFO,), done, found)
            with self._rx_lock:
                self._rx_waiters.append(entry)
            try:
                ok = self._send_session_frame(T_COLOR_GET, b"", "bcon:COLOR_GET")
                if not ok:
                    return None
                if not done.wait(limit):
                    return None
                frame = found.get("frame")
                if frame is None:
                    return None
                try:
                    payload = bytes(frame[1])
                except (TypeError, ValueError, IndexError):
                    return None
                if len(payload) != 12:
                    return None
                return payload
            finally:
                with self._rx_lock:
                    if entry in self._rx_waiters:
                        self._rx_waiters.remove(entry)
        except Exception as e:
            self._logger.debug(f"request_colorで例外: {e!r}", exc_info=True)
            return None

    def send_beacon(self, timeout: float = 2.0) -> dict[str, int] | None:
        """BEACON_STARTを送り直後のSTATUS写しを返す。失敗・無応答はNone。

        戻りのerrcodeは 0x00=受付（再生開始）・0x11=未保存・
        0x10=有線中拒否（0x10+(TYPE&0x0F)のCONFIG拒否域）。
        利用者スクリプト（WakeSwitch2）から叩く公開口。例外は投げない。
        """
        try:
            try:
                limit = float(timeout)
            except (TypeError, ValueError):
                limit = 2.0
            if math.isnan(limit):
                limit = 2.0
            elif math.isinf(limit):
                limit = 5.0
            else:
                limit = max(0.1, min(limit, 5.0))
            if self.ser is None:
                msg = "bconが開いていないためBEACONを送りません。"
                print(msg)
                self._logger.warning(msg)
                return None
            if not self.rx_pump_running():
                try:
                    if not self.start_rx_pump() or not self.rx_pump_running():
                        msg = "bconの受信ポンプが動かないためBEACONを送りません。"
                        print(msg)
                        self._logger.warning(msg)
                        return None
                except Exception:
                    msg = "bconの受信ポンプ起動に失敗したためBEACONを送りません。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
            found: dict[str, Any] = {}
            done = threading.Event()
            entry = ((T_STATUS,), done, found)
            with self._rx_lock:
                self._rx_waiters.append(entry)
            try:
                ok = self._send_session_frame(
                    T_BEACON_START,
                    b"",
                    "bcon:BEACON_START",
                )
                if not ok:
                    msg = "bconのBEACON送出に失敗しました。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
                if not done.wait(limit):
                    msg = f"bconのBEACON応答が来ませんでした({limit:.1f}s)。"
                    print(msg)
                    self._logger.warning(msg)
                    return None
                return self.last_status()
            finally:
                with self._rx_lock:
                    if entry in self._rx_waiters:
                        self._rx_waiters.remove(entry)
        except Exception as e:
            self._logger.debug(f"send_beaconで例外: {e!r}", exc_info=True)
            return None
