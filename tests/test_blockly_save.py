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


def _motion_ws() -> str:
    """motion/motion2ブロックを含むworkspace JSONを作る。"""
    release2 = {
        "type": "pokecon_motion2",
        "fields": {
            "FROM_L": "0,90",
            "TO_L": "127,90",
            "FROM_R": "0,0",
            "TO_R": "127,0",
            "T_MS": 200,
            "END": "RELEASE",
        },
    }
    cont2 = {
        "type": "pokecon_motion2",
        "fields": {
            "FROM_L": "0,90",
            "TO_L": "127,90",
            "FROM_R": "0,0",
            "TO_R": "127,0",
            "T_MS": 200,
            "END": "CONT",
        },
        "next": {"block": release2},
    }
    motion = {
        "type": "pokecon_motion",
        "fields": {
            "STICK": "LEFT",
            "FROM": "0,90",
            "TO": "127,450",
            "T_MS": 500,
            "END": "RELEASE",
        },
        "next": {"block": cont2},
    }
    program = {
        "type": "pokecon_program",
        "fields": {"NAME": "MotionTest", "TAGS": "blockly"},
        "inputs": {"DO": {"block": motion}},
    }
    return json.dumps({"blocks": {"languageVersion": 0, "blocks": [program]}})


def _motion_code() -> str:
    """motion/motion2ブロックの生成コード相当（実測のcodegen形に合わせる）。"""
    return (
        "from Commands.Keys import Direction, Stick\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "MotionTest"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.stick_move(Stick.LEFT, 0, 90, 127, 450, 500)\n"
        "        self.stick_release(Stick.LEFT)\n"
        "        self.stick_move2(Stick.LEFT, (0, 90), (127, 90), "
        "Stick.RIGHT, (0, 0), (127, 0), 200)\n"
        "        self.stick_move2(Stick.LEFT, (0, 90), (127, 90), "
        "Stick.RIGHT, (0, 0), (127, 0), 200)\n"
        "        self.stick_release_both()\n"
    )


def test_motion_save_load_keeps_fields_and_code(tmp_path: Path) -> None:
    """Given motion入りworkspace / When 保存→読込する / Then FROM/TO/T_MS/END欄が保持され同じstick_moveコードが出ること。"""
    app = make_app(tmp_path)
    ws = _motion_ws()
    code = _motion_code()
    assert blockly_save.save_blockly(app, "MotionTest", ws, code).status == "saved"
    res = blockly_save.load_blockly(app, "MotionTest")
    assert res.status == "ok"
    assert json.loads(res.workspace_json) == json.loads(ws)


def test_motion_save_load_code_still_validates(tmp_path: Path) -> None:
    """Given motion入り保存物 / When 保存→読込する / Then 読込後のコード相当が検証を通ること。"""
    from core import blockly_validate

    app = make_app(tmp_path)
    ws = _motion_ws()
    code = _motion_code()
    assert blockly_save.save_blockly(app, "MotionTest", ws, code).status == "saved"
    res = blockly_save.load_blockly(app, "MotionTest")
    assert res.status == "ok"
    assert blockly_validate.validate_workspace_json(res.workspace_json) == []
    assert blockly_validate.validate_generated_code(code) == []
