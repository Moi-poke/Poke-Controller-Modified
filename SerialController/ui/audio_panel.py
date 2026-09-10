#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audio_panel.py - 音声枠の組み立てと音声操作を受け持つMixin.

PokeControllerApp に混ぜて使う（多重継承）。camera_panel.py と同じ
流儀で、触る属性は下に宣言しておく（mypy のため）。
"""

from __future__ import annotations

import datetime
import os
import threading
import tkinter as tk
import tkinter.ttk as ttk
import wave
from typing import Any

import WindowUtils
import numpy as np
from core import audio_dsp
from core.AudioCapture import audio_available, parse_display
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
    audio_latency: Any
    audio_reload_button: Any
    audio_record_button: Any
    _audio_meter_after_id: Any
    _probe_thread: Any
    _probe_done: bool
    _probe_after_id: Any
    _on_setting_changed: Any

    def _build_audio_frame(self) -> None:
        self.audio_lf = ttk.Labelframe(self.frame_1, text="Audio")
        self.audio_input_name = tk.StringVar()
        self.audio_output_name = tk.StringVar()
        self.audio_monitor = tk.BooleanVar()
        self.audio_volume = tk.DoubleVar()
        self.audio_level = tk.StringVar(value="--")
        self.audio_latency = tk.StringVar(value="推定 --")

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
        self.audio_output_cb.bind(
            "<<ComboboxSelected>>", self._onAudioOutputSelected, add=""
        )

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
        ttk.Label(self.audio_lf, textvariable=self.audio_latency).grid(
            row=1, column=2, columnspan=2, sticky="w"
        )
        self.audio_record_button = ttk.Button(
            self.audio_lf, text="Test Rec 3s", command=self.recordAudioTest
        )
        self.audio_record_button.grid(padx="5", row=1, column=5)

        # 配置は明示rowで固定する。row省略の自動配置はgridした時点で
        # 空いている行へ置かれるため、後に明示配置されるSerial/Command枠の
        # 下敷きになる（row=1へ入り込んで隠れる）。兄弟枠と同じ流儀にする。
        self.audio_lf.grid(columnspan=3, padx="5", row=3, sticky="ew")

    def _refreshAudioDevices(self) -> None:
        """入出力の候補を流し込む。速い列挙を即出しし、開ける物だけ裏で絞る。

        試し開き（75台で数秒）で起動を止めないよう、裏スレッドで絞って
        GUI スレッドの poll で受け取る。スレッドから widget は触らない。
        """
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
        self._start_probe()

    def _start_probe(self) -> None:
        """開けるデバイスの絞り込みを裏で始める。多重起動しない。"""
        thread = getattr(self, "_probe_thread", None)
        if thread is not None and thread.is_alive():
            return
        self._probe_done = False

        def work() -> None:
            self.audio_service.refresh_probe_cache()
            self._probe_done = True

        self._probe_thread = threading.Thread(
            target=work, name="AudioProbe", daemon=True
        )
        self._probe_thread.start()
        self._poll_probe()

    def _poll_probe(self) -> None:
        """絞り込みの完了を GUI スレッドで待つ。終われば候補を置き換える。"""
        try:
            self._probe_after_id = self.root.after(500, self._poll_probe)
        except tk.TclError:
            return
        if not getattr(self, "_probe_done", False):
            return
        try:
            self.root.after_cancel(self._probe_after_id)
        except (tk.TclError, ValueError):
            pass
        self._probe_after_id = None
        try:
            inputs = self.audio_service.cached_inputs()
            outputs = self.audio_service.cached_outputs()
            if inputs:
                self.audio_input_cb["values"] = inputs
                self._restore_selection(
                    self.audio_input_cb,
                    self.audio_input_name,
                    self.settings.audio_input.get(),
                    inputs,
                )
            if outputs:
                self.audio_output_cb["values"] = outputs
                self._restore_selection(
                    self.audio_output_cb,
                    self.audio_output_name,
                    self.settings.audio_output.get(),
                    outputs,
                )
            self._update_latency_label()
        except tk.TclError:
            # 終了間際に発火した分。鎖は切れているので再予約しない。
            return

    @staticmethod
    def _restore_selection(
        combobox: Any, var: Any, spec: str, values: list[str]
    ) -> None:
        """絞り込み後も選んでいた物が残っていれば表示を寄せる。"""
        current = var.get()
        if current in values:
            return
        want = str(spec).strip()
        hit = ""
        for display in values:
            if display == want:
                hit = display
                break
            if want.isdigit() and parse_display(display) == int(want):
                hit = display
                break
        if not hit:
            return
        var.set(hit)
        try:
            combobox.set(hit)
        except tk.TclError:
            pass

    def _start_audio(self) -> None:
        """入力を開き、候補・メーターを回し始める。失敗はログだけ。"""
        self._refreshAudioDevices()
        name = self.settings.audio_input.get()
        if not self.audio_service.open(name):
            # バックエンド欠如は静かに（debug）。実デバイス失敗のprintは残す。
            try:
                missing = not audio_available() and (
                    getattr(self.audio_service.capture, "_factory", None) is None
                )
            except Exception:
                missing = False
            if missing:
                logger.debug("音声バックエンドがないため取込なしで起動します")
            else:
                print(f"音声入力を開けませんでした: {name or '既定の入力'}")
        self._apply_audio_widgets()
        self._schedule_meter()

    def _apply_audio_widgets(self) -> None:
        """設定値を画面へ流し込む（起動時と設定読込後のみ）。"""
        self.audio_input_name.set(
            self.audio_service.display_input(self.settings.audio_input.get())
        )
        self.audio_output_name.set(
            self.audio_service.display_output(self.settings.audio_output.get())
        )
        self.audio_monitor.set(self.settings.audio_monitor_enabled.get())
        self.audio_volume.set(self.settings.audio_monitor_volume.get())
        self._update_latency_label()

    def _update_latency_label(self) -> None:
        """選択中ペアの推定遅延を表示する。不明は -- のまま。"""
        try:
            est_in, est_out = self.audio_service.pair_est(
                self._selected_index(self.audio_input_name.get()),
                self._selected_index(self.audio_output_name.get()),
            )
        except Exception:
            return
        left = f"in {est_in:.0f}ms" if est_in >= 0 else "in --"
        right = f"out {est_out:.0f}ms" if est_out >= 0 else "out --"
        self.audio_latency.set(f"推定 {left}＋{right}")

    def _selected_index(self, display: str) -> str:
        """表示名（"番号: 名前"）から保存用の番号を取り出す。"""
        index = parse_display(display)
        return str(index) if index is not None else display

    def _onAudioInputSelected(self, *event: Any) -> None:
        display = self.audio_input_name.get()
        spec = self._selected_index(display)
        if self.audio_service.reopen(spec):
            self.settings.audio_input.set(spec)
            self._update_latency_label()
            self._on_setting_changed()
        else:
            print(f"音声入力を開けませんでした: {display}")

    def _onAudioOutputSelected(self, *event: Any) -> None:
        """再生中に出力を変えたら新デバイスで開き直す（失敗時は元に戻す）。"""
        capture = getattr(self.audio_service, "capture", None)
        if capture is None or not capture.isMonitorEnabled():
            return
        out = self.audio_output_name.get()
        try:
            vol = float(self.audio_volume.get())
        except (TypeError, ValueError):
            vol = 0.8
        previous = self.settings.audio_output.get()
        spec = self._selected_index(out)
        if self.audio_service.set_monitor(True, spec, vol):
            self.settings.audio_output.set(spec)
            self.settings.audio_monitor_volume.set(vol)
            self._update_latency_label()
            self._on_setting_changed()
        else:
            self.audio_output_name.set(self.audio_service.display_output(previous))

    def _onMonitorToggled(self, *event: Any) -> None:
        on = bool(self.audio_monitor.get())
        out = self.audio_output_name.get()
        vol = float(self.audio_volume.get())
        spec = self._selected_index(out)
        if self.audio_service.set_monitor(on, spec, vol):
            self.settings.audio_monitor_enabled.set(on)
            self.settings.audio_output.set(spec)
            self.settings.audio_monitor_volume.set(vol)
            self._update_latency_label()
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
            # 終了後に予約が残っていても再予約しない。
            # root 破棄後の after() は TclError になるため握る。
            try:
                self._schedule_meter()
            except tk.TclError:
                pass

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
        # 保存先は cwd 相対にしない（Window 起動時に chdir されても
        # ずれないよう APP_DIR 起点）。
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        clip_dir = os.path.join(WindowUtils.APP_DIR, "AudioClips")
        filespec = os.path.join(clip_dir, f"test_{stamp}.wav")
        try:
            os.makedirs(clip_dir, exist_ok=True)
            pcm = (np.clip(window.astype(np.float64), -1.0, 1.0) * 32767.0).astype(
                np.int16
            )
            with wave.open(filespec, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(audio_dsp.SAMPLE_RATE)
                wf.writeframes(pcm.tobytes())
        except OSError as e:
            print(f"録音の保存に失敗しました: {e}")
            return
        print(f"テスト録音を保存しました: {filespec}")
