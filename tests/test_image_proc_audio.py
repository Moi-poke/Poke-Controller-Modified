"""ImageProcAudioPythonCommand（画像＋音声の併用基底）の検証。実機なし。"""

from __future__ import annotations

import numpy as np
from Commands import PythonCommandBase
from Commands.PythonCommandBase import ImageProcAudioPythonCommand
from core import audio_dsp


def sine(freq: float, seconds: float, rate: int = 44100) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float64) / rate
    return (0.5 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


class FakeCapture:
    def __init__(self, data: np.ndarray) -> None:
        self._data = data

    def readWindow(self, seconds: float) -> np.ndarray | None:
        _ = seconds
        return self._data.copy()


class FakeCam:
    pass


class ConcreteCmd(ImageProcAudioPythonCommand):
    def do(self) -> None:
        pass


def test_combined_base_wires_vision_and_audio() -> None:
    data = (sine(3100.0, 1.5) + sine(4200.0, 1.5)).astype(np.float32)
    cam = FakeCam()
    cmd = ConcreteCmd(cam, None, FakeCapture(data))
    assert cmd.camera is cam
    assert cmd.audio is not None
    assert cmd.isTonePresent(
        [(3000.0, 3200.0), (4150.0, 4400.0)],
        [
            p * 0.5
            for p in [
                audio_dsp.band_power(data, 44100, lo, hi)
                for lo, hi in [(3000.0, 3200.0), (4150.0, 4400.0)]
            ]
        ],
        1.5,
    )


def test_combined_base_is_image_proc() -> None:
    """実行側の ImageProc 分岐（カメラ・音声属性の注入）に載ること。"""
    assert issubclass(
        ImageProcAudioPythonCommand, PythonCommandBase.ImageProcPythonCommand
    )
