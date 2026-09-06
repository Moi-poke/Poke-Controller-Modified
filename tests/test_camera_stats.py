"""Camera.getStats の検証。機器を開かずに回せる範囲だけ。"""

from core.Camera import Camera


def test_stats_initially_zero() -> None:
    camera = Camera(fps=60)
    stats = camera.getStats()
    assert stats["fps"] == 0.0
    assert stats["avg_ms"] == 0.0


def test_stats_resets_on_read() -> None:
    camera = Camera(fps=60)
    camera._stat_puts = 120
    import time

    camera._stat_began_at = time.perf_counter() - 2.0
    camera._stat_avg_ms = 1.5
    stats = camera.getStats()
    assert stats["fps"] == 60.0
    assert stats["avg_ms"] == 1.5
    # 読んだら区切り直す
    again = camera.getStats()
    assert again["fps"] == 0.0
