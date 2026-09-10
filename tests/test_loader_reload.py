"""CommandLoader.reload の単体分離の検証。

1 ファイルが壊れても残りは読み直せること。全体が止まるのは困る。
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType


def test_reload_continues_on_single_failure(monkeypatch: object) -> None:
    """1 つの再読み込みが落ちても例外なく続け、他は再読み込みする。"""
    from core.CommandLoader import CommandLoader

    good = ModuleType("test_reload_good_xyz")
    bad = ModuleType("test_reload_bad_xyz")
    # mypy の monkeypatch 型は Any のため、getattr 経由で差し替える。
    mp: object = monkeypatch
    assert mp is not None

    import typing

    cast_mp: typing.Any = monkeypatch
    cast_mp.setitem(sys.modules, good.__name__, good)
    cast_mp.setitem(sys.modules, bad.__name__, bad)

    loader = CommandLoader(base_path="dummy", base_class=object)
    loader.modules = [good, bad]

    import core.Utility as util

    cast_mp.setattr(util, "getModuleNames", lambda path: [good.__name__, bad.__name__])

    called: list[str] = []
    real_reload = importlib.reload

    def fake_reload(mod: ModuleType) -> ModuleType:
        if mod is bad:
            raise RuntimeError("壊れたコマンド相当")
        called.append(mod.__name__)
        return mod

    _ = real_reload
    cast_mp.setattr(importlib, "reload", fake_reload)

    # 落ちずに続けばよい（壊れた1件で全体が止まらない）。
    result = loader.reload()

    assert isinstance(result, list)
    assert good.__name__ in called
    # 壊れた側も一覧からは消さない（次回の再試行で直せる）。
    assert bad in loader.modules
    assert good in loader.modules
