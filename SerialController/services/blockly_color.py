#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""色割合とフィルタ試し打ち（GUI非依存）。

`core.preview_filter` の割合・加工を、編集器の試し打ち向けに
PNGバイト列か復号済みBGR画像（ndarray）で受けられる形にする。
検証に落ちたら例外ではなく err 文字列で返す。
"""

from __future__ import annotations

import cv2
import numpy as np
from core import preview_filter
from services import blockly_match


def parse_hsv_pair(lower: object, upper: object) -> tuple[list[int], list[int]]:
    """下限・上限のHSVを検査し、正規化した3整数リストの組で返す。"""
    return preview_filter.validate_hsv(lower, upper)


def _src_from(frame_png: bytes | np.ndarray) -> np.ndarray:
    """PNGバイト列かBGR画像をBGR画像にする。読めなければ ValueError。"""
    if isinstance(frame_png, np.ndarray):
        # 復号済みの受け口。空・次元不足は従来どおり読込失敗扱いにする。
        if frame_png.size == 0 or frame_png.ndim < 2:
            raise ValueError("画像を読み込めませんでした（取り直してください）")
        return frame_png
    return blockly_match.decode_frame_png(frame_png)


def ratio_on_png(
    frame_png: bytes | np.ndarray,
    lower: object,
    upper: object,
    crop: object,
) -> tuple[float, str | None]:
    """指定HSV範囲の割合 (0.0〜1.0) を求める。失敗時は (0.0, 理由)。"""
    try:
        lower_list, upper_list = parse_hsv_pair(lower, upper)
    except ValueError as e:
        return 0.0, str(e)
    try:
        src = _src_from(frame_png)
    except ValueError as e:
        return 0.0, str(e)
    try:
        crop_list = blockly_match.parse_crop(crop, int(src.shape[1]), int(src.shape[0]))
    except ValueError as e:
        return 0.0, str(e)
    return preview_filter.color_ratio(src, lower_list, upper_list, crop_list), None


def filter_png(
    frame_png: bytes | np.ndarray,
    lower: object,
    upper: object,
    mode: object,
    crop: object = None,
) -> tuple[bytes | None, float, str | None]:
    """加工プレビューをPNGバイト列で返す。失敗時は (None, 0.0, 理由)。"""
    m = str(mode)
    if m not in ("gray_out", "mask"):
        return None, 0.0, "modeは gray_out / mask で送ってください"
    ratio, err = ratio_on_png(frame_png, lower, upper, crop)
    if err is not None:
        return None, 0.0, err
    try:
        lower_list, upper_list = parse_hsv_pair(lower, upper)
        src = _src_from(frame_png)
        crop_list = blockly_match.parse_crop(crop, int(src.shape[1]), int(src.shape[0]))
    except ValueError as e:
        return None, 0.0, str(e)
    target = (
        src
        if crop_list is None
        else src[crop_list[1] : crop_list[3], crop_list[0] : crop_list[2]]
    )
    try:
        filtered = preview_filter.apply_filter(target, lower_list, upper_list, m)
    except ValueError as e:
        return None, 0.0, str(e)
    ok, buf = cv2.imencode(".png", filtered)
    if not ok:
        return None, 0.0, "画像を符号化できませんでした"
    return bytes(buf), ratio, None
