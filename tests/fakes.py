"""テスト用の偽物置き場。実機なしで回すための線と命令と時計。

collect 対象にしない（test_ で始まらない）ため、ここには
検証本体を書かず、部品だけを置く。
"""

from typing import Any

from core import Transport


class FakeTransport(Transport.Transport):
    """線の偽物。書かれた行を溜めるだけ。開閉は常に成功扱い。"""

    name = "fake"
    capability = Transport.LEGACY_ROW

    def __init__(self) -> None:
        self.rows: list[str] = []
        self._opened = False
        self._listeners: list[Any] = []

    def open(
        self,
        portNum: int,
        portName: str = "",
        baudrate: int = 9600,
        **extra: Any,
    ) -> bool:
        _ = (portNum, portName, baudrate, extra)
        self._opened = True
        return True

    def close(self) -> None:
        self._opened = False

    def is_open(self) -> bool:
        return self._opened

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        _ = measure_perf
        self.rows.append(row)
        for func in list(self._listeners):
            func(row)

    def add_listener(self, func: Any) -> bool:
        self._listeners.append(func)
        return True

    def remove_listener(self, func: Any) -> None:
        if func in self._listeners:
            self._listeners.remove(func)


class NoSerTransport(Transport.Transport):
    """ser を持たない方式（HID/BLE/TCP 等）の振る舞いの確認用。"""

    name = "noser"
    capability = "MY_BLE"

    def send_row(self, row: str, measure_perf: bool = True) -> None:
        _ = (row, measure_perf)


class FakeThread:
    """スレッドの偽物。join したら抜けたことにする。"""

    def __init__(self, alive: bool = True) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        _ = timeout
        self._alive = False


class StuckThread(FakeThread):
    """抜けないスレッドの偽物。join しても生き続ける。"""

    def join(self, timeout: float | None = None) -> None:
        _ = timeout


class FakeCommand:
    """命令の偽物。start / end / thread / alive だけを持つ。"""

    NAME = "Fake"

    def __init__(
        self,
        start_result: Any = None,
        start_raises: BaseException | None = None,
        end_raises: BaseException | None = None,
        thread: FakeThread | None = None,
    ) -> None:
        self._start_result = start_result
        self._start_raises = start_raises
        self._end_raises = end_raises
        self.start_calls: list[tuple[Any, Any]] = []
        self.end_calls: list[Any] = []
        self.resumed = False
        self.thread: FakeThread | None = (
            thread if thread is not None else FakeThread(alive=False)
        )
        self.alive = True
        self._on_done: Any = None

    def start(self, ser: Any, on_done: Any) -> Any:
        self.start_calls.append((ser, on_done))
        if self._start_raises is not None:
            raise self._start_raises
        self._on_done = on_done
        return self._start_result

    def finish(self) -> None:
        """走り終える。後始末の合図を送る（_cleanup の代わり）。"""
        self.alive = False
        if self.thread is not None:
            self.thread._alive = False
        self._on_done()

    def end(self, ser: Any) -> None:
        self.end_calls.append(ser)
        if self._end_raises is not None:
            raise self._end_raises
        self.alive = False

    def resume(self) -> None:
        self.resumed = True

    def togglePause(self) -> bool:
        return False


class FakeSer:
    """送り先の有無だけを切り替える偽物（REQUIRES_SERIAL 判定用）。"""

    def __init__(self, opened: bool = True) -> None:
        self._opened = opened

    def isOpened(self) -> bool:
        return self._opened


class FakeScheduler:
    """時計の偽物。予約を溜めて、手で進める。"""

    def __init__(self) -> None:
        self.queued: list[tuple[int, int, Any]] = []
        self.cancelled: list[Any] = []
        self._next = 0

    def schedule(self, ms: int, fn: Any) -> int:
        self._next += 1
        self.queued.append((self._next, ms, fn))
        return self._next

    def cancel(self, watch_id: Any) -> None:
        self.cancelled.append(watch_id)

    def run_next(self) -> None:
        _, _, fn = self.queued.pop(0)
        fn()

    def run_all(self) -> None:
        while self.queued:
            self.run_next()
