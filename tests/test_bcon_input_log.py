"""bconでも入力記録が取れること（実機不要）。

legacyは送信行（可変長）をInputLoggerへ配っていたが、bcon系は
add_listener=Falseで何も配らず、入力ログ・blockly記録が沈黙していた。
BconTransport/BconProcTransportが送った行の写しを配り、InputLoggerが
Pico式S行（7欄フルステート）も読むことで、bconでも記録が取れる。
"""

from __future__ import annotations

import time


def test_input_logger_reads_s_rows_like_legacy() -> None:
    """Given: S行と等価のlegacy行 / When: 差分を取る / Then: 同じイベントになること。"""
    from core import InputLog

    legacy = InputLog.InputLogger(emit=lambda line: None)
    live = InputLog.InputLogger(emit=lambda line: None)
    # A押下（姿勢bit 0x04）。legacyは2bit shift済みの0x10で送る。
    assert [(e.action, e.name) for e in legacy._diff("10 08")] == [
        ("PRESS", "Button.A")
    ]
    assert [(e.action, e.name) for e in live._diff("S 4 8 80 80 80 80")] == [
        ("PRESS", "Button.A")
    ]
    assert [(e.action, e.name) for e in legacy._diff("0 08")] == [
        ("RELEASE", "Button.A")
    ]
    assert [(e.action, e.name) for e in live._diff("S 0 8 80 80 80 80")] == [
        ("RELEASE", "Button.A")
    ]
    # 欄不足・不正値のS行は捨てる（落とさない）。
    assert live._parse("S 4 8 80") is None
    assert live._parse("S zz 8 80 80 80 80") is None
    # Sで始まるlegacy行と紛れない（wireのbtn欄にSは現れない）。
    assert legacy._parse("10 08") is not None


def test_bcon_notifies_sent_rows_once_and_skips_resends() -> None:
    """Given: bcon線＋聞き手 / When: 送る / Then: 送った行が1回だけ届き再送は削られること。"""
    from core.transport import create_transport

    made = create_transport("switch-bcon")
    got: list[str] = []
    assert made.add_listener(got.append) is True
    # 重ね付けしても二重に届かない。
    assert made.add_listener(got.append) is True

    class FakeSer:
        is_open = True

        def __init__(self) -> None:
            self.written: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.written.append(bytes(data))
            return len(data)

    made.ser = FakeSer()
    made.send_row("0 8 80 80 80 80")
    made.send_row("4 8 80 80 80 80")
    # 同一行の再送（120Hz定期再送相当）は削る。差分は必ず無イベントのため。
    made.send_row("4 8 80 80 80 80")
    made.send_row("S 4 8 80 80 80 80")
    assert got == ["0 8 80 80 80 80", "4 8 80 80 80 80", "S 4 8 80 80 80 80"]
    # endも配る（押下残りの解放が記録に残る）。
    made.send_row("end")
    assert got[-1] == "end"
    # 不正行は捨て・配らない。
    before = list(got)
    made.send_row("garbage row here")
    assert got == before
    made.remove_listener(got.append)
    made.send_row("0 8 80 80 80 80")
    assert got == before


def test_bcon_proc_forwards_sent_rows_to_listeners(monkeypatch) -> None:
    """Given: proc線＋聞き手 / When: 送る / Then: 子が送った行が親へ届くこと。"""
    import core.transport.bcon as _bcon
    from core.transport import bcon_proc
    from test_bcon_proc import _FakeSerial, _fake_spawn

    monkeypatch.setattr(_bcon.serial, "Serial", _FakeSerial)
    made = bcon_proc.BconProcTransport(_spawn=_fake_spawn)
    try:
        assert made.open(3, "COM3", 1000000) is True
        got: list[str] = []
        assert made.add_listener(got.append) is True
        made.send_row("0 8 80 80 80 80")
        made.send_row("4 8 80 80 80 80")
        deadline = time.perf_counter() + 3.0
        while time.perf_counter() < deadline and len(got) < 2:
            time.sleep(0.05)
        assert got == ["0 8 80 80 80 80", "4 8 80 80 80 80"]
    finally:
        made.close()


def test_sender_links_input_log_on_bcon_and_records() -> None:
    """Given: bcon線のSender / When: Aを押して離す / Then: 入力ログに残ること。"""
    import time as _time

    from core import Sender
    from core.Keys import Button, KeyPress
    from core.transport import create_transport

    lines: list[str] = []
    transport = create_transport("switch-bcon")

    class FakeSer:
        is_open = True

        def __init__(self) -> None:
            self.written: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.written.append(bytes(data))
            return len(data)

    transport.ser = FakeSer()
    sender = Sender.Sender(
        is_show_serial=False, input_log_emit=lines.append, transport=transport
    )
    assert sender.isInputLogLinked() is True
    keys = KeyPress(sender)
    keys.input(Button.A)
    keys.inputEnd(Button.A)
    sender.flushInputLog()
    _time.sleep(0.5)
    sender.flushInputLog()
    assert any("Button.A" in line for line in lines)


def test_blockly_recorder_attaches_to_bcon_transports() -> None:
    """Given: bcon/proc線 / When: 記録を付ける / Then: 付けられること。"""
    from core.transport import create_transport
    from services import blockly_record

    for name in ("switch-bcon", "switch-bcon-proc"):
        rec = blockly_record.Recorder()
        assert rec.start(create_transport(name)) is True
        assert rec.recording is True
        rec.stop()
