"""Tk の色名から Win32 COLORREF へのバイト順。実際の変換だけを見る。

設計: docs/superpowers/specs/2026-09-26-gdi-preview-renderer-design.md
5節と 13節「持ち越した課題」の1番目。

13節が名指しで残しているのは、この変換に初めてテストが無かったことだ。
tests/test_gdi_surface_contract.py の double は get_pixel で Tk 側の順を
組み立て直してしまうので、double 越しに測ると素通しでも通ってしまう。
自己検査の sentinel 0x0000FF00 もバイト位置が対称な値なので、ここを
通さない。よって RecordingGdiApi ではなく core.gdi_surface の変換関数
そのものを直接見る。

素通し（Tk の 0x00RRGGBB をそのまま pen へ渡す）なら "blue" は
0x000000FF になり、下の往復テストは 8 件すべて落ち、
test_a_passthrough_would_put_red_in_the_blue_slot も落ちる。
"""

from __future__ import annotations

import pytest
from core.gdi_surface import _colorref, _dib_channels
from core.preview_renderer import TK_COLORREF

#: 各色が指す RGB。テスト対象の実装とは独立した期待値なので、素通しは満たせない。
_EXPECTED_RGB: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
}

_ALPHA = 0xFF


def test_the_table_covers_exactly_the_names_it_documents() -> None:
    # Then: 表と期待値が同じ名前をもち、値が重複しない（逆引きが 1:1）。
    assert set(TK_COLORREF) == set(_EXPECTED_RGB)
    assert len(set(TK_COLORREF.values())) == len(TK_COLORREF)


def test_the_two_names_the_recognition_path_passes() -> None:
    # Given: core/CommandVision.py が実際に渡す 2 つ。
    # Then: バイト位置が反転している（Tk 側は blue=0x0000FF）。
    assert TK_COLORREF["blue"] == 0x00FF0000
    assert TK_COLORREF["red"] == 0x000000FF
    assert TK_COLORREF["blue"] != TK_COLORREF["red"]


def test_blue_is_in_the_high_byte_and_red_in_the_low_one() -> None:
    # Then: COLORREF は 0x00BBGGRR。青い成分が高バイト、赤い成分が下バイト。
    colorref = TK_COLORREF["blue"]
    assert (colorref >> 16) & 0xFF == 255
    assert colorref & 0xFF == 0

    # Then: つまり Tk の順（赤が高バイト）とは逆になっている。
    assert (colorref >> 16) & 0xFF != 0x0000FF >> 16


@pytest.mark.parametrize("name", sorted(_EXPECTED_RGB))
def test_the_colour_survives_the_dword_round_trip(name: str) -> None:
    # Given: 名前が指す RGB と、変換結果の COLORREF。
    red, green, blue = _EXPECTED_RGB[name]
    colorref = TK_COLORREF[name]

    # Then: COLORREF の並びが 0x00BBGGRR である。
    assert colorref == (blue << 16) | (green << 8) | red

    # Then: DIB のメモリ順（B, G, R, A）も Tk の順ではなく同じ色を指す。
    assert _dib_channels(colorref) == (blue, green, red, _ALPHA)

    # Then: Tk 内部形 0x00RRGGBB を数式で変換しても同じ COLORREF になる。
    # 表（記述的）と数式（gdi_surface 側）が食い違ってもここで落ちる。
    assert _colorref((red << 16) | (green << 8) | blue) == colorref


def test_a_passthrough_would_put_red_in_the_blue_slot() -> None:
    # Given: 素通し。Tk の 0x00RRGGBB をそのまま COLORREF として使う。
    passthrough_blue = 0x000000FF

    # Then: 値は表と違う。
    assert passthrough_blue != TK_COLORREF["blue"]

    # Then: しかも DIB の青 channel に赤が落ちる。この 2 バイトがそのまま
    # GDI へ届くので、認識枠が赤で塗られる。
    channels = _dib_channels(passthrough_blue)
    assert (channels[0], channels[2]) == (0, 255)
