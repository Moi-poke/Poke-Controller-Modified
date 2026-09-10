"""audio_dsp（DSP純粋関数）の検証。実機なしで回す。"""

import numpy as np

# 計画書は `import audio_dsp` と書くが、実体は SerialController/core/audio_dsp.py
# のため既存規約（`from core import X`）で読む。Task 7 のテストも同形。
from core import audio_dsp


def sine(freq: float, seconds: float, rate: int = 44100) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float64) / rate
    return (0.5 * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


def test_band_power_hits_target_band() -> None:
    x = sine(3100.0, 1.0)
    hit = audio_dsp.band_power(x, 44100, 3000.0, 3200.0)
    miss = audio_dsp.band_power(x, 44100, 5000.0, 5200.0)
    assert hit > miss * 10.0


def test_band_power_silence_is_zero() -> None:
    x = np.zeros(44100, dtype=np.float32)
    assert audio_dsp.band_power(x, 44100, 3000.0, 3200.0) == 0.0


def test_is_tone_dual_band() -> None:
    x = (sine(3100.0, 1.0) + sine(4200.0, 1.0)).astype(np.float32)
    bands = [(3000.0, 3200.0), (4150.0, 4400.0)]
    powers = [audio_dsp.band_power(x, 44100, lo, hi) for lo, hi in bands]
    assert audio_dsp.is_tone(x, 44100, bands, [p * 0.5 for p in powers])
    assert not audio_dsp.is_tone(x, 44100, bands, [p * 2.0 for p in powers])


def test_normalized_xcorr_self_is_one() -> None:
    x = sine(440.0, 1.0)
    # FFT経由の相関は小数誤差が乗るため等値ではなく許容誤差で見る
    assert abs(audio_dsp.normalized_xcorr(x, x) - 1.0) < 1e-6


def test_normalized_xcorr_unrelated_is_small() -> None:
    a = sine(440.0, 1.0)
    b = sine(880.0, 1.0)
    assert abs(audio_dsp.normalized_xcorr(a, b)) < 0.2


def test_dbfs_silence_is_minus_inf() -> None:
    x = np.zeros(100, dtype=np.float32)
    assert audio_dsp.dbfs(x) == float("-inf")
