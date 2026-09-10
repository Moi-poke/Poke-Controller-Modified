"""Pico UART は 115200 固定の検証（AGENTS.md の要求）。

既定 9600 のまま開くと watchdog（200ms 維持）に間に合わず、
取りこぼす。PICO 能力の線は 115200 へ寄せること。
"""

from __future__ import annotations

from typing import Any

from core.transport.text_serial import PicoUartTransport, TextSerialTransport


def _patched_open(t: PicoUartTransport) -> dict[str, Any]:
    """_open_serial とポンプを差し替え、呼ばれた引数を拾う。"""
    seen: dict[str, Any] = {}

    def fake_open(path: str, baudrate: int) -> bool:
        seen["path"] = path
        seen["baud"] = baudrate
        return True

    t._open_serial = fake_open  # type: ignore[method-assign]
    t.start_rx_pump = lambda: True  # type: ignore[method-assign]
    return seen


def test_pico_coerces_9600_to_115200() -> None:
    """9600 で開こうとしても線は 115200 で開く。"""
    t = PicoUartTransport()
    seen = _patched_open(t)
    assert t.open(1, "COM9", 9600) is True
    assert seen["baud"] == 115200
    assert seen["path"] == "COM9"
    assert t.send_interval == TextSerialTransport.calc_send_interval(115200)


def test_pico_default_is_115200() -> None:
    """引数省略時も 115200（9600 既定の継承はしない）。"""
    t = PicoUartTransport()
    seen = _patched_open(t)
    assert t.open(1, "COM9") is True
    assert seen["baud"] == 115200


def test_pico_bad_baud_still_false() -> None:
    """不正な baud でも例外なく False（posix/Darwin と同じ作法）。"""
    t = PicoUartTransport()
    assert t.open(0, "", "not-a-number") is False  # type: ignore[arg-type]


def test_legacy_still_allows_9600() -> None:
    """従来線は 9600 のまま（Pico の寄せが漏れない）。"""
    t = TextSerialTransport()
    seen: dict[str, Any] = {}

    def fake_open(path: str, baudrate: int) -> bool:
        seen["baud"] = baudrate
        return True

    t._open_serial = fake_open  # type: ignore[method-assign]
    t.start_rx_pump = lambda: True  # type: ignore[method-assign]
    assert t.open(1, "COM3", 9600) is True
    assert seen["baud"] == 9600
