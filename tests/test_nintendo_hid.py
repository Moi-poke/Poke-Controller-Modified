"""Nintendo HID直読みのhostベクタ（実機不要・偽デバイス）。"""

import struct


class _FakeHidDevice:
    def __init__(self, reports):
        self._reports = list(reports)
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        if not self._reports:
            return []
        return list(self._reports.pop(0))

    def close(self):
        self.closed = True


def _report_30(ax=0, ay=0, az=4096, gx=0, gy=0, gz=0):
    body = struct.pack("<6h", ax, ay, az, gx, gy, gz)
    return bytes([0x30] + [0] * 36)[:13] + body + body + body + bytes(64 - 49)


def test_nintendo_hid_parses_latest_sample():
    import core.nintendo_hid as nh

    s1 = struct.pack("<6h", 1, 2, 3, 4, 5, 6)
    s2 = struct.pack("<6h", 7, 8, 9, 10, 11, 12)
    s3 = struct.pack("<6h", 13, 14, 15, 16, 17, 18)
    report = bytes([0x30] + [0] * 12) + s1 + s2 + s3 + bytes(64 - 49)
    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    import threading

    reader._lock = threading.Lock()
    reader._dev = _FakeHidDevice([report])
    reader._packet = 0
    assert reader.read() == (13, 14, 15, 16, 17, 18)


def test_nintendo_hid_skips_non_30_reports():
    import core.nintendo_hid as nh

    other = bytes([0x21] + [0] * 63)
    good = _report_30(1, 2, 4096, 3, 4, 5)
    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    import threading

    reader._lock = threading.Lock()
    reader._dev = _FakeHidDevice([other, good])
    reader._packet = 0
    assert reader.read() == (1, 2, 4096, 3, 4, 5)


def test_nintendo_hid_closed_returns_none():
    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader()
    assert reader.read() is None
    assert reader.is_open() is False
    reader.close()
