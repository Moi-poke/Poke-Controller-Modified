"""AudioCapture の可用性ガードとデバイス列挙の検証。実機なしで回す。"""

import pytest
from core import AudioCapture


def test_unavailable_without_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AudioCapture, "_import_sounddevice", lambda: None)
    assert AudioCapture.audio_available() is False
    assert AudioCapture.list_input_devices() == []
    assert AudioCapture.list_output_devices() == []
