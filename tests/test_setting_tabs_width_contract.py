"""設定タブ（シリアル・オーディオ）が狭い幅に収まる契約。実 Tk。

設定タブは 1 行に部品を横並びにしており、シリアルとオーディオは 870px 前後を
要求していた。ログを右に置く標準の配置ではタブ欄がそれより狭く、常に
横スクロールバーが出ていた。項目名｜値の縦並びにして、横幅の要求を抑える。
"""

from __future__ import annotations

import time
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


# -- 候補に合わせたコンボボックスの幅 ----------------------------------------


def _text_px(combo: ttk.Combobox, text: str) -> int:
    from ui.combo_fit import combo_font

    return int(combo_font(combo).measure(text))


def test_a_combobox_is_widened_to_show_its_longest_choice(tk_root: tk.Tk) -> None:
    from ui.combo_fit import fit_combo_width

    window = tk.Toplevel(tk_root)
    try:
        combo = ttk.Combobox(window, width=16)
        combo.pack()
        longest = "COM3: USB Serial Port (COM3)"
        combo["values"] = ["COM1: 通信ポート (COM1)", longest]

        # When: the width is fitted to the choices.
        fit_combo_width(combo, min_chars=10, max_chars=40)
        window.update()

        # Then: 一番長い候補が欄に収まる（矢印の分も残して切れない）。
        assert combo.winfo_reqwidth() >= _text_px(combo, longest) + 16
    finally:
        window.destroy()


def test_a_combobox_never_grows_past_its_cap_or_shrinks_below_its_floor(
    tk_root: tk.Tk,
) -> None:
    from ui.combo_fit import fit_combo_width

    window = tk.Toplevel(tk_root)
    try:
        combo = ttk.Combobox(window)
        combo["values"] = ["x" * 200]
        fit_combo_width(combo, min_chars=10, max_chars=30)
        # Then: 極端に長い機器名でもタブを押し広げない（伸びる列で見せる）。
        assert int(combo.cget("width")) == 30

        combo["values"] = ["on"]
        fit_combo_width(combo, min_chars=10, max_chars=30)
        # Then: 短い候補でも選びにくいほど細くしない。
        assert int(combo.cget("width")) == 10
    finally:
        window.destroy()


def test_the_serial_tab_fits_with_a_real_usb_port_name(host: _Host) -> None:
    # Given: a typical USB-serial adapter name in the port list.
    longest = "COM3: USB Serial Port (COM3)"
    host._com_port_map = {}
    host.com_port_cb["values"] = ["COM1: 通信ポート (COM1)", longest]
    from ui.combo_fit import fit_combo_width

    fit_combo_width(host.com_port_cb, min_chars=12, max_chars=32)
    host.root.update()

    # Then: 名前が切れずに出て、それでもタブは狭い幅に収まる。
    assert host.com_port_cb.winfo_reqwidth() >= _text_px(host.com_port_cb, longest)
    assert host.serial_lf.winfo_reqwidth() <= _limit(host.serial_lf)


# -- Bcon タブ -------------------------------------------------------------


@pytest.fixture
def bcon(tk_root: tk.Tk) -> Iterator[Any]:
    from BconSetup import BconSetup

    window = tk.Toplevel(tk_root)
    tab = ttk.Frame(window)
    tab.pack(fill="both", expand=True)
    setup = BconSetup(tab, None, embedded=True, sender_provider=lambda: None)
    try:
        yield window, tab, setup
    finally:
        setup.close()
        window.destroy()


def test_the_bcon_tab_fits_a_narrow_tab_area(bcon: Any) -> None:
    window, tab, _setup = bcon
    window.update()

    # Then: Bcon タブ（bcon 系の通信方式で現れる）も狭い幅に収まる。
    #       タブ欄の要求幅は一番広いタブで決まるので、ここが広いと
    #       シリアルタブを開いていても横スクロールが出る。
    assert tab.winfo_reqwidth() <= _limit(tab) + 60


def test_the_bcon_tab_uses_two_columns_only_when_there_is_room(bcon: Any) -> None:
    window, _tab, setup = bcon

    # When: the tab area is wide.
    window.geometry("1400x700")
    window.update()

    # Then: 操作列と情報列を左右に並べる（縦を節約する）。
    ops, info = setup._col_ops, setup._col_info
    assert info.winfo_x() > ops.winfo_x() + ops.winfo_width() - 1
    assert abs(info.winfo_y() - ops.winfo_y()) < 5

    # When: the tab area is narrow.
    window.geometry("420x900")
    window.update()

    # Then: 縦 1 列に積む（横スクロールにしない）。
    assert info.winfo_y() >= ops.winfo_y() + ops.winfo_height() - 1


def test_the_bcon_tab_asks_for_the_full_size_of_its_content(bcon: Any) -> None:
    window, tab, setup = bcon
    window.geometry("420x900")
    window.update()

    # Then: 縦に積んだ中身の大きさ全部を求める（小さく求めると下が欠け、
    #       縦スクロールの範囲も足りなくなる）。
    ops, info = setup._col_ops, setup._col_info
    assert tab.winfo_reqwidth() >= max(ops.winfo_reqwidth(), info.winfo_reqwidth())
    assert tab.winfo_reqheight() >= ops.winfo_reqheight() + info.winfo_reqheight()


def test_a_bcon_tab_built_while_hidden_asks_for_its_full_size_once_shown(
    tk_root: tk.Tk,
) -> None:
    from BconSetup import BconSetup

    # Given: アプリと同じく、別のタブを開いたままノートへ足してから組み立てる
    #        （組み立て時点では中身の大きさがまだ決まっていない）。
    window = tk.Toplevel(tk_root)
    notebook = ttk.Notebook(window)
    notebook.pack(fill="both", expand=True)
    first = ttk.Frame(notebook, width=200, height=200)
    notebook.add(first, text="シリアル")
    window.update()
    tab = ttk.Frame(notebook)
    notebook.add(tab, text="Bcon")
    setup = BconSetup(tab, None, embedded=True, sender_provider=lambda: None)
    try:
        window.update()

        # When: the Bcon tab is opened.
        notebook.select(tab)
        window.update()

        # Then: 中身の大きさ全部を求めている（欠けない・縦スクロールが足りる）。
        ops, info = setup._col_ops, setup._col_info
        assert tab.winfo_reqwidth() >= max(ops.winfo_reqwidth(), info.winfo_reqwidth())
        assert tab.winfo_reqheight() >= min(
            ops.winfo_reqheight(), info.winfo_reqheight()
        )
        assert tab.winfo_reqwidth() > 100
    finally:
        setup.close()
        window.destroy()


def test_the_bcon_tab_grows_when_its_content_grows(bcon: Any) -> None:
    window, tab, setup = bcon
    window.geometry("420x900")
    window.update()

    # When: a status label in the info column gets a much longer text.
    setup._imu_label.config(text="IMU: " + "受信中 " * 30)
    # 画面の巡回（120ms ごと）が 1 周する間、待ちながら画面を回す。
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        window.update()
        time.sleep(0.02)

    # Then: 求める大きさも追従する（古い大きさのまま中身が欠けない）。
    assert tab.winfo_reqwidth() >= setup._col_info.winfo_reqwidth()
