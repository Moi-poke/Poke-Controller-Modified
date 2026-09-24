"""BconProcTransport.open() の失敗道はWARNINGを出す（黙ってFalseにしない）。

親側の黙り失敗（spawn例外・応答待ちtimeout・子側結果False）は
子stdoutがDEVNULLのため原因追跡ができない。親側で理由を残す。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any


class _AliveHandle:
    """起動済み扱いの子の代役。"""

    def __init__(self) -> None:
        self._alive = True

    def start(self) -> None:
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self._alive = False

    def join(self, timeout: float | None = None) -> None:
        _ = timeout


def test_spawn_worker_logs_spawn_exception(caplog: Any) -> None:
    """Given: spawnがRuntimeErrorで落ちる / When: _spawn_worker() / Then: WARNINGに例外が残りFalseを返す。"""
    from core.transport import bcon_proc

    def _boom(entry: Any, cfg: dict) -> Any:
        _ = entry, cfg
        raise RuntimeError("proc child did not connect")

    made = bcon_proc.BconProcTransport(_spawn=_boom)
    try:
        with caplog.at_level(logging.WARNING, logger="core.transport.bcon_proc"):
            assert made._spawn_worker() is False
    finally:
        made.close()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "spawn失敗でWARNINGが出ない"
    assert any("proc child did not connect" in r.getMessage() for r in warnings)


def test_open_logs_event_wait_timeout(caplog: Any, monkeypatch: Any) -> None:
    """Given: 応答を返さない子 / When: open() / Then: WARNINGにport/baud/timeoutが残りFalseを返す。"""
    from core.transport import bcon_proc

    monkeypatch.setattr(bcon_proc, "PROC_RPC_MARGIN_S", -9.9)

    def _silent(entry: Any, cfg: dict) -> tuple[Any, Any, Any]:
        _ = entry, cfg
        return _AliveHandle(), queue.Queue(), queue.Queue()

    made = bcon_proc.BconProcTransport(_spawn=_silent)
    try:
        with caplog.at_level(logging.WARNING, logger="core.transport.bcon_proc"):
            assert made.open(3, "COM3", 1000000) is False
    finally:
        made.close()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "open応答待ちtimeoutでWARNINGが出ない"
    assert any(
        "COM3" in r.getMessage()
        and "1000000" in r.getMessage()
        and ("待" in r.getMessage() or "timeout" in r.getMessage().lower())
        for r in warnings
    )


def test_open_logs_child_result_false(caplog: Any) -> None:
    """Given: 子が結果Falseを返す / When: open() / Then: WARNINGにbox内容が残りFalseを返す。"""
    from core.transport import bcon_proc

    cmd_q: Any = queue.Queue()
    evt_q: Any = queue.Queue()

    def _naysayer(entry: Any, cfg: dict) -> tuple[Any, Any, Any]:
        _ = entry, cfg
        return _AliveHandle(), cmd_q, evt_q

    made = bcon_proc.BconProcTransport(_spawn=_naysayer)
    assert made._spawn_worker() is True

    def _reply_false() -> None:
        try:
            msg = cmd_q.get(timeout=5.0)
        except Exception:
            return
        try:
            call_id = msg[1]
        except Exception:
            return
        evt_q.put(("reply", call_id, True, False))

    helper = threading.Thread(target=_reply_false, daemon=True)
    try:
        helper.start()
        with caplog.at_level(logging.WARNING, logger="core.transport.bcon_proc"):
            assert made.open(3, "COM3", 1000000) is False
    finally:
        helper.join(5.0)
        made.close()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "子側結果FalseでWARNINGが出ない"
    assert any("False" in r.getMessage() for r in warnings)
