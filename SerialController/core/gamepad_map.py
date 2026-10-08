#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gamepad_map.py - XInput の入力を Switch 側の操作へ割り当てる表（Tkなし）。

PC に接続したコントローラで Switch を直接触ったときと同じ操作に
なるよう、XInput 側の名前（A / DPAD_UP / LEFT_SHOULDER など）と
スティック・トリガを Switch 側（Button / Hat / 座標）へ直す。

ここが持つのは「いま何を押すべきか」の判断だけである。送信や
ポーリングの手順は持たない。

既定の割り当て（Xbox 系の配置を Switch の配置へ読み替える）:
  A/B/X/Y … そのまま A/B/X/Y
  START/BACK … PLUS/MINUS
  十字キー … Hat（TOP/RIGHT/BTM/LEFT。斜めは2方向の同時押しで合成）
  LB/RB … L/R、LT/RT … ZL/ZR（しきい値を超えたら押下）
  左スティック押し込み … LCLICK、右 … RCLICK
  左スティック … 左スティック座標、右 … 右スティック座標
  HOME/CAPTURE … XInput に無いため未割当（空のまま）

トリガとスティックの換算:
  ・トリガは 0〜255 のアナログ。DEADZONE_TRIGGER（30）を超えたら
    デジタルな押下として扱う（押し込み量は送らない）。
  ・スティックは -32768〜32767 の SHORT。0〜255 の座標へ直す。
    遊び（DEADZONE_LEFT/RIGHT）以内は中立（128, 128）に寄せる。
    Y は上が大きい（XInput）から上が小さい（Switch 行）へ反転する。
    丸めは int() で切り捨て（GuiAssets._stickXY と同じ規則）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.xinput import (
    DEADZONE_LEFT,
    DEADZONE_RIGHT,
    DEADZONE_TRIGGER,
    PadState,
)

# XInput 側の名前 → Switch 側の Button 名。
# Hat とスティックは別扱いのためここには載せない。
# HOME/CAPTURE は XInput に無いが SDL（Pro コン）では読めるため、
# ここに載せておく（XInput 経路では現れないので無害）。
BUTTON_MAP: dict[str, str] = {
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "START": "PLUS",
    "BACK": "MINUS",
    "HOME": "HOME",
    "CAPTURE": "CAPTURE",
    "LEFT_SHOULDER": "L",
    "RIGHT_SHOULDER": "R",
    "LEFT_THUMB": "LCLICK",
    "RIGHT_THUMB": "RCLICK",
}

# XInput 側の十字キー名 → Hat の向き名。
DPAD_MAP: dict[str, str] = {
    "DPAD_UP": "UP",
    "DPAD_RIGHT": "RIGHT",
    "DPAD_DOWN": "DOWN",
    "DPAD_LEFT": "LEFT",
}

# トリガ → Switch 側の Button 名。
TRIGGER_MAP: dict[str, str] = {
    "LEFT_TRIGGER": "ZL",
    "RIGHT_TRIGGER": "ZR",
}

# 座標の中立。
CENTER = 128


def stick_to_xy(raw_x: int, raw_y: int, deadzone: int) -> tuple[int, int]:
    """SHORT 値を 0〜255 の座標へ直す（読むだけの純関数）。

    遊び以内は中立に寄せる。遊びを超えた分は、遊びの端を 0 として
    最大値までの割合で伸ばす（端で 0/255 に届く）。
    Y は反転する（XInput は上が正、Switch 行は上が小さい）。
    """
    dz = max(0, int(deadzone))
    max_v = 32767
    x = _axis_to_coord(int(raw_x), dz, max_v)
    # Y は上が正（XInput）→上が小さい（Switch 行）へ反転する。
    # 最小値 -32768 の扱いに注意：-raw_y ではなく符号反転後に換算する。
    y = _axis_to_coord(-int(raw_y), dz, max_v)
    return x, y


def _axis_to_coord(value: int, deadzone: int, max_v: int) -> int:
    """1 軸分を 0〜255 へ直す。遊び以内は中立。"""
    if abs(value) <= deadzone:
        return CENTER
    span = max_v - deadzone
    if span <= 0:
        return CENTER
    if value > 0:
        mag = (value - deadzone) / span
        return max(0, min(255, int(CENTER + mag * (255 - CENTER))))
    mag = (-value - deadzone) / span
    return max(0, min(255, int(CENTER - mag * CENTER)))


@dataclass
class MappedInput:
    """1 回分の割り当て結果。呼び出し側はそのまま申告に使う。"""

    connected: bool = False
    buttons: frozenset[str] = field(default_factory=frozenset)
    hat_dirs: frozenset[str] = field(default_factory=frozenset)
    stick_l: tuple[int, int] = (CENTER, CENTER)
    stick_r: tuple[int, int] = (CENTER, CENTER)


def map_state(
    state: PadState,
    button_map: dict[str, str | None] | None = None,
    trigger_threshold: int = DEADZONE_TRIGGER,
    left_deadzone: int = DEADZONE_LEFT,
    right_deadzone: int = DEADZONE_RIGHT,
) -> MappedInput:
    """PadState を Switch 側の操作へ直す（読むだけの純関数）。

    button_map を渡すと既定のボタン割当を上書きできる（キー名は
    XInput 側、値は Switch 側の Button 名。None の値は外す）。
    未接続なら空（中立）を返す。
    """
    if not state.connected:
        return MappedInput()
    table = dict(BUTTON_MAP)
    if button_map is not None:
        for key, value in button_map.items():
            if value is None:
                table.pop(str(key), None)
            else:
                table[str(key)] = str(value)
    buttons: set[str] = set()
    for name in state.pressed:
        mapped = table.get(name)
        if mapped:
            buttons.add(mapped)
    if int(state.left_trigger) > int(trigger_threshold):
        mapped = TRIGGER_MAP.get("LEFT_TRIGGER")
        if mapped:
            buttons.add(mapped)
    if int(state.right_trigger) > int(trigger_threshold):
        mapped = TRIGGER_MAP.get("RIGHT_TRIGGER")
        if mapped:
            buttons.add(mapped)
    hat_dirs = frozenset(DPAD_MAP[name] for name in state.pressed if name in DPAD_MAP)
    stick_l = stick_to_xy(state.thumb_lx, state.thumb_ly, left_deadzone)
    stick_r = stick_to_xy(state.thumb_rx, state.thumb_ry, right_deadzone)
    return MappedInput(
        connected=True,
        buttons=frozenset(buttons),
        hat_dirs=hat_dirs,
        stick_l=stick_l,
        stick_r=stick_r,
    )


def diff_mapped(old: MappedInput | None, new: MappedInput) -> dict[str, Any]:
    """前回との差分を求める（読むだけの純関数）。

    返すのは press/release すべきボタン名、Hat の向き集合の変化、
    スティック座標の変化だけである。old が None なら全量を返す。
    """
    old_buttons = set(old.buttons) if old is not None else set()
    old_hat = set(old.hat_dirs) if old is not None else set()
    old_l = old.stick_l if old is not None else None
    old_r = old.stick_r if old is not None else None
    new_buttons = set(new.buttons)
    new_hat = set(new.hat_dirs)
    return {
        "press": frozenset(new_buttons - old_buttons),
        "release": frozenset(old_buttons - new_buttons),
        "hat_changed": new_hat != old_hat,
        "hat_dirs": frozenset(new_hat),
        "stick_l_changed": old_l != new.stick_l,
        "stick_l": new.stick_l,
        "stick_r_changed": old_r != new.stick_r,
        "stick_r": new.stick_r,
    }
