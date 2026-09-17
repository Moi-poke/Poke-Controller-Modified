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
