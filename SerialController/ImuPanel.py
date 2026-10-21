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

SOURCE_LABELS = (
    ("sensor", "センサー"),
    ("hid", "ProCon直結"),
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

# ジャイロ出力倍率のプリセット（実機比較用）。
SCALE_PRESETS = ("1.0", "0.5", "0.25", "0.125", "0.0625")


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
        self._sensor_reader: Any = None
        self._step_gen: Any = None
        self._stats_after: Any = None
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
        self._btn_toggle = ttk.Button(frame, text="送出開始", command=self._on_toggle)
        self._btn_toggle.grid(row=0, column=2, sticky=tk.EW, padx=2, pady=2)

        ttk.Label(frame, text="パッド").grid(
            row=1, column=0, sticky=tk.W, padx=2, pady=2
        )
        self._pad_var = tk.StringVar(value="0")
        self._pad_cb = ttk.Combobox(
            frame,
            width=10,
            state="readonly",
            textvariable=self._pad_var,
            values=["0", "1", "2", "3"],
        )
        self._pad_cb.grid(row=1, column=1, sticky=tk.EW, padx=2, pady=2)
        self._btn_step = ttk.Button(frame, text="STEP発火", command=self._on_step_fire)
        self._btn_step.grid(row=1, column=2, sticky=tk.EW, padx=2, pady=2)

        self._stats_var = tk.StringVar(value="停止中")
        ttk.Label(frame, textvariable=self._stats_var).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, padx=2, pady=2
        )
        ttk.Label(frame, text="反転").grid(row=3, column=0, sticky=tk.W, padx=2, pady=2)
        self._invert_var = tk.StringVar(value="なし")
        self._invert_cb = ttk.Combobox(
            frame,
            width=10,
            state="readonly",
            textvariable=self._invert_var,
            values=[label for _, label in INVERT_LABELS],
        )
        self._invert_cb.grid(row=3, column=1, sticky=tk.EW, padx=2, pady=2)
        self._invert_cb.bind("<<ComboboxSelected>>", self._on_invert_changed, add="+")
        ttk.Label(frame, text="感度").grid(row=4, column=0, sticky=tk.W, padx=2, pady=2)
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
        self._detail_var = tk.BooleanVar(value=False)
        self._detail_cb = ttk.Checkbutton(
            frame,
            text="詳細表示",
            variable=self._detail_var,
            command=self._on_detail_toggled,
        )
        self._detail_cb.grid(row=2, column=2, sticky=tk.EW, padx=2, pady=2)
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
            var.trace_add("write", self._make_manual_trace(i, var))
            self._manual_vars.append(var)
        self._manual_frame.grid(
            row=4, column=0, columnspan=3, sticky=tk.EW, pady=(4, 0)
        )
        self._manual_frame.grid_remove()
        self._source_cb.bind("<<ComboboxSelected>>", self._on_source_changed, add="")
        # 詳細の可視化パネル。チェックONのときだけ同frame内に展開する。
        self._gyro_view: Any = None
        self._load_settings_into_vars()
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
            acc = tuple(
                int(v) for v in str(settings.get("accel_signs", "1,1,1")).split(",")
            )
            gyr = tuple(
                int(v) for v in str(settings.get("gyro_signs", "1,1,1")).split(",")
            )
            for name, label in INVERT_LABELS:
                if INVERT_SIGNS[name] == (acc, gyr):
                    self._invert_var.set(label)
                    break
            scale = float(settings.get("gyro_scale", "1.0") or 1.0)
            self._scale_var.set(f"{scale:g}")
            for i, key in enumerate(
                ("acc_x", "acc_y", "acc_z", "manual_gx", "manual_gy", "manual_gz")
            ):
                try:
                    self._manual_vars[i].set(str(int(settings.get(key, "0"))))
                except (TypeError, ValueError, IndexError):
                    pass
            self._on_source_changed()
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

    def _on_source_changed(self, _event: Any = None) -> None:
        """入力源の切り替え。手動入力のときだけ6軸欄を出す。"""
        if self._source_name() == "manual":
            self._manual_frame.grid()
        else:
            self._manual_frame.grid_remove()

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

    def _invert_mode(self) -> str:
        """画面の反転ラベル→設定名。既定none。"""
        label = self._invert_var.get()
        for name, text in INVERT_LABELS:
            if text == label:
                return name
        return "none"

    def _on_invert_changed(self, _event: Any = None) -> None:
        """反転の組み合わせを設定へ書く（次の送出開始から効く）。"""
        acc, gyr = INVERT_SIGNS[self._invert_mode()]
        try:
            self._settings_writer(
                {
                    "accel_signs": ",".join(str(v) for v in acc),
                    "gyro_signs": ",".join(str(v) for v in gyr),
                }
            )
        except Exception:
            pass
        self._queue.put(
            ("log", f"IMU反転: {self._invert_var.get()}（次の送出開始から）")
        )

    def _on_scale_changed(self, _event: Any = None) -> None:
        """ジャイロ倍率を設定へ書く（次の送出開始から効く）。"""
        try:
            value = float(self._scale_var.get())
        except (TypeError, ValueError):
            return
        try:
            self._settings_writer({"gyro_scale": f"{value:g}"})
        except Exception:
            pass
        self._queue.put(("log", f"ジャイロ倍率: {value:g}（次の送出開始から）"))

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
        if self._streaming:
            thread = threading.Thread(target=self._do_stop, daemon=True)
            thread.start()
            return
        source = self._source_name()
        if source == "manual":
            self._save_manual_values()
        try:
            pad = int(self._pad_var.get())
        except (TypeError, ValueError):
            pad = 0
        acc, gyr = INVERT_SIGNS[self._invert_mode()]
        try:
            scale_text = f"{float(self._scale_var.get()):g}"
        except (TypeError, ValueError):
            scale_text = "1"
        try:
            self._settings_writer(
                {
                    "source": source,
                    "pad_index": str(pad),
                    "accel_signs": ",".join(str(v) for v in acc),
                    "gyro_signs": ",".join(str(v) for v in gyr),
                    "gyro_scale": scale_text,
                }
            )
        except Exception:
            pass
        thread = threading.Thread(
            target=self._do_start, args=(source, pad), daemon=True
        )
        thread.start()

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
            if self._gyro_view is not None:
                return
            try:
                from GyroView import GyroView

                view = GyroView(self.frame, rate_range=500)
                view.grid(row=3, column=0, columnspan=3, sticky=tk.EW)
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
        view, self._gyro_view = self._gyro_view, None
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

                reader = SdlSensorReader()
                self._apply_axis_settings(reader, settings)
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
                if not reader.is_open() and not reader.open():
                    return None
                self._sensor_reader = reader
                transform = self._sample_transform(settings)
                inner_read = reader.read
                last_try = {"t": 0.0}

                def read_with_reopen() -> Any:
                    # USB再列挙（UF2書き込み等）でハンドルが死んだ場合、
                    # パッド側が動いていなくてもIMU側から開き直す。
                    if not reader.is_open():
                        now = time.monotonic()
                        if now - last_try["t"] >= 1.0:
                            last_try["t"] = now
                            try:
                                reader.open(retries=1)
                            except Exception:
                                return None
                        if not reader.is_open():
                            return None
                    return inner_read()

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
    ) -> Any | None:
        """IMU軸・符号の変換関数を作る。恒等ならNone。

        HID直結は生値がSwitchフレームのため、通常は軸=0,1,2・符号=1,1,1。
        コンソール側の反転解釈を打ち消す実測（例: 符号=1,-1,-1）にも使う。
        """
        try:
            from config import parse_imu_axis_map
        except Exception:
            return None
        aa = parse_imu_axis_map(str(settings.get("accel_axes", "")), (0, 1, 2))
        as_ = parse_imu_axis_map(str(settings.get("accel_signs", "")), (-1, 1))
        ga = parse_imu_axis_map(str(settings.get("gyro_axes", "")), (0, 1, 2))
        gs = parse_imu_axis_map(str(settings.get("gyro_signs", "")), (-1, 1))
        if aa is None or as_ is None or ga is None or gs is None:
            return None
        if aa == (0, 1, 2) and ga == (0, 1, 2) and as_ == (1, 1, 1) and gs == (1, 1, 1):
            return None

        def apply(sample: Any) -> tuple[int, int, int, int, int, int]:
            ax, ay, az, gx, gy, gz = (int(v) for v in sample)
            acc = (ax, ay, az)
            gyr = (gx, gy, gz)
            ca = tuple(acc[aa[i]] * as_[i] for i in range(3))
            cg = tuple(gyr[ga[i]] * gs[i] for i in range(3))
            return (ca[0], ca[1], ca[2], cg[0], cg[1], cg[2])

        return apply

    @staticmethod
    def _apply_axis_settings(reader: Any, settings: dict[str, str]) -> None:
        """軸変換・バイアス設定をreaderへ適用する。読めない項目は飛ばす。"""
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
                reader.set_axis_map(aa, as_, ga, gs)
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
        try:
            dead_dps = float(settings_now.get("gyro_dead_dps", "0.0") or 0.0)
        except (TypeError, ValueError):
            dead_dps = 0.0
        try:
            gyro_scale = float(settings_now.get("gyro_scale", "1.0") or 1.0)
        except (TypeError, ValueError):
            gyro_scale = 1.0
        if not 0.01 <= gyro_scale <= 4.0:
            gyro_scale = 1.0
        dead_raw = max(0.0, dead_dps) / 0.06103
        first_sample = {"done": False}

        def watched() -> Any:
            sample = inner()
            if sample is None:
                return None
            try:
                ax, ay, az, gx, gy, gz = (int(v) for v in sample)
            except (TypeError, ValueError):
                return None
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

            # 瞬時表示では見えない単発の跳びを1秒窓の最大値で拾う。
            if not first_sample["done"]:
                first_sample["done"] = True
                panel._queue.put(
                    (
                        "log",
                        f"IMU不感帯={dead_dps:.1f}dps 初回サンプル:"
                        f" acc=({ax},{ay},{az}) gyro=({gx},{gy},{gz})",
                    )
                )
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

        gen = watched
        try:
            start = getattr(transport, "start_imu_loop", None)
            ok = bool(start(gen)) if callable(start) else False
        except Exception:
            ok = False
        if not ok:
            self._queue.put(("log", "IMU送出の開始に失敗しました。"))
            self._close_source()
            return
        self._streaming = True
        self._queue.put(("state", "streaming"))
        self._queue.put(("log", f"IMU送出を開始: {source}。"))

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
        self._queue.put(("log", "IMU送出を停止しました。"))

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
                            self._btn_toggle.configure(text="送出停止")
                        else:
                            self._btn_toggle.configure(text="送出開始")
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
        if self._streaming:
            thread = threading.Thread(target=self._do_stop, daemon=True)
            thread.start()
            try:
                thread.join(1.0)
            except Exception:
                pass
        else:
            self._close_source()
