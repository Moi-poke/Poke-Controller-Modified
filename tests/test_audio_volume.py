"""音量操作のhostベクタ（実機不要）。

音量バー確定で出力の開き直しをしない（フリーズ防止）ことと、
表示のdB換算を固める。
"""

from __future__ import annotations

from typing import Any


class _StubCapture:
    """AudioCaptureの偽物。呼ばれた口だけ記録する。"""

    def __init__(self) -> None:
        self.volumes: list[float] = []
        self.enabled_calls: list[tuple[bool, Any]] = []

    def setMonitorVolume(self, volume: float) -> None:
        self.volumes.append(float(volume))

    def setMonitorEnabled(self, on: bool, device: Any = None) -> bool:
        self.enabled_calls.append((bool(on), device))
        return True


def _service_with_stub() -> tuple[Any, _StubCapture]:
    from services.audio_service import AudioService

    service = AudioService(notify_user=lambda _msg: None)
    stub = _StubCapture()
    service.capture = stub
    return service, stub


def test_set_volume_applies_without_reopen() -> None:
    """音量だけ変える口は開き直し（setMonitorEnabled）を呼ばない。"""
    service, stub = _service_with_stub()
    service.set_volume(0.5)
    assert stub.volumes == [0.5]
    assert stub.enabled_calls == []


def test_set_volume_clamps_range() -> None:
    """範囲外は0.0-1.0へ収める。"""
    service, stub = _service_with_stub()
    service.set_volume(1.5)
    service.set_volume(-0.5)
    assert stub.volumes == [1.0, 0.0]


def test_format_volume_label_db() -> None:
    """%表示にdB換算を添える。線形80%は約-1.9dB。」"""
    from ui.audio_panel import format_volume_label

    assert format_volume_label(1.0) == "100% (0.0 dB)"
    assert format_volume_label(0.8) == "80% (-1.9 dB)"
    assert format_volume_label(0.5) == "50% (-6.0 dB)"
    assert format_volume_label(0.0) == "0% (-∞ dB)"
