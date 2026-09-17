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


def test_bcon_fallback_port_matches_legacy(monkeypatch):
    import serial as pyserial
    from core.transport import TextSerialTransport, create_transport

    made = create_transport("bcon")
    seen: dict[str, object] = {}

    class FakeSer:
        is_open = True

        def __init__(self, **kwargs) -> None:
            seen.update(kwargs)

        def write(self, data: bytes) -> int:
            return len(data)

        def read(self, n: int) -> bytes:
            return b""

        def close(self) -> None:
            pass

    monkeypatch.setattr(pyserial, "Serial", FakeSer)
    assert made.open(8, "", 1000000) is True
    # portName空の代替経路はlegacyと同一の口を指す（portToNumber往復を保つ）。
    assert seen.get("port") == TextSerialTransport._default_port_path(8)
    made.close()


def test_bcon_use_len12_sends_len12_frame_while_default_stays_len8():
    from core.transport import BconTransport, create_transport

    def _send_once(made, row="end"):
        written: list[bytes] = []

        class FakeSer:
            is_open = True

            def write(self, data: bytes) -> int:
                written.append(bytes(data))
                return len(data)

        made.ser = FakeSer()
        made.send_row(row)
        assert len(written) == 1
        return written[0]

    default = create_transport("bcon")
    frame8 = _send_once(default)
    assert frame8[0] == 0xAB
    assert frame8[1] == 0x01
    assert frame8[2] == 8
    assert len(frame8) == 13

    explicit8 = BconTransport()
    assert explicit8.use_len12 is False
    assert _send_once(explicit8)[2] == 8

    wide = BconTransport(use_len12=True)
    assert wide.use_len12 is True
    frame12 = _send_once(wide)
    assert frame12[0] == 0xAB
    assert frame12[1] == 0x01
    assert frame12[2] == 12
    assert len(frame12) == 17
    # 中立のLEN12 payloadはBTN u32LE＋u16LE中央0x0800×4（中央は不変）。
    assert frame12[3:15] == bytes(
        [0x00, 0x00, 0x00, 0x00, 0x00, 0x08, 0x00, 0x08, 0x00, 0x08, 0x00, 0x08]
    )
