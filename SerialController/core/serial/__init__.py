"""serial - 姿勢と調停と行の組み立てを受け持つ層。

sender.py が Sender 本体（手順の束ね）、arbitration.py が入力調停の
判断、encoding.py が送信行の書式（純関数）を持つ。
旧位置の core/Sender.py からも同じ名前で読める。
"""

from core.serial.arbitration import (
    ARBITRATION_COOLDOWN as ARBITRATION_COOLDOWN,
    ARBITRATION_MODE as ARBITRATION_MODE,
    ARBITRATION_MODES as ARBITRATION_MODES,
    HUMAN_SOURCES as HUMAN_SOURCES,
    REJECT_NOTIFY_INTERVAL as REJECT_NOTIFY_INTERVAL,
    Arbiter as Arbiter,
    list_arbitration_modes as list_arbitration_modes,
    resolve_arbitration_mode as resolve_arbitration_mode,
)
from core.serial.encoding import (
    encode_pico_state as encode_pico_state,
    format_legacy_row as format_legacy_row,
    pico_field as pico_field,
    verify_pico_encoder as verify_pico_encoder,
)
from core.serial.sender import Sender as Sender
