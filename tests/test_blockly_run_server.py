"""試し実行の受け口（/run・/run/state・/run/control）の検証。

本物の受け口を一時ポートで立て、本体側は偽の司令塔（実際にコマンドを
偽の線で走らせる）にする。GUIスレッドでの呼出は、検証用の糸が
drain_gui_calls を回して代わりに受け持つ。
"""

from __future__ import annotations

import functools
import http.client
import http.server
import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("tkinter")

from blockly_node import NEEDS_NODE, run_blockly  # noqa: E402
from core import Sender  # noqa: E402
from fakes import FakeTransport  # noqa: E402
from services import blockly_run  # noqa: E402
from ui import blockly_editor  # noqa: E402

pytestmark = NEEDS_NODE


class FakeRunner:
    def __init__(self) -> None:
        self.running_command: Any = None
        self.done = threading.Event()

    @property
    def state(self) -> str:
        cmd = self.running_command
        if cmd is None or self.done.is_set():
            return "idle"
        return "running"

    def is_busy(self) -> bool:
        return self.state != "idle"


class FakeHost:
    """本体の代役。受け取ったクラスを実際に偽の線で走らせる。"""

    def __init__(self) -> None:
        self.runner = FakeRunner()
        self.started: list[Any] = []
        self.transport = FakeTransport()
        self.gui_threads: set[int] = set()

    def start_external_command(self, cls: Any) -> str | None:
        self.gui_threads.add(threading.get_ident())
        if self.runner.is_busy():
            return "本体で別のコマンドが実行中です"
        cmd = cls()
        self.started.append(cmd)
        self.runner.running_command = cmd
        self.runner.done = threading.Event()
        sender = Sender.Sender(is_show_serial=False, transport=self.transport)
        assert cmd.start(sender, self.runner.done.set) is True
        return None

    def stopPlay(self) -> None:
        self.gui_threads.add(threading.get_ident())
        self.runner.running_command.end()

    def set_running_paused(self, paused: bool) -> None:
        self.gui_threads.add(threading.get_ident())
        cmd = self.runner.running_command
        if paused:
            cmd.pause()
        else:
            cmd.resume()

    def syncPauseState(self) -> None:
        pass


@pytest.fixture()
def run_server() -> Iterator[tuple[str, CountingHost]]:
    """受け口＋偽の本体＋GUIスレッド役の糸。"""
    host = CountingHost()
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
    blockly_editor._TRIAL = None
    blockly_editor._set_run_host(None)


def post(
    host: str, path: str, payload: dict[str, Any], origin: str = ""
) -> dict[str, Any]:
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


def state(host: str, since: int = 0) -> dict[str, Any]:
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request("GET", f"/run/state?since={since}")
    resp = conn.getresponse()
    value: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    return value


def control(host: str, run_id: Any, action: str, **extra: Any) -> dict[str, Any]:
    """`/run/control` を送る。run_id が None なら runId を付けない。"""
    payload: dict[str, Any] = {"action": action, **extra}
    if run_id is not None:
        payload["runId"] = run_id
    return post(host, "/run/control", payload)


def wait_state(host: str, cond: Any, timeout: float = 8.0) -> dict[str, Any]:
    end = time.monotonic() + timeout
    snap = state(host)
    while time.monotonic() < end:
        snap = state(host)
        if cond(snap):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"待ちきれない: {snap}")


def trial_code(body_js: str) -> str:
    """本物のBlocklyで試し実行用のコードを作る。"""
    out = run_blockly(
        f"""
const ws = new Blockly.Workspace();
Blockly.serialization.workspaces.load({body_js}, ws);
done(E.trialCode(ws, Blockly.Python, {{}}));
"""
    )
    code: str = out["code"]
    return code


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
                                "fields": {"BUTTON": "A", "DURATION": 0.02, "WAIT": 0},
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


def test_run_starts_the_trial_on_the_gui_thread_and_reports_until_done(
    run_server: tuple[str, FakeHost],
) -> None:
    """/run で本体のGUIスレッドから開始し、/run/state で最後まで様子が追えること。"""
    host, fake = run_server
    code = trial_code(PROGRAM)
    # When: 試し実行を頼む
    reply = post(host, "/run", {"code": code})
    # Then: 受け付けられ、本体（GUIスレッド役）で開始された
    assert reply["ok"] is True, reply
    assert fake.started and str(fake.started[0].NAME).startswith("[試し]")
    assert threading.get_ident() not in fake.gui_threads
    # Then: 最後まで走り、結果・表示のログが取れる
    snap = wait_state(host, lambda s: s["result"])
    assert snap["result"] == "完了"
    assert snap["runId"] == reply["runId"]
    assert any("hello" in text for _, text in snap["logs"])
    assert fake.transport.rows


def test_breakpoint_pause_is_visible_and_resume_continues(
    run_server: tuple[str, FakeHost],
) -> None:
    """区切りで止まった様子が見え、区切りを外して再開すると続きが走ること。"""
    host, _ = run_server
    code = trial_code(PROGRAM)
    # Given: 表示の手前に区切り
    reply = post(host, "/run", {"code": code, "breakpoints": ["pr1"]})
    assert reply["ok"] is True, reply
    # Then: 止まった様子（一時停止・表示ブロック）が見える
    snap = wait_state(host, lambda s: s["paused"] and s["blockId"] == "pr1")
    assert snap["running"] is True
    # When: 区切りを外して再開
    assert control(host, reply["runId"], "breakpoints", breakpoints=[])["ok"]
    assert control(host, reply["runId"], "resume")["ok"]
    # Then: 完了
    assert wait_state(host, lambda s: s["result"])["result"] == "完了"


def test_stop_from_the_editor_ends_the_trial_as_stopped(
    run_server: tuple[str, FakeHost],
) -> None:
    """編集画面の停止で止まり、結果が「停止」になること。"""
    host, _ = run_server
    loop = json.dumps(
        {
            "blocks": {
                "languageVersion": 0,
                "blocks": [
                    {
                        "type": "pokecon_program",
                        "id": "P",
                        "fields": {"NAME": "loop", "TAGS": "blockly"},
                        "inputs": {
                            "DO": {
                                "block": {
                                    "type": "controls_whileUntil",
                                    "id": "w",
                                    "fields": {"MODE": "WHILE"},
                                    "inputs": {
                                        "BOOL": {
                                            "block": {
                                                "type": "logic_boolean",
                                                "fields": {"BOOL": "TRUE"},
                                            }
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
    reply = post(host, "/run", {"code": trial_code(loop)})
    assert reply["ok"]
    wait_state(host, lambda s: s["blockId"] == "w")
    assert control(host, reply["runId"], "stop")["ok"]
    assert wait_state(host, lambda s: s["result"])["result"] == "停止"


def test_bad_code_is_refused_with_reasons(run_server: tuple[str, FakeHost]) -> None:
    """保存と同じ検査に落ちるコードは走らせず、理由を返すこと。"""
    host, fake = run_server
    reply = post(host, "/run", {"code": "import tkinter\n"})
    assert reply["ok"] is False
    assert "実行できません" in reply["message"]
    assert fake.started == []


def test_second_run_while_running_is_refused(run_server: tuple[str, FakeHost]) -> None:
    """走っている間の再実行は、本体の理由（実行中）で断られること。"""
    host, _ = run_server
    code = trial_code(PROGRAM)
    assert post(host, "/run", {"code": code, "breakpoints": ["pr1"]})["ok"]
    wait_state(host, lambda s: s["paused"])
    reply = post(host, "/run", {"code": code})
    assert reply["ok"] is False
    assert "実行中" in reply["message"]


def test_run_from_another_site_is_refused(run_server: tuple[str, FakeHost]) -> None:
    """コードを走らせる口なので、別サイトからの要求は403で断ること。"""
    host, fake = run_server
    reply = post(
        host, "/run", {"code": trial_code(PROGRAM)}, origin="http://evil.example"
    )
    assert reply == {"status": 403}
    assert fake.started == []


def test_run_without_the_app_explains_why() -> None:
    """本体と繋がっていない（司令塔なし）ときは、走らせずに理由を返すこと。"""
    blockly_editor._set_run_host(None)
    handler = functools.partial(blockly_editor._Handler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host = f"127.0.0.1:{httpd.server_address[1]}"
        reply = post(host, "/run", {"code": "x = 1\n"})
        assert reply["ok"] is False
        assert "本体" in reply["message"]
        assert state(host)["available"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5.0)


def test_saved_changes_are_reloaded_once_the_run_finishes() -> None:
    """実行中に保存した分も、実行が終わったら一覧へ反映すること（取りこぼさない）。"""
    watch_mtime = [1.0]
    watch = blockly_editor._ReloadWatch(mtime=lambda: watch_mtime[0])
    # Given: 実行中に保存先が変わった
    watch_mtime[0] = 2.0
    assert watch.tick(busy=True) is False
    # When: 実行が終わった
    # Then: 次の見回りで1回だけ作り直す
    assert watch.tick(busy=False) is True
    assert watch.tick(busy=False) is False


def test_step_from_the_editor_stops_again_at_the_next_block(
    run_server: tuple[str, FakeHost],
) -> None:
    """編集画面の「1つ進む」（本番の経路）は、次のブロックの手前で再び止まること。"""
    host, _ = run_server
    reply = post(host, "/run", {"code": trial_code(PROGRAM), "breakpoints": ["p1"]})
    assert reply["ok"]
    run_id = reply["runId"]
    wait_state(host, lambda s: s["paused"] and s["blockId"] == "p1")
    assert control(host, run_id, "breakpoints", breakpoints=[])["ok"]
    # When: 1つ進む
    assert control(host, run_id, "step")["ok"]
    # Then: 押すを実行して、表示の手前で止まる
    wait_state(host, lambda s: s["paused"] and s["blockId"] == "pr1")
    assert control(host, run_id, "resume")["ok"]
    assert wait_state(host, lambda s: s["result"])["result"] == "完了"


def test_a_gui_call_that_timed_out_is_not_run_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GUIスレッドが応答せず打ち切った仕事は、後で黙って実行しないこと。"""
    monkeypatch.setattr(blockly_editor, "GUI_CALL_TIMEOUT_S", 0.1)
    ran: list[int] = []
    # Given: 誰も取り出さない（GUIスレッドが塞がっている）
    with pytest.raises(TimeoutError):
        blockly_editor._call_on_gui(lambda: ran.append(1))
    # When: あとで取り出す
    blockly_editor.drain_gui_calls()
    # Then: 実行されない
    assert ran == []


class LateFailHost(FakeHost):
    """開始はできたのに、その後の手順で例外を出す本体（一覧の作り直しで落ちる等）。"""

    def start_external_command(self, cls: Any) -> str | None:
        super().start_external_command(cls)
        raise RuntimeError("一覧の作り直しで失敗")


def test_trial_that_started_despite_an_error_is_still_tracked() -> None:
    """開始後に本体側で例外が出ても、走っている試し実行は追跡・停止できること。"""
    fake = LateFailHost()
    blockly_editor._set_run_host(fake)
    stop = threading.Event()

    def gui_loop() -> None:
        while not stop.is_set():
            blockly_editor.drain_gui_calls()
            time.sleep(0.01)

    gui = threading.Thread(target=gui_loop, daemon=True)
    gui.start()
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(blockly_editor._Handler)
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{httpd.server_address[1]}"
    try:
        reply = post(
            host, "/run", {"code": trial_code(PROGRAM), "breakpoints": ["pr1"]}
        )
        # Then: 走っているので受け付けとして返し、様子も追える
        assert reply["ok"] is True, reply
        wait_state(host, lambda s: s["paused"] and s["runId"] == reply["runId"])
        assert control(host, reply["runId"], "stop")["ok"]
        assert wait_state(host, lambda s: s["result"])["result"] == "停止"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5.0)
        stop.set()
        gui.join(timeout=5.0)
        blockly_editor._set_run_host(None)


def test_non_object_json_body_gets_a_reply(run_server: tuple[str, FakeHost]) -> None:
    """JSONが配列などオブジェクトでないときも、黙って切らずに理由を返すこと。"""
    host, fake = run_server
    conn = http.client.HTTPConnection(host, timeout=10)
    conn.request(
        "POST",
        "/run",
        body=b"[1, 2]",
        headers={"Content-Type": "application/json", "Origin": f"http://{host}"},
    )
    resp = conn.getresponse()
    body = json.loads(resp.read().decode("utf-8"))
    assert body["ok"] is False
    assert fake.started == []


def test_trial_given_up_by_the_app_is_reported_as_stopped() -> None:
    """本体が止まらない作業を打ち切って空きに戻したら、編集画面へは「停止」と返すこと。"""
    from services import blockly_run

    fake = FakeHost()
    blockly_editor._set_run_host(fake)
    try:
        session = blockly_run.TrialSession(99)
        blockly_editor._TRIAL = blockly_editor._Trial(session, type("T", (), {}))
        # Given: 結果が出ないまま、本体の実行器は空き（打ち切り後）
        fake.runner.running_command = None
        # When: 様子を聞く
        state = blockly_editor._run_state(0)
        # Then: 走っていない・停止
        assert state["running"] is False
        assert state["result"] == "停止"
    finally:
        blockly_editor._TRIAL = None
        blockly_editor._set_run_host(None)


# -- runId 付きの操作・時間上限つき試し実行 -------------------------------------


class CountingHost(FakeHost):
    """止めた回数と、止めたスレッドを控える本体（他は偽の本体と同じ）。"""

    def __init__(self) -> None:
        super().__init__()
        self.stops = 0
        self.stop_threads: set[int] = set()

    def stopPlay(self) -> None:
        self.stops += 1
        self.stop_threads.add(threading.get_ident())
        super().stopPlay()


def wait_for(cond: Any, timeout: float = 5.0) -> None:
    """条件が成り立つまで待つ（GUI スレッドは回さない）。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("待ちきれない")


def pump_until(cond: Any, timeout: float = 5.0) -> None:
    """条件が成り立つまで、GUI スレッドの代わりに待ち行列を片付ける。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        blockly_editor.drain_gui_calls()
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("待ちきれない")


def test_state_reports_run_capabilities_with_and_without_a_trial(
    run_server: tuple[str, FakeHost],
) -> None:
    """様子には、時間上限つき・runId 付き操作の対応がいつでも入っていること。"""
    host, _ = run_server
    fresh = state(host)
    assert fresh["capabilities"] == ["boundedTrial", "runIdControl"]
    assert fresh["timedOut"] is False
    # When: 試し実行が始まっても同じ
    reply = post(host, "/run", {"code": trial_code(PROGRAM), "breakpoints": ["pr1"]})
    assert reply["ok"]
    snap = wait_state(host, lambda s: s["paused"])
    assert snap["capabilities"] == ["boundedTrial", "runIdControl"]
    assert snap["timedOut"] is False
    assert control(host, reply["runId"], "stop")["ok"]


def test_control_requires_the_current_run_id(
    run_server: tuple[str, CountingHost],
) -> None:
    """runId が無い・形が違う・古い操作は断り、何も変えないこと。"""
    host, fake = run_server
    reply = post(host, "/run", {"code": trial_code(PROGRAM), "breakpoints": ["pr1"]})
    assert reply["ok"]
    run_id = reply["runId"]
    wait_state(host, lambda s: s["paused"] and s["blockId"] == "pr1")
    trial = blockly_editor._TRIAL
    assert trial is not None
    # When/Then: どれも同じ理由で断られる
    for bad in (None, run_id + 1, 0, -1, "1", True, 1.5):
        res = control(host, bad, "stop")
        assert res["ok"] is False, (bad, res)
        assert res["message"] == "試し実行が切り替わったため操作しません"
    # 区切りも同じ扱い（受け付けて変えることはしない）
    res = control(host, run_id + 1, "breakpoints", breakpoints=[])
    assert res["ok"] is False
    assert res["message"] == "試し実行が切り替わったため操作しません"
    # Then: 走ったまま・止めていない・区切りも元のまま
    now = state(host)
    assert now["runId"] == run_id
    assert now["running"] is True and now["paused"] is True
    assert trial.session.stop_requested is False
    assert trial.session.breakpoints == frozenset({"pr1"})
    assert fake.stops == 0
    # When: 正しい runId なら効く
    assert control(host, run_id, "stop")["ok"]
    assert wait_state(host, lambda s: s["result"])["result"] == "停止"


def test_a_previous_run_id_cannot_control_the_next_run(
    run_server: tuple[str, CountingHost],
) -> None:
    """実行 A が終わって B が始まったら、A の runId では B を動かせないこと。"""
    host, fake = run_server
    first = post(host, "/run", {"code": trial_code(PROGRAM), "breakpoints": ["pr1"]})
    assert first["ok"]
    run_a = first["runId"]
    wait_state(
        host, lambda s: s["runId"] == run_a and s["paused"] and s["blockId"] == "pr1"
    )
    assert control(host, run_a, "stop")["ok"]
    wait_state(host, lambda s: s["runId"] == run_a and s["result"] == "停止")
    wait_for(lambda: not fake.runner.is_busy())
    # When: 新しい実行 B が始まる
    second = post(host, "/run", {"code": trial_code(PROGRAM), "breakpoints": ["pr1"]})
    assert second["ok"]
    run_b = second["runId"]
    assert run_b != run_a
    wait_state(
        host, lambda s: s["runId"] == run_b and s["paused"] and s["blockId"] == "pr1"
    )
    stops_before = fake.stops
    # Then: A の runId では何もできない
    for action in ("stop", "pause", "resume", "step"):
        res = control(host, run_a, action)
        assert res["ok"] is False, (action, res)
        assert res["message"] == "試し実行が切り替わったため操作しません"
    res = control(host, run_a, "breakpoints", breakpoints=[])
    assert res["ok"] is False
    assert fake.stops == stops_before
    now = state(host)
    assert now["runId"] == run_b
    assert now["running"] is True and now["paused"] is True
    assert blockly_editor._TRIAL is not None
    assert blockly_editor._TRIAL.session.breakpoints == frozenset({"pr1"})
    # When: B の runId なら止められる
    assert control(host, run_b, "stop")["ok"]
    assert wait_state(host, lambda s: s["result"])["result"] == "停止"


def test_control_queued_before_a_run_switch_is_rejected() -> None:
    """GUI スレッドの仕事が溜まっている間に切り替わったら、古い操作は効かないこと。

    runId を確かめるのは受け口のスレッドではなく、仕事を取り出した GUI スレッド
    なので、待っている間の切り替えにも追いつける。
    """
    fake = CountingHost()
    blockly_editor._set_run_host(fake)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(blockly_editor._Handler)
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{httpd.server_address[1]}"
    replies: list[dict[str, Any]] = []
    try:
        # Given: 試し実行 A が区切りで止まっている
        started: list[dict[str, Any]] = []
        starter = threading.Thread(
            target=lambda: started.append(
                post(
                    host,
                    "/run",
                    {"code": trial_code(PROGRAM), "breakpoints": ["pr1"]},
                )
            ),
            daemon=True,
        )
        starter.start()
        pump_until(lambda: bool(started))
        starter.join(timeout=5.0)
        run_a = started[0]["runId"]
        pump_until(lambda: state(host)["paused"])
        # Given: GUI スレッドは回さない（仕事は待ち行列に溜まる）
        send = threading.Thread(
            target=lambda: replies.append(control(host, run_a, "stop")), daemon=True
        )
        send.start()
        wait_for(lambda: blockly_editor._GUI_CALLS.qsize() >= 1)
        # When: その間に新しい試し実行 B が公開される（GUI スレッド相当の切り替え）
        trial_a = blockly_editor._TRIAL
        assert trial_a is not None
        session_b = blockly_run.TrialSession(run_a + 1)
        blockly_editor._TRIAL = blockly_editor._Trial(session_b, trial_a.cls)
        blockly_editor.drain_gui_calls()
        send.join(timeout=5.0)
        # Then: 溜まっていた停止は断られ、A も B も止まっていない
        assert replies and replies[0]["ok"] is False
        assert replies[0]["message"] == "試し実行が切り替わったため操作しません"
        assert fake.stops == 0
        assert trial_a.session.stop_requested is False
        assert session_b.stop_requested is False
    finally:
        cmd = fake.runner.running_command
        if cmd is not None and not fake.runner.done.is_set():
            cmd.end()
            fake.runner.done.wait(5.0)
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5.0)
        blockly_editor._TRIAL = None
        blockly_editor._set_run_host(None)


#: 押しっぱなしのまま長く待つ（期限で止まり、離す行が出るかを見る）。
BOUNDED_PROGRAM = json.dumps(
    {
        "blocks": {
            "languageVersion": 0,
            "blocks": [
                {
                    "type": "pokecon_program",
                    "id": "P",
                    "fields": {"NAME": "上限", "TAGS": "blockly"},
                    "inputs": {
                        "DO": {
                            "block": {
                                "type": "pokecon_hold",
                                "id": "h1",
                                "fields": {"TARGET": "Button.A", "WAIT": 0},
                                "next": {
                                    "block": {
                                        "type": "pokecon_wait",
                                        "id": "w1",
                                        "fields": {"SEC": 5},
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


def test_bounded_trial_stops_itself_and_releases_inputs(
    run_server: tuple[str, FakeHost],
) -> None:
    """時間上限つきの試し実行は、編集画面が何も送らなくても期限で止まること。"""
    host, fake = run_server
    started = time.monotonic()
    # When: 0.3 秒の上限で、長い待ちを含む組み立てを走らせる
    reply = post(
        host,
        "/run",
        {"code": trial_code(BOUNDED_PROGRAM), "maxSeconds": 0.3},
    )
    assert reply["ok"], reply
    # Then: こちらからは /run/control を送らずに、3秒以内へ停止する
    snap = wait_state(host, lambda s: s["result"], timeout=3.0)
    assert time.monotonic() - started < 3.0
    assert snap["runId"] == reply["runId"]
    assert snap["result"] == "停止"
    assert snap["timedOut"] is True
    # Then: 押しっぱなしも離れている（押す行のあとにボタン0の行が来る）。
    # 行の下位2ビットはスティックの変化印なので、ボタンは右へ2つずらして見る。
    states: list[int] = []
    for row in fake.transport.rows:
        token = row.split()[0]
        if token.startswith("0x"):
            states.append(int(token, 16) >> 2)
    assert states and states[0] != 0, fake.transport.rows
    assert states[-1] == 0, fake.transport.rows


@pytest.mark.parametrize(
    "value",
    [0, -0.5, True, False, "0.3", None, float("nan"), float("inf"), float("-inf")],
)
def test_bad_max_seconds_is_refused(
    value: Any, run_server: tuple[str, FakeHost]
) -> None:
    """時間上限は正の有限の数だけ。他の形は走らせずに理由を返すこと。"""
    host, fake = run_server
    reply = post(host, "/run", {"code": trial_code(PROGRAM), "maxSeconds": value})
    assert reply["ok"] is False, (value, reply)
    assert "時間上限" in reply["message"]
    assert fake.started == []


def test_expired_trial_deadline_does_not_stop_another_command() -> None:
    """期限切れでも、自分の試し実行でないコマンドは止めないこと。"""
    fake = CountingHost()
    blockly_editor._set_run_host(fake)
    try:
        # Given: 期限切れの試し実行の記録と、別の種類の走っている命令
        session = blockly_run.TrialSession(4242)
        session.deadline = time.monotonic() - 1.0
        trial_cls = type("TrialCommand", (), {})
        other = type("OtherCommand", (), {"alive": True})()
        fake.runner.running_command = other
        blockly_editor._TRIAL = blockly_editor._Trial(session, trial_cls)
        # When: 見回りが期限を見る
        blockly_editor.drain_gui_calls()
        # Then: 何も止めない
        assert fake.stops == 0
        assert session.stop_requested is False
        assert session.timed_out is False
    finally:
        blockly_editor._TRIAL = None
        blockly_editor._set_run_host(None)


def test_closing_the_editor_stops_its_bounded_trial() -> None:
    """エディタを閉じる経路では、時間上限つきの自分の試し実行を止めること。"""
    fake = CountingHost()
    blockly_editor._set_run_host(fake)
    try:
        session = blockly_run.TrialSession(77)
        session.deadline = time.monotonic() + 60.0
        cmd = type(
            "TrialCommand",
            (),
            {"alive": True, "end": lambda self: None},
        )()
        fake.runner.running_command = cmd
        blockly_editor._TRIAL = blockly_editor._Trial(session, type(cmd))
        # When: 閉じる（Menubar と同じ入口）
        blockly_editor.stop_blockly_editor()
        # Then: 呼んだスレッド（GUI スレッド）で停止を頼み、押しっぱなしを残さない
        assert fake.stops == 1
        assert session.stop_requested is True
        assert threading.get_ident() in fake.stop_threads
        # Then: 二度目は止め直さない
        blockly_editor.stop_blockly_editor()
        assert fake.stops == 1
    finally:
        blockly_editor._TRIAL = None
        blockly_editor._set_run_host(None)
