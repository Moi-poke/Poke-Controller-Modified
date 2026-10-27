from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from core import blockly_headless

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"

ALLOWED_STANDARD = (
    "controls_repeat",
    "controls_repeat_ext",
    "controls_whileUntil",
    "controls_for",
    "controls_if",
    "controls_flow_statements",
    "logic_compare",
    "logic_operation",
    "logic_negate",
    "logic_boolean",
    "math_number",
    "math_arithmetic",
    "math_modulo",
    "math_change",
    "math_random_int",
    "math_random_float",
    "text",
    "text_join",
    "variables_get",
    "variables_set",
)

NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node が無いため飛ばす"
)


def _fields(info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(field["name"]): field
        for field in info.get("fields", [])
        if isinstance(field, dict) and field.get("name")
    }


def _inputs(info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(entry["name"]): entry
        for entry in info.get("inputs", [])
        if isinstance(entry, dict) and entry.get("name")
    }


def _workspace(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "fields": {"NAME": "テスト", "TAGS": "blockly"},
                    "inputs": {"DO": {"block": block}},
                }
            ],
        }
    }


@NEEDS_NODE
def test_catalog_has_pokecon_and_allowed_standard_blocks() -> None:
    result = blockly_headless.catalog(BLOCKLY)
    assert result.ok, result.errors
    assert result.data is not None
    blocks = result.data["blocks"]
    assert "pokecon_press" in blocks
    assert "controls_repeat_ext" in blocks
    for name in ALLOWED_STANDARD:
        assert name in blocks, name
    # 許可していない標準ブロックは混ぜない。
    assert "lists_create_with" not in blocks
    standard = {name for name in blocks if not name.startswith("pokecon_")}
    assert standard == set(ALLOWED_STANDARD)


@NEEDS_NODE
def test_catalog_classifies_fields_and_inputs() -> None:
    result = blockly_headless.catalog(BLOCKLY)
    assert result.ok, result.data
    assert result.data is not None
    blocks = result.data["blocks"]

    press = _fields(blocks["pokecon_press"])
    assert press["BUTTON"]["kind"] == "controller"
    assert press["DURATION"]["kind"] == "number"
    assert press["BUTTON"]["options"]
    assert "A" in [value for _label, value in press["BUTTON"]["options"]]

    vision = _fields(blocks["pokecon_vision_contains"])
    assert vision["TEMPLATE"]["kind"] == "template"
    assert vision["CROP"]["kind"] == "crop"
    assert vision["THRESHOLD"]["kind"] == "number"
    assert vision["USE_GRAY"]["kind"] == "checkbox"
    assert vision["PREVIEW"]["serializable"] is False

    stick = _fields(blocks["pokecon_stick"])
    assert stick["PAD"]["kind"] == "stickpad"

    variables = _fields(blocks["variables_set"])
    assert variables["VAR"]["kind"] == "variable"

    repeat = _inputs(blocks["controls_repeat_ext"])
    assert repeat["TIMES"]["kind"] == "value"
    assert repeat["TIMES"]["check"] == ["Number"]
    assert repeat["DO"]["kind"] == "statement"

    assert blocks["controls_flow_statements"]["next"] is False
    assert blocks["pokecon_elapsed"]["output"] == ["Number"]
    assert blocks["pokecon_sub_def"]["previous"] is False


@NEEDS_NODE
def test_generate_returns_python_from_workspace() -> None:
    workspace = _workspace(
        {
            "type": "pokecon_wait",
            "fields": {"SEC": 0.75},
            "next": {
                "block": {
                    "type": "pokecon_press",
                    "fields": {"BUTTON": "A", "DURATION": 0.1, "WAIT": 0.2},
                }
            },
        }
    )
    result = blockly_headless.generate(workspace, BLOCKLY)
    assert result.ok, result.errors
    assert result.data is not None
    code = result.data["code"]
    assert "class BlocklyCmd(" in code
    assert "self.wait(0.75)" in code
    assert "self.press(Button.A, duration=0.1, wait=0.2)" in code
    assert result.data["warnings"] == []


@NEEDS_NODE
def test_generate_stick_both_emits_left_and_right() -> None:
    workspace = _workspace(
        {
            "type": "pokecon_stick",
            "fields": {
                "STICK": "BOTH",
                "PAD": "90,100",
                "ANGLE": 90,
                "DURATION": 0.2,
                "WAIT": 0.1,
            },
        }
    )
    result = blockly_headless.generate(workspace, BLOCKLY)
    assert result.ok, result.errors
    assert result.data is not None
    code = result.data["code"]
    assert "self.press([Direction(Stick.LEFT" in code
    assert "Direction(Stick.RIGHT" in code


@NEEDS_NODE
def test_generate_reports_unknown_block_without_crashing() -> None:
    workspace = _workspace({"type": "no_such_block", "fields": {}})
    result = blockly_headless.generate(workspace, BLOCKLY)
    assert not result.ok
    assert result.errors


@NEEDS_NODE
def test_generate_rejects_orphan_top_level_block() -> None:
    workspace = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "fields": {"NAME": "T", "TAGS": "blockly"},
                    "inputs": {
                        "DO": {"block": {"type": "pokecon_wait", "fields": {"SEC": 1}}}
                    },
                },
                {"type": "pokecon_wait", "fields": {"SEC": 2}},
            ],
        }
    }
    result = blockly_headless.generate(workspace, BLOCKLY)
    assert not result.ok
    assert any("プログラムの外" in error for error in result.errors)


@NEEDS_NODE
def test_generate_requires_program() -> None:
    workspace = {"blocks": {"languageVersion": 0, "blocks": []}}
    result = blockly_headless.generate(workspace, BLOCKLY)
    assert not result.ok
    assert any("プログラム" in error for error in result.errors)


def test_missing_assets_is_reported(tmp_path: Path) -> None:
    result = blockly_headless.catalog(tmp_path)
    # node が無い環境では node 不在のメッセージが先に出る。どちらでもよい。
    assert not result.ok
    assert result.errors
