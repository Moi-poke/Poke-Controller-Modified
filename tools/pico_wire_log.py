"""実線のS行を時刻付きで記録する（入力抜けの切り分け用）。

使い方:
    uv run --frozen python tools/pico_wire_log.py --port COM3 --duration 0.016 --times 100

press dwell＝press行→release行の間隔を測り、press行なしでrelease行が
来た回数（ワイヤ抜け）を数える。Switch本体の入力テスト画面と併用し、
ワイヤOK／表示NGを分離する。
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from datetime import datetime

import serial
from loguru import logger

# ---------------------------------------------------------------------------
# ボタン表は tools/pico_probe.py の BTN と同じ値を使う。
# 16進を手書きすると桁を取り違えるので、必ず名前で指定する。
# ---------------------------------------------------------------------------
BTN = {
    "Y": 0x0001,
    "B": 0x0002,
    "A": 0x0004,
    "X": 0x0008,
    "L": 0x0010,
    "R": 0x0020,
    "ZL": 0x0040,
    "ZR": 0x0080,
    "MINUS": 0x0100,
    "PLUS": 0x0200,
    "LCLICK": 0x0400,
    "RCLICK": 0x0800,
    "HOME": 0x1000,
    "CAPTURE": 0x2000,
}

HAT_CENTER = 8
STICK_CENTER = 0x80

# ★Pico 側 pico_main.c の #define BAUD_RATE と必ず同じにする。
BAUD_RATE = 115200


def drain(ser: serial.Serial) -> None:
    """受信済みを読み捨てる。ERR を含む行だけ画面に出す。

    読まずに送り続けると受信バッファが溜まるので毎回捨てる。
    書式異常の手掛かりになる ERR だけは黙って捨てずに表示する。
    """

    data = ser.read_all()
    if not data:
        return
    for line in data.decode("ascii", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        # 正常応答（PONG/OK/READY など）は捨てる。異常だけ見せる。
        if "ERR" in line:
            print(f"  <- {line}")


def send_state(
    ser: serial.Serial,
    btn: int = 0,
    hat: int = HAT_CENTER,
    lx: int = STICK_CENTER,
    ly: int = STICK_CENTER,
    rx: int = STICK_CENTER,
    ry: int = STICK_CENTER,
) -> str:
    """姿勢を1行送る。書式は pico_probe.py の send_state と同じ。"""

    line = f"S {btn:04x} {hat:x} {lx:02x} {ly:02x} {rx:02x} {ry:02x}\n"
    ser.write(line.encode("ascii"))
    return line.strip()


def build_parser() -> argparse.ArgumentParser:
    """引数組み立て。--port だけ必須にする。"""

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--button", default="A", choices=sorted(BTN))
    ap.add_argument("--duration", type=float, default=0.016)
    ap.add_argument("--times", type=int, default=100)
    # press を離したあと、次の press までの休み時間。
    ap.add_argument("--gap", type=float, default=0.5)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    code: int = BTN[args.button]
    duration: float = args.duration
    gap: float = args.gap
    times: int = args.times

    # loguru が書く log/ と同じ場所へ CSV を置く。無ければ作る。
    os.makedirs("log", exist_ok=True)
    csv_path = os.path.join(
        "log", f"pico_wire_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )

    # 開線失敗は traceback を出さず理由の1行だけ出す。
    # TextSerialTransport._open_serial と同じ作法（print＋logger.error）。
    try:
        ser = serial.Serial(args.port, BAUD_RATE, timeout=0.5)
    except (OSError, serial.SerialException, ValueError) as e:
        print("COM Port: can't be established")
        logger.error(f"COM Port: can't be established: {e}")
        return 1

    with ser:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["t", "line", "kind"])

            # 起動直後の READY を捨てる。残すと後の ERR 判定がずれる。
            time.sleep(0.3)
            drain(ser)

            # 死活確認。PONG が返れば往復できている。
            ser.write(b"P\n")
            writer.writerow([f"{time.perf_counter():.6f}", "P", "other"])
            time.sleep(0.2)
            drain(ser)

            print(f"-- {args.button} (0x{code:04x}) を {times} 回送ります --")
            print(f"-- CSV: {csv_path} --")
            try:
                for _ in range(times):
                    # 送出直後の時刻を取る。前後2点は要らないので直後1点に統一する。
                    # kind は送った側が知っているので受信解析はしない。
                    press = send_state(ser, btn=code)
                    writer.writerow([f"{time.perf_counter():.6f}", press, "press"])
                    time.sleep(duration)
                    release = send_state(ser)
                    writer.writerow([f"{time.perf_counter():.6f}", release, "release"])
                    time.sleep(gap)
                    drain(ser)
            finally:
                # 終わったら必ず全解放。押しっぱなしで終わらせない。
                ser.write(b"N\n")
                writer.writerow([f"{time.perf_counter():.6f}", "N", "other"])
                time.sleep(0.2)
                drain(ser)
                print("-- 終了。中立化を送りました --")
                print(f"-- CSV: {csv_path} --")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
