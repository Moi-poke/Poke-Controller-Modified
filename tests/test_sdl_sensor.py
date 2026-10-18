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
