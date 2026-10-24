"""ImuPanelの契約。実Tk。

ジャイロ送出の操作面が組み上がり、開始/停止で線の口が呼ばれ、
統計が表示される。線・設定は偽物で、実機不要。
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import tkinter.ttk as ttk
from typing import Any


class _FakeTransport:
    def __init__(self) -> None:
        self.opened = True
        self.source: Any = None
        self.running = False
        self.sent = 0

    def is_open(self) -> bool:
        return self.opened

    def start_imu_loop(self, source: Any) -> bool:
        self.source = source
        self.running = True
        return True

    def stop_imu_loop(self) -> bool:
        self.running = False
        return True

    def imu_stats(self) -> dict[str, int]:
        return {"sent": self.sent, "dropped": 0}


class _GatedTransport(_FakeTransport):
    """開始/停止をゲートで止め、ワーカーの重なりを観測する偽トランスポート。"""

    def __init__(self) -> None:
        super().__init__()
        self.start_calls = 0
        self.stop_calls = 0
        self.start_gate = threading.Event()
        self.stop_gate = threading.Event()

    def start_imu_loop(self, source: Any) -> bool:
        self.start_calls += 1
        self.source = source
        released = self.start_gate.wait(3.0)
        self.running = True
        return released

    def stop_imu_loop(self) -> bool:
        self.stop_calls += 1
        released = self.stop_gate.wait(3.0)
        self.running = False
        return released


class _FakeSettings:
    def __init__(self) -> None:
        self.data = {
            "source": "sine",
            "pad_index": "1",
            "sine_axis": "2",
            "sine_amp": "4096",
            "sine_period_s": "2.0",
            "step_axis": "2",
            "step_raw": "5899",
            "step_duration_s": "4.0",
            "acc_z": "4096",
        }
        self.saved = 0

    def read(self) -> dict[str, str]:
        return dict(self.data)

    def write(self, values: dict[str, str]) -> None:
        self.data.update(values)
        self.saved += 1


def _wait_until(pred: Any, root: Any = None, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if root is not None:
            try:
                root.update()
            except Exception:
                pass
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


def _make_panel(
    tk_root: tk.Tk,
    settings_data: dict[str, str] | None = None,
    transport: Any = None,
) -> tuple[Any, Any, Any, _FakeSettings, tk.Toplevel]:
    from ImuPanel import ImuPanel

    root = tk.Toplevel(tk_root)
    root.withdraw()
    if transport is None:
        transport = _FakeTransport()
    settings = _FakeSettings()
    if settings_data:
        settings.data.update(settings_data)
    logged: list[str] = []
    panel = ImuPanel(
        root,
        lambda: transport,
        settings.read,
        settings.write,
        logged.append,
    )
    panel.pack()
    root.update()
    panel._logged = logged  # type: ignore[attr-defined]
    return panel, root, transport, settings, root


def test_imu_panel_builds_with_source_and_pad(tk_root: tk.Tk) -> None:
    panel, root, _transport, _settings, _root = _make_panel(tk_root)
    try:
        assert isinstance(panel.frame, ttk.Labelframe)
        assert panel._source_var.get() == "SINE波形"
        assert panel._pad_var.get() == "1"
        assert panel._streaming is False
        # 診断用の入力源を読んだらデバッグ表示になる。
        assert panel._debug_var.get() is True
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_start_stop_calls_transport(tk_root: tk.Tk) -> None:
    panel, root, transport, settings, _root = _make_panel(tk_root)
    try:
        panel._on_toggle()
        assert _wait_until(lambda: transport.running, root), "start not called"
        assert _wait_until(lambda: panel._streaming is True, root)
        assert settings.saved >= 1
        panel._on_toggle()
        assert _wait_until(lambda: not transport.running, root), "stop not called"
        assert _wait_until(lambda: panel._streaming is False, root)
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_step_fire_only_when_stepping(tk_root: tk.Tk) -> None:
    panel, root, _transport, _settings, _root = _make_panel(tk_root)
    try:
        panel._on_step_fire()
        assert _wait_until(lambda: len(panel._logged) >= 1, root)
        assert "STEP送出中" in panel._logged[0]
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_closed_serial_logs_and_stays_stopped(tk_root: tk.Tk) -> None:
    panel, root, transport, _settings, _root = _make_panel(tk_root)
    try:
        transport.opened = False
        panel._on_toggle()
        assert _wait_until(lambda: len(panel._logged) >= 1, root)
        assert "開いていません" in panel._logged[0]
        assert transport.running is False
        assert panel._streaming is False
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_detail_expands_and_collapses(tk_root: tk.Tk) -> None:
    panel, root, _transport, _settings, _root = _make_panel(tk_root)
    try:
        assert getattr(panel, "_gyro_view", None) is None
        panel._detail_var.set(True)
        panel._on_detail_toggled()
        root.update()
        view = getattr(panel, "_gyro_view", None)
        assert view is not None
        view.push_raw(0, 0, 4096, 100, 0, 0)
        root.update()
        panel._detail_var.set(False)
        panel._on_detail_toggled()
        root.update()
        assert getattr(panel, "_gyro_view", None) is None
        assert view._stop is True
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_hid_invert_reads_and_writes_hid_keys(tk_root: tk.Tk) -> None:
    """HID直結の反転は hid_* を読み書きし、SDL用の符号へ混ぜない。"""
    data = {
        "source": "hid",
        "accel_signs": "-1,-1,1",
        "gyro_signs": "-1,-1,1",
        "hid_accel_signs": "1,-1,-1",
        "hid_gyro_signs": "1,1,1",
    }
    panel, root, _transport, settings, _root = _make_panel(tk_root, data)
    try:
        assert panel._source_var.get() == "HID直結"
        # HID直結は通常運用の源なので、反転はデバッグ表示でだけ出す。
        assert panel._invert_cb.winfo_manager() == ""
        panel._debug_var.set(True)
        panel._on_debug_toggled()
        assert panel._invert_cb.winfo_manager() == "grid"
        assert panel._invert_var.get() == "加速度"

        panel._invert_var.set("ジャイロ")
        panel._on_invert_changed()

        assert settings.data["hid_accel_signs"] == "1,1,1"
        assert settings.data["hid_gyro_signs"] == "1,-1,-1"
        assert settings.data["accel_signs"] == "-1,-1,1"
        assert settings.data["gyro_signs"] == "-1,-1,1"
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_invert_follows_selected_source(tk_root: tk.Tk) -> None:
    """入力源を切り替えると、その源が読む符号の表示へ追従する。"""
    data = {
        "source": "sensor",
        "accel_signs": "1,-1,-1",
        "gyro_signs": "1,1,1",
        "hid_accel_signs": "1,1,1",
        "hid_gyro_signs": "1,-1,-1",
    }
    panel, root, _transport, settings, _root = _make_panel(tk_root, data)
    try:
        panel._debug_var.set(True)
        panel._on_debug_toggled()
        assert panel._invert_var.get() == "加速度"

        panel._source_var.set("HID直結")
        panel._on_source_changed()
        assert panel._invert_var.get() == "ジャイロ"

        panel._source_var.set("パッドAPI")
        panel._on_source_changed()
        assert panel._invert_var.get() == "加速度"
        # 表示の追従だけで、設定は書き換えない。
        assert settings.saved == 0
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_unmodified_invert_keeps_configured_signs(tk_root: tk.Tk) -> None:
    """反転を触らずに送出開始しても、設定ファイルの既定符号を書き換えない。"""
    data = {
        "source": "sine",
        "accel_signs": "-1,-1,1",
        "gyro_signs": "-1,-1,1",
    }
    panel, root, transport, settings, _root = _make_panel(tk_root, data)
    try:
        assert panel._invert_var.get() == "設定値のまま"
        panel._on_toggle()
        assert _wait_until(lambda: transport.running, root)
        assert settings.data["accel_signs"] == "-1,-1,1"
        assert settings.data["gyro_signs"] == "-1,-1,1"
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_manual_out_of_range_is_normalized_on_start(
    tk_root: tk.Tk,
) -> None:
    """範囲外の手動入力は表示と送信値の両方をint16へ丸める。"""
    data = {"source": "manual"}
    panel, root, transport, settings, _root = _make_panel(tk_root, data)
    try:
        panel._manual_vars[0].set("40000")
        assert panel._manual_vars[0].get() == "40000"

        panel._on_toggle()
        assert _wait_until(lambda: transport.running, root)
        assert panel._manual_vars[0].get() == "32767"
        assert settings.data["acc_x"] == "32767"
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_manual_and_detail_rows_stay_below_controls(
    tk_root: tk.Tk,
) -> None:
    """手動入力と詳細表示が反転・感度の行へ重ならない。"""
    panel, root, _transport, _settings, _root = _make_panel(
        tk_root, {"source": "manual"}
    )
    try:
        root.deiconify()
        root.update()
        scale = panel._scale_cb
        manual = panel._manual_frame
        detail = panel._detail_cb
        assert manual.winfo_rooty() >= scale.winfo_rooty() + scale.winfo_height()

        panel._detail_var.set(True)
        panel._on_detail_toggled()
        root.update()
        view = panel._gyro_view
        assert view is not None
        assert panel._invert_cb.winfo_ismapped()
        assert view.winfo_rooty() >= manual.winfo_rooty() + manual.winfo_height()
        assert view.winfo_rooty() >= detail.winfo_rooty() + detail.winfo_height()
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_debug_off_collapses_detail(tk_root: tk.Tk) -> None:
    """デバッグOFFで詳細チェックだけでなく波形も畳む。"""
    panel, root, _transport, _settings, _root = _make_panel(tk_root)
    try:
        panel._debug_var.set(True)
        panel._on_debug_toggled()
        panel._detail_var.set(True)
        panel._on_detail_toggled()
        root.update()
        view = panel._gyro_view
        assert view is not None

        panel._debug_var.set(False)
        panel._on_debug_toggled()
        root.update()

        assert panel._detail_var.get() is False
        assert panel._gyro_view is None
        assert panel._detail_cb.winfo_manager() == ""
        assert view._stop is True
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_live_scale_and_invert_apply_while_streaming(
    tk_root: tk.Tk,
) -> None:
    """送出中の感度・反転は次のサンプルから効き、停止中は次回開始から。"""
    data = {
        "source": "manual",
        "accel_signs": "1,1,1",
        "gyro_signs": "1,1,1",
        "gyro_scale": "1.0",
    }
    panel, root, transport, settings, _root = _make_panel(tk_root, data)
    try:
        panel._manual_vars[3].set("1000")
        panel._manual_vars[4].set("200")
        panel._on_toggle()
        assert _wait_until(lambda: transport.running, root)
        assert _wait_until(lambda: panel._streaming is True, root)
        source = transport.source
        assert source is not None
        assert source() == (0, 0, 4096, 1000, 200, 0)

        panel._scale_var.set("0.5")
        panel._on_scale_changed()
        assert settings.data["gyro_scale"] == "0.5"
        assert source() == (0, 0, 4096, 500, 100, 0)
        assert _wait_until(lambda: any("即時" in line for line in panel._logged), root)

        panel._invert_var.set("ジャイロ")
        panel._on_invert_changed()
        assert settings.data["gyro_signs"] == "1,-1,-1"
        assert source() == (0, 0, 4096, 500, -100, 0)

        panel._on_toggle()
        assert _wait_until(
            lambda: panel._streaming is False and panel._stopping is False, root
        )
        trim_before = panel._live_trim
        panel._scale_var.set("0.25")
        panel._on_scale_changed()
        assert panel._live_trim == trim_before
        assert _wait_until(
            lambda: any("次回から" in line for line in panel._logged), root
        )
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_hid_live_invert_uses_hid_keys_only(tk_root: tk.Tk) -> None:
    """HID直結の送出中反転は hid_* だけを読み書きし、SDL符号へ混ぜない。"""
    data = {
        "source": "hid",
        "accel_signs": "-1,-1,1",
        "gyro_signs": "-1,-1,1",
        "hid_accel_signs": "1,1,1",
        "hid_gyro_signs": "1,1,1",
    }
    panel, root, transport, settings, _root = _make_panel(tk_root, data)
    try:
        sample = (100, 200, 300, 10, 20, 30)

        def read() -> tuple[int, int, int, int, int, int]:
            return sample

        # 実HIDを開かず、watched() の符号適用だけを確かめる。
        setattr(panel, "_build_source", lambda _source, _pad: read)
        panel._on_toggle()
        assert _wait_until(lambda: panel._streaming is True, root)
        source = transport.source
        assert source is not None
        assert source() == sample

        panel._invert_var.set("加速度")
        panel._on_invert_changed()
        assert settings.data["hid_accel_signs"] == "1,-1,-1"
        assert settings.data["hid_gyro_signs"] == "1,1,1"
        assert source() == (100, -200, -300, 10, 20, 30)

        panel._invert_var.set("ジャイロ")
        panel._on_invert_changed()
        assert settings.data["hid_accel_signs"] == "1,1,1"
        assert settings.data["hid_gyro_signs"] == "1,-1,-1"
        assert source() == (100, 200, 300, 10, -20, -30)

        # SDL用の符号は表示の追従にも書き込みにも触らない。
        assert settings.data["accel_signs"] == "-1,-1,1"
        assert settings.data["gyro_signs"] == "-1,-1,1"
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_hid_bias_and_axis_scale_apply_before_signs(
    tk_root: tk.Tk,
) -> None:
    """HIDのgyro_biasはrawへ換算し、軸別倍率→符号の順で効く。"""
    data = {
        "source": "hid",
        "hid_accel_signs": "1,1,1",
        "hid_gyro_signs": "1,1,1",
        "gyro_bias": "0.1,0,0",
        "hid_gyro_scale": "2.0,1.0,0.5",
        "gyro_scale": "1.0",
    }
    panel, root, transport, _settings, _root = _make_panel(tk_root, data)
    try:
        sample = (0, 0, 4096, 1000, 200, 400)

        def read() -> tuple[int, int, int, int, int, int]:
            return sample

        setattr(panel, "_build_source", lambda _source, _pad: read)
        panel._on_toggle()
        assert _wait_until(lambda: panel._streaming is True, root)
        source = transport.source
        assert source is not None
        from core.sdl_sensor import gyro_to_raw

        bias = gyro_to_raw(0.1)
        assert bias == 94
        # バイアス→軸別倍率→符号。Xは(1000-94)*2、Zは400*0.5。
        assert source() == (0, 0, 4096, (1000 - bias) * 2, 200, 200)

        panel._invert_var.set("ジャイロ")
        panel._on_invert_changed()
        assert source() == (0, 0, 4096, (1000 - bias) * 2, -200, -200)
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_sdl_bias_is_not_double_applied(tk_root: tk.Tk) -> None:
    """SDL路のバイアスは reader 側だけ。watched() では引かない。"""
    data = {
        "source": "sensor",
        "accel_signs": "1,1,1",
        "gyro_signs": "1,1,1",
        "gyro_bias": "0.1,0,0",
        "hid_gyro_scale": "2.0,2.0,2.0",
        "gyro_scale": "1.0",
    }
    panel, root, transport, settings, _root = _make_panel(tk_root, data)
    try:
        trim = panel._live_trim_from_settings(settings.read(), "sensor")
        assert trim[4] is None
        assert trim[5] is None
        hid_trim = panel._live_trim_from_settings(settings.read(), "hid")
        assert hid_trim[4] == (94, 0, 0)

        sample = (0, 0, 4096, 1000, 200, 400)

        def read() -> tuple[int, int, int, int, int, int]:
            return sample

        setattr(panel, "_build_source", lambda _source, _pad: read)
        panel._on_toggle()
        assert _wait_until(lambda: panel._streaming is True, root)
        assert transport.source is not None
        assert transport.source() == sample
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_toggle_does_not_overlap_workers(tk_root: tk.Tk) -> None:
    """連打しても開始/停止ワーカーが並走せず、停止後は再開始できる。"""
    transport = _GatedTransport()
    data = {
        "source": "manual",
        "accel_signs": "1,1,1",
        "gyro_signs": "1,1,1",
    }
    panel, root, _transport, _settings, _root = _make_panel(tk_root, data, transport)
    try:
        panel._on_toggle()
        panel._on_toggle()
        assert getattr(panel, "_starting") is True
        assert _wait_until(lambda: transport.start_calls == 1, root)
        assert transport.stop_calls == 0
        assert _wait_until(
            lambda: any("処理中" in line for line in panel._logged), root
        )

        transport.start_gate.set()
        assert _wait_until(lambda: panel._streaming is True, root)
        assert getattr(panel, "_starting") is False
        panel._on_toggle()
        panel._on_toggle()
        assert getattr(panel, "_stopping") is True
        assert _wait_until(lambda: transport.stop_calls == 1, root)
        assert transport.start_calls == 1
        assert _wait_until(
            lambda: any("処理中" in line for line in panel._logged), root
        )

        transport.stop_gate.set()
        assert _wait_until(
            lambda: panel._streaming is False and panel._stopping is False, root
        )

        # 停止完了後に開始を押したら、黙って無音にせず開始する。
        panel._on_toggle()
        assert _wait_until(lambda: transport.start_calls == 2, root)
        assert _wait_until(lambda: panel._streaming is True, root)
    finally:
        transport.start_gate.set()
        transport.stop_gate.set()
        panel.close()
        root.destroy()


def test_imu_panel_source_side_can_leave_signs_to_watched() -> None:
    """source側の軸変換は符号を掛けない（watchedとの二重適用防止）。"""
    from ImuPanel import ImuPanel

    class _Reader:
        def __init__(self) -> None:
            self.maps: list[tuple[Any, ...]] = []

        def set_axis_map(self, *args: Any) -> None:
            self.maps.append(args)

    settings = {
        "accel_axes": "2,0,1",
        "accel_signs": "-1,-1,1",
        "gyro_axes": "2,0,1",
        "gyro_signs": "-1,-1,1",
    }
    reader = _Reader()
    ImuPanel._apply_axis_settings(reader, settings, apply_signs=False)
    assert reader.maps == [((2, 0, 1), (1, 1, 1), (2, 0, 1), (1, 1, 1))]

    fn = ImuPanel._sample_transform(settings, apply_signs=False)
    assert fn is not None
    assert fn((1, 2, 3, 4, 5, 6)) == (3, 1, 2, 6, 4, 5)


def test_imu_panel_hid_source_rearms_through_ensure_live(
    tk_root: tk.Tk, monkeypatch
) -> None:
    """HIDが開いたまま0x30が古い場合も、開始時にensure_liveで再武装する。"""
    import core.nintendo_hid as nintendo_hid

    class _FakeHid:
        def __init__(self) -> None:
            self.ensure_calls = 0
            self.is_open_calls = 0

        def is_open(self) -> bool:
            self.is_open_calls += 1
            return True

        def ensure_live(self) -> bool:
            self.ensure_calls += 1
            return True

        def read_next(self):
            return (1, 2, 3, 4, 5, 6)

    fake = _FakeHid()
    monkeypatch.setattr(nintendo_hid, "get_shared_reader", lambda: fake)
    panel, root, _transport, _settings, _root = _make_panel(tk_root, {"source": "hid"})
    try:
        source = panel._build_source("hid", 0)
        assert source is not None
        assert source() == (1, 2, 3, 4, 5, 6)
        assert fake.ensure_calls == 1
        # 0x30が返っている間は再武装しない。
        source()
        assert fake.ensure_calls == 1
    finally:
        panel.close()
        root.destroy()
