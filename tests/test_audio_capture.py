"""AudioCapture の可用性ガードとデバイス列挙の検証。実機なしで回す。"""

from typing import Any

import numpy as np
import pytest
from core import AudioCapture as AC


def test_unavailable_without_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AC, "_import_sounddevice", lambda: None)
    assert AC.audio_available() is False
    assert AC.list_input_devices() == []
    assert AC.list_output_devices() == []


class FakeStream:
    """sounddevice ストリームの偽物。fire() で受信を再現する。"""

    def __init__(self, **kwargs: Any) -> None:
        self.callback: Any = kwargs["callback"]
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def fire(self, data: np.ndarray) -> None:
        assert self.started
        self.callback(data, len(data), None, None)


def test_open_read_close() -> None:
    box: dict[str, FakeStream] = {}

    def factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        box["stream"] = stream
        return stream

    cap = AC.AudioCapture(input_factory=factory)
    assert cap.isOpened() is False
    assert cap.openInput("dummy") is True
    assert cap.isOpened() is True

    chunk = np.ones(1024, dtype=np.float32)
    box["stream"].fire(chunk)
    window = cap.readWindow(1.0)
    assert window is not None
    assert window.size >= 1024
    assert float(np.max(window)) == 1.0

    # 複製であること（外で書き換えても次回に影響しない）
    window[:] = 0.0
    again = cap.readWindow(1.0)
    assert again is not None
    assert float(np.max(again)) == 1.0

    cap.close()
    assert cap.isOpened() is False
    assert cap.readWindow(1.0) is None


def test_open_fails_without_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AC, "_import_sounddevice", lambda: None)
    cap = AC.AudioCapture()
    assert cap.openInput("dummy") is False
    assert cap.isOpened() is False


def test_record_collects_seconds() -> None:
    box: dict[str, FakeStream] = {}

    def factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        box["stream"] = stream
        return stream

    cap = AC.AudioCapture(input_factory=factory)
    assert cap.openInput("dummy") is True
    chunk = np.full(1024, 0.25, dtype=np.float32)
    for _ in range(5):
        box["stream"].fire(chunk)
    data = cap.record(0.05)
    assert data.size > 0
    assert float(np.mean(data)) == 0.25
    cap.close()


def test_monitor_toggle_and_volume() -> None:
    inbox: dict[str, FakeStream] = {}
    outbox: dict[str, FakeStream] = {}

    def in_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        inbox["stream"] = stream
        return stream

    def out_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        outbox["stream"] = stream
        return stream

    cap = AC.AudioCapture(input_factory=in_factory, output_factory=out_factory)
    assert cap.isMonitorEnabled() is False
    assert cap.openInput("dummy") is True
    # 出力先未指定でも既定出力で開く（device=None 透過）
    assert cap.setMonitorEnabled(True) is True
    assert cap.isMonitorEnabled() is True
    cap.setMonitorVolume(0.5)
    inbox["stream"].fire(np.full(1024, 0.4, dtype=np.float32))
    buf = np.zeros((1024, 1), dtype=np.float32)
    outbox["stream"].callback(buf, 1024, None, None)
    assert float(np.max(np.abs(buf))) > 0.0
    assert float(np.max(np.abs(buf))) <= 0.5 + 1e-6
    cap.setMonitorEnabled(False)
    assert cap.isMonitorEnabled() is False
    cap.close()


def test_apply_volume_clips() -> None:
    frames = np.full(4, 0.9, dtype=np.float32)
    doubled = AC.apply_volume(frames, 2.0)
    assert float(np.max(np.abs(doubled))) <= 1.0
    assert float(AC.apply_volume(frames, 0.0)[0]) == 0.0


def _monitor_pair(
    jitter: int = 4,
) -> tuple[AC.AudioCapture, dict[str, FakeStream]]:
    box: dict[str, FakeStream] = {}

    def in_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        box["in"] = stream
        return stream

    def out_factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        box["out"] = stream
        return stream

    cap = AC.AudioCapture(
        input_factory=in_factory,  # type: ignore[arg-type]
        output_factory=out_factory,  # type: ignore[arg-type]
        jitter_chunks=jitter,
    )
    assert cap.openInput("dummy") is True
    assert cap.setMonitorEnabled(True) is True
    return cap, box


def test_streams_use_low_latency_params() -> None:
    seen: dict[str, dict[str, Any]] = {}

    def in_factory(**kwargs: Any) -> FakeStream:
        seen["in"] = kwargs
        return FakeStream(**kwargs)

    def out_factory(**kwargs: Any) -> FakeStream:
        seen["out"] = kwargs
        return FakeStream(**kwargs)

    cap = AC.AudioCapture(
        input_factory=in_factory,  # type: ignore[arg-type]
        output_factory=out_factory,  # type: ignore[arg-type]
    )
    assert cap.openInput(None) is True
    assert cap.setMonitorEnabled(True) is True
    assert seen["in"]["blocksize"] == 512
    assert seen["in"]["latency"] == "low"
    assert seen["out"]["blocksize"] == 512
    assert seen["out"]["latency"] == "low"
    cap.close()


def test_monitor_jitter_absorbs() -> None:
    cap, box = _monitor_pair(jitter=4)
    for _ in range(6):
        box["in"].fire(np.full(1024, 0.4, dtype=np.float32))
    buf = np.zeros((1024, 1), dtype=np.float32)
    box["out"].callback(buf, 1024, None, None)
    assert float(np.max(np.abs(buf))) > 0.0
    assert cap.getMonitorStats()["silence"] == 0
    cap.close()


def test_monitor_starved_silence_and_stats() -> None:
    cap, box = _monitor_pair(jitter=4)
    buf = np.zeros((1024, 1), dtype=np.float32)
    box["out"].callback(buf, 1024, None, None)
    assert float(np.max(np.abs(buf))) == 0.0
    assert cap.getMonitorStats()["silence"] == 1
    cap.close()


def test_monitor_drops_keep_latest() -> None:
    cap, box = _monitor_pair(jitter=2)
    for i in range(5):
        box["in"].fire(np.full(1024, 0.1 * (i + 1), dtype=np.float32))
    stats = cap.getMonitorStats()
    assert stats["drops"] >= 1
    buf = np.zeros((1024, 1), dtype=np.float32)
    box["out"].callback(buf, 1024, None, None)
    # 順次再生のため、残ったうち最も古い物（0.4×音量0.8=0.32）が出る
    assert abs(float(np.max(buf)) - 0.32) < 1e-6
    cap.close()


class FakeSD:
    """sounddevice モジュールの偽物。列挙と試し開きの成否を再現する。"""

    FAIL = {1, 3}

    DEVICES = [
        {"name": "Good Mic", "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Rate Mic", "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Good Spk", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "KS Spk", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Dup", "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Dup", "max_input_channels": 2, "max_output_channels": 0},
    ]

    @staticmethod
    def query_devices() -> list[dict[str, object]]:
        return FakeSD.DEVICES

    class _Stream:
        def __init__(self, **kwargs: Any) -> None:
            if int(kwargs.get("device", -1)) in FakeSD.FAIL:
                raise RuntimeError("open fail")
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class InputStream(_Stream):
        pass

    class OutputStream(_Stream):
        pass


def test_probe_openable_filters() -> None:
    real = AC._import_sounddevice
    AC._import_sounddevice = lambda: FakeSD  # type: ignore[assignment]
    try:
        assert AC.probe_openable(True) == [(0, "Good Mic"), (4, "Dup"), (5, "Dup")]
        assert AC.probe_openable(False) == [(2, "Good Spk")]
    finally:
        AC._import_sounddevice = real  # type: ignore[assignment]


def test_display_parse_roundtrip() -> None:
    assert AC.display_entries([(23, "Foo"), (4, "Dup")]) == ["23: Foo", "4: Dup"]
    assert AC.parse_display("23: Foo") == 23
    assert AC.parse_display("nope") is None
    assert AC.parse_display("") is None


def test_resolve_device() -> None:
    real = AC._import_sounddevice
    AC._import_sounddevice = lambda: FakeSD  # type: ignore[assignment]
    try:
        assert AC.resolve_device(True, None) is None
        assert AC.resolve_device(True, "") is None
        assert AC.resolve_device(True, 5) == 5
        assert AC.resolve_device(True, "5") == 5
        # 同名重複は先頭番号へ一本化（名前指定の曖昧さを潰す）
        assert AC.resolve_device(True, "Dup") == 4
        # 未知の名前はそのまま渡す（open時の成否に任せる後方互換）
        assert AC.resolve_device(True, "nope") == "nope"
    finally:
        AC._import_sounddevice = real  # type: ignore[assignment]


def test_device_entries_indexed() -> None:
    real = AC._import_sounddevice
    AC._import_sounddevice = lambda: FakeSD  # type: ignore[assignment]
    try:
        assert AC.device_entries(True)[0] == (0, "Good Mic")
        assert len(AC.device_entries(False)) == 2
        assert AC.display_for(True, "0") == "0: Good Mic"
        assert AC.display_for(True, "Dup") == "4: Dup"
        assert AC.display_for(True, "") == ""
        assert AC.display_for(True, "unknown") == "unknown"
    finally:
        AC._import_sounddevice = real  # type: ignore[assignment]
