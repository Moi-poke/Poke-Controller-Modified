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

import threading
import time
from typing import Any

from core import InputLog

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

    def __init__(self) -> None:
        self._logger = _RecordLogger()
        self._transport: Any = None
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
            return True
        except Exception:
            return False

    def stop(self) -> list[dict[str, Any]]:
        """記録を外し、手順を返す。記録中でなければ空。例外は外へ投げない。"""
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
            return events_to_steps(self._logger.snapshot())
        except Exception:
            return []
