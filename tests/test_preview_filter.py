"""preview_filter（HSVプレビューの純粋関数）の単体テスト。"""

from __future__ import annotations

import numpy as np
import pytest
from core import preview_filter


def red_image(w: int = 40, h: int = 30) -> np.ndarray:
    """全面赤のBGR画像を作る（H≈0のため赤ラップの検証に使う）。"""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (0, 0, 255)
    return img


def test_red_ratio_high() -> None:
    """全面赤は赤HSV範囲でほぼ1.0になる。"""
    img = red_image()
    ratio = preview_filter.color_ratio(img, [0, 100, 100], [10, 255, 255])
    assert ratio > 0.9


def test_red_hue_wrap() -> None:
    """0またぎ範囲（loH>hiH）でも赤を拾える。"""
    img = red_image()
    ratio = preview_filter.color_ratio(img, [170, 100, 100], [10, 255, 255])
    assert ratio > 0.4


def test_gray_out_keeps_shape_and_input() -> None:
    """gray_outはshape維持・対象外グレー化・入力不変。"""
    img = red_image()
    before = img.copy()
    out = preview_filter.apply_filter(img, [0, 100, 100], [10, 255, 255])
    assert out.shape == img.shape
    # 全面赤＝全画素が対象なので元色が維持される。
    assert np.array_equal(out, img)
    # 入力は変わらない（新配列を返す）。
    assert np.array_equal(img, before)
    assert out is not img


def test_gray_out_grays_non_target() -> None:
    """対象外はグレー化される（3chが等値になる）。"""
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    img[:, :] = (255, 0, 0)  # 青＝赤範囲の対象外
    out = preview_filter.apply_filter(img, [0, 100, 100], [10, 255, 255])
    assert out.shape == img.shape
    px = out[0, 0]
    assert int(px[0]) == int(px[1]) == int(px[2])


def test_mask_is_binary_bgr() -> None:
    """maskは白黒二値で各画素3ch等値。"""
    img = red_image()
    out = preview_filter.apply_filter(img, [0, 100, 100], [10, 255, 255], mode="mask")
    assert out.shape == img.shape
    assert bool(np.all(out[:, :, 0] == out[:, :, 1]))
    assert bool(np.all(out[:, :, 1] == out[:, :, 2]))
    values = set(np.unique(out).tolist())
    assert values <= {0, 255}


def test_validate_rejects_bad_values() -> None:
    """不正なHSVはValueError。"""
    with pytest.raises(ValueError):
        preview_filter.validate_hsv([200, 100, 100], [10, 255, 255])
    with pytest.raises(ValueError):
        preview_filter.validate_hsv([0, 100, 100], [10, 300, 255])
    with pytest.raises(ValueError):
        preview_filter.validate_hsv([0, 100], [10, 255, 255])


def test_crop_and_whole() -> None:
    """crop指定とNone全体の割合が一致する条件で比べる。"""
    img = red_image()
    whole = preview_filter.color_ratio(img, [0, 100, 100], [10, 255, 255])
    h, w = img.shape[:2]
    cropped = preview_filter.color_ratio(
        img, [0, 100, 100], [10, 255, 255], crop=[0, 0, w, h]
    )
    assert whole == pytest.approx(cropped)
    # 赤だけの左半分を切り出すと全面赤なので1.0になる。
    left = preview_filter.color_ratio(
        img, [0, 100, 100], [10, 255, 255], crop=[0, 0, w // 2, h]
    )
    assert left > 0.9
