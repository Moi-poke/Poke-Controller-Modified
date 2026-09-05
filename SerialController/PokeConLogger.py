"""PokeConLogger.py - 後方互換の再公開口.

実体は core/PokeConLogger.py へ移った。新規のコードは core 側から読むこと。
"""

from core.PokeConLogger import (
    ColorfulHandler as ColorfulHandler,
    root_logger as root_logger,
)
