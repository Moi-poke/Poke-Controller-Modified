"""test_convert.py: the present path no longer resizes or swaps channel order.

The old assertions pinned an RGB buffer that the DIB path does not have. Each
rewrite keeps the original intent:

* "same size skips resize"      -> the frame itself reaches compose unchanged
* "downscale matches reference" -> no longer a concept; the 1:1 check discards
                                   a mismatched frame instead of scaling it, so
                                   the new assertion is that nothing is resized
* "720p display is correct"    -> unchanged in value, BGR instead of RGB
* "full-match gray_out"        -> unchanged in value, BGR instead of RGB
* "mask does not mutate input"  -> unchanged, plus the input is still untouched
* "correction applies before RGB" -> correction still applies, BGR instead
* "correction runs before HSV" -> ordering intent preserved verbatim
"""

import cv2
import numpy as np
from GuiAssets import CaptureArea


def make_area(width: int, height: int) -> CaptureArea:
    area = CaptureArea.__new__(CaptureArea)
    area.show_width = width
    area.show_height = height
    area.show_size = (width, height)
    area._filter_enabled = False
    area._filter_lower = [0, 0, 0]
    area._filter_upper = [179, 255, 255]
    area._filter_mode = "gray_out"
    area._correction = None
    area._correction_active = False
    area._allocBuffers()
    return area


def test_convert_same_size_skips_resize() -> None:
    area = make_area(640, 360)
    frame = np.random.randint(0, 256, (360, 640, 3), dtype=np.uint8)
    assert np.array_equal(area._convert(frame), frame)


def test_convert_does_not_scale_a_mismatched_frame() -> None:
    area = make_area(640, 360)
    frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
    converted = area._convert(frame)
    assert converted.shape == (720, 1280, 3), "縮小も拡大もしてはいけない"
    assert not np.array_equal(
        converted,
        cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA),
    )


def test_convert_720p_display() -> None:
    """利用者の構成（取込720p→表示720p）。縮小なしで正しく出る。"""
    area = make_area(1280, 720)
    frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
    assert np.array_equal(area._convert(frame), frame)


def test_preview_filter_gray_out_full_match_keeps_color() -> None:
    """全面が対象色なら gray_out でも無地参照と一致する（表示だけ変わる）。"""
    area = make_area(64, 48)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = (0, 255, 0)  # BGR の緑。H≈60 で [50..70] に入る
    area.setPreviewFilter(True, [50, 100, 100], [70, 255, 255], "gray_out")
    assert np.array_equal(area._convert(frame), frame)


def test_preview_filter_mask_does_not_mutate_input() -> None:
    """mask でも入力 frame は変えない（認識・保存に影響しない）。"""
    area = make_area(64, 48)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = (0, 255, 0)
    before = frame.copy()
    area.setPreviewFilter(True, [50, 100, 100], [70, 255, 255], "mask")
    assert np.array_equal(area._convert(frame), np.full((48, 64, 3), 255, np.uint8))
    assert np.array_equal(frame, before)


def test_alloc_buffers_filter_buf_follows_size() -> None:
    """表示サイズ変更後の _allocBuffers で _filter_buf も追従する。

    setShowsize はTk を触るため頭出しできない。バッファ部分
    （_allocBuffers に委譲）だけ tk なしで確認する。
    """
    area = make_area(640, 360)
    assert area._filter_buf.shape == (360, 640, 3)
    area.show_width, area.show_height = 320, 180
    area.show_size = (320, 180)
    area._allocBuffers()
    assert area._filter_buf.shape == (180, 320, 3)


def test_correction_only_applies_before_rgb() -> None:
    """抽出OFF・補正ONでも補正がかかる（輝度+50で全画素+50）。"""
    from core import preview_filter

    area = make_area(64, 48)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = (100, 100, 100)
    corr = dict(preview_filter.DEFAULT_CORRECTION)
    corr["brightness"] = 50
    area.setPreviewFilter(False, [0, 0, 0], [179, 255, 255], "gray_out", corr)
    assert np.array_equal(area._convert(frame), np.full((48, 64, 3), 150, np.uint8))
    # 入力は変えない。
    assert np.array_equal(frame, np.full((48, 64, 3), 100, np.uint8))


def test_correction_neutral_keeps_bit_identical() -> None:
    """中立補正は無効時とbit一致（余計な経路を通らない）。"""
    from core import preview_filter

    area = make_area(64, 48)
    frame = np.random.randint(0, 256, (48, 64, 3), dtype=np.uint8)
    area.setPreviewFilter(
        False,
        [0, 0, 0],
        [179, 255, 255],
        "gray_out",
        dict(preview_filter.DEFAULT_CORRECTION),
    )
    assert np.array_equal(area._convert(frame), frame)


def test_correction_runs_before_hsv_mask() -> None:
    """補正→抽出の順にかかる（暗い赤を輝度で持ち上げると拾える）。"""
    from core import preview_filter

    area = make_area(64, 48)
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = (0, 0, 80)  # 暗い赤。V=80 で下限V=100に届かない
    corr = dict(preview_filter.DEFAULT_CORRECTION)
    corr["brightness"] = 100  # (100,100,180) になり H=0・S≈113・V=180 で拾える
    area.setPreviewFilter(True, [0, 100, 100], [10, 255, 255], "mask", corr)
    # 拾えていれば mask は白一色になる。
    assert np.array_equal(area._convert(frame), np.full((48, 64, 3), 255, np.uint8))
    # 補正なし（中立）では拾えない＝マスクが補正後の絵を見ている。
    area.setPreviewFilter(
        True,
        [0, 100, 100],
        [10, 255, 255],
        "mask",
        dict(preview_filter.DEFAULT_CORRECTION),
    )
    assert np.array_equal(area._convert(frame), np.zeros((48, 64, 3), np.uint8))
