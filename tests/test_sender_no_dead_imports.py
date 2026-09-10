"""Sender の不要な互換 import が無いことの検証。

線の実体は Transport が持つ。姿勢側に os/platform/serial は要らない。
"""

from __future__ import annotations


def test_no_dead_compat_imports() -> None:
    """os / platform / serial を直に束ねない。"""
    import core.serial.sender as sender_mod

    assert not hasattr(sender_mod, "os")
    assert not hasattr(sender_mod, "platform")
    assert not hasattr(sender_mod, "serial")


def test_sender_still_works_with_fake() -> None:
    """束ねを外しても姿勢→線の受け渡しは変わらない。"""
    from core import Sender, Transport
    from fakes import FakeTransport

    sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
    assert sender.openSerial(0, "", 9600) is True
    sender.writeRow("0x0003 8")
    fake = sender.transport
    assert isinstance(fake, FakeTransport)
    assert fake.rows == ["0x0003 8"]
    # Transport 由来の定数は引き続き読める。
    assert float(sender._send_interval) >= 0.0
    _ = Transport.TextSerialTransport
