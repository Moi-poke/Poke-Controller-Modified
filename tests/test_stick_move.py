"""stick_move / stick_move2 の検証（実機不要）。

設計スレッド ID:001〜009 の確定事項のうち、補助関数の振る舞いを固定する。
- 毎刻み t0 += P_k、_motion_clock は補正後の t0 から
- P_k は _pausedSeconds() の差分（sleep ジッタを含めない）
- 解放は固定の斜め Direction（両軸解放）
- stick_move2 は 1 刻み 1 回の input([dirL, dirR])
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.CommandOperate import OperateMixin
from core.Keys import Direction, Stick


class _FakeKeys:
    """keys の代役。input へ来た Direction を記録するだけ。"""

    def __init__(self) -> None:
        self.sent: list[list[Direction]] = []

    def input(self, btns: Any) -> None:
        items = list(btns) if isinstance(btns, list) else [btns]
        self.sent.append(items)


class _Host(OperateMixin):
    """Mixin を動かす最小の台。待ちは実時間で回す。"""

    def __init__(self) -> None:
        self.keys: Any = _FakeKeys()
        self.alive = True
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._paused_total = 0.0
        self._pause_started = 0.0

    def _cleanup(self) -> None:
        return None

    def _pausedSeconds(self) -> float:
        total = self._paused_total
        if not self._resume_event.is_set():
            total += time.perf_counter() - self._pause_started
        return total

    def pause_for(self, seconds: float) -> None:
        """一時停止を開始し、指定秒後に復帰させる（別スレッド）。"""

        def _resume() -> None:
            time.sleep(seconds)
            total = self._paused_total + (time.perf_counter() - self._pause_started)
            self._paused_total = total
            self._resume_event.set()

        self._pause_started = time.perf_counter()
        self._resume_event.clear()
        threading.Thread(target=_resume, daemon=True).start()


def test_polar_dir_maps_up_to_upper_half() -> None:
    """Given: 極座標 (r=127, θ=90°) / When: Direction へ直す / Then: 上半分になること。"""
    d = OperateMixin._polar_dir(Stick.LEFT, 127, 90)
    assert d.stick is Stick.LEFT
    # x は中央付近、y は Direction 規約で上が大きい（送信時に反転する）。
    assert abs(d.x - 128) <= 2
    assert d.y > 200


def test_polar_dir_clips_to_u8() -> None:
    """Given: 四角ゲートの斜め（r=180） / When: 直す / Then: 0〜255 に収まること。"""
    d = OperateMixin._polar_dir(Stick.LEFT, 180, 45)
    assert 0 <= d.x <= 255
    assert 0 <= d.y <= 255


def test_stick_move_sends_each_step_without_resending_from() -> None:
    """Given: 40ms の区間 / When: 再生する / Then: 2 刻みぶん送られ from は送り直さないこと。"""
    host = _Host()
    host.stick_move(Stick.LEFT, 0, 90, 127, 90, 40)
    # 20ms 刻みで 40ms → 2 回の input。from 自体は直前ブロック済みのため送らない。
    assert len(host.keys.sent) == 2
    # 最後の刻みは到達点そのもの。
    last = host.keys.sent[-1][0]
    expect = OperateMixin._polar_dir(Stick.LEFT, 127, 90)
    assert (last.x, last.y) == (expect.x, expect.y)
    assert host._motion_clock is not None


def test_stick_move_static_section_only_waits() -> None:
    """Given: 変化なし区間 / When: 再生する / Then: 何も送らず clock だけ進むこと。"""
    host = _Host()
    host.stick_move(Stick.LEFT, 127, 90, 127, 90, 60)
    assert host.keys.sent == []
    assert host._motion_clock is not None


def test_stick_move_chains_without_drift() -> None:
    """Given: 2 区間の連鎖 / When: 続けて再生する / Then: 終了予定が合計に一致すること。"""
    host = _Host()
    t0 = time.perf_counter()
    host.stick_move(Stick.LEFT, 0, 90, 127, 90, 40)
    host.stick_move(Stick.LEFT, 127, 90, 127, 450, 60)
    elapsed = host._motion_clock - t0
    # 合計 100ms 前後（連鎖引き継ぎで処理時間ぶん伸びない）。
    assert 0.09 <= elapsed <= 0.20


def test_stick_move_pause_does_not_fast_forward() -> None:
    """Given: 区間の途中で 1 秒停止 / When: 再開する / Then: 刻み間隔が保たれ合計が T+P になること。"""
    host = _Host()
    host.pause_for(1.0)
    started = time.perf_counter()
    host.stick_move(Stick.LEFT, 0, 90, 127, 450, 100)
    total = host._motion_clock - started
    # T(0.1) + P(1.0)。早送りなら 0.2 秒以下になる。
    assert 0.9 <= total <= 1.4
    # 刻みは 5 回（100ms / 20ms）。停止で刻みが飛ばされない。
    assert len(host.keys.sent) == 5


def test_stick_move2_sends_both_sides_in_one_call() -> None:
    """Given: L/R 同時 / When: 再生する / Then: 1 刻み 1 回の input に両方が載ること。"""
    host = _Host()
    host.stick_move2(
        Stick.LEFT,
        (127, 90),
        (127, 270),
        Stick.RIGHT,
        (127, 0),
        (127, 180),
        40,
    )
    assert len(host.keys.sent) == 2
    for items in host.keys.sent:
        assert len(items) == 2
        sticks = {d.stick for d in items}
        assert sticks == {Stick.LEFT, Stick.RIGHT}


def test_stick_move2_static_side_is_not_sent() -> None:
    """Given: R だけ静止 / When: 再生する / Then: 動く側だけ送られること。"""
    host = _Host()
    host.stick_move2(
        Stick.LEFT,
        (0, 90),
        (127, 90),
        Stick.RIGHT,
        (127, 0),
        (127, 0),
        40,
    )
    assert len(host.keys.sent) == 2
    for items in host.keys.sent:
        assert len(items) == 1
        assert items[0].stick is Stick.LEFT


def test_release_direction_covers_both_axes() -> None:
    """Given: 固定の斜め Direction(218, 218) / When: tilt を見る / Then: 両軸が対象になること。"""
    from core.Keys import Tilt

    d = Direction(Stick.LEFT, (218, 218))
    tilts = d.getTilting()
    assert Tilt.RIGHT in tilts
    assert Tilt.UP in tilts
    dr = Direction(Stick.RIGHT, (218, 218))
    rtilts = dr.getTilting()
    assert Tilt.R_RIGHT in rtilts
    assert Tilt.R_UP in rtilts
