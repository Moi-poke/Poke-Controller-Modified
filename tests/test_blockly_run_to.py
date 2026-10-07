"""「ここまで実行」と試し実行のキー操作の検証。

デバッガの定番操作に近づけるための追加分。RED（未実装）では落ち、
実装後に通る。node が無ければ node 依存の検証は飛ばす。
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from blockly_node import NEEDS_NODE, run_editor
from test_blockly_run import BASIC, make_session, run_trial, trial, wait_until
from test_blockly_run_server import FakeHost, post, trial_code, wait_state
from ui import blockly_editor

#: このファイル直下の node 検証に使う印。ファイル読込の検証には要らない。
NODE_ONLY = NEEDS_NODE

ROOT = Path(__file__).resolve().parent.parent
EDITOR_HTML = ROOT / "SerialController" / "assets" / "blockly" / "editor.html"
DOCS = ROOT / "docs" / "BLOCKLY_EDITOR.md"


# -- 本体側：run_to は1回だけ止める -------------------------------------------


@NODE_ONLY
def test_run_to_pauses_once_before_the_block_then_never_again() -> None:
    """run_to のブロックの手前で1回だけ止まり、繰返しの2周目は止まらないこと。"""
    # Given: 押す→2回繰返し{待つ}→表示の組み立て
    code = trial(BASIC)["r"]["code"]
    session = make_session()
    # When: 繰返しの中の待つを「ここまで実行」にする
    session.run_to = "w1"
    pauses: list[str] = []

    def control(cmd: Any) -> None:
        # Then: 待つの手前で止まる
        assert wait_until(lambda: cmd.isPaused() and session.block_id == "w1")
        pauses.append(session.block_id)
        cmd.resume()
        # Then: 2周目の待ち・表示では止まらずに終わる
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            if session.result:
                break
            if cmd.isPaused():
                pauses.append(session.block_id)
                cmd.resume()
            time.sleep(0.02)

    run_trial(code, session, control)
    # Then: 1回だけ止まり、再開で最後まで走って完了
    assert pauses == ["w1"]
    assert session.result == "完了"


# -- 受け口：/run の runTo ------------------------------------------------------


@pytest.fixture()
def trial_server() -> Iterator[tuple[str, FakeHost]]:
    """受け口＋偽の本体＋GUIスレッド役の糸（runTo 検証用）。"""
    host = FakeHost()
    blockly_editor._set_run_host(host)
    stop = threading.Event()

    def gui_loop() -> None:
        while not stop.is_set():
            blockly_editor.drain_gui_calls()
            time.sleep(0.01)

    gui = threading.Thread(target=gui_loop, daemon=True)
    gui.start()
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{httpd.server_address[1]}", host
    cmd = host.runner.running_command
    if cmd is not None and not host.runner.done.is_set():
        cmd.end()
        host.runner.done.wait(5)
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5.0)
    stop.set()
    gui.join(timeout=5.0)
    blockly_editor._set_run_host(None)


PROGRAM = json.dumps(
    {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "id": "P",
                    "fields": {"NAME": "試し", "TAGS": "blockly"},
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": "pokecon_press",
                                "id": "p1",
                                "fields": {
                                    "BUTTON": "A",
                                    "DURATION": 0.02,
                                    "WAIT": 0,
                                },
                                "next": {
                                    "block": {
                                        "type": "pokecon_print",
                                        "id": "pr1",
                                        "fields": {"KIND": "print"},
                                        "inputs": {
                                            "TEXT": {
                                                "block": {
                                                    "type": "text",
                                                    "fields": {"TEXT": "hello"},
                                                }
                                            }
                                        },
                                    }
                                },
                            }
                        }
                    },
                }
            ],
        }
    }
)


@NODE_ONLY
def test_run_with_run_to_stops_before_that_block(
    trial_server: tuple[str, FakeHost],
) -> None:
    """/run に runTo を渡すと、そのブロックの手前で止まること。"""
    # Given: 表示ブロックを「ここまで実行」にする
    host, _ = trial_server
    reply = post(host, "/run", {"code": trial_code(PROGRAM), "runTo": "pr1"})
    assert reply["ok"] is True, reply
    # Then: 様子の blockId がそのブロック
    snap = wait_state(host, lambda s: s["paused"] and s["blockId"] == "pr1")
    assert snap["running"] is True
    # When: 再開する
    assert post(host, "/run/control", {"action": "resume"})["ok"]
    # Then: 最後まで走って完了
    assert wait_state(host, lambda s: s["result"])["result"] == "完了"


# -- 編集画面：右クリック・キー操作 ---------------------------------------------

#: プログラム（press A → wait）を控えから戻す前置き。ブロックIDは固定。
_SETUP = """
routes.list = { stems: [], appId: 'app1' };
store['pokecon.blockly.draft.v1.app1'] = JSON.stringify({ stem: 'Run1', dirty: true,
  state: { blocks: { languageVersion: 0, blocks: [
    { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' }, inputs: { DO: { block:
      { type: 'pokecon_press', id: 'p1', fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 },
        next: { block: { type: 'pokecon_wait', id: 'w1', fields: { SEC: 1 } } } } } } },
    { type: 'pokecon_press', id: 'loose', x: 400, y: 0,
      fields: { BUTTON: 'B', DURATION: 0.1, WAIT: 0.1 } } ] } } });
const highlights = [];
ws.highlightBlock = (id) => { highlights.push(id); };
routes.run = { ok: true, message: '試し実行を始めました', runId: 1 };
routes['run/control'] = { ok: true, message: '' };
routes['run/state'] = { ok: true, available: true, runId: 1, running: true, paused: false,
  result: '', blockId: 'p1', error: '', errorBlock: '', logs: [], busy: true, otherBusy: false };
const runCalls = () => calls.filter((c) => String(c.url).indexOf('./run') === 0);
// 様子の取りに行きを止める（走ったままだと node が終わらない）。
async function settle() {
  routes['run/state'] = Object.assign({}, routes['run/state'], { running: false, result: '停止' });
  await flush(700);
}
// 偽のキー事象。preventDefault が呼ばれたかを記録する。
function fakeKey(over) {
  const prevented = [];
  const ev = Object.assign({ key: '', ctrlKey: false, shiftKey: false, metaKey: false,
    target: { tagName: 'DIV' },
    preventDefault: () => { prevented.push(true); } }, over || {});
  ev.prevented = prevented;
  return ev;
}
"""


@NODE_ONLY
def test_run_to_menu_item_shows_and_sends_run_to() -> None:
    """右クリック「ここまで実行」は条件で出分け、選ぶと /run に runTo が入ること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        const reg = Blockly.ContextMenuRegistry.registry;
        const item = reg.getItem('pokeconRunTo');
        const w1 = ws.getBlockById('w1');
        const prog = ws.getBlockById('P');
        // 値ブロックは出さない（影・部品棚と同条件）。
        const v = ws.newBlock('text');
        const shown = { stmt: item.preconditionFn({ block: w1 }),
          prog: item.preconditionFn({ block: prog }),
          value: item.preconditionFn({ block: v }) };
        const label = (typeof item.displayText === 'function')
          ? item.displayText({ block: w1 }) : item.displayText;
        // When: 待つの手前まで実行を選ぶ
        item.callback({ block: w1 });
        await flush(300);
        const sent = runCalls().find((c) => c.url === './run');
        await settle();
        done({ shown, label, runTo: sent.body.runTo, code: sent.body.code });
        """
    )
    # Then: 文には出て、プログラム自身・値には出ない
    assert res["shown"] == {"stmt": "enabled", "prog": "hidden", "value": "hidden"}
    assert "ここまで実行" in res["label"]
    # Then: /run の本文に runTo が入り、全体を走らせる
    assert res["runTo"] == "w1"
    assert "_pokecon_step(self, 'p1')" in res["code"]
    assert "_pokecon_step(self, 'w1')" in res["code"]


@NODE_ONLY
def test_run_to_menu_item_is_disabled_while_running() -> None:
    """「ここまで実行」は実行中は無効になること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        const reg = Blockly.ContextMenuRegistry.registry;
        const item = reg.getItem('pokeconRunTo');
        const shown = item.preconditionFn({ block: ws.getBlockById('w1') });
        await settle();
        done({ shown });
        """
    )
    assert res["shown"] == "disabled"


@NODE_ONLY
def test_run_to_pause_shows_specific_status() -> None:
    """「ここまで実行」で止まったとき、専用の状態行が出ること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        // When: 待つの手前まで実行を選ぶ
        Blockly.ContextMenuRegistry.registry.getItem('pokeconRunTo')
          .callback({ block: ws.getBlockById('w1') });
        await flush(300);
        // Given: 待つの手前で一時停止中にする
        routes['run/state'] = Object.assign({}, routes['run/state'],
          { paused: true, blockId: 'w1' });
        await flush(400);
        const status = els.status.textContent;
        await settle();
        done({ status });
        """
    )
    assert "ここまで実行" in res["status"]


@NODE_ONLY
def test_ctrl_shift_enter_runs_only_the_selected_block() -> None:
    """Ctrl+Shift+Enter で選択中のブロックだけを試すこと。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        // Given: 待ちを選択中にする
        Blockly.getSelected = () => ws.getBlockById('w1');
        // When: Ctrl+Shift+Enter を押す
        winHandlers.keydown(fakeKey({ key: 'Enter', ctrlKey: true, shiftKey: true }));
        await flush(300);
        const sent = runCalls().find((c) => c.url === './run');
        await settle();
        done({ code: sent.body.code });
        """
    )
    # Then: 待ちだけのコードになる
    assert "'w1'" in res["code"]
    assert "'p1'" not in res["code"]


@NODE_ONLY
def test_ctrl_shift_enter_without_selection_explains_why() -> None:
    """Ctrl+Shift+Enter で選択が無ければ、送らずに状態行で知らせること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        // Given: 何も選んでいない
        Blockly.getSelected = () => null;
        // When: Ctrl+Shift+Enter を押す
        winHandlers.keydown(fakeKey({ key: 'Enter', ctrlKey: true, shiftKey: true }));
        await flush(300);
        // 念のため様子の取りに行きを止める（送っていなければ何もしない）。
        await settle();
        done({ sent: runCalls().filter((c) => c.url === './run').length,
               status: els.status.textContent });
        """
    )
    assert res["sent"] == 0
    assert "選" in res["status"]


@NODE_ONLY
def test_f9_toggles_the_breakpoint_on_the_selected_block() -> None:
    """F9 で選択中のブロックの区切りを付け外しできること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        Blockly.getSelected = () => ws.getBlockById('w1');
        const reg = Blockly.ContextMenuRegistry.registry;
        const item = reg.getItem('pokeconBreakpoint');
        const before = item.displayText({ block: ws.getBlockById('w1') });
        // When: F9 を押す
        winHandlers.keydown(fakeKey({ key: 'F9' }));
        await flush(50);
        const after = item.displayText({ block: ws.getBlockById('w1') });
        // When: もう一度 F9 を押す
        winHandlers.keydown(fakeKey({ key: 'F9' }));
        await flush(50);
        const again = item.displayText({ block: ws.getBlockById('w1') });
        done({ before, after, again });
        """
    )
    assert "止める" in res["before"]
    assert "外す" in res["after"]
    assert "止める" in res["again"]


@NODE_ONLY
def test_f5_stops_reload_and_starts_the_run() -> None:
    """F5 は再読み込みを止めて、実行していなければ ▶ 実行すること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        // When: 何も走っていないときに F5 を押す
        const ev = fakeKey({ key: 'F5' });
        winHandlers.keydown(ev);
        await flush(300);
        const sent = runCalls().find((c) => c.url === './run');
        await settle();
        done({ prevented: ev.prevented, hasRun: !!sent });
        """
    )
    assert res["prevented"] == [True]
    assert res["hasRun"] is True


@NODE_ONLY
def test_f10_shift_f5_and_f5_resume_while_running() -> None:
    """実行中の F10 は1つ進む、Shift+F5 は停止、一時停止中の F5 は再開すること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        // When: F10（1つ進む）
        winHandlers.keydown(fakeKey({ key: 'F10' }));
        await flush(50);
        // Given: 一時停止中にする
        routes['run/state'] = Object.assign({}, routes['run/state'], { paused: true });
        await flush(400);
        // When: F5（再開）
        winHandlers.keydown(fakeKey({ key: 'F5' }));
        await flush(50);
        // When: Shift+F5（停止）
        winHandlers.keydown(fakeKey({ key: 'F5', shiftKey: true }));
        await flush(50);
        const actions = runCalls().filter((c) => c.url === './run/control')
          .map((c) => c.body.action);
        await settle();
        done({ actions });
        """
    )
    assert res["actions"] == ["step", "resume", "stop"]


@NODE_ONLY
def test_keys_do_nothing_while_typing_in_a_field() -> None:
    """入力欄に焦点があるときは、キー操作が効かないこと。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        Blockly.getSelected = () => ws.getBlockById('w1');
        // Given: 入力欄に焦点がある
        sandbox.document.activeElement = { tagName: 'INPUT' };
        const f5 = fakeKey({ key: 'F5' });
        winHandlers.keydown(f5);
        await flush(100);
        winHandlers.keydown(fakeKey({ key: 'F9' }));
        await flush(50);
        winHandlers.keydown(fakeKey({ key: 'Enter', ctrlKey: true, shiftKey: true }));
        await flush(100);
        const reg = Blockly.ContextMenuRegistry.registry;
        const label = reg.getItem('pokeconBreakpoint')
          .displayText({ block: ws.getBlockById('w1') });
        // 念のため様子の取りに行きを止める（送っていなければ何もしない）。
        await settle();
        done({ runs: runCalls().filter((c) => c.url === './run').length,
               controls: runCalls().filter((c) => c.url === './run/control').length,
               prevented: f5.prevented, label });
        """
    )
    # Then: 何も送らず、再読み込みも止めず、区切りも変わらない
    assert res["runs"] == 0
    assert res["controls"] == 0
    assert res["prevented"] == []
    assert "止める" in res["label"]


@NODE_ONLY
def test_buttons_show_their_shortcut_keys() -> None:
    """1つ進む等のボタンにキーが書いてあること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        done({ step: els.stepbtn.title, stop: els.stopbtn.title,
               pause: els.pausebtn.title });
        """
    )
    assert "F10" in res["step"]
    assert "F5" in res["stop"]
    assert "F5" in res["pause"]


# -- 使い方・文書 ---------------------------------------------------------------


def test_help_line_lists_the_run_to_and_key_operations() -> None:
    """使い方（#help）に「ここまで実行」とキー操作の行があること。"""
    # Given/When: 編集画面の原文を読む
    text = EDITOR_HTML.read_text(encoding="utf-8")
    # Then: 右クリックの新項目とキーの説明がある
    assert "ここまで実行" in text
    assert "F9" in text
    assert "F10" in text
    assert "Shift+F5" in text or "Shift＋F5" in text


def test_docs_cover_the_run_to_and_key_operations() -> None:
    """docs/BLOCKLY_EDITOR.md の試し実行に追記があること。"""
    # Given/When: 文書の原文を読む
    text = DOCS.read_text(encoding="utf-8")
    # Then: ここまで実行とキー操作の説明がある
    assert "ここまで実行" in text
    assert "F9" in text
    assert "F10" in text


@NODE_ONLY
def test_ctrl_enter_does_not_run_while_typing_a_name() -> None:
    """保存名などに文字を打っている間の Ctrl+Enter では、実機を動かさないこと。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        sandbox.document.activeElement = { tagName: 'INPUT' };
        winHandlers.keydown(fakeKey({ key: 'Enter', ctrlKey: true }));
        await flush(200);
        await settle();
        done({ runs: runCalls().filter((c) => c.url === './run').length });
        """
    )
    assert res["runs"] == 0


@NODE_ONLY
def test_f5_while_running_explains_instead_of_doing_nothing() -> None:
    """実行中（一時停止でない）の F5 は、黙らずに使えるキーを知らせること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        const ev = fakeKey({ key: 'F5' });
        winHandlers.keydown(ev);
        await flush(100);
        const status = els.status.textContent;
        await settle();
        done({ status, prevented: ev.prevented.length,
               runs: runCalls().filter((c) => c.url === './run').length });
        """
    )
    assert res["runs"] == 1
    assert res["prevented"] == 1
    assert "一時停止" in res["status"] and "停止" in res["status"]


@NODE_ONLY
def test_f5_does_not_start_a_run_while_a_dialog_is_open() -> None:
    """作例の窓などを開いている間の F5 では、走らせないこと（再読み込みは止める）。"""
    res = run_editor(
        _SETUP
        + """
        routes.samples = { ok: true, samples: [] };
        boot();
        await flush(400);
        els.newws.handlers.click();
        await flush(300);
        const ev = fakeKey({ key: 'F5' });
        winHandlers.keydown(ev);
        await flush(200);
        await settle();
        done({ prevented: ev.prevented.length,
               runs: runCalls().filter((c) => c.url === './run').length });
        """
    )
    assert res["runs"] == 0
    assert res["prevented"] == 1
