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
    AUDIO_RATE,
    AudioCapture,
    audio_available,
    close_raw_output,
    device_entries,
    display_entries,
    display_for,
    format_display,
    open_raw_output,
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
        # 実測の記録（出力番号 -> 中央値ms）。計測のたびに上書きする。
        self.measured: dict[int, float] = {}

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

    def fastest_output(self) -> str:
        """最も速い出力の番号（文字列）。実測優先、なければ推定。無ければ空。"""
        cached = self._probe_cache.get(False, [])
        best = ""
        best_score = float("inf")
        for index, _name, est in cached:
            score = self.measured.get(index, est if est >= 0 else float("inf"))
            if score < best_score:
                best_score = score
                best = str(index)
        return best

    def measure_latency(self, out_spec: str) -> dict[str, Any]:
        """選択中の出力の往復遅延を実測する。重いので裏で呼ぶこと。

        入力は現在開いている物を使い、出力へ計測ブリップを鳴らして
        戻りを検出する。モニター再生中は回り込みで誤検出するため拒む。
        """
        from core import audio_latency as AL

        capture = self.capture
        if capture is None or not capture.isOpened():
            return {
                "detected": 0,
                "total": 0,
                "median_ms": -1.0,
                "error": "音声入力が開いていません",
            }
        try:
            monitoring = bool(capture.isMonitorEnabled())
        except Exception:
            monitoring = False
        if monitoring:
            return {
                "detected": 0,
                "total": 0,
                "median_ms": -1.0,
                "error": "モニター再生を止めてから計測してください",
            }
        try:
            out, rate, latency = open_raw_output(out_spec or None)
        except Exception as e:
            return {
                "detected": 0,
                "total": 0,
                "median_ms": -1.0,
                "error": f"出力を開けません: {e}",
            }
        # 発射器の給電コールバックで開き直す。blocking write では
        # 返りのタイミングが振れて中央値が暴れるため使わない。
        close_raw_output(out)
        try:
            needle44 = AL.make_chirp(AUDIO_RATE)
            emitter = AL.Emitter(AL.to_device_chirp(needle44, rate), rate, latency)
            out, rate, latency = open_raw_output(
                out_spec or None, callback=emitter.callback
            )

            def wait_capture(seconds: float) -> Any:
                return capture.read_stamped(seconds)

            delays = self._collect_delays(capture, emitter, needle44, wait_capture)
            result = AL.summarize(delays)
            result["delays_ms"] = [round(v, 1) for v in delays]
            result["out_underflow"] = int(emitter.underruns)
        except Exception as e:
            logger.error(f"遅延計測に失敗しました: {e}")
            result = {
                "detected": 0,
                "total": 0,
                "median_ms": -1.0,
                "error": f"計測に失敗しました: {e}",
            }
        finally:
            close_raw_output(out)
        try:
            index = self._measured_index(out_spec)
            if index is not None and result.get("median_ms", -1.0) >= 0:
                self.measured[index] = float(result["median_ms"])
        except Exception:
            pass
        return result

    @staticmethod
    def _collect_delays(
        capture: Any, emitter: Any, needle44: Any, wait_capture: Any
    ) -> list[float]:
        """発射と検出を集めて時刻近接で組にする。

        全発射が終わり、戻りが途絶えるまで集める。組自体は
        pair_delays に任せ、見逃し・誤検出に引きずられない。
        """
        import time as _time

        from core import audio_latency as AL

        found_caps: list[float] = []
        search_from = 0
        deadline = _time.monotonic() + 25.0
        quiet_from: float | None = None
        while _time.monotonic() < deadline:
            _time.sleep(0.2)
            try:
                stamped = wait_capture(2.5)
            except Exception:
                continue
            if stamped is None:
                continue
            window, total, tap, in_latency, in_rate = stamped
            base = total - window.size
            advanced = False
            while True:
                at = AL.find_impulse(window, needle44, start=max(0, search_from - base))
                if at is None:
                    break
                absolute = base + at
                search_from = absolute + needle44.size
                got_at = AL.locate_played(tap, absolute, in_latency, in_rate)
                if got_at is None:
                    break
                found_caps.append(got_at)
                advanced = True
            if emitter.done:
                if advanced:
                    quiet_from = None
                elif quiet_from is None:
                    quiet_from = _time.monotonic()
                elif _time.monotonic() - quiet_from >= 2.0:
                    break
        return AL.pair_delays(emitter.play_times, found_caps)

    def _measured_index(self, out_spec: str) -> int | None:
        """実測の記録先の出力番号。分からなければ None。"""
        from core.AudioCapture import parse_display

        text = str(out_spec or "").strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        for index, name, _est in self._probe_cache.get(False, []):
            if text == name or text == f"{index}: {name}":
                return index
        parsed = parse_display(text)
        return parsed

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
