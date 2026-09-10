#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二重復号の排除（decode1回・ndarray受渡し）の検証。"""

from __future__ import annotations

import base64
from pathlib import Path

import cv2
import numpy as np
import pytest
from services import blockly_match


def _frame() -> np.ndarray:
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[:] = (60, 60, 60)
    img[30:70, 60:140] = (255, 255, 255)
    img[40:60, 80:120] = (0, 0, 0)
    return img


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def _app_with_template(base: Path, frame: np.ndarray) -> Path:
    app = base / "app"
    d = app / "Template" / "pack"
    d.mkdir(parents=True)
    (d / "part.png").write_bytes(_png(frame[30:70, 60:140]))
    return app


def test_upload_array_decodes_once() -> None:
    """uploadのbase64→画像はimdecode1回で済ませる。"""
    decode_once = getattr(blockly_match, "decode_upload_array", None)
    assert callable(decode_once), "decode_upload_array がありません（二重復号のまま）"
    raw = _png(_frame())
    b64 = base64.b64encode(raw).decode()
    calls = {"n": 0}
    orig = cv2.imdecode
    try:
        import cv2 as _cv2

        def counting(arr: object, flags: object) -> object:
            calls["n"] += 1
            return orig(arr, flags)  # type: ignore[call-overload]

        _cv2.imdecode = counting  # type: ignore[assignment]
        img = decode_once(b64)
    finally:
        import cv2 as _cv2b

        _cv2b.imdecode = orig  # type: ignore[method-assign]
    assert isinstance(img, np.ndarray)
    assert img.shape == (100, 200, 3)
    assert calls["n"] == 1


def test_upload_array_messages_match_bytes_api() -> None:
    """検証文言は従来のbytes APIと同一（HTTP応答をずらさない）。"""
    decode_once = getattr(blockly_match, "decode_upload_array", None)
    assert callable(decode_once)
    # 不正base64
    with pytest.raises(ValueError) as e1:
        blockly_match.decode_upload_image("!!! not base64 !!!")
    with pytest.raises(ValueError) as e2:
        decode_once("!!! not base64 !!!")
    assert str(e2.value) == str(e1.value)
    # 巨大申告
    big = base64.b64encode(b"x" * (blockly_match.MAX_UPLOAD_BYTES + 1)).decode()
    with pytest.raises(ValueError) as e3:
        blockly_match.decode_upload_image(big)
    with pytest.raises(ValueError) as e4:
        decode_once(big)
    assert str(e4.value) == str(e3.value)


def test_match_accepts_ndarray_without_frame_redecode(tmp_path: Path) -> None:
    """match_templateはndarrayを受け、frame側のimdecodeを省く。"""
    frame = _frame()
    app = _app_with_template(tmp_path, frame)
    # ndarray受付が無い現状は失敗（RED）するはず。
    res = blockly_match.match_template(app, frame, "pack/part.png", 0.7)  # type: ignore[arg-type]
    assert res.status == "ok"
    assert res.matched is True
    assert res.rect == {"x": 60, "y": 30, "width": 80, "height": 40}


def test_match_ndarray_skips_frame_imdecode(tmp_path: Path) -> None:
    """ndarray入力ではframeのimdecodeが走らない（テンプレ読込分のみ）。"""
    frame = _frame()
    app = _app_with_template(tmp_path, frame)
    calls = {"n": 0}
    orig = cv2.imdecode
    try:
        import cv2 as _cv2

        def counting(arr: object, flags: object) -> object:
            calls["n"] += 1
            return orig(arr, flags)  # type: ignore[call-overload]

        _cv2.imdecode = counting  # type: ignore[assignment]
        res = blockly_match.match_template(app, frame, "pack/part.png", 0.7)  # type: ignore[arg-type]
    finally:
        import cv2 as _cv2b

        _cv2b.imdecode = orig  # type: ignore[method-assign]
    assert res.status == "ok"
    # frame復号0回＋テンプレは別経路（_imread）のため、imdecodeは0回のはず。
    assert calls["n"] == 0, f"frame側で再復号しています: {calls['n']}回"
