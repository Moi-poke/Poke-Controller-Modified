#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aspect_lock.py - 「16:9 に固定」の寸法計算。

ウィンドウの縁をドラッグしている途中の枠を、中身（タイトルバー・メニュー・
縁を除いた所）が 16:9 になるよう直す。掴んだ縁は指に付いて動かし、反対側の
縁は動かさない。Tk にも Win32 にも触れない（core 層）。

縁の番号は Win32 の WMSZ_* と同じ値にしてある（WM_SIZING の wParam を
そのまま渡せる）。
"""

from __future__ import annotations

import math
from typing import Final

RATIO_W: Final[int] = 16
RATIO_H: Final[int] = 9

# 掴んだ縁（WMSZ_*）。
LEFT: Final[int] = 1
RIGHT: Final[int] = 2
TOP: Final[int] = 3
TOPLEFT: Final[int] = 4
TOPRIGHT: Final[int] = 5
BOTTOM: Final[int] = 6
BOTTOMLEFT: Final[int] = 7
BOTTOMRIGHT: Final[int] = 8

_LEFT_SIDE: Final[frozenset[int]] = frozenset({LEFT, TOPLEFT, BOTTOMLEFT})
_TOP_SIDE: Final[frozenset[int]] = frozenset({TOP, TOPLEFT, TOPRIGHT})

Rect = tuple[int, int, int, int]  # left, top, right, bottom（外形）
Size = tuple[int, int]


def _from_width(width: int, minimum: Size) -> Size:
    min_w, min_h = minimum
    width = max(width, min_w, math.ceil(min_h * RATIO_W / RATIO_H), 1)
    return width, max(1, round(width * RATIO_H / RATIO_W))


def _from_height(height: int, minimum: Size) -> Size:
    min_w, min_h = minimum
    height = max(height, min_h, math.ceil(min_w * RATIO_H / RATIO_W), 1)
    return max(1, round(height * RATIO_W / RATIO_H)), height


def snap_client(width: int, height: int, *, minimum: Size) -> Size:
    """固定を入れた瞬間の中身の寸法。今の幅を保ち、高さを合わせる。"""
    del height  # 幅を基準にする（幅の方が画面の並びに効く）。
    return _from_width(width, minimum)


def locked_client(edge: int, proposed: Size, minimum: Size) -> Size:
    """ドラッグ中の中身の寸法を 16:9 にする。

    左右の縁は幅、上下の縁は高さに合わせる。角はマウスの位置に一番近い
    16:9 の大きさ（16:9 の対角線へ投影した点）にする。角で「幅と高さの
    どちらが大きく動いたか」を毎回選ぶと、斜めに引いたときに数 px ごとに
    基準が入れ替わり、窓がガタガタと跳ねた。
    """
    width, height = proposed
    if edge in (LEFT, RIGHT):
        return _from_width(width, minimum)
    if edge in (TOP, BOTTOM):
        return _from_height(height, minimum)
    k = (width * RATIO_W + height * RATIO_H) / (RATIO_W**2 + RATIO_H**2)
    return _from_width(round(k * RATIO_W), minimum)


def locked_rect(edge: int, rect: Rect, frame: Size, minimum: Size) -> Rect:
    """WM_SIZING の枠（外形）を、中身が 16:9 になる枠へ直す。

    frame は外形と中身の差（タイトルバー・メニュー・縁の分）。
    """
    left, top, right, bottom = rect
    frame_w, frame_h = frame
    proposed = (right - left - frame_w, bottom - top - frame_h)
    width, height = locked_client(edge, proposed, minimum)
    outer_w, outer_h = width + frame_w, height + frame_h
    # 掴んだ側だけを動かし、反対側の縁は元の位置に残す。
    if edge in _LEFT_SIDE:
        left = right - outer_w
    else:
        right = left + outer_w
    if edge in _TOP_SIDE:
        top = bottom - outer_h
    else:
        bottom = top + outer_h
    return left, top, right, bottom
