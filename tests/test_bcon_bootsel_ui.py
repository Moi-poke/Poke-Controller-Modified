"""BOOTSEL行のheadless検査（実機不要）。"""

from __future__ import annotations

import queue as _q
from typing import Any


class _FakeBconTransport:
    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self, ok: bool = True) -> None:
        self._ok = ok
        self.bootsel_calls = 0

    def is_open(self) -> bool:
        return True

    def stop_live_loop(self) -> bool:
        return True

    def hello(self, timeout: float = 3.0) -> bool:
        _ = timeout
        return True

    def start_live_loop(self, interval_s: float = 1.0 / 120.0) -> bool:
        _ = interval_s
        return True

    def subscribe_rx(self, func: Any) -> Any:
        _ = func

        def _unsub() -> None:
            return None

        return _unsub

    def request_bootsel(self, timeout: float = 1.0) -> bool:
        _ = timeout
        self.bootsel_calls += 1
        return self._ok


class _FakeSender:
    def __init__(self, transport: Any) -> None:
        self.transport = transport


class _StubWindow:
    def after(self, ms: int, func: Any) -> Any:
        _ = (ms, func)
        return None

    def winfo_exists(self) -> bool:
        return True

    def destroy(self) -> None:
        return None


def _make_headless(transport: Any) -> Any:
    from BconSetup import BconSetup

    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = _FakeSender(transport)
    obj._queue = _q.Queue()
    obj._busy = False
    obj._stop = False
    obj._closed = False
    obj._rx_unsub = None
    obj._rx_transport = None
    obj.window = _StubWindow()
    return obj


def _drain_logs(obj: Any) -> str:
    texts: list[str] = []
    try:
        while True:
            kind, text = obj._queue.get_nowait()
            if kind == "log":
                texts.append(str(text))
    except _q.Empty:
        pass
    return "\n".join(texts)


def test_bootsel_confirm_sends_and_guides_reconnect(monkeypatch: Any) -> None:
    """確認→作業→request_bootsel×1→再起動/再接続の案内がqueueへ載る。"""
    import BconSetup as mod

    transport = _FakeBconTransport(ok=True)
    obj = _make_headless(transport)
    obj._run = lambda func: func()  # その場で走らせる。
    seen: dict[str, Any] = {}

    def _ask(title: str, msg: str, parent: Any = None) -> str:
        seen["title"] = title
        seen["msg"] = msg
        seen["parent"] = parent
        return "yes"

    monkeypatch.setattr(mod.tkmsg, "askquestion", _ask)
    obj._on_bootsel()
    assert seen.get("parent") is obj.window
    assert transport.bootsel_calls == 1
    logs = _drain_logs(obj)
    for word in ("再起動", "再接続", "COM", "HELLO"):
        assert word in logs


def test_bootsel_failure_reports_no_response(monkeypatch: Any) -> None:
    """不通時は応答なしを出す。"""
    import BconSetup as mod

    transport = _FakeBconTransport(ok=False)
    obj = _make_headless(transport)
    obj._run = lambda func: func()
    monkeypatch.setattr(mod.tkmsg, "askquestion", lambda *a, **k: "yes")
    obj._on_bootsel()
    assert transport.bootsel_calls == 1
    assert "応答なし" in _drain_logs(obj)


def test_bootsel_cancel_sends_nothing(monkeypatch: Any) -> None:
    """いいえなら送らない。"""
    import BconSetup as mod

    transport = _FakeBconTransport(ok=True)
    obj = _make_headless(transport)
    ran = {"hit": False}
    obj._run = lambda func: ran.__setitem__("hit", True)  # type: ignore[no-untyped-def]
    monkeypatch.setattr(mod.tkmsg, "askquestion", lambda *a, **k: "no")
    obj._on_bootsel()
    assert ran["hit"] is False
    assert transport.bootsel_calls == 0
