"""SDLセンサー換算のhostベクタ（実機不要）。"""


def test_accel_1g_is_4096():
    from core.sdl_sensor import accel_to_raw

    # 1/0.000244 = 4098.36 -> 4098（純正実測≒4104と整合）
    assert accel_to_raw(9.80665) == 4098
    assert accel_to_raw(0.0) == 0
    assert accel_to_raw(-9.80665) == -4098


def test_gyro_1rad_is_938():
    from core.sdl_sensor import gyro_to_raw

    # 1rad/s = 57.2958dps / 0.06103 = 938.7 -> 939
    assert gyro_to_raw(1.0) == 939
    assert gyro_to_raw(0.0) == 0


def test_conversion_saturates_and_rejects_nonfinite():
    from core.sdl_sensor import accel_to_raw, gyro_to_raw

    assert accel_to_raw(1e9) == 32767
    assert accel_to_raw(-1e9) == -32768
    assert gyro_to_raw(1e9) == 32767
    assert accel_to_raw(float("nan")) == 0
    assert accel_to_raw(float("inf")) == 0
    assert gyro_to_raw(float("nan")) == 0
    assert gyro_to_raw("bad") == 0  # type: ignore[arg-type]


def test_default_map_is_sdl_inverse():
    from core.sdl_sensor import SdlSensorReader

    # SDLのSwitch→SDL変換（SDL_X=-Sw_Y・SDL_Y=+Sw_Z・SDL_Z=-Sw_X）の逆写像。
    # Sw_X=-SDL_Z・Sw_Y=-SDL_X・Sw_Z=+SDL_Y。
    reader = SdlSensorReader()
    reader.read_raw = lambda: ((1.0, 2.0, 3.0), (0.1, 0.2, 0.3))  # type: ignore[method-assign]
    got = reader.read()
    assert got is not None
    from core.sdl_sensor import accel_to_raw, gyro_to_raw

    assert got[0] == accel_to_raw(-3.0)
    assert got[1] == accel_to_raw(-1.0)
    assert got[2] == accel_to_raw(2.0)
    assert got[3] == gyro_to_raw(-0.3)
    assert got[4] == gyro_to_raw(-0.1)
    assert got[5] == gyro_to_raw(0.2)


def test_axis_map_and_bias_applied():
    from core.sdl_sensor import SdlSensorReader

    reader = SdlSensorReader()
    calls = {"n": 0}

    def fake_raw():
        calls["n"] += 1
        return ((1.0, 2.0, 3.0), (0.1, 0.2, 0.3))

    reader.read_raw = fake_raw  # type: ignore[method-assign]
    base = reader.read()
    assert base is not None
    # 軸入替: accel恒等、gyro恒等へ戻す
    reader.set_axis_map((0, 1, 2), (1, 1, 1), (0, 1, 2), (1, 1, 1))
    mapped = reader.read()
    assert mapped is not None
    from core.sdl_sensor import accel_to_raw, gyro_to_raw

    assert mapped[0] == accel_to_raw(1.0)
    assert mapped[3] == gyro_to_raw(0.1)
    # バイアス: gyro Xから0.1引く -> 0になる
    reader.set_gyro_bias((0.1, 0.0, 0.0))
    biased = reader.read()
    assert biased is not None
    assert biased[3] == 0
    assert calls["n"] == 3


def test_axis_map_rejects_invalid():
    from core.sdl_sensor import SdlSensorReader

    reader = SdlSensorReader()
    reader.read_raw = lambda: ((9.80665, 0.0, 0.0), (0.0, 0.0, 0.0))  # type: ignore[method-assign]
    before = reader.read()
    reader.set_axis_map((5, 0, 1), (1, 1, 1), (0, 1, 2), (1, 1, 1))
    reader.set_gyro_bias((float("nan"), 0.0, 0.0))
    after = reader.read()
    assert before == after
    # 既定逆写像で Sw_Y=-SDL_X -> -4098
    assert after is not None and after == (0, -4098, 0, 0, 0, 0)


def test_closed_reader_returns_none():
    from core.sdl_sensor import SdlSensorReader

    reader = SdlSensorReader()
    assert reader.read() is None
    assert reader.is_open() is False
    reader.close()


def _sensor_with_lib(lib):
    from core.sdl_sensor import SdlSensorReader

    reader = SdlSensorReader()
    reader._ready = True
    reader._lib = lib
    return reader


def test_sensor_open_failure_logs_once():
    """SDL_GameControllerOpen の失敗は再試行しても1回だけ出す。"""
    from loguru import logger

    class _Lib:
        def SDL_GameControllerOpen(self, index):
            return None

    reader = _sensor_with_lib(_Lib())
    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        assert reader.open(0, retries=1) is False
        assert reader.open(0, retries=1) is False
    finally:
        logger.remove(handler)
    assert sum("開けません" in m for m in messages) == 1


def test_sensor_enable_failure_logs_once():
    """SetSensorEnabled の失敗も理由ごとに1回だけ出す。"""
    from loguru import logger

    class _Lib:
        def SDL_GameControllerOpen(self, index):
            return 1

        def SDL_GameControllerHasSensor(self, handle, kind):
            return 1

        def SDL_GameControllerSetSensorEnabled(self, handle, kind, enabled):
            return -1

        def SDL_GameControllerClose(self, handle):
            return None

    reader = _sensor_with_lib(_Lib())
    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        assert reader.open(0, retries=1) is False
        assert reader.open(0, retries=1) is False
    finally:
        logger.remove(handler)
    assert sum("有効化できません" in m for m in messages) == 1


def test_sensor_sdl_ensure_failure_logs_once(monkeypatch):
    """SDL初期化の失敗も再試行のたびに出さない。"""
    import sys
    import types

    from loguru import logger

    fake = types.ModuleType("pygame")
    fake._sdl2 = types.ModuleType("pygame._sdl2")  # type: ignore[attr-defined]
    fake_sdl2 = fake._sdl2
    fake_gc = types.ModuleType("pygame._sdl2.controller")
    fake_gc.init = lambda: None  # type: ignore[attr-defined]
    fake_sdl2.controller = fake_gc  # type: ignore[attr-defined]
    fake.init = lambda: (_ for _ in ()).throw(RuntimeError("no sdl"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pygame", fake)
    monkeypatch.setitem(sys.modules, "pygame._sdl2", fake_sdl2)
    monkeypatch.setitem(sys.modules, "pygame._sdl2.controller", fake_gc)

    from core.sdl_sensor import SdlSensorReader

    reader = SdlSensorReader()
    messages: list[str] = []
    handler = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        assert reader.available is False
        assert reader.available is False
    finally:
        logger.remove(handler)
    assert sum("SDLを初期化できません" in m for m in messages) == 1
