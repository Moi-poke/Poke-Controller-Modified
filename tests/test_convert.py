"""_convert（表示用変換）の検証。Tkなしで回せる範囲だけ。"""

import cv2
import numpy as np
from GuiAssets import CaptureArea


def make_area(width: int, height: int) -> CaptureArea:
    area = CaptureArea.__new__(CaptureArea)
    area.show_width = width
    area.show_height = height
    area.show_size = (width, height)
    area._allocBuffers()
    return area


def test_convert_same_size_skips_resize() -> None:
    area = make_area(640, 360)
    frame = np.random.randint(0, 256, (360, 640, 3), dtype=np.uint8)
    area._convert(frame)
    expected = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    assert np.array_equal(area._rgb_buf, expected)


def test_convert_downscale_matches_reference() -> None:
    area = make_area(640, 360)
    frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
    area._convert(frame)
    expected = cv2.cvtColor(
        cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA),
        cv2.COLOR_BGR2RGB,
    )
    assert np.array_equal(area._rgb_buf, expected)


def test_convert_720p_display() -> None:
    """利用者の構成（取込720p→表示720p）。縮小なしで正しく出る。"""
    area = make_area(1280, 720)
    frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
    area._convert(frame)
    expected = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    assert np.array_equal(area._rgb_buf, expected)
