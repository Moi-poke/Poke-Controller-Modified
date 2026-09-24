"""保存→再読み込み→実行の往復検証。実機・GUIなしで回す。

録画マクロの生成物（Commands.* 凍結名だけを使い、モジュール直下に
副作用を持たない do() 本体）が、保存したその場で読み直せて、
headless でも do() が走ること。実物の Commands/PythonCommands/ には
触らず、tmp_path の偽ツリーだけで閉じる。
"""

from __future__ import annotations

import importlib.util
import sys
import typing
from pathlib import Path
from types import ModuleType

import pytest
from Commands.Keys import Button
from Commands.PythonCommandBase import PythonCommand
from core.CommandLoader import CommandLoader
from services import macro_save

_MOD_NAME = "test_macro_reload_execute_mod"


def _macro_code() -> str:
    """保存するマクロ本文。凍結名のみ・直下副作用なしの約束を守る。"""
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class ReloadMacro(PythonCommand):\n"
        '    NAME = "再読込マクロ"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A, duration=0.12)\n"
        "        self.wait(0.42)\n"
        "        self.press(Button.B, duration=0.08)\n"
    )


def _load_saved_module(py_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """保存物をその場で読み込む。直下副作用があればここで落ちる。"""
    spec = importlib.util.spec_from_file_location(_MOD_NAME, str(py_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    cast_mp: typing.Any = monkeypatch
    cast_mp.setitem(sys.modules, _MOD_NAME, mod)
    spec.loader.exec_module(mod)
    return mod


def test_save_reload_execute_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存→reload→do() が GUI・実機なしで一巡する。"""
    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)

    res = macro_save.save_macro(app, "ReloadMacro", _macro_code())
    assert res.status == "saved"
    py_path = app / res.py_rel
    assert py_path.is_file()

    mod = _load_saved_module(py_path, monkeypatch)

    # loader.reload() の経路に載せる。実パスは絶対化すると import 名に
    # ならないため、test_loader_reload と同じく getModuleNames だけ差す。
    import core.Utility as util

    cast_mp: typing.Any = monkeypatch
    cast_mp.setattr(util, "getModuleNames", lambda path: [_MOD_NAME])

    loader = CommandLoader(base_path="dummy", base_class=PythonCommand)
    loader.modules = [mod]
    classes = loader.reload()
    assert any(c is getattr(mod, "ReloadMacro") for c in classes)

    target: typing.Any = next(
        c for c in classes if getattr(c, "NAME", "") == "再読込マクロ"
    )
    cmd: typing.Any = target()
    assert isinstance(cmd, PythonCommand)

    calls: list[tuple[str, typing.Any, float, float]] = []
    waits: list[float] = []

    def fake_press(
        buttons: typing.Any, duration: float = 0.1, wait: float = 0.1
    ) -> None:
        calls.append(("press", buttons, float(duration), float(wait)))

    def fake_wait(wait: float) -> None:
        waits.append(float(wait))

    cmd.press = fake_press
    cmd.wait = fake_wait
    cmd.do()

    assert [(c[1], c[2], c[3]) for c in calls] == [
        (Button.A, 0.12, 0.1),
        (Button.B, 0.08, 0.1),
    ]
    assert waits == [0.42]
