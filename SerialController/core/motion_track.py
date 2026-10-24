#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""motion_track.py - ジャイロ軌跡の記録と再生（Tkなし）。

.pgyro形式: Switch量子化済みのTargetImuSample相当（6xint16-LE）を
200Hz固定周期で並べたバイナリ。入力デバイスに依存しない再生が目的で、
Picoへ送る値と記録値が一致する。再生時は通常のIMU_SAMPLEとして送る。

形式:
    ヘッダー 32B:
        magic[4]  "PGYR"
        version   u16-LE (=1)
        header_size u16-LE (=32)
        period_us u32-LE (=5000)
        sample_count u32-LE
        flags     u32-LE (0)
        start_offset_us u64-LE (ボタン記録との同期用。単体では0)
        reserved  u32-LE (0)
    サンプル N件 x 12B:
        ax ay az gx gy gz (int16-LE x6)

使い方:
    with MotionRecorder("rec.pgyro") as rec:
        rec.write(sample)  # 6値タプル
    for sample in MotionPlayer("rec.pgyro"):
        transport.send_imu(*sample)
"""

from __future__ import annotations

import os
import struct
import threading
from collections.abc import Callable, Iterator
from typing import BinaryIO

PGYRO_MAGIC = b"PGYR"
PGYRO_VERSION = 1
PGYRO_HEADER_SIZE = 32
PGYRO_PERIOD_US = 5000

_HEADER_STRUCT = struct.Struct("<4sHHIIIQI")
_SAMPLE_STRUCT = struct.Struct("<6h")


class MotionRecorder:
    """軌跡の記録器。追記専用。スレッドセーフ。"""

    def __init__(self, path: str, start_offset_us: int = 0) -> None:
        self._path = str(path)
        self._start_offset_us = int(start_offset_us)
        self._lock = threading.Lock()
        self._fh: BinaryIO | None = None
        self._count = 0

    def open(self) -> None:
        """記録を開始する。既存ファイルは上書きする。"""
        with self._lock:
            self._close_locked()
            directory = os.path.dirname(os.path.abspath(self._path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._fh = open(self._path, "wb")
            # 仮ヘッダー（count=0）。close時に書き直す。
            self._fh.write(self._header_bytes(0))
            self._count = 0

    def _header_bytes(self, count: int) -> bytes:
        """count件分のヘッダーを組む。"""
        return _HEADER_STRUCT.pack(
            PGYRO_MAGIC,
            PGYRO_VERSION,
            PGYRO_HEADER_SIZE,
            PGYRO_PERIOD_US,
            int(count),
            0,
            self._start_offset_us,
            0,
        )

    def write(self, sample: tuple[int, int, int, int, int, int]) -> bool:
        """1件書く。開いていなければFalse。例外は投げない。"""
        try:
            ax, ay, az, gx, gy, gz = (int(v) for v in sample)
        except (TypeError, ValueError):
            return False
        vals = tuple(max(-32768, min(32767, v)) for v in (ax, ay, az, gx, gy, gz))
        with self._lock:
            if self._fh is None:
                return False
            try:
                self._fh.write(_SAMPLE_STRUCT.pack(*vals))
                self._count += 1
                return True
            except OSError:
                return False

    @property
    def count(self) -> int:
        """書いた件数。"""
        with self._lock:
            return self._count

    def _close_locked(self) -> None:
        """錠内で閉じる。ヘッダーを件数で書き直す。"""
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            fh.write(self._header_bytes(self._count))
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass

    def close(self) -> None:
        """記録を終える。例外は投げない。"""
        with self._lock:
            self._close_locked()

    def __enter__(self) -> MotionRecorder:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class MotionPlayer:
    """軌跡の再生器。読取専用。"""

    def __init__(self, path: str) -> None:
        self._path = str(path)
        self._samples: list[tuple[int, int, int, int, int, int]] = []
        self._start_offset_us = 0
        self._loaded = False
        self._error = ""

    @property
    def error(self) -> str:
        """読込失敗の理由。成功時は空。"""
        return self._error

    @property
    def start_offset_us(self) -> int:
        """ボタン記録との同期用オフセット。"""
        return self._start_offset_us

    def load(self) -> bool:
        """全件読む。成功で True。失敗はFalse＋error。"""
        self._samples = []
        self._error = ""
        try:
            with open(self._path, "rb") as fh:
                header = fh.read(PGYRO_HEADER_SIZE)
                if len(header) < PGYRO_HEADER_SIZE:
                    self._error = "ヘッダーが短い"
                    return False
                (
                    magic,
                    version,
                    header_size,
                    period_us,
                    count,
                    _flags,
                    start_offset,
                    _reserved,
                ) = _HEADER_STRUCT.unpack(header)
                if magic != PGYRO_MAGIC:
                    self._error = "magic不一致"
                    return False
                if version != PGYRO_VERSION:
                    self._error = f"version不一致: {version}"
                    return False
                if header_size != PGYRO_HEADER_SIZE:
                    self._error = f"header_size不一致: {header_size}"
                    return False
                if period_us != PGYRO_PERIOD_US:
                    self._error = f"period不一致: {period_us}"
                    return False
                body = fh.read()
                want = int(count) * _SAMPLE_STRUCT.size
                if len(body) < want:
                    self._error = f"本体不足: {len(body)}<{want}"
                    return False
                samples = []
                for i in range(int(count)):
                    chunk = body[i * 12 : (i + 1) * 12]
                    samples.append(_SAMPLE_STRUCT.unpack(chunk))
                self._samples = samples
                self._start_offset_us = int(start_offset)
                self._loaded = True
                return True
        except FileNotFoundError:
            self._error = "ファイルが無い"
            return False
        except OSError as e:
            self._error = f"読込失敗: {e}"
            return False
        except struct.error as e:
            self._error = f"解釈失敗: {e}"
            return False

    def __len__(self) -> int:
        return len(self._samples)

    def __iter__(self) -> Iterator[tuple[int, int, int, int, int, int]]:
        return iter(self._samples)

    def as_generator(self) -> Iterator[tuple[int, int, int, int, int, int] | None]:
        """IMU loop用の生成器。終端後はNoneを返し続ける。"""
        yield from self._samples
        while True:
            yield None

    def as_source(self) -> Callable[[], tuple[int, int, int, int, int, int] | None]:
        """IMU loop用の呼出口。終端後はNoneを返し続ける。"""
        iterator = self.as_generator()
        return lambda: next(iterator)
