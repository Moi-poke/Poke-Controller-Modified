"""SerialService の手順の検証。線は偽物、画面なしで回す。"""

from core import Sender, Transport
from fakes import FakeTransport
from services.serial_service import SenderSpec, SerialService


def make_service() -> tuple[SerialService, list[str]]:
    notices: list[str] = []
    service = SerialService(
        notify_user=notices.append,
        base_dir=".",
        input_log_emit=lambda line: None,
    )
    return service, notices


def make_spec(**over: object) -> SenderSpec:
    values: dict[str, object] = {
        "transport_name": "legacy_text",
        "is_show_serial": False,
        "arbitration_mode": "off",
        "arbitration_cooldown": 2.0,
        "input_log_format": "simple",
        "input_log_actions": "",
        "input_log_enabled": True,
        "input_log_stick_change": False,
    }
    values.update(over)
    return SenderSpec(**values)  # type: ignore[arg-type]


def test_build_sender_legacy() -> None:
    service, _ = make_service()
    service.build_sender(make_spec())
    assert isinstance(service.sender, Sender.Sender)
    assert service.sender.getTransportName() == "legacy_text"
    assert service.sender.isInputLogLinked() is True


def test_selected_transport_override() -> None:
    service, _ = make_service()
    assert service.selected_transport_name("legacy_text") == "legacy_text"
    service.transport_override = "pico_uart"
    assert service.selected_transport_name("legacy_text") == "pico_uart"
    service.clear_override()
    assert service.selected_transport_name("legacy_text") == "legacy_text"
    # 未知名は既定へ落ちる（理由つき）
    assert (
        service.selected_transport_name("no_such_name") == Transport.DEFAULT_TRANSPORT
    )


def test_switch_same_name_noop() -> None:
    service, _ = make_service()
    service.build_sender(make_spec())
    switched, linked = service.switch_transport("legacy_text")
    assert switched is False
    assert linked is True


def test_switch_to_dummy_legacy() -> None:
    class DummyLegacy(FakeTransport):
        name = "dummy_legacy_tmp"

    assert Transport.register_transport("dummy_legacy_tmp", DummyLegacy) is True
    try:
        service, _ = make_service()
        service.build_sender(make_spec())
        switched, linked = service.switch_transport("dummy_legacy_tmp")
        assert switched is True
        assert linked is True
        assert service.sender is not None
        assert service.sender.getTransportName() == "dummy_legacy_tmp"
    finally:
        assert Transport.unregister_transport("dummy_legacy_tmp") is True


def test_gui_call_conventions() -> None:
    """GUI が使う呼び出し形（キーワード引数含む）の回帰検証。

    実起動で TypeError（unexpected keyword argument）が出た反省から、
    画面側と同じ渡し方で呼べることをここで確かめる。
    """
    from loguru import logger

    service, _ = make_service()
    assert service.resolve_arbitration("human", logger=logger) == "human"
    assert service.resolve_arbitration("typo!", logger=logger) == "off"
    service.build_sender(make_spec())
    switched, linked = service.switch_transport("legacy_text", transport_logger=logger)
    assert switched is False
    assert linked is True


def test_connect_success_and_disconnect() -> None:
    service, _ = make_service()
    service.sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
    connected, keyboard_active = service.connect(
        port_num=0,
        port_name="fake",
        baud=9600,
        keyboard_enabled=False,
        setting_path="",
    )
    assert connected is True
    assert keyboard_active is False
    assert service.is_open() is True
    assert service.key_press is not None
    service.disconnect()
    assert service.is_open() is False
    assert service.key_press is None


def test_connect_failure() -> None:
    service, notices = make_service()
    service.build_sender(make_spec())
    # 実機が無いので開けない。例外なく (False, False) で戻る
    connected, keyboard_active = service.connect(
        port_num=9999,
        port_name="",
        baud=9600,
        keyboard_enabled=True,
        setting_path="",
    )
    assert (connected, keyboard_active) == (False, False)
    assert any("開けませんでした" in m for m in notices)


def test_keyboard_without_connection() -> None:
    service, _ = make_service()
    service.sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
    # 未接続（key_press なし）では理由を返して断る
    err = service.set_keyboard_enabled(True, "")
    assert err is not None
    assert service.keyboard is None
    # 切る側は常に成功
    assert service.set_keyboard_enabled(False, "") is None


def test_shutdown() -> None:
    service, _ = make_service()
    service.sender = Sender.Sender(is_show_serial=False, transport=FakeTransport())
    assert service.shutdown() is False  # 開いていない
    service.connect(
        port_num=0,
        port_name="fake",
        baud=9600,
        keyboard_enabled=False,
        setting_path="",
    )
    assert service.shutdown() is True  # 閉じた


def test_load_plugins_noop() -> None:
    service, notices = make_service()
    service.load_plugins("")
    service.load_plugins("no_such_dir_at_all")
    assert notices == []


def test_apply_input_log_no_crash() -> None:
    service, _ = make_service()
    service.apply_input_log("simple", "", True, False)  # sender 無しは何もしない
    service.build_sender(make_spec())
    service.apply_input_log("simple", "", True, False)
    service.set_input_log_enabled(False)
    service.set_arbitration("off", 2.0)
