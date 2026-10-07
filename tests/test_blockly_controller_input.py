"""ボタン・十字キー・スティックの入力欄（コントローラの絵から選ぶ欄）の検証。

旧欄は「↑」が左スティックなのに十字キーと書かれ、十字キー（Hat）は
選べなかった。新しい欄は十字キーを足し、表示を実物に合わせ、保存値は
従来どおり（旧保存物がそのまま読める）にする。
"""

from __future__ import annotations

import json
from typing import Any

from blockly_node import NEEDS_NODE, run_blockly
from core import Sender
from fakes import FakeTransport
from services import blockly_run

pytestmark = NEEDS_NODE

#: 入力欄を持つブロックと欄名。
INPUT_BLOCKS = {
    "pokecon_press": "BUTTON",
    "pokecon_hold": "TARGET",
    "pokecon_hold_end": "TARGET",
    "pokecon_press_rep": "TARGET",
    "pokecon_vision_press_until": "TARGET",
    "pokecon_vision_press_until_gone": "TARGET",
}


def probe(body: str) -> dict[str, Any]:
    return run_blockly("const ws = new Blockly.Workspace();\n" + body)


def test_every_input_field_offers_the_dpad_and_both_sticks() -> None:
    """どの入力欄でも十字キー8方向・左右スティック8方向・ボタン14個を選べること。"""
    res = probe(
        f"""
const out = {{}};
for (const [type, name] of Object.entries({json.dumps(INPUT_BLOCKS)})) {{
  const f = ws.newBlock(type).getField(name);
  out[type] = {{ values: f.getOptions(false).map((o) => o[1]) }};
}}
done(out);
"""
    )
    for type_, info in res.items():
        values = info["values"]
        hats = [v for v in values if v.startswith("Hat.")]
        left = [v for v in values if v.startswith("Direction.") and ".R_" not in v]
        right = [v for v in values if v.startswith("Direction.R_")]
        buttons = [v for v in values if v not in hats + left + right]
        assert len(hats) == 8, type_
        assert "Hat.CENTER" not in hats
        assert len(left) == 8 and len(right) == 8, type_
        assert len(buttons) == 14, type_


def test_labels_say_what_the_input_really_is() -> None:
    """表示は実物どおり（左スティックを十字キーと呼ばない・素の識別子を出さない）こと。"""
    res = probe(
        """
const b = ws.newBlock('pokecon_press');
const f = b.getField('BUTTON');
const label = (v) => { f.setValue(v); return f.getText(); };
done({ up: label('Direction.UP'), rup: label('Direction.R_UP'), hat: label('Hat.TOP'),
       minus: label('MINUS'), lclick: label('LCLICK'), cap: label('CAPTURE'), a: label('A'),
       tooltip: b.getTooltip() });
"""
    )
    assert "左スティック" in res["up"]
    assert "右スティック" in res["rup"]
    assert "十字" in res["hat"]
    assert res["a"] == "A"
    for key in ("minus", "lclick", "cap"):
        assert res[key] not in ("MINUS", "LCLICK", "CAPTURE"), key
    # ツールチップも実物どおり（旧: 「十字キーは方向指定」と誤記）
    assert "十字キー" in res["tooltip"] and "スティック" in res["tooltip"]


def test_old_saved_values_load_unchanged() -> None:
    """旧保存物の値（A・Direction.UP・Button.B）がそのまま読めて、そのまま保存されること。"""
    state = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {"type": "pokecon_press", "id": "a", "fields": {"BUTTON": "A"}},
                {
                    "type": "pokecon_press",
                    "id": "b",
                    "y": 80,
                    "fields": {"BUTTON": "Direction.UP"},
                },
                {
                    "type": "pokecon_hold",
                    "id": "c",
                    "y": 160,
                    "fields": {"TARGET": "Button.B"},
                },
            ],
        }
    }
    res = probe(
        f"""
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
const saved = Blockly.serialization.workspaces.save(ws).blocks.blocks;
done({{ fields: saved.map((b) => b.fields) }});
"""
    )
    fields = res["fields"]
    assert fields[0]["BUTTON"] == "A"
    assert fields[1]["BUTTON"] == "Direction.UP"
    assert fields[2]["TARGET"] == "Button.B"


def test_every_option_is_reachable_from_the_controller_picture() -> None:
    """絵の配置表が全候補を1回ずつ持つこと（絵から選べない入力を作らない）。"""
    res = probe(
        """
const P = Blockly.PokeconInput;
const f = ws.newBlock('pokecon_hold').getField('TARGET');
const g = ws.newBlock('pokecon_press').getField('BUTTON');
done({ layout: P.LAYOUT.map((it) => it.key),
       hold: f.getOptions(false).map((o) => o[1]),
       press: g.getOptions(false).map((o) => o[1]),
       holdKeys: f.getOptions(false).map((o) => P.keyOf(o[1])),
       pressKeys: g.getOptions(false).map((o) => P.keyOf(o[1])) });
"""
    )
    layout = res["layout"]
    assert len(layout) == len(set(layout)) == 38
    assert sorted(res["holdKeys"]) == sorted(layout)
    assert sorted(res["pressKeys"]) == sorted(layout)


def _press_code(value: str, field: str = "BUTTON", block: str = "pokecon_press") -> str:
    state = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "id": "P",
                    "fields": {"NAME": "pad", "TAGS": "blockly"},
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": block,
                                "id": "x",
                                "fields": {field: value, "DURATION": 0.02, "WAIT": 0},
                            }
                        }
                    },
                }
            ],
        }
    }
    res = probe(
        f"""
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
done({{ plain: Blockly.Python.workspaceToCode(ws),
        trial: E.trialCode(ws, Blockly.Python, {{}}).code }});
"""
    )
    return str(res["plain"]) + "\n#---\n" + str(res["trial"])


def _run(code: str) -> list[str]:
    session = blockly_run.TrialSession(1)
    cls, errors = blockly_run.build_trial_class(code, session)
    assert cls is not None, errors
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    import threading

    done = threading.Event()
    cmd = cls()
    assert cmd.start(sender, done.set) is True
    assert done.wait(10)
    assert session.result == "完了", session.error
    return list(transport.rows)


def test_dpad_press_generates_hat_and_really_moves_the_dpad() -> None:
    """十字キーを選ぶと Hat で押すコードになり、実際に線へ十字キーが出ること。"""
    plain, trial = _press_code("Hat.TOP").split("\n#---\n")
    # Then: 保存用コードは Hat.TOP を押し、Hat を読み込む
    assert "self.press(Hat.TOP," in plain
    assert (
        "from Commands.Keys import" in plain
        and "Hat" in plain.split("import", 2)[1] + plain
    )
    # Then: 走らせると、左スティック↑とは違う行（十字キー）が線へ出る
    hat_rows = _run(trial)
    stick_rows = _run(_press_code("Direction.UP").split("\n#---\n")[1])
    assert hat_rows and stick_rows
    assert hat_rows != stick_rows


def test_dpad_works_in_hold_too() -> None:
    """押し続ける系（hold）でも十字キーを選べ、Hat で生成されること。"""
    state = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "id": "P",
                    "fields": {"NAME": "pad", "TAGS": "blockly"},
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": "pokecon_hold",
                                "fields": {"TARGET": "Hat.LEFT", "WAIT": 0},
                                "next": {
                                    "block": {
                                        "type": "pokecon_hold_end",
                                        "fields": {"TARGET": "Hat.LEFT"},
                                    }
                                },
                            }
                        }
                    },
                }
            ],
        }
    }
    res = probe(
        f"""
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
done({{ code: Blockly.Python.workspaceToCode(ws) }});
"""
    )
    code = res["code"]
    assert "self.hold(Hat.LEFT," in code
    assert "self.holdEnd(Hat.LEFT)" in code
    assert "Hat" in code.split("class", 1)[0]


def test_hat_in_a_comment_does_not_add_an_unused_import() -> None:
    """コメントや文字列に「Hat.」と書いただけでは Hat を読み込まないこと。"""
    state = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "id": "P",
                    "fields": {"NAME": "c", "TAGS": "blockly"},
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": "pokecon_comment",
                                "fields": {"TEXT": "Hat.TOP と Direction.UP のメモ"},
                            }
                        }
                    },
                }
            ],
        }
    }
    res = probe(
        f"""
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
done({{ code: Blockly.Python.workspaceToCode(ws) }});
"""
    )
    head = res["code"].split("class", 1)[0]
    assert "Hat" not in head and "Direction" not in head
    assert "Hat.TOP" in res["code"]


def test_hash_in_a_template_name_keeps_the_needed_import() -> None:
    """画像名に「#」があっても、その後ろのキー名（Hat.TOP）を見落として import を欠かないこと。"""
    state = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "id": "P",
                    "fields": {"NAME": "h", "TAGS": "blockly"},
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": "pokecon_vision_press_until",
                                "fields": {
                                    "TEMPLATE": "img/#1.png",
                                    "TARGET": "Hat.TOP",
                                },
                            }
                        }
                    },
                }
            ],
        }
    }
    res = probe(
        f"""
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
done({{ code: Blockly.Python.workspaceToCode(ws) }});
"""
    )
    code = res["code"]
    assert "Hat.TOP" in code
    assert "Hat" in code.split("class", 1)[0]
