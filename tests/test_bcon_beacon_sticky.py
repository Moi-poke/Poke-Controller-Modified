"""BEACON送出後の粘着errcode誤判定の再現（実機不要・_on_beacon赤）。

_config_result は送出前後のSTATUS差分で粘着を見分けるが、_on_beacon
（BconSetup.py:973-1018）は post の errcode 絶対値だけで拒否を出す。
送出前から残る errcode=0x03・計数不動の粘着を、差分流儀なら
受け付け扱い＋注記にすべきところ、現行は拒否にしてしまう。
"""

from __future__ import annotations

import queue as _q
from typing import Any


class _FakeBeaconTransport:
    """線に触れない偽運搬器。公開口だけ持つ。"""

    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self, pre: dict[str, int], post: dict[str, int]) -> None:
        # 送出前の写し（粘着の起点）と送出後の STATUS 応答。
        self._pre = dict(pre)
        self._post = dict(post)
        self.sent: list[tuple[int, bytes]] = []

    def send_config(self, kind: int, body: bytes) -> bool:
        # 公開口。送出の記録だけ残す。
        self.sent.append((int(kind) & 0xFF, bytes(body)))
        return True

    def last_status(self) -> dict[str, int]:
        # 送出前の写し。線に触れず辞書写しを返す。
        return dict(self._pre)

    def request_status(self, timeout: float = 1.0) -> dict[str, int]:
        _ = timeout
        return dict(self._post)


class _FakeSender:
    """_require_bcon が辿る transport 置き場。"""

    def __init__(self, transport: Any) -> None:
        self.transport = transport


def _make_headless_beacon(transport: Any) -> Any:
    """窓を作らず queue だけ持つ検査体（tkinter を読まない）。"""
    from BconSetup import BconSetup

    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = _FakeSender(transport)
    obj._queue = _q.Queue()
    obj._busy = False
    obj._stop = False
    obj._closed = False
    obj._rx_unsub = None
    obj._rx_transport = None
    return obj


def _run_beacon_job_headless(obj: Any) -> None:
    """作業スレッドを起こさずその場で走らせる。"""
    obj._run = lambda func: func()
    obj._on_beacon()


def _drain_log_text(obj: Any) -> str:
    """溜めの log を全部つなげて返す（送ります＋判定の2件分）。"""
    parts: list[str] = []
    while not obj._queue.empty():
        kind, text = obj._queue.get_nowait()
        if kind == "log":
            parts.append(str(text))
    assert parts, "logが1件も積まれていません"
    return "\n".join(parts)


def test_on_beacon_sticky_identical_should_accept() -> None:
    """粘着・計数不動は受け付け扱い＋注記のはず（現行は拒否で赤）。"""
    # Given: 送出前から残る errcode=0x03。計数は動かない。
    sticky = {
        "flags": 0x00,
        "last_seq": 7,
        "err_crc": 0,
        "err_drop": 2,
        "errcode": 0x03,
    }
    transport = _FakeBeaconTransport(pre=sticky, post=dict(sticky))
    obj = _make_headless_beacon(transport)

    # When: BEACON 送出直後の STATUS で合否を出す。
    _run_beacon_job_headless(obj)

    # Then: 差分流儀なら粘着は今回のせいにしない。
    text = _drain_log_text(obj)
    assert "受け付けました" in text
    assert "拒否されました" not in text
    assert "過去の記録" in text


def test_on_beacon_counters_moved_is_fresh_reject() -> None:
    """計数が動いたら今回の拒否として出す（新規検出・緑のはず）。"""
    # Given: 送出前は正常、送出後は err_drop が進んだ拒否。
    pre = {
        "flags": 0x00,
        "last_seq": 10,
        "err_crc": 0,
        "err_drop": 5,
        "errcode": 0x00,
    }
    post = {
        "flags": 0x00,
        "last_seq": 11,
        "err_crc": 0,
        "err_drop": 6,
        "errcode": 0x03,
    }
    transport = _FakeBeaconTransport(pre=pre, post=post)
    obj = _make_headless_beacon(transport)

    # When: BEACON 送出直後の STATUS で合否を出す。
    _run_beacon_job_headless(obj)

    # Then: 今回の拒否として報告する。
    text = _drain_log_text(obj)
    assert "拒否されました" in text
    assert "err_drop=6" in text


def test_on_beacon_errcode_changed_is_fresh_reject() -> None:
    """計数不動でも errcode 変化は今回の層事象として拒否のはず（緑）。"""
    # Given: 計数は不動だが errcode だけ 0x00→0x03 へ変わる。
    pre = {
        "flags": 0x00,
        "last_seq": 7,
        "err_crc": 0,
        "err_drop": 2,
        "errcode": 0x00,
    }
    post = {
        "flags": 0x00,
        "last_seq": 7,
        "err_crc": 0,
        "err_drop": 2,
        "errcode": 0x03,
    }
    transport = _FakeBeaconTransport(pre=pre, post=post)
    obj = _make_headless_beacon(transport)

    # When: BEACON 送出直後の STATUS で合否を出す。
    _run_beacon_job_headless(obj)

    # Then: 今回の拒否として報告する。
    text = _drain_log_text(obj)
    assert "拒否されました" in text
