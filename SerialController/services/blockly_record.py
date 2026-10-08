#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""blockly_record.py - 本体の操作を記録し、押すブロックの手順へ変換する。

送信線へ出た入力（仮想コントローラ・キーボード・ゲームパッドのどれでも、
Sender から Transport へ出た行が材料）を InputLogger の差分判定でイベントへ
戻し、編集画面の `pokecon_press` ブロックの並び（押す・待つ）にして挿入する
ための材料を作る。「操作に合わせた形で入力したい」という要望への答え。

同時押しの再現は扱わない。重なった区間は無視し、離した順に並べるだけに
する（離した順が押した順と食い違うと、再現時に別の順で押されるため、
同時押しのつもりの操作は手順として残さない、ではなく順序だけ残す）。

tkinter には触らない。受け口（ui/blockly_editor.py）が GUI スレッドで
付け外しし、ここは線の聞き手と純粋な変換だけを持つ。
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from core import InputLog, motion as _motion

#: 0.05秒単位に丸めるための刻み。編集画面の DURATION・WAIT 欄が
#: 0.05秒刻みでも扱いやすいよう、ここで寄せる。
STEP_ROUND = 0.05
#: 丸めた後の最小値。0にすると「押していない」ことになり、
#: 編集画面の数値欄でも 0 は許すが、再現時に押したことが消える。
STEP_MIN = 0.05
#: 丸めた後の上限。60秒を超える待ちは、再現時に固まったように見える。
STEP_MAX = 60.0
#: 最後の手順の待ち。次が無いため、次の操作までの時間は測れない。
#: 編集画面の既定（0.1秒）に揃える。
LAST_WAIT = 0.1


def _round_step(value: float) -> float:
    """0.05秒単位に丸め、最小0.05・上限60に収める。"""
    try:
        stepped = round(float(value) / STEP_ROUND) * STEP_ROUND
    except (TypeError, ValueError):
        return STEP_MIN
    # 丸めの浮動小数点誤差（0.30000000000000004等）を欄の桁に寄せる。
    stepped = round(stepped, 2)
    if stepped < STEP_MIN:
        return STEP_MIN
    if stepped > STEP_MAX:
        return STEP_MAX
    return stepped


def _step_target(ev: InputLog.InputEvent) -> str:
    """イベントをブロックの入力欄の値の書式へ直す。

    ボタンは `Button.A` 形、十字キーは `Hat.TOP` 形のまま返す。
    スティックは `command_target` が8方向名（`Direction.UP` 等）を返す
    ときはそのまま使い、半倒し・中間角度で座標のまま
    （`Direction(Stick.LEFT, ...)`）のときは8方向へ丸める。丸めるのは、
    記録の目的が「押すブロックの並び」で、座標のままでは入力欄の候補に
    無い値になり、挿入時に選べないため。
    """
    if ev.kind != "stick":
        return ev.name
    try:
        target = InputLog.command_target(ev)
    except Exception:
        target = ""
    if target.startswith("Direction.") and "(" not in target:
        return target
    # 座標のまま・空のときは8方向へ丸める。左右の区別は名前の末尾で見る。
    label = InputLog.snap_direction(ev.deg)
    if not label:
        return target or ev.name
    prefix = "R_" if ev.name.endswith("RIGHT") else ""
    return f"Direction.{prefix}{label}"


def events_to_steps(events: list[InputLog.InputEvent]) -> list[dict[str, Any]]:
    """イベント列を手順（押す・待つの並び）へ変換する。純関数。

    各 RELEASE ごとに `{"target": 値, "duration": 秒, "wait": 秒}` を作る。
    `duration` は押していた時間（RELEASE の `duration`）、`wait` は離して
    から次の PRESS までの時間（最後の手順は 0.1）。どちらも 0.05秒単位に
    丸め、最小 0.05、上限 60。PRESS が無い RELEASE（記録開始前から
    押しっぱなしだったもの）は捨てる。同時押し（重なり）は、重なった
    区間を無視して離した順に並べるだけで、同時押しの再現はしない。
    """
    releases = [ev for ev in events if ev.action == "RELEASE"]
    presses = sorted(
        (ev for ev in events if ev.action == "PRESS"),
        key=lambda ev: ev.at,
    )
    steps: list[dict[str, Any]] = []
    for i, rel in enumerate(releases):
        if rel.duration is None:
            # PRESS が無い RELEASE。開始前から押していたものなので捨てる。
            continue
        target = _step_target(rel)
        if not target:
            continue
        duration = _round_step(rel.duration)
        # 次の PRESS（この RELEASE より後の最初のもの）までの時間。
        # 重なりの区間は無視し、離した順に並べるだけにする。
        wait: float = LAST_WAIT
        for pr in presses:
            if pr.at > rel.at:
                wait = _round_step(pr.at - rel.at)
                break
        steps.append({"target": target, "duration": duration, "wait": wait})
    # 最後の手順の待ちは次が無いため、丸め直さず既定にする。
    if steps:
        steps[-1]["wait"] = LAST_WAIT
    return steps


#: スティック操作をクリップとみなす r の閾値（u8 距離）。
#: motion.DEADZONE_R と同じ意味。ここでは座標で判定する。
MOTION_DEADZONE_XY = 8.0
#: クリップ終了の無操作継続（秒）。両スティック中立かつボタン保持なしが
#: これだけ続けばクリップを閉じる。
CLIP_IDLE_S = 0.15
#: 解放用の固定の斜め座標。両軸が閾値 127 を超えるため両 tilt が返り、
#: 両軸とも解放される（ID:006 確定。中立 Direction では片軸が残る）。
RELEASE_XY = (218, 218)


class _RecordLogger(InputLog.InputLogger):
    """記録用の聞き手。行の書式化はせず、PRESS/RELEASE だけを溜める。

    `feed` は親のものをそのまま使い、`_output` だけを上書きする。
    親の集約・流量制限は通さない（記録は表示ではないため、まとめたり
    捨てたりしてはならない）。
    """

    def __init__(self) -> None:
        super().__init__(emit=lambda _line: None)
        self._events_lock = threading.Lock()
        self._events: list[InputLog.InputEvent] = []

    def _output(self, events: list[InputLog.InputEvent]) -> None:
        """PRESS/RELEASE だけを錠つきのリストへ溜める。"""
        kept = [ev for ev in events if ev.action in ("PRESS", "RELEASE")]
        if not kept:
            return
        with self._events_lock:
            self._events.extend(kept)

    def snapshot(self) -> list[InputLog.InputEvent]:
        """溜めたイベントの写しを返す。"""
        with self._events_lock:
            return list(self._events)

    def clear(self) -> None:
        """溜めたイベントを捨て、押下状態も中立へ戻す。

        押下状態を残すと、前回の記録の途中で押したままだったものが、
        次の記録で「長く押していた操作」として出てしまう。
        """
        with self._lock:
            self._reset_state()
        with self._events_lock:
            self._events.clear()


class Recorder:
    """送信線への記録の付け外しと、手順への変換を持つ。"""

    def __init__(self, use_motion: bool = True) -> None:
        self._logger = _RecordLogger()
        self._motion_logger = _MotionLogger()
        #: スティック軌跡の記録を使うか。True なら stick 操作は motion 区間
        #: （stick_move 系の材料）になり、False なら従来の press 変換だけ。
        self.use_motion = bool(use_motion)
        self._transport: Any = None
        self._motion_transport: Any = None
        #: 記録を始めた時刻（壁時計）。手順の変換には使わない。
        self.started_at: float = 0.0

    @property
    def recording(self) -> bool:
        """いま記録中か。"""
        return self._transport is not None

    def start(self, transport: Any) -> bool:
        """送信線に記録を付ける。付けたら True。例外は外へ投げない。

        既に記録中なら False を返す（二重に付けると同じ行が二重に届く）。
        """
        try:
            if self._transport is not None:
                return False
            add = getattr(transport, "add_listener", None)
            if not callable(add):
                return False
            self._logger.clear()
            if not add(self._logger.feed):
                return False
            self._transport = transport
            # motion 用の聞き手も同じ線へ付ける。付けなくても press 記録は
            # 動く（motion 区間だけ作られない）。
            self._motion_transport = None
            if self.use_motion:
                try:
                    self._motion_logger.clear()
                    if add(self._motion_logger.feed):
                        self._motion_transport = transport
                except Exception:
                    self._motion_transport = None
            self.started_at = time.time()
            return True
        except Exception:
            return False

    def follow(self, transport: Any) -> bool:
        """記録中に線が差し替わっていたら、聞き手を新しい線へ付け替える。

        本体の Sender.setTransport は自分の入力ログの聞き手しか移さない。
        付け替えないと古い線を聞き続け、記録中のまま何も溜まらなくなる。
        付け替えたら True。記録中でない・同じ線・付けられないときは False。
        """
        try:
            old = self._transport
            if old is None or transport is None or transport is old:
                return False
            add = getattr(transport, "add_listener", None)
            if not callable(add) or not add(self._logger.feed):
                return False
            try:
                remove = getattr(old, "remove_listener", None)
                if callable(remove):
                    remove(self._logger.feed)
            except Exception:
                pass
            self._transport = transport
            # motion 用も付け替える。付けられなくても press 記録は続ける。
            try:
                if self._motion_transport is not None:
                    remove_old = getattr(old, "remove_listener", None)
                    if callable(remove_old):
                        remove_old(self._motion_logger.feed)
                    self._motion_transport = None
                if self.use_motion and add(self._motion_logger.feed):
                    self._motion_transport = transport
            except Exception:
                self._motion_transport = None
            return True
        except Exception:
            return False

    def stop(self) -> list[dict[str, Any]]:
        """記録を外し、手順を返す。記録中でなければ空。例外は外へ投げない。

        motion 用の聞き手が付いていて stick 操作があれば、motion 区間の
        材料（kind が stick/stick2/hold/hold_end/release_stick のもの）を
        混ぜて返す。無ければ従来の press 変換だけ。
        """
        try:
            transport, self._transport = self._transport, None
            if transport is None:
                return []
            try:
                remove = getattr(transport, "remove_listener", None)
                if callable(remove):
                    remove(self._logger.feed)
            except Exception:
                pass
            motion_transport, self._motion_transport = self._motion_transport, None
            if motion_transport is not None:
                try:
                    remove_motion = getattr(motion_transport, "remove_listener", None)
                    if callable(remove_motion):
                        remove_motion(self._motion_logger.feed)
                except Exception:
                    pass
            if motion_transport is not None:
                try:
                    motion_steps = build_motion_steps(self._motion_logger.snapshot())
                    if any(
                        step.get("kind") in ("stick", "stick2", "hold", "hold_end")
                        for step in motion_steps
                    ):
                        return motion_steps
                except Exception:
                    pass
            return events_to_steps(self._logger.snapshot())
        except Exception:
            return []


# ---------------------------------------------------------------------------
# スティック軌跡の記録（motion）
# ---------------------------------------------------------------------------
# PRESS/RELEASE だけでは落ちるスティックの途中経路を、極座標の waypoint 列
# として残す。圧縮の実体は core/motion.py（純粋層）。ここでは記録イベントの
# 収集・クリップ分割・区間合成・ブロック生成材料への変換だけを持つ。
#
# クリップの外側は従来どおり events_to_steps（pokecon_press 連鎖）へ回す。
# クリップ内は hold/holdEnd（待ちなし）＋ stick_move 系の呼び出し材料を作る。
# press を使うと待ちで軌跡が止まるため、クリップ内では使わない（ID:006）。


class _MotionLogger(InputLog.InputLogger):
    """軌跡記録用の聞き手。PRESS/RELEASE/CHANGE をすべて溜める。

    CHANGE にはその瞬間の座標・角度・倒し量が入る。親の集約・流量制限は
    通さない（記録は表示ではないため）。ただしスティックの CHANGE は
    log_stick_change=False だと生成されないため、記録用は True で作る。
    """

    def __init__(self) -> None:
        super().__init__(emit=lambda _line: None, log_stick_change=True)
        self._events_lock = threading.Lock()
        self._events: list[InputLog.InputEvent] = []

    def _output(self, events: list[InputLog.InputEvent]) -> None:
        """全イベントを錠つきのリストへ溜める。"""
        if not events:
            return
        with self._events_lock:
            self._events.extend(events)

    def snapshot(self) -> list[InputLog.InputEvent]:
        """溜めたイベントの写しを返す。"""
        with self._events_lock:
            return list(self._events)

    def clear(self) -> None:
        """溜めたイベントを捨て、押下状態も中立へ戻す。"""
        with self._lock:
            self._reset_state()
        with self._events_lock:
            self._events.clear()


def _stick_xy_at(
    events: list[InputLog.InputEvent],
) -> list[tuple[str, float, float, float]]:
    """スティック座標の時系列 [(side, at, x, y)] を抜く。

    PRESS/CHANGE の x・y を使う。RELEASE の座標は中立のため使わない
    （離す直前の値は CHANGE/PRESS 側にある）。at は perf_counter 値。
    """
    out: list[tuple[str, float, float, float]] = []
    for ev in events:
        if ev.kind != "stick":
            continue
        if ev.action not in ("PRESS", "CHANGE"):
            continue
        if ev.x is None or ev.y is None:
            continue
        side = "R" if ev.name.endswith("RIGHT") else "L"
        out.append((side, float(ev.at), float(ev.x), float(ev.y)))
    return out


def _motion_buttons(
    events: list[InputLog.InputEvent], t0: float
) -> list[tuple[float, str, bool]]:
    """ボタン/Hat の on/off [(t_ms, name, on)] を時刻順で返す。"""
    out: list[tuple[float, str, bool]] = []
    for ev in events:
        if ev.kind == "stick":
            continue
        if ev.action == "PRESS":
            out.append(((float(ev.at) - t0) * 1000.0, ev.name, True))
        elif ev.action == "RELEASE":
            out.append(((float(ev.at) - t0) * 1000.0, ev.name, False))
    out.sort(key=lambda item: item[0])
    return out


def build_motion_steps(
    events: list[InputLog.InputEvent],
    eps: float = _motion.DEFAULT_EPS,
) -> list[dict[str, Any]]:
    """イベント列を motion ブロックの材料へ変換する。純関数。

    返すのは [{kind, ...}] の並び。kind は "stick"（単独）/"stick2"（L+R 同時）/
    "hold" / "hold_end" / "release_stick" / "press"（クリップ外の従来手順）。
    時刻はすべて ms（整数）。T（移行時間）には 0.05 秒丸めを適用しない。

    クリップの切り方: 開始はどちらかのスティックが r >= 8、終了は両スティック
    中立かつボタン保持なしが 150ms 継続。押下区間がクリップにかかるボタンは
    クリップに含める（境界をまたぐ場合はクリップを広げる）。
    クリップ外は events_to_steps と同じ press 変換へ回す。
    """
    if not events:
        return []
    sticks = _stick_xy_at(events)
    if not sticks:
        # スティック操作なし。従来の press 変換そのまま。
        return [{"kind": "press", **step} for step in events_to_steps(events)]
    t0 = min(ev.at for ev in events)
    buttons = _motion_buttons(events, t0)
    # クリップ区間を求める（スティックが動いている範囲＋ボタンの跨ぎ＋前後余白）。
    clips = _split_clips(sticks, buttons, t0)
    if not clips:
        return [{"kind": "press", **step} for step in events_to_steps(events)]
    steps: list[dict[str, Any]] = []
    # クリップ外の press 変換用に、イベントをクリップ区間で区切る。
    for index, (clip_start, clip_end) in enumerate(clips):
        # クリップ前の press 区間。
        prev_end = clips[index - 1][1] if index > 0 else None
        before = [
            ev
            for ev in events
            if (prev_end is None or ev.at >= prev_end) and ev.at < clip_start
        ]
        for step in events_to_steps(before):
            steps.append({"kind": "press", **step})
        steps.extend(_clip_to_steps(sticks, buttons, t0, clip_start, clip_end, eps))
    # 最後のクリップ後の press 区間。
    after = [ev for ev in events if ev.at >= clips[-1][1]]
    for step in events_to_steps(after):
        steps.append({"kind": "press", **step})
    return steps


def _split_clips(
    sticks: list[tuple[str, float, float, float]],
    buttons: list[tuple[float, str, bool]],
    t0: float,
) -> list[tuple[float, float]]:
    """クリップ区間 [(開始 at, 終了 at)] を返す。at は perf_counter 値。"""
    # スティックが動いている時刻の範囲を求める。
    active = [
        at
        for _, at, x, y in sticks
        if math.hypot(x - 128.0, y - 128.0) >= MOTION_DEADZONE_XY
    ]
    if not active:
        return []
    start = min(active)
    # 終了: 最後の active から 150ms。ボタン保持があればその解放まで広げる。
    end = max(active) + CLIP_IDLE_S
    if buttons:
        # クリップにかかる押下（開始前から押下・終了後に解放）を含める。
        held: dict[str, float] = {}
        for t_ms, name, on in buttons:
            at = t0 + t_ms / 1000.0
            if on:
                held[name] = at
            else:
                held.pop(name, None)
        # 終了時点でまだ押されているものは、その解放時刻まで広げる。
        # （解放イベントが記録末尾に無い場合は CLIP_IDLE_S で閉じる。）
        for t_ms, name, on in buttons:
            at = t0 + t_ms / 1000.0
            if not on and at > end - CLIP_IDLE_S and at <= end + CLIP_IDLE_S:
                end = max(end, at)
    return [(start, end)]


def _clip_to_steps(
    sticks: list[tuple[str, float, float, float]],
    buttons: list[tuple[float, str, bool]],
    t0: float,
    clip_start: float,
    clip_end: float,
    eps: float,
) -> list[dict[str, Any]]:
    """1 クリップを stick/hold 系の材料へ変換する。"""
    # チャンネルごとに圧縮する。
    tracks: dict[str, list[_motion.Waypoint]] = {}
    for side in ("L", "R"):
        samples = [(at, x, y) for s, at, x, y in sticks if s == side]
        # クリップ範囲内のサンプルだけ使う。
        samples = [s for s in samples if clip_start - 0.05 <= s[0] <= clip_end]
        tracks[side] = _motion.compress_track(samples, clip_start, eps)
    base_ms = 0.0
    # 分割時刻: L/R の waypoint ∪ クリップ内のボタン/Hat イベント時刻。
    cuts = {base_ms}
    for side in ("L", "R"):
        for w in tracks[side]:
            cuts.add(float(w.t))
    for t_ms, _name, _on in buttons:
        at = t0 + t_ms / 1000.0
        if clip_start <= at <= clip_end:
            cuts.add((at - clip_start) * 1000.0)
    # クリップ終了も区切りに含める。
    cuts.add((clip_end - clip_start) * 1000.0)
    ordered = sorted(cuts)
    steps: list[dict[str, Any]] = []
    # クリップ内のボタン/Hat は hold/holdEnd（待ちなし）へ。
    for t_ms, name, on in buttons:
        at = t0 + t_ms / 1000.0
        if clip_start <= at <= clip_end:
            steps.append(
                {
                    "kind": "hold" if on else "hold_end",
                    "target": name,
                    "at_ms": round((at - clip_start) * 1000.0),
                }
            )
    # 区間ごとに stick 材料を作る。分割点の値は圧縮後の折れ線から補間で求める
    # （生データから取らない。極座標で線形な区間を途中で切っても ε 保証が保たれる）。
    for lo_ms, hi_ms in zip(ordered, ordered[1:]):
        span = hi_ms - lo_ms
        if span <= 0:
            continue
        seg: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {}
        for side in ("L", "R"):
            pts = tracks[side]
            if not pts:
                continue
            r0, th0 = _motion._restore_polar(pts, lo_ms)
            r1, th1 = _motion._restore_polar(pts, hi_ms)
            if r0 == r1 and th0 == th1:
                continue
            seg[side] = ((round(r0), round(th0)), (round(r1), round(th1)))
        if not seg:
            continue
        t_int = int(round(span))
        if len(seg) == 2:
            (l0, l1), (r0v, r1v) = seg["L"], seg["R"]
            steps.append(
                {
                    "kind": "stick2",
                    "l_from": [l0[0], l0[1]],
                    "l_to": [l1[0], l1[1]],
                    "r_from": [r0v[0], r0v[1]],
                    "r_to": [r1v[0], r1v[1]],
                    "t_ms": t_int,
                }
            )
        else:
            (side, ((fr, fth), (tr, tth))) = next(iter(seg.items()))
            steps.append(
                {
                    "kind": "stick",
                    "side": side,
                    "from": [fr, fth],
                    "to": [tr, tth],
                    "t_ms": t_int,
                }
            )
    # クリップ終端では動かした側ごとに解放する（設計 ID:006）。
    # end="hold" の引き継ぎは生成側（END 欄）で選ぶ。ここでは既定の
    # 解放材料を出し、最後の区間の END を RELEASE にするのは insertSteps 側。
    moved = [side for side in ("L", "R") if tracks[side]]
    if moved:
        if len(moved) == 2:
            steps.append({"kind": "release_stick", "side": "BOTH"})
        else:
            steps.append({"kind": "release_stick", "side": moved[0]})
    # 時刻順に並べ替える（hold と stick の混在を時刻順にする）。
    # stick 区間は開始時刻で並べる。
    timed: list[tuple[float, int, dict[str, Any]]] = []
    tail: list[dict[str, Any]] = []
    cursor = 0.0
    for step in steps:
        if step["kind"] in ("hold", "hold_end"):
            timed.append((float(step.pop("at_ms")), 0, step))
        elif step["kind"] == "release_stick":
            # 終端の解放は常に最後（時刻ソートの対象外）。
            tail.append(step)
        else:
            timed.append((cursor, 1, step))
            cursor += float(step["t_ms"])
    timed.sort(key=lambda item: (item[0], item[1]))
    return [step for _, _, step in timed] + tail
