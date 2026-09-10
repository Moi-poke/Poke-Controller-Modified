"""AudioMixin の検証。実機なし・DSP合成波で回す。"""

import numpy as np
import pytest
from core import audio_dsp
from core.CommandAudio import AudioMixin


def sine(freq: float, seconds: float, rate: int = 44100) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float64) / rate
    return (0.5 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


class FakeCapture:
    def __init__(self, data: np.ndarray) -> None:
        self._data = data

    def readWindow(self, seconds: float) -> np.ndarray | None:
        _ = seconds
        return self._data.copy()


class Probe(AudioMixin):
    """待ち系スタブ＋FakeCaptureで Mixin だけ動かす。"""

    def __init__(self, data: np.ndarray) -> None:
        self._initAudio(FakeCapture(data))
        self.Discord = None

    def wait(self, seconds: float) -> None:
        _ = seconds

    def _deadline(self, timeout: float):  # type: ignore[no-untyped-def]
        calls = 0

        def expired() -> bool:
            nonlocal calls
            calls += 1
            return calls > 3

        return expired


def test_wait_tone_hits() -> None:
    data = (sine(3100.0, 1.5) + sine(4200.0, 1.5)).astype(np.float32)
    probe = Probe(data)
    powers = [
        audio_dsp.band_power(data, 44100, lo, hi)
        for lo, hi in [(3000.0, 3200.0), (4150.0, 4400.0)]
    ]
    assert (
        probe.waitTone(
            [(3000.0, 3200.0), (4150.0, 4400.0)],
            [p * 0.5 for p in powers],
            timeout=10.0,
        )
        is True
    )


def test_wait_tone_timeout() -> None:
    probe = Probe(np.zeros(44100, dtype=np.float32))
    assert probe.waitTone([(3000.0, 3200.0)], [1e12], timeout=1.0) is False


def test_waitsound_matches_recorded(tmp_path: object) -> None:
    import pathlib

    needle = sine(880.0, 0.5)
    # 正規化相互相関のピークは ||needle||/||hay|| になる。
    # 無音区間を長く足すとピークが下がって閾値に届かないため、
    # hay は needle＋微小ノイズ（自己相関≒1を保てる範囲）にする。
    noise = 0.02 * sine(1733.0, 0.5)
    hay = (needle + noise).astype(np.float32)
    wav_path = str(pathlib.Path(str(tmp_path)) / "tone.wav")
    Probe(hay).recordClipTo(wav_path, source=needle, rate=44100)
    probe = Probe(hay)
    assert probe.isSoundPresent(wav_path, threshold=0.9, window_s=2.0) is True


def test_no_audio_raises() -> None:
    probe = Probe.__new__(Probe)
    probe._initAudio(None)
    probe.Discord = None
    with pytest.raises(RuntimeError):
        probe.isTonePresent([(3000.0, 3200.0)], [1.0])
