#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""encoding.py - 送信行の組み立て（純関数のみ）。

legacy の可変長行と Pico の full-state S 行・Q 行の書式を1箇所に置く。
以前は Keys.SendFormat.convert2str と Sender._buildRow に同じ書式が
二重に書かれており、1文字違うだけで実機の誤動作になる形だった。
どちらもここを通すので、書式はここだけ見ればよい。

純関数にしてある理由:
  snapshot や姿勢の値だけを引数に取り、self を読まない。
  同じ値からは常に同じ行が出る。何度呼んでも変わらない。
  変化印を下ろす副作用は呼び出し側（Sender / SendFormat）が行う。
"""

from __future__ import annotations

from typing import Any


def format_legacy_row(
    btn: Any,
    hat: Any,
    lx: Any,
    ly: Any,
    rx: Any,
    ry: Any,
    l_changed: bool,
    r_changed: bool,
) -> str:
    """legacy 書式の1行を組む（"0x00XX hat [lx ly] [rx ry]"）。

    書式は "0x00XX hat [lx ly] [rx ry]"（変化した側だけが付く可変長）。
      - btn は2ビット左シフトし、下位2bit へ L/R の変化印を入れる
        （0x2 = L が変化 / 0x1 = R が変化）
      - 座標は16進、Hat は10進
      - 先頭は format(x, "#06x") で必ず "0x" + 4桁
    この "0x" は「先頭を必ず 0 にする」ための仕掛けでもある。
      外すと ParseLine の座標行判定を通らない行が
      できるため、短くしてはいけない。
    """
    space = " "
    send_btn = int(btn) << 2
    str_L = ""
    str_R = ""
    if l_changed:
        send_btn |= 0x2
        str_L = format(lx, "x") + space + format(ly, "x")
    if r_changed:
        send_btn |= 0x1
        str_R = format(rx, "x") + space + format(ry, "x")
    return (
        format(send_btn, "#06x")
        + space
        + str(int(hat))
        + (space + str_L if l_changed else "")
        + (space + str_R if r_changed else "")
    )


def pico_field(snap: dict[str, Any], key: str) -> int:
    """1項目を Pico が受け取れる範囲へ収める。

    範囲外を黙って送らない。btn は16bit、他は8bit が上限で、
      超えた値を送ると Pico 側で別の意味になる（切り詰められる）。
    hat は 8 超を中立へ丸める（Pico ファームと同じ規則）。
      PC 側で先に丸めるのは、送った行と Pico の状態を一致させるため。
      丸めを Pico 任せにすると、PC が思っている姿勢とずれる。
    """
    value = int(snap[key])
    if value < 0:
        raise ValueError(f"Pico へ負の値は送れません: {key}={value}")
    if key == "btn":
        if value > 0xFFFF:
            raise ValueError(f"btn が16bitを超えています: {value}")
        return value
    if key == "hat":
        # 8 超は中立（8）。Pico ファームと同じ扱いを PC 側でも行う。
        return value if value <= 8 else 8
    if value > 0xFF:
        raise ValueError(f"{key} が8bitを超えています: {value}")
    return value


def encode_pico_state(snap: dict[str, Any]) -> str:
    """snapshot から Pico 用の1行を組む（純関数・副作用なし）。

    引数は snapshot() が返す辞書（btn / hat / lx / ly / rx / ry）。
      revision は行に含めない。通信内容ではなく PC 側の順序情報。

    従来書式と決定的に違う2点（ここを間違えると別のボタンが押される）:
      1. 従来書式は btn を2ビット左シフトし、下位2bit へスティックの
        変化印を入れる。Pico はシフトしない。同じ姿勢でも値が4倍違う。
      2. 従来書式は変化した側のスティックだけを付ける可変長。
        Pico は6項目すべて必須。足りないと ERR を返される。

    戻り値に改行は付けない。線へ出すときに付ける側が決める
      （legacy 側も同じ約束: Transport が付ける）。
    """
    return "S " + " ".join(
        format(pico_field(snap, key), "x")
        for key in ("btn", "hat", "lx", "ly", "rx", "ry")
    )


def encode_queued_state(snap: dict[str, Any], tick: int, dur: int) -> str:
    """snapshot から Q 行を組む（純関数・副作用なし）。

    書式は Pico ファームのキュー行の解釈と対である。S 行の先頭へ
    tick と dur を足しただけなので、encode_pico_state と同じ並びを
    そのまま使う。書式の解釈を 2 か所に分けない。
    """
    body = encode_pico_state(snap)
    return "Q %04x %04x %s" % (tick & 0xFFFF, dur & 0xFFFF, body[2:])


def verify_pico_encoder() -> bool:
    """encoder が仕様どおりかを機械で確かめる。

    目視で「書けている」と言わず、実際に組んで照合する。
    Pico ファームの受け入れ条件だけを根拠にする。
    """
    base = {
        "btn": 0,
        "hat": 8,
        "lx": 0x80,
        "ly": 0x80,
        "rx": 0x80,
        "ry": 0x80,
        "revision": 0,
    }

    # 中立の行
    if encode_pico_state(base) != "S 0 8 80 80 80 80":
        return False
    # B ボタン(0x0002)。従来書式と違いシフトしない
    b_down = dict(base, btn=0x0002)
    if encode_pico_state(b_down) != "S 2 8 80 80 80 80":
        return False
    # 16進で出ること（10進なら 255 になる）
    maxed = dict(base, lx=0xFF)
    if encode_pico_state(maxed) != "S 0 8 ff 80 80 80":
        return False
    # 何度呼んでも同じ（純関数）
    if encode_pico_state(base) != encode_pico_state(base):
        return False
    # 項目数は必ず7（S + 6項目）
    if len(encode_pico_state(base).split()) != 7:
        return False
    # "0x" が混ざらない（混ざると Pico が ERR を返す）
    if "0x" in encode_pico_state(dict(base, btn=0xABCD)):
        return False
    # hat の 8 超は中立へ丸める
    if encode_pico_state(dict(base, hat=9)) != "S 0 8 80 80 80 80":
        return False
    # 範囲外は黙って送らず例外にする
    try:
        encode_pico_state(dict(base, lx=0x100))
        return False
    except ValueError:
        pass
    return True
