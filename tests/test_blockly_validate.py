"""blockly_validate（ファイル名・生成コード・JSON）の検証。"""

from __future__ import annotations

import json

from core import blockly_validate


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
        "        self.wait(0.5)\n"
    )


def test_good_code_passes() -> None:
    assert blockly_validate.validate_generated_code(good_code()) == []
    assert blockly_validate.validate_stem("MyBlock") == []
    ws = {"blocks": {"languageVersion": 0, "blocks": []}}
    assert blockly_validate.validate_workspace_json(json.dumps(ws)) == []


def test_bad_stem_rejected() -> None:
    assert blockly_validate.validate_stem("my-pack") != []
    assert blockly_validate.validate_stem("../evil") != []
    assert blockly_validate.validate_stem("") != []


def test_relative_import_rejected() -> None:
    code = good_code() + "from . import foo\n"
    assert blockly_validate.validate_generated_code(code) != []


def test_unknown_commands_rejected() -> None:
    code = "from Commands.Unknown import X\n" + good_code()
    assert blockly_validate.validate_generated_code(code) != []


def test_missing_name_rejected() -> None:
    code = good_code().replace('NAME = "ブロック作成"', "NAME = ")
    assert blockly_validate.validate_generated_code(code) != []


def test_missing_do_rejected() -> None:
    code = good_code().replace("    def do(self) -> None:\n", "")
    assert blockly_validate.validate_generated_code(code) != []


def test_broken_json_rejected() -> None:
    assert blockly_validate.validate_workspace_json("{broken") != []
    assert blockly_validate.validate_workspace_json(json.dumps({"a": 1})) != []
