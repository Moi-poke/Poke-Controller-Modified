"""生成コードとブロックの対応付け（codeMap）と生成コード欄の行き来の検証。

ブロックが何のコードになるか・コードがどのブロックかを学べるよう、
生成コードの各行と文ブロックの対応を確かめる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from blockly_node import NEEDS_NODE, run_blockly, run_editor

pytestmark = NEEDS_NODE

SAMPLE_DIR = (
    Path(__file__).resolve().parent.parent
    / "SerialController"
    / "Commands"
    / "PythonCommands"
)


def _sample_states() -> list[tuple[str, dict[str, Any]]]:
    """同梱の作例11個を（ファイル名・読込状態）で返す。"""
    found = sorted(SAMPLE_DIR.glob("BlocklySample*.blockly.json"))
    assert len(found) == 11, f"作例は11個のはずが {len(found)} 個"
    out: list[tuple[str, dict[str, Any]]] = []
    for path in found:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = data.get("state", data) if isinstance(data, dict) else data
        assert isinstance(state, dict) and "blocks" in state, path.name
        out.append((path.name, state))
    return out


def _press(bid: str, button: str, nxt: dict[str, Any] | None = None) -> dict[str, Any]:
    """押すブロックのシリア化形（後続があればつなぐ）。"""
    block: dict[str, Any] = {
        "type": "pokecon_press",
        "id": bid,
        "fields": {"BUTTON": button, "DURATION": 0.1, "WAIT": 0.1},
    }
    if nxt is not None:
        block["next"] = {"block": nxt}
    return block


def _wait(bid: str, sec: float) -> dict[str, Any]:
    """待つブロックのシリア化形。"""
    return {"type": "pokecon_wait", "id": bid, "fields": {"SEC": sec}}


def _print_block(bid: str, text: str) -> dict[str, Any]:
    """表示ブロックのシリア化形。"""
    return {
        "type": "pokecon_print",
        "id": bid,
        "fields": {"KIND": "print"},
        "inputs": {"TEXT": {"block": {"type": "text", "fields": {"TEXT": text}}}},
    }


def _program(body: dict[str, Any]) -> dict[str, Any]:
    """プログラムで包んだ読込状態。"""
    return {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "id": "P",
                    "fields": {"NAME": "対応", "TAGS": "blockly"},
                    "inputs": {"DO": {"block": body}},
                }
            ],
        }
    }


def _assembled() -> dict[str, Any]:
    """押す→繰り返し{待つ}→表示の組み立て（対応付けの題材）。"""
    return _program(
        _press(
            "p1",
            "A",
            {
                "type": "controls_repeat_ext",
                "id": "r1",
                "inputs": {
                    "TIMES": {"block": {"type": "math_number", "fields": {"NUM": 2}}},
                    "DO": {"block": _wait("w1", 0.5)},
                },
                "next": {"block": _print_block("pr1", "hello")},
            },
        )
    )


def _preview_state() -> dict[str, Any]:
    """押す→待つの組み立て（生成コード欄の題材）。"""
    press = _press("p1", "A")
    press["next"] = {"block": _wait("w1", 0.5)}
    return _program(press)


def _codemap(state: dict[str, Any]) -> dict[str, Any]:
    """本物のBlocklyで対応付けを取り、通常生成・前後の印と一緒に返す。"""
    return run_blockly(
        f"""
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
E.syncOrphans(ws);
const before = Blockly.Python.STATEMENT_PREFIX;
const m = E.codeMap(ws, Blockly.Python);
const plain = Blockly.Python.workspaceToCode(ws);
const after = Blockly.Python.STATEMENT_PREFIX;
done({{ code: m.code, lines: m.lines, plain,
        before: before == null ? null : String(before),
        after: after == null ? null : String(after) }});
"""
    )


def test_every_bundled_sample_maps_to_exactly_the_same_code_it_would_generate() -> None:
    """作例11個のどれでも、対応付けの文字列は通常生成と1文字も違わないこと。"""
    # Given: 同梱の作例11個
    for name, state in _sample_states():
        # When: 対応付けを取る
        out = _codemap(state)
        # Then: 文字列は通常生成と完全一致し、行数分の対応がある
        assert out["code"] == out["plain"], name
        assert len(out["lines"]) == len(out["code"].split("\n")), name
        compile(out["code"], name, "exec")


def test_press_repeat_wait_and_print_lines_point_at_the_blocks_that_made_them() -> None:
    """押す→繰り返し{待つ}→表示の各行が、生んだブロックを指すこと。"""
    # Given: 押す→繰り返し{待つ}→表示の組み立て
    out = _codemap(_assembled())
    rows = out["code"].split("\n")
    ids = out["lines"]

    # When: 各行の出どころを調べる
    def owner(sub: str) -> Any:
        return ids[next(k for k, line in enumerate(rows) if sub in line)]

    # Then: 文の行は生んだブロック、import・class・def はどこにも属さない
    assert owner("self.press(") == "p1"
    assert owner("for ") == "r1"
    assert owner("self.wait(") == "w1"
    assert owner("hello") == "pr1"
    assert owner("from Commands") is None
    assert owner("class BlocklyCmd") is None
    assert owner("def do") is None


def test_codemap_leaves_the_generator_exactly_as_it_found_it() -> None:
    """対応付けの後で印の設定は元に戻り、通常生成に印が混ざらないこと。"""
    # Given/When: 対応付けを取った直後に通常生成する
    out = _codemap(_assembled())
    # Then: 印は空のまま戻り、通常生成に印は混ざらず、文字列も一致する
    assert not out["before"]
    assert out["after"] == out["before"]
    assert "@@pokecon" not in out["plain"]
    assert out["code"] == out["plain"]


def test_preview_rows_carry_block_ids_and_clicking_a_row_selects_that_block() -> None:
    """生成コード欄の行は出どころを持ち、行を押すとそのブロックが選ばれること。"""
    # Given: 押す→待つの組み立てと開いた生成コード欄
    res = run_editor(
        f"""
        boot();
        await flush(400);
        ws.clear();
        Blockly.serialization.workspaces.load({json.dumps(_preview_state())}, ws);
        ws.centerCalls = [];
        ws.centerOnBlock = function (id) {{ ws.centerCalls.push(id); }};
        els.previewtoggle.handlers.click();
        await flush(600);
        const rows = els.preview.options.filter((o) => o && typeof o.textContent === 'string');
        const byText = (sub) => rows.filter((r) => r.textContent.indexOf(sub) !== -1);
        const pressRows = byText('self.press(').filter((r) => r.attrs['data-block'] === 'p1');
        const waitRows = byText('self.wait(').filter((r) => r.attrs['data-block'] === 'w1');
        const headRows = byText('class BlocklyCmd');
        const headHasBlock = headRows.some((r) => 'data-block' in r.attrs);
        // When: 押すの行を押す（選択の記録を付けてから）
        const b = ws.getBlockById('p1');
        const selected = [];
        b.select = function () {{ selected.push(b.id); }};
        pressRows[0].handlers.click();
        done({{ pressHit: pressRows.length, waitHit: waitRows.length,
                headRows: headRows.length, headHasBlock,
                selected, centered: ws.centerCalls }});
        """
    )
    # Then: 押す・待つの行に出どころがあり、見出し行には無い
    assert res["pressHit"] == 1
    assert res["waitHit"] == 1
    assert res["headRows"] == 1
    assert res["headHasBlock"] is False
    # Then: 行を押すとそのブロックの選択と中央寄せが起きる
    assert res["selected"] == ["p1"]
    assert res["centered"] == ["p1"]


def test_selecting_a_block_highlights_its_lines_and_copy_matches_the_code() -> None:
    """ブロックを選ぶとその行が強調され、コピーは生成コードそのままであること。"""
    # Given: 押す→待つの組み立てと開いた生成コード欄
    res = run_editor(
        f"""
        boot();
        await flush(400);
        ws.clear();
        Blockly.serialization.workspaces.load({json.dumps(_preview_state())}, ws);
        els.previewtoggle.handlers.click();
        await flush(600);
        const rows = () => els.preview.options.filter((o) => o && typeof o.textContent === 'string');
        const selIds = () => rows()
          .filter((r) => (r.className || '').indexOf('sel') !== -1)
          .map((r) => r.attrs['data-block']);
        // When: 待つブロックの選択事象が届く
        const ev = new Blockly.Events.Selected(null, 'w1', ws.id);
        ev.newElementId = 'w1';
        ev.workspaceId = ws.id;
        Blockly.Events.fire(ev);
        await flush(150);
        const selNow = selIds();
        // When: 選択が外れる
        const ev2 = new Blockly.Events.Selected('w1', null, ws.id);
        ev2.newElementId = null;
        ev2.workspaceId = ws.id;
        Blockly.Events.fire(ev2);
        await flush(150);
        const selGone = selIds();
        // When: コピーする
        const copied = [];
        sandbox.navigator = {{ clipboard: {{ writeText: function (t) {{
          copied.push(t); return Promise.resolve(); }} }} }};
        els.previewcopy.handlers.click();
        await flush(300);
        const expected = Blockly.Python.workspaceToCode(ws);
        done({{ selNow, selGone, copied: copied[0] || null,
                expected, marked: (copied[0] || '').indexOf('@@pokecon') !== -1 }});
        """
    )
    # Then: 選んだブロックの行だけ強調され、外すと消える
    assert res["selNow"] == ["w1"]
    assert res["selGone"] == []
    # Then: コピーは印なしの生成コードそのまま
    assert res["copied"] == res["expected"]
    assert res["marked"] is False
