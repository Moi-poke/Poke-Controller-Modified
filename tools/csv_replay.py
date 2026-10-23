#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""csv_replay.py - IMUビューアのCSVをPicoへそのまま再生する。

想定CSV: 1行目がコメント、2行目がヘッダ t_s,slot,ax,ay,az,gx,gy,gz。
送出は t_s の差分ではなく、1行=1サンプル=5ms固定（ビューアCSVは
1レポートの3スロットを同じt_sで書くため）。
Pure-Switch の記録（実Joy-Con R）を bcon 経由で流し、0x30へ
同じ3サンプルを載せて純正と同じ挙動になるかを確かめる。

使い方:
    uv run --frozen python tools/csv_replay.py rec.csv [COM3] [--loop]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time

import serial

sys.path.insert(0, "SerialController")

from core.transport.bcon_protocol import (  # noqa: E402
    T_IMU_SAMPLE,
    T_NEUTRAL,
    frame_build,
)


def _load(path: str) -> list[tuple[float, int, tuple[int, ...]]]:
    rows: list[tuple[float, int, tuple[int, ...]]] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            if row[0].strip() == "t_s":
                continue
            try:
                t_s = float(row[0])
                slot = int(row[1])
                vals = tuple(int(float(v)) for v in row[2:8])
            except (ValueError, IndexError):
                continue
            if len(vals) == 6:
                rows.append((t_s, slot, vals))
    rows.sort(key=lambda r: r[0])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="CSV IMU replay")
    ap.add_argument("csv")
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()

    rows = _load(args.csv)
    if not rows:
        print("再生できる行がありません")
        return 1
    print(f"{len(rows)} samples, {rows[0][0]:.3f}..{rows[-1][0]:.3f} s")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.05)
    except Exception as e:
        print(f"UARTを開けません: {e!r}")
        return 1
    seq = 0
    try:
        for _ in range(60):
            ser.write(frame_build(T_NEUTRAL, b"", seq))
            seq = (seq + 1) & 0xFF
            time.sleep(0.02)
        # ビューアCSVは1レポートの3スロットを同一t_sで3行に分けて書く。
        # t_sの差分ではなく「1行=1サンプル=5ms」で送る（指示）。
        period = 0.005
        while True:
            deadline = time.perf_counter()
            for _t_s, _slot, vals in rows:
                payload = b"".join(
                    int(v).to_bytes(2, "little", signed=True) for v in vals
                )
                ser.write(frame_build(T_IMU_SAMPLE, payload, seq))
                seq = (seq + 1) & 0xFF
                deadline += period
                delay = deadline - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    deadline = time.perf_counter()
            if not args.loop:
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
