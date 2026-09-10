#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テンプレート照合の試し打ち（GUI非依存）。

実行時（`core.CommandVision.isContainTemplate`）と同一条件で
`TM_CCOEFF_NORMED` の最大一致度を求める。テンプレ読込は core の
`_imread_or_raise` を使い、存在・破損の文言も実行時と同じにする。
"""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from loguru import logger
from services import blockly_capture

#: upload画像の上限（localhostのため緩めの安全弁）。
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass
class MatchResult:
    """照合の結果。statusは ok / failed。"""

    status: str
    message: str
    score: float = 0.0
    matched: bool = False
    count: int = 0
    rect: dict[str, int] = field(default_factory=dict)


def parse_threshold(value: object) -> float:
    """しきい値（0〜1の数値）を検査する。おかしければ ValueError。"""
    try:
        threshold = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("しきい値は0〜1の数値で書いてください") from None
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("しきい値は0〜1の数値で書いてください")
    return threshold


def parse_crop(value: object, width: int, height: int) -> list[int] | None:
    """切出し範囲（実画像画素 `[x1, y1, x2, y2]`）を検査する。Noneは全体。おかしければ ValueError。"""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("範囲は [x1, y1, x2, y2] で送ってください")
    try:
        x1, y1, x2, y2 = (int(v) for v in value)
    except (TypeError, ValueError):
        raise ValueError("範囲は [x1, y1, x2, y2] の整数で送ってください") from None
    if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x1 >= x2 or y1 >= y2:
        raise ValueError(f"範囲が画像({width}x{height})の外を指しています")
    return [x1, y1, x2, y2]


def count_hits(
    res: np.ndarray, threshold: float, tw: int, th: int, max_count: int = 20
) -> int:
    """`findAllTemplates` と同一の重複除去で件数を数える（中心座標は返さない）。"""
    ys, xs = np.where(res >= threshold)
    if len(xs) == 0:
        return 0
    order = np.argsort(res[ys, xs])[::-1]
    near_x, near_y = max(1, tw // 2), max(1, th // 2)
    found: list[tuple[int, int]] = []
    for i in order:
        x, y = int(xs[i]), int(ys[i])
        if any(abs(x - px) < near_x and abs(y - py) < near_y for px, py in found):
            continue
        found.append((x, y))
        if len(found) >= max_count:
            break
    return len(found)


def decode_frame_png(png: bytes) -> np.ndarray:
    """PNGバイト列をBGR画像にする。読めなければ ValueError。"""
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("画像を読み込めませんでした（取り直してください）")
    return img


def decode_upload_image(b64: str) -> bytes:
    """base64の画像送付をPNGバイト列に戻す。おかしければ ValueError。"""
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("画像を読み込めませんでした（取り直してください）") from None
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("画像が大きすぎます（10MBまで）")
    decode_frame_png(raw)
    return raw


def match_template(
    app_dir: str | Path,
    frame_png: bytes,
    template: str,
    threshold: object,
    use_gray: bool = True,
    crop: object = None,
) -> MatchResult:
    """1枚の画像内でテンプレの最大一致度を求める。検証に落ちたらfailedで返す。"""
    name = str(template).strip()
    reason = blockly_capture.validate_template_name(name)
    if reason is not None:
        return MatchResult(status="failed", message=f"照合できません: {reason}")
    try:
        limit = parse_threshold(threshold)
    except ValueError as e:
        return MatchResult(status="failed", message=f"照合できません: {e}")
    try:
        src_img = decode_frame_png(frame_png)
    except ValueError as e:
        return MatchResult(status="failed", message=f"照合できません: {e}")
    height0, width0 = int(src_img.shape[0]), int(src_img.shape[1])
    try:
        crop_list = parse_crop(crop, width0, height0)
    except ValueError as e:
        return MatchResult(status="failed", message=f"照合できません: {e}")
    if crop_list is not None:
        x1, y1, x2, y2 = crop_list
        src_img = src_img[y1:y2, x1:x2]
        dx, dy = x1, y1
    else:
        dx, dy = 0, 0
    from core.CommandVision import VisionMixin, _imread_or_raise

    flags = cv2.IMREAD_GRAYSCALE if use_gray else cv2.IMREAD_COLOR
    try:
        src = cv2.cvtColor(src_img, cv2.COLOR_BGR2GRAY) if use_gray else src_img
        # 資源は APP_DIR 起点で読む（services では os.chdir しない）。
        # 絶対化して渡すと `_imread_or_raise` はそのまま読む
        # （TEMPLATE_PATH へは繋がない）。存在・破損の文言は実行時と同じ。
        filespec = str(
            Path(app_dir)
            / "Template"
            / PurePosixPath(name.replace("\\", "/")).as_posix()
        )
        template_img = _imread_or_raise(filespec, flags)
        VisionMixin._checkTemplate(src, template_img, None)
    except (ValueError, FileNotFoundError, cv2.error) as e:
        logger.warning(f"テンプレ照合の準備に失敗: {e}")
        return MatchResult(status="failed", message=f"照合できません: {e}")
    res = cv2.matchTemplate(src, template_img, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    score = float(max_val)
    height, width = int(template_img.shape[0]), int(template_img.shape[1])
    rect = {
        "x": int(max_loc[0]) + dx,
        "y": int(max_loc[1]) + dy,
        "width": width,
        "height": height,
    }
    matched = score >= limit
    count = count_hits(res, limit, width, height)
    tail = "ヒット" if matched else "ミス"
    logger.info(
        f"テンプレ照合: {name} 一致度={score:.3f} しきい値={limit} → {tail} {count}件"
    )
    return MatchResult(
        status="ok",
        message=f"一致度 {score * 100:.1f}%（しきい値 {limit * 100:.1f}%）→ {tail} {count}件",
        score=score,
        matched=matched,
        count=count,
        rect=rect,
    )
