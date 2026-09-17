"""bcon送受のhostベクタ（実機不要・骨格）。"""


def test_bcon_registers_and_opens_gracefully():
    from core.transport import create_transport, list_transports, resolve_transport_name

    assert "bcon" in list_transports()
    assert resolve_transport_name("bcon") == "bcon"
    made = create_transport("bcon")
    assert made.name == "bcon"
    assert made.open(0, "", "not-a-number") is False
    assert made.open(9999, "", 1000000) is False


def test_bcon_send_row_builds_single_binary_frame():
    from core.transport import create_transport

    made = create_transport("bcon")
    written: list[bytes] = []

    class FakeSer:
        is_open = True

        def write(self, data: bytes) -> int:
            written.append(bytes(data))
            return len(data)

    made.ser = FakeSer()
    made.send_row("10 08")
    assert len(written) == 1
    assert written[0][0] == 0xAB
    assert written[0][1] == 0x01
    assert written[0][2] == 8
    assert len(written[0]) == 13


def test_bcon_open_closes_previous_port_and_resets_parser(monkeypatch):
    import serial as pyserial
    from core.transport import create_transport

    made = create_transport("bcon")

    class FakeSer:
        is_open = True

        def __init__(self) -> None:
            self.closed = False

        def write(self, data: bytes) -> int:
            return len(data)

        def read(self, n: int) -> bytes:
            return b""

        def close(self) -> None:
            self.closed = True

    fakes = [FakeSer(), FakeSer()]

    def _fake_serial(**kwargs):
        _ = kwargs
        return fakes.pop(0)

    monkeypatch.setattr(pyserial, "Serial", _fake_serial)
    assert made.open(4, "", 1000000) is True
    first = made.ser
    made._parser.feed(b"\xab\x01")  # 欠片を残す
    assert made.open(4, "", 1000000) is True
    assert first.closed is True
    assert made.ser is not first
    assert len(made._parser._buf) == 0
    made._parser.feed(b"\xab\x01")
    second = made.ser
    made.close()
    assert second.closed is True
    assert made.ser is None
    assert len(made._parser._buf) == 0


def test_bcon_get_raw_serial_is_none_and_wakelink_falls_back():
    from core import WakeLink
    from core.transport import create_transport

    made = create_transport("bcon")
    written: list[bytes] = []

    class FakeSer:
        is_open = True

        def write(self, data: bytes) -> int:
            written.append(bytes(data))
            return len(data)

        def read(self, n: int) -> bytes:
            return b""

    made.ser = FakeSer()
    # バイナリ口をテキスト直読みへ渡さない。WakeLinkは例外なく失敗扱い。
    assert made.get_raw_serial() is None
    assert WakeLink.send_line(made, "Q") is False
    assert WakeLink.read_lines(made, 0.01) == []
    assert WakeLink.expect(made, "Q", "QOK", timeout=0.05) is None
    WakeLink.drain(made)
    assert written == []


def test_bcon_set_hooks_fire_on_send_row():
    from core.transport import create_transport

    made = create_transport("bcon")
    calls: list[tuple[str, str]] = []
    made.set_hooks(
        lambda row, show=True: calls.append(("begin", row)),
        lambda row, show=True: calls.append(("end", row)),
    )

    class FakeSer:
        is_open = True

        def write(self, data: bytes) -> int:
            return len(data)

    made.ser = FakeSer()
    made.send_row("10 08")
    assert [kind for kind, _ in calls] == ["begin", "end"]
    assert all(row == "10 08" for _, row in calls)
