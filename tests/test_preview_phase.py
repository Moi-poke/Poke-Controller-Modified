"""表示 tick の位相合わせ（PhaseLock）の検査。

取込と表示がそれぞれ別の時計で 60Hz を刻むと、2 つの位相がゆっくり
ずれ、重なった前後で「新フレーム無し」と「1 枚飛ばし」が多発して表示
fps が 45 前後まで落ちる（実測ログ 2026-10-04 16:53）。実機の時計は
再現できないので、カメラの公開時刻と表示 tick を仮想時間で模擬する。
"""

from __future__ import annotations

import bisect
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import pytest
from core.preview_phase import PhaseLock

_NS = 1_000_000_000
_MS = 1_000_000


class _Policy(Protocol):
    def observe(self, now_ns: int, seq: int, t_ready_ns: int) -> int | None: ...


class _NeverRealign:
    """対照群。位相合わせをしない（修正前の挙動）。"""

    def observe(self, now_ns: int, seq: int, t_ready_ns: int) -> int | None:
        return None


@dataclass(frozen=True, slots=True)
class _SimResult:
    published: int
    drawn: int
    realigns: int

    @property
    def ratio(self) -> float:
        return self.drawn / self.published


def _simulate(
    policy: _Policy,
    *,
    camera_hz: float,
    display_fps: int = 60,
    duration_s: float = 120.0,
    camera_jitter_ns: int = int(1.5 * _MS),
    wake_jitter_ns: int = int(0.8 * _MS),
    seed: int = 1,
) -> _SimResult:
    """カメラ公開と表示 tick を時刻順に処理し、描けた枚数を数える。

    表示 tick は anchor + n*P（P=1/display_fps）に起床の遅れを足した時刻。
    policy が遅延を返したら、その時刻（Tk after の ms 丸め込み）に 1 回
    描画し、そこを新しい anchor にする（PreviewClock.realign と同じ）。
    """
    rng = random.Random(seed)
    camera_period = _NS / camera_hz
    end_ns = int(duration_s * _NS)
    ready: list[int] = []
    k = 1
    while True:
        t = int(k * camera_period) + rng.randint(-camera_jitter_ns, camera_jitter_ns)
        if t > end_ns:
            break
        if ready and t <= ready[-1]:
            t = ready[-1] + 1
        ready.append(t)
        k += 1

    period = round(_NS / display_fps)
    anchor = 0
    n = 1
    realign_at: int | None = None
    drawn_seqs: set[int] = set()
    realigns = 0

    def dispatch(now: int) -> int | None:
        seq = bisect.bisect_right(ready, now)
        if seq == 0:
            return None
        drawn_seqs.add(seq)
        return policy.observe(now, seq, ready[seq - 1])

    while True:
        tick_at = anchor + n * period + rng.randint(0, wake_jitter_ns)
        if realign_at is not None and realign_at <= tick_at:
            now = realign_at
            realign_at = None
            if now > end_ns:
                break
            realigns += 1
            delay = dispatch(now)
            anchor, n = now, 1
        else:
            now = tick_at
            if now > end_ns:
                break
            delay = dispatch(now)
            n += 1
        if delay is not None and realign_at is None:
            delay_ms = max(1, -(-delay // _MS))  # Tk after は ms 単位の切り上げ
            realign_at = now + delay_ms * _MS + rng.randint(0, _MS)

    # 立ち上がりの 5 秒は除く
    warm = 5 * _NS
    first = bisect.bisect_right(ready, warm) + 1
    published = len(ready) - first + 1
    drawn = sum(1 for s in drawn_seqs if s >= first)
    return _SimResult(published, drawn, realigns)


def _phase_lock(display_fps: int = 60) -> PhaseLock:
    return PhaseLock(round(_NS / display_fps))


def test_without_phase_lock_a_near_equal_camera_drops_frames_in_beats() -> None:
    # Given: 60.05Hz のカメラと 60Hz の表示（20 秒周期でうなる）
    # When: 位相合わせ無しで 120 秒回す
    result = _simulate(_NeverRealign(), camera_hz=60.05)
    # Then: 重なり付近で取りこぼしが出る（対照群。この検査が落ちない
    # 状況なら、下の検査は何も守っていない）
    assert result.ratio < 0.98, f"対照群で取りこぼしが出ない: {result.ratio:.4f}"


@pytest.mark.parametrize("camera_hz", [60.05, 59.94, 60.0])
def test_phase_lock_keeps_nearly_every_frame_of_a_near_equal_camera(
    camera_hz: float,
) -> None:
    # Given: 表示とほぼ同じ周期のカメラ
    # When: 位相合わせ有りで 120 秒回す
    result = _simulate(_phase_lock(), camera_hz=camera_hz)
    # Then: 公開されたフレームのほぼ全部を描く（カメラが速いぶんの
    # 不可避な脱落 0.08% を除けば、うなりによる脱落は残らない）
    assert result.ratio >= 0.995, f"{camera_hz}Hz: 描画率 {result.ratio:.4f}"
    # 位相を据え直すのはうなり 1 周につき数回まで（連打しない）
    assert result.realigns <= 30, f"{camera_hz}Hz: 据え直し {result.realigns} 回"


@pytest.mark.parametrize(
    ("camera_hz", "display_fps"), [(30.0, 60), (180.0, 60), (60.0, 30)]
)
def test_phase_lock_never_realigns_when_rates_differ(
    camera_hz: float, display_fps: int
) -> None:
    # Given: カメラと表示の周期が明らかに違う（空振りや飛ばしが正常）
    # When: 位相合わせ有りで回す
    result = _simulate(
        _phase_lock(display_fps), camera_hz=camera_hz, display_fps=display_fps
    )
    # Then: 一度も据え直さない（据え直しても得るものが無い）
    assert result.realigns == 0, f"据え直し {result.realigns} 回"


def test_phase_lock_ignores_frames_without_timing() -> None:
    # Given: 公開時刻を持たないカメラ（t_ready=0 は「記録無し」の印）
    lock = _phase_lock()
    observe: Callable[[int, int], int | None] = lambda now, seq: lock.observe(  # noqa: E731
        now, seq, 0
    )
    # When: 同じ seq を何度見ても
    results = [observe(i * 16 * _MS, 1) for i in range(10)]
    # Then: 据え直しを要求しない
    assert results == [None] * 10


def test_phase_lock_targets_half_a_period_after_the_next_arrival() -> None:
    # Given: 16.67ms 周期で届くフレームを 3 枚見た後
    period = round(_NS / 60)
    lock = PhaseLock(period)
    for seq in (1, 2, 3):
        assert lock.observe(seq * period + 200_000, seq, seq * period) is None
    # When: 次の到着直前の tick が同じ seq=3 を見た（空振り）
    now = 4 * period - 300_000
    delay = lock.observe(now, 3, 3 * period)
    # Then: 次の到着（4P）から半周期後を狙う
    assert delay is not None
    assert abs(delay - (4 * period + period // 2 - now)) <= 1  # 半周期の丸め
