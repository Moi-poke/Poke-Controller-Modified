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


def test_multiple_programs_rejected() -> None:
    code = (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "A"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.A)\n"
        "\n\n"
        "class BlocklyCmd2(PythonCommand):\n"
        '    NAME = "B"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Button.B)\n"
    )
    assert blockly_validate.validate_generated_code(code) != []


def test_stick_import_and_press_passes() -> None:
    code = (
        "from Commands.Keys import Button, Direction, Stick\n"
        "from Commands.PythonCommandBase import PythonCommand\n"
        "\n\n"
        "class BlocklyCmd(PythonCommand):\n"
        '    NAME = "ブロック作成"\n'
        "\n"
        "    def do(self) -> None:\n"
        "        self.press(Direction(Stick.LEFT, 90, magnification=1.0))\n"
    )
    assert blockly_validate.validate_generated_code(code) == []


def test_subroutine_output_calls_pass() -> None:
    code = sub_code(
        "        self.log_sub()\n",
        "    def log_sub(self) -> None:\n"
        '        self.discord_text(content="hi")\n'
        "        self.pressRep(Button.A, 2)\n"
        "        self.hold(Button.B)\n"
        "        self.holdEnd(Button.B)\n"
        "\n",
    )
    assert blockly_validate.validate_generated_code(code) == []


def test_screenshot_and_discord_image_pass() -> None:
    code = (
        "from Commands.Keys import Button\n"
        "from Commands.PythonCommandBase import ImageProcPythonCommand\n"
        "\n\n"
        "class BlocklyCmd(ImageProcPythonCommand):\n"
        '    NAME = "ブロック作成"\n'
        "\n"
        "    def __init__(self, cam, gui=None):\n"
        "        super().__init__(cam, gui)\n"
        "\n"
        "    def do(self) -> None:\n"
        "        self.camera.saveCapture()\n"
        '        self.discord_image(content="done")\n'
        '        print("done")\n'
    )
    assert blockly_validate.validate_generated_code(code) == []


def test_audio_tone_calls_pass() -> None:
    code = sub_code(
        "        self.wait_tone_sub()\n",
        "    def wait_tone_sub(self) -> None:\n"
        "        self.waitTone([(3000.0, 3200.0)], [1000000.0], timeout=10.0)\n"
        "        x = self.isTonePresent([(3000.0, 3200.0)], [1000000.0])\n"
        "\n",
    )
    assert blockly_validate.validate_generated_code(code) == []


def dialog_ws(*fields_list: dict[str, object]) -> str:
    import json as _json

    chain: dict[str, object] | None = None
    for fields in reversed(list(fields_list)):
        block: dict[str, object] = {
            "type": "pokecon_dialog_choice",
            "fields": fields,
        }
        if chain is not None:
            block["next"] = {"block": chain}
        chain = block
    assert chain is not None
    return _json.dumps({"blocks": {"languageVersion": 0, "blocks": [chain]}})


def test_dialog_vars_valid_passes() -> None:
    ws = dialog_ws(
        {
            "VAR": "setting",
            "TITLE": "設定",
            "LABEL": "項目",
            "OPTIONS": "A,B",
            "DEFAULT": "A",
        },
    )
    assert blockly_validate.validate_dialog_vars(ws) == []


def test_dialog_vars_bad_name_rejected() -> None:
    for bad in ["", "123abc", "class", "self", "do", "NAME", "a-b"]:
        ws = dialog_ws({"VAR": bad})
        assert blockly_validate.validate_dialog_vars(ws) != [], bad


def test_dialog_number_range_rejected() -> None:
    import json as _json

    ws = _json.dumps(
        {
            "blocks": {
                "languageVersion": 0,
                "blocks": [
                    {
                        "type": "pokecon_dialog_number",
                        "fields": {
                            "VAR": "n",
                            "TITLE": "t",
                            "LABEL": "l",
                            "MIN": 10,
                            "MAX": 1,
                            "DEFAULT": 5,
                        },
                    }
                ],
            }
        }
    )
    assert blockly_validate.validate_dialog_vars(ws) != []


def test_dialog_vars_broken_json_ignored() -> None:
    assert blockly_validate.validate_dialog_vars("{broken") == []
