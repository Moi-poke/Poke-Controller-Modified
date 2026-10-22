#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spi_dump.py - PicoのUSB HID経由でSPI応答を任意範囲ダンプする。

使い方:
    uv run --frozen python tools/spi_dump.py [--start 0x6000] [--end 0x8040]
実Joy-Con R到着後は、同じツールで純正の応答を採り、FWの表と比較する。
"""

from __future__ import annotations

import argparse
import sys
import time

import hid

sys.path.insert(0, "SerialController")

from core.nintendo_hid import _RUMBLE_NEUTRAL  # noqa: E402


def _find_pico() -> bytes | None:
    """0x02が2(Joy-Con R)を返す個体を選ぶ。"""
    for d in hid.enumerate(0x057E, 0):
        path = d.get("path")
        if not path:
            continue
        dev = hid.device()
        try:
            dev.open_path(path)
            dev.set_nonblocking(False)
            for cmd in (bytes([0x80, 0x01]), bytes([0x80, 0x02]), bytes([0x80, 0x04])):
                dev.write(bytes(cmd) + bytes(64 - len(cmd)))
                time.sleep(0.05)
                for _ in range(5):
                    dev.read(64, timeout_ms=50)
            dev.write(bytes([0x01, 0]) + _RUMBLE_NEUTRAL + bytes([0x02]) + bytes(53))
            typ = None
            for _ in range(30):
                r = dev.read(64, timeout_ms=100)
                if r and len(r) > 17 and r[0] in (0x21, 0x81) and r[14] == 0x02:
                    typ = r[17]
                    break
            if typ == 2:
                return bytes(path)
        except Exception:
            pass
        finally:
            try:
                dev.close()
            except Exception:
                pass
    return None


def _open(path: bytes) -> "hid.device":
    dev = hid.device()
    dev.open_path(path)
    dev.set_nonblocking(False)
    wr = lambda b: dev.write(bytes(b) + bytes(64 - len(b)))  # noqa: E731
    for cmd in (bytes([0x80, 0x01]), bytes([0x80, 0x02]), bytes([0x80, 0x04])):
        wr(cmd)
        time.sleep(0.05)
        for _ in range(5):
            dev.read(64, timeout_ms=50)
    return dev


def _spi(dev: "hid.device", addr: int, n: int) -> bytes | None:
    b = bytes(
        [0x01, 0, *_RUMBLE_NEUTRAL, 0x10, addr & 0xFF, (addr >> 8) & 0xFF, 0, 0, n]
    )
    dev.write(b + bytes(64 - len(b)))
    deadline = time.perf_counter() + 0.6
    while time.perf_counter() < deadline:
        r = dev.read(64, timeout_ms=60)
        if r and len(r) > 20 and r[0] in (0x21, 0x81) and r[14] == 0x10:
            return bytes(r[20 : 20 + n])
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="SPI range dump")
    ap.add_argument("--start", type=lambda s: int(s, 0), default=0x6000)
    ap.add_argument("--end", type=lambda s: int(s, 0), default=0x8040)
    ap.add_argument("--chunk", type=int, default=0x1C, help="<=44")
    args = ap.parse_args()
    path = _find_pico()
    if path is None:
        print("Pico(Joy-Con R)が見つかりません")
        return 1
    dev = _open(path)
    try:
        addr = args.start
        while addr < args.end:
            n = min(args.chunk, args.end - addr)
            data = _spi(dev, addr, n)
            print(f"{addr:#06x}: {data.hex(' ') if data else 'none'}")
            addr += n
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
