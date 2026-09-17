#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_mapping.py - Modified行からbcon中間姿勢への写像（純関数）。

入力は `<btn-hex> <hat> [lx ly [rx ry]]` と `end`。
出力はLEN非依存の中間姿勢（buttons u32＋sticks u16域0-4095中央0x0800）。
LEN8送出時は `u16>>4` でu8化する（`0x800>>4==0x80`で等価）。
Y反転はここ1箇所に集約する。Pico側は値をそのままpackする。
"""

from __future__ import annotations

BTN_B = 1 << 0
BTN_A = 1 << 1
BTN_Y = 1 << 2
BTN_X = 1 << 3
BTN_R = 1 << 4
BTN_ZR = 1 << 5
BTN_PLUS = 1 << 6
BTN_RSTICK = 1 << 7
BTN_DOWN = 1 << 8
BTN_RIGHT = 1 << 9
BTN_LEFT = 1 << 10
BTN_UP = 1 << 11
BTN_L = 1 << 12
BTN_ZL = 1 << 13
BTN_MINUS = 1 << 14
BTN_LSTICK = 1 << 15
BTN_HOME = 1 << 16
BTN_CAPTURE = 1 << 17

_WIRE_TO_VIIPER = (
    None,
    None,
    BTN_Y,
    BTN_B,
    BTN_A,
    BTN_X,
    BTN_L,
    BTN_R,
    BTN_ZL,
    BTN_ZR,
    BTN_MINUS,
    BTN_PLUS,
    BTN_LSTICK,
    BTN_RSTICK,
    BTN_HOME,
    BTN_CAPTURE,
)

_HAT_TO_BTNS = (
    (BTN_UP,),
    (BTN_UP, BTN_RIGHT),
    (BTN_RIGHT,),
    (BTN_DOWN, BTN_RIGHT),
    (BTN_DOWN,),
    (BTN_DOWN, BTN_LEFT),
    (BTN_LEFT,),
    (BTN_UP, BTN_LEFT),
    (),
)

_CENTER_U16 = 0x0800


def neutral_state() -> dict[str, int]:
    """全解放の中間姿勢。`end`受信と等価。"""
    return {
        "buttons": 0,
        "lx": _CENTER_U16,
        "ly": _CENTER_U16,
        "rx": _CENTER_U16,
        "ry": _CENTER_U16,
    }


def is_end_row(row: str) -> bool:
    """`end`行か。大文字小文字を問わない。前後空白を無視する。"""
    return row.strip().lower() == "end"


def _u8_to_u16(value: int) -> int:
    """wire/LEN8のu8（0-255中央0x80）をu16域（0-4095中央0x0800）へ。"""
    return (max(0, min(0xFF, int(value))) << 4) & 0xFFF


def wire_row_to_state(row: str) -> dict[str, int]:
    """Modified行を中間姿勢へ写す。不正行はValueError（黙って送らない）。

    LS/RS quirkを再現する。LSのみ→LX/LYへwire lx/ly、RSのみ→RX/RYへ
    wire lx/ly、両方→各々、なし→スティック不変ではなく中立維持の呼び出し側
    が保持する。ここでは欄なし行は中央のまま返す。
    """
    text = row.strip()
    if is_end_row(text):
        return neutral_state()
    parts = text.replace("\r", " ").replace("\n", " ").split()
    if len(parts) not in (2, 4, 6):
        raise ValueError(f"bconへ送れない行形式です: {row!r}")
    try:
        wire = int(parts[0], 16)
        hat = int(parts[1], 16 if parts[1].lower().startswith("0x") else 10)
    except ValueError:
        raise ValueError(f"bconへ送れない行形式です: {row!r}") from None
    if not 0 <= wire <= 0xFFFF:
        raise ValueError(f"btnが16bitを超えています: {row!r}")
    buttons = 0
    for bit in range(2, 16):
        if wire & (1 << bit):
            mapped = _WIRE_TO_VIIPER[bit]
            if mapped is not None:
                buttons |= mapped
    if 0 <= hat <= 8:
        for btn in _HAT_TO_BTNS[hat]:
            buttons |= btn
    else:
        for btn in _HAT_TO_BTNS[8]:
            buttons |= btn
    state = neutral_state()
    state["buttons"] = buttons
    if len(parts) >= 4:
        try:
            lx = int(parts[2], 16)
            ly = int(parts[3], 16)
        except ValueError:
            raise ValueError(f"bconへ送れない行形式です: {row!r}") from None
        rs_only = bool(wire & 0x0001) and not bool(wire & 0x0002)
        ls_only = bool(wire & 0x0002) and not bool(wire & 0x0001)
        both = bool(wire & 0x0001) and bool(wire & 0x0002)
        if ls_only or both:
            state["lx"] = _u8_to_u16(lx)
            state["ly"] = _u8_to_u16(ly)
        if rs_only:
            state["rx"] = _u8_to_u16(lx)
            state["ry"] = _u8_to_u16(ly)
        if both and len(parts) == 6:
            try:
                rx = int(parts[4], 16)
                ry = int(parts[5], 16)
            except ValueError:
                raise ValueError(f"bconへ送れない行形式です: {row!r}") from None
            state["rx"] = _u8_to_u16(rx)
            state["ry"] = _u8_to_u16(ry)
    return state


def state_to_len8(state: dict[str, int]) -> bytes:
    """中間姿勢からSTATE LEN8 payload（BTN u32LE＋u8×4）を作る。"""
    buttons = int(state["buttons"]) & 0x003FFFFF
    lx = (int(state["lx"]) >> 4) & 0xFF
    ly = (int(state["ly"]) >> 4) & 0xFF
    rx = (int(state["rx"]) >> 4) & 0xFF
    ry = (int(state["ry"]) >> 4) & 0xFF
    return buttons.to_bytes(4, "little") + bytes([lx, ly, rx, ry])


def state_to_len12(state: dict[str, int]) -> bytes:
    """中間姿勢からSTATE LEN12 payload（BTN u32LE＋u16LE×4）を作る。"""
    buttons = int(state["buttons"]) & 0x003FFFFF
    out = bytearray(buttons.to_bytes(4, "little"))
    for key in ("lx", "ly", "rx", "ry"):
        value = max(0, min(0xFFF, int(state[key])))
        out.extend(value.to_bytes(2, "little"))
    return bytes(out)
