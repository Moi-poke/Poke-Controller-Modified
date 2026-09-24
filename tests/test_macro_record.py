"""マクロ録画のレコーダー契約（失敗定義）。実機なしで回す。"""

from __future__ import annotations

import datetime

from core.InputLog import InputEvent
from services.macro_recorder import MacroRecorder


def _wall() -> datetime.datetime:
    """実時刻の固定値。表示には使わず順序だけ見るため固定でよい。"""
    return datetime.datetime(2026, 1, 1, 0, 0, 0)


def _button(name: str, at: float, duration: float) -> InputEvent:
    """ボタンの離しイベントを作る。押下時間は duration に入る想定。"""
    return InputEvent(
        action="RELEASE",
        kind="button",
        name=name,
        at=at,
        wall=_wall(),
        duration=duration,
    )


def _stick(at: float, duration: float) -> InputEvent:
    """真上いっぱいに倒した左スティックの離しイベントを作る。"""
    return InputEvent(
        action="RELEASE",
        kind="stick",
        name="Stick.LEFT",
        at=at,
        wall=_wall(),
        duration=duration,
        deg=90.0,
        mag=1.0,
        max_mag=1.0,
        from_deg=90.0,
    )


def test_render_orders_press_and_wait() -> None:
    """押下→待ち→押下の順で self.press / self.wait が並ぶ。"""
    rec = MacroRecorder()
    rec.start()
    # 1回目: 開始 0.88／解放 1.00、2回目: 開始 1.42／解放 1.50。
    # 間の待ちは「次の開始 − 前の解放」= 0.42 になる想定。
    rec.feed(_button("Button.A", at=1.00, duration=0.12))
    rec.feed(_button("Button.B", at=1.50, duration=0.08))
    rec.stop()
    lines = rec.render()
    assert lines[0] == "    self.press(Button.A, duration=0.12)"
    assert lines[1] == "    self.wait(0.42)"
    assert lines[2] == "    self.press(Button.B, duration=0.08)"


def test_render_stick_snaps_to_up() -> None:
    """スティックは Direction.UP に丸めた名前で出す（command 書式の約束）。"""
    rec = MacroRecorder()
    rec.start()
    rec.feed(_stick(at=2.00, duration=0.20))
    rec.stop()
    lines = rec.render()
    assert lines == ["    self.press(Direction.UP, duration=0.20)"]


def test_render_rotation_and_repeat_are_comment_only() -> None:
    """回し・連打のまとめは出さない。説明だけのコメントに留める。"""
    rec = MacroRecorder()
    rec.start()
    spin = _stick(at=3.00, duration=0.50)
    # 1周分の回転。どの Direction 1つでも表せないので press 化しない。
    spin.turn = 360.0
    spin.moves = 4
    rec.feed(spin)
    rec.feed(_button("Button.A", at=3.60, duration=0.08))
    rec.feed(_button("Button.A", at=3.80, duration=0.08))
    rec.stop()
    lines = rec.render()
    # 回した説明はコメント行だけに置く。press 化はしない。
    assert any(line.startswith("    # ") for line in lines)
    assert not any("pressRep" in line for line in lines)
    # 連打もまとめず1回ずつ press として並べる。
    assert sum("self.press(Button.A" in line for line in lines) == 2
