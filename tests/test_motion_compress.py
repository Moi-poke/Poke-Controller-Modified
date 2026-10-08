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
    """1 周の円（r=127）。8ms 格子相当のサンプル列。

    座標は送信系（y は上が小さい）。θ=90° が上で反時計回りが正。
    """
    out: list[tuple[float, float, float]] = []
    for i in range(n):
        at = t0 + i * 0.008
        deg = 90 + i * 360 / (n - 1)
        x = 128 + 127 * math.cos(math.radians(deg))
        y = 128 - 127 * math.sin(math.radians(deg))
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
    # 上方向は送信系で y が減る。dy=CENTER-y で正に戻すため θ=+90° が上。
    samples: list[tuple[float, float, float]] = [
        (t0 + i * 0.008, 128.0, 128.0 - i * 127.0 / 19) for i in range(20)
    ]
    pts = motion.compress_track(samples, t0, 2.5)
    assert len(pts) >= 2
    # r=0 の点の θ も隣の有効値（+90°＝上）になっている。
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


def test_line_through_center_splits_with_bounded_error() -> None:
    """Given: 中心を貫く左→右の直線(r<=1.5通過) / When: compress_trackする / Then: 2区間以上に分割され各waypoint列の復元誤差がε以下であること。"""
    t0 = 5000.0
    n = 31
    samples = [(t0 + i * 0.008, 8.0 + 240.0 * i / (n - 1), 128.0) for i in range(n)]
    assert min(math.hypot(x - 128, y - 128) for _, x, y in samples) <= 1.5
    pts = motion.compress_track(samples, t0, 2.5)
    assert len(pts) >= 2
    times = [p.t for p in pts]
    assert len(set(times)) < len(times) or any(p.r == 0 for p in pts)
    worst = _restore_error(pts, samples, t0)
    assert worst <= 2.5 + 1.2


def test_refine_quantized_adds_midpoint_and_stops_at_depth() -> None:
    """Given: 誤差超過のtrackと2点の量子化列 / When: _refine_quantizedを直接呼ぶ / Then: 中点が追加され深さ上限で止まること。"""
    x0, y0 = motion.polar_to_xy(100.0, 90.0)
    x1, y1 = motion.polar_to_xy(40.0, 135.0)
    x2, y2 = motion.polar_to_xy(100.0, 180.0)
    track = [(0.0, x0, y0), (100.0, x1, y1), (200.0, x2, y2)]
    quant = [motion.Waypoint(0, 100, 90), motion.Waypoint(200, 100, 180)]
    before = motion._max_error_xy(
        track, [motion.Waypoint(float(w.t), float(w.r), float(w.th)) for w in quant]
    )
    assert before > 2.5
    out = motion._refine_quantized(track, list(quant), 2.5)
    assert len(out) > len(quant)
    assert [w.t for w in out] == sorted(w.t for w in out)
    capped = motion._refine_quantized(track, list(quant), 2.5, depth=4)
    assert capped == quant


def test_clockwise_turn_accumulates_negative() -> None:
    """Given: 時計回り1周の円 / When: 圧縮 / Then: θ が -360° 蓄積されること。"""
    t0 = 6000.0
    n = 63
    samples: list[tuple[float, float, float]] = []
    for i in range(n):
        at = t0 + i * 0.008
        deg = 90 - i * 360 / (n - 1)
        x = 128 + 127 * math.cos(math.radians(deg))
        y = 128 - 127 * math.sin(math.radians(deg))
        samples.append((at, x, y))
    pts = motion.compress_track(samples, t0, 2.5)
    assert len(pts) >= 2
    assert abs((pts[-1].th - pts[0].th) - -360.0) <= 2.0


def test_four_directions_roundtrip() -> None:
    """Given: 上下左右への倒し / When: 圧縮→復元 / Then: 各方向が保たれること。"""
    t0 = 7000.0
    # (dx, dy): 上下左右。送信系で上が y 減。
    cases = [
        ((0, -127), lambda x, y: y < 128.0, "up"),
        ((0, 127), lambda x, y: y > 128.0, "down"),
        ((127, 0), lambda x, y: x > 128.0, "right"),
        ((-127, 0), lambda x, y: x < 128.0, "left"),
    ]
    for (dx, dy), check, _label in cases:
        samples = [
            (t0 + i * 0.008, 128.0 + dx * i / 19, 128.0 + dy * i / 19)
            for i in range(20)
        ]
        pts = motion.compress_track(samples, t0, 2.5)
        assert pts, "倒しが waypoint になること"
        x, y = motion.restore_xy(pts, pts[-1].t)
        assert check(x, y), "方向が保たれること"
