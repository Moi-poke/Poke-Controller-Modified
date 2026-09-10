#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待機時高速路（空キューでTcl往復を省く）の検証。"""

from __future__ import annotations

import pytest

tkinter = pytest.importorskip("tkinter")


class _FakeVar:
    def __init__(self, value: bool = True) -> None:
        self._v = value
        self.get_calls = 0

    def get(self) -> bool:
        self.get_calls += 1
        return self._v


class _FakeArea:
    def __init__(self) -> None:
        self.yview_calls = 0
        self.after_calls = 0

    def yview(self) -> tuple[float, float]:
        self.yview_calls += 1
        return (0.0, 1.0)

    def after(self, _ms: int, _cb: object) -> str:
        self.after_calls += 1
        return "after-id"


class _FakeLogPanelHost:
    pass


def test_display_text_idle_skips_tcl_but_keeps_stats() -> None:
    """全キュー空ならVar取得等のTclを省き、統計（_log_flush_ms等）は保つ。"""
    import LogPane
    from ui import log_panel

    # 空キューを用意（本物の空DropOldestQueue）。
    LogPane.text_queue = LogPane.DropOldestQueue(maxsize=100)
    LogPane.sub_log_queue = LogPane.DropOldestQueue(maxsize=100)
    LogPane.input_log_queue = LogPane.DropOldestQueue(maxsize=100)

    host = _FakeLogPanelHost()
    host._closing = False  # type: ignore[attr-defined]
    host._log_flush_ms = 0.0  # type: ignore[attr-defined]
    host._log_flush_max_ms = 0.0  # type: ignore[attr-defined]
    host._pump_gap_max_ms = 0.0  # type: ignore[attr-defined]
    host._pump_last_at = None  # type: ignore[attr-defined]
    host._stat_ticks = 0  # type: ignore[attr-defined]
    host._display_after_id = None  # type: ignore[attr-defined]
    host.log_autoscroll = _FakeVar(True)  # type: ignore[attr-defined]
    host.show_input_log = _FakeVar(False)  # type: ignore[attr-defined]
    host.logArea = _FakeArea()  # type: ignore[attr-defined]
    host.subLogArea = _FakeArea()  # type: ignore[attr-defined]
    host.inputLogArea = _FakeArea()  # type: ignore[attr-defined]
    host.camera = None  # type: ignore[attr-defined]
    host.preview = None  # type: ignore[attr-defined]
    host.runner = type("R", (), {"state": "idle"})()  # type: ignore[attr-defined]
    # display_textが呼ぶ補助（Tclなしの stub）。
    host._log_video_stats = lambda: None  # type: ignore[attr-defined]
    host.display_text = lambda: None  # type: ignore[attr-defined]

    class FakeSerial:
        def is_open(self) -> bool:
            return False

    host.serial = FakeSerial()  # type: ignore[attr-defined]

    # 高速路：空ならVar.get（Tcl往復）を省く。現状は毎回呼ぶためRED。
    log_panel.LogPanelMixin.display_text(host)  # type: ignore[arg-type]
    assert host.log_autoscroll.get_calls == 0, "空なのにVar取得しています"  # type: ignore[attr-defined]
    assert host.show_input_log.get_calls == 0  # type: ignore[attr-defined]
    # 統計は生きている（ポンプ間隔の最大だけは初回None→設定なしでも属性あり）。
    assert hasattr(host, "_log_flush_ms")
    # after予約は次周期のため残す。
    assert host.logArea.after_calls == 1  # type: ignore[attr-defined]


def test_serial_monitor_idle_skips_status_tcl() -> None:
    """SerialMonitor空キューではstatusのTcl更新を省く（振る舞いは不変）。"""
    from SerialMonitor import SerialMonitor

    mon = SerialMonitor.__new__(SerialMonitor)
    mon._stop = False  # type: ignore[attr-defined]
    mon._closed = False  # type: ignore[attr-defined]
    mon._paused = False  # type: ignore[attr-defined]
    mon._bound = (
        object()
    )  # _ensure_subscribedを素通りさせるダミー # type: ignore[attr-defined]
    mon._noted_missing = True  # 注記行の混入を防ぐ # type: ignore[attr-defined]
    mon._shown = 0  # type: ignore[attr-defined]
    mon._hidden = 0  # type: ignore[attr-defined]
    mon._dropped = 0  # type: ignore[attr-defined]
    from LogPane import DropOldestQueue as _DQ

    mon._queue = _DQ(maxsize=100)  # type: ignore[attr-defined]

    status_sets = {"n": 0}

    class FakeStatus:
        def set(self, _s: str) -> None:
            status_sets["n"] += 1

    mon._status = FakeStatus()  # type: ignore[attr-defined]

    class FakeFilter:
        def get(self) -> str:
            return "通常のみ"

    mon._filter = FakeFilter()  # type: ignore[attr-defined]

    class FakeLog:
        def __init__(self) -> None:
            self.see_calls = 0

        def configure(self, *a: object, **k: object) -> None:
            pass

        def insert(self, *a: object, **k: object) -> None:
            pass

        def index(self, *a: object) -> str:
            return "1.0"

        def see(self, *a: object) -> None:
            self.see_calls += 1

    mon._log = FakeLog()  # type: ignore[attr-defined]

    after_n = {"n": 0}

    class FakeWin:
        def after(self, _ms: int, _cb: object) -> str:
            after_n["n"] += 1
            return "id"

    mon.window = FakeWin()  # type: ignore[attr-defined]
    # 購読の結び直しを無効化（輸送層なしの単体検証）。
    mon._ensure_subscribed = lambda: None  # type: ignore[method-assign]

    mon._poll()
    mon._poll()
    # 空の連打で毎回status.set（Tcl）しないのが高速路。現状は毎回呼ぶためRED。
    assert status_sets["n"] <= 1, (
        f"空なのにstatus更新しています: {status_sets['n']}回/2 polls"
    )
    assert after_n["n"] == 2
