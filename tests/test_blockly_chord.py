"""同時押し（chord連鎖）の記録の検証（実機不要）。

決め打ちの InputEvent 列を services/blockly_record.events_to_chord_steps へ
渡し、hold/holdEnd の連鎖・待ちの分割・Hat 入替を固定する。at は
perf_counter 値、duration は RELEASE だけに付ける（既存の作り方を踏襲）。
"""

from __future__ import annotations

import datetime
import math
import time
from collections import Counter
from typing import Any

import pytest
from blockly_node import NEEDS_NODE, run_blockly
from core import InputLog, Sender
from core.Keys import Button, Hat, KeyPress
from fakes import FakeTransport
from services import blockly_record

pytestmark = NEEDS_NODE

#: 再記録との時刻比較の許し（実 sleep の揺れを見る分）。
_ROUNDTRIP_TOL_S = 0.15


def _event(
    action: str,
    kind: str,
    name: str,
    at: float,
    duration: float | None = None,
) -> InputLog.InputEvent:
    """Given: 時刻と押下時間だけを持つ決め打ちのイベント列を作る。"""
    wall = datetime.datetime.now()
    return InputLog.InputEvent(action, kind, name, at, wall, duration=duration)


def _stick_events(
    t0: float,
    points: list[tuple[float, float, float]],
) -> list[InputLog.InputEvent]:
    """Given: 座標列 [(dt, x, y)] から PRESS/CHANGE を作る。"""
    evs: list[InputLog.InputEvent] = []
    for i, (dt, x, y) in enumerate(points):
        at = t0 + dt
        deg = math.degrees(math.atan2(128 - y, x - 128))
        mag = min(1.0, math.hypot(x - 128, y - 128) / 128.0)
        evs.append(
            InputLog.InputEvent(
                "PRESS" if i == 0 else "CHANGE",
                "stick",
                "Stick.LEFT",
                at,
                datetime.datetime.now(),
                x=x,
                y=y,
                deg=deg,
                mag=mag,
            )
        )
    return evs


def _target_obj(name: str) -> Any:
    """Given: Button.A / Hat.TOP のような名前 / When: 変換する / Then: KeyPress に渡せる実体を返すこと。"""
    head, _, tail = name.partition(".")
    if head == "Button":
        return getattr(Button, tail)
    if head == "Hat":
        return getattr(Hat, tail)
    raise ValueError(f"想定外の target: {name}")


def _row_buttons(rows: list[str]) -> list[int]:
    """Given: 送信行 / When: 先頭欄を読む / Then: ボタンの姿勢ビットだけを返すこと。"""
    return [(int(row.split()[0], 16) >> 2) for row in rows]


def _row_hats(rows: list[str]) -> list[str]:
    """Given: 送信行 / When: 次欄を読む / Then: Hat の値だけを返すこと。"""
    return [row.split()[1] for row in rows]


def _replay(steps: list[dict[str, Any]], keys: KeyPress) -> None:
    """Given: chord 手順 / When: 疑似の送信線へ再生する / Then: hold 待ちだけ待つこと。"""
    for step in steps:
        kind = step.get("kind")
        if kind == "hold":
            keys.hold(_target_obj(str(step["target"])))
            time.sleep(float(step.get("wait", 0.0)))
        elif kind == "hold_end":
            keys.holdEnd(_target_obj(str(step["target"])))
        elif kind == "wait":
            time.sleep(float(step.get("wait", 0.0)))
        elif kind == "press":
            obj = _target_obj(str(step["target"]))
            keys.input(obj)
            time.sleep(float(step.get("duration", 0.1)))
            keys.inputEnd(obj)
            time.sleep(float(step.get("wait", 0.1)))


def test_chord_staggered() -> None:
    """Given: A↓R↓A↑R↑（ずれあり） / When: 変換する / Then: hold 連鎖＋再生順が A→A+R→R→中立になること。"""
    # Given: ずらした同時押し
    events = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("PRESS", "button", "Button.R", 10.2),
        _event("RELEASE", "button", "Button.A", 10.5, 0.5),
        _event("RELEASE", "button", "Button.R", 10.7, 0.5),
    ]
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    # Then: hold A・hold R・holdEnd A・holdEnd R＋wait の並びになる
    assert [s["kind"] for s in steps] == [
        "hold",
        "hold",
        "hold_end",
        "wait",
        "hold_end",
        "wait",
    ]
    assert [s["target"] for s in steps if s["kind"] == "hold"] == [
        "Button.A",
        "Button.R",
    ]
    assert [s["target"] for s in steps if s["kind"] == "hold_end"] == [
        "Button.A",
        "Button.R",
    ]
    # Then: R 単独区間（A↑→R↑）は約0.2秒の待ちになる
    assert abs(float(steps[3]["wait"]) - 0.2) <= 0.02
    # When: 疑似の送信線へ再生する
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    _replay(steps, KeyPress(sender))
    # Then: 再生順は A→A+R→R→中立になる
    bit_a = 1 << InputLog.BUTTON_NAMES.index("Button.A")
    bit_r = 1 << InputLog.BUTTON_NAMES.index("Button.R")
    assert _row_buttons(transport.rows) == [bit_a, bit_a | bit_r, bit_r, 0]


def test_chord_simultaneous_same_row() -> None:
    """Given: 同atの PRESS 対 / When: 変換する / Then: 過渡1回＋BUTTON_NAMES 表順になること。"""
    # Given: 完全同時の PRESS 対（入力順は R→A）
    events = [
        _event("PRESS", "button", "Button.R", 10.0),
        _event("PRESS", "button", "Button.A", 10.0),
        _event("RELEASE", "button", "Button.R", 10.5, 0.5),
        _event("RELEASE", "button", "Button.A", 10.5, 0.5),
    ]
    # Then: 同時刻の通知材料として件数を数える（2件の同時開始）
    assert len([ev for ev in events if ev.action == "PRESS" and ev.at == 10.0]) == 2
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    holds = [s for s in steps if s["kind"] == "hold"]
    # Then: 決定的順序（BUTTON_NAMES 表順。A が R より前）になる
    assert [s["target"] for s in holds] == ["Button.A", "Button.R"]
    # Then: 過渡1回（先頭 hold の待ちは 0 で同時開始がずれない）
    assert holds[0]["wait"] == 0.0


def test_chord_release_gap() -> None:
    """Given: A↑→0.1秒→R↑ / When: 変換する / Then: R 単独区間が約0.1秒の kind:wait になること。"""
    # Given: 離す側だけが 0.1 秒ずれる重なり
    events = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("PRESS", "button", "Button.R", 10.2),
        _event("RELEASE", "button", "Button.A", 10.5, 0.5),
        _event("RELEASE", "button", "Button.R", 10.6, 0.4),
    ]
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    kinds = [s["kind"] for s in steps]
    # Then: holdEnd A→wait→holdEnd R の順に R 単独区間が残る
    assert kinds == ["hold", "hold", "hold_end", "wait", "hold_end", "wait"]
    gap = steps[kinds.index("hold_end") + 1]
    assert gap["kind"] == "wait"
    assert abs(float(gap["wait"]) - 0.1) <= 0.02


def test_chord_hat_switch_no_neutral() -> None:
    """Given: A 長押し中の Hat 切替 / When: 変換・再生する / Then: hold(新)→holdEnd(旧) で中立過渡がないこと。"""
    # Given: A を押したまま Hat.TOP→TOP_RIGHT へ切り替える
    events = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("PRESS", "hat", "Hat.TOP", 10.2),
        _event("RELEASE", "hat", "Hat.TOP", 10.4, 0.2),
        _event("PRESS", "hat", "Hat.TOP_RIGHT", 10.4),
        _event("RELEASE", "hat", "Hat.TOP_RIGHT", 10.6, 0.2),
        _event("RELEASE", "button", "Button.A", 10.8, 0.8),
    ]
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    order = [(s["kind"], s.get("target")) for s in steps]
    # Then: 新（TOP_RIGHT）の hold が旧（TOP）の holdEnd より先になる
    assert order.index(("hold", "Hat.TOP_RIGHT")) < order.index(("hold_end", "Hat.TOP"))
    # When: 疑似の送信線へ再生する
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    _replay(steps, KeyPress(sender))
    # Then: Hat が有効な区間に中立（8）の行が混ざらない
    hats = _row_hats(transport.rows)
    first = next(i for i, hat in enumerate(hats) if hat != str(int(Hat.CENTER)))
    last = max(i for i, hat in enumerate(hats) if hat != str(int(Hat.CENTER)))
    assert all(hat != str(int(Hat.CENTER)) for hat in hats[first : last + 1])


def test_chord_touch_is_not_chord() -> None:
    """Given: A↑B↓が同at（端点タッチ） / When: 変換する / Then: 連鎖化せず press 2個になること。"""
    # Given: 端だけが触れる前後操作（重なりなし）
    events = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("RELEASE", "button", "Button.A", 10.5, 0.5),
        _event("PRESS", "button", "Button.B", 10.5),
        _event("RELEASE", "button", "Button.B", 11.0, 0.5),
    ]
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    # Then: hold 化せず press 2個のままになる
    assert [s["kind"] for s in steps] == ["press", "press"]
    assert [s["target"] for s in steps] == ["Button.A", "Button.B"]


def test_chord_stop_mid_hold() -> None:
    """Given: 停止時に押下中 / When: 変換する / Then: 連鎖内は holdEnd・孤立は press で hold 残留がないこと。"""
    # Given: 連鎖の途中で B を押したまま止める
    chain = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("PRESS", "button", "Button.B", 10.1),
        _event("RELEASE", "button", "Button.A", 10.4, 0.4),
    ]
    # When: 停止時刻で変換する
    chained = blockly_record.events_to_chord_steps(chain, stop_at=11.0)
    holds = Counter(s["target"] for s in chained if s["kind"] == "hold")
    ends = Counter(s["target"] for s in chained if s["kind"] == "hold_end")
    # Then: 連鎖内は holdEnd で閉じ、hold だけが残らない
    assert holds == ends == Counter({"Button.A": 1, "Button.B": 1})
    # Given: 孤立した押下を止める
    alone = [_event("PRESS", "button", "Button.X", 20.0)]
    # When: 停止時刻で変換する
    single = blockly_record.events_to_chord_steps(alone, stop_at=20.5)
    # Then: 孤立は press（duration＝停止−押下）で hold は出ない
    assert [s["kind"] for s in single] == ["press"]
    assert single[0]["target"] == "Button.X"
    assert single[0]["duration"] == 0.5


def test_chord_stale_release_dropped() -> None:
    """Given: 開始前押下の RELEASE / When: 変換する / Then: 捨てて残りを press にすること。"""
    # Given: PRESS の無い RELEASE＋通常操作
    events = [
        _event("RELEASE", "button", "Button.A", 10.0, 0.1),
        _event("PRESS", "button", "Button.B", 10.5),
        _event("RELEASE", "button", "Button.B", 11.0, 0.5),
    ]
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    # Then: 開始前からの RELEASE は捨てられる
    assert [s["target"] for s in steps] == ["Button.B"]
    assert [s["kind"] for s in steps] == ["press"]


def test_chord_long_wait_split() -> None:
    """Given: 75秒相当の間隔 / When: 変換・挿入する / Then: hold/WAIT 側は 60＋15 に割れ pokecon_wait は1個のままなこと。"""
    # Given: 75秒重なる同時押し（R 持ち時間が 75秒）
    events = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("PRESS", "button", "Button.R", 10.0),
        _event("RELEASE", "button", "Button.A", 85.0, 75.0),
        _event("RELEASE", "button", "Button.R", 85.2, 75.2),
    ]
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    # Then: hold の WAIT 欄（上限60）に収まるよう 60＋15 に割れる
    assert steps[1] == {"kind": "hold", "target": "Button.R", "wait": 60.0}
    assert steps[2] == {"kind": "wait", "wait": 15.0}
    # When: 75秒の待ちを挿入する
    out = run_blockly(
        """
const ws = new Blockly.Workspace();
const made = Blockly.PokeconEditor.insertSteps(ws, [
  { kind: 'wait', wait: 75 },
], null);
const waits = made.filter((b) => b.type === 'pokecon_wait');
done({ n: made.length, types: made.map((b) => b.type),
       sec: waits.length ? waits[0].getFieldValue('SEC') : null });
"""
    )
    # Then: pokecon_wait（SEC 上限3600）は1個のまま 75秒を持つ
    assert out["types"] == ["pokecon_wait"]
    assert float(out["sec"]) == 75


def test_hat_roll_alone_no_neutral() -> None:
    """Given: Hat 単独ロール / When: 変換・再生する / Then: 入替で中立行がなく最後の holdEnd で初めて中立になること。"""
    # 安全性根拠の回帰試験。Hat 切替の中立過渡なしは、Keys.hold の
    # holdButton 残留ガードと Keys.inputEnd の hold_hat 復元に依存する
    # （参照は関数名で行い、行番号では行わないこと）。
    # Given: 十字キーだけの TOP→TOP_RIGHT ロール
    events = [
        _event("PRESS", "hat", "Hat.TOP", 10.0),
        _event("RELEASE", "hat", "Hat.TOP", 10.3, 0.3),
        _event("PRESS", "hat", "Hat.TOP_RIGHT", 10.3),
        _event("RELEASE", "hat", "Hat.TOP_RIGHT", 10.6, 0.3),
    ]
    # When: 変換する
    steps = blockly_record.events_to_chord_steps(events)
    order = [(s["kind"], s.get("target")) for s in steps]
    # Then: 連鎖化し、新の hold が旧の holdEnd より先になる（中立過渡なし）
    assert [kind for kind, _ in order][:2] == ["hold", "hold"]
    assert order.index(("hold", "Hat.TOP_RIGHT")) < order.index(("hold_end", "Hat.TOP"))
    # When: 疑似の送信線へ再生する
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    keys = KeyPress(sender)
    keys.hold(Hat.TOP)
    keys.hold(Hat.TOP_RIGHT)
    # Then: 旧を離しても新が残り、中立へ落ちない
    keys.holdEnd(Hat.TOP)
    assert sender.getPosture()["hat"] == int(Hat.TOP_RIGHT)
    # Then: 新の holdEnd で初めて中立に戻る
    keys.holdEnd(Hat.TOP_RIGHT)
    assert sender.getPosture()["hat"] == int(Hat.CENTER)


def test_chord_double_hold_raises() -> None:
    """Given: 同一 target の二重 hold / When: 変換する / Then: 警告でなく ValueError になること。"""
    # Given: 同一ボタンの重なり（離す前に同じボタンを押す）
    doubled_button = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("PRESS", "button", "Button.A", 10.1),
        _event("RELEASE", "button", "Button.A", 10.5, 0.5),
        _event("RELEASE", "button", "Button.A", 10.6, 0.5),
    ]
    # When / Then: 変換すると ValueError になる
    with pytest.raises(ValueError):
        blockly_record.events_to_chord_steps(doubled_button)
    # Given: 同一 Hat の重なり
    doubled_hat = [
        _event("PRESS", "hat", "Hat.TOP", 10.0),
        _event("PRESS", "hat", "Hat.TOP", 10.1),
        _event("RELEASE", "hat", "Hat.TOP", 10.5, 0.5),
        _event("RELEASE", "hat", "Hat.TOP", 10.6, 0.5),
    ]
    # When / Then: 変換すると ValueError になる
    with pytest.raises(ValueError):
        blockly_record.events_to_chord_steps(doubled_hat)


def test_clip_extension_warns() -> None:
    """Given: 長押し混在（ZL 跨ぎ） / When: 変換する / Then: 1クリップ＋warnings へ clip_long 1件になること。"""
    # Given: 短いスティック操作を跨ぐ ZL 長押し
    t0 = 10.0
    events = _stick_events(t0, [(0.0, 200, 60), (0.1, 200, 60)])
    events.append(_event("PRESS", "button", "Button.ZL", 9.5))
    events.append(_event("RELEASE", "button", "Button.ZL", 29.0, 19.5))
    warnings: list[dict[str, Any]] = []
    # When: 変換する
    steps = blockly_record.build_motion_steps(events, warnings=warnings)
    # Then: 延長で1クリップにまとまり、警告が1件だけ出る
    assert sum(1 for s in steps if s.get("kind") == "release_stick") == 1
    assert len(warnings) == 1
    assert warnings[0]["code"] == "clip_long"
    assert float(warnings[0]["seconds"]) > blockly_record.CLIP_WARN_S
    # Then: 吸収した ZL はクリップ外の press へ出ない
    assert all(
        s.get("target") != "Button.ZL" for s in steps if s.get("kind") == "press"
    )


def test_chord_roundtrip_no_neutral_rows() -> None:
    """Given: chord 手順 / When: 疑似線で再生し再記録する / Then: エッジ順一致・時刻誤差内・Hat 切替で中立行なしになること。"""
    # Given: A 長押し中の Hat 切替（決め打ちのイベント列）
    origin = [
        _event("PRESS", "button", "Button.A", 10.0),
        _event("PRESS", "hat", "Hat.TOP", 10.2),
        _event("RELEASE", "hat", "Hat.TOP", 10.4, 0.2),
        _event("PRESS", "hat", "Hat.TOP_RIGHT", 10.4),
        _event("RELEASE", "hat", "Hat.TOP_RIGHT", 10.6, 0.2),
        _event("RELEASE", "button", "Button.A", 10.8, 0.8),
    ]
    steps = blockly_record.events_to_chord_steps(origin)
    # When: 疑似の送信線へ再生しながら tap 相当の再記録を取る
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    rec = blockly_record.Recorder(use_motion=False)
    assert rec.start(transport) is True
    try:
        _replay(steps, KeyPress(sender))
        again = rec._logger.snapshot()
    finally:
        rec.stop()
    # Then: エッジ（action/kind/name）の並び順が一致する
    assert [(ev.action, ev.kind, ev.name) for ev in again] == [
        (ev.action, ev.kind, ev.name) for ev in origin
    ]
    # Then: 相対時刻が許容誤差内に収まる
    for old, new in zip(origin, again):
        assert (
            abs(
                (float(new.at) - float(again[0].at))
                - (float(old.at) - float(origin[0].at))
            )
            <= _ROUNDTRIP_TOL_S
        )
    # Then: Hat 切替で中立行が出ない
    hats = _row_hats(transport.rows)
    first = next(i for i, hat in enumerate(hats) if hat != str(int(Hat.CENTER)))
    last = max(i for i, hat in enumerate(hats) if hat != str(int(Hat.CENTER)))
    assert all(hat != str(int(Hat.CENTER)) for hat in hats[first : last + 1])


def test_contamination_latch() -> None:
    """Given: 記録中の所有者 / When: gamepad 以外を見る / Then: 短時間でも contaminated がラッチすること。"""
    # Given: 記録中
    rec = blockly_record.Recorder()
    transport = FakeTransport()
    assert rec.start(transport) is True
    try:
        # When: gamepad だけなら汚れない
        assert (
            rec.observe_owners(
                {"btn": {"gamepad": 4}, "hat": {"gamepad": 8}, "stick": {}}
            )
            is False
        )
        assert rec.contaminated is False
        # When: 短時間だけ本体側の所有者が混ざる
        assert (
            rec.observe_owners(
                {"btn": {"gamepad": 4, "script": 4}, "hat": {}, "stick": {}}
            )
            is True
        )
        # Then: すぐ居なくなってもラッチし、見逃さない
        assert (
            rec.observe_owners(
                {"btn": {"gamepad": 4}, "hat": {"gamepad": 8}, "stick": {}}
            )
            is True
        )
        assert rec.contaminated is True
    finally:
        rec.stop()
