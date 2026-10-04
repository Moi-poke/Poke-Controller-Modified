"""設定タブ（シリアル・オーディオ）が狭い幅に収まる契約。実 Tk。

設定タブは 1 行に部品を横並びにしており、シリアルとオーディオは 870px 前後を
要求していた。ログを右に置く標準の配置ではタブ欄がそれより狭く、常に
横スクロールバーが出ていた。項目名｜値の縦並びにして、横幅の要求を抑える。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.ttk as ttk
from collections.abc import Iterator
from typing import Any

import pytest
from ui.audio_panel import AudioPanelMixin
from ui.serial_panel import SerialPanelMixin

# ログを右に置いた窓や 4 つ並べた小さな窓でも横スクロールにならないよう、
# コマンドタブ（一番細い欄）と同じ程度まで詰める。
MAX_TAB_WIDTH = 340


class _Bcon:
    name = "bcon"


class _Host(SerialPanelMixin, AudioPanelMixin):
    """組み立てに要る口だけを持つ偽物。処理の入口は何もしない関数で返す。"""

    def __init__(self, window: tk.Toplevel) -> None:
        self.root = window
        self.tab_serial = ttk.Frame(window)
        self.tab_audio = ttk.Frame(window)
        self.tab_serial.pack(side="left", anchor="nw")
        self.tab_audio.pack(side="left", anchor="nw")
        self.baud_rate_state = "readonly"
        self.transport_name = tk.StringVar(master=window, value="legacy_text")
        self.arbitration_mode = tk.StringVar(master=window, value="off")
        self.serial = type("_Serial", (), {"sender": None})()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None

    def _bcon_transport(self) -> Any:
        sender = self.serial.sender
        return _Bcon() if sender == "bcon" else None


@pytest.fixture
def host(tk_root: tk.Tk) -> Iterator[_Host]:
    window = tk.Toplevel(tk_root)
    try:
        h = _Host(window)
        h._build_serial_frame()
        h._build_audio_frame()
        window.update()
        yield h
    finally:
        window.destroy()


def _limit(widget: tk.Misc) -> float:
    return MAX_TAB_WIDTH * float(widget.winfo_fpixels("1i")) / 96.0


def test_the_serial_tab_fits_a_narrow_tab_area(host: _Host) -> None:
    # Then: シリアル設定は狭いタブ欄でも横スクロールにならない幅に収まる。
    assert host.serial_lf.winfo_reqwidth() <= _limit(host.serial_lf)


def test_the_serial_tab_still_fits_when_the_bcon_rows_appear(host: _Host) -> None:
    # When: switched to bcon, which shows the LED / rumble / wired rows.
    host.serial.sender = "bcon"
    host._refresh_bcon_rows()
    host.root.update()

    # Then: 出てくる行を足しても幅は収まり、隠れていた部品は見えている。
    assert host.serial_lf.winfo_reqwidth() <= _limit(host.serial_lf)
    for widget in host._bcon_only_widgets():
        assert widget.winfo_ismapped(), widget


def test_the_audio_tab_fits_a_narrow_tab_area(host: _Host) -> None:
    # Then: オーディオも同じ幅に収まる。
    assert host.audio_lf.winfo_reqwidth() <= _limit(host.audio_lf)


def test_every_serial_and_audio_control_is_still_shown(host: _Host) -> None:
    # Then: 幅を詰めるために部品を落としていない（全部見えている）。
    for widget in (
        host.com_port_cb,
        host.baud_rate_cb,
        host.transport_cb,
        host.arbitration_cb,
        host.reloadComPort,
        host.disconnectComPort,
        host.cb_show_serial,
        host.audio_input_cb,
        host.audio_output_cb,
        host.audio_volume_scale,
        host.audio_volume_label,
        host.audio_reload_button,
    ):
        assert widget.winfo_ismapped(), widget
