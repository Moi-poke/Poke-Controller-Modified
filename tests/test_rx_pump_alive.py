"""受信ポンプが黙って死なないこと（再試行と状態公開）を固定する。実機は要らない。"""

from __future__ import annotations

import threading
from typing import Any

from loguru import logger


class _FailingSer:
    """読み取りが必ず例外になる偽の線。"""

    def __init__(self) -> None:
        self.reads = 0

    def read(self, _n: int = 1) -> bytes:
        self.reads += 1
        raise OSError("わざと")


def _build(module_name: str, class_name: str, ser: Any) -> Any:
    import importlib

    mod = importlib.import_module(module_name)
    cls = getattr(mod, class_name)
    transport = cls.__new__(cls)
    transport._rx_stop = threading.Event()
    transport._rx_dead = False
    transport._logger = logger
    transport.ser = ser
    return transport


def test_bcon_rx_pump_retries_before_giving_up() -> None:
    """Given: 読み取りが必ず例外になる線 / When: ポンプを回す / Then: 数回試してから死んだ印を残すこと。"""
    transport = _build("core.transport.bcon", "BconTransport", _FailingSer())
    transport._parser = None

    transport._rx_loop()  # 例外を投げずに戻る（諦めて break する）

    assert transport.rx_dead() is True
    # 1 回の失敗で諦めると、以後チャンクが来ても誰も読まない。
    assert transport.ser.reads > 1, "1 回の失敗で諦めている"


def test_bcon_rx_pump_without_a_line_is_not_dead() -> None:
    """Given: 線が無い（未接続） / When: ポンプを回す / Then: 死んだ印を立てないこと。"""
    transport = _build("core.transport.bcon", "BconTransport", None)
    transport._parser = None

    transport._rx_loop()

    # 未接続は正常な終了。ここで印を立てると、次の接続が「死んでいる」と誤って見える。
    assert transport.rx_dead() is False


def test_text_rx_pump_retries_before_giving_up() -> None:
    """Given: 読み取りが必ず例外になる線 / When: テキスト方式のポンプを回す / Then: 同じ扱いであること。"""
    transport = _build(
        "core.transport.text_serial", "TextSerialTransport", _FailingSer()
    )

    transport._rx_loop()

    assert transport.rx_dead() is True
    assert transport.ser.reads > 1, "1 回の失敗で諦めている"
