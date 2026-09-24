"""配布導入の依存確認（UI・CLI）の検証。実機・表示なしで回す。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

tkinter = pytest.importorskip("tkinter")

import ScriptPackTool  # noqa: E402
from core import pack_zip  # noqa: E402
from services import script_pack as _script_pack  # noqa: E402
from ui import script_pack_dialogs  # noqa: E402


def _stub_install_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """pip/uv を実行せず承認済み扱いにする（配管のみの検証）。"""

    def _fake(packages: list[str], **kwargs: Any) -> _script_pack.InstallResult:
        names = list(packages)
        return _script_pack.InstallResult(
            status="installed",
            message=f"依存を導入しました（{', '.join(names)}）。",
            missing=names,
            installed_deps=names,
        )

    monkeypatch.setattr(_script_pack, "install_dependencies", _fake)


def _make_zip(base: Path, entry_source: str, version: str = "1.0.0") -> Path:
    """指定の entry 本文を持つ配布 zip を作る（tmp 配下のみ）。"""
    src = base / "src"
    (src / "Commands" / "PythonCommands").mkdir(parents=True)
    (src / "Template" / "my-pack").mkdir(parents=True)
    data = {
        "name": "my-pack",
        "version": version,
        "author": "tester",
        "description": "依存確認の検証",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }
    (src / "pokecon.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    (src / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        entry_source, encoding="utf-8"
    )
    (src / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    out = base / f"my-pack-{version}.zip"
    pack_zip.create_pack(src, out)
    return out


CLEAN_ENTRY = "from Commands.Keys import Button\nfrom Commands.PythonCommandBase import PythonCommand\n"
DEPS_ENTRY = "from Commands.Keys import Button\nimport somelib_xyz_unknown_aaa\n"


class _MsgBox:
    """messagebox の偽物。askyesno の答えを順に返す。"""

    def __init__(self, answers: list[bool]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, Any]] = []

    def askyesno(self, title: str, body: str, parent: Any = None) -> bool:
        assert parent is not None, "parent= は必須"
        self.calls.append((title, body, parent))
        return self.answers.pop(0)

    def showinfo(self, *args: Any, **kwargs: Any) -> None:
        return None

    def showerror(self, *args: Any, **kwargs: Any) -> None:
        return None

    def showwarning(self, *args: Any, **kwargs: Any) -> None:
        return None


def _run_dialog(
    monkeypatch: pytest.MonkeyPatch,
    zip_path: Path,
    app: Path,
    answers: list[bool],
) -> tuple[_MsgBox, dict[str, bool], Any]:
    """ダイアログを偽物に差し替えて実行する。"""
    _stub_install_deps(monkeypatch)
    fake = _MsgBox(answers)
    root = object()
    monkeypatch.setattr(
        script_pack_dialogs.filedialog,
        "askopenfilename",
        lambda **kwargs: str(zip_path),
    )
    monkeypatch.setattr(script_pack_dialogs.tkmsg, "askyesno", fake.askyesno)
    monkeypatch.setattr(script_pack_dialogs.tkmsg, "showinfo", fake.showinfo)
    monkeypatch.setattr(script_pack_dialogs.tkmsg, "showerror", fake.showerror)
    monkeypatch.setattr(script_pack_dialogs.tkmsg, "showwarning", fake.showwarning)
    state = {"reloaded": False}

    def _reload() -> None:
        state["reloaded"] = True

    script_pack_dialogs.install_script_zip(
        root,  # type: ignore[arg-type]
        str(app),
        is_busy=lambda: False,
        reload_commands=_reload,
    )
    return fake, state, root


def test_deps_confirm_yes_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """依存確認で「はい」なら導入する。"""
    app = tmp_path / "app"
    app.mkdir()
    zip_path = _make_zip(tmp_path / "pack", DEPS_ENTRY)
    fake, state, _root = _run_dialog(monkeypatch, zip_path, app, [True])
    assert (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()
    assert state["reloaded"] is True
    assert fake.calls[0][0] == "依存確認"
    assert "somelib_xyz_unknown_aaa" in fake.calls[0][1]


def test_deps_confirm_no_cancels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """依存確認で「いいえ」なら置かずに終える。"""
    app = tmp_path / "app"
    app.mkdir()
    zip_path = _make_zip(tmp_path / "pack", DEPS_ENTRY)
    fake, state, _root = _run_dialog(monkeypatch, zip_path, app, [False])
    assert not (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()
    assert state["reloaded"] is False
    assert fake.calls[0][0] == "依存確認"


def test_deps_confirm_overflow_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """不足が6件なら5件＋残り件数で出す（注意と同じ形）。"""
    app = tmp_path / "app"
    app.mkdir()
    imports = "".join(f"import somelib_xyz_unknown_{i:02d}\n" for i in range(6))
    zip_path = _make_zip(
        tmp_path / "pack", "from Commands.Keys import Button\n" + imports
    )
    fake, _state, _root = _run_dialog(monkeypatch, zip_path, app, [False])
    body = fake.calls[0][1]
    assert "ほか 1 件" in body
    assert "somelib_xyz_unknown_00" in body


def test_overwrite_still_works(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """依存なしの上書きは従来どおり動く（振る舞いを変えない）。"""
    app = tmp_path / "app"
    app.mkdir()
    zip_v1 = _make_zip(tmp_path / "v1", CLEAN_ENTRY, "1.0.0")
    pack_zip.create_pack(tmp_path / "v1" / "src", tmp_path / "v1.zip")
    from services import script_pack

    assert script_pack.install_zip(app, zip_v1).status == "installed"
    zip_v2 = _make_zip(tmp_path / "v2", CLEAN_ENTRY, "2.0.0")
    fake, state, _root = _run_dialog(monkeypatch, zip_v2, app, [True])
    assert fake.calls[0][0] == "上書き確認"
    assert state["reloaded"] is True
    assert script_pack.read_record(app, "my-pack") is not None
    assert script_pack.read_record(app, "my-pack").version == "2.0.0"  # type: ignore[union-attr]


def test_deps_then_overwrite_sequential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """依存承認のあと上書き確認が出て、両方「はい」で入る。"""
    from services import script_pack

    app = tmp_path / "app"
    app.mkdir()
    zip_v1 = _make_zip(tmp_path / "v1", CLEAN_ENTRY, "1.0.0")
    assert script_pack.install_zip(app, zip_v1).status == "installed"
    zip_v2 = _make_zip(tmp_path / "v2", DEPS_ENTRY, "2.0.0")
    fake, state, _root = _run_dialog(monkeypatch, zip_v2, app, [True, True])
    assert [c[0] for c in fake.calls] == ["依存確認", "上書き確認"]
    assert state["reloaded"] is True
    assert script_pack.read_record(app, "my-pack").version == "2.0.0"  # type: ignore[union-attr]


def test_cli_deps_needs_yes_then_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    """CLI は素では止め、--yes で入れる（非対話の承認経路）。"""
    _stub_install_deps(monkeypatch)
    app = tmp_path / "app"
    app.mkdir()
    zip_path = _make_zip(tmp_path / "pack", DEPS_ENTRY)
    assert ScriptPackTool.main(["install", str(zip_path), "--app-dir", str(app)]) == 1
    assert not (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()
    out = capsys.readouterr().out
    assert "--yes" in out
    assert "somelib_xyz_unknown_aaa" in out
    assert (
        ScriptPackTool.main(["install", str(zip_path), "--app-dir", str(app), "--yes"])
        == 0
    )
    assert (app / "Commands" / "PythonCommands" / "MyPack.py").is_file()
