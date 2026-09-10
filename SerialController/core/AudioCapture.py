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

import queue
import threading
import time
from typing import Any

import numpy as np
from core import audio_dsp
from loguru import logger

# 録音・検知の基準。audio_dsp.SAMPLE_RATE をそのまま使う。
# 別々の数値で持つと乖離する（検知の前提が崩れる）ため参照で揃える。
AUDIO_RATE = audio_dsp.SAMPLE_RATE
AUDIO_CHANNELS = 1
AUDIO_CHUNK = 1024


def _import_sounddevice() -> Any | None:
    """sounddevice を読む。無い・壊れている環境では None。"""
    try:
        import sounddevice as sd

        return sd
    except Exception as e:
        logger.debug(f"音声機能は無効です（sounddevice: {e}）")
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


def apply_volume(frames: np.ndarray, volume: float) -> np.ndarray:
    """音量を掛けて [-1, 1] に収める純粋関数。"""
    out = frames.astype(np.float64) * float(volume)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


class AudioCapture:
    """入力1ストリームの所有者。直近 RING_SECONDS 秒を配る。"""

    def __init__(
        self,
        rate: int = AUDIO_RATE,
        ring_seconds: float = 5.0,
        input_factory: Any = None,
        output_factory: Any = None,
    ) -> None:
        self._rate = int(rate)
        capacity = max(AUDIO_CHUNK, int(self._rate * float(ring_seconds)))
        self._ring = np.zeros(capacity, dtype=np.float32)
        self._pos = 0
        self._filled = 0
        self._lock = threading.Lock()
        # _stream / _out_stream は別スレッドからの参照・差し替えが
        # 重なる（benign race）。参照代入は不可分で、古い参照を
        # 読んでも次回に直るだけのため Lock は入れない。特に
        # PortAudio コールバック内では待たせないことが優先。
        self._stream: Any = None
        self._input_name = ""
        self._factory = input_factory
        self._factory_out = output_factory
        self._monitor_volume = 0.8
        self._out_stream: Any = None
        self._out_device = ""
        self._pipe: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)

    def openInput(self, device: str | int | None) -> bool:
        """入力を開く。既に開いていれば閉じてから開き直す。成否を返す。"""
        self.close()
        # factory 注入時（テスト）は実バックエンドの有無を問わない。
        # PortAudio なしOSでも合成ストリームで検証できるよう gate を抜ける。
        if self._factory is None:
            sd = _import_sounddevice()
            if sd is None:
                logger.debug("音声入力を開けません（バックエンドなし）")
                return False
        else:
            sd = None
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
                assert sd is not None  # factory なしは gate 通過済み
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
        # _out_stream の参照は Lock なしで読む（benign race）。
        # コールバック内で待つと音が途切れるため、無ければ捨てるだけ。
        if self._out_stream is not None:
            try:
                self._pipe.put_nowait(mono.copy())
            except queue.Full:
                try:
                    self._pipe.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._pipe.put_nowait(mono.copy())
                except queue.Full:
                    pass

    def isOpened(self) -> bool:
        return self._stream is not None

    def selected_input(self) -> str:
        return self._input_name

    def close(self) -> None:
        self._stop_output()
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
            if (start < end) or (self._filled < self._ring.size and start == 0):
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

    # -- モニター再生 ---------------------------------------------------
    def setMonitorVolume(self, volume: float) -> None:
        try:
            v = float(volume)
        except (TypeError, ValueError):
            return
        self._monitor_volume = min(1.0, max(0.0, v))

    def isMonitorEnabled(self) -> bool:
        return self._out_stream is not None

    def setMonitorEnabled(self, on: bool, device: str | int | None = None) -> bool:
        """モニター再生のON/OFF。ON時は出力ストリームを開く。成否を返す。"""
        if not on:
            self._stop_output()
            return True
        if self._stream is None:
            print("モニターを開始できません: 音声入力が開いていません")
            logger.warning("モニター開始に失敗（入力未open）")
            return False
        # factory 注入時（テスト）は実バックエンドの有無を問わない。
        if self._factory_out is None:
            sd = _import_sounddevice()
            if sd is None:
                logger.debug("モニター出力を開けません（バックエンドなし）")
                return False
        else:
            sd = None
        self._stop_output()
        try:
            if self._factory_out is not None:
                out = self._factory_out(
                    samplerate=self._rate,
                    channels=AUDIO_CHANNELS,
                    blocksize=AUDIO_CHUNK,
                    device=device,
                    callback=self._on_output,
                )
            else:
                assert sd is not None  # factory なしは gate 通過済み
                out = sd.OutputStream(
                    samplerate=self._rate,
                    channels=AUDIO_CHANNELS,
                    dtype="float32",
                    blocksize=AUDIO_CHUNK,
                    device=device,
                    callback=self._on_output,
                )
            out.start()
        except Exception as e:
            logger.error(f"モニター出力を開けません: {e}")
            return False
        self._out_stream = out
        return True

    def _stop_output(self) -> None:
        out, self._out_stream = self._out_stream, None
        if out is None:
            return
        try:
            out.stop()
        except Exception:
            pass
        try:
            out.close()
        except Exception as e:
            logger.warning(f"モニター出力の解放で例外: {e}")

    def _on_output(self, outdata: Any, frames: int, _time: Any, _status: Any) -> None:
        """出力コールバック。pipe の最新を音量つきで書く。無ければ無音。"""
        try:
            data = self._pipe.get_nowait()
        except queue.Empty:
            data = None
        buf = np.asarray(outdata)
        if data is None:
            buf[:] = 0
            return
        mono = np.asarray(data, dtype=np.float32).ravel()
        if mono.size >= frames:
            chunk = mono[-frames:]
        else:
            chunk = np.zeros(frames, dtype=np.float32)
            chunk[-mono.size :] = mono
        shaped = np.tile(chunk.reshape(-1, 1), (1, AUDIO_CHANNELS))
        buf[:] = apply_volume(shaped, self._monitor_volume)
