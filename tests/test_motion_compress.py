"""motion 圧縮の検証（実機不要）。

設計スレッド ID:001〜009 の確定事項のうち、圧縮層の振る舞いを固定する。
- 8ms 格子化＋前値ホールド
- 極座標化（θ 展開、r 小域の θ 固定、中心素通りの強制分割）
- RDP（XY 距離・時間方向の誤差で ε 保証）
- 量子化（t 整数 ms、r 整数、θ 整数度）＋再検証
- r=0 の点の θ は隣の有効値（ID:006）
- 中立 1 単位差は仕様（ID:008）。(128,127) と (128,128) の差は実害なし。
"""

from __future__ import annotations

import math

from core import motion


def _circle(
    t0: float, period_s: float, n: int = 63
) -> list[tuple[float, float, float]]:
    """1 周の円（r=127）。8ms 格子相当のサンプル列。"""
    out: list[tuple[float, float, float]] = []
    for i in range(n):
        at = t0 + i * 0.008
        deg = 90 + i * 360 / (n - 1)
        x = 128 + 127 * math.cos(math.radians(deg))
        y = 128 + 127 * math.sin(math.radians(deg))
        out.append((at, x, y))
    return out


def _restore_error(
    pts: list[motion.Waypoint], samples: list[tuple[float, float, float]], t0: float
) -> float:
    worst = 0.0
    for at, x, y in samples:
        px, py = motion.restore_xy(pts, (at - t0) * 1000.0)
        worst = max(worst, math.hypot(px - x, py - y))
    return worst


def test_circle_compresses_to_endpoints_with_full_turn() -> None:
    """Given: 1 周 0.5 秒の円 / When: ε=2.5 で圧縮 / Then: θ が 360° 蓄積されること。"""
    t0 = 1000.0
    samples = _circle(t0, 0.5)
    pts = motion.compress_track(samples, t0, 2.5)
    assert len(pts) >= 2
    assert pts[0].r == 127
    # 1 周ぶんの累積角度（量子化で ±1° の余裕を見る）。
    assert abs((pts[-1].th - pts[0].th) - 360.0) <= 2.0
    assert _restore_error(pts, samples, t0) <= 2.5 + 1.2  # 量子化ぶんの余裕


def test_circle_point_count_shrinks_with_eps() -> None:
    """Given: 同じ円 / When: ε を変える / Then: 点数が単調に減ること。"""
    t0 = 2000.0
    samples = _circle(t0, 0.5)
    counts = [len(motion.compress_track(samples, t0, eps)) for eps in (1.0, 2.5, 4.0)]
    assert counts[0] >= counts[1] >= counts[2]
    assert counts[0] <= 20  # 63 点からは大幅に減る。


def test_straight_line_keeps_single_theta() -> None:
    """Given: 中央から上への直線 / When: 圧縮 / Then: θ が一定で r だけ増えること。"""
    t0 = 3000.0
    samples: list[tuple[float, float, float]] = [
        (t0 + i * 0.008, 128.0, 128.0 + i * 127.0 / 19) for i in range(20)
    ]
    pts = motion.compress_track(samples, t0, 2.5)
    assert len(pts) >= 2
    # r=0 の点の θ も隣の有効値（90°）になっている。
    assert all(abs(p.th - 90.0) <= 1.0 for p in pts)
    assert _restore_error(pts, samples, t0) <= 2.5 + 1.2


def test_still_section_returns_empty() -> None:
    """Given: 中立のまま / When: 圧縮 / Then: 空（静止区間）になること。"""
    t0 = 4000.0
    samples = [(t0 + i * 0.008, 128, 128) for i in range(20)]
    assert motion.compress_track(samples, t0) == []


def test_neutral_unit_difference_is_documented() -> None:
    """Given: r=0 の復元 / When: XY に戻す / Then: 中央値になること。

    ID:008 の 1 単位差（r=0 到達経路 (128,127) と inputEnd 解放 (128,128)）
    は仕様。ここでは復元式が中央を返すことだけを固定する。
    実測の基準値はテスト②・追加テスト（MotionRecorder 側）で取る。
    """
    x, y = motion.polar_to_xy(0.0, 90.0)
    assert (round(x), round(y)) == (128, 128)


def test_unwrap_crosses_branch_cut() -> None:
    """Given: ±180° をまたぐ角度列 / When: 展開 / Then: 連続になること。"""
    assert motion.unwrap_angles([170.0, -170.0, -150.0]) == [170.0, 190.0, 210.0]
