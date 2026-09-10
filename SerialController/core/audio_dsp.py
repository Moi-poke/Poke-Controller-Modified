#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_dsp.py - 音声検知の純粋関数群.

sounddevice も tkinter も読まない。合成波で検証できる形にし、
実機なしの pytest で検知ロジックを固める。窓つき入力を想定し、
呼び出し側（AudioMixin）が readWindow() の複製を渡す。
"""

from __future__ import annotations

import math
import wave
from collections.abc import Sequence

import numpy as np
from scipy import signal

# 録音・検知の基準レート。listen_shiny.py と同じ 44100Hz mono。
SAMPLE_RATE = 44100


def dbfs(x: np.ndarray) -> float:
    """フルスケール基準のレベル(dBFS)。無音は -inf。"""
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(peak)


def band_power(x: np.ndarray, rate: int, lo_hz: float, hi_hz: float) -> float:
    """指定帯域のパワー。Hann窓＋rFFTで漏れを抑える。"""
    if x.size == 0 or rate <= 0 or hi_hz <= lo_hz:
        return 0.0
    windowed = x.astype(np.float64) * np.hanning(x.size)
    spectrum = np.fft.rfft(windowed)
    freqs = np.fft.rfftfreq(x.size, d=1.0 / float(rate))
    mask = (freqs >= lo_hz) & (freqs <= hi_hz)
    return float(np.sum(np.abs(spectrum[mask]) ** 2))


def is_tone(
    x: np.ndarray,
    rate: int,
    bands: Sequence[tuple[float, float]],
    thresholds: Sequence[float],
) -> bool:
    """全帯域が閾値を超えたら True（色違い音の 3100Hz＋4200Hz 方式の一般化）。"""
    if len(bands) != len(thresholds) or not bands:
        raise ValueError("bands と thresholds は同数の空でない列です")
    return all(
        band_power(x, rate, lo, hi) >= th for (lo, hi), th in zip(bands, thresholds)
    )


def normalized_xcorr(hay: np.ndarray, needle: np.ndarray) -> float:
    """正規化相互相関の最大値（-1.0〜1.0）。同一波形なら 1.0。"""
    hay = hay.astype(np.float64).ravel()
    needle = needle.astype(np.float64).ravel()
    if hay.size == 0 or needle.size == 0 or hay.size < needle.size:
        return 0.0
    hay = hay - hay.mean()
    needle = needle - needle.mean()
    denom = float(np.linalg.norm(hay) * np.linalg.norm(needle))
    if denom <= 0.0:
        return 0.0
    corr = signal.correlate(hay, needle, mode="valid")
    return float(np.max(corr) / denom)


def match_template(
    x: np.ndarray, rate: int, template: np.ndarray, template_rate: int
) -> float:
    """録音窓とwavテンプレートの一致度。レート不一致は resample で吸収する。"""
    if template_rate != rate:
        ratio = float(rate) / float(template_rate)
        count = max(1, int(round(template.size * ratio)))
        template = signal.resample(template.astype(np.float64), count).astype(
            np.float32
        )
    return normalized_xcorr(x, template)


def load_wav_mono(path: str) -> tuple[np.ndarray, int]:
    """wav を mono float32 で読む。PCM以外・形式違いは理由付き例外。"""
    try:
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
    except (wave.Error, OSError, EOFError) as e:
        raise ValueError(f"wavを読めません: {path} ({e})") from e
    if width != 2:
        raise ValueError(f"16bit PCMのみ対応です: {path} (幅{width})")
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1).astype(np.float32)
    return data, int(rate)
