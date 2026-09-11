"""VisionMixin の複合ヘルパー（press_until / press_until_gone / wait_count）。実機なし。"""

from __future__ import annotations

from typing import Any

from core.CommandVision import VisionMixin


class Probe(VisionMixin):
    """_matchOnce・countTemplate・press・wait・締切だけ差し替える。"""

    def __init__(self, hits: list[bool], count: int = 0) -> None:
        self._hits = list(hits)
        self._count = count
        self.presses: list[tuple[Any, float, float]] = []
        self.waits = 0
        self.max_seen: list[int] = []

    def _matchOnce(self, *args: Any, **kwargs: Any) -> tuple[bool, float, Any]:
        hit = self._hits.pop(0) if self._hits else False
        return (hit, 1.0 if hit else 0.0, (0, 0))

    def countTemplate(
        self,
        template_path: Any,
        threshold: float = 0.7,
        use_gray: bool = True,
        crop: Any = None,
        max_count: int = 20,
    ) -> int:
        self.max_seen.append(int(max_count))
        return self._count

    def press(self, buttons: Any, duration: float = 0.1, wait: float = 0.1) -> None:
        self.presses.append((buttons, duration, wait))

    def wait(self, seconds: float) -> None:
        _ = seconds
        self.waits += 1

    def _deadline(self, timeout: float):  # type: ignore[no-untyped-def]
        calls = 0

        def expired() -> bool:
            nonlocal calls
            calls += 1
            return calls > 50

        return expired


def test_press_until_hits() -> None:
    probe = Probe([False, True])
    assert probe.press_until("a.png", "X") is True
    assert len(probe.presses) == 1
    assert probe.presses[0][0] == "X"


def test_press_until_timeout() -> None:
    probe = Probe([])
    assert probe.press_until("a.png", "X", timeout=1.0) is False
    assert len(probe.presses) > 0


def test_press_until_gone_immediate() -> None:
    probe = Probe([False])
    assert probe.press_until_gone("a.png", "X") is True
    assert probe.presses == []


def test_press_until_gone_presses_while_present() -> None:
    probe = Probe([True, True, False])
    assert probe.press_until_gone("a.png", "X") is True
    assert len(probe.presses) == 2


def test_wait_count_hits() -> None:
    probe = Probe([], count=3)
    assert probe.wait_count("a.png", 3, timeout=1.0) is True


def test_wait_count_timeout_and_max_count() -> None:
    probe = Probe([], count=1)
    assert probe.wait_count("a.png", 30, timeout=1.0) is False
    assert probe.max_seen and all(m >= 30 for m in probe.max_seen)
