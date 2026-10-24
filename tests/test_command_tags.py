"""CommandTags の重複NAME診断（表示のみ・loader無変更）の検証。"""

from __future__ import annotations

from typing import Any

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


def test_corrupt_tags_are_quarantined_and_saving_is_refused(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Given: 壊れた tags.json / When: 読む→保存する / Then: 退避して残し、保存を断ること。"""
    monkeypatch.setattr(CommandTags.util, "ospath", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(CommandTags, "_load_failed", False)
    path = tmp_path / CommandTags.TAGS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れている", encoding="utf-8")

    # 一覧は空で続ける（起動を妨げない）。
    assert CommandTags.loadOverrides() == {}

    # 壊れたファイルは退避して残す（黙って上書きすると手で直す余地が消える）。
    assert not path.exists()
    assert list(path.parent.glob("tags.json.corrupt-*"))

    # 退避先を直すまで保存しない。
    assert CommandTags.saveOverrides({"A": ["x"]}) is False
    assert not path.exists()


def test_saving_works_when_the_file_was_readable(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Given: 読める tags.json / When: 保存する / Then: 書けて読み直せること。"""
    monkeypatch.setattr(CommandTags.util, "ospath", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(CommandTags, "_load_failed", False)
    path = tmp_path / CommandTags.TAGS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"A": ["タグ"]}', encoding="utf-8")

    assert CommandTags.loadOverrides() == {"A": ["タグ"]}
    assert CommandTags.saveOverrides({"A": ["別"]}) is True
    assert CommandTags.loadOverrides() == {"A": ["別"]}


def test_saving_creates_the_folder_without_raising(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Given: Commands/ が無い（初回相当） / When: 保存する / Then: 例外を漏らさず作れること。"""
    monkeypatch.setattr(CommandTags.util, "ospath", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(CommandTags, "_load_failed", False)

    # フォルダが無い状態で mkstemp を呼ぶと FileNotFoundError が公開操作から
    # 漏れる（先に作る）。
    assert CommandTags.saveOverrides({"A": ["x"]}) is True
    assert (tmp_path / CommandTags.TAGS_JSON).is_file()


def test_saving_recovers_after_the_quarantined_file_is_fixed(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Given: 破損を退避した後 / When: 直したファイルを読む / Then: 保存できるよう戻ること。"""
    monkeypatch.setattr(CommandTags.util, "ospath", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(CommandTags, "_load_failed", False)
    path = tmp_path / CommandTags.TAGS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れている", encoding="utf-8")
    assert CommandTags.loadOverrides() == {}
    assert CommandTags.saveOverrides({"A": ["x"]}) is False

    # 利用者が直して置き直したら、保存の拒否は解ける（再起動を要さない）。
    path.write_text('{"A": ["タグ"]}', encoding="utf-8")
    assert CommandTags.loadOverrides() == {"A": ["タグ"]}
    assert CommandTags.saveOverrides({"A": ["別"]}) is True
    assert CommandTags.loadOverrides() == {"A": ["別"]}
