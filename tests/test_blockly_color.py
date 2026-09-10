"""services/blockly_color の検証。赤・黄の単色画像で割合と加工を確かめる。"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from services import blockly_color


def png_of(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return bytes(buf)


def test_ratio_on_red_full() -> None:
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[:] = (0, 0, 255)
    ratio, err = blockly_color.ratio_on_png(
        png_of(img), [0, 100, 100], [10, 255, 255], None
    )
    assert err is None
    assert ratio > 0.9


def test_filter_gray_out_returns_png() -> None:
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[:] = (0, 255, 255)
    out, ratio, err = blockly_color.filter_png(
        png_of(img), [20, 100, 100], [40, 255, 255], "gray_out", None
    )
    assert err is None
    assert out is not None
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    assert ratio > 0.9


def test_filter_mask_returns_png() -> None:
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[:] = (0, 255, 255)
    out, _ratio, err = blockly_color.filter_png(
        png_of(img), [20, 100, 100], [40, 255, 255], "mask", None
    )
    assert err is None
    assert out is not None
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_filter_bad_mode_fails() -> None:
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[:] = (0, 255, 255)
    out, ratio, err = blockly_color.filter_png(
        png_of(img), [20, 100, 100], [40, 255, 255], "weird", None
    )
    assert out is None
    assert ratio == 0.0
    assert err is not None
    assert "modeは gray_out / mask" in err


def test_ratio_bad_hsv_fails() -> None:
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[:] = (0, 0, 255)
    with pytest.raises(ValueError):
        blockly_color.parse_hsv_pair([0, 100, 100], [999, 0, 0])
    ratio, err = blockly_color.ratio_on_png(
        png_of(img), [0, 100, 100], [999, 0, 0], None
    )
    assert ratio == 0.0
    assert err is not None
