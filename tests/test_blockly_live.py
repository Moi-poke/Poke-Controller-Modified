"""ライブ編集の仲介（LiveBridge）の検証。"""

from __future__ import annotations

from services import blockly_live


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    def wall(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _bridge() -> tuple[blockly_live.LiveBridge, FakeClock]:
    clock = FakeClock()
    return blockly_live.LiveBridge(clock=clock.monotonic, wall=clock.wall), clock


def _workspace(value: int = 1) -> dict:
    return {"blocks": {"languageVersion": 0, "blocks": []}, "value": value}


def test_lease_allows_one_client_at_a_time() -> None:
    bridge, clock = _bridge()
    assert bridge.update("a", "r1", _workspace(), False) is True
    assert bridge.update("b", "r2", _workspace(2), False) is False
    clock.advance(blockly_live.LEASE_SECONDS + 0.1)
    assert bridge.update("b", "r3", _workspace(3), False) is True
    assert bridge.snapshot()["revision"] == "r3"


def test_enqueue_and_complete_updates_snapshot() -> None:
    bridge, _ = _bridge()
    assert bridge.update("a", "r1", _workspace(1), False) is True
    request_id, error = bridge.enqueue(
        [{"blockId": "b1", "field": "SEC", "value": 2}], "r1"
    )
    assert error == "" and request_id is not None
    handed = bridge.next_request("a")
    assert handed["leased"] is True
    assert handed["request"]["id"] == request_id
    assert handed["request"]["ops"][0]["value"] == 2
    assert bridge.next_request("a")["request"] is None
    assert bridge.complete(
        "a",
        request_id,
        ok=True,
        revision="r2",
        workspace=_workspace(2),
        status="applied",
    )
    result = bridge.wait(request_id, timeout=0.1)
    assert result["ok"] is True
    assert result["revision"] == "r2"
    assert bridge.snapshot()["revision"] == "r2"
    assert bridge.snapshot()["workspace"]["value"] == 2


def test_stale_base_revision_is_rejected() -> None:
    bridge, _ = _bridge()
    bridge.update("a", "r2", _workspace(2), False)
    request_id, error = bridge.enqueue([], "r1")
    assert request_id is None
    assert "変わっています" in error


def test_expired_request_is_not_delivered() -> None:
    bridge, clock = _bridge()
    bridge.update("a", "r1", _workspace(), False)
    request_id, error = bridge.enqueue([], "r1")
    assert error == "" and request_id is not None
    handed = bridge.next_request("a")
    # ブラウザの Date.now()（ミリ秒）と比べられる単位で渡す。
    assert (
        handed["request"]["deadline"]
        == 100.0 * 1000.0 + blockly_live.REQUEST_SECONDS * 1000.0
    )
    clock.advance(blockly_live.REQUEST_SECONDS + 1)
    handed = bridge.next_request("a")
    assert handed["request"] is None
    result = bridge.wait(request_id or 0, timeout=0.1)
    assert result["status"] == "expired"


def test_timeout_releases_active_request() -> None:
    # 実時計を使う（待ち時間の経過で timeout する）。
    bridge = blockly_live.LiveBridge()
    bridge.update("a", "r1", _workspace(), False)
    first, error = bridge.enqueue([], "r1")
    assert error == "" and first is not None
    assert bridge.next_request("a")["request"]["id"] == first
    result = bridge.wait(first, timeout=0.05)
    assert result["status"] == "timeout"
    second, error = bridge.enqueue([], "r1")
    assert error == "" and second is not None
    assert bridge.next_request("a")["request"]["id"] == second


def test_other_client_does_not_receive_requests() -> None:
    bridge, _ = _bridge()
    bridge.update("a", "r1", _workspace(), False)
    bridge.enqueue([], "r1")
    handed = bridge.next_request("b")
    assert handed["leased"] is False
    assert handed["request"] is None
