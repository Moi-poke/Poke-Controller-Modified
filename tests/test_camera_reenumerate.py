"""カメラ一覧の再列挙と個体識別の契約（clr は差し替え、実機なし）。

一覧は起動時に1回しか作られず、カメラタブの「再読み込み」は
``openCamera()`` だけを呼んでいた。後から挿したカメラが出ないため、
再読み込みで列挙もやり直す。同名のキャプチャボードは DevicePath から
作る識別子（``i: 名前 [key]``）で見分け、保存 key が見つからないときも
消さない（一時的に抜けただけの個体が戻ったとき照合できるようにする）。
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
import ui.camera_panel as camera_panel
from WindowUtils import deviceKey
from ui.camera_panel import CameraPanelMixin


class _Device:
    def __init__(self, name: str, path: str) -> None:
        self.Name = name
        self.DevicePath = path


class _DsDevice:
    devices: list[_Device] = []

    @classmethod
    def GetDevicesOfCat(cls, _category: Any) -> list[_Device]:
        return list(cls.devices)


class _FilterCategory:
    VideoInputDevice = 0


class _Var:
    def __init__(self, value: Any) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value


class _Combo:
    def __init__(self) -> None:
        self.values: list[str] = []
        self.current_index = -1

    def __setitem__(self, key: str, value: Any) -> None:
        if key != "values":
            raise KeyError(key)
        self.values = list(value)

    def current(self, index: int | None = None) -> int:
        if index is not None:
            self.current_index = int(index)
        return self.current_index

    def get(self) -> str:
        if 0 <= self.current_index < len(self.values):
            return self.values[self.current_index]
        return ""


class _Entry:
    def __init__(self) -> None:
        self.binds: list[str] = []

    def bind(self, sequence: str, _func: Any, add: str | None = None) -> None:
        self.binds.append(sequence)


class _Panel(CameraPanelMixin):
    os_name = "Windows"

    def __init__(self, *, camera_id: int = 0, key: str = "") -> None:
        self.camera_id = _Var(camera_id)
        self.camera_key = _Var(key)
        self.camera_entry = _Entry()
        self.Camera_Name = _Combo()
        self.camera_dic: dict[int, str] | None = None
        self.camera_keys: dict[int, str] = {}
        self._camera_labels: list[str] = []


def _install_windows(monkeypatch: pytest.MonkeyPatch, devices: list[_Device]) -> None:
    fake_clr = types.ModuleType("clr")
    fake_clr.AddReference = lambda _path: None  # type: ignore[attr-defined]
    fake_directshow = types.ModuleType("DirectShowLib")
    fake_directshow.DsDevice = _DsDevice  # type: ignore[attr-defined]
    fake_directshow.FilterCategory = _FilterCategory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "clr", fake_clr)
    monkeypatch.setitem(sys.modules, "DirectShowLib", fake_directshow)
    # 実環境の PYTHONNET_RUNTIME を書き換えない（ヘルパー自体は別テスト）。
    monkeypatch.setattr(camera_panel, "_use_coreclr_for_pythonnet", lambda: None)
    _DsDevice.devices = list(devices)


def test_reenumeration_follows_the_saved_device_when_order_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_a = "usb\\path-a"
    _install_windows(monkeypatch, [_Device("Capture", path_a)])
    panel = _Panel(camera_id=0, key=deviceKey(path_a))
    panel.locateCameraCmbbox()
    assert panel.camera_id.get() == 0

    # 新しいボードが先頭に入り、保存した個体は 1 番へずれた。
    _DsDevice.devices = [
        _Device("Capture", "usb\\path-b"),
        _Device("Capture", path_a),
    ]
    panel.locateCameraCmbbox()

    assert panel.camera_id.get() == 1
    assert panel.camera_key.get() == deviceKey(path_a)
    assert panel.Camera_Name.current() == 1


def test_unknown_saved_key_is_preserved_on_index_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一時的に抜けている個体の識別子を、別ボードの key で上書きしない。"""
    saved = deviceKey("usb\\missing")
    _install_windows(
        monkeypatch,
        [_Device("Capture", "usb\\path-a"), _Device("Capture", "usb\\path-b")],
    )
    panel = _Panel(camera_id=1, key=saved)

    panel.locateCameraCmbbox()

    assert panel.camera_id.get() == 1
    assert panel.camera_key.get() == saved


def test_unknown_saved_key_prompts_the_user_to_reselect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = deviceKey("usb\\missing")
    _install_windows(monkeypatch, [_Device("Capture", "usb\\path-a")])
    panel = _Panel(camera_id=0, key=saved)

    panel.locateCameraCmbbox()

    out = capsys.readouterr().out
    assert "選び直" in out
    assert saved in out


def test_two_same_name_boards_get_distinct_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ表示名でも識別子で区別でき、保存 key の照合に使える。"""
    _install_windows(
        monkeypatch,
        [_Device("Capture", "usb\\path-a"), _Device("Capture", "usb\\path-b")],
    )
    panel = _Panel(camera_id=0)

    panel.locateCameraCmbbox()

    assert panel.camera_dic is not None
    assert panel.camera_dic[0] == panel.camera_dic[1] == "Capture"
    key_a = panel.camera_keys[0]
    key_b = panel.camera_keys[1]
    assert key_a and key_b
    assert key_a != key_b
    assert key_a in panel._camera_labels[0]
    assert key_b in panel._camera_labels[1]


class _Tip:
    def __init__(self) -> None:
        self.text = ""


def test_tooltip_shows_the_full_label_with_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """幅の上限で key が見切れても、吹き出しでは全文を読める。"""
    path = "usb\\very-long-device-path"
    _install_windows(monkeypatch, [_Device("Capture Board", path)])
    panel = _Panel(camera_id=0)
    panel._camera_name_tooltip = _Tip()

    panel.locateCameraCmbbox()

    expected = panel.Camera_Name.get()
    assert deviceKey(path) in expected
    assert panel._camera_name_tooltip.text == expected


def test_first_run_records_the_key_of_the_selected_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_windows(
        monkeypatch,
        [_Device("Capture", "usb\\path-a"), _Device("Capture", "usb\\path-b")],
    )
    panel = _Panel(camera_id=1, key="")

    panel.locateCameraCmbbox()

    assert panel.camera_id.get() == 1
    assert panel.camera_key.get() == deviceKey("usb\\path-b")


def test_no_devices_keeps_the_saved_key_for_a_later_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = deviceKey("usb\\path-a")
    _install_windows(monkeypatch, [])
    panel = _Panel(camera_id=3, key=saved)

    panel.locateCameraCmbbox()

    assert panel.camera_dic == {0: "Disable"}
    assert panel.camera_id.get() == 0
    assert panel.camera_key.get() == saved


class _ReloadPanel(CameraPanelMixin):
    def __init__(self, *, fail: bool = False) -> None:
        self.os_name = "Windows"
        self.fail = fail
        self.calls: list[str] = []

    def locateCameraCmbbox(self) -> None:
        self.calls.append("enumerate")
        if self.fail:
            raise RuntimeError("no DirectShow")

    def openCamera(self) -> bool:
        self.calls.append("open")
        return True


class _RealReloadPanel(_Panel):
    """実の locateCameraCmbbox と、開いたことだけ記録する openCamera。"""

    def __init__(self, *, camera_id: int = 0, key: str = "") -> None:
        super().__init__(camera_id=camera_id, key=key)
        self.opened = 0

    def openCamera(self) -> bool:
        self.opened += 1
        return True


def test_reload_button_reenumerates_the_device_list_then_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path_a = "usb\\path-a"
    _install_windows(monkeypatch, [_Device("Capture", path_a)])
    panel = _RealReloadPanel(camera_id=0, key=deviceKey(path_a))
    panel.locateCameraCmbbox()

    # 後から挿したボードが先頭へ入り、保存した個体は 1 番へずれる。
    _DsDevice.devices = [
        _Device("New", "usb\\path-b"),
        _Device("Capture", path_a),
    ]
    panel.reloadCameraFromTab()

    assert panel.opened == 1
    assert panel.camera_dic == {0: "New", 1: "Capture", 2: "Disable"}
    assert panel.camera_id.get() == 1
    assert panel.camera_key.get() == deviceKey(path_a)
    assert "0: New" in panel.Camera_Name.values[0]


def test_reload_reenumerates_before_opening() -> None:
    panel = _ReloadPanel()

    panel.reloadCameraFromTab()

    assert panel.calls == ["enumerate", "open"]


def test_reload_still_opens_when_enumeration_fails() -> None:
    panel = _ReloadPanel(fail=True)

    panel.reloadCameraFromTab()

    assert panel.calls == ["enumerate", "open"]
