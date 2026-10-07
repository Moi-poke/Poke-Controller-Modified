"""編集画面の試し実行（実行・一時停止・1つ進む・停止・光らせる・区切り）の流れ。

editor.html の inline script を本物の Blockly（描画なし）と最小DOMで起動し、
受け口（/run・/run/state・/run/control）は記録つきの代役で返す。
"""

from __future__ import annotations

from blockly_node import NEEDS_NODE, run_editor

pytestmark = NEEDS_NODE

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
const warnings = [];
Blockly.Block.prototype.setWarningText = function (t) { warnings.push([this.id, t]); };
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
"""


def test_run_sends_traced_code_and_lights_the_running_block() -> None:
    """「実行」で目印入りのコードを送り、走っているブロックを光らせること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        // When: 実行を押す
        els.runbtn.handlers.click();
        await flush(400);
        const sent = runCalls().find((c) => c.url === './run');
        const during = { run: els.runbtn.disabled, stop: els.stopbtn.disabled,
                         pause: els.pausebtn.disabled, step: els.stepbtn.disabled };
        // When: 次のブロックへ進み、最後に完了する
        routes['run/state'] = Object.assign({}, routes['run/state'], { blockId: 'w1',
          logs: [[1, 'hello']] });
        await flush(400);
        routes['run/state'] = Object.assign({}, routes['run/state'], { running: false,
          result: '完了', busy: false });
        await flush(600);
        done({ code: sent.body.code, bps: sent.body.breakpoints, during, highlights,
               after: { run: els.runbtn.disabled, stop: els.stopbtn.disabled },
               status: els.status.textContent, log: els.runlog.textContent });
        """
    )
    # Then: 目印入りのコードを送った（外に置いた B は入らない）
    assert "_pokecon_step(self, 'p1')" in res["code"]
    assert "_pokecon_step(self, 'w1')" in res["code"]
    assert "'loose'" not in res["code"]
    assert res["bps"] == []
    # Then: 実行中は実行ボタンが押せず、停止・一時停止・1つ進むが押せる
    assert res["during"] == {"run": True, "stop": False, "pause": False, "step": False}
    # Then: 走ったブロックが順に光り、終わったら消える
    assert "p1" in res["highlights"] and "w1" in res["highlights"]
    assert res["highlights"].index("p1") < res["highlights"].index("w1")
    assert res["highlights"][-1] is None
    # Then: 終わると実行に戻り、完了とログが見える
    assert res["after"] == {"run": False, "stop": True}
    assert "完了" in res["status"]
    assert "hello" in res["log"]


def test_try_only_this_block_from_the_context_menu() -> None:
    """右クリックの「このブロックだけ試す」は、外に置いたブロックでもそれだけを送ること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        const item = Blockly.ContextMenuRegistry.registry.getItem('pokeconTryOnly');
        const loose = ws.getBlockById('loose');
        const shown = item.preconditionFn({ block: loose });
        item.callback({ block: loose });
        await flush(300);
        const sent = runCalls().find((c) => c.url === './run');
        await settle();
        done({ shown, code: sent.body.code, stillDisabled: !loose.isEnabled() });
        """
    )
    assert res["shown"] == "enabled"
    assert "Button.B" in res["code"]
    assert "Button.A" not in res["code"]
    assert res["stillDisabled"] is True


def test_breakpoint_toggle_is_sent_with_the_run() -> None:
    """右クリックの区切りを付けたブロックは、実行時に区切りとして送ること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        const item = Blockly.ContextMenuRegistry.registry.getItem('pokeconBreakpoint');
        const w1 = ws.getBlockById('w1');
        const labelBefore = item.displayText({ block: w1 });
        item.callback({ block: w1 });
        const labelAfter = item.displayText({ block: w1 });
        els.runbtn.handlers.click();
        await flush(300);
        const sent = runCalls().find((c) => c.url === './run');
        await settle();
        done({ labelBefore, labelAfter, bps: sent.body.breakpoints });
        """
    )
    assert "止める" in res["labelBefore"]
    assert "外す" in res["labelAfter"]
    assert res["bps"] == ["w1"]


def test_pause_step_and_stop_buttons_send_controls() -> None:
    """一時停止・1つ進む・停止は /run/control へ操作を送り、一時停止中は再開に変わること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        els.pausebtn.handlers.click();
        await flush(50);
        routes['run/state'] = Object.assign({}, routes['run/state'], { paused: true });
        await flush(400);
        const pausedLabel = els.pausebtn.attrs["aria-label"] + " " + els.pausebtn.title;
        els.pausebtn.handlers.click();
        await flush(50);
        els.stepbtn.handlers.click();
        await flush(50);
        els.stopbtn.handlers.click();
        await flush(50);
        const actions = runCalls().filter((c) => c.url === './run/control')
          .map((c) => c.body.action);
        await settle();
        done({ actions, pausedLabel, runState: els.runstate.textContent });
        """
    )
    assert res["actions"] == ["pause", "resume", "step", "stop"]
    assert "再開" in res["pausedLabel"]


def test_runtime_error_is_shown_on_the_failing_block() -> None:
    """実行時エラーは状態行と、落ちたブロックの警告として出ること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        routes['run/state'] = Object.assign({}, routes['run/state'], { running: false,
          result: 'エラー', error: 'ZeroDivisionError: division by zero', errorBlock: 'w1' });
        await flush(500);
        const errStatus = els.status.textContent;
        // When: もう一度実行すると前回の警告は消える
        routes['run/state'] = Object.assign({}, routes['run/state'], { running: true,
          result: '', error: '', errorBlock: '' });
        els.runbtn.handlers.click();
        await flush(300);
        const snapshot = warnings.slice();
        await settle();
        done({ warnings: snapshot, errStatus });
        """
    )
    assert ["w1", "ZeroDivisionError: division by zero"] in res["warnings"]
    assert "ZeroDivisionError" in res["errStatus"]
    assert res["warnings"][-1] == ["w1", None]


def test_run_refused_by_the_app_shows_why_and_stays_idle() -> None:
    """本体に断られた（別コマンド実行中など）ときは理由を出し、実行中にならないこと。"""
    res = run_editor(
        _SETUP
        + """
        routes.run = { ok: false, message: '本体で別のコマンドが実行中です' };
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        done({ status: els.status.textContent, run: els.runbtn.disabled,
               stop: els.stopbtn.disabled });
        """
    )
    assert "実行中" in res["status"]
    assert res["run"] is False
    assert res["stop"] is True


def test_run_is_refused_locally_when_an_image_block_has_no_image() -> None:
    """画像未選択の画像認識ブロックがあると、送らずに理由を出すこと（保存と同じ検査）。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        const v = ws.newBlock('pokecon_vision_wait_appear');
        ws.getBlockById('w1').nextConnection.connect(v.previousConnection);
        els.runbtn.handlers.click();
        await flush(300);
        done({ sent: runCalls().filter((c) => c.url === './run').length,
               status: els.status.textContent });
        """
    )
    assert res["sent"] == 0
    assert "画像" in res["status"]


def test_run_started_elsewhere_releases_this_editor() -> None:
    """別のタブ等で新しい試し実行が始まったら、この画面は実行中のまま固まらないこと。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(300);
        routes['run/state'] = Object.assign({}, routes['run/state'], { runId: 2 });
        await flush(500);
        done({ run: els.runbtn.disabled, stop: els.stopbtn.disabled,
               status: els.status.textContent });
        """
    )
    assert res["run"] is False and res["stop"] is True
    assert "別" in res["status"]


def test_lost_connection_gives_up_instead_of_polling_forever() -> None:
    """本体と通信できない状態が続いたら、取りに行くのをやめて理由を出すこと。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        els.runbtn.handlers.click();
        await flush(200);
        const orig = sandbox.fetch;
        sandbox.fetch = (url, o) => String(url).indexOf('run/state') !== -1
          ? Promise.reject(new Error('down')) : orig(url, o);
        await flush(9000);
        const n1 = calls.length;
        await flush(1500);
        done({ run: els.runbtn.disabled, status: els.status.textContent,
               stillPolling: calls.length !== n1 });
        """
    )
    assert res["run"] is False
    assert "通信" in res["status"]


def test_context_menu_items_are_hidden_on_toolbox_blocks() -> None:
    """部品棚（フライアウト）のブロックには試し実行・区切りの項目を出さないこと。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        const b = ws.getBlockById('w1');
        b.isInFlyout = true;
        const reg = Blockly.ContextMenuRegistry.registry;
        done(['pokeconTryOnly', 'pokeconTryFrom', 'pokeconBreakpoint'].map(
          (id) => reg.getItem(id).preconditionFn({ block: b })));
        """
    )
    assert res == ["hidden", "hidden", "hidden"]
