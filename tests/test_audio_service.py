"""audio_service（所有・切替・終了手順）の検証。実機なしで回す。"""

from typing import Any

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
