"""入力の開き直しでモニタが死ぬ不整合の検証。実機なし。

原因: openInput は先頭で close() → _stop_output() するため、
入力を選び直す／リロードすると出力流が止まる。なのに Monitor の
チェック表示は ON のまま残り、無音なのに ON という不整合になる。
期待: 開き直し成功＋Monitor ON 表示なら再開する。失敗時は表示を実態へ戻す。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ui.audio_panel import AudioPanelMixin


class Var:
    """tk 変数の偽物（get/set だけ）。"""

    def __init__(self, value: Any) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value


class FakeAudioService:
    """reopen＝開き直し、set_monitor＝再生のON/OFF。呼出しを記録する。"""

    def __init__(self) -> None:
        self.reopened: list[str] = []
        self.monitor_calls: list[tuple[bool, str, float]] = []
        self.reopen_ok = True
        self.monitor_ok = True

    def reopen(self, spec: str) -> bool:
        self.reopened.append(spec)
        return self.reopen_ok

    def set_monitor(self, on: bool, spec: str, vol: float) -> bool:
        self.monitor_calls.append((on, spec, vol))
        return self.monitor_ok


class PanelHarness(AudioPanelMixin):
    def __init__(self, service: FakeAudioService, monitor_on: bool) -> None:
        self.audio_service: Any = service
        self.audio_input_name: Any = Var("22: HDMI/Line In")
        self.audio_output_name: Any = Var("8: スピーカー")
        self.audio_volume: Any = Var(0.8)
        self.audio_monitor: Any = Var(monitor_on)
        self.settings: Any = SimpleNamespace(audio_input=Var(""))
        self.setting_changed: list[bool] = []
        self._on_setting_changed: Any = lambda: self.setting_changed.append(True)


def _panel(monitor_on: bool) -> tuple[PanelHarness, FakeAudioService]:
    service = FakeAudioService()
    return PanelHarness(service, monitor_on), service


def test_reopen_restarts_monitor_when_shown_on() -> None:
    """開き直し成功＋Monitor ON 表示なら、出力を開き直す（再開）。"""
    panel, service = _panel(monitor_on=True)
    panel._onAudioInputSelected()
    assert service.reopened == ["22"]
    assert service.monitor_calls == [(True, "8", 0.8)]
    assert bool(panel.audio_monitor.get()) is True


def test_reopen_leaves_monitor_stopped_when_shown_off() -> None:
    """Monitor OFF 表示なら、再開しない（余計な出力を開かない）。"""
    panel, service = _panel(monitor_on=False)
    panel._onAudioInputSelected()
    assert service.reopened == ["22"]
    assert service.monitor_calls == []
    assert bool(panel.audio_monitor.get()) is False


def test_restart_failure_unchecks_monitor() -> None:
    """再開に失敗したら、表示を実態（OFF）へ戻す。"""
    panel, service = _panel(monitor_on=True)
    service.monitor_ok = False
    panel._onAudioInputSelected()
    assert service.monitor_calls == [(True, "8", 0.8)]
    assert bool(panel.audio_monitor.get()) is False


def test_reopen_failure_keeps_monitor_intent() -> None:
    """開き直し失敗時は意図（ON表示）を残し、後で直れば自己回復する。"""
    panel, service = _panel(monitor_on=True)
    service.reopen_ok = False
    panel._onAudioInputSelected()
    assert service.reopened == ["22"]
    assert service.monitor_calls == []
    assert bool(panel.audio_monitor.get()) is True


class FakeCb:
    """コンボの偽物（["values"] 代入だけ受ける）。"""

    def __init__(self) -> None:
        self.values: list[str] = []

    def __setitem__(self, key: str, value: list[str]) -> None:
        assert key == "values"
        self.values = value

    def set(self, value: str) -> None:
        _ = value


class FakeAfterRoot:
    """after/after_cancel の偽物（予約するだけで進めない）。"""

    def after(self, ms: int, func: Any) -> int:
        _ = (ms, func)
        return 1

    def after_cancel(self, after_id: int) -> None:
        _ = after_id


class StartupService(FakeAudioService):
    """起動経路（列挙・自動選択・表示名・推定）に答える偽物。"""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[str] = []
        self.open_ok = True

    def open(self, spec: str) -> bool:
        self.opened.append(spec)
        return self.open_ok

    def auto_input(self, camera: str) -> str:
        _ = camera
        return ""

    def list_inputs(self) -> list[str]:
        return ["22: HDMI/Line In"]

    def list_outputs(self) -> list[str]:
        return ["8: スピーカー"]

    def refresh_probe_cache(self) -> None:
        return None

    def cached_inputs(self) -> list[str]:
        return ["22: HDMI/Line In"]

    def cached_outputs(self) -> list[str]:
        return ["8: スピーカー"]

    def display_input(self, spec: str) -> str:
        return "22: HDMI/Line In" if spec == "22" else spec

    def display_output(self, spec: str) -> str:
        return "8: スピーカー" if spec == "8" else spec

    def pair_est(self, in_spec: str, out_spec: str) -> tuple[float, float]:
        _ = (in_spec, out_spec)
        return (12.0, 93.0)


class StartupHarness(AudioPanelMixin):
    def __init__(self, service: StartupService, monitor_on: bool) -> None:
        self.root: Any = FakeAfterRoot()
        self.audio_service: Any = service
        self.audio_input_name: Any = Var("")
        self.audio_output_name: Any = Var("")
        self.audio_volume: Any = Var(0.0)
        self.audio_monitor: Any = Var(False)
        self.audio_level: Any = Var("--")
        self.audio_latency: Any = Var("")
        self.audio_input_cb: Any = FakeCb()
        self.audio_output_cb: Any = FakeCb()
        self.camera_name_fromDLL: Any = Var("Live Gamer")
        self.settings: Any = SimpleNamespace(
            audio_input=Var("22"),
            audio_output=Var("8"),
            audio_monitor_enabled=Var(monitor_on),
            audio_monitor_volume=Var(0.8),
        )
        self._on_setting_changed: Any = lambda: None


def _startup(monitor_on: bool) -> tuple[StartupHarness, StartupService]:
    service = StartupService()
    return StartupHarness(service, monitor_on), service


def test_startup_starts_monitor_when_enabled() -> None:
    """起動時に設定ONなら、入力を開いた後にモニタも開始する。"""
    panel, service = _startup(monitor_on=True)
    panel._start_audio()
    assert service.opened == ["22"]
    assert service.monitor_calls == [(True, "8", 0.8)]
    assert bool(panel.audio_monitor.get()) is True


def test_startup_skips_monitor_when_disabled() -> None:
    """起動時に設定OFFなら、モニタは開始しない。"""
    panel, service = _startup(monitor_on=False)
    panel._start_audio()
    assert service.opened == ["22"]
    assert service.monitor_calls == []
    assert bool(panel.audio_monitor.get()) is False


def test_startup_open_failure_keeps_intent() -> None:
    """起動時に入力が開けなくても意図は残し、後で直れば自己回復する。"""
    panel, service = _startup(monitor_on=True)
    service.open_ok = False
    panel._start_audio()
    assert service.opened == ["22"]
    assert service.monitor_calls == []
    assert bool(panel.audio_monitor.get()) is True


def test_startup_monitor_failure_unchecks() -> None:
    """起動時のモニタ開始に失敗したら、表示を実態へ戻す。"""
    panel, service = _startup(monitor_on=True)
    service.monitor_ok = False
    panel._start_audio()
    assert service.monitor_calls == [(True, "8", 0.8)]
    assert bool(panel.audio_monitor.get()) is False
