"""ScriptPackTool（CLI）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
from pathlib import Path

import ScriptPackTool


def make_src(base: Path) -> Path:
    src = base / "src"
    (src / "Commands" / "PythonCommands").mkdir(parents=True)
    (src / "Template" / "my-pack").mkdir(parents=True)
    data = {
        "name": "my-pack",
        "version": "1.0.0",
        "author": "alice",
        "description": "sample",
        "entry": "MyPack.py",
        "minAppVersion": "4.0.0",
        "templates": ["my-pack/a.png"],
    }
    (src / "pokecon.json").write_text(json.dumps(data), encoding="utf-8")
    (src / "Commands" / "PythonCommands" / "MyPack.py").write_text(
        "from Commands.PythonCommandBase import PythonCommand\n",
        encoding="utf-8",
    )
    (src / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return src


def test_check_good_zip_returns_zero(tmp_path: Path, capsys: object) -> None:
    out = tmp_path / "my-pack.zip"
    assert ScriptPackTool.main(["pack", str(make_src(tmp_path)), str(out)]) == 0
    assert ScriptPackTool.main(["check", str(out)]) == 0


def test_pack_bad_dir_returns_one(tmp_path: Path) -> None:
    src = tmp_path / "empty"
    src.mkdir()
    assert ScriptPackTool.main(["pack", str(src), str(tmp_path / "x.zip")]) == 1


def test_install_list_uninstall(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    out = tmp_path / "my-pack.zip"
    assert ScriptPackTool.main(["pack", str(make_src(tmp_path)), str(out)]) == 0
    assert ScriptPackTool.main(["install", str(out), "--app-dir", str(app)]) == 0
    assert ScriptPackTool.main(["list", "--app-dir", str(app)]) == 0
    assert ScriptPackTool.main(["uninstall", "my-pack", "--app-dir", str(app)]) == 1
    assert (
        ScriptPackTool.main(["uninstall", "my-pack", "--app-dir", str(app), "--yes"])
        == 0
    )
    assert ScriptPackTool.main(["list", "--app-dir", str(app)]) == 0


def test_check_broken_zip_returns_one(tmp_path: Path) -> None:
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"not a zip")
    assert ScriptPackTool.main(["check", str(bad)]) == 1
