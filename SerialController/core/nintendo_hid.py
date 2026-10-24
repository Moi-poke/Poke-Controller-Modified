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
# 0x30が来なくなってから、最新値として返す猶予。0x30は約15ms毎。
_LATEST_TTL_S = 0.3
# 読み取りスレッドが0x30を見失ってからハンドルを閉じるまで。
# 空読み3回（_READ_TIMEOUT_MS×3）と同じ猶予。
_POLL_STALE_S = 0.6

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
        # open/closeを直列化する。パッド読み・IMU送出・開始処理が同時に
        # openすると、後から来た側が動いているハンドルを閉じてしまう。
        self._open_lock = threading.Lock()
        self._dev: Any = None
        self._packet = 0
        self._pad_packet = 0
        # パッド内容の前回値。同じ内容なら packet を進めない。
        self._pad_key: tuple[Any, ...] | None = None
        # 読み取り専用スレッドが最新0x30を保持する。呼び出し側はここを見る
        # だけで済み、ブロッキング読みが送出ループを止めない。
        self._latest: bytes | None = None
        # 最新0x30を受け取った時刻（monotonic）。鮮度切れは返さない。
        self._latest_t = 0.0
        # open中の印。openは錠を長く持たないため、この間の読み手には
        # 未接続を返す（ブロッキングさせない）。
        self._opening = False
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

        パッド読み・IMU送出・開始処理から並行に呼ばれるため、全体を
        `_open_lock` で直列化する。既に0x30が新鮮なまま開いているなら
        何もせず True を返し、動いているハンドルを閉じない。
        """
        if not self.available():
            return False
        with self._open_lock:
            if path is None and self._is_ready():
                return True
            # 再試行の待ち時間も含めて open 中とする。この間の読み手は
            # `_open_lock` で待たずに未接続を返せる。
            with self._lock:
                self._opening = True
            try:
                for _ in range(max(1, int(retries))):
                    if self._open_once(path):
                        return True
                    try:
                        time.sleep(0.3)
                    except Exception:
                        pass
                return False
            finally:
                with self._lock:
                    self._opening = False

    def _is_ready(self) -> bool:
        """0x30が新鮮なまま開いているか（openの再入を弾く）。"""
        with self._lock:
            return self._dev is not None and self._fresh_030_locked() is not None

    def ensure_live(self, max_age_s: float = 0.25) -> bool:
        """0x30が新鮮なら何もせずTrue。古ければ開き直して再武装する。

        「開いているのに0x30が来ない」ハンドル（IMU未有効・report modeが
        戻った等）を呼び出し側から立て直すための入口。再武装に失敗したら
        False。例外は投げない。
        """
        try:
            age = max(0.0, float(max_age_s))
        except (TypeError, ValueError):
            age = 0.25
        try:
            with self._lock:
                if self._dev is not None and self._fresh_030_locked(age) is not None:
                    return True
                # open()の再利用判定（_LATEST_TTL_S）に引っかからないよう、
                # 鮮度切れのキャッシュを落としてから開き直す。
                self._latest = None
                self._latest_t = 0.0
            return self.open(retries=2)
        except Exception:
            return False

    def _open_once(self, path: bytes | None) -> bool:
        """1回のopen試行。成功で True。

        open・handshake・0x30待ちの間は `self._lock` を持たない。持つと
        GUIのパッド読みやIMU送出がUSBの待ち時間ぶん止まる。開けた
        ローカル実体を、成功時だけ錠内で `self._dev` へ差し替える。
        """
        # 旧ハンドルの読み取りスレッドを先に止める（錠の外でjoin）。
        self._stop_poller()
        with self._lock:
            self._close_locked()
        dev: Any = None
        try:
            import hid

            dev = hid.device()
            if path is not None:
                dev.open_path(path)
            else:
                dev.open(NINTENDO_VID, PROCON_PID)
            dev.set_nonblocking(False)
            if not self._handshake_dev(dev):
                return False
            # IMU有効化 → 0x30モード
            self._subcmd_dev(dev, 0x40, b"\x01")
            time.sleep(0.02)
            self._subcmd_dev(dev, 0x03, b"\x30")
            time.sleep(0.05)
            # 0x30が来るか確認
            report = self._read_report_dev(dev)
            if report is None or len(report) < 49 or report[0] != 0x30:
                # もう1回だけ待つ
                report = self._read_report_dev(dev)
                if report is None or len(report) < 49 or report[0] != 0x30:
                    return False
            # 成功判定に使った0x30も保持する。ここで捨てると直後の
            # read_pad()が未接続になり、呼び出し側がclose→openを
            # 繰り返す（8ms周期の開き直しストーム）。
            with self._lock:
                self._dev = dev
                self._packet = 0
                self._pad_key = None
                self._latest = report
                self._latest_t = time.monotonic()
                # 以降は専用スレッドが読み続け、呼び出し側は最新値を見る。
                self._start_poller_locked()
            dev = None  # 所有権を self へ移した
            return True
        except Exception:
            with self._lock:
                if self._dev is dev:
                    self._close_locked()
                    dev = None
            return False
        finally:
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass

    def _start_poller_locked(self) -> None:
        """読み取り専用スレッドを起こす（錠内で呼ぶ）。

        openの成功判定で読んだ0x30は消さない。消すと直後のread_pad()が
        未接続を返し、呼び出し側が開き直しを繰り返す。
        停止イベントはスレッドごとに新しく作る。前のスレッドの停止
        イベントをclearで戻すと、joinが間に合わなかった旧スレッドが
        生き返り、2本が同じハンドルを読む。
        """
        self._replies.clear()
        stop = threading.Event()
        self._poll_stop = stop
        worker = threading.Thread(
            target=self._poll_run,
            args=(stop,),
            name="nintendo-hid-poll",
            daemon=True,
        )
        self._poller = worker
        worker.start()

    def _poll_run(self, stop: threading.Event) -> None:
        """デバイスを読み続け、最新0x30と応答を保持する。

        生存判定は0x30だけ。0x3f等の別レポートが流れていても0x30が
        途絶えたら閉じる（report modeが戻った場合に開き直せる）。
        読み取りが連続で空振りする（USB再列挙でハンドルが死んだ）ときも
        自分で閉じる。閉じると is_open() が false になり、パッド側とIMU側が
        次の読みで開き直せる。

        stop はスレッド固有。`self._poll_stop` を共有すると、joinが
        間に合わなかった旧スレッドを新しい開始が clear で生き返らせ、
        2本が同じハンドルを読む。
        """
        last_030 = time.monotonic()
        while not stop.is_set():
            with self._lock:
                dev = self._dev
            if dev is None:
                break
            try:
                data = dev.read(_REPORT_LEN, timeout_ms=_READ_TIMEOUT_MS)
            except Exception:
                with self._lock:
                    # 別スレッドが開き直した後の新しいハンドルは閉じない。
                    if self._dev is dev:
                        self._close_locked()
                break
            now = time.monotonic()
            report = bytes(data) if data else b""
            if len(report) >= 49 and report[0] == 0x30:
                with self._lock:
                    # close/openでハンドルが差し替わっていたら触らない。
                    if self._dev is not dev:
                        break
                    last_030 = now
                    self._accept_030_locked(report, now)
                continue
            stale = False
            with self._lock:
                if self._dev is not dev:
                    break
                if len(report) >= 2:
                    self._replies.append(report)
                stale = now - last_030 >= _POLL_STALE_S
                if stale:
                    self._close_locked()
            if stale:
                break

    def _accept_030_locked(self, report: bytes, now: float) -> None:
        """最新0x30を取り込む（錠内で呼ぶ）。

        重複判定は timer byte ではなく内容（byte2以降＝電池・ボタン・
        スティック・IMU 3スロット）で行う。timer が固定・巻き戻る個体でも
        値の変化を取りこぼさないため。内容が同じ再送は最新値の時刻だけ
        進める。古い個体の値が鮮度切れのまま残っている場合は、内容が
        同じでも新しいレポートとして受け入れる（復帰時に古い姿勢を
        返さない）。
        """
        prev = self._latest
        fresh = now - self._latest_t <= _LATEST_TTL_S
        if (
            prev is not None
            and fresh
            and len(prev) >= 49
            and len(report) >= 49
            and prev[2:49] == report[2:49]
        ):
            self._latest_t = now
            return
        self._latest = report
        self._latest_t = now

    def _stop_poller(self) -> None:
        """読み取りスレッドを止める（錠の外でjoinする）。"""
        self._poll_stop.set()
        worker = self._poller
        self._poller = None
        if worker is not None and worker.is_alive():
            worker.join(0.5)

    def _write_dev(self, dev: Any, data: bytes) -> bool:
        """64B output reportを書く。"""
        if dev is None:
            return False
        try:
            buf = bytes(data) + bytes(_REPORT_LEN - len(data))
            return int(dev.write(buf[:_REPORT_LEN])) == _REPORT_LEN
        except Exception:
            return False

    def _read_report_dev(self, dev: Any) -> bytes | None:
        """1レポート読む。失敗・空はNone。"""
        if dev is None:
            return None
        try:
            data = dev.read(_REPORT_LEN, timeout_ms=_READ_TIMEOUT_MS)
            if not data:
                return None
            return bytes(data)
        except Exception:
            return None

    def _handshake_dev(self, dev: Any) -> bool:
        """80確認。Steam等が初期化済みなら便乗し、未初期化なら自前で行う。

        80 02はセッション毎1回のみの制約があり、他プロセスがhandshake済み
        だと失敗する実測。その場合は80 01の応答確認だけで通し、サブコマンド
        直送りでIMU有効化する（Steam初期化済みセッションへの便乗）。
        """
        # 80 01を送る。81応答が無くてもsubへ進む（Steamが横取りする場合あり）。
        # 応答があれば読み飛ばしてキューを空にする。
        try:
            self._write_dev(dev, bytes([0x80, 0x01]))
            for _ in range(5):
                report = self._read_report_dev(dev)
                if report is None:
                    break
                if len(report) >= 2 and report[0] == 0x81 and report[1] == 0x01:
                    break
        except Exception:
            pass
        # 自前handshakeを試す。失敗したら便乗（既に初期化済みのはず）。
        for sub in (0x02, 0x04):
            if not self._write_dev(dev, bytes([0x80, sub])):
                return True  # 書き失敗でも便乗を試す
            if sub == 0x04:
                time.sleep(0.05)
                continue
            report = self._read_report_dev(dev)
            if report is None or len(report) < 2:
                return True  # 応答なしでも便乗を試す
            if report[0] != 0x81 or report[1] != sub:
                return True  # 形状違いでも便乗を試す
        return True

    def _subcmd_dev(self, dev: Any, sub: int, arg: bytes) -> bytes | None:
        """サブコマンドを送り0x21応答を返す。失敗はNone。

        open中はまだ読み取りスレッドが居ないため、直接読んで応答を拾う。
        """
        packet = self._packet & 0xF
        self._packet += 1
        out = bytes([0x01, packet]) + _RUMBLE_NEUTRAL + bytes([sub & 0xFF]) + bytes(arg)
        if not self._write_dev(dev, out):
            return None
        for _ in range(5):
            report = self._read_report_dev(dev)
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
        self._latest_t = 0.0
        # 個体が変わったときに前の個体の内容を引き継がない。
        self._pad_key = None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass

    def close(self) -> None:
        """閉じる。例外は投げない。openとは直列化する。"""
        with self._open_lock:
            self._poll_stop.set()
            with self._lock:
                self._close_locked()
            self._stop_poller()

    def is_open(self) -> bool:
        """開いていて、open処理中でもないか。"""
        with self._lock:
            return self._dev is not None and not self._opening

    def is_opening(self) -> bool:
        """open処理中か（この間の読み手は未接続扱いにする）。"""
        with self._lock:
            return self._opening

    def read_next(self) -> tuple[int, int, int, int, int, int] | None:
        """最新のIMU 1件を返す。パッドAPIと同じ最新値方式。

        0x30は11〜15msごとに3スロットを運ぶが、送出は200Hzで読むため
        古いスロットを順に消費すると時間が巻き戻る。常に最新スロット
        （offset 37..48）だけを返し、古い値を溜めない。
        """
        return self.read()

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

    def _fresh_030_locked(self, max_age_s: float = _LATEST_TTL_S) -> bytes | None:
        """錠内で最新0x30の形と鮮度を検査する。古い・形違いはNone。"""
        report = self._latest
        if report is None or len(report) < 49 or report[0] != 0x30:
            return None
        # __new__で組み立てるテスト用の手置きキャッシュは時刻を持たない。
        stamp = getattr(self, "_latest_t", None)
        if stamp is not None and time.monotonic() - stamp > max_age_s:
            return None
        return report

    def _read_030(self) -> bytes | None:
        """最新の0x30レポートを返す。読み取りは専用スレッドが行う。

        0x30が一定時間来ていなければNone。切断後に古い値を返し続けて
        パッド側が「接続中」と誤認するのを防ぐ。
        """
        with self._lock:
            return self._fresh_030_locked()

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
            # 内容が同じなら packet を進めない。Gamepad.poll_once は
            # packet 不変を「変化なし」として表示更新を省くため、毎回
            # 進めると 8ms ごとに HID 列挙まで走ってしまう。
            key = (bits, frozenset(pressed), lt, rt, lx, ly, rx, ry)
            with self._lock:
                if key != self._pad_key:
                    self._pad_key = key
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
