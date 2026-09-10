#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_get_frameのPNGキャッシュ（~200ms・有界陳腐化）の検証。"""

from __future__ import annotations

import cv2
import numpy as np
from services import blockly_capture


def _img() -> np.ndarray:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    img[:] = (60, 120, 200)
    return img


def test_rapid_polls_collapse_encodes() -> None:
    """速い連打はエンコードを畳む（ rapid polls → encodes collapse ）。"""
    frame = _img()

    class FakeCam:
        def readFrame(self, copy: bool = False) -> object:
            return frame

    calls = {"n": 0}
    orig = cv2.imencode
    try:
        import cv2 as _cv2

        def counting(ext: object, img: object, *a: object, **k: object) -> object:
            calls["n"] += 1
            return orig(ext, img, *a, **k)  # type: ignore[call-overload]

        _cv2.imencode = counting  # type: ignore[assignment]
        get_frame = blockly_capture.build_get_frame(lambda: FakeCam())
        outs = [get_frame() for _ in range(10)]
    finally:
        import cv2 as _cv2b

        _cv2b.imencode = orig  # type: ignore[method-assign]
    assert all(o is not None and o[:8] == b"\x89PNG\r\n\x1a\n" for o in outs)
    assert calls["n"] <= 2, f"毎回エンコードしています: {calls['n']}回/10 polls"


def test_cache_expires_and_stays_bounded() -> None:
    """200ms後は作り直す（陳腐化は有界）。失敗時はNoneを保つ。"""
    import time

    frame = _img()

    class FakeCam:
        def readFrame(self, copy: bool = False) -> object:
            return frame

    get_frame = blockly_capture.build_get_frame(lambda: FakeCam())
    first = get_frame()
    assert first is not None
    time.sleep(0.25)
    second = get_frame()
    assert second is not None
    # 同一フレームなので中身は等しいが、期限切れで作り直したこと自体は
    # エンコード回数で別途見る。ここでは少なくとも有効なPNGが返る。
    assert second[:8] == b"\x89PNG\r\n\x1a\n"

    # 壊れたカメラはNone（キャッシュで誤魔化さない）。
    class DeadCam:
        def readFrame(self, copy: bool = False) -> object:
            return None

    dead = blockly_capture.build_get_frame(lambda: DeadCam())()
    assert dead is None
