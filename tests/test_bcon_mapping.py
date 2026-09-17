"""bcon写像のhostベクタ（実機不要）。"""


def test_bcon_map_a_and_y_hat_and_sticks():
    from core.serial.bcon_mapping import neutral_state, wire_row_to_state

    neutral = neutral_state()
    assert neutral == {
        "buttons": 0,
        "lx": 0x0800,
        "ly": 0x0800,
        "rx": 0x0800,
        "ry": 0x0800,
    }
    # A押下はwire 0x10（bit4）→VIIPER BTN_A (1<<1)
    assert wire_row_to_state("10 08")["buttons"] == (1 << 1)
    # Y押下はwire 0x0004（bit2）→VIIPER BTN_Y (1<<2)
    assert wire_row_to_state("0004 08 80 80 80 80")["buttons"] == (1 << 2)
    # HAT 0=Up→BTN_UP (1<<11)
    assert wire_row_to_state("0 0")["buttons"] == (1 << 11)
    # LSのみ→LX/LYへwire lx/ly（u8 0xFF→u16 0x0FF0）
    moved = wire_row_to_state("2 08 ff 80")
    assert (moved["lx"], moved["ly"]) == (0x0FF0, 0x0800)
    assert (moved["rx"], moved["ry"]) == (0x0800, 0x0800)


def test_bcon_hat_nine_way():
    from core.serial.bcon_mapping import wire_row_to_state

    up = 1 << 11
    right = 1 << 9
    down = 1 << 8
    left = 1 << 10
    cases = {
        0: up,
        1: up | right,
        2: right,
        3: down | right,
        4: down,
        5: down | left,
        6: left,
        7: up | left,
        8: 0,
    }
    for hat, want in cases.items():
        assert wire_row_to_state(f"0 {hat}")["buttons"] == want


def test_bcon_len12_roundtrip_matches_len8_center():
    from core.serial.bcon_mapping import (
        neutral_state,
        state_to_len8,
        state_to_len12,
        wire_row_to_state,
    )

    # 中立はu8中央0x80とu16中央0x0800で等価（0x80==0x800）。
    neutral = neutral_state()
    assert state_to_len8(neutral) == bytes(
        [0x00, 0x00, 0x00, 0x00, 0x80, 0x80, 0x80, 0x80]
    )
    assert state_to_len12(neutral) == bytes(
        [0x00, 0x00, 0x00, 0x00, 0x00, 0x08, 0x00, 0x08, 0x00, 0x08, 0x00, 0x08]
    )
    moved = wire_row_to_state("2 08 ff 80")
    assert (moved["lx"], moved["ly"]) == (0x0FF0, 0x0800)
    p8 = state_to_len8(moved)
    p12 = state_to_len12(moved)
    assert len(p8) == 8
    assert len(p12) == 12
    assert p8[:4] == p12[:4]
    assert p8[4] == 0xFF
    assert int.from_bytes(p12[4:6], "little") == 0x0FF0
    # LEN12→LEN8はu16>>4で一致する（中央0x0800>>4==0x80）。
    for i in range(4):
        u16 = int.from_bytes(p12[4 + i * 2 : 6 + i * 2], "little")
        assert ((u16 >> 4) & 0xFF) == p8[4 + i]


def test_bcon_map_rs_quirk_and_end():
    from core.serial.bcon_mapping import is_end_row, neutral_state, wire_row_to_state

    # RSのみ→RX/RYへwire lx/lyが載る
    moved = wire_row_to_state("1 08 ff 80")
    assert (moved["rx"], moved["ry"]) == (0x0FF0, 0x0800)
    assert (moved["lx"], moved["ly"]) == (0x0800, 0x0800)
    assert is_end_row("end") is True
    assert is_end_row("END") is True
    assert wire_row_to_state("end") == neutral_state()
