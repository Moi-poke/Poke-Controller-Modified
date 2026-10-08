#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""motion.py - スティック軌跡の圧縮（純粋層）.

記録した送信行の座標列を、極座標の waypoint 列へ圧縮する。tkinter 非依存。
使う側は services/blockly_record.py（MotionRecorder）。再生側は
core/CommandOperate.py の stick_move / stick_move2 が同じ補間式で読む。

手順（設計スレッド ID:001〜009 の確定事項）:
  1. 8ms 格子化 … at を round((at - t0) / 0.008) へ寄せる。前値ホールドで
     埋める（keepalive で間引かれた区間は「値が変わっていない」と扱う）。
  2. 極座標化 … (x, y) → (r, θ)。θ は ±180° で折り返さず累積角度へ展開する。
     r < 8 の点では θ を直前の値で固定する。r がほぼ 0 になる点では
     区間を強制分割する（中心素通りで θ が π 跳ぶため）。
  3. RDP … 誤差は 8ms slot 各時刻での復元点と元点の XY 距離で測る
     （幾何距離ではなく時間方向の誤差。速度情報が残る）。
  4. 量子化 … t は整数 ms、r は整数、θ は整数度。量子化後に誤差を再検証し、
     ε を超えた区間には点を追加する（整数度丸めは r=127 で最大約 1.1 ずれる）。
  5. 区間合成 … 分割時刻は「L の waypoint ∪ R の waypoint ∪ ボタン/Hat
     イベント時刻」。分割点の値は圧縮後の折れ線から補間で求める
     （生データから取らない。極座標で線形な区間を途中で切っても ε 保証が
     保たれる）。

RDP は点数最小を保証しないが誤差上限は保証する。厳密最小の動的計画法は
O(n^2)〜O(n^3) で、得られるのは数点の差のため不採用。

中立値の注意（ID:008 確定）: r=0 到達経路は input 経由で Y 反転が掛かり
(128, 127)、inputEnd 解放は反転なしで (128, 128) になる。1 単位差は仕様で、
再生は ε=2.5 未満、記録の読み戻しでは r=1 となりデッドゾーン（r<8）の内側
のため実害はない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: 8ms slot の格子幅（秒）。live worker の送出周期。
SLOT_S = 0.008
#: デッドゾーン。r がこれ未満の点では θ を直前の値で固定する。
DEADZONE_R = 8.0
#: 中心素通りとみなす r。ここで区間を強制分割する。
CENTER_R = 1.5
#: 既定の許容誤差 ε（u8 単位の XY 距離）。実データで点数-ε 曲線を測って確定する。
DEFAULT_EPS = 2.5
#: 中央値（u8）。
CENTER = 128.0


@dataclass
class Waypoint:
    """1 つの waypoint。t は区間開始からの相対 ms、r・th は極座標。"""

    t: float
    r: float
    th: float


@dataclass
class StickTrack:
    """片側スティックの圧縮結果。"""

    side: str  # "L" / "R"
    points: list[Waypoint] = field(default_factory=list)


@dataclass
class MotionClip:
    """1 クリップの圧縮結果。時刻はクリップ開始からの相対 ms。"""

    duration_ms: float = 0.0
    left: StickTrack = field(default_factory=lambda: StickTrack("L"))
    right: StickTrack = field(default_factory=lambda: StickTrack("R"))
    # ボタン/Hat イベント [(t_ms, name, on)]。時刻順。
    buttons: list[tuple[float, str, bool]] = field(default_factory=list)


def xy_to_polar(x: float, y: float) -> tuple[float, float]:
    """(x, y) を (r, θ[度]) へ直す。θ の原点は +x 方向、反時計回りが正。

    入るのは送信行の座標系（y は上が小さい）。InputLog.angle と同じく
    dy = CENTER - y として上を正に戻す。以前は dy = y - CENTER で θ の
    符号が反転し、再生で回転方向・上下が逆になっていた。
    """
    dx, dy = x - CENTER, CENTER - y
    return math.hypot(dx, dy), math.degrees(math.atan2(dy, dx))


def polar_to_xy(r: float, deg: float) -> tuple[float, float]:
    """(r, θ[度]) を送信行の (x, y)（y は上が小さい）へ戻す。"""
    a = math.radians(deg)
    return 128.0 + r * math.cos(a), 128.0 - r * math.sin(a)


def unwrap_angles(ths: list[float]) -> list[float]:
    """角度列を累積角度へ展開する（±180° の跳びを繋げる）。"""
    out: list[float] = []
    offset = 0.0
    prev: float | None = None
    for th in ths:
        if prev is not None:
            delta = (th - prev + 180.0) % 360.0 - 180.0
            offset += delta
            out.append(out[0] + offset if False else th + (offset - (th - ths[0])))
            # 上は分かりにくいので素直に書く: 前回値からの最短差を積む。
            out[-1] = prev + delta
        else:
            out.append(th)
        prev = out[-1]
    return out


def _restore_polar(points: list[Waypoint], t: float) -> tuple[float, float]:
    """折れ線（極座標の線形補間）から時刻 t の (r, θ) を求める。"""
    if not points:
        return 0.0, 0.0
    if t <= points[0].t:
        return points[0].r, points[0].th
    if t >= points[-1].t:
        return points[-1].r, points[-1].th
    for a, b in zip(points, points[1:]):
        if a.t <= t <= b.t:
            span = b.t - a.t
            u = (t - a.t) / span if span > 0 else 0.0
            return a.r + u * (b.r - a.r), a.th + u * (b.th - a.th)
    return points[-1].r, points[-1].th


def _max_error_xy(
    raw: list[tuple[float, float, float]],
    points: list[Waypoint],
) -> float:
    """各 slot 時刻での復元点と元点の XY 距離の最大値。"""
    worst = 0.0
    for t, x, y in raw:
        r, th = _restore_polar(points, t)
        px, py = polar_to_xy(r, th)
        worst = max(worst, math.hypot(px - x, py - y))
    return worst


def _rdp_polar(raw: list[tuple[float, float, float]], eps: float) -> list[int]:
    """RDP で残す点の添字を返す。誤差は XY 距離（時間方向）で測る。

    raw は [(t, x, y)]。両端は必ず残す。誤差の測り方は _max_error_xy と
    同じ（区間の両端を結ぶ直線で復元し、各 slot 時刻の XY 距離を見る）。
    """
    n = len(raw)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        seg = [Waypoint(raw[lo][0], 0.0, 0.0), Waypoint(raw[hi][0], 0.0, 0.0)]
        # 区間の両端だけでは極座標が要るため、一時的に raw の両端の
        # (r, th) を求めて直線復元する。
        (_, (r0, th0)) = _polar_of(raw[lo])
        (_, (r1, th1)) = _polar_of(raw[hi])
        seg = [Waypoint(raw[lo][0], r0, th0), Waypoint(raw[hi][0], r1, th1)]
        worst, at = 0.0, -1
        for i in range(lo + 1, hi):
            t, x, y = raw[i]
            r, th = _restore_polar(seg, t)
            px, py = polar_to_xy(r, th)
            err = math.hypot(px - x, py - y)
            if err > worst:
                worst, at = err, i
        if worst > eps and at >= 0:
            keep[at] = True
            stack.append((lo, at))
            stack.append((at, hi))
    return [i for i, k in enumerate(keep) if k]


def _polar_of(
    sample: tuple[float, float, float],
) -> tuple[None, tuple[float, float]]:
    """RDP 内部用。(t, x, y) の (r, th) を返す（θ 展開は呼び側で行う）。"""
    _, x, y = sample
    return None, xy_to_polar(x, y)


def _split_at_center(
    samples: list[tuple[float, float, float]],
) -> list[list[tuple[float, float, float]]]:
    """r がほぼ 0 になる点で分割する。空区間は作らない。"""
    runs: list[list[tuple[float, float, float]]] = []
    cur: list[tuple[float, float, float]] = []
    for t, x, y in samples:
        r, _ = xy_to_polar(x, y)
        cur.append((t, x, y))
        if r <= CENTER_R and len(cur) > 1:
            runs.append(cur)
            cur = [(t, x, y)]
    if cur:
        # 末尾が 1 点だけの断片は直前の区間へ畳む。
        if len(cur) == 1 and runs:
            runs[-1].append(cur[0])
        else:
            runs.append(cur)
    return [run for run in runs if len(run) >= 2]


def compress_track(
    samples: list[tuple[float, float, float]],
    t0: float,
    eps: float = DEFAULT_EPS,
) -> list[Waypoint]:
    """片側の座標列 [(at, x, y)...] を waypoint 列へ圧縮する。

    samples の at は perf_counter 値。t0（クリップ開始）からの相対 ms で
    waypoint を作る。変化がなければ空リストを返す（静止区間）。
    """
    if len(samples) < 2:
        return []
    # 8ms 格子化＋前値ホールド。
    grid: dict[int, tuple[float, float]] = {}
    for at, x, y in samples:
        slot = round((at - t0) / SLOT_S)
        grid[slot] = (float(x), float(y))
    if not grid:
        return []
    lo, hi = min(grid), max(grid)
    track: list[tuple[float, float, float]] = []
    last = grid[lo]
    for slot in range(lo, hi + 1):
        if slot in grid:
            last = grid[slot]
        track.append((slot * SLOT_S * 1000.0, last[0], last[1]))
    # 全点が中央なら静止。
    if all(math.hypot(x - CENTER, y - CENTER) < DEADZONE_R for _, x, y in track):
        return []
    out: list[Waypoint] = []
    for run in _split_at_center(track):
        out.extend(_compress_run(run, eps))
    if not out:
        return []
    # 量子化（t: 整数 ms、r: 整数、θ: 整数度）＋再検証。
    quant = [Waypoint(round(w.t), round(w.r), round(w.th)) for w in out]
    if (
        _max_error_xy(
            track, [Waypoint(float(w.t), float(w.r), float(w.th)) for w in quant]
        )
        > eps
    ):
        # 量子化で ε を超えた区間に点を追加する。素朴に全中点を追加し、
        # 収束するまで繰り返す（深さ上限付き）。
        quant = _refine_quantized(track, quant, eps)
    return quant


def _compress_run(run: list[tuple[float, float, float]], eps: float) -> list[Waypoint]:
    """中心を通らない 1 区間を圧縮する。"""
    # 極座標化＋θ 展開（r < 8 の点では θ を直前で固定）。
    # 1パス目: r >= DEADZONE の点だけで θ の基準を決める。r=0 の点の θ は
    # 「意味のない値」として、後の有効な θ で埋める（設計ID:006）。
    # 中心から真っすぐ倒す・真っすぐ戻す動きが直線になる。
    polars: list[tuple[float, float, float]] = []
    raw_th: list[float] = []
    for t, x, y in run:
        r, th = xy_to_polar(x, y)
        raw_th.append(th)
        polars.append((t, r, th))
    # 有効な θ（r が十分大きい点のもの）を前向き・後ろ向きに伝搬する。
    fixed = list(raw_th)
    fwd: float | None = None
    for i, (t, r, th) in enumerate(polars):
        if r >= DEADZONE_R:
            fwd = th
        elif fwd is not None:
            fixed[i] = fwd
    bwd: float | None = None
    for i in range(len(polars) - 1, -1, -1):
        t, r, th = polars[i]
        if r >= DEADZONE_R:
            bwd = fixed[i]
        elif bwd is not None:
            fixed[i] = bwd
    unwrapped = unwrap_angles(fixed)
    polars = [(t, r, th) for (t, r, _), th in zip(polars, unwrapped)]
    keep = _rdp_polar(run, eps)
    # keep は raw 添字。polars と対応付けて waypoint 化する。
    # RDP 内部の θ は未展開のため、ここでは polars の展開済みを使う。
    kept_t = {run[i][0] for i in keep}
    return [Waypoint(t, r, th) for t, r, th in polars if t in kept_t]


def _refine_quantized(
    track: list[tuple[float, float, float]],
    quant: list[Waypoint],
    eps: float,
    depth: int = 0,
) -> list[Waypoint]:
    """量子化後の誤差超過区間に中点を追加する（深さ上限 4）。"""
    if depth >= 4 or len(quant) < 2:
        return quant
    qf = [Waypoint(float(w.t), float(w.r), float(w.th)) for w in quant]
    # 最も誤差の大きい slot を探す。
    worst, at_t = 0.0, 0.0
    for t, x, y in track:
        r, th = _restore_polar(qf, t)
        px, py = polar_to_xy(r, th)
        err = math.hypot(px - x, py - y)
        if err > worst:
            worst, at_t = err, t
    if worst <= eps:
        return quant
    # at_t を含む区間の中点を追加する。
    for i, (a, b) in enumerate(zip(quant, quant[1:])):
        if a.t <= at_t <= b.t and b.t > a.t:
            mid_r, mid_th = _restore_polar(qf, (a.t + b.t) / 2.0)
            quant.insert(
                i + 1, Waypoint(round((a.t + b.t) / 2.0), round(mid_r), round(mid_th))
            )
            break
    else:
        return quant
    return _refine_quantized(track, quant, eps, depth + 1)


def restore_xy(points: list[Waypoint], t_ms: float) -> tuple[float, float]:
    """waypoint 列から時刻 t_ms の (x, y)（Direction 規約）を求める。

    再生側（stick_move と同じ補間式）のオフライン評価用。r=0 の点の θ は
    意味のない値として扱い、隣の θ を使う（記録時にコピー済みの想定）。
    """
    r, th = _restore_polar(points, t_ms)
    return polar_to_xy(r, th)
