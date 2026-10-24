#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sdl_sensor.py - SDL2 GameControllerセンサーの読取（Tkなし）。

pygame同梱のSDL2.dllをctypesで直接叩き、加速度・ジャイロを読む。
pygame._sdl2.controllerにはセンサーAPIが無いため、この迂回が必要である。
依存追加なし。pygame初期化済みのSDLインスタンスを共有する（SDL2は
同一joystickの多重openを許すため、SdlPadReaderと共存できる）。

SDL2センサー仕様:
  ・SDL_SENSOR_ACCEL=1・SDL_SENSOR_GYRO=2（SDL_sensor.h）。
  ・加速度は m/s^2、ジャイロは rad/s のfloat[3]。
  ・軸はSDL右手系（X右・Y上・Z手前。ドキュメントと実測で確認すること）。
  ・使う前にSDL_GameControllerSetSensorEnabledで有効化する。

Switch出力への換算（§9相当の正準レンジ）:
  ・加速度 ±8G: raw = (ms2 / 9.80665) / 0.000244
  ・ジャイロ ±2000dps: raw = (rad/s * 57.2958) / 0.06103
  ・int16へ飽和させる。

使い方:
    reader = SdlSensorReader()
    if reader.open(0):
        sample = reader.read()  # (ax,ay,az,gx,gy,gz) int16タプルかNone
"""

from __future__ import annotations

import ctypes
import math
import os
import threading
from typing import Any

from loguru import logger

STANDARD_GRAVITY = 9.80665
RAD_TO_DEG = 57.29577951308232
ACC_G_PER_LSB = 0.000244
GYRO_DPS_PER_LSB = 0.06103

SDL_SENSOR_INVALID = -1
SDL_SENSOR_UNKNOWN = 0
SDL_SENSOR_ACCEL = 1
SDL_SENSOR_GYRO = 2


def _clamp_i16(value: int) -> int:
    """int16へ飽和させる。"""
    return max(-32768, min(32767, int(value)))


def accel_to_raw(ms2: float) -> int:
    """加速度 m/s^2 → Switch生値。非有限値は0にする。"""
    try:
        v = float(ms2)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v):
        return 0
    return _clamp_i16(round((v / STANDARD_GRAVITY) / ACC_G_PER_LSB))


def gyro_to_raw(rads: float) -> int:
    """角速度 rad/s → Switch生値。非有限値は0にする。"""
    try:
        v = float(rads)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v):
        return 0
    return _clamp_i16(round((v * RAD_TO_DEG) / GYRO_DPS_PER_LSB))


def _load_sdl2() -> Any | None:
    """pygame同梱のSDL2共有libを読む。無ければNone。"""
    try:
        import pygame

        d = os.path.dirname(str(pygame.__file__))
        for name in ("SDL2.dll", "libSDL2.so", "libSDL2.dylib", "SDL2.so"):
            path = os.path.join(d, name)
            if os.path.isfile(path):
                lib = ctypes.CDLL(path)
                _bind(lib)
                return lib
    except Exception:
        return None
    return None


def _bind(lib: Any) -> None:
    """使う関数だけ型付けする。"""
    lib.SDL_GameControllerOpen.argtypes = [ctypes.c_int]
    lib.SDL_GameControllerOpen.restype = ctypes.c_void_p
    lib.SDL_GameControllerClose.argtypes = [ctypes.c_void_p]
    lib.SDL_GameControllerClose.restype = None
    lib.SDL_GameControllerHasSensor.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.SDL_GameControllerHasSensor.restype = ctypes.c_int
    lib.SDL_GameControllerSetSensorEnabled.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    lib.SDL_GameControllerSetSensorEnabled.restype = ctypes.c_int
    lib.SDL_GameControllerGetSensorData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_int,
    ]
    lib.SDL_GameControllerGetAttached.argtypes = [ctypes.c_void_p]
    lib.SDL_GameControllerGetAttached.restype = ctypes.c_int
    lib.SDL_GameControllerUpdate.argtypes = []
    lib.SDL_GameControllerUpdate.restype = None


class SdlSensorReader:
    """SDL2センサーの読取口。軸変換・バイアス補正・量子化まで行う。

    既定の軸変換はSDLのSwitch→SDL変換の逆写像（SDL_hidapi_switch.c
    SendSensorUpdate: SDL_X=-Sw_Y・SDL_Y=+Sw_Z・SDL_Z=-Sw_X）。
    すなわち Sw_X=-SDL_Z・Sw_Y=-SDL_X・Sw_Z=+SDL_Y。恒等写像では
    pitchが無反応軸に入り、上下の感度が落ちる（2026-10-18実機）。
    """

    def __init__(
        self,
        accel_axes: tuple[int, int, int] = (2, 0, 1),
        accel_signs: tuple[int, int, int] = (-1, -1, 1),
        gyro_axes: tuple[int, int, int] = (2, 0, 1),
        gyro_signs: tuple[int, int, int] = (-1, -1, 1),
        gyro_bias: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        self._lock = threading.Lock()
        self._lib: Any | None = None
        self._ready: bool | None = None
        self._handle: Any = None
        self._index = -1
        self._has_accel = False
        self._has_gyro = False
        self._accel_axes = tuple(int(v) for v in accel_axes)
        self._accel_signs = tuple(int(v) for v in accel_signs)
        self._gyro_axes = tuple(int(v) for v in gyro_axes)
        self._gyro_signs = tuple(int(v) for v in gyro_signs)
        self._gyro_bias = tuple(float(v) for v in gyro_bias)
        # 同じ失敗の警告を1回だけ出す（openの再試行で洪水にしない）。
        self._warned: set[str] = set()
        self._warn_lock = threading.Lock()

    def _warn_once(self, key: str, message: str) -> None:
        """同じ理由の警告を1回だけ出す。"""
        with self._warn_lock:
            if key in self._warned:
                return
            self._warned.add(key)
        logger.warning(message)

    def _ensure(self) -> bool:
        """SDL2 libを読める状態にする。成功で True。"""
        if self._ready is not None:
            return self._ready
        # pygame側の初期化が先（SDLインスタンスの共有元）。
        try:
            import pygame
            from pygame._sdl2 import controller as _gc

            pygame.init()
            _gc.init()
        except Exception as e:
            self._ready = False
            self._warn_once(f"ensure:{e!r}", f"SDLを初期化できません: {e}")
            return False
        lib = _load_sdl2()
        self._lib = lib
        self._ready = lib is not None
        return self._ready

    @property
    def available(self) -> bool:
        """この環境でSDL2センサーが読めるか。"""
        return self._ensure()

    def supports_motion(self, index: int = 0) -> bool:
        """指定番号が加速度・ジャイロ両対応か。開かずに確かめる。"""
        if not self._ensure() or self._lib is None:
            return False
        handle = None
        try:
            handle = self._lib.SDL_GameControllerOpen(int(index))
            if not handle:
                return False
            has_a = bool(
                self._lib.SDL_GameControllerHasSensor(handle, SDL_SENSOR_ACCEL)
            )
            has_g = bool(self._lib.SDL_GameControllerHasSensor(handle, SDL_SENSOR_GYRO))
            return has_a and has_g
        except Exception:
            return False
        finally:
            try:
                if handle:
                    self._lib.SDL_GameControllerClose(handle)
            except Exception:
                pass

    def open(self, index: int = 0, retries: int = 10) -> bool:
        """指定番号を開きセンサーを有効化する。成功で True。

        Steam起動中はHIDの奪い合いで開閉が不安定になる実測（SDL#3450）。
        失敗時は0.3秒待って最大retries回まで試す。例外は投げない。
        """
        if not self._ensure() or self._lib is None:
            return False
        import time as _time

        for attempt in range(max(1, int(retries))):
            with self._lock:
                self._close_locked()
                try:
                    handle = self._lib.SDL_GameControllerOpen(int(index))
                    if not handle:
                        self._warn_once(
                            f"sensor_open:{index}",
                            f"SDLでパッド{index}のセンサーを開けません",
                        )
                        continue
                    has_a = bool(
                        self._lib.SDL_GameControllerHasSensor(handle, SDL_SENSOR_ACCEL)
                    )
                    has_g = bool(
                        self._lib.SDL_GameControllerHasSensor(handle, SDL_SENSOR_GYRO)
                    )
                    if not (has_a and has_g):
                        self._lib.SDL_GameControllerClose(handle)
                        self._warn_once(
                            f"sensor_missing:{index}",
                            f"パッド{index}は加速度・ジャイロ非対応です",
                        )
                        continue
                    ok_a = self._lib.SDL_GameControllerSetSensorEnabled(
                        handle, SDL_SENSOR_ACCEL, 1
                    )
                    ok_g = self._lib.SDL_GameControllerSetSensorEnabled(
                        handle, SDL_SENSOR_GYRO, 1
                    )
                    if ok_a != 0 or ok_g != 0:
                        self._lib.SDL_GameControllerClose(handle)
                        self._warn_once(
                            f"sensor_enable:{index}:{ok_a}:{ok_g}",
                            f"パッド{index}のセンサーを有効化できません"
                            f"（accel={ok_a}, gyro={ok_g}）",
                        )
                        continue
                    self._handle = handle
                    self._index = int(index)
                    self._has_accel = True
                    self._has_gyro = True
                    return True
                except Exception as e:
                    self._warn_once(
                        f"sensor_open_exc:{index}:{e!r}",
                        f"SDLでパッド{index}のセンサーを開けません: {e}",
                    )
                    self._close_locked()
            if attempt + 1 < max(1, int(retries)):
                try:
                    _time.sleep(0.3)
                except Exception:
                    pass
        return False

    def _close_locked(self) -> None:
        """錠内で閉じる。開いていなければ何もしない。"""
        try:
            if self._handle is not None and self._lib is not None:
                try:
                    self._lib.SDL_GameControllerSetSensorEnabled(
                        self._handle, SDL_SENSOR_ACCEL, 0
                    )
                except Exception:
                    pass
                try:
                    self._lib.SDL_GameControllerSetSensorEnabled(
                        self._handle, SDL_SENSOR_GYRO, 0
                    )
                except Exception:
                    pass
                self._lib.SDL_GameControllerClose(self._handle)
        except Exception:
            pass
        self._handle = None
        self._index = -1
        self._has_accel = False
        self._has_gyro = False

    def close(self) -> None:
        """開いていれば閉じる。例外は投げない。"""
        with self._lock:
            self._close_locked()

    def is_open(self) -> bool:
        """開いていて装着中か。"""
        with self._lock:
            if self._handle is None or self._lib is None:
                return False
            try:
                return bool(self._lib.SDL_GameControllerGetAttached(self._handle))
            except Exception:
                return False

    def read_raw(
        self,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """物理量を読む。((ax,ay,az) m/s^2, (gx,gy,gz) rad/s)。失敗はNone。"""
        with self._lock:
            lib = self._lib
            handle = self._handle
        if lib is None or handle is None:
            return None
        try:
            import pygame as _pg

            _pg.event.pump()
        except Exception:
            pass
        try:
            lib.SDL_GameControllerUpdate()
        except Exception:
            pass
        try:
            accel = (ctypes.c_float * 3)()
            gyro = (ctypes.c_float * 3)()
            if (
                lib.SDL_GameControllerGetSensorData(handle, SDL_SENSOR_ACCEL, accel, 3)
                != 0
            ):
                return None
            if (
                lib.SDL_GameControllerGetSensorData(handle, SDL_SENSOR_GYRO, gyro, 3)
                != 0
            ):
                return None
            return (
                (float(accel[0]), float(accel[1]), float(accel[2])),
                (float(gyro[0]), float(gyro[1]), float(gyro[2])),
            )
        except Exception:
            return None

    def read(self) -> tuple[int, int, int, int, int, int] | None:
        """量子化済み6値を読む。軸変換・バイアス補正つき。失敗はNone。"""
        raw = self.read_raw()
        if raw is None:
            return None
        (ax, ay, az), (gx, gy, gz) = raw
        acc = (ax, ay, az)
        gyr = (gx, gy, gz)
        try:
            ca = tuple(
                acc[self._accel_axes[i]] * self._accel_signs[i] for i in range(3)
            )
            cg = tuple(
                (gyr[self._gyro_axes[i]] - self._gyro_bias[i]) * self._gyro_signs[i]
                for i in range(3)
            )
        except (IndexError, TypeError):
            return None
        return (
            accel_to_raw(ca[0]),
            accel_to_raw(ca[1]),
            accel_to_raw(ca[2]),
            gyro_to_raw(cg[0]),
            gyro_to_raw(cg[1]),
            gyro_to_raw(cg[2]),
        )

    def set_axis_map(
        self,
        accel_axes: tuple[int, int, int],
        accel_signs: tuple[int, int, int],
        gyro_axes: tuple[int, int, int],
        gyro_signs: tuple[int, int, int],
    ) -> None:
        """軸変換を変える。不正値は無視する。"""
        try:
            aa = tuple(int(v) for v in accel_axes)
            as_ = tuple(int(v) for v in accel_signs)
            ga = tuple(int(v) for v in gyro_axes)
            gs = tuple(int(v) for v in gyro_signs)
            if (
                len(aa) != 3
                or len(as_) != 3
                or len(ga) != 3
                or len(gs) != 3
                or any(v not in (0, 1, 2) for v in aa + ga)
                or any(v not in (-1, 1) for v in as_ + gs)
            ):
                return
            self._accel_axes = aa
            self._accel_signs = as_
            self._gyro_axes = ga
            self._gyro_signs = gs
        except (TypeError, ValueError):
            pass

    def set_gyro_bias(self, bias: tuple[float, float, float]) -> None:
        """ジャイロバイアス(rad/s)を変える。不正値は無視する。"""
        try:
            b = tuple(float(v) for v in bias)
            if len(b) != 3 or not all(math.isfinite(v) for v in b):
                return
            self._gyro_bias = b
        except (TypeError, ValueError):
            pass
