"""ImuPanelの契約。実Tk。

ジャイロ送出の操作面が組み上がり、開始/停止で線の口が呼ばれ、
統計が表示される。線・設定は偽物で、実機不要。
"""

from __future__ import annotations

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
) -> tuple[Any, Any, _FakeTransport, _FakeSettings, tk.Toplevel]:
    from ImuPanel import ImuPanel

    root = tk.Toplevel(tk_root)
    root.withdraw()
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
