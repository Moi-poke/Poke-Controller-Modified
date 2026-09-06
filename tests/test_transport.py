"""Transport / Sender / WakeLink の headless 検証。

実機（COM ポート・ファーム）が無くても回せるものだけを置く。
ハード必須の確認（遅延体感・end 時中立）は実機で行う。
"""

from core import Sender, Transport, WakeLink
from fakes import FakeTransport, NoSerTransport


def test_old_paths_alias_new() -> None:
    """旧位置（core/Transport.py 直下）は新配置の別名である。"""
    import core.Sender as old_s
    import core.Transport as old_t
    import core.serial.sender as new_s
    import core.transport as new_t

    assert old_t.Transport is new_t.Transport
    assert old_t.TextSerialTransport is new_t.TextSerialTransport
    assert old_t.PicoUartTransport is new_t.PicoUartTransport
    assert old_t.create_transport is new_t.create_transport
    assert old_t.register_transport is new_t.register_transport
    assert old_t.resolve_transport_name is new_t.resolve_transport_name
    assert old_s.Sender is new_s.Sender
    assert old_s.list_arbitration_modes is new_s.list_arbitration_modes
    assert old_s.resolve_arbitration_mode is new_s.resolve_arbitration_mode


def test_ui_names_are_classes_not_modules() -> None:
    """ui/ で生成する名前はクラスである（実起動の反省）。

    `from core import CommandLoader` はモジュールを束ねるため、
    生成に使う名前は `from core.X import X` で取ること。起動時の
    TypeError: 'module' object is not callable をここで防ぐ。
    """
    import inspect

    import ui.command_panel as command_panel

    assert inspect.isclass(command_panel.CommandLoader)


def test_calc_send_interval_boundaries() -> None:
    calc = Transport.TextSerialTransport.calc_send_interval
    # 9600bps では従来値 0.02 のまま
    assert calc(9600) == Transport.MIN_SEND_INTERVAL == 0.02
    # 高速では緩む（38400 で約 0.0115）
    fast = calc(38400)
    assert 0.0 < fast < Transport.MIN_SEND_INTERVAL
    # 不正値は落とさず既定へ
    assert calc(0) == Transport.MIN_SEND_INTERVAL
    assert calc(-1) == Transport.MIN_SEND_INTERVAL


def test_coalescable_only_stick() -> None:
    t = Transport.TextSerialTransport()
    t._before = "0x0003 8"
    assert t._coalescable("0x0003 8 80 80") is True
    assert t._coalescable("0x0004 8") is False
    assert t._coalescable("0x0003 7") is False


def test_live_capable_matrix() -> None:
    probe = Sender.Sender.__new__(Sender.Sender)
    assert probe._liveCapable(Transport.TextSerialTransport()) is False
    assert probe._liveCapable(Transport.PicoUartTransport()) is True
    # 未知 capability は legacy 等価（worker なし）で動く
    assert probe._liveCapable(NoSerTransport()) is False
    assert probe._liveCapable(FakeTransport()) is False


def test_unknown_capability_registers() -> None:
    assert (
        Transport.register_transport("test_ble_tmp", NoSerTransport, description="test")
        is True
    )
    try:
        made = Transport.create_transport("test_ble_tmp")
        assert made.capability == "MY_BLE"
    finally:
        assert Transport.unregister_transport("test_ble_tmp") is True


def test_open_never_raises() -> None:
    t = Transport.TextSerialTransport()
    # 不正 baud・存在しないポートでも例外なく False
    assert t.open(0, "", "not-a-number") is False  # type: ignore[arg-type]
    assert t.open(9999, "", 9600) is False


def test_wakelink_without_serial() -> None:
    # ser なし方式は例外なく失敗扱い（Sender は従来経路へ回す）
    assert WakeLink.send_line(NoSerTransport(), "Q") is False
    assert WakeLink.expect(NoSerTransport(), "Q", "QOK", timeout=0.05) is None
    WakeLink.drain(NoSerTransport())


def test_sender_writes_through_fake() -> None:
    sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
    assert sender.openSerial(0, "", 9600) is True
    assert sender.isOpened() is True
    sender.writeRow("0x0003 8")
    fake = sender.transport
    assert isinstance(fake, FakeTransport)
    assert fake.rows == ["0x0003 8"]
    sender.closeSerial()
    assert sender.isOpened() is False
