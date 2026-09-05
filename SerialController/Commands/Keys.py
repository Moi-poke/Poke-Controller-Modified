"""Commands/Keys.py - 後方互換の再公開口.

実体は core/Keys.py へ移った。既存のコマンドは
    from Commands.Keys import Button, Direction
と書いたまま変えずに使える。新規のコードは core 側から読むこと。
"""

from core.Keys import (
    CENTER as CENTER,
    MAX_VAL as MAX_VAL,
    MIN_VAL as MIN_VAL,
    Button as Button,
    Direction as Direction,
    Hat as Hat,
    KeyPress as KeyPress,
    SendFormat as SendFormat,
    Stick as Stick,
    Tilt as Tilt,
)
