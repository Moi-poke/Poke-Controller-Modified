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

from core.nintendo_hid import _RUMBLE_NEUTRAL, NintendoHidReader  # noqa: E402
from core.transport.bcon_protocol import (  # noqa: E402
    T_EMULATE_MODE,
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
            # 既存セッションの後では 80 01 だけでは応答しない。80 02/80 04 まで
            # 送ってから種別を問い合わせる（実測）。
            for cmd in (bytes([0x80, 0x01]), bytes([0x80, 0x02]), bytes([0x80, 0x04])):
                dev.write(bytes(cmd) + bytes(64 - len(cmd)))
                time.sleep(0.05)
                # 0x30連送中でもコマンドが通るよう多めに読み捨てる（実測）。
                for _ in range(5):
                    dev.read(64, timeout_ms=60)
            dev.write(bytes([0x01, 0]) + _RUMBLE_NEUTRAL + bytes([0x02]) + bytes(53))
            for _ in range(40):
                r = dev.read(64, timeout_ms=100)
                if r and len(r) >= 18 and r[0] in (0x21, 0x81) and r[14] == 0x02:
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
    ap.add_argument(
        "--role",
        type=int,
        choices=(0, 1, 2),
        default=None,
        help="EMULATE_MODEを送る (2=Joy-Con R)。変更時はFWが自動再起動する",
    )
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
        if args.role is not None:
            for _ in range(3):
                ser.write(frame_build(T_EMULATE_MODE, bytes([args.role]), seq))
                seq = (seq + 1) & 0xFF
                time.sleep(0.05)
            print(f"EMULATE={args.role} を送信。再起動待ち...", flush=True)
            ser.close()
            time.sleep(3.0)
            ser = serial.Serial(args.port, args.baud, timeout=0.05)
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
