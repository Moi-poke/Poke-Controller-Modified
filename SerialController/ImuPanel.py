#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ImuPanel.py - ジャイロ送出の操作面（BconSetupへ組み込む部品）。

BconSetupのcol_infoへLabelFrameとして組み込む。入力源の選択・波形の
設定・送出の開始/停止・統計の表示を担う。線の操作は作業スレッドで行い、
画面の更新だけafter()で戻す（BconSetupと同一規律）。

使い方:
    panel = ImuPanel(parent, transport_provider, settings_provider, log)
    panel.pack(fill=tk.X, pady=(0, 8))
    panel.close()  # 終了時に送出を止める
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

# 通常運用では使わない入力源。デバッグ時だけ選べる。
DEBUG_SOURCES = frozenset({"sine", "step", "rest", "manual"})

SOURCE_LABELS = (
    ("sensor", "パッドAPI"),
    ("hid", "HID直結"),
    ("sine", "SINE波形"),
    ("step", "STEP波形"),
    ("rest", "静止値"),
    ("manual", "手動入力"),
)

# PC側で重ねる反転の組み合わせ（FW側の変換を打ち消す実測用）。
# 値は (accel_signs, gyro_signs)。符号は1か-1のみ。
INVERT_LABELS = (
    ("none", "なし"),
    ("accel", "加速度"),
    ("gyro", "ジャイロ"),
    ("both", "両方"),
)
INVERT_SIGNS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "none": ((1, 1, 1), (1, 1, 1)),
    "accel": ((1, -1, -1), (1, 1, 1)),
    "gyro": ((1, 1, 1), (1, -1, -1)),
    "both": ((1, -1, -1), (1, -1, -1)),
}
# 設定ファイルの符号がプリセットのどれとも一致しないときの表示。
# この表示のまま送出を開始しても、符号を書き換えない。
CURRENT_INVERT_LABEL = "設定値のまま"

# ジャイロ出力倍率のプリセット（実機比較用）。
SCALE_PRESETS = ("1.0", "0.5", "0.25", "0.125", "0.0625")

# 送出中に毎サンプル読む加工値（倍率, 不感帯raw, 加速度符号, ジャイロ符号）。
_LiveTrim = tuple[float, float, tuple[int, int, int], tuple[int, int, int]]


class ImuPanel:
    """ジャイロ送出の操作面。"""

    def __init__(
        self,
        master: Any,
        transport_provider: Callable[[], Any],
        settings_provider: Callable[[], dict[str, str]],
        settings_writer: Callable[[dict[str, str]], None],
        log: Callable[[str], None],
    ) -> None:
        """transport_providerは送り先、settingsはIMU節の読み書き、logは行出し。"""
        self._transport_provider = transport_provider
        self._settings_provider = settings_provider
        self._settings_writer = settings_writer
        self._log = log
        self._queue: queue.Queue = queue.Queue()
        self._stop = False
        self._streaming = False
        # 開始/停止の作業スレッドが走っている間のフラグ。連打で
        # _do_start と _do_stop が並走しないようにする。
        self._starting = False
        self._stopping = False
        # 送出中に毎サンプル読む加工値（倍率, 不感帯raw, 加速度符号,
        # ジャイロ符号）。断面を不変タプルで持つので、GUIスレッドからの
        # 差し替えは次のサンプルから効く。
        self._live_trim: _LiveTrim | None = None
        self._sensor_reader: Any = None
        self._step_gen: Any = None
        # 直近1秒の最大振幅。表示は瞬時値のため、単発の跳び(spike)を
        # 見落とす。Switch側の誤動作切り分け用に統計行へ出す。
        self._peak_g = 0.0
        self._peak_a = 0.0
        self._acc_min: list[int | None] = [None, None, None]
        self._acc_max: list[int | None] = [None, None, None]
        # 送出レート表示用（前回の累計と時刻）。
        self._last_sent: int | None = None
        self._last_sent_t: float | None = None

        frame = ttk.LabelFrame(master, text="ジャイロ", padding=8)
        frame.columnconfigure(1, weight=1)
        self.frame = frame

        ttk.Label(frame, text="入力源").grid(
            row=0, column=0, sticky=tk.W, padx=2, pady=2
        )
        self._source_var = tk.StringVar(value="sensor")
        self._source_cb = ttk.Combobox(
            frame,
            width=10,
            state="readonly",
            textvariable=self._source_var,
            values=[label for _, label in SOURCE_LABELS],
        )
        self._source_cb.grid(row=0, column=1, sticky=tk.EW, padx=2, pady=2)
        self._btn_toggle = ttk.Button(frame, text="開始", command=self._on_toggle)
        self._btn_toggle.grid(row=0, column=2, sticky=tk.EW, padx=2, pady=2)

        # パッド番号は SDL センサー入力のときだけ意味がある。行ごと
        # 出し分け、ラベルで説明しない（レイアウトで示す）。
        self._pad_frame = ttk.Frame(frame)
        ttk.Label(self._pad_frame, text="パッド").grid(
            row=0, column=0, sticky=tk.W, padx=2, pady=2
        )
        self._pad_var = tk.StringVar(value="0")
        self._pad_cb = ttk.Combobox(
            self._pad_frame,
            width=10,
            state="readonly",
            textvariable=self._pad_var,
            values=["0", "1", "2", "3"],
        )
        self._pad_cb.grid(row=0, column=1, sticky=tk.EW, padx=2, pady=2)
        self._pad_frame.grid(row=1, column=0, columnspan=2, sticky=tk.EW)
        self._btn_step = ttk.Button(frame, text="STEP発火", command=self._on_step_fire)
        self._btn_step.grid(row=1, column=2, sticky=tk.EW, padx=2, pady=2)

        self._stats_var = tk.StringVar(value="停止中")
        ttk.Label(frame, textvariable=self._stats_var).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, padx=2, pady=2
        )
        self._invert_label = ttk.Label(frame, text="反転")
        self._invert_label.grid(row=3, column=0, sticky=tk.W, padx=2, pady=2)
        self._invert_var = tk.StringVar(value=CURRENT_INVERT_LABEL)
        self._invert_cb = ttk.Combobox(
            frame,
            width=10,
            state="readonly",
            textvariable=self._invert_var,
            values=[CURRENT_INVERT_LABEL, *(label for _, label in INVERT_LABELS)],
        )
        self._invert_cb.grid(row=3, column=1, sticky=tk.EW, padx=2, pady=2)
        self._invert_cb.bind("<<ComboboxSelected>>", self._on_invert_changed, add="+")
        self._scale_label = ttk.Label(frame, text="感度")
        self._scale_label.grid(row=4, column=0, sticky=tk.W, padx=2, pady=2)
        self._scale_var = tk.StringVar(value="1.0")
        self._scale_cb = ttk.Combobox(
            frame,
            width=10,
            state="readonly",
            textvariable=self._scale_var,
            values=list(SCALE_PRESETS),
        )
        self._scale_cb.grid(row=4, column=1, sticky=tk.EW, padx=2, pady=2)
        self._scale_cb.bind("<<ComboboxSelected>>", self._on_scale_changed, add="+")
        self._debug_var = tk.BooleanVar(value=False)
        self._debug_cb = ttk.Checkbutton(
            frame,
            text="デバッグ",
            variable=self._debug_var,
            command=self._on_debug_toggled,
        )
        self._debug_cb.grid(row=2, column=2, sticky=tk.EW, padx=2, pady=2)
        self._detail_var = tk.BooleanVar(value=False)
        self._detail_cb = ttk.Checkbutton(
            frame,
            text="詳細表示",
            variable=self._detail_var,
            command=self._on_detail_toggled,
        )
        self._detail_cb.grid(row=6, column=0, sticky=tk.W, padx=2, pady=2)
        # 手動入力: 6軸の生値を直接指定して送る。入力源が「手動入力」の
        # ときだけ表示する。値はtraceでメインスレッドから取り込み、
        # 送出スレッドはスナップショット(int6)だけを読む。
        self._manual_values = [0, 0, 4096, 0, 0, 0]
        self._manual_vars: list[tk.StringVar] = []
        self._manual_frame = ttk.LabelFrame(
            frame, text="手動入力（生値 LSB）", padding=4
        )
        for i, (name, dflt) in enumerate(
            zip(("AX", "AY", "AZ", "GX", "GY", "GZ"), self._manual_values)
        ):
            ttk.Label(self._manual_frame, text=name).grid(
                row=0, column=i * 2, sticky=tk.W, padx=(4 if i else 2, 0)
            )
            var = tk.StringVar(value=str(dflt))
            spin = ttk.Spinbox(
                self._manual_frame,
                from_=-32768,
                to=32767,
                width=7,
                textvariable=var,
            )
            spin.grid(row=0, column=i * 2 + 1, sticky=tk.W, padx=(0, 2))
            normalize = self._make_manual_normalizer(i, var)
            spin.bind("<FocusOut>", normalize, add="+")
            spin.bind("<Return>", normalize, add="+")
            var.trace_add("write", self._make_manual_trace(i, var))
            self._manual_vars.append(var)
        # 反転・感度の行（4）と重ねない。詳細表示（6）の手前へ置く。
        self._manual_frame.grid(
            row=5, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0)
        )
        self._manual_frame.grid_remove()
        self._source_cb.bind("<<ComboboxSelected>>", self._on_source_changed, add="")
        # 詳細の可視化パネル。チェックONのときだけ同frame内に展開する。
        # _apply_visibility() がデバッグOFFで畳むため、先に用意する。
        self._gyro_view: Any = None
        self._apply_visibility()
        self._load_settings_into_vars()
        # この部品は Window.loadSettings() より先に作られる。設定がまだ
        # 無い状態の既定値で送出開始→保存すると保存値を潰すため、
        # イベントループ開始後に読み直す。
        try:
            self.frame.after_idle(self._load_settings_into_vars)
        except Exception:
            pass
        self._poll()

    def pack(self, **kwargs: Any) -> None:
        """外枠を配置する。"""
        self.frame.pack(**kwargs)

    def _load_settings_into_vars(self) -> None:
        """設定値を画面へ反映する。読めなければ既定のまま。"""
        try:
            settings = self._settings_provider()
        except Exception:
            return
        try:
            source = str(settings.get("source", "sensor"))
            for name, label in SOURCE_LABELS:
                if name == source:
                    self._source_var.set(label)
                    break
            pad = int(settings.get("pad_index", "0"))
            if 0 <= pad <= 3:
                self._pad_var.set(str(pad))
            self._load_invert_from_settings(settings)
            scale = float(settings.get("gyro_scale", "1.0") or 1.0)
            self._scale_var.set(f"{scale:g}")
            for i, key in enumerate(
                ("acc_x", "acc_y", "acc_z", "manual_gx", "manual_gy", "manual_gz")
            ):
                try:
                    value = int(settings.get(key, "0"))
                except (TypeError, ValueError):
                    continue
                self._manual_vars[i].set(str(value))
                self._normalize_manual_value(i, self._manual_vars[i])
            if source in DEBUG_SOURCES:
                self._debug_var.set(True)
            self._refresh_source_choices()
            self._apply_visibility()
        except (TypeError, ValueError):
            pass

    def _make_manual_trace(
        self, index: int, var: tk.StringVar
    ) -> Callable[[str, str, str], None]:
        """手動入力欄の変更をintへ取り込むハンドラを作る（メインスレッド）。"""

        def _apply(*_args: str) -> None:
            try:
                value = int(var.get())
            except (TypeError, ValueError):
                return
            self._manual_values[index] = max(-32768, min(32767, value))

        return _apply

    def _normalize_manual_value(self, index: int, var: tk.StringVar) -> None:
        """表示中の値をint16へ丸め、送信値と画面表示を一致させる。"""
        try:
            value = int(var.get())
        except (TypeError, ValueError):
            # 数値として読めない文字列は直前の採用値へ戻す。
            value = self._manual_values[index]
        value = max(-32768, min(32767, value))
        self._manual_values[index] = value
        text = str(value)
        if var.get() != text:
            var.set(text)

    def _make_manual_normalizer(
        self, index: int, var: tk.StringVar
    ) -> Callable[[Any], None]:
        """SpinboxのFocusOut/Return用ハンドラを作る（Tkは引数1つだけ渡す）。"""

        def _normalize(_event: Any) -> None:
            self._normalize_manual_value(index, var)

        return _normalize

    def _normalize_manual_values(self) -> None:
        """手動入力の6軸すべてを丸める。送出開始の直前に呼ぶ。"""
        for i, var in enumerate(self._manual_vars):
            self._normalize_manual_value(i, var)

    def _sign_keys(self) -> tuple[str, str]:
        """選択中の入力源が読む符号キー。HID直結だけ hid_* を使う。"""
        prefix = "hid_" if self._source_name() == "hid" else ""
        return f"{prefix}accel_signs", f"{prefix}gyro_signs"

    def _signs_from_settings(
        self, settings: dict[str, str], source: str
    ) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """その入力源が使う符号を設定から読む。読めなければ源の既定へ倒す。

        HID直結は生値がSwitchフレームなので恒等、SDL路は軸写像の
        逆変換（-1,-1,1）が前提。watched() が毎サンプル適用する。
        """
        default = (1, 1, 1) if source == "hid" else (-1, -1, 1)
        try:
            from config import parse_imu_axis_map
        except Exception:
            return default, default
        prefix = "hid_" if source == "hid" else ""
        acc = parse_imu_axis_map(str(settings.get(prefix + "accel_signs", "")), (-1, 1))
        gyr = parse_imu_axis_map(str(settings.get(prefix + "gyro_signs", "")), (-1, 1))
        if acc is None:
            acc = default
        if gyr is None:
            gyr = default
        return acc, gyr

    def _live_trim_from_settings(
        self, settings: dict[str, str], source: str
    ) -> _LiveTrim:
        """送出中に毎サンプル読む加工値を設定から組み立てる。"""
        try:
            gyro_scale = float(settings.get("gyro_scale", "1.0") or 1.0)
        except (TypeError, ValueError):
            gyro_scale = 1.0
        if not 0.01 <= gyro_scale <= 4.0:
            gyro_scale = 1.0
        try:
            dead_dps = float(settings.get("gyro_dead_dps", "0.0") or 0.0)
        except (TypeError, ValueError):
            dead_dps = 0.0
        dead_raw = max(0.0, dead_dps) / 0.06103
        acc_signs, gyr_signs = self._signs_from_settings(settings, source)
        return (gyro_scale, dead_raw, acc_signs, gyr_signs)

    def _load_invert_from_settings(
        self, settings: dict[str, str] | None = None
    ) -> None:
        """選択中の入力源の符号を画面へ反映する。

        プリセットに一致しなければ「設定値のまま」を選び、送出開始時に
        符号を書き換えない（既定の軸写像を黙って壊さない）。
        """
        try:
            from config import parse_imu_axis_map

            if settings is None:
                settings = self._settings_provider()
        except Exception:
            return
        acc_key, gyr_key = self._sign_keys()
        acc = parse_imu_axis_map(str(settings.get(acc_key, "")), (-1, 1))
        gyr = parse_imu_axis_map(str(settings.get(gyr_key, "")), (-1, 1))
        if acc is not None and gyr is not None:
            for name, label in INVERT_LABELS:
                if INVERT_SIGNS[name] == (acc, gyr):
                    self._invert_var.set(label)
                    return
        self._invert_var.set(CURRENT_INVERT_LABEL)

    def _on_source_changed(self, _event: Any = None) -> None:
        """入力源に応じて行を出し分ける（ラベルで説明しない）。"""
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        """通常は実機の2源だけ。診断用の行はデバッグ時だけ出す。"""
        source = self._source_name()
        debug = bool(self._debug_var.get())
        if source == "sensor":
            self._pad_frame.grid()
        else:
            self._pad_frame.grid_remove()
        if source == "step":
            self._btn_step.grid()
        else:
            self._btn_step.grid_remove()
        if source == "manual":
            self._manual_frame.grid()
        else:
            self._manual_frame.grid_remove()
        for widget in (self._invert_label, self._invert_cb):
            if debug:
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self._scale_label, self._scale_cb):
            if debug:
                widget.grid()
            else:
                widget.grid_remove()
        if debug:
            self._detail_cb.grid()
        else:
            self._detail_cb.grid_remove()
            # デバッグOFFで詳細チェックだけ消すと波形が残る。チェックも
            # 畳み、入力源を通常へ戻した後も見えない状態に揃える。
            try:
                self._detail_var.set(False)
            except Exception:
                pass
            self._collapse_detail()
        if not debug and source in DEBUG_SOURCES:
            # デバッグ源を選んだまま通常へ戻したら実機源へ寄せる。
            for name, label in SOURCE_LABELS:
                if name == "hid":
                    self._source_var.set(label)
                    break
            self._refresh_source_choices()
        # 入力源が変わったら、その源が読む符号を画面へ反映する。
        self._load_invert_from_settings()

    def _refresh_source_choices(self) -> None:
        """デバッグ時だけ診断用の入力源を候補に出す。"""
        debug = bool(self._debug_var.get())
        values = [
            label for name, label in SOURCE_LABELS if debug or name not in DEBUG_SOURCES
        ]
        try:
            self._source_cb.config(values=values)
        except Exception:
            pass

    def _on_debug_toggled(self) -> None:
        self._refresh_source_choices()
        self._apply_visibility()

    def _manual_reader(self) -> Any:
        """手動入力の読み取り関数。送出スレッドから呼ばれる。"""
        snapshot = self._manual_values

        def _read() -> tuple[int, int, int, int, int, int]:
            return (
                snapshot[0],
                snapshot[1],
                snapshot[2],
                snapshot[3],
                snapshot[4],
                snapshot[5],
            )

        return _read

    def _invert_mode(self) -> str | None:
        """画面の反転ラベル→設定名。設定値のままならNone。"""
        label = self._invert_var.get()
        for name, text in INVERT_LABELS:
            if text == label:
                return name
        return None

    def _on_invert_changed(self, _event: Any = None) -> None:
        """反転の組み合わせを設定へ書き、送出中なら次のサンプルから効かせる。"""
        mode = self._invert_mode()
        if mode is None:
            self._queue.put(("log", "IMU反転: 設定値のまま（符号は書き換えません）"))
            return
        acc, gyr = INVERT_SIGNS[mode]
        acc_key, gyr_key = self._sign_keys()
        try:
            self._settings_writer(
                {
                    acc_key: ",".join(str(v) for v in acc),
                    gyr_key: ",".join(str(v) for v in gyr),
                }
            )
        except Exception:
            pass
        suffix = "（次回から）"
        trim = self._live_trim
        if self._streaming and trim is not None:
            self._live_trim = (trim[0], trim[1], acc, gyr)
            suffix = "（即時）"
        self._queue.put(("log", f"反転: {self._invert_var.get()}{suffix}"))

    def _on_scale_changed(self, _event: Any = None) -> None:
        """ジャイロ倍率を設定へ書き、送出中なら次のサンプルから効かせる。"""
        try:
            value = float(self._scale_var.get())
        except (TypeError, ValueError):
            return
        try:
            self._settings_writer({"gyro_scale": f"{value:g}"})
        except Exception:
            pass
        suffix = "（次回から）"
        trim = self._live_trim
        if self._streaming and trim is not None:
            self._live_trim = (value, trim[1], trim[2], trim[3])
            suffix = "（即時）"
        self._queue.put(("log", f"倍率: {value:g}{suffix}"))

    def _source_name(self) -> str:
        """画面の入力源ラベル→設定名。読めなければsensor。"""
        label = self._source_var.get()
        for name, text in SOURCE_LABELS:
            if text == label:
                return name
        return "sensor"

    def _save_manual_values(self) -> None:
        """手動入力の6軸を設定へ書く（次回起動でも同じ値を使う）。"""
        try:
            self._settings_writer(
                {
                    "acc_x": str(self._manual_values[0]),
                    "acc_y": str(self._manual_values[1]),
                    "acc_z": str(self._manual_values[2]),
                    "manual_gx": str(self._manual_values[3]),
                    "manual_gy": str(self._manual_values[4]),
                    "manual_gz": str(self._manual_values[5]),
                }
            )
        except Exception:
            pass

    def _on_toggle(self) -> None:
        """送出の開始/停止。線操作は作業スレッドで行う。"""
        if self._starting or self._stopping:
            self._queue.put(
                ("log", "開始/停止の処理中です。完了後にもう一度押してください。")
            )
            return
        if self._streaming:
            self._stopping = True
            try:
                self._btn_toggle.configure(text="停止中…")
            except Exception:
                pass
            thread = threading.Thread(target=self._stop_worker, daemon=True)
            thread.start()
            return
        source = self._source_name()
        if source == "manual":
            self._normalize_manual_values()
            self._save_manual_values()
        try:
            pad = int(self._pad_var.get())
        except (TypeError, ValueError):
            pad = 0
        try:
            scale_text = f"{float(self._scale_var.get()):g}"
        except (TypeError, ValueError):
            scale_text = "1"
        values = {
            "source": source,
            "pad_index": str(pad),
            "gyro_scale": scale_text,
        }
        mode = self._invert_mode()
        if mode is not None:
            # プリセットを明示的に選んだときだけ書く。未変更のまま開始して
            # 設定ファイルの既定符号（SDLの逆写像など）を壊さない。
            acc, gyr = INVERT_SIGNS[mode]
            acc_key, gyr_key = self._sign_keys()
            values[acc_key] = ",".join(str(v) for v in acc)
            values[gyr_key] = ",".join(str(v) for v in gyr)
        try:
            self._settings_writer(values)
        except Exception:
            pass
        self._starting = True
        try:
            self._btn_toggle.configure(text="開始中…")
        except Exception:
            pass
        thread = threading.Thread(
            target=self._start_worker, args=(source, pad), daemon=True
        )
        thread.start()

    def _start_worker(self, source: str, pad: int) -> None:
        """開始ワーカー。終了時は必ずフラグを戻し、失敗なら表示も戻す。"""
        try:
            self._do_start(source, pad)
        finally:
            self._starting = False
            if not self._streaming:
                self._queue.put(("state", "stopped"))

    def _stop_worker(self) -> None:
        """停止ワーカー。終了時は必ずフラグを戻す。"""
        try:
            self._do_stop()
        finally:
            self._stopping = False

    def _on_step_fire(self) -> None:
        """STEP波形の再発火。送出中でSTEP源のときだけ効く。"""
        gen = self._step_gen
        if gen is None or not self._streaming:
            self._queue.put(("log", "STEP送出中に押してください。"))
            return
        try:
            gen.trigger()
            self._queue.put(("log", "STEPを発火しました。"))
        except Exception as e:
            self._queue.put(("log", f"STEP発火に失敗: {e!r}"))

    def _on_detail_toggled(self) -> None:
        """詳細表示の切替。ONで同frame内に展開、OFFで畳む。送出は続ける。"""
        try:
            show = bool(self._detail_var.get())
        except Exception:
            show = False
        if show:
            if getattr(self, "_gyro_view", None) is not None:
                return
            try:
                from GyroView import GyroView

                view = GyroView(self.frame, rate_range=500)
                # 反転（3）・感度（4）・手動入力（5）・詳細チェック（6）と
                # 重ねない。それらの下へ置く。
                view.grid(row=7, column=0, columnspan=3, sticky=tk.EW)
                reset = ttk.Button(view, text="ヨーリセット", command=view.reset_yaw)
                reset.pack(pady=(0, 4))
                self._gyro_view = view
            except Exception as e:
                self._queue.put(("log", f"詳細表示を作れません: {e!r}"))
                try:
                    self._detail_var.set(False)
                except Exception:
                    pass
        else:
            self._collapse_detail()

    def _collapse_detail(self) -> None:
        """詳細表示を畳む。送出は続ける。"""
        view, self._gyro_view = getattr(self, "_gyro_view", None), None
        if view is not None:
            try:
                view.close()
            except Exception:
                pass
            try:
                view.destroy()
            except Exception:
                pass

    def _build_source(self, source: str, pad: int) -> Any:
        """入力源の生成器を作る。失敗はNone。"""
        if source == "manual":
            return self._manual_reader()
        try:
            settings = self._settings_provider()
        except Exception:
            settings = {}
        try:
            acc_z = int(settings.get("acc_z", "4096"))
        except (TypeError, ValueError):
            acc_z = 4096
        try:
            acc_x = int(settings.get("acc_x", "0"))
        except (TypeError, ValueError):
            acc_x = 0
        try:
            acc_y = int(settings.get("acc_y", "0"))
        except (TypeError, ValueError):
            acc_y = 0
        if source == "sensor":
            try:
                from core.sdl_sensor import SdlSensorReader

                # 符号は watched() が毎サンプル適用する（二重適用防止）。
                # 軸設定が欠けた場合でも reader 側の既定符号が残らないよう、
                # 生成時点で恒等にしておく。
                reader = SdlSensorReader(accel_signs=(1, 1, 1), gyro_signs=(1, 1, 1))
                self._apply_axis_settings(reader, settings, apply_signs=False)
                if not reader.open(pad):
                    return None
                self._sensor_reader = reader
                return reader.read
            except Exception:
                return None
        if source == "hid":
            try:
                from core.nintendo_hid import get_shared_reader

                reader = get_shared_reader()
                ensure_live = getattr(reader, "ensure_live", None)
                if callable(ensure_live):
                    # 開いているのに0x30が古い/IMU未有効のハンドルも、
                    # 開始時に再武装する（HID直結でジャイロが取れない対策）。
                    if not ensure_live():
                        return None
                elif not reader.is_open() and not reader.open():
                    return None
                self._sensor_reader = reader
                # 符号は watched() が毎サンプル適用する（二重適用防止）。
                transform = self._sample_transform(
                    settings, hid=True, apply_signs=False
                )
                # 0x30の3サンプルを5msごとに順に送る（階段化を避ける）。
                inner_read = reader.read_next
                last_try = {"t": 0.0}

                def read_with_reopen() -> Any:
                    # USB再列挙（UF2書き込み等）でハンドルが死んだ場合や、
                    # 開いたまま0x30が途絶えた場合に、IMU側から再武装する。
                    sample = inner_read()
                    if sample is not None:
                        return sample
                    now = time.monotonic()
                    if now - last_try["t"] >= 1.0:
                        last_try["t"] = now
                        try:
                            if callable(ensure_live):
                                ensure_live()
                            elif not reader.is_open():
                                reader.open(retries=1)
                        except Exception:
                            return None
                    return None

                if transform is None:
                    return read_with_reopen

                def mapped() -> Any:
                    sample = read_with_reopen()
                    if sample is None:
                        return None
                    try:
                        return transform(sample)
                    except Exception:
                        return None

                return mapped
            except Exception:
                return None
        if source == "sine":
            try:
                from core.imu_wave import SineWave

                axis = int(settings.get("sine_axis", "2"))
                amp = int(settings.get("sine_amp", "4096"))
                period = float(settings.get("sine_period_s", "2.0"))
                return SineWave(axis=axis, amp=amp, period_s=period, acc_z=acc_z)
            except (TypeError, ValueError):
                return None
        if source == "step":
            try:
                from core.imu_wave import StepWave

                axis = int(settings.get("step_axis", "2"))
                raw = int(settings.get("step_raw", "5899"))
                duration = float(settings.get("step_duration_s", "4.0"))
                gen = StepWave(axis=axis, raw=raw, duration_s=duration, acc_z=acc_z)
                gen.trigger()
                self._step_gen = gen
                return gen
            except (TypeError, ValueError):
                return None
        try:
            from core.imu_wave import StaticRest

            return StaticRest(acc_z=acc_z, acc_x=acc_x, acc_y=acc_y)
        except Exception:
            return None

    @staticmethod
    def _sample_transform(
        settings: dict[str, str],
        hid: bool = False,
        apply_signs: bool = True,
    ) -> Any | None:
        """IMU軸・符号の変換関数を作る。恒等ならNone。

        HID直結は生値がSwitchフレームのため `hid_*` キー（既定は恒等）を使う。
        接頭辞なしのキーはSDLセンサー用（Switch→SDL変換の逆写像）であり、
        コンソール側の反転解釈を打ち消す実測（例: 符号=1,-1,-1）にも使う。
        `apply_signs=False` は軸だけを返し、符号は呼び出し側（watched）が
        毎サンプル適用する（二重適用防止）。
        読めないキーはNone（＝無変換）に倒す。
        """
        try:
            from config import parse_imu_axis_map
        except Exception:
            return None
        pre = "hid_" if hid else ""
        aa = parse_imu_axis_map(str(settings.get(pre + "accel_axes", "")), (0, 1, 2))
        as_ = parse_imu_axis_map(str(settings.get(pre + "accel_signs", "")), (-1, 1))
        ga = parse_imu_axis_map(str(settings.get(pre + "gyro_axes", "")), (0, 1, 2))
        gs = parse_imu_axis_map(str(settings.get(pre + "gyro_signs", "")), (-1, 1))
        if aa is None or as_ is None or ga is None or gs is None:
            return None
        acc_signs = as_ if apply_signs else (1, 1, 1)
        gyr_signs = gs if apply_signs else (1, 1, 1)
        if (
            aa == (0, 1, 2)
            and ga == (0, 1, 2)
            and acc_signs == (1, 1, 1)
            and gyr_signs == (1, 1, 1)
        ):
            return None

        def apply(sample: Any) -> tuple[int, int, int, int, int, int]:
            ax, ay, az, gx, gy, gz = (int(v) for v in sample)
            acc = (ax, ay, az)
            gyr = (gx, gy, gz)
            ca = tuple(acc[aa[i]] * acc_signs[i] for i in range(3))
            cg = tuple(gyr[ga[i]] * gyr_signs[i] for i in range(3))
            return (ca[0], ca[1], ca[2], cg[0], cg[1], cg[2])

        return apply

    @staticmethod
    def _apply_axis_settings(
        reader: Any,
        settings: dict[str, str],
        apply_signs: bool = True,
    ) -> None:
        """軸変換・バイアス設定をreaderへ適用する。読めない項目は飛ばす。

        `apply_signs=False` は軸とバイアスだけを渡し、符号は呼び出し側
        （watched）が毎サンプル適用する（二重適用防止）。
        """
        try:
            from config import parse_imu_axis_map, parse_imu_bias
        except Exception:
            return
        try:
            aa = parse_imu_axis_map(str(settings.get("accel_axes", "")), (0, 1, 2))
            as_ = parse_imu_axis_map(str(settings.get("accel_signs", "")), (-1, 1))
            ga = parse_imu_axis_map(str(settings.get("gyro_axes", "")), (0, 1, 2))
            gs = parse_imu_axis_map(str(settings.get("gyro_signs", "")), (-1, 1))
            if aa is not None and as_ is not None and ga is not None and gs is not None:
                if apply_signs:
                    reader.set_axis_map(aa, as_, ga, gs)
                else:
                    reader.set_axis_map(aa, (1, 1, 1), ga, (1, 1, 1))
            bias = parse_imu_bias(str(settings.get("gyro_bias", "")))
            if bias is not None:
                reader.set_gyro_bias(bias)
        except Exception:
            pass

    def _do_start(self, source: str, pad: int) -> None:
        """送出開始の実体（作業スレッド）。"""
        try:
            transport = self._transport_provider()
        except Exception:
            transport = None
        if transport is None:
            self._queue.put(("log", "シリアルが開いていません。先に接続してください。"))
            return
        try:
            opened = bool(transport.is_open())
        except Exception:
            opened = False
        if not opened:
            self._queue.put(("log", "シリアルが開いていません。先に接続してください。"))
            return
        gen = self._build_source(source, pad)
        if gen is None:
            if source == "sensor":
                self._queue.put(
                    (
                        "log",
                        f"センサーが開けません: pad={pad}。対応パッドを繋いでください。",
                    )
                )
            elif source == "hid":
                self._queue.put(
                    ("log", "ProConが開けません。USB接続を確認してください。")
                )
            else:
                self._queue.put(
                    ("log", f"波形の生成に失敗: {source}。設定を確認してください。")
                )
            return
        # 可視化へ送出値を流す。窓の開閉に追従するため都度引き直す。
        inner = gen
        panel = self
        try:
            settings_now = self._settings_provider()
        except Exception:
            settings_now = {}
        # 加工値は開始時の設定で固定せず、不変タプルを毎サンプル読む。
        # GUIスレッドの反転・感度変更は次のサンプルから効く。
        self._live_trim = self._live_trim_from_settings(settings_now, source)

        def watched() -> Any:
            sample = inner()
            if sample is None:
                return None
            try:
                ax, ay, az, gx, gy, gz = (int(v) for v in sample)
            except (TypeError, ValueError):
                return None
            trim = panel._live_trim
            if trim is None:
                gyro_scale, dead_raw = 1.0, 0.0
                acc_signs = (1, 1, 1)
                gyr_signs = (1, 1, 1)
            else:
                gyro_scale, dead_raw, acc_signs, gyr_signs = trim
            # 符号は入力源ではなくここで一度だけ掛ける（二重適用防止）。
            ax = max(-32768, min(32767, ax * acc_signs[0]))
            ay = max(-32768, min(32767, ay * acc_signs[1]))
            az = max(-32768, min(32767, az * acc_signs[2]))
            gx = max(-32768, min(32767, gx * gyr_signs[0]))
            gy = max(-32768, min(32767, gy * gyr_signs[1]))
            gz = max(-32768, min(32767, gz * gyr_signs[2]))
            if gyro_scale != 1.0:
                # コンソール側のスケール解釈が想定と違う場合の実測トリム。
                gx = max(-32768, min(32767, int(round(gx * gyro_scale))))
                gy = max(-32768, min(32767, int(round(gy * gyro_scale))))
                gz = max(-32768, min(32767, int(round(gz * gyro_scale))))
            if dead_raw > 0.0:
                # 静止時の残余バイアスでゲーム側の判定が誤発火する
                # 場合の不感帯。各軸独立に潰す（実スイングは残る）。
                try:
                    from core.imu_wave import apply_gyro_deadband

                    ax, ay, az, gx, gy, gz = apply_gyro_deadband(
                        (ax, ay, az, gx, gy, gz), dead_raw
                    )
                except Exception:
                    pass

            peak_g = max(abs(gx), abs(gy), abs(gz)) * 0.06103
            peak_a = max(abs(ax), abs(ay), abs(az)) * 0.000244
            if peak_g > panel._peak_g:
                panel._peak_g = peak_g
            if peak_a > panel._peak_a:
                panel._peak_a = peak_a
            # 軸ごとの最小/最大。定値源なら0、FWのtimeout復帰や
            # 読みの取りこぼしで跳べば幅として見える。
            for i, v in enumerate((ax, ay, az)):
                lo = panel._acc_min[i]
                hi = panel._acc_max[i]
                panel._acc_min[i] = v if lo is None or v < lo else lo
                panel._acc_max[i] = v if hi is None or v > hi else hi
            view = getattr(panel, "_gyro_view", None)
            if view is not None:
                try:
                    view.push_raw(ax, ay, az, gx, gy, gz)
                except Exception:
                    pass
            # 加工後の値を送出する（不感帯を実際に効かせる）。
            return (ax, ay, az, gx, gy, gz)

        if self._stop:
            # close() が先に走った場合は線を掴まない。
            self._close_source()
            return
        gen = watched
        try:
            start = getattr(transport, "start_imu_loop", None)
            ok = bool(start(gen)) if callable(start) else False
        except Exception:
            ok = False
        if not ok:
            self._queue.put(("log", "ジャイロ送信を開始できませんでした。"))
            self._close_source()
            return
        self._streaming = True
        self._queue.put(("state", "streaming"))
        self._queue.put(("log", "ジャイロ送信を開始しました。"))

    def _do_stop(self) -> None:
        """送出停止の実体（作業スレッド）。"""
        try:
            transport = self._transport_provider()
        except Exception:
            transport = None
        if transport is not None:
            try:
                stop = getattr(transport, "stop_imu_loop", None)
                if callable(stop):
                    stop()
            except Exception:
                pass
        self._close_source()
        self._streaming = False
        self._queue.put(("state", "stopped"))
        self._queue.put(("log", "ジャイロ送信を停止しました。"))

    def _close_source(self) -> None:
        """入力源を閉じる。例外は投げない。

        HID直結はパッド読みと共有しているため、ここでは閉じない
        （閉じるとボタン入力が止まる）。プロセス終了時に任せる。
        """
        reader, self._sensor_reader = self._sensor_reader, None
        self._step_gen = None
        if reader is None:
            return
        try:
            from core.nintendo_hid import is_shared_reader

            if is_shared_reader(reader):
                return
        except Exception:
            pass
        try:
            reader.close()
        except Exception:
            pass

    def _poll(self) -> None:
        """作業スレッドからの届けを画面へ出す。"""
        if self._stop:
            return
        try:
            while True:
                try:
                    kind, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "log":
                    try:
                        self._log(str(payload))
                    except Exception:
                        pass
                elif kind == "state":
                    try:
                        if payload == "streaming":
                            self._btn_toggle.configure(text="停止")
                        else:
                            self._btn_toggle.configure(text="開始")
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self._refresh_stats()
        except Exception:
            pass
        try:
            self.frame.after(500, self._poll)
        except Exception:
            pass

    def _refresh_stats(self) -> None:
        """統計表示を更新する。送出中のみ読む。"""
        if not self._streaming:
            self._peak_g = 0.0
            self._peak_a = 0.0
            try:
                self._stats_var.set("停止中")
            except Exception:
                pass
            return
        peak_g = self._peak_g
        peak_a = self._peak_a
        self._peak_g = 0.0
        self._peak_a = 0.0
        spans = []
        for i in range(3):
            lo = self._acc_min[i]
            hi = self._acc_max[i]
            spans.append(0.0 if lo is None or hi is None else (hi - lo) * 0.000244)
        self._acc_min = [None, None, None]
        self._acc_max = [None, None, None]
        span_txt = f"ΔA=({spans[0]:.2f},{spans[1]:.2f},{spans[2]:.2f})G"
        try:
            transport = self._transport_provider()
            stats = getattr(transport, "imu_stats", None)
            info = stats() if callable(stats) else None
        except Exception:
            info = None
        try:
            if isinstance(info, dict):
                sent_now = int(info.get("sent", 0))
                now = time.monotonic()
                rate_txt = ""
                if self._last_sent is not None and self._last_sent_t is not None:
                    span = now - self._last_sent_t
                    if span > 0:
                        rate_txt = f" rate={(sent_now - self._last_sent) / span:.0f}/s"
                self._last_sent = sent_now
                self._last_sent_t = now
                self._stats_var.set(
                    f"送出中 sent={info.get('sent', 0)}"
                    f" dropped={info.get('dropped', 0)}{rate_txt}"
                    f" peak|g|={peak_g:.1f}dps peak|a|={peak_a:.2f}G {span_txt}"
                )
            else:
                self._stats_var.set(
                    f"送出中 peak|g|={peak_g:.1f}dps peak|a|={peak_a:.2f}G {span_txt}"
                )
        except Exception:
            pass

    def close(self) -> None:
        """送出を止めて予約を消す。例外は投げない。"""
        self._stop = True
        try:
            self._collapse_detail()
        except Exception:
            pass
        # 開始/停止ワーカーが走っている間に別の停止を重ねない。
        deadline = time.monotonic() + 1.0
        while (self._starting or self._stopping) and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._streaming and not self._stopping:
            self._stopping = True
            thread = threading.Thread(target=self._stop_worker, daemon=True)
            thread.start()
            try:
                thread.join(1.0)
            except Exception:
                pass
        elif not self._streaming:
            self._close_source()
