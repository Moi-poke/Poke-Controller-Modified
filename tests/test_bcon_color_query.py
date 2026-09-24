"""bcon色問合せのhostベクタ（実機不要・RED）。

FW契約はLOCKED（Switch-bcon側）: T_COLOR_GET=0x39（LEN0・PC→Pico）、
T_COLOR_INFO=0x3A（LEN12・Pico→PC・spi_color_6050の先頭12B）。
FW_MINOR=3。値はFW錠に由来するため下の期待値は直書きでよい。
"""

from typing import Any

# FW錠の値（Switch-bconのT_COLOR_GET/T_COLOR_INFOに由来）。
FW_T_COLOR_GET = 0x39
FW_T_COLOR_INFO = 0x3A

# 読取失敗時の表示文言（草案）。実装はこの一文を含むこと。
COLOR_READ_FALLBACK_NOTE = "色の読み取りに失敗しました。000000で表示します。"


def test_protocol_knows_color_ids() -> None:
    """bcon純粋層が問合せ2型を知っている（FW錠と一致）。"""
    from core.transport.bcon_protocol import T_COLOR_GET, T_COLOR_INFO

    assert T_COLOR_GET == FW_T_COLOR_GET
    assert T_COLOR_INFO == FW_T_COLOR_INFO
    assert T_COLOR_GET == 0x39
    assert T_COLOR_INFO == 0x3A


def test_proto_expected_len_color() -> None:
    """GETはLEN0・INFOはLEN12（FW錠どおり）。"""
    from core.transport.bcon_protocol import (
        T_COLOR_GET,
        T_COLOR_INFO,
        proto_expected_len,
    )

    assert proto_expected_len(T_COLOR_GET) == 0
    assert proto_expected_len(T_COLOR_INFO) == 12


def test_frame_build_roundtrip_color() -> None:
    """GET空・INFO12Bが組立→分解で往復する。"""
    from core.transport.bcon_protocol import (
        T_COLOR_GET,
        T_COLOR_INFO,
        BconParser,
        frame_build,
    )

    payload12 = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0])
    get_frame = frame_build(T_COLOR_GET, b"", 0x01)
    info_frame = frame_build(T_COLOR_INFO, payload12, 0x02)
    parser = BconParser()
    out = parser.feed(get_frame + info_frame)
    assert out == [(T_COLOR_GET, b"", 0x01), (T_COLOR_INFO, payload12, 0x02)]


def test_request_color_returns_12b_or_none_on_timeout() -> None:
    """問合せ助けの契約：12B成功か、来なければNone（例外なし）。

    助けはまだ無いためこのimportでREDになる（実装後にGREEN化）。
    """
    from BconSetup import request_color

    class FakeOk:
        def request_color(self, timeout: float = 1.0) -> bytes | None:
            _ = timeout
            return bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0])

    class FakeTimeout:
        def request_color(self, timeout: float = 1.0) -> bytes | None:
            _ = timeout
            return None

    assert request_color(FakeOk()) == bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0])
    assert request_color(FakeTimeout(), timeout=0.1) is None


def test_prefill_falls_back_to_zeros_with_note_on_failure() -> None:
    """前埋めは失敗時に000000＋注意で倒す（窓は作らない）。

    口はまだ無いためこのimportでREDになる（実装後にGREEN化）。
    """
    from BconSetup import prefill_color_entries

    class StubVar:
        def __init__(self, value: str = "") -> None:
            self._value = value

        def get(self) -> str:
            return self._value

        def set(self, value: str) -> None:
            self._value = value

    class FailingTransport:
        def request_color(self, timeout: float = 1.0) -> bytes | None:
            _ = timeout
            return None

    entries = [StubVar("ff0000") for _ in range(4)]
    notes: list[str] = []
    prefill_color_entries(FailingTransport(), entries, notes.append)
    assert [var.get() for var in entries] == ["000000"] * 4
    assert any(COLOR_READ_FALLBACK_NOTE in note for note in notes)


def test_preview_canvas_maps_entry_to_fill_on_input() -> None:
    """入力のたびにEntry文字列→Canvasのfillへ映す（偽Canvas・実Tkなし）。

    口はまだ無いためこのimportでREDになる（実装後にGREEN化）。
    """
    from BconSetup import update_color_preview

    class StubVar:
        def __init__(self, value: str) -> None:
            self._value = value

        def get(self) -> str:
            return self._value

    class FakeCanvas:
        def __init__(self) -> None:
            self.fills: dict[int, str] = {}

        def itemconfigure(self, item: int, fill: str) -> None:
            self.fills[item] = fill

    entries = [
        StubVar("ff0000"),
        StubVar("00ff00"),
        StubVar("0000ff"),
        StubVar("000000"),
    ]
    canvas = FakeCanvas()
    items = [1, 2, 3, 4]
    update_color_preview(entries, canvas, items)
    assert canvas.fills[1] == "#ff0000"
    assert canvas.fills[2] == "#00ff00"
    assert canvas.fills[3] == "#0000ff"
    assert canvas.fills[4] == "#000000"


def test_no_real_tk_in_this_module() -> None:
    """この試験盤は実Tkを作らない（ヘッドレス規律）。"""
    import pathlib

    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert ("tkinter" + ".Tk()") not in src
    assert ("tk" + ".Tk()") not in src


def test_color_query_uses_absolute_imports() -> None:
    """絶対import規律（SerialController起点・相対なし）。"""
    import pathlib

    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert "from BconSetup import" in src or "from core.transport" in src
    assert ("from " + ".BconSetup") not in src
    assert ("from " + ". import") not in src


def _unused_guard(obj: Any) -> None:
    """未使用警告避けの捨て口（呼ばない）。"""
    _ = obj
