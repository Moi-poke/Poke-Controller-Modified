"""press_probe.py - 押下→Switch音の到達率を自動計数する。

使い方:
    uv run --frozen python tools/press_probe.py --port COM3 --audio-device 2 --duration 0.008 --times 30
    uv run --frozen python tools/press_probe.py --list-audio

S行はdwellのあいだ8ms毎に繰り返す（実コントローラー＋新schedulerと同一条件）。
単発条件との比較は tools/pico_wire_log.py を使う（単発で消えるかで
Pico側のrepeat要求の有無が分かる）。
計数は core/audio_latency.measure_press（立ち上がり検出）で行い、
heard/total＋遅延中央値を出す。耳での数え間違いを排除するための道具。
Switchはボタン入力テスト等の発音画面にしておくこと（BGM画面は誤検出する）。
"""

from __future__ import annotations

import argparse
import atexit
import csv
import os
import re
import sys
import time
from datetime import datetime
from typing import Any

import serial
from loguru import logger

_CONTROLLER_DIR = os.path.join(os.path.dirname(__file__), "..", "SerialController")
if _CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, _CONTROLLER_DIR)

from core import (  # noqa: E402
    AudioCapture as AC,  # noqa: E402
    audio_latency as AL,  # noqa: E402
)
from core.AudioCapture import AudioCapture  # noqa: E402

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


def _begin_timer_period() -> None:
    """Windows のタイマー分解能を 1ms に上げる。

    8ms刻みの再送を time.sleep で刻むため。上げないと既定約15.6msへ
    丸められ、dwellが要求より延びる（短くはならないが条件がずれる）。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
        atexit.register(ctypes.windll.winmm.timeEndPeriod, 1)
    except Exception:
        logger.warning("timeBeginPeriod is unavailable; timer stays coarse")


def send_state(
    ser: serial.Serial,
    writer: Any,
    btn: int = 0,
    hat: int = HAT_CENTER,
    lx: int = STICK_CENTER,
    ly: int = STICK_CENTER,
    rx: int = STICK_CENTER,
    ry: int = STICK_CENTER,
    kind: str = "other",
    padded: bool = True,
) -> str:
    """姿勢を1行送り、送出時刻つきでCSVへ残す。

    padded=True は pico_probe と同じ4桁ゼロ埋め（"S 0004 ..."）。
    padded=False はアプリの encode_pico_state と同じ短縮形（"S 4 ..."）。
    アプリ経路との差が書式由来かを分ける対照条件のための切替である。
    """
    btn_text = f"{btn:04x}" if padded else format(btn, "x")
    line = f"S {btn_text} {hat:x} {lx:02x} {ly:02x} {rx:02x} {ry:02x}\n"
    ser.write(line.encode("ascii"))
    text = line.strip()
    writer.writerow([f"{time.perf_counter():.6f}", text, kind])
    return text


def read_mon_counts(lines: list[str]) -> dict[str, int]:
    """Mモニタの press/ok/ng を読む。無ければ空辞書。

    PicoはS行の成否を応答しない（計数するのみ）。M表示の2行目
    `mon empty=.. reply=.. press=.. ok=.. ng=..` が唯一の手掛かり。
    """
    found: dict[str, int] = {}
    for line in lines:
        m = re.search(r"press=(\d+)\s+ok=(\d+)\s+ng=(\d+)", line)
        if m:
            found = {
                "press": int(m.group(1)),
                "ok": int(m.group(2)),
                "ng": int(m.group(3)),
            }
    return found


def _mon_state(lines: list[str]) -> bool | None:
    """直近の monitor on/off 応答。無ければNone。"""
    for line in reversed(lines):
        s = line.strip()
        if s == "monitor on":
            return True
        if s == "monitor off":
            return False
    return None


def mon_snapshot(ser: serial.Serial) -> dict[str, int]:
    """Mを確実にONにし、heartbeatのmon行を読む。

    M自体の即時応答（monitor on/off）で開閉を確定する。
    heartbeat待ちだけでは、Pico多忙時の遅延と開閉不明が区別できない。
    """
    buf: list[str] = []
    try:
        for _ in range(4):
            ser.write(b"M\n")
            time.sleep(0.7)
            chunk = ser.read_all()
            if chunk:
                buf.extend(chunk.decode("ascii", "replace").splitlines())
            if _mon_state(buf) is True:
                break
        time.sleep(1.5)
        chunk = ser.read_all()
        if chunk:
            buf.extend(chunk.decode("ascii", "replace").splitlines())
    except Exception:
        return {}
    return read_mon_counts(buf)


def mon_off(ser: serial.Serial) -> None:
    """Mを消して残渣を捨てる。"""
    ser.write(b"M\n")
    time.sleep(0.2)
    ser.read_all()


def build_parser() -> argparse.ArgumentParser:
    """引数組み立て。--list-audio のときは --port を求めない。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="")
    ap.add_argument("--button", default="A", choices=sorted(BTN))
    ap.add_argument("--duration", type=float, default=0.008)
    ap.add_argument("--times", type=int, default=30)
    # dwell中の再送間隔（秒）。0で単発（pico_wire_log相当の対照条件）。
    ap.add_argument("--repeat-ms", type=float, default=8.0)
    # 送信行の書式。padded は pico_probe 準拠、short はアプリ準拠。
    ap.add_argument("--row-format", default="padded", choices=("padded", "short"))
    # 改行。lf は素ツール準拠、crlf はアプリ（TextSerialTransport）準拠。
    ap.add_argument("--line-ending", default="lf", choices=("lf", "crlf"))
    ap.add_argument("--audio-device", default=None)
    ap.add_argument("--list-audio", action="store_true")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.list_audio:
        for index, name in AC.device_entries(True):
            print(f"{index}: {name}")
        return 0
    if not args.port:
        print("--port が必要です（--list-audio を除く）")
        return 1
    code: int = BTN[args.button]
    duration = max(0.0, float(args.duration))
    times = max(1, int(args.times))
    repeat_s = max(0.0, float(args.repeat_ms)) / 1000.0
    padded = str(args.row_format) != "short"

    _begin_timer_period()

    os.makedirs("log", exist_ok=True)
    csv_path = os.path.join(
        "log", f"press_probe_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )

    try:
        ser = serial.Serial(args.port, BAUD_RATE, timeout=0.5)
    except (OSError, serial.SerialException, ValueError) as e:
        print("COM Port: can't be established")
        logger.error(f"COM Port: can't be established: {e}")
        return 1

    capture = AudioCapture()
    if not capture.openInput(args.audio_device):
        print("音声入力を開けませんでした")
        logger.error("音声入力を開けませんでした")
        ser.close()
        return 1

    def send_press() -> float:
        # dwell中はrepeat毎に同一行を送り続ける（実機＋schedulerと同条件）。
        # 単発だとPico側のrepeat要求の有無と切り分けられないため。
        t0 = time.perf_counter()
        send_state(ser, writer, btn=code, kind="press", padded=padded)
        end = t0 + duration
        while True:
            now = time.perf_counter()
            if now >= end:
                break
            if repeat_s <= 0.0:
                time.sleep(end - now)
                break
            time.sleep(min(repeat_s, end - now))
            send_state(ser, writer, btn=code, kind="press", padded=padded)
        # 解放は念のため複数回送る。1行ロスで押しっぱなしにしないため。
        send_state(ser, writer, kind="release", padded=padded)
        if repeat_s > 0.0:
            for _ in range(2):
                time.sleep(repeat_s)
                send_state(ser, writer, kind="release", padded=padded)
        return t0

    with ser:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["t", "line", "kind"])
            time.sleep(0.3)
            data = ser.read_all()
            for line in data.decode("ascii", "replace").splitlines():
                if "ERR" in line:
                    print(f"  <- {line.strip()}")
            ser.write(b"P\n")
            writer.writerow([f"{time.perf_counter():.6f}", "P", "other"])
            time.sleep(0.2)
            # 音声ストリームの立ち上がりを待つ（最初のbaselineを汚さない）。
            time.sleep(1.0)
            # Pico側のpress計数の基準。S行の成否は応答が無いためMで読む。
            pico_base = mon_snapshot(ser)
            print(
                f"-- {args.button} (0x{code:04x}) duration={duration}s "
                f"repeat={repeat_s * 1000:.0f}ms format={args.row_format} "
                f"を {times} 回送ります --"
            )
            print(f"-- CSV: {csv_path} --")
            try:
                result = AL.measure_press(send_press, capture, tries=times)
            finally:
                try:
                    pico_now = mon_snapshot(ser)
                    mon_off(ser)
                    base_p = int(pico_base.get("press", 0))
                    now_p = int(pico_now.get("press", 0))
                    base_ok = int(pico_base.get("ok", 0))
                    now_ok = int(pico_now.get("ok", 0))
                    base_ng = int(pico_base.get("ng", 0))
                    now_ng = int(pico_now.get("ng", 0))
                    print(
                        f"pico=press +{now_p - base_p} "
                        f"(ok +{now_ok - base_ok} ng +{now_ng - base_ng})"
                    )
                except Exception:
                    logger.debug("M計数の読み取りに失敗", exc_info=True)
                try:
                    ser.write(b"N\n")
                    writer.writerow([f"{time.perf_counter():.6f}", "N", "other"])
                except Exception:
                    logger.debug("中立化の送信に失敗", exc_info=True)
                print("-- 終了。中立化を送りました --")
                print(f"-- CSV: {csv_path} --")
    capture.close()

    if "error" in result:
        print(f"計測に失敗しました: {result['error']}")
        return 1
    heard = int(result.get("detected", 0))
    total = int(result.get("total", times))
    median = float(result.get("median_ms", -1.0))
    print(f"heard={heard}/total={total} median={median:.1f}ms")
    print(f"delays_ms={result.get('delays_ms', [])}")
    return 0 if heard >= total else 2


if __name__ == "__main__":
    raise SystemExit(main())
