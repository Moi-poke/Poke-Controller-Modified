#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""display_mode.py - プレビュー表示モードと判断だけの集約（純粋ロジック・Tkなし）。

プレビューをどう置くかは 2 択しか無いので、このファイルにまとめる。

  fixed … プリセット（640x360 など）で固定。ウィンドウの広さに依存しない。
  fit   … ウィンドウに合わせて拡大縮小する。描画面が 16:9 を保つので、
          映像は枠の中央に置かれ、余った部分は黒い余白になる。

さらに複数台を 1 画面に並べるため、ウィンドウ全体のレイアウトもここで決める。

  standard … 従来どおりの画面構成。タブ・ログ・カメラ欄の操作部を全部出す。
  compact  … 小さく詰める。タブ・ログ・操作部を隠し、1 行の操作バーだけを出す。
  preview  … プレビューと状態バッジだけ。操作バーも出さない。

どちらのモードも Menubar の表示設定ダイアログ、レイアウトも同メニューの
ラジオボタンから選ぶ。ここは候補と分岐だけを持ち、Tk の変数もウィジェットも
触らない（ヘッドレスで検証できるように）。
"""

from __future__ import annotations

import re
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

# ウィンドウレイアウト。複数台を 1 画面に並べることを前提に、
# 「台数に合わせて窓を小さくする」選択肢を用意する。
# 既定は standard（従来と同じ画面構成＝既存の見た目を変えない）。
LAYOUTS: Final[tuple[str, ...]] = ("standard", "compact", "preview")
DEFAULT_LAYOUT: Final[str] = "standard"
# コンパクト時のプレビューの要求サイズ。4 台を横に並べても 1 画面へ入る
# 大きさに抑えている（表示設定の fit より小さい。並べた窓どうしの
# 読み違いを避けるため、全部この値に揃える）。
COMPACT_REQUEST_SIZE: Final[tuple[int, int]] = (320, 180)

# プロファイルごとの色（帯と色チップに使う）。1 番は「なし」で空文字。
# 空文字は「色を出さない」の印で、帯もチップも pack しない。
PROFILE_COLORS: Final[tuple[tuple[str, str], ...]] = (
    ("なし", ""),
    ("赤", "#E53935"),
    ("橙", "#FB8C00"),
    ("黄", "#FDD835"),
    ("緑", "#43A047"),
    ("青", "#1E88E5"),
    ("紫", "#8E24AA"),
    ("水色", "#00ACC1"),
    ("茶", "#6D4C41"),
)

# 受け付ける色の形。これ以外の値を Tk へ渡すと落ちるため、
# 設定ファイルと画面の両方をこの 1 箇所で審査する。
_COLOR_RE: Final[re.Pattern[str]] = re.compile(r"^#[0-9A-Fa-f]{6}$")


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


def normalize_profile_color(text: str) -> str:
    """プロファイルの色を ``#RRGGBB`` へ正規化する。無効なら空文字。

    大文字小文字はどちらでも受け付けるが、返り値は常に大文字。保存のたびに
    値が揺れると「設定ファイルが毎回書き換わる」原因になるため。
    受け付けた形が 1 つだけ（``#RRGGBB``）なので、色名や 3 桁表記は
    受け付けない。Tk は解釈出来ても利用者ごとに違う意味になる。
    """
    value = (text or "").strip()
    if not _COLOR_RE.match(value):
        return ""
    return value.upper()


@dataclass(frozen=True, slots=True)
class LayoutPlan:
    """1 つのレイアウトが「どの部品を出すか」を全部並べた表。

    ui/layout_panel.py はこの表だけを見て grid/grid_remove を切り替える。
    条件を書き並べると、「コンパクトのときタブを消す」が複数箇所に散って
    戻せなくなるため、判断はここ 1 箇所に閉じる。
    """

    show_tabs: bool
    show_log: bool
    show_camera_controls: bool
    show_compact_bar: bool
    enforce_min_window: bool
    show_badge: bool
    # 下端の 1 行（状態・接続先・表示 fps）。標準だけ。コンパクト/プレビューは
    # バーとバッジが同じ情報を出すので二重に出さない。
    show_status_bar: bool
    preview: PreviewLayout


def layout_plan(layout: str, show_mode: str, show_size: str) -> LayoutPlan:
    """レイアウト名から、どの部品を出すか・プレビューをどう置くかを決める。

    不明なレイアウトは standard 扱い。補完（config.complete_missing）を
    抜けてもウィンドウそのものは壊さないため。
    """
    if layout == "compact":
        return LayoutPlan(
            show_tabs=False,
            show_log=False,
            show_camera_controls=False,
            show_compact_bar=True,
            # 小さい窓を許さないと 4 台を並べられないため、固定しない。
            enforce_min_window=False,
            show_badge=True,
            show_status_bar=False,
            preview=PreviewLayout(stretch=True, request_size=COMPACT_REQUEST_SIZE),
        )
    if layout == "preview":
        # compact から操作バーだけを外した形。プレビューとバッジだけが残る。
        return LayoutPlan(
            show_tabs=False,
            show_log=False,
            show_camera_controls=False,
            show_compact_bar=False,
            enforce_min_window=False,
            show_badge=True,
            show_status_bar=False,
            preview=PreviewLayout(stretch=True, request_size=COMPACT_REQUEST_SIZE),
        )
    # standard はプレビューの置き方だけを show_mode / show_size に委ねる
    # （従来どおり、表示設定ダイアログの選び方が効く）。
    return LayoutPlan(
        show_tabs=True,
        show_log=True,
        show_camera_controls=True,
        show_compact_bar=False,
        enforce_min_window=True,
        show_badge=False,
        show_status_bar=True,
        preview=preview_layout(show_mode, show_size),
    )
