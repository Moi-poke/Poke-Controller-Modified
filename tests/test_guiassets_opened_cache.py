#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live_sender の opened 判定 TTL キャッシュの検証（Tk なしで回す）。

背景: マウススティックの Motion は 8ms 間引き後も約125Hzで届き、
従来は1件ごとに live_sender() が ser.isOpened() を呼んでいた。
switch-bcon-proc では is_open が同期 RPC（最大8秒級）のため、
イベントの嵐 × ブロック RPC で GUI が数十秒固まる。
TTL キャッシュで照会を O(1) に畳む。離す操作だけは必ず再照会し、
中立への復帰を取りこぼさない。
"""

from __future__ import annotations

from collections.abc import Iterator

import GuiAssets
import pytest
from GuiAssets import CaptureArea


class _CountingFake:
    """isOpened の呼び出し回数を数える送り先。"""

    def __init__(self, opened: bool = True) -> None:
        self.opened = opened
        self.is_opened_calls = 0
        self.raise_on_opened = False
        self.sticks: list[tuple[str, int, int]] = []
        self.postures = 0

    def isOpened(self) -> bool:
        """開いているか。呼ぶたびに数える（RPC の代わり）。"""
        self.is_opened_calls += 1
        if self.raise_on_opened:
            raise RuntimeError("dead transport")
        return self.opened

    def setStick(self, side: str, x: int, y: int, source: str = "") -> bool:
        """スティック値を受ける。常に受け付ける。"""
        self.sticks.append((side, x, y))
        return True

    def sendPosture(self, source: str = "") -> None:
        """姿勢を送る。呼んだ回数だけ数える。"""
        self.postures += 1


def _make_area(ser: _CountingFake) -> CaptureArea:
    # __init__（実 Tk 生成）を避け、送出に必要な属性だけ持たせる。
    area = CaptureArea.__new__(CaptureArea)
    area.ser = ser  # type: ignore[attr-defined]
    area.radius = 60  # type: ignore[attr-defined]
    area._motion_last = {}  # type: ignore[attr-defined]
    area._last_sent = 0.0  # type: ignore[attr-defined]
    area._last_sent_mag = 0.0  # type: ignore[attr-defined]
    return area


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    """送り先ごとの opened キャッシュを毎回空にする（相互干渉の防止）。"""
    cache = getattr(GuiAssets, "_SER_OPENED_CACHE", None)
    if cache is not None:
        cache.clear()
    yield
    cache = getattr(GuiAssets, "_SER_OPENED_CACHE", None)
    if cache is not None:
        cache.clear()


def test_motion_storm_queries_opened_once() -> None:
    """N 回の motion 送出で isOpened 照会は1回だけ（TTL 内は使い回す）。"""
    fake = _CountingFake(opened=True)
    area = _make_area(fake)
    for _ in range(50):
        area._sendStick("L", 45.0, 0.5)
    assert fake.is_opened_calls == 1
    assert len(fake.sticks) == 50


def test_release_delivers_neutral() -> None:
    """離す操作は必ず中立 (128, 128) を setStick + sendPosture で届ける。"""
    fake = _CountingFake(opened=True)
    area = _make_area(fake)
    area._sendNeutralStick("L")
    assert ("L", 128, 128) in fake.sticks
    assert fake.postures == 1


def test_ttl_expiry_requeries(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL を過ぎたら再照会する（古い True に張り付かない）。"""
    now = [1000.0]
    monkeypatch.setattr(GuiAssets, "_ser_opened_clock", lambda: now[0])
    fake = _CountingFake(opened=True)
    area = _make_area(fake)
    area._sendStick("L", 45.0, 0.5)
    assert fake.is_opened_calls == 1
    area._sendStick("L", 45.0, 0.5)
    assert fake.is_opened_calls == 1
    now[0] += GuiAssets.SER_OPENED_TTL_S + 0.1
    area._sendStick("L", 45.0, 0.5)
    assert fake.is_opened_calls == 2


def test_opened_raising_skips_send_silently() -> None:
    """isOpened が投げても Tk へ逃がさず、送信だけ黙ってやめる。"""
    fake = _CountingFake()
    fake.raise_on_opened = True
    area = _make_area(fake)
    area._sendStick("L", 45.0, 0.5)
    area._sendNeutralStick("L")
    assert fake.sticks == []
    assert fake.postures == 0


def test_force_refresh_bypasses_cache() -> None:
    """_force_refresh=True は TTL 内でも再照会する（press/release 用）。"""
    fake = _CountingFake(opened=True)
    assert GuiAssets.live_sender(fake) is fake
    assert fake.is_opened_calls == 1
    assert GuiAssets.live_sender(fake) is fake
    assert fake.is_opened_calls == 1
    assert GuiAssets.live_sender(fake, _force_refresh=True) is fake
    assert fake.is_opened_calls == 2


def test_release_sees_fresh_close() -> None:
    """TTL 内の True が残っていても release は閉を再確認して送らない。"""
    fake = _CountingFake(opened=True)
    area = _make_area(fake)
    area._sendStick("L", 45.0, 0.5)
    assert fake.is_opened_calls == 1
    fake.sticks.clear()
    fake.postures = 0
    fake.opened = False
    area._sendNeutralStick("L")
    assert fake.is_opened_calls == 2
    assert fake.sticks == []
    assert fake.postures == 0
