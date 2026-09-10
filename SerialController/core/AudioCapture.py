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

from typing import Any

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
