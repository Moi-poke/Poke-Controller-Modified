"""16:9固定でWMに無視されたgeometry要求を実Tkで確認する."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator

import pytest
from Menubar import PokeController_Menubar
from Window import PokeControllerApp
from loguru import logger

LOCK_LABEL = "16:9 に固定"


def _drain(root: tk.Tk) -> None:
    root.update()
    root.update_idletasks()


def _invoke_lock(menu: PokeController_Menubar) -> None:
    for index in range(menu.menu_view.index("end") + 1):
        if menu.menu_view.type(index) != "command":
            continue
        if menu.menu_view.entrycget(index, "label") == LOCK_LABEL:
            menu.menu_view.invoke(index)
            return
    raise AssertionError(f"メニュー項目が見つかりません: {LOCK_LABEL}")


@pytest.fixture(scope="module")
def real_tk_root() -> Iterator[tk.Tk]:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk rootを作成できません: {exc}")
    root.deiconify()
    root.minsize(1, 1)
    root.maxsize(4096, 4096)
    root.geometry("1000x700")
    _drain(root)
    try:
        yield root
    finally:
        root.destroy()


def test_ignored_geometry_disables_policy_without_false_success(
    real_tk_root: tk.Tk,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given: WMがtargetを無視、When: 実メニューで固定、Then: 無効・警告・非成功."""
    root = real_tk_root
    app = PokeControllerApp.__new__(PokeControllerApp)
    app.root = root
    app._init_window_aspect_lock()
    menu = PokeController_Menubar(app)
    root.config(menu=menu)
    entry_size = (root.winfo_width(), root.winfo_height())
    original_geometry = root.geometry

    def ignored_geometry(specification: str = "") -> str:
        if specification:
            return ""
        return original_geometry()

    monkeypatch.setattr(root, "geometry", ignored_geometry)
    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")
    try:
        _invoke_lock(menu)
        _drain(root)
    finally:
        logger.remove(sink_id)

    policy = app._window_aspect_lock
    try:
        assert policy.enabled is False
        assert (root.winfo_width(), root.winfo_height()) == entry_size
        assert policy._after_id is None
        assert not root.tk.call("after", "info")
        assert any("16:9" in message for message in logs)
        assert not any("縦横比の固定を有効にしました" in message for message in logs)
    finally:
        app._cleanup_window_aspect_lock()
        menu.destroy()
