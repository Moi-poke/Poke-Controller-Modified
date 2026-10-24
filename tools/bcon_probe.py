#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bcon_probe.py - Picoと直接会話してFWの状態を確かめる診断道具。

使い方: uv run --frozen python tools/bcon_probe.py [COM3] [--baud 1000000]

アプリがCOM3を掴んでいると開けません。先にPoke-Controllerを終了してください。
HELLO_ACKの版（新FWかの確認）、STATUS_REQへの応答、PINGの往復を見る。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import serial

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))

from core.transport.bcon_protocol import (  # noqa: E402
    PROTO_SYNC,
    T_HELLO,
    T_HELLO_ACK,
    T_NEUTRAL,
    T_PING,
    T_PONG,
    T_STATUS,
    T_STATUS_REQ,
    crc8,
    frame_build,
)


def _scan(ser: serial.Serial, seconds: float) -> list[tuple[int, int, bytes]]:
    """SYNC基準でフレームを拾う。CRC不一致は捨てる。"""
    buf = bytearray()
    out: list[tuple[int, int, bytes]] = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(256)
        if chunk:
            buf.extend(chunk)
        while True:
            i = buf.find(bytes([PROTO_SYNC]))
            if i < 0:
                buf.clear()
                break
            if len(buf) < i + 5:
                del buf[:i]
                break
            ftype = buf[i + 1]
            ln = buf[i + 2]
            total = 1 + 1 + 1 + ln + 1 + 1
            if len(buf) < i + total:
                del buf[:i]
                break
            body = bytes(buf[i + 3 : i + 3 + ln])
            seq = buf[i + 3 + ln]
            crc = buf[i + 3 + ln + 1]
            frame = bytes(buf[i : i + total])
            del buf[: i + total]
            if crc8(frame[1 : 4 + ln]) != crc:
                continue
            out.append((ftype, seq, body))
        time.sleep(0.01)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="bcon UART直接プローブ")
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--baud", type=int, default=1000000)
    args = ap.parse_args()
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as e:
        print(f"開けません: {args.port} ({e!r})")
        return 1
    try:
        seq = 0
        # 呼び水 (baud hunt)。Picoは未確定の間 各rateに150msずつ滞在する
        # ため、短いバーストでは当たらない。約1.6秒連続で流して確実に掴む。
        # NEUTRALは姿勢のみで副作用が小さい。
        for _ in range(80):
            ser.write(frame_build(T_NEUTRAL, b"", seq))
            seq = (seq + 1) & 0xFF
            time.sleep(0.02)
        _scan(ser, 0.2)

        ser.write(frame_build(T_PING, b"", seq))
        seq = (seq + 1) & 0xFF
        pong = [f for f in _scan(ser, 0.8) if f[0] == T_PONG]
        print(f"PING: {'PONG受信' if pong else '応答なし'}")

        ser.write(frame_build(T_HELLO, bytes([4, 0]), seq))
        seq = (seq + 1) & 0xFF
        acks = [f for f in _scan(ser, 1.0) if f[0] == T_HELLO_ACK]
        if acks and len(acks[0][2]) >= 4:
            body = acks[0][2]
            print(
                f"HELLO_ACK: ver={body[0]} major={body[1]}"
                f" minor={body[2]} result={body[3]}"
            )
        else:
            print("HELLO_ACK: 応答なし")

        ser.write(frame_build(T_STATUS_REQ, b"", seq))
        seq = (seq + 1) & 0xFF
        st = [f for f in _scan(ser, 1.0) if f[0] == T_STATUS]
        if st:
            body = st[0][2]
            print(
                "STATUS: flags=0x%02X last=0x%02X crc16=%d drop16=%d err=0x%02X"
                % (
                    body[0],
                    body[1],
                    body[2] | (body[3] << 8),
                    body[4] | (body[5] << 8),
                    body[6],
                )
            )
        else:
            print("STATUS: 応答なし（誤表示バグを再現）")
        return 0
    finally:
        ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
