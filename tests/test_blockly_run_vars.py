"""試し実行中の変数表示（Scratchの変数モニター相当）の検証。

区切りで止めたときに変数の今の値が見えることを、本物のBlocklyで生成→
偽の線で本物のコマンドとして走らせる手法で確かめる。画面側は
editor.html の inline script を最小DOMで起動して確かめる。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from blockly_node import NEEDS_NODE, run_blockly, run_editor
from core import Sender
from fakes import FakeTransport
from services import blockly_run

pytestmark = NEEDS_NODE


def _var_set(bid: str, var_id: str, value_block: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "variables_set",
        "id": bid,
        "fields": {"VAR": {"id": var_id}},
        "inputs": {"VALUE": {"block": value_block}},
    }


def _var_get(var_id: str) -> dict[str, Any]:
    return {"type": "variables_get", "fields": {"VAR": {"id": var_id}}}


def _num(n: float) -> dict[str, Any]:
    return {"type": "math_number", "fields": {"NUM": n}}


def _text(value: str) -> dict[str, Any]:
    return {"type": "text", "fields": {"TEXT": value}}


def _press(bid: str, button: str) -> dict[str, Any]:
    return {
        "type": "pokecon_press",
        "id": bid,
        "fields": {"BUTTON": button, "DURATION": 0.02, "WAIT": 0},
    }


def _program_with_vars(
    body: dict[str, Any] | None, variables: list[dict[str, str]]
) -> dict[str, Any]:
    program: dict[str, Any] = {
        "type": "pokecon_program",
        "id": "P",
        "x": 0,
        "y": 0,
        "fields": {"NAME": "試し", "TAGS": "blockly"},
    }
    if body is not None:
        program["inputs"] = {"DO": {"block": body}}
    return {
        "variables": variables,
        "blocks": {"languageVersion": 0, "blocks": [program]},
    }


def trial(state: dict[str, Any], opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """本物のBlocklyで試し実行用のコードを作る（作った後の状態も返す）。"""
    return run_blockly(
        f"""
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
E.syncOrphans(ws);
Blockly.Events.setGroup(false);
const r = E.trialCode(ws, Blockly.Python, {json.dumps(opts or {})});
done({{ r }});
"""
    )


def make_session() -> blockly_run.TrialSession:
    return blockly_run.TrialSession(run_id=1)


def run_trial(
    code: str,
    session: blockly_run.TrialSession,
    control: Callable[[Any], None] | None = None,
    timeout: float = 10.0,
) -> tuple[Any, FakeTransport]:
    """試し実行のクラスを作り、偽の線で最後まで走らせる。"""
    cls, errors = blockly_run.build_trial_class(code, session)
    assert errors == [] and cls is not None, errors
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    cmd = cls()
    done = threading.Event()
    assert cmd.start(sender, done.set) is True
    if control is not None:
        control(cmd)
    assert done.wait(timeout), "試し実行が終わらない"
    return cmd, transport


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _vars_dict(session: blockly_run.TrialSession) -> dict[str, str]:
    return dict(session.snapshot()["vars"])


def test_breakpoint_snapshot_shows_japanese_variable_value() -> None:
    """区切りで止めた時点の様子に日本語の変数名と値が入ること。"""
    # Given: 変数「回数」に 3 を入れてから押す組み立て
    first = _var_set("s1", "回数", _num(3))
    press = _press("p1", "A")
    first["next"] = {"block": press}
    state = _program_with_vars(first, [{"name": "回数", "id": "回数"}])
    code = trial(state)["r"]["code"]
    session = make_session()
    session.breakpoints = frozenset({"p1"})

    def control(cmd: Any) -> None:
        # When: 区切りで止まるまで待ち、値を読んでから動かす
        assert wait_until(lambda: cmd.isPaused() and session.block_id == "p1")
        assert _vars_dict(session).get("回数") == "3"
        session.breakpoints = frozenset()
        cmd.resume()

    # Then: 最後まで走る
    run_trial(code, session, control)
    assert session.result == "完了"


def test_variable_value_advances_each_time_it_stops_in_a_loop() -> None:
    """繰り返しの中で変数を増やすと、止めるたびに値が進むこと。"""
    # Given: 回数=0 から 3 回まわし、中で +1 して待つ組み立て
    inc_value: dict[str, Any] = {
        "type": "math_arithmetic",
        "fields": {"OP": "ADD"},
        "inputs": {
            "A": {"block": _var_get("回数")},
            "B": {"block": _num(1)},
        },
    }
    inc = _var_set("inc", "回数", inc_value)
    wait_block: dict[str, Any] = {
        "type": "pokecon_wait",
        "id": "w1",
        "fields": {"SEC": 0},
    }
    inc["next"] = {"block": wait_block}
    loop: dict[str, Any] = {
        "type": "controls_repeat_ext",
        "id": "r1",
        "inputs": {
            "TIMES": {"block": _num(3)},
            "DO": {"block": inc},
        },
    }
    first = _var_set("s0", "回数", _num(0))
    first["next"] = {"block": loop}
    state = _program_with_vars(first, [{"name": "回数", "id": "回数"}])
    code = trial(state)["r"]["code"]
    session = make_session()
    session.breakpoints = frozenset({"w1"})
    seen: list[str] = []

    def control(cmd: Any) -> None:
        # When: 回るたびに止まって値を控え、動かす（3回分）
        for _ in range(3):
            assert wait_until(lambda: cmd.isPaused() and session.block_id == "w1")
            seen.append(_vars_dict(session).get("回数", "?"))
            cmd.resume()
        # Then: 3回とも止まった
        assert seen == ["1", "2", "3"]

    run_trial(code, session, control)
    assert session.result == "完了"


def test_snapshot_hides_non_variable_values_and_truncates_long_strings() -> None:
    """命令・型・関数・self・_始まりは出さず、長い文字は80文字で切ること。"""
    # Given: 通常変数・_始まり・100文字の変数を使う組み立て
    long_text = "あ" * 100
    s1 = _var_set("s1", "回数", _num(3))
    s2 = _var_set("s2", "_ないしょ", _num(9))
    s3 = _var_set("s3", "長文", _text(long_text))
    press = _press("p1", "A")
    s1["next"] = {"block": s2}
    s2["next"] = {"block": s3}
    s3["next"] = {"block": press}
    state = _program_with_vars(
        s1,
        [
            {"name": "回数", "id": "回数"},
            {"name": "_ないしょ", "id": "_ないしょ"},
            {"name": "長文", "id": "長文"},
        ],
    )
    code = trial(state)["r"]["code"]
    session = make_session()
    session.breakpoints = frozenset({"p1"})

    def control(cmd: Any) -> None:
        # When: 区切りで止まる
        assert wait_until(lambda: cmd.isPaused() and session.block_id == "p1")
        # Then: 通常変数は出て、_始まり・selfは出ない
        got = _vars_dict(session)
        assert got.get("回数") == "3"
        assert "_ないしょ" not in got
        assert "self" not in got
        assert not any(name.startswith("_") for name in got)
        # Then: 命令・型・関数の類は名前に出ない（値は表示用の文字だけ）
        for name in got:
            assert name in ("回数", "長文"), name
        # Then: 長い文字は80文字で切られて末尾が…になる
        long_val = got.get("長文", "")
        assert long_val.endswith("…")
        assert len(long_val) == 81
        session.breakpoints = frozenset()
        cmd.resume()

    run_trial(code, session, control)
    assert session.result == "完了"


#: 画面検証の前置き。実行の受け口は記録つきの代役で返す。
_EDITOR_SETUP = """
routes.list = { stems: [], appId: 'app1' };
store['pokecon.blockly.draft.v1.app1'] = JSON.stringify({ stem: 'Run1', dirty: true,
  state: { blocks: { languageVersion: 0, blocks: [
    { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' }, inputs: { DO: { block:
      { type: 'pokecon_press', id: 'p1', fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } } ] } } });
routes.run = { ok: true, message: '試し実行を始めました', runId: 1 };
routes['run/control'] = { ok: true, message: '' };
routes['run/state'] = { ok: true, available: true, runId: 1, running: true, paused: false,
  result: '', blockId: 'p1', error: '', errorBlock: '', logs: [], vars: [], busy: true, otherBusy: false };
async function settle() {
  routes['run/state'] = Object.assign({}, routes['run/state'], { running: false, result: '完了' });
  await flush(700);
}
// 変数欄の中身を読む（見出し・案内・表の行）。最小DOMでは表は options に積まれる。
function readVars() {
  const box = els.runvars;
  if (!box) { return { missing: true }; }
  const all = [];
  function walk(el) {
    all.push([el.textContent || '', el.className || '']);
    (el.options || []).forEach(walk);
  }
  walk(box);
  return { head: box.textContent || '', kids: (box.options || []).map((k) => k.textContent),
           cells: all };
}
"""


def test_editor_shows_vars_and_marks_changed_rows() -> None:
    """様子が vars を返すと名前と値が出て、変わった行に changed が付くこと。"""
    res = run_editor(
        _EDITOR_SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        // When: 変数が1件届く
        routes['run/state'] = Object.assign({}, routes['run/state'],
          { vars: [['回数', '3']] });
        await flush(500);
        const first = readVars();
        // When: 値が変わる
        routes['run/state'] = Object.assign({}, routes['run/state'],
          { vars: [['回数', '4']] });
        await flush(500);
        const second = readVars();
        await settle();
        done({ first, second });
        """
    )
    # Then: 名前と値が出る
    first_cells = [c[0] for c in res["first"]["cells"]]
    assert "回数" in first_cells
    assert "3" in first_cells
    # Then: 値が変わった行に changed が付く（変数は1行だけのため、changed 行がその行）
    flags = [(c[0], c[1]) for c in res["second"]["cells"]]
    assert any(cls == "changed" for _text, cls in flags)
    assert any(text == "4" for text, _cls in flags)


def test_editor_shows_a_guide_when_there_are_no_vars() -> None:
    """変数が0件のときは案内文が出ること。"""
    res = run_editor(
        _EDITOR_SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        // When: 変数なしのまま様子を取りに行く
        routes['run/state'] = Object.assign({}, routes['run/state'], { vars: [] });
        await flush(500);
        const got = readVars();
        await settle();
        done({ got });
        """
    )
    # Then: 案内文が出る
    joined = " ".join(res["got"]["kids"] + [c[0] for c in res["got"]["cells"]])
    assert "まだ変数はありません" in joined
