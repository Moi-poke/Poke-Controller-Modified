#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tk が運搬層 RPC で固まらないことの検証。

switch-bcon-proc の is_open は子プロセス往復（最大で約8秒級）であり、
Tk スレッド（200ms ポンプ・タイトル更新・ボタン）から同期呼びすると
1回の停滞がポンプ間隔の崩れとしてそのまま出る。Tk は IPC を待っては
いけない。8秒停滞する偽送り先で以下を確かめる（実 tk は作らない）。

(a) display_text が約200msで戻り、ポンプ間隔が崩れないこと。
(b) _update_title が約200msで戻ること。
(c) applyBconWired/Emulate が即座に戻り、setter が裏スレッドで
    呼ばれ、結果の反映が root.after(0) 経由であること。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

tkinter = pytest.importorskip("tkinter")

# 子往復の上限の目安（3.0秒 + PROC_RPC_MARGIN_S 5.0秒）。
STALL_S = 8.0
# 停滞と即時復帰を見分ける上限。即時なら数ms、停滞なら8秒のため
# 余裕を持たせても判定は揺らがない。
FAST_S = 2.0


class _StalledSender:
    """isOpened だけが8秒停滞する偽送り先。"""

    def __init__(self) -> None:
        self.flushed = 0

    def isOpened(self) -> bool:
        time.sleep(STALL_S)
        return True

    def flushInputLog(self) -> None:
        self.flushed += 1


class _FakeVar:
    def __init__(self, value: Any) -> None:
        self._v = value

    def get(self) -> Any:
        return self._v


class _FakeArea:
    def __init__(self) -> None:
        self.after_calls = 0

    def after(self, _ms: int, _cb: object) -> str:
        self.after_calls += 1
        return "after-id"


class _FakeRoot:
    def __init__(self) -> None:
        self.titles: list[str] = []
        self.afters: list[tuple[int, Any]] = []

    def title(self, *args: Any) -> None:
        if args:
            self.titles.append(str(args[0]))

    def after(self, ms: int, cb: object) -> str:
        self.afters.append((int(ms), cb))
        return "after-0"


def _make_service(tmp_path: Any) -> Any:
    from services.serial_service import SerialService

    service = SerialService(
        notify_user=lambda _m: None,
        base_dir=str(tmp_path),
        input_log_emit=None,
    )
    service.sender = _StalledSender()  # type: ignore[assignment]
    return service


def _make_log_host(service: Any) -> Any:
    from ui import log_panel

    host = log_panel.LogPanelMixin.__new__(log_panel.LogPanelMixin)
    host._closing = False  # type: ignore[attr-defined]
    host._log_flush_ms = 0.0  # type: ignore[attr-defined]
    host._log_flush_max_ms = 0.0  # type: ignore[attr-defined]
    host._pump_gap_max_ms = 0.0  # type: ignore[attr-defined]
    host._pump_last_at = None  # type: ignore[attr-defined]
    host._stat_ticks = 0  # type: ignore[attr-defined]
    host._display_after_id = None  # type: ignore[attr-defined]
    host.log_autoscroll = _FakeVar(True)  # type: ignore[attr-defined]
    host.show_input_log = _FakeVar(True)  # type: ignore[attr-defined]
    host.logArea = _FakeArea()  # type: ignore[attr-defined]
    host.subLogArea = _FakeArea()  # type: ignore[attr-defined]
    host.inputLogArea = _FakeArea()  # type: ignore[attr-defined]
    host.camera = None  # type: ignore[attr-defined]
    host.preview = None  # type: ignore[attr-defined]
    host.runner = type("R", (), {"state": "idle"})()  # type: ignore[attr-defined]
    host._log_video_stats = lambda: None  # type: ignore[attr-defined]
    host.serial = service  # type: ignore[attr-defined]
    return host


def test_display_text_returns_fast_with_stalled_transport(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """表示ポンプが8秒停滞に引きずられないこと。"""
    import LogPane
    from ui import log_panel

    service = _make_service(tmp_path)
    host = _make_log_host(service)

    # 空だと高速路で serial を触らずに終わるため、わざと1行積んで
    # is_open を通る経路（flush 側）へ回す。描画自体は stub する。
    monkeypatch.setattr(LogPane, "text_queue", LogPane.DropOldestQueue(maxsize=100))
    monkeypatch.setattr(LogPane, "sub_log_queue", LogPane.DropOldestQueue(maxsize=100))
    monkeypatch.setattr(
        LogPane, "input_log_queue", LogPane.DropOldestQueue(maxsize=100)
    )
    LogPane.text_queue.put("probe")
    monkeypatch.setattr(LogPane, "flushQueue", lambda _q, _w, _f: None)

    began = time.perf_counter()
    log_panel.LogPanelMixin.display_text(host)  # type: ignore[arg-type]
    elapsed = time.perf_counter() - began
    assert elapsed < FAST_S, f"display_text が停滞しました: {elapsed:.1f}秒"

    began = time.perf_counter()
    log_panel.LogPanelMixin.display_text(host)  # type: ignore[arg-type]
    elapsed2 = time.perf_counter() - began
    assert elapsed2 < FAST_S, f"display_text が停滞しました: {elapsed2:.1f}秒"
    gap_ms = float(host._pump_gap_max_ms)  # type: ignore[attr-defined]
    assert gap_ms < FAST_S * 1000.0, f"ポンプ間隔が崩れました: {gap_ms:.0f}ms"


def test_update_title_returns_fast_with_stalled_transport(tmp_path: Any) -> None:
    """タイトル更新が8秒停滞に引きずられないこと。"""
    import Window

    service = _make_service(tmp_path)
    host = Window.PokeControllerApp.__new__(Window.PokeControllerApp)
    host.profile = ""  # type: ignore[attr-defined]
    host.com_port_name = _FakeVar("COM9")  # type: ignore[attr-defined]
    host.serial = service  # type: ignore[attr-defined]
    host._running_command = ""  # type: ignore[attr-defined]
    host._paused = False  # type: ignore[attr-defined]
    host.runner = type("R", (), {"stop_waited": 0})()  # type: ignore[attr-defined]
    host.root = _FakeRoot()  # type: ignore[attr-defined]

    began = time.perf_counter()
    Window.PokeControllerApp._update_title(host)  # type: ignore[arg-type]
    elapsed = time.perf_counter() - began
    assert elapsed < FAST_S, f"_update_title が停滞しました: {elapsed:.1f}秒"
    assert host.root.titles  # type: ignore[attr-defined]


class _StalledBconTransport:
    """setter だけが8秒停滞する偽 bcon 運搬器。

    停滞は Event 待ちで表す（最大 STALL_S 秒）。呼び出しの記録だけ
    先に行うため、裏スレッド化の有無を速く判定できる。
    """

    def __init__(self) -> None:
        self.wired_calls: list[Any] = []
        self.emulate_calls: list[Any] = []
        self.release = threading.Event()

    def set_wired_mode(self, enable: Any, timeout: float = 1.0) -> None:
        self.wired_calls.append((enable, timeout))
        self.release.wait(STALL_S)

    def set_emulate_mode(self, role: Any, timeout: float = 1.0) -> None:
        self.emulate_calls.append((role, timeout))
        self.release.wait(STALL_S)


def _wait_for(pred: Any, timeout: float = 10.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return bool(pred())


def test_apply_bcon_wired_runs_setter_off_tk_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有線/無線切替が Tk を止めず、反映は after(0) 経由であること。"""
    import tkinter.messagebox as tkmsg

    from ui import serial_panel

    fake = _StalledBconTransport()
    host = serial_panel.SerialPanelMixin.__new__(serial_panel.SerialPanelMixin)
    host._bcon_transport = lambda: fake  # type: ignore[method-assign]
    host.bcon_wired = _FakeVar("有線")  # type: ignore[attr-defined]
    host.root = _FakeRoot()  # type: ignore[attr-defined]
    refreshed: list[bool] = []
    host._refresh_bcon_rows = lambda: refreshed.append(True)  # type: ignore[method-assign]
    monkeypatch.setattr(tkmsg, "askquestion", lambda *a: "yes")

    began = time.perf_counter()
    serial_panel.SerialPanelMixin.applyBconWired(host)  # type: ignore[arg-type]
    elapsed = time.perf_counter() - began
    assert elapsed < FAST_S, f"applyBconWired が停滞しました: {elapsed:.1f}秒"
    # 裏で setter が呼ばれたこと（Tk は待たない）。
    assert _wait_for(lambda: len(fake.wired_calls) == 1), "裏で setter が呼ばれません"
    # setter の完了前は反映の予約もないこと（Tk へ触らない）。
    assert host.root.afters == [], "setter 完了前に Tk へ触っています"  # type: ignore[attr-defined]
    fake.release.set()
    # 反映は Tk スレッドへの予約経由であること。
    assert _wait_for(lambda: len(host.root.afters) > 0), "after(0) 予約がありません"  # type: ignore[attr-defined]
    for ms, cb in list(host.root.afters):  # type: ignore[attr-defined]
        assert ms == 0
        cb()
    assert refreshed, "after(0) で _refresh_bcon_rows が呼ばれません"


def test_apply_bcon_emulate_runs_setter_off_tk_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """種別切替が Tk を止めず、反映は after(0) 経由であること。"""
    import tkinter.messagebox as tkmsg

    from ui import serial_panel

    fake = _StalledBconTransport()
    host = serial_panel.SerialPanelMixin.__new__(serial_panel.SerialPanelMixin)
    host._bcon_transport = lambda: fake  # type: ignore[method-assign]
    host.bcon_emulate = _FakeVar("Joy-Con (L)")  # type: ignore[attr-defined]
    host.root = _FakeRoot()  # type: ignore[attr-defined]
    refreshed: list[bool] = []
    host._refresh_bcon_rows = lambda: refreshed.append(True)  # type: ignore[method-assign]
    monkeypatch.setattr(tkmsg, "askquestion", lambda *a: "yes")

    began = time.perf_counter()
    serial_panel.SerialPanelMixin.applyBconEmulate(host)  # type: ignore[arg-type]
    elapsed = time.perf_counter() - began
    assert elapsed < FAST_S, f"applyBconEmulate が停滞しました: {elapsed:.1f}秒"
    assert _wait_for(lambda: len(fake.emulate_calls) == 1), "裏で setter が呼ばれません"
    assert fake.emulate_calls[0][0] == 1
    assert host.root.afters == [], "setter 完了前に Tk へ触っています"  # type: ignore[attr-defined]
    fake.release.set()
    assert _wait_for(lambda: len(host.root.afters) > 0), "after(0) 予約がありません"  # type: ignore[attr-defined]
    for ms, cb in list(host.root.afters):  # type: ignore[attr-defined]
        assert ms == 0
        cb()
    assert refreshed, "after(0) で _refresh_bcon_rows が呼ばれません"
