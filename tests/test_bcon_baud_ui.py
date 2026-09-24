"""bcon baud切替UIのRED試験（実機不要・headless）。

監査メモ（use_len12露出調査・2026-09-23）:
- BconTransport.__init__(use_len12=False)は bcon.py:112,121 に存在し、
  send_row系 bcon.py:365 でLEN12/LEN8を切替える。
- bcon_proc.py:219 で子へ透過、490-494でproxyが保持し593でcfg経由で渡す。
- UI露出は無し：ui/serial_panel.pyは汎用baudコンボのみ
  （WindowUtils.BAUD_RATE_VALUES=9600〜115200）、baud index切替ウィジェット無し。
- BconSetup.pyにbaud行無し（BAUD_SETは型名0x36と注意文言のみ）。
- docs/BCON_TRANSPORT.md:47「画面上のボタンは無い」と一致。
- setterはtransport層set_baud_indexのみ、永続化（settings.ini/config.py）無し。
- 結論：use_len12 UIはN/A（transport旗のみ、UI未実装のため重複なし）。
"""

from __future__ import annotations

import pytest


def test_baud_index_to_bps_uses_protocol_table():
    """各indexはBAUD_TABLEの値に一致する（期待値は表から読む）。"""
    from core.transport import bcon_baud as baud_ui
    from core.transport.bcon_protocol import BAUD_TABLE

    # 表の5件すべてが引けること。ハードコードせず表に当てる。
    for index in sorted(BAUD_TABLE.keys()):
        assert baud_ui.baud_bps_for_index(index) == BAUD_TABLE[index]


def test_baud_default_index_is_table_default():
    """既定indexはDEFAULT_BAUD_INDEX（=3）で1Mbpsを指す。"""
    from core.transport import bcon_baud as baud_ui
    from core.transport.bcon_protocol import BAUD_TABLE, DEFAULT_BAUD_INDEX

    assert DEFAULT_BAUD_INDEX == 3
    assert BAUD_TABLE[DEFAULT_BAUD_INDEX] == 1000000
    assert (
        baud_ui.baud_bps_for_index(baud_ui.BAUD_DEFAULT_INDEX)
        == BAUD_TABLE[DEFAULT_BAUD_INDEX]
    )


def test_baud_invalid_index_never_sent():
    """範囲外indexは送出せず拒否する（BAUD_SETを投げない）。"""
    from core.transport import bcon_baud as baud_ui
    from core.transport.bcon_protocol import BAUD_TABLE

    for bad in (-1, 5, 99):
        assert bad not in BAUD_TABLE
        with pytest.raises(ValueError):
            baud_ui.baud_bps_for_index(bad)
    # 送出ガード：不正indexは送信可否がFalse。
    assert baud_ui.baud_index_sendable(-1) is False
    assert baud_ui.baud_index_sendable(5) is False


def test_baud_adopt_confirm_message():
    """adopt成功時は確定文言（切替完了＋bps入り）を出す。"""
    from core.transport import bcon_baud as baud_ui
    from core.transport.bcon_protocol import BAUD_TABLE, DEFAULT_BAUD_INDEX

    bps = BAUD_TABLE[DEFAULT_BAUD_INDEX]
    msg = baud_ui.baud_adopt_message(bps)
    assert str(bps) in msg
    assert "切替" in msg


def test_baud_revert_display_message():
    """adopt失敗時は復帰文言（新旧bps入り・確定と文面が違う）を出す。"""
    from core.transport import bcon_baud as baud_ui
    from core.transport.bcon_protocol import BAUD_TABLE

    new_bps = BAUD_TABLE[1]
    old_bps = BAUD_TABLE[3]
    adopt = baud_ui.baud_adopt_message(new_bps)
    revert = baud_ui.baud_revert_message(new_bps, old_bps)
    assert str(new_bps) in revert
    assert str(old_bps) in revert
    assert revert != adopt
    assert "戻" in revert
