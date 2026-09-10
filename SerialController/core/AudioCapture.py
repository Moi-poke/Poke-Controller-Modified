#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AudioCapture.py - 音声入力の所有と最新区間の供給.

Camera.py の音声版。入力デバイスを1つだけ開き、リングバッファへ
書き続ける。検知（AudioMixin）もモニター再生もこの1本から読む。
デバイスをコマンドごとに開き直すと排他で競合するため、開閉の
所有はここへ寄せる。

sounddevice はトップレベルで import しない。Linux/mac で system
PortAudio が無い環境では import 自体が落ちるため、読めたときだけ
使う（欠如時は False・空リスト＋ログで無効化し、落とさない）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
from loguru import logger

# 録音・検知の基準。audio_dsp.SAMPLE_RATE と同じ値を使う。
AUDIO_RATE = 44100
AUDIO_CHANNELS = 1
AUDIO_CHUNK = 1024


def _import_sounddevice() -> Any | None:
    """sounddevice を読む。無い・壊れている環境では None。"""
    try:
        import sounddevice as sd

        return sd
    except Exception as e:
        logger.warning(f"音声機能は無効です（sounddevice: {e}）")
        return None


def audio_available() -> bool:
    """音声I/Oが使えるか。バックエンドの有無だけを見る。"""
    return _import_sounddevice() is not None


def _device_names(want_input: bool) -> list[str]:
    """入力／出力に出せるデバイス名の一覧。失敗時は空リスト。"""
    sd = _import_sounddevice()
    if sd is None:
        return []
    try:
        devices = sd.query_devices()
    except Exception as e:
        logger.warning(f"音声デバイスを列挙できません: {e}")
        return []
    names = []
    for dev in devices:
        try:
            ok = (
                dev["max_input_channels"] > 0
                if want_input
                else dev["max_output_channels"] > 0
            )
            if ok:
                names.append(str(dev["name"]))
        except (KeyError, TypeError):
            continue
    return names


def list_input_devices() -> list[str]:
    """録音に使えるデバイス名の一覧。"""
    return _device_names(True)


def list_output_devices() -> list[str]:
    """再生に使えるデバイス名の一覧。"""
    return _device_names(False)


class AudioCapture:
    """入力1ストリームの所有者。直近 RING_SECONDS 秒を配る。"""

    def __init__(
        self,
        rate: int = AUDIO_RATE,
        ring_seconds: float = 5.0,
        input_factory: Any = None,
    ) -> None:
        self._rate = int(rate)
        capacity = max(AUDIO_CHUNK, int(self._rate * float(ring_seconds)))
        self._ring = np.zeros(capacity, dtype=np.float32)
        self._pos = 0
        self._filled = 0
        self._lock = threading.Lock()
        self._stream: Any = None
        self._input_name = ""
        self._factory = input_factory

    def openInput(self, device: str | int | None) -> bool:
        """入力を開く。既に開いていれば閉じてから開き直す。成否を返す。"""
        self.close()
        sd = _import_sounddevice()
        if sd is None:
            return False
        name = "" if device is None else str(device)
        try:
            if self._factory is not None:
                stream = self._factory(
                    samplerate=self._rate,
                    channels=AUDIO_CHANNELS,
                    blocksize=AUDIO_CHUNK,
                    device=device,
                    callback=self._on_input,
                )
            else:
                stream = sd.InputStream(
                    samplerate=self._rate,
                    channels=AUDIO_CHANNELS,
                    dtype="float32",
                    blocksize=AUDIO_CHUNK,
                    device=device,
                    callback=self._on_input,
                )
            stream.start()
        except Exception as e:
            logger.error(f"音声入力を開けません ({name or '既定'}): {e}")
            return False
        self._stream = stream
        self._input_name = name
        logger.debug(f"音声入力を開きました: {name or '既定'}")
        return True

    def _on_input(self, indata: Any, frames: int, _time: Any, status: Any) -> None:
        """PortAudioスレッドから呼ばれる。重い処理は置かない。"""
        if status:
            logger.debug(f"音声入力の状態: {status}")
        mono = np.asarray(indata, dtype=np.float32).ravel()
        if mono.size == 0:
            return
        with self._lock:
            end = self._pos + mono.size
            if end <= self._ring.size:
                self._ring[self._pos : end] = mono
            else:
                first = self._ring.size - self._pos
                self._ring[self._pos :] = mono[:first]
                self._ring[: end - self._ring.size] = mono[first:]
            self._pos = end % self._ring.size
            self._filled = min(self._filled + mono.size, self._ring.size)

    def isOpened(self) -> bool:
        return self._stream is not None

    def selected_input(self) -> str:
        return self._input_name

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception as e:
            logger.warning(f"音声入力の解放で例外: {e}")

    def readWindow(self, seconds: float) -> np.ndarray | None:
        """直近 seconds 秒の複製を返す。未取得・未openは None。"""
        want = int(self._rate * float(seconds))
        if want <= 0 or self._stream is None:
            return None
        with self._lock:
            if self._filled == 0:
                return None
            want = min(want, self._filled)
            end = self._pos
            start = (end - want) % self._ring.size
            if start < end or self._filled < self._ring.size and start == 0:
                out = self._ring[end - want : end].copy()
            else:
                out = np.concatenate((self._ring[start:], self._ring[:end])).copy()
        return out[-want:] if out.size > want else out

    def _level(self) -> np.ndarray | None:
        return self.readWindow(0.1)

    def peak(self) -> float:
        window = self._level()
        if window is None or window.size == 0:
            return 0.0
        return float(np.max(np.abs(window)))

    def rms(self) -> float:
        window = self._level()
        if window is None or window.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(window.astype(np.float64) ** 2)))

    def record(self, seconds: float) -> np.ndarray:
        """seconds 秒ぶん集めて返す。未openは空配列。"""
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        deadline = time.monotonic() + float(seconds)
        while time.monotonic() < deadline:
            time.sleep(0.05)
        window = self.readWindow(seconds)
        if window is None:
            return np.zeros(0, dtype=np.float32)
        return window
