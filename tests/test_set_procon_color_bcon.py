"""set_procon_color の bcon 統一ベクタ（実機不要・RED）。

方針: bcon の口では O 行・query・WakeLink・direct_serial を使わず、
統一の色読み（BconSetup 側）で 12B を組み立て send_config(0x32) を
1 発だけ送り、直後に request_status を読んで errcode を
describe_bcon_errcode で解く。いまの実装は O 行を送るため赤のはず。
"""

from typing import Any

import pytest
from BconSetup import (
    build_color_payload,
    describe_bcon_errcode,
    parse_color_hex,
)
from Commands.PythonCommands.set_procon_color import (
    PRESET,
    SetProconColor,
    normalize,
)


class _FakeBconTransport:
    """bcon の口の代役。線には触らず呼び順だけ残す。"""

    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self) -> None:
        # _color_transport が ser の有無を見るため空でも持たせる。
        self.ser = object()
        self.send_config_calls: list[tuple[int, bytes]] = []
        self.request_status_calls = 0

    def is_open(self) -> bool:
        return True

    def send_config(self, type_: int, payload: bytes) -> bool:
        self.send_config_calls.append((int(type_), bytes(payload)))
        return True

    def request_status(self, timeout: float = 1.0) -> dict[str, int]:
        _ = timeout
        self.request_status_calls += 1
        return {
            "flags": 0x00,
            "last_seq": 1,
            "err_crc": 0,
            "err_drop": 0,
            "errcode": 0x12,
        }


def _make_script(
    transport: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, list[str], dict[str, Any]]:
    """ダイアログと出口を差し替えた検査体を作る。"""
    import Commands.PythonCommands.set_procon_color as mod

    script = SetProconColor()
    # 送り口は bcon の偽物だけに結ぶ。
    script.keys = type("Keys", (), {})()
    script.keys.ser = type("Sender", (), {})()
    script.keys.ser.transport = transport

    logs: list[str] = []
    script.print2 = logs.append  # type: ignore[method-assign]
    script.finish = lambda: None  # type: ignore[method-assign]
    script.wait = lambda _sec: None  # type: ignore[method-assign]

    seen: dict[str, Any] = {"query": [], "direct": []}

    def _fake_query(_transport: Any, row: str, *_a: Any, **_k: Any) -> list[str]:
        seen["query"].append(row)
        return []

    def _fake_direct(rows: list[str], waits: list[float]) -> None:
        _ = waits
        seen["direct"].extend(rows)

    # WakeLink の query は bcon の口では呼ばないはず（呼ばれたら赤）。
    monkeypatch.setattr(mod, "query", _fake_query)
    script.direct_serial = _fake_direct  # type: ignore[method-assign]
    return script, logs, seen


def test_bcon_path_sends_color_set_once() -> None:
    """bcon の口では 12B を send_config(0x32) で1発だけ送る（いまは赤）。"""
    import pytest as _pt

    _mp = _pt.MonkeyPatch()
    try:
        transport = _FakeBconTransport()
        script, logs, seen = _make_script(transport, _mp)
        # 1 回目の対話だけ使う（確かめなし・プリセット直送）。
        choice = "公式 ネオン (青/赤)"
        script.dialogue6widget = lambda *a, **k: [choice, False]  # type: ignore[method-assign]

        # Given: プリセット4色（統一の読みで 12B になるはず）。
        base = PRESET[choice]
        slots = [parse_color_hex(c) for c in base]
        assert all(s is not None for s in slots)
        expected = build_color_payload(slots)
        assert len(expected) == 12

        # When: 色変えをその場で走らせる。
        script.do()

        # Then: send_config(0x32, 12B) を1発だけ。O 行系は一切使わない。
        assert transport.send_config_calls == [(0x32, expected)]
        assert seen["query"] == []
        assert seen["direct"] == []
        assert all(not row.startswith("O ") for row in seen["query"])
        assert all(not row.startswith("O ") for row in seen["direct"])
        # 直後の STATUS を読み、errcode=0x12 を日本語で解く。
        assert transport.request_status_calls >= 1
        want = describe_bcon_errcode(0x12)
        assert "CONFIG拒否" in want
        assert any(want in line for line in logs)
    finally:
        _mp.undo()


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        # 小文字・そのまま。
        ("ff0000", (255, 0, 0)),
        # 先頭 #・大文字も受ける。
        ("#00FF00", (0, 255, 0)),
        # 先頭 0x も受ける（統一の読み）。
        ("0x0000ff", (0, 0, 255)),
        ("0XABCDEF", (0xAB, 0xCD, 0xEF)),
    ],
)
def test_normalize_parity_with_parse_color_hex(
    raw: str, want: tuple[int, int, int]
) -> None:
    """normalize と parse_color_hex の受付をそろえる（0x 差で赤）。"""
    # Given: 受け付けるはずの書き方。
    # When: 両方の読みに通す。
    left = normalize(raw)
    right = parse_color_hex(raw)
    # Then: どちらも同じ RGB になる。
    assert left is not None
    assert right is not None
    assert tuple(int(left[i : i + 2], 16) for i in (0, 2, 4)) == want
    assert right == want


@pytest.mark.parametrize("raw", ["f00", "fff", "zz1234", "", "12345", "1234567"])
def test_short_hex_rejected_by_both_readers(raw: str) -> None:
    """3桁などは両方とも受け付けない（意図違いの色を出さない）。"""
    assert normalize(raw) is None
    assert parse_color_hex(raw) is None


def test_canonical_slot_names_decision() -> None:
    """正準の部品名をここに固定する。どちらかが動いたら赤（要再決定）。"""
    from BconSetup import COLOR_SLOT_NAMES
    from Commands.PythonCommands.set_procon_color import PART_NAMES

    # Given: いまの両者の並び（正準の決定として記録する）。
    # When/Then: ずれていたら統一の対応表を作り直す合図。
    assert PART_NAMES == ("本体", "ボタン", "左グリップ", "右グリップ")
    assert COLOR_SLOT_NAMES == ("本体", "本体2", "左", "右")
    # 正準の対応: 台本の i 番目をそのまま 12B の i 番目へ（順序は変えない）。
    # 台本「ボタン」は荷物 slot1（本体2）へ載る。変えるならここを直す。
    canonical = {"本体": 0, "ボタン": 1, "左グリップ": 2, "右グリップ": 3}
    assert [canonical[name] for name in PART_NAMES] == [0, 1, 2, 3]
    assert len(PART_NAMES) == len(COLOR_SLOT_NAMES) == 4
