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


def _make_panel() -> tuple[Any, Any, _FakeTransport, _FakeSettings, tk.Tk]:
    from ImuPanel import ImuPanel

    root = tk.Tk()
    root.withdraw()
    transport = _FakeTransport()
    settings = _FakeSettings()
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


def test_imu_panel_builds_with_source_and_pad():
    panel, root, _transport, _settings, _root = _make_panel()
    try:
        assert isinstance(panel.frame, ttk.Labelframe)
        assert panel._source_var.get() == "SINE波形"
        assert panel._pad_var.get() == "1"
        assert panel._streaming is False
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_start_stop_calls_transport():
    panel, root, transport, settings, _root = _make_panel()
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


def test_imu_panel_step_fire_only_when_stepping():
    panel, root, _transport, _settings, _root = _make_panel()
    try:
        panel._on_step_fire()
        assert _wait_until(lambda: len(panel._logged) >= 1, root)
        assert "STEP送出中" in panel._logged[0]
    finally:
        panel.close()
        root.destroy()


def test_imu_panel_closed_serial_logs_and_stays_stopped():
    panel, root, transport, _settings, _root = _make_panel()
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


def test_imu_panel_detail_expands_and_collapses():
    panel, root, _transport, _settings, _root = _make_panel()
    try:
        assert panel._gyro_view is None
        panel._detail_var.set(True)
        panel._on_detail_toggled()
        root.update()
        assert panel._gyro_view is not None
        view = panel._gyro_view
        view.push_raw(0, 0, 4096, 100, 0, 0)
        root.update()
        panel._detail_var.set(False)
        panel._on_detail_toggled()
        root.update()
        assert panel._gyro_view is None
        assert view._stop is True
    finally:
        panel.close()
        root.destroy()
