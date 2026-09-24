"""bcon baud切替のUI側文言・対応表。切替の実体は持たない。

BAUD_SET送出・adopt確認・自動復帰は transport層（BconTransport.set_baud_index）
の責務である。ここは index↔bps の読み替えと、確定／復帰の表示文だけを
持ち、BAUD_TABLE / DEFAULT_BAUD_INDEX は bcon_protocol から読む
（値を写さない。表の更新に追従するため）。

use_len12 について（監査メモ 2026-09-23・UI N/A の理由）:
- BconTransport.__init__(use_len12=False) は transport旗のみで、
  send_row系がLEN12/LEN8を切り替えるためのものである。
- UI露出は無し（ui/serial_panel.pyは汎用baudのみ、BconSetup.pyにbaud行
  なし、setterはtransport層set_baud_indexのみ、永続化なし）。
- 旗の再建を要するcheckboxは金メッキになるため置かない。ここに理由を
  残し、UI追加は行わない。
"""

from __future__ import annotations

from core.transport.bcon_protocol import BAUD_TABLE, DEFAULT_BAUD_INDEX

# 既定index。bcon_protocol の既定（=3・1Mbps）をそのまま指す。
BAUD_DEFAULT_INDEX: int = DEFAULT_BAUD_INDEX

# Combobox表示（index 0-4 ↔ 115200/460800/921600/1M/2M）。
BAUD_DISPLAY_LABELS: dict[int, str] = {
    0: "115200",
    1: "460800",
    2: "921600",
    3: "1M",
    4: "2M",
}


def baud_bps_for_index(index: int) -> int:
    """indexに対応するbpsを返す。表に無いindexは送らず ValueError。"""
    try:
        key = int(index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"baud指定が不正です: {index!r}") from None
    try:
        return int(BAUD_TABLE[key])
    except KeyError:
        raise ValueError(f"baud指定が範囲外です: {index!r}") from None


def baud_index_sendable(index: int) -> bool:
    """BAUD_SETを投げてよいindexか。不正値はFalse（送らない）。"""
    try:
        baud_bps_for_index(index)
    except (TypeError, ValueError):
        return False
    return True


def baud_index_for_label(label: object) -> int | None:
    """Combobox表示からindexへ。読めなければNone（送らない）。"""
    try:
        text = str(label).strip()
    except Exception:
        return None
    for key, name in BAUD_DISPLAY_LABELS.items():
        if text == name:
            return key
    return None


def baud_label_for_index(index: int) -> str:
    """indexに対応するCombobox表示。表に無いindexは ValueError。"""
    baud_bps_for_index(index)
    return BAUD_DISPLAY_LABELS[int(index)]  # type: ignore[arg-type]


def baud_adopt_message(new_bps: int) -> str:
    """adopt成功時の確定文言。切替完了と新bpsを入れる。"""
    return f"baudを{int(new_bps)}bpsへ切替えました。"


def baud_revert_message(new_bps: int, old_bps: int) -> str:
    """adopt失敗時の復帰文言。新旧bpsを入れ、確定文面とは変える。

    transport層が旧レートへ自動復帰するため、UIはその旨を言い、
    baud_huntでの探し直しを勧める。
    """
    return (
        f"baudの切替えに失敗しました。{int(new_bps)}bpsへ替わらず"
        f"{int(old_bps)}bpsへ戻りました（transportが自動復帰）。"
        "baud_huntで探し直してください。"
    )
