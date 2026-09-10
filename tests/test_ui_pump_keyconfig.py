#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KeyConfig afterガードの検証（pynputスレッドからのTclError対策）。"""

from __future__ import annotations

import pytest

tkinter = pytest.importorskip("tkinter")

from KeyConfig import PokeKeycon, _button  # noqa: E402


class _FakeKc:
    """afterがTclErrorを投げる破棄済みダイアログの再現。"""

    def __init__(self, exc: BaseException | None = None) -> None:
        self.calls = 0
        self._exc = exc

    def after(self, _ms: int, _cb: object) -> None:
        self.calls += 1
        if self._exc is not None:
            raise self._exc


class _FakeKey:
    def __init__(self, char: str | None = None, name: str | None = None) -> None:
        self.char = char
        self.name = name


def _make_obj(kc: object) -> PokeKeycon:
    # __init__（実Tk生成）を避け、_on_pressに必要な属性だけ持たせる。
    obj = PokeKeycon.__new__(PokeKeycon)
    obj.capturing = _button("A", "A", "#ff4554")  # type: ignore[attr-defined]
    obj.kc = kc  # type: ignore[attr-defined]
    return obj


def test_on_press_after_tclerror_exits_cleanly() -> None:
    """ダイアログ破棄 mid-capture：afterのTclErrorを握って静かに抜ける。"""
    obj = _make_obj(_FakeKc(exc=tkinter.TclError("invalid command")))
    # 例外が出たらテスト失敗（握り潰しが仕様）。
    obj._on_press(_FakeKey(char="a"))
    assert obj.kc.calls == 1  # type: ignore[attr-defined]


def test_on_press_blocked_key_does_not_schedule() -> None:
    """Tab/Esc/Enterは割り当て対象外でafterを積まない（既存の振る舞い維持）。"""
    obj = _make_obj(_FakeKc())
    # Key.tab相当（charなし・nameあり）はBLOCKED_KEYSに当たる。
    obj._on_press(_FakeKey(char=None, name="tab"))
    assert obj.kc.calls == 0  # type: ignore[attr-defined]
