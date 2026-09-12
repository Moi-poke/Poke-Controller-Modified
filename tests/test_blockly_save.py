"""blockly_save（.py＋.blockly.json保存）の検証。実機・GUIなしで回す。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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


def test_vision_code_with_subdir_saves_without_warnings(tmp_path: Path) -> None:
    from services import blockly_templates

    app = make_app(tmp_path)
    code = (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        '        self.waitTemplate("my-pack/a.png", timeout=10.0, threshold=0.7)\n'
    )
    assert blockly_templates.validate_template_refs(code) == []
    res = blockly_save.save_blockly(app, "VisionOk", good_ws(), code)
    assert res.status == "saved"
    assert res.warnings == []


def test_dotdot_template_fails_without_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    code = (
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "画像認識"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        '        self.waitTemplate("../evil.png", timeout=10.0, threshold=0.7)\n'
    )
    res = blockly_save.save_blockly(app, "VisionNg", good_ws(), code)
    assert res.status == "failed"
    assert list((app / "Commands" / "PythonCommands").iterdir()) == []


def test_save_rollback_on_second_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2件目の書き込み失敗で1件目を巻き戻す（実書き＋故障注入）。"""
    from pathlib import Path as _Path

    app = make_app(tmp_path)
    orig = blockly_save._atomic_write
    calls = {"n": 0}

    def faulty(target: _Path, text: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("故障注入")
        orig(target, text)

    monkeypatch.setattr(blockly_save, "_atomic_write", faulty)
    res = blockly_save.save_blockly(app, "MyBlock", good_ws(), good_code())
    assert res.status == "failed"
    assert not (app / "Commands" / "PythonCommands" / "MyBlock.py").exists()
    assert not (app / "Commands" / "PythonCommands" / "MyBlock.blockly.json").exists()


def test_bad_dialog_var_fails_without_files(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    ws = json.dumps(
        {
            "blocks": {
                "languageVersion": 0,
                "blocks": [
                    {
                        "type": "pokecon_dialog_choice",
                        "fields": {"VAR": "class"},
                    }
                ],
            }
        }
    )
    res = blockly_save.save_blockly(app, "MyBlock", ws, good_code())
    assert res.status == "failed"
    assert list((app / "Commands" / "PythonCommands").iterdir()) == []
