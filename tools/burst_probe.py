"""burst_probe.py - 連打の到達率を受入計数する（本命の受入試験）。

使い方:
    uv run --frozen python tools/burst_probe.py --port COM3 --audio-device 70 --via app
    uv run --frozen python tools/burst_probe.py --port COM3 --audio-device 70 --via raw --times 30

--duration 0.05 --interval 0.1（100ms間隔の連打）を全件通しで送り、
音声を連続収録してクリック数を数える。孤立試行のpress_probe系と違い、
連打時のqueue滞留・行詰まり・検出の重なりまで含めた受入判定になる。
Switchはボタン入力テスト等の発音画面にしておくこと。
"""

from __future__ import annotations

import argparse
import atexit
import csv
import os
import re
import sys
import time
import wave
from datetime import datetime
from typing import Any

import numpy as np
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
from core.Keys import Button, KeyPress  # noqa: E402
from core.serial.sender import Sender  # noqa: E402
from core.transport.text_serial import PicoUartTransport  # noqa: E402

# ---------------------------------------------------------------------------
# ボタン表は tools/pico_probe.py の BTN と同じ値を使う。
# ---------------------------------------------------------------------------
BTN = {
    "Y": (0x0001, Button.Y),
    "B": (0x0002, Button.B),
    "A": (0x0004, Button.A),
    "X": (0x0008, Button.X),
    "L": (0x0010, Button.L),
    "R": (0x0020, Button.R),
    "ZL": (0x0040, Button.ZL),
    "ZR": (0x0080, Button.ZR),
    "MINUS": (0x0100, Button.MINUS),
    "PLUS": (0x0200, Button.PLUS),
    "LCLICK": (0x0400, Button.LCLICK),
    "RCLICK": (0x0800, Button.RCLICK),
    "HOME": (0x1000, Button.HOME),
    "CAPTURE": (0x2000, Button.CAPTURE),
}

HAT_CENTER = 8
STICK_CENTER = 0x80
BAUD_RATE = 115200


def _begin_timer_period() -> None:
    """Windows のタイマー分解能を 1ms に上げる。連打刻みの精度のため。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
        atexit.register(ctypes.windll.winmm.timeEndPeriod, 1)
    except Exception:
        logger.warning("timeBeginPeriod is unavailable; timer stays coarse")


class _RecSerial:
    """ser.writeを時刻付きで写す薄い包み。読み・閉じは委譲する。"""

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


def _mon_state(lines: list[str]) -> bool | None:
    """直近の monitor on/off 応答。無ければNone。"""
    for line in reversed(lines):
        s = line.strip()
        if s == "monitor on":
            return True
        if s == "monitor off":
            return False
    return None


def read_mon_counts(lines: list[str]) -> dict[str, int]:
    """集めた受信行から Mモニタの press/ok/ng を読む。無ければ空辞書。"""
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


def save_wav_mono(path: str, x: np.ndarray, rate: int) -> None:
    """監査用に収録波形を16bit mono wavで残す。miss時の聞き直し用。"""
    data = np.asarray(x, dtype=np.float64).ravel()
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    scale = 32000.0 / peak if peak > 1.0 else 32000.0
    pcm = (np.clip(data * scale, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(max(1, int(rate)))
        f.writeframes(pcm.tobytes())


def build_parser() -> argparse.ArgumentParser:
    """引数組み立て。--list-audio のときは --port を求めない。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="")
    ap.add_argument("--button", default="A", choices=sorted(BTN))
    # 複数ボタンの巡回（MashA型の連打検証用）。空なら --button を使う。
    # 例: --buttons A,B,X,Y（試行ごとに巡るため状態遷移の連続になる）。
    ap.add_argument("--buttons", default="")
    ap.add_argument("--duration", type=float, default=0.05)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--times", type=int, default=240)
    ap.add_argument("--via", default="app", choices=("app", "raw"))
    ap.add_argument("--repeat-ms", type=float, default=8.0)
    # app経路のscheduler最低保持（ミリ秒）。未指定ならSender既定のまま。
    # 40ms間隔の連打とdwellの干渉（dwell×2>intervalで滞留・dropped）を
    # 切り分けるためのsweep用。8〜64以外はSender側が無視する。
    ap.add_argument("--dwell-ms", type=int, default=None)
    ap.add_argument("--audio-device", default=None)
    ap.add_argument("--list-audio", action="store_true")
    # クリック計数の不応期（ミリ秒）。intervalより短くすること。
    # interval 60msの連打では既定60msだと隣と癒着するため40ms等へ下げる。
    ap.add_argument("--refractory-ms", type=float, default=60.0)
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
    seq_src = str(args.buttons).strip() or str(args.button)
    names = [part.strip().upper() for part in seq_src.split(",") if part.strip()]
    unknown = [name for name in names if name not in BTN]
    if not names or unknown:
        print(f"--buttons が不正です: {args.buttons or args.button}")
        return 1
    seq = [(BTN[name][0], BTN[name][1], name) for name in names]
    duration = max(0.0, float(args.duration))
    interval = max(0.0, float(args.interval))
    times = max(1, int(args.times))
    refractory_s = max(0.01, float(args.refractory_ms)) / 1000.0
    repeat_s = max(0.0, float(args.repeat_ms)) / 1000.0
    via_app = str(args.via) == "app"

    _begin_timer_period()
    os.makedirs("log", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    wire_path = os.path.join("log", f"burst_wire_{stamp}.csv")
    wav_path = os.path.join("log", f"burst_{stamp}.wav")

    sender: Sender | None = None
    transport: PicoUartTransport | None = None
    ser: Any = None
    keys: Any = None
    if via_app:
        transport = PicoUartTransport()
        sender = Sender(is_show_serial=False, transport=transport)
        if not sender.openSerial(0, args.port, 115200):
            print("COM Port: can't be established")
            return 1
        if args.dwell_ms is not None:
            sender.setLiveMinDwell(int(args.dwell_ms))
        keys = KeyPress(sender, source="script")
    else:
        try:
            ser = serial.Serial(args.port, BAUD_RATE, timeout=0.5)
        except (OSError, serial.SerialException, ValueError) as e:
            print("COM Port: can't be established")
            logger.error(f"COM Port: can't be established: {e}")
            return 1

    capture = AudioCapture(ring_seconds=30.0)
    if not capture.openInput(args.audio_device):
        print("音声入力を開けませんでした")
        logger.error("音声入力を開けませんでした")
        if sender is not None:
            sender.closeSerial()
        if ser is not None:
            ser.close()
        return 1

    def send_row(btn_value: int, kind: str) -> None:
        # raw経路はアプリ短縮形と同一書式（書式差の混入を避ける）。
        line = f"S {btn_value:x} {HAT_CENTER:x} {STICK_CENTER:02x} {STICK_CENTER:02x} "
        line += f"{STICK_CENTER:02x} {STICK_CENTER:02x}\n"
        assert ser is not None
        ser.write(line.encode("ascii"))
        wire_writer.writerow([f"{time.perf_counter():.6f}", line.strip(), kind])

    wire_file = open(wire_path, "w", newline="", encoding="utf-8")
    wire_writer = csv.writer(wire_file)
    if via_app:
        assert transport is not None
        wire_writer.writerow(["t", "line"])
        transport.ser = _RecSerial(transport.ser, wire_writer)
    else:
        wire_writer.writerow(["t", "line", "kind"])

    pico_base: dict[str, int] = {}
    pico_now: dict[str, int] = {}
    t0s: list[float] = []
    try:
        time.sleep(1.0)  # 音声ストリームの立ち上がりを待つ
        if via_app:
            assert transport is not None
            pico_base = mon_snapshot_sub(transport)
        else:
            assert ser is not None
            pico_base = mon_snapshot_raw(ser)
        time.sleep(0.5)  # 先頭0.5秒の静けさ（計数のbaseline用）
        print(
            f"-- {','.join(names)} duration={duration}s interval={interval}s "
            f"dwell={args.dwell_ms}ms "
            f"を {times} 回送ります（{args.via}経路） --"
        )
        print(f"-- CSV: {wire_path} --")
        for i in range(times):
            cycle = time.perf_counter()
            code_i, btn_i, _name_i = seq[i % len(seq)]
            if via_app:
                assert keys is not None
                keys.input(btn_i)
                t0s.append(time.perf_counter())
                time.sleep(duration)
                keys.inputEnd(btn_i)
            else:
                assert ser is not None
                t0 = time.perf_counter()
                send_row(code_i, "press")
                t0s.append(t0)
                end = t0 + duration
                while True:
                    now = time.perf_counter()
                    if now >= end:
                        break
                    if repeat_s <= 0.0:
                        time.sleep(end - now)
                        break
                    time.sleep(min(repeat_s, end - now))
                    send_row(code_i, "press")
                for _ in range(3):
                    send_row(0, "release")
                    time.sleep(max(0.0, repeat_s))
            rest = interval - (time.perf_counter() - cycle)
            if rest > 0:
                time.sleep(rest)
        time.sleep(1.0)  # 最終クリック＋遅延ぶんの尻尾
        # 音声読み出しはclose前（close後は環が読めない）。
        heard = 0
        rate = 44100
        try:
            stamped = capture.read_stamped(30.0)
        except Exception:
            stamped = None
        if stamped is not None:
            window, _total, tap, _lat, rate = stamped
            heard = count_in_run(window, rate, t0s, tap, refractory_s)
            try:
                save_wav_mono(wav_path, window, rate)
                print(f"-- WAV: {wav_path} --")
            except Exception:
                logger.debug("WAV保存に失敗", exc_info=True)
        else:
            print("音声の読み出しに失敗しました")
        if via_app:
            assert transport is not None
            pico_now = mon_snapshot_sub(transport)
            mon_off_sub(transport)
        else:
            assert ser is not None
            pico_now = mon_snapshot_raw(ser)
            mon_off_raw(ser)
            ser.write(b"N\n")
    finally:
        try:
            if keys is not None:
                keys.end()
        except Exception:
            logger.debug("終了時の中立化に失敗", exc_info=True)
        if sender is not None:
            sender.closeSerial()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
    capture.close()
    wire_file.close()

    # 収録全体からクリック数を数える（試行帰属はしない。合計で受入判定）。

    base_p = int(pico_base.get("press", 0))
    now_p = int(pico_now.get("press", 0))
    base_ok = int(pico_base.get("ok", 0))
    now_ok = int(pico_now.get("ok", 0))
    base_ng = int(pico_base.get("ng", 0))
    now_ng = int(pico_now.get("ng", 0))
    print(
        f"pico=press +{now_p - base_p} (ok +{now_ok - base_ok} ng +{now_ng - base_ng})"
    )
    if sender is not None:
        stats = sender.getLiveStats()
        print(
            "live_stats="
            f"put={stats.get('put')} sent={stats.get('sent')} "
            f"dropped={stats.get('dropped')}"
        )
    print(f"heard={heard}/total={len(t0s)}")
    if heard > len(t0s):
        print("注意: 検出数が試行数を上回りました（雑音の混入を疑うこと）")
    return 0 if 0 < heard >= len(t0s) else 2


def count_in_run(
    window: np.ndarray,
    rate: int,
    t0s: list[float],
    tap: list[tuple[float, int]],
    refractory_s: float = 0.06,
) -> int:
    """収録全体のクリック数を数える。試行帰属はしない。

    初押下+20ms〜最終押下+0.9秒の範囲で立ち上がりを数える。
    クリック遅延（約100ms）と次押下が重なっても合計は合う。
    """
    if not t0s:
        return 0
    rate = max(1, int(rate))
    first = AL.absolute_at(t0s[0], tap, rate)
    last = AL.absolute_at(t0s[-1], tap, rate)
    if first is None or last is None:
        return 0
    # 先頭0.4秒の静けさをbaselineに使う（押下直前はクリックで汚れる）。
    size = int(np.asarray(window).size)
    base = max(0, first - int(0.4 * rate))
    lo = max(0, first + int(0.02 * rate))
    hi = min(size, last + int(0.9 * rate))
    if hi <= lo or base >= size:
        return 0
    found = AL.count_onsets(
        np.asarray(window)[base:hi], rate, refractory_s=refractory_s
    )
    return sum(1 for at in found if base + at >= lo)


def mon_snapshot_raw(ser: serial.Serial) -> dict[str, int]:
    """Mを確実にONにし、heartbeatのmon行を読む（素シリアル用）。"""
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


def mon_off_raw(ser: serial.Serial) -> None:
    """Mを消す（素シリアル用）。"""
    try:
        ser.write(b"M\n")
        time.sleep(0.2)
        ser.read_all()
    except Exception:
        logger.debug("Mの停止に失敗", exc_info=True)


def mon_snapshot_sub(transport: Any) -> dict[str, int]:
    """Mを確実にONにし、heartbeatのmon行を購読で読む（Transport用）。"""
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


def mon_off_sub(transport: Any) -> None:
    """Mを消す（Transport用）。"""
    try:
        transport.ser.write(b"M\n")
        time.sleep(0.2)
    except Exception:
        logger.debug("Mの停止に失敗", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
