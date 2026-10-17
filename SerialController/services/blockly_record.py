#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""blockly_record.py - 本体の操作を記録し、押すブロックの手順へ変換する。

送信線へ出た入力（仮想コントローラ・キーボード・ゲームパッドのどれでも、
Sender から Transport へ出た行が材料）を InputLogger の差分判定でイベントへ
戻し、編集画面の `pokecon_press` ブロックの並び（押す・待つ）にして挿入する
ための材料を作る。「操作に合わせた形で入力したい」という要望への答え。

同時押し（重なった区間）は hold/holdEnd の連鎖（chord連鎖）にして残す。
重ならない単独押しは従来どおり press のまま。スティックと同時のボタンは
motion クリップ側で hold 化する（build_motion_steps）。

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

#: chord連鎖の hold 間 wait の刻み。press 系の 0.05 とは分け、0.01 で丸める。
#: 同時開始のずれを出さないよう 0 も許す（press 系の最小 0.05 へは寄せない）。
CHORD_ROUND = 0.01
#: hold の WAIT 欄の上限。超えた分は pokecon_wait 相当（kind:wait）へ分割する。
#: ブロック欄の上限（pokecon_hold の WAIT は最大60）に合わせる。
CHORD_WAIT_MAX = 60.0
#: 延長後のクリップ長の警告閾値（秒）。長押しの吸収で記録全体が1クリップに
#: 膨らんだとき、編集しにくくなるため warnings へ1件だけ知らせる。仮値。
CLIP_WARN_S = 10.0


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


# ---------------------------------------------------------------------------
# 同時押しの記録（chord連鎖）
# ---------------------------------------------------------------------------
# events_to_steps は重なりを無視して離した順に並べるだけだった。物理パッドの
# 直送では同時押しが増えたため、真に重なる区間の連結成分だけ hold/holdEnd の
# 連鎖にして残す。重ならない単独押しは press のまま（可読性・既存保存物のため）。
#
# 再生時の送信行は hold の積み重ね（A→A+R→R→中立）になる。完全同時の PRESS 対も
# 連鎖化するため、元の1行に対する過渡1行が入る。マクロ用途の合意事項とする。
# Hat の切替ロールは hold(新)→holdEnd(旧) の順に入れ替え、中立過渡を出さない
# （holdButton残留ガードと inputEnd の hold_hat 復元に依存する。参照は関数名で
# 行い、行番号では行わないこと）。


#: 押下区間（名前, 種類, 開始at, 終了at|None, PRESS, RELEASE|None）。
_PressSpan = tuple[
    str, str, float, float | None, InputLog.InputEvent, InputLog.InputEvent | None
]


def _press_intervals(events: list[InputLog.InputEvent]) -> list[_PressSpan]:
    """ボタン/Hat の押下区間を返す。

    PRESS→RELEASE を名前ごとに先入れ先出しで組にする。開始前の RELEASE
    （PRESS が無いもの）は捨てる。停止時に押したまま（RELEASE が無いもの）は
    終了 None で残す。stick・CHANGE は扱わない（stick 混じりは motion 側の仕事）。
    """
    pending: dict[str, list[tuple[float, InputLog.InputEvent]]] = {}
    spans: list[_PressSpan] = []
    for ev in events:
        if ev.kind not in ("button", "hat"):
            continue
        if ev.action == "PRESS":
            pending.setdefault(ev.name, []).append((float(ev.at), ev))
        elif ev.action == "RELEASE":
            queue = pending.get(ev.name)
            if not queue:
                # 開始前から押していたもの。捨てる（件数は返さない）。
                continue
            start, press_ev = queue.pop(0)
            spans.append((ev.name, press_ev.kind, start, float(ev.at), press_ev, ev))
    for queue in pending.values():
        for start, press_ev in queue:
            spans.append((press_ev.name, press_ev.kind, start, None, press_ev, None))
    return spans


def _chord_grid(moment: float, origin: float) -> float:
    """連鎖先頭からの経過を 0.01 秒単位の格子へ寄せる（累積丸めの土台）。"""
    try:
        stepped = round((float(moment) - float(origin)) / CHORD_ROUND) * CHORD_ROUND
    except (TypeError, ValueError):
        return 0.0
    return round(stepped, 2)


def _split_chord_wait(total: float) -> list[float]:
    """待ちを CHORD_WAIT_MAX 以下の塊へ割る。0 以下は空にする。"""
    total = round(float(total), 2)
    if not total > 0.0:
        return []
    chunks: list[float] = []
    while total > CHORD_WAIT_MAX:
        chunks.append(CHORD_WAIT_MAX)
        total = round(total - CHORD_WAIT_MAX, 2)
    if total > 0.0:
        chunks.append(total)
    return chunks


def events_to_chord_steps(
    events: list[InputLog.InputEvent],
    stop_at: float | None = None,
) -> list[dict[str, Any]]:
    """イベント列を同時押し再現の手順（hold連鎖）へ変換する。純関数。

    PRESS→RELEASE を区間化し、真に重なる連結成分
    （max(開始) < min(終了)。端点タッチは重なりとみなさない）だけ
    hold/holdEnd の連鎖にし、孤立区間は events_to_steps 互換の press のまま
    （kind だけ足す）。stick・CHANGE は扱わない。

    Hat は単一値チャンネルのため、新優先・旧即RELEASE で区間を詰める。
    同atの Hat RELEASE＋PRESS 対（切替ロール。単独でも）は
    hold(新)→holdEnd(旧) の順に入れ替える（新先旧後。中立過渡を出さない）。

    hold の WAIT は次のエッジまでの差分を、連鎖先頭基準の累積丸め
    （0.01 刻み・0 許容。上限超は kind:wait へ分割）で付ける。holdEnd の後の
    間隔と連鎖末尾の間隔は kind:wait（pokecon_wait 相当）で出す。
    開始前の RELEASE は捨てる。停止時に押下中は、孤立なら press
    （duration＝停止−押下）、連鎖内なら holdEnd で閉じる。
    同一 target の未解放 hold が重なったら ValueError（Keys.hold は重複を
    黙って捨てるため、変換の出口で検出する）。
    """
    if not events:
        return []
    if stop_at is None:
        stop_at = max(float(ev.at) for ev in events)
    event_seq = {id(ev): index for index, ev in enumerate(events)}
    button_order = {name: index for index, name in enumerate(InputLog.BUTTON_NAMES)}
    # 区間化し、終了の無いものは停止時刻で閉じる。
    spans: list[dict[str, Any]] = []
    for name, kind, start, end, press_ev, release_ev in _press_intervals(events):
        spans.append(
            {
                "name": name,
                "kind": kind,
                "start": start,
                "end": stop_at if end is None else end,
                "press_seq": event_seq.get(id(press_ev), 0),
                "rel_seq": event_seq.get(id(release_ev), 0)
                if release_ev is not None
                else -1,
            }
        )
    # Hat は新優先・旧即RELEASE で詰める（単一値チャンネルのため）。
    hat_starts = sorted(
        (sp for sp in spans if sp["kind"] == "hat"),
        key=lambda sp: (sp["start"], sp["press_seq"]),
    )
    for newer in hat_starts:
        for older in spans:
            if older["kind"] != "hat" or older["name"] == newer["name"]:
                continue
            if older["start"] < newer["start"] < older["end"]:
                older["end"] = newer["start"]
                older["rel_seq"] = newer["press_seq"]
    # 同名 Hat の端点連結は1区間へ畳む（離すと二重holdになる）。
    spans.sort(key=lambda sp: (sp["kind"] != "hat", sp["name"], sp["start"]))
    chained: list[dict[str, Any]] = []
    for span in spans:
        prev = chained[-1] if chained else None
        if (
            prev is not None
            and span["kind"] == "hat"
            and prev["kind"] == "hat"
            and prev["name"] == span["name"]
            and prev["end"] == span["start"]
        ):
            prev["end"] = max(prev["end"], span["end"])
            prev["rel_seq"] = span["rel_seq"]
            continue
        chained.append(span)
    spans = chained
    # 真に重なる区間の連結成分を作る。Hat 同士の端点連結（切替ロール）も
    # 同じ成分にする。ボタンの端点タッチは重なりとみなさない。
    count = len(spans)
    parent = list(range(count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for i in range(count):
        for j in range(i + 1, count):
            first = spans[i]
            second = spans[j]
            if (
                first["kind"] == "hat"
                and second["kind"] == "hat"
                and first["name"] != second["name"]
                and (first["end"] == second["start"] or second["end"] == first["start"])
            ):
                union(i, j)
            elif max(first["start"], second["start"]) < min(
                first["end"], second["end"]
            ):
                union(i, j)
    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    ordered = sorted(
        groups.values(),
        key=lambda idxs: (
            min(spans[i]["start"] for i in idxs),
            min(spans[i]["end"] for i in idxs),
        ),
    )
    comp_starts = [min(spans[i]["start"] for i in idxs) for idxs in ordered]
    press_times = sorted(sp["start"] for sp in spans)
    steps: list[dict[str, Any]] = []
    for group_no, idxs in enumerate(ordered):
        origin = comp_starts[group_no]
        next_start = comp_starts[group_no + 1] if group_no + 1 < len(ordered) else None
        if len(idxs) == 1:
            # 孤立区間は events_to_steps 互換の press のまま。
            span = spans[idxs[0]]
            duration = _round_step(span["end"] - span["start"])
            wait = LAST_WAIT
            for moment in press_times:
                if moment > span["end"]:
                    wait = _round_step(moment - span["end"])
                    break
            steps.append(
                {
                    "kind": "press",
                    "target": span["name"],
                    "duration": duration,
                    "wait": wait,
                }
            )
            continue
        # 連鎖内では Hat 入替対（同at RELEASE＋PRESS）を新先旧後にする。
        swap_pressers = {
            presser
            for presser in idxs
            for releaser in idxs
            if presser != releaser
            and spans[releaser]["kind"] == "hat"
            and spans[presser]["kind"] == "hat"
            and spans[releaser]["name"] != spans[presser]["name"]
            and spans[releaser]["end"] == spans[presser]["start"]
        }
        edges: list[tuple[float, int, int, int, int, bool, str]] = []
        for pos in idxs:
            span = spans[pos]
            if span["kind"] == "hat":
                # 同at Hat RELEASE＋PRESS 対は hold(新)→holdEnd(旧) に入替
                # （新先旧後。中立過渡を出さない）。それ以外は入力順保存
                # （Hat は RELEASE→PRESS）。種別 0=PRESS（新先）、
                # 1=RELEASE、2=PRESS（旧・通常）。
                if pos in swap_pressers:
                    press_kind = 0
                else:
                    press_kind = 2
                edges.append(
                    (
                        span["start"],
                        press_kind,
                        span["press_seq"],
                        0,
                        0,
                        True,
                        span["name"],
                    )
                )
                edges.append(
                    (span["end"], 1, span["rel_seq"], 0, 0, False, span["name"])
                )
            else:
                # 同時刻エッジ順序は入力順序を保存する。ボタンは1行あたり
                # BUTTON_NAMES 表順で届くため、表順が入力順になる。
                rank = button_order.get(span["name"], len(button_order))
                edges.append((span["start"], 2, 0, 0, rank, True, span["name"]))
                edges.append((span["end"], 1, 0, 0, rank, False, span["name"]))
        edges.sort(key=lambda edge: (edge[0], edge[1], edge[2], edge[3], edge[4]))
        grid = {edge[0]: _chord_grid(edge[0], origin) for edge in edges}
        grid_next = _chord_grid(next_start, origin) if next_start is not None else 0.0
        times = sorted(grid)
        by_time: dict[float, list[tuple[float, int, int, int, int, bool, str]]] = {}
        for edge in edges:
            by_time.setdefault(edge[0], []).append(edge)
        held: set[str] = set()
        for pos, moment in enumerate(times):
            group = by_time[moment]
            following = times[pos + 1] if pos + 1 < len(times) else None
            gap = 0.0
            if following is not None:
                gap = max(0.0, round(grid[following] - grid[moment], 2))
            group_has_release = any(not edge[5] for edge in group)
            # 同時 PRESS 群の間隔は末尾の hold が担う（先頭担いだと
            # 「A→A+R」の過渡が次の待ちで延び、同時開始がずれる）。
            # 同一時刻に離すものが混ざるときは、間隔を hold の WAIT へ載せず
            # 後ろの kind:wait へ一本化する（二重に待たないため）。
            hold_chunks = _split_chord_wait(gap) if not group_has_release else []
            press_edges = [edge for edge in group if edge[5]]
            last_press = press_edges[-1] if press_edges else None
            for edge in group:
                is_press = edge[5]
                target = edge[6]
                if is_press:
                    if target in held:
                        raise ValueError(
                            f"同時押しの変換で {target} の二重holdが出ました"
                            "（Keys.hold は重複を黙って捨てるため、"
                            "変換の出口で検出しています）"
                        )
                    held.add(target)
                    chunks = hold_chunks if edge == last_press else []
                    steps.append(
                        {
                            "kind": "hold",
                            "target": target,
                            "wait": chunks[0] if chunks else 0.0,
                        }
                    )
                    for chunk in chunks[1:]:
                        steps.append({"kind": "wait", "wait": chunk})
                else:
                    held.discard(target)
                    steps.append({"kind": "hold_end", "target": target})
            if group_has_release and following is not None:
                for chunk in _split_chord_wait(gap):
                    steps.append({"kind": "wait", "wait": chunk})
        # 連鎖末尾の間隔（次の成分まで。無ければ既定の待ち）。
        if next_start is not None:
            tail = max(0.0, round(grid_next - grid[times[-1]], 2))
            for chunk in _split_chord_wait(tail):
                steps.append({"kind": "wait", "wait": chunk})
        else:
            steps.append({"kind": "wait", "wait": LAST_WAIT})
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
        #: 記録中に本体側の操作が混ざったかの latch。一度立ったら次の
        #: 開始まで倒さない（停止後に読んで警告へ載せるため）。
        self._contaminated = False
        #: 調停の棄却件数の土台（human, auto）。初回の見回りで覚え、
        #: 増えたら混ざったとみなす。開始ごとに捨てる。
        self._arb_base: tuple[int, int] | None = None

    @property
    def recording(self) -> bool:
        """いま記録中か。"""
        return self._transport is not None

    @property
    def contaminated(self) -> bool:
        """記録中に本体側の操作が混ざったか（latch・読み取り専用）。"""
        return bool(self._contaminated)

    def observe_owners(
        self,
        owners: dict[str, Any] | None,
        arbitration: dict[str, Any] | None = None,
    ) -> bool:
        """所有者の写しと調停の様子を見て、汚染を latch する。

        GUI スレッドの見回りから呼ぶ（sender.getOwners()・getArbitration()
        の写しを渡す。持たない線では None でよい）。gamepad 以外の所有者
        が空でなければ汚染とする。調停の棄却件数が増えていても汚染とする。
        一度汚染したら次の開始まで倒さない。例外は外へ投げない。
        """
        try:
            if self._contaminated:
                return True
            if self._transport is None:
                return False
            if owners is not None:
                try:
                    btn = owners.get("btn", {})
                    hat = owners.get("hat", {})
                    stick = owners.get("stick", {})
                except Exception:
                    btn, hat, stick = {}, {}, {}
                if isinstance(btn, dict):
                    for owner, bits in btn.items():
                        if str(owner) == "gamepad":
                            continue
                        try:
                            if int(bits) != 0:
                                self._contaminated = True
                                return True
                        except Exception:
                            # 形が読めないものは「何か居る」とみなす。
                            self._contaminated = True
                            return True
                if isinstance(hat, dict):
                    for owner, claim in hat.items():
                        if str(owner) == "gamepad":
                            continue
                        # 中立（Sender.POSTURE_HAT_CENTER と同じ 8）の
                        # 置き土産は空とみなす。
                        value = claim
                        if isinstance(claim, (tuple, list)) and len(claim) > 0:
                            value = claim[0]
                        try:
                            if int(value) == 8:
                                continue
                        except Exception:
                            pass
                        self._contaminated = True
                        return True
                if isinstance(stick, dict):
                    for owner, claim in stick.items():
                        if str(owner) == "gamepad":
                            continue
                        points = claim
                        if isinstance(claim, (tuple, list)) and len(claim) == 2:
                            points = claim[0]
                        if not isinstance(points, dict):
                            # 形が違うものは「何か居る」とみなす。
                            self._contaminated = True
                            return True
                        live = False
                        for point in points.values():
                            try:
                                x, y = point
                            except Exception:
                                live = True
                                break
                            try:
                                # 中立（Sender.POSTURE_CENTER と同じ 128）
                                # だけなら空。
                                if int(x) != 128 or int(y) != 128:
                                    live = True
                                    break
                            except Exception:
                                live = True
                                break
                        if live:
                            self._contaminated = True
                            return True
            if isinstance(arbitration, dict):
                cur: tuple[int, int] | None
                try:
                    cur = (
                        int(arbitration.get("rejected_human", 0)),
                        int(arbitration.get("rejected_auto", 0)),
                    )
                except Exception:
                    cur = None
                if cur is not None:
                    if self._arb_base is None:
                        self._arb_base = cur
                    elif cur[0] > self._arb_base[0] or cur[1] > self._arb_base[1]:
                        self._contaminated = True
                        return True
            return bool(self._contaminated)
        except Exception:
            return bool(self._contaminated)

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
            # 新しい記録では汚染を忘れる（停止後に読む分は stop が保つ）。
            self._contaminated = False
            self._arb_base = None
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

    def stop(
        self, warnings: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """記録を外し、手順を返す。記録中でなければ空。例外は外へ投げない。

        stick 操作が無くボタン/Hat の重なりがあれば chord 連鎖（kind が
        press/hold/hold_end/wait のもの）を返す。stick 操作があれば motion
        区間の材料（kind が stick/stick2/hold/hold_end/release_stick のもの）
        を混ぜて返す。どちらでもなければ従来の press 変換だけ。
        warnings を渡すと motion 変換の警告（clip_long）を足す。汚染の
        latch は保ち、contaminated で読める。
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
            # 同時押しは motion より先に試す。stick 無し＋重なり有り（hold系が
            # 出る）のときだけ chord 手順を返す。stick 有りは従来どおり motion へ。
            try:
                chord_events = self._logger.snapshot()
                if not any(ev.kind == "stick" for ev in chord_events):
                    chord_steps = events_to_chord_steps(chord_events)
                    if any(
                        step.get("kind") in ("hold", "hold_end") for step in chord_steps
                    ):
                        return chord_steps
            except Exception:
                pass
            if motion_transport is not None:
                try:
                    motion_steps = build_motion_steps(
                        self._motion_logger.snapshot(), warnings=warnings
                    )
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
    （離す直前の値は CHANGE/PRESS 側にある）。RELEASE の at は抜かず、
    クリップ分割（_split_clips）の入力として別に集める。at は perf_counter 値。
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


def _stick_releases(
    events: list[InputLog.InputEvent],
) -> list[tuple[str, float]]:
    """スティックの解放 [(side, at)] を時刻順で返す。

    RELEASE の座標は中立のため使わず、at だけを使う。RELEASE が無い
    （停止時に押下中・運転席に残った）側は出さない。クリップ分割で
    「離した瞬間」を区切りに使う（離したのに中立へ戻らない残留の修正）。
    """
    out: list[tuple[str, float]] = []
    for ev in events:
        if ev.kind != "stick" or ev.action != "RELEASE":
            continue
        side = "R" if ev.name.endswith("RIGHT") else "L"
        out.append((side, float(ev.at)))
    out.sort(key=lambda item: item[1])
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
    warnings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """イベント列を motion ブロックの材料へ変換する。純関数。

    返すのは [{kind, ...}] の並び。kind は "stick"（単独）/"stick2"（L+R 同時）/
    "hold" / "hold_end" / "release_stick" / "press"（クリップ外の従来手順）。
    時刻はすべて ms（整数）。T（移行時間）には 0.05 秒丸めを適用しない。

    クリップの切り方: 開始はどちらかのスティックが r >= 8、終了は両スティック
    中立かつボタン保持なしが 150ms 継続。押下区間がクリップにかかるボタンは
    クリップに含める（境界をまたぐ場合はクリップを広げる）。
    クリップ外は events_to_steps と同じ press 変換へ回す。
    クリップと交差する押下区間は、開始・終了とも延長して吸収する（延長で
    新たに交差するものも取り込む。不動点まで繰り返す）。開始側の延長は
    押下開始まで、終了側は解放まで。吸収した区間のイベントはクリップ外
    （press 側）へ出さない。延長後のクリップ長が CLIP_WARN_S を超えたら、
    warnings（渡したときだけ）へ {"code": "clip_long", "seconds": 長さ} を
    1件足す（分割はしない）。hold/hold_end には at_ms（クリップ先頭からの
    ms。挿入側の WAIT 引継ぎ用）を付ける。
    """
    if not events:
        return []
    sticks = _stick_xy_at(events)
    if not sticks:
        # スティック操作なし。従来の press 変換そのまま。
        return [{"kind": "press", **step} for step in events_to_steps(events)]
    t0 = min(ev.at for ev in events)
    buttons = _motion_buttons(events, t0)
    # クリップ区間を求める（スティックが動いている範囲＋跨ぎ区間の吸収）。
    intervals = _press_intervals(events)
    releases = _stick_releases(events)
    clips, absorbed = _split_clips(sticks, intervals, releases)
    if warnings is not None:
        for clip_start, clip_end in clips:
            if clip_end - clip_start > CLIP_WARN_S:
                warnings.append(
                    {"code": "clip_long", "seconds": round(clip_end - clip_start, 2)}
                )
                break
    if not clips:
        return [{"kind": "press", **step} for step in events_to_steps(events)]
    steps: list[dict[str, Any]] = []
    # クリップ外の press 変換用に、イベントをクリップ区間で区切る。吸収済みは除く。
    # stick の RELEASE はクリップ側が引き受ける（press 側へ出すと中立へ戻す
    # press が余計に1個増え、方向が化けることがある）。
    for index, (clip_start, clip_end) in enumerate(clips):
        # クリップ前の press 区間。
        prev_end = clips[index - 1][1] if index > 0 else None
        before = [
            ev
            for ev in events
            if (prev_end is None or ev.at >= prev_end)
            and ev.at < clip_start
            and id(ev) not in absorbed
            and not (ev.kind == "stick" and ev.action == "RELEASE")
        ]
        for step in events_to_steps(before):
            steps.append({"kind": "press", **step})
        steps.extend(_clip_to_steps(sticks, buttons, t0, clip_start, clip_end, eps))
    # 最後のクリップ後の press 区間。
    after = [
        ev
        for ev in events
        if ev.at >= clips[-1][1]
        and id(ev) not in absorbed
        and not (ev.kind == "stick" and ev.action == "RELEASE")
    ]
    for step in events_to_steps(after):
        steps.append({"kind": "press", **step})
    return steps


def _split_clips(
    sticks: list[tuple[str, float, float, float]],
    intervals: list[_PressSpan],
    releases: list[tuple[str, float]] | None = None,
) -> tuple[list[tuple[float, float]], set[int]]:
    """クリップ区間 [(開始 at, 終了 at)] と吸収した押下イベントの id 集合を返す。

    at は perf_counter 値。クリップと交差する押下区間（端点タッチを含む）は
    開始・終了とも延長して吸収し、延長で新たに交差するものも取り込む
    （不動点まで）。開始側の延長は押下開始まで。RELEASE の無い押下中は
    終了を延ばさない。吸収した区間の PRESS/RELEASE はクリップ外へ出さない
    （二重化防止）。

    スティックの RELEASE 時刻では区間を分割する。離した瞬間の座標は
    中立のため座標列に出ず、分けなければ「離して置いた静止」が
    「倒し続け」に潰れ、再生で中立へ戻らなくなる（残留）。RELEASE が
    無い側の延長吸収は従来どおり行う。
    """
    # スティックが動いている時刻の範囲を求める。
    active = [
        at
        for _, at, x, y in sticks
        if math.hypot(x - 128.0, y - 128.0) >= MOTION_DEADZONE_XY
    ]
    if not active:
        return [], set()
    all_start = min(active)
    all_end = max(active) + CLIP_IDLE_S
    # 分割点: RELEASE 時刻（側不問）。ただし直後に押し直しがある
    # RELEASE では切らない（倒し直しを別クリップに割ると、単発の倒しが
    # 座標を持たない press へ落ち、方向が化ける）。切るのは「離した後に
    # active が無い」RELEASE＝その側の操作が本当に終わったものだけにする。
    press_ats = sorted(at for _, at, _, _ in sticks)
    cuts = sorted(
        {
            at
            for _, at in (releases or [])
            if all_start < at < all_end and not any(p > at for p in press_ats)
        }
    )
    bounds = [all_start, *cuts, all_end]
    clips: list[tuple[float, float]] = []
    absorbed: set[int] = set()
    for seg_start, seg_end in zip(bounds, bounds[1:]):
        if seg_end <= seg_start:
            continue
        seg_active = [at for at in active if seg_start <= at <= seg_end]
        if not seg_active:
            # 離した後の静止だけの区間はクリップにしない。次の active
            # （押し直し）があればそちらで拾う。
            continue
        start, end = seg_start, seg_end
        growing = True
        while growing:
            growing = False
            for _name, _kind, press_at, release_at, press_ev, release_ev in intervals:
                release_eff = float("inf") if release_at is None else release_at
                if press_at <= end and release_eff >= start:
                    if press_at < start:
                        if start > all_start:
                            # 前の区切りにかかる押下は吸収だけし、開始は割らない
                            # （跨ぎ区間が2クリップへ分裂して二重holdになるのを防ぐ）。
                            absorbed.add(id(press_ev))
                            if release_ev is not None:
                                absorbed.add(id(release_ev))
                            continue
                        # 最初の区切りは跨ぎ吸収の起点のため開始側へ延長する
                        # （ZL長押し等の警告対象）。
                        start = press_at
                        growing = True
                    if release_at is not None and release_at > end:
                        end = release_at
                        growing = True
                    absorbed.add(id(press_ev))
                    if release_ev is not None:
                        absorbed.add(id(release_ev))
        clips.append((start, end))
    return clips, absorbed


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
            seg[side] = ((round(r0), round(th0)), (round(r1), round(th1)))
        if not seg:
            continue
        t_int = int(round(span))
        # 両側とも無変化の区間は stick_move が待つだけで何も送らない。
        # 以前は捨てていたため、捨てた区間の時間が再生総時間から消え、
        # ゆっくり操作が超高速で再生されていた。静止区間として残す。
        if all(f == t for f, t in seg.values()):
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
    # stick 区間の開始時刻は ordered（記録時刻順）の区間開始を使う。
    # 以前は出した区間の span だけを積算していたため、捨てた無変化区間の
    # 時間が再生総時間から消え、ゆっくり操作が超高速で再生されていた。
    # 無変化区間の時間も ordered 上に残る（後続区間が前倒しにならない）。
    timed: list[tuple[float, int, dict[str, Any]]] = []
    tail: list[dict[str, Any]] = []
    bounds = [lo for lo, _hi in zip(ordered, ordered[1:]) if _hi > lo]
    pos = 0
    for step in steps:
        if step["kind"] in ("hold", "hold_end"):
            timed.append((float(step["at_ms"]), 0, step))
        elif step["kind"] == "release_stick":
            # 終端の解放は常に最後（時刻ソートの対象外）。
            tail.append(step)
        else:
            start = bounds[pos] if pos < len(bounds) else 0.0
            pos += 1
            timed.append((start, 1, step))
    timed.sort(key=lambda item: (item[0], item[1]))
    return [step for _, _, step in timed] + tail
