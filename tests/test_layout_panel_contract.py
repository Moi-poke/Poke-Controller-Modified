"""Contracts for ``ui/layout_panel.py`` against a real Tk tree shaped like Window.

The mixin decides which parts of the window are shown; the geometry managers
decide where the space goes. Those two halves only meet inside a live Tk tree,
so a test of ``layout_plan`` alone cannot see a hidden row that still claims
extra height, or a re-packed widget that silently moves to the end of its bar.
Both defects were found on the desktop (2026-10-03 layout probe) and are pinned
here with a real ``tk.Tk`` root.

The host builds the same grid that ``Window._build_ui`` and the panels build
(rows, spans, weights), and borrows the real ``CameraPanelMixin`` preview
layout method, so the stretch weights under test are the shipped ones.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator
from typing import Any

import pytest
from ui.camera_panel import CameraPanelMixin
from ui.layout_panel import LayoutPanelMixin
from ui.scroll_host import ScrollHost

_PROFILE = "switch1"
_COLOR = "#1E88E5"


class _PreviewDouble(tk.Frame):
    """CaptureArea の代わり。要求サイズとバッジだけを受け取る。"""

    def __init__(self, master: Any) -> None:
        super().__init__(master, background="black")
        self.badge_text = ""

    def setShowsize(self, height: int, width: int) -> None:
        self.config(width=width, height=height)

    def setBadge(self, text: str, background: int) -> None:
        self.badge_text = text

    def clearBadge(self) -> None:
        self.badge_text = ""


class _Serial:
    opened = True

    def is_open(self) -> bool:
        return self.opened


class _Runner:
    stop_waited = 0


class _Host(LayoutPanelMixin):
    """Window と同じ行・列・重みで部品を並べた最小の宿主。"""

    _current_preview_layout = CameraPanelMixin._current_preview_layout
    _apply_preview_layout = CameraPanelMixin._apply_preview_layout

    def __init__(self, root: tk.Toplevel) -> None:
        self.root = root
        self.profile = _PROFILE
        self.serial = _Serial()
        self.runner = _Runner()
        self._running_command = ""
        self._paused = False
        self.layout_mode = tk.StringVar(master=root, value="standard")
        self.profile_color = tk.StringVar(master=root, value="")
        self.show_mode = tk.StringVar(master=root, value="fit")
        self.show_size = tk.StringVar(master=root, value="640x360")
        self.com_port_name = tk.StringVar(master=root, value="COM9")
        self.transport_name = tk.StringVar(master=root, value="switch-bcon")

        # Window と同じく、本体はスクロール入れ物の中身として作る。
        self.scroll_host = ScrollHost(root)
        self.frame_1 = self.scroll_host.inner
        # Window._build_ui と同じ: カメラ行 0、タブ欄が行 1〜3、ログが列 3。
        self.camera_lf = ttk.Labelframe(self.frame_1, text="Camera")
        ttk.Label(self.camera_lf, text="Camera ID").grid(padx="5", sticky="ew")
        self.preview = _PreviewDouble(self.camera_lf)
        self.preview.grid(column=0, columnspan=7, row=2, padx="5", pady="5")
        self.camera_lf.grid(columnspan=3, padx="5", sticky="ew")
        self.setting_nb = ttk.Frame(self.frame_1, height=300)
        self.setting_nb.grid(
            column=0, columnspan=3, padx="5", row=1, rowspan=3, sticky="nsew"
        )
        self.log_nb = ttk.Frame(self.frame_1, width=300)
        self.log_nb.grid(column=3, padx="5", pady="5", row=0, rowspan=3, sticky="nsew")
        self.log_bar = ttk.Frame(self.frame_1, height=20)
        self.log_bar.grid(column=3, padx="5", row=3, sticky="ew")
        self.startButton = ttk.Button(self.frame_1, text="Start")
        self.pauseButton = ttk.Button(self.frame_1, text="Pause")
        self._build_layout_widgets()

        self.scroll_host.pack(expand=True, fill="both", side="top")
        self.frame_1.columnconfigure(3, weight=1)
        self.frame_1.rowconfigure(1, weight=1)
        self.frame_1.rowconfigure(2, weight=1)
        # Window も組み立て直後に 1 回適用する（起動時のレイアウト）。
        self._apply_layout()

    def openCommandPalette(self) -> None:
        pass

    def _on_setting_changed(self) -> None:
        pass

    def _apply_content_minsize(self) -> None:
        pass


@pytest.fixture
def host(tk_root: tk.Tk) -> Iterator[_Host]:
    # 窓はテストごとに新しい Toplevel。Window の root と同じく pack の親になる。
    window = tk.Toplevel(tk_root)
    window.geometry("960x600+0+0")
    try:
        yield _Host(window)
    finally:
        window.destroy()


def _settle(host: _Host) -> None:
    host.root.update_idletasks()
    host.root.update()


def test_compact_gives_the_whole_window_height_to_the_camera_frame(
    host: _Host,
) -> None:
    # Given: a 960x600 window in the standard layout.
    _settle(host)

    # When: the compact layout hides the tabs and the log.
    host.applyLayout("compact")
    _settle(host)

    # Then: タブ欄の行が重みを持ったままだと、隠した行が余りの高さを取り、
    #       プレビューの下に空白が残る。カメラ枠が frame_1 の高さをほぼ全部使う。
    frame_h = host.frame_1.winfo_height()
    camera_h = host.camera_lf.winfo_height()
    assert camera_h >= frame_h - 20, f"camera {camera_h}px of frame {frame_h}px"


def test_returning_to_standard_gives_the_tab_rows_their_height_back(
    host: _Host,
) -> None:
    # Given: a window tall enough to have spare height to share out (below the
    #        content request grid only shrinks, and the weights barely show),
    #        the standard layout's tab height, then the compact layout.
    host.root.geometry("960x1100+0+0")
    _settle(host)
    standard_tabs_h = host.setting_nb.winfo_height()
    host.applyLayout("compact")
    _settle(host)

    # When: the standard layout is applied again.
    host.applyLayout("standard")
    _settle(host)

    # Then: タブ欄が切替前と同じ高さに戻る（行の重みを戻し忘れると、
    #       カメラ行が余りを独占してタブ欄が縮む）。
    assert host.setting_nb.winfo_ismapped()
    assert host.setting_nb.winfo_height() == standard_tabs_h


def test_the_colour_chip_stays_left_of_the_profile_name_and_is_visible(
    host: _Host,
) -> None:
    # Given: the compact layout.
    host.applyLayout("compact")

    # When: a profile colour is applied after the bar was built.
    host.applyProfileColor(_COLOR)
    _settle(host)

    # Then: 色チップを pack し直すと左詰めの最後へ回り、状態文の後ろに
    #       細い線として出る。名前の左にあり、名前と同じくらいの高さを持つ。
    chip = host.compact_chip
    label = host.compact_profile_label
    assert chip.winfo_x() < label.winfo_x()
    assert chip.winfo_height() >= label.winfo_height() - 4


def test_the_compact_bar_names_the_profile_once(host: _Host) -> None:
    # Given: the compact layout with a named profile.
    host.applyLayout("compact")
    _settle(host)

    # Then: 名前はラベルが出している。状態文にも [名前] を付けると二重になる。
    #       プレビューのバッジは単独で見えるので、名前を付けたままにする。
    assert str(host.compact_profile_label.cget("text")) == _PROFILE
    assert f"[{_PROFILE}]" not in host.compact_status.get()
    assert host.preview.badge_text.startswith(f"[{_PROFILE}] ")


def test_an_unnamed_profile_still_gets_its_colour_chip_first(host: _Host) -> None:
    # Given: the compact layout for the unnamed (default) profile, so the
    #        profile name label is unpacked.
    host.profile = ""
    host.applyLayout("compact")
    _settle(host)

    # When: a profile colour is applied.
    host.applyProfileColor(_COLOR)
    _settle(host)

    # Then: 隠れた名前ラベルを基準に pack すると TclError になる。色チップは
    #       状態文の左に出る。
    assert host.compact_chip.winfo_ismapped()
    assert host.compact_chip.winfo_x() < host._compact_status_label.winfo_x()


def test_the_standard_layout_shows_a_status_bar_with_state_device_and_fps(
    host: _Host,
) -> None:
    # Given: the standard layout (applied at start-up).
    _settle(host)

    # When: the video stats arrive.
    host.publishVideoStats(44.9)
    _settle(host)

    # Then: 下端に 1 行。状態（名前はタイトルにあるので付けない）・接続先・
    #       表示 fps を出す。
    assert host.status_bar.winfo_ismapped()
    assert host.status_state.get() == "■ 停止中"
    assert host.status_device.get() == "COM9 (switch-bcon)"
    assert host.status_fps.get() == "表示 44.9 fps"


def test_the_status_bar_stays_visible_when_the_window_is_too_small(
    host: _Host,
) -> None:
    # When: the window is far smaller than the content.
    host.root.geometry("300x200+0+0")
    _settle(host)

    # Then: 本体より先に詰められて消えない（スクロールで届かない場所に置かない）。
    #       押し出された部品は unmap されても前回の高さを返すので、表示中かも見る。
    bar_bottom = host.status_bar.winfo_y() + host.status_bar.winfo_height()
    assert host.status_bar.winfo_ismapped()
    assert host.status_bar.winfo_height() > 0
    assert bar_bottom <= host.root.winfo_height()


def test_compact_and_preview_layouts_hide_the_status_bar(host: _Host) -> None:
    for layout in ("compact", "preview"):
        # When: a multi-window layout is applied.
        host.applyLayout(layout)
        _settle(host)

        # Then: バーかバッジが同じ情報を出すので、二重に出さない。
        assert not host.status_bar.winfo_ismapped(), layout

    # When: back to standard.
    host.applyLayout("standard")
    _settle(host)

    # Then: 戻る。
    assert host.status_bar.winfo_ismapped()


def test_a_closed_port_reads_as_not_connected(host: _Host) -> None:
    # When: the port is closed.
    host.serial.opened = False
    host._publish_status()

    # Then: ポート名を出したままにしない（繋がっていると誤解する）。
    assert host.status_device.get() == "未接続"
