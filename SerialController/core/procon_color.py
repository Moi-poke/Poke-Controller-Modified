#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core/procon_color.py - プロコン色まわりの純粋助けの置き場所。

実体はここに1つだけ置く。公開口は `Commands/CommandColor.py` の
再公開（利用者スクリプトの凍結面）で、`BconSetup.py` からも同じ名で
読める（後方互換の再公開）。窓・利用者台本の2口で読みを二重持ちに
しないための単一正本である。

ここに置くのは線・画面に触れない助けだけにする。tkinter・Tk変数・
Canvas・スレッドは持ち込まない（task bounds の規律）。運搬器への
依存は公開口（send_config / request_status 等）の有無だけを見る
ダックタイピングに留め、運搬器の実体は import しない。
"""

from __future__ import annotations

from typing import Any

from core.transport.base import BCON_STATE

# CONFIG拒否（0x10+(TYPE&0x0F)）の TYPE 名。0x38 EMULATE_MODE まで載せる。
_CONFIG_TYPE_NAMES = {
    0x30: "CAPTURE_START",
    0x31: "BEACON_START",
    0x32: "COLOR_SET",
    0x33: "KEY_DELETE",
    0x34: "WIRED_MODE",
    0x35: "STATUS_REQ",
    0x36: "BAUD_SET",
    0x37: "BOOTSEL",
    0x38: "EMULATE_MODE",
}


def parse_color_hex(text: Any) -> tuple[int, int, int] | None:
    """RRGGBB（先頭 #・0x 可）を RGB 3つ組へ。読めなければ None（送らない）。

    3桁（"f00"）は受け付けない。意図と違う色になる恐れがあるため。
    """
    try:
        cleaned = str(text).strip()
    except (TypeError, ValueError, AttributeError):
        return None
    if cleaned.startswith("#"):
        cleaned = cleaned[1:]
    elif cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) != 6:
        return None
    try:
        value = int(cleaned, 16)
    except ValueError:
        return None
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def build_color_payload(slots: Any) -> bytes:
    """RGB 4つ組から COLOR_SET の12Bを作る。4つ無ければ ValueError。"""
    try:
        items = list(slots)
    except TypeError:
        raise ValueError(f"色は4つ組で入れてください: {slots!r}") from None
    if len(items) != 4:
        raise ValueError(f"色は4つ組で入れてください: {len(items)}つ")
    out = bytearray()
    for item in items:
        try:
            red, green, blue = item
            out.extend([int(red) & 0xFF, int(green) & 0xFF, int(blue) & 0xFF])
        except (TypeError, ValueError):
            raise ValueError(f"色の組が不正です: {item!r}") from None
    return bytes(out)


def config_reject_type(errcode: Any) -> int | None:
    """CONFIG拒否（0x10-0x1F）を拒否元の TYPE へ戻す。違えば None。"""
    try:
        code = int(errcode) & 0xFF
    except (TypeError, ValueError):
        return None
    if 0x10 <= code <= 0x1F:
        return 0x30 | (code & 0x0F)
    return None


def describe_bcon_errcode(errcode: Any) -> str:
    """STATUS errcodeの日本語説明。CONFIG拒否は拒否元の TYPE 名まで解く。"""
    try:
        code = int(errcode) & 0xFF
    except (TypeError, ValueError):
        return f"不明({errcode!r})"
    table = {
        0x00: "正常",
        0x01: "LEN不正",
        0x02: "CRC不一致",
        0x03: "SEQ欠番",
        0x04: "未対応",
        0x05: "UART overrun",
        0x06: "overflow",
    }
    if code in table:
        return str(table[code])
    rejected = config_reject_type(code)
    if rejected is not None:
        name = _CONFIG_TYPE_NAMES.get(rejected, f"0x{rejected:02X}")
        return f"CONFIG拒否（{name}・0x10+(TYPE&0x0F)=0x{code:02X}）"
    return f"不明(0x{code:02X})"


def is_bcon_transport(transport: Any) -> bool:
    """bconの運搬器か。bcon以外には CONFIG を送らないための門。"""
    if transport is None:
        return False
    try:
        if getattr(transport, "name", "") == "bcon":
            return True
        return getattr(transport, "capability", "") == BCON_STATE
    except Exception:
        return False


def send_config_frame(transport: Any, type_: int, payload: bytes) -> bool:
    """CONFIG系を1発で送る。公開口を使う。

    運搬器の公開口（send_config）があれば使い、無い旧実装だけ
    内部の会話口（_send_session_frame）へ落とす。口が無い・
    失敗は False で返し、落とさない。
    """
    if transport is None:
        return False
    try:
        kind = int(type_) & 0xFF
        body = bytes(payload)
    except (TypeError, ValueError):
        return False
    public = getattr(transport, "send_config", None)
    if callable(public):
        try:
            return bool(public(kind, body))
        except Exception:
            return False
    sender = getattr(transport, "_send_session_frame", None)
    if not callable(sender):
        return False
    try:
        return bool(
            sender(
                kind,
                body,
                f"bcon-setup:0x{kind:02X}",
            )
        )
    except Exception:
        return False
