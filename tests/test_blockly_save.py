"""blockly_save（.py＋.blockly.json保存）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
from pathlib import Path

from services import blockly_save


def good_code() -> str:
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "ブロック作成"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A)\n"
    )


def good_ws() -> str:
    return json.dumps({"blocks": {"languageVersion": 0, "blocks": []}})


def make_app(base: Path) -> Path:
    app = base / "app"
    (app / "Commands" / "PythonCommands").mkdir(parents=True)
    return app


def test_save_roundtrip(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code())
    assert res.status == "saved"
    assert res.py_rel == "Commands/PythonCommands/MyBlock.py"
    assert res.json_rel == "Commands/PythonCommands/MyBlock.blockly.json"
    text = (app / res.py_rel).read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert (app / res.json_rel).is_file()
    assert blockly_save.list_blockly(app) == ["MyBlock"]


def test_overwrite_allowed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert (
        blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code()).status
        == "saved"
    )
    res = blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code())
    assert res.status == "saved"


def test_bad_stem_fails_without_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.save_blockly(app, "my-pack", good_ws(), good_code())
    assert res.status == "failed"
    assert list((app / "Commands" / "PythonCommands").iterdir()) == []


def test_bad_code_fails_without_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.save_blockly(app, "MyBlock", good_ws(), "from . import foo\n")
    assert res.status == "failed"
    assert list((app / "Commands" / "PythonCommands").iterdir()) == []


def test_load_roundtrip(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    ws = good_ws()
    assert blockly_save.save_blockly(app, "MyBlock", ws, good_code()).status == "saved"
    res = blockly_save.load_blockly(app, "MyBlock")
    assert res.status == "ok"
    assert res.workspace_json == ws


def test_load_missing_returns_failed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.load_blockly(app, "NoSuch")
    assert res.status == "failed"


def test_load_bad_stem_returns_failed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.load_blockly(app, "../evil")
    assert res.status == "failed"


def test_delete_removes_pair(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert (
        blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code()).status
        == "saved"
    )
    assert (
        blockly_save.save_blockly(app, "Other", good_ws(), good_code()).status
        == "saved"
    )
    res = blockly_save.delete_blockly(app, "MyBlock")
    assert res.status == "deleted"
    assert not (app / "Commands" / "PythonCommands" / "MyBlock.py").exists()
    assert not (app / "Commands" / "PythonCommands" / "MyBlock.blockly.json").exists()
    # 隣は残る。一覧からも消える。
    assert (app / "Commands" / "PythonCommands" / "Other.py").is_file()
    assert blockly_save.list_blockly(app) == ["Other"]
    again = blockly_save.delete_blockly(app, "MyBlock")
    assert again.status == "failed"


def test_delete_bad_stem_returns_failed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    res = blockly_save.delete_blockly(app, "my-pack")
    assert res.status == "failed"
