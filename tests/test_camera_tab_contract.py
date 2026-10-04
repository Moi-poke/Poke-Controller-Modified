"""カメラの設定をタブへ移し、プレビューの上は頻繁に使う操作だけにする契約。実 Tk。

プレビューの上の 1 行にカメラ選択・再読み込み・表示フィルタ・キャプチャを
並べていたため、窓が狭いとカメラ名が潰れて読めなかった（'0: l'）。
使う頻度で分ける:
  - カメラタブ: カメラの選択と再読み込み（Camera ID）、表示フィルタと調整
  - プレビューの上: プレビュー表示の入/切、キャプチャ、保存先
カメラを開けないときは、プレビューの上に知らせとカメラタブへの近道を出す。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator
from typing import Any

import pytest
from ui.camera_panel import CameraPanelMixin


class _Host(CameraPanelMixin):
    """組み立てに要る口だけを持つ偽物。処理の入口は何もしない関数で返す。"""

    def __init__(self, window: tk.Toplevel) -> None:
        self.root = window
        self.frame_1 = ttk.Frame(window)
        self.frame_1.pack(fill="both", expand=True)
        self.setting_nb = ttk.Notebook(self.frame_1)
        self.tab_serial = ttk.Frame(self.setting_nb)
        self.tab_controller = ttk.Frame(self.setting_nb)
        self.tab_audio = ttk.Frame(self.setting_nb)
        self.tab_command = ttk.Frame(self.setting_nb)
        for tab, text in (
            (self.tab_serial, "シリアル"),
            (self.tab_controller, "コントローラ"),
            (self.tab_audio, "オーディオ"),
            (self.tab_command, "コマンド"),
        ):
            self.setting_nb.add(tab, text=text)
        self.status_camera = tk.StringVar(master=window, value="")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


@pytest.fixture
def host(tk_root: tk.Tk) -> Iterator[_Host]:
    window = tk.Toplevel(tk_root)
    try:
        h = _Host(window)
        h._build_camera_frame()
        h._build_camera_tab()
        h.camera_lf.pack(fill="both", expand=True)
        h.setting_nb.pack(fill="both", expand=True)
        window.update()
        yield h
    finally:
        window.destroy()


def _descendants(widget: tk.Misc) -> list[tk.Misc]:
    out: list[tk.Misc] = []
    for child in widget.winfo_children():
        out.append(child)
        out.extend(_descendants(child))
    return out


def test_the_preview_toolbar_keeps_only_the_frequent_actions(host: _Host) -> None:
    above_preview = _descendants(host.camera_lf)

    # Then: プレビューの上はプレビュー表示・キャプチャ・保存先だけ。
    for widget in (host.cb_show_realtime, host.captureButton, host.OpencaptureButton):
        assert widget in above_preview, widget
        assert widget.winfo_ismapped(), widget
    # カメラ選択と表示フィルタはプレビューの上に無い。
    for widget in (
        host.Camera_Name,
        host.reloadButton,
        host.filt_check,
        host.filt_setting_button,
    ):
        assert widget not in above_preview, widget


def test_the_camera_tab_sits_between_controller_and_audio(host: _Host) -> None:
    # Then: 取り込み機器の設定（カメラ・オーディオ）を隣に並べる。
    names = [host.setting_nb.tab(t, "text") for t in host.setting_nb.tabs()]
    assert names == ["シリアル", "コントローラ", "カメラ", "オーディオ", "コマンド"]


def test_the_camera_tab_holds_camera_choice_and_the_display_filter(
    host: _Host,
) -> None:
    host.setting_nb.select(host.tab_camera)
    host.root.update()
    in_tab = _descendants(host.tab_camera)

    # Then: カメラの選択・再読み込み・表示フィルタ・調整がタブにあり、見えている。
    for widget in (
        host.Camera_Name,
        host.reloadButton,
        host.filt_check,
        host.filt_setting_button,
    ):
        assert widget in in_tab, widget
        assert widget.winfo_ismapped(), widget


def test_a_camera_that_cannot_open_shows_a_notice_with_a_shortcut(
    host: _Host,
) -> None:
    host.setting_nb.select(host.tab_serial)

    # When: the camera fails to open.
    host._show_camera_state(False, 3)
    host.root.update()

    # Then: プレビューの上に知らせが出て、状態表示にも出る。
    assert host.camera_notice.winfo_ismapped()
    assert "3" in str(host.camera_notice_label.cget("text"))
    assert "開けません" in host.status_camera.get()

    # When: the shortcut is pressed.
    host.camera_notice_button.invoke()
    host.root.update()

    # Then: カメラタブが開く（選び直す場所へすぐ行ける）。
    assert host.setting_nb.select() == str(host.tab_camera)


def test_an_opened_camera_hides_the_notice_and_shows_its_name(host: _Host) -> None:
    host._show_camera_state(False, 3)
    host.Camera_Name.set("0: Live Gamer EXTREME 3 [fbad85]")

    # When: the camera opens.
    host._show_camera_state(True, 0)
    host.root.update()

    # Then: 知らせは消え、状態表示に今のカメラ名が出る（識別子は省く）。
    assert not host.camera_notice.winfo_ismapped()
    assert host.status_camera.get() == "カメラ: 0: Live Gamer EXTREME 3"
