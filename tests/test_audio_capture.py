"""AudioCapture の可用性ガードとデバイス列挙の検証。実機なしで回す。"""

from typing import Any

import numpy as np
import pytest
from core import AudioCapture, AudioCapture as AC


def test_unavailable_without_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioCapture, "_import_sounddevice", lambda: None)
    assert AudioCapture.audio_available() is False
    assert AudioCapture.list_input_devices() == []
    assert AudioCapture.list_output_devices() == []


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
