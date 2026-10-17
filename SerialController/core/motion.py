#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""motion.py - スティック軌跡の圧縮（純粋層）.

記録した送信行の座標列を、極座標の waypoint 列へ圧縮する。tkinter 非依存。
使う側は services/blockly_record.py（Recorder）。再生側は
core/CommandOperate.py の stick_move / stick_move2 が同じ補間式で読む。

手順（設計スレッド ID:001〜009 の確定事項）:
  1. 8ms 格子化 … at を round((at - t0) / 0.008) へ寄せる。前値ホールドで
     埋める（keepalive で間引かれた区間は「値が変わっていない」と扱う）。
  2. 極座標化 … (x, y) → (r, θ)。θ は ±180° で折り返さず累積角度へ展開する。
     r < 8 の点では θ を有効な隣の値で固定する。中心に入る点では区間を
     強制分割する（中心素通りで θ が π 跳ぶため）。中立のまま留まる区間
     （解放して動かしていない間）では分割しない。1 点ごとに切ると 2 点
     ずつの断片が格子の数だけ並び、waypoint が数百個になる。
  3. RDP … 誤差は 8ms slot 各時刻での復元点と元点の XY 距離で測る
     （幾何距離ではなく時間方向の誤差。速度情報が残る）。内部の補間も
     出力と同じ展開済み θ で行う。折り返し前の θ で測ると別の経路を
     測ることになり、点が無駄に残る。
  4. 量子化 … t は整数 ms、r は整数、θ は整数度。量子化後に誤差を再検証し、
     ε を超えた格子時刻へその時刻の理想値の点を足す（区間の中点は折れ線の
     上にあるため形が変わらず、丸め直しの分しか誤差が減らない）。ε が丸め
     下限（r=127 の整数度で約 1.1）以下なら何点足しても満たせないため、
     量子化の限界として打ち切る。
  5. 区間合成 … 分割時刻は「L の waypoint ∪ R の waypoint ∪ ボタン/Hat
     イベント時刻」。分割点の値は圧縮後の折れ線から補間で求める
     （生データから取らない。極座標で線形な区間を途中で切っても ε 保証が
     保たれる）。実体は services/blockly_record.py 側。

RDP は点数最小を保証しないが誤差上限は保証する。厳密最小の動的計画法は
O(n^2)〜O(n^3) で、得られるのは数点の差のため不採用。

中立値の注意（ID:008 確定）: r=0 到達経路は input 経由で Y 反転が掛かり
(128, 127)、inputEnd 解放は反転なしで (128, 128) になる。1 単位差は仕様で、
再生は ε=2.5 未満、記録の読み戻しでは r=1 となりデッドゾーン（r<8）の
内側のため実害はない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from loguru import logger

#: 8ms slot の格子幅（秒）。live worker の送出周期。
SLOT_S = 0.008
#: デッドゾーン。r がこれ未満の点では θ を有効な隣の値で固定する。
DEADZONE_R = 8.0
#: 中心とみなす r。ここへ入る点で区間を強制分割する。
CENTER_R = 1.5
#: 既定の許容誤差 ε（u8 単位の XY 距離）。実データで点数-ε 曲線を測って確定する。
DEFAULT_EPS = 2.5
#: 中央値（u8）。
CENTER = 128.0
#: 量子化の理論上の上限（u8）。r の丸め ±0.5 と整数度丸め（最大半径
#: hypot(128,128) ≈ 181 で 181×rad(0.5) ≈ 1.58）が足し合わさる分。
#: ε がこれ以下では、経路によっては ε を満たせない（実測は経路依存で、
#: ランダム経路 1.53・四隅 2.08 程度）。いまはテストの許容判定だけが読む。
QUANT_FLOOR = 0.5 + math.hypot(CENTER, CENTER) * math.radians(0.5)
#: 量子化後の点を足す回数の上限。ε が下限に近いときの足しすぎを防ぐ。
REFINE_MAX_ROUNDS = 4


@dataclass
class Waypoint:
    """1 つの waypoint。t は区間開始からの相対 ms、r・th は極座標。"""

    t: float
    r: float
    th: float


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
    prev: float | None = None
    for th in ths:
        if prev is None:
            out.append(th)
        else:
            delta = (th - prev + 180.0) % 360.0 - 180.0
            out.append(prev + delta)
        prev = out[-1]
    return out


def _as_float(points: list[Waypoint]) -> list[Waypoint]:
    """整数 waypoint を補間用の float 列にする。"""
    return [Waypoint(float(w.t), float(w.r), float(w.th)) for w in points]


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


def _rdp_polar(
    pts: list[tuple[float, float, float, float, float]],
    eps: float,
) -> list[int]:
    """RDP で残す点の添字を返す。誤差は XY 距離（時間方向）で測る。

    pts は [(t, r, θ, x, y)]。θ は展開済みで出力と同じ表現、(x, y) は元の
    サンプル。両端は必ず残す。誤差の測り方は _max_error_xy と同じ
    （区間の両端を結ぶ直線で復元し、各 slot 時刻の XY 距離を見る）。
    """
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        seg = [Waypoint(pts[lo][0], pts[lo][1], pts[lo][2])]
        seg.append(Waypoint(pts[hi][0], pts[hi][1], pts[hi][2]))
        worst, at = 0.0, -1
        for i in range(lo + 1, hi):
            t, _r, _th, x, y = pts[i]
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


def _fill_theta(
    polars: list[tuple[float, float, float]],
    raw_th: list[float],
    borrow_r: float = DEADZONE_R,
) -> list[float]:
    """r が borrow_r 未満の点の θ を、有効な隣の θ で埋める（設計 ID:006）。

    前向きに埋めたあと、後ろ向きの有効値で上書きする。中心付近の θ は
    「意味のない値」なので、倒れてきた/倒れていく先の向きを使う。

    borrow_r を DEADZONE_R より小さくできるのは、θ を借りた点が XY で
    最大 r + round(r) 動くためである。2r + 0.5 <= ε の範囲に限れば、
    この移動だけで ε を超えることはない（_compress_run が
    min(DEADZONE_R, (ε - 0.5) / 2) を渡す）。
    """
    fixed = list(raw_th)
    fwd: float | None = None
    for i, (_t, r, th) in enumerate(polars):
        if r >= borrow_r:
            fwd = th
        elif fwd is not None:
            fixed[i] = fwd
    bwd: float | None = None
    for i in range(len(polars) - 1, -1, -1):
        _t, r, _th = polars[i]
        if r >= borrow_r:
            bwd = fixed[i]
        elif bwd is not None:
            fixed[i] = bwd
    return fixed


def _split_at_center(
    samples: list[tuple[float, float, float]],
) -> list[list[tuple[float, float, float]]]:
    """中心へ入る点で分割する。空区間は作らない。

    中立のまま留まる区間では切らない（切ると 2 点ずつの断片が格子の数だけ
    でき、waypoint が数百個になる）。中心から出る側は _compress_run が
    θ を埋めるため、分割しなくても直線になる。
    """
    runs: list[list[tuple[float, float, float]]] = []
    cur: list[tuple[float, float, float]] = []
    inside = False
    for t, x, y in samples:
        r, _ = xy_to_polar(x, y)
        cur.append((t, x, y))
        now_inside = r <= CENTER_R
        if now_inside and not inside and len(cur) > 1:
            runs.append(cur)
            cur = [(t, x, y)]
        inside = now_inside
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

    目標: ε > QUANT_FLOOR のときは、各格子時刻の誤差を ε 以内に収める。
    そのために、量子化で超えた分だけ単純化の目標を締めて作り直す（3 周まで）。
    それでも超えたままなら warning を 1 回残す（refine にも回数上限がある）。
    ε <= QUANT_FLOOR のときは、作り直しと refine を回しても届かないことが
    ある（誤差の上限は QUANT_FLOOR で、実測値は経路依存）。届かなければ
    warning を 1 回残す。
    誤差は元サンプルではなく、8ms 格子化と前値ホールドを掛けた後の track に
    対して測る（同じ格子時刻で比較する）。
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
    # 量子化（t/r/θ を整数化）で ε を超えたら、超えた分だけ単純化の目標を
    # 締めて作り直す。一律に QUANT_FLOOR を引くと、綺麗な入力（丸めの影響が
    # 小さい軌跡）でも点が無駄に増える。締め直しは 3 周までで、目標が 0 に
    # なったらそこで止める（全点を残せば誤差は量子化の分だけになる）。
    target = eps
    quant: list[Waypoint] = []
    ideal: dict[float, tuple[float, float]] = {}
    for _ in range(3):
        out: list[Waypoint] = []
        ideal = {}
        for run in _split_at_center(track):
            points, shape = _compress_run(run, target)
            out.extend(points)
            ideal.update(shape)
        if not out:
            return []
        quant = [Waypoint(round(w.t), round(w.r), round(w.th)) for w in out]
        if target <= 0.0:
            break
        worst = _max_error_xy(track, _as_float(quant))
        if worst <= eps:
            break
        # 量子化で超えた分（＋余白）だけ目標を下げて作り直す。
        target = max(0.0, target - (worst - eps) - 0.05)
    if _max_error_xy(track, _as_float(quant)) > eps:
        # 目標を 0 まで下げても超える場合（ε が量子化の下限以下など）は、
        # 理想値の点を足して届くところまで寄せ、1 回だけ警告を残す。
        quant = _refine_quantized(track, quant, eps, ideal)
        _warn_if_over_target(track, quant, eps)
    return quant


#: 目標を超えたままだったことを 1 回だけ知らせる（同じ呼び出しが何度も来るため）。
_OVER_TARGET_WARNED = False


def _warn_if_over_target(
    track: list[tuple[float, float, float]],
    quant: list[Waypoint],
    eps: float,
) -> None:
    """量子化後も ε を超えていたら 1 回だけ警告する（挙動は変えない）。

    ここに来るのは、refine の回数上限に達したか、既存の waypoint の値を
    直さないと縮まない誤差が残った場合である。将来の退行に気づくための記録。
    """
    global _OVER_TARGET_WARNED
    if _OVER_TARGET_WARNED:
        return
    worst = _max_error_xy(track, _as_float(quant))
    if worst > eps:
        _OVER_TARGET_WARNED = True
        logger.warning(
            f"軌跡圧縮が ε を超えました: ε={eps} 実測={worst:.2f}"
            "（以降の同種の警告は省略します）"
        )


def _compress_run(
    run: list[tuple[float, float, float]],
    eps: float,
) -> tuple[list[Waypoint], dict[float, tuple[float, float]]]:
    """中心を通らない 1 区間を圧縮する。

    返すのは (waypoint 列, {格子時刻: (r, θ)})。後者は量子化のやり直しで
    「その時刻に本来あるべき値」を足すために使う（折れ線から読むと、
    誤差のある値をもう一度置くことになり誤差が減らない）。
    """
    # 極座標化＋θ 展開（r < 8 の点では θ を有効な隣の値で固定）。
    # 1パス目: r >= DEADZONE の点だけで θ の基準を決める。r=0 の点の θ は
    # 「意味のない値」として、有効な隣の θ で埋める（設計ID:006）。
    # 中心から真っすぐ倒す・真っすぐ戻す動きが直線になる。
    polars: list[tuple[float, float, float]] = []
    raw_th: list[float] = []
    for t, x, y in run:
        r, th = xy_to_polar(x, y)
        raw_th.append(th)
        polars.append((t, r, th))
    # 借りた点は XY で最大 r + round(r) 動く。round(r) <= r + 0.5 なので
    # 2r + 0.5 <= eps の範囲だけ借りる（ε=3.4 でも保証が崩れないように）。
    #   例: ε=2.5 なら r<1.0、ε=3.4 なら r<1.45。
    borrow_r = min(DEADZONE_R, max(0.0, (eps - 0.5) / 2.0))
    fixed = _fill_theta(polars, raw_th, borrow_r)
    unwrapped = unwrap_angles(fixed)
    pts = [
        (t, r, th, x, y) for (t, r, _th), th, (_t2, x, y) in zip(polars, unwrapped, run)
    ]
    keep = _rdp_polar(pts, eps)
    shape = {t: (r, th) for t, r, th, _x, _y in pts}
    return [Waypoint(pts[i][0], pts[i][1], pts[i][2]) for i in keep], shape


def _refine_quantized(
    track: list[tuple[float, float, float]],
    quant: list[Waypoint],
    eps: float,
    ideal: dict[float, tuple[float, float]] | None = None,
) -> list[Waypoint]:
    """量子化で ε を超えた格子時刻へ、その時刻の理想値の点を足す。

    ideal は {格子時刻: (r, θ)}。省略時は元の (x, y) から作る。
    同じ時刻へ二度は足さない（そこは丸め誤差が下限）。ε が小さくても
    打ち切らない（回数上限と「既に点がある」判定で必ず止まる）。
    """
    if ideal is None:
        ideal = {t: xy_to_polar(x, y) for t, x, y in track}
    out = [Waypoint(round(w.t), round(w.r), round(w.th)) for w in quant]
    if len(out) < 2:
        return out
    for _ in range(REFINE_MAX_ROUNDS):
        qf = _as_float(out)
        worst, at_t = 0.0, 0.0
        for t, x, y in track:
            r, th = _restore_polar(qf, t)
            px, py = polar_to_xy(r, th)
            err = math.hypot(px - x, py - y)
            if err > worst:
                worst, at_t = err, t
        if worst <= eps:
            break
        if any(w.t == round(at_t) for w in out):
            # その時刻には既に点がある。残る差は丸めの下限で、縮まない。
            break
        pr = ideal.get(at_t)
        if pr is None:
            break
        out.append(
            Waypoint(float(round(at_t)), float(round(pr[0])), float(round(pr[1])))
        )
        out.sort(key=lambda w: w.t)
    return out


def restore_xy(points: list[Waypoint], t_ms: float) -> tuple[float, float]:
    """waypoint 列から時刻 t_ms の (x, y)（Direction 規約）を求める。

    再生側（stick_move と同じ補間式）のオフライン評価用。r=0 の点の θ は
    意味のない値として扱い、隣の θ を使う（記録時にコピー済みの想定）。
    """
    r, th = _restore_polar(points, t_ms)
    return polar_to_xy(r, th)
