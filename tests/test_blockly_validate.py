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


def test_command_audio_and_sounddevice_accepted() -> None:
    code = (
        "from Commands.CommandAudio import AudioPythonCommand\n"
        "import sounddevice\n" + good_code()
    )
    assert blockly_validate.validate_generated_code(code) == []


def test_unknown_third_party_rejected() -> None:
    code = "import somelib_xyz\n" + good_code()
    assert blockly_validate.validate_generated_code(code) != []


def sub_code(body: str, methods: str = "") -> str:
    return (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "ブロック作成"\n'
        "\n"
        f"{methods}"
        "    def do(self) -> None:\n"
        f"{body}"
    )


def test_subroutine_valid_passes() -> None:
    code = sub_code(
        "        self.my_sub(3)\n",
        "    def my_sub(self, n) -> None:\n        self.press(Button.A)\n\n",
    )
    assert blockly_validate.validate_generated_code(code) == []


def test_subroutine_comment_passes() -> None:
    code = sub_code(
        "        # メモ\n        self.press(Button.A)\n",
    )
    assert blockly_validate.validate_generated_code(code) == []


def test_subroutine_duplicate_rejected() -> None:
    code = sub_code(
        "        self.my_sub()\n",
        "    def my_sub(self) -> None:\n"
        "        self.press(Button.A)\n"
        "\n"
        "    def my_sub(self) -> None:\n"
        "        self.wait(0.5)\n"
        "\n",
    )
    assert blockly_validate.validate_generated_code(code) != []


def test_subroutine_undefined_call_rejected() -> None:
    code = sub_code("        self.unknown_sub()\n")
    assert blockly_validate.validate_generated_code(code) != []


def test_subroutine_direct_recursion_rejected() -> None:
    code = sub_code(
        "        self.my_sub()\n",
        "    def my_sub(self) -> None:\n        self.my_sub()\n\n",
    )
    assert blockly_validate.validate_generated_code(code) != []


def test_subroutine_indirect_recursion_rejected() -> None:
    code = sub_code(
        "        self.a_sub()\n",
        "    def a_sub(self) -> None:\n"
        "        self.b_sub()\n"
        "\n"
        "    def b_sub(self) -> None:\n"
        "        self.a_sub()\n"
        "\n",
    )
    assert blockly_validate.validate_generated_code(code) != []


def test_subroutine_nested_call_passes() -> None:
    code = sub_code(
        "        self.a_sub()\n",
        "    def a_sub(self) -> None:\n"
        "        self.b_sub()\n"
        "\n"
        "    def b_sub(self) -> None:\n"
        "        self.press(Button.A)\n"
        "\n",
    )
    assert blockly_validate.validate_generated_code(code) == []


def test_subroutine_arity_mismatch_rejected() -> None:
    code = sub_code(
        "        self.my_sub(1)\n",
        "    def my_sub(self, a, b) -> None:\n        self.press(Button.A)\n\n",
    )
    assert blockly_validate.validate_generated_code(code) != []


def test_subroutine_non_ascii_name_rejected() -> None:
    code = sub_code(
        "        self.press(Button.A)\n",
        "    def \u30c6\u30b9\u30c8(self) -> None:\n        self.press(Button.A)\n\n",
    )
    assert blockly_validate.validate_generated_code(code) != []
