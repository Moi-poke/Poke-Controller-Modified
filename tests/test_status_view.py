"""core/status_view.py（1 行の状態表示）の検証。

Tk を作らず、実行状態・接続状態・プロファイル名の合成だけを確認する。
バッジの色（Win32 COLORREF への変換）もここで押さえる。実画面の見た目は
手動 QA で行う（この層は文字列と整数だけを返すため）。
"""

from __future__ import annotations

from typing import Any

from core.display_mode import PROFILE_COLORS, normalize_profile_color
from core.status_view import BADGE_COLORS, hex_to_colorref, status_view

# 実行中のコマンド名。既定の「止まっている」とは別の値であることが分かるもの。
RUNNING_NAME = "自動テスト"


def _view(**overrides: Any) -> Any:
    """既定の「止まった状態」からの差し替え。停止中・接続済み・無名。"""
    base: dict[str, Any] = {
        "profile": "",
        "running_command": "",
        "paused": False,
        "stop_waited_ms": 0,
        "device": "COM3",
        "opened": True,
    }
    base.update(overrides)
    return status_view(**base)


def test_a_stopped_and_connected_app_reads_as_idle() -> None:
    # Given / When: 実行中のコマンドが無く、接続も済んでいる
    view = _view()

    # Then: 停止中として見せる（未接続と区別できる）。
    assert view.kind == "idle"
    assert view.text == "■ 停止中"


def test_a_disconnected_app_never_reads_as_idle() -> None:
    # Given / When: ポート名はあるが閉まっている、またはポート名そのものがない。
    closed = _view(opened=False)
    unknown = _view(device="")

    # Then: 両方とも未接続。停止中は「繋がっている前提」で読ませない。
    assert closed.kind == "disconnected"
    assert closed.text == "⚠ 未接続"
    assert unknown.kind == "disconnected"
    assert unknown.text == "⚠ 未接続"


def test_a_running_command_reads_as_running_with_a_play_mark() -> None:
    # Given / When: 実行中のコマンドがあり、一時停止ではない
    view = _view(running_command=RUNNING_NAME)

    # Then: ▶ 付きで実行中。
    assert view.kind == "running"
    assert view.text == f"▶ {RUNNING_NAME}"


def test_a_paused_command_reads_as_paused_with_a_pause_mark() -> None:
    # Given / When: 実行中だが一時停止している
    view = _view(running_command=RUNNING_NAME, paused=True)

    # Then: ⏸ 付きで一時停止。
    assert view.kind == "paused"
    assert view.text == f"⏸ {RUNNING_NAME}"


def test_stop_waiting_outranks_running_and_disconnected() -> None:
    # Given: 停止を待っている最中は、実行中のコマンド名と未接続が同時に残りがち。
    # When / Then: 待機を最優先にする（待つ以外の手が無いので、これが説明になる）。
    view = _view(running_command=RUNNING_NAME, opened=False, stop_waited_ms=2500)

    assert view.kind == "stopping"
    assert view.text == "⏳停止待ち 2秒"


def test_a_running_command_outranks_the_disconnected_state() -> None:
    # Given / When: 実行中だが切断済み（待ち合わせ中のような状態）
    view = _view(running_command=RUNNING_NAME, opened=False)

    # Then: 実行中を示す（切れただけでは未接続に隠さない）。
    assert view.kind == "running"
    assert view.text == f"▶ {RUNNING_NAME}"


def test_a_profile_name_is_prefixed_to_every_state() -> None:
    # Given / When: プロファイル名が空でない（複数台を並べている）
    cases = (
        ("idle", "", False, 0, "COM3", True, "[switch1] ■ 停止中"),
        ("running", "名前", False, 0, "COM3", True, "[switch1] ▶ 名前"),
        ("paused", "名前", True, 0, "COM3", True, "[switch1] ⏸ 名前"),
        ("disconnected", "", False, 0, "", False, "[switch1] ⚠ 未接続"),
        ("stopping", "", False, 1000, "COM3", True, "[switch1] ⏳停止待ち 1秒"),
    )
    for kind, running, paused, waited, device, opened, expected in cases:
        view = _view(
            profile="switch1",
            running_command=running,
            paused=paused,
            stop_waited_ms=waited,
            device=device,
            opened=opened,
        )

        # Then: どの状態でも先頭に [名前] が付く（並べた台を見分けられる）。
        assert view.kind == kind
        assert view.text == expected, view.text


def test_a_long_command_name_is_truncated_with_an_ellipsis() -> None:
    # Given: 上限を超えるコマンド名（Window.TITLE_COMMAND_MAX と同じ規則）
    long_name = "あ" * 21

    # When / Then: 19 文字 + 「…」で 20 文字に切る（前半を丸めない）。
    view = _view(running_command=long_name)
    assert view.text == "▶ " + "あ" * 19 + "…"
    assert len(view.text) == 2 + 20


def test_a_name_at_the_limit_is_kept_whole() -> None:
    # Given / When: ちょうど上限のコマンド名
    exact = "あ" * 20

    # Then: 切らない（不要な省略記号を出さない）。
    assert _view(running_command=exact).text == f"▶ {exact}"


def test_the_badge_colour_table_covers_every_kind() -> None:
    # Given / When / Then: 状態ごとに色が決まっている。無いと描けない。
    assert BADGE_COLORS["running"] == 0x00307A2E
    assert BADGE_COLORS["paused"] == 0x000080C0
    assert BADGE_COLORS["stopping"] == 0x000080C0
    assert BADGE_COLORS["disconnected"] == 0x002020B0
    assert BADGE_COLORS["idle"] == 0x00505050


def test_every_kind_status_view_can_produce_has_a_badge_colour() -> None:
    # Given / When / Then: status_view が返す kind が全部バッジの色を持つ。
    # 新しい kind を足して色を取りこぼさないことの担保。
    for view in (
        _view(),
        _view(running_command=RUNNING_NAME),
        _view(running_command=RUNNING_NAME, paused=True),
        _view(stop_waited_ms=1000),
        _view(opened=False),
    ):
        assert view.kind in BADGE_COLORS, view.kind


def test_hex_to_colorref_swaps_red_and_blue_for_win32() -> None:
    # Given / When: #RRGGBB を Win32 の COLORREF (0x00BBGGRR) へ
    # Then: 赤と青が入れ替わる（Tk の並び順のまま渡すと色が逆になる）。
    assert hex_to_colorref("#E53935") == 0x003539E5
    assert hex_to_colorref("#000000") == 0x00000000
    assert hex_to_colorref("#FFFFFF") == 0x00FFFFFF


def test_hex_to_colorref_accepts_lowercase_and_returns_zero_for_junk() -> None:
    # Given / When / Then: 小文字は同じ数値。不正値は 0（描かない）。
    assert hex_to_colorref("#e53935") == 0x003539E5
    assert hex_to_colorref("") == 0
    assert hex_to_colorref("#FFF") == 0
    assert hex_to_colorref("E53935") == 0
    assert hex_to_colorref("#GGGGGG") == 0


def test_every_profile_colour_round_trips_into_a_usable_colourref() -> None:
    # Given / When: 設定で選べるプロファイルの色
    # Then: どれを選んでも 0 にならない（帯や色チップが透明になる穴を作らない）。
    for name, value in PROFILE_COLORS:
        normalized = normalize_profile_color(value)
        assert normalized == value, name
        if normalized:
            assert hex_to_colorref(normalized) != 0, name
