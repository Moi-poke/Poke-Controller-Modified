"""GyroViewの描画負荷まわり（キュー上限・再描画回数・ヨーリセット）。"""

from __future__ import annotations

from typing import Any

import pytest


def _make_view(tk_root: Any) -> Any:
    from GyroView import GyroView

    return GyroView(tk_root, rate_range=500)


def test_gyro_view_bounded_queue_keeps_latest(tk_root: Any) -> None:
    """Given: 描画されないまま大量push / When: キューを見る / Then: 上限で最新側が残る。"""
    from GyroView import MAX_QUEUED_SAMPLES

    view = _make_view(tk_root)
    try:
        for i in range(MAX_QUEUED_SAMPLES * 10):
            view.push(float(i), 0.0, 0.0)
        assert len(view.q) == MAX_QUEUED_SAMPLES
        assert view.q[-1][0] == float(MAX_QUEUED_SAMPLES * 10 - 1)
        assert view.q[0][0] == float(MAX_QUEUED_SAMPLES * 9)
    finally:
        view.close()
        view.destroy()


def test_gyro_view_redraws_once_per_tick_not_per_sample(
    tk_root: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given: 1tickに複数サンプル / When: _tickを回す / Then: 再描画はtickにつき1回。"""
    from GyroView import MAX_QUEUED_SAMPLES

    view = _make_view(tk_root)
    calls: list[int] = []
    try:
        view.after_cancel(view._after_id)
        view._after_id = None
        monkeypatch.setattr(view, "_redraw", lambda: calls.append(1))
        # 既定は30fps（33ms）。詳細表示ONでも60fpsで描き直さない。
        assert view.interval == 33
        for i in range(MAX_QUEUED_SAMPLES * 2):
            view.push(10.0 + i, 0.0, 0.0)
        view._tick()
        assert calls == [1]
        # 入力が無いtickは描き直さない。
        view._tick()
        assert calls == [1]
    finally:
        view.close()
        view.destroy()


def test_gyro_view_reset_yaw_through_queue(tk_root: Any) -> None:
    """Given: ヨーが回った状態 / When: reset_yawを投入してtick / Then: ヨーが0へ戻る。"""
    view = _make_view(tk_root)
    try:
        view.after_cancel(view._after_id)
        view._after_id = None
        view.push(0.0, 0.0, 90.0, dt=0.1)
        view._tick()
        assert view.yaw != 0.0
        view.reset_yaw()
        view._tick()
        assert view.yaw == 0.0
    finally:
        view.close()
        view.destroy()
