#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sensor_diag.py - SDLセンサーの診断道具。

使い方: uv run --frozen python tools/sensor_diag.py [番号] [回数]
コントローラを動かしながら実行し、raw(物理量)とcooked(Switch生値)の
変化を見てください。静止時は加速度の大きさが約9.8、ジャイロが0付近。
"""

from __future__ import annotations

import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))

from core.sdl_sensor import SdlSensorReader  # noqa: E402


def main() -> int:
    index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    reader = SdlSensorReader()
    print(f"available={reader.available}")
    if not reader.available:
        print("SDL2センサーが使えません。")
        return 1
    if not reader.open(index):
        print(f"開けません: index={index}。センサー対応パッドを繋いでください。")
        return 1
    try:
        for _ in range(count):
            raw = reader.read_raw()
            cooked = reader.read()
            if raw is None or cooked is None:
                print("read=None", flush=True)
            else:
                (ax, ay, az), (gx, gy, gz) = raw
                mag = math.sqrt(ax * ax + ay * ay + az * az)
                print(
                    f"acc=({ax:+.2f},{ay:+.2f},{az:+.2f})|{mag:.2f} "
                    f"gyr=({gx:+.3f},{gy:+.3f},{gz:+.3f}) "
                    f"cooked={cooked}",
                    flush=True,
                )
            time.sleep(0.25)
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
