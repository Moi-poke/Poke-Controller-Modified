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


def test_bcon_map_rs_quirk_and_end():
    from core.serial.bcon_mapping import is_end_row, neutral_state, wire_row_to_state

    # RSのみ→RX/RYへwire lx/lyが載る
    moved = wire_row_to_state("1 08 ff 80")
    assert (moved["rx"], moved["ry"]) == (0x0FF0, 0x0800)
    assert (moved["lx"], moved["ly"]) == (0x0800, 0x0800)
    assert is_end_row("end") is True
    assert is_end_row("END") is True
    assert wire_row_to_state("end") == neutral_state()
