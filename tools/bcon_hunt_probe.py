"""bcon_hunt_probe.py - BconTransport.baud_hunt() が hunt 中の Pico を確定できるか測る。

使い方:
    uv run --frozen python tools/bcon_hunt_probe.py --port COM3 \
        --evidence-dir %TEMP%\\pokecon-hunt-probe\\run-1 --trials 5

PC の TX/RX を Pico のデータ UART (GP4/GP5) に直結しておくこと。
1 試行 = BREAK を送って Pico を hunt (baud 未確定) に戻す → 新しい
BconTransport で baud_hunt() → request_status() で疎通を確かめる。
判定は全試行で lock できて STATUS が返ること。

証拠: <evidence-dir>/report.json。evidence-dir は絶対パスで、まだ存在しない
こと (使い回しは上書きせず失敗させる)。
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

from core.transport.bcon import BconTransport  # noqa: E402

# Pico の BREAK 判定は 20ms 以上の low (src/main.c BREAK_LOW_MS)。余裕を見る。
BREAK_S = 0.1


def _send_break(port: str, baud: int) -> None:
    """別ハンドルで BREAK だけ送って閉じる (Transport に BREAK の口は無い)。"""
    with serial.Serial(port, baud, timeout=0.1) as ser:
        ser.send_break(BREAK_S)
        time.sleep(0.05)


def run_trial(port: str, baud: int, index: int) -> dict[str, Any]:
    _send_break(port, baud)
    made = BconTransport()
    row: dict[str, Any] = {"trial": index}
    if not made.open(0, portName=port, baudrate=baud):
        row.update({"opened": False, "pass": False})
        return row
    try:
        start = time.perf_counter()
        locked = made.baud_hunt()
        row["hunt_s"] = round(time.perf_counter() - start, 3)
        row["locked"] = locked
        status = made.request_status(timeout=1.0, quiet=True) if locked else None
        row["status"] = status
        row["pass"] = locked is not None and status is not None
    finally:
        made.close()
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--trials", type=int, default=5)
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

    rows: list[dict[str, Any]] = []
    for i in range(max(1, int(args.trials))):
        row = run_trial(args.port, int(args.baud), i)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        time.sleep(0.5)

    report = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "port": args.port,
        "baud": int(args.baud),
        "trials": rows,
        "passed": sum(1 for r in rows if r.get("pass")),
        "pass": all(r.get("pass") for r in rows),
    }
    with open(os.path.join(evidence, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(
        f"OVERALL {'PASS' if report['pass'] else 'FAIL'} {report['passed']}/{len(rows)}"
    )
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
