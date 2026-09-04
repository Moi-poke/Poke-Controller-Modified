#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WakeLink.py - wakecon との応答つき通信。GUI 設定画面と python コマンドの共有口。

PokeCon はふだん線を読まない（送りっぱなし）。ここでの読み取りは
wake の設定・確認の場面だけに使う。呼び出し側は GUI スレッド以外で
呼ぶこと。Tk の変数には触らない。

live worker が S 行を流し続けていても構わない。読みに出るのは
ファームからの応答行だけであり、書きは1行単位で噛み合う。
"""

from __future__ import annotations

import time
import traceback
from logging import getLogger, DEBUG, NullHandler
from typing import Any, List, Optional

_logger = getLogger(__name__)
_logger.addHandler(NullHandler())
_logger.setLevel(DEBUG)
_logger.propagate = True


def _ser_of(transport: Any) -> Any:
    """Transport が持つ pyserial を返す。無ければ None。"""
    ser = getattr(transport, "ser", None)
    if ser is None:
        return None
    if getattr(ser, "isOpen", lambda: False)() is False:
        return None
    return ser


def _lock_of(transport: Any) -> Any:
    """書き込みの錠。無ければ何もしない文脈管理を返す。"""

    class _NullLock:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: Any) -> bool:
            return False

    lock = getattr(transport, "_lock", None)
    if lock is None:
        return _NullLock()
    return lock


def send_line(transport: Any, line: str, timeout: float = 2.0) -> bool:
    """1行送る。送れたら True。例外は出さない。"""
    _ = timeout
    try:
        ser = _ser_of(transport)
        if ser is None:
            return False
        data = (line + "\r\n").encode("ascii")
        with _lock_of(transport):
            ser.write(data)
        return True
    except Exception:
        _logger.error(f"WakeLink send failed: {traceback.format_exc()}")
        return False


def read_lines(transport: Any, duration: float) -> List[str]:
    """duration 秒だけ読み、行の一覧を返す。例外は出さない。"""
    out: List[str] = []
    try:
        ser = _ser_of(transport)
        if ser is None:
            return out
        deadline = time.perf_counter() + max(0.0, float(duration))
        buf = bytearray()
        while time.perf_counter() < deadline:
            try:
                chunk = ser.read(64)
            except Exception:
                break
            if not chunk:
                continue
            buf.extend(chunk)
            while True:
                idx = buf.find(b"\n")
                if idx < 0:
                    break
                raw = bytes(buf[:idx])
                del buf[:idx + 1]
                try:
                    out.append(raw.decode("ascii", errors="replace").strip())
                except Exception:
                    continue
        return out
    except Exception:
        _logger.error(f"WakeLink read failed: {traceback.format_exc()}")
        return out


def drain(transport: Any) -> None:
    """受信の残りを捨てる。応答待ちの前に呼ぶ。"""
    try:
        ser = _ser_of(transport)
        if ser is None:
            return
        reset = getattr(ser, "reset_input_buffer", None)
        if callable(reset):
            reset()
    except Exception:
        pass


def query(transport: Any, line: str, prefixes: Any,
          timeout: float = 3.0) -> List[str]:
    """1行送り、prefixes に合う応答行だけ集めて返す。

    prefixes は先頭一致の文字列かその並び。live の S 行は送るだけで
    読みには出ないため、拾うのはファームの応答だけになる。
    """
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    drain(transport)
    if not send_line(transport, line):
        return []
    found: List[str] = []
    for text in read_lines(transport, timeout):
        for prefix in prefixes:
            if text.startswith(prefix):
                found.append(text)
                break
    return found
