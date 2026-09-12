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

    送出時（advance）に判定する。後続の非中立がある未送出中立は
    age不問で飛ばす。レガシー（状態行の上書き）と同等の遷移になる。
    """
    sched = LiveScheduler()
    sched.push(_snap(0, 0))
    sched.advance(0.0)
    assert sched.current()["btn"] == 0
    sched.push(_snap(4, 1))  # A押下
    sched.push(_snap(0, 2))  # 解放（未送出）
    sched.push(_snap(2, 3))  # B押下。Aとも中立とも違う
    sched.advance(1.0)
    assert sched.current()["btn"] == 4
    sched.advance(2.0)
    assert sched.current()["btn"] == 2  # 中立を挟まずBへ
    assert sched.stats()["merged"] == 1
    assert sched.pending_empty()


def test_unshown_neutral_kept_for_same_repress() -> None:
    """同ボタンの再押下では中立を残す（連打の2発目を潰さない）。

    実時間どおりに進めた場合：中立は再押下の到着前に送出されるため、
    skipは発火せず全件届く。100ms間隔の連打の可視性は保たれる。
    """
    sched = LiveScheduler()  # dwell 0.016
    sched.push(_snap(0, 0))
    sched.advance(0.0)
    sched.push(_snap(4, 1))
    delivered: list[int] = []
    last: int | None = None
    now = 0.0

    def step(to: float) -> None:
        nonlocal now, last
        while now < to:
            now = round(now + 0.008, 9)
            sched.advance(now)
            cur = sched.current()
            if cur is not None and int(cur["revision"]) != last:
                last = int(cur["revision"])
                delivered.append(last)

    step(0.050)
    sched.push(_snap(0, 2))
    step(0.100)
    sched.push(_snap(4, 3))
    step(0.200)
    assert sched.stats()["merged"] == 0
    assert delivered == [0, 1, 2, 3]
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


def test_eb_cadence_stays_discrete_with_16ms_dwell() -> None:
    """E-b周期（60ms押下＋20ms離し）の同ボタン連打は分離されたまま。

    中立 age 20ms が dwell 16ms を超えるため融合しない。個別pressの
    可視性が要る用途の根拠。既定16msを守る錠の1つ。
    """
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(4, 1))
    now = 0.0
    delivered: list[int] = []
    last: int | None = None
    for cycle in range(5):
        base = cycle * 0.080
        while now < base + 0.060:
            now = round(now + 0.008, 9)
            sched.advance(now)
            cur = sched.current()
            if cur is not None and int(cur["revision"]) != last:
                last = int(cur["revision"])
                delivered.append(last)
        sched.push(_snap(0, 100 + cycle))
        while now < base + 0.080:
            now = round(now + 0.008, 9)
            sched.advance(now)
            cur = sched.current()
            if cur is not None and int(cur["revision"]) != last:
                last = int(cur["revision"])
                delivered.append(last)
        sched.push(_snap(4, 200 + cycle))
    while not sched.pending_empty():
        now = round(now + 0.008, 9)
        sched.advance(now)
        cur = sched.current()
        if cur is not None and int(cur["revision"]) != last:
            last = int(cur["revision"])
            delivered.append(last)
    assert sched.stats()["merged"] == 0
    assert delivered.count(1) == 1
    assert sum(1 for r in delivered if r >= 100 and r < 200) == 5
    assert sum(1 for r in delivered if r >= 200) == 5
    assert sched.pending_empty()


def test_alternating_40ms_cadence_holds_with_16ms_dwell() -> None:
    """元スクリプト形（40ms毎の異種交互連打）は遅延なく完遂する。

    生ser直書きは状態を40ms毎に上書きする。schedulerも中立の
    読み替え・skipで同じ線形にし、全20件が順に・溢れなく届く。
    最終pressの送出遅延は1スロット（8ms）以内に収まる。
    """
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    btns = [4, 2]
    now = 0.0
    delivered_at: dict[int, float] = {}
    order: list[int] = []
    for k in range(20):
        sched.push(_snap(btns[k % 2], k + 1))
        end = (k + 1) * 0.040
        while now < end:
            now = round(now + 0.008, 9)
            sched.advance(now)
            cur = sched.current()
            if cur is not None:
                rev = int(cur["revision"])
                delivered_at.setdefault(rev, now)
                if not order or order[-1] != rev:
                    order.append(rev)
    while not sched.pending_empty():
        now = round(now + 0.008, 9)
        sched.advance(now)
        cur = sched.current()
        if cur is not None:
            rev = int(cur["revision"])
            delivered_at.setdefault(rev, now)
            if not order or order[-1] != rev:
                order.append(rev)
    assert sched.stats()["dropped"] == 0
    assert order == list(range(1, 21))
    assert delivered_at[20] - 19 * 0.040 <= 0.016


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


def _drain_revisions(sched: LiveScheduler) -> list[int]:
    """未送出が空になるまで8ms刻みで進め、送出遷移の revision 列を返す。"""
    delivered: list[int] = []
    last: int | None = None
    now = 0.0
    for _ in range(1000):
        now = round(now + 0.008, 9)
        sched.advance(now)
        cur = sched.current()
        if cur is not None and int(cur["revision"]) != last:
            last = int(cur["revision"])
            delivered.append(last)
        if sched.pending_empty():
            break
    return delivered


def test_advance_skips_superseded_neutral() -> None:
    """後続の非中立がある未送出中立は飛ばす（age不問）。

    送出時点で追い越されていた中立を送っても周期が延びるだけで、
    下流の分解能（約1 dwell未満のギャップは数えられない）には載ら
    ない。落とした分は merged に数える。同内容・異内容を問わない。
    """
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(4, 1))
    sched.advance(0.0)
    assert sched.current()["btn"] == 4
    sched.push(_snap(0, 2))
    sched.push(_snap(4, 3))  # 後続ありのため送出時に中立を飛ばす
    assert sched.stats()["merged"] == 0
    sched.advance(1.0)
    assert sched.current()["btn"] == 4
    assert sched.current()["revision"] == 3
    assert sched.stats()["merged"] == 1
    assert sched.pending_empty()


def test_advance_keeps_lone_neutral() -> None:
    """後続がない中立は飛ばさない（解放の到達保証）。"""
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(4, 1))
    sched.advance(0.0)
    sched.push(_snap(0, 2))
    assert _drain_revisions(sched) == [1, 2]
    assert sched.pending_empty()


def test_advance_waits_dwell_before_skip() -> None:
    """dwell未満は飛ばしも進めもしない（最小保持の維持）。"""
    sched = LiveScheduler(slot_s=0.008, min_dwell_s=0.016)
    sched.push(_snap(4, 1))
    sched.advance(0.0)
    sched.push(_snap(0, 2))
    sched.push(_snap(2, 3))
    sched.advance(0.008)
    assert sched.current()["btn"] == 4
    assert _drain_revisions(sched) == [1, 3]
