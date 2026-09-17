"""bcon純粋層のhostベクタ（実機不要）。"""


def test_bcon_crc8_vector():
    from core.transport.bcon_protocol import crc8

    assert crc8(b"123456789") == 0xF4


def test_bcon_frame_build_state_neutral():
    from core.transport.bcon_protocol import T_STATE, frame_build

    payload = bytes([0x00, 0x00, 0x00, 0x00, 0x80, 0x80, 0x80, 0x80])
    frame = frame_build(T_STATE, payload, 0x00)
    assert frame[0] == 0xAB
    assert frame[1] == T_STATE
    assert frame[2] == 8
    assert len(frame) == 13


def test_bcon_parser_slides_on_bad_crc():
    from core.transport.bcon_protocol import T_PING, BconParser, frame_build

    good = frame_build(T_PING, b"", 0x05)
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    parser = BconParser()
    out = parser.feed(bytes(bad) + good)
    assert len(out) == 1
    assert out[0][0] == T_PING
    assert out[0][2] == 0x05


def test_bcon_hello_len_and_baud_table():
    from core.transport.bcon_protocol import (
        BAUD_TABLE,
        DEFAULT_BAUD_INDEX,
        T_BAUD_SET,
        T_HELLO,
        T_HELLO_ACK,
        frame_build,
        proto_expected_len,
    )

    assert proto_expected_len(T_HELLO) == 2
    assert proto_expected_len(T_HELLO_ACK) == 4
    assert proto_expected_len(T_BAUD_SET) == 1
    assert BAUD_TABLE[DEFAULT_BAUD_INDEX] == 1000000
    frame = frame_build(T_HELLO, bytes([0x04, 0x01]), 0x07)
    assert len(frame) == 7
