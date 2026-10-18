#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imu_wave.py - IMU波形の生成器（Tkなし・純粋層）。

Picoへ送るIMU_SAMPLE (§5.13) の値を時刻から決める。実コントローラが
無い環境でのE2E検証と、SDLセンサー導入までの足場が目的である。
FW側 imu_dummy.c のSINE/STEPと同形だが、PC側生成のため時刻は
perf_counter基準で、量子化済みint16を直接出す。

使い方:
    gen = SineWave(axis=2, amp=4096, period_s=2.0)
    sample = gen()  # (ax, ay, az, gx, gy, gz) の6値かNone
    transport.start_imu_loop(gen)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


def _clamp_i16(value: int) -> int:
    """int16へ飽和させる。"""
    return max(-32768, min(32767, int(value)))


@dataclass
class SineWave:
    """SINE波形。指定軸のgyroだけを振り、他は静止値。"""

    axis: int = 2
    amp: int = 4096
    period_s: float = 2.0
    acc_z: int = 4096

    def __post_init__(self) -> None:
        if self.axis not in (0, 1, 2):
            raise ValueError(f"axisは0..2です: {self.axis!r}")
        if self.period_s <= 0:
            raise ValueError(f"period_sは正です: {self.period_s!r}")
        self._t0 = time.perf_counter()

    def __call__(self) -> tuple[int, int, int, int, int, int]:
        """現時刻の6値を返す。失敗はしない（Noneを返さない）。"""
        el = time.perf_counter() - self._t0
        phase = (el % self.period_s) / self.period_s * (2.0 * math.pi)
        test = int(round(self.amp * math.sin(phase)))
        gyro = [0, 0, 0]
        gyro[self.axis] = _clamp_i16(test)
        return (0, 0, int(self.acc_z), gyro[0], gyro[1], gyro[2])


@dataclass
class StepWave:
    """STEP波形。trigger後のT秒だけ指定軸に一定値を載せる。"""

    axis: int = 2
    raw: int = 5899
    duration_s: float = 4.0
    acc_z: int = 4096

    def __post_init__(self) -> None:
        if self.axis not in (0, 1, 2):
            raise ValueError(f"axisは0..2です: {self.axis!r}")
        if self.duration_s <= 0:
            raise ValueError(f"duration_sは正です: {self.duration_s!r}")
        self._t0: float | None = None

    def trigger(self) -> None:
        """STEP区間を開始する。区間中の再呼出しは取り直す。"""
        self._t0 = time.perf_counter()

    def __call__(self) -> tuple[int, int, int, int, int, int]:
        """現時刻の6値を返す。区間外は静止値（Noneを返さない）。"""
        test = 0
        if self._t0 is not None:
            el = time.perf_counter() - self._t0
            if 0 <= el < self.duration_s:
                test = _clamp_i16(self.raw)
        gyro = [0, 0, 0]
        gyro[self.axis] = test
        return (0, 0, int(self.acc_z), gyro[0], gyro[1], gyro[2])


@dataclass(frozen=True, slots=True)
class StaticRest:
    """静止値。IMU loopの疎通確認用。"""

    acc_z: int = 4096

    def __call__(self) -> tuple[int, int, int, int, int, int]:
        return (0, 0, int(self.acc_z), 0, 0, 0)
