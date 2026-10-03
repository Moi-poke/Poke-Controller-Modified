#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pane_arrangement.py - プレビュー・設定タブ・ログの並べ方を決める。

配置のプリセット（ログを右 / 右に並べる / 縦一列）と、各欄の表示/非表示から、
ttk.PanedWindow の入れ子の形を返す。組み立て（ui/pane_layout.py）はこの形を
そのまま作るだけにして、「どの配置で何を並べるか」の判断をここ 1 箇所に閉じる。
Tk には触れない（core 層）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

# (キー, メニューに出す名前)。キーは settings.ini の [Window] arrangement。
ARRANGEMENTS: Final[tuple[tuple[str, str], ...]] = (
    ("log_right", "ログを右"),
    ("side", "右にタブとログ"),
    ("stack", "縦一列"),
)
# 既定は今までと同じ見た目。
DEFAULT_ARRANGEMENT: Final[str] = "log_right"

# 窓を広げた分の配り方。プレビューが窓に合わせて伸びるときは主にプレビューへ。
# 伸びない（固定サイズ）ときに渡すと空白が増えるだけなので 0 にする。
_PREVIEW_STRETCH_WEIGHT: Final[int] = 3
_SIDE_WEIGHT: Final[int] = 1

HORIZONTAL: Final[str] = "horizontal"
VERTICAL: Final[str] = "vertical"


@dataclass(frozen=True, slots=True)
class Leaf:
    """1 つの欄（preview / tabs / log）。weight は親の分割での取り分。"""

    name: str
    weight: int


@dataclass(frozen=True, slots=True)
class Split:
    """欄を縦または横に並べる分割。"""

    orient: str
    children: tuple[Leaf | Split, ...]
    weight: int


Node = Leaf | Split


def normalize_arrangement(value: str) -> str:
    """候補に無い配置は既定へ落とす（壊れた設定で起動を止めない）。"""
    keys = [key for key, _label in ARRANGEMENTS]
    return value if value in keys else DEFAULT_ARRANGEMENT


def _split(orient: str, children: list[Node | None]) -> Node | None:
    """隠れた欄を抜き、子が 1 つになった分割はその子そのものにする。"""
    kept = [child for child in children if child is not None]
    if not kept:
        return None
    if len(kept) == 1:
        return kept[0]
    weight = max(1, sum(child.weight for child in kept))
    return Split(orient=orient, children=tuple(kept), weight=weight)


def arrangement_tree(
    arrangement: str, *, show_tabs: bool, show_log: bool, stretch: bool
) -> Node:
    """配置と表示/非表示から入れ子の形を決める。プレビューは常に出す。"""
    preview = Leaf("preview", _PREVIEW_STRETCH_WEIGHT if stretch else 0)
    tabs = Leaf("tabs", _SIDE_WEIGHT) if show_tabs else None
    log = Leaf("log", _SIDE_WEIGHT) if show_log else None

    key = normalize_arrangement(arrangement)
    if key == "side":
        tree = _split(HORIZONTAL, [preview, _split(VERTICAL, [tabs, log])])
    elif key == "stack":
        tree = _split(VERTICAL, [preview, tabs, log])
    else:
        tree = _split(HORIZONTAL, [_split(VERTICAL, [preview, tabs]), log])
    # preview は必ずあるので None にはならない。
    assert tree is not None
    return tree


def describe(node: Node) -> str:
    """形を短い文字列にする（テストとログ用）。例: ``H[V[preview,tabs],log]``。"""
    if isinstance(node, Leaf):
        return node.name
    head = "H" if node.orient == HORIZONTAL else "V"
    return f"{head}[{','.join(describe(child) for child in node.children)}]"


def format_sash_ratios(ratios: dict[str, list[float]]) -> str:
    """仕切り位置（形ごとの割合）を ini の 1 キーに収める文字列にする。"""
    rounded = {key: [round(v, 3) for v in values] for key, values in ratios.items()}
    return json.dumps(rounded, ensure_ascii=True, sort_keys=True)


def parse_sash_ratios(text: str) -> dict[str, list[float]]:
    """format_sash_ratios の逆。壊れていたら空（既定の位置で始める）。"""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[float]] = {}
    for key, values in data.items():
        if not isinstance(key, str) or not isinstance(values, list):
            return {}
        if not all(isinstance(v, int | float) and 0.0 <= v <= 1.0 for v in values):
            return {}
        out[key] = [float(v) for v in values]
    return out
