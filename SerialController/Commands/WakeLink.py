"""Commands/WakeLink.py - 後方互換の再公開口.

実体は core/WakeLink.py へ移った。既存のコマンドは
    from Commands.WakeLink import query
と書いたまま変えずに使える。新規のコードは core 側から読むこと。
"""

from core.WakeLink import (
    _lock_of as _lock_of,
    _ser_of as _ser_of,
    drain as drain,
    expect as expect,
    query as query,
    read_lines as read_lines,
    send_line as send_line,
)
