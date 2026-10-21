#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nintendo_hid.py - ProCon USB HIDの直読み（Tkなし）。

Steam起動中はSDL経由のopenが奪い合いで不安定になる実測（SDL#3450）。
hidapiで直接USB HIDを開き、80ハンドシェイク→IMU有効化→0x30読みを行う。
SDLを使わないためSteam・pygameと干渉しない。

手順（dekuNukem USB-HID-Notes準拠）:
  1. hidでVID 057E/PID 2009を開く
  2. 80 01 → 81応答、80 02 → 81、80 03 → 81、80 02 → 81、80 04（USB固定）
  3. sub 0x40 arg 01（IMU有効化）
  4. sub 0x03 arg 30（0x30モード）
  5. 0x30レポート（64B）からIMU 3件を読む。最新（3件目）を使う。

使い方:
    reader = NintendoHidReader()
    if reader.open():
        sample = reader.read()  # Switch生値6値かNone
"""

from __future__ import annotations

import collections
import struct
import threading
import time
from typing import Any

_RUMBLE_NEUTRAL = bytes([0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40])
_REPORT_LEN = 64
_READ_TIMEOUT_MS = 200

# プロセス内で共有するHID実体（パッド読みとIMU送出で同一ハンドルを使う）。
_shared_lock = threading.Lock()
_shared_reader: "NintendoHidReader | None" = None

NINTENDO_VID = 0x057E
PROCON_PID = 0x2009

# ボタン名 → ビット（xinput.py と同じ値）。
_BUTTON_BITS: dict[str, int] = {
    "DPAD_UP": 0x0001,
    "DPAD_DOWN": 0x0002,
    "DPAD_LEFT": 0x0004,
    "DPAD_RIGHT": 0x0008,
    "START": 0x0010,
    "BACK": 0x0020,
    "LEFT_THUMB": 0x0040,
    "RIGHT_THUMB": 0x0080,
    "LEFT_SHOULDER": 0x0100,
    "RIGHT_SHOULDER": 0x0200,
    "A": 0x1000,
    "B": 0x2000,
    "X": 0x4000,
    "Y": 0x8000,
}


def _stick12_to_short(value: int, invert: bool = False) -> int:
    """12bitスティック（中央0x800）→ SHORT。

    Switch生値は上/右が大きい（Y反転はPico側packの4096-Yが担う）。
    そのためXInput（上/右が正）へはinvert不要。
    """
    v = max(0, min(0xFFF, int(value))) - 0x800
    if invert:
        v = -v
    return int(round(v / 0x800 * 32767))


class NintendoHidReader:
    """ProCon USB HIDの直読み口。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dev: Any = None
        self._packet = 0
        self._pad_packet = 0
        # 読み取り専用スレッドが最新0x30を保持する。呼び出し側はここを見る
        # だけで済み、ブロッキング読みが送出ループを止めない。
        self._latest: bytes | None = None
        self._replies: collections.deque[bytes] = collections.deque(maxlen=16)
        self._poller: threading.Thread | None = None
        self._poll_stop = threading.Event()

    @staticmethod
    def available() -> bool:
        """hidが使えるか。"""
        try:
            import hid  # noqa: F401

            return True
        except Exception:
            return False

    @staticmethod
    def enumerate() -> list:
        """ProConのHID情報一覧。失敗は空。"""
        try:
            import hid

            return hid.enumerate(NINTENDO_VID, PROCON_PID)
        except Exception:
            return []

    def open(self, path: bytes | None = None, retries: int = 5) -> bool:
        """開いてIMUを有効化する。成功で True。

        Steam起動中は奪い合いで不安定になる実測。失敗時は0.3秒待って
        最大retries回まで試す。例外は投げない。
        """
        if not self.available():
            return False
        for _ in range(max(1, int(retries))):
            if self._open_once(path):
                return True
            try:
                time.sleep(0.3)
            except Exception:
                pass
        return False

    def _open_once(self, path: bytes | None) -> bool:
        """1回のopen試行。成功で True。"""
        # 旧ハンドルの読み取りスレッドを先に止める（錠の外でjoin）。
        self._stop_poller()
        with self._lock:
            self._close_locked()
            try:
                import hid

                dev = hid.device()
                if path is not None:
                    dev.open_path(path)
                else:
                    dev.open(NINTENDO_VID, PROCON_PID)
                dev.set_nonblocking(False)
                self._dev = dev
                self._packet = 0
            except Exception:
                self._close_locked()
                return False
            try:
                if not self._handshake_locked():
                    self._close_locked()
                    return False
                # IMU有効化 → 0x30モード
                self._subcmd_locked(0x40, b"\x01")
                time.sleep(0.02)
                self._subcmd_locked(0x03, b"\x30")
                time.sleep(0.05)
                # 0x30が来るか確認
                report = self._read_report_locked()
                if report is None or len(report) < 49 or report[0] != 0x30:
                    # もう1回だけ待つ
                    report = self._read_report_locked()
                    if report is None or len(report) < 49 or report[0] != 0x30:
                        self._close_locked()
                        return False
                # 以降は専用スレッドが読み続け、呼び出し側は最新値を見る。
                self._start_poller_locked()
                return True
            except Exception:
                self._close_locked()
                return False

    def _start_poller_locked(self) -> None:
        """読み取り専用スレッドを起こす（錠内で呼ぶ）。"""
        self._latest = None
        self._replies.clear()
        self._poll_stop.clear()
        if self._poller is not None and self._poller.is_alive():
            return
        worker = threading.Thread(
            target=self._poll_run,
            name="nintendo-hid-poll",
            daemon=True,
        )
        self._poller = worker
        worker.start()

    def _poll_run(self) -> None:
        """デバイスを読み続け、最新0x30と応答を保持する。

        読み取りが連続で空振りする（USB再列挙でハンドルが死んだ）ときは
        自分で閉じる。閉じると is_open() が false になり、パッド側とIMU側が
        次の読みで開き直せる。
        """
        idle = 0
        while not self._poll_stop.is_set():
            with self._lock:
                dev = self._dev
            if dev is None:
                break
            try:
                data = dev.read(_REPORT_LEN, timeout_ms=_READ_TIMEOUT_MS)
            except Exception:
                with self._lock:
                    self._close_locked()
                break
            if not data:
                idle += 1
                # 0x30は約15ms毎に来る。200ms×3回の空振りは切断扱い。
                if idle >= 3:
                    with self._lock:
                        self._close_locked()
                    break
                continue
            idle = 0
            report = bytes(data)
            if len(report) < 2:
                continue
            with self._lock:
                if report[0] == 0x30:
                    self._latest = report
                else:
                    self._replies.append(report)

    def _stop_poller(self) -> None:
        """読み取りスレッドを止める（錠の外でjoinする）。"""
        self._poll_stop.set()
        worker = self._poller
        self._poller = None
        if worker is not None and worker.is_alive():
            worker.join(0.5)

    def _write_locked(self, data: bytes) -> bool:
        """64B output reportを書く。"""
        if self._dev is None:
            return False
        try:
            buf = bytes(data) + bytes(_REPORT_LEN - len(data))
            return int(self._dev.write(buf[:_REPORT_LEN])) == _REPORT_LEN
        except Exception:
            return False

    def _read_report_locked(self) -> bytes | None:
        """1レポート読む。失敗・空はNone。"""
        if self._dev is None:
            return None
        try:
            data = self._dev.read(_REPORT_LEN, timeout_ms=_READ_TIMEOUT_MS)
            if not data:
                return None
            return bytes(data)
        except Exception:
            return None

    def _handshake_locked(self) -> bool:
        """80確認。Steam等が初期化済みなら便乗し、未初期化なら自前で行う。

        80 02はセッション毎1回のみの制約があり、他プロセスがhandshake済み
        だと失敗する実測。その場合は80 01の応答確認だけで通し、サブコマンド
        直送りでIMU有効化する（Steam初期化済みセッションへの便乗）。
        """
        # 80 01を送る。81応答が無くてもsubへ進む（Steamが横取りする場合あり）。
        # 応答があれば読み飛ばしてキューを空にする。
        try:
            self._write_locked(bytes([0x80, 0x01]))
            for _ in range(5):
                report = self._read_report_locked()
                if report is None:
                    break
                if len(report) >= 2 and report[0] == 0x81 and report[1] == 0x01:
                    break
        except Exception:
            pass
        # 自前handshakeを試す。失敗したら便乗（既に初期化済みのはず）。
        for sub in (0x02, 0x04):
            if not self._write_locked(bytes([0x80, sub])):
                return True  # 書き失敗でも便乗を試す
            if sub == 0x04:
                import time as _time

                _time.sleep(0.05)
                continue
            report = self._read_report_locked()
            if report is None or len(report) < 2:
                return True  # 応答なしでも便乗を試す
            if report[0] != 0x81 or report[1] != sub:
                return True  # 形状違いでも便乗を試す
        return True

    def _subcmd_locked(self, sub: int, arg: bytes) -> bytes | None:
        """サブコマンドを送り0x21応答を返す。失敗はNone。

        open後は受信スレッドが回っているため、応答は `_replies` から拾う
        （直接readするとスレッドに奪われて必ず失敗する）。
        """
        packet = self._packet & 0xF
        self._packet += 1
        out = bytes([0x01, packet]) + _RUMBLE_NEUTRAL + bytes([sub & 0xFF]) + bytes(arg)
        if not self._write_locked(out):
            return None
        poller = self._poller
        if poller is not None and poller.is_alive():
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                with self._lock:
                    for i, rep in enumerate(self._replies):
                        if (
                            len(rep) >= 15
                            and rep[0] == 0x21
                            and rep[14] == (sub & 0xFF)
                        ):
                            del self._replies[i]
                            return rep
                time.sleep(0.005)
            return None
        for _ in range(5):
            report = self._read_report_locked()
            if report is None:
                return None
            if len(report) >= 15 and report[0] == 0x21 and report[14] == (sub & 0xFF):
                return report
            # 0x30等が混ざったら読み飛ばす
        return None

    def _close_locked(self) -> None:
        """錠内で閉じる。cacheも消す（閉じた後のreadはNone）。"""
        dev, self._dev = self._dev, None
        self._latest = None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass

    def close(self) -> None:
        """閉じる。例外は投げない。"""
        self._poll_stop.set()
        with self._lock:
            self._close_locked()
        self._stop_poller()

    def is_open(self) -> bool:
        """開いているか。"""
        with self._lock:
            return self._dev is not None

    def read(self) -> tuple[int, int, int, int, int, int] | None:
        """最新IMU 1件（Switch生値6値）を読む。失敗はNone。"""
        report = self._read_030()
        if report is None:
            return None
        # 3件目（最新）を使う。offset 37..48。
        try:
            return struct.unpack("<6h", report[37:49])
        except struct.error:
            return None

    def _read_030(self) -> bytes | None:
        """最新の0x30レポートを返す。読み取りは専用スレッドが行う。"""
        with self._lock:
            report = self._latest
            if report is None or len(report) < 49 or report[0] != 0x30:
                return None
            return report

    def read_pad(self) -> Any:
        """ボタン・スティックをPadStateで読む。失敗は未接続。

        0x30レポート（dekuNukem準拠）から抽出する。スティック12bitは
        SHORT（-32767〜32767）へ換算する。Yは上が正（XInputと同じ向き）。
        """
        from core.xinput import PadState

        report = self._read_030()
        if report is None:
            return PadState()
        try:
            b0, b1, b2 = report[3], report[4], report[5]
            pressed: set[str] = set()
            # byte3: Y,X,B,A,SR,SL,R,ZR
            if b0 & 0x01:
                pressed.add("Y")
            if b0 & 0x02:
                pressed.add("X")
            if b0 & 0x04:
                pressed.add("B")
            if b0 & 0x08:
                pressed.add("A")
            if b0 & 0x40:
                pressed.add("RIGHT_SHOULDER")
            # byte4: -,+,Rstick,Lstick,Home,Capture
            if b1 & 0x01:
                pressed.add("BACK")
            if b1 & 0x02:
                pressed.add("START")
            if b1 & 0x04:
                pressed.add("RIGHT_THUMB")
            if b1 & 0x08:
                pressed.add("LEFT_THUMB")
            if b1 & 0x10:
                pressed.add("HOME")
            if b1 & 0x20:
                pressed.add("CAPTURE")
            # byte5: Down,Up,Right,Left,L,ZL
            if b2 & 0x01:
                pressed.add("DPAD_DOWN")
            if b2 & 0x02:
                pressed.add("DPAD_UP")
            if b2 & 0x04:
                pressed.add("DPAD_RIGHT")
            if b2 & 0x08:
                pressed.add("DPAD_LEFT")
            if b2 & 0x40:
                pressed.add("LEFT_SHOULDER")
            # ZR/ZLはデジタル（byte3 bit7 / byte5 bit7）
            rt = 255 if b0 & 0x80 else 0
            lt = 255 if b2 & 0x80 else 0
            # スティック12bit LE（中央0x800）
            lx = report[6] | ((report[7] & 0x0F) << 8)
            ly = (report[7] >> 4) | (report[8] << 4)
            rx = report[9] | ((report[10] & 0x0F) << 8)
            ry = (report[10] >> 4) | (report[11] << 4)
            bits = 0
            for name in pressed:
                bits |= _BUTTON_BITS.get(name, 0)
            with self._lock:
                self._pad_packet += 1
                packet = self._pad_packet
            return PadState(
                connected=True,
                buttons=bits,
                left_trigger=lt,
                right_trigger=rt,
                thumb_lx=_stick12_to_short(lx),
                thumb_ly=_stick12_to_short(ly),
                thumb_rx=_stick12_to_short(rx),
                thumb_ry=_stick12_to_short(ry),
                packet=packet,
                pressed=frozenset(pressed),
            )
        except Exception:
            return PadState()


def get_shared_reader() -> NintendoHidReader:
    """プロセス内で1本のHIDハンドルを共有する。

    ProConを2つの口（パッド読みとIMU送出）から同時に開くと、出力レポート
    のpacket採番とサブコマンド応答が食い違い、片方の読みが壊れる恐れが
    ある。実体を1つに固定して開閉の競合を避ける（SdlPadReader._shared_sdl
    と同じ規律）。
    """
    global _shared_reader
    with _shared_lock:
        if _shared_reader is None:
            _shared_reader = NintendoHidReader()
        return _shared_reader


def is_shared_reader(reader: object) -> bool:
    """その実体がプロセス共有のHIDリーダーか。"""
    with _shared_lock:
        return _shared_reader is not None and _shared_reader is reader
