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
from loguru import logger

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

    向きが決められない（角度が無い）ときは空文字を返す。呼び側は空を
    捨てる。選択欄は候補外の値を既定（A）のまま残すため、`Stick.LEFT` の
    ような素の名前を渡すと「A を押す」材料になってしまう。
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
        return ""
    prefix = "R_" if ev.name.endswith("RIGHT") else ""
    return f"Direction.{prefix}{label}"


def events_to_steps(
    events: list[InputLog.InputEvent],
    next_at: float | None = None,
) -> list[dict[str, Any]]:
    """イベント列を手順（押す・待つの並び）へ変換する。純関数。

    各 RELEASE ごとに `{"target": 値, "duration": 秒, "wait": 秒}` を作る。
    `duration` は押していた時間（RELEASE の `duration`）、`wait` は離して
    から次の PRESS までの時間（最後の手順は 0.1）。どちらも 0.05秒単位に
    丸め、最小 0.05、上限 60。PRESS が無い RELEASE（記録開始前から
    押しっぱなしだったもの）は捨てる。同時押し（重なり）は、重なった
    区間を無視して離した順に並べるだけで、同時押しの再現はしない。

    next_at に時刻（perf_counter）を渡すと、最後の手順の待ちを「その時刻
    まで」にする。範囲ごとに切って呼ぶとき、範囲の外に続く操作（次の
    クリップの開始など）までの間を既定の 0.1 秒へ潰さないため。
    """
    releases = [ev for ev in events if ev.action == "RELEASE"]
    presses = sorted(
        (ev for ev in events if ev.action == "PRESS"),
        key=lambda ev: ev.at,
    )
    steps: list[dict[str, Any]] = []
    last_at: float | None = None
    for rel in releases:
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
        last_at = rel.at
    # 最後の手順の待ちは次が無いため、丸め直さず既定にする。
    if steps:
        steps[-1]["wait"] = LAST_WAIT
    if steps and next_at is not None and last_at is not None:
        # 範囲の外に続く操作までは、その時刻ぶん待つ。
        steps[-1]["wait"] = _round_step(max(0.0, next_at - last_at))
    return steps


#: スティック操作をクリップとみなす r の閾値（u8 距離）。
#: motion.DEADZONE_R と同じ意味。ここでは座標で判定する。
MOTION_DEADZONE_XY = 8.0
#: 送信行の中立座標（u8）。
CENTER_XY = 128.0
#: クリップ終了の無操作継続（秒）。倒した側が中立へ戻ったあと、これだけ
#: 何も動かなければクリップを閉じる（まだ倒している側があればここで閉じる）。
CLIP_IDLE_S = 0.15
#: 同時とみなす解放の幅（秒）。同じ行で左右を離すと時刻が一致するため、
#: 1 送信周期ぶんを同じ扱いにして BOTH の 1 材料へまとめる。
RELEASE_MERGE_S = 0.01


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
                    auto = [
                        str(step["target"])
                        for step in motion_steps
                        if step.get("auto_release")
                    ]
                    if auto:
                        # 利用者の次の行動が変わる情報。GUI（LogPane 経由）と
                        # ファイルの両方へ 1 回だけ出す。
                        message = (
                            "記録停止時に押していた "
                            + "・".join(auto)
                            + " は、記録の終わりで離します"
                        )
                        print(message)
                        logger.info(message)
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


def _stick_side(ev: InputLog.InputEvent) -> str:
    """スティックのイベントがどちら側か。"""
    return "R" if ev.name.endswith("RIGHT") else "L"


def _stick_xy_at(
    events: list[InputLog.InputEvent],
) -> list[tuple[str, float, float, float]]:
    """スティック座標の時系列 [(side, at, x, y)] を抜く。

    PRESS/CHANGE の x・y を使う。RELEASE は中立（中央）のサンプルとして
    入れる。離した時刻は「中央へ戻った時刻」そのもので、入れないと
    トラックが最後の倒し値のまま伸び、保持や操作の間が倒しっぱなしに
    なる。at は perf_counter 値。
    """
    out: list[tuple[str, float, float, float]] = []
    for ev in events:
        if ev.kind != "stick":
            continue
        if ev.action in ("PRESS", "CHANGE"):
            if ev.x is None or ev.y is None:
                continue
            out.append((_stick_side(ev), float(ev.at), float(ev.x), float(ev.y)))
        elif ev.action == "RELEASE":
            x = CENTER_XY if ev.x is None else float(ev.x)
            y = CENTER_XY if ev.y is None else float(ev.y)
            out.append((_stick_side(ev), float(ev.at), x, y))
    return out


def _stick_release_times(
    events: list[InputLog.InputEvent],
) -> dict[str, list[float]]:
    """スティックの解放時刻 {side: [at]} を返す。

    「離した＝中立へ戻った」時刻でクリップを閉じるため、座標ではなく
    イベントから取る（中立へ寄っただけの CHANGE と区別する）。
    """
    out: dict[str, list[float]] = {"L": [], "R": []}
    for ev in events:
        if ev.kind == "stick" and ev.action == "RELEASE":
            out[_stick_side(ev)].append(float(ev.at))
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

    クリップの切り方: 開始はどちらかのスティックが r >= 8、終了は倒した側が
    すべて中立へ戻った時刻（まだ倒している側があれば最後に倒した時刻＋150ms）。
    押下区間がクリップにかかるボタンはクリップに含める（境界をまたぐ場合は
    クリップを広げる）。クリップ内は hold/holdEnd（待ちなし）＋ stick_move 系
    の材料、クリップ外は events_to_steps と同じ press 変換へ回す。

    クリップが表したスティック操作（倒した側の押下〜解放）は press 変換へ
    回さない。回すと同じ区間が二度再生され、記録に無い再倒しが挟まる。
    """
    if not events:
        return []
    sticks = _stick_xy_at(events)
    if not sticks:
        # スティック操作なし。従来の press 変換そのまま。
        return [{"kind": "press", **step} for step in events_to_steps(events)]
    t0 = min(ev.at for ev in events)
    releases = _stick_release_times(events)
    buttons = _motion_buttons(events, t0)
    # クリップ区間を求める（倒した範囲＋解放＋ボタンの跨ぎ）。
    clips = _split_clips(sticks, releases, buttons, t0)
    if not clips:
        return [{"kind": "press", **step} for step in events_to_steps(events)]
    steps: list[dict[str, Any]] = []
    # クリップが表したイベント。press 変換から外す（同じ区間の二重再生を防ぐ）。
    covered_all: dict[int, bool] = {}
    # クリップ外の press 変換用に、イベントをクリップ区間で区切る。
    for index, (clip_start, clip_end) in enumerate(clips):
        clip_steps, moved = _clip_to_steps(
            sticks, buttons, releases, t0, clip_start, clip_end, eps
        )
        covered_all.update(_covered_events(events, clip_start, clip_end, moved))
        # クリップ前の press 区間。
        prev_end = clips[index - 1][1] if index > 0 else None
        before = [
            ev
            for ev in events
            if not covered_all.get(id(ev))
            and (prev_end is None or ev.at >= prev_end)
            and ev.at < clip_start
        ]
        # 倒す前に始まってクリップ内で離した浅い操作（r が閾値未満）は、
        # 解放だけがクリップ内に落ちて対が組めず材料から消える。押下側ごと
        # ここへ寄せ、press 手順として残す。トラックに残った側の操作は
        # covered で外れているため二重にはならない。
        before += [
            ev
            for ev in events
            if ev.kind == "stick"
            and not covered_all.get(id(ev))
            and ev.action in ("PRESS", "RELEASE")
            and clip_start <= ev.at < clip_end
        ]
        before.sort(key=lambda e: e.at)
        # クリップへ繋がる待ちは、間を既定の 0.1 秒へ潰さず実時刻で残す。
        for step in events_to_steps(before, next_at=clip_start):
            steps.append({"kind": "press", **step})
        steps.extend(clip_steps)
    # 最後のクリップ後の press 区間。
    after = [
        ev for ev in events if not covered_all.get(id(ev)) and ev.at >= clips[-1][1]
    ]
    for step in events_to_steps(after):
        steps.append({"kind": "press", **step})
    return steps


def _button_spans(
    buttons: list[tuple[float, str, bool]], t0: float
) -> list[tuple[float, float | None]]:
    """ボタン/Hat の押下区間 [(押下 at, 解放 at|None)] を返す。

    解放が記録に無い（押しっぱなしのまま停止した）ものは None。
    """
    opened: dict[str, float] = {}
    spans: list[tuple[float, float | None]] = []
    for t_ms, name, on in buttons:
        at = t0 + t_ms / 1000.0
        if on:
            opened[name] = at
        else:
            press = opened.pop(name, None)
            if press is not None:
                spans.append((press, at))
    spans.extend((at, None) for at in opened.values())
    spans.sort()
    return spans


def _split_clips(
    sticks: list[tuple[str, float, float, float]],
    releases: dict[str, list[float]],
    buttons: list[tuple[float, str, bool]],
    t0: float,
) -> list[tuple[float, float]]:
    """クリップ区間 [(開始 at, 終了 at)] を返す。at は perf_counter 値。

    終了は「倒した側がすべて中立へ戻った時刻」。まだ離していない側があれば
    最後に倒した時刻＋CLIP_IDLE_S で閉じる。倒したままの時間（保持）と解放が
    クリップの外へ出ると、同じ区間が press 手順として二重に再生される。
    押下区間がクリップにかかるボタン/Hat は、境界をまたぐ場合にクリップを
    広げて丸ごと含める（片側だけ材料に残ると、押していない物を離す／
    押しっぱなしが残る形になる）。
    """
    # スティックが倒れている（r >= 閾値）時刻。
    active = [
        (side, at)
        for side, at, x, y in sticks
        if math.hypot(x - CENTER_XY, y - CENTER_XY) >= MOTION_DEADZONE_XY
    ]
    if not active:
        return []
    start = min(at for _side, at in active)
    end: float | None = None
    for side in {side for side, _at in active}:
        last = max(at for s, at in active if s == side)
        back = [at for at in releases.get(side, []) if at >= last]
        if not back:
            # まだ倒している側がある。押しっぱなしの末尾として閉じる。
            end = None
            break
        end = max(end, max(back)) if end is not None else max(back)
    if end is None:
        end = max(at for _side, at in active) + CLIP_IDLE_S
    # ボタン/Hat の押下区間を、またぐならクリップへ含める。
    spans = _button_spans(buttons, t0)
    for _ in range(len(spans) + 1):
        grew = False
        for press_at, release_at in spans:
            tail = end if release_at is None else release_at
            if press_at > end or tail < start:
                continue
            new_start, new_end = min(start, press_at), max(end, tail)
            if new_start != start or new_end != end:
                start, end = new_start, new_end
                grew = True
        if not grew:
            break
    return [(start, end)]


def _covered_events(
    events: list[InputLog.InputEvent],
    clip_start: float,
    clip_end: float,
    moved: set[str],
) -> dict[int, bool]:
    """クリップの材料が再現するイベント id を返す。

    対象は 2 つ。クリップ内の押下から始まるボタン/Hat（hold/holdEnd に
    なる）と、トラックが残った側（moved）のスティック押下〜解放（stick
    区間と解放材料になる）。press 変換へ回すと同じ区間が二度再生され、
    記録に無い押し直し・倒し直しが挟まる。
    """
    covered: dict[int, bool] = {}
    opened: dict[tuple[str, str], InputLog.InputEvent] = {}
    for ev in events:
        if ev.action not in ("PRESS", "RELEASE"):
            continue
        key = (ev.kind, ev.name)
        if ev.action == "PRESS":
            opened[key] = ev
            inside = clip_start <= ev.at <= clip_end
            side = _stick_side(ev) if ev.kind == "stick" else ""
            if inside and (ev.kind != "stick" or side in moved):
                covered[id(ev)] = True
        else:
            press = opened.pop(key, None)
            if press is not None and covered.get(id(press)):
                covered[id(ev)] = True
    return covered


def _clip_release_times(
    releases: dict[str, list[float]],
    moved: set[str],
    clip_start: float,
    clip_end: float,
) -> list[tuple[float, str]]:
    """クリップ内の解放材料 [(at, side)] を時刻順で返す。

    同じ行で左右を離した（時刻が 1 送信周期以内の）ものは BOTH の 1 件へ
    まとめる。記録に解放が無い側（倒したまま停止）はクリップ終端で離す。
    """
    out: list[tuple[float, str]] = []
    for side in ("L", "R"):
        if side not in moved:
            continue
        times = [t for t in releases.get(side, []) if clip_start <= t <= clip_end]
        if times:
            out.extend((t, side) for t in times)
        else:
            out.append((clip_end, side))
    out.sort()
    merged: list[tuple[float, str]] = []
    for at, side in out:
        last = merged[-1] if merged else None
        if (
            last is not None
            and last[1] not in (side, "BOTH")
            and at - last[0] <= RELEASE_MERGE_S
        ):
            merged[-1] = (at, "BOTH")
        else:
            merged.append((at, side))
    return merged


#: 記録停止で解放が無い hold に確保する最短の押下時間（ms）。
#: 8ms スロット 3 つ分。実機で「何 ms あれば押下が認識されるか」を測って確定する。
MIN_HOLD_MS = 3 * 8


def _close_open_holds(steps: list[dict[str, Any]], clip_len_ms: int) -> list[str]:
    """記録停止で解放が無い hold に、クリップ終端の hold_end を補う。

    target ごとに開閉を順に追う（「同じ target の hold_end があるか」だけで
    見ると、hold → hold_end → hold の 2 回目を取りこぼす）。

    補った hold_end は押下から MIN_HOLD_MS を下回らない時刻へ置く。押下が
    終端の直前にあっても、8ms スロットで 1 回も線に出ない事態を避けるため。
    戻り値は補った target（利用者へ知らせるため）。
    """
    open_at: dict[str, int] = {}
    for step in steps:
        if step["kind"] == "hold":
            open_at[str(step["target"])] = int(step["at_ms"])
        elif step["kind"] == "hold_end":
            open_at.pop(str(step["target"]), None)
    for target, at in sorted(open_at.items(), key=lambda kv: kv[1]):
        steps.append(
            {
                "kind": "hold_end",
                "target": target,
                "at_ms": max(int(clip_len_ms), at + MIN_HOLD_MS),
                # 記録に無い解放。呼び出し側が利用者への通知に使う。
                "auto_release": True,
            }
        )
    return list(open_at)


def _clip_to_steps(
    sticks: list[tuple[str, float, float, float]],
    buttons: list[tuple[float, str, bool]],
    releases: dict[str, list[float]],
    t0: float,
    clip_start: float,
    clip_end: float,
    eps: float,
) -> tuple[list[dict[str, Any]], set[str]]:
    """1 クリップを stick/hold/解放の材料へ変換する。

    返すのは (材料, トラックが残った側)。残らなかった側の操作は呼び側が
    press 変換で拾う（材料から消さない）。
    """
    # チャンネルごとに圧縮する。
    tracks: dict[str, list[_motion.Waypoint]] = {}
    for side in ("L", "R"):
        samples = [(at, x, y) for s, at, x, y in sticks if s == side]
        # クリップ範囲内のサンプルだけ使う。
        samples = [s for s in samples if clip_start - 0.05 <= s[0] <= clip_end]
        # 再生開始時のスティックは中立。最初のサンプルがいきなり倒れ値だと
        # 最初の区間が「無変化」になり、stick_move が何も送らない（倒す動きが
        # 再生から丸ごと消える）。1 格子前に中立を置き、倒し始めを区間にする。
        if (
            samples
            and math.hypot(samples[0][1] - CENTER_XY, samples[0][2] - CENTER_XY)
            >= MOTION_DEADZONE_XY
        ):
            samples.insert(0, (samples[0][0] - _motion.SLOT_S, CENTER_XY, CENTER_XY))
        tracks[side] = _motion.compress_track(samples, clip_start, eps)
        if not tracks[side]:
            # 倒したまま変化も解放も無い 1 点だけの側は、圧縮が空になる。
            # 「その値で保持している」として残す（消すとスティック操作ごと
            # 材料から落ちる）。
            held = [
                s
                for s in samples
                if math.hypot(s[1] - CENTER_XY, s[2] - CENTER_XY) >= MOTION_DEADZONE_XY
            ]
            if held:
                at, x, y = held[-1]
                r, th = _motion.xy_to_polar(x, y)
                tracks[side] = [
                    _motion.Waypoint(max(0.0, (at - clip_start) * 1000.0), r, th)
                ]
    moved = {side for side in ("L", "R") if tracks[side]}
    clip_releases = _clip_release_times(releases, moved, clip_start, clip_end)
    clip_len = (clip_end - clip_start) * 1000.0
    # 8ms 格子へ寄せた点がクリップ終端を跨ぐと、解放より後ろに区間が残り、
    # 解放が最後に来なくなる（素材は並び順がすべて）。終端へ寄せる。
    for side in ("L", "R"):
        tracks[side] = [
            _motion.Waypoint(min(w.t, clip_len), w.r, w.th) for w in tracks[side]
        ]
    base_ms = 0.0
    # 分割時刻: L/R の waypoint ∪ クリップ内のボタン/Hat ∪ 解放時刻。
    cuts = {base_ms}
    for side in ("L", "R"):
        for w in tracks[side]:
            cuts.add(float(w.t))
    for t_ms, _name, _on in buttons:
        at = t0 + t_ms / 1000.0
        if clip_start <= at <= clip_end:
            cuts.add((at - clip_start) * 1000.0)
    # 解放も区切りにする。区間が解放をまたぐと、離した後に倒し直す形になる。
    for at, _side in clip_releases:
        cuts.add((at - clip_start) * 1000.0)
    # クリップ終了も区切りに含める。
    cuts.add(clip_len)
    ordered = sorted(cuts)
    steps: list[dict[str, Any]] = []
    # クリップ内のボタン/Hat は hold/holdEnd（待ちなし）へ。hold の待ちを
    # 入れると押下が延び、以後の区間がその分ずれる。
    for t_ms, name, on in buttons:
        at = t0 + t_ms / 1000.0
        if not (clip_start <= at <= clip_end):
            continue
        entry: dict[str, Any] = {
            "kind": "hold" if on else "hold_end",
            "target": name,
            "at_ms": round((at - clip_start) * 1000.0),
        }
        if on:
            entry["wait"] = 0
        steps.append(entry)
    # 解放材料。離した時刻ごとに出す（操作が複数あれば複数回離している）。
    for at, side in clip_releases:
        steps.append(
            {
                "kind": "release_stick",
                "side": side,
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
            seg[side] = ((round(r0), round(th0)), (round(r1), round(th1)))
        if not seg:
            continue
        t_int = int(round(span))
        # 無変化の区間は stick_move が待つだけで何も送らない。以前は捨てて
        # いたため、捨てた区間の時間が再生総時間から消え、ゆっくり操作が
        # 超高速で再生されていた。静止区間として残す。
        if all(f == t for f, t in seg.values()):
            if len(seg) == 2:
                # 両側が動いているクリップでは両側の材料にする。片側だけに
                # すると、終端の解放がその側しか離せない（もう一方が倒れたまま）。
                l0, _l1 = seg["L"]
                r0v, _r1v = seg["R"]
                steps.append(
                    {
                        "kind": "stick2",
                        "l_from": [l0[0], l0[1]],
                        "l_to": [l0[0], l0[1]],
                        "r_from": [r0v[0], r0v[1]],
                        "r_to": [r0v[0], r0v[1]],
                        "t_ms": t_int,
                        "static": True,
                    }
                )
            else:
                (side, ((fr, fth), (_tr, _tth))) = next(iter(seg.items()))
                steps.append(
                    {
                        "kind": "stick",
                        "side": side,
                        "from": [fr, fth],
                        "to": [fr, fth],
                        "t_ms": t_int,
                        "static": True,
                    }
                )
            continue
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
    # 記録停止で解放が無い hold は、このままだと押しっぱなしで再生される。
    # at_ms を剥がして時刻順へ並べ替える前に、終端の hold_end を補う。
    # 通知はここではしない（この関数は何度も呼ばれる）。auto_release の印を
    # 見て、呼び出し側が 1 回だけ知らせる。
    _close_open_holds(steps, int(round(clip_len)))
    # 時刻順に並べ替える（hold・解放と stick の混在を時刻順にする）。
    # stick 区間の開始時刻は ordered（記録時刻順）の区間開始を使う。
    # 以前は出した区間の span だけを積算していたため、捨てた無変化区間の
    # 時間が再生総時間から消え、ゆっくり操作が超高速で再生されていた。
    # 無変化区間の時間も ordered 上に残る（後続区間が前倒しにならない）。
    # 同じ時刻では ボタン/Hat → 解放 → 区間開始 の順に置く。
    rank = {"hold": 0, "hold_end": 0, "release_stick": 1}
    timed: list[tuple[float, int, dict[str, Any]]] = []
    bounds = [lo for lo, _hi in zip(ordered, ordered[1:]) if _hi > lo]
    pos = 0
    for step in steps:
        if step["kind"] in rank:
            timed.append((float(step.pop("at_ms")), rank[step["kind"]], step))
        else:
            start = bounds[pos] if pos < len(bounds) else 0.0
            pos += 1
            timed.append((start, 2, step))
    timed.sort(key=lambda item: (item[0], item[1]))
    return [step for _, _, step in timed], moved
