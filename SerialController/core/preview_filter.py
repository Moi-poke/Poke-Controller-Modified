"""preview_filter.py - HSVプレビューの純粋関数群.

カラーフィルタのプレビュー表示と割合計算のための、tkinter を使わない
純粋関数だけを集めたもの。画面の状態は一切見ないし、保持もしない。

色相は環状なので、赤のように 0 をまたぐ範囲は下限のほうが大きい値に
なる。そのまま inRange へ渡すと常に0件になり、「赤が無い」と誤判定
する。core/CommandVision.py の getColorRatio と同一条件で2つに割って
足す（後続の blockly_color や GuiAssets がこの振る舞いを前提にする）。
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

# OpenCV の HSV は H だけ 0〜179（S・V は 0〜255）。
_H_MAX = 179
_SV_MAX = 255

#: 色補正の既定値（全項目ニュートラル＝恒等変換）。
#: ガンマ 0.1〜3.0、コントラスト 0起点±2.0（倍率=1.0+値）、
#: 輝度 -100〜100、彩度 0.0〜3.0、色相シフト -90〜90。
#: config.py の [PreviewFilter] 既定値と揃えること（同期テストあり）。
DEFAULT_CORRECTION: dict[str, float | int] = {
    "gamma": 1.0,
    "contrast": 0.0,
    "brightness": 0,
    "saturation": 1.0,
    "hue_shift": 0,
}


def _check_side(value: object, name: str) -> list[int]:
    """片側の HSV 3整数を取り出す。形が違えば ValueError."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"HSV {name}は3整数で指定してください: {value!r}")
    nums: list[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"HSV {name}は3整数で指定してください: {value!r}")
        nums.append(v)
    if not 0 <= nums[0] <= _H_MAX:
        raise ValueError(f"HSV {name}のHは0〜179で指定してください: {nums[0]}")
    for i, label in ((1, "S"), (2, "V")):
        if not 0 <= nums[i] <= _SV_MAX:
            raise ValueError(
                f"HSV {name}のS・Vは0〜255で指定してください: {label}={nums[i]}"
            )
    return nums


def validate_hsv(lower: object, upper: object) -> tuple[list[int], list[int]]:
    """下限・上限の HSV を検証し、正規化した3整数リストの組で返す。

    H は 0〜179、S・V は 0〜255。それぞれ3整数でなければ ValueError。
    """
    return (_check_side(lower, "下限"), _check_side(upper, "上限"))


def hsv_mask(
    bgr: np.ndarray, lower: list[int] | tuple, upper: list[int] | tuple
) -> np.ndarray:
    """BGR 画像から HSV 範囲のマスク（0/255 の単チャンネル）を作る。

    H が環状なため、下限の H が上限より大きい（0またぎ）の場合は
    [loH..179] と [0..hiH] に割って OR する。
    """
    lo_vals, hi_vals = validate_hsv(lower, upper)
    hsv: np.ndarray = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lo = np.array(lo_vals, dtype=np.uint8)
    hi = np.array(hi_vals, dtype=np.uint8)
    if int(lo[0]) <= int(hi[0]):
        mask: np.ndarray = cv2.inRange(hsv, lo, hi)
        return mask
    lo1 = np.array([lo[0], lo[1], lo[2]], dtype=np.uint8)
    hi1 = np.array([_H_MAX, hi[1], hi[2]], dtype=np.uint8)
    lo2 = np.array([0, lo[1], lo[2]], dtype=np.uint8)
    hi2 = np.array([hi[0], hi[1], hi[2]], dtype=np.uint8)
    wrapped: np.ndarray = cv2.bitwise_or(
        cv2.inRange(hsv, lo1, hi1), cv2.inRange(hsv, lo2, hi2)
    )
    return wrapped


def color_ratio(
    bgr: np.ndarray,
    lower: list[int] | tuple,
    upper: list[int] | tuple,
    crop: list[int] | None = None,
) -> float:
    """指定範囲で、その色が占める割合 (0.0〜1.0) を返す。

    crop は実画素の [x1, y1, x2, y2]、None なら全体を見る。
    """
    target = bgr if crop is None else bgr[crop[1] : crop[3], crop[0] : crop[2]]
    mask = hsv_mask(target, lower, upper)
    total = float(mask.shape[0] * mask.shape[1])
    if total <= 0:
        return 0.0
    return float(np.count_nonzero(mask)) / total


def apply_filter(
    bgr: np.ndarray,
    lower: list[int] | tuple,
    upper: list[int] | tuple,
    mode: str = "gray_out",
) -> np.ndarray:
    """プレビュー用の加工画像を新配列で返す（入力は変えない）。

    "gray_out" は対象外だけグレー化し、対象画素は元色のまま残す。
    "mask" は白黒二値の BGR 画像にする。
    """
    mask = hsv_mask(bgr, lower, upper)
    if mode == "mask":
        binary: np.ndarray = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        return binary
    if mode == "gray_out":
        gray: np.ndarray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray_bgr: np.ndarray = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return np.where(mask[:, :, None] > 0, bgr, gray_bgr)
    raise ValueError(f"mode は gray_out/mask で指定してください: {mode!r}")


def validate_correction(corr: object) -> dict[str, float | int]:
    """色補正5項目を検証し、正規化した辞書で返す。

    gamma 0.1〜3.0、contrast -2.0〜+2.0（0起点。倍率=1.0+値）、
    brightness -100〜100（整数）、saturation 0.0〜3.0、
    hue_shift -90〜90（整数）。5キー過不足なく数値でなければ ValueError。
    """
    if not isinstance(corr, dict) or set(corr.keys()) != set(DEFAULT_CORRECTION.keys()):
        raise ValueError(
            "色補正は gamma/contrast/brightness/saturation/hue_shift"
            f" の5キーで指定してください: {corr!r}"
        )
    gamma = corr["gamma"]
    contrast = corr["contrast"]
    brightness = corr["brightness"]
    saturation = corr["saturation"]
    hue_shift = corr["hue_shift"]
    if (
        isinstance(gamma, bool)
        or not isinstance(gamma, (int, float))
        or not 0.1 <= float(gamma) <= 3.0
    ):
        raise ValueError(f"ガンマは0.1〜3.0で指定してください: {gamma!r}")
    if (
        isinstance(contrast, bool)
        or not isinstance(contrast, (int, float))
        or not -2.0 <= float(contrast) <= 2.0
    ):
        raise ValueError(f"コントラストは-2.0〜+2.0で指定してください: {contrast!r}")
    if (
        isinstance(brightness, bool)
        or not isinstance(brightness, int)
        or not -100 <= brightness <= 100
    ):
        raise ValueError(f"輝度は-100〜100の整数で指定してください: {brightness!r}")
    if (
        isinstance(saturation, bool)
        or not isinstance(saturation, (int, float))
        or not 0.0 <= float(saturation) <= 3.0
    ):
        raise ValueError(f"彩度は0.0〜3.0で指定してください: {saturation!r}")
    if (
        isinstance(hue_shift, bool)
        or not isinstance(hue_shift, int)
        or not -90 <= hue_shift <= 90
    ):
        raise ValueError(f"色相シフトは-90〜90の整数で指定してください: {hue_shift!r}")
    return {
        "gamma": float(gamma),
        "contrast": float(contrast),
        "brightness": brightness,
        "saturation": float(saturation),
        "hue_shift": hue_shift,
    }


def is_correction_neutral(corr: object) -> bool:
    """補正が全項目既定（恒等変換）なら True。形が違えば False。"""
    if not isinstance(corr, dict):
        return False
    try:
        checked = validate_correction(corr)
    except ValueError:
        return False
    return all(checked[k] == DEFAULT_CORRECTION[k] for k in DEFAULT_CORRECTION)


@lru_cache(maxsize=32)
def _gamma_lut(gamma: float) -> np.ndarray:
    """ガンマ補正のLUT（256要素）を作る。同じ値は使い回す。"""
    lut = np.arange(256, dtype=np.float32) / 255.0
    lut = np.power(lut, gamma) * 255.0
    return np.clip(lut, 0, 255).astype(np.uint8)


def apply_correction(
    bgr: np.ndarray, corr: dict[str, float | int] | object
) -> np.ndarray:
    """色補正をかけたBGR画像を新配列で返す（入力は変えない）。

    適用順はガンマ→輝度/コントラスト→彩度/色相シフト（OBSの
    色補正フィルタと同列）。全項目既定なら複写を返す。
    """
    checked = validate_correction(corr)
    gamma = float(checked["gamma"])
    contrast = float(checked["contrast"])
    brightness = int(checked["brightness"])
    saturation = float(checked["saturation"])
    hue_shift = int(checked["hue_shift"])
    if is_correction_neutral(checked):
        return bgr.copy()
    work: np.ndarray = bgr.copy()
    if gamma != 1.0:
        # LUTは1chずつ当てる（3ch一括だと色が混ざる）。
        # ch面は非連続のためdstに直接書けず、一時面経由で戻す。
        lut = _gamma_lut(gamma)
        for ch in range(3):
            work[:, :, ch] = cv2.LUT(work[:, :, ch], lut)
    alpha = max(0.0, 1.0 + contrast)
    if alpha != 1.0 or brightness != 0:
        work = cv2.convertScaleAbs(work, alpha=alpha, beta=float(brightness))
    if saturation != 1.0 or hue_shift != 0:
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV).astype(np.int16)
        if saturation != 1.0:
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation, 0, _SV_MAX)
        if hue_shift != 0:
            hsv[:, :, 0] = (hsv[:, :, 0] + hue_shift) % (_H_MAX + 1)
        work = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    return work
