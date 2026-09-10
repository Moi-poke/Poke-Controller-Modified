"""音声パネルの裏実行（録音の固まり・旗の競合）の検証。実機なし。"""

from __future__ import annotations

import threading
import time
from typing import Any

from ui.audio_panel import AudioPanelMixin


class FakeAfterRoot:
    """tk の after/after_cancel の偽物（手で進める）。"""

    def __init__(self) -> None:
        self.queued: dict[int, Any] = {}
        self._next = 0

    def after(self, ms: int, func: Any) -> int:
        _ = ms
        self._next += 1
        self.queued[self._next] = func
        return self._next

    def after_cancel(self, after_id: int) -> None:
        self.queued.pop(int(after_id), None)

    def run_all(self) -> None:
        while self.queued:
            _, func = self.queued.popitem()
            func()


class FakeButton:
    def __init__(self) -> None:
        self.disabled = False

    def state(self, spec: Any) -> None:
        for item in list(spec):
            if item == "disabled":
                self.disabled = True
            elif item == "!disabled":
                self.disabled = False


class SlowCapture:
    """record() が遅い取込口の偽物（GUI固まりの再現用）。"""

    def __init__(self, delay: float = 0.5) -> None:
        self.delay = delay
        self.record_threads: list[str] = []
        self._opened = True

    def isOpened(self) -> bool:
        return self._opened

    def record(self, seconds: float) -> Any:
        import numpy as np

        self.record_threads.append(threading.current_thread().name)
        time.sleep(self.delay)
        n = max(1, int(44100 * float(seconds)))
        return np.zeros(n, dtype="float32")


class FakeAudioService:
    def __init__(self, capture: Any) -> None:
        self.capture = capture


class PanelHarness(AudioPanelMixin):
    def __init__(self, capture: Any) -> None:
        self.root: Any = FakeAfterRoot()
        self.audio_service: Any = FakeAudioService(capture)
        self.audio_record_button: Any = FakeButton()
        self.printed: list[str] = []


def test_5_record_does_not_block_gui(tmp_path: Any) -> None:
    """録音試験は裏で回し、GUIは固まらない（計測と同じ流儀）。"""
    import builtins
    import pathlib

    import WindowUtils

    cap = SlowCapture(delay=0.5)
    panel = PanelHarness(cap)
    real_app_dir = WindowUtils.APP_DIR
    WindowUtils.APP_DIR = str(tmp_path)  # type: ignore[assignment]
    real_print = builtins.print
    printed: list[str] = []
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))  # type: ignore[assignment]
    try:
        t0 = time.perf_counter()
        panel.recordAudioTest()
        dt = time.perf_counter() - t0
        # 裏実行のため即戻る（表で3秒待たない）
        assert dt < 0.20
        # 裏で録っている間も表は生きている（旗・予約が進む）
        assert getattr(panel, "_record_thread", None) is not None
        assert panel._record_thread.is_alive()  # type: ignore[attr-defined]
        # メーター相当の表操作が詰まらない（after予約ができる）
        rid = panel.root.after(10, lambda: None)
        assert rid in panel.root.queued
        panel.root.after_cancel(rid)
    finally:
        builtins.print = real_print  # type: ignore[assignment]
    # 裏が終わるまで待って後始末を流す
    panel._record_thread.join(timeout=5.0)  # type: ignore[attr-defined]
    assert not panel._record_thread.is_alive()  # type: ignore[attr-defined]
    # 録音は裏糸で回った（表糸ではない）
    assert cap.record_threads and all(
        name != threading.current_thread().name for name in cap.record_threads
    )
    # 後始末は表糸で流せる（printは表で出す）
    real_print2 = builtins.print
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))  # type: ignore[assignment]
    try:
        panel.root.run_all()
    finally:
        builtins.print = real_print2  # type: ignore[assignment]
        WindowUtils.APP_DIR = real_app_dir  # type: ignore[assignment]
    assert list(pathlib.Path(tmp_path, "AudioClips").glob("test_*.wav"))
    assert any("保存しました" in line for line in printed)


def test_5_record_double_press_ignored(tmp_path: Any) -> None:
    """録音中の二度押しは無視する（多重起動しない）。"""
    import builtins

    import WindowUtils

    cap = SlowCapture(delay=0.5)
    panel = PanelHarness(cap)
    real_app_dir = WindowUtils.APP_DIR
    WindowUtils.APP_DIR = str(tmp_path)  # type: ignore[assignment]
    real_print = builtins.print
    builtins.print = lambda *a, **k: None  # type: ignore[assignment]
    try:
        panel.recordAudioTest()
        first = getattr(panel, "_record_thread", None)
        assert first is not None
        panel.recordAudioTest()
        second = getattr(panel, "_record_thread", None)
        assert first is second
    finally:
        builtins.print = real_print  # type: ignore[assignment]
    assert first is not None
    first.join(timeout=5.0)
    panel.root.run_all()
    WindowUtils.APP_DIR = real_app_dir  # type: ignore[assignment]


def test_6_record_done_is_event(tmp_path: Any) -> None:
    """録音の完了旗は Event で渡す（bool直書きの競合を避ける）。"""
    import threading as th

    import WindowUtils

    cap = SlowCapture(delay=0.01)
    panel = PanelHarness(cap)
    real_app_dir = WindowUtils.APP_DIR
    WindowUtils.APP_DIR = str(tmp_path)  # type: ignore[assignment]
    try:
        # 実装が Event を使うこと（bool直書きは不可視化の競合になる）
        panel.recordAudioTest()
        record_done = getattr(panel, "_record_done", None)
        assert isinstance(record_done, th.Event)
        panel._record_thread.join(timeout=5.0)  # type: ignore[attr-defined]
        panel.root.run_all()
    finally:
        WindowUtils.APP_DIR = real_app_dir  # type: ignore[assignment]
