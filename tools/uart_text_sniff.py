#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uart_text_sniff.py - PicoのUARTに出る診断行（U01等）を拾う。

FWは0x01サブコマンドごとに "U01 s=<sub> d=<arg> hs=<0|1>" をUARTへ出す。
アプリがCOM3を使っている間は使えない（停止してから実行）。PicoはSwitchへ
USB接続したままでよい（UARTはPCのアダプタへ）。

使い方:
    uv run --frozen python tools/uart_text_sniff.py [COM3] [--seconds 60]
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import serial

sys.path.insert(0, "SerialController")

from core.transport.bcon_protocol import (  # noqa: E402
    PROTO_VER,
    T_HELLO,
    T_NEUTRAL,
    frame_build,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="UART診断行スニファ")
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--ping", action="store_true", help="NEUTRALを送ってTXを起こす")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as e:
        print(f"UARTを開けません（アプリが使用中？）: {e!r}")
        return 1
    print(f"listening {args.port} @ {args.baud} ... (Ctrl+Cで終了)")
    # TX抑制解除のため、HELLOを1発送ってから待つ。
    ser.write(frame_build(T_HELLO, bytes([PROTO_VER, 0]), 0))
    buf = b""
    seq = 0
    end = time.perf_counter() + args.seconds
    next_ping = 0.0
    try:
        while time.perf_counter() < end:
            if args.ping and time.perf_counter() >= next_ping:
                ser.write(frame_build(T_NEUTRAL, b"", seq))
                seq = (seq + 1) & 0xFF
                next_ping = time.perf_counter() + 0.02
            chunk = ser.read(256)
            if not chunk:
                continue
            buf += chunk
            # 改行で区切り、印字可能な行だけ出す（バイナリは捨てる）
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("ascii", errors="ignore").strip("\r\x00")
                text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
                text = re.sub(r"\s+", " ", text).strip()
                if text and (
                    text.startswith("U0") or text.startswith("U8") or "S40" in text
                ):
                    print(f"{time.strftime('%H:%M:%S')}  {text}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
