"""プレビュー・設定タブ・ログの並べ方（core.pane_arrangement、Tk を使わない）。

配置のプリセットと表示/非表示から、ttk.PanedWindow の入れ子の形を決める。
組み立て側（ui/pane_layout.py）はこの形をそのまま作るだけにして、
「どの配置で何を並べるか」の判断をここ 1 箇所に閉じる。
"""

from __future__ import annotations

import pytest
from core.pane_arrangement import (
    ARRANGEMENTS,
    DEFAULT_ARRANGEMENT,
    Leaf,
    Split,
    arrangement_tree,
    describe,
    normalize_arrangement,
)


def test_the_presets_are_log_right_side_and_stack_with_log_right_as_default() -> None:
    # Then: 既定は今までと同じ見た目（ログが右）。
    assert [key for key, _label in ARRANGEMENTS] == ["log_right", "side", "stack"]
    assert DEFAULT_ARRANGEMENT == "log_right"


@pytest.mark.parametrize(
    ("arrangement", "shape"),
    [
        # 今までどおり: 左にプレビューとタブを縦、右にログ。
        ("log_right", "H[V[preview,tabs],log]"),
        # 横長の画面向け: 左にプレビュー、右にタブとログを縦。
        ("side", "H[preview,V[tabs,log]]"),
        # 縦長・狭い画面向け: 上から順に。
        ("stack", "V[preview,tabs,log]"),
    ],
)
def test_each_preset_nests_the_three_areas_in_its_own_shape(
    arrangement: str, shape: str
) -> None:
    tree = arrangement_tree(arrangement, show_tabs=True, show_log=True, stretch=True)
    assert describe(tree) == shape


@pytest.mark.parametrize(
    ("arrangement", "shape"),
    [
        ("log_right", "V[preview,tabs]"),
        # 右に並べる配置では、残ったタブはプレビューの右のまま。
        ("side", "H[preview,tabs]"),
        ("stack", "V[preview,tabs]"),
    ],
)
def test_hidden_areas_drop_out_and_single_child_splits_collapse(
    arrangement: str, shape: str
) -> None:
    # When: the log is collapsed.
    no_log = arrangement_tree(arrangement, show_tabs=True, show_log=False, stretch=True)

    # Then: ログが消え、子が 1 つだけになった分割はその子そのものになる。
    assert describe(no_log) == shape

    # When: both side areas are collapsed.
    alone = arrangement_tree(arrangement, show_tabs=False, show_log=False, stretch=True)

    # Then: プレビューだけ（分割は作らない）。
    assert alone == Leaf("preview", 3)


def test_hiding_only_the_tabs_keeps_the_preset_direction() -> None:
    # Then: ログを右に置く配置は、タブを畳んでもログは右のまま。
    tree = arrangement_tree("log_right", show_tabs=False, show_log=True, stretch=True)
    assert describe(tree) == "H[preview,log]"
    tree = arrangement_tree("stack", show_tabs=False, show_log=True, stretch=True)
    assert describe(tree) == "V[preview,log]"


def test_a_stretching_preview_takes_most_of_the_spare_space() -> None:
    # Given: fit mode (the preview follows the window).
    tree = arrangement_tree("stack", show_tabs=True, show_log=True, stretch=True)

    # Then: 窓を広げた分は主にプレビューへ回る。
    assert isinstance(tree, Split)
    weights = {
        child.name: child.weight for child in tree.children if isinstance(child, Leaf)
    }
    assert weights["preview"] > weights["tabs"]
    assert weights["preview"] > weights["log"]


def test_a_fixed_preview_gives_the_spare_space_to_the_other_areas() -> None:
    # Given: fixed size mode (the preview does not grow).
    tree = arrangement_tree("stack", show_tabs=True, show_log=True, stretch=False)

    # Then: 伸びないプレビューに余白を渡さない（空白が増えるだけ）。
    assert isinstance(tree, Split)
    weights = {
        child.name: child.weight for child in tree.children if isinstance(child, Leaf)
    }
    assert weights["preview"] == 0
    assert weights["tabs"] > 0 and weights["log"] > 0


def test_unknown_arrangements_fall_back_to_the_default() -> None:
    # Then: 手で書き換えた ini でも起動を止めない。
    assert normalize_arrangement("diagonal") == DEFAULT_ARRANGEMENT
    assert normalize_arrangement("side") == "side"
    assert describe(
        arrangement_tree("diagonal", show_tabs=True, show_log=True, stretch=True)
    ) == describe(
        arrangement_tree(
            DEFAULT_ARRANGEMENT, show_tabs=True, show_log=True, stretch=True
        )
    )


def test_sash_ratios_round_trip_through_the_settings_text() -> None:
    from core.pane_arrangement import format_sash_ratios, parse_sash_ratios

    ratios = {"H[V[preview,tabs],log]": [0.7], "V[preview,tabs]": [0.55]}

    # Then: 書いて読めば同じ（ini の 1 キーに収める）。
    assert parse_sash_ratios(format_sash_ratios(ratios)) == ratios


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[0.5]",
        '{"V[preview,tabs]": "half"}',
        '{"V[preview,tabs]": [1.5]}',
        '{"V[preview,tabs]": [-0.1]}',
    ],
)
def test_broken_sash_text_reads_as_no_saved_positions(text: str) -> None:
    from core.pane_arrangement import parse_sash_ratios

    # Then: 壊れた値は捨てる（起動を止めず、既定の位置で始める）。
    assert parse_sash_ratios(text) == {}
