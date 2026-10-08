#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xinput.py - XInput 対応コントローラの読み取り（Windows 専用・Tkなし）。

PC に接続したコントローラ（Xbox 系など、XInput に対応したもの）の
現在の押下状態を読み取る。読むだけであり、送信や設定の知識は持たない。
読み取った状態を Switch 側の姿勢へどう申告するかは呼び出し側が決める。

なぜ ctypes 直読みか:
  ・Windows 標準の xinput1_4.dll を直接叩くため、新しい依存は要らない
    （pyproject に足すものは無い）。
  ・読取に失敗したら False を返して黙って閉じた扱いにする。
    例外で落とすと、抜き差しのたびにアプリが止まる。

mac/Linux では常に「繋がっていない」ものとして扱う（XInput 自体が
Windows の API のため）。OS 判定で落とすだけであり、import 自体は
どの OS でもできる。

XInput のボタン定義（wButtons のビット）は XINPUT.H の値と同じ。
  DPAD_UP/DOWN/LEFT/RIGHT = 0x0001/0x0002/0x0004/0x0008
  START/BACK = 0x0010/0x0020
  LEFT_THUMB/RIGHT_THUMB（押し込み） = 0x0040/0x0080
  LEFT_SHOULDER/RIGHT_SHOULDER = 0x0100/0x0200
  A/B/X/Y = 0x1000/0x2000/0x4000/0x8000

スティックの生値は -32768〜32767（SHORT）。0〜255 の座標への換算は
呼び出し側の仕事であり、ここでは生値と押下集合だけを返す。
デッドゾーンは XInput 側の既定（左 7849・右 8689・トリガ 30）を
呼び出し側で使うために定数として出す。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

# wButtons のビット（XINPUT.H と同じ値）。
BTN_DPAD_UP = 0x0001
BTN_DPAD_DOWN = 0x0002
BTN_DPAD_LEFT = 0x0004
BTN_DPAD_RIGHT = 0x0008
BTN_START = 0x0010
BTN_BACK = 0x0020
BTN_LEFT_THUMB = 0x0040
BTN_RIGHT_THUMB = 0x0080
BTN_LEFT_SHOULDER = 0x0100
BTN_RIGHT_SHOULDER = 0x0200
BTN_A = 0x1000
BTN_B = 0x2000
BTN_X = 0x4000
BTN_Y = 0x8000

# XInput 側の既定の遊び（XINPUT.H と同じ値）。
DEADZONE_LEFT = 7849
DEADZONE_RIGHT = 8689
DEADZONE_TRIGGER = 30

# 触れるパッドの数（XUSER_MAX_COUNT = 4）。
MAX_PADS = 4

# 接続切れ（ERROR_DEVICE_NOT_CONNECTED = 1167）。
_NOT_CONNECTED = 1167


class _Gamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", wintypes.SHORT),
        ("sThumbLY", wintypes.SHORT),
        ("sThumbRX", wintypes.SHORT),
        ("sThumbRY", wintypes.SHORT),
    ]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _Gamepad)]


@dataclass
class PadState:
    """1 回の読み取り結果。connected が False なら他は無意味。"""

    connected: bool = False
    buttons: int = 0
    left_trigger: int = 0
    right_trigger: int = 0
    thumb_lx: int = 0
    thumb_ly: int = 0
    thumb_rx: int = 0
    thumb_ry: int = 0
    packet: int = 0
    pressed: frozenset[str] = field(default_factory=frozenset)
    # 読み取り直後の生軸値（SDL の -1.0〜1.0 等）。切り分け表示用。
    # 送信・割当には使わない。
    raw_axes: tuple[float, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """検証・表示用の辞書。pressed は並び替えて安定させる。"""
        return {
            "connected": self.connected,
            "buttons": self.buttons,
            "left_trigger": self.left_trigger,
            "right_trigger": self.right_trigger,
            "thumb_lx": self.thumb_lx,
            "thumb_ly": self.thumb_ly,
            "thumb_rx": self.thumb_rx,
            "thumb_ry": self.thumb_ry,
            "packet": self.packet,
            "pressed": sorted(self.pressed),
        }


# ビットから名前への対応。呼び出し側が Switch 側のボタンへ割り当てる
# ための材料であり、割当自体はここでは決めない。
BUTTON_NAMES: dict[int, str] = {
    BTN_DPAD_UP: "DPAD_UP",
    BTN_DPAD_DOWN: "DPAD_DOWN",
    BTN_DPAD_LEFT: "DPAD_LEFT",
    BTN_DPAD_RIGHT: "DPAD_RIGHT",
    BTN_START: "START",
    BTN_BACK: "BACK",
    BTN_LEFT_THUMB: "LEFT_THUMB",
    BTN_RIGHT_THUMB: "RIGHT_THUMB",
    BTN_LEFT_SHOULDER: "LEFT_SHOULDER",
    BTN_RIGHT_SHOULDER: "RIGHT_SHOULDER",
    BTN_A: "A",
    BTN_B: "B",
    BTN_X: "X",
    BTN_Y: "Y",
}


def pressed_names(buttons: int) -> frozenset[str]:
    """ビット列から押下中の名前集合を作る（読むだけの純関数）。"""
    return frozenset(name for bit, name in BUTTON_NAMES.items() if int(buttons) & bit)


def _load_library() -> Any | None:
    """使える XInput の DLL を1つ掴む。無ければ None。

    Windows 以外では探さない（XInput 自体が Windows の API のため）。
    複数ある場合は新しい順に試す（1_4 → 1_3 → 9_1_0）。
    """
    if os.name != "nt":
        return None
    for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            return ctypes.WinDLL(name)
        except OSError:
            continue
    return None


class XInputReader:
    """XInput パッドの読み取り口。状態を持たず、何度でも読める。

    DLL の掴み直しは初回だけ行う。掴めなかったら以後も読まずに
    未接続を返す（毎回探すと抜き差しのたびに遅くなる）。
    """

    def __init__(self) -> None:
        self._lib: Any | None = None
        self._tried = False

    def _get_state_fn(self) -> Any | None:
        """XInputGetState 関数。掴めなければ None。"""
        if self._tried:
            lib = self._lib
        else:
            self._tried = True
            lib = _load_library()
            self._lib = lib
        if lib is None:
            return None
        fn = getattr(lib, "XInputGetState", None)
        if fn is None:
            return None
        try:
            fn.argtypes = [wintypes.DWORD, ctypes.POINTER(_State)]
            fn.restype = wintypes.DWORD
        except (AttributeError, TypeError):
            return None
        return fn

    @property
    def available(self) -> bool:
        """この環境で XInput が読めるか（DLL が掴めるか）。"""
        return self._get_state_fn() is not None

    def read(self, index: int = 0) -> PadState:
        """指定番号のパッドを読む。繋がっていなければ未接続を返す。

        範囲外の番号も未接続として扱う（例外にしない）。
        読取中の例外も未接続へ畳む。抜き差しの瞬間などに DLL 側が
        投げても、呼び出し側のポーリングが止まらないため。
        """
        if not 0 <= int(index) < MAX_PADS:
            return PadState()
        fn = self._get_state_fn()
        if fn is None:
            return PadState()
        try:
            state = _State()
            rc = fn(int(index), ctypes.byref(state))
        except Exception:
            return PadState()
        if rc != 0:
            return PadState()
        gamepad = state.Gamepad
        return PadState(
            connected=True,
            buttons=int(gamepad.wButtons),
            left_trigger=int(gamepad.bLeftTrigger),
            right_trigger=int(gamepad.bRightTrigger),
            thumb_lx=int(gamepad.sThumbLX),
            thumb_ly=int(gamepad.sThumbLY),
            thumb_rx=int(gamepad.sThumbRX),
            thumb_ry=int(gamepad.sThumbRY),
            packet=int(state.dwPacketNumber),
            pressed=pressed_names(int(gamepad.wButtons)),
        )

    def scan(self) -> list[int]:
        """繋がっているパッド番号の一覧。1つも無ければ空リスト。"""
        return [i for i in range(MAX_PADS) if self.read(i).connected]
