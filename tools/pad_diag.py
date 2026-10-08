#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pad_diag.py - 接続中のパッドの生値を読む診断道具。

使い方: uv run --frozen python tools/pad_diag.py [番号] [回数]
スティックを倒しながら実行し、raw の変化を見てください。
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "SerialController"))

from core.pad_source import PadSource  # noqa: E402


def main() -> None:
    index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    src = PadSource()
    print("scan:", src.scan())
    print("names:", src.names())
    for _ in range(count):
        st = src.read(index)
        print(
            f"connected={st.connected} route={src.route_of(index)!r} "
            f"raw={tuple(st.raw_axes)} "
            f"short=({st.thumb_lx},{st.thumb_ly})({st.thumb_rx},{st.thumb_ry}) "
            f"pressed={sorted(st.pressed)} packet={st.packet}",
            flush=True,
        )
        time.sleep(0.25)


if __name__ == "__main__":
    main()
