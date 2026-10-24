"""ε を振った軌跡圧縮の保証（4.7）と、終了処理の棄権経路（4.6）を固定する。"""

from __future__ import annotations

import math
import random
from typing import Any

import pytest
from core import motion


def _random_tracks(
    count: int, seed: int, n: int = 30
) -> list[list[tuple[float, float, float]]]:
    """8ms 格子の緩いランダム経路を count 本作る。"""
    rng = random.Random(seed)
    tracks = []
    for _ in range(count):
        x, y = rng.uniform(60, 196), rng.uniform(60, 196)
        xy = [(x, y)]
        step = rng.choice([10.0, 30.0, 60.0])
        for _ in range(n - 1):
            x = min(255.0, max(0.0, x + rng.uniform(-step, step)))
            y = min(255.0, max(0.0, y + rng.uniform(-step, step)))
            xy.append((x, y))
        tracks.append(
            [
                (1000.0 + i * motion.SLOT_S, float(a), float(b))
                for i, (a, b) in enumerate(xy)
            ]
        )
    return tracks


def _max_err(pts: list[Any], samples: list[tuple[float, float, float]]) -> float:
    """実装と同じ 8ms 格子の時刻で誤差を測る。

    実際のサンプル時刻（非格子）で測ると、前値ホールドの分（最大 8ms ぶんの
    移動）が乗る。それは圧縮では減らせない格子化の誤差で、実装の保証対象は
    「前値ホールド後の track」なので、ここも同じ定義に揃える。
    """
    qf = [motion.Waypoint(float(p.t), float(p.r), float(p.th)) for p in pts]
    t0 = samples[0][0]
    worst = 0.0
    for at, x, y in samples:
        slot_ms = round((at - t0) / motion.SLOT_S) * motion.SLOT_S * 1000.0
        r, th = motion._restore_polar(qf, slot_ms)
        px, py = motion.polar_to_xy(r, th)
        worst = max(worst, math.hypot(px - x, py - y))
    return worst


def _allowed(eps: float) -> float:
    """その ε で許される上限。

    実装は「量子化で超えた分だけ目標を締め直す」ので、ε が量子化の上限
    （QUANT_FLOOR）以上なら厳密に ε 以内、それ未満なら QUANT_FLOOR までが
    限界になる（結び目の r/θ の丸めは圧縮では消せない）。余裕は付けない。
    """
    # QUANT_FLOOR（≒2.08）と対応する値。定数を検査対象にすると、定数を
    # 大きくする変更でテストも一緒に緩むので、ここは literal で固定する。
    return max(eps, 2.08) + 1e-6


@pytest.mark.parametrize("eps", [1.3, 1.5, 2.0, 2.5, 3.0, 3.4, 4.0, 6.0])
def test_epsilon_bound_holds_for_every_epsilon(eps: float) -> None:
    """Given: 緩いランダム経路 400 本 / When: その ε で圧縮 / Then: 許される上限内であること。"""
    worst = 0.0
    for samples in _random_tracks(400, seed=1):
        pts = motion.compress_track(samples, samples[0][0], eps)
        worst = max(worst, _max_err(pts, samples))
    assert worst <= _allowed(eps), f"eps={eps} 最悪={worst:.3f}"


def test_center_crossing_paths_hold_for_every_epsilon() -> None:
    """Given: 中心を横切る軌跡 / When: 各 ε で圧縮 / Then: 目標内であること。"""
    n = 80
    left = [(128 - 120 + 240 * i / (n // 2 - 1), 128.0) for i in range(n // 2)]
    down = [(128.0, 128 - 120 + 240 * i / (n - n // 2 - 1)) for i in range(n - n // 2)]
    samples = [
        (1000.0 + i * motion.SLOT_S, float(x), float(y))
        for i, (x, y) in enumerate(left + down)
    ]
    for eps in (1.3, 2.5, 3.4, 6.0):
        pts = motion.compress_track(samples, 1000.0, eps)
        assert _max_err(pts, samples) <= _allowed(eps), eps
