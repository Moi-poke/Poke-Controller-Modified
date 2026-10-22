"""bcon-proc 親側 IMU loop の回帰テスト（実機不要）。"""

from __future__ import annotations

import threading

from core.transport.bcon_proc import BconProcTransport


def test_parent_imu_loop_forwards_samples_counts_and_stops() -> None:
    """Given: 偽のRPC / When: IMU loopを回して停止する / Then: 6値転送と統計・停止が揃う。"""
    transport = BconProcTransport(logger=None)
    transport._worker_alive = lambda: True  # type: ignore[method-assign]

    forwarded: list[tuple[str, tuple[int, ...]]] = []
    first_three = threading.Event()

    def fake_rpc(method: str, args: tuple[int, ...], timeout: float) -> bool:
        forwarded.append((method, args))
        if len(forwarded) >= 3:
            first_three.set()
        return True

    transport._rpc_bool = fake_rpc  # type: ignore[method-assign]

    samples: list[tuple[int, int, int, int, int, int]] = [
        (1, 2, 3, 4, 5, 6),
        (10, 20, 30, 40, 50, 60),
        (100, 200, 300, 400, 500, 600),
    ]
    feed = iter(samples)

    def source() -> tuple[int, int, int, int, int, int] | None:
        return next(feed, None)

    assert transport.start_imu_loop(source, interval_s=0.001) is True
    assert first_three.wait(2.0), "親側IMU loopがサンプルを転送しない"
    assert transport.imu_loop_running() is True

    assert transport.stop_imu_loop() is True
    assert transport.imu_loop_running() is False

    assert forwarded == [
        ("send_imu", (1, 2, 3, 4, 5, 6)),
        ("send_imu", (10, 20, 30, 40, 50, 60)),
        ("send_imu", (100, 200, 300, 400, 500, 600)),
    ]
    stats = transport.imu_stats()
    assert stats["sent"] == len(forwarded)
    assert stats["dropped"] == 0
