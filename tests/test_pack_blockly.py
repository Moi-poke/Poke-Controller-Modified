"""packにおける `.blockly.json` 同梱の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from core import pack_zip
from services import script_pack

ENTRY_PY = (
    "from Commands.Keys import Button\n"
    "from Commands.PythonCommandBase import PythonCommand\n"
    "\n\n"
    "class PackCmd(PythonCommand):\n"
    '    NAME = "配布"\n'
    "\n"
    "    def do(self) -> None:\n"
    "        self.press(Button.A)\n"
)

WS_JSON = json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})

MANIFEST = {
    "name": "my-pack",
    "version": "1.0.0",
    "author": "tester",
    "description": "blockly同梱の検証",
    "entry": "MyEntry.py",
    "minAppVersion": "4.0.0",
    "templates": ["my-pack/a.png"],
}


def make_staged(base: Path, *, with_json: bool) -> Path:
    staged = base / "staged"
    (staged / "Commands" / "PythonCommands").mkdir(parents=True)
    (staged / "Template" / "my-pack").mkdir(parents=True)
    (staged / "pokecon.json").write_text(
        json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8"
    )
    (staged / "Commands" / "PythonCommands" / "MyEntry.py").write_text(
        ENTRY_PY, encoding="utf-8"
    )
    if with_json:
        (staged / "Commands" / "PythonCommands" / "MyEntry.blockly.json").write_text(
            WS_JSON, encoding="utf-8"
        )
    (staged / "Template" / "my-pack" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return staged


def test_create_includes_companion_json(tmp_path: Path) -> None:
    staged = make_staged(tmp_path, with_json=True)
    names = pack_zip.create_pack(staged, tmp_path / "out.zip")
    assert "Commands/PythonCommands/MyEntry.blockly.json" in names
    with zipfile.ZipFile(tmp_path / "out.zip") as zf:
        assert (
            zf.read("Commands/PythonCommands/MyEntry.blockly.json").decode("utf-8")
            == WS_JSON
        )


def test_create_without_json_stays_compatible(tmp_path: Path) -> None:
    staged = make_staged(tmp_path, with_json=False)
    names = pack_zip.create_pack(staged, tmp_path / "out.zip")
    assert "Commands/PythonCommands/MyEntry.blockly.json" not in names
    assert "Commands/PythonCommands/MyEntry.py" in names


def test_install_and_uninstall_carry_json(tmp_path: Path) -> None:
    staged = make_staged(tmp_path, with_json=True)
    pack_zip.create_pack(staged, tmp_path / "out.zip")
    app = tmp_path / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    res = script_pack.install_zip(app, tmp_path / "out.zip")
    assert res.status == "installed"
    assert "Commands/PythonCommands/MyEntry.blockly.json" in res.files
    assert (app / "Commands" / "PythonCommands" / "MyEntry.blockly.json").is_file()
    un = script_pack.uninstall_package(app, "my-pack")
    assert un.status == "removed"
    assert not (app / "Commands" / "PythonCommands" / "MyEntry.blockly.json").exists()
