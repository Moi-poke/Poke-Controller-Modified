"""16:9 固定の寸法計算（core.aspect_lock）。Tk を作らない。

ウィンドウの縁をドラッグしている途中の枠（WM_SIZING の RECT）を、中身が
16:9 になるよう直す計算。掴んだ縁は利用者の指に付いて動き、反対側の縁は
動かさない。
"""

from __future__ import annotations

import pytest
from core.aspect_lock import (
    BOTTOM,
    BOTTOMLEFT,
    BOTTOMRIGHT,
    LEFT,
    RIGHT,
    TOP,
    TOPLEFT,
    locked_rect,
    snap_client,
)

# 枠（タイトルバー・メニュー・縁）の分。中身はこれを引いた残り。
FRAME = (16, 70)


def _client(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = rect
    return right - left - FRAME[0], bottom - top - FRAME[1]


def _rect_for(client_w: int, client_h: int) -> tuple[int, int, int, int]:
    return (100, 100, 100 + client_w + FRAME[0], 100 + client_h + FRAME[1])


@pytest.mark.parametrize("edge", [LEFT, RIGHT, TOP, BOTTOM, TOPLEFT, BOTTOMRIGHT])
def test_any_drag_keeps_the_client_area_at_16_to_9(edge: int) -> None:
    # When: the user drags an edge to an arbitrary, non-16:9 size.
    proposed = _rect_for(1100, 450)

    rect = locked_rect(edge, proposed, FRAME, previous=(800, 450), minimum=(1, 1))

    # Then: 中身は 16:9（整数に丸めた 1px 以内）。
    width, height = _client(rect)
    assert abs(width * 9 - height * 16) <= 16


def test_dragging_the_right_edge_sets_the_height_from_the_width() -> None:
    rect = locked_rect(RIGHT, _rect_for(1120, 450), FRAME, (800, 450), (1, 1))

    # Then: 横に広げた分だけ下へ伸びる。左上は動かない。
    assert _client(rect) == (1120, 630)
    assert rect[:2] == (100, 100)


def test_dragging_the_bottom_edge_sets_the_width_from_the_height() -> None:
    # Given: 下の縁だけを引き下げた（幅は元のまま）。
    rect = locked_rect(BOTTOM, _rect_for(800, 630), FRAME, (800, 450), (1, 1))

    # Then: 高さに合わせて幅が広がる（下の縁のドラッグが打ち消されない）。
    assert _client(rect) == (1120, 630)
    assert rect[:2] == (100, 100)


def test_dragging_the_left_edge_keeps_the_right_edge_in_place() -> None:
    proposed = (100 - 320, 100, 100 + 800 + FRAME[0], 100 + 450 + FRAME[1])

    rect = locked_rect(LEFT, proposed, FRAME, (800, 450), (1, 1))

    # Then: 掴んだ左の縁が指に付いて動き、右の縁は動かない。
    assert rect[0] == 100 - 320
    assert rect[2] == 100 + 800 + FRAME[0]
    assert _client(rect) == (1120, 630)


def test_dragging_the_top_left_corner_keeps_the_bottom_right_corner() -> None:
    right, bottom = 100 + 800 + FRAME[0], 100 + 450 + FRAME[1]
    proposed = (100 - 320, 100 - 10, right, bottom)

    rect = locked_rect(TOPLEFT, proposed, FRAME, (800, 450), (1, 1))

    # Then: 反対の角（右下）は動かず、大きく動いた向き（横）に合わせる。
    assert (rect[2], rect[3]) == (right, bottom)
    assert _client(rect) == (1120, 630)


def test_a_mostly_vertical_corner_drag_follows_the_height() -> None:
    # Given: 右下の角を、ほとんど真下へ引いた。
    rect = locked_rect(BOTTOMRIGHT, _rect_for(810, 630), FRAME, (800, 450), (1, 1))

    # Then: 大きく動いた向き（縦）に合わせる（縦に引いたのに縮まない）。
    assert _client(rect) == (1120, 630)


def test_the_locked_size_never_goes_below_the_minimum() -> None:
    # When: dragged far smaller than the window's minimum size.
    rect = locked_rect(BOTTOMLEFT, _rect_for(100, 50), FRAME, (800, 450), (640, 300))

    # Then: 最小寸法の両方を満たす一番小さい 16:9 で止まる。
    width, height = _client(rect)
    assert width >= 640
    assert height >= 300
    assert abs(width * 9 - height * 16) <= 16


def test_snapping_on_enable_keeps_the_width_and_fixes_the_height() -> None:
    # Then: 有効にした瞬間は今の幅を保ち、高さを 16:9 に合わせる。
    assert snap_client(1280, 900, minimum=(1, 1)) == (1280, 720)
    assert snap_client(800, 300, minimum=(640, 480)) == (854, 480)
