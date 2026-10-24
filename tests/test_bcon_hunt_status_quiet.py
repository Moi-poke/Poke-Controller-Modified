"""接続直後の baud hunt 窓で STATUS 欠落・異常を実エラー表示しない。

Switch-bcon は hunt 確定まで UART1 TX を止め、確定前のフレームを捨てる。
そのため HELLO_ACK 成立前の STATUS 欠落と復号ノイズ（errcode）は
通信異常ではない。成立後は従来どおり可視化する（実機不要）。
"""

import threading
import time
from typing import Any

from core.transport.bcon_protocol import (
    PROTO_VER,
    RESULT_OK,
    T_HELLO_ACK,
    T_STATUS,
    frame_build,
)


def _make_scripted_ser() -> Any:
    """読み書きの記録を持つ疑似シリアル（単一読み口のポンプ用）。"""

    class ScriptedSer:
        is_open = True

        def __init__(self) -> None:
            self._chunks: list[bytes] = []
            self._lock = threading.Lock()
            self.written: list[bytes] = []
            self.baudrate = 1000000

        def read(self, n: int) -> bytes:
            _ = n
            with self._lock:
                if self._chunks:
                    return self._chunks.pop(0)
            time.sleep(0.001)
            return b""

        @property
        def in_waiting(self) -> int:
            with self._lock:
                return sum(len(c) for c in self._chunks)

        def feed(self, data: bytes) -> None:
            with self._lock:
                self._chunks.append(bytes(data))

        def write(self, data: bytes) -> int:
            with self._lock:
                self.written.append(bytes(data))
            return len(data)

    return ScriptedSer()


def _open_transport() -> tuple[Any, Any]:
    from core.transport import create_transport

    made = create_transport("bcon")
    ser = _make_scripted_ser()
    made.ser = ser
    made.start_rx_pump()
    return made, ser


def _feed_hello_ack(ser: Any) -> None:
    payload = bytes([PROTO_VER, 0x00, 0x04, RESULT_OK])
    ser.feed(frame_build(T_HELLO_ACK, payload, 0x00))


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_status_timeout_is_quiet_before_hello(capsys) -> None:
    made, _ser = _open_transport()
    try:
        assert made.hello_established() is False
        # Given: HELLO 未確立（hunt 確定前）で STATUS_REQ が返らない
        # When: 状態確認を待つ
        assert made.request_status(timeout=0.1) is None
        # Then: 「来ませんでした」を実エラーとして出さない
        assert "STATUSが来ませんでした" not in capsys.readouterr().out
    finally:
        made.stop_rx_pump()


def test_status_errcode_is_quiet_before_hello(capsys) -> None:
    made, ser = _open_transport()
    try:
        assert made.hello_established() is False
        # Given: hunt 中の復号ノイズが errcode=0x01 として載った STATUS
        # When: 受信する
        ser.feed(
            frame_build(
                T_STATUS, bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01]), 0x10
            )
        )
        assert _wait_until(lambda: made.last_status()["errcode"] == 0x01)
        # Then: 写しは残すが、異常としては通知しない
        assert "STATUS異常" not in capsys.readouterr().out
    finally:
        made.stop_rx_pump()


def test_status_timeout_is_visible_after_hello(capsys) -> None:
    made, ser = _open_transport()
    try:
        # Given: HELLO_ACK 成立済み（adopted=4・RESULT=OK）
        _feed_hello_ack(ser)
        assert _wait_until(made.hello_established)
        capsys.readouterr()
        # When: 成立後に STATUS_REQ が返らない
        assert made.request_status(timeout=0.1) is None
        # Then: 従来どおり通知する
        assert "STATUSが来ませんでした" in capsys.readouterr().out
    finally:
        made.stop_rx_pump()


def test_status_errcode_is_visible_after_hello(capsys) -> None:
    made, ser = _open_transport()
    try:
        # Given: HELLO_ACK 成立済み
        _feed_hello_ack(ser)
        assert _wait_until(made.hello_established)
        capsys.readouterr()
        # When: 成立後に errcode=0x02 の STATUS を受ける
        ser.feed(
            frame_build(
                T_STATUS, bytes([0x00, 0x09, 0x03, 0x00, 0x00, 0x00, 0x02]), 0x21
            )
        )
        assert _wait_until(lambda: made.last_status()["errcode"] == 0x02)
        # Then: 従来どおり異常として通知する
        out = capsys.readouterr().out
        assert "STATUS異常" in out
        assert "errcode=0x02" in out
    finally:
        made.stop_rx_pump()
