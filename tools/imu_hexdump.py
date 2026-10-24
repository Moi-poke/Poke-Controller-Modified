#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imu_hexdump.py - PC→UART→FW→0x30 の各段の値を16進で採る。

PicoのUSBをPCへ、UARTをCOM3へ繋いだ状態で、IMU_SAMPLEを直接注入し、
同じPicoがUSBへ出す0x30の3スロット（byte13-48）を記録する。
アプリがCOM3を掴んでいると失敗する（閉じてから実行）。

使い方:
    uv run --frozen python tools/imu_hexdump.py [COM3]
"""

from __future__ import annotations

import argparse
import struct
import sys
import time

import hid
import serial

sys.path.insert(0, "SerialController")

from core.nintendo_hid import _RUMBLE_NEUTRAL  # noqa: E402
from core.transport.bcon_protocol import (  # noqa: E402
    T_IMU_SAMPLE,
    T_NEUTRAL,
    frame_build,
)

AX, AY, AZ = 0, 0, 4096
VECTORS = [
    ("AZ=4096 GZ=0", (AX, AY, AZ, 0, 0, 0)),
    ("AZ=4096 GZ=-1", (AX, AY, AZ, 0, 0, -1)),
    ("AZ=4096 GZ=+1", (AX, AY, AZ, 0, 0, 1)),
    ("AZ=4096 GZ=-16", (AX, AY, AZ, 0, 0, -16)),
    ("AZ=4096 GZ=+16", (AX, AY, AZ, 0, 0, 16)),
    ("AZ=4096 GZ=-32768", (AX, AY, AZ, 0, 0, -32768)),
    ("AZ=4096 GZ=+32767", (AX, AY, AZ, 0, 0, 32767)),
    ("AZ=4096 GX=-1", (AX, AY, AZ, -1, 0, 0)),
    ("AZ=4096 GY=-1", (AX, AY, AZ, 0, -1, 0)),
    ("AZ=4096 AX=-1", (-1, AY, AZ, 0, 0, 0)),
]


def _pico_path() -> bytes | None:
    """0x02がJoy-Con R(2)を返すHIDパス。Picoを選ぶ。"""
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


def _open_pico(path: bytes) -> "hid.device":
    dev = hid.device()
    dev.open_path(path)
    dev.set_nonblocking(False)

    def wr(b: bytes) -> None:
        dev.write(bytes(b) + bytes(64 - len(b)))

    for cmd in (bytes([0x80, 0x01]), bytes([0x80, 0x02]), bytes([0x80, 0x04])):
        wr(cmd)
        time.sleep(0.05)
        for _ in range(5):
            dev.read(64, timeout_ms=50)
    wr(bytes([0x01, 0]) + _RUMBLE_NEUTRAL + bytes([0x03, 0x30]) + bytes(52))
    time.sleep(0.05)
    wr(bytes([0x01, 1]) + _RUMBLE_NEUTRAL + bytes([0x40, 0x01]) + bytes(52))
    time.sleep(0.05)
    return dev


def main() -> int:
    ap = argparse.ArgumentParser(description="0x30 IMU hexdump")
    ap.add_argument("port", nargs="?", default="COM3")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, 1000000, timeout=0.05)
    except Exception as e:
        print(f"UARTを開けません（アプリが使用中？）: {e!r}")
        return 1
    path = _pico_path()
    if path is None:
        print("Pico(Joy-Con R, type=2)のHIDが見つかりません")
        ser.close()
        return 1
    dev = _open_pico(path)
    seq = 0
    try:
        for _ in range(60):
            ser.write(frame_build(T_NEUTRAL, b"", seq))
            seq = (seq + 1) & 0xFF
            time.sleep(0.02)
        for label, vec in VECTORS:
            payload = b"".join(int(v).to_bytes(2, "little", signed=True) for v in vec)
            reports: list[bytes] = []
            deadline = time.perf_counter() + 0.30
            next_tx = time.perf_counter()
            while time.perf_counter() < deadline:
                now = time.perf_counter()
                if now >= next_tx:
                    ser.write(frame_build(T_IMU_SAMPLE, payload, seq))
                    seq = (seq + 1) & 0xFF
                    next_tx += 0.005
                data = dev.read(64, timeout_ms=2)
                if data and data[0] == 0x30 and len(data) >= 49:
                    reports.append(bytes(data[13:49]))
            print(f"\n## {label}  reports={len(reports)}")
            for r in reports[:3]:
                slots = [struct.unpack_from("<6h", r, k * 12) for k in range(3)]
                print("   " + r.hex(" "))
                print(f"   slots acc/gyro: {slots}")
            if len(reports) > 3:
                uniq = {r.hex() for r in reports}
                print(f"   distinct payloads: {len(uniq)}")
    finally:
        dev.close()
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
