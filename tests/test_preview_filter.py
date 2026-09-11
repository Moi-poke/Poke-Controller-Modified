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


def neutral_correction() -> dict:
    """全項目が既定（恒等変換）の補正辞書を作る。"""
    return dict(preview_filter.DEFAULT_CORRECTION)


def test_correction_neutral_is_identity() -> None:
    """既定補正は見た目を変えない（新配列で返す）。"""
    img = red_image()
    before = img.copy()
    out = preview_filter.apply_correction(img, neutral_correction())
    assert out is not img
    assert np.array_equal(out, img)
    assert np.array_equal(img, before)


def test_correction_gamma_darkens() -> None:
    """ガンマ>1は中間調を暗くする（白は白のまま）。"""
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[:, :] = (128, 128, 128)
    out = preview_filter.apply_correction(img, {**neutral_correction(), "gamma": 2.0})
    assert out.shape == img.shape
    # 128^(2.0) スケールで約64になる（LUT丸めで±1許す）。
    assert abs(int(out[0, 0, 0]) - 64) <= 1
    white = np.zeros((2, 2, 3), dtype=np.uint8)
    white[:, :] = (255, 255, 255)
    assert np.array_equal(
        preview_filter.apply_correction(white, {**neutral_correction(), "gamma": 2.0}),
        white,
    )


def test_correction_brightness_shifts() -> None:
    """輝度+100は全画素を100上げ（255で頭打ち）。"""
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[:, :] = (100, 100, 100)
    out = preview_filter.apply_correction(
        img, {**neutral_correction(), "brightness": 100}
    )
    assert int(out[0, 0, 0]) == 200


def test_correction_contrast_zero_origin() -> None:
    """コントラストは0起点±2。+1.0で倍率2.0、-1.0で0.0（平坦）。"""
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[:, :] = (100, 100, 100)
    doubled = preview_filter.apply_correction(
        img, {**neutral_correction(), "contrast": 1.0}
    )
    assert int(doubled[0, 0, 0]) == 200
    flat = preview_filter.apply_correction(
        img, {**neutral_correction(), "contrast": -1.0}
    )
    assert int(flat[0, 0, 0]) == 0


def test_correction_saturation_zero_grays() -> None:
    """彩度0はグレー化（3ch等値）。"""
    img = red_image()
    out = preview_filter.apply_correction(
        img, {**neutral_correction(), "saturation": 0.0}
    )
    px = out[0, 0]
    assert int(px[0]) == int(px[1]) == int(px[2])


def test_correction_hue_shift_wraps() -> None:
    """色相シフトはH環上で回る（赤+90で緑系になる）。"""
    img = red_image()
    out = preview_filter.apply_correction(
        img, {**neutral_correction(), "hue_shift": 60}
    )
    import cv2

    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    # 赤H≈0に+60で緑付近（OpenCVのH=60。±丸め）。
    assert abs(int(hsv[0, 0, 0]) - 60) <= 2


def test_validate_correction_rejects_bad_values() -> None:
    """範囲外・型違いの補正はValueError。"""
    base = neutral_correction()
    with pytest.raises(ValueError):
        preview_filter.validate_correction({**base, "gamma": 0.05})
    with pytest.raises(ValueError):
        preview_filter.validate_correction({**base, "contrast": 2.5})
    with pytest.raises(ValueError):
        preview_filter.validate_correction({**base, "brightness": 101})
    with pytest.raises(ValueError):
        preview_filter.validate_correction({**base, "saturation": -0.1})
    with pytest.raises(ValueError):
        preview_filter.validate_correction({**base, "hue_shift": 91})
    with pytest.raises(ValueError):
        preview_filter.validate_correction({"gamma": 1.0})
    with pytest.raises(ValueError):
        preview_filter.validate_correction({**base, "gamma": True})


def test_is_correction_neutral() -> None:
    """既定だけが中立と判定される。"""
    assert preview_filter.is_correction_neutral(neutral_correction()) is True
    assert (
        preview_filter.is_correction_neutral({**neutral_correction(), "brightness": 1})
        is False
    )


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
