"""軌跡の記録と再生のhostベクタ（実機不要）。"""

import os
import tempfile


def test_record_and_play_roundtrip():
    from core.motion_track import MotionPlayer, MotionRecorder

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rec.pgyro")
        samples = [(i, -i, 4096, 100 + i, -100, 0) for i in range(10)]
        with MotionRecorder(path) as rec:
            for sample in samples:
                assert rec.write(sample) is True
            assert rec.count == 10
        player = MotionPlayer(path)
        assert player.load() is True
        assert player.error == ""
        assert len(player) == 10
        assert list(player) == samples


def test_record_clamps_i16_and_rejects_bad():
    from core.motion_track import MotionPlayer, MotionRecorder

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rec.pgyro")
        with MotionRecorder(path) as rec:
            assert rec.write((99999, -99999, 4096, 0, 0, 0)) is True
            assert rec.write(("x", 0, 0, 0, 0, 0)) is False  # type: ignore[arg-type]
            assert rec.count == 1
        player = MotionPlayer(path)
        assert player.load() is True
        assert list(player) == [(32767, -32768, 4096, 0, 0, 0)]


def test_player_rejects_broken_files():
    from core.motion_track import MotionPlayer

    with tempfile.TemporaryDirectory() as tmp:
        missing = MotionPlayer(os.path.join(tmp, "none.pgyro"))
        assert missing.load() is False
        assert missing.error != ""
        bad_magic = os.path.join(tmp, "bad.pgyro")
        with open(bad_magic, "wb") as fh:
            fh.write(b"XXXX" + b"\x00" * 28)
        player = MotionPlayer(bad_magic)
        assert player.load() is False
        assert "magic" in player.error
        short = os.path.join(tmp, "short.pgyro")
        with open(short, "wb") as fh:
            fh.write(b"PGYR" + b"\x00" * 10)
        player2 = MotionPlayer(short)
        assert player2.load() is False


def test_player_generator_ends_with_none():
    from core.motion_track import MotionPlayer, MotionRecorder

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rec.pgyro")
        with MotionRecorder(path) as rec:
            rec.write((0, 0, 4096, 10, 20, 30))
        player = MotionPlayer(path)
        assert player.load() is True
        gen = player.as_generator()
        assert next(gen) == (0, 0, 4096, 10, 20, 30)
        assert next(gen) is None
        assert next(gen) is None


def test_player_as_source_is_callable():
    from core.motion_track import MotionPlayer, MotionRecorder

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rec.pgyro")
        with MotionRecorder(path) as rec:
            rec.write((1, 2, 3, 4, 5, 6))
        player = MotionPlayer(path)
        assert player.load() is True
        source = player.as_source()
        assert callable(source)
        assert source() == (1, 2, 3, 4, 5, 6)
        assert source() is None


def test_recorder_header_layout():
    import struct

    from core.motion_track import MotionRecorder

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "rec.pgyro")
        with MotionRecorder(path, start_offset_us=12345) as rec:
            rec.write((1, 2, 3, 4, 5, 6))
        with open(path, "rb") as fh:
            header = fh.read(32)
            body = fh.read()
        magic, ver, hsize, period, count, flags, off, res = struct.unpack(
            "<4sHHIIIQI", header
        )
        assert magic == b"PGYR"
        assert (ver, hsize, period, count, flags, off, res) == (
            1,
            32,
            5000,
            1,
            0,
            12345,
            0,
        )
        assert body == struct.pack("<6h", 1, 2, 3, 4, 5, 6)
