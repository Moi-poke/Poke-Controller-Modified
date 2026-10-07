"""Blockly試し実行の検証（本物のBlocklyで生成→本物のコマンドとして走らせる）。

編集画面の「実行」「このブロックだけ試す」は、保存せずに今の組み立てを
走らせ、実行中のブロックを光らせる。生成は node の vm で本物の Blockly、
実行は services.blockly_run が作った本物の PythonCommand を偽の線
（FakeTransport）越しに走らせて確かめる。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from blockly_node import NEEDS_NODE, run_blockly
from core import Sender
from fakes import FakeTransport
from services import blockly_run

pytestmark = NEEDS_NODE


def _press(bid: str, button: str, nxt: dict[str, Any] | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "pokecon_press",
        "id": bid,
        "fields": {"BUTTON": button, "DURATION": 0.02, "WAIT": 0},
    }
    if nxt is not None:
        block["next"] = {"block": nxt}
    return block


def _print(bid: str, text_block: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "pokecon_print",
        "id": bid,
        "fields": {"KIND": "print"},
        "inputs": {"TEXT": {"block": text_block}},
    }


def _text(value: str) -> dict[str, Any]:
    return {"type": "text", "fields": {"TEXT": value}}


def _program(
    body: dict[str, Any] | None, extra: list[dict[str, Any]] | None = None
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
    return {"blocks": {"languageVersion": 0, "blocks": [program, *(extra or [])]}}


#: press A → 2回繰り返し{wait 0} → 表示 "hello"
BASIC = _program(
    _press(
        "p1",
        "A",
        {
            "type": "controls_repeat_ext",
            "id": "r1",
            "inputs": {
                "TIMES": {"block": {"type": "math_number", "fields": {"NUM": 2}}},
                "DO": {
                    "block": {"type": "pokecon_wait", "id": "w1", "fields": {"SEC": 0}}
                },
            },
            "next": {"block": _print("pr1", _text("hello"))},
        },
    )
)


def trial(state: dict[str, Any], opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """本物のBlocklyで試し実行用のコードを作る（作った後の状態も返す）。"""
    return run_blockly(
        f"""
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({json.dumps(state)}, ws);
E.syncOrphans(ws);
Blockly.Events.setGroup(false);
const undoBefore = ws.getUndoStack().length;
const before = E.stateKey(ws);
const r = E.trialCode(ws, Blockly.Python, {json.dumps(opts or {})});
const plain = Blockly.Python.workspaceToCode(ws);
const disabled = ws.getAllBlocks(false).filter((b) => !b.isEnabled()).map((b) => b.id).sort();
done({{ r, plain, disabled, same: before === E.stateKey(ws),
        undoGrew: ws.getUndoStack().length !== undoBefore }});
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


# -- 生成（ブラウザ側） --------------------------------------------------------


def test_trial_code_marks_every_statement_so_the_running_block_can_be_lit() -> None:
    """試し実行のコードは各ブロックの手前に目印を持ち、保存用のコードは汚さないこと。"""
    # Given/When: 基本の組み立てを試し実行用に生成する
    out = trial(BASIC)
    code = out["r"]["code"]
    # Then: 押す・繰り返し・待つ・表示の手前に目印がある
    for bid in ("p1", "r1", "w1", "pr1"):
        assert f"_pokecon_step(self, '{bid}')" in code, bid
    # Then: 目印は do() の中だけで、モジュール直下・プログラム自身には付かない
    assert "_pokecon_step(self, 'P')" not in code
    assert all(not line.startswith("_pokecon_step") for line in code.splitlines()), code
    # Then: 保存用の生成は目印を含まない（後片付けされている）
    assert "_pokecon_step" not in out["plain"]
    assert out["same"] is True and out["undoGrew"] is False


def test_trying_only_one_block_runs_just_that_block() -> None:
    """「このブロックだけ試す」は選んだブロック（中身ごと）だけを do() に置くこと。"""
    # Given/When: 繰り返しブロックだけを試す
    code = trial(BASIC, {"blockId": "r1", "only": True})["r"]["code"]
    # Then: 繰り返しとその中身は入り、前後の押す・表示は入らない
    assert "'r1'" in code and "'w1'" in code
    assert "'p1'" not in code and "'pr1'" not in code
    assert "self.press(" not in code


def test_trying_from_a_block_runs_it_and_everything_after() -> None:
    """「ここから下を試す」は選んだブロックと後続を走らせ、前は走らせないこと。"""
    # Given/When: 繰り返しから下を試す
    code = trial(BASIC, {"blockId": "r1"})["r"]["code"]
    # Then: 繰り返し・表示は入り、先頭の押すは入らない
    assert "'r1'" in code and "'pr1'" in code
    assert "'p1'" not in code


def test_a_block_left_outside_the_program_can_still_be_tried() -> None:
    """プログラムの外（灰色）に置いたブロックも試せ、試した後も灰色のまま残ること。"""
    # Given: プログラムの外に press B を置く
    state = _program(None, [{**_press("loose", "B"), "x": 300, "y": 300}])
    # When: それだけを試す
    out = trial(state, {"blockId": "loose", "only": True})
    # Then: B を押すコードになる
    assert "Button.B" in out["r"]["code"]
    assert "'loose'" in out["r"]["code"]
    # Then: 試した後も無効のまま・保存物と取り消し履歴は変わらない
    assert out["disabled"] == ["loose"]
    assert out["same"] is True and out["undoGrew"] is False


def test_a_value_block_cannot_be_tried_alone() -> None:
    """値ブロック（文字など）は単独で走らせられないため、理由を返すこと。"""
    state = _program(None, [{**_text("x"), "id": "t1", "x": 300, "y": 0}])
    out = trial(state, {"blockId": "t1", "only": True})
    assert "code" not in out["r"]
    assert "値" in out["r"]["error"]


def test_blocks_inside_a_subroutine_with_arguments_are_refused() -> None:
    """引数のあるサブルーチンの中身は単独だと引数が無く落ちるため、先に断ること。"""
    sub = {
        "type": "pokecon_sub_def",
        "id": "S",
        "x": 300,
        "y": 0,
        "fields": {"NAME": "go", "ARGS": "n"},
        "inputs": {"DO": {"block": _press("in_sub", "A")}},
    }
    out = trial(_program(None, [sub]), {"blockId": "in_sub", "only": True})
    assert "code" not in out["r"]
    assert "引数" in out["r"]["error"]


def test_trial_without_a_program_explains_why() -> None:
    """プログラムブロックが無いときは、生成せずに理由を返すこと。"""
    state = {"blocks": {"languageVersion": 0, "blocks": []}}
    out = trial(state)
    assert "code" not in out["r"]
    assert "プログラム" in out["r"]["error"]


# -- 実行（本体側） ------------------------------------------------------------


def test_trial_run_presses_buttons_and_reports_each_block_in_order() -> None:
    """試し実行は本物のコマンドとして走り、通ったブロックを順に知らせること。"""
    code = trial(BASIC)["r"]["code"]
    session = make_session()
    seen: list[str] = []
    session.on_step = seen.append
    # When: 偽の線で最後まで走らせる
    cmd, transport = run_trial(code, session)
    # Then: 押す→繰り返し→待つ×2→表示の順に通った
    assert [s for s in seen if s] == ["p1", "r1", "w1", "r1", "w1", "r1", "pr1"]
    # Then: A を押した行が線へ出ている
    assert transport.rows, "線へ何も出ていない"
    # Then: 表示はログとして拾われ、結果は完了
    assert any("hello" in text for _, text in session.logs_since(0))
    assert session.result == "完了"
    assert str(cmd.NAME).startswith("[試し]")
    assert getattr(cmd, "POKECON_TRIAL", False) is True


def test_breakpoint_pauses_before_the_block_and_step_moves_one_block() -> None:
    """区切り（ブレークポイント）の手前で止まり、「1つ進む」で1ブロックだけ進むこと。"""
    code = trial(BASIC)["r"]["code"]
    session = make_session()
    session.breakpoints = frozenset({"pr1"})

    def control(cmd: Any) -> None:
        # Then: 表示の手前で一時停止している
        assert wait_until(lambda: cmd.isPaused() and session.block_id == "pr1")
        assert not any("hello" in t for _, t in session.logs_since(0))
        # When: 区切りを外して1つ進む（表示を実行して終わる）
        session.breakpoints = frozenset()
        session.request_step(cmd)

    run_trial(code, session, control)
    assert any("hello" in t for _, t in session.logs_since(0))
    assert session.result == "完了"


def test_step_from_a_pause_stops_again_at_the_next_block() -> None:
    """一時停止中の「1つ進む」は、次のブロックの手前で再び止まること。"""
    code = trial(BASIC)["r"]["code"]
    session = make_session()
    session.breakpoints = frozenset({"r1"})

    def control(cmd: Any) -> None:
        assert wait_until(lambda: cmd.isPaused() and session.block_id == "r1")
        session.breakpoints = frozenset()
        # When: 1つ進む
        session.request_step(cmd)
        # Then: 繰り返しの中の待つで止まる
        assert wait_until(lambda: cmd.isPaused() and session.block_id == "w1")
        # 後始末: 再開して最後まで
        cmd.resume()

    run_trial(code, session, control)
    assert session.result == "完了"


def test_stop_ends_even_a_loop_without_any_wait() -> None:
    """待ちを含まない無限ループでも、停止で抜けて「停止」になること。"""
    loop = _program(
        {
            "type": "controls_whileUntil",
            "id": "loop",
            "fields": {"MODE": "WHILE"},
            "inputs": {
                "BOOL": {"block": {"type": "logic_boolean", "fields": {"BOOL": "TRUE"}}}
            },
        }
    )
    code = trial(loop)["r"]["code"]
    session = make_session()

    def control(cmd: Any) -> None:
        assert wait_until(lambda: session.block_id == "loop")
        session.stop_requested = True
        cmd.end()

    run_trial(code, session, control, timeout=5.0)
    assert session.result == "停止"


def test_runtime_error_points_at_the_failing_block() -> None:
    """実行時の例外は、落ちたブロックと理由を持って「エラー」で終わること。"""
    division = {
        "type": "math_arithmetic",
        "fields": {"OP": "DIVIDE"},
        "inputs": {
            "A": {"block": {"type": "math_number", "fields": {"NUM": 1}}},
            "B": {"block": {"type": "math_number", "fields": {"NUM": 0}}},
        },
    }
    state = _program(_press("p1", "A", _print("bad", division)))
    code = trial(state)["r"]["code"]
    session = make_session()
    run_trial(code, session)
    assert session.result == "エラー"
    assert session.error_block == "bad"
    assert "ZeroDivisionError" in session.error


def test_trial_refuses_code_that_import_checks_reject() -> None:
    """保存と同じ検査に落ちるコード（許可外のimport）は、クラスを作らず理由を返すこと。"""
    code = (
        "import os\n"
        "from Commands.PythonCommandBase import PythonCommand\n\n\n"
        "class C(PythonCommand):\n"
        '    NAME = "x"\n\n'
        "    def do(self) -> None:\n"
        "        pass\n"
    )
    # os は標準なので通る。公開面外の Commands 経路で落とす
    bad = code.replace("import os\n", "from Commands.PythonCommands import secret\n")
    cls, errors = blockly_run.build_trial_class(bad, make_session())
    assert cls is None
    assert errors and "Commands" in errors[0]


def test_snapshot_reports_progress_for_the_editor() -> None:
    """受け口が返す様子（今のブロック・結果・新しいログ）を1つの辞書で返すこと。"""
    code = trial(BASIC)["r"]["code"]
    session = make_session()
    run_trial(code, session)
    snap = session.snapshot(since=0)
    assert snap["runId"] == 1
    assert snap["result"] == "完了"
    assert snap["running"] is False
    assert snap["logs"] and snap["logs"][-1][0] >= 1
    # 既読の続きからは新しいログだけ返す
    last = snap["logs"][-1][0]
    assert session.snapshot(since=last)["logs"] == []


def _forever_loop() -> dict[str, Any]:
    return _program(
        {
            "type": "controls_whileUntil",
            "id": "loop",
            "fields": {"MODE": "WHILE"},
            "inputs": {
                "BOOL": {"block": {"type": "logic_boolean", "fields": {"BOOL": "TRUE"}}}
            },
        }
    )


def test_stop_from_the_main_window_is_reported_as_stopped() -> None:
    """本体の停止ボタン（編集画面を通らない停止）でも、結果は「完了」でなく「停止」になること。"""
    code = trial(_forever_loop())["r"]["code"]
    session = make_session()

    def control(cmd: Any) -> None:
        assert wait_until(lambda: session.block_id == "loop")
        # When: 本体側の停止（stop_requested は立てない）
        cmd.end()

    run_trial(code, session, control, timeout=5.0)
    assert session.result == "停止"


def test_finish_block_still_counts_as_completed() -> None:
    """「正常終了する」ブロックでの終了は「完了」のままであること（停止と取り違えない）。"""
    state = _program(_press("p1", "A", {"type": "pokecon_finish", "id": "fin"}))
    code = trial(state)["r"]["code"]
    session = make_session()
    run_trial(code, session)
    assert session.result == "完了"


def _loop_with_break() -> dict[str, Any]:
    return _program(
        {
            "type": "controls_repeat_ext",
            "id": "rep",
            "inputs": {
                "TIMES": {"block": {"type": "math_number", "fields": {"NUM": 3}}},
                "DO": {
                    "block": {
                        **_press("in", "A"),
                        "next": {
                            "block": {
                                "type": "controls_flow_statements",
                                "id": "brk",
                                "fields": {"FLOW": "BREAK"},
                            }
                        },
                    }
                },
            },
        }
    )


def test_break_cannot_be_tried_outside_its_loop() -> None:
    """「中断」を単独・後続付きで試すと文法エラーのコードになるため、先に理由を返すこと。"""
    only = trial(_loop_with_break(), {"blockId": "brk", "only": True})["r"]
    below = trial(_loop_with_break(), {"blockId": "in"})["r"]
    assert "code" not in only and "繰り返し" in only["error"]
    assert "code" not in below and "繰り返し" in below["error"]
    # 対照: 繰り返しごと試すのは通り、コードとして読める
    whole = trial(_loop_with_break(), {"blockId": "rep", "only": True})["r"]
    compile(whole["code"], "<t>", "exec")


def test_trial_refuses_trace_ids_that_are_not_plain_strings() -> None:
    """目印の引数が素の文字列でない（手で書き換えたID等の注入）コードは走らせないこと。"""
    code = (
        "from Commands.PythonCommandBase import PythonCommand\n\n\n"
        "class C(PythonCommand):\n"
        '    NAME = "x"\n\n'
        "    def do(self) -> None:\n"
        "        _pokecon_step(self, 'a' + str(1))\n"
    )
    cls, errors = blockly_run.build_trial_class(code, make_session())
    assert cls is None
    assert errors and "目印" in errors[0]
    # 対照: 素の文字列なら通る
    ok = code.replace("'a' + str(1)", "'a1'")
    assert blockly_run.build_trial_class(ok, make_session())[0] is not None
