#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""display_mode.py - プレビュー表示モードの判断（純粋ロジック・Tkなし）。

プレビューをどう置くかは 2 択しか無いので、このファイルにまとめる。

  fixed … プリセット（640x360 など）で固定。ウィンドウの広さに依存しない。
  fit   … ウィンドウに合わせて拡大縮小する。描画面が 16:9 を保つので、
          映像は枠の中央に置かれ、余った部分は黒い余白になる。

どちらのモードも Menubar の表示設定ダイアログから選ぶ。ここは候補と分岐だけを持ち、
Tk の変数もウィジェットも触らない（ヘッドレスで検証できるように）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# 表示モード。fixed … プリセットで固定 / fit … 枠に合わせて拡大縮小
SHOW_MODES: Final[tuple[str, str]] = ("fixed", "fit")
# 固定モードのプリセット。候補は WindowUtils.SHOW_SIZE_VALUES と同じ中身の複製で、
# 設定の補正（config.complete_missing）もここを参照する。
SHOW_SIZES: Final[tuple[str, ...]] = ("640x360", "960x540", "1280x720", "1920x1080")
DEFAULT_SHOW_MODE: Final[str] = "fixed"
DEFAULT_SHOW_SIZE: Final[str] = "640x360"
# fit のときの要求サイズ。ウィンドウがこれより小さくても要求寸法は下がらない
# （小さいときは枠の中に収まるよう縮んで表示される）。
FIT_REQUEST_SIZE: Final[tuple[int, int]] = (640, 360)


@dataclass(frozen=True, slots=True)
class PreviewLayout:
    """プレビュー枠へ要求する配置。

    stretch      … True なら枠いっぱいまで伸ばす（fit）。False なら要求サイズのまま。
    request_size … (幅, 高さ)。枠の要求寸法で、中央寄せの基準になる。
    """

    stretch: bool
    request_size: tuple[int, int]


def parse_show_size(text: str) -> tuple[int, int]:
    """``'1280x720'`` を ``(幅, 高さ)`` へ。候補に無ければ既定値として解釈する。

    settings.ini を手で書き間違えたまま起動しても落ちないことだけが目的。
    「何を監視するか」まではこのファイルの責務ではない。
    """
    value = (text or "").strip()
    if value not in SHOW_SIZES:
        value = DEFAULT_SHOW_SIZE
    width, _, height = value.partition("x")
    return int(width), int(height)


def preview_layout(mode: str, show_size: str) -> PreviewLayout:
    """表示モードと固定サイズから、枠の要求（伸ばすかどうか）を決める。

    不明なモードは fixed 扱い。補完（config.complete_missing）を抜けても
    ウィンドウそのものは壊さないため。
    """
    if mode == "fit":
        # 固定サイズはこのモードでは選ばれていないため参照しない。
        return PreviewLayout(stretch=True, request_size=FIT_REQUEST_SIZE)
    return PreviewLayout(stretch=False, request_size=parse_show_size(show_size))
