"""Commands/StickCommand.py - 後方互換の再公開口.

実体は core/StickCommand.py へ移った。新規のコードは core 側から読むこと。
"""

from core.StickCommand import (
    StickCommand as StickCommand,
    StickLeft as StickLeft,
    StickRight as StickRight,
)
