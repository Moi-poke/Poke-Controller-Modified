"""Contracts for the main menu bar, walked through a real Tk menu tree.

メニューは「見出し → 項目 → 呼ばれる処理」の 3 点が揃って初めて使える。
源の文字列検査では、項目が別の見出しへ紛れ込んだり、ショートカットと違う
入口を呼んだりしても気付けないので、実際に組み立てた tk.Menu を歩いて確かめる。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator
from typing import Any

import pytest
from Menubar import PokeController_Menubar

_TOP = ["ファイル(F)", "コマンド(C)", "表示(V)", "接続(N)", "ツール(T)", "ヘルプ(H)"]


class _App:
    """Menubar が触る app の口だけを持つ偽物。呼ばれた名前を記録する。"""

    def __init__(self, root: tk.Toplevel) -> None:
        self.root = root
        self.calls: list[str] = []
        self.layout_mode = tk.StringVar(master=root, value="standard")
        self.profile_color = tk.StringVar(master=root, value="")
        self.app_version = "9.9.9"
        self.arrangement = tk.StringVar(master=root, value="log_right")
        self.show_tabs_pane = tk.BooleanVar(master=root, value=True)
        self.show_log_pane = tk.BooleanVar(master=root, value=True)
        self.args: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:
        # 入口の名前だけ記録する偽メソッド。存在しない属性で落とさない。
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args: Any, **_kwargs: Any) -> None:
            self.calls.append(name)
            self.args.append((name, args))

        return record


@pytest.fixture
def bar(tk_root: tk.Tk) -> Iterator[tuple[PokeController_Menubar, _App]]:
    window = tk.Toplevel(tk_root)
    app = _App(window)
    menubar = PokeController_Menubar(app)
    try:
        yield menubar, app
    finally:
        window.destroy()


def _entries(menu: tk.Menu) -> list[tuple[str, str]]:
    """(種類, ラベル) の並び。区切り線はラベル無しで残す。"""
    last = menu.index("end")
    if last is None:
        return []
    out = []
    for i in range(int(last) + 1):
        kind = str(menu.type(i))
        # 区切り線と切り取り線（tearoff）はラベルを持たない。
        no_label = kind in ("separator", "tearoff")
        label = "" if no_label else str(menu.entrycget(i, "label"))
        out.append((kind, label))
    return out


def _submenu(bar: tk.Menu, label: str) -> tk.Menu:
    for kind, text in _entries(bar):
        if kind == "cascade" and text == label:
            return bar.nametowidget(bar.entrycget(label, "menu"))
    raise AssertionError(f"見出し {label} が無い: {_entries(bar)}")


def _index(menu: tk.Menu, label: str) -> int:
    for i, (_kind, text) in enumerate(_entries(menu)):
        if text == label:
            return i
    raise AssertionError(f"項目 {label} が無い: {_entries(menu)}")


def test_the_menu_bar_uses_the_standard_desktop_headings_in_order(
    bar: tuple[PokeController_Menubar, _App],
) -> None:
    # Then: 「メニュー」1 つに全部を入れず、一般的な見出しを並べる。
    #       (F) などは Alt キーでの操作用（OBS など国内向け訳と同じ表記）。
    menubar, _app = bar
    assert [t for k, t in _entries(menubar) if k == "cascade"] == _TOP


@pytest.mark.parametrize(
    ("heading", "label", "entry"),
    [
        ("コマンド(C)", "開始", "StartCommandWithF6"),
        ("コマンド(C)", "停止", "StopCommandWithEsc"),
        ("コマンド(C)", "一時停止 / 再開", "PauseCommandWithF7"),
        ("コマンド(C)", "再読み込み", "ReloadCommandWithF5"),
        ("コマンド(C)", "コマンドを選択...", "openCommandPalette"),
        ("コマンド(C)", "コマンドフォルダを開く", "OpenCommandDir"),
        ("ファイル(F)", "キャプチャを保存", "saveCapture"),
        ("ファイル(F)", "キャプチャフォルダを開く", "OpenCaptureDir"),
        ("ファイル(F)", "終了", "exit"),
        ("接続(N)", "シリアルポートを再接続", "reloadSerialPort"),
        ("接続(N)", "シリアルポートを切断", "inactivateSerial"),
        ("接続(N)", "カメラを再読み込み", "openCamera"),
        ("接続(N)", "音声デバイスを再読み込み", "reloadAudio"),
    ],
)
def test_each_menu_item_calls_the_same_entry_as_its_button_or_shortcut(
    bar: tuple[PokeController_Menubar, _App], heading: str, label: str, entry: str
) -> None:
    # Given: the built menu bar.
    menubar, app = bar
    menu = _submenu(menubar, heading)

    # When: the item is invoked.
    menu.invoke(_index(menu, label))

    # Then: ボタンやショートカットと同じ入口を呼ぶ（入口が分かれると、
    #       片方だけ直したときに挙動が食い違う）。
    assert app.calls == [entry]


@pytest.mark.parametrize(
    ("heading", "label", "accelerator"),
    [
        ("コマンド(C)", "開始", "F6"),
        ("コマンド(C)", "停止", "Esc"),
        ("コマンド(C)", "一時停止 / 再開", "F7"),
        ("コマンド(C)", "再読み込み", "F5"),
        ("コマンド(C)", "コマンドを選択...", "Ctrl+K"),
    ],
)
def test_shortcut_items_show_their_key(
    bar: tuple[PokeController_Menubar, _App],
    heading: str,
    label: str,
    accelerator: str,
) -> None:
    # Then: ショートカットはメニューの右に出す（覚えなくても見つかる）。
    menubar, _app = bar
    menu = _submenu(menubar, heading)
    assert menu.entrycget(_index(menu, label), "accelerator") == accelerator


def test_the_aspect_lock_is_one_check_item_that_shows_its_state(
    bar: tuple[PokeController_Menubar, _App],
) -> None:
    # Then: 「固定」「解除」の 2 項目では今どちらか分からない。チェック 1 つにする。
    menubar, _app = bar
    view = _submenu(menubar, "表示(V)")
    labels = [t for _k, t in _entries(view)]
    assert "固定を解除" not in labels
    assert view.type(_index(view, "16:9 に固定")) == "checkbutton"


def test_settings_and_help_items_live_under_tools_and_help(
    bar: tuple[PokeController_Menubar, _App],
) -> None:
    # Then: 設定系はツール、案内系はヘルプ。コマンドの下に混ぜない。
    menubar, _app = bar
    tools = [t for _k, t in _entries(_submenu(menubar, "ツール(T)"))]
    help_ = [t for _k, t in _entries(_submenu(menubar, "ヘルプ(H)"))]
    commands = [t for _k, t in _entries(_submenu(menubar, "コマンド(C)"))]
    for label in ("キーコンフィグ...", "入力ログの書式...", "Discord通知の設定..."):
        assert label in tools
        assert label not in commands
    for label in (
        "ショートカット一覧",
        "ドキュメント",
        "エラー報告をコピー...",
        "バージョン情報",
    ):
        assert label in help_
    # 中身と名前が食い違っていた項目は無くす（表示設定で戻せる）。
    every = [t for k, t in _entries(menubar) if k == "cascade"]
    for heading in every:
        assert "画面サイズのリセット" not in [
            t for _k, t in _entries(_submenu(menubar, heading))
        ]


def test_the_view_menu_picks_the_arrangement_and_collapses_panes(
    bar: tuple[PokeController_Menubar, _App],
) -> None:
    # Given: the view menu.
    menubar, app = bar
    view = _submenu(menubar, "表示(V)")
    labels = [t for _k, t in _entries(view)]

    # Then: 並べ方は 3 択のラジオ（今の値に印が付く）、欄はチェックで出し入れ。
    for label in ("ログを右", "右にタブとログ", "縦一列"):
        assert view.type(_index(view, label)) == "radiobutton"
    for label in ("設定タブ", "ログ"):
        assert view.type(_index(view, label)) == "checkbutton"
    assert labels.index("縦一列") < labels.index("設定タブ")

    # When: an arrangement and a collapse are chosen.
    view.invoke(_index(view, "縦一列"))
    view.invoke(_index(view, "ログ"))

    # Then: アプリ側の入口に、選んだ値がそのまま渡る。
    assert ("applyArrangement", ("stack",)) in app.args
    assert ("setPaneVisible", ("log", False)) in app.args
