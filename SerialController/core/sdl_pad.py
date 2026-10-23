#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sdl_pad.py - SDL GameController の読み取り（Tkなし）。

XInput に対応していないコントローラ（Switch Pro コンなど）を
読むための口。core/xinput.py の XInputReader と同じ PadState を
返すため、呼び出し側はどちらも同じに扱える。

なぜ GameController API か:
  ・生の joystick は個体ごとにボタン番号・軸順が違う。Pro コンと
    Xbox 系で同じ番号が別の操作を指すため、番号決め打ちでは
    「割り当てがめちゃくちゃ」になる。
  ・SDL の GameController は機種ごとの配置データベースを持ち、
    a/b/x/y・十字キー・スティック・トリガを正規の意味で読める。
    Pro コンも Xbox 系も同じ意味で読めるため、割当は1つで済む。
  ・Pro コンの A/B と X/Y は Switch 本体準拠の位置で読める。
    （SDL が配置を吸収するため、呼び出し側で入れ替えない）。

標準ボタン（SDL_GameControllerButton 順）:
  0 A・1 B・2 X・3 Y・4 BACK・5 GUIDE・6 START・7 左押し込み・
  8 右押し込み・9 LB・10 RB・11 十字上・12 十字下・13 十字左・
  14 十字右・15 MISC・16〜19 パドル・20 タッチパッド
標準軸: 0 左X・1 左Y・2 右X・3 右Y・4 左トリガ・5 右トリガ。
  Y は上が負。トリガは 0.0（離し）〜1.0（押し）。

値の換算:
  ・スティック -1.0〜1.0 → SHORT（-32767〜32767）。PadState の
    形に合わせるためであり、0〜255 への換算は gamepad_map が行う。
  ・トリガ 0.0〜1.0 → 0〜255。0.0（離し）が 0 になる。
  ・CAPTURE と HOME は標準ボタンに無い。Pro コンの CAPTURE と
    HOME は MISC/GUIDE に出る個体があるため、MISC→CAPTURE・
    GUIDE→HOME に読み替える。
  ・packet は SDL に無いため、変化のたびに進める自前の連番を使う。
    同じ内容なら進めない（XInput の dwPacketNumber と同じ意味）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.xinput import MAX_PADS, PadState, pressed_names

# SDL初期化に失敗したときの再試行間隔(秒)。一度の失敗を永久に
# 引きずると、以降ずっと「ゲームパッドが見つかりません」になる。
ENSURE_RETRY_S = 2.0

# 未接続が続いたときに SDL を作り直すまでの時間(秒)。HIDの奪い合い
# などで SDL の列挙が古くなったままになる実測がある。
RESET_MISSING_S = 2.0

# GameController 標準ボタン番号 → XInput と同じ名前。
# Pro コンの CAPTURE/HOME は標準に無いため、MISC→CAPTURE・
# GUIDE→HOME に読み替える（Pro コンではその個体が多い）。
_GC_BUTTON_NAMES: dict[int, str] = {
    0: "A",
    1: "B",
    2: "X",
    3: "Y",
    4: "BACK",
    5: "GUIDE_HOME",
    6: "START",
    7: "LEFT_THUMB",
    8: "RIGHT_THUMB",
    9: "LEFT_SHOULDER",
    10: "RIGHT_SHOULDER",
    11: "DPAD_UP",
    12: "DPAD_DOWN",
    13: "DPAD_LEFT",
    14: "DPAD_RIGHT",
    15: "MISC_CAPTURE",
}


# ボタン番号 → ビット（xinput.py と同じ値）。pressed 集合だけでなく
# buttons ビット列も埋めるため（検証・表示用）。
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


def _axis_to_short(value: float) -> int:
    """-1.0〜1.0 → SHORT。範囲外は丸める。"""
    clamped = max(-1.0, min(1.0, float(value)))
    return int(round(clamped * 32767))


def _trigger_to_byte(value: float) -> int:
    """トリガ値 → 0〜255。

    生joystick は -1.0（離し）〜1.0（押し）。GameController 標準の
    0.0〜1.0 も受ける。離し側は一律 0 に潰す。
    """
    number = float(value)
    if number <= 0.0:
        return 0
    clamped = min(1.0, number)
    return max(0, min(255, int(round(clamped * 255))))


def _normalize_axis(value: Any) -> float:
    """GameController の軸値 → -1.0〜1.0。

    pygame 2.6 の get_axis は int（Sint16相当の -32768〜32767）を
    返す。-1.0〜1.0 の float だと思って掛け算すると、中立の微小値
    （例: -1324）が -1324.0 へ膨らみ、丸めで全倒れになる。
    -1〜1 の範囲ならそのまま float 化し、それ以外は Sint16 として
    32767 で割る。負側の -32768 は -1.0 に丸める。
    """
    raw = value() if callable(value) else value
    number = float(raw)  # type: ignore[arg-type]
    if isinstance(raw, float) or -1.0 <= number <= 1.0:
        # float 実装か、既に -1〜1 の範囲ならそのまま使う。
        return max(-1.0, min(1.0, number))
    return max(-1.0, min(1.0, number / 32767.0))


class SdlPadReader:
    """SDL GameController の読み取り口。

    XInputReader と同じ read(index) / scan() / available を持つ。
    pygame の初期化は初回の読み取り時に1度だけ行う。初期化に失敗
    したら以後も読まずに未接続を返す。GameController に対応して
    いないパッド（is_controller が False）は未接続として扱う。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready: bool | None = None
        self._retry_at = 0.0
        self._missing_since: float | None = None
        self._sticks: dict[int, Any] = {}
        # パッドごとの前回内容と連番。内容が変わったときだけ進める。
        self._last_key: dict[int, Any] = {}
        self._packet: dict[int, int] = {}
        # 読み取り済みの番号。初回だけ古いキャッシュ疑いで読み直す。
        self._seen: set[int] = set()

    def _is_fresh(self, index: int) -> bool:
        """まだ一度も読んでいない番号か。"""
        with self._lock:
            return int(index) not in self._seen

    def _mark_seen(self, index: int) -> None:
        """読み取り済みにする。"""
        with self._lock:
            self._seen.add(int(index))

    def _ensure(self) -> bool:
        """pygame と GameController を使える状態にする。成功で True。

        失敗は恒久ではない。ENSURE_RETRY_S 経過後に再試行する
        （一度のinit失敗で永久に未接続になるのを防ぐ）。
        """
        if self._ready is True:
            return True
        now = time.monotonic()
        if self._ready is False and now < self._retry_at:
            return False
        try:
            import pygame
            from pygame._sdl2 import controller as _gc

            pygame.init()
            _gc.init()
            self._ready = True
        except Exception:
            self._ready = False
            self._retry_at = now + ENSURE_RETRY_S
        return self._ready

    def _reset_sdl(self) -> None:
        """SDL を作り直して列挙をやり直す。失敗しても落とさない。"""
        try:
            import pygame
            from pygame._sdl2 import controller as gc

            try:
                gc.quit()
            except Exception:
                pass
            pygame.quit()
        except Exception:
            pass
        with self._lock:
            self._sticks.clear()
        self._ready = None

    @property
    def available(self) -> bool:
        """この環境で SDL が読めるか（pygame が入っているか）。"""
        return self._ensure()

    def _gc(self) -> Any | None:
        """GameController モジュール。無ければ None。"""
        if not self._ensure():
            return None
        try:
            from pygame._sdl2 import controller as gc

            return gc
        except Exception:
            return None

    def names(self) -> dict[int, str]:
        """繋がっているパッド番号 → 名前。選択肢の表示用。

        GameController と生joystick の和集合を返す。抜き差しで
        両者の番号がずれても、どちらかにいれば選べる。
        """
        gc = self._gc()
        out: dict[int, str] = {}
        if gc is not None:
            try:
                for i in range(int(gc.get_count())):
                    try:
                        out[i] = str(gc.name_forindex(i))
                    except Exception:
                        continue
            except Exception:
                pass
        try:
            import pygame.joystick as _joy

            _joy.init()
            for i in range(int(_joy.get_count())):
                if i in out:
                    continue
                try:
                    out[i] = str(_joy.Joystick(i).get_name())
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _stick(self, index: int) -> tuple[Any | None, Any | None]:
        """(GameController, 生joystick) の組。掴めなければ (None, None)。

        ボタンは GameController の配置DBで正規化して読み、軸は
        生joystick から読む。GameController の軸は SDL 2.28 の
        Pro コン配置で Y が -1 に張り付く実測があるため使わない。
        生joystick の軸順は Pro コン実測で標準通り（0/1左XY・
        2/3右XY・4/5トリガ、Y上が負・トリガ-1.0離し）を確認済み。
        """
        with self._lock:
            pair = self._sticks.get(int(index))
        if pair is not None:
            gc_stick, joy_stick, dev_id = pair
            alive = False
            try:
                alive = bool(gc_stick.attached())
            except Exception:
                alive = False
            if alive:
                # 生側も触って確かめる。抜けた実体はここで例外になる。
                try:
                    joy_stick.get_numaxes()
                except Exception:
                    alive = False
            if alive:
                # 番号を使い回した別デバイスなら捨てる。SDL の ID は
                # 再接続で変わり、使い回されない（SDL_JoystickID 仕様）。
                # 古い実体のまま読むと別物の軸が残り、中立で倒れ続ける。
                try:
                    if joy_stick.get_instance_id() != dev_id:
                        alive = False
                except Exception:
                    pass
            if alive:
                return (gc_stick, joy_stick)
            with self._lock:
                self._sticks.pop(int(index), None)
        gc = self._gc()
        if gc is None:
            return (None, None)
        try:
            if not bool(gc.is_controller(int(index))):
                return (None, None)
            gc_stick = gc.Controller(int(index))
            raw_stick: Any | None = None
            try:
                import pygame.joystick as _joy

                _joy.init()
                if 0 <= int(index) < _joy.get_count():
                    raw_stick = _joy.Joystick(int(index))
                    raw_stick.init()
            except Exception:
                raw_stick = None
            joy_stick = raw_stick
            if joy_stick is None:
                return (None, None)
            try:
                dev_id = joy_stick.get_instance_id()
            except Exception:
                dev_id = None
        except Exception:
            return (None, None)
        with self._lock:
            self._sticks[int(index)] = (gc_stick, joy_stick, dev_id)
        return (gc_stick, joy_stick)

    def read(self, index: int = 0) -> PadState:
        """指定番号のパッドを読む。繋がっていなければ未接続を返す。"""
        if not 0 <= int(index) < MAX_PADS:
            return PadState()
        gc = self._gc()
        if gc is None:
            return PadState()
        # イベントポンプは未接続時も必須。SDL は pump でデバイスの
        # 到着/離脱を処理するため、接続後だけ呼ぶと一度見失った後に
        # 到着イベントを拾えず永久に復帰しない。
        try:
            import pygame as _pg

            _pg.event.pump()
        except Exception:
            pass
        gc_stick, joy_stick = self._stick(index)
        if gc_stick is None or joy_stick is None:
            now = time.monotonic()
            if self._missing_since is None:
                self._missing_since = now
            elif now - self._missing_since >= RESET_MISSING_S:
                # 未接続が続く: SDL を作り直して列挙をやり直す。
                self._missing_since = now
                self._reset_sdl()
            return PadState()
        self._missing_since = None
        try:
            pressed: set[str] = set()
            for num, name in _GC_BUTTON_NAMES.items():
                try:
                    if gc_stick.get_button(num):
                        pressed.add(name)
                except Exception:
                    continue
            # 軸は生joystick から読む。GameController の軸は張り付く。
            # 生の軸順は標準通り（Y上が負・トリガ-1.0離し）。
            # -1.0〜1.0 の float で来るため正規化は素通しになる。
            axes: list[float] = []
            for axis in range(6):
                try:
                    axes.append(_normalize_axis(joy_stick.get_axis(axis)))
                except Exception:
                    # 標準軸の既定。スティックは中央、トリガは離し。
                    axes.append(0.0 if axis in (0, 1, 2, 3) else -1.0)
            lx = _axis_to_short(axes[0])
            # Y は上が負、SHORT は上が正（XInput と同じ向き）。
            ly = _axis_to_short(-axes[1])
            rx = _axis_to_short(axes[2])
            ry = _axis_to_short(-axes[3])
            lt = _trigger_to_byte(axes[4])
            rt = _trigger_to_byte(axes[5])
            # GUIDE→HOME・MISC→CAPTURE の読み替え。pressed 側の
            # 仮名を Switch 側の正名へ直す。
            if "GUIDE_HOME" in pressed:
                pressed.discard("GUIDE_HOME")
                pressed.add("HOME")
            if "MISC_CAPTURE" in pressed:
                pressed.discard("MISC_CAPTURE")
                pressed.add("CAPTURE")
            bits = 0
            for name in pressed:
                bits |= _BUTTON_BITS.get(name, 0)
            key = (
                bits,
                lt,
                rt,
                lx,
                ly,
                rx,
                ry,
                frozenset(pressed),
            )
            last = self._last_key.get(int(index))
            if last is not None and last == key:
                packet = self._packet.get(int(index), 0)
            else:
                packet = self._packet.get(int(index), 0) + 1
                self._packet[int(index)] = packet
                self._last_key[int(index)] = key
            return PadState(
                connected=True,
                buttons=bits,
                left_trigger=lt,
                right_trigger=rt,
                thumb_lx=lx,
                thumb_ly=ly,
                thumb_rx=rx,
                thumb_ry=ry,
                packet=packet,
                pressed=frozenset(pressed),
                raw_axes=tuple(round(float(v), 4) for v in axes),
            )
        except Exception:
            # 抜き差しの瞬間など。次回読み直すため実体を捨てる。
            with self._lock:
                self._sticks.pop(int(index), None)
            return PadState()

    def scan(self) -> list[int]:
        """繋がっているパッド番号の一覧。1つも無ければ空リスト。"""
        return [i for i in range(MAX_PADS) if self.read(i).connected]

    @staticmethod
    def debug_names(state: PadState) -> list[str]:
        """pressed 集合の表示用（読むだけの純関数）。"""
        return sorted(pressed_names(state.buttons))
