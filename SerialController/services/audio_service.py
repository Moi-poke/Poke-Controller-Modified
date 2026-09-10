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
    device_entries,
    display_entries,
    display_for,
    format_display,
    guess_capture_input,
    probe_details,
)
from loguru import logger


class AudioService:
    """音声の取り込み口（AudioCapture）とその周辺の所有者。"""

    def __init__(self, notify_user: Callable[[str], None]) -> None:
        self._notify = notify_user
        self.capture: Any = AudioCapture()
        # 試し開きの結果（(番号, 名前, 推定ms)）。裏スレッドが埋める。
        # 空の間は display_* が速い列挙（推定なし）で代用する。
        self._probe_cache: dict[bool, list[tuple[int, str, float]]] = {}

    # -- 選択 -----------------------------------------------------------

    @staticmethod
    def list_inputs() -> list[str]:
        """入力の表示名（"番号: 名前"）。速い列挙で即返す。"""
        return display_entries(device_entries(True))

    @staticmethod
    def list_outputs() -> list[str]:
        """出力の表示名（"番号: 名前"）。速い列挙で即返す。"""
        return display_entries(device_entries(False))

    @staticmethod
    def probe_inputs() -> list[str]:
        """開ける入力の表示名だけ。試し開きするため裏で回すこと。"""
        return [
            format_display(index, name, est) for index, name, est in probe_details(True)
        ]

    @staticmethod
    def probe_outputs() -> list[str]:
        """開ける出力の表示名だけ。試し開きするため裏で回すこと。"""
        return [
            format_display(index, name, est)
            for index, name, est in probe_details(False)
        ]

    def refresh_probe_cache(self) -> None:
        """試し開きの結果を覚える（推定遅延つき）。裏スレッドから呼ぶ。"""
        try:
            self._probe_cache = {
                True: probe_details(True),
                False: probe_details(False),
            }
        except Exception as e:
            logger.warning(f"音声デバイスの絞り込みに失敗しました: {e}")
            self._probe_cache = {}

    def cached_inputs(self) -> list[str]:
        """絞り込み済みの入力表示名。未完了なら速い列挙で代用する。"""
        cached = self._probe_cache.get(True)
        if not cached:
            return self.list_inputs()
        return [format_display(i, n, e) for i, n, e in cached]

    def cached_outputs(self) -> list[str]:
        """絞り込み済みの出力表示名。未完了なら速い列挙で代用する。"""
        cached = self._probe_cache.get(False)
        if not cached:
            return self.list_outputs()
        return [format_display(i, n, e) for i, n, e in cached]

    def display_input(self, spec: str) -> str:
        """設定値に対応する入力の表示名（推定があれば付ける）。"""
        return self._display(True, spec)

    def display_output(self, spec: str) -> str:
        """設定値に対応する出力の表示名（推定があれば付ける）。"""
        return self._display(False, spec)

    def _display(self, want_input: bool, spec: str) -> str:
        text = str(spec or "").strip()
        if not text:
            return ""
        for index, name, est in self._probe_cache.get(want_input, []):
            if text == str(index) or text == name:
                return format_display(index, name, est)
        return display_for(want_input, spec)

    def pair_est(self, in_spec: str, out_spec: str) -> tuple[float, float]:
        """入出力の推定遅延ms。不明は -1。ジッタ滞留は含まない。"""
        return (self._est_for(True, in_spec), self._est_for(False, out_spec))

    def auto_input(self, camera_name: str) -> str:
        """取込口の番号（文字列）。ゲーム音はキャプチャボードからしか
        取れないため、カメラ名から推定する。分からなければ空（既定）。"""
        try:
            found = guess_capture_input(camera_name, device_entries(True))
        except Exception:
            return ""
        return "" if found is None else str(found)

    def _est_for(self, want_input: bool, spec: str) -> float:
        text = str(spec or "").strip()
        if not text:
            return -1.0
        for index, name, est in self._probe_cache.get(want_input, []):
            if text == str(index) or text == name:
                return float(est)
        return -1.0

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
