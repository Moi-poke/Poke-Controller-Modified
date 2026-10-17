"""起動時の並び順と、discardLive が線を無音にしないことを固定する。時計は要らない。"""

from __future__ import annotations

from typing import Any

from core.serial.live_scheduler import LiveScheduler


def _snap(rev: int) -> dict[str, Any]:
    return {
        "btn": 0,
        "hat": 8,
        "lx": 128,
        "ly": 128,
        "rx": 128,
        "ry": 128,
        "revision": rev,
    }


def test_clear_keeps_the_current_state_so_the_line_is_not_silent() -> None:
    """Given: 送出中の状態がある / When: discardLive（clear）する / Then: 送出中の状態は残ること。"""
    sched = LiveScheduler()
    sched.push(_snap(4))
    sched.advance(0.0)
    assert sched.clear() is False
    # ここで current が消えると、塞ぎを解いて起こし直すまで線が無音になる。
    assert sched.current()["revision"] == 4


def test_start_worker_pushes_the_initial_state_under_the_posture_lock() -> None:
    """Given: live 対応の送信器 / When: worker を起こす / Then: 初期姿勢の投入が錠の中であること。"""
    from core.serial.sender import Sender
    from fakes import FakeTransport

    sender = Sender(is_show_serial=False, transport=FakeTransport())
    # live 対応の判定だけを差し替える（worker は起こすが送り先は偽物）。
    sender._liveCapable = lambda *a, **k: True  # type: ignore[method-assign]
    held: list[bool] = []
    orig = sender.putLive

    def _record(snap: dict[str, Any] | None = None, *, _closing: bool = False) -> None:
        # RLock._is_owned は「いまこのスレッドが持っているか」。錠を離して
        # から積むと、同時に走った apply* の新しい revision が先に並ぶ。
        owned = getattr(sender._lock, "_is_owned", None)
        held.append(bool(owned()) if callable(owned) else False)
        orig(snap, _closing=_closing)

    sender.putLive = _record  # type: ignore[method-assign]
    try:
        sender.startLiveWorker()
    finally:
        sender.stopLiveWorker(0.5)
    assert held, "初期姿勢が積まれていない"
    assert all(held), f"錠の外で初期姿勢を積んでいる: {held}"
