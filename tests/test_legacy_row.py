"""legacy 行書式の一文字一致の検証。

Keys.SendFormat.convert2str と Sender._buildRow は同じ
encoding.format_legacy_row を通す。ここでは両方へ同じ操作を
与え、出力が一致することを機械で確かめる（目視の代わり）。
"""

import random

from core import Sender
from core.Keys import Button, Direction, Hat, SendFormat, Stick
from fakes import FakeTransport

BUTTONS = [
    Button.Y,
    Button.B,
    Button.A,
    Button.X,
    Button.L,
    Button.R,
    Button.ZL,
    Button.ZR,
    Button.MINUS,
    Button.PLUS,
    Button.LCLICK,
    Button.RCLICK,
    Button.HOME,
    Button.CAPTURE,
]
HATS = [
    Hat.TOP,
    Hat.TOP_RIGHT,
    Hat.RIGHT,
    Hat.BTM_RIGHT,
    Hat.BTM,
    Hat.BTM_LEFT,
    Hat.LEFT,
    Hat.TOP_LEFT,
    Hat.CENTER,
]


def _drive_both(
    sender: Sender.Sender,
    fmt: SendFormat,
    btns: list[Button],
    hat: Hat | None,
    left: tuple[int, int] | None,
    right: tuple[int, int] | None,
) -> None:
    """両方へ同じ操作を与える（実フローと同じ申告経路で）。

    Sender 側は KeyPress と同じ pressButtons / setHat / setStick を
    通す。素の apply* は所有者合成の対象外で、後の合成で上書きされる。
    """
    fmt.setButton(btns)
    assert sender.pressButtons(btns)
    if hat is None:
        fmt.unsetHat()
        assert sender.setHat(None)
    else:
        fmt.setHat([hat])
        assert sender.setHat(int(hat))
    if left is not None:
        fmt.setAnyDirection([Direction(Stick.LEFT, left)])
        assert sender.setStick("L", left[0], 255 - left[1])
    if right is not None:
        fmt.setAnyDirection([Direction(Stick.RIGHT, right)])
        assert sender.setStick("R", right[0], 255 - right[1])


def test_legacy_row_matches_sendformat() -> None:
    rng = random.Random(42)
    for _ in range(60):
        sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
        fmt = SendFormat()
        btns = [b for b in BUTTONS if rng.random() < 0.3]
        hat = rng.choice(HATS + [None])
        left = (rng.randrange(256), rng.randrange(256)) if rng.random() < 0.7 else None
        right = (rng.randrange(256), rng.randrange(256)) if rng.random() < 0.7 else None
        _drive_both(sender, fmt, btns, hat, left, right)
        assert sender._buildRow() == fmt.convert2str()


def test_legacy_row_neutral() -> None:
    sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
    fmt = SendFormat()
    fmt.resetAllButtons()
    fmt.resetAllDirections()
    fmt.unsetHat()
    sender.releaseAll()
    # 中立の完全行。印は両方立っているので座標まで含む
    assert sender._buildRow() == fmt.convert2str()
    assert sender._buildRow().startswith("0x0000 8")
