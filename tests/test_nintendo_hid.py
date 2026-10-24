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


def _report_30(ax=0, ay=0, az=4096, gx=0, gy=0, gz=0, timer=0):
    body = struct.pack("<6h", ax, ay, az, gx, gy, gz)
    head = bytearray([0x30] + [0] * 36)
    head[1] = int(timer) & 0xFF
    return bytes(head)[:13] + body + body + body + bytes(64 - 49)


def test_nintendo_hid_parses_latest_sample():
    import core.nintendo_hid as nh

    s1 = struct.pack("<6h", 1, 2, 3, 4, 5, 6)
    s2 = struct.pack("<6h", 7, 8, 9, 10, 11, 12)
    s3 = struct.pack("<6h", 13, 14, 15, 16, 17, 18)
    report = bytes([0x30] + [0] * 12) + s1 + s2 + s3 + bytes(64 - 49)
    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    import threading

    reader._lock = threading.Lock()
    reader._latest = report
    reader._packet = 0
    assert reader.read() == (13, 14, 15, 16, 17, 18)


def test_read_next_always_returns_latest_slot():
    """0x30の3スロットを消費せず、毎回最新（37..48）を返す。

    200Hzで読むと3件/11〜15msの生産に追いつかず、古いスロットを順に
    返すと時間が巻き戻る。パッドAPIと同じ最新値方式を固定する。
    """
    import threading
    import time

    import core.nintendo_hid as nh

    s1 = struct.pack("<6h", 1, 2, 3, 4, 5, 6)
    s2 = struct.pack("<6h", 7, 8, 9, 10, 11, 12)
    s3 = struct.pack("<6h", 13, 14, 15, 16, 17, 18)
    report = bytes([0x30] + [0] * 12) + s1 + s2 + s3 + bytes(64 - 49)
    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    reader._lock = threading.Lock()
    reader._latest = report
    reader._latest_t = time.monotonic()
    reader._packet = 0
    for _ in range(5):
        assert reader.read_next() == (13, 14, 15, 16, 17, 18)
    assert not hasattr(reader, "_samples")


def test_read_next_follows_latest_poller_report():
    """複数の0x30を受けても、read_nextは最後の最新値だけを返し続ける。"""
    import time

    import core.nintendo_hid as nh

    first = _report_30(1, 1, 1, 1, 1, 1, timer=1)
    second = _report_30(2, 2, 2, 2, 2, 2, timer=2)
    reader = nh.NintendoHidReader()
    reader._dev = _FakeHidDevice([first, second])
    reader._start_poller_locked()
    try:
        deadline = time.monotonic() + 2.0
        while reader.read() != (2, 2, 2, 2, 2, 2) and time.monotonic() < deadline:
            time.sleep(0.01)
        for _ in range(3):
            assert reader.read_next() == (2, 2, 2, 2, 2, 2)
    finally:
        reader.close()
    assert reader.read_next() is None  # 閉じたら旧個体の値を残さない


def test_same_030_content_does_not_replace_latest():
    """内容まで同じ 0x30 再送は最新値を差し替えず、時刻だけ進める。"""
    import threading
    import time

    import core.nintendo_hid as nh

    now = time.monotonic()
    first = _report_30(1, 0, 0, 0, 0, 0, timer=7)
    duplicate = _report_30(1, 0, 0, 0, 0, 0, timer=7)
    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    reader._lock = threading.Lock()
    reader._latest = first
    reader._latest_t = now
    with reader._lock:
        reader._accept_030_locked(duplicate, now + 0.001)
    assert reader._latest is first
    assert reader._latest_t == now + 0.001  # 生存時刻だけ進む


def test_fixed_timer_still_updates_changed_samples():
    """timer byte が固定でも、IMUサンプルが変われば最新値を更新する。"""
    import threading
    import time

    import core.nintendo_hid as nh

    now = time.monotonic()
    before = _report_30(1, 2, 3, 4, 5, 6, timer=0)
    after = _report_30(7, 8, 9, 10, 11, 12, timer=0)
    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    reader._lock = threading.Lock()
    reader._latest = before
    reader._latest_t = now
    with reader._lock:
        reader._accept_030_locked(after, now + 0.001)
    assert reader._latest is after
    assert reader.read() == (7, 8, 9, 10, 11, 12)


def test_newer_030_timer_updates_changed_samples():
    """timer が進んだレポートも当然ながら最新値を更新する。"""
    import threading
    import time

    import core.nintendo_hid as nh

    now = time.monotonic()
    before = _report_30(1, 2, 3, 4, 5, 6, timer=7)
    after = _report_30(7, 8, 9, 10, 11, 12, timer=8)
    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    reader._lock = threading.Lock()
    reader._latest = before
    reader._latest_t = now
    with reader._lock:
        reader._accept_030_locked(after, now + 0.001)
    assert reader._latest is after


def test_read_pad_packet_advances_only_on_content_change():
    """同じ0x30の再読みでは packet を進めない（表示更新を省かせる）。"""
    import threading
    import time

    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    reader._lock = threading.Lock()
    reader._latest = _report_30()
    reader._latest_t = time.monotonic()
    reader._pad_packet = 0
    reader._pad_key = None
    first = reader.read_pad()
    second = reader.read_pad()
    assert first.connected is True
    assert second.packet == first.packet

    changed = bytearray(_report_30())
    changed[3] = 0x08  # A
    reader._latest = bytes(changed)
    reader._latest_t = time.monotonic()
    third = reader.read_pad()
    assert third.packet == first.packet + 1
    assert "A" in third.pressed


def test_nintendo_hid_ignores_non_30_cache():
    """読み取りスレッドは0x30以外を_cacheへ入れない。念のためIDでも弾く。"""
    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader.__new__(nh.NintendoHidReader)
    import threading

    reader._lock = threading.Lock()
    reader._latest = bytes([0x21] + [0] * 63)
    reader._packet = 0
    assert reader.read() is None


def test_nintendo_hid_poller_caches_latest_30():
    """専用スレッドが0x30だけを保持し、read()はそれを返す。"""
    import time

    import core.nintendo_hid as nh

    other = bytes([0x21] + [0] * 63)
    good = _report_30(1, 2, 4096, 3, 4, 5)
    reader = nh.NintendoHidReader()
    reader._dev = _FakeHidDevice([other, good])
    reader._latest = None
    reader._start_poller_locked()
    deadline = time.monotonic() + 2.0
    while reader.read() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    reader.close()
    assert reader.read() is None  # closeでcacheも消える
    assert reader._replies and reader._replies[0] == other


def test_nintendo_hid_closed_returns_none():
    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader()
    assert reader.read() is None
    assert reader.read_next() is None
    assert reader.is_open() is False
    reader.close()


def test_nintendo_hid_shared_reader_is_singleton():
    """パッド読みとIMU送出は同一ハンドルを共有する（二重open防止）。"""
    import core.nintendo_hid as nh

    nh._shared_reader = None
    first = nh.get_shared_reader()
    second = nh.get_shared_reader()
    assert first is second
    nh._shared_reader = None


def test_nintendo_hid_stick_y_polarity() -> None:
    """Switch生値は上が大きい。XInputへは反転しない（上下逆の回帰防止）。"""
    import core.nintendo_hid as nh

    assert nh._stick12_to_short(0x800) == 0
    assert nh._stick12_to_short(0xFFF) > 0  # 上 = 正
    assert nh._stick12_to_short(0x000) < 0  # 下 = 負


class _ProbeHidDevice:
    """0x30を返し続ける偽デバイス。open/close回数をownerに記録する。"""

    def __init__(self, owner):
        self._owner = owner
        self.closed = False
        self.written = []

    def open(self, vid, pid):
        self._owner.opens += 1

    def open_path(self, path):
        self._owner.opens += 1

    def set_nonblocking(self, flag):
        return None

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        import time

        time.sleep(0.002)
        return list(_report_30(1, 2, 3, 4, 5, 6))

    def close(self):
        self.closed = True
        self._owner.closes += 1


class _FakeHidModule:
    """hidapi の代役。device() が毎回新しい偽デバイスを返す。"""

    def __init__(self):
        self.opens = 0
        self.closes = 0
        self.devices = []

    def device(self):
        dev = _ProbeHidDevice(self)
        self.devices.append(dev)
        return dev


def _install_fake_hid(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("hid")
    factory = _FakeHidModule()
    fake.device = factory.device  # type: ignore[attr-defined]
    fake.enumerate = lambda vid, pid: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hid", fake)
    return factory


def test_nintendo_hid_open_exposes_probe_to_first_read_pad(monkeypatch):
    """openの成功判定に使った0x30を捨てない（直後のread_padが接続扱い）。"""
    import core.nintendo_hid as nh

    factory = _install_fake_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        assert reader.is_open() is True
        state = reader.read_pad()
        assert state.connected is True
        assert reader.read() == (1, 2, 3, 4, 5, 6)
        assert reader._latest_t > 0.0
    finally:
        reader.close()
    assert factory.opens == 1
    assert factory.closes == 1


def test_nintendo_hid_concurrent_open_keeps_one_handle(monkeypatch):
    """並行openでも動いているハンドルを閉じ合わない。"""
    import threading

    import core.nintendo_hid as nh

    factory = _install_fake_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    barrier = threading.Barrier(4)
    results = []

    def _worker() -> None:
        barrier.wait()
        results.append(reader.open(retries=1))

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5.0)
        assert results == [True, True, True, True]
        assert factory.opens == 1
        assert factory.closes == 0
    finally:
        reader.close()


class _SlowOpenHidDevice:
    """open がゲートで止まる偽デバイス（open中の読みを観測する）。"""

    def __init__(self) -> None:
        import threading

        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def open(self, vid, pid):
        self.entered.set()
        self.release.wait(2.0)

    def open_path(self, path):
        self.entered.set()
        self.release.wait(2.0)

    def set_nonblocking(self, flag):
        return None

    def write(self, data):
        return len(data)

    def read(self, size, timeout_ms=0):
        return list(_report_30(1, 2, 3, 4, 5, 6))

    def close(self):
        self.closed = True


def test_open_does_not_block_readers(monkeypatch):
    """open中でも is_open / read_pad は錠を待たず false / 未接続を返す。"""
    import sys
    import threading
    import time
    import types

    import core.nintendo_hid as nh

    dev = _SlowOpenHidDevice()
    fake = types.ModuleType("hid")
    fake.device = lambda: dev  # type: ignore[attr-defined]
    fake.enumerate = lambda vid, pid: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hid", fake)
    reader = nh.NintendoHidReader()
    result = []
    thread = threading.Thread(target=lambda: result.append(reader.open(retries=1)))
    thread.start()
    try:
        assert dev.entered.wait(1.0)
        started = time.monotonic()
        assert reader.is_open() is False
        assert reader.is_opening() is True
        assert reader.read_pad().connected is False
        assert time.monotonic() - started < 0.3
        dev.release.set()
        thread.join(2.0)
        assert result == [True]
        assert reader.is_open() is True
    finally:
        dev.release.set()
        thread.join(2.0)
        reader.close()


def test_nintendo_hid_stale_030_is_not_returned():
    """0x30が途絶えたら古い値を返さない（connected=False）。"""
    import time

    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader()
    with reader._lock:
        reader._latest = _report_30(1, 2, 3, 4, 5, 6)
        reader._latest_t = time.monotonic() - (nh._LATEST_TTL_S + 1.0)
    assert reader.read() is None
    assert reader.read_pad().connected is False
    assert reader.read_next() is None


class _ChatterHidDevice:
    """最初だけ0x30を返し、以降は0x3fを返し続ける偽デバイス。"""

    def __init__(self):
        self.reads = 0
        self.closed = False

    def read(self, size, timeout_ms=0):
        import time

        time.sleep(0.005)
        self.reads += 1
        if self.reads == 1:
            return list(_report_30(1, 2, 3, 4, 5, 6))
        return [0x3F] + [0] * 63

    def close(self):
        self.closed = True


def test_nintendo_hid_poller_closes_when_030_stops():
    """0x3fが流れ続けても0x30が途絶えたらハンドルを閉じる。"""
    import time

    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader()
    dev = _ChatterHidDevice()
    reader._dev = dev
    reader._start_poller_locked()
    deadline = time.monotonic() + 3.0
    while reader.is_open() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert reader.is_open() is False
    assert dev.closed is True
    assert reader.read() is None


def test_pad_source_hid_first_read_after_open_is_connected(monkeypatch):
    """PadSource経由でもopen直後の初回readが未接続にならない。"""
    import core.nintendo_hid as nh
    from core.pad_source import PadSource
    from core.xinput import PadState

    class _NoPad:
        available = False

        def read(self, index):
            return PadState()

        def scan(self):
            return []

        def names(self):
            return {}

    factory = _install_fake_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    source = PadSource(xinput=_NoPad(), sdl=_NoPad(), hid=reader)
    try:
        assert source.read(0).connected is True
        assert source.route_of(0) == "hid"
        assert source.read(0).connected is True
        assert factory.opens == 1
        assert factory.closes == 0
    finally:
        reader.close()


def test_ensure_live_fresh_does_not_open(monkeypatch):
    """0x30が新鮮ならopenを呼ばない。"""
    import core.nintendo_hid as nh

    factory = _install_fake_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        assert reader.ensure_live(max_age_s=5.0) is True
        assert factory.opens == 1
        assert factory.closes == 0
    finally:
        reader.close()


def test_ensure_live_stale_calls_open_with_two_retries():
    """0x30が古ければ open(retries=2) で再武装する。"""
    import time

    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader()
    reader._dev = object()
    reader._latest = _report_30(1, 2, 3, 4, 5, 6)
    reader._latest_t = time.monotonic() - 10.0
    calls = []

    def _open(retries=5):
        calls.append(retries)
        return False

    reader.open = _open  # type: ignore[method-assign]
    assert reader.ensure_live() is False
    assert calls == [2]
    assert reader._latest is None  # 古い値は捨てる


def test_ensure_live_stale_rearms_real_handle(monkeypatch):
    """開いたまま0x30が止まったハンドルを開き直して復帰する。"""
    import time

    import core.nintendo_hid as nh

    factory = _install_fake_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        reader._stop_poller()  # 0x30の供給だけ止める（ハンドルは開いたまま）
        with reader._lock:
            reader._latest_t = time.monotonic() - 10.0
        assert reader.is_open() is True
        assert reader.ensure_live(max_age_s=0.25) is True
        assert factory.opens == 2
        assert reader.read_pad().connected is True
    finally:
        reader.close()
