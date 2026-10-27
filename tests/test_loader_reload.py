"""CommandLoader.reload の単体分離の検証。

1 ファイルが壊れても残りは読み直せること。全体が止まるのは困る。
"""

from __future__ import annotations

import importlib
import sys
import typing
from types import ModuleType

from loguru import logger


def test_import_all_modules_continues_on_system_exit(monkeypatch: object) -> None:
    """import 時の sys.exit()（SystemExit）で全体の読み込みを止めない。"""
    import core.Utility as util

    good = ModuleType("test_import_good_xyz")
    broken_name = "test_import_bad_xyz"
    mp: typing.Any = monkeypatch

    def fake_import(name: str) -> ModuleType:
        if name == broken_name:
            raise SystemExit("x")
        return good

    mp.setattr(importlib, "import_module", fake_import)

    logged: list[str] = []
    sink_id = logger.add(
        lambda msg: logged.append(str(msg)), level="ERROR", format="{message}"
    )
    try:
        modules = util.importAllModules("dummy", [broken_name, good.__name__])
    finally:
        logger.remove(sink_id)

    # 例外は外へ漏れず、壊れていない側だけが返る。
    assert modules == [good]
    assert any(broken_name in line for line in logged)


def test_reload_continues_on_system_exit(monkeypatch: object) -> None:
    """reload 中の sys.exit()（SystemExit）でも他の再読み込みを続ける。"""
    from core.CommandLoader import CommandLoader

    good = ModuleType("test_reload_exit_good_xyz")
    bad = ModuleType("test_reload_exit_bad_xyz")
    mp: typing.Any = monkeypatch

    mp.setitem(sys.modules, good.__name__, good)
    mp.setitem(sys.modules, bad.__name__, bad)

    loader = CommandLoader(base_path="dummy", base_class=object)
    loader.modules = [good, bad]

    import core.Utility as util

    mp.setattr(util, "getModuleNames", lambda path: [good.__name__, bad.__name__])

    called: list[str] = []

    def fake_reload(mod: ModuleType) -> ModuleType:
        if mod is bad:
            raise SystemExit("x")
        called.append(mod.__name__)
        return mod

    mp.setattr(importlib, "reload", fake_reload)

    # 落ちずに続けばよい（壊れた1件で全体が止まらない）。
    result = loader.reload()

    assert isinstance(result, list)
    assert good.__name__ in called
    assert good in loader.modules


def test_reload_continues_on_single_failure(monkeypatch: object) -> None:
    """1 つの再読み込みが落ちても例外なく続け、他は再読み込みする。"""
    from core.CommandLoader import CommandLoader

    good = ModuleType("test_reload_good_xyz")
    bad = ModuleType("test_reload_bad_xyz")
    # mypy の monkeypatch 型は Any のため、getattr 経由で差し替える。
    mp: object = monkeypatch
    assert mp is not None

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
