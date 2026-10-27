from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from core import blockly_headless
from services import blockly_author

ROOT = Path(__file__).resolve().parent.parent
BLOCKLY = ROOT / "SerialController" / "assets" / "blockly"

NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node が無いため飛ばす"
)


def _field(
    name: str,
    kind: str = "text",
    default: Any = None,
    options: list[list[str]] | None = None,
    serializable: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "default": default,
        "options": options,
        "serializable": serializable,
    }


def _input(
    name: str, kind: str = "value", check: list[str] | None = None
) -> dict[str, Any]:
    return {"name": name, "kind": kind, "check": check or []}


def _block(
    block_type: str,
    *,
    fields: list[dict[str, Any]] | None = None,
    inputs: list[dict[str, Any]] | None = None,
    previous: bool = True,
    next: bool = True,
    output: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": block_type,
        "tooltip": "",
        "previous": previous,
        "next": next,
        "output": output,
        "fields": fields or [],
        "inputs": inputs or [],
    }


def _catalog() -> dict[str, Any]:
    button = [["A", "A"], ["B", "B"]]
    target = [["A", "Button.A"], ["B", "Button.B"]]
    blocks = [
        _block(
            "pokecon_program",
            fields=[
                _field("NAME", default="ブロック作成"),
                _field("TAGS", default="blockly"),
            ],
            inputs=[_input("DO", "statement")],
            previous=False,
            next=False,
        ),
        _block(
            "pokecon_press",
            fields=[
                _field("BUTTON", "controller", "A", button),
                _field("DURATION", "number", 0.1),
                _field("WAIT", "number", 0.1),
            ],
        ),
        _block(
            "pokecon_hold",
            fields=[
                _field("TARGET", "controller", "Button.A", target),
                _field("WAIT", "number", 0.1),
            ],
        ),
        _block(
            "pokecon_hold_end",
            fields=[_field("TARGET", "controller", "Button.A", target)],
        ),
        _block(
            "pokecon_stick",
            fields=[
                _field(
                    "STICK",
                    "text",
                    "LEFT",
                    [["L", "LEFT"], ["R", "RIGHT"], ["L+R", "BOTH"]],
                ),
                _field("PAD", "stickpad", "90,100"),
                _field("ANGLE", "number", 90),
                _field("DURATION", "number", 0.1),
                _field("WAIT", "number", 0.1),
            ],
        ),
        _block(
            "pokecon_stick_release",
            fields=[
                _field(
                    "STICK",
                    "text",
                    "LEFT",
                    [["L", "LEFT"], ["R", "RIGHT"], ["L+R", "BOTH"]],
                )
            ],
        ),
        _block(
            "pokecon_press_rep",
            fields=[
                _field("TARGET", "controller", "Button.A", target),
                _field("COUNT", "number", 3),
                _field("DURATION", "number", 0.1),
                _field("INTERVAL", "number", 0.1),
                _field("WAIT", "number", 0.1),
            ],
        ),
        _block("pokecon_wait", fields=[_field("SEC", "number", 0.5)]),
        _block(
            "pokecon_print",
            fields=[
                _field("KIND", "text", "print", [["表示", "print"], ["結果", "print2"]])
            ],
            inputs=[_input("TEXT")],
        ),
        _block("pokecon_comment", fields=[_field("TEXT", default="メモ")]),
        _block("pokecon_finish"),
        _block("pokecon_screenshot"),
        _block(
            "pokecon_discord",
            fields=[
                _field(
                    "KIND",
                    "text",
                    "text",
                    [["テキスト", "text"], ["画像付き", "image"]],
                )
            ],
            inputs=[_input("CONTENT")],
        ),
        _block(
            "pokecon_sub_def",
            fields=[_field("NAME", default="my_sub"), _field("ARGS", default="")],
            inputs=[_input("DO", "statement"), _input("RETURN")],
            previous=False,
            next=False,
        ),
        _block(
            "pokecon_sub_call",
            fields=[_field("NAME", default="my_sub")],
            inputs=[_input("ARG0"), _input("ARG1"), _input("ARG2")],
        ),
        _block(
            "pokecon_sub_call_value",
            fields=[_field("NAME", default="my_sub")],
            inputs=[_input("ARG0"), _input("ARG1"), _input("ARG2")],
            previous=False,
            next=False,
            output=[],
        ),
        _block(
            "pokecon_vision_contains",
            fields=[
                _field("TEMPLATE", "template", ""),
                _field("PREVIEW", "display", "", None, False),
                _field("THRESHOLD", "number", 0.7),
                _field("CROP", "crop", ""),
                _field("USE_GRAY", "checkbox", "FALSE"),
                _field("CAPOPEN", "display", "📷", None, False),
            ],
            previous=False,
            next=False,
            output=["Boolean"],
        ),
        _block(
            "pokecon_elapsed",
            previous=False,
            next=False,
            output=["Number"],
        ),
        _block(
            "controls_repeat_ext",
            inputs=[
                _input("TIMES", check=["Number"]),
                _input("DO", "statement"),
            ],
        ),
        _block(
            "controls_whileUntil",
            fields=[
                _field(
                    "MODE",
                    "text",
                    "WHILE",
                    [["続ける", "WHILE"], ["終わる", "UNTIL"]],
                )
            ],
            inputs=[
                _input("BOOL", check=["Boolean"]),
                _input("DO", "statement"),
            ],
        ),
        _block(
            "controls_if",
            inputs=[
                _input("IF0", check=["Boolean"]),
                _input("DO0", "statement"),
            ],
        ),
        _block(
            "controls_flow_statements",
            fields=[
                _field(
                    "FLOW",
                    "text",
                    "BREAK",
                    [["抜ける", "BREAK"], ["続ける", "CONTINUE"]],
                )
            ],
            next=False,
        ),
        _block(
            "controls_for",
            fields=[_field("VAR", "variable")],
            inputs=[
                _input("FROM", check=["Number"]),
                _input("TO", check=["Number"]),
                _input("BY", check=["Number"]),
                _input("DO", "statement"),
            ],
        ),
        _block(
            "math_number",
            fields=[_field("NUM", "number", 0)],
            previous=False,
            next=False,
            output=["Number"],
        ),
        _block(
            "text",
            fields=[_field("TEXT", default="")],
            previous=False,
            next=False,
            output=["String"],
        ),
        _block(
            "logic_boolean",
            fields=[
                _field("BOOL", "text", "TRUE", [["true", "TRUE"], ["false", "FALSE"]])
            ],
            previous=False,
            next=False,
            output=["Boolean"],
        ),
        _block(
            "variables_set",
            fields=[_field("VAR", "variable")],
            inputs=[_input("VALUE")],
        ),
        _block(
            "variables_get",
            fields=[_field("VAR", "variable")],
            previous=False,
            next=False,
            output=[],
        ),
        _block(
            "math_change",
            fields=[_field("VAR", "variable")],
            inputs=[_input("DELTA", check=["Number"])],
        ),
        _block(
            "pokecon_dialog_choice",
            fields=[
                _field("VAR", "variable", "setting"),
                _field("TITLE", default="設定"),
                _field("LABEL", default="項目"),
                _field("OPTIONS", default="A,B"),
                _field("DEFAULT", default="A"),
            ],
        ),
        _block(
            "pokecon_dialog_number",
            fields=[
                _field("VAR", "variable", "count"),
                _field("TITLE", default="設定"),
                _field("LABEL", default="個数"),
                _field("MIN", "number", 1),
                _field("MAX", "number", 10),
                _field("DEFAULT", "number", 3),
            ],
        ),
        _block(
            "pokecon_dialog_check",
            fields=[
                _field("VAR", "variable", "confirm"),
                _field("TITLE", default="確認"),
                _field("LABEL", default="送る"),
                _field("DEFAULT", "checkbox", "TRUE"),
            ],
        ),
    ]
    return {"blocks": {block["type"]: block for block in blocks}}


FAKE_CATALOG = _catalog()


def _build(spec: Any) -> blockly_author.BuildResult:
    return blockly_author.build_workspace(spec, FAKE_CATALOG)


def _program_do(workspace: dict[str, Any]) -> dict[str, Any]:
    return workspace["blocks"]["blocks"][0]["inputs"]["DO"]["block"]


def test_build_chain_and_variables() -> None:
    spec = {
        "name": "テスト",
        "tags": ["blockly", "sample"],
        "do": [
            {"press": "A", "duration": 0.2, "wait": 0.4},
            {"wait": 1.5},
            {"repeat": 3, "do": [{"press": "B"}]},
            {"set": "count", "to": 0},
            {"change": "count", "by": 2},
        ],
    }
    result = _build(spec)
    assert result.ok, result.errors
    workspace = result.workspace
    assert workspace is not None
    program = workspace["blocks"]["blocks"][0]
    assert program["type"] == "pokecon_program"
    assert program["fields"] == {"NAME": "テスト", "TAGS": "blockly,sample"}
    press = _program_do(workspace)
    assert press["type"] == "pokecon_press"
    assert press["fields"] == {"BUTTON": "A", "DURATION": 0.2, "WAIT": 0.4}
    wait = press["next"]["block"]
    assert wait["type"] == "pokecon_wait"
    assert wait["fields"] == {"SEC": 1.5}
    repeat = wait["next"]["block"]
    assert repeat["type"] == "controls_repeat_ext"
    assert repeat["inputs"]["TIMES"]["block"] == {
        "type": "math_number",
        "fields": {"NUM": 3},
    }
    assert repeat["inputs"]["DO"]["block"]["type"] == "pokecon_press"
    set_block = repeat["next"]["block"]
    assert set_block["type"] == "variables_set"
    assert set_block["fields"]["VAR"] == {"id": "count"}
    assert set_block["inputs"]["VALUE"]["block"]["fields"]["NUM"] == 0
    change = set_block["next"]["block"]
    assert change["type"] == "math_change"
    assert change["inputs"]["DELTA"]["block"]["fields"]["NUM"] == 2
    assert workspace["variables"] == [{"name": "count", "id": "count"}]


def test_build_if_else_and_while() -> None:
    spec = {
        "name": "分岐",
        "do": [
            {
                "if": {"var": "flag"},
                "do": [{"print": "yes"}],
                "else": [{"print": "no", "result": True}],
            },
            {
                "while": {"var": "flag"},
                "do": [{"break": True}],
            },
        ],
    }
    result = _build(spec)
    assert result.ok, result.errors
    workspace = result.workspace
    assert workspace is not None
    conditional = _program_do(workspace)
    assert conditional["type"] == "controls_if"
    assert conditional["extraState"] == {"hasElse": True}
    assert conditional["inputs"]["IF0"]["block"]["type"] == "variables_get"
    assert conditional["inputs"]["DO0"]["block"]["type"] == "pokecon_print"
    assert conditional["inputs"]["ELSE"]["block"]["fields"]["KIND"] == "print2"
    loop = conditional["next"]["block"]
    assert loop["type"] == "controls_whileUntil"
    assert loop["fields"]["MODE"] == "WHILE"
    assert loop["inputs"]["BOOL"]["block"]["type"] == "variables_get"
    assert loop["inputs"]["DO"]["block"]["type"] == "controls_flow_statements"


def test_elapsed_must_fit_boolean_input() -> None:
    result = _build(
        {"name": "n", "do": [{"if": {"elapsed": True}, "do": [{"print": "x"}]}]}
    )
    assert not result.ok
    assert any("Boolean" in error for error in result.errors)


def test_build_subs_calls_and_return() -> None:
    spec = {
        "name": "サブ",
        "do": [{"call": "aisatsu", "args": ["x"]}],
        "subs": [
            {
                "name": "aisatsu",
                "args": ["msg"],
                "do": [{"print": {"var": "msg"}}],
                "return": "ok",
            }
        ],
    }
    result = _build(spec)
    assert result.ok, result.errors
    workspace = result.workspace
    assert workspace is not None
    call = _program_do(workspace)
    assert call == {
        "type": "pokecon_sub_call",
        "fields": {"NAME": "aisatsu"},
        "inputs": {"ARG0": {"block": {"type": "text", "fields": {"TEXT": "x"}}}},
    }
    sub = workspace["blocks"]["blocks"][1]
    assert sub["type"] == "pokecon_sub_def"
    assert sub["fields"] == {"NAME": "aisatsu", "ARGS": "msg"}
    assert sub["inputs"]["RETURN"]["block"]["fields"]["TEXT"] == "ok"


def test_dialog_var_stays_string_and_is_not_a_variable() -> None:
    spec = {
        "name": "設定",
        "do": [
            {
                "dialog_choice": {
                    "var": "setting",
                    "title": "題",
                    "label": "項目",
                    "options": ["A", "B"],
                    "default": "A",
                }
            },
            {"dialog_number": {"var": "count", "min": 1, "max": 5, "default": 2}},
            {"dialog_check": {"var": "confirm", "default": True}},
        ],
    }
    result = _build(spec)
    assert result.ok, result.errors
    workspace = result.workspace
    assert workspace is not None
    block = _program_do(workspace)
    assert block["fields"]["VAR"] == "setting"
    assert block["fields"]["OPTIONS"] == "A,B"
    assert "variables" not in workspace
    assert block["next"]["block"]["fields"]["VAR"] == "count"
    assert block["next"]["block"]["next"]["block"]["fields"]["DEFAULT"] == "TRUE"


def test_display_only_fields_are_ignored() -> None:
    result = _build(
        {
            "name": "n",
            "do": [
                {
                    "if": {
                        "type": "pokecon_vision_contains",
                        "fields": {
                            "TEMPLATE": "a.png",
                            "PREVIEW": "ignore",
                            "CAPOPEN": "ignore",
                        },
                    },
                    "do": [{"print": "x"}],
                }
            ],
        }
    )
    assert result.ok, result.errors
    text = json.dumps(result.workspace, ensure_ascii=False)
    assert "PREVIEW" not in text
    assert "CAPOPEN" not in text


def test_unknown_block_and_field_names_get_suggestions() -> None:
    block = _build({"name": "n", "do": [{"type": "pokecon_pres"}]})
    assert not block.ok
    assert any("pokecon_press" in error for error in block.errors)
    field = _build({"name": "n", "do": [{"press": "A", "duraton": 0.1}]})
    assert not field.ok
    assert any("duration" in error for error in field.errors)
    missing_input = _build(
        {
            "name": "n",
            "do": [
                {
                    "type": "pokecon_print",
                    "fields": {"KIND": "print"},
                    "inputs": {"TXET": "x"},
                }
            ],
        }
    )
    assert not missing_input.ok
    assert any("TEXT" in error for error in missing_input.errors)


def test_template_crop_and_threshold_are_checked() -> None:
    def conditional(fields: dict[str, Any]) -> blockly_author.BuildResult:
        return _build(
            {
                "name": "n",
                "do": [
                    {
                        "if": {
                            "type": "pokecon_vision_contains",
                            "fields": fields,
                        },
                        "do": [{"print": "x"}],
                    }
                ],
            }
        )

    empty = conditional({})
    assert not empty.ok
    assert any("TEMPLATE が空です" in error for error in empty.errors)
    crop = conditional({"TEMPLATE": "a.png", "CROP": "3,2,1,0"})
    assert not crop.ok
    assert any("x2>x1" in error for error in crop.errors)
    bad_crop = conditional({"TEMPLATE": "a.png", "CROP": "a,b,c,d"})
    assert not bad_crop.ok
    assert any("x1,y1,x2,y2" in error for error in bad_crop.errors)
    threshold = conditional({"TEMPLATE": "a.png", "THRESHOLD": 2})
    assert not threshold.ok
    assert any("0〜1" in error for error in threshold.errors)
    good = conditional({"TEMPLATE": "a.png", "CROP": "10,20,30,40"})
    assert good.ok, good.errors


def test_sub_program_errors() -> None:
    duplicate = _build(
        {
            "name": "n",
            "do": [],
            "subs": [{"name": "s", "do": []}, {"name": "s", "do": []}],
        }
    )
    assert not duplicate.ok
    assert any("重複" in error for error in duplicate.errors)
    undefined = _build({"name": "n", "do": [{"call": "nope"}]})
    assert not undefined.ok
    assert any("未定義" in error for error in undefined.errors)
    arity = _build(
        {
            "name": "n",
            "do": [{"call": "s"}],
            "subs": [{"name": "s", "args": ["a", "b"], "do": []}],
        }
    )
    assert not arity.ok
    assert any("引数の数" in error for error in arity.errors)
    cycle = _build(
        {
            "name": "n",
            "do": [],
            "subs": [
                {"name": "a", "do": [{"call": "b"}]},
                {"name": "b", "do": [{"call": "a"}]},
            ],
        }
    )
    assert not cycle.ok
    assert any("循環" in error for error in cycle.errors)


def test_break_must_be_last_in_statement_chain() -> None:
    broken = _build({"name": "n", "do": [{"break": True}, {"wait": 1}]})
    assert not broken.ok
    assert any("後ろに文を置けません" in error for error in broken.errors)
    inside_loop = _build(
        {"name": "n", "do": [{"while": True, "do": [{"break": True}]}]}
    )
    assert inside_loop.ok, inside_loop.errors


@pytest.fixture(scope="session")
def app_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("blockly_app")
    shutil.copytree(BLOCKLY, base / "assets" / "blockly")
    template = base / "Template" / "controller_check"
    template.mkdir(parents=True)
    (template / "btn_a.png").write_bytes(b"png")
    return base


def _smoke_spec(stem: str = "SmokeBlockly") -> dict[str, Any]:
    return {
        "stem": stem,
        "name": "スモーク",
        "tags": ["blockly"],
        "do": [
            {"press": "A", "duration": 0.1, "wait": 0.2},
            {"wait": 0.5},
            {
                "if": {
                    "type": "pokecon_vision_contains",
                    "fields": {"TEMPLATE": "controller_check/btn_a.png"},
                },
                "do": [{"print": "見つけた", "result": True}],
            },
            {"call": "aisatsu", "args": ["x"]},
        ],
        "subs": [
            {
                "name": "aisatsu",
                "args": ["msg"],
                "do": [{"print": {"var": "msg"}}],
            }
        ],
    }


@NEEDS_NODE
def test_check_spec_generates_real_code(app_dir: Path) -> None:
    result = blockly_author.check_spec(_smoke_spec(), app_dir)
    assert result.ok, result.errors
    assert "class BlocklyCmd(" in result.code
    assert "self.press(Button.A, duration=0.1, wait=0.2)" in result.code
    assert "self.aisatsu('x')" in result.code
    assert "def aisatsu(self, msg)" in result.code


@NEEDS_NODE
def test_check_spec_rejects_missing_template(app_dir: Path) -> None:
    spec = _smoke_spec()
    spec["do"] = [
        {
            "if": {
                "type": "pokecon_vision_contains",
                "fields": {"TEMPLATE": "missing.png"},
            },
            "do": [{"print": "x"}],
        }
    ]
    result = blockly_author.check_spec(spec, app_dir)
    assert not result.ok
    assert any("TEMPLATE の画像が見つかりません" in error for error in result.errors)


@NEEDS_NODE
def test_save_spec_writes_pair_and_records_sha(app_dir: Path) -> None:
    result = blockly_author.save_spec(_smoke_spec("SavedBlockly"), app_dir)
    assert result.status == "saved", result.message
    py_path = app_dir / "Commands" / "PythonCommands" / "SavedBlockly.py"
    json_path = app_dir / "Commands" / "PythonCommands" / "SavedBlockly.blockly.json"
    assert py_path.is_file()
    assert json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(py_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert data["pokecon"]["pySha256"] == digest


@NEEDS_NODE
def test_render_workspace_from_built_json(app_dir: Path) -> None:
    built = blockly_author.build_workspace(
        _smoke_spec(), blockly_headless.catalog(app_dir).data
    )
    assert built.ok, built.errors
    rendered = blockly_author.render_workspace(
        json.dumps(built.workspace, ensure_ascii=False), app_dir
    )
    assert rendered.ok, rendered.errors
    assert "self.wait(0.5)" in rendered.code


@NEEDS_NODE
def test_catalog_markdown_lists_fields(app_dir: Path) -> None:
    catalog = blockly_headless.catalog(app_dir)
    assert catalog.ok, catalog.errors
    from services import blockly_templates

    text = blockly_author.catalog_markdown(
        catalog.data, blockly_templates.list_image_templates(app_dir)
    )
    assert "`pokecon_press`" in text
    assert "`BUTTON` (controller" in text
    assert "`controls_if`" in text
    assert "controller_check/btn_a.png" in text


def test_node_missing_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blockly_headless.shutil, "which", lambda _name: None)
    result = blockly_author.check_spec(
        {"name": "n", "do": []}, ROOT / "SerialController"
    )
    assert not result.ok
    assert any(
        blockly_headless.NODE_MISSING_MESSAGE in error for error in result.errors
    )
