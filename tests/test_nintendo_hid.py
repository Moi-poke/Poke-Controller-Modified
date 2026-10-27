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


class _BlockingReadHidDevice:
    """read が解除イベントまで戻らない偽デバイス（close の競合を観測する）。"""

    def __init__(self) -> None:
        import threading

        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = False
        self.closed_during_read = False
        self._reading = False

    def read(self, size, timeout_ms=0):
        self._reading = True
        self.entered.set()
        self.release.wait(5.0)
        self._reading = False
        return []

    def write(self, data):
        return len(data)

    def close(self):
        self.closed_during_read = self._reading
        self.closed = True


def test_close_does_not_close_device_while_read_blocked():
    """読み取り中の dev.close() をしない（ネイティブuse-after-freeの回避）。"""
    import threading

    import core.nintendo_hid as nh

    dev = _BlockingReadHidDevice()
    reader = nh.NintendoHidReader()
    reader._dev = dev
    reader._start_poller_locked()
    assert dev.entered.wait(2.0)

    done = threading.Event()

    def closer() -> None:
        reader.close()
        done.set()

    thread = threading.Thread(target=closer)
    thread.start()
    try:
        # close はブロック中の read を待つ（この時点では完了しない）。
        assert not done.wait(0.1)
        assert dev.closed_during_read is False
        assert dev.closed is False

        dev.release.set()
        assert done.wait(2.0)
        assert dev.closed is True
        assert dev.closed_during_read is False
        assert reader.is_open() is False
    finally:
        dev.release.set()
        thread.join(2.0)


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
        # open は読み取りを止めない専用スレッドで走る。完了を待ってから
        # 次の read で接続を確認する。
        assert source.read(0).connected is False
        thread = source._hid_open_thread
        if thread is not None:
            thread.join(5.0)
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


class _ArmingHidDevice:
    """0x30を流し、output report 0x01 のサブコマンドへ0x21で応答する偽デバイス。"""

    def __init__(self, owner, *, ack=True, imu_after_arm=True, start_armed=False):
        import time

        self._owner = owner
        self._time = time
        self.ack = ack
        self.imu_after_arm = imu_after_arm
        self.armed = start_armed
        self.subcmds = []
        self.written = []
        self.closed = False
        self._pending = []

    def open(self, vid, pid):
        self._owner.opens += 1

    def open_path(self, path):
        self._owner.opens += 1

    def set_nonblocking(self, flag):
        return None

    def write(self, data):
        data = bytes(data)
        self.written.append(data)
        if data and data[0] == 0x01 and len(data) >= 12:
            sub = data[10]
            arg = data[11]
            self.subcmds.append((sub, arg))
            if self.ack:
                reply = bytearray(64)
                reply[0] = 0x21
                reply[13] = 0x80 | sub  # ACK
                reply[14] = sub  # subcommand id
                self._pending.append(bytes(reply))
            if sub == 0x40 and arg == 0x01 and self.imu_after_arm:
                self.armed = True
        return len(data)

    def read(self, size, timeout_ms=0):
        self._time.sleep(0.001)
        if self._pending:
            return list(self._pending.pop(0))
        if self.armed:
            return list(_report_30(1, 2, 4096, 3, 4, 5))
        return list(_report_30(0, 0, 0, 0, 0, 0))

    def close(self):
        self.closed = True
        self._owner.closes += 1


class _ArmingHidModule:
    def __init__(self, device_cls=_ArmingHidDevice, **kwargs):
        self.opens = 0
        self.closes = 0
        self.devices = []
        self._device_cls = device_cls
        self._kwargs = kwargs

    def device(self):
        dev = self._device_cls(self, **self._kwargs)
        self.devices.append(dev)
        return dev


def _install_arming_hid(monkeypatch, device_cls=_ArmingHidDevice, **kwargs):
    """0x21応答を返す偽hidを入れる。待ち時間はテスト用に短縮する。"""
    import sys
    import types

    import core.nintendo_hid as nh

    fake = types.ModuleType("hid")
    factory = _ArmingHidModule(device_cls=device_cls, **kwargs)
    fake.device = factory.device  # type: ignore[attr-defined]
    fake.enumerate = lambda vid, pid: []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hid", fake)
    monkeypatch.setattr(nh, "_HANDSHAKE_REPLY_TIMEOUT_S", 0.02)
    monkeypatch.setattr(nh, "_ARM_REPLY_TIMEOUT_S", 0.1)
    return factory


def test_open_arms_imu_and_verifies_ack(monkeypatch):
    """openが 0x40 01 → 0x03 30 を送り、0x21応答でIMU有効を確定する。"""
    import core.nintendo_hid as nh

    factory = _install_arming_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        assert reader.is_armed() is True
        assert factory.devices[0].subcmds == [(0x40, 0x01), (0x03, 0x30)]
    finally:
        reader.close()
    assert reader.is_armed() is False


def test_open_without_ack_and_zero_imu_is_not_armed(monkeypatch):
    """0x30が流れてもIMU未有効なら「準備完了」にしない（従来の誤認の回帰）。"""
    import core.nintendo_hid as nh

    factory = _install_arming_hid(monkeypatch, ack=False, imu_after_arm=False)
    reader = nh.NintendoHidReader()
    try:
        # パッド読みのためハンドル自体は開ける（ボタン入力を壊さない）。
        assert reader.open(retries=1) is True
        assert reader.is_open() is True
        assert reader.is_armed() is False
        # 鮮度だけでTrueを返さず、再武装を試して失敗を報告する。
        assert reader.ensure_live() is False
        # 再武装は同じハンドル（開き直さない）。
        assert factory.opens == 1
        assert len(factory.devices[0].subcmds) == 4  # open2 + 再武装2
    finally:
        reader.close()


def test_open_treats_nonzero_imu_without_ack_as_armed(monkeypatch):
    """応答が取れない個体（Steam初期化済み等）でもIMU値が非0なら有効。"""
    import core.nintendo_hid as nh

    factory = _install_arming_hid(monkeypatch, ack=False, start_armed=True)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        assert reader.is_armed() is True
        assert reader.ensure_live() is True
        assert factory.opens == 1
    finally:
        reader.close()


def test_ensure_live_rearms_fresh_unarmed_handle_without_reopen(monkeypatch):
    """0x30が新鮮でも未武装なら、同じハンドルへ送り直して復帰する。"""
    import core.nintendo_hid as nh

    factory = _install_arming_hid(monkeypatch, ack=False, imu_after_arm=False)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        assert reader.is_armed() is False
        dev = factory.devices[0]
        dev.ack = True
        dev.imu_after_arm = True
        assert reader.ensure_live() is True
        assert reader.is_armed() is True
        assert factory.opens == 1
        assert dev.subcmds[-2:] == [(0x40, 0x01), (0x03, 0x30)]
    finally:
        reader.close()


def test_arm_imu_resends_even_when_030_is_fresh(monkeypatch):
    """arm_imuは0x30の鮮度に関係なく必ず 0x40 → 0x03 を送る。"""
    import core.nintendo_hid as nh

    factory = _install_arming_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        dev = factory.devices[0]
        dev.subcmds.clear()
        assert reader.arm_imu() is True
        assert dev.subcmds == [(0x40, 0x01), (0x03, 0x30)]
        assert reader.is_armed() is True
    finally:
        reader.close()


def test_open_rearms_fresh_handle_that_is_unarmed(monkeypatch):
    """openの再利用経路でも、未武装のハンドルは送り直す。"""
    import core.nintendo_hid as nh

    factory = _install_arming_hid(monkeypatch)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        dev = factory.devices[0]
        reader._armed = False
        dev.subcmds.clear()
        assert reader.open(retries=1) is True  # 0x30が新鮮なので開き直さない
        assert factory.opens == 1
        assert dev.subcmds == [(0x40, 0x01), (0x03, 0x30)]
        assert reader.is_armed() is True
    finally:
        reader.close()


def test_arm_reply_accepts_ack_byte_conventions() -> None:
    """0x21応答の判定: byte13=ACK(0x80|sub or 0x80)、byte14=sub。"""
    import core.nintendo_hid as nh

    reply = bytearray(64)
    reply[0] = 0x21
    reply[13] = 0xC0
    reply[14] = 0x40
    assert nh.NintendoHidReader._is_reply(bytes(reply), 0x40) is True
    assert nh.NintendoHidReader._is_reply(bytes(reply), 0x03) is False
    reply[13] = 0x80
    assert nh.NintendoHidReader._is_reply(bytes(reply), 0x40) is True
    reply[13] = 0x90
    assert nh.NintendoHidReader._is_reply(bytes(reply), 0x40) is False
    reply[13] = 0x83
    reply[14] = 0x03
    assert nh.NintendoHidReader._is_reply(bytes(reply), 0x03) is True


def test_has_imu_payload_distinguishes_zero_slots() -> None:
    """IMU未有効の0x30は全スロット0。accel z の重力で非0になる。"""
    import core.nintendo_hid as nh

    assert nh.NintendoHidReader._has_imu_payload(_report_30(0, 0, 0, 0, 0, 0)) is False
    assert (
        nh.NintendoHidReader._has_imu_payload(_report_30(0, 0, 4096, 0, 0, 0)) is True
    )
    assert nh.NintendoHidReader._has_imu_payload(b"\x30" + bytes(10)) is False


class _ScriptedHidDevice:
    def __init__(self, reports) -> None:
        self.reports = list(reports)
        self.writes: list[bytes] = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        if not self.reports:
            return []
        return list(self.reports.pop(0))


def test_handshake_skips_mixed_030_and_still_sends_8004():
    """80応答待ちに0x30が混ざっても読み飛ばし、80 04まで送る。"""
    import core.nintendo_hid as nh

    def _81(sub: int) -> bytes:
        return bytes([0x81, sub]) + bytes(62)

    dev = _ScriptedHidDevice(
        [
            _report_30(0, 0, 0, 0, 0, 0),  # 80 01の応答より先に0x30
            _81(0x01),
            _81(0x02),
            _81(0x04),
        ]
    )
    reader = nh.NintendoHidReader()
    assert reader._handshake_dev(dev) is True
    sent = [w[:2] for w in dev.writes]
    assert sent == [b"\x80\x01", b"\x80\x02", b"\x80\x04"]


def test_ensure_live_falls_back_to_nonzero_imu_without_ack(monkeypatch):
    """応答なしの個体でも、0x30のIMU値が非0なら再武装成功とみなす。"""
    import core.nintendo_hid as nh

    factory = _install_arming_hid(monkeypatch, ack=False, start_armed=True)
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        reader._armed = False  # 外部要因で状態を失った扱い
        assert reader.ensure_live() is True
        assert reader.is_armed() is True
        assert factory.opens == 1
    finally:
        reader.close()


class _Delayed030HidDevice(_ArmingHidDevice):
    """最初の数回は空読みを返し、0x30の到着が遅れる個体。"""

    def __init__(self, owner, empty_reads=0, **kwargs):
        super().__init__(owner, **kwargs)
        self.empty_reads = int(empty_reads)

    def read(self, size, timeout_ms=0):
        if self.empty_reads > 0 and not self._pending:
            self.empty_reads -= 1
            self._time.sleep(0.005)
            return []
        return super().read(size, timeout_ms)


def test_open_waits_for_delayed_first_030(monkeypatch):
    """ハンドル直後0x30が来ない個体でも、1回の空読みで諦めない。"""
    import core.nintendo_hid as nh

    monkeypatch.setattr(nh, "_FIRST_030_TIMEOUT_S", 0.3)
    factory = _install_arming_hid(
        monkeypatch, device_cls=_Delayed030HidDevice, empty_reads=30
    )
    reader = nh.NintendoHidReader()
    try:
        assert reader.open(retries=1) is True
        assert reader.is_armed() is True
        assert reader.read() == (1, 2, 4096, 3, 4, 5)
        assert factory.devices[0].empty_reads == 0
    finally:
        reader.close()


def _report_with_stick(lx: int, ly: int, rx: int = 0x800, ry: int = 0x800) -> bytes:
    """0x30レポートのスティック12bit欄を差し替える。"""
    report = bytearray(_report_30())
    report[6] = lx & 0xFF
    report[7] = ((lx >> 8) & 0x0F) | ((ly & 0x0F) << 4)
    report[8] = (ly >> 4) & 0xFF
    report[9] = rx & 0xFF
    report[10] = ((rx >> 8) & 0x0F) | ((ry & 0x0F) << 4)
    report[11] = (ry >> 4) & 0xFF
    return bytes(report)


def test_read_pad_learns_stick_range() -> None:
    """HID直結の12bitスティックを実測レンジでフルスケールへ合わせる。

    ProConの生値は0..4095の全域を使わない（実測 456..3545）。固定の
    0x800幅で割ると全倒しが約78%にしかならないため、片側幅を学習する。
    """
    import time

    import core.nintendo_hid as nh

    reader = nh.NintendoHidReader()
    try:
        reader._latest = _report_with_stick(0x800, 0x800)
        reader._latest_t = time.monotonic()
        assert reader.read_pad().thumb_lx == 0

        # 学習前の微小ノイズをフルスケールへ増幅しない。
        reader._latest = _report_with_stick(0x801, 0x800)
        reader._latest_t = time.monotonic()
        small = reader.read_pad().thumb_lx
        assert 0 < small < 1000

        reader._latest = _report_with_stick(0xDD9, 0x800)
        reader._latest_t = time.monotonic()
        assert reader.read_pad().thumb_lx == 32767

        reader._latest = _report_with_stick(0x1C8, 0x800)
        reader._latest_t = time.monotonic()
        assert reader.read_pad().thumb_lx == -32767

        reader._latest = _report_with_stick(0x800, 0x800, 0xE00, 0x200)
        reader._latest_t = time.monotonic()
        state = reader.read_pad()
        assert state.thumb_rx == 32767
        assert state.thumb_ry == -32767
    finally:
        reader.close()
