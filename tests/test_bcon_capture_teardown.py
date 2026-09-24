"""取込完了後の後片付けのhostベクタ（実機不要）。

取込期限が来たら取込用の購読だけ外し、残りカスを捨てる。
閉じる・持替でも同じ後始末を通し、二重でも落とさない。
次に繋いだ先へは張り直せる。他の購読者は壊さない。
別過程の転送（bcon_proc）は live/状態確認の口のため残す。
"""

import queue as _q
from pathlib import Path
from typing import Any


class _StubWindow:
    """Toplevelの偽物。after予約は捨てる（mainloopなし）。"""

    def __init__(self) -> None:
        self.exists = True

    def winfo_exists(self) -> bool:
        return self.exists

    def destroy(self) -> None:
        self.exists = False

    def after(self, ms: int, func: Any) -> Any:
        _ = (ms, func)
        return None

    def after_cancel(self, _id: Any) -> None:
        return None


class _StubLogText:
    """ログ欄の偽物。挿入だけ覚える。"""

    def __init__(self) -> None:
        self.text = ""

    def configure(self, **kwargs: Any) -> None:
        _ = kwargs

    def insert(self, index: Any, text: str) -> None:
        _ = index
        self.text += text

    def see(self, index: Any) -> None:
        _ = index

    def yview(self) -> tuple[float, float]:
        return (0.0, 1.0)

    def index(self, spec: str) -> str:
        _ = spec
        return "1.0"

    def delete(self, start: str, end: str) -> None:
        _, _ = start, end


class _FakeBconTransport:
    """購読の出入りだけ見る口。線には触らない。"""

    name = "bcon"
    capability = "BCON_STATE"

    def __init__(self) -> None:
        self.rx_subs: list[Any] = []
        self.rx_unsub_calls = 0

    def subscribe_rx(self, func: Any) -> Any:
        if func not in self.rx_subs:
            self.rx_subs.append(func)

        def _unsub() -> None:
            self.rx_unsub_calls += 1
            if func in self.rx_subs:
                self.rx_subs.remove(func)

        return _unsub

    def emit_frame(self, type_: int, payload: bytes) -> None:
        for func in list(self.rx_subs):
            func((type_, bytes(payload), 0))


def _make_teardown_setup(transport: Any) -> Any:
    """窓を作らない検査体。queueと窓stubだけ持つ。"""
    from BconSetup import BconSetup

    obj: Any = BconSetup.__new__(BconSetup)
    obj._sender = object()
    obj._queue = _q.Queue()
    obj._busy = False
    obj._stop = False
    obj._closed = False
    obj._rx_unsub = None
    obj._rx_transport = None
    obj._capture_saved_seen = False
    obj._capture_deadline_id = None
    obj._log = _StubLogText()
    obj.window = _StubWindow()
    obj._ensure_counters()
    return obj


def _queue_kinds(obj: Any) -> list[str]:
    """溜めの中身の種別だけ抜く（順序つき）。"""
    items: list[Any] = []
    try:
        while True:
            items.append(obj._queue.get_nowait())
    except Exception:
        pass
    for item in items:
        obj._queue.put(item)
    kinds: list[str] = []
    for item in items:
        try:
            kind, _ = item
            kinds.append(str(kind))
        except (TypeError, ValueError):
            kinds.append("?")
    return kinds


def test_capture_complete_unsubs_and_clears_stale() -> None:
    """期限が来たら自分の購読を外し、残りカスを捨てる（完了後qsize→静寂）。"""
    # Given: 繋いで実況を受け、溜めに残りカスが残る。
    transport = _FakeBconTransport()
    obj = _make_teardown_setup(transport)
    obj._bind_player_info(transport)
    assert len(transport.rx_subs) == 1
    transport.emit_frame(0x23, b"\x05\x03")
    transport.emit_frame(0x22, b"\x10\x20")
    assert obj._queue.qsize() == 2

    # When: 取込期限が来る（保存あり・なし両方で同じ後始末）。
    obj._capture_saved_seen = True
    obj._on_capture_deadline()

    # Then: 自分の購読は外れ、残りカスは消え、完了文だけ残る。
    assert transport.rx_subs == []
    kinds = _queue_kinds(obj)
    assert "player_info" not in kinds
    assert "rumble" not in kinds
    assert "log" in kinds
    assert obj._queue.qsize() <= 2
    # 完了後は新しいフレームが届かない（T1数えの母数が止まる）。
    before = int(obj.counters_snapshot()["rx_total"])
    transport.emit_frame(0x23, b"\x0f\x00")
    assert int(obj.counters_snapshot()["rx_total"]) == before


def test_teardown_idempotent_double_safe() -> None:
    """完了後始末は二重でも落とさず、運搬器側の解除は1回だけ。"""
    # Given: 繋いだ窓。
    transport = _FakeBconTransport()
    obj = _make_teardown_setup(transport)
    obj._bind_player_info(transport)
    assert len(transport.rx_subs) == 1

    # When: 完了後始末を二重に呼ぶ（期限二重・閉じ二重の代役）。
    obj._teardown_capture()
    obj._teardown_capture()
    obj.close()
    obj.close()

    # Then: 落とさず、解除は1回だけ、外れたまま。
    assert transport.rx_subs == []
    assert transport.rx_unsub_calls == 1
    assert obj._queue.empty()


def test_rebind_after_capture_teardown() -> None:
    """完了後に次の運搬器へは張り直せる（持替で古い購読は残さない）。"""
    # Given: 先に繋いで期限で外した窓。
    first = _FakeBconTransport()
    second = _FakeBconTransport()
    obj = _make_teardown_setup(first)
    obj._sender = type("_S", (), {"transport": second})()
    obj._bind_player_info(first)
    obj._on_capture_deadline()

    # When: 次の運搬器へ繋ぎ直す。
    obj._bind_player_info(second)

    # Then: 古い方には残らず、新しい方にだけ付く。
    assert first.rx_subs == []
    assert len(second.rx_subs) == 1
    # 新しい実況が届く（再bindの証拠）。
    second.emit_frame(0x23, b"\x05\x03")
    assert obj._queue.qsize() >= 1


def test_other_subscribers_unaffected() -> None:
    """自分の解除で他の購読者は壊さない（live/状態確認の口は残す）。"""
    # Given: 同じ運搬器に自分＋他の購読者。
    transport = _FakeBconTransport()
    obj = _make_teardown_setup(transport)
    obj._bind_player_info(transport)
    other_seen: list[Any] = []
    other_unsub = transport.subscribe_rx(other_seen.append)
    assert len(transport.rx_subs) == 2

    # When: 自分の取込だけ片付ける。
    obj._on_capture_deadline()

    # Then: 他の購読者は残り、実況も届く。
    assert len(transport.rx_subs) == 1
    transport.emit_frame(0x23, b"\x05\x03")
    assert len(other_seen) == 1
    _ = other_unsub


def test_forwarding_stays_for_live_status_paths() -> None:
    """別過程の全転送は残す（live/状態確認が全ftypeを要するため）。

    T1数え（ftype別rx率・qsize）はBconSetup側で賄え、配達の選別は
    要らない。転送を削ると他の購読者が壊れるため残す旨を史料に書く。
    """
    src = Path("SerialController/core/transport/bcon_proc.py").read_text(
        encoding="utf-8"
    )
    # 全転送の実体が残ること（STATUS等も運ぶ）。
    assert "_forward_frame" in src
    assert 'evt_q.put(("rx"' in src or 'evt_q.put(("rx"' in src
    # 残す理由の史料（他の購読者・live/状態確認への言及）。
    assert "他の購読者" in src or "他の購読" in src
    assert "live" in src.lower() or "状態確認" in src
