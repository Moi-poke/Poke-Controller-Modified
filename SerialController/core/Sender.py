"""Sender.py - 後方互換の再公開口.

実体は core/serial/ へ移った（sender.py が Sender 本体、
arbitration.py が入力調停、encoding.py が行の組み立て）。既存のコードは
    from core.Sender import Sender
    from core import Sender
と書いたまま変えずに使える。新規のコードは core.serial 側から読むこと.
"""

from core.serial import (
    ARBITRATION_COOLDOWN as ARBITRATION_COOLDOWN,
    ARBITRATION_MODE as ARBITRATION_MODE,
    ARBITRATION_MODES as ARBITRATION_MODES,
    HUMAN_SOURCES as HUMAN_SOURCES,
    REJECT_NOTIFY_INTERVAL as REJECT_NOTIFY_INTERVAL,
    Arbiter as Arbiter,
    Sender as Sender,
    list_arbitration_modes as list_arbitration_modes,
    resolve_arbitration_mode as resolve_arbitration_mode,
)
from core.serial.sender import (
    INPUT_LOG_FORMAT as INPUT_LOG_FORMAT,
    INPUT_LOG_STICK_CHANGE as INPUT_LOG_STICK_CHANGE,
)
from core.transport import MIN_SEND_INTERVAL as MIN_SEND_INTERVAL
