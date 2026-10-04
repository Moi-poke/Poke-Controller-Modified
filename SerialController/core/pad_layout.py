#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pad_layout.py - 仮想コントローラのボタンの並びと当たり判定。

Pro コントローラを上から見た並び（左にスティックと十字キー、右に ABXY と
スティック、上に L/ZL・R/ZR）を論理座標で持つ。描画側（GuiAssets.ControllerGUI）
は倍率を掛けて描くだけにして、「どこを押したらどのボタンか」の判断を
ここ 1 箇所に閉じる。Tk には触れない（core 層）。

名前は Commands.Keys.Button の属性名、十字キーは旧 UI の HAT 名
（"UP" / "UP_RIGHT" …）をそのまま使う。送信側の処理を変えずに済む。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

# 論理座標の広さ。等倍（96dpi）で 1 単位 = 1px。旧 UI の 600x300 窓の半分以下。
PAD_W: Final[int] = 420
PAD_H: Final[int] = 170
# 押せる的の最小寸法（WCAG 2.2 のポインタの的 24px）。
MIN_TARGET: Final[int] = 24
# 広い所に置いても画面を占領しないよう、DPI 倍率に対してここまでしか広げない。
MAX_ZOOM: Final[float] = 1.5
# 別窓は利用者が窓の大きさで決めるので、大きめまで追従する。
FLOATING_MAX_ZOOM: Final[float] = 3.0
# 極端に狭いときも描画が壊れない下限。
MIN_SCALE: Final[float] = 0.25
# 十字キーの真ん中は向きが決まらないので何も押さない（半径に対する割合）。
_DPAD_DEAD: Final[float] = 0.25

# 0 度（右）から反時計回りに 45 度ずつ。斜めは角を押したときに入る。
DPAD_NAMES: Final[tuple[str, ...]] = (
    "RIGHT",
    "UP_RIGHT",
    "UP",
    "UP_LEFT",
    "LEFT",
    "DOWN_LEFT",
    "DOWN",
    "DOWN_RIGHT",
)


@dataclass(frozen=True, slots=True)
class PadShape:
    """1 つの的。cx/cy は中心、w/h は的の外接矩形の寸法（論理座標）。

    kind: "pill"（肩のボタン）/ "circle" / "square"（キャプチャ）/
    "stick"（スティック押し込み）/ "dpad"（十字キー。name は "DPAD"）。
    """

    name: str
    kind: str
    cx: float
    cy: float
    w: float
    h: float
    label: str


SHAPES: Final[tuple[PadShape, ...]] = (
    PadShape("ZL", "pill", 46, 17, 60, 24, "ZL"),
    PadShape("L", "pill", 112, 17, 60, 24, "L"),
    PadShape("R", "pill", 308, 17, 60, 24, "R"),
    PadShape("ZR", "pill", 374, 17, 60, 24, "ZR"),
    PadShape("LCLICK", "stick", 84, 76, 52, 52, "LS"),
    PadShape("DPAD", "dpad", 140, 128, 60, 60, ""),
    PadShape("MINUS", "circle", 176, 52, 24, 24, "−"),
    PadShape("PLUS", "circle", 244, 52, 24, 24, "+"),
    PadShape("CAPTURE", "square", 186, 92, 24, 24, ""),
    PadShape("HOME", "circle", 234, 92, 26, 26, "⌂"),
    PadShape("X", "circle", 336, 48, 28, 28, "X"),
    PadShape("Y", "circle", 308, 76, 28, 28, "Y"),
    PadShape("A", "circle", 364, 76, 28, 28, "A"),
    PadShape("B", "circle", 336, 104, 28, 28, "B"),
    PadShape("RCLICK", "stick", 280, 128, 52, 52, "RS"),
)


def dpad_direction(dx: float, dy: float, radius: float) -> str | None:
    """十字キーの中心からのずれ（画面座標、y は下向き）を 8 方向にする。"""
    dist = math.hypot(dx, dy)
    if dist > radius or dist < radius * _DPAD_DEAD:
        return None
    angle = math.degrees(math.atan2(-dy, dx)) % 360
    return DPAD_NAMES[int(((angle + 22.5) % 360) // 45)]


def hit_test(x: float, y: float) -> str | None:
    """論理座標の点にある的の名前。十字キーは向きの名前。無ければ None。"""
    for shape in SHAPES:
        dx, dy = x - shape.cx, y - shape.cy
        if shape.kind == "dpad":
            direction = dpad_direction(dx, dy, shape.w / 2)
            if direction is not None:
                return direction
            continue
        if shape.kind in ("circle", "stick"):
            if math.hypot(dx, dy) <= shape.w / 2:
                return shape.name
            continue
        if abs(dx) <= shape.w / 2 and abs(dy) <= shape.h / 2:
            return shape.name
    return None


# スティックを倒し切るまでのドラッグ量（論理座標）。スティックの外周の半径と同じ。
STICK_REACH: Final[float] = 26.0
# 押し込み（クリック）とドラッグを分ける動きのしきい値（論理座標）。
STICK_TAP_SLOP: Final[float] = 3.0
# スティックの名前（押し込みのボタン名）から、どちら側のスティックか。
STICK_SIDES: Final[dict[str, str]] = {"LCLICK": "L", "RCLICK": "R"}


def stick_value(dx: float, dy: float) -> tuple[int, int]:
    """中心からのドラッグ量（画面座標、y は下向き）を 0〜255 の座標にする。

    倒し量は STICK_REACH で 1 に飽和させ、向きは保つ（斜めでも円の内側）。
    計算と丸め（int）はプレビュー上のマウス操作（CaptureArea._stickXY）と揃える。
    """
    dist = math.hypot(dx, dy)
    if dist == 0:
        return 128, 128
    mag = min(1.0, dist / STICK_REACH)
    x = int(128 + mag * 127.5 * dx / dist)
    y = int(128 + mag * 127.5 * dy / dist)
    return max(0, min(255, x)), max(0, min(255, y))


def fit_scale(
    width: float, height: float, *, base: float, max_zoom: float = MAX_ZOOM
) -> float:
    """置き場所の広さに合わせた倍率。縦横比を保ち、DPI 倍率の max_zoom 倍まで。

    タブに埋め込むときは max_zoom=1（等倍より大きくしない）、別窓では
    窓の大きさに合わせて大きくできるよう max_zoom を上げて呼ぶ。
    """
    scale = min(width / PAD_W, height / PAD_H)
    return max(MIN_SCALE, min(scale, base * max_zoom))
