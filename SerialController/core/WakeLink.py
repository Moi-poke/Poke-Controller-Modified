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
from logging import DEBUG, NullHandler, getLogger
from typing import Any

_logger = getLogger(__name__)
_logger.addHandler(NullHandler())
_logger.setLevel(DEBUG)
_logger.propagate = True


def _ser_of(transport: Any) -> Any:
    """Transport が持つ pyserial を返す。無ければ None。

    新しい接続方式（HID/BLE/TCP 等）は ser を持たない。その場合は
    Transport.get_raw_serial() が None を返す。ここでは例外を出さず
    None を返し、呼び出し側は従来経路へフォールバックする。
    """
    getter = getattr(transport, "get_raw_serial", None)
    if callable(getter):
        try:
            ser = getter()
        except Exception:
            _logger.debug("get_raw_serial に失敗", exc_info=True)
            return None
    else:
        ser = getattr(transport, "ser", None)
    if ser is None:
        return None
    is_open = getattr(transport, "is_open", None)
    if callable(is_open):
        # 開閉の判断は線の持ち主（Transport）に任せる。
        # pyserial 直の判定は、Transport に聞けない場合の予備である。
        try:
            if not is_open():
                return None
        except Exception:
            return None
        return ser
    if getattr(ser, "isOpen", lambda: False)() is False:
        return None
    return ser


def _lock_of(transport: Any) -> Any:
    """書き込みの錠。無ければ何もしない文脈管理を返す。

    Transport.acquire_write_lock() があればそちらを優先する。
    直に ._lock を触らない（非シリアル方式は錠を持たない場合がある）。
    """

    class _NullLock:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: Any) -> None:
            return None

    getter = getattr(transport, "acquire_write_lock", None)
    if callable(getter):
        try:
            lock = getter()
            if lock is not None:
                return lock
        except Exception:
            _logger.debug("acquire_write_lock に失敗", exc_info=True)
        return _NullLock()
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


def read_lines(transport: Any, duration: float) -> list[str]:
    """duration 秒だけ読み、行の一覧を返す。例外は出さない。"""
    out: list[str] = []
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
                del buf[: idx + 1]
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
        # 捨てるだけなので落とさない。ファイル側にだけ残す。
        _logger.debug("drain に失敗", exc_info=True)


def query(transport: Any, line: str, prefixes: Any, timeout: float = 3.0) -> list[str]:
    """1行送り、prefixes に合う応答行だけ集めて返す。

    prefixes は先頭一致の文字列かその並び。live の S 行は送るだけで
    読みには出ないため、拾うのはファームの応答だけになる。
    """
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    drain(transport)
    if not send_line(transport, line):
        return []
    found: list[str] = []
    for text in read_lines(transport, timeout):
        for prefix in prefixes:
            if text.startswith(prefix):
                found.append(text)
                break
    return found


def expect(
    transport: Any, line: str, prefixes: Any, timeout: float = 0.5
) -> str | None:
    """1行送り、合致する最初の応答行を待つ。見つかればその行を返す。

    query が期限いっぱいまで集めるのに対し、こちらは1件見つかり次第
    戻る。QOK / QRUN のような即応の確認に向く。見つからなければ None。
    呼び出しは作業スレッドで行い、Tk の変数には触らない（query と同じ）。
    """
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    drain(transport)
    if not send_line(transport, line):
        return None
    ser = _ser_of(transport)
    if ser is None:
        return None
    deadline = time.perf_counter() + max(0.0, float(timeout))
    buf = bytearray()
    try:
        while time.perf_counter() < deadline:
            try:
                chunk = ser.read(64)
            except Exception:
                break
            if chunk:
                buf.extend(chunk)
                while True:
                    idx = buf.find(b"\n")
                    if idx < 0:
                        break
                    raw = bytes(buf[:idx])
                    del buf[: idx + 1]
                    try:
                        text = raw.decode("ascii", errors="replace").strip()
                    except Exception:
                        continue
                    for prefix in prefixes:
                        if text.startswith(prefix):
                            return text
    except Exception:
        _logger.error(f"WakeLink expect failed: {traceback.format_exc()}")
    return None
