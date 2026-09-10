#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CommandAudio.py - 音声検知 API（AudioMixin）.

VisionMixin の音声版。待ち系（wait / _deadline）は継承側
（OperateMixin 経由の PythonCommand）が用意する。検知の本体は
core/audio_dsp.py の純粋関数で、ここは窓の取得と待ち回しだけを持つ。
"""

from __future__ import annotations

import os
import time
import traceback
import wave
from collections.abc import Callable
from os import path
from typing import Any

import numpy as np
from core import audio_dsp
from loguru import logger

# テンプレートwavの既定の置き場所（画像の Template/ に対応）。
# SerialController/Template/audio/<pack>/*.wav の規約。
TEMPLATE_AUDIO_PATH = path.normpath(
    path.join(path.dirname(path.dirname(path.abspath(__file__))), "Template", "audio")
)


# 録音クリップの既定保存先（画像の Captures/ に対応）。
# cwd 相対にしない。WindowUtils.APP_DIR と同じ場所を __file__ から
# 求める（core から WindowUtils は import 禁止のため）。
def default_clip_dir() -> str:
    """録音クリップの既定保存先（APP_DIR 起点）。"""
    return path.normpath(
        path.join(path.dirname(path.dirname(path.abspath(__file__))), "AudioClips")
    )


AUDIO_CLIP_DIR = default_clip_dir()


def _get_audio_filespec(audio_path: str) -> str:
    """テンプレートwavのパスを取得する。絶対パスならそのまま返す。"""
    if path.isabs(audio_path):
        return path.normpath(audio_path)
    return path.normpath(path.join(TEMPLATE_AUDIO_PATH, audio_path))


class AudioMixin:
    """音声検知の API をまとめた Mixin（単体では使わない）。"""

    wait: Callable[[float], None]
    _deadline: Callable[[float], Callable[[], bool]]

    def _initAudio(self, audio: Any) -> None:
        """検知に使う音声源を受け取る。継承側の __init__ から呼ぶ。"""
        self.audio: Any = audio
        self._sound_triggers: list[dict[str, Any]] = []
        # 取込レートと検知レートの乖離はここで気づく。
        # AudioCapture の既定は audio_dsp.SAMPLE_RATE 起点だが、
        # 別レートで開かれた源を渡されると検知の前提が崩れる。
        try:
            rate = getattr(audio, "_rate", None)
        except Exception:
            rate = None
        if rate is not None and int(rate) != audio_dsp.SAMPLE_RATE:
            logger.warning(
                f"音声源のレートが検知の前提と違います: {rate} "
                f"(想定 {audio_dsp.SAMPLE_RATE})"
            )

    def _readWindowOrRaise(self, seconds: float) -> np.ndarray:
        """直近の録音窓を返す。取れなければ RuntimeError にする。"""
        audio = getattr(self, "audio", None)
        if audio is None:
            raise RuntimeError("音声源が割り当てられていません。")
        window = audio.readWindow(seconds)
        if window is None or getattr(window, "size", 0) == 0:
            raise RuntimeError("音声を取得できません（未接続 / デバイス停止）。")
        return window

    def isTonePresent(
        self,
        bands: list[tuple[float, float]],
        thresholds: list[float],
        window_s: float = 1.5,
    ) -> bool:
        """指定帯域がすべて閾値を超えたら True。"""
        window = self._readWindowOrRaise(window_s)
        return audio_dsp.is_tone(window, audio_dsp.SAMPLE_RATE, bands, thresholds)

    def waitTone(
        self,
        bands: list[tuple[float, float]],
        thresholds: list[float],
        window_s: float = 1.5,
        timeout: float = 10.0,
        interval: float = 0.2,
    ) -> bool:
        """トーンが鳴るまで待つ。鳴れば True、時間切れは False。"""
        expired = self._deadline(timeout)
        while True:
            if self.isTonePresent(bands, thresholds, window_s):
                return True
            if expired():
                logger.debug("waitTone timeout")
                return False
            self.wait(interval)

    def _scoreSound(
        self, window: np.ndarray, template: np.ndarray, template_rate: int
    ) -> float:
        """録音窓と読み済みテンプレートの一致度（poll 内の再読込を避ける）。"""
        return audio_dsp.match_template(
            window, audio_dsp.SAMPLE_RATE, template, template_rate
        )

    def isSoundPresent(
        self,
        template_wav: str,
        threshold: float = 0.8,
        window_s: float = 3.0,
    ) -> bool:
        """登録音に近ければ True（正規化相互相関）。"""
        filespec = _get_audio_filespec(template_wav)
        try:
            template, rate = audio_dsp.load_wav_mono(filespec)
        except ValueError as e:
            raise RuntimeError(str(e)) from e
        window = self._readWindowOrRaise(window_s)
        score = self._scoreSound(window, template, rate)
        return bool(score >= float(threshold))

    def waitSound(
        self,
        template_wav: str,
        threshold: float = 0.8,
        window_s: float = 3.0,
        timeout: float = 10.0,
        interval: float = 0.2,
    ) -> bool:
        """登録音が鳴るまで待つ。鳴れば True、時間切れは False。"""
        filespec = _get_audio_filespec(template_wav)
        try:
            template, rate = audio_dsp.load_wav_mono(filespec)
        except ValueError as e:
            raise RuntimeError(str(e)) from e
        expired = self._deadline(timeout)
        while True:
            window = self._readWindowOrRaise(window_s)
            if bool(self._scoreSound(window, template, rate) >= float(threshold)):
                return True
            if expired():
                logger.debug(f"waitSound timeout: {template_wav}")
                return False
            self.wait(interval)

    def recordClip(
        self, seconds: float, name: str = "clip", clip_dir: str | None = None
    ) -> str:
        """直近 seconds 秒をwav保存し、保存先パスを返す（saveFrame 対応）。"""
        window = self._readWindowOrRaise(seconds)
        target = clip_dir or default_clip_dir()
        os.makedirs(target, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        stamp += f"_{int((time.time() % 1) * 1000):03d}"
        safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(name))
        filespec = path.join(target, f"{stamp}_{safe}.wav")
        pcm = np.clip(window.astype(np.float64), -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
        try:
            with wave.open(filespec, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(audio_dsp.SAMPLE_RATE)
                wf.writeframes(pcm.tobytes())
        except OSError as e:
            logger.warning(f"録音を保存できませんでした: {filespec} ({e})")
            return ""
        return filespec

    def recordClipTo(self, filespec: str, source: np.ndarray, rate: int) -> str:
        """検証・変換用に任意波形をwav保存する（テストの往復用）。"""
        data = np.asarray(source, dtype=np.float32)
        if int(rate) != audio_dsp.SAMPLE_RATE:
            from scipy import signal as _signal

            count = max(1, int(round(data.size * audio_dsp.SAMPLE_RATE / rate)))
            data = _signal.resample(data.astype(np.float64), count).astype(np.float32)
        directory = path.dirname(filespec)
        if directory:
            os.makedirs(directory, exist_ok=True)
        pcm = (np.clip(data.astype(np.float64), -1.0, 1.0) * 32767.0).astype(np.int16)
        with wave.open(filespec, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(audio_dsp.SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        return filespec

    def onSoundDetected(
        self,
        kind: str,
        callback: Callable[[str], None],
        cooldown_s: float = 5.0,
        **params: Any,
    ) -> None:
        """検知時コールバックを登録する。駆動は watchSounds が行う。

        kind は "tone"（params: bands/thresholds/window_s）か
        "sound"（params: template_wav/threshold/window_s）。
        """
        if kind not in ("tone", "sound"):
            raise ValueError(f"kind は tone/sound です: {kind}")
        if kind == "tone":
            if "bands" not in params or "thresholds" not in params:
                raise ValueError("tone には bands/thresholds が必要です")
        else:
            if "template_wav" not in params:
                raise ValueError("sound には template_wav が必要です")
        self._sound_triggers.append(
            {
                "kind": kind,
                "callback": callback,
                "params": params,
                "cooldown": float(cooldown_s),
                "last": 0.0,
            }
        )

    def watchSounds(self, timeout: float = 10.0, interval: float = 0.2) -> bool:
        """登録コールバックを回す。1件でも発火したら True、時間切れは False。"""
        expired = self._deadline(timeout)
        while True:
            now = time.monotonic()
            for trigger in list(self._sound_triggers):
                if now - float(trigger["last"]) < float(trigger["cooldown"]):
                    continue
                hit = self._matchTrigger(trigger)
                if hit:
                    trigger["last"] = now
                    try:
                        trigger["callback"](str(trigger["kind"]))
                    except Exception:
                        logger.error(
                            f"音声コールバックで例外: {traceback.format_exc()}"
                        )
                    return True
            if expired():
                return False
            self.wait(interval)

    def _matchTrigger(self, trigger: dict[str, Any]) -> bool:
        params = trigger["params"]
        if trigger["kind"] == "tone":
            return self.isTonePresent(
                params["bands"],
                params["thresholds"],
                float(params.get("window_s", 1.5)),
            )
        return self.isSoundPresent(
            params["template_wav"],
            float(params.get("threshold", 0.8)),
            float(params.get("window_s", 3.0)),
        )

    def _notifySound(self, label: str) -> None:
        """検知のDiscord通知。通知先が無ければログだけ残す。"""
        discord = getattr(self, "Discord", None)
        if discord is None:
            logger.info(f"音声を検知: {label}")
            return
        try:
            discord.send_message(index=0, content=label, without_image=True)
        except Exception:
            logger.error(f"音声検知の通知に失敗: {traceback.format_exc()}")
