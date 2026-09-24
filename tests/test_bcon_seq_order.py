"""bcon SEQ順と線上の順の一致（実機不要・決定的逆転試験）。"""

from __future__ import annotations

import threading

from core.transport import create_transport
from core.transport.bcon_protocol import T_PING


class _BlockingFirstSer:
    """最初のwriteだけ門で止める疑似シリアル。"""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._guard = threading.Lock()
        self._calls = 0
        # 最初のwriteが到達した（＝SEQを先取りして書込中で止まった）。
        self.entered = threading.Event()
        # 2回目のwriteが到達した（＝後取りSEQが先に線へ出た）。
        self.second_entered = threading.Event()
        # 開くまで最初のwriteは戻らない（失敗安全の上限5秒付き）。
        self.gate = threading.Event()

    def write(self, data: bytes) -> int:
        with self._guard:
            self._calls += 1
            call_no = self._calls
        if call_no == 1:
            self.entered.set()
            self.gate.wait(5.0)
        else:
            self.second_entered.set()
        with self._guard:
            self.written.append(bytes(data))
        return len(data)


def test_wire順序は採番順と一致する():
    """Given: 採番10始まり・初回write停止中のbcon。

    When: 別スレッドがもう1発の会話フレームを送る。
    Then: 線上のSEQ順は採番順[10, 11]のまま（逆転[11, 10]は欠番扱いでFWが0x03）。
    """
    made = create_transport("bcon")
    ser = _BlockingFirstSer()
    made.ser = ser
    with made._lock:
        made._tx_seq = 10

    results: dict[str, bool] = {}

    def _send(key: str) -> None:
        results[key] = bool(made._send_session_frame(T_PING, b"", key))

    first = threading.Thread(target=_send, args=("t1",), daemon=True)
    first.start()
    # t1がSEQ=10を採りwriteの門で止まるまで待つ（決定的・sleepなし）。
    assert ser.entered.wait(5.0)
    second = threading.Thread(target=_send, args=("t2",), daemon=True)
    second.start()
    # 修正前はt2が即書くため門が開く（逆転の証拠）。修正後は開かず timeout する。
    ser.second_entered.wait(0.5)
    ser.gate.set()
    first.join(5.0)
    second.join(5.0)
    assert not first.is_alive()
    assert not second.is_alive()
    assert results.get("t1") is True
    assert results.get("t2") is True
    # SEQ位置は 3+LEN（[SYNC][TYPE][LEN][荷物][SEQ][CRC8]）。
    seqs = [frame[3 + frame[2]] for frame in ser.written]
    assert seqs == [10, 11]
