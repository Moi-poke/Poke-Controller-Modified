#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imu_send.py - PCからPicoへIMU波形を送るE2E検証道具。

使い方:
    uv run --frozen python tools/imu_send.py --port COM3 --wave sine --axis 2 --secs 30
    uv run --frozen python tools/imu_send.py --port COM3 --wave step --axis 1 --raw 4096

HELLOで接続し、IMU loopを回して波形を送り続ける。Ctrl+Cで止める。
STATEは送らないため、ボタン操作は別途アプリから行うこと。
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))

from core.imu_wave import SineWave, StaticRest, StepWave  # noqa: E402
from core.sdl_sensor import SdlSensorReader  # noqa: E402
from core.transport import create_transport  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="IMU波形のE2E送信")
    ap.add_argument("--port", default="COM3", help="data-UARTの口")
    ap.add_argument("--baud", type=int, default=1000000, help="速度")
    ap.add_argument(
        "--wave",
        choices=("sine", "step", "rest", "sensor", "hid"),
        default="sine",
        help="波形。sensorはSDLセンサー、hidはProCon USB直読み（Steam共存可）",
    )
    ap.add_argument("--axis", type=int, default=2, help="gyro軸 0..2")
    ap.add_argument("--pad", type=int, default=0, help="センサー用パッド番号")
    ap.add_argument("--amp", type=int, default=4096, help="SINE振幅raw")
    ap.add_argument("--period", type=float, default=2.0, help="SINE周期秒")
    ap.add_argument("--raw", type=int, default=5899, help="STEP値raw")
    ap.add_argument("--duration", type=float, default=4.0, help="STEP継続秒")
    ap.add_argument("--acc-z", type=int, default=4096, help="静止加速度Z")
    ap.add_argument("--secs", type=float, default=30.0, help="送信秒数")
    ap.add_argument("--no-hello", action="store_true", help="HELLOを飛ばす")
    ap.add_argument("--record", default="", help="送出値を.pgyroへ記録する")
    ap.add_argument("--play", default="", help=".pgyroを再生する（波形指定より優先）")
    args = ap.parse_args()

    sensor_reader = None
    recorder = None
    if args.play:
        from core.motion_track import MotionPlayer

        player = MotionPlayer(args.play)
        if not player.load():
            print(f"再生できません: {player.error}")
            return 1
        print(f"再生: {args.play} {len(player)}件")
        gen = player.as_source()
    elif args.wave == "sine":
        gen = SineWave(
            axis=args.axis, amp=args.amp, period_s=args.period, acc_z=args.acc_z
        )
    elif args.wave == "step":
        gen = StepWave(
            axis=args.axis, raw=args.raw, duration_s=args.duration, acc_z=args.acc_z
        )
        gen.trigger()
    elif args.wave == "sensor":
        sensor_reader = SdlSensorReader()
        if not sensor_reader.open(args.pad):
            print(f"センサーが開けません: pad={args.pad}")
            return 1
        print(f"センサー使用: pad={args.pad}")
        gen = sensor_reader.read
    elif args.wave == "hid":
        from core.nintendo_hid import NintendoHidReader

        hid_reader = NintendoHidReader()
        if not hid_reader.open():
            print("ProCon HIDが開けません。USB接続を確認してください。")
            return 1
        print("ProCon HID直読みを使用。")
        sensor_reader = hid_reader
        gen = hid_reader.read
    else:
        gen = StaticRest(acc_z=args.acc_z)
    if args.record and not args.play:
        from core.motion_track import MotionRecorder

        recorder = MotionRecorder(args.record)
        recorder.open()
        inner = gen

        def recording_gen() -> object:
            sample = inner()
            if sample is not None and recorder is not None:
                recorder.write(sample)
            return sample

        gen = recording_gen  # type: ignore[assignment]

    made = create_transport("switch-bcon")
    port_num = 0
    digits = "".join(c for c in args.port if c.isdigit())
    if digits:
        port_num = int(digits)
    if not made.open(port_num, args.port, args.baud):
        print(f"開けません: {args.port}")
        return 1
    made.start_rx_pump()
    try:
        if not args.no_hello:
            if not made.hello():
                print("HELLO不通。送出だけ行う場合は --no-hello を付ける。")
                return 1
            print("HELLO通過。")
        if not made.start_imu_loop(gen):
            print("IMU loopの起動に失敗。")
            return 1
        print(f"送信中: wave={args.wave} axis={args.axis} {args.secs}秒 (Ctrl+Cで中断)")
        t0 = time.perf_counter()
        try:
            while time.perf_counter() - t0 < args.secs:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("中断した。")
        stats = made.imu_stats()
        el = time.perf_counter() - t0
        rate = stats["sent"] / el if el > 0 else 0
        print(f"sent={stats['sent']} dropped={stats['dropped']} rate={rate:.1f}Hz")
        return 0
    finally:
        try:
            made.stop_imu_loop()
        except Exception:
            pass
        made.close()
        if sensor_reader is not None:
            sensor_reader.close()
        if recorder is not None:
            recorder.close()
            print(f"記録: {args.record} {recorder.count}件")


if __name__ == "__main__":
    raise SystemExit(main())
