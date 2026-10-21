#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""procon_spi.py - 実ProConのSPI較正を読む（S1用）。

使い方: uv run --frozen python tools/procon_spi.py

サブコマンドの応答は単一ハンドルでしか安定しないため、先に
Poke-Controllerを終了すること（別ハンドル同時openでは0x10が全ゼロを返す）。
読む範囲: 0x6020(6軸工場cal 24B), 0x8026(ユーザーcal magic+data),
0x6080(6軸水平オフセット 6B), 0x603D(スティック工場cal)。
"""

from __future__ import annotations

import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))

from core.nintendo_hid import NintendoHidReader  # noqa: E402

_READS = (
    (0x6020, 24, "6軸 工場キャリブ"),
    (0x8026, 26, "6軸 ユーザーキャリブ(magic+data)"),
    (0x6080, 6, "6軸 水平オフセット"),
    (0x603D, 18, "スティック 工場キャリブ"),
)


def _read(reader: NintendoHidReader, addr: int, size: int) -> bytes | None:
    """0x10 SPI readを1回。応答のdata部を返す。"""
    # 0x10 は「4byteアドレス(LE) + 1byteサイズ」。2byteアドレスでは
    # 実機が全ゼロを返す（ProCon実測）。
    reply = reader._subcmd_locked(0x10, struct.pack("<IB", addr, size))  # noqa: SLF001
    if reply is None or len(reply) < 20 + size:
        return None
    if reply[13] != 0x90 or reply[14] != 0x10:
        return None
    return reply[20 : 20 + size]


def _i16(data: bytes) -> list[int]:
    return [v for (v,) in struct.iter_unpack("<h", data[: len(data) // 2 * 2])]


def main() -> int:
    reader = NintendoHidReader()
    if not reader.open(retries=2):
        print("ProConを開けません（USB接続を確認）")
        return 1
    try:
        print("addr   size  name")
        for addr, size, name in _READS:
            data = _read(reader, addr, size)
            if data is None:
                print(f"0x{addr:04X} {size:3d}  {name}: 応答なし")
                continue
            print(f"0x{addr:04X} {size:3d}  {name}")
            print(f"  raw: {data.hex(' ')}")
            if addr == 0x6020:
                v = _i16(data)
                print(
                    "  acc_origin=%s acc_scale=%s gyro_origin=%s gyro_scale=%s"
                    % (v[0:3], v[3:6], v[6:9], v[9:12])
                )
            elif addr == 0x6080:
                v = _i16(data)
                print(f"  X={v[0]} Y={v[1]} Z={v[2]}")
            elif addr == 0x8026:
                magic = data[0:2]
                print(f"  magic={magic.hex(' ')} (B2 A1なら有効)")
                if magic == b"\xb2\xa1":
                    v = _i16(data[2:])
                    print(
                        "  acc_origin=%s acc_scale=%s gyro_origin=%s gyro_scale=%s"
                        % (v[0:3], v[3:6], v[6:9], v[9:12])
                    )
            time.sleep(0.03)
        return 0
    finally:
        reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
