"""bcon_fw_e2e.py - Switch-bcon FW の回帰を実機で確かめる (2026-10-05 の修正群)。

使い方 (evidence-dir は絶対パスで、まだ存在しないこと):
    uv run --frozen python tools/bcon_fw_e2e.py autostatus --port COM3 --evidence-dir D:\\ev\\a1
    uv run --frozen python tools/bcon_fw_e2e.py neutral    --port COM3 --evidence-dir D:\\ev\\n1
    uv run --frozen python tools/bcon_fw_e2e.py ring       --port COM3 --evidence-dir D:\\ev\\r1

配線:
    autostatus / neutral  PC の TX/RX を Pico のデータ UART (GP4/GP5) に直結。1Mbps。
    ring                  アダプタ1本で TX→GP5 (データ RX)、RX←GP0 (ログ TX)。115200。
                          Pico の応答は読めないので、ログの BCON 行で判定する。

検査:
    autostatus  HELLO flags bit0 (自動STATUS) で落ちないこと。HELLOなし・flags=0 を対照に
                各段6秒 STATUS を取り続け、途絶 (=WDT再起動) が無いこと。
                旧FW は自動STATUS の初回 (約1秒後) で Core0 スタックが溢れて落ちた。
    neutral     A押下STATE の後 PING だけを送り続けても 200ms で中立に戻ること
                (STATUS bit2)。無送信を対照、STATE連送中は戻らないことも見る。
    ring        BREAK→目標rateで STATE 送出→確定idxが目標と一致し、受理数が送信数を
                超えないこと (旧FW はリングの過去 frame を再生して別 rate に偽lock した)。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

import serial

_CONTROLLER_DIR = os.path.join(os.path.dirname(__file__), "..", "SerialController")
if _CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, _CONTROLLER_DIR)

from core.transport.bcon_protocol import (  # noqa: E402
    BAUD_TABLE,
    T_HELLO,
    T_PING,
    T_STATE,
    T_STATUS,
    T_STATUS_REQ,
    frame_build,
)

NEUTRAL_STATE = bytes([0, 0, 0, 0, 0x80, 0x80, 0x80, 0x80])
A_STATE = bytes([0x02, 0, 0, 0, 0x80, 0x80, 0x80, 0x80])
ST_TIMEOUT_NEUTRAL = 0x04


class Wire:
    """1本の線に採番つきで frame を書き、STATUS を読む。"""

    def __init__(self, ser: serial.Serial) -> None:
        self.ser = ser
        self.seq = 0

    def tx(self, type_: int, payload: bytes = b"") -> None:
        self.ser.write(frame_build(type_, payload, self.seq))
        self.seq = (self.seq + 1) & 0xFF

    def status_flags(self, wait_s: float = 0.4) -> int | None:
        self.ser.reset_input_buffer()
        self.tx(T_STATUS_REQ)
        head = bytes([0xAB, T_STATUS, 7])
        end = time.time() + wait_s
        buf = b""
        while time.time() < end:
            buf += self.ser.read(256)
            i = buf.find(head)
            if i >= 0 and len(buf) >= i + 12:
                return buf[i + 3]
        return None

    def stream(self, payload: bytes, count: int, gap_s: float) -> None:
        for _ in range(count):
            self.tx(T_STATE, payload)
            time.sleep(gap_s)

    def relock(self, max_s: float = 8.0) -> int | None:
        """STATE を流して hunt 中でも確定させ、STATUS が返るまで待つ。"""
        end = time.time() + max_s
        while time.time() < end:
            self.stream(NEUTRAL_STATE, 140, 0.005)
            flags = self.status_flags()
            if flags is not None:
                return flags
        return None


def check_autostatus(port: str) -> dict[str, Any]:
    wire = Wire(serial.Serial(port, 1000000, timeout=0.02))
    rows: list[dict[str, Any]] = []
    for stage in ("none", "hello0", "hello1", "hello1", "hello1"):
        start_flags = wire.relock()
        if stage == "hello0":
            wire.tx(T_HELLO, bytes([0x04, 0x00]))
        elif stage == "hello1":
            wire.tx(T_HELLO, bytes([0x04, 0x01]))
        t0 = time.time()
        lost: float | None = None
        seen = 0
        while time.time() - t0 < 6.0:
            if wire.status_flags() is None:
                lost = round(time.time() - t0, 2)
                break
            seen += 1
            time.sleep(0.3)
        rows.append(
            {
                "stage": stage,
                "start_flags": start_flags,
                "lost_after_s": lost,
                "n": seen,
            }
        )
        print(json.dumps(rows[-1]), flush=True)
    wire.ser.close()
    return {"rows": rows, "pass": all(r["lost_after_s"] is None for r in rows)}


def check_neutral(port: str) -> dict[str, Any]:
    wire = Wire(serial.Serial(port, 1000000, timeout=0.02))
    wire.relock()
    rows: list[dict[str, Any]] = []
    for mode in ("ping", "silent", "ping", "silent"):
        wire.stream(A_STATE, 30, 0.005)
        t0 = time.time()
        while time.time() - t0 < 0.6:
            if mode == "ping":
                wire.tx(T_PING)
            time.sleep(0.1)
        flags = wire.status_flags()
        hold = None if flags is None else bool(flags & ST_TIMEOUT_NEUTRAL)
        rows.append({"mode": mode, "flags": flags, "timeout_neutral": hold})
        print(json.dumps(rows[-1]), flush=True)
    # 連送中は戻らないこと (過剰発動の否定)。
    streaming: list[bool | None] = []
    for _ in range(5):
        wire.stream(A_STATE, 60, 1 / 120)
        flags = wire.status_flags()
        streaming.append(None if flags is None else bool(flags & ST_TIMEOUT_NEUTRAL))
    wire.tx(T_STATE, NEUTRAL_STATE)
    wire.ser.close()
    ok = all(r["timeout_neutral"] for r in rows) and streaming == [False] * 5
    return {"rows": rows, "while_streaming": streaming, "pass": ok}


def _bcon_fields(lines: list[str]) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    for line in lines:
        if line.startswith("BCON t="):
            kv = dict(p.split("=", 1) for p in line.split() if "=" in p)
            last = {"frames": int(kv["frames"]), "baud": kv["baud"]}
    return last


def check_ring(port: str, log_path: str) -> dict[str, Any]:
    ser = serial.Serial(port, 115200, timeout=0.05)
    idx_of = {v: k for k, v in BAUD_TABLE.items()}
    seq = 0
    with open(log_path, "w", encoding="utf-8", newline="") as logf:

        def read_log(secs: float) -> dict[str, Any] | None:
            end = time.time() + secs
            buf = b""
            while time.time() < end:
                buf += ser.read(4096)
            lines = buf.decode("utf-8", "replace").splitlines()
            for line in lines:
                logf.write(f"[{datetime.now():%H:%M:%S.%f}] {line.rstrip()}\n")
            logf.flush()
            return _bcon_fields(lines)

        def send(rate: int, count: int, gap_s: float) -> None:
            nonlocal seq
            ser.baudrate = rate
            for _ in range(count):
                ser.write(frame_build(T_STATE, NEUTRAL_STATE, seq))
                seq = (seq + 1) & 0xFF
                time.sleep(gap_s)
            ser.flush()
            time.sleep(0.05)
            ser.baudrate = 115200

        rows: list[dict[str, Any]] = []
        for rate in (115200, 460800, 921600, 1000000, 1000000):
            before = read_log(2.2)
            ser.send_break(0.1)
            time.sleep(0.05)
            send(rate, 120, 0.01)
            locked = read_log(2.2)
            send(rate, 40, 0.005)
            after = read_log(2.2)
            ok = (
                before is not None
                and locked is not None
                and after is not None
                and locked["baud"] == f"{idx_of[rate]}L"
                and locked["frames"] - before["frames"] <= 120
                and after["frames"] - locked["frames"] == 40
            )
            rows.append(
                {
                    "target": rate,
                    "before": before,
                    "locked": locked,
                    "after": after,
                    "pass": ok,
                }
            )
            print(json.dumps(rows[-1]), flush=True)
    ser.close()
    return {"rows": rows, "pass": all(r["pass"] for r in rows)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("check", choices=("autostatus", "neutral", "ring"))
    ap.add_argument("--port", required=True)
    ap.add_argument("--evidence-dir", required=True)
    args = ap.parse_args()

    evidence = str(args.evidence_dir)
    if not os.path.isabs(evidence):
        print("evidence-dir は絶対パスで指定してください。", file=sys.stderr)
        return 2
    if os.path.exists(evidence):
        print(f"evidence-dir が既にあります: {evidence}", file=sys.stderr)
        return 2
    os.makedirs(evidence)

    if args.check == "autostatus":
        result = check_autostatus(args.port)
    elif args.check == "neutral":
        result = check_neutral(args.port)
    else:
        result = check_ring(args.port, os.path.join(evidence, "log.txt"))
    report = {
        "check": args.check,
        "when": datetime.now().isoformat(timespec="seconds"),
        "port": args.port,
        **result,
    }
    with open(os.path.join(evidence, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"OVERALL {'PASS' if report['pass'] else 'FAIL'}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
