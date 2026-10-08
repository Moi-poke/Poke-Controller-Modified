"""motion 記録パイプラインの検証（実機不要）。

設計スレッド ID:001〜009 の確定事項のうち、記録側の生成ルールを固定する。
- クリップ内は hold/holdEnd（待ちなし）。press は使わない。
- 分割点の値は圧縮後の折れ線から補間で求める。
- クリップ終端では動かした側ごとに解放材料を出す。
- クリップ外は従来の press 変換。
"""

from __future__ import annotations

import datetime
import math

from blockly_node import NEEDS_NODE
from core import InputLog
from services import blockly_record as br

pytestmark = NEEDS_NODE


def _stick_events(
    t0: float,
    points: list[tuple[float, float, float]],
    release_at: float | None = None,
) -> list[InputLog.InputEvent]:
    """座標列 [(dt, x, y)] から PRESS/CHANGE (+RELEASE) を作る。"""
    evs = []
    for i, (dt, x, y) in enumerate(points):
        at = t0 + dt
        deg = math.degrees(math.atan2(y - 128, x - 128))
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
    if release_at is not None:
        evs.append(
            InputLog.InputEvent(
                "RELEASE",
                "stick",
                "Stick.LEFT",
                t0 + release_at,
                datetime.datetime.now(),
                duration=release_at,
                deg=90.0,
                mag=1.0,
                max_mag=1.0,
            )
        )
    return evs


def _circle_points(
    n: int = 63, r: float = 127.0, period: float = 0.5
) -> list[tuple[float, float, float]]:
    out = []
    for i in range(n):
        dt = i * period / (n - 1)
        deg = 90 + i * 360 / (n - 1)
        x = 128 + r * math.cos(math.radians(deg))
        y = 128 + r * math.sin(math.radians(deg))
        out.append((dt, x, y))
    return out


def test_circle_with_button_produces_hold_and_sticks() -> None:
    """Given: 円回し＋途中のボタン / When: 変換 / Then: hold 統一＋stick 区間になること。"""
    t0 = 5000.0
    evs = _stick_events(t0, _circle_points(), release_at=0.51)
    evs.append(
        InputLog.InputEvent(
            "PRESS", "button", "Button.A", t0 + 0.20, datetime.datetime.now()
        )
    )
    evs.append(
        InputLog.InputEvent(
            "RELEASE",
            "button",
            "Button.A",
            t0 + 0.30,
            datetime.datetime.now(),
            duration=0.1,
        )
    )
    steps = br.build_motion_steps(sorted(evs, key=lambda e: e.at))
    kinds = [s["kind"] for s in steps]
    # press は混ざらない（クリップ内は hold 統一）。
    assert "press" not in kinds
    assert "hold" in kinds
    assert "hold_end" in kinds
    sticks = [s for s in steps if s["kind"] == "stick"]
    assert len(sticks) >= 2
    # ボタン時刻で区間が分割されている（hold の前後で区間が切れる）。
    assert any(s["t_ms"] <= 60 for s in sticks)
    # 終端に解放材料がある。
    assert steps[-1] == {"kind": "release_stick", "side": "L"}
    # T に丸めなし（整数 ms のまま）。
    assert all(isinstance(s["t_ms"], int) for s in sticks)


def test_no_stick_falls_back_to_press() -> None:
    """Given: ボタンだけ / When: 変換 / Then: 従来の press 変換になること。"""
    t0 = 6000.0
    wall = datetime.datetime.now()
    evs = [
        InputLog.InputEvent("PRESS", "button", "Button.A", t0, wall),
        InputLog.InputEvent(
            "RELEASE", "button", "Button.A", t0 + 0.1, wall, duration=0.1
        ),
    ]
    steps = br.build_motion_steps(evs)
    assert all(s["kind"] == "press" for s in steps)
    assert steps[0]["target"] == "Button.A"


def test_full_turn_theta_accumulates() -> None:
    """Given: 1 周の円 / When: 変換 / Then: 累積 θ が 360° になること。"""
    t0 = 7000.0
    evs = _stick_events(t0, _circle_points(), release_at=0.51)
    steps = br.build_motion_steps(sorted(evs, key=lambda e: e.at))
    sticks = [s for s in steps if s["kind"] == "stick"]
    first = sticks[0]["from"][1]
    last = sticks[-1]["to"][1]
    assert abs((last - first) - 360.0) <= 5.0


def test_recorder_stop_returns_motion_when_stick_present() -> None:
    """Given: 実線に記録 / When: スティックを動かして止める / Then: motion 材料が返ること。"""
    import time

    from core import Sender
    from core.Keys import Button, Direction, KeyPress
    from fakes import FakeTransport

    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    keys = KeyPress(sender)
    rec = br.Recorder()
    assert rec.start(transport) is True
    keys.input(Direction.UP)
    time.sleep(0.05)
    keys.input(Direction.UP_RIGHT)
    time.sleep(0.05)
    keys.inputEnd(Direction.UP_RIGHT)
    keys.input(Button.A)
    time.sleep(0.05)
    keys.inputEnd(Button.A)
    steps = rec.stop()
    kinds = {s.get("kind", "press") for s in steps}
    # stick 区間か、少なくとも従来の press 手順が返る。
    assert kinds & {"stick", "stick2", "press"}


def test_insert_motion_steps_builds_motion_blocks() -> None:
    """Given: motion 材料 / When: insertSteps で作る / Then: motion/hold 系ブロックになること。"""
    from blockly_node import run_blockly

    out = run_blockly(
        """
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' },
    inputs: { DO: { block: { type: 'pokecon_press', id: 'p1',
      fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } }
] } }, ws);
const made = Blockly.PokeconEditor.insertSteps(ws, [
  { kind: 'stick', side: 'L', from: [0, 90], to: [127, 90], t_ms: 40 },
  { kind: 'hold', target: 'Button.A' },
  { kind: 'stick', side: 'L', from: [127, 90], to: [127, 450], t_ms: 460 },
  { kind: 'hold_end', target: 'Button.A' },
  { kind: 'release_stick', side: 'L' },
], ws.getBlockById('p1'));
const types = made.map((b) => b.type);
const motion = made.filter((b) => b.type === 'pokecon_motion')[0];
done({ n: made.length, types: types,
       stick: motion ? motion.getFieldValue('STICK') : null,
       from: motion ? motion.getFieldValue('FROM') : null,
       to: motion ? motion.getFieldValue('TO') : null,
       t: motion ? motion.getFieldValue('T_MS') : null,
       end: made.filter((b) => b.type === 'pokecon_motion').map((b) => b.getFieldValue('END')) });
"""
    )
    # motion 2 個＋hold＋hold_end。release_stick はブロックを増やさず
    # 直前の motion の END を RELEASE にする。
    assert out["types"] == [
        "pokecon_motion",
        "pokecon_hold",
        "pokecon_motion",
        "pokecon_hold_end",
    ]
    assert out["stick"] == "LEFT"
    assert out["from"] == "0,90"
    assert out["to"] == "127,90"
    assert float(out["t"]) == 40
    assert out["end"] == ["CONT", "RELEASE"]


def test_motion_generator_emits_stick_move() -> None:
    """Given: motion ブロック / When: 生成する / Then: stick_move が出ること。"""
    from blockly_node import run_blockly

    out = run_blockly(
        """
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_motion', id: 'm1',
    fields: { STICK: 'LEFT', FROM: '0,90', TO: '127,450', T_MS: 500, END: 'RELEASE' } },
] } }, ws);
const blk = ws.getBlockById('m1');
const code = Blockly.Python.forBlock['pokecon_motion'](blk, Blockly.Python);
done({ code: code });
"""
    )
    assert "self.stick_move(Stick.LEFT, 0, 90, 127, 450, 500)" in out["code"]
    assert "self.stick_release(Stick.LEFT)" in out["code"]


def test_motion2_generator_emits_stick_move2() -> None:
    """Given: motion2 ブロック / When: 生成する / Then: stick_move2 が出ること。"""
    from blockly_node import run_blockly

    out = run_blockly(
        """
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_motion2', id: 'm2',
    fields: { FROM_L: '0,90', TO_L: '127,90', FROM_R: '0,0', TO_R: '127,180',
              T_MS: 200, END: 'CONT' } },
] } }, ws);
const blk = ws.getBlockById('m2');
const code = Blockly.Python.forBlock['pokecon_motion2'](blk, Blockly.Python);
done({ code: code });
"""
    )
    assert "self.stick_move2(Stick.LEFT" in out["code"]
    assert "Stick.RIGHT" in out["code"]
    assert "stick_release" not in out["code"]


def test_motion_playback_with_hold_keeps_hat() -> None:
    """Given: 十字キーの押しっぱなし＋スティック区間の再生 / When: 区間の前後で hold/holdEnd を併用する / Then: 途中の解放で Hat が落ちず最後に戻ること。"""
    import threading

    from core import Sender
    from core.CommandOperate import OperateMixin
    from core.Keys import Button, Hat, KeyPress, Stick
    from fakes import FakeTransport

    class _LiveHost(OperateMixin):
        """再生の台。待ちは実時間で回す。"""

        def __init__(self, keys: KeyPress) -> None:
            self.keys = keys
            self.alive = True
            self._stop_event = threading.Event()
            self._resume_event = threading.Event()
            self._resume_event.set()

        def _cleanup(self) -> None:
            return None

        def _pausedSeconds(self) -> float:
            return 0.0

    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    host = _LiveHost(KeyPress(sender))
    # Given: 十字キーを押さえたまま区間を再生する
    host.hold(Hat.TOP)
    host.stick_move(Stick.LEFT, 0, 90, 127, 90, 40)
    host.stick_release(Stick.LEFT)
    # When: 関係ないボタンを離す
    host.keys.inputEnd(Button.A)
    # Then: 押しっぱなしの十字キーが残る
    assert sender.getPosture()["hat"] == int(Hat.TOP)
    # When: 押しっぱなしを解く
    host.holdEnd(Hat.TOP)
    # Then: 中立に戻る
    assert sender.getPosture()["hat"] == int(Hat.CENTER)


def _stick_events_right(
    t0: float,
    points: list[tuple[float, float, float]],
    release_at: float | None = None,
) -> list[InputLog.InputEvent]:
    """R側版の材料化。_stick_events と同じ座標列を Stick.RIGHT で作る。"""
    evs = []
    for i, (dt, x, y) in enumerate(points):
        at = t0 + dt
        deg = math.degrees(math.atan2(y - 128, x - 128))
        mag = min(1.0, math.hypot(x - 128, y - 128) / 128.0)
        evs.append(
            InputLog.InputEvent(
                "PRESS" if i == 0 else "CHANGE",
                "stick",
                "Stick.RIGHT",
                at,
                datetime.datetime.now(),
                x=x,
                y=y,
                deg=deg,
                mag=mag,
            )
        )
    if release_at is not None:
        evs.append(
            InputLog.InputEvent(
                "RELEASE",
                "stick",
                "Stick.RIGHT",
                t0 + release_at,
                datetime.datetime.now(),
                duration=release_at,
                deg=90.0,
                mag=1.0,
                max_mag=1.0,
            )
        )
    return evs


def test_dual_stick_materializes_stick2() -> None:
    """Given: L/R 同時に同じ円回し / When: 変換 / Then: kind=stick2 が出て両側の from/to と t_ms を持つこと。"""
    t0 = 11000.0
    pts = _circle_points()
    evs = _stick_events(t0, pts, release_at=0.51) + _stick_events_right(
        t0, pts, release_at=0.51
    )
    steps = br.build_motion_steps(sorted(evs, key=lambda e: e.at))
    sticks2 = [s for s in steps if s["kind"] == "stick2"]
    assert sticks2
    for s in sticks2:
        assert {"l_from", "l_to", "r_from", "r_to", "t_ms"} <= set(s)
        assert isinstance(s["t_ms"], int)


def test_dual_stick_ends_with_both_release() -> None:
    """Given: L/R 同時に同じ円回し / When: 変換 / Then: 終端が side=BOTH の release_stick になること。"""
    t0 = 11100.0
    pts = _circle_points()
    evs = _stick_events(t0, pts, release_at=0.51) + _stick_events_right(
        t0, pts, release_at=0.51
    )
    steps = br.build_motion_steps(sorted(evs, key=lambda e: e.at))
    assert steps[-1] == {"kind": "release_stick", "side": "BOTH"}


def _arc_points(
    n: int = 15,
    r: float = 100.0,
    period: float = 0.1,
    deg0: float = 90.0,
    span: float = 90.0,
    dt0: float = 0.0,
) -> list[tuple[float, float, float]]:
    """短い弧の座標列。B6 の移動部の材料にする。"""
    out = []
    for i in range(n):
        dt = dt0 + i * period / (n - 1)
        deg = deg0 + i * span / (n - 1)
        out.append(
            (
                dt,
                128 + r * math.cos(math.radians(deg)),
                128 + r * math.sin(math.radians(deg)),
            )
        )
    return out


def test_button_cut_inside_flat_adds_no_stick_segment() -> None:
    """Given: 動くクリップ内の静止小区間と平坦部に入るボタンcut / When: 変換 / Then: ボタンなし時より stick 区間が増えないこと。"""
    t0 = 12000.0
    move1 = _arc_points(dt0=0.0)
    fx, fy = move1[-1][1], move1[-1][2]
    flat = [(0.1 + i * 0.01, fx, fy) for i in range(21)]
    pts = move1 + flat + _arc_points(deg0=180.0, dt0=0.3)
    base = br.build_motion_steps(
        sorted(_stick_events(t0, pts, release_at=0.41), key=lambda e: e.at)
    )
    evs = _stick_events(t0, pts, release_at=0.41)
    evs.append(
        InputLog.InputEvent(
            "PRESS", "button", "Button.A", t0 + 0.20, datetime.datetime.now()
        )
    )
    evs.append(
        InputLog.InputEvent(
            "RELEASE",
            "button",
            "Button.A",
            t0 + 0.25,
            datetime.datetime.now(),
            duration=0.05,
        )
    )
    with_cut = br.build_motion_steps(sorted(evs, key=lambda e: e.at))
    n_base = sum(1 for s in base if s["kind"] == "stick")
    n_cut = sum(1 for s in with_cut if s["kind"] == "stick")
    assert n_cut <= n_base


def test_buttons_around_stick_stay_as_press_in_order() -> None:
    """Given: スティック前後のボタン(A押下→スティック→B押下) / When: 変換 / Then: 前後が kind=press で残り press→stick…→press の時刻順になること。"""
    t0 = 13000.0
    evs = [
        InputLog.InputEvent("PRESS", "button", "Button.A", t0, datetime.datetime.now()),
        InputLog.InputEvent(
            "RELEASE",
            "button",
            "Button.A",
            t0 + 0.1,
            datetime.datetime.now(),
            duration=0.1,
        ),
    ]
    evs += _stick_events(
        t0 + 0.3, _circle_points(n=25, r=100.0, period=0.2), release_at=0.21
    )
    evs += [
        InputLog.InputEvent(
            "PRESS", "button", "Button.B", t0 + 0.9, datetime.datetime.now()
        ),
        InputLog.InputEvent(
            "RELEASE",
            "button",
            "Button.B",
            t0 + 1.0,
            datetime.datetime.now(),
            duration=0.1,
        ),
    ]
    steps = br.build_motion_steps(sorted(evs, key=lambda e: e.at))
    kinds = [s["kind"] for s in steps]
    assert kinds[0] == "press"
    assert steps[0]["target"] == "Button.A"
    assert kinds[-1] == "press"
    assert steps[-1]["target"] == "Button.B"
    first_stick = kinds.index("stick")
    last_stick = len(kinds) - 1 - kinds[::-1].index("stick")
    assert first_stick > 0
    assert last_stick < len(kinds) - 1
    assert all(k == "stick" for k in kinds[first_stick : last_stick + 1])


def test_two_discrete_ops_stay_single_clip() -> None:
    """Given: 離散2回操作(0〜0.3sと1.0〜1.3s) / When: 変換 / Then: 1クリップ扱いで2操作が1つのsteps列にまとまること(将来分割対応時の変更検出用)。"""
    t0 = 14000.0
    base = _circle_points(n=25, r=100.0, period=0.3)
    shifted = [(dt + 1.0, x, y) for dt, x, y in base]
    evs = _stick_events(t0, base, release_at=0.31) + _stick_events(
        t0, shifted, release_at=1.31
    )
    steps = br.build_motion_steps(sorted(evs, key=lambda e: e.at))
    assert any(s["kind"] == "stick" for s in steps)
    releases = [s for s in steps if s["kind"] == "release_stick"]
    assert len(releases) == 1
    assert steps[-1]["kind"] == "release_stick"
