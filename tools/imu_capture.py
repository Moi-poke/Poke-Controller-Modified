#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imu_capture.py - PicoがUSBへ出す0x30を直接記録する（S4用）。

使い方:
    uv run --frozen python tools/imu_capture.py --seconds 10
    uv run --frozen python tools/imu_capture.py --list
    uv run --frozen python tools/imu_capture.py --path 3 --seconds 10

PicoのUSBをPCへ繋いだ状態で使う（Switchには繋がない）。0x30を出すには
ハンドシェイク（80 04 と 0x03/0x30）をこちらから送る必要がある。
PC→PicoのIMUは別途UART（Poke-Controller等）から流しておくこと。

出力: レポート間隔の分布、Σω×5ms の整合、先頭数件の生値。
CSVは --csv <path> で保存。
"""

from __future__ import annotations

import argparse
import statistics
import struct
import time

import hid

VID = 0x057E
PID = 0x2009
REPORT_LEN = 64
RUMBLE_NEUTRAL = bytes([0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40])


def _list() -> list[dict]:
    return hid.enumerate(VID, PID)


def _open(path: bytes | None) -> hid.device:
    dev = hid.device()
    if path is not None:
        dev.open_path(path)
    else:
        dev.open(VID, PID)
    dev.set_nonblocking(False)
    return dev


def _write(dev: hid.device, data: bytes) -> int:
    return int(dev.write(bytes(data) + bytes(REPORT_LEN - len(data))))


def _read(dev: hid.device, timeout_ms: int = 200) -> bytes | None:
    data = dev.read(REPORT_LEN, timeout_ms=timeout_ms)
    return bytes(data) if data else None


def _handshake(dev: hid.device) -> None:
    """0x30を出させる最小の手順。応答は読み捨てる。"""
    for cmd in (bytes([0x80, 0x01]), bytes([0x80, 0x02]), bytes([0x80, 0x04])):
        _write(dev, cmd)
        time.sleep(0.05)
        for _ in range(2):
            _read(dev, 100)
    packet = 0
    for sub, arg in ((0x40, b"\x01"), (0x03, b"\x30")):
        _write(
            dev,
            bytes([0x01, packet & 0xF]) + RUMBLE_NEUTRAL + bytes([sub]) + arg,
        )
        packet += 1
        time.sleep(0.15)


def _slots(report: bytes) -> list[tuple[int, int, int, int, int, int]]:
    out = []
    for k in range(3):
        base = 13 + k * 12
        out.append(struct.unpack("<6h", report[base : base + 12]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="0x30キャプチャ")
    ap.add_argument("--list", action="store_true", help="候補を一覧して終了")
    ap.add_argument("--path", type=int, default=None, help="候補の番号")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--csv", default=None, help="CSV出力先")
    args = ap.parse_args()

    devs = _list()
    if args.list or not devs:
        print(f"候補 {len(devs)} 件 (VID=057E PID=2009)")
        for i, d in enumerate(devs):
            print(f"  [{i}] {d.get('product_string')} path={d.get('path')}")
        if args.list:
            return 0
        if not devs:
            return 1
    path = devs[args.path]["path"] if args.path is not None else devs[0]["path"]
    dev = _open(path)
    print(f"open: path={path}")
    try:
        _handshake(dev)
        stamps: list[float] = []
        rows: list[tuple[float, bytes]] = []
        end = time.perf_counter() + args.seconds
        while time.perf_counter() < end:
            r = _read(dev, 100)
            if r is None or len(r) < 49 or r[0] != 0x30:
                continue
            t = time.perf_counter()
            stamps.append(t)
            rows.append((t, r))
        print(f"0x30: {len(rows)} 件 / {args.seconds:.1f}s")
        if len(rows) < 2:
            return 1
        d = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
        print(
            "間隔ms: mean=%.2f median=%.2f min=%.2f max=%.2f"
            % (
                statistics.fmean(d),
                statistics.median(d),
                min(d),
                max(d),
            )
        )
        # 15ms窓のヒストグラム（1ms刻み、14-16に集中するのが正常）
        hist: dict[int, int] = {}
        for v in d:
            k = int(round(v))
            hist[k] = hist.get(k, 0) + 1
        top = sorted(hist.items(), key=lambda kv: -kv[1])[:5]
        print("間隔の上位: " + ", ".join(f"{k}ms x{v}" for k, v in top))
        if args.csv:
            with open(args.csv, "w", encoding="utf-8", newline="") as fh:
                fh.write(
                    "t_ms,slot0_ax,slot0_ay,slot0_az,slot0_gx,slot0_gy,slot0_gz,"
                    "slot1_ax,slot1_ay,slot1_az,slot1_gx,slot1_gy,slot1_gz,"
                    "slot2_ax,slot2_ay,slot2_az,slot2_gx,slot2_gy,slot2_gz\n"
                )
                t0 = stamps[0]
                for t, r in rows:
                    vals = [v for s in _slots(r) for v in s]
                    fh.write(
                        f"{(t - t0) * 1000.0:.3f}," + ",".join(map(str, vals)) + "\n"
                    )
            print(f"csv: {args.csv}")
        for t, r in rows[:3]:
            print(f"  t={t - stamps[0]:.3f}s slots={_slots(r)}")
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    raise SystemExit(main())
