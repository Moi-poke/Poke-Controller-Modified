"""操作の記録（マクロ記録）の検証。

本体で実際に押した操作（送信線へ出た入力）を記録し、押すブロックの
並びにして挿入する。送信は本物の Sender＋KeyPress＋FakeTransport で行う。
"""

from __future__ import annotations

import datetime
import functools
import http.client
import http.server
import json
import threading
import time
from typing import Any

import pytest

pytest.importorskip("tkinter")

from blockly_node import NEEDS_NODE, run_blockly, run_editor  # noqa: E402
from core import InputLog, Sender  # noqa: E402
from core.Keys import Button, Direction, Hat, KeyPress  # noqa: E402
from fakes import FakeTransport  # noqa: E402
from services import blockly_record  # noqa: E402
from ui import blockly_editor  # noqa: E402

pytestmark = NEEDS_NODE


def _event(
    action: str,
    kind: str,
    name: str,
    at: float,
    duration: float | None = None,
) -> InputLog.InputEvent:
    """Given: 時刻と押下時間だけを持つ決め打ちのイベント列を作る。"""
    wall = datetime.datetime.now()
    return InputLog.InputEvent(action, kind, name, at, wall, duration=duration)


def test_real_wire_records_button_hat_and_stick_in_order() -> None:
    """Given: 本物の送信経路に記録を付ける / When: A→十字キー上→左スティック上を操作する / Then: 順に手順になること。"""
    # Given: 本物の送信経路に記録を付ける
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    keys = KeyPress(sender)
    rec = blockly_record.Recorder()
    assert rec.start(transport) is True
    # When: A を約0.1秒押す→約0.3秒待つ→十字キー上→左スティック上を倒す
    keys.input(Button.A)
    time.sleep(0.1)
    keys.inputEnd(Button.A)
    time.sleep(0.3)
    keys.input(Hat.TOP)
    time.sleep(0.1)
    keys.inputEnd(Hat.TOP)
    keys.input(Direction.UP)
    time.sleep(0.1)
    keys.inputEnd(Direction.UP)
    steps = rec.stop()
    # Then: 順に Button.A・Hat.TOP・Direction.UP になる
    assert [s["target"] for s in steps] == ["Button.A", "Hat.TOP", "Direction.UP"]
    # Then: A の duration は約0.1・wait は約0.3（0.05単位の丸めで±0.1以内）
    assert abs(float(steps[0]["duration"]) - 0.1) <= 0.1
    assert abs(float(steps[0]["wait"]) - 0.3) <= 0.1


def test_events_to_steps_rounds_clamps_and_drops_stale_release() -> None:
    """Given: 決め打ちのイベント列 / When: 手順へ変換する / Then: 丸め・最小値・最後のwait・開始前からのRELEASEを捨てること。"""
    # Given: 決め打ちのイベント列
    events = [
        # When: 記録開始前から押しっぱなしだった RELEASE（PRESS が無い）
        _event("RELEASE", "button", "Button.B", 0.0, None),
        _event("PRESS", "button", "Button.A", 1.0),
        # When: 0.023秒だけ押す（0.05未満→最小0.05）
        _event("RELEASE", "button", "Button.A", 1.023, 0.023),
        _event("PRESS", "button", "Button.B", 1.5),
        # When: 100秒押す（上限60）→次が無いので最後のwaitは0.1
        _event("RELEASE", "button", "Button.B", 101.5, 100.0),
    ]
    # When: 手順へ変換する
    steps = blockly_record.events_to_steps(events)
    # Then: 開始前からの RELEASE は捨てられる
    assert [s["target"] for s in steps] == ["Button.A", "Button.B"]
    # Then: 0.05単位に丸められ、最小0.05になる
    assert steps[0]["duration"] == 0.05
    # Then: 上限60になる
    assert steps[1]["duration"] == 60
    # Then: 最後の手順の wait は0.1になる
    assert steps[1]["wait"] == 0.1


def test_events_to_steps_ignores_change_events() -> None:
    """Given: CHANGE を含むイベント列 / When: 変換する / Then: CHANGE は手順にならないこと。"""
    # Given: CHANGE を含むイベント列
    events = [
        _event("PRESS", "stick", "Stick.LEFT", 1.0),
        InputLog.InputEvent(
            "CHANGE",
            "stick",
            "Stick.LEFT",
            1.1,
            datetime.datetime.now(),
            x=200,
            y=100,
            deg=45.0,
            mag=1.0,
        ),
        _event("RELEASE", "stick", "Stick.LEFT", 1.2, 0.2),
    ]
    events[-1].deg = 90.0
    events[-1].mag = 1.0
    events[-1].max_mag = 1.0
    # When: 変換する
    steps = blockly_record.events_to_steps(events)
    # Then: CHANGE は手順にならず、離した向き（8方向）だけが残る
    assert len(steps) == 1
    assert steps[0]["target"] == "Direction.UP"


def test_recorder_refuses_double_start() -> None:
    """Given: 記録中 / When: もう一度開始する / Then: 断られること。"""
    # Given: 記録中
    rec = blockly_record.Recorder()
    transport = FakeTransport()
    assert rec.start(transport) is True
    assert rec.recording is True
    # When: もう一度開始する
    # Then: 断られる
    assert rec.start(FakeTransport()) is False
    assert rec.stop() == [] or isinstance(rec.stop(), list)


class _FakeSender:
    """Given: 送信線を持つ偽の sender。"""

    def __init__(self, transport: FakeTransport) -> None:
        self.transport = transport


class _FakeSerial:
    """Given: sender を持つ偽の serial。"""

    def __init__(self, transport: FakeTransport) -> None:
        self.sender = _FakeSender(transport)


class _FakeHost:
    """Given: 司令塔の代役（serial.sender.transport を持つ）。"""

    def __init__(self, transport: FakeTransport) -> None:
        self.serial = _FakeSerial(transport)
        self.transport = transport


def _serve_with_host(
    host: Any,
) -> tuple[str, http.server.ThreadingHTTPServer, threading.Event, threading.Thread]:
    """Given: 受け口＋偽の本体＋GUIスレッド役の糸を立てる。"""
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
    return f"127.0.0.1:{httpd.server_address[1]}", httpd, stop, gui


def _teardown_server(
    httpd: http.server.ThreadingHTTPServer,
    stop: threading.Event,
    gui: threading.Thread,
) -> None:
    """Given: 受け口と糸を片付ける。"""
    try:
        if getattr(blockly_editor, "_RECORDER", None) is not None:
            try:
                blockly_editor._RECORDER.stop()
            except Exception:
                pass
    except Exception:
        pass
    httpd.shutdown()
    httpd.server_close()
    stop.set()
    gui.join(timeout=5.0)
    blockly_editor._set_run_host(None)


def _post(host: str, path: str, payload: dict[str, Any], origin: str = "") -> Any:
    """When: JSON を送り、応答の辞書（または状態）を返す。"""
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request(
        "POST",
        path,
        body=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": origin or f"http://{host}",
        },
    )
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        return {"status": resp.status}
    value: dict[str, Any] = json.loads(body.decode("utf-8"))
    return value


def test_record_start_stop_returns_steps_through_the_real_wire() -> None:
    """Given: 司令塔の代役 / When: /record/start→送信→/record/stop / Then: 手順が返ること。"""
    # Given: 司令塔の代役
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    keys = KeyPress(sender)
    host = _FakeHost(transport)
    addr, httpd, stop, gui = _serve_with_host(host)
    try:
        # When: /record/start→送信→/record/stop
        assert _post(addr, "/record/start", {})["ok"] is True
        keys.input(Button.A)
        time.sleep(0.1)
        keys.inputEnd(Button.A)
        reply = _post(addr, "/record/stop", {})
        # Then: 手順が返る
        assert reply["ok"] is True
        assert [s["target"] for s in reply["steps"]] == ["Button.A"]
        assert "1" in reply["message"]
    finally:
        _teardown_server(httpd, stop, gui)


def test_record_start_without_the_app_explains_why() -> None:
    """Given: 司令塔なし / When: /record/start / Then: 理由つき ok:false になること。"""
    # Given: 司令塔なし
    addr, httpd, stop, gui = _serve_with_host(None)
    try:
        # When: /record/start
        reply = _post(addr, "/record/start", {})
        # Then: 理由つき ok:false になる
        assert reply["ok"] is False
        assert reply["message"]
    finally:
        _teardown_server(httpd, stop, gui)


def test_record_double_start_is_refused() -> None:
    """Given: 記録中 / When: もう一度 /record/start / Then: 断られること。"""
    # Given: 記録中
    host = _FakeHost(FakeTransport())
    addr, httpd, stop, gui = _serve_with_host(host)
    try:
        assert _post(addr, "/record/start", {})["ok"] is True
        # When: もう一度 /record/start
        reply = _post(addr, "/record/start", {})
        # Then: 断られる
        assert reply["ok"] is False
        assert reply["message"]
    finally:
        _teardown_server(httpd, stop, gui)


def test_record_from_another_site_is_refused() -> None:
    """Given: 別オリジン / When: /record/start / Then: 403 で断られること。"""
    # Given: 別オリジン
    host = _FakeHost(FakeTransport())
    addr, httpd, stop, gui = _serve_with_host(host)
    try:
        # When: /record/start
        reply = _post(addr, "/record/start", {}, origin="http://evil.example")
        # Then: 403 で断られる
        assert reply == {"status": 403}
    finally:
        _teardown_server(httpd, stop, gui)


#: Given: 記録の画面検証の前置き（プログラム＋選択中の press）。
_SETUP = """
routes.list = { stems: [], appId: 'app1' };
store['pokecon.blockly.draft.v1.app1'] = JSON.stringify({ stem: 'Rec1', dirty: true,
  state: { blocks: { languageVersion: 0, blocks: [
    { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' }, inputs: { DO: { block:
      { type: 'pokecon_press', id: 'p1', fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } }
  ] } } });
routes['record/start'] = { ok: true, message: '記録を始めました' };
routes['record/stop'] = { ok: true, message: '2 個の操作を記録しました',
  steps: [ { target: 'Button.A', duration: 0.1, wait: 0.2 },
           { target: 'Hat.TOP', duration: 0.1, wait: 0.1 } ] };
"""


def test_record_button_toggles_and_inserts_press_blocks_after_the_selection() -> None:
    """Given: 編集画面 / When: 記録ボタンを押して止める / Then: 選択中の後ろに pokecon_press が入ること。"""
    res = run_editor(
        _SETUP
        + """
        boot();
        await flush(400);
        const recbtn = els.recbtn;
        const labelBefore = recbtn ? recbtn.textContent : null;
        // When: 記録ボタンを押す
        recbtn.handlers.click();
        await flush(300);
        const started = { label: els.recbtn.textContent,
          runDisabled: els.runbtn.disabled, state: els.runstate.textContent,
          sent: calls.filter((c) => c.url === './record/start').length };
        // 選択中のブロック（p1）を選んでから止める
        const p1 = ws.getBlockById('p1');
        try { p1.select(); } catch (e) {}
        if (Blockly.getSelected) {
          try { Blockly.getSelected = () => p1; } catch (e) {}
        }
        const undoBefore = ws.getAllBlocks(false).length;
        recbtn.handlers.click();
        await flush(300);
        const after = ws.getBlockById('p1').getNextBlock();
        const ids = [];
        for (let b = after; b; b = b.getNextBlock()) { ids.push(b.id); }
        const fields = ids.map((id) => { const b = ws.getBlockById(id);
          return [b.type, b.getFieldValue('BUTTON'), b.getFieldValue('DURATION'), b.getFieldValue('WAIT')]; });
        // 取り消し1回で全部消えるか
        ws.undo(false);
        const gone = ws.getBlockById('p1').getNextBlock();
        const countAfterUndo = ws.getAllBlocks(false).length;
        // ポーリングの繰り返しを残さない（node が終わるように止める）
        try { if (typeof stopAllTimers === 'function') { stopAllTimers(); } } catch (e) {}
        done({ labelBefore, started, ids, fields, gone: !!gone,
               countAfterUndo, undoBefore,
               runAfter: els.runbtn.disabled, recAfter: els.recbtn.textContent,
               status: els.status.textContent });
        """
    )
    # Then: 記録ボタン→/record/start が呼ばれ、表示が「記録を止める」になる
    assert res["started"]["sent"] == 1
    assert "止める" in res["started"]["label"]
    assert res["started"]["runDisabled"] is True
    assert "記録中" in res["started"]["state"]
    # Then: 選択中ブロックの後ろに pokecon_press として入る
    assert len(res["ids"]) == 2
    assert res["fields"][0][0] == "pokecon_press"
    # Then: BUTTON 欄が A（素の名）/ Hat.TOP になる
    assert res["fields"][0][1] == "A"
    assert res["fields"][1][1] == "Hat.TOP"
    # Then: DURATION・WAIT が入る
    assert float(res["fields"][0][2]) == 0.1
    assert float(res["fields"][0][3]) == 0.2
    # Then: 全体が1つの取り消し単位（取り消し1回で全部消える）
    assert res["gone"] is False
    assert res["countAfterUndo"] == res["undoBefore"]


def test_record_with_no_steps_shows_a_hint() -> None:
    """Given: 0件の手順 / When: 記録を止める / Then: 案内が出て何も入らないこと。"""
    res = run_editor(
        _SETUP
        + """
        routes['record/stop'] = { ok: true, message: 'x', steps: [] };
        boot();
        await flush(400);
        const before = ws.getAllBlocks(false).length;
        els.recbtn.handlers.click();
        await flush(300);
        els.recbtn.handlers.click();
        await flush(300);
        try { if (typeof stopAllTimers === 'function') { stopAllTimers(); } } catch (e) {}
        done({ status: els.status.textContent,
               count: ws.getAllBlocks(false).length - before });
        """
    )
    # Then: 案内が出て何も入らない
    assert "記録されませんでした" in res["status"]
    assert res["count"] == 0


def test_insert_steps_builds_press_blocks_without_drawing() -> None:
    """Given: 手順の一覧 / When: insertSteps で作る / Then: 描画なしで pokecon_press の並びになること。"""
    out = run_blockly(
        """
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
  { type: 'pokecon_program', id: 'P', fields: { NAME: 'x' },
    inputs: { DO: { block: { type: 'pokecon_press', id: 'p1',
      fields: { BUTTON: 'A', DURATION: 0.1, WAIT: 0.1 } } } } }
] } }, ws);
const made = Blockly.PokeconEditor.insertSteps(ws, [
  { target: 'Button.A', duration: 0.1, wait: 0.2 },
  { target: 'Direction.UP', duration: 0.15, wait: 0.1 },
], ws.getBlockById('p1'));
const p1next = ws.getBlockById('p1').getNextBlock();
done({ n: made.length, types: made.map((b) => b.type),
       btns: made.map((b) => b.getFieldValue('BUTTON')),
       durs: made.map((b) => b.getFieldValue('DURATION')),
       next: p1next ? p1next.id : null, first: made[0] ? made[0].id : null });
"""
    )
    # Then: 描画なしで pokecon_press の並びになる
    assert out["n"] == 2
    assert out["types"] == ["pokecon_press", "pokecon_press"]
    # Then: ボタンは素の名、方向は Direction.UP のまま
    assert out["btns"] == ["A", "Direction.UP"]
    assert [float(v) for v in out["durs"]] == [0.1, 0.15]
    # Then: 選択中の直後に入る
    assert out["next"] == out["first"]


def test_a_new_recording_starts_from_a_clean_slate() -> None:
    """前回の記録の途中で押したままだったものを、次の記録へ持ち越さないこと。"""
    transport = FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=transport)
    keys = KeyPress(sender)
    rec = blockly_record.Recorder()
    # Given: 1回目の記録の途中で A を押したまま止める
    assert rec.start(transport)
    keys.input([Button.A])
    time.sleep(0.05)
    rec.stop()
    # When: しばらくして2回目を始め、A を離してから B を押して離す
    time.sleep(0.6)
    assert rec.start(transport)
    keys.inputEnd([Button.A])
    keys.input([Button.B])
    time.sleep(0.1)
    keys.inputEnd([Button.B])
    steps = rec.stop()
    # Then: 2回目の手順に、前回から押していた A の長い押下は入らない
    assert [s["target"] for s in steps] == ["Button.B"]


def test_recording_follows_a_swapped_transport() -> None:
    """記録中に本体の送信線が差し替わっても、新しい線の操作を記録し続けること。"""
    # Given: 本物の sender（線1）で記録を始める
    first, second = FakeTransport(), FakeTransport()
    sender = Sender.Sender(is_show_serial=False, transport=first)
    keys = KeyPress(sender)
    host = _FakeHost(first)
    host.serial.sender = sender  # type: ignore[assignment]  # 本物の sender に差し替える
    addr, httpd, stop, gui = _serve_with_host(host)
    try:
        assert _post(addr, "/record/start", {})["ok"] is True
        # When: 線が差し替わり、GUI の見回りが1回走ってから操作する
        assert sender.setTransport(second)
        blockly_editor._call_on_gui(blockly_editor._follow_record_transport)
        keys.input(Button.B)
        time.sleep(0.1)
        keys.inputEnd(Button.B)
        reply = _post(addr, "/record/stop", {})
        # Then: 新しい線での操作が記録され、古い線には聞き手が残らない
        assert [s["target"] for s in reply["steps"]] == ["Button.B"]
        assert first._listeners == [] or all(
            getattr(f, "__self__", None) is not blockly_editor._RECORDER._logger
            for f in first._listeners
        )
    finally:
        _teardown_server(httpd, stop, gui)


class _Runner:
    def __init__(self) -> None:
        self.running_command: Any = None

    @property
    def state(self) -> str:
        return "idle" if self.running_command is None else "running"

    def is_busy(self) -> bool:
        return self.running_command is not None


def test_server_refuses_trial_while_recording_and_recording_while_trial() -> None:
    """受け口でも、記録中の試し実行・試し実行中の記録を断ること（画面の制限だけに頼らない）。"""
    from services import blockly_run

    transport = FakeTransport()
    host = _FakeHost(transport)
    host.runner = _Runner()  # type: ignore[attr-defined]  # 試し実行の様子を見る口
    addr, httpd, stop, gui = _serve_with_host(host)
    try:
        # Given: 記録中 / When: 試し実行 / Then: 断る
        assert _post(addr, "/record/start", {})["ok"] is True
        reply = _post(addr, "/run", {"code": "x = 1\n"})
        assert reply["ok"] is False and "記録" in reply["message"]
        _post(addr, "/record/stop", {})
        # Given: 試し実行中 / When: 記録 / Then: 断る
        trial_cls = type("TrialCmd", (), {})
        blockly_editor._TRIAL = blockly_editor._Trial(
            blockly_run.TrialSession(7), trial_cls
        )
        host.runner.running_command = trial_cls()  # type: ignore[attr-defined]
        reply = _post(addr, "/record/start", {})
        assert reply["ok"] is False and "試し実行" in reply["message"]
        assert blockly_editor._RECORDER.recording is False
    finally:
        blockly_editor._TRIAL = None
        _teardown_server(httpd, stop, gui)
