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


def _stick(lx: int = 128, ly: int = 128, rev: int = 0) -> dict:
    return {
        "btn": 0,
        "hat": 8,
        "lx": lx,
        "ly": ly,
        "rx": 128,
        "ry": 128,
        "revision": rev,
    }


def test_unshown_neutral_dropped_for_different_press() -> None:
    """未送出の中立は、異なる押下が来たら落とす。

    ワイヤに出ていない中立を落としても見た目は変わらない。
    レガシー（状態行の上書き）と同等の遷移になる。
    """
    sched = LiveScheduler()
    sched.push(_snap(0, 0))
    sched.advance(0.0)
    assert sched.current()["btn"] == 0
    sched.push(_snap(4, 1))  # A押下
    sched.push(_snap(0, 2))  # 解放（未送出）
    sched.push(_snap(2, 3))  # B押下。Aとも中立とも違う
    assert sched.stats()["merged"] == 1
    sched.advance(1.0)
    assert sched.current()["btn"] == 4
    sched.advance(2.0)
    assert sched.current()["btn"] == 2  # 中立を挟まずBへ
    assert sched.pending_empty()


def test_unshown_neutral_kept_for_same_repress() -> None:
    """同ボタンの再押下では中立を残す（連打の2発目を潰さない）。"""
    sched = LiveScheduler()
    sched.push(_snap(0, 0))
    sched.advance(0.0)
    sched.push(_snap(4, 1))
    sched.push(_snap(0, 2))
    sched.push(_snap(4, 3))  # 同じA。直近の非中立と同じ
    assert sched.stats()["merged"] == 0
    sched.advance(1.0)
    assert sched.current()["btn"] == 4
    sched.advance(2.0)
    assert sched.current()["btn"] == 0
    sched.advance(3.0)
    assert sched.current()["btn"] == 4
    assert sched.pending_empty()


def test_40ms_interval_completes_with_16ms_dwell() -> None:
    """40ms周期の連打はdwell 16msで滞留なく完遂する。

    8msスロット量子化で実効16ms×2状態=32msとなり、40ms周期に
    収まる。20ms毎にpress/neutralを交互申告し、全100件が順に
    送出され、溢れ捨てが0であること。
    """
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    seen: set[tuple[int, int]] = set()
    last: tuple[int, int] | None = None
    now = 0.0
    end = 0.0
    for k in range(100):
        btn = 4 if k % 2 == 0 else 0
        sched.push(_snap(btn, k + 1))
        end = (k + 1) * 0.020
        while now < end:
            now = round(now + 0.008, 9)
            sched.advance(now)
            cur = sched.current()
            if cur is not None:
                key = (int(cur["btn"]), int(cur["revision"]))
                if key != last:
                    seen.add(key)
                    last = key
    while not sched.pending_empty():
        now = round(now + 0.008, 9)
        sched.advance(now)
        cur = sched.current()
        if cur is not None:
            key = (int(cur["btn"]), int(cur["revision"]))
            if key != last:
                seen.add(key)
                last = key
    assert sched.stats()["dropped"] == 0
    assert len(seen) == 100


def test_40ms_interval_backlogs_with_34ms_dwell() -> None:
    """dwell 34msでは40ms周期が滞留し溢れ捨てが出る（回帰防止）。

    実効34→40ms×2状態=80msが40ms周期を上回るため、未送出が
    積み上がり容量32を超える。既定を34へ戻してはならない根拠。
    """
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.034)
    now = 0.0
    end = 0.0
    for k in range(100):
        btn = 4 if k % 2 == 0 else 0
        sched.push(_snap(btn, k + 1))
        end = (k + 1) * 0.020
        while now < end:
            now = round(now + 0.008, 9)
            sched.advance(now)
    assert sched.stats()["dropped"] > 0


def test_stick_chain_collapses_to_latest() -> None:
    """スティックの連続倒しは最新へ畳む（中立キューを作らない）。

    btn/hatが変わらないスティックのみの差分は、既存の畳み規則で
    末尾へ統合される。連続値は最新だけ届けば十分なためである。
    ボタンのエッジとは扱いが違う点に注意。
    """
    sched = LiveScheduler()
    sched.push(_stick(128, 128, 0))
    sched.advance(0.0)
    sched.push(_stick(200, 128, 1))  # 左へ倒す
    sched.push(_stick(128, 128, 2))  # 戻す（畳まれる）
    sched.push(_stick(56, 128, 3))  # 右へ倒す（畳まれる）
    assert sched.stats()["merged"] == 2
    sched.advance(1.0)
    assert sched.current()["lx"] == 56  # 最新だけが出る
    assert sched.pending_empty()
