"""軌跡圧縮の ε 保証を固定する。実機不要（純関数のみ）。"""

from __future__ import annotations

import math
import random
from typing import Any

from core import motion

_T0 = 1000.0


def _samples(xy: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """座標列を 8ms 格子の (at, x, y) へ。"""
    return [
        (_T0 + i * motion.SLOT_S, float(x), float(y)) for i, (x, y) in enumerate(xy)
    ]


def _max_error(pts: list[Any], samples: list[tuple[float, float, float]]) -> float:
    """各サンプル時刻で復元した XY と元 XY の距離の最大。"""
    qf = [motion.Waypoint(float(p.t), float(p.r), float(p.th)) for p in pts]
    worst = 0.0
    for at, x, y in samples:
        r, th = motion._restore_polar(qf, (at - samples[0][0]) * 1000.0)
        px, py = motion.polar_to_xy(r, th)
        worst = max(worst, math.hypot(px - x, py - y))
    return worst


def _bound(eps: float) -> float:
    """実装が保証する上限（量子化の下限を含む）。"""
    # 掃引と同じ理由で literal 固定（motion.QUANT_FLOOR ≒ 2.08 と対応）。
    return max(eps, 2.08) + 1e-6


def _line_through_center(n: int = 80) -> list[tuple[float, float]]:
    """中央を横切って縦へ抜ける軌跡（中心付近で θ が跳ぶ形）。"""
    left = [(128 - 120 + 240 * i / (n // 2 - 1), 128.0) for i in range(n // 2)]
    down = [(128.0, 128 - 120 + 240 * i / (n - n // 2 - 1)) for i in range(n - n // 2)]
    return left + down


def test_dead_zone_theta_borrowing_stays_within_the_bound() -> None:
    """Given: 中心近く(r=7.6)から大きく向きが変わる 3 点 / When: 圧縮 / Then: ε 以内であること。"""
    samples = _samples([(131.0, 135.0), (35.0, 151.0), (66.0, 193.0)])
    pts = motion.compress_track(samples, _T0, 2.5)
    assert pts
    assert _max_error(pts, samples) <= _bound(2.5)


def test_line_through_center_stays_within_the_bound() -> None:
    """Given: 中心を貫く軌跡 / When: 圧縮 / Then: ε 以内であること。"""
    samples = _samples(_line_through_center())
    pts = motion.compress_track(samples, _T0, 2.5)
    assert pts
    assert _max_error(pts, samples) <= _bound(2.5)


def test_random_paths_stay_within_the_bound() -> None:
    """Given: 緩いランダム経路 400 本 / When: 圧縮 / Then: すべて ε 以内であること。"""
    rng = random.Random(20261015)
    eps = 2.5
    violations: list[tuple[float, list[tuple[float, float]]]] = []
    for _ in range(400):
        x, y = rng.uniform(60, 196), rng.uniform(60, 196)
        xy = [(x, y)]
        step = rng.choice([10.0, 30.0, 60.0])
        for _ in range(rng.randint(20, 40) - 1):
            x = min(255.0, max(0.0, x + rng.uniform(-step, step)))
            y = min(255.0, max(0.0, y + rng.uniform(-step, step)))
            xy.append((x, y))
        samples = _samples(xy)
        pts = motion.compress_track(samples, _T0, eps)
        worst = _max_error(pts, samples) if pts else 0.0
        if worst > _bound(eps):
            violations.append((worst, xy))
    assert not violations, (
        f"ε を超えた経路: {len(violations)} 本 / 最悪 {max(v[0] for v in violations):.3f}"
    )


def test_control_full_deadzone_borrowing_breaks_the_bound(
    monkeypatch: Any,
) -> None:
    """Given: 借用範囲をデッドゾーン全体に戻した旧挙動 / When: 同じ入力 / Then: ε を超えること。"""
    original = motion._fill_theta

    def _old(polars: Any, raw_th: Any, borrow_r: float = motion.DEADZONE_R) -> Any:
        return original(polars, raw_th, motion.DEADZONE_R)

    monkeypatch.setattr(motion, "_fill_theta", _old)
    samples = _samples([(131.0, 135.0), (35.0, 151.0), (66.0, 193.0)])
    pts = motion.compress_track(samples, _T0, 2.5)
    assert _max_error(pts, samples) > _bound(2.5), (
        "旧挙動でも ε 以内なら、この検査は何も守っていない"
    )
