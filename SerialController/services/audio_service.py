#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_service.py - 音声入出力の所有と接続手順を1つに持つ層.

serial_service.py の音声版。Window に残すのは tk 変数の読み書き・
確認ダイアログ・見た目の反映だけ。ここは tkinter を触らず、
設定値は通常の Python 値で受け取り、利用者への通知は
notify_user（Window は print を渡す）へ出す。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.AudioCapture import (
    AudioCapture,
    audio_available,
    list_input_devices,
    list_output_devices,
)
from loguru import logger


class AudioService:
    """音声の取り込み口（AudioCapture）とその周辺の所有者。"""

    def __init__(self, notify_user: Callable[[str], None]) -> None:
        self._notify = notify_user
        self.capture: Any = AudioCapture()

    # -- 選択 -----------------------------------------------------------

    @staticmethod
    def list_inputs() -> list[str]:
        return list_input_devices()

    @staticmethod
    def list_outputs() -> list[str]:
        return list_output_devices()

    # -- 接続 -----------------------------------------------------------

    def open(self, name: str) -> bool:
        """入力を開く。失敗は False＋利用者向け1行＋ログ。"""
        if self.capture.openInput(name or None):
            return True
        # バックエンド欠如は静かに（debug）。実デバイス失敗のみ通知する。
        if isinstance(self.capture, AudioCapture):
            try:
                factory = getattr(self.capture, "_factory", None)
            except Exception:
                factory = None
            if factory is None and not audio_available():
                logger.debug("音声バックエンドがないため取込なしで起動します")
                return False
        message = f"音声入力を開けません: {name or '既定の入力'}"
        self._notify(message)
        logger.error(message)
        return False

    def reopen(self, name: str) -> bool:
        """選び直し。open と同じ（開き直しは openInput が面倒を見る）。"""
        return self.open(name)

    def close(self) -> None:
        self.capture.close()

    def shutdown(self) -> bool:
        """終了時の片付け。その場で閉じきれたら True。"""
        try:
            self.capture.close()
        except Exception as e:
            logger.warning(f"音声の終了処理で例外: {e}")
            return False
        return True

    def set_monitor(self, on: bool, out_name: str, volume: float) -> bool:
        """モニター再生のON/OFF。入力未openではONにしない。"""
        if on and not self.capture.isOpened():
            message = "モニターを開始できません: 音声入力が開いていません"
            self._notify(message)
            logger.warning(message)
            return False
        try:
            self.capture.setMonitorVolume(volume)
        except Exception as e:
            logger.warning(f"音量の反映に失敗しました: {e}")
        if self.capture.setMonitorEnabled(on, out_name or None):
            return True
        message = "モニター出力を開けません"
        self._notify(message)
        logger.error(message)
        return False
