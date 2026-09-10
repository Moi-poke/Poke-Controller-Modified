"""audio_service（所有・切替・終了手順）の検証。実機なしで回す。"""

from typing import Any

import pytest
from services import audio_service


class FakeCapture:
    def __init__(self) -> None:
        self.opened = ""
        self.monitor = False
        self.volume = 0.0
        self.closed = 0

    def openInput(self, device: Any) -> bool:
        if device == "ng":
            return False
        self.opened = str(device)
        return True

    def close(self) -> None:
        self.opened = ""
        self.monitor = False
        self.closed += 1

    def isOpened(self) -> bool:
        return self.opened != ""

    def setMonitorEnabled(self, on: bool, device: Any = None) -> bool:
        _ = device
        self.monitor = bool(on)
        return True

    def isMonitorEnabled(self) -> bool:
        return self.monitor

    def setMonitorVolume(self, volume: float) -> None:
        self.volume = float(volume)

    def selected_input(self) -> str:
        return self.opened


def make_service() -> tuple[audio_service.AudioService, FakeCapture, list[str]]:
    notes: list[str] = []
    svc = audio_service.AudioService(notify_user=notes.append)
    fake = FakeCapture()
    svc.capture = fake  # type: ignore[assignment]
    return svc, fake, notes


def test_open_close() -> None:
    svc, fake, _ = make_service()
    assert svc.open("mic") is True
    assert fake.opened == "mic"
    svc.close()
    assert fake.opened == ""


def test_open_failure_notifies() -> None:
    svc, _, notes = make_service()
    assert svc.open("ng") is False
    assert notes  # 利用者向けに1行出る


def test_monitor_needs_input() -> None:
    svc, fake, notes = make_service()
    assert svc.set_monitor(True, "sp", 0.5) is False
    assert notes
    assert svc.open("mic") is True
    assert svc.set_monitor(True, "sp", 0.5) is True
    assert fake.monitor is True
    assert fake.volume == 0.5


def test_shutdown_closes() -> None:
    svc, fake, _ = make_service()
    svc.open("mic")
    assert svc.shutdown() is True
    assert fake.closed >= 1


def test_monitor_output_failure_notifies() -> None:
    svc, fake, notes = make_service()
    assert svc.open("mic") is True

    def _fail(_on: bool, _device: Any = None) -> bool:
        return False

    fake.setMonitorEnabled = _fail  # type: ignore[method-assign]
    assert svc.set_monitor(True, "sp", 0.5) is False
    assert notes  # 利用者向けに1行出る


def test_probe_lists_display(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _, _ = make_service()
    monkeypatch.setattr(
        audio_service,
        "probe_details",
        lambda want: [(7, "Ok Mic", 12.0)] if want else [(3, "Ok Spk", 93.0)],
    )
    assert svc.probe_inputs() == ["7: Ok Mic [est. 12ms]"]
    assert svc.probe_outputs() == ["3: Ok Spk [est. 93ms]"]


def test_display_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _, _ = make_service()
    monkeypatch.setattr(
        audio_service,
        "device_entries",
        lambda want: [(7, "Ok Mic")] if want else [],
    )
    assert svc.list_inputs() == ["7: Ok Mic"]
    assert svc.list_outputs() == []
    assert svc.display_output("") == ""


def test_fastest_output_picks_min_est() -> None:
    svc, _, _ = make_service()
    svc._probe_cache = {
        False: [(3, "A", 90.0), (5, "B", 20.0)],
    }
    assert svc.fastest_output() == "5"
    svc._probe_cache = {}
    assert svc.fastest_output() == ""


def test_auto_input_guesses_board(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _, _ = make_service()
    monkeypatch.setattr(
        audio_service,
        "device_entries",
        lambda want: [(4, "HDMI/Line In (Live Gamer)")],
    )
    assert svc.auto_input("Live Gamer EXTREME 3") == "4"
    assert svc.auto_input("OBS Virtual Camera") == ""
