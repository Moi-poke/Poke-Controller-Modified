"""Q経路の残存を禁止する。pico-wakeConにQ/Rは無いため、PC側にQ公開面を残さない。"""

from core import CommandOperate
from core.serial import encoding, sender


def test_queue_surface_removed() -> None:
    assert not hasattr(encoding, "encode_queued_state")
    for name in (
        "shouldQueue",
        "runQueued",
        "queueThreshold",
        "encodeQueuedState",
        "_runQueueExchange",
        "_waitQueueDone",
        "_sendQueueLine",
        "_queue_unsupported",
        "_noteQueueSupported",
        "_forgetQueueSupport",
        "_queueKnownUnsupported",
        "QUEUE_THRESHOLD_S",
        "QUEUE_WAIT_MARGIN_S",
    ):
        assert not hasattr(sender.Sender, name), name
    assert not hasattr(CommandOperate.OperateMixin, "_pressQueued")
