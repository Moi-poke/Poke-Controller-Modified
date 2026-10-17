"""記録停止時に押していたボタンの解放（hold_end の補完）を固定する。実機不要。"""

from __future__ import annotations

import datetime
import math
from typing import Any

from core import InputLog
from services import blockly_record as br

_T0 = 1000.0


def _btn(action: str, at: float, name: str = "Button.ZR") -> InputLog.InputEvent:
    return InputLog.InputEvent(
        action, "button", name, _T0 + at, datetime.datetime.now()
    )


def _hat(action: str, at: float, name: str) -> InputLog.InputEvent:
    return InputLog.InputEvent(action, "hat", name, _T0 + at, datetime.datetime.now())


def _stick(duration: float = 0.6, release_at: float | None = 0.6) -> list[Any]:
    """スティックを倒して（必要なら）戻すイベント列。"""
    evs: list[Any] = []
    n = int(duration / 0.008)
    for i in range(n + 1):
        dt = i * 0.008
        x = 128.0 + 100.0 * min(1.0, dt / 0.2)
        evs.append(
            InputLog.InputEvent(
                "PRESS" if i == 0 else "CHANGE",
                "stick",
                "Stick.LEFT",
                _T0 + dt,
                datetime.datetime.now(),
                x=x,
                y=128.0,
                deg=math.degrees(math.atan2(0.0, x - 128.0)),
                mag=min(1.0, math.hypot(x - 128.0, 0.0) / 128.0),
            )
        )
    if release_at is not None:
        evs.append(
            InputLog.InputEvent(
                "RELEASE",
                "stick",
                "Stick.LEFT",
                _T0 + release_at,
                datetime.datetime.now(),
                duration=release_at,
                deg=90.0,
                mag=1.0,
                max_mag=1.0,
            )
        )
    return evs


def _hold_ends(steps: list[dict[str, Any]]) -> list[str]:
    return [str(s["target"]) for s in steps if s["kind"] == "hold_end"]


def test_stop_while_holding_closes_the_hold_at_the_clip_end() -> None:
    """Given: 倒す→ZR を押す→解放の前に停止 / When: 材料化 / Then: hold_end が補われること。"""
    steps = br.build_motion_steps(_stick() + [_btn("PRESS", 0.3)])
    assert _hold_ends(steps) == ["Button.ZR"], steps


def test_press_before_the_clip_still_gets_a_hold_end() -> None:
    """Given: ZR を押してから倒して停止 / When: 材料化 / Then: hold_end が補われること。"""
    steps = br.build_motion_steps([_btn("PRESS", 0.0)] + _stick())
    assert _hold_ends(steps) == ["Button.ZR"], steps


def test_press_release_press_gets_a_hold_end_for_each_open_hold() -> None:
    """Given: ZR 押す→離す→押す→停止 / When: 材料化 / Then: 解放の無い 2 回目にも hold_end が出ること。"""
    steps = br.build_motion_steps(
        _stick() + [_btn("PRESS", 0.2), _btn("RELEASE", 0.3), _btn("PRESS", 0.4)]
    )
    holds = [s for s in steps if s["kind"] == "hold"]
    assert len(holds) == 2, steps
    # 1 つ目は記録された RELEASE が、2 つ目は補完が閉じる。
    assert _hold_ends(steps) == ["Button.ZR", "Button.ZR"], steps


def test_control_without_the_closure_leaves_the_button_held() -> None:
    """Given: 補完を無効にした状態 / When: 同じ入力 / Then: hold_end が出ないこと（検査が効いている証拠）。"""
    original = br._close_open_holds
    br._close_open_holds = lambda steps, clip_len_ms: []  # type: ignore[assignment]
    try:
        steps = br.build_motion_steps(_stick() + [_btn("PRESS", 0.3)])
    finally:
        br._close_open_holds = original  # type: ignore[assignment]
    assert _hold_ends(steps) == [], steps


def test_close_open_holds_keeps_the_minimum_press_time() -> None:
    """Given: 終端の直前に押した hold / When: 補完 / Then: 押下から最短時間を下回らないこと。"""
    steps: list[dict[str, Any]] = [
        {"kind": "hold", "target": "Button.ZR", "at_ms": 595}
    ]
    assert br._close_open_holds(steps, 600) == ["Button.ZR"]
    assert steps[-1] == {
        "kind": "hold_end",
        "target": "Button.ZR",
        "at_ms": 595 + br.MIN_HOLD_MS,
        # 記録に無い解放である印（呼び出し側が利用者への通知に使う）。
        "auto_release": True,
    }


def test_close_open_holds_never_ends_before_the_clip() -> None:
    """Given: 早い時刻の hold / When: 補完 / Then: クリップ終端より前へ出ないこと。"""
    steps: list[dict[str, Any]] = [{"kind": "hold", "target": "Button.A", "at_ms": 100}]
    br._close_open_holds(steps, 600)
    assert steps[-1]["at_ms"] == 600


def test_hat_direction_change_without_release_closes_both_directions() -> None:
    """Given: Hat 上→右（上を離さず）→停止 / When: 材料化 / Then: 両方に hold_end が出ること。"""
    steps = br.build_motion_steps(
        _stick() + [_hat("PRESS", 0.2, "Hat.TOP"), _hat("PRESS", 0.3, "Hat.RIGHT")]
    )
    ends = _hold_ends(steps)
    # 上の RELEASE が記録に無いので、上も右も「開いたまま」になる。
    # 両方を閉じないと、生成コードがどちらかを押したまま終わる。
    assert ends == ["Hat.TOP", "Hat.RIGHT"], steps
    # 補完は「動いている stick 区間」より後（クリップ終端）に置かれる。
    # クリップ末尾には長さ 0 の静止区間が付くことがあるので、それも含めた
    # 位置は問わない（順序の意味は「動きが終わってから離す」こと）。
    kinds = [s["kind"] for s in steps]
    moving = [
        i
        for i, s in enumerate(steps)
        if s["kind"] in ("stick", "stick2") and int(s.get("t_ms", 0)) > 0
    ]
    assert kinds.index("hold_end") > max(moving), kinds


def test_auto_hold_end_does_not_stretch_the_clip() -> None:
    """Given: 終端 1ms 前に押した hold / When: 材料化 / Then: stick 区間の合計が変わらないこと。"""
    base = br.build_motion_steps(_stick())
    late = br.build_motion_steps(_stick() + [_btn("PRESS", 0.599)])
    total = sum(int(s["t_ms"]) for s in late if s["kind"] in ("stick", "stick2"))
    base_total = sum(int(s["t_ms"]) for s in base if s["kind"] in ("stick", "stick2"))
    # 補完した hold_end は待ちを持たないので、再生時間（stick 区間の合計）は
    # 伸びない。順序は stick 区間より後（クリップ終端）になる。
    assert total == base_total, (total, base_total)
    kinds = [s["kind"] for s in late]
    moving = [
        i
        for i, s in enumerate(late)
        if s["kind"] in ("stick", "stick2") and int(s.get("t_ms", 0)) > 0
    ]
    assert any(s.get("auto_release") for s in late), late
    assert kinds.index("hold_end") > max(moving), kinds
