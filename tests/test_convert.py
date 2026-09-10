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


def test_preview_filter_gray_out_full_match_keeps_color() -> None:
    """全面が対象色なら gray_out でも無地参照と一致する（表示だけ変わる）。"""
    area = make_area(64, 48)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = (0, 255, 0)  # BGR の緑。H≈60 で [50..70] に入る
    area.setPreviewFilter(True, [50, 100, 100], [70, 255, 255], "gray_out")
    area._convert(frame)
    expected = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    assert np.array_equal(area._rgb_buf, expected)


def test_preview_filter_mask_does_not_mutate_input() -> None:
    """mask でも入力 frame は変えない（認識・保存に影響しない）。"""
    area = make_area(64, 48)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = (0, 255, 0)
    before = frame.copy()
    area.setPreviewFilter(True, [50, 100, 100], [70, 255, 255], "mask")
    area._convert(frame)
    assert np.array_equal(frame, before)
    # 全面一致の mask は白一色の BGR→RGB になる
    assert np.array_equal(area._rgb_buf, np.full((48, 64, 3), 255, dtype=np.uint8))


def test_alloc_buffers_filter_buf_follows_size() -> None:
    """表示サイズ変更後の _allocBuffers で _filter_buf も追従する。

    setShowsize 本体は PhotoImage（要 Tk）のため頭出しできない。
    バッファ部分（_allocBuffers に委譲）だけ tk なしで確認する。
    """
    area = make_area(640, 360)
    assert area._filter_buf.shape == (360, 640, 3)
    area.show_width, area.show_height = 320, 180
    area.show_size = (320, 180)
    area._allocBuffers()
    assert area._filter_buf.shape == (180, 320, 3)
