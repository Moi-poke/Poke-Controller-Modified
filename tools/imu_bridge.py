#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imu_bridge.py - 実ProConのIMUをPico(UART)へ中継する。

使い方: uv run --frozen python tools/imu_bridge.py [COM3] [--baud 1000000]

Poke-Controllerを使わずに、ProConの0x30（最新スロット）を約200Hzで
T_IMU_SAMPLEとしてPicoへ送る。PicoのUSBをPCに出してWebHID等で
0x30を観察するとき用。Ctrl+Cで止める。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import serial

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))

from core.nintendo_hid import NintendoHidReader  # noqa: E402
from core.transport.bcon_protocol import (  # noqa: E402
    T_IMU_SAMPLE,
    T_NEUTRAL,
    frame_build,
)


def _procon_path() -> bytes | None:
    """0x02応答が種別3(ProCon)のHIDパスを返す。"""
    import hid

    for d in hid.enumerate(0x057E, 0):
        path = d.get("path")
        if not path:
            continue
        dev = hid.device()
        try:
            dev.open_path(path)
            dev.set_nonblocking(False)
            dev.write(bytes([0x80, 0x01]) + bytes(62))
            time.sleep(0.05)
            dev.write(
                bytes([0x01, 0])
                + bytes([0x00, 0x01, 0x40, 0x40, 0, 1, 0x40, 0x40])
                + bytes([0x02])
                + bytes(54)
            )
            for _ in range(6):
                r = dev.read(64, timeout_ms=150)
                if r and len(r) >= 18 and r[0] == 0x21 and r[14] == 0x02:
                    if r[17] == 3:
                        return bytes(path)
                    break
        except Exception:
            pass
        finally:
            try:
                dev.close()
            except Exception:
                pass
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="ProCon IMU → Pico 中継")
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--baud", type=int, default=1000000)
    args = ap.parse_args()

    path = _procon_path()
    if path is None:
        print("ProCon(type=3)が見つかりません")
        return 1
    reader = NintendoHidReader()
    if not reader.open(path=path, retries=2):
        print("ProConを開けません")
        return 1
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as e:
        print(f"UARTを開けません: {e!r}")
        reader.close()
        return 1
    seq = 0
    try:
        for _ in range(60):
            ser.write(frame_build(T_NEUTRAL, b"", seq))
            seq = (seq + 1) & 0xFF
            time.sleep(0.02)
        print("中継開始 (Ctrl+Cで停止)")
        n = 0
        t_end = time.perf_counter() + 1.0
        while True:
            sample = reader.read()
            if sample is None:
                time.sleep(0.002)
                continue
            payload = b"".join(
                int(v).to_bytes(2, "little", signed=True) for v in sample
            )
            ser.write(frame_build(T_IMU_SAMPLE, payload, seq))
            seq = (seq + 1) & 0xFF
            n += 1
            time.sleep(0.005)
            if time.perf_counter() > t_end:
                t_end += 1.0
                print(f"  {n} Hz", flush=True)
                n = 0
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
