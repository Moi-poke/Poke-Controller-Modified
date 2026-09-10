#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_panel.py - 音声枠の組み立てと音声操作を受け持つMixin.

PokeControllerApp に混ぜて使う（多重継承）。camera_panel.py と同じ
流儀で、触る属性は下に宣言しておく（mypy のため）。
"""

from __future__ import annotations

import datetime
import os
import tkinter as tk
import tkinter.ttk as ttk
import wave
from typing import Any

import numpy as np
from loguru import logger

METER_INTERVAL_MS = 200


class AudioPanelMixin:
    """音声パネルMixin。単体では使わない。"""

    frame_1: Any
    root: Any
    settings: Any
    audio_service: Any
    audio_lf: Any
    audio_input_cb: Any
    audio_output_cb: Any
    audio_input_name: Any
    audio_output_name: Any
    audio_monitor: Any
    audio_volume: Any
    audio_level: Any
    audio_reload_button: Any
    audio_record_button: Any
    _audio_meter_after_id: Any
    _on_setting_changed: Any

    def _build_audio_frame(self) -> None:
        self.audio_lf = ttk.Labelframe(self.frame_1, text="Audio")
        self.audio_input_name = tk.StringVar()
        self.audio_output_name = tk.StringVar()
        self.audio_monitor = tk.BooleanVar()
        self.audio_volume = tk.DoubleVar()
        self.audio_level = tk.StringVar(value="--")

        ttk.Label(self.audio_lf, text="Input:").grid(padx="5", row=0, column=0)
        self.audio_input_cb = ttk.Combobox(
            self.audio_lf, textvariable=self.audio_input_name, width=28
        )
        self.audio_input_cb.grid(padx="5", row=0, column=1, sticky="ew")
        self.audio_input_cb.bind(
            "<<ComboboxSelected>>", self._onAudioInputSelected, add=""
        )

        ttk.Label(self.audio_lf, text="Output:").grid(padx="5", row=0, column=2)
        self.audio_output_cb = ttk.Combobox(
            self.audio_lf, textvariable=self.audio_output_name, width=28
        )
        self.audio_output_cb.grid(padx="5", row=0, column=3, sticky="ew")

        ttk.Checkbutton(
            self.audio_lf,
            text="Monitor",
            variable=self.audio_monitor,
            command=self._onMonitorToggled,
        ).grid(padx="5", row=0, column=4)

        self.audio_reload_button = ttk.Button(
            self.audio_lf, text="Reload Audio", command=self.reloadAudio
        )
        self.audio_reload_button.grid(padx="5", row=0, column=5)

        ttk.Label(self.audio_lf, text="Level:").grid(padx="5", row=1, column=0)
        ttk.Label(self.audio_lf, textvariable=self.audio_level).grid(
            row=1, column=1, sticky="w"
        )
        self.audio_record_button = ttk.Button(
            self.audio_lf, text="Test Rec 3s", command=self.recordAudioTest
        )
        self.audio_record_button.grid(padx="5", row=1, column=5)

        # 明示rowなし（tk自動配置）。camera枠の次へ置かれる。
        self.audio_lf.grid(columnspan=3, padx="5", sticky="ew")

    def _refreshAudioDevices(self) -> None:
        """入出力の候補を列挙して流し込む。失敗時は空のまま。"""
        try:
            inputs = self.audio_service.list_inputs()
        except Exception as e:
            logger.warning(f"音声入力の列挙に失敗しました: {e}")
            inputs = []
        try:
            outputs = self.audio_service.list_outputs()
        except Exception as e:
            logger.warning(f"音声出力の列挙に失敗しました: {e}")
            outputs = []
        self.audio_input_cb["values"] = inputs
        self.audio_output_cb["values"] = outputs

    def _start_audio(self) -> None:
        """入力を開き、候補・メーターを回し始める。失敗はログだけ。"""
        self._refreshAudioDevices()
        name = self.settings.audio_input.get()
        if not self.audio_service.open(name):
            print(f"音声入力を開けませんでした: {name or '既定の入力'}")
        self._apply_audio_widgets()
        self._schedule_meter()

    def _apply_audio_widgets(self) -> None:
        """設定値を画面へ流し込む（起動時と設定読込後のみ）。"""
        self.audio_input_name.set(self.settings.audio_input.get())
        self.audio_output_name.set(self.settings.audio_output.get())
        self.audio_monitor.set(self.settings.audio_monitor_enabled.get())
        self.audio_volume.set(self.settings.audio_monitor_volume.get())

    def _onAudioInputSelected(self, *event: Any) -> None:
        name = self.audio_input_name.get()
        if self.audio_service.reopen(name):
            self.settings.audio_input.set(name)
            self._on_setting_changed()
        else:
            print(f"音声入力を開けませんでした: {name}")

    def _onMonitorToggled(self, *event: Any) -> None:
        on = bool(self.audio_monitor.get())
        out = self.audio_output_name.get()
        vol = float(self.audio_volume.get())
        if self.audio_service.set_monitor(on, out, vol):
            self.settings.audio_monitor_enabled.set(on)
            self.settings.audio_output.set(out)
            self.settings.audio_monitor_volume.set(vol)
            self._on_setting_changed()
        else:
            # 失敗時は表示を実態へ戻す（ONに見えたままにしない）
            self.audio_monitor.set(False)

    def _schedule_meter(self) -> None:
        self._stop_meter()
        self._audio_meter_after_id = self.root.after(
            METER_INTERVAL_MS, self._update_meter
        )

    def _stop_meter(self) -> None:
        after_id = getattr(self, "_audio_meter_after_id", None)
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except (tk.TclError, ValueError):
            pass
        self._audio_meter_after_id = None

    def _update_meter(self) -> None:
        try:
            capture = self.audio_service.capture
            peak = capture.peak() if capture is not None else 0.0
            self.audio_level.set(f"{peak:.2f}")
        except Exception as e:
            logger.debug(f"レベル表示を更新できません: {e}")
        finally:
            self._schedule_meter()

    def reloadAudio(self) -> None:
        """入力を開き直す。"""
        self._refreshAudioDevices()
        self._onAudioInputSelected()

    def recordAudioTest(self) -> None:
        """3秒録って保存先を知らせる（検知の疎通確認用）。"""
        capture = self.audio_service.capture
        if capture is None or not capture.isOpened():
            print("録音できません: 音声入力が開いていません")
            return
        window = capture.record(3.0)
        if window.size == 0:
            print("録音できませんでした（詳細はターミナルのログ）")
            return
        # 保存は Mixin の手順に寄せず、ここでは生pcmをwavへ書くだけ。
        # 検知用テンプレ化は recordClip（コマンド側）を使う。
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filespec = f"./AudioClips/test_{stamp}.wav"
        try:
            os.makedirs("./AudioClips", exist_ok=True)
            pcm = (np.clip(window.astype(np.float64), -1.0, 1.0) * 32767.0).astype(
                np.int16
            )
            with wave.open(filespec, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(pcm.tobytes())
        except OSError as e:
            print(f"録音の保存に失敗しました: {e}")
            return
        print(f"テスト録音を保存しました: {filespec}")
