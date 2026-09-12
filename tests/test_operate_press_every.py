"""pressEvery（開始間隔＋保持幅）の検証。実機なしで回す。"""

import threading
import time
from typing import Any

from core.CommandOperate import OperateMixin


class _Keys:
    """input/inputEndの時刻を記録する偽物。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, float]] = []

    def input(self, btns: Any) -> None:
        _ = btns
        self.events.append(("in", time.perf_counter()))

    def inputEnd(self, btns: Any) -> None:
        _ = btns
        self.events.append(("end", time.perf_counter()))


class _Cmd(OperateMixin):
    def __init__(self) -> None:
        self.keys: Any = _Keys()
        self.alive = True
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._paused_total = 0.0
        self._pause_started = 0.0

    def _cleanup(self, *_a: Any, **_k: Any) -> None:
        return None

    def _pausedSeconds(self) -> float:
        return 0.0


def test_press_every_keeps_interval() -> None:
    """開始間隔どおりに戻り、保持幅どおりに離す。"""
    cmd = _Cmd()
    t0 = time.perf_counter()
    cmd.pressEvery("A", 0.3, 0.1)
    total = time.perf_counter() - t0
    kinds = [k for k, _ in cmd.keys.events]
    assert kinds == ["in", "end"]
    hold = cmd.keys.events[1][1] - cmd.keys.events[0][1]
    assert abs(hold - 0.1) < 0.06
    assert abs(total - 0.3) < 0.08


def test_press_every_clamps_duration_over_interval() -> None:
    """保持が間隔を超えたら丸める（固まらない・負待ちしない）。"""
    cmd = _Cmd()
    t0 = time.perf_counter()
    cmd.pressEvery("A", 0.1, 0.5)
    total = time.perf_counter() - t0
    assert total < 0.5
    assert [k for k, _ in cmd.keys.events] == ["in", "end"]
