"""仮想コントローラの並びと当たり判定（core.pad_layout、Tk を使わない）。

ボタンの位置・大きさ・押した場所からどのボタンかを決める判断をここに閉じ、
描画（GuiAssets.ControllerGUI）は形を拡大縮小して描くだけにする。
"""

from __future__ import annotations

import math

import pytest
from Commands.Keys import Button
from core.pad_layout import (
    DPAD_NAMES,
    MIN_TARGET,
    PAD_H,
    PAD_W,
    SHAPES,
    fit_scale,
    hit_test,
)

_BUTTONS = [
    "A",
    "B",
    "X",
    "Y",
    "L",
    "R",
    "ZL",
    "ZR",
    "LCLICK",
    "RCLICK",
    "MINUS",
    "PLUS",
    "CAPTURE",
    "HOME",
]


def test_every_switch_button_has_exactly_one_shape_named_after_the_button_enum() -> (
    None
):
    # Then: 名前は Commands.Keys.Button の属性そのまま（送信側でそのまま引ける）。
    names = [s.name for s in SHAPES if s.kind != "dpad"]
    assert sorted(names) == sorted(_BUTTONS)
    for name in names:
        assert hasattr(Button, name)
    assert [s.kind for s in SHAPES].count("dpad") == 1


@pytest.mark.parametrize("name", _BUTTONS)
def test_pressing_the_centre_of_a_button_hits_that_button(name: str) -> None:
    shape = next(s for s in SHAPES if s.name == name)
    assert hit_test(shape.cx, shape.cy) == name


@pytest.mark.parametrize(
    ("angle", "name"),
    [
        (0, "RIGHT"),
        (45, "UP_RIGHT"),
        (90, "UP"),
        (135, "UP_LEFT"),
        (180, "LEFT"),
        (225, "DOWN_LEFT"),
        (270, "DOWN"),
        (315, "DOWN_RIGHT"),
    ],
)
def test_the_dpad_maps_the_pressed_direction_including_diagonals(
    angle: int, name: str
) -> None:
    # Given: 十字キーの中心から、指定の向きへ少し離れた点（画面座標は y が下向き）。
    pad = next(s for s in SHAPES if s.kind == "dpad")
    r = pad.w / 2 * 0.7
    x = pad.cx + r * math.cos(math.radians(angle))
    y = pad.cy - r * math.sin(math.radians(angle))

    # Then: 8 方向のどれかになる。斜めは角を押せば入る（旧 UI の斜めボタンの代わり）。
    assert hit_test(x, y) == name
    assert name in DPAD_NAMES


def test_the_dpad_centre_is_a_dead_zone() -> None:
    # Then: 真ん中では向きが決まらないので何も押さない。
    pad = next(s for s in SHAPES if s.kind == "dpad")
    assert hit_test(pad.cx, pad.cy) is None


def test_empty_body_and_outside_the_pad_hit_nothing() -> None:
    assert hit_test(PAD_W / 2, PAD_H - 4) is None
    assert hit_test(-10, -10) is None
    assert hit_test(PAD_W + 10, PAD_H / 2) is None


def test_targets_are_big_enough_and_do_not_overlap_inside_the_pad() -> None:
    # Then: 等倍（96dpi で 1 単位 = 1px）でも 24px 以上の的にする（WCAG 2.2 の最小）。
    boxes = []
    for s in SHAPES:
        assert s.w >= MIN_TARGET and s.h >= MIN_TARGET, s.name
        x0, y0, x1, y1 = s.cx - s.w / 2, s.cy - s.h / 2, s.cx + s.w / 2, s.cy + s.h / 2
        assert 0 <= x0 and x1 <= PAD_W and 0 <= y0 and y1 <= PAD_H, s.name
        boxes.append((s.name, x0, y0, x1, y1))
    # 的同士が重なると、押した場所と違うボタンが入る。
    for i, (na, ax0, ay0, ax1, ay1) in enumerate(boxes):
        for nb, bx0, by0, bx1, by1 in boxes[i + 1 :]:
            assert ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0, (na, nb)


def test_the_pad_is_far_smaller_than_the_old_600x300_window() -> None:
    # Then: 旧 UI（600x300 の窓）の半分以下の面積で全部のボタンが並ぶ。
    assert PAD_W * PAD_H <= 600 * 300 / 2


def test_fit_scale_follows_the_space_but_keeps_the_aspect_and_a_cap() -> None:
    # Then: 幅・高さの狭い方に合わせる（縦横比を保つ）。
    assert fit_scale(PAD_W, PAD_H * 10, base=1.0) == pytest.approx(1.0)
    assert fit_scale(PAD_W * 10, PAD_H, base=1.0) == pytest.approx(1.0)
    assert fit_scale(PAD_W / 2, PAD_H, base=1.0) == pytest.approx(0.5)
    # 広い所でも大きくなりすぎない（画面を占領しない）。
    assert fit_scale(PAD_W * 10, PAD_H * 10, base=1.0) == pytest.approx(1.5)
    # 高 DPI では上限も DPI 倍。
    assert fit_scale(PAD_W * 10, PAD_H * 10, base=2.0) == pytest.approx(3.0)
    # 極端に狭くても潰れきらない（0 や負で描画が壊れない）。
    assert fit_scale(1, 1, base=1.0) > 0.2
    # タブに埋め込むときは等倍まで（max_zoom=1）。別窓は大きくできる。
    assert fit_scale(PAD_W * 10, PAD_H * 10, base=1.0, max_zoom=1.0) == 1.0
    assert fit_scale(PAD_W * 3, PAD_H * 3, base=1.0, max_zoom=3.0) == 3.0
