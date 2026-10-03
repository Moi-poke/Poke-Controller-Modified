"""Contracts for the display settings dialog and the camera row it replaced.

FPS と描画方式は一度決めたら滅多に変えないので、カメラ欄の常設コンボから
表示設定ダイアログへ移した。ダイアログは今の値を見せ、適用でそのまま返す。
カメラ欄の側は、渡された値が変わったときだけ従来の適用処理（applyFps /
applyRenderer）を呼ぶ。Camera ID 欄はカメラ名を取れない環境でだけ出す。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator
from typing import Any

import WindowUtils
import pytest
from ui.camera_panel import CameraPanelMixin
from ui.display_settings import DisplaySettingsDialog


@pytest.fixture
def dialog(tk_root: tk.Tk) -> Iterator[tuple[DisplaySettingsDialog, list[Any]]]:
    applied: list[Any] = []
    dlg = DisplaySettingsDialog(
        tk_root,
        mode="fit",
        size="640x360",
        on_apply=lambda *args: applied.append(args),
        color="",
        fps="30",
        renderer="photo",
    )
    try:
        yield dlg, applied
    finally:
        dlg.destroy()


def test_the_dialog_shows_the_current_fps_and_renderer_as_readonly_choices(
    dialog: tuple[DisplaySettingsDialog, list[Any]],
) -> None:
    # Then: 今の値が選ばれ、候補は WindowUtils の表そのまま（手入力不可）。
    dlg, _applied = dialog
    assert dlg.fps_cb.get() == "30"
    assert dlg.renderer_cb.get() == "photo"
    assert [str(v) for v in dlg.fps_cb["values"]] == [
        str(v) for v in WindowUtils.FPS_VALUES
    ]
    assert list(dlg.renderer_cb["values"]) == WindowUtils.RENDERER_VALUES
    assert str(dlg.fps_cb.cget("state")) == "readonly"
    assert str(dlg.renderer_cb.cget("state")) == "readonly"


def test_apply_returns_fps_and_renderer_with_the_display_choices(
    dialog: tuple[DisplaySettingsDialog, list[Any]],
) -> None:
    # When: FPS and renderer are changed and applied.
    dlg, applied = dialog
    dlg.fps_var.set("60")
    dlg.renderer_var.set("gdi")
    dlg._apply()

    # Then: (mode, size, color, fps, renderer) の順で 1 回だけ返る。
    assert applied == [("fit", "640x360", "", "60", "gdi")]


def test_unknown_fps_and_renderer_fall_back_to_the_defaults(tk_root: tk.Tk) -> None:
    # Given: broken values from a hand-edited ini.
    dlg = DisplaySettingsDialog(
        tk_root,
        mode="fit",
        size="640x360",
        on_apply=lambda *args: None,
        fps="999",
        renderer="vulkan",
    )
    try:
        # Then: 画面に嘘を出さない（camera_panel の _current_* と同じ既定）。
        assert dlg.fps_var.get() == "45"
        assert dlg.renderer_var.get() == "auto"
    finally:
        dlg.destroy()


class _Var:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Panel:
    """applyDisplaySettings が触る口だけを持つ偽物。"""

    applyDisplaySettings = CameraPanelMixin.applyDisplaySettings

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.show_mode = _Var("fit")
        self.show_size = _Var("640x360")
        self.fps = _Var("45")
        self.renderer = _Var("auto")

    def applyFps(self) -> None:
        self.calls.append(f"fps={self.fps.get()}")

    def applyRenderer(self) -> None:
        self.calls.append(f"renderer={self.renderer.get()}")

    def applyProfileColor(self, color: str) -> None:
        self.calls.append(f"color={color}")

    def _apply_preview_layout(self) -> None:
        pass

    def _apply_content_minsize(self) -> None:
        pass

    def _on_setting_changed(self) -> None:
        pass


def test_changed_fps_and_renderer_go_through_the_existing_apply_paths() -> None:
    # When: the dialog returns a new fps and renderer.
    panel = _Panel()
    panel.applyDisplaySettings("fit", "640x360", "", "60", "gdi")

    # Then: カメラ・プレビューへの反映と保存は従来の applyFps / applyRenderer。
    assert "fps=60" in panel.calls
    assert "renderer=gdi" in panel.calls


def test_unchanged_fps_and_renderer_are_not_reapplied() -> None:
    # When: OK is pressed without touching fps or renderer.
    panel = _Panel()
    panel.applyDisplaySettings("fit", "640x360", "", "45", "auto")

    # Then: 「再起動後に反映」の案内やカメラの設定し直しを無駄に出さない。
    assert not [c for c in panel.calls if c.startswith(("fps=", "renderer="))]


class _Widget:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def grid_remove(self) -> None:
        self.calls.append("grid_remove")

    def config(self, **kw: Any) -> None:
        self.calls.append(f"config {kw}")

    def bind(self, *args: Any, **kw: Any) -> None:
        pass


class _NamePanel:
    _setup_camera_name = CameraPanelMixin._setup_camera_name
    _bindCameraEntry = CameraPanelMixin._bindCameraEntry

    def __init__(self, os_name: str, names_ok: bool) -> None:
        self.os_name = os_name
        self._names_ok = names_ok
        self.camera_id_label = _Widget()
        self.camera_entry = _Widget()
        self.Camera_Name = _Widget()
        self.camera_name_fromDLL = _Var("")

    def _onCameraEntryApplied(self, *_event: Any) -> None:
        pass

    def locateCameraCmbbox(self) -> None:
        if not self._names_ok:
            raise RuntimeError("no DirectShow")


def test_the_camera_id_row_is_hidden_when_camera_names_are_listed() -> None:
    # When: the camera names were listed (Windows / macOS).
    panel = _NamePanel("Windows", names_ok=True)
    panel._setup_camera_name()

    # Then: 名前に番号が入っているので、ID 欄は二重表示になる。隠す。
    assert "grid_remove" in panel.camera_id_label.calls
    assert "grid_remove" in panel.camera_entry.calls


@pytest.mark.parametrize(("os_name", "names_ok"), [("Linux", True), ("Windows", False)])
def test_the_camera_id_row_stays_when_names_cannot_be_listed(
    os_name: str, names_ok: bool
) -> None:
    # When: names cannot be listed (Linux, or the listing failed).
    panel = _NamePanel(os_name, names_ok=names_ok)
    panel._setup_camera_name()

    # Then: ID を打つ以外に選ぶ手段が無いので、ID 欄を残す。
    assert "grid_remove" not in panel.camera_id_label.calls
    assert "grid_remove" not in panel.camera_entry.calls
