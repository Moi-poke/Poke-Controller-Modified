"""core/display_mode.py（プレビュー表示モードの判断）の検証。

Tk を作らず、候補の解釈と 2 モードの分岐だけを確かめる。実画面の
見た目（実際に伸びるか、中心に来るか）は手動 QA で行う。
"""

from __future__ import annotations

from core.display_mode import (
    DEFAULT_SHOW_MODE,
    DEFAULT_SHOW_SIZE,
    FIT_REQUEST_SIZE,
    SHOW_MODES,
    SHOW_SIZES,
    parse_show_size,
    preview_layout,
)


def test_the_mode_table_offers_exactly_fixed_and_fit() -> None:
    # Given / When / Then: 表示モードは 2 つだけ。表を弄ると設定の補正と
    # 画面の選択肢がずれるので、ここを直接押さえる。
    assert SHOW_MODES == ("fixed", "fit")
    assert DEFAULT_SHOW_MODE == "fixed"


def test_the_size_table_lists_the_four_presets_in_ascending_order() -> None:
    # Given / When / Then: 固定サイズのプリセットは 4 つ。960x540 が今回の追加分。
    assert SHOW_SIZES == ("640x360", "960x540", "1280x720", "1920x1080")
    assert DEFAULT_SHOW_SIZE == "640x360"
    assert DEFAULT_SHOW_SIZE in SHOW_SIZES


def test_parse_show_size_reads_every_preset_as_width_then_height() -> None:
    # Given / When: 候補表の各値
    # Then: 「幅x高さ」を (幅, 高さ) の順で返す（入れ替えない）。
    assert parse_show_size("640x360") == (640, 360)
    assert parse_show_size("960x540") == (960, 540)
    assert parse_show_size("1280x720") == (1280, 720)
    assert parse_show_size("1920x1080") == (1920, 1080)


def test_parse_show_size_falls_back_to_the_default_for_an_unknown_value() -> None:
    # Given: 候補に無い値（settings.ini を書き間違えた状態、または空欄）
    # When / Then: 既定の 640x360 として解釈する（起動を止めない）。
    assert parse_show_size("1x1") == (640, 360)
    assert parse_show_size("") == (640, 360)
    assert parse_show_size("640X360") == (640, 360), (
        "大文字の x は候補外。例外ではなく既定として解釈する"
    )


def test_every_preset_round_trips_through_parse_show_size() -> None:
    # Given / When / Then: 表の値が壊れていたらカードの並びと食い違う。
    for text in SHOW_SIZES:
        width, height = parse_show_size(text)
        assert f"{width}x{height}" == text, text


def test_preview_layout_in_fixed_mode_requests_the_chosen_preset() -> None:
    # Given / When: 固定サイズモード × 各プリセット
    for text in SHOW_SIZES:
        layout = preview_layout("fixed", text)

        # Then: 伸びず、要求サイズは選んだプリセットそのもの。
        assert layout.stretch is False, text
        assert layout.request_size == parse_show_size(text), text


def test_preview_layout_in_fit_mode_stretches_the_preview_to_the_frame() -> None:
    # Given: ウィンドウに合わせるモード（固定サイズは何を選んでも無関係）
    # When / Then: 伸びる要求を出し、要求サイズは最小の 16:9 になる。
    layout = preview_layout("fit", "1280x720")
    assert layout.stretch is True
    assert layout.request_size == FIT_REQUEST_SIZE
    assert FIT_REQUEST_SIZE == (640, 360)


def test_preview_layout_in_fit_mode_ignores_the_fixed_size() -> None:
    # Given / When: fit に違う固定サイズを渡す
    # Then: 結果は変わらない（固定サイズ側は選ばれていない）。
    assert preview_layout("fit", "typo") == preview_layout("fit", "1920x1080")


def test_preview_layout_treats_an_unknown_mode_as_fixed() -> None:
    # Given: 候補に無いモード名（補完が効いていない設定など）
    # When
    layout = preview_layout("typo", "1280x720")

    # Then: 固定サイズ扱い（伸びない要求を出す）。
    assert layout == preview_layout("fixed", "1280x720"), layout
    assert layout.stretch is False
    assert layout.request_size == (1280, 720)


def test_the_two_modes_really_produce_different_layouts() -> None:
    # Given / When: 同じ固定サイズで 2 モードを比べる。
    fixed = preview_layout("fixed", "1920x1080")
    fit = preview_layout("fit", "1920x1080")

    # Then: 伸びるかどうかが異なる（上のテストが空振りしないことの担保）。
    assert fixed.stretch is not fit.stretch
    assert fixed != fit
