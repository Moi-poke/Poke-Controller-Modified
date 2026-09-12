"""app_probe.py - 実アプリ経路（Keys→Sender→scheduler→実線）の到達率を計数する。

使い方:
    uv run --frozen python tools/app_probe.py --port COM3 --audio-device 70 --duration 0.016 --times 30

tools/press_probe.py が素のシリアル直送なのに対し、こちらは本物の
KeyPress＋Sender＋LiveScheduler を実線へ繋いで叩く。scheduler が
疑わしい場合と無罪の場合を分けるための対照実験が目的。
終了時に getLiveStats を出し、PC側の送出会計（put/sent/dropped）と
Switch音の heard/total を突き合わせる。
"""

from __future__ import annotations

import argparse
import atexit
import csv
import os
import re
import sys
import time
from typing import Any

import serial  # noqa: F401  # 接続失敗の例外型で使う
from loguru import logger

_CONTROLLER_DIR = os.path.join(os.path.dirname(__file__), "..", "SerialController")
if _CONTROLLER_DIR not in sys.path:
    sys.path.insert(0, _CONTROLLER_DIR)

from core import (  # noqa: E402
    AudioCapture as AC,  # noqa: E402
    audio_latency as AL,  # noqa: E402
)
from core.AudioCapture import AudioCapture  # noqa: E402
from core.Keys import Button, KeyPress  # noqa: E402
from core.serial.sender import Sender  # noqa: E402
from core.transport.text_serial import PicoUartTransport  # noqa: E402

BUTTONS = {
    "Y": Button.Y,
    "B": Button.B,
    "A": Button.A,
    "X": Button.X,
}


class _RecSerial:
    """ser.writeを時刻付きで写す薄い包み。読み・閉じは委譲する。

    ワイヤdwell（press行→中立行の間隔）を試行ごとに測るため、
    workerの送出をそのまま記録する。rxポンプの読みには触らない。
    """

    def __init__(self, inner: Any, writer: Any) -> None:
        self._inner = inner
        self._writer = writer

    def write(self, data: bytes) -> int:
        n = int(self._inner.write(data))
        try:
            text = bytes(data).decode("ascii", "replace").strip()
        except Exception:
            text = ""
        self._writer.writerow([f"{time.perf_counter():.6f}", text])
        return n

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def read_mon_counts(lines: list[str]) -> dict[str, int]:
    """集めた受信行から Mモニタの press/ok/ng を読む。無ければ空辞書。

    注意: Transport は読みポンプが常駐し、ser直読みと競合する（横取り）。
    受信は subscribe_rx で購読すること。直の read_all では取れない。
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


def mon_snapshot(transport: Any) -> dict[str, int]:
    """Mを確実にONにし、heartbeatのmon行を購読で読む。

    M自体の即時応答（monitor on/off）で開閉を確定する。
    heartbeat待ちだけでは、Pico多忙時の遅延と開閉不明が区別できない。
    """
    collected: list[str] = []
    try:
        unsub = transport.subscribe_rx(collected.append)
    except Exception:
        return {}
    try:
        for _ in range(4):
            try:
                transport.ser.write(b"M\n")
            except Exception:
                return {}
            time.sleep(0.7)
            if _mon_state(collected) is True:
                break
        time.sleep(1.5)
        return read_mon_counts(collected)
    finally:
        try:
            unsub()
        except Exception:
            logger.debug("購読解除に失敗", exc_info=True)


def mon_off(transport: Any) -> None:
    """Mを消す。"""
    try:
        transport.ser.write(b"M\n")
        time.sleep(0.2)
    except Exception:
        logger.debug("Mの停止に失敗", exc_info=True)


def _begin_timer_period() -> None:
    """Windows のタイマー分解能を 1ms に上げる。press幅の精度のため。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
        atexit.register(ctypes.windll.winmm.timeEndPeriod, 1)
    except Exception:
        logger.warning("timeBeginPeriod is unavailable; timer stays coarse")


def build_parser() -> argparse.ArgumentParser:
    """引数組み立て。--list-audio のときは --port を求めない。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="")
    ap.add_argument("--button", default="A", choices=sorted(BUTTONS))
    ap.add_argument("--duration", type=float, default=0.016)
    ap.add_argument("--times", type=int, default=30)
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
    btn: Any = BUTTONS[args.button]
    duration = max(0.0, float(args.duration))
    times = max(1, int(args.times))

    _begin_timer_period()

    transport = PicoUartTransport()
    sender = Sender(is_show_serial=False, transport=transport)
    # Pico は 115200 固定。coerce の記録は Transport 側が出す。
    if not sender.openSerial(0, args.port, 115200):
        print("COM Port: can't be established")
        return 1

    capture = AudioCapture()
    if not capture.openInput(args.audio_device):
        print("音声入力を開けませんでした")
        logger.error("音声入力を開けませんでした")
        sender.closeSerial()
        return 1

    keys = KeyPress(sender, source="script")

    os.makedirs("log", exist_ok=True)
    from datetime import datetime

    wire_path = os.path.join(
        "log", f"app_wire_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    )
    wire_file = open(wire_path, "w", newline="", encoding="utf-8")
    wire_writer = csv.writer(wire_file)
    wire_writer.writerow(["t", "line"])
    # 以降の送出をすべて時刻付きで写す（rxポンプの読みは委譲のまま）。
    transport.ser = _RecSerial(transport.ser, wire_writer)

    # core/audio_latency.measure_press と同じ手順だが、試行ごとに
    # heard/delayを残す（missが何試行目か分からないとdwellと突き合わせられない）。
    tries: list[dict[str, Any]] = []
    try:
        # 音声ストリームの立ち上がりを待つ（最初のbaselineを汚さない）。
        time.sleep(1.0)
        # Pico側のpress計数の基準。S行の成否は応答が無いためMで読む。
        pico_base = mon_snapshot(transport)
        print(
            f"-- {args.button} duration={duration}s を {times} 回送ります（実経路） --"
        )
        print(f"-- CSV: {wire_path} --")
        for _ in range(times):
            try:
                before = capture.readWindow(0.3)
                baseline = AL.window_rms(before) if before is not None else 0.0
            except Exception:
                baseline = 0.0
            # scheduler が dwell 保持＋毎slot再送する。PC側sleepは手順用。
            keys.input(btn)
            t0 = time.perf_counter()
            time.sleep(duration)
            keys.inputEnd(btn)
            time.sleep(AL.ONSET_SETTLE)
            heard = False
            delay = -1.0
            try:
                stamped = capture.read_stamped(2.0)
            except Exception:
                stamped = None
            if stamped is not None:
                window, total, tap, in_latency, in_rate = stamped
                rate = int(in_rate)
                search_from = int(total - (time.perf_counter() - t0 + 0.3) * rate)
                at = AL.onset_absolute(window, total, baseline, search_from)
                if at is not None:
                    got_at = AL.locate_played(tap, at, in_latency, rate)
                    if got_at is not None:
                        heard = True
                        delay = (got_at - t0) * 1000.0
            tries.append({"t0": t0, "heard": heard, "delay": delay})
    finally:
        try:
            pico_now = mon_snapshot(transport)
            mon_off(transport)
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
            keys.end()
        except Exception:
            logger.debug("終了時の中立化に失敗", exc_info=True)
        sender.closeSerial()
    capture.close()
    wire_file.close()

    stats = sender.getLiveStats()
    print(
        "live_stats="
        f"put={stats.get('put')} sent={stats.get('sent')} "
        f"replaced={stats.get('replaced')} dropped={stats.get('dropped')} "
        f"priority={stats.get('priority')}"
    )
    # ワイヤdwellを試行ごとに測る（press行→中立行の間隔）。
    wire_rows: list[tuple[float, int]] = []
    with open(wire_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                btn_value = int(row["line"].split()[1], 16)
            except (IndexError, ValueError):
                continue
            wire_rows.append((float(row["t"]), btn_value))
    heard_count = 0
    miss_dwells: list[float] = []
    for index, trial in enumerate(tries):
        start = float(trial["t0"])
        press_at: float | None = None
        dwell = -1.0
        for stamp, value in wire_rows:
            if stamp < start - 0.05:
                continue
            if stamp > start + duration + 1.0:
                break
            if press_at is None:
                if value != 0:
                    press_at = stamp
            elif value == 0:
                dwell = (stamp - press_at) * 1000.0
                break
        if bool(trial["heard"]):
            heard_count += 1
        else:
            miss_dwells.append(dwell)
        print(
            f"try {index + 1}: heard={1 if trial['heard'] else 0} "
            f"wire={dwell:.1f}ms delay={float(trial['delay']):.1f}ms"
        )
    delays = sorted(float(t["delay"]) for t in tries if t["heard"])
    median = delays[len(delays) // 2] if delays else -1.0
    print(f"heard={heard_count}/total={len(tries)} median={median:.1f}ms")
    print(f"miss_wire_dwells={[round(v, 1) for v in miss_dwells]}")
    return 0 if heard_count >= len(tries) else 2


if __name__ == "__main__":
    raise SystemExit(main())
