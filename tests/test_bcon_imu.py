"""IMU_SAMPLE送受信のhostベクタ（実機不要）。"""

import struct
import threading
import time


def test_imu_frame_build_len12():
    from core.transport.bcon_protocol import T_IMU_SAMPLE, frame_build

    assert T_IMU_SAMPLE == 0x04
    payload = struct.pack("<6h", 0, 0, 4096, 0, 0, 0)
    frame = frame_build(T_IMU_SAMPLE, payload, 0x07)
    assert frame[0] == 0xAB
    assert frame[1] == 0x04
    assert frame[2] == 12
    assert len(frame) == 17
    assert frame[3:15] == payload


def test_imu_expected_len():
    from core.transport.bcon_protocol import T_IMU_SAMPLE, proto_expected_len

    assert proto_expected_len(T_IMU_SAMPLE) == 12


def test_imu_parser_accepts_len12():
    from core.transport.bcon_protocol import T_IMU_SAMPLE, BconParser, frame_build

    payload = struct.pack("<6h", 1, -2, 4096, 100, -100, 0)
    frame = frame_build(T_IMU_SAMPLE, payload, 0x09)
    parser = BconParser()
    out = parser.feed(frame)
    assert out == [(T_IMU_SAMPLE, payload, 0x09)]


def test_imu_parser_rejects_bad_len():
    from core.transport.bcon_protocol import T_IMU_SAMPLE, BconParser, frame_build

    frame = frame_build(T_IMU_SAMPLE, b"\x00" * 8, 0x0A)
    parser = BconParser()
    out = parser.feed(frame)
    assert out == []
    assert parser.err_crc >= 1


class _FakeSerial:
    def __init__(self):
        self.written = []
        self.is_open = True

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)


def _open_transport():
    import core.transport.bcon as bcon_mod

    made = bcon_mod.BconTransport()
    made.ser = _FakeSerial()
    return made


def test_send_imu_writes_one_frame():
    import struct

    from core.transport.bcon_protocol import T_IMU_SAMPLE

    made = _open_transport()
    try:
        ok = made.send_imu(0, 0, 4096, 100, -100, 0)
        assert ok is True
        assert len(made.ser.written) == 1
        frame = made.ser.written[0]
        assert frame[0] == 0xAB
        assert frame[1] == T_IMU_SAMPLE
        assert frame[2] == 12
        assert frame[3:15] == struct.pack("<6h", 0, 0, 4096, 100, -100, 0)
        assert made.imu_stats() == {"sent": 1, "dropped": 0}
    finally:
        made.close()


def test_send_imu_saturates_i16():
    import struct

    made = _open_transport()
    try:
        ok = made.send_imu(99999, -99999, 4096, 0, 0, 0)
        assert ok is True
        frame = made.ser.written[0]
        assert frame[3:15] == struct.pack("<6h", 32767, -32768, 4096, 0, 0, 0)
    finally:
        made.close()


def test_send_imu_closed_returns_false():
    import core.transport.bcon as bcon_mod

    made = bcon_mod.BconTransport()
    assert made.send_imu(0, 0, 4096, 0, 0, 0) is False


def test_imu_loop_sends_periodic():
    made = _open_transport()
    try:
        from core.imu_wave import StaticRest

        assert made.start_imu_loop(StaticRest(), interval_s=0.005) is True
        assert made.imu_loop_running() is True
        deadline = time.time() + 2.0
        while made.imu_stats()["sent"] < 5 and time.time() < deadline:
            time.sleep(0.01)
        assert made.stop_imu_loop() is True
        assert made.imu_loop_running() is False
        stats = made.imu_stats()
        assert stats["sent"] >= 5
        for frame in made.ser.written:
            assert frame[1] == 0x04
    finally:
        made.close()


def test_send_imu_does_not_fire_write_hooks():
    """IMUは入力行ではないのでSenderのフックを呼ばない（send_rowは従来どおり）。"""
    made = _open_transport()
    calls = []
    made.set_hooks(
        lambda row, show=True: calls.append(("begin", row)),
        lambda row, show=True: calls.append(("end", row)),
    )
    try:
        assert made.send_imu(0, 0, 4096, 100, -100, 0) is True
        assert calls == []
        made.send_row("10 08")
        assert [kind for kind, _row in calls] == ["begin", "end"]
    finally:
        made.close()


def test_imu_loop_restart_after_stuck_stop_uses_new_source():
    """停止joinが0.5秒で諦めた直後の開始でも、新しいsourceで送出する。"""
    import struct

    from core.transport.bcon_protocol import T_IMU_SAMPLE

    made = _open_transport()
    release = threading.Event()
    entered = threading.Event()

    def _slow() -> tuple[int, int, int, int, int, int]:
        entered.set()
        release.wait(2.0)
        return (1, 0, 4096, 0, 0, 0)

    try:
        assert made.start_imu_loop(_slow, interval_s=0.01) is True
        assert entered.wait(1.0), "旧loopがsourceを呼ばない"
        # 旧スレッドはsource内で止まっているためjoinがtimeoutする。
        assert made.stop_imu_loop() is False
        sent_before = made.imu_stats()["sent"]
        assert (
            made.start_imu_loop(lambda: (2, 0, 4096, 0, 0, 0), interval_s=0.01) is True
        )
        assert made.imu_loop_running() is True
        release.set()
        deadline = time.time() + 2.0
        while made.imu_stats()["sent"] <= sent_before and time.time() < deadline:
            time.sleep(0.01)
        assert made.imu_stats()["sent"] > sent_before
        axes = [
            struct.unpack("<6h", f[3:15])[0]
            for f in made.ser.written
            if len(f) >= 15 and f[1] == T_IMU_SAMPLE
        ]
        assert axes, "再開後にIMUが線へ出ない"
        assert 2 in axes, "新しいsourceの値が送られていない"
        assert 1 not in axes, "停止した旧sourceが送り続けている"
        assert made.stop_imu_loop() is True
        assert made.imu_loop_running() is False
    finally:
        release.set()
        made.close()


def test_imu_wave_sine_static():
    from core.imu_wave import SineWave, StaticRest, StepWave

    rest = StaticRest()
    assert rest() == (0, 0, 4096, 0, 0, 0)
    step = StepWave(axis=1, raw=1000)
    assert step() == (0, 0, 4096, 0, 0, 0)
    step.trigger()
    assert step() == (0, 0, 4096, 0, 1000, 0)
    sine = SineWave(axis=0, amp=100, period_s=1.0)
    got = sine()
    assert got[0] == 0 and got[1] == 0 and got[2] == 4096
    assert got[4] == 0 and got[5] == 0
    assert -100 <= got[3] <= 100
