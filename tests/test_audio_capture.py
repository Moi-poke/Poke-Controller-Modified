"""AudioCapture の可用性ガードとデバイス列挙の検証。実機なしで回す。"""

from typing import Any

import numpy as np
import pytest
from core import AudioCapture as AC


def test_unavailable_without_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AC, "_import_sounddevice", lambda: None)
    assert AC.audio_available() is False
    assert AC.list_input_devices() == []
    assert AC.list_output_devices() == []


class FakeStream:
    """sounddevice ストリームの偽物。fire() で受信を再現する。"""

    def __init__(self, **kwargs: Any) -> None:
        self.callback: Any = kwargs["callback"]
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def fire(self, data: np.ndarray) -> None:
        assert self.started
        self.callback(data, len(data), None, None)


def test_open_read_close() -> None:
    box: dict[str, FakeStream] = {}

    def factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        box["stream"] = stream
        return stream

    cap = AC.AudioCapture(input_factory=factory)
    assert cap.isOpened() is False
    assert cap.openInput("dummy") is True
    assert cap.isOpened() is True

    chunk = np.ones(1024, dtype=np.float32)
    box["stream"].fire(chunk)
    window = cap.readWindow(1.0)
    assert window is not None
    assert window.size >= 1024
    assert float(np.max(window)) == 1.0

    # 複製であること（外で書き換えても次回に影響しない）
    window[:] = 0.0
    again = cap.readWindow(1.0)
    assert again is not None
    assert float(np.max(again)) == 1.0

    cap.close()
    assert cap.isOpened() is False
    assert cap.readWindow(1.0) is None


def test_open_fails_without_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AC, "_import_sounddevice", lambda: None)
    cap = AC.AudioCapture()
    assert cap.openInput("dummy") is False
    assert cap.isOpened() is False


def test_record_collects_seconds() -> None:
    box: dict[str, FakeStream] = {}

    def factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        box["stream"] = stream
        return stream

    cap = AC.AudioCapture(input_factory=factory)
    assert cap.openInput("dummy") is True
    chunk = np.full(1024, 0.25, dtype=np.float32)
    for _ in range(5):
        box["stream"].fire(chunk)
    data = cap.record(0.05)
    assert data.size > 0
    assert float(np.mean(data)) == 0.25
    cap.close()


def test_monitor_toggle_and_volume() -> None:
    inbox: dict[str, FakeStream] = {}
    outbox: dict[str, FakeStream] = {}

    def in_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        inbox["stream"] = stream
        return stream

    def out_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        outbox["stream"] = stream
        return stream

    cap = AC.AudioCapture(input_factory=in_factory, output_factory=out_factory)
    assert cap.isMonitorEnabled() is False
    assert cap.openInput("dummy") is True
    # 出力先未指定でも既定出力で開く（device=None 透過）
    assert cap.setMonitorEnabled(True) is True
    assert cap.isMonitorEnabled() is True
    cap.setMonitorVolume(0.5)
    inbox["stream"].fire(np.full(1024, 0.4, dtype=np.float32))
    buf = np.zeros((1024, 1), dtype=np.float32)
    outbox["stream"].callback(buf, 1024, None, None)
    assert float(np.max(np.abs(buf))) > 0.0
    assert float(np.max(np.abs(buf))) <= 0.5 + 1e-6
    cap.setMonitorEnabled(False)
    assert cap.isMonitorEnabled() is False
    cap.close()


def test_apply_volume_clips() -> None:
    frames = np.full(4, 0.9, dtype=np.float32)
    doubled = AC.apply_volume(frames, 2.0)
    assert float(np.max(np.abs(doubled))) <= 1.0
    assert float(AC.apply_volume(frames, 0.0)[0]) == 0.0
