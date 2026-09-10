"""Commands.Transport 再公開の検証（後方互換の別名）。

core/Transport.py にある名前は Commands 側からも読めること。
"""

from __future__ import annotations


def test_live_worker_capabilities_reexported() -> None:
    """LIVE_WORKER_CAPABILITIES が両口で同じ実体を指す。"""
    from Commands import Transport as cmd_t
    from core import Transport as core_t

    assert hasattr(cmd_t, "LIVE_WORKER_CAPABILITIES")
    assert cmd_t.LIVE_WORKER_CAPABILITIES is core_t.LIVE_WORKER_CAPABILITIES
