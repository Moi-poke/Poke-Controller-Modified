"""LiveScheduler の検証。仮想時刻を通すため実スレッドも実線も要らない。"""

from core.serial.live_scheduler import LiveScheduler


def _snap(btn: int = 0, rev: int = 0) -> dict:
    return {
        "btn": btn,
        "hat": 8,
        "lx": 128,
        "ly": 128,
        "rx": 128,
        "ry": 128,
        "revision": rev,
    }


def test_short_press_is_extended_to_min_dwell() -> None:
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(btn=4, rev=1))  # t=0 press
    sched.advance(0.0)
    assert sched.current()["btn"] == 4
    sched.push(_snap(btn=0, rev=2))  # t=0.008 release（dwell未満）
    sched.advance(0.008)
    assert sched.current()["btn"] == 4  # まだpressを保持
    sched.advance(0.016)
    assert sched.current()["btn"] == 0  # ここでreleaseへ進む


def test_long_press_is_not_extended() -> None:
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(btn=4, rev=1))
    sched.advance(0.0)
    sched.push(_snap(btn=0, rev=2))
    sched.advance(0.100)
    assert sched.current()["btn"] == 0
    assert sched.pending_empty()


def test_stick_only_merges_button_edge_never_drops() -> None:
    sched = LiveScheduler()
    sched.push(_snap(btn=4, rev=1))
    sched.push(dict(_snap(btn=4, rev=2), lx=200))  # btn同値→畳む
    sched.push(_snap(btn=0, rev=3))  # btn変化→残す
    assert sched.stats()["merged"] == 1
    sched.advance(0.0)
    assert sched.current()["lx"] == 200  # 畳まれた最新値が出る
    sched.advance(1.0)
    assert sched.current()["btn"] == 0  # releaseは消えていない


def test_overflow_counts_dropped() -> None:
    sched = LiveScheduler(capacity=2)
    sched.push(_snap(btn=1, rev=1))
    sched.push(_snap(btn=2, rev=2))
    sched.push(_snap(btn=3, rev=3))  # 最古が捨てられる
    assert sched.stats()["dropped"] == 1
