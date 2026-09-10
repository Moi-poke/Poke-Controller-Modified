"""CommandTags の重複NAME診断（表示のみ・loader無変更）の検証。"""

from __future__ import annotations

from core import CommandTags


def _fake_cls(name: str, module: str) -> type:
    cls = type(module.rsplit(".", 1)[-1] + "_" + name, (), {"NAME": name})
    cls.__module__ = module
    return cls


def test_build_command_map_keeps_duplicates() -> None:
    a = _fake_cls("おなじ", "Commands.PythonCommands.A")
    b = _fake_cls("おなじ", "Commands.PythonCommands.B")
    mapping = CommandTags.buildCommandMap([a, b])
    assert len(mapping) == 2
    assert any("[A]" in k for k in mapping)
    assert any("[B]" in k for k in mapping)


def test_find_duplicate_names_reports_both() -> None:
    a = _fake_cls("おなじ", "Commands.PythonCommands.A")
    b = _fake_cls("おなじ", "Commands.PythonCommands.B")
    c = _fake_cls("べつ", "Commands.PythonCommands.C")
    dups = CommandTags.findDuplicateNames([a, b, c])
    assert set(dups.keys()) == {"おなじ"}
    assert set(dups["おなじ"]) == {a, b}
