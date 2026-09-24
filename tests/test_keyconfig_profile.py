#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Menubar.OpenKeyConfig のプロファイル受け渡しの検証（バグ3の回帰用）。"""

from __future__ import annotations

from typing import Any

import Menubar
from Settings import GuiSettings


class _FakeRoot:
    """実Tkを作らない最小の親。protocol/destroy だけ持つ。"""

    def protocol(self, _name: str, _cb: object) -> None:
        return None


class _FakeApp:
    """profile="switch1" のアプリ本体の再現。root だけ持てばよい。"""

    def __init__(self) -> None:
        self.profile = "switch1"
        self.root = _FakeRoot()


class _StubKeycon:
    """PokeKeycon の差し替え。受け取った profile を記録する。"""

    last_profile: str | None = None
    last_on_saved: Any = None

    def __init__(
        self,
        master: Any = None,
        profile: str = "",
        on_saved: Any = None,
    ) -> None:
        type(self).last_profile = profile
        type(self).last_on_saved = on_saved
        self.master = master

    def protocol(self, _name: str, _cb: object) -> None:
        return None


def _make_menubar(app: _FakeApp) -> Menubar.PokeController_Menubar:
    # __init__（実tk.Menu生成）を避け、OpenKeyConfigに必要な属性だけ持たせる。
    obj = Menubar.PokeController_Menubar.__new__(Menubar.PokeController_Menubar)
    obj.app = app  # type: ignore[attr-defined]
    obj.key_config = None  # type: ignore[attr-defined]
    return obj


def test_open_keyconfig_forwards_profile(monkeypatch: Any) -> None:
    """アプリのprofileをキーコンフィグへ渡す（現状は未受け渡しで失敗する）。"""
    # Given: プロファイルswitch1で動くアプリ本体
    app = _FakeApp()
    bar = _make_menubar(app)
    _StubKeycon.last_profile = None
    _StubKeycon.last_on_saved = None
    monkeypatch.setattr(Menubar, "PokeKeycon", _StubKeycon)

    # When: メニューからキーコンフィグを開く
    bar.OpenKeyConfig()

    # Then: PokeKeyconへprofileが届き、設定パスもプロファイル別になる
    profile: str | None = _StubKeycon.last_profile
    assert profile == "switch1"
    assert GuiSettings._path_for(profile) == GuiSettings._path_for("switch1")
    assert GuiSettings._path_for(profile) != GuiSettings.SETTING_PATH
    assert callable(_StubKeycon.last_on_saved)
