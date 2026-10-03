"""core/display_mode.py（プレビュー表示モードの判断）の検証。

Tk を作らず、候補の解釈と 2 モードの分岐だけを確かめる。実画面の
見た目（実際に伸びるか、中心に来るか）は手動 QA で行う。
"""

from __future__ import annotations

from core.display_mode import (
    COMPACT_REQUEST_SIZE,
    DEFAULT_LAYOUT,
    DEFAULT_SHOW_MODE,
    DEFAULT_SHOW_SIZE,
    FIT_REQUEST_SIZE,
    LAYOUTS,
    PROFILE_COLORS,
    SHOW_MODES,
    SHOW_SIZES,
    layout_plan,
    normalize_profile_color,
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


# ===========================================================================
# ウィンドウレイアウト（複数台並列起動向け）
# ===========================================================================


def test_the_layout_table_offers_standard_compact_and_preview() -> None:
    # Given / When / Then: レイアウトは 3 つだけ。既定は従来と同じ standard。
    assert LAYOUTS == ("standard", "compact", "preview")
    assert DEFAULT_LAYOUT == "standard"
    assert DEFAULT_LAYOUT in LAYOUTS


def test_the_standard_layout_plan_keeps_every_pane_visible() -> None:
    # Given / When: 標準レイアウト（従来と同じ画面構成）
    plan = layout_plan("standard", "fixed", "1280x720")

    # Then: タブ・ログ・カメラ欄の操作部をすべて出し、バーもバッジも出さない。
    assert plan.show_tabs is True
    assert plan.show_log is True
    assert plan.show_camera_controls is True
    assert plan.show_compact_bar is False
    assert plan.enforce_min_window is True
    assert plan.show_badge is False
    # 状態・接続先・fps の 1 行は標準だけ（他はバーとバッジが同じ情報を出す）。
    assert plan.show_status_bar is True


def test_the_standard_layout_plan_delegates_the_preview_to_the_show_mode() -> None:
    # Given / When / Then: 標準レイアウトのプレビューは show_mode / show_size が正。
    assert layout_plan("standard", "fixed", "960x540").preview == preview_layout(
        "fixed", "960x540"
    )
    assert layout_plan("standard", "fit", "typo").preview == preview_layout(
        "fit", "typo"
    )


def test_the_compact_layout_plan_hides_the_side_panes_and_shows_the_bar() -> None:
    # Given / When: コンパクトレイアウト（1 画面に複数台を並べる）
    plan = layout_plan("compact", "fixed", "1920x1080")

    # Then: タブ・ログ・操作部を隠し、コンパクトバーとバッジを出す。
    # 最小サイズの固定もしない（小さい窓を許すため）。
    assert plan.show_tabs is False
    assert plan.show_log is False
    assert plan.show_camera_controls is False
    assert plan.show_compact_bar is True
    assert plan.enforce_min_window is False
    assert plan.show_badge is True
    assert plan.show_status_bar is False


def test_the_preview_layout_plan_drops_the_compact_bar() -> None:
    # Given / When: プレビューのみ
    plan = layout_plan("preview", "fixed", "640x360")

    # Then: コンパクトと同じく隠すが、バー（操作）は出さない。
    assert plan.show_tabs is False
    assert plan.show_log is False
    assert plan.show_camera_controls is False
    assert plan.show_compact_bar is False
    assert plan.show_badge is True
    assert plan.enforce_min_window is False
    assert plan.show_status_bar is False


def test_the_compact_layout_plan_ignores_the_chosen_show_mode() -> None:
    # Given / When: コンパクト/プレビューは show_mode / show_size に依らず同じ要求。
    compact = layout_plan("compact", "fit", "1920x1080")
    preview = layout_plan("preview", "fixed", "640x360")

    # Then: コンパクト用の要求（16:9）で伸ばす。表示設定ダイアログの選び方に
    # 左右されない（並べた台はすべて同じ大きさで決まる）。
    assert compact.preview.stretch is True
    assert compact.preview.request_size == COMPACT_REQUEST_SIZE
    assert COMPACT_REQUEST_SIZE == (320, 180)
    assert compact.preview == preview.preview


def test_layout_plan_treats_an_unknown_layout_as_standard() -> None:
    # Given: 候補に無いレイアウト名（壊れた settings.ini、または空欄）
    # When / Then: 標準へ落とす（起動を止めない）。
    assert layout_plan("typo", "fixed", "640x360") == layout_plan(
        "standard", "fixed", "640x360"
    )
    assert layout_plan("", "fit", "640x360") == layout_plan(
        "standard", "fit", "640x360"
    )


def test_the_three_layouts_really_produce_different_plans() -> None:
    # Given / When: 同じ表示設定で 3 レイアウトを比べる。
    standard = layout_plan("standard", "fixed", "640x360")
    compact = layout_plan("compact", "fixed", "640x360")
    preview = layout_plan("preview", "fixed", "640x360")

    # Then: どれか1つだけ違う版が存在する（上のテストが空振りしないことの担保）。
    assert standard != compact
    assert compact != preview
    assert standard != preview


# ===========================================================================
# プロファイルの色
# ===========================================================================


def test_the_profile_color_table_lists_nine_colors_starting_with_none() -> None:
    # Given / When / Then: 色は「なし」+ 8 色。1番目の名前が「なし」であること。
    assert PROFILE_COLORS[0] == ("なし", "")
    assert len(PROFILE_COLORS) == 9
    # 表の値は必ず正規化を通せる（正規化できない値が入ると帯が出ない）。
    for name, value in PROFILE_COLORS:
        assert value == normalize_profile_color(value), name


def test_normalize_profile_color_keeps_an_empty_value_empty() -> None:
    # Given / When / Then: 「なし」は空文字のまま（帯も色チップも出さない）。
    assert normalize_profile_color("") == ""
    assert normalize_profile_color("なし") == ""


def test_normalize_profile_color_upcases_a_lowercase_hex_value() -> None:
    # Given / When: 小文字で書かれた #rrggbb（設定ファイルを手で編集した状態）
    # Then: 大文字へ正規化する（保存のたびに値が揺れない）。
    assert normalize_profile_color("#e53935") == "#E53935"
    assert normalize_profile_color("#1e88e5") == "#1E88E5"


def test_normalize_profile_color_drops_a_malformed_value() -> None:
    # Given: 壊れた値（桁数不足・# 無し・色名だけ）
    # When / Then: 空文字へ落とす（Tk へ渡すと落ちるため）。
    assert normalize_profile_color("#FFF") == "", "3 桁は不正"
    assert normalize_profile_color("E53935") == "", "# 無しは不正"
    assert normalize_profile_color("red") == "", "色名は受け付けない"
    assert normalize_profile_color("#GGGGGG") == "", "16 進でない"
    assert normalize_profile_color("#E539351") == "", "桁が合わない"


def test_normalize_profile_color_accepts_every_value_in_the_table() -> None:
    # Given / When / Then: 表の値が全てそのまま通る（帯が出ない穴を作らない）。
    for name, value in PROFILE_COLORS:
        assert normalize_profile_color(value) == value, name
